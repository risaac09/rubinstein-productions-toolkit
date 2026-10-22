"""
Offline tests for rpstills.develop: the solve against an analytic Lab
response of (EV, mired, tint), and the whole develop run against a fake
backend that paints a synthetic portrait whose skin follows that response,
with a fake Vision. The apple and lightcraft command lines are checked
against stand-in executables. No RAW, no swiftc, no Vision, no network.

Run: ~/.venvs/rp-tools/bin/python -m unittest production.tests.test_rpstills_develop
"""

import argparse
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rpstills import develop, look, measure  # noqa: E402

TRIAL = HERE.parent / "stills-looks" / "portrait-cheek-trial-v0.json"
TARGET = (64.0, 15.0, 14.0)
BOUNDS = [(-3.0, 3.0), (1e6 / 12000, 1e6 / 2500), (-6.0, 6.0)]
X0 = (0.0, 1e6 / 5500, 0.0)


def response(x, curved=False):
    """Analytic skin Lab for (ev, mired, tint): L* from a mid-grey exposure,
    b* warming as mired falls, a* with tint, and some coupling. curved makes
    the b* slope at 5500 K a third of its slope at the answer."""
    ev, mired, tint = (float(v) for v in x)
    m = mired - 150.0
    L = 116 * np.cbrt(0.18 * 2.0 ** ev) - 16 - 0.02 * m
    a = 12.0 + 0.6 * tint - 0.03 * m
    b = 6.0 + 0.002 * (200.0 - mired) ** 2 if curved else 6.0 + 25.0 * np.tanh(-m / 60.0)
    return np.array([L, a, b])


