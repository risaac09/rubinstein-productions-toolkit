"""
rpresolve.overlay: checks a graphic rendered with alpha (a lower third, a
title, an intro master) before it goes on a timeline. numpy only; ffprobe
and ffmpeg read the file. Never connects to Resolve.

Rows, FAIL first in the report:

- alpha: the stream carries an alpha channel; under 10 bits warns
- codec: ProRes 4444 or 4444 XQ is the house master; anything else warns
- tags: colour range, matrix, primaries and transfer against --expect
- fps: the frame rate against --fps
- clear: no visible pixel at each --clear-at time (default the first frame)
- opaque: some fully opaque pixels at the hold time
- fringe: edge colour error where the graphic meets transparency, read as
  straight and as premultiplied alpha, at the hold time
- brand: CIEDE2000 between named sample points and token values passed in
  at run time (the values never live in this repo)

The fringe row needs no compositing over grounds. Over any ground G, a
straight reading gives C*a + G*(1 - a) against the clean F*a + G*(1 - a),
an error of a*(C - F); a premultiplied reading gives C + G*(1 - a), an
error of C - F*a. C is the edge pixel's colour, a its alpha, and F the
colour of the nearest fully opaque pixel. Black, white and grey grounds
all show the same two numbers.
"""

import json
import re

import numpy as np

from . import color
from .delivercheck import parse_fps
from .measure import FFMPEG, MeasureError, _run, bit_depth, decode_filter, probe as _probe

CODE_MAX = 1023          # fringe numbers are in 10-bit code values
FULL = 1 - 0.5 / 1023    # alpha at full scale
# ffmpeg widens 8-bit alpha by a shift (255 -> 1020 of 1023, 65280 of 65535), so a PNG
# wrapped to ProRes peaks at 0.997; that still counts as opaque, and the opaque row warns.
OPAQUE = 1 - 5 / 1023
CLEAR = 0.5 / 1023       # alpha at or below this counts as fully clear
EDGE_RADIUS = 3          # px searched for the opaque fill and the clear side of an edge
EDGE_ALPHA = (0.1, 0.9)  # edge pixels outside this alpha band are left out as noise
# Working default, 10-bit codes at the median edge pixel. Measured 2.1 on Chrome-rendered
# type wrapped from 8-bit PNG to ProRes 4444; a premultiplied file read as straight gave 135.
FRINGE_MAX = 4.0
DE_MAX = 1.0             # working default for brand samples, CIEDE2000
SAMPLE_BOX = 5           # brand samples average a 5 x 5 px box
HOUSE_PROFILES = ("4444", "4444 XQ")


class ToolMissing(RuntimeError):
    pass


def probe(path):
    try:
        return _probe(path)
    except FileNotFoundError:
        raise ToolMissing("ffprobe not found; set RPRESOLVE_FFPROBE")


def _call(cmd, **kw):
    try:
        return _run(cmd, **kw)
    except FileNotFoundError:
        raise ToolMissing(f"{cmd[0]} not found; set RPRESOLVE_FFMPEG and RPRESOLVE_FFPROBE")


# ---------------------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------------------

_ALPHA_FMT = re.compile(r"^(yuva|gbrap|ya\d|rgba|bgra|argb|abgr)")


def has_alpha(pix_fmt):
    return bool(_ALPHA_FMT.match(pix_fmt or ""))


def alpha_bits(pix_fmt):
    """Bits in the alpha plane: yuva444p10le -> 10, rgba64le -> 16, rgba -> 8."""
    pix_fmt = pix_fmt or ""
    if re.match(r"^(rgba|bgra)64", pix_fmt):
        return 16
    return bit_depth(pix_fmt)


def read_rgba(path, t, width, height, vf=None):
    """One frame at time t as float RGBA 0..1, shape (h, w, 4). A source
    without alpha reads as fully opaque."""
    cmd = [FFMPEG, "-v", "error", "-ss", f"{t:.4f}", "-i", "file:" + path, "-frames:v", "1"]
    cmd += ["-vf", vf] if vf else []
    cmd += ["-f", "rawvideo", "-pix_fmt", "rgba64le", "-"]
    proc = _call(cmd)
    want = width * height * 8
    if proc.returncode != 0 or len(proc.stdout) < want:
        raise MeasureError(f"ffmpeg could not read a frame at {t:.3f}s: "
                           f"{proc.stderr.decode(errors='replace').strip()[-200:]}")
    arr = np.frombuffer(proc.stdout[:want], dtype="<u2").reshape(height, width, 4)
    return arr.astype(np.float32) / np.float32(65535)  # float32 halves a 4K frame to 130 MB


# ---------------------------------------------------------------------------
# Pure measurements (tested on synthetic arrays)
# ---------------------------------------------------------------------------

def _views(h, w, dy, dx):
    """Slices (target, source) so that source = target shifted by (dy, dx)."""
    ty = slice(max(0, -dy), h - max(0, dy))
    tx = slice(max(0, -dx), w - max(0, dx))
    sy = slice(ty.start + dy, ty.stop + dy)
    sx = slice(tx.start + dx, tx.stop + dx)
    return (ty, tx), (sy, sx)


