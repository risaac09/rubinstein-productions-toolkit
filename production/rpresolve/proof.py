"""
rpresolve.proof: the pipeline proof. Compares a test render with a reference
(another render, or Resolve's own decode exported as a file) and says PASS or
FAIL, row by row. Offline: ffmpeg reads both files; nothing touches Resolve.

Rows:
    - frames: both files give the same number of frames and the same size
    - range: legal or full range per file (the colour_range tag, and values
      outside 16..235 in signalstats); a mismatch is its own FAIL row
    - level shift: a systematic offset in mean luma with the range agreeing,
      the mark of a gamma or transfer mismatch; the row names the measured
      offset in 8-bit codes and the gamma that would explain it
    - luma / chroma shift: per-frame mean Y, U and V of the test minus the
      reference (signalstats YAVG, UAVG, VAVG)
    - psnr, ssim: ffmpeg psnr and ssim filters on the raw code values
    - vmaf: ffmpeg libvmaf when the build has it, else "not available"

Luma and range come from measure.signal_frames; this module adds no second
reader of them.

THE TRAP. Resolve decodes log H.264 about 25 code values darker than ffmpeg
does. Never compare a Resolve render against an ffmpeg decode of a log
source: the level-shift row will fire, and it is the decode, not the pipeline.
Compare render against render, or against Resolve's own decode of the clip
exported as a file.
"""

import json
import math
import re

from . import measure
from .measure import MeasureError

DEFAULTS = {"psnr": 45.0, "ssim": 0.99, "vmaf": 95.0, "luma_shift": 1.5, "chroma_shift": 1.5}
MAX_FRAMES = 300
NOT_AVAILABLE = "not available"
LEGAL = (16, 235)
RANGE_MARGIN = 1.5  # codes of slack around 16..235 before a file counts as full range


def _ffmpeg(args):
    proc = measure._run([measure.FFMPEG, "-hide_banner", "-nostats"] + args,
                        encoding="utf-8", errors="replace")
    return proc


def has_libvmaf():
    proc = measure._run([measure.FFMPEG, "-hide_banner", "-filters"], encoding="utf-8", errors="replace")
    return bool(re.search(r"\blibvmaf\b", proc.stdout))


def detect_range(tags, frames):
    """'full' or 'legal' for one file: the colour_range tag when it says so
    (legal footage carries sub-black and super-white overshoot, so values
    outside 16..235 do not overrule a tv tag), else the signalstats extremes
    against 16..235."""
    if tags.get("color_range") in ("pc", "tv"):
        return "full" if tags["color_range"] == "pc" else "legal"
    if frames and (min(f["ymin"] for f in frames) < LEGAL[0] - RANGE_MARGIN
                   or max(f["ymax"] for f in frames) > LEGAL[1] + RANGE_MARGIN):
        return "full"
    return "legal"


def _pair_filter(graph):
    # setparams drops the range tag so the filters compare raw code values and
    # swscale cannot rescale one file to match the other and hide a range shift.
    return ("[0:v]setparams=range=tv,format=yuv420p[a];[1:v]setparams=range=tv,format=yuv420p[b];"
            + graph)


def _compare(test, ref, graph, frames):
    proc = _ffmpeg(["-i", "file:" + test, "-i", "file:" + ref, "-frames:v", str(frames),
                    "-lavfi", _pair_filter(graph), "-f", "null", "-"])
    return proc.stderr


def psnr(test, ref, frames):
    """(average dB over frames, minimum dB); inf for identical frames."""
    err = _compare(test, ref, "[a][b]psnr", frames)
    m = re.search(r"PSNR .*average:(\S+) min:(\S+)", err)
    if not m:
        raise MeasureError("ffmpeg psnr printed no summary: " + err.strip()[-200:])
    return float(m.group(1)), float(m.group(2))


def ssim(test, ref, frames):
    err = _compare(test, ref, "[a][b]ssim", frames)
    m = re.search(r"SSIM .*All:(\S+)", err)
    if not m:
        raise MeasureError("ffmpeg ssim printed no summary: " + err.strip()[-200:])
    return float(m.group(1))


def vmaf(test, ref, frames):
    """Mean VMAF, or None when the ffmpeg build has no libvmaf."""
    if not has_libvmaf():
        return None
    err = _compare(test, ref, "[a][b]libvmaf", frames)
    m = re.search(r"VMAF score: (\S+)", err)
    if not m:
        raise MeasureError("ffmpeg libvmaf printed no score: " + err.strip()[-200:])
    return float(m.group(1))


def _mean(xs):
    return sum(xs) / len(xs)


def _row(name, ok, value, limit=""):
    return {"check": name, "status": "PASS" if ok else "FAIL", "value": value, "limit": limit}


def shifts(test_frames, ref_frames):
    """Per-frame test minus reference for Y, U and V means, in 8-bit codes."""
    n = min(len(test_frames), len(ref_frames))
    return [{k: test_frames[i][k + "avg"] - ref_frames[i][k + "avg"] for k in "yuv"}
            for i in range(n)]


