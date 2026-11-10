"""
Tests for rpresolve.methods and the MCP edit_methods tool: every shipped method is
sound, validate names each kind of fault, and to_manifest's output is a version 2
manifest that interchange writes as FCPXML with the method's timing, dissolves and
scales, plus the effects apply_effect takes.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import copy
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rpresolve import cutlist, interchange, methods  # noqa: E402
from rpresolve.cmd_effect import WIRING  # noqa: E402
from rpresolve.mcp import server  # noqa: E402
from rpresolve.mcp.registry import ToolContext  # noqa: E402

IDS = ["beat-burst", "cutout-clones", "matte-title", "punch-montage", "scale-window-stack",
       "slow-cinematic", "tempo-ladder", "word-burst"]


def fills(m, gap=2.0):
    """One fill per slot, each from its own place in one of two made-up sources, with
    room for dissolve handles either side."""
    out, t = [], 5.0
    for i, s in enumerate(m["slots"]):
        out.append({"source_path": f"/nowhere/src{i % 2}.mov", "in": t, "out": t + s["seconds"]})
        t += s["seconds"] + gap
    return out


class TestShippedMethods(unittest.TestCase):
    def test_every_method_is_sound(self):
        ms = methods.list_methods()
        self.assertEqual([m["id"] for m in ms], IDS)
        for m in ms:
            self.assertEqual(methods.validate(m), [], m["id"])
            self.assertEqual(methods.load(m["id"]), m)

    def test_file_names_are_ids_and_effects_are_macros(self):
        for name in os.listdir(methods.METHOD_DIR):
            m = methods.load(os.path.join(methods.METHOD_DIR, name))
            self.assertEqual(name, m["id"] + ".json")
            for s in m["slots"]:
                self.assertTrue(s["effect"] is None or s["effect"] in WIRING)

    def test_unknown_method(self):
        with self.assertRaisesRegex(ValueError, "no edit method 'nope'"):
            methods.load("nope")


class TestValidate(unittest.TestCase):
    def setUp(self):
        self.m = methods.load("punch-montage")

    def problems(self, change):
        m = copy.deepcopy(self.m)
        change(m)
        return " | ".join(methods.validate(m))

    def test_faults_are_named(self):
        cases = [
            (lambda m: m.update(fits=["travel", "beach"]), "fits tag 'beach'"),
            (lambda m: m.update(aspect="1:1"), "aspect"),
            (lambda m: m.update(look="no-such-look"), "look"),
            (lambda m: m.update(bpm=[130, 100]), "bpm"),
            (lambda m: m.update(extra=1), "unknown field 'extra'"),
            (lambda m: m["rules"].update(end="fade:1"), "rules.end"),
            (lambda m: m.update(length_s=20), "length_s"),
            (lambda m: m["slots"][0].update(move="zoom"), "slot 1: move"),
            (lambda m: m["slots"][0].update(effect="glow"), "slot 1: effect"),
            (lambda m: m["slots"][0].update(role="the runner at the lake"), "slot 1: role must be one of"),
            (lambda m: m["slots"][0].update(speed=0.5, effect="zoom-push"), "takes no effect"),
            (lambda m: m["slots"][0].update(seconds=0.05), "slot 1: seconds"),
            (lambda m: m["slots"][0].update(transition_out={"type": "cut", "frames": 3}),
             "a cut has 0 frames"),
            (lambda m: m["slots"][-1].update(transition_out={"type": "dissolve", "frames": 2}),
             "last slot"),
            (lambda m: m["slots"][1].update(text="caption"), "slot 2: text"),
            (lambda m: m["slots"][1].pop("query"), "slot 2: missing field 'query'"),
        ]
        for change, want in cases:
            self.assertIn(want, self.problems(change))
        self.assertEqual(self.problems(lambda m: None), "")


class TestToManifest(unittest.TestCase):
    def test_round_trips_through_interchange(self):
        for mid in IDS:
            m = methods.load(mid)
            man = methods.to_manifest(m, fills(m))
            cutlist.validate_manifest(json.loads(json.dumps(man)), allow_no_speech=True)
            self.assertEqual(man["version"], 2)
            self.assertIs(man["speech"], False)
            spans = man["clips"][0]["spans"]
            self.assertEqual(len(spans), len(m["slots"]))
            text, report = interchange.build(man, "fcpxml")
            self.assertEqual(report["spans"], len(spans), mid)
            want = sum(cutlist.frame(s["out"], m["fps"]) - cutlist.frame(s["in"], m["fps"])
                       for s in spans)
            self.assertEqual(report["frames"], want, mid)
            dissolves = sum(1 for s in m["slots"] if s["transition_out"]["type"] == "dissolve")
            self.assertEqual(report["transitions"], dissolves, mid)
            self.assertEqual(text.count("<transition "), dissolves, mid)
            want = []
            for i, s in enumerate(m["slots"]):
                if s["effect"]:
                    want.append({"span_index": i, "effect": s["effect"]})
                if s["speed"] != 1:
                    want.append({"span_index": i, "effect": "retime",
                                 "params": {"speed": s["speed"]}})
            self.assertEqual(man["effects"], want, mid)

    def test_timing_scale_and_hand_steps(self):
        m = methods.load("punch-montage")
        man = methods.to_manifest(m, fills(m))
        spans = man["clips"][0]["spans"]
        self.assertEqual(spans[0]["out"] - spans[0]["in"], 1.71)
        self.assertEqual(spans[1]["scale"], 1.15)
        self.assertNotIn("scale", spans[0])
        self.assertEqual(man["method"]["end"], "black:0.7")
        steps = {(x["target"], x["control"]): x for x in man["method"]["ui_steps"]}
        self.assertNotIn(("span 7", "Speed"), steps)
        self.assertIn({"span_index": 7, "effect": "retime", "params": {"speed": 0.7}},
                      man["effects"])
        self.assertEqual(steps[("span 2", "Styled Text")]["page"], "Fusion")
        self.assertEqual(steps[("after span 14", "Color and Duration")]["value"],
                         "black, 0.7 s, after the last item")
        for x in man["method"]["ui_steps"]:
            self.assertEqual(set(x), {"target", "page", "panel", "control", "value", "why"})
        self.assertEqual([s["path"] for s in man["sources"]],
                         ["/nowhere/src0.mov", "/nowhere/src1.mov"])
        wipe = methods.to_manifest(methods.load("slow-cinematic"),
                                   fills(methods.load("slow-cinematic")))
        w = [x for x in wipe["method"]["ui_steps"] if x["target"] == "the cut into span 5"]
        self.assertEqual(w[0]["panel"], "Effects > Video Transitions > Wipe > Edge Wipe")
        self.assertEqual(w[0]["value"], "29 frames, centred on the cut")

    def test_a_named_word_goes_to_the_text_effect_as_a_param(self):
        m = methods.load("punch-montage")
        i = next(k for k, s in enumerate(m["slots"]) if s["text"] == "word")
        self.assertIn(m["slots"][i]["effect"], methods.TEXT_MACROS)
        f = fills(m)
        f[i]["text"] = "HOME"
        man = methods.to_manifest(m, f)
        self.assertIn({"span_index": i, "effect": m["slots"][i]["effect"],
                       "params": {"text": "HOME"}}, man["effects"])
        self.assertFalse([x for x in man["method"]["ui_steps"] if x["control"] == "Styled Text"])
        f[i]["text"] = " "
        with self.assertRaisesRegex(ValueError, "non-empty word"):
            methods.to_manifest(m, f)

    def test_the_matte_slot_gets_the_magic_mask_recipe_step(self):
        m = methods.load("matte-title")
        i = next(k for k, sl in enumerate(m["slots"]) if sl["effect"] == "person-matte-text")
        f = fills(m)
        f[i]["text"] = "HOME"
        man = methods.to_manifest(m, f)
        (step,) = [x for x in man["method"]["ui_steps"] if x["target"] == f"span {i}"]
        self.assertEqual((step["page"], step["panel"]),
                         ("Fusion", "comp rp-fx person-matte-text > SubjectMatte_PLACEHOLDER"))
        self.assertEqual(step["control"], "replace with a Person Magic Mask (Better)")
        for want in ("Track Forward and Reverse", "2 to 5 px", "Merge2.EffectMask",
                     "delete the placeholder", "get_image", "fusion-macros.md",
                     "untested live", "sandbox project first"):
            self.assertIn(want, step["value"])
        self.assertIn("in front of the subject", step["why"])

    def test_the_word_step_names_the_comp_of_the_slots_own_text_effect(self):
        m = methods.load("matte-title")
        i = next(k for k, sl in enumerate(m["slots"]) if sl["effect"] == "person-matte-text")
        man = methods.to_manifest(m, fills(m))  # no word in the fill
        panels = [x["panel"] for x in man["method"]["ui_steps"] if x["target"] == f"span {i}"]
        self.assertIn("comp rp-fx person-matte-text > Text1", panels)
        self.assertNotIn("comp rp-fx text-word > Text1", panels)
        m = methods.load("cutout-clones")
        i = next(k for k, sl in enumerate(m["slots"]) if sl["effect"] == "text-word")
        man = methods.to_manifest(m, fills(m))
        self.assertIn("comp rp-fx text-word > Text1",
                      [x["panel"] for x in man["method"]["ui_steps"] if x["target"] == f"span {i}"])

    def test_a_fill_gain_is_the_flash_params_gain(self):
        m = methods.load("punch-montage")
        i = next(k for k, sl in enumerate(m["slots"]) if sl["effect"] == "exposure-flash")
        f = fills(m)
        plain = methods.to_manifest(m, f)
        self.assertIn({"span_index": i, "effect": "exposure-flash"}, plain["effects"])
        f[i]["gain"] = 10
        man = methods.to_manifest(m, f)
        self.assertIn({"span_index": i, "effect": "exposure-flash", "params": {"gain": 10}},
                      man["effects"])
        self.assertEqual(len(man["effects"]), len(plain["effects"]))
        f[i]["gain"] = 1
        self.assertIn("gain", [e for e in methods.to_manifest(m, f)["effects"]
                               if e["span_index"] == i][0]["params"])

    def test_a_gain_outside_1_to_20_or_off_a_flash_slot_is_refused(self):
        m = methods.load("punch-montage")
        i = next(k for k, sl in enumerate(m["slots"]) if sl["effect"] == "exposure-flash")
        other = next(k for k, sl in enumerate(m["slots"]) if sl["effect"] != "exposure-flash")
        for bad in (0.5, 21, "2", True, float("nan")):
            f = fills(m)
            f[i]["gain"] = bad
            with self.assertRaisesRegex(ValueError, "a number from 1 to 20"):
                methods.to_manifest(m, f)
        f = fills(m)
        f[other]["gain"] = 2
        with self.assertRaisesRegex(ValueError, "gain is for an exposure-flash slot"):
            methods.to_manifest(m, f)

    def test_retime_and_reframe_pan_are_not_slot_effects(self):
        m = methods.load("punch-montage")
        for e in ("retime", "reframe-pan"):
            m["slots"][0]["effect"] = e
            self.assertIn("leave effect null", " | ".join(methods.validate(m)))

    def test_braw_sources_get_a_decode_recipe(self):
        m = methods.load("slow-cinematic")
        f = fills(m)
        for x in f[::2]:
            x["source_path"] = "/nowhere/A001_C002.BRAW"
        man = methods.to_manifest(m, f)
        braw = [x for x in man["method"]["ui_steps"] if x["control"] == "Decode Using"]
        self.assertEqual(len(braw), 1)
        self.assertEqual((braw[0]["page"], braw[0]["panel"], braw[0]["value"]),
                         ("Color", "Camera Raw palette", "Clip"))
        self.assertIn("never from Project Settings", braw[0]["why"])
        self.assertTrue(braw[0]["target"].startswith("every item from source s1"))
        plain = methods.to_manifest(m, fills(m))
        self.assertFalse(any(x["control"] == "Decode Using" for x in plain["method"]["ui_steps"]))

    def test_refusals(self):
        m = methods.load("punch-montage")
        f = fills(m)
        with self.assertRaisesRegex(ValueError, "one fill per slot"):
            methods.to_manifest(m, f[:-1])
        short = copy.deepcopy(f)
        short[0]["out"] = short[0]["in"] + 1.0
        with self.assertRaisesRegex(ValueError, "slot 1 .*holds 1.000 s"):
            methods.to_manifest(m, short)
        rel = copy.deepcopy(f)
        rel[0]["source_path"] = "src.mov"
        with self.assertRaisesRegex(ValueError, "absolute"):
            methods.to_manifest(m, rel)
        overlap = copy.deepcopy(f)
        overlap[2] = dict(overlap[0], out=overlap[0]["in"] + 4)
        with self.assertRaisesRegex(ValueError, "slot 3 .*: its fill overlaps slot 1"):
            methods.to_manifest(m, overlap)

    def test_reuse_marks_the_later_span(self):
        m = methods.load("word-burst")
        f = fills(m)
        f[-2] = dict(f[0], out=f[0]["in"] + 2)
        man = methods.to_manifest(m, f)
        self.assertTrue(man["clips"][0]["spans"][-2]["reuse"])
        cutlist.validate_manifest(man, allow_no_speech=True)


class TestTool(unittest.TestCase):
    def call(self, args):
        return server.build_registry().get("edit_methods").handler(args, ToolContext())

    def test_lists_and_returns_one(self):
        r = self.call({})
        self.assertTrue(r["ok"])
        self.assertEqual([x["id"] for x in r["methods"]], IDS)
        one = self.call({"id": "beat-burst"})
        self.assertEqual(one["method"]["aspect"], "9:16")

    def test_a_bad_file_is_named(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.json")
            with open(p, "w") as f:
                json.dump({"edit_method": 1}, f)
            r = self.call({"path": p})
            self.assertFalse(r["ok"])
            self.assertIn("missing field 'slots'", r["summary"])


class TestPrivacy(unittest.TestCase):
    def test_methods_and_macros_hold_no_links_or_handles(self):
        for d in (methods.METHOD_DIR, os.path.join(HERE.parent, "fusion-macros")):
            for name in os.listdir(d):
                text = Path(d, name).read_text(encoding="utf-8")
                self.assertIsNone(re.search(r"https?://|@[A-Za-z0-9_.]+|tiktok|/Users/", text,
                                            re.I), name)


if __name__ == "__main__":
    unittest.main()
