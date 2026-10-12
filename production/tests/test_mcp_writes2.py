"""
Tests for duplicate_timeline_auto, apply_grade and queue_render
(rpresolve.workflows and rpresolve.mcp.tools_write) against the shared
Resolve fakes: dry run then plan_sha, the [auto]-only and default-graph
guards, the grade-leak check, UI restore, and that a render is queued
for a delivery destination, the only way to queue one, but never started.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import resolve_fakes as rf  # noqa: E402
from rpresolve import api, workflows  # noqa: E402
from rpresolve.mcp import schema, server  # noqa: E402
from rpresolve.mcp.registry import Cancelled, ToolContext  # noqa: E402
from test_mcp import Harness, init  # noqa: E402

REG = server.build_registry()
P = "RP Automation Sandbox"


def call(name, args, resolve):
    tool = REG.get(name)
    args = schema.coerce(tool.input_schema, {"project": P, **args})
    errors = schema.validate(tool.input_schema, args)
    if errors:
        raise AssertionError(errors)
    return tool.handler(schema.with_defaults(tool.input_schema, args),
                        ToolContext(session=rf.Session(resolve)))


def run_twice(name, args, resolve):
    """A dry run, then the real run with its plan_sha."""
    dry = call(name, args, resolve)
    return dry, call(name, {**args, "dry_run": False, "plan_sha": dry["plan_sha"]}, resolve)


class Base(unittest.TestCase):
    def setUp(self):
        rf.CALLS.clear()
        rf.DRX_GRAPHS.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.realpath(self.tmp.name)
        env = mock.patch.dict(os.environ, {
            "RPRESOLVE_MCP_JOURNAL": os.path.join(self.dir, "writes.jsonl"),
            "RPRESOLVE_LOCK": os.path.join(self.dir, "lock")})
        env.start()
        self.addCleanup(env.stop)
        self.clip = rf.Clip("P1.MOV", "clip-1", {"File Path": "/w/P1.MOV"})
        self.other = rf.Clip("P2.MOV", "clip-2", {"File Path": "/w/P2.MOV"})
        self.origin = rf.Timeline("Edit", "tl-edit", 25.0, 86400, 86500, {
            "video": [[rf.Item("P1.MOV", 86400, 86450, self.clip, source=(10, 60)),
                       rf.Item("P2.MOV", 86450, 86500, self.other, source=(0, 50))]],
            "audio": [[rf.Item("P1.MOV", 86400, 86450, self.clip, nodes=None)]]})
        self.here = rf.Timeline("Where Isaac is", "tl-here")
        self.project = rf.Project(timelines=[self.origin, self.here], current=self.here)
        self.resolve = rf.Resolve(self.project, page="cut")

    def tearDown(self):
        self.tmp.cleanup()

    def journal(self):
        path = os.environ["RPRESOLVE_MCP_JOURNAL"]
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [json.loads(line)["event"] for line in f]

    def ui(self):
        return self.resolve.page, self.project.current

    def make_auto(self):
        dry, real = run_twice("duplicate_timeline_auto", {"timeline": "Edit"}, self.resolve)
        return next(t for t in self.project.timelines if t.name == "Edit [auto]")


class TestDuplicate(Base):
    def test_dry_run_then_real(self):
        dry = call("duplicate_timeline_auto", {"timeline": "Edit"}, self.resolve)
        self.assertEqual(dry["new_name"], "Edit [auto]")
        self.assertEqual(dry["origin"]["items"], 3)
        self.assertEqual(len(self.project.timelines), 2)
        real = call("duplicate_timeline_auto", {"timeline": "Edit", "dry_run": False,
                                                "plan_sha": dry["plan_sha"]}, self.resolve)
        self.assertEqual(real["created"]["name"], "Edit [auto]")
        self.assertEqual(real["mismatches"], [])
        self.assertIn("every item matches", real["summary"])
        self.assertEqual(self.ui(), ("cut", self.here))  # the copy was made current, then put back
        self.assertEqual(self.journal(), ["started", "finished"])

    def test_refusals(self):
        self.make_auto()
        with self.assertRaisesRegex(workflows.Refused, "already exists"):
            call("duplicate_timeline_auto", {"timeline": "Edit"}, self.resolve)
        with self.assertRaisesRegex(workflows.Refused, "already an \\[auto\\]"):
            call("duplicate_timeline_auto", {"timeline": "Edit [auto]"}, self.resolve)
        with self.assertRaises(AssertionError):
            call("duplicate_timeline_auto", {"timeline": "Edit", "new_name": "Mine"},
                 self.resolve)
        r = call("duplicate_timeline_auto", {"timeline": "Edit [auto]",
                                             "new_name": "Edit v2 [auto]"}, self.resolve)
        self.assertEqual(r["new_name"], "Edit v2 [auto]")


class TestApplyGrade(Base):
    def setUp(self):
        super().setUp()
        self.lut = os.path.join(self.dir, "Look.cube")
        open(self.lut, "w").close()
        self.drx = os.path.join(self.dir, "out709.drx")
        open(self.drx, "w").close()
        rf.DRX_GRAPHS[self.drx] = [("CST OUT 709", "", ["OFX: Color Space Transform"])]

    def test_only_auto_timelines(self):
        with self.assertRaisesRegex(workflows.Refused, "not an \\[auto\\]"):
            call("apply_grade", {"timeline": "Edit", "lut": {"path": self.lut}}, self.resolve)

    def test_lut_dry_then_real_reads_back_and_restores(self):
        auto = self.make_auto()
        rf.CALLS.clear()
        args = {"timeline": "Edit [auto]", "lut": {"path": self.lut}}
        dry = call("apply_grade", args, self.resolve)
        self.assertEqual(len(dry["targets"]), 2)
        self.assertEqual(rf.mutating_calls(), [])
        real = call("apply_grade", {**args, "dry_run": False, "plan_sha": dry["plan_sha"]},
                    self.resolve)
        self.assertEqual([r["ok"] for r in real["results"]], [True, True])
        self.assertEqual(auto.tracks["video"][0][0].graph.nodes[0][1], self.lut)
        self.assertEqual(real["leaks"], [])
        self.assertIn("no other timeline changed", real["summary"])
        self.assertEqual(self.origin.tracks["video"][0][0].graph.nodes, [("", "", None)])
        self.assertEqual(self.ui(), ("cut", self.here))

    def test_a_lut_symlink_is_sent_by_its_own_path_and_the_lut_list_is_rescanned(self):
        # A link in Resolve's LUT folder to a file kept elsewhere: Resolve scanned the
        # link's path, so SetLUT must get that path and not the link's target.
        root = os.path.join(self.dir, "LUT")
        os.makedirs(root)
        target = os.path.join(self.dir, "repo", "Look.cube")
        os.makedirs(os.path.dirname(target))
        open(target, "w").close()
        link = os.path.join(root, "Look.cube")
        os.symlink(target, link)
        auto = self.make_auto()
        rf.CALLS.clear()
        dry, real = run_twice("apply_grade", {"timeline": "Edit [auto]", "lut": {"path": link}},
                              self.resolve)
        self.assertEqual([r["ok"] for r in real["results"]], [True, True])
        self.assertEqual(auto.tracks["video"][0][0].graph.nodes[0][1], link)
        self.assertEqual(auto.tracks["video"][0][1].graph.nodes[0][1], link)
        self.assertIn("RefreshLUTList", [c[1] for c in rf.CALLS])

    def test_existing_grades_need_overwrite(self):
        auto = self.make_auto()
        auto.tracks["video"][0][0].graph.nodes[:] = [("Hand", "", ["Contrast"])]
        r = call("apply_grade", {"timeline": "Edit [auto]", "lut": {"path": self.lut}},
                 self.resolve)
        self.assertEqual([t["index"] for t in r["targets"]], [2])
        self.assertIn("not default", r["refused"][0]["reasons"][0])
        r = call("apply_grade", {"timeline": "Edit [auto]", "lut": {"path": self.lut},
                                 "overwrite": True}, self.resolve)
        self.assertEqual([t["index"] for t in r["targets"]], [1, 2])
        self.assertIn("replacing 1 existing grade", r["summary"])

    def test_remote_version_refused_and_node_limit_is_hard(self):
        auto = self.make_auto()
        auto.tracks["video"][0][0].version = {"versionName": "R", "versionType": 1}
        r = call("apply_grade", {"timeline": "Edit [auto]", "lut": {"path": self.lut}},
                 self.resolve)
        self.assertIn("remote", r["refused"][0]["reasons"][0])
        r = call("apply_grade", {"timeline": "Edit [auto]", "lut": {"path": self.lut},
                                 "overwrite": True}, self.resolve)
        self.assertEqual([t["index"] for t in r["targets"]], [2])  # remote stays refused
        with self.assertRaises(AssertionError):
            call("apply_grade", {"timeline": "Edit [auto]", "items": [2, 2],
                                 "lut": {"path": self.lut}}, self.resolve)
        with self.assertRaisesRegex(workflows.Refused, "cannot add node 3"):
            call("apply_grade", {"timeline": "Edit [auto]", "items": [2], "overwrite": True,
                                 "lut": {"path": self.lut, "node": 3}}, self.resolve)

    def test_drx_needs_its_manifest_and_is_read_back(self):
        self.make_auto()
        with self.assertRaisesRegex(workflows.Refused, "label manifest"):
            call("apply_grade", {"timeline": "Edit [auto]", "drx": {"path": self.drx}},
                 self.resolve)
        with open(self.drx[:-4] + ".json", "w") as f:
            json.dump({"num_nodes": 1, "labels": ["CST OUT 709"]}, f)
        dry, real = run_twice("apply_grade", {"timeline": "Edit [auto]", "items": [1],
                                              "drx": {"path": self.drx}}, self.resolve)
        self.assertEqual(real["results"][0]["ok"], True)
        self.assertEqual(real["exit_status"], 0)
        rf.DRX_GRAPHS[self.drx] = [("Wrong", "", None)]
        dry, real = run_twice("apply_grade", {"timeline": "Edit [auto]", "items": [2],
                                              "drx": {"path": self.drx}}, self.resolve)
        self.assertFalse(real["results"][0]["ok"])
        self.assertIn("manifest", real["results"][0]["detail"])
        self.assertEqual(real["exit_status"], 1)

    def test_a_shared_graph_is_reported_as_a_leak(self):
        auto = self.make_auto()
        auto.tracks["video"][0][0].graph = self.origin.tracks["video"][0][0].graph
        dry, real = run_twice("apply_grade", {"timeline": "Edit [auto]", "items": [1],
                                              "lut": {"path": self.lut}}, self.resolve)
        self.assertEqual(real["leaks"], ["Edit V1 #1"])
        self.assertIn("GRADE LEAKED", real["summary"])
        self.assertEqual(real["exit_status"], 1)

    def test_stale_sha_and_bad_arguments(self):
        self.make_auto()
        with self.assertRaisesRegex(workflows.Refused, "plan changed"):
            call("apply_grade", {"timeline": "Edit [auto]", "lut": {"path": self.lut},
                                 "dry_run": False, "plan_sha": "0" * 64}, self.resolve)
        with self.assertRaisesRegex(workflows.Refused, "exactly one"):
            call("apply_grade", {"timeline": "Edit [auto]"}, self.resolve)
        with self.assertRaisesRegex(workflows.Refused, "not found"):
            call("apply_grade", {"timeline": "Edit [auto]",
                                 "lut": {"path": "nowhere/none.cube"}}, self.resolve)
        with self.assertRaisesRegex(workflows.Refused, "no item"):
            call("apply_grade", {"timeline": "Edit [auto]", "items": [9],
                                 "lut": {"path": self.lut}}, self.resolve)


class TestStabilize(Base):
    def setUp(self):
        super().setUp()
        self.auto = self.make_auto()
        self.items = self.auto.tracks["video"][0]
        self.items.append(rf.Item("P3.MOV", 86500, 86560, self.clip, source=(100, 160)))
        self.seen = []  # (item id, page, current timeline, playhead) at each Stabilize()
        for it in self.items:
            it.on_stabilize = lambda it=it: self.seen.append(
                (it.uid, self.resolve.page, self.project.current, self.auto.playhead))
        rf.CALLS.clear()

    def stab(self, args=None, **more):
        return call("stabilize", {"timeline": "Edit [auto]", **(args or {}), **more},
                    self.resolve)

    def stabilized(self):
        return [c[2][0] for c in rf.CALLS if c[1] == "Stabilize"]

    def test_only_auto_timelines(self):
        with self.assertRaisesRegex(workflows.Refused, "not an \\[auto\\]"):
            self.stab(timeline="Edit")
        self.assertEqual(rf.mutating_calls(), [])

    def test_wrong_project_is_refused(self):
        with self.assertRaises(api.ProjectChanged):
            self.stab(project="Another Project")
        with self.assertRaises(api.ProjectChanged):
            self.stab(project_id="not-this-id")
        self.assertEqual(rf.mutating_calls(), [])
        self.assertEqual(self.stabilized(), [])

    def test_dry_run_gives_a_plan_sha_and_touches_nothing(self):
        dry = self.stab()
        self.assertEqual(len(dry["plan_sha"]), 64)
        self.assertEqual([t["index"] for t in dry["targets"]], [1, 2, 3])
        self.assertEqual([t["source_start"] for t in dry["targets"]], [10, 0, 100])
        self.assertEqual([t["item_id"] for t in dry["targets"]], [i.uid for i in self.items])
        self.assertFalse(dry["verified"])
        self.assertIn("not visible through GetProperty", dry["note"])
        self.assertIn(dry["plan_sha"], dry["summary"])
        self.assertEqual(rf.mutating_calls(), [])
        self.assertEqual(self.ui(), ("cut", self.here))

    def test_a_real_run_needs_the_plan_sha(self):
        with self.assertRaisesRegex(workflows.Refused, "needs the plan_sha"):
            self.stab(dry_run=False)
        self.assertEqual(self.stabilized(), [])

    def test_a_stale_plan_sha_is_refused(self):
        dry = self.stab()
        self.items[1].source = (5, 55)  # the plan changed since the dry run
        with self.assertRaisesRegex(workflows.Refused, "plan changed"):
            self.stab(dry_run=False, plan_sha=dry["plan_sha"])
        self.assertEqual(self.stabilized(), [])
        dry2 = self.stab(items=[1])
        with self.assertRaisesRegex(workflows.Refused, "plan changed"):
            self.stab(dry_run=False, plan_sha=dry2["plan_sha"], items=[1, 3])
        self.assertEqual(self.stabilized(), [])

    def test_real_run_stabilizes_exactly_the_planned_items(self):
        dry, real = run_twice("stabilize", {"timeline": "Edit [auto]", "items": [1, 3]},
                              self.resolve)
        self.assertEqual(self.stabilized(), [self.items[0].uid, self.items[2].uid])
        self.assertEqual([r["index"] for r in real["results"]], [1, 3])
        self.assertEqual([r["ok"] for r in real["results"]], [True, True])
        for r in real["results"]:
            self.assertIsInstance(r["elapsed_s"], float)
            self.assertGreaterEqual(r["elapsed_s"], 0)
            self.assertTrue(r["playhead_set"])
        self.assertEqual(real["exit_status"], 0)
        self.assertFalse(real["verified"])
        self.assertIn("NOT verified", real["summary"])
        self.assertEqual(self.journal()[2:], ["started", "finished"])  # after make_auto's own pair
        # Nothing else changed: the other timelines' items were not touched.
        self.assertEqual([c for c in rf.CALLS if c[1] == "Stabilize"
                          and c[2][0] in (i.uid for i in self.origin.tracks["video"][0])], [])

    def test_the_timeline_is_current_on_the_color_page_for_each_call(self):
        self.stab(dry_run=False, plan_sha=self.stab()["plan_sha"])
        self.assertEqual(len(self.seen), 3)
        for uid, page, current, playhead in self.seen:
            self.assertEqual((page, current), ("color", self.auto))
        # The playhead went to each item's start (start frames 86400, 86450, 86500 at 25 fps).
        self.assertEqual([s[3] for s in self.seen], ["00:57:36:00", "00:57:38:00", "00:57:40:00"])

    def test_ui_is_put_back(self):
        self.here.playhead = "01:00:09:12"
        real = self.stab(dry_run=False, plan_sha=self.stab()["plan_sha"])
        self.assertEqual(self.ui(), ("cut", self.here))
        self.assertEqual(self.here.playhead, "01:00:09:12")
        self.assertEqual(real["ui_restore_problems"], [])

    def test_a_restore_problem_is_reported(self):
        # Where Isaac was vanishes while Resolve works: the restore says so.
        self.items[2].on_stabilize = lambda: self.project.timelines.remove(self.here)
        real = self.stab(dry_run=False, plan_sha=self.stab()["plan_sha"])
        self.assertTrue(real["ui_restore_problems"], real)
        self.assertIn("UI was not fully restored", real["summary"])

    def test_one_call_is_capped(self):
        # A call holds the Resolve lock, so it never runs more than the cap.
        with self.assertRaisesRegex(AssertionError, "max_items: 26 is above the maximum 25"):
            self.stab(max_items=26)
        with self.assertRaisesRegex(workflows.Refused, "max_items must be 1 to 25"):
            workflows.stabilize(self.resolve, P, "Edit [auto]", max_items=26, dry_run=True)
        dry = self.stab()
        self.assertEqual(dry["remaining"], [])

    def test_the_journal_keeps_the_rows_for_stabilize_only(self):
        real = self.stab(dry_run=False, plan_sha=self.stab()["plan_sha"])
        self.assertEqual(real["stabilized"], real["results"])

    def test_max_items_is_honoured(self):
        dry = self.stab(max_items=2)
        self.assertEqual([t["index"] for t in dry["targets"]], [1, 2])
        self.assertEqual(dry["planned_total"], 3)
        self.assertIn("2 of 3 item(s); call again with item 3 for the rest", dry["summary"])
        self.assertEqual(dry["remaining"], [3])
        self.assertNotEqual(dry["plan_sha"], self.stab()["plan_sha"])
        real = self.stab(max_items=2, dry_run=False, plan_sha=dry["plan_sha"])
        self.assertEqual(self.stabilized(), [self.items[0].uid, self.items[1].uid])
        self.assertEqual(len(real["results"]), 2)
        # max_items counts from the selection, not from the track.
        dry = self.stab(items=[3, 2], max_items=1)
        self.assertEqual([t["index"] for t in dry["targets"]], [2])

    def test_a_false_from_stabilize_is_reported_per_item(self):
        self.items[1].stabilize_returns = False
        dry = self.stab()
        real = self.stab(dry_run=False, plan_sha=dry["plan_sha"])
        self.assertEqual([r["ok"] for r in real["results"]], [True, False, True])
        self.assertIn("returned False", real["results"][1]["detail"])
        self.assertEqual(real["exit_status"], 1)
        self.assertIn("True for 2 of 3", real["summary"])
        self.assertEqual(self.stabilized(), [i.uid for i in self.items])  # it went on
        self.assertEqual(self.journal()[2:], ["started", "finished"])  # after make_auto's own pair

    def test_a_raise_from_stabilize_is_a_failed_item(self):
        self.items[0].stabilize_returns = RuntimeError("no tracking data")
        real = self.stab(dry_run=False, plan_sha=self.stab()["plan_sha"])
        self.assertFalse(real["results"][0]["ok"])
        self.assertIn("RuntimeError: no tracking data", real["results"][0]["detail"])
        self.assertEqual(real["exit_status"], 1)

    def test_a_cancel_stops_between_items_and_keeps_what_was_done(self):
        dry = self.stab()
        calls = []

        def cancel():
            calls.append(1)
            if len(self.stabilized()) >= 1:
                raise Cancelled("cancelled by the client")

        with self.assertRaises(Cancelled) as cm:
            workflows.stabilize(self.resolve, P, "Edit [auto]", dry_run=False,
                                check_cancel=cancel, expect_sha=dry["plan_sha"])
        self.assertEqual(self.stabilized(), [self.items[0].uid])
        self.assertEqual(len(cm.exception.stabilized), 1)
        self.assertEqual(self.ui(), ("cut", self.here))

    def test_item_and_track_checks(self):
        with self.assertRaisesRegex(workflows.Refused, "no item\\(s\\) \\[9\\]"):
            self.stab(items=[9])
        with self.assertRaisesRegex(workflows.Refused, "1 video track"):
            self.stab(track=2)
        with self.assertRaises(AssertionError):
            self.stab(max_items=0)

    def test_a_render_in_progress_refuses(self):
        self.project.rendering = True
        with self.assertRaisesRegex(workflows.Refused, "render is running"):
            self.stab()

    def test_timecode_helper(self):
        tc = workflows._frames_to_timecode
        self.assertEqual(tc(86400, 24), "01:00:00:00")
        self.assertEqual(tc(90050, 25), "01:00:02:00")
        self.assertEqual(tc(1800, 30, drop=True), "00:01:00;02")
        self.assertEqual(tc(17982, 30, drop=True), "00:10:00;00")


class TestQueueRender(Base):
    """queue_render queues for a delivery destination and nothing else.
    client_master is the one destination that takes the timeline's own size,
    so these timelines need no shape of their own."""

    def args(self, **kw):
        return {"timeline": "Edit", "destination": "client_master",
                "name": {"client": "Acme", "slug": "edit"}, "target_dir": self.dir, **kw}

    def out(self, slug="edit"):
        return os.path.join(self.dir, "client_master", f"Acme_{slug}_master.mov")

    def test_queued_never_started_and_ui_and_format_restored(self):
        self.project.fmt = {"format": "mp4", "codec": "H264"}
        dry, real = run_twice("queue_render", self.args(), self.resolve)
        self.assertEqual(dry["output"], self.out())
        self.assertIn("TargetDir", dry["deliver_changed"])
        self.assertEqual(real["job"]["TimelineName"], "Edit")
        self.assertEqual(real["job"]["VideoFormat"], "mov")
        self.assertEqual(real["readback_problems"], [])
        self.assertIn("NOT started", real["summary"])
        self.assertEqual(self.project.fmt, {"format": "mp4", "codec": "H264"})
        self.assertEqual(self.ui(), ("cut", self.here))
        self.assertNotIn("StartRendering", [c[1] for c in rf.CALLS])
        self.assertEqual(len(self.project.jobs), 1)
        self.assertEqual(self.journal(), ["started", "finished"])

    def test_a_dry_run_queues_and_journals_nothing(self):
        dry = call("queue_render", self.args(), self.resolve)
        self.assertEqual(len(dry["plan_sha"]), 64)
        self.assertEqual((self.project.jobs, self.journal()), ([], []))
        with self.assertRaisesRegex(workflows.Refused, "plan_sha"):
            call("queue_render", self.args(dry_run=False), self.resolve)
        with self.assertRaisesRegex(workflows.Refused, "plan changed"):
            call("queue_render", self.args(dry_run=False, plan_sha="0" * 64), self.resolve)
        self.assertEqual(self.project.jobs, [])

    def test_unset_deliver_format_is_reported(self):
        self.project.fmt = {"format": "unknown", "codec": ""}
        dry, real = run_twice("queue_render", self.args(), self.resolve)
        self.assertTrue(any("no format set" in w for w in real["warnings"]))

    def test_refusals(self):
        os.makedirs(os.path.dirname(self.out()))
        open(self.out(), "w").close()
        with self.assertRaisesRegex(workflows.Refused, "already exists"):
            call("queue_render", self.args(), self.resolve)
        self.project.rendering = True
        with self.assertRaisesRegex(workflows.Refused, "render is running"):
            call("queue_render", self.args(name={"client": "Acme", "slug": "other"}),
                 self.resolve)
        self.project.rendering = False
        repo = os.path.join(self.dir, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        with self.assertRaisesRegex(workflows.Refused, "git working tree"):
            call("queue_render", self.args(target_dir=repo), self.resolve)
        with self.assertRaisesRegex(workflows.Refused, "does not exist"):
            call("queue_render", self.args(target_dir=os.path.join(self.dir, "nope")),
                 self.resolve)
        with self.assertRaises(AssertionError):
            call("queue_render", self.args(name={"client": "Acme", "slug": "../escape"}),
                 self.resolve)
        with self.assertRaisesRegex(workflows.Refused, "unknown destination 'nope'"):
            call("queue_render", self.args(destination="nope"), self.resolve)
        self.assertEqual(self.project.jobs, [])


class TestQueueRenderIsDestinationOnly(Base):
    """What a client of the server sees: no preset in the schema, and a call
    that still passes one is told to pass a destination."""

    def setUp(self):
        super().setUp()
        self.h = Harness(REG)
        self.addCleanup(self.close)
        init(self.h)

    def close(self):
        try:
            self.h.close()
        except OSError:
            pass

    def call_tool(self, arguments):
        self.h.send({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                     "params": {"name": "queue_render",
                                "arguments": {"project": P, "timeline": "Edit", **arguments}}})
        return self.h.recv()["result"]

    def test_the_listed_tool_has_no_preset(self):
        self.h.send({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = {t["name"]: t for t in self.h.recv()["result"]["tools"]}
        self.assertEqual(len(tools), 27)  # 23, plus set_color_management, timeline_from_clips, relink and stabilize
        tool = tools["queue_render"]
        props = tool["inputSchema"]["properties"]
        self.assertEqual(sorted(props), ["destination", "dry_run", "name", "plan_sha", "project",
                                         "project_id", "target_dir", "timeline"])
        self.assertEqual(tool["inputSchema"]["required"], ["project", "timeline", "destination"])
        self.assertNotIn("enum", props["destination"])
        self.assertNotIn("preset", json.dumps(tool).lower())

    def test_a_call_that_still_passes_a_preset_is_told_to_pass_a_destination(self):
        r = self.call_tool({"preset": "master", "output_dir": self.dir, "custom_name": "cut"})
        self.assertTrue(r["isError"])
        errors = json.loads(r["content"][0]["text"])["errors"]
        for name in ("preset", "output_dir", "custom_name"):
            hit = [e for e in errors if f"'{name}' was removed" in e]
            self.assertEqual(len(hit), 1, errors)
            self.assertIn("'destination'", hit[0])
        self.assertTrue(any("missing required 'destination'" in e for e in errors), errors)
        self.assertFalse(any("unknown property" in e for e in errors), errors)

    def test_a_preset_beside_a_destination_is_refused_too(self):
        r = self.call_tool({"destination": "client_master", "name": {"client": "Acme",
                            "slug": "edit"}, "target_dir": self.dir, "preset": "master"})
        self.assertTrue(r["isError"])
        errors = json.loads(r["content"][0]["text"])["errors"]
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("'preset' was removed", errors[0])
        self.assertIn("'destination'", errors[0])

    def test_nothing_reaches_resolve_or_the_journal(self):
        self.call_tool({"preset": "master", "output_dir": self.dir})
        self.call_tool({"preset": "master", "output_dir": self.dir, "dry_run": False,
                        "plan_sha": "0" * 64})
        self.assertEqual((self.project.jobs, self.journal(), rf.CALLS), ([], [], []))


if __name__ == "__main__":
    unittest.main()
