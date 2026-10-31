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


if __name__ == "__main__":
    unittest.main()
