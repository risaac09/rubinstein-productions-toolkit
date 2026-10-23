"""lutlab.cube: read, sample and write 3D LUTs (.cube) and Hald CLUT PNGs.

A LUT here is a dict: table (N, N, N, 3) indexed [r, g, b], domain_min and
domain_max (3 each), title. The .cube text lists red fastest. Resolve's own
.dat 3D LUTs (a `3DLUTSIZE N` header, blue fastest, domain 0..1) read too, so
the LUTs Resolve ships are usable as sources. 1D and shaper LUTs are refused
with an error rather than read wrong."""

import os
import struct
import zlib

import numpy as np


def read_cube(path):
    size, dmin, dmax, title, rows, blue_fastest = None, [0.0] * 3, [1.0] * 3, "", [], False
    with open(path, errors="ignore") as f:
        for line in f:
            t = line.split()
            if not t or t[0].startswith("#"):
                continue
            key = t[0].upper()
            if key == "3DLUTSIZE":
                size, blue_fastest = int(t[1]), True
            elif key == "LUT_3D_SIZE":
                size = int(t[1])
            elif key in ("LUT_1D_SIZE", "LUT_1D_INPUT_RANGE"):
                raise ValueError(f"{path}: 1D or shaper LUTs are not supported ({t[0]})")
            elif key == "DOMAIN_MIN":
                dmin = [float(v) for v in t[1:4]]
            elif key == "DOMAIN_MAX":
                dmax = [float(v) for v in t[1:4]]
            elif key == "LUT_3D_INPUT_RANGE":
                dmin, dmax = [float(t[1])] * 3, [float(t[2])] * 3
            elif key == "TITLE":
                title = line.strip()[5:].strip().strip('"')
            elif key[0].isalpha():
                continue
            else:
                rows.append(t[:3])
    if not size or len(rows) != size ** 3:
        raise ValueError(f"{path}: LUT_3D_SIZE {size} but {len(rows)} entries")
    flat = np.array(rows, dtype=np.float64)
    table = flat.reshape(size, size, size, 3)
    if not blue_fastest:
        table = table.transpose(2, 1, 0, 3)  # [b, g, r] -> [r, g, b]
    return {"table": np.ascontiguousarray(table), "domain_min": np.array(dmin), "domain_max": np.array(dmax),
            "title": title}


# Decision D10, 2026-10-23: V-Log to DI mean Delta E 0.026 at 65 against 0.074 at 33.
DEFAULT_SIZE = 65


def lattice(size, dmin=(0, 0, 0), dmax=(1, 1, 1)):
    """Input coordinates of every node, shape (N, N, N, 3), indexed [r, g, b]."""
    axes = [np.linspace(lo, hi, size) for lo, hi in zip(dmin, dmax)]
    r, g, b = np.meshgrid(*axes, indexing="ij")
    return np.stack([r, g, b], -1)


def sample(lut, rgb):
    """Trilinear sample of a LUT at input values rgb (..., 3); inputs clamp to the domain."""
    t = lut["table"]
    n = t.shape[0]
    p = (np.asarray(rgb, dtype=np.float64) - lut["domain_min"]) / (lut["domain_max"] - lut["domain_min"])
    p = np.clip(p, 0, 1) * (n - 1)
    i0 = np.minimum(np.floor(p).astype(int), n - 2)
    f = p - i0
    out = np.zeros(p.shape[:-1] + (3,))
    for dr in (0, 1):
        wr = f[..., 0] if dr else 1 - f[..., 0]
        for dg in (0, 1):
            wg = f[..., 1] if dg else 1 - f[..., 1]
            for db in (0, 1):
                wb = f[..., 2] if db else 1 - f[..., 2]
                out += (wr * wg * wb)[..., None] * t[i0[..., 0] + dr, i0[..., 1] + dg, i0[..., 2] + db]
    return out


def cube_text(table, title="lutlab", dmin=(0, 0, 0), dmax=(1, 1, 1), digits=6):
    n = table.shape[0]
    head = [f'TITLE "{title}"', f"LUT_3D_SIZE {n}",
            "DOMAIN_MIN " + " ".join(f"{v:g}" for v in dmin), "DOMAIN_MAX " + " ".join(f"{v:g}" for v in dmax)]
    flat = table.transpose(2, 1, 0, 3).reshape(-1, 3)
    fmt = f"%.{digits}f %.{digits}f %.{digits}f"
    return "\n".join(head + [fmt % tuple(r) for r in flat]) + "\n"


def write_text(path, text, overwrite=False):
    """Atomic write. Refuses to overwrite unless asked: a LUT is versioned, never
    edited in place, and Resolve caches a LUT by path."""
    if os.path.exists(path) and not overwrite:
        raise FileExistsError(path)
    with open(path + ".part", "w") as f:
        f.write(text)
    os.replace(path + ".part", path)
    return path


def write_cube(path, table, title="lutlab", dmin=(0, 0, 0), dmax=(1, 1, 1), digits=6, overwrite=False):
    return write_text(path, cube_text(table, title, dmin, dmax, digits), overwrite)


# --- Hald CLUT ---------------------------------------------------------------

def hald_inputs(level):
    """Input colours of a Hald CLUT of this level, as an image (L^3, L^3, 3) in 0..1.
    Pixel k (row-major) holds r = k % N, g = (k // N) % N, b = k // N^2, N = L^2."""
    n, side = level * level, level ** 3
    k = np.arange(side * side)
    rgb = np.stack([k % n, (k // n) % n, k // (n * n)], -1) / (n - 1)
    return rgb.reshape(side, side, 3)


def png16(rgb):
    """16-bit RGB PNG bytes from floats in 0..1, written with zlib only."""
    h, w, _ = rgb.shape
    v = np.round(np.clip(rgb, 0, 1) * 65535).astype(">u2")
    raw = b"".join(b"\x00" + v[y].tobytes() for y in range(h))
    chunk = lambda tag, data: struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def write_hald(path, fn, level=8, overwrite=False):
    """Write fn (input rgb (..., 3) in 0..1 -> output rgb) as a Hald CLUT PNG."""
    if os.path.exists(path) and not overwrite:
        raise FileExistsError(path)
    data = png16(fn(hald_inputs(level)))
    with open(path + ".part", "wb") as f:
        f.write(data)
    os.replace(path + ".part", path)
    return path
