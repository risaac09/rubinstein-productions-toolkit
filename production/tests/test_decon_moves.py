"""
Tests for rpresolve.decon_moves (decon stages D3 and D4). The verifier: synthetic shots warped from a
textured still with known zoom, pan, tilt and shake, encoded by ffmpeg, measured back; scale and pan
error within 5 percent of the move, and the class right. The summarise tests need numpy alone; the
rest need OpenCV and ffmpeg (the rp-tools venv) and skip, naming the module, without them.
"""

import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rpresolve import delivercheck as dc  # noqa: E402

try:
    import numpy as np
    from rpresolve import decon_moves as dm
except ImportError as e:
    dm, NP_MISSING = None, e.name or str(e)
else:
    NP_MISSING = None
try:
    import cv2
    from rpresolve import cmd_decon, decon, methods, shots
    from test_decon import _parse
except ImportError as e:
    CV_MISSING = e.name or str(e)
else:
    CV_MISSING = None

W, H, FPS = 640, 360, 24
TOL = 0.05


def texture(seed, w=1600, h=900):
    """A still with corners everywhere: blurred noise, stretched to full range."""
    rng = np.random.default_rng(seed)
    im = cv2.GaussianBlur(rng.random((h, w)).astype(np.float32), (0, 0), 2.0)
    im = (im - im.min()) / (im.max() - im.min())
    return np.round(im * 255).astype(np.uint8)


def shot(base, n, zoom=(1.0, 1.0), pan=0.0, tilt=0.0, centre=0.5, shake=0.0, ease=None, seed=0):
    """n frames of base seen through a W x H window. zoom runs linearly from zoom[0] to zoom[1] about
    the frame point (centre, 0.5); the camera moves pan of the frame width and tilt of its height
    over the shot (content moves the other way), along ease(u) when given; shake adds random px."""
    rng = np.random.default_rng(seed)
    bh, bw = base.shape
    out = []
    for i in range(n):
        u = i / (n - 1)
        e = ease(u) if ease else u
        z = zoom[0] + (zoom[1] - zoom[0]) * u
        jx, jy = (rng.normal(0, shake, 2) if shake else (0.0, 0.0))
        bx = bw / 2 + (centre - 0.5) * W + pan * W * e + jx  # the base point under the pivot
        by = bh / 2 + tilt * H * e + jy
        px, py = centre * W, H / 2
        m = np.float32([[z, 0, px - z * bx], [0, z, py - z * by]])
        out.append(cv2.warpAffine(base, m, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT))
    return out


def encode(path, frames):
    subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray",
                    "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "ffv1", "-pix_fmt", "yuv420p", path],
                   input=b"".join(f.tobytes() for f in frames), check=True, capture_output=True)


def one_shot_report(n):
    return {"fps": FPS, "shots": [{"shot": 1, "in": 0.0, "out": n / FPS, "duration": n / FPS, "into": "start",
                                   "blend_in": "", "blend_out": ""}]}


