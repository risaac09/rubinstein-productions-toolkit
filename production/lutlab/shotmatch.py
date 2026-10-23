"""lutlab.shotmatch: match one shot to a reference shot of the same content.

Two frames of the same scene (two cameras on one set, or one camera before
and after a light change): REF is the look to reach, SRC is the shot to
change. Both are code values in one space (default srgb; a log frame works
with --space vlog and the like, the fit then runs in that log).

The fit is two stages, both on aligned pixel pairs:
  1. an ASC CDL (slope, offset, power per channel, one saturation), fitted by
     least squares on CIELAB error with a robust loss. Ten numbers, so it
     cannot memorise the pair.
  2. a 17-point 3D residual LUT on what the CDL leaves, regularised toward
     zero and toward smooth. Held-out data keeps it honest: it is fitted on half
     the pixels (a checkerboard of 32 px blocks), scored on the other half,
     and kept only when it beats the CDL alone there; of three strengths the
     best held-out one wins.

Content check first. Unpaired transfer, a match fitted between two different
scenes, measured worse than no correction, so the pair is refused when, after
alignment, the luminance of the two frames does not rank-correlate (Spearman,
default 0.8). The same colour change keeps the ranking; a different picture
does not. Nothing is written for a refused pair.

Output (never inside a git tree): NAME.cc (the CDL, ASC CDL 1.01 XML, for a
Resolve CDL import), NAME.cube (CDL and residual baked in, one LUT), NAME-
residual.cube (the residual alone, to follow the CDL in a second node), and
NAME.report.md with the CIEDE2000 error before and after, on held-out pixels
and on all."""

from pathlib import Path

import numpy as np
from scipy import sparse
from scipy.optimize import least_squares
from scipy.sparse.linalg import splu

from rpstills.measure import delta_e2000

from . import cube, fit, pairs, spaces
from .cdl import cdl_apply, cdl_xml, read_cdl  # noqa: F401  one copy of the CDL helpers, in lutlab.cdl

GRID = 17
LUMA = np.array([0.2126, 0.7152, 0.0722])
MIN_CORR = 0.80
MIN_GAIN = 0.25  # mean dE2000 a residual must earn on held-out pixels to be kept
STRENGTHS = (0.3, 3.0, 30.0)  # residual regularisation, times the data weight per node
CDL_LO = np.array([0.2] * 3 + [-0.4] * 3 + [0.3] * 3 + [0.0])
CDL_HI = np.array([4.0] * 3 + [0.4] * 3 + [3.0] * 3 + [3.0])


class Refused(ValueError):
    """The two frames do not show the same content."""


def _vec(c):
    return np.concatenate([c["slope"], c["offset"], c["power"], [c["sat"]]])


def _cdl(v):
    return {"slope": np.array(v[0:3]), "offset": np.array(v[3:6]), "power": np.array(v[6:9]), "sat": float(v[9])}


def _lab(codes, space):
    return spaces.cielab(np.maximum(spaces.to_lin709(np.clip(codes, 0, 1), space), 0))


def fit_cdl(src, ref, space, n=6000):
    """CDL dict for src (N, 3) -> ref (N, 3): robust least squares on CIELAB difference."""
    pick = np.random.default_rng(0).choice(len(src), min(len(src), n), replace=False)
    s, r = src[pick], ref[pick]
    r_lab = _lab(r, space)
    slope = np.array([np.polyfit(s[:, k], r[:, k], 1) for k in range(3)])
    x0 = np.clip(np.concatenate([slope[:, 0], slope[:, 1], [1, 1, 1], [1.0]]), CDL_LO + 1e-3, CDL_HI - 1e-3)
    res = least_squares(lambda v: (_lab(cdl_apply(s, _cdl(v)), space) - r_lab).ravel(), x0,
                        bounds=(CDL_LO, CDL_HI), loss="soft_l1", f_scale=3.0, x_scale="jac")
    return _cdl(res.x)


