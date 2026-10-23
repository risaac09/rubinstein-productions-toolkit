"""
Offline tests for rpstills.inputs (ICC to sRGB, EXIF orientation) and the
crop-only background burn in rpstills.background and rpstills.finish.
ICC profiles are built here as bytes; images are synthetic. No media, no
Vision, no network.

Run: ~/.venvs/rp-tools/bin/python -m unittest production.tests.test_rpstills_inputs
"""

import io
import json
import shutil
import struct
import sys
import tempfile
import tracemalloc
import unittest
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from rpstills import audit, background, finish, inputs, skin  # noqa: E402
from test_rpstills_finish import W, H, face, fp, portrait  # noqa: E402

# D50-adapted RGB colorants (XYZ columns) and tone curve gamma of three common working spaces
SPACES = {
    "Display P3": ((0.51512, 0.24120, -0.00105), (0.29198, 0.69225, 0.04189), (0.15710, 0.06657, 0.78407), 563 / 256),
    "Adobe RGB (1998)": ((0.60974, 0.31111, 0.01947), (0.20528, 0.62567, 0.06087), (0.14919, 0.06322, 0.74457), 563 / 256),
    "ProPhoto RGB": ((0.79767, 0.28804, 0.0), (0.13519, 0.71187, 0.0), (0.03135, 0.0, 0.82521), 461 / 256),
}
SRGB_D50 = np.array([[0.4360747, 0.3850649, 0.1430804], [0.2225045, 0.7168786, 0.0606169],
                     [0.0139322, 0.0971045, 0.7141733]])


def matrix_icc(name):
    """A minimal ICC v2 matrix/TRC RGB profile for SPACES[name]."""
    r, g, b, gamma = SPACES[name]
    desc = name.encode()
    s15 = lambda *v: b"".join(struct.pack(">i", round(x * 65536)) for x in v)  # noqa: E731
    xyz = lambda *v: b"XYZ \0\0\0\0" + s15(*v)  # noqa: E731
    curv = b"curv\0\0\0\0" + struct.pack(">IH", 1, round(gamma * 256))
    tags = [(b"desc", b"desc\0\0\0\0" + struct.pack(">I", len(desc) + 1) + desc + b"\0" * 79),
            (b"wtpt", xyz(0.9642, 1.0, 0.8249)), (b"rXYZ", xyz(*r)), (b"gXYZ", xyz(*g)), (b"bXYZ", xyz(*b)),
            (b"rTRC", curv), (b"gTRC", curv), (b"bTRC", curv)]
    table, data, start = b"", b"", 132 + 12 * len(tags)
    for sig, body in tags:
        body += b"\0" * (-len(body) % 4)
        table, data = table + sig + struct.pack(">II", start + len(data), len(body)), data + body
    header = (struct.pack(">I", start + len(data)) + b"\0" * 4 + struct.pack(">I", 0x02100000) + b"mntrRGB XYZ "
              + b"\0" * 12 + b"acsp" + b"\0" * 28 + s15(0.9642, 1.0, 0.8249) + b"\0" * 48)
    return header + struct.pack(">I", len(tags)) + table + data


def expected_srgb(name, rgb):
    """The sRGB values of encoded rgb (0..1) in SPACES[name], from the colorants and gamma alone."""
    r, g, b, gamma = SPACES[name]
    xyz = np.array([r, g, b]).T @ (np.asarray(rgb, float) ** gamma)
    lin = np.clip(np.linalg.solve(SRGB_D50, xyz), 0, 1)
    return np.where(lin <= 0.0031308, lin * 12.92, 1.055 * lin ** (1 / 2.4) - 0.055)


def save(path, rgb8, icc=None, orientation=None):
    exif = Image.Exif()
    if orientation:
        exif[0x0112] = orientation
    Image.fromarray(rgb8).save(path, exif=exif, icc_profile=icc, **({"quality": 100, "subsampling": 0}
                                                                   if str(path).endswith(".jpg") else {}))


class ColourTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rpstills-inputs-"))
        self.addCleanup(shutil.rmtree, self.tmp)

    def patch(self, colours):
        """A 16x16 file per colour, flat, as uint8 RGB."""
        return [np.full((16, 16, 3), np.round(np.array(c) * 255), np.uint8) for c in colours]

    def test_wide_gamut_profiles_are_converted_to_srgb(self):
        colours = [(0.6, 0.4, 0.3), (0.5, 0.5, 0.5), (0.7, 0.55, 0.45), (0.45, 0.5, 0.4)]  # inside sRGB's gamut
        for name in SPACES:
            for i, (c, img) in enumerate(zip(colours, self.patch(colours))):
                path = str(self.tmp / f"{name[:3]}{i}.jpg")
                save(path, img, icc=matrix_icc(name))
                rgb, info = inputs.read_upright(path)
                want = expected_srgb(name, img[0, 0] / 255.0)
                np.testing.assert_allclose(rgb[8, 8], want, atol=0.012, err_msg=f"{name} {c}")
                self.assertEqual(info["colour"], f"converted from {name} to sRGB")
                self.assertEqual((info["icc"], info["srgb_icc"]), (name, False))
        # a wide gamut colour really moves: P3 (0.6, 0.4, 0.3) is not sRGB (0.6, 0.4, 0.3)
        self.assertGreater(abs(expected_srgb("ProPhoto RGB", [0.6, 0.4, 0.3]) - [0.6, 0.4, 0.3]).max(), 0.05)

    def test_an_srgb_profile_is_left_alone_and_an_untagged_file_is_taken_as_srgb_and_says_so(self):
        img = self.patch([(0.6, 0.4, 0.3)])[0]
        tagged, plain = str(self.tmp / "t.tif"), str(self.tmp / "u.tif")
        save(tagged, img, icc=finish.srgb_icc())
        save(plain, img)
        rgb, info = inputs.read_upright(tagged)
        np.testing.assert_array_equal(rgb[0, 0], img[0, 0] / np.float32(255))
        self.assertEqual((info["colour"], info["srgb_icc"]), ("sRGB profile", True))
        rgb, info = inputs.read_upright(plain)
        np.testing.assert_array_equal(rgb[0, 0], img[0, 0] / np.float32(255))
        self.assertEqual((info["colour"], info["icc"], info["srgb_icc"]), ("no profile, taken as sRGB", None, False))

    def test_a_16_bit_source_with_a_wide_profile_is_converted_at_8_bits_and_an_srgb_one_is_kept(self):
        wide = np.full((4, 4, 3), np.round(np.array([0.6, 0.4, 0.3]) * 65535), np.uint16)
        out, colour, name, is_srgb = inputs.to_srgb8(wide, matrix_icc("Display P3"))
        self.assertEqual((out.dtype, name, is_srgb), (np.uint8, "Display P3", False))
        np.testing.assert_allclose(out[0, 0] / 255, expected_srgb("Display P3", [0.6, 0.4, 0.3]), atol=0.012)
        kept, colour, _, is_srgb = inputs.to_srgb8(wide, finish.srgb_icc())
        self.assertIs(kept, wide)
        self.assertTrue(is_srgb)

    def test_a_broken_profile_is_a_row_error_not_a_crash(self):
        path = str(self.tmp / "bad.jpg")
        save(path, self.patch([(0.5, 0.5, 0.5)])[0], icc=b"not a profile")
        with self.assertRaisesRegex(ValueError, "ICC profile"):
            inputs.read_upright(path)

    def test_audit_measures_the_converted_pixels_and_each_row_says_what_was_done(self):
        img = np.round(portrait() * 255).astype(np.uint8)
        files = {"p3.jpg": matrix_icc("Display P3"), "srgb.jpg": finish.srgb_icc(), "none.jpg": None}
        dets = {}
        for name, icc in files.items():
            save(str(self.tmp / name), img, icc=icc)
            dets[str(self.tmp / name)] = {"width": W, "height": H, "faces": [face()]}
        rows = {r["file"]: r for r in audit.build([str(self.tmp / n) for n in files], dets)["files"]}
        self.assertEqual(rows["p3.jpg"]["colour"], "converted from Display P3 to sRGB")
        self.assertEqual(rows["srgb.jpg"]["colour"], "sRGB profile")
        self.assertEqual(rows["none.jpg"]["colour"], "no profile, taken as sRGB")
        self.assertEqual(rows["none.jpg"]["skin_L"], rows["srgb.jpg"]["skin_L"])   # taken as sRGB: unchanged
        self.assertGreater(abs(rows["p3.jpg"]["skin_L"] - rows["none.jpg"]["skin_L"]), 0.5)  # P3 values move

    def test_finish_converts_before_it_crops_and_the_row_says_so(self):
        folder = self.tmp / "src"
        folder.mkdir()
        save(str(folder / "P1.jpg"), np.round(portrait() * 255).astype(np.uint8), icc=matrix_icc("Display P3"))
        row = finish.finish_frame("P1", str(folder / "P1.jpg"), {"width": W, "height": H, "faces": [face()]},
                                  finish.solo_profiles(json.loads((HERE.parent / "stills-config.json").read_text()),
                                                       finish.DEFAULT_CROPS),
                                  str(self.tmp / "out"), False, False, finish.srgb_icc())
        self.assertNotIn("error", row)
        self.assertEqual((row["colour"], row["icc"], row["orientation"]), ("converted from Display P3 to sRGB",
                                                                           "Display P3", 1))


