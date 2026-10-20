"""
rpresolve.rhythm: how a cut moves in time, as numbers that ask questions.
Offline: it reads a cut list, or a render's picture and sound, and judges
nothing. A beat the editor marked by hand outranks every line it prints.

Run from production/:
    python3 -m rpresolve.rhythm CUT [--fps R] [--beats MAP.json]
        [--profile document|short-form] [--hook S] [--window S] [--out FILE]

CUT is one of:
    .edl    CMX 3600: the record in and out of every video event (needs --fps)
    .json   timeline_items output: the items on video track 1, start and end
            in timeline frames (fps from its timeline block, else --fps)
    .tsv    a paper edit (clip_id, in, out in seconds), laid end to end
    other   a render: cuts from ffmpeg scdet at qc's threshold, and the
            loudness line from its audio

What it reports, and why:
    - shots, total, average shot length with the median, median over
      average and the coefficient of variation: the average alone hides the
      spread (Salt, on Cinemetrics: across 1,520 sound features median over
      average sits near 0.62 and the coefficient near 1.1)
    - shots under 8 frames, and a length histogram
    - cuts per window across the cut, as a sparkline: where it speeds up
    - whether shots get longer or shorter through the cut: the rank
      correlation of length with position, against shuffled orders of the
      same shots (random orders show trends too, so a trend counts only when
      fewer than one shuffle in twenty matches it), with the rolling median
      to say where
    - with a beat map (the sound lane's JSON, `beats_s`): the share of cuts
      within 1 and 2 frames of a beat, against the share chance would give
    - for a render, the loudness line the story packet prints
Then questions under the profile, `document` or `short-form`.
"""

import argparse
import csv
import json
import math
import os
import random
import re
import statistics
import sys

from . import cutlist, delivercheck as dc, paths, qc, storypacket, trimreview

ROLL = 9            # shots in the rolling median window (8 to 12 is the usual range)
SHUFFLES = 200
SEED = 7
FLASH_FRAMES = 8
WINDOW_S = 10.0     # seconds per cutting-rate bin
HOOK_S = 2.0
EVEN_CV, SKEWED_CV = 0.6, 2.0
SHORT_MEDIAN_S = 2.0  # a median under this reads as short-form tempo
BUCKETS = (1, 2, 4, 8, 16)  # histogram edges in seconds
TC = r"(\d\d):(\d\d):(\d\d)[:;.](\d\d)"
EDL_EVENT = re.compile(r"^\s*\d+\s+\S+\s+(\S+)\s+\S+(?:\s+\d+)?\s+" + r"\s+".join([TC] * 4))


class RhythmError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Cut lists: every reader returns (shot durations in seconds, fps or None)
# ---------------------------------------------------------------------------

def _tc_frames(groups, fps):
    """HH:MM:SS:FF as a frame count at the timecode's nominal rate (drop
    frame is read as non-drop: a cut length is off by at most a frame or
    two a minute)."""
    h, m, s, f = (int(g) for g in groups)
    return ((h * 60 + m) * 60 + s) * round(fps) + f


def from_edl(path, fps):
    """Durations of the video events of a CMX 3600 EDL, in record order.
    Audio-only events are skipped; two events at one record in (a
    dissolve's outgoing and incoming lines) count once, the later line."""
    if not fps:
        raise RhythmError("an EDL needs --fps")
    events = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = EDL_EVENT.match(line)
            if not m or not ("V" in m.group(1) or m.group(1) == "B"):
                continue
            rec_in, rec_out = _tc_frames(m.groups()[9:13], fps), _tc_frames(m.groups()[13:17], fps)
            if rec_out > rec_in:
                events[rec_in] = rec_out
    if not events:
        raise RhythmError(f"{path} has no video events")
    return [(events[k] - k) / fps for k in sorted(events)], fps