def _weights(rgb, n=GRID):
    """Sparse trilinear weights (N, n^3), node index r * n^2 + g * n + b."""
    p = np.clip(rgb, 0, 1) * (n - 1)
    i0 = np.minimum(np.floor(p).astype(int), n - 2)
    f = p - i0
    rows, cols, vals = [], [], []
    for dr in (0, 1):
        for dg in (0, 1):
            for db in (0, 1):
                w = (f[:, 0] if dr else 1 - f[:, 0]) * (f[:, 1] if dg else 1 - f[:, 1]) * (f[:, 2] if db else 1 - f[:, 2])
                rows.append(np.arange(len(p)))
                cols.append((i0[:, 0] + dr) * n * n + (i0[:, 1] + dg) * n + i0[:, 2] + db)
                vals.append(w)
    return sparse.csr_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(len(p), n ** 3))


def _smooth(n=GRID):
    """Second differences along each axis of the node grid."""
    d = sparse.diags([1, -2, 1], [0, 1, 2], shape=(n - 2, n))
    eye = sparse.identity(n)
    return sparse.vstack([sparse.kron(sparse.kron(d, eye), eye), sparse.kron(sparse.kron(eye, d), eye),
                          sparse.kron(sparse.kron(eye, eye), d)]).tocsr()


def fit_residual(mid, ref, strength, n=GRID):
    """Node offsets (n, n, n, 3) so that mid + offset(mid) approaches ref. Unsupported nodes stay 0."""
    a = _weights(mid, n)
    reg = _smooth(n)
    lhs = (a.T @ a + (len(mid) / n ** 3) * strength * (reg.T @ reg + 0.1 * sparse.identity(n ** 3))).tocsc()
    solve = splu(lhs)
    d = np.clip(ref - mid, -0.3, 0.3)
    return np.clip(np.stack([solve.solve(a.T @ d[:, k]) for k in range(3)], -1), -0.3, 0.3).reshape(n, n, n, 3)


def residual_lut(delta, n=GRID):
    return {"table": cube.lattice(n) + delta, "domain_min": np.zeros(3), "domain_max": np.ones(3), "title": "residual"}


def apply_match(rgb, cdl, delta=None):
    """CDL, clipped to 0..1, then the residual LUT."""
    y = np.clip(cdl_apply(rgb, cdl), 0, 1)
    return y if delta is None else np.clip(cube.sample(residual_lut(delta, delta.shape[0]), y), 0, 1)


def _rank_corr(a, b):
    ra, rb = np.argsort(np.argsort(a.ravel())), np.argsort(np.argsort(b.ravel()))
    return float(np.corrcoef(ra, rb)[0, 1])


def content_score(src_full, ref_full, margin=0.10, px=120):
    """Spearman correlation of blurred luminance between two aligned frames, centre crop."""
    import cv2
    out = []
    for im in (src_full, ref_full):
        h, w = im.shape[:2]
        c = im[int(h * margin):int(h * (1 - margin)), int(w * margin):int(w * (1 - margin))]
        y = c @ LUMA
        out.append(cv2.resize(y.astype(np.float32), (px, max(8, int(px * y.shape[0] / y.shape[1]))),
                              interpolation=cv2.INTER_AREA))
    return _rank_corr(*out)


