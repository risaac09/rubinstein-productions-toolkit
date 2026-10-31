"""rpstills.develop: RAW files to 16-bit TIFFs, neutralised on a neutral reference
with exposure set on the cheek, or solved onto one skin target.

    python3 -m rpstills develop <out dir> --look FILE [--ids ID ...]
                                [--backend apple|lightcraft] [--raw PATH ...]
                                [--neutral-box X0 Y0 X1 Y1] [--wb-from ID]

The look's solve.mode picks what the three settings are solved onto:
  neutral  the house rule for delivery: the cheek's L* onto target_L, and the
           median a*, b* of a neutral reference onto neutral_ab (0, 0). The
           reference is a white or grey garment or a grey card below the face
           (skin.neutral_mask, taken once on the first render and carried to
           the full-size file), or --neutral-box in every frame solved on its
           own neutral (with --wb-from, the reference frame only). Skin hue is
           left where the complexion puts it. A frame with no neutral fails
           (NO_NEUTRAL) unless --wb-from names a frame in the same light: that
           frame is solved first, and every other frame takes its mired and
           tint and solves EV alone onto the cheek L* (status on L* only); a
           reference that fails fails them too. The reference may also be a
           converged neutral row of calibration.json in the same develop/
           folder, from the same backend; a stored row that is not one stops
           the run before anything is developed.
  target   (the default) the cheek's whole Lab onto target_lab, as below:
           for matching a set to one hero frame.

Frames are the selection (selects.json beside the index, else the cull
proposal, as rpstills.crops reads it) or --ids, with RAWs from the index
rows' `raw` field; or explicit --raw files (the id is the file stem). Each
frame is developed small (SOLVE_EDGE px on the long edge) at a starting
point, Vision finds the largest face with landmarks on that render, and a
damped Newton solve moves three develop settings until the mean CIELAB of
the skin sample (measure.skin_lab: the cheek apples, or the box sample)
lands on the look's target:

    ev      exposure, stops                       drives L*
    mired   white balance, 1e6 / kelvin           drives b*
    tint    white balance tint, backend units     drives a*

The Jacobian is measured by finite differences at the start (FD_STEP per
axis) and updated by Broyden's rule after every step, so it follows the
response as the point moves; one Jacobian measured at 5000 K is wrong at
9000 K. A step is scaled as a whole to stay within MAX_STEP; a step that
makes the residual worse is undone and the next one halved. An axis whose
step would leave its bounds lands exactly on the bound; when a later step
still pushes it out, it is pinned, whether or not that step helped the
others: it leaves the free set and the others solve on their own
components (tint pinned at the backend's tint_max leaves ev and mired solving
L* and b*, and a* falls where it does). A start clipped onto a bound is
held on the first step, not pinned, so the pin is decided on the updated
Jacobian. An axis with no room (tint_max 0) is pinned from the start, and
the finite-difference probe stays inside the bounds. A render that cannot
be measured (Unmeasurable: the neutral clipped at every pixel) turns the
probe the other way, or counts as a step that made things worse. The
solve stops when the residual over the free axes' components is within
AIM (CIELAB units; half of TOL, a margin for the full-size file) or after
MAX_ITER steps.
Status per frame, judged on TOL:
    converged      every requested axis met the target (within TOL)
    pinned         the free axes met it, but an axis sits on a bound and
                   the full target is missed (delta_e says by how much)
    not_converged  MAX_ITER ran out; the TIFF is the best point found
The frame is then developed at full size, 16-bit, at the solved point, and
the sample is measured again on that file (lab_out). A frame's status is
the worse of the solve render's (status_solve) and the delivered file's,
so a file that misses the target is never reported as converged. Only
"converged" sets converged: true. The exit status is 1 when any frame is
not converged (failed, pinned or not_converged), and stderr names them.
Geometry scales from each render's own size
(the face is normalized), never from a fixed width.

Backends, behind Backend.render(raw, values, out, long_edge=None, bit16=False):
  apple       the default, macOS: develop_raw.swift on CIRAWFilter (exposure,
              neutral temperature, tint), compiled once into
              ~/Library/Caches/rpstills as rpstills.vision does. Look values
              do not apply to it and are listed as ignored.
  lightcraft  opt-in: lightcraft-cli from $RPSTILLS_LIGHTCRAFT. The look's
              develop values are LightCraft control ids, the profile a
              LightCraft profile id. The start is its as-shot white balance
              (develop.wb mode=asShot).
Tint is in the backend's own units, so a tint bound set on one backend is
not the same cast on the other. So is temperature: on a RAW LightCraft has
no Kelvin scale for, wb.temp is relative (6500 = as shot), so its rows
carry "temp" and temp_unit instead of "temp_k", and temp_bounds_k bounds
that relative value. temp_bounds_k is Kelvin only on the apple backend.
So solve.tint_max is per backend, {"apple": 50, "lightcraft": 6}, and the
solve reads the active backend's (a backend the look leaves out takes
TINT_MAX's). A plain number, the old form, bounds every backend alike.

Look files gain two blocks (rpstills look ignores both):
  "develop": {"backend": "apple"|"lightcraft", "profile": ..., "values": {...}}
  "solve":   {"mode": "target"|"neutral", "target_lab": [L, a, b],
              "target_L": 64, "neutral_ab": [0, 0],
              "neutral": {"min_L": 40, "max_chroma": 25, "min_share": 0.002},
              "sample": "cheek"|"box",
              "free": ["ev", "mired", "tint"],
              "tint_max": {"apple": 50, "lightcraft": 6},
              "ev_bounds": [-3, 3], "temp_bounds_k": [2500, 12000]}
target_lab is needed in target mode; in neutral mode target_L defaults to
target_lab[0], else TARGET_L.

Writes <out>/develop/<id>.tif (16-bit sRGB) and <out>/develop/calibration.json:
per frame x, x0, lab, lab_out (the cheek), residual, delta_e, pinned,
iterations, converged, status, mode, target (a* and b* null on a --wb-from
frame), neutral (source auto|box|wb_from, n_px, lab_out, box, from; null in
target mode), backend and its version, look name and version, sample,
temp_unit, the params it was solved under (tint_max as the active backend's
bound), seconds. A frame
developed before keeps its row (and its params) unless developed
again. TIFFs and calibration.json name client frames: refused inside any
git working tree.
"""

