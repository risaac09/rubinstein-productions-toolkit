"""
rpresolve.filmed: the rate a clip was filmed at, and the frames it really holds.

Resolve can report something else. A BRAW shot off-speed (24 fps on a camera set
to a 29.97 project) reads as 29.97 with a count about a quarter too long: the
frames play fast and the last one holds to the end. Timelines that follow the
filming use these numbers.
"""

import subprocess

from . import detect

STANDARD = ("23.976", "24", "25", "29.97", "30", "47.952", "48", "50", "59.94", "60",
            "100", "119.88", "120")


def snap(fps):
    """fps as Resolve spells the nearest standard rate, or to three places."""
    near = min(STANDARD, key=lambda s: abs(float(s) - fps))
    return near if abs(float(near) - fps) < 0.05 else f"{fps:.3f}".rstrip("0").rstrip(".")


def _out(cmd, run):
    return run(cmd, capture_output=True, text=True).stdout.strip()


def read(path, run=subprocess.run):
    """{fps, frames} for a file. A camera's own off-speed frame time beats the
    container's average rate, which drifts (23.987 for a 24 fps clip)."""
    t = _out([detect.EXIFTOOL, "-s3", "-OffspeedFrameTime", path], run)
    line = _out([detect.FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
                 "stream=avg_frame_rate,nb_frames", "-of", "csv=p=0", path], run)
    parts = line.splitlines()[0].split(",") if line else []
    if len(parts) < 2 or not parts[1].isdigit():
        raise ValueError(f"cannot read the frames of {path}")
    try:
        fps = 1 / float(t)
    except (ValueError, ZeroDivisionError):
        num, _, den = parts[0].partition("/")
        fps = float(num) / float(den or 1)
    return {"fps": snap(fps), "frames": int(parts[1])}


def timeline_rate(rows, probe=read):
    """The one rate rows were filmed at, and {uid: real frames}. Raises
    ValueError on mixed rates, or a clip Resolve still reads at another rate."""
    rates, real, wrong = {}, {}, []
    for r in rows:
        got = probe(r["path"])
        rates.setdefault(got["fps"], []).append(r["name"])
        real[r["uid"]] = got["frames"]
        if abs(float(r["fps"]) - float(got["fps"])) > 0.01:
            wrong.append(f"{r['name']} ({r['fps']})")
    if len(rates) > 1:
        raise ValueError("these clips were filmed at different rates: " + "; ".join(
            f"{k}: {', '.join(v[:3])}" for k, v in sorted(rates.items())))
    if wrong:
        raise ValueError(f"filmed at {next(iter(rates))} but Resolve reads another rate: "
                         f"{', '.join(wrong[:3])}. link_proxies sets it, on a clip no timeline uses.")
    return next(iter(rates)), real


def trim(rows, real):
    """Cut each row to the frames its file holds, in place."""
    at = 0
    for r in rows:
        if real[r["uid"]] < r["frames"]:
            r["end"] = real[r["uid"]]  # exclusive
            r["length"] = real[r["uid"]]
        r["record"] = at
        at += r["length"]
