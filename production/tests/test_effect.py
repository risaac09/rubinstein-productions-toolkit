"""
Tests for apply_effect (rpresolve.cmd_effect and the MCP tool): the macros parse and
carry every tool their wiring names, the [auto]-only guard, dry run then plan_sha,
the journal, the comp built and wired between MediaIn1 and MediaOut1, keyframe
handles written as offsets, and a read-back that catches a handle overshoot.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import contextlib
import io
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import resolve_fakes as rf  # noqa: E402
from rpresolve import cmd_effect, workflows  # noqa: E402
from rpresolve.mcp import schema  # noqa: E402
from test_mcp_writes2 import REG, Base, call, run_twice  # noqa: E402


# --- a fake Fusion comp: tools, inputs, links and splines -------------------------

class Output:
    def __init__(self, tool):
        self.tool = tool

    def GetTool(self):
        return self.tool


class Input:
    def __init__(self, tool, key):
        self.tool, self.key = tool, key

    def GetConnectedOutput(self):
        src = self.tool.links.get(self.key)
        return Output(src) if src else None


class FTool:
    def __init__(self, comp, ctor, name):
        self.comp, self.ctor, self.Name = comp, ctor, name
        self.values, self.links, self.keys = {}, {}, {}

    def SetAttrs(self, attrs):
        self.comp.tools[attrs["TOOLS_Name"]] = self.comp.tools.pop(self.Name)
        self.Name = attrs["TOOLS_Name"]

    def SetInput(self, key, value):
        self.values[key] = value

    def ConnectInput(self, key, other):
        self.links[key] = other
        return True

    def __getitem__(self, key):
        return Input(self, key)

    def AddModifier(self, key, ctor):
        self.links[key] = self.comp.AddTool(ctor)
        return True

    def SetKeyFrames(self, keys, replace):
        self.keys = dict(keys)

    def GetKeyFrames(self):
        return {float(f): k for f, k in self.keys.items()}

    def GetInput(self, key, t):
        """A spline-driven input between two keys, with the handles read as offsets
        as Resolve reads them: the midpoint moves by the outgoing handle's value."""
        spline = self.links.get(key)
        if not spline or not spline.keys:
            return self.values.get(key)
        frames = sorted(spline.keys)
        for f0, f1 in zip(frames, frames[1:]):
            if f0 <= t <= f1:
                k0, k1 = spline.keys[f0], spline.keys[f1]
                return (k0[1] + k1[1]) / 2 + (k0.get("RH") or {}).get(2, 0)
        return spline.keys[frames[-1]][1]


class FComp:
    def __init__(self):
        self.tools, self.n = {}, 0
        self.AddTool("MediaIn", name="MediaIn1")
        self.AddTool("MediaOut", name="MediaOut1")

    def AddTool(self, ctor, x=None, y=None, name=None):
        self.n += 1
        t = FTool(self, ctor, name or f"{ctor}{self.n}")
        self.tools[t.Name] = t
        return t

    def FindTool(self, name):
        return self.tools.get(name)

    def Lock(self): pass
    def Unlock(self): pass


def give_fusion(item):
    """Fusion comps on a fake timeline item: none at first, as on a fresh item;
    AddFusionComp adds 'Composition N'."""
    item.comps = {}

    def add():
        rf.CALLS.append(("Item", "AddFusionComp", (item.uid,)))
        name = f"Composition {len(item.comps) + 1}"
        item.comps[name] = FComp()
        return item.comps[name]

    def rename(old, new):
        rf.CALLS.append(("Item", "RenameFusionCompByName", (old, new)))
        item.comps[new] = item.comps.pop(old)
        return True

    item.AddFusionComp = add
    item.RenameFusionCompByName = rename
    item.GetFusionCompNameList = lambda: list(item.comps)


