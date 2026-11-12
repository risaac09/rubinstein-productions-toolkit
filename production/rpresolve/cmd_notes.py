"""
notes: cut on the notes a song plays. `notes measure AUDIO --out notes.json`
finds the played onsets and the beat period; `notes snap notes.json --beats
4,2,2 --first T` puts each planned cut on the strongest note near its beat;
`notes check notes.json --cuts t1,t2 | --plan plan.json` counts the cuts that
land within one frame of a note, against chance. A measurement, not a verdict:
check always exits 0. The work is rpresolve/notes.py.
"""

import json
import sys

from rpresolve import cli
from rpresolve import notes

NAME = "notes"
HELP = "Offline: played-note onsets, snap planned cuts to them, check cuts against them"


def add_arguments(p):
    sub = p.add_subparsers(dest="action", required=True)
    m = sub.add_parser("measure", help="onsets and beat period of an audio file")
    m.add_argument("audio")
    m.add_argument("--out", required=True, help="notes.json, outside any git tree")
    m.add_argument("--min-bpm", type=float, default=notes.MIN_BPM)
    m.add_argument("--max-bpm", type=float, default=notes.MAX_BPM)
    m.add_argument("--lines", help="JSON list of [start, end] seconds, carried in as timestamps")
    s = sub.add_parser("snap", help="cut frames on the strongest played note near each beat")
    s.add_argument("notes")
    s.add_argument("--beats", required=True, help="beats per shot, e.g. 4,2,2")
    s.add_argument("--first", type=float, required=True, help="seconds of the first planned beat")
    s.add_argument("--fps", type=float, default=notes.FPS)
    s.add_argument("--window", type=float, default=notes.WINDOW)
    s.add_argument("--floor", type=float, default=notes.FLOOR)
    s.add_argument("--lead", type=float, default=notes.LEAD)
    c = sub.add_parser("check", help="cuts within one frame of a note, and the chance level")
    c.add_argument("notes")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--cuts", help="cut times in seconds after music-in, comma-separated")
    g.add_argument("--plan", help="a snap output (cut_times, music_in)")
    c.add_argument("--music-in", type=float, help="seconds; default 0, or the plan's")
    c.add_argument("--fps", type=float, default=notes.FPS)
    c.add_argument("--floor", type=float, default=notes.FLOOR, help="only notes at or above this strength count")


def run(args):
    from rpresolve import paths
    try:
        if getattr(args, "fps", 1) <= 0:
            raise notes.NotesError("--fps must be above 0")
        if args.action == "measure":
            problem = paths.out_problem(args.out, any_git_tree=True)
            if problem:
                raise paths.OutputRefused(problem)
            lines = notes.load(args.lines) if args.lines else None
            rep = notes.measure(args.audio, args.min_bpm, args.max_bpm, lines=lines)
            with open(args.out, "w") as f:
                json.dump(rep, f, indent=1)
            print(f"notes: {rep['bpm']:g} BPM, {len(rep['onsets'])} onset(s). Wrote {args.out}")
        elif args.action == "snap":
            out = notes.snap(notes.load(args.notes), [int(x) for x in args.beats.split(",")],
                             args.first, args.fps, args.window, args.floor, args.lead)
            print(json.dumps(out, indent=1))
        else:
            plan = notes.load(args.plan) if args.plan else {}
            cuts = [float(x) for x in args.cuts.split(",")] if args.cuts else plan["cut_times"]
            mi = args.music_in if args.music_in is not None else plan.get("music_in", 0.0)
            total, hit, chance, used = notes.check(notes.load(args.notes), cuts, mi, args.fps, args.floor)
            print(f"cuts {total}, on a note {hit}, chance {chance:.2f}, notes {used} at floor {args.floor:g}")
    except (notes.NotesError, KeyError, OSError, TypeError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 0 if args.action == "check" else 1  # check is a measurement: it never fails
    return 0


if __name__ == "__main__":
    cli.script(__name__)
