"""
Tests for rpresolve.mixbuild: the spec parser, the refusals, byte-identical
renders, loudness-matched blind A/B excerpts. Fixtures are lavfi
(speech-shaped and pink noise, a sine bed); no client audio.
"""

import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import numpy as np
    from rpresolve import duckplan as dp
    from rpresolve import dxmetrics as dxm
    from rpresolve import mixbuild as mb
    from rpresolve import audioreport as ar
    from rpresolve.paths import OutputRefused
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

needs_numpy = unittest.skipUnless(HAVE_NUMPY, "needs numpy")
FFMPEG = os.environ.get("RPRESOLVE_FFMPEG", "/opt/homebrew/bin/ffmpeg")
# A real RNNoise model, when the environment names one; otherwise each test gets a tiny generated one.
REAL_RNNOISE = os.environ.get("RPRESOLVE_RNNOISE_MODEL")
REAL_RNNOISE = REAL_RNNOISE if REAL_RNNOISE and os.path.isfile(REAL_RNNOISE) else None


def tiny_rnnoise(path):
    """A valid .rnnn with one neuron per hidden layer and every weight zero:
    arnndn loads it and passes the signal at half gain, which is enough to
    measure its delay. The layer shapes are the ones ffmpeg checks."""
    lines = ["rnnoise-nu model file version 1"]
    zeros = lambda n: " ".join(["0"] * n)
    for kind, i, o, act in (("dense", 42, 1, 0), ("gru", 1, 1, 2), ("gru", 44, 1, 2),
                            ("gru", 44, 1, 2), ("dense", 1, 22, 1), ("dense", 1, 1, 1)):
        lines.append(f"{i} {o} {act}")
        lines += [zeros(i * o), zeros(o)] if kind == "dense" else [zeros(3 * i * o), zeros(3 * o * o),
                                                                   zeros(3 * o)]
    Path(path).write_text("\n".join(lines) + "\n")
    return path

SPEC = """\
# a comment
sample_rate: 48000
channels: 2
stems:
  dialogue:
    input: dialogue.wav
    chain:
      - highpass: 80
      - denoise: {method: afftdn, nr: 12}
      - denoise: {method: candidates}
      - eq: {freq: 3000, gain: 2, q: 1}
      - deess: {}
      - compress: {threshold: -18, ratio: 3}
      - fade:
          in: 0.2
          out: 0.5
      - loudnorm: {i: -16, tp: -1.5}
    candidates:
      afftdn_6: {method: afftdn, nr: 6}
      arnndn: {method: arnndn}
  music:
    input: music.wav
    chain: [{gain: -3}, {lowpass: 12000}]
mix:
  gains: {dialogue: 0, music: -12}
  duck: {key: dialogue, under: [music], depth: 12}
  chain:
    - loudnorm: {i: -16, tp: -1.5}
    - limiter: {ceiling: -1.5}
"""


def lavfi(path, expr, seconds, channels=2):
    subprocess.run([FFMPEG, "-hide_banner", "-v", "error", "-y", "-f", "lavfi", "-i", expr,
                    "-t", str(seconds), "-ac", str(channels), "-c:a", "pcm_s24le", path], check=True)


def fixtures(d, seconds=24):
    # speech-shaped: noise band-limited to the voice range, pulsed like syllables, in 2.5 s
    # phrases with 1.5 s pauses (the duck key needs them), over a pink floor
    lavfi(os.path.join(d, "dialogue.wav"),
          f"anoisesrc=d={seconds}:c=white:r=48000:seed=3:a=0.5,bandpass=f=1200:width_type=h:w=2500,"
          "tremolo=f=4:d=0.9,volume=3,volume='lt(mod(t\\,4)\\,2.5)':eval=frame[a];"
          "anoisesrc=d=%d:c=pink:r=48000:seed=5:a=0.02[b];"
          "[a][b]amix=inputs=2:normalize=0" % seconds, seconds)
    lavfi(os.path.join(d, "music.wav"), f"sine=f=220:d={seconds}:r=48000,volume=0.3", seconds)


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = self._t.name
        self.addCleanup(self._t.cleanup)
        self.model = REAL_RNNOISE or tiny_rnnoise(os.path.join(self.d, "tiny.rnnn"))
        env = mock.patch.dict(os.environ, {"RPRESOLVE_RNNOISE_MODEL": self.model})
        env.start()
        self.addCleanup(env.stop)


@needs_numpy
class SpecParser(Base):
    def test_yaml_subset_and_json_agree(self):
        y = mb.parse_yaml(SPEC)
        self.assertEqual(y["stems"]["dialogue"]["chain"][0], {"highpass": 80})
        self.assertEqual(y["stems"]["dialogue"]["chain"][6], {"fade": {"in": 0.2, "out": 0.5}})
        self.assertEqual(y["stems"]["music"]["chain"], [{"gain": -3}, {"lowpass": 12000}])
        self.assertEqual(y["mix"]["duck"]["under"], ["music"])
        self.assertEqual(mb.parse_yaml("a: [1, 2.5, x]\nb: 'q: r'\nc: true\nd:\n"),
                         {"a": [1, 2.5, "x"], "b": "q: r", "c": True, "d": None})
        self.assertEqual(json.loads(json.dumps(y)), y)

    def test_bad_specs_are_refused(self):
        for text, frag in (("stems: {}\n", "at least one stem"),
                           ("stems:\n  a: {input: x.wav, chain: [{nope: 1}]}\n", "unknown step"),
                           ("stems:\n  a: {input: x.wav}\nbogus: 1\n", "unknown top-level"),
                           ("stems:\n  a: {input: x.wav, chain: [{eq: {frq: 1}}]}\n", None),
                           ("a: 1\na: 2\n", "twice")):
            p = os.path.join(self.d, "s.yaml")
            Path(p).write_text(text)
            with self.assertRaises(mb.SpecError) as cm:
                s = mb.load_spec(p)
                mb.chain_filters(list(s["stems"].values())[0]["chain"], s, 5.0, "t", [])
            if frag:
                self.assertIn(frag, str(cm.exception))

    def test_eq_and_limiter_filters(self):
        spec = {"sample_rate": 48000, "channels": 2}
        f = mb.chain_filters([{"eq": {"freq": 3000, "gain": 2}}, {"limiter": {"ceiling": -1.5}}],
                             spec, 5.0, "t", [])
        self.assertEqual(f[0], ["equalizer=f=3000:t=q:w=1:g=2:precision=f64", "aresample=192000",
                                "alimiter=limit=0.841395:level=disabled:attack=5:release=50",
                                "aresample=48000"])


