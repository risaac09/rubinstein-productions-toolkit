"""
Offline tests for rpresolve.trimreview and the trim-review command and
tool: silences from an envelope and from generated audio, fillers, repeats
and Whisper loops from synthetic words, times on a cut manifest's clips,
and a TSV that proposes and deletes nothing. Synthetic words and audio
only. The audio tests need ffmpeg; the numpy envelope check needs numpy.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import argparse
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
from rpresolve import cutlist, trimreview as tr  # noqa: E402

FFMPEG = cutlist.FFMPEG
has_ffmpeg = unittest.skipUnless(os.access(FFMPEG, os.X_OK), "ffmpeg missing")
try:
    import numpy  # noqa: F401
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


def W(word, start, end):
    return {"word": " " + word, "start": start, "end": end}


# Tone at 0-2 s, silence 2-5 s, tone 5-7 s, silence 7-8 s, tone 8-10 s (see tone_wav).
PIECES = [(2.0, 0.5), (3.0, 0.0), (2.0, 0.5), (1.0, 0.0), (2.0, 0.5)]
WORDS = [W("So", 0.1, 0.3), W("um,", 0.4, 0.6), W("I", 0.7, 0.8), W("I", 0.8, 0.9),
         W("think", 0.9, 1.3), W("you", 5.1, 5.2), W("know", 5.2, 5.4), W("very", 5.5, 5.7),
         W("very", 5.7, 5.9), W("good.", 5.9, 6.3), W("like", 8.2, 8.4), W("hmm", 8.5, 8.8),
         W("we", 9.0, 9.1), W("were", 9.1, 9.3), W("we", 9.3, 9.4), W("were", 9.4, 9.6)]


def env_of(pieces, hop=cutlist.HOP_S):
    """An envelope like cutlist.envelope's: -20 dBFS for tone, -90 for silence."""
    env, t = [], 0.0
    for secs, amp in pieces:
        for _ in range(int(round(secs / hop))):
            env.append((round(t, 3), -20.0 if amp else -90.0))
            t += hop
    return env


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


def by_kind(rows, kind):
    return [r for r in rows if r["kind"] == kind]


