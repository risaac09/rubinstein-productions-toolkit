"""
Tests for rpresolve.drafts, the draft farm, on synthetic clips: each plan renders to a
720p proxy of the planned length at the method's rate and shape, the effects ffmpeg
cannot draw are named, the lanes tuner adds lanes only while throughput rises, the look
cube is found by name and camera, BRAW plays only from a proxy, and output in a git tree
is refused.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rpresolve import drafts, methods  # noqa: E402

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")

METHOD = {
    "edit_method": 1, "id": "test-draft", "name": "Test draft", "summary": "three slots",
    "fits": ["outdoor"], "aspect": "16:9", "fps": 24, "length_s": 2.5, "bpm": None,
    "look": "print-teal",
    "rules": {"reuse": False, "min_shot_s": 0.2, "snap_to_beat": False, "end": "black:0.25"},
    "slots": [
        {"role": "establishing", "seconds": 1.0, "query": "wide", "move": "push-in", "speed": 1.0,
         "transition_out": {"type": "dissolve", "frames": 2}, "effect": "zoom-push",
         "scale": None, "text": None},
        {"role": "face", "seconds": 0.5, "query": "close", "move": "static", "speed": 1.0,
         "transition_out": {"type": "cut", "frames": 0}, "effect": "crop-window",
         "scale": [1.2, 1.2], "text": None},
        {"role": "closer", "seconds": 0.75, "query": "end", "move": "static", "speed": 1.0,
         "transition_out": {"type": "cut", "frames": 0}, "effect": "scale-window",
         "scale": None, "text": "word"}]}


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                          "-show_entries", "stream=width,height,nb_read_frames,r_frame_rate",
                          "-of", "json", path], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)["streams"][0]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.realpath(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def plan(self, name, method, src, aspect=None):
        m = copy.deepcopy(method)
        if aspect:
            m["aspect"] = aspect
        fills, t = [], 0.5
        for s in m["slots"]:
            fills.append({"source_path": src, "in": t, "out": t + s["seconds"]})
            t += s["seconds"] + 0.5
        d = os.path.join(self.dir, "plans", name)
        os.makedirs(d)
        with open(os.path.join(d, "method.json"), "w") as f:
            json.dump(m, f)
        with open(os.path.join(d, "manifest.json"), "w") as f:
            json.dump(methods.to_manifest(m, fills), f)
        return d


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class TestRender(Base):
    def setUp(self):
        super().setUp()
        self.src = os.path.join(self.dir, "src.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=s=1920x1080:r=24:d=8", "-pix_fmt", "yuv420p",
                        "-c:v", "libx264", "-preset", "ultrafast", self.src], check=True)

    def test_two_plans_render_at_their_shape_and_length(self):
        a = self.plan("wide", METHOD, self.src)
        b = self.plan("tall", METHOD, self.src, aspect="9:16")
        out = os.path.join(self.dir, "out")
        doc = drafts.build([a, b], out, hw=False, lanes=2)
        self.assertEqual([d["plan"] for d in doc["drafts"]], ["wide", "tall"])
        for d, size in zip(doc["drafts"], ([1280, 720], [720, 1280])):
            st = probe(d["file"])
            self.assertEqual([st["width"], st["height"]], size)
            self.assertEqual(st["r_frame_rate"], "24/1")
            self.assertEqual(int(st["nb_read_frames"]), 24 + 12 + 18 + 6)  # spans plus black
            notes = " | ".join(d["notes"])
            self.assertIn("span 2: scale-window: drawn over black", notes)
            self.assertIn("span 1: the dissolve into it is drawn as a cut", notes)
            self.assertIn("look print-teal not applied", notes)
            self.assertTrue(os.path.isfile(os.path.join(out, d["plan"] + ".notes.txt")))
        self.assertEqual(doc["lanes"], 2)
        with open(os.path.join(out, "drafts.json")) as f:
            self.assertEqual(json.load(f)["encoder"], "libx264")
        if (doc["sheet"] or {}).get("dir"):
            self.assertTrue(os.path.isfile(os.path.join(out, "sheet", "sheet.jpg")))
            self.assertTrue(os.path.isfile(os.path.join(out, "sheet.key", "key.json")))
        else:
            self.assertIn("no sheet", doc["sheet"]["error"])
        self.assertEqual(sorted(p for p in os.listdir(out) if p.startswith(".drafts-")), [])

    def test_a_look_cube_is_applied(self):
        root = os.path.join(self.dir, "luts", "RP Looks")
        os.makedirs(root)
        with open(os.path.join(root, "RP Print Teal [Cam].cube"), "w") as f:
            f.write("LUT_3D_SIZE 2\n" + "".join(f"{r} {g} {b}\n" for b in (0, 1) for g in (0, 1)
                                                for r in (0, 1)))
        a = self.plan("graded", METHOD, self.src)
        doc = drafts.build([a], os.path.join(self.dir, "Isaac's out, take: 2"), camera="Cam",
                           lut_root=os.path.dirname(root), hw=False, lanes=1)
        self.assertTrue(doc["drafts"][0]["lut"].endswith("RP Print Teal [Cam].cube"))
        self.assertFalse(any("not applied" in n for n in doc["drafts"][0]["notes"]))
        self.assertIsNone(doc["sheet"])
        self.assertEqual(int(probe(doc["drafts"][0]["file"])["nb_read_frames"]), 60)

    def test_a_fill_past_the_source_end_is_refused(self):
        short = os.path.join(self.dir, "short.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=s=640x360:r=24:d=2", "-pix_fmt", "yuv420p",
                        "-c:v", "libx264", "-preset", "ultrafast", short], check=True)
        a = self.plan("short", METHOD, short)  # the third slot runs to 3.75 s of a 2 s clip
        with self.assertRaisesRegex(drafts.DraftError, "past the end of its source"):
            drafts.build([a], os.path.join(self.dir, "out"), hw=False, lanes=1)


class TestRefusals(Base):
    def test_out_in_a_git_tree_or_not_empty(self):
        with self.assertRaisesRegex(drafts.DraftError, "inside a git working tree"):
            drafts.build([], str(HERE / "draft-out"))
        busy = os.path.join(self.dir, "busy")
        os.makedirs(busy)
        Path(busy, "x").write_text("x")
        with self.assertRaisesRegex(drafts.DraftError, "not empty"):
            drafts.build([], busy)

    def test_braw_needs_a_proxy(self):
        with self.assertRaisesRegex(drafts.DraftError, "cannot decode BRAW"):
            drafts.media_path("/w/A001.braw")
        Path(self.dir, "A001.mov").write_text("")
        self.assertEqual(drafts.media_path("/w/A001.braw", self.dir),
                         os.path.join(self.dir, "A001.mov"))
        self.assertEqual(drafts.media_path("/w/a.mp4"), "/w/a.mp4")

    def test_a_plan_needs_a_manifest(self):
        with self.assertRaisesRegex(drafts.DraftError, "no manifest.json"):
            drafts.load_plan(self.dir)

    def test_every_drawn_effect_has_a_filter(self):
        for e in drafts.DRAWN:
            self.assertTrue(drafts.effect_filter(e, 1.0, 24, 1280, 720), e)
        for e in ("person-matte-text", "cross-dissolve-short"):
            self.assertIsNone(drafts.effect_filter(e, 1.0, 24, 1280, 720))


class TestNewDraws(unittest.TestCase):
    def vf(self, effects, seconds=1.0):
        return drafts.span_filter({}, effects, 1280, 720, 24, seconds)

    def test_music_is_cut_to_the_draft_and_faded(self):
        self.assertEqual(drafts.music_args(None, 10), [])
        a = drafts.music_args({"path": "/m/song.m4a", "in": 8.24}, 10.0)
        self.assertEqual(a[:6], ["-ss", "8.2400", "-t", "10.0000", "-i", "/m/song.m4a"])
        self.assertIn("afade=t=out:st=9.000:d=1.000", a)

    def test_retime_is_a_setpts_before_the_scale(self):
        vf, missing = self.vf([{"effect": "retime", "params": {"speed": 0.5}}])
        self.assertTrue(vf.startswith("setpts=PTS/0.5,scale="), vf)
        self.assertEqual(missing, [])
        ramp, _ = self.vf([{"effect": "retime", "params": {"speed": 0.5, "speed_to": 1.5}}], 2.0)
        self.assertTrue(ramp.startswith("setpts='(sqrt(0.25+"), ramp)
        # u = 0.5 t + 0.25 t^2 over 2 s: 2 s of output consume 2 s of source
        self.assertIn("-0.5)/0.5/TB'", ramp)

    def test_reframe_pan_replaces_the_centred_crop_and_is_labelled(self):
        vf, missing = self.vf([{"effect": "reframe-pan",
                                "params": {"pan_from": 0.6, "pan_to": 0.3}}])
        self.assertIn("crop=1280:720:x='(iw-1280)*(0.6+(0.3-0.6)*t/1.0000)'", vf)
        self.assertEqual(vf.count("crop=1280:720"), 1)
        self.assertTrue(missing[0].startswith("reframe-pan: the crop moves"))

    def test_scale_window_grows_to_full_and_is_labelled(self):
        vf, missing = self.vf(["scale-window"])
        self.assertIn("0.22+0.5*min(t*24\\,36)/36", vf)
        self.assertIn("pad=1280:720", vf)
        self.assertTrue(missing[0].startswith("scale-window: drawn over black"))


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class TestNewDrawsRender(Base):
    def test_retime_ramp_pan_and_window_render_the_planned_frames(self):
        src = os.path.join(self.dir, "src.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=s=1920x1080:r=24:d=8", "-pix_fmt", "yuv420p",
                        "-c:v", "libx264", "-preset", "ultrafast", src], check=True)
        m = copy.deepcopy(METHOD)
        m["rules"]["end"] = None
        m["length_s"] = 2.25
        m["slots"][0].update(effect=None, speed=0.5)
        m["slots"][2].update(effect="scale-window")
        d = self.plan("new", m, src, aspect="9:16")
        mp = os.path.join(d, "manifest.json")
        with open(mp) as f:
            man = json.load(f)
        self.assertIn({"span_index": 0, "effect": "retime", "params": {"speed": 0.5}},
                      man["effects"])
        man["effects"][0]["params"] = {"speed": 0.5, "speed_to": 1.5}
        man["effects"].append({"span_index": 1, "effect": "reframe-pan",
                               "params": {"pan_from": 0.2, "pan_to": 0.8}})
        with open(mp, "w") as f:
            json.dump(man, f)
        doc = drafts.build([d], os.path.join(self.dir, "out"), hw=False, lanes=1)
        st = probe(doc["drafts"][0]["file"])
        self.assertEqual(int(st["nb_read_frames"]), 24 + 12 + 18)
        notes = " | ".join(doc["drafts"][0]["notes"])
        self.assertIn("span 1: reframe-pan:", notes)
        self.assertNotIn("retime", notes)


class TestLanes(unittest.TestCase):
    def tune(self, rate_for_lanes, n_jobs=40, cap=16):
        """A fake runner whose throughput depends only on how many run at once."""
        import threading
        import time
        live, lock = [0], threading.Lock()
        seen = []

        def runner(job):
            with lock:
                live[0] += 1
                seen.append(live[0])
            time.sleep(0.01 / rate_for_lanes(live[0]))
            with lock:
                live[0] -= 1
        jobs = [{"seconds": 0.01} for _ in range(n_jobs)]
        return drafts.run_lanes(jobs, cap=cap, runner=runner), seen

    def test_adds_lanes_while_throughput_rises(self):
        r, _ = self.tune(lambda n: 1.0)  # each job takes as long however many run
        self.assertGreaterEqual(r["lanes"], 3)
        self.assertEqual(r["probe"][0][0], 1)

    def test_stops_when_throughput_stops_rising(self):
        r, _ = self.tune(lambda n: 1.0 / (n * n))  # contention: each lane lowers throughput
        self.assertEqual(r["lanes"], 1)

    def test_fixed_lanes_and_cap(self):
        r, _ = self.tune(lambda n: 1.0, n_jobs=4)
        self.assertLessEqual(r["lanes"], 16)
        self.assertEqual(drafts.run_lanes([{"seconds": 1}], lanes=40, cap=4,
                                          runner=lambda j: None)["lanes"], 4)


if __name__ == "__main__":
    unittest.main()
