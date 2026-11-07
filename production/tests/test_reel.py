"""
Tests for rpresolve.reel: the beat-aligned plan on synthetic words and a
120 BPM beat grid (stdlib), and the preview render on lavfi fixtures
(ffmpeg). No real media, no Resolve.

Run: /usr/bin/python3 -m unittest tests.test_reel -v
"""

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import cutlist, reel  # noqa: E402
from rpresolve import delivercheck as dc  # noqa: E402
from rpresolve import interchange as ic  # noqa: E402

FPS = 25
BEAT = 0.5  # 120 BPM


def whisper(words):
    """A Whisper JSON from (word, start, end) triples, one segment per pair."""
    ws = [{"word": " " + w, "start": s, "end": e, "probability": 0.9} for w, s, e in words]
    segs = [{"id": i, "start": ws[k]["start"], "end": ws[min(k + 1, len(ws) - 1)]["end"],
             "text": "", "words": ws[k:k + 2]} for i, k in enumerate(range(0, len(ws), 2))]
    return {"text": " ".join(w for w, _, _ in words), "segments": segs, "language": "en"}


# Words of 0.3 s every 0.5 s from 1.0 s (gaps of 0.2 s), with longer pauses after
# "four" (1.2 s), "nine" (1.0 s) and "thirteen" (end): three phrases.
PHRASES = [(["one", "two", "three", "four"], 1.0), (["five", "six", "seven", "eight", "nine"], 4.0),
           (["ten", "eleven", "twelve", "thirteen"], 7.5)]


def words_list():
    out = []
    for ws, t0 in PHRASES:
        for i, w in enumerate(ws):
            out.append((w, round(t0 + i * 0.5, 3), round(t0 + i * 0.5 + 0.3, 3)))
    return out


def beats(seconds=60, beat=BEAT):
    return {"file": "/nonexistent/beat.wav", "duration_s": seconds, "tempo": round(60 / beat, 1),
            "beats_s": [round(k * beat, 3) for k in range(int(seconds / beat))]}


