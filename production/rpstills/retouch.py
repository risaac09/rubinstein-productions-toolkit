"""rpstills.retouch: opt-in portrait retouch of one face, from its landmarks.

    rgb, report = retouch.apply(rgb, face_px)                # every step, in STEPS order
    rgb, report = retouch.evenness(rgb, face_px)             # or one step

rgb is float32 sRGB 0..1, HxWx3 (what audit.read_rgb returns); face_px is a
face from vision.faces(..., landmarks=True) put through skin.to_pixels.
Every step has one shape, fn(rgb, face_px, **params) -> (rgb, report): it
returns a new array, leaves every pixel it does not touch bit-identical,
and reports what it touched. Sizes scale from the face box width w;
strengths are the module constants below, and params() echoes them into
finish.json.

  heal_spots  a dark spot is where the small blur sits SPOT_THRESHOLD (sRGB
              luma) below the wide blur, on eroded skin, no larger than
              (w/SPOT_AREA_DIV)^2 px; each is replaced by the wide skin blur.
              Never inside the eyes, brows or lips (dilated by skin.EYE_HOLE).
  evenness    band-pass on the skin mask (rpstills.skin): takes EVEN_STRENGTH
              of the mid band (mask-normalised Gaussians at w/SIGMA_SMALL_DIV
              and w/SIGMA_LARGE_DIV) and EVEN_FINE of the band below it, so
              blotches even out and pores stay. It fades out toward the mask
              edge, where the wide blur sees skin on one side only.
  eyes        inside the eye polygons: x EYE_LIFT and EYE_CLARITY of an
              unsharp mask at w/EYE_CLARITY_DIV.
  under_eye   the under-eye ellipses on skin, where darker than the cheek's
              UNDER_EYE_PCT percentile, lifted toward it by at most
              UNDER_EYE_MAX_LIFT, mixed at UNDER_EYE_MIX.
  teeth       inside the inner lips only, bright low-saturation pixels:
              saturation x (1 + TEETH_SAT), value x (1 + TEETH_VALUE).
  deglare     spectacle lenses: inside each lens ellipse (not the eye), where
              the image is brighter than the cheek by GLARE_DL and has lost
              GLARE_DC of the cheek's colour (chroma along the cheek's a*, b*
              direction: a veil of sky or window washes skin toward neutral
              or blue, while bare forehead keeps its redness), the
              low-frequency veil is subtracted and a*, b* pulled back to the
              cheek, all the way wherever the weight reaches GLARE_AB_FULL
              (a partial pull from blue toward skin would stop on pink). It
              dims a veil of sky or window; it cannot bring back what an
              opaque reflection hides.

apply() runs the steps on the face's region only (the box padded by ROI_PAD
of its larger side), so a 24 MP frame does not take a dozen full-frame
float copies, and computes the skin mask once, before any step.

Tuned on one subject, with glasses, under overcast light, on 6000 x 4000
frames; not validated on other skin tones, light or lenses. Nothing in
rpstills retouches unless asked.
"""

import cv2
import numpy as np

from . import skin

STEPS = ("heal_spots", "evenness", "eyes", "under_eye", "teeth", "deglare")  # heal first: it finds spots before evenness softens them
ROI_PAD = 0.6               # apply(): the face box padded by this share of its larger side, every edge
LUMA = (0.2126, 0.7152, 0.0722)  # Rec.709 weights on the encoded values

SIGMA_SMALL_DIV = 450       # evenness: small blur sigma = w / SIGMA_SMALL_DIV
SIGMA_LARGE_DIV = 55        # evenness: large blur sigma = w / SIGMA_LARGE_DIV
EVEN_STRENGTH = 0.30        # share of the mid band taken off
EVEN_FINE = 0.05            # share of the finest band taken off (pores stay)

SPOT_THRESHOLD = 0.09       # heal: small blur this far below the wide blur (luma 0..1) is a dark spot
SPOT_AREA_DIV = 70          # heal: a spot is at most (w / SPOT_AREA_DIV)^2 px; larger is a feature, kept
SPOT_ERODE_DIV = 90         # heal: spots only on skin eroded by w / SPOT_ERODE_DIV px
SPOT_DILATE_DIV = 120       # heal: each spot grown by w / SPOT_DILATE_DIV px
SPOT_FEATHER_DIV = 340      # heal: and feathered by a Gaussian of w / SPOT_FEATHER_DIV

