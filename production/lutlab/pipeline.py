"""lutlab.pipeline: decompose a .cube, and build a .cube / Hald CLUT from parameters."""

import hashlib
import json
import re
from pathlib import Path

import numpy as np

from . import cube, fit, model, report, spaces


def plausible(codes, in_space, ev=(-8.0, 6.0)):
    """Lattice nodes a camera can actually produce: luminance within a sane exposure
    range and not far outside Rec.709 (strongly negative channels are gamut corners)."""
    lin = spaces.to_lin709(codes, in_space)
    y = spaces.luminance(lin)
    e = model.exposure(y)
    big = np.abs(lin).max(-1)
    return (y > 0) & (e >= ev[0]) & (e <= ev[1]) & (lin.min(-1) >= -0.05 * big)


def natural_samples(lut, in_space, ev=(-6.0, 4.0), rel=np.arange(0.0, 0.33, 0.03), hue_step=5):
    """Colours real scenes contain, evenly spread in OKLab: exposures from `ev`,
    chroma relative to lightness up to about the ColorChecker's strongest patch,
    every `hue_step` degrees. The LUT is sampled there for the fit. Its lattice
    nodes would be a poor sample: uniform in code values, so most of a log
    LUT's nodes are saturations no surface reaches, and the numbers would bend
    toward them and away from skin, foliage and sky."""
    e = np.arange(ev[0], ev[1] + 1e-9, 0.5)
    L = np.cbrt(0.18 * 2.0 ** e)                   # OKLab L of a grey at that exposure
    h = np.radians(np.arange(0, 360, hue_step))
    LL, RR, HH = np.meshgrid(L, rel, h, indexing="ij")
    lab = np.stack([LL, RR * LL * np.cos(HH), RR * LL * np.sin(HH)], -1).reshape(-1, 3)
    lin = spaces.oklab_to_lin709(lab)
    keep = lin.min(-1) >= -0.05 * np.abs(lin).max(-1)
    codes = spaces.from_lin709(lin[keep], in_space)
    inside = ((codes >= lut["domain_min"]) & (codes <= lut["domain_max"])).all(-1)
    return codes[inside]


def neutral_axis(lut, n=1024):
    t = np.linspace(0, 1, n)[:, None]
    codes = lut["domain_min"] + t * (lut["domain_max"] - lut["domain_min"])
    return codes, cube.sample(lut, codes)


def neutral_rows(p, codes, target, in_space):
    lin = spaces.to_lin709(codes, in_space)[:, 0]
    e = model.exposure(lin)
    rows = []
    marks = [-6, -4, -2, -1, 0, 1, 2, 4, 6] if in_space not in report.DISPLAY else None
    pts = ([int(np.argmin(np.abs(e - m))) for m in marks if e.min() - 0.1 <= m <= e.max() + 0.1] if marks else
           [int(np.argmin(np.abs(codes[:, 0] - c))) for c in (0.1, 0.25, 0.5, 0.75, 0.9)])
    pred = model.evaluate(p, codes[pts])
    for k, i in enumerate(pts):
        rows.append([f"{e[i]:+.1f} stops", f"{codes[i, 0]:.3f}", " ".join(f"{v:.3f}" for v in target[i]),
                     " ".join(f"{v:.3f}" for v in pred[k])])
    return rows


