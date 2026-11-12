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
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rpresolve import drafts, methods, windowplate  # noqa: E402

DraftErrorAlias = drafts.DraftError

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
            self.assertIn("span 2: scale-window: over the previous shot playing on", notes)
            self.assertIn("span 1: crop-window: over the previous shot playing on", notes)
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
        m["slots"][1]["effect"] = "cross-dissolve-short"
        m["slots"][1]["text"] = None
        return [self.plan("plain", METHOD, self.src), self.plan("matted", m, self.src)]

    def test_the_ledger_is_in_drafts_json_per_draft(self):
        plans = self.two_lettered_plans()
        out = os.path.join(self.dir, "out")
        drafts.build(plans, out, hw=False, lanes=2)
        with open(os.path.join(out, "drafts.json")) as f:
            saved = json.load(f)["drafts"]
        by_plan = {d["plan"]: d["effects"] for d in saved}
        plain, matted = by_plan["plain"], by_plan["matted"]
        # both windows follow another span, so each opens over the previous shot playing on: the
        # ledger's note is the one the draft's own notes give for that span
        for win, span in (("scale-window", 2), ("crop-window", 1)):
            window = next(e for e in plain["approximated"] if e["effect"] == win)
            self.assertEqual(window["note"], drafts.WINDOW_OVER[win])
            self.assertIn(f"span {span}: {win}: " + window["note"],
                          next(d for d in saved if d["plan"] == "plain")["notes"])
        self.assertEqual(plain["drawn"], [{"effect": "zoom-push", "spans": [0]}])
        self.assertEqual([(e["effect"], e["spans"]) for e in plain["approximated"]],
                         [("crop-window", [1]), ("dissolve", [1]), ("scale-window", [2])])
        self.assertEqual(plain["not_drawn"], [])
        self.assertEqual(matted["not_drawn"], [{"effect": "cross-dissolve-short", "spans": [1]}])
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
            self.assertEqual(any("cross-dissolve-short (no ffmpeg approximation)" in x for x in rest),
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
        doc = drafts.build([a], os.path.join(self.dir, "The operator's out, take: 2"), camera="Cam",
                           lut_root=os.path.dirname(root), hw=False, lanes=1)
        self.assertTrue(doc["drafts"][0]["lut"].endswith("RP Print Teal [Cam].cube"))
        self.assertFalse(any("not applied" in n for n in doc["drafts"][0]["notes"]))
        self.assertIsNone(doc["sheet"])
        self.assertIsNone(doc["send"])  # one draft has no letter to send under
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(doc["drafts"][0]["file"]),
                                                     "send")))
        self.assertEqual(int(probe(doc["drafts"][0]["file"])["nb_read_frames"]), 60)

    def test_a_fill_past_the_source_end_is_refused(self):
        short = os.path.join(self.dir, "short.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=s=640x360:r=24:d=3.5", "-pix_fmt", "yuv420p",
                        "-c:v", "libx264", "-preset", "ultrafast", short], check=True)
        a = self.plan("short", METHOD, short)  # the third slot runs to 3.75 s of a 3.5 s clip
        with self.assertRaisesRegex(drafts.DraftError, "past the end of its source"):
            drafts.build([a], os.path.join(self.dir, "out"), hw=False, lanes=1)


class TestSendNotes(Base):
    """DIR/send opens each letter's notes with a count: 'everything drawn', '1 note', 'N notes'."""

    def test_the_header_is_everything_drawn_one_note_or_a_count(self):
        lettered = []
        for i, notes in enumerate(([], ["span 1: the dissolve into it is drawn as a cut"],
                                   ["a", "b", "c"])):
            f = os.path.join(self.dir, f"d{i}.mp4")
            Path(f).write_bytes(bytes([i]))
            lettered.append({"file": f, "notes": notes})
        labels = {"A": {"input_index": 2}, "B": {"input_index": 0}, "C": {"input_index": 1}}
        sent = drafts._send(os.path.join(self.dir, "send"), labels, lettered)
        self.assertEqual(sent["files"], ["A.mp4", "A.notes.txt", "B.mp4", "B.notes.txt",
                                         "C.mp4", "C.notes.txt"])
        heads = {}
        for letter in labels:
            lines = Path(sent["dir"], letter + ".notes.txt").read_text().splitlines()
            heads[letter] = lines[0]
            self.assertEqual(lines[1:], lettered[labels[letter]["input_index"]]["notes"])
            self.assertEqual(Path(sent["dir"], letter + ".mp4").read_bytes(),
                             bytes([labels[letter]["input_index"]]))
        self.assertEqual(heads, {"A": "3 notes", "B": "everything drawn", "C": "1 note"})


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
        self.assertIsNone(drafts.effect_filter("cross-dissolve-short", 1.0, 24, 1280, 720))


