"""
Offline tests for rpstills.retouch, rpstills.background, rpstills.finish,
the optional eye line in crops.solo_crop and the stills-config.json crop
profiles. A synthetic portrait drawn here (skin ellipse, eyes, brows, lips,
teeth, a torso on a textured background) with fabricated landmarks and a
fake Vision. No real media, no macOS Vision, no network.

Run: ~/.venvs/rp-tools/bin/python -m unittest production.tests.test_rpstills_finish
"""

import argparse
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import cv2
import numpy as np
from PIL import Image, ImageCms, JpegImagePlugin

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rpstills import background, crops, finish, measure, retouch, skin, vision  # noqa: E402

W, H = 800, 1000
SKIN = tuple(float(c) for c in measure.lab_to_srgb((64.0, 15.0, 14.0)))
TEETH = (0.88, 0.82, 0.62)       # pale yellow: HSV value 0.88, saturation 0.30
LIPS = (0.55, 0.2, 0.2)
EYE = (0.1, 0.08, 0.08)


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


def face(landmarks=True):
    f = {"x": 240 / W, "y": 300 / H, "w": 320 / W, "h": 320 / H, "confidence": 1.0}
    if landmarks:
        f["landmarks"] = {k: (v / [W, H]).tolist() for k, v in landmarks_px().items()}
    return f


SMALL = {"x": 0.05, "y": 0.05, "w": 0.04, "h": 0.04, "confidence": 0.9}  # Vision's face 0, not the subject


def fp():
    return skin.to_pixels(face(), W, H)


def portrait(textured=True):
    """float32 sRGB: a head and torso on a blue-grey background."""
    img = np.zeros((H, W, 3), np.float32)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    img[:] = np.dstack([0.30 + 0.10 * xx / W, 0.38 + 0.05 * yy / H, 0.55 + 0.0 * xx])
    if textured:
        img += np.random.default_rng(0).normal(0, 0.02, img.shape).astype(np.float32)
    cv2.fillPoly(img, [np.int32([[300, 640], [500, 640], [700, 1000], [100, 1000]])], (0.45, 0.12, 0.12))
    cv2.rectangle(img, (350, 600), (450, 700), SKIN, -1)
    cv2.ellipse(img, (400, 460), (165, 210), 0, 0, 360, SKIN, -1)
    lm = landmarks_px()
    for k, col in (("leftEye", EYE), ("rightEye", EYE), ("leftEyebrow", (0.25, 0.18, 0.15)),
                   ("rightEyebrow", (0.25, 0.18, 0.15)), ("outerLips", LIPS), ("innerLips", TEETH)):
        cv2.fillPoly(img, [np.round(lm[k]).astype(np.int32)], col)
    return np.clip(img, 0, 1)


def luma(rgb):
    return rgb @ np.float32(retouch.LUMA)


class HealTest(unittest.TestCase):
    def test_a_small_dark_spot_on_the_cheek_is_healed_and_a_large_patch_kept(self):
        img = portrait()
        cv2.circle(img, (330, 520), 2, tuple(c * 0.55 for c in SKIN), -1)      # blemish, ~13 px
        cv2.circle(img, (470, 610), 9, tuple(c * 0.55 for c in SKIN), -1)      # a mole-sized patch: a feature
        out, rep = retouch.heal_spots(img, fp())
        self.assertGreaterEqual(rep["spots"], 1)
        self.assertGreaterEqual(rep["kept_large"], 1)
        self.assertLess(abs(luma(out)[520, 330] - luma(img)[520, 345]), 0.03)  # now reads as the skin beside it
        self.assertLess(luma(img)[520, 330], luma(img)[520, 345] - 0.2)
        np.testing.assert_array_equal(out[600:621, 460:481], img[600:621, 460:481])

    def test_never_inside_the_eyes_brows_or_lips_even_on_a_mask_that_includes_them(self):
        img = portrait()
        for xy in ((300, 421), (400, 545), (330, 386)):      # in an eye, the lips, a brow
            cv2.circle(img, xy, 2, (0.02, 0.02, 0.02), -1)
        everything = np.zeros((H, W), np.float32)
        cv2.ellipse(everything, (400, 460), (165, 210), 0, 0, 360, 1.0, -1)
        out, rep = retouch.heal_spots(img, fp(), mask=everything)
        feats = retouch._features(fp(), img.shape)
        np.testing.assert_array_equal(out[feats], img[feats])

    def test_clean_skin_is_left_bit_identical(self):
        img = portrait()
        out, rep = retouch.heal_spots(img, fp())
        self.assertEqual(rep["spots"], 0)
        np.testing.assert_array_equal(out, img)


