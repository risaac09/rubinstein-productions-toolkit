"""
Tests for `project` (rpresolve/project.py): the resumable chain from a card to the Resolve
step, on a synthetic card folder. The card commands run for real; offload.sh, detect, the
indexer and sheets are stubbed, so the test shows the order, what stops the chain, what a dry
run writes (nothing), and that a second run resumes.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import contextlib
import datetime
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
        # load_config(None, ...) reads $RP_PIPELINE_CONFIG: a machine that has one set (work_root,
        # indexer) must not change what these tests see.
        env = mock.patch.dict(os.environ)
        env.start()
        os.environ.pop(project.CONFIG_ENV, None)
        self.addCleanup(env.stop)
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

    def state(self):
        with open(os.path.join(self.cfg["work_root"], "demo", "project.json"), encoding="utf-8") as f:
            return json.load(f)

    def fake(self, argv, **kw):
        self.calls.append(argv)
        if "rpresolve.cmd_card" in argv:
            return project.run_cmd(argv, **kw)
        if "rpresolve.cmd_detect" in argv:
            return 2, json.dumps([{"path": os.path.join(self.card, "DCIM", "A001.mov"), "profile": "ok"},
                                  {"path": os.path.join(self.card, "DCIM", "A002.mov"), "profile": "review"}]), ""
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
        self.assertIn("may the card be formatted: no, no offsite.txt names", text)

    def test_a_second_squeeze_answer_is_refused_unless_replace(self):
        self.go(apply=True)
        quiet = lambda m: None  # noqa: E731
        self.assertEqual(project.answer(self.cfg, "demo", True, 1.0, (), False, say=quiet), 0)
        for clips in ((), ("A001.mov",)):
            with self.assertRaisesRegex(project.ProjectError, r"already answered for \d clip\(s\).*--replace"):
                project.answer(self.cfg, "demo", False, 1.5, clips, False, say=quiet)
        self.assertEqual(project.answer(self.cfg, "demo", True, 1.5, ("A001.mov",), False, say=quiet,
                                        replace=True), 0)
        self.assertEqual(list(self.record()["answers"]["squeeze"]["clips"].values()), [1.5])
        project.answer(self.cfg, "demo", True, 1.5, ("A002.mov",), False, say=quiet, replace=True)
        with self.assertRaisesRegex(project.ProjectError, "already answered for 2 clip"):  # each has its own
            project.answer(self.cfg, "demo", False, 2.0, (), False, say=quiet)

    def test_a_profile_answer_clears_the_detect_review_row(self):
        quiet = lambda m: None  # noqa: E731
        self.go(apply=True)
        self.assertEqual(project.answer(self.cfg, "demo", True, 1.0, (), False, say=quiet), 0)
        self.assertEqual(project.answer(self.cfg, "demo", False, profile="D-Log M", say=quiet), 0)  # dry run
        self.assertNotIn("profile", self.record()["answers"])
        self.go(apply=True)
        self.assertIn("profile A002.mov", self.state()["open"])
        self.assertEqual(project.answer(self.cfg, "demo", True, profile="D-Log M", say=quiet), 0)
        self.assertEqual(list(self.record()["answers"]["profile"]["clips"].values()), ["D-Log M"])
        self.assertIsNone(self.record()["answers"]["profile"]["default"])  # A001 was pinned by detect: no default
        self.go(apply=True)
        self.assertEqual(self.state()["open"], [])

    def test_a_profile_answer_never_covers_a_pinned_clip_and_a_second_needs_replace(self):
        quiet = lambda m: None  # noqa: E731
        self.go(apply=True)
        with self.assertRaisesRegex(project.ProjectError, "from its headers"):
            project.answer(self.cfg, "demo", True, profile="Normal", clips=("A001.mov",), say=quiet)
        with self.assertRaisesRegex(project.ProjectError, "no such clip"):
            project.answer(self.cfg, "demo", True, profile="Normal", clips=("NOPE.mov",), say=quiet)
        with self.assertRaisesRegex(project.ProjectError, "is not one of"):
            project.answer(self.cfg, "demo", True, profile="Log", say=quiet)
        self.assertEqual(project.answer(self.cfg, "demo", True, profile="Normal", clips=("A002.mov",), say=quiet), 0)
        for clips in ((), ("A002.mov",)):
            with self.assertRaisesRegex(project.ProjectError, r"profile is already answered for 1 clip.*--replace"):
                project.answer(self.cfg, "demo", True, profile="D-Log M", clips=clips, say=quiet)
        self.assertEqual(list(self.record()["answers"]["profile"]["clips"].values()), ["Normal"])
        self.assertEqual(project.answer(self.cfg, "demo", True, profile="D-Log M", say=quiet, replace=True), 0)
        self.assertEqual(list(self.record()["answers"]["profile"]["clips"].values()), ["D-Log M"])

    def test_a_corrupt_clip_stays_open_and_takes_no_profile(self):
        quiet = lambda m: None  # noqa: E731
        real = self.fake

        def corrupt(argv, **kw):
            if "rpresolve.cmd_detect" in argv:
                self.calls.append(argv)
                return 2, json.dumps([{"path": os.path.join(self.card, "DCIM", "A001.mov"), "profile": "review"},
                                      {"path": os.path.join(self.card, "DCIM", "A002.mov"), "profile": "corrupt"}]), ""
            return real(argv, **kw)
        mock.patch.object(project, "RUN", corrupt).start()
        self.go(apply=True)
        project.answer(self.cfg, "demo", True, 1.0, (), False, say=quiet)
        with self.assertRaisesRegex(project.ProjectError, "corrupt"):
            project.answer(self.cfg, "demo", True, profile="HLG", clips=("A002.mov",), say=quiet)
        self.assertEqual(project.answer(self.cfg, "demo", True, profile="HLG", say=quiet), 0)
        self.assertEqual(len(self.record()["answers"]["profile"]["clips"]), 1)  # A001 only
        self.go(apply=True)
        self.assertEqual(self.state()["open"], ["corrupt A002.mov"])

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
        card = self.card_in_work("sample-shoot")
        rc, out = self.go(card=card, slug=None)
        self.assertIn("project sample-shoot (dry run)", out)
        with self.assertRaisesRegex(project.ProjectError,
                                    r"in the work folder 'sample-shoot' but --project is 'other'"):
            self.go(card=card, slug="other")
        rc, out = self.go(card=card, slug="other", new_project=True)
        self.assertIn("project other (dry run)", out)
        self.go(card=card, slug="sample-shoot")  # the same slug is fine
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

    def test_a_second_card_is_added_and_gets_its_own_identify_index_and_sheets(self):
        self.go(apply=True)
        project.answer(self.cfg, "demo", True, 1.0, (), False, say=lambda m: None)
        self.go(apply=True)  # the first card runs through index and sheets
        other = self._second_card()
        before = {n: len(self.ran(n)) for n in ("rpresolve.cmd_detect", "rpresolve.cmd_sheets", "indexer-bin")}
        _, out = self.go(apply=True, card=other)
        with open(os.path.join(self.cfg["work_root"], "demo", "project.json"), encoding="utf-8") as f:
            st = json.load(f)
        self.assertEqual(st["cards"], [self.card, other])
        self.assertNotIn("identify: done", out)
        for n, was in before.items():
            self.assertGreater(len(self.ran(n)), was, n)
        self.assertTrue(any(other in c for c in self.ran("indexer-bin")))
        self.assertEqual(st["card_done"]["identify"], [self.card, other])
        calls = len(self.calls)  # the first card again: every per-card step is done for it
        self.go(apply=True, card=self.card)
        self.assertEqual(len(self.ran("rpresolve.cmd_detect")), before["rpresolve.cmd_detect"] + 1)
        self.assertNotIn(self.card, [c[-1] for c in self.calls[calls:] if "indexer-bin" in c])

    def _formattable(self, *cards):
        """Run status over a started project holding `cards`; True when it says the card may be formatted."""
        self.go(apply=True)
        path = os.path.join(self.cfg["work_root"], "demo", "project.json")
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        st["cards"] = list(cards)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(st, f)
        lines = []
        project.status(self.cfg, "demo", say=lines.append)
        line = [x for x in lines if x.startswith("may the card be formatted")][0]
        return line.startswith("may the card be formatted: yes")

    def _offsite(self, card, utc=None):
        # a check time after the record was written; not far future: card._record_newer distrusts that
        utc = utc or (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(card + ".card.json", encoding="utf-8") as f:
            cid = json.load(f)["card_id"]
        Path(card + ".offsite.txt").write_text(f"card_id {cid}\nremote b2:bucket\nchecked_utc {utc}\n")

    def _second_card(self):
        other = self.card.replace("CAMA-000000", "CAMA-111111")
        shutil.copytree(self.card, other)
        for ext in (".card.json", ".SHA256SUMS"):
            if os.path.exists(self.card + ext):
                shutil.copy(self.card + ext, other + ext)
        return other

    def test_status_formats_only_when_every_card_is_safe(self):
        self._offsite(self.card)
        self.assertTrue(self._formattable(self.card))

    def test_status_refuses_format_without_offsite_or_cards(self):
        self.assertFalse(self._formattable(self.card))
        self.assertFalse(self._formattable())

    def test_status_refuses_format_when_the_record_is_stale(self):
        self._offsite(self.card, "2001-01-01T00:00:00Z")  # the record was written after this check
        self.assertFalse(self._formattable(self.card))

    def test_status_refuses_format_when_any_card_fails_not_just_the_last(self):
        other = self._second_card()
        self._offsite(self.card)
        for order in ((self.card, other), (other, self.card)):  # the unsafe one first, then last
            self.assertFalse(self._formattable(*order))

    def test_status_refuses_format_for_an_unreadable_card(self):
        self._offsite(self.card)
        self.assertFalse(self._formattable(self.card, self.card + "-missing"))

    def test_config_error_lists_the_footage_index_key(self):
        p = os.path.join(self.dir, "c.json")
        Path(p).write_text(json.dumps({"work_roots": "/w"}))
        with self.assertRaisesRegex(project.ProjectError, "footage_index"):
            project.load_config(p)

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
        # the destination's render folder, as queue_render reads it: an overlay outside the repo
        overlay = os.path.join(self.dir, "overlay.json")
        Path(overlay).write_text(json.dumps({"destinations": {"client_master": {"target_dir": self.dir}}}))
        mock.patch.dict(os.environ, {"RPRESOLVE_CONFIG": overlay}).start()
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

    def build(self, apply=False, manifest="given", piece=None):
        self.out.clear()
        project.build(self.cfg, "demo", self.manifest if manifest == "given" else manifest, apply=apply,
                      say=self.out.append, piece=piece)
        return "\n".join(self.out)

    def status(self):
        out = []
        project.status(self.cfg, "demo", say=out.append)
        return "\n".join(out)

    def renders(self):
        return os.path.join(self.dir, "demo", "renders")

    def test_a_destination_with_no_target_dir_renders_under_the_work_root(self):
        self.answers()
        os.environ.pop("RPRESOLVE_CONFIG")
        text = self.build()
        want = self.renders()
        self.assertIn(f"render folder: {os.path.join(want, 'client_master')}", text)
        self.assertFalse(os.path.exists(want))  # a dry run makes nothing
        self.assertIn(f"{os.path.join(want, 'client_master')}", self.status())
        self.assertEqual([t for t, _ in self.calls], ["assemble_timeline", "apply_effect", "apply_grade", "apply_grade"])
        self.assertIn("not checkable until", text)
        self.build(apply=True)
        self.assertTrue(os.path.isdir(want))
        self.assertEqual(self.calls[-1][1]["target_dir"], want)

    def test_a_configured_target_dir_wins(self):
        self.answers()
        text = self.build(apply=True)
        self.assertIn(f"render folder: {os.path.join(self.dir, 'client_master')}", text)
        self.assertNotIn("target_dir", self.calls[-1][1])
        self.assertFalse(os.path.exists(self.renders()))

    def test_bad_name_parts_are_refused_before_any_step(self):
        self.answer(shape="3840x1608")
        self.answer(look="/luts/look.cube")
        self.answer(destination="client_master", name_parts=["client=Acme"])
        os.environ.pop("RPRESOLVE_CONFIG")  # the default folder skips queue_render in a dry run
        for apply in (False, True):
            with self.assertRaisesRegex(project.ProjectError, "nothing was built.*missing slug"):
                self.build(apply=apply)
        self.assertEqual(self.calls, [])

    def test_the_render_folder_is_made_before_the_assemble_and_a_file_in_its_way_stops_it(self):
        self.answers()
        os.environ.pop("RPRESOLVE_CONFIG")
        self.build(apply=True)
        self.assertTrue(os.path.isdir(self.renders()))
        self.calls.clear()
        shutil.rmtree(self.renders())
        Path(self.renders()).write_text("a file")
        with self.assertRaisesRegex(project.ProjectError, "nothing was built: cannot make the render folder"):
            self.build(apply=True)
        self.assertEqual(self.calls, [])

    def test_two_pieces_that_render_to_one_file_are_refused(self):
        self.answers()
        self.answer(piece="second-cut", destination="client_master", name_parts=["client=Acme", "slug=demo-cut"])
        with self.assertRaisesRegex(project.ProjectError, "piece 'second-cut' renders to the same file"):
            self.build()
        self.assertEqual(self.calls, [])

    def picked(self):
        ledger, exclude = os.path.join(self.dir, "drafts.json"), os.path.join(self.dir, "exclude.json")
        Path(ledger).write_text("{}")
        Path(exclude).write_text("{}")
        self.answer(pick=(self.manifest, ledger, exclude, "plan-b"))
        return ledger, exclude

    def test_the_build_reads_the_pick_with_its_ledger_and_exclude_list(self):
        self.answers()
        with self.assertRaisesRegex(project.ProjectError, "--pick MANIFEST"):
            self.build(manifest=None)
        with self.assertRaisesRegex(project.ProjectError, "go with --pick"):
            self.answer(pick=(None, self.manifest, None, None))
        ledger, exclude = self.picked()
        self.build(manifest=None)
        sent = self.calls[0][1]
        self.assertEqual((sent["manifest"], sent["ledger"], sent["draft"], sent["exclude"]),
                         (self.manifest, ledger, "plan-b", exclude))
        other = os.path.join(self.dir, "other.json")
        Path(other).write_text(json.dumps({"effects": []}))
        self.calls.clear()
        self.build(manifest=other)  # another manifest: the exclude list holds, the pick's ledger does not
        self.assertEqual(self.calls[0][1]["exclude"], exclude)
        self.assertNotIn("ledger", self.calls[0][1])
        with self.assertRaisesRegex(project.ProjectError, "already recorded"):
            self.answer(pick=(other, None, None, None))

    def test_a_second_piece_records_only_what_differs(self):
        self.answers()
        self.answer(piece="second-cut", destination="client_master", name_parts=["client=Acme", "slug=second-cut"])
        with self.assertRaisesRegex(project.ProjectError, "already recorded"):
            self.answer(piece="second-cut", destination="client_master", name_parts=["client=Acme"])
        self.build(apply=True, piece="second-cut")
        real = [(t, a) for t, a in self.calls if not a["dry_run"]]
        self.assertEqual(real[0][1]["width"], 3840)  # the project's shape
        self.assertEqual(real[0][1]["name"], "second-cut [auto]")
        self.assertEqual(real[-1][1]["name"], {"client": "Acme", "slug": "second-cut"})
        self.assertIn("recorded for piece second-cut: destination", self.status())
        with self.assertRaisesRegex(project.ProjectError, "no answers recorded for piece 'third'"):
            self.build(piece="third")

    def test_status_shows_answers_as_recorded_and_stops_asking_for_them(self):
        pk, root = self.packet()
        Path(root, "library.json").write_text("{}")
        self.answer(shape="3840x1608")
        self.assertIn("project answer SLUG", self.status())
        self.assertIn("music (optional)", self.status())
        self.answer(look="/luts/look.cube")
        self.answer(destination="client_master", name_parts=["client=Acme", "slug=demo-cut"])
        self.answer(music=(pk, "B", None))
        text = self.status()
        self.assertIn('recorded: shape: {"width": 3840, "height": 1608}', text)
        self.assertNotIn("open: ", text)
        self.assertNotIn("project answer SLUG", text)  # step 8 now names only the build
        self.assertIn("next: project build SLUG", text)
        self.assertNotIn("music (optional)", text)
        self.assertIn("next: write the shot list", text)
        self.answer(pick=(self.manifest, None, None, None))
        self.assertNotIn("next: write the shot list", self.status())  # the pick ends step 7's hand work

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

    def test_a_bad_answer_records_none_of_the_others(self):
        with self.assertRaisesRegex(project.ProjectError, "KEY=VALUE"):
            self.answer(shape="3840x1608", destination="client_master", name_parts=["client"])
        state = os.path.join(self.dir, "demo", "project.json")
        self.assertFalse(os.path.exists(state) and "answers" in json.load(open(state)))
        self.answer(shape="3840x1608", destination="client_master", name_parts=["client=Acme"])

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

    def packet(self, letter="B"):
        """A music root with one track and a candidates packet whose letter B stands for it."""
        root = os.path.join(self.dir, "lib")
        os.makedirs(os.path.join(root, "a"))
        Path(root, "a", "tone one.wav").write_bytes(b"x")
        pk = os.path.join(self.dir, "pk")
        os.makedirs(pk)
        Path(pk, "key.json").write_text(json.dumps({"in": 4, "options": {"B": {"path": "a/tone one.wav"}}}))
        self.cfg["music_root"] = root
        return pk, root

    def test_a_music_pick_goes_into_a_copy_of_the_manifest(self):
        self.answers()
        pk, root = self.packet()
        self.answer(music=(pk, "b", None))
        st = json.load(open(os.path.join(self.dir, "demo", "project.json")))
        self.assertEqual(st["answers"]["music"]["path"], os.path.join(root, "a", "tone one.wav"))
        self.assertEqual(st["answers"]["music"]["in"], 4.0)
        with self.assertRaisesRegex(project.ProjectError, "already recorded"):
            self.answer(music=(pk, "B", None))
        text = self.build(apply=True)
        sent = self.calls[0][1]["manifest"]
        self.assertEqual(sent, os.path.join(self.dir, "demo", "build-manifest.json"))
        self.assertEqual(json.load(open(sent))["music"], {"path": os.path.join(root, "a", "tone one.wav"), "in": 4.0})
        self.assertNotIn("music", json.load(open(self.manifest)))  # the picked manifest is untouched
        self.assertIn("music:", text)

    def test_a_manifest_with_its_own_music_and_a_pick_is_refused(self):
        self.answers()
        pk, _ = self.packet()
        self.answer(music=(pk, "B", None))
        Path(self.manifest).write_text(json.dumps({"music": {"path": "/m/own.wav", "in": 0}}))
        for apply in (False, True):
            with self.assertRaisesRegex(project.ProjectError, r"own\.wav.*tone one\.wav.*--clear-music"):
                self.build(apply=apply)
        self.assertEqual(self.calls, [])
        self.answer(clear_music=True)
        self.assertEqual(self.calls, [])
        self.assertNotIn("music", json.load(open(os.path.join(self.dir, "demo", "project.json")))["answers"])
        self.build()
        self.assertEqual(self.calls[0][1]["manifest"], self.manifest)  # the manifest's own music stands

    def test_a_piece_clears_the_projects_pick_for_itself_only(self):
        self.answers()
        pk, _ = self.packet()
        self.answer(music=(pk, "B", None))
        self.answer(piece="second-cut", destination="client_master", name_parts=["client=Acme", "slug=second-cut"])
        Path(self.manifest).write_text(json.dumps({"music": {"path": "/m/own.wav", "in": 0}}))
        with self.assertRaisesRegex(project.ProjectError, r"project answer demo --piece second-cut --clear-music"):
            self.build(piece="second-cut")
        self.answer(piece="second-cut", clear_music=True)
        self.build(piece="second-cut")  # the manifest's own music stands for the piece
        self.assertEqual(self.calls[0][1]["manifest"], self.manifest)
        st = json.load(open(os.path.join(self.dir, "demo", "project.json")))
        self.assertIn("music", st["answers"])  # the project's pick is untouched

    def test_a_manifest_whose_music_is_the_pick_goes_ahead(self):
        self.answers()
        pk, root = self.packet()
        self.answer(music=(pk, "B", None))
        Path(self.manifest).write_text(json.dumps({"music": {"path": os.path.join(root, "a", "tone one.wav"), "in": 4}}))
        text = self.build()
        self.assertEqual(self.calls[0][1]["manifest"], self.manifest)
        self.assertNotIn("recorded pick goes in", text)

    def test_a_pieces_own_pick_keeps_the_projects_exclude_list(self):
        self.answers()
        ledger, exclude = self.picked()
        self.answer(piece="second-cut", destination="client_master", name_parts=["client=Acme", "slug=second-cut"])
        self.answer(piece="second-cut", pick=(self.manifest, None, None, None))
        self.build(piece="second-cut")
        self.assertEqual(self.calls[0][1]["exclude"], exclude)
        self.assertNotIn("ledger", self.calls[0][1])

    def test_clearing_a_pick_that_is_not_recorded_is_refused(self):
        with self.assertRaisesRegex(project.ProjectError, "no music pick"):
            self.answer(clear_music=True)

    def test_a_music_key_without_a_letter_is_refused(self):
        pk, _ = self.packet()
        with self.assertRaisesRegex(project.ProjectError, "needs both"):
            self.answer(music=(pk, None, None))

    def test_a_dry_run_build_leaves_no_file_beside_the_state(self):
        self.answers()
        pk, _ = self.packet()
        self.answer(music=(pk, "B", None))
        self.build()
        self.assertFalse(os.path.exists(os.path.join(self.dir, "demo", "build-manifest.json")))
        self.assertFalse(os.path.exists(os.path.dirname(self.calls[0][1]["manifest"])))  # temp copy removed

    def test_a_letter_the_packet_does_not_hold_is_refused(self):
        pk, _ = self.packet()
        with self.assertRaisesRegex(project.ProjectError, "no key.json with letter C"):
            self.answer(music=(pk, "C", None))

    def test_a_dry_run_refusal_other_than_the_missing_timeline_is_reported(self):
        self.answers()
        orig = self.fake

        def no_folder(tool, args):
            if tool == "queue_render":
                raise ValueError("no target folder: give one")
            return orig(tool, args)
        mock.patch.object(project, "CALL", no_folder).start()
        with self.assertRaisesRegex(project.ProjectError, "queue_render: no target folder"):
            self.build()

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
