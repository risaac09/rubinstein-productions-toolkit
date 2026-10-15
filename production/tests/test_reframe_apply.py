"""
Tests for reframe_apply (rpresolve.workflows and rpresolve.mcp.tools_write)
against the shared Resolve fakes: a framing plan written onto an [auto]
timeline's items, dry run then plan_sha, every item read back, a journal
line, the refusals (not [auto], another size or input scaling, an item's own
Scaling, a resolution not planned, a zoom below fill), do-nothing items left
alone, and a change on another timeline using the same media reported as a
leak.

Run: /usr/bin/python3 -m unittest tests.test_reframe_apply
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
from rpresolve import reframe, workflows  # noqa: E402
from rpresolve.mcp import schema, server  # noqa: E402
from rpresolve.mcp.registry import ToolContext  # noqa: E402
from test_framing import MASTER, PYXIS, Reader, TestMasterShape, detector  # noqa: E402

REG = server.build_registry()
P = "RP Automation Sandbox"
STANDING = TestMasterShape.STANDING
SEATED = (2900.0, 1700.0, 3200.0, 2000.0)
ZERO = {"ZoomX": 1.0, "ZoomY": 1.0, "Pan": 0.0, "Tilt": 0.0, "RotationAngle": 0.0,
        "CropLeft": 0.0, "CropRight": 0.0, "CropTop": 0.0, "CropBottom": 0.0, "FlipX": False,
        "FlipY": False, "Scaling": 0}


def call(name, args, resolve):
    tool = REG.get(name)
    args = schema.coerce(tool.input_schema, {"project": P, **args})
    errors = schema.validate(tool.input_schema, args)
    if errors:
        raise AssertionError(errors)
    return tool.handler(schema.with_defaults(tool.input_schema, args),
                        ToolContext(session=rf.Session(resolve)))


def framing_plan(boxes, scaling=reframe.SCALE_TO_CROP):
    """A framing plan for /media/<name>.braw, one face box per clip."""
    items = [{"name": n, "path": f"/media/{n}.braw", "in": 0.0, "out": 10.0} for n in boxes]
    by_path = {f"/media/{n}.braw": b for n, b in boxes.items()}

    def detect_for(it):
        return detector(by_path[it["path"]], PYXIS)
    plans = [reframe.plan_items([it], aspect=reframe.MASTER, master_size=MASTER, scaling=scaling,
                                reader_for=lambda _: Reader(PYXIS), detect=detect_for(it))
             for it in items]
    out = dict(plans[0], items=[p["items"][0] for p in plans], rows=[p["rows"][0] for p in plans])
    return out


class Base(unittest.TestCase):
    def setUp(self):
        rf.CALLS.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.realpath(self.tmp.name)
        self.journal = os.path.join(self.dir, "writes.jsonl")
        env = mock.patch.dict(os.environ, {"RPRESOLVE_MCP_JOURNAL": self.journal,
                                           "RPRESOLVE_LOCK": os.path.join(self.dir, "lock")})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.tmp.cleanup)
        clip_props = {"Resolution": "6048x4032", "FPS": "30"}
        self.stand = rf.Clip("STAND", "clip-17", dict(clip_props, **{"File Path": "/media/STAND.braw"}))
        self.seat = rf.Clip("SEAT", "clip-10", dict(clip_props, **{"File Path": "/media/SEAT.braw"}))
        self.items = [rf.Item("STAND", 86400, 86700, self.stand, props=ZERO, source=(0, 300)),
                      rf.Item("SEAT", 86700, 87000, self.seat, props=ZERO, source=(0, 300))]
        settings = {"timelineResolutionWidth": "5952", "timelineResolutionHeight": "2646",
                    "timelineInputResMismatchBehavior": "scaleToCrop"}
        self.look = rf.Timeline("Look [auto]", "tl-look", 30.0, 86400, 87000,
                                {"video": [self.items], "audio": [[]]}, settings=settings)
        self.edit_item = rf.Item("STAND", 86400, 86700, self.stand, props=ZERO, source=(0, 300))
        self.edit = rf.Timeline("Edit", "tl-edit", 30.0, 86400, 86700,
                                {"video": [[self.edit_item]], "audio": [[]]}, settings=settings)
        self.project = rf.Project(timelines=[self.look, self.edit], current=self.look)
        self.resolve = rf.Resolve(self.project)
        self.plan_path = self.write_plan(framing_plan({"STAND": STANDING, "SEAT": SEATED}))

    def write_plan(self, plan, name="framing.json"):
        path = os.path.join(self.dir, name)
        with open(path, "w") as f:
            json.dump(plan, f)
        return path

    def args(self, **kw):
        return {"timeline": "Look [auto]", "plan": self.plan_path, **kw}

    def events(self):
        if not os.path.exists(self.journal):
            return []
        with open(self.journal) as f:
            return [json.loads(line) for line in f]


class TestReframeApply(Base):
    def test_dry_run_writes_nothing_and_names_what_it_would_set(self):
        dry = call("reframe_apply", self.args(), self.resolve)
        self.assertTrue(dry["dry_run"])
        self.assertEqual([t["name"] for t in dry["targets"]], ["STAND"])
        self.assertEqual(dry["targets"][0]["move"], "tilt")
        self.assertLess(dry["targets"][0]["props"]["Tilt"], 0)
        self.assertEqual([(u["name"], u["why"]) for u in dry["unchanged"]],
                         [("SEAT", "centre fill already keeps the head")])
        self.assertEqual(len(dry["plan_sha"]), 64)
        self.assertEqual(rf.mutating_calls(), [])
        self.assertEqual(self.events(), [])
        self.assertIn("would set Zoom, Pan and Tilt on 1 item(s)", dry["summary"])

    def test_real_run_sets_reads_back_and_journals(self):
        dry = call("reframe_apply", self.args(), self.resolve)
        real = call("reframe_apply", self.args(dry_run=False, plan_sha=dry["plan_sha"]),
                    self.resolve)
        want = dry["targets"][0]["props"]
        self.assertEqual([r["ok"] for r in real["results"]], [True])
        self.assertEqual(real["results"][0]["read_back"], want)
        self.assertEqual({k: self.items[0].props[k] for k in want}, want)
        self.assertEqual({k: self.items[1].props[k] for k in want}, {k: ZERO[k] for k in want})
        self.assertEqual(real["leaks"], [])
        self.assertIn("no other timeline changed", real["summary"])
        self.assertEqual(real["exit_status"], 0)
        self.assertEqual([e["event"] for e in self.events()], ["started", "finished"])
        self.assertEqual(self.events()[-1]["status"], "ok")
        self.assertEqual(self.events()[-1]["tool"], "reframe_apply")
        # Run again: the item already carries the framing, so nothing is a target.
        again = call("reframe_apply", self.args(), self.resolve)
        self.assertEqual(again["targets"], [])
        self.assertEqual(sorted(u["why"] for u in again["unchanged"]),
                         ["centre fill already keeps the head",
                          "the item already carries this framing"])

    def test_a_real_run_needs_the_plan_sha_and_the_same_plan(self):
        with self.assertRaisesRegex(workflows.Refused, "plan_sha"):
            call("reframe_apply", self.args(dry_run=False), self.resolve)
        with self.assertRaisesRegex(workflows.Refused, "changed since the dry run"):
            call("reframe_apply", self.args(dry_run=False, plan_sha="0" * 64), self.resolve)
        self.assertEqual(rf.mutating_calls(), [])

    def test_only_auto_timelines(self):
        with self.assertRaisesRegex(workflows.Refused, "not an \\[auto\\] timeline"):
            call("reframe_apply", self.args(timeline="Edit"), self.resolve)

    def test_another_size_or_input_scaling_is_refused(self):
        self.look.settings["timelineInputResMismatchBehavior"] = "scaleToFit"
        with self.assertRaisesRegex(workflows.Refused, "the plan was made for 'scaleToCrop'"):
            call("reframe_apply", self.args(), self.resolve)
        self.look.settings.update(timelineInputResMismatchBehavior="scaleToCrop",
                                  timelineResolutionWidth="3840")
        with self.assertRaisesRegex(workflows.Refused, "the plan was made for 5952x2646"):
            call("reframe_apply", self.args(), self.resolve)

    def test_an_item_with_its_own_scaling_is_refused(self):
        self.items[0].props["Scaling"] = 3
        with self.assertRaisesRegex(workflows.Refused, "its own Scaling 3"):
            call("reframe_apply", self.args(), self.resolve)

    def test_per_item_refusals(self):
        self.seat.props["Resolution"] = "1920x1080"
        stranger = rf.Clip("X", "clip-x", {"File Path": "/media/X.mov", "Resolution": "6048x4032"})
        self.items.append(rf.Item("X", 87000, 87100, stranger, props=ZERO))
        dry = call("reframe_apply", self.args(), self.resolve)
        why = {r["name"]: r["reasons"][0] for r in dry["refused"]}
        self.assertIn("the clip is 1920x1080 and the plan was made on 6048x4032", why["SEAT"])
        self.assertEqual(why["X"], "no plan entry for its file")
        self.assertEqual(dry["exit_status"], 2)

    def test_a_zoom_below_the_fill_scale_needs_allow_bars(self):
        plan = framing_plan({"STAND": STANDING, "SEAT": SEATED})
        plan["items"][0]["entry"]["scale"] = 0.9
        path = self.write_plan(plan, "narrow.json")
        dry = call("reframe_apply", self.args(plan=path), self.resolve)
        self.assertIn("below the fill scale", dry["refused"][0]["reasons"][0])
        dry = call("reframe_apply", self.args(plan=path, allow_bars=True), self.resolve)
        self.assertEqual([t["name"] for t in dry["targets"]], ["STAND"])

    def test_a_change_on_another_timeline_is_reported_as_a_leak(self):
        # The Edit timeline's item shares its properties with the target (a Resolve that
        # applied the transform to every use of the clip would look like this).
        self.edit_item.props = self.items[0].props
        dry = call("reframe_apply", self.args(), self.resolve)
        real = call("reframe_apply", self.args(dry_run=False, plan_sha=dry["plan_sha"]),
                    self.resolve)
        self.assertEqual(real["leaks"], ["Edit V1 #1"])
        self.assertEqual(real["exit_status"], 1)
        self.assertIn("FRAMING LEAKED to 1 item(s)", real["summary"])

    def test_several_entries_for_a_file_go_by_source_range(self):
        # Two plan entries for STAND's file (0-4 s standing, 4-10 s seated); the item plays
        # frames 0-300 at 30 fps (0-10 s) and overlaps the second more, so it takes the
        # seated framing. An item whose range cannot be read is refused, not guessed.
        plan = framing_plan({"STAND": STANDING, "SEAT": SEATED})
        seated = framing_plan({"STAND": SEATED})["items"][0]
        plan["items"][0].update({"in": 0.0, "out": 4.0})
        plan["items"].append(dict(seated, **{"in": 4.0, "out": 10.0}))
        path = self.write_plan(plan, "two.json")
        dry = call("reframe_apply", self.args(plan=path), self.resolve)
        self.assertEqual([u["plan_item"] for u in dry["unchanged"] if u["name"] == "STAND"],
                         ["STAND"])
        self.assertEqual(next(u for u in dry["unchanged"] if u["name"] == "STAND")["why"],
                         "centre fill already keeps the head")
        self.items[0].source = (0, None)
        dry = call("reframe_apply", self.args(plan=path), self.resolve)
        self.assertIn("the plan has 2 entries for its file",
                      next(r for r in dry["refused"] if r["name"] == "STAND")["reasons"][0])

    def test_a_squeezed_entry_warns(self):
        plan = framing_plan({"STAND": STANDING, "SEAT": SEATED})
        plan["items"][0]["entry"]["squeeze"] = 1.5
        dry = call("reframe_apply", self.args(plan=self.write_plan(plan, "sq.json")), self.resolve)
        self.assertTrue(any("squeeze 1.5" in w for w in dry["warnings"]), dry["warnings"])
        self.assertIn("anamorphic transform is unchecked live", dry["summary"])

    def test_a_plan_that_is_not_a_framing_plan_is_refused(self):
        path = self.write_plan({"clips": []}, "manifest.json")
        with self.assertRaisesRegex(workflows.Refused, "not a framing plan"):
            call("reframe_apply", self.args(plan=path), self.resolve)


class TestReframePlanItems(unittest.TestCase):
    def test_the_offline_tool_writes_a_framing_plan(self):
        tool = REG.get("reframe_plan")
        with tempfile.TemporaryDirectory() as d:
            stills = []
            for i in range(3):
                p = os.path.join(d, f"STAND_f{i}.ppm")
                with open(p, "wb") as f:
                    f.write(b"P6 90 60 255\n" + bytes([128]) * (90 * 60 * 3))
                stills.append(p)
            out = os.path.join(d, "out", "framing.json")
            os.makedirs(os.path.dirname(out))
            args = {"items": [{"name": "STAND", "path": "/media/STAND.braw", "stills": stills,
                               "src": list(PYXIS)}],
                    "aspects": ["master"], "master_size": list(MASTER),
                    "input_scaling": "scaleToCrop", "out": out}
            self.assertEqual(schema.validate(tool.input_schema, args), [])
            with mock.patch.object(reframe.vision, "face_boxes", detector(STANDING, PYXIS)):
                r = tool.handler(schema.with_defaults(tool.input_schema, args), ToolContext())
            self.assertIn("framing plan for 1 item(s) at master 5952x2646", r["summary"])
            self.assertIn("1 pass with a move", r["summary"])
            with open(out) as f:
                plan = json.load(f)
            self.assertEqual((plan["kind"], plan["scaling"]), ("framing", "scaleToCrop"))
            self.assertEqual(plan["items"][0]["move"], "tilt")
            self.assertTrue(os.path.exists(r["tsv"]))
            for p in stills:
                self.assertTrue(os.path.exists(p))
            with self.assertRaisesRegex(ValueError, "not both"):
                tool.handler(schema.with_defaults(tool.input_schema,
                                                  dict(args, manifest=out)), ToolContext())


if __name__ == "__main__":
    unittest.main()
