"""
qc: one FAIL-first QC table for a rendered file: deliver-check's rows (with
--dest) plus ffmpeg blackdetect, freezedetect, scdet, ebur128, astats and
silencedetect.
Sound rows (VP-121, all thresholds working defaults, see qc.py): lra, phone_proxy,
and with cut times (a start column in --shots, else the file's own scdet cuts)
true_peak_per_cut and silence_at_cut; speech_vs_rest with --words; stems_sum with
--stems. WARN rows do not fail the table.
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
    p.add_argument("--words", metavar="JSON",
                   help="mlx_whisper word-level JSON of this file: adds speech_vs_rest (speech "
                   "loudness against the rest; WARN when the rest is within 6 LU of speech or "
                   "louder, a working default)")
    p.add_argument("--stems", nargs="+", metavar="WAV",
                   help="Stem files: adds stems_sum (stems summed against the mix: integrated "
                   "within 0.1 LU and residual under -50 dBFS, working defaults). Takes every "
                   "path after it, so give it last")
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
                   shots=qc.shot_count(args.shots) if args.shots else None, thresholds=th,
                   shot_cut_times=qc.shot_cuts(args.shots) if args.shots else None,
                   words=qc.load_words(args.words) if args.words else None,
                   stems=args.stems)
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
