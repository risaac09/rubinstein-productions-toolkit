"""
rpresolve.mcp.tools_read: tools that read the open Resolve project and
change nothing, not even the UI (no page or timeline switches).
"""

import os
import shutil
import subprocess
import sys

from .. import api
from . import __version__
from .registry import READ, Tool

TOOLKIT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
STATUS_LOCK_WAIT = 5.0


def _git(*args):
    try:
        out = subprocess.run(["git", "-C", TOOLKIT_DIR, *args], stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             encoding="utf-8", timeout=10)
        return out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _server_info():
    commit = _git("rev-parse", "--short", "HEAD")
    status = _git("status", "--porcelain", "--untracked-files=no")
    return {"version": __version__, "toolkit_path": os.path.dirname(TOOLKIT_DIR),
            "commit": commit, "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(status) if status is not None else None,
            "python": sys.version.split()[0]}


def _tools_available():
    def has(path):
        return os.access(path, os.X_OK)
    try:
        import numpy  # noqa: F401
        numpy_ok = True
    except ImportError:
        numpy_ok = False
    return {"ffmpeg": has("/opt/homebrew/bin/ffmpeg"), "ffprobe": has("/opt/homebrew/bin/ffprobe"),
            "exiftool": has("/usr/local/bin/exiftool"), "python3.14": has("/opt/homebrew/bin/python3.14"),
            "swiftc": bool(shutil.which("swiftc")), "numpy": numpy_ok}


def resolve_status(args, ctx):
    """Server, Resolve and open-project state. Succeeds with Resolve closed."""
    out = {"summary": "", "server": _server_info(), "resolve": {"connected": False},
           "project": None, "tools": _tools_available()}
    try:
        with api.ResolveLock(timeout=STATUS_LOCK_WAIT):
            resolve = ctx.session.get()
            out["resolve"] = {"connected": True, "product": resolve.GetProductName(),
                              "version": resolve.GetVersionString(),
                              "page": resolve.GetCurrentPage()}
            try:
                pm, project = api.current_project(resolve)
            except api.ProjectChanged as e:
                out["project"] = {"open": False, "error": str(e)}
            else:
                tl = project.GetCurrentTimeline()
                out["project"] = {
                    "open": True, "name": project.GetName(), "unique_id": project.GetUniqueId(),
                    "color_science_mode": project.GetSetting("colorScienceMode"),
                    "timeline_count": project.GetTimelineCount(),
                    "current_timeline": tl.GetName() if tl else None,
                    "render_jobs": len(project.GetRenderJobList() or []),
                    "rendering": bool(project.IsRenderingInProgress())}
    except api.ResolveBusy as e:
        out["resolve"] = {"connected": None, "busy": True, "error": str(e)}
    except api.ResolveAPIError as e:
        out["resolve"] = {"connected": False, "error": str(e)}
    r, p = out["resolve"], out["project"] or {}
    if r.get("connected"):
        out["summary"] = (f"Resolve {r['version']} connected; " +
                          (f"project '{p['name']}' open ({p['timeline_count']} timelines)"
                           if p.get("open") else "no project open"))
    elif r.get("busy"):
        out["summary"] = "Resolve is busy with another session or the CLI"
    else:
        out["summary"] = "Resolve is not connected: " + r.get("error", "")
    if out["server"].get("dirty"):
        out["summary"] += "; the toolkit checkout has uncommitted changes"
    return out


def register(registry):
    registry.add(Tool(
        "resolve_status",
        "Report this server's toolkit commit, whether DaVinci Resolve is running and "
        "reachable, and the open project's name, unique id, colour science, timeline count, "
        "current timeline and render queue, plus which helper tools are installed. Works with "
        "Resolve closed. Call it first: every write tool must name the open project exactly.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        resolve_status, title="Resolve status", annotations=READ))