class OrientationTest(unittest.TestCase):
    def test_all_eight_tags_are_applied_to_jpeg_and_tiff_alike_and_reported(self):
        # 8x8 blocks of flat colour, so JPEG's loss stays inside a few levels; asymmetric, no tag maps it to itself
        rng = np.random.default_rng(3)
        img = np.kron(rng.integers(40, 200, (2, 3, 3), dtype=np.uint8), np.ones((8, 8, 1), np.uint8))
        with tempfile.TemporaryDirectory(prefix="rpstills-orient-") as d:
            for ext in ("tif", "jpg"):
                for o in range(1, 9):
                    path = str(Path(d) / f"o{o}.{ext}")
                    save(path, img, orientation=o)
                    rgb, info = inputs.read_upright(path)
                    want = inputs.upright(img, o)
                    got = np.round(rgb * 255)
                    self.assertLess(np.abs(got - want).max(), 1 if ext == "tif" else 4, f"{ext} tag {o}")
                    self.assertEqual(rgb.shape[:2], (24, 16) if o >= 5 else (16, 24))
                    self.assertEqual((info["orientation"], info["orientation_applied"]), (o, o != 1))
                    self.assertTrue(rgb.flags["C_CONTIGUOUS"])

    def test_the_tags_mean_what_the_exif_standard_says(self):
        img = np.zeros((4, 6, 3), np.uint8)
        img[0, 0] = 255                                                    # the stored top-left corner
        corner = {1: (0, 0), 2: (0, 5), 3: (3, 5), 4: (3, 0), 5: (0, 0), 6: (0, 3), 7: (5, 3), 8: (5, 0)}
        for o, (y, x) in corner.items():
            out = inputs.upright(img, o)
            self.assertEqual(tuple(np.argwhere(out[..., 0] == 255)[0]), (y, x), f"tag {o}")

    def test_a_missing_or_impossible_tag_is_upright(self):
        with tempfile.TemporaryDirectory(prefix="rpstills-orient-") as d:
            img = np.zeros((4, 6, 3), np.uint8)
            for name, o in (("none.tif", None), ("nine.tif", 9)):
                save(str(Path(d) / name), img, orientation=o)
                rgb, info = inputs.read_upright(str(Path(d) / name))
                self.assertEqual((rgb.shape[:2], info["orientation"], info["orientation_applied"]), ((4, 6), 1, False))

    def test_finish_finishes_a_turned_frame_when_vision_reports_the_upright_size(self):
        with tempfile.TemporaryDirectory(prefix="rpstills-orient-") as d:
            turned = np.rot90(np.round(portrait() * 255).astype(np.uint8), 1).copy()   # stored on its side
            save(str(Path(d) / "R.jpg"), turned, icc=finish.srgb_icc(), orientation=6)  # tag 6 turns it back clockwise
            profiles = finish.solo_profiles(json.loads((HERE.parent / "stills-config.json").read_text()),
                                            finish.DEFAULT_CROPS)
            rows = [finish.finish_frame("R", str(Path(d) / "R.jpg"), det, profiles, str(Path(d) / "out"), False, False,
                                        finish.srgb_icc()) for det in
                    ({"width": W, "height": H, "faces": [face()]}, {"width": H, "height": W, "faces": [face()]})]
            self.assertNotIn("error", rows[0])
            self.assertEqual((rows[0]["width"], rows[0]["height"], rows[0]["orientation"],
                              rows[0]["orientation_applied"]), (turned.shape[0], turned.shape[1], 6, True))
            self.assertIn("upright file", rows[1]["error"])    # Vision saw another size: still refused


