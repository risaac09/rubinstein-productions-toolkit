"""
Tests for rpresolve.decon_sound (decon D8) on clips ffmpeg makes here: flat-colour shots cut at
2, 4 and 6 s under an audio track with known hits (a burst on the first cut, one frame after the
second, none at the third, a stray one off any cut) and a known loud span (3.2 to 3.9 s).

Run: ~/.venvs/rp-tools/bin/python -m unittest production.tests.test_decon_sound -v
"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse  # noqa: E402
from rpresolve import delivercheck as dc, methods  # noqa: E402

try:
    import numpy as np
    from rpresolve import audioreport, cmd_decon, cutlist, decon, decon_sound, shots  # noqa: E402
except ImportError:  # numpy missing on this interpreter
    np = None

FPS, SR = 25, 16000
COLOURS = ["0xe8e8e8", "0x202080", "0xe0c020", "0x208020"]


def audio(path, bursts, loud=None, secs=8.0):
    """A quiet noise floor, a 40 ms 1 kHz burst at each time in bursts, and a steady loud tone over loud=(a, b)."""
    n = int(SR * secs)
    y = np.random.RandomState(1).randn(n) * 1e-4
    t = np.arange(int(0.04 * SR)) / SR
    for b in bursts:
        i = int(round(b * SR))
        y[i:i + len(t)] += 0.5 * np.sin(2 * np.pi * 1000 * t)
    if loud:
        i, j = int(loud[0] * SR), int(loud[1] * SR)
        y[i:j] += 0.25 * np.sin(2 * np.pi * 300 * np.arange(j - i) / SR)
    pcm = (np.clip(y, -1, 1) * 32767).astype("<i2").tobytes()
    subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "s16le", "-ar", str(SR),
                    "-ac", "1", "-i", "-", path], input=pcm, check=True)


def video(out, wav=None):
    parts = ";".join(f"color=c={c}:s=192x108:r={FPS}:d=2,format=yuv420p[v{i}]" for i, c in enumerate(COLOURS))
    graph = parts + ";" + "".join(f"[v{i}]" for i in range(4)) + "concat=n=4:v=1:a=0[v]"
    cmd = [dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y"] + (["-i", wav] if wav else []) + \
        ["-filter_complex", graph, "-map", "[v]"] + (["-map", "0:a", "-c:a", "pcm_s16le"] if wav else []) + \
        ["-c:v", "ffv1", out]
    subprocess.run(cmd, check=True, stdin=subprocess.DEVNULL)


@unittest.skipIf(np is None, "numpy not installed")
@unittest.skipUnless(os.access(dc.FFMPEG, os.X_OK), "ffmpeg missing")
class TestDeconSound(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        wav = os.path.join(d, "a.wav")
        audio(wav, bursts=[2.0, 4.0 + 1 / FPS, 5.0], loud=(3.2, 3.9))
        cls.ref = os.path.join(d, "ref.mkv")
        video(cls.ref, wav)
        cls.silent = os.path.join(d, "silent.mkv")
        video(cls.silent)
        cls.report = shots.build(cls.ref, dissolves=False, adaptive=True)
        cls.sm = decon_sound.build(cls.ref, cls.report, kinds=False)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_onsets_at_the_known_times(self):
        ons = decon_sound.onsets(np.frombuffer(
            cutlist.decode_pcm(self.ref, 0.0), dtype="<i2") / 32768.0)
        for want in (2.0, 4.04, 5.0):
            self.assertTrue(any(abs(o - want) < 0.03 for o in ons), (want, ons))

    def test_hits_at_cuts(self):
        self.assertEqual(self.sm["cuts"], 3)
        got = {h["cut"]: h["offset_frames"] for h in self.sm["hits"]}
        self.assertEqual(sorted(got), [1, 2])   # none at the third cut; the stray burst at 5 s is no hit
        self.assertAlmostEqual(got[1], 0.0, delta=0.6)
        self.assertAlmostEqual(got[2], 1.0, delta=0.6)
        self.assertAlmostEqual(self.sm["hit_share"], 0.667, places=2)

    def test_loud_span_in_the_curve(self):
        c = self.sm["curve_db"]
        self.assertGreater(c[3], c[1] + 6)     # 3 s holds the loud span, 1 s the floor
        self.assertGreater(c[3], c[6] + 6)

    def test_kinds_sidecar_gives_spans_and_no_text(self):
        d = self.tmp.name
        side = os.path.join(d, "ref.kinds.json")
        with open(side, "w") as f:
            json.dump({"spans": [{"start": 0, "end": 1.9, "kind": "speech", "withheld": False},
                                 {"start": 2.0, "end": 2.4, "kind": "music", "withheld": True},
                                 {"start": 2.6, "end": 3.0, "kind": "music", "withheld": True}]}, f)
        sm = decon_sound.build(self.ref, self.report)       # found beside the video
        self.assertEqual(sm["spans"], [{"start_s": 0.0, "end_s": 1.9, "kind": "speech"},
                                       {"start_s": 2.0, "end_s": 3.0, "kind": "music"}])
        self.assertEqual(sm["spans_from"], "ref.kinds.json")
        os.remove(side)
        bad = os.path.join(d, "bad.json")
        with open(bad, "w") as f:
            f.write("[1]")
        with self.assertRaises(decon_sound.SoundError):
            decon_sound.build(self.ref, self.report, kinds=bad)

    def test_audio_only_kinds_run_and_never_fail_the_map(self):
        sm = decon_sound.build(self.ref, self.report)
        self.assertTrue(sm["spans_from"].startswith("vertcut kinds"))
        self.assertTrue(all(s["kind"] in decon_sound.KINDS for s in sm["spans"]))

    def test_silent_audio_still_validates(self):
        wav = os.path.join(self.tmp.name, "quiet.wav")
        subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "anullsrc=r=16000:cl=mono", "-t", "8", wav], check=True)
        quiet = os.path.join(self.tmp.name, "quiet.mkv")
        video(quiet, wav)
        sm = decon_sound.build(quiet, self.report, kinds=False)
        self.assertTrue(sm["integrated_lufs"] is None or sm["integrated_lufs"] < -60)
        rec = decon.recipe(self.report, quiet, "snd-quiet", sound=sm)
        self.assertEqual(methods.validate(rec), [])
        self.assertIsNotNone(decon_sound.compare_part(sm, self.sm))

    def test_no_audio_is_an_error(self):
        rep = shots.build(self.silent, dissolves=False, adaptive=True)
        with self.assertRaisesRegex(decon_sound.SoundError, "no audio"):
            decon_sound.build(self.silent, rep)

    def test_recipe_carries_sound_and_validates(self):
        rec = decon.recipe(self.report, self.ref, "snd-test", sound=self.sm)
        self.assertEqual(methods.validate(rec), [])
        self.assertEqual([h["slot"] for h in rec["sound"]["hits"]], [2, 3])
        bad = copy.deepcopy(rec)
        bad["sound"]["hits"][0]["slot"] = 99
        bad["sound"]["spans"] = [{"start_s": 2, "end_s": 1, "kind": "x"}]
        bad["sound"]["extra"] = 1
        self.assertEqual(len(methods.validate(bad)), 3)
        plain = decon.recipe(self.report, self.ref, "snd-test")
        self.assertNotIn("sound", plain)
        self.assertEqual(methods.validate(plain), [])

    def test_hits_in_the_end_black_are_left_out(self):
        sm = {"integrated_lufs": -20.0, "lra_lu": 1.0, "curve_db": [], "spans": [],
              "hits": [{"cut": 2, "offset_frames": 0.0}, {"cut": 3, "offset_frames": 0.0}]}
        self.assertEqual([h["slot"] for h in decon_sound.for_recipe(sm, 3)["hits"]], [3])

    def test_compare_adds_a_sound_part_and_skips_without_audio(self):
        same = decon.compare(self.report, self.report, self.ref, self.ref)
        self.assertEqual(same["parts"]["sound"], 1.0)
        self.assertIn("sound", same["timing_only_parts"])
        self.assertEqual(same["score"], 100.0)
        rb = shots.build(self.silent, dissolves=False, adaptive=True)
        r = decon.compare(self.report, rb, self.ref, self.silent)
        self.assertNotIn("sound", r["parts"])
        self.assertIn("no audio", r["sound"]["skipped"])
        self.assertIn("Sound: not scored", decon.summary_compare(r))

    def test_cli_sound_and_recipe_flag(self):
        d = self.tmp.name
        out = os.path.join(d, "sound.json")
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            rc = cmd_decon.run(argparse.Namespace(
                action="sound", reference=self.ref, out=out, kinds=None, backend="scdet", no_dissolves=True))
        self.assertEqual(rc, 0)
        with open(out) as f:
            self.assertEqual(json.load(f)["cuts"], 3)
        rout = os.path.join(d, "recipe.json")
        buf = StringIO()
        with redirect_stdout(buf), redirect_stderr(StringIO()):
            rc = cmd_decon.run(argparse.Namespace(
                action="recipe", reference=self.ref, out=rout, id="snd-cli", name=None, fits="motion", beats=None,
                grade=False, against=None, sound=True, kinds=None, titles=False, effects=False, backend="scdet", no_dissolves=True))
        self.assertEqual(rc, 0, buf.getvalue())
        with open(rout) as f:
            self.assertIn("sound", json.load(f))

    def test_a_made_mix_with_a_known_duck_shows_the_dip(self):
        """Music at a steady level ducked 14 dB over 3 to 5 s (what duckplan does): the curve dips there."""
        d = self.tmp.name
        wav = os.path.join(d, "duck.wav")
        subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "sine=f=220:d=8:r=16000,volume='if(between(t,3,5),0.2,1)':eval=frame,volume=0.3",
                        wav], check=True)
        c = decon_sound.curve(audioreport.loudness(wav)[1])
        self.assertLess(c[4], c[1] - 10)
        self.assertLess(c[4], c[6] - 10)


if __name__ == "__main__":
    unittest.main()
