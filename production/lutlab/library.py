"""lutlab.library: what LUTs are installed, what each expects, which are copies.

Walks Resolve's LUT folders (rpresolve.api.DEFAULT_LUT_ROOTS) and records, for
every .cube and Resolve .dat: path, lattice size, title, sha256, and a guess
at the input encoding from the file and folder names. Byte-identical files are
grouped (the "Ava 614.CUBE 2" copies); nothing is ever moved or deleted.

The registry is derived and regenerable. Hand fixes go in
library-overrides.json beside it, keyed by sha256 so they survive a rename:
  {"<sha256>": {"space": "vlog", "note": "creative, expects V-Log -1 EV"}}"""

import hashlib
import json
import os
import re
from pathlib import Path

from rpresolve.api import DEFAULT_LUT_ROOTS

from . import spaces

EXTS = (".cube", ".dat")
# (pattern on the lowercased relative path, guess); first match wins. A guess
# that is not a lutlab space is still recorded, with space null.
GUESSES = [
    (r"v-?log|vlog|v-?gamut", "vlog"),
    (r"gen ?5|film ?gen ?5|bmd ?film ?5", "bmdfilm5"),
    (r"logc ?4|log ?c4", "logc4"),
    (r"log ?c|logc ?3|alexa", "logc3"),
    (r"davinci intermediate|dwg|\bdi\b|\(dwg-di\)", "di"),
    (r"s-?log ?3", "slog3"),
    (r"s-?log ?2", "slog2"),
    (r"d-?log|dji", "dlog"),
    (r"apple ?log|iphone", "applelog"),
    (r"canon ?log|c-?log", "clog"),
    (r"blackmagic|bmd", "bmdfilm-older"),
    (r"rec\.? ?709|\b709\b|\[post\]|rec709\)", "r709"),
]


def guess_space(rel):
    """Guess from the part of the file name before " to " (a conversion LUT,
    "GH5 Cine-D to VLog", expects what comes first), then from the folders,
    nearest first."""
    folder, fn = os.path.split(rel)
    src = re.split(r"\s+to\s+|To(?=[A-Z])", fn, maxsplit=1)[0]
    for s in [src, *reversed(folder.split(os.sep))]:
        for pat, g in GUESSES:
            if s and re.search(pat, s.lower()):
                return g
    return "unknown"


def header(path):
    size, title = None, ""
    with open(path, errors="ignore") as f:
        for i, line in enumerate(f):
            t = line.split()
            if t and t[0].upper() in ("LUT_3D_SIZE", "3DLUTSIZE"):
                size = int(t[1])
            elif t and t[0].upper() == "TITLE":
                title = line.strip()[5:].strip().strip('"')
            elif t and t[0].upper() == "LUT_1D_SIZE":
                return None, title
            if size and i > 40:
                break
    return size, title


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan(roots=DEFAULT_LUT_ROOTS, overrides=None):
    overrides = overrides or {}
    seen, entries = set(), []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for d, dirs, files in os.walk(root):
            dirs[:] = sorted(x for x in dirs if not x.startswith("."))
            for fn in sorted(files):
                path = os.path.join(d, fn)
                low = fn.lower()
                # Finder copies end "NAME.CUBE 2": still a LUT
                if not (low.endswith(EXTS) or re.search(r"\.(cube|dat) \d+$", low)):
                    continue
                real = os.path.realpath(path)
                if real in seen:
                    continue
                seen.add(real)
                rel = os.path.relpath(path, root)
                try:
                    size, title = header(path)
                except OSError:
                    continue
                if size is None:
                    continue  # 1D, shaper or not a 3D LUT
                digest = sha256(path)
                g = guess_space(rel)
                e = {"name": fn, "rel": rel, "root": root, "path": path, "lattice": size, "title": title,
                     "sha256": digest, "guess": g, "space": g if g in spaces.SPACES else None, "note": ""}
                e.update({k: v for k, v in overrides.get(digest, {}).items() if k in ("space", "note")})
                entries.append(e)
    groups = _by_hash(entries)
    for e in entries:
        e["copies"] = [p for p in groups[e["sha256"]] if p != e["path"]]
    return entries


def _by_hash(entries):
    out = {}
    for e in entries:
        out.setdefault(e["sha256"], []).append(e["path"])
    return out


def duplicate_groups(entries):
    return [sorted(v) for v in _by_hash(entries).values() if len(v) > 1]


def write(entries, outdir):
    p = Path(outdir) / "registry.json"
    tmp = p.with_suffix(".json.part")
    tmp.write_text(json.dumps({"lutlab_library": 1, "roots": list(DEFAULT_LUT_ROOTS), "entries": entries},
                              indent=1) + "\n")
    os.replace(tmp, p)
    return p


def load_overrides(outdir):
    p = Path(outdir) / "library-overrides.json"
    return json.loads(p.read_text()) if p.exists() else {}