class EvennessTest(unittest.TestCase):
    def test_mid_band_blotches_are_reduced_by_about_the_strength_and_the_background_is_untouched(self):
        img = portrait()
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        blotch = 0.04 * np.sin(2 * np.pi * xx / 20) * np.sin(2 * np.pi * yy / 20)
        m = skin.skin_mask(fp(), (img * 255 + 0.5).astype(np.uint8))
        img = np.clip(img + (blotch * (m > 0.99))[..., None], 0, 1).astype(np.float32)
        out, rep = retouch.evenness(img, fp(), mask=m)
        patch = (slice(470, 530), slice(300, 360))   # left cheek, well inside the mask
        before, after = luma(img)[patch].std(), luma(out)[patch].std()
        self.assertLess(after, 0.85 * before)
        self.assertGreater(after, 0.55 * before)        # evened, not flattened
        self.assertEqual(rep["strength"], retouch.EVEN_STRENGTH)
        bg = m == 0
        np.testing.assert_array_equal(out[bg], img[bg])


    def test_a_shadowed_face_edge_is_not_lifted_into_a_rim(self):
        img = portrait()
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        r = np.sqrt(((xx - 400) / 165) ** 2 + ((yy - 460) / 210) ** 2)
        inside = r <= 1
        img[inside] *= (1 - 0.4 * np.clip((r[inside] - 0.8) / 0.2, 0, 1))[:, None]   # darker toward the jaw
        m = skin.skin_mask(fp(), retouch.to_u8(img))
        hard = (m > 0.5).astype(np.uint8)
        k = int(320 / retouch.SIGMA_LARGE_DIV)
        edge = (hard > 0) & ~(cv2.erode(hard, np.ones((2 * k + 1,) * 2, np.uint8)) > 0)
        out, _ = retouch.evenness(img, fp(), mask=m)
        d = luma(out) - luma(img)
        self.assertLess(float(d[edge].mean()), 0.001)          # an unfaded band-pass lifts it 0.0017
        self.assertLess(float(np.percentile(d[edge], 95)), 0.0035)


class EyesUnderEyeTeethTest(unittest.TestCase):
    def test_eyes_are_lifted_and_far_pixels_unchanged(self):
        img = portrait()
        img[410:431, 300:341] = 0.30
        out, rep = retouch.eyes(img, fp())
        self.assertGreater(luma(out)[420, 320], luma(img)[420, 320] * 1.04)
        np.testing.assert_array_equal(out[600:, :], img[600:, :])
        self.assertGreater(rep["eye_px"], 500)

    def test_under_eye_is_lifted_by_at_most_the_cap(self):
        img = portrait()
        area = skin.region_masks(fp(), img.shape)["under_eye"]
        img[area] *= 0.85
        out, rep = retouch.under_eye(img, fp())
        y = (459, 320)
        self.assertGreater(luma(out)[y], luma(img)[y] * 1.03)
        cap = 1 + (retouch.UNDER_EYE_MAX_LIFT - 1) * retouch.UNDER_EYE_MIX
        self.assertLessEqual(rep["max_gain"], cap + 1e-4)
        self.assertLessEqual(float((out / np.maximum(img, 1e-4)).max()), cap + 1e-3)

    def test_teeth_lose_saturation_and_gain_value_inside_the_inner_lips_only(self):
        img = portrait()
        out, rep = retouch.teeth(img, fp())
        hsv0 = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)[560, 400]
        hsv1 = cv2.cvtColor(out, cv2.COLOR_RGB2HSV)[560, 400]
        self.assertAlmostEqual(hsv1[1] / hsv0[1], 1 + retouch.TEETH_SAT, delta=0.03)
        self.assertAlmostEqual(hsv1[2] / hsv0[2], 1 + retouch.TEETH_VALUE, delta=0.01)
        inner = skin.poly(img.shape, landmarks_px()["innerLips"])
        np.testing.assert_array_equal(out[~inner], img[~inner])
        self.assertGreater(rep["px"], 500)


