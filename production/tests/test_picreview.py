"""
Offline tests for rpresolve.picreview: scale bands and the jump and
progression questions on known sequences, a pairs sheet from a generated
render (size, and two different halves), the report for a render and for a
cut list that needs --render, the scale track off macOS, and the command's
output guards. lavfi media only, face_boxes stubbed. The media tests need
ffmpeg.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import cutlist, picreview  # noqa: E402
from rpresolve import delivercheck as dc  # noqa: E402

has_ffmpeg = unittest.skipUnless(os.access(cutlist.FFMPEG, os.X_OK), "ffmpeg missing")


def make_render(path):
    """Three 1 s shots at 25 fps: a square crossing left to right on navy, right
    to left on yellow, then red alone."""
    graph = ("color=0x203060:s=160x90:r=25:d=1[n];color=white:s=20x20:r=25:d=1[s1];"
             "color=0xc0c040:s=160x90:r=25:d=1[o];color=white:s=20x20:r=25:d=1[s2];"
             "color=red:s=160x90:r=25:d=1[r];"
             "[n][s1]overlay=x='t*120':y=35[a];[o][s2]overlay=x='120-t*120':y=35[b];"
             "[a][b][r]concat=n=3:v=1:a=0[v]")
    subprocess.run([cutlist.FFMPEG, "-v", "error", "-y", "-filter_complex", graph, "-map", "[v]",
                    "-c:v", "mpeg4", "-q:v", "2", path], check=True)


def faces(*areas):
    """A face_boxes stub: the nth image gets one box of that share of the frame."""
    def fake(images):
        return {img: ([{"x": 0.4, "y": 0.2, "w": a ** 0.5, "h": a ** 0.5, "confidence": 1.0}]
                      if a else []) for img, a in zip(images, areas)}
    return fake


class Flags(unittest.TestCase):
    def test_bands(self):
        self.assertEqual([picreview.band(a) for a in (0, 0.004, 0.01, 0.03, 0.1, 0.4)],
                         [0, 1, 2, 3, 4, 5])

    def test_jump_when_neighbours_share_a_band(self):
        q = picreview.flags([3, 3, 5], [4.0, 9.0])
        self.assertEqual(len(q), 1)
        self.assertIn("Shots 1 and 2 are both medium across the cut at 0:04", q[0])
        self.assertTrue(q[0].endswith("?"))

    def test_no_face_breaks_flags(self):
        self.assertEqual(picreview.flags([3, 0, 3, 0, 0], [1, 2, 3, 4]), [])

    def test_progression_of_three_or_more(self):
        q = picreview.flags([1, 2, 3, 4], [5, 10, 15])
        self.assertEqual(len(q), 1)
        self.assertIn("Shots 1 to 4 grow one band at a time, from wide to close", q[0])
        shrink = picreview.flags([5, 3, 2], [5, 10])
        self.assertIn("Shots 1 to 3 shrink", shrink[0])
        self.assertEqual(picreview.flags([1, 3, 2, 4], [5, 10, 15]), [])  # two moves, no run

    def test_progression_after_a_shot_without_a_face(self):
        q = picreview.flags([0, 1, 2, 3], [5, 10, 15])
        self.assertIn("Shots 2 to 4 grow", q[0])


@has_ffmpeg
class Media(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = self._tmp.name
        self.mov = os.path.join(self.d, "r.mov")
        make_render(self.mov)

    def tearDown(self):
        self._tmp.cleanup()

    def test_sheet_pairs_before_and_after(self):
        jpg = os.path.join(self.d, "s.jpg")
        picreview.make_sheet(self.mov, 1.0, 25, jpg)
        v = dc.probe(jpg)["streams"][0]
        self.assertEqual((v["width"], v["height"]), (1280, 360))  # two 640 px frames, 16:9
        raw = subprocess.run([cutlist.FFMPEG, "-v", "error", "-i", jpg, "-f", "rawvideo",
                              "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
        row = raw[100 * 1280:101 * 1280]  # a row clear of the square and the grid lines
        left, right = sum(row[20:600]) / 580, sum(row[680:1260]) / 580
        self.assertGreater(abs(left - right), 10)  # navy before the cut, yellow after

    def test_sheet_error_is_a_pic_error(self):
        with self.assertRaises(picreview.PicError):
            picreview.make_sheet(self.mov, 99.0, 25, os.path.join(self.d, "s.jpg"))

    def test_report_for_a_render(self):
        sheets, out = os.path.join(self.d, "sheets"), os.path.join(self.d, "r.md")
        os.mkdir(sheets)
        with mock.patch("rpresolve.vision.face_boxes", faces(0.03, 0.03, 0.1)):
            text, summary = picreview.build(self.mov, sheets=sheets, out=out)
        self.assertEqual(summary, "picreview: 2 cut(s), 2 sheet(s), 1 question(s)")
        self.assertEqual(sorted(os.listdir(sheets)), ["001_1.000.jpg", "002_2.000.jpg"])
        self.assertIn("### 001 · 0:01 (1.00 s)", text)
        self.assertIn("![cut 1](<sheets/001_1.000.jpg>)", text)
        self.assertIn("- before: shot 1, medium (3.0% of frame)", text)
        self.assertIn("- after: shot 3, close (10.0% of frame)", text)
        self.assertIn("Shots 1 and 2 are both medium", text)
        self.assertEqual(text.count("- reading:\n"), 2)

    def test_scale_not_measured_off_macos(self):
        with mock.patch("rpresolve.vision.face_boxes", return_value=None):
            text, _ = picreview.build(self.mov)
        self.assertIn("shot scale: not measured", text)
        self.assertIn("- before: shot 1, not measured", text)
        self.assertIn("## Questions\n\n- none", text)

    def test_a_shot_without_a_face_reads_none(self):
        with mock.patch("rpresolve.vision.face_boxes", faces(0, 0.1, 0)):
            text, _ = picreview.build(self.mov)
        self.assertIn("- before: shot 1, none", text)

    def test_cut_list_needs_the_render(self):
        tsv = os.path.join(self.d, "p.tsv")
        Path(tsv).write_text("clip_id\tin\tout\nc/1\t0\t1\nc/2\t0\t1\nc/3\t0\t1\n")
        with self.assertRaises(picreview.PicError):
            picreview.build(tsv)
        with mock.patch("rpresolve.vision.face_boxes", return_value=None):
            text, summary = picreview.build(tsv, render=self.mov, sheets=self.d)
        self.assertIn("2 cut(s), 2 sheet(s)", summary)
        self.assertIn("p.tsv", text.splitlines()[0])

    def test_command_guards(self):
        inside = str(Path(__file__).resolve().parent / "sheets")
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            self.assertEqual(picreview.main([self.mov, "--sheets", inside]), 1)
            self.assertEqual(picreview.main([self.mov, "--out", inside + ".md"]), 1)
            self.assertEqual(picreview.main([os.path.join(self.d, "none.mov")]), 1)
        self.assertIn("git working tree", err.getvalue())
        self.assertFalse(os.path.exists(inside))
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()), \
                mock.patch("rpresolve.vision.face_boxes", return_value=None):
            self.assertEqual(picreview.main([self.mov]), 0)
        self.assertIn("# Picture review: r.mov", out.getvalue())


if __name__ == "__main__":
    unittest.main()
