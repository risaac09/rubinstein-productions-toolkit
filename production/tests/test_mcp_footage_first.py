"""
Tests for the offline MCP tools notes, shotlist and watch: each is a thin call of its
command's run(), so these check the result shape (printed text and exit status), the
refusals the wrapper adds (relative paths, a git working tree, a missing argument), and one
real call of each on synthetic files. No Resolve, no media.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from rpresolve import paths  # noqa: E402
from rpresolve.mcp import server  # noqa: E402
from test_mcp_offline import call  # noqa: E402
from test_shotlist import LIST, PLAN  # noqa: E402

NOTES = {"period": 0.5, "onsets": [1.0, 2.0, 2.5, 3.0], "strength": [3.0, 3.0, 3.0, 3.0]}


class TestFootageFirstTools(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, doc):
        p = os.path.join(self.d, name)
        with open(p, "w") as f:
            json.dump(doc, f)
        return p

    def test_registered_offline_and_not_read_only(self):
        reg = server.build_registry()
        for name in ("notes", "shotlist", "watch"):
            t = reg.get(name)
            self.assertFalse(t.annotations["readOnlyHint"], name)
            self.assertIn("Never connects to Resolve", t.description)
            self.assertNotIn("project", t.input_schema["properties"])
        self.assertIn("notes, shotlist and watch never connect", server.INSTRUCTIONS)

    def test_notes_check_and_snap_return_the_printed_result_and_exit(self):
        n = self.write("n.json", NOTES)
        r = call("notes", {"action": "check", "notes": n, "cuts": "1.0,2.05,2.5,3.0", "fps": 10})
        self.assertEqual((r["exit"], r["ok"]), (0, True))
        self.assertEqual(r["output"], "cuts 4, on a note 4, chance 0.30, notes 4 at floor 1.5")
        r = call("notes", {"action": "snap", "notes": n, "beats": "1,1", "first": 1.0, "fps": 24})
        self.assertEqual(r["exit"], 0)
        self.assertIn("cut_frames", json.loads(r["output"]))

    def test_notes_refusals(self):
        n = self.write("n.json", NOTES)
        with self.assertRaisesRegex(ValueError, "absolute"):
            call("notes", {"action": "check", "notes": "n.json", "cuts": "1"})
        with self.assertRaisesRegex(ValueError, "exactly one of cuts or plan"):
            call("notes", {"action": "check", "notes": n})
        with self.assertRaisesRegex(ValueError, "snap needs first"):
            call("notes", {"action": "snap", "notes": n, "beats": "1"})
        with self.assertRaisesRegex(ValueError, "measure needs out"):
            call("notes", {"action": "measure", "audio": n})

    def test_shotlist_check_and_compile(self):
        sl = self.write("list.json", LIST)
        plan = self.write("plan.json", PLAN)
        r = call("shotlist", {"action": "check", "list": sl})
        self.assertEqual((r["exit"], r["output"]), (0, f"{sl}: 5 shots, ok"))
        out = os.path.join(self.d, "draft")
        r = call("shotlist", {"action": "compile", "list": sl, "cuts": plan, "out": out})
        self.assertEqual(r["exit"], 0, r)
        self.assertTrue(os.path.isfile(os.path.join(out, "manifest.json")))
        self.assertIn("5 spans", r["output"])

    def test_shotlist_bad_list_is_exit_1_with_the_reasons(self):
        sl = self.write("list.json", dict(LIST, fps=0))
        r = call("shotlist", {"action": "check", "list": sl})
        self.assertEqual((r["exit"], r["ok"]), (1, False))
        self.assertIn("ERROR", r["errors"])

    def test_output_inside_a_git_tree_is_refused(self):
        sl = self.write("list.json", LIST)
        repo = os.path.join(self.d, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        with self.assertRaises(paths.OutputRefused):
            call("shotlist", {"action": "compile", "list": sl, "period": 1.0,
                              "out": os.path.join(repo, "out")})

    def test_watch_status_says_why_not(self):
        r = call("watch", {"action": "status", "dir": self.d})
        self.assertEqual(r["exit"], 1)
        self.assertTrue(r["output"].startswith("not acked: "))
        with self.assertRaisesRegex(ValueError, "absolute"):
            call("watch", {"action": "status", "dir": "rel"})
        with self.assertRaisesRegex(ValueError, "ack needs notes"):
            call("watch", {"action": "ack", "dir": self.d})


if __name__ == "__main__":
    unittest.main()
