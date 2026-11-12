"""
Tests for rpresolve.shotlist and `shotlist` on synthetic paths: a list and a cut plan
compile into the v2 manifest, retime and reframe-pan entries, method.json and crops.json
the draft farm reads, and the draft farm draws a retime and a moving crop on one span.

Run: /usr/bin/python3 -m unittest production/tests/test_shotlist.py -v
"""

import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rpresolve import cli, cmd_vertical, cutlist, drafts, shotlist  # noqa: E402


def shot(name, clip, at, beats, cx, fps=None, **kw):
    s = {"name": name, "source": f"/v/trip/{clip}.mov", "in": at, "beats": beats, "cx": cx, **kw}
    if fps:
        s["source_fps"] = fps
    return s


# The shape of a real morning list: 30 and 60 fps sources conformed to 24, a pan or two,
# one clip used twice, an end black and music; the last shot carries its own speed.
LIST = {"fps": 24, "aspect": "9:16", "look": "none", "end": "black:0.4",
        "music": {"path": "/v/songs/bed.m4a"},
        "shots": [shot("a", "c1", 3.6, 4, [0.46, 0.46], 30),
                  shot("b", "c1", 3.9, 2, [0.5, 0.5], 30),
                  shot("c", "c2", 9.5, 2, [0.43, 0.31], 60),
                  shot("d", "c3", 1.0, 1, [0.6, 0.6], 24),
                  shot("e", "c4", 15.9, 4, [0.4, 0.68], speed=0.5)]}
PLAN = {"music_in": 4.07, "cut_frames": [0, 47, 71, 97, 117, 166]}


def compiled(sl=LIST, plan=PLAN):
    return shotlist.compile_list(sl, shotlist.shot_frames(sl, plan), plan["music_in"])


class TestValidate(unittest.TestCase):
    def test_a_sound_list_has_no_problems(self):
        self.assertEqual(shotlist.validate(LIST), [])

    def test_each_problem_is_one_line_naming_the_shot(self):
        bad = copy.deepcopy(LIST)
        bad["shots"][1].update({"beats": 0, "cx": [0.5, 1.5], "source": "rel.mov"})
        bad["shots"][2]["name"] = "a"
        bad["shots"][3].update({"in": -1, "speed": 0})
        got = shotlist.validate(bad)
        self.assertEqual(len(got), 6)
        self.assertTrue(all("\n" not in g for g in got))
        for who in ("shot 2 (b): beats", "shot 2 (b): cx", "shot 2 (b): source",
                    "shot 3 (a): name is used twice", "shot 4 (d): in", "shot 4 (d): speed"):
            self.assertTrue(any(g.startswith(who) for g in got), who)

    def test_the_header_is_checked(self):
        for key, val in (("fps", 0), ("aspect", "1:1"), ("look", "no-such-look"),
                         ("end", "fade:1"), ("music", {"path": "rel.m4a"}), ("shots", [])):
            bad = dict(LIST, **{key: val})
            self.assertTrue(shotlist.validate(bad), key)
        self.assertEqual(shotlist.validate([]), ["a shot list is a JSON object"])


class TestConform(unittest.TestCase):
    def test_thirty_and_sixty_conform_to_a_24_list_and_the_rest_do_not(self):
        c = shotlist.conform_speed
        self.assertEqual((c(24, 30), c(24, 29.97), c(24, 60), c(24, 59.94)), (0.8, 0.8, 0.4, 0.4))
        self.assertEqual((c(24, 24), c(24, 25), c(24, None), c(30, 30), c(25, 60)),
                         (1.0, 1.0, 1.0, 1.0, 1.0))

    def test_a_speed_on_the_shot_wins(self):
        self.assertEqual(shotlist.shot_speed(LIST["shots"][4], 24), 0.5)


