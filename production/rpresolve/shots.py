"""
rpresolve.shots: the shot list of a source file, from ffmpeg scdet. Offline:
ffprobe and ffmpeg, never Resolve.

qc and rhythm run scdet on a render; this runs the same filter, at qc's
threshold (qc.THRESHOLDS["scdet_threshold"]) and through qc.parse_scdet, on
the footage itself: a camera file with in-camera cuts, a screen recording, a
supplied edit. Every frame scoring the threshold or more starts a new shot.

Output: one row per shot, a header first:
    shot,in,out,duration
in and out are seconds in the source (the shot holds [in, out)). A path
ending .tsv is written tab-separated, which is the paper-edit shape rhythm
reads (in and out columns); any other path comma-separated. Either way
`qc --shots` counts its rows (qc.shot_count), so a render of this footage,
uncut, should show one cut fewer than the list has shots.

Run from production/:
    python3 -m rpresolve.shots SOURCE --out SHOTS.csv|SHOTS.tsv [--threshold 10]

What it does not do: dissolves, fades and wipes (scdet scores frame to frame,
so a slow transition seldom crosses the threshold), or flashes and
whip pans, which can score as cuts. A hard cut between two shots of the
same scene in the same light can score under the threshold. The list
proposes; the editor's own shot list outranks it.
"""

import argparse
import os
import sys

from . import delivercheck as dc, paths, qc

THRESHOLD = qc.THRESHOLDS["scdet_threshold"]
COLUMNS = ("shot", "in", "out", "duration")


def scdet_filter(threshold=THRESHOLD):
    """The scdet filter exactly as qc and rhythm attach it."""
    return f"scdet=t={threshold}"


def build(source, threshold=THRESHOLD):
    """{file, threshold, fps, duration_s, cuts, shots} for a video file;
    shots are {shot, in, out, duration}. Raises dc.ToolMissing or
    dc.CheckError."""
    dc.check_tools(ffmpeg=True)
    if not os.path.isfile(source):
        raise dc.CheckError(f"{source} is not a file.")
    info = dc.probe(source)
    video = next((s for s in info.get("streams") or [] if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    if video is None:
        raise dc.CheckError(f"{source} has no video stream.")
    total = dc.duration(info)
    if total is None:
        raise dc.CheckError(f"{source} has no readable duration.")
    rate = dc.rate(video.get("avg_frame_rate"))
    proc = dc._run([dc.FFMPEG, "-hide_banner", "-nostats", "-nostdin", "-i", dc._src(source),
                    "-map", "0:v:0", "-vf", scdet_filter(threshold), "-f", "null", "-"],
                   dc.READ_TIMEOUT_S)
    if proc.returncode != 0:
        raise dc.CheckError(f"ffmpeg could not scan {source}: {proc.stderr.strip()[-300:]}")
    cuts = sorted({t for t in qc.parse_scdet(proc.stderr, threshold) if 0 < t < total})
    edges = [0.0] + cuts + [total]
    shots = [{"shot": i + 1, "in": round(a, 3), "out": round(b, 3), "duration": round(b - a, 3)}
             for i, (a, b) in enumerate(zip(edges, edges[1:]))]
    return {"file": os.path.abspath(source), "threshold": threshold,
            "fps": round(float(rate), 3) if rate else None, "duration_s": round(total, 3),
            "cuts": [round(c, 3) for c in cuts], "shots": shots}


def table(report, sep=","):
    """The shot list as text: COLUMNS, then one row per shot."""
    rows = [sep.join(COLUMNS)]
    rows += [sep.join(str(s[k]) for k in COLUMNS) for s in report["shots"]]
    return "\n".join(rows) + "\n"


def write(report, path):
    """Write the shot list (tab-separated for .tsv, else comma-separated);
    refused inside any git working tree."""
    problem = paths.out_problem(path, any_git_tree=True)
    if problem:
        raise paths.OutputRefused(problem)
    from resolve_survey import write_report  # atomic: temp file, then replace
    write_report(path, table(report, "\t" if path.lower().endswith(".tsv") else ","))
    return path


def summary(report):
    cuts = report["cuts"]
    shown = ", ".join(f"{c:.2f}s" for c in cuts[:8]) + (f" and {len(cuts) - 8} more"
                                                        if len(cuts) > 8 else "")
    return (f"shots: {len(report['shots'])} shot(s), {len(cuts)} cut(s) in "
            f"{report['duration_s']:g} s at scdet t={report['threshold']:g}"
            + (f" (cuts at {shown})" if cuts else "") + ". A proposal: check the cuts by eye.")


def add_arguments(p):
    p.add_argument("source", help="a video file: camera original, screen recording or edit")
    p.add_argument("--out", required=True,
                   help="shot list, .csv or .tsv (rhythm reads .tsv), outside any git tree")
    p.add_argument("--threshold", type=float, default=THRESHOLD,
                   help=f"scdet score that makes a cut (default {THRESHOLD:g}, as qc)")
    return p


def run(args):
    try:
        problem = paths.out_problem(args.out, any_git_tree=True)
        if problem:
            raise paths.OutputRefused(problem)
        report = build(args.source, args.threshold)
        write(report, args.out)
    except (dc.ToolMissing, dc.CheckError, OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(summary(report))
    print(f"Wrote {args.out}", file=sys.stderr)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m rpresolve.shots",
                                 description="The shot list of a source file, from ffmpeg scdet.")
    return run(add_arguments(ap).parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
