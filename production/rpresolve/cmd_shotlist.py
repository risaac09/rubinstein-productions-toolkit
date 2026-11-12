"""
shotlist: a shot list written in story order, compiled into the manifest the draft farm
and assemble_timeline read. Offline, stdlib only.

    shotlist check LIST
    shotlist compile LIST --cuts plan.json --out DIR [--exclude FILE] [--method ID]

compile writes DIR/manifest.json (version 2, with retime and reframe-pan entries),
DIR/method.json (the plain method the draft farm reads beside it) and, for a 9:16 list,
DIR/crops.json (each shot's static centre, the mean of its cx). Without --cuts, --period
SECONDS gives every shot that length. --exclude refuses a list that uses an excluded take,
as the draft farm does. --method copies that method's look and end rule in; it never
reorders or refills shots. The format is in rpresolve/shotlist.py.
"""

import json
import os
import sys

from rpresolve import cli
from rpresolve import paths as rppaths

NAME = "shotlist"
HELP = "Offline: check a shot list, or compile it with a cut plan into a draft-farm manifest"


def add_arguments(p):
    sub = p.add_subparsers(dest="action", required=True)
    c = sub.add_parser("check", help="validate a shot list")
    c.add_argument("list")
    k = sub.add_parser("compile", help="write manifest.json, method.json and crops.json")
    k.add_argument("list")
    k.add_argument("--cuts", help="a cut plan (cut_frames, music_in), as `notes snap` writes it")
    k.add_argument("--period", type=float, help="seconds every shot lasts when there is no --cuts")
    k.add_argument("--out", required=True, help="Folder, outside any git tree")
    k.add_argument("--exclude", help="Absolute path of the exclude list")
    k.add_argument("--method", help="An edit method id or path: its look and end rule are used")


def _sizes(sl):
    """{source: [width, height]} for the sources ffprobe can read; the 9:16 preview needs
    them, and a source it cannot read is named in a note."""
    from rpresolve import delivercheck
    out = {}
    for src in sorted({s["source"] for s in sl["shots"]}):
        if not os.path.isfile(src):  # compiled away from the media: no size, no note
            continue
        try:
            delivercheck.check_tools(ffmpeg=False)
            v = next(x for x in delivercheck.probe(src).get("streams", [])
                     if x.get("codec_type") == "video")
            out[src] = [int(v["width"]), int(v["height"])]
        except (delivercheck.CheckError, delivercheck.ToolMissing, StopIteration, KeyError,
                TypeError, ValueError):
            print(f"note: no source_size for {os.path.basename(src)} (ffprobe could not read it)",
                  file=sys.stderr)
    return out


def run(args):
    from rpresolve import cutlist, methods, shotlist
    from rpresolve.workflows import Refused, check_exclude
    try:
        sl = shotlist.load(args.list)
        problems = shotlist.validate(sl)
        if problems:
            for line in problems:
                print(f"ERROR: {line}", file=sys.stderr)
            return 1
        if args.action == "check":
            print(f"{args.list}: {len(sl['shots'])} shots, ok")
            return 0
        if args.cuts and args.period is not None:
            raise shotlist.ShotlistError("give --cuts or --period, not both")
        cuts = shotlist.load(args.cuts) if args.cuts else None
        if args.method:
            m = methods.load(args.method)
            sl = dict(sl, look=m["look"], end=m["rules"]["end"])
        frames = shotlist.shot_frames(sl, cuts, args.period)
        manifest, method = shotlist.compile_list(sl, frames, cuts.get("music_in") if cuts else None)
        check_exclude(
            args.exclude,
            lambda takes: [f"span {i + 1} {os.path.basename(src)} ({take})"
                           for i, src, take in cutlist.exclude_matches(manifest, takes)]
            + [f"music {os.path.basename(src)} ({take})"
               for src, take in cutlist.exclude_music_matches(manifest, takes)],
            "the shot list")
        outputs = {"manifest.json": manifest, "method.json": method}
        crops = shotlist.crops_for(sl, manifest, _sizes(sl))
        if crops:
            from rpresolve import cmd_vertical
            try:
                cmd_vertical.load_crops(crops)
                outputs["crops.json"] = crops
            except cmd_vertical.Refused as e:
                print(f"note: no crops.json ({e})", file=sys.stderr)
        if rppaths.in_any_git_tree(args.out):
            raise rppaths.OutputRefused(f"{args.out} is inside a git working tree. The output "
                                        "names media; write it outside any repository.")
        os.makedirs(args.out, exist_ok=True)
        for name, doc in outputs.items():
            rppaths.write_private(os.path.join(args.out, name), json.dumps(doc, indent=1) + "\n")
    except (shotlist.ShotlistError, Refused, ValueError, OSError, rppaths.OutputRefused) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"{args.out}: {', '.join(outputs)}; {len(manifest['clips'][0]['spans'])} spans, "
          f"{sum(frames)} frames at {sl['fps']:g} fps")
    return 0


if __name__ == "__main__":
    cli.script(__name__)
