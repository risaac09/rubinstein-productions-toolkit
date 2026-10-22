"""rpstills.measure: colour measurement on rendered stills, for grading QA.

Skin is sampled where it is reliable: the middle of the largest face box
(inner SHRINK of its width and height, centred a little below the box
centre to stay off the hairline), taking the per-channel median so a
highlight or a lash does not move it. Everything is CIELAB (D65) from sRGB
values. fit_look() finds the exposure and white balance gains that move a
measured skin colour onto a target, in sRGB-linear light as a first guess;
the closed loop in Resolve corrects the remaining difference between sRGB
and DaVinci Wide Gamut primaries.

Landmark-based skin (for developed and finished files, not the index):
lab_image() is the same CIELAB maths per pixel on float sRGB 0..1;
skin_lab() is the mean Lab over the cheek apples (rpstills.skin, needs
landmarks) or the box sample above, with hue, chroma and the Rec.709
vectorscope angle. vectorscope_deg() reads the angle a scope draws: BT.709
Y'CbCr from the encoded values, angle of (Cb, Cr) counter-clockwise from +Cb,
so the skin-tone line sits near 123 degrees."""

import numpy as np

D65 = np.array([0.95047, 1.0, 1.08883])
M = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]])
SHRINK = 0.5
SKIN_CENTRE_Y = 0.55


def srgb_to_linear(c):
    c = np.asarray(c, float) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def lab(rgb):
    """sRGB 0..255 (3,) to CIELAB (3,)."""
    xyz = (M @ srgb_to_linear(rgb)) / D65
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.array([116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])])


def face_region(upright, crop_px, face, out_w):
    """Pixel box of the skin sample inside a rendered crop. upright is the
    full-frame (W, H); crop_px is [x, y, w, h] on that frame; face is a
    normalized Vision box; out_w the rendered width."""
    W, H = upright
    x, y, w, _h = crop_px
    fx, fy, fw, fh = face["x"] * W, face["y"] * H, face["w"] * W, face["h"] * H
    cx, cy = fx + fw / 2, fy + fh * SKIN_CENTRE_Y
    rw, rh = fw * SHRINK, fh * SHRINK
    s = out_w / float(w)
    return [int((cx - rw / 2 - x) * s), int((cy - rh / 2 - y) * s), int((cx + rw / 2 - x) * s), int((cy + rh / 2 - y) * s)]


def skin_rgb(img, box):
    """Median sRGB of a PIL image region, or None when the box is not inside the image."""
    w, h = img.size
    if box[0] < 0 or box[1] < 0 or box[2] > w or box[3] > h or box[2] <= box[0] or box[3] <= box[1]:
        return None
    a = np.asarray(img.convert("RGB").crop(box), float).reshape(-1, 3)
    return np.median(a, 0)


def fit_look(skin_srgb, target_lab, ev_range=(-1.0, 2.5), gain_range=(0.6, 1.6), steps=36):
    """(exposure_ev, [r, g, b] gains) that move skin_srgb to target_lab, green gain fixed at 1.
    Grid search with a refinement pass; returns (ev, wb, residual_lab_distance)."""
    lin = srgb_to_linear(skin_srgb)

    def err(ev, gr, gb):
        out = np.clip(lin * (2.0 ** ev) * np.array([gr, 1.0, gb]), 0, None)
        enc = np.where(out <= 0.0031308, out * 12.92, 1.055 * out ** (1 / 2.4) - 0.055) * 255.0
        return float(np.linalg.norm(lab(enc) - np.asarray(target_lab)))

    best = (1e9, 0.0, 1.0, 1.0)
    ev_grid = np.linspace(*ev_range, steps)
    g_grid = np.linspace(*gain_range, steps)
    for ev in ev_grid:
        for gr in g_grid:
            for gb in g_grid:
                e = err(ev, gr, gb)
                if e < best[0]:
                    best = (e, ev, gr, gb)
    for scale in (0.15, 0.04):
        e0, ev0, gr0, gb0 = best
        for ev in np.linspace(ev0 - scale * 3, ev0 + scale * 3, 15):
            for gr in np.linspace(gr0 - scale, gr0 + scale, 15):
                for gb in np.linspace(gb0 - scale, gb0 + scale, 15):
                    e = err(ev, gr, gb)
                    if e < best[0]:
                        best = (e, ev, gr, gb)
    e, ev, gr, gb = best
    return round(float(ev), 3), [round(float(gr), 4), 1.0, round(float(gb), 4)], round(e, 2)


