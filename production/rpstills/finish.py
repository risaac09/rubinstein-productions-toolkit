"""rpstills.finish: background burn and retouch (both opt-in), then crop and
write the delivery JPEGs of developed portraits.

    python -m rpstills finish <out> [--ids ID ...] [--src DIR] [--retouch none|all|ID ...]
                              [--background off|on] [--crop PROFILE ...] [--config PATH]

Reads <out>/develop/<id>.tif (what `rpstills develop` writes), or every TIFF
and JPEG directly in --src DIR; an id is the file name without extension.
For each frame it finds the faces again with landmarks
(vision.faces(..., landmarks=True)) and works on the largest one (by box
area, not Vision's first face):

  0. input       inputs.read_upright: an embedded ICC profile other than
                 sRGB is converted to sRGB (none: taken as sRGB), the EXIF
                 orientation is applied; both are recorded in the row.
  1. background  --background on: background.subject_mask, then
                 background.burn (-0.6 EV, 15 % desaturation outside the
                 subject), over the crop region only (burn_region: the crop
                 boxes and the face, plus the feather's reach); the result
                 inside the crops equals a full-frame burn. A refused mask
                 leaves the frame unburned and the reason is recorded. Off
                 by default.
  2. retouch     --retouch all or a list of ids: retouch.apply, every step,
                 on the face's region only. Never by default (none).
  3. crop        one crop per --crop profile (default DEFAULT_CROPS) from
                 stills-config.json, by crops.solo_crop with the eye line
                 measured from the landmarks (the mean of the two eye
                 centres), so the profile's eye_line lands on the eyes rather
                 than on the box estimate. A profile with an "out" size is
                 resized to it; one without is written at the crop's size.

Writes <out>/finish/<profile>/<id>.jpg (JPEG quality JPEG_QUALITY, 4:4:4,
an sRGB ICC profile built by Pillow's ImageCms, so no macOS profile file is
needed) and <out>/finish/finish.json: per frame the face, what each stage
did (mask share or refusal, every retouch step's report), each crop's
pixels, achieved eye line and flags (tight, face_cut, upscale, as in
crops.json), and every constant used. Output shows and names client
frames, so <out>, or any profile folder under <out>/finish, inside any git
working tree is refused before any work, as is a --src file that one of
its own crops would overwrite.

The source is taken as developed and graded, in sRGB (an untagged file is
taken as sRGB): apart from the burn and the retouch, finish does not change
exposure or colour. Vision needs macOS and swiftc. A frame that cannot be
finished (unreadable, an ICC profile that cannot be converted, no face,
Vision saw another size than the upright file) is recorded with its
reason and the run exits 1.
"""

import json
import os
import sys
import tempfile

import numpy as np

from . import background, crops, inputs, retouch, skin, vision

EXTS = (".tif", ".tiff", ".jpg", ".jpeg")
JPEG_QUALITY = 93
SUBSAMPLING = 0  # Pillow: 0 is 4:4:4, no chroma subsampling (red lips and skin edges stay clean)
DEFAULT_CROPS = ("headshot_4x5_eye36",)
CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "stills-config.json")


def srgb_icc():
    """An sRGB ICC profile as bytes, from Pillow (no platform profile file)."""
    from PIL import ImageCms
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def sources(out_dir, src=None, ids=None):
    """{id: path} of the frames to finish. ValueError when there are none,
    when two files share an id, or when an asked-for id has no file."""
    folder = src or os.path.join(out_dir, "develop")
    if not os.path.isdir(folder):
        raise ValueError(f"{folder} is not a folder (run develop first, or pass --src)")
    found = {}
    for name in sorted(os.listdir(folder)):
        stem, ext = os.path.splitext(name)
        if ext.lower() in EXTS and not name.startswith(".") and os.path.isfile(os.path.join(folder, name)):
            if stem in found:
                raise ValueError(f"two files for id {stem} in {folder}")
            found[stem] = os.path.join(folder, name)
    if ids:
        missing = [i for i in ids if i not in found]
        if missing:
            raise ValueError(f"no file for id(s) {', '.join(missing)} in {folder}")
        found = {i: found[i] for i in dict.fromkeys(ids)}
    if not found:
        raise ValueError(f"no TIFF or JPEG in {folder}")
    return found


