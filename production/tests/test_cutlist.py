"""
Offline tests for rpresolve.cutlist and rpresolve.cut. Synthetic words,
synthetic approved text and synthetic audio only: no real transcript, no
real media, no Resolve.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import cut, cutlist

APPROVED = """---
title: synthetic essay
---
The river keeps its own time. Most people act as if the map is the whole
journey. It is not, it is a small part. It is 20 percent. I can say that with
confidence now. For a long time I kept that quiet.
"""


def words_from(spec):
    """[(word, start, end)] -> Whisper-shaped JSON document."""
    return {"segments": [{"words": [{"word": " " + w, "start": a, "end": b} for w, a, b in spec]}]}


SPEECH = [("people", 1.0, 1.3), ("act", 1.3, 1.5), ("like", 1.5, 1.7), ("the", 1.7, 1.8),
          ("map", 1.8, 2.1), ("is", 2.1, 2.2), ("everything.", 2.2, 2.7), ("it's", 2.8, 3.0),
          ("20%.", 3.0, 3.5), ("I", 3.6, 3.7), ("kept", 3.7, 4.0), ("that", 4.0, 4.2),
          ("very", 4.2, 4.4), ("quiet.", 4.4, 4.9), ("and", 4.9, 5.1), ("being", 5.1, 5.4)]


def tone_wav(path, pieces, rate=16000):
    """A mono WAV of (seconds, amplitude) pieces: a 220 Hz tone, or silence at 0."""
    frames = bytearray()
    t = 0
    for secs, amp in pieces:
        for i in range(int(secs * rate)):
            v = int(amp * 32767 * math.sin(2 * math.pi * 220 * (t + i) / rate))
            frames += struct.pack("<h", v)
        t += int(secs * rate)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


class TestText(unittest.TestCase):
    def test_tokens_and_content(self):
        self.assertEqual(cutlist.tokens("It’s 20%."), ["it's", "20"])
        self.assertEqual(cutlist.content_tokens("I was keeping that very quiet, you know"),
                         ["keep", "quiet"])
        self.assertIn("percent", cutlist.content_tokens("It's 20%"))

    def test_approved_verdicts(self):
        approved = cutlist.content_tokens(cutlist.strip_frontmatter(APPROVED))
        paraphrase = cutlist.content_tokens(
            "they act like the map is everything. it's 20%. I kept that very quiet")
        self.assertEqual(cutlist.approved_check(paraphrase, approved)["verdict"], "pass")
        inserted = cutlist.content_tokens(
            "the map is everything, and so that's created something really strange you know, "
            "a whole weird tangle of mountains and valleys and markets and borders nobody drew")
        r = cutlist.approved_check(inserted, approved)
        self.assertEqual(r["verdict"], "fail")
        self.assertTrue(r["unmatched"])
        self.assertEqual(cutlist.approved_check([], approved)["verdict"], "fail")


class TestWordsAndEnds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.words_path = os.path.join(self.tmp.name, "w.json")
        with open(self.words_path, "w") as f:
            json.dump(words_from(SPEECH), f)
        self.words = cutlist.load_words(self.words_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_midpoint_membership(self):
        self.assertEqual(cutlist.end_words_for(self.words, 4.95, 2), "very quiet.")
        self.assertEqual(cutlist.span_text(self.words, 3.6, 4.95), "I kept that very quiet.")

    def test_end_word_rule_without_audio(self):
        ok = cutlist.end_check(self.words, 4.95, "very quiet")
        self.assertTrue(ok["ok"])
        self.assertFalse(ok["audio_checked"])
        early = cutlist.end_check(self.words, 4.25, "very quiet")
        self.assertFalse(early["ok"])
        self.assertIn("expected \"very quiet\"", early["reason"])
        dangling = cutlist.end_check(self.words, 5.05, "quiet. and")
        self.assertEqual(dangling["dangling"], "and")
        self.assertFalse(dangling["ok"])

    def test_repetition_loops(self):
        loop = [(w, i * 0.2, i * 0.2 + 0.2) for i, w in
                enumerate(["learn", "how", "to"] * 5 + ["done"])]
        words = cutlist.load_words(self._write(words_from(loop)))
        loops = cutlist.repetition_loops(words)
        self.assertEqual(len(loops), 1)
        self.assertEqual(loops[0][2], "learn how to")
        self.assertEqual(cutlist.repetition_loops(self.words), [])

    def _write(self, doc):
        p = os.path.join(self.tmp.name, "x.json")
        with open(p, "w") as f:
            json.dump(doc, f)
        return p


class TestSilences(unittest.TestCase):
    def test_runs_and_clean_cuts(self):
        env = [(i * 0.01, -20.0) for i in range(10)] + [(0.1 + i * 0.01, -60.0) for i in range(5)] \
            + [(0.15 + i * 0.01, -20.0) for i in range(10)]
        runs = cutlist.silences(env)
        self.assertEqual(runs, [(0.1, 0.15)])
        self.assertEqual(cutlist.cut_is_clean(runs, 0.125), (0.1, 0.15))
        self.assertIsNone(cutlist.cut_is_clean(runs, 0.102))   # too near the edge
        self.assertIsNone(cutlist.cut_is_clean(runs, 0.05))    # on speech
        short = [(0.0, -20.0), (0.01, -60.0), (0.02, -20.0)]
        self.assertEqual(cutlist.silences(short), [])

    @unittest.skipUnless(os.access(cutlist.FFMPEG, os.X_OK), "ffmpeg missing")
    def test_envelope_and_suggestion_on_real_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "t.wav")
            # speech until 4.9 s, a 100 ms pause, speech again
            tone_wav(wav, [(4.9, 0.3), (0.1, 0.0), (1.0, 0.3)])
            words = cutlist.load_words(self._words(tmp))
            env = cutlist.envelope(wav, 3.5, 6.0)
            r = cutlist.end_check(words, 5.2, "very quiet", env)
            self.assertFalse(r["ok"])
            self.assertAlmostEqual(r["suggest"], 4.95, delta=0.02)
            good = cutlist.end_check(words, 4.95, "very quiet", env)
            self.assertTrue(good["ok"], good["reason"])

    def _words(self, tmp):
        p = os.path.join(tmp, "w.json")
        with open(p, "w") as f:
            json.dump(words_from(SPEECH), f)
        return p


class TestManifest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.src = os.path.join(d, "source.bin")
        with open(self.src, "wb") as f:
            f.write(b"not really media")
        self.words = os.path.join(d, "w.json")
        with open(self.words, "w") as f:
            json.dump(words_from(SPEECH), f)
        self.approved = os.path.join(d, "essay.md")
        with open(self.approved, "w") as f:
            f.write(APPROVED)

    def tearDown(self):
        self.tmp.cleanup()

    def test_importers(self):
        script = os.path.join(self.tmp.name, "cuts.py")
        with open(script, "w") as f:
            f.write("import os\nCUTS = {\n  'a_30': [(1.0, 2.75), (3.6, 4.95)],\n}\nraise SystemExit\n")
        self.assertEqual(cutlist.spans_from_cuts_script(script), {"a_30": [(1.0, 2.75), (3.6, 4.95)]})
        tsv = os.path.join(self.tmp.name, "c.tsv")
        with open(tsv, "w") as f:
            f.write("# id\tin\tout\ttitle\nX\t00:00:01\t00:00:02.5\tt\nY\t00:00:00:12\t00:00:01:00\tu\n")
        self.assertEqual(cutlist.spans_from_vertcut_tsv(tsv, fps=24),
                         {"X": [(1.0, 2.5)], "Y": [(0.5, 1.0)]})
        edl = os.path.join(self.tmp.name, "edl.json")
        with open(edl, "w") as f:
            json.dump({"ranges": [{"source": "C1", "start": 2.42, "end": 6.85}]}, f)
        self.assertEqual(cutlist.spans_from_edl(edl), {"C1": [(2.42, 6.85)]})

    def test_frame_rounds_half_up(self):
        self.assertEqual(cutlist.frame(0.02, 25), 1)    # 0.5 frames: up, not to even
        self.assertEqual(cutlist.frame(0.06, 25), 2)    # 1.5 frames
        self.assertEqual(cutlist.frames({"in": 0.02, "out": 0.06}, 25), 1)

    def test_selects_windows_stay_inside_the_text(self):
        words = cutlist.load_words(self.words)
        approved = cutlist.load_approved(self.approved)
        rows = cutlist.selects(words, approved, min_s=1.0, max_s=5.0)
        self.assertTrue(rows)
        for r in rows:
            self.assertGreaterEqual(r["coverage"], cutlist.PASS_COVERAGE)

    def test_build_validate_and_frames(self):
        m = cutlist.build_manifest({"a_30": [(1.0, 2.75), (3.6, 4.95)]}, self.src, 25,
                                   self.words, self.approved, reframe={"face_x": 270})
        self.assertEqual(m["clips"][0]["spans"][1]["end_words"], "that very quiet.")
        self.assertEqual(m["source_sha256"], cutlist.sha256_file(self.src))
        # [round(in*fps), round(out*fps)): 25..69 is 44 frames, 90..124 is 34.
        self.assertEqual(cutlist.clip_frames(m["clips"][0], 25), 44 + 34)
        bad = json.loads(json.dumps(m))
        bad["clips"][0]["spans"][1]["in"] = 2.0
        with self.assertRaises(cutlist.CutlistError):
            cutlist.validate_manifest(bad)
        rows = cutlist.endcheck(m, audio=False)
        self.assertEqual([r["ok"] for r in rows], [True, True])


class FakeItem:
    def __init__(self, start, dur, src):
        self.start, self.dur, self.src, self.props = start, dur, src, {}

    def GetStart(self): return self.start
    def GetDuration(self): return self.dur
    def GetSourceStartFrame(self): return self.src
    def SetProperty(self, k, v): self.props[k] = v; return True
    def GetProperty(self, k): return self.props.get(k)


class FakeTimeline:
    def __init__(self, name, start=90000):
        self.name, self.start, self.items, self.settings = name, start, [], {}

    def GetName(self): return self.name
    def GetStartFrame(self): return self.start
    def GetEndFrame(self): return self.start + sum(i.dur for i in self.items)
    def SetSetting(self, k, v): self.settings[k] = v; return True
    def GetSetting(self, k): return self.settings.get(k)
    def GetItemListInTrack(self, kind, idx): return list(self.items)

    def DuplicateTimeline(self, name):
        t = FakeTimeline(name, self.start)
        t.items = [FakeItem(i.start, i.dur, i.src) for i in self.items]
        return t


class FakePool:
    def __init__(self, exclusive=True):
        self.exclusive, self.current = exclusive, None

    def CreateEmptyTimeline(self, name):
        self.current = FakeTimeline(name)
        return self.current

    def AppendToTimeline(self, infos):
        for info in infos:
            end = info["endFrame"] if self.exclusive else info["endFrame"] + 1
            self.current.items.append(FakeItem(info["recordFrame"], end - info["startFrame"],
                                               info["startFrame"]))
        return True


class FakeProject:
    def SetCurrentTimeline(self, tl): return True


class TestCut(unittest.TestCase):
    CLIP = {"name": "a_30", "spans": [{"in": 1.0, "out": 2.75}, {"in": 3.6, "out": 4.95}],
            "reframe": {"face_x": 270}}

    def test_build_clip_reads_back_exact_frames(self):
        r = cut.build_clip(FakeProject(), FakePool(), object(), self.CLIP, 25, "T")
        self.assertTrue(r["ok"], r["reason"])
        self.assertEqual((r["frames"], r["frames_expected"]), (78, 78))
        self.assertEqual(r["name"], "T_a_30 [auto]")

    def test_build_clip_catches_a_wrong_end_convention(self):
        r = cut.build_clip(FakeProject(), FakePool(exclusive=False), object(), self.CLIP, 25, "T")
        self.assertFalse(r["ok"])
        self.assertIn("expected", r["reason"])

    def test_reframe_math_matches_the_ep002_script(self):
        props = cut.reframe_props({"face_x": 270}, 1280)
        self.assertAlmostEqual(props["ZoomX"], 2.25 / (1080 / 1280))
        self.assertAlmostEqual(props["Pan"], (640 - 270) * 2.25)
        wide = FakeTimeline("T_a_30 [auto]")
        wide.items = [FakeItem(90000, 44, 25)]
        t = cut.build_tall(FakeProject(), wide, self.CLIP, 1280, "T")
        self.assertTrue(t["ok"], t["reason"])
        self.assertEqual(t["name"], "T_a_30_9x16 [auto]")
        self.assertIn("no reframe", cut.build_tall(FakeProject(), wide, {"name": "b"}, 1280, "T")["reason"])


if __name__ == "__main__":
    unittest.main()
