"""
rpresolve.decon_motion: change inside a take that is no cut (`decon recipe --motion`,
`decon compare --key --motion`, `decon compare REF REBUILD --roi`). Offline: ffmpeg and numpy.

    find(video, cuts)              [seconds] where a punch-in, pan or scale change starts, or a
                                   region or object turns on or off, with no cut
    events(video, cuts, roi)       the same as dicts (t, frames, magnitude, kind), for one region

A port of the idea of a frame-difference event reader: frames are read EVENT_W wide in grey and
each frame step scores the mean absolute grey change from the frame before (0 to 255). A burst is
a run of steps above max(2, twice the quarter-point step), runs with a gap of one frame joining. A
burst near a cut is that cut. A burst of two frames or less that peaks high and comes back is a
flash, which decon_effects reads. A `step` is a burst of half a second or less after which the
picture settles on a new state (an object lights, a title lands); any other burst is a `move` (a
punch-in, a pan). An event is kept when its size, a step's net change or a move's peak, reaches
`floor`. FLOOR 6 is for the whole frame; a region is a small part of it, so ROI_FLOOR is lower.
An event's time is when its speed first reaches RISE of its peak. FLOOR and RISE were set on one
reference of animated punch-ins (on 2026-11-23 with the cut detector: 44 of 56 steps found, 9 detections
near no step, where the cut detectors alone found 28); 5 to 7 and a quarter to a third give the same.

Shortcut: the frames of one video are held in memory (a minute of 9:16 at 25 fps is about 70 MB).
"""

import subprocess

import numpy as np

from . import delivercheck as dc

EVENT_W = 160     # frames are read this wide
FLOOR = 6.0       # grey levels: smallest event, whole frame
ROI_FLOOR = 2.5   # the same for a region
HOLD_S = 0.3      # seconds a state must hold on each side of a step
STEP_MAX_S = 0.5  # a longer burst is a move
FLASH_FRAMES = 2
RISE = 0.25       # an event starts when its speed first reaches this share of its peak (an ease-in's tail is not the start)
CUT_PAD = 2       # frames: a burst this near a cut is the cut


def read(video, roi=None):
    """(uint8 grey frames [n, h, w], fps), cropped to roi (x, y, w, h in source pixels) and read EVENT_W wide."""
    info = dc.probe(video)
    v = next((s for s in info.get("streams") or [] if s.get("codec_type") == "video"), None)
    fps = dc.rate(v.get("avg_frame_rate")) if v else None
    if not v or not fps or not v.get("width") or not v.get("height"):
        raise dc.CheckError(f"{video} has no readable video stream")
    x, y, w, h = roi or (0, 0, int(v["width"]), int(v["height"]))
    if w < 2 or h < 2 or x < 0 or y < 0 or x + w > int(v["width"]) or y + h > int(v["height"]):
        raise dc.CheckError(f"roi {x},{y},{w},{h} is not inside the {v['width']}x{v['height']} frame of {video}")
    oh = max(2, round(EVENT_W * h / w / 2) * 2)
    p = subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-i", dc._src(video), "-map", "0:v:0",
                        "-vf", f"crop={w}:{h}:{x}:{y},scale={EVENT_W}:{oh}:flags=area,format=gray", "-f", "rawvideo", "-"],
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=dc.READ_TIMEOUT_S)
    if p.returncode != 0:
        raise dc.CheckError(f"ffmpeg could not read {video}: {p.stderr.decode(errors='replace')[-200:]}")
    return np.frombuffer(p.stdout, np.uint8).reshape(-1, oh, EVENT_W), float(fps)


def _diff(a, b):
    return float(np.abs(a.astype(np.int16) - b).mean())


