"""
Tests for rpresolve.roomtone: the quietest-window search on numpy power
arrays, then find on a two-stream file written here (one live stream with
a dropout, one all zeros) and beds looped from noise and from a hum tone,
with the seam check, the curve choice and the refusals. Synthetic signals
only.

Run: /usr/bin/python3 -m unittest production.tests.test_roomtone -v
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from rpresolve import dxmetrics as dxm, roomtone as rt  # noqa: E402
from rpresolve.paths import OutputRefused  # noqa: E402

RATE = 48000
FFMPEG = os.environ.get("RPRESOLVE_FFMPEG", "/opt/homebrew/bin/ffmpeg")
needs_ffmpeg = unittest.skipUnless(os.access(FFMPEG, os.X_OK), "ffmpeg missing")


def noise(seconds, rms, seed=1, channels=1):
    return rms * np.random.default_rng(seed).standard_normal((int(seconds * RATE), channels))


def write_wav(path, x, rate=RATE):
    x = np.asarray(x).reshape(len(x), -1)
    with wave.open(path, "wb") as w:
        w.setnchannels(x.shape[1])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.clip(np.round(x * 32767), -32768, 32767).astype("<i2").tobytes())
    return path


class TestQuietest(unittest.TestCase):
    def test_the_least_power_run_wins(self):
        p = np.r_[np.full(10, 1.0), np.full(5, 0.01), np.full(10, 1.0)]
        self.assertEqual(rt.quietest(p, 5), (10, 5))

    def test_a_run_through_digital_silence_is_never_chosen(self):
        p = np.r_[np.full(4, 1.0), np.zeros(3), np.full(4, 1.0), np.full(4, 0.5), np.full(4, 1.0)]
        self.assertEqual(rt.quietest(p, 4), (11, 4))
        self.assertIsNone(rt.quietest(np.zeros(20), 4))
        self.assertIsNone(rt.quietest(np.r_[1.0, 0.0, 1.0, 0.0], 2))

    def test_a_short_stream_gives_the_whole_stream(self):
        self.assertEqual(rt.quietest(np.full(3, 0.2), 30), (0, 3))


@needs_ffmpeg
class TestFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.tmp, name)

    def two_stream_file(self):
        """10 s: stream 0 noise at -40 dBFS, -60 dBFS over 4-7 s and digital
        zeros over 1.0-1.5 s; stream 1 all zeros (an unused camera track)."""
        live = noise(10, 0.01)
        live[4 * RATE:7 * RATE] = noise(3, 0.001, seed=2)
        live[RATE:int(1.5 * RATE)] = 0
        a = write_wav(self.path("live.wav"), live)
        b = write_wav(self.path("zero.wav"), np.zeros(10 * RATE))
        out = self.path("two.mov")
        subprocess.run([FFMPEG, "-v", "error", "-y", "-i", a, "-i", b, "-map", "0:a", "-map", "1:a",
                        "-c:a", "pcm_s16le", out], check=True)
        return out

    def test_find_reports_each_stream(self):
        r = rt.find([self.two_stream_file()])
        live, dead = r["streams"]
        self.assertEqual(live["status"], "ok")
        self.assertAlmostEqual(live["start_s"], 4.0, delta=0.11)
        self.assertAlmostEqual(live["end_s"], 7.0, delta=0.11)
        self.assertAlmostEqual(live["rms_dbfs"], -60.0, delta=1.0)
        self.assertLess(live["peak_dbfs"], -45)
        self.assertEqual(dead["status"], "digital_silence")
        self.assertNotIn("start_s", dead)
        self.assertEqual(r["params"]["window_s"], rt.WINDOW_S)

    def test_find_command_writes_json_and_refuses_the_repo(self):
        f = self.two_stream_file()
        out = self.path("find.json")
        with redirect_stdout(io.StringIO()) as so, redirect_stderr(io.StringIO()):
            self.assertEqual(rt.main(["find", f, "--out", out]), 0)
        self.assertIn("digital_silence", so.getvalue())
        with open(out, encoding="utf-8") as fh:
            self.assertEqual(len(json.load(fh)["streams"]), 2)
        inside = str(Path(__file__).resolve().parent / "roomtone-test.json")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as se:
            self.assertEqual(rt.main(["find", f, "--out", inside]), 1)
        self.assertIn("git working tree", se.getvalue())
        self.assertFalse(os.path.exists(inside))

    def test_a_noise_bed_takes_qsin_and_keeps_its_level(self):
        src = write_wav(self.path("room.wav"), noise(6, 0.003, seed=3, channels=2))
        out = self.path("bed.wav")
        r = rt.bed(src, out, 20.0, start=1.0, end=4.0)
        self.assertEqual(r["curve"], "qsin")
        self.assertTrue(r["seam_ok"])
        self.assertLessEqual(abs(r["seam_dev_db"]), rt.SEAM_MAX_DB)
        self.assertLess(r["tried"]["tri"], -2.0)  # a linear crossfade dips incoherent noise
        self.assertEqual(r["seams"], 10)
        x, _ = dxm.decode(out)
        self.assertEqual(len(x), 20 * RATE)
        info = rt.ar.audio_stream(out)
        self.assertEqual((info["channels"], info["sample_rate"]), (2, RATE))
        level = 10 * np.log10(np.mean(x ** 2))
        self.assertAlmostEqual(level, 20 * np.log10(0.003), delta=1.0)
        with open(self.path("bed.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["curve"], "qsin")
        self.assertEqual(sorted(os.listdir(self.tmp)), ["bed.json", "bed.wav", "room.wav"])

    def test_a_hum_bed_takes_tri_and_a_forced_qsin_fails_the_check(self):
        t = np.arange(5 * RATE) / RATE
        src = write_wav(self.path("hum.wav"), 0.05 * np.sin(2 * np.pi * 60 * t))
        r = rt.bed(src, self.path("bed.wav"), 10.0)  # default window: the quietest 3 s
        self.assertEqual(r["curve"], "tri")
        self.assertLess(abs(r["seam_dev_db"]), 0.5)
        self.assertGreater(r["tried"]["qsin"], 2.0)  # an equal-power crossfade bumps a tone
        out = self.path("bed-qsin.wav")
        with redirect_stdout(io.StringIO()) as so, redirect_stderr(io.StringIO()):
            self.assertEqual(rt.main(["bed", src, "--length", "10", "--out", out,
                                      "--curve", "qsin"]), 1)
        self.assertIn("OVER", so.getvalue())
        self.assertTrue(os.path.exists(out))

    def test_bed_refusals(self):
        src = write_wav(self.path("room.wav"), noise(6, 0.003))
        with self.assertRaises(ValueError):
            rt.bed(src, self.path("b.wav"), 10, start=1.0, end=2.5)  # under MIN_SOURCE_S
        with self.assertRaises(ValueError):
            rt.bed(src, self.path("b.wav"), 10, start=1.0, end=4.0, xfade_s=2.0)
        with self.assertRaises(ValueError):
            rt.bed(src, self.path("b.wav"), 10, start=1.0)
        with self.assertRaises(rt.ar.ReportError):
            rt.bed(src, self.path("b.wav"), 10, start=4.0, end=8.0)  # past the end
        with self.assertRaises(OutputRefused):
            rt.bed(src, src, 10)
        with self.assertRaises(OutputRefused):
            rt.bed(src, str(Path(__file__).resolve().parent / "roomtone-bed.wav"), 10)
        silent = write_wav(self.path("silent.wav"), np.zeros(4 * RATE))
        with self.assertRaises(rt.ar.ReportError):
            rt.bed(silent, self.path("b.wav"), 10)
        self.assertEqual(sorted(os.listdir(self.tmp)), ["room.wav", "silent.wav"])


if __name__ == "__main__":
    unittest.main()
