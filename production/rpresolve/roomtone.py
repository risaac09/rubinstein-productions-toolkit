"""
rpresolve.roomtone: find the room tone a recording already holds, and loop
it into a bed of any length with seams that do not pump. Offline: ffmpeg
decodes and renders, numpy measures; the source is never changed.

Run from production/:
    python3 -m rpresolve.roomtone find FILE [FILE ...] [--window 3] [--out JSON]
    python3 -m rpresolve.roomtone bed FILE --length S --out BED.wav [--stream N]
        [--start S --end S] [--xfade 1] [--curve auto|qsin|tri]

find reads every audio stream of every file and gives, per stream, the
quietest --window seconds (start, end, RMS and peak in dBFS, from 100 ms
steps). A stream that is all digital zeros, such as the two unused tracks
of a four-track camera file, is left out and reported as digital_silence;
a window that crosses any digital silence inside a live stream (a dropout:
a run of 1 ms or more that is zero on every channel, dxmetrics.ZERO_RUN_S)
is not room tone and is never chosen. A stream shorter than the window
gives the whole stream.

bed takes [start, end) of one stream (by default the quietest WINDOW_S that
find gives) and makes one loop unit from it: the window's last --xfade
seconds crossfaded (acrossfade) into its start, so the unit's end runs on
into its own beginning sample for sample. aloop repeats the unit to
--length. Every seam is then that one crossfade, and the check measures
its middle: the power over the middle SEAM_SHARE of each crossfade in the
bed against the mean power of the two source stretches it blends, so the
room's own drift does not count against the seam. qsin keeps the power of
incoherent noise level through a crossfade (tri dips about 3 dB), tri
keeps the amplitude of coherent tones such as hum level (qsin bumps about
3 dB); --curve auto renders both and keeps the one with the smaller
deviation. A seam more than SEAM_MAX_DB off is reported and the command
exits 1, the bed still written for listening.
The bed has the stream's own rate and channels, as 24-bit WAV, with a JSON
record beside it (BED.json).

What it does not do: decide the level a bed sits at under dialogue (6 to
10 dB under the clip noise is a usual start), fill gaps in a timeline, or
remove hum from the loop. A window under MIN_SOURCE_S is refused: a short
loop is heard as a loop. None of this is listening.
"""

import argparse
import json
import math
import os
import sys

import numpy as np

from . import audioreport as ar
from . import delivercheck as dc
from . import dxmetrics as dxm
from . import paths

WINDOW_S = 3.0
HOP_S = 0.1
MIN_SOURCE_S = 2.0
XFADE_S = 1.0
SEAM_SHARE = 0.4                 # the middle 40% of each crossfade is measured
SEAM_MAX_DB = 1.0
CURVES = ("qsin", "tri")
RUN_TIMEOUT_S = 3600


def _db(power):
    return round(10 * math.log10(power), 1) if power > 0 else None


def quietest(power, n, dead=None):
    """(first hop, hops) of the n-hop run with the least mean power that
    holds no digital silence (a hop of zero power, or one flagged in `dead`),
    or None. n is cut to the stream's length."""
    n = min(n, len(power))
    if n < 1:
        return None
    c = np.concatenate(([0.0], np.cumsum(power)))
    bad = power <= 0 if dead is None else (power <= 0) | dead
    dead = np.concatenate(([0], np.cumsum(bad)))
    sums = c[n:] - c[:-n]
    sums[(dead[n:] - dead[:-n]) > 0] = np.inf
    i = int(np.argmin(sums))
    return (i, n) if np.isfinite(sums[i]) else None


def find_stream(path, stream, window_s=WINDOW_S):
    """The quietest window of one stream, as a row."""
    fp = dxm.frame_power(path, stream, HOP_S)
    hop = fp["hop_s"]
    row = {"file": path, "stream": stream, "channels": fp["channels"],
           "sample_rate": fp["sample_rate"],
           "duration_s": round(fp["samples"] / fp["sample_rate"], 3)}
    if not len(fp["peak"]) or fp["peak"].max() == 0:
        return dict(row, status="digital_silence", note="every sample is zero: left out")
    q = quietest(fp["power"], max(1, int(round(window_s / hop))), fp["dead"])
    if q is None:
        return dict(row, status="no_window",
                    note="no stretch of the window's length without digital silence")
    i, n = q
    return dict(row, status="ok", start_s=round(i * hop, 3), end_s=round((i + n) * hop, 3),
                rms_dbfs=_db(float(fp["power"][i:i + n].mean())),
                peak_dbfs=_db(float(fp["peak"][i:i + n].max()) ** 2),
                window_s=round(n * hop, 3))