EYE_LIFT = 1.08             # eyes: brightness gain inside the eye polygons
EYE_CLARITY = 0.4           # eyes: unsharp-mask amount
EYE_CLARITY_DIV = 150       # eyes: unsharp-mask sigma = w / EYE_CLARITY_DIV
EYE_PAD = 0.01              # eyes: polygons dilated by this share of w
EYE_FEATHER_DIV = 200       # eyes: feather sigma = w / EYE_FEATHER_DIV

UNDER_EYE_MAX_LIFT = 1.12   # under-eye: at most +12 %
UNDER_EYE_MIX = 0.6         # under-eye: share of the lift applied
UNDER_EYE_PCT = 60          # under-eye: lifted toward this percentile of cheek luma
UNDER_EYE_FEATHER_DIV = 60  # under-eye: feather sigma = w / UNDER_EYE_FEATHER_DIV

TEETH_SAT = -0.35           # teeth: saturation change
TEETH_VALUE = 0.04          # teeth: value change
TEETH_MIN_V = 0.45          # teeth: only pixels at least this bright (HSV value)
TEETH_MAX_S = 0.45          # teeth: and at most this saturated
TEETH_FEATHER_DIV = 400     # teeth: feather sigma = w / TEETH_FEATHER_DIV, kept inside the inner lips

GLARE_DL = 4.0              # deglare: L* above the cheek before a pixel counts as glare
GLARE_DL_RANGE = 12.0       # deglare: L* over which the glare weight ramps to full
GLARE_DC = 6.0              # deglare: skin chroma lost (along the cheek's a*, b*) before it counts
GLARE_DC_RANGE = 10.0       # deglare: chroma loss over which that ramps
GLARE_GAIN = 1.6            # deglare: weight gain before the clip to 1
GLARE_FLOOR = 3.0           # deglare: the veil is taken down to the cheek L* minus this
GLARE_EYE_PAD = 0.02        # deglare: the eyes, dilated by this share of w, are left alone
GLARE_FEATHER_DIV = 250     # deglare: weight feathered by w / GLARE_FEATHER_DIV
GLARE_LOW_DIV = 200         # deglare: the veil is the Lab blurred by w / GLARE_LOW_DIV
GLARE_MIN = 0.2             # deglare: no pixel weighs this much -> no glare, nothing done
GLARE_TOUCHED = 0.3         # deglare: weight above this counts toward the area reported
GLARE_AB_FULL = 0.3         # deglare: a*, b* go all the way to the cheek's where the weight reaches this


def params():
    """Every constant, for finish.json."""
    return {"steps": list(STEPS), "roi_pad": ROI_PAD,
            "evenness": {"sigma_small": f"w/{SIGMA_SMALL_DIV}", "sigma_large": f"w/{SIGMA_LARGE_DIV}",
                         "strength": EVEN_STRENGTH, "fine": EVEN_FINE},
            "heal_spots": {"threshold": SPOT_THRESHOLD, "max_area": f"(w/{SPOT_AREA_DIV})^2",
                           "erode": f"w/{SPOT_ERODE_DIV}", "dilate": f"w/{SPOT_DILATE_DIV}",
                           "feather": f"w/{SPOT_FEATHER_DIV}", "excluded": "eyes, brows, lips"},
            "eyes": {"lift": EYE_LIFT, "clarity": EYE_CLARITY, "clarity_sigma": f"w/{EYE_CLARITY_DIV}",
                     "pad": EYE_PAD, "feather": f"w/{EYE_FEATHER_DIV}"},
            "under_eye": {"max_lift": UNDER_EYE_MAX_LIFT, "mix": UNDER_EYE_MIX, "cheek_pct": UNDER_EYE_PCT,
                          "feather": f"w/{UNDER_EYE_FEATHER_DIV}"},
            "teeth": {"sat": TEETH_SAT, "value": TEETH_VALUE, "min_v": TEETH_MIN_V, "max_s": TEETH_MAX_S,
                      "region": "inner lips"},
            "deglare": {"dl": GLARE_DL, "dl_range": GLARE_DL_RANGE, "dc": GLARE_DC, "dc_range": GLARE_DC_RANGE,
                        "gain": GLARE_GAIN, "floor": GLARE_FLOOR, "low": f"w/{GLARE_LOW_DIV}",
                        "min_weight": GLARE_MIN, "touched_weight": GLARE_TOUCHED, "ab_full_weight": GLARE_AB_FULL}}


