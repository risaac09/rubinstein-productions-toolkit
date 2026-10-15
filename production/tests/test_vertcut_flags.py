"""
Tests for vertcut's loudness and preset flags. The defaults must stay what the
tool always wrote (-16 LUFS, -1.5 dBTP, LRA 11, crf 20, veryfast); the flags
exist so a destination that wants -14 LUFS is not a source edit.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CUT = ROOT / "tools" / "vertcut" / "lib" / "vertcut_cut.py"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"

try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


def load():
    spec = importlib.util.spec_from_file_location("vertcut_cut", CUT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(HAVE_PIL, "Pillow missing (vertcut needs it)")
class TestFlags(unittest.TestCase):
    def setUp(self):
        self.v = load()

    def test_the_defaults_are_what_the_tool_always_wrote(self):
        self.assertEqual(self.v.loudnorm_filter(), "loudnorm=I=-16:TP=-1.5:LRA=11")
        self.assertEqual(self.v.video_codec_args(20, "veryfast"),
                         ["-c:v", "libx264", "-crf", "20", "-preset", "veryfast"])

    def test_a_different_target(self):
        self.assertEqual(self.v.loudnorm_filter(-14, -1.0, 9), "loudnorm=I=-14:TP=-1:LRA=9")
        self.assertEqual(self.v.video_codec_args(18, "slow")[-2:], ["-preset", "slow"])

    def test_out_of_range_targets_are_refused(self):
        for args in ((5, -1.5, 11), (-40, -1.5, 11), (-16, 1.0, 11), (-16, -12.0, 11), (-16, -1.5, 0.5),
                     (-16, -1.5, 40)):
            with self.subTest(args=args):
                with self.assertRaises(SystemExit):
                    self.v.loudnorm_filter(*args)

    def test_the_presets_are_x264s(self):
        self.assertIn("veryfast", self.v.X264_PRESETS)
        self.assertEqual(len(self.v.X264_PRESETS), 9)


@unittest.skipUnless(HAVE_PIL and os.access(FFMPEG, os.X_OK), "Pillow or ffmpeg missing")
class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.src = os.path.join(d, "src.mp4")
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=2",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-c:a", "aac",
                        "-shortest", self.src], check=True)
        self.tsv = os.path.join(d, "clips.tsv")
        Path(self.tsv).write_text("1\t0.2\t1.5\tA test\n")
        self.out = os.path.join(d, "out")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cut(self, *extra):
        return subprocess.run([sys.executable, str(CUT), self.src, self.tsv, "-o", self.out, "--no-captions", *extra],
                              capture_output=True, text=True)

    def test_a_cut_with_the_new_flags_encodes(self):
        p = self.run_cut("--lufs", "-14", "--preset", "ultrafast")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(os.listdir(self.out)), 1)

    def test_a_bad_target_stops_before_any_work(self):
        p = self.run_cut("--lufs", "5")
        self.assertEqual(p.returncode, 1)
        self.assertIn("--lufs 5 is outside -30 .. -5", p.stderr)
        self.assertFalse(os.path.exists(self.out))

    def test_a_bad_preset_is_an_argument_error(self):
        p = self.run_cut("--preset", "warp")
        self.assertEqual(p.returncode, 2)


if __name__ == "__main__":
    unittest.main()