def find(files, window_s=WINDOW_S):
    """{params, streams: [row per audio stream of every file]}."""
    dc.check_tools(ffmpeg=True)
    if window_s <= 0:
        raise ValueError("--window must be positive")
    rows = []
    for f in files:
        try:
            info = dc.probe(f)
        except dc.CheckError as e:
            raise ar.ReportError(str(e))
        count = sum(1 for s in info.get("streams", []) if s.get("codec_type") == "audio")
        if not count:
            raise ar.ReportError(f"{f} has no audio stream")
        rows += [find_stream(f, k, window_s) for k in range(count)]
    return {"params": {"window_s": window_s, "hop_s": HOP_S}, "streams": rows}


def find_summary(r):
    lines = []
    for s in r["streams"]:
        head = f"{os.path.basename(s['file'])} s{s['stream']} ({s['channels']} ch)"
        if s["status"] == "ok":
            lines.append(f"{head}: {s['start_s']:.1f}-{s['end_s']:.1f} s, RMS {s['rms_dbfs']} "
                         f"dBFS, peak {s['peak_dbfs']} dBFS")
        else:
            lines.append(f"{head}: {s['status']} ({s['note']})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The bed
# ---------------------------------------------------------------------------

def bed_graph(stream, src_samples, xfade_samples, total_samples, curve):
    """The filter_complex: one loop unit (the window's tail crossfaded into
    its head, src - xfade samples long), looped and cut to total. The tail
    and head come from two opens of the source (inputs 0 and 1), not one
    asplit: acrossfade in ffmpeg 6.1 to 7.1 ends its output as soon as its
    second input is done, and an asplit head ends before the tail starts."""
    unit = src_samples - xfade_samples
    return (f"[0:a:{stream}]atrim=start_sample={unit}:end_sample={src_samples},"
            f"asetpts=PTS-STARTPTS[tail];"
            f"[1:a:{stream}]atrim=end_sample={unit},asetpts=PTS-STARTPTS[head];"
            f"[tail][head]acrossfade=ns={xfade_samples}:c1={curve}:c2={curve},"
            f"aloop=loop=-1:size={unit},atrim=end_sample={total_samples},"
            f"asetpts=N/SR/TB[out]")


def seams(x, src, unit, xfade):
    """(worst deviation dB, seam count): the RMS over the middle SEAM_SHARE
    of each crossfade in the bed x, against the mean power of the two source
    stretches it blends (src is the window, mono). Room tone drifts on its
    own; this measures only what the crossfade adds or takes away."""
    w = max(1, int(round(SEAM_SHARE * xfade)))
    a = xfade // 2 - w // 2

    def power(y, i):
        return float((y[i:i + w] ** 2).mean())
    ref = (power(src, unit + a) + power(src, a)) / 2
    devs = []
    for k in range(0, len(x) - (a + w) + 1, unit):
        p = power(x, k + a)
        devs.append(10 * math.log10(p / ref) if p > 0 and ref > 0 else float("inf"))
    worst = max(devs, key=abs) if devs else 0.0
    return round(worst, 2) + 0.0, len(devs)


def _render(src, start, graph, tmp):
    proc = dc._run([dc.FFMPEG, "-hide_banner", "-nostdin", "-nostats", "-v", "error", "-y",
                    *(["-ss", f"{start:.6f}", "-i", dc._src(src)] * 2), "-filter_complex", graph,
                    "-map", "[out]", "-map_metadata", "-1", "-c:a", "pcm_s24le", "-f", "wav",
                    dc._src(tmp)], RUN_TIMEOUT_S)
    if proc.returncode != 0:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise ar.ReportError(f"ffmpeg could not render the bed: {proc.stderr.strip()[-300:]}")
    return tmp


def bed(src, out, length_s, stream=0, start=None, end=None, xfade_s=XFADE_S, curve="auto"):
    """Write the bed WAV and its JSON record; return the record."""
    dc.check_tools(ffmpeg=True)
    outs = (out, os.path.splitext(out)[0] + ".json")
    for p in outs:
        problem = paths.out_problem(p, any_git_tree=True)
        if problem:
            raise paths.OutputRefused(problem)
    problem = dxm.source_problem(outs, [src])
    if problem:
        raise paths.OutputRefused(problem)
    if curve not in CURVES + ("auto",):
        raise ValueError(f"--curve is one of auto, {', '.join(CURVES)}")
    if length_s <= 0:
        raise ValueError("--length must be positive")
    if (start is None) != (end is None):
        raise ValueError("give both --start and --end, or neither")
    if start is None:
        row = find_stream(src, stream)
        if row["status"] != "ok":
            raise ar.ReportError(f"no room tone in {src} stream {stream}: {row['note']}")
        start, end = row["start_s"], row["end_s"]
    rate = ar.audio_stream(src, stream)["sample_rate"]
    if end - start < MIN_SOURCE_S:
        raise ValueError(f"the window is {end - start:.2f} s; a loop needs at least "
                         f"{MIN_SOURCE_S:g} s of source or it is heard as a loop")
    if not 0 < xfade_s <= (end - start) / 2:
        raise ValueError("--xfade must be above 0 and at most half the window")
    src_n, xf_n = int(round((end - start) * rate)), int(round(xfade_s * rate))
    total = int(round(length_s * rate))
    window, _ = dxm.decode(src, stream, start, end)
    if len(window) < src_n - 1:
        raise ar.ReportError(f"{src} holds {len(window) / rate:.2f} s from {start:g} s, "
                             f"less than the {end - start:g} s window")
    tried, best, part = {}, None, None
    try:
        for c in (CURVES if curve == "auto" else (curve,)):
            part = _render(src, start, bed_graph(stream, src_n, xf_n, total, c),
                           f"{out}.{c}.part.wav")
            x, _ = dxm.decode(part)
            if abs(len(x) - total) > 1:
                raise ar.ReportError(f"{len(x)} samples rendered, {total} asked")
            tried[c] = seams(x, window, src_n - xf_n, xf_n)
            if best is None or abs(tried[c][0]) < abs(tried[best[0]][0]):
                best, part = (c, part), (best[1] if best else None)
            if part:
                os.remove(part)
            part = None
    except BaseException:
        for f in (part, best and best[1]):
            if f and os.path.exists(f):
                os.remove(f)
        raise
    os.replace(best[1], out)
    dev, count = tried[best[0]]
    rec = {"source": src, "stream": stream, "window_s": [start, end], "length_s": length_s,
           "xfade_s": xfade_s, "unit_s": round((src_n - xf_n) / rate, 6), "sample_rate": rate,
           "curve": best[0], "seam_dev_db": dev, "seams": count,
           "seam_ok": abs(dev) <= SEAM_MAX_DB,
           "tried": {k: v[0] for k, v in tried.items()}, "out": out,
           "params": {"seam_share": SEAM_SHARE, "seam_max_db": SEAM_MAX_DB,
                      "min_source_s": MIN_SOURCE_S}}
    paths.write_private(os.path.splitext(out)[0] + ".json", json.dumps(rec, indent=1) + "\n",
                        any_git_tree=True)
    return rec


def bed_summary(r):
    return (f"{r['out']}: {r['length_s']:g} s from {r['window_s'][0]:g}-{r['window_s'][1]:g} s, "
            f"{r['curve']} crossfade {r['xfade_s']:g} s, seam {r['seam_dev_db']:+} dB over "
            f"{r['seams']} seam(s) ({'ok' if r['seam_ok'] else 'OVER ' + str(SEAM_MAX_DB) + ' dB'})"
            f"; tried " + ", ".join(f"{k} {v:+}" for k, v in r["tried"].items()))


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def add_arguments(p):
    sub = p.add_subparsers(dest="action", required=True)
    f = sub.add_parser("find", help="the quietest window of every audio stream")
    f.add_argument("files", nargs="+")
    f.add_argument("--window", type=float, default=WINDOW_S, help="Seconds (default 3)")
    f.add_argument("--out", help="Write the JSON here, outside any git tree")
    b = sub.add_parser("bed", help="loop a window into a bed of any length")
    b.add_argument("file")
    b.add_argument("--length", type=float, required=True, help="Seconds of bed")
    b.add_argument("--out", required=True, help="The bed WAV, outside any git tree")
    b.add_argument("--stream", type=int, default=0)
    b.add_argument("--start", help="Window start (default: the quietest window)")
    b.add_argument("--end", help="Window end")
    b.add_argument("--xfade", type=float, default=XFADE_S, help="Crossfade seconds (default 1)")
    b.add_argument("--curve", choices=("auto",) + CURVES, default="auto")


def run(args):
    """Exit 0 written; 1 not written, or (bed) a seam over SEAM_MAX_DB."""
    try:
        if args.action == "find":
            problem = (paths.out_problem(args.out, any_git_tree=True)
                       or dxm.source_problem([args.out], args.files) if args.out else None)
            if problem:
                raise paths.OutputRefused(problem)
            r = find(args.files, args.window)
            print(find_summary(r))
            if args.out:
                paths.write_private(args.out, json.dumps(r, indent=1) + "\n", any_git_tree=True)
                print(f"Wrote {args.out}", file=sys.stderr)
            return 0
        r = bed(args.file, args.out, args.length, args.stream,
                ar.parse_time(args.start) if args.start else None,
                ar.parse_time(args.end) if args.end else None, args.xfade, args.curve)
    except (ar.ReportError, dc.CheckError, dc.ToolMissing, paths.OutputRefused, ValueError,
            OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(bed_summary(r))
    return 0 if r["seam_ok"] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m rpresolve.roomtone",
                                 description="Find a recording's room tone and loop it into a "
                                             "bed. Offline.")
    add_arguments(ap)
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
