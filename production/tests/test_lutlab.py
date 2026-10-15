"""
Offline tests for lutlab: colour maths, .cube and Hald I/O, the look model,
decompose and build round trips on synthetic LUTs, and the output guard.
No licensed LUTs, no images. stdlib unittest; numpy/scipy/OpenCV from macOS's
/usr/bin/python3.

Run: /usr/bin/python3 production/tests/test_lutlab.py
"""

import json
import shutil
import subprocess
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


class CameraSpacesTest(unittest.TestCase):
    def test_published_mid_grey(self):
        # 18% grey code values from the vendors' documents
        for name, want in (("logc3", 0.3910), ("logc4", 0.2784), ("bmdfilm5", 0.3836), ("vlog", 0.4233)):
            enc = spaces.SPACES[name][1]
            self.assertAlmostEqual(float(enc(np.array(0.18))), want, places=3, msg=name)

    def test_continuous_at_the_cut(self):
        for enc, cut in ((spaces.bmdfilm5_encode, 0.005), (spaces.logc4_encode, spaces._C4T)):
            a, b = enc(np.array([cut - 1e-9, cut]))
            self.assertAlmostEqual(float(a), float(b), places=6)

    def test_cameras(self):
        self.assertEqual(spaces.camera("Pyxis"), ("bmdfilm5", "Pyxis"))
        self.assertEqual(spaces.camera("s5ii")[0], "vlog")
        with self.assertRaises(ValueError):
            spaces.camera("fx3")


class DatTest(unittest.TestCase):
    def test_resolve_dat_is_blue_fastest(self):
        tmp = Path(tempfile.mkdtemp())
        rows = ["3DLUTSIZE 2", ""] + [f"{r} {g} {b}" for r in (0, 1) for g in (0, 1) for b in (0, 1)]
        (tmp / "x.dat").write_text("\n".join(rows) + "\n")
        lut = cube.read_cube(str(tmp / "x.dat"))
        np.testing.assert_allclose(lut["table"][1, 0, 0], [1, 0, 0])
        np.testing.assert_allclose(lut["table"][0, 0, 1], [0, 0, 1])


def look_lut(in_space, size=33):
    """A synthetic camera LUT: the synthetic look, read from in_space."""
    p = synthetic_params()
    X = cube.lattice(size)
    return {"table": np.clip(model.evaluate(p, X, in_space), 0, 1), "domain_min": np.zeros(3),
            "domain_max": np.ones(3), "title": "synthetic"}


class PortTest(unittest.TestCase):
    def test_port_to_own_space_is_identity(self):
        from lutlab import port
        lut = look_lut("vlog")
        t, share = port.port_table(lut, "vlog", "vlog", 33)
        np.testing.assert_allclose(t, lut["table"], atol=1e-9)
        self.assertEqual(share, 0.0)

    def test_port_equals_composition(self):
        from lutlab import port
        lut = look_lut("vlog", 65)
        t, _ = port.port_table(lut, "vlog", "bmdfilm5", 65)
        ported = {"table": t, "domain_min": np.zeros(3), "domain_max": np.ones(3)}
        x = pipeline.natural_samples(ported, "bmdfilm5")
        want = cube.sample(lut, spaces.from_lin709(spaces.to_lin709(x, "bmdfilm5"), "vlog"))
        d = np.abs(cube.sample(ported, x) - want).max(-1)
        # re-sampling onto a new lattice softens hard clips a little; the bulk is exact
        self.assertLess(np.median(d), 0.003)
        self.assertLess(np.percentile(d, 99), 0.05)

    def test_round_trip_and_diff(self):
        from lutlab import port
        lut = look_lut("vlog", 65)
        there, _ = port.port_table(lut, "vlog", "bmdfilm5", 65)
        back, _ = port.port_table({"table": there, "domain_min": np.zeros(3), "domain_max": np.ones(3)},
                                  "bmdfilm5", "vlog", 65)
        d = port.diff(lut, {"table": back, "domain_min": np.zeros(3), "domain_max": np.ones(3)}, "vlog")
        self.assertLess(d["all"]["median"], 0.3)
        self.assertEqual(port.diff(lut, lut, "vlog")["all"]["max"], 0.0)

    def test_exposure_offset(self):
        from lutlab import port
        lut = look_lut("vlog")
        t0, _ = port.port_table(lut, "vlog", "vlog", 33, 0.0)
        t1, _ = port.port_table(lut, "vlog", "vlog", 33, 1.0)
        self.assertGreater(t1[16, 16, 16].mean(), t0[16, 16, 16].mean())