def luma(rgb):
    return rgb @ np.float32(LUMA)


def to_u8(rgb):
    return (np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8)


def _skin(rgb, face_px, mask):
    return skin.skin_mask(face_px, to_u8(rgb)) if mask is None else mask


def _odd(n):
    return max(3, int(n) | 1)


def evenness(rgb, face_px, strength=EVEN_STRENGTH, fine=EVEN_FINE, mask=None):
    """Even out mid-frequency blotchiness on the skin, keep fine texture."""
    w = face_px["w"]
    m = _skin(rgb, face_px, mask)
    hard = (m > 0.5).astype(np.float32)
    lo_s = skin.nblur(rgb, hard, w / SIGMA_SMALL_DIV)
    lo_l = skin.nblur(rgb, hard, w / SIGMA_LARGE_DIV)
    # near the mask edge the wide blur sees skin on one side only and would lift the shadowed edge into
    # a light rim; fade the band-pass out where less than all of the wide neighbourhood is skin
    weight = m * np.clip(2 * cv2.GaussianBlur(hard, (0, 0), w / SIGMA_LARGE_DIV) - 1, 0, 1)
    out = rgb - weight[..., None] * (strength * (lo_s - lo_l) + fine * (rgb - lo_s))
    return np.clip(out, 0, 1).astype(np.float32), {"strength": strength, "fine": fine,
                                                   "skin_px": int((m > 0.5).sum())}


def _features(face_px, shape):
    """bool HxW: eyes, brows and lips, dilated by skin.EYE_HOLE of the face width."""
    d = int(skin.EYE_HOLE * face_px["w"])
    out = np.zeros(shape[:2], bool)
    for name in ("leftEye", "rightEye", "leftEyebrow", "rightEyebrow", "outerLips"):
        out |= skin.poly(shape, skin.points(face_px, name), d)
    return out


def heal_spots(rgb, face_px, threshold=SPOT_THRESHOLD, mask=None):
    """Replace small dark spots on the skin with the surrounding skin tone."""
    w = face_px["w"]
    m = _skin(rgb, face_px, mask)
    hard = (m > 0.5).astype(np.float32)
    lo_s = skin.nblur(rgb, hard, w / SIGMA_SMALL_DIV)
    lo_l = skin.nblur(rgb, hard, w / SIGMA_LARGE_DIV)
    core = cv2.erode(hard, np.ones((_odd(w / SPOT_ERODE_DIV),) * 2, np.uint8)) > 0
    features = _features(face_px, rgb.shape)
    spots = ((luma(lo_l) - luma(lo_s)) > threshold) & core & ~features
    n, labels, stats, _ = cv2.connectedComponentsWithStats(spots.astype(np.uint8), connectivity=8)
    max_area = (w / SPOT_AREA_DIV) ** 2
    small = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] <= max_area]
    report = {"threshold": threshold, "max_area_px": round(max_area, 1), "spots": len(small),
              "kept_large": n - 1 - len(small), "healed_px": 0}
    if not small:
        return rgb.copy(), report
    keep = np.isin(labels, small).astype(np.uint8)
    grown = cv2.dilate(keep, np.ones((_odd(w / SPOT_DILATE_DIV),) * 2, np.uint8)).astype(np.float32)
    heal = cv2.GaussianBlur(grown, (0, 0), w / SPOT_FEATHER_DIV) * ~features
    out = rgb * (1 - heal[..., None]) + lo_l * heal[..., None]
    report["healed_px"] = int(keep.sum())
    return np.clip(out, 0, 1).astype(np.float32), report


