"""
Tests for the assemble command (rpresolve.cmd_assemble): the offline plan (frames at the
manifest's rate, a continued take, a dissolve as a Cross Dissolve, the end black) and
apply_assemble against the shared Resolve fakes: the [auto]-only guard, the frame-rate
refusal, dry run and plan_sha, clips cut at their own rate onto a 24 fps timeline, the
static scale, the music bed (baked by ffmpeg with its fade, the baked file on A1 and the
original never placed), and the read-back, and the colour handling
(T30): detect's colour space per source in the dry run, the tag written and read back on a
new import, a clip already in the pool never written, an unmanaged project left alone.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import json
import os
import subprocess
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
from rpresolve import delivercheck as dc  # noqa: E402
from rpresolve.workflows import Refused  # noqa: E402

REAL_BAKE, REAL_SOURCE_SECONDS = a.musicbed.bake, a.musicbed.source_seconds  # before Base stubs them
needs_ffmpeg = unittest.skipUnless(
    all(os.access(t, os.X_OK) for t in (dc.FFMPEG, dc.FFPROBE)), "ffmpeg or ffprobe missing")

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
        # the bed is baked by ffmpeg; here bake() records its call and writes a stub file
        self.bed_dir = os.path.join(d, "beds")
        self.baked = []
        self.bake_error = None
        self.song_seconds = 60.0  # what the dry run's ffprobe finds in the stub song
        for new in (mock.patch.object(a.musicbed, "bake", self.fake_bake),
                    mock.patch.object(a.musicbed, "source_seconds", lambda src: self.song_seconds)):
            new.start()
            self.addCleanup(new.stop)
        self.here = rf.Timeline("Where", "tl-here")
        self.project = rf.Project(timelines=[self.here], current=self.here)
        self.project.settings["timelineFrameRate"] = "24"
        self.resolve = rf.Resolve(self.project, page="cut")

    def fake_bake(self, src, in_s, seconds, fade_s, out):
        if self.bake_error:
            raise a.musicbed.BedError(self.bake_error)
        self.baked.append((src, in_s, seconds, fade_s, out))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        Path(out).write_bytes(b"x")
        return seconds

    def go(self, **kw):
        args = {"name": "Shoot m v1 [auto]", "manifest": self.manifest, "bed_dir": self.bed_dir}
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
        self.assertEqual(audio[0].source, (0, 54))  # the in point is in the bed: it starts at its frame 0
        self.assertEqual([c for c in rf.CALLS if c[1] == "SetFades"], [])  # T18: baked, not set
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


class TestBed(Base):
    """The music is cut and faded by ffmpeg before the Resolve write, and the baked file is what
    goes on A1 (T18: SetFades reads back and did not render)."""

    def audio_imports(self):
        return [p for c in rf.CALLS if c[1] == "ImportMedia" for p in c[2][0]
                if not p.endswith(".mov")]

    def test_the_baked_file_goes_on_a1_and_the_original_does_not(self):
        dry = self.go()
        r = self.go(dry_run=False, expect_sha=dry["plan_sha"])
        self.assertEqual((r["exit_status"], r["problems"]), (0, []))
        (src, in_s, seconds, fade_s, out), = self.baked
        self.assertEqual((src, in_s), (self.song, 8.0))
        self.assertEqual(seconds, 2.25)  # 42 picture frames and 12 of black at 24 fps
        self.assertEqual(fade_s, 0.5625)  # one second, never more than a quarter of the bed
        self.assertEqual(os.path.dirname(out), self.bed_dir)
        self.assertEqual(self.audio_imports(), [out])  # the original song is never imported
        tl = next(t for t in self.project.timelines if t.name == "Shoot m v1 [auto]")
        (item,) = tl.GetItemListInTrack("audio", 1)
        self.assertEqual(item.clip.props["File Path"], out)
        self.assertEqual(r["audio"]["frames"], 54)
        self.assertEqual((r["bed"]["path"], r["bed"]["result"]), (out, "baked"))

    def test_the_dry_run_lists_the_bed_and_fade_and_bakes_nothing(self):
        dry = self.go()
        self.assertEqual(self.baked, [])
        self.assertFalse(os.path.exists(self.bed_dir))
        bed = dry["bed"]
        self.assertEqual((bed["src"], bed["in"], bed["fade_s"], bed["result"]),
                         (self.song, 8.0, 0.5625, "would bake"))
        self.assertTrue(bed["path"].startswith(self.bed_dir) and bed["path"].endswith(".wav"))

    def test_a_long_bed_fades_for_one_second(self):
        long = {**self.manifest, "clips": [{"name": "m", "spans": [
            {"source": "s1", "in": 2.0, "out": 12.0}]}]}
        self.assertEqual(self.go(manifest=long)["bed"]["fade_s"], 1.0)

    def test_the_plan_hash_holds_the_bed(self):
        base = self.go()["plan_sha"]
        self.assertEqual(self.go()["plan_sha"], base)
        self.assertNotEqual(self.go(bed_dir=self.bed_dir + "2")["plan_sha"], base)
        later = {**self.manifest, "music": {"path": self.song, "in": 9.0}}
        self.assertNotEqual(self.go(manifest=later)["plan_sha"], base)
        Path(self.song).write_bytes(b"a different song")  # a changed source is a new bed
        self.assertNotEqual(self.go()["plan_sha"], base)

    def test_the_bed_sits_beside_the_manifest_file_by_default(self):
        path = os.path.join(os.path.realpath(self.tmp.name), "m.json")
        with open(path, "w") as f:
            json.dump(self.manifest, f)
        dry = self.go(manifest=path, bed_dir=None)
        self.assertEqual(os.path.dirname(dry["bed"]["path"]), os.path.dirname(path))

    def test_a_manifest_with_no_path_needs_a_bed_dir(self):
        with self.assertRaisesRegex(Refused, "give bed_dir"):
            self.go(bed_dir=None)
        with self.assertRaisesRegex(Refused, "give bed_dir"):
            self.go(bed_dir="beds")
        silent = {**self.manifest, "music": None}
        self.assertIsNone(self.go(manifest=silent, bed_dir=None)["bed"])  # no music, no bed

    def test_a_bed_dir_inside_a_git_tree_is_refused(self):
        repo = os.path.join(os.path.realpath(self.tmp.name), "repo")
        os.makedirs(os.path.join(repo, ".git"))
        with self.assertRaisesRegex(Refused, "inside a git working tree"):
            self.go(bed_dir=os.path.join(repo, "beds"))
        path = os.path.join(repo, "m.json")  # the default folder is the manifest's
        with open(path, "w") as f:
            json.dump(self.manifest, f)
        with self.assertRaisesRegex(Refused, "inside a git working tree"):
            self.go(manifest=path, bed_dir=None)
        self.assertEqual(self.baked, [])

    def test_a_bed_that_cannot_be_made_writes_nothing_to_resolve(self):
        dry = self.go()
        self.bake_error = "the song holds 1.00 s of music from 8 s, the bed needs 2.25 s"
        with self.assertRaisesRegex(Refused, "bed was not made.*holds 1.00 s"):
            self.go(dry_run=False, expect_sha=dry["plan_sha"])
        self.assertEqual(rf.mutating_calls(), [])
        self.assertEqual(self.project.GetTimelineCount(), 1)

    def test_the_dry_run_refuses_a_song_that_runs_out_before_the_bed_does(self):
        self.song_seconds = 9.0  # 1 s of music from 8 s; the bed needs 2.25 s
        with self.assertRaisesRegex(Refused, "song.m4a holds 1.00 s of music from 8 s, the bed needs 2.25 s"):
            self.go()
        self.assertEqual((self.baked, rf.mutating_calls(), os.path.exists(self.bed_dir)),
                         ([], [], False))
        self.song_seconds = 10.25  # exactly enough
        self.assertEqual(self.go()["bed"]["result"], "would bake")

    def test_a_song_whose_length_ffprobe_cannot_give_is_left_to_bake(self):
        self.song_seconds = None
        self.assertEqual(self.go()["bed"]["result"], "would bake")

    def test_a_bed_already_there_is_not_asked_about_the_song_again(self):
        dry = self.go()
        os.makedirs(self.bed_dir)
        Path(dry["bed"]["path"]).write_bytes(b"x")
        self.song_seconds = 1.0
        self.assertEqual(self.go()["bed"]["result"], "would reuse")

    @needs_ffmpeg
    def test_a_real_bake_gives_a_file_as_long_as_the_planned_bed_and_a1_holds_it(self):
        song = os.path.join(os.path.dirname(self.song), "real.wav")
        subprocess.run([dc.FFMPEG, "-v", "error", "-y", "-nostdin", "-f", "lavfi", "-i",
                        "sine=frequency=440:sample_rate=48000:duration=12", song],
                       stdin=subprocess.DEVNULL, check=True)
        manifest = {**self.manifest, "music": {"path": song, "in": 8.0}}
        with mock.patch.object(a.musicbed, "bake", REAL_BAKE), \
                mock.patch.object(a.musicbed, "source_seconds", REAL_SOURCE_SECONDS):
            dry = self.go(manifest=manifest)
            self.assertEqual(dry["bed"]["result"], "would bake")
            r = self.go(manifest=manifest, dry_run=False, expect_sha=dry["plan_sha"])
        self.assertEqual((r["exit_status"], r["problems"], r["bed"]["result"]), (0, [], "baked"))
        info = dc.probe(r["bed"]["path"])
        self.assertAlmostEqual(dc.duration(info), 54 / 24, delta=0.01)  # picture + end black
        self.assertEqual(r["audio"]["frames"], 54)
        self.assertLess(dc.window_level(r["bed"]["path"], 0.1, 0.1)
                        + 12, dc.window_level(r["bed"]["path"], 1.5, 0.5))  # and it falls at the end

    def test_a_bed_already_there_is_reused_not_baked_again(self):
        dry = self.go()
        os.makedirs(self.bed_dir)
        Path(dry["bed"]["path"]).write_bytes(b"x")
        r = self.go(dry_run=False, expect_sha=dry["plan_sha"])
        self.assertEqual((self.baked, r["bed"]["result"], r["problems"]), ([], "reused", []))

    def test_a_bed_of_the_wrong_length_on_a1_is_a_problem(self):
        dry = self.go()
        real = rf._place

        def short(tl, info):
            placed = real(tl, info)
            for it in placed:
                if info.get("mediaType") == 2:
                    it.end -= 6
            return placed

        with mock.patch.object(rf, "_place", short):
            r = self.go(dry_run=False, expect_sha=dry["plan_sha"])
        self.assertIn("the music sits at frame 0 for 48 frames, planned 0 for 54", r["problems"])
        self.assertEqual(r["exit_status"], 1)

    def test_the_mcp_tool_takes_a_bed_dir_and_says_the_fade_is_baked(self):
        from rpresolve.mcp import schema, server
        from rpresolve.mcp.registry import ToolContext
        tool = server.build_registry().get("assemble_timeline")
        path = os.path.join(os.path.realpath(self.tmp.name), "m.json")
        with open(path, "w") as f:
            json.dump(self.manifest, f)
        args = {"project": self.project.GetName(), "name": "Shoot m v1 [auto]", "manifest": path,
                "bed_dir": self.bed_dir}
        self.assertEqual(schema.validate(tool.input_schema, args), [])
        with mock.patch.dict(os.environ, {"RPRESOLVE_LOCK": os.path.join(self.tmp.name, "lock")}):
            dry = tool.handler(schema.with_defaults(tool.input_schema, args),
                               ToolContext(session=rf.Session(self.resolve)))
        self.assertIn("music bed on A1 (0.5625 s fade baked in)", dry["summary"])
        self.assertEqual(os.path.dirname(dry["bed"]["path"]), self.bed_dir)


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
        self.assertNotIn("Input Color Space", clips[self.baked[0][4]].props)  # the bed is not picture
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

    def test_a_file_in_another_bin_gets_its_own_tagged_import(self):
        # The build reuses only a clip in 'methods [auto]'; any other file is imported again,
        # and that new clip is the one on the timeline. The dry run and the real run read it.
        pre = self.pool_clip(self.one, cs=detect.CS_VLOG, bin_name="Source")
        dry = self.go()
        row = self.rows(dry)["one.mov"]
        self.assertEqual((row["action"], row["in_pool"], row["current"], row["flag"]),
                         ("tag", True, None, ""))
        self.assertIn("also in another bin", row["note"])
        self.assertTrue(any("one.mov: also in another bin" in x for x in dry["colour"]["notes"]))
        r = self.go(dry_run=False, expect_sha=dry["plan_sha"])
        self.assertEqual((r["exit_status"], r["problems"], r["left_behind"]), (0, [], None))
        self.assertEqual((pre.props["Input Color Space"], pre.props["Data Level"]),
                         (detect.CS_VLOG, "Auto"))  # the existing clip is not touched
        self.assertEqual(len(self.writes()), 4)
        new = next(c for c in self.project.pool.root.subs[-1].clips if c.props["File Path"] == self.one)
        self.assertIsNot(new, pre)
        self.assertEqual((new.props["Input Color Space"], new.props["Data Level"]),
                         (detect.CS_VLOG, "Full"))
        self.assertEqual((self.rows(r)["one.mov"]["result"], self.rows(r)["one.mov"]["current"]),
                         ("tagged", detect.CS_VLOG))

    def hand_back(self, reads):
        """A real run where ImportMedia answers the pooled one.mov itself, not a copy; returns
        the result and that clip. Whether Resolve duplicates a pooled file is not measured."""
        pre = self.pool_clip(self.one, cs=reads, bin_name="Source")
        real = rf.Pool.ImportMedia

        def hand_back(pool, paths):
            return real(pool, [x for x in paths if x != self.one]) + [pre]

        with mock.patch.object(rf.Pool, "ImportMedia", hand_back):
            r = self.go(dry_run=False)
        self.assertEqual((pre.props["Input Color Space"], pre.props["Data Level"]), (reads, "Auto"))
        self.assertEqual(len(self.writes()), 2)  # two.mov alone: the existing clip is never written
        row = self.rows(r)["one.mov"]
        self.assertEqual((row["action"], row["current"]), ("tag", reads))
        self.assertIn("already in the pool", row["result"])
        return r

    def test_an_import_that_hands_back_a_pooled_clip_reports_it_when_it_reads_rec709(self):
        r = self.hand_back(UNTAGGED)
        self.assertEqual(len([x for x in r["problems"] if "one.mov" in x]), 1)
        self.assertEqual((r["exit_status"], r["left_behind"]), (1, "Shoot m v1 [auto]"))

    def test_an_import_that_hands_back_a_pooled_clip_is_fine_when_it_reads_its_space(self):
        r = self.hand_back(detect.CS_VLOG)
        self.assertEqual((r["exit_status"], r["problems"]), (0, []))

    def test_clips_that_reached_the_bin_since_the_plan_are_not_written(self):
        # colour_plan said 'tag' for both; by the build both are in the bin, so nothing is
        # imported (no music either, so no pool snapshot is taken) and only 'was it imported?'
        # stands in the way
        self.manifest["music"] = None
        real_plan = a.colour_plan
        pre = [rf.Clip(os.path.basename(x), "pre-" + os.path.basename(x),
                       {"File Path": x, "FPS": "60.0", "Input Color Space": UNTAGGED,
                        "Data Level": "Auto"}) for x in (self.one, self.two)]

        def stale(project, root, p):
            rows = real_plan(project, root, p)
            root.subs.append(rf.Folder("methods [auto]", pre))
            return rows

        with mock.patch.object(a, "colour_plan", stale):
            r = self.go(dry_run=False)
        self.assertEqual([c for c in rf.CALLS if c[1] == "ImportMedia"], [])
        self.assertEqual(self.writes(), [])
        self.assertEqual([c.props["Input Color Space"] for c in pre], [UNTAGGED, UNTAGGED])
        self.assertEqual(len([x for x in r["problems"] if x.startswith("colour:")]), 2)
        for row in self.rows(r).values():
            self.assertIn("already in the pool", row["result"])

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

    def mcp(self, **kw):
        from rpresolve.mcp import schema, server
        from rpresolve.mcp.registry import ToolContext
        tool = server.build_registry().get("assemble_timeline")
        path = os.path.join(os.path.realpath(self.tmp.name), "m.json")
        with open(path, "w") as f:
            json.dump(self.manifest, f)
        env = {"RPRESOLVE_MCP_JOURNAL": os.path.join(self.tmp.name, "writes.jsonl"),
               "RPRESOLVE_LOCK": os.path.join(self.tmp.name, "lock")}
        args = {"project": self.project.GetName(), "name": "Shoot m v1 [auto]", "manifest": path, **kw}
        with mock.patch.dict(os.environ, env):
            return tool.handler(schema.with_defaults(tool.input_schema, args),
                                ToolContext(session=rf.Session(self.resolve)))

    def test_the_mcp_summary_names_the_colour_work(self):
        self.pool_clip(self.two)
        dry = self.mcp()
        self.assertIn("colour: 1 to tag; in the pool before this run, log by detect and "
                      "reading Rec.709: two.mov", dry["summary"])
        real = self.mcp(dry_run=False, plan_sha=dry["plan_sha"])
        self.assertIn("colour: two.mov: detect says", real["summary"])  # a problem is named

    def test_the_mcp_summary_counts_the_tags_a_clean_real_run_wrote(self):
        dry = self.mcp()
        self.assertIn("colour: 2 to tag", dry["summary"])
        real = self.mcp(dry_run=False, plan_sha=dry["plan_sha"])
        self.assertIn("built and read back", real["summary"])
        self.assertTrue(real["summary"].endswith("; colour: 2 of 2 tagged"))


if __name__ == "__main__":
    unittest.main()
