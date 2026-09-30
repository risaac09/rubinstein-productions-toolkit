"""
rpresolve.reframe: a static crop per span for the 9:16 and 1:1 versions of
a cut, centred on the speaker's face, and the check that the face stays
inside it on every sampled frame. Offline: ffmpeg reads frames from the
source and macOS Vision finds faces (vision.py); nothing touches Resolve.
Stdlib only.

For each span of each clip:
  1. Frames are sampled evenly across [in, out): `samples` per span
     (default 8) and at least `per_second` a second (default 0.5), at
     in + (i + 0.5) * length / n, never more than MAX_SAMPLES.
  2. The picture area: the rows and columns whose mean luma is above
     ACTIVE_LEVEL (24 of 255) in at least one sample. A call recording
     that letterboxes its panes inside the 16:9 frame has a picture area
     smaller than the frame, and a crop that leaves it shows black bars.
  3. Faces: Vision's boxes, linked into tracks across the samples by overlap
     (IoU at least LINK_IOU, or centres within LINK_DISTANCE box widths).
     The primary face (RULE) is the track present in the most samples, a
     tie going to the larger median box. With speaker_x (a person naming
     the speaker, in source pixels), it is the track whose median centre is
     nearest that x among the eligible tracks: those present in at least
     half as many samples as the most present one. Without it, any other
     eligible track at AMBIGUOUS_AREA of the primary's median area or more
     makes the choice ambiguous, and the span is flagged for review: on a
     two-up call Vision often misses the speaker as they turn or lean, so
     the steadier listener can win on presence.
     Every sample is judged: one where the primary face is missing has
     the box nearest its path checked in its place, when that box is
     within NEAR_PATH face widths and no other eligible track holds it (a
     lean that jumps further than LINK_DISTANCE starts a track of its
     own); else the sample is unchecked. Either way the span is flagged.
  4. The crop: centred on the median face centre, clamped so the crop stays
     inside the picture area, at `scale` output pixels per source pixel
     (cut.py's convention; the clip's reframe.scale, else DEFAULT_SCALE).
     When that loses the face, the middle of the checked boxes' extent
     (each grown by MARGIN), clamped the same way, is tried at the same
     scale before any other: when any centre at a scale holds every box,
     that one does (to the 0.1 px the centre is rounded to). The result
     records which centre was used.
  5. The check: every checked box (the primary face's, and any taken in
     its place), grown by MARGIN of its size on each side, is mapped
     through that exact transform and must sit inside the output frame.
     Reported over all the span's samples: inside, unchecked, the share
     inside, the worst sample and by how many output pixels.
  6. The fallback: when the default scale leaves bars (the crop is larger
     than the picture area) or loses the face, the widest scale that still
     fills the frame is tried and checked the same way (the median, then
     the extent's middle). When that fails too, the span needs a manual
     reframe or a split. A span with no face in any sample says so and
     gets no centre; nothing is guessed.
     allow_bars keeps the default scale when it holds the face but leaves
     bars (the Ep 002 look); such a crop is status "bars" and named as such.

The transform, for Resolve's default input scaling (scaleToFit): the
source is fit into the timeline by fit = min(W/sw, H/sh), zoomed about the
frame centre, then moved in timeline pixels. A crop of s output pixels per
source pixel centred on source point (cx, cy) is
    ZoomX = ZoomY = s / fit     (scaleToCrop: fit = max(W/sw, H/sh))
    Pan  = (sw/2 - cx) * s
    Tilt = (cy - sh/2) * s
and a source point (x, y) lands at X = W/2 + (x - cx) s, Y = H/2 + (y - cy) s.
Pan's sign and units are confirmed on a render (the Ep 002 sandbox 9:16:
the face centre measured in the render is where this puts it, within 3
pixels). Tilt's sign assumes Resolve's y axis points up; it is not yet
confirmed on a render (see resolve-template-spec.md, Reframe), so every
report row whose crop sets a Tilt says so, and the summary counts them.
"""

import copy
import json
import math
import os
import statistics
import subprocess
import tempfile

from . import vision
from .detect import DetectError, run_ffprobe

FFMPEG = os.environ.get("RPRESOLVE_FFMPEG", "/opt/homebrew/bin/ffmpeg")
TIMEOUT_S = 120  # per ffmpeg frame grab