@unittest.skipIf(dm is None, f"needs {NP_MISSING}")
class TestSummarise(unittest.TestCase):
    """The arithmetic on given similarities: numpy only."""

    def steps(self, n, s=1.0, tx=0.0, ty=0.0):
        return [np.array([[s, 0, tx], [0, s, ty], [0, 0, 1.0]]) for _ in range(n)]

    def test_static(self):
        r = dm.summarise(self.steps(20), 320, 180)
        self.assertEqual(r["class"], "static")
        self.assertEqual((r["scale"], r["pan"], r["ramp"]), (1.0, 0.0, None))

    def test_push_about_the_centre(self):
        s = 1.15 ** (1 / 20)  # each step scales about the centre: x -> s x + (1 - s) c
        steps = [np.array([[s, 0, (1 - s) * 160], [0, s, (1 - s) * 90], [0, 0, 1.0]]) for _ in range(20)]
        r = dm.summarise(steps, 320, 180)
        self.assertEqual(r["class"], "push")
        self.assertAlmostEqual(r["scale"], 1.15, places=3)
        self.assertAlmostEqual(r["centre"], 0.5, places=2)
        self.assertAlmostEqual(r["pan"], 0.0, places=3)

    def test_pan_and_untracked_steps(self):
        steps = self.steps(30, tx=-3.2)
        steps[4] = None  # a step that could not be fitted counts as no motion
        r = dm.summarise(steps, 320, 180)
        self.assertEqual(r["class"], "pan")
        self.assertAlmostEqual(r["pan"], -3.2 * 29 / 320, places=4)
        self.assertEqual(r["untracked"], 1)

    def test_ramp(self):
        steps = [np.array([[1, 0, -(1 + 4 * i / 29)], [0, 1, 0], [0, 0, 1.0]]) for i in range(30)]
        r = dm.summarise(steps, 320, 180)
        self.assertIsNotNone(r["ramp"])
        self.assertGreater(r["ramp"], dm.RAMP)

    def test_words(self):
        base = {"scale": 1.2, "centre": 0.4, "pan": -0.2, "ramp": None}
        push = dm._words(dict(base, **{"class": "push"}))
        self.assertEqual((push["move"], push["slot_scale"]), ("push-in", [1.0, 1.2]))
        self.assertEqual(push["effects"][0], dict(push["effects"][0], effect="zoom-push", params={"centre": 0.4}))
        pull = dm._words(dict(base, scale=0.8, **{"class": "pull"}))
        self.assertEqual((pull["move"], pull["slot_scale"]), ("static", [1.25, 1.0]))
        pan = dm._words(dict(base, **{"class": "pan"}))
        self.assertEqual(pan["effects"][0]["params"], {"pan_from": 0.5, "pan_to": 0.7})
        for cls, (move, role) in dm.MOVE_WORDS.items():
            from rpresolve import methods as mt
            self.assertIn(move, mt.MOVES, cls)
            self.assertIn(role, mt.ROLES, cls)


