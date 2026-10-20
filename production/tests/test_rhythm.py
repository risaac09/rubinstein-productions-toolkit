"""
Offline tests for rpresolve.rhythm: shot statistics on known lengths, the
EDL, timeline_items and paper-edit readers, the shuffled-order trend test,
cuts against a beat grid and chance, the questions each profile asks, a
render's cuts and loudness from a generated clip, and the command's output
guard. Synthetic cut lists and lavfi media only. The render test needs
ffmpeg.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import cutlist, rhythm  # noqa: E402

has_ffmpeg = unittest.skipUnless(os.access(cutlist.FFMPEG, os.X_OK), "ffmpeg missing")

EDL = """TITLE: test
FCM: NON-DROP FRAME

001  AX       V     C        00:00:10:00 00:00:12:00 01:00:00:00 01:00:02:00
* FROM CLIP NAME: a.mov
002  AX       A     C        00:00:10:00 00:00:12:00 01:00:00:00 01:00:02:00
003  AX       V     C        00:00:20:00 00:00:23:12 01:00:02:00 01:00:05:12
004  AX       V     C        00:00:05:00 00:00:05:00 01:00:05:12 01:00:05:12
004  BX       V     D    012 00:00:30:00 00:00:31:00 01:00:05:12 01:00:06:12
"""


class Measures(unittest.TestCase):
    def test_stats(self):
        s = rhythm.stats([1.0, 2.0, 3.0, 10.0, 0.2], fps=25)
        self.assertEqual(s["shots"], 5)
        self.assertAlmostEqual(s["asl"], 3.24)
        self.assertEqual(s["median"], 2.0)
        self.assertAlmostEqual(s["ratio"], 2.0 / 3.24)
        self.assertEqual(s["flash"], [4])  # 0.2 s is 5 frames at 25 fps
        self.assertEqual(rhythm.stats([0.2], fps=None)["flash"], [])

    def test_histogram_and_cut_rate(self):
        durs = [0.5, 1.5, 3, 5, 9, 20]
        self.assertEqual([n for _, n in rhythm.histogram(durs)], [1, 1, 1, 1, 1, 1])
        # Cuts at 0.5, 2, 5, 10, 19 s: two in the first 5 s, one in each later bin.
        self.assertEqual(rhythm.cut_rate(durs, 5), [2, 1, 1, 1, 0, 0, 0, 0])
        self.assertEqual(rhythm.spark([0, 1, 2, 4]), "·▂▄█")

    def test_trend_finds_a_built_acceleration_and_not_noise(self):
        accel = [8.0] * 10 + [4.0] * 10 + [1.0] * 10
        tr = rhythm.trend(accel)
        self.assertEqual((tr["min"][0], tr["max"][0]), (1.0, 8.0))
        self.assertLess(tr["rho"], -0.8)
        self.assertLess(tr["share"], 0.05)
        mixed = [8.0, 1.0, 4.0] * 10
        self.assertGreater(rhythm.trend(mixed)["share"], 0.5)
        self.assertIsNone(rhythm.trend([1.0] * 10))

    def test_spearman_with_ties(self):
        self.assertEqual(rhythm.ranks([3, 1, 3, 2]), [3.5, 1.0, 3.5, 2.0])
        self.assertAlmostEqual(rhythm.spearman([1, 2, 3, 4]), 1.0)
        self.assertAlmostEqual(rhythm.spearman([4, 3, 2, 1]), -1.0)
        self.assertEqual(rhythm.spearman([2, 2, 2]), 0.0)

    def test_on_beat_against_chance(self):
        durs = [2.0, 2.0, 2.0, 2.0]               # cuts at 2, 4, 6
        beats = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
        r = rhythm.on_beat(durs, beats, fps=25)
        self.assertEqual(r[1][0], 1.0)
        # Each beat covers 2 frames either side (0.08 s); the one at 0 only half.
        self.assertAlmostEqual(r[2][1], (7 * 0.16 + 0.08) / 8.0)
        off = rhythm.on_beat([2.5, 2.0, 2.0], beats, fps=25)
        self.assertEqual(off[2][0], 0.0)


class Readers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        p = os.path.join(self.d, name)
        Path(p).write_text(text)
        return p

    def test_edl_video_events_in_record_order(self):
        durs, fps = rhythm.from_edl(self.write("c.edl", EDL), 25)
        # The audio event and the zero-length outgoing half of the dissolve drop out.
        self.assertEqual(durs, [2.0, 3.48, 1.0])
        with self.assertRaisesRegex(rhythm.RhythmError, "needs --fps"):
            rhythm.from_edl(self.write("c.edl", EDL), None)

    def test_timeline_items(self):
        doc = {"timeline": {"fps": "24"}, "rows": [
            {"track": 1, "start": 86448, "end": 86496}, {"track": 2, "start": 86400, "end": 86500},
            {"track": 1, "start": 86400, "end": 86448}]}
        self.assertEqual(rhythm.from_items(self.write("t.json", json.dumps(doc))), ([2.0, 2.0], 24.0))
        gap = {"timeline": {"fps": 24}, "rows": [{"track": 1, "start": 0, "end": 48},
                                                  {"track": 1, "start": 72, "end": 96}]}
        self.assertEqual(rhythm.from_items(self.write("g.json", json.dumps(gap)))[0], [3.0, 1.0])
        page = dict(gap, total=300, next_offset=2)
        with self.assertRaisesRegex(rhythm.RhythmError, "holds 2 of 300 items"):
            rhythm.from_items(self.write("p.json", json.dumps(page)))
        bare = [{"track": 1, "start": 0, "end": 50}]
        with self.assertRaisesRegex(rhythm.RhythmError, "pass --fps"):
            rhythm.from_items(self.write("b.json", json.dumps(bare)))
        self.assertEqual(rhythm.from_items(self.write("b.json", json.dumps(bare)), 25), ([2.0], 25))

    def test_paper_edit(self):
        p = self.write("p.tsv", "clip_id\tin\tout\tbeat\nCAM-a1b2c3/P1\t3.0\t5.5\thook\nCAM-a1b2c3/P2\t0\t4\tturn\n")
        self.assertEqual(rhythm.from_tsv(p), ([2.5, 4.0], None))
        bad = self.write("q.tsv", "clip_id\tin\tout\nx\t5\t3\n")
        with self.assertRaisesRegex(rhythm.RhythmError, "not after its in"):
            rhythm.from_tsv(bad)
        blank = self.write("r.tsv", "clip_id\tin\tout\nx\t5\n")
        with self.assertRaisesRegex(rhythm.RhythmError, "no number in in or out"):
            rhythm.from_tsv(blank)


class Questions(unittest.TestCase):
    def ask(self, durs, profile="document", fps=25, beats=None):
        s = rhythm.stats(durs, fps)
        return rhythm.questions(s, durs, rhythm.trend(durs), beats, profile, 2.0, fps)

    def test_profiles(self):
        even = [3.0] * 20
        q = self.ask(even, "short-form")
        self.assertTrue(any("unusually even" in x for x in q))
        self.assertTrue(any("past the 2 s hook window" in x for x in q))
        self.assertFalse(any("hook window" in x for x in self.ask(even, "document")))
        quick = [1.0, 1.5] * 10
        self.assertTrue(any("short-form tempo in a document" in x for x in self.ask(quick)))
        self.assertFalse(any("short-form tempo" in x for x in self.ask(quick, "short-form")))

    def test_a_real_trend_is_asked_about(self):
        q = self.ask([8.0] * 10 + [4.0] * 10 + [1.0] * 10)
        self.assertTrue(any(x.startswith("Shots get shorter as the cut goes on (rank correlation -0.") for x in q))
        self.assertFalse(any("Shots get" in x for x in self.ask([8.0, 1.0, 4.0] * 10)))

    def test_flash_and_beats(self):
        q = self.ask([2.0, 0.2, 3.0, 0.1, 4.0], beats={1: (0.1, 0.1), 2: (0.1, 0.2)})
        self.assertTrue(any(x.startswith("2 shot(s) run under 8 frames (at 0:02, 0:05)") for x in q))
        self.assertTrue(any("no more often than chance (10% against 20%)" in x for x in q))


class Report(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_paper_edit_report(self):
        p = os.path.join(self.d, "p.tsv")
        Path(p).write_text("clip_id\tin\tout\n" + "".join(f"c/{i}\t0\t{3 + i % 2}\n" for i in range(20)))
        beats = os.path.join(self.d, "m.json")
        Path(beats).write_text(json.dumps({"beats_s": [0.5 * i for i in range(200)]}))
        text = rhythm.report(p, fps=25, beats_path=beats)
        self.assertIn("- shots 20, total 1:10 at 25 fps", text)
        self.assertIn("- cuts on a beat: within 1 frame 100% (chance 16%)", text)
        self.assertIn("## Questions (document)", text)
        self.assertIn("- cuts on a beat: not measured", rhythm.report(p, beats_path=beats))

    @has_ffmpeg
    def test_render_cuts_and_loudness(self):
        mov = os.path.join(self.d, "r.mov")
        graph = ("color=red:s=64x36:r=25:d=2[a];color=blue:s=64x36:r=25:d=3[b];"
                 "color=green:s=64x36:r=25:d=1[c];[a][b][c]concat=n=3:v=1:a=0[v]")
        subprocess.run([cutlist.FFMPEG, "-v", "error", "-y", "-filter_complex", graph,
                        "-f", "lavfi", "-i", "sine=f=440:d=6", "-map", "[v]", "-map", "0:a",
                        "-c:v", "mpeg4", "-q:v", "3", "-c:a", "aac", "-shortest", mov], check=True)
        durs, fps = rhythm.from_render(mov)
        self.assertEqual(fps, 25)
        self.assertEqual([round(d, 2) for d in durs], [2.0, 3.0, 1.0])
        text = rhythm.report(mov)
        self.assertIn("- shots 3, total 0:06 at 25 fps", text)
        self.assertIn("energy (1 char = 1.0 s", text)

    def test_command_output_guard(self):
        p = os.path.join(self.d, "p.tsv")
        Path(p).write_text("clip_id\tin\tout\nc/1\t0\t2\n")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(rhythm.main([p]), 0)
            inside = str(Path(__file__).resolve().parent / "rhythm.md")
            self.assertEqual(rhythm.main([p, "--out", inside]), 1)
            self.assertEqual(rhythm.main([os.path.join(self.d, "none.edl")]), 1)
        self.assertIn("# Rhythm: p.tsv", out.getvalue())
        self.assertIn("inside this repository", err.getvalue())
        self.assertFalse(os.path.exists(inside))


if __name__ == "__main__":
    unittest.main()