class TestExclude(Base):
    def listed(self, doc):
        p = os.path.join(self.dir, "exclude.json")
        Path(p).write_text(json.dumps(doc))
        return p

    def refused(self, take, src="/w/day1/cam-a/Clip0042.mov"):
        plan = self.plan("p" + str(len(os.listdir(os.path.join(self.dir, "plans")))), METHOD, src)
        out = os.path.join(self.dir, "out")
        with mock.patch.object(drafts, "run_lanes", side_effect=AssertionError("ffmpeg ran")):
            with self.assertRaisesRegex(drafts.DraftError, r"p\d span 0: Clip0042.mov is excluded"):
                drafts.build([plan], out, hw=False, exclude=self.listed({"takes": [take]}))

    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.dir, "plans"))

    def test_a_stem_a_basename_and_a_path_suffix_refuse_before_any_job(self):
        for take in ("clip0042", "CLIP0042.MOV", "cam-a/Clip0042.mov", "day1/cam-a/clip0042.mov"):
            self.refused(take)

    def test_the_music_file_is_checked_too(self):
        plan = self.plan("p", METHOD, "/w/Clip0042.mov")
        path = os.path.join(plan, "manifest.json")
        m = json.loads(Path(path).read_text())
        Path(path).write_text(json.dumps({**m, "music": {"path": "/w/audio/Song.m4a", "in": 1.0}}))
        with mock.patch.object(drafts, "run_lanes", side_effect=AssertionError("ffmpeg ran")):
            with self.assertRaisesRegex(drafts.DraftError, r"p music: Song\.m4a is excluded \(audio/song\)"):
                drafts.build([plan], os.path.join(self.dir, "out"), hw=False,
                             exclude=self.listed({"takes": ["audio/song"]}))

    def test_a_file_without_takes_is_refused(self):
        plan = self.plan("p", METHOD, "/w/Clip0042.mov")
        for doc in ({"shots": ["s1"]}, {"take": ["Clip0042"]}, {"takes": "Clip0042"}, []):
            with self.assertRaisesRegex(drafts.DraftError, "`takes`"):
                drafts.build([plan], os.path.join(self.dir, "out"), hw=False,
                             exclude=self.listed(doc))

    @unittest.skipUnless(FFMPEG, "needs ffmpeg")
    def test_a_clean_manifest_passes_and_the_notes_say_whether_a_list_was_given(self):
        src = os.path.join(self.dir, "src.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=s=640x360:r=24:d=8", "-pix_fmt", "yuv420p",
                        "-c:v", "libx264", "-preset", "ultrafast", src], check=True)
        plan = self.plan("p", METHOD, src)
        none = drafts.build([plan], os.path.join(self.dir, "a"), hw=False, lanes=1)
        self.assertIn("no exclude list given", none["drafts"][0]["notes"])
        ex = self.listed({"takes": ["other-take"], "shots": ["s1"]})
        some = drafts.build([plan], os.path.join(self.dir, "b"), hw=False, lanes=1, exclude=ex)
        notes = some["drafts"][0]["notes"]
        self.assertNotIn("no exclude list given", notes)
        self.assertIn("not checked here: shot ids belong to the planner's index", notes)


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class TestCrops(Base):
    """A 9:16 draft cropped at each span's own centre from a crops file. The source is three
    vertical bands, red | lime | blue, each a third of the width, so the window's centre says
    which band it was cut from (the middle crop is lime)."""

    def setUp(self):
        super().setUp()
        self.src = os.path.join(self.dir, "bands.mp4")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        "color=c=red:s=640x1080:r=24:d=4", "-f", "lavfi", "-i",
                        "color=c=lime:s=640x1080:r=24:d=4", "-f", "lavfi", "-i",
                        "color=c=blue:s=640x1080:r=24:d=4", "-filter_complex", "hstack=inputs=3",
                        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
                        "-crf", "8", self.src], check=True)

    def method(self, *effects):
        m = copy.deepcopy(METHOD)
        m.update(look=None, length_s=0.5 * len(effects), aspect="9:16")
        m["rules"]["end"] = None
        m["slots"] = [{**METHOD["slots"][0], "seconds": 0.5, "effect": e, "scale": None,
                       "move": "static", "transition_out": {"type": "cut", "frames": 0},
                       "text": None} for e in effects]
        return m

    def make(self, name, m, aspect=None):
        return self.plan(name, m, self.src, aspect)

    def crops(self, cxs, path=None, **kw):
        doc = {"aspect": "9x16", "reviewed": False,
               "items": [{"index": n, "name": f"i{n}", "path": path or self.src, "in": 0, "out": 1,
                          "cx": cx, "zoom": 1.0, "source_size": [1920, 1080]}
                         for n, cx in enumerate(cxs, 1)]}
        p = os.path.join(self.dir, "crops.json")
        Path(p).write_text(json.dumps(doc))
        return p

    def rgb(self, video, n, x=360, y=640):
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", video, "-vf",
                            f"select=eq(n\\,{n}),crop=2:2:{x}:{y},format=rgb24", "-frames:v", "1",
                            "-f", "rawvideo", "-"], capture_output=True, check=True)
        return tuple(r.stdout[:3])

    def band(self, video, n, **kw):
        r, g, b = self.rgb(video, n, **kw)
        return "red" if r > 200 > g else "lime" if g > 200 > r else "blue" if b > 200 > r else (r, g, b)

    def test_each_span_is_cropped_at_its_own_centre(self):
        plan = self.make("tall", self.method(None, None, None))
        none = drafts.build([plan], os.path.join(self.dir, "a"), hw=False, lanes=1)["drafts"][0]
        self.assertEqual([self.band(none["file"], n) for n in (3, 15, 27)], ["lime"] * 3)
        self.assertNotIn("crops", none)
        got = drafts.build([plan], os.path.join(self.dir, "b"), hw=False, lanes=1,
                           crops=self.crops([0.17, 0.5, 0.83]))["drafts"][0]
        self.assertEqual([self.band(got["file"], n) for n in (3, 15, 27)], ["red", "lime", "blue"])
        self.assertEqual(got["crops"], [{"span": 0, "cx": 0.17, "used": True},
                                        {"span": 1, "cx": 0.5, "used": True},
                                        {"span": 2, "cx": 0.83, "used": True}])
        self.assertIn("9:16 crops from the crops file: span 0 at centre 0.1700; "
                      "span 1 at centre 0.5000; span 2 at centre 0.8300", got["notes"])

    def test_the_notes_group_spans_that_share_a_centre(self):
        plan = self.make("tall", self.method(None, None, None))
        got = drafts.build([plan], os.path.join(self.dir, "a"), hw=False, lanes=1,
                           crops=self.crops([0.17, 0.17, 0.83]))["drafts"][0]
        self.assertIn("9:16 crops from the crops file: spans 0-1 at centre 0.1700; "
                      "span 2 at centre 0.8300", got["notes"])

    def test_a_window_plate_is_framed_with_the_crop_of_the_shot_it_continues(self):
        plan = self.make("tall", self.method(None, "scale-window"))
        got = drafts.build([plan], os.path.join(self.dir, "a"), hw=False, lanes=1,
                           crops=self.crops([0.17, 0.83]))["drafts"][0]
        # span 1 starts at frame 12: its window (blue) is small and centred, and the corner is
        # the plate, the red shot playing on at span 0's crop, where a centre crop is lime
        self.assertEqual(self.band(got["file"], 12), "blue")
        self.assertEqual(self.band(got["file"], 12, x=5, y=5), "red")

    def test_a_reframe_pan_keeps_its_own_crop_and_the_notes_say_so(self):
        plan = self.make("tall", self.method(None, None))
        mp = os.path.join(plan, "manifest.json")
        man = json.loads(Path(mp).read_text())
        man["effects"] = [{"effect": "reframe-pan", "span_index": 1,
                           "params": {"pan_from": 0.0, "pan_to": 0.0}}]
        Path(mp).write_text(json.dumps(man))
        got = drafts.build([plan], os.path.join(self.dir, "a"), hw=False, lanes=1,
                           crops=self.crops([0.17, 0.83]))["drafts"][0]
        self.assertEqual(self.band(got["file"], 15), "red")  # pan_from 0 is the left edge
        self.assertIn("span 1: reframe-pan sets the crop; the crops file's centre is not used",
                      got["notes"])
        self.assertEqual([c["used"] for c in got["crops"]], [True, False])

    def test_a_crops_file_that_does_not_fit_the_plan_is_refused(self):
        plan = self.make("tall", self.method(None, None, None))
        out = os.path.join(self.dir, "a")
        with mock.patch.object(drafts, "run_lanes", side_effect=AssertionError("ffmpeg ran")):
            with self.assertRaisesRegex(DraftErrorAlias, r"tall: the crops file has 2 item\(s\), "
                                                         r"the plan 3 span\(s\)"):
                drafts.build([plan], out, hw=False, crops=self.crops([0.2, 0.8]))
            with self.assertRaisesRegex(DraftErrorAlias, r"tall span 1: .*item 2 is another file "
                                                         r"\(other.mp4\)"):
                crops = self.crops([0.2, 0.5, 0.8])
                doc = json.loads(Path(crops).read_text())
                doc["items"][1]["path"] = "/elsewhere/other.mp4"
                Path(crops).write_text(json.dumps(doc))
                drafts.build([plan], out, hw=False, crops=crops)
            bad = os.path.join(self.dir, "bad.json")
            Path(bad).write_text("{}")
            with self.assertRaisesRegex(DraftErrorAlias, "9x16 crops.json"):
                drafts.build([plan], out, hw=False, crops=bad)

    def test_a_16_9_plan_ignores_the_crops_file_and_says_so(self):
        wide = self.make("wide", self.method(None, None), aspect="16:9")
        got = drafts.build([wide], os.path.join(self.dir, "a"), hw=False, lanes=1,
                           crops=self.crops([0.2]))["drafts"][0]  # one item: never counted
        self.assertIn("the crops file is not used: the method is 16:9", got["notes"])
        self.assertNotIn("crops", got)


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
        self.assertIn("crop=1280:720:x='clip((0.6+(0.3-0.6)*t/1.0000)*iw-ow/2\\,0\\,iw-ow)'", vf)
        self.assertEqual(vf.count("crop=1280:720"), 1)
        self.assertEqual(missing, [])  # the centre follows the macro's, so nothing is approximate

    def test_a_static_pan_crops_where_centre_crop_does(self):
        vf, _ = self.vf([{"effect": "reframe-pan", "params": {"pan_from": 0.37, "pan_to": 0.37}}])
        x = vf.split("x='")[1].split("':y=")[0]
        want = drafts.centre_crop(1280, 720, 0.37).split("x='")[1].split("':y=")[0]
        self.assertEqual(x.replace("(0.37+(0.37-0.37)*t/1.0000)", "0.3700"), want)

    def test_scale_window_grows_to_full_and_is_labelled(self):
        vf, missing = self.vf(["scale-window"])
        self.assertIn("0.22+0.5*min(t*24\\,36)/36", vf)
        self.assertIn("pad=1280:720", vf)
        self.assertTrue(missing[0].startswith("scale-window: drawn over black"))

    BACK = "scale=1280:720,fps=24,format=yuv420p"

    def test_a_window_opens_over_the_previous_shot_playing_on_not_a_held_frame(self):
        vf, missing = drafts.span_filter({}, ["scale-window"], 1280, 720, 24, 2.0, back=self.BACK)
        self.assertTrue(vf.startswith("[0:v]"), vf)
        self.assertIn("overlay=", vf)
        self.assertIn(f"[1:v]{self.BACK},fps=24:start_time=0,trim=end_frame=37,"
                      "tpad=stop_mode=clone:stop=13[bg]", vf)
        self.assertTrue(vf.startswith("[0:v]fps=24:start_time=0,"), vf)  # the window on the grid too
        self.assertNotIn("loop", vf)  # the old plate looped one frame
        self.assertNotIn("end_frame=1,", vf)
        self.assertEqual(missing, ["scale-window: " + drafts.WINDOW_OVER["scale-window"]])
        self.assertTrue(missing[0].startswith("scale-window: over the previous shot playing on"))
        vf, missing = drafts.span_filter({}, ["crop-window"], 1280, 720, 24, 2.0, back=self.BACK)
        self.assertIn("trim=end_frame=26,tpad=stop_mode=clone:stop=24[bg]", vf)
        self.assertIn("[fg][bg]blend=all_expr='if(gt(abs(X-W/2),W*(", vf)
        self.assertNotIn("geq", vf)  # outside the box is the plate, not black
        self.assertEqual(missing, ["crop-window: " + drafts.WINDOW_OVER["crop-window"]])
        self.assertTrue(missing[0].startswith("crop-window: over the previous shot playing on"))

    def test_a_span_shorter_than_its_window_gets_only_the_frames_it_shows(self):
        vf, _ = drafts.span_filter({}, ["scale-window"], 1280, 720, 24, 10 / 24, back=self.BACK)
        self.assertIn("trim=end_frame=10,tpad=stop_mode=clone:stop=2[bg]", vf)

    def test_with_no_previous_shot_a_window_opens_from_black_and_says_so(self):
        vf, missing = drafts.span_filter({}, ["crop-window"], 1280, 720, 24, 2.0)
        self.assertIn("geq=", vf)
        self.assertEqual(missing, ["crop-window: " + drafts.APPROX["crop-window"]])
        self.assertTrue(missing[0].startswith("crop-window: opens from black"))
        _, missing = drafts.span_filter({}, ["scale-window"], 1280, 720, 24, 2.0)
        self.assertEqual(missing, ["scale-window: " + drafts.APPROX["scale-window"]])
        self.assertTrue(missing[0].startswith("scale-window: drawn over black"))

    def test_the_plate_is_read_from_the_previous_out_point_not_its_last_frame(self):
        plan = {"name": "p", "method": {**METHOD, "look": None}, "manifest": methods.to_manifest(
            METHOD, [{"source_path": "/none/a.mp4", "in": 0.5, "out": 1.5},
                     {"source_path": "/none/a.mp4", "in": 6.0, "out": 6.5},
                     {"source_path": "/none/a.mp4", "in": 7.0, "out": 7.75}])}
        jobs, _ = drafts.plan_jobs(plan, "/w", False)
        cmd = jobs[1]["cmd"]  # span 1 is the crop-window
        inputs = [cmd[k + 1] for k, x in enumerate(cmd) if x == "-ss"]
        self.assertEqual(inputs, ["6.0000", "1.5000"])  # its own in point, then the previous out
        self.assertEqual((jobs[0]["cmd"].count("-i"), jobs[1]["cmd"].count("-i")), (1, 2))

    def test_the_plate_goes_through_the_previous_spans_scale_and_look_not_the_windows(self):
        with tempfile.TemporaryDirectory() as work:
            cube = os.path.join(work, "src.cube")
            Path(cube).write_text("")
            manifest = methods.to_manifest(
                METHOD, [{"source_path": "/none/a.mp4", "in": 0.5, "out": 1.5},
                         {"source_path": "/none/a.mp4", "in": 6.0, "out": 6.5},
                         {"source_path": "/none/a.mp4", "in": 7.0, "out": 7.75}])
            spans = manifest["clips"][0]["spans"]
            spans[0]["scale"] = 1.3  # the outgoing shot's; span 1 (the window) carries 1.2
            self.assertEqual(spans[1]["scale"], 1.2)
            with mock.patch.object(drafts, "find_lut", lambda *a, **k: cube):
                jobs, _ = drafts.plan_jobs({"name": "p", "method": METHOD, "manifest": manifest},
                                           work, False, camera="cam")
        cmd = jobs[1]["cmd"]
        mix, plate = cmd[cmd.index("-filter_complex") + 1].split(";")[:2]
        self.assertTrue(plate.startswith("[1:v]"), plate)
        self.assertIn("scale=trunc(1280*1.3/2)*2", plate)  # item 1's scale
        self.assertNotIn("1.2", plate)  # not the window span's own
        self.assertIn("lut3d=file=look.cube", plate)
        self.assertIn("scale=trunc(1280*1.2/2)*2", mix)  # the span itself keeps its own
        self.assertIn("lut3d=file=look.cube", mix)

    def test_the_plate_check_allows_a_half_frame_and_no_more(self):
        def length(frames):
            return mock.patch.object(drafts.subprocess, "run", return_value=mock.Mock(
                stdout=f"{3.0 + frames / 24:.6f}\n"))
        for frames in (37, 36.6):  # exactly enough; a half frame short is rounding
            with length(frames):
                drafts._check_plate("p.mp4", 3.0, 37, 24, "x")
        for frames in (36, 10):
            with length(frames), self.assertRaisesRegex(DraftErrorAlias, "x: the previous shot has"):
                drafts._check_plate("p.mp4", 3.0, 37, 24, "x")
        with mock.patch.object(drafts.subprocess, "run", return_value=mock.Mock(stdout="")):
            drafts._check_plate("p.mp4", 3.0, 37, 24, "x")  # a length ffprobe cannot read passes


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
        self.assertNotIn("span 1: reframe-pan:", notes)  # drawn at the macro's centre
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
                         [("dissolve", [1, 3]), ("exposure-flash", [0, 2]),
                          ("person-matte-text", [1])])
        self.assertEqual(led["approximated"][0]["note"], "drawn as a cut")
        self.assertIn("each channel clipped at white", led["approximated"][1]["note"])
        self.assertEqual(led["not_drawn"], [{"effect": "cross-dissolve-short", "spans": [2]}])

    def test_a_window_is_from_black_on_span_0_and_over_the_previous_shot_playing_on_after_it(self):
        self.assertEqual(set(drafts.WINDOW_OVER), set(windowplate.PLATE_FRAMES))
        for win, extra in (("scale-window", drafts.WINDOW_DRIFT), ("crop-window", drafts.BOX_RATE)):
            led = drafts.ledger(self.man([(win, 0), (win, 2)]))
            rows = {tuple(e["spans"]): e["note"] for e in led["approximated"]}
            self.assertEqual(rows, {(0,): drafts.APPROX[win], (2,): drafts.WINDOW_OVER[win]})
            self.assertNotEqual(rows[(0,)], rows[(2,)])
            self.assertRegex(rows[(0,)], r"over black|from black")
            self.assertIn("previous shot playing on", rows[(2,)])
            for note in rows.values():
                self.assertIn(extra, note)
                self.assertNotRegex(note, r"held|last frame")
            # a lone window keeps one row, whichever side it is on
            self.assertEqual([e["spans"] for e in drafts.ledger(
                self.man([(win, 3)]))["approximated"]], [[3]])
            self.assertEqual([e["spans"] for e in drafts.ledger(
                self.man([(win, 0)]))["approximated"]], [[0]])

    def test_the_ledger_note_for_a_window_is_the_note_span_filter_gives(self):
        for win in windowplate.PLATE_FRAMES:
            _, first = drafts.span_filter({}, [win], 1280, 720, 24, 1.0)
            _, later = drafts.span_filter({}, [win], 1280, 720, 24, 1.0, back="null")
            led = drafts.ledger(self.man([(win, 0), (win, 2)]))
            self.assertEqual([f"{win}: " + e["note"] for e in led["approximated"]],
                             first + later)

    def test_an_empty_plan_has_an_empty_ledger(self):
        self.assertEqual(drafts.ledger(self.man([])), {"drawn": [], "approximated": [],
                                                       "not_drawn": []})

    def test_every_difference_from_a_macro_is_in_approx(self):
        for e in ("exposure-flash", "dip-to-black", "crop-window", "scale-window", "dissolve"):
            self.assertIn(e, drafts.APPROX)
        self.assertTrue(set(drafts.APPROX) <= drafts.DRAWN | {"dissolve"})
        self.assertEqual(sorted(drafts.DRAWN - set(drafts.APPROX)), ["reframe-pan", "retime", "zoom-push"])

    def test_approx_has_no_duplicate_keys(self):
        # a dict literal keeps the last of two equal keys without a word, so one note vanishes
        import ast
        tree = ast.parse(Path(drafts.__file__).read_text(encoding="utf-8"))
        lit = next(n.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
                   and any(getattr(t, "id", None) == "APPROX" for t in n.targets))
        keys = [k.value for k in lit.keys]
        self.assertEqual(sorted(keys), sorted(set(keys)))
        self.assertEqual(keys.count("crop-window"), 1)

    def test_the_notes_name_an_effect_the_draft_cannot_draw(self):
        _, missing = drafts.span_filter({}, ["cross-dissolve-short"], 1280, 720, 24, 1.0)
        self.assertEqual(missing, ["cross-dissolve-short (no ffmpeg approximation)"])
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


