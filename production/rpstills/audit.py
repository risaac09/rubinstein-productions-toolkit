"""rpstills.audit: the numbers of a folder of finished stills, and how far each file sits from the set.

    python3 -m rpstills audit <dir> [--out DIR] [--profile NAME] [--hero FILE]

Reads every JPEG and TIFF directly in <dir>, finds faces with landmarks
(Vision, through vision.faces(..., landmarks=True)) and measures each file
on its largest face (by box area, not Vision's first face):

  skin        mean CIELAB over the cheek apples (rpstills.skin; the box
              sample when Vision gave no landmarks): L*, a*, b*, hue, chroma,
              and the Rec.709 vectorscope angle
  consistency against the set's reference: the median of skin L*, a* and b*
              over the measured files, or with --hero FILE that file's own
              skin Lab: d_E00 (CIEDE2000), d_L, d_chroma, and d_hue_deg
              (signed, wrapped)
  neutral     a white or grey garment or card below the face
              (skin.neutral_mask with develop's defaults): its median a*, b*,
              pixel count, and neutral_err, the chroma of that median; None
              when no neutral is found, or when more than NEUTRAL_BLOWN of it
              is clipped in every channel. neutral_clip_pct is the share of
              the mask left out as clipped
  clip_pct    share of pixels with any channel at or above CLIP_AT
  icc         the embedded profile's description, whether it is sRGB, and colour:
              what was done (sRGB profile, no profile taken as sRGB, or converted)
  aspect      width / height, and with --profile its relative error against
              the profile's aspect in stills-config.json
  eye_y       the eye line (mean of both eye centres) from the top, as a share
              of the height; with --profile, its distance to the profile's
              eye_line
  headroom    the crown estimate (HEAD_ABOVE face heights above the box top)
              from the top, as a share of the height; negative is a cut crown.
              It reads low: see HEAD_ABOVE
  face_w      face box width as a share of the image width
  eye_sharp   variance of the Laplacian on the eye box (both eyes, padded by
              EYE_PAD of its width) resized to EYE_BOX_W wide; eye_box_px is
              that box's width in the file. Compare only boxes of similar
              size: one narrower than EYE_BOX_W is upsampled and reads softer
  bg_peak_L   the BG_PCT percentile of L* outside an ellipse round the face
              centre with semi-axes BG_AXES face widths and heights (3.2 x
              2.8 face boxes across) and above the chin (the shoulders below
              are subject); bg_share is that region's share of the frame (a
              tight headshot leaves only its top corners, a few percent)

It writes audit.json (rows, reference, set spread, parameters) and prints a
table with the spread of each metric (sd, min..max). Each row's checks name
the skin metrics past CHECK, working defaults Isaac may change: a check asks a
person to look, it is not a verdict. neutral_err past its CHECK value is
counted on its own summary line, not as a check.
Output names client files, so it is refused inside any git working tree.
Every file is read through inputs.read_upright first: an embedded ICC profile
that is not sRGB (Display P3, Adobe RGB, ProPhoto) is converted to sRGB, a file
with no profile is taken as sRGB, and the EXIF orientation is applied, so the
pixels match what Vision sees. Each row says which (colour, orientation,
orientation_applied).
"""

import json
import os
import sys
import tempfile

import cv2
import numpy as np

from . import inputs, measure, skin, vision

EXTS = (".jpg", ".jpeg", ".tif", ".tiff")
CLIP_AT = 252 / 255
# face heights from the box top to the crown estimate; copied from rpresolve.reframe, not imported (that module
# is the 3.9 video side). The landmarks request's box top sits about 0.2 face heights above the brows, so on
# the stills checked the estimate fell 0.03-0.05 of the frame height above the visible hair: headroom reads low
HEAD_ABOVE = 0.6
EYE_PAD = 0.10       # eye box padding, share of the eye box width, every side
EYE_BOX_W = 400      # eye box width for the sharpness measure, px
BG_AXES = (1.6, 1.4)  # background starts outside this ellipse: semi-axes in face box widths, heights
BG_PCT = 98
# working defaults, not standards: a row's checks name each skin metric whose size is past its value here.
# neutral_err past its value is counted on its own summary line, never as a check: the mask cannot tell a cast on a
# white shirt from a pale coloured shirt or a print's pale motifs, which read their own colour
CHECK = {"d_E00": 3.0, "d_L": 3.0, "d_hue_deg": 5.0, "neutral_err": 2.0}
HUE_MIN_CHROMA = 5.0  # d_hue_deg needs this skin chroma on both sides; nearer grey the hue angle is noise
NEUTRAL_BLOWN = 0.5   # a neutral with more of its mask than this clipped in every channel is not read (the rest is edge)
CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "stills-config.json")

