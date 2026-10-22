"""
qc: one FAIL-first QC table for a rendered file: deliver-check's rows (with
--dest) plus ffmpeg blackdetect, freezedetect, scdet, ebur128, astats and
silencedetect (portrait renders add hook WARN rows that never change the exit).
"""

import json
import sys

from rpresolve import cli

NAME = "qc"
HELP = "Offline: one FAIL-first QC table for a rendered file"


def add_arguments(p):
    p.add_argument("file")
    p.add_argument("--dest", help="Destination key from resolve-config.json; adds "
                   "deliver-check's rows and the loudness target")
    p.add_argument("--config", dest="deliver_config",
                   help="A config overlay kept outside this repo (may also go before the command)")
    p.add_argument("--fps", help="The timeline's frame rate to assert (with --dest)")
    p.add_argument("--size", help="WIDTHxHEIGHT to assert for a timeline-size destination")
    p.add_argument("--duration", type=float, metavar="SECONDS",
                   help="Expected duration (the lock); adds a duration row")
    p.add_argument("--shots", metavar="CSV",
                   help="Shot list: a header row, then one row per shot; the cut count is checked "
                   "against rows minus one")
    p.add_argument("--set", action="append", metavar="KEY=VALUE",
                   help="Override a threshold (repeatable); keys are in rpresolve/qc.py THRESHOLDS")
    p.add_argument("--json", action="store_true", help="Print the result as JSON")


def run(args):
    """The QC table: deliver-check's rows (with --dest) plus the ffmpeg
    rows. Exit 0 all pass, 1 any fail (or an unreadable file), 2 ffprobe or
    ffmpeg missing."""
    from rpresolve import deliver, delivercheck as dc, qc
    try:
        th = qc.parse_overrides(args.set)
        if (args.fps or args.size) and not args.dest:
            raise ValueError("--fps and --size assert against a destination: add --dest.")
        dest = folders = None
        if args.dest:
            config = cli.deliver_config(args)
            dest, folders = deliver.destination(config, args.dest), deliver.subfolders(config)
        size = cli.size(args.size) if args.size else None
        if args.fps:
            dc.parse_fps(args.fps)
        r = qc.run(args.file, dest, config_folders=folders, fps=args.fps, size=size,
                   expected_duration=args.duration,
                   shots=qc.shot_count(args.shots) if args.shots else None, thresholds=th)
    except dc.ToolMissing as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except (dc.CheckError, deliver.DeliverError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(r, indent=1, default=str))
    else:
        sys.stdout.write(qc.format_report(r))
    return 0 if r["status"] == "pass" else 1


if __name__ == "__main__":
    cli.script(__name__)
