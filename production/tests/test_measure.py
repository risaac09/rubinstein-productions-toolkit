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

    def test_camera_match_with_skin_unavailable(self):
        frames = [{"t": 1.0, "luma": {"p50": 50.0}, "skin": measure.UNAVAILABLE},
                  {"t": 11.0, "luma": {"p50": 48.0}, "skin": measure.UNAVAILABLE}]
        out = measure.camera_match(frames, [("A", 0, 5), ("B", 10, 15)])
        self.assertIsNone(out["pairs"][0]["skin_de2000"])
        self.assertEqual(out["pairs"][0]["luma_p50_delta_ire"], 2.0)

    def test_decode_filter_is_explicit(self):
        vf, note = measure.decode_filter({"pix_fmt": "yuv420p", "color_space": "", "color_range": ""})
        self.assertEqual(vf, "scale=in_color_matrix=bt709:in_range=limited")
        self.assertIn("untagged", note)
        vf, note = measure.decode_filter({"pix_fmt": "yuv420p", "color_space": "smpte170m",
                                          "color_range": "pc"})
        self.assertEqual((vf, note), ("scale=in_color_matrix=bt601:in_range=full", ""))
        self.assertEqual(measure.decode_filter({"pix_fmt": "rgb48le"}), (None, ""))

    def test_rotation_and_timeouts(self):
        self.assertEqual(measure.rotation({"side_data_list": [{"rotation": -90}]}), 270)
        self.assertEqual(measure.rotation({"tags": {"rotate": "90"}}), 90)
        self.assertEqual(measure.rotation({}), 0)
        from unittest import mock
        with mock.patch.object(measure.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("ffprobe", 1)):
            with self.assertRaises(measure.MeasureError):
                measure.probe("x.mov")

    def test_sampling_and_pixel_formats(self):
        self.assertEqual(measure.sample_times(0, 10), [0.0])
        self.assertEqual(measure.sample_times(10, 2), [2.5, 7.5])
        self.assertTrue(measure.is_rgb_source("rgb48le"))
        self.assertFalse(measure.is_rgb_source("yuv420p10le"))
        self.assertEqual(measure.bit_depth("yuv422p10le"), 10)
        self.assertEqual(measure.bit_depth("yuv420p"), 8)
        self.assertEqual(measure.bit_depth("p010le"), 10)


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
        self.assertEqual(report["decode_note"], "color_space 'untagged': decoded as bt709")

    def test_rotated_clip_is_not_scrambled(self):
        with tempfile.TemporaryDirectory() as tmp:
            flat, clip = os.path.join(tmp, "flat.mov"), os.path.join(tmp, "rot.mov")
            # Left half white, right half black, stored 320x180, displayed rotated 90.
            subprocess.run([measure.FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                            "color=c=black:size=320x180:rate=24:duration=1,"
                            "drawbox=x=0:y=0:w=160:h=180:color=white:t=fill",
                            "-c:v", "prores_ks", "-pix_fmt", "yuv422p10le", "-y", flat], check=True)
            subprocess.run([measure.FFMPEG, "-v", "error", "-display_rotation", "90", "-i", flat,
                            "-c", "copy", "-y", clip], check=True)
            info = measure.probe(clip)
            if (info["width"], info["height"]) == (320, 180):
                self.skipTest("this ffmpeg did not record the rotation")
            rgb = measure.read_frame(clip, 0.5, info["width"], info["height"],
                                     measure.decode_filter(info["tags"])[0])
        self.assertEqual(rgb.shape, (320, 180, 3))
        rows = rgb.mean(axis=(1, 2))
        # After a quarter turn the split runs across rows: each row is uniform.
        self.assertTrue(((rows > 0.9) | (rows < 0.1)).all(), rows[:5])
        self.assertAlmostEqual(float((rows > 0.9).mean()), 0.5, delta=0.02)


