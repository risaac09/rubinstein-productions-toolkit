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
        self.calls, self.ticks = [], []
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
        rc = project.start(self.cfg, kw.pop("card", self.card), kw.pop("slug", "demo"), say=out.append,
                           tick=self.ticks.append, **kw)
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
        self.assertIn("the shot list is by hand; then project build", out)
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

    def card_in_work(self, folder):
        card = os.path.join(self.cfg["work_root"], folder, "CAMA-000000")
        os.makedirs(os.path.join(card, "DCIM"))
        Path(card, "DCIM", "B001.mov").write_bytes(b"y" * 10)
        project.run_cmd([sys.executable, "-m", "rpresolve.cmd_card", "init", "--retrofit", card,
                         "--cam", "CAMA", "--apply"])
        return card

    def test_the_work_folder_is_the_project(self):
        card = self.card_in_work("oslo-first-snow")
        rc, out = self.go(card=card, slug=None)
        self.assertIn("project oslo-first-snow (dry run)", out)
        with self.assertRaisesRegex(project.ProjectError,
                                    r"in the work folder 'oslo-first-snow' but --project is 'other'"):
            self.go(card=card, slug="other")
        rc, out = self.go(card=card, slug="other", new_project=True)
        self.assertIn("project other (dry run)", out)
        self.go(card=card, slug="oslo-first-snow")  # the same slug is fine
        self.assertEqual(project.inside_work_root(self.cfg, self.card), None)  # the landing root is not
        self.assertEqual(project.inside_work_root(self.cfg, self.cfg["work_root"]), None)

    def test_a_card_outside_the_work_root_still_needs_the_slug(self):
        with self.assertRaisesRegex(project.ProjectError, "needs a project slug"):
            self.go(slug=None)

    def test_progress_lines_count_clips_and_beat_while_a_command_runs(self):
        with mock.patch.object(project, "BATCH", 1):
            self.go()
        self.assertIn("  detect: 1 of 2 clips", self.ticks)
        self.assertIn("  detect: 2 of 2 clips", self.ticks)
        slow = mock.Mock(side_effect=lambda argv, **kw: (__import__("time").sleep(0.35), (0, "", ""))[1])
        with mock.patch.object(project, "RUN", slow), mock.patch.object(project, "HEARTBEAT", 0.1):
            ch = project.Chain(self.cfg, "demo", True, tick=self.ticks.append)
            ch.cmd(["x", "-m", "rpresolve.cmd_sheets"], writes=True)
        self.assertTrue(any("cmd_sheets: still working" in t for t in self.ticks))

    def test_a_second_start_does_not_repeat_card_set_or_detect(self):
        self.go(apply=True)
        sets = len(self.ran("rpresolve.cmd_card"))
        detects = len(self.ran("rpresolve.cmd_detect"))
        self.go(apply=True)
        self.assertEqual((len(self.ran("rpresolve.cmd_card")), len(self.ran("rpresolve.cmd_detect"))),
                         (sets, detects))

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