class DeglareTest(unittest.TestCase):
    def glare(self):
        img = portrait()
        lens = skin.region_masks(fp(), img.shape)["lens"] & ~retouch._features(fp(), img.shape)
        band = np.zeros((H, W), bool)
        band[360:400] = True                            # a cool bright veil across the upper lenses
        veil = lens & band
        img[veil] = 0.5 * img[veil] + 0.5 * np.float32([0.85, 0.92, 1.0])
        return img, veil

    def test_a_cool_veil_on_the_lenses_is_dimmed_and_reported(self):
        img, veil = self.glare()
        out, rep = retouch.deglare(img, fp())
        self.assertTrue(rep["applied"])
        self.assertGreater(rep["touched_px"], 0.3 * veil.sum())
        self.assertGreater(rep["mean_dL"], 3)
        lab0, lab1 = (cv2.cvtColor(a, cv2.COLOR_RGB2LAB) for a in (img, out))
        self.assertLess(lab1[veil][:, 0].mean(), lab0[veil][:, 0].mean() - 3)
        self.assertGreater(lab1[veil][:, 2].mean(), lab0[veil][:, 2].mean() + 2)   # warmer: toward the skin
        eyes = skin.region_masks(fp(), img.shape)["eyes"]
        np.testing.assert_allclose(out[eyes], img[eyes], atol=1e-6)

    def test_bright_forehead_inside_the_lens_ellipse_keeps_its_colour(self):
        img, veil = self.glare()
        img[:, :400][veil[:, :400]] = portrait()[:, :400][veil[:, :400]]   # veil on the right lens only
        brow = (slice(352, 372), slice(300, 341))       # above the left brow, inside the lens ellipse
        img[brow] = measure.lab_to_srgb((74.0, 15.0, 9.0))  # brighter and a little cooler, same redness
        out, rep = retouch.deglare(img, fp())
        self.assertTrue(rep["applied"])
        np.testing.assert_array_equal(out[brow], img[brow])
        right = veil.copy(); right[:, :400] = False
        lab0, lab1 = (cv2.cvtColor(a, cv2.COLOR_RGB2LAB) for a in (img, out))
        self.assertLess(lab1[right][:, 0].mean(), lab0[right][:, 0].mean() - 3)

    def test_no_glare_no_change(self):
        img = portrait()
        out, rep = retouch.deglare(img, fp())
        self.assertFalse(rep["applied"])
        self.assertEqual(rep["touched_px"], 0)
        np.testing.assert_array_equal(out, img)


class ApplyTest(unittest.TestCase):
    def test_every_step_runs_inside_the_face_region_and_reports(self):
        img = portrait()
        out, rep = retouch.apply(img, fp())
        self.assertEqual(list(rep["steps"]), list(retouch.STEPS))
        x, y, w, h = rep["roi"]
        pad = retouch.ROI_PAD * 320
        self.assertEqual((x, y), (int(240 - pad), int(300 - pad)))
        outside = np.ones((H, W), bool)
        outside[y:y + h, x:x + w] = False
        np.testing.assert_array_equal(out[outside], img[outside])
        self.assertEqual(out.dtype, np.float32)

    def test_the_region_gives_the_same_result_as_the_whole_frame(self):
        img = portrait()
        a, _ = retouch.apply(img, fp(), steps=("eyes", "teeth"))
        b, _ = retouch.eyes(img, fp())
        b, _ = retouch.teeth(b, fp())
        np.testing.assert_allclose(a, b, atol=1e-5)

    def test_unknown_steps_and_missing_landmarks_raise(self):
        with self.assertRaisesRegex(ValueError, "unknown retouch step"):
            retouch.apply(portrait(), fp(), steps=("liquify",))
        with self.assertRaisesRegex(ValueError, "landmarks"):
            retouch.apply(portrait(), skin.to_pixels(face(landmarks=False), W, H))

    def test_params_echo_every_strength(self):
        p = retouch.params()
        self.assertEqual(p["evenness"]["strength"], 0.30)
        self.assertEqual(p["eyes"]["lift"], 1.08)
        self.assertEqual(p["teeth"]["sat"], -0.35)
        self.assertEqual(p["under_eye"]["max_lift"], 1.12)
        self.assertEqual(p["heal_spots"]["threshold"], 0.09)
        json.dumps(p)


