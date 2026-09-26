"""
rpresolve.ingest: import classified media into camera bins and tag each clip
with the input color space and data level that rpresolve.detect found.

For projects created for the RCM pipeline (and the sandbox) only. The rules
that keep it safe on a live project:
    - Nothing that was in the media pool before the run is touched. Its
      File Path is on the "existing" list and the plan skips it.
    - Only clips this run imported get SetClipProperty, and every write is
      read back (api.set_clip_property_checked).
    - A row detect could not pin ('review') is imported untagged into a
      Review bin; derived renders, proxies and corrupt files are skipped.
    - Camera RAW (BRAW) is imported untagged: RCM decodes it without a
      per-clip input tag.

plan_ingest() is pure and unit-tested. apply_plan() drives the media pool;
the caller pins the project and snapshots the current folder around it.
"""

import os
import unicodedata

from . import api
from . import detect

REVIEW_BIN = "Review"
SKIP_PROFILES = {"derived": "derived file (render or export)",
                 "proxy": "camera proxy: link, do not import",
                 detect.CORRUPT: "corrupt or truncated file"}
DATA_LEVELS = {"full": "Full", "video": "Video"}

# Actions a plan entry can carry.
TAG, IMPORT_ONLY, SKIP = "tag", "import-only", "skip"


def norm_path(path):
    """Resolved path in Unicode NFC, so a name read back over SMB in NFD
    still matches the one detect saw."""
    return unicodedata.normalize("NFC", os.path.realpath(path))


def camera_bin(camera):
    """A media-pool bin name from detect's camera column. Slashes would read
    as nesting, so they become ' - '."""
    name = " ".join((camera or "").replace("/", " - ").split())
    return name or "Unknown"


def plan_ingest(rows, existing_paths, parent="Source"):
    """One plan entry per detect row, in row order:
        {path, camera, profile, action, bin: [parent, child],
         input_color_space, data_level, reason}
    action is TAG (import, then write the tags), IMPORT_ONLY (import, write
    nothing) or SKIP. existing_paths are File Paths already in the pool."""
    existing = {norm_path(p) for p in existing_paths}
    seen = set()
    plan = []
    for row in rows:
        path = row["path"]
        key = norm_path(path)
        entry = {"path": path, "camera": row.get("camera", ""), "profile": row.get("profile", ""),
                 "action": SKIP, "bin": None, "input_color_space": "", "data_level": "",
                 "reason": ""}
        profile = entry["profile"]
        cs = row.get("input_color_space", "")
        if key in seen:
            entry["reason"] = "listed twice in this run"
        elif key in existing:
            entry["reason"] = "already in the media pool before this run; left untouched"
        elif profile in SKIP_PROFILES:
            entry["reason"] = SKIP_PROFILES[profile]
        elif profile == detect.REVIEW or not cs:
            entry.update(action=IMPORT_ONLY, bin=[parent, REVIEW_BIN],
                         reason="detect could not pin a profile: " + (row.get("note") or "no note"))
        elif cs == detect.CS_CAMERA_RAW:
            entry.update(action=IMPORT_ONLY, bin=[parent, camera_bin(entry["camera"])],
                         reason="camera RAW: RCM decodes it without an input tag")
        else:
            entry.update(action=TAG, bin=[parent, camera_bin(entry["camera"])],
                         input_color_space=cs,
                         data_level=DATA_LEVELS.get(row.get("data_level", ""), ""))
            if row.get("vfr") == "yes":
                entry["reason"] = "VFR: conform to CFR before editing"
        seen.add(key)
        plan.append(entry)
    return plan


def pool_file_paths(root):
    """Every clip File Path in the media pool, walking all bins."""
    paths = []
    stack = [root]
    while stack:
        folder = stack.pop()
        for clip in folder.GetClipList() or []:
            path = clip.GetClipProperty("File Path")
            if path:
                paths.append(path)
        stack.extend(folder.GetSubFolderList() or [])
    return paths


