"""
sting-sync: does the intro's sound sting land on its picture keyframes?
Re-measures each hit from the audio against the lockup's keyframes; signed
offsets in frames and ms, FAIL first; see rpresolve/stingsync.py.
"""

import json
import sys

from rpresolve import cli

NAME = "sting-sync"
HELP = "Offline: does the sting's sound land on the lockup's picture keyframes"


def add_arguments(p):
    p.add_argument("--lockup", required=True, help="lockup.json (one cut, or {cuts: {long, bumper}})")
    p.add_argument("--cues", required=True, help="cues.json with measured hit onsets")
    p.add_argument("--cut", choices=["long", "bumper"], help="Which cut (default: the cues' cut, else long)")
    p.add_argument("--wav", help="The sting master; each hit is re-measured within 150 ms of its picture event")
    p.add_argument("--transient", help="The transient stem; measured instead of the master when given")
    p.add_argument("--lead-ms", type=float, default=40.0, help="Fail a lead over this (default 40)")
    p.add_argument("--lag-ms", type=float, default=60.0, help="Fail a lag over this (default 60)")
    p.add_argument("--tol-ms", type=float, default=1.0, help="Fail a declared onset this far off (default 1)")
    p.add_argument("--json", action="store_true", help="Rows as JSON instead of the table")


def run(args):
    """Sting timing against the lockup keyframes (VP-146). Exit 0 pass,
    1 any FAIL, 2 an input missing, unreadable or breaking the contract."""
    from rpresolve import stingsync as ss
    try:
        rows = ss.run(args.lockup, args.cues, master=args.wav, stem=args.transient, cut=args.cut,
                      lead_ms=args.lead_ms, lag_ms=args.lag_ms, tol_ms=args.tol_ms)
    except ss.StingError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(json.dumps(rows, indent=1) + "\n" if args.json else ss.format_table(rows))
    return int(any(r["status"] == ss.FAIL for r in rows))


if __name__ == "__main__":
    cli.script(__name__)