def _toward(mask, dy, dx, radius):
    """True where mask holds within radius steps along (dy, dx)."""
    h, w = mask.shape
    out = np.zeros_like(mask)
    for k in range(1, radius + 1):
        t, s = _views(h, w, k * dy, k * dx)
        out[t] |= mask[s]
    return out


def edge_fill(rgb, opaque, clear, radius):
    """(edge mask, fill colour) for antialiased edges: pixels with an opaque
    pixel on one side and a clear pixel on the other, along a row or a
    column, each within radius. The fill is the nearest opaque pixel on
    that side, so it belongs to the shape the edge belongs to. A translucent
    line that runs into an opaque shape has no clear side opposite it."""
    h, w = opaque.shape
    edge = np.zeros_like(opaque)
    fill = np.zeros_like(rgb)
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        side, colour = np.zeros_like(opaque), np.zeros_like(rgb)
        for k in range(1, radius + 1):
            t, s = _views(h, w, k * dy, k * dx)
            take = opaque[s] & ~side[t]
            colour[t][take] = rgb[s][take]
            side[t] |= take
        new = side & _toward(clear, -dy, -dx, radius) & ~edge
        fill[new] = colour[new]
        edge |= new
    return edge, fill


def fringe(rgba, radius=EDGE_RADIUS, band=EDGE_ALPHA):
    """Edge colour error in 10-bit codes (median and 99th percentile) under
    a straight and a premultiplied reading. Edge pixels sit inside the alpha
    band, between an opaque and a clear pixel (see between). A translucent
    panel's interior, glyph edges over it, and hairlines too thin to reach
    full opacity have no such neighbours and are left out."""
    rgb, a = rgba[..., :3], rgba[..., 3]
    opaque, clear = a >= OPAQUE, a <= CLEAR
    edge, fill = edge_fill(rgb, opaque, clear, radius)
    edge &= (a > band[0]) & (a < band[1])
    n = int(edge.sum())
    if n == 0:
        return {"edge_pixels": 0}
    c, f, al = rgb[edge], fill[edge], a[edge][:, None]
    out = {"edge_pixels": n}
    for mode, err in (("straight", np.abs(c - f) * al), ("premultiplied", np.abs(c - f * al))):
        err = np.max(err, axis=1) * CODE_MAX
        out[mode] = round(float(np.median(err)), 2)
        out[mode + "_p99"] = round(float(np.percentile(err, 99)), 2)
    return out


def parse_hex(text):
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", (text or "").strip())
    if not m:
        raise ValueError(f"not a #rrggbb colour: {text!r}")
    v = m.group(1)
    return np.array([int(v[i:i + 2], 16) / 255.0 for i in (0, 2, 4)])


