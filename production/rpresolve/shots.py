"""
rpresolve.shots: the shot list of a source file, from ffmpeg scdet. Offline:
ffprobe and ffmpeg, never Resolve.

qc and rhythm run scdet on a render; this runs the same filter, at qc's
threshold (qc.THRESHOLDS["scdet_threshold"], fixed unless adaptive, below) and through qc.parse_scdet, on
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
        [--backend scdet|pyscenedetect]

Backends for hard cuts: scdet (the default, as above) or pyscenedetect, the
PySceneDetect command line (`scenedetect`, adaptive detector, downscale 2, shots from 2 frames),
which the 2026-10-31 trial on a 26-cut render found better (24 of 26 matched,
none false, against scdet's 14 and 50). It is a separate program, never
imported: install it with `uv tool install 'scenedetect[opencv]==0.7.2'`, which
puts `scenedetect` on PATH. --threshold applies to scdet only. The
dissolve pass below runs after either backend; with pyscenedetect a cut wins
over a dissolve that holds it (with scdet the dissolve wins, as below). On
handheld footage the dissolve pass calls motion a dissolve: on the trial's
render it found 56 where there were none, so pass --no-dissolves there.

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

Dips and flashes come from the same grey frames (no second decode, so
--no-dissolves skips them too). A dip is a flat near-black run of 2 frames or
more, or a near-white run of 4 or more, with a picture on both sides; the
frames where luma falls into it and rises out are its span, and the shot after
it has into "dip" with blend_in and blend_out as that span. A dip replaces a
cut or dissolve found inside it. A flash is 1 to 3 frames far brighter than
both neighbours; the report lists their times under "flashes" (the cuts a
flash scores are left as scdet found them).

Dark, flat and black-and-white footage (adaptive=True, which only decon sets): scdet scores shrink
with contrast (a dim black-and-white clip scores its cuts 6 to 14, under 10) and PySceneDetect's
default weighs hue and saturation, which black and white lacks. An adaptive build samples the file's
frames first: the scdet threshold scales down in proportion to the median luma spread of the
frames that have a picture (near-black frames are left out, so a black head does not count), never
below 0.4 of the set value, and PySceneDetect runs luma-only on black and white. A cut in the first
two frames is dropped (an encoder start-up artefact the lower bar would count). The report says what
ran: threshold_used, contrast, low_contrast, black_and_white. The default build, qc, besttake and
any explicit --threshold keep the fixed threshold, so `qc --shots` agrees with the list.

What it does not do: wipes and fades to or from a colour that is not near
black or white, or a fade at the very start or end of the file; a dissolve over footage that
moves a lot, since the run is then no straight line; a dissolve in a clip with
no still stretch at all. Whip pans can score as cuts. The list
proposes; the editor's own shot list outranks it.
"""

import argparse
import csv
import io
import os
import shutil
import subprocess
import sys
import tempfile

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
MAX_DISSOLVE_S = 3600.0  # longest file the dissolve pass holds in memory (about 200 MB of grey frames at 25 fps, a few times over in find_dissolves)
MAX_STEP = 0.35     # no single frame step may be more than this share of the whole move
DARK_MAX, WHITE_MIN = 24.0, 232.0   # mean grey of a near-black or near-white frame (video-range black is 16, white 235)
FLAT_STD = 8.0      # a dip frame is near flat: its grey levels spread less than this
MIN_DARK, MIN_WHITE = 2, 4   # frames in a dip's flat run (a white run of 1 to 3 frames is a flash)
RAMP_FRAMES, RAMP_STEP = 6, 4.0   # a fall or rise is at most this many frames, each moving the mean more than RAMP_STEP
FLASH_RISE, MAX_FLASH = 60.0, 3   # a flash is 1 to MAX_FLASH frames this many levels above both neighbours
BACKENDS = ("scdet", "pyscenedetect")
# the 2026-10-31 trial's settings, with shots down to 2 frames (its default of 15 hides flash cuts)
PSD_ARGS = ("-d", "2", "-q", "-m", "2", "detect-adaptive")
# scdet scores and PySceneDetect's colour channels both shrink on dark, flat or black-and-white footage
REF_CONTRAST, MIN_SCALE = 25.0, 0.4   # luma spread (grey levels) at which the threshold applies as set; never below 0.4 of it
NO_PICTURE = 2.0                      # a frame whose luma spread is under this (black, a slate) says nothing about contrast
START_FRAMES = 2                      # an adaptive cut this close to the start is the encoder's first frame, not a shot
NO_CHROMA_READ = 99.0                 # chroma reported for a file look() could not sample: ordinary colour footage
GREY_CHROMA = 4.0                     # mean distance of U and V from neutral below which footage counts as black and white


