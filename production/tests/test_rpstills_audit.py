"""
Offline tests for rpstills.audit: synthetic portraits written here (8- and
16-bit TIFF, a JPEG with an sRGB profile) with fabricated landmarks and a
fake Vision. No real media, no macOS Vision, no network.

Run: ~/.venvs/rp-tools/bin/python -m unittest production.tests.test_rpstills_audit
"""

import argparse
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import cv2
import numpy as np
from PIL import Image, ImageCms

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rpstills import audit, measure  # noqa: E402

W, H = 800, 1000
TARGET = (64.0, 15.0, 14.0)
BLUE = (0.2, 0.3, 0.6)


def ring(cx, cy, rx, ry, n=8):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return np.stack([cx + rx * np.cos(t), cy + ry * np.sin(t)], 1)


def big_face(landmarks=True):
    """Normalized face, box (240, 300, 320, 320) px on 800x1000, eyes at y 420."""
    f = {"x": 0.3, "y": 0.3, "w": 0.4, "h": 0.32, "confidence": 1.0}
    if landmarks:
        t = np.linspace(0, np.pi, 11)
        px = {"leftEye": ring(320, 420, 30, 10), "rightEye": ring(480, 420, 30, 10),
              "leftEyebrow": ring(320, 385, 35, 5, 6), "rightEyebrow": ring(480, 385, 35, 5, 6),
              "outerLips": ring(400, 560, 64, 22, 12), "innerLips": ring(400, 560, 50, 8),
              "faceContour": np.stack([400 + 155 * np.cos(t), 460 + 170 * np.sin(t)], 1),
              "nose": np.array([[400, 440], [388, 500], [412, 500]], float)}
        f["landmarks"] = {k: (v / [W, H]).tolist() for k, v in px.items()}
    return f


SMALL = {"x": 0.05, "y": 0.05, "w": 0.04, "h": 0.04, "confidence": 0.9}  # Vision's face 0, not the subject


def portrait(bright_bg=False, bright_face=False, eye_texture=None, skin=TARGET, garment=None):
    """float sRGB: skin ellipse at the skin Lab (TARGET) on blue, dark eyes
    and lips; garment, a Lab, fills y 700..1000, x 150..650 below the face."""
    img = np.zeros((H, W, 3), np.float32)
    img[:] = BLUE
    if garment is not None:
        img[700:, 150:650] = measure.lab_to_srgb(garment)
    cv2.ellipse(img, (400, 460), (165, 210), 0, 0, 360, tuple(float(c) for c in measure.lab_to_srgb(skin)), -1)
    for c in ((320, 420), (480, 420)):
        cv2.ellipse(img, c, (30, 10), 0, 0, 360, (0.1, 0.08, 0.08), -1)
    cv2.ellipse(img, (400, 560), (64, 22), 0, 0, 360, (0.55, 0.2, 0.2), -1)
    if eye_texture is not None:
        img[395:445, 280:520] = eye_texture
    if bright_bg:
        img[10:70, 10:70] = 0.95     # L* 95.5, background, near the head
        img[0:20, 780:800] = 1.0     # 400 clipped pixels
    if bright_face:
        img[290:320, 385:415] = 1.0  # forehead: inside the face ellipse, not background
    return img


def save_tif(path, rgb, bits=8):
    scale = 255 if bits == 8 else 65535
    dtype = np.uint8 if bits == 8 else np.uint16
    cv2.imwrite(str(path), cv2.cvtColor((np.clip(rgb, 0, 1) * scale + 0.5).astype(dtype), cv2.COLOR_RGB2BGR))


def save_srgb_jpeg(path, rgb, orientation=None):
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    exif = Image.Exif()
    if orientation:
        exif[0x0112] = orientation
    Image.fromarray((rgb * 255 + 0.5).astype(np.uint8)).save(path, quality=97, subsampling=0, icc_profile=icc,
                                                              exif=exif)


def det(faces, w=W, h=H):
    return {"width": w, "height": h, "faces": faces, "people": [], "error": None}