def gamma_for(test_mean, ref_mean):
    """The gamma g with (test/255) = (ref/255)^g that turns the reference mean
    into the test mean; None where either mean sits at a bound."""
    a, b = test_mean / 255, ref_mean / 255
    if not (0 < a < 1 and 0 < b < 1):
        return None
    return math.log(a) / math.log(b)


def compare(test, ref, thresholds=None, frames=MAX_FRAMES):
    """Compare test against ref; returns a report with 'rows', 'passed' and
    the per-frame shifts. Raises MeasureError when a file cannot be read."""
    th = dict(DEFAULTS, **{k: v for k, v in (thresholds or {}).items() if v is not None})
    infos = [measure.probe(p) for p in (test, ref)]
    depths = [measure.bit_depth(i["tags"]["pix_fmt"]) for i in infos]
    sig = [measure.signal_frames(p, d, 0.0, frames) for p, d in zip((test, ref), depths)]
    if not all(sig):
        raise MeasureError("signalstats read no frames from " + (test if not sig[0] else ref))
    rows = []
    size = [(i["width"], i["height"]) for i in infos]
    counts = [len(s) for s in sig]
    same = size[0] == size[1] and counts[0] == counts[1]
    rows.append(_row("frames", same, f"test {size[0][0]}x{size[0][1]} x{counts[0]}, "
                     f"ref {size[1][0]}x{size[1][1]} x{counts[1]}", "same size and frame count"))
    ranges = [detect_range(i["tags"], s) for i, s in zip(infos, sig)]
    extremes = [f"{min(f['ymin'] for f in s):.0f}..{max(f['ymax'] for f in s):.0f}" for s in sig]
    rows.append(_row("range", ranges[0] == ranges[1],
                     f"test {ranges[0]} (Y {extremes[0]}), ref {ranges[1]} (Y {extremes[1]})",
                     "same range"))
    sh = shifts(*sig)
    ys, us, vs = ([s[k] for s in sh] for k in "yuv")
    mean_y = _mean(ys)
    if ranges[0] == ranges[1]:
        g = gamma_for(_mean([f["yavg"] for f in sig[0]]), _mean([f["yavg"] for f in sig[1]]))
        rows.append(_row("gamma or level shift", abs(mean_y) <= th["luma_shift"],
                         f"mean luma {mean_y:+.2f} codes" + (f", gamma about {g:.2f}" if g else ""),
                         f"within +-{th['luma_shift']} codes"))
    else:
        rows.append(_row("gamma or level shift", True, "not judged: the ranges differ", ""))
    rows.append(_row("luma shift per frame", max(abs(y) for y in ys) <= th["luma_shift"] or ranges[0] != ranges[1],
                     f"mean {mean_y:+.2f}, worst {max(ys, key=abs):+.2f} codes", f"+-{th['luma_shift']} codes"))
    worst_c = max(us + vs, key=abs)
    rows.append(_row("chroma shift per frame", abs(worst_c) <= th["chroma_shift"],
                     f"U mean {_mean(us):+.2f}, V mean {_mean(vs):+.2f}, worst {worst_c:+.2f} codes",
                     f"+-{th['chroma_shift']} codes"))
    if same:
        p_avg, p_min = psnr(test, ref, frames)
        rows.append(_row("psnr", p_avg >= th["psnr"], f"{p_avg:.2f} dB avg, {p_min:.2f} min",
                         f">= {th['psnr']} dB"))
        s = ssim(test, ref, frames)
        rows.append(_row("ssim", s >= th["ssim"], f"{s:.4f}", f">= {th['ssim']}"))
        try:
            v = vmaf(test, ref, frames)
        except MeasureError as e:
            rows.append(_row("vmaf", False, "libvmaf failed: " + str(e), f">= {th['vmaf']}"))
        else:
            if v is None:
                rows.append({"check": "vmaf", "status": "n/a", "limit": f">= {th['vmaf']}",
                             "value": NOT_AVAILABLE + " (ffmpeg has no libvmaf)"})
            else:
                rows.append(_row("vmaf", v >= th["vmaf"], f"{v:.2f}", f">= {th['vmaf']}"))
    else:
        for name in ("psnr", "ssim", "vmaf"):
            rows.append({"check": name, "status": "n/a", "value": "skipped: size or frame count differs",
                         "limit": ""})
    return {"test": test, "ref": ref, "thresholds": th, "rows": rows,
            "passed": all(r["status"] != "FAIL" for r in rows),
            "frames": [{"i": i, "dy": round(s["y"], 3), "du": round(s["u"], 3), "dv": round(s["v"], 3)}
                       for i, s in enumerate(sh)]}


def format_report(report):
    """FAIL rows first, then the rest; the verdict last."""
    rows = sorted(report["rows"], key=lambda r: r["status"] != "FAIL")
    lines = [f"pipeline-proof: {report['test']} against {report['ref']}"]
    lines += [f"  {r['status']:<4}  {r['check']:<24} {r['value']}" + (f"  [{r['limit']}]" if r["limit"] else "")
              for r in rows]
    lines.append("RESULT: " + ("PASS" if report["passed"] else "FAIL"))
    return "\n".join(lines) + "\n"


def write_json(report, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
