"""
Tests for vertcut's frame-0 hook (--hook-text): the text goes through rpresolve.hooklint
unchanged, the card sits inside the portrait margins and clear of the caption bar, it
holds for max(0.8 s, 0.3 s per word), captions wait for it, and the render differs from
the no-hook render only inside the card. Hook text here is placeholder text; the tool
never writes copy. Sources come from ffmpeg lavfi. vertcut tests run in the 3.14 job.

Run: /usr/bin/python3 -m unittest discover production/tests -p test_vertcut_hook.py -v
"""

import importlib.util
import json
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
FONTS = ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf")
FONT = next((f for f in FONTS if os.path.isfile(f)), None)

try:
    from PIL import Image, ImageChops, ImageFont
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


def load():
    spec = importlib.util.spec_from_file_location("vertcut_cut", CUT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(HAVE_PIL and FONT, "Pillow or a bold TrueType font missing")
class TestHookGeometry(unittest.TestCase):
    def setUp(self):
        self.v = load()
        self.font = ImageFont.truetype(FONT, 66)

    def test_card_sits_inside_the_margins_and_clear_of_the_caption(self):
        v = self.v
        box = v.plan_hook(["PLACEHOLDER ONE", "PLACEHOLDER TWO"], self.font, v.CAPTION_BOTTOM, v.LINE_CHARS)
        self.assertEqual(v.unsafe_sides(box), [])
        self.assertFalse(v.overlaps(box, v.cue_box(["M" * 16] * 2, self.font, v.CAPTION_BOTTOM)))

    def test_png_alpha_bbox_stays_inside_the_margins(self):
        v = self.v
        lines = ["PLACEHOLDER ONE", "PLACEHOLDER TWO"]
        box = v.plan_hook(lines, self.font, v.CAPTION_BOTTOM, v.LINE_CHARS)
        style = {"text_rgb": (255, 255, 255), "bar_rgb": (0, 0, 0)}
        with tempfile.TemporaryDirectory() as d:
            png = os.path.join(d, "h.png")
            v.render_cue(lines, self.font, png, style, 0.88)
            a = Image.open(png).getchannel("A").getbbox()
        placed = (box[0] + a[0], box[1] + a[1], box[0] + a[2], box[1] + a[3])
        self.assertEqual(v.unsafe_sides(placed), [])
        self.assertFalse(v.overlaps(placed, v.cue_box(["M" * 16] * 2, self.font, v.CAPTION_BOTTOM)))

    def test_a_card_that_meets_the_caption_bar_is_refused(self):
        with self.assertRaises(self.v.CaptionError):
            self.v.plan_hook(["PLACEHOLDER ONE", "PLACEHOLDER TWO"], self.font, 600, 16)

    def test_face_overlap(self):
        v = self.v
        box = (100, 300, 900, 500)
        self.assertTrue(v.hook_covers_face(box, [{"x": 0.2, "y": 0.17, "w": 0.2, "h": 0.1}]))
        self.assertFalse(v.hook_covers_face(box, [{"x": 0.2, "y": 0.5, "w": 0.2, "h": 0.1}]))
        self.assertFalse(v.hook_covers_face(box, []))

    def test_hold_follows_the_word_count(self):
        v = self.v
        self.assertEqual(v.hook_hold(["one two"]), 0.8)
        self.assertAlmostEqual(v.hook_hold(["one two three four", "five six seven eight nine"]), 2.7)


@unittest.skipUnless(HAVE_PIL and FONT and os.path.isfile(FFMPEG), "Pillow, a bold font or ffmpeg missing")
class TestHookRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.dir = os.path.realpath(cls.tmp.name)
        cls.src = os.path.join(d, "SW999_Test_src.mp4")
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=1920x1080:rate=25:duration=6", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=6", "-pix_fmt", "yuv420p", "-shortest", cls.src], check=True)
        cls.tsv = os.path.join(d, "clips.tsv")
        Path(cls.tsv).write_text("1\t0:00\t0:04\tplaceholder title\n")
        cls.words = os.path.join(d, "words.json")
        with open(cls.words, "w") as f:
            json.dump({"segments": [{"words": [{"word": " w%d" % i, "start": i * 0.3, "end": i * 0.3 + 0.25}
                                               for i in range(14)]}]}, f)
        cls.style = os.path.join(d, "style.json")
        Path(cls.style).write_text(json.dumps({"font": FONT}))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_cut(self, out, *extra, captions=True):
        cmd = [sys.executable, str(CUT), self.src, self.tsv, "-o", os.path.join(self.dir, out), "--draft-names",
               "--caption-style", self.style] + (["--words", self.words] if captions else ["--no-captions"])
        return subprocess.run(cmd + list(extra), capture_output=True, text=True)

    def hook_args(self, text="PLACEHOLDER ONE|PLACEHOLDER TWO"):
        return ["--hook-text", text, "--hook-source", "placeholder source"]

    def test_lint_failures_exit_2(self):
        for text in ("a b c d e f g h i j", "PLACEHOLDER " + chr(0x2014) + " TWO", "ONE|TWO|THREE"):
            with self.subTest(text=text):
                r = self.run_cut("o-bad", "--dry-run", *self.hook_args(text))
                self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(self.run_cut("o-bad", "--dry-run", "--hook-text", "ONE").returncode, 2)

    def test_dry_run_warns_that_the_caption_bar_enters_the_bottom_band(self):
        r = self.run_cut("o-dry", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("WARN the caption bar (bottom edge y=1680) enters the bottom safezone band", r.stderr)
        # the default is recorded, not moved: a bar ending at 1240 clears the band
        self.assertNotIn("WARN", self.run_cut("o-dry", "--dry-run", "--caption-y", "1240").stderr)

    def test_hook_json_hold_and_captions_wait(self):
        text = "AA BB CC DD EE|FF GG HH II"
        r = self.run_cut("o-hold", "--crf", "30", "--preset", "ultrafast", *self.hook_args(text))
        self.assertEqual(r.returncode, 0, r.stderr)
        review = Path(self.dir, "o-hold", "review-srt")
        meta = json.loads(next(review.glob("*.hook.json")).read_text())
        self.assertGreaterEqual(meta["end"], max(0.8, 0.3 * 9) - 1e-9)
        self.assertEqual((meta["start"], meta["source"], meta["text"]),
                         (0, "placeholder source", text.replace("|", "\n")))
        starts = [l.split(" --> ")[0] for l in next(review.glob("*.srt")).read_text().splitlines() if " --> " in l]
        self.assertTrue(starts)
        for s in starts:
            h, m, rest = s.split(":")
            self.assertGreaterEqual(int(h) * 3600 + int(m) * 60 + float(rest.replace(",", ".")), meta["end"] - 1e-3)

    def test_render_differs_only_inside_the_card(self):
        for out, extra in (("o-plain", []), ("o-hook", self.hook_args())):
            r = self.run_cut(out, "--crf", "0", "--preset", "ultrafast", *extra, captions=False)
            self.assertEqual(r.returncode, 0, r.stderr)
        frames = []
        for out in ("o-plain", "o-hook"):
            mp4 = next(Path(self.dir, out).glob("*.mp4"))
            png = os.path.join(self.dir, out + ".png")
            subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", "0.1", "-i", str(mp4),
                            "-frames:v", "1", png], check=True)
            frames.append(Image.open(png).convert("RGB"))
        diff = ImageChops.difference(*frames).getbbox()
        box = json.loads(next(Path(self.dir, "o-hook", "review-srt").glob("*.hook.json")).read_text())["box"]
        self.assertIsNotNone(diff)
        pad = 2   # 4:2:0 chroma bleeds one row or column past the card
        self.assertTrue(box[0] - pad <= diff[0] and box[1] - pad <= diff[1]
                        and diff[2] <= box[2] + pad and diff[3] <= box[3] + pad, (diff, box))

    @unittest.skipIf(sys.platform == "darwin", "the face check runs on macOS")
    def test_face_check_is_skipped_off_the_mac(self):
        r = self.run_cut("o-skip", "--crf", "30", "--preset", "ultrafast", *self.hook_args(), captions=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SKIP face check", r.stderr)


if __name__ == "__main__":
    unittest.main()
