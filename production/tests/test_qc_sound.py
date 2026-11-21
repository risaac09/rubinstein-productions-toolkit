"""
Tests for the sound rows of rpresolve.qc (VP-121): lra, true_peak_per_cut,
silence_at_cut, speech_vs_rest, phone_proxy and stems_sum, on audio made
here with ffmpeg lavfi aevalsrc and numpy (48 kHz, a few seconds, 16-bit
WAV). Every threshold is a working default; these tests pin the rows, not
the numbers' standing. Skipped where ffmpeg is missing ("ffmpeg missing") or
numpy is ("needs numpy").

Run: /usr/bin/python3 -m unittest production.tests.test_qc_sound -v
"""

import csv
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import delivercheck as dc, qc  # noqa: E402

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

WORKFLOW = Path(__file__).resolve().parent.parent / "resolve_workflow.py"
TONE = "sin(2*PI*1000*t)"


def _have_ffmpeg():
    return os.access(dc.FFMPEG, os.X_OK) and os.access(dc.FFPROBE, os.X_OK)


def _wav(path, expr, d):
    proc = subprocess.run(
        [dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"aevalsrc='{expr}':s=48000:d={d}", "-c:a", "pcm_s16le", path],
        stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)


def _write16(path, x):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def _starts(path, starts):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["shot", "start_s"])
        for i, s in enumerate(starts, 1):
            w.writerow([f"S{i}", s])
    return path


def _words(path, spans):
    words = [{"word": "w", "start": a, "end": b} for a, b in spans]
    with open(path, "w") as fh:
        json.dump({"segments": [{"words": words}]}, fh)
    return path


