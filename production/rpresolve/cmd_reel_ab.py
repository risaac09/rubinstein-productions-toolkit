"""
reel-ab: build a blind A/B packet from 2 to 4 renders of one 9:16 reel. Each is cut
to the same span, scaled to 720x1280 H.264/AAC with faststart (under 25 MiB), and
loudness-matched within 0.5 LU. The files are lettered A, B ... in a seeded order
and written to --out; the key goes to OUT.key/key.json, apart from the folder you
show a reviewer, where review-log reads it. --opening adds frame0.jpg, a sheet of
each file's first frame (needs the rp-tools venv). Refuses any git tree, an
existing folder, a render that is not 9:16, and a span past the shortest render.
Exit 2 on a refusal. Touches neither Resolve nor the inputs.
"""

import sys

from rpresolve import cli

NAME = "reel-ab"
HELP = "Build a lettered, loudness-matched 720p 9:16 A/B packet with a sealed key"


def add_arguments(p):
    p.add_argument("files", nargs="+", metavar="RENDER", help="2 to 4 renders of the same reel")
    p.add_argument("--out", required=True, help="a new folder outside any git tree; the key goes to OUT.key")
    p.add_argument("--start", type=float, default=0.0, help="seconds into each render (default 0)")
    p.add_argument("--seconds", type=float, default=10.0, help="span length, 30 at most (default 10)")
    p.add_argument("--seed", type=int, help="shuffle seed (default: random, kept in the key)")
    p.add_argument("--opening", action="store_true", help="also write frame0.jpg, each file's first frame")


def run(args):
    from rpresolve import delivercheck as dc
    from rpresolve import reelab
    try:
        packet = reelab.build(args.files, args.out, args.start, args.seconds, args.seed, args.opening)
    except (reelab.ReelAbError, dc.ToolMissing, dc.CheckError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print(f"packet: {args.out} ({len(packet['labels'])} files, spread {packet['spread_lu']} LU)")
    print(f"key: {reelab.key_dir(args.out)}/key.json (keep it out of what the reviewer sees)")
    return 0


if __name__ == "__main__":
    cli.script(__name__)
