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

IDS = ["beat-burst", "cutout-clones", "punch-montage", "scale-window-stack",
       "slow-cinematic", "word-burst"]


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
            self.assertEqual(man["effects"],
                             [{"span_index": i, "effect": s["effect"]}
                              for i, s in enumerate(m["slots"]) if s["effect"]], mid)

    def test_timing_scale_and_hand_steps(self):
        m = methods.load("punch-montage")
        man = methods.to_manifest(m, fills(m))
        spans = man["clips"][0]["spans"]
        self.assertEqual(spans[0]["out"] - spans[0]["in"], 1.71)
        self.assertEqual(spans[1]["scale"], 1.15)
        self.assertNotIn("scale", spans[0])
        self.assertEqual(man["method"]["end"], "black:0.7")
        steps = " ".join(man["method"]["hand_steps"])
        self.assertIn("span 7: retime to 70 percent", steps)
        self.assertIn("span 2: set the word", steps)
        self.assertEqual([s["path"] for s in man["sources"]],
                         ["/nowhere/src0.mov", "/nowhere/src1.mov"])
        wipe = methods.to_manifest(methods.load("slow-cinematic"),
                                   fills(methods.load("slow-cinematic")))
        self.assertIn("span 5: a 29-frame wipe", " ".join(wipe["method"]["hand_steps"]))

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
