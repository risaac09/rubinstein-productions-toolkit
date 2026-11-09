"""
Tests for the vertical command (rpresolve.cmd_vertical): centre choice (face, edge, hand),
clamping, one centre for a take, the review gate, the review sheet, and apply_vertical against
the shared Resolve fakes: the [auto]-only guard, refusals, dry run, plan_sha, Scaling 3, the
Pan formula, the pivot, centre and size of the two comp kinds, and the read-back.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import resolve_fakes as rf  # noqa: E402
from rpresolve import cmd_effect, cmd_vertical as v, reframe, workflows  # noqa: E402

SRC = [3840, 2160]
HW = v.half_width(SRC)


def item(name, path, in_, out, **kw):
    return {"name": name, "path": path, "in": in_, "out": out, "size": SRC, **kw}


def face_at(x, status="pass"):
    return lambda items, **kw: {"items": [{"status": status, "reason": "",
                                           "entry": {"x": x, "src": SRC}}]}


class TestPlan(unittest.TestCase):
    def test_hw_is_half_the_portrait_window(self):
        self.assertAlmostEqual(HW, 0.5 * 9 / 16 / (16 / 9))

    def test_face_centre_is_x_over_source_width(self):
        c = v.plan([item("a", "/m/a.mov", 0, 4)], planner=face_at(2400))
        r = c["items"][0]
        self.assertEqual((r["cx"], r["how"], r["status"]), (0.625, "face", "pass"))
        self.assertEqual((c["aspect"], c["reviewed"]), ("9x16", False))
        self.assertEqual(r["source_size"], SRC)

    def test_the_planner_is_asked_for_1080x1920_at_scale_to_crop(self):
        seen = {}

        def planner(items, **kw):
            seen.update(kw, items=items)
            return face_at(1920)(items)
        v.plan([item("a", "/m/a.mov", 1, 4)], planner=planner)
        self.assertEqual((seen["aspect"], seen["master_size"], seen["scaling"]),
                         ("9x16", [1080, 1920], "scaleToCrop"))
        self.assertEqual((seen["items"][0]["in"], seen["items"][0]["out"]), (1.0, 4.0))

    def test_a_fallback_passes_other_statuses_use_the_edge(self):
        c = v.plan([item("a", "/m/a.mov", 0, 4, crop_x=0.3)], planner=face_at(2400, "fallback"))
        self.assertEqual((c["items"][0]["how"], c["items"][0]["status"]), ("face", "fallback"))
        for status in ("manual", "no_face", "bars"):
            c = v.plan([item("a", "/m/a.mov", 0, 4, crop_x=0.3)], planner=face_at(2400, status))
            r = c["items"][0]
            self.assertEqual((r["how"], r["status"]), ("edge", status))
            self.assertAlmostEqual(r["cx"], 0.5 + (0.3 - 0.5) * (1 - 2 * HW), places=4)

    def test_edge_without_a_crop_is_the_middle_and_a_planner_error_is_kept(self):
        def boom(items, **kw):
            raise reframe.ReframeError("no ffmpeg")
        r = v.plan([item("a", "/m/a.mov", 0, 4)], planner=boom)["items"][0]
        self.assertEqual((r["cx"], r["how"], r["status"], r["reason"]),
                         (0.5, "edge", "face_error", "no ffmpeg"))

    def test_edge_mode_never_calls_the_planner(self):
        planner = mock.Mock()
        c = v.plan([item("a", "/m/a.mov", 0, 4, crop_x=0.9)], mode="edge", planner=planner)
        planner.assert_not_called()
        self.assertEqual(c["items"][0]["how"], "edge")

    def test_a_hand_override_wins_by_name_or_number(self):
        items = [item("a", "/m/a.mov", 0, 4), item("b", "/m/b.mov", 0, 4)]
        c = v.plan(items, overrides={"a": 0.3, "#2": 0.7}, planner=face_at(2400))
        self.assertEqual([(r["cx"], r["how"]) for r in c["items"]], [(0.3, "hand"), (0.7, "hand")])
        with self.assertRaisesRegex(v.VerticalError, "0 to 1"):
            v.plan(items, overrides={"a": 1.5}, planner=face_at(2400))

    def test_the_centre_is_clamped_to_the_zoomed_window(self):
        for zoom in (1.0, 2.0):
            lo = HW / zoom
            c = v.plan([item("a", "/m/a.mov", 0, 4, zoom=zoom)], planner=face_at(100))
            self.assertAlmostEqual(c["items"][0]["cx"], lo, places=4)
            c = v.plan([item("a", "/m/a.mov", 0, 4, zoom=zoom)], overrides={"a": 0.99})
            self.assertAlmostEqual(c["items"][0]["cx"], 1 - lo, places=4)

    def test_a_take_shares_one_centre(self):
        calls = []

        def planner(items, **kw):
            calls.append(items[0])
            return face_at(2400)(items)
        c = v.plan([item("a", "/m/a.mov", 0, 2), item("a", "/m/a.mov", 2, 5),
                    item("b", "/m/a.mov", 9, 11), item("c", "/m/c.mov", 11, 12)], planner=planner)
        cx = [r["cx"] for r in c["items"]]
        self.assertEqual(cx[0], cx[1])
        self.assertEqual(len(calls), 3)  # the take, the gap, the other file
        self.assertEqual((calls[0]["in"], calls[0]["out"]), (0.0, 5.0))  # judged over the take
        self.assertEqual((c["items"][1]["status"], c["items"][1]["shared_with"]), ("shared", 1))
        self.assertNotIn("shared_with", c["items"][2])

    def test_a_hand_override_on_one_cut_of_a_take_stays_on_that_cut(self):
        c = v.plan([item("a", "/m/a.mov", 0, 2), item("a2", "/m/a.mov", 2, 5)],
                   overrides={"a2": 0.3}, planner=face_at(2400))
        self.assertEqual([r["cx"] for r in c["items"]], [0.625, 0.3])

    def test_a_take_shares_the_heads_override(self):
        c = v.plan([item("a", "/m/a.mov", 0, 2), item("a2", "/m/a.mov", 2, 5)],
                   overrides={"a": 0.3}, planner=face_at(2400))
        self.assertEqual([r["cx"] for r in c["items"]], [0.3, 0.3])

    def test_bad_items_are_refused(self):
        with self.assertRaisesRegex(v.VerticalError, "give path"):
            v.plan([{"name": "a", "in": 0, "out": 1}], mode="edge")
        with self.assertRaisesRegex(v.VerticalError, "out after in"):
            v.plan([item("a", "/m/a.mov", 3, 1)], mode="edge")

    def test_review_sets_the_flag_and_apply_needs_it(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "crops.json")
            with open(path, "w") as f:
                json.dump(v.plan([item("a", "/m/a.mov", 0, 4)], mode="edge"), f)
            self.assertFalse(v.load_crops(path)["reviewed"])
            v.mark_reviewed(path)
            self.assertTrue(v.load_crops(path)["reviewed"])


class TestSheet(unittest.TestCase):
    def test_filter_and_times(self):
        row = {"cx": 0.4, "zoom": 1.0, "source_size": SRC, "in": 0, "out": 0.8}
        f = v.frame_filter(row, 12)
        self.assertIn("color=yellow", f)
        self.assertIn("scale=560:315", f)
        self.assertEqual(v.frame_times(row), [0.4])
        self.assertEqual(len(v.frame_times({**row, "out": 3})), 3)

    def test_sheet_is_a_small_jpeg(self):
        ffmpeg = reframe.FFMPEG
        if not os.access(ffmpeg, os.X_OK):
            self.skipTest("ffmpeg missing")
        with tempfile.TemporaryDirectory() as d:
            clip = os.path.join(d, "t.mp4")
            subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                            "testsrc2=size=640x360:rate=24:duration=3", "-pix_fmt", "yuv420p", clip],
                           stdin=subprocess.DEVNULL, check=True)
            crops = v.plan([{"name": f"t{n}", "path": clip, "in": 0, "out": 3 if n == 1 else 0.5,
                             "size": [640, 360]} for n in range(1, 9)], mode="edge")
            sheets = v.review_sheets(crops, os.path.join(d, "out"), ffmpeg=ffmpeg)
            self.assertEqual([os.path.basename(s) for s in sheets], ["review-01.jpg", "review-02.jpg"])
            for s in sheets:
                self.assertLess(os.path.getsize(s), 1024 * 1024)
                size = subprocess.run([ffmpeg.replace("ffmpeg", "ffprobe"), "-v", "error",
                                       "-show_entries", "stream=width,height", "-of", "csv=p=0", s],
                                      capture_output=True, text=True).stdout.strip().split(",")
                self.assertLess(max(int(x) for x in size), 2000)


# --- fakes for apply ---------------------------------------------------------

class FTool:
    def __init__(self, values):
        self.values = dict(values)

    def SetInput(self, key, value):
        self.values[key] = value

    def GetInput(self, key, t=None):
        return self.values.get(key)


class FComp:
    def __init__(self, tools):
        self.tools = {k: FTool(x) for k, x in tools.items()}
        self.locked = 0

    def FindTool(self, name):
        return self.tools.get(name)

    def Lock(self):
        self.locked += 1

    def Unlock(self):
        self.locked -= 1


def comps(word="OUTSIDE"):
    return {"rp-fx zoom-push": FComp({"Transform1": {"Pivot": {1: 0.5, 2: 0.5}}}),
            "rp-fx text-word": FComp({"Merge1": {"Center": {1: 0.5, 2: 0.5}},
                                      "Text1": {"Size": 0.14, "StyledText": word}})}


class FusionItem(rf.Item):
    def __copy__(self):
        new = rf.Item.__new__(FusionItem)
        new.__dict__.update(self.__dict__)
        new.comps = copy.deepcopy(self.comps)
        return new

    def GetFusionCompNameList(self):
        return list(self.comps)

    def GetFusionCompByName(self, name):
        return self.comps.get(name)


PATHS = ["/m/a.mov", "/m/a.mov", "/m/b.mov"]


class TestApply(unittest.TestCase):
    def setUp(self):
        rf.CALLS.clear()
        self.clips = {p: rf.Clip(os.path.basename(p), "clip-" + p, {"File Path": p}) for p in PATHS}
        zoom = {"ZoomX": 1.2, "ZoomY": 1.2, "Pan": 0, "Tilt": 0, "Scaling": 0}
        self.items = [FusionItem(f"i{n}", 86400 + 50 * n, 86450 + 50 * n, self.clips[p],
                                 props=dict(zoom), source=(0, 50)) for n, p in enumerate(PATHS)]
        self.items[0].comps = comps()
        self.items[1].comps = {}
        self.items[2].comps = comps("OK")
        self.source = rf.Timeline("Mix 16x9 [auto]", "tl-src", 24.0, 86400, 86550, {
            "video": [self.items], "audio": [[]]},
            settings={"timelineResolutionWidth": "3840", "timelineResolutionHeight": "2160"})
        self.here = rf.Timeline("Where", "tl-here")
        self.project = rf.Project(timelines=[self.source, self.here], current=self.here)
        self.resolve = rf.Resolve(self.project, page="cut")
        self.crops = {"aspect": "9x16", "reviewed": True, "items": [
            {"index": n, "name": f"i{n - 1}", "path": p, "cx": cx, "zoom": 1.2}
            for n, (p, cx) in enumerate(zip(PATHS, (0.3, 0.3, 0.62)), 1)]}
        patch = mock.patch.object(cmd_effect, "refresh", create=True,
                                  side_effect=lambda comp: sorted(comp.tools))
        self.refresh = patch.start()
        self.addCleanup(patch.stop)

    def go(self, **kw):
        a = {"timeline": "Mix 9x16 [auto]", "crops": self.crops, "source_timeline": "Mix 16x9 [auto]"}
        a.update(kw)
        return v.apply_vertical(self.resolve, self.project.GetName(), a.pop("timeline"),
                                a.pop("crops"), **a)

    def real(self, **kw):
        dry = self.go(**kw)
        return dry, self.go(dry_run=False, expect_sha=dry["plan_sha"], **kw)

    def target(self):
        return next(t for t in self.project.timelines if t.name == "Mix 9x16 [auto]")

    def test_pan_formula_is_resolves_pan_for_a_centred_tilt_zero_crop(self):
        for src, zoom, cx in (((3840, 2160), 1.2, 0.3), ((6048, 4032), 1.0, 0.62),
                              ((1920, 1080), 2.0, 0.5)):
            fit = reframe.fit_factor(src, v.OUT_SIZE, reframe.SCALE_TO_CROP)
            props = reframe.resolve_props(src, v.OUT_SIZE, zoom * fit, (cx * src[0], src[1] / 2.0),
                                          reframe.SCALE_TO_CROP)
            self.assertAlmostEqual(props["Pan"], (0.5 - cx) * 1080 * zoom, places=6)
            self.assertAlmostEqual(props["Tilt"], 0, places=6)
            self.assertAlmostEqual(props["ZoomX"], zoom, places=6)

    def test_text_size_scales_inversely_with_the_word(self):
        self.assertEqual(v.text_size("OUTSIDE"), 0.048)
        self.assertEqual(v.text_size("OK"), 0.09)  # capped
        self.assertEqual(v.text_size("a biggerword"), 0.0336)  # the longest word
        self.assertEqual(v.text_size("OK", cap=0.2), 0.168)

    def test_refusals_write_nothing(self):
        cases = ((dict(timeline="Mix 9x16"), "not an \\[auto\\]"),
                 (dict(crops={**self.crops, "reviewed": False}), "not reviewed"),
                 (dict(crops={**self.crops, "items": self.crops["items"][:2]}), "3 item\\(s\\), "
                  "crops.json has 2"),
                 (dict(source_timeline=None), "give source_timeline"),
                 (dict(crops={**self.crops, "items": [{**self.crops["items"][0], "zoom": 1.5}]
                              + self.crops["items"][1:]}), "has zoom 1.2/1.2, crops.json has 1.5"),
                 (dict(crops={**self.crops, "items": [{**self.crops["items"][0], "name": "x"}]
                              + self.crops["items"][1:]}), "is named 'i0'"))
        for kw, pattern in cases:
            with self.subTest(pattern=pattern):
                with self.assertRaisesRegex(workflows.Refused, pattern):
                    self.go(dry_run=False, **kw)
        self.assertEqual(rf.mutating_calls(), [])
        self.assertEqual(len(self.project.timelines), 2)

    def test_a_different_file_is_refused(self):
        self.crops["items"][2]["path"] = "/m/other.mov"
        with self.assertRaisesRegex(workflows.Refused, "different file"):
            self.go()

    def test_an_existing_target_of_another_size_is_never_resized(self):
        self.project.add(rf.Timeline("Mix 9x16 [auto]", "tl-old", 24.0, tracks={
            "video": [[]], "audio": [[]]},
            settings={"timelineResolutionWidth": "1920", "timelineResolutionHeight": "1080"}))
        with self.assertRaisesRegex(workflows.Refused, "never changed"):
            self.go(dry_run=False)
        self.assertEqual(rf.mutating_calls(), [])

    def test_dry_run_plans_and_writes_nothing(self):
        dry = self.go()
        self.assertEqual((dry["dry_run"], dry["create"], dry["exit_status"]), (True, True, 0))
        self.assertEqual(len(dry["plan_sha"]), 64)
        t = dry["targets"]
        self.assertEqual([x["props"]["Pan"] for x in t], [259.2, 259.2, -155.52])
        self.assertTrue(all(x["props"]["Scaling"] == 3 and x["props"]["Tilt"] == 0 for x in t))
        self.assertEqual(t[0]["comps"][0]["set"], {"Transform1.Pivot": (0.3, 0.5)})
        self.assertEqual(t[0]["comps"][1]["set"], {"Merge1.Center": (0.3, 0.5), "Text1.Size": 0.048})
        self.assertEqual(t[2]["comps"][1]["set"]["Text1.Size"], 0.09)
        self.assertEqual(t[1]["comps"], [])
        self.assertEqual(rf.mutating_calls(), [])
        self.assertEqual(len(self.project.timelines), 2)
        self.refresh.assert_not_called()

    def test_a_real_run_needs_a_current_sha(self):
        dry = self.go()
        with self.assertRaisesRegex(workflows.Refused, "plan changed"):
            self.go(dry_run=False, expect_sha="0" * 64)
        self.crops["items"][0]["cx"] = 0.31
        with self.assertRaisesRegex(workflows.Refused, "plan changed"):
            self.go(dry_run=False, expect_sha=dry["plan_sha"])
        self.assertEqual(rf.mutating_calls(), [])
        self.assertEqual(len(self.project.timelines), 2)

    def test_a_real_run_duplicates_sizes_and_reads_everything_back(self):
        dry, real = self.real()
        self.assertEqual(real["exit_status"], 0, real["results"])
        new = self.target()
        self.assertEqual((new.settings["timelineResolutionWidth"],
                          new.settings["timelineResolutionHeight"]), ("1080", "1920"))
        self.assertEqual(real["created_size"], [1080, 1920])
        self.assertEqual(self.source.settings["timelineResolutionWidth"], "3840")
        got = [(i.props["Scaling"], i.props["Pan"], i.props["Tilt"], i.props["ZoomX"])
               for i in new.tracks["video"][0]]
        self.assertEqual(got, [(3, 259.2, 0, 1.2), (3, 259.2, 0, 1.2), (3, -155.52, 0, 1.2)])
        self.assertTrue(all(r["ok"] for r in real["results"]))
        self.assertEqual(real["results"][0]["read_back"]["Scaling"], 3)
        c0 = new.tracks["video"][0][0].comps
        self.assertEqual(c0["rp-fx zoom-push"].tools["Transform1"].values["Pivot"], {1: 0.3, 2: 0.5})
        self.assertEqual(c0["rp-fx text-word"].tools["Merge1"].values["Center"], {1: 0.3, 2: 0.5})
        self.assertEqual(c0["rp-fx text-word"].tools["Text1"].values["Size"], 0.048)
        self.assertEqual(new.tracks["video"][0][2].comps["rp-fx text-word"]
                         .tools["Text1"].values["Size"], 0.09)
        self.assertEqual(self.refresh.call_count, 4)  # every touched comp, and no other
        self.assertEqual(real["results"][0]["comps"][0]["refreshed"], ["Transform1"])
        # the origin timeline's items and comps are untouched
        self.assertEqual([i.props["Scaling"] for i in self.items], [0, 0, 0])
        self.assertEqual(self.items[0].comps["rp-fx zoom-push"].tools["Transform1"].values["Pivot"],
                         {1: 0.5, 2: 0.5})
        self.assertEqual(self.items[0].comps["rp-fx text-word"].tools["Text1"].values["Size"], 0.14)
        self.assertEqual(self.project.current, self.here)  # the UI is put back

    def test_an_existing_1080x1920_target_is_written_in_place(self):
        dry, _ = self.real()
        again = self.go(dry_run=False)
        self.assertEqual((again["create"], again["exit_status"]), (False, 0))
        self.assertEqual(len([t for t in self.project.timelines if t.name == "Mix 9x16 [auto]"]), 1)

    def test_a_read_back_that_differs_is_a_failure(self):
        self.items[1].ignored = {"Pan"}  # SetProperty says yes and keeps the old value
        _, real = self.real()
        self.assertEqual(real["exit_status"], 1)
        bad = [r for r in real["results"] if not r["ok"]]
        self.assertEqual([r["index"] for r in bad], [2])
        self.assertIn("Pan", bad[0]["problems"][0])

    def test_a_copy_missing_a_comp_is_named_and_the_rest_still_lands(self):
        # the copy's comps are matched to the plan by name: a lost comp is a problem, not a shifted write
        orig = FusionItem.__copy__

        def lossy(it):
            new = orig(it)
            if it.props.get("ZoomX") and len(new.comps) == 2:
                new.comps.pop("rp-fx zoom-push")
            return new

        with mock.patch.object(FusionItem, "__copy__", lossy):
            _, real = self.real()
        self.assertEqual(real["exit_status"], 1)
        r0 = real["results"][0]
        self.assertIn("rp-fx zoom-push: the copy has no comp of that name", r0["problems"])
        self.assertEqual([c["comp"] for c in r0["comps"]], ["rp-fx zoom-push", "rp-fx text-word"])
        self.assertEqual(self.target().tracks["video"][0][0].comps["rp-fx text-word"]
                         .tools["Merge1"].values["Center"], {1: 0.3, 2: 0.5})

    def test_a_comp_input_that_does_not_take_is_a_failure(self):
        for it in self.items:
            for comp in it.comps.values():
                for tool in comp.tools.values():
                    tool.SetInput = lambda key, value: None
        _, real = self.real()
        self.assertEqual(real["exit_status"], 1)
        self.assertTrue(any("Transform1.Pivot reads" in p for r in real["results"]
                            for p in r["problems"]), real["results"])

    def test_a_missing_refresh_is_named(self):
        with mock.patch.object(cmd_effect, "refresh", None, create=True):
            _, real = self.real()
        self.assertEqual(real["exit_status"], 1)
        self.assertIn("refresh is missing", real["results"][0]["problems"][0])

    def mcp(self, **kw):
        from rpresolve.mcp import schema, server
        from rpresolve.mcp.registry import ToolContext
        tool = server.build_registry().get("vertical_apply")
        args = {"project": self.project.GetName(), "timeline": "Mix 9x16 [auto]",
                "crops": self.path, "source_timeline": "Mix 16x9 [auto]", **kw}
        self.assertEqual(schema.validate(tool.input_schema, args), [])
        return tool.handler(schema.with_defaults(tool.input_schema, args),
                            ToolContext(session=rf.Session(self.resolve)))

    def test_dry_run_then_the_real_run_is_journalled(self):
        with tempfile.TemporaryDirectory() as d:
            self.path = os.path.join(os.path.realpath(d), "crops.json")
            with open(self.path, "w") as f:
                json.dump(self.crops, f)
            env = {"RPRESOLVE_MCP_JOURNAL": os.path.join(d, "writes.jsonl"),
                   "RPRESOLVE_LOCK": os.path.join(d, "lock")}
            with mock.patch.dict(os.environ, env):
                dry = self.mcp()
                self.assertIn("would duplicate 'Mix 16x9 [auto]'", dry["summary"])
                self.assertFalse(os.path.exists(env["RPRESOLVE_MCP_JOURNAL"]))
                with self.assertRaises(Exception):
                    self.mcp(dry_run=False)  # no plan_sha
                real = self.mcp(dry_run=False, plan_sha=dry["plan_sha"])
                self.assertEqual((real["applied"], real["exit_status"]), (3, 0))
                self.assertEqual(self.target().GetItemListInTrack("video", 1)[0].props["Scaling"], 3)
                with open(env["RPRESOLVE_MCP_JOURNAL"]) as f:
                    self.assertIn("vertical_apply", f.read())


class TestCommand(unittest.TestCase):
    def test_the_command_plans_and_reviews_offline(self):
        from rpresolve import cli
        with tempfile.TemporaryDirectory() as d:
            items = os.path.join(d, "items.json")
            with open(items, "w") as f:
                json.dump([item("a", "/m/a.mov", 0, 4, crop_x=0.4)], f)
            out = os.path.join(d, "out")
            rc = cli.main(["vertical", "plan", items, "--out", out, "--centre", "edge",
                           "--no-sheet"])  # found by the shared parser
            self.assertEqual(rc, 0)
            crops = os.path.join(out, "crops.json")
            self.assertFalse(json.load(open(crops, encoding="utf-8"))["reviewed"])
            self.assertEqual(cli.main(["review", crops], module=v), 0)
            self.assertTrue(json.load(open(crops, encoding="utf-8"))["reviewed"])


if __name__ == "__main__":
    unittest.main()