import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time

import cv2
import numpy as np

from . import crops, index, measure, skin, vision
from .audit import read_rgb

HELPER_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "develop_raw.swift")
ENV_LIGHTCRAFT = "RPSTILLS_LIGHTCRAFT"
BACKENDS = ("apple", "lightcraft")
RENDER_TIMEOUT_S = 600

AXES = ("ev", "mired", "tint")
LAB_OF = (0, 2, 1)                     # the Lab component each axis drives: ev -> L*, mired -> b*, tint -> a*
FD_STEP = np.array([0.25, 10.0, 4.0])  # finite-difference step per axis; also the scale of the Broyden update
MAX_STEP = np.array([1.5, 50.0, 10.0])
MAX_ITER = 6
TOL = 1.0                              # CIELAB units over the free axes' components: what a status is judged on
AIM = 0.5                              # where a develop solve stops: margin for the full-size file (0.45 off seen)
MIN_DAMP = 0.125
SOLVE_EDGE = 1500                      # long edge of the solve renders, px
START_TEMP_K = 5500.0                  # first white balance when the backend reports no as-shot value
MODES = ("target", "neutral")
# Tint bound per backend, in its own units. lightcraft 6: set on LightCraft, where tint 0 sits near neutral.
# apple 50: CIRAWFilter documents no tint range, so it comes from the RW2 test frames on apple: as-shot tint
# -14.4, white shirts neutral near -31, the trial skin target -29 to beyond -40 (rpstills-develop.md, Known limits).
TINT_MAX = {"apple": 50.0, "lightcraft": 6.0}
SOLVE_DEFAULTS = {"mode": "target", "sample": "cheek", "free": list(AXES), "tint_max": TINT_MAX,
                  "ev_bounds": [-3.0, 3.0], "temp_bounds_k": [2500.0, 12000.0]}
TARGET_L = 64.0                        # neutral mode's cheek L* when the look names neither target_L nor target_lab
NEUTRAL_DEFAULTS = {"min_L": skin.NEUTRAL_MIN_L, "max_chroma": skin.NEUTRAL_MAX_CHROMA,
                    "min_share": skin.NEUTRAL_MIN_SHARE}
NO_NEUTRAL = ("no neutral reference in frame: shoot a grey card, pass --neutral-box, or --wb-from ID "
              "(a frame in the same light)")


class Unmeasurable(ValueError):
    """measure_fn cannot read this point (the neutral clipped at every pixel): solve() probes the other
    way, or takes it as a step that made things worse."""


# ---------------------------------------------------------------- the solve

def _residual(y, target, axes):
    rows = [LAB_OF[i] for i in axes]
    return float(np.linalg.norm((np.asarray(target) - y)[rows])) if rows else 0.0


def status(lab, target, requested, free, pinned, tol=TOL):
    """converged, pinned or not_converged for a measured Lab; requested,
    free and pinned are axis names (see the module docstring)."""
    lab = np.asarray(lab, float)
    if _residual(lab, target, [AXES.index(a) for a in requested]) <= tol:
        return "converged"
    if pinned and _residual(lab, target, [AXES.index(a) for a in free]) <= tol:
        return "pinned"
    return "not_converged"


def _step(J, r, x, free, lo, hi, damp):
    """Damped Newton step over the free axes, scaled as a whole (so the
    direction holds) to stay within MAX_STEP. An axis that would leave its
    bounds is held on the bound and the others are solved again with that
    move fixed. Returns (dx, axes that already sat on the bound the step
    pushes against: the pinned ones)."""
    held = {}
    while True:
        dx = np.zeros(3)
        for i, v in held.items():
            dx[i] = v
        idx = [i for i in free if i not in held]
        if idx:
            rows = [LAB_OF[i] for i in idx]
            rhs = r[rows] - J[rows] @ dx
            step = np.linalg.lstsq(J[np.ix_(rows, idx)], rhs, rcond=None)[0] * damp
            dx[idx] = step * min(1.0, float(np.min(MAX_STEP[idx] / np.maximum(np.abs(step), 1e-12))))
        out = [i for i in idx if not lo[i] <= x[i] + dx[i] <= hi[i]]
        if not out:
            return dx, sorted(i for i, v in held.items() if v == 0.0)
        for i in out:
            held[i] = float(np.clip(x[i] + dx[i], lo[i], hi[i]) - x[i])


