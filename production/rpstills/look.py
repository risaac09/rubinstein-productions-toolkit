"""rpstills.look: a stills look as a 3D LUT in DaVinci Wide Gamut / Intermediate.

The Resolve projects here are colour managed with a DaVinci Wide Gamut,
DaVinci Intermediate (DWG/DI) timeline, and a stills timeline sets its output
colour space to sRGB itself, so a look needs no output transform. A node LUT
runs in the timeline space, so the LUT maps DI-encoded DWG to DI-encoded DWG.

The look is plain linear-light maths on each pixel, in this order:
  1. exposure_ev      a gain of 2**ev on all channels
  2. wb               per-channel gains (r, g, b), the white balance trim
  3. contrast         gamma-style contrast about mid grey (0.18)
  4. shoulder_knee    a smooth roll-off above the knee so lifted exposure does
                      not clip: identity at or below the knee, 1.0 as the limit
  5. saturation       about the luma of the pixel, in DI-encoded values
All defaults are the identity, so an unset look changes nothing.

Constants are the published DaVinci Intermediate curve."""

import json
import os

import numpy as np

DI_A, DI_B, DI_C, DI_M = 0.0075, 7.0, 0.07329248, 10.44426855
DI_LIN_CUT, DI_LOG_CUT = 0.00262409, 0.02740668
MID_GREY = 0.18
LUMA = np.array([0.2722287168, 0.6740817658, 0.0536895174])  # DWG luma weights

DEFAULTS = {"exposure_ev": 0.0, "wb": [1.0, 1.0, 1.0], "contrast": 1.0, "shoulder_knee": 1.0, "saturation": 1.0}


def di_encode(x):
    x = np.asarray(x, dtype=np.float64)
    return np.where(x <= DI_LIN_CUT, x * DI_M, (np.log2(np.maximum(x, 0) + DI_A) + DI_B) * DI_C)


def di_decode(y):
    y = np.asarray(y, dtype=np.float64)
    return np.where(y <= DI_LOG_CUT, y / DI_M, np.exp2(y / DI_C - DI_B) - DI_A)


def shoulder(x, knee):
    """Identity up to knee; above it 1 - (1 - knee) * exp(-(x - knee) / (1 - knee)).
    Slope is 1 at the knee and the output approaches 1.0. knee >= 1 disables it."""
    x = np.asarray(x, dtype=np.float64)
    if knee >= 1.0:
        return x
    room = 1.0 - knee
    return np.where(x <= knee, x, knee + room * (1.0 - np.exp(-(x - knee) / room)))


def apply(rgb_di, params):
    """DI-encoded DWG pixels (..., 3) to DI-encoded DWG pixels."""
    p = dict(DEFAULTS, **params)
    lin = di_decode(rgb_di)
    lin = lin * (2.0 ** p["exposure_ev"]) * np.asarray(p["wb"], dtype=np.float64)
    if p["contrast"] != 1.0:
        pos = np.maximum(lin, 0)
        lin = np.where(lin > 0, MID_GREY * (pos / MID_GREY) ** p["contrast"], lin)
    lin = shoulder(lin, p["shoulder_knee"])
    out = di_encode(lin)
    if p["saturation"] != 1.0:
        luma = (out * LUMA).sum(-1, keepdims=True)
        out = luma + p["saturation"] * (out - luma)
    return out


def cube_text(params, size=33, title="rpstills look"):
    grid = np.linspace(0.0, 1.0, size)
    # .cube order: red varies fastest
    b, g, r = np.meshgrid(grid, grid, grid, indexing="ij")
    rgb = np.stack([r, g, b], -1).reshape(-1, 3)
    out = apply(rgb, params)
    lines = [f'TITLE "{title}"', f"LUT_3D_SIZE {size}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    lines += [f"{a:.6f} {c:.6f} {d:.6f}" for a, c, d in out]
    return "\n".join(lines) + "\n"


def write_cube(path, params, size=33, title="rpstills look"):
    """Write the LUT beside nothing else: refuses to overwrite and refuses a git work tree
    is the caller's check; this function only refuses to overwrite."""
    if os.path.exists(path):
        raise FileExistsError(path)
    with open(path + ".part", "w") as f:
        f.write(cube_text(params, size, title))
    os.replace(path + ".part", path)
    return path


def trim_ev(skin_l, median_l, damp=0.5, limit=0.6, step=0.2):
    """Per-shot exposure trim in stops. A frame's skin L* against the session
    median (both measured on the neutral render) gives the exposure error; the
    trim corrects a share `damp` of it, is limited to +/- `limit` and snaps to
    `step`, so a session needs only a handful of LUT variants. Half the error
    keeps real differences between people and still pulls outliers in."""
    y = lambda l: ((l + 16.0) / 116.0) ** 3
    ev = -damp * float(np.log2(y(skin_l) / y(median_l)))
    ev = max(-limit, min(limit, ev))
    return round(round(ev / step) * step, 3) + 0.0


def load_params(path):
    with open(path) as f:
        return json.load(f)
