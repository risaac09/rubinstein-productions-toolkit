"""
rpresolve.reframe: a static crop per span for any delivery shape (the 9:16,
1:1, 4:5 and 16:9 versions of a cut, or the master timeline's own shape),
centred on the subject's face or, for the master, left as shot unless a
head needs room, and the check that the whole head stays inside it on every
sampled frame. plan_items frames a timeline's clips for reframe_apply. Offline: ffmpeg reads frames from the
source and macOS Vision finds faces (vision.py); nothing touches Resolve.
Stdlib only.

For each span of each clip:
  1. Frames are sampled evenly across [in, out): `samples` per span
     (default 8) and at least `per_second` a second (default 0.5), at
     in + (i + 0.5) * length / n, never more than MAX_SAMPLES.
  2. The picture area: the frame less its black bars. A bar is a row or
     column at the edge of the frame whose brightest pixel is at or below
     BAR_LEVEL (40 of 255) in every sample, so it is flat and black; a dark
     scene (a night stage) has specks and texture above that and keeps its
     whole frame. A call recording that letterboxes its panes inside the
     16:9 frame has a picture area smaller than the frame, and a crop that
     leaves it shows black bars. Mean luma is not used: it cannot tell a
     bar from a dark room.
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
     The subject can be steered where the footage has no single speaker
     (a stage with an audience): subject_region keeps only faces whose
     centre is inside a source-pixel rectangle, min_face_height drops faces
     smaller than a share of the frame height (the audience), speaker_x with
     speaker_y names a point, and prefer="largest" ranks eligible faces by
     size before presence. What was ignored is counted in the report.
     Every sample is judged: one where the primary face is missing has
     the box nearest its path checked in its place, when that box is
     within NEAR_PATH face widths and no other eligible track holds it (a
     lean that jumps further than LINK_DISTANCE starts a track of its
     own); else the sample is unchecked. Either way the span is flagged.
  4. The crop: centred on the median face centre (for the master shape,
     first on the frame's middle: centre fill, the shot as shot), lifted so
     the head (HEAD_ABOVE face heights above Vision's box) and HEADROOM
     face heights of room above it stay in (lift(); never lowered), then
     clamped so the crop stays inside the picture area, at `scale` output
     pixels per source pixel (cut.py's convention). The scale is the clip's reframe.scale; with none
     it is AUTO, the widest scale that fills the frame for the source and the
     target aspect (0.7257 for a 5952x2646 source into 9:16, a zoom of
     about 4). With allow_bars and no scale it is DEFAULT_SCALE, the Ep 002
     punch-in, which was calibrated on 720p sources and is far too tight on
     a wide high-resolution one.
     When that loses the face, the middle of the checked boxes' extent
     (each grown by MARGIN), clamped the same way, is tried at the same
     scale before any other: when any centre at a scale holds every box,
     that one does (to the 0.1 px the centre is rounded to). The result
     records which centre was used.
  5. The check: every checked box (the primary face's, and any taken in
     its place), grown by MARGIN of its size on each side and clipped to
     the picture area (a margin that does not exist in the frame cannot be
     required), is mapped through that exact transform and must sit inside
     the output frame. Reported over all the span's samples: inside,
     unchecked, the share inside, the worst sample and by how many output
     pixels. A crop passes only on enough evidence: at least
     MIN_CHECKED_SAMPLES samples and `min_checked` (default half) of the
     span's samples must have a checked face. Fewer and the span is manual,
     however well the few boxes sit: a face seen in one frame of fifteen
     says nothing about the other fourteen.
  6. The fallback: when the default scale leaves bars (the crop is larger
     than the picture area) or loses the face, the widest scale that still
     fills the frame is tried and checked the same way (the median, then
     the extent's middle). When that fails too, the span needs a manual
     reframe or a split. A span with no face in any sample says so and
     gets no centre; nothing is guessed.
     allow_bars keeps the default scale when it holds the face but leaves
     bars (the Ep 002 look); such a crop is status "bars" and named as such.

The transform, for Resolve's input scalings scaleToFit and scaleToCrop: the
source is fit into the timeline by fit = min(W/sw, H/sh) (scaleToCrop:
max), zoomed about the frame centre, then moved. A crop of s output pixels
per source pixel centred on source point (cx, cy) moves the picture by
    dx = (sw/2 - cx) * s       dy = (sh/2 - cy) * s      (output pixels, y down)
and a source point (x, y) lands at X = W/2 + (x - cx) s, Y = H/2 + (y - cy) s.
Resolve's Pan and Tilt are not output pixels: each is measured against the
fitted picture on its own axis, so a Pan of P moves the picture right by
P * (sw * fit) / W and a Tilt of T moves it up by T * (sh * fit) / H. Hence
    ZoomX = ZoomY = s / fit
    Pan  = dx * W / (sw * fit)
    Tilt = -dy * H / (sh * fit)
Pan and Tilt are plain output pixels only on the axis where the fitted
picture spans the timeline (a 16:9 source into 16:9; any source's width
into a 9:16 at scaleToFit, which is why Pan alone looked like pixels).
Confirmed 2026-10-15 on six fixtures exported from Resolve 21.1.1.10 (two
scalings; 1080x1920, 1080x1080, 1920x1080 and 5952x2646 timelines; UHD and
3:2 6K sources): the first run, made with Pan and Tilt as output pixels,
measured Tilt moving the picture by 0.315 to 1.496 of its value, each
within 0.8% of (sh * fit) / H, and Pan by 0.664 where (sw * fit) / W is
0.667; the second run, with this transform, landed every marker (see
TILT_CONFIRMED). Negative Tilt moves the picture down and shows the top of
the source.
"""

import copy
import json
import math
import os
import statistics
import subprocess
import tempfile

from . import cutlist, vision
from .detect import DetectError, run_ffprobe

FFMPEG = os.environ.get("RPRESOLVE_FFMPEG", "/opt/homebrew/bin/ffmpeg")
TIMEOUT_S = 120  # per ffmpeg frame grab

ASPECTS = {"9x16": (1080, 1920), "1x1": (1080, 1080), "4x5": (1080, 1350), "16x9": (1920, 1080)}
MASTER = "master"          # the project's own timeline shape (master_size), e.g. 5952x2646
# Where a crop starts before the face moves it: a version made for a platform is a crop
# on the subject; the master keeps the shot as shot (centre fill) and moves only for a head.
ANCHOR_FACE, ANCHOR_MIDDLE = "face", "middle"
HEAD_ABOVE = 0.6           # face heights from the top of Vision's face box (the brows) to the top of the head
HEADROOM = 0.65            # face heights wanted between the head's top and the frame's, at the median
DEFAULT_SCALE = 2.25       # output pixels per source pixel (the Ep 002 script's crop; 720p calibration)
AUTO = "auto"              # the widest scale that fills the frame, derived per source and aspect
DEFAULT_SAMPLES = 8
MIN_PER_SECOND = 0.5
MAX_SAMPLES = 120
MARGIN = 0.10              # of the face box's width and height, on each side
BAR_LEVEL = 40            # brightest pixel (0..255) at or below which an edge row or column is a black bar
EDGE_NOTE_PX = 1.0       # how far a face box may reach past the picture before the span is flagged
MIN_CHECKED_SHARE = 0.5   # of a span's samples that must have a checked face before a crop can pass
MIN_CHECKED_SAMPLES = 3   # and never fewer than this many
PREFER_PRESENT, PREFER_LARGEST = "present", "largest"
PREFERS = (PREFER_PRESENT, PREFER_LARGEST)
TILT_CONFIRMED = True     # tilt-check on six fixtures, 2026-10-15 (the module docstring)
TILT_NEGLIGIBLE_PX = 0.5  # a Tilt this small moves the picture less than a pixel: a wrong sign shows nothing
LINK_IOU = 0.2
LINK_DISTANCE = 0.75       # box widths between centres
AMBIGUOUS_AREA = 0.5       # another eligible face at this share of the primary's area: ambiguous
NEAR_PATH = 3.0            # face widths: a box this near the primary, held by no rival, is checked
FILL_TOLERANCE_PX = 0.5
STILL_SHAPE_TOLERANCE = 0.005   # a still's width/height may differ from the source's by this share
SCALE_TO_FIT, SCALE_TO_CROP = "scaleToFit", "scaleToCrop"
SCALINGS = (SCALE_TO_FIT, SCALE_TO_CROP)
RULE = "the face present in the most samples, a tie going to the larger median box"
RULE_LARGEST = "the largest face among those present in at least half as many samples as the most present"

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