@unittest.skipUnless(_have_ffmpeg(), "ffmpeg missing")
class SoundRows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        t = cls.t = cls._tmp.name
        p = lambda n: os.path.join(t, n)  # noqa: E731
        cls.hot = p("hot.wav")        # 2 s at 0.3, 2 s at full scale, 2 s at 0.3
        _wav(cls.hot, f"(0.3+0.7*between(t,2,4))*{TONE}", 6)
        cls.calm = p("calm.wav")
        _wav(cls.calm, f"0.3*{TONE}", 6)
        cls.gap = p("gap.wav")        # 0.4 s of digital silence from 3.0 s
        _wav(cls.gap, f"0.3*{TONE}*(1-between(t,3.0,3.4))", 6)
        cls.blip = p("blip.wav")      # 40 ms of silence from 3.0 s
        _wav(cls.blip, f"0.3*{TONE}*(1-between(t,3.0,3.04))", 6)
        # speech 1 to 3 s over a bed: the bed louder than the speech, then quieter
        cls.buried = p("buried.wav")
        _wav(cls.buried, f"0.25*(2*random(0)-1)+0.1*{TONE}*between(t,1,3)", 6)
        cls.clear = p("clear.wav")
        _wav(cls.clear, f"0.02*(2*random(0)-1)+0.6*{TONE}*between(t,1,3)", 6)
        cls.words = _words(p("words.json"), [(1.0, 1.5), (1.6, 3.0)])
        # PRI-22: a steady bed with two speech spans; the second is buried (planted fault)
        cls.bed_ok = p("bed_ok.wav")
        _wav(cls.bed_ok, f"0.05*(2*random(0)-1)+0.6*{TONE}*(between(t,2,3.5)+between(t,6,7.5))", 10)
        cls.bed_bad = p("bed_bad.wav")
        _wav(cls.bed_bad, f"0.05*(2*random(0)-1)+{TONE}*(0.6*between(t,2,3.5)+0.03*between(t,6,7.5))", 10)
        cls.bed_words = _words(p("bed_words.json"), [(2.0, 3.5), (6.0, 7.5)])
        # room tone, then a voice's loud, decaying tail in the short gap beside the first span
        cls.bed_tail = p("bed_tail.wav")
        _wav(cls.bed_tail, f"0.02*(2*random(0)-1)+{TONE}*(0.3*between(t,2,3.5)+0.2*exp(-6*(t-3.5))*between(t,3.7,4.8)+0.3*between(t,5.0,6.6))", 9)
        cls.bed_tail_words = _words(p("bed_tail_words.json"), [(2.0, 3.5), (5.0, 6.6)])
        cls.bed_short_words = _words(p("bed_short_words.json"), [(0.5, 4.5), (5.0, 9.5)])
        cls.low = p("low.wav")        # energy only at 100 Hz
        _wav(cls.low, "0.5*sin(2*PI*100*t)", 6)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def by(self, path, **kw):
        return {r["check"]: r for r in qc.run(path, **kw)["checks"]}

    def test_lra_is_reported_and_never_checked(self):
        row = self.by(self.calm)["lra"]
        self.assertEqual(row["status"], qc.SKIP)
        self.assertIn("LU", row["found"])
        self.assertIn("not checked", row["expected"])

    def test_true_peak_per_cut_names_the_shot_over_the_ceiling(self):
        row = self.by(self.hot, shot_cut_times=[2.0, 4.0])["true_peak_per_cut"]
        self.assertEqual(row["status"], qc.FAIL)
        self.assertIn("shot 2 (2.00s to 4.00s)", row["found"])
        self.assertNotIn("shot 1", row["found"])
        self.assertNotIn("shot 3", row["found"])

    def test_true_peak_per_cut_passes_a_calm_file_and_skips_without_cuts(self):
        self.assertEqual(self.by(self.calm, shot_cut_times=[2.0, 4.0])["true_peak_per_cut"]["status"],
                         qc.PASS)
        self.assertEqual(self.by(self.calm)["true_peak_per_cut"]["status"], qc.SKIP)

    @unittest.skipUnless(np, "needs numpy")
    def test_silence_at_cut_warns_on_a_gap_across_the_cut(self):
        row = self.by(self.gap, shot_cut_times=[3.2])["silence_at_cut"]
        self.assertEqual(row["status"], qc.WARN)
        self.assertIn("cut 1 at 3.20s", row["found"])
        self.assertIn("working default", row["expected"])

    @unittest.skipUnless(np, "needs numpy")
    def test_silence_at_cut_ignores_a_blip_under_two_frames_and_a_cut_in_tone(self):
        self.assertEqual(self.by(self.blip, shot_cut_times=[3.02])["silence_at_cut"]["status"], qc.PASS)
        self.assertEqual(self.by(self.gap, shot_cut_times=[1.0])["silence_at_cut"]["status"], qc.PASS)
        self.assertEqual(self.by(self.gap)["silence_at_cut"]["status"], qc.SKIP)

    def test_speech_vs_rest_warns_when_the_rest_is_louder(self):
        words = qc.load_words(self.words)
        row = self.by(self.buried, words=words)["speech_vs_rest"]
        self.assertEqual(row["status"], qc.WARN, row)
        self.assertEqual(self.by(self.clear, words=words)["speech_vs_rest"]["status"], qc.PASS)

    def test_voice_vs_bed_names_the_buried_span_only(self):
        words = qc.load_words(self.bed_words)
        bad = self.by(self.bed_bad, words=words)["voice_vs_bed"]
        self.assertEqual(bad["status"], qc.WARN, bad)
        self.assertIn("5.80s to 7.70s", bad["found"])
        self.assertNotIn("1.80s", bad["found"])
        self.assertIn("working default", bad["expected"])
        self.assertEqual(self.by(self.bed_ok, words=words)["voice_vs_bed"]["status"], qc.PASS)

    def test_voice_vs_bed_does_not_read_a_decaying_voice_tail_as_a_bed(self):
        row = self.by(self.bed_tail, words=qc.load_words(self.bed_tail_words))["voice_vs_bed"]
        self.assertEqual(row["status"], qc.PASS, row)

    def test_voice_vs_bed_is_not_measurable_without_a_long_gap(self):
        row = self.by(self.bed_ok, words=qc.load_words(self.bed_short_words))["voice_vs_bed"]
        self.assertEqual(row["status"], qc.SKIP, row)
        self.assertIn("not measurable", row["found"])

    def test_voice_vs_bed_skips_without_words_or_readable_spans(self):
        self.assertEqual(self.by(self.bed_ok)["voice_vs_bed"]["status"], qc.SKIP)
        short = qc.load_words(_words(os.path.join(self.t, "short.json"), [(1.0, 1.2)]))
        self.assertEqual(self.by(self.bed_ok, words=short)["voice_vs_bed"]["status"], qc.SKIP)

    def test_speech_vs_rest_skips_without_words(self):
        self.assertEqual(self.by(self.buried)["speech_vs_rest"]["status"], qc.SKIP)

    def test_phone_proxy_warns_on_energy_under_300_hz_only(self):
        row = self.by(self.low)["phone_proxy"]
        self.assertEqual(row["status"], qc.WARN, row)
        self.assertEqual(self.by(self.calm)["phone_proxy"]["status"], qc.PASS)

    @unittest.skipUnless(np, "needs numpy")
    def test_stems_sum_passes_when_they_sum_and_fails_when_they_do_not(self):
        n = 48000 * 4
        t = np.arange(n) / 48000
        a, b = 0.2 * np.sin(2 * np.pi * 440 * t), 0.2 * np.sin(2 * np.pi * 880 * t)
        p = lambda name: os.path.join(self.t, name)  # noqa: E731
        for name, x in (("a.wav", a), ("b.wav", b), ("mix.wav", a + b), ("badmix.wav", a + 0.5 * b)):
            _write16(p(name), x)
        stems = [p("a.wav"), p("b.wav")]
        good = self.by(p("mix.wav"), stems=stems)["stems_sum"]
        self.assertEqual(good["status"], qc.PASS, good)
        bad = self.by(p("badmix.wav"), stems=stems)["stems_sum"]
        self.assertEqual(bad["status"], qc.FAIL, bad)
        self.assertEqual(self.by(p("mix.wav"))["stems_sum"]["status"], qc.SKIP)
        _write16(p("short.wav"), b[:48000])  # one second of a four second stem
        self.assertEqual(self.by(p("mix.wav"), stems=[p("a.wav"), p("short.wav")])["stems_sum"]["status"],
                         qc.FAIL)

    def test_warn_rows_do_not_fail_the_table_and_sort_after_fail(self):
        r = qc.run(self.low)
        self.assertEqual(r["counts"][qc.WARN], 1)
        self.assertEqual(r["status"], "pass")
        self.assertIn("1 warn", qc.format_report(r))

    def test_every_threshold_can_be_overridden(self):
        th = qc.parse_overrides(["phone_drop_lu=100"])
        self.assertEqual(self.by(self.low, thresholds=th)["phone_proxy"]["status"], qc.PASS)


