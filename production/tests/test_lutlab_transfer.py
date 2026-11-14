"""
Tests for lutlab transfer (reference still to LUT) on synthetic stills: a known Lab transform recovered by
each stage, one planted failure per guard, a determinism check, and the files it writes. No footage.
Needs numpy, scipy, OpenCV and Pillow (the rp-tools venv).

Run: ~/.venvs/rp-tools/bin/python -m unittest production.tests.test_lutlab_transfer   (from the repo root)
"""

import sys
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import cv2
import numpy as np

PROD = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROD))

from lutlab import cube, transfer  # noqa: E402
from lutlab.__main__ import main  # noqa: E402

H, W = 200, 320


def scene(seed=1, amp=1.0):
    """Smooth colour field, soft blobs and hard boxes, mid-range so a mild transform stays in gamut."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W] / np.array([H, W]).reshape(2, 1, 1)
    im = np.stack([0.3 + 0.35 * xx, 0.3 + 0.35 * yy, 0.5 + 0.2 * np.sin(6 * (xx + yy))], -1)
    for _ in range(12):
        cy, cx, r = rng.random(), rng.random(), 0.05 + 0.15 * rng.random()
        im += np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2) / r ** 2))[..., None] * (rng.random(3) - 0.5) * 0.4
    for _ in range(8):
        y, x = rng.integers(0, H - 40), rng.integers(0, W - 60)
        im[y:y + 30, x:x + 50] = 0.25 + 0.5 * rng.random(3)
    return np.clip(0.5 + amp * (im - 0.5), 0.05, 0.95)


def save(path, im):
    cv2.imwrite(str(path), np.round(np.clip(im, 0, 1)[..., ::-1] * 65535).astype(np.uint16))


def lab_graded(im, a, b):
    lab = transfer.to_lab(im.reshape(-1, 3), "srgb")
    return transfer.from_lab(lab @ np.asarray(a).T + b, "srgb").reshape(im.shape)


def de(x, y):
    from rpstills.measure import delta_e2000
    return delta_e2000(transfer.to_lab(x, "srgb"), transfer.to_lab(y, "srgb"))


class TestTransfer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.d = Path(cls._tmp.name)
        cls.im = scene(1)
        cls.src = cls.d / "src.png"
        save(cls.src, cls.im)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def ref_of(self, name, im):
        p = self.d / name
        save(p, im)
        return p

    # --- recovering a known transform -----------------------------------------------------------

    def test_round_trip_lab(self):
        c = np.random.default_rng(0).random((500, 3)) * 0.8 + 0.1
        np.testing.assert_allclose(transfer.from_lab(transfer.to_lab(c, "srgb"), "srgb"), c, atol=1e-6)

    def test_reinhard_recovers_per_axis_scale(self):
        truth = np.array([1.1, 1.3, 0.8])
        ref = self.ref_of("r1.png", lab_graded(self.im, np.diag(truth), [3, 4, -5]))
        res = transfer.transfer(self.src, ref, stages=("reinhard",), size=17)
        np.testing.assert_allclose(res["chain"][0]["scale"], truth, atol=0.03)
        px = self.im.reshape(-1, 3)
        want = np.asarray(cv2.imread(str(ref), cv2.IMREAD_UNCHANGED)[..., 2::-1], dtype=float).reshape(-1, 3) / 65535
        got = transfer.apply_chain(px, res["chain"], "srgb")
        self.assertLess(np.median(de(got, want)), 0.3)

    def test_mkl_recovers_a_coupled_transform(self):
        a = np.array([[1.1, 0.0, 0.0], [0.0, 1.25, 0.12], [0.0, 0.12, 1.15]])  # symmetric positive definite
        ref = self.ref_of("r2.png", lab_graded(self.im, a, [3, 4, -5]))
        res = transfer.transfer(self.src, ref, stages=("mkl",), size=17)
        np.testing.assert_allclose(res["chain"][0]["matrix"], a, atol=0.04)
        want = np.asarray(cv2.imread(str(ref), cv2.IMREAD_UNCHANGED)[..., 2::-1], dtype=float).reshape(-1, 3) / 65535
        got = transfer.apply_chain(self.im.reshape(-1, 3), res["chain"], "srgb")
        self.assertLess(np.median(de(got, want)), 0.3)

    def test_ot_closes_a_nonlinear_gap_the_gaussians_leave(self):
        lab = transfer.to_lab(self.im.reshape(-1, 3), "srgb")
        lab[:, 0] = 100 * (lab[:, 0] / 100) ** 0.7  # a tone curve: not a Gaussian map
        ref = self.ref_of("r3.png", transfer.from_lab(lab, "srgb").reshape(self.im.shape))
        res = transfer.transfer(self.src, ref, size=17)
        rows = {r["stage"]: r for r in res["rows"]}
        self.assertTrue(rows["ot"]["applied"])
        self.assertLess(rows["ot"]["held_out"]["sliced_w2"], 0.7 * rows["mkl"]["held_out"]["sliced_w2"])
        self.assertLess(rows["mkl"]["held_out"]["sliced_w2"], rows["none"]["held_out"]["sliced_w2"])

    def test_identity_reference_is_near_identity(self):
        res = transfer.transfer(self.src, self.src, size=17)
        self.assertLess(res["lattice_move"]["mean"], 1.0)

    def test_table_is_bounded_and_ramp_monotone(self):
        a = np.array([[1.2, 0, 0], [0, 1.6, 0.2], [0, 0.2, 1.5]])
        ref = self.ref_of("r4.png", lab_graded(self.im, a, [8, 10, -12]))
        res = transfer.transfer(self.src, ref, size=33)
        self.assertTrue(np.isfinite(res["table"]).all())
        self.assertGreaterEqual(res["table"].min(), 0)
        self.assertLessEqual(res["table"].max(), 1)
        diag = res["table"][np.arange(33), np.arange(33), np.arange(33)]
        self.assertGreaterEqual(np.diff(diag, axis=0).min(), -transfer.RAMP_TOL)

    def test_deterministic(self):
        ref = self.ref_of("r5.png", lab_graded(self.im, np.diag([1.1, 1.2, 0.9]), [2, 3, -4]))
        outs = [cube.cube_text(transfer.transfer(self.src, ref, size=17)["table"]) for _ in range(2)]
        self.assertEqual(outs[0], outs[1])

    # --- one planted failure per guard ----------------------------------------------------------

    def test_refuses_a_tiny_still(self):
        small = self.d / "small.png"
        save(small, self.im[:40, :40])
        with self.assertRaisesRegex(transfer.Refused, "too small"):
            transfer.transfer(small, self.src)

    def test_refuses_a_flat_still(self):
        flat = self.d / "flat.png"
        save(flat, np.full((H, W, 3), 0.5) + np.random.default_rng(0).normal(0, 0.0005, (H, W, 3)))
        with self.assertRaisesRegex(transfer.Refused, "flat"):
            transfer.transfer(self.src, flat)
        with self.assertRaisesRegex(transfer.Refused, "flat"):
            transfer.transfer(flat, self.src)

    def test_refuses_a_source_with_no_colour(self):
        g = self.d / "grey.png"
        y = self.im @ np.array([0.2126, 0.7152, 0.0722])
        save(g, np.repeat(y[..., None], 3, -1))
        with self.assertRaisesRegex(transfer.Refused, "no colour"):
            transfer.transfer(g, self.src)

    def test_refuses_values_that_are_not_numbers(self):
        arr = self.im.copy()
        arr[10, 10, 0] = np.nan  # cv2 will not round-trip a NaN through a file, so plant it at the reader
        with mock.patch.object(transfer.pairs, "read_image", return_value=arr):
            with self.assertRaisesRegex(transfer.Refused, "not numbers"):
                transfer.transfer(self.src, self.src)

    def test_a_stage_that_stretches_too_far_is_skipped(self):
        low = self.ref_of("low.png", scene(1, amp=0.3))  # the source has under a fifth of the reference's spread
        high = self.ref_of("high.png", scene(1, amp=1.6))
        with self.assertRaisesRegex(transfer.Refused, "stretches a Lab axis"):
            transfer.transfer(low, high, stages=("reinhard",))

    def test_a_stage_that_moves_colours_too_far_is_skipped(self):
        ref = self.ref_of("far.png", lab_graded(self.im, np.eye(3), [0, 70, 0]))
        with self.assertRaisesRegex(transfer.Refused, "Lab units at the 99th percentile"):
            transfer.transfer(self.src, ref, stages=("reinhard",))

    def test_a_steppy_ramp_skips_every_stage(self):
        ref = self.ref_of("r6.png", lab_graded(self.im, np.diag([1.3, 1.3, 1.3]), [0, 0, 0]))
        with self.assertRaisesRegex(transfer.Refused, "step of slope"):
            transfer.transfer(self.src, ref, max_slope=0.5)

    def test_ramp_check_names_a_non_monotone_ramp(self):
        self.assertIn("not monotone", transfer.ramp_check(lambda c: c + 0.05 * np.sin(40 * c)))
        self.assertEqual(transfer.ramp_check(lambda c: c ** 1.2), "")

    @staticmethod
    def shift_stage(da, gain=1.0, scale=1.0):
        return {"stage": "reinhard", "mu_s": np.zeros(3), "mu_r": np.array([0.0, da, 0.0]),
                "scale": np.full(3, scale), "gain": gain}

    def test_a_stage_that_makes_nan_is_skipped(self):
        nan = self.shift_stage(0, scale=np.nan)  # what a zero-spread axis in both stills would give
        with mock.patch.object(transfer, "fit_reinhard", return_value=nan):
            with self.assertRaisesRegex(transfer.Refused, "not numbers"):
                transfer.transfer(self.src, self.src, stages=("reinhard",))

    def test_chain_move_is_bounded_across_stages(self):
        with mock.patch.object(transfer, "MAX_MOVE", 30.0), \
                mock.patch.object(transfer, "fit_mkl", return_value=self.shift_stage(20)):
            res = transfer.transfer(self.src, self.src, stages=("mkl", "mkl"), size=9)
        first, second = res["rows"][1], res["rows"][2]
        self.assertTrue(first["applied"])  # 20 alone is within 30
        self.assertFalse(second["applied"])  # 20 + 20 is not
        self.assertIn("chain would move", second["reason"])

    def test_unknown_stage_or_space(self):
        with self.assertRaises(ValueError):
            transfer.transfer(self.src, self.src, stages=("sinkhorn",))
        with self.assertRaises(ValueError):
            transfer.transfer(self.src, self.src, space="nope")

    # --- files ----------------------------------------------------------------------------------

    def test_cli_writes_cube_and_report_and_refuses_overwrite(self):
        ref = self.ref_of("r7.png", lab_graded(self.im, np.diag([1.1, 1.2, 0.9]), [2, 3, -4]))
        out = self.d / "out"
        args = ["transfer", "--ref", str(ref), "--src", str(self.src), "--size", "17", "--out", str(out), "--name", "t"]
        with redirect_stdout(StringIO()):
            self.assertEqual(main(args), 0)
        lut = cube.read_cube(out / "t.cube")
        self.assertEqual(lut["table"].shape, (17, 17, 17, 3))
        report = (out / "t.report.md").read_text()
        for word in ("reinhard", "mkl", "ot", "sliced W2", "applies nothing"):
            self.assertIn(word, report)
        with self.assertRaises(FileExistsError), redirect_stdout(StringIO()):
            main(args)

    def test_a_grey_image_reads_as_three_equal_channels(self):
        from lutlab import pairs
        grey = self.d / "grey.png"
        cv2.imwrite(str(grey), np.tile(np.arange(40, dtype=np.uint8) * 6, (30, 1)))
        a = pairs.read_image(grey)
        self.assertEqual(a.shape, (30, 40, 3))
        self.assertTrue((a[..., 0] == a[..., 2]).all())


    def test_cli_refusal_writes_nothing_and_exits_2(self):
        out = self.d / "out2"
        small = self.d / "small.png"
        save(small, self.im[:40, :40])
        err = StringIO()
        with redirect_stderr(err), redirect_stdout(StringIO()):
            rc = main(["transfer", "--ref", str(self.src), "--src", str(small), "--out", str(out), "--name", "t"])
        self.assertEqual(rc, 2)
        self.assertIn("refused", err.getvalue())
        self.assertFalse((out / "t.cube").exists())

    def test_cli_bad_stage_is_a_plain_refusal(self):
        err = StringIO()
        with redirect_stderr(err), redirect_stdout(StringIO()):
            rc = main(["transfer", "--ref", str(self.src), "--src", str(self.src), "--stages", "sinkhorn",
                       "--out", str(self.d / "out3"), "--name", "t"])
        self.assertEqual(rc, 2)

    def test_cli_does_not_even_make_a_folder_in_resolves_tree(self):
        target = self.d / "Blackmagic Design" / "new"
        with self.assertRaises(PermissionError), redirect_stdout(StringIO()):
            main(["transfer", "--ref", str(self.src), "--src", str(self.src), "--out", str(target), "--name", "t"])
        self.assertFalse(target.exists())

    def test_never_writes_into_resolves_folders(self):
        res = transfer.transfer(self.src, self.src, size=17)
        target = self.d / "Blackmagic Design" / "LUT"
        target.mkdir(parents=True)
        with self.assertRaises(PermissionError):
            transfer.write(res, target, "t", self.src, self.src)


if __name__ == "__main__":
    unittest.main()
