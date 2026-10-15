"""lutlab.verify: check a Resolve render against the LUT it should carry.

Decode one frame of the source and one of the render with ffmpeg, fit the
source to the render's frame, run the source through the LUT with cube.sample,
and report Delta E 2000 between that and the render over the interior of the
frame. A median under about 1 means Resolve applied the LUT the way lutlab
models it. The source has to be in the LUT's input encoding: an S5II V-Log
clip as shot, or a Pyxis frame rendered out of Resolve in Gen 5 (ffmpeg can't
read BRAW). Both files are read as BT.709 video."""

import json
import subprocess

import numpy as np

from . import cube, fit, spaces


def probe_size(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:stream_side_data=rotation",
                        "-of", "json", str(path)], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"ffprobe failed on {path}: {r.stderr[-300:]}")
    s = json.loads(r.stdout)["streams"][0]
    w, h = int(s["width"]), int(s["height"])
    rot = next((d["rotation"] for d in s.get("side_data_list", []) if "rotation" in d), 0)
    return (h, w) if abs(int(rot)) % 180 == 90 else (w, h)  # ffmpeg autorotates on decode


def decode_frame(path, t, width, height):
    """The frame at t seconds as float RGB (height, width, 3) in 0..1, 16-bit,
    scaled to fit width x height (aspect kept, black bars) and full range."""
    # convert to RGB first, with the matrix forced, then resize and pad in RGB
    vf = (f"scale=iw:ih:in_color_matrix=bt709:out_range=full,format=rgb48le,"
          f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=area,"
          f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
           "-vf", vf, "-f", "rawvideo", "-"]
    r = subprocess.run(cmd, capture_output=True)
    need = width * height * 6
    if r.returncode or len(r.stdout) != need:
        raise RuntimeError(f"cannot decode a frame of {path} at {t}s: {r.stderr.decode()[-300:]}")
    return np.frombuffer(r.stdout, dtype="<u2").reshape(height, width, 3).astype(np.float64) / 65535


def content_box(src_size, width, height, margin):
    """(x0, x1, y0, y1) of the picture inside the fitted frame, trimmed by margin on every side."""
    sw, sh = src_size
    k = min(width / sw, height / sh)
    cw, ch = sw * k, sh * k
    x0, y0 = (width - cw) / 2, (height - ch) / 2
    return (int(np.ceil(x0 + cw * margin)), int(np.floor(x0 + cw * (1 - margin))),
            int(np.ceil(y0 + ch * margin)), int(np.floor(y0 + ch * (1 - margin))))


def verify(source, render, lut_path, t, render_t=None, out="r709", margin=0.10, fit_to=None):
    if not 0 <= margin < 0.5:
        raise ValueError("margin must be from 0 up to (not including) 0.5")
    lut = cube.read_cube(lut_path)
    width, height = fit_to or probe_size(render)
    src = decode_frame(source, t, width, height)
    ren = decode_frame(render, t if render_t is None else render_t, width, height)
    x0, x1, y0, y1 = content_box(probe_size(source), width, height, margin)
    src, ren = src[y0:y1, x0:x1], ren[y0:y1, x0:x1]
    want = cube.sample(lut, src)
    lab = lambda y: spaces.cielab(np.maximum(spaces.to_lin709(np.clip(y, 0, 1), out), 0))
    de = spaces.delta_e2000(lab(want).reshape(-1, 3), lab(ren).reshape(-1, 3))
    bias = (ren - want).reshape(-1, 3).mean(0)
    return {"lut": str(lut_path), "frame": [width, height], "t": t, "pixels": int(de.size),
            "dE2000": fit.stats(de), "mean render minus lut (r,g,b, 0..1)": [round(float(b), 4) for b in bias]}
