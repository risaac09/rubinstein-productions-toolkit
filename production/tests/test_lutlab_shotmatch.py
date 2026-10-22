"""
Tests for lutlab shot-match on synthetic pairs: a known CDL recovered, a pair of different scenes refused,
and the .cc and .cube files it writes. No footage. Needs numpy, scipy, OpenCV and Pillow (the rp-tools venv).

Run: ~/.venvs/rp-tools/bin/python -m unittest production.tests.test_lutlab_shotmatch   (from the repo root)
"""

import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import cv2
import numpy as np

PROD = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROD))

from lutlab import cube, shotmatch  # noqa: E402
from lutlab.__main__ import main  # noqa: E402

TRUTH = {"slope": np.array([1.15, 1.0, 0.85]), "offset": np.array([0.02, 0.0, -0.02]),
         "power": np.array([0.9, 1.0, 1.1]), "sat": 1.2}
TOL = 0.01  # recovered CDL, applied to a colour grid: max code-value error
H, W = 270, 480


def scene(seed):
    """Smooth colour field with soft blobs and a few hard-edged boxes, 0.1..0.85 per channel."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W] / np.array([H, W]).reshape(2, 1, 1)
    im = np.stack([0.15 + 0.6 * xx, 0.15 + 0.6 * yy, 0.5 + 0.3 * np.sin(6 * (xx + yy))], -1)
    for _ in range(14):
        cy, cx, r = rng.random(), rng.random(), 0.05 + 0.15 * rng.random()
        im += np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2) / r ** 2))[..., None] * (rng.random(3) - 0.5) * 0.5
    im = np.clip(im, 0.08, 0.86)
    for _ in range(10):
        y, x = rng.integers(0, H - 50), rng.integers(0, W - 70)
        im[y:y + 40, x:x + 60] = 0.1 + 0.75 * rng.random(3)
    return im


def save(path, im):
    cv2.imwrite(str(path), np.round(np.clip(im, 0, 1)[..., ::-1] * 65535).astype(np.uint16))


class TestShotMatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.d = Path(cls._tmp.name)
        src = scene(1)
        cls.src, cls.ref = cls.d / "src.png", cls.d / "ref.png"
        save(cls.src, src)
        save(cls.ref, shotmatch.apply_match(src, TRUTH))
        cls.other = cls.d / "other.png"
        save(cls.other, scene(2))
        cls.m = shotmatch.match(cls.src, cls.ref)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_known_cdl_is_recovered_within_tolerance(self):
        grid = cube.lattice(9).reshape(-1, 3) * 0.7 + 0.1  # the range the scene covers
        got = np.clip(shotmatch.cdl_apply(grid, self.m["cdl"]), 0, 1)
        want = np.clip(shotmatch.cdl_apply(grid, TRUTH), 0, 1)
        self.assertLess(np.abs(got - want).max(), TOL)
        self.assertLess(self.m["errors"]["match"]["held-out"]["median"], 0.3)
        self.assertGreater(self.m["errors"]["before"]["held-out"]["median"], 3.0)

    def test_residual_is_dropped_when_the_cdl_already_explains_the_pair(self):
        self.assertIsNone(self.m["delta"])

    def test_residual_helps_when_the_look_is_not_a_cdl(self):
        src = scene(1)
        ref = shotmatch.apply_match(src, TRUTH)
        ref = np.clip(ref + 0.05 * np.sin(9 * ref[..., ::-1]), 0, 1)  # cross-channel wobble no CDL makes
        save(self.d / "ref2.png", ref)
        m = shotmatch.match(self.src, self.d / "ref2.png")
        self.assertIsNotNone(m["delta"])
        self.assertLess(m["errors"]["match"]["held-out"]["mean"], 0.8 * m["errors"]["cdl"]["held-out"]["mean"])

    def test_different_content_is_refused(self):
        with self.assertRaises(shotmatch.Refused):
            shotmatch.match(self.src, self.other)

    def test_aspect_mismatch_is_refused(self):
        save(self.d / "wide.png", scene(1)[:, :300])
        with self.assertRaises(shotmatch.Refused):
            shotmatch.match(self.src, self.d / "wide.png")

    def test_files_and_cli(self):
        out = self.d / "out"
        with redirect_stdout(StringIO()) as so:
            self.assertEqual(main(["shot-match", "--ref", str(self.ref), "--src", str(self.src),
                                   "--out", str(out), "--name", "t"]), 0)
        self.assertIn("held-out dE2000", so.getvalue())
        back = shotmatch.read_cdl(out / "t.cc")
        for k in ("slope", "offset", "power"):
            np.testing.assert_allclose(back[k], self.m["cdl"][k], atol=1e-5)
        lut = cube.read_cube(out / "t.cube")
        px = np.array([[0.3, 0.4, 0.5], [0.7, 0.2, 0.3]])
        np.testing.assert_allclose(cube.sample(lut, px), shotmatch.apply_match(px, self.m["cdl"]), atol=2e-3)
        self.assertIn("CIEDE2000", (out / "t.report.md").read_text())
        with redirect_stderr(StringIO()) as se:
            self.assertEqual(main(["shot-match", "--ref", str(self.other), "--src", str(self.src),
                                   "--out", str(self.d / "out2")]), 2)
        self.assertIn("refused", se.getvalue())
        self.assertEqual(list((self.d / "out2").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
