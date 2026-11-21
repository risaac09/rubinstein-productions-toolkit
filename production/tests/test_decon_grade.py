"""
Tests for rpresolve.decon_grade: scene grouping, the vignette flag, `decon recipe --grade`, and the
verifier (a clip graded with a shipped look through ffmpeg lut3d, estimated back, median CIEDE2000 <= 5).
Needs numpy, OpenCV, Pillow, scipy (the rp-tools venv) and ffmpeg.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

PROD = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROD))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rpresolve import delivercheck as dc  # noqa: E402

MISSING = None
try:
    import cv2
    import numpy as np
    from lutlab import cube, model
    from rpresolve import cmd_decon, decon, decon_grade, methods
    from test_decon import _parse
except ImportError as e:  # name the module: the 3.9 job has numpy alone and fails on a skip that blames numpy
    decon_grade, MISSING = None, e.name or str(e)

MAX_DE = 5.0
LOOKS = PROD / "lutlab-looks"


def picture(seed=1, h=108, w=192):
    """A smooth colour field with soft blobs: stands in for an ungraded frame."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w] / np.array([h, w]).reshape(2, 1, 1)
    im = np.stack([0.25 + 0.5 * xx, 0.25 + 0.5 * yy, 0.5 + 0.25 * np.sin(6 * (xx + yy))], -1)
    for _ in range(14):
        cy, cx, r = rng.random(), rng.random(), 0.06 + 0.15 * rng.random()
        im += np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2) / r ** 2))[..., None] * (rng.random(3) - 0.5) * 0.5
    return np.clip(im, 0.03, 0.97)


def clip(path, img, fps=24, secs=1):
    png = path + ".png"
    cv2.imwrite(png, np.round(img[..., ::-1] * 255).astype(np.uint8))
    subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-loop", "1", "-framerate", str(fps),
                    "-i", png, "-t", str(secs), "-c:v", "ffv1", "-pix_fmt", "gbrp", path], check=True,
                   stdin=subprocess.DEVNULL)


def bake_look(look, path, size=33):
    """A shipped look delta as a Rec.709-in, Rec.709-out .cube."""
    p = model.apply_delta(model.blank(), json.loads((LOOKS / f"{look}.json").read_text()))
    grid = cube.lattice(size)
    cube.write_cube(path, model.evaluate(p, grid.reshape(-1, 3)).reshape(grid.shape).clip(0, 1), look)


def grade_clip(src, dst, cube_path):
    subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", src, "-vf",
                    f"lut3d=file={cube_path}:interp=tetrahedral", "-c:v", "ffv1", "-pix_fmt", "gbrp", dst],
                   check=True, stdin=subprocess.DEVNULL)


@unittest.skipIf(decon_grade is None, f"{MISSING} not importable")
@unittest.skipUnless(os.access(dc.FFMPEG, os.X_OK), "ffmpeg missing")
class TestDeconGrade(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_vignette_flag(self):
        flat = np.full((100, 160, 3), 0.5)
        self.assertFalse(decon_grade.vignette(flat))
        yy, xx = np.mgrid[0:100, 0:160]
        fall = 1 - 0.8 * (np.hypot((yy - 50) / 50, (xx - 80) / 80) / 1.41) ** 2
        self.assertTrue(decon_grade.vignette(flat * fall[..., None]))

    def test_scenes_split_on_a_colour_jump(self):
        labs = [np.array([50.0, 0, 0]), np.array([52.0, 1, 0]), np.array([30.0, 40, -40])]
        self.assertEqual(decon_grade.scenes([None] * 3, labs), [[0, 1], [2]])

    def test_known_look_estimated_back(self):
        src = picture()
        worst = {}
        for look in ("print-teal", "bleach", "tungsten"):
            cp = os.path.join(self.tmp.name, look + ".cube")
            bake_look(look, cp)
            a, b = (os.path.join(self.tmp.name, f"{look}-{k}.mkv") for k in ("src", "graded"))
            clip(a, src)
            grade_clip(a, b, cp)
            s, g = decon.frame(a, 0.2, 192) / 255.0, decon.frame(b, 0.2, 192) / 255.0
            self.assertGreater(float(np.abs(s - g).mean()), 0.02, f"{look} did not change the picture")
            worst[look] = decon_grade.verify_known(s, g)
        print("verifier median CIEDE2000:", {k: round(v, 2) for k, v in worst.items()})
        for look, de in worst.items():
            self.assertLessEqual(de, MAX_DE, look)

    def test_one_frame_scene_reads_a_real_frame(self):
        # a one-frame video: the scene's middle sits past the last frame's pts unless read as recipe reads it
        ref = os.path.join(self.tmp.name, "one.mkv")
        clip(ref, picture(3), secs=1 / 24)
        out = os.path.join(self.tmp.name, "one.json")
        so, se = StringIO(), StringIO()
        with redirect_stdout(so), redirect_stderr(se):
            code = cmd_decon.run(_parse(["recipe", ref, "--out", out, "--grade", "--no-dissolves"]))
        self.assertEqual(code, 0, se.getvalue())
        g = json.loads(Path(self.tmp.name, "one.grade", "grade.json").read_text())
        self.assertEqual(len(g["scenes"]), 1)
        self.assertEqual(g["scenes"][0]["still_s"], 0.0)

    def test_cli_grade_sidecar(self):
        # two scenes: a plain picture, then a different one with a corner fall-off
        a, b = picture(1), picture(2)[:, ::-1] * np.array([1.0, 0.85, 0.7])
        yy, xx = np.mgrid[0:108, 0:192]
        b = b * (1 - 0.5 * (np.hypot((yy - 54) / 54, (xx - 96) / 96) / 1.41) ** 2)[..., None]
        parts = []
        for i, img in enumerate((a, b)):
            p = os.path.join(self.tmp.name, f"part{i}.mkv")
            clip(p, np.clip(img, 0.03, 0.97), secs=2)
            parts.append(p)
        ref = os.path.join(self.tmp.name, "ref.mkv")
        lst = os.path.join(self.tmp.name, "list.txt")
        Path(lst).write_text("".join(f"file '{p}'\n" for p in parts))
        subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "0",
                        "-i", lst, "-c", "copy", ref], check=True, stdin=subprocess.DEVNULL)
        out = os.path.join(self.tmp.name, "recipe.json")
        so, se = StringIO(), StringIO()
        with redirect_stdout(so), redirect_stderr(se):
            code = cmd_decon.run(_parse(["recipe", ref, "--out", out, "--grade", "--no-dissolves"]))
        self.assertEqual(code, 0, se.getvalue())
        gdir = os.path.join(self.tmp.name, "recipe.grade")
        g = json.loads(Path(gdir, "grade.json").read_text())
        self.assertEqual(len(g["scenes"]), 2)
        for s in g["scenes"]:
            self.assertTrue(s["cube"] and os.path.getsize(os.path.join(gdir, s["cube"])) > 0, s)
        self.assertEqual([s["flags"]["vignette"] for s in g["scenes"]], [False, True])
        self.assertEqual(g["scenes"][0]["shots"], [1, 1])
        rec = json.loads(Path(out).read_text())  # the method keeps its shape
        self.assertNotIn("grade", rec)
        self.assertEqual(methods.validate(rec), [])


if __name__ == "__main__":
    unittest.main()