class DeltaTest(unittest.TestCase):
    def test_empty_delta_and_zero_white_balance_are_identity(self):
        p = synthetic_params("vlog")
        x = np.random.default_rng(5).uniform(0.1, 0.8, (200, 3))
        q = model.apply_delta(p, {"lutlab_delta": 1, "white_balance": {"temp": 0, "tint": 0}})
        np.testing.assert_allclose(model.evaluate(q, x), model.evaluate(p, x), atol=1e-12)
        np.testing.assert_allclose(model.white_balance_matrix(0, 0), np.eye(3))

    def test_warm_is_warm(self):
        g = model.white_balance_matrix(30, 0) @ np.full(3, 0.18)
        self.assertGreater(g[0], g[2])
        g = model.white_balance_matrix(-30, 0) @ np.full(3, 0.18)
        self.assertLess(g[0], g[2])

    def test_delta_arithmetic(self):
        p = synthetic_params()
        d = {"tone": {"contrast": 1.1, "mid": 0.01}, "sat_gain": {"all": 0.5, "mids": {"060 orange": 2.0}},
             "hue_shift": {"shadows": 3}, "split_tone": {"L=0.25": {"hue": 220.0, "amount": 1.5}}}
        q = model.apply_delta(p, d)
        self.assertAlmostEqual(q["tone"]["contrast"], p["tone"]["contrast"] * 1.1)
        self.assertAlmostEqual(q["tone"]["mid"], p["tone"]["mid"] + 0.01)
        self.assertAlmostEqual(q["sat_gain"]["mids"]["060 orange"], p["sat_gain"]["mids"]["060 orange"] * 2.0)
        self.assertAlmostEqual(q["sat_gain"]["shadows"]["030 red"], p["sat_gain"]["shadows"]["030 red"] * 0.5)
        self.assertAlmostEqual(q["hue_shift"]["shadows"]["060 orange"], p["hue_shift"]["shadows"]["060 orange"] + 3)
        self.assertAlmostEqual(q["split_tone"]["L=0.25"]["amount"], 3.0, places=3)  # same hue: amounts add

    def test_bad_delta_refused(self):
        p = synthetic_params()
        for d in ({"sat": 1}, {"tone": {"gamma": 2}}, {"tone": {"mode": "lum"}}, {"sat_gain": {"lows": 1}},
                  {"hue_shift": {"all": {"999 x": 1}}}, {"hue_lum": {"orange": 2}}, {"sat_rolloff": {"C=9": 1}}):
            with self.assertRaises(ValueError, msg=d):
                model.apply_delta(p, d)

    def test_shipped_looks_load_and_bake(self):
        looks = Path(__file__).resolve().parent.parent / "lutlab-looks"
        tmp = Path(tempfile.mkdtemp())
        base = tmp / "base"
        base.mkdir()
        (base / "params.json").write_text(json.dumps(synthetic_params("logc3")))
        files = sorted(looks.glob("*.json"))
        self.assertGreaterEqual(len(files), 6)
        out = pipeline.looks(base / "params.json", [str(f) for f in files], ["pyxis", "s5ii"], tmp, size=9)
        self.assertEqual(len(out), 2 * len(files))
        for f in out:
            self.assertTrue(np.isfinite(cube.read_cube(f)["table"]).all(), f)