class TestCompile(unittest.TestCase):
    def setUp(self):
        self.man, self.method = compiled()

    def test_the_manifest_has_the_shape_the_farm_reads(self):
        m = self.man
        cutlist.validate_manifest(m, allow_no_speech=True)
        self.assertEqual((m["version"], m["speech"], m["fps"]), (2, False, 24))
        self.assertEqual([s["path"] for s in m["sources"]],
                         ["/v/trip/c1.mov", "/v/trip/c2.mov", "/v/trip/c3.mov", "/v/trip/c4.mov"])
        self.assertEqual([s["id"] for s in m["sources"]], ["s1", "s2", "s3", "s4"])
        self.assertEqual(m["method"], {"id": "shotlist", "aspect": "9:16", "look": None,
                                       "end": "black:0.4", "ui_steps": []})
        self.assertEqual(m["music"], {"path": "/v/songs/bed.m4a", "in": 4.07})

    def test_a_span_lasts_its_timeline_frames_from_its_in(self):
        spans = self.man["clips"][0]["spans"]
        frames = [cutlist.frames(s, 24) for s in spans]
        self.assertEqual(frames, [47, 24, 26, 20, 49])
        self.assertEqual([s["in"] for s in spans], [3.6, 3.9, 9.5, 1.0, 15.9])
        self.assertEqual(spans[0]["out"], round(3.6 + 47 / 24, 6))

    def test_overlapping_footage_in_one_source_is_marked_reuse(self):
        spans = self.man["clips"][0]["spans"]
        self.assertTrue(spans[1].get("reuse"))
        self.assertFalse(spans[0].get("reuse"))

    def test_effects_are_a_retime_per_speed_and_a_pan_per_moving_crop(self):
        self.assertEqual(self.man["effects"], [
            {"span_index": 0, "effect": "retime", "params": {"speed": 0.8}},
            {"span_index": 1, "effect": "retime", "params": {"speed": 0.8}},
            {"span_index": 2, "effect": "reframe-pan", "params": {"pan_from": 0.43, "pan_to": 0.31}},
            {"span_index": 2, "effect": "retime", "params": {"speed": 0.4}},
            {"span_index": 4, "effect": "reframe-pan", "params": {"pan_from": 0.4, "pan_to": 0.68}},
            {"span_index": 4, "effect": "retime", "params": {"speed": 0.5}}])

    def test_the_music_start_is_the_lists_own_when_it_has_one(self):
        sl = copy.deepcopy(LIST)
        sl["music"]["in"] = 9.0
        self.assertEqual(compiled(sl)[0]["music"]["in"], 9.0)
        del sl["music"]
        self.assertNotIn("music", compiled(sl)[0])

    def test_the_method_is_sound_and_matches_the_cuts(self):
        from rpresolve import methods
        self.assertEqual(methods.validate(self.method), [])
        self.assertEqual(methods.end_black(self.method), 0.4)
        self.assertAlmostEqual(self.method["length_s"], 166 / 24 + 0.4)

    def test_cut_frames_must_fit_the_shots(self):
        for frames in ([0, 47, 71], [0, 47, 47, 97, 117, 166], [0, 47.5, 71, 97, 117, 166]):
            with self.assertRaises(shotlist.ShotlistError):
                shotlist.shot_frames(LIST, {"cut_frames": frames})

    def test_a_period_gives_every_shot_that_length(self):
        self.assertEqual(shotlist.shot_frames(LIST, None, 0.5), [12] * 5)
        with self.assertRaises(shotlist.ShotlistError):
            shotlist.shot_frames(LIST, None, None)


class TestFarmDraws(unittest.TestCase):
    """The draft farm draws a retime and a moving crop on one span of such a manifest."""

    def test_span_filter_has_both_setpts_and_the_pan_expression(self):
        man, _ = compiled()
        by = {}
        for e in man["effects"]:
            by.setdefault(e["span_index"], []).append(e)
        span = man["clips"][0]["spans"][2]
        vf, missing = drafts.span_filter(span, by[2], 720, 1280, 24, 26 / 24)
        self.assertTrue(vf.startswith("setpts=PTS/0.4,"))
        self.assertIn("crop=720:1280:x='(iw-720)*(0.43+(0.31-0.43)*t/", vf)
        self.assertEqual(missing, ["reframe-pan: the crop moves from pan_from to pan_to as a "
                                   "share of the source width, not Resolve's Pan arithmetic"])

    def test_plan_jobs_builds_from_the_written_plan_folder(self):
        man, method = compiled()
        with tempfile.TemporaryDirectory() as d:
            for name, doc in (("manifest.json", man), ("method.json", method)):
                Path(d, name).write_text(json.dumps(doc))
            plan = drafts.load_plan(d)
            jobs, info = drafts.plan_jobs(plan, d, False)
        self.assertEqual(len(jobs), 6)  # five shots and the end black
        self.assertEqual(info["frames"], 166 + 10)
        self.assertEqual(info["size"], [720, 1280])
        vf = jobs[2]["cmd"][jobs[2]["cmd"].index("-vf") + 1]
        self.assertIn("setpts=PTS/0.4", vf)
        self.assertIn("(0.43+(0.31-0.43)", vf)


class TestCrops(unittest.TestCase):
    def test_a_9x16_list_gives_a_crops_file_load_crops_accepts(self):
        man, _ = compiled()
        crops = shotlist.crops_for(LIST, man)
        cmd_vertical.load_crops(crops)
        self.assertEqual([i["cx"] for i in crops["items"]], [0.46, 0.5, 0.37, 0.6, 0.54])
        self.assertEqual([i["speed"] for i in crops["items"]], [0.8, 0.8, 0.4, 1.0, 0.5])
        centres = drafts.span_centres({"method": {"aspect": "9:16"}, "manifest": man}, crops)
        self.assertEqual(centres, [0.46, 0.5, 0.37, 0.6, 0.54])

    def test_a_16x9_list_gives_none(self):
        sl = dict(LIST, aspect="16:9")
        self.assertIsNone(shotlist.crops_for(sl, compiled(sl)[0]))


