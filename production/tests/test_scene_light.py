"""
Offline tests for rpresolve.scene_light and the scene-light command: worked
numbers on hand-written reports, and one block built from measure.py's own
readouts of a synthetic face. No Resolve, no real footage.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import argparse
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import cmd_scene_light, scene_light  # noqa: E402

try:
    import numpy as np
    from rpresolve import measure
except ImportError:  # numpy missing on this interpreter
    np = None


def report(p50=45.0, lighting=None, shadows=None, skin=None, transfer=None):
    return {"file": "shot.mov", "transfer": {"tag": "bt709", "withheld": transfer},
            "luma": {"p1": 2.0, "p5": 5.0, "p50": p50, "p95": 90.0, "p99": 98.0},
            "lighting": lighting if lighting is not None else
            {"faces_measured": 4, "stops": 1.58, "ratio": 2.99, "lit_side": "right", "name": "classic"},
            "shadows": shadows if shadows is not None else
            {"readable_pct": 12.5, "sculpted_pct": 4.0, "lost_pct": 3.25, "shadow_pct": 19.75},
            "skin": skin if skin is not None else {"L_srgb": 64.2, "lab": [60, 14, 18]}}


class TestKey(unittest.TestCase):
    def test_bands_on_the_house_thresholds(self):
        # 60 and 30 IRE are the defaults; each threshold belongs to its outer band.
        self.assertEqual([scene_light.key_of(v) for v in (72.0, 60.0, 59.9, 45.0, 30.1, 30.0, 8.0)],
                         ["high", "high", "mid", "mid", "mid", "low", "low"])


class TestBlock(unittest.TestCase):
    def test_worked_numbers(self):
        self.assertEqual(scene_light.light_block(report()), {
            "ratio_stops": 1.58, "ratio": 2.99, "lit_side": "right", "key": "mid",
            "skin_l_srgb": 64.2, "shadow_readable_pct": 12.5, "shadow_lost_pct": 3.25,
            "withheld": None})

    def test_log_withholds_every_number(self):
        block = scene_light.light_block(report(p50=72.0, transfer="log"))
        self.assertTrue(all(block[k] is None for k in scene_light.FIELDS), block)
        self.assertIn("tagged log", block["withheld"])

    def test_missing_number_is_null_with_a_reason(self):
        block = scene_light.light_block(report(
            lighting="no lighting ratio from the sampled frames: too few cheek pixels on a side",
            skin="no face with enough skin pixels in the sampled frames"))
        self.assertEqual((block["ratio"], block["ratio_stops"], block["lit_side"], block["skin_l_srgb"]),
                         (None, None, None, None))
        self.assertEqual(block["key"], "mid")
        self.assertIn("ratio: no lighting ratio", block["withheld"])
        self.assertIn("skin: no face", block["withheld"])

    def test_clipped_cheek_nulls_skin(self):
        warn = "12% of the sample has a channel at clip: L* and hue are limits, not readings"
        block = scene_light.light_block(report(skin={"L_srgb": 92.1, "warnings": [warn]}))
        self.assertIsNone(block["skin_l_srgb"])
        self.assertIn("at clip", block["withheld"])
        other = scene_light.light_block(report(skin={"L_srgb": 64.2, "warnings": ["no face landmarks"]}))
        self.assertEqual(other["skin_l_srgb"], 64.2)

    def test_record_names_the_taste(self):
        rec = scene_light.shot_record(report())
        self.assertEqual(set(rec), {"file", "light", "taste", "review"})
        self.assertIn("key", rec["taste"])
        self.assertTrue(set(rec["taste"]) <= set(scene_light.FIELDS))


@unittest.skipIf(np is None, "numpy not installed")
class TestFromMeasureReadouts(unittest.TestCase):
    """The block from measure.py's own functions on a synthetic frame: a face
    lit one stop harder on its left (2:1), on a dark blue ground."""

    def test_one_stop_face(self):
        skin = np.array((0.80, 0.60, 0.50))
        img = np.full((200, 200, 3), (0.02, 0.03, 0.10))
        img[40:160, 60:100] = skin
        img[40:160, 100:140] = skin * 2 ** (-1.0 / 2.4)
        box = {"x": 0.30, "y": 0.20, "w": 0.40, "h": 0.60}
        frames = [{"lighting": measure.face_lighting(img, [box])}]
        rep = {"file": "synthetic", "transfer": measure.transfer_of({"color_transfer": "bt709"}),
               "luma": measure.luma_stats(img), "lighting": measure.pooled_lighting(frames),
               "shadows": measure.mean_classes([measure.shadow_classes(img)]),
               "skin": measure.skin_stats(img, [box], min_pixels=10)}
        block = scene_light.light_block(rep)
        self.assertAlmostEqual(block["ratio_stops"], 1.0, delta=0.25)
        self.assertAlmostEqual(block["ratio"], 2.0, delta=0.4)
        self.assertEqual(block["lit_side"], "left")
        self.assertEqual(block["key"], "low")  # most of the frame is the dark ground
        self.assertGreater(block["shadow_lost_pct"] + block["shadow_readable_pct"], 0)
        self.assertEqual(block["skin_l_srgb"], rep["skin"]["L_srgb"])
        self.assertIsNone(block["withheld"])


class TestCommand(unittest.TestCase):
    def test_reads_saved_reports_and_writes_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            shot = os.path.join(tmp, "a.json")
            with open(shot, "w", encoding="utf-8") as f:
                json.dump(report(p50=70.0), f)
            out = os.path.join(tmp, "light.json")
            stdout = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                code = cmd_scene_light.run(argparse.Namespace(shots=[shot], samples=10, json=out))
            self.assertEqual(code, 0)
            self.assertIn("key high, ratio 2.99:1", stdout.getvalue())
            with open(out, encoding="utf-8") as f:
                records = json.load(f)
            self.assertEqual(records[0]["light"]["key"], "high")

    def test_refuses_json_that_is_not_a_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            shot = os.path.join(tmp, "x.json")
            with open(shot, "w", encoding="utf-8") as f:
                json.dump([1, 2], f)
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                code = cmd_scene_light.run(argparse.Namespace(shots=[shot], samples=10, json=None))
            self.assertEqual(code, 1)
            self.assertIn("not a measure --json report", err.getvalue())


if __name__ == "__main__":
    unittest.main()