def slack(box, out, scale, centre, margin=MARGIN, area=None, head_above=0.0):
    """Output pixels between the face box, grown by `margin` of its size on
    each side, and the nearest edge of the output frame; negative when the
    grown box crosses that edge by so much. With `area`, the grown box is
    clipped to the picture first: a face at the edge of the source has no
    margin beyond it, and no crop can include what the frame does not hold.
    head_above extends the box upward by that many face heights first, so
    the top of the head is checked, not the brows."""
    x0, y0, x1, y1 = box
    mx, my = margin * (x1 - x0), margin * (y1 - y0)
    gx0, gy0, gx1, gy1 = x0 - mx, y0 - my - head_above * (y1 - y0), x1 + mx, y1 + my
    clipped = [False] * 4
    if area is not None:
        # At or past the edge: a box that reaches exactly the picture's edge is held by
        # the same half-pixel tolerance as one clipped to it.
        if gx0 <= area[0]:
            gx0, clipped[0] = area[0], True
        if gy0 <= area[1]:
            gy0, clipped[1] = area[1], True
        if gx1 >= area[2]:
            gx1, clipped[2] = area[2], True
        if gy1 >= area[3]:
            gy1, clipped[3] = area[3], True
        if gx0 >= gx1 or gy0 >= gy1:   # nothing of the face is in the picture
            return -max(gx0 - gx1, gy0 - gy1, 1.0) * scale
    a = to_output((gx0, gy0), out, scale, centre)
    b = to_output((gx1, gy1), out, scale, centre)
    sides = [a[0], a[1], out[0] - b[0], out[1] - b[1]]
    # The fill scale is rounded up so no bar opens, which leaves the window a hair
    # smaller than the picture: a box clipped to the picture's edge may overshoot the
    # window's edge by that hair. Half a pixel is not a face leaving the crop.
    sides = [0.0 if c and -FILL_TOLERANCE_PX <= v < 0 else v for c, v in zip(clipped, sides)]
    return min(sides)


def needed(total, share=MIN_CHECKED_SHARE):
    """The fewest checked samples a crop needs before it can pass: `share` of
    the span's `total` samples and at least MIN_CHECKED_SAMPLES, never more
    than the span has."""
    if not total:
        return 0
    return min(int(total), max(MIN_CHECKED_SAMPLES, int(math.ceil(share * total - 1e-9))))


def check(boxes, out, scale, centre, margin=MARGIN, samples=None, area=None,
          min_checked=MIN_CHECKED_SHARE, head_above=0.0):
    """The inside-crop check over [(t, box)] of a span that sampled
    `samples` frames (default: one per box): {inside, checked, unchecked,
    total, need, share, worst_t, worst_px}. A sample with no box is unchecked
    and never counts as inside; share is inside over all samples. worst_px
    is the smallest slack (negative: outside). need is the fewest checked
    samples a crop must have to pass (see needed()); `area` clips each grown
    box to the picture (see slack())."""
    rows = [(t, slack(b, out, scale, centre, margin, area, head_above)) for t, b in boxes]
    total = len(rows) if samples is None else int(samples)
    need = needed(total, min_checked)
    if not rows:
        return {"inside": 0, "checked": 0, "unchecked": total, "total": total, "need": need,
                "share": 0.0 if total else None, "worst_t": None, "worst_px": None}
    inside = sum(1 for _, s in rows if s >= -1e-6)
    t, worst = min(rows, key=lambda r: r[1])
    return {"inside": inside, "checked": len(rows), "unchecked": max(0, total - len(rows)),
            "total": total, "need": need, "share": round(inside / float(total), 3), "worst_t": t,
            "worst_px": round(worst, 1)}


def held(c):
    """Every checked box inside, and enough of them checked: at least one,
    and the span's `need` (a face seen on one frame of fifteen holds nothing)."""
    return bool(c["checked"]) and c["inside"] == c["checked"] and c["checked"] >= c.get("need", 1)


def thin(c):
    """Every checked box sits inside but too few were checked to trust it."""
    return bool(c["checked"]) and c["inside"] == c["checked"] and c["checked"] < c.get("need", 1)


def shift_px(src, scale, centre):
    """(dx, dy): how far the picture moves in output pixels, y down, for a
    crop of `scale` centred on source point `centre`."""
    return ((src[0] / 2.0 - centre[0]) * scale, (src[1] / 2.0 - centre[1]) * scale)


def resolve_props(src, out, scale, centre, scaling=SCALE_TO_FIT):
    """ZoomX, ZoomY, Pan and Tilt for a crop of `scale` output pixels per
    source pixel centred on source point `centre`, in Resolve's units (the
    module docstring)."""
    fit = fit_factor(src, out, scaling)
    dx, dy = shift_px(src, scale, centre)
    return {"ZoomX": scale / fit, "ZoomY": scale / fit,
            "Pan": dx * out[0] / (src[0] * fit), "Tilt": -dy * out[1] / (src[1] * fit)}


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


def choose(tracks, speaker_x=None, speaker_y=None, prefer=PREFER_PRESENT):
    """(index of the primary track, the rule used, a review note or '').
    Without a hint the primary is the face present in the most samples, a
    tie going to the larger; prefer=PREFER_LARGEST ranks the eligible faces
    (see eligible()) by median area first, for footage where the subject is
    nearest the camera and the audience is not. speaker_x, with speaker_y
    when given, names a point: the eligible face nearest it."""
    if not tracks:
        return None, "", ""
    stats = [track_stats(t) for t in tracks]
    ok = set(eligible(tracks))
    if prefer == PREFER_LARGEST:
        order = sorted((i for i in range(len(tracks)) if i in ok),
                       key=lambda i: (-stats[i]["area"], -stats[i]["samples"]))
    else:
        order = sorted(range(len(tracks)), key=lambda i: (-stats[i]["samples"], -stats[i]["area"]))
    if speaker_x is not None:
        eligible_ = [i for i in order if i in ok]

        def away(i):
            dx = stats[i]["x"] - speaker_x
            dy = 0.0 if speaker_y is None else stats[i]["y"] - speaker_y
            return math.hypot(dx, dy)
        pick = min(eligible_, key=away)
        where = f"speaker_x {speaker_x:g}" + ("" if speaker_y is None else f", speaker_y {speaker_y:g}")
        rule = (f"the face nearest {where}, among faces in at least half as "
                "many samples as the most present")
        off = away(pick)
        note = (f"the nearest face is {off:.0f} px from {where}" if off > stats[pick]["w"] else "")
        return pick, rule, note
    pick = order[0]
    rule = RULE if prefer == PREFER_PRESENT else RULE_LARGEST
    for i in order[1:]:
        if i in ok and stats[i]["area"] >= AMBIGUOUS_AREA * stats[pick]["area"]:
            return pick, rule, (
                f"two faces: the chosen one at x {stats[pick]['x']:.0f} (in "
                f"{stats[pick]['samples']} samples) and another at x {stats[i]['x']:.0f} (in "
                f"{stats[i]['samples']}, at {stats[i]['area'] / stats[pick]['area']:.0%} of its "
                "size); name the speaker with speaker_x, or the subject with subject_region")
    return pick, rule, ""