# (row key, table header, format); every numeric row key the spread covers
COLUMNS = [("skin_L", "L*", "{:.1f}"), ("skin_a", "a*", "{:.1f}"), ("skin_b", "b*", "{:.1f}"),
           ("hue_deg", "hue", "{:.1f}"), ("chroma", "C*", "{:.1f}"), ("vectorscope_deg", "scope", "{:.1f}"),
           ("d_E00", "dE00", "{:.1f}"), ("d_L", "dL", "{:+.1f}"), ("d_chroma", "dC*", "{:+.1f}"),
           ("d_hue_deg", "dhue", "{:+.1f}"), ("neutral_a", "neu.a", "{:+.1f}"), ("neutral_b", "neu.b", "{:+.1f}"),
           ("neutral_err", "neu.err", "{:.1f}"), ("neutral_clip_pct", "neu.clip", "{:.1f}"),
           ("clip_pct", "clip%", "{:.2f}"), ("aspect", "aspect", "{:.3f}"), ("aspect_err", "asp.err", "{:+.3f}"),
           ("eye_y", "eyeY", "{:.3f}"), ("eye_y_err", "eye.err", "{:+.3f}"), ("headroom", "head", "{:.3f}"),
           ("face_w", "faceW", "{:.3f}"), ("eye_sharp", "eyeSharp", "{:.0f}"), ("eye_box_px", "eyeBoxPx", "{:.0f}"),
           ("bg_peak_L", "bgL", "{:.1f}")]


def list_images(folder):
    return sorted(os.path.join(folder, n) for n in os.listdir(folder)
                  if n.lower().endswith(EXTS) and not n.startswith(".") and os.path.isfile(os.path.join(folder, n)))


def read_rgb(path):
    """float32 HxWx3 sRGB 0..1 from an 8- or 16-bit file, as stored (no
    EXIF rotation). ValueError when it cannot be read."""
    a = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if a is None or a.dtype not in (np.uint8, np.uint16):
        raise ValueError("unreadable image (8- or 16-bit JPEG or TIFF wanted)")
    if a.ndim == 2:
        a = np.repeat(a[..., None], 3, 2)
    rgb = cv2.cvtColor(np.ascontiguousarray(a[..., :3]), cv2.COLOR_BGR2RGB)
    return rgb.astype(np.float32) / np.iinfo(a.dtype).max


def wrap_deg(d):
    return (d + 180.0) % 360.0 - 180.0


def eye_box(face_px, shape):
    """(x0, y0, x1, y1) px: both eyes, padded by EYE_PAD of their width,
    clipped to the image; None when under 2 px either way."""
    eyes = np.vstack([skin.points(face_px, "leftEye"), skin.points(face_px, "rightEye")])
    (x0, y0), (x1, y1) = eyes.min(0), eyes.max(0)
    pad = EYE_PAD * (x1 - x0)
    H, W = shape[:2]
    x0, y0 = int(max(0, x0 - pad)), int(max(0, y0 - pad))
    x1, y1 = int(min(W, np.ceil(x1 + pad))), int(min(H, np.ceil(y1 + pad)))
    return (x0, y0, x1, y1) if x1 - x0 >= 2 and y1 - y0 >= 2 else None


def eye_sharpness(rgb, face_px):
    box = eye_box(face_px, rgb.shape)
    if box is None:
        return None
    x0, y0, x1, y1 = box
    luma = rgb[y0:y1, x0:x1] @ np.float32(measure.BT709) * 255
    h = max(1, round(EYE_BOX_W * (y1 - y0) / (x1 - x0)))
    interp = cv2.INTER_AREA if x1 - x0 > EYE_BOX_W else cv2.INTER_CUBIC
    small = cv2.resize(luma, (EYE_BOX_W, h), interpolation=interp)
    return float(cv2.Laplacian(small.astype(np.float64), cv2.CV_64F).var())


def background_peak(rgb, face_px):
    H, W = rgb.shape[:2]
    m = np.ones((H, W), np.uint8)
    cx, cy = face_px["x"] + face_px["w"] / 2, face_px["y"] + face_px["h"] / 2
    cv2.ellipse(m, (int(round(cx)), int(round(cy))),
                (max(1, int(round(BG_AXES[0] * face_px["w"]))), max(1, int(round(BG_AXES[1] * face_px["h"])))),
                0, 0, 360, 0, -1)
    m[max(0, int(round(face_px["y"] + face_px["h"]))):] = 0
    px = rgb[m > 0]
    peak = float(np.percentile(measure.lab_image(px)[:, 0], BG_PCT)) if len(px) else None
    return peak, float(m.mean())


