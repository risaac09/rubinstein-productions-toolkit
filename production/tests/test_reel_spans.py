"""
reel-spans on a synthetic word-timed transcript (placeholder tokens, no speech, no media).

Run: /usr/bin/python3 -m unittest -v production.tests.test_reel_spans
"""

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rpresolve import cli, reelspans  # noqa: E402

LIB = HERE.parents[1] / "tools" / "vertcut" / "lib"
try:
    import PIL  # noqa: F401  (vertcut_cut needs it)
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


def load(name):
    sys.path.insert(0, str(LIB))
    spec = importlib.util.spec_from_file_location(name, LIB / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def transcript(n=240):
    """Words wNN each 0.5 s long, one every 0.5 s; a full stop after every 10th word.
    The two-word run 'w5 w6' is repeated at word 105 so it occurs twice."""
    out = []
    for i in range(n):
        text = "w%d" % {104: 5, 105: 6}.get(i, i + 1)
        if (i + 1) % 10 == 0:
            text += "."
        out.append({"word": " " + text, "start": i * 0.5, "end": i * 0.5 + 0.5})
    return out


class ReelSpansTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.words = transcript()
        self.words_path = self.dir / "words.json"
        self.words_path.write_text(json.dumps({"segments": [{"words": self.words}]}))

    def one(self, start, end, **extra):
        row = dict({"title": "T", "start": start, "end": end}, **extra)
        return reelspans.locate(self.words, [row])[0]

    def test_exact_match_snaps_to_word_edges(self):
        # words 20..29 are w21..w30. (0-based index 20 starts at 10.0 s; index 29 ends at 15.0 s)
        r = self.one("w21 w22", "w29 w30.")
        self.assertEqual((r["in"], r["out"]), (10.0, 15.0))
        self.assertEqual((r["fails"], r["warns"]), ([], []))

    def test_case_and_punctuation_insensitive(self):
        r = self.one("W21, w22!", '"W29  W30"')
        self.assertEqual((r["in"], r["out"]), (10.0, 15.0))
        self.assertEqual(r["fails"], [])

    def test_not_found_gives_hint_and_substitutes_nothing(self):
        r = self.one("w21 w23", "w29 w30.")
        self.assertIsNone(r["in"])
        self.assertTrue(any(m.startswith("start phrase not found; nearest text:") and " at " in m for m in r["fails"]))

    def test_duplicate_phrase_lists_each_time(self):
        r = self.one("w5 w6", "w19 w20.")
        msg = [m for m in r["fails"] if m.startswith("start phrase found 2 times")]
        self.assertEqual(len(msg), 1)
        self.assertIn("2.00 s", msg[0])
        self.assertIn("52.00 s", msg[0])

    def test_occurrence_index_picks_one(self):
        first = self.one("w5 w6", "w109 w110.", start_occurrence=1)
        self.assertEqual(first["in"], 2.0)
        second = self.one("w5 w6", "w109 w110.", start_occurrence=2)
        self.assertEqual(second["in"], 52.0)
        past = self.one("w5 w6", "w109 w110.", start_occurrence=3)
        self.assertTrue(any("occurrence 3" in m for m in past["fails"]))

    def test_overlapping_repeat_counts_once(self):
        words = [{"word": "no", "start": i, "end": i + 0.5} for i in range(3)]
        _, hits = reelspans.find(reelspans.flatten(words), "no no")
        self.assertEqual(len(hits), 1)

    def test_reversed_span_fails(self):
        r = self.one("w41 w42", "w21 w22")
        self.assertIsNone(r["out"])
        self.assertIn("end phrase comes before the start phrase", r["fails"])

    def test_length_warns(self):
        long_ = self.one("w1 w2", "w199 w200.")
        self.assertEqual(long_["fails"], [])
        self.assertTrue(any("over 90 s" in m for m in long_["warns"]))
        short = self.one("w21 w22", "w23 w24")
        self.assertTrue(any("under 5 s" in m for m in short["warns"]))
        loose = reelspans.locate(self.words, [{"title": "T", "start": "w21 w22", "end": "w23 w24"}],
                                 min_s=1.0)[0]
        self.assertFalse(any("under" in m for m in loose["warns"]))

    def test_mid_sentence_warn(self):
        r = self.one("w21 w22", "w27 w28")
        self.assertTrue(any("mid-sentence" in m for m in r["warns"]))
        self.assertEqual(self.one("w21 w22", "w29 w30.")["warns"], [])

    def test_tsv_rows_and_header(self):
        tsv = self.dir / "p.tsv"
        tsv.write_text("title\tstart\tend\n# note\nA\tw21 w22\tw29 w30.\nB\tw5 w6\tw19 w20.\t2\n")
        rows = reelspans.read_rows(tsv)
        self.assertEqual([r["title"] for r in rows], ["A", "B"])
        self.assertEqual(rows[1]["start_occurrence"], 2)
        tsv.write_text('#3 pivot\t"w21 w22"\t"w29 w30."\n', encoding="utf-8")
        self.assertEqual(reelspans.read_rows(tsv)[0]["start"], '"w21 w22"')
        tsv.write_text("A\tonly start\n")
        with self.assertRaises(ValueError):
            reelspans.read_rows(tsv)

    def _run(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = cli.main(["reel-spans", *map(str, argv)])
        return status, out.getvalue(), err.getvalue()

    def test_fail_row_left_out_and_exit_1(self):
        tsv = self.dir / "p.tsv"
        tsv.write_text("A\tw21 w22\tw29 w30.\nB\tnope nope\tw29 w30.\n")
        out = self.dir / "cuts.tsv"
        status, text, _ = self._run(self.words_path, tsv, "-o", out)
        self.assertEqual(status, 1)
        self.assertIn("FAIL r02", text)
        self.assertEqual(out.read_text(), "r01\t10.000\t15.000\tA\n")

    def test_refuses_git_tree(self):
        tsv = self.dir / "p.tsv"
        tsv.write_text("A\tw21 w22\tw29 w30.\n")
        status, _, err = self._run(self.words_path, tsv, "-o", HERE / "cuts-should-not-exist.tsv")
        self.assertEqual(status, 2)
        self.assertIn("git working tree", err)
        self.assertFalse((HERE / "cuts-should-not-exist.tsv").exists())

    @unittest.skipUnless(HAVE_PIL, "Pillow missing (vertcut needs it)")
    def test_round_trip_through_read_manifest_and_vertcut_manifest(self):
        tsv = self.dir / "p.tsv"
        tsv.write_text("A\tw21 w22\tw29 w30.\nB\tw41 w42\tw59 w60.\n")
        out = self.dir / "cuts.tsv"
        status, _, _ = self._run(self.words_path, tsv, "-o", out)
        self.assertEqual(status, 0)
        vc = load("vertcut_cut")
        rows = vc.read_manifest(str(out), 25.0)
        self.assertEqual([(r["id"], r["start"], r["end"], r["title"]) for r in rows],
                         [("r01", 10.0, 15.0, "A"), ("r02", 20.0, 30.0, "B")])
        src = self.dir / "src.mov"
        src.write_bytes(b"placeholder")  # only hashed
        vm = load("vertcut_manifest")
        m = vm.build(str(src), str(out), str(self.words_path), 25.0)
        self.assertEqual([c["name"] for c in m["clips"]], ["r01", "r02"])
        self.assertEqual(m["clips"][0]["spans"][0]["in"], 10.0)


if __name__ == "__main__":
    unittest.main()
