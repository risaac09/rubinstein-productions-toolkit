"""/usr/bin/python3 -m lutlab <command> ...   (run from production/)

  decompose <lut.cube> --in SPACE --out-space r709|srgb [--out DIR] [--name NAME] [--overwrite]
        fit the look model to a LUT: params.json, <name>.residual.cube, report.md, plots
  build <params.json> [--delta LOOK.json] [--cube OUT.cube] [--size N] [--in SPACE|--camera C]
        [--no-residual] [--hald OUT.png] [--hald-level L] [--overwrite]
        rebuild a LUT and/or a Hald CLUT from parameters, optionally with a look delta on top
  looks <base params.json> [--deltas DIR|FILE ...] --camera pyxis,s5ii [--size N] [--out DIR]
        bake every look delta on the base for every camera: "<look> [<Camera>].cube"
  port <lut> --from SPACE (--to SPACE | --camera C) [--size 33] [--ev 0] [--monitor] [--out DIR]
        re-address a LUT made for one log to another (exact; no model)
  diff <a> <b> --in SPACE [--out-space r709]
        Delta E 2000 between two LUTs on natural colours, overall and by hue
  apply <lut ...> --frame F [--frame F ...] [--width 960] [--out DIR]
  apply <lut> --video IN.mov [--out DIR]
        preview: graded stills and a contact sheet, or an H.264 clip via ffmpeg
  verify-render SOURCE RENDER LUT --t SECONDS [--render-t S] [--out-space r709] [--margin 0.10]
        Delta E 2000 between a Resolve render and the source frame run through the LUT
  library [--scan] [--list] [--space S] [--dupes] [--out DIR]
        registry of the LUTs in Resolve's LUT folders: input-space guess, copies
  fit-pairs --raw A.RW2 --jpg A.JPG [--raw B.RW2 --jpg B.JPG ...] [--out DIR] [--name NAME]
  fit-pairs --src log.tif --dst graded.tif --in SPACE --out-space r709 [--out DIR] [--name NAME]
        fit the same model from image pairs (RAW + camera JPEG, or source + graded frame)

Spaces: vlog, bmdfilm5, logc3, logc4, di, r709, srgb, lin709. Cameras: s5ii (vlog),
pyxis (bmdfilm5). LUT inputs read .cube and Resolve .dat. Output goes to --out or
$LUTLAB_OUT/<name>; never inside a git work tree or an iCloud folder.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from . import guard, pipeline, spaces

LOOKS_DIR = Path(__file__).resolve().parent.parent / "lutlab-looks"


def _in_space(ap, a):
    if getattr(a, "camera", None):
        if a.in_space:
            ap.error("give --in or --camera, not both")
        return spaces.camera(a.camera)[0]
    return a.in_space


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
    p.add_argument("--delta")
    p.add_argument("--cube")
    p.add_argument("--size", type=int, default=33)
    p.add_argument("--in", dest="in_space")
    p.add_argument("--camera")
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
    p = sub.add_parser("looks")
    p.add_argument("base")
    p.add_argument("--deltas", nargs="+", default=[str(LOOKS_DIR)])
    p.add_argument("--camera", default="pyxis,s5ii")
    p.add_argument("--size", type=int, default=33)
    p.add_argument("--out")
    p.add_argument("--overwrite", action="store_true")
    p = sub.add_parser("port")
    p.add_argument("lut")
    p.add_argument("--from", dest="src", required=True)
    p.add_argument("--to", dest="dst")
    p.add_argument("--camera")
    p.add_argument("--size", type=int, default=33)
    p.add_argument("--ev", type=float, default=0.0, help="exposure offset in stops applied before the LUT")
    p.add_argument("--monitor", action="store_true", help="also write a 33-point copy clipped to 0..1 for in-camera use")
    p.add_argument("--name")
    p.add_argument("--out")
    p.add_argument("--overwrite", action="store_true")
    p = sub.add_parser("diff")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--in", dest="in_space", required=True)
    p.add_argument("--out-space", default="r709")
    p = sub.add_parser("apply")
    p.add_argument("luts", nargs="+")
    p.add_argument("--frame", action="append", default=[])
    p.add_argument("--video")
    p.add_argument("--width", type=int, default=960)
    p.add_argument("--out")
    p.add_argument("--overwrite", action="store_true")
    p = sub.add_parser("verify-render")
    p.add_argument("source")
    p.add_argument("render")
    p.add_argument("lut")
    p.add_argument("--t", type=float, required=True, help="seconds into the source")
    p.add_argument("--render-t", type=float, help="seconds into the render (default: same as --t)")
    p.add_argument("--out-space", default="r709")
    p.add_argument("--margin", type=float, default=0.10, help="fraction of the picture trimmed on every side")
    p = sub.add_parser("library")
    p.add_argument("--scan", action="store_true", help="rescan Resolve's LUT folders (default when no registry yet)")
    p.add_argument("--list", action="store_true")
    p.add_argument("--space")
    p.add_argument("--dupes", action="store_true")
    p.add_argument("--out")
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
        for f in pipeline.build(a.params, a.cube, a.size, _in_space(ap, a), not a.no_residual, a.hald, a.hald_level,
                                a.overwrite, a.delta):
            print(f)
    elif a.cmd == "looks":
        out = guard.out_dir(a.out, "looks")
        deltas = []
        for d in a.deltas:
            deltas += sorted(str(x) for x in Path(d).glob("*.json")) if Path(d).is_dir() else [d]
        cams = [c.strip() for c in a.camera.split(",") if c.strip()]
        for f in pipeline.looks(a.base, deltas, cams, out, a.size, a.overwrite):
            print(f)
    elif a.cmd == "port":
        from . import cube, port
        if bool(a.dst) == bool(a.camera):
            ap.error("give --to SPACE or --camera NAME")
        src = spaces.check_space(a.src)
        dst, label = (spaces.camera(a.camera) if a.camera else (spaces.check_space(a.dst), a.dst))
        lut = cube.read_cube(a.lut)
        # keep a Finder copy's suffix ("Look.CUBE 2" -> "Look 2") so it never collides with "Look.CUBE"
        name = a.name or re.sub(r"\.(cube|dat)( \d+)?$", r"\2", Path(a.lut).name, flags=re.I)
        out = guard.out_dir(a.out, "ported")
        table, share = port.port_table(lut, src, dst, a.size, a.ev)
        path = out / f"{name} [{label}].cube"
        print(cube.write_cube(str(path), table, f"{name} [{label}] from {src}", overwrite=a.overwrite))
        if a.monitor:
            mt = table if a.size == 33 else port.port_table(lut, src, dst, 33, a.ev)[0]
            print(cube.write_cube(str(out / f"{name} [{label} monitor].cube"), mt.clip(0, 1),
                                  f"{name} [{label} monitor]", overwrite=a.overwrite))
        print(f"natural colours outside the source LUT's domain (clamped): {share:.1%}")
    elif a.cmd == "diff":
        from . import cube, port
        r = port.diff(cube.read_cube(a.a), cube.read_cube(a.b), spaces.check_space(a.in_space), a.out_space)
        print(json.dumps(r, indent=1))
    elif a.cmd == "apply":
        from . import apply
        out = guard.out_dir(a.out, "preview")
        if a.video:
            if len(a.luts) != 1:
                ap.error("--video takes one LUT")
            dst = out / f"{Path(a.video).stem} - {Path(a.luts[0]).stem}.mp4"
            files = apply.video(a.luts[0], a.video, str(dst), a.overwrite)
        elif a.frame:
            files = apply.stills(a.luts, a.frame, out, a.width, a.overwrite)
        else:
            ap.error("give --frame F (one or more) or --video V")
        for f in files:
            print(f)
    elif a.cmd == "verify-render":
        from . import verify
        print(json.dumps(verify.verify(a.source, a.render, a.lut, a.t, a.render_t, a.out_space, a.margin), indent=1))
    elif a.cmd == "library":
        from . import library
        out = guard.out_dir(a.out, "library")
        reg = Path(out) / "registry.json"
        if a.scan or not reg.exists():
            entries = library.scan(overrides=library.load_overrides(out))
            print(library.write(entries, out), f"({len(entries)} LUTs)")
        else:
            entries = json.loads(reg.read_text())["entries"]
        if a.dupes:
            for g in library.duplicate_groups(entries):
                print("\n  ".join(["same bytes:"] + g))
        if a.list or a.space:
            rows = [e for e in entries if not a.space or (e["space"] or e["guess"]) == a.space]
            for e in sorted(rows, key=lambda e: (e["guess"], e["rel"].lower())):
                cp = f"  (+{len(e['copies'])} copies)" if e["copies"] else ""
                print(f"{e['space'] or e['guess']:<14} {e['lattice']:>3}  {e['rel']}{cp}")
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