class SolveTest(unittest.TestCase):
    def run_solve(self, target=TARGET, bounds=BOUNDS, free=develop.AXES, x0=X0, curved=False, **kw):
        calls = []

        def fn(x):
            calls.append(np.array(x, float))
            return response(x, curved)

        return develop.solve(fn, x0, bounds, target, list(free), **kw), calls

    def test_all_three_axes_reach_the_target(self):
        r, calls = self.run_solve()
        self.assertEqual(r["status"], "converged")
        self.assertTrue(r["converged"])
        self.assertEqual(r["pinned"], [])
        self.assertLessEqual(r["residual"], develop.TOL)
        self.assertLessEqual(r["delta_e"], develop.TOL)
        ev, mired, tint = r["x"]
        self.assertAlmostEqual(ev, 0.844, delta=0.08)
        self.assertAlmostEqual(1e6 / mired, 1e6 / 130.1, delta=150)
        self.assertAlmostEqual(tint, 4.0, delta=1.0)
        self.assertEqual(r["evaluations"], len(calls))
        self.assertEqual(r["evaluations"], 1 + 3 + r["iterations"])

    def test_the_jacobian_follows_a_curved_response(self):
        # b* slope at the start is a third of the slope at the answer: a Jacobian measured
        # once at 5500 K overshoots and rings; Broyden's updates converge
        r, _ = self.run_solve(curved=True, tol=0.3)
        self.assertEqual(r["status"], "converged")
        self.assertAlmostEqual(r["lab"][2], 14.0, delta=0.3)
        self.assertLessEqual(r["iterations"], develop.MAX_ITER)

    def test_tint_pins_and_ev_mired_solve_l_and_b(self):
        r, calls = self.run_solve(target=(64.0, 20.0, 14.0))
        self.assertEqual(r["pinned"], ["tint"])
        self.assertEqual(r["free"], ["ev", "mired"])
        self.assertEqual(r["status"], "pinned")
        self.assertFalse(r["converged"])            # a* is missed: never reported as success
        self.assertEqual(r["x"][2], 6.0)
        self.assertLessEqual(r["residual"], develop.TOL)  # L* and b* met
        self.assertAlmostEqual(r["lab"][0], 64.0, delta=1.0)
        self.assertAlmostEqual(r["lab"][2], 14.0, delta=1.0)
        self.assertGreater(r["delta_e"], 3.0)
        for x in calls:
            self.assertLessEqual(abs(x[2]), 6.0)

    def test_a_long_white_balance_move_does_not_pin_tint_on_the_wrong_side(self):
        # a* falls steeply with mired at 5500 K and flattens by 9000 K: the first linear step
        # asks for tint -40. Pinning on that first hit (with the mired move clipped) left tint
        # at -6 and a* 18; the answer is tint pinned at +6.
        def coupled(x):
            ev, mired, tint = (float(v) for v in x)
            m = mired - 150.0
            return np.array([116 * np.cbrt(0.18 * 2.0 ** ev) - 16 - 0.02 * m,
                             12.0 + 0.6 * tint - 10.0 * np.tanh((m - 25.0) / 12.0), 6.0 - 0.2 * m])
        r = develop.solve(coupled, X0, BOUNDS, (64.0, 27.0, 14.0), list(develop.AXES))
        self.assertEqual((r["status"], r["pinned"], r["x"][2]), ("pinned", ["tint"], 6.0))
        self.assertAlmostEqual(r["lab"][1], 25.6, delta=0.3)

    def test_an_ev_bound_pins_too(self):
        bounds = [(-1.0, 0.5)] + BOUNDS[1:]
        r, _ = self.run_solve(bounds=bounds)
        self.assertIn("ev", r["pinned"])
        self.assertEqual(r["x"][0], 0.5)
        self.assertFalse(r["converged"])
        self.assertLess(r["lab"][0], 63.0)

    def test_running_out_of_steps_is_not_converged(self):
        r, _ = self.run_solve(max_iter=1, tol=0.01)
        self.assertEqual(r["status"], "not_converged")
        self.assertFalse(r["converged"])
        self.assertEqual(r["iterations"], 1)

    def test_an_axis_left_out_stays_and_its_component_is_not_asked_for(self):
        r, calls = self.run_solve(target=(64.0, 40.0, 14.0), free=("ev", "mired"), x0=(0.0, 1e6 / 5500, 2.0))
        self.assertEqual(r["status"], "converged")
        self.assertTrue(r["converged"])
        self.assertEqual({x[2] for x in calls}, {2.0})
        self.assertGreater(r["delta_e"], 10.0)
        self.assertEqual(r["evaluations"], 1 + 2 + r["iterations"])

    def test_start_outside_the_bounds_is_clipped_and_calls_stay_inside(self):
        r, calls = self.run_solve(x0=(0.0, 1e6 / 4834, -14.4))
        self.assertEqual(calls[0][2], -6.0)
        lo, hi = np.array(BOUNDS).T
        for x in calls:
            self.assertTrue(np.all(x >= lo - 1e-9) and np.all(x <= hi + 1e-9), x)
        self.assertEqual(r["status"], "converged")

    def test_bad_free_axes_are_refused(self):
        with self.assertRaises(ValueError):
            develop.solve(response, X0, BOUNDS, TARGET, ["exposure"])
        with self.assertRaises(ValueError):
            develop.solve(response, X0, BOUNDS, TARGET, [])


# ---------------------------------------------------------------- a fake develop

FULL = (1200, 900)  # the fake camera's developed size, W x H


def ring(cx, cy, rx, ry, n=10):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return np.stack([cx + rx * np.cos(t), cy + ry * np.sin(t)], 1)


