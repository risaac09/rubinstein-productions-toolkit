"""
Tests for rpresolve.duckplan: the speech-activity key on numpy power arrays
(blips, held pauses, lookahead), the sidechain threshold and the sendcmd
lines, then whole renders of a music chord under a loud and a quiet talker
written here: the depth holds within 2 dB for both, the lead-in is not
ducked, and the sendcmd file applied by ffmpeg gives the same depth.
Synthetic signals only.

Run: /usr/bin/python3 -m unittest production.tests.test_duckplan -v
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

from rpresolve import duckplan as dp, dxmetrics as dxm  # noqa: E402

RATE = 48000
FFMPEG = os.environ.get("RPRESOLVE_FFMPEG", "/opt/homebrew/bin/ffmpeg")
needs_ffmpeg = unittest.skipUnless(os.access(FFMPEG, os.X_OK), "ffmpeg missing")
HOP = dp.HOP_S


def write_wav(path, x, rate=RATE):
    x = np.asarray(x).reshape(len(x), -1)
    with wave.open(path, "wb") as w:
        w.setnchannels(x.shape[1])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.clip(np.round(x * 32767), -32768, 32767).astype("<i2").tobytes())
    return path


def talk(seconds, rms_dbfs, seed):
    """Noise cut into syllables, five a second, at an RMS of rms_dbfs."""
    n = int(seconds * RATE)
    env = np.abs(np.sin(2 * np.pi * 2.5 * np.arange(n) / RATE))
    x = np.random.default_rng(seed).standard_normal(n) * env
    return x * 10 ** (rms_dbfs / 20) / np.sqrt(np.mean(x ** 2))


def dialogue():
    """12 s: room at -60 dBFS; a loud talker 2-5 s (-20 dBFS), a quiet one
    7-10 s (-32 dBFS)."""
    x = 0.001 * np.random.default_rng(9).standard_normal(12 * RATE)
    x[2 * RATE:5 * RATE] += talk(3, -20, 1)
    x[7 * RATE:10 * RATE] += talk(3, -32, 2)
    return x


def music(seconds=12):
    t = np.arange(int(seconds * RATE)) / RATE
    chord = sum(0.05 * np.sin(2 * np.pi * f * t) for f in (220.0, 277.18, 329.63))
    return np.stack([chord, 0.8 * chord], axis=1)


def steps(*runs):
    """Mean power per hop from (seconds, dBFS) runs."""
    return np.concatenate([np.full(int(round(s / HOP)), 10 ** (db / 10)) for s, db in runs])


class TestKey(unittest.TestCase):
    def test_speech_opens_the_key_ahead_of_the_first_word(self):
        k, floor, gate, phrases = dp.key(steps((2, -60), (1, -20), (2, -60)), HOP)
        self.assertAlmostEqual(floor, -60.0, places=3)
        self.assertAlmostEqual(gate, -50.0, places=3)
        self.assertEqual(phrases, [[200, 300]])
        t = np.arange(len(k)) * HOP
        self.assertLess(k[t < 2.0 - dp.LOOKAHEAD_S - 0.05].max(), 0.01)  # the lead-in is left alone
        self.assertGreater(k[int(2.0 / HOP)], 0.9)                       # down by the first word
        self.assertGreater(k[(t > 2.1) & (t < 3.0)].min(), 0.99)
        self.assertLess(k[t > 3.0 + dp.HOLD_S + 0.3].max(), 0.01)        # back up after the hold

    def test_a_click_does_not_open_it_and_a_pause_does_not_close_it(self):
        k, _, _, phrases = dp.key(steps((2, -60), (0.03, -10), (2, -60)), HOP)
        self.assertLess(k.max(), 0.01)
        self.assertEqual(phrases, [])
        k, _, _, phrases = dp.key(steps((2, -60), (1, -20), (0.2, -60), (1, -20), (2, -60)), HOP)
        self.assertEqual(phrases, [[200, 420]])
        self.assertGreater(k[int(2.9 / HOP):int(3.3 / HOP)].min(), 0.99)

    def test_a_cut_between_rooms_needs_the_gate_set_by_hand(self):
        # a noisy room (-40) with a word, then a quiet room (-60) with a softer word:
        # the file's one floor is the quiet room's, so the noisy room's tone opens the key
        p = steps((3, -40), (1, -20), (3, -40), (3, -60), (1, -35), (3, -60))
        k, floor, _, phrases = dp.key(p, HOP)
        self.assertAlmostEqual(floor, -60.0, places=3)
        self.assertEqual(phrases[0], [0, 700])
        k, _, _, phrases = dp.key(p, HOP, gate_db=-38)
        self.assertEqual(phrases, [[300, 400], [1000, 1100]])
        t = np.arange(len(k)) * HOP
        self.assertLess(k[t < 2.8].max(), 0.01)
        self.assertLess(k[(t > 4.5) & (t < 9.8)].max(), 0.01)

    def test_the_gate_can_be_set_by_hand_and_silence_is_refused(self):
        _, _, gate, phrases = dp.key(steps((2, -60), (1, -45), (2, -60)), HOP, gate_db=-40)
        self.assertEqual((gate, phrases), (-40.0, []))
        with self.assertRaises(dp.ar.ReportError):
            dp.key(np.zeros(300), HOP)

    def test_sidechain_threshold_gives_the_depth(self):
        f = dp.sidechain_filter(12)
        thr = float(f.split("threshold=")[1].split(":")[0])
        self.assertAlmostEqual(20 * np.log10(thr) * (1 - 1 / dp.SC_RATIO), -12.0, places=3)
        self.assertIn(f"ratio={dp.SC_RATIO}", f)

    def test_sendcmd_lines_rest_at_both_ends(self):
        k, _, _, _ = dp.key(steps((1, -60), (1, -20), (2, -60)), HOP)
        lines = dp.sendcmd_lines(k, HOP, 12.0)
        self.assertEqual(lines[0], "0.000 volume@duck volume 1.000000;")
        self.assertTrue(lines[-1].endswith("volume 1.000000;"))
        self.assertIn("volume 0.251189;", "".join(lines))
        times = [float(x.split()[0]) for x in lines]
        self.assertEqual(times, sorted(times))


@needs_ffmpeg
class TestRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.dx = write_wav(os.path.join(cls.tmp, "dx.wav"), dialogue())
        cls.mx = write_wav(os.path.join(cls.tmp, "mx.wav"), music())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def out(self, name):
        return os.path.join(self.tmp, name)

    def check(self, p, depth):
        m = p["measured"]
        self.assertAlmostEqual(m["gr_under_speech_db"], depth, delta=1.0)
        self.assertEqual(len(m["span_gr_db"]), 2)
        for v in m["span_gr_db"]:            # loud and quiet talker alike
            self.assertAlmostEqual(v, depth, delta=2.0)
        self.assertLessEqual(m["depth_spread_db"], 2.0)
        self.assertLess(abs(m["gr_outside_db"]), 0.5)
        self.assertLess(abs(m["gr_lead_in_db"]), 0.5)
        self.assertGreater(m["lead_in_s"], 1.7)

    def test_render_ducks_both_talkers_by_the_depth(self):
        for depth in (12.0, 18.0):
            with self.subTest(depth=depth):
                p = dp.render(self.mx, self.dx, self.out(f"r{depth:g}"), depth_db=depth)
                self.check(p, depth)
                self.assertEqual(len(p["speech_spans"]), 2)
                self.assertAlmostEqual(p["speech_spans"][0][0], 2.0, delta=0.05)
                self.assertAlmostEqual(p["speech_spans"][1][1], 10.0, delta=0.05)
                info = dp.ar.audio_stream(p["out"])
                self.assertEqual((info["channels"], info["sample_rate"]), (2, RATE))
                with open(p["files"]["plan"], encoding="utf-8") as f:
                    saved = json.load(f)
                self.assertNotIn("_key", saved)
                self.assertEqual(saved["params"]["lookahead_s"], dp.LOOKAHEAD_S)
                self.assertIn("measured", saved)

    def test_the_sendcmd_file_gives_the_same_depth(self):
        d = self.out("plan")
        p = dp.make_plan(self.dx, d)
        key = dp.ar.audio_stream(p["files"]["key"])
        self.assertEqual((key["channels"], key["sample_rate"]), (1, dp.KEY_RATE))
        out = os.path.join(d, "sendcmd-ducked.wav")
        n = int(RATE * dp.HOP_S)
        subprocess.run([FFMPEG, "-v", "error", "-y", "-i", self.mx, "-af",
                        f"asetnsamples=n={n}:p=0,asendcmd=f=duck.sendcmd,volume@duck=volume=1",
                        "-c:a", "pcm_f32le", out], check=True, cwd=d)
        x, _ = dxm.decode(self.mx)
        y, _ = dxm.decode(out)
        k = np.interp(np.arange(len(x)) / RATE, np.arange(len(p["_key"])) * p["_hop"], p["_key"])
        self.check(dict(p, measured=dp.measure(x, y[:len(x)], k, RATE, 12.0)), 12.0)

    def test_the_command_refuses_the_repo(self):
        inside = str(Path(__file__).resolve().parent / "duckplan-test")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as se:
            self.assertEqual(dp.main(["plan", self.dx, "--out", inside]), 1)
        self.assertIn("git working tree", se.getvalue())
        self.assertFalse(os.path.exists(inside))
        with redirect_stdout(io.StringIO()) as so, redirect_stderr(io.StringIO()):
            self.assertEqual(dp.main(["plan", self.dx, "--out", self.out("cli"),
                                      "--depth", "15"]), 0)
        self.assertIn("2 phrase(s)", so.getvalue())
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as se:
            self.assertEqual(dp.main(["plan", self.dx, "--out", self.out("bad"),
                                      "--depth", "0"]), 1)
        self.assertIn("--depth", se.getvalue())


if __name__ == "__main__":
    unittest.main()
