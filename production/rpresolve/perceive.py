"""
rpresolve.perceive: a perception packet for one frame, so a judgment about
light, colour and framing rests on scopes and numbers a reader can check,
not on an uncalibrated monitor. Offline: ffmpeg reads the frame; nothing
touches Resolve.

The packet is a new folder holding:
    frame.png        the frame as decoded, at most 1920 pixels wide
    waveform.png     luma waveform
    parade.png       R, G and B waveforms side by side
    vectorscope.png  Cb/Cr, each point in its own colour, with the 75%
                     targets and the skin-tone line
    histogram.png    R, G and B
    falsecolour.png  luma bands painted, everything else grey
    palette.png      the dominant colours, each as wide as its share
    sheet.png        frame, false colour, waveform, vectorscope and palette
                     in one image, for a reader that opens one file
    packet.json      the numbers, a legend for every image, the file's tags
With a reference still, the same set for it under ref/, and a compare block.

The numbers: luma percentiles in IRE and the display contrast in stops,
clipping, the share of the frame in each false-colour band, chroma, the
cast of the near-neutral pixels, a Lab palette, and per face (macOS Vision)
the skin Lab and hue against the 123 degree line, the skin's exposure, the
ratio of its lit side to its shadow side, and its size and place in frame.

The frame is decoded as measure decodes it: display-encoded Rec.709 with a
2.4 gamma, D65, and the luma percentiles are measure's own on that frame.
A log frame reads flat and low in chroma, so perceive a render with the
colour path applied. A file tagged PQ, HLG, linear or log gets a
transfer_note and no ratios (display contrast, lighting), since those need
the right curve. packet.json opens with a small header (schema, transfer,
generated_by, generated_at) that other sidecars can share. With targets=True a
targets.json (schema cine.targets; numbers only) joins it, for a reference
library. numpy only; PNGs via zlib.
"""

import json
import os
import shutil
import struct
import time
import zlib

import numpy as np

from . import color, measure, refs
from .paths import in_any_git_tree

FRAME_MAX_W = 1920
WAVE_W, WAVE_H = 512, 256
VECTOR = WAVE_H  # the sheet shows it at this size, so no resize thins its lines
HIST_W, HIST_H = 512, 160
PALETTE_K = 8
PALETTE_PIXELS = 20000
DISPLAY_BLACK = 0.001  # linear floor for the contrast figure: a 1000:1 display
NEUTRAL_CHROMA = 8.0   # Lab C* under which a midtone counts as near neutral
SIDE_MIN_PIXELS = 50   # skin pixels each half of a face needs for a lighting ratio
SHEET_W = 1280
BANDS = (("shadows", 0.0, 33.0), ("mids", 33.0, 66.0), ("highlights", 66.0, 100.001))  # luma IRE
READ_AS = "BT.1886 display: Rec.709 primaries, 2.4 gamma, D65"
NOT_REC709 = {"smpte2084": "PQ", "arib-std-b67": "HLG", "linear": "linear", "log100": "log", "log316": "log"}

# ARRI's camera false-colour layout, in percent of the video signal.
FALSE_COLOUR = (
    ("purple", 0.0, 2.5, (150, 0, 200), "black clip"),
    ("blue", 2.5, 4.0, (0, 90, 255), "just above black clip"),
    ("green", 38.0, 42.0, (0, 210, 0), "18% grey"),
    ("pink", 52.0, 56.0, (255, 120, 190), "one stop over 18% grey"),
    ("yellow", 97.0, 99.0, (255, 235, 0), "just below white clip"),
    ("red", 99.0, 100.0, (255, 0, 0), "white clip"),
)
TARGETS = {"R": (1, 0, 0), "Yl": (1, 1, 0), "G": (0, 1, 0), "Cy": (0, 1, 1), "B": (0, 0, 1), "Mg": (1, 0, 1)}

