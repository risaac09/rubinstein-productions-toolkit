"""
Tests for auto captions in Resolve (rpresolve.workflows.create_captions and
the MCP create_captions tool) against the shared Resolve fakes: settings
chosen by the timeline's shape, the dry run then plan_sha, the read-back (a
call that returns True and places nothing is a failure), the UI put back,
and every refusal writing nothing.

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
from rpresolve import api, config as rpconfig, workflows  # noqa: E402
from rpresolve.mcp import schema, server  # noqa: E402
from rpresolve.mcp.registry import ToolContext  # noqa: E402

P = "RP Automation Sandbox"
REG = server.build_registry()


def timeline(name, uid, width, height, subtitles=None):
    clip = rf.Clip("P1.MOV", "clip-1", {"File Path": "/w/P1.MOV"})
    tracks = {"video": [[rf.Item("P1.MOV", 86400, 86500, clip)]],
              "audio": [[rf.Item("P1.MOV", 86400, 86500, clip, nodes=None)]]}
    if subtitles is not None:
        tracks["subtitle"] = subtitles
    return rf.Timeline(name, uid, 25.0, 86400, 86500, tracks=tracks,
                       settings={"timelineResolutionWidth": str(width),
                                 "timelineResolutionHeight": str(height)})


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
        settle = mock.patch.object(workflows, "CAPTION_SETTLE_S", 0)
        settle.start()
        self.addCleanup(settle.stop)
        self.config = rpconfig.load_config("/nonexistent/config.json")
        self.wide = timeline("Clip [auto]", "tl-wide", 1920, 1080)
        self.tall = timeline("Clip 9x16 [auto]", "tl-tall", 1080, 1920, subtitles=[[]])
        self.square = timeline("Clip 1x1 [auto]", "tl-square", 1080, 1080)
        self.captioned = timeline("Captioned [auto]", "tl-cap", 1920, 1080,
                                  subtitles=[[rf.Item("Sub", 86400, 86410, nodes=None)]])
        self.human = timeline("Isaac's edit", "tl-human", 1920, 1080)
        self.here = rf.Timeline("Where Isaac is", "tl-here")
        self.project = rf.Project(timelines=[self.wide, self.tall, self.square, self.captioned,
                                             self.human, self.here], current=self.here)
        self.resolve = rf.Resolve(self.project, page="deliver")

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self, tl="Clip 9x16 [auto]", **kw):
        kw.setdefault("config", self.config)
        return workflows.create_captions(self.resolve, P, tl, **kw)

    def twice(self, tl="Clip 9x16 [auto]", **kw):
        dry = self.run_it(tl, dry_run=True, **kw)
        return dry, self.run_it(tl, expect_sha=dry["plan_sha"], **kw)

    def made(self):
        return [c for c in rf.CALLS if c[1] == "CreateSubtitlesFromAudio"]


class TestCreateCaptions(Base):
    def test_settings_follow_the_shape(self):
        for tl, shape, chars, brk in (("Clip [auto]", "landscape", 42, "SINGLE"),
                                      ("Clip 9x16 [auto]", "portrait", 16, "DOUBLE"),
                                      ("Clip 1x1 [auto]", "square", 24, "DOUBLE")):
            with self.subTest(tl=tl):
                r = self.run_it(tl, dry_run=True)
                self.assertEqual(r["shape"], shape)
                self.assertEqual(r["settings"], {
                    "SUBTITLE_LANGUAGE": "AUTO_CAPTION_ENGLISH",
                    "SUBTITLE_CHARS_PER_LINE": chars,
                    "SUBTITLE_LINE_BREAK": f"AUTO_CAPTION_LINE_{brk}"})
        self.assertEqual(rf.mutating_calls(), [])

    def test_dry_run_then_real_run_reads_back_and_puts_the_ui_back(self):
        dry, r = self.twice()
        self.assertEqual(len(dry["plan_sha"]), 64)
        self.assertEqual(r["exit_status"], 0, r)
        self.assertTrue(r["returned"])
        self.assertEqual((r["subtitle_tracks_before"], r["subtitle_tracks_after"], r["items"]),
                         ([0], [3], 3))
        self.assertEqual(r["first_item"], {"track": 1, "start": 86400, "end": 86410})
        # the settings Resolve got, keyed by its own constants (floats)
        (_, _, (settings,)), = self.made()
        self.assertEqual(settings, {0.0: 3.0, 2.0: 16, 3.0: 2.0})
        # made current and the Edit page opened for the call, then both put back
        self.assertIn(("Project", "SetCurrentTimeline", ("Clip 9x16 [auto]",)), rf.CALLS)
        self.assertIn(("Resolve", "OpenPage", ("edit",)), rf.CALLS)
        self.assertEqual((self.project.current, self.resolve.page), (self.here, "deliver"))
        self.assertEqual(r["ui_restore_problems"], [])
        self.assertNotIn("StartRendering", [c[1] for c in rf.CALLS])

    def test_the_deliver_page_returns_false(self):
        # What Resolve did live when the call was made from the Deliver page.
        self.project.current = self.tall
        self.assertFalse(self.tall.CreateSubtitlesFromAudio({0.0: 3.0}))
        self.assertEqual(workflows._subtitle_items(self.tall), ([0], None))
        self.resolve.page = "edit"
        self.assertTrue(self.tall.CreateSubtitlesFromAudio({0.0: 3.0}))

    def test_a_page_that_will_not_open_stops_the_call(self):
        self.resolve.OpenPage = lambda page: True  # says yes, stays on Deliver
        dry = self.run_it(dry_run=True)
        with self.assertRaisesRegex(api.WriteNotApplied, "edit page"):
            self.run_it(expect_sha=dry["plan_sha"])
        self.assertEqual(self.made(), [])
        self.assertEqual(self.project.current, self.here)

    def test_true_with_nothing_placed_is_a_failure(self):
        self.tall.auto_captions = 0
        dry, r = self.twice()
        self.assertTrue(r["returned"])
        self.assertEqual((r["items"], r["exit_status"], r["first_item"]), (0, 1, None))
        self.assertEqual(self.project.current, self.here)

    def test_an_overlay_changes_the_portrait_settings(self):
        overlay = os.path.join(self.dir, "overlay.json")
        with open(overlay, "w", encoding="utf-8") as f:
            json.dump({"deliver": {"captions": {"portrait": {"chars_per_line": 18}}}}, f)
        config = rpconfig.load_config(overlay, strict=True)
        r = self.run_it(dry_run=True, config=config)
        self.assertEqual((r["settings"]["SUBTITLE_CHARS_PER_LINE"],
                          r["settings"]["SUBTITLE_LINE_BREAK"]), (18, "AUTO_CAPTION_LINE_DOUBLE"))
        bad = rpconfig.merge(self.config, {"deliver": {"captions": {"portrait":
                                                                    {"chars_per_line": 61}}}})
        with self.assertRaisesRegex(workflows.Refused, "1 to 60"):
            self.run_it(dry_run=True, config=bad)

    def test_refusals_write_nothing(self):
        cases = [
            ("Isaac's edit", {}, "not an \\[auto\\] timeline"),
            ("Captioned [auto]", {}, "already has 1 subtitle item"),
            ("Clip [auto]", {"language": "fr"}, "resolve.AUTO_CAPTION_FRENCH"),
            ("Clip [auto]", {"language": "xx"}, "unknown caption language"),
        ]
        for tl, kw, why in cases:
            with self.subTest(tl=tl, why=why):
                with self.assertRaisesRegex(workflows.Refused, why):
                    self.run_it(tl, **kw)
        self.project.rendering = True
        with self.assertRaisesRegex(workflows.Refused, "render is running"):
            self.run_it()
        self.project.rendering = False
        with self.assertRaises(api.ProjectChanged):
            workflows.create_captions(self.resolve, "Another Project", "Clip [auto]",
                                      config=self.config)
        with self.assertRaisesRegex(workflows.Refused, "plan changed"):
            self.run_it(expect_sha="0" * 64)
        self.assertEqual(rf.mutating_calls(), [])
        self.assertEqual(self.made(), [])

    def test_a_missing_constant_is_refused(self):
        self.resolve.SUBTITLE_LINE_BREAK = None
        with self.assertRaisesRegex(workflows.Refused, r"resolve\.SUBTITLE_LINE_BREAK"):
            self.run_it(dry_run=True)

    def test_an_unreadable_size_is_refused(self):
        odd = timeline("Odd [auto]", "tl-odd", 0, 0)
        self.project.add(odd)
        self.project.settings.pop("timelineResolutionWidth")
        with self.assertRaisesRegex(workflows.Refused, "frame shape"):
            self.run_it("Odd [auto]", dry_run=True)


class TestMCP(Base):
    def call(self, args):
        tool = REG.get("create_captions")
        errors = schema.validate(tool.input_schema, args)
        if errors:
            raise AssertionError(errors)
        return tool.handler(schema.with_defaults(tool.input_schema, args),
                            ToolContext(session=rf.Session(self.resolve)))

    def events(self):
        path = os.environ["RPRESOLVE_MCP_JOURNAL"]
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f]

    def test_plan_then_run(self):
        base = {"project": P, "timeline": "Clip 9x16 [auto]"}
        dry = self.call(base)
        self.assertIn("16 characters per line", dry["summary"])
        self.assertIn(dry["plan_sha"], dry["summary"])
        self.assertEqual((self.made(), self.events()), ([], []))
        with self.assertRaisesRegex(workflows.Refused, "plan_sha"):
            self.call({**base, "dry_run": False})
        r = self.call({**base, "dry_run": False, "plan_sha": dry["plan_sha"]})
        self.assertIn("made 3 caption item(s)", r["summary"])
        ev = self.events()
        self.assertEqual([(e["event"], e["tool"]) for e in ev],
                         [("started", "create_captions"), ("finished", "create_captions")])
        self.assertEqual(ev[0]["project"], {"name": P, "id": None})
        self.assertEqual(ev[1]["status"], "ok")

    def test_a_failed_read_back_says_failed(self):
        self.wide.auto_captions = 0
        base = {"project": P, "timeline": "Clip [auto]"}
        dry = self.call(base)
        r = self.call({**base, "dry_run": False, "plan_sha": dry["plan_sha"]})
        self.assertIn("FAILED", r["summary"])
        self.assertIn("returned True", r["summary"])
        self.assertEqual(self.events()[-1]["status"], "failed")

    def test_the_language_is_an_enum(self):
        with self.assertRaises(AssertionError):
            self.call({"project": P, "timeline": "Clip [auto]", "language": "english"})


if __name__ == "__main__":
    unittest.main()