def measure_file(path, rgb, det, profile=None, info=None):
    """One row of numbers for an upright sRGB image (inputs.read_upright) with
    its info, and its Vision result."""
    H, W = rgb.shape[:2]
    info = info or {}
    row = {"file": os.path.basename(path), "width": W, "height": H, "aspect": W / H,
           "icc": info.get("icc"), "srgb_icc": bool(info.get("srgb_icc")), "colour": info.get("colour"),
           "orientation": info.get("orientation", 1), "orientation_applied": bool(info.get("orientation_applied")),
           "clip_pct": float((rgb.max(2) >= CLIP_AT - 1e-6).mean() * 100)}
    if profile is not None:
        want = profile.get("aspect")
        row["aspect_want"] = want[0] / want[1] if want else None
        row["aspect_err"] = W / H / row["aspect_want"] - 1 if want else None
    if not det or det.get("error"):
        row["error"] = (det or {}).get("error") or "Vision returned nothing for this file"
        return row
    if (det.get("width"), det.get("height")) != (W, H):
        row["error"] = f"Vision saw {det.get('width')}x{det.get('height')}, the upright file is {W}x{H}"
        return row
    row["faces"] = len(det.get("faces") or [])
    if not row["faces"]:
        row["error"] = "no face"
        return row
    face = max(det["faces"], key=lambda f: f["w"] * f["h"])
    fp = skin.to_pixels(face, W, H)
    has_marks = all((face.get("landmarks") or {}).get(k) for k in ("leftEye", "rightEye", "outerLips"))
    region = "cheek" if has_marks else "box"
    try:
        s = measure.skin_lab(rgb, fp, region)
    except ValueError as e:
        s = None
        row["skin_error"] = str(e)
    if s:
        row.update({"skin_region": region, "skin_L": s["L"], "skin_a": s["a"], "skin_b": s["b"],
                    "hue_deg": s["hue_deg"], "chroma": s["chroma"], "vectorscope_deg": s["vectorscope_deg"],
                    "skin_n_px": s["n_px"]})
    mask = skin.neutral_mask(rgb, fp)
    n = None
    if mask is not None:
        clipped = rgb[mask] >= CLIP_AT - 1e-6
        row["neutral_clip_pct"] = float(clipped.any(1).mean() * 100)
        if clipped.all(1).mean() > NEUTRAL_BLOWN:
            row["neutral_note"] = "blown: the unclipped rest is the garment's edge, not a read of it"
        else:
            n = measure.neutral_lab(rgb, mask)
    row.update({"neutral_a": n and n["a"], "neutral_b": n and n["b"], "neutral_n_px": n and n["n_px"],
                "neutral_err": n and n["chroma"]})
    row["face_w"] = fp["w"] / W
    row["headroom"] = (fp["y"] - HEAD_ABOVE * fp["h"]) / H
    if has_marks:
        row["eye_y"] = float(np.mean([c[1] for c in skin.eye_centres(fp)])) / H
        row["eye_sharp"] = eye_sharpness(rgb, fp)
        box = eye_box(fp, rgb.shape)
        row["eye_box_px"] = box[2] - box[0] if box else None
        if profile is not None and profile.get("eye_line") is not None:
            row["eye_y_err"] = row["eye_y"] - profile["eye_line"]
    row["bg_peak_L"], row["bg_share"] = background_peak(rgb, fp)
    return {k: round(v, 4) if isinstance(v, float) else v for k, v in row.items()}


def spread(rows):
    """{metric: {"n", "mean", "sd", "min", "max", "range"}} over rows that have it."""
    out = {}
    for key, _, _ in COLUMNS:
        v = np.array([r[key] for r in rows if r.get(key) is not None], float)
        if len(v):
            out[key] = {"n": int(len(v)), "mean": round(float(v.mean()), 3), "sd": round(float(v.std()), 3),
                        "min": round(float(v.min()), 3), "max": round(float(v.max()), 3),
                        "range": round(float(np.ptp(v)), 3)}
    return out


