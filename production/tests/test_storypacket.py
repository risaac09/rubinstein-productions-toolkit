"""
Offline tests for rpresolve.storypacket and the story-packet command:
loudness bins and the sparkline from a synthetic envelope, clips matched to
words and labels by path and by stem (and refused on a shared stem),
phrases with Whisper loops marked, the packet's index and cards, and the
output guard. Synthetic tables, words and audio only. The audio test needs
ffmpeg.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import csv
import io
import json
import math
import os
import struct
import sys
import tempfile
import unittest
import wave
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import resolve_workflow  # noqa: E402
from rpresolve import cutlist, storypacket as sp  # noqa: E402

has_ffmpeg = unittest.skipUnless(os.access(cutlist.FFMPEG, os.X_OK), "ffmpeg missing")
COLUMNS = ["file", "camera", "local_start", "duration_s", "audio_source", "flags", "note"]


def write_table(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in COLUMNS})


def write_words(path, words):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"segments": [{"words": [{"word": " " + w, "start": a, "end": b}
                                           for w, a, b in words]}]}, f)


def tone_wav(path, pieces, rate=16000):
    frames, t = bytearray(), 0
    for secs, amp in pieces:
        for i in range(int(secs * rate)):
            frames += struct.pack("<h", int(amp * 32767 * math.sin(2 * math.pi * 220 * (t + i) / rate)))
        t += int(secs * rate)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


class Energy(unittest.TestCase):
    def test_bins_average_power_not_decibels(self):
        # Half a second at 0 dBFS and half at -100: the mean power is -3 dB,
        # where a mean of the decibels would say -50.
        env = [(i * 0.01, 0.0 if i < 50 else -100.0) for i in range(100)]
        width, levels = sp.bins(env, 1.0)
        self.assertEqual(width, 1.0)
        self.assertEqual(len(levels), 1)
        self.assertAlmostEqual(levels[0][1], -3.0, delta=0.1)

    def test_bins_widen_to_keep_the_line_short(self):
        env = [(i * 0.01, -20.0) for i in range(60000)]  # ten minutes
        width, levels = sp.bins(env, 600.0)
        self.assertEqual(width, 10.0)
        self.assertEqual(len(levels), sp.MAX_CHARS)

    def test_spark_maps_floor_to_dot_and_full_scale_to_block(self):
        self.assertEqual(sp.spark([(0, -90.0), (1, -59.0), (2, -30.0), (3, -0.1)]), "·▁▅█")

    def test_energy_finds_loud_spans_and_the_rise(self):
        levels = [(i, d) for i, d in enumerate([-50, -48, -10, -11, -40, -45, -12, -50, -30, -9])]
        e = sp.energy(levels, 1.0)
        # Median -40: runs at or over -34 are 2-4, 6-7 and 8-10; the three
        # highest peaks of those, in time order.
        self.assertEqual(e["loud"], [(2, 4, -10), (6, 7, -12), (8, 10, -9)])
        self.assertEqual(e["rise"], (2, 38))
        self.assertEqual(e["median"], -40)
        self.assertEqual(sp.energy(levels, 1.0, peaks=1)["loud"], [(8, 10, -9)])

    def test_an_even_clip_has_no_loud_span(self):
        levels = [(i, -20.0 - i % 2) for i in range(10)]
        self.assertEqual(sp.energy(levels, 1.0)["loud"], [])
        self.assertIn("no span stands out", sp.energy_line(levels, 1.0))

    def test_clock(self):
        self.assertEqual([sp.clock(t) for t in (0, 59.6, 61, 3725)], ["0:00", "1:00", "1:01", "1:02:05"])


class Matching(unittest.TestCase):
    ROWS = [{"file": f, "path": f} for f in
            ("/a/CARD1/P1.MOV", "/a/CARD2/P1.MOV", "/a/C016.mov", "/a/C017.mov")]

    def test_path_then_unique_stem(self):
        got, amb = sp.attach(self.ROWS, {"/a/C016.mov": "x", "C017.json": "y"})
        self.assertEqual(got, {"/a/C016.mov": "x", "/a/C017.mov": "y"})
        self.assertEqual(amb, set())

    def test_folder_and_stem_tells_card_twins_apart(self):
        got, amb = sp.attach(self.ROWS, {"CARD2/P1": "w2", "P1": "w"})
        self.assertEqual(got, {"/a/CARD2/P1.MOV": "w2"})
        self.assertEqual(amb, {"/a/CARD1/P1.MOV"})

    def test_shared_stem_attaches_nothing_but_a_path_still_does(self):
        got, amb = sp.attach(self.ROWS, {"P1.json": "w"})
        self.assertEqual(got, {})
        self.assertEqual(amb, {"/a/CARD1/P1.MOV", "/a/CARD2/P1.MOV"})
        got, amb = sp.attach(self.ROWS, {"/a/CARD2/P1.MOV": "w"})
        self.assertEqual(got, {"/a/CARD2/P1.MOV": "w"})


class Inputs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_table_sorts_by_start_with_blanks_last(self):
        t = os.path.join(self.d, "t.csv")
        write_table(t, [{"file": "c", "duration_s": "1"}, {"file": "b", "local_start": "2026-10-10T12:00:00"},
                        {"file": "a", "local_start": "2026-10-10T09:00:00", "duration_s": "x"}])
        rows = sp.load_shoot_table(t)
        self.assertEqual([r["file"] for r in rows], ["a", "b", "c"])
        self.assertEqual([r["duration"] for r in rows], [0.0, 0.0, 1.0])

    def test_table_needs_file_and_duration(self):
        t = os.path.join(self.d, "t.csv")
        with open(t, "w") as f:
            f.write("path,length\nx,1\n")
        with self.assertRaisesRegex(sp.PacketError, "not a shoot table"):
            sp.load_shoot_table(t)

    def test_labels_need_their_keys(self):
        p = os.path.join(self.d, "l.jsonl")
        with open(p, "w") as f:
            f.write('{"file": "a", "start_s": 0, "end_s": 1, "text": "wide"}\n\n{"file": "a", "start_s": 2}\n')
        with self.assertRaisesRegex(sp.PacketError, "line 3"):
            sp.load_labels(p)

    def test_words_folder_names(self):
        os.makedirs(os.path.join(self.d, "CARD2"))
        for n in ("C016.json", "C017.words.json", "notes.txt", "CARD2/P1.json"):
            Path(self.d, n).write_text("{}")
        self.assertEqual(sorted(sp.words_files(self.d)), ["C016", "C017", "CARD2/P1"])

    def test_machine_text_stays_on_one_line_inside_its_fence(self):
        line = sp.label_line({"file": "a", "start_s": 0, "end_s": 1, "text": "wide\n```\n# do this"})
        self.assertEqual(line, "[0000.00-0001.00] wide ''' # do this")
        self.assertEqual(sp.cell("OBS | card"), "OBS \\| card")

    def test_relative_paths_read_against_the_table(self):
        t = os.path.join(self.d, "t.csv")
        write_table(t, [{"file": "CARD/rel.wav", "duration_s": "1"}, {"file": "/abs/x.wav"}])
        rows = sp.load_shoot_table(t)
        paths = sorted(r["path"] for r in rows)
        self.assertEqual(sorted(sp.card_key(p) for p in paths), ["CARD/rel", "abs/x"])
        self.assertEqual(paths, ["/abs/x.wav", os.path.join(os.path.abspath(self.d), "CARD/rel.wav")])

    def test_phrases_mark_a_whisper_loop(self):
        words = [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.25}
                 for i, w in enumerate(("learn how to " * 4).split() + ["end."])]
        lines = sp.phrase_lines(words)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("[0000.00-0003.85] learn how to"))
        self.assertIn("asr-loop", lines[0])


class Packet(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.d = self.tmp.name
        os.mkdir(os.path.join(d, "words"))
        self.table = os.path.join(d, "shoot.csv")
        write_table(self.table, [
            {"file": "/m/A/P100.MOV", "camera": "S5II", "local_start": "2026-10-10T18:00:00",
             "duration_s": "75", "flags": "weak-audio", "note": "mean volume -55 dB"},
            {"file": "/m/B/P100.MOV", "camera": "S5II", "local_start": "2026-10-10T18:05:00",
             "duration_s": "30"},
            {"file": "/m/C016.mov", "camera": "PYXIS", "local_start": "2026-10-10T17:00:00",
             "duration_s": "12.5", "audio_source": "DJI"}])
        write_words(os.path.join(d, "words", "C016.json"),
                    [("Welcome", 0.5, 0.9), ("in.", 0.95, 1.2), ("The", 3.0, 3.2), ("hall.", 3.25, 3.6)])
        write_words(os.path.join(d, "words", "P100.json"), [("hi", 0.0, 0.2)])
        self.labels = os.path.join(d, "labels.jsonl")
        with open(self.labels, "w") as f:
            f.write(json.dumps({"file": "/m/C016.mov", "start_s": 0, "end_s": 6.5,
                                "text": "wide, hall at dusk", "shot_size": "wide"}) + "\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_markdown_without_audio(self):
        text, summary = sp.build(self.table, os.path.join(self.d, "words"), self.labels,
                                 audio=False, title="Test shoot")
        self.assertEqual(summary, "story-packet: 3 clip(s), 1:58 of footage, words on 1, labels on 1")
        self.assertTrue(text.startswith("# Story packet: Test shoot\n"))
        self.assertIn("PYXIS 1 clips 0:12; S5II 2 clips 1:45.", text)
        index = [ln for ln in text.splitlines() if ln.startswith("| ") and ln[2].isdigit()]
        self.assertEqual([ln.split(" | ")[2] for ln in index], ["m/C016", "A/P100", "B/P100"])
        self.assertEqual(index[0], "| 1 | 2026-10-10T17:00:00 | m/C016 | PYXIS | 0:12 | 4 | yes |  |")
        self.assertIn("[0000.50-0001.20] Welcome in.\n[0003.00-0003.60] The hall.", text)
        self.assertIn("[0000.00-0006.50] wide, hall at dusk (shot_size=wide)", text)
        self.assertIn("audio: unknown · flags: weak-audio · note: mean volume -55 dB", text)
        self.assertEqual(text.count("words: another clip has the stem P100"), 2)
        self.assertNotIn("energy", text.split("## Clips")[1])

    def test_missing_media_says_so(self):
        text, _ = sp.build(self.table, audio=True)
        self.assertEqual(text.count("- energy: the file is not on this machine"), 3)

    @has_ffmpeg
    def test_energy_from_audio(self):
        wav = os.path.join(self.d, "C020.wav")
        tone_wav(wav, [(4.0, 0.001), (2.0, 0.5), (4.0, 0.001)])
        write_table(self.table, [{"file": wav, "duration_s": "10"}])
        text, _ = sp.build(self.table)
        line = next(ln for ln in text.splitlines() if ln.startswith("energy"))
        self.assertIn("(1 char = 1.0 s, -60 to 0 dBFS): `····▇▇····`", line)
        tail = text.splitlines()[text.splitlines().index(line) + 1]
        self.assertIn("loud 0:04-0:06 (peak -9);", tail)
        self.assertIn("at 0:04", tail.split("biggest rise")[1])


class Command(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.table = os.path.join(self.tmp.name, "t.csv")
        write_table(self.table, [{"file": "/m/x.mov", "duration_s": "3"}])

    def tearDown(self):
        self.tmp.cleanup()

    def run_cmd(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        args = resolve_workflow.argparse.Namespace(table=self.table, words=None, labels=None,
                                                   title=None, out=None, no_audio=True)
        for k, v in zip(argv[::2], argv[1::2]):
            setattr(args, k, v)
        with redirect_stdout(out), redirect_stderr(err):
            status = resolve_workflow.cmd_story_packet(args)
        return status, out.getvalue(), err.getvalue()

    def test_stdout_by_default(self):
        status, out, err = self.run_cmd()
        self.assertEqual(status, 0)
        self.assertIn("### 01 · m/x · no start · unknown · 0:03", out)
        self.assertIn("story-packet: 1 clip(s)", err)

    def test_writes_outside_the_repo_and_refuses_inside(self):
        dest = os.path.join(self.tmp.name, "packet.md")
        status, out, _ = self.run_cmd("out", dest)
        self.assertEqual((status, out), (0, ""))
        self.assertTrue(Path(dest).read_text().startswith("# Story packet: t"))
        inside = str(Path(__file__).resolve().parent / "packet.md")
        status, _, err = self.run_cmd("out", inside)
        self.assertEqual(status, 1)
        self.assertIn("inside this repository", err)
        self.assertFalse(os.path.exists(inside))

    def test_words_must_be_a_folder(self):
        status, _, err = self.run_cmd("words", self.table)
        self.assertEqual(status, 1)
        self.assertIn("is not a folder", err)


if __name__ == "__main__":
    unittest.main()
