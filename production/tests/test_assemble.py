"""
Tests for the assemble command (rpresolve.cmd_assemble): the offline plan (frames at the
manifest's rate, a continued take, a dissolve as a Cross Dissolve, the end black) and
apply_assemble against the shared Resolve fakes: the [auto]-only guard, the frame-rate
refusal, dry run and plan_sha, clips cut at their own rate onto a 24 fps timeline, the
static scale, the music with its fade, and the read-back, and the colour handling
(T30): detect's colour space per source in the dry run, the tag written and read back on a
new import, a clip already in the pool never written, an unmanaged project left alone.

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
from rpresolve import cmd_assemble as a  # noqa: E402
from rpresolve import detect  # noqa: E402
from rpresolve.workflows import Refused  # noqa: E402

UNTAGGED = "Rec.709 (Scene)"  # what an import with no tag reads on a colour-managed project


def drow(path, cs=detect.CS_VLOG, profile="V-Log", level="full", note=""):
    """A detect row, as detect.detect_paths returns it."""
    return {"path": path, "camera": "Test Cam", "profile": profile, "input_color_space": cs,
            "data_level": level, "vfr": "no", "confidence": "high" if cs else "low",
            "rule": "4", "note": note}


def set_clip_property(clip, key, value):
    """The fake clip's SetClipProperty (resolve_fakes.Clip has none): the write lands."""
    rf._log(clip, "SetClipProperty", key, value)
    clip.props[key] = value
    return True


class Base(unittest.TestCase):
    def setUp(self):
        rf.CALLS.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = os.path.realpath(self.tmp.name)
        self.one, self.two, self.song = (os.path.join(d, n) for n in ("one.mov", "two.mov", "song.m4a"))
        for p in (self.one, self.two, self.song):
            Path(p).write_bytes(b"x")
        tag = {"Input Color Space": UNTAGGED, "Data Level": "Auto"}
        props = {self.one: {"FPS": "30.0", **tag}, self.two: {"FPS": "60.0", **tag},
                 self.song: {"FPS": "", "Type": "Audio"}}
        patch = mock.patch.dict(rf.IMPORT_PROPS, props)
        patch.start()
        self.addCleanup(patch.stop)
        # detect never runs ffprobe here: both files are V-Log from one camera
        self.detected = {self.one: drow(self.one), self.two: drow(self.two)}
        for new in (mock.patch.object(
                        detect, "detect_paths",
                        lambda paths: ([self.detected[p] for p in paths if p in self.detected], [])),
                    mock.patch.object(rf.Clip, "SetClipProperty", set_clip_property, create=True)):
            new.start()
            self.addCleanup(new.stop)
        self.manifest = {
            "version": 2, "speech": False, "fps": 24,
            "sources": [{"id": "s1", "path": self.one}, {"id": "s2", "path": self.two}],
            "clips": [{"name": "m", "spans": [
                {"source": "s1", "in": 2.0, "out": 3.0},
                {"source": "s1", "in": 3.0, "out": 3.5, "scale": 1.15},
                {"source": "s2", "in": 10.0, "out": 10.25,
                 "transition": {"type": "dissolve", "frames": 2}}]}],
            "effects": [], "music": {"path": self.song, "in": 8.0},
            "method": {"id": "m", "aspect": "16:9", "look": None, "end": "black:0.5", "ui_steps": []}}
        self.here = rf.Timeline("Where", "tl-here")
        self.project = rf.Project(timelines=[self.here], current=self.here)
        self.project.settings["timelineFrameRate"] = "24"
        self.resolve = rf.Resolve(self.project, page="cut")

    def go(self, **kw):
        args = {"name": "Shoot m v1 [auto]", "manifest": self.manifest}
        args.update(kw)
        return a.apply_assemble(self.resolve, self.project.GetName(), args.pop("name"),
                                args.pop("manifest"), **args)


