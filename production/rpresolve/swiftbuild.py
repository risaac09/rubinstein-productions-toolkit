"""rpresolve.swiftbuild: the one place a Swift helper is compiled and cached.
Stdlib only; rpstills imports it the way it imports rpresolve.paths.

build(source, cache_dir) -> path of cache_dir/<stem>-<sha256[:12]>, or None
when swiftc is missing, the build fails or times out. The compile goes to a
temp file in cache_dir and os.replace moves it into place, so the final name
only ever holds a finished binary, even across an interrupt or two racing
processes."""

import hashlib
import os
import shutil
import subprocess

SWIFTC = shutil.which("swiftc") or "/usr/bin/swiftc"
BUILD_TIMEOUT_S = 900


def build(source, cache_dir, swiftc=None):
    swiftc = swiftc or SWIFTC
    try:
        with open(source, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError:
        return None
    path = os.path.join(cache_dir, f"{os.path.splitext(os.path.basename(source))[0]}-{digest}")
    if os.access(path, os.X_OK):
        return path
    if not os.access(swiftc, os.X_OK):
        return None
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        os.makedirs(cache_dir, exist_ok=True)
        proc = subprocess.run([swiftc, "-O", source, "-o", tmp], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=BUILD_TIMEOUT_S)
        if proc.returncode != 0 or not os.access(tmp, os.X_OK):
            return None
        os.replace(tmp, path)
    except (subprocess.TimeoutExpired, OSError):
        return None
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return path if os.access(path, os.X_OK) else None