def load_values(path):
    """{name: rgb 0..1} from a JSON object of name -> '#rrggbb'."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not data:
        raise ValueError(f"{path}: expected a JSON object of name -> '#rrggbb'")
    return {str(k): parse_hex(v if isinstance(v, str) else "") for k, v in data.items()}


def parse_sample(text):
    """NAME=X,Y or NAME=X,Y@SECONDS -> (name, x, y, t or None)."""
    m = re.fullmatch(r"([^=]+)=(\d+),(\d+)(?:@([0-9.]+))?", (text or "").strip())
    if not m:
        raise ValueError(f"--sample wants NAME=X,Y or NAME=X,Y@SECONDS, got {text!r}")
    t = float(m.group(4)) if m.group(4) else None
    return m.group(1), int(m.group(2)), int(m.group(3)), t


def parse_expect(text):
    m = re.fullmatch(r"(color_range|color_space|color_primaries|color_transfer|pix_fmt)=(\S+)",
                     (text or "").strip())
    if not m:
        raise ValueError(f"--expect wants KEY=VALUE with KEY one of color_range, color_space, "
                         f"color_primaries, color_transfer, pix_fmt; got {text!r}")
    return m.group(1), m.group(2)


def sample_box(rgba, x, y, size=SAMPLE_BOX):
    """Mean RGB and minimum alpha of a size x size box centred on (x, y)."""
    h, w = rgba.shape[:2]
    r = size // 2
    if not (r <= x < w - r and r <= y < h - r):
        raise ValueError(f"sample point {x},{y} is too close to the edge of a {w}x{h} frame")
    box = rgba[y - r:y + r + 1, x - r:x + r + 1]
    return box[..., :3].reshape(-1, 3).mean(axis=0), float(box[..., 3].min())


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------

def _row(rows, status, name, detail):
    rows.append({"status": status, "row": name, "detail": detail})


def check(path, expect=(), fps=None, clear_at=(0.0,), hold=None, alpha_mode="straight",
          values=None, samples=(), fringe_max=FRINGE_MAX, de_max=DE_MAX):
    """Run every row on one file. Returns {file, status, rows, probe}."""
    for name, *_ in samples:
        if values is None or name not in values:
            raise ValueError(f"--sample {name}: no value named {name} in --values")
    want_fps = parse_fps(fps) if fps else None
    p = probe(path)
    tags, rows = p["tags"], []
    pix = tags["pix_fmt"]

    if not has_alpha(pix):
        _row(rows, "FAIL", "alpha", f"no alpha channel (pix_fmt {pix or 'unknown'})")
    else:
        bits = alpha_bits(pix)
        _row(rows, "PASS" if bits >= 10 else "WARN", "alpha", f"{bits}-bit alpha ({pix})")

    house = p["codec"] == "prores" and p["profile"] in HOUSE_PROFILES
    _row(rows, "PASS" if house else "WARN", "codec",
         f"{p['codec']} {p['profile']}".strip() + ("" if house else "; the house master is ProRes 4444"))

    for key, want in expect:
        got = tags.get(key, "")
        _row(rows, "PASS" if got == want else "FAIL", "tags", f"{key} {got or 'untagged'} (want {want})")
    if not expect:
        _row(rows, "INFO", "tags", ", ".join(f"{k} {tags[k] or 'untagged'}" for k in sorted(tags)))

    if want_fps:
        got_fps = parse_fps(p["fps"]) if re.fullmatch(r"\d+(/\d+)?", p["fps"] or "") else None
        _row(rows, "PASS" if got_fps == want_fps else "FAIL", "fps", f"{p['fps']} (want {fps})")
    else:
        _row(rows, "INFO", "fps", p["fps"])

    vf, note = decode_filter(tags)
    if note:
        _row(rows, "WARN", "tags", note)
    w, h, dur = p["width"], p["height"], p["duration"]
    for t in clear_at:
        a = read_rgba(path, t, w, h, vf)[..., 3]
        visible = float((a > CLEAR).mean())
        _row(rows, "PASS" if visible == 0 else "FAIL", "clear",
             f"at {t:.3f}s " + ("nothing visible" if visible == 0 else f"{visible:.2%} of pixels visible"))

    t_hold = hold if hold is not None else dur / 2
    hold_rgba = read_rgba(path, t_hold, w, h, vf)
    covered = float((hold_rgba[..., 3] >= OPAQUE).mean())
    peak = float(hold_rgba[..., 3].max())
    detail = f"at {t_hold:.3f}s {covered:.2%} of pixels opaque, peak alpha {peak:.4f}"
    if covered == 0:
        _row(rows, "FAIL", "opaque", detail)
    elif peak < FULL:
        _row(rows, "WARN", "opaque", detail + f"; {1 - peak:.1%} of what is under it shows through. "
             "If the graphic is meant to be solid, an 8-bit alpha was likely widened by a shift")
    else:
        _row(rows, "PASS", "opaque", detail)

    fr = fringe(hold_rgba)
    if fr["edge_pixels"] == 0:
        _row(rows, "INFO", "fringe", f"at {t_hold:.3f}s no edge between graphic and transparency")
    else:
        other_mode = "premultiplied" if alpha_mode == "straight" else "straight"
        got, other = fr[alpha_mode], fr[other_mode]
        detail = (f"at {t_hold:.3f}s median {got} codes read as {alpha_mode} (p99 "
                  f"{fr[alpha_mode + '_p99']}), {other} as {other_mode}, over {fr['edge_pixels']} "
                  f"edge px (max {fringe_max})")
        if got > fringe_max and other < got:
            detail += f"; the file reads cleaner as {other_mode}"
        _row(rows, "PASS" if got <= fringe_max else "FAIL", "fringe", detail)

    for name, x, y, t in samples:
        src = hold_rgba if t is None or t == t_hold else read_rgba(path, t, w, h, vf)
        rgb, amin = sample_box(src, x, y)
        at = f"{name} at {x},{y}"
        if amin < OPAQUE:
            _row(rows, "FAIL", "brand", f"{at} is not fully opaque (alpha {amin:.3f})")
            continue
        de = float(color.delta_e_2000(color.rec709_to_lab(rgb), color.rec709_to_lab(values[name])))
        got_hex = "#" + "".join(f"{int(round(v * 255)):02x}" for v in rgb)
        _row(rows, "PASS" if de <= de_max else "FAIL", "brand",
             f"{at} reads {got_hex}, Delta E 2000 {de:.2f} (max {de_max})")

    order = {"FAIL": 0, "WARN": 1, "PASS": 2, "INFO": 3}
    rows.sort(key=lambda r: order[r["status"]])
    status = "fail" if any(r["status"] == "FAIL" for r in rows) else "pass"
    return {"file": path, "status": status, "rows": rows, "probe": p}


def format_report(report):
    lines = [f"overlay-check {report['file']}: {report['status'].upper()}"]
    lines += [f"  {r['status']:<4}  {r['row']:<7} {r['detail']}" for r in report["rows"]]
    return "\n".join(lines) + "\n"