class TestMacros(unittest.TestCase):
    def test_every_macro_file_has_wiring_and_parses(self):
        files = {p[:-len(".setting")] for p in os.listdir(cmd_effect.MACRO_DIR)
                 if p.endswith(".setting")}
        self.assertEqual(files, set(cmd_effect.WIRING))
        for effect in sorted(files):
            _, parsed = cmd_effect.load_macro(effect)
            self.assertEqual(parsed["Tools"]["__ctor"], "ordered", effect)

    def test_parse_setting(self):
        p = cmd_effect.parse_setting(
            '{ Tools = ordered() { T1 = Transform { Inputs = { Size = Input { Value = 1.5 },'
            ' Center = Input { Value = { 0.5, 0.45 } }, M = Input { Value = FuID { "None" } },'
            ' ["Gamut.X"] = Input { Value = "a\\"b" } } },'
            ' S = BezierSpline { KeyFrames = { [0] = { 1, RH = { 16, 1 } }, [48] = { 1.15 } } } } }')
        t = p["Tools"]["T1"]
        self.assertEqual(t["__ctor"], "Transform")
        self.assertEqual(t["Inputs"]["Size"]["Value"], 1.5)
        self.assertEqual(t["Inputs"]["Center"]["Value"], {1: 0.5, 2: 0.45})
        self.assertEqual(cmd_effect._value(t["Inputs"]["M"]["Value"]), "None")
        self.assertEqual(t["Inputs"]["Gamut.X"]["Value"], 'a"b')
        self.assertEqual(p["Tools"]["S"]["KeyFrames"][0], {1: 1, "RH": {1: 16, 2: 1}})
        self.assertEqual(list(p["Tools"]), ["T1", "S", "__ctor"])
        with self.assertRaises(ValueError):
            cmd_effect.parse_setting("{ A = { 1, }")

    def test_handles_become_offsets(self):
        rel = cmd_effect.relative_handles({0: {1: 1, "RH": {1: 16, 2: 1}},
                                           48: {1: 1.15, "LH": {1: 32, 2: 1.15}}})
        self.assertEqual(rel[0]["RH"], {1: 16, 2: 0})
        self.assertEqual(rel[48]["LH"], {1: -16, 2: 0})

    def test_unknown_effect_is_refused(self):
        with self.assertRaisesRegex(workflows.Refused, "no macro 'nope'"):
            cmd_effect.load_macro("nope")
        tool = REG.get("apply_effect")
        self.assertTrue(schema.validate(tool.input_schema,
                                        {"project": "P", "timeline": "T", "effect": "nope"}))

    def test_the_command_lists_every_macro(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cmd_effect.run(None), 0)


