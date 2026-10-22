"""
Tests for rpresolve.captionlint on synthetic SubRip and TTML text.

Run: python3 -m unittest production.tests.test_captionlint -v
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import captionlint as cl  # noqa: E402

CLEAN = """1
00:00:01,000 --> 00:00:04,000
A short line.

2
00:00:04,500 --> 00:00:07,000
<i>Another</i> short line.
"""

BAD = """1
00:00:00,000 --> 00:00:01,000
This line runs on well past the forty-two character mark.

2
00:00:00,900 --> 00:00:02,000
one
two
three

3
00:00:02,050 --> 00:00:02,500 X1:10 X2:1900 Y1:900 Y2:1000
Far too many characters for half a second.
"""

TTML = """<?xml version="1.0" encoding="UTF-8"?>
<tt xmlns="http://www.w3.org/ns/ttml" xmlns:tts="http://www.w3.org/ns/ttml#styling"
    tts:extent="1920px 1080px">
 <head><layout>
  <region xml:id="low" tts:origin="10% 80%" tts:extent="80% 10%"/>
  <region xml:id="edge" tts:origin="0px 1000px" tts:extent="1920px 80px"/>
 </layout></head>
 <body><div>
  <p begin="00:00:01.000" end="00:00:03.000" region="low">Inside title safe.</p>
  <p begin="00:00:03.500" end="00:00:05.000" region="edge">On the edge.</p>
 </div></body>
</tt>
"""


class CaptionLintTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def lint(self, text, ext=".srt", size=None, th=None):
        path = os.path.join(self.tmp.name, "c" + ext)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        r = cl.lint(cl.load(path, size), th)
        return r, {row["check"]: row for row in r["checks"]}

    def test_clean_file_passes_and_markup_is_not_counted(self):
        r, by = self.lint(CLEAN)
        self.assertEqual(r["status"], "pass")
        self.assertEqual(by["safe_zone"]["status"], cl.SKIP)
        self.assertEqual(cl.measure("<i>Another</i> {\\an8}line\nx"), (["Another line", "x"], 13))

    def test_each_rule_fires(self):
        r, by = self.lint(BAD, size=(1920, 1080))
        self.assertEqual(r["status"], "fail")
        self.assertEqual(by["line_length"]["status"], cl.FAIL)
        self.assertEqual(by["lines_per_cue"]["status"], cl.FAIL)
        self.assertEqual(by["overlap"]["status"], cl.FAIL)
        self.assertEqual(by["gap"]["status"], cl.WARN)        # 0.05 s after cue 2
        self.assertEqual(by["reading_speed"]["status"], cl.WARN)
        self.assertEqual(by["safe_zone"]["status"], cl.FAIL)  # x from 0.5%
        self.assertEqual(r["checks"][0]["status"], cl.FAIL)   # FAIL rows first

    def test_two_frame_gap_at_30_fps_passes(self):
        _, by = self.lint(CLEAN.replace("00:00:04,500", "00:00:04,067"))
        self.assertEqual(by["gap"]["status"], cl.PASS)
        _, by = self.lint(CLEAN.replace("00:00:04,500", "00:00:04,040"))
        self.assertEqual(by["gap"]["status"], cl.WARN)

    def test_pixel_positions_need_a_size(self):
        _, by = self.lint(BAD)
        self.assertEqual(by["safe_zone"]["status"], cl.SKIP)
        self.assertIn("--size", by["safe_zone"]["note"])

    def test_threshold_override(self):
        th = cl.qc.parse_overrides(["max_chars=10"], base=cl.THRESHOLDS)
        _, by = self.lint(CLEAN, th=th)
        self.assertEqual(by["line_length"]["status"], cl.FAIL)
        with self.assertRaises(ValueError):
            cl.qc.parse_overrides(["black_min_s=1"], base=cl.THRESHOLDS)

    def test_ttml_regions(self):
        r, by = self.lint(TTML, ext=".ttml")
        self.assertEqual(r["cues"], 2)
        self.assertEqual(by["safe_zone"]["status"], cl.FAIL)
        self.assertIn("region edge", by["safe_zone"]["note"])
        self.assertNotIn("region low", by["safe_zone"]["note"])

    def test_unreadable_srt_is_a_parse_fail(self):
        r, by = self.lint("not a caption\n")
        self.assertEqual(by["parse"]["status"], cl.FAIL)
        self.assertEqual(by["line_length"]["status"], cl.SKIP)

    def test_cli_exit_codes(self):
        good, vtt = (os.path.join(self.tmp.name, n) for n in ("good.srt", "c.vtt"))
        for path in (good, vtt):
            with open(path, "w", encoding="utf-8") as f:
                f.write(CLEAN)
        with open(os.devnull, "w") as null:
            out, err, sys.stdout, sys.stderr = sys.stdout, sys.stderr, null, null
            try:
                self.assertEqual(cl.main([good]), 0)
                self.assertEqual(cl.main([good, "--set", "max_chars=5"]), 1)
                self.assertEqual(cl.main([vtt]), 2)  # not .srt or TTML
            finally:
                sys.stdout, sys.stderr = out, err


if __name__ == "__main__":
    unittest.main()
