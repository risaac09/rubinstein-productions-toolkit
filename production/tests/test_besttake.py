"""
Tests for rpresolve.besttake on takes made here: tone bursts for speech with
silence gaps between them, a take that clips, a take with a noisy room, and a
video take with a hard cut. Deterministic (seeded noise, exact tones).

Run: /usr/bin/python3 -m unittest production.tests.test_besttake -v
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import wave
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from rpresolve import besttake as bt, delivercheck as dc, vision  # noqa: E402

RATE = 48000
SECONDS = 12


def take_signal(on_s, off_s, amp=0.3, noise=0.001, seed=1):
    """440 Hz bursts of on_s with off_s between them, plus seeded noise."""
    t = np.arange(SECONDS * RATE) / RATE
    gate = (np.mod(t, on_s + off_s) < on_s).astype(float)
    x = amp * np.sin(2 * np.pi * 440 * t) * gate
    return x + noise * np.random.default_rng(seed).standard_normal(len(t))


def write_wav(path, x):
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


def make_video(out, wav, cut):
    """160x90 video the length of the wav, with a hard cut at 6 s when `cut`."""
    a = "testsrc2=s=160x90:r=10:d=6"
    b = "smptebars=s=160x90:r=10:d=6" if cut else a
    graph = f"{a},format=yuv420p[v0];{b},format=yuv420p[v1];[v0][v1]concat=n=2:v=1:a=0[v]"
    proc = subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y",
                           "-filter_complex", graph, "-i", wav, "-map", "[v]", "-map", "0:a",
                           "-c:v", "ffv1", "-c:a", "pcm_s16le", out],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)


class TestScoring(unittest.TestCase):
    def test_ramp_is_higher_or_lower_better(self):
        self.assertEqual(bt.metric_score(0.75, {"good": 0.75, "bad": 0.35}), 1.0)
        self.assertAlmostEqual(bt.metric_score(0.55, {"good": 0.75, "bad": 0.35}), 0.5)
        self.assertEqual(bt.metric_score(0.0, {"good": 0.75, "bad": 0.35}), 0.0)
        self.assertEqual(bt.metric_score(0.0, {"good": 0.0, "bad": 0.02}), 1.0)
        self.assertEqual(bt.metric_score(0.03, {"good": 0.0, "bad": 0.02}), 0.0)

    def test_band_scores_both_sides(self):
        row = {"good": [130, 170], "bad": [90, 220]}
        self.assertEqual(bt.metric_score(150, row), 1.0)
        self.assertAlmostEqual(bt.metric_score(110, row), 0.5)
        self.assertAlmostEqual(bt.metric_score(195, row), 0.5)
        self.assertEqual(bt.metric_score(300, row), 0.0)

    def test_score_is_weighted_mean_over_what_exists(self):
        table = {"speech_ratio": {"good": 1.0, "bad": 0.0, "weight": 3.0},
                 "wpm": {"good": [130, 170], "bad": [90, 220], "weight": 1.0}}
        scores, total = bt.score_take({"speech_ratio": 0.5}, table)
        self.assertEqual(scores, {"speech_ratio": 0.5})
        self.assertEqual(total, 0.5)               # wpm absent: weights renormalise
        _, total = bt.score_take({"speech_ratio": 0.5, "wpm": 150}, table)
        self.assertAlmostEqual(total, (3 * 0.5 + 1.0) / 4)
        self.assertEqual(bt.score_take({}, table), ({}, None))

    def test_formula_checks_and_merges(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.json")
            Path(p).write_text(json.dumps({"clipped_share": {"weight": 0}}))
            t = bt.load_formula(p)
            self.assertEqual(t["clipped_share"]["weight"], 0)
            self.assertEqual(t["clipped_share"]["bad"], bt.RANKING["clipped_share"]["bad"])
            self.assertEqual(bt.RANKING["clipped_share"]["weight"], 3.0)   # not mutated
            for bad in ({"loudness": {"weight": 1}}, {"wpm": {"weight": -1}},
                        {"wpm": {"good": [100, 90]}}, {"wpm": {"good": 5}}):
                Path(p).write_text(json.dumps(bad))
                with self.assertRaises(bt.BestTakeError):
                    bt.load_formula(p)

    def test_reason_quotes_numbers_and_never_says_best(self):
        m = {"speech_ratio": 0.8, "clipped_share": 0.03, "noise_floor_dbfs": -58.0,
             "wada_snr_db": 28.0}
        scores, _ = bt.score_take(m, bt.RANKING)
        text = bt.reason(m, scores)
        self.assertIn("speech share 0.8", text)
        self.assertIn("clipped share 0.03 (score 0.00)", text)
        self.assertNotIn("best", text.lower())
        self.assertEqual(bt.reason({}, {}), "no metric could be measured")


class TestTakes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        d = cls.dir = os.path.join(cls._tmp.name, "takes")
        os.mkdir(d)
        write_wav(os.path.join(d, "a_steady.wav"), take_signal(3.0, 0.5))
        write_wav(os.path.join(d, "b_gappy.wav"), take_signal(0.5, 1.5))
        write_wav(os.path.join(d, "c_clipped.wav"), take_signal(3.0, 0.5, amp=1.6))
        write_wav(os.path.join(d, "d_noisy.wav"), take_signal(3.0, 0.5, noise=0.03))
        # 24 words in 12 s: 120 wpm
        words = [{"start": i * 0.5, "end": i * 0.5 + 0.3, "word": f"w{i}"} for i in range(24)]
        Path(d, "a_steady.json").write_text(json.dumps({"segments": [{"words": words}]}))
        Path(d, "e_broken.wav").write_text("not audio")
        cls.rep = bt.build(d, faces=False)
        cls.by = {os.path.basename(r["file"]): r for r in cls.rep["takes"]}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_numbers(self):
        a, b = self.by["a_steady.wav"]["metrics"], self.by["b_gappy.wav"]["metrics"]
        self.assertAlmostEqual(a["speech_ratio"], 3.0 / 3.5, delta=0.05)
        self.assertAlmostEqual(b["speech_ratio"], 0.25, delta=0.05)
        self.assertEqual(a["pauses"], 3)                      # 0.5 s gaps: 3 between 4 runs
        self.assertGreater(b["pauses"], a["pauses"])
        self.assertLess(a["noise_floor_dbfs"], -50)
        self.assertGreater(self.by["d_noisy.wav"]["metrics"]["noise_floor_dbfs"],
                           a["noise_floor_dbfs"] + 20)
        self.assertEqual(a["clipped_share"], 0.0)
        self.assertGreater(self.by["c_clipped.wav"]["metrics"]["clipped_share"], 0.3)
        self.assertNotIn("cuts", a)                           # audio only: no video numbers

    def test_transcript_gives_wpm_only_to_the_take_that_has_one(self):
        self.assertEqual(self.by["a_steady.wav"]["metrics"]["wpm"], 120.0)
        self.assertNotIn("wpm", self.by["b_gappy.wav"]["metrics"])

    def test_order_and_skips(self):
        names = [os.path.basename(r["file"]) for r in self.rep["takes"]]
        self.assertEqual(names[0], "a_steady.wav")
        self.assertLess(names.index("a_steady.wav"), names.index("c_clipped.wav"))
        self.assertLess(names.index("a_steady.wav"), names.index("d_noisy.wav"))
        self.assertLess(names.index("a_steady.wav"), names.index("b_gappy.wav"))
        self.assertEqual([r["rank"] for r in self.rep["takes"]], [1, 2, 3, 4])
        self.assertEqual([os.path.basename(s["file"]) for s in self.rep["skipped"]],
                         ["e_broken.wav"])

    def test_formula_is_the_only_weighting(self):
        zero = bt.load_formula()
        for row in zero.values():
            row["weight"] = 0
        zero["clipped_share"]["weight"] = 1
        rep = bt.build(self.dir, faces=False, formula=zero)
        clipped = [r for r in rep["takes"] if r["file"].endswith("c_clipped.wav")][0]
        self.assertEqual(clipped["score"], 0.0)
        self.assertEqual(rep["formula"]["clipped_share"]["weight"], 1)

    def test_markdown_and_json_never_pick(self):
        md = bt.markdown(self.rep)
        self.assertIn("not a pick", md)
        self.assertEqual(md.count("\n| 1 |"), 1)
        self.assertNotIn("best", md.lower().replace("besttake", ""))
        self.assertIn("e_broken.wav", md)
        json.dumps(self.rep)

    def test_cli_writes_outside_git_and_refuses_inside(self):
        out = os.path.join(self._tmp.name, "out.json")
        with redirect_stdout(io.StringIO()) as so, redirect_stderr(io.StringIO()):
            rc = bt.main([self.dir, "--no-faces", "--json", out,
                          "--md", os.path.join(self._tmp.name, "o.md")])
        self.assertEqual(rc, 0)
        self.assertIn("| 1 |", so.getvalue())
        self.assertEqual(json.loads(Path(out).read_text())["takes"][0]["rank"], 1)
        inside = os.path.join(os.path.dirname(os.path.abspath(bt.__file__)), "x.json")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as se:
            self.assertEqual(bt.main([self.dir, "--no-faces", "--json", inside]), 1)
        self.assertFalse(os.path.exists(inside))
        self.assertIn("besttake:", se.getvalue())

    def test_manifest_with_relative_paths(self):
        m = os.path.join(self._tmp.name, "m.json")
        Path(m).write_text(json.dumps({"takes": [
            {"file": "takes/a_steady.wav", "words": "takes/a_steady.json"},
            {"file": "takes/b_gappy.wav"}]}))
        rep = bt.build(m, faces=False)
        self.assertEqual(len(rep["takes"]), 2)
        self.assertEqual(rep["takes"][0]["metrics"]["wpm"], 120.0)

    def test_empty_folder_and_bad_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(bt.BestTakeError):
                bt.find_takes(d)
            p = os.path.join(d, "m.json")
            Path(p).write_text("{}")
            with self.assertRaises(bt.BestTakeError):
                bt.find_takes(p)


class TestVideo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        d = cls._tmp.name
        wav = os.path.join(d, "t.wav")
        write_wav(wav, take_signal(3.0, 0.5))
        cls.steady = os.path.join(d, "steady.mkv")
        cls.jumpy = os.path.join(d, "jumpy.mkv")
        make_video(cls.steady, wav, cut=False)
        make_video(cls.jumpy, wav, cut=True)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_cuts_and_stability(self):
        s = bt.measure_take(self.steady, faces=False)["metrics"]
        j = bt.measure_take(self.jumpy, faces=False)["metrics"]
        self.assertEqual(s["cuts"], 0)
        self.assertEqual(s["longest_shot_share"], 1.0)
        self.assertEqual(j["cuts"], 1)
        self.assertAlmostEqual(j["longest_shot_share"], 0.5, delta=0.02)
        self.assertNotIn("face_share", s)

    def test_faces_share_from_vision(self):
        def one_face(images):
            return {p: ([{"x": 0.4, "y": 0.2, "w": 0.2, "h": 0.3, "confidence": 0.9}]
                        if i % 2 == 0 else []) for i, p in enumerate(images)}
        with mock.patch.object(vision, "face_boxes", one_face):
            m = bt.measure_take(self.steady)
        self.assertAlmostEqual(m["metrics"]["face_share"], 0.5, delta=0.1)

    def test_no_vision_skips_cleanly(self):
        with mock.patch.object(vision, "face_boxes", return_value=None):
            r = bt.measure_take(self.steady)
        self.assertIsNone(r["metrics"]["face_share"])
        self.assertIn("faces not measured (Vision unavailable)", r["notes"])
        _, score = bt.score_take(r["metrics"], bt.RANKING)
        self.assertIsNotNone(score)                           # left out of the mean, not zeroed


if __name__ == "__main__":
    unittest.main()
