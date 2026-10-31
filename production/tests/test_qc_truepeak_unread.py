"""
true_peak_per_cut when a shot's loudness read fails (audit 2026-10-31 #9).
The read is stubbed, so no ffmpeg or numpy is needed.

Run: /usr/bin/python3 -m unittest production.tests.test_qc_truepeak_unread -v
"""

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import qc  # noqa: E402


class ReportError(Exception):
    pass


def _row(reads, cuts=(1.0, 2.0)):
    """reads: one value per shot, a dBTP number, "ERR" for a failed read, None for a read with no peak."""
    it = iter(reads)

    def loudness(path, ch, a, b):
        v = next(it)
        if v == "ERR":
            raise ReportError("no read")
        return [{"true_peak_dbtp": v}]

    fake = types.SimpleNamespace(ReportError=ReportError, loudness=loudness)
    with mock.patch.object(qc, "_ar", lambda: fake):
        return qc._sound_true_peak_per_cut("x.wav", list(cuts), 3.0, -1.0, None)


class UnreadShots(unittest.TestCase):
    def test_all_measured_still_passes(self):
        r = _row([-3.0, -2.0, -4.0])
        self.assertEqual(r["status"], qc.PASS)
        self.assertIn("3 shots, worst -2.0", r["found"])

    def test_some_unread_warns_and_is_left_out_of_n(self):
        r = _row([-3.0, "ERR", -4.0])
        self.assertEqual(r["status"], qc.WARN)
        self.assertIn("2 shots", r["found"])
        self.assertIn("shot 2 unreadable", r["found"])

    def test_none_measured_fails_instead_of_passing(self):
        r = _row(["ERR", "ERR", "ERR"])
        self.assertEqual(r["status"], qc.FAIL)
        self.assertIn("shot 1, 2, 3 unreadable", r["found"])
        self.assertNotIn("None dBTP", r["found"])

    def test_a_read_with_no_peak_counts_as_unread(self):
        # loudness() answered but gave true_peak_dbtp None
        r = _row([None, None, None])
        r2 = _row([-3.0, None, -4.0])
        self.assertEqual((r["status"], r2["status"]), (qc.FAIL, qc.WARN))

    def test_over_ceiling_stays_fail_and_names_the_unread(self):
        r = _row([0.5, "ERR", -4.0])
        self.assertEqual(r["status"], qc.FAIL)
        self.assertIn("shot 1", r["found"])
        self.assertIn("shot 2 unreadable", r["found"])


class OneFullEburPass(unittest.TestCase):
    """audit 2026-10-31 #26: the phone proxy reuses the framelog qc.run read,
    so _sound_rows runs only the filtered (high-pass) ebur128 pass."""

    def test_sound_rows_does_not_reread_the_full_file(self):
        calls = []

        def ebur(path, chain=""):
            calls.append(chain)
            return {}, [(0.0, -30.0, -30.0)]

        fake_ar = types.SimpleNamespace(NO_READING_LUFS=-120.7)
        row = {"check": "stub", "status": qc.PASS}
        stubs = {n: mock.patch.object(qc, n, lambda *a, **k: row) for n in (
            "_sound_true_peak_per_cut", "_sound_silence_at_cut", "_sound_speech_vs_rest",
            "_sound_stems_sum")}
        with mock.patch.object(qc, "_np", lambda: object()), \
                mock.patch.object(qc, "_ar", lambda: fake_ar), \
                mock.patch.object(qc, "_ebur", ebur), \
                stubs["_sound_true_peak_per_cut"], stubs["_sound_silence_at_cut"], \
                stubs["_sound_speech_vs_rest"], stubs["_sound_stems_sum"]:
            rows = qc._sound_rows("x.wav", {"lra_lu": 3.0, "integrated_lufs": -16.0},
                                  [(0.0, -20.0, -18.0)], qc.THRESHOLDS, None, None, None, None,
                                  -1.0, 24.0, 3.0)
        self.assertEqual(len(calls), 1)
        self.assertIn("highpass", calls[0])
        self.assertIn("mix -18.0 LUFS", next(r for r in rows if r["check"] == "phone_proxy")["found"])


if __name__ == "__main__":
    unittest.main()