def admit(b, src, region=None, min_face_height=None):
    """Whether a face box (source pixels) may be the subject: tall enough
    (min_face_height is a share of the frame height, to leave out an
    audience) and centred inside `region` (x0, y0, x1, y1 in source pixels)."""
    if min_face_height and (b[3] - b[1]) < min_face_height * src[1]:
        return False
    if region:
        cx, cy = _centre(b)
        if not (region[0] <= cx <= region[2] and region[1] <= cy <= region[3]):
            return False
    return True


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

def size_of(aspect, master_size=None):
    """(width, height) of a shape: one of ASPECTS, or MASTER with the
    timeline's size given."""
    if aspect == MASTER:
        try:
            w, h = (int(v) for v in master_size)
        except (TypeError, ValueError):
            raise ReframeError("the master shape needs master_size: the timeline's width and "
                               "height in pixels")
        if w <= 0 or h <= 0:
            raise ReframeError(f"master_size must be positive (got {w}x{h})")
        return (w, h)
    if aspect not in ASPECTS:
        raise ReframeError(f"shape '{aspect}': one of {', '.join(ASPECTS)} or {MASTER}")
    return ASPECTS[aspect]


def anchor_of(aspect):
    return ANCHOR_MIDDLE if aspect == MASTER else ANCHOR_FACE


def head_tops(boxes, head_above=HEAD_ABOVE):
    """The estimated top of the head over [(t, box)]: Vision's face box
    stops near the brows, so head_above face heights above its top."""
    return [b[1] - head_above * (b[3] - b[1]) for _, b in boxes]


def lift(centre, boxes, out, scale, margin=MARGIN, head_above=HEAD_ABOVE, headroom=HEADROOM):
    """The crop centre moved up, never down, until the window's top is at or
    above both every checked head top less its margin (the head must stay
    in) and the median head top less `headroom` median face heights (the
    room wanted above it). place() clamps it to the picture afterwards."""
    if not boxes:
        return centre
    hs = [b[3] - b[1] for _, b in boxes]
    tops = head_tops(boxes, head_above)
    hard = min(t - margin * h for t, h in zip(tops, hs))
    soft = statistics.median(tops) - headroom * statistics.median(hs)
    half = out[1] / (2.0 * scale)
    want = min(hard, soft)
    return (centre[0], want + half) if centre[1] - half > want else centre


def headroom_of(boxes, out, scale, y, area, head_above=HEAD_ABOVE, headroom=HEADROOM):
    """{head_top, headroom_px, want_px, room_px, cut, short}: the median
    head top (source px); the output pixels above it in a crop centred at
    row y; the output pixels wanted; how much picture there is above the
    head (output px at this scale; negative when the head reaches past the
    top of the picture); cut: the highest head top of any sample is above
    the picture, so no crop keeps the whole head; short: the crop gives
    less room than the picture holds and `headroom` wants (by more than a
    pixel). A shot framed tight by the camera is neither: nothing above
    the head was there to keep."""
    tops = head_tops(boxes, head_above)
    top, h = statistics.median(tops), statistics.median(b[3] - b[1] for _, b in boxes)
    have = (top - (y - out[1] / (2.0 * scale))) * scale
    want = headroom * h * scale
    room = (top - area[1]) * scale
    return {"head_top": round(top, 1), "headroom_px": round(have, 1), "want_px": round(want, 1),
            "room_px": round(room, 1), "cut": min(tops) < area[1] - EDGE_NOTE_PX,
            "short": have < min(want, room) - 1.0}


def move_of(src, out, scale, centre, scaling=SCALE_TO_FIT):
    """What the crop changes against the item as it sits at Zoom 1, Pan 0,
    Tilt 0 under the input scaling: 'none', or some of zoom, pan and tilt.
    The fill scale is rounded up at the fourth decimal, so a zoom within
    0.02% of 1 is none."""
    dx, dy = shift_px(src, scale, centre)
    zoom = scale / fit_factor(src, out, scaling)
    parts = [name for name, moved in (("zoom", abs(zoom - 1.0) > 2e-4),
                                      ("pan", abs(dx) >= 0.5), ("tilt", abs(dy) >= 0.5)) if moved]
    return "+".join(parts) or "none"


