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
    p.add_argument("--shots", help="the shot list `shots` writes (.csv or .tsv), or a shots report (.json): adds the cut-to-beat table")
    p.add_argument("--prior-bpm", type=float, default=120.0, help="centre of the tempo prior (default 120)")


def run(args):
    import json
    import sys
    from rpresolve import paths
    try:
        from rpresolve import beats, cutlist, decon, musicmap
    except ModuleNotFoundError as e:
        print(f"ERROR: music-map needs {e.name}, which this Python does not have", file=sys.stderr)
        return 1
    try:
        problem = paths.out_problem(args.out, any_git_tree=True)
        if problem:
            raise paths.OutputRefused(problem)
        decon.check_outputs([args.video, args.shots], [args.out])
        mm = musicmap.build(args.video, args.prior_bpm)
        table = None
        if args.shots:
            table = musicmap.cut_table(_load_shots(args.shots, args.video), mm)
            mm["cuts"] = table
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(mm, f, indent=1)
    except (musicmap.MusicMapError, beats.BeatsError, cutlist.CutlistError, OSError, ValueError, KeyError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(musicmap.summary(mm, table))
    print(f"Wrote {args.out}", file=sys.stderr)
    return 0


def _load_shots(path, video):
    """A shots report: the .csv or .tsv `shots` writes (its frame rate read from the video), or the JSON form."""
    import csv
    import json
    from rpresolve import delivercheck as dc
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if text.lstrip().startswith("{"):
        report = json.loads(text)
    else:
        rows = list(csv.DictReader(text.splitlines(), delimiter="\t" if path.lower().endswith(".tsv") else ","))
        if not rows or not {"shot", "in"} <= set(rows[0]):
            raise ValueError(f"{path} is not a shot list from `shots` (.csv, .tsv) or a shots report (.json)")
        v = next((s for s in dc.probe(video).get("streams") or [] if s.get("codec_type") == "video"), {})
        fps = dc.rate(v.get("avg_frame_rate"))
        report = {"fps": float(fps) if fps else None,
                  "shots": [{"shot": int(r["shot"]), "in": float(r["in"])} for r in rows]}
    if not report.get("fps"):
        raise ValueError(f"{path}: no frame rate, so cut offsets cannot be given in frames")
    return report


if __name__ == "__main__":
    cli.script(__name__)