def retouch_ids(values, ids):
    """The set of ids to retouch from --retouch values (none, all, or ids,
    space or comma separated). ValueError for an id not being finished."""
    words = [w for v in values or [] for w in v.split(",") if w]
    if not words or words == ["none"]:
        return set()
    if "none" in words:  # before "all": a contradiction is refused, never read as the more invasive choice
        raise ValueError("--retouch none cannot be mixed with all or ids")
    if "all" in words:
        return set(ids)
    unknown = [w for w in words if w not in ids]
    if unknown:
        raise ValueError(f"--retouch names id(s) not being finished: {', '.join(unknown)}")
    return set(words)


def solo_profiles(cfg, names):
    """{name: profile} for crop profile names. ValueError for an unknown
    name or a profile that is not a solo crop (union, no aspect)."""
    out = {}
    for n in names:
        if not n or n in (".", "..") or os.path.basename(n) != n:
            raise ValueError(f"profile name {n!r} is not a plain name (it becomes a folder under finish/)")
        p = cfg["profiles"].get(n)
        if p is None:
            raise ValueError(f"no profile {n!r}; known: {', '.join(sorted(cfg['profiles']))}")
        if not p.get("aspect") or p.get("union") or p.get("crop_height_in_face_heights") is None \
                or p.get("eye_line") is None:
            raise ValueError(f"profile {n!r} is not a solo crop (it needs aspect, crop_height_in_face_heights "
                             "and eye_line)")
        out[n] = p
    return out


def crop_geometry(W, H, face, face_px, profile):
    """(crop tuple from crops.solo_crop, integer box (cx, cy, cw, ch), eye_y share or None) of one profile."""
    aspect = profile["aspect"][0] / profile["aspect"][1]
    eye_y = None
    if face_px.get("landmarks"):
        try:
            eye_y = float(np.mean([c[1] for c in skin.eye_centres(face_px)])) / H
        except ValueError:  # Vision leaves out a region it did not find: an eye missing, use the box estimate
            eye_y = None
    c = crops.solo_crop(face, aspect, W, H, profile["crop_height_in_face_heights"], profile["eye_line"], eye_y=eye_y)
    x0, y0, w, h, tight = c
    ch = min(H, int(round(h)))
    cw = min(W, int(round(ch * aspect)))
    cx, cy = min(max(0, int(round(x0))), W - cw), min(max(0, int(round(y0))), H - ch)
    return c, (cx, cy, cw, ch), eye_y


def burn_region(boxes, face_px, W, H):
    """(x0, y0, x1, y1): the union of the crop boxes and the face box, grown by the feather margin, clipped
    to the frame. Burning only here equals burning the frame wherever a crop reads it."""
    m = background.feather_margin(face_px)
    xs = [b[0] for b in boxes] + [b[0] + b[2] for b in boxes] + [face_px["x"], face_px["x"] + face_px["w"]]
    ys = [b[1] for b in boxes] + [b[1] + b[3] for b in boxes] + [face_px["y"], face_px["y"] + face_px["h"]]
    return (max(0, int(min(xs)) - m), max(0, int(min(ys)) - m),
            min(W, int(np.ceil(max(xs))) + m), min(H, int(np.ceil(max(ys))) + m))


def crop_frame(rgb, face, face_px, det_faces, name, profile):
    """(uint8 crop, crop record) of one profile on an upright float frame."""
    H, W = rgb.shape[:2]
    c, (cx, cy, cw, ch), eye_y = crop_geometry(W, H, face, face_px, profile)
    tile = (np.clip(rgb[cy:cy + ch, cx:cx + cw], 0, 1) * 255 + 0.5).astype(np.uint8)
    size = [cw, ch]
    if profile.get("out"):
        from PIL import Image
        size = list(profile["out"])
        tile = np.asarray(Image.fromarray(tile).resize(tuple(size), Image.Resampling.LANCZOS))
    eye_px = eye_y * H if eye_y is not None else (face["y"] + 0.40 * face["h"]) * H
    rec = {"px": [cx, cy, cw, ch], "size": size, "flags": crops.flags({"faces": det_faces}, c, W, H, profile),
           "eye_line": profile["eye_line"], "eye_line_at": round((eye_px - cy) / ch, 4),
           "eye_from": "landmarks" if eye_y is not None else "box estimate"}
    return tile, rec