def args(src, out=None, profile=None, hero=None):
    p = argparse.ArgumentParser()
    audit.add_arguments(p)
    return p.parse_args([str(src)] + (["--out", str(out)] if out else []) + (["--profile", profile] if profile else [])
                        + (["--hero", str(hero)] if hero else []))


class AuditMeasureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rpstills-audit-"))
        self.addCleanup(shutil.rmtree, self.tmp)

    def row(self, rgb, name="a.tif", faces=None, profile=None, bits=8):
        path = str(self.tmp / name)
        save_tif(path, rgb, bits) if name.endswith(".tif") else save_srgb_jpeg(path, rgb)
        self.doc = audit.build([path], {path: det([SMALL, big_face()] if faces is None else faces)},
                               "p" if profile else None, profile)
        return self.doc["files"][0]

    def test_the_largest_face_is_measured_with_its_geometry(self):
        r = self.row(portrait())
        self.assertNotIn("error", r)
        self.assertEqual(r["faces"], 2)
        self.assertAlmostEqual(r["face_w"], 0.4, places=4)                 # the big face, not face 0
        self.assertAlmostEqual(r["headroom"], (300 - 0.6 * 320) / 1000, places=4)
        self.assertAlmostEqual(r["eye_y"], 0.42, places=4)
        self.assertAlmostEqual(r["aspect"], 0.8, places=4)

    def test_skin_numbers_and_no_fixed_reference_columns(self):
        r = self.row(portrait())
        self.assertEqual(r["skin_region"], "cheek")
        for key, want in zip(("skin_L", "skin_a", "skin_b"), TARGET):
            self.assertAlmostEqual(r[key], want, delta=0.3)
        self.assertAlmostEqual(r["vectorscope_deg"], 117.7, delta=0.4)   # kept, a plain measurement
        self.assertAlmostEqual(r["chroma"], float(np.hypot(r["skin_a"], r["skin_b"])), delta=0.02)
        for gone in ("d_trial_deg", "d_skin_line_deg"):
            self.assertNotIn(gone, r)
        self.assertFalse(hasattr(audit, "REFERENCES"))
        self.assertEqual((r["d_E00"], r["d_L"], r["checks"]), (0.0, 0.0, []))  # one file is its own median

    def test_sixteen_bit_tiff_reads_at_full_precision_and_has_no_profile(self):
        r = self.row(portrait(), name="d16.tif", bits=16)
        self.assertAlmostEqual(r["skin_L"], 64.0, delta=0.02)
        self.assertIsNone(r["icc"]); self.assertFalse(r["srgb_icc"])

    def test_srgb_profile_is_found_in_a_jpeg(self):
        r = self.row(portrait(), name="c.jpg")
        self.assertTrue(r["srgb_icc"]); self.assertIn("sRGB", r["icc"])
        self.assertAlmostEqual(r["vectorscope_deg"], 117.7, delta=1.0)

    def test_clip_share_counts_any_channel_at_252(self):
        r = self.row(portrait(bright_bg=True))
        self.assertAlmostEqual(r["clip_pct"], 400 / (W * H) * 100, places=4)
        self.assertEqual(self.row(portrait())["clip_pct"], 0.0)

    def test_background_peak_sees_the_bright_corner_but_not_the_face(self):
        bright = self.row(portrait(bright_bg=True))
        self.assertGreater(bright["bg_peak_L"], 95.0)
        self.assertGreater(bright["bg_share"], 0.0)
        plain = self.row(portrait(bright_face=True))
        self.assertAlmostEqual(plain["bg_peak_L"], float(measure.lab_image(np.float32(BLUE))[0]), delta=0.5)

    def test_profile_distances(self):
        r = self.row(portrait(), profile={"aspect": [4, 5], "eye_line": 0.33})
        self.assertAlmostEqual(r["aspect_err"], 0.0, places=4)
        self.assertAlmostEqual(r["eye_y_err"], 0.42 - 0.33, places=4)
        r = self.row(portrait(), profile={"aspect": [1, 1], "eye_line": 0.35})
        self.assertAlmostEqual(r["aspect_err"], -0.2, places=4)
        r = self.row(portrait(), profile={"aspect": None})
        self.assertIsNone(r["aspect_err"])

    def test_without_landmarks_the_box_sample_stands_in(self):
        r = self.row(portrait(), faces=[big_face(landmarks=False)])
        self.assertEqual(r["skin_region"], "box")
        self.assertNotIn("eye_y", r); self.assertNotIn("eye_sharp", r)
        self.assertIn("headroom", r)

    def test_no_face_and_a_rotated_file_are_reported_not_measured(self):
        r = self.row(portrait(bright_bg=True), faces=[])
        self.assertEqual(r["error"], "no face")
        self.assertGreater(r["clip_pct"], 0)
        path = str(self.tmp / "rot.tif")
        save_tif(path, portrait())
        r = audit.build([path], {path: det([big_face()], w=H, h=W)})["files"][0]
        self.assertIn("upright", r["error"]); self.assertNotIn("skin_L", r)
        r = audit.build([path], {})["files"][0]
        self.assertIn("Vision returned nothing", r["error"])

    def test_an_exif_orientation_other_than_1_is_applied_and_the_row_says_so(self):
        # stored turned the way a camera holds, tagged so a viewer turns it back; Vision reports the upright size
        up = portrait()
        stored = {"o1.jpg": (up, 1), "o3.jpg": (up[::-1, ::-1], 3), "o6.jpg": (np.rot90(up, 1), 6),
                  "o8.jpg": (np.rot90(up, -1), 8), "o2.jpg": (up[:, ::-1], 2)}
        paths = {}
        for name, (img, o) in stored.items():
            paths[name] = str(self.tmp / name)
            save_srgb_jpeg(paths[name], np.ascontiguousarray(img), orientation=o)
        tif = str(self.tmp / "o3.tif")
        Image.fromarray((up[::-1, ::-1] * 255 + 0.5).astype(np.uint8)).save(tif, tiffinfo={274: 3})
        doc = audit.build([*paths.values(), tif], {p: det([big_face()]) for p in [*paths.values(), tif]})
        rows = {r["file"]: r for r in doc["files"]}
        base = rows["o1.jpg"]
        self.assertNotIn("error", base); self.assertFalse(base["orientation_applied"])
        for name, o in (("o3.jpg", 3), ("o6.jpg", 6), ("o8.jpg", 8), ("o2.jpg", 2), ("o3.tif", 3)):
            r = rows[name]
            self.assertNotIn("error", r, name)
            self.assertEqual((r["orientation"], r["orientation_applied"], r["width"], r["height"]), (o, True, W, H))
            for k in ("skin_L", "eye_y", "bg_peak_L", "headroom"):          # the same picture once turned upright
                self.assertAlmostEqual(r[k], base[k], delta={"skin_L": 0.6, "bg_peak_L": 0.5}.get(k, 0.01), msg=f"{name} {k}")

    def test_an_unreadable_file_is_a_row_with_an_error(self):
        path = self.tmp / "bad.jpg"
        path.write_bytes(b"not an image")
        r = audit.build([str(path)], {})["files"][0]
        self.assertIn("unreadable", r["error"])

    def test_eye_box_width_is_reported_with_the_sharpness(self):
        # eyes span x 290..510, padded by 10% of 220 each side
        r = self.row(portrait())
        self.assertEqual(r["eye_box_px"], 264)
        self.assertIsNotNone(r["eye_sharp"])

    def test_eye_sharpness_ranks_and_survives_an_upscale(self):
        # band-limited detail (lashes, iris), not pixel noise, which no resampling keeps; a downscaled file
        # loses detail and reads softer, which is why eye_box_px goes with every row
        rng = np.random.default_rng(0)
        tex = cv2.GaussianBlur(rng.uniform(0.2, 0.8, (50, 240)).astype(np.float32), (0, 0), 1.5)
        tex = ((tex - tex.mean()) / tex.std() * 0.15 + 0.5)[..., None].repeat(3, 2)
        sharp_img = portrait(eye_texture=tex)
        soft_img = cv2.GaussianBlur(sharp_img, (0, 0), 2.0)
        from rpstills import skin
        fp = skin.to_pixels(big_face(), W, H)
        sharp, soft = audit.eye_sharpness(sharp_img, fp), audit.eye_sharpness(soft_img, fp)
        self.assertGreater(sharp, 3 * soft)
        twice = cv2.resize(sharp_img, (2 * W, 2 * H), interpolation=cv2.INTER_CUBIC)
        sharp2 = audit.eye_sharpness(twice, skin.to_pixels(big_face(), 2 * W, 2 * H))
        self.assertAlmostEqual(sharp2 / sharp, 1.0, delta=0.3)

    def test_neutral_err_on_a_tinted_white_garment(self):
        # green-yellow, the cast a fixed skin target gave a white shirt; a warm cast past about C* 4 falls in the
        # skin tone box, so neutral_mask leaves it out and the row reads no neutral
        r = self.row(portrait(garment=(85.0, -3.0, 6.0)))
        self.assertAlmostEqual(r["neutral_a"], -3.0, delta=0.5)
        self.assertAlmostEqual(r["neutral_b"], 6.0, delta=0.5)
        self.assertAlmostEqual(r["neutral_err"], float(np.hypot(3, 6)), delta=0.5)
        self.assertGreater(r["neutral_n_px"], 100000)
        self.assertEqual(r["neutral_clip_pct"], 0.0)
        self.assertEqual(r["checks"], [])   # counted on its own, not a check: see the pale coloured shirt below
        self.assertIn("neutral read on 1, 1 over neu.err 2", audit.summary(self.doc))
        self.assertIn("0 flagged", audit.summary(self.doc))
        r = self.row(portrait(garment=(85.0, 0.0, 0.0)))
        self.assertLess(r["neutral_err"], 0.5)
        self.assertIn("neutral read on 1, 0 over", audit.summary(self.doc))

    def test_a_pale_coloured_shirt_reads_its_own_colour_and_raises_no_check(self):
        # light blue, chroma under 25, passes the mask: a per-file chroma cannot tell it from a cast
        r = self.row(portrait(garment=(80.0, -5.0, -20.0)))
        self.assertAlmostEqual(r["neutral_err"], float(np.hypot(5, 20)), delta=0.5)
        self.assertEqual(r["checks"], [])
        self.assertIn("0 flagged", audit.summary(self.doc))

    def test_no_garment_no_neutral_and_the_summary_counts_it(self):
        r = self.row(portrait())
        for k in ("neutral_a", "neutral_b", "neutral_n_px", "neutral_err", "neutral_clip_pct"):
            self.assertIsNone(r.get(k), k)
        self.assertNotIn("neutral_err", r["checks"])
        self.assertIn("neutral read on 0, 0 over neu.err 2", audit.summary(self.doc))
        self.assertIn("1 without a neutral", audit.summary(self.doc))   # a set with no neutral says so

    def test_a_blown_garment_is_not_read_on_its_edge(self):
        # a flat white shirt clipped in every channel, softened as a lens does: the unclipped rest is its edge
        # against the blue background, which read as a blue cast of C* 6
        r = self.row(cv2.GaussianBlur(portrait(garment=(100.0, 0.0, 0.0)), (0, 0), 1.5), bits=16)
        self.assertIsNone(r["neutral_err"]); self.assertIsNone(r["neutral_n_px"])
        self.assertIn("blown", r["neutral_note"])
        self.assertGreater(r["neutral_clip_pct"], 90)
        self.assertIn("1 without a neutral", audit.summary(self.doc))
        # a third of it blown: still read on the rest, and the share left out is reported
        img = portrait(garment=(85.0, -3.0, 6.0))
        img[900:, 150:650] = 1.0
        r = self.row(img, bits=16)
        self.assertAlmostEqual(r["neutral_clip_pct"], 100 / 3, delta=1.0)
        self.assertAlmostEqual(r["neutral_b"], 6.0, delta=0.5)
        self.assertNotIn("neutral_note", r)

    def test_hue_is_not_compared_near_grey(self):
        # a black-and-white set: chroma under HUE_MIN_CHROMA, so the hue angle is noise and d_hue_deg is None
        paths = []
        for i, lab in enumerate(((64.0, 0.3, 0.2), (63.5, -0.2, 0.3), (64.5, 0.1, -0.3))):
            paths.append(str(self.tmp / f"g{i}.tif"))
            save_tif(paths[-1], portrait(skin=lab), bits=16)
        doc = audit.build(paths, {p: det([big_face()]) for p in paths})
        for r in doc["files"]:
            self.assertIsNone(r["d_hue_deg"], r["file"]); self.assertEqual(r["checks"], [], r["file"])
            self.assertLess(r["d_E00"], 1.0)
        self.assertEqual(doc["params"]["hue_min_chroma"], audit.HUE_MIN_CHROMA)
        ref = {"L": 64.0, "a": 2.0, "b": 2.5}
        self.assertIsNone(audit.deviations({"skin_L": 64.0, "skin_a": 1.6, "skin_b": 2.6}, ref)["d_hue_deg"])
        self.assertIsNotNone(audit.deviations({"skin_L": 64.0, "skin_a": 15.0, "skin_b": 14.0},
                                              {"L": 64.0, "a": 13.0, "b": 17.0})["d_hue_deg"])

    def test_spread(self):
        s = audit.spread([{"skin_L": 1.0}, {"skin_L": 3.0}, {"skin_L": None}, {}])
        self.assertEqual(s["skin_L"], {"n": 2, "mean": 2.0, "sd": 1.0, "min": 1.0, "max": 3.0, "range": 2.0})
        self.assertNotIn("hue_deg", s)

    def test_wrap_deg(self):
        self.assertAlmostEqual(audit.wrap_deg(359.0 - 1.0), -2.0)
        self.assertAlmostEqual(audit.wrap_deg(-190.0), 170.0)


