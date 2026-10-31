"""
rpresolve.safezone: the safe-zone margins and the box predicate, written once.
qc (safe_zone) reads the portrait margins; deliver-check (safe_zone),
overlay-check (safe) and captionlint (safe_zone) test boxes with outside().
captionlint keeps its own uniform --set margin. Stdlib only, no media.

A box is (x0, y0, x1, y1) as fractions of the frame. Margins are
(top, bottom, side) as fractions of the frame's height, height and width.
Portrait frames carry the social UI margins; every other shape carries
title safe. All of these are working defaults, not a platform's published
numbers (platform-specs.md records none).
"""

import json

PORTRAIT = {"top": 0.14, "bottom": 0.35, "side": 0.06}   # the 9:16 UI bands
TITLE_SAFE = 0.05                                          # each edge, landscape and square


def margins(width, height):
    """(top, bottom, side) for a frame of this size."""
    if height > width:
        return PORTRAIT["top"], PORTRAIT["bottom"], PORTRAIT["side"]
    return TITLE_SAFE, TITLE_SAFE, TITLE_SAFE


def outside(box, m):
    """The sides of the safe zone m the box crosses, as a list of 'top',
    'bottom', 'left', 'right' (empty when it sits inside)."""
    top, bottom, side = m
    x0, y0, x1, y1 = box
    eps = 1e-9
    return [n for n, hit in (("top", y0 < top - eps), ("bottom", y1 > 1 - bottom + eps),
                             ("left", x0 < side - eps), ("right", x1 > 1 - side + eps)) if hit]


def load_boxes(path):
    """[(label, box)] from a JSON list of {"label": str, "box": [x0, y0, x1, y1]}
    in frame fractions. Raises ValueError on anything else."""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except OSError as e:
        raise ValueError(f"{path}: {e.strerror or e}")
    out = []
    for i, e in enumerate(doc if isinstance(doc, list) else [None]):
        b = e.get("box") if isinstance(e, dict) else None
        if not (isinstance(b, list) and len(b) == 4
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1
                        for v in b) and b[0] < b[2] and b[1] < b[3]):
            raise ValueError(f"{path}: entry {i} is not {{\"label\", \"box\": [x0, y0, x1, y1]}} "
                             "with 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1")
        out.append((str(e.get("label") or f"box {i + 1}"), tuple(float(v) for v in b)))
    return out
