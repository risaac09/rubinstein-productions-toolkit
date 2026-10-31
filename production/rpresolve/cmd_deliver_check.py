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
    p.add_argument("--boxes", help="JSON list of {label, box: [x0, y0, x1, y1]} frame fractions "
                   "for the captions and supers on screen; each must sit inside the safe zone")
    p.add_argument("--no-loudness", action="store_true",
                   help="Skip the loudness read (it reads the whole file)")
    p.add_argument("--ledger-row", metavar="PATH",
                   help="Also write a release-ledger row stub (one JSON line, keyed to the house "
                   "file name) to PATH, replacing any file there (one row per file); refused inside any git tree")
    p.add_argument("--json", action="store_true", help="Print the result as JSON")


def run(args):
    """Check a rendered file against its destination. Exit 0 all pass, 1 any
    fail (or an unreadable file or boxes JSON), 2 ffprobe or ffmpeg missing."""
    from rpresolve import audiostate, deliver, delivercheck as dc, paths, safezone
    ledger = getattr(args, "ledger_row", None)
    try:
        if ledger:
            inputs = [args.file, args.audio_state, getattr(args, "boxes", None),
                      getattr(args, "deliver_config", None), getattr(args, "config", None)]
            problem = (paths.out_problem(ledger, any_git_tree=True)
                       or paths.source_problem([ledger], [p for p in inputs if p]))
            if problem:
                raise ValueError(problem)
        config = cli.deliver_config(args)
        dest = deliver.destination(config, args.dest)
        size = cli.size(args.size) if args.size else None
        if args.fps:
            dc.parse_fps(args.fps)
        boxes = safezone.load_boxes(args.boxes) if getattr(args, "boxes", None) else None
        r = dc.check(args.file, dest, fps=args.fps, size=size, loudness=not args.no_loudness,
                     folders=deliver.subfolders(config), boxes=boxes)
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
    if ledger:  # after the report, so a write error never hides the result
        try:
            line = dc.ledger_line(dc.ledger_row(r, sha256=dc.file_sha256(args.file)))
            paths.write_private(ledger, line, any_git_tree=True)
        except (OSError, ValueError) as e:
            print(f"ERROR: ledger row not written: {e}", file=sys.stderr)
            return 1
    return 0 if r["status"] == "pass" else 1


if __name__ == "__main__":
    cli.script(__name__)
