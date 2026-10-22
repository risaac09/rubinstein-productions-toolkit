"""rpstills.background: find the subject and burn the background down.

    mask, info = background.subject_mask(rgb, face_px)   # float32 HxW 0..1, or None and info["reason"]
    mask, info = background.subject_mask(rgb, face_px, region)  # only the window (x0, y0, x1, y1): finish's crops
    rgb, report = background.burn(rgb, mask)             # BURN_EV and BURN_DESAT outside the subject

rgb is float32 sRGB 0..1, HxWx3; face_px is the subject's face in pixels
(skin.to_pixels; landmarks are not needed here).

subject_mask runs OpenCV GrabCut on a copy at SCALE of the frame, seeded
from the face box:
  certain subject     the face core (FACE_CORE, shares of the box)
  probable subject    a head ellipse (HEAD_AXES of the box) and a torso
                      trapezoid from TORSO_TOP face heights below the box top
                      to the frame bottom (TORSO_TOP_X, TORSO_BOTTOM_X)
  certain background  a strip BORDER of the width along the top and both
                      sides (not the bottom: the torso runs off it)
OpenCV's RNG is seeded (cv2.setRNGSeed(SEED)) first, so a run repeats. The
result is closed, dilated a little to keep flyaway hair out of the burn,
scaled up and feathered by a Gaussian of face width / FEATHER_DIV.

It refuses (None, with info["reason"]) rather than hand back a mask that
would burn the subject or nothing: when the face core is too small to seed,
when GrabCut fails, when the mask covers less than CORE_MIN_COVER of the
face core, or less than MIN_SHARE or more than MAX_SHARE of the frame.

burn() works in linear light: background pixels go down by ev stops and
toward their channel mean by desat; pixels the mask holds at 1 are left
bit-identical. It is per pixel, so it can run on a crop of the frame:
subject_mask(..., region) builds the mask only there (the GrabCut seed and
result are the frame's), and the part of the region more than feather_margin()
from its edge equals the full-frame mask exactly.

Tuned on head-and-shoulders portraits against an open background; a busy
background close to the subject's tones, or hands and props, are where
GrabCut goes wrong. Check the result; nothing runs this unless asked.
"""

import cv2
import numpy as np

SCALE = 0.25                     # GrabCut runs on the frame scaled by this
ITERATIONS = 6
SEED = 0                         # cv2.setRNGSeed: GrabCut's GMM init is random
FACE_CORE = (0.15, 0.10, 0.85, 0.90)  # certain subject: x0, y0, x1, y1 as shares of the face box
HEAD_AXES = (0.95, 1.05)         # head ellipse semi-axes, face box widths and heights
TORSO_TOP = 1.2                  # torso starts this many face heights below the box top
TORSO_TOP_X = (-0.3, 1.3)        # its top edge, in face widths from the box's left edge
TORSO_BOTTOM_X = (-1.2, 2.2)     # its bottom edge (at the frame bottom), likewise
BORDER = 0.03                    # certain background strip, share of the scaled width (at least 4 px)
CLOSE_DIV = 30                   # closing kernel = scaled face width / CLOSE_DIV (at least 3 px)
DILATE_DIV = 42                  # then a dilation of scaled face width / DILATE_DIV (at least 3 px)
FEATHER_DIV = 60                 # feather sigma at full size = face width / FEATHER_DIV
CORE_MIN_PX = 16                 # a face core smaller than this at SCALE cannot seed GrabCut
CORE_MIN_COVER = 0.95            # refuse when the mask covers less of the face core
MIN_SHARE, MAX_SHARE = 0.05, 0.90  # refuse a mask outside this share of the frame
BURN_EV = -0.6
BURN_DESAT = 0.15


def params():
    return {"scale": SCALE, "iterations": ITERATIONS, "seed": SEED, "face_core": list(FACE_CORE),
            "head_axes": list(HEAD_AXES), "torso_top": TORSO_TOP, "torso_top_x": list(TORSO_TOP_X),
            "torso_bottom_x": list(TORSO_BOTTOM_X), "border": BORDER, "close": f"w/{CLOSE_DIV}",
            "dilate": f"w/{DILATE_DIV}", "feather": f"w/{FEATHER_DIV}", "core_min_px": CORE_MIN_PX,
            "core_min_cover": CORE_MIN_COVER, "share": [MIN_SHARE, MAX_SHARE],
            "burn_ev": BURN_EV, "burn_desat": BURN_DESAT}