def from_items(path, fps=None):
    """Durations of the items on video track 1 of a timeline_items result
    (the tool's JSON, or its rows alone), in timeline order."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    rows = data.get("rows", []) if isinstance(data, dict) else data
    tl = data.get("timeline", {}) if isinstance(data, dict) else {}
    try:
        fps = float(tl.get("fps") or fps or 0)
    except (TypeError, ValueError):
        fps = float(fps or 0)
    if not fps:
        raise RhythmError(f"{path} names no frame rate; pass --fps")
    items = sorted((r for r in rows if r.get("track", 1) == 1 and r.get("end") is not None),
                   key=lambda r: r["start"])
    if not items:
        raise RhythmError(f"{path} has no items on video track 1")
    return [(r["end"] - r["start"]) / fps for r in items], fps


def from_tsv(path):
    """Durations of a paper edit's rows (in and out in seconds), in row order."""
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    if not rows or not {"in", "out"} <= set(rows[0]):
        raise RhythmError(f"{path} is not a paper edit: it needs in and out columns")
    try:
        durs = [float(r["out"]) - float(r["in"]) for r in rows]
    except ValueError as e:
        raise RhythmError(f"{path}: {e}")
    if any(d <= 0 for d in durs):
        raise RhythmError(f"{path} has a row whose out is not after its in")
    return durs, None


