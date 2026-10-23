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


if __name__ == "__main__":
    unittest.main()
