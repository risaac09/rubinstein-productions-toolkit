"""
cut with reframes, against the shared Resolve fakes: per-span transforms on
the 9:16 version, the 1:1 version built the same way, the clip-level face_x
kept as it was, a version with no reframe named UNREFRAMED and not built,
and a version whose picture does not fill the frame named BARS. Synthetic
files and fake Resolve objects only.

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
os.environ.setdefault("RPRESOLVE_LOCK", os.path.join(
    tempfile.gettempdir(), f"rpresolve-test-{os.getpid()}.lock"))

import resolve_fakes as rf  # noqa: E402
from rpresolve import cut, cutlist, reframe, workflows  # noqa: E402

WORDS = [("hello", 1.0, 1.5), ("world.", 1.6, 2.0), ("again", 3.0, 3.4), ("friend.", 3.5, 3.9)]


def span_entry(x, y=360.0, scale=2.25, **extra):
    return {"x": x, "y": y, "scale": scale, "src": [1280, 720], "area": [0, 0, 1280, 720],
            "status": "pass", **extra}


class Base(unittest.TestCase):
    def setUp(self):
        rf.CALLS.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.realpath(self.tmp.name)
        self.src = os.path.join(self.dir, "source.mp4")
        with open(self.src, "wb") as f:
            f.write(b"synthetic media")
        self.words = os.path.join(self.dir, "w.json")
        with open(self.words, "w") as f:
            json.dump({"segments": [{"words": [{"word": w, "start": a, "end": b}
                                               for w, a, b in WORDS]}]}, f)
        self.approved = os.path.join(self.dir, "a.md")
        with open(self.approved, "w") as f:
            f.write("Hello world. Again friend.")
        clip = rf.Clip("source.mp4", "clip-1", {"File Path": self.src, "Resolution": "1280x720",
                                                "FPS": 25, "Type": "Video"})
        self.home = rf.Timeline("Home", "tl-home")
        self.project = rf.Project(timelines=[self.home], current=self.home,
                                  root=rf.Folder("Master", [clip]))
        self.resolve = rf.Resolve(self.project, page="edit")

    def tearDown(self):
        self.tmp.cleanup()

    def manifest(self, reframe_=None, spans=((0.9, 2.1), (2.9, 4.1)), span_reframes=None):
        m = cutlist.build_manifest({"hi": list(spans)}, self.src, 25, self.words, self.approved,
                                   reframe=reframe_)
        for s, r in zip(m["clips"][0]["spans"], span_reframes or []):
            if r is not None:
                s["reframe"] = r
        path = os.path.join(self.dir, "m.json")
        with open(path, "w") as f:
            json.dump(m, f)
        return path

    def cut(self, manifest, **kw):
        return workflows.cut(self.resolve, "RP Automation Sandbox", manifest, prefix="T",
                             audio=False, **kw)

    def timeline(self, name):
        return cut.timeline_names(self.project)[name]

    def items(self, name):
        return self.timeline(name).GetItemListInTrack("video", 1)


class TestPerSpan(Base):
    def test_each_span_gets_its_own_transform_read_back(self):
        path = self.manifest(span_reframes=[{"9x16": span_entry(270)},
                                            {"9x16": span_entry(1000, 300)}])
        dry = self.cut(path, dry_run=True)
        self.assertEqual(dry["would_create"], ["T_hi [auto]", "T_hi_9x16 [auto]"])
        self.assertEqual([(x["span"], x["props"]["Pan"]) for x in dry["reframes"]],
                         [(1, 832.5), (2, -810.0)])
        r = self.cut(path, expect_sha=dry["plan_sha"])
        tall = r["results"][1]
        self.assertTrue(tall["ok"], tall["reason"])
        # 2.25 on a 720-row source cannot fill 1920 rows: built, and named.
        self.assertEqual(tall["bars"], ["span 1: the picture covers 1620 of 1920 rows",
                                        "span 2: the picture covers 1620 of 1920 rows"])
        self.assertEqual(r["exit_status"], 2)
        self.assertIsNone(tall["props"])  # the spans differ
        items = self.items("T_hi_9x16 [auto]")
        self.assertEqual([round(i.props["Pan"], 4) for i in items], [832.5, -810.0])
        self.assertEqual([round(i.props["Tilt"], 4) for i in items], [0.0, -135.0])
        self.assertEqual({round(i.props["ZoomX"], 4) for i in items}, {2.6667})
        self.assertEqual([x["read_back"]["Pan"] for x in tall["items"]], [832.5, -810.0])
        self.assertTrue(any("Tilt" in w for w in tall["warnings"]))
        self.assertTrue(any("scaleToFit assumed" in w for w in tall["warnings"]))
        # The 16:9 is untouched, and so is the UI.
        self.assertEqual([i.props for i in self.items("T_hi [auto]")], [{}, {}])
        tl = self.timeline("T_hi_9x16 [auto]")
        self.assertEqual((tl.settings["timelineResolutionWidth"],
                          tl.settings["timelineResolutionHeight"]), ("1080", "1920"))
        self.assertIs(self.project.current, self.home)
        self.assertEqual(self.resolve.page, "edit")

    def test_a_span_entry_wins_over_the_clip_face_x(self):
        path = self.manifest({"face_x": 270}, span_reframes=[None, {"9x16": span_entry(1000)}])
        r = self.cut(path)
        pans = [round(i.props["Pan"], 4) for i in self.items("T_hi_9x16 [auto]")]
        self.assertEqual(pans, [832.5, -810.0])
        self.assertEqual([x["source"] for x in r["results"][1]["items"]], ["clip", "span"])

    def test_a_write_that_does_not_land_fails_the_version(self):
        path = self.manifest(span_reframes=[{"9x16": span_entry(270)}, {"9x16": span_entry(300)}])
        real = rf._copy_item

        def stubborn(it):
            new = real(it)
            new.ignored = {"Pan"}
            return new
        with mock.patch.object(rf, "_copy_item", stubborn):
            r = self.cut(path)
        self.assertFalse(r["results"][1]["ok"])
        self.assertIn("Property 'Pan'", r["results"][1]["reason"])
        self.assertEqual(r["exit_status"], 1)
        # The duplicate exists and is not what was planned: it is named.
        self.assertIn("T_hi_9x16 [auto]", cut.timeline_names(self.project))
        self.assertEqual(r["results"][1]["left_behind"], "T_hi_9x16 [auto]")
        self.assertEqual(r["left_behind"], ["T_hi_9x16 [auto]"])
        self.assertIn("LEFT BEHIND: 'T_hi_9x16 [auto]' exists with no item transformed",
                      r["results"][1]["reason"])


class TestSquare(Base):
    def test_1x1_beside_9x16(self):
        path = self.manifest(span_reframes=[
            {"9x16": span_entry(300, scale=2.6667), "1x1": span_entry(360, scale=1.5)},
            {"9x16": span_entry(320, scale=2.6667), "1x1": span_entry(400, scale=1.5)}])
        r = self.cut(path, aspects=["9x16", "1x1"])
        self.assertEqual([x["name"] for x in r["results"]],
                         ["T_hi [auto]", "T_hi_9x16 [auto]", "T_hi_1x1 [auto]"])
        self.assertEqual(r["exit_status"], 0, [x["reason"] for x in r["results"]])
        sq = self.timeline("T_hi_1x1 [auto]")
        self.assertEqual((sq.settings["timelineResolutionWidth"],
                          sq.settings["timelineResolutionHeight"]), ("1080", "1080"))
        # 1080x1080 from 1280x720: fit 0.84375, so scale 1.5 is zoom 1.7778; (640 - 360) * 1.5.
        self.assertEqual([(round(i.props["ZoomX"], 4), round(i.props["Pan"], 4))
                          for i in self.items("T_hi_1x1 [auto]")],
                         [(1.7778, 420.0), (1.7778, 360.0)])
        self.assertEqual({round(i.props["ZoomX"], 4) for i in self.items("T_hi_9x16 [auto]")},
                         {3.1605})
        self.assertEqual(r["results"][2]["bars"], [])
        # Both versions are duplicated from the 16:9.
        dups = [c for c in rf.CALLS if c[1] == "DuplicateTimeline"]
        self.assertEqual(len(dups), 2)
        with self.assertRaises(workflows.Refused):
            self.cut(path, aspects=["4x5"], dry_run=True)
        with self.assertRaises(workflows.Refused):
            self.cut(path, aspects=["9x16"], make_9x16=False, dry_run=True)

    def test_input_scaling_is_read_and_used(self):
        path = self.manifest(span_reframes=[{"1x1": span_entry(640, scale=1.5)},
                                            {"1x1": span_entry(640, scale=1.5)}])
        self.assertEqual(self.cut(path, aspects=["1x1"], dry_run=True)["input_scaling"],
                         "scaleToFit")
        self.project.settings[cut.INPUT_SCALING] = "scaleToCrop"
        r = self.cut(path, aspects=["1x1"])
        self.assertEqual(r["input_scaling"], "scaleToCrop")
        # scaleToCrop fits 720 rows to 1080: 1.5 already, so zoom 1.
        self.assertEqual({round(i.props["ZoomX"], 4) for i in self.items("T_hi_1x1 [auto]")}, {1.0})


class TestPerVersion(Base):
    def test_a_new_aspect_beside_an_existing_cut_is_built_from_its_16x9(self):
        path = self.manifest(span_reframes=[
            {"9x16": span_entry(270), "1x1": span_entry(360, scale=1.5)},
            {"9x16": span_entry(300), "1x1": span_entry(400, scale=1.5)}])
        self.cut(path, aspects=["9x16"])
        before = {n: [dict(i.props) for i in self.items(n)]
                  for n in ("T_hi [auto]", "T_hi_9x16 [auto]")}
        dry = self.cut(path, aspects=["9x16", "1x1"], dry_run=True)
        self.assertEqual(dry["would_create"], ["T_hi_1x1 [auto]"])
        self.assertEqual(dry["skipped_existing"], ["T_hi [auto]", "T_hi_9x16 [auto]"])
        rf.CALLS.clear()
        r = self.cut(path, aspects=["9x16", "1x1"], expect_sha=dry["plan_sha"])
        self.assertEqual([(x["name"], x["ok"]) for x in r["results"]], [("T_hi_1x1 [auto]", True)])
        self.assertEqual(r["exit_status"], 0)
        self.assertEqual([(round(i.props["ZoomX"], 4), round(i.props["Pan"], 4))
                          for i in self.items("T_hi_1x1 [auto]")],
                         [(1.7778, 420.0), (1.7778, 360.0)])
        # Additive: the two that existed are as they were, and one timeline was made.
        self.assertEqual({n: [dict(i.props) for i in self.items(n)] for n in before}, before)
        self.assertEqual([c[2] for c in rf.CALLS if c[1] == "DuplicateTimeline"],
                         [("T_hi_1x1 [auto]",)])
        self.assertEqual([c for c in rf.CALLS if c[1] == "CreateEmptyTimeline"], [])
        # Nothing left to do.
        self.assertEqual(self.cut(path, aspects=["9x16", "1x1"], dry_run=True)["would_create"], [])

    def test_an_existing_16x9_that_no_longer_matches_is_not_built_from(self):
        path = self.manifest(span_reframes=[{"1x1": span_entry(360, scale=1.5)},
                                            {"1x1": span_entry(400, scale=1.5)}])
        self.cut(path, aspects=[])
        self.items("T_hi [auto]")[1].source = (80, None)  # a person trimmed the second span
        dry = self.cut(path, aspects=["1x1"], dry_run=True)
        self.assertEqual(dry["would_create"], [])
        self.assertIn("T_hi_1x1 [auto]: 'T_hi [auto]' exists and its V1 items differ",
                      dry["refused"][0])
        self.assertIn("span 2: source starts at 80, expected 73", dry["refused"][0])
        r = self.cut(path, aspects=["1x1"], expect_sha=dry["plan_sha"])
        self.assertEqual([(x["name"], x["refused"]) for x in r["results"]],
                         [("T_hi_1x1 [auto]", True)])
        self.assertNotIn("T_hi_1x1 [auto]", cut.timeline_names(self.project))
        self.assertEqual(r["exit_status"], 1)


class TestPlanSha(Base):
    def test_the_scaling_or_the_resolution_changing_after_the_dry_run_is_refused(self):
        path = self.manifest(span_reframes=[{"1x1": span_entry(640, scale=1.5)},
                                            {"1x1": span_entry(640, scale=1.5)}])
        dry = self.cut(path, aspects=["1x1"], dry_run=True)
        self.assertEqual({x["props"]["ZoomX"] for x in dry["reframes"]}, {1.7778})
        # scaleToCrop would write Zoom 1.0 for the same crop: not what was reviewed.
        self.project.settings[cut.INPUT_SCALING] = "scaleToCrop"
        with self.assertRaisesRegex(workflows.Refused, "input scaling"):
            self.cut(path, aspects=["1x1"], expect_sha=dry["plan_sha"])
        self.assertNotIn("T_hi [auto]", cut.timeline_names(self.project))
        del self.project.settings[cut.INPUT_SCALING]
        self.assertEqual(self.cut(path, aspects=["1x1"], dry_run=True)["plan_sha"],
                         dry["plan_sha"])
        clip = self.project.pool.root.clips[0]
        clip.props["Resolution"] = "1920x1080"
        with self.assertRaisesRegex(workflows.Refused, "resolution"):
            self.cut(path, aspects=["1x1"], expect_sha=dry["plan_sha"])

    def test_a_16x9_whose_scaling_differs_from_the_plan_is_refused(self):
        path = self.manifest(span_reframes=[{"1x1": span_entry(640, scale=1.5)},
                                            {"1x1": span_entry(640, scale=1.5)}])
        wide = rf.Timeline("W [auto]", "w", settings={cut.INPUT_SCALING: "scaleToCrop"})
        self.project.add(wide)
        clip = cutlist.load_manifest(path)["clips"][0]
        r = cut.build_aspect(self.project, wide, clip, (1280, 720), "T", "1x1",
                             scaling="scaleToFit")
        self.assertEqual((r["ok"], r["refused"]), (False, True))
        self.assertIn("the plan was made for 'scaleToFit'", r["reason"])
        self.assertEqual([c for c in rf.CALLS if c[1] == "DuplicateTimeline"], [])


class TestRefusedAndLeftBehind(Base):
    def test_an_unknown_input_scaling_is_refused_before_anything_is_duplicated(self):
        path = self.manifest(span_reframes=[{"1x1": span_entry(640, scale=1.5)},
                                            {"1x1": span_entry(640, scale=1.5)}])
        self.project.settings[cut.INPUT_SCALING] = "stretch"
        dry = self.cut(path, aspects=["1x1"], dry_run=True)
        self.assertEqual(dry["would_create"], ["T_hi [auto]"])
        self.assertEqual(dry["reframes"], [])
        self.assertEqual(len(dry["refused"]), 1)
        self.assertIn("T_hi_1x1 [auto]: timelineInputResMismatchBehavior is 'stretch'",
                      dry["refused"][0])
        r = self.cut(path, aspects=["1x1"], expect_sha=dry["plan_sha"])
        sq = r["results"][1]
        self.assertEqual((sq["ok"], sq["refused"], sq["left_behind"]), (False, True, None))
        self.assertIn("known for scaleToFit and scaleToCrop only", sq["reason"])
        self.assertNotIn("T_hi_1x1 [auto]", cut.timeline_names(self.project))
        self.assertEqual([c for c in rf.CALLS if c[1] == "DuplicateTimeline"], [])
        self.assertEqual((r["left_behind"], r["exit_status"]), ([], 1))

    def test_an_item_with_its_own_scaling_is_refused(self):
        # A per-clip Fill (3) changes the crop while every ZoomX and Pan still reads back.
        path = self.manifest(span_reframes=[{"1x1": span_entry(640, scale=1.5)},
                                            {"1x1": span_entry(640, scale=1.5)}])
        self.cut(path, aspects=[])
        self.items("T_hi [auto]")[1].props["Scaling"] = 3
        dry = self.cut(path, aspects=["1x1"], dry_run=True)
        self.assertEqual(dry["would_create"], [])
        self.assertIn("item 2 has its own Scaling 3 (Fill)", dry["refused"][0])
        clip = cutlist.load_manifest(path)["clips"][0]
        r = cut.build_aspect(self.project, self.timeline("T_hi [auto]"), clip, (1280, 720), "T",
                             "1x1")
        self.assertEqual((r["ok"], r["refused"]), (False, True))
        self.assertIn("item 2 has its own Scaling 3 (Fill)", r["reason"])
        self.assertNotIn("T_hi_1x1 [auto]", cut.timeline_names(self.project))
        # 0 is the project's setting: built, and an item that says nothing is noted.
        self.items("T_hi [auto]")[1].props["Scaling"] = 0
        r = cut.build_aspect(self.project, self.timeline("T_hi [auto]"), clip, (1280, 720), "T",
                             "1x1")
        self.assertTrue(r["ok"], r["reason"])
        self.assertIn("1 of 2 V1 items did not report their Scaling", " ".join(r["warnings"]))

    def test_an_item_count_mismatch_after_the_duplicate_is_left_behind_and_journalled(self):
        from rpresolve.mcp import schema, server
        from rpresolve.mcp.registry import ToolContext
        path = self.manifest(span_reframes=[{"9x16": span_entry(270)},
                                            {"9x16": span_entry(300)}])
        real = rf.Timeline.DuplicateTimeline

        def lossy(tl, name=None):
            dup = real(tl, name)
            dup.tracks["video"][0] = dup.tracks["video"][0][:1]
            return dup
        journal = os.path.join(self.dir, "writes.jsonl")
        env = mock.patch.dict(os.environ, {"RPRESOLVE_MCP_JOURNAL": journal,
                                           "RPRESOLVE_LOCK": os.path.join(self.dir, "lock")})
        env.start()
        self.addCleanup(env.stop)
        tool = server.build_registry().get("cut")

        class Session:
            def get(s):
                return self.resolve
        args = schema.with_defaults(tool.input_schema, {
            "project": "RP Automation Sandbox", "manifest": path, "prefix": "T",
            "audio": False})
        dry = tool.handler(args, ToolContext(session=Session()))
        with mock.patch.object(rf.Timeline, "DuplicateTimeline", lossy):
            r = tool.handler(dict(args, dry_run=False, plan_sha=dry["plan_sha"]),
                             ToolContext(session=Session()))
        self.assertEqual(r["created"], ["T_hi [auto]"])
        self.assertEqual(r["left_behind"], ["T_hi_9x16 [auto]"])
        self.assertIn("1 items on V1 of T_hi_9x16 [auto], expected 2", r["results"][1]["reason"])
        self.assertIn("LEFT BEHIND (each exists as its failed build left it; delete before a "
                      "re-run): T_hi_9x16 [auto]", r["summary"])
        with open(journal, encoding="utf-8") as f:
            finished = [json.loads(x) for x in f if '"finished"' in x][-1]
        self.assertEqual(finished["detail"]["left_behind"], ["T_hi_9x16 [auto]"])


class TestLoud(Base):
    def test_no_reframe_is_named_unreframed_and_not_built(self):
        path = self.manifest()
        dry = self.cut(path, dry_run=True)
        self.assertEqual(dry["would_create"], ["T_hi [auto]"])
        self.assertEqual(len(dry["unreframed"]), 1)
        self.assertIn("T_hi_9x16 [auto]: span 1: no reframe for 9x16", dry["unreframed"][0])
        r = self.cut(path, expect_sha=dry["plan_sha"])
        tall = r["results"][1]
        self.assertEqual((tall["ok"], tall["unreframed"]), (False, True))
        self.assertTrue(tall["reason"].startswith("UNREFRAMED: "))
        self.assertNotIn("T_hi_9x16 [auto]", cut.timeline_names(self.project))
        self.assertEqual(r["exit_status"], 1)

    def test_every_aspect_with_no_reframe_is_named(self):
        # Both versions unreframed: each is named, in the dry run and the real run.
        path = self.manifest()
        dry = self.cut(path, aspects=["9x16", "1x1"], dry_run=True)
        self.assertEqual(dry["would_create"], ["T_hi [auto]"])
        self.assertEqual([u.split(":")[0] for u in dry["unreframed"]],
                         ["T_hi_9x16 [auto]", "T_hi_1x1 [auto]"])
        r = self.cut(path, aspects=["9x16", "1x1"], expect_sha=dry["plan_sha"])
        self.assertEqual([(x["name"], x["unreframed"]) for x in r["results"][1:]],
                         [("T_hi_9x16 [auto]", True), ("T_hi_1x1 [auto]", True)])

    def test_a_span_reframe_plan_left_without_a_crop_is_unreframed(self):
        path = self.manifest({"face_x": 270}, span_reframes=[
            None, {"9x16": {"status": "manual", "reason": "the face leaves the crop"}}])
        dry = self.cut(path, dry_run=True)
        self.assertIn("span 2 has no 9x16 crop (reframe-plan: manual: the face leaves the crop)",
                      dry["unreframed"][0])
        path = self.manifest(span_reframes=[{"9x16": dict(span_entry(300), src=[1920, 1080])},
                                            {"9x16": span_entry(300)}])
        dry = self.cut(path, dry_run=True)
        self.assertIn("planned on a 1920x1080 source, and this clip is 1280x720",
                      dry["unreframed"][0])

    def test_the_clip_face_x_is_built_as_before_and_its_bars_are_named(self):
        path = self.manifest({"face_x": 270})
        r = self.cut(path)
        tall = r["results"][1]
        self.assertTrue(tall["ok"], tall["reason"])
        self.assertEqual(tall["props"], {"ZoomX": 2.6667, "ZoomY": 2.6667, "Pan": 832.5})
        for it in self.items("T_hi_9x16 [auto]"):
            self.assertNotIn("Tilt", it.props)  # as the Ep 002 script: no Tilt written
        self.assertEqual(tall["bars"], ["span 1: the picture covers 1620 of 1920 rows",
                                        "span 2: the picture covers 1620 of 1920 rows"])
        self.assertTrue(any("hand-set face_x" in w for w in tall["warnings"]))
        self.assertEqual(r["exit_status"], 2)

    def test_mcp_summary_names_unreframed_and_bars(self):
        from rpresolve.mcp import schema, server
        from rpresolve.mcp.registry import ToolContext
        tool = server.build_registry().get("cut")

        class Session:
            def get(s):
                return self.resolve
        env = mock.patch.dict(os.environ, {
            "RPRESOLVE_MCP_JOURNAL": os.path.join(self.dir, "writes.jsonl"),
            "RPRESOLVE_LOCK": os.path.join(self.dir, "lock")})
        env.start()
        self.addCleanup(env.stop)

        def call(args):
            args = schema.with_defaults(tool.input_schema, args)
            self.assertEqual(schema.validate(tool.input_schema, args), [])
            return tool.handler(args, ToolContext(session=Session()))
        path = self.manifest({"face_x": 270})
        base = {"project": "RP Automation Sandbox", "manifest": path, "prefix": "T",
                "audio": False, "aspects": ["9x16"]}
        dry = call(base)
        self.assertIn("BARS (the picture does not fill the frame) on T_hi_9x16 [auto]",
                      dry["summary"])
        r = call({**base, "dry_run": False, "plan_sha": dry["plan_sha"]})
        self.assertIn("BARS", r["summary"])
        self.assertEqual(r["created"], ["T_hi [auto]", "T_hi_9x16 [auto]"])
        path = self.manifest()
        base = {**base, "manifest": path, "prefix": "V"}
        dry = call(base)
        self.assertIn("UNREFRAMED (not built): V_hi_9x16 [auto]", dry["summary"])
        r = call({**base, "dry_run": False, "plan_sha": dry["plan_sha"]})
        self.assertIn("UNREFRAMED (not built): V_hi_9x16 [auto]", r["summary"])
        self.assertNotIn("FAILED", r["summary"])


class TestItemPlans(unittest.TestCase):
    CLIP = {"name": "c", "spans": [{"in": 0, "out": 1}], "reframe": {"face_x": 270}}

    def test_resolution_and_a_source_with_none(self):
        self.assertEqual(cut.parse_resolution("1280x720"), (1280, 720))
        self.assertEqual(cut.parse_resolution(" 1920 x 1080"), (1920, 1080))
        self.assertEqual(cut.parse_resolution(""), (0, None))
        with self.assertRaisesRegex(cut.Unreframed, "no resolution"):
            cut.item_plans(self.CLIP, "9x16", (0, None))

    def test_legacy_with_only_a_width_keeps_the_old_math(self):
        p = cut.item_plans(self.CLIP, "9x16", 1280)[0]
        self.assertEqual(p["props"], cut.reframe_props({"face_x": 270}, 1280))
        self.assertIsNone(p["fills"])

    def test_widest_filled_from_the_planner_fills(self):
        clip = {"name": "c", "spans": [{"in": 0, "out": 1, "reframe": {"9x16": {
            "x": 400.0, "y": 360.0, "scale": 5.3334, "src": [1280, 720],
            "area": [0, 180, 1280, 540]}}}]}
        p = cut.item_plans(clip, "9x16", (1280, 720))[0]
        self.assertTrue(p["fills"])
        self.assertEqual(p["bars"], "")
        self.assertAlmostEqual(p["props"]["ZoomX"], 5.3334 / 0.84375)
        self.assertAlmostEqual(p["props"]["Pan"], 240 * 5.3334)

    def test_a_planned_crop_is_the_planner_transform(self):
        # cut and reframe-plan agree on the numbers for the same entry.
        report_props = reframe.resolve_props((1280, 720), (1080, 1920), 2.25, (400.0, 360.0))
        clip = {"name": "c", "spans": [{"in": 0, "out": 1, "reframe": {"9x16": {
            "x": 400.0, "y": 360.0, "scale": 2.25}}}]}
        self.assertEqual(cut.item_plans(clip, "9x16", (1280, 720))[0]["props"], report_props)


if __name__ == "__main__":
    unittest.main()
