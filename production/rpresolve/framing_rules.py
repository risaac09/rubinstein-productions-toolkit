"""
rpresolve.framing_rules: subject, framing rule, true-resolution margin and edge
distractions for a crop. Pure and stdlib only; reframe.py calls it for every
crop it plans and puts the result on the crop as "framing".

A crop is (out, scale, centre) as in reframe.py: an output frame of out[0] x
out[1] pixels, `scale` output pixels per (desqueezed) source pixel, centred on
a source point. Everything below follows from that.

True resolution. The source pixels under a crop are out / scale on each axis,
in the desqueezed picture; in the file's own pixels the columns are divided by
the squeeze (stored[0] / src[0]). A deliverable of D[0] x D[1] is native (no
upscale) when the stored columns >= D[0] and the rows >= D[1]. The margin is
min(columns / D[0], rows / D[1]): 1.0 is exactly native, below 1 is an upscale
by 1 / margin. At the widest crop that fills the frame (the fill scale) the
margin is the largest zoom over fill that stays native: max_zoom.

  A Pyxis 6048x4032 into 3840x2160: the fill crop is 6048x3402, margin 1.575,
  so true 4K holds to a zoom of 1.575 over fill. Into 9:16 (2160x3840): the
  crop is 2268x4032, margin 1.05.

Shot size comes from the head's height in the output frame: the face box
(Vision stops near the brows) plus HEAD_ABOVE face heights, times scale, over
out[1]. The bands come from body proportions (an adult is about 7.5 heads
tall, so a full figure filling the frame has a head about 1/8 of it); they are
derived, not measured, and are a description, never a failure.

The rules, which `choose` picks from:
  FACE           framing "centre" (the default): the face centre, as before
  CENTRED        the face centred on x, eye line on the upper third (9:16 and
                 any frame taller than wide; a frontal face in a wide frame)
  LOOK_ROOM      a face turned away from camera in a wide frame: its centre on
                 the third line away from where it looks, so the open two
                 thirds lie in front of it; eye line on the upper third
  AS_SHOT        the master shape: the shot as shot, moved only for the head
  SALIENCY       no face: the scene's saliency centre
  HAND           a person's centre
Then reframe's lift() and place() keep the whole head with its room and the
crop inside the picture, so a rule's point is where the crop starts, not
always where it lands; the report gives where the eye line landed.

Look direction is Vision's yaw. Its sign has not been confirmed on a frame
(LOOK_CONFIRMED), so a crop placed by it is flagged for a look.

Edges: a second face cut by the crop's edge, and an edge band of the crop
much brighter than the crop (a screen, a window, a lamp), are flagged. They
describe; they never move a crop.
"""

import statistics

HEAD_ABOVE = 0.6         # as reframe.HEAD_ABOVE: face heights from the box's top to the head's top
EYE_DROP = 0.2           # face heights from the top of Vision's box (the brows) down to the eyes: an estimate
NATIVE_TOLERANCE_PX = 0.5
DELIVER = {"9x16": (2160, 3840), "16x9": (3840, 2160), "1x1": (2160, 2160), "4x5": (2160, 2700)}
THIRD = 1.0 / 3.0
LOOK_YAW = 0.35          # radians (about 20 degrees): a face turned this far looks somewhere
LOOK_CONFIRMED = False   # whether a positive yaw looks to frame right: no frame has confirmed it
EDGE_BAND = 0.04         # of the crop's width (or height): the band at each edge that is read
EDGE_BRIGHT = 1.6        # an edge band whose mean luma is this many times the crop's is flagged
EDGE_MIN_LUMA = 96       # ... and at least this bright (0..255), so a dark frame's noise is not
ROW_STRIDE = 4           # read every 4th row of a luma frame: means, not edges

FACE, CENTRED, LOOK_ROOM, AS_SHOT, SALIENCY, HAND = ("face centre", "centred", "look room",
                                                     "as shot", "saliency", "hand")
MODES = ("centre", "rules")  # reframe's framing argument: the face centre (as before), or the rules
RULES = {
    FACE: "the face centred, lifted for the head and its room",
    CENTRED: "centred on the face, eye line on the upper third",
    LOOK_ROOM: "look room: the face on the third line away from its look, eye line on the "
               "upper third",
    AS_SHOT: "as shot: the master's centre fill, moved only to keep the head",
    SALIENCY: "the saliency centre (no face)",
    HAND: "a person's centre (hand)",
}

# (name, lowest head share of the frame height): the first the head reaches
SHOTS = (("ECU", 0.75), ("CU", 0.42), ("MCU", 0.29), ("MS", 0.20), ("MLS", 0.135),
         ("FS", 0.09), ("WS", 0.0))


