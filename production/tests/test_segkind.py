"""
Tests for tools/vertcut/lib/segkind.py and rpresolve.guard: what a Whisper
segment is decided from numbers, withheld text replaced by a label, and
every transcript reader in the toolkit going through the guard.

Every transcript here is synthetic: placeholder tokens for words, and
numpy-made audio from segkind_synth (sung notes, a syllable stream, wind, a
chord stream). No recorded speech or song, no lyrics.

Run: /usr/bin/python3 -m unittest production.tests.test_segkind -v
"""

import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
PROD = HERE.parent
ROOT = PROD.parent
sys.path.insert(0, str(PROD))
sys.path.insert(0, str(ROOT / "tools" / "vertcut" / "lib"))

import segkind  # noqa: E402
from rpresolve import cutlist, guard, trimreview  # noqa: E402

try:
    import numpy as np  # noqa: F401
    from . import segkind_synth as syn  # noqa: E402
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

# Word tokens that never appear in any label or reason code, so a leak into
# printed output is a plain substring check.
LYRIC = "zorblat quindle farrow vemble oskit"
SPEECH = "alpha beta gamma delta epsilon zeta eta theta"
SPEECH2 = "kappa lambda mu nu xi omicron pi rho sigma"


def seg(start, end, text, **extra):
    """One Whisper-shaped segment with evenly spaced word timestamps."""
    toks = text.split()
    n = len(toks)
    words = [{"word": " " + t, "start": round(start + (end - start) * i / n, 3),
              "end": round(start + (end - start) * (i + 1) / n, 3), "probability": 0.9}
             for i, t in enumerate(toks)]
    d = {"id": 0, "seek": 0, "start": start, "end": end, "text": " " + text, "words": words,
         "avg_logprob": -0.3, "compression_ratio": 1.4, "no_speech_prob": 0.05,
         "temperature": 0.0}
    d.update(extra)
    return d


def doc(*segs):
    return {"language": "en", "text": " ".join(s["text"].strip() for s in segs),
            "segments": list(segs)}


def write(tmp, name, data):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return p


class TestWhisperNumbers(unittest.TestCase):
    """No audio: the decision comes from Whisper's own numbers and text counts."""

    def kinds(self, *segs, **kw):
        return [(r["kind"], r["withheld"]) for r in segkind.classify(doc(*segs), None, **kw)]

    def test_ordinary_speech_is_kept(self):
        self.assertEqual(self.kinds(seg(0.0, 3.0, SPEECH), seg(3.2, 6.0, SPEECH2)),
                         [("speech", False), ("speech", False)])

    def test_a_repeated_phrase_is_a_hallucination(self):
        loop = " ".join(["thank you"] * 6)
        r = segkind.classify(doc(seg(0.0, 4.0, loop)), None)[0]
        self.assertEqual((r["kind"], r["withheld"], r["label"]),
                         ("hallucination", True, "[inaudible]"))
        self.assertIn("repeated_tokens", r["reasons"])

    def test_identical_segments_in_a_row_are_a_loop(self):
        segs = [seg(i * 2.0, i * 2.0 + 1.9, "so so good") for i in range(3)]
        self.assertEqual(self.kinds(*segs), [("hallucination", True)] * 3)

    def test_whispers_compression_gate_and_zero_length_words(self):
        r = segkind.classify(doc(seg(0.0, 3.0, SPEECH, compression_ratio=3.1)), None)[0]
        self.assertEqual(r["kind"], "hallucination")
        s = seg(0.0, 3.0, SPEECH)
        for w in s["words"]:
            w["end"] = w["start"]
        self.assertEqual(self.kinds(s), [("hallucination", True)])

    def test_whispers_own_music_marker(self):
        self.assertEqual(self.kinds(seg(0.0, 3.0, "♪ " + LYRIC + " ♪")),
                         [("music", True)])
        self.assertEqual(self.kinds(seg(0.0, 3.0, "[Music]")), [("music", True)])

    def test_an_empty_segment_is_neither_shown_nor_withheld(self):
        r = segkind.classify(doc(seg(0.0, 1.0, "")), None)[0]
        self.assertTrue(r["empty"])
        self.assertFalse(r["withheld"])

    def test_text_signals_do_not_read_the_words(self):
        # the features of a segment are numbers and reason codes only
        r = segkind.classify(doc(seg(0.0, 3.0, LYRIC)), None)[0]
        flat = json.dumps(r)
        for tok in LYRIC.split():
            self.assertNotIn(tok, flat)


class TestRedactAndGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_redact_replaces_withheld_text_with_its_label(self):
        d = doc(seg(0.0, 3.0, SPEECH), seg(3.2, 6.0, " ".join(["thank you"] * 6)))
        res = segkind.classify(d, None)
        safe = segkind.redact(d, res, audio_checked=False)
        self.assertTrue(segkind.is_safe(safe))
        held = safe["segments"][1]
        self.assertEqual((held["text"], held["words"], held["kind"], held["redacted"]),
                         ("[inaudible]", [], "hallucination", True))
        self.assertNotIn("thank", json.dumps(safe))
        self.assertEqual(safe["segments"][0]["text"].strip(), SPEECH)
        self.assertEqual(safe["text"], SPEECH + " [inaudible]")
        self.assertEqual(safe["safety"]["withheld_segments"], 1)
        self.assertIn("warning", safe["safety"])     # no audio: singing was not screened

    def test_a_safe_json_round_trips_and_keeps_its_kinds(self):
        d = doc(seg(0.0, 3.0, SPEECH), seg(5.0, 8.0, "♪ " + LYRIC))
        safe = segkind.redact(d, segkind.classify(d, None), audio_checked=True)
        p = write(self.tmp.name, "clip.json", safe)
        data, res, checked = segkind.load_guarded(p)
        self.assertTrue(checked)
        self.assertEqual([r["kind"] for r in res], ["speech", "music"])
        self.assertEqual(data["segments"][1]["text"], "[music]")

    def test_a_raw_json_is_screened_in_memory_and_says_so(self):
        p = write(self.tmp.name, "clip.json", doc(seg(0.0, 3.0, SPEECH),
                                                   seg(5.0, 8.0, "♪ " + LYRIC)))
        data, blocks, checked = guard.load(p)
        self.assertFalse(checked)
        self.assertEqual([(b["kind"], b["label"]) for b in blocks], [("music", "[music]")])
        self.assertNotIn("zorblat", json.dumps(data))

    def test_the_raw_file_is_refused_by_name(self):
        p = write(self.tmp.name, "clip.raw.json", doc(seg(0.0, 3.0, SPEECH)))
        with self.assertRaises(guard.GuardError):
            guard.load(p)
        with self.assertRaises(cutlist.CutlistError):
            cutlist.load_words(p)

    def test_overrides_win_in_either_direction(self):
        tsv = os.path.join(self.tmp.name, "sung.tsv")
        with open(tsv, "w") as f:
            f.write("clip\tstart\tend\tkind\n*\t0:00\t0:03\tsung\nclip\t3.2\t6.0\tspeech\n")
        ov = segkind.read_overrides(tsv)
        d = doc(seg(0.0, 3.0, SPEECH), seg(3.2, 6.0, " ".join(["thank you"] * 6)))
        res = segkind.classify(d, None, overrides=ov, clip="clip")
        self.assertEqual([(r["kind"], r["withheld"], r["source"]) for r in res],
                         [("sung", True, "override"), ("speech", False, "override")])
        # a safe JSON has no text left to restore: promotion is blocked, not faked
        safe = segkind.redact(d, segkind.classify(d, None), audio_checked=False)
        res2 = segkind.classify(safe, None, overrides=ov, clip="clip")
        self.assertTrue(res2[1]["promotion_blocked"])
        self.assertTrue(res2[1]["withheld"])

    def test_kinds_cli_prints_kinds_and_times_never_text(self):
        p = write(self.tmp.name, "clip.json", doc(seg(0.0, 3.0, SPEECH),
                                                   seg(5.0, 8.0, "♪ " + LYRIC)))
        out = io.StringIO()
        self.assertEqual(segkind.main(["kinds", p, "--segments"], out=out), 0)
        text = out.getvalue()
        self.assertIn("music", text)
        for tok in (LYRIC + " " + SPEECH).split():
            self.assertNotIn(tok, text)