class TooLong(dc.CheckError):
    """The file is over MAX_DISSOLVE_S, so the dissolve pass will not run."""


def scdet_filter(threshold=THRESHOLD):
    """The scdet filter exactly as qc and rhythm attach it."""
    return f"scdet=t={threshold}"


def scenedetect_exe():
    """The PySceneDetect command line, `scenedetect` on PATH. Raises dc.ToolMissing."""
    exe = shutil.which("scenedetect")
    if not exe:
        raise dc.ToolMissing("PySceneDetect is not on PATH: uv tool install 'scenedetect[opencv]==0.7.2' "
                             "(it lands in ~/.local/bin); or use --backend scdet.")
    return exe


def parse_scene_list(text):
    """Cut times in seconds (the start of every scene after the first) from `scenedetect list-scenes`
    CSV, which may open with a "Timecode List:" row before its header."""
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith("Scene Number")), None)
    if start is None:
        raise dc.CheckError("PySceneDetect wrote no scene list header.")
    rows = list(csv.DictReader(io.StringIO("\n".join(lines[start:]))))
    try:
        return [float(r["Start Time (seconds)"]) for r in rows[1:]]
    except (KeyError, TypeError, ValueError):  # another PySceneDetect version's columns
        raise dc.CheckError("PySceneDetect's scene list has no readable 'Start Time (seconds)' column; "
                            "this reads version 0.7.2's list-scenes CSV.")