LEGEND = {
    "frame": "The decoded frame, display-encoded Rec.709, downscaled to at most 1920 wide.",
    "waveform": "Luma (Rec.709 weights) per frame column. x: frame left to right. y: 0 IRE at the "
                "bottom to 100 at the top; amber lines every 10 IRE, brighter at 0, 50 and 100.",
    "parade": "R, G and B waveforms side by side, left to right, each with the waveform's IRE lines.",
    "vectorscope": "Cb right, Cr up, centre neutral, edge 100% saturation. Each point drawn in the "
                   "average colour of the pixels there. Squares: the 75% targets in their own "
                   "colours (R near 103 degrees, then Mg, B, Cy, G, Yl). Orange line: the skin-tone "
                   "line at 123 degrees.",
    "histogram": "R, G and B counts, 0 at the left to full scale at the right, added together "
                 "(white where all three overlap). A spike at either end is clipping.",
    "falsecolour": "Each band of FALSE_COLOUR painted in its colour; every other pixel is its luma "
                   "in grey, dimmed. Shares are in false_colour.",
    "palette": "The dominant colours (k-means in Lab), each as wide as its share of the frame.",
    "sheet": "Frame (top left) and false colour (top right); waveform and vectorscope below; the "
             "palette strip at the bottom. Each is a resized copy of its own file.",
}


class PerceiveError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# PNG
# ---------------------------------------------------------------------------

def png_bytes(img):
    """An 8-bit RGB PNG of uint8 array (h, w, 3)."""
    h, w = img.shape[:2]
    rows = np.concatenate([np.zeros((h, 1), np.uint8), np.ascontiguousarray(img, np.uint8).reshape(h, w * 3)],
                          axis=1)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows.tobytes(), 6)) + chunk(b"IEND", b""))


def write_png(path, img):
    with open(path, "wb") as f:
        f.write(png_bytes(img))


