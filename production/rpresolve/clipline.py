"""
rpresolve.clipline: an ' [auto]' timeline from media-pool clips, one after
another in a stated order. The Resolve half of the timeline_from_clips
tool; workflows.timeline_from_clips pins the project, plans and puts the UI
back. Stdlib only.

What it is for: a test ladder, a camera comparison, a selects reel; any
timeline that is "these clips, in this order". cut builds spans of one
source and sync stacks two recordings; nothing built this.

What Resolve does, and what this module does about it (spike notes in
syncbuild.py and cut.py apply here too):
    - MediaPool.AppendToTimeline appends to the CURRENT timeline and returns
      a truthy value even when it places nothing, so the new timeline is made
      current first and every item is read back from V1.
    - With no recordFrame a clip is appended at the end of what the timeline
      holds. Placing by a planned recordFrame would overlap (and trim) the
      previous clip whenever Resolve rounds a converted length a frame the
      other way, so the clips are appended in order and the read-back
      checks that they run one into the next: the first at the timeline's
      start, each next at the end of the one before, no gap, no overlap.
    - startFrame and endFrame count source frames at the clip's own rate and
      endFrame is exclusive. A clip whose rate differs from the timeline's
      (a 119.88 fps clip on a 29.97 fps timeline) is conformed by Resolve to
      the timeline's rate at real-time speed: its planned length is
      frames / clip rate in seconds, at the timeline's rate, TRUNCATED to
      whole frames, and it is flagged conformed in the plan. Truncated, not
      rounded: measured live on Resolve 21.1.1.10, 35 PYXIS clips at 30 fps
      on a 29.97 fps timeline each came out one frame shorter than their
      rounded length (203 frames became 202), 7723 frames in all against
      7758 rounded; the same-rate and 119.88 fps clips matched exactly.
    - Picture only (mediaType 1, V1). Audio is not placed: how Resolve lays
      a multichannel clip's sound over audio tracks that do not exist yet has
      not been checked live, and a track index that does not exist places
      nothing without saying so. Add audio by hand, or leave it.
    - The timeline is made with no custom settings: it follows the project's
      frame rate and size. (cut and sync switch a timeline to custom
      settings; a forum report says that write changes more than it names.)
"""

import math
import re

from . import api
from . import syncbuild as sb

AUTO = " [auto]"
ORDERS = ("name", "path", "given")
VIDEO = 1  # AppendToTimeline mediaType: video only


class PlanError(RuntimeError):
    """The clips cannot be planned; nothing was written."""


