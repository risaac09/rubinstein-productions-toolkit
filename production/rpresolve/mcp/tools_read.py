"""
rpresolve.mcp.tools_read: tools that read the open Resolve project and
change nothing, not even the UI (no page or timeline switches).
"""

import os
import shutil
import subprocess
import sys

from .. import api, grade, render
from . import __version__
from .registry import READ, Tool

TOOLKIT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
STATUS_LOCK_WAIT = 5.0
READ_LOCK_WAIT = 10.0
AUTO_SUFFIX = " [auto]"
TRACK_TYPES = ("video", "audio", "subtitle")
TRANSFORM = ("ZoomX", "ZoomY", "Pan", "Tilt", "RotationAngle", "CropLeft", "CropRight",
             "CropTop", "CropBottom")
CLIP_PROPS = ("File Path", "Type", "Input Color Space", "Data Level", "Resolution", "FPS",
              "Frames", "Duration", "Video Codec", "Start TC")


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


def _call(obj, method, *args):
    return api._safe_call(obj, method, *args)


def _open_project(args, ctx):
    """(resolve, project) for the open project, checked against the optional
    project and project_id arguments. Reads never switch projects."""
    resolve, _pm, project = ctx.session.project()
    name, uid = project.GetName(), project.GetUniqueId()
    if args.get("project") and args["project"] != name:
        raise api.ProjectChanged(f"the open project is '{name}', not '{args['project']}'.")
    if args.get("project_id") and args["project_id"] != uid:
        raise api.ProjectChanged(f"the open project's unique id is {uid}, not {args['project_id']}.")
    return resolve, project


def _project_info(project):
    return {"name": project.GetName(), "unique_id": project.GetUniqueId()}


def _page(rows, args):
    offset, limit = args["offset"], args["limit"]
    page = rows[offset:offset + limit]
    nxt = offset + len(page)
    return {"total": len(rows), "offset": offset, "returned": len(page),
            "next_offset": nxt if nxt < len(rows) else None, "rows": page}


def _timelines(project):
    count = int(_call(project, "GetTimelineCount") or 0)
    return [(i, project.GetTimelineByIndex(i)) for i in range(1, count + 1)]


def _find_timeline(project, ref):
    """A timeline by unique id, else by exact name (refused when two share it)."""
    found = [(i, tl) for i, tl in _timelines(project) if tl and _call(tl, "GetUniqueId") == ref]
    if not found:
        found = [(i, tl) for i, tl in _timelines(project) if tl and _call(tl, "GetName") == ref]
    if not found:
        raise ValueError(f"no timeline named or with unique id '{ref}' in "
                         f"'{project.GetName()}'; list_timelines shows them.")
    if len(found) > 1:
        ids = ", ".join(str(_call(tl, "GetUniqueId")) for _, tl in found)
        raise ValueError(f"{len(found)} timelines are named '{ref}'; name one by unique id "
                         f"({ids}).")
    return found[0]


# ---------------------------------------------------------------------------
# list_timelines
# ---------------------------------------------------------------------------

def list_timelines(args, ctx):
    with api.ResolveLock(timeout=READ_LOCK_WAIT):
        _resolve, project = _open_project(args, ctx)
        current = _call(project.GetCurrentTimeline(), "GetUniqueId")
        needle = (args.get("name_contains") or "").lower()
        rows = []
        for i, tl in _timelines(project):
            name = _call(tl, "GetName") or ""
            if needle and needle not in name.lower():
                continue
            start, end = _call(tl, "GetStartFrame"), _call(tl, "GetEndFrame")
            uid = _call(tl, "GetUniqueId")
            rows.append({"index": i, "name": name, "unique_id": uid,
                         "fps": _call(tl, "GetSetting", "timelineFrameRate"),
                         "start_frame": start, "end_frame": end,
                         "frames": end - start if isinstance(start, int) and isinstance(end, int)
                         else None,
                         "tracks": {t: _call(tl, "GetTrackCount", t) for t in TRACK_TYPES},
                         "current": uid == current, "auto": name.endswith(AUTO_SUFFIX)})
        result = {"project": _project_info(project)}
    result.update(_page(rows, args))
    result["summary"] = (f"{result['total']} timeline(s) in '{result['project']['name']}'" +
                         (f" matching '{args['name_contains']}'" if needle else ""))
    return result


# ---------------------------------------------------------------------------
# timeline_items
# ---------------------------------------------------------------------------

