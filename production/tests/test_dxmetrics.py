"""
Tests for rpresolve.dxmetrics: sibilance, flatness, hum and the blind SNR on
numpy signals with known answers, then loudness, lag and compare on WAV files
written here (ffmpeg adelay supplies a known delay). Synthetic signals only.

Run: /usr/bin/python3 -m unittest production.tests.test_dxmetrics -v
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

from rpresolve import dxmetrics as dxm  # noqa: E402

RATE = 48000
FFMPEG = os.environ.get("RPRESOLVE_FFMPEG", "/opt/homebrew/bin/ffmpeg")
needs_ffmpeg = unittest.skipUnless(os.access(FFMPEG, os.X_OK), "ffmpeg missing")


def tone(freq, seconds, amp=0.1, rate=RATE):
    t = np.arange(int(seconds * rate)) / rate
    return amp * np.sin(2 * np.pi * freq * t)


def noise(seconds, rms=0.01, seed=1, rate=RATE):
    return rms * np.random.default_rng(seed).standard_normal(int(seconds * rate))


def write_wav(path, x, rate=RATE):
    """16-bit PCM, mono (n,) or interleaved (n, ch)."""
    x = np.asarray(x).reshape(len(x), -1)
    with wave.open(path, "wb") as w:
        w.setnchannels(x.shape[1])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.clip(np.round(x * 32767), -32768, 32767).astype("<i2").tobytes())
    return path


class TestMeasures(unittest.TestCase):
    def test_sibilance_ratio_reads_band_power(self):
        x = tone(1000, 2, 1.0) + tone(7000, 2, 0.1)
        self.assertAlmostEqual(dxm.sibilance_ratio_db(x, RATE), -20.0, delta=0.3)
        # white noise: 4000 Hz of band over 4900 Hz
        self.assertAlmostEqual(dxm.sibilance_ratio_db(noise(4), RATE), -0.88, delta=0.3)
        self.assertIsNone(dxm.sibilance_ratio_db(noise(1), 16000))  # no 9 kHz at 16 kHz

    def test_flatness_tells_tone_from_noise(self):
        self.assertGreater(dxm.flatness(noise(2)), 0.8)
        self.assertLess(dxm.flatness(tone(440, 2)), 0.1)
        self.assertIsNone(dxm.flatness(np.zeros(RATE)))

    def test_hum_excess_finds_the_mains_harmonics(self):
        x = noise(6, 0.01)
        quiet = dxm.hum_excess_db(x, RATE)
        hum = x + sum(tone(60 * k, 6, 0.02 / k) for k in range(1, 6))
        found = dxm.hum_excess_db(hum, RATE)
        self.assertLess(abs(quiet[50]), 3)
        self.assertLess(abs(quiet[60]), 3)
        self.assertGreater(found[60], 15)
        self.assertLess(found[50], 3)
        self.assertEqual(dxm.hum_excess_db(noise(0.5), RATE), {50: None, 60: None})

    def test_hum_reads_the_quiet_frames_not_the_speech(self):
        # a loud burst over the second half must not hide hum in the first
        x = noise(8, 0.005) + sum(tone(50 * k, 8, 0.01 / k) for k in range(1, 6))
        x[len(x) // 2:] += noise(4, 0.3, seed=2)
        self.assertGreater(dxm.hum_excess_db(x, RATE)[50], 15)

    def test_wada_table_rises_with_snr(self):
        g, snrs = dxm._wada_table()
        self.assertTrue(np.all(np.diff(g) > 0))
        self.assertEqual((snrs.min(), snrs.max()), dxm.WADA_RANGE_DB)

    def test_wada_snr_tracks_a_known_mixture(self):
        rng = np.random.default_rng(5)
        n = 300000
        s = rng.gamma(0.4, 1.0, n) * rng.choice([-1.0, 1.0], n)
        s /= np.sqrt(np.mean(s ** 2))
        est = []
        for snr in (0, 10, 20, 30):
            est.append(dxm.wada_snr_db(s * 10 ** (snr / 20) + rng.standard_normal(n)))
            self.assertAlmostEqual(est[-1], snr, delta=2.0)
        self.assertEqual(est, sorted(est))
        self.assertIsNone(dxm.wada_snr_db(np.zeros(100)))

    def test_quiet_frames_leave_out_digital_silence(self):
        x = np.r_[np.zeros(RATE), noise(1, 0.5), noise(1, 0.01)]
        q = dxm.quiet_frames(x, RATE, share=0.5)
        self.assertEqual(len(q), RATE)
        self.assertLess(np.sqrt(np.mean(q ** 2)), 0.02)


@needs_ffmpeg
class TestFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.tmp, name)

    def delayed(self, src, samples, out, extra=""):
        subprocess.run([FFMPEG, "-v", "error", "-y", "-i", src, "-af",
                        f"adelay=delays={samples}S:all=1,atrim=end_sample={4 * RATE}{extra}",
                        "-c:a", "pcm_s16le", out], check=True)
        return out

    def test_measure_reads_loudness_and_echoes_params(self):
        f = write_wav(self.path("sine.wav"), tone(1000, 4, 0.1))
        r = dxm.measure(f)
        self.assertAlmostEqual(r["integrated_lufs"], -23.0, delta=0.3)
        self.assertEqual(r["sample_rate"], RATE)
        self.assertEqual(r["range_s"], [0.0, 4.0])
        self.assertEqual(r["params"]["hum_nperseg"], dxm.HUM_NPERSEG)
        part = dxm.measure(f, start=1.0, end=2.5)
        self.assertEqual(part["range_s"], [1.0, 2.5])

    def test_lag_finds_a_delay_and_a_flipped_polarity(self):
        a = write_wav(self.path("a.wav"), noise(4, 0.1) + tone(220, 4, 0.05))
        b = self.delayed(a, 1200, self.path("b.wav"))
        g = dxm.lag(a, b)
        self.assertEqual(g["lag_samples"], 1200)
        self.assertEqual(g["lag_ms"], 25.0)
        self.assertEqual(g["polarity"], "same")
        self.assertGreater(g["peak_corr"], 0.9)
        self.assertEqual(dxm.lag(b, a)["lag_samples"], -1200)
        flip = self.delayed(a, 0, self.path("flip.wav"), ",volume=-1")
        self.assertEqual(dxm.lag(a, flip)["polarity"], "inverted")
        self.assertEqual(dxm.lag(a, flip)["lag_samples"], 0)

    def test_compare_gives_b_minus_a(self):
        a = write_wav(self.path("a.wav"), noise(4, 0.02) + sum(tone(60 * k, 4, 0.02 / k)
                                                               for k in range(1, 6)))
        b = write_wav(self.path("b.wav"), noise(4, 0.02))
        r = dxm.compare(a, b)
        self.assertLess(r["delta"]["hum_excess_db_60"], -10)
        self.assertEqual(set(r["delta"]), set(dxm.DELTAS))
        self.assertEqual(r["lag"]["lag_samples"], 0)

    def test_command_writes_json_and_refuses_the_repo(self):
        a = write_wav(self.path("a.wav"), noise(2, 0.05))
        out = self.path("m.json")
        with redirect_stdout(io.StringIO()) as so, redirect_stderr(io.StringIO()):
            self.assertEqual(dxm.main(["measure", a, "--out", out]), 0)
        self.assertIn("WADA SNR", so.getvalue())
        with open(out, encoding="utf-8") as f:
            self.assertIn("wada_snr_db", json.load(f))
        inside = str(Path(__file__).resolve().parent / "dxmetrics-test.json")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as se:
            self.assertEqual(dxm.main(["measure", a, "--out", inside]), 1)
        self.assertIn("git working tree", se.getvalue())
        self.assertFalse(os.path.exists(inside))
        with redirect_stdout(io.StringIO()) as so, redirect_stderr(io.StringIO()):
            self.assertEqual(dxm.main(["lag", a, a]), 0)
        self.assertIn("lag 0 samples", so.getvalue())


if __name__ == "__main__":
    unittest.main()
