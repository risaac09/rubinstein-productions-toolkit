"""rpstills.audit: the numbers of a folder of finished stills, report only.

    python3 -m rpstills audit <dir> [--out DIR] [--profile NAME]

Reads every JPEG and TIFF directly in <dir>, finds faces with landmarks
(Vision, through vision.faces(..., landmarks=True)) and measures each file
on its largest face (by box area, not Vision's first face):

  skin        mean CIELAB over the cheek apples (rpstills.skin; the box
              sample when Vision gave no landmarks): L*, a*, b*, hue, chroma,
              and the Rec.709 vectorscope angle, with its signed distance to
              two reference angles (REFERENCES): the stills trial target
              (117.7, cheek Lab 64/15/14) and the scope's skin-tone line (123)
  clip_pct    share of pixels with any channel at or above CLIP_AT
  icc         the embedded profile's description, and whether it is sRGB
  aspect      width / height, and with --profile its relative error against
              the profile's aspect in stills-config.json
  eye_y       the eye line (mean of both eye centres) from the top, as a share
              of the height; with --profile, its distance to the profile's
              eye_line
  headroom    the crown estimate (HEAD_ABOVE face heights above the box top)
              from the top, as a share of the height; negative is a cut crown
  face_w      face box width as a share of the image width
  eye_sharp   variance of the Laplacian on the eye box (both eyes, padded by
              EYE_PAD of its width) resized to EYE_BOX_W wide
  bg_peak_L   the BG_PCT percentile of L* outside the face ellipse scaled by
              BG_AXES and above the chin (the shoulders below are subject);
              bg_share is that region's share of the frame (a tight
              headshot leaves only its top corners, a few percent)

It writes audit.json (rows, set spread, parameters) and prints a table with
the spread of each metric (sd, min..max). There is no PASS or FAIL: nobody
has set the ranges yet, and made-up thresholds would read as a standard.
Output names client files, so it is refused inside any git working tree.
Images are measured as stored; pass upright files.
"""

import io
import json
import os
import sys
import tempfile

import cv2
import numpy as np

from . import measure, skin, vision

EXTS = (".jpg", ".jpeg", ".tif", ".tiff")
CLIP_AT = 252 / 255
HEAD_ABOVE = 0.6     # face heights from the box top (the brows) to the crown; copied from rpresolve.reframe, not imported (that module is the 3.9 video side)
EYE_PAD = 0.10       # eye box padding, share of the eye box width, every side
EYE_BOX_W = 400      # eye box width for the sharpness measure, px
BG_AXES = (1.6, 1.4)  # background starts outside this ellipse: semi-axes in face box widths, heights
BG_PCT = 98
REFERENCES = {"trial": 117.7, "skin_line": 123.0}  # vectorscope degrees; distances, not verdicts
CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "stills-config.json")

# (row key, table header, format); every numeric row key the spread covers
COLUMNS = [("skin_L", "L*", "{:.1f}"), ("skin_a", "a*", "{:.1f}"), ("skin_b", "b*", "{:.1f}"),
           ("hue_deg", "hue", "{:.1f}"), ("chroma", "C*", "{:.1f}"), ("vectorscope_deg", "scope", "{:.1f}"),
           ("d_trial_deg", "d117.7", "{:+.1f}"), ("d_skin_line_deg", "d123", "{:+.1f}"),
           ("clip_pct", "clip%", "{:.2f}"), ("aspect", "aspect", "{:.3f}"), ("aspect_err", "asp.err", "{:+.3f}"),
           ("eye_y", "eyeY", "{:.3f}"), ("eye_y_err", "eye.err", "{:+.3f}"), ("headroom", "head", "{:.3f}"),
           ("face_w", "faceW", "{:.3f}"), ("eye_sharp", "eyeSharp", "{:.0f}"), ("bg_peak_L", "bgL", "{:.1f}")]


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


def icc_name(path):
    """The embedded ICC profile's description, or None."""
    from PIL import Image, ImageCms
    try:
        with Image.open(path) as im:
            icc = im.info.get("icc_profile")
        if not icc:
            return None
        return ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))).strip() or "unnamed"
    except (OSError, ImageCms.PyCMSError):
        return None


def wrap_deg(d):
    return (d + 180.0) % 360.0 - 180.0