def face(landmarks=True):
    """Normalized face: eyes at y 0.35, mouth at 0.42."""
    f = {"x": 0.4, "y": 0.2, "w": 0.2, "h": 0.3, "confidence": 1.0}
    if landmarks:
        t = np.linspace(0, np.pi, 11)
        f["landmarks"] = {k: np.asarray(v).tolist() for k, v in {
            "leftEye": ring(0.55, 0.35, 0.02, 0.008), "rightEye": ring(0.45, 0.35, 0.02, 0.008),
            "leftEyebrow": ring(0.45, 0.325, 0.025, 0.004, 6), "rightEyebrow": ring(0.55, 0.325, 0.025, 0.004, 6),
            "outerLips": ring(0.5, 0.42, 0.04, 0.015, 12), "innerLips": ring(0.5, 0.42, 0.03, 0.005),
            "faceContour": np.stack([0.5 + 0.1 * np.cos(t), 0.36 + 0.15 * np.sin(t)], 1),
            "nose": [[0.5, 0.36], [0.49, 0.39], [0.51, 0.39]]}.items()}
    return f


SMALL_FACE = {"x": 0.02, "y": 0.02, "w": 0.03, "h": 0.03, "confidence": 0.9}


def paint(lab, w, h):
    """float sRGB portrait: grey ground, a skin ellipse of the given Lab, dark eyes, red lips."""
    img = np.full((h, w, 3), 0.45, np.float32)
    S = np.array([w, h], float)

    def ell(c, r, colour):
        cv2.ellipse(img, tuple(int(round(v)) for v in np.multiply(c, S)), tuple(max(1, int(round(v))) for v in
                    np.multiply(r, S)), 0, 0, 360, tuple(float(v) for v in colour), -1)
    ell((0.5, 0.36), (0.11, 0.17), np.clip(measure.lab_to_srgb(lab), 0, 1))
    for cx in (0.45, 0.55):
        ell((cx, 0.35), (0.02, 0.008), (0.08, 0.06, 0.06))
    ell((0.5, 0.42), (0.04, 0.015), (0.55, 0.2, 0.2))
    return img


class FakeBackend(develop.Backend):
    name = "fake"
    axes = ("exposure", "temp", "tint")

    def __init__(self, shot=None, fail_on=None, full_offset=(0.0, 0.0, 0.0)):
        self.calls, self.shot, self.fail_on, self.full_offset = [], shot, fail_on, np.array(full_offset)

    def unavailable(self):
        return None

    def version(self):
        return {"engine": "fake", "version": "1"}

    def as_shot(self, raw):
        return self.shot

    def render(self, raw, values, out_path, long_edge=None, bit16=False):
        if self.fail_on and self.fail_on in raw:
            raise RuntimeError("fake backend cannot read this RAW")
        self.calls.append({"raw": raw, "values": dict(values), "long_edge": long_edge, "bit16": bit16})
        w, h = FULL
        if long_edge and long_edge < max(w, h):
            s = long_edge / max(w, h)
            w, h = round(w * s), round(h * s)
        x = (values["exposure"], 1e6 / values["temp"], values["tint"])
        rgb = paint(response(x) + (self.full_offset if bit16 else 0.0), w, h)
        scale, dtype = (65535, np.uint16) if bit16 else (255, np.uint8)
        cv2.imwrite(out_path, cv2.cvtColor((rgb * scale + 0.5).astype(dtype), cv2.COLOR_RGB2BGR))


def fake_faces(faces=None):
    calls = []

    def detect(paths, landmarks=False):
        calls.append((list(paths), landmarks))
        out = {}
        for p in paths:
            h, w = cv2.imread(p, cv2.IMREAD_UNCHANGED).shape[:2]
            out[p] = {"width": w, "height": h, "faces": [SMALL_FACE, face()] if faces is None else faces,
                      "people": [], "error": None}
        return out
    detect.calls = calls
    return detect


class DevelopFrameTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rpstills-develop-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.dest = self.tmp / "develop"
        self.look = develop.load_look(TRIAL)
        patcher = mock.patch.object(develop, "SOLVE_EDGE", 400)
        patcher.start()
        self.addCleanup(patcher.stop)

    def build(self, frames, backend=None, detect=None, look=None):
        return develop.build(frames, backend or FakeBackend(), look or self.look, str(self.dest),
                             detect=detect or fake_faces(), log=lambda *_: None)

    def test_a_frame_is_solved_small_and_developed_full_size_at_16_bit(self):
        backend, detect = FakeBackend(), fake_faces()
        doc = self.build([("F1", "/raw/F1.RW2")], backend, detect)
        r = doc["frames"]["F1"]
        self.assertEqual(r["status"], "converged", r)
        self.assertTrue(r["converged"])
        for got, want in zip(r["lab_out"], TARGET):
            self.assertAlmostEqual(got, want, delta=1.0)
        self.assertLessEqual(r["delta_e_out"], 1.0)
        self.assertEqual(r["size"], list(FULL))
        a = cv2.imread(r["tif"], cv2.IMREAD_UNCHANGED)
        self.assertEqual((a.dtype, a.shape[1], a.shape[0]), (np.dtype(np.uint16), *FULL))
        self.assertEqual(os.path.basename(r["tif"]), "F1.tif")
        self.assertEqual(sorted(os.listdir(self.dest)), ["F1.tif"])  # no part file left
        solves = backend.calls[:-1]
        self.assertTrue(all(c["long_edge"] == 400 and not c["bit16"] for c in solves))
        self.assertEqual((backend.calls[-1]["long_edge"], backend.calls[-1]["bit16"]), (None, True))
        self.assertEqual(len(solves), r["evaluations"])
        self.assertEqual(len(detect.calls), 1)                 # Vision once per frame, on the first render
        self.assertEqual(detect.calls[0][1], True)

    def test_geometry_scales_from_each_render_and_not_a_fixed_width(self):
        r = self.build([("F1", "/raw/F1.RW2")])["frames"]["F1"]
        ratio = r["sample_px"]["out"] / r["sample_px"]["solve"]
        self.assertAlmostEqual(ratio, (1200 / 400) ** 2, delta=1.5)   # same patch, 9x the pixels
        self.assertAlmostEqual(r["face"]["w"], 0.2, places=4)         # the big face, not face 0

    def test_record_carries_what_the_spec_asks_for(self):
        r = self.build([("F1", "/raw/F1.RW2")], FakeBackend(shot={"temp_k": 4834.0, "tint": -14.4}))["frames"]["F1"]
        for key in ("x", "lab", "residual", "pinned", "iterations", "converged", "backend", "backend_version",
                    "look", "target", "sample", "seconds", "x0", "lab_out", "status"):
            self.assertIn(key, r)
        self.assertEqual(r["backend"], "fake")
        self.assertEqual(r["look"], {"name": "rpstills portrait cheek trial v0", "version": "0.0.1"})
        self.assertEqual(r["target"], list(TARGET))
        self.assertEqual(r["sample"], "cheek")
        self.assertEqual(r["x0"]["temp_k"], 4834)
        self.assertEqual(r["x0"]["tint"], -6.0)                       # as shot, clipped to tint_max
        self.assertEqual(set(r["x"]), {"ev", "temp_k", "mired", "tint"})
        self.assertAlmostEqual(r["x"]["temp_k"], 1e6 / r["x"]["mired"], delta=1)

    def test_a_pinned_tint_is_reported_not_hidden(self):
        lk = json.loads(TRIAL.read_text())
        lk["solve"]["target_lab"] = [64.0, 20.0, 14.0]
        path = self.tmp / "look.json"
        path.write_text(json.dumps(lk))
        doc = self.build([("F1", "/raw/F1.RW2")], look=develop.load_look(path))
        r = doc["frames"]["F1"]
        self.assertEqual((r["status"], r["converged"], r["pinned"]), ("pinned", False, ["tint"]))
        self.assertGreater(r["delta_e_out"], 2.0)
        self.assertIn("1 pinned (tint)", develop.summary(doc))
        self.assertIn("0 converged", develop.summary(doc))

    def test_a_full_size_file_that_misses_is_not_converged(self):
        # the engine renders the full-size file 3 L* brighter than its small renders
        r = self.build([("F1", "/raw/F1.RW2")], FakeBackend(full_offset=(3.0, 0.0, 0.0)))["frames"]["F1"]
        self.assertEqual(r["status_solve"], "converged")
        self.assertEqual((r["status"], r["converged"]), ("not_converged", False))
        self.assertAlmostEqual(r["lab_out"][0] - r["lab"][0], 3.0, delta=0.6)

    def test_failures_are_rows_and_the_rest_still_run(self):
        frames = [("NORAW", None), ("BAD", "/raw/BAD.RW2"), ("F1", "/raw/F1.RW2")]
        doc = self.build(frames, FakeBackend(fail_on="BAD"))
        rows = doc["frames"]
        self.assertIn("no RAW twin", rows["NORAW"]["error"])
        self.assertIn("cannot read", rows["BAD"]["error"])
        self.assertEqual(rows["BAD"]["status"], "failed")
        self.assertEqual(rows["F1"]["status"], "converged")
        self.assertIn("2 failed", develop.summary(doc))

    def test_no_face_no_landmarks_and_no_vision_fail_the_frame(self):
        r = self.build([("F1", "/raw/F1.RW2")], detect=fake_faces(faces=[]))["frames"]["F1"]
        self.assertIn("no face", r["error"])
        r = self.build([("F1", "/raw/F1.RW2")], detect=fake_faces(faces=[face(landmarks=False)]))["frames"]["F1"]
        self.assertIn("landmarks", r["error"])
        r = self.build([("F1", "/raw/F1.RW2")], detect=lambda paths, landmarks=False: None)["frames"]["F1"]
        self.assertIn("Vision helper cannot run", r["error"])
        self.assertFalse(os.path.exists(self.dest / "F1.tif"))
        self.assertNotIn("stale_tif", r)
        self.build([("F1", "/raw/F1.RW2")])
        r = self.build([("F1", "/raw/F1.RW2")], detect=fake_faces(faces=[]))["frames"]["F1"]
        self.assertIn("earlier run", r["stale_tif"])

    def test_the_box_sample_needs_no_landmarks(self):
        lk = json.loads(TRIAL.read_text())
        lk["solve"]["sample"] = "box"
        path = self.tmp / "look.json"
        path.write_text(json.dumps(lk))
        r = self.build([("F1", "/raw/F1.RW2")], detect=fake_faces(faces=[face(landmarks=False)]),
                       look=develop.load_look(path))["frames"]["F1"]
        self.assertNotIn("error", r)
        self.assertEqual(r["sample"], "box")

    def test_look_values_reach_only_a_backend_that_takes_them(self):
        lk = json.loads(TRIAL.read_text())
        lk["develop"]["values"] = {"light.contrast": 32}
        path = self.tmp / "look.json"
        path.write_text(json.dumps(lk))
        backend = FakeBackend()
        doc = self.build([("F1", "/raw/F1.RW2")], backend, look=develop.load_look(path))
        self.assertEqual(doc["look_values_ignored"], ["light.contrast"])
        self.assertNotIn("light.contrast", backend.calls[0]["values"])
        self.assertIn("ignored", develop.summary(doc))
        backend = FakeBackend()
        backend.look_values = True
        self.build([("F2", "/raw/F2.RW2")], backend, look=develop.load_look(path))
        self.assertEqual(backend.calls[0]["values"]["light.contrast"], 32)

    def test_write_keeps_rows_of_frames_not_developed_again(self):
        dest = str(self.dest)
        develop.write(dest, self.build([("A", "/raw/A.RW2")]))
        path = develop.write(dest, self.build([("B", "/raw/B.RW2")]))
        with open(path) as f:
            doc = json.load(f)
        self.assertEqual(sorted(doc["frames"]), ["A", "B"])
        self.assertEqual(doc["target"], list(TARGET))
        self.assertEqual(doc["params"]["solve"]["tint_max"], 6)


class LookAndFramesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rpstills-develop-"))
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_the_trial_look_is_labelled_and_the_look_command_ignores_its_blocks(self):
        doc = json.loads(TRIAL.read_text())
        self.assertIn("TRIAL", doc["status"])
        self.assertIn("Not a house standard", doc["status"])
        lk = develop.load_look(TRIAL)
        self.assertEqual(lk["solve"]["target_lab"], list(TARGET))
        self.assertEqual(lk["develop"]["backend"], "apple")
        self.assertEqual(look.cube_text(doc.get("params", doc), size=5), look.cube_text({}, size=5))

    def test_trial_target_reads_117_7_on_the_vectorscope(self):
        self.assertAlmostEqual(measure.vectorscope_deg(measure.lab_to_srgb(TARGET)), 117.7, delta=0.3)

    def bad(self, **change):
        doc = json.loads(TRIAL.read_text())
        for k, v in change.items():
            block, key = k.split("__")
            if v is None:
                doc[block].pop(key)
            else:
                doc[block][key] = v
        path = self.tmp / "bad.json"
        path.write_text(json.dumps(doc))
        with self.assertRaises(ValueError):
            develop.load_look(path)

    def test_a_look_that_cannot_solve_is_refused(self):
        self.bad(solve__target_lab=[64, 15])
        self.bad(solve__target_lab=None)
        self.bad(solve__sample="forehead")
        self.bad(solve__free=["ev", "exposure"])
        self.bad(solve__free=[])
        self.bad(solve__ev_bounds=[3, -3])
        self.bad(solve__temp_bounds_k=[0, 12000])
        self.bad(develop__backend="darktable")
        path = self.tmp / "nosolve.json"
        path.write_text(json.dumps({"name": "x", "params": {}}))
        with self.assertRaises(ValueError):
            develop.load_look(path)

    def index(self, rows, selects=None, cull=None):
        out = self.tmp / "out"
        out.mkdir(exist_ok=True)
        (out / "index.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        if selects is not None:
            (out / "selects.json").write_text(json.dumps({"keep": selects}))
        if cull is not None:
            (out / "cull.json").write_text(json.dumps({"proposal": [{"picks": cull}]}))
        return str(out)

    def test_frames_come_from_selects_else_the_cull_proposal(self):
        rows = [{"id": "A", "raw": "/r/A.RW2"}, {"id": "B", "raw": None}, {"id": "C", "raw": "/r/C.RW2"}]
        out = self.index(rows, selects=["C", "A"], cull=["B"])
        self.assertEqual(develop.select_frames(out), ([("C", "/r/C.RW2"), ("A", "/r/A.RW2")], "selects.json"))
        os.remove(os.path.join(out, "selects.json"))
        self.assertEqual(develop.select_frames(out), ([("B", None)], "cull proposal"))
        self.assertEqual(develop.select_frames(out, ids=["A"]), ([("A", "/r/A.RW2")], "--ids"))
        with self.assertRaisesRegex(ValueError, "not in the index"):
            develop.select_frames(out, ids=["Z"])
        os.remove(os.path.join(out, "cull.json"))
        with self.assertRaisesRegex(ValueError, "cull"):
            develop.select_frames(out)
        with self.assertRaisesRegex(ValueError, "index.jsonl"):
            develop.select_frames(str(self.tmp / "nowhere"))

    def test_explicit_raws_name_the_frames(self):
        a, b = self.tmp / "A.RW2", self.tmp / "B.RW2"
        a.write_bytes(b"raw"); b.write_bytes(b"raw")
        frames, src = develop.select_frames(str(self.tmp), raws=[str(a), str(b)])
        self.assertEqual((frames, src), ([("A", str(a)), ("B", str(b))], "--raw"))
        self.assertEqual(develop.select_frames(str(self.tmp), ids=["B"], raws=[str(a), str(b)])[0], [("B", str(b))])
        with self.assertRaisesRegex(ValueError, "not among"):
            develop.select_frames(str(self.tmp), ids=["Z"], raws=[str(a)])
        sub = self.tmp / "sub"
        sub.mkdir()
        (sub / "A.RW2").write_bytes(b"raw")
        with self.assertRaisesRegex(ValueError, "stem"):
            develop.select_frames(str(self.tmp), raws=[str(a), str(sub / "A.RW2")])
        with self.assertRaisesRegex(ValueError, "no such RAW"):
            develop.select_frames(str(self.tmp), raws=[str(self.tmp / "missing.RW2")])


def stand_in(path, info=None, fail=False):
    """An executable that logs its argv as one JSON line and writes the file
    named by path=... or the second positional argument."""
    log = str(path) + ".log"
    path.write_text(f"""#!{sys.executable}
import json, sys
argv = sys.argv[1:]
with open({log!r}, "a") as f:
    f.write(json.dumps(argv) + "\\n")
if {fail!r}:
    print("boom"); sys.exit(3)
if argv == ["--version"]:
    print("lightcraft-cli 0.0-test"); sys.exit(0)
if argv and argv[0] == "--info":
    print(json.dumps({info!r})); sys.exit(0)
out = [a[5:] for a in argv if a.startswith("path=")] or [argv[1]]
open(out[0], "wb").write(b"tif")
print(json.dumps({{"ok": True}}))
""")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path), log


