"""lutlab.transfer: propose a colour transfer from a reference still to a source still, as a .cube.

REF is the look to reach, SRC is the shot to change; the two stills may show different scenes, so
nothing is paired and the transfer is on colour distributions, not pixels. Both are code values in
one space (default srgb; a log still works with --space vlog and the like). Every statistic and
every distance is measured in CIELAB of the code values decoded to linear Rec.709, the way
shotmatch and port.diff measure; the LUT itself maps codes to codes in that same space.

Three stages, each optional, run in this order on what the earlier ones leave:
  reinhard  per-channel mean and standard deviation in Lab (Reinhard et al., 2001, in CIELAB)
  mkl       the linear Monge-Kantorovich map between the two Lab Gaussians (Pitie and Kokaram, 2007):
            matches the full covariance, so it rotates colour as well as scales it
  ot        sliced optimal transport (iterative distribution transfer, Pitie et al., 2005): random
            rotations, a monotone 1-D quantile match per axis, damped steps; a nonlinear refinement
            that matches what the Gaussians cannot
Own numpy code, no third-party transfer library.

Guards. A stage whose own change is out of bounds, or that makes the grey ramp non-monotone or
steppy, is skipped and says why; when no stage survives, nothing is written. Inputs that cannot
support a fit (a tiny still, a flat one, a source with no colour) are refused. The table is clipped
to 0..1. Fitting uses half the sampled pixels and the report scores the other half.

The tool proposes and applies nothing: it writes NAME.cube and NAME.report.md to a directory the
caller names, never inside a git tree and never in Resolve's own folders."""

from pathlib import Path

import numpy as np

from rpstills.measure import delta_e2000

from . import cube, pairs, spaces

STAGES = ("reinhard", "mkl", "ot")
MIN_PIXELS = 4096        # a still smaller than 64 x 64 cannot say what its colours are
MIN_L_STD = 2.0          # Lab L standard deviation of a still that is flat
MIN_CHROMA_STD = 1.0     # the source's largest a/b standard deviation; below it there is no colour to map
SAMPLE = 60000           # pixels kept per still, by stride, half to fit and half to score
MAX_GAIN = 4.0           # a stage may not stretch any Lab axis by more than this (chosen default)
MAX_MOVE = 60.0          # nor move 99% of the source's own pixels further than this many Lab units
MAX_SLOPE = 12.0         # grey ramp: output change per unit of input change, per channel
RAMP_TOL = 1e-4          # grey ramp: a drop larger than this in any channel is non-monotone
OT_ROTATIONS, OT_STEP, OT_LEVELS = 24, 0.5, 65
SLICES = 64              # directions the distance is averaged over


class Refused(ValueError):
    """The inputs cannot support a fit."""


# --- Lab <-> codes -----------------------------------------------------------

_M = spaces.XYZ["709"]
_MINV = np.linalg.inv(_M)
_WHITE = _M.sum(1)


def to_lab(codes, space):
    return spaces.cielab(np.maximum(spaces.to_lin709(np.clip(codes, 0, 1), space), 0))


def from_lab(lab, space):
    """Lab -> codes in 0..1. Colours outside the space's gamut clip at its edge."""
    L, a, b = np.moveaxis(np.asarray(lab, dtype=np.float64), -1, 0)
    fy = (L + 16) / 116
    f = np.stack([fy + a / 500, fy, fy - b / 200], -1)
    d = 6 / 29
    t = np.where(f > d, f ** 3, 3 * d * d * (f - 4 / 29))
    lin = np.maximum(spaces._apply(_MINV, t * _WHITE), 0)
    return np.clip(spaces.from_lin709(lin, space), 0, 1)


# --- the stages: each is fitted on Lab samples, applied to Lab points --------

def fit_reinhard(s, r):
    scale = r.std(0) / s.std(0)
    return {"stage": "reinhard", "mu_s": s.mean(0), "mu_r": r.mean(0), "scale": scale, "gain": float(scale.max())}


def _sym(c, p):
    w, v = np.linalg.eigh(c)
    return (v * np.maximum(w, 1e-9) ** p) @ v.T


