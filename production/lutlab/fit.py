"""lutlab.fit: fit the look model to samples (input codes -> target output codes).

One fitter serves both sources: the nodes of a LUT (decompose) and binned
pixels from image pairs (fit-pairs). Stages are fitted in model order, each on
what the earlier ones leave: tone on the neutral axis, split tone on the
near-neutral samples, then hue_lum, hue_shift, sat_gain and sat_rolloff on the
coloured samples. The colour fits are linear least squares with a smoothness
penalty across hue, so a sparsely sampled hue borrows from its neighbours
instead of going wild."""

import numpy as np
from scipy.optimize import least_squares

from . import model, spaces


def y_code(codes, out):
    """Output codes -> the code value of their luminance, as a grey would be encoded."""
    y = spaces.luminance(spaces.to_lin709(codes, out))
    return spaces.from_lin709(np.repeat(y[..., None], 3, -1), out)[..., 0]


def _tone_from_vector(v):
    black, white, mid, c, ft, fs, ltq, lsp = v
    return {"black": black, "white": white, "mid": mid, "contrast": c,
            "toe_start": -(1 - ft) * (mid - black) / c, "toe_strength": float(np.exp(ltq)),
            "shoulder_start": fs * (white - mid) / c, "shoulder_strength": float(np.exp(lsp))}


def fit_tone(lin, target, out, w, mode="per_channel"):
    """lin: scene-linear inputs (N, 3); target: output codes (N, 3). Fits the
    curve so the luminance of the per-channel result matches the target's."""
    ty = y_code(target, out)
    sw = np.sqrt(w)
    ey = model.exposure(spaces.luminance(lin))
    order = np.argsort(ey)
    at = lambda x: float(np.interp(x, ey[order], ty[order]))
    mid = min(max(at(0.0), 0.02), 0.98)
    c = min(max(at(0.5) - at(-0.5), 0.01), 0.9)
    lo, hi = float(np.min(ty)), float(np.max(ty))
    x0 = [lo, hi + 0.01, mid, c, 0.5, 0.5, np.log(2.0), np.log(2.0)]
    # black and white stay near the darkest and brightest outputs seen: past the
    # data's range the curve would otherwise trade white against shoulder freely
    bounds = ([lo - 0.05, hi - 0.02, 0.01, 1e-3, 0.01, 0.01, np.log(0.05), np.log(0.05)],
              [lo + 0.02, hi + 0.10, 0.99, 1.5, 0.99, 0.99, np.log(50), np.log(50)])
    x0 = np.clip(x0, np.array(bounds[0]) + 1e-6, np.array(bounds[1]) - 1e-6)

    probe = model.blank("lin709", out)

    def resid(v):
        t = _tone_from_vector(v)
        bad = max(t["black"] - t["mid"] + 1e-3, 0) + max(t["mid"] - t["white"] + 1e-3, 0)
        if bad > 0:  # graded, so the solver can climb back to black < mid < white
            return sw * (1.0 + 10.0 * bad)
        probe["tone"] = dict(t, mode=mode)
        return sw * (y_code(model.base(probe, lin), out) - ty)

    r = least_squares(resid, x0, bounds=bounds, x_scale="jac", max_nfev=4000)
    t = _tone_from_vector(r.x)
    t = {k: round(float(t[k]), 5) for k in model.TONE_KEYS}
    t["mode"] = mode
    return t


def _solve(A, y, w, regs):
    """argmin sum w (A x - y)^2 + sum lam |R x|^2, with lam scaled to the data weight."""
    Aw = A * w[:, None]
    lhs, rhs = A.T @ Aw, Aw.T @ y
    scale = max(float(w.sum()), 1e-9) / A.shape[1]
    for R, k in regs:
        lhs = lhs + k * scale * (R.T @ R)
    return np.linalg.solve(lhs, rhs)


def _second_diff(n, periodic):
    D = np.zeros((n if periodic else n - 2, n))
    for i in range(D.shape[0]):
        j = i if not periodic else i - 1
        for d, v in zip((0, 1, 2), (1.0, -2.0, 1.0)):
            D[i, (j + d) % n] = v
    return D


def _grid_regs(k_hue=0.002, k_band=0.001, k_ridge=1e-5):
    nb, nh = len(model.BANDS), len(model.HUES)
    dh = np.kron(np.eye(nb), _second_diff(nh, True))
    db = np.zeros(((nb - 1) * nh, nb * nh))
    for b in range(nb - 1):
        for h in range(nh):
            db[b * nh + h, b * nh + h], db[b * nh + h, (b + 1) * nh + h] = -1, 1
    return [(dh, k_hue), (db, k_band), (np.eye(nb * nh), k_ridge)]


def fit_split(base_lab, target_lab, w, chroma_max=0.02):
    """Grey drift (a, b) by lightness from samples whose base is near neutral."""
    _, cb, _ = spaces.lch(base_lab)
    wn = w * np.clip(1 - cb / chroma_max, 0, 1)
    keep = wn > 0
    A = model.hat_weights(target_lab[keep, 0], model.SPLIT_L)
    d = target_lab[keep, 1:] - base_lab[keep, 1:]
    n = len(model.SPLIT_L)
    regs = [(np.diff(np.eye(n), axis=0), 0.002), (np.eye(n), 1e-5)]  # first differences: unsampled knots hold the last value
    return np.stack([_solve(A, d[:, i], wn[keep], regs) for i in range(2)], -1)


