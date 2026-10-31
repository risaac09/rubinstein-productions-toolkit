"""
reel-spans: turn the start and end phrases a person quotes into a checked vertcut cuts
worksheet (id, in, out, title). Offline. It proposes nothing: it only locates the quoted
words in a word-timed transcript JSON. The report gives OK, WARN or FAIL per row; a FAIL row
is left out of the worksheet and the exit status is 1. Refuses an output inside any git
working tree (exit 2). The work is rpresolve/reelspans.py.
"""

import sys

from rpresolve import cli, cutlist, paths, reelspans

NAME = "reel-spans"
HELP = "Offline: quoted start and end phrases to a checked vertcut cuts worksheet"


def add_arguments(p):
    p.add_argument("words", help="word-timed transcript JSON (Whisper, as cutlist reads it)")
    p.add_argument("phrases", help="TSV: title, start phrase, end phrase[, start occurrence, end occurrence]")
    p.add_argument("-o", "--out", required=True, help="cuts worksheet TSV to write, outside any git tree")
    p.add_argument("--min-s", type=float, default=reelspans.MIN_S, help="WARN under this many seconds (default %(default)g)")
    p.add_argument("--max-s", type=float, default=reelspans.MAX_S, help="WARN over this many seconds (default %(default)g)")


def run(args):
    if not 0 <= args.min_s < args.max_s:
        print("ERROR: --min-s must be at least 0 and below --max-s", file=sys.stderr)
        return 2
    try:
        results, text, out = reelspans.build(args.words, args.phrases, args.out, args.min_s, args.max_s)
    except (paths.OutputRefused, cutlist.CutlistError, ValueError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print(text)
    print(f"wrote {out}" if out else f"nothing written: no row passed; any earlier {args.out} is stale")
    return 1 if any(r["fails"] for r in results) else 0


if __name__ == "__main__":
    cli.script(__name__)
