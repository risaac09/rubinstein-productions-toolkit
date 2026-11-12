"""
Tests for rpresolve.notes on synthetic clicks at known, irregular times:
measure finds them, snap takes the strongest note in the window and falls back
to the planned beat below the floor, check counts and gives the chance level
on a case worked by hand, and the command prints its one row.

Run: /usr/bin/python3 -m unittest production.tests.test_notes -v
"""

import argparse
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import wave
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import cmd_notes, notes  # noqa: E402  (plain Python: no numpy needed)

try:
    import numpy as np
except ImportError:  # the stdlib-only 3.9 CI job
    np = None

needs_audio = unittest.skipIf(np is None or shutil.which("ffmpeg") is None, "numpy or ffmpeg missing")

SR = 16000
CLICKS = [0.50, 1.02, 1.50, 2.12, 2.55, 3.05, 3.61, 4.10, 4.58, 5.12, 5.60, 6.11, 6.62, 7.10]
HAND = {"onsets": [1.0, 2.0, 3.0], "strength": [3.0, 3.0, 3.0], "period": 1.0}


def write_clicks(path, times, seconds=8.0):
    rng = np.random.default_rng(1)
    y = np.zeros(int(SR * seconds))
    for t in times:
        i = int(round(t * SR))
        burst = rng.standard_normal(80) * np.exp(-np.arange(80) / 15.0)
        y[i:i + 80] += burst
    pcm = (y / np.abs(y).max() * 0.8 * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


@needs_audio
class TestMeasure(unittest.TestCase):
    def test_finds_the_clicks(self):
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "clicks.wav"
            write_clicks(wav, CLICKS)
            rep = notes.measure(str(wav))
        hop = 2 * rep["hop"] / rep["sr"]  # frames are centred: the flux peak runs up to ~1.5 hops early
        for t in CLICKS:
            self.assertLessEqual(min(abs(o - t) for o in rep["onsets"]), hop, t)
        self.assertEqual(len(rep["onsets"]), len(rep["strength"]))
        self.assertTrue(104 <= rep["bpm"] <= 136)

    def test_lines_are_timestamps_only(self):
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "clicks.wav"
            write_clicks(wav, CLICKS)
            rep = notes.measure(str(wav), lines=[[1, 2.5], [3, 4]])
        self.assertEqual(rep["lines"], [[1.0, 2.5], [3.0, 4.0]])


class TestSnap(unittest.TestCase):
    def test_strongest_in_window_then_fallback(self):
        n = {"period": 0.5, "onsets": [4.55, 4.62, 5.30], "strength": [2.0, 5.0, 1.0]}
        out = notes.snap(n, [1, 1], first=4.0, fps=24, lead=0.04)
        # beat 1 at 4.5: the 4.62 note (strength 5) beats 4.55 (2); beat 2 at 5.0: 5.30 is outside 0.16
        self.assertAlmostEqual(out["music_in"], 3.96)
        self.assertEqual(out["cut_frames"], [0, round((4.62 - 3.96) * 24), round((5.0 - 3.96) * 24)])
        self.assertAlmostEqual(out["cut_notes"][1], 4.62 - 3.96)

    def test_below_the_floor_keeps_the_beat(self):
        n = {"period": 0.5, "onsets": [4.55], "strength": [1.4]}
        out = notes.snap(n, [1], first=4.0, fps=24, lead=0.04)
        self.assertEqual(out["cut_frames"][1], round((4.5 - 3.96) * 24))


class TestCheck(unittest.TestCase):
    def test_hand_case(self):
        # fps 10: +-0.1 around each onset. Cuts 1.0, 2.05, 2.5, 3.0 -> 3 on a note.
        # Span 1.0..3.0; covered [1.0,1.1] + [1.9,2.1] + [2.9,3.0] = 0.4 -> 0.2.
        total, hit, chance, used = notes.check(HAND, [1.0, 2.05, 2.5, 3.0], 0.0, 10)
        self.assertEqual((total, hit, used), (4, 3, 3))
        self.assertAlmostEqual(chance, 0.2)

    def test_weak_onset_does_not_count(self):
        weak = {"onsets": [1.0, 2.0, 2.5, 3.0], "strength": [3.0, 3.0, 0.4, 3.0]}
        total, hit, chance, used = notes.check(weak, [1.0, 2.05, 2.5, 3.0], 0.0, 10)
        self.assertEqual((hit, used), (3, 3))
        self.assertAlmostEqual(chance, 0.2)
        self.assertEqual(notes.check(weak, [1.0, 2.5], 0.0, 10, floor=0.0)[1], 2)

    def test_command_row_and_plan(self):
        with tempfile.TemporaryDirectory() as d:
            nj, pj = Path(d) / "n.json", Path(d) / "p.json"
            nj.write_text(json.dumps(HAND))
            pj.write_text(json.dumps({"music_in": 1.0, "cut_times": [0.0, 1.0, 1.5, 2.0]}))
            for argv in (["check", str(nj), "--cuts", "1.0,2.05,2.5,3.0", "--fps", "10"],
                         ["check", str(nj), "--plan", str(pj), "--fps", "10"]):
                p = argparse.ArgumentParser()
                cmd_notes.add_arguments(p)
                buf = io.StringIO()
                with redirect_stdout(buf):
                    self.assertEqual(cmd_notes.run(p.parse_args(argv)), 0)
                self.assertEqual(buf.getvalue(), "cuts 4, on a note 3, chance 0.20, notes 3 at floor 1.5\n")


if __name__ == "__main__":
    unittest.main()
