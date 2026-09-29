"""
Tests for queueing a render for a delivery destination
(rpresolve.workflows.queue_render with destination=, render.py's
destination helpers) against the shared Resolve fakes: dry run then
plan_sha, every pre-check refusal, a refused Deliver setting, the read
back from the render queue, and that nothing is ever started.

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
from rpresolve import api, config as rpconfig, render, workflows  # noqa: E402
from rpresolve.mcp import schema, server  # noqa: E402
from rpresolve.mcp.registry import ToolContext  # noqa: E402

REG = server.build_registry()

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
        self.tall = self.shaped("Clip 9x16 [auto]", "tl-tall", 1080, 1920)
        self.square = self.shaped("Clip 1x1 [auto]", "tl-square", 1080, 1080)
        self.project = rf.Project(timelines=[self.wide, self.bare, self.here, self.tall,
                                             self.square], current=self.here)
        self.project.fmt = {"format": "mp4", "codec": "H264"}
        self.resolve = rf.Resolve(self.project, page="cut")

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def shaped(name, uid, width, height):
        """A captioned timeline of its own size."""
        clip = rf.Clip("P1.MOV", "clip-1", {"File Path": "/w/P1.MOV"})
        return rf.Timeline(name, uid, "23.976", tracks={
            "video": [[rf.Item("P1.MOV", 86400, 86500, clip)]],
            "audio": [[rf.Item("P1.MOV", 86400, 86500, clip, nodes=None)]],
            "subtitle": [[rf.Item("Subtitle 1", 86400, 86424, None, nodes=None)]]},
            settings={"timelineResolutionWidth": str(width),
                      "timelineResolutionHeight": str(height)})

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
        self.assertEqual(rs["DataBurnIn"], "None")
        self.assertIn("VideoQuality", r["carried_over"])
        self.assertIn("render mode Single clip (put back)", r["deliver_changed"])

    def test_the_job_is_queued_in_single_clip_mode_and_the_mode_put_back(self):
        self.project.render_mode = 0  # Individual clips, as left after dailies
        modes = []
        add = self.project.AddRenderJob

        def add_and_note():
            modes.append(self.project.render_mode)
            return add()
        self.project.AddRenderJob = add_and_note
        dry, r = self.twice()
        self.assertEqual(r["exit_status"], 0, r)
        self.assertEqual(modes, [1])
        self.assertEqual(self.project.render_mode, 0)  # put back
        self.assertEqual(r["warnings"], [])

    def test_a_render_mode_that_does_not_take_queues_nothing(self):
        self.project.render_mode = 0
        self.project.stuck_mode = True  # SetCurrentRenderMode says yes, the mode stays 0
        dry, r = self.twice()
        self.assertEqual(r["exit_status"], 1)
        self.assertIn("Single clip", r["warnings"][0])
        self.assertEqual(self.project.jobs, [])
        self.assertEqual(self.project.fmt, {"format": "mp4", "codec": "H264"})

    def test_one_job_per_destination_each_named_for_it(self):
        names = []
        for key in ("youtube_16x9", "youtube_16x9_hd", "linkedin_16x9", "substack_16x9"):
            parts = {**CLIP, "slug": key.replace("_", "-")}
            names.append(self.twice(dest=key, parts=parts)[1]["job"]["OutputFilename"])
        for key, tl in (("linkedin_9x16", "Clip 9x16 [auto]"), ("linkedin_1x1", "Clip 1x1 [auto]")):
            r = self.twice(dest=key, timeline=tl)[1]
            self.assertEqual(r["exit_status"], 0, r)
            names.append(r["job"]["OutputFilename"])
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
    def test_a_timeline_of_another_shape_is_refused(self):
        cases = (("linkedin_9x16", "Clip [auto]", "portrait"),    # 3840x2160, the project's
                 ("linkedin_1x1", "Clip [auto]", "square"),
                 ("youtube_16x9", "Clip 9x16 [auto]", "landscape"),
                 ("linkedin_16x9", "Clip 1x1 [auto]", "landscape"),
                 ("linkedin_9x16", "Clip 1x1 [auto]", "portrait"))
        for key, tl, shape in cases:
            with self.subTest(dest=key, timeline=tl):
                with self.assertRaisesRegex(workflows.Refused, f"{shape}.*with bars"):
                    self.queue(dest=key, timeline=tl, dry_run=True)
        self.assertEqual((self.project.jobs, rf.mutating_calls()), ([], []))

    def test_the_same_orientation_must_still_match_within_one_percent(self):
        self.project.add(self.shaped("DCI [auto]", "tl-dci", 4096, 2160))
        with self.assertRaisesRegex(workflows.Refused, "1.896:1.*1.778:1"):
            self.queue(timeline="DCI [auto]", dry_run=True)
        self.project.add(self.shaped("Near [auto]", "tl-near", 1920, 1088))  # 0.7% off
        r = self.queue(timeline="Near [auto]", dry_run=True)
        self.assertEqual(r["timeline"]["size"], [1920, 1088])

    def test_an_unreadable_timeline_size_is_refused_for_a_fixed_size(self):
        self.project.settings.pop("timelineResolutionWidth")
        with self.assertRaisesRegex(workflows.Refused, "could not be read"):
            self.queue(dry_run=True)

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


class TestMCP(Base):
    """queue_render with a destination, as the protocol calls it."""

    def call(self, args):
        tool = REG.get("queue_render")
        args = schema.coerce(tool.input_schema, {"project": P, "timeline": "Clip [auto]",
                                                 **args})
        errors = schema.validate(tool.input_schema, args)
        if errors:
            raise AssertionError(errors)
        return tool.handler(schema.with_defaults(tool.input_schema, args),
                            ToolContext(session=rf.Session(self.resolve)))

    def args(self, **kw):
        return {"destination": "linkedin_16x9", "name": dict(CLIP), "target_dir": self.out, **kw}

    def journal(self):
        path = os.environ["RPRESOLVE_MCP_JOURNAL"]
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(line)["event"] for line in f]

    def test_dry_run_then_real_run(self):
        dry = self.call(self.args())
        self.assertIn("would queue 'Clip [auto]' for LinkedIn 16:9", dry["summary"])
        self.assertIn("sidecar SW001_Guest_01_example-clip_16x9.srt", dry["summary"])
        self.assertIn(dry["plan_sha"], dry["summary"])
        self.assertIn("render mode Single clip (put back)", dry["summary"])
        self.assertIn("VideoQuality", dry["summary"].split("as it stands:")[1])
        self.assertEqual((self.project.jobs, self.journal()), ([], []))
        with self.assertRaisesRegex(workflows.Refused, "plan_sha"):
            self.call(self.args(dry_run=False))
        real = self.call(self.args(dry_run=False, plan_sha=dry["plan_sha"]))
        self.assertEqual(real["exit_status"], 0, real["summary"])
        self.assertIn("NOT started", real["summary"])
        self.assertIn("GammaTag", real["summary"])  # named as unverified
        self.assertEqual(real["job"]["OutputFilename"], "SW001_Guest_01_example-clip_16x9.mp4")
        self.assertEqual(self.journal(), ["started", "finished"])
        self.assertEqual(real["journal"]["path"], os.environ["RPRESOLVE_MCP_JOURNAL"])
        self.assertNotIn("StartRendering", [c[1] for c in rf.CALLS])

    def test_preset_or_destination_never_both(self):
        with self.assertRaisesRegex(workflows.Refused, "exactly one"):
            self.call(self.args(preset="master"))
        with self.assertRaisesRegex(workflows.Refused, "exactly one"):
            self.call({"output_dir": self.out})
        with self.assertRaisesRegex(ValueError, "target_dir and name"):
            self.call(self.args(output_dir=self.out))
        with self.assertRaisesRegex(ValueError, "go with destination"):
            self.call({"preset": "master", "output_dir": self.out, "name": dict(CLIP)})
        # the preset form still works as before
        r = self.call({"preset": "master", "output_dir": self.out})
        self.assertTrue(r["output"].endswith("Clip [auto]_master.mov"))

    def test_schema_and_argument_refusals(self):
        for bad in ({"destination": "tiktok"}, {"name": {**CLIP, "slug": "Bad Slug"}},
                    {"name": {**CLIP, "index": 0}}, {"name": {**CLIP, "extra": "x"}}):
            with self.subTest(bad=bad):
                with self.assertRaises(AssertionError):
                    self.call(self.args(**bad))
        with self.assertRaisesRegex(ValueError, "absolute"):
            self.call(self.args(target_dir="relative/out"))
        with self.assertRaisesRegex(workflows.Refused, "missing guest"):
            self.call(self.args(name={k: v for k, v in CLIP.items() if k != "guest"}))
        with self.assertRaisesRegex(workflows.Refused, "no subtitle track"):
            self.call(self.args(timeline="Bare [auto]"))

    def test_an_overlay_can_carry_the_target_folder(self):
        overlay = os.path.join(self.dir, "overlay.json")
        with open(overlay, "w", encoding="utf-8") as f:
            json.dump({"destinations": {"linkedin_16x9": {"target_dir": self.out}}}, f)
        args = {k: v for k, v in self.args().items() if k != "target_dir"}
        with mock.patch.dict(os.environ, {"RPRESOLVE_CONFIG": overlay}):
            r = self.call(args)
        self.assertEqual(os.path.dirname(r["output"]), self.out)
        with mock.patch.dict(os.environ, {"RPRESOLVE_CONFIG": overlay + ".missing"}):
            with self.assertRaisesRegex(ValueError, "not a file"):
                self.call(args)
        with self.assertRaisesRegex(workflows.Refused, "no target folder"):
            self.call(args)

    def test_the_tool_list(self):
        names = [t.name for t in REG.list()]
        self.assertEqual(len(names), 16)
        self.assertIn("deliver_check", names)
        schema_ = REG.get("queue_render").input_schema
        self.assertEqual(schema_["required"], ["project", "timeline"])
        self.assertIn("client_master", schema_["properties"]["destination"]["enum"])
        self.assertTrue(REG.get("deliver_check").annotations["readOnlyHint"])


if __name__ == "__main__":
    unittest.main()