def match(src_path, ref_path, space="srgb", min_corr=MIN_CORR):
    """Fit the match. Returns {cdl, delta, content, aligned, errors, ...}; raises Refused for a mismatched pair."""
    spaces.check_space(space)
    src, ref = pairs.read_image(src_path), pairs.read_image(ref_path)
    if abs(np.log((src.shape[1] / src.shape[0]) / (ref.shape[1] / ref.shape[0]))) > 0.03:
        raise Refused("the frames differ in aspect ratio; crop them to the same framing first")
    src = pairs._resize(src, ref.shape[1], ref.shape[0])
    s, r, held, info, (sw, rw) = pairs.sample_pair(src, ref, src)
    corr = content_score(sw, rw)
    if corr < min_corr:
        raise Refused(f"the frames do not show the same content (luminance rank correlation {corr:.2f}, "
                      f"needs {min_corr:g}); unpaired transfer measures worse than no correction")
    if len(s) < 2000:
        raise Refused(f"only {len(s)} usable pixels after masking; too few to fit")
    cdl = fit_cdl(s, r, space)
    mid = np.clip(cdl_apply(s, cdl), 0, 1)
    train, test = ~held, held
    rng = np.random.default_rng(0)
    cap = lambda idx: rng.choice(idx, min(len(idx), 60000), replace=False)  # raster order is top of the frame first
    fit_idx, all_idx = cap(np.flatnonzero(train)), cap(np.arange(len(s)))
    de = lambda pred, tgt: delta_e2000(_lab(pred, space), _lab(tgt, space))
    base = de(mid[test], r[test]).mean()
    scored = []
    for k in STRENGTHS:
        d = fit_residual(mid[fit_idx], r[fit_idx], k)
        scored.append((de(apply_match(s[test], cdl, d), r[test]).mean(), k))
    best, k = min(scored)
    keep = best < base * 0.98 and base - best > MIN_GAIN
    delta = fit_residual(mid[all_idx], r[all_idx], k) if keep else None
    final = lambda x: apply_match(x, cdl, delta)
    errors = {"before": {"held-out": fit.stats(de(s[test], r[test])), "all": fit.stats(de(s, r))},
              "cdl": {"held-out": fit.stats(de(mid[test], r[test])), "all": fit.stats(de(mid, r))},
              "match": {"held-out": fit.stats(de(final(s[test]), r[test])), "all": fit.stats(de(final(s), r))}}
    return {"cdl": cdl, "delta": delta, "residual_strength": k if keep else None, "content_corr": round(corr, 3),
            "aligned": info["aligned"], "pixels": int(len(s)), "errors": errors, "space": space}


def report_md(m, name, src, ref):
    e = m["errors"]
    row = lambda label, k: (f"| {label} | " + " | ".join(
        f"{e[k][w][q]}" for w in ("held-out", "all") for q in ("median", "mean", "p95")) + " |")
    c = m["cdl"]
    v = lambda a: " ".join(f"{x:.4f}" for x in a)
    return "\n".join([
        f"# {name}: shot match", "", f"Source `{Path(src).name}` matched to reference `{Path(ref).name}`, in `{m['space']}` code values.", "",
        f"- content check: luminance rank correlation {m['content_corr']} ({'aligned' if m['aligned'] else 'alignment did not converge'})",
        f"- pixels used: {m['pixels']}", "",
        "## CDL", "", f"- slope {v(c['slope'])}", f"- offset {v(c['offset'])}", f"- power {v(c['power'])}",
        f"- saturation {c['sat']:.4f}", "",
        "## Residual LUT", "",
        (f"17-point, regularisation strength {m['residual_strength']:g}; kept because it beat the CDL alone on held-out pixels."
         if m["delta"] is not None else "Not used: it did not beat the CDL alone on held-out pixels, so the match is the CDL."), "",
        "## CIEDE2000 against the reference", "",
        "| | held-out median | mean | p95 | all median | mean | p95 |", "|---|---|---|---|---|---|---|",
        row("before", "before"), row("CDL", "cdl"), row("CDL + residual", "match"), "",
        "Held-out pixels are the other half of a checkerboard of 32 px blocks: not seen by the residual fit. "
        "The CDL has ten numbers and is fitted on all pixels.", ""])


def write(m, outdir, name, src, ref, size=cube.DEFAULT_SIZE, overwrite=False):
    """Write NAME.cc, NAME.cube, NAME-residual.cube (when the residual is kept) and NAME.report.md."""
    out = Path(outdir)
    if not overwrite:  # refuse before writing any of them
        clash = [f for f in (f"{name}.cc", f"{name}.cube", f"{name}-residual.cube", f"{name}.report.md") if (out / f).exists()]
        if clash:
            raise FileExistsError(", ".join(str(out / f) for f in clash))
    files = [cube.write_text(str(out / f"{name}.cc"), cdl_xml(m["cdl"], name), overwrite)]
    grid = cube.lattice(size)
    table = apply_match(grid.reshape(-1, 3), m["cdl"], m["delta"]).reshape(grid.shape)
    files.append(cube.write_cube(str(out / f"{name}.cube"), table, f"{name} shot match", overwrite=overwrite))
    if m["delta"] is not None:
        files.append(cube.write_cube(str(out / f"{name}-residual.cube"), residual_lut(m["delta"])["table"],
                                     f"{name} residual (after the CDL)", overwrite=overwrite))
    files.append(cube.write_text(str(out / f"{name}.report.md"), report_md(m, name, src, ref), overwrite))
    return files