def save_jpeg(path, tile, icc):
    """Write a uint8 HxWx3 array as JPEG atomically."""
    from PIL import Image
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".finish-", suffix=".jpg")
    os.close(fd)
    try:
        Image.fromarray(tile).save(tmp, "JPEG", quality=JPEG_QUALITY, subsampling=SUBSAMPLING, icc_profile=icc)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
    return path


def jpeg_paths(out_dir, profiles, ids):
    """Every JPEG a run writes: out_dir/finish/<profile>/<id>.jpg."""
    return [os.path.join(out_dir, "finish", n, f"{fid}.jpg") for n in profiles for fid in ids]


def finish_frame(fid, path, det, profiles, out_dir, do_retouch, do_background, icc):
    """Finish one frame, write its crops, return its finish.json row."""
    row = {"id": fid, "source": path}
    try:
        rgb, info = inputs.read_upright(path)
    except ValueError as e:
        row["error"] = str(e)
        return row
    H, W = rgb.shape[:2]
    row.update(width=W, height=H, orientation=info["orientation"], orientation_applied=info["orientation_applied"],
               icc=info["icc"], colour=info["colour"])
    if not det or det.get("error"):
        row["error"] = (det or {}).get("error") or "Vision returned nothing for this file"
        return row
    if (det.get("width"), det.get("height")) != (W, H):
        row["error"] = f"Vision saw {det.get('width')}x{det.get('height')}, the upright file is {W}x{H}"
        return row
    faces = det.get("faces") or []
    if not faces:
        row["error"] = "no face"
        return row
    face = max(faces, key=lambda f: f["w"] * f["h"])
    fp = skin.to_pixels(face, W, H)
    row["faces"] = len(faces)
    row["face"] = {k: round(face[k], 5) for k in ("x", "y", "w", "h")}
    row["landmarks"] = bool(face.get("landmarks"))
    if do_background:
        boxes = [crop_geometry(W, H, face, fp, p)[1] for p in profiles.values()]
        x0, y0, x1, y1 = region = burn_region(boxes, fp, W, H)
        mask, binfo = background.subject_mask(rgb, fp, region)
        if mask is None:
            row["background"] = dict(binfo, applied=False)
        else:
            rgb[y0:y1, x0:x1], rep = background.burn(rgb[y0:y1, x0:x1], mask)
            row["background"] = dict(binfo, applied=True, region=[x0, y0, x1, y1], **rep)
            del mask
    else:
        row["background"] = "off"
    if do_retouch:
        try:
            rgb, row["retouch"] = retouch.apply(rgb, fp)
        except ValueError as e:
            row["retouch"] = {"error": f"not retouched: {e}"}
    else:
        row["retouch"] = "off"
    row["crops"] = {}
    for name, profile in profiles.items():
        tile, rec = crop_frame(rgb, face, fp, faces, name, profile)
        rec["file"] = save_jpeg(jpeg_paths(out_dir, [name], [fid])[0], tile, icc)
        row["crops"][name] = rec
    return row


def refuse_git(out_dir, files=()):
    """ValueError when out_dir/finish, or any of files (the JPEGs, whose
    profile folder may be a link elsewhere), may not take client output."""
    from rpresolve.paths import out_problem  # lazy: rpresolve is stdlib-only, imported only to write
    for path in (os.path.join(out_dir, "finish", "finish.json"), *files):
        problem = out_problem(path, any_git_tree=True)
        if problem:
            raise ValueError(problem)


def refuse_overwrite(out_dir, paths, profiles):
    """ValueError when a source is a file one of the run's crops would replace
    (--src naming a folder under out/finish)."""
    sources_ = {os.path.realpath(p): fid for fid, p in paths.items()}
    hit = sorted({sources_[os.path.realpath(j)] for j in jpeg_paths(out_dir, profiles, paths)
                  if os.path.realpath(j) in sources_})
    if hit:
        raise ValueError(f"the source of {', '.join(hit)} would be overwritten by its own crop; "
                         "pass a --src folder outside out/finish, or another --crop")