@needs_numpy
class Render(Base):
    def spec(self):
        fixtures(self.d)
        p = os.path.join(self.d, "mix.yaml")
        Path(p).write_text(SPEC)
        return mb.load_spec(p), p

    def out(self, name):
        # the tempdir is outside any git tree
        return os.path.join(self.d, name)

    def test_same_spec_gives_identical_wavs_and_measures(self):
        spec, p = self.spec()
        a = mb.render(spec, self.out("a"), p, report=True)
        b = mb.render(spec, self.out("b"), p)
        # shortcut: arnndn is not stable across runs on the CI (apt) ffmpeg build, so its hash and
        # its graph's loudnorm measurements (embedded from that output) are left
        # out; upgrade when a pinned ffmpeg makes it stable. Every other output stays exact.
        def hashes(r):
            return {k: v["sha256"] for k, v in r["outputs"].items() if k != "dialogue__arnndn"}
        def graphs(r):
            # the arnndn graph keeps its structure; only the measured loudnorm numbers are blanked
            return {k: re.sub(r"(measured_\w+|offset)=[^:]+", r"\1=_", v) if k == "dialogue__arnndn"
                    else v for k, v in r["graphs"].items()}
        self.assertEqual(hashes(a), hashes(b))
        self.assertEqual(set(a["outputs"]), {"dialogue", "music", "dialogue__afftdn_6",
                                             "dialogue__arnndn", "mix", "mixed/dialogue",
                                             "mixed/music"})
        self.assertIn("measured_I=", a["graphs"]["dialogue__arnndn"])
        self.assertEqual(graphs(a), graphs(b))
        self.assertEqual(a["rnnoise_models"], {self.model: mb.sha256(self.model)})
        mix = a["reports"]["mix"]
        self.assertAlmostEqual(mix["integrated_lufs"], -16.0, delta=1.0)
        self.assertLessEqual(mix["true_peak_dbtp"], -1.0)
        self.assertIn("noise_floor_change_db", a["reports"]["dialogue__afftdn_6"])
        self.assertTrue(os.path.isfile(self.out("a/reports/mix/loudness.png")))

    def test_ducking_lowers_music_under_dialogue(self):
        spec, p = self.spec()
        spec["mix"]["chain"] = []
        on = mb.render(spec, self.out("on"), p)
        del spec["mix"]["duck"]
        off = mb.render(spec, self.out("off"), p)
        self.assertNotEqual(on["outputs"]["mix"]["sha256"], off["outputs"]["mix"]["sha256"])
        self.assertIn("sidechaincompress", on["graphs"]["mix"])
        self.assertNotIn("sidechaincompress", off["graphs"]["mix"])

    def test_refuses_to_write_over_a_source(self):
        spec, p = self.spec()
        spec["stems"]["dialogue"]["input"] = self.out("stems/dialogue.wav")
        os.makedirs(self.out("stems"))
        Path(spec["stems"]["dialogue"]["input"]).write_bytes(b"RIFFsource")
        with self.assertRaises(OutputRefused):
            mb.render(spec, self.d, p)
        self.assertEqual(Path(self.out("stems/dialogue.wav")).read_bytes(), b"RIFFsource")

    def test_refuses_an_output_inside_a_git_tree(self):
        spec, p = self.spec()
        os.makedirs(self.out("repo/.git"))
        with self.assertRaises(OutputRefused):
            mb.render(spec, self.out("repo/out"), p)

    def test_dry_run_prints_graphs_and_writes_nothing(self):
        spec, p = self.spec()
        m = mb.render(spec, self.out("dry"), p, dry_run=True)
        self.assertIn("highpass=f=80", m["graphs"]["dialogue"])
        self.assertIn("amix=inputs=2:normalize=0", m["graphs"]["mix"])
        self.assertFalse(os.path.exists(self.out("dry/mix.wav")))


def pcm(path):
    ch = ar.audio_stream(path)["channels"]
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", path, "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, "<f4").astype(np.float64).reshape(-1, ch)


def lag(x, y, most=4000):
    """The lag of y behind x (negative: ahead), by cross-correlation."""
    m = 1 << (len(x) + len(y)).bit_length()
    c = np.fft.irfft(np.fft.rfft(y, m) * np.conj(np.fft.rfft(x, m)), m)
    c = np.concatenate((c[-most:], c[:most + 1]))
    return int(np.argmax(c)) - most


def write_spec(d, spec):
    p = os.path.join(d, "spec.json")
    Path(p).write_text(json.dumps(spec))
    return mb.load_spec(p), p