ASPECTS = {"9x16": (1080, 1920), "1x1": (1080, 1080)}
DEFAULT_SCALE = 2.25       # output pixels per source pixel (the Ep 002 script's crop)
DEFAULT_SAMPLES = 8
MIN_PER_SECOND = 0.5
MAX_SAMPLES = 120
MARGIN = 0.10              # of the face box's width and height, on each side
ACTIVE_LEVEL = 24          # mean luma (0..255) above which a row or column is picture
LINK_IOU = 0.2
LINK_DISTANCE = 0.75       # box widths between centres
AMBIGUOUS_AREA = 0.5       # another eligible face at this share of the primary's area: ambiguous
NEAR_PATH = 3.0            # face widths: a box this near the primary, held by no rival, is checked
FILL_TOLERANCE_PX = 0.5
SCALE_TO_FIT, SCALE_TO_CROP = "scaleToFit", "scaleToCrop"
SCALINGS = (SCALE_TO_FIT, SCALE_TO_CROP)
RULE = "the face present in the most samples, a tie going to the larger median box"

PASS, BARS, FALLBACK, MANUAL, NO_FACE = "pass", "bars", "fallback", "manual", "no_face"
STATUSES = (PASS, BARS, FALLBACK, MANUAL, NO_FACE)


class ReframeError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Geometry (pure). Boxes and areas are (x0, y0, x1, y1) in source pixels.
# ---------------------------------------------------------------------------

def fit_factor(src, out, scaling=SCALE_TO_FIT):
    """Output pixels per source pixel before any zoom: how Resolve scales a
    source whose size differs from the timeline's."""
    (sw, sh), (w, h) = src, out
    if scaling == SCALE_TO_FIT:
        return min(w / float(sw), h / float(sh))
    if scaling == SCALE_TO_CROP:
        return max(w / float(sw), h / float(sh))
    raise ReframeError(f"input scaling '{scaling}': the transform is known for "
                       f"{' and '.join(SCALINGS)} only")


def fill_scale(out, area):
    """The smallest scale at which the picture area covers the whole output
    frame: the widest crop with no bars. Rounded up at the fourth decimal,
    so the rounding never opens a bar."""
    w, h = out
    s = max(w / float(area[2] - area[0]), h / float(area[3] - area[1]))
    return math.ceil(s * 1e4 - 1e-6) / 1e4


def place(face, out, scale, area):
    """The crop centre: the face centre clamped so the crop window stays in
    the picture area; on an axis where the window is larger than the area,
    the area's middle (and the crop leaves bars on that axis)."""
    centre = []
    for axis in (0, 1):
        half = out[axis] / (2.0 * scale)
        lo, hi = area[axis] + half, area[axis + 2] - half
        mid = (area[axis] + area[axis + 2]) / 2.0
        centre.append(mid if lo > hi else min(max(face[axis], lo), hi))
    return tuple(centre)


def extent_centre(boxes, margin=MARGIN):
    """The middle of the extent of [(t, box)], each box grown by `margin`
    of its size on each side; None when there are no boxes."""
    if not boxes:
        return None
    grown = [(b[0] - margin * (b[2] - b[0]), b[1] - margin * (b[3] - b[1]),
              b[2] + margin * (b[2] - b[0]), b[3] + margin * (b[3] - b[1])) for _, b in boxes]
    return ((min(g[0] for g in grown) + max(g[2] for g in grown)) / 2.0,
            (min(g[1] for g in grown) + max(g[3] for g in grown)) / 2.0)


def to_output(point, out, scale, centre):
    """Where a source point lands in the output frame."""
    return (out[0] / 2.0 + (point[0] - centre[0]) * scale,
            out[1] / 2.0 + (point[1] - centre[1]) * scale)


def coverage(out, scale, centre, area):
    """(columns, rows) of the output frame the picture area covers."""
    x0, y0 = to_output(area[:2], out, scale, centre)
    x1, y1 = to_output(area[2:], out, scale, centre)
    return (max(0.0, min(out[0], x1) - max(0.0, x0)), max(0.0, min(out[1], y1) - max(0.0, y0)))


def fills(out, scale, centre, area):
    cols, rows = coverage(out, scale, centre, area)
    return cols >= out[0] - FILL_TOLERANCE_PX and rows >= out[1] - FILL_TOLERANCE_PX


def bars_text(out, scale, centre, area):
    """'' when the picture fills the frame, else how much of it the picture
    covers ('the picture covers 810 of 1920 rows')."""
    cols, rows = coverage(out, scale, centre, area)
    parts = []
    if rows < out[1] - FILL_TOLERANCE_PX:
        parts.append(f"{rows:.0f} of {out[1]} rows")
    if cols < out[0] - FILL_TOLERANCE_PX:
        parts.append(f"{cols:.0f} of {out[0]} columns")
    return ("the picture covers " + " and ".join(parts)) if parts else ""