A, B, C = (64.0, 15.0, 14.0), (60.0, 13.0, 17.0), (68.0, 18.0, 10.0)  # component median: A


class SetConsistencyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rpstills-audit-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.paths = []
        for name, lab in (("a.tif", A), ("b.tif", B), ("c.tif", C)):
            path = str(self.tmp / name)
            save_tif(path, portrait(skin=lab), bits=16)
            self.paths.append(path)

    def build(self, hero=None):
        return audit.build(self.paths, {p: det([big_face()]) for p in self.paths}, hero=hero)

    def test_the_median_reference_and_each_file_against_it(self):
        doc = self.build()
        ref = doc["reference"]
        self.assertEqual((ref["source"], ref["file"], ref["n"]), ("median", None, 3))
        for got, want in zip((ref["L"], ref["a"], ref["b"]), A):
            self.assertAlmostEqual(got, want, delta=0.05)
        rows = {r["file"]: r for r in doc["files"]}
        for name, lab in (("a.tif", A), ("b.tif", B), ("c.tif", C)):
            r = rows[name]
            self.assertAlmostEqual(r["d_E00"], float(measure.delta_e2000(A, lab)), delta=0.1, msg=name)
            self.assertAlmostEqual(r["d_L"], lab[0] - A[0], delta=0.1, msg=name)
            self.assertAlmostEqual(r["d_chroma"], float(np.hypot(*lab[1:]) - np.hypot(*A[1:])), delta=0.1, msg=name)
            want_hue = np.degrees(np.arctan2(lab[2], lab[1]) - np.arctan2(A[2], A[1]))
            self.assertAlmostEqual(r["d_hue_deg"], float(want_hue), delta=0.3, msg=name)
        self.assertAlmostEqual(rows["b.tif"]["d_hue_deg"], 9.56, delta=0.3)   # signed: b is yellower
        self.assertAlmostEqual(rows["c.tif"]["d_hue_deg"], -13.98, delta=0.3)

    def test_checks_fire_past_the_working_defaults(self):
        doc = self.build()
        rows = {r["file"]: r for r in doc["files"]}
        self.assertEqual(rows["a.tif"]["checks"], [])
        self.assertEqual(rows["b.tif"]["checks"], ["d_E00", "d_L", "d_hue_deg"])
        self.assertEqual(rows["c.tif"]["checks"], ["d_E00", "d_L", "d_hue_deg"])
        self.assertEqual(doc["params"]["check"], audit.CHECK)
        self.assertIn("2 flagged (d_E00 2, d_L 2, d_hue_deg 2)", audit.summary(doc))
        self.assertIn("check: d_E00, d_L, d_hue_deg", audit.table(doc))
        with mock.patch.dict(audit.CHECK, {"d_L": 5.0, "d_hue_deg": 15.0, "d_E00": 6.0}):
            self.assertEqual([r["checks"] for r in self.build()["files"]], [[], [], []])

    def test_a_hero_replaces_the_median(self):
        doc = self.build(hero=self.paths[1])
        ref = doc["reference"]
        self.assertEqual((ref["source"], ref["file"], ref["n"]), ("hero", "b.tif", 1))
        rows = {r["file"]: r for r in doc["files"]}
        self.assertEqual(rows["b.tif"]["d_E00"], 0.0)
        self.assertAlmostEqual(rows["a.tif"]["d_L"], A[0] - B[0], delta=0.1)
        self.assertAlmostEqual(rows["c.tif"]["d_L"], C[0] - B[0], delta=0.1)
        self.assertIn("reference hero b.tif", audit.table(doc))

    def test_a_hero_without_a_skin_measure_is_refused(self):
        dets = {p: det([big_face()]) for p in self.paths}
        dets[self.paths[0]] = det([])
        with self.assertRaisesRegex(ValueError, "--hero a.tif: no skin measure \\(no face\\)"):
            audit.build(self.paths, dets, hero=self.paths[0])

    def test_files_not_measured_stay_out_of_the_median(self):
        dets = {p: det([big_face()]) for p in self.paths}
        dets[self.paths[2]] = det([])
        doc = audit.build(self.paths, dets)
        self.assertEqual(doc["reference"]["n"], 2)
        self.assertAlmostEqual(doc["reference"]["L"], (A[0] + B[0]) / 2, delta=0.05)
        self.assertNotIn("checks", doc["files"][2]); self.assertNotIn("d_E00", doc["files"][2])
        self.assertIsNone(audit.build(self.paths, {p: det([]) for p in self.paths})["reference"])


class AuditRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rpstills-audit-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.src = self.tmp / "finals"
        self.src.mkdir()
        save_tif(self.src / "a.tif", portrait(bright_bg=True))
        save_srgb_jpeg(self.src / "b.jpg", portrait())
        (self.src / "notes.txt").write_text("not an image")
        self.calls = []

    def fake_faces(self, paths, landmarks=False):
        self.calls.append((list(paths), landmarks))
        return {p: det([SMALL, big_face()]) for p in paths}

    def run_cli(self, a):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(audit.vision, "faces", side_effect=self.fake_faces), \
                redirect_stdout(out), redirect_stderr(err):
            code = audit.run(a)
        return code, out.getvalue(), err.getvalue()

    def test_end_to_end_writes_audit_json_and_a_table_with_no_verdicts(self):
        code, out, err = self.run_cli(args(self.src, self.tmp / "report", "headshot_4x5"))
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.calls[0][1], "landmarks must be asked for")
        self.assertEqual([os.path.basename(p) for p in self.calls[0][0]], ["a.tif", "b.jpg"])
        doc = json.loads((self.tmp / "report" / "audit.json").read_text())
        self.assertEqual([r["file"] for r in doc["files"]], ["a.tif", "b.jpg"])
        self.assertEqual(doc["profile"], "headshot_4x5")
        self.assertEqual(doc["profile_spec"], {"aspect": [4, 5], "eye_line": 0.33})
        self.assertNotIn("references_deg", doc)
        self.assertEqual(doc["reference"]["source"], "median")
        self.assertEqual(doc["params"]["check"], audit.CHECK)
        self.assertNotIn("d117.7", out); self.assertNotIn("d123", out)
        self.assertEqual(doc["params"]["head_above"], 0.6)
        self.assertEqual(doc["spread"]["face_w"]["n"], 2)
        self.assertIn("spread", out); self.assertIn("working defaults, not verdicts", out)
        for word in ("PASS", "FAIL", "WARN"):
            self.assertNotIn(word, out + json.dumps(doc))
        self.assertFalse(list((self.tmp / "report").glob(".audit-*")), "no temporary file is left behind")

    def test_hero_by_name_or_path_and_a_hero_outside_the_folder(self):
        for hero in ("b.jpg", self.src / "b.jpg"):
            code, out, err = self.run_cli(args(self.src, self.tmp / "report", hero=hero))
            self.assertEqual(code, 0, err)
            doc = json.loads((self.tmp / "report" / "audit.json").read_text())
            self.assertEqual((doc["reference"]["source"], doc["reference"]["file"]), ("hero", "b.jpg"))
        self.calls.clear()
        (self.tmp / "elsewhere.jpg").write_bytes((self.src / "b.jpg").read_bytes())
        for hero in ("missing.jpg", self.tmp / "elsewhere.jpg", "notes.txt"):
            code, _, err = self.run_cli(args(self.src, self.tmp / "report", hero=hero))
            self.assertEqual(code, 1); self.assertIn("is not a JPEG or TIFF in", err)
        self.assertEqual(self.calls, [], "refused before Vision runs")

    def test_hero_named_through_a_symlinked_folder(self):
        # on macOS /tmp and $TMPDIR are symlinks, and a drive or NAS mount may be one: the same file is accepted
        link = self.tmp / "link"
        link.symlink_to(self.src, target_is_directory=True)
        for folder, hero in ((link, self.src / "b.jpg"), (self.src, link / "b.jpg")):
            code, _, err = self.run_cli(args(folder, self.tmp / "report", hero=hero))
            self.assertEqual(code, 0, err)
            doc = json.loads((self.tmp / "report" / "audit.json").read_text())
            self.assertEqual((doc["reference"]["source"], doc["reference"]["file"]), ("hero", "b.jpg"))

    def test_default_out_is_the_audited_folder(self):
        code, _, err = self.run_cli(args(self.src))
        self.assertEqual(code, 0, err)
        self.assertTrue((self.src / "audit.json").exists())

    def test_output_inside_a_git_tree_is_refused_before_vision_runs(self):
        repo = self.tmp / "repo"
        (repo / ".git").mkdir(parents=True)
        code, _, err = self.run_cli(args(self.src, repo / "out"))
        self.assertEqual(code, 1)
        self.assertIn("git working tree", err)
        self.assertEqual(self.calls, [])
        self.assertFalse((repo / "out" / "audit.json").exists())
        with self.assertRaisesRegex(ValueError, "git working tree"):
            audit.write(str(repo / "out"), {"files": []})

    def test_refusals(self):
        code, _, err = self.run_cli(args(self.src, profile="no_such_profile"))
        self.assertEqual(code, 1); self.assertIn("headshot_4x5", err)
        empty = self.tmp / "empty"; empty.mkdir()
        code, _, err = self.run_cli(args(empty))
        self.assertEqual(code, 1); self.assertIn("no JPEG or TIFF", err)
        code, _, err = self.run_cli(args(self.tmp / "missing"))
        self.assertEqual(code, 1); self.assertIn("not a folder", err)
        with mock.patch.object(audit.vision, "faces", return_value=None), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as e:
            self.assertEqual(audit.run(args(self.src)), 1)
        self.assertIn("Vision helper cannot run", e.getvalue())


if __name__ == "__main__":
    unittest.main()
