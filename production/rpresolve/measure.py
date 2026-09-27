"""
rpresolve.measure: numbers from a rendered file, so a grade or a camera
match is judged on the render rather than by eye alone. Offline: ffmpeg
reads the file; nothing touches Resolve.

Per sampled frame, and pooled over the file:
    - luma p1 / p50 / p99 in IRE (Rec.709 luma of the display RGB)
    - clipping: share of pixels with any channel at the top code value, or
      every channel at zero
    - legal range: ffmpeg signalstats YMIN / YMAX, scaled to 8-bit code
      values (legal video range is 16..235)
    - skin, where macOS Vision finds a face: median Lab, chroma, and the
      vectorscope hue angle against the skin-tone line (123 degrees)
With segments (label=start-end, seconds), the skin medians per label and
the pairwise CIEDE2000 between labels: the camera-match numbers.

The input is taken as display-encoded Rec.709 with a 2.4 gamma, the house
delivery encoding. The file's own colour tags are reported beside the
numbers, never used to guess a different decode.

numpy is required (the system /usr/bin/python3 has it). Face boxes need
macOS with swiftc; without them the skin section reads UNAVAILABLE.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile

import numpy as np

from . import color

FFMPEG = os.environ.get("RPRESOLVE_FFMPEG", "/opt/homebrew/bin/ffmpeg")
FFPROBE = os.environ.get("RPRESOLVE_FFPROBE", "/opt/homebrew/bin/ffprobe")
SWIFTC = shutil.which("swiftc") or "/usr/bin/swiftc"
VISION_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vision_faces.swift")
CACHE_DIR = os.path.expanduser("~/Library/Caches/rpresolve")

SKIN_LINE_DEG = 123.0
UNAVAILABLE = "UNAVAILABLE"
# Skin mask inside a face box, as YCbCr ranges on 8-bit full-range values
# (the widely used Chai and Ngan bounds).
SKIN_CB, SKIN_CR = (77, 127), (133, 173)
PIXEL_STRIDE = 2  # per-frame luma and clipping use every second row and column
TIMEOUT_S = 300   # per ffmpeg/ffprobe call; a stalled SMB read must not hang the run
# ffprobe color_space tag -> ffmpeg scale in_color_matrix. Anything else,
# untagged included, is decoded as Rec.709 and the report says so.
MATRICES = {"bt709": "bt709", "smpte170m": "bt601", "bt470bg": "bt601",
            "bt2020nc": "bt2020", "bt2020c": "bt2020"}


class MeasureError(RuntimeError):
    pass


def _run(cmd, **kw):
    try:
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=TIMEOUT_S, **kw)
    except subprocess.TimeoutExpired:
        raise MeasureError(f"{os.path.basename(cmd[0])} timed out after {TIMEOUT_S}s")


# ---------------------------------------------------------------------------
# Reading the render
# ---------------------------------------------------------------------------

def probe(path):
    """{width, height, duration, fps, color tags} of the first video stream."""
    cmd = [FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
           "stream=width,height,avg_frame_rate,color_range,color_space,color_transfer,"
           "color_primaries,pix_fmt:stream_side_data=rotation:stream_tags=rotate:format=duration",
           "-of", "json", "file:" + path]
    proc = _run(cmd, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise MeasureError(f"ffprobe failed on {path}: {proc.stderr.strip()[-200:]}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
        raise MeasureError(f"no video stream in {path}")
    s = streams[0]
    try:
        duration = float((data.get("format") or {}).get("duration") or 0)
    except ValueError:
        duration = 0.0
    width, height = int(s["width"]), int(s["height"])
    # ffmpeg auto-rotates on decode, so a quarter-turn swaps the frame's size.
    if rotation(s) % 180 == 90:
        width, height = height, width
    return {"width": width, "height": height, "duration": duration,
            "tags": {k: s.get(k, "") for k in ("pix_fmt", "color_range", "color_space",
                                               "color_transfer", "color_primaries")}}


def rotation(stream):
    """Display rotation in degrees (0..359) from ffprobe's side data or the
    older 'rotate' tag."""
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                return int(round(float(sd["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    try:
        return int((stream.get("tags") or {}).get("rotate", 0)) % 360
    except (TypeError, ValueError):
        return 0


def decode_filter(tags):
    """(-vf value, note) that decodes YUV with an explicit matrix and range
    from the file's tags; ffmpeg's own default is BT.601 when untagged. RGB
    sources need no filter."""
    if is_rgb_source(tags.get("pix_fmt")):
        return None, ""
    space = tags.get("color_space") or ""
    matrix = MATRICES.get(space, "bt709")
    note = "" if space in MATRICES else f"color_space '{space or 'untagged'}': decoded as bt709"
    rng = "full" if tags.get("color_range") == "pc" else "limited"
    return f"scale=in_color_matrix={matrix}:in_range={rng}", note


def sample_times(duration, count):
    """count times spread evenly through the file, avoiding the first and
    last instants. A still (no duration) gives one sample at 0."""
    if duration <= 0 or count <= 1:
        return [0.0] if duration <= 0 else [duration / 2]
    return [duration * (i + 0.5) / count for i in range(count)]


def read_frame(path, t, width, height, vf=None):
    """One frame at time t as float RGB 0..1, shape (h, w, 3), decoded by
    ffmpeg to full-range 16-bit RGB (limited-range sources are expanded).
    width and height are the displayed (rotated) size from probe()."""
    cmd = [FFMPEG, "-v", "error", "-ss", f"{t:.3f}", "-i", "file:" + path, "-frames:v", "1"]
    cmd += ["-vf", vf] if vf else []
    cmd += ["-f", "rawvideo", "-pix_fmt", "rgb48le", "-"]
    proc = _run(cmd)
    want = width * height * 6
    if proc.returncode != 0 or len(proc.stdout) < want:
        raise MeasureError(f"ffmpeg could not read a frame at {t:.3f}s: "
                           f"{proc.stderr.decode(errors='replace').strip()[-200:]}")
    arr = np.frombuffer(proc.stdout[:want], dtype="<u2").reshape(height, width, 3)
    return arr.astype(np.float64) / 65535.0


_STAT = re.compile(r"lavfi\.signalstats\.(YMIN|YMAX)=(\d+)")


def legal_range(path, t, bit_depth):
    """(YMIN, YMAX) from ffmpeg signalstats at time t, in 8-bit code values."""
    cmd = [FFMPEG, "-v", "error", "-ss", f"{t:.3f}", "-i", "file:" + path, "-frames:v", "1",
           "-vf", "signalstats,metadata=print:file=-", "-f", "null", "-"]
    proc = _run(cmd, encoding="utf-8", errors="replace")
    found = dict(_STAT.findall(proc.stdout))
    if "YMIN" not in found or "YMAX" not in found:
        return None
    scale = 2 ** (bit_depth - 8)
    return int(found["YMIN"]) / scale, int(found["YMAX"]) / scale


RGB_PIX_FMTS = ("rgb", "bgr", "gbr", "argb", "rgba", "abgr", "bgra", "gray", "pal8")


def is_rgb_source(pix_fmt):
    """True for RGB and greyscale pixel formats, where the Y'CbCr legal
    range (16..235) does not apply."""
    return (pix_fmt or "").startswith(RGB_PIX_FMTS)


def bit_depth(pix_fmt):
    """Bits per component of a Y'CbCr pixel format: yuv420p10le and
    p010le -> 10; formats without a width suffix are 8-bit."""
    m = re.search(r"(\d{2})(le|be)$", pix_fmt or "")
    return int(m.group(1)) if m else 8


# ---------------------------------------------------------------------------
# Pure measurements (tested on synthetic arrays)
# ---------------------------------------------------------------------------

def luma_stats(rgb, stride=PIXEL_STRIDE):
    y = color.luma_ire(rgb[::stride, ::stride].reshape(-1, 3))
    p1, p50, p99 = np.percentile(y, [1, 50, 99])
    return {"p1": round(float(p1), 2), "p50": round(float(p50), 2), "p99": round(float(p99), 2)}


def clipping(rgb, stride=PIXEL_STRIDE):
    """Share of pixels (percent) with any channel at the top code value, and
    with every channel at zero."""
    px = rgb[::stride, ::stride].reshape(-1, 3)
    top = 1.0 - 0.5 / 255
    bottom = 0.5 / 255
    return {"high_pct": round(float(np.mean(px.max(axis=1) >= top)) * 100, 3),
            "low_pct": round(float(np.mean(px.max(axis=1) <= bottom)) * 100, 3)}


def skin_mask(rgb):
    """Boolean mask of skin-coloured pixels (YCbCr bounds, 8-bit full range)."""
    y = color._dot3(rgb, color.REC709_LUMA)
    cb = 128 + 255 * (rgb[..., 2] - y) / 1.8556
    cr = 128 + 255 * (rgb[..., 0] - y) / 1.5748
    return ((cb >= SKIN_CB[0]) & (cb <= SKIN_CB[1]) & (cr >= SKIN_CR[0]) & (cr <= SKIN_CR[1]))


def face_region(rgb, box):
    """The inner part of a face box (normalized, top-left origin): the middle
    60 percent across and 25..85 percent down, which keeps cheeks and nose
    and leaves most hair and background out."""
    h, w = rgb.shape[:2]
    x0 = int((box["x"] + 0.2 * box["w"]) * w)
    x1 = int((box["x"] + 0.8 * box["w"]) * w)
    y0 = int((box["y"] + 0.25 * box["h"]) * h)
    y1 = int((box["y"] + 0.85 * box["h"]) * h)
    x0, x1 = max(0, x0), min(w, max(x0 + 1, x1))
    y0, y1 = max(0, y0), min(h, max(y0 + 1, y1))
    return rgb[y0:y1, x0:x1]


def skin_stats(rgb, boxes, min_pixels=200):
    """Median skin Lab, chroma and vectorscope hue over every face box, or
    None when no box yields min_pixels skin pixels."""
    pixels = []
    for box in boxes:
        region = face_region(rgb, box)
        mask = skin_mask(region)
        if mask.any():
            pixels.append(region[mask])
    if not pixels:
        return None
    px = np.concatenate(pixels)
    if len(px) < min_pixels:
        return None
    lab = np.median(color.rec709_to_lab(px), axis=0)
    hue = float(np.median(color.vectorscope_angle(px)))
    return {"lab": [round(float(v), 3) for v in lab],
            "chroma": round(float(color.chroma(lab)), 3),
            "hue_deg": round(hue, 2),
            "hue_off_skin_line_deg": round(hue - SKIN_LINE_DEG, 2),
            "pixels": int(len(px))}


def parse_segment(text):
    """'label=start-end' (seconds) -> (label, start, end)."""
    m = re.match(r"^([^=]+)=([\d.]+)-([\d.]+)$", text.strip())
    if not m:
        raise MeasureError(f"segment '{text}' is not label=start-end in seconds")
    start, end = float(m.group(2)), float(m.group(3))
    if end <= start:
        raise MeasureError(f"segment '{text}' ends before it starts")
    return m.group(1).strip(), start, end


def camera_match(frames, segments, hero=None):
    """Skin medians per segment label and pairwise CIEDE2000 between labels.
    frames: [{t, skin, luma}]; segments: [(label, start, end)]."""
    per = {}
    for label, start, end in segments:
        labs = [f["skin"]["lab"] for f in frames
                if isinstance(f.get("skin"), dict) and start <= f["t"] <= end]
        p50 = [f["luma"]["p50"] for f in frames if start <= f["t"] <= end]
        per[label] = {"frames_with_skin": len(labs),
                      "skin_lab": [round(float(v), 3) for v in np.median(labs, axis=0)] if labs else None,
                      "luma_p50": round(float(np.median(p50)), 2) if p50 else None}
        if labs:
            per[label]["skin_chroma"] = round(float(color.chroma(np.array(per[label]["skin_lab"]))), 3)
    pairs = []
    labels = [s[0] for s in segments]
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            la, lb = per[a]["skin_lab"], per[b]["skin_lab"]
            de = float(color.delta_e_2000(np.array(la), np.array(lb))) if la and lb else None
            dl = (abs(per[a]["luma_p50"] - per[b]["luma_p50"])
                  if per[a]["luma_p50"] is not None and per[b]["luma_p50"] is not None else None)
            pairs.append({"a": a, "b": b, "skin_de2000": None if de is None else round(de, 3),
                          "luma_p50_delta_ire": None if dl is None else round(dl, 2)})
    out = {"segments": per, "pairs": pairs}
    if hero and per.get(hero, {}).get("skin_chroma"):
        ref = per[hero]["skin_chroma"]
        out["chroma_vs_hero_pct"] = {k: round((v["skin_chroma"] / ref - 1) * 100, 2)
                                     for k, v in per.items() if v.get("skin_chroma")}
    return out


# ---------------------------------------------------------------------------
# Face boxes (macOS Vision, compiled once)
# ---------------------------------------------------------------------------

def vision_binary():
    """Path of the compiled Vision helper, building it into the cache on
    first use (keyed by the source's sha256). None when swiftc is missing
    or the build fails."""
    try:
        with open(VISION_SOURCE, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError:
        return None
    binary = os.path.join(CACHE_DIR, f"vision_faces-{digest}")
    if os.access(binary, os.X_OK):
        return binary
    if not os.access(SWIFTC, os.X_OK):
        return None
    os.makedirs(CACHE_DIR, exist_ok=True)
    proc = subprocess.run([SWIFTC, "-O", VISION_SOURCE, "-o", binary],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return binary if proc.returncode == 0 and os.access(binary, os.X_OK) else None


def face_boxes(images):
    """{image path: [box]} from the Vision helper, or None when the helper
    cannot run."""
    binary = vision_binary()
    if not binary:
        return None
    proc = subprocess.run([binary, *images], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        return None
    out = {}
    for line in proc.stdout.splitlines():
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        out[doc.get("path")] = doc.get("faces") or []
    return out


def _write_png(rgb, path):
    import cv2  # the system python has opencv; imported only when faces are wanted
    bgr = (np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8)[..., ::-1]
    if not cv2.imwrite(path, bgr):
        raise MeasureError(f"could not write {path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def measure(path, samples=10, segments=(), hero=None, faces=True):
    info = probe(path)
    depth = bit_depth(info["tags"]["pix_fmt"])
    rgb_source = is_rgb_source(info["tags"]["pix_fmt"])
    full_range = info["tags"].get("color_range") == "pc"
    vf, decode_note = decode_filter(info["tags"])
    frames, pooled = [], []
    with tempfile.TemporaryDirectory(prefix="rpresolve-measure-") as tmp:
        stills = []
        for i, t in enumerate(sample_times(info["duration"], samples)):
            rgb = read_frame(path, t, info["width"], info["height"], vf)
            pooled.append(rgb[::PIXEL_STRIDE * 2, ::PIXEL_STRIDE * 2])
            frame = {"t": round(t, 3), "luma": luma_stats(rgb), "clipping": clipping(rgb),
                     "legal_8bit": None if rgb_source or full_range else legal_range(path, t, depth),
                     "skin": None}
            if faces:
                still = os.path.join(tmp, f"f{i:03d}.png")
                _write_png(rgb, still)
                stills.append((frame, still, rgb))
            frames.append(frame)
        boxes = face_boxes([s for _, s, _ in stills]) if stills else None
        for frame, still, rgb in stills:
            if boxes is None:
                frame["skin"] = UNAVAILABLE
            else:
                frame["faces"] = len(boxes.get(still, []))
                frame["skin"] = skin_stats(rgb, boxes.get(still, []))
    all_px = np.concatenate([p.reshape(-1, 3) for p in pooled])
    legal = [f["legal_8bit"] for f in frames if f["legal_8bit"]]
    report = {
        "file": path, "tags": info["tags"], "decode_note": decode_note,
        "frames_sampled": len(frames),
        "luma": luma_stats(all_px.reshape(-1, 1, 3), stride=1),
        "clipping": clipping(all_px.reshape(-1, 1, 3), stride=1),
        "legal_8bit": ("not applicable (RGB source)" if rgb_source else
                       "not applicable (full-range source)" if full_range else
                       {"ymin": min(l[0] for l in legal), "ymax": max(l[1] for l in legal),
                        "within_16_235": all(16 <= l[0] and l[1] <= 235 for l in legal)}
                       if legal else None),
        "frames": frames,
    }
    skins = [f["skin"] for f in frames if isinstance(f.get("skin"), dict)]
    if not faces:
        report["skin"] = "not measured (--no-faces)"
    elif any(f.get("skin") == UNAVAILABLE for f in frames):
        report["skin"] = UNAVAILABLE + " (macOS Vision helper could not be built or run)"
    elif skins:
        lab = np.median([s["lab"] for s in skins], axis=0)
        hue = float(np.median([s["hue_deg"] for s in skins]))
        report["skin"] = {"frames_with_skin": len(skins),
                          "lab": [round(float(v), 3) for v in lab],
                          "chroma": round(float(color.chroma(lab)), 3),
                          "hue_deg": round(hue, 2),
                          "hue_off_skin_line_deg": round(hue - SKIN_LINE_DEG, 2)}
    else:
        report["skin"] = "no face with enough skin pixels in the sampled frames"
    if segments:
        report["camera_match"] = camera_match(frames, segments, hero)
    return report


def format_summary(report):
    """A short human-readable summary of a measure report."""
    lines = [f"measure: {report['file']}",
             f"  tags: " + ", ".join(f"{k}={v or '?'}" for k, v in report["tags"].items()),
             f"  frames sampled: {report['frames_sampled']}"
             + (f" ({report['decode_note']})" if report.get("decode_note") else ""),
             "  luma IRE p1/p50/p99: {p1} / {p50} / {p99}".format(**report["luma"]),
             "  clipping: {high_pct}% high, {low_pct}% low".format(**report["clipping"])]
    legal = report.get("legal_8bit")
    if isinstance(legal, dict):
        legal_text = (f"{legal['ymin']:.1f}..{legal['ymax']:.1f}, "
                      f"{'within' if legal['within_16_235'] else 'OUTSIDE'} 16..235")
    else:
        legal_text = legal or UNAVAILABLE
    lines.append("  legal range (8-bit Y): " + legal_text)
    skin = report.get("skin")
    if isinstance(skin, dict):
        lines.append(f"  skin: {skin['frames_with_skin']} frame(s); chroma {skin['chroma']}; "
                     f"hue {skin['hue_deg']} deg ({skin['hue_off_skin_line_deg']:+} vs the 123 deg line)")
    else:
        lines.append(f"  skin: {skin}")
    match = report.get("camera_match")
    if match:
        for p in match["pairs"]:
            lines.append(f"  {p['a']} vs {p['b']}: skin dE2000 {p['skin_de2000']}, "
                         f"luma p50 delta {p['luma_p50_delta_ire']} IRE")
        for k, v in (match.get("chroma_vs_hero_pct") or {}).items():
            lines.append(f"  {k} skin chroma vs hero: {v:+}%")
    return "\n".join(lines) + "\n"
