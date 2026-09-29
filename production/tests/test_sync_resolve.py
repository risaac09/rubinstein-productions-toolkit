"""
Tests for the Resolve half of sync (rpresolve.workflows.sync,
rpresolve.syncbuild, the MCP sync tool and the sync command) against the
shared Resolve fakes, with the measurement stubbed: the stacked [auto]
timeline and every placement read back, AddTrack before a new track
index, an AppendToTimeline that answers truthy and places nothing, the
refusals, drift reported and never corrected, and AutoSyncAudio run only
on clips the run imported and checked against the measured offset.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import argparse
import copy
import functools
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
from rpresolve import api, syncbuild, workflows  # noqa: E402

try:
    import numpy  # noqa: F401
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

P = "RP Automation Sandbox"


def report(offset_s, fps=25.0, match=True, exceeds=False, reasons=()):
    """A measurement as rpresolve.sync.measure returns it."""
    exact = offset_s * fps
    placed = int(exact + 0.5) if exact >= 0 else -int(-exact + 0.5)
    over_ms = 48.0 if exceeds else 0.0
    worst_ms = abs(exact - placed) / fps * 1000 + over_ms / 2
    return {"match": match, "reasons": list(reasons), "offset_s": offset_s,
            "head_offset_s": offset_s, "tail_offset_s": offset_s, "polarity": "normal",
            "coarse": None,
            "overlap": {"start_s": 0.0, "end_s": 60.0, "seconds": 60.0},
            "windows": [{"label": "head", "center_s": 15.0, "seconds": 30.0,
                         "offset_s": offset_s, "ncc": 0.8, "peak_ratio": 12.0,
                         "polarity": "normal"}],
            "frames": {"fps": fps, "exact": exact, "placed": placed,
                       "residual_frames": exact - placed,
                       "residual_ms": (exact - placed) / fps * 1000, "inherent_ms": 500 / fps},
            "drift": {"ppm": 80.0 if exceeds else 0.0, "ms_per_min": 4.8 if exceeds else 0.0,
                      "over_overlap_ms": over_ms, "over_overlap_frames": over_ms * fps / 1000,
                      "mid_s": 30.0, "offset_mid_s": offset_s, "worst_ms": worst_ms,
                      "worst_frames": worst_ms * fps / 1000, "threshold_frames": 0.5,
                      "exceeds": exceeds, "retime_pct": 99.992 if exceeds else 100.0,
                      "retime_offset_s": offset_s - 0.0024 if exceeds else offset_s,
                      "span_s": 30.0, "residuals_ms": [0.0], "max_residual_ms": 0.0,
                      "residual_limit_ms": 4.0},
            "thresholds": {}}


class Base(unittest.TestCase):
    OFFSET = 2.5  # the recorder started 2.5 s (62.5 -> 63 frames at 25 fps) after the camera

    def setUp(self):
        rf.CALLS.clear()
        rf.IMPORT_PROPS.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.realpath(self.tmp.name)
        env = mock.patch.dict(os.environ, {
            "RPRESOLVE_MCP_JOURNAL": os.path.join(self.dir, "writes.jsonl"),
            "RPRESOLVE_LOCK": os.path.join(self.dir, "lock")})
        env.start()
        self.addCleanup(env.stop)
        self.cam_path = self.file("A001.MOV")
        self.rec_path = self.file("REC01.WAV")
        self.cam2_path = self.file("B001.MOV")
        self.cam = rf.Clip("A001.MOV", "clip-cam", self.cam_props(self.cam_path))
        self.rec = rf.Clip("REC01.WAV", "clip-rec", self.rec_props(self.rec_path))
        self.cam2 = rf.Clip("B001.MOV", "clip-cam2", self.cam_props(self.cam2_path, 1400))
        self.bin = rf.Folder("Day 1", [self.cam, self.rec, self.cam2])
        self.here = rf.Timeline("Isaac's edit", "tl-here")
        self.project = rf.Project(timelines=[self.here], current=self.here,
                                  root=rf.Folder("Master", subs=[self.bin]))
        self.resolve = rf.Resolve(self.project, page="edit")
        self.measured = []

    def tearDown(self):
        self.tmp.cleanup()

    def file(self, name):
        path = os.path.join(self.dir, name)
        open(path, "wb").close()
        return path

    @staticmethod
    def cam_props(path, frames=1500):
        return {"File Path": path, "Type": "Video + Audio", "FPS": "25", "Frames": str(frames),
                "Audio Ch": "2"}

    @staticmethod
    def rec_props(path, frames=1600, channels=2):
        return {"File Path": path, "Type": "Audio", "FPS": "25", "Frames": str(frames),
                "Audio Ch": str(channels)}

    def measure(self, offset_s=None, **kw):
        def fake(reference, other, **args):
            self.measured.append((reference, other, args))
            return report(self.OFFSET if offset_s is None else offset_s, args.get("fps", 25.0),
                          **kw)
        return fake

    def probe(self, path):
        video = path.endswith(".MOV")
        return {"duration": 60.0 if video else 64.0,
                "audio": {"index": 1, "sample_rate": 48000, "channels": 2, "start": 0.0},
                "video": {"fps": 25.0, "start": 0.0} if video else None, "audio_start_s": 0.0}

    def run_it(self, reference="clip-cam", other="clip-rec", measure=None, **kw):
        return workflows.sync(self.resolve, P, reference, other,
                              measure=measure or self.measure(), probe=self.probe, **kw)

    def twice(self, *a, **kw):
        dry = self.run_it(*a, dry_run=True, **kw)
        return dry, self.run_it(*a, expect_sha=dry["plan_sha"], **kw)

    def built(self, name="A001 sync [auto]"):
        return next(t for t in self.project.timelines if t.GetName() == name)

    def items(self, tl, kind, track):
        return [(it.GetName(), it.GetStart() - tl.GetStartFrame(), it.GetSourceStartFrame(),
                 it.GetDuration()) for it in tl.GetItemListInTrack(kind, track) or []]


class TestSyncPool(Base):
    def test_dry_run_plans_and_writes_nothing(self):
        r = self.run_it(dry_run=True)
        self.assertEqual(rf.mutating_calls(), [])
        self.assertEqual(r["would_create"], ["A001 sync [auto]"])
        self.assertEqual((r["rate"], r["offset_frames"], r["mode"]), ("25", 63, "pool"))
        self.assertEqual([(p["role"], p["kind"], p["track"], p["record"], p["length"])
                          for p in r["plan"]],
                         [("reference", "video", 1, 0, 1500), ("reference", "audio", 1, 0, 1500),
                          ("other", "audio", 2, 63, 1600)])
        self.assertEqual(len(r["plan_sha"]), 64)
        self.assertEqual(self.measured[0][:2], (self.cam_path, self.rec_path))
        self.assertEqual(self.measured[0][2]["fps"], 25.0)

    def test_real_run_stacks_and_reads_every_placement_back(self):
        dry, r = self.twice()
        self.assertEqual(r["exit_status"], 0, r["problems"])
        tl = self.built()
        self.assertEqual(tl.GetStartFrame(), 90000)  # 01:00:00:00 at 25 fps
        self.assertEqual(self.items(tl, "video", 1), [("A001.MOV", 0, 0, 1500)])
        self.assertEqual(self.items(tl, "audio", 1), [("A001.MOV", 0, 0, 1500)])
        self.assertEqual(self.items(tl, "audio", 2), [("REC01.WAV", 63, 0, 1600)])
        self.assertTrue(all(p["ok"] for p in r["built"]["placements"]))
        # A2 was added (stereo, read back) before the recorder was appended to it.
        names = [c[1] for c in rf.CALLS]
        self.assertLess(names.index("AddTrack"), max(i for i, n in enumerate(names)
                                                     if n == "AppendToTimeline"))
        self.assertIn(("Timeline", "AddTrack", ("audio", "stereo")), rf.CALLS)
        # recordFrame is absolute: the start frame plus the planned offset.
        appends = [c[2][0][0] for c in rf.CALLS if c[1] == "AppendToTimeline"]
        self.assertEqual([(a["trackIndex"], a["recordFrame"], a.get("mediaType"), a["endFrame"])
                          for a in appends], [(1, 90000, None, 1500), (2, 90063, 2, 1600)])
        # The UI is put back; nothing else in the project changed.
        self.assertIs(self.project.current, self.here)
        self.assertEqual(r["ui_restore_problems"], [])
        self.assertEqual([t.GetName() for t in self.project.timelines],
                         ["Isaac's edit", "A001 sync [auto]"])
        self.assertFalse(any(c[1].startswith(("Delete", "AutoSync", "Import", "SetClip"))
                             for c in rf.CALLS))

    def test_a_negative_offset_moves_the_reference_instead(self):
        dry, r = self.twice(measure=self.measure(-1.2))
        self.assertEqual(r["exit_status"], 0, r["problems"])
        tl = self.built()
        self.assertEqual(self.items(tl, "video", 1), [("A001.MOV", 30, 0, 1500)])
        self.assertEqual(self.items(tl, "audio", 2), [("REC01.WAV", 0, 0, 1600)])

    def test_a_second_camera_goes_on_v2_and_a2(self):
        dry, r = self.twice(other="B001.MOV")
        self.assertEqual(r["exit_status"], 0, r["problems"])
        tl = self.built()
        self.assertEqual(self.items(tl, "video", 2), [("B001.MOV", 63, 0, 1400)])
        self.assertEqual(self.items(tl, "audio", 2), [("B001.MOV", 63, 0, 1400)])
        self.assertIn(("Timeline", "AddTrack", ("video",)), rf.CALLS)

    def test_clips_by_path_and_name(self):
        r = self.run_it(self.cam_path, "REC01.WAV", dry_run=True)
        self.assertEqual((r["reference"]["uid"], r["other"]["uid"]), ("clip-cam", "clip-rec"))

    def test_items_are_known_by_position_not_by_python_object(self):
        # Resolve hands back a new wrapper for the same item on every
        # GetItemListInTrack call; identity across calls proves nothing.
        real = self.project.pool.CreateEmptyTimeline

        def fresh_wrappers(name):
            tl = real(name)
            plain = tl.GetItemListInTrack
            tl.GetItemListInTrack = lambda kind, i: (
                None if plain(kind, i) is None else [copy.copy(it) for it in plain(kind, i)])
            return tl
        self.project.pool.CreateEmptyTimeline = fresh_wrappers
        dry, r = self.twice()
        self.assertEqual(r["problems"], [])
        self.assertEqual(r["exit_status"], 0)

    def test_add_track_that_adds_nothing_stops_the_other(self):
        real = self.project.pool.CreateEmptyTimeline

        def refusing(name):
            tl = real(name)
            tl.refuse_add_track = True
            return tl
        self.project.pool.CreateEmptyTimeline = refusing
        dry, r = self.twice()
        self.assertEqual(r["exit_status"], 1)
        self.assertTrue(any("AddTrack('audio') did not add a track" in p for p in r["problems"]))
        self.assertTrue(any("other A2: 0 item(s)" in p for p in r["problems"]), r["problems"])
        self.assertEqual(self.items(self.built(), "video", 1), [("A001.MOV", 0, 0, 1500)])

    def test_truthy_append_that_places_nothing_is_caught(self):
        self.project.pool.AppendToTimeline = lambda infos: [None]  # truthy, nothing placed
        dry, r = self.twice()
        self.assertEqual(r["exit_status"], 1)
        self.assertEqual(r["built"]["returned"], {"reference": True, "other": True})
        self.assertEqual(sum(1 for p in r["problems"] if "0 item(s)" in p), 3, r["problems"])

    def test_a_placement_off_by_more_than_a_frame_is_reported(self):
        real = self.project.pool.AppendToTimeline

        def late(infos):
            return real([{**i, "recordFrame": i["recordFrame"] + 2} for i in infos])
        self.project.pool.AppendToTimeline = late
        dry, r = self.twice()
        self.assertEqual(r["exit_status"], 1)
        self.assertIn("reference V1: record 2 (planned 0)", r["problems"])

    def test_a_placement_one_frame_off_is_reported(self):
        # Record and source start are whole frames this plan chose: one frame off
        # is wrong, on top of the half frame of rounding the offset already has.
        real = self.project.pool.AppendToTimeline

        def late(infos):
            return real([{**i, "recordFrame": i["recordFrame"] + (i["trackIndex"] == 2)}
                         for i in infos])
        self.project.pool.AppendToTimeline = late
        dry, r = self.twice()
        self.assertEqual(r["exit_status"], 1)
        self.assertEqual(r["problems"], ["other A2: record 64 (planned 63)"])

    def test_a_source_start_one_frame_in_is_reported_and_a_length_may_round(self):
        # Starting a frame into each source also shortens each by a frame: the
        # start is wrong, the length within the frame a rate conversion rounds.
        real = self.project.pool.AppendToTimeline

        def skewed(infos):
            return real([{**i, "startFrame": i["startFrame"] + 1} for i in infos])
        self.project.pool.AppendToTimeline = skewed
        dry, r = self.twice()
        self.assertEqual(r["exit_status"], 1)
        self.assertEqual(r["problems"], ["reference V1: source_start 1 (planned 0)",
                                         "reference A1: source_start 1 (planned 0)",
                                         "other A2: source_start 1 (planned 0)"])

    def test_drift_over_the_threshold_is_reported_not_corrected(self):
        dry, r = self.twice(measure=self.measure(exceeds=True))
        self.assertEqual(r["exit_status"], 2)
        self.assertTrue(r["measurement"]["drift"]["exceeds"])
        self.assertFalse(any(c[1] == "SetProperty" for c in rf.CALLS))  # no retime
        self.assertEqual(self.items(self.built(), "audio", 2), [("REC01.WAV", 63, 0, 1600)])

    def test_refusals_write_nothing(self):
        cases = [
            (dict(measure=self.measure(match=False, reasons=["head window: peak only 1.05x"])),
             "do not match well enough"),
            (dict(other="clip-cam"), "same media-pool clip"),
            (dict(other="nothing"), "no clip in the media pool"),
            (dict(name="Stacked"), r"must end with ' \[auto\]'"),
            (dict(autosync=True), "only on clips this run imports"),
        ]
        for kw, why in cases:
            with self.subTest(why=why):
                with self.assertRaisesRegex(workflows.Refused, why):
                    self.run_it(**kw)
        self.project.add(rf.Timeline("A001 sync [auto]", "tl-old"))
        with self.assertRaisesRegex(workflows.Refused, "already exists"):
            self.run_it()
        self.assertEqual(rf.mutating_calls(), [])

    def test_offline_media_and_unreported_frames_are_refused(self):
        os.remove(self.rec_path)
        with self.assertRaisesRegex(workflows.Refused, "not on disk"):
            self.run_it()
        self.rec.props["File Path"] = self.cam2_path
        self.cam.props["Frames"] = ""
        with self.assertRaisesRegex(workflows.Refused, "FPS and Frames"):
            self.run_it()

    def test_a_rate_resolve_has_no_timeline_for_is_refused(self):
        self.cam.props["FPS"] = "12.5"
        with self.assertRaisesRegex(workflows.Refused, "not a timeline frame rate"):
            self.run_it()

    def test_the_groups_of_an_inconsistent_pair_are_in_the_refusal(self):
        bad = report(2.5, match=False, reasons=["the windows disagree"])
        bad["groups"] = [{"offset_s": 2.5, "windows": ["w2", "w3"]},
                         {"offset_s": 2.316, "windows": ["head", "tail"]}]
        with self.assertRaisesRegex(workflows.Refused, r"\+2\.3160 s \(head, tail\)"):
            self.run_it(measure=lambda *a, **k: bad)

    def test_a_changed_plan_and_another_project_are_refused(self):
        dry = self.run_it(dry_run=True)
        with self.assertRaisesRegex(workflows.Refused, "plan changed"):
            self.run_it(measure=self.measure(3.0), expect_sha=dry["plan_sha"])
        with self.assertRaises(api.ProjectChanged):
            workflows.sync(self.resolve, "Other", "clip-cam", "clip-rec",
                           measure=self.measure(), dry_run=True)
        self.assertEqual(rf.mutating_calls(), [])

    def test_a_measurement_error_is_a_refusal(self):
        def broken(*a, **k):
            raise syncbuild.SyncError("ffmpeg could not decode the audio")
        with self.assertRaisesRegex(workflows.Refused, "could not be measured"):
            self.run_it(measure=broken)

        def buggy(*a, **k):
            raise NotImplementedError("a bug stays loud")
        with self.assertRaises(NotImplementedError):
            self.run_it(measure=buggy)


class TestSyncImport(Base):
    def setUp(self):
        super().setUp()
        self.new_cam = self.file("C001.MOV")
        self.new_rec = self.file("ZOOM0001.WAV")
        rf.IMPORT_PROPS[self.new_cam] = {"Type": "Video + Audio", "FPS": "25",
                                         "Frames": "1500", "Audio Ch": "2"}
        rf.IMPORT_PROPS[self.new_rec] = {"Type": "Audio", "FPS": "25", "Frames": "1601",
                                         "Audio Ch": "2"}

    def run_import(self, **kw):
        return self.twice(self.new_cam, self.new_rec, bin="Sync run", **kw)

    def test_imports_into_a_new_bin_and_builds(self):
        dry, r = self.run_import()
        self.assertEqual(dry["mode"], "import")
        self.assertIn("lengths_note", dry)
        self.assertEqual(r["exit_status"], 0, r["problems"])
        new_bin = next(f for f in self.project.pool.root.subs if f.name == "Sync run")
        self.assertEqual([c.name for c in new_bin.clips], ["C001.MOV", "ZOOM0001.WAV"])
        self.assertIs(self.project.pool.current, self.project.pool.root)  # folder put back
        tl = self.built("C001 sync [auto]")
        self.assertEqual(self.items(tl, "audio", 2), [("ZOOM0001.WAV", 63, 0, 1601)])
        self.assertEqual([x["path"] for x in r["imported"]], [self.new_cam, self.new_rec])
        # The timeline is made in the run's bin (CreateEmptyTimeline adds to the
        # current folder), and the folder Isaac had open is put back after.
        order = [(c[1], c[2][-1]) for c in rf.CALLS
                 if c[1] in ("SetCurrentFolder", "CreateEmptyTimeline")]
        self.assertEqual(order, [("SetCurrentFolder", "Sync run"),
                                 ("CreateEmptyTimeline", "C001 sync [auto]"),
                                 ("SetCurrentFolder", "Master")])

    def test_a_file_resolve_does_not_import_stops_before_building(self):
        real = self.project.pool.ImportMedia
        self.project.pool.ImportMedia = lambda paths: real(paths)[:1]
        dry = self.run_it(self.new_cam, self.new_rec, bin="Sync run", dry_run=True)
        with self.assertRaisesRegex(api.WriteNotApplied, "did not return a clip for .*ZOOM0001"):
            self.run_it(self.new_cam, self.new_rec, bin="Sync run", expect_sha=dry["plan_sha"])
        self.assertFalse(any(c[1] == "CreateEmptyTimeline" for c in rf.CALLS))
        self.assertIs(self.project.pool.current, self.project.pool.root)
        self.assertIs(self.project.current, self.here)

    def test_dry_run_imports_nothing(self):
        self.run_it(self.new_cam, self.new_rec, bin="Sync run", dry_run=True)
        self.assertEqual(rf.mutating_calls(), [])

    def test_an_existing_bin_or_pooled_file_is_refused(self):
        with self.assertRaisesRegex(workflows.Refused, "already exists"):
            self.run_it(self.new_cam, self.new_rec, bin="Day 1")
        with self.assertRaisesRegex(workflows.Refused, "already in the media pool"):
            self.run_it(self.new_cam, self.rec_path, bin="Sync run")
        with self.assertRaisesRegex(workflows.Refused, "absolute paths of files"):
            self.run_it("C001.MOV", self.new_rec, bin="Sync run")
        self.assertEqual(rf.mutating_calls(), [])

    def test_autosync_that_agrees(self):
        self.project.pool.autosync_offset = 63
        dry, r = self.run_import(autosync=True)
        self.assertEqual(dry["would_create"],
                         ["C001 sync [auto]", "C001 sync (AutoSyncAudio) [auto]"])
        a = r["autosync"]
        self.assertEqual((a["verdict"], a["implied_offset_s"]), ("agrees", 2.52), r["problems"])
        self.assertEqual(r["exit_status"], 0, r["problems"])
        self.assertIn("Synced Audio", a["changed_properties"])
        (_, _, (clips, settings)), = [c for c in rf.CALLS if c[1] == "AutoSyncAudio"]
        self.assertEqual(clips, ["C001.MOV", "ZOOM0001.WAV"])
        self.assertEqual(settings, {10.0: 20.0, 11.0: -2, 12.0: True, 13.0: True})
        # AutoSyncAudio ran after the stacked timeline was built and read back.
        names = [c[1] for c in rf.CALLS]
        self.assertLess(names.index("AppendToTimeline"), names.index("AutoSyncAudio"))

    def test_autosync_that_disagrees_fails(self):
        self.project.pool.autosync_offset = 70
        dry, r = self.run_import(autosync=True)
        self.assertEqual(r["autosync"]["verdict"], "disagrees")
        self.assertEqual(r["exit_status"], 1)
        self.assertTrue(any("more than a frame apart" in p for p in r["problems"]))

    def test_autosync_that_cannot_be_read_back_is_unverified(self):
        self.project.pool.autosync_offset = 63
        self.project.pool.autosync_separate = False
        dry, r = self.run_import(autosync=True)
        self.assertEqual(r["autosync"]["verdict"], "unverifiable")
        self.assertEqual(r["exit_status"], 2)
        self.assertIn("measured offset alone", r["autosync"]["note"])

    def test_autosync_needs_its_constants(self):
        with mock.patch.object(rf.Resolve, "AUDIO_SYNC_WAVEFORM", None):
            with self.assertRaisesRegex(workflows.Refused, "resolve.AUDIO_SYNC_WAVEFORM"):
                self.run_it(self.new_cam, self.new_rec, bin="Sync run", autosync=True)


class TestRateAndPlan(unittest.TestCase):
    def test_rate_strings(self):
        self.assertEqual(syncbuild.rate_string(30000 / 1001), "29.97")
        self.assertEqual(syncbuild.rate_string(24000 / 1001), "23.976")
        self.assertEqual(syncbuild.rate_string(60.0), "60")
        self.assertIsNone(syncbuild.rate_string(12.5))
        self.assertIsNone(syncbuild.rate_string("x"))

    def test_audio_subtypes(self):
        self.assertEqual([syncbuild.audio_subtype(n) for n in (1, 2, 4, 6, 0)],
                         ["mono", "stereo", "adaptive4", "5.1", "stereo"])

    def test_lengths_convert_between_rates(self):
        ref = {"fps": 25.0, "frames": 250, "video": True, "channels": 2}
        other = {"fps": 50.0, "frames": 500, "video": True, "channels": 2}
        p = syncbuild.plan(ref, other, 10, 25.0)
        self.assertEqual([(x["role"], x["record"], x["length"]) for x in p["placements"]],
                         [("reference", 0, 250), ("reference", 0, 250), ("other", 10, 250),
                          ("other", 10, 250)])
        self.assertEqual(p["appends"][1]["endFrame"], 500)  # source frames at its own rate


class TestSyncCommandAndTool(Base):
    def cli(self, **extra):
        ns = dict(reference="clip-cam", other="clip-rec", project=P, project_id=None, bin=None,
                  name=None, autosync=False, window=None, dry_run=False, plan_sha=None)
        ns.update(extra)
        sync = functools.partial(workflows.sync, measure=self.measure(), probe=self.probe)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(resolve_workflow, "get_resolve", return_value=self.resolve), \
                mock.patch.object(resolve_workflow.rpwork, "sync", sync), \
                redirect_stdout(out), redirect_stderr(err):
            status = resolve_workflow.cmd_sync(argparse.Namespace(**ns))
        return status, out.getvalue(), err.getvalue()

    def test_cli_dry_run_then_real_run(self):
        status, out, _ = self.cli(dry_run=True)
        self.assertEqual(status, 0)
        self.assertIn("placed at +63", out)
        sha = out.split("plan_sha: ")[1].split()[0]
        status, out, err = self.cli(plan_sha=sha)
        self.assertEqual(status, 0, err)
        self.assertIn("ok   other     A2: read back from +63", out)

    def test_cli_refusal_exits_1(self):
        status, _, err = self.cli(other="clip-cam")
        self.assertEqual(status, 1)
        self.assertIn("same media-pool clip", err)

    @unittest.skipUnless(HAS_NUMPY, "numpy not installed")
    def test_mcp_tool_dry_run_then_real_run_journalled(self):
        from rpresolve import sync as rpsync
        from rpresolve.mcp import schema, server
        from rpresolve.mcp.registry import ToolContext
        tool = server.build_registry().get("sync")
        self.assertFalse(tool.annotations["readOnlyHint"])

        def call(args):
            args = schema.with_defaults(tool.input_schema, {"project": P, **args})
            self.assertEqual(schema.validate(tool.input_schema, args), [])
            return tool.handler(args, ToolContext(session=rf.Session(self.resolve)))
        with mock.patch.object(rpsync, "measure", self.measure()):
            dry = call({"reference": "clip-cam", "other": "clip-rec"})
            self.assertIn("would build 'A001 sync [auto]'", dry["summary"])
            self.assertIn(dry["plan_sha"], dry["summary"])
            with self.assertRaisesRegex(workflows.Refused, "plan_sha"):
                call({"reference": "clip-cam", "other": "clip-rec", "dry_run": False})
            r = call({"reference": "clip-cam", "other": "clip-rec", "dry_run": False,
                      "plan_sha": dry["plan_sha"]})
        self.assertIn("3 of 3 placement(s) read back", r["summary"])
        with open(os.environ["RPRESOLVE_MCP_JOURNAL"], encoding="utf-8") as f:
            events = [json.loads(line) for line in f]
        self.assertEqual([(e["event"], e["tool"]) for e in events],
                         [("started", "sync"), ("finished", "sync")])


if __name__ == "__main__":
    unittest.main()
