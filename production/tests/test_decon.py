"""
Tests for rpresolve.decon on clips ffmpeg makes here: flat colour shots at a method's own
timing, cut on whole frames as a render is, with the method's end black.
"""

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
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

    def test_recipe_carries_a_dip(self):
        S = "s=192x108:r=10"
        parts = [f"color=c=0xe0c020:{S}:d=1.0", f"color=c=0x208020:{S}:d=1.0,fade=t=out:st=0.8:d=0.2",
                 f"color=c=black:{S}:d=0.2", f"color=c=0x802020:{S}:d=1.4,fade=t=in:st=0:d=0.2",
                 f"color=c=0x80e0f0:{S}:d=1.0"]
        graph = ";".join(f"{p},format=yuv420p[v{i}]" for i, p in enumerate(parts))
        graph += ";" + "".join(f"[v{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=0[v]"
        path = os.path.join(self.tmp.name, "dip.mkv")
        subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-filter_complex", graph,
                        "-map", "[v]", "-c:v", "ffv1", path], check=True, stdin=subprocess.DEVNULL)
        rep = shots.build(path)
        self.assertEqual([s["into"] for s in rep["shots"]], ["start", "cut", "dip", "cut"])
        rec = decon.recipe(rep, path, "dip-check")
        self.assertEqual([s["transition_out"]["type"] for s in rec["slots"]], ["cut", "dip", "cut", "cut"])
        self.assertAlmostEqual(rec["slots"][1]["transition_out"]["frames"], 6, delta=1)
        self.assertEqual(methods.validate(rec), [])

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

    def test_compare_builds_both_sides_adaptive_and_records_the_detector(self):
        out = os.path.join(self.tmp.name, "cmp.json")
        with mock.patch.object(shots, "build", wraps=shots.build) as spy:
            code, err = self._cli(["compare", self.ref, self.ref, "--out", out, "--no-dissolves"])
        self.assertEqual(code, 0, err)
        self.assertEqual([c.kwargs.get("adaptive") for c in spy.call_args_list], [True, True])
        with open(out, encoding="utf-8") as f:
            det = json.load(f)["detector"]
        for side in ("reference", "rebuild"):
            self.assertEqual(set(det[side]), {"backend", "threshold_used", "low_contrast", "black_and_white"})
            self.assertIsNotNone(det[side]["threshold_used"])

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


    def _cli(self, argv):
        so, se = StringIO(), StringIO()
        with redirect_stdout(so), redirect_stderr(se):
            code = cmd_decon.run(_parse(argv))
        return code, se.getvalue()

    def _copy(self, name):
        dst = os.path.join(self.tmp.name, name)
        shutil.copy(self.ref, dst)
        return dst, Path(dst).read_bytes()

    def test_refuses_to_overwrite_inputs(self):
        victim, before = self._copy("victim.mkv")
        link = os.path.join(self.tmp.name, "link.json")
        if os.path.lexists(link):
            os.remove(link)
        os.symlink(victim, link)
        for argv in (["recipe", victim, "--out", victim],
                     ["recipe", self.ref, "--out", link],
                     ["compare", self.ref, victim, "--out", os.path.join(self.tmp.name, "c.json"), "--sheet", victim],
                     ["compare", self.ref, victim, "--out", victim],
                     ["compare", self.ref, victim, "--out", os.path.join(self.tmp.name, "x"), "--sheet",
                      os.path.join(self.tmp.name, "x")]):
            code, err = self._cli(argv)
            self.assertEqual(code, 1, argv)
            self.assertTrue("same file" in err or "symbolic link" in err, err)
            self.assertEqual(Path(victim).read_bytes(), before, argv)

    def test_hard_link_to_input_refused(self):
        victim, before = self._copy("hl_victim.mkv")
        hl = os.path.join(self.tmp.name, "hl.json")
        if os.path.exists(hl):
            os.remove(hl)
        os.link(victim, hl)
        code, err = self._cli(["recipe", victim, "--out", hl])
        self.assertEqual(code, 1)
        self.assertEqual(Path(victim).read_bytes(), before)

    def test_sheet_is_a_file_even_when_named_like_a_protocol(self):
        d = tempfile.mkdtemp(dir=self.tmp.name)
        cwd = os.getcwd()
        os.chdir(d)
        try:
            decon.compare(self.report, self.report, self.ref, self.ref, "pipe:1.png")
        finally:
            os.chdir(cwd)
        self.assertEqual(os.listdir(d), ["pipe:1.png"])
        self.assertGreater(os.path.getsize(os.path.join(d, "pipe:1.png")), 0)

    def test_compare_picture_not_a_match_claim(self):
        t, flip = os.path.join(self.tmp.name, "t.mkv"), os.path.join(self.tmp.name, "flip.mkv")
        subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=s=192x108:r=25:d=2", "-c:v", "ffv1", t], check=True, stdin=subprocess.DEVNULL)
        subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", t, "-vf",
                        "hflip,vflip", "-c:v", "ffv1", flip], check=True, stdin=subprocess.DEVNULL)
        a, b = shots.build(t, dissolves=False), shots.build(flip, dissolves=False)
        r = decon.compare(a, b, t, flip)
        self.assertTrue(r["picture_differs"], r)
        self.assertLess(r["parts"]["picture"], 0.5)
        self.assertIn("Picture differs", decon.summary_compare(r))
        self.assertEqual(r["reference"], "t.mkv")  # a name, not a path
        self.assertFalse(decon.compare(self.report, self.report, self.ref, self.ref)["picture_differs"])

    def test_one_frame_last_shot_and_one_frame_file(self):
        flash, one = os.path.join(self.tmp.name, "flash.mkv"), os.path.join(self.tmp.name, "one.mkv")
        g = ("color=c=0x202020:s=192x108:r=25:d=2,format=yuv420p[a];color=c=white:s=192x108:r=25:d=2,"
             "format=yuv420p[b];color=c=0x202020:s=192x108:r=25:d=0.04,format=yuv420p[c];"
             "[a][b][c]concat=n=3:v=1:a=0[v]")
        subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-filter_complex", g, "-map",
                        "[v]", "-c:v", "ffv1", flash], check=True, stdin=subprocess.DEVNULL)
        subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=red:s=192x108:r=25:d=0.04", "-c:v", "ffv1", one], check=True,
                       stdin=subprocess.DEVNULL)
        rep = shots.build(flash, dissolves=False)
        decon.recipe(rep, flash, "flash-check")
        decon.compare(rep, rep, flash, flash)
        r1 = shots.build(one, dissolves=False)
        self.assertEqual(decon.compare(r1, r1, one, one)["score"], 100.0)

    def test_duplicate_beats_and_names(self):
        bp = os.path.join(self.tmp.name, "b.json")
        Path(bp).write_text(json.dumps({"beats_s": [1.0, 1.0, 1.0]}))
        rec = decon.recipe(self.report, self.ref, "dup-beats", beats_path=bp)
        self.assertIsNone(rec["bpm"])
        self.assertNotIn("ref.mkv", json.dumps(rec))
        self.assertEqual(rec["name"], "Recipe dup-beats")


def _parse(argv):
    import argparse
    p = argparse.ArgumentParser()
    cmd_decon.add_arguments(p)
    return p.parse_args(argv)


if __name__ == "__main__":
    unittest.main()
