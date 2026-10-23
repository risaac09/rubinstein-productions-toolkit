"""
detect: classify each media file's camera and picture profile from its headers
(ffprobe, exiftool), with the proposed Input Color Space and Data Level.
"""

import sys

from rpresolve import cli
from rpresolve import detect as rpdetect
from rpresolve import paths as rppaths
from rpresolve import workflows as rpwork

NAME = "detect"
HELP = "Offline: classify camera and picture profile from file headers"


def add_arguments(p):
    p.add_argument("paths", nargs="+", help="Media files or directories (searched recursively)")
    p.add_argument("--out", "-o", help="Write the table here instead of stdout")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of TSV")


def run(args):
    """Classify each media file's camera and picture profile from its
    headers and write a TSV (or JSON). Never connects to Resolve.
    A pinned row carries an input_color_space at high or medium confidence;
    anything the survey grades lower is 'review' with an empty one.
    Exit status: 0 when every file is pinned, 2 when any row is 'review',
    'corrupt' or low confidence, 1 when the tools are missing or no file
    was found. A file that fails to probe becomes a 'review' row and the
    run goes on."""
    # The same guards as the survey, checked before any file is probed:
    # rows carry full media paths, and this repository is public.
    problem = rppaths.out_problem(args.out) if args.out else None
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    try:
        r = rpwork.detect(args.paths)
    except (rpdetect.ToolMissing, rpwork.Refused) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    rows = r["rows"]
    for m in r["missing"]:
        print(f"  [skip] Not found: {m}", file=sys.stderr)

    text = rpdetect.format_json(rows) if args.json else rpdetect.format_tsv(rows)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Wrote {len(rows)} row(s) to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    print(r["summary"], file=sys.stderr)
    return r["exit_status"]


if __name__ == "__main__":
    cli.script(__name__)
