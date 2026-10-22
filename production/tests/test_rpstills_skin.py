"""
Offline tests for rpstills.skin (landmark masks), the rpstills.measure
additions (per-pixel Lab, hue, chroma, the Rec.709 vectorscope angle,
skin_lab) and the --landmarks pass-through in rpstills.vision. A synthetic
face drawn here with fabricated landmarks; no real media, no macOS Vision.

Run: ~/.venvs/rp-tools/bin/python -m unittest production.tests.test_rpstills_skin
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rpstills import measure, skin, vision  # noqa: E402

W, H = 800, 1000
TARGET = (64.0, 15.0, 14.0)


def ring(cx, cy, rx, ry, n=8):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return np.stack([cx + rx * np.cos(t), cy + ry * np.sin(t)], 1)


def landmarks_px():
    """Pixel landmarks of a frontal face whose box is (240, 300, 320, 320)."""
    t = np.linspace(0, np.pi, 11)
    return {"leftEye": ring(320, 420, 30, 10), "rightEye": ring(480, 420, 30, 10),
            "leftEyebrow": ring(320, 385, 35, 5, 6), "rightEyebrow": ring(480, 385, 35, 5, 6),
            "outerLips": ring(400, 560, 64, 22, 12), "innerLips": ring(400, 560, 50, 8),
            "faceContour": np.stack([400 + 155 * np.cos(t), 460 + 170 * np.sin(t)], 1),
            "nose": np.array([[400, 440], [388, 500], [412, 500]], float)}


def face(swap=False, w=W, h=H):
    """A normalized Vision-style face. swap=True relabels left and right."""
    lm = landmarks_px()
    if swap:
        lm["leftEye"], lm["rightEye"] = lm["rightEye"], lm["leftEye"]
        lm["leftEyebrow"], lm["rightEyebrow"] = lm["rightEyebrow"], lm["leftEyebrow"]
        lm["outerLips"] = lm["outerLips"][::-1]
    return {"x": 240 / W, "y": 300 / H, "w": 320 / W, "h": 320 / H, "confidence": 1.0,
            "landmarks": {k: (v / [W, H]).tolist() for k, v in lm.items()}}


def portrait(features=True):
    """float sRGB HxWx3: a skin ellipse at TARGET Lab on blue, with dark eyes and lips."""
    img = np.zeros((H, W, 3), np.float32)
    img[:] = (0.2, 0.3, 0.6)
    cv2.ellipse(img, (400, 460), (165, 210), 0, 0, 360, tuple(float(c) for c in measure.lab_to_srgb(TARGET)), -1)
    if features:
        lm = landmarks_px()
        for k, col in (("leftEye", (0.1, 0.08, 0.08)), ("rightEye", (0.1, 0.08, 0.08)),
                       ("outerLips", (0.55, 0.2, 0.2))):
            cv2.fillPoly(img, [np.round(lm[k]).astype(np.int32)], col)
    return img


class GeometryTest(unittest.TestCase):
    def test_to_pixels_scales_box_and_landmarks_and_keeps_other_keys(self):
        fp = skin.to_pixels(face(), W, H)
        self.assertAlmostEqual(fp["x"], 240); self.assertAlmostEqual(fp["w"], 320)
        self.assertAlmostEqual(fp["y"], 300); self.assertAlmostEqual(fp["h"], 320)
        self.assertEqual(fp["confidence"], 1.0)
        np.testing.assert_allclose(fp["landmarks"]["leftEye"], landmarks_px()["leftEye"], atol=1e-6)
        bare = skin.to_pixels({"x": 0.5, "y": 0.5, "w": 0.1, "h": 0.1}, 100, 200)
        self.assertNotIn("landmarks", bare); self.assertEqual(bare["h"], 20)

    def test_eye_centres_are_sorted_by_image_x_whatever_vision_calls_them(self):
        for swap in (False, True):
            left, right = skin.eye_centres(skin.to_pixels(face(swap), W, H))
            np.testing.assert_allclose(left, [320, 420], atol=1e-6)
            np.testing.assert_allclose(right, [480, 420], atol=1e-6)

    def test_cheek_patches_sit_40_percent_toward_the_same_side_mouth_corner(self):
        m = skin.cheek_mask(skin.to_pixels(face(), W, H), (H, W))
        self.assertEqual(m.dtype, bool); self.assertEqual(m.shape, (H, W))
        n, _, stats, cents = cv2.connectedComponentsWithStats(m.astype(np.uint8))
        self.assertEqual(n, 3)
        cents = sorted(cents[1:].tolist())
        # eye (320, 420) toward corner (336, 560); eye (480, 420) toward corner (464, 560)
        np.testing.assert_allclose(cents[0], [326.4, 476.0], atol=1.0)
        np.testing.assert_allclose(cents[1], [473.6, 476.0], atol=1.0)
        area = np.pi * (0.06 * 320) * (0.05 * 320)
        for a in stats[1:, cv2.CC_STAT_AREA]:
            self.assertAlmostEqual(a / area, 1.0, delta=0.06)

    def test_relabelled_left_and_right_give_the_same_cheeks(self):
        a = skin.cheek_mask(skin.to_pixels(face(False), W, H), (H, W))
        b = skin.cheek_mask(skin.to_pixels(face(True), W, H), (H, W))
        self.assertTrue(np.array_equal(a, b))

    def test_masks_scale_with_the_image_not_a_fixed_width(self):
        small = skin.cheek_mask(skin.to_pixels(face(), W, H), (H, W)).sum()
        big = skin.cheek_mask(skin.to_pixels(face(), 2 * W, 2 * H), (2 * H, 2 * W)).sum()
        self.assertAlmostEqual(big / small, 4.0, delta=0.2)

    def test_missing_landmarks_name_the_region(self):
        with self.assertRaisesRegex(ValueError, "outerLips"):
            skin.cheek_mask({"x": 0, "y": 0, "w": 10, "h": 10,
                             "landmarks": {"leftEye": [[1, 1]], "rightEye": [[5, 1]]}}, (20, 20))
        with self.assertRaisesRegex(ValueError, "leftEye"):
            skin.eye_centres({"x": 0, "y": 0, "w": 10, "h": 10})

    def test_region_masks(self):
        r = skin.region_masks(skin.to_pixels(face(), W, H), (H, W))
        self.assertEqual(set(r), {"eyes", "brows", "lips_outer", "lips_inner", "lens", "under_eye"})
        for m in r.values():
            self.assertEqual(m.dtype, bool); self.assertEqual(m.shape, (H, W))
        self.assertTrue(r["eyes"][420, 320] and r["eyes"][420, 480])
        self.assertTrue(r["brows"][385, 320] and r["brows"][385, 480])
        self.assertTrue(r["lips_outer"][560, 400] and r["lips_inner"][560, 400])
        self.assertFalse((r["lips_inner"] & ~r["lips_outer"]).any())
        # the inner lips reach the mouth corners (336, 464), past the inner points' own 350..450
        self.assertTrue(r["lips_inner"][560, 342] and r["lips_inner"][560, 458])
        # a lens ellipse per eye, centred at the eye (rims measured on real frames), much larger than it
        self.assertTrue(r["lens"][420, 320] and r["lens"][380, 480] and r["lens"][470, 480])
        self.assertFalse(r["lens"][360, 320])
        self.assertGreater(r["lens"].sum(), 5 * r["eyes"].sum())
        # under-eye: below the eye's lower edge (430) by 0.09 face heights, not on the eye nor the cheek sample
        self.assertTrue(r["under_eye"][459, 320] and r["under_eye"][459, 480])
        self.assertFalse((r["under_eye"] & r["eyes"]).any())
        self.assertFalse((r["under_eye"] & skin.cheek_mask(skin.to_pixels(face(), W, H), (H, W))).any())

    def test_skin_mask_keeps_skin_and_drops_eyes_lips_background_and_above_the_brows(self):
        rgb8 = (portrait() * 255 + 0.5).astype(np.uint8)
        m = skin.skin_mask(skin.to_pixels(face(), W, H), rgb8)
        self.assertEqual(m.dtype, np.float32); self.assertEqual(m.shape, (H, W))
        self.assertGreaterEqual(m.min(), 0.0); self.assertLessEqual(m.max(), 1.0)
        self.assertGreater(m[476, 326], 0.95)    # cheek
        self.assertGreater(m[300, 400], 0.95)    # forehead, from the lifted brows
        self.assertLess(m[50, 50], 0.01)         # background (fails the tone box)
        self.assertLess(m[420, 320], 0.05)       # eye
        self.assertLess(m[560, 400], 0.05)       # lips
        self.assertLess(m[258, 400], 0.05)       # skin tone, but above the lifted brows
        self.assertLess(m[400, 530], 0.05)       # skin tone beside the brows above the eye line (hair, on a real face)
        self.assertGreater(m[450, 535], 0.95)    # beside the eye below the eye line: kept

    def test_tone_mask_takes_an_image_or_a_list_of_pixels(self):
        rgb8 = (portrait() * 255 + 0.5).astype(np.uint8)
        img = skin.tone_mask(rgb8)
        self.assertEqual(img.shape, (H, W))
        self.assertTrue(img[476, 326]); self.assertFalse(img[50, 50])
        np.testing.assert_array_equal(skin.tone_mask(rgb8.reshape(-1, 3)), img.reshape(-1))

    def test_nblur_does_not_bleed_outside_the_mask(self):
        img = np.zeros((100, 100), np.float32); img[:, :50] = 1.0
        mask = (img > 0).astype(np.float32)
        out = skin.nblur(img, mask, 6)
        self.assertGreater(out[50, 49], 0.999)                       # a plain blur would give about 0.5
        self.assertLess(cv2.GaussianBlur(img, (0, 0), 6)[50, 49], 0.6)
        rgb = np.dstack([img, img * 0.5, img * 0.25])
        np.testing.assert_allclose(skin.nblur(rgb, mask, 6)[50, 10], [1.0, 0.5, 0.25], atol=1e-4)


class MeasureAdditionsTest(unittest.TestCase):
    def test_vectorscope_reference_angles(self):
        # mandatory: the Caucasian cheek mean and the stills trial target
        self.assertAlmostEqual(measure.vectorscope_deg(measure.lab_to_srgb((59.6, 11.8, 14.6))), 123.2, delta=0.3)
        self.assertAlmostEqual(measure.vectorscope_deg(measure.lab_to_srgb((64.0, 15.0, 14.0))), 117.7, delta=0.3)

    def test_vectorscope_draws_red_where_a_scope_does_and_ignores_scale(self):
        self.assertAlmostEqual(measure.vectorscope_deg([1, 0, 0]), 102.9, delta=0.2)
        s = measure.lab_to_srgb(TARGET)
        self.assertAlmostEqual(measure.vectorscope_deg(s * 255), measure.vectorscope_deg(s), places=9)

    def test_lab_image_is_lab_per_pixel_and_inverts(self):
        rng = np.random.default_rng(0)
        px = rng.uniform(0, 1, (64, 3)).astype(np.float32)
        lab = measure.lab_image(px.reshape(8, 8, 3)).reshape(-1, 3)
        self.assertEqual(lab.dtype, np.float32)
        for i in (0, 17, 63):
            np.testing.assert_allclose(lab[i], measure.lab(px[i] * 255), atol=2e-3)
        np.testing.assert_allclose(measure.lab_to_srgb(lab), px, atol=2e-4)

    def test_hue_and_chroma(self):
        self.assertAlmostEqual(float(measure.hue_deg(-1.0, -1.0)), 225.0)
        self.assertAlmostEqual(float(measure.hue_deg(1.0, 1.0)), 45.0)
        self.assertAlmostEqual(float(measure.chroma(3.0, 4.0)), 5.0)

    def test_skin_lab_on_the_cheeks_reads_the_painted_skin(self):
        s = measure.skin_lab(portrait(), skin.to_pixels(face(), W, H), "cheek")
        self.assertEqual(set(s), {"L", "a", "b", "hue_deg", "chroma", "vectorscope_deg", "n_px"})
        for got, want in zip((s["L"], s["a"], s["b"]), TARGET):
            self.assertAlmostEqual(got, want, delta=0.05)
        self.assertAlmostEqual(s["hue_deg"], float(measure.hue_deg(15, 14)), delta=0.1)
        self.assertAlmostEqual(s["chroma"], float(measure.chroma(15, 14)), delta=0.1)
        self.assertAlmostEqual(s["vectorscope_deg"], 117.7, delta=0.3)
        self.assertGreater(s["n_px"], 1500)

    def test_skin_lab_leaves_a_spectacle_rim_crossing_the_cheeks_out(self):
        fp = skin.to_pixels(face(), W, H)
        clean = measure.skin_lab(portrait(), fp, "cheek")
        img = portrait()
        img[468:474] = (0.12, 0.11, 0.11)      # a dark frame rim through both patches
        s = measure.skin_lab(img, fp, "cheek")
        for got, want in zip((s["L"], s["a"], s["b"]), TARGET):
            self.assertAlmostEqual(got, want, delta=0.05)
        self.assertLess(s["n_px"], clean["n_px"] * 0.95)
        whole = img[skin.cheek_mask(fp, img.shape)]
        self.assertLess(float(measure.lab_image(whole)[:, 0].mean()), 62.0)   # what the ungated mean was

    def test_skin_lab_measures_an_off_balance_render_whole(self):
        # a cool render takes the whole patch out of the tone box; the develop solve still needs a number
        cool = (64.0, 10.0, -12.0)
        img = portrait(features=False)
        cv2.ellipse(img, (400, 460), (165, 210), 0, 0, 360, tuple(float(c) for c in measure.lab_to_srgb(cool)), -1)
        fp = skin.to_pixels(face(), W, H)
        self.assertFalse(skin.tone_mask((img[476, 326] * 255 + 0.5).astype(np.uint8)))
        s = measure.skin_lab(img, fp, "cheek")
        self.assertAlmostEqual(s["b"], -12.0, delta=0.05)
        self.assertEqual(s["n_px"], int(skin.cheek_mask(fp, img.shape).sum()))

    def test_skin_lab_box_needs_no_landmarks(self):
        f = face(); del f["landmarks"]
        s = measure.skin_lab(portrait(features=False), skin.to_pixels(f, W, H), "box")
        self.assertAlmostEqual(s["L"], 64.0, delta=0.05)
        self.assertEqual(s["n_px"], 160 * 160)

    def test_skin_lab_refuses_a_bad_region_or_an_empty_sample(self):
        fp = skin.to_pixels(face(), W, H)
        with self.assertRaises(ValueError):
            measure.skin_lab(portrait(), fp, "forehead")
        off = dict(fp, x=-5000.0, y=-5000.0)
        with self.assertRaisesRegex(ValueError, "no pixel"):
            measure.skin_lab(portrait(), off, "box")


class NeutralTest(unittest.TestCase):
    """The face box is (240, 300, 320, 320): the search starts at y 668 and spans the frame's width."""
    SHIRT = (0.93, 0.92, 0.88)

    def scene(self):
        img = portrait()
        img[20:200, 20:120] = 1.0               # a white wall beside the head: above the search
        img[620:760, 340:460] = measure.lab_to_srgb(TARGET)   # the neck, skin-coloured
        img[760:, 150:650] = self.SHIRT         # a white shirt
        img[680:760, 150:300] = (0.85, 0.2, 0.2)   # a red scarf
        img[700:740, :120] = 0.85                # a pale path beside the body, clear of the column under the face
        return img, skin.to_pixels(face(), W, H)

    def test_the_shirt_is_kept_and_neck_scarf_path_and_what_is_above_are_not(self):
        img, fp = self.scene()
        m = skin.neutral_mask(img, fp)
        self.assertTrue(m[762:998, 152:648].all())
        self.assertFalse(m[:668].any())
        self.assertFalse(m[668:760, 340:460].any())
        self.assertFalse(m[680:760, 150:300].any())
        self.assertFalse(m[700:740, :120].any())
        self.assertEqual(int(m.sum()), 240 * 500)
        # skin passes min_L and max_chroma: only the tone gate keeps the neck out
        neck = measure.lab_image(measure.lab_to_srgb(TARGET))
        self.assertTrue(neck[0] >= skin.NEUTRAL_MIN_L and measure.chroma(neck[1], neck[2]) <= skin.NEUTRAL_MAX_CHROMA)

    def test_too_small_a_neutral_is_none(self):
        img, fp = self.scene()
        img[760:] = (0.2, 0.3, 0.6)              # a blue shirt: the path alone (4800 px) is not a neutral
        self.assertIsNone(skin.neutral_mask(img, fp))
        img[900:930, 380:420] = self.SHIRT       # 1200 px, under 0.002 of 800 000
        self.assertIsNone(skin.neutral_mask(img, fp))
        self.assertEqual(int(skin.neutral_mask(img, fp, min_share=0.001).sum()), 1200)

    def test_a_white_in_warm_light_is_kept_and_the_neck_still_is_not(self):
        img, fp = self.scene()
        warm = measure.lab_to_srgb((85.0, 2.0, 10.0))   # inside the YCrCb skin box, but far from the cheek's colour
        self.assertTrue(skin.tone_mask((warm * 255 + 0.5).astype(np.uint8)))
        img[760:, 150:650] = warm
        m = skin.neutral_mask(img, fp)
        self.assertEqual(int(m.sum()), 240 * 500)
        self.assertFalse(m[668:760, 340:460].any())

    def test_a_light_wall_beside_the_neck_is_not_a_neutral(self):
        # the neck is narrower than the face box, so the wall beside it lies in the column under the face
        for wall in ((70.0, 0.0, 0.0), (70.0, 6.0, -12.0), (90.0, 0.0, 0.0)):
            img, fp = portrait(), skin.to_pixels(face(), W, H)
            img[600:] = measure.lab_to_srgb(wall)
            img[620:800, 340:460] = measure.lab_to_srgb(TARGET)       # the neck
            img[800:, 150:650] = measure.lab_to_srgb((25.0, 2.0, -3.0))  # a dark top: no neutral in frame
            self.assertIsNone(skin.neutral_mask(img, fp), wall)
        img[600:] = measure.lab_to_srgb((70.0, 6.0, -12.0))
        img[620:800, 340:460] = measure.lab_to_srgb(TARGET)
        img[800:, 150:650] = measure.lab_to_srgb((92.0, 0.0, 0.0))   # a white shirt joined to a tinted wall
        self.assertIsNone(skin.neutral_mask(img, fp))                # the wall would move the median: no neutral

    def test_an_18_percent_grey_card_below_the_face_is_found(self):
        img, fp = self.scene()
        img[760:] = (0.2, 0.3, 0.6)                                   # a blue shirt
        img[800:950, 330:470] = measure.lab_to_srgb((49.5, 0.0, 0.0))   # 116 * 0.18 ** (1 / 3) - 16
        m = skin.neutral_mask(img, fp)
        self.assertEqual(int(m.sum()), 150 * 140)
        self.assertTrue(m[800:950, 330:470].all())

    def test_a_pale_print_is_not_a_neutral(self):
        img, fp = self.scene()
        img[760:] = (0.2, 0.3, 0.6)
        tints = [(85.0, 10.0, 2.0), (82.0, 6.0, -10.0), (90.0, 0.0, 10.0)]   # pink, lilac, cream petals
        for i, y in enumerate(range(764, 1000, 24)):
            for j, x in enumerate(range(154, 640, 24)):
                img[y:y + 14, x:x + 14] = measure.lab_to_srgb(tints[(i + j) % 3])
        self.assertIsNone(skin.neutral_mask(img, fp))
        self.assertIsNotNone(skin.neutral_mask(*self.scene()))

    def test_neutral_lab_is_the_median_and_leaves_clipped_pixels_out(self):
        img, fp = self.scene()
        m = skin.neutral_mask(img, fp)
        img[880:, 150:650] = 1.0                 # the lower half of the shirt blown out
        n = measure.neutral_lab(img, m)
        self.assertEqual(n["n_px"], 120 * 500)
        np.testing.assert_allclose([n["L"], n["a"], n["b"]], measure.lab_image(np.array(self.SHIRT)), atol=0.01)
        self.assertAlmostEqual(n["chroma"], float(measure.chroma(n["a"], n["b"])), places=1)
        img[760:] = 1.0
        self.assertIsNone(measure.neutral_lab(img, m))


