"""
taste-profile distiller on synthetic events only.

Run: /usr/bin/python3 -m unittest -v production.tests.test_taste_distill
"""

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rpresolve import cmd_taste_profile, tastedistill as td  # noqa: E402


def ev(choice="A", step="look", opts=None, **kw):
    e = {"ts": "2026-01-01T00:00:00Z", "project": "P-TEST", "step": step,
         "options": opts or {"A": "warm", "B": "cool"}, "choice": choice,
         "reason": None, "device": "desktop"}
    e.update(kw)
    return e


def distill(events):
    return td.distill(list(enumerate(events, 1)))


class DistillTests(unittest.TestCase):
    def test_under_five_no_rule(self):
        self.assertEqual(distill([ev("A")] * 4)["look"]["verdict"], "not yet a rule")

    def test_five_consistent_rule(self):
        r = distill([ev("A")] * 5)["look"]
        self.assertEqual(r["verdict"], "rule")
        self.assertEqual(r["direction"], "warm")
        self.assertEqual(r["exemplars"], [1, 2, 3])

    def test_contradiction_listed(self):
        r = distill([ev("A")] * 9 + [ev("B")])["look"]
        self.assertEqual(r["verdict"], "rule")
        self.assertEqual(r["contradictions"], [10])

    def test_label_order_does_not_matter(self):
        flipped = ev("B", opts={"A": "cool", "B": "warm"})
        self.assertEqual(distill([ev("A")] * 3 + [flipped] * 2)["look"]["direction"], "warm")

    def test_repeat_pairs_below_80_block(self):
        r = distill([ev("A")] * 5 + [ev("B")] * 3)["look"]
        self.assertEqual(r["verdict"], "inconsistent, no rule")
        self.assertEqual((r["agree"], r["pairs"]), (13, 28))

    def test_repeat_pairs_at_80_pass(self):
        r = distill([ev("A")] * 9 + [ev("B")])["look"]  # 36 of 45 pairs agree
        self.assertEqual(r["verdict"], "rule")

    def test_ratings_have_no_repeat_group(self):
        events = [ev(c, step="grade") for c in (5, 5, 5, 5, 5, 4, 3)]
        for e in events:
            e["options"] = {}
        r = distill(events)["grade"]
        self.assertEqual((r["verdict"], r["pairs"]), ("rule", 0))

    def test_plurality_without_majority_no_rule(self):
        opts = {"A": "a", "B": "b", "C": "c", "D": "d", "E": "e"}
        events = [ev("A", opts=opts, stimulus=str(i)) for i in range(5)] + \
                 [ev(c, opts=opts, stimulus=c + "x") for c in "BCDE" for _ in range(4)]
        self.assertEqual(distill(events)["look"]["verdict"], "no majority, no rule")

    def test_stimulus_key_groups_repeats(self):
        events = [ev("A", stimulus="s1")] * 5 + [ev("B", stimulus="s1")] * 3
        self.assertEqual(distill(events)["look"]["verdict"], "inconsistent, no rule")
        events = [ev("A", stimulus=str(i)) for i in range(7)] + [ev("B", stimulus="z")]
        self.assertEqual(distill(events)["look"]["verdict"], "rule")

    def test_noise_types_not_pooled(self):
        events = [ev("A", step="sound", noise_type="rumble")] * 3 + \
                 [ev("A", step="sound", noise_type="wind")] * 3
        res = distill(events)
        self.assertEqual(set(res), {"sound [rumble]", "sound [wind]"})
        self.assertTrue(all(r["verdict"] == "not yet a rule" for r in res.values()))

    def test_line_only_answer_not_counted(self):
        self.assertEqual(distill([ev(None)] * 6), {})

    def test_never_list(self):
        opts = {"A": "warm", "B": "cool", "C": "green"}
        self.assertEqual(distill([ev("A", opts=opts)] * 5)["look"]["never"], ["cool", "green"])


class FileTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = os.path.realpath(tmp.name)
        self.log = os.path.join(self.dir, "events.jsonl")

    def write(self, lines):
        Path(self.log).write_text("\n".join(lines) + "\n")

    def run_cmd(self, *argv):
        ap = argparse.ArgumentParser()
        cmd_taste_profile.add_arguments(ap)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cmd_taste_profile.run(ap.parse_args(argv))
        return code, out.getvalue()

    def test_malformed_counted_not_fatal(self):
        self.write([json.dumps(ev("A")), "{not json", "[1,2]",
                    json.dumps({"options": "x"}), json.dumps(ev("Z")), json.dumps(ev("A"))])
        text = td.build(self.log)
        self.assertIn("Read 2 answers; 4 malformed lines skipped", text)
        self.assertIn("orders options and never picks", text)

    def test_dry_run_prints_and_writes_nothing(self):
        self.write([json.dumps(ev("A"))] * 5)
        code, out = self.run_cmd(self.log)
        self.assertEqual(code, 0)
        self.assertIn("Rule: prefers warm", out)
        self.assertEqual(os.listdir(self.dir), ["events.jsonl"])

    def test_out_outside_git_writes(self):
        self.write([json.dumps(ev("A"))] * 5)
        dest = os.path.join(self.dir, "taste-profile.md")
        self.assertEqual(self.run_cmd(self.log, "--out", dest)[0], 0)
        self.assertIn("# Taste profile", Path(dest).read_text())

    def test_out_inside_git_tree_refused(self):
        self.write([json.dumps(ev("A"))] * 5)
        repo = os.path.join(self.dir, "repo")
        os.mkdir(repo)
        subprocess.run(["git", "init", "-q", repo], check=True)
        dest = os.path.join(repo, "taste-profile.md")
        code, out = self.run_cmd(self.log, "--out", dest)
        self.assertEqual(code, 1)
        self.assertIn("git working tree", out)
        self.assertFalse(os.path.exists(dest))


if __name__ == "__main__":
    unittest.main()
