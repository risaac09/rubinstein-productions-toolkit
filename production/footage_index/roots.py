"""Footage roots: (name, mount) pairs from the "roots" setting (see config.py): a JSON list in the config file, or in
FOOTAGE_ROOTS as a JSON list or the path of a JSON file.

  FOOTAGE_ROOTS='[["Library", "/mnt/library"], ["Shoots", "/Volumes/Shoots"]]'
  entries may also be {"name", "mount", "volume", "catalog", "include", "skip_dirs"}

With no "roots" setting there is one root, the folder given by --root or FOOTAGE_ROOT, walked live.
include limits a root to those subtrees (["Current/"]); skip_dirs is a case-insensitive regex that drops any folder whose name
matches (render, cache, proxy, prep and the like).
mount is where this machine reads; volume is the path Resolve and the plans name (default: the mount for the single
root, /Volumes/<name> for a named one); catalog is a tab-separated file of path, bytes, mtime, and a root without one is walked live.
The first root is the default: its relpaths stay bare ("Shoots/a/clip.mov"), every other root's are
"<name>:<path>", so rows indexed before a second root was added keep their paths."""
import functools
import json
import os
import re
from collections import namedtuple

from . import config

Root = namedtuple("Root", "name mount volume catalog include skip_dirs", defaults=((), None))

NO_ROOT = "no footage folder: pass --root DIR (before the command), set FOOTAGE_ROOT, or name roots in the config file (RP_PIPELINE_CONFIG)"


def _default(mount):
    """The single root. With no folder given it has an empty mount: commands that only read the database still work, and
    the ones that read footage say what is missing (NO_ROOT)."""
    mount = os.path.abspath(os.path.expanduser(mount)) if mount else ""
    return [Root("Footage", mount, mount, None)]


def load():
    cfg = config.get("roots")
    return _load(json.dumps(cfg) if isinstance(cfg, list) else (cfg or "").strip(), os.environ.get("FOOTAGE_ROOT", ""))


@functools.lru_cache(maxsize=8)
def _load(raw, single=""):
    if not raw or raw == "[]":
        return _default(single)
    if not raw.startswith(("[", "{")):
        with open(os.path.expanduser(raw)) as f:
            raw = f.read()
    out = []
    for e in json.loads(raw):
        d = e if isinstance(e, dict) else {"name": e[0], "mount": e[1]}
        name = d["name"]
        if ":" in name or "/" in name or any(r.name == name for r in out):
            raise ValueError(f"bad or repeated root name {name!r}")
        out.append(Root(name, os.path.expanduser(d["mount"]), d.get("volume") or f"/Volumes/{name}",
                        os.path.expanduser(d["catalog"]) if d.get("catalog") else None,
                        tuple(d.get("include") or ()), d.get("skip_dirs")))
    if not out:
        raise ValueError("the roots setting names no root")
    return out


def wanted(root, rel):
    """True when a path inside root passes its include list and does not sit under a skipped folder."""
    if root.include and not rel.startswith(root.include):
        return False
    dirs = rel.split("/")[:-1]
    return "#recycle" not in dirs and not (root.skip_dirs and any(re.search(root.skip_dirs, d, re.I) for d in dirs))


def qualify(root, rel, roots=None):
    """The relpath stored in the index: bare for the default (first) root."""
    return rel if root is (roots or load())[0] else f"{root.name}:{rel}"


def split(relpath, roots=None):
    """(root, path inside it) for a stored relpath."""
    roots = roots or load()
    name, sep, rest = relpath.partition(":")
    if sep:
        for r in roots[1:]:  # only a configured non-default root's name counts: "Foo:bar/x.mov" may be a bare path of the first root
            if r.name == name:
                return r, rest
    return roots[0], relpath


def volume_path(relpath):
    r, rel = split(relpath)
    return os.path.join(r.volume, rel)


def local_paths(relpath, roots=None):
    """Where this machine can read a relpath: the root's mount, then its volume path."""
    r, rel = split(relpath, roots)
    return [os.path.join(m, rel) for m in (r.mount, r.volume) if m]