BT709 = (0.2126, 0.7152, 0.0722)
CB_DIV, CR_DIV = 1.8556, 1.5748  # BT.709 Cb = (B' - Y') / 1.8556, Cr = (R' - Y') / 1.5748


def lab_image(rgb):
    """float sRGB 0..1 (..., 3) to CIELAB (..., 3) float32: lab() per pixel."""
    c = np.asarray(rgb, np.float32)[..., :3]
    lin = np.where(c <= 0.04045, c / 12.92, ((np.maximum(c, 0) + 0.055) / 1.055) ** 2.4)
    xyz = (lin @ M.T.astype(np.float32)) / D65.astype(np.float32)
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])],
                    -1).astype(np.float32)


def lab_to_srgb(lab_values):
    """CIELAB (..., 3) to float sRGB (..., 3), the inverse of lab_image. Not
    clipped: out-of-gamut colours come back below 0 or above 1."""
    v = np.asarray(lab_values, float)
    fy = (v[..., 0] + 16) / 116
    f = np.stack([fy + v[..., 1] / 500, fy, fy - v[..., 2] / 200], -1)
    xyz = np.where(f ** 3 > 0.008856, f ** 3, (f - 16 / 116) / 7.787) * D65
    lin = xyz @ np.linalg.inv(M).T
    return np.where(lin <= 0.0031308, lin * 12.92, 1.055 * np.maximum(lin, 0) ** (1 / 2.4) - 0.055)


def hue_deg(a, b):
    """CIELAB hue angle in degrees, 0..360."""
    return np.degrees(np.arctan2(b, a)) % 360


def chroma(a, b):
    return np.hypot(a, b)


def vectorscope_deg(rgb_srgb_mean):
    """Angle in degrees (0..360) of an encoded sRGB colour on a Rec.709
    vectorscope. Any scale (0..1 or 0..255): the angle does not change."""
    r, g, b = (float(x) for x in np.asarray(rgb_srgb_mean, float)[:3])
    y = BT709[0] * r + BT709[1] * g + BT709[2] * b
    return float(np.degrees(np.arctan2((r - y) / CR_DIV, (b - y) / CB_DIV)) % 360)


def box_mask(face_px, shape):
    """bool HxW: the box sample (inner SHRINK of the face box, centred at
    SKIN_CENTRE_Y of its height), clipped to the image."""
    H, W = shape[:2]
    cx, cy = face_px["x"] + face_px["w"] / 2, face_px["y"] + face_px["h"] * SKIN_CENTRE_Y
    rw, rh = face_px["w"] * SHRINK / 2, face_px["h"] * SHRINK / 2
    m = np.zeros((H, W), bool)
    y0, y1, x0, x1 = (max(0, int(round(float(v)))) for v in (cy - rh, cy + rh, cx - rw, cx + rw))
    m[y0:y1, x0:x1] = True
    return m


def skin_lab(rgb, face_px, region="cheek"):
    """Mean skin colour of a float sRGB 0..1 image over the cheek apples
    (region "cheek", needs landmarks) or the box sample (region "box"):
    {"L", "a", "b", "hue_deg", "chroma", "vectorscope_deg", "n_px"}, rounded.
    ValueError when the sample holds no pixel."""
    if region == "cheek":
        from .skin import cheek_mask  # lazy: keeps this module numpy-only for the LUT tools
        m = cheek_mask(face_px, rgb.shape)
    elif region == "box":
        m = box_mask(face_px, rgb.shape)
    else:
        raise ValueError(f"unknown skin region {region!r}")
    n = int(m.sum())
    if not n:
        raise ValueError("the skin sample holds no pixel")
    px = np.asarray(rgb, np.float32)[..., :3][m]
    L, a, b = (float(x) for x in lab_image(px).mean(0))
    return {"L": round(L, 2), "a": round(a, 2), "b": round(b, 2), "hue_deg": round(float(hue_deg(a, b)), 2),
            "chroma": round(float(chroma(a, b)), 2), "vectorscope_deg": round(vectorscope_deg(px.mean(0)), 2),
            "n_px": n}