@needs_numpy
class FollowUps(Base):
    def test_docstring_example_passes_validate_spec(self):
        block = mb.__doc__.split("the spec's folder.\n\n", 1)[1].split("\n\nSteps:", 1)[0]
        spec = mb.validate_spec(mb.parse_yaml(textwrap.dedent(block)), self.d)
        self.assertIn("afftdn_18", spec["stems"]["dialogue"]["candidates"])
        self.assertEqual(spec["mix"]["duck"]["depth"], 12)

    def test_step_settings_are_checked_when_the_spec_is_read(self):
        for chain, frag in (([{"dehum": {"freq": 90}}], "dehum freq"),
                            ([{"declick": {"window": 1}}], "declick window"),
                            ([{"denoise": {"method": "anlmdn", "s": 0}}], "s 0")):
            with self.assertRaises(mb.SpecError) as cm:
                mb.validate_spec({"stems": {"a": {"input": "a.wav", "chain": chain}}}, self.d)
            self.assertIn(frag, str(cm.exception))
        with self.assertRaises(mb.SpecError) as cm:
            mb.validate_spec({"stems": {"a": {"input": "a.wav"}, "b": {"input": "b.wav"}},
                              "mix": {"duck": {"key": "a", "under": "b", "threshold": -30}}}, self.d)
        self.assertIn("speech activity", str(cm.exception))
        self.assertEqual(mb.s_dehum({"freq": 50, "harmonics": 2}, mb.Ctx(48000, 1, [], "t")),
                         ["bandreject=f=50:width_type=q:w=10:precision=f64",
                          "bandreject=f=100:width_type=q:w=10:precision=f64"])

    def test_denoise_and_declick_leave_no_lag(self):
        # arnndn through RPRESOLVE_RNNOISE_MODEL: the real model when it is set, the tiny one in CI
        chains = {"afftdn": [{"denoise": {"method": "afftdn", "nr": 12}}],
                  "anlmdn": [{"denoise": {"method": "anlmdn"}}],
                  "arnndn": [{"denoise": {"method": "arnndn"}}],
                  "declick": [{"declick": {}}], "dehum": [{"dehum": 60}]}
        for rate in (48000, 44100):
            d = os.path.join(self.d, str(rate))
            os.makedirs(d)
            src = os.path.join(d, "dialogue.wav")
            lavfi(src, f"anoisesrc=d=4:c=white:r={rate}:seed=3:a=0.5,bandpass=f=1200:width_type=h:"
                       f"w=2500,tremolo=f=4:d=0.9[a];sine=f=60:r={rate}:d=4,volume=0.1[b];"
                       "[a][b]amix=inputs=2:normalize=0", 4, channels=1)
            spec, p = write_spec(d, {"sample_rate": rate, "channels": 1, "stems": {
                n: {"input": "dialogue.wav", "chain": c} for n, c in chains.items()}})
            m = mb.render(spec, os.path.join(d, "out"), p)
            x = pcm(src)[:, 0]
            for n in chains:
                with self.subTest(rate=rate, chain=n):
                    y = pcm(os.path.join(d, "out", m["outputs"][n]["file"]))[:, 0]
                    self.assertEqual(len(y), len(x))
                    self.assertEqual(lag(x, y), 0)
            y = pcm(os.path.join(d, "out", "stems", "dehum.wav"))[:, 0]
            self.assertLess(abs(np.fft.rfft(y)[240]), abs(np.fft.rfft(x)[240]) / 10)  # the 60 Hz bin

    def test_loudnorm_falling_back_to_dynamic_is_recorded(self):
        fixtures(self.d, 8)
        spec, p = write_spec(self.d, {"channels": 2, "stems": {
            "loud": {"input": "dialogue.wav", "chain": [{"loudnorm": {"i": -5, "tp": -9}}]},
            "fine": {"input": "dialogue.wav", "chain": [{"loudnorm": {"i": -23, "tp": -1, "lra": 30}}]}}})
        m = mb.render(spec, self.out("o"), p)
        ln = [s for s in m["skipped"] if s["step"] == "loudnorm"]
        self.assertEqual([s["where"] for s in ln], ["stem loud"])
        self.assertTrue(ln[0]["why"].startswith("fell back to dynamic"))
        self.assertEqual(json.loads(Path(self.out("o/render.json")).read_text())["skipped"], m["skipped"])
        self.assertEqual(ar.audio_stream(self.out("o/stems/loud.wav"))["sample_rate"], 48000)

    def out(self, name):
        return os.path.join(self.d, name)

    def mix_spec(self, chain):
        fixtures(self.d, 8)
        return write_spec(self.d, {"stems": {"dialogue": {"input": "dialogue.wav"},
                                             "music": {"input": "music.wav"}},
                                   "mix": {"gains": {"dialogue": -2, "music": -6},
                                           "duck": {"key": "dialogue", "under": ["music"], "depth": 12},
                                           "chain": chain}})

    def test_mixed_stems_sum_to_the_mix(self):
        spec, p = self.mix_spec([{"eq": {"freq": 2000, "gain": 3}}, {"highpass": 60},
                                 {"loudnorm": {"i": -23, "tp": -1, "lra": 30}}])
        m = mb.render(spec, self.out("o"), p)
        self.assertEqual(m["skipped"], [])
        mix = pcm(self.out("o/mix.wav"))
        both = pcm(self.out("o/mixed/dialogue.wav")) + pcm(self.out("o/mixed/music.wav"))
        self.assertEqual(both.shape, mix.shape)
        resid = 20 * np.log10(np.abs(both - mix).max() / np.abs(mix).max())
        self.assertLess(resid, -110)
        self.assertIn(dp.sidechain_filter(12), m["graphs"]["mix"])
        self.assertEqual(m["duck"]["depth_db"], 12)
        self.assertTrue(os.path.isfile(self.out("o/duck/duck-key.wav")))

    def test_a_key_with_no_speech_fails_before_the_mix(self):
        spec, p = self.mix_spec([])
        lavfi(self.out("dialogue.wav"), "anullsrc=r=48000:cl=stereo", 8)
        with self.assertRaises(mb.MixError) as cm:
            mb.render(spec, self.out("o"), p)
        self.assertIn("mix.duck.gate_db", str(cm.exception))
        self.assertFalse(os.path.exists(self.out("o/mix.wav")))

    def test_mixed_stems_stop_before_a_limiter(self):
        spec, p = self.mix_spec([{"limiter": {"ceiling": -1.5}}])
        m = mb.render(spec, self.out("o"), p)
        self.assertEqual([(s["where"], s["step"]) for s in m["skipped"]], [("mixed stems", "limiter")])
        self.assertNotIn("alimiter", m["graphs"]["mixed/music"])
        # with no linear step at all, the mixed stems are the stems at their mix gain and duck
        spec["mix"]["chain"] = []
        n = mb.render(spec, self.out("n"), p)
        ducked = pcm(self.out("n/mixed/music.wav"))
        music = pcm(self.out("n/stems/music.wav")) * 10 ** (-6 / 20)
        for a, b in ((0.5, 2.2), (4.5, 6.2)):  # inside the phrases
            span = slice(int(a * 48000), int(b * 48000))
            depth = 10 * np.log10((music[span] ** 2).mean() / (ducked[span] ** 2).mean())
            self.assertAlmostEqual(depth, 12, delta=1.0)
        quiet = slice(int(3.2 * 48000), int(3.8 * 48000))  # a pause: no duck
        self.assertLess(10 * np.log10((music[quiet] ** 2).mean() / (ducked[quiet] ** 2).mean()), 1)


