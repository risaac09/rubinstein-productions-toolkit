"""
Offline tests for rpstills.cluster and rpstills.cull: the pure logic over
an index, with no images, Vision or NAS. stdlib unittest only.

Run: /usr/bin/python3 production/tests/test_rpstills_pure.py
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpstills import cluster, crops, cull, look, measure


def row(i, t, faces=1, quality=0.5, eyes=0.3, sharp=100.0, phash="0" * 16, h=0.2, face_hash="0" * 16):
    return {"id": f"F{i:03d}", "time": t, "subsec": None, "phash": phash, "face_hash": face_hash, "error": None,
            "proxy": f"proxies/F{i:03d}.jpg", "jpeg": f"/shoot/F{i:03d}.JPG",
            "faces": [{"x": 0.4, "y": 0.2, "w": 0.15, "h": h, "confidence": 0.9, "quality": quality,
                       "eye_left": eyes, "eye_right": eyes} for _ in range(faces)],
            "sharpness": [sharp] * faces}


class ClusterTest(unittest.TestCase):
    def test_segments_bursts_runs(self):
        rows = [row(1, "2026-09-12T16:00:00"), row(2, "2026-09-12T16:00:01"),   # burst of two, same hash
                row(3, "2026-09-12T16:00:20", phash="f" * 16),                   # same run, new burst
                row(4, "2026-09-12T16:02:00", faces=3),                          # new run (gap and class)
                row(5, "2026-09-12T17:00:00")]                                   # new segment (58 min gap)
        c = cluster.build(rows)
        self.assertEqual([len(s["frames"]) for s in c["segments"]], [4, 1])
        self.assertEqual(c["bursts"][0], ["F001", "F002"])
        self.assertEqual(len(c["bursts"]), 4)
        self.assertEqual([(r["class"], len(r["frames"])) for r in c["runs"]],
                         [("solo", 3), ("group", 1), ("solo", 1)])

    def test_person_change_splits_a_solo_run(self):
        rows = [row(1, "2026-09-12T16:00:00"), row(2, "2026-09-12T16:00:05"),
                row(3, "2026-09-12T16:00:17", face_hash="f" * 16),   # 12 s pause and a new face
                row(4, "2026-09-12T16:00:19", face_hash="f" * 16),
                row(5, "2026-09-12T16:00:21", face_hash="0" * 16)]   # big hash jump alone also splits
        runs = cluster.build(rows)["runs"]
        self.assertEqual([r["frames"] for r in runs], [["F001", "F002"], ["F003", "F004"], ["F005"]])

    def test_small_background_face_does_not_count(self):
        r = row(1, "2026-09-12T16:00:00")
        r["faces"].append({"x": 0.9, "y": 0.1, "w": 0.02, "h": 0.02, "confidence": 0.6})
        self.assertEqual(cluster.frame_class(r), "solo")

    def test_timeless_frame_stays_with_its_neighbours(self):
        rows = [row(1, "2026-09-12T16:00:00"), row(2, None), row(3, "2026-09-12T16:00:03")]
        c = cluster.build(rows)
        self.assertEqual(len(c["segments"]), 1)
        self.assertEqual(len(c["runs"]), 1)

    def test_bad_rows_are_skipped(self):
        rows = [row(1, "2026-09-12T16:00:00"), dict(row(2, "2026-09-12T16:00:01"), error="boom")]
        self.assertEqual(len(cluster.build(rows)["runs"][0]["frames"]), 1)


class CropsTest(unittest.TestCase):
    CFG = crops.load_config(str(Path(__file__).resolve().parent.parent / "stills-config.json"))

    def frame(self, w=6000, h=4000, orient=1, faces=None):
        return {"id": "F1", "width": w, "height": h, "orientation": orient,
                "faces": faces if faces is not None else [{"x": 0.45, "y": 0.30, "w": 0.10, "h": 0.15}]}

    def test_upright_size_swaps_for_rotated_frames(self):
        self.assertEqual(crops.upright_size(self.frame(orient=6)), (4000, 6000))
        self.assertEqual(crops.upright_size(self.frame(orient=1)), (6000, 4000))

    def test_square_headshot_holds_the_face_with_the_eye_line_near_a_third(self):
        big = self.frame(faces=[{"x": 0.43, "y": 0.22, "w": 0.14, "h": 0.22}])
        p = crops.plan_frame(big, "solo", self.CFG)["crops"]["headshot_square"]
        x, y, w, h = p["px"]
        self.assertEqual(w, h)
        face_cx, eye_y = 0.50 * 6000, (0.22 + 0.4 * 0.22) * 4000
        self.assertTrue(x < face_cx < x + w)
        self.assertAlmostEqual((eye_y - y) / h, 0.35, delta=0.02)
        self.assertEqual(p["flags"], [])

    def test_a_small_face_flags_upscale(self):
        small = self.frame(faces=[{"x": 0.45, "y": 0.30, "w": 0.03, "h": 0.04}])
        self.assertIn("upscale", crops.plan_frame(small, "solo", self.CFG)["crops"]["headshot_square"]["flags"])

    def test_crop_stays_inside_the_frame_and_flags_tight(self):
        edge = self.frame(w=4000, h=6000, faces=[{"x": 0.02, "y": 0.02, "w": 0.30, "h": 0.20}])
        p = crops.plan_frame(edge, "solo", self.CFG)["crops"]["portrait_16x9"]
        x, y, w, h = p["px"]
        self.assertTrue(x >= 0 and y >= 0 and x + w <= 4000 and y + h <= 6000)
        self.assertIn("tight", p["flags"])

    def test_group_crop_contains_every_face(self):
        faces = [{"x": 0.05 + 0.1 * i, "y": 0.4, "w": 0.04, "h": 0.06} for i in range(8)]
        p = crops.plan_frame(self.frame(faces=faces), "group", self.CFG)["crops"]["group_16x9"]
        self.assertNotIn("face_cut", p["flags"])

    def test_unknown_selected_ids_raise(self):
        rows = [self.frame()]
        clusters = {"runs": [{"class": "solo", "frames": ["F1"]}]}
        with self.assertRaises(ValueError):
            crops.build(rows, clusters, ["F1", "GONE"], self.CFG)

    def test_frames_without_a_counted_face_get_no_crops(self):
        self.assertEqual(crops.plan_frame(self.frame(faces=[]), "none", self.CFG)["crops"], {})


class LookTest(unittest.TestCase):
    import numpy as np

    def test_di_round_trip(self):
        x = self.np.array([0.0, 0.001, 0.00262409, 0.01, 0.18, 1.0, 10.0, 80.0])
        self.assertTrue(self.np.allclose(look.di_decode(look.di_encode(x)), x, atol=1e-9))

    def test_default_look_is_the_identity(self):
        rgb = self.np.random.RandomState(1).rand(500, 3)
        self.assertTrue(self.np.allclose(look.apply(rgb, {}), rgb, atol=1e-9))

    def test_exposure_plus_one_stop_doubles_linear(self):
        grey = look.di_encode(self.np.array([0.18, 0.18, 0.18]))
        out = look.di_decode(look.apply(grey, {"exposure_ev": 1.0}))
        self.assertTrue(self.np.allclose(out, 0.36, atol=1e-6))

    def test_shoulder_is_identity_below_the_knee_and_never_exceeds_one(self):
        x = self.np.linspace(0, 40, 4001)
        y = look.shoulder(x, 0.65)
        self.assertTrue(self.np.allclose(y[x <= 0.65], x[x <= 0.65]))
        self.assertTrue(y.max() < 1.0 + 1e-9)
        self.assertTrue(self.np.all(self.np.diff(y) >= -1e-12))

    def test_trim_ev_is_zero_at_the_median_signed_clamped_and_snapped(self):
        self.assertEqual(look.trim_ev(38.0, 38.0), 0.0)
        self.assertGreater(look.trim_ev(30.0, 38.0), 0.0)      # darker than the median gets lifted
        self.assertLess(look.trim_ev(46.0, 38.0), 0.0)         # brighter gets pulled down
        self.assertEqual(look.trim_ev(5.0, 60.0), 0.6)         # clamped
        v = look.trim_ev(33.0, 38.0)
        self.assertAlmostEqual(v / 0.2, round(v / 0.2), places=6)  # snapped to the step

    def test_cube_has_the_right_size_and_red_varies_fastest(self):
        text = look.cube_text({}, size=5)
        lines = text.strip().splitlines()
        self.assertIn("LUT_3D_SIZE 5", lines)
        data = [l for l in lines if l and l[0].isdigit()]
        self.assertEqual(len(data), 125)
        self.assertAlmostEqual(float(data[1].split()[0]), 0.25, places=5)  # second entry: red = 0.25


class MeasureTest(unittest.TestCase):
    def test_lab_of_white_and_black(self):
        w = measure.lab([255, 255, 255]); b = measure.lab([0, 0, 0])
        self.assertAlmostEqual(w[0], 100.0, places=1); self.assertAlmostEqual(w[1], 0.0, delta=0.2)
        self.assertAlmostEqual(b[0], 0.0, places=1)

    def test_fit_look_recovers_a_known_move(self):
        import numpy as np
        start = np.array([100.0, 78.0, 74.0])
        lin = measure.srgb_to_linear(start) * (2.0 ** 0.8) * np.array([1.12, 1.0, 0.86])
        enc = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * lin ** (1 / 2.4) - 0.055) * 255.0
        ev, wb, resid = measure.fit_look(start, measure.lab(enc))
        self.assertAlmostEqual(ev, 0.8, delta=0.08)
        self.assertAlmostEqual(wb[0], 1.12, delta=0.04)
        self.assertAlmostEqual(wb[2], 0.86, delta=0.04)
        self.assertLess(resid, 1.0)

    def test_region_maps_into_the_rendered_crop_and_off_frame_is_none(self):
        from PIL import Image
        box = measure.face_region((6000, 4000), [1000, 500, 2000, 2000], {"x": 0.25, "y": 0.2, "w": 0.1, "h": 0.15}, 2048)
        self.assertTrue(0 <= box[0] < box[2] <= 2048 and 0 <= box[1] < box[3] <= 2048)
        self.assertIsNone(measure.skin_rgb(Image.new("RGB", (100, 100)), [90, 90, 120, 120]))


class IndexTest(unittest.TestCase):
    def test_frame_ids_fall_back_to_relative_paths_on_collision(self):
        from rpstills import index
        files = ["/s/a/P1.JPG", "/s/b/P2.JPG"]
        self.assertEqual(index.frame_ids(files, "/s"), ["P1", "P2"])
        files = ["/s/a/P1.JPG", "/s/b/P1.JPG"]
        self.assertEqual(index.frame_ids(files, "/s"), ["a__P1", "b__P1"])


class CullTest(unittest.TestCase):
    def test_one_pick_per_burst_and_quality_order(self):
        rows = [row(1, "2026-09-12T16:00:00", quality=0.9), row(2, "2026-09-12T16:00:01", quality=0.95),  # one burst
                row(3, "2026-09-12T16:00:10", quality=0.6, phash="f" * 16),
                row(4, "2026-09-12T16:00:20", quality=0.7, phash="a" * 16, eyes=0.1),               # eyes closed
                row(5, "2026-09-12T16:00:30", quality=0.4, phash="5" * 16)]
        c = cluster.build(rows)
        cu = cull.build(rows, c)
        picks = cu["proposal"][0]["picks"]
        self.assertEqual(picks[0], "F002")           # best of the burst
        self.assertNotIn("F001", picks)              # the burst gives one pick
        self.assertEqual(len(picks), 3)
        self.assertEqual(cu["scores"]["F004"]["eyes"], 0.0)
        self.assertLess(cu["scores"]["F004"]["score"], cu["scores"]["F003"]["score"])

    def test_no_face_run_proposes_nothing(self):
        rows = [row(1, "2026-09-12T16:00:00", faces=0)]
        cu = cull.build(rows, cluster.build(rows))
        self.assertEqual(cu["proposal"][0]["picks"], [])
        self.assertEqual(cu["scores"]["F001"]["score"], 0.0)


if __name__ == "__main__":
    unittest.main()