class DeltaE2000Test(unittest.TestCase):
    def test_sharma_pairs(self):
        # Sharma, Wu and Dalal (2005), table 1
        pairs = [((50, 2.6772, -79.7751), (50, 0, -82.7485), 2.0425), ((50, 2.49, -0.001), (50, -2.49, 0.0009), 7.1792),
                 ((50, 2.5, 0), (73, 25, -18), 27.1492), ((50, 0, 0), (50, -1, 2), 2.3669),
                 ((50, 2.5, 0), (56, -27, -3), 31.9030), ((50, 2.5, 0), (61, -5, 29), 22.8977),
                 ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
                 ((2.0776, 0.0795, -1.1350), (0.9033, -0.0636, -0.5514), 0.9082)]
        a, b, want = (np.array([p[i] for p in pairs], dtype=np.float64) for i in range(3))
        np.testing.assert_allclose(measure.delta_e2000(a, b), want, atol=1e-4)
        self.assertAlmostEqual(float(measure.delta_e2000(a[0], b[0])), 2.0425, places=4)   # one pair, a scalar
        self.assertEqual(measure.delta_e2000(a.reshape(2, 4, 3), b[0]).shape, (2, 4))       # broadcast

    def test_lutstats_uses_this_copy(self):
        from lutlab import lutstats
        self.assertIs(lutstats.delta_e2000, measure.delta_e2000)


