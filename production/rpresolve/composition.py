"""
rpresolve.composition: what the scene reads (scene.py) say about a span, and
the composition numbers of a crop. Pure and stdlib only: reads in, numbers out.

A read is one scene.read() dict per sampled frame: attention (or objectness)
boxes, normalized, origin top left; a Laplacian sharpness ratio; the frame to
frame mean absolute difference (motion). Depth is never used: scene.read
returns null for it without a compiled model, and that stays null here.

Numbers describe and never fail a shot. The move type (hold, pan or track) is
recorded for a person or a later step; the crop the plan writes stays static.
"""

import statistics

HOLD, PAN, TRACK = "hold", "pan", "track"
MOVES = (HOLD, PAN, TRACK)
DRIFT_PAN = 0.04     # the subject's centre drifts this share of the frame (either axis) across a span: a pan
DRIFT_TRACK = 0.15   # ... this share: a track
MOTION_STILL = 0.005  # frame-to-frame difference at or below this (0..1): the frame is still, a drift is noise
SAFE = 0.95          # the share of the crop, centred, the subject should sit inside


def _union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def subject_box(read, src):
    """(x0, y0, x1, y1) in source pixels: the union of a read's attention boxes,
    else its objectness boxes; None when it has neither."""
    sal = (read or {}).get("saliency") or {}
    for key in ("attention", "objectness"):
        found = [b for b in (sal.get(key) or []) if b.get("w", 0) > 0 and b.get("h", 0) > 0]
        if found:
            return _union([(b["x"] * src[0], b["y"] * src[1], (b["x"] + b["w"]) * src[0],
                            (b["y"] + b["h"]) * src[1]) for b in found])
    return None


def summarise(reads, src):
    """reads: [(t, scene.read() dict or None)]. None when no read has a subject
    box; else {boxes: [(t, box)], centre, spread, sharpness, motion, depth}.
    spread is the standard deviation of the centre as a share of the frame."""
    boxes = [(t, b) for t, r in reads if (b := subject_box(r, src))]
    if not boxes:
        return None
    cs = [((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0) for _, b in boxes]
    sd = (lambda v: statistics.pstdev(v) if len(v) > 1 else 0.0)
    ratios = [r["sharpness"]["ratio"] for _, r in reads
              if r and (r.get("sharpness") or {}).get("ratio") is not None]
    mads = [r["motion"]["mad"] for _, r in reads if r and (r.get("motion") or {}).get("mad") is not None]
    return {"boxes": boxes,
            "centre": {"x": round(statistics.median(c[0] for c in cs), 1),
                       "y": round(statistics.median(c[1] for c in cs), 1)},
            "spread": {"x": round(sd([c[0] for c in cs]) / src[0], 4),
                       "y": round(sd([c[1] for c in cs]) / src[1], 4)},
            "sharpness": round(statistics.median(ratios), 3) if ratios else None,
            "motion": round(statistics.median(mads), 4) if mads else None,
            "depth": None}


def drift(path, src):
    """(dx, dy) in source pixels from the first half of a path of (x, y) centres to
    the second half (medians); (0, 0) for fewer than two points."""
    if len(path) < 2:
        return 0.0, 0.0
    h = len(path) // 2
    a, b = path[:h], path[len(path) - h:]
    return (statistics.median(p[0] for p in b) - statistics.median(p[0] for p in a),
            statistics.median(p[1] for p in b) - statistics.median(p[1] for p in a))


def move_type(path, src, motion=None):
    """hold, pan or track for a span: how far the subject's centre drifts across
    it, as a share of the frame. A frame that barely changes (motion at or below
    MOTION_STILL) is a hold whatever the drift."""
    if motion is not None and motion <= MOTION_STILL:
        return HOLD
    dx, dy = drift(path, src)
    d = max(abs(dx) / src[0], abs(dy) / src[1])
    return TRACK if d >= DRIFT_TRACK else PAN if d >= DRIFT_PAN else HOLD


def compose(boxes, out, scale, centre, src, path_move):
    """The numbers for a crop of `scale` output px per source px centred on
    `centre`, over the span's subject boxes [(t, (x0, y0, x1, y1))] (a face's
    box already grown up to the head): {headroom_px, left_px, right_px, lead_px,
    negative_space, safe}. Gaps are output pixels between the median box and
    the crop's edge; lead_px is the gap on the side the subject moves toward
    (None for a hold); negative_space is the share of the crop outside the
    median box; safe is the share of boxes inside the central SAFE of the crop."""
    hw, hh = out[0] / (2.0 * scale), out[1] / (2.0 * scale)
    win = (centre[0] - hw, centre[1] - hh, centre[0] + hw, centre[1] + hh)
    med = [statistics.median(b[i] for _, b in boxes) for i in range(4)]
    gaps = {"headroom_px": (med[1] - win[1]) * scale, "left_px": (med[0] - win[0]) * scale,
            "right_px": (win[2] - med[2]) * scale}
    inner_w, inner_h = hw * SAFE, hh * SAFE
    safe = sum(1 for _, b in boxes if b[0] >= centre[0] - inner_w and b[2] <= centre[0] + inner_w
               and b[1] >= centre[1] - inner_h and b[3] <= centre[1] + inner_h)
    dx = drift([((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0) for _, b in boxes], src)[0]
    lead = None
    if path_move != HOLD and abs(dx) >= 1e-9:
        lead = gaps["right_px"] if dx > 0 else gaps["left_px"]
    inside = max(0.0, min(med[2], win[2]) - max(med[0], win[0])) * max(0.0, min(med[3], win[3]) - max(med[1], win[1]))
    out_d = {k: round(v, 1) for k, v in gaps.items()}
    out_d.update(lead_px=None if lead is None else round(lead, 1),
                 negative_space=round(1.0 - inside / ((win[2] - win[0]) * (win[3] - win[1])), 3),
                 safe=round(safe / len(boxes), 3))
    return out_d