def slack(box, out, scale, centre, margin=MARGIN):
    """Output pixels between the face box, grown by `margin` of its size on
    each side, and the nearest edge of the output frame; negative when the
    grown box crosses that edge by so much."""
    x0, y0, x1, y1 = box
    mx, my = margin * (x1 - x0), margin * (y1 - y0)
    a = to_output((x0 - mx, y0 - my), out, scale, centre)
    b = to_output((x1 + mx, y1 + my), out, scale, centre)
    return min(a[0], a[1], out[0] - b[0], out[1] - b[1])


def check(boxes, out, scale, centre, margin=MARGIN, samples=None):
    """The inside-crop check over [(t, box)] of a span that sampled
    `samples` frames (default: one per box): {inside, checked, unchecked,
    total, share, worst_t, worst_px}. A sample with no box is unchecked and
    never counts as inside; share is inside over all samples. worst_px is
    the smallest slack (negative: outside)."""
    rows = [(t, slack(b, out, scale, centre, margin)) for t, b in boxes]
    total = len(rows) if samples is None else int(samples)
    if not rows:
        return {"inside": 0, "checked": 0, "unchecked": total, "total": total,
                "share": 0.0 if total else None, "worst_t": None, "worst_px": None}
    inside = sum(1 for _, s in rows if s >= -1e-6)
    t, worst = min(rows, key=lambda r: r[1])
    return {"inside": inside, "checked": len(rows), "unchecked": max(0, total - len(rows)),
            "total": total, "share": round(inside / float(total), 3), "worst_t": t,
            "worst_px": round(worst, 1)}


def held(c):
    """Every checked box inside, and at least one checked."""
    return bool(c["checked"]) and c["inside"] == c["checked"]


def resolve_props(src, out, scale, centre, scaling=SCALE_TO_FIT):
    """ZoomX, ZoomY, Pan and Tilt for a crop of `scale` output pixels per
    source pixel centred on source point `centre`."""
    zoom = scale / fit_factor(src, out, scaling)
    return {"ZoomX": zoom, "ZoomY": zoom, "Pan": (src[0] / 2.0 - centre[0]) * scale,
            "Tilt": (centre[1] - src[1] / 2.0) * scale}


# ---------------------------------------------------------------------------
# Faces across samples (pure)
# ---------------------------------------------------------------------------

def box_px(face, src):
    """A Vision box (normalized, top-left origin) in source pixels."""
    sw, sh = src
    return (face["x"] * sw, face["y"] * sh, (face["x"] + face["w"]) * sw,
            (face["y"] + face["h"]) * sh)


def _centre(b):
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def link(samples):
    """Tracks from [(t, [box])] in time order: each box joins the track
    whose latest box it overlaps best (IoU at least LINK_IOU, or centres
    within LINK_DISTANCE box widths), at most one box per track per sample;
    a box that joins none starts a track. Returns [[(t, box)]]."""
    tracks = []
    for t, boxes in samples:
        pairs = []
        for i, b in enumerate(boxes):
            for j, tr in enumerate(tracks):
                last = tr[-1][1]
                (bx, by), (lx, ly) = _centre(b), _centre(last)
                dist = math.hypot(bx - lx, by - ly)
                score = iou(b, last)
                if score >= LINK_IOU or dist <= LINK_DISTANCE * max(b[2] - b[0], last[2] - last[0]):
                    pairs.append((score, -dist, i, j))
        used_b, used_t = set(), set()
        for _, _, i, j in sorted(pairs, reverse=True):
            if i not in used_b and j not in used_t:
                tracks[j].append((t, boxes[i]))
                used_b.add(i)
                used_t.add(j)
        tracks += [[(t, b)] for i, b in enumerate(boxes) if i not in used_b]
    return tracks


def track_stats(track):
    """Median centre, size and area of a track, and how many samples it is in."""
    boxes = [b for _, b in track]
    return {"samples": len(track),
            "x": statistics.median(_centre(b)[0] for b in boxes),
            "y": statistics.median(_centre(b)[1] for b in boxes),
            "w": statistics.median(b[2] - b[0] for b in boxes),
            "h": statistics.median(b[3] - b[1] for b in boxes),
            "area": statistics.median((b[2] - b[0]) * (b[3] - b[1]) for b in boxes)}


def eligible(tracks):
    """Indices of the tracks in at least half as many samples as the most
    present one: the faces that count as someone in the span."""
    most = max((len(t) for t in tracks), default=0)
    return [i for i, t in enumerate(tracks) if 2 * len(t) >= most]


