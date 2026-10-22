"""rpstills.develop: RAW files to 16-bit TIFFs, each solved onto one skin target.

    python3 -m rpstills develop <out dir> --look FILE [--ids ID ...]
                                [--backend apple|lightcraft] [--raw PATH ...]

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
step would leave its bounds lands on the bound; when the next step still
pushes it out, it is pinned: it leaves the free set and the others solve
on their own components (tint pinned at the look's tint_max leaves ev and
mired solving L* and b*, and a* falls where it does). The solve stops when
the residual over the free axes' components is within AIM (CIELAB units;
half of TOL, a margin for the full-size file) or after MAX_ITER steps.
Status per frame, judged on TOL:
    converged      every requested axis met the target (within TOL)
    pinned         the free axes met it, but an axis sits on a bound and
                   the full target is missed (delta_e says by how much)
    not_converged  MAX_ITER ran out; the TIFF is the best point found
The frame is then developed at full size, 16-bit, at the solved point, and
the sample is measured again on that file (lab_out). A frame's status is
the worse of the solve render's (status_solve) and the delivered file's,
so a file that misses the target is never reported as converged. Only
"converged" sets converged: true. The exit status is 1 when any frame
failed or did not converge. Geometry scales from each render's own size
(the face is normalized), never from a fixed width.

Backends, behind Backend.render(raw, values, out, long_edge=None, bit16=False):
  apple       the default, macOS: develop_raw.swift on CIRAWFilter (exposure,
              neutral temperature, tint), compiled once into
              ~/Library/Caches/rpstills as rpstills.vision does. Look values
              do not apply to it and are listed as ignored.
  lightcraft  TRIAL, opt-in: lightcraft-cli from $RPSTILLS_LIGHTCRAFT. The
              look's develop values are LightCraft control ids, the profile a
              LightCraft profile id. Nothing should rely on it before its
              trial says adopt.
Tint is in the backend's own units, so a tint_max set on one backend is
not the same cast on the other.

Look files gain two blocks (rpstills look ignores both):
  "develop": {"backend": "apple"|"lightcraft", "profile": ..., "values": {...}}
  "solve":   {"target_lab": [L, a, b], "sample": "cheek"|"box",
              "free": ["ev", "mired", "tint"], "tint_max": 6,
              "ev_bounds": [-3, 3], "temp_bounds_k": [2500, 12000]}

Writes <out>/develop/<id>.tif (16-bit sRGB) and <out>/develop/calibration.json:
per frame x, x0, lab, lab_out, residual, delta_e, pinned, iterations,
converged, status, backend and its version, look name and version, target,
sample, seconds. A frame developed before keeps its row unless developed
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
SOLVE_DEFAULTS = {"sample": "cheek", "free": list(AXES), "tint_max": 6.0, "ev_bounds": [-3.0, 3.0],
                  "temp_bounds_k": [2500.0, 12000.0]}


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
    active = list(requested)
    x = np.clip(np.asarray(x0, float), lo, hi)
    y = np.asarray(measure_fn(x), float)
    evals = 1
    J = np.zeros((3, 3))
    for i in active:
        h = FD_STEP[i] if x[i] + FD_STEP[i] <= hi[i] else -FD_STEP[i]
        xi = x.copy()
        xi[i] += h
        J[:, i] = (np.asarray(measure_fn(xi), float) - y) / h
        evals += 1
    pinned, damp, it = [], 1.0, 0
    while active and _residual(y, target, active) > tol and it < max_iter:
        it += 1
        dx, pins = _step(J, target - y, x, active, lo, hi, damp)
        y_new = np.asarray(measure_fn(x + dx), float)
        evals += 1
        w = dx / FD_STEP ** 2  # Broyden's update in step-scaled units, so mired does not swamp ev
        if dx @ w > 0:
            J += np.outer(y_new - y - J @ dx, w) / (dx @ w)
        keep = [i for i in active if i not in pins]
        if keep and _residual(y_new, target, keep) >= _residual(y, target, keep):
            damp = max(MIN_DAMP, damp / 2)  # worse: stay, and try a shorter step on the updated Jacobian
            continue
        x, y, damp = x + dx, y_new, 1.0
        pinned += [AXES[i] for i in pins]
        active = keep
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
    if proc.returncode != 0:
        raise RuntimeError(f"{os.path.basename(cmd[0])} failed: {(proc.stdout + proc.stderr).strip()[-400:]}")
    return proc.stdout


class Backend:
    """A RAW developer. axes are its control ids for (exposure EV, temperature
    K, tint); look_values says whether a look's develop values reach it."""
    name = None
    axes = (None, None, None)
    look_values = False

    def unavailable(self):
        """Why it cannot run, or None."""
        raise NotImplementedError

    def version(self):
        raise NotImplementedError

    def as_shot(self, raw):
        """{"temp_k", "tint"} the RAW was shot at, or None when unknown."""
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

    def __init__(self, profile=None, exe=None):
        self.exe = exe or os.environ.get(ENV_LIGHTCRAFT)
        self.profile = profile

    def unavailable(self):
        if not self.exe:
            return f"the lightcraft backend is a trial: set {ENV_LIGHTCRAFT} to a lightcraft-cli binary"
        if not os.access(self.exe, os.X_OK):
            return f"{ENV_LIGHTCRAFT}={self.exe} is not an executable"
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
    t = s.get("target_lab")
    if not (isinstance(t, list) and len(t) == 3 and all(isinstance(v, (int, float)) for v in t)):
        raise ValueError("solve.target_lab must be [L*, a*, b*]")
    if s["sample"] not in ("cheek", "box"):
        raise ValueError(f"solve.sample is cheek or box, not {s['sample']!r}")
    if not s["free"] or set(s["free"]) - set(AXES):
        raise ValueError(f"solve.free is a non-empty subset of {list(AXES)}")
    (e0, e1), (k0, k1) = s["ev_bounds"], s["temp_bounds_k"]
    if not (e0 < e1 and 0 < k0 < k1 and s["tint_max"] >= 0):
        raise ValueError("solve bounds need ev_bounds lo < hi, 0 < temp_bounds_k lo < hi, tint_max >= 0")
    return dict(doc, develop=dev, solve=s)


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


