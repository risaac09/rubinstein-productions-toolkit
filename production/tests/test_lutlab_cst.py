"""
Offline tests for lutlab cst, lutstats and pack: colour space transform LUTs, their Delta E 2000 against
the exact transform and through a round trip, and the zip for Resolve (iPad too). Synthetic only: every
LUT is generated here, no footage. stdlib unittest; numpy (and scipy for the comparisons with port and
pipeline) from the ~/.venvs/rp-tools venv.

Run: ~/.venvs/rp-tools/bin/python -m unittest production.tests.test_lutlab_cst   (from the repo root)
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import numpy as np

PROD = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROD))

from lutlab import cst, cube, lutstats, model, pack, spaces  # noqa: E402
from lutlab.__main__ import main  # noqa: E402


def unit(table):
    return {"table": table, "domain_min": np.zeros(3), "domain_max": np.ones(3), "title": ""}


class CstTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_vlog_grey_lands_on_di_grey(self):
        # V-Log 18% grey (0.4233, Panasonic) is DaVinci Intermediate 0.3360 (Blackmagic): exact and through the cube
        self.assertAlmostEqual(float(cst.transform(np.full(3, 0.4233), "vlog", "di")[0]), 0.3361, delta=2e-4)
        g = lutstats.tetra(unit(cst.table("vlog", "di", 33)), np.full(3, 0.4233))
        self.assertAlmostEqual(float(g[0]), 0.3361, delta=1e-3)
        self.assertAlmostEqual(float(g[0]), float(g[2]), places=12)  # neutral stays neutral (Linux BLAS differs by an ulp)

    def test_equals_port_of_an_identity_at_the_nodes(self):
        from lutlab import port  # port pulls in scipy; only the test needs it
        identity = unit(cube.lattice(9))
        for src, dst in (("vlog", "di"), ("bmdfilm5", "r709"), ("logc3", "srgb"), ("logc4", "di"), ("di", "r709"),
                         ("lin709", "di")):
            want, _ = port.port_table(identity, dst, src, 9)
            np.testing.assert_allclose(cst.table(src, dst, 9), want, atol=1e-12, err_msg=f"{src}->{dst}")

    def test_display_target_clips_above_white(self):
        over = spaces.from_lin709(np.full(3, 2.0), "vlog")  # one stop over display white
        np.testing.assert_allclose(cst.transform(over, "vlog", "r709"), 1.0)
        grey = spaces.from_lin709(np.full(3, 0.18), "vlog")
        self.assertAlmostEqual(float(cst.transform(grey, "vlog", "r709")[0]), 0.18 ** (1 / 2.4), places=9)
        self.assertAlmostEqual(float(cst.transform(grey, "vlog", "srgb")[0]), float(spaces.srgb_encode(0.18)), places=9)

    def test_cli_writes_once(self):
        self.assertEqual(main(["cst", "--from", "vlog", "--to", "r709", "--out", str(self.tmp)]), 0)
        f = self.tmp / "vlog-to-r709-65.cube"
        lut = cube.read_cube(str(f))
        self.assertEqual(lut["table"].shape, (65, 65, 65, 3))
        np.testing.assert_allclose(lut["table"], cst.table("vlog", "r709", 65), atol=1e-6)
        self.assertIn("vlog to r709", lut["title"])
        with self.assertRaises(FileExistsError):
            main(["cst", "--from", "vlog", "--to", "r709", "--out", str(self.tmp)])
        main(["cst", "--from", "vlog", "--to", "r709", "--out", str(self.tmp), "--overwrite"])

    def test_size_33_still_works(self):
        self.assertEqual(main(["cst", "--from", "vlog", "--to", "di", "--size", "33", "--out", str(self.tmp)]), 0)
        self.assertEqual(cube.read_cube(str(self.tmp / "vlog-to-di-33.cube"))["table"].shape, (33, 33, 33, 3))

    def test_refusals(self):
        with self.assertRaises(SystemExit):
            main(["cst", "--from", "di", "--to", "di", "--out", str(self.tmp)])
        with self.assertRaises(SystemExit):  # argparse: 17 is not a cst size
            main(["cst", "--from", "vlog", "--to", "di", "--size", "17", "--out", str(self.tmp)])
        with self.assertRaises(PermissionError):
            cst.write("vlog", "di", 33, PROD / "tests" / "lutlab-cst-out")
        self.assertFalse((PROD / "tests" / "lutlab-cst-out").exists())

    def test_gh7_is_a_vlog_camera(self):
        self.assertEqual(spaces.camera("GH7"), ("vlog", "GH7"))
        self.assertEqual(spaces.target("gh7"), ("vlog", "GH7", None))


class LutstatsTest(unittest.TestCase):
    def test_delta_e2000_sharma_pairs(self):
        # Sharma, Wu and Dalal (2005): lutstats reuses spaces.delta_e2000; these pin it on more of the table
        pairs = [((50, 2.6772, -79.7751), (50, 0, -82.7485), 2.0425), ((50, 2.49, -0.001), (50, -2.49, 0.0009), 7.1792),
                 ((50, 2.5, 0), (73, 25, -18), 27.1492), ((50, 0, 0), (50, -1, 2), 2.3669),
                 ((50, 2.5, 0), (56, -27, -3), 31.9030), ((50, 2.5, 0), (61, -5, 29), 22.8977),
                 ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
                 ((2.0776, 0.0795, -1.1350), (0.9033, -0.0636, -0.5514), 0.9082)]
        a, b, want = (np.array([p[i] for p in pairs], dtype=np.float64) for i in range(3))
        np.testing.assert_allclose(spaces.delta_e2000(a, b), want, atol=1e-4)

    def test_natural_samples_match_pipeline(self):
        from lutlab import pipeline
        for s in ("vlog", "di", "r709"):
            np.testing.assert_array_equal(lutstats.natural(s), pipeline.natural_samples(unit(cube.lattice(2)), s))

    def test_tetrahedral(self):
        rng = np.random.default_rng(3)
        lut = unit(cst.table("vlog", "r709", 17))
        nodes = cube.lattice(17)[::4, ::3, ::5].reshape(-1, 3)
        np.testing.assert_allclose(lutstats.tetra(lut, nodes), cube.sample(lut, nodes), atol=1e-12)
        # exact for an affine table anywhere, as trilinear is
        m, c = rng.normal(size=(3, 3)), rng.normal(size=3)
        aff = unit(cube.lattice(5) @ m.T + c)
        x = rng.uniform(0, 1, (500, 3))
        np.testing.assert_allclose(lutstats.tetra(aff, x), x @ m.T + c, atol=1e-12)
        # a grey stays grey: tetrahedral walks the cell diagonal, trilinear mixes off-axis nodes in
        g = np.repeat(rng.uniform(0, 1, 300)[:, None], 3, -1)
        t, tri = lutstats.tetra(lut, g), cube.sample(lut, g)
        self.assertLess(np.abs(t - t[:, :1]).max(), 1e-12)
        self.assertGreater(np.abs(tri - tri[:, :1]).max(), 1e-4)
        self.assertEqual(lutstats.tetra(lut, np.array([0.4233, 0.4233, 0.4233])).shape, (3,))

    def test_check_own_cube_near_and_wrong_cube_far(self):
        r = lutstats.check(unit(cst.table("vlog", "di", 33)), "vlog", "di")
        self.assertEqual((r["size"], r["interp"]), (33, "tetrahedral"))
        self.assertLess(r["natural"]["mean"], 0.1)
        self.assertLess(r["natural"]["max"], 1.0)
        self.assertLess(r["neutral"]["max"], 0.2)
        wrong = lutstats.check(unit(cst.table("vlog", "r709", 33)), "bmdfilm5", "r709")
        self.assertGreater(wrong["natural"]["mean"], 2.0)
        self.assertIn("natural dE2000 mean", lutstats.summary(r))

    def test_round_trip(self):
        r33, r65 = lutstats.roundtrip("vlog", "di", 33), lutstats.roundtrip("vlog", "di", 65)
        # measured 33: natural p99 0.55 max 0.85, neutral max 1.54 (at V-Log 1.0, eight stops over 18% grey);
        # 65: natural p99 0.17 max 0.30
        self.assertLess(r33["natural"]["p99"], 0.7)
        self.assertLess(r33["natural"]["max"], 1.2)
        self.assertLess(r33["neutral"]["max"], 2.0)
        self.assertLess(r65["natural"]["p99"], 0.25)
        self.assertLess(r65["natural"]["max"], 0.5)
        self.assertLess(r65["natural"]["mean"], r33["natural"]["mean"])
        self.assertLess(r33["code_error_10bit"]["natural"]["p99"], 3.0)
        tri = lutstats.roundtrip("vlog", "di", 33, "trilinear")
        self.assertLess(r33["neutral"]["mean"], tri["neutral"]["mean"])

    def test_cli(self):
        out = subprocess.run([sys.executable, "-m", "lutlab", "lutstats", "roundtrip", "--from", "vlog", "--to", "di"],
                             cwd=PROD, capture_output=True, text=True, check=True).stdout
        r = json.loads(out[:out.rindex("}") + 1])
        self.assertEqual((r["mode"], r["size"]), ("roundtrip", 65))
        self.assertIn("roundtrip vlog <-> di 65^3 tetrahedral", out)
        with tempfile.TemporaryDirectory() as tmp:
            main(["cst", "--from", "vlog", "--to", "di", "--out", tmp])
            out = subprocess.run([sys.executable, "-m", "lutlab", "lutstats", "check", f"{tmp}/vlog-to-di-65.cube",
                                  "--from", "vlog", "--to", "di", "--interp", "trilinear"],
                                 cwd=PROD, capture_output=True, text=True, check=True).stdout
        self.assertIn("check vlog -> di 65^3 trilinear", out)


class PackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def unpack(self, path):
        d = Path(tempfile.mkdtemp(dir=self.tmp))
        with zipfile.ZipFile(path) as z:
            z.extractall(d)
            return d / Path(path).stem, sorted(z.namelist())

    def test_contents_parse_and_sums_verify(self):
        path = pack.build(["s5ii", "gh7", "rcm"], self.tmp, 33, "20261022")
        self.assertEqual(path.name, "rp-luts-20261022.zip")
        root, names = self.unpack(path)
        rel = ["cst/di-to-r709-33.cube", "cst/vlog-to-di-33.cube", "cst/vlog-to-r709-33.cube",
               "check/cast-magenta.cube", "README.txt", "SHA256SUMS"]
        self.assertEqual(names, sorted(f"rp-luts-20261022/{r}" for r in rel))
        for f in root.rglob("*.cube"):
            self.assertEqual(cube.read_cube(str(f))["table"].shape, (33, 33, 33, 3), f.name)
        np.testing.assert_allclose(cube.read_cube(str(root / "cst/vlog-to-di-33.cube"))["table"],
                                   cst.table("vlog", "di", 33), atol=1e-6)
        sums = dict(reversed(line.split("  ", 1)) for line in (root / "SHA256SUMS").read_text().splitlines())
        self.assertEqual(sorted(sums), sorted(r for r in rel if r != "SHA256SUMS"))
        for m, h in sums.items():
            self.assertEqual(hashlib.sha256((root / m).read_bytes()).hexdigest(), h, m)
        text = (root / "README.txt").read_text()
        for s in ("On My iPad > DaVinci Resolve > LUT", "Update Lists", "DaVinci Intermediate timeline", "Unverified",
                  "No looks in this pack", "vlog-to-di-33.cube: Panasonic V-Log / V-Gamut (S5II, GH7) in"):
            self.assertIn(s, text)
        self.assertNotIn(str(Path.home()), text)
        # di-to-r709 writes Rec.709: it must not sit with the colour-managed setup, where Resolve maps it twice
        bullets = text.split("\n- ")
        self.assertNotIn("di-to-r709", next(b for b in bullets if "Timeline to DaVinci WG/Intermediate" in b))
        self.assertIn("Never use it in a Resolve Color Managed project",
                      next(b for b in bullets if b.startswith("cst/di-to-r709-*.cube")))

    def test_bad_dates_refused_and_no_part_left(self):
        out = self.tmp / "d"
        for d in ("2026131", "202611", "19790101", "00010101", "21080101"):
            with self.assertRaises(ValueError, msg=d):
                pack.build(["s5ii"], out, 33, d)
        with self.assertRaises(SystemExit):
            main(["pack", "--camera", "s5ii", "--date", "2026131", "--out", str(out)])
        self.assertEqual(list(out.glob("*")) if out.exists() else [], [])
        with mock.patch.object(zipfile.ZipFile, "writestr", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                pack.build(["s5ii"], out, 33, "20261022")
        self.assertEqual(list(out.glob("*")), [])
        with zipfile.ZipFile(pack.build(["s5ii"], out, 33, "20260131")) as z:
            self.assertEqual({i.date_time for i in z.infolist()}, {(2026, 1, 31, 0, 0, 0)})

    def test_cast_turns_grey_magenta(self):
        root, _ = self.unpack(pack.build(["s5ii"], self.tmp, 33, "20261022"))
        lut = cube.read_cube(str(root / "check/cast-magenta.cube"))
        r, g, b = lutstats.tetra(lut, np.full(3, 0.5))
        self.assertGreater(r, g)
        self.assertGreater(b, g)
        L, a, bb = spaces.cielab(spaces.to_lin709(np.array([r, g, b]), "r709"))
        self.assertGreater(a, 5)
        self.assertLess(bb, 0)

    def test_reproducible_and_refuses(self):
        a = pack.build(["s5ii"], self.tmp / "a", 33, "20261022").read_bytes()
        b = pack.build(["s5ii"], self.tmp / "b", 33, "20261022").read_bytes()
        self.assertEqual(a, b)
        with self.assertRaises(FileExistsError):
            pack.build(["s5ii"], self.tmp / "a", 33, "20261022")
        pack.build(["s5ii"], self.tmp / "a", 33, "20261022", overwrite=True)
        with self.assertRaises(PermissionError):
            pack.build(["s5ii"], PROD / "tests" / "lutlab-pack-out", 33, "20261022")
        with self.assertRaises(ValueError):
            pack.build(["fx3"], self.tmp / "c", 33, "20261022")
        with self.assertRaises(ValueError):
            pack.build(["s5ii"], self.tmp / "c", 33, "2026-10-22")

    def test_cli(self):
        out = self.tmp / "cli"
        self.assertEqual(main(["pack", "--camera", "pyxis,rcm", "--date", "20261022", "--out", str(out)]), 0)
        _, names = self.unpack(out / "rp-luts-20261022.zip")
        self.assertIn("rp-luts-20261022/cst/bmdfilm5-to-di-33.cube", names)
        self.assertEqual(sum(n.endswith(".cube") for n in names), 4)  # bmdfilm5 to di and r709, di to r709, cast
        main(["pack", "--camera", "S5II, s5ii,gh7", "--date", "20261022", "--out", str(out / "dup")])
        root, names = self.unpack(out / "dup" / "rp-luts-20261022.zip")
        self.assertIn("Cameras: s5ii, gh7.", (root / "README.txt").read_text())
        self.assertEqual(sum(n.endswith(".cube") for n in names), 3)
        with self.assertRaises(SystemExit):
            main(["pack", "--camera", "s5ii", "--looks", str(self.tmp), "--out", str(out)])
        with self.assertRaises(SystemExit):
            main(["pack", "--camera", "s5ii,fx3", "--out", str(out)])

    def test_looks_on_a_base(self):
        base = self.tmp / "base"
        base.mkdir()
        (base / "params.json").write_text(json.dumps(model.blank("logc3", "r709")))
        delta = self.tmp / "warm.json"
        delta.write_text(json.dumps({"name": "warm", "white_balance": {"temp": 20}}))
        root, names = self.unpack(pack.build(["s5ii", "gh7", "rcm"], self.tmp / "l", 33, "20261022",
                                             base=str(base / "params.json"), deltas=[str(delta)]))
        looks = sorted(n.split("/looks/")[1] for n in names if "/looks/" in n)
        self.assertEqual(looks, ["warm [GH7].cube", "warm [RCM].cube", "warm [S5II].cube"])
        np.testing.assert_allclose(cube.read_cube(str(root / "looks/warm [GH7].cube"))["table"],
                                   cube.read_cube(str(root / "looks/warm [S5II].cube"))["table"])
        self.assertNotIn("No looks", (root / "README.txt").read_text())
        self.assertIn("looks/warm [RCM].cube", (root / "SHA256SUMS").read_text())


class GuardAndNamesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_synced_folder_is_refused_whatever_its_case(self):
        from lutlab import guard
        synced = self.tmp / "Synced"
        synced.mkdir()
        # the case rule is macOS's (APFS is case-insensitive by default); exercise it on any CI host
        with mock.patch.object(guard, "ICLOUD", [synced]), mock.patch.object(guard.sys, "platform", "darwin"):
            with self.assertRaises(PermissionError):
                guard.check_out_dir(self.tmp / "synced" / "x")
        self.assertFalse((self.tmp / "synced" / "x").exists())

    def test_a_differently_cased_folder_is_allowed_off_macos(self):
        from lutlab import guard
        synced = self.tmp / "Synced"
        synced.mkdir()
        with mock.patch.object(guard, "ICLOUD", [synced]), mock.patch.object(guard.sys, "platform", "linux"):
            self.assertTrue(guard.check_out_dir(self.tmp / "synced" / "x").is_dir())

    def test_a_look_name_with_a_path_is_refused(self):
        from lutlab import pipeline
        for name in (str(self.tmp / "escaped"), "Day/Night", ".."):
            delta = self.tmp / "d.json"
            delta.write_text(json.dumps({"name": name}))
            with self.assertRaises(ValueError):
                pipeline.looks(self.tmp / "missing-params.json", [delta], ["s5ii"], self.tmp / "out")
        self.assertFalse((self.tmp / "escaped").exists())


class NumpyOnlyTest(unittest.TestCase):
    def test_runs_with_scipy_pillow_and_opencv_blocked(self):
        code = f"""
import sys
for m in ("scipy", "PIL", "cv2"):
    sys.modules[m] = None
sys.path.insert(0, {str(PROD)!r})
from lutlab.__main__ import main
out = sys.argv[1]
assert main(["cst", "--from", "vlog", "--to", "di", "--out", out]) == 0
assert main(["lutstats", "roundtrip", "--from", "vlog", "--to", "di"]) == 0
assert main(["lutstats", "check", out + "/vlog-to-di-65.cube", "--from", "vlog", "--to", "di"]) == 0
assert main(["pack", "--camera", "s5ii,gh7,rcm", "--date", "20261022", "--out", out]) == 0
assert "lutlab.pipeline" not in sys.modules
"""
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run([sys.executable, "-c", code, tmp], capture_output=True, text=True,
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((Path(tmp) / "rp-luts-20261022.zip").exists())
            self.assertTrue((Path(tmp) / "vlog-to-di-65.cube").exists())


if __name__ == "__main__":
    unittest.main()
