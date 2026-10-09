"""
Tests for set_color_management and timeline_from_clips
(rpresolve.workflows, rpresolve.colormgmt, rpresolve.clipline and
rpresolve.mcp.tools_write) against the shared Resolve fakes: the dry run
then plan_sha, the fresh-project gate, every refusal, the read-back, the
journal, that the UI is put back, and that nothing is ever created, loaded
or saved as a project.

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
from rpresolve import api, clipline, colormgmt, workflows  # noqa: E402
from rpresolve.mcp import schema, server  # noqa: E402
from rpresolve.mcp.registry import ToolContext  # noqa: E402

REG = server.build_registry()
P = "RP Automation Sandbox"


def call(name, args, resolve, project=P):
    tool = REG.get(name)
    args = schema.coerce(tool.input_schema, {"project": project, **args})
    errors = schema.validate(tool.input_schema, args)
    if errors:
        raise AssertionError(errors)
    return tool.handler(schema.with_defaults(tool.input_schema, args),
                        ToolContext(session=rf.Session(resolve)))


def run_twice(name, args, resolve):
    """A dry run, then the real run with its plan_sha."""
    dry = call(name, args, resolve)
    return dry, call(name, {**args, "dry_run": False, "plan_sha": dry["plan_sha"]}, resolve)


def video(name, uid, fps="25.000", frames="50", path=None):
    return rf.Clip(name, uid, {"Type": "Video + Audio", "FPS": fps, "Frames": frames,
                               "Audio Ch": "2", "File Path": path or f"/w/{name}"})


class Base(unittest.TestCase):
    def setUp(self):
        rf.CALLS.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.realpath(self.tmp.name)
        env = mock.patch.dict(os.environ, {
            "RPRESOLVE_MCP_JOURNAL": os.path.join(self.dir, "writes.jsonl"),
            "RPRESOLVE_LOCK": os.path.join(self.dir, "lock")})
        env.start()
        self.addCleanup(env.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def journal(self):
        path = os.environ["RPRESOLVE_MCP_JOURNAL"]
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [json.loads(line) for line in f]

    def calls(self, prefix):
        return [c for c in rf.CALLS if c[1].startswith(prefix)]


# ---------------------------------------------------------------------------
# set_color_management
# ---------------------------------------------------------------------------

class TestSetColorManagement(Base):
    def fresh(self, mode="davinciYRGB", **kw):
        project = rf.Project(root=rf.Folder("Master", subs=[rf.Folder("Source")]), **kw)
        project.settings["colorScienceMode"] = mode
        return project, rf.Resolve(project, page="media")

    def test_dry_run_changes_nothing_then_the_real_run_sets_and_reads_back(self):
        project, resolve = self.fresh()
        dry = call("set_color_management", {}, resolve)
        self.assertEqual(dry["would_change"], ["colorScienceMode"])
        self.assertEqual(dry["plan"][0]["now"], "davinciYRGB")
        self.assertIn("would set colorScienceMode", dry["summary"])
        self.assertIn(dry["plan_sha"], dry["summary"])
        self.assertEqual(self.calls("SetSetting"), [])
        self.assertEqual(self.journal(), [])
        real = call("set_color_management", {"dry_run": False, "plan_sha": dry["plan_sha"]},
                    resolve)
        self.assertEqual(real["exit_status"], 0)
        self.assertEqual(real["applied"], [{"key": "colorScienceMode", "was": "davinciYRGB",
                                            "now": "davinciYRGBColorManagedv2"}])
        self.assertEqual(project.settings["colorScienceMode"], "davinciYRGBColorManagedv2")
        self.assertEqual([e["event"] for e in self.journal()], ["started", "finished"])
        self.assertEqual(self.journal()[1]["status"], "ok")
        self.assertEqual(self.journal()[1]["detail"]["applied"], real["applied"])

    def test_it_never_creates_loads_or_saves_a_project(self):
        project, resolve = self.fresh()
        run_twice("set_color_management", {}, resolve)
        names = {c[1] for c in rf.CALLS}
        self.assertTrue(names <= {"SetSetting"}, names)

    def test_an_already_managed_project_is_left_alone_even_with_work_in_it(self):
        project, resolve = self.fresh(mode="davinciYRGBColorManagedv2")
        project.add(rf.Timeline("Edit", "tl-1"))
        project.pool.root.clips.append(video("A.MOV", "c-1"))
        dry = call("set_color_management", {}, resolve)
        self.assertEqual(dry["would_change"], [])
        self.assertIn("nothing to set", dry["summary"])
        real = call("set_color_management", {"dry_run": False, "plan_sha": dry["plan_sha"]},
                    resolve)
        self.assertEqual(real["applied"], [])
        self.assertEqual(self.calls("SetSetting"), [])

    def test_a_project_with_a_timeline_or_a_clip_is_refused_and_nothing_is_written(self):
        for what in ("timeline", "clip", "both"):
            with self.subTest(what):
                rf.CALLS.clear()
                project, resolve = self.fresh()
                if what in ("timeline", "both"):
                    project.add(rf.Timeline("Edit", "tl-1"))
                if what in ("clip", "both"):
                    project.pool.root.subs[0].clips.append(video("A.MOV", "c-1"))
                with self.assertRaises(workflows.Refused) as cm:
                    call("set_color_management", {}, resolve)
                self.assertIn("not a fresh project", str(cm.exception))
                self.assertEqual(project.settings["colorScienceMode"], "davinciYRGB")
                self.assertEqual(self.calls("SetSetting"), [])

    def test_empty_bins_do_not_count_against_a_fresh_project(self):
        project, resolve = self.fresh()
        project.pool.root.subs.append(rf.Folder("Selects"))
        dry = call("set_color_management", {}, resolve)
        self.assertEqual(dry["would_change"], ["colorScienceMode"])

    def test_a_stale_sha_is_refused_and_a_real_run_needs_one(self):
        project, resolve = self.fresh()
        dry = call("set_color_management", {}, resolve)
        project.settings["colorScienceMode"] = "acescct"  # the project changed since the dry run
        with self.assertRaises(workflows.Refused) as cm:
            call("set_color_management", {"dry_run": False, "plan_sha": dry["plan_sha"]}, resolve)
        self.assertIn("plan changed", str(cm.exception))
        with self.assertRaises(workflows.Refused):
            call("set_color_management", {"dry_run": False}, resolve)
        self.assertEqual(project.settings["colorScienceMode"], "acescct")

    def test_a_write_a_modal_dialog_swallowed_is_a_failure_not_a_success(self):
        project, resolve = self.fresh()
        project.ignore_settings.add("colorScienceMode")
        _, real = run_twice("set_color_management", {}, resolve)
        self.assertEqual(real["exit_status"], 1)
        self.assertEqual(real["failed"]["key"], "colorScienceMode")
        self.assertIn("modal dialog", real["failed"]["problem"])
        self.assertIn("FAILED", real["summary"])
        self.assertIn("Nothing is rolled back", real["summary"])
        self.assertEqual(self.journal()[1]["status"], "failed")

    def test_the_wrong_project_is_refused(self):
        project, resolve = self.fresh()
        with self.assertRaises(api.ProjectChanged):
            call("set_color_management", {}, resolve, project="Somebody's Edit")
        self.assertEqual(self.calls("SetSetting"), [])

    def test_a_key_that_resolve_changes_itself_after_the_write_is_not_called_a_success(self):
        # A write that reads back wrong stops the run at that key.
        project, resolve = self.fresh()
        project.reset_on["colorScienceMode"] = {"colorScienceMode": "davinciYRGB"}
        _, real = run_twice("set_color_management", {}, resolve)
        self.assertEqual(real["exit_status"], 1)
        self.assertEqual(real["failed"]["key"], "colorScienceMode")

    def test_the_final_read_names_a_key_another_write_reset(self):
        # With a preset of several keys, Resolve may reset an earlier one when a later one
        # changes (the Output DRT, after the output colour space): the closing read finds it.
        project, _ = self.fresh(mode="davinciYRGBColorManagedv2")
        project.settings["colorSpaceOutput"] = "Rec.709"
        keys = (("colorScienceMode", "davinciYRGBColorManagedv2"),
                ("colorSpaceOutput", "DaVinci WG/Intermediate"))
        self.assertEqual(colormgmt.final_drift(project, keys),
                         ["colorSpaceOutput: now 'Rec.709', the preset wants "
                          "'DaVinci WG/Intermediate'"])
        self.assertEqual(colormgmt.final_drift(project, keys[:1]), [])

    def test_the_schema_offers_only_known_presets(self):
        project, resolve = self.fresh()
        with self.assertRaises(AssertionError):
            call("set_color_management", {"preset": "rcm-dwg"}, resolve)
        with self.assertRaises(workflows.Refused):
            workflows.set_color_management(resolve, P, "rcm-dwg", dry_run=True)


# ---------------------------------------------------------------------------
# timeline_from_clips
# ---------------------------------------------------------------------------

class TestTimelineFromClips(Base):
    def setUp(self):
        super().setUp()
        self.p10 = video("P10.MOV", "c-10", frames="75")
        self.p2 = video("P2.MOV", "c-2", frames="50")
        self.p1 = video("P1.MOV", "c-1", frames="100")
        self.tl_item = rf.Clip("An old timeline", "tl-item", {"Type": "Timeline"})
        self.audio = rf.Clip("Room.WAV", "wav-1", {"Type": "Audio", "FPS": "25.000",
                                                   "File Path": "/w/Room.WAV"})
        self.ladder = rf.Folder("Ladder", clips=[self.p10, self.p2, self.tl_item, self.p1,
                                                 self.audio])
        self.root = rf.Folder("Master", subs=[self.ladder, rf.Folder("Other")])
        self.here = rf.Timeline("Where Isaac is", "tl-here")
        self.project = rf.Project(timelines=[self.here], current=self.here, root=self.root)
        self.resolve = rf.Resolve(self.project, page="cut")
        self.name = "Ladder [auto]"

    def tl_names(self):
        return [self.project.GetTimelineByIndex(i).GetName()
                for i in range(1, self.project.GetTimelineCount() + 1)]

    def test_a_bin_in_natural_name_order_then_built_and_read_back(self):
        dry, real = run_twice("timeline_from_clips", {"name": self.name, "bin": "Ladder"},
                              self.resolve)
        self.assertEqual([r["name"] for r in dry["plan"]], ["P1.MOV", "P2.MOV", "P10.MOV"])
        self.assertEqual([r["length"] for r in dry["plan"]], [100, 50, 75])
        self.assertEqual([r["record"] for r in dry["plan"]], [0, 100, 150])
        self.assertEqual(dry["total_frames"], 225)
        self.assertEqual({s["name"] for s in dry["skipped"]}, {"An old timeline", "Room.WAV"})
        self.assertEqual(dry["conformed"], [])
        self.assertEqual(real["exit_status"], 0)
        self.assertEqual(real["problems"], [])
        self.assertEqual([i["name"] for i in real["items"]],
                         ["P1.MOV", "P2.MOV", "P10.MOV"])
        self.assertTrue(all(i["ok"] for i in real["items"]))
        self.assertIn(self.name, self.tl_names())
        self.assertIn("every item read back", real["summary"])

    def test_a_dry_run_creates_nothing_and_journals_nothing(self):
        dry = call("timeline_from_clips", {"name": self.name, "bin": "Ladder"}, self.resolve)
        self.assertTrue(dry["dry_run"])
        self.assertEqual(self.tl_names(), ["Where Isaac is"])
        self.assertEqual(self.calls("CreateEmptyTimeline"), [])
        self.assertEqual(self.calls("AppendToTimeline"), [])
        self.assertEqual(self.journal(), [])

    def test_the_run_is_journalled_and_the_ui_is_put_back(self):
        _, real = run_twice("timeline_from_clips", {"name": self.name, "bin": "Ladder"},
                            self.resolve)
        self.assertEqual([e["event"] for e in self.journal()], ["started", "finished"])
        self.assertEqual(self.journal()[1]["status"], "ok")
        self.assertIn("built", self.journal()[1]["summary"])
        self.assertIs(self.project.current, self.here)
        self.assertEqual(self.resolve.page, "cut")
        self.assertEqual(real["ui_restore_problems"], [])

    def test_clips_are_picture_only_on_v1_and_no_custom_settings_are_written(self):
        run_twice("timeline_from_clips", {"name": self.name, "bin": "Ladder"}, self.resolve)
        tl = self.project.GetTimelineByIndex(2)
        self.assertEqual(len(tl.GetItemListInTrack("video", 1)), 3)
        self.assertEqual(tl.GetItemListInTrack("audio", 1), [])
        self.assertEqual(self.calls("SetSetting"), [])
        sent = [c[2][0][0] for c in self.calls("AppendToTimeline")]
        self.assertTrue(all(i["mediaType"] == 1 and i["trackIndex"] == 1 for i in sent))
        self.assertTrue(all("recordFrame" not in i for i in sent))

    def test_it_never_creates_loads_saves_or_deletes_anything_else(self):
        run_twice("timeline_from_clips", {"name": self.name, "bin": "Ladder"}, self.resolve)
        names = {c[1] for c in rf.CALLS}
        self.assertFalse(names & {"CreateProject", "LoadProject", "SaveProject", "DeleteTimelines",
                                  "DeleteClips", "ImportMedia"}, names)

    def test_a_clip_at_another_rate_is_conformed_and_planned_in_real_time(self):
        self.p2.props["FPS"] = "50.000"
        self.p2.props["Frames"] = "100"  # 2 s
        dry = call("timeline_from_clips", {"name": self.name, "bin": "Ladder"}, self.resolve)
        row = next(r for r in dry["plan"] if r["name"] == "P2.MOV")
        self.assertTrue(row["conformed"])
        self.assertEqual(row["length"], 50)  # 2 s at 25 fps
        self.assertEqual(dry["conformed"], ["P2.MOV"])
        self.assertIn("conformed", dry["summary"])

    def test_a_conformed_length_is_truncated_as_resolve_does(self):
        # Measured live: 30 fps clips on a 29.97 fps timeline came out one frame short each
        # (203 frames -> 202); the same arithmetic on this 25 fps fake timeline.
        self.p2.props.update({"FPS": "50.000", "Frames": "101"})   # 2.02 s -> 50.5 frames
        self.p10.props.update({"FPS": "50.000", "Frames": "150"})  # 3 s -> exactly 75
        dry = call("timeline_from_clips", {"name": self.name, "bin": "Ladder"}, self.resolve)
        lengths = {r["name"]: r["length"] for r in dry["plan"]}
        self.assertEqual(lengths, {"P1.MOV": 100, "P2.MOV": 50, "P10.MOV": 75})
        slow = clipline.plan(self.root, 29.97002997002997, bin_path="Ladder")
        self.assertTrue(all(r["length"] == r["frames"] for r in slow["rows"] if not r["conformed"]))
        self.p1.props.update({"FPS": "30.000", "Frames": "203"})
        p = clipline.plan(self.root, 30000 / 1001.0, refs=["c-1"])
        self.assertEqual((p["rows"][0]["conformed"], p["rows"][0]["length"]), (True, 202))

    def test_named_clips_by_id_path_and_name_in_the_order_given(self):
        dry = call("timeline_from_clips", {"name": self.name, "order": "given",
                                           "clips": ["c-10", "/w/P1.MOV", "P2.MOV"]}, self.resolve)
        self.assertEqual([r["name"] for r in dry["plan"]], ["P10.MOV", "P1.MOV", "P2.MOV"])
        self.assertEqual(dry["skipped"], [])

    def test_order_by_path(self):
        self.p1.props["File Path"] = "/w/z/P1.MOV"
        dry = call("timeline_from_clips", {"name": self.name, "bin": "Ladder", "order": "path"},
                   self.resolve)
        self.assertEqual([r["name"] for r in dry["plan"]], ["P2.MOV", "P10.MOV", "P1.MOV"])

    def test_paging_the_plan(self):
        dry = call("timeline_from_clips", {"name": self.name, "bin": "Ladder", "limit": 2},
                   self.resolve)
        self.assertEqual((dry["total"], dry["returned"], dry["next_offset"]), (3, 2, 2))

    def test_refusals_write_nothing(self):
        cases = {
            "both bin and clips": {"name": self.name, "bin": "Ladder", "clips": ["c-1"]},
            "neither": {"name": self.name},
            "unknown bin": {"name": self.name, "bin": "Nope"},
            "an audio-only clip named": {"name": self.name, "clips": ["wav-1"]},
            "a timeline named": {"name": self.name, "clips": ["tl-item"]},
            "an unknown clip": {"name": self.name, "clips": ["no-such-clip"]},
            "a clip listed twice": {"name": self.name, "clips": ["c-1", "P1.MOV"]},
            "a bin with nothing to place": {"name": self.name, "bin": "Other"},
        }
        for label, args in cases.items():
            with self.subTest(label):
                with self.assertRaises(workflows.Refused):
                    call("timeline_from_clips", args, self.resolve)
        self.assertEqual(self.calls("CreateEmptyTimeline"), [])
        self.assertEqual(self.tl_names(), ["Where Isaac is"])

    def test_the_name_must_end_auto_and_must_be_new(self):
        with self.assertRaises(AssertionError):  # the schema's pattern
            call("timeline_from_clips", {"name": "Ladder", "bin": "Ladder"}, self.resolve)
        with self.assertRaises(workflows.Refused):  # the workflow, called directly
            workflows.timeline_from_clips(self.resolve, P, "Ladder", bin="Ladder", dry_run=True)
        run_twice("timeline_from_clips", {"name": self.name, "bin": "Ladder"}, self.resolve)
        with self.assertRaises(workflows.Refused) as cm:
            call("timeline_from_clips", {"name": self.name, "bin": "Ladder"}, self.resolve)
        self.assertIn("already exists", str(cm.exception))

    def test_a_stale_sha_is_refused_and_a_real_run_needs_one(self):
        dry = call("timeline_from_clips", {"name": self.name, "bin": "Ladder"}, self.resolve)
        self.p1.props["Frames"] = "101"  # the clip changed since the dry run
        with self.assertRaises(workflows.Refused) as cm:
            call("timeline_from_clips", {"name": self.name, "bin": "Ladder", "dry_run": False,
                                         "plan_sha": dry["plan_sha"]}, self.resolve)
        self.assertIn("plan changed", str(cm.exception))
        with self.assertRaises(workflows.Refused):
            call("timeline_from_clips", {"name": self.name, "bin": "Ladder", "dry_run": False},
                 self.resolve)
        self.assertEqual(self.tl_names(), ["Where Isaac is"])

    def test_the_wrong_project_is_refused(self):
        with self.assertRaises(api.ProjectChanged):
            call("timeline_from_clips", {"name": self.name, "bin": "Ladder"}, self.resolve,
                 project="Somebody's Edit")

    def test_a_resolve_that_places_nothing_is_a_failure_named_left_behind(self):
        self.project.pool.AppendToTimeline = lambda items: True  # truthy, places nothing
        _, real = run_twice("timeline_from_clips", {"name": self.name, "bin": "Ladder"},
                            self.resolve)
        self.assertEqual(real["exit_status"], 1)
        self.assertEqual(real["left_behind"], self.name)
        self.assertIn("V1 holds 0 item(s)", " ".join(real["problems"]))
        self.assertIn("FAILED", real["summary"])
        self.assertEqual(self.journal()[1]["status"], "failed")
        self.assertEqual(self.journal()[1]["detail"]["left_behind"], self.name)

    def test_the_journal_carries_a_count_not_every_item_and_the_bin_is_named(self):
        _, real = run_twice("timeline_from_clips", {"name": self.name, "bin": "Ladder"},
                            self.resolve)
        self.assertEqual(real["created"]["items"], 3)
        self.assertEqual(len(real["items"]), 3)
        self.assertEqual(self.journal()[1]["detail"]["created"]["items"], 3)
        self.assertEqual(real["lands_in_bin"], "Master")  # the open bin, as the fake keeps it

    def test_naming_many_clips_walks_the_pool_once(self):
        walks = []
        real_walk = clipline.sb.walk

        def counting(root):
            walks.append(1)
            return real_walk(root)
        with mock.patch.object(clipline.sb, "walk", counting):
            clipline.plan(self.root, 25.0, refs=["c-1", "c-2", "c-10"])
        self.assertEqual(len(walks), 1)

    def test_a_cancel_mid_build_names_the_timeline_it_left_behind(self):
        from rpresolve.mcp.registry import Cancelled
        n = []

        def cancel_on_second():
            n.append(1)
            if len(n) == 2:
                raise Cancelled("cancelled")
        with self.assertRaises(Cancelled) as cm:
            workflows.timeline_from_clips(self.resolve, P, self.name, bin="Ladder",
                                          dry_run=False, check_cancel=cancel_on_second)
        self.assertIn(self.name, str(cm.exception))
        self.assertIn("left in the project", str(cm.exception))
        self.assertIn(self.name, self.tl_names())

    def test_the_project_changing_mid_build_is_a_stop_not_a_mismatch(self):
        real_check = api.ProjectPin.check
        calls = []

        def flaky(pin, pm):
            calls.append(1)
            if len(calls) == 3:  # after the timeline exists and one clip is placed
                raise api.ProjectChanged("Open project is now 'Another'. Stopping.")
            return real_check(pin, pm)
        with mock.patch.object(api.ProjectPin, "check", flaky):
            r = workflows.timeline_from_clips(self.resolve, P, self.name, bin="Ladder",
                                              dry_run=False)
        self.assertEqual(r["exit_status"], 1)
        self.assertEqual(r["left_behind"], self.name)
        self.assertTrue(any("stopped, the open project changed" in p for p in r["problems"]))

    def test_a_gap_between_clips_is_caught(self):
        real_append = self.project.pool.AppendToTimeline
        seen = []

        def with_gap(items):
            seen.append(items)
            if len(seen) == 2:  # the second clip lands 7 frames late
                items = [{**items[0], "recordFrame": self.project.current.end + 7}]
            return real_append(items)
        self.project.pool.AppendToTimeline = with_gap
        _, real = run_twice("timeline_from_clips", {"name": self.name, "bin": "Ladder"},
                            self.resolve)
        self.assertEqual(real["exit_status"], 1)
        self.assertTrue(any("the one before ends" in p for p in real["problems"]), real["problems"])

    def test_an_item_nothing_planned_on_another_track_is_caught(self):
        real_append = self.project.pool.AppendToTimeline

        def stray(items):
            r = real_append(items)
            tl = self.project.current
            if not tl.tracks["audio"][0]:
                tl.tracks["audio"][0].append(rf.Item("stray", tl.start, tl.start + 5, None,
                                                     nodes=None))
            return r
        self.project.pool.AppendToTimeline = stray
        _, real = run_twice("timeline_from_clips", {"name": self.name, "bin": "Ladder"},
                            self.resolve)
        self.assertEqual(real["exit_status"], 1)
        self.assertTrue(any("A1 holds" in p for p in real["problems"]), real["problems"])


class TestClipline(unittest.TestCase):
    def test_natural_order(self):
        names = ["P10.MOV", "p2.MOV", "P1.MOV", "P2b.MOV"]
        self.assertEqual(sorted(names, key=clipline.natural_key),
                         ["P1.MOV", "p2.MOV", "P2b.MOV", "P10.MOV"])

    def test_a_bin_path_may_lead_with_the_root_and_nest(self):
        inner = rf.Folder("Sony ILCE-7M4")
        root = rf.Folder("Master", subs=[rf.Folder("Source", subs=[inner])])
        self.assertIs(clipline.folder_at(root, "Source/Sony ILCE-7M4")[0], inner)
        self.assertIs(clipline.folder_at(root, "Master/Source/Sony ILCE-7M4")[0], inner)
        self.assertIsNone(clipline.folder_at(root, "Source/Nope")[0])
        twin = rf.Folder("Master", subs=[rf.Folder("A"), rf.Folder("A")])
        self.assertIn("2 bins", clipline.folder_at(twin, "A")[1])


if __name__ == "__main__":
    unittest.main()
