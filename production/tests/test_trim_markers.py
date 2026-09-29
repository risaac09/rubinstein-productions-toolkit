"""
Tests for trim-review markers in Resolve (rpresolve.workflows
.trim_review_markers, rpresolve.markers, the MCP trim_review_markers tool
and trim-review --markers) against the shared Resolve fakes: rows mapped
from source seconds to marker frames through the items that play the
source, a colour per kind, each marker read back with GetMarkers, a frame
that already holds a marker refused, [auto] timelines only, and nothing
cut, rippled or deleted. Synthetic words and audio only.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import argparse
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import resolve_fakes as rf  # noqa: E402
import resolve_workflow  # noqa: E402
from rpresolve import api, cutlist, markers, workflows  # noqa: E402
from rpresolve.mcp import schema, server  # noqa: E402
from rpresolve.mcp.registry import ToolContext  # noqa: E402
from test_trimreview import PIECES, W, tone_wav  # noqa: E402

P = "RP Automation Sandbox"
WORDS = [W("So", 0.1, 0.3), W("um,", 0.4, 0.6), W("I", 0.7, 0.8), W("I", 0.8, 0.9),
         W("think", 0.9, 1.3), W("well", 20.5, 20.8), W("uh", 21.0, 21.2),
         W("never", 40.0, 40.3), W("um", 40.5, 40.7)]


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
        self.source = os.path.join(self.dir, "take.wav")
        tone_wav(self.source, PIECES)
        self.words_path = os.path.join(self.dir, "take.words.json")
        with open(self.words_path, "w", encoding="utf-8") as f:
            json.dump({"segments": [{"words": WORDS}]}, f)
        clip = rf.Clip("take.wav", "clip-take", {"File Path": self.source, "FPS": "25"})
        other = rf.Clip("other.mov", "clip-other", {"File Path": "/w/other.mov", "FPS": "25"})
        # Two spans of the source: 0-10 s at the start, then 20-26 s.
        self.auto = rf.Timeline("Clip [auto]", "tl-auto", 25.0, 90000, 90400, tracks={
            "video": [[rf.Item("take.wav", 90000, 90250, clip, source=(0, 250)),
                       rf.Item("take.wav", 90250, 90400, clip, source=(500, 650))]],
            "audio": [[rf.Item("take.wav", 90000, 90250, clip, nodes=None, source=(0, 250)),
                       rf.Item("take.wav", 90250, 90400, clip, nodes=None, source=(500, 650))]]})
        self.elsewhere = rf.Timeline("Other [auto]", "tl-other", 25.0, 90000, 90100, tracks={
            "video": [[rf.Item("other.mov", 90000, 90100, other, source=(0, 100))]]})
        self.human = rf.Timeline("Isaac's edit", "tl-human", 25.0, 90000, 90400,
                                 tracks=self.auto.tracks)
        self.here = rf.Timeline("Where Isaac is", "tl-here")
        self.project = rf.Project(timelines=[self.auto, self.elsewhere, self.human, self.here],
                                  current=self.here)
        self.resolve = rf.Resolve(self.project, page="color")

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self, tl="Clip [auto]", audio=False, **kw):
        return workflows.trim_review_markers(self.resolve, P, tl, self.source,
                                             cutlist.load_words(self.words_path), audio=audio,
                                             **kw)

    def twice(self, **kw):
        dry = self.run_it(dry_run=True, **kw)
        return dry, self.run_it(expect_sha=dry["plan_sha"], **kw)


class TestPlan(Base):
    def test_rows_map_through_the_items(self):
        r = self.run_it(dry_run=True)
        self.assertEqual(rf.mutating_calls(), [])
        got = [(m["frame"], m["color"], m["name"], m["duration"]) for m in r["planned"]]
        self.assertEqual(got, [(10, "Yellow", "filler: um,", 5),
                               (18, "Purple", "repeat: I I", 5),
                               (275, "Yellow", "filler: uh", 5)])
        # 40.5 s is not on the timeline (the items show 0-10 s and 20-26 s).
        self.assertEqual(r["rows"], 3)
        self.assertEqual([(i["src_start_s"], i["src_end_s"]) for i in r["items"]],
                         [(0.0, 10.0), (20.0, 26.0)])
        m = r["planned"][0]
        self.assertIn("cut-candidate (high confidence)", m["note"])
        self.assertIn("nothing was cut", m["note"])
        self.assertTrue(m["custom"].startswith(markers.CUSTOM))

    def test_a_frame_that_holds_a_marker_is_refused(self):
        self.auto.markers[10.0] = {"color": "Green", "duration": 1.0, "note": "", "name": "mine",
                                   "customData": ""}
        r = self.run_it(dry_run=True)
        self.assertEqual([m["frame"] for m in r["planned"]], [18, 275])
        self.assertEqual(r["refused"], [{"frame": 10, "reason": "a marker is already at this frame",
                                         "kind": "filler", "source_start": 0.4}])

    def test_retimed_items_are_skipped(self):
        slow = rf.Item("take.wav", 90000, 90250, self.auto.tracks["video"][0][0].clip,
                       source=(0, 125))
        self.auto.tracks = {"video": [[slow]]}
        with self.assertRaisesRegex(workflows.Refused, "retimed"):
            self.run_it(dry_run=True)

    def test_refusals(self):
        with self.assertRaisesRegex(workflows.Refused, "not an \\[auto\\] timeline"):
            self.run_it("Isaac's edit", dry_run=True)
        with self.assertRaisesRegex(workflows.Refused, "no item on 'Other \\[auto\\]' plays"):
            self.run_it("Other [auto]", dry_run=True)
        with self.assertRaises(api.ProjectChanged):
            workflows.trim_review_markers(self.resolve, "Another", "Clip [auto]", self.source,
                                          None, audio=False, dry_run=True)
        self.assertEqual(rf.mutating_calls(), [])


class TestAdd(Base):
    def test_markers_are_added_read_back_and_nothing_else_changes(self):
        dry, r = self.twice()
        self.assertEqual(r["exit_status"], 0, r)
        self.assertEqual([x["ok"] for x in r["results"]], [True, True, True])
        self.assertEqual(sorted(self.auto.GetMarkers()), [10.0, 18.0, 275.0])
        self.assertEqual(self.auto.GetMarkers()[275.0]["color"], "Yellow")
        mutating = {c[1] for c in rf.mutating_calls()}
        self.assertEqual(mutating, {"AddMarker", "SetCurrentTimeline"})
        self.assertEqual((self.project.current, self.resolve.page), (self.here, "color"))
        self.assertEqual(len(self.auto.tracks["video"][0]), 2)  # nothing cut
        self.assertEqual(self.human.GetMarkers(), {})

    def test_a_marker_that_reads_back_differently_fails(self):
        real = self.auto.AddMarker

        def renamed(frame, color, name, note, duration, custom=""):
            return real(frame, color, name + " (renamed)", note, duration, custom)
        self.auto.AddMarker = renamed
        dry, r = self.twice()
        self.assertEqual(r["exit_status"], 1)
        self.assertIn("read back differs in name", r["results"][0]["problem"])

    def test_a_refused_add_is_reported(self):
        self.auto.AddMarker = lambda *a: True  # says yes, adds nothing
        dry, r = self.twice()
        self.assertEqual(r["exit_status"], 1)
        self.assertIn("no marker at frame 10 after AddMarker (returned True)",
                      r["results"][0]["problem"])

    def test_markers_added_since_the_plan_stop_the_run(self):
        dry = self.run_it(dry_run=True)
        self.auto.markers[400.0] = {"color": "Red", "duration": 1.0, "note": "", "name": "new",
                                    "customData": ""}
        with self.assertRaisesRegex(workflows.Refused, "plan changed"):
            self.run_it(expect_sha=dry["plan_sha"])
        self.assertFalse(any(c[1] == "AddMarker" for c in rf.CALLS))

    @unittest.skipUnless(os.access(cutlist.FFMPEG, os.X_OK), "ffmpeg missing")
    def test_silences_from_the_audio_become_blue_markers(self):
        dry, r = self.twice(audio=True)
        self.assertEqual(r["exit_status"], 0, r)
        blue = [m for m in r["planned"] if m["color"] == "Blue"]
        # The first span's silences, 2-5 s and 7-8 s; the file ends before the second.
        self.assertEqual([(m["frame"], m["duration"]) for m in blue], [(50, 75), (175, 25)])
        self.assertEqual(r["counts"]["kind"]["silence"], 2)
        self.assertEqual(sorted(k for k, v in self.auto.GetMarkers().items()
                                if v["color"] == "Blue"), [50.0, 175.0])


class TestToolAndCommand(Base):
    def call(self, args):
        tool = server.build_registry().get("trim_review_markers")
        args = schema.with_defaults(tool.input_schema, {"project": P, **args})
        self.assertEqual(schema.validate(tool.input_schema, args), [])
        return tool.handler(args, ToolContext(session=rf.Session(self.resolve)))

    def test_mcp_dry_run_then_real_run(self):
        base = {"timeline": "Clip [auto]", "source": self.source, "words": self.words_path,
                "audio": False}
        dry = self.call(base)
        self.assertIn("would add 3 marker(s) to 'Clip [auto]'", dry["summary"])
        self.assertIn("nothing is cut", dry["summary"])
        r = self.call({**base, "dry_run": False, "plan_sha": dry["plan_sha"]})
        self.assertIn("added 3 of 3 marker(s)", r["summary"])
        with open(os.environ["RPRESOLVE_MCP_JOURNAL"], encoding="utf-8") as f:
            events = [json.loads(line)["event"] for line in f]
        self.assertEqual(events, ["started", "finished"])
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.call({"timeline": "Clip [auto]"})

    def test_mcp_summary_names_why_rows_were_refused(self):
        self.auto.markers[10.0] = {"color": "Green", "duration": 1.0, "note": "", "name": "mine",
                                   "customData": ""}
        dry = self.call({"timeline": "Clip [auto]", "source": self.source,
                         "words": self.words_path, "audio": False})
        self.assertIn("refused, left unmarked: 1 (a marker is already at this frame)",
                      dry["summary"])

    def test_cli_markers(self):
        ns = dict(input=self.source, words=self.words_path, out=None, silence_db="-45",
                  min_silence=0.8, tighten=1.2, cut=2.5, soft_fillers=False, no_audio=True,
                  markers=True, timeline="Clip [auto]", project=P, project_id=None,
                  dry_run=True, plan_sha=None)

        def run(**extra):
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(resolve_workflow, "get_resolve", return_value=self.resolve), \
                    redirect_stdout(out), redirect_stderr(err):
                status = resolve_workflow.cmd_trim_review(argparse.Namespace(**{**ns, **extra}))
            return status, out.getvalue(), err.getvalue()
        status, out, _ = run()
        self.assertEqual(status, 0)
        sha = out.split("plan_sha: ")[1].split()[0]
        status, out, err = run(dry_run=False, plan_sha=sha)
        self.assertEqual(status, 0, err)
        self.assertIn("Added and read back 3 of 3 marker(s); nothing was cut.", out)
        status, _, err = run(timeline="Isaac's edit")
        self.assertEqual(status, 1)
        self.assertIn("not an [auto] timeline", err)


if __name__ == "__main__":
    unittest.main()
