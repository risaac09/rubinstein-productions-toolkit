"""Per-clip pass: metadata from rpresolve.detect, keyframe thumbnails (log footage shown
through a colour-space transform to Rec.709), a motion cue and face counts. Read-only on the footage."""
import math
import os
import subprocess
import tempfile
import threading

import numpy as np
from PIL import Image

from . import config
from rpresolve import detect, vision

TOOLKIT = config.PRODUCTION
THUMBS = config.get("thumbs")
CST_DIR = os.path.join(config.get("cache_dir"), "cst")  # lutlab refuses to write LUTs inside a git tree
LUTLAB_PY = config.get("tools_python")
BRAW2PPM = os.path.join(TOOLKIT, "lutlab/braw/braw2ppm")
STEP_S, MIN_N, MAX_N = 4.0, 3, 40
_CST_LOCK = threading.Lock()
# detect profile -> lutlab cst space; anything else is shown as decoded
LOG_SPACES = {"V-Log": "vlog", "Film Gen 5": "bmdfilm5", "LogC3": "logc3", "LogC4": "logc4"}  # substring of detect's profile


def sample_times(duration):
    """(t, t_in, t_out) keyframes every ~4 s, at least 3 and at most 40 per clip."""
    if not duration or duration <= 0:
        return [(0.0, 0.0, 0.0)]
    n = max(MIN_N, min(MAX_N, math.ceil(duration / STEP_S)))
    seg = duration / n
    return [(round(seg * (i + 0.5), 3), round(seg * i, 3), round(seg * (i + 1), 3)) for i in range(n)]


def cst_chain(space):
    """lut3d filter string, log -> DaVinci Intermediate -> Rec.709, built once with lutlab cst."""
    if not space:
        return ""
    os.makedirs(CST_DIR, exist_ok=True)
    cubes = []
    for a, b in ((space, "di"), ("di", "r709")):
        path = os.path.join(CST_DIR, f"{a}-to-{b}-33.cube")
        with _CST_LOCK:  # six lanes ask at once; lutlab refuses to overwrite
            if not os.path.exists(path):
                subprocess.run([LUTLAB_PY, "-m", "lutlab", "cst", "--from", a, "--to", b, "--size", "33", "--out", CST_DIR],
                               cwd=TOOLKIT, check=True, capture_output=True)
        cubes.append(f"lut3d=file='{path}'")
    return ",".join(cubes) + ","


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=codec_name,width,height,avg_frame_rate:format=duration", "-of", "default=nw=1",
                          "file:" + path], capture_output=True, text=True, timeout=120).stdout
    kv = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    num, _, den = kv.get("avg_frame_rate", "0/1").partition("/")
    fps = float(num) / float(den) if den and float(den) else None
    dur = float(kv["duration"]) if kv.get("duration", "N/A") != "N/A" else None
    return {"codec": kv.get("codec_name"), "width": int(kv.get("width") or 0) or None,
            "height": int(kv.get("height") or 0) or None, "fps": fps, "duration": dur}


def desqueeze_factor(path):
    """Anamorphic factor above 1 for a clip, else None. The one place the answer comes from (the toolkit's squeeze record,
    then the file's own tag); swap this body when the source moves."""
    try:
        try:
            f = _card_answer(path)
        except Exception:  # not in a card, or a record the toolkit rejects: the plain squeeze answer
            f = None
        if f is None:
            from rpresolve import squeeze
            f, _where = squeeze.answer(path)
    except Exception:  # no toolkit (CI), no ffprobe, an unreadable file: shown as decoded
        return None
    return f if f and f > 1 else None


_CARDS = {}


def _card_answer(path):
    """The card record's squeeze for this file (answers.squeeze, else a BRAW file's own tag), or None when no record covers it."""
    from rpresolve import card
    root = card.find(path)  # CardError when the file is in no card
    if root not in _CARDS:  # one load per card: it reads SHA256SUMS
        _CARDS[root] = card.load(root)
    c = _CARDS[root]
    rel = os.path.relpath(path, root)
    clip = next((x for x in c["clips"] if x["relpath"] == rel), None)
    return card.squeeze_answer(c, clip)[0] if clip else None


def _ffmpeg_frame(hw, path, t, out, vf, timeout):
    """(ok, reason). VideoToolbox fails on some 4K long-GOP h264 and stalls on large HEVC and ProRes RAW."""
    cmd = ["ffmpeg", "-v", "error", "-y", *(["-hwaccel", "videotoolbox"] if hw else []), "-ss", f"{t:.3f}", "-i", "file:" + path,
           "-frames:v", "1", "-vf", vf, "-q:v", "3", out]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"ffmpeg timed out after {timeout}s ({'videotoolbox' if hw else 'software'})"
    if r.returncode == 0 and os.path.exists(out):
        return True, ""
    lines = [l for l in r.stderr.decode(errors="replace").splitlines() if l.strip()]
    return False, (lines[-1] if lines else f"ffmpeg exit {r.returncode}")[:200]


