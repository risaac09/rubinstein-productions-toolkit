"""
sheets: contact sheets of every video file under a folder, with no index.

Up to --per-clip frames of each clip, spread over its duration, tiled onto sheet-NN.jpg in
--out (outside any git tree). Each cell keeps its picture's aspect and is labelled with the
file stem and the second; a clip with a squeeze answer (squeeze.py: a person's record, or a
BRAW file's own tag) is stretched to its real shape and marked with the factor, and one with
no answer is marked "squeeze open". sheets.json maps every cell (sheet, row, col) to the
file's absolute path and the second. BRAW is read through braw2ppm, or, when that is not
built, through its proxy (<stem>.mov or .mp4 in --proxy-dir or a camera's `Proxy` folder).
Folders named Proxy or Stills and dot-files are skipped. Needs Pillow; offline.
"""

import io
import json
import os
import re
import subprocess
import sys

from rpresolve import cli
from rpresolve import drafts
from rpresolve import paths as rppaths
from rpresolve import squeeze as rpsqueeze

NAME = "sheets"
HELP = "Offline: label and tile frames of every video under a folder onto contact sheets"

VIDEO = (".mov", ".mp4", ".m4v", ".mxf", ".mkv", ".avi", ".braw")
SKIP_DIRS = {"proxy", "stills"}
BRAW2PPM = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "lutlab", "braw", "braw2ppm")
CELL_W, IMG_H, LABEL_H = 320, 180, 36


def add_arguments(p):
    p.add_argument("folder", help="a card copy or any folder; searched recursively")
    p.add_argument("-o", "--out", required=True, help="folder for sheet-NN.jpg and sheets.json, "
                   "outside any git tree (made if its parent exists)")
    p.add_argument("--per-clip", type=int, default=6, help="frames a clip, spread over it (default 6)")
    p.add_argument("--cols", type=int, default=6)
    p.add_argument("--rows", type=int, default=4)
    p.add_argument("--proxy-dir", help="where BRAW proxies are (<name>.mov or .mp4)")


def find_clips(folder):
    out = []
    for d, dirs, files in os.walk(folder):
        dirs[:] = sorted(x for x in dirs if x.lower() not in SKIP_DIRS and not x.startswith("."))
        out += [os.path.join(os.path.abspath(d), f) for f in sorted(files)
                if f.lower().endswith(VIDEO) and not f.startswith(".")]
    return out


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, **kw)


def _png(args_before, src):
    """One frame of src as PNG bytes through ffmpeg, or None."""
    r = _run(["ffmpeg", "-v", "error", *args_before, "-i", src, "-frames:v", "1",
              "-vf", f"scale=-2:{IMG_H * 2}", "-f", "image2pipe", "-c:v", "png", "-"])
    return r.stdout if r.returncode == 0 and r.stdout else None


def _duration(path):
    r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
             text=True)
    try:
        return float(r.stdout.split()[0])
    except (ValueError, IndexError):
        return None


class Source:
    """How to read frames of one clip: .duration, .frame(second) -> PNG bytes or None, and
    .stretch, whether the picture it gives is still squeezed (a proxy may be desqueezed)."""

    def __init__(self, path, proxy_dir=None, tmp=None):
        self.path, self.tmp, self.proxy, self.duration, self.pre = path, tmp, None, None, None
        if not path.lower().endswith(".braw"):
            self.duration = _duration(path)
            return
        if os.path.isfile(BRAW2PPM):
            r = _run([BRAW2PPM, "--spherical", path, "0", "1", os.devnull], text=True)
            m = re.search(r"rate ([0-9.]+) .*?frames ([0-9]+)", r.stderr)
            if m and float(m.group(1)) > 0:
                self.duration = int(m.group(2)) / float(m.group(1))
                return
        self.proxy = drafts.proxy_for(path, proxy_dir)
        if self.proxy:
            self.duration = _duration(self.proxy)

    def frame(self, second):
        if not self.path.lower().endswith(".braw"):
            return _png(["-ss", f"{second:.3f}"], self.path)
        if self.proxy:
            return _png(["-ss", f"{second:.3f}"], self.proxy)
        ppm = os.path.join(self.tmp, "f.ppm")
        r = _run([BRAW2PPM, "--spherical", self.path, f"{second:.3f}s", "1", ppm])
        return _png([], ppm) if r.returncode == 0 and os.path.isfile(ppm) else None