def eyes(rgb, face_px, lift=EYE_LIFT, clarity=EYE_CLARITY):
    """Lift the eyes and add a little clarity, inside the eye polygons only."""
    w = face_px["w"]
    d = int(EYE_PAD * w)
    region = skin.poly(rgb.shape, skin.points(face_px, "leftEye"), d) | \
        skin.poly(rgb.shape, skin.points(face_px, "rightEye"), d)
    weight = cv2.GaussianBlur(region.astype(np.float32), (0, 0), w / EYE_FEATHER_DIV)
    clear = rgb + clarity * (rgb - cv2.GaussianBlur(rgb, (0, 0), w / EYE_CLARITY_DIV))
    out = rgb * (1 - weight[..., None]) + np.clip(clear * lift, 0, 1) * weight[..., None]
    return np.clip(out, 0, 1).astype(np.float32), {"lift": lift, "clarity": clarity,
                                                   "eye_px": int(region.sum())}


def under_eye(rgb, face_px, max_lift=UNDER_EYE_MAX_LIFT, mix=UNDER_EYE_MIX, mask=None):
    """Lift the skin under the eyes toward the cheek, where it is darker."""
    w = face_px["w"]
    m = _skin(rgb, face_px, mask)
    area = skin.region_masks(face_px, rgb.shape)["under_eye"]
    weight = cv2.GaussianBlur(area.astype(np.float32) * m, (0, 0), w / UNDER_EYE_FEATHER_DIV)
    y = luma(rgb)
    cheeks = skin.cheek_mask(face_px, rgb.shape)
    report = {"max_lift": max_lift, "mix": mix, "px": int((weight > 0.05).sum())}
    if not cheeks.any():
        report["skipped"] = "the cheek sample is outside the image"
        return rgb.copy(), report
    target = float(np.percentile(y[cheeks], UNDER_EYE_PCT))
    gain = 1 + (np.clip(target / np.maximum(y, 1e-3), 1, max_lift) - 1) * mix * weight
    out = rgb * gain[..., None]
    core = weight > 0.5
    report.update(cheek_luma=round(target, 4),
                  mean_gain=round(float(gain[core].mean()), 4) if core.any() else 1.0,
                  max_gain=round(float(gain.max()), 4))
    return np.clip(out, 0, 1).astype(np.float32), report


def teeth(rgb, face_px, sat=TEETH_SAT, value=TEETH_VALUE):
    """Inside the inner lips, take some yellow out of bright, pale pixels."""
    w = face_px["w"]
    inner = skin.region_masks(face_px, rgb.shape)["lips_inner"]  # inner lips out to the mouth corners
    hsv = cv2.cvtColor(np.ascontiguousarray(np.clip(rgb, 0, 1), np.float32), cv2.COLOR_RGB2HSV)
    pick = inner & (hsv[..., 2] > TEETH_MIN_V) & (hsv[..., 1] < TEETH_MAX_S)
    weight = cv2.GaussianBlur(pick.astype(np.float32), (0, 0), max(0.5, w / TEETH_FEATHER_DIV)) * inner
    report = {"sat": sat, "value": value, "px": int(pick.sum())}
    if not pick.any():
        return rgb.copy(), report
    hsv[..., 1] *= 1 + sat * weight
    hsv[..., 2] *= 1 + value * weight
    done = np.clip(cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB), 0, 1)
    return np.where((weight > 0)[..., None], done, rgb).astype(np.float32), report


