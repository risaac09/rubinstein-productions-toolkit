"""rpstills.vision: faces with capture quality and eye state, and whole-body
person boxes, from macOS Vision through vision_stills.swift, compiled once
into ~/Library/Caches/rpstills (keyed by the source's sha256). The helper is
run over batches of upright images; a face is
    {"x", "y", "w", "h", "confidence", "quality", "eye_left", "eye_right",
     "roll", "yaw"}
and a person {"x", "y", "w", "h", "confidence"}, boxes normalized from the
top left. faces(images, landmarks=True) runs the helper with --landmarks and
each face also carries
    "landmarks": {"leftEye", "rightEye", "leftEyebrow", "rightEyebrow",
                  "outerLips", "innerLips", "faceContour", "nose"}
as lists of [x, y] in the same normalized, top-left, whole-image convention
(Vision's left/right naming; rpstills.skin pairs by image x). The index never
asks for them. faces() returns None when the helper cannot run, and callers
say so."""

import hashlib
import json
import os
import shutil
import subprocess

SWIFTC = shutil.which("swiftc") or "/usr/bin/swiftc"
SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vision_stills.swift")
CACHE_DIR = os.path.expanduser("~/Library/Caches/rpstills")
BATCH = 64
TIMEOUT_S = 900


def binary(source=SOURCE):
    try:
        with open(source, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError:
        return None
    path = os.path.join(CACHE_DIR, f"{os.path.splitext(os.path.basename(source))[0]}-{digest}")
    if os.access(path, os.X_OK):
        return path
    if not os.access(SWIFTC, os.X_OK):
        return None
    os.makedirs(CACHE_DIR, exist_ok=True)
    proc = subprocess.run([SWIFTC, "-O", source, "-o", path], stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return path if proc.returncode == 0 and os.access(path, os.X_OK) else None


def _batch(exe, flag, chunk):
    """{path: doc} for one helper run, or None when it timed out or exited non-zero."""
    try:
        proc = subprocess.run([exe, *flag, *chunk], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, encoding="utf-8", errors="replace",
                              timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    out = {}
    for line in proc.stdout.splitlines():
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        out[doc.get("path")] = {"width": doc.get("width"), "height": doc.get("height"),
                                "faces": doc.get("faces") or [],
                                "people": doc.get("people") or [], "error": doc.get("error")}
    return out


def faces(images, landmarks=False):
    """{image path: {"width", "height", "faces": [...], "people": [...]}} or
    None when the helper cannot run. landmarks=True adds each face's landmark
    points. A batch that fails is retried one file at a time; a file that still
    fails is absent from the result, so callers treat a missing path as unread."""
    exe = binary()
    if not exe:
        return None
    flag = ["--landmarks"] if landmarks else []
    out = {}
    for start in range(0, len(images), BATCH):
        chunk = list(images[start:start + BATCH])
        got = _batch(exe, flag, chunk)
        if got is not None:
            out.update(got)
            continue
        misses = 0
        for one in chunk:
            if misses >= 3:   # a wedged helper: leave the rest of this batch unread rather than wait on each file
                break
            got = _batch(exe, flag, [one])
            misses = 0 if got is not None else misses + 1
            out.update(got or {})
    return out


MATTE_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "person_matte.swift")


def person_mattes(jobs):
    """[bool] per job (image path, matte PNG path, x, y): the foreground instance Vision finds at (x, y),
    normalized from the top left, written to the matte path as a grey PNG (255 = the person), or every instance
    when the point is on none. False for a job Vision could not segment; None when the helper cannot run."""
    exe = binary(MATTE_SOURCE)
    if not exe or not jobs:
        return None if not exe else []
    try:
        proc = subprocess.run([exe, *(str(v) for j in jobs for v in j)], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, encoding="utf-8",
                              errors="replace", timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None
    done = {}
    for line in proc.stdout.splitlines():
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        done[doc.get("path")] = bool(doc.get("ok"))
    return [done.get(j[0], False) for j in jobs] if proc.returncode == 0 else None