class TestCommand(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.list = self.write("list.json", LIST)
        self.plan = self.write("plan.json", PLAN)

    def write(self, name, doc):
        p = os.path.join(self.dir, name)
        Path(p).write_text(json.dumps(doc))
        return p

    def cmd(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = cli.main(["shotlist", *argv])
        return status, out.getvalue(), err.getvalue()

    def test_check_passes_a_good_list_and_names_the_bad_shot(self):
        self.assertEqual(self.cmd("check", self.list)[0], 0)
        bad = copy.deepcopy(LIST)
        bad["shots"][2]["beats"] = 0
        status, _, err = self.cmd("check", self.write("bad.json", bad))
        self.assertEqual(status, 1)
        self.assertEqual(err.strip(), "ERROR: shot 3 (c): beats must be a whole number above 0")

    def test_compile_writes_the_three_files(self):
        out = os.path.join(self.dir, "out")
        status, text, err = self.cmd("compile", self.list, "--cuts", self.plan, "--out", out)
        self.assertEqual((status, err), (0, ""), err)
        self.assertEqual(sorted(os.listdir(out)), ["crops.json", "manifest.json", "method.json"])
        man = json.loads(Path(out, "manifest.json").read_text())
        self.assertEqual(man, json.loads(json.dumps(compiled()[0])))
        self.assertIn("5 spans, 166 frames", text)

    def test_a_16x9_list_writes_no_crops(self):
        out = os.path.join(self.dir, "out")
        sl = self.write("wide.json", dict(LIST, aspect="16:9"))
        self.assertEqual(self.cmd("compile", sl, "--cuts", self.plan, "--out", out)[0], 0)
        self.assertEqual(sorted(os.listdir(out)), ["manifest.json", "method.json"])

    def test_period_stands_in_for_cuts_and_both_or_neither_is_refused(self):
        out = os.path.join(self.dir, "out")
        self.assertEqual(self.cmd("compile", self.list, "--period", "0.5", "--out", out)[0], 0)
        s, _, err = self.cmd("compile", self.list, "--out", os.path.join(self.dir, "o2"))
        self.assertEqual(s, 1)
        self.assertIn("--cuts", err)
        s, _, err = self.cmd("compile", self.list, "--period", "1", "--cuts", self.plan,
                             "--out", os.path.join(self.dir, "o3"))
        self.assertEqual(s, 1)

    def test_an_excluded_take_is_refused_and_nothing_is_written(self):
        out = os.path.join(self.dir, "out")
        ex = self.write("exclude.json", {"takes": ["C3"]})
        s, _, err = self.cmd("compile", self.list, "--cuts", self.plan, "--out", out,
                             "--exclude", ex)
        self.assertEqual(s, 1)
        self.assertIn("excluded take(s) in the shot list: span 4 c3.mov (C3)", err)
        self.assertFalse(os.path.exists(out))
        music = self.write("exclude2.json", {"takes": ["songs/bed"]})
        s, _, err = self.cmd("compile", self.list, "--cuts", self.plan, "--out", out,
                             "--exclude", music)
        self.assertEqual(s, 1)
        self.assertIn("music bed.m4a", err)

    def test_method_copies_its_look_and_end_and_nothing_else(self):
        out = os.path.join(self.dir, "out")
        s, _, err = self.cmd("compile", self.list, "--cuts", self.plan, "--out", out,
                             "--method", "slow-cinematic")
        self.assertEqual(s, 0, err)
        man = json.loads(Path(out, "manifest.json").read_text())
        self.assertEqual((man["method"]["look"], man["method"]["end"]), ("ice-blue", None))
        self.assertEqual(len(man["clips"][0]["spans"]), 5)
        self.assertEqual([s["in"] for s in man["clips"][0]["spans"]], [3.6, 3.9, 9.5, 1.0, 15.9])
        self.assertEqual(self.cmd("compile", self.list, "--cuts", self.plan, "--out",
                                  os.path.join(self.dir, "o2"), "--method", "nope")[0], 1)

    def test_output_inside_a_git_tree_is_refused(self):
        os.makedirs(os.path.join(self.dir, "repo", ".git"))
        s, _, err = self.cmd("compile", self.list, "--cuts", self.plan, "--out",
                             os.path.join(self.dir, "repo", "out"))
        self.assertEqual(s, 1)
        self.assertIn("git working tree", err)


if __name__ == "__main__":
    unittest.main()