def deglare(rgb, face_px):
    """Dim the bright, cool veil a spectacle lens reflects, inside the lens
    ellipses. Reports the area touched; does nothing when no pixel is glare."""
    w = face_px["w"]
    regions = skin.region_masks(face_px, rgb.shape)
    lens = regions["lens"]
    cheeks = skin.cheek_mask(face_px, rgb.shape)
    report = {"lens_px": int(lens.sum()), "touched_px": 0, "touched_share_of_lens": 0.0, "mean_dL": 0.0,
              "applied": False}
    if not lens.any() or not cheeks.any():
        report["skipped"] = "no lens or cheek area inside the image"
        return rgb.copy(), report
    lab = cv2.cvtColor(np.ascontiguousarray(np.clip(rgb, 0, 1), np.float32), cv2.COLOR_RGB2LAB)
    ref = np.median(lab[cheeks], 0)
    d = int(GLARE_EYE_PAD * w)
    eye = skin.poly(rgb.shape, skin.points(face_px, "leftEye"), d) | \
        skin.poly(rgb.shape, skin.points(face_px, "rightEye"), d)
    bright = np.clip((lab[..., 0] - ref[0] - GLARE_DL) / GLARE_DL_RANGE, 0, 1)
    # b* alone (the first version) also took bright forehead above the frame, which is a little cooler
    # than the cheek but keeps its a*; the loss of the cheek's own chroma separates veil from skin
    loss = ((ref[1] - lab[..., 1]) * ref[1] + (ref[2] - lab[..., 2]) * ref[2]) / max(1e-3, float(np.hypot(*ref[1:])))
    washed = np.sqrt(np.clip((loss - GLARE_DC) / GLARE_DC_RANGE, 0, 1))
    g = (lens & ~eye) * np.clip(GLARE_GAIN * bright * washed, 0, 1)
    g = cv2.GaussianBlur(g.astype(np.float32), (0, 0), w / GLARE_FEATHER_DIV)
    if g.max() < GLARE_MIN:
        return rgb.copy(), report
    lo = cv2.GaussianBlur(lab, (0, 0), w / GLARE_LOW_DIV)
    dl = g * np.clip(lo[..., 0] - ref[0] + GLARE_FLOOR, 0, None)
    lab[..., 0] -= dl
    # a*, b* at full strength past GLARE_AB_FULL: a partial straight-line pull from blue to skin stops on pink
    lab[..., 1:] += np.clip(g / GLARE_AB_FULL, 0, 1)[..., None] * (ref[1:] - lo[..., 1:])
    done = np.clip(cv2.cvtColor(lab, cv2.COLOR_LAB2RGB), 0, 1)
    touched = g > GLARE_TOUCHED
    report.update(applied=True, touched_px=int(touched.sum()),
                  touched_share_of_lens=round(float(touched.sum() / max(1, lens.sum())), 4),
                  mean_dL=round(float(dl[touched].mean()), 2) if touched.any() else 0.0,
                  cheek_lab=[round(float(v), 2) for v in ref])
    return np.where((g > 0)[..., None], done, rgb).astype(np.float32), report


def shift(face_px, dx, dy):
    """face_px moved by (dx, dy) pixels, landmarks included."""
    out = dict(face_px, x=face_px["x"] + dx, y=face_px["y"] + dy)
    if face_px.get("landmarks"):
        out["landmarks"] = {k: (np.asarray(v, float) + [dx, dy]).tolist() for k, v in face_px["landmarks"].items()}
    return out


def roi(face_px, shape, pad=ROI_PAD):
    """(x0, y0, x1, y1): the face box padded by pad of its larger side, inside the image."""
    H, W = shape[:2]
    p = pad * max(face_px["w"], face_px["h"])
    x0, y0 = max(0, int(face_px["x"] - p)), max(0, int(face_px["y"] - p))
    x1 = min(W, int(np.ceil(face_px["x"] + face_px["w"] + p)))
    y1 = min(H, int(np.ceil(face_px["y"] + face_px["h"] + p)))
    return x0, y0, x1, y1


def apply(rgb, face_px, steps=STEPS):
    """Run steps (names from STEPS, in the order given) on the face's region.
    Returns (rgb, {"roi": [x, y, w, h], "steps": {name: report}}). ValueError
    for an unknown step, a face without the landmarks a step needs, or a face
    outside the image."""
    unknown = [s for s in steps if s not in STEPS]
    if unknown:
        raise ValueError(f"unknown retouch step(s) {unknown}; known: {', '.join(STEPS)}")
    x0, y0, x1, y1 = roi(face_px, rgb.shape)
    if x1 - x0 < 8 or y1 - y0 < 8:
        raise ValueError("the face is outside the image")
    sub = np.ascontiguousarray(rgb[y0:y1, x0:x1], np.float32)
    fp = shift(face_px, -x0, -y0)
    mask = skin.skin_mask(fp, to_u8(sub)) if {"evenness", "heal_spots", "under_eye"} & set(steps) else None
    reports = {}
    for name in steps:
        fn = globals()[name]
        sub, reports[name] = fn(sub, fp, mask=mask) if name in ("evenness", "heal_spots", "under_eye") \
            else fn(sub, fp)
    out = np.array(rgb, np.float32, copy=True)
    out[y0:y1, x0:x1] = sub
    return out, {"roi": [x0, y0, x1 - x0, y1 - y0], "steps": reports}