@unittest.skipIf(CV_MISSING or NP_MISSING, f"needs {CV_MISSING or NP_MISSING}")
@unittest.skipUnless(os.access(dc.FFMPEG, os.X_OK), "ffmpeg missing")
class TestMeasure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = texture(1)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def measure(self, name, **kw):
        n = kw.pop("n", 48)
        path = os.path.join(self.tmp.name, name + ".mkv")
        encode(path, shot(self.base, n, **kw))
        # the reader trims TRIM_FRAMES off each end, so the truth is the move between those frames
        return dm.measure(one_shot_report(n), path)["shots"][0], n

    def within(self, got, want, what):
        self.assertLessEqual(abs(got - want), TOL * abs(want), f"{what}: measured {got}, truth {want}")

    def inner(self, n, a, b):
        """A linear move from a to b over n frames, as read between the trimmed ends."""
        k = dm.TRIM_FRAMES
        return a + (b - a) * k / (n - 1), a + (b - a) * (n - 1 - k) / (n - 1)

    def test_zoom_push_params(self):
        r, n = self.measure("push", zoom=(1.0, 1.15))  # the zoom-push macro: 1.0 to 1.15 over 48 frames
        z0, z1 = self.inner(n, 1.0, 1.15)
        self.assertEqual(r["class"], "push")
        self.within(r["scale"] - 1, z1 / z0 - 1, "push scale")
        self.assertAlmostEqual(r["centre"], 0.5, delta=0.05)
        self.assertEqual(r["move"], "push-in")

    def test_pull(self):
        r, n = self.measure("pull", zoom=(1.25, 1.0))
        z0, z1 = self.inner(n, 1.25, 1.0)
        self.assertEqual(r["class"], "pull")
        self.within(r["scale"] - 1, z1 / z0 - 1, "pull scale")
        self.assertEqual(r["slot_scale"][1], 1.0)

    def test_push_off_centre(self):
        r, n = self.measure("push-left", zoom=(1.0, 1.3), centre=0.3)
        z0, z1 = self.inner(n, 1.0, 1.3)
        self.within(r["scale"] - 1, z1 / z0 - 1, "push scale")
        self.assertAlmostEqual(r["centre"], 0.3, delta=0.05)

    def test_pan(self):
        r, n = self.measure("pan", pan=0.25)
        p0, p1 = self.inner(n, 0.0, 0.25)
        self.assertEqual(r["class"], "pan")
        self.within(-r["pan"], p1 - p0, "pan")
        rp = next(e for e in r["effects"] if e["effect"] == "reframe-pan")["params"]
        self.within(rp["pan_to"] - rp["pan_from"], p1 - p0, "reframe-pan pan_to")

    def test_tilt(self):
        r, n = self.measure("tilt", tilt=-0.2)
        t0, t1 = self.inner(n, 0.0, -0.2)
        self.assertEqual(r["class"], "tilt")
        self.within(-r["tilt"], t1 - t0, "tilt")
        self.assertEqual(r["move"], "tilt-up")

    def test_static_and_handheld(self):
        r, _ = self.measure("static")
        self.assertEqual(r["class"], "static")
        self.assertLess(r["jitter"], dm.JITTER / 5)
        r, _ = self.measure("hand", shake=4.0, seed=3)
        self.assertEqual(r["class"], "handheld")

    def test_long_ramped_pan(self):
        # 240 frames read every 2nd (MAX_STEPS); the camera starts still and speeds up (speed 2u)
        r, _ = self.measure("ramp", n=240, pan=0.6, ease=lambda u: u * u)
        self.assertEqual(r["class"], "pan")
        self.assertEqual(r["steps"], 117)
        self.assertGreater(r["ramp"], dm.RAMP)
        self.assertTrue(any(e["effect"] == "retime" for e in r["effects"]))

    def test_recipe_moves_cli(self):
        a = shot(texture(2), 48, zoom=(1.0, 1.15))
        b = shot(texture(3), 48, pan=0.25)
        ref = os.path.join(self.tmp.name, "two.mkv")
        encode(ref, [(f // 2 + 100).astype(np.uint8) for f in a] + [f // 2 for f in b])  # a luma step at the cut
        out = os.path.join(self.tmp.name, "two.recipe.json")
        with redirect_stdout(StringIO()) as so, redirect_stderr(StringIO()):
            code = cmd_decon.run(_parse(["recipe", ref, "--out", out, "--moves", "--no-dissolves"]))
        self.assertEqual(code, 0, so.getvalue())
        rec = json.loads(Path(out).read_text())
        self.assertEqual(methods.validate(rec), [])
        self.assertEqual([s["move"] for s in rec["slots"]], ["push-in", "pan"])
        self.assertEqual(rec["slots"][0]["scale"][0], 1.0)
        self.assertIn("feature tracking", rec["summary"])
        side = json.loads(Path(out[:-5] + ".moves.json").read_text())
        self.assertEqual([s["class"] for s in side["shots"]], ["push", "pan"])
        self.assertIn("moves: ", so.getvalue())

    def test_span_leaves_out_both_blends(self):
        a = {"in": 0.0, "out": 2.0, "into": "start", "blend_in": "", "blend_out": ""}
        b = {"in": 2.0, "out": 4.0, "into": "dissolve", "blend_in": 1.75, "blend_out": 2.25}
        self.assertEqual(dm._span(a, b, 24), (2 / 24, 1.75 - 2 / 24))
        self.assertEqual(dm._span(b, None, 24), (2.25 + 2 / 24, 4.0 - 2 / 24))

    def test_apply_refuses_too_few(self):
        rec = {"slots": [{}, {}]}
        with self.assertRaises(dm.MovesError):
            dm.apply(rec, {"shots": [{}]})


if __name__ == "__main__":
    unittest.main()
