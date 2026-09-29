"""
Tests for queueing a render for a delivery destination
(rpresolve.workflows.queue_render with destination=, render.py's
destination helpers) against the shared Resolve fakes: dry run then
plan_sha, every pre-check refusal, a refused Deliver setting, the read
back from the render queue, and that nothing is ever started.

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
from rpresolve import api, config as rpconfig, render, workflows  # noqa: E402

P = "RP Automation Sandbox"
CLIP = {"show": "SW", "episode": 1, "guest": "Guest", "index": 1, "slug": "example-clip"}


class Base(unittest.TestCase):
    def setUp(self):
        rf.CALLS.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.realpath(self.tmp.name)
        self.out = os.path.join(self.dir, "renders")
        os.makedirs(self.out)
        env = mock.patch.dict(os.environ, {
            "RPRESOLVE_MCP_JOURNAL": os.path.join(self.dir, "writes.jsonl"),
            "RPRESOLVE_LOCK": os.path.join(self.dir, "lock")})
        env.start()
        self.addCleanup(env.stop)
        self.config = rpconfig.load_config("/nonexistent/config.json")
        clip = rf.Clip("P1.MOV", "clip-1", {"File Path": "/w/P1.MOV"})
        caption = rf.Item("Subtitle 1", 86400, 86424, None, nodes=None)
        self.wide = rf.Timeline("Clip [auto]", "tl-wide", "23.976", tracks={
            "video": [[rf.Item("P1.MOV", 86400, 86500, clip)]],
            "audio": [[rf.Item("P1.MOV", 86400, 86500, clip, nodes=None)]],
            "subtitle": [[caption]]})
        self.bare = rf.Timeline("Bare [auto]", "tl-bare", 25.0, tracks={
            "video": [[rf.Item("P1.MOV", 86400, 86500, clip)]], "audio": [[]]},
            settings={"timelineResolutionWidth": "1920", "timelineResolutionHeight": "800"})
        self.here = rf.Timeline("Where Isaac is", "tl-here")
        self.project = rf.Project(timelines=[self.wide, self.bare, self.here],
                                  current=self.here)
        self.project.fmt = {"format": "mp4", "codec": "H264"}
        self.resolve = rf.Resolve(self.project, page="cut")

    def tearDown(self):
        self.tmp.cleanup()

    def queue(self, dest="youtube_16x9", timeline="Clip [auto]", parts=CLIP, **kw):
        return workflows.queue_render(self.resolve, P, timeline, output_dir=kw.pop("out", self.out),
                                      destination=dest, name_parts=parts, config=self.config, **kw)

    def twice(self, **kw):
        dry = self.queue(dry_run=True, **kw)
        return dry, self.queue(expect_sha=dry["plan_sha"], **kw)


class TestQueueDestination(Base):
    def test_dry_run_changes_nothing(self):
        r = self.queue(dry_run=True)
        name = "SW001_Guest_01_example-clip_16x9.mp4"
        self.assertEqual(r["output"], os.path.join(self.out, name))
        self.assertEqual(r["sidecar"], os.path.join(self.out, name[:-4] + ".srt"))
        self.assertEqual(r["timeline"]["subtitle_tracks"], [1])
        self.assertIn("GammaTag", r["deliver_changed"])
        self.assertEqual(len(r["plan_sha"]), 64)
        self.assertEqual(rf.mutating_calls(), [])
        self.assertEqual(self.project.jobs, [])

    def test_real_run_queues_reads_back_and_restores(self):
        dry, r = self.twice()
        self.assertEqual(r["exit_status"], 0, r)
        job = r["job"]
        self.assertEqual((job["OutputFilename"], job["TargetDir"], job["TimelineName"]),
                         ("SW001_Guest_01_example-clip_16x9.mp4", self.out, "Clip [auto]"))
        self.assertEqual((job["FormatWidth"], job["FormatHeight"], job["VideoCodec"]),
                         (3840, 2160, "H265"))
        self.assertEqual(r["readback_problems"], [])
        # fields the fake's job list does not report stay unverified, by name
        for key in ("ColorSpaceTag", "GammaTag", "ExportSubtitle", "SubtitleFormat"):
            self.assertIn(key, r["unverified"])
        rs = self.project.render_settings
        self.assertEqual((rs["ColorSpaceTag"], rs["GammaTag"], rs["SubtitleFormat"],
                          rs["FrameRate"], rs["AudioCodec"], rs["CustomName"]),
                         ("Rec.709", "Gamma 2.4", "SeparateFile", 23.976, "aac",
                          "SW001_Guest_01_example-clip_16x9"))
        self.assertEqual(self.project.fmt, {"format": "mp4", "codec": "H264"})  # put back
        self.assertEqual((self.resolve.page, self.project.current), ("cut", self.here))
        self.assertNotIn("StartRendering", [c[1] for c in rf.CALLS])
        self.assertEqual(len(self.project.jobs), 1)

    def test_one_job_per_destination_each_named_for_it(self):
        names = []
        for key in ("youtube_16x9", "youtube_16x9_hd", "linkedin_16x9", "substack_16x9"):
            parts = {**CLIP, "slug": key.replace("_", "-")}
            names.append(self.twice(dest=key, parts=parts)[1]["job"]["OutputFilename"])
        for key in ("linkedin_9x16", "linkedin_1x1"):
            names.append(self.twice(dest=key)[1]["job"]["OutputFilename"])
        r = self.twice(dest="client_master", timeline="Bare [auto]",
                       parts={"client": "Client", "slug": "example-slug"})[1]
        names.append(r["job"]["OutputFilename"])
        self.assertEqual((r["job"]["FormatWidth"], r["job"]["FormatHeight"]), (1920, 800))
        self.assertEqual(r["job"]["AudioBitDepth"], 24)
        self.assertEqual(self.project.render_settings["ExportSubtitle"], False)
        self.assertEqual(len(set(names)), 7)
        self.assertIn("SW001_Guest_01_example-clip_9x16.mp4", names)
        self.assertIn("Client_example-slug_master.mov", names)

    def test_timeline_size_falls_back_to_the_project(self):
        r = self.queue(dest="client_master", timeline="Clip [auto]", dry_run=True,
                       parts={"client": "Client", "slug": "s"})
        self.assertEqual((r["settings"][0]["settings"]["FormatWidth"],
                          r["settings"][0]["settings"]["FormatHeight"]), (3840, 2160))

    def test_stale_sha_is_refused(self):
        with self.assertRaisesRegex(workflows.Refused, "plan changed"):
            self.queue(expect_sha="0" * 64)
        self.assertEqual(self.project.jobs, [])


class TestRefusals(Base):
    def test_captions_without_a_subtitle_track(self):
        with self.assertRaisesRegex(workflows.Refused, "no subtitle track"):
            self.queue(timeline="Bare [auto]", dry_run=True)
        with self.assertRaisesRegex(workflows.Refused, "no subtitle track"):
            self.queue(dest="linkedin_9x16", timeline="Bare [auto]", dry_run=True)
        self.wide.tracks["subtitle"] = [[]]
        with self.assertRaisesRegex(workflows.Refused, "empty"):
            self.queue(dry_run=True)

    def test_existing_file_or_sidecar_or_queued_job(self):
        name = os.path.join(self.out, "SW001_Guest_01_example-clip_16x9")
        open(name + ".mp4", "w").close()
        with self.assertRaisesRegex(workflows.Refused, "already exists"):
            self.queue(dry_run=True)
        os.remove(name + ".mp4")
        open(name + ".srt", "w").close()
        with self.assertRaisesRegex(workflows.Refused, "caption file"):
            self.queue(dry_run=True)
        os.remove(name + ".srt")
        self.twice()
        with self.assertRaisesRegex(workflows.Refused, "render queue"):
            self.queue(dry_run=True)

    def test_folder_rules(self):
        with self.assertRaisesRegex(workflows.Refused, "does not exist"):
            self.queue(out=os.path.join(self.dir, "nope"), dry_run=True)
        repo = os.path.join(self.dir, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        with self.assertRaisesRegex(workflows.Refused, "git working tree"):
            self.queue(out=repo, dry_run=True)
        volumes = os.path.join(self.dir, "Volumes")
        leftover = os.path.join(volumes, "Work", "renders")
        os.makedirs(leftover)
        with self.assertRaisesRegex(workflows.Refused, "not mounted"):
            self.queue(out=leftover, volumes_root=volumes, dry_run=True)
        with self.assertRaisesRegex(workflows.Refused, "no target folder"):
            self.queue(out=None, dry_run=True)

    def test_names_destinations_and_a_running_render(self):
        with self.assertRaisesRegex(workflows.Refused, "guest"):
            self.queue(parts={**CLIP, "guest": "Two Words"}, dry_run=True)
        with self.assertRaisesRegex(workflows.Refused, "unknown destination"):
            self.queue(dest="tiktok", dry_run=True)
        self.project.rendering = True
        with self.assertRaisesRegex(workflows.Refused, "render is running"):
            self.queue(dry_run=True)

    def test_wrong_project_is_refused(self):
        with self.assertRaises(api.ProjectChanged):
            workflows.queue_render(self.resolve, "Other", "Clip [auto]", output_dir=self.out,
                                   destination="youtube_16x9", name_parts=CLIP,
                                   config=self.config, dry_run=True)

    def test_a_refused_required_setting_queues_nothing(self):
        self.project.refuse = {"GammaTag"}
        dry, r = self.twice()
        self.assertEqual(r["exit_status"], 1)
        self.assertIn("GammaTag", r["warnings"][0])
        self.assertEqual(self.project.jobs, [])
        self.assertEqual(self.project.fmt, {"format": "mp4", "codec": "H264"})

    def test_a_refused_optional_setting_is_a_warning(self):
        self.project.refuse = {"NetworkOptimization"}
        dry, r = self.twice()
        self.assertEqual(r["exit_status"], 0)
        self.assertTrue(any("NetworkOptimization" in w for w in r["warnings"]))
        self.assertEqual(len(self.project.jobs), 1)

    def test_a_codec_resolve_does_not_offer_queues_nothing(self):
        self.config["destinations"]["youtube_16x9"]["codec"] = "AV1"
        self.config["destinations"]["youtube_16x9"]["codec_fallbacks"] = []
        dry, r = self.twice()
        self.assertEqual(r["exit_status"], 1)
        self.assertIn("not offered", r["warnings"][0])
        self.assertEqual(self.project.jobs, [])


class TestReadback(unittest.TestCase):
    def test_exact_loose_and_unverified(self):
        job = {"TargetDir": "/out/", "OutputFilename": "a.mp4", "FormatWidth": "1920",
               "FrameRate": "25", "VideoCodec": "H.265", "AudioCodec": "Linear PCM"}
        problems, warnings, unverified = render.readback_report(
            job, {"TargetDir": "/out", "OutputFilename": "a.mp4", "FormatWidth": 1080,
                  "FrameRate": 25.0, "AudioSampleRate": 48000},
            {"VideoCodec": ["H265", "H.265"], "AudioCodec": ["lpcm"], "VideoFormat": ["mp4"]})
        self.assertEqual(problems, ["FormatWidth: queued '1920', expected 1080"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("AudioCodec", warnings[0])
        self.assertEqual(unverified, ["AudioSampleRate", "VideoFormat"])

    def test_queued_outputs(self):
        project = rf.Project(jobs=[{"JobId": "j1", "TargetDir": "/a", "OutputFilename": "b.mp4"},
                                   {"JobId": "j2"}])
        self.assertEqual(render.queued_outputs(project), {os.path.realpath("/a/b.mp4")})


if __name__ == "__main__":
    unittest.main()
