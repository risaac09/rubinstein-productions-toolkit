"""
manifest: build a cut manifest from a CUTS script, a vertcut TSV or a video-use
edl.json: the manifest that endcheck, reframe-plan, trim-review and the MCP cut
tool read.
"""

import json
import os
import sys

from rpresolve import cli
from rpresolve import paths as rppaths

NAME = "manifest"
HELP = "Offline: build a cut manifest"


def add_arguments(p):
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-cuts", metavar="PY", help="A Python file with a literal CUTS dict")
    src.add_argument("--from-tsv", metavar="TSV", help="A vertcut cuts TSV")
    src.add_argument("--from-edl", metavar="JSON", help="A video-use edl.json")
    p.add_argument("--source", required=True, help="The source media file")
    p.add_argument("--fps", type=float, required=True, help="Source frame rate")
    p.add_argument("--words", required=True, help="mlx_whisper word-level JSON of the source")
    p.add_argument("--approved", required=True, help="The approved text (essay) clips must stay inside")
    p.add_argument("--offset", type=float, default=0.0, help="Seconds added to TSV timecodes")
    p.add_argument("--face-x", type=float, help="Speaker's face centre in source pixels, for 9:16")
    p.add_argument("--out", required=True, help="Manifest JSON path (refused inside this repo)")


def run(args):
    """Build a cut manifest (see rpresolve/cutlist.py) and write it to --out."""
    from rpresolve import cutlist
    problem = rppaths.out_problem(args.out, any_git_tree=True)
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    try:
        if args.from_cuts:
            spans = cutlist.spans_from_cuts_script(args.from_cuts)
        elif args.from_tsv:
            spans = cutlist.spans_from_vertcut_tsv(args.from_tsv, args.fps, args.offset)
        else:
            spans = cutlist.spans_from_edl(args.from_edl)
        reframe = {"face_x": args.face_x} if args.face_x is not None else None
        m = cutlist.build_manifest(spans, os.path.abspath(args.source), args.fps,
                                   os.path.abspath(args.words), os.path.abspath(args.approved),
                                   reframe=reframe)
    except (cutlist.CutlistError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    cli.write_private(args.out, json.dumps(m, indent=1) + "\n")
    n = sum(len(c["spans"]) for c in m["clips"])
    print(f"Wrote {len(m['clips'])} clip(s), {n} span(s) to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    cli.script(__name__)
