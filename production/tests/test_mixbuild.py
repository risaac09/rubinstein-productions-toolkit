"""
Tests for rpresolve.mixbuild: the spec parser, the refusals, byte-identical
renders, loudness-matched blind A/B excerpts. Fixtures are lavfi
(speech-shaped and pink noise, a sine bed); no client audio.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import numpy  # noqa: F401
    from rpresolve import mixbuild as mb
    from rpresolve import audioreport as ar
    from rpresolve.paths import OutputRefused
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

needs_numpy = unittest.skipUnless(HAVE_NUMPY, "needs numpy")
FFMPEG = os.environ.get("RPRESOLVE_FFMPEG", "/opt/homebrew/bin/ffmpeg")

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
      arnndn_none: {method: arnndn}
  music:
    input: music.wav
    chain: [{gain: -3}, {lowpass: 12000}]
mix:
  gains: {dialogue: 0, music: -12}
  duck: {key: dialogue, under: [music], threshold: -30, ratio: 8}
  chain:
    - loudnorm: {i: -16, tp: -1.5}
    - limiter: {ceiling: -1.5}
"""


def lavfi(path, expr, seconds, channels=2):
    subprocess.run([FFMPEG, "-hide_banner", "-v", "error", "-y", "-f", "lavfi", "-i", expr,
                    "-t", str(seconds), "-ac", str(channels), "-c:a", "pcm_s24le", path], check=True)


def fixtures(d, seconds=24):
    # speech-shaped: noise band-limited to the voice range, pulsed like syllables, over a pink floor
    lavfi(os.path.join(d, "dialogue.wav"),
          f"anoisesrc=d={seconds}:c=white:r=48000:seed=3:a=0.5,bandpass=f=1200:width_type=h:w=2500,"
          "tremolo=f=4:d=0.9,volume=3[a];anoisesrc=d=%d:c=pink:r=48000:seed=5:a=0.02[b];"
          "[a][b]amix=inputs=2:normalize=0" % seconds, seconds)
    lavfi(os.path.join(d, "music.wav"), f"sine=f=220:d={seconds}:r=48000,volume=0.3", seconds)


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = self._t.name
        self.addCleanup(self._t.cleanup)


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
        self.assertEqual(f[0], ["equalizer=f=3000:t=q:w=1:g=2", "aresample=192000",
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
        self.assertEqual({k: v["sha256"] for k, v in a["outputs"].items()},
                         {k: v["sha256"] for k, v in b["outputs"].items()})
        self.assertEqual(set(a["outputs"]), {"dialogue", "music", "dialogue__afftdn_6",
                                             "dialogue__arnndn_none", "mix"})
        self.assertEqual(a["graphs"], b["graphs"])
        self.assertTrue(any(s["step"] == "denoise arnndn" for s in a["skipped"]))
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
