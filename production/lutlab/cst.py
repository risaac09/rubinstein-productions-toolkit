"""lutlab.cst: colour space transform LUTs, camera log to DaVinci Intermediate or a display. numpy only.

  ~/.venvs/rp-tools/bin/python -m lutlab cst --from vlog --to di [--size 65|33] [--out DIR]
        -> DIR/vlog-to-di-65.cube

A technical transform, not a look: decode the source's code values to scene linear, change primaries
(through linear Rec.709, as everything in lutlab does), encode to the target, clip to 0..1. No tone
mapping and no gamut mapping, like Resolve's Color Space Transform with both set to None, so a display
target (r709, srgb) clips everything above display white: scene linear 1.0, about 2.5 stops over 18%
grey. Use it to check footage or to build a DaVinci Intermediate pipeline by hand, not as a grade.

At every lattice node the table equals `port` of an identity LUT (the trick used before this command
existed: an identity cube in the target space, ported from the source), without needing scipy.

Imports cube, spaces and guard only (no pipeline, fit, port or report), so it runs without scipy or Pillow,
as on a machine that only has numpy."""

import numpy as np

from . import cube, guard, spaces

FROM = ("vlog", "bmdfilm5", "logc3", "logc4", "di", "lin709")
TO = ("di", "r709", "srgb")
SIZES = (33, 65)
DEFAULT_SIZE = cube.DEFAULT_SIZE


def transform(codes, src, dst):
    """Code values in src -> code values in dst, clipped to 0..1 (what a .cube node can hold)."""
    return np.clip(spaces.from_lin709(spaces.to_lin709(codes, src), dst), 0.0, 1.0)


def table(src, dst, size=cube.DEFAULT_SIZE):
    return transform(cube.lattice(size), src, dst)


def file_name(src, dst, size):
    return f"{src}-to-{dst}-{size}.cube"


def write(src, dst, size, out, overwrite=False):
    """Write DIR/<src>-to-<dst>-<size>.cube atomically; refuses to replace a file unless asked."""
    path = guard.check_out_dir(out) / file_name(src, dst, size)
    return cube.write_cube(str(path), table(src, dst, size), f"{src} to {dst} (lutlab cst, {size})",
                           overwrite=overwrite)


def summary(src, dst, size, path):
    grey = transform(spaces.from_lin709(np.full(3, 0.18), src), src, dst)[0]
    return f"{path}  {src} -> {dst}, {size}^3, 18% grey {grey:.4f}"


def add_arguments(p):
    p.add_argument("--from", dest="src", required=True, choices=FROM)
    p.add_argument("--to", dest="dst", required=True, choices=TO)
    p.add_argument("--size", type=int, default=cube.DEFAULT_SIZE, choices=SIZES)
    p.add_argument("--out")
    p.add_argument("--overwrite", action="store_true")


def run(a):
    if a.src == a.dst:
        raise SystemExit(f"lutlab cst: --from and --to are both {a.src}; that is the identity")
    path = write(a.src, a.dst, a.size, guard.out_dir(a.out, "cst"), a.overwrite)
    print(summary(a.src, a.dst, a.size, path))
    return 0
