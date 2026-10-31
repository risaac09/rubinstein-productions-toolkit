"""python3 -m rpstills <command> ...

  index   <shoot folder> <out dir> [--tiles auto|off|all] [--times FILE] [--interval S] [--start ISO]
                                             read originals, write proxies and index.jsonl
                                             (--tiles: a second Vision pass over tiles of the original for frames
                                             with no subject face, default auto; --times, --interval, --start:
                                             a time for frames with no EXIF time, see rpstills/times.py)
  cluster <out dir>                          segments, bursts, runs -> clusters.json
  cull    <out dir>                          scores and proposal -> cull.json
  review  <out dir> --session NAME [--title] -> review.html
  look    <params.json> <out.cube> [--size N]   write a look as a DWG/DI 3D LUT (refuses to overwrite)
  crops   <out dir> [--config PATH]          crop plan per profile -> crops.json, crops-preview.jpg
  run     <shoot folder> <out dir> --session NAME   all four in order (takes the index options)

  develop <out dir> --look FILE [--ids ...] [--backend apple|lightcraft] [--raw PATH ...]
          [--neutral-box X0 Y0 X1 Y1] [--wb-from ID]
                                             RAW -> out/develop/<id>.tif (16-bit) + calibration.json: white balance
                                             from a neutral reference and exposure from cheek L* (solve.mode
                                             neutral), or onto one skin target (target mode)
  finish  <out dir> [--ids ...] [--src DIR] [--retouch none|all|ID ...] [--background off|on] [--crop PROFILE ...]
                                             opt-in background burn and retouch, crops on the landmark eye line
                                             -> out/finish/<profile>/<id>.jpg (sRGB) + finish.json
  audit   <dir> [--out DIR] [--profile NAME] [--hero FILE]
                                             numbers of finished stills (skin Lab and scope angle, set consistency
                                             against the median or a hero, neutral garment, clip, ICC, eye line,
                                             headroom, eye sharpness, background) -> audit.json; checks, no pass/fail
  absheet IMG IMG [...] --out DIR [--seed N] [--title T]
                                             lettered A/B/C decision sheet (2-9 images, phone legible) + key.json
  serve   <folder> [--bind 127.0.0.1] [--port 8780]
                                             read-only file server with byte ranges, for review on a phone or iPad
"""

import argparse
import importlib
import os
import sys

from . import cluster, crops, cull, index, look, review

# Subcommands that parse their own arguments and never read the shoot index. Each is imported only
# when it is the command, so the shoot commands and serve run without the OpenCV that audit and finish load.
SELF_CONTAINED = ("develop", "finish", "audit", "absheet", "serve")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    ap = argparse.ArgumentParser(prog="rpstills")
    sub = ap.add_subparsers(dest="cmd", required=True)
    def index_options(p):
        p.add_argument("--workers", type=int, default=4)
        p.add_argument("--tiles", choices=index.TILES_MODES, default="auto")
        p.add_argument("--times", help="sidecar of frame times (JSON or TSV); default times.json or times.tsv in the shoot folder")
        p.add_argument("--interval", type=float, help="seconds between numbered frame grabs (shot_001, shot_002, ...)")
        p.add_argument("--start", help="ISO time of the first numbered grab, for prefixes the sidecar does not start")
    p = sub.add_parser("index"); p.add_argument("shoot"); p.add_argument("out"); index_options(p)
    p = sub.add_parser("cluster"); p.add_argument("out")
    p = sub.add_parser("cull"); p.add_argument("out")
    p = sub.add_parser("review"); p.add_argument("out"); p.add_argument("--session", required=True); p.add_argument("--title")
    p = sub.add_parser("look"); p.add_argument("params"); p.add_argument("cube"); p.add_argument("--size", type=int, default=33)
    p = sub.add_parser("crops"); p.add_argument("out"); p.add_argument("--config")
    p = sub.add_parser("run"); p.add_argument("shoot"); p.add_argument("out"); p.add_argument("--session", required=True)
    p.add_argument("--title"); index_options(p)
    for name in SELF_CONTAINED:
        p = sub.add_parser(name)
        if argv[:1] == [name]:         # argparse takes the command as the first word; the rest stay bare
            importlib.import_module("." + name, __package__).add_arguments(p)
    a = ap.parse_args(argv)
    if a.cmd in SELF_CONTAINED:
        return importlib.import_module("." + a.cmd, __package__).run(a)
    if a.cmd == "look":
        doc = look.load_params(a.params)
        try:
            print(look.write_cube(os.path.abspath(a.cube), doc.get("params", doc), a.size, doc.get("name", "rpstills look")))
        except FileExistsError as e:
            print(f"refusing to overwrite {e}; a look is versioned, write a new file name", file=sys.stderr)
            return 1
        return 0
    out = os.path.abspath(a.out)
    from rpresolve.paths import out_problem  # lazy: rpresolve is stdlib-only, imported only to write
    problem = out_problem(os.path.join(out, "index.jsonl"), any_git_tree=True)
    if problem:
        print(f"rpstills {a.cmd}: {problem}", file=sys.stderr)
        return 1
    if a.cmd in ("index", "run"):
        try:
            rows = index.build(os.path.abspath(a.shoot), out, workers=a.workers, times_file=a.times,
                               interval=a.interval, start=a.start, tiles=a.tiles)
        except (ValueError, OSError) as e:
            print(f"rpstills: {e}", file=sys.stderr)
            return 1
    else:
        rows = index.load(out)
    if a.cmd == "index":
        return 0
    rows = index.augment_face_hash(out, rows)
    rows = index.augment_frame_metrics(out, rows)
    cl = cluster.build(rows)
    if a.cmd in ("cluster", "run"):
        print(cluster.write(out, cl)); print(cluster.summary(cl))
        if a.cmd == "cluster":
            return 0
    cu = cull.build(rows, cl)
    if a.cmd in ("cull", "run"):
        print(cull.write(out, cu)); print(cull.summary(cu))
        if a.cmd == "cull":
            return 0
    if a.cmd == "crops":
        cfg = crops.load_config(a.config or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "stills-config.json"))
        ids, src = crops.selection(out, cu)
        plan = crops.build(rows, cl, ids, cfg)
        print(crops.write(out, plan, src, cfg)); print("selection:", src); print(crops.summary(plan))
        path, shown, total = crops.preview(out, rows, plan, cfg)
        if path is None:
            print("nothing to preview")
        else:
            print(path); print(f"preview shows {shown} of {total} frames, every flagged one first")
        return 0
    source = rows[0]["jpeg"].rsplit("/", 1)[0] if rows else a.shoot if hasattr(a, "shoot") else out
    print(review.build(out, a.session, source, rows, cl, cu, title=a.title))
    return 0


if __name__ == "__main__":
    sys.exit(main())
