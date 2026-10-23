"""
rpresolve.color: the colour math measure and match share. numpy only.

Display values are Rec.709 primaries with a pure 2.4 gamma (the house
delivery encoding, BT.1886 with a zero black level), D65 white. Lab uses
the same D65 white, so a neutral stays at a* = b* = 0.

delta_e_2000 follows Sharma, Wu and Dalal (2005) and is checked in the
tests against pairs from their published table.
"""

import numpy as np


def _dot3(rgb, weights):
    """rgb[..., :3] . weights without BLAS: numpy 2.0 on macOS Accelerate
    raises spurious overflow warnings from matmul on valid data."""
    return rgb[..., 0] * weights[0] + rgb[..., 1] * weights[1] + rgb[..., 2] * weights[2]


REC709_TO_XYZ = np.array([
    [0.4124564, 0.3575761, 0.1804375],
    [0.2126729, 0.7151522, 0.0721750],
    [0.0193339, 0.1191920, 0.9503041],
])
D65 = np.array([0.95047, 1.0, 1.08883])
REC709_LUMA = np.array([0.2126, 0.7152, 0.0722])


def rec709_to_lab(rgb, gamma=2.4):
    """Display-encoded Rec.709 RGB (0..1, any leading shape) to CIE Lab."""
    rgb = np.clip(np.asarray(rgb, dtype=float), 0.0, 1.0)
    lin = rgb ** gamma
    xyz = np.stack([_dot3(lin, row) for row in REC709_TO_XYZ], axis=-1)
    t = xyz / D65
    eps = (6 / 29) ** 3
    f = np.where(t > eps, np.cbrt(t), t / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16,
                     500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])], axis=-1)


def chroma(lab):
    lab = np.asarray(lab, dtype=float)
    return np.hypot(lab[..., 1], lab[..., 2])


def delta_e_2000(lab1, lab2):
    """CIEDE2000 between Lab arrays of the same shape (kL = kC = kH = 1)."""
    lab1, lab2 = np.asarray(lab1, dtype=float), np.asarray(lab2, dtype=float)
    L1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    L2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]
    c_bar = (np.hypot(a1, b1) + np.hypot(a2, b2)) / 2
    g = 0.5 * (1 - np.sqrt(c_bar ** 7 / (c_bar ** 7 + 25.0 ** 7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360
    zero = (c1p * c2p) == 0
    dh = h2p - h1p
    dh = np.where(dh > 180, dh - 360, np.where(dh < -180, dh + 360, dh))
    dh = np.where(zero, 0.0, dh)
    d_l, d_c = L2 - L1, c2p - c1p
    d_h = 2 * np.sqrt(c1p * c2p) * np.sin(np.radians(dh / 2))
    l_bar, cp_bar = (L1 + L2) / 2, (c1p + c2p) / 2
    hsum = h1p + h2p
    h_bar = np.where(zero, hsum,
                     np.where(np.abs(h1p - h2p) <= 180, hsum / 2,
                              np.where(hsum < 360, (hsum + 360) / 2, (hsum - 360) / 2)))
    t = (1 - 0.17 * np.cos(np.radians(h_bar - 30)) + 0.24 * np.cos(np.radians(2 * h_bar))
         + 0.32 * np.cos(np.radians(3 * h_bar + 6)) - 0.20 * np.cos(np.radians(4 * h_bar - 63)))
    s_l = 1 + 0.015 * (l_bar - 50) ** 2 / np.sqrt(20 + (l_bar - 50) ** 2)
    s_c = 1 + 0.045 * cp_bar
    s_h = 1 + 0.015 * cp_bar * t
    r_t = (-2 * np.sqrt(cp_bar ** 7 / (cp_bar ** 7 + 25.0 ** 7))
           * np.sin(np.radians(60 * np.exp(-((h_bar - 275) / 25) ** 2))))
    return np.sqrt((d_l / s_l) ** 2 + (d_c / s_c) ** 2 + (d_h / s_h) ** 2
                   + r_t * (d_c / s_c) * (d_h / s_h))


def luma_ire(rgb):
    """Rec.709 luma of display-encoded RGB (0..1), in IRE (0..100)."""
    return _dot3(np.asarray(rgb, dtype=float), REC709_LUMA) * 100.0


def vectorscope_angle(rgb):
    """Hue angle on a vectorscope, degrees counterclockwise from +Cb (the
    B-Y axis), from display RGB. The skin-tone line sits near 123 degrees."""
    rgb = np.asarray(rgb, dtype=float)
    y = _dot3(rgb, REC709_LUMA)
    cb = (rgb[..., 2] - y) / 1.8556
    cr = (rgb[..., 0] - y) / 1.5748
    return np.degrees(np.arctan2(cr, cb)) % 360


# Camera transfer curves. Signal values are normalised 0..1 (10-bit code / 1023).
# V-Log: Panasonic, "V-Log/V-Gamut Reference Manual" Rev 1.0 (2014-11-28). The
# constants are cross-checked against the open-source `colour` library; the
# manual's PDF was not readable offline, so the tests pin its 10-bit table
# (0% = 128, 18% grey = 433, 90% reflection = 602) instead.
VLOG_CUT1, VLOG_CUT2 = 0.01, 0.181
VLOG_B, VLOG_C, VLOG_D = 0.00873, 0.241514, 0.598206


def vlog_encode(lin):
    """Scene-linear reflectance (18% grey = 0.18) to V-Log signal."""
    lin = np.asarray(lin, dtype=float)
    return np.where(lin < VLOG_CUT1, 5.6 * lin + 0.125,
                    VLOG_C * np.log10(np.maximum(lin, VLOG_CUT1) + VLOG_B) + VLOG_D)


def vlog_decode(sig):
    """V-Log signal to scene-linear reflectance."""
    sig = np.asarray(sig, dtype=float)
    return np.where(sig < VLOG_CUT2, (sig - 0.125) / 5.6,
                    10.0 ** ((np.maximum(sig, VLOG_CUT2) - VLOG_D) / VLOG_C) - VLOG_B)


# HLG: ITU-R BT.2100 OETF (scene light E in 0..1, signal E' in 0..1).
HLG_A = 0.17883277
HLG_B = 1 - 4 * HLG_A
HLG_C = 0.5 - HLG_A * np.log(4 * HLG_A)


def hlg_encode(lin):
    lin = np.clip(np.asarray(lin, dtype=float), 0.0, 1.0)
    return np.where(lin <= 1 / 12, np.sqrt(3 * lin),
                    HLG_A * np.log(np.maximum(12 * lin - HLG_B, 1e-12)) + HLG_C)


def hlg_decode(sig):
    sig = np.clip(np.asarray(sig, dtype=float), 0.0, 1.0)
    return np.where(sig <= 0.5, sig ** 2 / 3,
                    (np.exp((np.maximum(sig, 0.5) - HLG_C) / HLG_A) + HLG_B) / 12)
