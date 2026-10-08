"""rpstills.measure: colour measurement on rendered stills, for grading QA.

Skin is sampled where it is reliable: the middle of the largest face box
(inner SHRINK of its width and height, centred a little below the box
centre to stay off the hairline), taking the per-channel median so a
highlight or a lash does not move it. Everything is CIELAB (D65) from sRGB
values. fit_look() finds the exposure and white balance gains that move a
measured skin colour onto a target, in sRGB-linear light as a first guess;
the closed loop in Resolve corrects the remaining difference between sRGB
and DaVinci Wide Gamut primaries."""

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
