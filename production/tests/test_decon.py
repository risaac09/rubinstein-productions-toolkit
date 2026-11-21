"""
Tests for rpresolve.decon on clips ffmpeg makes here: flat colour shots at a method's own
timing, cut on whole frames as a render is, with the method's end black.
"""

import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import delivercheck as dc, methods  # noqa: E402

try:
    import numpy  # noqa: F401
    from rpresolve import cmd_decon, decon, shots  # noqa: E402
except ImportError:  # numpy missing on this interpreter
    decon = None

# light and dark in turn, so every join is a luma step scdet sees
PALETTE = ["0xe8e8e8", "0x202080", "0xe0c020", "0x208020", "0xf0a0c0", "0x802020", "0x80e0f0", "0x403010"]


def render(out, method, order=None, end=None):
    """method's slots as flat colour, cut on whole frames, then end seconds of black."""
    fps, secs = method["fps"], [s["seconds"] for s in method["slots"]]
    edges, t = [0], 0.0
    for d in secs:
        t += d
        edges.append(round(t * fps))
    frames = [b - a for a, b in zip(edges, edges[1:])]
    order = list(order if order is not None else range(len(frames)))
    parts = [f"color=c={PALETTE[i % len(PALETTE)]}:s=192x108:r={fps}:d={frames[i] / fps}" for i in order]
    tail = methods.end_black(method) if end is None else end
    if tail:
        parts.append(f"color=c=black:s=192x108:r={fps}:d={tail}")
    graph = ";".join(f"{p},format=yuv420p[v{i}]" for i, p in enumerate(parts))
    graph += ";" + "".join(f"[v{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=0[v]"
    subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-filter_complex", graph,
                    "-map", "[v]", "-c:v", "ffv1", out], check=True, stdin=subprocess.DEVNULL)


@unittest.skipIf(decon is None, "numpy not installed")
@unittest.skipUnless(os.access(dc.FFMPEG, os.X_OK), "ffmpeg missing")
class TestDecon(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.method = methods.load("punch-montage")  # 15 slots, shots down to 3 frames, end black
        cls.ref = os.path.join(cls.tmp.name, "ref.mkv")
        render(cls.ref, cls.method)
        cls.report = shots.build(cls.ref, dissolves=False)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_recipe_round_trip(self):
        rec = decon.recipe(self.report, self.ref, "punch-check")
        self.assertEqual(methods.validate(rec), [])
        self.assertAlmostEqual(methods.end_black(rec), 0.7, delta=1 / self.method["fps"])  # cut on a frame
        trip = decon.match_method(rec, self.method)
        self.assertTrue(trip["ok"], trip)
        self.assertEqual(trip["slots"], [15, 15])
        self.assertEqual(rec["aspect"], "16:9")

    def test_compare_self_is_perfect(self):
        r = decon.compare(self.report, self.report, self.ref, self.ref)
        self.assertEqual(r["score"], 100.0)
        self.assertEqual(r["cut_error_frames"]["max"], 0.0)

    def test_compare_shuffled_scores_low(self):
        shuf = os.path.join(self.tmp.name, "shuf.mkv")
        order = list(range(len(self.method["slots"])))
        random.Random(3).shuffle(order)
        render(shuf, self.method, order)
        sheet = os.path.join(self.tmp.name, "sheet.png")
        r = decon.compare(self.report, shots.build(shuf, dissolves=False), self.ref, shuf, sheet)
        self.assertLess(r["score"], 60)
        self.assertLess(r["cut_error_frames"]["recall"], 0.5)
        self.assertTrue(os.path.getsize(sheet) > 0)

    def test_cli_recipe_against(self):
        out = os.path.join(self.tmp.name, "recipe.json")
        so, se = StringIO(), StringIO()
        with redirect_stdout(so), redirect_stderr(se):
            code = cmd_decon.run(_parse(["recipe", self.ref, "--out", out, "--id", "punch-check",
                                         "--against", "punch-montage", "--no-dissolves"]))
        self.assertEqual(code, 0, se.getvalue())
        self.assertIn("pass", so.getvalue())
        with open(out, encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)["slots"]), 15)


def _parse(argv):
    import argparse
    p = argparse.ArgumentParser()
    cmd_decon.add_arguments(p)
    return p.parse_args(argv)


if __name__ == "__main__":
    unittest.main()