def choose(tracks, speaker_x=None):
    """(index of the primary track, the rule used, a review note or '')."""
    if not tracks:
        return None, "", ""
    stats = [track_stats(t) for t in tracks]
    order = sorted(range(len(tracks)), key=lambda i: (-stats[i]["samples"], -stats[i]["area"]))
    if speaker_x is not None:
        ok = set(eligible(tracks))
        eligible_ = [i for i in order if i in ok]
        pick = min(eligible_, key=lambda i: abs(stats[i]["x"] - speaker_x))
        rule = (f"the face nearest speaker_x {speaker_x:g}, among faces in at least half as "
                "many samples as the most present")
        off = abs(stats[pick]["x"] - speaker_x)
        note = (f"the nearest face is {off:.0f} px from speaker_x {speaker_x:g}"
                if off > stats[pick]["w"] else "")
        return pick, rule, note
    pick = order[0]
    rivals = set(eligible(tracks))
    for i in order[1:]:
        if i in rivals and stats[i]["area"] >= AMBIGUOUS_AREA * stats[pick]["area"]:
            return pick, RULE, (
                f"two faces: the chosen one at x {stats[pick]['x']:.0f} (in "
                f"{stats[pick]['samples']} samples) and another at x {stats[i]['x']:.0f} (in "
                f"{stats[i]['samples']}, at {stats[i]['area'] / stats[pick]['area']:.0%} of its "
                "size); name the speaker with speaker_x")
    return pick, RULE, ""


def off_track(tracks, pick):
    """[(t, box)] to check in place of the primary face on the samples it
    is missing from: on each, the box nearest the primary's path (its
    nearest sampled centre) among tracks that are not eligible, when it
    is within NEAR_PATH face widths. A face another eligible track holds
    is someone else's and is never taken."""
    primary = tracks[pick]
    have = {t for t, _ in primary}
    centres = [_centre(b) for _, b in primary]
    width = statistics.median(b[2] - b[0] for _, b in primary)
    rivals = set(eligible(tracks))
    best = {}
    for j, tr in enumerate(tracks):
        if j == pick or j in rivals:
            continue
        for t, b in tr:
            if t in have:
                continue
            cx, cy = _centre(b)
            d = min(math.hypot(cx - x, cy - y) for x, y in centres)
            if d <= NEAR_PATH * max(width, b[2] - b[0]) and (t not in best or d < best[t][0]):
                best[t] = (d, b)
    return sorted((t, b) for t, (_, b) in best.items())


def _times(ts):
    return ", ".join(f"{t:g}" for t in ts) + " s"


# ---------------------------------------------------------------------------
# One span (pure: faces and the picture area in, crops out)
# ---------------------------------------------------------------------------

def plan_aspect(boxes, face, src, area, aspect, scale=DEFAULT_SCALE, margin=MARGIN,
                allow_bars=False, samples=None):
    """The crop for one aspect: try `scale`, then the widest scale that
    fills the frame. boxes: [(t, box)] to check (the primary face's, and
    any taken in its place); face: the primary's (x, y) median centre;
    samples: how many frames the span sampled (default len(boxes)). With
    allow_bars, the default scale is kept when it holds the face even
    though the picture does not fill the frame (status BARS, a named
    choice). At each scale the median centre is tried first, then, when it
    loses the face, the middle of the boxes' extent. Returns {aspect,
    status, reason, centre, scale, x, y, fills, bars, check, props,
    tried}; centre is "median" or "extent". Centre and scale are rounded
    before the check, so the check holds for the values written."""
    out = ASPECTS[aspect]
    widest = fill_scale(out, area)
    candidates = [("default", round(float(scale), 4))]
    if abs(widest - scale) > 1e-4:
        candidates.append(("widest filled", round(widest, 4)))
    points = [("median", face)]
    ext = extent_centre(boxes, margin)
    if ext is not None:
        points.append(("extent", ext))
    tried = []
    for label, s in candidates:
        for how, point in points:
            centre = tuple(round(v, 1) for v in place(point, out, s, area))
            if how != "median" and (tried[-1]["x"], tried[-1]["y"]) == centre:
                continue  # the extent's middle is where the median already put the crop
            c = check(boxes, out, s, centre, margin, samples)
            tried.append({"label": label, "centre": how, "scale": s, "x": centre[0],
                          "y": centre[1], "bars": bars_text(out, s, centre, area), "check": c})
            if held(c):
                break
        last = tried[-1]
        if held(last["check"]) and (not last["bars"] or (allow_bars and label == "default")):
            break
    last = tried[-1]
    ok = held(last["check"]) and (not last["bars"] or allow_bars)
    first = [t for t in tried if t["label"] == "default"][-1]
    face_there = _held_text(first["check"])
    why_not_default = (f"at the default scale {first['scale']:g} " +
                       (f"{first['bars']} ({face_there})" if first["bars"] else face_there))
    if first["centre"] == "extent":
        why_not_default += " (centred on the middle of the face's extent)"
    result = {"aspect": aspect, "centre": last["centre"], "scale": last["scale"], "x": last["x"],
              "y": last["y"], "fills": not last["bars"], "bars": last["bars"],
              "check": last["check"], "tried": tried}
    moved = ""
    if last["centre"] == "extent":
        m = [t for t in tried if t["label"] == last["label"] and t["centre"] == "median"][0]
        moved = (f"centred on the middle of the face's extent (x {last['x']:g}, y "
                 f"{last['y']:g}): the median centre (x {m['x']:g}) loses the face on "
                 f"{m['check']['checked'] - m['check']['inside']} of "
                 f"{m['check']['checked']} checked samples")
    if ok and last["bars"]:
        result.update(status=BARS, reason=f"{why_not_default}; kept with bars (allow_bars)")
    elif ok and last["label"] == "default":
        result.update(status=PASS, reason=moved)
    elif ok:
        result.update(status=FALLBACK, reason=f"{why_not_default}; the widest filled scale "
                      f"{last['scale']:g} holds the face" + (f", {moved}" if moved else ""))
    else:
        result.update(status=MANUAL, reason=(
            f"{why_not_default}" + ("" if last["label"] == "default" else
            f"; at the widest filled scale {last['scale']:g} {_held_text(last['check'])}") +
            ("; neither the median centre nor the middle of the face's extent holds it"
             if ext is not None else "") + ": needs a manual reframe or a split"))
    result["props"] = {k: round(v, 4) for k, v in
                       resolve_props(src, out, result["scale"], (result["x"], result["y"])).items()}
    return result


