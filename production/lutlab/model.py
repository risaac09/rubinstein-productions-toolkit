"""lutlab.model: a look as a few readable numbers, and how to evaluate it.

Order of operations, input code values to output code values:
  1. decode the input space to scene linear, Rec.709 primaries; exposure is
     e = log2(linear / 0.18), stops from mid grey.
  2. tone: the same filmic curve on each channel, stops -> output code value.
  3. colour, in OKLab on the tone-mapped result:
       hue_lum      lightness change by hue, in L points (x100) at chroma 0.10
       sat_gain     chroma gain by hue and lightness band, times sat_rolloff,
                    an extra gain by chroma (compression of strong colours)
       hue_shift    hue rotation in degrees by hue and lightness band
       split_tone   a tint added to everything by lightness: hue in degrees and
                    amount in OKLab chroma points (x100)
  4. residual: whatever the numbers above miss, a 3D table of deltas in the
     output code values, added back to rebuild the source exactly.
Every value at its default is the identity for that stage.

OKLab hue angles for orientation: sRGB red ~29, orange ~55 (skin sits about
45-70), yellow ~110, green ~142, cyan ~195, blue ~264, magenta ~328."""

import copy

import numpy as np

from . import spaces

HUES = ["000 pink", "030 red", "060 orange", "090 amber", "120 yellow", "150 green",
        "180 teal", "210 cyan", "240 azure", "270 blue", "300 violet", "330 magenta"]
HUE_STEP = 30.0
BANDS = ["shadows", "mids", "highlights"]
BAND_L = np.array([0.35, 0.60, 0.85])          # OKLab L at the centre of each band
SPLIT_L = np.array([0.10, 0.25, 0.40, 0.55, 0.70, 0.85, 1.00])
ROLL_C = np.array([0.08, 0.16, 0.24, 0.32])    # OKLab chroma knots; gain is 1 at chroma 0
HUE_LUM_C = 0.10
MID_GREY = 0.18
TONE_KEYS = ["black", "white", "mid", "contrast", "toe_start", "toe_strength", "shoulder_start", "shoulder_strength"]
OUT_SPACES = ("r709", "srgb")


def identity_tone(out="r709"):
    """A tone curve close to a plain display encode of mid grey, as a starting point."""
    mid = float(spaces.from_lin709(np.array([MID_GREY]), out)[0])
    return {"black": 0.0, "white": 1.0, "mid": round(mid, 4), "contrast": 0.13, "toe_start": -3.0,
            "toe_strength": 2.0, "shoulder_start": 2.0, "shoulder_strength": 2.0}


def blank(in_space="r709", out="r709"):
    return {
        "lutlab": 1,
        "in": in_space,
        "out": out,
        "tone": identity_tone(out),
        "hue_lum": {h: 0.0 for h in HUES},
        "sat_gain": {b: {h: 1.0 for h in HUES} for b in BANDS},
        "sat_rolloff": {f"C={c:.2f}": 1.0 for c in ROLL_C},
        "hue_shift": {b: {h: 0.0 for h in HUES} for b in BANDS},
        "split_tone": {f"L={l:.2f}": {"hue": 0.0, "amount": 0.0} for l in SPLIT_L},
    }


# --- params <-> arrays -------------------------------------------------------

def arrays(p):
    rot = np.array([[p["hue_shift"][b][h] for h in HUES] for b in BANDS], dtype=np.float64)
    sat = np.array([[p["sat_gain"][b][h] for h in HUES] for b in BANDS], dtype=np.float64)
    roll = np.array([p["sat_rolloff"][f"C={c:.2f}"] for c in ROLL_C], dtype=np.float64)
    hlum = np.array([p["hue_lum"][h] for h in HUES], dtype=np.float64)
    st = [p["split_tone"][f"L={l:.2f}"] for l in SPLIT_L]
    hue = np.radians([s["hue"] for s in st])
    amt = np.array([s["amount"] for s in st]) / 100
    split = np.stack([amt * np.cos(hue), amt * np.sin(hue)], -1)
    return {"rot": rot, "sat": sat, "roll": roll, "hlum": hlum, "split": split}