# ---------------------------------------------------------------------------
# True resolution
# ---------------------------------------------------------------------------

def deliver_of(aspect, out):
    """The deliverable's pixel size: UHD for the platform shapes (DELIVER),
    else the frame itself (the master)."""
    return tuple(DELIVER.get(aspect) or out)


def native(src, stored, out, scale, deliver):
    """The file's own pixels under a crop and the margin over `deliver`.
    src: the planned (desqueezed) size; stored: the file's size."""
    cols = out[0] / float(scale) * stored[0] / float(src[0])
    rows = out[1] / float(scale) * stored[1] / float(src[1])
    margin = min(cols / deliver[0], rows / deliver[1])
    return {"cols": int(round(cols)), "rows": int(round(rows)), "deliver": list(deliver),
            "margin": round(margin, 3),
            "native": cols >= deliver[0] - NATIVE_TOLERANCE_PX
            and rows >= deliver[1] - NATIVE_TOLERANCE_PX}


def limits(stored, squeeze, deliver):
    """The widest crop of the deliverable's shape inside a whole frame
    (stored size, desqueezed by `squeeze`) and the largest zoom over it that
    stays native: {crop: desqueezed size, stored: file pixels under it,
    max_zoom}. A max_zoom below 1 means no crop of this shape is native."""
    w, h = stored[0] * float(squeeze), float(stored[1])
    aspect = deliver[0] / float(deliver[1])
    cw, ch = (h * aspect, h) if w / h >= aspect else (w, w / aspect)
    sc, sr = cw / squeeze, ch
    return {"crop": [int(round(cw)), int(round(ch))], "stored": [int(round(sc)), int(round(sr))],
            "max_zoom": round(min(sc / deliver[0], sr / deliver[1]), 3)}


def native_text(n, max_zoom=None):
    """'2268x4032 under 2160x3840, margin 1.05' and, below native, the upscale."""
    t = f"{n['cols']}x{n['rows']} source px under {n['deliver'][0]}x{n['deliver'][1]}, " \
        f"margin {n['margin']:g}"
    if max_zoom is not None:
        t += f" (native to zoom {max_zoom:g} over fill)"
    if not n["native"]:
        t += f": BELOW {n['deliver'][0]}x{n['deliver'][1]}, an upscale of {1 / n['margin']:.2f}x " \
            "to deliver it"
    return t


# ---------------------------------------------------------------------------
# Subject and rule
# ---------------------------------------------------------------------------

def shot_size(face_h, scale, out_h):
    """ECU to WS from the head's height in the output frame (module docstring)."""
    share = (1 + HEAD_ABOVE) * face_h * scale / out_h
    return next(name for name, low in SHOTS if share >= low)


def look_of(yaw):
    """'left', 'right' or None (frontal, or no yaw) from Vision's yaw in radians."""
    if yaw is None or abs(yaw) < LOOK_YAW:
        return None
    return "right" if yaw > 0 else "left"


def choose(out, subject, look=None, master=False, mode="centre"):
    """The rule for a crop: subject is "face", "saliency" or "hand"; mode is
    reframe's framing argument (MODES)."""
    if subject == "hand":
        return HAND
    if subject == "saliency":
        return SALIENCY
    if master:
        return AS_SHOT
    if mode != "rules":
        return FACE
    if out[0] > out[1] and look:
        return LOOK_ROOM
    return CENTRED


def target(rule, face, out, scale, look=None):
    """The crop centre (source px) a rule asks for. face: {x, y, w, h} of the
    median face box (x, y its centre). For CENTRED and LOOK_ROOM the eye line
    (EYE_DROP face heights below the box's top) lands on the upper third:
    eye - cy = (1/3 - 1/2) * out_h / scale. LOOK_ROOM puts the face centre on
    the third line behind its look: x_out = W/3 looking right, 2W/3 looking
    left. Other rules keep the face centre (reframe decides the rest)."""
    x, y = face["x"], face["y"]
    if rule not in (CENTRED, LOOK_ROOM):
        return (x, y)
    eye = y - face["h"] / 2.0 + EYE_DROP * face["h"]
    cy = eye + out[1] / (6.0 * scale)
    if rule == LOOK_ROOM:
        sign = 1 if look == "right" else -1
        return (x + sign * out[0] / (6.0 * scale), cy)
    return (x, cy)


def placement(face, out, scale, centre):
    """Where the face landed: eye line as a share of the frame height, its
    distance from the upper third in output px, and the face centre's x as a
    share of the width."""
    eye = face["y"] - face["h"] / 2.0 + EYE_DROP * face["h"]
    eye_out = out[1] / 2.0 + (eye - centre[1]) * scale
    x_out = out[0] / 2.0 + (face["x"] - centre[0]) * scale
    return {"eye_line": round(eye_out / out[1], 3), "third_px": round(eye_out - out[1] * THIRD, 1),
            "face_x": round(x_out / out[0], 3)}


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------