def solve(measure_fn, x0, bounds, target, free, max_iter=MAX_ITER, tol=TOL):
    """Solve measure_fn(x) = target for x = (ev, mired, tint).

    measure_fn maps x to CIELAB (3,); bounds is [(lo, hi)] per axis; free
    names the axes that may move (the rest stay at x0, clipped). Returns
    {"x", "lab", "residual" (over the axes still free), "delta_e" (all of
    L*a*b*), "pinned", "free", "iterations", "evaluations", "converged",
    "status"}; see the module docstring for the statuses."""
    unknown = set(free) - set(AXES)
    if unknown or not free:
        raise ValueError(f"free axes must be a non-empty subset of {AXES}, not {list(free)}")
    lo, hi = (np.array([b[k] for b in bounds], float) for k in (0, 1))
    target = np.asarray(target, float)
    requested = [i for i, a in enumerate(AXES) if a in free]
    pinned = [AXES[i] for i in requested if lo[i] >= hi[i]]  # no room to move (tint_max 0): pinned from the start
    active = [i for i in requested if lo[i] < hi[i]]
    x = np.clip(np.asarray(x0, float), lo, hi)
    y = np.asarray(measure_fn(x), float)
    evals = 1
    J = np.zeros((3, 3))
    for i in active:
        up, down = hi[i] - x[i], x[i] - lo[i]  # the probe stays inside the bounds, however narrow they are
        h = min(FD_STEP[i], up) if up >= min(FD_STEP[i], down) else -min(FD_STEP[i], down)
        xi = x.copy()
        xi[i] += h
        evals += 1
        try:
            yi = measure_fn(xi)
        except Unmeasurable:  # a white shirt clipped by +0.25 EV: probe down instead, when there is room
            if h < 0 or down <= 0:
                raise
            h = -min(FD_STEP[i], down)
            xi[i] = x[i] + h
            evals += 1
            yi = measure_fn(xi)
        J[:, i] = (np.asarray(yi, float) - y) / h
    damp, it = 1.0, 0
    while active and _residual(y, target, active) > tol and it < max_iter:
        it += 1
        dx, pins = _step(J, target - y, x, active, lo, hi, damp)
        if it == 1:
            pins = []  # a start on a bound (a clipped x0) is held, not pinned: the first Jacobian is the stale one
        x_new = x + dx
        x_new = np.where(np.abs(x_new - lo) < 1e-9, lo, np.where(np.abs(x_new - hi) < 1e-9, hi, x_new))  # land exactly
        dx = x_new - x
        evals += 1
        try:
            y_new = np.asarray(measure_fn(x_new), float)
        except Unmeasurable:
            damp = max(MIN_DAMP, damp / 2)  # as a step that made things worse
            continue
        w = dx / FD_STEP ** 2  # Broyden's update in step-scaled units, so mired does not swamp ev
        if dx @ w > 0:
            J += np.outer(y_new - y - J @ dx, w) / (dx @ w)
        # a pin is x on the bound and the step pushing out, whether or not the step helped the others
        pinned += [AXES[i] for i in pins]
        active = [i for i in active if i not in pins]
        if active and _residual(y_new, target, active) > _residual(y, target, active):
            damp = max(MIN_DAMP, damp / 2)  # worse: stay, and try a shorter step on the updated Jacobian
            continue
        x, y, damp = x_new, y_new, 1.0
    names = [AXES[i] for i in active]
    st = status(y, target, [AXES[i] for i in requested], names, pinned, tol)
    return {"x": x.tolist(), "lab": y.tolist(), "residual": _residual(y, target, active),
            "delta_e": float(np.linalg.norm(target - y)), "pinned": pinned, "free": names,
            "iterations": it, "evaluations": evals, "converged": st == "converged", "status": st}


# ---------------------------------------------------------------- backends