class BackgroundTest(unittest.TestCase):
    def test_subject_mask_holds_head_and_torso_not_the_background_and_repeats(self):
        img = portrait()
        m, info = background.subject_mask(img, fp())
        self.assertIsNotNone(m, info.get("reason"))
        self.assertEqual(m.shape, (H, W)); self.assertEqual(m.dtype, np.float32)
        for y, x in ((460, 400), (420, 320), (560, 400), (850, 400), (660, 400)):
            self.assertGreater(m[y, x], 0.9, (y, x))
        for y, x in ((40, 40), (40, 760), (300, 60), (300, 740), (150, 400)):
            self.assertLess(m[y, x], 0.1, (y, x))
        self.assertTrue(background.MIN_SHARE < info["share"] < background.MAX_SHARE)
        self.assertEqual(info["core_cover"], 1.0)
        again, _ = background.subject_mask(img, fp())
        np.testing.assert_array_equal(m, again)

    def test_a_face_core_off_the_frame_is_refused(self):
        off = dict(fp(), x=-900.0)
        m, info = background.subject_mask(portrait(), off)
        self.assertIsNone(m)
        self.assertIn("too small", info["reason"])

    def test_the_guard_refuses_a_mask_that_misses_the_core_or_is_too_small_or_too_big(self):
        core = (40, 40, 60, 60)
        mask = np.zeros((200, 200), np.uint8)
        mask[30:180, 30:120] = 1
        self.assertIsNone(background.check(mask, core))
        miss = mask.copy(); miss[40:60, 40:60] = 0
        self.assertIn("face core", background.check(miss, core))
        tiny = np.zeros_like(mask); tiny[40:60, 40:60] = 1
        self.assertIn("not a subject", background.check(tiny, core))
        full = np.ones_like(mask)
        self.assertIn("no background", background.check(full, core))
        self.assertIn("too small", background.check(mask, (40, 40, 42, 42)))

    def test_burn_darkens_and_desaturates_only_the_background(self):
        img = np.zeros((10, 20, 3), np.float32)
        img[:, :] = (0.5, 0.5, 0.5)
        img[:, 10:15] = (0.8, 0.4, 0.2)
        mask = np.zeros((10, 20), np.float32)
        mask[:, :5] = 1.0
        out, rep = background.burn(img, mask)
        np.testing.assert_array_equal(out[:, :5], img[:, :5])
        want = background.to_srgb(background.to_linear(np.float32(0.5)) * 2 ** background.BURN_EV)
        np.testing.assert_allclose(out[0, 7], [want] * 3, atol=1e-5)          # grey: only darker
        sat = lambda p: p.max() - p.min()  # noqa: E731
        dark = background.to_srgb(background.to_linear(np.float32([0.8, 0.4, 0.2])) * 2 ** background.BURN_EV)
        self.assertAlmostEqual(sat(out[0, 12]), sat(dark) * (1 - background.BURN_DESAT), places=4)
        self.assertEqual(rep["ev"], -0.6); self.assertEqual(rep["desat"], 0.15)
        self.assertAlmostEqual(rep["background_share"], 0.75)