def set_arrays(p, a, digits=3):
    p = copy.deepcopy(p)
    r = lambda v: round(float(v), digits)
    for i, b in enumerate(BANDS):
        for j, h in enumerate(HUES):
            p["hue_shift"][b][h] = r(a["rot"][i, j])
            p["sat_gain"][b][h] = r(a["sat"][i, j])
    for c, v in zip(ROLL_C, a["roll"]):
        p["sat_rolloff"][f"C={c:.2f}"] = r(v)
    for h, v in zip(HUES, a["hlum"]):
        p["hue_lum"][h] = r(v)
    for l, (da, db) in zip(SPLIT_L, a["split"]):
        p["split_tone"][f"L={l:.2f}"] = {"hue": r(np.degrees(np.arctan2(db, da)) % 360), "amount": r(100 * np.hypot(da, db))}
    return p


# --- interpolation weights (linear in the knot values, so fitting is linear) -

def hue_weights(h_rad):
    """Periodic Catmull-Rom weights on the 12 hue knots, shape (..., 12)."""
    x = (np.degrees(h_rad) % 360) / HUE_STEP
    k = np.floor(x).astype(int)
    t = x - k
    w = [(-t ** 3 + 2 * t ** 2 - t) / 2, (3 * t ** 3 - 5 * t ** 2 + 2) / 2,
         (-3 * t ** 3 + 4 * t ** 2 + t) / 2, (t ** 3 - t ** 2) / 2]
    out = np.zeros(np.shape(x) + (len(HUES),))
    for d, wd in zip((-1, 0, 1, 2), w):  # four distinct knots, so no index repeats
        np.put_along_axis(out, ((k + d) % len(HUES))[..., None], wd[..., None], -1)
    return out


def hat_weights(x, knots):
    """Piecewise-linear weights on knots, clamped at both ends, shape (..., K)."""
    x = np.clip(x, knots[0], knots[-1])
    k = np.clip(np.searchsorted(knots, x, side="right") - 1, 0, len(knots) - 2)
    t = (x - knots[k]) / (knots[k + 1] - knots[k])
    out = np.zeros(np.shape(x) + (len(knots),))
    np.put_along_axis(out, k[..., None], (1 - t)[..., None], -1)
    np.put_along_axis(out, (k + 1)[..., None], t[..., None], -1)
    return out


def roll_weights(c):
    """Weights on ROLL_C for the log chroma gain; zero at chroma 0, held past the last knot."""
    w = hat_weights(c, np.concatenate([[0.0], ROLL_C]))
    return w[..., 1:]


def grid_weights(L, h):
    """Band x hue weights, shape (..., 36), band-major."""
    wl = hat_weights(L, BAND_L)
    wh = hue_weights(h)
    return (wl[..., :, None] * wh[..., None, :]).reshape(np.shape(L) + (len(BANDS) * len(HUES),))


# --- tone --------------------------------------------------------------------

def tone_curve(e, t):
    """Filmic curve on exposure e (stops from mid grey) -> output code value.
    Straight in the middle (slope `contrast` code values per stop through `mid`
    at e = 0); roll-offs into `black` below toe_start and `white` above
    shoulder_start, with matching slope at the joins. Strength is the shape of
    the tail: large (toward 50) settles into black or white within a couple of
    stops, like an exponential; small (toward 0.05) keeps creeping for many
    stops, a long soft tail."""
    mid, c = t["mid"], max(t["contrast"], 1e-6)
    black, white = t["black"], t["white"]
    ts = max(t["toe_start"], (black - mid) / c * 0.999)
    ss = min(t["shoulder_start"], (white - mid) / c * 0.999)
    yt, ys = mid + c * ts, mid + c * ss
    tq, sp = max(t["toe_strength"], 1e-3), max(t["shoulder_strength"], 1e-3)
    y = mid + c * e
    with np.errstate(over="ignore", invalid="ignore"):
        hi = white - (white - ys) * (1 + c * np.maximum(e - ss, 0) / (sp * (white - ys))) ** -sp
        lo = black + (yt - black) * (1 + c * np.maximum(ts - e, 0) / (tq * (yt - black))) ** -tq
    return np.where(e > ss, hi, np.where(e < ts, lo, y))


