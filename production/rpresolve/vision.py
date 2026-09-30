"""
rpresolve.vision: face rectangles from macOS Vision, through a small Swift
helper (vision_faces.swift) compiled once into ~/Library/Caches/rpresolve.
Stdlib only, so tools without numpy (reframe) can use it; measure.py
imports it from here.

Boxes are normalized with the origin at the top left:
    {"x": 0.41, "y": 0.18, "w": 0.21, "h": 0.30, "confidence": 0.87}
The helper runs on macOS with swiftc (Xcode or the command line tools);
anywhere else face_boxes() returns None and callers say so.
"""

import hashlib
import json
import os
import shutil
import subprocess

SWIFTC = shutil.which("swiftc") or "/usr/bin/swiftc"
VISION_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vision_faces.swift")
CACHE_DIR = os.path.expanduser("~/Library/Caches/rpresolve")
TIMEOUT_S = 600  # one call over every image of a batch


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
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return binary if proc.returncode == 0 and os.access(binary, os.X_OK) else None


def face_boxes(images):
    """{image path: [box]} from the Vision helper, or None when the helper
    cannot run. An image Vision could not read maps to []."""
    binary = vision_binary()
    if not binary:
        return None
    try:
        proc = subprocess.run([binary, *images], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
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
        out[doc.get("path")] = doc.get("faces") or []
    return out