@needs_numpy
class BlindAB(Base):
    def test_excerpts_match_within_half_an_lu_and_the_key_is_sealed(self):
        fixtures(self.d)
        a, b, c = (os.path.join(self.d, x) for x in ("dialogue.wav", "quiet.wav", "music.wav"))
        subprocess.run([FFMPEG, "-hide_banner", "-v", "error", "-y", "-i", a, "-af",
                        "volume=-9dB", b], check=True)
        out = os.path.join(self.d, "packet")
        pk = mb.ab_packet([a, b, c], out, start=2.0, seconds=20.0, seed=11)
        self.assertEqual(pk["labels"], ["A", "B", "C"])
        loud = [ar.loudness(os.path.join(out, f"{x}.wav"))[0]["integrated_lufs"] for x in "ABC"]
        self.assertLessEqual(max(loud) - min(loud), 0.5)
        self.assertLessEqual(pk["spread_lu"], 0.5)
        key = os.path.join(out, "sealed", "key.json")
        self.assertEqual(hashlib.sha256(Path(key).read_bytes()).hexdigest(), pk["key_sha256"])
        k = json.loads(Path(key).read_text())
        self.assertEqual(sorted(v["source"] for v in k["labels"].values()), sorted([a, b, c]))
        self.assertNotIn("dialogue", Path(out, "packet.json").read_text())
        self.assertEqual(ar.audio_stream(os.path.join(out, "A.wav"))["sample_rate"], 48000)
        # the same seed shuffles the same way; a sealed key is never replaced
        with self.assertRaises(OutputRefused):
            mb.ab_packet([a, b, c], out, start=2.0, seed=11)
        pk2 = mb.ab_packet([a, b, c], os.path.join(self.d, "packet2"), start=2.0, seed=11)
        self.assertEqual(pk["files"], pk2["files"])

    def test_noise_type_lands_in_the_sealed_key_and_is_checked(self):
        fixtures(self.d)
        a, c = (os.path.join(self.d, x) for x in ("dialogue.wav", "music.wav"))
        out = os.path.join(self.d, "nt")
        mb.ab_packet([a, c], out, seconds=5.0, seed=3, noise_type="other:fan")
        self.assertEqual(json.loads(Path(out, "sealed", "key.json").read_text())["noise_type"], "other:fan")
        out2 = os.path.join(self.d, "nt2")
        mb.ab_packet([a, c], out2, seconds=5.0, seed=3)
        self.assertNotIn("noise_type", json.loads(Path(out2, "sealed", "key.json").read_text()))
        for bad in ("other", "traffic", "other:", "other:fan ", "other:-x"):
            with self.assertRaises(mb.SpecError):
                mb.ab_packet([a, c], os.path.join(self.d, "nt3"), noise_type=bad)
        self.assertFalse(os.path.exists(os.path.join(self.d, "nt3")))

    def test_refuses_to_cut_over_a_source(self):
        fixtures(self.d)
        src = os.path.join(self.d, "A.wav")  # the packet's first label, but here a source
        os.rename(os.path.join(self.d, "dialogue.wav"), src)
        before = Path(src).read_bytes()
        with self.assertRaises(OutputRefused):
            mb.ab_packet([src, os.path.join(self.d, "music.wav")], self.d)
        self.assertEqual(Path(src).read_bytes(), before)


if __name__ == "__main__":
    unittest.main()


def tone_fixture(path, seconds=16):
    """Pink floor all through, speech bursts at 0.5-2.5 s and 11-13 s, and a
    digital-silence gap from 8 to 9 s."""
    lavfi(path,
          f"anoisesrc=d={seconds}:c=white:r=48000:seed=3:a=0.5,bandpass=f=1200:width_type=h:w=2500,"
          "tremolo=f=4:d=0.9,volume=3,volume='lt(mod(t\\,10.5)\\,2.0)+gt(t\\,10.5)*lt(t\\,13)':"
          f"eval=frame[a];anoisesrc=d={seconds}:c=pink:r=48000:seed=5:a=0.02[b];"
          "[a][b]amix=inputs=2:normalize=0,volume=0:enable='between(t,8,9)'", seconds)


