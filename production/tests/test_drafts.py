"""
Tests for rpresolve.drafts, the draft farm, on synthetic clips: each plan renders to a
720p proxy of the planned length at the method's rate and shape, the effects ffmpeg
cannot draw are named, the lanes tuner adds lanes only while throughput rises, the look
cube is found by name and camera, BRAW plays only from a proxy, and output in a git tree
is refused.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import contextlib
import copy
import io
import json
import os
import re
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
            self.assertIn("span 2: scale-window: over the previous shot's last frame", notes)
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

    def two_lettered_plans(self):
        """Two plans out of one source; the second has an effect no draft can show."""
        m = copy.deepcopy(METHOD)
        m["slots"][1]["effect"] = "person-matte-text"
        m["slots"][1]["text"] = None
        return [self.plan("plain", METHOD, self.src), self.plan("matted", m, self.src)]

    def test_the_ledger_is_in_drafts_json_per_draft(self):
        plans = self.two_lettered_plans()
        out = os.path.join(self.dir, "out")
        drafts.build(plans, out, hw=False, lanes=2)
        with open(os.path.join(out, "drafts.json")) as f:
            by_plan = {d["plan"]: d["effects"] for d in json.load(f)["drafts"]}
        plain, matted = by_plan["plain"], by_plan["matted"]
        self.assertEqual(plain["drawn"], [{"effect": "zoom-push", "spans": [0]}])
        self.assertEqual([(e["effect"], e["spans"]) for e in plain["approximated"]],
                         [("crop-window", [1]), ("dissolve", [1]), ("scale-window", [2])])
        self.assertEqual(plain["not_drawn"], [])
        self.assertEqual(matted["not_drawn"], [{"effect": "person-matte-text", "spans": [1]}])
        self.assertNotIn("crop-window", json.dumps(matted))

    def test_send_holds_the_drafts_under_the_sheets_letters_without_naming_them(self):
        plans = self.two_lettered_plans()
        out = os.path.join(self.dir, "out")
        shown = io.StringIO()
        with contextlib.redirect_stdout(shown):
            self.assertEqual(drafts.main(plans + ["--out", out, "--no-hw", "--lanes", "2"]), 0)
        with open(os.path.join(out, "drafts.json")) as f:
            doc = json.load(f)
        if not (doc["sheet"] or {}).get("dir"):
            self.skipTest("no sheet without Pillow, so no letters")
        with open(os.path.join(out, "sheet.key", "key.json")) as f:
            labels = json.load(f)["labels"]  # the sealed key: the only place letters meet plans
        self.assertEqual(sorted(labels), ["A", "B"])
        self.assertEqual(sorted(os.listdir(os.path.join(out, "send"))),
                         ["A.mp4", "A.notes.txt", "B.mp4", "B.notes.txt"])
        for letter, label in labels.items():
            plan = os.path.splitext(os.path.basename(label["source"]))[0]
            with open(os.path.join(out, plan + ".mp4"), "rb") as a, \
                    open(os.path.join(out, "send", letter + ".mp4"), "rb") as b:
                self.assertEqual(a.read(), b.read(), letter)
            with open(os.path.join(out, "send", letter + ".notes.txt")) as f:
                head, *rest = f.read().splitlines()
            with open(os.path.join(out, plan + ".notes.txt")) as f:
                self.assertEqual(rest, f.read().splitlines())
            self.assertEqual(head, f"{len(rest)} notes")
            self.assertEqual(any("person-matte-text (no ffmpeg approximation)" in x for x in rest),
                             plan == "matted", letter)
        # nothing that is printed or left beside the folder pairs a letter with a plan
        self.assertEqual(sorted(doc["send"]["files"]),
                         ["A.mp4", "A.notes.txt", "B.mp4", "B.notes.txt"])
        self.assertNotIn("summary", doc["sheet"])
        self.assertNotRegex(shown.getvalue(), r"\b[AB]=")
        self.assertNotRegex(json.dumps(doc), r"\b[AB]=")
        self.assertIn(os.path.join(out, "send"), shown.getvalue())

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


class TestLedger(unittest.TestCase):
    def man(self, effects, dissolve_into=()):
        spans = [{"source": "s1", "in": 0, "out": 1} for _ in range(4)]
        for i in dissolve_into:
            spans[i]["transition"] = {"type": "dissolve", "frames": 2}
        return {"clips": [{"name": "m", "spans": spans}],
                "effects": [{"span_index": i, "effect": e} for e, i in effects]}

    def test_each_effect_is_drawn_approximated_or_not_drawn_with_its_spans(self):
        led = drafts.ledger(self.man([("zoom-push", 3), ("exposure-flash", 2), ("exposure-flash", 0),
                                      ("person-matte-text", 1), ("cross-dissolve-short", 2)],
                                     dissolve_into=(1, 3)))
        self.assertEqual(led["drawn"], [{"effect": "zoom-push", "spans": [3]}])
        self.assertEqual([(e["effect"], e["spans"]) for e in led["approximated"]],
                         [("dissolve", [1, 3]), ("exposure-flash", [0, 2])])
        self.assertEqual(led["approximated"][0]["note"], "drawn as a cut")
        self.assertIn("display-space", led["approximated"][1]["note"])
        self.assertEqual(led["not_drawn"],
                         [{"effect": "cross-dissolve-short", "spans": [2]},
                          {"effect": "person-matte-text", "spans": [1]}])

    def test_an_empty_plan_has_an_empty_ledger(self):
        self.assertEqual(drafts.ledger(self.man([])), {"drawn": [], "approximated": [],
                                                       "not_drawn": []})

    def test_every_difference_from_a_macro_is_in_approx(self):
        for e in ("exposure-flash", "dip-to-black", "crop-window", "scale-window", "dissolve"):
            self.assertIn(e, drafts.APPROX)
        self.assertTrue(set(drafts.APPROX) <= drafts.DRAWN | {"dissolve"})
        self.assertEqual(sorted(drafts.DRAWN - set(drafts.APPROX)), ["retime", "zoom-push"])

    def test_the_notes_name_an_effect_the_draft_cannot_draw(self):
        _, missing = drafts.span_filter({}, ["person-matte-text"], 1280, 720, 24, 1.0)
        self.assertEqual(missing, ["person-matte-text (no ffmpeg approximation)"])
        _, missing = drafts.span_filter({}, ["exposure-flash"], 1280, 720, 24, 1.0)
        self.assertTrue(missing[0].startswith("exposure-flash: a gain falling to 1"))


def flat_luma(grey, effects, seconds=1.0, fps=24, src_fps=24):
    """Mean luma (0 to 255) of each output frame of a flat grey frame of this level run
    through span_filter, read back as full-range grey: the levels a screen shows."""
    vf, _ = drafts.span_filter({}, effects, 320, 180, fps, seconds)
    n = round(seconds * fps)
    r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        f"color=c=0x{grey:02x}{grey:02x}{grey:02x}:s=320x180:r={src_fps}:d={seconds + 1}",
                        "-vf", vf + ",format=gray", "-frames:v", str(n), "-f", "rawvideo", "-"],
                       capture_output=True, check=True)
    size = 320 * 180
    return [sum(r.stdout[i * size:(i + 1) * size]) / size for i in range(n)]


def stops(x):
    """The dip-to-black macro's gain keys (1, 0.5, 0.25, 0.125, 0.06, 0.02, 0 every 3 frames)
    at x frames after the first, read straight off the .setting."""
    ys = [1, 0.5, 0.25, 0.125, 0.06, 0.02, 0]
    if x <= 0:
        return 1.0
    if x >= 18:
        return 0.0
    k = int(x // 3)
    return ys[k] + (ys[k + 1] - ys[k]) * (x - 3 * k) / 3


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class TestGainDraw(unittest.TestCase):
    """Trap T28: the macro's flash on a flat frame of luma 49 read 65 at gain 2 and 117 at
    gain 10 on its first frame. The old draw added 0.3 brightness and read about 119 for both."""

    def flash(self, gain, grey=49):
        return flat_luma(grey, [{"effect": "exposure-flash", "params": {"gain": gain}}])

    def test_gain_2_and_10_match_the_measured_macro_within_10_percent(self):
        for gain, measured in ((2, 65), (10, 117)):
            first = self.flash(gain)[0]
            self.assertAlmostEqual(first / measured, 1, delta=0.10, msg=f"gain {gain}: {first}")

    def test_the_default_is_gain_2_and_gain_1_is_no_flash(self):
        default = flat_luma(49, ["exposure-flash"])
        self.assertEqual(default, self.flash(2))
        self.assertEqual(self.flash(1), [49.0] * 24)

    def test_the_flash_settles_to_the_unflashed_level_by_frame_12(self):
        frames = self.flash(10)
        # eq rounds its table, so a level can wobble by one or two either way
        self.assertTrue(all(a >= b - 2 for a, b in zip(frames, frames[1:])), frames)
        for level in frames[12:]:
            self.assertAlmostEqual(level, 49, delta=1)
        self.assertGreater(frames[6], 49 + 5)

    def test_a_bright_frame_clips_at_white_and_a_dark_one_lifts_more(self):
        self.assertEqual(self.flash(10, grey=200)[0], 255)
        self.assertGreater(self.flash(10)[0] - 49, self.flash(2)[0] - 49)

    def test_the_dip_follows_the_even_stop_keys_and_ends_black(self):
        n, grey = 30, 200
        levels = flat_luma(grey, ["dip-to-black"], seconds=n / 24)
        for j, level in enumerate(levels):
            want = min(255, grey * stops(j - (n - 19)) ** (1 / drafts.GAMMA))
            self.assertAlmostEqual(level, want, delta=5, msg=f"frame {j}")
        self.assertEqual(levels[n - 19], grey)  # the keys start 18 frames before the last
        self.assertLess(levels[-1], 2)
        # a straight fade to black reads 100 at its midpoint; the macro's keys read about 88
        self.assertLess(levels[n - 10], 0.5 * grey)

    def test_the_dip_lands_on_the_output_frames_of_a_faster_source(self):
        self.assertEqual(flat_luma(200, ["dip-to-black"], seconds=1.0, src_fps=60)[-1], 0)


def gray_frame(path, n, w, h):
    """Frame n of a draft as w*h grey bytes."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf",
                        f"select=eq(n\\,{n}),format=gray", "-frames:v", "1", "-f", "rawvideo", "-"],
                       capture_output=True, check=True)
    return r.stdout


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class TestMotionRender(Base):
    def setUp(self):
        super().setUp()
        self.src = os.path.join(self.dir, "grey.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        "color=c=0xb0b0b0:s=1920x1080:r=24:d=8", "-pix_fmt", "yuv420p",
                        "-c:v", "libx264", "-preset", "ultrafast", self.src], check=True)
        m = copy.deepcopy(METHOD)
        m.update(look=None, length_s=3.0)
        m["rules"]["end"] = None
        for slot, (sec, eff) in zip(m["slots"], [(1.5, "crop-window"), (0.5, None),
                                                (1.0, "scale-window")]):
            slot.update(seconds=sec, effect=eff, scale=None, move="static")
        d = self.plan("motion", m, self.src)
        self.draft = drafts.build([d], os.path.join(self.dir, "out"), hw=False,
                                  lanes=1)["drafts"][0]["file"]

    def test_crop_window_box_changes_size_over_the_span(self):
        def lit_columns(n):  # columns of the middle row that are not black
            row = gray_frame(self.draft, n, 1280, 720)[360 * 1280:361 * 1280]
            return sum(1 for v in row if v > 40)
        early, late = lit_columns(4), lit_columns(20)  # frame 4 is opening, 20 is held
        self.assertGreater(early, 0)
        self.assertGreater(late, early + 100)

    def test_scale_window_grows_over_the_previous_shot(self):
        f = gray_frame(self.draft, 48 + 1, 1280, 720)  # span 2 starts at frame 48; frame 1: a small window
        corner = [f[y * 1280 + x] for y in range(0, 100) for x in range(0, 100)]
        self.assertGreater(sum(corner) / len(corner), 100)  # the grey shot, not black


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
