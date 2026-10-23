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

Dissolves: scdet scores frame to frame, so a slow cross-dissolve seldom
crosses its threshold. A second pass reads the clip as 32x18 grey frames and
looks for a run where the picture moves in a straight line from one shot to
the other (each pixel steadily toward its new value, no one big step): the
path walked frame by frame is about as long as the distance from the first
frame to the last. Such a run, MIN_FRAMES or more long, is a dissolve, with
its first and last frame (the frames where it leaves shot A and settles on
shot B). The shot boundary sits at its middle; an scdet cut inside one is the
dissolve, not a second cut. The shot list gains three columns after the four
above: into (start, cut or dissolve), blend_in and blend_out (seconds, dissolves
only). --no-dissolves skips the pass.

What it does not do: wipes, dip-to-black and fades to or from a flat colour
(the blend must have pixels moving both ways); a dissolve over footage that
moves a lot, since the run is then no straight line; a dissolve in a clip with
no still stretch at all. Flashes and whip pans can score as cuts. The list
proposes; the editor's own shot list outranks it.
"""

import argparse
import os
import subprocess
import sys

import numpy as np

from . import delivercheck as dc, paths, qc

THRESHOLD = qc.THRESHOLDS["scdet_threshold"]
COLUMNS = ("shot", "in", "out", "duration", "into", "blend_in", "blend_out")
GREY_W, GREY_H = 32, 18
MIN_FRAMES = 5      # a dissolve is at least this many frames, first to last
MAX_SECONDS = 3.0   # and at most this long
MIN_MOVE = 8.0      # mean grey levels (of 255) the picture must travel, first frame to last
STRAIGHT = 0.9      # distance travelled over distance walked, frame by frame
MAX_OFF = 0.25      # a window frame may sit this far off the blend line, as a share of the move
MAX_NOISE = 1.0     # grey levels a frame step can move on noise alone
MAX_STEP = 0.35     # no single frame step may be more than this share of the whole move


def scdet_filter(threshold=THRESHOLD):
    """The scdet filter exactly as qc and rhythm attach it."""
    return f"scdet=t={threshold}"


def grey_frames(source, total):
    """(n, GREY_H * GREY_W) float32 grey frames of a video file's first video stream. All of them
    are held in memory: 2.3 KB a frame, so an hour at 25 fps is about 200 MB, with a few more such
    copies while find_dissolves runs; split a longer file first."""
    try:
        proc = subprocess.run([dc.FFMPEG, "-hide_banner", "-nostats", "-nostdin", "-v", "error", "-i", dc._src(source),
                               "-map", "0:v:0", "-vf", f"scale={GREY_W}:{GREY_H}:flags=area,format=gray",
                               "-f", "rawvideo", "-"], stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=dc.READ_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise dc.CheckError(f"ffmpeg timed out reading {source}")
    if proc.returncode != 0:
        raise dc.CheckError(f"ffmpeg could not read {source}: {proc.stderr.decode(errors='replace').strip()[-300:]}")
    size = GREY_W * GREY_H
    return np.frombuffer(proc.stdout, np.uint8)[:len(proc.stdout) // size * size].reshape(-1, size).astype(np.float32)


def _off_the_line(win):
    """Largest mean grey-level gap, as a share of the first-to-last distance, between a frame of the
    window and the blend of its first and last frames that matches it best. A dissolve sits on the
    line; moving content does not."""
    d = win[-1] - win[0]
    a = ((win - win[0]) * d).sum(1) / max(float((d * d).sum()), 1e-9)
    gap = np.abs(win - win[0] - a[:, None] * d).mean(1)
    return float(gap.max() / max(np.abs(d).mean(), 1e-9))


def find_dissolves(frames, fps):
    """[{start_frame, end_frame, start, end, duration}] for the dissolves in grey frames (n, pixels).
    start_frame is the last frame still on shot A, end_frame the first on shot B."""
    n = len(frames)
    step = np.abs(np.diff(frames, axis=0)).mean(1)  # step[i]: frame i to i+1
    floor = min(1.5 * float(np.percentile(step, 10)), MAX_NOISE) if n > 2 else 0.0  # noise, not progress
    walked = np.concatenate([[0.0], np.cumsum(np.maximum(step - floor, 0))])
    hit = np.zeros(max(n - 1, 0), bool)  # hit[i]: the step from frame i to i+1 belongs to a dissolve
    for length in range(MIN_FRAMES - 1, min(n, int(MAX_SECONDS * fps) + 1)):
        s = np.arange(n - length)
        dist = np.abs(frames[s + length] - frames[s]).mean(1)
        path = walked[s + length] - walked[s]
        # the biggest step inside each window, from a sliding max
        big = np.max(np.lib.stride_tricks.sliding_window_view(step, length), axis=1)[:len(s)]
        ok = (dist >= MIN_MOVE) & (path > 0) & (dist >= STRAIGHT * path) & (big <= MAX_STEP * dist)
        for i in s[ok]:
            if _off_the_line(frames[i:i + length + 1]) > MAX_OFF:
                continue
            # only the steps that move: a still stretch inside the window is not part of it
            hit[i:i + length] |= step[i:i + length] > floor + 0.25 * (big[i] - floor)
    out, i = [], 0
    while i < len(hit):
        if not hit[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(hit) and hit[j + 1]:
            j += 1
        j += 1  # the run of steps i..j-1 spans frames i..j
        a, b = i, j
        if b - a + 1 >= MIN_FRAMES:
            out.append({"start_frame": a, "end_frame": b, "start": round(a / fps, 3), "end": round(b / fps, 3),
                        "duration": round((b - a) / fps, 3)})
        i = j + 1
    return out


def build(source, threshold=THRESHOLD, dissolves=True):
    """{file, threshold, fps, duration_s, cuts, dissolves, shots} for a video file;
    shots are {shot, in, out, duration, into, blend_in, blend_out}. Raises
    dc.ToolMissing or dc.CheckError."""
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
    found = []
    if dissolves and rate:
        found = find_dissolves(grey_frames(source, total), float(rate))
        # scdet can catch one frame of a quick dissolve: that is the dissolve, not a cut
        cuts = [c for c in cuts if not any(d["start"] - 0.5 / float(rate) <= c <= d["end"] + 0.5 / float(rate)
                                           for d in found)]
    bounds = sorted([(c, None) for c in cuts] + [((d["start"] + d["end"]) / 2, d) for d in found],
                    key=lambda b: b[0])
    bounds = [b for b in bounds if 0 < b[0] < total]
    edges = [0.0] + [b[0] for b in bounds] + [total]
    shots = []
    for i, (a, b) in enumerate(zip(edges, edges[1:])):
        d = bounds[i - 1][1] if i else None
        shots.append({"shot": i + 1, "in": round(a, 3), "out": round(b, 3), "duration": round(b - a, 3),
                      "into": "start" if not i else "dissolve" if d else "cut",
                      "blend_in": d["start"] if d else "", "blend_out": d["end"] if d else ""})
    return {"file": os.path.abspath(source), "threshold": threshold,
            "fps": round(float(rate), 3) if rate else None, "duration_s": round(total, 3),
            "cuts": [round(c, 3) for c in cuts], "dissolves": found, "shots": shots}


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
    cuts, diss = report["cuts"], report.get("dissolves", [])
    shown = ", ".join(f"{c:.2f}s" for c in cuts[:8]) + (f" and {len(cuts) - 8} more"
                                                        if len(cuts) > 8 else "")
    blends = ", ".join(f"frames {d['start_frame']}-{d['end_frame']} ({d['start']:.2f}-{d['end']:.2f}s)" for d in diss[:8])
    return (f"shots: {len(report['shots'])} shot(s), {len(cuts)} cut(s), {len(diss)} dissolve(s) in "
            f"{report['duration_s']:g} s at scdet t={report['threshold']:g}"
            + (f" (cuts at {shown})" if cuts else "") + (f" (dissolves at {blends})" if diss else "")
            + ". A proposal: check the cuts by eye.")


def add_arguments(p):
    p.add_argument("source", help="a video file: camera original, screen recording or edit")
    p.add_argument("--out", required=True,
                   help="shot list, .csv or .tsv (rhythm reads .tsv), outside any git tree")
    p.add_argument("--threshold", type=float, default=THRESHOLD,
                   help=f"scdet score that makes a cut (default {THRESHOLD:g}, as qc)")
    p.add_argument("--no-dissolves", action="store_true", help="skip the dissolve pass (cuts only, as before)")
    return p


def run(args):
    try:
        problem = paths.out_problem(args.out, any_git_tree=True)
        if problem:
            raise paths.OutputRefused(problem)
        if os.path.realpath(args.out) == os.path.realpath(args.source):
            raise paths.OutputRefused(f"{args.out} is the source; the shot list never replaces it")
        report = build(args.source, args.threshold, not args.no_dissolves)
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
