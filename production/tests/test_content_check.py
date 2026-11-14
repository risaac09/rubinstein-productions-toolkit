"""
Tests for rpresolve.cmd_content_check on lavfi media made here (small H.264/AAC
files) and on invented word lists and face boxes. Every threshold is a working
default; these tests pin the rows, not the numbers' standing. Skipped where
ffmpeg is missing ("ffmpeg missing"); the repeat row also where numpy is.

Run: /usr/bin/python3 -m unittest production.tests.test_content_check -v
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import cmd_content_check as cc, delivercheck as dc, qc, vision  # noqa: E402

try:
    import numpy  # noqa: F401
except ImportError:  # pragma: no cover
    numpy = None

TH = qc.parse_overrides([], base={**qc.THRESHOLDS, **cc.THRESHOLDS})


def _have_ffmpeg():
    if not all(os.access(t, os.X_OK) for t in (dc.FFMPEG, dc.FFPROBE)):
        return False
    enc = subprocess.run([dc.FFMPEG, "-hide_banner", "-encoders"], stdin=subprocess.DEVNULL,
                         capture_output=True, text=True).stdout
    return "libx264" in enc


def _ff(*args):
    p = subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", *args],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr)


def _video(out, src, audio="sine=f=440:r=48000:d={d},volume=0.3", d=4, pan=None):
    args = ["-f", "lavfi", "-i", src.format(d=d)]
    if audio:
        args += ["-f", "lavfi", "-i", audio.format(d=d)]
    if pan:
        args += ["-af", pan]
    _ff(*args, "-c:v", "libx264", "-pix_fmt", "yuv420p", *(["-c:a", "aac", "-ac", "2"] if audio else ["-an"]),
        "-t", str(d), out)


def _row(result, name):
    return next(c for c in result["checks"] if c["check"] == name)


def _words(*spans):
    return [{"start": a, "end": b, "word": w} for a, b, w in spans]


class Parsers(unittest.TestCase):
    def test_channel_rms(self):
        text = ("[Parsed_astats_0 @ 0x1] Channel: 1\n[Parsed_astats_0 @ 0x1] RMS level dB: -20.5\n"
                "[Parsed_astats_0 @ 0x1] Channel: 2\n[Parsed_astats_0 @ 0x1] RMS level dB: -inf\n")
        self.assertEqual(cc.parse_channel_rms(text), [-20.5, float("-inf")])

    def test_channel_row(self):
        self.assertEqual(cc.channel_row([-20.0, -22.0], 2, TH)["status"], qc.PASS)
        self.assertEqual(cc.channel_row([-20.0, -30.0], 2, TH)["status"], qc.FAIL)
        self.assertEqual(cc.channel_row([-20.0, float("-inf")], 2, TH)["status"], qc.FAIL)
        self.assertEqual(cc.channel_row([-20.0], 1, TH)["status"], qc.SKIP)
        self.assertEqual(cc.channel_row([float("-inf")] * 2, 2, TH)["status"], qc.SKIP)


class CutInWord(unittest.TestCase):
    WORDS = _words((1.0, 1.5, "alpha"), (1.6, 2.2, "beta"), (3.0, 3.4, "gamma"))

    def test_between_words_passes(self):
        self.assertEqual(cc.word_rows([2.6], self.WORDS, TH)["status"], qc.PASS)

    def test_inside_a_word_fails_and_names_it(self):
        r = cc.word_rows([1.9], self.WORDS, TH)
        self.assertEqual(r["status"], qc.FAIL)
        self.assertIn("beta", r["found"])

    def test_on_a_word_edge_warns(self):
        r = cc.word_rows([1.01], self.WORDS, TH)
        self.assertEqual(r["status"], qc.WARN)
        self.assertEqual(cc.word_rows([2.21], self.WORDS, TH)["status"], qc.WARN)

    def test_inside_beats_edge(self):
        self.assertEqual(cc.word_rows([1.01, 1.9], self.WORDS, TH)["status"], qc.FAIL)


class FaceRow(unittest.TestCase):
    def _row(self, boxes):
        images = [f"/x/{i}.jpg" for i in range(len(boxes))]
        fake = dict(zip(images, boxes))
        with mock.patch.object(vision, "face_boxes", return_value=fake), \
                mock.patch.object(cc.dc, "_run", return_value=mock.Mock(returncode=0, stderr="")), \
                mock.patch.object(cc.os, "listdir", return_value=[os.path.basename(i) for i in images]):
            return cc.face_row("/x/clip.mp4", 10.0, TH)

    centred = [{"x": 0.4, "y": 0.3, "w": 0.2, "h": 0.3}]
    clipped = [{"x": 0.0, "y": 0.3, "w": 0.2, "h": 0.3}]

    def test_centred_faces_pass(self):
        self.assertEqual(self._row([self.centred] * 5)["status"], qc.PASS)

    def test_faces_at_the_edge_warn(self):
        self.assertEqual(self._row([self.clipped] * 4 + [self.centred] * 2)["status"], qc.WARN)

    def test_too_few_faces_skip(self):
        self.assertEqual(self._row([self.centred] * 2 + [[]] * 5)["status"], qc.SKIP)

    def test_no_helper_skips(self):
        with mock.patch.object(vision, "face_boxes", return_value=None), \
                mock.patch.object(cc.dc, "_run", return_value=mock.Mock(returncode=0, stderr="")), \
                mock.patch.object(cc.os, "listdir", return_value=["f0001.jpg"]):
            self.assertEqual(cc.face_row("/x/clip.mp4", 10.0, TH)["status"], qc.SKIP)


@unittest.skipUnless(_have_ffmpeg(), "ffmpeg missing")
class OnMedia(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        t = cls._tmp.name
        a, b = os.path.join(t, "a.mp4"), os.path.join(t, "b.mp4")
        cls.f = {k: os.path.join(t, k + ".mp4") for k in
                 ("clean", "silent", "noaudio", "frozen", "leftonly", "repeat", "distinct")}
        scene_a = "testsrc2=s=160x90:r=10:d={d}"
        scene_b = "mandelbrot=s=160x90:r=10:end_scale=0.05"
        _video(cls.f["clean"], scene_b)
        _video(cls.f["silent"], scene_b, audio="sine=f=440:r=48000:d={d},volume=0")
        _video(cls.f["noaudio"], scene_b, audio=None)
        _video(cls.f["frozen"], "color=c=red:s=160x90:r=10:d={d}")
        _video(cls.f["leftonly"], scene_b, pan="pan=stereo|c0=c0|c1=0*c1")
        _video(a, scene_a, d=4)
        _video(b, scene_b, d=4)
        for name, order in (("repeat", (a, b, a)), ("distinct", (a, b))):
            ins = [x for p in order for x in ("-i", p)]
            n = len(order)
            _ff(*ins, "-filter_complex", "".join(f"[{i}:v][{i}:a]" for i in range(n)) +
                f"concat=n={n}:v=1:a=1[v][a]", "-map", "[v]", "-map", "[a]", "-c:v", "libx264",
                "-pix_fmt", "yuv420p", "-c:a", "aac", cls.f[name])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def table(self, name, **kw):
        return cc.run_checks(self.f[name], TH, faces=False, **kw)

    def test_clean_render_has_no_fail(self):
        r = self.table("clean")
        self.assertEqual(r["status"], qc.PASS, r["checks"])

    def test_silent_render_fails_the_share(self):
        self.assertEqual(_row(self.table("silent"), "silent_share")["status"], qc.FAIL)

    def test_no_audio_stream_warns_and_has_no_balance_row(self):
        r = self.table("noaudio")
        self.assertEqual(_row(r, "silent_share")["status"], qc.WARN)
        self.assertEqual(r["status"], qc.PASS)
        self.assertNotIn("channel_balance", [c["check"] for c in r["checks"]])

    def test_frozen_render_fails_the_share(self):
        self.assertEqual(_row(self.table("frozen"), "frozen_share")["status"], qc.FAIL)

    def test_one_channel_fails_balance(self):
        self.assertEqual(_row(self.table("leftonly"), "channel_balance")["status"], qc.FAIL)

    @unittest.skipUnless(numpy, "needs numpy")
    def test_repeated_scene_fails_and_distinct_scenes_pass(self):
        rep = _row(self.table("repeat"), "repeated_footage")
        self.assertEqual(rep["status"], qc.FAIL, rep)
        self.assertEqual(_row(self.table("distinct"), "repeated_footage")["status"], qc.PASS)

    @unittest.skipUnless(numpy, "needs numpy")
    def test_a_held_shot_is_not_a_repeat(self):
        self.assertEqual(_row(self.table("frozen"), "repeated_footage")["status"], qc.PASS)

    def test_words_file_is_optional(self):
        self.assertEqual(_row(self.table("clean"), "cut_in_word")["status"], qc.SKIP)

    def test_cut_in_word_through_the_table(self):
        csv_path = os.path.join(self._tmp.name, "shots.csv")
        with open(csv_path, "w") as f:
            f.write("start_s\n0\n1.0\n")
        words = _words((0.5, 1.5, "alpha"))
        r = cc.run_checks(self.f["clean"], TH, words=words, shots=csv_path, faces=False)
        self.assertEqual(_row(r, "cut_in_word")["status"], qc.FAIL)
        self.assertEqual(r["status"], qc.FAIL)


if __name__ == "__main__":
    unittest.main()
