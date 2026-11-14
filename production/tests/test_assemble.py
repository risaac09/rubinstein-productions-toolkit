"""
Tests for the assemble command (rpresolve.cmd_assemble): the offline plan (frames at the
manifest's rate, a continued take, a dissolve as a Cross Dissolve, the end black) and
apply_assemble against the shared Resolve fakes: the [auto]-only guard, the frame-rate
refusal, dry run and plan_sha, clips cut at their own rate onto a 24 fps timeline, the
static scale, the music bed (baked by ffmpeg with its fade, the baked file on A1 and the
original never placed), and the read-back, and the colour handling
(T30): detect's colour space per source in the dry run, the tag written and read back on a
new import, a clip already in the pool never written, an unmanaged project left alone,
and the windows: a scale-window or crop-window span puts the montage on V2 with the shot
before it playing on as a plate on V1, refused when that source runs out inside the plate.

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
REAL_MEASURE = a.measure_bed
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
    def test_a_recorded_squeeze_is_planned_for_the_pixel_aspect_ratio_and_a_bad_record_is_refused(self):
        record = Path(os.path.dirname(self.one), "squeeze.json")
        record.write_text(json.dumps({"default": 1, "clips": {"two.mov": 1.5}}))
        p = a.plan(self.manifest)
        self.assertEqual(p["not_built"], [])  # the line is gone: this tool now handles it
        self.assertEqual(p["squeeze"], [{"path": self.two, "factor": 1.5, "where": "record",
                                         "set": True}])
        record.write_text("{not json")
        with self.assertRaisesRegex(a.Refused, "cannot read the squeeze record"):
            a.plan(self.manifest)

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
            self.assertEqual(a.run(mock.Mock(manifest=path, exclude=None)), 0)
        text = "\n".join(str(c.args[0]) for c in out.call_args_list)
        self.assertIn("3 item(s), 42 frames at 24 fps, then 12 of black", text)
        self.assertIn("same take, continued", text)
        self.assertIn("no exclude list given", text)


class TestExclude(Base):
    def listed(self, doc):
        p = os.path.join(self.tmp.name, "exclude.json")
        Path(p).write_text(json.dumps(doc))
        return p

    def test_a_stem_a_basename_and_a_path_suffix_refuse_in_both_runs(self):
        parent = os.path.basename(os.path.dirname(self.one))
        for take in ("ONE", "one.mov", f"{parent}/one.mov"):
            ex = self.listed({"takes": [take]})
            for dry in (True, False):
                with self.assertRaisesRegex(Refused, r"span 1 one\.mov"):
                    self.go(exclude=ex, dry_run=dry)
        self.assertEqual([c for c in rf.CALLS if c[1].startswith(rf.MUTATING_PREFIXES)], [])

    def test_a_clean_manifest_passes_and_carries_the_list(self):
        ex = self.listed({"takes": ["elsewhere"], "shots": ["s1"]})
        r = self.go(exclude=ex)
        self.assertEqual((r["exclude"]["path"], r["exclude"]["takes"], r["exclude"]["matches"]),
                         (ex, ["elsewhere"], []))
        self.assertIn("shot ids belong to the planner's index", r["exclude"]["not_checked"])
        self.assertIsNone(self.go()["exclude"])

    def test_the_music_file_is_checked_too(self):
        for take in ("SONG", "song.m4a", f"{os.path.basename(os.path.dirname(self.song))}/song.m4a"):
            for dry in (True, False):
                with self.assertRaisesRegex(Refused, r"music song\.m4a"):
                    self.go(exclude=self.listed({"takes": [take]}), dry_run=dry)
        self.assertEqual([c for c in rf.CALLS if c[1].startswith(rf.MUTATING_PREFIXES)], [])
        self.assertEqual(self.baked, [])
        self.go(exclude=self.listed({"takes": ["sing"]}))  # a part of a name is not a match
        a.plan({**self.manifest, "music": None}, self.listed({"takes": ["song"]}))  # no music, no hit

    def test_a_build_without_a_list_says_nothing_was_checked(self):
        r = self.go()
        self.assertIsNone(r["exclude"])
        self.assertIn("no exclude list given", r["exclude_note"])
        self.assertIn("no exclude list given", r["plan"]["exclude_note"])
        self.assertIsNone(self.go(exclude=self.listed({"takes": ["x"]}))["exclude_note"])

    def test_a_file_without_takes_is_refused(self):
        for doc in ({"shots": ["s1"]}, {"take": ["one"]}):
            with self.assertRaisesRegex(Refused, "`takes`"):
                self.go(exclude=self.listed(doc))

    def test_the_plan_sha_follows_the_exclude_file(self):
        a_sha = self.go(exclude=self.listed({"takes": ["x"]}))["plan_sha"]
        b_sha = self.go(exclude=self.listed({"takes": ["y"]}))["plan_sha"]
        self.assertEqual(len({a_sha, b_sha, self.go()["plan_sha"]}), 3)
        with self.assertRaisesRegex(Refused, "plan changed"):
            self.go(exclude=self.listed({"takes": ["x"]}), dry_run=False, expect_sha=b_sha)

    def test_the_offline_command_refuses(self):
        path = os.path.join(self.tmp.name, "m.json")
        Path(path).write_text(json.dumps(self.manifest))
        ex = self.listed({"takes": ["two"]})
        with mock.patch("builtins.print") as out:
            self.assertEqual(a.run(mock.Mock(manifest=path, exclude=ex)), 2)
        self.assertIn("span 3 two.mov", str(out.call_args_list[0].args[0]))


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
            self.assertIn("no exclude list given", dry["summary"])
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


class TestWindows(Base):
    """A scale-window or crop-window span gets a plate: the previous span's clip, counted on from
    its out point, on V1 under the montage on V2 (windowplate.py, fusion-macros.md)."""

    def setUp(self):
        super().setUp()
        self.three = os.path.join(os.path.dirname(self.one), "three.mov")
        Path(self.three).write_bytes(b"x")
        tag = {"Input Color Space": UNTAGGED, "Data Level": "Auto"}
        for new in (mock.patch.dict(rf.IMPORT_PROPS, {self.three: {"FPS": "24.0", **tag}}),
                    mock.patch.dict(self.detected, {self.three: drow(self.three)})):
            new.start()
            self.addCleanup(new.stop)
        # 24 fps, 30 fps, 60 fps and 30 fps spans: the plate counts source frames at the
        # outgoing clip's own rate, 37 on 24, then 26 on 60 (65 source frames)
        self.wm = {**self.manifest,
                   "sources": [{"id": "a", "path": self.one}, {"id": "b", "path": self.two},
                               {"id": "c", "path": self.three}],
                   "clips": [{"name": "m", "spans": [
                       {"source": "c", "in": 2.0, "out": 3.0, "scale": 1.1},
                       {"source": "a", "in": 10.0, "out": 12.0},
                       {"source": "b", "in": 5.0, "out": 6.0},
                       {"source": "a", "in": 20.0, "out": 21.5}]}],
                   "effects": [{"span_index": 1, "effect": "scale-window"},
                               {"span_index": 3, "effect": "crop-window"}]}
        self.length = {self.one: 100.0, self.two: 100.0, self.three: 100.0}  # what ffprobe finds
        new = mock.patch.object(a, "_video_seconds", lambda path: self.length[path])
        new.start()
        self.addCleanup(new.stop)

    def built(self, **kw):
        dry = self.go(manifest=self.wm, **kw)
        return dry, self.go(manifest=self.wm, dry_run=False, expect_sha=dry["plan_sha"], **kw)

    def tl(self):
        return next(t for t in self.project.timelines if t.name == "Shoot m v1 [auto]")

    def test_the_plan_marks_each_window_span_with_the_frames_it_needs(self):
        p = a.plan(self.wm)
        self.assertEqual([(r.get("window"), r.get("plate")) for r in p["spans"]],
                         [(None, None), ("scale-window", 37), (None, None), ("crop-window", 26)])
        self.assertEqual((p["track"], p["not_built"]), (2, []))
        self.assertEqual(a.plan(self.manifest)["track"], 1)  # no window, nothing changes
        # a span shorter than its window shows only the frames it has
        short = {**self.wm, "clips": [{"name": "m", "spans": [
            {"source": "c", "in": 2.0, "out": 3.0}, {"source": "a", "in": 10.0, "out": 10.25}]}],
            "effects": [{"span_index": 1, "effect": "crop-window"}]}
        self.assertEqual(a.plan(short)["spans"][1]["plate"], 6)

    def test_a_window_on_the_first_span_has_no_shot_to_open_over_and_says_so(self):
        first = {**self.wm, "effects": [{"span_index": 0, "effect": "scale-window"}]}
        p = a.plan(first)
        self.assertEqual((p["track"], [r.get("plate") for r in p["spans"]]), (1, [None] * 4))
        self.assertEqual(p["not_built"], ["the scale-window on item 1: no shot before it to "
                                          "open over, so it opens over black"])

    def test_the_dry_run_lists_each_plate_with_the_handle_the_source_has(self):
        dry = self.go(manifest=self.wm)
        self.assertEqual(dry["track"], 2)
        self.assertEqual([(r["into"], r["window"], r["frames"], r["record"], r["source"])
                          for r in dry["plates"]],
                         [(2, "scale-window", 37, 24, "three.mov"),
                          (4, "crop-window", 26, 96, "two.mov")])
        self.assertEqual(dry["plates"][0]["handle_s"], 97.0)  # 100 s file, out point at 3 s
        self.assertEqual(rf.mutating_calls(), [])
        self.length[self.three] = 50.0  # a different file is a different plan
        self.assertNotEqual(self.go(manifest=self.wm)["plan_sha"], dry["plan_sha"])

    def test_a_source_that_ends_inside_its_plate_is_refused_before_anything_is_written(self):
        for path, out_s, want in ((self.three, 3.0, "item 2: the scale-window opens over item 1 "
                                   "playing on for 37 frames, and three.mov has 0.50 s"),
                                  (self.two, 6.0, "item 4: the crop-window opens over item 3 "
                                   "playing on for 26 frames, and two.mov has 0.50 s")):
            self.length[path] = out_s + 0.5
            with self.assertRaisesRegex(Refused, want):
                self.go(manifest=self.wm, dry_run=False)
            self.length[path] = 100.0
        self.assertEqual(rf.mutating_calls(), [])
        self.assertEqual(self.project.GetTimelineCount(), 1)

    def test_one_frame_short_is_refused_and_enough_is_not(self):
        self.length[self.three] = 3.0 + 37 / 24 - 1 / 24  # 36 frames after the out point
        with self.assertRaisesRegex(Refused, "three.mov has 1.50 s"):
            self.go(manifest=self.wm)
        self.length[self.three] = 3.0 + 37 / 24  # exactly 37
        self.assertEqual(self.go(manifest=self.wm)["plates"][0]["handle_s"], 1.542)

    def test_a_source_ffprobe_cannot_read_is_left_to_the_read_back(self):
        self.length[self.three] = None  # a BRAW file, say
        self.assertIsNone(self.go(manifest=self.wm)["plates"][0]["handle_s"])

    def test_the_montage_is_on_v2_and_the_plates_on_v1(self):
        dry, r = self.built()
        self.assertEqual((r["exit_status"], r["problems"], r["left_behind"]), (0, [], None))
        tl = self.tl()
        self.assertEqual(tl.GetTrackCount("video"), 2)
        v2, v1 = tl.GetItemListInTrack("video", 2), tl.GetItemListInTrack("video", 1)
        # every span in its order, at its frame: the numbering a V1 build always had
        self.assertEqual([i.GetDuration() for i in v2], [24, 48, 24, 36])
        self.assertEqual([i.GetStart() - tl.GetStartFrame() for i in v2], [0, 24, 72, 96])
        self.assertEqual([i.source for i in v2], [(48, 72), (300, 360), (300, 360), (600, 645)])
        self.assertEqual([x["index"] for x in r["items"]], [1, 2, 3, 4])
        # a plate: the previous clip from where its span stopped, as long as the window needs
        self.assertEqual([(i.GetStart() - tl.GetStartFrame(), i.GetDuration()) for i in v1],
                         [(24, 37), (96, 26)])
        self.assertEqual([i.source for i in v1], [(72, 109), (360, 425)])
        self.assertEqual([i.clip.props["File Path"] for i in v1], [self.three, self.two])
        self.assertEqual(v1[0].props["ZoomX"], 1.1)  # item 1's static scale carries onto its plate
        self.assertNotIn("ZoomX", v1[1].props)
        self.assertEqual([(x["placed"]["start"], x["placed"]["frames"]) for x in r["plates"]],
                         [(24, 37), (96, 26)])
        calls = [c[2][0] for c in rf.CALLS if c[1] == "AppendToTimeline"]
        self.assertEqual([i.get("trackIndex") for i in calls[0] if i["mediaType"] == 1], [2] * 4)
        self.assertEqual([i["recordFrame"] - tl.GetStartFrame() for i in calls[0]], [0, 24, 72, 96])
        self.assertEqual([i["trackIndex"] for i in calls[1]], [1, 1])
        audio = tl.GetItemListInTrack("audio", 1)
        self.assertEqual(len(audio), 1)  # the music alone, whatever the picture tracks hold
        self.assertEqual(audio[0].GetDuration(), 132 + 12)

    def test_a_manifest_without_a_window_is_built_on_v1_alone_as_before(self):
        dry = self.go()
        r = self.go(dry_run=False, expect_sha=dry["plan_sha"])
        tl = self.tl()
        self.assertEqual((r["track"], r["plates"], tl.GetTrackCount("video")), (1, [], 1))
        self.assertEqual([c for c in rf.CALLS if c[1] == "AddTrack"], [])
        self.assertTrue(all("recordFrame" not in i for c in rf.CALLS if c[1] == "AppendToTimeline"
                            for i in c[2][0] if i["mediaType"] == 1))

    def test_a_track_resolve_will_not_add_stops_the_build_and_is_named(self):
        dry = self.go(manifest=self.wm)
        with mock.patch.object(rf.Timeline, "AddTrack", lambda self, kind, *sub: True):  # says yes, adds nothing
            r = self.go(manifest=self.wm, dry_run=False, expect_sha=dry["plan_sha"])
        self.assertTrue(any("could not add V2" in x for x in r["problems"]))
        self.assertEqual((r["exit_status"], r["left_behind"]), (1, "Shoot m v1 [auto]"))
        self.assertEqual(self.tl().GetItemListInTrack("video", 1), [])  # no plates on a track with no montage over it

    def test_a_plate_that_comes_up_short_is_a_problem_and_the_timeline_is_left(self):
        dry = self.go(manifest=self.wm)
        real = rf._place

        def clamp(tl, info):  # the source ends early: Resolve clamps the clip
            placed = real(tl, info)
            for it in placed:
                if info["trackIndex"] == 1:
                    it.end -= 5
            return placed

        with mock.patch.object(rf, "_place", clamp):
            r = self.go(manifest=self.wm, dry_run=False, expect_sha=dry["plan_sha"])
        self.assertEqual(r["exit_status"], 1)
        self.assertIn("the plate under item 2 sits at frame 24 for 32 frames, planned 24 for 37; "
                      "the outgoing source may be too short", r["problems"])

    def test_a_plate_that_starts_at_the_wrong_source_frame_is_a_problem(self):
        # a held frame, or the span's own footage, would still pass on position and length
        dry = self.go(manifest=self.wm)
        real = rf._place

        def shift(tl, info):  # Resolve starts the plate somewhere else in the file
            placed = real(tl, info)
            for it in placed:
                if info["trackIndex"] == 1:
                    it.source = (it.source[0] - 24, it.source[1] - 24)
            return placed

        with mock.patch.object(rf, "_place", shift):
            r = self.go(manifest=self.wm, dry_run=False, expect_sha=dry["plan_sha"])
        self.assertEqual(r["exit_status"], 1)
        self.assertIn("the plate under item 2 starts at source frame 48, planned 72 "
                      "(the outgoing shot's out point)", r["problems"])
        self.assertIn("the plate under item 4 starts at source frame 336, planned 360 "
                      "(the outgoing shot's out point)", r["problems"])

    def test_a_plate_cut_from_another_clip_is_a_problem(self):
        dry = self.go(manifest=self.wm)
        real = rf._place

        def swap(tl, info):  # the plate under item 2 comes from the window span's own clip
            placed = real(tl, info)
            for it in placed:
                if info["trackIndex"] == 1 and it.clip.props["File Path"] == self.three:
                    it.clip = rf.Clip("one.mov", "clip-other", {"File Path": self.one})
            return placed

        with mock.patch.object(rf, "_place", swap):
            r = self.go(manifest=self.wm, dry_run=False, expect_sha=dry["plan_sha"])
        self.assertEqual(r["exit_status"], 1)
        self.assertTrue(any("the plate under item 2 is " in x and "not the outgoing shot" in x
                            for x in r["problems"]), r["problems"])

    def test_a_montage_item_that_lands_off_its_frame_on_v2_is_a_problem(self):
        dry = self.go(manifest=self.wm)
        real = rf._place

        def late(tl, info):  # the track above V1 does not honour recordFrame
            placed = real(tl, info)
            for it in placed:
                if info["trackIndex"] == 2:
                    it.start += 3
                    it.end += 3
            return placed

        with mock.patch.object(rf, "_place", late):
            r = self.go(manifest=self.wm, dry_run=False, expect_sha=dry["plan_sha"])
        self.assertEqual(r["exit_status"], 1)
        self.assertIn("item 2 sits at frame 27, planned 24", r["problems"])

    def test_a_dissolve_into_a_window_span_is_added_on_the_montage_not_the_plate(self):
        spans = [dict(x) for x in self.wm["clips"][0]["spans"]]
        spans[1]["transition"] = {"type": "dissolve", "frames": 2}
        both = {**self.wm, "clips": [{"name": "m", "spans": spans}]}
        dry = self.go(manifest=both)
        r = self.go(manifest=both, dry_run=False, expect_sha=dry["plan_sha"])
        self.assertEqual((r["exit_status"], r["problems"]), (0, []))
        self.assertEqual(r["transitions"], [{"into": 2, "frames": 2, "planned": 2}])
        v2, v1 = (self.tl().GetItemListInTrack("video", t) for t in (2, 1))
        # centred on the cut at the end of item 1; the plate under item 2 has none
        self.assertEqual(len(v2[0].__dict__.get("transitions", [])), 1)
        self.assertTrue(all(not i.__dict__.get("transitions") for i in v1 + v2[1:]))
        self.assertEqual([(i.GetStart() - self.tl().GetStartFrame(), i.GetDuration()) for i in v1],
                         [(24, 37), (96, 26)])  # whether Resolve lets a plate sit under a dissolve is a live check

    def test_a_window_straight_after_a_window_takes_the_plate_from_that_spans_own_clip(self):
        adjacent = {**self.wm, "effects": [{"span_index": 1, "effect": "scale-window"},
                                           {"span_index": 2, "effect": "crop-window"}]}
        dry = self.go(manifest=adjacent)
        r = self.go(manifest=adjacent, dry_run=False, expect_sha=dry["plan_sha"])
        self.assertEqual((r["exit_status"], r["problems"]), (0, []))
        v1 = self.tl().GetItemListInTrack("video", 1)
        # item 3 is 24 frames long, so its 26 frame plate is cut to 24; item 2's own 30 fps
        # clip plays on from where item 2 stopped (360), not the plate under item 2
        self.assertEqual([(i.GetStart() - self.tl().GetStartFrame(), i.GetDuration()) for i in v1],
                         [(24, 37), (72, 24)])
        self.assertEqual([i.source for i in v1], [(72, 109), (360, 390)])
        self.assertEqual([i.clip.props["File Path"] for i in v1], [self.three, self.one])

    def test_the_other_tools_number_the_montage_on_its_track_not_the_plates(self):
        from rpresolve import cmd_effect, cmd_vertical
        dry, r = self.built()
        v2, v1 = (self.tl().GetItemListInTrack("video", t) for t in (2, 1))
        fx = cmd_effect.apply_effect(self.resolve, self.project.GetName(), "Shoot m v1 [auto]",
                                     "scale-window", track=2, items=[2, 4], dry_run=True)
        self.assertEqual([(t["index"], t["item_id"]) for t in fx["targets"]],
                         [(2, v2[1].GetUniqueId()), (4, v2[3].GetUniqueId())])
        self.assertEqual(fx["timeline"]["track"], 2)
        on_v1 = cmd_effect.apply_effect(self.resolve, self.project.GetName(), "Shoot m v1 [auto]",
                                        "scale-window", track=1, dry_run=True)
        self.assertEqual([t["item_id"] for t in on_v1["targets"]], [i.GetUniqueId() for i in v1])
        # vertical_apply counts the track it is given against crops.json: V2 holds the four
        # spans, V1 only the two plates
        for it in v2:
            it.props.update(ZoomX=1.0, ZoomY=1.0)
        crops = {"aspect": "9x16", "reviewed": True, "items": [
            {"index": n, "name": it.GetName(), "path": it.clip.props["File Path"], "cx": 0.5,
             "zoom": 1.0} for n, it in enumerate(v2, 1)]}
        plan = cmd_vertical.apply_vertical(self.resolve, self.project.GetName(),
                                           "Shoot m v1 9x16 [auto]", crops,
                                           source_timeline="Shoot m v1 [auto]", track=2)
        self.assertEqual([t["index"] for t in plan["targets"]], [1, 2, 3, 4])
        with self.assertRaisesRegex(Refused, r"V1 has 2 item\(s\), crops.json has 4"):
            cmd_vertical.apply_vertical(self.resolve, self.project.GetName(),
                                        "Shoot m v1 9x16 [auto]", crops,
                                        source_timeline="Shoot m v1 [auto]", track=1)
        # apply_grade numbers the same way: V2 holds the four spans, V1 the two plates and a
        # warning that a montage is above it
        from rpresolve import workflows
        lut = os.path.join(os.path.dirname(self.one), "Look.cube")
        Path(lut).write_bytes(b"")
        for track, want in ((2, [1, 2, 3, 4]), (1, [1, 2])):
            g = workflows.apply_grade(self.resolve, self.project.GetName(), "Shoot m v1 [auto]",
                                      lut={"path": lut}, track=track, dry_run=True)
            self.assertEqual([t["index"] for t in g["targets"]], want)
        self.assertEqual(g["timeline"]["other_tracks"], {2: 4})

    def test_the_command_and_the_mcp_summary_say_where_the_montage_goes(self):
        from rpresolve.mcp import schema, server
        from rpresolve.mcp.registry import ToolContext
        path = os.path.join(os.path.realpath(self.tmp.name), "w.json")
        with open(path, "w") as f:
            json.dump(self.wm, f)
        with mock.patch("builtins.print") as out:
            self.assertEqual(a.run(mock.Mock(manifest=path, exclude=None)), 0)
        text = "\n".join(str(c.args[0]) for c in out.call_args_list)
        self.assertIn("scale-window, 37 f of #1 playing on", text)
        self.assertIn("the montage goes on V2; V1 holds a plate under each window span", text)
        tool = server.build_registry().get("assemble_timeline")
        args = {"project": self.project.GetName(), "name": "Shoot m v1 [auto]", "manifest": path,
                "bed_dir": self.bed_dir}
        with mock.patch.dict(os.environ, {"RPRESOLVE_LOCK": os.path.join(self.tmp.name, "lock")}):
            dry = tool.handler(schema.with_defaults(tool.input_schema, args),
                               ToolContext(session=rf.Session(self.resolve)))
        self.assertIn("montage on V2 over 2 plate(s) on V1", dry["summary"])
        self.assertIn("track 2", dry["summary"])


@needs_ffmpeg
class TestRealHandleProbe(unittest.TestCase):
    """_video_seconds and plate_handles against a real file: a 3 s picture beside 5 s of sound."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = os.path.realpath(self.tmp.name)
        self.src = os.path.join(self.dir, "take.mp4")
        subprocess.run([dc.FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                        "color=c=black:s=64x64:r=24:d=3", "-f", "lavfi", "-i", "sine=d=5",
                        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", self.src],
                       check=True)

    def test_the_length_is_the_picture_not_the_longer_sound(self):
        self.assertAlmostEqual(a._video_seconds(self.src), 3.0, delta=0.1)

    def test_a_missing_or_unreadable_file_has_no_length(self):
        junk = os.path.join(self.dir, "junk.mov")
        Path(junk).write_bytes(b"x")
        self.assertIsNone(a._video_seconds(os.path.join(self.dir, "nope.mov")))
        self.assertIsNone(a._video_seconds(junk))

    def test_the_plate_needs_its_frames_after_the_out_point(self):
        def manifest(out):
            return {"version": 2, "speech": False, "fps": 24,
                    "sources": [{"id": "s", "path": self.src}],
                    "clips": [{"name": "m", "spans": [{"source": "s", "in": 0.0, "out": out},
                                                      {"source": "s", "in": 0.0, "out": 1.0, "reuse": True}]}],
                    "effects": [{"span_index": 1, "effect": "scale-window"}], "music": None,
                    "method": {"id": "m", "aspect": "16:9", "look": None, "end": None,
                               "ui_steps": []}}
        rows = a.plate_handles(a.plan(manifest(1.0)))  # 2 s after the out point, 37 frames wanted
        self.assertAlmostEqual(rows[0]["handle_s"], 2.0, delta=0.1)
        with self.assertRaisesRegex(Refused, r"take.mp4 has 0\.[45]\d s"):
            a.plate_handles(a.plan(manifest(2.5)))  # 0.5 s left, 1.54 s wanted


if __name__ == "__main__":
    unittest.main()


class TestLedgerAtBuild(Base):
    """The build applies only what the draft drew: the draft farm's ledger flags a manifest
    effect marked not_drawn in the dry run and the real run refuses it unless overridden."""

    def setUp(self):
        super().setUp()
        from rpresolve import drafts
        self.man = {**self.manifest,
                    "effects": [{"span_index": 1, "effect": "cross-dissolve-short"},
                                {"span_index": 0, "effect": "zoom-push"}]}
        self.path = os.path.join(os.path.realpath(self.tmp.name), "drafts.json")
        self.write([{"plan": "a", "effects": drafts.ledger(self.man)}])

    def write(self, drafts):
        with open(self.path, "w") as f:
            json.dump({"drafts": drafts}, f)

    def go(self, **kw):
        return super().go(manifest=self.man, ledger=self.path, **kw)

    def test_no_ledger_says_nothing_was_checked(self):
        dry = super().go(manifest=self.man)
        self.assertEqual(dry["ledger"]["checked"], False)
        self.assertIn("no ledger given", dry["ledger"]["note"])

    def test_the_dry_run_flags_what_the_draft_did_not_draw(self):
        dry = self.go()
        self.assertEqual(dry["ledger"]["flagged"],
                         [{"effect": "cross-dissolve-short", "spans": [1], "overridden": False}])
        self.assertEqual([c for c in rf.CALLS if c[1].startswith(rf.MUTATING_PREFIXES)], [])

    def test_the_real_run_refuses_until_the_effect_is_named_in_override(self):
        dry = self.go()
        with self.assertRaisesRegex(Refused, "did not draw cross-dissolve-short"):
            self.go(dry_run=False, expect_sha=dry["plan_sha"])
        with self.assertRaisesRegex(Refused, "did not draw cross-dissolve-short"):
            self.go(dry_run=False, override=["zoom-push"])
        self.assertEqual([c for c in rf.CALLS if c[1].startswith(rf.MUTATING_PREFIXES)], [])
        dry = self.go(override=["cross-dissolve-short"])
        self.assertNotEqual(dry["plan_sha"], self.go()["plan_sha"])  # the override is in the plan
        r = self.go(dry_run=False, expect_sha=dry["plan_sha"], override=["cross-dissolve-short"])
        self.assertEqual((r["exit_status"], r["problems"]), (0, []))
        self.assertEqual(r["ledger"]["flagged"][0]["overridden"], True)

    def test_a_ledger_that_is_not_this_manifests_is_refused(self):
        self.write([{"plan": "a", "effects": {"drawn": [], "approximated": [], "not_drawn": []}}])
        with self.assertRaisesRegex(Refused, "not this manifest's"):
            self.go()

    def test_a_draft_transition_is_not_flagged_and_the_plan_says_the_build_does_not_make_it(self):
        from rpresolve import drafts
        self.man = {**self.manifest, "effects": [{"span_index": 1, "effect": "luma-fade",
                                                  "params": {"frames": 8}}]}
        led = drafts.ledger(self.man)
        self.assertIn("luma-fade", [e["effect"] for e in led["approximated"]])
        self.write([{"plan": "a", "effects": led}])
        dry = self.go()
        self.assertEqual(dry["ledger"]["flagged"], [])
        self.assertIn("the luma-fade into item 2: not built by this tool (the draft farm draws it)",
                      dry["plan"]["not_built"])
        self.assertEqual(self.go(dry_run=False, expect_sha=dry["plan_sha"])["exit_status"], 0)

    def test_a_clean_ledger_passes_a_real_run(self):
        self.man = self.manifest
        from rpresolve import drafts
        self.write([{"plan": "a", "effects": drafts.ledger(self.man)}])
        dry = self.go()
        self.assertEqual(dry["ledger"]["flagged"], [])
        self.assertEqual(self.go(dry_run=False, expect_sha=dry["plan_sha"])["exit_status"], 0)


class TestTimelineColourAndCameraSound(Base):
    """The new timeline uses custom settings, so project colour spaces are copied onto it;
    camera_sound places each shot's own audio on A1 and moves the music to A2."""

    def setUp(self):
        super().setUp()
        self.project.settings.update(colorSpaceTimeline="DaVinci WG/Intermediate",
                                     colorSpaceOutput="Rec.709 Gamma 2.4")

    def built(self, **kw):
        dry = self.go(**kw)
        r = self.go(dry_run=False, expect_sha=dry["plan_sha"], **kw)
        tl = next(t for t in self.project.timelines if t.name == "Shoot m v1 [auto]")
        return dry, r, tl

    def test_the_projects_colour_spaces_land_on_the_timeline_and_are_read_back(self):
        dry, r, tl = self.built()
        self.assertEqual(dry["timeline_colour"], {"colorSpaceTimeline": "DaVinci WG/Intermediate",
                                                  "colorSpaceOutput": "Rec.709 Gamma 2.4"})
        self.assertEqual((r["exit_status"], r["problems"]), (0, []))
        self.assertEqual(tl.settings["colorSpaceTimeline"], "DaVinci WG/Intermediate")
        self.assertEqual(tl.settings["colorSpaceOutput"], "Rec.709 Gamma 2.4")
        self.assertEqual((tl.settings["timelineResolutionWidth"],
                          tl.settings["timelineResolutionHeight"]), ("3840", "2160"))

    def test_the_colour_spaces_are_part_of_the_plan_hash(self):
        before = self.go()["plan_sha"]
        self.project.settings["colorSpaceOutput"] = "Rec.709 (Scene)"
        self.assertNotEqual(before, self.go()["plan_sha"])

    def test_a_colour_space_that_does_not_land_is_a_problem(self):
        orig = rf.Timeline.SetSetting

        def refuse(tl, key, value):
            return False if key == "colorSpaceOutput" else orig(tl, key, value)
        with mock.patch.object(rf.Timeline, "SetSetting", refuse):
            _, r, _ = self.built()
        self.assertTrue(any("colorSpaceOutput" in x for x in r["problems"]), r["problems"])

    def test_without_camera_sound_a1_holds_the_music_alone(self):
        dry, r, tl = self.built()
        self.assertFalse(dry["plan"]["sound"])
        self.assertEqual(r["camera_audio"], [])
        self.assertEqual(len(tl.GetItemListInTrack("audio", 1)), 1)

    def test_camera_sound_goes_under_each_picture_on_a1_and_the_music_moves_to_a2(self):
        self.manifest["camera_sound"] = True
        dry, r, tl = self.built()
        self.assertTrue(dry["plan"]["sound"])
        self.assertEqual((r["exit_status"], r["problems"]), (0, []))
        pics = tl.GetItemListInTrack("video", 1)
        snd = tl.GetItemListInTrack("audio", 1)
        self.assertEqual([(i.start, i.end) for i in snd], [(i.start, i.end) for i in pics])
        self.assertEqual([i.source for i in snd], [i.source for i in pics])
        self.assertEqual([x["index"] for x in r["camera_audio"]], [1, 2, 3])
        (bed,) = tl.GetItemListInTrack("audio", 2)
        self.assertEqual(bed.clip.props["File Path"], r["bed"]["path"])
        self.assertEqual(r["audio"]["frames"], 54)

    def test_camera_sound_is_part_of_the_plan_hash(self):
        before = self.go()["plan_sha"]
        self.manifest["camera_sound"] = True
        self.assertNotEqual(before, self.go()["plan_sha"])

    def test_a_source_with_no_audio_is_a_problem(self):
        self.manifest["camera_sound"] = True
        with mock.patch.dict(rf.IMPORT_PROPS[self.two], {"Type": "Video"}):
            _, r, _ = self.built()
        self.assertTrue(any("camera sound" in x for x in r["problems"]), r["problems"])

    def test_camera_sound_without_music_still_lands_on_a1(self):
        self.manifest["camera_sound"] = True
        self.manifest["music"] = None
        _, r, tl = self.built()
        self.assertEqual((r["exit_status"], r["problems"]), (0, []))
        self.assertEqual(len(tl.GetItemListInTrack("audio", 1)), 3)


class TestCameraSoundDetails(Base):
    def test_camera_sound_must_be_a_boolean(self):
        self.manifest["camera_sound"] = "false"
        with self.assertRaisesRegex(Refused, "camera_sound is true or false"):
            self.go()

    def test_camera_sound_lines_up_with_a_montage_on_v2(self):
        self.manifest["camera_sound"] = True
        self.manifest["effects"] = [{"effect": "scale-window", "span_index": 1}]
        dry = self.go()
        r = self.go(dry_run=False, expect_sha=dry["plan_sha"])
        tl = next(t for t in self.project.timelines if t.name == "Shoot m v1 [auto]")
        pics = tl.GetItemListInTrack("video", dry["plan"]["track"])
        snd = tl.GetItemListInTrack("audio", 1)
        self.assertEqual([(i.start, i.end) for i in snd], [(i.start, i.end) for i in pics])
        self.assertEqual(r["problems"], [])


class TestDesqueeze(Base):
    """A source a person recorded as anamorphic gets its pool clip's PAR set before items are
    placed; the shape the timeline takes is judged against the desqueezed picture."""

    def setUp(self):
        super().setUp()
        Path(os.path.dirname(self.one), "squeeze.json").write_text(
            json.dumps({"default": 1, "clips": {"two.mov": 1.5}}))
        for p in (self.one, self.two):
            rf.IMPORT_PROPS[p]["PAR"] = "Square"
        self.addCleanup(lambda: [rf.IMPORT_PROPS[p].pop("PAR", None) for p in (self.one, self.two)])
        dims = mock.patch.object(a.dc, "video_dims", lambda path: (5952, 3968))
        dims.start()
        self.addCleanup(dims.stop)

    def clip(self, path):
        return next(c for c in a._all_clips(self.project.pool.root) if c.props["File Path"] == path)

    def test_the_dry_run_lists_the_clip_and_sets_nothing(self):
        dry = self.go()
        self.assertEqual([(r["par"], r["current"], r["action"]) for r in dry["desqueeze"]],
                         [("1.5", None, "set")])
        self.assertIn("would set Pixel Aspect Ratio on 1 clip(s)", self.summary(dry))
        self.assertFalse([c for c in rf.CALLS if c[1] == "SetClipProperty" and c[2][0] == "PAR"])

    def summary(self, r):
        from rpresolve.mcp import tools_write
        return tools_write._squeeze_line(r)

    def test_the_real_run_sets_par_and_reads_it_back_only_on_the_squeezed_clip(self):
        dry = self.go()
        r = self.go(dry_run=False, expect_sha=dry["plan_sha"])
        self.assertEqual((r["exit_status"], r["problems"]), (0, []))
        self.assertEqual(r["desqueeze"][0]["result"], "set")
        self.assertEqual(self.clip(self.two).props["PAR"], "1.5")
        self.assertEqual(self.clip(self.one).props["PAR"], "Square")  # spherical: untouched

    def test_a_clip_holding_another_non_square_value_is_reported_and_left(self):
        first = self.go(dry_run=False, expect_sha=self.go()["plan_sha"])
        self.assertEqual(first["problems"], [])
        self.clip(self.two).props["PAR"] = "2"
        self.project.timelines.pop()  # build again under a new name
        r = self.go(name="Again [auto]", dry_run=False,
                    expect_sha=self.go(name="Again [auto]")["plan_sha"])
        self.assertEqual(r["desqueeze"][0]["action"], "left")
        self.assertEqual(self.clip(self.two).props["PAR"], "2")
        self.assertTrue(any("pixel aspect ratio" in x for x in r["problems"]), r["problems"])

    def test_a_clip_that_already_holds_the_factor_is_not_written(self):
        self.go(dry_run=False, expect_sha=self.go()["plan_sha"])
        rf.CALLS.clear()
        again = self.go(name="Again [auto]")
        self.assertEqual(again["desqueeze"][0]["action"], "already")
        self.go(name="Again [auto]", dry_run=False, expect_sha=again["plan_sha"])
        self.assertFalse([c for c in rf.CALLS if c[1] == "SetClipProperty" and c[2][0] == "PAR"])

    def test_a_par_that_does_not_land_is_a_problem(self):
        with mock.patch.object(rf.Clip, "SetClipProperty", lambda c, k, v: True, create=True):
            r = self.go(dry_run=False, expect_sha=self.go()["plan_sha"])
        self.assertTrue(any("PAR" in x for x in r["problems"]), r["problems"])

    def test_a_braw_file_is_never_written(self):
        braw = os.path.join(os.path.dirname(self.one), "three.braw")
        Path(braw).write_bytes(b"x")
        self.manifest["sources"].append({"id": "s3", "path": braw})
        self.manifest["clips"][0]["spans"].append({"source": "s3", "in": 1.0, "out": 1.5})
        Path(os.path.dirname(self.one), "squeeze.json").write_text(
            json.dumps({"clips": {"two.mov": 1.5}}))  # no default: the BRAW file answers for itself
        with mock.patch.object(a.rpsqueeze, "file_tag", lambda p, ff="ffprobe": 1.5):
            p = a.plan(self.manifest)
        row = next(r for r in p["squeeze"] if r["path"] == braw)
        self.assertEqual((row["where"], row["set"]), ("file tag", False))

    def test_the_plan_hash_follows_the_pixel_aspect_ratio_rows(self):
        before = self.go()["plan_sha"]
        Path(os.path.dirname(self.one), "squeeze.json").write_text(
            json.dumps({"default": 1, "clips": {"two.mov": 2.0}}))
        self.assertNotEqual(before, self.go()["plan_sha"])

    def test_a_shape_that_crops_more_than_five_percent_says_so_and_names_the_fit(self):
        r = self.go(width=3840, height=1608)  # 2.39:1 over a 2.25:1 picture
        (row,) = r["shape"]
        self.assertEqual(row["crop"], {"axis": "height", "percent": 5.8})
        self.assertEqual(row["fits"], "3840x1708")
        self.assertIn("crops 5.8% of the desqueezed picture's height", row["note"])
        self.assertIn("3840x1708 keeps all of it", self.summary(r))
        self.assertEqual(r["size"], [3840, 1608])  # it does not choose a shape

    def test_a_wide_picture_in_a_narrow_shape_crops_its_width(self):
        (row,) = self.go(width=1920, height=1080)["shape"]
        self.assertEqual(row["crop"]["axis"], "width")
        self.assertGreater(row["crop"]["percent"], 20)

    def test_a_shape_within_five_percent_is_quiet(self):
        (row,) = self.go(width=3840, height=1708)["shape"]
        self.assertEqual((row["note"], row["fits"]), ("", None))

    def test_a_source_whose_size_cannot_be_read_is_not_judged(self):
        with mock.patch.object(a.dc, "video_dims", lambda path: None):
            (row,) = self.go()["shape"]
        self.assertIn("not judged", row["note"])


class TestCompactResult(Base):
    """The tool's result groups per-file rows that differ only by path and says each distinct
    note once with a count; the plan_sha and the full result are unchanged."""

    def many(self, n=12):
        d = os.path.dirname(self.one)
        spans, sources = [], []
        for i in range(n):
            p = os.path.join(d, f"cam{i:02d}.mov")
            Path(p).write_bytes(b"x")
            rf.IMPORT_PROPS[p] = {"FPS": "30.0", "Input Color Space": UNTAGGED, "Data Level": "Auto",
                                  "PAR": "Square"}
            self.detected[p] = drow(p)
            sources.append({"id": f"c{i}", "path": p})
            spans.append({"source": f"c{i}", "in": 1.0, "out": 1.5})
        self.addCleanup(lambda: [rf.IMPORT_PROPS.pop(s["path"], None) for s in sources])
        Path(d, "squeeze.json").write_text(json.dumps({"default": 1.5}))
        self.manifest.update(sources=sources, music=None)
        self.manifest["clips"][0]["spans"] = spans
        mock.patch.object(a.dc, "video_dims", lambda p: (5952, 3968)).start()
        self.addCleanup(mock.patch.stopall)

    def tool(self, **more):
        from rpresolve.mcp import tools_write
        from rpresolve.mcp.registry import Registry
        reg = Registry()
        tools_write.register(reg)
        return reg

    def test_identical_rows_merge_with_a_count_and_the_file_names(self):
        self.many(12)
        full = self.go(width=3840, height=1608)
        c = a.compact(full)
        self.assertEqual(len(full["desqueeze"]), 12)
        (row,) = c["desqueeze"]
        self.assertEqual((row["count"], len(row["files"]), row["par"]), (12, 12, "1.5"))
        (srow,) = c["shape"]
        self.assertEqual(srow["count"], 12)
        self.assertEqual(len(c["colour"]["sources"]), 1)
        self.assertEqual(c["plan_sha"], full["plan_sha"])
        self.assertLess(len(json.dumps(c, default=str)), 0.7 * len(json.dumps(full, default=str)))

    def test_a_row_that_differs_stays_a_full_row(self):
        self.many(4)
        clip_path = self.manifest["sources"][2]["path"]
        full = self.go()
        full["desqueeze"][2] = {**full["desqueeze"][2], "action": "left", "current": "2"}
        c = a.compact(full)
        paths = [r.get("path") for r in c["desqueeze"]]
        self.assertIn(clip_path, paths)
        self.assertEqual(sorted(r.get("count", 1) for r in c["desqueeze"]), [1, 3])

    def test_once_says_each_distinct_line_one_time_with_a_count(self):
        lines = ["colour: a.mov: odd", "colour: b.mov: odd", "colour: c.mov: other"]
        self.assertEqual(a.once(lines, ["a.mov", "b.mov", "c.mov"]),
                         ["2 sources: colour: each: odd", "colour: c.mov: other"])

    def test_the_mcp_summary_names_the_shape_note_once(self):
        from rpresolve.mcp import tools_write
        self.many(12)
        r = self.go(width=3840, height=1608)
        line = tools_write._squeeze_line(r)
        self.assertEqual(line.count("keeps all of it"), 1)
        self.assertIn("12 sources: ", line)


class TestCameraLevel(Base):
    """camera_sound_lufs: one gain for the whole camera bed, measured over the spans used."""

    def setUp(self):
        super().setUp()
        self.manifest["camera_sound"] = True
        self.manifest["camera_sound_lufs"] = -27
        self.measured = {"integrated_lufs": -53.0, "true_peak_dbtp": -30.0}
        patch = mock.patch.object(a, "measure_bed", lambda p: self.measured)
        patch.start()
        self.addCleanup(patch.stop)

    def built(self):
        dry = self.go()
        r = self.go(dry_run=False, expect_sha=dry["plan_sha"])
        tl = next(t for t in self.project.timelines if t.name == "Shoot m v1 [auto]")
        return dry, r, tl

    def test_the_gain_is_planned_and_applied_to_every_camera_item_with_read_back(self):
        dry, r, tl = self.built()
        self.assertEqual((dry["camera_level"]["gain_db"], dry["camera_level"]["true_peak_after"]),
                         (26.0, -4.0))
        self.assertEqual((r["exit_status"], r["problems"]), (0, []))
        self.assertEqual(r["camera_level"]["applied"], 3)
        for it in tl.GetItemListInTrack("audio", 1):
            self.assertEqual(it.GetProperty("AudioVolume"), 26.0)

    def test_a_gain_that_would_clip_is_refused_in_the_dry_run(self):
        self.measured = {"integrated_lufs": -53.0, "true_peak_dbtp": -20.0}
        with self.assertRaisesRegex(Refused, "above the -1 dBTP ceiling"):
            self.go()

    def test_a_silent_bed_is_refused(self):
        self.measured = {"integrated_lufs": float("-inf"), "true_peak_dbtp": -90.0}
        with self.assertRaisesRegex(Refused, "silent"):
            self.go()

    def test_the_target_needs_camera_sound_and_a_sane_number(self):
        self.manifest["camera_sound"] = False
        with self.assertRaisesRegex(Refused, "set camera_sound true"):
            self.go()
        self.manifest["camera_sound"] = True
        self.manifest["camera_sound_lufs"] = "loud"
        with self.assertRaisesRegex(Refused, "number from -70 to 0"):
            self.go()

    def test_the_level_is_part_of_the_plan_hash(self):
        before = self.go()["plan_sha"]
        self.measured = {"integrated_lufs": -50.0, "true_peak_dbtp": -30.0}
        self.assertNotEqual(before, self.go()["plan_sha"])

    def test_items_that_refuse_the_volume_are_a_problem_with_the_gain_named(self):
        orig = rf.Item.SetProperty

        def refuse(it, key, value):
            return False if key == "AudioVolume" else orig(it, key, value)
        with mock.patch.object(rf.Item, "SetProperty", refuse):
            _, r, _ = self.built()
        self.assertTrue(any("did not take AudioVolume +26 dB" in x for x in r["problems"]), r["problems"])
        self.assertTrue(any("set +26 dB on A1 by hand" in x for x in r["plan"]["not_built"]))
        self.assertEqual(r["camera_level"]["applied"], 0)

    def test_without_the_key_nothing_is_measured_or_set(self):
        del self.manifest["camera_sound_lufs"]
        with mock.patch.object(a, "measure_bed", side_effect=AssertionError("measured")):
            dry, r, tl = self.built()
        self.assertIsNone(r["camera_level"])

    def test_the_summary_reports_the_measure_and_the_gain(self):
        from rpresolve.mcp import tools_write
        self.assertIn("measures -53 LUFS (true peak -30 dBTP): would set +26 dB for -27 LUFS",
                      tools_write._level_line(self.go()))

    @needs_ffmpeg
    def test_measure_bed_reads_exactly_the_spans_used(self):
        # a 1 s tone at a known level then a quiet second; only the loud second is used
        src = os.path.join(os.path.dirname(self.one), "tone.wav")
        subprocess.run([dc.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=1000:d=1",
                        "-f", "lavfi", "-i", "sine=f=1000:d=1", "-filter_complex",
                        "[0:a]volume=0.1[a];[1:a]volume=0.001[b];[a][b]concat=n=2:v=0:a=1",
                        src], check=False, stdin=subprocess.DEVNULL)
        if not os.path.isfile(src):
            self.skipTest("could not make the test tone")
        real = REAL_MEASURE(
            {"fps": 24, "spans": [{"path": src, "in": 0.0, "frames": 24}]})
        loud = REAL_MEASURE({"fps": 24, "spans": [{"path": src, "in": 1.0, "frames": 24}]})
        self.assertGreater(real["integrated_lufs"], loud["integrated_lufs"] + 20)