def _held_text(c):
    """How a check went, in words."""
    if not c["checked"]:
        return "no face is checked on any sample"
    where = (f"all {c['total']} samples" if not c["unchecked"] else
             f"all {c['checked']} checked samples of {c['total']}")
    if c["inside"] == c["checked"]:
        return f"the face stays inside on {where}"
    return (f"the face leaves the crop on {c['checked'] - c['inside']} of {c['checked']} checked "
            f"samples (worst {c['worst_px']} px at {c['worst_t']} s)")


def no_crop(samples, aspects, reason, area=None):
    """A span's result with no crop for any aspect (status no_face), and
    why."""
    return {"samples": samples, "face_samples": 0, "area": list(area) if area else None,
            "face": None, "rule": "", "review": [], "tracks": 0, "track": [], "off_track": [],
            "unchecked": [], "aspects": {a: {"aspect": a, "status": NO_FACE, "reason": reason}
                                         for a in aspects}}


def plan_span(samples, src, area, aspects, scale=DEFAULT_SCALE, speaker_x=None, margin=MARGIN,
              allow_bars=False):
    """samples: [(t, [Vision box])] for one span; area: the picture area or
    None (every sample black). Returns {samples, face_samples, area, face,
    rule, review, tracks, track, off_track, unchecked, aspects: {aspect:
    plan_aspect(...) or a no_face result}}. Every sample is judged: the
    primary face's box, else a box taken in its place (off_track), else
    the sample is unchecked; either of the last two flags the span."""
    tracks = link([(t, [box_px(f, src) for f in faces]) for t, faces in samples])
    pick, rule, note = choose(tracks, speaker_x)
    if area is None or pick is None:
        out = no_crop(len(samples), aspects, "every sampled frame is black" if area is None else
                      f"no face in any of the {len(samples)} sampled frames", area)
        out["tracks"] = len(tracks)
        return out
    out = no_crop(len(samples), (), "", area)
    out["tracks"] = len(tracks)
    primary = tracks[pick]
    st = track_stats(primary)
    stray = off_track(tracks, pick)
    have = {t for t, _ in primary} | {t for t, _ in stray}
    unchecked = [t for t, _ in samples if t not in have]
    out.update(face_samples=len(primary), rule=rule,
               face={k: round(st[k], 1) for k in ("x", "y", "w", "h")},
               track=[[t] + [round(v, 1) for v in b] for t, b in primary],
               off_track=[[t] + [round(v, 1) for v in b] for t, b in stray],
               unchecked=unchecked)
    if note:
        out["review"].append(note)
    if stray:
        out["review"].append(
            f"the face is off its track at {_times(t for t, _ in stray)} (x " +
            ", ".join(f"{_centre(b)[0]:.0f}" for _, b in stray) +
            "); taken as the speaker's there and checked")
    if unchecked:
        out["review"].append(f"no face is checked at {_times(unchecked)} ({len(unchecked)} of "
                             f"{len(samples)} samples): the face may leave the crop there")
    boxes = sorted(primary + stray, key=lambda r: r[0])
    for a in aspects:
        out["aspects"][a] = plan_aspect(boxes, (st["x"], st["y"]), src, area, a, scale, margin,
                                        allow_bars, samples=len(samples))
    return out