class TestPlan(Base):
    def test_frames_a_continued_take_the_black_and_what_is_not_built(self):
        p = a.plan(self.manifest)
        self.assertEqual([r["frames"] for r in p["spans"]], [24, 12, 6])
        self.assertEqual([r["continues"] for r in p["spans"]], [False, True, False])
        self.assertEqual((p["frames"], p["black_frames"]), (42, 12))
        self.assertEqual(p["music"], {"path": self.song, "in": 8.0})
        self.assertEqual(p["not_built"], [])
        self.assertEqual(p["spans"][2]["transition"], {"type": "Cross Dissolve", "frames": 2})
        self.assertNotIn("transition", p["spans"][1])

    def test_a_manifest_file_is_read_and_a_bad_one_refused(self):
        path = os.path.join(self.tmp.name, "m.json")
        with open(path, "w") as f:
            json.dump(self.manifest, f)
        self.assertEqual(a.plan(path)["frames"], 42)
        with self.assertRaises(Refused):
            a.plan(os.path.join(self.tmp.name, "none.json"))
        with self.assertRaises(Refused):
            a.plan({**self.manifest, "music": {"path": "song.m4a"}})

    def test_the_command_prints_the_plan(self):
        path = os.path.join(self.tmp.name, "m.json")
        with open(path, "w") as f:
            json.dump(self.manifest, f)
        with mock.patch("builtins.print") as out:
            self.assertEqual(a.run(mock.Mock(manifest=path)), 0)
        text = "\n".join(str(c.args[0]) for c in out.call_args_list)
        self.assertIn("3 item(s), 42 frames at 24 fps, then 12 of black", text)
        self.assertIn("same take, continued", text)