def flat_chroma(color, effects, seconds=1.0, fps=24):
    """Mean (U, V) of each output frame of a flat colour run through span_filter."""
    vf, _ = drafts.span_filter({}, effects, 320, 180, fps, seconds)
    n = round(seconds * fps)
    r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        f"color=c={color}:s=320x180:r={fps}:d={seconds + 1}",
                        "-vf", vf + ",format=yuv444p", "-frames:v", str(n), "-f", "rawvideo", "-"],
                       capture_output=True, check=True)
    size = 320 * 180
    out = []
    for i in range(n):
        base = i * 3 * size
        out.append((sum(r.stdout[base + size:base + 2 * size]) / size,
                    sum(r.stdout[base + 2 * size:base + 3 * size]) / size))
    return out


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


def flash_level(grey, gain):
    """The flash's first frame on a level: the macro's gamma 2.4 multiply about video black,
    clipped at white (trap T28). On full-range levels black is 0, so it is a plain multiply."""
    return min(255.0, grey * gain ** (1 / 2.4))


def flat_rgb(color, effects, n=1):
    """(R, G, B) of the first frame of a flat colour run through span_filter."""
    vf, _ = drafts.span_filter({}, effects, 320, 180, 24, 1.0)
    r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        f"color=c={color}:s=320x180:r=24:d=2",
                        "-vf", vf + ",format=rgb24", "-frames:v", "1", "-f", "rawvideo", "-"],
                       capture_output=True, check=True)
    return tuple(r.stdout[:3])


