"""
Tests for rpresolve.decon_titles. The scoring and the method schema run anywhere (stdlib only);
detection needs numpy, Pillow and macOS Vision and skips elsewhere. The verifier renders clips with Pillow whose titles
have known text, in and out frames, boxes and animation (cut, fade, slide, word by word), and
checks each comes back with in and out within 2 frames, box overlap (IoU) at least 0.7 and the
right animation class.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import decon_titles as dt, delivercheck as dc, methods, vision  # noqa: E402

try:
    import numpy  # noqa: F401
except ImportError:
    numpy = None
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None

FONT = "/System/Library/Fonts/Helvetica.ttc"
W, H, FPS, N = 960, 540, 25, 150
FADE, SLIDE = 8, 10

# text, in frame, out frame (exclusive), animation in, animation out, x, y (top of the line box), size
SPECS = [
    ("OPENING NIGHT", 6, 40, "cut", "cut", 120, 80, 56),
    ("Slow fade in", 30, 80, "fade", "fade", 300, 400, 44),
    ("SLIDE LEFT", 60, 110, "slide", "cut", 520, 220, 50),
    ("ONE TWO THREE", 95, 140, "word", "cut", 160, 300, 48),
]


def _alpha(spec, n):
    _, i, o, a_in, a_out = spec[:5]
    a = 1.0
    if a_in == "fade":
        a = min(a, (n - i + 1) / (FADE + 1))
    if a_out == "fade":
        a = min(a, (o - n) / (FADE + 1))
    return max(0.0, min(1.0, a))


def truth_box(spec):
    text, *_, x, y, size = spec
    f = ImageFont.truetype(FONT, size)
    asc, desc = f.getmetrics()
    x0, _, x1, _ = f.getbbox(text, anchor="la")
    return {"x": (x + x0) / W, "y": y / H, "w": (x1 - x0) / W, "h": (asc + desc) / H}


def render(out, specs=SPECS, n=N, shift=0):
    """specs as frames over a still gradient, as lossless FFV1; shift moves every title in time."""
    bg = Image.new("RGB", (W, H))
    bg.putdata([(20 + x // 16, 30 + y // 12, 70) for y in range(H) for x in range(W)])
    proc = subprocess.Popen([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "rawvideo",
                             "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "ffv1", out],
                            stdin=subprocess.PIPE)
    for k in range(n):
        im = bg.copy()
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        for spec in specs:
            text, i, o, a_in, _, x, y, size = spec
            i, o = i + shift, o + shift
            if not i <= k < o:
                continue
            f = ImageFont.truetype(FONT, size)
            a = _alpha((text, i, o, a_in, spec[4]), k)
            if a_in == "slide":
                x = x - round(200 * max(0, SLIDE - 1 - (k - i)) / SLIDE)
            shown = text
            if a_in == "word":
                words = text.split()
                shown = " ".join(words[:min(len(words), (k - i) // 6 + 1)])
            d.text((x, y), shown, font=f, fill=(255, 255, 255, round(255 * a)), anchor="la")
        im.paste(layer, (0, 0), layer)
        proc.stdin.write(im.tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg failed")


def _vision_ok():
    return sys.platform == "darwin" and vision.vision_binary(vision.TEXT_SOURCE) is not None


class TestScoreAndSchema(unittest.TestCase):
    def test_related(self):
        self.assertTrue(dt.related("ONE", "ONE TWO THREE"))
        self.assertTrue(dt.related("Slow fade in", "Slow fade ln"))
        self.assertFalse(dt.related("OPENING NIGHT", "SLIDE LEFT"))

    def test_score(self):
        box = {"x": 0.1, "y": 0.1, "w": 0.3, "h": 0.1}
        a = [{"text": "HELLO", "in_s": 1.0, "out_s": 3.0, "box": box, "animation": "cut", "animation_out": "cut"}]
        self.assertEqual(dt.score(a, a, 25)["part"], 1.0)
        late = [dict(a[0], in_s=1.24, out_s=3.24)]   # 6 frames late at 25 fps: half of TITLE_SCALE
        s = dt.score(a, late, 25)
        self.assertEqual(s["max_frames"], 6.0)
        self.assertAlmostEqual(s["part"], 0.5)
        self.assertEqual(dt.score(a, [], 25)["part"], 0.0)
        self.assertIsNone(dt.score([], [], 25)["part"])

    def test_method_titles_schema(self):
        m = methods.load("punch-montage")
        ok = {"text": "HELLO", "in_s": 0.5, "out_s": 2.0, "box": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.1},
              "animation": "fade", "animation_out": "cut"}
        self.assertEqual(methods.validate(dict(m, titles=[ok])), [])
        self.assertEqual(methods.validate(dict(m, titles=[])), [])
        bad = dict(ok, in_s=3.0, animation="spin", box={"x": 0.9, "y": 0.2, "w": 0.3, "h": 0.1})
        p = methods.validate(dict(m, titles=[bad]))
        self.assertEqual(len(p), 3, p)
        self.assertTrue(methods.validate(dict(m, titles=[dict(ok, extra=1)])))
        self.assertTrue(methods.validate(dict(m, titles="HELLO")))


@unittest.skipIf(numpy is None, "numpy not installed")
@unittest.skipIf(Image is None, "Pillow not installed")
@unittest.skipUnless(os.access(dc.FFMPEG, os.X_OK), "ffmpeg missing")
@unittest.skipUnless(os.path.isfile(FONT), "Helvetica missing")
@unittest.skipUnless(_vision_ok(), "macOS Vision text helper not available")
class TestDetect(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.clip = os.path.join(cls.tmp.name, "titles.mkv")
        render(cls.clip)
        cls.found = dt.detect(cls.clip, FPS, N, (W, H))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_each_title_within_two_frames_and_iou(self):
        self.assertEqual(len(self.found), len(SPECS), self.found)
        for spec in SPECS:
            text, i, o, a_in, a_out = spec[:5]
            with self.subTest(text=text):
                got = next(t for t in self.found if t["text"] == text)
                self.assertLessEqual(abs(got["in_s"] * FPS - i), 2, got)
                self.assertLessEqual(abs(got["out_s"] * FPS - o), 2, got)
                self.assertGreaterEqual(dt._iou(got["box"], truth_box(spec)), 0.7, got)
                self.assertEqual(got["animation"], a_in, got)
                self.assertEqual(got["animation_out"], a_out, got)

    def test_recipe_carries_titles_and_validates(self):
        m = dict(methods.load("punch-montage"), titles=self.found)
        self.assertEqual(methods.validate(m), [])

    def test_cli_recipe_and_compare(self):
        from rpresolve import cmd_decon
        p = argparse.ArgumentParser()
        cmd_decon.add_arguments(p)
        rec, cmp_ = (os.path.join(self.tmp.name, n) for n in ("recipe.json", "compare.json"))
        with redirect_stdout(StringIO()) as out, redirect_stderr(StringIO()):
            self.assertEqual(cmd_decon.run(p.parse_args(["recipe", self.clip, "--out", rec, "--titles",
                                                         "--no-dissolves"])), 0)
            self.assertEqual(cmd_decon.run(p.parse_args(["compare", self.clip, self.clip, "--out", cmp_,
                                                         "--titles", "--no-dissolves"])), 0)
        with open(rec, encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual(methods.validate(doc), [])
        self.assertEqual(sorted(t["text"] for t in doc["titles"]), sorted(s[0] for s in SPECS))
        with open(cmp_, encoding="utf-8") as f:
            c = json.load(f)
        self.assertEqual(c["parts"]["titles"], 1.0)
        self.assertIn("titles", c["timing_only_parts"])
        self.assertIn("Titles: reference 4, rebuild 4, matched 4", out.getvalue())

    def test_compare_part(self):
        late = os.path.join(self.tmp.name, "late.mkv")
        render(late, shift=3)
        s = dt.score(self.found, dt.detect(late, FPS, N, (W, H)), FPS)
        self.assertEqual(s["matched"], len(SPECS))
        self.assertLessEqual(abs(s["median_frames"] - 3), 1, s)
        self.assertEqual(dt.score(self.found, self.found, FPS)["part"], 1.0)


if __name__ == "__main__":
    unittest.main()
