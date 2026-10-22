"""
Tests for rpresolve.wordsnap: word edges snapped against a synthetic
envelope (pauses, a stop-consonant dip, a click, untranscribed sound after a
long pause), then against tone bursts ffmpeg decodes, the output read back
through cutlist.load_words, and the CLI's refusal to write in a git tree.

Run: /usr/bin/python3 -m unittest production.tests.test_wordsnap -v
"""

import io
import json
import math
import os
import random
import struct
import sys
import tempfile
import unittest
import wave
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import cutlist, paths, wordsnap as ws  # noqa: E402

SPEECH, ROOM = -30.0, -80.0


def env_of(pieces, hop=ws.HOP_S):
    """An envelope like trimreview.envelope's from (seconds, dBFS) pieces."""
    env, i = [], 0
    for secs, db in pieces:
        for _ in range(int(round(secs / hop))):
            env.append((round(i * hop, 3), db))
            i += 1
    return env


def whisper(*words):
    """A Whisper JSON: one segment per pair of words, as mlx_whisper splits."""
    ws_ = [{"word": " " + w, "start": s, "end": e, "probability": 0.9} for w, s, e in words]
    segs = [{"id": i, "start": ws_[k]["start"], "end": ws_[min(k + 1, len(ws_) - 1)]["end"],
             "text": "", "words": ws_[k:k + 2]} for i, k in enumerate(range(0, len(ws_), 2))]
    return {"text": " ".join(w for w, _, _ in words), "segments": segs, "language": "en"}


def times(data):
    return [(w["start"], w["end"]) for s in data["segments"] for w in s["words"]]


# Room tone 0-1.0, speech 1.0-2.0, room 2.0-2.6, speech 2.6-3.5, room 3.5-5.0.
PIECES = [(1.0, ROOM), (1.0, SPEECH), (0.6, ROOM), (0.9, SPEECH), (1.5, ROOM)]


class TestSnap(unittest.TestCase):
    def snap(self, words, pieces=PIECES, **kw):
        return ws.snap(whisper(*words), env_of(pieces), **kw)

    def test_edges_in_pauses_move_onto_the_speech(self):
        out = self.snap([("Hello", 0.8, 1.3), ("there.", 1.3, 1.9), ("Next", 2.3, 3.8)])
        self.assertEqual(times(out), [(1.0, 1.3), (1.3, 2.0), (2.6, 3.5)])
        moved = [w["moved_ms"] for s in out["segments"] for w in s["words"]]
        self.assertEqual(moved, [{"start": 200, "end": 0}, {"start": 0, "end": 100},
                                 {"start": 300, "end": -300}])
        meta = out["wordsnap"]
        self.assertEqual((meta["words"], meta["starts"]["moved"], meta["ends"]["moved"]), (3, 2, 2))
        self.assertEqual(meta["starts"]["max_abs_ms"], 300)
        self.assertEqual((meta["floor_db"], meta["threshold_db"]), (ROOM, ROOM + ws.MARGIN_DB))
        for key in ("hop_s", "margin_db", "reach_s", "min_run_s", "floor_percentile"):
            self.assertIn(key, meta)

    def test_digital_silence_is_not_the_floor(self):
        # a muted or gated head of exact zeros reads -180 dB; as the floor it
        # would make all room tone speech and leave every edge where it was
        zeros = [(1.0, -180.0)] + PIECES[1:]
        out = self.snap([("Hello", 0.8, 1.3), ("there.", 1.3, 1.9), ("Next", 2.3, 3.8)], pieces=zeros)
        self.assertEqual(times(out), [(1.0, 1.3), (1.3, 2.0), (2.6, 3.5)])
        self.assertEqual(out["wordsnap"]["floor_db"], ROOM)

    def test_a_late_start_moves_back_to_the_onset_but_not_into_the_word_before(self):
        out = self.snap([("Hello", 1.15, 1.9)])
        self.assertEqual(times(out), [(1.0, 2.0)])
        # "there" starts inside the run "Hello" also sits in: no pause between, no move
        out = self.snap([("Hello", 1.0, 1.4), ("there", 1.45, 1.9)])
        self.assertEqual(times(out)[1][0], 1.45)

    def test_nothing_moves_further_than_reach(self):
        out = self.snap([("Hello", 0.5, 1.9)])
        self.assertEqual(times(out)[0][0], 0.5)
        out = self.snap([("Hello", 0.5, 1.9)], reach=0.6)
        self.assertEqual(times(out)[0][0], 1.0)

    def test_a_short_dip_is_speech_and_a_click_is_not(self):
        dip = [(1.0, ROOM), (0.5, SPEECH), (0.02, ROOM), (0.48, SPEECH), (2.0, ROOM)]
        out = self.snap([("Big", 1.0, 1.51), ("dog", 1.51, 1.9)], pieces=dip)
        self.assertEqual(times(out), [(1.0, 1.51), (1.51, 2.0)])
        click = [(0.7, ROOM), (0.02, SPEECH), (0.28, ROOM), (1.0, SPEECH), (2.0, ROOM)]
        out = self.snap([("Hello", 0.65, 1.9)], pieces=click)
        self.assertEqual(times(out)[0][0], 1.0)

    def test_untranscribed_sound_after_a_long_pause_does_not_stretch_the_word(self):
        tail = [(1.0, ROOM), (0.6, SPEECH), (1.0, ROOM), (0.3, SPEECH), (1.0, ROOM)]
        out = self.snap([("ISO", 1.0, 2.8)], pieces=tail)
        self.assertEqual(times(out), [(1.0, 2.8)])

    def test_a_word_over_room_tone_keeps_its_times_and_no_overlap_appears(self):
        out = self.snap([("Hello", 1.0, 1.9), ("mm", 4.0, 4.3)])
        self.assertEqual(times(out)[1], (4.0, 4.3))
        rng = random.Random(3)
        pieces = [(rng.choice([0.05, 0.2, 0.4, 0.7]), rng.choice([SPEECH, ROOM]))
                  for _ in range(60)]
        total = sum(p for p, _ in pieces)
        raw, t = [], 0.3
        while t < total - 0.5:
            d = rng.uniform(0.1, 0.5)
            raw.append(("w", round(t, 2), round(t + d, 2)))
            t += d + rng.choice([0.0, 0.0, 0.15])
        new = times(self.snap(raw, pieces=pieces))
        for (s, e), (ns, ne) in zip([(s, e) for _, s, e in raw], new):
            self.assertLess(ns, ne)
            self.assertLessEqual(abs(ns - s), ws.REACH_S + 1e-9)
            self.assertLessEqual(abs(ne - e), ws.REACH_S + 1e-9)
        for (_, e0), (s1, _) in zip(new, new[1:]):
            self.assertLessEqual(e0, s1 + 1e-9)

    def test_the_output_keeps_the_load_words_contract(self):
        data = whisper(("Hello", 0.8, 1.3), ("there.", 1.3, 1.9), ("Next", 2.3, 3.8))
        out = ws.snap(data, env_of(PIECES))
        self.assertEqual(data["segments"][0]["words"][0]["start"], 0.8)  # input untouched
        self.assertEqual(out["text"], data["text"])
        self.assertEqual(out["segments"][0]["words"][0]["probability"], 0.9)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "snapped.json")
            ws.write(out, p)
            words = cutlist.load_words(p)
        self.assertEqual([(w["word"], w["start"], w["end"]) for w in words],
                         [("Hello", 1.0, 1.3), ("there.", 1.3, 2.0), ("Next", 2.6, 3.5)])

    def test_no_words_or_no_audio_is_an_error(self):
        with self.assertRaises(ws.WordsnapError):
            ws.snap({"segments": [{"words": []}]}, env_of(PIECES))
        with self.assertRaises(ws.WordsnapError):
            ws.snap(whisper(("a", 0.0, 0.1)), [])