def fit_mkl(s, r):
    cs, cr = np.cov(s.T), np.cov(r.T)
    h, hi = _sym(cs, 0.5), _sym(cs, -0.5)
    t = hi @ _sym(h @ cr @ h, 0.5) @ hi
    return {"stage": "mkl", "mu_s": s.mean(0), "mu_r": r.mean(0), "matrix": t,
            "gain": float(np.linalg.eigvalsh((t + t.T) / 2).max())}


def fit_ot(s, r, seed=0):
    """Sliced optimal transport of the sample s onto r. Stores the rotations and the 1-D displacement
    curves so the stage is a function of any Lab point, not a move of the sample alone."""
    rng = np.random.default_rng(seed)
    levels = np.linspace(0, 1, OT_LEVELS)
    cur, steps = s.copy(), []
    for _ in range(OT_ROTATIONS):
        q, _ = np.linalg.qr(rng.standard_normal((3, 3)))
        ps, pr = cur @ q, r @ q
        qs, qr = np.quantile(ps, levels, axis=0), np.quantile(pr, levels, axis=0)
        qs = np.maximum.accumulate(qs, axis=0) + np.arange(OT_LEVELS)[:, None] * 1e-9   # strictly rising
        steps.append((q, qs, qr - qs))
        cur = _ot_step(cur, steps[-1])
    return {"stage": "ot", "steps": steps}


def _ot_step(x, step):
    q, qs, disp = step
    p = x @ q
    d = np.stack([np.interp(p[:, k], qs[:, k], disp[:, k]) for k in range(3)], -1)  # clamps at the ends
    return x + OT_STEP * (d @ q.T)


def apply_stage(st, x):
    """Lab points (N, 3) through one fitted stage."""
    if st["stage"] == "reinhard":
        return (x - st["mu_s"]) * st["scale"] + st["mu_r"]
    if st["stage"] == "mkl":
        return (x - st["mu_s"]) @ st["matrix"].T + st["mu_r"]
    for step in st["steps"]:
        x = _ot_step(x, step)
    return x


def apply_chain(codes, chain, space):
    """Codes (..., 3) through the kept stages, back to codes in 0..1."""
    shape = np.shape(codes)
    lab = to_lab(np.reshape(codes, (-1, 3)), space)
    for st in chain:
        lab = apply_stage(st, lab)
    return from_lab(lab, space).reshape(shape)


# --- guards and distances ----------------------------------------------------

def ramp_check(fn, n=1024, max_slope=MAX_SLOPE):
    """The grey ramp through fn (codes -> codes): returns '' when every channel is monotone and no
    step is steeper than max_slope, else a plain reason."""
    g = np.linspace(0, 1, n)
    out = fn(np.repeat(g[:, None], 3, 1))
    d = np.diff(out, axis=0)
    if d.min() < -RAMP_TOL:
        k = int(np.argmin(d.min(1)))
        return f"the grey ramp falls by {-d.min():.4f} near input {g[k]:.3f}, so it is not monotone"
    slope = d.max() * (n - 1)
    if slope > max_slope:
        return f"the grey ramp has a step of slope {slope:.1f} (limit {max_slope:g}), which would band"
    return ""


def slice_distance(a, b, seed=1):
    """Sliced Wasserstein-2 between two Lab samples, in Lab units: the root of the mean over fixed
    random directions of the squared quantile gap."""
    rng = np.random.default_rng(seed)
    dirs = rng.standard_normal((SLICES, 3))
    dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
    lv = np.linspace(0.005, 0.995, 200)
    qa, qb = np.quantile(a @ dirs.T, lv, axis=0), np.quantile(b @ dirs.T, lv, axis=0)
    return float(np.sqrt(((qa - qb) ** 2).mean()))


def _summary(x, ref):
    return {"mean_shift": round(float(np.linalg.norm(x.mean(0) - ref.mean(0))), 3),
            "std_ratio": [round(float(v), 3) for v in x.std(0) / ref.std(0)],
            "sliced_w2": round(slice_distance(x, ref), 3)}


