"""/usr/bin/python3 -m lutlab <command> ...   (run from production/)

  decompose <lut.cube> --in SPACE --out-space r709|srgb [--out DIR] [--name NAME] [--overwrite]
        fit the look model to a LUT: params.json, <name>.residual.cube, report.md, plots
  build <params.json> [--cube OUT.cube] [--size N] [--in SPACE] [--no-residual]
        [--hald OUT.png] [--hald-level L] [--overwrite]
        rebuild a LUT and/or a Hald CLUT from parameters (refuses to overwrite)
  fit-pairs --raw A.RW2 --jpg A.JPG [--raw B.RW2 --jpg B.JPG ...] [--out DIR] [--name NAME]
  fit-pairs --src log.tif --dst graded.tif --in SPACE --out-space r709 [--out DIR] [--name NAME]
        fit the same model from image pairs (RAW + camera JPEG, or source + graded frame)

Spaces: vlog, logc3, di, r709, srgb, lin709. Output goes to --out or $LUTLAB_OUT/<name>;
never inside a git work tree or an iCloud folder.
"""

import argparse
import sys
from pathlib import Path

from . import guard, pipeline


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lutlab", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("decompose")
    p.add_argument("lut")
    p.add_argument("--in", dest="in_space", required=True)
    p.add_argument("--out-space", default="r709")
    p.add_argument("--out")
    p.add_argument("--name")
    p.add_argument("--overwrite", action="store_true", help="replace an existing params.json (and its hand edits)")
    p = sub.add_parser("build")
    p.add_argument("params")
    p.add_argument("--cube")
    p.add_argument("--size", type=int, default=33)
    p.add_argument("--in", dest="in_space")
    p.add_argument("--no-residual", action="store_true")
    p.add_argument("--hald")
    p.add_argument("--hald-level", type=int, default=8)
    p.add_argument("--overwrite", action="store_true")
    p = sub.add_parser("fit-pairs")
    p.add_argument("--raw", action="append", default=[])
    p.add_argument("--jpg", action="append", default=[])
    p.add_argument("--src", action="append", default=[])
    p.add_argument("--dst", action="append", default=[])
    p.add_argument("--in", dest="in_space")
    p.add_argument("--out-space", default="srgb")
    p.add_argument("--out")
    p.add_argument("--name", default="pairs")
    a = ap.parse_args(argv)

    if a.cmd == "decompose":
        name = a.name or Path(a.lut).stem
        out = guard.out_dir(a.out, name)
        p = pipeline.decompose(a.lut, a.in_space, a.out_space, out, name, a.overwrite)
        fe = p["fit_error"]["natural colours"]
        print(f"{out}/report.md  dE2000 median tone {fe['tone']['median']} -> full {fe['full']['median']} "
              f"(p95 {fe['full']['p95']}); rebuild max |d| {p['source']['rebuild_max_abs_error']:.2e}")
    elif a.cmd == "build":
        if not (a.cube or a.hald):
            ap.error("build needs --cube and/or --hald")
        for f in (a.cube, a.hald):
            if f:
                guard.check_out_dir(Path(f).expanduser().resolve().parent)
        for f in pipeline.build(a.params, a.cube, a.size, a.in_space, not a.no_residual, a.hald, a.hald_level, a.overwrite):
            print(f)
    elif a.cmd == "fit-pairs":
        from . import pairs
        out = guard.out_dir(a.out, a.name)
        if a.raw or a.jpg:
            if len(a.raw) != len(a.jpg):
                ap.error("give one --jpg for every --raw")
            p = pairs.fit_raw_jpeg(list(zip(a.raw, a.jpg)), out, a.name)
        else:
            if len(a.src) != len(a.dst) or not a.src or not a.in_space:
                ap.error("give --src/--dst pairs and --in")
            p = pairs.fit_frames(list(zip(a.src, a.dst)), a.in_space, a.out_space, out, a.name)
        fe = p["fit_error"]
        print(f"{out}/report.md  held-out dE2000 median {fe['held-out pixels']['full']['median']} "
              f"(p95 {fe['held-out pixels']['full']['p95']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