class TestFlashFilter(unittest.TestCase):
    def test_the_filter_is_a_per_channel_gamma_2_4_multiply_clipped_at_white(self):
        u = "min(round(T*24)/12,1)"
        for gain in (2, 10):
            k = f"pow(1+({gain}-1)*(1-({u})*({u})*(3-2*({u}))),0.416667)"
            want = ("format=gbrp,geq=" + ":".join(f"{c}='clip({c}(X,Y)*{k}+0.5,0,255)'"
                                                  for c in "gbr")
                    + ":enable='lt(round(t*24),12)'")
            got = drafts.effect_filter("exposure-flash", 1.0, 24, 320, 180, {"gain": gain})
            self.assertEqual(got, want)

    def test_the_default_gain_is_2_and_the_fall_follows_the_frame_rate(self):
        self.assertEqual(drafts.effect_filter("exposure-flash", 1.0, 24, 320, 180),
                         drafts.effect_filter("exposure-flash", 1.0, 24, 320, 180, {"gain": 2}))
        self.assertIn("round(T*30)/12", drafts.effect_filter("exposure-flash", 1.0, 30, 320, 180))


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class TestGainDraw(unittest.TestCase):
    """Trap T28: the build's flash is a gamma 2.4 multiply about video black that clips each
    channel at video white. The build read 81 at gain 2 and 141 or 142 at gain 10 on a dark
    shot (unflashed video level 64); the draft reads 80.4 and 141.4 on a flat frame of that level."""

    def flash(self, gain, grey=49):
        return flat_luma(grey, [{"effect": "exposure-flash", "params": {"gain": gain}}])

    def test_gain_2_and_10_match_the_measured_build_on_the_dark_shot(self):
        # a full-range level of 56 is the build's unflashed video level 64 (16 + 56 * 219 / 255)
        for gain, measured in ((2, 81), (10, 141.5)):
            video = 16 + self.flash(gain, grey=56)[0] * 219 / 255
            self.assertAlmostEqual(video, measured, delta=1.5, msg=f"gain {gain}: {video}")

    def test_a_gradient_follows_the_formula_and_clips_at_white(self):
        # a horizontal ramp, 0 to 255; the input's own 8-bit step (about 1.2 levels) is
        # multiplied by the gain, so the tolerance grows with it
        w, h = 320, 180
        ramp = bytes(min(255, x * 256 // w) for _ in range(h) for x in range(w) for _ in range(3))
        for gain in (2, 10):
            vf, _ = drafts.span_filter({}, [{"effect": "exposure-flash", "params": {"gain": gain}}],
                                       w, h, 24, 1.0)
            out = {}
            for name, chain in (("flash", vf), ("plain", "format=yuv420p")):
                r = subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                                    "-s", f"{w}x{h}", "-r", "24", "-i", "-", "-vf",
                                    chain + ",format=gray", "-frames:v", "1", "-f", "rawvideo", "-"],
                                   input=ramp * 30, capture_output=True, check=True)
                out[name] = list(r.stdout[:w])
            k = gain ** (1 / 2.4)
            for x in range(w):
                want = min(255.0, out["plain"][x] * k)
                self.assertAlmostEqual(out["flash"][x], want, delta=1.2 * k + 1.5,
                                       msg=f"gain {gain}, x {x}")
                if want >= 255.5 + 1.2 * k:  # well past white: exactly white, no roll-off
                    self.assertEqual(out["flash"][x], 255, msg=f"gain {gain}, x {x}")
            self.assertEqual(out["flash"][-1], 255)

    def test_each_channel_multiplies_and_clips_on_its_own(self):
        # luma alone would keep the ratio of the three; the build sends red and green to white
        # and leaves blue at 50 * 10 ** (1 / 2.4)
        got = flat_rgb("0xC86432", [{"effect": "exposure-flash", "params": {"gain": 10}}])
        self.assertGreaterEqual(min(got[:2]), 254)  # white, give or take the YCbCr round trip
        self.assertAlmostEqual(got[2], flash_level(50, 10), delta=3)
        got = flat_rgb("0xC86432", [{"effect": "exposure-flash", "params": {"gain": 2}}])
        self.assertGreaterEqual(got[0], 254)
        for c, v in zip(got[1:], (100, 50)):
            self.assertAlmostEqual(c, flash_level(v, 2), delta=3)

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

    def test_the_flash_falls_on_the_smoothstep_curve_not_a_line(self):
        # each frame reads as the formula at the macro's smoothstep gain for that frame
        g, grey = 10, 49
        frames = self.flash(g, grey)
        for j in (0, 1, 3, 6, 9, 11):
            u = j / 12
            gain = 1 + (g - 1) * (1 - u * u * (3 - 2 * u))
            self.assertAlmostEqual(frames[j], flash_level(grey, gain), delta=1.5, msg=f"frame {j}")
        # a straight fall would put frame 9 at gain 3.25, 9 levels above where the macro's is
        self.assertLess(frames[9], flash_level(grey, 1 + (g - 1) * 0.25) - 5)

    def test_the_flash_scales_chroma_with_the_levels(self):
        red = "0x602020"
        (_, v0), = flat_chroma(red, [], seconds=1 / 24)
        (_, v1), = flat_chroma(red, [{"effect": "exposure-flash", "params": {"gain": 10}}],
                               seconds=1 / 24)
        want = 10 ** (1 / drafts.FLASH_GAMMA)
        self.assertGreater(v0 - 128, 15)
        self.assertAlmostEqual((v1 - 128) / (v0 - 128), want, delta=0.15 * want)
        # and the chroma is back to the unflashed colour once the flash has settled
        settled = flat_chroma(red, [{"effect": "exposure-flash", "params": {"gain": 10}}])[-1]
        self.assertAlmostEqual(settled[1], v0, delta=1)

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

    def test_a_dip_on_a_span_shorter_than_its_keys_starts_partway_down_the_curve(self):
        n, grey = 12, 200  # the keys span 18 frames; 12 start at the key 7 frames in
        levels = flat_luma(grey, ["dip-to-black"], seconds=n / 24)
        self.assertEqual(len(levels), n)
        for j, level in enumerate(levels):
            want = grey * stops(j + 19 - n) ** (1 / drafts.GAMMA)
            self.assertAlmostEqual(level, want, delta=5, msg=f"frame {j}")
        self.assertLess(levels[0], 0.6 * grey)  # not the full-gain start of a long span
        self.assertLess(levels[-1], 2)

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