def resize(img, w, h):
    """Nearest-neighbour resize of an image array."""
    return img[np.arange(h) * img.shape[0] // h][:, np.arange(w) * img.shape[1] // w]


def to_u8(rgb):
    return (np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8)


def downscale(rgb, max_w=FRAME_MAX_W):
    """Box-average by the smallest whole factor that brings the width to max_w or under."""
    k = -(-rgb.shape[1] // max_w)
    if k <= 1:
        return rgb
    h, w = rgb.shape[0] // k * k, rgb.shape[1] // k * k
    return rgb[:h, :w].reshape(h // k, k, w // k, k, 3).mean(axis=(1, 3))


# ---------------------------------------------------------------------------
# Scopes (numpy arrays in, uint8 images out)
# ---------------------------------------------------------------------------

def _density(index, size):
    counts = np.bincount(index, minlength=size).astype(float)
    return np.log1p(counts) / np.log1p(counts.max()) if counts.max() else counts


def _wave(values, w, h, tint):
    """Waveform of values (rows, cols) in 0..1: trace in tint on black with IRE lines."""
    rows, cols = values.shape
    col = np.broadcast_to((np.arange(cols) * w // cols)[None, :], values.shape)
    lvl = np.clip(np.rint((1 - values) * (h - 1)), 0, h - 1).astype(int)
    dens = _density((lvl * w + col).ravel(), w * h).reshape(h, w)
    img = dens[..., None] * np.asarray(tint, float)[None, None, :]
    for ire in range(0, 101, 10):
        y = int(round((1 - ire / 100) * (h - 1)))
        line = (200, 150, 0) if ire in (0, 50, 100) else (90, 65, 0)
        img[y] = np.maximum(img[y], line)
    return img.astype(np.uint8)


def waveform(rgb, w=WAVE_W, h=WAVE_H):
    return _wave(color.luma_ire(rgb) / 100, w, h, (235, 235, 235))


def parade(rgb, w=WAVE_W, h=WAVE_H):
    third = w // 3
    tints = ((255, 70, 70), (70, 255, 70), (90, 140, 255))
    return np.concatenate([_wave(rgb[..., c], third, h, tints[c]) for c in range(3)], axis=1)


def _cbcr(rgb):
    y = color._dot3(rgb, color.REC709_LUMA)
    return (rgb[..., 2] - y) / 1.8556, (rgb[..., 0] - y) / 1.5748


def vectorscope(rgb, size=VECTOR):
    """Cb/Cr scatter, each bin in the mean colour of its pixels, brightness by count."""
    px = rgb.reshape(-1, 3)
    cb, cr = _cbcr(px)
    c, r = size // 2, size // 2 - 4  # centre and the radius of 100% saturation (|Cb| or |Cr| = 0.5)
    x = np.clip(np.rint(c + cb / 0.5 * r), 0, size - 1).astype(int)
    y = np.clip(np.rint(c - cr / 0.5 * r), 0, size - 1).astype(int)
    idx = y * size + x
    counts = np.bincount(idx, minlength=size * size).astype(float)
    mean = np.stack([np.bincount(idx, px[:, k], size * size) for k in range(3)], axis=1)
    mean /= np.maximum(counts, 1)[:, None]
    mean /= np.maximum(mean.max(axis=1, keepdims=True), 1e-6)  # full brightness hue; density sets level
    dens = _density(idx, size * size)
    img = (mean * (0.25 + 0.75 * dens[:, None]) * (counts > 0)[:, None] * 255).reshape(size, size, 3)
    img[c, :] = np.maximum(img[c, :], 40)
    img[:, c] = np.maximum(img[:, c], 40)
    for t in np.linspace(0, r, 4 * r):  # the skin-tone line
        a = np.radians(measure.SKIN_LINE_DEG)
        img[int(round(c - t * np.sin(a))), int(round(c + t * np.cos(a)))] = (255, 140, 0)
    for rgb_t in TARGETS.values():
        tcb, tcr = _cbcr(0.75 * np.array(rgb_t, float))
        tx, ty = int(round(c + tcb / 0.5 * r)), int(round(c - tcr / 0.5 * r))
        box = np.array(rgb_t, float) * 255
        img[ty - 4:ty + 5, [tx - 4, tx + 4]] = box
        img[[ty - 4, ty + 4], tx - 4:tx + 5] = box
    return img.astype(np.uint8)


def histogram(rgb, w=HIST_W, h=HIST_H):
    """R, G and B 256-bin histograms added together; the end bins may run off the top."""
    codes = np.clip(np.rint(rgb.reshape(-1, 3) * 255), 0, 255).astype(int)
    img = np.zeros((h, w, 3))
    rows = np.arange(h)[:, None]
    for k in range(3):
        counts = np.bincount(codes[:, k], minlength=256).astype(float)
        top = counts[1:255].max() or counts.max() or 1
        heights = (np.minimum(counts / top, 1.0) * (h - 1))[np.arange(w) * 256 // w]
        img[..., k] += (rows >= (h - 1 - heights)[None, :]) * 255
    for q in (1, 2, 3):
        img[:, q * w // 4] = np.maximum(img[:, q * w // 4], 60)
    return np.clip(img, 0, 255).astype(np.uint8)


def false_colour(rgb, sample=None):
    """(image, shares): the bands painted, the rest dimmed luma grey; shares in
    percent, taken on `sample` (default rgb) so a downscaled image can carry
    shares from the full frame, where small clipped highlights survive."""
    y = color.luma_ire(rgb)
    ys = y if sample is None else color.luma_ire(sample)
    img = np.repeat((y / 100 * 0.6 * 255)[..., None], 3, axis=-1)

    def band(v, lo, hi):
        return (v >= lo) & ((v <= hi) if hi >= 100 else (v < hi))
    shares = []
    for name, lo, hi, paint, meaning in FALSE_COLOUR:
        img[band(y, lo, hi)] = paint
        shares.append({"band": name, "ire": [lo, hi], "meaning": meaning,
                       "pct": round(float(band(ys, lo, hi).mean()) * 100, 2)})
    return img.astype(np.uint8), shares


def sheet(images, w=SHEET_W):
    half = w // 2
    fh = max(1, round(images["frame"].shape[0] * half / images["frame"].shape[1]))
    top = np.concatenate([resize(images["frame"], half, fh), resize(images["falsecolour"], half, fh)], axis=1)
    mid = np.concatenate([resize(images["waveform"], w - WAVE_H, WAVE_H),
                          resize(images["vectorscope"], WAVE_H, WAVE_H)], axis=1)
    return np.concatenate([top, mid, resize(images["palette"], w, 32)], axis=0)


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------

def _linear_luma(rgb):
    return color._dot3(np.clip(rgb, 0, 1) ** 2.4, color.REC709_LUMA)


def luma_numbers(rgb, stride=measure.PIXEL_STRIDE):
    """Luma percentiles in IRE over the pixels measure.luma_stats samples, so
    p1, p50 and p99 equal measure's for the same frame; display contrast in stops."""
    rgb = rgb[::stride, ::stride]
    ire = np.percentile(color.luma_ire(rgb), [1, 10, 50, 90, 99])
    lin = np.maximum(np.percentile(_linear_luma(rgb), [1, 99]), DISPLAY_BLACK)
    return {"ire": {k: round(float(v), 2) for k, v in zip(("p1", "p10", "p50", "p90", "p99"), ire)},
            "display_contrast_stops": round(float(np.log2(lin[1] / lin[0])), 2)}


def colour_numbers(lab):
    """Chroma percentiles and the median a*, b* of near-neutral midtones."""
    c = color.chroma(lab)
    neutral = (c < NEUTRAL_CHROMA) & (lab[:, 0] > 15) & (lab[:, 0] < 95)
    cast = None
    if neutral.mean() >= 0.005:
        a, b = np.median(lab[neutral, 1]), np.median(lab[neutral, 2])
        cast = {"a": round(float(a), 2), "b": round(float(b), 2),
                "pct_of_frame": round(float(neutral.mean()) * 100, 2)}
    return {"chroma": {"p50": round(float(np.percentile(c, 50)), 2), "p90": round(float(np.percentile(c, 90)), 2)},
            "neutral_cast": cast}


def palette(px, lab, k=PALETTE_K, iters=25):
    """k-means in Lab (k-means++ seeded, fixed seed): [{hex, lab, chroma, hue_deg, share_pct}],
    largest share first. px and lab are matching (n, 3) arrays."""
    rng = np.random.default_rng(0)
    centres = [lab[rng.integers(len(lab))]]
    for _ in range(1, k):
        d = np.min([((lab - m) ** 2).sum(axis=1) for m in centres], axis=0)
        if d.sum() <= 0:
            break
        centres.append(lab[rng.choice(len(lab), p=d / d.sum())])
    centres = np.array(centres)
    for _ in range(iters):
        label = np.argmin(((lab[:, None, :] - centres[None]) ** 2).sum(axis=2), axis=1)
        new = np.array([lab[label == i].mean(axis=0) if (label == i).any() else centres[i]
                        for i in range(len(centres))])
        if np.allclose(new, centres, atol=1e-3):
            break
        centres = new
    label = np.argmin(((lab[:, None, :] - centres[None]) ** 2).sum(axis=2), axis=1)
    out = []
    for i in range(len(centres)):
        mask = label == i
        if not mask.any():
            continue
        m, rgb = lab[mask].mean(axis=0), px[mask].mean(axis=0)
        out.append({"hex": "#" + "".join(f"{v:02x}" for v in to_u8(rgb)),
                    "lab": [round(float(v), 2) for v in m],
                    "chroma": round(float(color.chroma(m)), 2),
                    "hue_deg": round(float(np.degrees(np.arctan2(m[2], m[1])) % 360), 1),
                    "share_pct": round(float(mask.mean()) * 100, 2)})
    return sorted(out, key=lambda e: -e["share_pct"])


def palette_strip(entries, w=WAVE_W, h=48):
    img = np.zeros((h, w, 3), np.uint8)
    x = 0
    for e in entries:
        dx = int(round(e["share_pct"] / 100 * w))
        img[:, x:x + dx] = [int(e["hex"][i:i + 2], 16) for i in (1, 3, 5)]
        x += dx
    return img


def face_entry(rgb, box):
    """Skin, exposure, lighting ratio, size and place for one Vision face box
    (normalized, top-left origin)."""
    entry = {"box": {k: round(float(box[k]), 4) for k in ("x", "y", "w", "h")},
             "centre": [round(float(box["x"] + box["w"] / 2), 3), round(float(box["y"] + box["h"] / 2), 3)],
             "area_pct": round(float(box["w"] * box["h"]) * 100, 2),
             "skin": measure.skin_stats(rgb, [box]), "skin_ire_p50": None, "lighting": None}
    region = measure.face_region(rgb, box)
    mask = measure.skin_mask(region)
    if mask.any():
        entry["skin_ire_p50"] = round(float(np.median(color.luma_ire(region[mask]))), 2)
    half = region.shape[1] // 2
    sides = []
    for part, m in ((region[:, :half], mask[:, :half]), (region[:, half:], mask[:, half:])):
        sides.append(float(np.median(_linear_luma(part[m]))) if m.sum() >= SIDE_MIN_PIXELS else None)
    if None not in sides and min(sides) > 0:
        lit = "left" if sides[0] >= sides[1] else "right"
        ratio = max(sides) / min(sides)
        entry["lighting"] = {"lit_side": lit, "ratio": round(ratio, 2), "stops": round(float(np.log2(ratio)), 2)}
    return entry


# ---------------------------------------------------------------------------
# One frame, one folder
# ---------------------------------------------------------------------------

def read(path, at=None):
    """(display RGB float array, source dict) for the frame at `at` seconds,
    default the middle; a still has no duration and reads at 0. The source's
    file is recorded as an absolute path, the key other sidecars join on."""
    info = measure.probe(path)
    if at is None:
        at = measure.sample_times(info["duration"], 1)[0]
    elif at < 0 or (info["duration"] > 0 and at > info["duration"]):
        raise PerceiveError(f"--at {at} is outside {path} (0..{info['duration']:.3f}s)")
    vf, note = measure.decode_filter(info["tags"])
    rgb = measure.read_frame(path, at, info["width"], info["height"], vf)
    tag = info["tags"].get("color_transfer") or "untagged"
    source = {"file": os.path.abspath(path), "t": round(at, 3), "width": info["width"], "height": info["height"],
              "tags": info["tags"], "decode_note": note, "transfer": {"tag": tag, "read_as": READ_AS}}
    if tag in NOT_REC709:
        source["transfer_note"] = (f"tagged {NOT_REC709[tag]}: every number here assumes display Rec.709 "
                                   "with a 2.4 gamma, so it misreads this file; ratios are withheld")
    return rgb, source


def withhold_ratios(numbers):
    """Blank the numbers that need the right transfer curve."""
    numbers["luma"]["display_contrast_stops"] = None
    for face in numbers["faces"] if isinstance(numbers["faces"], list) else []:
        face["lighting"] = None


def analyse(rgb, folder, faces=True):
    """Write the images for one frame into folder and return its numbers.
    Scopes, chroma and palette use the frame as downscaled for frame.png, so
    they line up with it; luma, false-colour shares, clipping and faces use
    the full frame, sampled as measure samples it."""
    small = downscale(rgb)
    fc_img, bands = false_colour(small, rgb[::measure.PIXEL_STRIDE, ::measure.PIXEL_STRIDE])
    px = small.reshape(-1, 3)[::max(1, small.shape[0] * small.shape[1] // PALETTE_PIXELS)]
    lab = color.rec709_to_lab(px)
    pal = palette(px, lab)
    images = {"frame": to_u8(small), "waveform": waveform(small), "parade": parade(small),
              "vectorscope": vectorscope(small), "histogram": histogram(small),
              "falsecolour": fc_img, "palette": palette_strip(pal)}
    images["sheet"] = sheet(images)
    files = {}
    for name, img in images.items():
        files[name] = name + ".png"
        write_png(os.path.join(folder, files[name]), img)
    out = {"files": files, "luma": luma_numbers(rgb), "clipping": measure.clipping(rgb),
           "false_colour": bands, **colour_numbers(lab), "palette": pal}
    if not faces:
        out["faces"] = "not measured (--no-faces)"
    else:
        boxes = measure.face_boxes([os.path.join(folder, files["frame"])])
        if boxes is None:
            out["faces"] = measure.UNAVAILABLE + " (macOS Vision helper could not be built or run)"
        else:
            found = sorted(boxes.get(os.path.join(folder, files["frame"]), []), key=lambda b: -b["w"] * b["h"])
            out["faces"] = [face_entry(rgb, b) for b in found]
    return out


def _largest_skin(numbers):
    faces = numbers.get("faces")
    if isinstance(faces, list):
        for f in faces:
            if f.get("skin"):
                return f["skin"]["lab"]
    return None


def band_colour(rgb, stride=measure.PIXEL_STRIDE):
    """Mean Lab chroma and hue of the pixels in each fixed luma band (shadows
    under 33 IRE, mids 33 to 66, highlights 66 and over), over the pixels
    luma_numbers samples, plus the hue shift: shadows minus highlights in
    degrees, wrapped to (-180, 180]. An empty band is None."""
    px = rgb[::stride, ::stride].reshape(-1, 3)
    ire = color.luma_ire(px)
    lab = color.rec709_to_lab(px)
    out = {}
    for name, lo, hi in BANDS:
        m = (ire >= lo) & (ire < hi)
        if not m.any():
            out[name] = None
            continue
        a, b = float(lab[m, 1].mean()), float(lab[m, 2].mean())
        out[name] = {"share_pct": round(float(m.mean()) * 100, 2), "chroma": round(float(np.hypot(a, b)), 2),
                     "hue_deg": round(float(np.degrees(np.arctan2(b, a)) % 360), 1)}
    shift = None
    if out["shadows"] and out["highlights"]:
        d = out["shadows"]["hue_deg"] - out["highlights"]["hue_deg"]
        shift = round((d + 180) % 360 - 180, 1)
    return {"bands": out, "hue_shift_shadows_vs_highlights_deg": shift}


def targets(rgb, source, numbers):
    """The numbers a grade is matched to, from one hero still: luma
    percentiles, display contrast, Lab palette, per-band chroma and hue, the
    shadow-against-highlight hue shift, the near-neutral cast and the largest
    face's skin Lab. `numbers` is analyse()'s dict. Numbers only, no pixels.
    Bands and contrast are None when the transfer tag is not Rec.709."""
    bands = {"bands": None, "hue_shift_shadows_vs_highlights_deg": None} if "transfer_note" in source \
        else band_colour(rgb)
    out = {"schema": {"name": "cine.targets", "version": 1}, "transfer": source["transfer"],
           "generated_by": "rpresolve.perceive",
           "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "bands_split": "luma IRE: shadows < 33, mids 33 to 66, highlights >= 66",
           "luma_ire": numbers["luma"]["ire"], "display_contrast_stops": numbers["luma"]["display_contrast_stops"],
           "palette": [{k: p[k] for k in ("lab", "share_pct")} for p in numbers["palette"]],
           **bands, "neutral_cast": numbers["neutral_cast"], "skin_lab": _largest_skin(numbers)}
    if "transfer_note" in source:
        out["transfer_note"] = source["transfer_note"]
    return out


def compare(frame, ref):
    """How the frame differs from the reference: frame minus reference."""
    out = {"luma_ire_delta": {k: round(frame["luma"]["ire"][k] - ref["luma"]["ire"][k], 2)
                              for k in frame["luma"]["ire"]},
           "display_contrast_stops_delta": (round(frame["luma"]["display_contrast_stops"]
                                                  - ref["luma"]["display_contrast_stops"], 2)
                                            if None not in (frame["luma"]["display_contrast_stops"],
                                                            ref["luma"]["display_contrast_stops"]) else None),
           "chroma_p50_vs_ref_pct": (round((frame["chroma"]["p50"] / ref["chroma"]["p50"] - 1) * 100, 1)
                                     if ref["chroma"]["p50"] else None),
           "neutral_cast_delta": None, "skin_de2000": None}
    if frame["neutral_cast"] and ref["neutral_cast"]:
        out["neutral_cast_delta"] = {k: round(frame["neutral_cast"][k] - ref["neutral_cast"][k], 2) for k in "ab"}
    fs, rs = _largest_skin(frame), _largest_skin(ref)
    if fs and rs:
        out["skin_de2000"] = round(float(color.delta_e_2000(np.array(fs), np.array(rs))), 2)
    # Each frame colour against its nearest reference colour, weighted by share.
    ref_labs = np.array([p["lab"] for p in ref["palette"]])
    near = [(p["share_pct"], float(np.min(color.delta_e_2000(np.array(p["lab"])[None], ref_labs))))
            for p in frame["palette"]]
    total = sum(s for s, _ in near)
    out["palette_de2000"] = round(sum(s * d for s, d in near) / total, 2) if total else None
    return out


def out_dir_problem(out):
    """Why `out` cannot take a packet, or None. The packet holds frames of the
    source, so it never goes inside a git working tree."""
    if refs.is_reference(out):
        return refs.reference_problem(out)
    if in_any_git_tree(out):
        return f"{out} is inside a git working tree. The packet holds frames of the media; write it outside."
    if os.path.exists(out) and (not os.path.isdir(out) or os.listdir(out)):
        return f"{out} exists and is not an empty folder; name a new one."
    if not os.path.isdir(os.path.dirname(os.path.abspath(out))):
        return f"the folder above {out} does not exist."
    return None


def perceive(path, out, at=None, ref=None, faces=True, with_targets=False):
    """Write a packet for the frame of `path` at `at` (and `ref`, a still or
    the middle of a clip) into the new folder `out`; return packet.json's dict."""
    problem = out_dir_problem(out)
    if problem:
        raise PerceiveError(problem)
    rgb, source = read(path, at)
    ref_rgb, ref_source = read(ref) if ref else (None, None)
    created = not os.path.isdir(out)
    if created:
        os.mkdir(out)
    try:
        packet = _write(out, rgb, source, ref, ref_rgb, ref_source, faces, with_targets)
    except BaseException:
        # The folder was new or empty at the start, so all of it is this run's.
        shutil.rmtree(out, ignore_errors=True)
        if not created:
            os.makedirs(out, exist_ok=True)
        raise
    return packet


def _write(out, rgb, source, ref, ref_rgb, ref_source, faces, with_targets=False):
    packet = {"schema": {"name": "cine.perceive", "version": 1}, "transfer": source["transfer"],
              "generated_by": "rpresolve.perceive",
              "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "source": source, "legend": LEGEND, **analyse(rgb, out, faces)}
    if "transfer_note" in source:
        withhold_ratios(packet)
    if ref:
        os.mkdir(os.path.join(out, "ref"))
        packet["reference"] = {"source": ref_source, **analyse(ref_rgb, os.path.join(out, "ref"), faces)}
        packet["reference"]["files"] = {k: "ref/" + v for k, v in packet["reference"]["files"].items()}
        if "transfer_note" in ref_source:
            withhold_ratios(packet["reference"])
        packet["compare"] = compare(packet, packet["reference"])
    with open(os.path.join(out, "packet.json"), "w", encoding="utf-8") as f:
        json.dump(packet, f, indent=2)
        f.write("\n")
    if with_targets:
        with open(os.path.join(out, "targets.json"), "w", encoding="utf-8") as f:
            json.dump(targets(rgb, source, packet), f, indent=2)
            f.write("\n")
    return packet


def format_summary(packet, out):
    ire = packet["luma"]["ire"]
    lines = [f"perceive: {packet['source']['file']} at {packet['source']['t']}s -> {out}"]
    lines += [f"  WARNING: {r['source']['transfer_note']}" for r in (packet, packet.get("reference") or {})
              if r.get("source", {}).get("transfer_note")]
    lines += [
             f"  luma IRE p1/p10/p50/p90/p99: {ire['p1']} / {ire['p10']} / {ire['p50']} / {ire['p90']} / {ire['p99']}"
             f"; display contrast {packet['luma']['display_contrast_stops']} stops",  # None when withheld
             "  false colour: " + ", ".join(f"{b['band']} {b['pct']}%" for b in packet["false_colour"] if b["pct"]),
             f"  chroma p50/p90: {packet['chroma']['p50']} / {packet['chroma']['p90']}; neutral cast: "
             + (f"a {packet['neutral_cast']['a']:+}, b {packet['neutral_cast']['b']:+}" if packet["neutral_cast"]
                else "too few neutral pixels"),
             "  palette: " + " ".join(f"{p['hex']} {p['share_pct']}%" for p in packet["palette"])]
    faces = packet["faces"]
    if isinstance(faces, list):
        lines.append(f"  faces: {len(faces)}")
        for f in faces:
            skin = f["skin"]
            lit = f["lighting"]
            lines.append(f"    area {f['area_pct']}% centre {f['centre']}; skin "
                         + (f"hue {skin['hue_deg']} ({skin['hue_off_skin_line_deg']:+} vs 123), chroma {skin['chroma']}"
                            if skin else "too few skin pixels")
                         + (f"; {f['skin_ire_p50']} IRE" if f["skin_ire_p50"] is not None else "")
                         + (f"; {lit['lit_side']} side lit {lit['ratio']}:1 ({lit['stops']} stops)" if lit else ""))
    else:
        lines.append(f"  faces: {faces}")
    cmp_ = packet.get("compare")
    if cmp_:
        lines.append(f"  vs reference: luma p50 {cmp_['luma_ire_delta']['p50']:+} IRE, contrast "
                     + (f"{cmp_['display_contrast_stops_delta']:+}" if cmp_["display_contrast_stops_delta"] is not None
                        else "n/a")
                     + " stops, chroma "
                     + (f"{cmp_['chroma_p50_vs_ref_pct']:+}%" if cmp_["chroma_p50_vs_ref_pct"] is not None else "n/a")
                     + ", "
                     f"palette dE2000 {cmp_['palette_de2000']}, skin dE2000 {cmp_['skin_de2000']}")
    return "\n".join(lines) + "\n"
