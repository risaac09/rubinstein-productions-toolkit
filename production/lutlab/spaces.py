"""lutlab.spaces: encodings, gamuts, OKLab, CIELAB and Delta E 2000, in numpy.

A space is an encoding (code value <-> linear light) plus a gamut. Every look
is evaluated through scene linear light in Rec.709 primaries ("lin709"), so a
space only needs to say how its code values get there and back.

Display encodings (r709, srgb) decode to display light; lutlab treats that as
the linear quantity for a creative Rec.709-in LUT, the usual convention.
Constants: Panasonic V-Log, ARRI LogC3 (EI 800), Blackmagic DaVinci
Intermediate, BT.1886 (2.4) and IEC 61966-2-1 sRGB, all as published."""

import numpy as np

from rpstills.look import di_decode, di_encode  # the published DaVinci Intermediate curve, one copy

D65 = (0.3127, 0.3290)
PRIMARIES = {
    "709": [(0.64, 0.33), (0.30, 0.60), (0.15, 0.06)],
    "vgamut": [(0.730, 0.280), (0.165, 0.840), (0.100, -0.030)],
    "awg3": [(0.6840, 0.3130), (0.2210, 0.8480), (0.0861, -0.1020)],
    "dwg": [(0.8000, 0.3130), (0.1682, 0.9877), (0.0790, -0.1155)],
    "2020": [(0.708, 0.292), (0.170, 0.797), (0.131, 0.046)],
}


def rgb_to_xyz_matrix(prim, wp=D65):
    xyz = lambda x, y: np.array([x / y, 1.0, (1 - x - y) / y])
    P = np.stack([xyz(*p) for p in prim], 1)
    return P * np.linalg.solve(P, xyz(*wp))


XYZ = {k: rgb_to_xyz_matrix(v) for k, v in PRIMARIES.items()}


def gamut_matrix(src, dst):
    return np.linalg.inv(XYZ[dst]) @ XYZ[src]


def _apply(m, rgb):
    return np.einsum("ij,...j->...i", m, rgb)


def _spow(x, p):
    return np.sign(x) * np.abs(x) ** p


# --- encodings: decode(code) -> linear, encode(linear) -> code -------------

def vlog_decode(y):
    return np.where(y < 0.181, (y - 0.125) / 5.6, 10 ** ((y - 0.598206) / 0.241514) - 0.00873)


def vlog_encode(x):
    return np.where(x < 0.01, 5.6 * x + 0.125, 0.241514 * np.log10(np.maximum(x + 0.00873, 1e-12)) + 0.598206)


def logc3_encode(x):
    return np.where(x > 0.010591, 0.247190 * np.log10(np.maximum(5.555556 * x + 0.052272, 1e-12)) + 0.385537,
                    5.367655 * x + 0.092809)


def logc3_decode(y):
    return np.where(y > 0.149658, (10 ** ((y - 0.385537) / 0.247190) - 0.052272) / 5.555556, (y - 0.092809) / 5.367655)




def srgb_decode(y):
    return np.where(np.abs(y) <= 0.04045, y / 12.92, np.sign(y) * ((np.abs(y) + 0.055) / 1.055) ** 2.4)


def srgb_encode(x):
    return np.where(np.abs(x) <= 0.0031308, x * 12.92, np.sign(x) * (1.055 * np.abs(x) ** (1 / 2.4) - 0.055))


SPACES = {
    # name: (decode, encode, gamut, description)
    "vlog": (vlog_decode, vlog_encode, "vgamut", "Panasonic V-Log / V-Gamut"),
    "logc3": (logc3_decode, logc3_encode, "awg3", "ARRI LogC3 (EI 800) / ARRI Wide Gamut 3"),
    "di": (di_decode, di_encode, "dwg", "DaVinci Intermediate / DaVinci Wide Gamut"),
    "r709": (lambda y: _spow(y, 2.4), lambda x: _spow(x, 1 / 2.4), "709", "Rec.709, BT.1886 gamma 2.4"),
    "srgb": (srgb_decode, srgb_encode, "709", "sRGB"),
    "lin709": (lambda y: y, lambda x: x, "709", "linear Rec.709"),
}


