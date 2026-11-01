"""
endcheck: check every cut's end word, that it sits in a pause of the audio, and
that it says only what the approved text says.
"""

import json
import sys

from rpresolve import cli
from rpresolve import paths as rppaths

NAME = "endcheck"
HELP = "Offline: check every cut's end, pause and text"


def add_arguments(p):
    p.add_argument("manifest")
    p.add_argument("--no-audio", action="store_true", help="Word and text checks only")
    p.add_argument("--json", help="Also write the full results here")


def run(args):
    """Check a manifest. Exit 0 all pass, 2 any review, 1 any fail."""
    from rpresolve import cutlist
    problem = rppaths.out_problem(args.json, any_git_tree=True) if args.json else None
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    try:
        m = cutlist.load_manifest(args.manifest)
        rows = cutlist.endcheck(m, audio=not args.no_audio)
    except (cutlist.CutlistError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    for r in rows:
        print(cutlist.endcheck_line(r))
    n = cutlist.endcheck_counts(rows)
    fails, reviews = n["fail"], n["review"]
    print(f"endcheck: {len(rows)} span(s): {n['pass']} pass, {reviews} review, "
          f"{fails} fail" + (" (audio not checked)" if args.no_audio else ""), file=sys.stderr)
    if args.json:
        cli.write_private(args.json, json.dumps(rows, indent=1, default=str) + "\n")
    return 1 if fails else (2 if reviews else 0)


if __name__ == "__main__":
    cli.script(__name__)