def reference(rows, hero=None):
    """{"source", "file", "L", "a", "b", "n"}: the median skin Lab of the
    measured rows, or the hero row's own; None when nothing was measured.
    ValueError when the hero has no skin measure."""
    if hero is not None:
        r = next((r for r in rows if r["file"] == hero), None)
        if r is None or r.get("skin_L") is None:
            why = "not among the files" if r is None else r.get("error") or r.get("skin_error")
            raise ValueError(f"--hero {hero}: no skin measure ({why})")
        return {"source": "hero", "file": hero, "L": r["skin_L"], "a": r["skin_a"], "b": r["skin_b"], "n": 1}
    lab = np.array([[r["skin_L"], r["skin_a"], r["skin_b"]] for r in rows if r.get("skin_L") is not None])
    if not len(lab):
        return None
    L, a, b = (round(float(v), 4) for v in np.median(lab, 0))
    return {"source": "median", "file": None, "L": L, "a": a, "b": b, "n": len(lab)}


def deviations(row, ref):
    """The row's skin against the reference: d_E00, d_L, d_chroma, d_hue_deg
    (None when either chroma is under HUE_MIN_CHROMA)."""
    lab = (row["skin_L"], row["skin_a"], row["skin_b"])
    c, c_ref = float(measure.chroma(*lab[1:])), float(measure.chroma(ref["a"], ref["b"]))
    hue = wrap_deg(measure.hue_deg(*lab[1:]) - measure.hue_deg(ref["a"], ref["b"]))
    return {"d_E00": round(float(measure.delta_e2000((ref["L"], ref["a"], ref["b"]), lab)), 4),
            "d_L": round(lab[0] - ref["L"], 4), "d_chroma": round(c - c_ref, 4),
            "d_hue_deg": round(float(hue), 4) if min(c, c_ref) >= HUE_MIN_CHROMA else None}


def build(paths, detections, profile_name=None, profile=None, hero=None):
    """The audit document for image paths and vision.faces(..., landmarks=True)
    output; hero, one of paths, replaces the set median as the reference."""
    rows = []
    for p in paths:
        try:
            rgb, info = inputs.read_upright(p)
        except ValueError as e:
            rows.append({"file": os.path.basename(p), "error": str(e)})
            continue
        rows.append(measure_file(p, rgb, detections.get(p), profile, info))
    ref = reference(rows, hero and os.path.basename(hero))
    for r in rows:
        if ref and r.get("skin_L") is not None:
            r.update(deviations(r, ref))
        if "error" not in r:
            r["checks"] = [k for k, v in CHECK.items()
                           if k != "neutral_err" and r.get(k) is not None and abs(r[k]) > v]
    return {"tool": "rpstills audit", "files": rows, "reference": ref, "spread": spread(rows),
            "profile": profile_name,
            "profile_spec": ({"aspect": profile.get("aspect"), "eye_line": profile.get("eye_line")}
                             if profile is not None else None),
            "params": {"clip_at": CLIP_AT, "head_above": HEAD_ABOVE, "eye_pad": EYE_PAD, "eye_box_w": EYE_BOX_W,
                       "bg_axes": list(BG_AXES), "bg_pct": BG_PCT, "cheek_at": skin.CHEEK_AT,
                       "cheek_radii": list(skin.CHEEK_RADII), "face": "largest box area", "check": CHECK,
                       "hue_min_chroma": HUE_MIN_CHROMA,
                       "neutral": {"min_L": skin.NEUTRAL_MIN_L, "max_chroma": skin.NEUTRAL_MAX_CHROMA,
                                   "min_share": skin.NEUTRAL_MIN_SHARE, "blown": NEUTRAL_BLOWN}},
            "note": "A check names a skin metric past its working default (params.check); it asks a person to look "
                    "and is not a verdict. neutral_err past params.check is counted in the summary, not as a check."}


