"""
Offline tests for rpresolve.continuity on synthetic measure-shaped dicts.
No Resolve, no footage. Skipped where numpy is missing.

Run: python3 -m unittest production.tests.test_continuity -v
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import numpy as np
    from rpresolve import color, continuity
except ImportError:
    np = None

needs_numpy = unittest.skipIf(np is None, "numpy not installed")


def rep(name, p50=45.0, hi=0.0, lo=0.0, skin_lab=(60.0, 14.0, 18.0), hue_off=0.0,
        side="left", cast=None):
    r = {"file": f"/scratch/{name}.mov", "luma": {"p50": p50},
         "clipping": {"high_pct": hi, "low_pct": lo},
         "skin": ({"lab": list(skin_lab), "hue_off_skin_line_deg": hue_off, "frames_with_skin": 5}
                  if skin_lab else "no face with enough skin pixels in the sampled frames"),
         "lighting": ({"lit_side": side, "stops": 1.0} if side else "withheld: tagged HLG, not display Rec.709")}
    if cast:
        r["neutral_cast"] = {"a": cast[0], "b": cast[1]}
    return r


SCENE = [rep("A001", p50=44, hue_off=2),                          # the reference
         rep("A002", p50=70, hi=3.0, hue_off=10, side="right"),   # bright, clipped, other side
         rep("A003", p50=40, hue_off=-4, skin_lab=(60.0, 22.0, 28.0)),  # warmer skin
         rep("A004", p50=45, skin_lab=None, side=None)]            # no skin, light withheld


@needs_numpy
class TestContinuity(unittest.TestCase):
    def test_names_one_reference_with_reasons(self):
        s = continuity.build({"S01": SCENE})["scenes"]["S01"]
        self.assertEqual(s["reference"], "A001")
        self.assertEqual(len(s["reasons"]), 3)
        self.assertIn("exposure", s["reasons"][0])
        self.assertGreater(s["margin"], 0)

    def test_flagged_pairs_and_withheld_light_not_flagged(self):
        pairs = {(f["a"], f["b"]): f["reasons"]
                 for f in continuity.build({"S01": SCENE})["scenes"]["S01"]["flagged_pairs"]}
        self.assertTrue(any("light side" in r for r in pairs[("A001", "A002")]))
        self.assertTrue(any("white point: skin" in r for r in pairs[("A001", "A003")]))
        self.assertFalse(any(k for k in pairs if "A004" in k))

    def test_neutral_cast_beats_skin_and_mixed_basis_is_noted(self):
        reps = {"x": rep("x", cast=(0.0, 0.0)), "y": rep("y", cast=(3.0, 0.0)),
                "z": rep("z")}
        f = {(p["a"], p["b"]): p["reasons"] for p in continuity.flag_pairs(reps)}
        self.assertIn("neutral cast", f[("x", "y")][0])
        self.assertIn("basis differs", f[("x", "z")][0])

    def test_single_clip_scene(self):
        s = continuity.build({"S": [rep("only")]})["scenes"]["S"]
        self.assertEqual(s["reference"], "only")
        self.assertEqual(s["flagged_pairs"], [])

    def test_duplicate_clip_name_refused(self):
        with self.assertRaises(ValueError):
            continuity.build({"S": [rep("a"), rep("a")]})

    def test_second_run_reports_before_and_after(self):
        before = continuity.build({"S01": SCENE})
        # Shot Match pulled A003 toward the reference; A002 is not re-measured.
        after = continuity.build({"S01": [rep("A001", p50=44, hue_off=2),
                                          rep("A003", skin_lab=(60.0, 15.0, 19.0))]})
        row = continuity.compare(before, after)["S01"]["clips"]
        self.assertGreater(row["A003"]["before_de2000"], row["A003"]["after_de2000"])
        self.assertLess(row["A003"]["change"], 0)
        self.assertEqual(row["A002"]["after_de2000"], None)
        self.assertIn("note", row["A002"])
        expect = float(color.delta_e_2000(np.array([60.0, 22.0, 28.0]), np.array([60.0, 14.0, 18.0])))
        self.assertAlmostEqual(row["A003"]["before_de2000"], expect, places=2)

    def test_cli_two_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = []
            for r in SCENE:
                p = os.path.join(tmp, os.path.basename(r["file"]) + ".json")
                Path(p).write_text(json.dumps(r))
                files.append(p)
            out = os.path.join(tmp, "first.json")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(continuity.main(["--scene", "S01", *files, "--out", out]), 0)
            self.assertIn("reference A001", buf.getvalue())
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(continuity.main(["--scene", "S01", *files, "--before", out]), 0)
            self.assertIn("shot match, scene S01", buf.getvalue())

    def test_bad_input_exits_2(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(continuity.main(["--scene", "S", "/nonexistent.json"]), 2)


if __name__ == "__main__":
    unittest.main()
