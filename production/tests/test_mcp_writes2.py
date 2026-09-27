"""
Tests for duplicate_timeline_auto, apply_grade and queue_render
(rpresolve.workflows and rpresolve.mcp.tools_write) against the shared
Resolve fakes: dry run then plan_sha, the [auto]-only and default-graph
guards, the grade-leak check, UI restore, and that a render is queued
but never started.

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
from rpresolve import paths, workflows  # noqa: E402
from rpresolve.mcp import schema, server  # noqa: E402
from rpresolve.mcp.registry import ToolContext  # noqa: E402

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


class TestQueueRender(Base):
    def args(self, **kw):
        return {"timeline": "Edit", "preset": "master", "output_dir": self.dir, **kw}

    def test_queued_never_started_and_ui_and_format_restored(self):
        self.project.fmt = {"format": "mp4", "codec": "H264"}
        dry, real = run_twice("queue_render", self.args(), self.resolve)
        self.assertEqual(dry["output"], os.path.join(self.dir, "Edit_master.mov"))
        self.assertIn("TargetDir", dry["deliver_changed"])
        self.assertEqual(real["job"]["TimelineName"], "Edit")
        self.assertEqual(real["job"]["VideoFormat"], "mov")
        self.assertEqual(real["readback_problems"], [])
        self.assertIn("NOT started", real["summary"])
        self.assertEqual(self.project.fmt, {"format": "mp4", "codec": "H264"})
        self.assertEqual(self.ui(), ("cut", self.here))
        self.assertNotIn("StartRendering", [c[1] for c in rf.CALLS])
        self.assertEqual(len(self.project.jobs), 1)

    def test_unset_deliver_format_is_reported(self):
        self.project.fmt = {"format": "unknown", "codec": ""}
        dry, real = run_twice("queue_render", self.args(), self.resolve)
        self.assertTrue(any("no format set" in w for w in real["warnings"]))

    def test_refusals(self):
        open(os.path.join(self.dir, "Edit_master.mov"), "w").close()
        with self.assertRaisesRegex(workflows.Refused, "already exists"):
            call("queue_render", self.args(), self.resolve)
        self.project.rendering = True
        with self.assertRaisesRegex(workflows.Refused, "render is running"):
            call("queue_render", self.args(custom_name="other"), self.resolve)
        self.project.rendering = False
        repo = os.path.join(self.dir, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        with self.assertRaises(paths.OutputRefused):
            call("queue_render", self.args(output_dir=repo), self.resolve)
        with self.assertRaisesRegex(workflows.Refused, "not an existing folder"):
            call("queue_render", self.args(output_dir=os.path.join(self.dir, "nope")),
                 self.resolve)
        with self.assertRaises(AssertionError):
            call("queue_render", self.args(custom_name="../escape"), self.resolve)
        with self.assertRaises(AssertionError):
            call("queue_render", self.args(preset="nope"), self.resolve)
        self.assertEqual(self.project.jobs, [])


if __name__ == "__main__":
    unittest.main()
