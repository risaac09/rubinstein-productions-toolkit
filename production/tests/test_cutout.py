"""
rpresolve.cutout: the ffmpeg side on a generated clip with a synthetic matte (a
moving white disc), checked by ffprobe; the Vision side only where the helper
builds (macOS with swiftc), on a clip with no person in it.

Run from production/: python3 -m unittest tests.test_cutout -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PRODUCTION = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PRODUCTION))

from rpresolve import cutout, paths  # noqa: E402

FFMPEG, FFPROBE = cutout.FFMPEG, cutout.FFPROBE
W, H, RATE = 320, 180, 25


def disc_mattes(frames, mattes_dir, quality):
    """A stand-in for the Vision helper: a white disc moving across black, one PNG per frame."""
    subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                    f"color=c=black:size={W}x{H}:rate={RATE}:duration={len(frames) / RATE:.6f},"
                    "format=gray,geq=lum='255*lt(hypot(X-(60+40*T),Y-90),40)'",
                    "-frames:v", str(len(frames)), "-start_number", "1", os.path.join(mattes_dir, "%06d.png")],
                   check=True)
    return [{"path": f, "matte": os.path.join(mattes_dir, os.path.basename(f)), "coverage": 0.15} for f in frames]


def probe(path):
    out = subprocess.run([FFPROBE, "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                          "stream=pix_fmt,profile,codec_name,r_frame_rate,nb_read_frames,width,height,color_primaries",
                          "-of", "json", path], check=True, stdout=subprocess.PIPE, encoding="utf-8").stdout
    return json.loads(out)["streams"][0]


def alpha_means(path):
    """The mean alpha of every frame, 0 to 255, through alphaextract and a 1x1 scale."""
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", path, "-vf", "alphaextract,scale=1:1", "-f", "rawvideo",
                          "-pix_fmt", "gray", "-"], check=True, stdout=subprocess.PIPE).stdout
    return list(raw)


@unittest.skipUnless(os.access(FFMPEG, os.X_OK) and os.access(FFPROBE, os.X_OK), "ffmpeg missing")
class TestFfmpegSide(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.clip = os.path.join(cls.tmp.name, "talk.mov")
        subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                        f"color=c=0x4060a0:size={W}x{H}:rate={RATE}:duration=2",
                        "-c:v", "prores_ks", "-pix_fmt", "yuv422p10le", "-y", cls.clip], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_alpha_clip_from_a_synthetic_matte(self):
        out_dir = os.path.join(self.tmp.name, "out")
        report = cutout.build(self.clip, 0.5, 1.5, out_dir, "fast", feather=2, height=H, segmenter=disc_mattes)
        self.assertEqual(report["frames"], RATE)
        self.assertEqual(report["no_person_frames"], [])
        self.assertEqual(report["output"], os.path.join(out_dir, "talk_cutout_0.5-1.5.mov"))
        s = probe(report["output"])
        # prores_ks encodes 4444 from yuva444p10le; ffmpeg's decoder reads the stream back as 12-bit
        self.assertEqual((s["codec_name"], s["profile"], s["pix_fmt"], s["r_frame_rate"], s["color_primaries"]),
                         ("prores", "4444", "yuva444p12le", f"{RATE}/1", "bt709"))
        self.assertEqual((int(s["width"]), int(s["height"]), int(s["nb_read_frames"])), (W, H, RATE))
        means = alpha_means(report["output"])
        self.assertEqual(len(means), RATE)
        self.assertTrue(all(5 < m < 250 for m in means), means)   # a disc: part opaque, part clear
        with open(os.path.join(out_dir, cutout.REPORT_NAME), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["mean_coverage"], 0.15)
        self.assertEqual([n for n in os.listdir(out_dir) if n.startswith(".cutout-")], [])

    def test_summary_warns_on_frames_with_no_person(self):
        rows = lambda frames, d, q: [dict(r, coverage=0.0 if i == 2 else 0.2)  # noqa: E731
                                     for i, r in enumerate(disc_mattes(frames, d, q))]
        report = cutout.build(self.clip, 0.0, 0.2, os.path.join(self.tmp.name, "warn"), height=H, segmenter=rows)
        self.assertEqual(report["no_person_frames"], [3])
        self.assertIn("WARN: no person found in 1 frame(s): 3", cutout.summary(report))

    def test_span_past_the_end_warns(self):
        report = cutout.build(self.clip, 1.8, 2.4, os.path.join(self.tmp.name, "short"), height=H,
                              segmenter=disc_mattes)
        self.assertEqual((report["frames"], report["expected_frames"]), (5, 15))
        self.assertIn("WARN: 15 frame(s) expected in the span, 5 read", cutout.summary(report))

    def test_refused_paths_exit_2(self):
        inside = os.path.join(str(PRODUCTION), "tests", "cutout-out")
        self.assertTrue(paths.in_any_git_tree(inside))
        args = cutout.add_arguments(__import__("argparse").ArgumentParser()).parse_args(
            [self.clip, "--in", "0", "--out", "1", "--out-dir", inside])
        self.assertEqual(cutout.run(args), 2)
        self.assertFalse(os.path.exists(inside))
        # a source sitting where an output would go is never written over
        over = os.path.join(self.tmp.name, "over")
        os.mkdir(over)
        same = os.path.join(over, cutout.REPORT_NAME)
        os.link(self.clip, same)
        args = cutout.add_arguments(__import__("argparse").ArgumentParser()).parse_args(
            [same, "--in", "0", "--out", "1", "--out-dir", over])
        self.assertEqual(cutout.run(args), 2)
        self.assertEqual(os.listdir(over), [cutout.REPORT_NAME])

    def test_no_helper_exits_1_with_the_reason(self):
        args = cutout.add_arguments(__import__("argparse").ArgumentParser()).parse_args(
            [self.clip, "--in", "0", "--out", "0.2", "--out-dir", os.path.join(self.tmp.name, "nohelper")])
        import io
        from contextlib import redirect_stderr
        err = io.StringIO()
        with mock.patch.object(cutout, "person_binary", return_value=None), redirect_stderr(err):
            self.assertEqual(cutout.run(args), 1)
        self.assertIn("needs macOS and swiftc", err.getvalue())

    def test_vision_helper_finds_no_person_in_a_flat_clip(self):
        if not (sys.platform == "darwin" and cutout.person_binary()):
            self.skipTest("Vision person helper unavailable (needs macOS and swiftc)")
        report = cutout.build(self.clip, 0.0, 0.2, os.path.join(self.tmp.name, "vision"), "fast", height=H)
        self.assertEqual(report["frames"], 5)
        self.assertEqual(report["no_person_frames"], [1, 2, 3, 4, 5])
        self.assertEqual(probe(report["output"])["pix_fmt"], "yuva444p12le")
        self.assertTrue(all(m == 0 for m in alpha_means(report["output"])))


if __name__ == "__main__":
    unittest.main()
