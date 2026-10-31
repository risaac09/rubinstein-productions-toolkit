"""
Tests for vertcut's caption standard (production/design-tokens.md): a partial-opacity
bar behind each cue, text and bar colours from options or a local file, 16 characters
a line and two lines at most, and a refusal when a word will not fit. No brand value
is asserted or shipped; the colours here are arbitrary test values.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CUT = ROOT / "tools" / "vertcut" / "lib" / "vertcut_cut.py"

try:
    from PIL import Image, ImageFont
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


def load():
    spec = importlib.util.spec_from_file_location("vertcut_cut", CUT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def words(text, step=0.3):
    return [{"start": i * step, "end": i * step + 0.25, "word": w}
            for i, w in enumerate(text.split())]


@unittest.skipUnless(HAVE_PIL, "Pillow missing (vertcut needs it)")
class TestWrapAndGroup(unittest.TestCase):
    def setUp(self):
        self.v = load()

    def test_wrap_at_sixteen(self):
        self.assertEqual(self.v.wrap_chars("one two three four five", 16),
                         ["one two three", "four five"])
        self.assertEqual(self.v.wrap_chars("exactly sixteen!!", 16), ["exactly", "sixteen!!"])

    def test_no_cue_exceeds_two_lines_of_sixteen(self):
        text = "we keep the friction because the friction is the product we sell"
        cues = self.v.group_cues(words(text), 0, 10, 8, 99, 5.0)
        self.assertGreater(len(cues), 1)
        for c in cues:
            lines = self.v.wrap_chars(c["text"], 16)
            self.assertLessEqual(len(lines), 2, c["text"])
            self.assertTrue(all(len(l) <= 16 for l in lines), c["text"])
        self.assertEqual(" ".join(c["text"] for c in cues), text)

    def test_a_word_that_cannot_fit_is_refused(self):
        with self.assertRaises(self.v.CaptionError):
            self.v.group_cues(words("an incomprehensibilities word"), 0, 5, 3, 32, 5.0)

    def test_the_line_length_is_an_option(self):
        cues = self.v.group_cues(words("an incomprehensibilities word"), 0, 5, 3, 64, 5.0, 24)
        self.assertEqual(cues[0]["text"], "an incomprehensibilities word")


@unittest.skipUnless(HAVE_PIL, "Pillow missing (vertcut needs it)")
class TestStyle(unittest.TestCase):
    def setUp(self):
        self.v = load()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.v.LOCAL_STYLE = os.path.join(self.tmp.name, "absent.json")

    def write(self, data):
        p = os.path.join(self.tmp.name, "style.json")
        with open(p, "w") as fh:
            json.dump(data, fh)
        return p

    def test_defaults_are_neutral(self):
        s = self.v.resolve_style(None, None, None, None)
        self.assertEqual(s["text_rgb"], (255, 255, 255))
        self.assertEqual(s["bar_rgb"], (0, 0, 0))
        self.assertEqual(s["font"], self.v.DEFAULT_FONT)

    def test_file_then_flags(self):
        p = self.write({"text_color": "#102030", "bar_color": "#405060", "font": "/x.ttf"})
        s = self.v.resolve_style(p, None, "#AABBCC", None)
        self.assertEqual(s["text_rgb"], (0x10, 0x20, 0x30))
        self.assertEqual(s["bar_rgb"], (0xAA, 0xBB, 0xCC))
        self.assertEqual(s["font"], "/x.ttf")

    def test_the_untracked_local_file_is_picked_up(self):
        self.v.LOCAL_STYLE = self.write({"bar_color": "#010203"})
        self.assertEqual(self.v.resolve_style(None, None, None, None)["bar_rgb"], (1, 2, 3))

    def test_an_empty_value_in_the_file_keeps_the_default(self):
        s = self.v.resolve_style(self.write({"font": None, "bar_color": ""}), None, None, None)
        self.assertEqual(s["font"], self.v.DEFAULT_FONT)
        self.assertEqual(s["bar_rgb"], (0, 0, 0))

    def test_bad_style_is_refused(self):
        for data in ({"bar_color": "navy"}, {"colour": "#000000"}):
            with self.subTest(data=data):
                with self.assertRaises(self.v.CaptionError):
                    self.v.resolve_style(self.write(data), None, None, None)

    def test_the_bar_is_drawn_at_the_opacity_with_no_outline(self):
        font = ImageFont.load_default()
        style = {"text_rgb": (200, 10, 10), "bar_rgb": (10, 20, 30)}
        try:
            font = ImageFont.truetype(self.v.DEFAULT_FONT, 40)
        except OSError:
            self.skipTest("no TrueType font for sizing")
        path = os.path.join(self.tmp.name, "cue.png")
        w, h = self.v.render_cue(["hello there"], font, path, style, 0.88)
        img = Image.open(path).convert("RGBA")
        self.assertEqual(img.size, (w, h))
        self.assertEqual(img.getpixel((w // 2, 2)), (10, 20, 30, round(0.88 * 255)))
        inner = [img.getpixel((x, y)) for x in range(4, w - 4) for y in range(4, h - 4)]
        self.assertTrue(all(px[3] >= round(0.88 * 255) for px in inner))   # the bar is behind everything
        self.assertIn((200, 10, 10, 255), inner)                            # solid text colour, no stroke

    def test_a_line_wider_than_the_frame_is_refused(self):
        try:
            font = ImageFont.truetype(self.v.DEFAULT_FONT, 200)
        except OSError:
            self.skipTest("no TrueType font for sizing")
        with self.assertRaises(self.v.CaptionError):
            self.v.render_cue(["sixteen chars ok!"], font, os.path.join(self.tmp.name, "c.png"),
                              {"text_rgb": (1, 1, 1), "bar_rgb": (0, 0, 0)}, 0.88)


@unittest.skipUnless(HAVE_PIL, "Pillow missing (vertcut needs it)")
class TestSafeZone(unittest.TestCase):
    def setUp(self):
        self.v = load()
        font = next((f for f in (self.v.DEFAULT_FONT, "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
                     if os.path.isfile(f)), None)
        self.font = ImageFont.truetype(font, 66) if font else ImageFont.load_default(size=66)

    def test_cue_box_geometry_for_one_and_two_lines(self):
        v = self.v
        one = v.cue_box(["one line"], self.font, 1680)
        two = v.cue_box(["one line", "two lines"], self.font, 1680)
        self.assertEqual((one[3], two[3]), (1680, 1680))
        w1, h1, lh = v.cue_size(["one line"], self.font)[:3]
        self.assertEqual(one[2] - one[0], w1)
        self.assertEqual(one[1], 1680 - h1)
        self.assertEqual(two[3] - two[1] - (one[3] - one[1]), lh)       # the second line adds one line height
        self.assertAlmostEqual((one[0] + one[2]) / 2, v.W / 2)           # centred

    def test_default_caption_bar_sits_inside_the_bottom_band(self):
        # Records today's default (CAPTION_BOTTOM=1680 against the lowest 35 percent, from y=1248). When
        # the caption height is ruled, swap this for an assertion that the bar is safe.
        v = self.v
        self.assertEqual(v.CAPTION_BOTTOM, 1680)
        self.assertEqual(v.unsafe_sides(v.cue_box(["one line"], self.font, v.CAPTION_BOTTOM)), ["bottom"])
        self.assertEqual(v.unsafe_sides(v.cue_box(["one line"], self.font, 1240)), [])

    def test_four_part_timecodes_are_ntsc_correct(self):
        v = self.v
        self.assertAlmostEqual(v.parse_tc("00:01:00:15", 29.97), 1815 * 1001 / 30000, places=4)    # 60.5605 s
        self.assertAlmostEqual(v.parse_tc("00:01:00:12", 23.976), 1452 * 1001 / 24000, places=4)
        self.assertAlmostEqual(v.parse_tc("00:01:00:12", 25), 60.48)
        self.assertEqual(v.parse_tc("1:30", 25), 90)
        self.assertEqual(v.parse_tc("2.5", 25), 2.5)


if __name__ == "__main__":
    unittest.main()