def plan_aspect(boxes, face, src, area, aspect, scale=DEFAULT_SCALE, margin=MARGIN,
                allow_bars=False, samples=None, min_checked=MIN_CHECKED_SHARE, out=None,
                anchor=ANCHOR_FACE, head_above=HEAD_ABOVE, headroom=HEADROOM,
                scaling=SCALE_TO_FIT):
    """The crop for one aspect: try `scale`, then the widest scale that
    fills the frame. boxes: [(t, box)] to check (the primary face's, and
    any taken in its place); face: the primary's (x, y) median centre;
    samples: how many frames the span sampled (default len(boxes)). scale
    may be AUTO: the widest scale that fills the frame is then the one
    candidate, and holding the face there is a pass, not a fallback.
    min_checked: the share of samples that must have a checked face (see
    needed()). With
    allow_bars, the default scale is kept when it holds the face even
    though the picture does not fill the frame (status BARS, a named
    choice). At each scale the median centre is tried first, then, when it
    loses the face, the middle of the boxes' extent. With anchor "middle"
    (the master shape) the frame's middle is tried first: the shot as shot.
    Every point is lifted (lift()) so the head and the room above it stay
    in, then clamped to the picture; the check covers the head (head_above).
    A crop that keeps the head with less room than `headroom` wants is a
    fallback that says so. Returns {aspect, status, reason, centre, scale,
    x, y, fills, bars, check, props, tried, out, head_top, headroom_px,
    want_px, move}; centre is "middle", "median" or "extent". Centre and
    scale are rounded before the check, so the check holds for the values
    written. out: the frame size (default ASPECTS[aspect])."""
    out = tuple(out) if out else ASPECTS[aspect]
    widest = fill_scale(out, area)
    auto = scale == AUTO
    if auto:
        scale = widest
    named = "the widest filled scale" if auto else "the default scale"
    candidates = [("default", round(float(scale), 4))]
    if abs(widest - scale) > 1e-4:
        candidates.append(("widest filled", round(widest, 4)))
    points = [("middle", (src[0] / 2.0, src[1] / 2.0))] if anchor == ANCHOR_MIDDLE else []
    points.append(("median", face))
    ext = extent_centre(boxes, margin)
    if ext is not None:
        points.append(("extent", ext))
    tried = []
    for label, s in candidates:
        for how, point in points:
            lifted = lift(point, boxes, out, s, margin, head_above, headroom)
            centre = tuple(round(v, 1) for v in place(lifted, out, s, area))
            if tried and tried[-1]["label"] == label and (tried[-1]["x"], tried[-1]["y"]) == centre:
                continue  # this point put the crop where an earlier one already did
            c = check(boxes, out, s, centre, margin, samples, area, min_checked, head_above)
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
    why_not_default = (f"at {named} {first['scale']:g} " +
                       (f"{first['bars']} ({face_there})" if first["bars"] else face_there))
    if first["centre"] != tried[0]["centre"]:
        why_not_default += (" (centred on the middle of the face's extent)" if first["centre"] ==
                            "extent" else " (centred on the median face centre)")
    result = {"aspect": aspect, "centre": last["centre"], "scale": last["scale"], "x": last["x"],
              "y": last["y"], "fills": not last["bars"], "bars": last["bars"],
              "check": last["check"], "tried": tried}
    moved = ""
    firsts = [t for t in tried if t["label"] == last["label"]]
    if last["centre"] != firsts[0]["centre"]:
        m = firsts[0]
        what = {"median": "the median face centre", "middle": "the frame's middle"}[m["centre"]]
        where = ("the middle of the face's extent" if last["centre"] == "extent" else
                 "the median face centre")
        moved = (f"centred on {where} (x {last['x']:g}, y {last['y']:g}): {what} (x "
                 f"{m['x']:g}) loses the face on {m['check']['checked'] - m['check']['inside']} "
                 f"of {m['check']['checked']} checked samples")
    if ok and last["bars"]:
        result.update(status=BARS, reason=f"{why_not_default}; kept with bars (allow_bars)")
    elif ok and last["label"] == "default":
        result.update(status=PASS, reason=moved)
    elif ok:
        result.update(status=FALLBACK, reason=f"{why_not_default}; the widest filled scale "
                      f"{last['scale']:g} holds the face" + (f", {moved}" if moved else ""))
    elif thin(last["check"]):
        c = last["check"]
        result.update(status=MANUAL, reason=(
            f"the face is checked on only {c['checked']} of {c['total']} samples (at least "
            f"{c['need']} needed), so no crop can be trusted: sample more (--samples), name the "
            "subject (subject_region or speaker_x), or reframe by hand"))
    else:
        result.update(status=MANUAL, reason=(
            f"{why_not_default}" + ("" if last["label"] == "default" else
            f"; at the widest filled scale {last['scale']:g} {_held_text(last['check'])}") +
            ("; neither the median centre nor the middle of the face's extent holds it"
             if ext is not None else "") + ": needs a manual reframe or a split"))
    result["out"] = list(out)
    if result["status"] in (PASS, BARS, FALLBACK):
        room = headroom_of(boxes, out, result["scale"], result["y"], area, head_above, headroom)
        result.update({k: room[k] for k in ("head_top", "headroom_px", "want_px")})
        why, note = "", ""
        if room["cut"]:
            why = ("the subject is at the top edge of the picture: the head reaches past it, "
                   "so no crop keeps the whole head")
        elif room["short"]:
            why = (f"{room['headroom_px']:.0f} px above the head where "
                   f"{min(room['want_px'], room['room_px']):.0f} are wanted: the crop cannot "
                   "move up further without losing the face below")
        elif room["headroom_px"] < room["want_px"] - 1.0:
            note = (f"headroom {room['headroom_px']:.0f} px, all the picture has above the head "
                    f"({room['want_px']:.0f} wanted): kept as shot")
        if why:
            if result["status"] == PASS:
                result["status"] = FALLBACK
            result["reason"] = "; ".join(x for x in (result["reason"], "headroom: " + why) if x)
        elif note:
            result["reason"] = "; ".join(x for x in (result["reason"], note) if x)
        result["move"] = move_of(src, out, result["scale"], (result["x"], result["y"]), scaling)
    result["props"] = {k: round(v, 4) for k, v in
                       resolve_props(src, out, result["scale"], (result["x"], result["y"]),
                                     scaling).items()}
    return result


def _held_text(c):
    """How a check went, in words."""
    if not c["checked"]:
        return "no face is checked on any sample"
    where = (f"all {c['total']} samples" if not c["unchecked"] else
             f"all {c['checked']} checked samples of {c['total']}")
    if c["inside"] == c["checked"]:
        if thin(c):
            return (f"the face is checked on only {c['checked']} of {c['total']} samples "
                    f"(at least {c['need']} needed)")
        return f"the face stays inside on {where}"
    return (f"the face leaves the crop on {c['checked'] - c['inside']} of {c['checked']} checked "
            f"samples (worst {c['worst_px']} px at {c['worst_t']} s)")


def no_crop(samples, aspects, reason, area=None):
    """A span's result with no crop for any aspect (status no_face), and
    why."""
    return {"samples": samples, "face_samples": 0, "area": list(area) if area else None,
            "face": None, "rule": "", "review": [], "tracks": 0, "ignored": 0, "track": [],
            "off_track": [], "unchecked": [],
            "aspects": {a: {"aspect": a, "status": NO_FACE, "reason": reason} for a in aspects}}


def plan_span(samples, src, area, aspects, scale=DEFAULT_SCALE, speaker_x=None, margin=MARGIN,
              allow_bars=False, min_checked=MIN_CHECKED_SHARE, subject_region=None,
              min_face_height=None, speaker_y=None, prefer=PREFER_PRESENT, master_size=None,
              scaling=SCALE_TO_FIT):
    """samples: [(t, [Vision box])] for one span; area: the picture area or
    None (every sample black). Returns {samples, face_samples, area, face,
    rule, review, tracks, ignored, track, off_track, unchecked, aspects:
    {aspect: plan_aspect(...) or a no_face result}}. Every sample is judged:
    the primary face's box, else a box taken in its place (off_track), else
    the sample is unchecked; either of the last two flags the span.
    subject_region, min_face_height, speaker_y and prefer steer which face is
    the subject (see the module docstring); `ignored` counts the boxes they
    left out. scale may be AUTO."""
    ignored = 0
    kept = []
    for t, faces in samples:
        boxes_t = []
        for f in faces:
            b = box_px(f, src)
            if admit(b, src, subject_region, min_face_height):
                boxes_t.append(b)
            else:
                ignored += 1
        kept.append((t, boxes_t))
    tracks = link(kept)
    pick, rule, note = choose(tracks, speaker_x, speaker_y, prefer)
    if area is None or pick is None:
        why = ("every sampled frame is black" if area is None else
               f"no face in any of the {len(samples)} sampled frames" if not ignored else
               f"no face in any of the {len(samples)} sampled frames inside the subject region "
               f"at the minimum size ({ignored} face boxes ignored)")
        out = no_crop(len(samples), aspects, why, area)
        out["tracks"] = len(tracks)
        out["ignored"] = ignored
        return out
    out = no_crop(len(samples), (), "", area)
    out["tracks"] = len(tracks)
    out["ignored"] = ignored
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
    edge = [t for t, b in boxes   # a source pixel of overshoot is Vision's float noise, not an edge
            if b[0] < area[0] - EDGE_NOTE_PX or b[1] < area[1] - EDGE_NOTE_PX
            or b[2] > area[2] + EDGE_NOTE_PX or b[3] > area[3] + EDGE_NOTE_PX]
    if edge:
        out["review"].append(
            f"the face box reaches past the edge of the picture at {_times(edge)}: the crop "
            "keeps the face but not the usual margin there")
    for a in aspects:
        out["aspects"][a] = plan_aspect(boxes, (st["x"], st["y"]), src, area, a, scale, margin,
                                        allow_bars, samples=len(samples), min_checked=min_checked,
                                        out=size_of(a, master_size), anchor=anchor_of(a),
                                        scaling=scaling)
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
    return {"width": w, "height": h, "duration": duration, "squeeze": squeeze_tag(data)}