def decompose(path, in_space, out, outdir, name=None, overwrite=False):
    spaces.check_space(in_space)
    if (outdir / "params.json").exists() and not overwrite:
        raise FileExistsError(f"{outdir / 'params.json'} exists (it may hold hand edits); "
                              "use a new --out or pass --overwrite")
    if out not in model.OUT_SPACES:
        raise ValueError(f"--out-space must be one of {model.OUT_SPACES}")
    lut = cube.read_cube(path)
    name = name or Path(path).stem
    n = lut["table"].shape[0]
    X = cube.lattice(n, lut["domain_min"], lut["domain_max"]).reshape(-1, 3)
    Y = lut["table"].reshape(-1, 3)
    pl = plausible(X, in_space)
    Xn = natural_samples(lut, in_space)
    Yn = cube.sample(lut, Xn)
    wn = ((Yn > 0.002) & (Yn < 0.998)).all(-1).astype(np.float64)
    dc, dy = neutral_axis(lut)
    dw = (spaces.to_lin709(dc, in_space)[:, 0] > 1e-6).astype(np.float64)

    p = fit.fit(in_space, out, Xn, Yn, wn, dc[dw > 0], dy[dw > 0], dw[dw > 0])
    resid = (Y - model.evaluate(p, X)).reshape(lut["table"].shape)
    res_name = f"{name}.residual.cube"
    cube.write_cube(str(outdir / res_name), resid, f"lutlab residual {name}", lut["domain_min"], lut["domain_max"],
                    digits=7, overwrite=True)
    p["residual"] = res_name
    p["fit_error"] = {"natural colours": fit.errors(p, Xn, Yn), "plausible": fit.errors(p, X, Y, pl),
                      "all nodes": fit.errors(p, X, Y)}
    p["source"] = {"file": Path(path).name, "title": lut["title"], "size": n,
                   "domain_min": lut["domain_min"].tolist(), "domain_max": lut["domain_max"].tolist(),
                   "output_range": [float(Y.min()), float(Y.max())]}
    p["skin_family"] = report.skin_family(lambda c: cube.sample(lut, c), in_space, out)
    rebuilt = model.evaluate(p, X) + cube.read_cube(str(outdir / res_name))["table"].reshape(-1, 3)
    p["source"]["rebuild_max_abs_error"] = float(np.abs(rebuilt - Y).max())
    (outdir / "params.json").write_text(json.dumps(p, indent=1) + "\n")

    rows, before, after = report.patch_table(lambda c: cube.sample(lut, c), in_space, out)
    e = model.exposure(spaces.to_lin709(dc[dw > 0], in_space)[:, 0])
    images = {"tone curve": "tone.png", "hue vs hue": "hue_shift.png", "hue vs sat": "hue_sat.png",
              "neutral ramp": "ramp.png", "patches": "patches.png", "residual": "residual.png"}
    report.plot_tone(outdir / "tone.png", p, e, fit.y_code(dy[dw > 0], out), dy[dw > 0])
    report.plot_hue(outdir / "hue_shift.png", outdir / "hue_sat.png", p)
    report.plot_ramp(outdir / "ramp.png", dc, dy, out)
    report.plot_patches(outdir / "patches.png", before, after)
    de = spaces.delta_e2000(spaces.cielab(np.maximum(spaces.to_lin709(np.clip(Y[pl], 0, 1), out), 0)),
                            spaces.cielab(np.maximum(spaces.to_lin709(np.clip(model.evaluate(p, X[pl]), 0, 1), out), 0)))
    report.plot_hist(outdir / "residual.png", de, "What the numbers miss: Delta E 2000, plausible nodes, model vs source")
    md = report.markdown(p, f"{name}: decomposition", rows, neutral_rows(p, dc[dw > 0], dy[dw > 0], in_space), images)
    (outdir / "report.md").write_text(md)
    return p


def load(params_path, delta=None):
    """A look's params.json, optionally with a delta file layered on (model.apply_delta)."""
    p = json.loads(Path(params_path).read_text())
    p["_dir"] = str(Path(params_path).resolve().parent)
    if delta:
        p = model.apply_delta(p, json.loads(Path(delta).read_text()))
    return p


def make_fn(p, in_space=None, residual=True, out_encoding=None):
    """Input codes (in in_space, default the look's own) -> output codes, clipped to the source's range.

    out_encoding re-encodes the finished display picture (clipped to 0..1 in the look's own out space) into
    another space's code values, e.g. "di" for a LUT that sits before a colour-managed project's output
    transform. The picture is the same; only the container changes."""
    model.check(p)
    in_space = spaces.check_space(in_space or p["in"])
    res = None
    if residual and p.get("residual"):
        res = cube.read_cube(str(Path(p["_dir"]) / p["residual"]))
    lo, hi = p.get("source", {}).get("output_range", [0.0, 1.0])
    lo, hi = min(lo, 0.0), max(hi, 1.0)

    def fn(codes):
        out = model.evaluate(p, codes, in_space)
        if res is not None:
            src = codes if in_space == p["in"] else spaces.from_lin709(spaces.to_lin709(codes, in_space), p["in"])
            # outside the source's domain the residual knows nothing: add none rather than the clamped edge
            inside = ((src >= res["domain_min"] - 1e-9) & (src <= res["domain_max"] + 1e-9)).all(-1)
            out = out + cube.sample(res, src) * inside[..., None]
        out = np.clip(out, lo, hi)
        if out_encoding:
            out = spaces.from_lin709(spaces.to_lin709(np.clip(out, 0, 1), p["out"]), out_encoding)
        return out
    return fn