def eye_sharpness(rgb, face_px):
    eyes = np.vstack([skin.points(face_px, "leftEye"), skin.points(face_px, "rightEye")])
    (x0, y0), (x1, y1) = eyes.min(0), eyes.max(0)
    pad = EYE_PAD * (x1 - x0)
    H, W = rgb.shape[:2]
    x0, y0 = int(max(0, x0 - pad)), int(max(0, y0 - pad))
    x1, y1 = int(min(W, np.ceil(x1 + pad))), int(min(H, np.ceil(y1 + pad)))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
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


def measure_file(path, rgb, det, profile=None):
    """One row of numbers for an image (float sRGB) and its Vision result."""
    H, W = rgb.shape[:2]
    name = icc_name(path)
    row = {"file": os.path.basename(path), "width": W, "height": H, "aspect": W / H,
           "icc": name, "srgb_icc": bool(name and "srgb" in name.lower()),
           "clip_pct": float((rgb.max(2) >= CLIP_AT - 1e-6).mean() * 100)}
    if profile is not None:
        want = profile.get("aspect")
        row["aspect_want"] = want[0] / want[1] if want else None
        row["aspect_err"] = W / H / row["aspect_want"] - 1 if want else None
    if not det or det.get("error"):
        row["error"] = (det or {}).get("error") or "Vision returned nothing for this file"
        return row
    if (det.get("width"), det.get("height")) != (W, H):
        row["error"] = f"Vision saw {det.get('width')}x{det.get('height')}, the file is {W}x{H}; pass upright files"
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
                    "skin_n_px": s["n_px"],
                    "d_trial_deg": wrap_deg(s["vectorscope_deg"] - REFERENCES["trial"]),
                    "d_skin_line_deg": wrap_deg(s["vectorscope_deg"] - REFERENCES["skin_line"])})
    row["face_w"] = fp["w"] / W
    row["headroom"] = (fp["y"] - HEAD_ABOVE * fp["h"]) / H
    if has_marks:
        row["eye_y"] = float(np.mean([c[1] for c in skin.eye_centres(fp)])) / H
        row["eye_sharp"] = eye_sharpness(rgb, fp)
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


def build(paths, detections, profile_name=None, profile=None):
    """The audit document for image paths and vision.faces(..., landmarks=True) output."""
    rows = []
    for p in paths:
        try:
            rgb = read_rgb(p)
        except ValueError as e:
            rows.append({"file": os.path.basename(p), "error": str(e)})
            continue
        rows.append(measure_file(p, rgb, detections.get(p), profile))
    return {"tool": "rpstills audit", "files": rows, "spread": spread(rows),
            "profile": profile_name,
            "profile_spec": ({"aspect": profile.get("aspect"), "eye_line": profile.get("eye_line")}
                             if profile is not None else None),
            "references_deg": REFERENCES,
            "params": {"clip_at": CLIP_AT, "head_above": HEAD_ABOVE, "eye_pad": EYE_PAD, "eye_box_w": EYE_BOX_W,
                       "bg_axes": list(BG_AXES), "bg_pct": BG_PCT, "cheek_at": skin.CHEEK_AT,
                       "cheek_radii": list(skin.CHEEK_RADII), "face": "largest box area"},
            "note": "Report only: no ranges are set, so nothing passes or fails."}


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
        tail = "yes" if r.get("srgb_icc") else (r.get("icc") or "none")
        lines.append(f"{r['file']:<{width}} {cells}  {tail}" + (f"  [{r['error']}]" if r.get("error") else ""))
    for k, h, fmt in cols:
        s = doc["spread"].get(k)
        if s:
            lines.append(f"spread {h:<8} sd {s['sd']:.3f}  range {_cell(s['min'], fmt)}..{_cell(s['max'], fmt)}"
                         f"  (n {s['n']})")
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
    return text + f", {no_icc} without an sRGB profile, {errors} not measured; report only, no ranges set"


def add_arguments(p):
    p.add_argument("dir", help="folder of finished JPEG or TIFF files")
    p.add_argument("--out", help="folder for audit.json (default: the audited folder); never inside a git tree")
    p.add_argument("--profile", help="a stills-config.json profile to compare aspect and eye line against")


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
    doc = build(paths, det, args.profile, profile)
    print(table(doc))
    print(write(out, doc))
    print(summary(doc))
    return 0