class CropsEyeLineTest(unittest.TestCase):
    CFG = crops.load_config(str(HERE.parent / "stills-config.json"))

    def test_eye_y_places_the_profile_eye_line_on_the_measured_eyes(self):
        f = {"x": 0.43, "y": 0.22, "w": 0.14, "h": 0.22}
        x0, y0, w, h, tight = crops.solo_crop(f, 0.8, 6000, 4000, 3.2, 0.36, eye_y=0.30)
        self.assertFalse(tight)
        self.assertAlmostEqual((0.30 * 4000 - y0) / h, 0.36, places=6)

    def test_without_eye_y_the_crop_is_as_before(self):
        f = {"x": 0.43, "y": 0.22, "w": 0.14, "h": 0.22}
        fh = 0.22 * 4000
        h = 2.6 * fh
        cy = (0.22 * 4000 + 0.40 * fh) - 0.35 * h + h / 2
        want = crops._fit((0.5 * 6000, cy, h, h), 1.0, 6000, 4000)
        self.assertEqual(crops.solo_crop(f, 1.0, 6000, 4000, 2.6, 0.35), want)

    def test_config_has_the_new_profiles_and_keeps_the_old(self):
        p = self.CFG["profiles"]
        self.assertEqual(self.CFG["version"], "0.2.0")
        for name, aspect, k, eye in (("headshot_square_tight", [1, 1], 2.3, 0.40),
                                     ("headshot_4x5_eye36", [4, 5], 3.2, 0.36),
                                     ("halfbody_2x3", [2, 3], 5.2, 0.25)):
            self.assertEqual((p[name]["aspect"], p[name]["crop_height_in_face_heights"], p[name]["eye_line"]),
                             (aspect, k, eye))
            self.assertEqual(p[name]["classes"], ["solo"])
        for name, k, eye, out in (("headshot_square", 2.6, 0.35, [2048, 2048]),
                                  ("headshot_4x5", 3.2, 0.33, [1080, 1350]),
                                  ("portrait_16x9", 3.6, 0.38, [2400, 1350])):
            self.assertEqual((p[name]["crop_height_in_face_heights"], p[name]["eye_line"], p[name]["out"]),
                             (k, eye, out))
        self.assertTrue(p["group_16x9"]["union"])
        self.assertEqual(p["archive_master"]["format"], "tiff16")
        self.assertEqual(p["review_2048"]["quality"], 85)

    def test_the_crops_command_plans_the_new_profiles_for_a_solo_frame(self):
        row = {"id": "F1", "width": 6000, "height": 4000, "orientation": 1,
               "faces": [{"x": 0.45, "y": 0.30, "w": 0.10, "h": 0.15}]}
        names = set(crops.plan_frame(row, "solo", self.CFG)["crops"])
        self.assertTrue({"headshot_square_tight", "headshot_4x5_eye36", "halfbody_2x3"} <= names)


def save_tif16(path, rgb):
    cv2.imwrite(str(path), cv2.cvtColor((np.clip(rgb, 0, 1) * 65535 + 0.5).astype(np.uint16), cv2.COLOR_RGB2BGR))


class FinishRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rpstills-finish-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.out = self.tmp / "shoot"
        (self.out / "develop").mkdir(parents=True)
        self.img = portrait()
        for fid in ("F1", "F2"):
            save_tif16(self.out / "develop" / f"{fid}.tif", self.img)
        self.faces = {}

    def fake_faces(self, paths, landmarks=False):
        self.assertTrue(landmarks)
        out = {}
        for p in paths:
            fid = Path(p).stem
            faces = self.faces.get(fid, [SMALL, face()])
            size = (W, H) if fid != "ROT" else (H, W)
            out[p] = {"width": size[0], "height": size[1], "faces": faces, "people": [], "error": None}
        return out

    def run_cli(self, *argv):
        p = argparse.ArgumentParser()
        finish.add_arguments(p)
        args = p.parse_args([str(self.out), *argv])
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(vision, "faces", self.fake_faces), redirect_stdout(out), redirect_stderr(err):
            code = finish.run(args)
        return code, out.getvalue(), err.getvalue()

    def doc(self):
        return json.loads((self.out / "finish" / "finish.json").read_text())

    def test_defaults_crop_only_untouched_with_srgb_444_jpegs(self):
        code, out, err = self.run_cli()
        self.assertEqual(code, 0, err)
        doc = self.doc()
        self.assertEqual(doc["options"], {"retouch": [], "background": "off", "crops": ["headshot_4x5_eye36"]})
        r = {row["id"]: row for row in doc["frames"]}["F1"]
        self.assertEqual((r["retouch"], r["background"]), ("off", "off"))
        self.assertAlmostEqual(r["face"]["w"], 0.4)                       # the big face, not face 0
        c = r["crops"]["headshot_4x5_eye36"]
        path = self.out / "finish" / "headshot_4x5_eye36" / "F1.jpg"
        self.assertEqual(c["file"], str(path))
        with Image.open(path) as im:
            self.assertEqual(JpegImagePlugin.get_sampling(im), 0)         # 4:4:4
            icc = im.info.get("icc_profile")
            self.assertIn("srgb", ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))).lower())
            got = np.asarray(im, np.float32) / 255
        x, y, w, h = c["px"]
        self.assertEqual(list(got.shape[:2]), [h, w])
        self.assertAlmostEqual(w / h, 0.8, delta=0.002)
        self.assertLess(float(np.abs(got - self.img[y:y + h, x:x + w]).mean()), 4 / 255)   # JPEG error on the noise
        self.assertIn("2 of 2 frames finished", out)

    def test_eye_line_from_landmarks_lands_on_the_profile_line(self):
        code, _, err = self.run_cli("--crop", "headshot_square_tight", "headshot_4x5_eye36", "halfbody_2x3")
        self.assertEqual(code, 0, err)
        crops_ = {row["id"]: row for row in self.doc()["frames"]}["F1"]["crops"]
        c = crops_["headshot_square_tight"]
        self.assertEqual(c["eye_from"], "landmarks")
        self.assertAlmostEqual(c["eye_line_at"], 0.40, delta=0.002)
        self.assertEqual(c["flags"], [])
        x, y, w, h = c["px"]
        self.assertEqual(w, h)
        self.assertAlmostEqual((420 - y) / h, 0.40, delta=0.002)    # the eyes are drawn at y 420
        for name, aspect in (("headshot_4x5_eye36", 0.8), ("halfbody_2x3", 2 / 3)):
            c = crops_[name]                              # 3.2 and 5.2 face heights do not fit in 1000 px
            self.assertIn("tight", c["flags"])
            self.assertAlmostEqual(c["size"][0] / c["size"][1], aspect, delta=0.002)
            self.assertEqual(c["size"][1], H)

    def test_a_profile_with_an_out_size_is_resized_to_it_and_flags_upscale(self):
        code, _, err = self.run_cli("--crop", "headshot_4x5", "--ids", "F1")
        self.assertEqual(code, 0, err)
        c = self.doc()["frames"][0]["crops"]["headshot_4x5"]
        self.assertEqual(c["size"], [1080, 1350])
        self.assertIn("upscale", c["flags"])
        with Image.open(c["file"]) as im:
            self.assertEqual(im.size, (1080, 1350))

    def test_retouch_and_background_only_where_asked(self):
        code, out, err = self.run_cli("--retouch", "F1", "--background", "on")
        self.assertEqual(code, 0, err)
        doc = self.doc()
        rows = {row["id"]: row for row in doc["frames"]}
        self.assertEqual(set(rows["F1"]["retouch"]["steps"]), set(retouch.STEPS))
        self.assertEqual(rows["F2"]["retouch"], "off")
        for fid in ("F1", "F2"):
            bg = rows[fid]["background"]
            self.assertTrue(bg["applied"], bg)
            self.assertEqual(bg["ev"], -0.6)
        self.assertEqual(doc["params"]["retouch"]["evenness"]["strength"], 0.30)
        self.assertEqual(doc["params"]["background"]["seed"], 0)
        self.assertEqual(doc["params"]["jpeg_quality"], 93)
        with Image.open(self.out / "finish" / "headshot_4x5_eye36" / "F2.jpg") as im:
            f2 = np.asarray(im, np.float32) / 255
        x, y, w, h = rows["F2"]["crops"]["headshot_4x5_eye36"]["px"]
        src = self.img[y:y + h, x:x + w]
        self.assertLess(luma(f2)[10:40, 10:40].mean(), luma(src)[10:40, 10:40].mean() - 0.05)  # burned corner
        self.assertLess(abs(luma(f2)[420 - y, 400 - x] - luma(src)[420 - y, 400 - x]), 0.02)    # face kept
        self.assertIn("retouched 1, background burned 2", out)

    def test_retouch_all_and_comma_lists(self):
        self.assertEqual(finish.retouch_ids(["all"], ["F1", "F2"]), {"F1", "F2"})
        self.assertEqual(finish.retouch_ids(["F1,F2"], ["F1", "F2"]), {"F1", "F2"})
        self.assertEqual(finish.retouch_ids(["none"], ["F1"]), set())
        with self.assertRaises(ValueError):
            finish.retouch_ids(["F9"], ["F1"])
        with self.assertRaises(ValueError):
            finish.retouch_ids(["none", "F1"], ["F1"])

    def test_bad_frames_are_recorded_and_the_run_fails(self):
        save_tif16(self.out / "develop" / "NOFACE.tif", self.img)
        save_tif16(self.out / "develop" / "ROT.tif", self.img)
        self.faces["NOFACE"] = []
        code, out, err = self.run_cli("--retouch", "all")
        self.assertEqual(code, 1)
        rows = {row["id"]: row for row in self.doc()["frames"]}
        self.assertEqual(rows["NOFACE"]["error"], "no face")
        self.assertIn("pass upright files", rows["ROT"]["error"])
        self.assertTrue((self.out / "finish" / "headshot_4x5_eye36" / "F1.jpg").exists())
        self.assertIn("NOFACE: no face", err)

    def test_retouch_without_landmarks_is_recorded_not_fatal(self):
        self.faces["F1"] = [face(landmarks=False)]
        code, _, err = self.run_cli("--retouch", "F1", "--ids", "F1")
        self.assertEqual(code, 0, err)
        row = self.doc()["frames"][0]
        self.assertIn("not retouched", row["retouch"]["error"])
        self.assertEqual(row["crops"]["headshot_4x5_eye36"]["eye_from"], "box estimate")

    def test_refusals_before_any_work(self):
        cases = [(["--crop", "group_16x9"], "not a solo crop"), (["--crop", "nope"], "no profile"),
                 (["--ids", "F9"], "no file for id"), (["--retouch", "F9"], "not being finished"),
                 (["--src", str(self.tmp / "missing")], "not a folder")]
        for argv, msg in cases:
            code, _, err = self.run_cli(*argv)
            self.assertEqual(code, 1, argv)
            self.assertIn(msg, err)
        self.assertFalse((self.out / "finish").exists())

    def test_output_inside_a_git_tree_is_refused(self):
        (self.out / ".git").mkdir()
        code, _, err = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("git working tree", err)
        self.assertFalse((self.out / "finish").exists())

    def test_src_folder_and_no_vision(self):
        src = self.tmp / "graded"
        src.mkdir()
        Image.fromarray((self.img * 255 + 0.5).astype(np.uint8)).save(src / "G1.jpg", quality=95)
        code, _, err = self.run_cli("--src", str(src))
        self.assertEqual(code, 0, err)
        self.assertEqual([r["id"] for r in self.doc()["frames"]], ["G1"])
        p = argparse.ArgumentParser(); finish.add_arguments(p)
        with mock.patch.object(vision, "faces", lambda *a, **k: None), redirect_stderr(io.StringIO()) as e, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(finish.run(p.parse_args([str(self.out)])), 1)
        self.assertIn("Vision helper cannot run", e.getvalue())


if __name__ == "__main__":
    unittest.main()
