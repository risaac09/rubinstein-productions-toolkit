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
        self.attrs, self.toggles = {}, []

    @property
    def ID(self):
        return self.ctor

    def SetAttrs(self, attrs):
        self.attrs.update(attrs)
        if "TOOLB_PassThrough" in attrs:
            self.toggles.append(attrs["TOOLB_PassThrough"])
        if "TOOLS_Name" in attrs:
            self.comp.tools[attrs["TOOLS_Name"]] = self.comp.tools.pop(self.Name)
            self.Name = attrs["TOOLS_Name"]

    def GetAttrs(self, key):
        return self.attrs.get(key)

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
        if self.keys and min(self.keys) < 0 and 0 not in self.keys:
            # as Fusion does (T25): a key at frame 0 when the first key sits before it
            v = self.keys[min(self.keys)][1]
            self.keys[0] = {1: v, "LH": {1: -1, 2: 0}, "RH": {1: 1, 2: 0}}

    def GetKeyFrames(self):
        return {float(f): k for f, k in self.keys.items()}

    def GetInput(self, key, t=None):
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

    def GetToolList(self, selected=False):
        return dict(enumerate(self.tools.values(), 1))

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

    def test_keys_are_scaled_to_source_frames(self):
        """Comp time counts source frames: a 30 fps clip on a 24 fps timeline runs the
        macro's keys 1.25 times as far apart, and the dip still ends on the last frame."""
        _, parsed = cmd_effect.load_macro("dip-to-black")
        self.assertEqual(cmd_effect.comp_timing(parsed, "dip-to-black", 72, 90),
                         (1.25, 90 - 1 - round(167 * 1.25)))
        self.assertEqual(cmd_effect.comp_timing(parsed, "dip-to-black", 72, None), (1, 72 - 1 - 167))
        macro = cmd_effect.shifted(cmd_effect.scaled(parsed, 1.25), 89 - 209)
        keys = macro["Tools"]["BrightnessContrast1Gain"]["KeyFrames"]
        self.assertEqual(max(keys), 89)
        fade = sorted(keys)[1:]
        self.assertEqual(fade[-1] - fade[0], round(167 * 1.25) - round(149 * 1.25))
        # A handle moves with its key: still one scaled frame before it.
        self.assertAlmostEqual(keys[89]["LH"][1], 89 - 1.25, places=6)
        _, zoom = cmd_effect.load_macro("zoom-push")
        self.assertIs(cmd_effect.scaled(zoom, 1), zoom)

    def test_read_back_catches_absolute_handles(self):
        _, parsed = cmd_effect.load_macro("zoom-push")
        comp = FComp()
        _, animated = cmd_effect._build(comp, parsed)
        cmd_effect._wire(comp, "zoom-push")
        self.assertEqual(cmd_effect._read_back(comp, "zoom-push", parsed, animated), [])
        comp.FindTool("Transform1Size").keys = parsed["Tools"]["Transform1Size"]["KeyFrames"]
        problems = cmd_effect._read_back(comp, "zoom-push", parsed, animated)
        self.assertTrue(any("outside 1 to 1.15" in p for p in problems), problems)


class TestRefresh(unittest.TestCase):
    def test_every_tool_is_toggled_on_then_off_except_media_and_modifiers(self):
        comp = FComp()
        for ctor in ("Transform", "BezierSpline", "XYPath", "PolyPath", "Merge"):
            comp.AddTool(ctor)
        done = cmd_effect.refresh(comp)
        self.assertEqual(done, ["Transform3", "Merge7"])
        for name in done:
            self.assertEqual(comp.FindTool(name).toggles, [True, False])
        for name in ("MediaIn1", "MediaOut1", "BezierSpline4", "XYPath5", "PolyPath6"):
            self.assertEqual(comp.FindTool(name).toggles, [], name)

    def test_read_back_fails_on_a_stuck_pass_through(self):
        _, parsed = cmd_effect.load_macro("zoom-push")
        comp = FComp()
        _, animated = cmd_effect._build(comp, parsed)
        cmd_effect._wire(comp, "zoom-push")
        cmd_effect.refresh(comp)
        self.assertEqual(cmd_effect._read_back(comp, "zoom-push", parsed, animated), [])
        comp.FindTool("Transform1").attrs["TOOLB_PassThrough"] = True
        self.assertIn("Transform1 is still pass-through",
                      cmd_effect._read_back(comp, "zoom-push", parsed, animated))


