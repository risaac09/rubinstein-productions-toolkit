"""
selects: propose spans that sit inside the approved text.
"""

import sys

from rpresolve import cli

NAME = "selects"
HELP = "Offline: propose spans inside the approved text"


def add_arguments(p):
    p.add_argument("words", help="mlx_whisper word-level JSON")
    p.add_argument("--approved", required=True, help="The approved text (essay)")
    p.add_argument("--min-seconds", type=float, default=20.0)
    p.add_argument("--max-seconds", type=float, default=65.0)
    p.add_argument("--out", help="Write the TSV here instead of stdout")


def run(args):
    """Propose spans inside the approved text, as a TSV (in, out, seconds,
    coverage, end words, text). Isaac picks; nothing is cut."""
    from rpresolve import cutlist
    try:
        words = cutlist.load_words(args.words)
        approved = cutlist.load_approved(args.approved)
    except (cutlist.CutlistError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    rows = cutlist.selects(words, approved, args.min_seconds, args.max_seconds)
    text = cutlist.selects_tsv(rows)
    if args.out:
        cli.write_private(args.out, text)
        print(f"Wrote {len(rows)} proposal(s) to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    cli.script(__name__)
