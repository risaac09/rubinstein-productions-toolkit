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

    def test_the_house_name_comes_from_the_rule_in_rpresolve(self):
        dest = self.v.load_destination("linkedin_9x16")
        parts = {"show": "SW", "episode": "1", "guest": "Guest", "index": 3}
        self.assertEqual(self.v.clip_name(dest, parts, "An example clip!"),
                         "SW001_Guest_03_an-example-clip_9x16.mp4")
        self.assertEqual(self.v.clip_name(dest, parts, ""), "SW001_Guest_03_clip_9x16.mp4")

    def test_a_part_that_breaks_the_rule_stops_the_run(self):
        dest = self.v.load_destination("linkedin_9x16")
        with self.assertRaises(SystemExit):
            self.v.clip_name(dest, {"show": "sw", "episode": "1", "guest": "Guest", "index": 1}, "x")

    def test_a_long_title_still_gives_a_valid_slug(self):
        self.assertTrue(self.v.slug("word " * 30, "clip").islower())
        self.assertFalse(self.v.slug("a" * 59 + " b", "clip").endswith("-"))

    def test_the_audio_args_follow_the_destination(self):
        dest = self.v.load_destination("linkedin_9x16")
        self.assertEqual(self.v.audio_codec_args(dest),
                         ["-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-ac", "2"])

    def test_a_destination_vertcut_cannot_write_is_refused(self):
        self.v.check_destination(self.v.load_destination("linkedin_9x16"))
        for key in ("linkedin_1x1", "youtube_16x9", "client_master"):
            with self.subTest(key=key):
                with self.assertRaises(SystemExit):
                    self.v.check_destination(self.v.load_destination(key))

    def test_the_colour_tags_are_explicit_bt709_limited_range(self):
        a = self.v.COLOR_TAG_ARGS
        for flag, val in (("-color_primaries", "bt709"), ("-color_trc", "bt709"),
                          ("-colorspace", "bt709"), ("-color_range", "tv")):
            self.assertEqual(a[a.index(flag) + 1], val)
        self.assertIn("color_trc=bt709", self.v.COLOR_TAG_FILTER)

    def test_a_burnin_destination_keeps_its_srt_out_from_beside_the_clip(self):
        dest = self.v.load_destination("linkedin_9x16")
        self.assertEqual(dest["captions"], "burnin")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = os.path.join(tmp.name, "SW001_Guest_01_a-test_9x16.mp4")
        self.assertEqual(self.v.srt_path(out, dest),
                         os.path.join(tmp.name, "review-srt", "SW001_Guest_01_a-test_9x16.srt"))
        self.assertEqual(self.v.srt_path(out, dict(dest, captions="sidecar")),
                         os.path.join(tmp.name, "SW001_Guest_01_a-test_9x16.srt"))

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

    NAME = ("--show", "SW", "--episode", "1", "--guest", "Guest")

    def run_cut(self, *extra):
        return subprocess.run([sys.executable, str(CUT), self.src, self.tsv, "-o", self.out, "--no-captions",
                               *self.NAME, *extra], capture_output=True, text=True)

    def test_a_cut_with_the_new_flags_encodes(self):
        p = self.run_cut("--lufs", "-14", "--preset", "ultrafast")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(os.listdir(self.out)), 1)

    def test_the_output_has_the_house_name_and_the_destinations_tags_and_audio(self):
        p = self.run_cut()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(os.listdir(self.out), ["SW001_Guest_01_a-test_9x16.mp4"])
        probe = subprocess.run(
            [shutil.which("ffprobe") or FFMPEG.replace("ffmpeg", "ffprobe"), "-v", "error", "-show_entries",
             "stream=codec_name,color_primaries,color_transfer,color_space,color_range,sample_rate,channels",
             "-of", "default=nw=1", os.path.join(self.out, os.listdir(self.out)[0])],
            capture_output=True, text=True).stdout
        for line in ("color_primaries=bt709", "color_transfer=bt709", "color_space=bt709",
                     "color_range=tv", "sample_rate=48000", "channels=2"):
            self.assertIn(line, probe)

    def test_missing_name_parts_are_named(self):
        p = subprocess.run([sys.executable, str(CUT), self.src, self.tsv, "-o", self.out, "--no-captions",
                            "--show", "SW"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn("--episode, --guest", p.stderr)
        self.assertFalse(os.path.exists(self.out))

    def test_draft_names_keep_the_old_name(self):
        p = subprocess.run([sys.executable, str(CUT), self.src, self.tsv, "-o", self.out, "--no-captions",
                            "--draft-names"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(os.listdir(self.out), ["1-a-test.mp4"])

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
