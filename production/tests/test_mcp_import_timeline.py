"""
Tests for import_timeline (rpresolve.workflows, rpresolve.mcp.tools_write)
and the FCPXML reader behind it (rpresolve.fcpxmlread), against the shared
Resolve fakes: the plan from the file, the refusals, the read-back after
the fake import, and the journal.
"""

import os
import sys
import tempfile
import unittest
from fractions import Fraction
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import resolve_fakes as rf  # noqa: E402
from rpresolve import api, fcpxmlread, workflows  # noqa: E402
from rpresolve.mcp import schema, server  # noqa: E402
from rpresolve.mcp.registry import ToolContext  # noqa: E402

REG = server.build_registry()
P = "RP Automation Sandbox"

FCPXML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.10">
  <resources>
    <format id="r1" name="FFVideoFormat1080p25" frameDuration="1/25s" width="1920" height="1080"/>
    <asset id="r2" name="a" start="0s" duration="12s" hasVideo="1" hasAudio="1" format="r1">
      <media-rep kind="original-media" src="file://{dir}/a.mp4"/></asset>
    <asset id="r3" name="music" start="0s" duration="20s" hasVideo="0" hasAudio="1">
      <media-rep kind="original-media" src="file://{dir}/music.wav"/></asset>
    <asset id="r4" name="cutout" start="0s" duration="4s" hasVideo="1" hasAudio="0" format="r1">
      <media-rep kind="original-media" src="file://{dir}/missing.mov"/></asset>
    <effect id="r5" name="Cross Dissolve" uid="FxPlug:4731E73A-8DAC-4113-9A30-AE85B1761265"/>
  </resources>
  <library><event name="e"><project name="p">
    <sequence format="r1" duration="9s" tcStart="3600s" tcFormat="NDF">
      <spine>
        <asset-clip ref="r2" name="a1" offset="0s" start="2s" duration="3s">
          <asset-clip ref="r3" name="music" lane="-1" offset="2s" start="0s" duration="9s"/>
          <asset-clip ref="r4" name="cutout" lane="1" offset="5s" start="0s" duration="2s"/>
        </asset-clip>
        <transition name="Cross Dissolve" offset="66/25s" duration="12/25s">
          <filter-video ref="r5" name="Cross Dissolve"/></transition>
        <asset-clip ref="r2" name="a2" offset="3s" start="6s" duration="3s">
          <adjust-transform scale="1.2 1.2"/></asset-clip>
        <asset-clip ref="r2" name="a3" offset="6s" start="9s" duration="3s"/>
      </spine>
    </sequence>
  </project></event></library>