class TestApply(Base):
    def test_refusals_write_nothing(self):
        cases = [({"name": "Shoot m v1"}, "must end with"),
                 ({"name": "Where"}, "must end with"),
                 ({"manifest": {**self.manifest, "fps": 30}}, "takes the project's rate")]
        for kw, want in cases:
            with self.assertRaisesRegex(Refused, want):
                self.go(dry_run=False, **kw)
        os.remove(self.two)
        with self.assertRaisesRegex(Refused, "not on this machine: two.mov"):
            self.go(dry_run=False)
        self.assertEqual([c for c in rf.CALLS if c[1].startswith(rf.MUTATING_PREFIXES)], [])

    def test_an_existing_name_is_never_overwritten(self):
        self.project.add(rf.Timeline("Shoot m v1 [auto]", "tl-x"))
        with self.assertRaisesRegex(Refused, "already exists"):
            self.go()

    def test_dry_run_plans_and_writes_nothing(self):
        dry = self.go()
        self.assertTrue(dry["dry_run"] and dry["plan_sha"] and dry["created"] is None)
        self.assertEqual([c for c in rf.CALLS if c[1].startswith(rf.MUTATING_PREFIXES)], [])
        with self.assertRaisesRegex(Refused, "plan changed"):
            self.go(dry_run=False, expect_sha="0" * 64)

    def test_a_real_run_cuts_each_clip_at_its_own_rate_and_reads_back(self):
        dry = self.go()
        r = self.go(dry_run=False, expect_sha=dry["plan_sha"])
        self.assertEqual((r["exit_status"], r["problems"], r["left_behind"]), (0, [], None))
        tl = next(t for t in self.project.timelines if t.name == "Shoot m v1 [auto]")
        self.assertEqual([tl.GetSetting(k) for k in ("timelineResolutionWidth",
                                                     "timelineResolutionHeight",
                                                     "timelineInputResMismatchBehavior")],
                         ["3840", "2160", "scaleToCrop"])
        items = tl.GetItemListInTrack("video", 1)
        self.assertEqual([i.GetDuration() for i in items], [24, 12, 6])
        # 30 fps: 2.0 s is frame 60, 24 timeline frames are 30 source frames; the second
        # span carries on from the first; 60 fps: 6 timeline frames are 15 source frames.
        self.assertEqual([i.source for i in items], [(60, 90), (90, 105), (600, 615)])
        self.assertEqual((items[1].props["ZoomX"], items[1].props["ZoomY"]), (1.15, 1.15))
        self.assertNotIn("ZoomX", items[0].props)
        audio = tl.GetItemListInTrack("audio", 1)
        self.assertEqual(len(audio), 1)
        self.assertEqual((audio[0].GetStart() - tl.GetStartFrame(), audio[0].GetDuration()), (0, 54))
        self.assertEqual(audio[0].source[0], 8 * 24)  # an audio file with no FPS counts at the timeline's
        self.assertEqual(r["audio"]["fade_out"], 12)
        self.assertEqual(r["transitions"], [{"into": 3, "frames": 2, "planned": 2}])
        self.assertEqual([t.GetName() for t in items[1].transitions], ["Cross Dissolve"])
        self.assertEqual([c for c in rf.CALLS if c[1] == "AddTransition"][0][2][1],
                         {"type": "Cross Dissolve", "category": "simple", "position": "end",
                          "alignment": "center", "duration": 2})
        self.assertIs(self.project.GetCurrentTimeline(), self.here)  # the UI is put back
        self.assertEqual([f.GetName() for f in self.project.pool.root.GetSubFolderList()],
                         ["methods [auto]"])

    def test_the_mcp_tool_dry_runs_then_journals_the_real_run(self):
        from rpresolve.mcp import schema, server
        from rpresolve.mcp.registry import ToolContext
        tool = server.build_registry().get("assemble_timeline")
        path = os.path.join(os.path.realpath(self.tmp.name), "m.json")
        with open(path, "w") as f:
            json.dump(self.manifest, f)
        env = {"RPRESOLVE_MCP_JOURNAL": os.path.join(self.tmp.name, "writes.jsonl"),
               "RPRESOLVE_LOCK": os.path.join(self.tmp.name, "lock")}

        def call(**kw):
            args = {"project": self.project.GetName(), "name": "Shoot m v1 [auto]",
                    "manifest": path, **kw}
            self.assertEqual(schema.validate(tool.input_schema, args), [])
            return tool.handler(schema.with_defaults(tool.input_schema, args),
                                ToolContext(session=rf.Session(self.resolve)))

        with mock.patch.dict(os.environ, env):
            dry = call()
            self.assertIn("would build 'Shoot m v1 [auto]' with 3 item(s), 42 frames", dry["summary"])
            self.assertFalse(os.path.exists(env["RPRESOLVE_MCP_JOURNAL"]))
            real = call(dry_run=False, plan_sha=dry["plan_sha"])
            self.assertIn("built and read back", real["summary"])
            with open(env["RPRESOLVE_MCP_JOURNAL"]) as f:
                self.assertIn("assemble_timeline", f.read())

    def test_a_dissolve_resolve_refuses_is_a_problem(self):
        dry = self.go()
        with mock.patch.object(rf.Item, "add_transition_returns", False):
            r = self.go(dry_run=False, expect_sha=dry["plan_sha"])
        self.assertEqual(r["transitions"], [{"into": 3, "frames": None, "planned": 2}])
        self.assertIn("dissolve into item 3: AddTransition failed (no handles?)", r["problems"])
        self.assertEqual(r["exit_status"], 1)

    def test_a_length_that_differs_is_a_problem_and_the_timeline_is_left(self):
        dry = self.go()
        real = rf._place

        def short(tl, info):
            placed = real(tl, info)
            for it in placed:
                if it.source == (600, 615):
                    it.end -= 1
            return placed

        with mock.patch.object(rf, "_place", short):
            r = self.go(dry_run=False, expect_sha=dry["plan_sha"])
        self.assertEqual(r["exit_status"], 1)
        self.assertIn("item 3 is 5 frames, planned 6", r["problems"])
        self.assertEqual(r["left_behind"], "Shoot m v1 [auto]")