def build(params_path, out_cube=None, size=33, in_space=None, residual=True, hald=None, hald_level=8, overwrite=False,
          delta=None, out_encoding=None):
    p = load(params_path, delta)
    if out_encoding:
        spaces.check_space(out_encoding)
    fn = make_fn(p, in_space, residual, out_encoding)
    same = (in_space or p["in"]) == p["in"]
    src = p.get("source", {})
    dmin = src.get("domain_min", [0, 0, 0]) if same else [0, 0, 0]
    dmax = src.get("domain_max", [1, 1, 1]) if same else [1, 1, 1]
    written = []
    if out_cube:
        X = cube.lattice(size, dmin, dmax)
        title = f"lutlab {Path(out_cube).stem}"
        written.append(cube.write_cube(str(out_cube), fn(X), title, dmin, dmax, overwrite=overwrite))
    if hald:
        written.append(cube.write_hald(str(hald), fn, hald_level, overwrite=overwrite))  # Hald inputs are 0..1 codes
    return written


def monitor_names(looks, labels):
    """Card file names (without .cube) for the in-camera copies: {(look, label): name}. A name is the camera's
    first two letters or digits plus the first four of the look's name without its leading "RP ", upper case,
    so it fits the S5II's FAT32 limit of 8 characters ("RP Print Teal" on the S5II is S5PRIN). Where two collide, the look part's last two characters
    become the first two hex digits of a hash of the look's full name; if that still collides, ValueError."""
    alnum = lambda s, n: re.sub(r"[^A-Za-z0-9]", "", s).upper()[:n]
    house = lambda s: re.sub(r"^RP\s+", "", s, flags=re.I)  # the shipped looks all start "RP "
    names = {(lk, lb): alnum(lb, 2) + alnum(house(lk), 4) for lk in looks for lb in labels}
    count = {}
    for n in names.values():
        count[n] = count.get(n, 0) + 1
    for (lk, lb), n in list(names.items()):
        if count[n] > 1:
            names[(lk, lb)] = n[:-2] + hashlib.sha1(lk.encode()).hexdigest()[:2].upper()
    if len(set(names.values())) != len(names):
        raise ValueError("monitor file names still collide; rename a look")
    return names


def looks(base_params, deltas, cameras, outdir, size=33, overwrite=False, monitor=False):
    """Bake every delta on the base for every target: <outdir>/<look> [<Label>].cube.
    A target is a camera (its log in, Rec.709 out) or "rcm" (DaVinci Intermediate in and out, for a node
    in a Resolve Color Managed project; see spaces.RCM).
    monitor also writes, per camera look, a 33-point copy clipped to 0..1 named by monitor_names (port's
    monitor_copy does the resampling and the clip). rcm gets none: no camera loads it."""
    from . import port
    written = []
    names = [json.loads(Path(d).read_text()).get("name") or Path(d).stem for d in deltas]
    for n in names:
        if Path(n).name != n or n in (".", ".."):
            raise ValueError(f"look name {n!r} is not a file name")  # a name with a path would write outside --out
    labels = [spaces.target(c)[1] for c in cameras if c.lower() != "rcm"]
    short = monitor_names(names, labels) if monitor else {}
    for d, name in zip(deltas, names):
        for cam in cameras:
            space, label, enc = spaces.target(cam)
            out = Path(outdir) / f"{name} [{label}].cube"
            written += build(base_params, str(out), size, space, True, overwrite=overwrite, delta=d, out_encoding=enc)
            if monitor and cam.lower() != "rcm":
                fn = make_fn(load(base_params, d), space, True, enc)
                table = port.monitor_copy(None, size, lambda n: fn(cube.lattice(n)))
                written.append(cube.write_cube(str(Path(outdir) / f"{short[(name, label)]}.cube"), table,
                                               f"{name} [{label} monitor]", overwrite=overwrite))
    return written