def grab(path, t, out, vf, braw_fps=None, why=None, state=None):
    """One frame at t written to out (JPEG). BRAW goes through braw2ppm (ffmpeg cannot decode it). Anything else tries
    VideoToolbox for 60 s, then software; why (a list) collects the reason when both fail. state (a dict) remembers
    that hardware failed on this clip, so its later keyframes go straight to software."""
    if path.lower().endswith(".braw"):
        with tempfile.TemporaryDirectory() as tmp:
            ppm = os.path.join(tmp, "f.ppm")
            subprocess.run([BRAW2PPM, path, str(int(t * (braw_fps or 24))), ppm], capture_output=True, timeout=120)
            if not os.path.exists(ppm):
                if why is not None:
                    why.append("braw2ppm wrote no frame")
                return False
            r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", ppm, "-frames:v", "1", "-vf", vf, "-q:v", "3", out],
                               capture_output=True, timeout=120)
        return r.returncode == 0 and os.path.exists(out)
    ok, reason = _ffmpeg_frame(True, path, t, out, vf, 60) if not (state and state.get("sw")) else (False, "")
    if not ok:
        ok, reason = _ffmpeg_frame(False, path, t, out, vf, 300)
        if ok and state is not None:
            state["sw"] = True
    if not ok and why is not None:
        why.append(reason)
    return ok


def small_grey(img_path):
    return np.asarray(Image.open(img_path).convert("L").resize((160, 90)), np.float32) / 255.0


def process(root, relpath, cid):
    """Everything for one clip, no db access: returns (meta, shots) or raises."""
    path = os.path.join(root, relpath)
    rows, _missing = detect.detect_paths([path])
    d = rows[0] if rows else {}
    meta = probe(path) if not path.lower().endswith(".braw") else {"codec": "braw", "width": None, "height": None,
                                                                   "fps": None, "duration": None}
    profile = d.get("profile") or ""
    space = next((v for k, v in LOG_SPACES.items() if k in profile), None) or \
        ("bmdfilm5" if path.lower().endswith(".braw") else None)
    f = desqueeze_factor(path)  # stretch width by the factor first, so thumbnails and embeddings see the picture as shot
    vf = cst_chain(space) + (f"scale=trunc(iw*{f}/2)*2:ih," if f else "") + "scale=384:-2"
    if meta["duration"] is None and path.lower().endswith(".braw"):
        info = subprocess.run([BRAW2PPM, path, "0", "1", "/dev/null"], capture_output=True, text=True).stderr
        for line in info.splitlines():  # "rate <fps> ... frames <n> ..."
            parts = line.split()
            if parts[:1] == ["rate"] and "frames" in parts:
                meta["fps"] = float(parts[1])
                meta["duration"] = int(parts[parts.index("frames") + 1]) / meta["fps"]
    folder = os.path.join(THUMBS, str(cid))
    os.makedirs(folder, exist_ok=True)
    shots, why, state = [], [], {}
    for t, t_in, t_out in sample_times(meta["duration"]):
        thumb = os.path.join(folder, f"{t:09.3f}.jpg")
        if not grab(path, t, thumb, vf, meta["fps"], why, state):
            if not shots and len(why) >= 3:  # three keyframes in a row fail both routes: not worth the other 37
                break
            continue
        motion = None
        nxt = os.path.join(folder, f".{t:09.3f}-next.jpg")
        if meta["duration"] and t + 0.5 < meta["duration"] and grab(path, t + 0.5, nxt, vf, meta["fps"], None, state):
            motion = float(np.abs(small_grey(thumb) - small_grey(nxt)).mean())
            os.remove(nxt)
        luma = float(np.asarray(Image.open(thumb).convert("L"), np.float32).mean() / 255.0)
        shots.append({"t": t, "t_in": t_in, "t_out": t_out, "thumb": thumb, "motion": motion, "luma": luma})
    boxes = vision.face_boxes([s["thumb"] for s in shots]) if shots else {}
    for s in shots:
        b = (boxes or {}).get(s["thumb"])
        s["faces"] = None if b is None else len(b)
        s["face_max"] = max((x["w"] * x["h"] for x in b), default=0.0) if b else (None if b is None else 0.0)
    clip_meta = {"camera": d.get("camera"), "profile": profile or None, "color_space": d.get("input_color_space"),
                 **meta}
    if not shots:  # both decode routes failed at every keyframe: the caller names it undecodable, with the last reason
        clip_meta["undecodable"] = why[-1] if why else "no frames"
    return clip_meta, shots
