"""
Tests for the camera transform table (production/camera-transforms.json) and
the V-Log and HLG curves in rpresolve.color. Pure numpy, no media.

Run: /usr/bin/python3 -m unittest production.tests.test_camera_transforms -v
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import numpy as np
    from rpresolve import color
    HAVE_NUMPY = True
except ImportError:  # the stdlib-only CI job
    HAVE_NUMPY = False

needs_numpy = unittest.skipUnless(HAVE_NUMPY, "needs numpy")

TABLE = Path(__file__).resolve().parent.parent / "camera-transforms.json"
CODES = np.arange(0, 1024) if HAVE_NUMPY else None


@needs_numpy
class CurveRoundTrip(unittest.TestCase):
    def check(self, enc, dec):
        sig = CODES / 1023.0
        back = np.round(enc(dec(sig)) * 1023)
        self.assertLessEqual(np.abs(back - CODES).max(), 1)

    def test_vlog_codes_round_trip(self):
        self.check(color.vlog_encode, color.vlog_decode)

    def test_hlg_codes_round_trip(self):
        self.check(color.hlg_encode, color.hlg_decode)

    def test_vlog_linear_ramp_round_trip(self):
        lin = np.concatenate([[0.0], np.geomspace(1e-4, 46.0, 400)])
        codes = np.round(color.vlog_encode(lin) * 1023)
        again = np.round(color.vlog_encode(color.vlog_decode(codes / 1023)) * 1023)
        self.assertLessEqual(np.abs(again - codes).max(), 1)

    def test_vlog_published_points(self):
        # Panasonic manual, 10-bit: 0% = 128, 18% grey = 433, 90% = 602.
        got = np.round(color.vlog_encode([0.0, 0.18, 0.90]) * 1023)
        self.assertEqual(list(got), [128, 433, 602])

    def test_vlog_segments_meet(self):
        self.assertAlmostEqual(float(color.vlog_encode(0.01 - 1e-9)),
                               float(color.vlog_encode(0.01)), places=6)

    def test_hlg_points(self):
        self.assertAlmostEqual(float(color.hlg_encode(1 / 12)), 0.5, places=6)
        self.assertAlmostEqual(float(color.hlg_encode(1.0)), 1.0, places=6)


class Table(unittest.TestCase):
    def setUp(self):
        self.t = json.loads(TABLE.read_text())

    def test_rows_and_fields(self):
        names = [r["camera"] for r in self.t["cameras"]]
        self.assertEqual(names, ["Blackmagic Pyxis", "Panasonic S5II",
                                 "Panasonic GH7", "iPhone 15 Pro or later"])
        for r in self.t["cameras"]:
            for k in ("input_curve", "input_primaries", "measure_linearise",
                      "resolve_input_colour_space", "record_per_clip"):
                self.assertTrue(r.get(k), (r["camera"], k))

    def test_braw_row_has_no_offline_decode(self):
        row = self.t["cameras"][0]
        self.assertIn("measure from a Resolve-rendered ramp", row["measure_linearise"])

    def test_vlog_rows_name_implemented_curve(self):
        for r in self.t["cameras"][1:3]:
            self.assertEqual(r["measure_linearise"], "rpresolve.color.vlog_decode")
        self.assertEqual(self.t["cameras"][3]["measure_linearise"], "rpresolve.color.hlg_decode")


if __name__ == "__main__":
    unittest.main()