def window(out, scale, centre):
    hw, hh = out[0] / (2.0 * scale), out[1] / (2.0 * scale)
    return (centre[0] - hw, centre[1] - hh, centre[0] + hw, centre[1] + hh)


def cut_faces(others, win):
    """Sides of the window that cut one of `others` (median boxes of faces
    that are not the subject): the box is partly inside and partly out."""
    sides = []
    for b in others:
        inside = b[0] < win[2] and b[2] > win[0] and b[1] < win[3] and b[3] > win[1]
        if not inside:
            continue
        for side, cut in (("left", b[0] < win[0]), ("right", b[2] > win[2]),
                          ("top", b[1] < win[1]), ("bottom", b[3] > win[3])):
            if cut and side not in sides:
                sides.append(side)
    return sides


def _mean(lumas, size, box):
    """Mean luma (0..255) of a box (luma px) over every frame, every ROW_STRIDE-th row."""
    w = size[0]
    x0, y0 = max(0, int(box[0])), max(0, int(box[1]))
    x1, y1 = min(size[0], int(box[2])), min(size[1], int(box[3]))
    if x1 <= x0 or y1 <= y0:
        return None
    total = count = 0
    for buf in lumas:
        for r in range(y0, y1, ROW_STRIDE):
            total += sum(buf[r * w + x0:r * w + x1])
            count += x1 - x0
    return total / float(count)


def bright_edges(lumas, luma_size, src, win):
    """Sides whose edge band (EDGE_BAND of the crop) is EDGE_BRIGHT times the
    crop's mean luma and at least EDGE_MIN_LUMA: [(side, band mean, crop mean)]."""
    if not lumas:
        return []
    kx, ky = luma_size[0] / float(src[0]), luma_size[1] / float(src[1])
    x0, y0, x1, y1 = win[0] * kx, win[1] * ky, win[2] * kx, win[3] * ky
    whole = _mean(lumas, luma_size, (x0, y0, x1, y1))
    if not whole:
        return []
    bx, by = EDGE_BAND * (x1 - x0), EDGE_BAND * (y1 - y0)
    found = []
    for side, box in (("left", (x0, y0, x0 + bx, y1)), ("right", (x1 - bx, y0, x1, y1)),
                      ("top", (x0, y0, x1, y0 + by)), ("bottom", (x0, y1 - by, x1, y1))):
        m = _mean(lumas, luma_size, box)
        if m is not None and m >= EDGE_MIN_LUMA and m >= EDGE_BRIGHT * whole:
            found.append((side, round(m), round(whole)))
    return found


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------

def record(p, aspect, src, stored, subject, rule, face=None, look=None, others=(), lumas=None,
           luma_size=None, fill=None, require_native=False):
    """The "framing" dict for an aspect plan p that carries a crop (x, y,
    scale, out) and the review lines it adds. fill: the fill scale, for
    max_zoom. A crop below native is flagged when the source is native at
    fill (the zoom lost it) or, with require_native, always."""
    out, scale, centre = tuple(p["out"]), p["scale"], (p["x"], p["y"])
    deliver = deliver_of(aspect, out)
    n = native(src, stored, out, scale, deliver)
    rec = {"subject": subject, "rule": rule, "rule_text": RULES[rule], "native": n}
    if fill:
        rec["max_zoom"] = native(src, stored, out, fill, deliver)["margin"]
    if face and subject != "hand":
        rec["shot"] = shot_size(face["h"], scale, out[1]) if subject == "face" else None
        rec.update(placement(face, out, scale, centre))
    if look:
        rec["look"] = look
    win = window(out, scale, centre)
    edges = [f"a second face is cut at the {s} edge" for s in cut_faces(others, win)]
    edges += [f"the {s} edge is bright (luma {m} against {w} in the crop): a screen, window or "
              "lamp?" for s, m, w in bright_edges(lumas, luma_size, src, win)]
    rec["edges"] = edges
    review = []
    if not n["native"] and (require_native or rec.get("max_zoom", 0) >= 1):
        review.append(f"{aspect}: {native_text(n)}")
    if edges:
        review.append(f"{aspect}: edge: " + "; ".join(edges))
    if rule == LOOK_ROOM and not LOOK_CONFIRMED:
        review.append(f"{aspect}: look room from Vision's yaw, whose sign no frame has confirmed: "
                      "check the face looks into the open side")
    return rec, review


def median_yaw(yaws):
    yaws = [y for y in yaws if y is not None]
    return statistics.median(yaws) if yaws else None