def from_render(path, threshold=None):
    """Durations between ffmpeg scdet cuts in a render, and its frame rate."""
    info = dc.probe(path)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise RhythmError(f"{path} has no video stream")
    num, _, den = (video.get("avg_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    total = float(info.get("format", {}).get("duration") or video.get("duration") or 0)
    th = qc.THRESHOLDS["scdet_threshold"] if threshold is None else threshold
    proc = dc._run([dc.FFMPEG, "-hide_banner", "-nostats", "-nostdin", "-i", dc._src(path),
                    "-map", "0:v:0", "-vf", f"scdet=t={th}", "-f", "null", "-"], dc.READ_TIMEOUT_S)
    if proc.returncode != 0:
        raise RhythmError(f"ffmpeg could not scan {path}: {proc.stderr.strip()[-200:]}")
    edges = [0.0] + [t for t in qc.parse_scdet(proc.stderr, th) if 0 < t < total] + [total]
    return [b - a for a, b in zip(edges, edges[1:]) if b > a], fps or None


def read_cut(path, fps=None):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".edl":
        return from_edl(path, fps)
    if ext == ".json":
        return from_items(path, fps)
    if ext == ".tsv":
        return from_tsv(path)[0], fps
    durs, rate = from_render(path)
    return durs, fps or rate


# ---------------------------------------------------------------------------
# Measures
# ---------------------------------------------------------------------------

def starts(durs):
    out, t = [], 0.0
    for d in durs:
        out.append(t)
        t += d
    return out


def stats(durs, fps=None):
    mean = statistics.fmean(durs)
    med = statistics.median(durs)
    flash = [i for i, d in enumerate(durs) if fps and d * fps < FLASH_FRAMES - 1e-6]
    return {"shots": len(durs), "total": sum(durs), "asl": mean, "median": med,
            "ratio": med / mean, "cv": statistics.pstdev(durs) / mean,
            "shortest": min(durs), "longest": max(durs), "flash": flash}


def histogram(durs):
    """[(label, count)] over BUCKETS."""
    edges = (0,) + BUCKETS + (math.inf,)
    out = []
    for a, b in zip(edges, edges[1:]):
        label = f"{a}-{b} s" if b != math.inf else f"{a}+ s"
        out.append((label, sum(1 for d in durs if a <= d < b)))
    return out


def cut_rate(durs, window=WINDOW_S):
    """Cuts per window across the cut (a cut is every shot start but the first)."""
    total = sum(durs)
    counts = [0] * max(1, math.ceil(total / window))
    for t in starts(durs)[1:]:
        counts[min(len(counts) - 1, int(t // window))] += 1
    return counts


def spark(values):
    """A sparkline scaled from zero to the largest value."""
    top = max(values) or 1
    return "".join(storypacket.LEVELS[min(7, int(v / top * 7.999))] if v else "·" for v in values)


def rolling_median(durs, k=ROLL):
    return [statistics.median(durs[i:i + k]) for i in range(len(durs) - k + 1)]


def ranks(values):
    """Ranks from 1, ties sharing their mean rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out, i = [0.0] * len(values), 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in order[i:j + 1]:
            out[k] = (i + j) / 2 + 1
        i = j + 1
    return out


def spearman(values):
    """Rank correlation of values against their position: below zero, the
    shots get shorter as the cut goes on."""
    x, y = list(range(1, len(values) + 1)), ranks(values)
    mx, my = statistics.fmean(x), statistics.fmean(y)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    return sxy / (sx * sy) if sx and sy else 0.0


def trend(durs, k=ROLL, shuffles=SHUFFLES, seed=SEED):
    """Whether shots get longer or shorter through the cut: the rank
    correlation of length with position, the share of shuffled orders of
    the same shots that correlate as strongly either way, and where the
    rolling median is lowest and highest. None with too few shots."""
    if len(durs) < 2 * k:
        return None
    rho = spearman(durs)
    r, more, pool = random.Random(seed), 0, list(durs)
    for _ in range(shuffles):
        r.shuffle(pool)
        more += abs(spearman(pool)) >= abs(rho) - 1e-9
    roll, st = rolling_median(durs, k), starts(durs)
    lo, hi = roll.index(min(roll)), roll.index(max(roll))
    return {"rho": rho, "share": more / shuffles, "min": (min(roll), st[lo]),
            "max": (max(roll), st[hi])}


def load_beats(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    beats = data.get("beats_s") if isinstance(data, dict) else data
    if not beats:
        raise RhythmError(f"{path} has no beats_s")
    return sorted(float(b) for b in beats)


def on_beat(durs, beats, fps, tolerances=(1, 2)):
    """For each tolerance in frames: the share of cuts within it of a beat,
    and the share chance would give (the part of the cut's length that lies
    within the tolerance of some beat)."""
    cuts, total = starts(durs)[1:], sum(durs)
    out = {}
    for tol in tolerances:
        w = tol / fps
        hit = sum(1 for c in cuts if any(abs(c - b) <= w for b in beats))
        covered, end = 0.0, 0.0
        for b in beats:
            a, z = max(0.0, b - w, end), min(total, b + w)
            covered += max(0.0, z - a)
            end = max(end, z)
        out[tol] = (hit / len(cuts) if cuts else 0.0, covered / total if total else 0.0)
    return out


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def questions(s, durs, tr, beats, profile, hook, fps):
    st, q = starts(durs), []
    if s["flash"]:
        at = ", ".join(storypacket.clock(st[i]) for i in s["flash"][:5])
        q.append(f"{len(s['flash'])} shot(s) run under {FLASH_FRAMES} frames (at {at}"
                 f"{', ...' if len(s['flash']) > 5 else ''}). Flash frames, or meant?")
    if s["cv"] < EVEN_CV:
        q.append(f"Shot lengths are unusually even (CV {s['cv']:.2f}; sound features average "
                 "about 1.1). Is that evenness the rhythm you want?")
    elif s["cv"] > SKEWED_CV:
        q.append(f"A few long shots dominate (CV {s['cv']:.2f}). Do they hold attention, or stall?")
    if tr and tr["share"] < 0.05:
        way = "shorter" if tr["rho"] < 0 else "longer"
        q.append(f"Shots get {way} as the cut goes on (rank correlation {tr['rho']:+.2f}; "
                 f"{tr['share']:.0%} of shuffled orders go as far). The rolling median runs from "
                 f"{tr['min'][0]:.1f} s near {storypacket.clock(tr['min'][1])} to "
                 f"{tr['max'][0]:.1f} s near {storypacket.clock(tr['max'][1])}. Meant?")
    if beats:
        hit, chance = beats[2]
        if hit <= chance * 1.5:
            q.append(f"Cuts land within 2 frames of a beat no more often than chance "
                     f"({hit:.0%} against {chance:.0%}). Should the music-led sections land on it?")
    if profile == "short-form" and durs[0] > hook:
        q.append(f"The first shot runs {durs[0]:.1f} s, past the {hook:g} s hook window. Does "
                 "something change on screen inside it?")
    if profile == "document" and s["median"] < SHORT_MEDIAN_S:
        q.append(f"The median shot is {s['median']:.1f} s, a short-form tempo in a document "
                 "profile. Meant?")
    return q


def report(cut, fps=None, beats_path=None, profile="document", hook=HOOK_S, window=WINDOW_S,
           ffmpeg=None):
    """The rhythm report as Markdown."""
    durs, fps = read_cut(cut, fps)
    s = stats(durs, fps)
    tr = trend(durs)
    beats = on_beat(durs, load_beats(beats_path), fps) if beats_path and fps else None
    lines = [f"# Rhythm: {os.path.basename(cut)}", "",
             "These numbers describe the cut and judge nothing. A beat the editor marked by hand "
             "outranks every line here.", "",
             f"- shots {s['shots']}, total {storypacket.clock(s['total'])}"
             + (f" at {fps:g} fps" if fps else ""),
             f"- average shot {s['asl']:.2f} s, median {s['median']:.2f} s, median over average "
             f"{s['ratio']:.2f}, CV {s['cv']:.2f} (sound features: about 0.62 and 1.1)",
             f"- shortest {s['shortest']:.2f} s, longest {s['longest']:.2f} s",
             "- lengths: " + ", ".join(f"{label} {n}" for label, n in histogram(durs)),
             f"- cuts per {window:g} s: `{spark(cut_rate(durs, window))}`"]
    if tr:
        lines.append(f"- rolling median over {ROLL} shots: {tr['min'][0]:.1f} to "
                     f"{tr['max'][0]:.1f} s; length against position: rank correlation "
                     f"{tr['rho']:+.2f}, matched by {tr['share']:.0%} of {SHUFFLES} shuffled orders")
    else:
        lines.append(f"- rolling median: too few shots (needs {2 * ROLL})")
    if beats:
        lines.append("- cuts on a beat: " + "; ".join(
            f"within {t} frame{'s' if t > 1 else ''} {h:.0%} (chance {c:.0%})"
            for t, (h, c) in beats.items()))
    elif beats_path:
        lines.append("- cuts on a beat: not measured (no frame rate; pass --fps)")
    if os.path.splitext(cut)[1].lower() not in (".edl", ".json", ".tsv"):
        try:
            env = trimreview.envelope(cut, 0.0, None, ffmpeg)
        except cutlist.CutlistError:
            env = None
            lines.append("- energy: no audio read")
        if env:
            width, levels = storypacket.bins(env, s["total"])
            lines.append("- " + storypacket.energy_line(levels, width).replace("  \n", "\n  "))
    q = questions(s, durs, tr, beats, profile, hook, fps)
    lines += ["", f"## Questions ({profile})", ""] + ([f"- {x}" for x in q] or ["- none"])
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m rpresolve.rhythm",
                                 description="How a cut moves in time, as numbers that ask "
                                             "questions. Offline; judges nothing.")
    ap.add_argument("cut", help="An EDL, a timeline_items JSON, a paper-edit TSV, or a render")
    ap.add_argument("--fps", type=float, help="Frame rate (needed for an EDL)")
    ap.add_argument("--beats", help="A beat map JSON with beats_s (the sound lane's music map)")
    ap.add_argument("--profile", choices=("document", "short-form"), default="document")
    ap.add_argument("--hook", type=float, default=HOOK_S, help="Hook window, seconds")
    ap.add_argument("--window", type=float, default=WINDOW_S, help="Seconds per cutting-rate bin")
    ap.add_argument("--out", help="Write the report here (refused in this repo)")
    args = ap.parse_args(argv)
    problem = paths.out_problem(args.out) if args.out else None
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    try:
        text = report(args.cut, args.fps, args.beats, args.profile, args.hook, args.window)
    except (RhythmError, dc.CheckError, cutlist.CutlistError, OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.out:
        paths.write_private(args.out, text)
        print(f"Wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
