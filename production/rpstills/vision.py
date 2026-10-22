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


def binary():
    try:
        with open(SOURCE, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError:
        return None
    path = os.path.join(CACHE_DIR, f"vision_stills-{digest}")
    if os.access(path, os.X_OK):
        return path
    if not os.access(SWIFTC, os.X_OK):
        return None
    os.makedirs(CACHE_DIR, exist_ok=True)
    proc = subprocess.run([SWIFTC, "-O", SOURCE, "-o", path], stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return path if proc.returncode == 0 and os.access(path, os.X_OK) else None


def faces(images, landmarks=False):
    """{image path: {"width", "height", "faces": [...], "people": [...]}} or
    None. landmarks=True adds each face's landmark points."""
    exe = binary()
    if not exe:
        return None
    flag = ["--landmarks"] if landmarks else []
    out = {}
    for start in range(0, len(images), BATCH):
        chunk = list(images[start:start + BATCH])
        try:
            proc = subprocess.run([exe, *flag, *chunk], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, encoding="utf-8", errors="replace",
                                  timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return None
        if proc.returncode != 0:
            return None
        for line in proc.stdout.splitlines():
            try:
                doc = json.loads(line)
            except ValueError:
                continue
            out[doc.get("path")] = {"width": doc.get("width"), "height": doc.get("height"),
                                    "faces": doc.get("faces") or [],
                                    "people": doc.get("people") or [], "error": doc.get("error")}
    return out