def _bursts(d):
    q = np.sort(d[1:])
    act = max(2.0, 2 * float(q[len(q) // 4]))
    out = []
    for i in range(1, len(d)):
        if d[i] <= act:
            continue
        if out and i - out[-1][1] <= 2:
            out[-1][1] = i
        else:
            out.append([i, i])
    return out


def from_frames(g, fps, cuts=(), floor=FLOOR):
    """Events in grey frames g [n, h, w]: dicts of t (seconds), frames, magnitude, kind (step or move)."""
    if len(g) < 3:
        return []
    d = np.array([0.0] + [_diff(g[i], g[i - 1]) for i in range(1, len(g))])
    settle, cutf = round(HOLD_S * fps), [c * fps for c in cuts]
    out = []
    for a, b in _bursts(d):
        if a <= 1 or any(a - CUT_PAD <= c <= b + CUT_PAD for c in cutf):
            continue
        net, peak = _diff(g[b], g[a - 1]), float(d[a:b + 1].max())
        if b - a + 1 <= FLASH_FRAMES and peak >= 2 * floor and b + 1 < len(g) and _diff(g[b + 1], g[a - 1]) < floor:
            continue
        was, now = _diff(g[a - 1], g[max(0, a - 1 - settle)]), _diff(g[b], g[min(len(g) - 1, b + settle)])
        step = net >= floor and b - a + 1 <= STEP_MAX_S * fps and max(was, now) <= net / 3
        size = net if step else peak
        if size >= floor:
            on = a + int(np.argmax(d[a:b + 1] >= RISE * peak))
            out.append({"t": round(on / fps, 3), "frames": b - a + 1, "magnitude": round(size, 1),
                        "kind": "step" if step else "move"})
    return out


def events(video, cuts=(), roi=None, floor=None):
    g, fps = read(video, roi)
    return from_frames(g, fps, cuts, floor if floor is not None else (ROI_FLOOR if roi else FLOOR))


def find(video, cuts=()):
    return [e["t"] for e in events(video, cuts)]


def parse_roi(text):
    """'x,y,w,h' as four ints."""
    try:
        r = tuple(int(float(v)) for v in text.split(","))
    except ValueError:
        r = ()
    if len(r) != 4:
        raise ValueError(f"roi must be x,y,w,h in pixels, got '{text}'")
    return r


def region_table(video_ref, rois, video_rb, rb_roi=None, cuts_ref=(), cuts_rb=()):
    """On and off times of change in each region of the reference against the rebuild's (whole frame
    unless rb_roi). The events of all regions are merged by time and read in pairs, on then off; the
    rebuild's are paired in order, and its events beyond the reference's pairs are counted, not scored.
    {rows: [{region, ref: [on, off], rebuild: [on, off], diff: [on, off]}], rebuild_extra}"""
    ref = sorted(((e["t"], i) for i, r in enumerate(rois) for e in events(video_ref, cuts_ref, r, ROI_FLOOR)))
    rb = [e["t"] for e in events(video_rb, cuts_rb, rb_roi, ROI_FLOOR)]
    n = len(ref) // 2
    rows = []
    for k in range(n):
        (a, ia), (b, ib) = ref[2 * k], ref[2 * k + 1]
        row = {"region": "+".join(str(i + 1) for i in sorted({ia, ib})), "ref": [a, b]}
        got = rb[2 * k:2 * k + 2]
        if len(got) == 2:
            row["rebuild"], row["diff"] = got, [round(got[0] - a, 3), round(got[1] - b, 3)]
        rows.append(row)
    return {"rows": rows, "rebuild_extra": max(0, len(rb) - 2 * n)}


def summary_table(t):
    out = ["Region on and off (s): reference, rebuild, rebuild minus reference"]
    for k, r in enumerate(t["rows"], 1):
        rb = f"{r['rebuild'][0]:g}, {r['rebuild'][1]:g}" if "rebuild" in r else "none"
        df = f"{r['diff'][0]:+.2f}, {r['diff'][1]:+.2f}" if "diff" in r else "none"
        out.append(f"  {k} (region {r['region']}): {r['ref'][0]:g}, {r['ref'][1]:g} | {rb} | {df}")
    if t["rebuild_extra"]:
        out.append(f"  {t['rebuild_extra']} more rebuild changes after the last pair")
    return "\n".join(out)
