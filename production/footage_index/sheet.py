"""Contact sheets of a whole shoot: every clip under a prefix, a few indexed thumbnails each, so a plan starts from
what was shot. Cells keep the thumbnail's aspect; sheets.json maps (sheet, row, col) back to a source path and second."""
import json
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import edit, gate as G, roots, search

IMG_W, IMG_H, LABEL_H = 240, 135, 36   # image box (letterboxed), label strip under it
RED, FONT_PX = (220, 30, 30), 12


def _pick(shots, k):
    """Up to k shots spread evenly over the clip, in time order."""
    if len(shots) <= k:
        return shots
    return [shots[i] for i in sorted({int(round(x)) for x in np.linspace(0, len(shots) - 1, k)})]


def _cell(r, first, flags, excl, font):
    im = Image.new("RGB", (IMG_W, IMG_H + LABEL_H), (24, 24, 24))
    try:
        t = Image.open(r["thumb"]).convert("RGB")
        k = min(IMG_W / t.width, IMG_H / t.height)
        t = t.resize((max(1, round(t.width * k)), max(1, round(t.height * k))))
        im.paste(t, ((IMG_W - t.width) // 2, (IMG_H - t.height) // 2))
    except (OSError, ValueError, TypeError):
        flags = flags + ["nothumb"]
    d = ImageDraw.Draw(im)
    stem = os.path.splitext(os.path.basename(r["relpath"]))[0]
    mo = "-" if r["motion"] is None else f"{r['motion']:.2f}"
    d.text((3, IMG_H + 4), f"{stem[:22]} {r['t']:.0f}s", fill=(255, 255, 255), font=font)
    d.text((3, IMG_H + 19), f"m{mo}" + (f" {r['fps']:g}fps" if first and r["fps"] else "") + "".join(f" {f}" for f in flags),
           fill=(255, 190, 60) if flags else (170, 170, 170), font=font)
    if excl:
        d.rectangle([0, 0, IMG_W - 1, IMG_H + LABEL_H - 1], outline=RED, width=4)
        d.rectangle([4, 4, 40, 20], fill=RED)
        d.text((7, 6), "EXCL", fill=(255, 255, 255), font=font)
    return im


def make(c, prefix, out, skip=(), per_clip=6, cols=8, rows=6, exclude=None):
    """Write sheet-NN.jpg and sheets.json into out; returns {"sheets": [paths], "clips": n, "cells": n}."""
    p = search.shoot_prefix(prefix)
    if exclude and not os.path.exists(exclude):
        raise ValueError(f"no exclude file at {exclude}")
    ex = edit.read_exclude(exclude) if exclude else edit.load_exclude(p)  # no --exclude: the shoot's own list
    allrows = [dict(r) for r in c.execute(
        "SELECT s.id, s.clip_id, s.t, s.thumb, s.motion, s.luma, cl.relpath, cl.fps, cl.duration FROM shots s "
        "JOIN clips cl ON cl.id = s.clip_id WHERE substr(cl.relpath, 1, ?) = ? ORDER BY cl.relpath, s.t", (len(p), p))]
    skip = [s.lower() for s in skip]
    rows_ = [r for r in allrows if not any(s in r["relpath"].lower() for s in skip)]
    keys = G.take_keys(c, {r["clip_id"]: (r["relpath"], r["duration"]) for r in allrows})
    dead = {allrows[i]["id"] for i in G.excluded(allrows, [keys[r["clip_id"]] for r in allrows], ex)}
    flag = dict(zip((r["id"] for r in rows_), G.shot_flags(rows_)))
    by_clip = {}
    for r in rows_:
        by_clip.setdefault(r["clip_id"], []).append(r)
    cells = [(r, i == 0) for shots in by_clip.values() for i, r in enumerate(_pick(shots, per_clip))]
    font = ImageFont.load_default(size=FONT_PX)
    os.makedirs(out, exist_ok=True)
    per, sheets, index = cols * rows, [], []
    for n in range(0, len(cells), per):
        chunk = cells[n:n + per]
        sh = Image.new("RGB", (cols * IMG_W, -(-len(chunk) // cols) * (IMG_H + LABEL_H)), (0, 0, 0))
        for k, (r, first) in enumerate(chunk):
            sh.paste(_cell(r, first, flag[r["id"]], r["id"] in dead, font), ((k % cols) * IMG_W, (k // cols) * (IMG_H + LABEL_H)))
            index.append({"sheet": n // per + 1, "row": k // cols + 1, "col": k % cols + 1, "clip_id": r["clip_id"],
                          "relpath": r["relpath"], "path": roots.volume_path(r["relpath"]), "second": r["t"],
                          "motion": r["motion"], "fps": r["fps"], "flags": flag[r["id"]] + (["excluded"] if r["id"] in dead else [])})
        sheets.append(os.path.join(out, f"sheet-{n // per + 1:02d}.jpg"))
        sh.save(sheets[-1], quality=88)
    with open(os.path.join(out, "sheets.json"), "w") as f:
        json.dump(index, f, indent=1)
    return {"sheets": sheets, "clips": len(by_clip), "cells": len(cells)}