def label(path, second, factor):
    """The two label lines of a cell."""
    stem = os.path.splitext(os.path.basename(path))[0]
    note = "squeeze open" if factor is None else (f"{factor:g}x" if factor != 1 else "")
    return f"{stem[:30]} {second:.1f}s", note


def make(folder, out, per_clip=6, cols=6, rows=4, proxy_dir=None):
    """Write sheet-NN.jpg and sheets.json into out; returns {"sheets": [paths], "clips": n, "cells": n}."""
    import tempfile
    from PIL import Image, ImageDraw, ImageFont
    clips = find_clips(folder)
    font = ImageFont.load_default(size=13)
    cells, skipped = [], []
    with tempfile.TemporaryDirectory() as tmp:
        for path in clips:
            f, _ = rpsqueeze.answer(path)
            src = Source(path, proxy_dir, tmp)
            if not src.duration:
                skipped.append(path)
                continue
            for i in range(per_clip):
                sec = src.duration * (i + 0.5) / per_clip
                png = src.frame(sec)
                im = None
                if png:
                    im = Image.open(io.BytesIO(png)).convert("RGB")
                    if f not in (None, 1) and not src.proxy:  # a proxy may be desqueezed already
                        im = im.resize((round(im.width * f), im.height))
                    elif f not in (None, 1):
                        a, b = rpsqueeze.picture_aspect(path), rpsqueeze.picture_aspect(src.proxy)
                        g = rpsqueeze.remaining(f, a, b)
                        if g:
                            im = im.resize((round(im.width * g), im.height))
                cells.append((path, sec, f, im))
    os.makedirs(out, exist_ok=True)
    per, sheets, index = cols * rows, [], []
    cell_h = IMG_H + LABEL_H
    for n in range(0, len(cells), per):
        chunk = cells[n:n + per]
        sh = Image.new("RGB", (cols * CELL_W, -(-len(chunk) // cols) * cell_h), (0, 0, 0))
        for k, (path, sec, f, im) in enumerate(chunk):
            cell = Image.new("RGB", (CELL_W, cell_h), (24, 24, 24))
            if im:
                s = min(CELL_W / im.width, IMG_H / im.height)
                im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))))
                cell.paste(im, ((CELL_W - im.width) // 2, (IMG_H - im.height) // 2))
            a, b = label(path, sec, f)
            d = ImageDraw.Draw(cell)
            d.text((4, IMG_H + 3), a + ("" if im else " (no frame)"), fill=(255, 255, 255), font=font)
            d.text((4, IMG_H + 19), b, fill=(255, 190, 60), font=font)
            sh.paste(cell, ((k % cols) * CELL_W, (k // cols) * cell_h))
            index.append({"sheet": n // per + 1, "row": k // cols + 1, "col": k % cols + 1,
                          "path": path, "second": round(sec, 3), "squeeze": f})
        sheets.append(os.path.join(out, f"sheet-{n // per + 1:02d}.jpg"))
        sh.save(sheets[-1], quality=88)
    with open(os.path.join(out, "sheets.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=1)
    return {"sheets": sheets, "clips": len(clips) - len(skipped), "cells": len(cells),
            "skipped": skipped}


def run(args):
    """Tile frames of every video under a folder onto labelled sheets (offline). Exit 0
    the sheets are written, 1 they could not be."""
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("ERROR: sheets needs Pillow, which this Python lacks. Use /opt/homebrew/bin/python3.",
              file=sys.stderr)
        return 1
    if not os.path.isdir(args.folder):
        print(f"ERROR: {args.folder}: not a folder", file=sys.stderr)
        return 1
    if args.per_clip < 1 or args.cols < 1 or args.rows < 1:
        print("ERROR: --per-clip, --cols and --rows are at least 1", file=sys.stderr)
        return 1
    problem = rppaths.out_problem(args.out)
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    r = make(args.folder, args.out, args.per_clip, args.cols, args.rows, args.proxy_dir)
    for s in r["skipped"]:
        print(f"skipped (no duration, or no proxy for BRAW): {s}", file=sys.stderr)
    print(f"{r['cells']} cell(s) from {r['clips']} clip(s) on {len(r['sheets'])} sheet(s) in {args.out}")
    return 0 if r["cells"] else 1


if __name__ == "__main__":
    cli.script(__name__)
