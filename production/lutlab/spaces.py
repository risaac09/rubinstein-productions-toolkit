"""lutlab.spaces: encodings, gamuts, OKLab, CIELAB and Delta E 2000, in numpy.

A space is an encoding (code value <-> linear light) plus a gamut. Every look
is evaluated through scene linear light in Rec.709 primaries ("lin709"), so a
space only needs to say how its code values get there and back.

Display encodings (r709, srgb) decode to display light; lutlab treats that as
the linear quantity for a creative Rec.709-in LUT, the usual convention.
Constants: Panasonic V-Log, ARRI LogC3 (EI 800) and LogC4, Blackmagic Film
Gen 5, Blackmagic DaVinci Intermediate, BT.1886 (2.4) and IEC 61966-2-1 sRGB,
all as published. The house cameras are in CAMERAS: the S5II and GH7 shoot
V-Log, the Pyxis BRAW (decoded as Gen 5)."""

import numpy as np

from rpstills.look import di_decode, di_encode  # the published DaVinci Intermediate curve, one copy
from rpstills.measure import delta_e2000  # noqa: F401  CIEDE2000 has one copy, in rpstills.measure

D65 = (0.3127, 0.3290)
PRIMARIES = {
    "709": [(0.64, 0.33), (0.30, 0.60), (0.15, 0.06)],
    "vgamut": [(0.730, 0.280), (0.165, 0.840), (0.100, -0.030)],
    "awg3": [(0.6840, 0.3130), (0.2210, 0.8480), (0.0861, -0.1020)],
    "dwg": [(0.8000, 0.3130), (0.1682, 0.9877), (0.0790, -0.1155)],
    "bmdwg5": [(0.7177215, 0.3171181), (0.2280410, 0.8615690), (0.1005841, -0.0820452)],
    "awg4": [(0.7347, 0.2653), (0.1424, 0.8576), (0.0991, -0.0308)],
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


# ARRI LogC4 (ALEXA 35 and ARRI's current Rec.709 LUTs), from ARRI's LogC4 specification
_C4A = (2.0 ** 18 - 16) / 117.45
_C4B = (1023.0 - 95) / 1023
_C4C = 95.0 / 1023
_C4S = (7 * np.log(2) * 2.0 ** (7 - 14 * _C4C / _C4B)) / (_C4A * _C4B)
_C4T = (2.0 ** (14 * (-_C4C / _C4B) + 6) - 64) / _C4A


def logc4_encode(x):
    return np.where(x >= _C4T, (np.log2(np.maximum(_C4A * x + 64, 1e-12)) - 6) / 14 * _C4B + _C4C, (x - _C4T) / _C4S)


def logc4_decode(y):
    return np.where(y >= 0, (2.0 ** (14 * (y - _C4C) / _C4B + 6) - 64) / _C4A, y * _C4S + _C4T)


# Blackmagic Film Generation 5 (Pyxis, BRAW decoded as Gen 5), from Blackmagic's
# Generation 5 Color Science document
_G5A, _G5B, _G5C = 0.08692876065491224, 0.005494072432257808, 0.5300133392291939
_G5D, _G5E, _G5CUT = 8.283605932402494, 0.09246575342465753, 0.005


def bmdfilm5_encode(x):
    return np.where(x < _G5CUT, _G5D * x + _G5E, _G5A * np.log(np.maximum(x + _G5B, 1e-12)) + _G5C)


def bmdfilm5_decode(y):
    return np.where(y < _G5D * _G5CUT + _G5E, (y - _G5E) / _G5D, np.exp((y - _G5C) / _G5A) - _G5B)




def srgb_decode(y):
    return np.where(np.abs(y) <= 0.04045, y / 12.92, np.sign(y) * ((np.abs(y) + 0.055) / 1.055) ** 2.4)


def srgb_encode(x):
    return np.where(np.abs(x) <= 0.0031308, x * 12.92, np.sign(x) * (1.055 * np.abs(x) ** (1 / 2.4) - 0.055))


SPACES = {
    # name: (decode, encode, gamut, description)
    "vlog": (vlog_decode, vlog_encode, "vgamut", "Panasonic V-Log / V-Gamut"),
    "logc3": (logc3_decode, logc3_encode, "awg3", "ARRI LogC3 (EI 800) / ARRI Wide Gamut 3"),
    "logc4": (logc4_decode, logc4_encode, "awg4", "ARRI LogC4 / ARRI Wide Gamut 4"),
    "bmdfilm5": (bmdfilm5_decode, bmdfilm5_encode, "bmdwg5", "Blackmagic Film Gen 5 / Wide Gamut Gen 5"),
    "di": (di_decode, di_encode, "dwg", "DaVinci Intermediate / DaVinci Wide Gamut"),
    "r709": (lambda y: _spow(y, 2.4), lambda x: _spow(x, 1 / 2.4), "709", "Rec.709, BT.1886 gamma 2.4"),
    "srgb": (srgb_decode, srgb_encode, "709", "sRGB"),
    "lin709": (lambda y: y, lambda x: x, "709", "linear Rec.709"),
}


# The house cameras: --camera NAME -> (space, label for file names)
CAMERAS = {
    "s5ii": ("vlog", "S5II"),
    "pyxis": ("bmdfilm5", "Pyxis"),
    "gh7": ("vlog", "GH7"),
}


def camera(name):
    if name.lower() not in CAMERAS:
        raise ValueError(f"unknown camera {name!r}; known: {', '.join(CAMERAS)}")
    return CAMERAS[name.lower()]


# A node in a Resolve Color Managed project sees the timeline space (DaVinci Intermediate by default), and
# Resolve's output transform follows it. A Rec.709-out LUT there would be mapped a second time, so the "rcm"
# target takes DI in and puts the finished Rec.709 picture back into DI code values. With the output colour
# space at Rec.709 Gamma 2.4 and tone and gamut mapping off, the output transform undoes that exactly.
RCM = ("di", "RCM", "di")


def target(name):
    """--camera NAME for `looks`: (input space, file label, output encoding or None for the look's own)."""
    if name.lower() == "rcm":
        return RCM
    space, label = camera(name)
    return space, label, None


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