def squeeze_tag(data):
    """The anamorphic squeeze a file states in its tags (BRAW writes
    anamorphic=1.5x), or None. Never guessed from the lens or the shape."""
    for tags in [((data or {}).get("format") or {}).get("tags") or {}] + [
            s.get("tags") or {} for s in (data or {}).get("streams") or []]:
        for k, v in tags.items():
            if "anamorphic" in k.lower():
                try:
                    q = float(str(v).strip().lower().rstrip("x"))
                except ValueError:
                    continue
                if q > 0:
                    return q
    return None


class SourceReader:
    """Frames of one source: read(times, folder) -> [(t, png path, luma)],
    luma being width*height bytes of 8-bit grey, one ffmpeg call a frame."""

    def __init__(self, path):
        info = probe(path)
        self.path, self.size, self.duration = path, (info["width"], info["height"]), info["duration"]
        self.luma_size, self.squeeze = self.size, info.get("squeeze")

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


class StillsReader:
    """Frames of a source ffmpeg cannot decode (BRAW): stills of the same
    picture at any size of its shape, exported by Resolve or the camera's
    proxy. read() ignores the times asked and returns every still, so each
    span is judged on all of them; t is the still's index. size is the
    source's (src, or the stills' own); luma_size the stills'. A still of
    another shape is refused: its boxes would land in the wrong place."""

    def __init__(self, paths, src=None, ffmpeg=None):
        if not paths:
            raise ReframeError("no stills given")
        self.paths, self.ffmpeg = list(paths), ffmpeg or FFMPEG
        sizes = {(i["width"], i["height"]) for i in map(probe, self.paths)}
        if len(sizes) != 1:
            raise ReframeError("the stills are not all one size: " +
                               ", ".join(f"{w}x{h}" for w, h in sorted(sizes)))
        self.luma_size = sizes.pop()
        self.size = tuple(int(v) for v in src) if src else self.luma_size
        a, b = self.size[0] / float(self.size[1]), self.luma_size[0] / float(self.luma_size[1])
        if abs(a / b - 1) > STILL_SHAPE_TOLERANCE:
            raise ReframeError(f"the stills are {self.luma_size[0]}x{self.luma_size[1]} and the "
                               f"source is {self.size[0]}x{self.size[1]}: not the same shape")
        self.duration, self.squeeze = 0.0, None

    def read(self, times, folder):
        out = []
        w, h = self.luma_size
        for i, p in enumerate(self.paths):
            cmd = [self.ffmpeg, "-v", "error", "-i", "file:" + p, "-frames:v", "1", "-f",
                   "rawvideo", "-pix_fmt", "gray", "-"]
            try:
                proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, timeout=TIMEOUT_S)
            except (OSError, subprocess.TimeoutExpired) as e:
                raise ReframeError(f"cannot read the still {p}: {e}")
            if proc.returncode != 0 or len(proc.stdout) < w * h:
                raise ReframeError(f"ffmpeg could not read the still {p}: "
                                   f"{proc.stderr.decode(errors='replace').strip()[-200:]}")
            out.append((float(i + 1), p, proc.stdout[:w * h]))
        return out


def picture_area(lumas, size, level=BAR_LEVEL):
    """(x0, y0, x1, y1): the frame less its black bars; None when every frame
    is black. A bar is a run of rows (or columns) from an edge whose
    brightest pixel is at or below `level` in every frame, so it is flat and
    black. A dark scene is not a bar: it has specks, lights and sensor noise
    above `level`, and the mean luma that an earlier version used could not
    tell the two apart (a night stage lost 88% of its frame)."""
    w, h = size

    def first_active(n, line_max):
        for i in range(n):
            if any(line_max(buf, i) > level for buf in lumas):
                return i
        return None

    def row_max(buf, r):
        return max(buf[r * w:(r + 1) * w])

    def col_max(buf, c):
        return max(buf[c::w])

    top = first_active(h, row_max)
    if top is None:
        return None
    bottom = first_active(h, lambda buf, i: row_max(buf, h - 1 - i))
    left = first_active(w, col_max)
    right = first_active(w, lambda buf, i: col_max(buf, w - 1 - i))
    return (left, top, w - right, h - bottom)


# ---------------------------------------------------------------------------
# A manifest
# ---------------------------------------------------------------------------

def clip_scale(clip, allow_bars=False):
    """The scale a clip asks for. Its own reframe.scale wins. With none the
    scale is AUTO (the widest scale that fills the frame, derived from the
    source and the aspect), or, when allow_bars is set, DEFAULT_SCALE: the
    Ep 002 punch-in, whose look is bars on a letterboxed call recording."""
    s = (clip.get("reframe") or {}).get("scale")
    if s == AUTO:
        return AUTO
    if s is None:
        return DEFAULT_SCALE if allow_bars else AUTO
    return float(s)


def _subject_args(min_checked, subject_region, min_face_height, prefer):
    """Validate the steering arguments; returns subject_region as a tuple."""
    if not isinstance(min_checked, (int, float)) or isinstance(min_checked, bool) \
            or not 0 <= min_checked <= 1:
        raise ReframeError(f"min_checked must be a share between 0 and 1 (got {min_checked!r})")
    if min_face_height is not None and not (isinstance(min_face_height, (int, float))
                                            and 0 < min_face_height <= 1):
        raise ReframeError(f"min_face_height must be a share of the frame height above 0 and "
                           f"at most 1 (got {min_face_height!r})")
    if prefer not in PREFERS:
        raise ReframeError(f"prefer must be one of {', '.join(PREFERS)} (got {prefer!r})")
    if subject_region is not None:
        try:
            x0, y0, x1, y1 = (float(v) for v in subject_region)
        except (TypeError, ValueError):
            raise ReframeError("subject_region must be four numbers: x0 y0 x1 y1 in source pixels")
        if not (x0 < x1 and y0 < y1):
            raise ReframeError(f"subject_region must have x0 < x1 and y0 < y1 (got {subject_region!r})")
        subject_region = (x0, y0, x1, y1)
    return subject_region