def _x_out(x):
    ev, mired, tint = (float(v) for v in x)
    return {"ev": round(ev, 3), "temp_k": round(1e6 / mired), "mired": round(mired, 2), "tint": round(tint, 2)}


def develop_frame(fid, raw, backend, look, dest, work, detect):
    """Solve and develop one frame into dest/<id>.tif. Returns its row;
    ValueError or RuntimeError when it cannot be developed."""
    s, dev = look["solve"], look["develop"]
    bounds = [tuple(s["ev_bounds"]), (1e6 / s["temp_bounds_k"][1], 1e6 / s["temp_bounds_k"][0]),
              (-float(s["tint_max"]), float(s["tint_max"]))]
    shot = backend.as_shot(raw)
    x0 = [0.0, 1e6 / (shot["temp_k"] if shot else START_TEMP_K), shot["tint"] if shot else 0.0]
    base = dict(dev["values"]) if backend.look_values else {}

    def values(x):
        ev, mired, tint = (float(v) for v in x)
        return dict(base, **dict(zip(backend.axes, (round(ev, 4), round(1e6 / mired, 1), round(tint, 3)))))

    state = {"face": None, "n": 0, "n_px": None}

    def measure_fn(x):
        state["n"] += 1
        path = os.path.join(work, f"{fid}-{state['n']}.tif")
        backend.render(raw, values(x), path, long_edge=SOLVE_EDGE)
        rgb = read_rgb(path)
        if state["face"] is None:
            state["face"] = _face(path, rgb, detect, s["sample"])
        os.remove(path)
        lab, state["n_px"] = _lab(rgb, state["face"], s["sample"])
        return lab

    result = solve(measure_fn, x0, bounds, s["target_lab"], s["free"], tol=AIM)
    final = os.path.join(dest, f".{fid}.part.tif")
    try:
        backend.render(raw, values(result["x"]), final, bit16=True)
        rgb = read_rgb(final)
        lab_out, n_out = _lab(rgb, state["face"], s["sample"])
        tif = os.path.join(dest, f"{fid}.tif")
        os.replace(final, tif)
    finally:
        if os.path.exists(final):
            os.remove(final)
    # the delivered file decides: a solve that met the target small but misses it full size is not converged
    st_solve, st_out = (status(lab, s["target_lab"], s["free"], result["free"], result["pinned"])
                        for lab in (result["lab"], lab_out))
    st = max(st_solve, st_out, key=("converged", "pinned", "not_converged").index)
    face = state["face"]
    return {"id": fid, "raw": raw, "tif": tif, "size": [rgb.shape[1], rgb.shape[0]],
            "x": _x_out(result["x"]), "x0": _x_out(np.clip(x0, *zip(*bounds))),
            "as_shot": shot, "lab": [round(v, 2) for v in result["lab"]],
            "lab_out": [round(float(v), 2) for v in lab_out], "residual": round(result["residual"], 3),
            "delta_e": round(result["delta_e"], 3),
            "delta_e_out": round(float(np.linalg.norm(lab_out - s["target_lab"])), 3),
            "pinned": result["pinned"], "free": result["free"], "iterations": result["iterations"],
            "evaluations": result["evaluations"], "converged": st == "converged", "status": st,
            "status_solve": st_solve, "sample_px": {"solve": state["n_px"], "out": n_out},
            "face": {k: round(float(face[k]), 4) for k in ("x", "y", "w", "h")}}