def natural_key(text):
    """A sort key that orders P2 before P10 and is case-blind."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(text or ""))]


def folder_at(root, path):
    """(folder, None) for a bin path from the root ('GH7', 'Source/Sony
    ILCE-7M4'; the root's own name may lead), or (None, why)."""
    parts = [p for p in str(path).strip("/").split("/") if p]
    folder = root
    if parts and parts[0] == root.GetName() and not any(
            f.GetName() == parts[0] for f in root.GetSubFolderList() or []):
        parts = parts[1:]
    for part in parts:
        found = [f for f in folder.GetSubFolderList() or [] if f.GetName() == part]
        if not found:
            return None, f"no bin '{part}' in '{folder.GetName()}'"
        if len(found) > 1:
            return None, f"{len(found)} bins named '{part}' in '{folder.GetName()}'"
        folder = found[0]
    return folder, None


def plan(root, tl_fps, bin_path=None, refs=None, order="name"):
    """The timeline's rows, in order, and the clips they place.

    Exactly one of bin_path (the clips directly in that bin) and refs (clip
    unique ids, absolute file paths or names). A bin lists what it cannot
    place (a timeline, an audio-only clip, a clip Resolve gives no rate or
    length) under skipped; a ref that cannot be placed is an error, because
    the caller named it. order: 'name' or 'path' (natural order) or 'given'
    (the order of refs, or of the bin as Resolve lists it).

    Returns {rows, skipped, clips}: rows are {name, uid, path, fps, frames,
    length, record, conformed} (length and record in timeline frames; record
    is where the row lands if every length reads back as planned), clips is
    {uid: media pool item}. Raises PlanError."""
    if order not in ORDERS:
        raise PlanError(f"order must be one of {', '.join(ORDERS)} (got '{order}')")
    strict = refs is not None
    if strict:
        clips, pool = [], [c for _, c in sb.walk(root)]  # walked once, not once per ref
        for ref in refs:
            clip, why = sb.find_clip(root, ref, pool)
            if why:
                raise PlanError(why)
            clips.append(clip)
    else:
        folder, why = folder_at(root, bin_path)
        if why:
            raise PlanError(why)
        clips = list(folder.GetClipList() or [])
    rows, skipped, by_uid = [], [], {}
    for clip in clips:
        info = sb.clip_info(clip)
        why = None
        if info["uid"] in by_uid:
            why = "listed twice"
        elif not info["video"]:
            why = f"not a video clip (Type '{clip.GetClipProperty('Type')}')"
        elif not info["fps"] or not info["frames"]:
            why = "Resolve does not report its FPS and Frames"
        if why:
            if strict:
                raise PlanError(f"'{info['name']}': {why}")
            skipped.append({"name": info["name"], "uid": info["uid"], "reason": why})
            continue
        by_uid[info["uid"]] = clip
        conformed = abs(float(info["fps"]) - float(tl_fps)) > 0.01
        length = (math.floor(info["frames"] / float(info["fps"]) * float(tl_fps) + 1e-6)
                  if conformed else info["frames"])
        rows.append({"name": info["name"], "uid": info["uid"], "path": info["path"],
                     "fps": info["fps"], "frames": info["frames"], "length": length,
                     "conformed": conformed})
    if not rows:
        raise PlanError("no video clip to place" + (
            "; the bin holds " + ", ".join(f"'{s['name']}' ({s['reason']})" for s in skipped[:3])
            if skipped else ""))
    if order == "name":
        rows.sort(key=lambda r: natural_key(r["name"]))
    elif order == "path":
        rows.sort(key=lambda r: natural_key(r["path"]))
    at = 0
    for r in rows:
        r["record"] = at
        at += r["length"]
    return {"rows": rows, "skipped": skipped, "clips": by_uid}


def read_back(tl, rows):
    """V1 against the plan: one item per row, in order, each the planned
    clip from its first source frame, its length within the rounding a
    conversion may add, the first at the timeline's start and every next
    one at the end of the one before. Nothing on any other track. Returns
    (items, problems); an item is {n, name, start, length, ok}."""
    items, problems = [], []
    start0 = int(tl.GetStartFrame())
    have = tl.GetItemListInTrack("video", 1) or []
    if len(have) != len(rows):
        problems.append(f"V1 holds {len(have)} item(s), the plan has {len(rows)}")
    end_before = start0
    for n, row in enumerate(rows):
        if n >= len(have):
            items.append({"n": n + 1, "name": row["name"], "ok": False})
            continue
        it = have[n]
        uid = sb._uid(api._safe_call(it, "GetMediaPoolItem"))
        start, end = it.GetStart(), it.GetEnd()
        src = api._safe_call(it, "GetSourceStartFrame")
        bad = []
        if uid != row["uid"]:
            bad.append(f"is clip {uid}, planned {row['uid']}")
        if src is not None and abs(float(src)) > 0:
            bad.append(f"starts at source frame {src}, planned 0")
        if start != end_before:
            bad.append(f"starts at {start - start0} (the one before ends at {end_before - start0})")
        if abs((end - start) - row["length"]) > sb.LENGTH_TOLERANCE_FRAMES:
            bad.append(f"is {end - start} frames long, planned {row['length']}")
        if bad:
            problems.append(f"#{n + 1} {row['name']}: " + "; ".join(bad))
        items.append({"n": n + 1, "name": row["name"], "start": start - start0,
                      "length": end - start, "ok": not bad})
        end_before = end
    for kind in ("video", "audio", "subtitle"):
        for t in range(1, int(api._safe_call(tl, "GetTrackCount", kind) or 0) + 1):
            if kind == "video" and t == 1:
                continue
            extra = tl.GetItemListInTrack(kind, t) or []
            if extra:
                problems.append(f"{kind[0].upper()}{t} holds {len(extra)} item(s) the plan "
                                "does not place")
    return items, problems


def build(project, media_pool, name, rows, clips, check=None):
    """Make the timeline, make it current, append every row, read it back.
    Raises api.WriteNotApplied before anything exists (the timeline could
    not be made). After that a Resolve problem, or the project changing, is
    returned, never raised, with the timeline named under left_behind:
    nothing is deleted, so a person removes a timeline that is not right. A
    cancel is raised, its message naming the timeline it left behind. Returns {name, unique_id,
    start_frame, items, problems, left_behind}."""
    tl = media_pool.CreateEmptyTimeline(name)
    if not tl or tl.GetName() != name:
        raise api.WriteNotApplied(f"CreateEmptyTimeline('{name}') failed")
    out = {"name": name, "unique_id": sb._uid(tl), "start_frame": None, "items": [],
           "problems": [], "left_behind": None}
    try:
        if not project.SetCurrentTimeline(tl) or sb._uid(project.GetCurrentTimeline()) != sb._uid(tl):
            raise api.WriteNotApplied(f"could not make '{name}' current to place clips on it")
        out["start_frame"] = int(tl.GetStartFrame())
        for row in rows:
            if check:
                check()
            media_pool.AppendToTimeline([{"mediaPoolItem": clips[row["uid"]], "startFrame": 0,
                                          "endFrame": row["frames"], "mediaType": VIDEO,
                                          "trackIndex": 1}])
        out["items"], out["problems"] = read_back(tl, rows)
    except api.ProjectChanged as e:
        out["problems"].append(f"stopped, the open project changed: {e}")
    except api.ResolveAPIError as e:
        out["problems"].append(f"{type(e).__name__}: {e}")
    except BaseException as e:  # a cancel: the timeline exists, so say so before stopping
        e.args = (f"{e} (the timeline '{name}' was made and is left in the project, holding "
                  f"{len(api._safe_call(tl, 'GetItemListInTrack', 'video', 1) or [])} item(s); "
                  "check or delete it)",) + e.args[1:]
        raise
    if out["problems"]:
        out["left_behind"] = name
    return out
