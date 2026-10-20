"""
deliver-fix-loudness: the loudness step of the deliver loop: two-pass loudnorm
to the destination's target, video copied untouched, into <stem>.loudfix<ext>.
"""

import json
import sys

from rpresolve import cli

NAME = "deliver-fix-loudness"
HELP = "Offline: normalise loudness, video copied untouched"


def add_arguments(p):
    p.add_argument("file")
    cli.deliver_arguments(p)
    p.add_argument("--replace", action="store_true",
                   help="After the fixed file passes, move the original to ~/.Trash and give "
                        "the fix its name")
    p.add_argument("--fps", help="The timeline's frame rate to assert in the check")
    p.add_argument("--size", help="WIDTHxHEIGHT to assert for a timeline-size destination")
    p.add_argument("--json", action="store_true", help="Print the result as JSON")


def run(args):
    """Normalise a render's loudness to its destination's target into
    <stem>.loudfix<ext>, then check it. --replace swaps it in only after
    the check passes, moving the original to ~/.Trash. Exit 0 the fixed
    file passes, 1 it does not (or the fix failed), 2 ffmpeg missing."""
    from rpresolve import deliver, delivercheck as dc
    try:
        dest = deliver.destination(cli.deliver_config(args), args.dest)
        size = cli.size(args.size) if args.size else None
        if args.fps:
            dc.parse_fps(args.fps)
        r = dc.fix_loudness(args.file, dest, replace=args.replace, fps=args.fps, size=size)
    except dc.ToolMissing as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except (dc.CheckError, deliver.DeliverError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(r, indent=1, default=str))
    else:
        m, out = r["measured"], r["result_loudnorm"]
        print(f"Measured:  I {m['input_i']} LUFS, true peak {m['input_tp']} dBTP, "
              f"LRA {m['input_lra']} LU")
        print(f"loudnorm:  {out.get('normalization_type')} mode, output I {out.get('output_i')} "
              f"LUFS, true peak {out.get('output_tp')} dBTP")
        sys.stdout.write(dc.format_report(r["check"]))
        print(f"Video stream bit-identical: {'yes' if r['video_identical'] else 'NO'}")
        if r["replaced"]:
            print(f"Replaced: {r['fixed']} (the original is in {r['trashed']})")
        else:
            print(f"Fixed file: {r['fixed']} (the original is untouched)")
    for w in r["warnings"]:
        print(f"  WARNING: {w}", file=sys.stderr)
    return 0 if r["status"] == "pass" else 1


if __name__ == "__main__":
    cli.script(__name__)
