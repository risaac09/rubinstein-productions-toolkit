"""
music-map: a music map of a finished video, from its own audio: the beat grid (beats_s, bpm,
as rpresolve/beats.py makes it, so `decon recipe --beats` reads it) and sections of low, mid
or high energy. With --shots (a `shots` report) it adds a cut-to-beat table: each cut's
nearest beat, the offset in frames, and the section it falls in. A new command, not an
extension of `beats`: that one takes a music file and stays a plain beat map; this one adds
what a cut needs. The work is rpresolve/musicmap.py.
"""

from rpresolve import cli

NAME = "music-map"
HELP = "Offline: beats and energy sections of a video's audio, and where its cuts fall"


def add_arguments(p):
    p.add_argument("video", help="a video with audio (any format ffmpeg reads)")
    p.add_argument("--out", required=True, help="music-map .json, outside any git tree")
    p.add_argument("--shots", help="a shots report (.json): adds the cut-to-beat table")
    p.add_argument("--prior-bpm", type=float, default=120.0, help="centre of the tempo prior (default 120)")


def run(args):
    import json
    import sys
    from rpresolve import paths
    try:
        from rpresolve import beats, cutlist, musicmap
    except ModuleNotFoundError as e:
        print(f"ERROR: music-map needs {e.name}, which this Python does not have", file=sys.stderr)
        return 1
    try:
        problem = paths.out_problem(args.out, any_git_tree=True)
        if problem:
            raise paths.OutputRefused(problem)
        mm = musicmap.build(args.video, args.prior_bpm)
        table = None
        if args.shots:
            with open(args.shots, encoding="utf-8") as f:
                table = musicmap.cut_table(json.load(f), mm)
            mm["cuts"] = table
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(mm, f, indent=1)
    except (musicmap.MusicMapError, beats.BeatsError, cutlist.CutlistError, OSError, ValueError, KeyError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(musicmap.summary(mm, table))
    print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    cli.script(__name__)