def plan(manifest, aspects=("9x16", "1x1"), samples=DEFAULT_SAMPLES, per_second=MIN_PER_SECOND,
         speaker_x=None, only=None, allow_bars=False, reader=None, detect=None, check_cancel=None,
         progress=None, min_checked=MIN_CHECKED_SHARE, subject_region=None, min_face_height=None,
         speaker_y=None, prefer=PREFER_PRESENT, master_size=None, scaling=SCALE_TO_FIT,
         squeeze=None):
    """Plan every span of the manifest's clips (or only those named).
    reader: a SourceReader (default: the manifest's source); detect:
    images -> {path: [Vision box]} or None (default: vision.face_boxes).
    Sample times at or past the source's end (reader.duration, when it
    knows one) are not read; the span says so, and a span wholly past the
    end gets no crop. min_checked, subject_region, min_face_height,
    speaker_y and prefer are described in the module docstring; the scale
    comes from clip_scale(). master_size (the timeline's width and height)
    is needed when aspects include MASTER; scaling is the input scaling the
    props in the report are given for (cut and reframe_apply recompute them
    for the timeline they write). squeeze: the source's anamorphic squeeze;
    the manifest's "squeeze" wins, then this, then the file's own tag, else
    1.0 and the report says it was assumed. Planning runs on the
    desqueezed picture (width times squeeze).
    Returns the report: {source, size, aspects, samples, per_second,
    speaker_x, speaker_y, rule, margin, min_checked, subject_region,
    min_face_height, prefer, spans: [...], rows: [...], counts}."""
    unknown = [a for a in aspects if a not in ASPECTS and a != MASTER]
    if unknown or not aspects:
        raise ReframeError(f"aspects must be among {', '.join(ASPECTS)} or {MASTER} (got "
                           f"{', '.join(unknown) or 'none'})")
    if MASTER in aspects:
        size_of(MASTER, master_size)
    if scaling not in SCALINGS:
        raise ReframeError(f"input scaling '{scaling}': one of {', '.join(SCALINGS)}")
    subject_region = _subject_args(min_checked, subject_region, min_face_height, prefer)
    if speaker_y is not None and speaker_x is None:
        raise ReframeError("speaker_y names a point only together with speaker_x")
    clips = manifest["clips"]
    if only:
        missing = set(only) - {c["name"] for c in clips}
        if missing:
            raise ReframeError("no such clip(s): " + ", ".join(sorted(missing)))
        clips = [c for c in clips if c["name"] in set(only)]
    one = cutlist.single_source(manifest, "reframe")
    reader = reader or SourceReader(one["path"])
    detect = detect or vision.face_boxes
    stored = tuple(reader.size)
    luma_size = tuple(getattr(reader, "luma_size", None) or stored)
    for value, source in ((manifest.get("squeeze"), "manifest"), (squeeze, "argument"),
                          (getattr(reader, "squeeze", None), "file tag")):
        if value is not None:
            break
    else:
        value, source = 1.0, "assumed"
    try:
        sq = float(value)
    except (TypeError, ValueError):
        sq = 0.0
    if not 0.5 <= sq <= 3.0:
        raise ReframeError(f"squeeze {value!r} ({source}): a factor such as 1, 1.33 or 1.5")
    src = (int(round(stored[0] * sq)), stored[1])
    end = float(getattr(reader, "duration", 0) or 0)
    total = sum(len(c["spans"]) for c in clips)
    if speaker_x is not None:
        rule = f"nearest speaker_x {speaker_x:g}" + ("" if speaker_y is None else f", speaker_y {speaker_y:g}")
    else:
        rule = RULE if prefer == PREFER_PRESENT else RULE_LARGEST
    report = {"source": one["path"], "size": list(src), "stored_size": list(stored),
              "squeeze": sq, "squeeze_from": source, "master_size":
              list(size_of(MASTER, master_size)) if master_size else None, "scaling": scaling,
              "aspects": list(aspects),
              "samples": samples, "per_second": per_second, "speaker_x": speaker_x,
              "speaker_y": speaker_y, "rule": rule, "margin": MARGIN,
              "allow_bars": bool(allow_bars), "min_checked": min_checked,
              "subject_region": list(subject_region) if subject_region else None,
              "min_face_height": min_face_height, "prefer": prefer, "spans": []}
    done = 0
    with tempfile.TemporaryDirectory(prefix="rpresolve-reframe-") as tmp:
        for clip in clips:
            scale = clip_scale(clip, allow_bars)
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
                    area = picture_area([luma for _, _, luma in frames], luma_size)
                    if area is not None and luma_size != src:
                        kx, ky = src[0] / float(luma_size[0]), src[1] / float(luma_size[1])
                        area = (area[0] * kx, area[1] * ky, area[2] * kx, area[3] * ky)
                    sp = plan_span([(t, boxes.get(png, [])) for t, png, _ in frames], src, area,
                                   aspects, scale, speaker_x, allow_bars=allow_bars,
                                   min_checked=min_checked, subject_region=subject_region,
                                   min_face_height=min_face_height, speaker_y=speaker_y,
                                   prefer=prefer, master_size=master_size, scaling=scaling)
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
                    if os.path.dirname(os.path.abspath(png)) == os.path.abspath(folder):
                        os.remove(png)  # never a still the caller gave (StillsReader)
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
         "inside": [p["check"]["inside"], p["check"]["total"]], "move": p.get("move"),
         "headroom_px": p.get("headroom_px")}
    if p.get("out"):
        e["out"] = list(p["out"])
    if p.get("reason"):
        e["reason"] = p["reason"]
    if p["bars"]:
        e["bars"] = p["bars"]
    if review:
        e["review"] = "; ".join(review)
    return e


def apply(manifest, report):
    """A copy of the manifest whose planned spans carry reframe[aspect]
    entries. Clip-level reframe (face_x) is kept as it was; cut uses a
    span's own entry first. A squeezed source's entries carry the file's
    own size (stored) beside the desqueezed one they were planned on (src)."""
    m = copy.deepcopy(manifest)
    by_clip = {c["name"]: c for c in m["clips"]}
    stored = report.get("stored_size")
    for sp in report["spans"]:
        span = by_clip[sp["clip"]]["spans"][sp["span"] - 1]
        rf = dict(span.get("reframe") or {})
        for a, p in sp["aspects"].items():
            rf[a] = entry(p, report["size"], sp["area"], sp["face"], sp["review"])
            if stored and list(stored) != list(report["size"]) and "x" in rf[a]:
                rf[a].update(stored=list(stored), squeeze=report.get("squeeze"))
        span["reframe"] = rf
    m["reframe_plan"] = {k: report.get(k) for k in ("size", "stored_size", "squeeze",
                                                    "squeeze_from", "master_size", "scaling",
                                                    "aspects", "samples", "per_second",
                                                    "speaker_x", "speaker_y", "rule", "margin",
                                                    "allow_bars", "min_checked", "subject_region",
                                                    "min_face_height", "prefer")}
    return m


COLUMNS = ("clip", "span", "in", "out", "aspect", "status", "review", "samples", "face_samples",
           "face_x", "face_y", "hand_face_x", "dx", "centre", "scale", "zoom", "pan", "tilt",
           "move", "head_top", "headroom_px", "fills", "inside", "unchecked", "share", "worst_t",
           "worst_px", "reason")


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
            if (not TILT_CONFIRMED and p["status"] in (PASS, BARS, FALLBACK)
                    and abs(props.get("Tilt") or 0.0) > TILT_NEGLIGIBLE_PX):
                reason = "; ".join(x for x in (reason, TILT_NOTE.format(tilt=props["Tilt"])) if x)
            out.append({"clip": sp["clip"], "span": sp["span"], "in": sp["in"], "out": sp["out"],
                        "aspect": a, "status": p["status"], "review": "; ".join(sp["review"]),
                        "samples": sp["samples"], "face_samples": sp["face_samples"],
                        "face_x": face.get("x"), "face_y": face.get("y"),
                        "hand_face_x": sp.get("hand_face_x"), "dx": dx,
                        "centre": p.get("centre"), "scale": p.get("scale"),
                        "zoom": props.get("ZoomX"), "pan": props.get("Pan"),
                        "tilt": props.get("Tilt"), "move": p.get("move"),
                        "head_top": p.get("head_top"), "headroom_px": p.get("headroom_px"),
                        "fills": p.get("fills"),
                        "inside": c.get("inside"), "unchecked": c.get("unchecked"),
                        "share": c.get("share"), "worst_t": c.get("worst_t"),
                        "worst_px": c.get("worst_px"), "reason": reason})
    return out


def counts(rows):
    n = {s: 0 for s in STATUSES}
    for r in rows:
        n[r["status"]] += 1
    n["review"] = sum(1 for r in rows if r["review"])
    n["tilt"] = 0 if TILT_CONFIRMED else sum(1 for r in rows if r["status"] in (PASS, BARS, FALLBACK)
                                             and abs(r["tilt"] or 0.0) > TILT_NEGLIGIBLE_PX)
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
             if n.get("tilt") else "") +
            ("; squeeze not read (no manifest value, argument or file tag): planned as 1.0, "
             "so give squeeze for an anamorphic source" if report.get("squeeze_from") == "assumed"
             else f"; squeeze {report['squeeze']:g} ({report['squeeze_from']})"
             if report.get("squeeze_from") else ""))