def write(out_dir, doc):
    """audit.json in out_dir, atomically. ValueError when out_dir may not take it."""
    from rpresolve.paths import out_problem  # lazy: rpresolve is stdlib-only, imported only to write
    path = os.path.join(out_dir, "audit.json")
    problem = out_problem(path, any_git_tree=True)
    if problem:
        raise ValueError(problem)
    os.makedirs(out_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=".audit-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(doc, f, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
    return path


def _cell(v, fmt):
    return "-" if v is None else fmt.format(v)


def table(doc):
    cols = [c for c in COLUMNS if any(r.get(c[0]) is not None for r in doc["files"])]
    width = max([4] + [len(r["file"]) for r in doc["files"]])
    lines = [f"{'file':<{width}} " + " ".join(f"{h:>8}" for _, h, _ in cols) + "  sRGB"]
    for r in doc["files"]:
        cells = " ".join(f"{_cell(r.get(k), fmt):>8}" for k, _, fmt in cols)
        tail = "yes" if r.get("srgb_icc") else ("converted" if r.get("icc") else "none")
        lines.append(f"{r['file']:<{width}} {cells}  {tail}" + (f"  [{r['error']}]" if r.get("error") else "")
                     + (f"  check: {', '.join(r['checks'])}" if r.get("checks") else ""))
    for k, h, fmt in cols:
        s = doc["spread"].get(k)
        if s:
            lines.append(f"spread {h:<8} sd {s['sd']:.3f}  range {_cell(s['min'], fmt)}..{_cell(s['max'], fmt)}"
                         f"  (n {s['n']})")
    ref = doc.get("reference")
    if ref:
        lines.append(("reference hero " + ref["file"] if ref["source"] == "hero" else f"reference median of {ref['n']}")
                     + f": L* {ref['L']:.1f} a* {ref['a']:.1f} b* {ref['b']:.1f}")
    return "\n".join(lines)


def summary(doc):
    rows = doc["files"]
    measured = sum(1 for r in rows if r.get("skin_L") is not None)
    text = f"{len(rows)} files, skin measured on {measured}"
    s = doc["spread"].get("vectorscope_deg")
    if s:
        text += f", vectorscope {s['min']:.1f}..{s['max']:.1f} deg (sd {s['sd']:.2f})"
    no_icc = sum(1 for r in rows if "srgb_icc" in r and not r["srgb_icc"])
    errors = sum(1 for r in rows if r.get("error"))
    checked = [k for r in rows for k in r.get("checks", [])]
    flagged = sum(1 for r in rows if r.get("checks"))
    on = ", ".join(f"{k} {checked.count(k)}" for k in CHECK if k in checked)
    neutral = [r.get("neutral_err") for r in rows if "error" not in r]
    read = [v for v in neutral if v is not None]
    cast = sum(1 for v in read if v > CHECK["neutral_err"])
    turned = sum(1 for r in rows if r.get("orientation_applied"))
    converted = sum(1 for r in rows if r.get("icc") and not r.get("srgb_icc"))
    return (text + f", {no_icc} without an sRGB profile ({converted} converted to sRGB, the rest taken as sRGB), "
            f"{turned} turned upright by EXIF, {errors} not measured; {flagged} flagged"
            + (f" ({on})" if on else "") + "; checks are working defaults, not verdicts"
            + f"; neutral read on {len(read)}, {cast} over neu.err {CHECK['neutral_err']:g} (a coloured or printed "
              f"garment reads its own colour), {len(neutral) - len(read)} without a neutral")


def add_arguments(p):
    p.add_argument("dir", help="folder of finished JPEG or TIFF files")
    p.add_argument("--out", help="folder for audit.json (default: the audited folder); never inside a git tree")
    p.add_argument("--profile", help="a stills-config.json profile to compare aspect and eye line against")
    p.add_argument("--hero", metavar="FILE", help="a file in the folder whose skin Lab is the reference "
                                                  "(default: the set median)")


def run(args):
    src = os.path.abspath(args.dir)
    if not os.path.isdir(src):
        print(f"rpstills audit: {args.dir} is not a folder", file=sys.stderr)
        return 1
    paths = list_images(src)
    if not paths:
        print(f"rpstills audit: no JPEG or TIFF in {args.dir}", file=sys.stderr)
        return 1
    profile = None
    if args.profile:
        with open(CONFIG) as f:
            profiles = json.load(f)["profiles"]
        if args.profile not in profiles:
            print(f"rpstills audit: no profile {args.profile!r}; known: {', '.join(sorted(profiles))}", file=sys.stderr)
            return 1
        profile = profiles[args.profile]
    hero = None
    if args.hero:
        # by file identity: a symlinked folder (/tmp on macOS, a mounted drive) or another letter case names it too
        given = [c for c in (args.hero, os.path.join(src, args.hero)) if os.path.isfile(c)]
        hero = next((p for p in paths if any(os.path.samefile(c, p) for c in given)), None)
        if hero is None:
            print(f"rpstills audit: --hero {args.hero} is not a JPEG or TIFF in {args.dir}", file=sys.stderr)
            return 1
    out = os.path.abspath(args.out or src)
    from rpresolve.paths import out_problem
    problem = out_problem(os.path.join(out, "audit.json"), any_git_tree=True)
    if problem:
        print(f"rpstills audit: {problem}", file=sys.stderr)
        return 1
    det = vision.faces(paths, landmarks=True)
    if det is None:
        print("rpstills audit: the Vision helper cannot run (it needs macOS and swiftc)", file=sys.stderr)
        return 1
    try:
        doc = build(paths, det, args.profile, profile, hero)
    except ValueError as e:
        print(f"rpstills audit: {e}", file=sys.stderr)
        return 1
    print(table(doc))
    print(write(out, doc))
    print(summary(doc))
    return 0
