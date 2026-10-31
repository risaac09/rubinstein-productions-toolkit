"""rpstills.index: one JSON line per camera JPEG of a shoot folder.

Each original is read once: EXIF (time, orientation, exposure), a perceptual
hash, and an upright proxy JPEG (PROXY_EDGE on the long edge) written under
<out>/proxies/. Faces, capture quality, eye state and face sharpness are
then measured on the proxies. The RAW twin is only recorded by path.

Three things reach past the camera-JPEG case (a wide frame grab, a stage):
  tiles     a frame with no subject face at proxy size (cluster.counted_faces)
            gets a second pass: the upright original is cut into a 3x2 grid
            of overlapping tiles, each brought up to TILE_EDGE (at most 2x),
            and Vision runs on those. A face of 5% of the frame height is 66
            px on a 2000 px proxy of an anamorphic frame and Vision misses it;
            on a tile it is 130. Faces found this way are mapped back to the
            frame and merged with duplicates dropped. --tiles off skips the
            pass; --tiles all runs it on every frame.
  people    whole-body boxes from Vision, for frames where no face is found
            at any size: the cull scores their composition.
  times     a frame with no EXIF time takes one from a sidecar, a stamp in
            its name, or its place in a numbered sequence (times.py).
Every row also carries `frame`, the whole-frame measures cull.py ranks a
face-less run by (frames.py).

Row shape (index.jsonl):
  {"id": "P1039029", "jpeg": "...", "raw": "..." | null, "proxy": "proxies/P1039029.jpg",
   "time": "2026-09-20T17:31:02", "subsec": "12", "orientation": 1,
   "width": 6000, "height": 4000, "upright_w": 6000, "upright_h": 4000,
   "iso": 400, "f": 3.2, "shutter": "1/1300", "focal": 141.0,
   "phash": "...", "face_hash": "..." | null, "faces": [...], "sharpness": [..per face..],
   "people": [...], "faces_unread": true | false, "face_source": "proxy" | "tiles" | null, "tiled": true | false,
   "time_source": "exif" | "sidecar" | "filename" | "sequence" | null,
   "frame": {"sharp", "mean", "contrast", "clip_lo", "clip_hi"} | null, "error": null}
"""

import datetime as _dt
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageOps

from . import cluster, frames, times as times_mod

try:
    import imagehash
except ImportError:  # the index still runs, bursts just cannot be clustered
    imagehash = None