def helper():
    """Path of the compiled develop_raw helper, compiling it on first use;
    None when it cannot be built (no swiftc, not macOS)."""
    try:
        with open(HELPER_SOURCE, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError:
        return None
    path = os.path.join(vision.CACHE_DIR, f"develop_raw-{digest}")
    if os.access(path, os.X_OK):
        return path
    if not os.access(vision.SWIFTC, os.X_OK):
        return None
    os.makedirs(vision.CACHE_DIR, exist_ok=True)
    proc = subprocess.run([vision.SWIFTC, "-O", HELPER_SOURCE, "-o", path], stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return path if proc.returncode == 0 and os.access(path, os.X_OK) else None


def _run(cmd):
    """stdout of cmd; RuntimeError with its last output when it fails."""
    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              encoding="utf-8", errors="replace", timeout=RENDER_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{os.path.basename(cmd[0])} took over {RENDER_TIMEOUT_S} s")
    except OSError as e:  # a directory, or a binary gone since the check
        raise RuntimeError(f"{cmd[0]} cannot run: {e}")
    if proc.returncode != 0:
        raise RuntimeError(f"{os.path.basename(cmd[0])} failed: {(proc.stdout + proc.stderr).strip()[-400:]}")
    return proc.stdout


class Backend:
    """A RAW developer. axes are its control ids for (exposure EV, temperature,
    tint); look_values says whether a look's develop values reach it.
    temp_key names the temperature in rows ("temp_k" only where it is
    Kelvin) and temp_unit says what it is."""
    name = None
    axes = (None, None, None)
    look_values = False
    temp_key, temp_unit = "temp_k", "K"

    def unavailable(self):
        """Why it cannot run, or None."""
        raise NotImplementedError

    def version(self):
        raise NotImplementedError

    def as_shot(self, raw):
        """{temp_key, "tint"} the RAW was shot at, or None when unknown."""
        return None

    def render(self, raw, values, out_path, long_edge=None, bit16=False):
        """Develop raw with values (control id -> value) into out_path (.tif).
        long_edge scales down to that many px; bit16 asks for 16-bit."""
        raise NotImplementedError


class AppleBackend(Backend):
    name = "apple"
    axes = ("exposure", "temp", "tint")  # the helper's flags: CIRAWFilter exposure, neutralTemperature, neutralTint

    def __init__(self, exe=None):
        self.exe = exe or helper()

    def unavailable(self):
        return None if self.exe else "the develop_raw helper cannot be built (it needs macOS and swiftc)"

    def version(self):
        return {"engine": "CIRAWFilter", "macos": platform.mac_ver()[0] or None, "helper": os.path.basename(self.exe)}

    def as_shot(self, raw):
        try:
            doc = json.loads(_run([self.exe, "--info", raw]).strip().splitlines()[-1])
            shot = {"temp_k": float(doc["temp"]), "tint": float(doc["tint"])}
        except (RuntimeError, ValueError, KeyError, IndexError):
            return None
        return shot if shot["temp_k"] > 0 else None

    def render(self, raw, values, out_path, long_edge=None, bit16=False):
        cmd = [self.exe, raw, out_path]
        for key in self.axes:
            if key in values:
                cmd += [f"--{key}", repr(float(values[key]))]
        if long_edge:
            cmd += ["--long-edge", str(int(long_edge))]
        _run(cmd + ["--depth", "16" if bit16 else "8"])


class LightCraftBackend(Backend):
    name = "lightcraft"
    axes = ("light.exposure", "wb.temp", "wb.tint")
    look_values = True
    # on a RAW LightCraft has no Kelvin scale for (the test RW2 files), wb.temp is relative: 6500 is as shot
    temp_key, temp_unit = "temp", "lightcraft wb.temp: Kelvin only where LightCraft has a Kelvin scale " \
                                  "for the camera, else relative (6500 = as shot)"

    def __init__(self, profile=None, exe=None):
        self.exe = exe or os.environ.get(ENV_LIGHTCRAFT)
        self.profile = profile

    def unavailable(self):
        if not self.exe:
            return f"the lightcraft backend is opt-in: set {ENV_LIGHTCRAFT} to a lightcraft-cli binary"
        if not (os.path.isfile(self.exe) and os.access(self.exe, os.X_OK)):
            return f"{ENV_LIGHTCRAFT}={self.exe} is not an executable"
        return None

    def as_shot(self, raw):
        try:
            for line in _run([self.exe, "run", "--import", raw, "library.selectAll", "develop.wb",
                              "mode=asShot"]).splitlines():
                doc = json.loads(line)
                if doc.get("command") == "develop.wb":
                    shot = {"temp": float(doc["result"]["temp"]), "tint": float(doc["result"]["tint"])}
                    return shot if shot["temp"] > 0 else None
        except (RuntimeError, ValueError, KeyError, TypeError, AttributeError):
            return None
        return None

    def version(self):
        try:
            v = _run([self.exe, "--version"]).strip() or None
        except RuntimeError:
            v = None
        return {"engine": "lightcraft", "version": v, "path": self.exe, "trial": True}

    def render(self, raw, values, out_path, long_edge=None, bit16=False):
        cmd = [self.exe, "run", "--import", raw, "library.selectAll"]
        if self.profile:
            cmd += ["develop.profile", f"id={self.profile}"]
        # export defaults to 3000 px; longEdge=0 is full size
        cmd += ["develop.set", json.dumps({"values": values}), "app.export", f"path={out_path}",
                f"longEdge={int(long_edge or 0)}"]
        _run(cmd + (["bitDepth=16"] if bit16 else []))
        if not os.path.exists(out_path):
            raise RuntimeError(f"lightcraft-cli reported success but wrote no {os.path.basename(out_path)}")


def make_backend(name, profile=None):
    if name == "apple":
        return AppleBackend()
    if name == "lightcraft":
        return LightCraftBackend(profile)
    raise ValueError(f"unknown backend {name!r}; known: {', '.join(BACKENDS)}")


# ---------------------------------------------------------------- looks and frames

def load_look(path):
    """The look document with "develop" and "solve" checked and filled with
    defaults. ValueError when the solve block cannot run."""
    with open(path) as f:
        doc = json.load(f)
    dev = dict({"backend": "apple", "profile": None, "values": {}}, **(doc.get("develop") or {}))
    if dev["backend"] not in BACKENDS:
        raise ValueError(f"develop.backend must be one of {BACKENDS}, not {dev['backend']!r}")
    if not isinstance(dev["values"], dict):
        raise ValueError("develop.values must be an object of control id -> value")
    if not isinstance(doc.get("solve"), dict):
        raise ValueError("the look has no solve block (target_lab, sample, free, bounds)")
    s = dict(SOLVE_DEFAULTS, **doc["solve"])
    if s["mode"] not in MODES:
        raise ValueError(f"solve.mode is target or neutral, not {s['mode']!r}")

    def numbers(v, n):
        return isinstance(v, list) and len(v) == n and all(isinstance(x, (int, float)) for x in v)
    t = s.get("target_lab")
    if (s["mode"] == "target" or t is not None) and not numbers(t, 3):
        raise ValueError("solve.target_lab must be [L*, a*, b*]")
    if s["mode"] == "neutral":
        s.setdefault("target_L", t[0] if t else TARGET_L)
        s.setdefault("neutral_ab", [0.0, 0.0])
        n = s.get("neutral") or {}
        if not isinstance(n, dict) or set(n) - set(NEUTRAL_DEFAULTS):
            raise ValueError(f"solve.neutral takes {', '.join(NEUTRAL_DEFAULTS)}")
        s["neutral"] = dict(NEUTRAL_DEFAULTS, **n)
        if not (numbers([s["target_L"]], 1) and numbers(s["neutral_ab"], 2)
                and numbers(list(s["neutral"].values()), 3)):
            raise ValueError("solve.target_L must be a number, neutral_ab [a*, b*], neutral's values numbers")
    if s["sample"] not in ("cheek", "box"):
        raise ValueError(f"solve.sample is cheek or box, not {s['sample']!r}")
    if not s["free"] or set(s["free"]) - set(AXES):
        raise ValueError(f"solve.free is a non-empty subset of {list(AXES)}")
    t = doc["solve"].get("tint_max", {})
    if isinstance(t, dict):
        if set(t) - set(BACKENDS):
            raise ValueError(f"solve.tint_max names unknown backends {sorted(set(t) - set(BACKENDS))}; "
                             f"known: {', '.join(BACKENDS)}")
        t = dict(TINT_MAX, **t)
    s["tint_max"] = t
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0
               for v in (t.values() if isinstance(t, dict) else [t])):
        raise ValueError("solve.tint_max is a number >= 0, or {backend: number >= 0}")
    (e0, e1), (k0, k1) = s["ev_bounds"], s["temp_bounds_k"]
    if not (e0 < e1 and 0 < k0 < k1):
        raise ValueError("solve bounds need ev_bounds lo < hi, 0 < temp_bounds_k lo < hi")
    return dict(doc, develop=dev, solve=s)


def tint_max(s, backend):
    """The tint bound a checked solve block sets on this backend: its entry
    of the per-backend tint_max, or the plain number (the old form)."""
    t = s["tint_max"]
    if not isinstance(t, dict):
        return float(t)
    if backend not in t:
        raise ValueError(f"solve.tint_max has no bound for backend {backend!r}")
    return float(t[backend])


def target_of(s):
    """The Lab a solve block aims at: target_lab, or in neutral mode
    [cheek L*, neutral a*, neutral b*]."""
    return [float(v) for v in (s["target_lab"] if s["mode"] == "target" else [s["target_L"], *s["neutral_ab"]])]


def select_frames(out_dir, ids=None, raws=None):
    """([(id, raw path or None)], source). Explicit RAWs win (id = file
    stem; --ids then picks among them); else --ids or the selection, with
    RAWs from the index. ValueError when the frames cannot be named."""
    if raws:
        frames = [(os.path.splitext(os.path.basename(p))[0], os.path.abspath(p)) for p in raws]
        stems = [fid for fid, _ in frames]
        if len(set(stems)) != len(stems):
            raise ValueError("two --raw files share a file stem; develop them in separate runs")
        missing = [p for _, p in frames if not os.path.isfile(p)]
        if missing:
            raise ValueError(f"no such RAW file: {missing[0]}")
        if ids:
            unknown = [i for i in ids if i not in stems]
            if unknown:
                raise ValueError(f"--ids not among the --raw stems: {unknown[:5]}")
            frames = [f for f in frames if f[0] in ids]
        return frames, "--raw"
    try:
        rows = index.load(out_dir)
    except FileNotFoundError:
        raise ValueError(f"no index.jsonl in {out_dir}: run rpstills index, or pass --raw")
    source = "--ids"
    if not ids:
        cull_path = os.path.join(out_dir, "cull.json")
        if not os.path.exists(os.path.join(out_dir, "selects.json")) and not os.path.exists(cull_path):
            raise ValueError(f"no selects.json or cull.json in {out_dir}: run rpstills cull, or pass --ids")
        cull_doc = {"proposal": []}
        if os.path.exists(cull_path):
            with open(cull_path) as f:
                cull_doc = json.load(f)
        ids, source = crops.selection(out_dir, cull_doc)
    by_id = {r["id"]: r for r in rows}
    missing = [fid for fid in ids if fid not in by_id]
    if missing:
        raise ValueError(f"{len(missing)} ids are not in the index (first: {missing[:5]})")
    if not ids:
        raise ValueError(f"the selection ({source}) is empty")
    return [(fid, by_id[fid].get("raw")) for fid in ids], source


# ---------------------------------------------------------------- one frame

def _face(path, rgb, detect, sample):
    """The largest face Vision finds on a render (normalized)."""
    det = detect([path], landmarks=True)
    if det is None:
        raise RuntimeError("the Vision helper cannot run (it needs macOS and swiftc)")
    entry = det.get(path) or {}
    if entry.get("error"):
        raise ValueError(f"Vision: {entry['error']}")
    if (entry.get("width"), entry.get("height")) != (rgb.shape[1], rgb.shape[0]):
        raise ValueError(f"Vision saw {entry.get('width')}x{entry.get('height')}, "
                         f"the render is {rgb.shape[1]}x{rgb.shape[0]}")
    faces = entry.get("faces") or []
    if not faces:
        raise ValueError("no face on the develop render")
    face = max(faces, key=lambda f: f["w"] * f["h"])
    if sample == "cheek" and not all((face.get("landmarks") or {}).get(k)
                                     for k in ("leftEye", "rightEye", "outerLips")):
        raise ValueError("Vision gave no eye and lip landmarks; the cheek sample needs them")
    return face


def _lab(rgb, face, sample):
    s = measure.skin_lab(rgb, skin.to_pixels(face, rgb.shape[1], rgb.shape[0]), sample)
    return np.array([s["L"], s["a"], s["b"]]), s["n_px"]


def _neutral_mask(rgb, face, box, params):
    """bool HxW: the --neutral-box (normalised x0, y0, x1, y1), else the
    garment or card below the face. ValueError NO_NEUTRAL when none is found."""
    H, W = rgb.shape[:2]
    if box:
        m = np.zeros((H, W), bool)
        m[int(round(box[1] * H)):int(round(box[3] * H)), int(round(box[0] * W)):int(round(box[2] * W))] = True
        if not m.any():
            raise ValueError(f"the --neutral-box covers no pixel at {W}x{H}")
        return m
    # no retry on a provisional balance: grey-world takes a blue or red shirt for a neutral (tested); fail loud instead
    m = skin.neutral_mask(rgb, skin.to_pixels(face, W, H), **params)
    if m is None:
        raise ValueError(NO_NEUTRAL)
    return m


def _neutral(rgb, mask):
    """measure.neutral_lab over mask; None when there is no mask (--wb-from)."""
    if mask is None:
        return None
    n = measure.neutral_lab(rgb, mask)
    if n is None:
        raise Unmeasurable("the neutral reference is clipped at every pixel")
    return n


def _vector(mode, cheek, neutral, target):
    """What the solve compares with target: the cheek Lab (target mode), or
    the cheek L* with the neutral's a*, b*. Without a neutral (--wb-from)
    only L* is asked for, and a*, b* stand at the target so delta_e is L*'s."""
    if mode == "target":
        return cheek
    return np.array([cheek[0], *((neutral["a"], neutral["b"]) if neutral else target[1:])])


def _x_out(x, temp_key="temp_k"):
    ev, mired, tint = (float(v) for v in x)
    return {"ev": round(ev, 3), temp_key: round(1e6 / mired), "mired": round(mired, 2), "tint": round(tint, 2)}


def develop_frame(fid, raw, backend, look, dest, work, detect, neutral_box=None, wb=None):
    """Solve and develop one frame into dest/<id>.tif. Returns its row;
    ValueError or RuntimeError when it cannot be developed. In neutral mode
    neutral_box (normalised x0, y0, x1, y1) replaces the garment below the
    face, and wb ({"mired", "tint", "from"}, a --wb-from frame's) holds the
    white balance and solves EV alone onto the cheek L*."""
    s, dev = look["solve"], look["develop"]
    mode, target = s["mode"], target_of(s)
    free = ["ev"] if wb else s["free"]
    t = tint_max(s, backend.name)
    bounds = [tuple(s["ev_bounds"]), (1e6 / s["temp_bounds_k"][1], 1e6 / s["temp_bounds_k"][0]), (-t, t)]
    shot = backend.as_shot(raw)
    x0 = [0.0, 1e6 / (shot[backend.temp_key] if shot else START_TEMP_K), shot["tint"] if shot else 0.0]
    if wb:  # held exactly, never clipped to this look's bounds
        x0[1:] = wb["mired"], wb["tint"]
        bounds[1:] = [(wb["mired"],) * 2, (wb["tint"],) * 2]
    base = dict(dev["values"]) if backend.look_values else {}

    def values(x):
        ev, mired, tint = (float(v) for v in x)
        return dict(base, **dict(zip(backend.axes, (round(ev, 4), round(1e6 / mired, 1), round(tint, 3)))))

    state = {"face": None, "mask": None, "n": 0, "n_px": None, "seen": {}}

    def measure_fn(x):
        state["n"] += 1
        path = os.path.join(work, f"{fid}-{state['n']}.tif")
        backend.render(raw, values(x), path, long_edge=SOLVE_EDGE)
        rgb = read_rgb(path)
        if state["face"] is None:
            state["face"] = _face(path, rgb, detect, s["sample"])
            if mode == "neutral" and not wb:  # taken once, at the start's white balance
                state["mask"] = _neutral_mask(rgb, state["face"], neutral_box, s["neutral"])
        os.remove(path)
        lab, state["n_px"] = _lab(rgb, state["face"], s["sample"])
        neutral = _neutral(rgb, state["mask"])
        state["seen"][tuple(float(v) for v in x)] = lab
        return _vector(mode, lab, neutral, target)

    result = solve(measure_fn, x0, bounds, target, free, tol=AIM)
    seen = state["seen"]  # the solve returns a point it measured; the nearest guards a float that drifted by an ulp
    lab = seen[min(seen, key=lambda k: float(np.abs(np.subtract(k, result["x"])).sum()))]
    final = os.path.join(dest, f".{fid}.part.tif")
    try:
        backend.render(raw, values(result["x"]), final, bit16=True)
        rgb = read_rgb(final)
        lab_out, n_out = _lab(rgb, state["face"], s["sample"])
        mask = state["mask"]
        if mask is not None:  # carried to the full size, never taken again at the solved white balance
            mask = cv2.resize(mask.astype(np.uint8), (rgb.shape[1], rgb.shape[0]), interpolation=cv2.INTER_NEAREST) > 0
        neutral_out = _neutral(rgb, mask)
        tif = os.path.join(dest, f"{fid}.tif")
        os.replace(final, tif)
    finally:
        if os.path.exists(final):
            os.remove(final)
    # the delivered file decides: a solve that met the target small but misses it full size is not converged
    vec_out = _vector(mode, lab_out, neutral_out, target)
    st_solve, st_out = (status(v, target, free, result["free"], result["pinned"]) for v in (result["lab"], vec_out))
    st = max(st_solve, st_out, key=("converged", "pinned", "not_converged").index)
    face = state["face"]
    neutral = None if mode == "target" else {
        "source": "wb_from" if wb else "box" if neutral_box else "auto",
        "n_px": neutral_out and neutral_out["n_px"],
        "lab_out": neutral_out and [neutral_out[k] for k in ("L", "a", "b")],
        "box": None if wb or not neutral_box else [float(v) for v in neutral_box],
        "from": wb["from"] if wb else None}
    return {"id": fid, "raw": raw, "tif": tif, "size": [rgb.shape[1], rgb.shape[0]],
            "x": _x_out(result["x"], backend.temp_key), "x0": _x_out(np.clip(x0, *zip(*bounds)), backend.temp_key),
            "as_shot": shot, "lab": [round(float(v), 2) for v in lab],
            "lab_out": [round(float(v), 2) for v in lab_out], "residual": round(result["residual"], 3),
            "delta_e": round(result["delta_e"], 3),
            "delta_e_out": round(float(np.linalg.norm(vec_out - np.asarray(target))), 3),
            "pinned": result["pinned"], "free": result["free"], "iterations": result["iterations"],
            "evaluations": result["evaluations"], "converged": st == "converged", "status": st,
            "status_solve": st_solve, "sample_px": {"solve": state["n_px"], "out": n_out},
            "face": {k: round(float(face[k]), 4) for k in ("x", "y", "w", "h")},
            "mode": mode, "target": [target[0], None, None] if wb else target, "neutral": neutral}


# ---------------------------------------------------------------- the run

def wb_reference(row, fid, backend):
    """{"mired", "tint", "from"}: the white balance a --wb-from frame's row
    hands on. ValueError unless the row is a converged neutral-mode solve
    on this backend (tint and temperature are in each backend's own units)."""
    if not row:
        raise ValueError(f"--wb-from {fid}: not among this run's frames and no row in calibration.json")
    if (row.get("mode"), row.get("status"), row.get("backend")) != ("neutral", "converged", backend):
        raise ValueError(f"--wb-from {fid}: not a solved neutral to take a white balance from (mode "
                         f"{row.get('mode')}, status {row.get('status')}, backend {row.get('backend')}; "
                         f"wanted neutral, converged, {backend})")
    return {"mired": float(row["x"]["mired"]), "tint": float(row["x"]["tint"]), "from": fid}


def _rows_on_disk(dest):
    """The frames of dest/calibration.json; {} when there is none or it cannot be read."""
    try:
        with open(os.path.join(dest, "calibration.json")) as f:
            return json.load(f).get("frames") or {}
    except (OSError, ValueError):
        return {}


def build(frames, backend, look, dest, detect=None, log=print, neutral_box=None, wb_from=None):
    """Develop every (id, raw) into dest. Returns the calibration document;
    a frame that fails is a row with its error, the others still run.
    wb_from (neutral mode) is developed first when it is among the frames
    (when it fails, the frames that need it fail with that reason), else
    read from dest/calibration.json; ValueError, before anything is
    developed, when it is in neither or its stored row is not a converged
    neutral solve on this backend."""
    detect = detect or vision.faces
    s, dev = look["solve"], look["develop"]
    neutral_keys = ("target_L", "neutral_ab", "neutral") if s["mode"] == "neutral" else ()
    # every row carries the params it was solved under: a later run that merges into the file replaces the top block
    common = {"backend": backend.name, "backend_version": backend.version(),
              "look": {"name": look.get("name"), "version": look.get("version")}, "mode": s["mode"],
              "target": target_of(s), "sample": s["sample"], "temp_unit": backend.temp_unit,
              "params": {"solve": dict({k: s[k] for k in (*SOLVE_DEFAULTS, *neutral_keys)},
                                   tint_max=tint_max(s, backend.name)), "fd_step": FD_STEP.tolist(),
                         "max_step": MAX_STEP.tolist(), "max_iter": MAX_ITER, "tol": TOL, "aim": AIM,
                         "min_damp": MIN_DAMP, "solve_edge": SOLVE_EDGE, "start_temp_k": START_TEMP_K,
                         "profile": dev.get("profile")}}
    ignored = sorted(dev["values"]) if dev["values"] and not backend.look_values else []
    wb = None
    if wb_from and wb_from not in dict(frames):
        wb = wb_reference(_rows_on_disk(dest).get(wb_from), wb_from, backend.name)
    if wb_from:
        frames = sorted(frames, key=lambda f: f[0] != wb_from)  # the reference first, the rest in order
    os.makedirs(dest, exist_ok=True)
    rows = {}
    with tempfile.TemporaryDirectory(prefix="rpstills-develop-") as work:
        for fid, raw in frames:
            t0 = time.monotonic()
            try:
                if not raw:
                    raise ValueError("the index has no RAW twin for this frame")
                ref = None
                if wb_from and fid != wb_from:
                    ref = wb or wb_reference(rows.get(wb_from), wb_from, backend.name)
                row = develop_frame(fid, raw, backend, look, dest, work, detect, neutral_box, ref)
            except Exception as e:  # one bad frame is an error row; Ctrl-C still stops the run
                row = {"id": fid, "raw": raw, "error": str(e), "converged": False, "status": "failed"}
                if os.path.exists(os.path.join(dest, f"{fid}.tif")):
                    row["stale_tif"] = "the TIFF on disk is from an earlier run"
            rows[fid] = row = {**common, **row, "seconds": round(time.monotonic() - t0, 2)}
            log(_line(row))
            # a crash or Ctrl-C on a later frame keeps every row so far (write merges rows already on disk)
            write(dest, {"tool": "rpstills develop", "frames": rows, **common, "look_values_ignored": ignored})
    return {"tool": "rpstills develop", "frames": rows, **common, "look_values_ignored": ignored}


def _line(row):
    if row.get("error"):
        return f"{row['id']}: failed: {row['error']}"
    x, n = row["x"], row.get("neutral")
    pin = f", pinned {','.join(row['pinned'])}" if row["pinned"] else ""
    temp = f"{x['temp_k']} K" if "temp_k" in x else f"temp {x['temp']}"
    neu = ""
    if n:
        neu = (f"  neutral a* {n['lab_out'][1]:.1f} b* {n['lab_out'][2]:.1f} ({n['n_px']} px)" if n["lab_out"]
               else f"  white balance from {n['from']}")
    return (f"{row['id']}: {row['status']}{pin}  ev {x['ev']:+.2f}  {temp}  tint {x['tint']:+.1f}  "
            f"Lab {row['lab_out'][0]:.1f} {row['lab_out'][1]:.1f} {row['lab_out'][2]:.1f}{neu}  "
            f"dE {row['delta_e_out']:.2f}  {row['iterations']} steps  {row['seconds']:.1f} s")


def write(dest, doc):
    """calibration.json in dest, atomically, keeping rows of frames this run
    did not develop."""
    path = os.path.join(dest, "calibration.json")
    frames = _rows_on_disk(dest)
    frames.update(doc["frames"])
    out = dict(doc, frames=frames)
    fd, tmp = tempfile.mkstemp(dir=dest, prefix=".calibration-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(out, f, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
    return path


def summary(doc):
    rows = list(doc["frames"].values())
    count = {k: sum(1 for r in rows if r["status"] == k) for k in ("converged", "pinned", "not_converged", "failed")}
    pins = sorted({a for r in rows for a in r.get("pinned") or []})
    t = doc["target"]
    aim = (f"cheek L* {t[0]:g} ({doc['sample']}), neutral a* {t[1]:g} b* {t[2]:g}" if doc.get("mode") == "neutral"
           else f"target Lab {' '.join(f'{v:g}' for v in t)} ({doc['sample']})")
    text = (f"{len(rows)} frames: {count['converged']} converged, {count['pinned']} pinned"
            + (f" ({', '.join(pins)})" if pins else "")
            + f", {count['not_converged']} not converged, {count['failed']} failed; backend {doc['backend']}, {aim}")
    if doc.get("look_values_ignored"):
        text += f"; look values ignored by this backend: {', '.join(doc['look_values_ignored'])}"
    return text


def add_arguments(p):
    p.add_argument("out", help="the shoot's out dir (index.jsonl, selects.json or cull.json); "
                               "writes OUT/develop/, never inside a git tree")
    p.add_argument("--look", required=True, help="look JSON with a solve block (and optionally a develop block)")
    p.add_argument("--ids", nargs="+", help="frame ids (default: selects.json, else the cull proposal)")
    p.add_argument("--backend", choices=BACKENDS, help="default: the look's develop.backend, else apple; "
                                                       f"lightcraft is opt-in and needs {ENV_LIGHTCRAFT}")
    p.add_argument("--raw", nargs="+", metavar="PATH", help="develop these RAW files (id = file stem) instead of "
                                                            "the index selection")
    p.add_argument("--neutral-box", nargs=4, type=float, metavar=("X0", "Y0", "X1", "Y1"),
                   help="neutral mode: this box (0..1, top-left origin) is the neutral reference in every frame "
                        "solved on its own neutral, in place of the garment below the face (a grey card); with "
                        "--wb-from, only the reference frame, and nothing when the reference comes from "
                        "calibration.json")
    p.add_argument("--wb-from", metavar="ID", help="neutral mode: solve frame ID on its neutral first (a frame of "
                                                    "this run, or a row of OUT/develop/calibration.json), then give "
                                                    "its white balance to every other frame and solve exposure alone "
                                                    "(frames in the same light)")


def run(args):
    try:
        look = load_look(args.look)
    except (OSError, ValueError, TypeError) as e:
        print(f"rpstills develop: look {args.look}: {e}", file=sys.stderr)
        return 1
    box = args.neutral_box
    if (box or args.wb_from) and look["solve"]["mode"] != "neutral":
        print("rpstills develop: --neutral-box and --wb-from need a look with solve.mode neutral", file=sys.stderr)
        return 1
    if box and not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
        print("rpstills develop: --neutral-box is X0 Y0 X1 Y1 within 0..1, X0 < X1 and Y0 < Y1", file=sys.stderr)
        return 1
    out = os.path.abspath(args.out)
    dest = os.path.join(out, "develop")
    from rpresolve.paths import out_problem  # lazy: rpresolve is stdlib-only, imported only to write
    problem = out_problem(os.path.join(dest, "calibration.json"), any_git_tree=True)
    if problem:
        print(f"rpstills develop: {problem}", file=sys.stderr)
        return 1
    try:
        frames, source = select_frames(out, args.ids, args.raw)
        backend = make_backend(args.backend or look["develop"]["backend"], look["develop"].get("profile"))
    except ValueError as e:
        print(f"rpstills develop: {e}", file=sys.stderr)
        return 1
    why = backend.unavailable()
    if why:
        print(f"rpstills develop: {why}", file=sys.stderr)
        return 1
    print(f"developing {len(frames)} frames from {source} with {backend.name}")
    try:
        doc = build(frames, backend, look, dest, neutral_box=box, wb_from=args.wb_from)
    except ValueError as e:  # a --wb-from reference that cannot be used: nothing was developed
        print(f"rpstills develop: {e}", file=sys.stderr)
        return 1
    print(write(dest, doc))
    print(summary(doc))
    bad = [r["id"] for r in doc["frames"].values() if r["status"] != "converged"]
    if bad:
        print(f"rpstills develop: {len(bad)} frames did not converge (failed, pinned or not_converged): "
              f"{', '.join(bad)}", file=sys.stderr)
    return 1 if bad else 0