class Build(unittest.TestCase):
    """The last stretch: three recorded answers, then one build that calls each tool in order."""

    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp(prefix="project-build-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.cfg = project.load_config(None, {"work_root": self.dir})
        self.manifest = os.path.join(self.dir, "m.json")
        Path(self.manifest).write_text(json.dumps({"effects": [
            {"span_index": 3, "effect": "zoom-push", "params": {"x": 1}}]}))
        self.calls, self.exist = [], False
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(project, "CALL", self.fake).start()
        mock.patch.object(project, "OPEN_NAME", lambda: "Demo Project").start()
        mock.patch.object(project, "_SESSION", object()).start()
        self.out = []

    def fake(self, tool, args):
        self.calls.append((tool, args))
        if tool != "assemble_timeline" and not (self.exist or any(c[0] == "assemble_timeline" and not c[1]["dry_run"]
                                                                   for c in self.calls)):
            raise ValueError("no such timeline")
        if args["dry_run"] is False and args.get("plan_sha") != "sha-" + tool:
            raise AssertionError("real run without its own plan_sha")
        r = {"summary": f"{tool} summary", "plan_sha": "sha-" + tool, "results": [{"ok": True}]}
        if tool == "assemble_timeline":
            r.update(timeline=args["name"], plan={"track": 2}, problems=[])
        if tool == "queue_render":
            r.update(job={"JobId": "j1"}, exit_status=0)
        return r

    def answer(self, **kw):
        project.answer(self.cfg, "demo", True, say=self.out.append, **kw)

    def answers(self):
        self.answer(shape="3840x1608")
        self.answer(look="/luts/look.cube")
        self.answer(destination="client_master", name_parts=["client=Acme", "slug=demo-cut"])

    def build(self, apply=False):
        self.out.clear()
        project.build(self.cfg, "demo", self.manifest, apply=apply, say=self.out.append)
        return "\n".join(self.out)

    def test_refuses_naming_what_is_missing(self):
        self.answer(shape="3840x1608")
        with self.assertRaisesRegex(project.ProjectError, "look.*destination"):
            self.build()
        self.assertEqual(self.calls, [])

    def test_an_answer_is_recorded_once_with_who_and_when(self):
        self.answers()
        st = json.load(open(os.path.join(self.dir, "demo", "project.json")))
        self.assertEqual(st["answers"]["shape"]["width"], 3840)
        self.assertEqual(st["answers"]["destination"]["name"], {"client": "Acme", "slug": "demo-cut"})
        self.assertTrue(st["answers"]["look"]["by"] and st["answers"]["look"]["at"])
        with self.assertRaisesRegex(project.ProjectError, "already recorded"):
            self.answer(shape="1920x1080")

    def test_status_lists_open_answers(self):
        self.answer(shape="3840x1608")
        out = []
        project.status(self.cfg, "demo", say=out.append)
        text = "\n".join(out)
        self.assertIn("open: look", text)
        self.assertIn("open: destination", text)
        self.assertNotIn("open: delivery shape", text)

    def test_dry_run_prints_one_plan_and_writes_nothing(self):
        self.answers()
        before = open(os.path.join(self.dir, "demo", "project.json")).read()
        text = self.build()
        self.assertIn("assemble_timeline summary", text)
        self.assertIn("not checkable until 'demo [auto]' is built", text)
        self.assertTrue(all(c[1]["dry_run"] for c in self.calls))
        self.assertEqual(open(os.path.join(self.dir, "demo", "project.json")).read(), before)

    def test_apply_runs_in_order_each_with_its_own_sha_and_records_the_job(self):
        self.answers()
        self.build(apply=True)
        real = [(t, a) for t, a in self.calls if not a["dry_run"]]
        self.assertEqual([t for t, _ in real], ["assemble_timeline", "apply_effect", "apply_grade",
                                                "apply_grade", "queue_render"])
        self.assertEqual(real[0][1]["width"], 3840)
        self.assertEqual(real[1][1]["spans"], [3])
        self.assertEqual([a["track"] for t, a in real if t == "apply_grade"], [1, 2])
        self.assertEqual(real[-1][1]["name"], {"client": "Acme", "slug": "demo-cut"})
        out = []
        project.status(self.cfg, "demo", say=out.append)
        self.assertTrue(any(l.startswith("9. ") and "queued" in l and "waiting for a person" in l for l in out))

    def test_first_failure_stops(self):
        self.answers()
        orig = self.fake

        def boom(tool, args):
            if tool == "apply_effect":
                raise ValueError("refused")
            return orig(tool, args)
        mock.patch.object(project, "CALL", boom).start()
        with self.assertRaisesRegex(project.ProjectError, "apply_effect: refused"):
            self.build(apply=True)
        self.assertNotIn("queue_render", [c[0] for c in self.calls])


if __name__ == "__main__":
    unittest.main()