class RegionBurnTest(unittest.TestCase):
    TOL = 1e-4      # of full scale: the regional burn is the same arithmetic on the same mask

    def setUp(self):
        self.img = portrait()
        self.face = fp()
        self.mask, _ = background.subject_mask(self.img, self.face)
        self.burned, _ = background.burn(self.img, self.mask)

    def test_the_windowed_mask_equals_the_full_mask_inside_the_window(self):
        for region in ((100, 200, 700, 900), (0, 0, 400, 500), (300, 700, 800, 1000)):
            x0, y0, x1, y1 = region
            m, _ = background.subject_mask(self.img, self.face, region)
            self.assertEqual(m.shape, (y1 - y0, x1 - x0))
            keep = background.feather_margin(self.face)
            inner = (slice(min(keep, (y1 - y0) // 2), (y1 - y0) - min(keep, (y1 - y0) // 2)),
                     slice(min(keep, (x1 - x0) // 2), (x1 - x0) - min(keep, (x1 - x0) // 2)))
            ref = self.mask[y0:y1, x0:x1]
            np.testing.assert_allclose(m[inner], ref[inner], atol=self.TOL, err_msg=str(region))

    def test_finish_burns_only_the_crop_region_and_the_crop_equals_the_full_frame_burn(self):
        # the portrait pasted into a 2400x3000 frame: its face is a seventh of the width, so the crop and its
        # margin are a share of the frame
        w, h, ox, oy = 2400, 3000, 800, 1000
        big = cv2.resize(self.img, (w, h), interpolation=cv2.INTER_LINEAR)
        big[oy:oy + H, ox:ox + W] = self.img
        f0 = face()
        faces = [dict(f0, x=(f0["x"] * W + ox) / w, y=(f0["y"] * H + oy) / h, w=f0["w"] * W / w, h=f0["h"] * H / h,
                      landmarks={k: [[(x * W + ox) / w, (y * H + oy) / h] for x, y in v]
                                 for k, v in f0["landmarks"].items()})]
        fpx = skin.to_pixels(faces[0], w, h)
        profiles = finish.solo_profiles(json.loads((HERE.parent / "stills-config.json").read_text()),
                                        finish.DEFAULT_CROPS)
        boxes = [finish.crop_geometry(w, h, faces[0], fpx, p)[1] for p in profiles.values()]
        region = finish.burn_region(boxes, fpx, w, h)
        x0, y0, x1, y1 = region
        self.assertLess((x1 - x0) * (y1 - y0), 0.5 * w * h)          # the frame is bigger than the work
        d = tempfile.mkdtemp(prefix="rpstills-burn-")
        self.addCleanup(shutil.rmtree, d)
        path = str(Path(d) / "B.tif")
        save(path, np.round(big * 255).astype(np.uint8))
        row = finish.finish_frame("B", path, {"width": w, "height": h, "faces": faces}, profiles, d, False, True,
                                  finish.srgb_icc())
        self.assertNotIn("error", row)
        self.assertEqual(row["background"]["region"], list(region))
        self.assertTrue(row["background"]["applied"])
        got = np.asarray(Image.open(row["crops"]["headshot_4x5_eye36"]["file"]).convert("RGB"), np.float32) / 255
        src = inputs.read_upright(path)[0]
        full, _ = background.burn(src, background.subject_mask(src, fpx)[0])
        want, _ = finish.crop_frame(full, faces[0], fpx, faces, "x", profiles["headshot_4x5_eye36"])
        regional = src.copy()
        m, _ = background.subject_mask(src, fpx, region)
        regional[y0:y1, x0:x1], _ = background.burn(src[y0:y1, x0:x1], m)
        cx, cy, cw, ch = boxes[0]
        self.assertLess(np.abs(regional[cy:cy + ch, cx:cx + cw] - full[cy:cy + ch, cx:cx + cw]).max(), self.TOL)
        # the written JPEG is the regional crop (mean error of JPEG q93 on a noisy synthetic frame), and the
        # full-frame crop's pixels are the same to a level of 8 bits
        tile, _ = finish.crop_frame(regional, faces[0], fpx, faces, "x", profiles["headshot_4x5_eye36"])
        self.assertLess(np.abs(got - tile / np.float32(255)).mean(), 0.01)
        self.assertLessEqual(np.abs(tile.astype(int) - want).max(), 1)
        outside = np.ones((h, w), bool)
        outside[y0:y1, x0:x1] = False
        np.testing.assert_array_equal(regional[outside], src[outside])

    def test_the_window_costs_a_share_of_the_memory_of_the_whole_frame(self):
        # 2000x2500 float32 frame (60 MB); a 700x900 window. tracemalloc counts numpy and OpenCV arrays alike
        img = cv2.resize(portrait(), (2000, 2500), interpolation=cv2.INTER_LINEAR)
        f = {k: v * 2.5 for k, v in self.face.items() if isinstance(v, float)}
        region = (600, 700, 1300, 1600)
        x0, y0, x1, y1 = region

        def peak(fn):
            tracemalloc.start()
            try:
                fn()
                return tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()

        def whole():
            m, _ = background.subject_mask(img, f)
            background.burn(img, m)

        def window():
            m, _ = background.subject_mask(img, f, region)
            background.burn(img[y0:y1, x0:x1], m)

        full, part = peak(whole), peak(window)
        self.assertGreater(full, 100e6)                    # sanity: the whole-frame path really is heavy
        self.assertLess(part, 0.5 * full, (part, full))    # the window is 13 % of the frame; 50 % is a loose bound


class RefineTest(unittest.TestCase):
    def strands(self):
        """(hard mask, luminance guide, strand pixels, bright ground near them): a dark disc subject on a bright
        ground, and thin dark strands (2 px) running out of it that the hard mask stops short of."""
        n = 240
        guide = np.full((n, n), 0.9, np.float32)
        yy, xx = np.mgrid[0:n, 0:n]
        disc = (yy - 120) ** 2 + (xx - 120) ** 2 < 60 ** 2
        guide[disc] = 0.15
        strand = np.zeros((n, n), bool)
        for x in range(30, 240, 14):
            strand[185:215, x:x + 2] = True                  # hanging down below the disc's bottom at y 180
        strand &= ~disc
        guide[strand] = 0.15
        hard = disc.astype(np.float32)
        near = cv2.dilate(strand.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool) & ~strand & ~disc
        return hard, guide, strand, near

    def test_the_refined_mask_follows_thin_strands_the_blurred_hard_mask_misses(self):
        hard, guide, strand, near = self.strands()
        blurred = cv2.GaussianBlur(hard, (0, 0), 2.0)
        refined = np.clip(background.guided_filter(guide, hard, 20), 0, 1)
        # metric: mean mask on the strand pixels minus mean mask on the bright ground within 4 px of them
        sep = lambda m: float(m[strand].mean() - m[near].mean())  # noqa: E731
        self.assertGreater(sep(refined), 0.10)
        self.assertGreater(sep(refined), sep(blurred) + 0.10)
        # and it leaves the subject and the far ground where they were
        self.assertGreater(refined[120, 120], 0.97)
        self.assertLess(refined[10, 10], 0.03)

    def test_subject_mask_is_refined_by_default_and_can_be_left_hard(self):
        img, f = portrait(), fp()
        soft, info = background.subject_mask(img, f)
        hard, hinfo = background.subject_mask(img, f, refine=False)
        self.assertTrue(info["refined"]); self.assertNotIn("refined", hinfo)
        self.assertEqual(info["share"], hinfo["share"])        # the guard still looks at the hard mask
        self.assertGreater(float(np.abs(soft - hard).max()), 0.0)
        self.assertEqual(background.params()["guide"], "w/12, eps 0.001")


if __name__ == "__main__":
    unittest.main()
