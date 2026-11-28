"""
Tests for transform_apply (rpresolve.workflows and rpresolve.mcp.tools_write)
against the shared Resolve fakes: rotation, zoom, pan and tilt from an
explicit list written onto an [auto] timeline's items, dry run then
plan_sha, every item read back with its timeline current, a journal line,
the refusals (not [auto], an index not on the track or given twice, an entry
with nothing to set), the quarter-turn warning, and a change on another
timeline using the same media reported as a leak.

Run: /usr/bin/python3 -m unittest tests.test_transform_apply
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import resolve_fakes as rf  # noqa: E402
import test_reframe_apply as tra  # noqa: E402
from rpresolve import api, workflows  # noqa: E402
from rpresolve.mcp import schema  # noqa: E402

call = tra.call
SIDEWAYS = {"index": 1, "rotation": 90, "zoom": 2.25}


class TestTransformApply(tra.Base):
    def args(self, items=(SIDEWAYS,), **kw):
        return {"timeline": "Look [auto]", "items": [dict(e) for e in items], **kw}

    def test_dry_run_writes_nothing_and_names_what_it_would_set(self):
        dry = call("transform_apply", self.args(), self.resolve)
        self.assertTrue(dry["dry_run"])
        self.assertEqual([t["name"] for t in dry["targets"]], ["STAND"])
        self.assertEqual(dry["targets"][0]["props"],
                         {"RotationAngle": 90.0, "ZoomX": 2.25, "ZoomY": 2.25})
        self.assertEqual(dry["warnings"], [])
        self.assertEqual(len(dry["plan_sha"]), 64)
        self.assertEqual(rf.mutating_calls(), [])
        self.assertEqual(self.events(), [])
        self.assertIn("would set the transform of 1 item(s)", dry["summary"])

    def test_real_run_sets_reads_back_and_journals(self):
        items = (SIDEWAYS, {"index": 2, "rotation": 180, "tilt": -150})
        dry = call("transform_apply", self.args(items), self.resolve)
        real = call("transform_apply", self.args(items, dry_run=False, plan_sha=dry["plan_sha"]),
                    self.resolve)
        self.assertEqual([r["ok"] for r in real["results"]], [True, True])
        self.assertEqual(self.items[0].props["RotationAngle"], 90.0)
        self.assertEqual((self.items[0].props["ZoomX"], self.items[0].props["ZoomY"]), (2.25, 2.25))
        self.assertEqual((self.items[1].props["RotationAngle"], self.items[1].props["Tilt"]),
                         (180.0, -150.0))
        self.assertEqual(self.items[1].props["ZoomX"], 1.0)  # zoom not given: left alone
        self.assertEqual(real["leaks"], [])
        self.assertEqual(real["exit_status"], 0)
        self.assertIn("no other timeline changed", real["summary"])
        self.assertEqual([e["event"] for e in self.events()], ["started", "finished"])
        self.assertEqual(self.events()[-1]["tool"], "transform_apply")
        again = call("transform_apply", self.args(items), self.resolve)
        self.assertEqual(again["targets"], [])
        self.assertEqual({u["why"] for u in again["unchanged"]},
                         {"the item already carries this transform"})

    def test_the_timeline_is_current_while_its_items_are_written(self):
        self.project.current = self.edit
        dry = call("transform_apply", self.args(), self.resolve)
        seen, write = [], api.set_property_checked
        with mock.patch.object(api, "set_property_checked", side_effect=lambda *a: (
                seen.append(self.project.current.name), write(*a))[1]):
            call("transform_apply", self.args(dry_run=False, plan_sha=dry["plan_sha"]),
                 self.resolve)
        self.assertEqual(set(seen), {"Look [auto]"})
        self.assertEqual(self.project.current, self.edit)

    def test_a_quarter_turn_below_fill_warns(self):
        dry = call("transform_apply", self.args([{"index": 1, "rotation": -90}]), self.resolve)
        self.assertEqual(len(dry["warnings"]), 1)
        self.assertIn("black at the sides below zoom 2.249", dry["warnings"][0])
        self.assertIn("1 warning(s)", dry["summary"])
        self.items[0].props["RotationAngle"] = 90.0  # turned already; a zoom alone is checked too
        dry = call("transform_apply", self.args([{"index": 1, "zoom": 1.2}]), self.resolve)
        self.assertEqual(len(dry["warnings"]), 1)

    def test_a_real_run_needs_the_plan_sha_and_the_same_values(self):
        with self.assertRaisesRegex(workflows.Refused, "plan_sha"):
            call("transform_apply", self.args(dry_run=False), self.resolve)
        dry = call("transform_apply", self.args(), self.resolve)
        with self.assertRaisesRegex(workflows.Refused, "changed since the dry run"):
            call("transform_apply", self.args([{"index": 1, "rotation": 91, "zoom": 2.25}],
                                              dry_run=False, plan_sha=dry["plan_sha"]),
                 self.resolve)
        self.assertEqual(rf.mutating_calls(), [])

    def test_refusals(self):
        with self.assertRaisesRegex(workflows.Refused, "not an \\[auto\\] timeline"):
            call("transform_apply", {**self.args(), "timeline": "Edit"}, self.resolve)
        with self.assertRaisesRegex(workflows.Refused, "no item\\(s\\) \\[3\\] on V1"):
            call("transform_apply", self.args([{"index": 3, "rotation": 90}]), self.resolve)
        with self.assertRaisesRegex(workflows.Refused, "given more than once"):
            call("transform_apply", self.args([SIDEWAYS, SIDEWAYS]), self.resolve)
        with self.assertRaisesRegex(workflows.Refused, "nothing to set"):
            call("transform_apply", self.args([{"index": 1}]), self.resolve)
        self.assertEqual(rf.mutating_calls(), [])

    def test_the_schema_bounds_the_values(self):
        tool = tra.REG.get("transform_apply")
        for bad in ({"index": 1, "rotation": 400}, {"index": 1, "zoom": 0},
                    {"index": 0, "rotation": 90}, {"index": 1, "spin": 90}):
            errs = schema.validate(tool.input_schema,
                                   {"project": tra.P, "timeline": "x", "items": [bad]})
            self.assertTrue(errs, bad)

    def test_a_change_on_another_timeline_is_reported_as_a_leak(self):
        self.edit_item.props = self.items[0].props
        dry = call("transform_apply", self.args(), self.resolve)
        real = call("transform_apply", self.args(dry_run=False, plan_sha=dry["plan_sha"]),
                    self.resolve)
        self.assertEqual(real["leaks"], ["Edit V1 #1"])
        self.assertEqual(real["exit_status"], 1)
        self.assertIn("TRANSFORM LEAKED to 1 item(s)", real["summary"])


if __name__ == "__main__":
    unittest.main()