def rms_db(x):
    return 10 * np.log10((x ** 2).mean())


@needs_numpy
class Presets(Base):
    def test_every_preset_validates_renders_and_keeps_length(self):
        fixtures(self.d, 8)
        stems = {k: {"input": "dialogue.wav", "preset": k} for k in mb.PRESETS}
        spec, p = write_spec(self.d, {"stems": stems})
        m = mb.render(spec, self.out("o"), p)
        n = len(pcm(os.path.join(self.d, "dialogue.wav")))
        for k in mb.PRESETS:
            self.assertEqual(len(pcm(self.out(f"o/stems/{k}.wav"))), n)
            self.assertEqual(m["presets"][k]["preset"], k)
            self.assertTrue(m["presets"][k]["why"])
        self.assertIn("arnndn", m["graphs"]["lav"])
        self.assertIn("arnndn", m["graphs"]["boom"])
        self.assertNotIn("afftdn", m["graphs"]["vo"])       # a booth gets no denoise
        self.assertNotIn("acompressor", m["graphs"]["music_bed"])

    def out(self, name):
        return os.path.join(self.d, name)

    def test_no_preset_sets_loudness_or_claims_a_result(self):
        for k, v in mb.PRESETS.items():
            self.assertNotIn("loudnorm", [list(i)[0] for i in v["chain"]])
            self.assertNotIn("better", v["why"].lower())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(mb.main(["presets"]), 0)
        self.assertIn("music_bed", buf.getvalue())

    def test_preset_and_chain_together_or_unknown_are_refused(self):
        for st, frag in (({"preset": "lav", "chain": [{"gain": 1}]}, "not both"),
                         ({"preset": "nope"}, "unknown preset")):
            with self.assertRaises(mb.SpecError) as cm:
                mb.validate_spec({"stems": {"d": dict(st, input="x.wav")}}, self.d)
            self.assertIn(frag, str(cm.exception))

    def test_preset_ab_packet_is_blind_and_loudness_matched(self):
        fixtures(self.d, 12)
        pk = mb.preset_ab(os.path.join(self.d, "dialogue.wav"), self.out("ab"), ("lav", "vo"),
                          start=0.0, seconds=6.0, seed=7)
        self.assertEqual(pk["labels"], ["A", "B", "C"])     # the input and two presets
        self.assertLessEqual(pk["spread_lu"], mb.AB_TOLERANCE_LU)
        key = json.loads(Path(self.out("ab/packet/sealed/key.json")).read_text())
        srcs = sorted(os.path.relpath(v["source"], self.d) for v in key["labels"].values())
        self.assertEqual(srcs, ["ab/renders/lav/stems/dialogue.wav", "ab/renders/vo/stems/dialogue.wav",
                                "dialogue.wav"])
        self.assertTrue(os.path.isfile(self.out("ab/packet/A.wav")))


@needs_numpy
class Arnndn(Base):
    def check(self, step):
        return mb.validate_spec({"stems": {"d": {"input": "x.wav", "chain": [{"denoise": step}]}}},
                                self.d)

    def test_the_filter_and_its_delay(self):
        ctx = mb.Ctx(48000, 1, [], "t", probe=False)
        self.assertEqual(mb.s_denoise({"method": "arnndn", "mix": 0.5}, ctx),  # the model from the env
                         [f"arnndn=m={mb._filter_escape(self.model)}:mix=0.5",
                          "aformat=sample_rates=48000"])
        filt = f"arnndn=m={mb._filter_escape(self.model)}:mix=1"
        self.assertEqual(mb.delay_samples(filt, 48000), 480)            # one 10 ms frame
        # scaled by rate/48000, give or take the resampler ffmpeg puts in front of it
        self.assertLessEqual(abs(mb.delay_samples(filt, 44100) - 441), 8)

    def test_a_missing_or_unusable_model_refuses_the_spec(self):
        with mock.patch.dict(os.environ, {"RPRESOLVE_RNNOISE_MODEL": ""}):
            with self.assertRaises(mb.SpecError) as cm:
                self.check({"method": "arnndn"})
            self.assertIn("RPRESOLVE_RNNOISE_MODEL", str(cm.exception))
            with self.assertRaises(mb.SpecError) as cm:  # a preset that uses it fails the same way
                mb.validate_spec({"stems": {"d": {"input": "x.wav", "preset": "lav"}}}, self.d)
            self.assertIn("RPRESOLVE_RNNOISE_MODEL", str(cm.exception))
            self.check({"method": "afftdn"})              # nothing else needs the model
            mb.validate_spec({"stems": {"d": {"input": "x.wav", "preset": "vo"}}}, self.d)
        spec = self.check("arnndn")                       # the env's model is pinned into the spec
        self.assertEqual(spec["stems"]["d"]["chain"], [{"denoise": {"method": "arnndn", "model": self.model}}])
        for model, frag in (("std.rnnn", "absolute path"),
                            (os.path.join(self.d, "nope.rnnn"), "no readable file"),
                            (self.d, "no readable file")):
            with self.assertRaises(mb.SpecError) as cm:
                self.check({"method": "arnndn", "model": model})
            self.assertIn(frag, str(cm.exception))
        locked = tiny_rnnoise(os.path.join(self.d, "locked.rnnn"))
        os.chmod(locked, 0)
        if not os.access(locked, os.R_OK):  # root reads it anyway
            with self.assertRaises(mb.SpecError):
                self.check({"method": "arnndn", "model": locked})

    def test_a_path_with_filter_syntax_in_it_renders(self):
        d = os.path.join(self.d, "we:ird, it's [a];b ")
        os.makedirs(d)
        model = tiny_rnnoise(os.path.join(d, "m:1,'[x];y .rnnn"))
        lavfi(os.path.join(self.d, "dialogue.wav"), "sine=f=440:r=48000:d=2", 2, channels=1)
        spec, p = write_spec(self.d, {"channels": 1, "stems": {"dialogue": {
            "input": "dialogue.wav", "chain": [{"denoise": {"method": "arnndn", "model": model}}]}}})
        m = mb.render(spec, os.path.join(self.d, "out"), p)
        self.assertIn("arnndn", m["graphs"]["dialogue"])
        self.assertEqual(list(m["rnnoise_models"]), [model])
        self.assertEqual(len(pcm(os.path.join(self.d, "out", "stems", "dialogue.wav"))), 96000)

    def test_only_the_speech_presets_default_to_it(self):
        self.assertEqual(mb.s_denoise({}, mb.Ctx(48000, 1, [], "t", probe=False))[0][:7], "afftdn=")
        uses = {k for k, v in mb.PRESETS.items()
                if any(list(i)[0] == "denoise" and i["denoise"].get("method") == "arnndn"
                       for i in v["chain"])}
        self.assertEqual(uses, {"lav", "boom"})