def to_linear(v):
    return np.where(v <= 0.04045, v / 12.92, ((np.maximum(v, 0) + 0.055) / 1.055) ** 2.4)


def to_srgb(v):
    v = np.maximum(v, 0)
    return np.where(v <= 0.0031308, v * 12.92, 1.055 * v ** (1 / 2.4) - 0.055)


def _core(face_px, scale):
    """(x0, y0, x1, y1) of the face core at scale, as ints."""
    x, y, w, h = (face_px[k] * scale for k in ("x", "y", "w", "h"))
    a, b, c, d = FACE_CORE
    return int(round(x + a * w)), int(round(y + b * h)), int(round(x + c * w)), int(round(y + d * h))


def seed_mask(shape, face_px, scale=SCALE):
    """The GrabCut seed (uint8 GC_* labels) for an image of `shape` (already
    scaled), from a full-size face scaled by `scale`."""
    sh, sw = shape[:2]
    x, y, w, h = (face_px[k] * scale for k in ("x", "y", "w", "h"))
    m = np.full((sh, sw), cv2.GC_PR_BGD, np.uint8)
    cv2.ellipse(m, (int(round(x + w / 2)), int(round(y + h / 2))),
                (max(1, int(round(HEAD_AXES[0] * w))), max(1, int(round(HEAD_AXES[1] * h)))),
                0, 0, 360, int(cv2.GC_PR_FGD), -1)
    top = y + TORSO_TOP * h
    torso = np.array([[x + TORSO_TOP_X[0] * w, top], [x + TORSO_TOP_X[1] * w, top],
                      [x + TORSO_BOTTOM_X[1] * w, sh], [x + TORSO_BOTTOM_X[0] * w, sh]])
    cv2.fillPoly(m, [np.round(torso).astype(np.int32)], int(cv2.GC_PR_FGD))
    x0, y0, x1, y1 = _core(face_px, scale)
    m[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = cv2.GC_FGD
    b = max(4, int(BORDER * sw))
    m[:b] = cv2.GC_BGD
    m[:, :b] = cv2.GC_BGD
    m[:, -b:] = cv2.GC_BGD
    return m


def check(binary, core):
    """Why a binary subject mask (HxW, 0/1) must not be used, or None. core
    is the face core box (x0, y0, x1, y1) in the mask's pixels."""
    x0, y0, x1, y1 = core
    patch = binary[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
    if patch.size < CORE_MIN_PX:
        return f"the face core is {patch.size} px at scale {SCALE}, too small to seed the subject mask"
    cover = float(patch.mean())
    if cover < CORE_MIN_COVER:
        return f"the mask covers {cover:.0%} of the face core (wanted {CORE_MIN_COVER:.0%})"
    share = float(binary.mean())
    if share < MIN_SHARE:
        return f"the mask covers {share:.1%} of the frame (under {MIN_SHARE:.0%}): not a subject"
    if share > MAX_SHARE:
        return f"the mask covers {share:.1%} of the frame (over {MAX_SHARE:.0%}): no background found"
    return None


def _taps(n_dst, n_src, lo, hi):
    """Linear-interpolation taps (low index, high index, weight of the high) of cv2.resize's mapping from an
    n_src row to n_dst, for destination pixels lo..hi."""
    f = (np.arange(lo, hi) + 0.5) * (n_src / n_dst) - 0.5
    i = np.floor(f).astype(np.int64)
    w = (f - i).astype(np.float32)
    return np.clip(i, 0, n_src - 1), np.clip(i + 1, 0, n_src - 1), w


def _resize_window(small, W, H, region):
    """cv2.resize(small, (W, H), INTER_LINEAR) evaluated only on region (x0, y0, x1, y1), separably, so the
    full-frame float mask is never built."""
    x0, y0, x1, y1 = region
    iy0, iy1, wy = _taps(H, small.shape[0], y0, y1)
    ix0, ix1, wx = _taps(W, small.shape[1], x0, x1)
    rows = small[iy0] * (1 - wy)[:, None] + small[iy1] * wy[:, None]
    return rows[:, ix0] * (1 - wx) + rows[:, ix1] * wx


def feather_margin(face_px):
    """Pixels of context a region needs round it for its mask to equal the full-frame mask: the Gaussian's
    radius (4 sigma for float32 in OpenCV) plus a pixel of resize support."""
    return int(np.ceil(4 * face_px["w"] / FEATHER_DIV)) + 2


def subject_mask(rgb, face_px, region=None):
    """(float32 mask, info) or (None, info with "reason"). The mask is HxW, or
    with region (x0, y0, x1, y1) in full-frame pixels only that window, equal
    to the full mask there when the window has feather_margin() of room round
    the part that matters (GrabCut still runs on the whole scaled frame)."""
    H, W = rgb.shape[:2]
    u8 = np.empty((H, W, 3), np.uint8)
    for i in range(0, H, 256):  # in stripes: a float copy of the whole frame is what cost the memory
        u8[i:i + 256] = np.clip(rgb[i:i + 256, :, :3], 0, 1) * 255 + 0.5
    small = cv2.resize(u8, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_AREA)
    del u8
    sh, sw = small.shape[:2]
    core = _core(face_px, SCALE)
    cx0, cy0 = max(0, core[0]), max(0, core[1])
    cx1, cy1 = min(sw, core[2]), min(sh, core[3])
    if (cx1 - cx0) * (cy1 - cy0) < CORE_MIN_PX or cx1 <= cx0 or cy1 <= cy0:
        return None, {"reason": f"the face core inside the frame is too small at scale {SCALE} to seed the "
                                "subject mask"}
    m = seed_mask(small.shape, face_px, SCALE)
    bg_model, fg_model = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.setRNGSeed(SEED)
    try:
        cv2.grabCut(small, m, None, bg_model, fg_model, ITERATIONS, cv2.GC_INIT_WITH_MASK)
    except cv2.error as e:
        return None, {"reason": f"GrabCut failed: {str(e).strip().splitlines()[-1]}"}
    sub = np.isin(m, (cv2.GC_FGD, cv2.GC_PR_FGD)).astype(np.uint8)
    fw = face_px["w"] * SCALE
    k = max(3, int(fw / CLOSE_DIV) | 1)
    sub = cv2.morphologyEx(sub, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    k = max(3, int(fw / DILATE_DIV) | 1)
    sub = cv2.dilate(sub, np.ones((k, k), np.uint8))
    reason = check(sub, (cx0, cy0, cx1, cy1))
    info = {"share": round(float(sub.mean()), 4),
            "core_cover": round(float(sub[cy0:cy1, cx0:cx1].mean()), 4), "scaled": [sw, sh]}
    if reason:
        info["reason"] = reason
        return None, info
    x0, y0, x1, y1 = region or (0, 0, W, H)
    if (x0, y0, x1, y1) == (0, 0, W, H):
        full = cv2.resize(sub.astype(np.float32), (W, H), interpolation=cv2.INTER_LINEAR)
    else:
        full = _resize_window(sub.astype(np.float32), W, H, region)
    return np.clip(cv2.GaussianBlur(full, (0, 0), face_px["w"] / FEATHER_DIV), 0, 1), info


def burn(rgb, mask, ev=BURN_EV, desat=BURN_DESAT):
    """Darken by ev stops (linear light) and desaturate by desat where mask
    is below 1. Returns (rgb float32, report)."""
    bg = 1 - np.clip(np.asarray(mask, np.float32), 0, 1)
    out = to_srgb(to_linear(rgb) * np.exp2(ev * bg)[..., None])
    out += (out.mean(2, keepdims=True) - out) * (desat * bg)[..., None]
    out = np.where((bg > 0)[..., None], np.clip(out, 0, 1), rgb).astype(np.float32)
    return out, {"ev": ev, "desat": desat, "background_share": round(float((bg > 0.5).mean()), 4)}