def logged(log):
    with open(log) as f:
        return [json.loads(line) for line in f]


class BackendCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rpstills-develop-"))
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_apple_helper_flags_and_as_shot(self):
        exe, log = stand_in(self.tmp / "develop_raw", info={"width": 6000, "height": 4000, "temp": 4833.9,
                                                              "tint": -14.4, "orientation": 1})
        b = develop.AppleBackend(exe=exe)
        self.assertIsNone(b.unavailable())
        self.assertEqual(b.as_shot("/r/A.RW2"), {"temp_k": 4833.9, "tint": -14.4})
        out = str(self.tmp / "a.tif")
        b.render("/r/A.RW2", {"exposure": 0.5, "temp": 6000.0, "tint": -6.0, "ignored.key": 1}, out, long_edge=1500)
        b.render("/r/A.RW2", {"exposure": 0.5, "temp": 6000.0, "tint": -6.0}, out, bit16=True)
        self.assertEqual(logged(log)[1:], [
            ["/r/A.RW2", out, "--exposure", "0.5", "--temp", "6000.0", "--tint", "-6.0", "--long-edge", "1500",
             "--depth", "8"],
            ["/r/A.RW2", out, "--exposure", "0.5", "--temp", "6000.0", "--tint", "-6.0", "--depth", "16"]])
        self.assertEqual(b.version()["engine"], "CIRAWFilter")

    def test_lightcraft_is_opt_in_and_builds_the_trial_command(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(develop.ENV_LIGHTCRAFT, None)
            self.assertIn(develop.ENV_LIGHTCRAFT, develop.LightCraftBackend().unavailable())
        with mock.patch.dict(os.environ, {develop.ENV_LIGHTCRAFT: str(self.tmp / "nope")}):
            self.assertIn("not an executable", develop.LightCraftBackend().unavailable())
        exe, log = stand_in(self.tmp / "lightcraft-cli")
        with mock.patch.dict(os.environ, {develop.ENV_LIGHTCRAFT: exe}):
            b = develop.make_backend("lightcraft", profile="lc.color")
        self.assertIsNone(b.unavailable())
        self.assertEqual(b.version(), {"engine": "lightcraft", "version": "lightcraft-cli 0.0-test", "path": exe,
                                       "trial": True})
        self.assertIsNone(b.as_shot("/r/A.RW2"))
        out = str(self.tmp / "a.tif")
        values = {"light.contrast": 32, "light.exposure": 0.5, "wb.temp": 6000.0, "wb.tint": 1.5}
        b.render("/r/A.RW2", values, out, long_edge=1500)
        b.render("/r/A.RW2", values, out, bit16=True)
        head = ["run", "--import", "/r/A.RW2", "library.selectAll", "develop.profile", "id=lc.color",
                "develop.set", json.dumps({"values": values}), "app.export", f"path={out}"]
        self.assertEqual(logged(log)[1:], [head + ["longEdge=1500"], head + ["longEdge=0", "bitDepth=16"]])

    def test_a_failing_backend_raises_with_its_output(self):
        exe, _ = stand_in(self.tmp / "lightcraft-cli", fail=True)
        with self.assertRaisesRegex(RuntimeError, "boom"):
            develop.LightCraftBackend(exe=exe).render("/r/A.RW2", {}, str(self.tmp / "a.tif"))
        self.assertIsNone(develop.AppleBackend(exe=exe).as_shot("/r/A.RW2"))
        exe, _ = stand_in(self.tmp / "zero", info={"width": 0, "height": 0, "temp": 0, "tint": 0})
        self.assertIsNone(develop.AppleBackend(exe=exe).as_shot("/r/A.RW2"))  # no kelvin of 0 to divide by

    def test_unknown_backend(self):
        with self.assertRaises(ValueError):
            develop.make_backend("darktable")


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rpstills-develop-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        for p in (mock.patch.object(develop, "SOLVE_EDGE", 400),
                  mock.patch.object(develop, "make_backend", lambda name, profile=None: FakeBackend()),
                  mock.patch.object(develop.vision, "faces", fake_faces())):
            p.start()
            self.addCleanup(p.stop)

    def cli(self, *argv):
        p = argparse.ArgumentParser()
        develop.add_arguments(p)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = develop.run(p.parse_args([str(a) for a in argv]))
        return code, out.getvalue(), err.getvalue()

    def test_develop_the_selection_from_the_index(self):
        out = self.tmp / "out"
        out.mkdir()
        (out / "index.jsonl").write_text(json.dumps({"id": "F1", "raw": "/r/F1.RW2"}) + "\n")
        (out / "selects.json").write_text(json.dumps({"keep": ["F1"]}))
        code, text, err = self.cli(out, "--look", TRIAL)
        self.assertEqual(code, 0, err)
        self.assertIn("from selects.json", text)
        self.assertIn("1 frames: 1 converged", text)
        with open(out / "develop" / "calibration.json") as f:
            cal = json.load(f)
        self.assertEqual(cal["frames"]["F1"]["status"], "converged")
        self.assertTrue((out / "develop" / "F1.tif").exists())

    def test_explicit_raws_and_a_failed_frame_exit_1(self):
        raw = self.tmp / "F2.RW2"
        raw.write_bytes(b"raw")
        with mock.patch.object(develop.vision, "faces", fake_faces(faces=[])):
            code, text, err = self.cli(self.tmp / "out", "--look", TRIAL, "--raw", raw)
        self.assertEqual(code, 1)
        self.assertIn("1 failed", text)
        self.assertIn("F2", err)

    def test_refusals(self):
        code, _, err = self.cli(HERE / "never-written", "--look", TRIAL, "--raw", TRIAL)
        self.assertEqual(code, 1)
        self.assertIn("git working tree", err)
        self.assertFalse((HERE / "never-written").exists())
        code, _, err = self.cli(self.tmp / "out", "--look", self.tmp / "missing.json")
        self.assertEqual((code, "look" in err), (1, True))
        lk = json.loads(TRIAL.read_text())
        lk["solve"]["tint_max"] = "six"
        (self.tmp / "typo.json").write_text(json.dumps(lk))
        code, _, err = self.cli(self.tmp / "out", "--look", self.tmp / "typo.json")
        self.assertEqual((code, "look" in err), (1, True))
        code, _, err = self.cli(self.tmp / "out", "--look", TRIAL)
        self.assertEqual(code, 1)
        self.assertIn("index.jsonl", err)


if __name__ == "__main__":
    unittest.main()