class Helpers(unittest.TestCase):
    def test_speech_spans_join_gaps_then_pad(self):
        words = [{"start": 1.0, "end": 1.5}, {"start": 1.8, "end": 2.0}, {"start": 5.0, "end": 5.5}]
        self.assertEqual(qc.speech_spans(words, 0.2, 0.5), [(0.8, 2.2), (4.8, 5.7)])
        self.assertEqual(qc.speech_spans(words, 0.05, 0.1)[0], (0.95, 1.55))
        # padding that makes spans touch merges them
        self.assertEqual(len(qc.speech_spans(words, 1.5, 0.1)), 1)

    def test_shot_cuts_reads_a_start_column_or_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(qc.shot_cuts(_starts(os.path.join(d, "a.csv"), [0, 2.5, 4])), [2.5, 4.0])
            plain = os.path.join(d, "b.csv")
            with open(plain, "w") as fh:
                fh.write("shot,note\nS1,x\nS2,y\n")
            self.assertIsNone(qc.shot_cuts(plain))
            bad = os.path.join(d, "c.csv")
            with open(bad, "w") as fh:
                fh.write("shot,start_s\nS1,soon\n")
            with self.assertRaises(dc.CheckError):
                qc.shot_cuts(bad)


@unittest.skipUnless(_have_ffmpeg(), "ffmpeg missing")
class SoundCli(unittest.TestCase):
    def test_help_documents_the_flags(self):
        out = subprocess.run([sys.executable, str(WORKFLOW), "qc", "--help"],
                             stdin=subprocess.DEVNULL, capture_output=True, text=True).stdout
        for word in ("--words", "--stems", "working default"):
            self.assertIn(word, out)

    def test_cli_prints_the_sound_rows(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "t.wav")
            _wav(f, f"0.3*{TONE}", 6)
            r = subprocess.run([sys.executable, str(WORKFLOW), "qc", f, "--json"],
                               stdin=subprocess.DEVNULL, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            names = {c["check"] for c in json.loads(r.stdout)["checks"]}
            self.assertTrue({"lra", "true_peak_per_cut", "silence_at_cut", "speech_vs_rest",
                             "phone_proxy", "stems_sum"} <= names)
            bad = subprocess.run([sys.executable, str(WORKFLOW), "qc", f, "--words",
                                  os.path.join(d, "none.json")], stdin=subprocess.DEVNULL,
                                 capture_output=True, text=True)
            self.assertEqual(bad.returncode, 1)


@unittest.skipUnless(np, "needs numpy")
class VoiceVsBedLocalBed(unittest.TestCase):
    """The bed is read per span from the gaps beside it, on synthetic frames
    (no audio: one momentary frame per 0.1 s)."""

    PAD = 0.2

    def _run(self, words, level, end, tail_s=0.0):
        """level(t, in_gap_start) -> LUFS; the speech pad is the row's own."""
        frames = [(i / 10, level(i / 10), 0) for i in range(int(end * 10))]
        ws = [{"word": "w", "start": a, "end": b} for a, b in words]
        return qc._sound_voice_vs_bed(frames, ws, qc.THRESHOLDS)

    @staticmethod
    def _levels(spans, gaps, voice=-22.0, quiet=-50.0):
        # spans: padded (a, b); gaps: {(a, b): LUFS}; everything else is quiet
        def f(t):
            if any(a <= t < b for a, b in spans):
                return voice
            return next((v for (a, b), v in gaps.items() if a <= t < b), quiet)
        return f

    # raw word gaps of 1.6 s: padded 1.2 s, short but readable (0.1 s in, 0.3 s out, 0.4 s left)
    WORDS = [(5, 9), (10.6, 14.6), (16.2, 20.2), (21.8, 25.8)]
    PADDED = [(4.8, 9.2), (10.4, 14.8), (16.0, 20.4), (21.6, 26.0)]

    def test_voice_over_a_local_music_bed_warns(self):
        words = [(5, 9), (15, 19), (25, 29)]
        padded = [(4.8, 9.2), (14.8, 19.2), (24.8, 29.2)]
        f = self._levels(padded, {(9.2, 14.8): -24.0})
        row = self._run(words, f, 35)
        self.assertEqual(row["status"], qc.WARN, row)
        self.assertIn("4.80s to 9.20s", row["found"])
        self.assertIn("14.80s to 19.20s", row["found"])
        self.assertNotIn("24.80s", row["found"])

    def test_music_in_short_gaps_warns_on_the_spans_beside_them(self):
        f = self._levels(self.PADDED, {(9.2, 10.4): -24.0, (14.8, 16.0): -24.0})
        row = self._run(self.WORDS, f, 35)
        self.assertEqual(row["status"], qc.WARN, row)
        for a in ("4.80s", "10.40s", "16.00s"):
            self.assertIn(a, row["found"])
        self.assertNotIn("21.60s", row["found"])

    def test_the_next_voice_starting_at_the_end_of_a_short_gap_is_not_a_bed(self):
        base = self._levels(self.PADDED, {})

        def f(t):  # a breath or onset fills the last 0.4 s before each voice
            if any(a - 0.4 <= t < a for a, _ in self.PADDED[1:]):
                return -30.0
            return base(t)
        self.assertEqual(self._run(self.WORDS, f, 35)["status"], qc.PASS)

    def test_a_loud_long_gap_far_away_does_not_flag_every_span(self):
        words = [(5 + 5 * i, 9 + 5 * i) for i in range(6)]  # padded gaps of 0.6 s: not readable
        padded = [(a - 0.2, b + 0.2) for a, b in words]
        f = self._levels(padded, {(34.2, 45.0): -24.0})
        row = self._run(words, f, 45)
        self.assertEqual(row["status"], qc.WARN, row)
        self.assertIn("29.80s", row["found"])   # touches the loud gap
        flagged = re.findall(r"(\d+\.\d\ds) to", row["found"])
        self.assertEqual(flagged, ["19.80s", "24.80s", "29.80s"])  # the three nearer the loud gap

    def test_a_span_with_no_readable_side_walks_out_to_the_nearest_long_gap(self):
        # three spans 0.9 s apart (not readable); the middle one has no readable side
        words = [(5, 9), (10.3, 14.3), (15.6, 19.6)]
        padded = [(4.8, 9.2), (10.1, 14.5), (15.4, 19.8)]
        loud_head = self._run(words, self._levels(padded, {(0.0, 4.8): -24.0}), 30)
        self.assertEqual(loud_head["status"], qc.WARN, loud_head)
        self.assertIn("10.10s", loud_head["found"])  # head and tail are equally far: the louder wins
        self.assertNotIn("15.40s", loud_head["found"])  # its own right gap is quiet
        loud_tail = self._run(words, self._levels(padded, {(19.8, 30.0): -24.0}), 30)
        self.assertIn("10.10s", loud_tail["found"])
        self.assertIn("15.40s", loud_tail["found"])
        self.assertNotIn("4.80s to", loud_tail["found"])  # the quiet head is its own bed

    def test_a_span_with_no_signal_says_no_voice_here(self):
        words = [(5, 9), (15, 19), (25, 29)]
        padded = [(4.8, 9.2), (14.8, 19.2), (24.8, 29.2)]

        def f(t):
            if 14.8 <= t < 19.2:
                return -120.0  # the transcript has words where the file is silent
            return self._levels([padded[0], padded[2]], {})(t)
        row = self._run(words, f, 35)
        self.assertEqual(row["status"], qc.WARN, row)
        self.assertEqual(row["found"], "14.80s to 19.20s no voice here: check the transcript timing")

    def test_a_span_past_the_end_of_the_audio_says_no_voice_here(self):
        words = [(5, 9), (15, 19), (40, 44)]  # the file is 35 s long
        padded = [(4.8, 9.2), (14.8, 19.2)]
        row = self._run(words, self._levels(padded, {}), 35)
        self.assertEqual(row["status"], qc.WARN, row)
        self.assertIn("39.80s to 44.20s no voice here", row["found"])

    def test_no_voice_warnings_past_the_fifth_are_counted(self):
        words = [(5 + 6 * i, 9 + 6 * i) for i in range(8)]
        row = self._run(words, lambda t: -120.0 if t >= 5 else -50.0, 80)
        self.assertEqual(row["status"], qc.WARN, row)
        self.assertTrue(row["found"].endswith("; +3 more"), row["found"])

    def test_music_only_in_gaps_too_short_to_read_passes(self):
        words = [(5, 9), (10.3, 14.3), (15.6, 19.6)]  # the known looser case
        padded = [(4.8, 9.2), (10.1, 14.5), (15.4, 19.8)]
        f = self._levels(padded, {(9.2, 10.1): -24.0, (14.5, 15.4): -24.0})
        self.assertEqual(self._run(words, f, 30)["status"], qc.PASS)

    def test_a_voice_tail_in_short_gaps_is_not_a_bed(self):
        base = self._levels(self.PADDED, {})

        def f(t):  # the momentary window still holds the voice for 0.5 s into each gap
            if any(a <= t < a + 0.5 for _, a in self.PADDED):
                return -28.0
            return base(t)
        self.assertEqual(self._run(self.WORDS, f, 35)["status"], qc.PASS)


if __name__ == "__main__":
    unittest.main()