</fcpxml>
"""


def call(name, args, resolve, project=P):
    tool = REG.get(name)
    args = schema.coerce(tool.input_schema, {"project": project, **args})
    errors = schema.validate(tool.input_schema, args)
    if errors:
        raise AssertionError(errors)
    return tool.handler(schema.with_defaults(tool.input_schema, args),
                        ToolContext(session=rf.Session(resolve)))


def run_twice(name, args, resolve):
    dry = call(name, args, resolve)
    return dry, call(name, {**args, "dry_run": False, "plan_sha": dry["plan_sha"]}, resolve)


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
        self.addCleanup(self.tmp.cleanup)
        for f in ("a.mp4", "music.wav"):
            open(os.path.join(self.dir, f), "wb").close()
        self.file = os.path.join(self.dir, "reel.fcpxml")
        with open(self.file, "w", encoding="utf-8") as f:
            f.write(FCPXML.format(dir=self.dir))
        self.here = rf.Timeline("Where Isaac is", "tl-here")
        self.root = rf.Folder("Master", subs=[rf.Folder("Reels")])
        self.project = rf.Project(timelines=[self.here], current=self.here, root=self.root)
        self.resolve = rf.Resolve(self.project, page="edit")
        self.pool = self.project.GetMediaPool()
        self.name = "Reel 01 [auto]"
        # what the fake Resolve places for this file: the file's own layout
        self.pool.import_layout = {self.file: {
            "video": [[("a1", 0, 75), ("Cross Dissolve", 66, 12, {"transition": True}),
                       ("a2", 75, 75, {"ZoomX": 1.2}), ("a3", 150, 75)], [("cutout", 75, 50)]],
            "audio": [[("a1", 0, 75), ("a2", 75, 75), ("a3", 150, 75)], [("music", 0, 225)]]}}

    def journal(self):
        import json
        p = os.environ["RPRESOLVE_MCP_JOURNAL"]
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def tl_names(self):
        return [self.project.GetTimelineByIndex(i).GetName()
                for i in range(1, self.project.GetTimelineCount() + 1)]


class TestReader(Base):
    def test_events_transitions_and_media_are_read_in_frames(self):
        r = fcpxmlread.read(self.file)
        self.assertEqual(r["rate"], Fraction(25))
        self.assertFalse(r["drop"])
        self.assertEqual([(e["name"], e["kind"], e["lane"], e["record"], e["frames"])
                          for e in r["events"]],
                         [("a1", "video", 0, 0, 75), ("music", "audio", -1, 0, 225),
                          ("a2", "video", 0, 75, 75), ("cutout", "video", 1, 75, 50),
                          ("a3", "video", 0, 150, 75)])
        self.assertEqual(r["transitions"], [{"name": "Cross Dissolve", "record": 66, "frames": 12}])
        self.assertEqual([e.get("scale") for e in r["events"]], [None, None, 1.2, None, None])
        self.assertEqual(r["media"], {os.path.join(self.dir, "a.mp4"): True,
                                      os.path.join(self.dir, "music.wav"): True,
                                      os.path.join(self.dir, "missing.mov"): False})
        self.assertEqual(r["warnings"], [])

    def test_a_time_off_the_frame_grid_is_rounded_and_named(self):
        with open(self.file, "w", encoding="utf-8") as f:
            f.write(FCPXML.format(dir=self.dir).replace('name="a3" offset="6s"',
                                                        'name="a3" offset="601/100s"'))
        r = fcpxmlread.read(self.file)
        self.assertEqual([e["record"] for e in r["events"] if e["name"] == "a3"], [150])
        self.assertEqual(len(r["warnings"]), 1)
        self.assertIn("a3", r["warnings"][0])

    def test_not_fcpxml_is_an_error(self):
        bad = os.path.join(self.dir, "bad.fcpxml")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("<otio/>")
        with self.assertRaises(fcpxmlread.FcpxmlError):
            fcpxmlread.read(bad)


class TestImportTimeline(Base):
    def test_the_plan_lists_the_file_and_the_real_run_reads_every_video_clip_back(self):
        dry, real = run_twice("import_timeline", {"name": self.name, "file": self.file},
                              self.resolve)
        self.assertTrue(dry["dry_run"])
        self.assertEqual(dry["rate"], "25/1")
        self.assertEqual([e["name"] for e in dry["events"]], ["a1", "music", "a2", "cutout", "a3"])
        self.assertEqual(dry["media_missing"], [os.path.join(self.dir, "missing.mov")])
        self.assertEqual(len(dry["transitions"]), 1)
        self.assertIn("1 media file(s) missing", dry["summary"])
        self.assertEqual(real["exit_status"], 0)
        self.assertEqual(real["problems"], [])
        self.assertEqual([(i["name"], i["track"], i["zoom"], i["ok"]) for i in real["items"]],
                         [("a1", 1, None, True), ("a2", 1, 1.2, True), ("cutout", 2, None, True),
                          ("a3", 1, None, True)])
        self.assertEqual(real["transitions"], [{"name": "Cross Dissolve", "record": 66,
                                                "frames": 12, "placed_as": "Cross Dissolve",
                                                "track": 1, "ok": True}])
        self.assertEqual(real["tracks"], {"V1": 4, "V2": 1, "A1": 3, "A2": 1})
        self.assertEqual(real["clips_added"], ["a1", "a2", "a3", "cutout", "a1", "a2", "a3",
                                               "music"])
        self.assertIn(self.name, self.tl_names())
        self.assertIn("every transition in place (placed as Cross Dissolve)", real["summary"])
        call = rf.CALLS[[c[1] for c in rf.CALLS].index("ImportTimelineFromFile")]
        self.assertEqual(call[2], (self.file, {"timelineName": self.name,
                                               "importSourceClips": True}))

    def test_a_dry_run_imports_nothing_and_journals_nothing(self):
        call("import_timeline", {"name": self.name, "file": self.file}, self.resolve)
        self.assertNotIn("ImportTimelineFromFile", [c[1] for c in rf.CALLS])
        self.assertEqual(self.journal(), [])

    def test_the_run_is_journalled_and_the_ui_is_put_back(self):
        _, real = run_twice("import_timeline", {"name": self.name, "file": self.file},
                            self.resolve)
        self.assertEqual([e["event"] for e in self.journal()], ["started", "finished"])
        self.assertEqual(self.journal()[1]["status"], "ok")
        self.assertIs(self.project.current, self.here)
        self.assertEqual(self.resolve.page, "edit")
        self.assertEqual(real["ui_restore_problems"], [])

    def test_a_timeline_that_does_not_match_the_file_is_left_behind_and_named(self):
        v1 = self.pool.import_layout[self.file]["video"][0]
        v1[2] = ("a2", 76, 75, {"ZoomX": 1.2})  # a frame late
        v1[1] = ("Cross Dissolve", 66, 10, {"transition": True})  # two frames short
        v1.append(("a3", 170, 10, {"ZoomX": 1.0}))  # a stray second item
        _, real = run_twice("import_timeline", {"name": self.name, "file": self.file},
                            self.resolve)
        self.assertEqual(real["exit_status"], 1)
        self.assertEqual(real["left_behind"], self.name)
        self.assertIn("a2: no item at record 75", real["problems"][0])
        self.assertIn("nearest: a2 at 76", real["problems"][0])
        self.assertIn("transition #1 Cross Dissolve: none at record 66", real["problems"][1])
        self.assertIn("2 video item(s) the file does not name: a2 at 76, a3 at 170",
                      real["problems"][2])
        self.assertIn("1 transition(s) the file does not name", real["problems"][3])
        self.assertFalse(real["items"][1]["ok"])
        self.assertFalse(real["transitions"][0]["ok"])
        self.assertIn("FAILED", real["summary"])
        self.assertIn(self.name, self.tl_names())

    def test_a_scale_the_file_set_must_read_back_as_zoom(self):
        self.pool.import_layout[self.file]["video"][0][2] = ("a2", 75, 75, {"ZoomX": 1.0})
        _, real = run_twice("import_timeline", {"name": self.name, "file": self.file},
                            self.resolve)
        self.assertEqual(real["problems"], ["#2 a2: ZoomX reads 1.0, the file says 1.2"])
        self.assertFalse(real["items"][1]["ok"])

    def test_no_audio_placed_is_a_problem_and_a_crossfade_is_not_audio(self):
        self.pool.import_layout[self.file]["audio"] = [[("Cross Fade", 66, 12,
                                                         {"transition": True})]]
        _, real = run_twice("import_timeline", {"name": self.name, "file": self.file},
                            self.resolve)
        self.assertEqual(real["problems"],
                         ["the file names 1 audio clip(s); no audio item was placed"])

    def test_a_timeline_under_another_name_is_not_applied_and_names_the_clips_added(self):
        layout = self.pool.import_layout.pop(self.file)
        self.pool.import_layout[self.file] = layout
        self.pool.rename_import = "Something else"
        with self.assertRaises(api.WriteNotApplied) as cm:
            run_twice("import_timeline", {"name": self.name, "file": self.file}, self.resolve)
        self.assertIn("8 clip(s) were added to the bin 'Master'", str(cm.exception))


class TestReaderWrappers(Base):
    def test_a_clip_wrapper_counts_once_and_its_connected_lane_still_counts(self):
        wrapped = FCPXML.format(dir=self.dir).replace(
            '<asset-clip ref="r2" name="a3" offset="6s" start="9s" duration="3s"/>',
            '<clip name="a3" offset="6s" start="9s" duration="3s"><video ref="r2" offset="9s" '
            'duration="3s"/><audio ref="r2" offset="9s" duration="3s"/>'
            '<video ref="r4" name="pop" lane="1" offset="10s" duration="1s"/></clip>')
        with open(self.file, "w", encoding="utf-8") as f:
            f.write(wrapped)
        r = fcpxmlread.read(self.file)
        self.assertEqual([(e["name"], e["lane"], e["record"]) for e in r["events"]
                          if e["record"] >= 150], [("a3", 0, 150), ("pop", 1, 175)])

    def test_a_zero_frame_duration_and_a_bad_scale_are_named(self):
        text = FCPXML.format(dir=self.dir).replace('scale="1.2 1.2"', 'scale="1,2 1,2"')
        with open(self.file, "w", encoding="utf-8") as f:
            f.write(text)
        r = fcpxmlread.read(self.file)
        self.assertNotIn("scale", [k for e in r["events"] for k in e])
        self.assertEqual(r["warnings"], ["a2: scale '1,2 1,2' is not a number; not checked"])
        with open(self.file, "w", encoding="utf-8") as f:
            f.write(text.replace('frameDuration="1/25s"', 'frameDuration="0s"'))
        with self.assertRaises(fcpxmlread.FcpxmlError):
            fcpxmlread.read(self.file)

    def test_refusals(self):
        with self.assertRaises(workflows.Refused):
            call("import_timeline", {"name": "Reel 01 [auto]", "file": "relative.fcpxml"},
                 self.resolve)
        other = os.path.join(self.dir, "x.otio")
        open(other, "w").close()
        with self.assertRaises(workflows.Refused):
            call("import_timeline", {"name": self.name, "file": other}, self.resolve)
        self.project.add(rf.Timeline(self.name, "tl-taken"))
        with self.assertRaises(workflows.Refused):
            call("import_timeline", {"name": self.name, "file": self.file}, self.resolve)

    def test_a_stale_plan_sha_is_refused(self):
        dry = call("import_timeline", {"name": self.name, "file": self.file}, self.resolve)
        with open(self.file, "a", encoding="utf-8") as f:
            f.write("\n")
        with self.assertRaises(workflows.Refused):
            call("import_timeline", {"name": self.name, "file": self.file, "dry_run": False,
                                     "plan_sha": dry["plan_sha"]}, self.resolve)


if __name__ == "__main__":
    unittest.main()
