"""
Tests for the MCP read tools (rpresolve.mcp.tools_read): list_timelines,
timeline_items, media_pool and render_queue_status against the shared
Resolve fakes. Each test also asserts that no tool called a setter: reads
never switch the current timeline, page, bin or project.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

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
from rpresolve import api  # noqa: E402
from rpresolve.mcp import schema, server  # noqa: E402
from rpresolve.mcp.registry import ToolContext  # noqa: E402

REG = server.build_registry()
LUT = "V-Log+Undone+v3/S5 IIX/S5 IIX V-Log Undone v3 65.cube"


def call(name, args, project):
    tool = REG.get(name)
    args = schema.coerce(tool.input_schema, args)
    errors = schema.validate(tool.input_schema, args)
    if errors:
        raise AssertionError(errors)
    ctx = ToolContext(session=rf.Session(rf.Resolve(project)))
    return tool.handler(schema.with_defaults(tool.input_schema, args), ctx)


def sandbox():
    gh7 = rf.Clip("P1001861.MOV", "clip-gh7", {"File Path": "/w/P1001861.MOV",
                                               "Input Color Space": "Panasonic V-Gamut/V-Log",
                                               "Data Level": "Auto", "FPS": 25.0,
                                               "Type": "Video"})
    iphone = rf.Clip("IMG_1.MOV", "clip-ip", {"File Path": "/w/IMG_1.MOV",
                                              "Input Color Space": "Rec.2100 HLG"})
    v1 = [rf.Item("P1001861.MOV", 86400, 86424, gh7, [("", LUT, ["LUT: S5 IIX"])],
                  {"ZoomX": 1.0, "Pan": 0.0}, (0, 29), {"versionName": "Version 1",
                                                        "versionType": 0}),
          rf.Item("IMG_1.MOV", 86424, 86448, iphone)]
    graded = rf.Timeline("A3 spike grades", "tl-1", 24.0, 86400, 86448,
                         {"video": [v1, [rf.Item("title", 86400, 86410)]],
                          "audio": [[rf.Item("P1001861.MOV", 86400, 86424, gh7, nodes=None)]]})
    current = rf.Timeline("A3 smartreframe 9x16", "tl-2", 24.0, 86400, 86760)
    auto = rf.Timeline("SW_rigor_30 [auto]", "tl-3", 25.0, 86400, 87150)
    twin_a = rf.Timeline("Same", "tl-4")
    twin_b = rf.Timeline("Same", "tl-5")
    root = rf.Folder("Master", [], [
        rf.Folder("Source", [], [rf.Folder("Panasonic DC-GH7", [gh7]),
                                 rf.Folder("iPhone 16 Pro Max", [iphone])]),
        rf.Folder("A3 samples", [rf.Clip(f"s{i}", f"c{i}") for i in range(5)])])
    jobs = [{"JobId": "j1", "RenderJobName": "Job 1", "TimelineName": "SW_rigor_30 [auto]",
             "TargetDir": "/r", "OutputFilename": "rigor_30"},
            {"JobId": "j2", "RenderJobName": "Job 2", "_status": "Complete"}]
    return rf.Project(timelines=[graded, current, auto, twin_a, twin_b], current=current,
                      root=root, jobs=jobs)


class Base(unittest.TestCase):
    def setUp(self):
        rf.CALLS.clear()
        lock = tempfile.NamedTemporaryFile(delete=False)
        lock.close()
        self.addCleanup(os.unlink, lock.name)
        env = mock.patch.dict(os.environ, {"RPRESOLVE_LOCK": lock.name})
        env.start()
        self.addCleanup(env.stop)
        self.project = sandbox()

    def tearDown(self):
        self.assertEqual(rf.mutating_calls(), [])


class TestFakes(Base):
    def test_setters_are_recorded(self):
        self.project.SetCurrentTimeline(self.project.timelines[0])
        rf.Resolve(self.project).OpenPage("color")
        self.assertEqual([c[1] for c in rf.mutating_calls()], ["SetCurrentTimeline", "OpenPage"])
        rf.CALLS.clear()


class TestProjectCheck(Base):
    def test_wrong_name_or_id_is_refused(self):
        for tool, extra in (("list_timelines", {}), ("timeline_items", {"timeline": "tl-1"}),
                            ("media_pool", {}), ("render_queue_status", {})):
            with self.assertRaisesRegex(api.ProjectChanged, "not 'Other'"):
                call(tool, {"project": "Other", **extra}, self.project)
            with self.assertRaisesRegex(api.ProjectChanged, "not nope"):
                call(tool, {"project_id": "nope", **extra}, self.project)
        r = call("list_timelines", {"project": "RP Automation Sandbox",
                                    "project_id": "sandbox-id"}, self.project)
        self.assertEqual(r["project"], {"name": "RP Automation Sandbox", "unique_id": "sandbox-id"})


class TestListTimelines(Base):
    def test_rows_current_auto_and_filter(self):
        r = call("list_timelines", {}, self.project)
        self.assertEqual(r["total"], 5)
        by = {t["unique_id"]: t for t in r["rows"]}
        self.assertTrue(by["tl-2"]["current"])
        self.assertFalse(by["tl-1"]["current"])
        self.assertTrue(by["tl-3"]["auto"])
        self.assertEqual(by["tl-1"]["tracks"], {"video": 2, "audio": 1, "subtitle": 0})
        self.assertEqual(by["tl-2"]["frames"], 360)
        r = call("list_timelines", {"name_contains": "AUTO]", "limit": 1}, self.project)
        self.assertEqual([t["name"] for t in r["rows"]], ["SW_rigor_30 [auto]"])
        self.assertIsNone(r["next_offset"])


class TestTimelineItems(Base):
    def test_a_non_current_timeline_by_name(self):
        r = call("timeline_items", {"timeline": "A3 spike grades"}, self.project)
        self.assertEqual(r["timeline"]["unique_id"], "tl-1")
        self.assertEqual(r["total"], 3)
        first = r["rows"][0]
        self.assertEqual((first["track"], first["index"], first["source_end"]), (1, 1, 29))
        self.assertEqual(first["media"]["Input Color Space"], "Panasonic V-Gamut/V-Log")
        self.assertEqual(first["grade"]["nodes"][0]["lut"], LUT)
        self.assertEqual(first["grade"]["version"], {"name": "Version 1", "type": "local"})
        self.assertNotIn("fingerprint", first["grade"])
        self.assertEqual(first["transform"]["ZoomX"], 1.0)
        self.assertIsNone(r["rows"][2]["media"])  # the title on V2
        self.assertIs(self.project.current, self.project.timelines[1])

    def test_track_and_type_selection(self):
        r = call("timeline_items", {"timeline": "tl-1", "track": 2}, self.project)
        self.assertEqual([x["name"] for x in r["rows"]], ["title"])
        r = call("timeline_items", {"timeline": "tl-1", "track_type": "audio"}, self.project)
        self.assertEqual(r["total"], 1)
        self.assertNotIn("grade", r["rows"][0])
        r = call("timeline_items", {"timeline": "tl-1", "grades": False}, self.project)
        self.assertNotIn("grade", r["rows"][0])
        with self.assertRaisesRegex(ValueError, "2 video track"):
            call("timeline_items", {"timeline": "tl-1", "track": 3}, self.project)

    def test_unknown_and_ambiguous_names(self):
        with self.assertRaisesRegex(ValueError, "no timeline"):
            call("timeline_items", {"timeline": "missing"}, self.project)
        with self.assertRaisesRegex(ValueError, "tl-4, tl-5"):
            call("timeline_items", {"timeline": "Same"}, self.project)
        r = call("timeline_items", {"timeline": "tl-5"}, self.project)
        self.assertEqual(r["timeline"]["unique_id"], "tl-5")


class TestMediaPool(Base):
    def test_tree_and_folder_clips(self):
        r = call("media_pool", {"depth": 1}, self.project)
        self.assertEqual(r["folder"], "Master")
        self.assertEqual([c["name"] for c in r["tree"]["children"]], ["Source", "A3 samples"])
        self.assertNotIn("children", r["tree"]["children"][0])
        self.assertEqual(r["total"], 0)
        r = call("media_pool", {"folder": "Master/Source/Panasonic DC-GH7"}, self.project)
        self.assertEqual(r["folder"], "Master/Source/Panasonic DC-GH7")
        self.assertEqual(r["rows"][0]["Input Color Space"], "Panasonic V-Gamut/V-Log")
        self.assertEqual(r["rows"][0]["unique_id"], "clip-gh7")
        r = call("media_pool", {"folder": "A3 samples", "limit": 2, "offset": 2}, self.project)
        self.assertEqual([c["name"] for c in r["rows"]], ["s2", "s3"])
        self.assertEqual(r["next_offset"], 4)
        with self.assertRaisesRegex(ValueError, "no folder 'Nope' in 'Master/Source'"):
            call("media_pool", {"folder": "Source/Nope"}, self.project)


class TestRenderQueue(Base):
    def test_jobs_with_status(self):
        r = call("render_queue_status", {}, self.project)
        self.assertEqual(r["total"], 2)
        self.assertEqual(r["rows"][0]["JobStatus"], "Ready")
        self.assertEqual(r["rows"][1]["JobStatus"], "Complete")
        self.assertEqual(r["deliver"], {"format": "mov", "codec": "ProRes422HQ"})
        self.assertFalse(r["rendering"])
        self.assertEqual(r["summary"], "2 render job(s): 1 Complete, 1 Ready")


class TestLock(Base):
    def test_busy_lock_is_reported_not_waited_out(self):
        with mock.patch("rpresolve.mcp.tools_read.READ_LOCK_WAIT", 0.2):
            with api.ResolveLock(timeout=1):
                with self.assertRaises(api.ResolveBusy):
                    call("list_timelines", {}, self.project)


if __name__ == "__main__":
    unittest.main()