def line(r):
    """One row as the line the CLI prints."""
    parts = [f"{r['status'].upper():8} {r['clip']:<14} span {r['span']} {r['aspect']:<5}"]
    if r["face_x"] is not None:
        parts.append(f"face x {r['face_x']:.0f}" +
                     (f" (hand {r['hand_face_x']:g}, {r['dx']:+.0f})" if r["dx"] is not None else ""))
    if r["scale"] is not None and r["status"] in (PASS, BARS, FALLBACK):
        parts.append(f"scale {r['scale']:g} zoom {r['zoom']:g} pan {r['pan']:g} tilt {r['tilt']:g}"
                     + (f" move {r['move']}" if r.get("move") else ""))
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


def plan_items(items, aspect=MASTER, master_size=None, scaling=SCALE_TO_FIT, reader_for=None,
               **kw):
    """A framing plan for items of a timeline, each its own source: [{name,
    path, in, out, squeeze?, stills?, src?}] (in and out in source seconds;
    stills: images to judge in place of the file, for a source ffmpeg
    cannot read, with src the source's own size). Each item is planned as
    one span for `aspect` (MASTER needs master_size) with plan(); kw goes
    to plan(). reader_for(item) may supply the reader (tests).
    Returns {kind, aspect, out, scaling, items: [{name, path, in, out,
    size, stored_size, squeeze, squeeze_from, status, reason, review, move,
    entry, row}], rows, counts}; entry is what reframe_apply writes from."""
    out = size_of(aspect, master_size)
    plan_ = {"kind": "framing", "aspect": aspect, "out": list(out), "scaling": scaling,
             "items": [], "rows": []}
    for it in items:
        name, path = it.get("name") or os.path.basename(it["path"]), it["path"]
        if not it.get("stills") and (it.get("in") is None or it.get("out") is None):
            raise ReframeError(f"{name}: give in and out (source seconds) for a file; only "
                               "stills are judged without a range")
        a, b = float(it.get("in") or 0.0), float(it.get("out") if it.get("out") is not None else 1.0)
        if b <= a:
            raise ReframeError(f"{name}: out ({b:g}) must be after in ({a:g})")
        if reader_for:
            reader = reader_for(it)
        elif it.get("stills"):
            reader = StillsReader(it["stills"], it.get("src"))
        else:
            reader = SourceReader(path)
        m = {"source_path": path, "squeeze": it.get("squeeze"),
             "clips": [{"name": name, "spans": [{"in": a, "out": b}]}]}
        rep = plan(m, aspects=[aspect], reader=reader, master_size=master_size, scaling=scaling,
                   **kw)
        sp = rep["spans"][0]
        p = sp["aspects"][aspect]
        e = entry(p, rep["size"], sp["area"], sp["face"], sp["review"]) if sp["area"] else \
            {"status": p["status"], "reason": p["reason"]}
        e.update(squeeze=rep["squeeze"], stored=rep["stored_size"])
        row = dict(rep["rows"][0], clip=name)
        plan_["items"].append({"name": name, "path": path, "in": a, "out": b,
                               "size": rep["size"], "stored_size": rep["stored_size"],
                               "squeeze": rep["squeeze"], "squeeze_from": rep["squeeze_from"],
                               "status": p["status"], "reason": p.get("reason", ""),
                               "review": sp["review"], "move": p.get("move"), "entry": e,
                               "row": row})
        plan_["rows"].append(row)
    plan_["counts"] = counts(plan_["rows"])
    plan_["counts"]["none"] = sum(1 for i in plan_["items"] if i["move"] == "none")
    return plan_


def write_report(path_stem, report, write):
    """Write <stem>.tsv and <stem>.json with `write(path, text)`; returns the paths."""
    tsv_path, json_path = path_stem + ".tsv", path_stem + ".json"
    write(tsv_path, tsv(report["rows"]))
    write(json_path, json.dumps(report, indent=1, default=str) + "\n")
    return tsv_path, json_path


# ---------------------------------------------------------------------------
# The Tilt sign: a fixture to render, and the check on what comes back
# ---------------------------------------------------------------------------
#
# Pan's sign was confirmed on a render. Tilt's was not, and a wrong sign moves
# every crop that sets one off the face. This does not confirm it; it makes
# the render that does a ten-minute hand step. tilt_fixture writes a short UHD
# clip with a white marker at the crop centre and a red one at the middle of
# the source, and the Pan, Tilt and Zoom a person types into Resolve's
# Inspector on a 1080x1920 timeline. The person renders one still (only a
# person presses Render). tilt_check finds the markers in it and says whether
# they landed where this module's convention puts them, where a flipped Tilt
# (or a flipped Pan) would put them, or somewhere else.

TILT_SRC = (3840, 2160)
TILT_ASPECT = "9x16"
TILT_CENTRE = (0.46, 0.30)    # the crop centre as a share of the source: off the middle on both axes
TILT_MARK = 120               # marker side in source pixels
TILT_SECONDS, TILT_FPS = 2, 24
TILT_VIDEO, TILT_EXPECT_FILE, TILT_RESULT_FILE = "tilt-fixture.mp4", "tilt-expect.json", "tilt-result.json"
TILT_COLOURS = {"a": "white", "b": "red", "c": "yellow"}
# The fixtures that settled Pan and Tilt's units (the module docstring): two
# input scalings, four timeline shapes, two source shapes, so that (sw*fit)/W
# and (sh*fit)/H take different values. "master" is a 2026-10 festival
# project's setup, a 3:2 6K frame into 5952x2646 at scaleToCrop.
TILT_VARIANTS = {
    "9x16": {"src": TILT_SRC, "out": (1080, 1920), "centre": TILT_CENTRE, "scaling": SCALE_TO_FIT},
    "master": {"src": (6048, 4032), "out": (5952, 2646), "centre": (0.46, 0.40),
               "scaling": SCALE_TO_CROP},
    "1x1": {"src": TILT_SRC, "out": (1080, 1080), "centre": (0.46, 0.35), "scaling": SCALE_TO_FIT},
    "9x16-6k": {"src": (6048, 4032), "out": (1080, 1920), "centre": (0.46, 0.35),
                "scaling": SCALE_TO_FIT},
    "master-fit": {"src": (6048, 4032), "out": (5952, 2646), "centre": (0.46, 0.40),
                   "scaling": SCALE_TO_FIT},
    "16x9-crop": {"src": TILT_SRC, "out": (1920, 1080), "centre": (0.46, 0.35),
                  "scaling": SCALE_TO_CROP},
}