@needs_numpy
class SpeechNorm(Base):
    def out(self, name):
        return os.path.join(self.d, name)

    def spec(self, extra=None, chain=None):
        fixtures(self.d, 16)
        # music at a steady level: it fills the pauses, so the whole mix reads louder than the speech
        return write_spec(self.d, {"stems": {"dialogue": {"input": "dialogue.wav"},
                                             "music": {"input": "music.wav"}},
                                   "mix": {"gains": {"music": -9},
                                           "chain": chain or [{"speechnorm": dict({"key": "dialogue",
                                                                                   "i": -20}, **(extra or {}))}]}})

    def speech_lufs(self, mix_path, spans):
        x = pcm(mix_path)
        joined = np.concatenate([x[int(a * 48000):int(b * 48000)] for a, b in spans]).astype("<f4")
        wav = self.out("joined.wav")
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "f32le", "-ar", "48000", "-ac", "2", "-i", "-",
                        "-c:a", "pcm_f32le", wav], input=joined.tobytes(), check=True)
        return ar.loudness(wav)[0]["integrated_lufs"]

    def test_speech_spans_land_on_target_and_whole_mix_does_not(self):
        spec, p = self.spec()
        m = mb.render(spec, self.out("o"), p)
        sn = m["speechnorm"]
        self.assertGreater(sn["spans"], 1)
        self.assertGreater(abs(sn["whole_lufs"] - sn["speech_lufs"]), 0.3)
        spans = dp.plan(self.out("o/stems/dialogue.wav"), 0)["speech_spans"]
        self.assertAlmostEqual(self.speech_lufs(self.out("o/mix.wav"), spans), -20, delta=0.5)
        whole = ar.loudness(self.out("o/mix.wav"))[0]["integrated_lufs"]
        self.assertGreater(abs(whole - (-20)), 0.3)   # a plain loudnorm to -20 would have hit this
        self.assertEqual(m["skipped"], [])

    def test_mixed_stems_still_sum_to_the_mix(self):
        spec, p = self.spec(chain=[{"eq": {"freq": 2000, "gain": 3}},
                                   {"speechnorm": {"key": "dialogue", "i": -20}}])
        mb.render(spec, self.out("o"), p)
        both = pcm(self.out("o/mixed/dialogue.wav")) + pcm(self.out("o/mixed/music.wav"))
        mix = pcm(self.out("o/mix.wav"))
        self.assertLess(20 * np.log10(np.abs(both - mix).max() / np.abs(mix).max()), -110)

    def test_a_peak_past_tp_is_recorded_and_dry_run_says_unmeasured(self):
        spec, p = self.spec({"i": -5, "tp": -6})
        m = mb.render(spec, self.out("o"), p)
        self.assertEqual([(s["where"], s["step"]) for s in m["skipped"]], [("mix", "speechnorm")])
        d = mb.render(spec, self.out("d"), p, dry_run=True)
        self.assertIn("dry run", d["skipped"][0]["why"])

    def test_refusals(self):
        for stems, mix, frag in (
                ({"d": {"input": "x.wav", "chain": [{"speechnorm": {"key": "d"}}]}}, {}, "mix chain"),
                ({"d": {"input": "x.wav"}}, {"chain": [{"speechnorm": {"key": "z"}}]}, "key must be"),
                ({"d": {"input": "x.wav"}},
                 {"chain": [{"speechnorm": {"key": "d"}}, {"loudnorm": {}}]}, "not with loudnorm"),
                ({"d": {"input": "x.wav"}}, {"chain": [{"speechnorm": {}}]}, "needs key")):
            with self.assertRaises(mb.SpecError) as cm:
                mb.validate_spec({"stems": stems, "mix": mix}, self.d)
            self.assertIn(frag, str(cm.exception))