def _media(mpi):
    if not mpi:
        return None
    props = _call(mpi, "GetClipProperty") or {}
    out = {"name": _call(mpi, "GetName"), "unique_id": _call(mpi, "GetUniqueId")}
    out.update({k: props.get(k) for k in ("File Path", "Input Color Space", "Data Level")})
    return out


def _item_row(item, track_type, track, index, grades):
    row = {"track": track, "index": index, "name": _call(item, "GetName"),
           "start": _call(item, "GetStart"), "end": _call(item, "GetEnd"),
           "duration": _call(item, "GetDuration"),
           "left_offset": _call(item, "GetLeftOffset"),
           "source_start": _call(item, "GetSourceStartFrame"),
           "source_end": _call(item, "GetSourceEndFrame"),
           "enabled": _call(item, "GetClipEnabled"),
           "media": _media(_call(item, "GetMediaPoolItem"))}
    if track_type == "video":
        row["transform"] = {k: _call(item, "GetProperty", k) for k in TRANSFORM}
        if grades:
            g = grade.read_grade(item)
            row["grade"] = {k: v for k, v in g.items() if k != "fingerprint"} if g else None
    return row


def timeline_items(args, ctx):
    kind = args["track_type"]
    with api.ResolveLock(timeout=READ_LOCK_WAIT):
        _resolve, project = _open_project(args, ctx)
        index, tl = _find_timeline(project, args["timeline"])
        tracks = int(_call(tl, "GetTrackCount", kind) or 0)
        wanted = [args["track"]] if args.get("track") else list(range(1, tracks + 1))
        if args.get("track") and args["track"] > tracks:
            raise ValueError(f"'{tl.GetName()}' has {tracks} {kind} track(s).")
        rows = []
        for t in wanted:
            for n, item in enumerate(_call(tl, "GetItemListInTrack", kind, t) or [], 1):
                ctx.check_cancel()
                rows.append(_item_row(item, kind, t, n, args["grades"] and kind == "video"))
        result = {"project": _project_info(project),
                  "timeline": {"index": index, "name": tl.GetName(),
                               "unique_id": _call(tl, "GetUniqueId"),
                               "fps": _call(tl, "GetSetting", "timelineFrameRate"),
                               "start_frame": _call(tl, "GetStartFrame"),
                               "auto": tl.GetName().endswith(AUTO_SUFFIX)},
                  "track_type": kind, "tracks": tracks}
    result.update(_page(rows, args))
    result["summary"] = (f"{result['total']} {kind} item(s) on '{result['timeline']['name']}'"
                         f" across {len(wanted)} track(s)")
    result["note"] = ("start/end are timeline frames; source_start/source_end count in the "
                      "source clip's own frame rate.")
    return result


# ---------------------------------------------------------------------------
# media_pool
# ---------------------------------------------------------------------------

def _folder_at(root, path):
    parts = [p for p in (path or "").split("/") if p]
    if parts and parts[0] == root.GetName():
        parts = parts[1:]
    folder, walked = root, [root.GetName()]
    for part in parts:
        subs = [f for f in _call(folder, "GetSubFolderList") or [] if f.GetName() == part]
        if not subs:
            names = [f.GetName() for f in _call(folder, "GetSubFolderList") or []]
            raise ValueError(f"no folder '{part}' in '{'/'.join(walked)}'; it has "
                             f"{', '.join(names) or 'no subfolders'}.")
        folder = subs[0]
        walked.append(part)
    return folder, "/".join(walked)


def _tree(folder, depth):
    subs = _call(folder, "GetSubFolderList") or []
    node = {"name": folder.GetName(), "clips": len(_call(folder, "GetClipList") or []),
            "subfolders": len(subs)}
    if depth > 0 and subs:
        node["children"] = [_tree(f, depth - 1) for f in subs]
    return node


def _clip_row(clip):
    props = _call(clip, "GetClipProperty") or {}
    row = {"name": _call(clip, "GetName"), "unique_id": _call(clip, "GetUniqueId")}
    row.update({k: props.get(k) for k in CLIP_PROPS})
    return row


def media_pool(args, ctx):
    with api.ResolveLock(timeout=READ_LOCK_WAIT):
        _resolve, project = _open_project(args, ctx)
        root = project.GetMediaPool().GetRootFolder()
        folder, path = _folder_at(root, args.get("folder"))
        result = {"project": _project_info(project), "folder": path,
                  "tree": _tree(folder, args["depth"])}
        rows = []
        if args["clips"]:
            for clip in _call(folder, "GetClipList") or []:
                ctx.check_cancel()
                rows.append(_clip_row(clip))
    result.update(_page(rows, args))
    result["summary"] = (f"'{path}': {result['tree']['clips']} clip(s), "
                         f"{result['tree']['subfolders']} subfolder(s)")
    return result