class TestParams(Base):
    def setUp(self):
        super().setUp()
        self.auto = self.make_auto()
        self.items = self.auto.tracks["video"][0]
        for it in self.items + self.origin.tracks["video"][0]:
            give_fusion(it)
            it.source = (100, 340)  # 240 source frames
        rf.CALLS.clear()

    def run_fx(self, effect, params, items=(1,)):
        return run_twice("apply_effect", {"timeline": "Edit [auto]", "effect": effect,
                                          "items": list(items), "params": params},
                         self.resolve)

    def comp(self, effect, i=0):
        return self.items[i].comps["rp-fx " + effect]

    def test_validation_refuses_before_any_write(self):
        bad = [("zoom-push", {"text": "x"}), ("retime", {}), ("retime", {"speed": 0}),
               ("retime", {"speed_to": 2}), ("text-word", {"centre": 1.5}),
               ("text-word", {"text": ""}), ("reframe-pan", {"pan_from": 0.4}),
               ("text-word", {"nope": 1}), ("exposure-flash", {"speed": 1})]
        for effect, params in bad:
            with self.assertRaises((workflows.Refused, AssertionError), msg=(effect, params)):
                call("apply_effect", {"timeline": "Edit [auto]", "effect": effect,
                                      "params": params}, self.resolve)
        tool = REG.get("apply_effect")
        self.assertTrue(schema.validate(tool.input_schema, {
            "project": "P", "timeline": "T", "effect": "retime", "params": {"bogus": 1}}))
        self.assertEqual(self.added_comps(), 0)

    def added_comps(self):
        return len([c for c in rf.CALLS if c[1] == "AddFusionComp"])

    def test_validate_params_refuses_by_kind_and_range(self):
        v = cmd_effect.validate_params
        self.assertEqual(v("text-word", {"centre": 0.5}), {"centre": 0.5})
        for effect, params in [("text-word", {"centre": 1.5}), ("zoom-push", {"text": "x"}),
                               ("retime", {"speed": -1}), ("retime", {"speed": True}),
                               ("reframe-pan", {"pan_from": 0.1}), ("retime", "fast")]:
            with self.assertRaises(workflows.Refused, msg=(effect, params)):
                v(effect, params)

    def test_params_change_the_plan_sha(self):
        a = call("apply_effect", {"timeline": "Edit [auto]", "effect": "text-word"}, self.resolve)
        b = call("apply_effect", {"timeline": "Edit [auto]", "effect": "text-word",
                                  "params": {"text": "HOME"}}, self.resolve)
        c = call("apply_effect", {"timeline": "Edit [auto]", "effect": "text-word",
                                  "params": {"text": "AWAY"}}, self.resolve)
        self.assertEqual(len({a["plan_sha"], b["plan_sha"], c["plan_sha"]}), 3)
        self.assertEqual(b["params"], {"text": "HOME"})

    def test_text_word_sets_and_reads_back_every_value(self):
        _, real = self.run_fx("text-word", {"text": "HOME", "size": 0.05, "centre": 0.33})
        r = real["results"][0]
        self.assertTrue(r["ok"], r)
        comp = self.comp("text-word")
        self.assertEqual(comp.FindTool("Text1").values["StyledText"], "HOME")
        self.assertEqual(comp.FindTool("Text1").values["Size"], 0.05)
        self.assertEqual(comp.FindTool("Merge1").values["Center"], {1: 0.33, 2: 0.5})
        self.assertEqual(r["params"], {"Text1.StyledText": "HOME", "Text1.Size": 0.05,
                                       "Merge1.Center": {1: 0.33, 2: 0.5}})
        self.assertEqual(comp.FindTool("Text1").toggles, [True, False])

    def test_zoom_push_centre_sets_the_pivot(self):
        _, real = self.run_fx("zoom-push", {"centre": 0.4})
        self.assertTrue(real["results"][0]["ok"])
        self.assertEqual(self.comp("zoom-push").FindTool("Transform1").values["Pivot"],
                         {1: 0.4, 2: 0.5})

    def test_a_param_that_does_not_read_back_fails_the_item(self):
        _, parsed = cmd_effect.load_macro("text-word")
        macro, checks = cmd_effect.apply_params(parsed, "text-word", {"text": "HOME"}, 240)
        comp = FComp()
        _, animated = cmd_effect._build(comp, macro)
        comp.FindTool("Text1").values["StyledText"] = "OTHER"
        _, problems = cmd_effect._read_params(comp, checks)
        self.assertEqual(len(problems), 1)
        self.assertIn("StyledText", problems[0])

    def test_retime_constant_keys_are_offsets(self):
        _, real = self.run_fx("retime", {"speed": 0.7})
        self.assertTrue(real["results"][0]["ok"], real["results"])
        comp = self.comp("retime")
        keys = comp.FindTool("TimeStretcher1SourceTime").keys
        self.assertEqual(sorted(keys), [0, 240])
        self.assertAlmostEqual(keys[240][1], 168.0)
        self.assertAlmostEqual(keys[0]["RH"][1], 80.0)
        self.assertAlmostEqual(keys[0]["RH"][2], 56.0)
        self.assertAlmostEqual(keys[240]["LH"][1], -80.0)
        self.assertAlmostEqual(keys[240]["LH"][2], -56.0)
        self.assertEqual(comp.FindTool("TimeStretcher1").values["InterpolateBetweenFrames"], 0)
        self.assertIs(comp.FindTool("TimeStretcher1").links["Input"], comp.FindTool("MediaIn1"))
        self.assertIs(comp.FindTool("MediaOut1").links["Input"], comp.FindTool("TimeStretcher1"))
        self.assertAlmostEqual(real["results"][0]["params"]["TimeStretcher1SourceTime@240"], 168.0)

    def test_retime_ramp_is_the_hermite_of_a_linear_change(self):
        self.run_fx("retime", {"speed": 0.5, "speed_to": 1.5})
        keys = self.comp("retime").FindTool("TimeStretcher1SourceTime").keys
        self.assertAlmostEqual(keys[240][1], 240.0)  # (0.5 + 1.5) / 2 * 240
        self.assertAlmostEqual(keys[0]["RH"][2], 0.5 * 240 / 3)
        self.assertAlmostEqual(keys[240]["LH"][2], -1.5 * 240 / 3)
        self.assertAlmostEqual(keys[0]["RH"][1], 80.0)

    def test_exposure_flash_gain_defaults_to_two_and_applies_when_given(self):
        self.run_fx("exposure-flash", {})
        keys = self.comp("exposure-flash").FindTool("ColorCorrector1MasterRGBGain").keys
        self.assertEqual(keys[0][1], 2)
        _, real = self.run_fx("exposure-flash", {"gain": 10}, items=(2,))
        self.assertTrue(real["results"][0]["ok"], real["results"])
        self.assertEqual(real["results"][0]["params"]["ColorCorrector1MasterRGBGain@0"], 10)
        keys = self.comp("exposure-flash", 1).FindTool("ColorCorrector1MasterRGBGain").keys
        self.assertEqual(keys[0][1], 10)
        self.assertEqual(keys[0]["RH"][2], 0)  # level handle, as an offset
        self.assertEqual(keys[max(keys)][1], 1)

    def test_exposure_flash_gain_is_validated(self):
        v = cmd_effect.validate_params
        self.assertEqual(v("exposure-flash", {"gain": 20}), {"gain": 20})
        for effect, params in [("exposure-flash", {"gain": 0.5}), ("exposure-flash", {"gain": 21}),
                               ("exposure-flash", {"gain": True}),
                               ("exposure-flash", {"gain": float("nan")}),
                               ("zoom-push", {"gain": 3})]:
            with self.assertRaises(workflows.Refused, msg=(effect, params)):
                v(effect, params)

    def test_retime_needs_a_readable_source_length(self):
        self.items[0].source = (0, None)
        with self.assertRaisesRegex(workflows.Refused, "source frames"):
            call("apply_effect", {"timeline": "Edit [auto]", "effect": "retime",
                                  "items": [1], "params": {"speed": 2}}, self.resolve)

    def test_reframe_pan_keys_run_linearly_from_a_to_b(self):
        _, real = self.run_fx("reframe-pan", {"pan_from": 0.6, "pan_to": 0.33, "pan_base": 0.45})
        self.assertTrue(real["results"][0]["ok"], real["results"])
        comp = self.comp("reframe-pan")
        keys = comp.FindTool("XYPath1X").keys
        a, b = 0.5 + 0.45 - 0.6, 0.5 + 0.45 - 0.33
        self.assertAlmostEqual(keys[0][1], a)
        self.assertAlmostEqual(keys[240][1], b)
        self.assertAlmostEqual(keys[0]["RH"][1], 80.0)
        self.assertAlmostEqual(keys[0]["RH"][2], (b - a) / 3)
        self.assertAlmostEqual(keys[240]["LH"][1], -80.0)
        self.assertAlmostEqual(keys[240]["LH"][2], -(b - a) / 3)
        self.assertEqual(comp.FindTool("XYPath1").values["Y"], 0.5)
        self.assertIs(comp.FindTool("Transform1").links["Center"], comp.FindTool("XYPath1"))
        self.assertIs(comp.FindTool("MediaOut1").links["Input"], comp.FindTool("Transform1"))