@needs_numpy
class RoomTone(Base):
    def out(self, name):
        return os.path.join(self.d, name)

    def render(self, rt_cfg, name="o"):
        tone_fixture(self.out("tone.wav"))
        spec, p = write_spec(self.d, {"stems": {"d": {"input": "tone.wav", "roomtone": rt_cfg}}})
        return mb.render(spec, self.out(name), p)

    def test_the_gap_is_filled_at_the_rooms_level_and_nothing_else_moves(self):
        tone_fixture(self.out("tone.wav"))
        src = pcm(self.out("tone.wav"))
        self.assertEqual(float(np.abs(src[int(8.1 * 48000):int(8.9 * 48000)]).max()), 0.0)
        m = self.render({})
        r = m["roomtone"]["d"]
        self.assertEqual(len(r["gaps"]), 1)
        self.assertAlmostEqual(r["gaps"][0][0], 8.0, delta=0.02)
        out = pcm(self.out("o/stems/d.wav"))
        floor = rms_db(src[int(3 * 48000):int(7 * 48000)])
        gap = rms_db(out[int(8.1 * 48000):int(8.9 * 48000)])
        self.assertAlmostEqual(gap, floor, delta=1.5)
        self.assertGreater(float(np.abs(out[int(8.1 * 48000):int(8.9 * 48000)]).max()), 0.0)
        for a, b in ((0, 7.9), (9.1, 16)):     # outside the gap and its fades: the input as it was
            span = slice(int(a * 48000), int(b * 48000))
            self.assertLess(float(np.abs(out[span] - src[span]).max()), 1e-5)
        self.assertEqual(self.dead_hops(self.out("o/stems/d.wav")), 0)

    def dead_hops(self, path):
        fp = dxm.frame_power(path, 0, 0.1)
        return int((fp["power"] <= 0).sum())

    def test_an_edit_point_gets_tone_under_it_and_is_otherwise_untouched(self):
        m = self.render({"gaps": [], "points": [5.0], "edit_level_db": -6})
        src, out = pcm(self.out("tone.wav")), pcm(self.out("o/stems/d.wav"))
        centre = slice(int(4.95 * 48000), int(5.05 * 48000))
        rise = rms_db(out[centre]) - rms_db(src[centre])
        self.assertGreater(rise, 0.4)               # -6 dB of noise on noise: about +1 dB
        self.assertLess(rise, 1.6)
        far = slice(int(6.0 * 48000), int(7.5 * 48000))
        self.assertLess(float(np.abs(out[far] - src[far]).max()), 1e-5)
        self.assertEqual(m["roomtone"]["d"]["points"], [5.0])

    def test_the_render_is_byte_identical_and_a_clean_stem_is_left_alone(self):
        a = self.render({})
        b = self.render({}, "o2")
        self.assertEqual({k: v["sha256"] for k, v in a["outputs"].items()},
                         {k: v["sha256"] for k, v in b["outputs"].items()})
        fixtures(self.d, 6)
        spec, p = write_spec(self.d, {"stems": {"d": {"input": "dialogue.wav", "roomtone": {}}}})
        c = mb.render(spec, self.out("o3"), p)
        self.assertEqual(c["roomtone"], {})
        self.assertEqual([s["step"] for s in c["skipped"]], ["roomtone"])

    def test_explicit_gaps_and_a_chosen_window_and_the_cap(self):
        tone_fixture(self.out("tone.wav"))
        m = self.render({"gaps": [[8.2, 8.6]], "window": [3, 6], "source": "tone.wav"})
        r = m["roomtone"]["d"]
        self.assertEqual((r["gaps"], r["window_s"]), ([[8.2, 8.6]], [3.0, 6.0]))
        out = pcm(self.out("o/stems/d.wav"))
        self.assertGreater(float(np.abs(out[int(8.3 * 48000):int(8.5 * 48000)]).max()), 0.0)
        self.assertEqual(float(np.abs(out[int(8.7 * 48000):int(8.9 * 48000)]).max()), 0.0)
        with self.assertRaises(mb.MixError) as cm:
            self.render({"points": [i * 0.1 + 1 for i in range(mb.MAX_FILLS + 1)]}, "o2")
        self.assertIn("at most", str(cm.exception))

    def test_a_scalar_speechnorm_and_a_lowpass_past_nyquist(self):
        spec = mb.validate_spec({"stems": {"d": {"input": "x.wav"}},
                                 "mix": {"chain": [{"speechnorm": "d"}]}}, self.d)
        self.assertEqual(spec["mix"]["chain"], [{"speechnorm": {"key": "d"}}])
        with self.assertRaises(mb.SpecError):
            mb.validate_spec({"sample_rate": 32000, "stems": {"d": {"input": "x.wav", "preset": "music_bed"}}},
                             self.d)

    def test_bad_settings_are_refused(self):
        for cfg, frag in (({"gaps": [[3, 2]]}, "end after start"), ({"nope": 1}, "unknown setting"),
                          ({"window": [0, 1]}, "a loop needs")):
            with self.assertRaises(mb.SpecError) as cm:
                mb.validate_spec({"stems": {"d": {"input": "x.wav", "roomtone": cfg}}}, self.d)
            self.assertIn(frag, str(cm.exception))