# ---------------------------------------------------------------------------
# render_queue_status
# ---------------------------------------------------------------------------

def render_queue_status(args, ctx):
    with api.ResolveLock(timeout=READ_LOCK_WAIT):
        _resolve, project = _open_project(args, ctx)
        jobs = render.list_jobs(project)
        fmt = _call(project, "GetCurrentRenderFormatAndCodec") or {}
        result = {"project": _project_info(project),
                  "rendering": bool(_call(project, "IsRenderingInProgress")),
                  "deliver": {"format": fmt.get("format"), "codec": fmt.get("codec")}}
    result.update(_page(jobs, args))
    states = {}
    for j in jobs:
        states[j.get("JobStatus") or "unknown"] = states.get(j.get("JobStatus") or "unknown", 0) + 1
    result["summary"] = (f"{len(jobs)} render job(s)" +
                         (": " + ", ".join(f"{n} {k}" for k, n in sorted(states.items()))
                          if jobs else "") + ("; rendering now" if result["rendering"] else ""))
    return result


def register(registry):
    registry.add(Tool(
        "resolve_status",
        "Report this server's toolkit commit, whether DaVinci Resolve is running and "
        "reachable, and the open project's name, unique id, colour science, timeline count, "
        "current timeline and render queue, plus which helper tools are installed. Works with "
        "Resolve closed. Call it first: every write tool must name the open project exactly.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        resolve_status, title="Resolve status", annotations=READ))

    project_props = {
        "project": {"type": "string",
                    "description": "The open project's name; refused if another is open."},
        "project_id": {"type": "string", "description": "The open project's unique id."}}
    paging = lambda default: {  # noqa: E731
        "offset": {"type": "integer", "minimum": 0, "default": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": default}}

    def obj(props, required=()):
        return {"type": "object", "properties": {**project_props, **props},
                "required": list(required), "additionalProperties": False}

    registry.add(Tool(
        "list_timelines",
        "List the open project's timelines: index, name, unique id, frame rate, start and "
        "end frames, track counts, which is current, and which are ' [auto]' (made by these "
        "tools). Changes nothing, not even the current timeline.",
        obj({"name_contains": {"type": "string",
                               "description": "Only timelines whose name contains this."},
             **paging(100)}),
        list_timelines, title="List timelines", annotations=READ))
    registry.add(Tool(
        "timeline_items",
        "The items on one timeline's tracks: timeline and source frames, media file, input "
        "colour space and data level, and for video the transform (zoom, pan, crop) and the "
        "grade (version, colour group, nodes with labels, LUTs and tools). Reads a timeline "
        "without making it current.",
        obj({"timeline": {"type": "string", "description": "Timeline name or unique id."},
             "track_type": {"type": "string", "enum": list(TRACK_TYPES), "default": "video"},
             "track": {"type": "integer", "minimum": 1,
                       "description": "Only this track (default: all)."},
             "grades": {"type": "boolean", "default": True,
                        "description": "Read each video item's grade."},
             **paging(100)}, ["timeline"]),
        timeline_items, title="Timeline items", annotations=READ))
    registry.add(Tool(
        "media_pool",
        "The open project's media pool: the folder tree from a folder (with clip counts) and "
        "that folder's clips with file path, type, input colour space, data level, "
        "resolution, frame rate, duration and codec. Changes nothing, not even the "
        "selected bin.",
        obj({"folder": {"type": "string",
                        "description": "Folder path from the root, e.g. 'Source/Panasonic "
                        "DC-GH7' (default: the root)."},
             "depth": {"type": "integer", "minimum": 0, "maximum": 6, "default": 2,
                       "description": "Levels of subfolders to show."},
             "clips": {"type": "boolean", "default": True,
                       "description": "List the folder's own clips."},
             **paging(100)}),
        media_pool, title="Media pool", annotations=READ))
    registry.add(Tool(
        "render_queue_status",
        "The open project's render queue: every job's settings and status (queued, "
        "rendering, complete, failed, with percentage and error), whether a render is "
        "running, and the Deliver page's current format and codec. Changes nothing.",
        obj({**paging(50)}),
        render_queue_status, title="Render queue status", annotations=READ))
