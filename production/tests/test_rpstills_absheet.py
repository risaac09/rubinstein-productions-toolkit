"""
Offline tests for rpstills.absheet: the A/B/C decision sheet. Synthetic
images written here (solid colours, noise, a 16-bit TIFF, an EXIF-rotated
JPEG). No real media, no network.

Run: ~/.venvs/rp-tools/bin/python -m unittest production.tests.test_rpstills_absheet
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

import cv2
import numpy as np
from PIL import Image, ImageCms

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rpstills import absheet  # noqa: E402

COLOURS = [(220, 40, 40), (40, 200, 60), (40, 70, 220), (230, 200, 40), (200, 50, 200),
           (40, 200, 210), (240, 140, 30), (120, 120, 120), (250, 250, 250)]
PHONE_W = 390  # CSS px a phone gives an image shown at the chat's full width


class AbsheetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rpstills-absheet-"))
        self.addCleanup(shutil.rmtree, self.tmp)

    def solids(self, n, size=(400, 500)):
        paths = []
        for i in range(n):
            p = self.tmp / f"look{i}.png"
            Image.new("RGB", size, COLOURS[i]).save(p)
            paths.append(str(p))
        return paths

    def test_order_is_seeded_and_never_the_order_given(self):
        for n in range(2, 10):
            for seed in range(200):
                perm = absheet.order(n, seed)
                self.assertEqual(sorted(perm), list(range(n)))
                self.assertNotEqual(perm, list(range(n)), (n, seed))
        self.assertEqual(absheet.order(6, 42), absheet.order(6, 42))
        self.assertGreater(len({tuple(absheet.order(6, s)) for s in range(20)}), 10)

    def test_every_letter_sits_on_its_own_image_and_the_key_says_which(self):
        for n in (2, 3, 5, 9):
            out = self.tmp / f"out{n}"
            paths = self.solids(n)
            doc = absheet.build(paths, out, seed=7)
            key = json.loads((out / "key.json").read_text())
            self.assertEqual(key, json.loads(json.dumps(doc)))
            self.assertEqual(list(key["labels"]), list(absheet.LETTERS[:n]))
            self.assertEqual(sorted(v["input_index"] for v in key["labels"].values()), list(range(n)))
            self.assertNotEqual([v["input_index"] for v in key["labels"].values()], list(range(n)))
            with Image.open(out / "sheet.jpg") as im:
                sheet = np.asarray(im.convert("RGB"), int)
            cols, rows = key["sheet"]["grid"]
            cw, ch = key["sheet"]["cell"]
            title_h = 0
            for k, (letter, v) in enumerate(key["labels"].items()):
                r, c = divmod(k, cols)
                x0 = absheet.GAP + c * (cw + absheet.GAP)
                y0 = title_h + absheet.GAP + r * (ch + absheet.GAP)
                centre = sheet[y0 + ch // 2, x0 + cw // 2]
                np.testing.assert_allclose(centre, COLOURS[v["input_index"]], atol=12, err_msg=f"{n} {letter}")
                self.assertEqual(Path(v["source"]).name, f"look{v['input_index']}.png")
                self.assertEqual(v["size"], [400, 500])

    def test_letters_are_drawn_white_on_a_dark_badge(self):
        images = [Image.new("RGB", (400, 500), (250, 250, 250)) for _ in range(4)]
        sheet, geo = absheet.render(images)
        a = np.asarray(sheet, int)
        for x, y, w, h in geo["badges"]:
            badge = a[y:y + h, x:x + w]
            self.assertGreater((badge.max(2) < 40).mean(), 0.3)       # dark badge, even on a white picture
            self.assertGreater((badge.min(2) > 215).mean(), 0.05)     # white letter strokes inside it
        self.assertGreaterEqual(geo["label_px"], absheet.LABEL_SHARE * min(geo["cell"]) - 1)

    def test_size_caps_hold_for_large_noisy_images(self):
        rng = np.random.default_rng(1)
        paths = []
        for i in range(9):
            p = self.tmp / f"noise{i}.png"
            Image.fromarray(rng.integers(0, 256, (1500, 1200, 3), dtype=np.uint8)).save(p)
            paths.append(str(p))
        doc = absheet.build(paths, self.tmp / "out", seed=3, title="Noise test")
        path = self.tmp / "out" / "sheet.jpg"
        self.assertLess(path.stat().st_size, absheet.MAX_BYTES)
        self.assertEqual(doc["sheet"]["bytes"], path.stat().st_size)
        with Image.open(path) as im:
            self.assertLessEqual(max(im.size), absheet.MAX_EDGE)
            self.assertEqual(list(im.size), doc["sheet"]["size"])
            icc = ImageCms.ImageCmsProfile(io.BytesIO(im.info["icc_profile"]))
            self.assertIn("sRGB", ImageCms.getProfileDescription(icc))
        self.assertLess(doc["sheet"]["quality"], absheet.QUALITIES[0])   # had to step down
        self.assertEqual(doc["title"], "Noise test")

    def test_letters_stay_legible_at_phone_width(self):
        for aspect in (0.8, 1.0, 1.5, 2 / 3):
            for n in range(2, 10):
                geo = absheet.layout([(int(1000 * aspect), 1000)] * n)
                label_px = max(absheet.LABEL_MIN_PX, int(absheet.LABEL_SHARE * min(geo["cell"])))
                on_phone = label_px * min(1.0, PHONE_W / geo["sheet"][0])
                self.assertGreaterEqual(on_phone, 14, (aspect, n, geo))
                self.assertLessEqual(max(geo["sheet"]), absheet.MAX_EDGE)

    def test_tiff16_and_exif_rotation_load(self):
        tif = self.tmp / "deep.tif"
        cv2.imwrite(str(tif), np.full((300, 200, 3), 30000, np.uint16))
        jpg = self.tmp / "rotated.jpg"
        exif = Image.Exif()
        exif[0x0112] = 6
        Image.new("RGB", (300, 200), (10, 200, 10)).save(jpg, exif=exif)
        self.assertEqual(absheet.load(str(jpg)).size, (200, 300))
        deep = absheet.load(str(tif))
        self.assertEqual(deep.size, (200, 300))
        self.assertAlmostEqual(np.asarray(deep).mean(), 30000 / 65535 * 255, delta=2)
        doc = absheet.build([str(tif), str(jpg)], self.tmp / "out", seed=1)
        self.assertEqual({v["size"][0] for v in doc["labels"].values()}, {200})

    def test_wrong_counts_unreadable_files_and_git_trees_are_refused(self):
        p = argparse.ArgumentParser()
        absheet.add_arguments(p)
        bad = self.tmp / "bad.jpg"
        bad.write_bytes(b"not an image")
        repo = self.tmp / "repo"
        (repo / ".git").mkdir(parents=True)
        cases = [(self.solids(1), self.tmp / "o1", "1 images"), (self.solids(9) + self.solids(1), self.tmp / "o2", "10"),
                 (self.solids(2) + [str(bad)], self.tmp / "o3", "bad.jpg"),
                 (self.solids(2), repo / "sheet", "git working tree")]
        for paths, out, msg in cases:
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                self.assertEqual(absheet.run(p.parse_args([*paths, "--out", str(out)])), 1)
            self.assertIn(msg, err.getvalue())
            self.assertFalse((Path(out) / "sheet.jpg").exists())

    def test_summary_names_the_folder_when_file_names_repeat(self):
        paths = []
        for i, look in enumerate(("natural", "warm")):
            (self.tmp / look).mkdir()
            paths.append(str(self.tmp / look / "F1.jpg"))
            Image.new("RGB", (40, 50), COLOURS[i]).save(paths[-1])
        text = absheet.summary(absheet.build(paths, self.tmp / "o", seed=2))
        self.assertIn("natural/F1.jpg", text)
        self.assertIn("warm/F1.jpg", text)

    def test_cli_records_a_random_seed_and_prints_the_summary(self):
        p = argparse.ArgumentParser()
        absheet.add_arguments(p)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(absheet.run(p.parse_args([*self.solids(3), "--out", str(self.tmp / "o")])), 0)
        key = json.loads((self.tmp / "o" / "key.json").read_text())
        self.assertIsInstance(key["seed"], int)
        self.assertIn(f"seed {key['seed']}", out.getvalue())
        self.assertIn("A=look", out.getvalue())
        self.assertFalse([n for n in os.listdir(self.tmp / "o") if n.startswith(".")])   # no temp files left


if __name__ == "__main__":
    unittest.main()