class TestRows(unittest.TestCase):
    def review(self, words=WORDS, **kw):
        return tr.review_range("/nonexistent.wav", words, 0.0, 10.0, env=env_of(PIECES), **kw)

    def test_silences_and_their_suggestions(self):
        rows, thr = self.review()
        self.assertEqual(thr, -45.0)
        s = by_kind(rows, tr.SILENCE)
        self.assertEqual([(r["start"], r["end"]) for r in s], [(2.0, 5.0), (7.0, 8.0)])
        self.assertEqual([r["suggestion"] for r in s], [tr.CUT, tr.KEEP])
        self.assertEqual([r["confidence"] for r in s], ["high", "high"])
        rows, _ = self.review(min_silence_s=1.5)
        self.assertEqual(len(by_kind(rows, tr.SILENCE)), 1)
        rows, _ = self.review(cut_s=5, tighten_s=2)
        self.assertEqual(by_kind(rows, tr.SILENCE)[0]["suggestion"], tr.TIGHTEN)

    def test_a_word_inside_a_silence_lowers_its_confidence(self):
        rows, _ = self.review(words=WORDS + [W("mm", 3.0, 3.2)])
        s = by_kind(rows, tr.SILENCE)[0]
        self.assertEqual(s["confidence"], "medium")
        self.assertIn("Whisper heard: mm", s["text"])
        rows, _ = tr.review_range("/x.wav", None, 0.0, 10.0, env=env_of(PIECES))
        self.assertEqual(by_kind(rows, tr.SILENCE)[0]["confidence"], "medium")
        self.assertIn("no words", by_kind(rows, tr.SILENCE)[0]["text"])

    def test_fillers_hard_and_soft(self):
        rows, _ = self.review()
        f = by_kind(rows, tr.FILLER)
        self.assertEqual([(r["text"], r["confidence"], r["suggestion"]) for r in f],
                         [("um,", "high", tr.CUT), ("hmm", "medium", tr.CUT)])
        rows, _ = self.review(soft=True)
        soft = [r for r in by_kind(rows, tr.FILLER) if r["confidence"] == "low"]
        self.assertEqual([(r["text"], r["suggestion"]) for r in soft],
                         [("you know", tr.KEEP), ("like", tr.KEEP)])

    def test_repeats(self):
        rows, _ = self.review()
        reps = by_kind(rows, tr.REPEAT)
        self.assertEqual([(r["text"], r["confidence"], r["suggestion"]) for r in reps],
                         [("I I", "medium", tr.TIGHTEN), ("very very", "low", tr.KEEP),
                          ("we were we were", "medium", tr.TIGHTEN)])

    def test_a_whisper_loop_is_one_row_and_hides_the_rows_inside_it(self):
        loop = [W(w, 1.0 + 0.2 * i, 1.2 + 0.2 * i) for i, w in
                enumerate(["and", "then", "um", "and", "then", "um", "and", "then", "um"])]
        rows, _ = tr.review_range("/x.wav", loop, 0.0, 10.0, audio=False)
        self.assertEqual([r["kind"] for r in rows], [tr.LOOP])
        self.assertEqual(rows[0]["suggestion"], tr.KEEP)
        self.assertIn("re-transcribe", rows[0]["text"])

    def test_auto_threshold(self):
        env = [(i * 0.01, -60.0 if i % 10 < 3 else -20.0) for i in range(1000)]
        self.assertEqual(tr.threshold(env, "auto"), -50.0)
        self.assertEqual(tr.threshold([(0, -200.0)] * 10, "auto"), -70.0)
        self.assertEqual(tr.threshold([(0, -10.0)] * 10, "auto"), -30.0)
        self.assertEqual(tr.threshold(env, -40), -40.0)

    def test_tsv_has_the_columns_and_no_action_but_proposals(self):
        rows, _ = self.review(soft=True)
        text = tr.tsv([{**r, "clip": "", "span": "", "source_start": r["start"],
                        "source_end": r["end"]} for r in rows])
        lines = text.splitlines()
        self.assertEqual(lines[0].split("\t")[:6],
                         ["start", "end", "kind", "text", "confidence", "suggestion"])
        self.assertTrue(all(len(line.split("\t")) == len(tr.COLUMNS) for line in lines))
        self.assertEqual({line.split("\t")[5] for line in lines[1:]} - {tr.KEEP, tr.TIGHTEN, tr.CUT},
                         set())
        self.assertIn("Nothing is cut", tr.summary(rows))


class TestManifest(unittest.TestCase):
    def test_times_run_along_each_clip(self):
        m = {"source_path": "/x.wav", "fps": 25, "words": "/x.json",
             "clips": [{"name": "one", "spans": [{"in": 0.0, "out": 2.0}, {"in": 5.0, "out": 10.0}]}]}
        seen = []

        def fake_range(source, words, a, b, **kw):
            seen.append((a, b))
            return [r for r in [tr._row(8.2, 8.4, tr.FILLER, "like", "low", tr.KEEP),
                                tr._row(0.4, 0.6, tr.FILLER, "um", "high", tr.CUT)]
                    if a <= r["start"] < b], None
        real = tr.review_range
        tr.review_range = fake_range
        try:
            r = tr.review_manifest(m, words=WORDS)
        finally:
            tr.review_range = real
        self.assertEqual(seen, [(0.0, 2.0), (5.0, 10.0)])
        self.assertEqual([(x["clip"], x["span"], x["start"], x["source_start"]) for x in r["rows"]],
                         [("one", 1, 0.4, 0.4), ("one", 2, 5.2, 8.2)])

    def test_is_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            m = os.path.join(d, "m.json")
            with open(m, "w", encoding="utf-8") as f:
                json.dump({"clips": [], "source_path": "/x"}, f)
            other = os.path.join(d, "words.json")
            with open(other, "w", encoding="utf-8") as f:
                json.dump({"segments": []}, f)
            self.assertTrue(tr.is_manifest(m))
            self.assertFalse(tr.is_manifest(other))
            self.assertFalse(tr.is_manifest(os.path.join(d, "a.wav")))


