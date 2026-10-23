"""
shoot-table: one CSV row per clip: camera, start, duration, fps, size, profile,
lens and audio, with long-take, VFR, weak-audio and unexpected-camera flags and
a per-camera --offset. The last columns audit capture audio (streams, noise
floor, clipping, dead channel, dual mono, room tone) over the first
--audio-seconds. It calls detect for camera and profile.
"""

import sys

from rpresolve import cli
from rpresolve import detect as rpdetect
from rpresolve import shoot_table as rpshoot
from rpresolve import paths as rppaths

NAME = "shoot-table"
HELP = "Offline: one CSV row per clip with flags for long, VFR and weak-audio takes"


def add_arguments(p):
    p.add_argument("paths", nargs="+", help="Absolute paths of media files or folders (searched recursively)")
    p.add_argument("--out", "-o", help="Write the CSV here instead of stdout")
    p.add_argument("--offset", action="append", metavar="CAM=SECONDS",
                   help="Clock offset added to local_start for one camera name as detect names it (repeatable)")
    p.add_argument("--long-min", type=float, default=rpshoot.LONG_TAKE_MIN,
                   help="Flag takes longer than this many minutes (default %(default)s)")
    p.add_argument("--weak-db", type=float, default=rpshoot.WEAK_AUDIO_DB,
                   help="Flag audio whose mean volume is below this dB (default %(default)s)")
    p.add_argument("--audio-seconds", type=float, default=rpshoot.AUDIO_SECONDS,
                   help="Audit at most this many seconds of audio per clip (working default %(default)s). "
                        "Other audit thresholds are working defaults in shoot_table.py: silent-audio under "
                        f"{rpshoot.SILENT_DB:g} dBFS in the first {rpshoot.SILENT_S:g} s, dead channel "
                        f"{rpshoot.DEAD_DB:g} dB under the loudest, dual mono at correlation "
                        f"{rpshoot.DUAL_MONO_R}, no-room-tone under {rpshoot.TONE_MIN_S:g} s within "
                        f"{rpshoot.TONE_DB:g} dB of the noise floor")


def run(args):
    """Write the shoot table CSV (see rpresolve/shoot_table.py). Never
    connects to Resolve. Exit status: 0 clean, 2 when any row carries a
    flag, 1 on a usage error, missing tools or no file found."""
    problem = rppaths.out_problem(args.out) if args.out else None
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    offsets = {}
    for item in args.offset or []:
        cam, sep, secs = item.rpartition("=")
        try:
            offsets[cam] = float(secs)
        except ValueError:
            cam = ""
        if not sep or not cam:
            print(f"ERROR: --offset wants CAM=SECONDS (got '{item}')", file=sys.stderr)
            return 1
    try:
        rows, missing = rpshoot.build(args.paths, long_min=args.long_min, weak_db=args.weak_db,
                                      offsets=offsets, audio_seconds=args.audio_seconds)
    except rpdetect.ToolMissing as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    for m in missing:
        print(f"  [skip] Not found: {m}", file=sys.stderr)
    if not rows:
        print("ERROR: no video files found", file=sys.stderr)
        return 1
    bad = rpshoot.unknown_offsets(rows, offsets)
    if bad:
        print("ERROR: --offset names no camera in the table: " + ", ".join(bad)
              + " (cameras: " + ", ".join(sorted({r['camera'] for r in rows})) + ")", file=sys.stderr)
        return 1
    text = rpshoot.format_csv(rows)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Wrote {len(rows)} row(s) to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    print(rpshoot.summarize(rows), file=sys.stderr)
    return 2 if any(r["flags"] for r in rows) else 0


if __name__ == "__main__":
    cli.script(__name__)
