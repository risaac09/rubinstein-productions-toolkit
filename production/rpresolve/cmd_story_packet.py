"""
story-packet: a shoot as story material in one Markdown file for the selects
board: per clip in capture order, the shoot-table row, an audio energy
sparkline with the loud spans, the words as timed phrases, any picture labels
and, with --strips, a contact strip of eight frames. It scores and chooses
nothing.
"""

import os
import sys

from rpresolve import cli
from rpresolve import paths as rppaths

NAME = "story-packet"
HELP = "Offline: a shoot as story material in one Markdown file"


def add_arguments(p):
    p.add_argument("table", help="The shoot-table CSV (one row per clip)")
    p.add_argument("--words", help="Folder of mlx_whisper word JSON, one <stem>.json per clip")
    p.add_argument("--labels", help="JSONL of picture labels: file, start_s, end_s, text")
    p.add_argument("--title", help="The packet's title (default: the table's name)")
    p.add_argument("--out", help="Write the packet here instead of stdout (refused in this repo)")
    p.add_argument("--no-audio", action="store_true", help="Skip the energy line (no decode)")
    p.add_argument("--strips", help="Write a contact strip per clip (8 frames, 2 rows) into this folder "
                                    "and link it from each card (refused in any git tree)")


def run(args):
    """One Markdown packet from a shoot table, words and labels, for the
    selects board. Scores and chooses nothing. Exit 0 written, 1 failed."""
    from rpresolve import cutlist, storypacket as sp
    problem = rppaths.out_problem(args.out, any_git_tree=True) if args.out else None
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    if args.words and not os.path.isdir(args.words):
        print(f"ERROR: --words {args.words} is not a folder.", file=sys.stderr)
        return 1
    if args.strips:
        # Strips show people: refused in any git tree, not only this one.
        problem = rppaths.out_problem(os.path.join(args.strips, "strip.jpg"), any_git_tree=True)
        if problem:
            print(f"ERROR: --strips: {problem}", file=sys.stderr)
            return 1
        os.makedirs(args.strips, exist_ok=True)
    try:
        text, summary = sp.build(args.table, args.words, args.labels, audio=not args.no_audio,
                                 title=args.title, strips_dir=args.strips,
                                 link_base=os.path.dirname(os.path.abspath(args.out))
                                 if args.out else None)
    except (sp.PacketError, cutlist.CutlistError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.out:
        cli.write_private(args.out, text)
        print(f"Wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    print(summary, file=sys.stderr)
    return 0


if __name__ == "__main__":
    cli.script(__name__)