def check_space(name):
    if name not in SPACES:
        raise ValueError(f"unknown space {name!r}; known: {', '.join(SPACES)}")
    return name


def to_lin709(code, space):
    dec, _, gamut, _ = SPACES[check_space(space)]
    lin = dec(np.asarray(code, dtype=np.float64))
    return lin if gamut == "709" else _apply(gamut_matrix(gamut, "709"), lin)


def from_lin709(lin, space):
    _, enc, gamut, _ = SPACES[check_space(space)]
    lin = np.asarray(lin, dtype=np.float64)
    return enc(lin if gamut == "709" else _apply(gamut_matrix("709", gamut), lin))


LUMA709 = XYZ["709"][1]


def luminance(lin709):
    return np.asarray(lin709) @ LUMA709


# --- OKLab (Ottosson 2020), from linear Rec.709/sRGB ------------------------

_OK1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
                 [0.2119034982, 0.6806995451, 0.1073969566],
                 [0.0883024619, 0.2817188376, 0.6299787005]])
_OK2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                 [1.9779984951, -2.4285922050, 0.4505937099],
                 [0.0259040371, 0.7827717662, -0.8086757660]])
_OK1I, _OK2I = np.linalg.inv(_OK1), np.linalg.inv(_OK2)


def oklab(lin709):
    return _apply(_OK2, np.cbrt(_apply(_OK1, lin709)))


def oklab_to_lin709(lab):
    return _apply(_OK1I, _apply(_OK2I, lab) ** 3)


def lch(lab):
    """OKLab -> (L, C, h in radians)."""
    return lab[..., 0], np.hypot(lab[..., 1], lab[..., 2]), np.arctan2(lab[..., 2], lab[..., 1])


# --- CIELAB and Delta E 2000 -------------------------------------------------

_WHITE = XYZ["709"].sum(1)


def cielab(lin709):
    t = _apply(XYZ["709"], lin709) / _WHITE
    d = 6 / 29
    f = np.where(t > d ** 3, np.cbrt(np.maximum(t, 0)), t / (3 * d * d) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def delta_e2000(lab1, lab2):
    L1, a1, b1 = np.moveaxis(np.asarray(lab1, dtype=np.float64), -1, 0)
    L2, a2, b2 = np.moveaxis(np.asarray(lab2, dtype=np.float64), -1, 0)
    cb = (np.hypot(a1, b1) + np.hypot(a2, b2)) / 2
    g = 0.5 * (1 - np.sqrt(cb ** 7 / (cb ** 7 + 25.0 ** 7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360
    zero = (c1p * c2p) == 0
    dhp = h2p - h1p
    dhp = np.where(dhp > 180, dhp - 360, np.where(dhp < -180, dhp + 360, dhp))
    dhp = np.where(zero, 0, dhp)
    dHp = 2 * np.sqrt(c1p * c2p) * np.sin(np.radians(dhp / 2))
    lbp, cbp = (L1 + L2) / 2, (c1p + c2p) / 2
    hs = h1p + h2p
    hbp = np.where(zero, hs, np.where(np.abs(h1p - h2p) <= 180, hs / 2, np.where(hs < 360, (hs + 360) / 2, (hs - 360) / 2)))
    t = (1 - 0.17 * np.cos(np.radians(hbp - 30)) + 0.24 * np.cos(np.radians(2 * hbp))
         + 0.32 * np.cos(np.radians(3 * hbp + 6)) - 0.20 * np.cos(np.radians(4 * hbp - 63)))
    dth = 30 * np.exp(-(((hbp - 275) / 25) ** 2))
    rc = 2 * np.sqrt(cbp ** 7 / (cbp ** 7 + 25.0 ** 7))
    sl = 1 + 0.015 * (lbp - 50) ** 2 / np.sqrt(20 + (lbp - 50) ** 2)
    sc, sh = 1 + 0.045 * cbp, 1 + 0.015 * cbp * t
    rt = -np.sin(np.radians(2 * dth)) * rc
    dl, dc, dh = (L2 - L1) / sl, (c2p - c1p) / sc, dHp / sh
    return np.sqrt(dl * dl + dc * dc + dh * dh + rt * dc * dh)