def reel_args(manifest, music, out):
    p = argparse.ArgumentParser()
    reel.add_arguments(p)
    return p.parse_args(["plan", manifest, "--music", music, "--beats", "b.json", "--out", out])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.realpath(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.words = os.path.join(self.dir, "src.words.json")
        with open(self.words, "w", encoding="utf-8") as f:
            json.dump(whisper(words_list()), f)
        self.src = os.path.join(self.dir, "src.mov")
        self.music = os.path.join(self.dir, "beat.wav")
        open(self.music, "wb").close()

    def manifest(self, spans):
        m = {"version": 1, "source_path": self.src, "source_sha256": "0" * 64, "fps": FPS,
             "words": self.words, "approved_text": "x",
             "clips": [{"name": "reel", "spans": spans}]}
        return cutlist.validate_manifest(m)

    # the three phrases as spans whose edges sit a little inside the pauses, on the frame grid
    SPANS = [{"in": 0.92, "out": 2.92}, {"in": 3.92, "out": 6.52}, {"in": 7.4, "out": 9.32}]


class TestWindows(Base):
    def test_edges_move_only_inside_the_pauses(self):
        ws = cutlist.load_words(self.words)
        w = reel.windows(ws, self.SPANS[1], FPS)
        # in: down to "four"'s end (2.8) + EDGE_S, up to the span's own in (3.92)
        self.assertEqual((w["in_lo"], w["in_hi"]), (71, 98))
        # out: from "nine"'s end (6.3) + EDGE_S to "ten"'s start (7.5) - EDGE_S
        self.assertEqual((w["out_lo"], w["out_hi"]), (158, 187))
        self.assertEqual(w["d_orig"], 65)
        self.assertEqual((w["first"], w["last"]), ("five", "nine"))
        last = reel.windows(ws, self.SPANS[2], FPS)
        self.assertEqual(last["out_hi"], 257)  # no word after: floor((9.3 + MAX_PAUSE_S) * FPS)
        with self.assertRaisesRegex(reel.ReelError, "no words"):
            reel.windows(ws, {"in": 20.0, "out": 21.0}, FPS)

    def test_place_moves_the_out_point_first(self):
        w = {"in_lo": 71, "in_hi": 98, "out_lo": 158, "out_hi": 187}
        self.assertEqual(reel.place(w, 70), (98, 168))   # out moves, in stays
        self.assertEqual(reel.place(w, 100), (87, 187))  # out at its limit, in moves earlier
        self.assertEqual(reel.place(w, 60), (98, 158))   # shortest: out at its earliest


class TestPlan(Base):
    def test_every_cut_lands_on_a_beat_and_outside_every_word(self):
        m = self.manifest(self.SPANS)
        r, rows, warnings = reel.plan(m, self.music, beats(), self.dir)
        self.assertEqual(warnings, [])
        ws = cutlist.load_words(self.words)
        record = 0.0
        for s, row in zip(r["clips"][0]["spans"], rows):
            record += s["out"] - s["in"]
            self.assertAlmostEqual(record, row["cut_s"], places=3)
            beat_s = row["beat"] * BEAT  # the cut is that beat, rounded to the frame grid
            self.assertLessEqual(abs(row["cut_s"] - beat_s), 0.5 / FPS + 1e-9)
            self.assertAlmostEqual(row["offset_ms"], (row["cut_s"] - beat_s) * 1000, places=1)
            for w in ws:  # no edge inside a word
                self.assertFalse(w["start"] < s["in"] < w["end"], (s, w))
                self.assertFalse(w["start"] < s["out"] < w["end"], (s, w))
            self.assertTrue(cutlist.words_between(ws, s["in"], s["out"]))
        self.assertEqual(rows[0]["beat"] % reel.BEATS_PER_BAR, 0)  # the hook ends on a downbeat
        self.assertEqual([row["transition"] for row in rows], ["cut"] * 3)
        self.assertEqual(r["music"], {"path": self.music, "in": 0.0})
        self.assertEqual(r["reel"]["tempo"], 120.0)
        self.assertEqual(r["reel"]["cuts"][-1]["cut_s"], rows[-1]["cut_s"])
        self.assertEqual(r["version"], 1)
        self.assertEqual(r["clips"][0]["spans"][0]["in"], 0.92)  # in-points never move later
        cutlist.validate_manifest(r)  # endcheck and interchange read it unchanged

    def test_the_downbeat_offset_moves_the_hook_cut(self):
        m = self.manifest(self.SPANS)
        _, rows, _ = reel.plan(m, self.music, beats(), self.dir, downbeat_offset=2)
        self.assertEqual(rows[0]["beat"] % 4, 2)

    def test_dissolves_need_room_in_the_pauses(self):
        m = self.manifest(self.SPANS)
        r, rows, warnings = reel.plan(m, self.music, beats(), self.dir, transition="dissolve:6")
        spans = r["clips"][0]["spans"]
        self.assertEqual(spans[0].get("transition"), None)
        for k in (1, 2):
            self.assertEqual(spans[k]["transition"], {"type": "dissolve", "frames": 6})
            self.assertEqual(rows[k]["transition"], "dissolve 6")
        # a dissolve too long for the 0.2 s gap between "two" and "three" falls back to a cut
        tight = [{"in": 0.92, "out": 1.92}, {"in": 1.92, "out": 2.92}, {"in": 3.92, "out": 6.52}]
        r2, rows2, warnings2 = reel.plan(self.manifest(tight), self.music, beats(), self.dir,
                                        transition="dissolve:12", downbeat_offset=3)
        self.assertEqual(rows2[1]["transition"], "cut")
        self.assertTrue(any("cut instead" in w for w in warnings2))
        cutlist.validate_manifest(r2)  # spans 1 and 2 share the pause after "two": no overlap

    def test_music_in_moves_the_grid_and_keeps_the_downbeat(self):
        r, rows, _ = reel.plan(self.manifest(self.SPANS), self.music, beats(), self.dir, music_in=1.5)
        for row in rows:  # a cut at record t is music time t + 1.5
            self.assertLessEqual(abs(row["cut_s"] + 1.5 - row["beat"] * BEAT), 0.5 / FPS + 1e-9)
        self.assertEqual(rows[0]["beat"] % reel.BEATS_PER_BAR, 0)
        with self.assertRaisesRegex(reel.ReelError, "ends before it"):
            reel.plan(self.manifest(self.SPANS), self.music, beats(), self.dir, music_in=90.0)

    def test_the_last_span_may_end_off_the_beat(self):
        grid = dict(beats(), beats_s=[k * BEAT for k in range(11)])  # the grid stops at 5 s
        r, rows, warnings = reel.plan(self.manifest(self.SPANS), self.music, grid, self.dir)
        self.assertEqual(warnings, ["span 3 ends off the beat: no beat in its range"])
        self.assertIsNone(rows[-1]["beat"])
        self.assertIsNone(rows[-1]["offset_ms"])
        self.assertIsNotNone(rows[-2]["beat"])
        s = r["clips"][0]["spans"][-1]
        self.assertAlmostEqual(rows[-1]["cut_s"] - rows[-2]["cut_s"], s["out"] - s["in"], places=3)
        reel.tsv(rows)
        cutlist.validate_manifest(r)

    def test_the_last_out_point_stays_inside_the_source(self):
        open(self.src, "wb").close()
        with mock.patch.object(reel.ic, "probe", return_value={"frames": 240}):  # 9.6 s
            r, _, _ = reel.plan(self.manifest(self.SPANS), self.music, beats(), self.dir)
        self.assertLessEqual(r["clips"][0]["spans"][-1]["out"], 9.6)

    def test_two_spans_either_side_of_one_pause_split_it(self):
        # "four" ends at 2.8, "five" starts at 4.0: both spans may drift into that pause
        m = self.manifest([{"in": 0.92, "out": 2.84}, {"in": 3.96, "out": 6.52}])
        ws = cutlist.load_words(self.words)
        wins = [reel.windows(ws, s, FPS) for s in m["clips"][0]["spans"]]
        self.assertGreater(wins[0]["out_hi"], wins[1]["in_lo"])
        reel._share_pauses(m, m["clips"][0]["spans"], wins)
        self.assertEqual(wins[0]["out_hi"], 85)
        self.assertEqual(wins[1]["in_lo"], 85)
        self.assertEqual(wins[1]["in_hi"], 99)  # unchanged
        self.assertEqual((wins[0]["hold_hi"], wins[1]["hold_lo"]), (99, 71))  # handles keep the pause
        r, rows, _ = reel.plan(m, self.music, beats(), self.dir)
        s1, s2 = r["clips"][0]["spans"]
        self.assertLessEqual(s1["out"], s2["in"])
        cutlist.validate_manifest(r)
        # the later span's in-point sits early in the pause: the split stops there, not at the middle
        early = self.manifest([{"in": 0.92, "out": 2.84}, {"in": 2.88, "out": 6.52}])
        wins = [reel.windows(ws, s, FPS) for s in early["clips"][0]["spans"]]
        reel._share_pauses(early, early["clips"][0]["spans"], wins)
        self.assertEqual((wins[0]["out_hi"], wins[1]["in_lo"], wins[1]["in_hi"]), (72, 72, 72))
        r, rows, _ = reel.plan(early, self.music, beats(), self.dir, downbeat_offset=1,
                               transition="dissolve:6")
        cutlist.validate_manifest(r)
        self.assertEqual(rows[1]["transition"], "cut")  # a handle before 72 would reach "four"
        r, rows, _ = reel.plan(m, self.music, beats(), self.dir, transition="dissolve:6")
        cutlist.validate_manifest(r)
        self.assertEqual(rows[1]["transition"], "dissolve 6")  # handles overlap in the pause

    def test_punch_in_alternates_and_skips_cutouts(self):
        m = self.manifest(self.SPANS)
        r, rows, _ = reel.plan(m, self.music, beats(), self.dir, punch="1.0,1.2", cutout=[2])
        spans = r["clips"][0]["spans"]
        self.assertEqual([s.get("scale") for s in spans], [1.0, None, 1.2])
        self.assertEqual([s.get("cutout") for s in spans], [None, True, None])
        self.assertEqual([row["cutout"] for row in rows], [False, True, False])
        marked = self.manifest([dict(self.SPANS[0], cutout=True)] + self.SPANS[1:])
        r, rows, _ = reel.plan(marked, self.music, beats(), self.dir, punch="1.0,1.2")
        self.assertEqual([s.get("scale") for s in r["clips"][0]["spans"]], [None, 1.0, 1.2])
        with self.assertRaisesRegex(reel.ReelError, "--cutout 4: the clip has 3"):
            reel.plan(m, self.music, beats(), self.dir, cutout=[4])

    def test_over_max_s_trailing_spans_drop_with_a_warning(self):
        m = self.manifest(self.SPANS)
        r, rows, warnings = reel.plan(m, self.music, beats(), self.dir, max_s=5.0)
        self.assertEqual(len(rows), 2)
        self.assertEqual(warnings, ["span 3 dropped: the reel would run past 5 s"])
        self.assertLessEqual(r["reel"]["seconds"], 5.0)
        _, rows, warnings = reel.plan(self.manifest(self.SPANS[:1]), self.music, beats(), self.dir,
                                      max_s=1.0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(warnings, ["the one span left runs past 1 s; nothing to drop"])

    def test_no_beat_in_range_names_the_span(self):
        m = self.manifest(self.SPANS)
        # at 60 BPM the downbeats sit 4 s apart: none between "four" and "five"
        with self.assertRaisesRegex(reel.ReelError, r"span 1 \(one \.\.\. four\): no beat"):
            reel.plan(m, self.music, beats(beat=1.0), self.dir)

    def test_writes_refuse_a_git_tree_and_land_outside(self):
        m = self.manifest(self.SPANS)
        r, rows, _ = reel.plan(m, self.music, beats(), self.dir)
        with self.assertRaises(Exception):
            reel.write_plan(r, rows, os.path.join(os.path.dirname(__file__), "reel-out"))
        out = os.path.join(self.dir, "plan")
        path = reel.write_plan(r, rows, out)
        self.assertEqual(reel.write_plan(r, rows, out), path)  # a second run reuses the folder
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["reel"]["tempo"], 120.0)
        with open(os.path.join(out, "reel-plan.tsv"), encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0].split("\t")[:3], ["span", "in", "out"])
        self.assertEqual(len(lines), 4)

    def test_plan_refuses_to_write_over_its_input(self):
        path = os.path.join(self.dir, "reel.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.manifest(self.SPANS), f)
        args = reel_args(path, self.music, self.dir)
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(reel.run(args), 2)
        self.assertIn("own output", err.getvalue())

    def test_bad_flags(self):
        with self.assertRaisesRegex(reel.ReelError, "dissolve:N"):
            reel._transition("wipe:5")
        with self.assertRaisesRegex(reel.ReelError, "two or more"):
            reel._punch("1.2")
        with self.assertRaisesRegex(reel.ReelError, "beats.json"):
            p = os.path.join(self.dir, "b.json")
            with open(p, "w") as f:
                json.dump({"tempo": 120}, f)
            reel.load_beats(p)
        with open(p, "w") as f:
            json.dump({"tempo": 120, "beats_s": [1.0, 0.5]}, f)
        with self.assertRaisesRegex(reel.ReelError, "time order"):
            reel.load_beats(p)
        with open(p, "w") as f:
            json.dump({"tempo": 120, "beats_s": [0.5, 1.0]}, f)
        self.assertEqual(reel.load_beats(p)["beats_path"], p)
        m = self.manifest(self.SPANS)
        del m["fps"]
        with self.assertRaisesRegex(reel.ReelError, "pass --fps"):
            reel.plan(m, self.music, beats(), self.dir)


@unittest.skipUnless(os.access(dc.FFMPEG, os.X_OK) and os.access(dc.FFPROBE, os.X_OK),
                     "ffmpeg missing")
class TestPreview(Base):
    def test_the_preview_renders_the_plan_as_9x16(self):
        subprocess.run([dc.FFMPEG, "-v", "error", "-y", "-f", "lavfi",
                        "-i", f"testsrc2=size=320x180:rate={FPS}:duration=12",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=12",
                        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", self.src], stdin=subprocess.DEVNULL, check=True)
        subprocess.run([dc.FFMPEG, "-v", "error", "-y", "-f", "lavfi",
                        "-i", "sine=frequency=110:sample_rate=48000:duration=30", self.music],
                       stdin=subprocess.DEVNULL, check=True)
        layer = os.path.join(self.dir, "pop.mov")
        subprocess.run([dc.FFMPEG, "-v", "error", "-y", "-f", "lavfi",
                        "-i", f"color=c=green@0.5:s=320x180:r={FPS}:d=1,format=rgba",
                        "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
                        layer], stdin=subprocess.DEVNULL, check=True)
        m = self.manifest(self.SPANS)
        r, rows, _ = reel.plan(m, self.music, beats(), self.dir, transition="dissolve:5",
                               punch="0.9,1.2")
        r["clips"][0]["spans"][2]["layer"] = {"path": layer}  # on a dissolved span
        xml = ic.fcpxml(ic.timeline(r))  # the plan writes as FCPXML, on the same frames
        self.assertEqual(xml.count("<transition "), 2)
        self.assertIn('lane="-1"', xml)
        out = os.path.join(self.dir, "preview.mp4")
        self.assertIn("setpts=PTS-STARTPTS+0.120000/TB", " ".join(reel.preview_command(r, out)[0]))
        seconds = reel.preview(r, out)
        self.assertAlmostEqual(seconds, rows[-1]["cut_s"], places=3)
        probe = subprocess.run([dc.FFPROBE, "-v", "error", "-show_entries",
                                "stream=codec_type,width,height,r_frame_rate,sample_rate,channels,"
                                "color_transfer:format=duration", "-of", "json", out],
                               stdout=subprocess.PIPE, check=True, encoding="utf-8")
        info = json.loads(probe.stdout)
        v = next(s for s in info["streams"] if s["codec_type"] == "video")
        a = next(s for s in info["streams"] if s["codec_type"] == "audio")
        self.assertEqual((v["width"], v["height"], v["r_frame_rate"], v.get("color_transfer")),
                         (1080, 1920, f"{FPS}/1", "bt709"))
        self.assertEqual((a["sample_rate"], a["channels"]), ("48000", 2))
        self.assertAlmostEqual(float(info["format"]["duration"]), seconds, delta=0.15)
        del r["clips"][0]["spans"][1]["transition"]  # a cut into a dissolve: concat feeds xfade
        self.assertAlmostEqual(reel.preview(r, out), seconds, places=3)


if __name__ == "__main__":
    unittest.main()