def tilt_expect(src=TILT_SRC, aspect=TILT_ASPECT, centre_share=TILT_CENTRE, scaling=SCALE_TO_FIT,
                out=None):
    """What a render of the fixture must show if Pan and Tilt follow this
    module's convention. Marker a (white) sits at the crop centre, so it must
    land at the middle of the output; marker b (red) at the middle of the
    source; marker c (yellow) 300 source pixels above a, for a human to see
    which way is up. A source point (x, y) lands at
        X = W/2 + (x - sw/2) * scale + Pan        (Pan moves the image right)
        Y = H/2 + (y - sh/2) * scale - Tilt       (Tilt moves the image up)
    and if Tilt's sign were the other way, at Y = H/2 + (y - sh/2) * scale +
    Tilt; likewise for Pan. 'expect', 'if_tilt_flipped' and 'if_pan_flipped'
    give the landing of a and b under each reading. `out` (a timeline size)
    overrides the aspect's."""
    out = tuple(out) if out else ASPECTS[aspect]
    sw, sh = src
    scale = fill_scale(out, (0, 0, sw, sh))
    cx, cy = centre_share[0] * sw, centre_share[1] * sh
    raw = resolve_props(src, out, scale, (cx, cy), scaling)
    dx, dy = shift_px(src, scale, (cx, cy))
    marks = {"a": (cx, cy), "b": (sw / 2.0, sh / 2.0), "c": (cx, cy - 300.0)}

    def land(point, pan_dir=1, tilt_dir=1):
        return [round(out[0] / 2.0 + (point[0] - sw / 2.0) * scale + pan_dir * dx, 2),
                round(out[1] / 2.0 + (point[1] - sh / 2.0) * scale + tilt_dir * dy, 2)]

    return {
        "source": [sw, sh], "aspect": aspect if out == ASPECTS.get(aspect) else None,
        "out": list(out), "scale": scale,
        "input_scaling": scaling, "centre": [round(cx, 2), round(cy, 2)],
        "props": {k: round(v, 4) for k, v in raw.items()},
        "markers": {k: {"colour": TILT_COLOURS[k], "at": [round(v[0], 2), round(v[1], 2)]}
                    for k, v in marks.items()},
        "expect": {k: land(marks[k]) for k in ("a", "b", "c")},
        "if_tilt_flipped": {k: land(marks[k], tilt_dir=-1) for k in ("a", "b")},
        "if_pan_flipped": {k: land(marks[k], pan_dir=-1) for k in ("a", "b")},
        "marker_side": TILT_MARK,
    }


def tilt_filter(expect):
    """The ffmpeg video filter that draws the three markers."""
    half = TILT_MARK / 2.0
    colour = {"white": "white", "red": "red", "yellow": "yellow"}
    boxes = []
    for k in ("a", "b", "c"):
        m = expect["markers"][k]
        boxes.append(f"drawbox=x={m['at'][0] - half:.0f}:y={m['at'][1] - half:.0f}:"
                     f"w={TILT_MARK}:h={TILT_MARK}:color={colour[m['colour']]}:t=fill")
    return ",".join(boxes)


def tilt_fixture(folder, ffmpeg=None, variant="9x16"):
    """Write the fixture clip and tilt-expect.json into `folder` (created)
    for one of TILT_VARIANTS. Returns the expect dict. Needs ffmpeg with
    libx264."""
    ffmpeg = ffmpeg or FFMPEG
    if variant not in TILT_VARIANTS:
        raise ReframeError(f"tilt fixture variant must be one of {', '.join(TILT_VARIANTS)} "
                           f"(got {variant!r})")
    v = TILT_VARIANTS[variant]
    expect = tilt_expect(v["src"], centre_share=v["centre"], scaling=v["scaling"], out=v["out"])
    os.makedirs(folder, exist_ok=True)
    video = os.path.join(folder, TILT_VIDEO)
    sw, sh = expect["source"]
    cmd = [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
           f"color=c=black:s={sw}x{sh}:r={TILT_FPS}:d={TILT_SECONDS}",
           "-vf", tilt_filter(expect), "-c:v", "libx264", "-preset", "veryfast", "-crf", "12",
           "-pix_fmt", "yuv420p", video]
    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ReframeError(f"cannot run ffmpeg to write the fixture: {e}")
    if proc.returncode != 0 or not os.path.exists(video):
        raise ReframeError("ffmpeg could not write the fixture: "
                           f"{proc.stderr.decode(errors='replace').strip()[-300:]}")
    with open(os.path.join(folder, TILT_EXPECT_FILE), "w", encoding="utf-8") as f:
        json.dump(expect, f, indent=1)
        f.write("\n")
    return expect


def read_rgb(path, ffmpeg=None):
    """(width, height, rgb24 bytes) of a still, decoded by ffmpeg."""
    ffmpeg = ffmpeg or FFMPEG
    info = probe(path)
    w, h = info["width"], info["height"]
    cmd = [ffmpeg, "-v", "error", "-i", "file:" + path, "-frames:v", "1", "-f", "rawvideo",
           "-pix_fmt", "rgb24", "-"]
    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ReframeError(f"cannot decode {path}: {e}")
    if proc.returncode != 0 or len(proc.stdout) < w * h * 3:
        raise ReframeError(f"ffmpeg could not decode {path}: "
                           f"{proc.stderr.decode(errors='replace').strip()[-200:]}")
    return w, h, proc.stdout[:w * h * 3]


def find_marker(rgb, size, test, stride=1):
    """(x, y, pixels) of the centroid of the pixels `test(r, g, b)` accepts,
    sampled every `stride` pixels (pixels is the count of samples); None when
    no pixel matches."""
    w, h = size
    sx = sy = n = 0
    for y in range(0, h, stride):
        row = rgb[y * w * 3:(y + 1) * w * 3]
        for x in range(0, w, stride):
            i = x * 3
            if test(row[i], row[i + 1], row[i + 2]):
                sx += x
                sy += y
                n += 1
    return (sx / float(n), sy / float(n), n) if n else None


def _is_white(r, g, b):
    return r > 170 and g > 170 and b > 170


def _is_red(r, g, b):
    return r > 150 and g < 100 and b < 100


def tilt_check(rgb, size, expect, tolerance=None):
    """Compare the markers in a render (rgb24 bytes of `size`) with `expect`
    (tilt_expect()). Returns {size, tolerance, found: {a, b}, expected,
    pan, tilt, ok, notes}. pan and tilt are 'confirmed' (both markers landed
    where this module's convention puts them on that axis), 'inverted' (where
    the flipped reading puts them) or 'unclear'. The render may be any size of
    the fixture's shape; positions scale with it. ok needs both confirmed."""
    w, h = size
    out_w, out_h = expect["out"]
    if w * out_h != h * out_w:
        raise ReframeError(f"the render is {w}x{h}; the fixture is {out_w}x{out_h} (any size of "
                           "that shape will do)")
    k = w / float(out_w)
    tol = float(tolerance) if tolerance is not None else max(4.0, 0.01 * w)
    stride = max(1, w // 1200)
    least = max(4, int(0.2 * ((expect["marker_side"] * expect["scale"] * k) / stride) ** 2))
    found = {}
    notes = []
    for key, test in (("a", _is_white), ("b", _is_red)):
        m = find_marker(rgb, size, test, stride)
        if m and m[2] >= least:
            found[key] = [round(m[0], 1), round(m[1], 1)]
        else:
            found[key] = None
            notes.append(f"marker {key} ({TILT_COLOURS[key]}) not found in the render: it is "
                         "off the frame, or the render is not of the fixture")

    def axis(i):
        if found["a"] is None or found["b"] is None:
            return "unclear"

        def holds(table):
            return all(abs(found[m][i] - table[m][i] * k) <= tol for m in ("a", "b"))
        flipped = expect["if_pan_flipped"] if i == 0 else expect["if_tilt_flipped"]
        if holds(expect["expect"]):
            return "confirmed"
        if holds(flipped):
            return "inverted"
        return "unclear"

    pan, tilt = axis(0), axis(1)
    for name, verdict in (("Pan", pan), ("Tilt", tilt)):
        if verdict == "unclear" and found["a"] is not None and found["b"] is not None:
            notes.append(f"{name}: the markers landed where neither reading puts them; check the "
                         "Zoom, Pan and Tilt typed into Resolve and that input scaling is "
                         f"{expect['input_scaling']}")
    return {"size": [w, h], "tolerance": tol, "found": found,
            "expected": {m: [round(v * k, 1) for v in expect["expect"][m]] for m in ("a", "b")},
            "pan": pan, "tilt": tilt, "ok": pan == "confirmed" and tilt == "confirmed",
            "notes": notes}

