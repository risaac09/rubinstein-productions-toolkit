"""
measure: luma, clipping, legal range and skin numbers from a render, and
camera-match numbers per segment.
"""

import json
import sys

from rpresolve import cli
from rpresolve import paths as rppaths

NAME = "measure"
HELP = "Offline: luma, clipping, legal range and skin numbers from a render"


def add_arguments(p):
    p.add_argument("file", help="Rendered video or still")
    p.add_argument("--samples", type=int, default=10, help="Frames to sample (default 10)")
    p.add_argument("--segment", action="append", metavar="LABEL=START-END",
                   help="A camera's time range in seconds; repeat per camera")
    p.add_argument("--hero", help="Segment label whose skin chroma the others are compared to")
    p.add_argument("--no-faces", action="store_true", help="Skip face and skin measurement")
    p.add_argument("--delivery", help="A delivery encode of the file: compare its readable shadow to the master's")
    p.add_argument("--json", help="Also write the full report as JSON here")


def run(args):
    """Measure a rendered file (see rpresolve/measure.py). Prints a summary;
    --json writes the full report. Exit 1 when the file cannot be read."""
    try:
        from rpresolve import measure as rpmeasure
    except ImportError as e:
        print(f"ERROR: measure needs numpy ({e}). Use /usr/bin/python3.", file=sys.stderr)
        return 1
    problem = rppaths.out_problem(args.json, any_git_tree=True) if args.json else None
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    try:
        segments = [rpmeasure.parse_segment(s) for s in args.segment or []]
        if args.hero and args.hero not in [s[0] for s in segments]:
            print(f"ERROR: --hero {args.hero} is not one of the segment labels.", file=sys.stderr)
            return 1
        report = rpmeasure.measure(args.file, samples=args.samples, segments=segments,
                                   hero=args.hero, faces=not args.no_faces,
                                   delivery=args.delivery)
    except rpmeasure.MeasureError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    sys.stdout.write(rpmeasure.format_summary(report))
    if args.json:
        cli.write_private(args.json, json.dumps(report, indent=2) + "\n")
        print(f"Wrote {args.json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    cli.script(__name__)
