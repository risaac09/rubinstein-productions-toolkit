"""rpstills.skin: face regions from Vision landmarks, as pixel masks.

Pure numpy and OpenCV geometry over one face dict; no image is read here.
A face comes from vision.faces(images, landmarks=True) normalized to the
whole image; to_pixels() turns it into pixel coordinates, the form every
other function takes:

    face_px = skin.to_pixels(face, width, height)
    cheeks = skin.cheek_mask(face_px, (height, width))       # bool HxW
    weight = skin.skin_mask(face_px, rgb_u8)                 # float32 HxW, 0..1

Left and right are image sides. Vision's leftEye/rightEye naming is not
trusted (it is the image-left eye on the frames checked so far, the
subject's own right eye); eyes and lip corners are each sorted by image x
and paired on the same side, so a mirrored or relabelled face still puts
each cheek patch under its own eye. Every size scales from the face box,
never from a fixed image width.

  cheek_mask   the cheek apples: an ellipse CHEEK_AT of the way from each
               eye centre to the mouth corner on the same side, radii
               CHEEK_RADII of the face box. The sample skin Lab is read on.
  skin_mask    the hull of the face contour and the brows lifted
               FOREHEAD_LIFT face heights (the forehead), cut above the eye
               line to the brows' span less TEMPLE_INSET face widths (the
               hull alone reaches past the hairline at the temples), kept
               where the YCrCb tone is skin (tone_mask: SKIN_CR, SKIN_CB),
               less the eyes, brows and lips dilated, opened, and feathered
               by a Gaussian of face width / FEATHER_DIV.
  region_masks eyes, brows, lips_outer, lips_inner (the inner lips and both
               mouth corners: Vision's 6 inner points span only the middle
               teeth), lens (an ellipse per eye where a spectacle lens sits)
               and under_eye (less the cheek patches, so a lift there never
               moves the skin sample); bool HxW, not dilated.
  nblur        a Gaussian blur normalized by a mask, so only masked pixels
               contribute (no halo from glasses or hair at the edge).

Tuned on one subject with glasses under overcast light; the tone box and
the proportions are not validated on other skin tones or light.
"""

import cv2
import numpy as np

CHEEK_AT = 0.40               # share of the way from the eye centre to the same-side mouth corner
CHEEK_RADII = (0.06, 0.05)    # ellipse radii as shares of the face box (width, height)
FOREHEAD_LIFT = 0.32          # face heights the brows are lifted to close the forehead
TEMPLE_INSET = 0.02           # above the eye line, skin_mask keeps x inside the brows' span less this, face widths
SKIN_CR = (133, 180)          # YCrCb (OpenCV, 8-bit) skin box, inclusive
SKIN_CB = (77, 135)
EYE_HOLE = 0.035              # eye hole dilation, face widths; brows and lips take half
OPEN_DIV = 300                # opening kernel = face width / OPEN_DIV, at least 3 px
FEATHER_DIV = 90              # feather sigma = face width / FEATHER_DIV
LENS_UP = -0.05               # lens centre above the eye centre, eye widths (negative: below; checked on the rims)
LENS_RADII = (1.05, 0.95)     # lens ellipse radii, eye widths
UNDER_EYE_DOWN = 0.09         # under-eye centre below the eye's lower edge, face heights
UNDER_EYE_RADII = (0.55, 0.06)  # eye widths across, face heights down


def to_pixels(face, width, height):
    """A normalized face (box and any landmarks) in pixel coordinates. Other
    keys are copied as they are."""
    out = dict(face)
    out["x"], out["w"] = face["x"] * width, face["w"] * width
    out["y"], out["h"] = face["y"] * height, face["h"] * height
    if face.get("landmarks"):
        scale = np.array([width, height], float)
        out["landmarks"] = {k: (np.asarray(v, float).reshape(-1, 2) * scale).tolist()
                            for k, v in face["landmarks"].items()}
    return out


def points(face_px, name):
    """(N, 2) float array of one landmark region; ValueError when it is missing."""
    pts = (face_px.get("landmarks") or {}).get(name)
    if not pts:
        raise ValueError(f"face has no {name} landmarks")
    return np.asarray(pts, float).reshape(-1, 2)


def eye_centres(face_px):
    """(left_xy, right_xy): the two eye centres sorted by image x, whatever
    Vision called them."""
    a, b = points(face_px, "leftEye").mean(0), points(face_px, "rightEye").mean(0)
    return (a, b) if a[0] <= b[0] else (b, a)


def mouth_corners(face_px):
    """(left_xy, right_xy): the outer-lip points with the least and greatest x."""
    lips = points(face_px, "outerLips")
    return lips[lips[:, 0].argmin()], lips[lips[:, 0].argmax()]


def cheek_centres(face_px):
    """Centres of the two cheek patches, image-left first."""
    return [e + CHEEK_AT * (c - e) for e, c in zip(eye_centres(face_px), mouth_corners(face_px))]


def _ellipse(m, centre, radii):
    cv2.ellipse(m, (int(round(centre[0])), int(round(centre[1]))),
                (max(1, int(round(radii[0]))), max(1, int(round(radii[1])))), 0, 0, 360, 1, -1)