def matte_box(w=1280, h=720, **params):
    """person-matte-text's drawbox arguments, as ints and the colour string."""
    f = drafts.effect_filter("person-matte-text", 1.0, 24, w, h, params)
    assert f.startswith("drawbox="), f
    kv = dict(x.split("=", 1) for x in f[len("drawbox="):].split(":"))
    return {k: (int(v) if k in ("x", "y", "w", "h") else v) for k, v in kv.items()}


def macro_setting(name):
    """The numbers of a Fusion macro's Text1 from its .setting file."""
    text = (HERE.parent / "fusion-macros" / f"{name}.setting").read_text(encoding="utf-8")
    num = lambda key: float(re.search(rf"\b{key} = Input \{{ Value = ([0-9.]+) \}}", text).group(1))
    cx, cy = (float(v) for v in re.search(
        r"Center = Input \{ Value = \{ ([0-9.]+), ([0-9.]+) \}", text).groups())
    return {"center": (cx, cy), "size": num("Size"), "word": re.search(
        r'StyledText = Input \{ Value = "([^"]*)"', text).group(1),
        "rgb": tuple(round(num(k) * 255) for k in ("Red1", "Green1", "Blue1"))}


class TestMatteTitleFilter(unittest.TestCase):
    """person-matte-text is drawn as a plain title box in the macro's title position."""

    def box(self, w=1280, h=720, **params):
        return matte_box(w, h, **params)

    def test_the_box_is_centred_where_the_macro_puts_the_word(self):
        b = self.box(text="WILD")
        x, y, bw, bh = (b[k] for k in "xywh")
        self.assertLessEqual(abs(x + bw / 2 - 0.55 * 1280), 1)  # Center 0.55
        self.assertLessEqual(abs(y + bh / 2 - (1 - 0.55) * 720), 1)  # Fusion's y counts from the bottom
        self.assertEqual(bh, int(0.32 * 720 * 0.72))  # Size 0.32 of the frame height
        self.assertEqual(b["color"], "0xD7371F@0.85")  # the macro's red-orange, not text-word's white

    def test_the_numbers_come_from_the_macro_file(self):
        m = macro_setting("person-matte-text")
        self.assertEqual((m["size"], m["word"]), (drafts.MATTE_SIZE, drafts.MATTE_WORD))
        self.assertEqual(drafts.MATTE_COLOUR, "0x%02X%02X%02X" % m["rgb"])
        b = self.box(1280, 720)  # the stored word at the stored size
        cx, cy = m["center"]
        self.assertLessEqual(abs(b["x"] + b["w"] / 2 - cx * 1280), 1)
        self.assertLessEqual(abs(b["y"] + b["h"] / 2 - (1 - cy) * 720), 1)  # y counts from the bottom

    def test_the_box_follows_the_word_and_the_size_and_stays_in_the_frame(self):
        short, long_ = self.box(text="HI"), self.box(text="MOUNTAINEERING")
        self.assertGreater(long_["w"], short["w"])
        half = self.box(text="WILD", size=0.16)
        self.assertLess(half["h"], self.box(text="WILD")["h"])
        self.assertLess(half["w"], self.box(text="WILD")["w"])
        for w, h in ((1280, 720), (720, 1280)):
            b = self.box(w, h, text="MOUNTAINEERING")
            self.assertGreaterEqual(b["x"], 0)
            self.assertLessEqual(b["x"] + b["w"], w)
        self.assertEqual(self.box(), self.box(text="WILD"))  # no word: the macro's stored one

    def test_a_tiny_size_is_a_tiny_box_never_a_strip_to_the_frame_edge(self):
        # drawbox reads a side of 0 as "to the edge of the frame"
        for params in ({"size": 0.001}, {"size": 0.0005, "text": "a"}):
            b = self.box(**params)
            self.assertGreaterEqual(b["w"], 1, params)
            self.assertGreaterEqual(b["h"], 1, params)
            self.assertLess(b["w"] * b["h"], 100, params)
        b = self.box(size=9.0)  # and a huge one stays in the frame
        self.assertEqual((b["x"], b["w"], b["h"]), (0, 1280, 720))
        self.assertGreaterEqual(b["y"], 0)

    def test_beat_burst_at_9_16_is_a_full_width_bar_with_the_stored_word(self):
        m = methods.load("beat-burst")
        self.assertEqual(m["aspect"], "9:16")
        i = next(k for k, sl in enumerate(m["slots"]) if sl["effect"] == "person-matte-text")
        self.assertIsNone(m["slots"][i]["text"])
        man = methods.to_manifest(m, [{"source_path": f"/nowhere/s{k}.mov", "in": 0.0,
                                       "out": sl["seconds"]} for k, sl in enumerate(m["slots"])])
        w, h = drafts.SIZES[m["aspect"]]
        ent = next(e for e in man["effects"] if e["effect"] == "person-matte-text")
        self.assertNotIn("text", ent.get("params") or {})
        b = self.box(w, h, **(ent.get("params") or {}))
        # "WILD" at Size 0.32 of a 1280 high frame is wider than 720, as in the macro: full width
        self.assertEqual((b["x"], b["y"], b["w"], b["h"]), (0, 429, 720, 294))
        self.assertLessEqual(abs(b["y"] + b["h"] / 2 - 0.45 * h), 1)
        led = drafts.ledger(man)
        self.assertEqual([(e["effect"], e["spans"]) for e in led["approximated"]
                          if e["effect"] == "person-matte-text"], [("person-matte-text", [i])])
        self.assertEqual(led["not_drawn"], [])

    def test_it_is_drawn_with_a_note_that_the_title_is_in_front_and_the_matte_is_not(self):
        self.assertIn("person-matte-text", drafts.DRAWN)
        note = drafts.APPROX["person-matte-text"]
        self.assertIn("IN FRONT of the subject", note)
        self.assertIn("the matte is not drawn", note)
        _, missing = drafts.span_filter({}, [{"effect": "person-matte-text",
                                              "params": {"text": "HI"}}], 1280, 720, 24, 1.0)
        self.assertEqual(missing, ["person-matte-text: " + note])

    def test_the_ledger_lists_it_as_approximated_never_as_not_drawn(self):
        man = {"clips": [{"name": "m", "spans": [{"source": "s1", "in": 0, "out": 1}] * 2}],
               "effects": [{"span_index": 1, "effect": "person-matte-text",
                            "params": {"text": "HI"}}]}
        led = drafts.ledger(man)
        self.assertEqual(led["approximated"], [{"effect": "person-matte-text", "spans": [1],
                                                "note": drafts.APPROX["person-matte-text"]}])
        self.assertEqual((led["drawn"], led["not_drawn"]), ([], []))


