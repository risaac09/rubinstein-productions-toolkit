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


def portrait(bright_bg=False, bright_face=False, eye_texture=None):
    """float sRGB: skin ellipse at TARGET Lab on blue, dark eyes and lips."""
    img = np.zeros((H, W, 3), np.float32)
    img[:] = BLUE
    cv2.ellipse(img, (400, 460), (165, 210), 0, 0, 360, tuple(float(c) for c in measure.lab_to_srgb(TARGET)), -1)
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


def save_srgb_jpeg(path, rgb):
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    Image.fromarray((rgb * 255 + 0.5).astype(np.uint8)).save(path, quality=97, subsampling=0, icc_profile=icc)


def det(faces, w=W, h=H):
    return {"width": w, "height": h, "faces": faces, "people": [], "error": None}


def args(src, out=None, profile=None):
    p = argparse.ArgumentParser()
    audit.add_arguments(p)
    return p.parse_args([str(src)] + (["--out", str(out)] if out else []) + (["--profile", profile] if profile else []))


class AuditMeasureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rpstills-audit-"))
        self.addCleanup(shutil.rmtree, self.tmp)

    def row(self, rgb, name="a.tif", faces=None, profile=None, bits=8):
        path = str(self.tmp / name)
        save_tif(path, rgb, bits) if name.endswith(".tif") else save_srgb_jpeg(path, rgb)
        doc = audit.build([path], {path: det([SMALL, big_face()] if faces is None else faces)},
                          "p" if profile else None, profile)
        return doc["files"][0]

    def test_the_largest_face_is_measured_with_its_geometry(self):
        r = self.row(portrait())
        self.assertNotIn("error", r)
        self.assertEqual(r["faces"], 2)
        self.assertAlmostEqual(r["face_w"], 0.4, places=4)                 # the big face, not face 0
        self.assertAlmostEqual(r["headroom"], (300 - 0.6 * 320) / 1000, places=4)
        self.assertAlmostEqual(r["eye_y"], 0.42, places=4)
        self.assertAlmostEqual(r["aspect"], 0.8, places=4)

    def test_skin_numbers_and_reference_distances(self):
        r = self.row(portrait())
        self.assertEqual(r["skin_region"], "cheek")
        for key, want in zip(("skin_L", "skin_a", "skin_b"), TARGET):
            self.assertAlmostEqual(r[key], want, delta=0.3)
        self.assertAlmostEqual(r["vectorscope_deg"], 117.7, delta=0.4)
        self.assertAlmostEqual(r["d_trial_deg"], r["vectorscope_deg"] - 117.7, places=3)
        self.assertAlmostEqual(r["d_skin_line_deg"], r["vectorscope_deg"] - 123.0, places=3)
        self.assertAlmostEqual(r["chroma"], float(np.hypot(r["skin_a"], r["skin_b"])), delta=0.02)

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

    def test_an_unreadable_file_is_a_row_with_an_error(self):
        path = self.tmp / "bad.jpg"
        path.write_bytes(b"not an image")
        r = audit.build([str(path)], {})["files"][0]
        self.assertIn("unreadable", r["error"])

    def test_eye_sharpness_ranks_and_ignores_resolution(self):
        # band-limited detail (lashes, iris), not pixel noise, which no resampling keeps
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

    def test_spread(self):
        s = audit.spread([{"skin_L": 1.0}, {"skin_L": 3.0}, {"skin_L": None}, {}])
        self.assertEqual(s["skin_L"], {"n": 2, "mean": 2.0, "sd": 1.0, "min": 1.0, "max": 3.0, "range": 2.0})
        self.assertNotIn("hue_deg", s)

    def test_wrap_deg(self):
        self.assertAlmostEqual(audit.wrap_deg(359.0 - 1.0), -2.0)
        self.assertAlmostEqual(audit.wrap_deg(-190.0), 170.0)


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
        self.assertEqual(doc["references_deg"], {"trial": 117.7, "skin_line": 123.0})
        self.assertEqual(doc["params"]["head_above"], 0.6)
        self.assertEqual(doc["spread"]["face_w"]["n"], 2)
        self.assertIn("spread", out); self.assertIn("report only", out)
        for word in ("PASS", "FAIL", "WARN"):
            self.assertNotIn(word, out + json.dumps(doc))
        self.assertFalse(list((self.tmp / "report").glob(".audit-*")), "no temporary file is left behind")

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
