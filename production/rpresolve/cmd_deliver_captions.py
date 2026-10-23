"""
deliver-captions: turn Resolve's <stem>_<track>.ttml sidecar into a zero-based
<stem>.srt beside the render.
"""

import json
import sys

from rpresolve import cli

NAME = "deliver-captions"
HELP = "Offline: Resolve's .ttml sidecar to a zero-based <stem>.srt beside the render"


def add_arguments(p):
    p.add_argument("file")
    cli.deliver_arguments(p)
    p.add_argument("--track", help="The subtitle track's sidecar to convert, when Resolve "
                                   "wrote more than one (<stem>_<track>.ttml)")
    p.add_argument("--keep-ttml", action="store_true",
                   help="Leave the .ttml beside the file (default: moved to ~/.Trash)")
    p.add_argument("--json", action="store_true", help="Print the result as JSON")


def run(args):
    """Turn Resolve's TTML sidecar beside a render into a zero-based
    <stem>.srt, read it back, and (unless --keep-ttml) move the TTML to
    ~/.Trash. Exit 0 written and verified, 1 refused or failed, 2 ffprobe
    missing."""
    from rpresolve import captions, deliver, delivercheck as dc
    try:
        dest = deliver.destination(cli.deliver_config(args), args.dest)
        r = captions.deliver_captions(args.file, dest, track=args.track,
                                      keep_ttml=args.keep_ttml)
    except dc.ToolMissing as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except (dc.CheckError, deliver.DeliverError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(r, indent=1, default=str))
    else:
        sys.stdout.write(captions.format_result(r))
    for w in r["warnings"]:
        print(f"  WARNING: {w}", file=sys.stderr)
    return 0 if r["status"] == "pass" else 1


if __name__ == "__main__":
    cli.script(__name__)