def exposure(lin):
    return np.log2(np.maximum(lin, MID_GREY * 2.0 ** -24) / MID_GREY)


# --- evaluation --------------------------------------------------------------

TONE_MODES = ("per_channel", "luminance", "max_rgb")


def base(p, codes, in_space=None):
    """Input codes -> tone-mapped output codes (no colour stage).

    tone.mode says what the curve is applied to. per_channel: each of R, G, B
    on its own, the film-like way, which also raises saturation where contrast
    is steep. luminance / max_rgb: the curve maps that one norm and the
    channels keep their scene ratios, so hue and scene saturation survive the
    curve and every colour change is left to the colour stage."""
    lin = spaces.to_lin709(codes, in_space or p["in"])
    mode = p["tone"].get("mode", "per_channel")
    if mode == "per_channel":
        return tone_curve(exposure(lin), p["tone"])
    if mode not in TONE_MODES:
        raise ValueError(f"tone.mode must be one of {TONE_MODES}")
    norm = spaces.luminance(lin) if mode == "luminance" else lin.max(-1)
    grey = tone_curve(exposure(norm), p["tone"])
    disp = spaces.to_lin709(np.repeat(grey[..., None], 3, -1), p["out"])[..., 0]
    ratio = lin / np.where(norm > 1e-9, norm, 1.0)[..., None]
    ratio = np.where((norm > 1e-9)[..., None], ratio, 1.0)
    return spaces.from_lin709(ratio * disp[..., None], p["out"])


def colour(p, lab, stage="full"):
    """OKLab in -> OKLab out. stage 'tone' returns lab unchanged, 'split' adds only the split tone."""
    if stage == "tone":
        return lab
    a = arrays(p)
    L, C, h = spaces.lch(lab)
    if stage == "full":
        g = grid_weights(L, h)
        rot = g @ a["rot"].reshape(-1)
        logs = g @ np.log(np.maximum(a["sat"], 1e-6)).reshape(-1) + roll_weights(C) @ np.log(np.maximum(a["roll"], 1e-6))
        dl = (hue_weights(h) @ a["hlum"]) / 100 * np.minimum(C / HUE_LUM_C, 1)
        L, C, h = L + dl, C * np.exp(logs), h + np.radians(rot)
    ab = hat_weights(L, SPLIT_L) @ a["split"]
    return np.stack([L, C * np.cos(h) + ab[..., 0], C * np.sin(h) + ab[..., 1]], -1)


def check(p):
    """Refuse a tone curve that cannot be monotonic, e.g. after a hand edit."""
    t = p["tone"]
    if not (t["black"] < t["mid"] < t["white"]) or t["contrast"] <= 0:
        raise ValueError(f"tone needs black < mid < white and contrast > 0; got black {t['black']}, mid {t['mid']}, "
                         f"white {t['white']}, contrast {t['contrast']}")
    if min(t["toe_strength"], t["shoulder_strength"]) <= 0:
        raise ValueError("tone strengths must be positive")


def evaluate(p, codes, in_space=None, stage="full"):
    """Input codes (..., 3) in `in_space` (default the look's own) -> output codes, unclipped."""
    if p["out"] not in OUT_SPACES:
        raise ValueError(f"out space must be one of {OUT_SPACES}")
    b = base(p, codes, in_space)
    if stage == "tone":
        return b
    lab = colour(p, spaces.oklab(spaces.to_lin709(b, p["out"])), stage)
    return spaces.from_lin709(spaces.oklab_to_lin709(lab), p["out"])