def build(out_dir, paths, detections, profiles, retouch_set, do_background, cfg_version=None):
    """Finish every frame ({id: path}) and write its crops; returns the
    finish.json document (not yet written)."""
    refuse_git(out_dir, jpeg_paths(out_dir, profiles, paths))
    refuse_overwrite(out_dir, paths, profiles)
    icc = srgb_icc()
    rows = [finish_frame(fid, p, detections.get(p), profiles, out_dir, fid in retouch_set, do_background, icc)
            for fid, p in paths.items()]
    return {"tool": "rpstills finish", "frames": rows, "config_version": cfg_version,
            "options": {"retouch": sorted(retouch_set), "background": "on" if do_background else "off",
                        "crops": list(profiles)},
            "params": {"jpeg_quality": JPEG_QUALITY, "subsampling": "4:4:4", "icc": "sRGB (Pillow ImageCms)",
                       "face": "largest box area",
                       "eye_line": "landmarks (mean of the eye centres), else the box estimate",
                       "profiles": {n: {k: p.get(k) for k in ("aspect", "crop_height_in_face_heights",
                                                               "eye_line", "out")}
                                    for n, p in profiles.items()},
                       "retouch": retouch.params(), "background": background.params()}}


def write(out_dir, doc):
    """finish.json in out_dir/finish, atomically."""
    folder = os.path.join(out_dir, "finish")
    refuse_git(out_dir)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "finish.json")
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".finish-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(doc, f, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
    return path


def summary(doc):
    rows = doc["frames"]
    done = [r for r in rows if not r.get("error")]
    n_crops = sum(len(r.get("crops") or {}) for r in done)
    touched = sum(1 for r in done if isinstance(r.get("retouch"), dict) and "steps" in r["retouch"])
    bg = [r["background"] for r in done if isinstance(r.get("background"), dict)]
    burned = sum(1 for b in bg if b.get("applied"))
    flagged = sum(1 for r in done for c in r["crops"].values() if c["flags"])
    text = (f"{len(done)} of {len(rows)} frames finished, {n_crops} crops ({', '.join(doc['options']['crops'])}), "
            f"retouched {touched}, background burned {burned}")
    if len(bg) > burned:
        text += f" (refused {len(bg) - burned})"
    return text + f", {flagged} crops flagged"


def add_arguments(p):
    p.add_argument("out", help="the shoot's output folder: reads out/develop/<id>.tif, writes out/finish/; "
                               "never inside a git tree")
    p.add_argument("--ids", nargs="+", metavar="ID", help="frames to finish (default: every file)")
    p.add_argument("--src", help="a folder of developed TIFF or JPEG files to finish instead of out/develop")
    p.add_argument("--retouch", nargs="+", default=["none"], metavar="none|all|ID",
                   help="frames to retouch: none (default), all, or ids")
    p.add_argument("--background", choices=("off", "on"), default="off",
                   help="burn the background down outside a GrabCut subject mask (default off)")
    p.add_argument("--crop", nargs="+", metavar="PROFILE",
                   help=f"solo crop profiles from stills-config.json (default {' '.join(DEFAULT_CROPS)})")
    p.add_argument("--config", help="stills-config.json to read the profiles from (default: the toolkit's)")


def run(args):
    out = os.path.abspath(args.out)
    try:
        refuse_git(out)
        with open(args.config or CONFIG) as f:
            cfg = json.load(f)
        profiles = solo_profiles(cfg, args.crop or DEFAULT_CROPS)
        paths = sources(out, os.path.abspath(args.src) if args.src else None, args.ids)
        retouch_set = retouch_ids(args.retouch, list(paths))
        refuse_git(out, jpeg_paths(out, profiles, paths))
        refuse_overwrite(out, paths, profiles)
    except (ValueError, OSError) as e:
        print(f"rpstills finish: {e}", file=sys.stderr)
        return 1
    det = vision.faces(list(paths.values()), landmarks=True)
    if det is None:
        print("rpstills finish: the Vision helper cannot run (it needs macOS and swiftc)", file=sys.stderr)
        return 1
    doc = build(out, paths, det, profiles, retouch_set, args.background == "on", cfg.get("version"))
    print(write(out, doc))
    for r in doc["frames"]:
        if r.get("error"):
            print(f"rpstills finish: {r['id']}: {r['error']}", file=sys.stderr)
    print(summary(doc))
    return 1 if any(r.get("error") for r in doc["frames"]) else 0
