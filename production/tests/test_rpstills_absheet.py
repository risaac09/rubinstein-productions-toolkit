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
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageCms, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rpstills import absheet  # noqa: E402

COLOURS = [(220, 40, 40), (40, 200, 60), (40, 70, 220), (230, 200, 40), (200, 50, 200),
           (40, 200, 210), (240, 140, 30), (120, 120, 120), (250, 250, 250)]
PHONE_W = 390  # CSS px a phone gives an image shown at the chat's full width


def wide_icc(desc=b"Test Wide RGB"):
    """A minimal ICC v2 matrix/TRC RGB profile: Adobe RGB (1998) primaries, D50-adapted, gamma 563/256."""
    s15 = lambda *v: b"".join(struct.pack(">i", round(x * 65536)) for x in v)  # noqa: E731
    xyz = lambda *v: b"XYZ \0\0\0\0" + s15(*v)  # noqa: E731
    curv = b"curv\0\0\0\0" + struct.pack(">IH", 1, 563)
    tags = [(b"desc", b"desc\0\0\0\0" + struct.pack(">I", len(desc) + 1) + desc + b"\0" * 79),
            (b"wtpt", xyz(0.9642, 1.0, 0.8249)), (b"rXYZ", xyz(0.60974, 0.31111, 0.01947)),
            (b"gXYZ", xyz(0.20528, 0.62567, 0.06087)), (b"bXYZ", xyz(0.14919, 0.06322, 0.74457)),
            (b"rTRC", curv), (b"gTRC", curv), (b"bTRC", curv)]
    table, data, start = b"", b"", 132 + 12 * len(tags)
    for sig, body in tags:
        body += b"\0" * (-len(body) % 4)
        table, data = table + sig + struct.pack(">II", start + len(data), len(body)), data + body
    header = (struct.pack(">I", start + len(data)) + b"\0" * 4 + struct.pack(">I", 0x02100000) + b"mntrRGB XYZ "
              + b"\0" * 12 + b"acsp" + b"\0" * 28 + s15(0.9642, 1.0, 0.8249) + b"\0" * 48)
    return header + struct.pack(">I", len(tags)) + table + data


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

    def test_drawn_capitals_stay_about_11_px_tall_at_phone_width(self):
        draw = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        for aspect in (0.5, 0.5625, 2 / 3, 0.8, 1.0, 1.5, 16 / 9, 2.0):
            for n in range(2, 10):
                geo = absheet.layout([(int(1000 * aspect), 1000)] * n)
                label_px = max(absheet.LABEL_MIN_PX, int(absheet.LABEL_SHARE * min(geo["cell"])))
                f = absheet.font(label_px)
                ink = min(draw.textbbox((0, 0), c, font=f, stroke_width=max(1, label_px // 24))[3]
                          - draw.textbbox((0, 0), c, font=f, stroke_width=max(1, label_px // 24))[1]
                          for c in absheet.LETTERS[:n])
                self.assertGreaterEqual(ink * min(1.0, PHONE_W / geo["sheet"][0]), 10.5, (aspect, n, geo))

    def test_a_picture_tagged_with_another_profile_is_shown_in_srgb(self):
        skin = Image.new("RGB", (400, 500), (196, 140, 115))
        srgb = ImageCms.createProfile("sRGB")
        tagged = self.tmp / "srgb.png"
        skin.save(tagged, icc_profile=ImageCms.ImageCmsProfile(srgb).tobytes())
        icc = wide_icc()
        wide = ImageCms.profileToProfile(skin, srgb, ImageCms.ImageCmsProfile(io.BytesIO(icc)), outputMode="RGB")
        self.assertGreater(abs(wide.getpixel((0, 0))[0] - 196), 8)      # other numbers, the same colour
        other = self.tmp / "wide.png"
        wide.save(other, icc_profile=icc)
        doc = absheet.build([str(tagged), str(other)], self.tmp / "out", seed=4)
        with Image.open(self.tmp / "out" / "sheet.jpg") as im:
            sheet = np.asarray(im.convert("RGB"), int)
        cw, ch = doc["sheet"]["cell"]
        for k in range(2):
            r, c = divmod(k, doc["sheet"]["grid"][0])
            x0, y0 = absheet.GAP + c * (cw + absheet.GAP), absheet.GAP + r * (ch + absheet.GAP)
            np.testing.assert_allclose(sheet[y0 + ch // 2, x0 + cw // 2], (196, 140, 115), atol=4)

    def test_an_input_that_is_the_sheet_it_would_write_is_refused(self):
        out = self.tmp / "round1"
        absheet.build(self.solids(2), out, seed=1)
        before = (out / "sheet.jpg").read_bytes()
        key = (out / "key.json").read_text()
        with self.assertRaisesRegex(ValueError, "would be overwritten"):
            absheet.build([str(out / "sheet.jpg"), *self.solids(3)[2:]], out, seed=2)
        self.assertEqual((out / "sheet.jpg").read_bytes(), before)
        self.assertEqual((out / "key.json").read_text(), key)

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
