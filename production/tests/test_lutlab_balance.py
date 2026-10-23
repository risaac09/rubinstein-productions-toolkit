"""
Offline tests for lutlab balance: per-shot exposure and white balance onto a neutral reference or a skin
target, the CDL it writes, and the set table. Synthetic only: each frame is a flat skin patch (the face
box) over a flat neutral patch, in a camera log with a known cast and exposure. stdlib unittest; numpy,
OpenCV and rpstills from the ~/.venvs/rp-tools venv.

Run: ~/.venvs/rp-tools/bin/python -m unittest production.tests.test_lutlab_balance   (from the repo root)
"""

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

PROD = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROD))

from lutlab import balance, cdl, cube, spaces  # noqa: E402
from lutlab.__main__ import main  # noqa: E402

FACE = {"x": 0.35, "y": 0.08, "w": 0.30, "h": 0.40}
FACE_ARGS = ["--face", "0.35", "0.08", "0.30", "0.40"]
NEUTRAL_BOX = ["--neutral-box", "0.3", "0.65", "0.7", "0.95"]
SKIN_SRGB = np.array([0.80, 0.60, 0.50])     # a Lab near (66, 17, 20)
W, H = 640, 360


BEIGE = np.array([0.75, 0.73, 0.68])         # a warm wall: pale, low chroma (about 16), a hue away from skin


def frame(space, cast=(1.0, 1.0, 1.0), ev=0.0, neutral=True, wall=(0.25, 0.30, 0.45), skin=SKIN_SRGB, beige=False):
    """Code values (H, W, 3): a wall, a skin patch under FACE, a neutral grey patch below it (or a blue one)."""
    img = np.tile(spaces.srgb_decode(np.array(wall)), (H, W, 1))
    img[int(.08 * H):int(.48 * H), int(.35 * W):int(.65 * W)] = spaces.srgb_decode(skin)
    img[int(.62 * H):, int(.25 * W):int(.75 * W)] = spaces.srgb_decode(np.array([0.5] * 3 if neutral else [0.2, 0.3, 0.9]))
    if beige:  # most of the row under the face, beside a short grey patch
        img[int(.62 * H):, int(.52 * W):] = spaces.srgb_decode(BEIGE)
    cam = spaces.SPACES[space][0](np.clip(spaces.from_lin709(img, space), 0, 1))
    return np.clip(spaces.SPACES[space][1](cam * np.asarray(cast) * 2.0 ** ev), 0, 1)


def write_shot(root, name, space, **kw):
    d = Path(root) / name
    d.mkdir()
    for i in range(3):  # three near-identical frames: the sampling takes them as they come
        cv2.imwrite(str(d / f"f{i}.png"), np.round(frame(space, **kw)[..., ::-1] * 65535).astype(np.uint16))
    return str(d)