# ---------------------------------------------------------------- the run

def build(frames, backend, look, dest, detect=None, log=print):
    """Develop every (id, raw) into dest. Returns the calibration document;
    a frame that fails is a row with its error, the others still run."""
    detect = detect or vision.faces
    s, dev = look["solve"], look["develop"]
    common = {"backend": backend.name, "backend_version": backend.version(),
              "look": {"name": look.get("name"), "version": look.get("version")},
              "target": [float(v) for v in s["target_lab"]], "sample": s["sample"]}
    ignored = sorted(dev["values"]) if dev["values"] and not backend.look_values else []
    os.makedirs(dest, exist_ok=True)
    rows = {}
    with tempfile.TemporaryDirectory(prefix="rpstills-develop-") as work:
        for fid, raw in frames:
            t0 = time.monotonic()
            try:
                if not raw:
                    raise ValueError("the index has no RAW twin for this frame")
                row = develop_frame(fid, raw, backend, look, dest, work, detect)
            except (ValueError, RuntimeError, OSError) as e:
                row = {"id": fid, "raw": raw, "error": str(e), "converged": False, "status": "failed"}
                if os.path.exists(os.path.join(dest, f"{fid}.tif")):
                    row["stale_tif"] = "the TIFF on disk is from an earlier run"
            row.update(common, seconds=round(time.monotonic() - t0, 2))
            rows[fid] = row
            log(_line(row))
    return {"tool": "rpstills develop", "frames": rows, **common, "look_values_ignored": ignored,
            "params": {"solve": {k: s[k] for k in SOLVE_DEFAULTS}, "fd_step": FD_STEP.tolist(),
                       "max_step": MAX_STEP.tolist(), "max_iter": MAX_ITER, "tol": TOL, "aim": AIM,
                       "min_damp": MIN_DAMP, "solve_edge": SOLVE_EDGE, "start_temp_k": START_TEMP_K,
                       "profile": dev.get("profile")}}


def _line(row):
    if row.get("error"):
        return f"{row['id']}: failed: {row['error']}"
    x = row["x"]
    pin = f", pinned {','.join(row['pinned'])}" if row["pinned"] else ""
    return (f"{row['id']}: {row['status']}{pin}  ev {x['ev']:+.2f}  {x['temp_k']} K  tint {x['tint']:+.1f}  "
            f"Lab {row['lab_out'][0]:.1f} {row['lab_out'][1]:.1f} {row['lab_out'][2]:.1f}  "
            f"dE {row['delta_e_out']:.2f}  {row['iterations']} steps  {row['seconds']:.1f} s")


def write(dest, doc):
    """calibration.json in dest, atomically, keeping rows of frames this run
    did not develop."""
    path = os.path.join(dest, "calibration.json")
    frames = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                frames = json.load(f).get("frames") or {}
        except (OSError, ValueError):
            frames = {}
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
    text = (f"{len(rows)} frames: {count['converged']} converged, {count['pinned']} pinned"
            + (f" ({', '.join(pins)})" if pins else "")
            + f", {count['not_converged']} not converged, {count['failed']} failed; backend {doc['backend']}, "
            f"target Lab {' '.join(f'{v:g}' for v in doc['target'])} ({doc['sample']})")
    if doc.get("look_values_ignored"):
        text += f"; look values ignored by this backend: {', '.join(doc['look_values_ignored'])}"
    return text


def add_arguments(p):
    p.add_argument("out", help="the shoot's out dir (index.jsonl, selects.json or cull.json); "
                               "writes OUT/develop/, never inside a git tree")
    p.add_argument("--look", required=True, help="look JSON with a solve block (and optionally a develop block)")
    p.add_argument("--ids", nargs="+", help="frame ids (default: selects.json, else the cull proposal)")
    p.add_argument("--backend", choices=BACKENDS, help="default: the look's develop.backend, else apple; "
                                                       f"lightcraft is a trial and needs {ENV_LIGHTCRAFT}")
    p.add_argument("--raw", nargs="+", metavar="PATH", help="develop these RAW files (id = file stem) instead of "
                                                            "the index selection")


def run(args):
    try:
        look = load_look(args.look)
    except (OSError, ValueError, TypeError) as e:
        print(f"rpstills develop: look {args.look}: {e}", file=sys.stderr)
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
    doc = build(frames, backend, look, dest)
    print(write(dest, doc))
    print(summary(doc))
    bad = [r["id"] for r in doc["frames"].values() if r["status"] in ("failed", "not_converged")]
    if bad:
        print(f"rpstills develop: {len(bad)} frames failed or did not converge: {', '.join(bad)}", file=sys.stderr)
    return 1 if bad else 0