# ---------------------------------------------------------------------------
# Reading the source
# ---------------------------------------------------------------------------

def span_times(a, b, samples=DEFAULT_SAMPLES, per_second=MIN_PER_SECOND):
    """Sample times across [a, b): max(samples, per_second * length),
    capped at MAX_SAMPLES, each in the middle of its slice."""
    n = max(1, int(samples), int(math.ceil((b - a) * per_second - 1e-9)))
    n = min(n, MAX_SAMPLES)
    return [round(a + (i + 0.5) * (b - a) / n, 3) for i in range(n)]


def _rotation(stream):
    # As measure.rotation: ffprobe's display matrix side data, or the old tag.
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                return int(round(float(sd["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    try:
        return int((stream.get("tags") or {}).get("rotate", 0)) % 360
    except (TypeError, ValueError):
        return 0


def probe(path):
    """(width, height) as displayed (ffmpeg rotates on decode) and duration."""
    try:
        data, err = run_ffprobe(path)
    except DetectError as e:
        raise ReframeError(str(e))
    streams = [s for s in (data or {}).get("streams") or [] if s.get("codec_type") == "video"]
    if not streams:
        raise ReframeError(f"no video stream in {path}: {err or 'ffprobe found none'}")
    s = streams[0]
    w, h = int(s["width"]), int(s["height"])
    if _rotation(s) % 180 == 90:
        w, h = h, w
    try:
        duration = float((data.get("format") or {}).get("duration") or 0)
    except ValueError:
        duration = 0.0
    return {"width": w, "height": h, "duration": duration}


class SourceReader:
    """Frames of one source: read(times, folder) -> [(t, png path, luma)],
    luma being width*height bytes of 8-bit grey, one ffmpeg call a frame."""

    def __init__(self, path):
        info = probe(path)
        self.path, self.size, self.duration = path, (info["width"], info["height"]), info["duration"]

    def read(self, times, folder):
        out = []
        for t in times:
            png = os.path.join(folder, f"t{t:012.3f}.png")
            cmd = [FFMPEG, "-v", "error", "-ss", f"{t:.3f}", "-i", "file:" + self.path,
                   "-an", "-frames:v", "1", "-y", png,
                   "-an", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
            try:
                proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, timeout=TIMEOUT_S)
            except subprocess.TimeoutExpired:
                raise ReframeError(f"ffmpeg timed out reading the frame at {t:.3f}s")
            except OSError as e:
                raise ReframeError(f"cannot run ffmpeg: {e}")
            want = self.size[0] * self.size[1]
            if proc.returncode != 0 or len(proc.stdout) < want or not os.path.exists(png):
                raise ReframeError(f"ffmpeg could not read the frame at {t:.3f}s: "
                                   f"{proc.stderr.decode(errors='replace').strip()[-200:]}")
            out.append((t, png, proc.stdout[:want]))
        return out


def picture_area(lumas, size, level=ACTIVE_LEVEL):
    """(x0, y0, x1, y1): the bounding box of the rows and columns whose mean
    luma is above `level` in at least one frame; None when every frame is
    black."""
    w, h = size
    rows, cols = [False] * h, [False] * w
    for buf in lumas:
        for r in range(h):
            if not rows[r] and sum(buf[r * w:(r + 1) * w]) > level * w:
                rows[r] = True
        for c in range(w):
            if not cols[c] and sum(buf[c::w]) > level * h:
                cols[c] = True
    if not any(rows) or not any(cols):
        return None
    y0, y1 = rows.index(True), h - rows[::-1].index(True)
    x0, x1 = cols.index(True), w - cols[::-1].index(True)
    return (x0, y0, x1, y1)


# ---------------------------------------------------------------------------
# A manifest
# ---------------------------------------------------------------------------

def plan(manifest, aspects=tuple(ASPECTS), samples=DEFAULT_SAMPLES, per_second=MIN_PER_SECOND,
         speaker_x=None, only=None, allow_bars=False, reader=None, detect=None, check_cancel=None,
         progress=None):
    """Plan every span of the manifest's clips (or only those named).
    reader: a SourceReader (default: the manifest's source); detect:
    images -> {path: [Vision box]} or None (default: vision.face_boxes).
    Sample times at or past the source's end (reader.duration, when it
    knows one) are not read; the span says so, and a span wholly past the
    end gets no crop.
    Returns the report: {source, size, aspects, samples, per_second,
    speaker_x, rule, margin, spans: [...], rows: [...], counts}."""
    unknown = [a for a in aspects if a not in ASPECTS]
    if unknown or not aspects:
        raise ReframeError(f"aspects must be among {', '.join(ASPECTS)} (got {', '.join(unknown) or 'none'})")
    clips = manifest["clips"]
    if only:
        missing = set(only) - {c["name"] for c in clips}
        if missing:
            raise ReframeError("no such clip(s): " + ", ".join(sorted(missing)))
        clips = [c for c in clips if c["name"] in set(only)]
    reader = reader or SourceReader(manifest["source_path"])
    detect = detect or vision.face_boxes
    src = reader.size
    end = float(getattr(reader, "duration", 0) or 0)
    total = sum(len(c["spans"]) for c in clips)
    report = {"source": manifest["source_path"], "size": list(src), "aspects": list(aspects),
              "samples": samples, "per_second": per_second, "speaker_x": speaker_x,
              "rule": RULE if speaker_x is None else f"nearest speaker_x {speaker_x:g}",
              "margin": MARGIN, "allow_bars": bool(allow_bars), "spans": []}
    done = 0
    with tempfile.TemporaryDirectory(prefix="rpresolve-reframe-") as tmp:
        for clip in clips:
            scale = float((clip.get("reframe") or {}).get("scale", DEFAULT_SCALE))
            hand_x = (clip.get("reframe") or {}).get("face_x")
            for i, s in enumerate(clip["spans"], 1):
                if check_cancel:
                    check_cancel()
                folder = os.path.join(tmp, f"{done:04d}")
                os.makedirs(folder)
                times = span_times(s["in"], s["out"], samples, per_second)
                past = [t for t in times if end and t >= end]
                times = [t for t in times if t not in past]
                frames = reader.read(times, folder) if times else []
                if frames:
                    boxes = detect([png for _, png, _ in frames])
                    if boxes is None:
                        raise ReframeError("the macOS Vision helper could not be built or run, "
                                           "so no face was found and no crop is planned")
                    area = picture_area([luma for _, _, luma in frames], src)
                    sp = plan_span([(t, boxes.get(png, [])) for t, png, _ in frames], src, area,
                                   aspects, scale, speaker_x, allow_bars=allow_bars)
                else:
                    sp = no_crop(0, aspects, f"the span lies past the source's end ({end:g} s): "
                                 "no frame to read")
                if past:
                    sp["review"].append(f"the span runs past the source's end ({end:g} s): "
                                        f"{len(past)} of {len(past) + len(times)} sample times "
                                        "were not read")
                sp.update({"clip": clip["name"], "span": i, "in": s["in"], "out": s["out"],
                           "hand_face_x": hand_x})
                report["spans"].append(sp)
                for png in [png for _, png, _ in frames]:
                    os.remove(png)
                done += 1
                if progress:
                    progress(done, total)
    report["rows"] = rows(report)
    report["counts"] = counts(report["rows"])
    return report


def entry(aspect_plan, src, area, face, review):
    """The span's reframe[aspect] entry for the manifest copy. A crop that
    cannot hold the face carries its status and reason and no centre, so
    cut refuses that version; nothing is guessed."""
    p = aspect_plan
    if p["status"] not in (PASS, BARS, FALLBACK):
        return {"status": p["status"], "reason": p["reason"]}
    e = {"x": p["x"], "y": p["y"], "scale": p["scale"], "src": list(src), "area": list(area),
         "face_x": face["x"], "face_y": face["y"], "centre": p["centre"], "status": p["status"],
         "inside": [p["check"]["inside"], p["check"]["total"]]}
    if p["bars"]:
        e["bars"] = p["bars"]
    if review:
        e["review"] = "; ".join(review)
    return e


def apply(manifest, report):
    """A copy of the manifest whose planned spans carry reframe[aspect]
    entries. Clip-level reframe (face_x) is kept as it was; cut uses a
    span's own entry first."""
    m = copy.deepcopy(manifest)
    by_clip = {c["name"]: c for c in m["clips"]}
    for sp in report["spans"]:
        span = by_clip[sp["clip"]]["spans"][sp["span"] - 1]
        rf = dict(span.get("reframe") or {})
        for a, p in sp["aspects"].items():
            rf[a] = entry(p, report["size"], sp["area"], sp["face"], sp["review"])
        span["reframe"] = rf
    m["reframe_plan"] = {k: report[k] for k in ("size", "aspects", "samples", "per_second",
                                                "speaker_x", "rule", "margin", "allow_bars")}
    return m


COLUMNS = ("clip", "span", "in", "out", "aspect", "status", "review", "samples", "face_samples",
           "face_x", "face_y", "hand_face_x", "dx", "centre", "scale", "zoom", "pan", "tilt",
           "fills", "inside", "unchecked", "share", "worst_t", "worst_px", "reason")


TILT_NOTE = ("Tilt {tilt:g} assumes Resolve's y axis points up, which no render has confirmed "
             "yet: check a still before trusting this crop")


def rows(report):
    """One row per span and aspect. A crop that sets a Tilt carries
    TILT_NOTE in its reason."""
    out = []
    for sp in report["spans"]:
        face = sp["face"] or {}
        for a, p in sp["aspects"].items():
            c = p.get("check") or {}
            props = p.get("props") or {}
            dx = (round(face["x"] - sp["hand_face_x"], 1)
                  if face and sp.get("hand_face_x") is not None else None)
            reason = p.get("reason", "")
            if p["status"] in (PASS, BARS, FALLBACK) and abs(props.get("Tilt") or 0.0) > 1e-6:
                reason = "; ".join(x for x in (reason, TILT_NOTE.format(tilt=props["Tilt"])) if x)
            out.append({"clip": sp["clip"], "span": sp["span"], "in": sp["in"], "out": sp["out"],
                        "aspect": a, "status": p["status"], "review": "; ".join(sp["review"]),
                        "samples": sp["samples"], "face_samples": sp["face_samples"],
                        "face_x": face.get("x"), "face_y": face.get("y"),
                        "hand_face_x": sp.get("hand_face_x"), "dx": dx,
                        "centre": p.get("centre"), "scale": p.get("scale"),
                        "zoom": props.get("ZoomX"), "pan": props.get("Pan"),
                        "tilt": props.get("Tilt"), "fills": p.get("fills"),
                        "inside": c.get("inside"), "unchecked": c.get("unchecked"),
                        "share": c.get("share"), "worst_t": c.get("worst_t"),
                        "worst_px": c.get("worst_px"), "reason": reason})
    return out


def counts(rows):
    n = {s: 0 for s in STATUSES}
    for r in rows:
        n[r["status"]] += 1
    n["review"] = sum(1 for r in rows if r["review"])
    n["tilt"] = sum(1 for r in rows if r["status"] in (PASS, BARS, FALLBACK)
                    and abs(r["tilt"] or 0.0) > 1e-6)
    return n


def needs_person(rows):
    """Rows a person must look at: no crop, a crop kept with bars, or a
    flagged choice of face."""
    return [r for r in rows if r["status"] in (MANUAL, NO_FACE, BARS) or r["review"]]


def tsv(rows_):
    def cell(v):
        return "" if v is None else " ".join(str(v).split())
    return "\n".join(["\t".join(COLUMNS)] +
                     ["\t".join(cell(r[k]) for k in COLUMNS) for r in rows_]) + "\n"


def summary(report):
    n = report["counts"]
    return (f"reframe-plan: {len(report['rows'])} crop(s) over {len(report['spans'])} span(s): "
            f"{n[PASS]} pass, {n[FALLBACK]} fallback (widest filled scale), {n[MANUAL]} manual, "
            f"{n[NO_FACE]} no face" + (f", {n[BARS]} kept with bars" if n[BARS] else "") +
            (f"; {n['review']} flagged for review" if n["review"] else "") +
            (f"; {n['tilt']} set a Tilt, whose sign no render has confirmed yet (check a still)"
             if n.get("tilt") else ""))


def line(r):
    """One row as the line the CLI prints."""
    parts = [f"{r['status'].upper():8} {r['clip']:<14} span {r['span']} {r['aspect']:<5}"]
    if r["face_x"] is not None:
        parts.append(f"face x {r['face_x']:.0f}" +
                     (f" (hand {r['hand_face_x']:g}, {r['dx']:+.0f})" if r["dx"] is not None else ""))
    if r["scale"] is not None and r["status"] in (PASS, BARS, FALLBACK):
        parts.append(f"scale {r['scale']:g} zoom {r['zoom']:g} pan {r['pan']:g} tilt {r['tilt']:g}")
    if r["inside"] is not None:
        parts.append(f"inside {r['inside']}/{r['samples']}" +
                     (f" ({r['unchecked']} unchecked)" if r["unchecked"] else "") +
                     f" worst {r['worst_px']} px")
    text = "  ".join(parts)
    if r["reason"]:
        text += f"  | {r['reason']}"
    if r["review"]:
        text += f"  | REVIEW: {r['review']}"
    return text


def write_report(path_stem, report, write):
    """Write <stem>.tsv and <stem>.json with `write(path, text)`; returns the paths."""
    tsv_path, json_path = path_stem + ".tsv", path_stem + ".json"
    write(tsv_path, tsv(report["rows"]))
    write(json_path, json.dumps(report, indent=1, default=str) + "\n")
    return tsv_path, json_path