def run(*argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(["balance", *argv])
    return code, buf.getvalue()


class Balance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def out(self, name="o"):
        return str(Path(self.tmp) / name)

    def rows(self, name="o"):
        return json.loads((Path(self.tmp) / name / "balance.json").read_text())

    def test_recovers_cast_and_exposure(self):
        for space in ("vlog", "bmdfilm5"):
            with self.subTest(space=space):
                shot = write_shot(self.tmp, f"s-{space}", space, cast=(1.15, 1.0, 0.85), ev=-0.7)
                code, _ = run(shot, "--space", space, *FACE_ARGS, *NEUTRAL_BOX, "--out", self.out(space))
                r = self.rows(space)["shots"][0]
                self.assertEqual((code, r["status"]), (0, "converged"))
                self.assertLess(abs(r["after"]["skin"][0] - 64.0), 1.0)           # exposure on the skin
                self.assertLess(max(abs(v) for v in r["after"]["neutral_ab"]), 1.0)  # the neutral is neutral
                self.assertGreater(r["before"]["delta_e"], 10)                      # it started well off
                g = np.array(r["gains"]) * np.array([1.15, 1.0, 0.85]) * 2 ** -0.7  # the cast, undone
                self.assertLess(np.ptp(g) / g.mean(), 0.03)

    def test_bright_neutral_with_dark_stripes_still_balances(self):
        """A white garment that clips at the cheek's exposure, striped dark blue: only the stripes survive the
        clip filter, and they used to be taken as the neutral."""
        cast = (1.15, 1.0, 0.85)
        img = frame("vlog", cast=cast, skin=np.array([0.35, 0.27, 0.22]))
        lin = np.tile(spaces.srgb_decode(np.array([0.85] * 3)), (H, W, 1))
        lin[:, np.arange(W) % 10 > 6] = spaces.srgb_decode(np.array([0.30, 0.30, 0.45]))
        cam = spaces.SPACES["vlog"][0](np.clip(spaces.from_lin709(lin, "vlog"), 0, 1)) * np.asarray(cast)
        rows, cols = slice(int(.62 * H), None), slice(int(.25 * W), int(.75 * W))
        img[rows, cols] = np.clip(spaces.SPACES["vlog"][1](cam), 0, 1)[rows, cols]
        d = Path(self.tmp) / "stripes"
        d.mkdir()
        cv2.imwrite(str(d / "f0.png"), np.round(img[..., ::-1] * 65535).astype(np.uint16))
        run(str(d), "--space", "vlog", "--frames", "1", *FACE_ARGS, *NEUTRAL_BOX, "--out", self.out())
        g = np.array(self.rows()["shots"][0]["gains"]) * np.asarray(cast)
        self.assertLess(np.ptp(g) / g.mean(), 0.04)      # the white, not the stripes, set the white balance

    def test_auto_neutral_below_the_face(self):
        shot = write_shot(self.tmp, "a", "vlog", cast=(1.1, 1.0, 0.9))
        code, _ = run(shot, "--space", "vlog", *FACE_ARGS, "--out", self.out())
        r = self.rows()["shots"][0]
        self.assertEqual((code, r["status"]), (0, "converged"))
        self.assertLess(max(abs(v) for v in r["after"]["neutral_ab"]), 1.0)

    def test_no_neutral_is_refused(self):
        shot = write_shot(self.tmp, "blue", "vlog", neutral=False, cast=(1.1, 1.0, 0.9))
        code, text = run(shot, "--space", "vlog", *FACE_ARGS, "--out", self.out())
        r = self.rows()["shots"][0]
        self.assertEqual((code, r["status"]), (1, "no_neutral"))
        self.assertIn("neutral", r["error"])
        self.assertFalse(list((Path(self.tmp) / "o").glob("*.cc")))   # nothing to apply

    def test_wb_from_takes_white_balance_and_solves_ev(self):
        a = write_shot(self.tmp, "a", "vlog", cast=(1.15, 1.0, 0.85), ev=-0.3)
        b = write_shot(self.tmp, "b", "vlog", cast=(1.15, 1.0, 0.85), ev=0.6, neutral=False)
        code, _ = run(b, a, "--space", "vlog", *FACE_ARGS, "--wb-from", "a", "--out", self.out())
        rb, ra = self.rows()["shots"]  # rows keep the order given
        self.assertEqual((ra["shot"], rb["shot"], code), ("a", "b", 0))
        self.assertEqual((rb["mired"], rb["tint"], rb["wb_from"]), (ra["mired"], ra["tint"], "a"))
        self.assertLess(abs(rb["after"]["skin"][0] - 64.0), 1.0)
        self.assertIsNone(rb["after"]["neutral_ab"])
        # and from the earlier run's file
        code, _ = run(b, "--space", "vlog", *FACE_ARGS, "--wb-from", f"{self.out()}/balance.json:a", "--out", self.out("o2"))
        rb2 = self.rows("o2")["shots"][0]
        self.assertEqual((code, rb2["mired"], rb2["ev"]), (0, ra["mired"], rb["ev"]))

    def test_wb_from_a_failed_reference_fails_the_rest(self):
        a = write_shot(self.tmp, "a", "vlog", neutral=False)
        b = write_shot(self.tmp, "b", "vlog", neutral=False)
        code, _ = run(a, b, "--space", "vlog", *FACE_ARGS, "--wb-from", "a", "--out", self.out())
        self.assertEqual((code, [r["status"] for r in self.rows()["shots"]]), (1, ["no_neutral", "no_neutral"]))

    def test_target_mode_from_json(self):
        shot = write_shot(self.tmp, "t", "vlog", cast=(1.1, 1.0, 0.9))
        tj = Path(self.tmp) / "targets.json"
        tj.write_text(json.dumps({"default": [60, 14, 18], "other": [50, 10, 10]}))
        code, _ = run(shot, "--space", "vlog", *FACE_ARGS, "--targets", str(tj), "--out", self.out())
        r = self.rows()["shots"][0]
        self.assertEqual((code, r["mode"]), (0, "target"))
        self.assertLess(np.linalg.norm(np.array(r["after"]["skin"]) - [60, 14, 18]), 1.0)
        with self.assertRaises(ValueError):
            tj.write_text("[1, 2]")
            balance.load_targets(tj)

    def test_cdl_round_trips(self):
        shot = write_shot(self.tmp, "c", "bmdfilm5", cast=(1.15, 1.0, 0.85), ev=-0.5)
        run(shot, "--space", "bmdfilm5", *FACE_ARGS, *NEUTRAL_BOX, "--out", self.out())
        r = self.rows()["shots"][0]
        out = Path(self.tmp) / "o"
        c = cdl.read_cdl(out / "c.cc")
        self.assertTrue(np.allclose(c["slope"], r["cdl"]["slope"], atol=1e-5))
        self.assertLess(r["cdl_error"]["max"], 2.0)
        # the CDL against the exact correction, on a grey ramp and the skin patch, in the camera's code values
        codes = np.concatenate([np.outer(np.linspace(0.25, 0.75, 9), np.ones(3)), [frame("bmdfilm5")[40, 320]]])
        dec, enc = spaces.SPACES["bmdfilm5"][:2]
        exact = enc(dec(codes) * np.array(r["gains"]))
        self.assertLess(np.abs(cdl.cdl_apply(codes, c) - exact).max(), 0.02)
        # and the baked cube is the same CDL
        lut = cube.read_cube(str(out / "c.cube"))
        self.assertLess(np.abs(cube.sample(lut, codes) - np.clip(cdl.cdl_apply(codes, c), 0, 1)).max(), 0.005)

    def test_set_table_tightens_the_set(self):
        shots = [write_shot(self.tmp, f"s{i}", "vlog", cast=cast, ev=ev)
                 for i, (cast, ev) in enumerate([((1.2, 1, .8), -.6), ((.85, 1, 1.15), .4), ((1, 1, 1), 0)])]
        code, text = run(*shots, "--space", "vlog", *FACE_ARGS, *NEUTRAL_BOX, "--out", self.out())
        doc = self.rows()
        st = doc["set"]
        self.assertEqual(code, 0)
        self.assertEqual(len(st["shots"]), 3)
        self.assertLess(max(s["d_E00"] for s in st["shots"]), 1.5)
        self.assertGreater(max(s["d_E00_before"] for s in st["shots"]), 5)
        self.assertIn("| s0 |", text)
        self.assertTrue((Path(self.tmp) / "o" / "balance.md").exists())

    def test_video_samples_the_middle_of_the_clip(self):
        img = frame("vlog")
        with mock.patch("lutlab.verify.probe_size", return_value=(W, H)), \
                mock.patch.object(balance, "_duration", return_value=10.0), \
                mock.patch("lutlab.verify.decode_frame", return_value=img) as dec, \
                mock.patch.object(Path, "is_file", return_value=True), mock.patch.object(Path, "is_dir", return_value=False):
            frames = balance.read_frames("clip.mov", 5)
        self.assertEqual([round(c.args[1], 2) for c in dec.call_args_list], [1.0, 3.0, 5.0, 7.0, 9.0])
        self.assertEqual(len(frames), 5)

    def test_bad_hero_and_existing_outputs_stop_before_writing(self):
        shot = write_shot(self.tmp, "h", "vlog")
        args = [shot, "--space", "vlog", *FACE_ARGS, *NEUTRAL_BOX, "--out", self.out()]
        with self.assertRaises(SystemExit):
            run(*args, "--hero", "nope")
        self.assertEqual(run(*args)[0], 0)
        with self.assertRaises(SystemExit):
            run(*args)                                   # balance.json is there: nothing is rewritten
        self.assertEqual(run(*args, "--overwrite")[0], 0)

    def test_no_vision_and_no_face_flag_is_no_face(self):
        shot = write_shot(self.tmp, "v", "vlog")
        with mock.patch("rpstills.vision.faces", return_value=None):
            code, _ = run(shot, "--space", "vlog", "--out", self.out())
        self.assertEqual((code, self.rows()["shots"][0]["status"]), (1, "no_face"))

    def test_refuses_output_inside_a_git_tree(self):
        shot = write_shot(self.tmp, "g", "vlog")
        with self.assertRaises(PermissionError):
            run(shot, "--space", "vlog", *FACE_ARGS, *NEUTRAL_BOX, "--out", str(PROD / "x-balance-out"))


def person_matte_image():
    """The person of frame(): the face patch and the neutral patch under it, the wall outside (uint8 grey)."""
    m = np.zeros((H, W), np.uint8)
    m[int(.08 * H):int(.48 * H), int(.35 * W):int(.65 * W)] = 255
    m[int(.62 * H):, int(.25 * W):int(.52 * W)] = 255
    return m


class Matte(unittest.TestCase):
    CAST = (1.12, 1.0, 0.88)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        d = Path(self.tmp) / "walled"
        d.mkdir()
        cv2.imwrite(str(d / "f0.png"), np.round(frame("vlog", cast=self.CAST, beige=True)[..., ::-1] * 65535).astype(np.uint16))
        self.shot = str(d)
        self.matte_file = str(Path(self.tmp) / "matte.png")
        cv2.imwrite(self.matte_file, person_matte_image())

    def run_balance(self, *extra):
        out = str(Path(self.tmp) / "o")
        code, _ = run(self.shot, "--space", "vlog", "--frames", "1", *FACE_ARGS, "--out", out, "--overwrite", *extra)
        return code, json.loads((Path(out) / "balance.json").read_text())["shots"][0]

    def cast_left(self, r):
        g = np.array(r["gains"]) * np.asarray(self.CAST)       # what the correction leaves of the cast
        return float(np.ptp(g) / g.mean())

    def test_wall_beside_the_garment_sets_the_white_balance_without_a_matte_and_not_with_one(self):
        code, r = self.run_balance()
        self.assertGreater(self.cast_left(r), 0.08)            # the wall is most of the "neutral": warm shot, wrong grade
        for spec in (self.matte_file, str(Path(self.tmp) / "mattes")):
            with self.subTest(spec=spec):
                if spec.endswith("mattes"):
                    Path(spec).mkdir()
                    cv2.imwrite(str(Path(spec) / "f0.png"), person_matte_image())
                code, r = self.run_balance("--matte", spec)
                self.assertEqual((code, r["status"]), (0, "converged"))
                self.assertLess(self.cast_left(r), 0.04)       # the grey patch, not the wall, is the neutral
                self.assertAlmostEqual(r["matte"]["person_share"], person_matte_image().mean() / 255, places=2)

    def test_skin_sector_is_centred_on_the_cheek_and_reports_what_it_catches(self):
        _, r = self.run_balance("--matte", self.matte_file, "--skin-width", "30")
        sec = r["matte"]["skin_sector"]
        self.assertEqual(sec["width_deg"], 30.0)
        self.assertLess(abs(sec["hue_deg"] - 50), 25)                  # a warm skin hue, not the wall's
        self.assertEqual(sec["person_share"] > 0.3, True)              # the face patch is in the slice
        self.assertLess(sec["background_share"], 0.05)                 # the blue wall is not
        _, plain = self.run_balance()
        self.assertIsNone(plain.get("matte"))

    def test_matte_folder_without_the_frame_is_a_row_error(self):
        empty = Path(self.tmp) / "empty"
        empty.mkdir()
        cv2.imwrite(str(empty / "other.png"), person_matte_image())
        code, r = self.run_balance("--matte", str(empty))
        self.assertEqual((code, r["status"]), (1, "no_matte"))
        self.assertIn("f0", r["error"])

    def test_read_matte_takes_16_bit_rgb_and_alpha(self):
        from lutlab import matte
        m = person_matte_image()
        for name, img in (("g16.png", m.astype(np.uint16) * 257), ("rgb.png", np.dstack([m] * 3)),
                          ("rgba.png", np.dstack([np.zeros_like(m)] * 3 + [m]))):
            p = Path(self.tmp) / name
            cv2.imwrite(str(p), img)
            got = matte.read_matte(p, (H // 2, W // 2))
            self.assertEqual(got.shape, (H // 2, W // 2))
            self.assertAlmostEqual(float(got.mean()), m.mean() / 255, places=2, msg=name)
        with self.assertRaises(matte.MatteError):
            matte.read_matte(Path(self.tmp) / "none.png", (4, 4))

    def test_vision_matte_through_the_helper_and_without_it(self):
        from lutlab import matte
        fake = []

        def helper(jobs):
            fake.extend(jobs)
            for _, out, _, _ in jobs:
                cv2.imwrite(out, person_matte_image())
            return [True] * len(jobs)
        with mock.patch("rpstills.vision.person_mattes", helper):
            code, r = self.run_balance("--matte", "vision")
        self.assertEqual((code, r["status"]), (0, "converged"))
        x, y = fake[0][2:]
        self.assertEqual((round(x, 2), round(y, 2)), (0.5, 0.28))      # the helper is asked for the face centre
        with mock.patch("rpstills.vision.person_mattes", lambda jobs: None):
            with self.assertRaises(matte.MatteError):
                matte.matter("vision")([("f", frame("vlog"))], "vlog", [FACE])
        with self.assertRaises(SystemExit):                            # an unknown source is a usage error
            run(self.shot, "--space", "vlog", *FACE_ARGS, "--matte", "nowhere", "--out", str(Path(self.tmp) / "x"))

    def test_a_matte_that_fails_on_every_frame_is_no_matte_not_no_neutral(self):
        with mock.patch("rpstills.vision.person_mattes", lambda jobs: [False] * len(jobs)):
            code, r = self.run_balance("--matte", "vision")
        self.assertEqual((code, r["status"]), (1, "no_matte"))

    def test_grabcut_matte_holds_the_person_and_repeats(self):
        from lutlab import matte
        fr = [("f", frame("vlog"))]
        a = matte.matter("grabcut")(fr, "vlog", [FACE])[0]
        b = matte.matter("grabcut")(fr, "vlog", [FACE])[0]
        self.assertTrue(np.array_equal(a, b))
        self.assertGreater(float(a[int(.2 * H):int(.4 * H), int(.45 * W):int(.55 * W)].mean()), 0.95)  # the face


if __name__ == "__main__":
    unittest.main()