class RcmTest(unittest.TestCase):
    """The colour-managed set: DaVinci Intermediate in and out, so Resolve's output transform finishes the job."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        base = self.tmp / "base"
        base.mkdir()
        (base / "params.json").write_text(json.dumps(synthetic_params("logc3")))
        self.base = base / "params.json"
        self.rng = np.random.default_rng(7)

    def test_target(self):
        self.assertEqual(spaces.target("RCM"), ("di", "RCM", "di"))
        self.assertEqual(spaces.target("s5ii"), ("vlog", "S5II", None))
        with self.assertRaises(ValueError):
            spaces.camera("rcm")  # port --camera rcm would map a Rec.709 LUT twice

    def test_output_transform_undoes_the_container(self):
        # what Resolve does after the node, tone and gamut mapping off: DI -> linear -> Rec.709 primaries -> gamma 2.4
        p = pipeline.load(self.base)
        cam = pipeline.make_fn(p, "vlog")
        rcm = pipeline.make_fn(p, "di", out_encoding="di")
        lin = np.exp(self.rng.uniform(np.log(0.002), np.log(4.0), (400, 3)))
        lin = np.stack([lin[:, 0], lin[:, 0] * self.rng.uniform(0.5, 1.5, 400), lin[:, 1]], -1)  # colours, not only greys
        want = np.clip(cam(spaces.from_lin709(lin, "vlog")), 0, 1)
        seen_by_resolve = spaces.from_lin709(spaces.to_lin709(rcm(spaces.from_lin709(lin, "di")), "di"), "r709")
        np.testing.assert_allclose(seen_by_resolve, want, atol=1e-6)

    def test_baked_set_matches_the_camera_set(self):
        d = self.tmp / "delta.json"
        d.write_text(json.dumps({"name": "warm", "white_balance": {"temp": 20}}))
        out = pipeline.looks(self.base, [str(d)], ["s5ii", "rcm"], self.tmp, size=65)
        self.assertEqual([Path(f).name for f in out], ["warm [S5II].cube", "warm [RCM].cube"])
        cam, rcm = (cube.read_cube(f) for f in out)
        lin = np.exp(self.rng.uniform(np.log(0.01), np.log(2.0), (500, 3)))
        want = cube.sample(cam, spaces.from_lin709(lin, "vlog"))
        got = spaces.from_lin709(spaces.to_lin709(cube.sample(rcm, spaces.from_lin709(lin, "di")), "di"), "r709")
        de = spaces.delta_e2000(spaces.cielab(spaces.to_lin709(np.clip(want, 0, 1), "r709")),
                                spaces.cielab(spaces.to_lin709(np.clip(got, 0, 1), "r709")))
        self.assertLess(float(np.median(de)), 0.5)

    def test_display_codes_stay_in_range(self):
        rcm = pipeline.make_fn(pipeline.load(self.base), "di", out_encoding="di")
        out = rcm(cube.lattice(17))
        white = spaces.from_lin709(np.ones(3), "di")  # display white in DI
        self.assertTrue((out <= white.max() + 1e-6).all())
        self.assertTrue((out >= spaces.from_lin709(np.zeros(3), "di").min() - 1e-6).all())

    def test_build_camera_rcm_matches_looks(self):
        a, b = self.tmp / "a.cube", self.tmp / "b.cube"
        pipeline.build(self.base, str(a), 9, "di", out_encoding="di")
        from lutlab.__main__ import main
        main(["build", str(self.base), "--cube", str(b), "--size", "9", "--camera", "rcm"])
        np.testing.assert_allclose(cube.read_cube(str(a))["table"], cube.read_cube(str(b))["table"], atol=1e-9)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
class VerifyRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from lutlab import apply
        cls.tmp = Path(tempfile.mkdtemp())
        cls.src = cls.tmp / "src.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=24:duration=1",
                        "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv444p", "-color_primaries", "bt709",
                        "-colorspace", "bt709", str(cls.src)], check=True)
        cls.lut = cls.tmp / "look.cube"
        cube.write_cube(str(cls.lut), look_lut("vlog", 33)["table"], "look")
        cls.other = cls.tmp / "other.cube"
        cube.write_cube(str(cls.other), cube.lattice(33), "identity")
        cls.render = cls.tmp / "render.mp4"
        apply.video(str(cls.lut), str(cls.src), str(cls.render), crf=0)

    def test_render_matches_its_own_lut(self):
        from lutlab import verify
        r = verify.verify(str(self.src), str(self.render), str(self.lut), 0.5)
        self.assertLess(r["dE2000"]["median"], 1.0)

    def test_wrong_lut_is_far(self):
        from lutlab import verify
        right = verify.verify(str(self.src), str(self.render), str(self.lut), 0.5)
        wrong = verify.verify(str(self.src), str(self.render), str(self.other), 0.5)
        self.assertGreater(wrong["dE2000"]["median"], max(3.0, 5 * right["dE2000"]["median"]))

    def test_grab_writes_16bit_pngs_and_rotates(self):
        import cv2
        from lutlab import verify
        out, out_r = self.tmp / "grab", self.tmp / "grab-r"
        out.mkdir()
        out_r.mkdir()
        a = verify.grab(str(self.src), [0.5], out, width=640)[0]
        b = verify.grab(str(self.src), [0.5], out_r, width=640, rotate=90)[0]
        ia, ib = cv2.imread(a, cv2.IMREAD_UNCHANGED), cv2.imread(b, cv2.IMREAD_UNCHANGED)
        self.assertEqual((ia.dtype, ia.shape), (np.dtype("uint16"), (360, 640, 3)))
        self.assertEqual(ib.shape, (640, 360, 3))
        np.testing.assert_array_equal(ib, np.rot90(ia, -1))  # clockwise
        with self.assertRaises(FileExistsError):
            verify.grab(str(self.src), [0.5], out, width=640)

    def test_bad_margin_refused(self):
        from lutlab import verify
        with self.assertRaises(ValueError):
            verify.verify(str(self.src), str(self.render), str(self.lut), 0.5, margin=0.5)

    def test_content_box_skips_bars(self):
        from lutlab import verify
        x0, x1, y0, y1 = verify.content_box((1000, 1000), 1920, 1080, 0.1)
        self.assertEqual((y0, y1), (108, 972))
        self.assertGreater(x0, (1920 - 1080) // 2)


class LibraryTest(unittest.TestCase):
    def test_scan_guess_and_copies(self):
        from lutlab import library
        root = Path(tempfile.mkdtemp())
        (root / "Panasonic").mkdir()
        lut = cube.cube_text(cube.lattice(2))
        (root / "Panasonic" / "V-Log to V-709.cube").write_text(lut)
        (root / "Look.CUBE").write_text(lut)
        (root / "Look.CUBE 2").write_text(lut)
        (root / "GH5 Cine-D to VLog.cube").write_text(cube.cube_text(cube.lattice(3)))
        (root / "shaper.cube").write_text("LUT_1D_SIZE 2\n0 0 0\n1 1 1\n")
        (root / "notes.txt").write_text("x")
        e = {x["rel"]: x for x in library.scan([str(root)])}
        self.assertEqual(set(e), {"Panasonic/V-Log to V-709.cube", "Look.CUBE", "Look.CUBE 2", "GH5 Cine-D to VLog.cube"})
        self.assertEqual(e["Panasonic/V-Log to V-709.cube"]["space"], "vlog")
        self.assertEqual(e["GH5 Cine-D to VLog.cube"]["guess"], "unknown")  # the input is Cine-D, not V-Log
        self.assertEqual(len(library.duplicate_groups(list(e.values()))), 1)  # three identical files, one group
        o = library.scan([str(root)], {e["Look.CUBE"]["sha256"]: {"space": "r709", "note": "creative"}})
        self.assertTrue(all(x["space"] == "r709" for x in o if x["name"].startswith("Look")))


if __name__ == "__main__":
    unittest.main()
