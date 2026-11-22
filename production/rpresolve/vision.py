"""
rpresolve.vision: face rectangles from macOS Vision, through a small Swift
helper (vision_faces.swift) compiled once into ~/Library/Caches/rpresolve.
Stdlib only, so tools without numpy (reframe) can use it; measure.py
imports it from here.

Boxes are normalized with the origin at the top left:
    {"x": 0.41, "y": 0.18, "w": 0.21, "h": 0.30, "confidence": 0.87}
With landmarks=True each box also carries
    "landmarks": {"leftEye": [[x, y], ...], "rightEye", "outerLips"}
in the same normalized convention (Vision's left and right naming).
The helper runs on macOS with swiftc (Xcode or the command line tools);
anywhere else face_boxes() returns None and callers say so.

text_boxes() does the same for lines of text (vision_text.swift, Vision's text
request): {image path: [{"text", "x", "y", "w", "h", "confidence"}]}.
"""

import json
import os
import shutil
import subprocess

from . import swiftbuild

SWIFTC = shutil.which("swiftc") or "/usr/bin/swiftc"
VISION_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vision_faces.swift")
TEXT_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vision_text.swift")
CACHE_DIR = os.path.expanduser("~/Library/Caches/rpresolve")
TIMEOUT_S = 600  # one call over every image of a batch


def vision_binary(source=VISION_SOURCE):
    """Path of the compiled Vision helper, building it into the cache on
    first use (keyed by the source's sha256). None when swiftc is missing
    or the build fails."""
    return swiftbuild.build(source, CACHE_DIR, SWIFTC)


def face_boxes(images, landmarks=False):
    """{image path: [box]} from the Vision helper, or None when the helper
    cannot run. An image Vision could not read maps to []."""
    return _run(VISION_SOURCE, ["--landmarks"] if landmarks else [], images, "faces")


def text_boxes(images):
    """{image path: [line]} from the Vision text helper, or None when it cannot run.
    An image Vision could not read maps to []."""
    return _run(TEXT_SOURCE, [], images, "texts")


def _run(source, flags, images, key):
    binary = vision_binary(source)
    if not binary:
        return None
    try:
        proc = subprocess.run([binary, *flags, *images], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
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
        out[doc.get("path")] = doc.get(key) or []
    return out