class VisionLandmarksTest(unittest.TestCase):
    def run_wrapper(self, **kw):
        line = json.dumps({"path": "/x/a.jpg", "width": W, "height": H, "faces": [face()], "people": []})
        proc = mock.Mock(returncode=0, stdout=line + "\n")
        with mock.patch.object(vision, "binary", lambda: "/bin/true"), \
                mock.patch.object(vision.subprocess, "run", return_value=proc) as run:
            out = vision.faces(["/x/a.jpg"], **kw)
        return out, run.call_args[0][0]

    def test_landmarks_are_asked_for_with_the_flag_first_and_passed_through(self):
        out, argv = self.run_wrapper(landmarks=True)
        self.assertEqual(argv, ["/bin/true", "--landmarks", "/x/a.jpg"])
        self.assertEqual(len(out["/x/a.jpg"]["faces"][0]["landmarks"]["outerLips"]), 12)

    def test_without_landmarks_the_call_is_as_before(self):
        _, argv = self.run_wrapper()
        self.assertEqual(argv, ["/bin/true", "/x/a.jpg"])

    def test_the_helper_takes_the_flag_and_writes_every_region(self):
        src = (HERE.parent / "rpstills" / "vision_stills.swift").read_text()
        self.assertIn('"--landmarks"', src)
        for name in ("leftEye", "rightEye", "leftEyebrow", "rightEyebrow", "outerLips", "innerLips",
                     "faceContour", "nose"):
            self.assertIn(f'("{name}", ', src)


if __name__ == "__main__":
    unittest.main()