def rgb_at(path, n, w, x, y):
    """The (r, g, b) of pixel (x, y) in frame n of a draft w pixels wide."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf",
                        f"select=eq(n\\,{n}),format=rgb24", "-frames:v", "1", "-f", "rawvideo", "-"],
                       capture_output=True, check=True)
    return tuple(r.stdout[(y * w + x) * 3:(y * w + x) * 3 + 3])


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class TestMatteTitleRender(Base):
    def setUp(self):
        super().setUp()
        self.src = os.path.join(self.dir, "grey.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        "color=c=0xb0b0b0:s=1920x1080:r=24:d=8", "-pix_fmt", "yuv420p",
                        "-c:v", "libx264", "-preset", "ultrafast", self.src], check=True)

    def matte_plan(self, name):
        m = copy.deepcopy(METHOD)
        m.update(look=None, length_s=2.0)
        m["rules"]["end"] = None
        m["slots"] = m["slots"][:2]
        m["slots"][0].update(effect=None, scale=None, seconds=1.0,
                             transition_out={"type": "cut", "frames": 0})
        m["slots"][1].update(effect="person-matte-text", text="word", scale=None, seconds=1.0)
        d = os.path.join(self.dir, "plans", name)
        os.makedirs(d)
        fills = [{"source_path": self.src, "in": 0.5, "out": 1.5},
                 {"source_path": self.src, "in": 2.5, "out": 3.5, "text": "HI"}]
        with open(os.path.join(d, "method.json"), "w") as f:
            json.dump(m, f)
        with open(os.path.join(d, "manifest.json"), "w") as f:
            json.dump(methods.to_manifest(m, fills), f)
        return d

    def build_matted(self):
        plans = [self.matte_plan("matted"), self.plan("plain", METHOD, self.src)]
        out = os.path.join(self.dir, "out")
        return out, drafts.build(plans, out, hw=False, lanes=2)

    def test_the_title_box_covers_the_picture_on_its_span_only_and_the_notes_say_so(self):
        out, doc = self.build_matted()
        d = next(x for x in doc["drafts"] if x["plan"] == "matted")
        box = matte_box(1280, 720, text="HI")  # the filter the draft ran, span 1 starts at frame 24
        cx, cy = box["x"] + box["w"] // 2, box["y"] + box["h"] // 2
        r, g, b = rgb_at(d["file"], 30, 1280, cx, cy)
        self.assertGreater(r - b, 100, (r, g, b))  # red-orange over the grey picture
        # the box edges: just inside is red-orange, a few pixels outside is the grey picture
        # (the margin is wider than yuv420p's chroma blur)
        for name, inside, outside in (
                ("left", (box["x"] + 6, cy), (box["x"] - 8, cy)),
                ("right", (box["x"] + box["w"] - 7, cy), (box["x"] + box["w"] + 8, cy)),
                ("top", (cx, box["y"] + 6), (cx, box["y"] - 8)),
                ("bottom", (cx, box["y"] + box["h"] - 7), (cx, box["y"] + box["h"] + 8))):
            r, g, b = rgb_at(d["file"], 30, 1280, *inside)
            self.assertGreater(r - b, 100, (name, "inside", (r, g, b)))
            r, g, b = rgb_at(d["file"], 30, 1280, *outside)
            self.assertLess(abs(r - b), 20, (name, "outside", (r, g, b)))
        r, g, b = rgb_at(d["file"], 30, 1280, 30, 30)
        self.assertLess(abs(r - b), 20, (r, g, b))  # the corner is still the plain picture
        r, g, b = rgb_at(d["file"], 6, 1280, cx, cy)  # span 0 has no title
        self.assertLess(abs(r - b), 20, (r, g, b))
        want = "span 1: person-matte-text: " + drafts.APPROX["person-matte-text"]
        self.assertIn(want, d["notes"])
        with open(os.path.join(out, "matted.notes.txt")) as f:
            self.assertIn(want, f.read().splitlines())
        self.assertEqual(d["effects"]["not_drawn"], [])
        self.assertEqual([(e["effect"], e["spans"]) for e in d["effects"]["approximated"]],
                         [("person-matte-text", [1])])

    def test_the_lettered_copy_of_the_draft_carries_the_matte_note(self):
        out, doc = self.build_matted()
        if not (doc["sheet"] or {}).get("dir"):
            self.skipTest("no sheet without Pillow, so no letters")
        want = "span 1: person-matte-text: " + drafts.APPROX["person-matte-text"]
        heads = {}
        for n in os.listdir(os.path.join(out, "send")):
            if n.endswith(".notes.txt"):
                with open(os.path.join(out, "send", n)) as f:
                    heads[n] = f.read().splitlines()
        self.assertEqual(sum(want in v for v in heads.values()), 1)


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class TestPlateRender(Base):
    """A source whose luma climbs 40 levels a second (about 2 grey levels a frame in the draft),
    so a held frame, or a plate that starts a few frames early or late, cannot pass."""

    def setUp(self):
        super().setUp()
        self.src = os.path.join(self.dir, "ramp.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        "color=c=black:s=1920x1080:r=24:d=10,"
                        "geq=lum='min(235,16+40*T)':cb=128:cr=128", "-pix_fmt", "yuv420p",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "8", self.src],
                       check=True)
        m = copy.deepcopy(METHOD)
        m.update(look=None, length_s=5.0)
        m["rules"]["end"] = None
        slots = [(1.0, None), (2.0, "scale-window"), (0.5, None), (1.5, "crop-window")]
        m["slots"] = [{**m["slots"][0], "seconds": sec, "effect": eff, "scale": None,
                       "move": "static", "transition_out": {"type": "cut", "frames": 0},
                       "text": None} for sec, eff in slots]
        # the windows' own shots are far on in the ramp (6 s, bright); the shots they
        # open over end at 1.5 s and 2.5 s (dark), where the plate carries on from
        fills = [{"source_path": self.src, "in": a, "out": a + sec}
                 for (sec, _), a in zip(slots, (0.5, 6.0, 2.0, 8.2))]
        d = os.path.join(self.dir, "plans", "ramp")
        os.makedirs(d)
        with open(os.path.join(d, "method.json"), "w") as f:
            json.dump(m, f)
        with open(os.path.join(d, "manifest.json"), "w") as f:
            json.dump(methods.to_manifest(m, fills), f)
        self.doc = drafts.build([d], os.path.join(self.dir, "out"), hw=False, lanes=1)["drafts"][0]
        self.draft = self.doc["file"]

    def corner(self, n):
        """Mean grey of a 60 px square in the top left corner of frame n (0 to 255, full range)."""
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", self.draft, "-vf",
                            f"select=eq(n\\,{n}),crop=60:60:0:0,format=gray", "-frames:v", "1",
                            "-f", "rawvideo", "-"], capture_output=True, check=True)
        return sum(r.stdout) / len(r.stdout)

    def plate_luma(self, seconds):
        """The grey the source holds at this time: 16 + 40 s in video range, to full range."""
        return (40 * seconds) * 255 / 219

    def test_scale_window_carries_on_from_the_previous_out_point(self):
        first = 24  # span 1 starts after span 0's 24 frames
        for j in (0, 12, 24, 30):
            self.assertAlmostEqual(self.corner(first + j), self.plate_luma(1.5 + j / 24), delta=2.5,
                                   msg=f"window frame {j}")
        # a held frame would put frame 30 within a level of frame 0
        self.assertGreater(self.corner(first + 30) - self.corner(first), 20)
        self.assertGreater(self.corner(first + 40), 120)  # full frame from frame 37: its own shot
        self.assertEqual(int(probe(self.draft)["nb_read_frames"]), 24 + 48 + 12 + 36)

    def test_crop_window_shows_the_previous_shot_playing_on_outside_its_box(self):
        first = 24 + 48 + 12
        for j in (0, 10, 20, 25):
            self.assertAlmostEqual(self.corner(first + j), self.plate_luma(2.5 + j / 24), delta=2.5,
                                   msg=f"window frame {j}")
        self.assertGreater(self.corner(first + 25) - self.corner(first), 15)
        self.assertGreater(self.corner(first + 30), 120)  # open to full frame from frame 26

    def test_the_notes_name_what_the_plate_leaves_out(self):
        notes = " | ".join(self.doc["notes"])
        self.assertIn("span 1: scale-window: over the previous shot playing on", notes)
        self.assertIn("span 3: crop-window: over the previous shot playing on", notes)

    def test_a_window_with_no_handle_on_the_previous_shot_is_refused(self):
        m = copy.deepcopy(METHOD)
        m.update(look=None, length_s=2.0)
        m["rules"]["end"] = None
        m["slots"] = [{**m["slots"][0], "seconds": 1.0, "effect": None, "scale": None,
                       "transition_out": {"type": "cut", "frames": 0}, "text": None},
                      {**m["slots"][0], "seconds": 1.0, "effect": "scale-window", "scale": None,
                       "transition_out": {"type": "cut", "frames": 0}, "text": None}]
        fills = [{"source_path": self.src, "in": 8.6, "out": 9.8},  # about 0.4 s of the file is left
                 {"source_path": self.src, "in": 2.0, "out": 3.0}]
        d = os.path.join(self.dir, "plans", "short")
        os.makedirs(d)
        with open(os.path.join(d, "method.json"), "w") as f:
            json.dump(m, f)
        with open(os.path.join(d, "manifest.json"), "w") as f:
            json.dump(methods.to_manifest(m, fills), f)
        with self.assertRaisesRegex(DraftErrorAlias, r"short span 1: the previous shot has 0\.4\d s"):
            drafts.build([d], os.path.join(self.dir, "out2"), hw=False, lanes=1)


def late_by(t, late):
    """The 24 fps frame time nearest t whose first 29.97 fps source frame at or after it
    comes more than 22 ms late (past half a draw frame: it rounds to the next one) or under
    10 ms late. -ss lands that far into a source
    frame, so this sets which layer of a window starts a part of a frame behind."""
    step = 1001 / 30000
    k = round(t * 24)
    for d in range(0, 12):
        for c in (k + d, k - d):
            gap = -(-(c / 24) // step) * step - c / 24 + 1e-9  # to the next source frame
            if (gap > 0.022) if late else (gap < 0.01):
                return c / 24
    raise AssertionError("no frame time near %s" % t)


class Frames:
    """Reads a draft's decoded frames (self.raw, self.size, self.W, self.H) by colour."""

    def colour(self, n, x, y):
        i = n * self.size + (y * self.W + x) * 3
        r, g, b = self.raw[i:i + 3]
        for name, hit in (("R", r > 150 and g < 100 and b < 100),
                          ("G", g > 90 and r < 100 and b < 100),
                          ("B", b > 150 and r < 100 and g < 100)):
            if hit:
                return name
        return "?"

    def red_width(self, n):
        return sum(self.colour(n, x, self.H // 2) == "R" for x in range(self.W))


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class TestWindowLayerTiming(Frames, Base):
    """A 29.97 fps source drawn at 24 fps, one colour per shot, so each draft frame says which
    layer is on screen and how big the window is. The window layer must be there from frame
    0 and reach full frame on the macro's frame (37 for a scale-window, 26 for a crop-window),
    whichever part of a source frame -ss lands on (T32)."""

    W, H = 1280, 720
    SPANS = [(1.0, None), (1.75, "scale-window"), (1.5, "crop-window"), (1.5, "crop-window")]
    START = [0, 24, 66, 102]

    def setUp(self):
        super().setUp()
        self.src = os.path.join(self.dir, "colours.mp4")
        segs = [f"color=c={c}:s=640x360:r=30000/1001:d=5" for c in ("green", "blue", "red")]
        subprocess.run(["ffmpeg", "-v", "error"] + sum((["-f", "lavfi", "-i", x] for x in segs), [])
                       + ["-filter_complex", "concat=n=3:v=1:a=0", "-pix_fmt", "yuv420p",
                          "-c:v", "libx264", "-preset", "ultrafast", "-crf", "8", self.src],
                       check=True)
        m = copy.deepcopy(METHOD)
        m.update(look=None, length_s=sum(sec for sec, _ in self.SPANS))
        m["rules"]["end"] = None
        m["slots"] = [{**m["slots"][0], "seconds": sec, "effect": eff, "scale": None,
                       "move": "static", "transition_out": {"type": "cut", "frames": 0},
                       "text": None} for sec, eff in self.SPANS]
        # green, then red under a scale-window, then blue under a crop-window, then red under
        # a crop-window: each window opens over the shot before it playing on. The layer that
        # is the later one of each pair starts a part of a source frame behind.
        out0 = late_by(1.5, False)  # span 1's plate: on time; its red window: late
        in1 = late_by(10.5, True)
        out2 = late_by(7.0, True)  # span 3's plate: late; its red window: on time
        in3 = late_by(13.0, False)
        ins = [out0 - 1.0, in1, out2 - 1.5, in3]
        fills = [{"source_path": self.src, "in": a, "out": a + sec}
                 for (sec, _), a in zip(self.SPANS, ins)]
        d = os.path.join(self.dir, "plans", "timing")
        os.makedirs(d)
        with open(os.path.join(d, "method.json"), "w") as f:
            json.dump(m, f)
        with open(os.path.join(d, "manifest.json"), "w") as f:
            json.dump(methods.to_manifest(m, fills), f)
        doc = drafts.build([d], os.path.join(self.dir, "out"), hw=False, lanes=1)["drafts"][0]
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", doc["file"], "-f", "rawvideo",
                              "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        self.size = self.W * self.H * 3
        self.raw = raw
        self.assertEqual(len(raw) // self.size, 24 + 42 + 36 + 36)

    def test_a_scale_window_is_on_screen_from_its_first_frame(self):
        first = self.START[1]
        for j, z in ((0, 0.22), (1, 0.22 + 0.5 / 36), (2, 0.22 + 1.0 / 36), (36, 0.72)):
            self.assertAlmostEqual(self.red_width(first + j), self.W * z, delta=8,
                                   msg=f"window frame {j}")
        self.assertEqual(self.colour(first, 5, 5), "G")  # the plate, not black

    def test_a_scale_window_snaps_to_full_frame_on_the_macros_frame_37(self):
        first = self.START[1]
        self.assertEqual(self.colour(first + 36, 5, 5), "G")
        for j in (37, 38):
            self.assertEqual(self.colour(first + j, 5, 5), "R", msg=f"frame {j}")
            self.assertGreater(self.red_width(first + j), self.W - 10)

    def test_a_crop_window_after_a_crop_window_opens_on_the_plate_not_full_frame(self):
        first = self.START[3]  # a red box over the blue shot playing on
        self.assertEqual(self.colour(first, 5, 5), "B")
        self.assertEqual(self.colour(first, self.W // 4, self.H // 2), "B")
        self.assertEqual(self.colour(first + 8, self.W // 2, self.H // 2), "R")
        self.assertEqual(self.colour(first + 8, self.W // 4, self.H // 2), "B")

    def test_a_crop_window_is_full_frame_on_the_macros_frame_26(self):
        for first in (self.START[2], self.START[3]):
            self.assertEqual(self.colour(first + 25, 5, 5), self.colour(first, 5, 5))  # a sliver of plate
            for j in (26, 27):
                self.assertEqual(self.colour(first + j, 5, 5), self.colour(first + j, 640, 360),
                                 msg=f"span at {first}, frame {j}")


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class TestFromBlackWindowTiming(Frames, Base):
    """A window on the first span (over black, no plate) from a red 29.97 fps source drawn at 24
    fps, from two in points that land -ss on different parts of a source frame. The window's
    clock reads the draw rate, so a scale-window is full frame on the macro's frame 37 and a
    crop-window on frame 26 whichever part of a source frame -ss lands on (T32)."""

    W, H = 1280, 720

    def draft(self, effect, ss):
        src = os.path.join(self.dir, "red.mp4")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        "color=c=red:s=640x360:r=30000/1001:d=4", "-pix_fmt", "yuv420p",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "8", src], check=True)
        m = copy.deepcopy(METHOD)
        m.update(look=None, length_s=1.75)
        m["rules"]["end"] = None
        m["slots"] = [{**m["slots"][0], "seconds": 1.75, "effect": effect, "scale": None,
                       "move": "static", "transition_out": {"type": "cut", "frames": 0},
                       "text": None}]
        d = os.path.join(self.dir, "plans", f"{effect}-{ss}")
        os.makedirs(d)
        with open(os.path.join(d, "method.json"), "w") as f:
            json.dump(m, f)
        with open(os.path.join(d, "manifest.json"), "w") as f:
            json.dump(methods.to_manifest(m, [{"source_path": src, "in": ss, "out": ss + 1.75}]), f)
        doc = drafts.build([d], os.path.join(self.dir, f"out-{effect}-{ss}"), hw=False,
                           lanes=1)["drafts"][0]
        self.raw = subprocess.run(["ffmpeg", "-v", "error", "-i", doc["file"], "-f", "rawvideo",
                                   "-pix_fmt", "rgb24", "-"], capture_output=True,
                                  check=True).stdout
        self.size = self.W * self.H * 3
        self.assertEqual(len(self.raw) // self.size, 42)

    def test_a_scale_window_on_the_first_span_snaps_on_frame_37(self):
        for ss in (0.0, 0.5):
            with self.subTest(ss=ss):
                self.draft("scale-window", ss)
                self.assertAlmostEqual(self.red_width(36), self.W * 0.72, delta=8)
                for j in (37, 38):
                    self.assertGreater(self.red_width(j), self.W - 10, msg=f"frame {j}")

    def test_a_crop_window_on_the_first_span_is_full_frame_on_frame_26(self):
        for ss in (0.0, 0.01, 0.5):  # 0.01 made it full a frame early before the fix
            with self.subTest(ss=ss):
                self.draft("crop-window", ss)
                self.assertNotEqual(self.colour(25, 5, 5), "R")  # the box has not reached the corner
                for j in (26, 27):
                    self.assertEqual(self.colour(j, 5, 5), "R", msg=f"frame {j}")


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