@unittest.skipUnless(HAVE_NUMPY, "numpy missing (the audio screen needs it)")
class TestSyntheticAudio(unittest.TestCase):
    """The singing screen on numpy-made audio. Thresholds are the module's;
    these cases are the clear ones each kind must get right."""

    def classify(self, x, *segs):
        an = segkind.finish(segkind.analyze(x))
        return segkind.classify(doc(*segs), an)

    def test_a_syllable_stream_is_speech(self):
        res = self.classify(syn.speech_like(10.0, seed=1),
                            seg(0.5, 4.5, SPEECH), seg(4.6, 9.5, SPEECH2))
        self.assertEqual([(r["kind"], r["withheld"]) for r in res],
                         [("speech", False), ("speech", False)])

    def test_held_vibrato_notes_are_sung(self):
        res = self.classify(syn.sung(10.0, seed=2), seg(0.2, 5.0, LYRIC), seg(5.1, 9.8, LYRIC))
        for r in res:
            self.assertIn(r["kind"], ("sung", "music"))
            self.assertTrue(r["withheld"])
            self.assertIn("held_pitch_plateaus", r["reasons"])

    def test_a_chord_stream_is_music(self):
        res = self.classify(syn.instrument(10.0, seed=5),
                            seg(0.0, 9.9, "mm hmm", avg_logprob=-0.9, no_speech_prob=0.4))
        self.assertEqual((res[0]["kind"], res[0]["withheld"]), ("music", True))

    def test_wind_with_a_repeated_phrase_is_a_hallucination(self):
        res = self.classify(syn.wind(10.0, seed=4),
                            seg(0.0, 9.9, " ".join(["thank you"] * 4), avg_logprob=-1.3,
                                no_speech_prob=0.7))
        self.assertEqual((res[0]["kind"], res[0]["withheld"]), ("hallucination", True))

    def test_a_song_bleeds_into_its_neighbours(self):
        x = np.concatenate([syn.speech_like(5.0, seed=6), syn.sung(6.0, seed=7),
                            syn.speech_like(5.0, seed=8)])
        res = self.classify(x, seg(0.3, 4.8, SPEECH), seg(5.2, 10.8, LYRIC),
                            seg(11.3, 15.8, SPEECH))
        self.assertIn(res[1]["kind"], ("sung", "music"))
        for r in (res[0], res[2]):
            self.assertEqual((r["kind"], r["lean"], r["withheld"]), ("uncertain", "sung", True))
            self.assertEqual(r["reasons"], ["next_to_sung_segment"])

    def test_the_wav_path_screens_from_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "clip.wav")
            syn.write_wav(wav, syn.sung(6.0, seed=9))
            p = write(tmp, "clip.json", doc(seg(0.2, 5.8, LYRIC)))
            data, blocks, checked = guard.load(p, wav)
            self.assertTrue(checked)
            self.assertEqual(len(blocks), 1)
            self.assertNotIn("zorblat", json.dumps(data))


