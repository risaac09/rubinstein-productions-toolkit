"""
Offline tests for lutlab: colour maths, .cube and Hald I/O, the look model,
decompose and build round trips on synthetic LUTs, and the output guard.
No licensed LUTs, no images. stdlib unittest; numpy/scipy/OpenCV from macOS's
/usr/bin/python3.

Run: /usr/bin/python3 production/tests/test_lutlab.py
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lutlab import cube, fit, guard, model, pairs, pipeline, spaces  # noqa: E402


def synthetic_params(in_space="r709", mode="per_channel"):
    p = model.blank(in_space, "r709")
    p["tone"] = {"black": 0.02, "white": 0.97, "mid": 0.42, "contrast": 0.16, "toe_start": -2.0,
                 "toe_strength": 1.5, "shoulder_start": 1.5, "shoulder_strength": 3.0, "mode": mode}
    for b in model.BANDS:
        p["hue_shift"][b]["060 orange"] = 6.0
        p["sat_gain"][b]["240 azure"] = 0.8
    p["sat_gain"]["shadows"] = {h: 0.85 for h in model.HUES}
    p["hue_lum"]["120 yellow"] = -3.0
    p["split_tone"]["L=0.25"] = {"hue": 220.0, "amount": 1.5}
    return p


class ColourMathTest(unittest.TestCase):
    def test_delta_e2000_reference(self):
        # Sharma, Wu and Dalal (2005), test pair 1
        self.assertAlmostEqual(float(spaces.delta_e2000(np.array([50, 2.6772, -79.7751]),
                                                        np.array([50, 0, -82.7485]))), 2.0425, places=4)

    def test_encodings_round_trip(self):
        x = np.array([0.0005, 0.02, 0.18, 0.9, 4.0])  # not 0.01: published V-Log is not continuous at its cut
        for name in spaces.SPACES:
            dec, enc, _, _ = spaces.SPACES[name]
            np.testing.assert_allclose(dec(enc(x)), x, rtol=1e-6, atol=1e-9, err_msg=name)

    def test_vlog_mid_grey(self):
        self.assertAlmostEqual(float(spaces.vlog_encode(np.array(0.18))), 0.4233, places=3)

    def test_gamut_and_oklab_round_trip(self):
        rgb = np.random.default_rng(1).uniform(0, 1, (50, 3))
        for name in spaces.SPACES:
            np.testing.assert_allclose(spaces.to_lin709(spaces.from_lin709(rgb, name), name), rgb, atol=1e-7)
        np.testing.assert_allclose(spaces.oklab_to_lin709(spaces.oklab(rgb)), rgb, atol=1e-9)
        np.testing.assert_allclose(spaces.oklab(np.ones(3)), [1, 0, 0], atol=1e-4)


class CubeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_read_write_domain_and_sample(self):
        t = cube.lattice(5) ** 2
        p = cube.write_cube(str(self.tmp / "a.cube"), t, "x", (0, 0, 0), (2, 2, 2))
        lut = cube.read_cube(p)
        np.testing.assert_allclose(lut["table"], t, atol=1e-6)
        np.testing.assert_allclose(lut["domain_max"], [2, 2, 2])
        # node at input (1, 0, 2) is lattice index (2, 0, 4)
        np.testing.assert_allclose(cube.sample(lut, np.array([1.0, 0.0, 2.0])), t[2, 0, 4], atol=1e-6)

    def test_input_range_and_red_fastest(self):
        rows = ["LUT_3D_SIZE 2", "LUT_3D_INPUT_RANGE 0 4"]
        rows += [f"{r} {g} {b}" for b in (0, 1) for g in (0, 1) for r in (0, 1)]
        (self.tmp / "b.cube").write_text("\n".join(rows) + "\n")
        lut = cube.read_cube(str(self.tmp / "b.cube"))
        np.testing.assert_allclose(lut["table"][1, 0, 0], [1, 0, 0])
        np.testing.assert_allclose(cube.sample(lut, np.array([2.0, 4.0, 0.0])), [0.5, 1, 0])

    def test_1d_refused(self):
        (self.tmp / "c.cube").write_text("LUT_1D_SIZE 2\n0 0 0\n1 1 1\n")
        with self.assertRaises(ValueError):
            cube.read_cube(str(self.tmp / "c.cube"))

    def test_refuses_overwrite(self):
        p = str(self.tmp / "d.cube")
        cube.write_cube(p, cube.lattice(2))
        with self.assertRaises(FileExistsError):
            cube.write_cube(p, cube.lattice(2))

    def test_hald_identity(self):
        import cv2
        p = cube.write_hald(str(self.tmp / "h.png"), lambda rgb: rgb, level=4)
        im = cv2.imread(p, cv2.IMREAD_UNCHANGED)[..., ::-1] / 65535
        self.assertEqual(im.shape, (64, 64, 3))
        flat = im.reshape(-1, 3)
        n = 16
        np.testing.assert_allclose(flat[1], [1 / (n - 1), 0, 0], atol=1e-4)      # red fastest
        np.testing.assert_allclose(flat[n], [0, 1 / (n - 1), 0], atol=1e-4)
        np.testing.assert_allclose(flat[n * n], [0, 0, 1 / (n - 1)], atol=1e-4)


class ModelTest(unittest.TestCase):
    def test_blank_colour_is_identity(self):
        p = model.blank()
        lab = spaces.oklab(np.random.default_rng(2).uniform(0.05, 1, (100, 3)))
        np.testing.assert_allclose(model.colour(p, lab), lab, atol=1e-12)

    def test_hue_weights_partition_of_unity(self):
        w = model.hue_weights(np.radians(np.linspace(0, 359, 97)))
        np.testing.assert_allclose(w.sum(-1), 1, atol=1e-12)
        np.testing.assert_allclose(model.hue_weights(np.radians(np.array([60.0]))), [np.eye(12)[2]], atol=1e-12)

    def test_tone_curve_shape(self):
        t = synthetic_params()["tone"]
        e = np.linspace(-12, 12, 2001)
        y = model.tone_curve(e, t)
        self.assertTrue(np.all(np.diff(y) > 0))
        self.assertAlmostEqual(float(model.tone_curve(np.array(0.0), t)), t["mid"])
        self.assertTrue(t["black"] < y.min() and y.max() < t["white"])
        d = np.gradient(y, e)
        self.assertLess(abs(d[np.argmin(abs(e - t["shoulder_start"]))] - t["contrast"]), 0.005)  # C1 at the join


class ReportTest(unittest.TestCase):
    def test_identity_reads_as_no_change(self):
        from lutlab import report
        for space in ("r709", "srgb", "vlog"):
            out = "r709"
            ident = lambda c, s=space: spaces.from_lin709(spaces.to_lin709(c, s), out)  # a pure colour-space transform
            rows, _, _ = report.patch_table(ident, space, out)
            for r in rows:
                self.assertAlmostEqual(r["L_after"], r["L_before"], places=4, msg=(space, r["patch"]))
                self.assertAlmostEqual(r["density"], 0, places=4)
            f = report.skin_family(ident, space, out)
            self.assertEqual((f["hue_shift_deg"], f["lightness_change"]), (0.0, 0.0))


class DecomposeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def decompose(self, p, size=33):
        src = cube.write_cube(str(self.tmp / "src.cube"), np.clip(model.evaluate(p, cube.lattice(size)), 0, 1))
        out = self.tmp / "out"
        out.mkdir()
        return src, out, pipeline.decompose(src, p["in"], "r709", out, "synthetic")

    def test_recovers_parameters(self):
        p = synthetic_params()
        _, out, q = self.decompose(p)
        self.assertEqual(q["tone"]["mode"], "per_channel")
        self.assertAlmostEqual(q["tone"]["mid"], 0.42, delta=0.005)
        self.assertAlmostEqual(q["tone"]["contrast"], 0.16, delta=0.005)
        self.assertAlmostEqual(q["hue_shift"]["mids"]["060 orange"], 6.0, delta=0.6)
        self.assertAlmostEqual(q["sat_gain"]["shadows"]["150 green"], 0.85, delta=0.03)
        self.assertAlmostEqual(q["hue_lum"]["120 yellow"], -3.0, delta=0.6)
        self.assertAlmostEqual(q["split_tone"]["L=0.25"]["amount"], 1.5, delta=0.3)
        self.assertLess(q["fit_error"]["natural colours"]["full"]["median"], 0.3)
        self.assertLess(q["source"]["rebuild_max_abs_error"], 1e-6)
        for f in ("report.md", "tone.png", "hue_shift.png", "patches.png", "synthetic.residual.cube"):
            self.assertTrue((out / f).exists(), f)

    def test_finds_luminance_mode(self):
        _, _, q = self.decompose(synthetic_params("vlog", "luminance"))
        self.assertEqual(q["tone"]["mode"], "luminance")

    def test_build_exact_and_edit(self):
        src, out, _ = self.decompose(synthetic_params())
        a = cube.read_cube(src)
        pipeline.build(out / "params.json", out / "same.cube", size=33)
        np.testing.assert_allclose(cube.read_cube(str(out / "same.cube"))["table"], a["table"], atol=2e-6)
        # one variable: a softer shoulder changes highlights, leaves shadows alone
        p = json.loads((out / "params.json").read_text())
        p["tone"]["shoulder_strength"] = 0.3
        (out / "soft.json").write_text(json.dumps(p))
        pipeline.build(out / "soft.json", out / "soft.cube", size=33)
        b = cube.read_cube(str(out / "soft.cube"))["table"]
        self.assertLess(np.abs(b - a["table"])[:8, :8, :8].max(), 1e-6)
        self.assertGreater(np.abs(b - a["table"])[-1, -1, -1].max(), 0.01)
        with self.assertRaises(FileExistsError):
            pipeline.build(out / "params.json", out / "same.cube", size=33)

    def test_refuses_to_replace_params(self):
        src, out, _ = self.decompose(synthetic_params())
        with self.assertRaises(FileExistsError):
            pipeline.decompose(src, "r709", "r709", out, "synthetic")
        pipeline.decompose(src, "r709", "r709", out, "synthetic", overwrite=True)

    def test_bad_tone_edit_refused(self):
        _, out, _ = self.decompose(synthetic_params())
        p = json.loads((out / "params.json").read_text())
        p["tone"]["mid"] = p["tone"]["black"] - 0.01
        (out / "bad.json").write_text(json.dumps(p))
        with self.assertRaises(ValueError):
            pipeline.build(out / "bad.json", out / "bad.cube")

    def test_build_retargets_input_space(self):
        _, out, q = self.decompose(synthetic_params())
        f = pipeline.make_fn(pipeline.load(out / "params.json"), "srgb", residual=False)
        x = np.array([[0.5, 0.4, 0.3]])
        same = model.evaluate(q, spaces.from_lin709(spaces.to_lin709(x, "srgb"), "r709"))
        np.testing.assert_allclose(f(x), np.clip(same, 0, 1), atol=1e-9)


class PairsTest(unittest.TestCase):
    def test_bins_average(self):
        src = np.array([[0.1, 0.1, 0.1]] * 3 + [[0.9, 0.9, 0.9]] * 3)
        dst = src * 0.5
        xs, ys, cnt = pairs.bin_samples(src, dst, src, bins=4)
        np.testing.assert_allclose(sorted(cnt), [3, 3])
        np.testing.assert_allclose(ys, xs * 0.5)

    def test_fit_from_binned_pixels(self):
        p = synthetic_params("lin709")
        rng = np.random.default_rng(3)
        lab = np.stack([rng.uniform(0.3, 0.9, 20000), rng.normal(0, 0.06, 20000), rng.normal(0, 0.06, 20000)], -1)
        lin = np.maximum(spaces.oklab_to_lin709(lab), 1e-4)
        lin = np.concatenate([lin, np.repeat(np.geomspace(0.005, 1.5, 300)[:, None], 3, 1)])
        tgt = model.evaluate(p, lin)
        xs, ys, cnt = pairs.bin_samples(lin, tgt, spaces.di_encode(lin) / 0.8)
        q, _ = pairs._fit_binned(xs, ys, cnt, "lin709", "r709")
        self.assertLess(fit.errors(q, lin, np.clip(tgt, 0, 1))["full"]["median"], 0.5)


class GuardTest(unittest.TestCase):
    def test_refuses_git_tree(self):
        with self.assertRaises(PermissionError):
            guard.check_out_dir(Path(__file__).resolve().parent / "lutlab-out")
        self.assertFalse((Path(__file__).resolve().parent / "lutlab-out").exists())

    def test_refuses_icloud(self):
        with self.assertRaises(PermissionError):
            guard.check_out_dir(Path.home() / "Documents" / "lutlab-test-never-created")

    def test_allows_temp(self):
        d = guard.check_out_dir(Path(tempfile.mkdtemp()) / "x")
        self.assertTrue(d.is_dir())


if __name__ == "__main__":
    unittest.main()
