"""rpstills.index: one JSON line per camera JPEG of a shoot folder.

Each original is read once: EXIF (time, orientation, exposure), a perceptual
hash, and an upright proxy JPEG (PROXY_EDGE on the long edge) written under
<out>/proxies/. Faces, capture quality, eye state and face sharpness are
then measured on the proxies. The RAW twin is only recorded by path.

Row shape (index.jsonl):
  {"id": "P1039029", "jpeg": "...", "raw": "..." | null, "proxy": "proxies/P1039029.jpg",
   "time": "2026-09-20T17:31:02", "subsec": "12", "orientation": 1,
   "width": 6000, "height": 4000, "upright_w": 6000, "upright_h": 4000,
   "iso": 400, "f": 3.2, "shutter": "1/1300", "focal": 141.0,
   "phash": "...", "face_hash": "..." | null, "faces": [...], "sharpness": [..per face..], "error": null}
"""

import datetime as _dt
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageOps

try:
    import imagehash
except ImportError:  # the index still runs, bursts just cannot be clustered
    imagehash = None

PROXY_EDGE = 2000
RAW_EXTS = (".RW2", ".rw2", ".DNG", ".dng", ".ARW", ".arw", ".CR3", ".cr3", ".NEF", ".nef", ".RAF", ".raf")
EXIF_IFD = 0x8769
TAG_DATETIME = 0x9003
TAG_SUBSEC = 0x9291
TAG_ISO = 0x8827
TAG_FNUMBER = 0x829D
TAG_EXPOSURE = 0x829A
TAG_FOCAL = 0x920A
TAG_ORIENTATION = 0x0112