@needs_numpy
class TestLightReadouts(unittest.TestCase):
    SKIN = np.array((0.80, 0.60, 0.50))
    BOX = {"x": 0.30, "y": 0.20, "w": 0.40, "h": 0.60}

    def face(self, stops):
        """A 200x200 frame whose face has a lit left half and a right half
        `stops` darker in linear light (display = linear ** (1/2.4))."""
        img = np.full((200, 200, 3), (0.1, 0.2, 0.8))
        img[40:160, 60:100] = self.SKIN
        img[40:160, 100:140] = self.SKIN * 2 ** (-stops / 2.4)
        return img

    def test_known_ratios_within_a_quarter_stop(self):
        for stops in (0.5, 1.0, 1.5, 2.0):
            got = measure.face_lighting(self.face(stops), [self.BOX])
            self.assertEqual(len(got), 1, stops)
            self.assertAlmostEqual(got[0]["stops"], stops, delta=0.25)
            self.assertEqual(got[0]["lit_side"], "left")
        # 2:1 is one stop in the (key + fill) : fill convention
        self.assertAlmostEqual(measure.face_lighting(self.face(1.0), [self.BOX])[0]["ratio"], 2.0, delta=0.1)

    def test_names_label_and_pool(self):
        self.assertEqual([measure.ratio_name(s) for s in (0.4, 1.0, 1.5, 2.5, 4)],
                         ["flat", "flat", "classic", "dramatic", "extreme"])
        frames = [{"lighting": [{"stops": 1.0, "lit_side": "left"}]},
                  {"lighting": [{"stops": 2.0, "lit_side": "left"}, {"stops": 3.0, "lit_side": "right"}]},
                  {"lighting": None}]
        pooled = measure.pooled_lighting(frames)
        self.assertEqual((pooled["faces_measured"], pooled["stops"], pooled["lit_side"], pooled["name"]),
                         (3, 2.0, "left", "classic"))
        self.assertIsNone(measure.pooled_lighting([{"lighting": None}]))

    def test_transfer_withholds(self):
        for tag in ("smpte2084", "arib-std-b67", "linear", "log100"):
            self.assertTrue(measure.transfer_of({"color_transfer": tag})["withheld"], tag)
        for tag in ("bt709", "iec61966-2-1", ""):
            self.assertIsNone(measure.transfer_of({"color_transfer": tag})["withheld"], tag)

    def test_shadow_classes(self):
        rng = np.random.default_rng(1)
        h, w = 96, 128
        grad = np.tile(np.linspace(0.06, 0.18, w), (h, 1))        # about 6..18 IRE, smooth
        smooth = measure.shadow_classes(np.dstack([grad] * 3))
        self.assertEqual((smooth["sculpted_pct"], smooth["lost_pct"]), (100.0, 0.0))
        noisy = measure.shadow_classes(np.dstack([np.clip(grad + rng.normal(0, 0.03, grad.shape), 0, 1)] * 3))
        self.assertGreater(noisy["readable_pct"], 95)
        self.assertEqual(measure.shadow_classes(np.zeros((h, w, 3)))["lost_pct"], 100.0)
        bright = measure.shadow_classes(np.full((h, w, 3), 0.8))
        self.assertEqual(bright["shadow_pct"], 0.0)

    def test_highlights_and_percentiles(self):
        ramp = np.tile(np.linspace(0, 1, 1000), (20, 1))
        ramp = np.dstack([ramp] * 3)
        hl = measure.highlights(ramp, stride=1)
        self.assertAlmostEqual(hl["tail_ire_per_pct"], 1.0, delta=0.05)
        clipped = ramp.copy()
        clipped[:, 850:] = 1.0
        hl = measure.highlights(clipped, stride=1)
        self.assertEqual(hl["tail_ire_per_pct"], 0.0)
        self.assertAlmostEqual(hl["clip_pct"], 15.0, delta=0.2)
        stats = measure.luma_stats(ramp, stride=1)
        self.assertAlmostEqual(stats["p5"], 5.0, delta=0.2)
        self.assertAlmostEqual(stats["p95"], 95.0, delta=0.2)


@needs_numpy
@unittest.skipUnless(os.access(measure.FFMPEG if np is not None else "", os.X_OK), "ffmpeg missing")
class TestLightOnClips(unittest.TestCase):
    def clip(self, tmp, name, vf):
        path = os.path.join(tmp, name)
        subprocess.run([measure.FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                        "color=c=0x181818:size=320x180:rate=24:duration=2", "-vf", vf, "-pix_fmt", "yuv422p10le",
                        "-c:v", "prores_ks", "-y", path], check=True)
        return path

    def test_report_names_transfer_and_compares_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            master = self.clip(tmp, "m.mov", "noise=alls=40:allf=t")                 # grainy shadow
            crushed = self.clip(tmp, "d.mov", "eq=brightness=-0.2,format=yuv422p10le")  # shadow at the floor
            report = measure.measure(master, samples=2, faces=False, delivery=crushed)
        self.assertEqual(report["transfer"]["read_as"], measure.READ_AS)
        self.assertGreater(report["shadows"]["readable_pct"], 50)
        cmp = report["shadow_vs_delivery"]
        self.assertGreater(cmp["readable_drop_pts"], measure.DELIVERY_LIMIT_PTS)
        self.assertFalse(cmp["within_limit"])

    def test_hlg_tag_withholds(self):
        with tempfile.TemporaryDirectory() as tmp:
            hlg = self.clip(tmp, "hlg.mov", "setparams=color_trc=arib-std-b67")
            if measure.probe(hlg)["tags"]["color_transfer"] != "arib-std-b67":
                self.skipTest("this ffmpeg did not record the HLG tag")
            withheld = measure.measure(hlg, samples=1, faces=False)
        self.assertEqual(withheld["transfer"]["withheld"], "HLG")
        self.assertTrue(withheld["shadows"].startswith("withheld"))
        self.assertTrue(withheld["lighting"].startswith("withheld"))
        self.assertIn("lighting ratio: withheld", measure.format_summary(withheld))


@needs_numpy
class TestCmdMeasure(unittest.TestCase):
    def test_hero_must_be_a_segment(self):
        import resolve_workflow as rw
        args = argparse.Namespace(file="x.mov", samples=1, segment=["A=0-1"], hero="B",
                                  no_faces=True, delivery=None, json=None)
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            self.assertEqual(rw.cmd_measure(args), 1)
        self.assertIn("--hero B", err.getvalue())


if __name__ == "__main__":
    unittest.main()