PROXY_EDGE = 2000
FACE_HASH_MIN = 0.08    # a face crop shorter than this share of the frame carries no identity signal
TILE_COLS, TILE_ROWS = 3, 2
TILE_OVERLAP = 0.3      # of a tile, shared with its neighbour
TILE_EDGE = 2200        # long edge a tile is brought up to ...
TILE_UP_MAX = 2.0       # ... by at most this factor, never down
TILE_BATCH = 12         # frames whose tiles are on disk at once
MERGE_IOU = 0.3
TILES_MODES = ("auto", "off", "all")
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
    shoot, else the path relative to the shoot folder (with the extension too if that still collides) with '/' as '__'."""
    stems = [os.path.splitext(os.path.basename(f))[0] for f in files]
    if len(set(stems)) == len(stems):
        return stems
    rel = [os.path.relpath(f, folder).replace(os.sep, "__") for f in files]
    bare = [os.path.splitext(r)[0] for r in rel]
    return bare if len(set(bare)) == len(bare) else rel   # X1.jpg and X1.jpeg in one folder keep their extensions


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
                row["time_source"] = "exif"
            except (TypeError, ValueError):
                row["time"] = None
                row["time_source"] = None
            row["subsec"] = str(ifd.get(TAG_SUBSEC) or "").strip() or None
            row["iso"] = ifd.get(TAG_ISO)
            row["f"] = _ratio(ifd.get(TAG_FNUMBER))
            row["shutter"] = _shutter(ifd.get(TAG_EXPOSURE))
            row["focal"] = _ratio(ifd.get(TAG_FOCAL))
            im.draft("RGB", (PROXY_EDGE, PROXY_EDGE))  # a half-scale decode is enough for a 2000 px proxy
            up = ImageOps.exif_transpose(im)
            row["phash"] = str(imagehash.phash(up)) if imagehash else None
            up.thumbnail((PROXY_EDGE, PROXY_EDGE))
            row["frame"] = frames.metrics_image(up)   # on the proxy-size image already in hand, in the worker
            proxy = os.path.join(proxy_dir, f"{stem}.jpg")
            st = os.stat(jpeg)
            stamp = f"rpstills src {st.st_size} {st.st_mtime_ns}".encode()   # which source made this proxy
            reuse = False
            if os.path.exists(proxy):
                with Image.open(proxy) as existing:
                    reuse = existing.info.get("comment") == stamp
                    if reuse:
                        row["upright_w"], row["upright_h"] = existing.size
            if not reuse:   # absent, from another source, or from before the stamp: rebuild
                row["upright_w"], row["upright_h"] = up.size
                up.save(proxy + ".part", "JPEG", quality=88, comment=stamp)
                os.replace(proxy + ".part", proxy)
    except Exception as e:  # one bad frame must not stop the shoot
        row["error"] = f"{type(e).__name__}: {e}"
    return row


def face_hash(proxy_path, faces, margin=0.25):
    """phash of the main face crop (largest box, with a margin), used to
    notice a change of person between neighbouring frames. None without
    imagehash or a face, and None when the largest face is under
    FACE_HASH_MIN of the frame height: a crop of a few dozen pixels, scaled up
    to 160, hashes the compression noise and would split one performer's run
    into many."""
    if not faces or imagehash is None:
        return None
    f = max(faces, key=lambda f: f.get("h", 0))
    if f.get("h", 0) < FACE_HASH_MIN:
        return None
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


def tile_boxes(w, h, cols=TILE_COLS, rows=TILE_ROWS, overlap=TILE_OVERLAP):
    """[(x0, y0, x1, y1)] pixel boxes covering a w x h image in a cols x rows
    grid, each tile sharing `overlap` of its size with the next. A portrait
    image swaps the grid."""
    if h > w:
        cols, rows = rows, cols
    tw = w / (cols - (cols - 1) * overlap)
    th = h / (rows - (rows - 1) * overlap)
    out = []
    for j in range(rows):
        for i in range(cols):
            x0, y0 = i * tw * (1 - overlap), j * th * (1 - overlap)
            out.append((int(round(x0)), int(round(y0)), int(round(min(w, x0 + tw))),
                        int(round(min(h, y0 + th)))))
    return out


def face_iou(a, b):
    ix = max(0.0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
    iy = max(0.0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
    inter = ix * iy
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def merge_faces(faces, iou_min=MERGE_IOU):
    """Faces with duplicates dropped, largest first kept: a face is a duplicate
    of a larger one it overlaps by `iou_min` or more, or whose centre lies
    inside it (the same face seen in two tiles, or at two scales)."""
    kept = []
    for f in sorted(faces, key=lambda f: -f["h"]):
        cx, cy = f["x"] + f["w"] / 2.0, f["y"] + f["h"] / 2.0
        if any(face_iou(f, k) >= iou_min or
               (k["x"] <= cx <= k["x"] + k["w"] and k["y"] <= cy <= k["y"] + k["h"]) for k in kept):
            continue
        kept.append(f)
    return kept


def tile_to_frame(face, box, width, height):
    """A face box normalized to a tile, mapped to the frame (normalized to the
    whole upright image). Other keys (quality, eyes, ...) are kept."""
    tw, th = box[2] - box[0], box[3] - box[1]
    out = dict(face)
    out["x"] = (box[0] + face["x"] * tw) / float(width)
    out["y"] = (box[1] + face["y"] * th) / float(height)
    out["w"] = face["w"] * tw / float(width)
    out["h"] = face["h"] * th / float(height)
    return out


def detect_tiles(rows, out_dir, workers=4, log=print):
    """{id: [face]} from a tiled Vision pass over each row's upright original
    (see the module docstring); ids with nothing found are absent. None when
    the Vision helper cannot run. Tile files are written under <out>/tiles/ a
    batch at a time and removed as soon as Vision has read them."""
    from . import vision
    tile_dir = os.path.join(out_dir, "tiles")
    os.makedirs(tile_dir, exist_ok=True)

    def cut(row):
        jobs = []
        try:
            with Image.open(row["jpeg"]) as im:
                up = ImageOps.exif_transpose(im)
                width, height = up.size
                for k, box in enumerate(tile_boxes(width, height)):
                    tile = up.crop(box)
                    f = max(1.0, min(TILE_UP_MAX, TILE_EDGE / float(max(tile.size))))
                    if f > 1.0:
                        tile = tile.resize((int(round(tile.width * f)), int(round(tile.height * f))),
                                           Image.LANCZOS)
                    path = os.path.join(tile_dir, f"{row['id']}_t{k}.jpg")
                    tile.convert("RGB").save(path, "JPEG", quality=90)
                    jobs.append((row["id"], box, width, height, path))
        except Exception as e:  # an unreadable original costs its tiles, not the shoot
            log(f"index: no tiles for {row['id']}: {type(e).__name__}: {e}")
            for j in jobs:   # and leaves no half a frame's tiles on disk
                try:
                    os.remove(j[4])
                except OSError:
                    pass
            jobs = []
        return jobs

    found = {}
    try:
        for start in range(0, len(rows), TILE_BATCH):
            jobs = []
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for res in pool.map(cut, rows[start:start + TILE_BATCH]):
                    jobs.extend(res)
            seen = vision.faces([j[4] for j in jobs]) if jobs else {}
            for j in jobs:
                try:
                    os.remove(j[4])
                except OSError:
                    pass
            if seen is None:
                return None
            for rid, box, width, height, path in jobs:
                for face in (seen.get(path) or {}).get("faces") or []:
                    found.setdefault(rid, []).append(tile_to_frame(face, box, width, height))
    finally:
        try:
            os.rmdir(tile_dir)
        except OSError:
            pass
    return {rid: merge_faces(fs) for rid, fs in found.items()}


def augment_frame_metrics(out_dir, rows, log=print):
    """Add the whole-frame measures to rows that lack them (an index written
    before they existed) and rewrite index.jsonl."""
    todo = [r for r in rows if not r.get("error") and "frame" not in r]
    if not todo:
        return rows
    log(f"index: frame measures for {len(todo)} frames")
    for r in todo:
        r["frame"] = frames.metrics(os.path.join(out_dir, r["proxy"]))
    path = os.path.join(out_dir, "index.jsonl")
    with open(path + ".part", "w") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    os.replace(path + ".part", path)
    return rows


def build(folder, out_dir, workers=4, log=print, times_file=None, interval=None, start=None,
          tiles="auto"):
    from . import vision
    if tiles not in TILES_MODES:
        raise ValueError(f"tiles must be one of {', '.join(TILES_MODES)} (got {tiles!r})")
    sidecar_path = times_file or times_mod.default_sidecar(folder)
    sidecar = times_mod.load_sidecar(sidecar_path) if sidecar_path else None
    start_dt = None
    if start:
        start_dt = times_mod.parse_iso(start)
        if start_dt is None:
            raise ValueError(f"--start is not an ISO time: {start!r}")
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
    if start_dt is not None:
        by_id = (sidecar or {}).get("by_id", {})
        prefixes = set()
        for r in rows:   # only frames that would fall through to a sequence time count
            name = os.path.basename(r["jpeg"])
            stem = os.path.splitext(name)[0]
            seq = times_mod.sequence_of(stem)
            if (not r.get("time") and seq and r["id"] not in by_id and stem not in by_id
                    and times_mod.from_filename(name) is None):
                prefixes.add(seq[0])
        prefixes -= set((sidecar or {}).get("starts", {}))
        if len(prefixes) > 1:
            log(f"index: --start names one sequence but the undated frames carry {len(prefixes)} "
                f"prefixes ({', '.join(sorted(prefixes)[:5])}); it is not applied. "
                "Name each prefix's start in a times.json sidecar.")
            start_dt = None
    for r in rows:   # a frame with no EXIF time: sidecar, a stamp in its name, or its place in a sequence
        if r.get("time"):
            continue
        name = os.path.basename(r["jpeg"])
        t, src = times_mod.resolve(r["id"], name, sidecar, interval, start_dt)
        if t is None and r["id"] != os.path.splitext(name)[0]:
            t, src = times_mod.resolve(os.path.splitext(name)[0], name, sidecar, interval, start_dt)
        r["time"], r["time_source"] = (t.isoformat() if t else None), src
    undated = sum(1 for r in rows if not r["error"] and not r["time"])
    if undated:
        log(f"index: {undated} frames have no time (no EXIF, sidecar, file-name stamp or sequence); "
            "they stay in one run unless the class changes. See --times, --interval, --start.")
    proxies = [os.path.join(out_dir, r["proxy"]) for r in rows if not r["error"]]
    log(f"index: faces on {len(proxies)} proxies")
    seen = vision.faces(proxies)
    if seen is None:
        log("index: Vision helper unavailable; faces left empty")
    else:
        lost = sum(1 for p in proxies if p not in seen)
        if lost:
            log(f"index: Vision failed on {lost} proxies; their faces are left empty (faces_unread)")
    for r in rows:
        doc = (seen or {}).get(os.path.join(out_dir, r["proxy"]))
        r["faces_unread"] = seen is not None and doc is None and not r["error"]   # the helper failed on this file
        r["faces"] = (doc or {}).get("faces") or []
        r["people"] = (doc or {}).get("people") or []
        r["face_source"] = "proxy" if r["faces"] else None
        r["tiled"] = False
        if doc and doc.get("error") and not r["error"]:
            r["error"] = doc["error"]
        r.setdefault("frame", None)
    if tiles != "off" and seen is not None:
        todo = [r for r in rows if not r["error"] and (tiles == "all" or not cluster.counted_faces(r))]
        if todo:
            log(f"index: tiled pass on {len(todo)} frames "
                f"({'all frames' if tiles == 'all' else 'no subject face at proxy size'})")
            extra = detect_tiles(todo, out_dir, workers, log)
            if extra is None:
                log("index: tiled pass unavailable (Vision helper)")
            else:
                gained = 0
                for r in todo:
                    r["tiled"] = True
                    new = extra.get(r["id"]) or []
                    if not new:
                        continue
                    had = bool(cluster.counted_faces(r))
                    r["faces"] = merge_faces(r["faces"] + new)
                    r["face_source"] = "tiles"
                    gained += 0 if had or not cluster.counted_faces(r) else 1
                log(f"index: tiles found a subject face on {gained} more frames")
    for r in rows:
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
        sys.exit("usage: python3 -m rpstills.index <shoot folder> <out dir>  (see python3 -m rpstills for options)")
    build(sys.argv[1], sys.argv[2])
