"""
tilt-check: find tilt-fixture's markers in the still you rendered and say
whether Pan and Tilt landed where reframe-plan's convention puts them. A person
renders the still, so no MCP tool is planned.
"""

import json
import os
import sys

from rpresolve import cli
from rpresolve import paths as rppaths

NAME = "tilt-check"
HELP = ("Offline: find the fixture's markers in the still you rendered and say whether Pan and "
        "Tilt landed where reframe-plan puts them (exit 0 both, 2 not)")


def add_arguments(p):
    p.add_argument("still", help="The rendered still (PNG, JPEG, TIFF)")
    p.add_argument("--fixture", required=True, help="The folder tilt-fixture wrote")
    p.add_argument("--tolerance", type=float, metavar="PX",
                   help="Pixels the markers may be off (default 1%% of the render width, at least 4)")


def run(args):
    """Find the fixture's markers in a rendered still and say whether Pan and
    Tilt landed where reframe-plan's convention puts them. Writes
    tilt-result.json beside the fixture. Exit 0 both confirmed, 2 inverted or
    unclear, 1 error. It confirms nothing by itself: the constant that drops
    the unconfirmed note changes in a commit that cites this output."""
    from rpresolve import reframe
    folder = os.path.abspath(args.fixture)
    try:
        with open(os.path.join(folder, reframe.TILT_EXPECT_FILE), encoding="utf-8") as f:
            expect = json.load(f)
        w, h, rgb = reframe.read_rgb(os.path.abspath(args.still))
        result = reframe.tilt_check(rgb, (w, h), expect, tolerance=args.tolerance)
    except (reframe.ReframeError, OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    result["still"] = os.path.basename(args.still)
    result["input_scaling"] = expect.get("input_scaling")
    path = os.path.join(folder, reframe.TILT_RESULT_FILE)
    problem = rppaths.out_problem(path, any_git_tree=True)
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    cli.write_private(path, json.dumps(result, indent=1) + "\n")
    f = result["found"]
    print(f"render {w}x{h}; white marker found at {f['a']}, expected {result['expected']['a']}; "
          f"red marker found at {f['b']}, expected {result['expected']['b']} "
          f"(tolerance {result['tolerance']:g} px)")
    print(f"Pan {result['pan'].upper()}, Tilt {result['tilt'].upper()}")
    for n in result["notes"]:
        print(f"  {n}")
    if result["ok"]:
        print("Both match this module's convention. To drop the unconfirmed note, change "
              "TILT_CONFIRMED in rpresolve/reframe.py in a commit that cites this result.")
    print(f"Wrote {path}", file=sys.stderr)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    cli.script(__name__)