def cheek_mask(face_px, shape):
    """bool HxW: the two cheek-apple ellipses."""
    m = np.zeros(shape[:2], np.uint8)
    radii = (CHEEK_RADII[0] * face_px["w"], CHEEK_RADII[1] * face_px["h"])
    for c in cheek_centres(face_px):
        _ellipse(m, c, radii)
    return m > 0


def poly(shape, pts, dilate=0):
    """bool HxW: the filled convex hull of pts, dilated by `dilate` px."""
    m = np.zeros(shape[:2], np.uint8)
    pts = np.asarray(pts, float).reshape(-1, 2)
    if len(pts) >= 3:
        cv2.fillPoly(m, [cv2.convexHull(np.round(pts).astype(np.int32))], 1)
        if dilate > 0:
            m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * dilate + 1,) * 2))
    return m > 0


def _eye_parts(face_px):
    """[(eye points, centre, width)] for both eyes, image-left first."""
    eyes = [points(face_px, "leftEye"), points(face_px, "rightEye")]
    eyes.sort(key=lambda e: e[:, 0].mean())
    return [(e, e.mean(0), float(np.ptp(e[:, 0]))) for e in eyes]


def region_masks(face_px, shape):
    """{"eyes", "brows", "lips_outer", "lips_inner", "lens", "under_eye"}:
    bool HxW each, undilated."""
    shape = shape[:2]
    H = face_px["h"]
    lens = np.zeros(shape, np.uint8)
    under = np.zeros(shape, np.uint8)
    for e, c, ew in _eye_parts(face_px):
        _ellipse(lens, (c[0], c[1] - LENS_UP * ew), (LENS_RADII[0] * ew, LENS_RADII[1] * ew))
        _ellipse(under, (c[0], e[:, 1].max() + UNDER_EYE_DOWN * H),
                 (UNDER_EYE_RADII[0] * ew, UNDER_EYE_RADII[1] * H))
    return {"eyes": poly(shape, points(face_px, "leftEye")) | poly(shape, points(face_px, "rightEye")),
            "brows": poly(shape, points(face_px, "leftEyebrow")) | poly(shape, points(face_px, "rightEyebrow")),
            "lips_outer": poly(shape, points(face_px, "outerLips")),
            "lips_inner": poly(shape, np.vstack([points(face_px, "innerLips"), *mouth_corners(face_px)])),
            "lens": lens > 0, "under_eye": (under > 0) & ~cheek_mask(face_px, shape)}


def tone_mask(rgb_u8):
    """bool (...): which pixels of a uint8 RGB array (..., 3), an image or
    a list of pixels, fall inside the YCrCb skin box (SKIN_CR, SKIN_CB)."""
    a = np.ascontiguousarray(rgb_u8[..., :3], np.uint8)
    ycc = cv2.cvtColor(a.reshape(-1, 1, 3), cv2.COLOR_RGB2YCrCb).reshape(a.shape)
    cr, cb = ycc[..., 1], ycc[..., 2]
    return (cr >= SKIN_CR[0]) & (cr <= SKIN_CR[1]) & (cb >= SKIN_CB[0]) & (cb <= SKIN_CB[1])


def skin_mask(face_px, rgb_u8):
    """float32 HxW in [0, 1]: facial skin, feathered."""
    shape = rgb_u8.shape[:2]
    w, h = face_px["w"], face_px["h"]
    brows = np.vstack([points(face_px, "leftEyebrow"), points(face_px, "rightEyebrow")])
    face = poly(shape, np.vstack([points(face_px, "faceContour"), brows - [0.0, FOREHEAD_LIFT * h]]))
    # brown and blond hair passes the tone box, and the hull reaches it at the temples
    top = max(0, int(np.mean([c[1] for c in eye_centres(face_px)])))
    face[:top, :max(0, int(brows[:, 0].min() + TEMPLE_INSET * w))] = False
    face[:top, max(0, int(np.ceil(brows[:, 0].max() - TEMPLE_INSET * w))):] = False
    tone = tone_mask(rgb_u8)
    d = int(EYE_HOLE * w)
    holes = (poly(shape, points(face_px, "leftEye"), d) | poly(shape, points(face_px, "rightEye"), d)
             | poly(shape, points(face_px, "leftEyebrow"), d // 2)
             | poly(shape, points(face_px, "rightEyebrow"), d // 2)
             | poly(shape, points(face_px, "outerLips"), d // 2))
    m = (face & tone & ~holes).astype(np.uint8)
    k = max(3, int(w / OPEN_DIV) | 1)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((k, k), np.uint8)).astype(np.float32)
    return np.clip(cv2.GaussianBlur(m, (0, 0), w / FEATHER_DIV), 0, 1)


def nblur(img, mask, sigma):
    """Gaussian blur of img (HxW or HxWxC) where only pixels weighted by mask
    contribute: blur(img * mask) / blur(mask)."""
    img = np.asarray(img, np.float32)
    mask = np.asarray(mask, np.float32)
    m = cv2.GaussianBlur(mask, (0, 0), sigma) + 1e-6
    if img.ndim == 3:
        return cv2.GaussianBlur(img * mask[..., None], (0, 0), sigma) / m[..., None]
    return cv2.GaussianBlur(img * mask, (0, 0), sigma) / m