class TestColour(Base):
    """T30: an untagged clip keeps Rec.709, so a log source needs its Input Color Space."""

    def pool_clip(self, path, cs=UNTAGGED, bin_name="methods [auto]"):
        """A clip that was in the media pool before the run, in the named bin."""
        clip = rf.Clip(os.path.basename(path), "pre-" + os.path.basename(path),
                       {"File Path": path, "FPS": "60.0", "Input Color Space": cs,
                        "Data Level": "Auto"})
        self.project.pool.root.subs.append(rf.Folder(bin_name, [clip]))
        return clip

    @staticmethod
    def writes():
        return [c for c in rf.CALLS if c[1] == "SetClipProperty"]

    def rows(self, r):
        return {os.path.basename(x["path"]): x for x in r["colour"]["sources"]}

    def test_the_dry_run_lists_a_colour_row_per_source_and_writes_nothing(self):
        self.pool_clip(self.two)
        dry = self.go()
        rows = self.rows(dry)
        self.assertEqual(list(rows), ["one.mov", "two.mov"])  # no row for the music
        self.assertEqual((rows["one.mov"]["detected"], rows["one.mov"]["in_pool"],
                          rows["one.mov"]["current"], rows["one.mov"]["action"]),
                         (detect.CS_VLOG, False, None, "tag"))
        self.assertEqual((rows["two.mov"]["detected"], rows["two.mov"]["in_pool"],
                          rows["two.mov"]["current"], rows["two.mov"]["action"]),
                         (detect.CS_VLOG, True, UNTAGGED, "left"))
        self.assertIn("detect says", rows["two.mov"]["flag"])
        self.assertEqual(dry["problems"], [])  # the dry run shows it; the real run reports it
        self.assertEqual(rf.mutating_calls(), [])
        self.assertEqual(self.writes(), [])

    def test_a_new_import_is_tagged_and_read_back(self):
        dry = self.go()
        r = self.go(dry_run=False, expect_sha=dry["plan_sha"])
        self.assertEqual((r["exit_status"], r["problems"], r["left_behind"]), (0, [], None))
        clips = {c.props["File Path"]: c for c in self.project.pool.root.subs[0].clips}
        for path in (self.one, self.two):
            self.assertEqual((clips[path].props["Input Color Space"], clips[path].props["Data Level"]),
                             (detect.CS_VLOG, "Full"))
        self.assertNotIn("Input Color Space", clips[self.song].props)  # the music is not picture
        self.assertEqual(len(self.writes()), 4)
        for x in r["colour"]["sources"]:
            self.assertEqual((x["result"], x["current"]), ("tagged", detect.CS_VLOG))

    def test_a_clip_in_the_pool_is_never_written_and_a_mis_tagged_log_one_is_a_problem(self):
        pre = self.pool_clip(self.two)
        dry = self.go()
        r = self.go(dry_run=False, expect_sha=dry["plan_sha"])
        self.assertEqual(pre.props["Input Color Space"], UNTAGGED)
        self.assertEqual(pre.props["Data Level"], "Auto")
        self.assertEqual(len(self.writes()), 2)  # one.mov alone: Input Color Space, Data Level
        line = next(x for x in r["problems"] if "two.mov" in x)
        self.assertIn(detect.CS_VLOG, line)
        self.assertIn(UNTAGGED, line)
        self.assertEqual((r["exit_status"], r["left_behind"]), (1, "Shoot m v1 [auto]"))
        self.assertEqual(self.rows(r)["one.mov"]["result"], "tagged")

    def test_a_clip_in_another_bin_counts_as_in_the_pool(self):
        pre = self.pool_clip(self.one, bin_name="Source")
        r = self.go(dry_run=False)
        self.assertEqual(pre.props["Input Color Space"], UNTAGGED)
        self.assertEqual(self.rows(r)["one.mov"]["action"], "left")
        self.assertEqual(len(self.writes()), 2)  # two.mov alone

    def test_a_clip_that_reads_its_detected_space_is_fine(self):
        self.pool_clip(self.two, cs=detect.CS_VLOG)
        r = self.go(dry_run=False)
        self.assertEqual((r["exit_status"], r["problems"]), (0, []))

    def test_a_display_source_on_rec709_is_fine(self):
        self.detected[self.two] = drow(self.two, cs=detect.CS_REC709, profile="SDR")
        self.pool_clip(self.two)
        r = self.go(dry_run=False)
        self.assertEqual((r["exit_status"], r["problems"]), (0, []))

    def test_an_unmanaged_project_writes_nothing_and_says_so(self):
        self.project.settings["colorScienceMode"] = "davinciYRGB"
        self.pool_clip(self.two)
        r = self.go(dry_run=False)
        self.assertEqual(self.writes(), [])
        self.assertFalse(r["colour"]["managed"])
        self.assertTrue(any("not colour" in x or "Color Managed" in x for x in r["colour"]["notes"]))
        self.assertEqual((r["exit_status"], r["problems"]), (0, []))  # the tags would do nothing
        self.assertEqual({x["action"] for x in r["colour"]["sources"]}, {"none", "left"})

    def test_a_source_detect_cannot_pin_gets_a_note_and_no_tag(self):
        self.detected[self.one] = drow(self.one, cs="", profile=detect.REVIEW, note="no gamma tag")
        r = self.go(dry_run=False)
        row = self.rows(r)["one.mov"]
        self.assertEqual((row["action"], row["detected"]), ("none", ""))
        self.assertIn("detect could not pin a profile: no gamma tag", row["note"])
        self.assertTrue(any("one.mov" in x for x in r["colour"]["notes"]))
        self.assertEqual(len(self.writes()), 2)  # two.mov alone
        self.assertEqual((r["exit_status"], r["problems"]), (0, []))

    def test_detect_failing_is_a_note_and_the_build_goes_on(self):
        def broken(paths):
            raise detect.ToolMissing("ffprobe not found")
        with mock.patch.object(detect, "detect_paths", broken):
            r = self.go(dry_run=False)
        self.assertEqual(self.writes(), [])
        self.assertIn("detect did not run: ffprobe not found", r["colour"]["notes"])
        self.assertEqual((r["exit_status"], r["problems"]), (0, []))
        self.assertIsNotNone(r["created"])

    def test_a_tag_that_does_not_land_is_a_problem(self):
        def stuck(clip, key, value):
            rf._log(clip, "SetClipProperty", key, value)
            return True  # Resolve says yes and the property does not change
        with mock.patch.object(rf.Clip, "SetClipProperty", stuck):
            r = self.go(dry_run=False)
        self.assertTrue(any("colour: one.mov" in x and "read back" in x for x in r["problems"]))
        self.assertTrue(self.rows(r)["one.mov"]["result"].startswith("FAILED"))
        self.assertEqual((r["exit_status"], r["left_behind"]), (1, "Shoot m v1 [auto]"))

    def test_sources_of_two_colour_spaces_get_a_note(self):
        self.detected[self.two] = drow(self.two, cs=detect.CS_HLG, profile="HLG")
        r = self.go()
        self.assertTrue(any("more than one input colour space" in x for x in r["colour"]["notes"]))

    def test_the_plan_sha_covers_the_colour_rows(self):
        dry = self.go()
        self.pool_clip(self.two)  # tagged by hand between the dry run and the real one
        with self.assertRaisesRegex(Refused, "plan changed"):
            self.go(dry_run=False, expect_sha=dry["plan_sha"])
        self.assertEqual(rf.mutating_calls(), [])

    def test_the_mcp_summary_names_the_colour_work(self):
        from rpresolve.mcp import schema, server
        from rpresolve.mcp.registry import ToolContext
        tool = server.build_registry().get("assemble_timeline")
        self.pool_clip(self.two)
        path = os.path.join(os.path.realpath(self.tmp.name), "m.json")
        with open(path, "w") as f:
            json.dump(self.manifest, f)
        env = {"RPRESOLVE_MCP_JOURNAL": os.path.join(self.tmp.name, "writes.jsonl"),
               "RPRESOLVE_LOCK": os.path.join(self.tmp.name, "lock")}
        args = {"project": self.project.GetName(), "name": "Shoot m v1 [auto]", "manifest": path}
        with mock.patch.dict(os.environ, env):
            dry = tool.handler(schema.with_defaults(tool.input_schema, args),
                               ToolContext(session=rf.Session(self.resolve)))
        self.assertIn("colour: 1 to tag; in the pool before this run, log by detect and "
                      "reading Rec.709: two.mov", dry["summary"])


if __name__ == "__main__":
    unittest.main()
