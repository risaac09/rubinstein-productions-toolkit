"""
Tests for `project` (rpresolve/project.py): the resumable chain from a card to the Resolve
step, on a synthetic card folder. The card commands run for real; offload.sh, detect, the
indexer and sheets are stubbed, so the test shows the order, what stops the chain, what a dry
run writes (nothing), and that a second run resumes.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rpresolve import project  # noqa: E402


class Chain(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp(prefix="project-test-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.card = os.path.join(self.dir, "land", "CAMA-000000")
        os.makedirs(os.path.join(self.card, "DCIM"))
        for n in ("A001.mov", "A002.mov"):
            Path(self.card, "DCIM", n).write_bytes(b"x" * 10)
        real = project.run_cmd
        real([sys.executable, "-m", "rpresolve.cmd_card", "init", "--retrofit", self.card,
              "--cam", "CAMA", "--apply"])
        self.calls = []
        self.cfg = project.load_config(None, {
            "work_root": os.path.join(self.dir, "work"), "sheets_root": os.path.join(self.dir, "sheets"),
            "indexer": "indexer-bin --flag"})
        os.makedirs(self.cfg["work_root"])
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(project, "RUN", self.fake).start()

    def record(self):
        with open(self.card + ".card.json", encoding="utf-8") as f:
            return json.load(f)

    def fake(self, argv, **kw):
        self.calls.append(argv)
        if "rpresolve.cmd_card" in argv:
            return project.run_cmd(argv, **kw)
        if "rpresolve.cmd_detect" in argv:
            return 2, json.dumps([{"path": "/x/A001.mov", "profile": "ok"},
                                  {"path": "/x/A002.mov", "profile": "review"}]), ""
        if "rpresolve.cmd_sheets" in argv:
            return 0, "6 cell(s) from 2 clip(s) on 1 sheet(s)", ""
        return 0, "", ""

    def go(self, *a, **kw):
        out = []
        rc = project.start(self.cfg, self.card, kw.pop("slug", "demo"), say=out.append, **kw)
        return rc, "\n".join(out)

    def ran(self, name):
        return [c for c in self.calls if any(name in a for a in c)]

    def test_dry_run_writes_nothing_and_stops_for_the_squeeze_answer(self):
        rc, out = self.go()
        self.assertEqual(rc, 2)
        self.assertIn("needs a squeeze answer for 2 clip(s)", out)
        self.assertFalse(os.path.exists(os.path.join(self.cfg["work_root"], "demo")))
        self.assertFalse(os.path.exists(self.cfg["sheets_root"]))
        self.assertIn("would run:", out)
        self.assertEqual(self.ran("indexer-bin"), [])  # stopped before the index
        self.assertIsNone(self.record().get("project"))

    def test_apply_then_answer_then_resume_to_the_resolve_step(self):
        rc, out = self.go(apply=True)
        self.assertEqual(rc, 2)
        with open(os.path.join(self.cfg["work_root"], "demo", "project.json"), encoding="utf-8") as f:
            st = json.load(f)
        self.assertEqual(st["steps"]["ask"]["status"], "waiting")
        self.assertEqual(len(st["open"]), 3)  # two squeeze, one detect review
        sheets = self.ran("rpresolve.cmd_sheets")[0]
        self.assertEqual(sheets.count("--only"), 2)
        self.assertEqual(self.record()["project"], "demo")
        self.assertEqual(project.answer(self.cfg, "demo", True, 1.0, (), False, say=lambda m: None), 0)
        rc, out = self.go(apply=True)
        self.assertEqual(rc, 2)
        self.assertIn("needs a person to create the Resolve project", out)
        self.assertIn("managed-dwg", out)
        self.assertEqual(self.ran("indexer-bin")[0][-3:], ["index", "--card", self.card])
        self.assertEqual(len(self.ran("rpresolve.cmd_detect")), 1)  # identify is done: not run again
        project.answer(self.cfg, "demo", True, resolved=True, say=lambda m: None)
        rc, out = self.go(apply=True)
        self.assertEqual(rc, 0)
        self.assertIn("steps 7 to 9 are by hand", out)
        lines = []
        project.status(self.cfg, "demo", say=lines.append)
        text = "\n".join(lines)
        self.assertIn("6. Resolve project colour: done", text)
        self.assertIn("7. shot list, cut times, drafts, watch, pick: pending", text)
        self.assertIn("next: write the shot list", text)
        self.assertIn("no, no offsite.txt names it", text)

    def test_without_an_indexer_or_sheets_root_the_steps_are_skipped(self):
        self.cfg.update(indexer=None, sheets_root=None)
        subprocess.run([sys.executable, "-m", "rpresolve.cmd_card", "set", self.card, "--squeeze", "1",
                        "--apply"], cwd=project.PRODUCTION, check=True, capture_output=True)
        rc, out = self.go(apply=True)
        self.assertIn("no indexer configured: skipped", out)
        self.assertIn("no sheets root: skipped", out)

    def test_a_card_volume_needs_cam_and_a_dry_run_stops_after_naming_the_copy(self):
        vol = os.path.join(self.dir, "vol")
        os.makedirs(vol)
        self.cfg["landing_root"] = os.path.join(self.dir, "land")

        def fake(argv, **kw):
            self.calls.append(argv)
            return 0, "card_id 0123456789ab\nfiles 1\n", ""
        mock.patch.object(project, "RUN", fake).start()
        out = []
        self.assertEqual(project.start(self.cfg, vol, "demo", say=out.append), 2)
        self.assertIn("needs --cam", "\n".join(out))
        out = []
        self.assertEqual(project.start(self.cfg, vol, "demo", cam="CAMA", say=out.append), 2)
        self.assertIn("would run:", "\n".join(out))
        self.assertIn("CAMA-012345", "\n".join(out))
        self.assertEqual(self.ran("copy"), [])

    def test_refusals(self):
        with self.assertRaisesRegex(project.ProjectError, "needs a project slug"):
            project.start(self.cfg, self.card, None, say=print)
        with self.assertRaisesRegex(project.ProjectError, "a project is a slug"):
            project.start(self.cfg, self.card, "Bad Slug", say=print)
        with self.assertRaisesRegex(project.ProjectError, "no work root"):
            project.start(project.load_config(None, {}), self.card, "demo", say=print)

    def test_status_of_an_unknown_project_is_refused(self):
        with self.assertRaisesRegex(project.ProjectError, "run project start first"):
            project.status(self.cfg, "nothing", say=print)

    def test_config_file(self):
        p = os.path.join(self.dir, "c.json")
        Path(p).write_text(json.dumps({"work_root": "/w", "indexer": {"argv": ["~/x", "-m", "y"], "env": {"P": "~/z"}}}))
        cfg = project.load_config(p, {"sheets_root": "/s"})
        self.assertEqual((cfg["work_root"], cfg["sheets_root"], cfg["landing_root"]), ("/w", "/s", None))
        argv, env = project.indexer_command(cfg)
        self.assertEqual(argv[1:], ["-m", "y"])
        self.assertTrue(argv[0].startswith("/") and env["P"].startswith("/"))
        Path(p).write_text(json.dumps({"work_roots": "/w"}))
        with self.assertRaisesRegex(project.ProjectError, "unknown key"):
            project.load_config(p)

    def test_the_example_config_loads(self):
        cfg = project.load_config(str(HERE.parent / "project.example.json"))
        self.assertTrue(cfg["work_root"])


if __name__ == "__main__":
    unittest.main()