def _halves(x, block=64):
    """Fit and held-out halves, alternating blocks of the strided sample so a held-out pixel is never beside a fit pixel."""
    mask = (np.arange(len(x)) // block) % 2 == 0
    return x[mask], x[~mask]


def refuse_resolve(path):
    if any(part.casefold() == "blackmagic design" for part in Path(path).resolve().parts):
        raise PermissionError(f"{path}: lutlab proposes a LUT and never writes into Resolve's folders")


def _sample(path):
    im = pairs.read_image(path)
    if not np.isfinite(im).all():
        raise Refused(f"{Path(path).name}: has values that are not numbers")
    h, w = im.shape[:2]
    if h * w < MIN_PIXELS:
        raise Refused(f"{Path(path).name}: {w}x{h} is too small to say what its colours are (needs {MIN_PIXELS} pixels or more)")
    flat = im.reshape(-1, 3)
    return flat[np.linspace(0, len(flat) - 1, min(len(flat), SAMPLE)).astype(int)]


MAX_SIZE = 129   # Resolve reads cubes to 129 per edge


def transfer(src_path, ref_path, space="srgb", stages=STAGES, size=cube.DEFAULT_SIZE, max_slope=MAX_SLOPE):
    """Fit the staged transfer. Returns the result dict; raises Refused when the inputs cannot support
    a fit or no stage passes its guard."""
    spaces.check_space(space)
    if not 2 <= size <= MAX_SIZE:
        raise ValueError(f"--size is a cube edge from 2 to {MAX_SIZE}; got {size}")
    stages = tuple(stages)
    bad = [s for s in stages if s not in STAGES]
    if bad or not stages:
        raise ValueError(f"stages are chosen from {', '.join(STAGES)}; got {', '.join(bad) or 'none'}")
    s_all, r_all = _sample(src_path), _sample(ref_path)
    s_lab, r_lab = to_lab(s_all, space), to_lab(r_all, space)
    for name, lab in (("source", s_lab), ("reference", r_lab)):
        if lab[:, 0].std() < MIN_L_STD:
            raise Refused(f"the {name} still is flat (lightness spread {lab[:, 0].std():.1f}); there is no distribution to match")
    if np.linalg.eigvalsh(np.cov(s_lab[:, 1:].T)).max() < MIN_CHROMA_STD ** 2:
        raise Refused("the source still has almost no colour (it reads as grey); a transfer has no colour to carry")
    fit_s, hold_s = _halves(s_lab)
    fit_r, hold_r = _halves(r_lab)
    fn = lambda chain: (lambda c: apply_chain(c, chain, space))
    chain, rows = [], [{"stage": "none", "applied": True, "reason": "", "gain": None, "move_p99": 0.0,
                        "held_out": _summary(hold_s, hold_r)}]
    cur_fit, cur_hold = fit_s, hold_s
    for name in stages:
        st = {"reinhard": fit_reinhard, "mkl": fit_mkl, "ot": fit_ot}[name](cur_fit, fit_r)
        new_fit, new_hold = apply_stage(st, cur_fit), apply_stage(st, cur_hold)
        move = float(np.percentile(np.linalg.norm(new_fit - fit_s, axis=1), 99))  # the whole chain so far, not this stage alone
        gain = st.get("gain")
        reason = ""
        if not (np.isfinite(new_fit).all() and np.isfinite(new_hold).all()):
            reason = "it produced values that are not numbers (an axis with no spread in one of the stills)"
        elif gain is not None and gain > MAX_GAIN:
            reason = f"it stretches a Lab axis by {gain:.1f}x (limit {MAX_GAIN:g}x); the two stills are too far apart for a bounded change"
        elif move > MAX_MOVE:
            reason = f"the chain would move the source's colours by {move:.0f} Lab units at the 99th percentile (limit {MAX_MOVE:g})"
        else:
            reason = ramp_check(fn(chain + [st]), max_slope=max_slope)
        row = {"stage": name, "applied": not reason, "reason": reason, "gain": None if gain is None else round(gain, 3),
               "move_p99": round(move, 3)}
        if not reason:
            chain.append(st)
            cur_fit, cur_hold = new_fit, new_hold
        row["held_out"] = _summary(cur_hold, hold_r)
        rows.append(row)
    if not chain:
        raise Refused("no stage passed its guard, so there is nothing to propose: " + "; ".join(f"{r['stage']}: {r['reason']}" for r in rows[1:]))
    grid = cube.lattice(size)
    table = apply_chain(grid.reshape(-1, 3), chain, space).reshape(grid.shape)
    de = delta_e2000(to_lab(table.reshape(-1, 3), space), to_lab(grid.reshape(-1, 3), space))
    return {"space": space, "chain": chain, "table": table, "rows": rows, "size": size,
            "lattice_move": {"mean": round(float(de.mean()), 3), "max": round(float(de.max()), 3)}}


# --- output ------------------------------------------------------------------

def report_md(res, name, src, ref):
    r0 = res["rows"][0]["held_out"]
    line = lambda r: (f"| {r['stage']} | {'kept' if r['applied'] else 'skipped'} | {r['held_out']['mean_shift']} | "
                      f"{' '.join(str(v) for v in r['held_out']['std_ratio'])} | {r['held_out']['sliced_w2']} | "
                      f"{'' if r['gain'] is None else r['gain']} | {r['move_p99']} | {r['reason']} |")
    last = [r for r in res["rows"] if r["applied"]][-1]["held_out"]
    return "\n".join([
        f"# {name}: reference transfer", "",
        f"Source `{Path(src).name}` carried toward reference `{Path(ref).name}`, in `{res['space']}` code values; "
        "statistics and distances in CIELAB of the decoded linear Rec.709.", "",
        "A proposal: this applies nothing. Whether the look is right is a call by eye.", "",
        "## Stages", "",
        "Distances are on the held-out half of the sampled pixels (the stages were fitted on the other half). "
        "Mean shift is the Lab distance between the two means. Std ratio is source over reference for L, a, b; "
        "1 is matched. Sliced W2 is the distance between the two colour distributions in Lab units; 0 is identical. "
        "Gain is the largest Lab stretch of the stage, move the 99th percentile Lab move of the source's own pixels, cumulative over the kept stages.", "",
        "| stage | status | mean shift | std ratio L a b | sliced W2 | gain | move p99 | why skipped |",
        "|---|---|---|---|---|---|---|---|",
        line({**res["rows"][0], "applied": True, "stage": "before", "gain": None}),
        *[line(r) for r in res["rows"][1:]], "",
        f"Sliced W2 {r0['sliced_w2']} before, {last['sliced_w2']} after.", "",
        "## The LUT", "",
        f"{res['size']}-point `{name}.cube`, codes in and out in `{res['space']}`, clipped to 0..1. "
        f"CIEDE2000 from identity over the lattice: mean {res['lattice_move']['mean']}, max {res['lattice_move']['max']}. "
        "The grey ramp is monotone in every channel with no step steeper than "
        f"{MAX_SLOPE:g} (checked on the kept chain).", "",
        "This matches colour distributions, not content: a reference with a different scene composition pulls "
        "colours that the composition, not the grade, put there. Run `decompose` on the `.cube` to read it as a "
        "named, bounded look before using it.", ""])


def write(res, outdir, name, src, ref, overwrite=False):
    """Write NAME.cube and NAME.report.md into outdir. Refuses Resolve's own folders."""
    refuse_resolve(outdir)
    out = Path(outdir).resolve()
    clash = [str(out / f) for f in (f"{name}.cube", f"{name}.report.md") if (out / f).exists()]
    if clash and not overwrite:
        raise FileExistsError(", ".join(clash))
    return [cube.write_cube(str(out / f"{name}.cube"), res["table"], f"{name.replace(chr(34), chr(39))} reference transfer", overwrite=overwrite),
            cube.write_text(str(out / f"{name}.report.md"), report_md(res, name, src, ref), overwrite)]
