"""lutlab.model: a look as a few readable numbers, and how to evaluate it.

Order of operations, input code values to output code values:
  1. decode the input space to scene linear, Rec.709 primaries; exposure is
     e = log2(linear / 0.18), stops from mid grey. white_balance (optional)
     adapts the scene here, before the curve: temp in mireds (+ warmer),
     tint in thousandths of CIE 1960 v (+ magenta).
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


# --- white balance -----------------------------------------------------------

_BRADFORD = np.array([[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]])
D65_MIRED = 1e6 / 6504.0


def daylight_xy(cct):
    """CIE daylight locus chromaticity, valid 4000 K to 25000 K (clamped)."""
    t = float(np.clip(cct, 4000.0, 25000.0))
    if t <= 7000:
        x = -4.6070e9 / t ** 3 + 2.9678e6 / t ** 2 + 0.09911e3 / t + 0.244063
    else:
        x = -2.0064e9 / t ** 3 + 1.9018e6 / t ** 2 + 0.24748e3 / t + 0.237040
    return x, -3.0 * x * x + 2.870 * x - 0.275


def _xy_to_xyz(x, y):
    return np.array([x / y, 1.0, (1 - x - y) / y])


def white_balance_matrix(temp=0.0, tint=0.0):
    """Linear Rec.709 -> linear Rec.709 adaptation that renders a D65 neutral as
    the colour of a white `temp` mireds warmer and `tint` (x 0.001 v') more
    magenta. Both 0 is exactly the identity."""
    if not temp and not tint:
        return np.eye(3)
    src = _xy_to_xyz(*daylight_xy(1e6 / D65_MIRED))
    x, y = daylight_xy(1e6 / (D65_MIRED + temp))
    u, v = 4 * x / (-2 * x + 12 * y + 3), 6 * y / (-2 * x + 12 * y + 3)  # xy -> CIE 1960 uv
    v -= tint / 1000.0
    d = 2 * u - 8 * v + 4
    dst = _xy_to_xyz(3 * u / d, 2 * v / d)
    lms = _BRADFORD @ dst / (_BRADFORD @ src)
    m = np.linalg.inv(_BRADFORD) @ np.diag(lms) @ _BRADFORD
    x709 = spaces.XYZ["709"]
    return np.linalg.inv(x709) @ m @ x709


def white_balance(lin, wb):
    if not wb or not (wb.get("temp") or wb.get("tint")):
        return lin
    return np.einsum("ij,...j->...i", white_balance_matrix(wb.get("temp", 0.0), wb.get("tint", 0.0)), lin)


def base(p, codes, in_space=None):
    """Input codes -> tone-mapped output codes (no colour stage).

    tone.mode says what the curve is applied to. per_channel: each of R, G, B
    on its own, the film-like way, which also raises saturation where contrast
    is steep. luminance / max_rgb: the curve maps that one norm and the
    channels keep their scene ratios, so hue and scene saturation survive the
    curve and every colour change is left to the colour stage."""
    lin = white_balance(spaces.to_lin709(codes, in_space or p["in"]), p.get("white_balance"))
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


# --- looks as deltas on a base -----------------------------------------------

TONE_ADD = ("black", "white", "mid", "toe_start", "shoulder_start")
TONE_MUL = ("contrast", "toe_strength", "shoulder_strength")


def _grid(v):
    """A delta's band x hue entry: a number (every band and hue), {band: number},
    or {band: {hue: number}}. Returns {band: {hue: number or None}}."""
    out = {b: {h: None for h in HUES} for b in BANDS}
    if isinstance(v, (int, float)):
        return {b: {h: float(v) for h in HUES} for b in BANDS}
    for b, row in v.items():
        bands = BANDS if b == "all" else [b]
        for bb in bands:
            if bb not in out:
                raise ValueError(f"unknown band {b!r}; known: all, {', '.join(BANDS)}")
            if isinstance(row, (int, float)):
                out[bb] = {h: float(row) for h in HUES}
            else:
                for h, x in row.items():
                    if h not in out[bb]:
                        raise ValueError(f"unknown hue knot {h!r}; known: {', '.join(HUES)}")
                    out[bb][h] = float(x)
    return out


DELTA_KEYS = {"lutlab_delta", "name", "description", "white_balance", "tone", "hue_lum", "sat_gain",
              "sat_rolloff", "hue_shift", "split_tone"}


def apply_delta(p, d):
    """A look (params) plus a delta -> a new look. Additive: tone black, white,
    mid, toe_start, shoulder_start; hue_lum; hue_shift; split_tone (as OKLab
    vectors); white_balance temp and tint. Multiplicative: tone contrast and
    strengths; sat_gain; sat_rolloff. tone.mode replaces. An empty delta is
    the identity. The base's residual is kept: it is the detail of the base
    the numbers miss, and stays under the look."""
    unknown = set(d) - DELTA_KEYS
    if unknown:
        raise ValueError(f"unknown delta keys: {', '.join(sorted(unknown))}")
    p = copy.deepcopy(p)
    wb = dict(p.get("white_balance") or {})
    for k in ("temp", "tint"):
        wb[k] = round(wb.get(k, 0.0) + float(d.get("white_balance", {}).get(k, 0.0)), 4)
    if wb["temp"] or wb["tint"]:
        p["white_balance"] = wb
    for k, v in d.get("tone", {}).items():
        if k in TONE_ADD:
            p["tone"][k] = round(p["tone"][k] + v, 6)
        elif k in TONE_MUL:
            p["tone"][k] = round(p["tone"][k] * v, 6)
        elif k == "mode":
            if v not in TONE_MODES:
                raise ValueError(f"tone.mode must be one of {TONE_MODES}")
            p["tone"]["mode"] = v
        else:
            raise ValueError(f"unknown tone key {k!r}")
    for h, v in d.get("hue_lum", {}).items():
        if h not in p["hue_lum"]:
            raise ValueError(f"unknown hue knot {h!r}; known: {', '.join(HUES)}")
        p["hue_lum"][h] += float(v)
    for key, op in (("hue_shift", lambda a, b: a + b), ("sat_gain", lambda a, b: a * b)):
        if key in d:
            for b, row in _grid(d[key]).items():
                for h, v in row.items():
                    if v is not None:
                        p[key][b][h] = round(op(p[key][b][h], v), 6)
    for c, v in d.get("sat_rolloff", {}).items():
        if c not in p["sat_rolloff"]:
            raise ValueError(f"unknown sat_rolloff knot {c!r}; known: {', '.join(p['sat_rolloff'])}")
        p["sat_rolloff"][c] = round(p["sat_rolloff"][c] * v, 6)
    for lk, sv in d.get("split_tone", {}).items():
        if lk not in p["split_tone"]:
            raise ValueError(f"unknown split_tone level {lk!r}; known: {', '.join(p['split_tone'])}")
        cur = p["split_tone"][lk]
        vec = (cur["amount"] * np.cos(np.radians(cur["hue"])) + sv["amount"] * np.cos(np.radians(sv["hue"])),
               cur["amount"] * np.sin(np.radians(cur["hue"])) + sv["amount"] * np.sin(np.radians(sv["hue"])))
        p["split_tone"][lk] = {"hue": round(float(np.degrees(np.arctan2(vec[1], vec[0])) % 360), 3),
                               "amount": round(float(np.hypot(*vec)), 4)}
    p["look"] = d.get("name") or p.get("look")
    return p
