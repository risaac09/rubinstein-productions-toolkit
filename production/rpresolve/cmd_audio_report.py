"""
audio-report: loudness over time, spectrogram, peaks, clipping, clicks,
silences and octave balance of one audio stream, as JSON and two PNGs.
production/resolve-mcp.md says what the report holds.
"""

import json
import sys

from rpresolve import cli

NAME = "audio-report"
HELP = ("Offline: loudness over time, spectrogram, peaks, clipping, clicks, silences and octave "
        "balance of one audio stream, as JSON and PNGs")


def add_arguments(p):
    p.add_argument("file")
    p.add_argument("-o", "--out", required=True,
                   help="Folder for audio-report.json, loudness.png and spectrogram.png, "
                        "outside any git tree (made if its parent exists)")
    p.add_argument("--start", help="Where to start: seconds, m:ss or h:mm:ss (default 0)")
    p.add_argument("--end", help="Where to stop (default: the end)")
    p.add_argument("--stream", type=int, default=0, help="Audio stream (0 is the first)")
    p.add_argument("--dest", help="Take the loudness target and true-peak ceiling from this "
                                  "destination in resolve-config.json")
    p.add_argument("--config", dest="deliver_config",
                   help="A config overlay kept outside this repo (may also go before the "
                        "command)")
    p.add_argument("--target", type=float, help="Integrated loudness target in LUFS "
                                                "(over --dest's)")
    p.add_argument("--ref", help="A reference file to compare with, read whole")
    p.add_argument("--ref-stream", type=int, default=0, help="Audio stream of the reference")
    p.add_argument("--json", action="store_true", help="Print the whole report as JSON")


def run(args):
    """Loudness over time, spectrogram, peaks, clipping, clicks, silences and
    octave balance of one audio stream, as JSON and two PNGs in --out
    (offline; rpresolve/audioreport.py). Exit 0 the report is written,
    flags or not; 1 it could not be."""
    try:
        from rpresolve import audioreport as ar, deliver, delivercheck as dc
    except ImportError as e:
        print(f"ERROR: audio-report needs numpy ({e}). Use /usr/bin/python3.", file=sys.stderr)
        return 1
    try:
        th, target = {}, args.target
        if args.dest:
            loud = deliver.destination(cli.deliver_config(args), args.dest)["loudness"]
            th = {k: loud[k] for k in ("true_peak_max_dbtp", "tolerance_lu")}
            target = loud.get("integrated_lufs") if target is None else target
        start = ar.parse_time(args.start) if args.start else None
        end = ar.parse_time(args.end) if args.end else None
        r = ar.report(args.file, args.out, stream=args.stream, start=start, end=end,
                      reference=args.ref, ref_stream=args.ref_stream, target_lufs=target, th=th)
    except (ar.ReportError, dc.ToolMissing, deliver.DeliverError, ValueError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(r, indent=1))
    else:
        sys.stdout.write(ar.format_summary(r))
    return 0


if __name__ == "__main__":
    cli.script(__name__)
