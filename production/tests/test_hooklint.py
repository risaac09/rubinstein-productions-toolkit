"""
Offline tests for rpresolve.hooklint. Pure Python, no media, no Resolve.

Run: /usr/bin/python3 -m unittest production.tests.test_hooklint -v
"""

import re
import subprocess
import sys
import unittest
from pathlib import Path

PROD = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROD))

from rpresolve import hooklint  # noqa: E402

TALL, SQUARE = (1080, 1920), (1080, 1080)


def rows(lines, size=TALL, **kw):
    return {n: s for n, s, _ in hooklint.lint(lines, *size, **kw)}


class HookLint(unittest.TestCase):
    def test_clean_passes(self):
        r = rows(["Plain words here", "second line"], hold=1.8, start=0)
        self.assertEqual(r.pop("lexicon"), "SKIP")
        self.assertEqual(set(r.values()), {"PASS"})

    def test_ten_words_fail(self):
        self.assertEqual(rows(["a b c d e", "f g h i j"])["words"], "FAIL")
        self.assertEqual(rows(["a b c d e", "f g h i"])["words"], "PASS")

    def test_em_dash_fails(self):
        self.assertEqual(rows(["wait " + chr(0x2014) + " what"])["dash"], "FAIL")

    def test_emoji_fails(self):
        self.assertEqual(rows(["go \U0001F525"])["emoji"], "FAIL")

    def test_line_limit_depends_on_shape(self):
        line = "x" * 17
        self.assertEqual(rows([line], TALL)["lines"], "FAIL")
        self.assertEqual(rows([line], SQUARE)["lines"], "PASS")
        self.assertEqual(rows(["x" * 25], SQUARE)["lines"], "FAIL")
        self.assertEqual(rows(["a", "b", "c"])["lines"], "FAIL")

    def test_hold_floor_and_per_word(self):
        self.assertEqual(rows(["one two"], hold=0.7)["hold"], "FAIL")
        self.assertEqual(rows(["one two"], hold=0.8)["hold"], "PASS")
        self.assertEqual(rows(["a b c d e f g h"], hold=2.3)["hold"], "FAIL")   # 8 words need 2.4 s
        self.assertEqual(rows(["a b c d e f g h"], hold=2.4)["hold"], "PASS")
        self.assertEqual(rows(["one"])["hold"], "SKIP")

    def test_start_must_be_zero(self):
        self.assertEqual(rows(["one"], start=0.5)["start"], "FAIL")
        self.assertEqual(rows(["one"], start=0)["start"], "PASS")

    def test_lexicon_only_when_configured(self):
        self.assertEqual(rows(["one two"])["lexicon"], "SKIP")
        self.assertEqual(rows(["one two"], lexicon=[" "])["lexicon"], "SKIP")
        self.assertEqual(rows(["one two"], lexicon=["TWO"])["lexicon"], "FAIL")
        self.assertEqual(rows(["one two"], lexicon=["three"])["lexicon"], "PASS")

    def test_edges(self):
        self.assertEqual(rows(["done \u2713 \u2192 next"])["emoji"], "PASS")
        self.assertEqual(rows(["go \u23F0"])["emoji"], "FAIL")
        self.assertEqual(rows(["abcdefghij\nklmnopqrstuvwxyzz"])["lines"], "FAIL")   # split on newline
        self.assertEqual(rows(["wait - what"])["words"], "PASS")
        self.assertEqual(rows([])["lines"], "FAIL")
        self.assertEqual(rows(["a class act"], lexicon=["ass"])["lexicon"], "PASS")

    def test_cli_exit_codes(self):
        def run(*a):
            return subprocess.run([sys.executable, "-m", "rpresolve.hooklint", *a], cwd=PROD,
                                  capture_output=True, text=True).returncode
        self.assertEqual(run("--line", "short", "--size", "1080x1920"), 0)
        self.assertEqual(run("--line", "x" * 17, "--size", "1080x1920"), 1)
        self.assertEqual(run("--line", "x", "--size", "bad"), 2)
        self.assertEqual(run("--line", "x", "--size", "0x0"), 2)
        self.assertEqual(run("--line", "x", "--size", "1080x1920", "--hold", "nan"), 2)

    def test_no_text_generation_path(self):
        body = (PROD / "rpresolve" / "hooklint.py").read_text().split('"""', 2)[2]
        self.assertIsNone(re.search(r"anthropic|openai|ollama|urllib|requests|subprocess|random", body))


if __name__ == "__main__":
    unittest.main()
