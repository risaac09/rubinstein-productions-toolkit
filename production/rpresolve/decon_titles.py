"""
rpresolve.decon_titles: on-screen text in a reference video, for `decon recipe --titles` and
`decon compare --titles`. macOS only: the text is read by Vision's text request through
vision_text.swift (rpresolve.vision); elsewhere detect() raises TitlesUnavailable.

    detect(video, fps)       [title]: each line of text with its in and out times, box and animation
    score(ref, rb, fps)      title timing error between two title lists, and a 0 to 1 part

A title is {"text", "in_s", "out_s", "box": {x, y, w, h}, "animation", "animation_out"}: times on
the video's own clock (in is the first frame drawn, out the frame after the last), the box
normalized with the origin top left (Vision's line box, ascent to descent) where the title rests,
and the animation in and out, one of ANIMATIONS.

How. Every STRIDE-th frame is read; a line is followed from sample to sample while its text stays
the same, or one is a word prefix of the other (a word-by-word reveal), or they are within
SIMILAR of each other (a misread letter). A line gone for two samples ends. Each edge is then
found to the frame: Vision reads every frame between the last sample without the line and the
first with it, and from the first frame it reads, the in point walks back while the line's box
still differs from the empty frame before it by FAINT of the settled title's difference (a fade
is drawn before Vision can read it). The out point is found the same way forward.

Animation in: "word" when the text grows by whole words while on screen; "slide" when the box at
the first read frame sits more than MOVED from where it rests; "fade" when the first drawn frame
differs from the empty one by under HALF of the settled title's difference; else "cut". The
same tests, without "word", give animation_out.

Limits: the walk compares against the empty frame, so over moving footage it runs on to that
sample: an in point up to STRIDE - 1 frames early, an out point as late (exact on a still
background). Times are frame index over fps, so a file whose video starts at a non-zero
timestamp, or with a variable frame rate, is off by that against pts-based shot times. A slide
from outside the frame is timed from the first frame Vision can read. Text in the footage itself
(a sign, a shirt) is reported like a title. Two lines are two titles.
"""

import difflib
import os
import statistics
import subprocess
import tempfile

from . import delivercheck as dc, vision

STRIDE = 4            # frames between samples on the first pass
MIN_CONF = 0.3        # Vision lines under this confidence are dropped
SIMILAR = 0.8         # difflib ratio for "the same line, a letter misread"
FAINT = 0.05          # share of the settled difference that still counts as drawn
HALF = 0.6            # a first frame under this share of it is a fade
MOVED = 0.02          # box centre shift (share of the frame) that makes a slide
READ_W = 1280         # frames are read at most this wide
BATCH = 200           # images per Vision call, well inside vision.TIMEOUT_S
TITLE_SCALE = 12.0    # mean in and out error, frames, that scores zero in compare
ANIMATIONS = ("cut", "fade", "slide", "word")


class TitlesUnavailable(ValueError):
    pass


def _norm(t):
    return " ".join(t.split())


def related(a, b):
    """Same line: equal, one a word prefix of the other, or within SIMILAR."""
    a, b = _norm(a).lower(), _norm(b).lower()
    if a == b:
        return True
    wa, wb = a.split(), b.split()
    short, long_ = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    if short and long_[:len(short)] == short:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= SIMILAR