def fit_colour(base_lab, target_lab, w, split):
    """hue_lum, hue_shift, sat_gain and sat_rolloff from coloured samples."""
    lb, cb, hb = spaces.lch(base_lab)
    lt = target_lab[..., 0]
    ab = target_lab[..., 1:] - model.hat_weights(lt, model.SPLIT_L) @ split
    ct, ht = np.hypot(ab[..., 0], ab[..., 1]), np.arctan2(ab[..., 1], ab[..., 0])
    keep = (w > 0) & (cb > 0.005) & (ct > 0.002)
    lb, cb, hb, lt, ct, ht, w = lb[keep], cb[keep], hb[keep], lt[keep], ct[keep], ht[keep], w[keep]
    g = model.grid_weights(lb, hb)
    wh = model.hue_weights(hb)
    nh = len(model.HUES)

    # hue_lum: lightness change grows with chroma up to HUE_LUM_C
    a_l = wh * np.minimum(cb / model.HUE_LUM_C, 1)[:, None]
    hlum = 100 * _solve(a_l, lt - lb, w * np.clip(cb / 0.03, 0, 1),
                        [(_second_diff(nh, True), 0.002), (np.eye(nh), 1e-5)])

    # hue_shift, weighted by chroma since hue is noise near grey
    dh = np.degrees(np.angle(np.exp(1j * (ht - hb))))
    wr = w * np.clip(np.minimum(cb, ct) / 0.04, 0, 1) ** 2
    rot = _solve(g, dh, wr, _grid_regs()).reshape(len(model.BANDS), nh)

    # sat_gain x sat_rolloff, in log chroma gain
    rw = model.roll_weights(cb)
    A = np.concatenate([g, rw], 1)
    regs = [(np.pad(R, ((0, 0), (0, rw.shape[1]))), k) for R, k in _grid_regs()]
    nr = rw.shape[1]
    dr = np.zeros((nr - 1, A.shape[1]))
    for i in range(nr - 1):
        dr[i, g.shape[1] + i], dr[i, g.shape[1] + i + 1] = -1, 1
    regs += [(dr, 0.01), (np.pad(np.eye(nr), ((0, 0), (g.shape[1], 0))), 1e-3)]
    x = _solve(A, np.log(ct / cb), w * np.clip(cb / 0.03, 0, 1), regs)
    sat = np.exp(x[:g.shape[1]]).reshape(len(model.BANDS), nh)
    roll = np.exp(x[g.shape[1]:])
    return {"rot": rot, "sat": sat, "roll": roll, "hlum": hlum}


def fit(in_space, out, codes, target, w, tone_codes, tone_target, tone_w, mode=None):
    """Fit every stage. With mode None, try each tone mode and keep the one whose
    full model is closest to the target (mean Delta E 2000 on the weighted samples).
    Returns params (without residual or fit_error)."""
    if mode is None:
        best = None
        for m in model.TONE_MODES:
            p = fit(in_space, out, codes, target, w, tone_codes, tone_target, tone_w, m)
            score = errors(p, codes, target, w > 0)["full"]["mean"]
            if best is None or score < best[0]:
                best = (score, p)
        return best[1]
    p = model.blank(in_space, out)
    lin_t = spaces.to_lin709(tone_codes, in_space)
    p["tone"] = fit_tone(lin_t, tone_target, out, tone_w, mode)
    lab = lambda c: spaces.oklab(spaces.to_lin709(c, out))
    base_t, tgt_t = lab(model.base(p, tone_codes)), lab(tone_target)
    split = fit_split(base_t, tgt_t, tone_w)
    base_c, tgt_c = lab(model.base(p, codes)), lab(target)
    a = fit_colour(base_c, tgt_c, w, split)
    a["split"] = split
    return model.set_arrays(p, a)


def errors(p, codes, target, mask=None):
    """Delta E 2000 (CIELAB, D65) between the model at each stage and the target."""
    m = np.ones(codes.shape[:-1], bool) if mask is None else mask
    ref = spaces.cielab(np.maximum(spaces.to_lin709(np.clip(target[m], 0, 1), p["out"]), 0))
    out = {}
    for stage in ("tone", "split", "full"):
        pred = np.clip(model.evaluate(p, codes[m], stage=stage), 0, 1)
        de = spaces.delta_e2000(ref, spaces.cielab(np.maximum(spaces.to_lin709(pred, p["out"]), 0)))
        out[stage] = stats(de)
    return out


def stats(de):
    de = np.asarray(de).ravel()
    if de.size == 0:
        return {"n": 0}
    return {"n": int(de.size), "median": round(float(np.median(de)), 3), "mean": round(float(de.mean()), 3),
            "p95": round(float(np.percentile(de, 95)), 3), "max": round(float(de.max()), 3)}
