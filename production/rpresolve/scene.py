"""
rpresolve.scene: per-frame saliency, Laplacian sharpness, frame-to-frame
motion and (model-gated) depth, through scene_cli.swift compiled once into
~/Library/Caches/rpresolve. Stdlib only. Every call returns None when the
helper cannot run (no macOS or swiftc), so callers say so.

Depth never downloads anything: it needs a compiled .mlmodelc path, and
without one it returns {"depth": None, "reason": "no model"}. Motion is a
mean absolute difference, not optical flow.

Off-by-default hook for perceive's packet: a caller may attach
scene.read(image) under a "scene" key; perceive does not call it today.
"""

import hashlib
import json
import os
import shutil
import subprocess

SWIFTC = shutil.which("swiftc") or "/usr/bin/swiftc"
SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scene_cli.swift")
CACHE_DIR = os.path.expanduser("~/Library/Caches/rpresolve")
TIMEOUT_S = 120
BUILD_TIMEOUT_S = 300


def scene_binary():
    """Compiled helper path, built into the cache on first use (keyed by the
    source sha256); None when swiftc is missing or the build fails."""
    try:
        with open(SOURCE, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError:
        return None
    binary = os.path.join(CACHE_DIR, f"scene_cli-{digest}")
    if os.access(binary, os.X_OK):
        return binary
    if not os.access(SWIFTC, os.X_OK):
        return None
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = f"{binary}.{os.getpid()}.tmp"  # atomic rename: a racing caller never runs a half-linked file
    try:
        proc = subprocess.run([SWIFTC, "-O", SOURCE, "-o", tmp], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=BUILD_TIMEOUT_S)
        if proc.returncode != 0:
            return None
        os.replace(tmp, binary)
    except (subprocess.TimeoutExpired, OSError):
        return None
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return binary if os.access(binary, os.X_OK) else None


def _run(*argv):
    binary = scene_binary()
    if not binary:
        return None
    try:
        proc = subprocess.run([binary, *map(str, argv)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, encoding="utf-8", errors="replace", timeout=TIMEOUT_S)
        return json.loads(proc.stdout) if proc.returncode == 0 else None
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None


def saliency(image):
    return _run("saliency", image)


def sharpness(image):
    return _run("sharpness", image)


def motion(image_a, image_b):
    return _run("motion", image_a, image_b)


def depth(image, model=None):
    return _run("depth", image, *(["--depth-model", model] if model else []))


def read(image, previous=None, depth_model=None):
    """One dict for a packet slot, or None when the helper cannot run. A read that
    failed on a bad image is None inside the dict."""
    if not scene_binary():
        return None
    out = {"saliency": saliency(image), "sharpness": sharpness(image),
           "depth": depth(image, depth_model) if depth_model else {"depth": None, "reason": "no model"}}
    if previous:
        out["motion"] = motion(previous, image)
    return out