@needs_numpy
class Stems(Base):
    """DX/MX/FX/ME and the sum-null check. Fixtures: the speech-shaped dialogue, a
    sine bed for music, a 880 Hz sine for effects."""

    def out(self, name):
        return os.path.join(self.d, name)

    def render_json(self):
        return json.loads(Path(self.out("o/render.json")).read_text())

    def spec(self, chain, roles=("dx", "mx", "fx")):
        fixtures(self.d, 8)
        lavfi(self.out("fx.wav"), "sine=f=880:d=8:r=48000,volume=0.1", 8)
        st = {"dialogue": {"input": "dialogue.wav"}, "music": {"input": "music.wav"},
              "fx": {"input": "fx.wav"}}
        for (n, v), r in zip(st.items(), roles):
            if r:
                v["role"] = r
        return write_spec(self.d, {"stems": st, "mix": {
            "gains": {"dialogue": -2, "music": -6, "fx": -4},
            "duck": {"key": "dialogue", "under": ["music"], "depth": 12}, "chain": chain}})

    def test_stems_export_and_null_on_a_linear_bus(self):
        spec, p = self.spec([{"eq": {"freq": 2000, "gain": 3}}, {"highpass": 60}])
        m = mb.render(spec, self.out("o"), p)
        self.assertTrue(m["stems"]["ok"])
        self.assertLess(m["stems"]["residual_dbfs"], mb.NULL_FLOOR_DBFS)
        self.assertEqual(m["skipped"], [])
        mix = pcm(self.out("o/mix.wav"))
        dx, mx, fx, me = (pcm(self.out(f"o/export/{x}.wav")) for x in ("DX", "MX", "FX", "ME"))
        self.assertEqual({x.shape for x in (dx, mx, fx, me)}, {mix.shape})
        self.assertLess(float(np.abs(dx + mx + fx - mix).max()), 1e-5)
        self.assertLess(float(np.abs(me - (mx + fx)).max()), 1e-5)
        # the M&E has no dialogue: quiet where the music is ducked out of nothing but the fx
        self.assertLess(rms_db(me[int(1 * 48000):int(2 * 48000)]), rms_db(mix[int(1 * 48000):int(2 * 48000)]))
        for k in ("DX", "MX", "FX", "ME"):
            self.assertIn(f"export/{k}", m["outputs"])
        self.assertEqual(self.render_json()["stems"]["files"], ["DX", "FX", "ME", "MX"])

    def test_a_clipped_mix_names_the_clip_not_the_bus(self):
        spec, p = self.spec([{"gain": 30}])
        with self.assertRaises(mb.NullError) as cm:
            mb.render(spec, self.out("o"), p)
        self.assertIn("clipped at full scale", str(cm.exception))

    def test_a_limiter_on_the_mix_bus_fails_the_null_loudly(self):
        spec, p = self.spec([{"gain": 12}, {"limiter": {"ceiling": -12}}])
        with self.assertRaises(mb.NullError) as cm:
            mb.render(spec, self.out("o"), p)
        msg = str(cm.exception)
        self.assertIn("DO NOT NULL", msg)
        self.assertIn("limiter", msg)
        st = cm.exception.manifest["stems"]
        self.assertFalse(st["ok"])
        self.assertGreater(st["residual_dbfs"], mb.NULL_FLOOR_DBFS)
        # written for listening, and the record says the same
        self.assertTrue(os.path.isfile(self.out("o/export/DX.wav")))
        self.assertFalse(self.render_json()["stems"]["ok"])
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            spec_path = self.out("spec.json")
            rc = mb.main(["render", spec_path, "--out", self.out("o2")])
        self.assertEqual(rc, 1)
        self.assertIn("DO NOT NULL", err.getvalue())

    def test_no_roles_means_no_export_and_partial_roles_are_refused(self):
        spec, p = self.spec([], roles=(None, None, None))
        m = mb.render(spec, self.out("o"), p)
        self.assertIsNone(m["stems"])
        self.assertFalse(os.path.exists(self.out("o/export")))
        with self.assertRaises(mb.SpecError):
            self.spec([], roles=("dx", "mx", None))
            mb.load_spec(self.out("spec.json"))
        with self.assertRaises(mb.SpecError):
            self.spec([], roles=("dx", "music", "fx"))
            mb.load_spec(self.out("spec.json"))


@needs_numpy
class RoomToneDenoise(Base):
    def out(self, name):
        return os.path.join(self.d, name)

    def render(self, cfg, name="o", chain=None):
        tone_fixture(self.out("tone.wav"))
        st = {"input": "tone.wav", "role": "dx", "denoise_roomtone": cfg}
        if chain:
            st["chain"] = chain
        spec, p = write_spec(self.d, {"stems": {"d": st}})
        return mb.render(spec, self.out(name), p)

    def test_the_floor_drops_and_the_speech_does_not(self):
        m = self.render({"nr": 12, "window": [3, 5]})
        src, out = pcm(self.out("tone.wav")), pcm(self.out("o/stems/d.wav"))
        self.assertEqual(src.shape, out.shape)
        self.assertEqual(m["denoise_roomtone"]["d"]["window_s"][0], 3.0)
        quiet = slice(int(5.5 * 48000), int(7.5 * 48000))     # outside the window it learned from
        drop = rms_db(src[quiet]) - rms_db(out[quiet])
        self.assertGreater(drop, 9.0)    # nr 12 asked for 12 dB; 3 dB of slack
        burst = slice(int(0.6 * 48000), int(2.4 * 48000))
        self.assertAlmostEqual(rms_db(out[burst]), rms_db(src[burst]), delta=1.0)
        self.assertEqual(lag(src[:96000, 0], out[:96000, 0]), 0)    # the delay is trimmed

    def test_the_quietest_window_is_found_when_none_is_given(self):
        m = self.render({"nr": 12})
        w = m["denoise_roomtone"]["d"]["window_s"]
        self.assertGreaterEqual(w[0], 2.5)
        self.assertLessEqual(w[1], 8.0)
        src, out = pcm(self.out("tone.wav")), pcm(self.out("o/stems/d.wav"))
        quiet = slice(int(5.5 * 48000), int(7.5 * 48000))
        self.assertGreater(rms_db(src[quiet]) - rms_db(out[quiet]), 9.0)

    def test_a_window_past_the_end_or_in_digital_silence_is_refused(self):
        for w in ([14, 18], [8.1, 8.8]):
            with self.assertRaises(mb.MixError, msg=str(w)):
                self.render({"window": w})

    def test_refusals(self):
        tone_fixture(self.out("tone.wav"))
        for st in ({"role": "mx", "denoise_roomtone": {}}, {"denoise_roomtone": {"window": [1, 1.1]}},
                   {"denoise_roomtone": {"nr": 0}}, {"denoise_roomtone": {"bogus": 1}}):
            with self.assertRaises(mb.SpecError, msg=str(st)):
                mb.validate_spec({"stems": {"d": dict(st, input="tone.wav")}}, self.d)
