"""
Offline tests for rpresolve.color and rpresolve.measure. Synthetic arrays
and an ffmpeg-generated test clip only; no Resolve, no real footage.
Skipped where numpy (or, for the clip, ffmpeg) is missing.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import argparse
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import numpy as np
    from rpresolve import color, measure
except ImportError:  # numpy missing on this interpreter
    np = None

needs_numpy = unittest.skipIf(np is None, "numpy not installed")


@needs_numpy
class TestColor(unittest.TestCase):
    # Pairs and expected CIEDE2000 from Sharma, Wu and Dalal (2005), table 1.
    SHARMA = [
        ((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485), 2.0425),
        ((50.0, 0.0, 0.0), (50.0, -1.0, 2.0), 2.3669),
        ((50.0, 2.5, 0.0), (73.0, 25.0, -18.0), 27.1492),
        ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
    ]

    def test_delta_e_2000_matches_the_published_table(self):
        for a, b, want in self.SHARMA:
            self.assertAlmostEqual(float(color.delta_e_2000(np.array(a), np.array(b))), want, places=4)

    def test_lab_of_white_and_neutrals(self):
        white = color.rec709_to_lab(np.array([1.0, 1.0, 1.0]))
        self.assertAlmostEqual(white[0], 100.0, places=2)
        grey = color.rec709_to_lab(np.array([0.5, 0.5, 0.5]))
        self.assertAlmostEqual(grey[1], 0.0, places=2)
        self.assertAlmostEqual(grey[2], 0.0, places=2)

    def test_luma_and_vectorscope(self):
        self.assertAlmostEqual(float(color.luma_ire(np.array([1.0, 1.0, 1.0]))), 100.0, places=6)
        skin = np.array([0.80, 0.60, 0.50])  # a light skin tone
        self.assertLess(abs(float(color.vectorscope_angle(skin)) - measure.SKIN_LINE_DEG), 15)
        red = np.array([1.0, 0.0, 0.0])
        self.assertAlmostEqual(float(color.vectorscope_angle(red)), 103.6, delta=1.0)


@needs_numpy
class TestPureMeasurements(unittest.TestCase):
    def test_luma_and_clipping(self):
        img = np.zeros((40, 40, 3))
        img[:, :10] = 1.0          # a quarter of the frame at the top code value
        img[:, 10:20] = 0.5
        img[:, 20:30] = 0.25
        stats = measure.luma_stats(img)
        self.assertEqual(stats["p99"], 100.0)
        self.assertEqual(stats["p1"], 0.0)
        clip = measure.clipping(img)
        self.assertEqual((clip["high_pct"], clip["low_pct"]), (25.0, 25.0))

    def test_skin_stats_inside_a_face_box(self):
        img = np.zeros((100, 100, 3))
        img[:] = [0.1, 0.2, 0.8]                   # a blue background
        img[20:80, 30:70] = [0.80, 0.60, 0.50]     # a skin-toned face
        self.assertFalse(measure.skin_mask(img[0:1, 0:1]).any())
        box = {"x": 0.30, "y": 0.20, "w": 0.40, "h": 0.60}
        skin = measure.skin_stats(img, [box], min_pixels=10)
        self.assertIsNotNone(skin)
        self.assertLess(abs(skin["hue_off_skin_line_deg"]), 15)
        self.assertIsNone(measure.skin_stats(img, [], min_pixels=10))
        self.assertIsNone(measure.skin_stats(img, [{"x": 0, "y": 0, "w": 0.1, "h": 0.1}], min_pixels=10))

    def test_segments_and_camera_match(self):
        self.assertEqual(measure.parse_segment("A=0-12.5"), ("A", 0.0, 12.5))
        for bad in ("A=5-2", "A:0-1", "=0-1"):
            with self.assertRaises(measure.MeasureError):
                measure.parse_segment(bad)
        frames = [
            {"t": 1.0, "luma": {"p50": 50.0}, "skin": {"lab": [60.0, 15.0, 18.0]}},
            {"t": 2.0, "luma": {"p50": 52.0}, "skin": {"lab": [60.0, 15.0, 18.0]}},
            {"t": 11.0, "luma": {"p50": 47.0}, "skin": {"lab": [58.0, 12.0, 15.0]}},
            {"t": 12.0, "luma": {"p50": 47.0}, "skin": None},
        ]
        out = measure.camera_match(frames, [("A", 0, 5), ("B", 10, 15)], hero="A")
        pair = out["pairs"][0]
        want = float(color.delta_e_2000(np.array([60.0, 15.0, 18.0]), np.array([58.0, 12.0, 15.0])))
        self.assertAlmostEqual(pair["skin_de2000"], round(want, 3))
        self.assertEqual(pair["luma_p50_delta_ire"], 4.0)
        self.assertEqual(out["segments"]["B"]["frames_with_skin"], 1)
        self.assertLess(out["chroma_vs_hero_pct"]["B"], 0)
        self.assertEqual(out["chroma_vs_hero_pct"]["A"], 0.0)

    def test_sampling_and_pixel_formats(self):
        self.assertEqual(measure.sample_times(0, 10), [0.0])
        self.assertEqual(measure.sample_times(10, 2), [2.5, 7.5])
        self.assertTrue(measure.is_rgb_source("rgb48le"))
        self.assertFalse(measure.is_rgb_source("yuv420p10le"))
        self.assertEqual(measure.bit_depth("yuv422p10le"), 10)
        self.assertEqual(measure.bit_depth("yuv420p"), 8)


@needs_numpy
@unittest.skipUnless(os.access(measure.FFMPEG if np is not None else "", os.X_OK), "ffmpeg missing")
class TestOnAGeneratedClip(unittest.TestCase):
    def test_mid_grey_clip(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip = os.path.join(tmp, "grey.mov")
            subprocess.run([measure.FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                            "color=c=0x808080:size=320x180:rate=24:duration=2",
                            "-pix_fmt", "yuv422p10le", "-c:v", "prores_ks", "-y", clip], check=True)
            report = measure.measure(clip, samples=2, faces=False)
        self.assertEqual(report["frames_sampled"], 2)
        self.assertAlmostEqual(report["luma"]["p50"], 50.2, delta=1.0)  # 0x80 = 50.2%
        self.assertEqual(report["clipping"], {"high_pct": 0.0, "low_pct": 0.0})
        self.assertTrue(report["legal_8bit"]["within_16_235"])
        self.assertEqual(report["skin"], "not measured (--no-faces)")


@needs_numpy
class TestCmdMeasure(unittest.TestCase):
    def test_hero_must_be_a_segment(self):
        import resolve_workflow as rw
        args = argparse.Namespace(file="x.mov", samples=1, segment=["A=0-1"], hero="B",
                                  no_faces=True, json=None)
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            self.assertEqual(rw.cmd_measure(args), 1)
        self.assertIn("--hero B", err.getvalue())


if __name__ == "__main__":
    unittest.main()