def _ratio(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _shutter(v):
    f = _ratio(v)
    if not f:
        return None
    return f"1/{round(1 / f)}" if f < 1 else f"{f:g}"


def jpegs_in(folder):
    out = []
    for root, _dirs, files in os.walk(folder):
        for name in sorted(files):
            if name.lower().endswith((".jpg", ".jpeg")) and not name.startswith("."):
                out.append(os.path.join(root, name))
    return sorted(out)


def raw_twin(jpeg):
    stem = os.path.splitext(jpeg)[0]
    for ext in RAW_EXTS:
        if os.path.exists(stem + ext):
            return stem + ext
    return None


def frame_ids(files, folder):
    """A unique id per file: the bare stem when stems are unique across the
    shoot, else the path relative to the shoot folder with '/' as '__'."""
    stems = [os.path.splitext(os.path.basename(f))[0] for f in files]
    if len(set(stems)) == len(stems):
        return stems
    return [os.path.splitext(os.path.relpath(f, folder))[0].replace(os.sep, "__") for f in files]


def read_one(jpeg, proxy_dir, stem=None):
    """EXIF, phash and the proxy for one original. Returns a row without faces."""
    stem = stem or os.path.splitext(os.path.basename(jpeg))[0]
    row = {"id": stem, "jpeg": jpeg, "raw": raw_twin(jpeg), "proxy": f"proxies/{stem}.jpg", "error": None}
    try:
        with Image.open(jpeg) as im:
            exif = im.getexif()
            ifd = exif.get_ifd(EXIF_IFD) if exif else {}
            row["orientation"] = int(exif.get(TAG_ORIENTATION, 1) or 1)
            row["width"], row["height"] = im.size
            try:  # a blank or malformed date loses the time, not the frame
                row["time"] = _dt.datetime.strptime(str(ifd.get(TAG_DATETIME)), "%Y:%m:%d %H:%M:%S").isoformat()
            except (TypeError, ValueError):
                row["time"] = None
            row["subsec"] = str(ifd.get(TAG_SUBSEC) or "").strip() or None
            row["iso"] = ifd.get(TAG_ISO)
            row["f"] = _ratio(ifd.get(TAG_FNUMBER))
            row["shutter"] = _shutter(ifd.get(TAG_EXPOSURE))
            row["focal"] = _ratio(ifd.get(TAG_FOCAL))
            im.draft("RGB", (PROXY_EDGE, PROXY_EDGE))  # a half-scale decode is enough for a 2000 px proxy
            up = ImageOps.exif_transpose(im)
            row["phash"] = str(imagehash.phash(up)) if imagehash else None
            up.thumbnail((PROXY_EDGE, PROXY_EDGE))
            proxy = os.path.join(proxy_dir, f"{stem}.jpg")
            if os.path.exists(proxy):  # an earlier run's proxy stays; record its real size
                with Image.open(proxy) as existing:
                    row["upright_w"], row["upright_h"] = existing.size
            else:
                row["upright_w"], row["upright_h"] = up.size
                up.save(proxy + ".part", "JPEG", quality=88)
                os.replace(proxy + ".part", proxy)
    except Exception as e:  # one bad frame must not stop the shoot
        row["error"] = f"{type(e).__name__}: {e}"
    return row


def face_hash(proxy_path, faces, margin=0.25):
    """phash of the main face crop (largest box, with a margin), used to
    notice a change of person between neighbouring frames. None without
    imagehash or a face."""
    if not faces or imagehash is None:
        return None
    f = max(faces, key=lambda f: f.get("h", 0))
    try:
        with Image.open(proxy_path) as im:
            w, h = im.size
            box = (int(max(0, (f["x"] - margin * f["w"]) * w)), int(max(0, (f["y"] - margin * f["h"]) * h)),
                   int(min(w, (f["x"] + (1 + margin) * f["w"]) * w)), int(min(h, (f["y"] + (1 + margin) * f["h"]) * h)))
            crop = im.crop(box).convert("L").resize((160, 160))
            return str(imagehash.phash(crop))
    except Exception:
        return None


def augment_face_hash(out_dir, rows, log=print):
    """Add face_hash to rows that lack it and rewrite index.jsonl."""
    todo = [r for r in rows if r.get("faces") and "face_hash" not in r]
    if not todo:
        return rows
    log(f"index: face hashes for {len(todo)} frames")
    for r in todo:
        r["face_hash"] = face_hash(os.path.join(out_dir, r["proxy"]), r["faces"])
    path = os.path.join(out_dir, "index.jsonl")
    with open(path + ".part", "w") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    os.replace(path + ".part", path)
    return rows


def face_sharpness(proxy_path, faces):
    """Variance of the Laplacian inside each face box, on the proxy."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return [None] * len(faces)
    img = cv2.imread(proxy_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return [None] * len(faces)
    h, w = img.shape
    out = []
    for f in faces:
        x0, y0 = int(f["x"] * w), int(f["y"] * h)
        x1, y1 = int((f["x"] + f["w"]) * w), int((f["y"] + f["h"]) * h)
        crop = img[max(0, y0):min(h, y1), max(0, x0):min(w, x1)]
        out.append(float(cv2.Laplacian(crop, cv2.CV_64F).var()) if crop.size else None)
    return out


def build(folder, out_dir, workers=4, log=print):
    from . import vision
    proxy_dir = os.path.join(out_dir, "proxies")
    os.makedirs(proxy_dir, exist_ok=True)
    files = jpegs_in(folder)
    ids = frame_ids(files, folder)
    log(f"index: {len(files)} JPEGs under {folder}")
    rows = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for n, row in enumerate(pool.map(lambda ji: read_one(ji[0], proxy_dir, ji[1]), zip(files, ids)), 1):
            rows.append(row)
            if n % 100 == 0:
                log(f"  read {n}/{len(files)}")
    proxies = [os.path.join(out_dir, r["proxy"]) for r in rows if not r["error"]]
    log(f"index: faces on {len(proxies)} proxies")
    seen = vision.faces(proxies)
    if seen is None:
        log("index: Vision helper unavailable; faces left empty")
    for r in rows:
        doc = (seen or {}).get(os.path.join(out_dir, r["proxy"]))
        r["faces"] = (doc or {}).get("faces") or []
        if doc and doc.get("error") and not r["error"]:
            r["error"] = doc["error"]
        r["sharpness"] = face_sharpness(os.path.join(out_dir, r["proxy"]), r["faces"]) if r["faces"] else []
        r["face_hash"] = face_hash(os.path.join(out_dir, r["proxy"]), r["faces"])
    path = os.path.join(out_dir, "index.jsonl")
    with open(path + ".part", "w") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    os.replace(path + ".part", path)
    log(f"index: wrote {path}")
    return rows


def load(out_dir):
    with open(os.path.join(out_dir, "index.jsonl")) as f:
        return [json.loads(line) for line in f if line.strip()]


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("usage: python3 -m rpstills.index <shoot folder> <out dir>")
    build(sys.argv[1], sys.argv[2])