def _size(w, h):
    s = min(1.0, READ_W / w)
    return max(2, int(w * s) // 2 * 2), max(2, int(h * s) // 2 * 2)


def _read(video, frames, size, tmp, tag, raw=True, fps=None):
    """{frame: (png path, HxWx3 uint8 or None)} for the given frame numbers (a range with a step
    is selected as one expression). raw=False writes the PNGs only, so a long coarse pass holds
    no frames in memory. With fps, an explicit list is read from a seek to its first frame, not
    by decoding the file from the start (a long reference made each read a full pass)."""
    import numpy as np
    step = frames.step if isinstance(frames, range) else None
    frames = sorted(set(frames))
    if not frames:
        return {}
    w, h = size
    seek = fps and not step and frames[0] > 2
    off = frames[0] if seek else 0
    sel = (f"not(mod(n\\,{step}))" if step and frames[0] == 0 else "+".join(f"eq(n\\,{n - off})" for n in frames))
    vf = f"select='{sel}',scale={w}:{h}"
    base = [dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error"] + (["-ss", f"{(off - 0.5) / fps:.4f}"] if seek else []) \
        + ["-i", dc._src(video), "-map", "0:v:0", "-vf", vf, "-fps_mode", "passthrough", "-frames:v", str(len(frames))]
    pat = os.path.join(tmp, f"{tag}-%06d.png")
    p = subprocess.run(base + ["-y", pat], stdin=subprocess.DEVNULL, capture_output=True, timeout=dc.READ_TIMEOUT_S)
    if p.returncode != 0:
        raise TitlesUnavailable(f"ffmpeg could not read frames of {video}: {p.stderr.decode(errors='replace')[-200:]}")
    if not raw:
        return {f: (pat % (i + 1), None) for i, f in enumerate(frames) if os.path.exists(pat % (i + 1))}
    r = subprocess.run(base + ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdin=subprocess.DEVNULL,
                       capture_output=True, timeout=dc.READ_TIMEOUT_S)
    if r.returncode != 0:
        raise TitlesUnavailable(f"ffmpeg could not read frames of {video}: {r.stderr.decode(errors='replace')[-200:]}")
    n = len(r.stdout) // (w * h * 3)
    imgs = np.frombuffer(r.stdout[:n * w * h * 3], dtype=np.uint8).reshape(n, h, w, 3)
    return {f: (pat % (i + 1), imgs[i]) for i, f in enumerate(frames[:n])}


def _lines(read):
    """{frame: [line]} through Vision, low-confidence and empty lines dropped."""
    paths, got = [p for p, _ in read.values()], {}
    for i in range(0, len(paths), BATCH):
        part = vision.text_boxes(paths[i:i + BATCH])
        if part is None:
            raise TitlesUnavailable("the Vision text helper failed or timed out (it runs on macOS with swiftc only)")
        got.update(part)
    out = {}
    for f, (p, _) in read.items():
        out[f] = [dict(x, text=_norm(x["text"])) for x in got.get(p, [])
                  if x.get("confidence", 0) >= MIN_CONF and any(c.isalnum() for c in x.get("text", ""))]
    return out


def _track(samples):
    """Lines followed across samples: [{"hits": [(frame, line)], "last_i": last sample index}]."""
    tracks, done = [], []
    for i, (f, lines) in enumerate(samples):
        used = set()
        for ln in sorted(lines, key=lambda x: -x["confidence"]):
            cand = [t for t in tracks if id(t) not in used and related(t["hits"][-1][1]["text"], ln["text"])]
            if cand:
                t = max(cand, key=lambda t: _iou(t["hits"][-1][1], ln))
            else:
                t = {"hits": []}
                tracks.append(t)
            t["hits"].append((f, ln))
            t["last_i"] = i
            used.add(id(t))
        for t in [t for t in tracks if i - t["last_i"] >= 2]:   # gone two samples running
            tracks.remove(t)
            done.append(t)
    return done + tracks


def _iou(a, b):
    ix = max(0.0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
    iy = max(0.0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
    inter = ix * iy
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def _diff(img, empty, box):
    """Mean absolute difference (0 to 255) of img against the empty frame inside box."""
    import numpy as np
    h, w = img.shape[:2]
    x0, y0 = int(box["x"] * w), int(box["y"] * h)
    x1, y1 = max(x0 + 1, int((box["x"] + box["w"]) * w)), max(y0 + 1, int((box["y"] + box["h"]) * h))
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return float(np.abs(img[y0:y1, x0:x1].astype(np.int16) - empty[y0:y1, x0:x1]).mean())


def _inside(b):
    """Box clamped into the frame: Vision's line box can overhang an edge by a few pixels."""
    x, y = max(0.0, b["x"]), max(0.0, b["y"])
    return {"x": round(x, 4), "y": round(y, 4), "w": round(min(b["w"], 1 - x), 4), "h": round(min(b["h"], 1 - y), 4)}


def _centre(b):
    return b["x"] + b["w"] / 2, b["y"] + b["h"] / 2


def _edge(video, size, tmp, t, side, nframes, rest, full, fps=None):
    """(edge frame, animation) for one side ("in" or "out") of a track."""
    hits = t["hits"]
    if side == "in":
        known, text = hits[0][0], hits[0][1]["text"]
        bound = hits[0][0] - STRIDE            # the sample before, without the line
        gap = list(range(max(0, bound + 1), known))
    else:
        known, text = hits[-1][0], hits[-1][1]["text"]
        bound = hits[-1][0] + STRIDE
        gap = list(range(known + 1, min(nframes, bound)))
    has_empty = 0 <= bound < nframes
    read = _read(video, gap + [known] + ([bound] if has_empty else []), size, tmp, f"{side}{known}", fps=fps)
    lines = _lines({f: v for f, v in read.items() if f in gap})
    order = sorted(lines, reverse=(side == "out"))   # outermost frame first
    edge, box = known, (hits[0] if side == "in" else hits[-1])[1]
    for f in order:
        x = next((x for x in lines[f] if related(text, x["text"])), None)
        if x:
            edge, box = f, x
            break
    box = {k: box[k] for k in ("x", "y", "w", "h")}
    moved = max(abs(a - b) for a, b in zip(_centre(box), _centre(rest))) > MOVED
    ratio = 1.0   # no empty frame on this side: the title is on at the video's first or last frame
    if has_empty and full > 0 and edge in read:
        empty, step = read[bound][1], (-1 if side == "in" else 1)
        while edge + step != bound and (edge + step) in read \
                and _diff(read[edge + step][1], empty, box) >= FAINT * full:
            edge += step
        ratio = _diff(read[edge][1], empty, box) / full
    return edge, ("slide" if moved else "fade" if ratio < HALF else "cut")


def detect(video, fps, nframes, dims, min_h=0.0):
    """[title] for video (fps, frame count, (width, height)), sorted by in time. min_h drops lines whose box is
    shorter than that share of the frame (a screen recording's interface text reads as hundreds of titles)."""
    if not fps or not nframes:
        raise TitlesUnavailable(f"{video} has no frame rate or frame count")
    if vision.vision_binary(vision.TEXT_SOURCE) is None:
        raise TitlesUnavailable("the Vision text helper cannot run (macOS with swiftc only)")
    size = _size(*dims)
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        lines = _lines(_read(video, range(0, nframes, STRIDE), size, tmp, "s", raw=False))
        samples = [(f, lines[f]) for f in sorted(lines)]
        for t in _track(samples):
            if len(t["hits"]) < 2:   # one sample is OCR flicker on a busy frame (a screen recording gave hundreds)
                continue
            texts = [ln["text"] for _, ln in t["hits"]]
            words = [len(x.split()) for x in texts]
            final = statistics.mode([x for x, n in zip(texts, words) if n == max(words)])
            full_hits = [ln for _, ln in t["hits"] if ln["text"] == final]
            rest = _inside({k: statistics.median(ln[k] for ln in full_hits) for k in ("x", "y", "w", "h")})
            if rest["h"] < min_h:   # before the reads: each kept track costs several decodes
                continue
            mid_f = [f for f, ln in t["hits"] if ln["text"] == final]
            mid_f = mid_f[len(mid_f) // 2]
            # the settled title against an empty frame either side (the sample before, else after)
            empty_f = next((f for f in (t["hits"][0][0] - STRIDE, t["hits"][-1][0] + STRIDE) if 0 <= f < nframes), None)
            full = 0.0
            if empty_f is not None:
                two = _read(video, [mid_f, empty_f], size, tmp, f"full{mid_f}", fps=fps)
                if mid_f in two and empty_f in two:
                    full = _diff(two[mid_f][1], two[empty_f][1], rest)
            fin, a_in = _edge(video, size, tmp, t, "in", nframes, rest, full, fps)
            fout, a_out = _edge(video, size, tmp, t, "out", nframes, rest, full, fps)
            # word by word: the count only rises, at least twice over, and the first read is a word prefix of the final
            if len(set(words)) > 1 and words == sorted(words) and final.lower().split()[:words[0]] == texts[0].lower().split():
                a_in = "word"
            out.append({"text": final, "in_s": round(fin / fps, 4), "out_s": round((fout + 1) / fps, 4),
                        "box": rest, "animation": a_in, "animation_out": a_out})
    return sorted(out, key=lambda x: (x["in_s"], x["box"]["y"]))


def score(ref, rb, fps):
    """Title timing error. Each reference title is paired with the unused rebuild title of the
    same (or related) text nearest in time; error is |in| and |out| difference in frames. The
    part is the sum over pairs of max(0, 1 - mean error / TITLE_SCALE), over the larger count."""
    used, pairs = set(), []
    for a in ref:
        cand = [(abs(b["in_s"] - a["in_s"]), j) for j, b in enumerate(rb)
                if j not in used and related(a["text"], b["text"])]
        if not cand:
            continue
        j = min(cand)[1]
        used.add(j)
        b = rb[j]
        pairs.append({"text": a["text"], "in_frames": round(abs(b["in_s"] - a["in_s"]) * fps, 2),
                      "out_frames": round(abs(b["out_s"] - a["out_s"]) * fps, 2),
                      "animation": [a["animation"], b["animation"]]})
    n = max(len(ref), len(rb))
    errs = [e for p in pairs for e in (p["in_frames"], p["out_frames"])]
    part = (sum(max(0.0, 1 - (p["in_frames"] + p["out_frames"]) / 2 / TITLE_SCALE) for p in pairs) / n) if n else None
    return {"titles": [len(ref), len(rb)], "matched": len(pairs),
            "median_frames": round(statistics.median(errs), 2) if errs else None,
            "max_frames": round(max(errs), 2) if errs else None, "pairs": pairs, "part": part}