class TestApplyEffect(Base):
    def setUp(self):
        super().setUp()
        self.auto = self.make_auto()
        self.items = self.auto.tracks["video"][0]
        for it in self.items + self.origin.tracks["video"][0]:
            give_fusion(it)
        rf.CALLS.clear()

    def fx(self, args=None, **more):
        return call("apply_effect", {"timeline": "Edit [auto]", "effect": "zoom-push",
                                     **(args or {}), **more}, self.resolve)

    def added(self):
        return [c for c in rf.CALLS if c[1] == "AddFusionComp"]

    def test_only_auto_timelines(self):
        with self.assertRaisesRegex(workflows.Refused, "not an \\[auto\\]"):
            self.fx(timeline="Edit")
        with self.assertRaisesRegex(workflows.Refused, "not an \\[auto\\]"):
            self.fx(timeline="Edit", dry_run=False, plan_sha="0" * 64)
        self.assertEqual(self.added(), [])
        self.assertEqual(rf.mutating_calls(), [])

    def test_dry_run_plans_and_touches_nothing(self):
        dry = self.fx()
        self.assertEqual(len(dry["plan_sha"]), 64)
        self.assertEqual([t["index"] for t in dry["targets"]], [1, 2])
        self.assertEqual(dry["effect"]["comp"], "rp-fx zoom-push")
        self.assertEqual(dry["effect"]["wiring"], {"in": ["Transform1.Input"], "out": "Transform1"})
        self.assertIn(dry["plan_sha"], dry["summary"])
        self.assertEqual(self.added(), [])
        self.assertEqual(self.journal(), ["started", "finished"])  # make_auto's, nothing more

    def test_a_real_run_needs_a_current_plan_sha(self):
        with self.assertRaisesRegex(workflows.Refused, "needs the plan_sha"):
            self.fx(dry_run=False)
        dry = self.fx()
        with self.assertRaisesRegex(workflows.Refused, "plan changed"):
            self.fx(effect="dip-to-black", dry_run=False, plan_sha=dry["plan_sha"])
        self.assertEqual(self.added(), [])

    def test_real_run_builds_wires_and_reads_back(self):
        dry, real = run_twice("apply_effect", {"timeline": "Edit [auto]", "effect": "zoom-push",
                                               "items": [2]}, self.resolve)
        self.assertEqual(len(self.added()), 1)
        self.assertEqual([r["ok"] for r in real["results"]], [True])
        self.assertEqual(real["exit_status"], 0)
        comp = self.items[1].comps["rp-fx zoom-push"]
        self.assertIs(comp.FindTool("Transform1").links["Input"], comp.FindTool("MediaIn1"))
        self.assertIs(comp.FindTool("MediaOut1").links["Input"], comp.FindTool("Transform1"))
        self.assertEqual(comp.FindTool("Transform1Size").keys[0]["RH"], {1: 16, 2: 0})
        self.assertNotIn("rp-fx zoom-push", self.items[0].comps)
        self.assertEqual(self.journal(), ["started", "finished"] * 2)
        again = self.fx(items=[2, 1])
        self.assertEqual([r["index"] for r in again["refused"]], [2])
        self.assertEqual([t["index"] for t in again["targets"]], [1])
        with self.assertRaisesRegex(workflows.Refused, "already has Fusion comp"):
            self.fx(items=[2])

    def test_an_item_with_any_comp_is_refused(self):
        self.items[0].comps["Composition 1"] = FComp()
        dry = self.fx()
        self.assertEqual([r["index"] for r in dry["refused"]], [1])
        self.assertIn("would replace the one that renders", dry["refused"][0]["reasons"][0])

    def test_dip_needs_a_readable_length(self):
        self.items[0].GetDuration = lambda: None
        dry = self.fx(effect="dip-to-black")
        self.assertEqual([r["index"] for r in dry["refused"]], [1])
        self.assertIn("length cannot be read", dry["refused"][0]["reasons"][0])

    def test_a_failure_mid_run_names_the_items_done(self):
        dry = self.fx()
        def boom():
            raise RuntimeError("link dropped")
        self.items[1].AddFusionComp = boom
        with self.assertRaisesRegex(RuntimeError, "link dropped") as ctx:
            self.fx(dry_run=False, plan_sha=dry["plan_sha"])
        self.assertEqual([(r["index"], r["ok"]) for r in ctx.exception.effects_added],
                         [(1, True), (2, False)])
        self.assertEqual(self.journal()[-2:], ["started", "finished"])

    def test_a_missing_tool_is_named_not_raised(self):
        _, parsed = cmd_effect.load_macro("text-word")
        comp = FComp()
        real_add = comp.AddTool
        comp.AddTool = lambda ctor, *a, **k: None if ctor == "Merge" else real_add(ctor, *a, **k)
        skipped, animated = cmd_effect._build(comp, parsed)
        cmd_effect._wire(comp, "text-word")
        problems = cmd_effect._read_back(comp, "text-word", parsed, animated)
        self.assertIn("Merge1 missing", problems)
        self.assertTrue(any("AddTool Merge failed" in x for x in skipped))

    def test_two_input_wiring_and_skipped_values(self):
        _, real = run_twice("apply_effect", {"timeline": "Edit [auto]", "items": [1],
                                             "effect": "person-matte-text"}, self.resolve)
        comp = self.items[0].comps["rp-fx person-matte-text"]
        media_in = comp.FindTool("MediaIn1")
        self.assertIs(comp.FindTool("Merge1").links["Background"], media_in)
        self.assertIs(comp.FindTool("Merge2").links["Foreground"], media_in)
        self.assertIs(comp.FindTool("Merge2").links["EffectMask"],
                      comp.FindTool("SubjectMatte_PLACEHOLDER"))
        self.assertEqual(comp.FindTool("Text1").values["Font"], "Arial")
        self.assertTrue(real["results"][0]["ok"])
        _, real = run_twice("apply_effect", {"timeline": "Edit [auto]", "items": [2],
                                             "effect": "exposure-flash"}, self.resolve)
        self.assertEqual(real["results"][0]["skipped"], ["ColorCorrector1.ColorRanges"])

    def test_dip_to_black_ends_on_the_items_last_frame(self):
        dry, real = run_twice("apply_effect", {"timeline": "Edit [auto]", "items": [1],
                                               "effect": "dip-to-black"}, self.resolve)
        frames = self.items[0].end - self.items[0].start
        self.assertEqual(dry["targets"][0]["key_offset"], frames - 1 - 167)
        comp = self.items[0].comps["rp-fx dip-to-black"]
        self.assertEqual(max(comp.FindTool("BrightnessContrast1Gain").keys), frames - 1)
        self.assertTrue(real["results"][0]["ok"], real["results"])
        zoom = self.fx(items=[2])
        self.assertEqual(zoom["targets"][0]["key_offset"], 0)

    def test_read_back_catches_absolute_handles(self):
        _, parsed = cmd_effect.load_macro("zoom-push")
        comp = FComp()
        _, animated = cmd_effect._build(comp, parsed)
        cmd_effect._wire(comp, "zoom-push")
        self.assertEqual(cmd_effect._read_back(comp, "zoom-push", parsed, animated), [])
        comp.FindTool("Transform1Size").keys = parsed["Tools"]["Transform1Size"]["KeyFrames"]
        problems = cmd_effect._read_back(comp, "zoom-push", parsed, animated)
        self.assertTrue(any("outside 1 to 1.15" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
