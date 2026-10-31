"""lutlab.apply: look at LUTs on real frames before baking anything into Resolve.

Stills: each frame through each LUT, plus one contact sheet (rows are frames,
columns the source then each LUT, labelled). Video: one LUT through ffmpeg's
lut3d filter to an H.264 preview. Inputs are files ffmpeg or OpenCV read
(TIFF, PNG, JPEG, MOV, MP4); BRAW is not one of them, so export a Pyxis still
from Resolve first (Gen 5 or DaVinci Intermediate, matching the LUT's input)."""

import os
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from . import cube
from .pairs import read_image

LABEL_H = 28


def _small(img, width):
    h, w = img.shape[:2]
    if w <= width:
        return img
    return cv2.resize(img, (width, round(h * width / w)), interpolation=cv2.INTER_AREA)


def _to8(img):
    return Image.fromarray(np.round(np.clip(img, 0, 1) * 255).astype(np.uint8))


def stills(luts, frames, outdir, width=960, overwrite=False):
    """Writes <frame> - <lut>.png for each pair and contact.jpg; returns the paths."""
    loaded = [(Path(p).stem, cube.read_cube(p)) for p in luts]
    written, rows = [], []
    for fp in frames:
        src = _small(read_image(fp), width)
        row = [("source", src)]
        for name, lut in loaded:
            out = cube.sample(lut, src)
            dst = Path(outdir) / f"{Path(fp).stem} - {name}.png"
            if dst.exists() and not overwrite:
                raise FileExistsError(dst)
            _to8(out).save(dst)
            written.append(str(dst))
            row.append((name, out))
        rows.append(row)
    sheet = Path(outdir) / "contact.jpg"
    if sheet.exists() and not overwrite:
        raise FileExistsError(sheet)
    cell_w = max(r[0][1].shape[1] for r in rows)
    cell_h = max(r[0][1].shape[0] for r in rows) + LABEL_H
    canvas = Image.new("RGB", (cell_w * len(rows[0]), cell_h * len(rows)), (18, 18, 18))
    draw = ImageDraw.Draw(canvas)
    for i, row in enumerate(rows):
        for j, (label, img) in enumerate(row):
            canvas.paste(_to8(img), (j * cell_w, i * cell_h + LABEL_H))
            draw.text((j * cell_w + 8, i * cell_h + 7), label[:60], fill=(230, 230, 230))
    canvas.save(sheet, quality=90)
    written.append(str(sheet))
    return written


def video(lut_path, src, dst, overwrite=False, crf=18):
    """One LUT over a clip with ffmpeg lut3d (tetrahedral). The LUT is rewritten
    as a plain .cube under a temp name: filter-graph paths cannot carry the
    brackets, colons and quotes LUT names have."""
    if os.path.exists(dst) and not overwrite:
        raise FileExistsError(dst)
    lut = cube.read_cube(lut_path)
    with tempfile.TemporaryDirectory() as tmp:
        safe = os.path.join(tmp, "look.cube")
        cube.write_cube(safe, lut["table"], "lutlab preview", lut["domain_min"], lut["domain_max"])
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y" if overwrite else "-n", "-i", str(src),
               "-vf", f"scale=in_color_matrix=bt709,lut3d=file={safe}:interp=tetrahedral,scale=out_color_matrix=bt709",
               "-c:v", "libx264", "-crf", str(crf), "-pix_fmt", "yuv420p",
               # verify.decode_frame reads every clip as bt709; tag the output so players agree
               "-colorspace", "bt709", "-bsf:v", "h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1", "-c:a", "aac", "-b:a", "192k", str(dst)]
        subprocess.run(cmd, check=True)
    return [str(dst)]
