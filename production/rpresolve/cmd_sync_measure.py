"""
sync-measure: where one recording sits against another, from their audio:
offset in seconds and frames, clock drift, confidence; it refuses to call a
weak match a match. The MCP sync tool stacks the pair.
"""

import json
import sys

from rpresolve import cli

NAME = "sync-measure"
HELP = ("Offline: where one recording sits against another, from their audio (offset, drift, "
        "confidence)")


def _fps_arg(text):
    """--fps as a float (23.976, 25, 24000/1001); raises ValueError."""
    from rpresolve import delivercheck as dc
    return float(dc.parse_fps(text))


def add_arguments(p):
    p.add_argument("reference", help="The reference: the camera clip")
    p.add_argument("other", help="The other recording: an audio file or a second camera")
    p.add_argument("--fps", help="Frame rate for the offset in frames (default: the "
                                 "reference's)")
    p.add_argument("--window", type=float, default=30.0,
                   help="Seconds per measuring window (default 30)")
    p.add_argument("--ref-stream", type=int, default=0, help="Audio stream of the reference "
                                                             "(0 is the first)")
    p.add_argument("--other-stream", type=int, default=0, help="Audio stream of the other")
    p.add_argument("--json", action="store_true", help="Print the whole report as JSON")


def run(args):
    """Measure where <other> sits against <reference> from the sound both
    recorded (offline; rpresolve/sync.py). Exit 0 a match, 2 a match whose
    placement leaves either end of the overlap more than half a frame out
    (frame rounding plus half the drift; reported, never corrected), 1 no
    match or a file that cannot be read."""
    try:
        from rpresolve import sync as rpsync
    except ImportError as e:
        print(f"ERROR: sync-measure needs numpy ({e}). Use /usr/bin/python3.", file=sys.stderr)
        return 1
    try:
        fps = _fps_arg(args.fps) if args.fps else None
        r = rpsync.measure(args.reference, args.other, fps=fps, window_s=args.window,
                           ref_stream=args.ref_stream, other_stream=args.other_stream)
    except (rpsync.SyncError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(r, indent=1, default=str))
    else:
        sys.stdout.write(rpsync.format_summary(r))
    if not r["match"]:
        return 1
    return 2 if (r.get("drift") or {}).get("exceeds") else 0


if __name__ == "__main__":
    cli.script(__name__)