class TestClipNumbering(Base):
    """A dissolve is a track item with no media pool clip (T29). Items are clip numbers and
    spans are manifest indices, as vertical_apply counts; the raw track position is kept."""

    def setUp(self):
        super().setUp()
        self.auto = self.make_auto()
        self.track = self.auto.tracks["video"][0]
        self.a, self.b, self.c = (rf.Item(n, 86400 + 50 * i, 86450 + 50 * i, self.clip, source=(0, 50))
                                  for i, n in enumerate(("A", "B", "C")))
        self.dissolve = rf.Item("Cross Dissolve", 86448, 86452, None, nodes=None, source=(0, 0))
        self.track[:] = [self.a, self.dissolve, self.b, self.c]
        for it in self.track:
            give_fusion(it)
        rf.CALLS.clear()

    def fx(self, **more):
        return call("apply_effect", {"timeline": "Edit [auto]", "effect": "zoom-push", **more},
                    self.resolve)

    def added(self):
        return [c for c in rf.CALLS if c[1] == "AddFusionComp"]

    def test_items_count_clips_and_skip_the_dissolve(self):
        dry = self.fx(items=[3])
        self.assertEqual([t["name"] for t in dry["targets"]], ["C"])  # the old count gave B
        self.assertEqual([(t["index"], t["position"]) for t in dry["targets"]], [(3, 4)])
        _, real = run_twice("apply_effect", {"timeline": "Edit [auto]", "effect": "zoom-push",
                                             "items": [3]}, self.resolve)
        self.assertEqual([(r["index"], r["position"], r["ok"]) for r in real["results"]],
                         [(3, 4, True)])
        self.assertIn("rp-fx zoom-push", self.c.comps)
        self.assertEqual(self.b.comps, {})

    def test_spans_are_the_manifests_own_indices(self):
        dry = self.fx(spans=[2])
        self.assertEqual([t["name"] for t in dry["targets"]], ["C"])
        self.assertEqual([(t["index"], t["position"]) for t in dry["targets"]], [(3, 4)])
        self.assertEqual(self.fx(spans=[0])["targets"][0]["name"], "A")
        _, real = run_twice("apply_effect", {"timeline": "Edit [auto]", "effect": "zoom-push",
                                             "spans": [2]}, self.resolve)
        self.assertEqual([r["ok"] for r in real["results"]], [True])
        self.assertIn("rp-fx zoom-push", self.c.comps)
        self.assertEqual(self.b.comps, {})

    def test_the_default_never_targets_the_transition(self):
        dry = self.fx()
        self.assertEqual([t["name"] for t in dry["targets"]], ["A", "B", "C"])
        self.assertEqual([(t["index"], t["position"]) for t in dry["targets"]],
                         [(1, 1), (2, 3), (3, 4)])
        run_twice("apply_effect", {"timeline": "Edit [auto]", "effect": "zoom-push"}, self.resolve)
        self.assertEqual(len(self.added()), 3)
        self.assertEqual(self.dissolve.comps, {})

    def test_items_and_spans_together_are_refused(self):
        with self.assertRaisesRegex(workflows.Refused, "not both"):
            self.fx(items=[1], spans=[0])
        self.assertEqual(self.added(), [])

    def test_a_number_past_the_clips_is_refused_in_its_own_terms(self):
        # four items on the track, three clips: raw position 4 is not a clip number 4
        with self.assertRaisesRegex(workflows.Refused, "no clip\\(s\\) \\[4\\].*3 clip"):
            self.fx(items=[4])
        with self.assertRaisesRegex(workflows.Refused, "no span\\(s\\) \\[3\\]"):
            self.fx(spans=[3])
        self.assertEqual(self.added(), [])

    def test_an_empty_spans_list_is_refused(self):
        with self.assertRaisesRegex(workflows.Refused, "spans is empty"):
            cmd_effect.apply_effect(self.resolve, "RP Automation Sandbox", "Edit [auto]",
                                    "zoom-push", spans=[], dry_run=True)

    def test_the_plan_hash_follows_the_numbering(self):
        by_items, by_spans = self.fx(items=[3]), self.fx(spans=[2])
        self.assertEqual(by_items["plan_sha"], by_spans["plan_sha"])  # one plan, two spellings
        self.assertNotEqual(by_items["plan_sha"], self.fx(items=[2])["plan_sha"])
        # the same clip, number and item, but the dissolve moved: the raw position changed
        self.track[:] = [self.a, self.b, self.c, self.dissolve]
        moved = self.fx(items=[3])
        self.assertEqual(moved["targets"][0]["item_id"], by_items["targets"][0]["item_id"])
        self.assertEqual(moved["targets"][0]["position"], 3)
        self.assertNotEqual(moved["plan_sha"], by_items["plan_sha"])

    def test_the_tool_schema_takes_spans_from_zero(self):
        tool = REG.get("apply_effect")
        base = {"project": "P", "timeline": "T", "effect": "zoom-push"}
        self.assertEqual(schema.validate(tool.input_schema, {**base, "spans": [0, 2]}), [])
        self.assertTrue(schema.validate(tool.input_schema, {**base, "spans": [-1]}))
        self.assertTrue(schema.validate(tool.input_schema, {**base, "items": [0]}))
        self.assertTrue(schema.validate(tool.input_schema, {**base, "spans": []}))

    def test_a_refused_clip_after_a_dissolve_keeps_both_numbers(self):
        self.c.comps["Composition 1"] = FComp()  # the third clip already has a comp
        dry = self.fx()
        self.assertEqual([(r["index"], r["position"], r["name"]) for r in dry["refused"]],
                         [(3, 4, "C")])
        self.assertEqual([t["name"] for t in dry["targets"]], ["A", "B"])
        with self.assertRaisesRegex(workflows.Refused, "#3 C: .*would replace"):
            self.fx(items=[3])
        self.assertEqual(self.added(), [])

    def test_the_count_is_per_track(self):
        x, y = (rf.Item(n, 86500 + 50 * i, 86550 + 50 * i, self.clip, source=(0, 50))
                for i, n in enumerate(("X", "Y")))
        gap = rf.Item("Cross Dissolve", 86548, 86552, None, nodes=None, source=(0, 0))
        self.auto.tracks["video"].append([x, gap, y])
        for it in (x, gap, y):
            give_fusion(it)
        dry = self.fx(track=2, items=[2])
        self.assertEqual([(t["name"], t["index"], t["position"]) for t in dry["targets"]],
                         [("Y", 2, 3)])
        self.assertEqual([t["name"] for t in self.fx(track=2, spans=[0])["targets"]], ["X"])
        self.assertEqual([t["name"] for t in self.fx(track=1, spans=[0])["targets"]], ["A"])

    def test_a_dissolve_at_either_end_of_the_track(self):
        lead = rf.Item("Lead", 86400, 86404, None, nodes=None, source=(0, 0))
        tail = rf.Item("Tail", 86550, 86554, None, nodes=None, source=(0, 0))
        self.track[:] = [lead, self.a, self.dissolve, self.b, self.c, tail]
        for it in (lead, tail):
            give_fusion(it)
        dry = self.fx(items=[3])
        self.assertEqual([(t["name"], t["index"], t["position"]) for t in dry["targets"]],
                         [("C", 3, 5)])
        self.assertEqual([t["name"] for t in self.fx()["targets"]], ["A", "B", "C"])
        with self.assertRaisesRegex(workflows.Refused, "no clip\\(s\\) \\[4\\]"):
            self.fx(items=[4])  # position 6 is the trailing dissolve, not a fourth clip


if __name__ == "__main__":
    unittest.main()