class TestGuardWiring(unittest.TestCase):
    """The readers downstream: words lists carry the withheld spans, and
    selects, endcheck and trim review act on them."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # speech, then a Whisper-marked music segment, then speech
        self.path = write(self.tmp.name, "clip.json",
                          doc(seg(0.0, 4.0, "the river keeps its own time"),
                              seg(6.0, 11.0, "♪ " + LYRIC + " ♪"),
                              seg(13.0, 17.0, "most people act as if the map is the journey")))
        self.words = cutlist.load_words(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_words_withholds_and_carries_the_blocks(self):
        self.assertNotIn("zorblat", " ".join(w["word"] for w in self.words))
        self.assertEqual([(b["kind"], b["start"], b["end"]) for b in self.words.blocks],
                         [("music", 6.0, 11.0)])
        self.assertFalse(self.words.audio_checked)
        self.assertEqual(cutlist.blocks_of(list(self.words)), [])   # a plain list has none

    def test_all_withheld_is_an_error_that_says_why(self):
        p = write(self.tmp.name, "song.json", doc(seg(0.0, 4.0, "♪ " + LYRIC)))
        with self.assertRaises(cutlist.CutlistError) as cm:
            cutlist.load_words(p)
        self.assertIn("withheld", str(cm.exception))
        self.assertNotIn("zorblat", str(cm.exception))

    def test_selects_never_proposes_across_a_withheld_span(self):
        approved = cutlist.content_tokens("the river keeps its own time. most people act as if "
                                          "the map is the journey")
        rows = cutlist.selects(self.words, approved, min_s=1.0, max_s=20.0)
        for r in rows:
            self.assertIsNone(cutlist.overlaps_block(self.words.blocks, r["in"], r["out"]))
            self.assertNotIn("zorblat", r["text"])

    def test_endcheck_fails_a_span_over_withheld_text(self):
        src = os.path.join(self.tmp.name, "clip.mov")
        open(src, "wb").close()
        m = {"version": 1, "source_path": src, "fps": 25, "words": self.path,
             "approved_text": None,
             "clips": [{"name": "a", "spans": [{"in": 0.0, "out": 3.9, "end_words": "own time"},
                                                {"in": 3.0, "out": 9.0, "end_words": ""}]}]}
        rows = cutlist.endcheck(m, approved=cutlist.content_tokens("the river keeps its own time"),
                                audio=False)
        self.assertTrue(rows[0]["ok"])
        self.assertFalse(rows[1]["ok"])
        self.assertIn("withheld as music", rows[1]["reason"])

    def test_trim_review_reports_a_withheld_span_as_non_speech(self):
        # through review_source, which indexes the words first (the MCP tool's path)
        rows = trimreview.review_source(None, self.words, [(0.0, 17.0)], audio=False)["rows"]
        held = [r for r in rows if r["kind"] == trimreview.NONSPEECH]
        self.assertEqual([(r["start"], r["end"], r["suggestion"]) for r in held],
                         [(6.0, 11.0, trimreview.KEEP)])
        self.assertEqual(held[0]["text"], "[music] withheld as music")


class TestVertcutCut(unittest.TestCase):
    def setUp(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("Pillow missing (vertcut needs it)")
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "vertcut_cut", ROOT / "tools" / "vertcut" / "lib" / "vertcut_cut.py")
        self.v = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.v)
        self.tmp = tempfile.TemporaryDirectory()
        self.path = write(self.tmp.name, "clip.json",
                          doc(seg(0.0, 4.0, "the river keeps its own time"),
                              seg(6.0, 11.0, "♪ " + LYRIC + " ♪"),
                              seg(13.0, 17.0, "most people act as if the map is the journey")))

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_withheld_span_becomes_a_label_cue_with_no_words(self):
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            words = self.v.load_words(self.path)
        self.assertIn("not screened for singing", err.getvalue())
        blocks = cutlist.blocks_of(words)
        cues = self.v.group_cues(words, 2.0, 15.0, 3, 32, 0.6, 16, blocks, self.v.LABEL_SETS["music"])
        labels = [c for c in cues if c.get("label")]
        self.assertEqual([(c["text"], round(c["start"], 2), round(c["end"], 2)) for c in labels],
                         [("[music]", 4.0, 9.0)])
        self.assertNotIn("zorblat", " ".join(c["text"] for c in cues))
        for a, b in zip(cues, cues[1:]):          # a label never runs over the next cue
            self.assertLessEqual(a["end"], b["start"] + 1e-9)
        self.assertEqual(self.v.group_cues(words, 2.0, 15.0, 3, 32, 0.6, 16, blocks,
                                           self.v.LABEL_SETS["none"]), [c for c in cues
                                                                        if not c.get("label")])

    def test_music_overlaps_names_the_span(self):
        words = cutlist.load_words(self.path)
        hit = self.v.music_overlaps(cutlist.blocks_of(words), 3.0, 7.0)
        self.assertEqual([(b["kind"], b["start"], b["end"]) for b in hit], [("music", 6.0, 11.0)])
        self.assertEqual(self.v.music_overlaps(cutlist.blocks_of(words), 12.0, 16.0), [])


class TestEveryReaderIsGuarded(unittest.TestCase):
    """A source scan: a module that reads a `segments` key either goes
    through guard.load, is the screen the guard calls, or is named here with
    why it may read without the guard. A new transcript reader fails this
    until it is routed or explained."""

    UNGUARDED = {
        "tools/vertcut/lib/segkind.py": "the screen the guard calls",
        "production/rpresolve/transcribe.py": "writes the Whisper JSON; its fake backend copies "
                                              "segments from a file into one",
        "production/rpresolve/wordsnap.py": "moves word times and writes the JSON back as "
                                            "given, text untouched; every consumer of its "
                                            "output reads it through cutlist.load_words",
        "production/rpresolve/motionlint.py": "a motion spec's segments, not a transcript",
        "production/rpresolve/mcp/tools_offline.py": "the measure tool's time segments argument, "
                                                     "not a transcript",
    }
    READS = re.compile(r"""(\.get\(\s*["']segments["']\s*\)|\[\s*["']segments["']\s*\])""")

    def test_segment_walkers_are_the_guard_or_named(self):
        found = {}
        for sub in ("production/rpresolve", "tools/vertcut/lib"):
            for p in sorted((ROOT / sub).rglob("*.py")):
                rel = str(p.relative_to(ROOT))
                text = p.read_text(encoding="utf-8")
                if not self.READS.search(text):
                    continue
                if "guard.load(" in text:
                    continue
                found[rel] = True
        unexplained = sorted(set(found) - set(self.UNGUARDED))
        self.assertEqual(unexplained, [], "transcript readers that skip rpresolve.guard: "
                         "route them through guard.load or cutlist.load_words, or name them "
                         "in UNGUARDED with the reason")
        stale = sorted(set(self.UNGUARDED) - set(found))
        self.assertEqual(stale, [], "UNGUARDED names files that no longer read segments")


if __name__ == "__main__":
    unittest.main()
