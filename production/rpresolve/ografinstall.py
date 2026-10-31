"""
rpresolve.ografinstall: are the OGraf templates Resolve loads the ones in this repo?

Compares the sha256 of each template's .ograf.json and .js under
tools/ograf-render/templates/<id>/ with the copy under Resolve's OGraf titles
folder (<root>/<id>/<file>). A check only: nothing is copied or written, and
the installed folder is read, never created. Stdlib only.

Resolve reads a template when it starts, so an installed file newer than the
running Resolve process needs a restart. The process start time comes from ps.
"""

import hashlib
import os
import subprocess
import time
from pathlib import Path

REPO_TEMPLATES = Path(__file__).resolve().parents[2] / "tools" / "ograf-render" / "templates"
INSTALLED_ROOT = os.path.expanduser(
    "~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Templates/Edit/Titles/OGraf")
SUFFIXES = (".ograf.json", ".js")
RESOLVE_BIN = "Resolve.app/Contents/MacOS/Resolve"  # the bundle is "DaVinci Resolve.app"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parse_start(ps_text):
    """Epoch start of the first Resolve line in `ps -axo lstart=,command=` output, or None."""
    for line in ps_text.splitlines():
        if RESOLVE_BIN in line:
            try:
                return time.mktime(time.strptime(" ".join(line.split()[:5]), "%a %b %d %H:%M:%S %Y"))
            except ValueError:
                continue  # an unreadable date reads as not running: no restart notice
    return None


def resolve_start_time():
    """Epoch seconds when the running Resolve started, or None when it is not running."""
    try:
        # ps prints lstart in the user's locale (a French one reads "sam. 31 oct."); force C.
        return parse_start(subprocess.run(["ps", "-axo", "lstart=,command="], capture_output=True,
                                          text=True, timeout=10,
                                          env={**os.environ, "LC_ALL": "C"}).stdout)
    except (OSError, subprocess.SubprocessError):
        return None


def check(template=None, repo_root=None, installed_root=None, started_at=None):
    """One row per template file: installed, missing or stale, with both hashes.
    `started_at` is a function so a test can fake the process start time."""
    repo_root, installed_root = Path(repo_root or REPO_TEMPLATES), Path(installed_root or INSTALLED_ROOT)
    started_at = started_at or resolve_start_time
    if not repo_root.is_dir():
        raise ValueError(f"no template folder at {repo_root}.")
    ids = [template] if template else sorted(p.name for p in repo_root.iterdir() if p.is_dir())
    rows, newest = [], 0.0
    for tid in ids:
        src = repo_root / tid
        if not src.is_dir():
            raise ValueError(f"no template '{tid}' in {repo_root}.")
        for f in sorted(p for p in src.iterdir() if p.name.endswith(SUFFIXES)):
            dest = installed_root / tid / f.name
            installed_sha = _sha(dest) if dest.is_file() else None
            repo_sha = _sha(f)
            if installed_sha and installed_sha == repo_sha:  # only a file Resolve would load
                newest = max(newest, dest.stat().st_mtime)
            rows.append({"template": tid, "file": f.name, "repo_sha256": repo_sha,
                         "installed_sha256": installed_sha,
                         "status": "missing" if installed_sha is None
                         else "installed" if installed_sha == repo_sha else "stale"})
    start = started_at() if newest else None
    restart = bool(start and newest > start)
    return {"rows": rows, "restart_needed": restart,
            "notice": "Restart Resolve: a template was installed after it started." if restart else None,
            "ok": bool(rows) and all(r["status"] == "installed" for r in rows)}
