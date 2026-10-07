"""rpstills.crops: a crop per standard profile for each selected frame.

Reads index.jsonl, clusters.json and the selection (selects.json exported
from the review sheet, else the cull proposal) and writes crops.json plus
one preview sheet. Pure geometry on the face boxes already in the index;
no image is touched except the previews.

A crop is normalized to the upright image, {"x","y","w","h"} from the top
left, plus "px" on the full-resolution upright frame. Flags, per crop:
  tight      the frame could not hold the wanted crop and it was shrunk to fit
  face_cut   a counted face is not fully inside the crop
  upscale    the crop has fewer pixels than the profile's output size
"""

import json
import os

MIN_FACE = 0.04
FACE_PAD = 0.05  # a face must clear the crop edge by this share of its own size


def load_config(path):
    with open(path) as f:
        return json.load(f)


def upright_size(row):
    w, h = row["width"], row["height"]
    return (h, w) if int(row.get("orientation") or 1) in (5, 6, 7, 8) else (w, h)


def counted(row):
    return [f for f in row.get("faces") or [] if f.get("h", 0) >= MIN_FACE]


def _fit(box, aspect, W, H):
    """Shift (then shrink) a pixel box of the wanted aspect to sit inside W x H.
    box = (cx, cy, w, h) with w / h == aspect. Returns (x0, y0, w, h, tight)."""
    cx, cy, w, h = box
    tight = False
    if w > W or h > H:
        s = min(W / w, H / h)
        w, h, tight = w * s, h * s, True
    x0 = min(max(0.0, cx - w / 2), W - w)
    y0 = min(max(0.0, cy - h / 2), H - h)
    return x0, y0, w, h, tight


def solo_crop(face, aspect, W, H, height_in_faces, eye_line):
    fh = face["h"] * H
    fcx = (face["x"] + face["w"] / 2) * W
    eye_y = face["y"] * H + 0.40 * fh
    h = height_in_faces * fh
    w = h * aspect
    cy = eye_y - eye_line * h + h / 2
    return _fit((fcx, cy, w, h), aspect, W, H)


def union_crop(faces, aspect, W, H, margin):
    x0 = min(f["x"] for f in faces) * W
    x1 = max(f["x"] + f["w"] for f in faces) * W
    y0 = min(f["y"] for f in faces) * H
    y1 = max(f["y"] + f["h"] for f in faces) * H
    uw, uh = x1 - x0, y1 - y0
    w = uw * (1 + 2 * margin)
    h = max(uh * (1 + 2 * margin) + uh * 0.0, w / aspect)
    if h * aspect > w:
        w = h * aspect
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2 + uh * 0.15  # a little more room below the faces than above
    return _fit((cx, cy, w, h), aspect, W, H)


def flags(row, crop_px, W, H, profile):
    x0, y0, w, h, tight = crop_px
    out = []
    if tight:
        out.append("tight")
    for f in counted(row):
        pad_x, pad_y = FACE_PAD * f["w"] * W, FACE_PAD * f["h"] * H
        if (f["x"] * W < x0 + pad_x or (f["x"] + f["w"]) * W > x0 + w - pad_x
                or f["y"] * H < y0 + pad_y or (f["y"] + f["h"]) * H > y0 + h - pad_y):
            out.append("face_cut")
            break
    target = profile.get("out")
    if target and (w < target[0] or h < target[1]):
        out.append("upscale")
    return out


def plan_frame(row, cls, cfg):
    W, H = upright_size(row)
    faces = counted(row)
    crops = {}
    for name, p in cfg["profiles"].items():
        if cls not in p.get("classes", []) or not p.get("aspect"):
            continue
        if not faces:
            continue
        aspect = p["aspect"][0] / p["aspect"][1]
        if p.get("union") or (cls in p.get("union_for", [])):
            c = union_crop(faces, aspect, W, H, p.get("union_margin", 0.3))
        else:
            main = max(faces, key=lambda f: f["h"])
            c = solo_crop(main, aspect, W, H, p["crop_height_in_face_heights"], p["eye_line"])
        x0, y0, w, h, _ = c
        crops[name] = {"x": round(x0 / W, 5), "y": round(y0 / H, 5), "w": round(w / W, 5), "h": round(h / H, 5),
                       "px": [round(x0), round(y0), round(w), round(h)], "flags": flags(row, c, W, H, p)}
    return {"upright": [W, H], "class": cls, "crops": crops}


def selection(out_dir, cull_doc):
    """(frame ids, source name). selects.json beside the index wins over the proposal."""
    path = os.path.join(out_dir, "selects.json")
    if os.path.exists(path):
        with open(path) as f:
            return list(dict.fromkeys(json.load(f)["keep"])), "selects.json"
    return [fid for p in cull_doc["proposal"] for fid in p["picks"]], "cull proposal"


def build(rows, clusters, ids, cfg):
    by_id = {r["id"]: r for r in rows}
    cls_of = {fid: run["class"] for run in clusters["runs"] for fid in run["frames"]}
    return {fid: plan_frame(by_id[fid], cls_of.get(fid, "none"), cfg) for fid in ids if fid in by_id}


def write(out_dir, plan, source, cfg):
    path = os.path.join(out_dir, "crops.json")
    with open(path, "w") as f:
        json.dump({"source": source, "config_version": cfg.get("version"), "frames": plan}, f, indent=1, sort_keys=True)
    return path


def summary(plan):
    counts, n = {}, 0
    for fp in plan.values():
        for name, c in fp["crops"].items():
            n += 1
            for fl in c["flags"]:
                counts[fl] = counts.get(fl, 0) + 1
    return f"{len(plan)} frames, {n} crops, flags {counts or 'none'}"


def preview(out_dir, rows, plan, cfg, limit=24):
    from PIL import Image, ImageDraw
    by_id = {r["id"]: r for r in rows}
    names = [n for n in cfg["profiles"] if cfg["profiles"][n].get("aspect")]
    ids = list(plan)[:limit]
    T = 200
    sheet = Image.new("RGB", (T * (len(names) + 1), (T + 16) * len(ids)), "#101010")
    d = ImageDraw.Draw(sheet)
    for i, fid in enumerate(ids):
        im = Image.open(os.path.join(out_dir, by_id[fid]["proxy"])).convert("RGB")
        base = im.copy(); base.thumbnail((T, T)); sheet.paste(base, (0, i * (T + 16)))
        d.text((4, i * (T + 16) + T + 2), fid, fill="white")
        for j, n in enumerate(names, 1):
            c = plan[fid]["crops"].get(n)
            if not c:
                continue
            W, H = im.size
            tile = im.crop((int(c["x"] * W), int(c["y"] * H), int((c["x"] + c["w"]) * W), int((c["y"] + c["h"]) * H)))
            tile.thumbnail((T, T)); sheet.paste(tile, (j * T, i * (T + 16)))
            d.text((j * T + 4, i * (T + 16) + T + 2), n + (" " + ",".join(c["flags"]) if c["flags"] else ""), fill="white")
    path = os.path.join(out_dir, "crops-preview.jpg")
    sheet.save(path, quality=82)
    return path
