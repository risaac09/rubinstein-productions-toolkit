#!/usr/bin/env python3
"""Writes the synthetic reference chart for the look book: a 960x540 16-bit PNG in V-Log / V-Gamut.

Everything is computed, nothing is photographed, so the same code gives the same pixels.
Top: 24 patches (skin-tone-like, foliage, sky, primaries, a six-step grey row), values given as
8-bit sRGB and decoded to linear Rec.709. Middle: a 21-step grey ramp, one step per third of a
stop around 18 percent grey, over a smooth ramp. Bottom: a hue sweep at constant linear
luminance 0.18, then the same sweep at half saturation. The script encodes the linear values
with lutlab's own V-Log encoder, so the chart is what a V-Log camera would record.

Usage (from production/):  ~/.venvs/rp-tools/bin/python lutlab-looks/make_chart.py OUT.png
"""
import colorsys
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lutlab import spaces  # noqa: E402

W, H = 960, 540

# 24 approximate colour-chart patches as 8-bit sRGB (own values, not a measured chart).
PATCHES = [
    (115, 82, 68), (194, 150, 130), (98, 122, 157), (87, 108, 67), (133, 128, 177), (103, 189, 170),
    (214, 126, 44), (80, 91, 166), (193, 90, 99), (94, 60, 108), (157, 188, 64), (224, 163, 46),
    (56, 61, 150), (70, 148, 73), (175, 54, 60), (231, 199, 31), (187, 86, 149), (8, 133, 161),
    (243, 243, 242), (200, 200, 200), (160, 160, 160), (122, 122, 121), (85, 85, 85), (52, 52, 52),
]


def lin_from_srgb8(rgb):
    return spaces.srgb_decode(np.asarray(rgb, dtype=np.float64) / 255)


def hue_row(width, height, sat):
    x = np.arange(width)
    row = np.zeros((width, 3))
    for i in x:
        r, g, b = colorsys.hsv_to_rgb(i / width, 1.0, 1.0)
        c = np.array([r, g, b])
        y = spaces.LUMA709 @ c
        c = c / y * 0.18                     # constant linear luminance 0.18
        grey = np.full(3, 0.18)
        row[i] = grey + (c - grey) * sat
    return np.clip(np.repeat(row[None], height, 0), 0, None)


def chart():
    img = np.zeros((H, W, 3))
    img[:] = 0.18 * 0.5
    # patches: 6 x 4 in the top 288 px
    pw, ph, gap, x0, y0 = 150, 56, 10, 25, 14
    for i, p in enumerate(PATCHES):
        r, c = divmod(i, 6)
        x, y = x0 + c * (pw + gap), y0 + r * (ph + gap)
        img[y:y + ph, x:x + pw] = lin_from_srgb8(p)
    # 21-step grey ramp, 1/3 stop apart, centred on 18 percent grey
    stops = (np.arange(21) - 10) / 3
    levels = 0.18 * 2.0 ** stops
    sw = (W - 2 * x0) // 21
    for i, v in enumerate(levels):
        img[300:360, x0 + i * sw:x0 + (i + 1) * sw] = v
    # smooth ramp, 0 to 1 linear, shown on a log-ish axis (ten stops below 1 to 1)
    t = np.linspace(-10, 0, W - 2 * x0)
    img[366:396, x0:W - x0] = (2.0 ** t)[None, :, None]
    # hue sweeps
    img[404:464, x0:W - x0] = hue_row(W - 2 * x0, 60, 1.0)
    img[470:530, x0:W - x0] = hue_row(W - 2 * x0, 60, 0.5)
    return img


def main(out):
    code = np.clip(spaces.from_lin709(chart(), "vlog"), 0, 1)
    px = np.round(code * 65535).astype(np.uint16)
    out = Path(out)
    if out.exists():
        raise SystemExit(f"{out} exists")
    cv2.imwrite(str(out), px[..., ::-1])
    print(out)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
