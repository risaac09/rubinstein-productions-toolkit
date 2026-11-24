"""
scene-colour: the colour block of the per-shot scene model, one per perceive
packet, in the order given, with each shot's palette, skin-hue and cast
difference from the one before. Reads perceive's packet.json (and
targets.json for the shadow-to-highlight tint) and, with --detect, detect's
JSON table for the input colour space. Measures nothing new; never connects
to Resolve. Each block lists its taste fields and asks the harmony question;
no number is a pass or a fail. Module: rpresolve/scene_colour.py.
"""

import json
import sys

from rpresolve import cli
from rpresolve import paths as rppaths

NAME = "scene-colour"
HELP = "Offline: the per-shot colour block (cast, skin, palette, tint, input space) from perceive packets"


def add_arguments(p):
    p.add_argument("packets", nargs="+", help="perceive packet folders, in story order")
    p.add_argument("--detect", metavar="JSON", help="detect --json output, for input_space")
    p.add_argument("--out", "-o", help="Write the JSON here instead of stdout")


def run(args):
    """Print or write the colour blocks. Exit 0 written, 1 error."""
    from rpresolve import scene_colour
    problem = rppaths.out_problem(args.out, any_git_tree=True) if args.out else None
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    try:
        rows = None
        if args.detect:
            with open(args.detect, encoding="utf-8") as f:
                rows = json.load(f)
        shots = scene_colour.shots(args.packets, rows)
    except (scene_colour.SceneColourError, OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    text = json.dumps({"axis": "colour", "shots": shots}, indent=1) + "\n"
    if args.out:
        cli.write_private(args.out, text)
        print(f"Wrote {len(shots)} shot(s) to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    cli.script(__name__)