def burst_wav(path, pieces, rate=48000, seed=5):
    """Tone bursts (440 Hz at -12 dBFS) over noise at about -70 dBFS."""
    rng = random.Random(seed)
    frames, n = bytearray(), 0
    for secs, on in pieces:
        for _ in range(int(secs * rate)):
            tone = 0.25 * math.sin(2 * math.pi * 440 * n / rate) if on else 0.0
            v = 0.0003 * rng.gauss(0, 1) + tone
            frames += struct.pack("<h", int(max(-1, min(1, v)) * 32767))
            n += 1
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


class TestOnAudio(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        d = cls._tmp.name
        cls.wav = os.path.join(d, "bursts.wav")
        burst_wav(cls.wav, [(1.0, False), (1.0, True), (0.6, False), (0.9, True), (1.5, False)])
        cls.words = os.path.join(d, "words.json")
        with open(cls.words, "w", encoding="utf-8") as f:
            json.dump(whisper(("Hello", 0.8, 1.3), ("there.", 1.3, 1.9), ("Next", 2.3, 3.8)), f)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_edges_land_within_a_window_of_the_bursts(self):
        out = ws.build(self.words, self.wav)
        want = [(1.0, 1.3), (1.3, 2.0), (2.6, 3.5)]
        for (s, e), (ws_, we) in zip(times(out), want):
            self.assertAlmostEqual(s, ws_, delta=0.011)
            self.assertAlmostEqual(e, we, delta=0.011)
        self.assertEqual(out["wordsnap"]["source"], os.path.abspath(self.wav))
        self.assertLess(out["wordsnap"]["floor_db"], -60)

    def test_cli_writes_and_refuses_a_git_tree(self):
        out = os.path.join(self._tmp.name, "loose", "snapped.json")
        so, se = io.StringIO(), io.StringIO()
        with redirect_stdout(so), redirect_stderr(se):
            self.assertEqual(ws.main([self.words, self.wav, "--out", out]), 0)
        self.assertIn("wordsnap: 3 word(s)", so.getvalue())
        self.assertEqual(len(cutlist.load_words(out)), 3)
        repo = os.path.join(self._tmp.name, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        bad = os.path.join(repo, "snapped.json")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            self.assertEqual(ws.main([self.words, self.wav, "--out", bad]), 1)
        self.assertIn("git working tree", err.getvalue())
        self.assertFalse(os.path.exists(bad))
        with self.assertRaises(paths.OutputRefused):
            ws.write({}, bad)
        for src in (self.wav, self.words):                   # never over an input
            with open(src, "rb") as f:
                before = f.read()
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
                self.assertEqual(ws.main([self.words, self.wav, "--out", src]), 1)
            self.assertIn("is an input", err.getvalue())
            with open(src, "rb") as f:
                self.assertEqual(f.read(), before)


if __name__ == "__main__":
    unittest.main()
