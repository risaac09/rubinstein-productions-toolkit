"""
perceive: one frame as a folder of scopes (waveform, parade, vectorscope,
histogram, false colour), a Lab palette and packet.json, optionally against a
reference still, for a session to read beside the frame; see
rpresolve/perceive.py.
"""

import sys

from rpresolve import cli

NAME = "perceive"
HELP = "Offline: one frame as scopes, a Lab palette and numbers, for reading light and colour"


def add_arguments(p):
    p.add_argument("file", help="Video or still")
    p.add_argument("--out", required=True,
                   help="A new folder for the packet (refused inside any git working tree)")
    p.add_argument("--at", type=float, help="Frame time in seconds (default the middle)")
    p.add_argument("--ref", help="A reference still (or the middle of a clip) to compare against")
    p.add_argument("--no-faces", action="store_true", help="Skip face, skin and lighting-ratio numbers")
    p.add_argument("--targets", action="store_true",
                   help="Also write targets.json (numbers only) beside packet.json, for a reference library")


def run(args):
    """Write a perception packet for one frame (see rpresolve/perceive.py).
    Exit 1 when the frame cannot be read or the folder is refused."""
    try:
        from rpresolve import measure as rpmeasure, perceive as rpperceive
    except ImportError as e:
        print(f"ERROR: perceive needs numpy ({e}). Use /usr/bin/python3.", file=sys.stderr)
        return 1
    try:
        packet = rpperceive.perceive(args.file, args.out, at=args.at, ref=args.ref,
                                     faces=not args.no_faces, with_targets=args.targets)
    except (rpmeasure.MeasureError, rpperceive.PerceiveError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    sys.stdout.write(rpperceive.format_summary(packet, args.out))
    return 0


if __name__ == "__main__":
    cli.script(__name__)
