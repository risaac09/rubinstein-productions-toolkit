"""
deliver-check: check a rendered file against its destination: codec, size, fps,
colour tags, audio, captions, loudness and name.
"""

import json
import sys

from rpresolve import cli

NAME = "deliver-check"
HELP = "Offline: check a rendered file against its destination"


def add_arguments(p):
    p.add_argument("file")
    cli.deliver_arguments(p)
    p.add_argument("--fps", help="The timeline's frame rate to assert (23.976, 25, 24000/1001)")
    p.add_argument("--size", help="WIDTHxHEIGHT to assert for a timeline-size destination")
    p.add_argument("--audio-state", help="An audio-state JSON (audio-state -o): also compare the "
                   "audio codec, sample rate and bit depth the queued Resolve jobs report "
                   "with the destination")
    p.add_argument("--no-loudness", action="store_true",
                   help="Skip the loudness read (it reads the whole file)")
    p.add_argument("--json", action="store_true", help="Print the result as JSON")


def run(args):
    """Check a rendered file against its destination. Exit 0 all pass, 1 any
    fail (or an unreadable file), 2 ffprobe or ffmpeg missing."""
    from rpresolve import audiostate, deliver, delivercheck as dc
    try:
        config = cli.deliver_config(args)
        dest = deliver.destination(config, args.dest)
        size = cli.size(args.size) if args.size else None
        if args.fps:
            dc.parse_fps(args.fps)
        r = dc.check(args.file, dest, fps=args.fps, size=size, loudness=not args.no_loudness,
                     folders=deliver.subfolders(config))
        if args.audio_state:
            dc.add_rows(r, audiostate.compare(audiostate.load(args.audio_state), dest))
    except dc.ToolMissing as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except (dc.CheckError, deliver.DeliverError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(r, indent=1, default=str))
    else:
        sys.stdout.write(dc.format_report(r))
    return 0 if r["status"] == "pass" else 1


if __name__ == "__main__":
    cli.script(__name__)
