"""rpstills.frames: what a frame is worth when there is no face to measure.

A wide stage shot, a room, a hillside: the cull has nothing to rank by face
quality, so before this module such a run got no pick at all. Three plain
measures stand in, each between 0 and 1, all taken from the proxy:

  sharp        variance of the Laplacian over the whole frame (long edge
               SHARP_SIDE). Rank-normalized inside the run by cull.py, so it
               compares only frames of the same scene. A rank alone would give
               a perfect score to the best of a run of out-of-focus frames, so
               a frame under BLUR_BELOW is out of focus in absolute terms and
               scores 0 (on a low-light stage shoot the soft frames sat at 7 to 36
               and the usable ones at 89 and up, a gap with nothing between).
  exposure     0 for a blank frame (mean luma under 6% or over 94%), then
               scaled down by blown highlights (a quarter of the frame at 247
               or above takes it to 0), by a crushed frame (more than half of
               it at 8 or below), and by flat contrast (a standard deviation
               under 0.12 of full scale). A dark stage with a lit performer
               scores well; fog and a black lens cap do not.
  composition  only when Vision found a person (index.py's `people`): the
               largest person's size, how near the subject sits to a third
               line or the centre line, and whether the head has room above it.

Pure functions where they can be; metrics() reads one image with Pillow, and
OpenCV when it is installed (Pillow's edge filter otherwise).
"""

from PIL import Image, ImageFilter

SHARP_SIDE = 1000
BLUR_BELOW = 40.0   # Laplacian variance at SHARP_SIDE under which a frame is out of focus, whatever its rank
BLANK_LOW, BLANK_HIGH = 0.06, 0.94
CLIP_LOW, CLIP_HIGH = 8, 247
FLAT_CONTRAST = 0.12
THIRDS = (1 / 3.0, 0.5, 2 / 3.0)


def _laplacian_variance(im):
    try:
        import cv2
        import numpy as np
        return float(cv2.Laplacian(np.asarray(im), cv2.CV_64F).var())
    except ImportError:
        edges = im.filter(ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128))
        hist = edges.histogram()
        n = float(sum(hist))
        mean = sum(i * c for i, c in enumerate(hist)) / n
        return sum(c * (i - mean) ** 2 for i, c in enumerate(hist)) / n


def metrics_image(im):
    """{"sharp", "mean", "contrast", "clip_lo", "clip_hi"} of a Pillow image
    (left unchanged): sharp as above; mean and contrast (standard deviation)
    of luma as shares of full scale; clip_lo and clip_hi the shares of pixels
    at or below CLIP_LOW and at or above CLIP_HIGH. None when it cannot be
    measured."""
    try:
        g = im.convert("L")
        g.thumbnail((SHARP_SIDE, SHARP_SIDE))
        hist = g.histogram()
        n = float(sum(hist))
        mean = sum(i * c for i, c in enumerate(hist)) / n
        var = sum(c * (i - mean) ** 2 for i, c in enumerate(hist)) / n
        return {"sharp": round(_laplacian_variance(g), 3), "mean": round(mean / 255.0, 4),
                "contrast": round((var ** 0.5) / 255.0, 4),
                "clip_lo": round(sum(hist[:CLIP_LOW + 1]) / n, 4),
                "clip_hi": round(sum(hist[CLIP_HIGH:]) / n, 4)}
    except Exception:
        return None


def metrics(path):
    """metrics_image() of an image file; None when the file cannot be read."""
    try:
        with Image.open(path) as im:
            return metrics_image(im)
    except Exception:
        return None


def is_blurry(m):
    """A frame measured as out of focus in absolute terms (see BLUR_BELOW)."""
    return bool(m) and m.get("sharp") is not None and m["sharp"] < BLUR_BELOW


def exposure_score(m):
    """0 to 1: usable light and contrast. See the module docstring."""
    if not m or m["mean"] < BLANK_LOW or m["mean"] > BLANK_HIGH:
        return 0.0
    blown = max(0.0, 1.0 - min(1.0, 4.0 * m["clip_hi"]))
    crushed = max(0.0, 1.0 - max(0.0, m["clip_lo"] - 0.5) * 2.0)
    flat = min(1.0, m["contrast"] / FLAT_CONTRAST)
    return round(blown * crushed * flat, 4)


def composition_score(people):
    """0 to 1 for the largest person in `people` ([{x, y, w, h}], normalized,
    top-left origin), or None when there is none. Size 0.4 (full from 3% to
    60% of the frame), placement 0.4 (on a third line or the centre line),
    headroom 0.2 (the box clears the top edge)."""
    if not people:
        return None
    p = max(people, key=lambda q: q["w"] * q["h"])
    area = p["w"] * p["h"]
    if area < 0.03:
        size = area / 0.03
    elif area > 0.6:
        size = max(0.0, (1.0 - area) / 0.4)
    else:
        size = 1.0
    cx = p["x"] + p["w"] / 2.0
    place = max(0.0, 1.0 - min(abs(cx - t) for t in THIRDS) / 0.17)
    head = 1.0 if p["y"] > 0.02 else 0.5
    return round(0.4 * size + 0.4 * place + 0.2 * head, 4)


def frame_score(sharp_rank, exposure, composition):
    """The frame's score from its parts; a blank frame (exposure 0) scores 0
    whatever else it has. Without a person: 60% sharpness, 40% exposure. With
    one: 45%, 30% and 25% composition."""
    if exposure <= 0.0:
        return 0.0
    if composition is None:
        return round(0.6 * sharp_rank + 0.4 * exposure, 4)
    return round(0.45 * sharp_rank + 0.30 * exposure + 0.25 * composition, 4)