def look(source):
    """(contrast, chroma) of a video file: median luma spread of its frames that have a picture, and
    mean colour distance from neutral, over 32x18 frames sampled at 2 a second (keyframes only read the dark clips 2x too bright). A file it cannot sample reads as ordinary
    footage (a half-decoded file does too: fail safe). Raises dc.CheckError on a timeout."""
    try:
        proc = subprocess.run([dc.FFMPEG, "-hide_banner", "-nostats", "-nostdin", "-v", "error", "-i", dc._src(source), "-map", "0:v:0",
                               "-vf", f"fps=2,scale={GREY_W}:{GREY_H}:flags=area,format=yuv444p",
                               "-f", "rawvideo", "-"], stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=dc.READ_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise dc.CheckError(f"ffmpeg timed out sampling {source}")
    size = GREY_W * GREY_H
    raw = np.frombuffer(proc.stdout, np.uint8)[:len(proc.stdout) // (3 * size) * 3 * size]
    if proc.returncode != 0 or not raw.size:
        return REF_CONTRAST, NO_CHROMA_READ
    f = raw.reshape(-1, 3, size).astype(np.float32)
    spread = f[:, 0].std(1)
    seen = spread >= NO_PICTURE
    return float(np.median(spread[seen])) if seen.any() else 0.0, float(np.abs(f[seen if seen.any() else slice(None), 1:] - 128).mean())


def pyscenedetect_cuts(source, luma_only=False):
    """Hard-cut times in seconds from PySceneDetect's adaptive detector. Raises dc.ToolMissing or
    dc.CheckError."""
    exe = scenedetect_exe()
    with tempfile.TemporaryDirectory() as tmp:
        try:
            proc = subprocess.run([exe, "-i", os.path.abspath(source), *PSD_ARGS, *(["-l"] if luma_only else []), "list-scenes", "-o", tmp,
                                   "-f", "scenes.csv"], stdin=subprocess.DEVNULL, capture_output=True,
                                  text=True, timeout=dc.READ_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise dc.CheckError(f"PySceneDetect timed out reading {source}")
        out = os.path.join(tmp, "scenes.csv")
        if proc.returncode != 0 or not os.path.isfile(out):
            raise dc.CheckError(f"PySceneDetect could not read {source}: {proc.stderr.strip()[-300:]}")
        with open(out, encoding="utf-8") as f:
            return parse_scene_list(f.read())


def grey_frames(source, total):
    """(n, GREY_H * GREY_W) float32 grey frames of a video file's first video stream. All of them
    are held in memory: 2.3 KB a frame, so an hour at 25 fps is about 200 MB, with a few more such
    copies while find_dissolves runs; a file above MAX_DISSOLVE_S is refused by build."""
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


def find_dips(frames, fps):
    """(dips, flashes) in grey frames (n, pixels). A dip is a flat near-black (or near-white) run with
    a picture on both sides, plus the frames over which the luma falls into it and rises out;
    start_frame is the last frame still on shot A, end_frame the first fully on shot B. A flash is
    1 to MAX_FLASH frames FLASH_RISE or more brighter than both neighbours: a list of times."""
    n = len(frames)
    m, sd = frames.mean(1), frames.std(1)
    flat = sd < FLAT_STD
    dips, flashes, i = [], [], 0
    while i < n:
        kind = "black" if flat[i] and m[i] <= DARK_MAX else "white" if flat[i] and m[i] >= WHITE_MIN else None
        j = i
        while kind and j + 1 < n and flat[j + 1] and (m[j + 1] <= DARK_MAX if kind == "black" else m[j + 1] >= WHITE_MIN):
            j += 1
        if kind and 0 < i and j < n - 1 and j - i + 1 >= (MIN_DARK if kind == "black" else MIN_WHITE):
            a, b = i, j
            while a > 0 and i - a < RAMP_FRAMES and abs(m[a - 1] - m[a]) > RAMP_STEP:
                a -= 1
            while b < n - 1 and b - j < RAMP_FRAMES and abs(m[b + 1] - m[b]) > RAMP_STEP:
                b += 1
            dips.append({"start_frame": a, "end_frame": b, "start": round(a / fps, 3), "end": round(b / fps, 3),
                         "duration": round((b - a) / fps, 3), "to": kind})
            i = b + 1
        else:
            i = j + 1
    for i in range(1, n - 1):  # a flash starts on a frame far above the one before it
        if m[i] - m[i - 1] < FLASH_RISE:
            continue
        for j in range(i, min(i + MAX_FLASH, n - 1)):
            if m[j] - m[j + 1] >= FLASH_RISE and m[i:j + 1].min() - max(m[i - 1], m[j + 1]) >= FLASH_RISE:
                if not any(d["start_frame"] <= i <= d["end_frame"] for d in dips):
                    flashes.append(round(i / fps, 3))
                break
    return dips, flashes


def build(source, threshold=None, dissolves=True, backend="scdet", adaptive=False):
    """{file, backend, threshold, fps, duration_s, cuts, dissolves, shots} for a video file;
    shots are {shot, in, out, duration, into, blend_in, blend_out}. threshold None is qc's; adaptive
    (decon's) scales that default to the footage's contrast and never an explicit threshold. Raises
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
    if dissolves and rate and total > MAX_DISSOLVE_S:
        raise TooLong(f"{source} runs {total / 3600:.1f} h; the dissolve pass holds every frame in memory "
                            f"and stops above {MAX_DISSOLVE_S / 3600:g} h. Pass --no-dissolves for cuts only, "
                            f"or split the file first.")
    if backend not in BACKENDS:
        raise ValueError(f"backend must be one of {', '.join(BACKENDS)}, not {backend!r}")
    adaptive = adaptive and threshold is None  # an explicit threshold is used as given
    threshold = THRESHOLD if threshold is None else threshold
    contrast, chroma = look(source) if adaptive else (None, NO_CHROMA_READ)
    scale = min(1.0, max(MIN_SCALE, contrast / REF_CONTRAST)) if adaptive else 1.0
    used = round(threshold * scale, 3)  # scdet scores track contrast: a fixed bar misses cuts in dark or flat footage
    grey_footage = adaptive and chroma < GREY_CHROMA  # PySceneDetect weighs hue and saturation, which black and white lacks
    if backend == "pyscenedetect":
        found_cuts = pyscenedetect_cuts(source, luma_only=grey_footage)
    else:
        proc = dc._run([dc.FFMPEG, "-hide_banner", "-nostats", "-nostdin", "-i", dc._src(source),
                        "-map", "0:v:0", "-vf", scdet_filter(used), "-f", "null", "-"],
                       dc.READ_TIMEOUT_S)
        if proc.returncode != 0:
            raise dc.CheckError(f"ffmpeg could not scan {source}: {proc.stderr.strip()[-300:]}")
        found_cuts = qc.parse_scdet(proc.stderr, used)
    start = START_FRAMES / float(rate) if adaptive and rate else 0.0
    cuts = sorted({t for t in found_cuts if start < t < total})
    found, dips, flashes = [], [], []
    if dissolves and rate:
        grey = grey_frames(source, total)  # one decode serves the dissolve and dip passes
        found = find_dissolves(grey, float(rate))
        dips, flashes = find_dips(grey, float(rate)) if grey is not None else ([], [])
        half = 0.5 / float(rate)
        # a dip's own fall and rise read as dissolves and its edges as cuts: the dip replaces both,
        # and takes the dissolve's span where that reaches further (a fall or rise over RAMP_FRAMES)
        for p in dips:
            for d in [d for d in found if d["start"] - half <= p["end"] and p["start"] - half <= d["end"]]:
                p["start_frame"], p["end_frame"] = min(p["start_frame"], d["start_frame"]), max(p["end_frame"], d["end_frame"])
            p.update(start=round(p["start_frame"] / float(rate), 3), end=round(p["end_frame"] / float(rate), 3),
                     duration=round((p["end_frame"] - p["start_frame"]) / float(rate), 3))
        found = [d for d in found if not any(d["start"] - half <= p["end"] and p["start"] - half <= d["end"] for p in dips)]
        cuts = [c for c in cuts if not any(p["start"] - half <= c <= p["end"] + half for p in dips)]
        if backend == "pyscenedetect":
            # its cuts are the confident ones: a "dissolve" holding one is handheld motion around a cut
            found = [d for d in found if not any(d["start"] - half <= c <= d["end"] + half for c in cuts)]
        else:
            # scdet can catch one frame of a quick dissolve: that is the dissolve, not a cut
            cuts = [c for c in cuts if not any(d["start"] - half <= c <= d["end"] + half for d in found)]
    bounds = sorted([(c, None) for c in cuts] + [((d["start"] + d["end"]) / 2, d) for d in found + dips],
                    key=lambda b: b[0])
    bounds = [b for b in bounds if 0 < b[0] < total]
    edges = [0.0] + [b[0] for b in bounds] + [total]
    shots = []
    for i, (a, b) in enumerate(zip(edges, edges[1:])):
        d = bounds[i - 1][1] if i else None
        shots.append({"shot": i + 1, "in": round(a, 3), "out": round(b, 3), "duration": round(b - a, 3),
                      "into": "start" if not i else ("dip" if "to" in d else "dissolve") if d else "cut",
                      "blend_in": d["start"] if d else "", "blend_out": d["end"] if d else ""})
    return {"file": os.path.abspath(source), "backend": backend, "threshold": threshold,
            "threshold_used": used, "contrast": None if contrast is None else round(contrast, 1),
            "black_and_white": grey_footage, "low_contrast": scale < 1.0, "fps": round(float(rate), 3) if rate else None, "duration_s": round(total, 3),
            "cuts": [round(c, 3) for c in cuts], "dissolves": found, "dips": dips, "flashes": flashes,
            "shots": shots}


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
    dips, flashes = report.get("dips", []), report.get("flashes", [])
    extra = ((f"; {len(dips)} dip(s) at " + ", ".join(f"{d['start']:.2f}s" for d in dips[:8])) if dips else "") + \
            ((f"; {len(flashes)} flash(es) at " + ", ".join(f"{t:.2f}s" for t in flashes[:8])) if flashes else "")
    return (f"shots: {len(report['shots'])} shot(s), {len(cuts)} cut(s), {len(diss)} dissolve(s) in "
            f"{report['duration_s']:g} s "
            + ("by PySceneDetect (adaptive)" if report.get("backend") == "pyscenedetect"
               else f"at scdet t={report['threshold']:g}")
            + (f" (cuts at {shown})" if cuts else "") + (f" (dissolves at {blends})" if diss else "") + extra
            + ". A proposal: check the cuts by eye.")


def add_arguments(p):
    p.add_argument("source", help="a video file: camera original, screen recording or edit")
    p.add_argument("--out", required=True,
                   help="shot list, .csv or .tsv (rhythm reads .tsv), outside any git tree")
    p.add_argument("--threshold", type=float, default=None,
                   help=f"scdet score that makes a cut (default {THRESHOLD:g}, as qc; always used as given)")
    p.add_argument("--no-dissolves", action="store_true", help="skip the dissolve pass (cuts only, as before)")
    p.add_argument("--backend", choices=BACKENDS, default="scdet",
                   help="hard-cut detector: scdet (default) or pyscenedetect (the scenedetect program, adaptive)")
    return p


def run(args):
    try:
        problem = paths.out_problem(args.out, any_git_tree=True)
        if problem:
            raise paths.OutputRefused(problem)
        if os.path.realpath(args.out) == os.path.realpath(args.source):
            raise paths.OutputRefused(f"{args.out} is the source; the shot list never replaces it")
        report = build(args.source, args.threshold, not args.no_dissolves, args.backend)
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