def ensure_folder(media_pool, root, parts):
    """The bin at root/parts[0]/parts[1]/..., created where missing. The
    first same-named sibling wins, as in resolve_workflow.find_folder."""
    current = root
    for part in parts:
        match = next((f for f in current.GetSubFolderList() or [] if f.GetName() == part), None)
        if match is None:
            match = media_pool.AddSubFolder(current, part)
            if not match:
                raise api.ResolveAPIError(f"could not create bin '{'/'.join(parts)}'")
        current = match
    return current


def _import_and_tag(media_pool, entries, results):
    """Import one bin's entries into the current folder, then tag the TAG
    entries with read-back. Fills results[id(entry)] as each one finishes."""
    items = media_pool.ImportMedia([e["path"] for e in entries]) or []
    by_path = {}
    for item in items:
        path = item.GetClipProperty("File Path") if item else ""
        if path:
            by_path[norm_path(path)] = item
    for e in entries:
        r = results[id(e)]
        item = by_path.get(norm_path(e["path"]))
        if item is None:
            r["result"] = "FAILED: not imported (Resolve returned no clip for this path)"
            continue
        if e["action"] == IMPORT_ONLY:
            r["result"] = "imported"
            continue
        try:
            r["input_color_space"] = api.set_clip_property_checked(
                item, "Input Color Space", e["input_color_space"])
            if e["data_level"]:
                r["data_level"] = api.set_clip_property_checked(item, "Data Level", e["data_level"])
            r["result"] = "tagged"
        except api.WriteNotApplied as err:
            r["result"] = f"FAILED: {err}"


def apply_plan(media_pool, root, plan, check=None):
    """Import and tag per plan. Returns one result per plan entry:
    {path, bin, action, result, input_color_space, data_level}, where result
    is 'skipped', 'imported', 'tagged', or 'FAILED: <why>'. check, when
    given, is called before each bin's writes (ProjectPin.check).
    Never raises for a Resolve failure: the first error (a bin that cannot
    be made, the open project changing) stops the run, and every entry not
    yet done is marked FAILED with the reason, so the report still shows
    exactly what was written."""
    results = {id(e): {"path": e["path"], "bin": "/".join(e["bin"] or []), "action": e["action"],
                       "result": "skipped" if e["action"] == SKIP else "",
                       "input_color_space": "", "data_level": ""} for e in plan}
    groups = {}
    for e in plan:
        if e["action"] != SKIP:
            groups.setdefault(tuple(e["bin"]), []).append(e)
    stopped = None
    for parts, entries in groups.items():
        if stopped:
            for e in entries:
                results[id(e)]["result"] = f"FAILED: not run, stopped earlier ({stopped})"
            continue
        try:
            if check:
                check()
            folder = ensure_folder(media_pool, root, list(parts))
            if not media_pool.SetCurrentFolder(folder):
                for e in entries:
                    results[id(e)]["result"] = f"FAILED: could not open bin '{'/'.join(parts)}'"
                continue
            _import_and_tag(media_pool, entries, results)
        except Exception as err:  # a changed project, a refused bin, a dropped bridge
            stopped = f"{type(err).__name__}: {err}"
            for e in entries:
                if not results[id(e)]["result"]:
                    results[id(e)]["result"] = f"FAILED: not run ({stopped})"
    return [results[id(e)] for e in plan]


REPORT_COLUMNS = ("path", "bin", "action", "result", "input_color_space", "data_level", "reason")


def format_report(plan, results):
    """TSV of the run: plan reasons joined to the results."""
    def cell(v):
        return " ".join(str(v or "").split())
    lines = ["\t".join(REPORT_COLUMNS)]
    for e, r in zip(plan, results):
        row = dict(r, reason=e["reason"])
        lines.append("\t".join(cell(row.get(c, "")) for c in REPORT_COLUMNS))
    return "\n".join(lines) + "\n"
