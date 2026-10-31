"""
overlay-check: check a graphic master rendered with alpha: alpha, colour tags,
clear and opaque frames, edge fringe and brand colour, with the colour values
passed in from a file kept outside the repo. Not yet registered as an MCP tool.
"""

import json
import sys

from rpresolve import cli

NAME = "overlay-check"
HELP = ("Offline: alpha, tags, clear and opaque frames, edge fringe and brand colour of a graphic "
        "master")


def add_arguments(p):
    p.add_argument("file")
    p.add_argument("--expect", action="append", metavar="KEY=VALUE",
                   help="A tag the file must carry: color_range, color_space, color_primaries, "
                        "color_transfer or pix_fmt; repeat per tag")
    p.add_argument("--fps", help="The frame rate to assert (23.976, 25, 24000/1001)")
    p.add_argument("--clear-at", action="append", type=float, metavar="SECONDS",
                   help="A time with nothing visible; repeat (default the first frame)")
    p.add_argument("--no-clear", action="store_true",
                   help="Skip the clear row, for a graphic that starts visible")
    p.add_argument("--hold", type=float, metavar="SECONDS",
                   help="The time of the opaque, fringe and brand rows (default mid-file)")
    p.add_argument("--alpha-mode", choices=("straight", "premultiplied"), default="straight",
                   help="How the timeline will read the alpha (default straight)")
    p.add_argument("--values", help="JSON of name -> '#rrggbb', kept outside this repo")
    p.add_argument("--sample", action="append", metavar="NAME=X,Y[@SECONDS]",
                   help="A pixel that must match the value NAME; repeat")
    p.add_argument("--sample-box", type=int,
                   help="Side of the square each sample averages, odd; 1 for a hairline "
                        "(default: SAMPLE_BOX in rpresolve/overlay.py)")
    p.add_argument("--fringe-max", type=float,
                   help="Edge error allowed, 10-bit codes at the median edge pixel "
                        "(default: FRINGE_MAX in rpresolve/overlay.py)")
    p.add_argument("--de-max", type=float,
                   help="CIEDE2000 allowed at a brand sample (default: DE_MAX in "
                        "rpresolve/overlay.py)")
    p.add_argument("--json", action="store_true", help="Print the result as JSON")
    p.add_argument("--boxes-out", metavar="PATH",
                   help="Write the safe row's box (measured at the hold time) as the JSON deliver-check --boxes reads; "
                        "refused inside any git tree")


def run(args):
    """Check a graphic rendered with alpha (see rpresolve/overlay.py). Exit 0
    no FAIL row, 1 any FAIL (or an unreadable file), 2 ffprobe or ffmpeg missing."""
    try:
        from rpresolve import measure as rpmeasure, overlay
    except ImportError as e:
        print(f"ERROR: overlay-check needs numpy ({e}). Use /usr/bin/python3.", file=sys.stderr)
        return 1
    try:
        expect = [overlay.parse_expect(s) for s in args.expect or []]
        samples = [overlay.parse_sample(s) for s in args.sample or []]
        values = overlay.load_values(args.values) if args.values else None
        clear_at = [] if args.no_clear else (args.clear_at or [0.0])
        r = overlay.check(args.file, expect=expect, fps=args.fps, clear_at=clear_at,
                          hold=args.hold, alpha_mode=args.alpha_mode, values=values,
                          samples=samples,
                          fringe_max=overlay.FRINGE_MAX if args.fringe_max is None else args.fringe_max,
                          de_max=overlay.DE_MAX if args.de_max is None else args.de_max,
                          sample_size=overlay.SAMPLE_BOX if args.sample_box is None else args.sample_box)
    except overlay.ToolMissing as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except (rpmeasure.MeasureError, OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(r, indent=1))
    else:
        sys.stdout.write(overlay.format_report(r))
    code = 0 if r["status"] == "pass" else 1
    if args.boxes_out:  # after the report, so a refused path does not hide the rows
        from rpresolve import safezone
        box = next((x["box"] for x in r["rows"] if x["row"] == "safe"), None)
        try:
            if box is None:  # an empty list would tell deliver-check nothing is on screen
                raise ValueError("--boxes-out: no visible pixels at the hold time, so no box")
            safezone.write_boxes(args.boxes_out, [("graphic", box)])
        except (OSError, ValueError) as e:  # ValueError: paths.OutputRefused
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
    return code


if __name__ == "__main__":
    cli.script(__name__)