@has_ffmpeg
class TestAudio(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.wav = os.path.join(cls.tmp.name, "take.wav")
        tone_wav(cls.wav, PIECES)
        cls.words = os.path.join(cls.tmp.name, "take.words.json")
        with open(cls.words, "w", encoding="utf-8") as f:
            json.dump({"segments": [{"words": WORDS}]}, f)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_silences_from_the_audio(self):
        r = tr.review_source(self.wav, cutlist.load_words(self.words))
        s = by_kind(r["rows"], tr.SILENCE)
        self.assertEqual(len(s), 2)
        self.assertAlmostEqual(s[0]["start"], 2.0, delta=0.02)
        self.assertAlmostEqual(s[0]["end"], 5.0, delta=0.02)
        self.assertEqual([x["source_start"] for x in r["rows"]], [x["start"] for x in r["rows"]])

    def test_a_range_clips_its_silences(self):
        r = tr.review_source(self.wav, None, ranges=[(3.0, 6.0)])
        s = by_kind(r["rows"], tr.SILENCE)
        self.assertEqual(len(s), 1)
        self.assertAlmostEqual(s[0]["start"], 3.0, delta=0.02)
        self.assertIn("head of the range", s[0]["text"])

    @unittest.skipUnless(HAS_NUMPY, "numpy not installed")
    def test_numpy_envelope_matches_cutlist(self):
        a = tr.envelope(self.wav, 1.0, 3.0)
        b = cutlist.envelope(self.wav, 1.0, 3.0)
        self.assertEqual(len(a), len(b))
        self.assertTrue(all(abs(x[0] - y[0]) < 1e-9 and abs(x[1] - y[1]) < 1e-6
                            for x, y in zip(a, b)))

    def cli(self, *argv, **extra):
        ns = dict(input=argv[0], words=None, out=None, silence_db="-45", min_silence=0.8,
                  tighten=1.2, cut=2.5, soft_fillers=False, no_audio=False, markers=False,
                  timeline=None, project=None, project_id=None, dry_run=False, plan_sha=None)
        ns.update(extra)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = resolve_workflow.cmd_trim_review(argparse.Namespace(**ns))
        return status, out.getvalue(), err.getvalue()

    def test_cli_source_to_stdout_and_file(self):
        status, out, err = self.cli(self.wav, words=self.words)
        self.assertEqual(status, 0)
        self.assertEqual(out.splitlines()[0].split("\t")[0], "start")
        self.assertIn("silence", out)
        self.assertIn("Nothing is cut", err)
        dest = os.path.join(self.tmp.name, "review.tsv")
        status, _, err = self.cli(self.wav, words=self.words, out=dest, silence_db="auto")
        self.assertEqual(status, 0)
        with open(dest, encoding="utf-8") as f:
            self.assertGreater(len(f.read().splitlines()), 3)
        status, _, err = self.cli(self.wav, silence_db="loud")
        self.assertEqual(status, 1)

    def test_cli_manifest(self):
        m = cutlist.build_manifest({"c1": [(0.0, 6.0)]}, self.wav, 25, self.words,
                                   self.words, source_sha256="0" * 64,
                                   words=cutlist.load_words(self.words))
        path = os.path.join(self.tmp.name, "manifest.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(m, f)
        status, out, _ = self.cli(path)
        self.assertEqual(status, 0)
        rows = [line.split("\t") for line in out.splitlines()[1:]]
        self.assertTrue(rows)
        self.assertEqual({r[tr.COLUMNS.index("clip")] for r in rows}, {"c1"})

    def test_cli_markers_need_a_timeline_and_project(self):
        status, _, err = self.cli(self.wav, words=self.words, markers=True)
        self.assertEqual(status, 1)
        self.assertIn("--timeline and --project", err)

    def test_mcp_tool_writes_the_tsv(self):
        from rpresolve.mcp import schema, server
        from rpresolve.mcp.registry import ToolContext
        tool = server.build_registry().get("trim_review")
        dest = os.path.join(self.tmp.name, "mcp.tsv")
        args = schema.with_defaults(tool.input_schema, {"source": self.wav, "words": self.words,
                                                        "out": dest})
        self.assertEqual(schema.validate(tool.input_schema, args), [])
        r = tool.handler(args, ToolContext())
        self.assertEqual(r["file"], dest)
        self.assertEqual(r["total"], len(r["rows"]))
        self.assertGreater(r["counts"]["kind"]["silence"], 0)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            tool.handler({**args, "manifest": dest}, ToolContext())


if __name__ == "__main__":
    unittest.main()
