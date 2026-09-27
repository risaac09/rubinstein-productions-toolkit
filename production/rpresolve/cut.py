"""
rpresolve.cut: build [auto] timelines from a cut manifest (see cutlist.py).

For each clip, a 16:9 timeline "<prefix>_<clip> [auto]" at the manifest's
fps holding one item per span, and a 9:16 duplicate "<prefix>_<clip>_9x16
[auto]" reframed on the speaker. Additive only: an existing timeline of the
same name is left alone and reported, nothing else in the project changes.

Gates before anything is built:
    - the source item: a media-pool clip whose file has the manifest's
      sha256 (the ASR-drift lesson: cut from the file the words came from)
    - endcheck: every span passes (review is allowed); a failing span stops
      the clip unless force=True, which is for reproducing an old cut
Every write is read back: timeline settings, each item's source frames and
duration, and the reframe properties.

A span covers source frames [round(in*fps), round(out*fps)). Resolve's
AppendToTimeline endFrame is exclusive (measured on Ep 002: the old script's
round(out*fps) - 1 lost one frame per span), so endFrame = round(out*fps).
"""

import os

from . import api
from . import cutlist

AUTO = " [auto]"
WIDE = (1920, 1080)
TALL = (1080, 1920)


def reframe_props(reframe, src_width, tall_width=TALL[0]):
    """ZoomX/ZoomY/Pan for a 9:16 crop centred on reframe['face_x'] (source
    pixels), at reframe['scale'] timeline pixels per source pixel (default
    2.25). Resolve first fits the source to the timeline width, so the zoom
    divides out that fit. The Ep 002 script's math, generalised."""
    scale = float(reframe.get("scale", 2.25))
    face_x = float(reframe["face_x"])
    zoom = scale / (tall_width / float(src_width))
    pan = (src_width / 2.0 - face_x) * scale
    return {"ZoomX": zoom, "ZoomY": zoom, "Pan": pan}


def find_source_item(root, sha256, size_hint=None):
    """The media-pool clip whose file hashes to sha256. Files are hashed only
    when their size matches the manifest source's, so a large pool is cheap."""
    stack, seen = [root], set()
    while stack:
        folder = stack.pop()
        for clip in folder.GetClipList() or []:
            path = clip.GetClipProperty("File Path")
            if not path or path in seen or not os.path.isfile(path):
                continue
            seen.add(path)
            if size_hint is not None and os.path.getsize(path) != size_hint:
                continue
            if cutlist.sha256_file(path) == sha256:
                return clip
        stack.extend(folder.GetSubFolderList() or [])
    return None


def timeline_names(project):
    return {project.GetTimelineByIndex(i).GetName(): project.GetTimelineByIndex(i)
            for i in range(1, (project.GetTimelineCount() or 0) + 1)}


def _setup(timeline, fps, size):
    timeline.SetSetting("useCustomSettings", "1")
    api.set_setting_checked(timeline, "timelineResolutionWidth", str(size[0]))
    api.set_setting_checked(timeline, "timelineResolutionHeight", str(size[1]))


def build_clip(project, media_pool, item, clip, fps, prefix, check=None):
    """Build one clip's 16:9 timeline and read it back. Returns
    {name, frames_expected, frames, items, ok, reason}."""
    name = f"{prefix}_{clip['name']}{AUTO}"
    spans = clip["spans"]
    expected = cutlist.clip_frames(clip, fps)
    out = {"name": name, "frames_expected": expected, "frames": None, "items": [], "ok": False,
           "reason": ""}
    if check:
        check()
    tl = media_pool.CreateEmptyTimeline(name)
    if not tl or tl.GetName() != name:
        out["reason"] = "CreateEmptyTimeline failed"
        return out
    project.SetCurrentTimeline(tl)
    # Frame rate is fixed once a timeline holds a clip; set it while empty.
    # Resolve reads it back as a float (25.0), so compare numbers.
    tl.SetSetting("useCustomSettings", "1")
    tl.SetSetting("timelineFrameRate", str(int(fps)) if float(fps).is_integer() else str(fps))
    got = tl.GetSetting("timelineFrameRate")
    try:
        rate_ok = abs(float(got) - float(fps)) < 1e-3
    except (TypeError, ValueError):
        rate_ok = False
    if not rate_ok:
        raise api.WriteNotApplied(f"timelineFrameRate: wrote {fps}, read back {got!r}")
    _setup(tl, fps, WIDE)
    start = tl.GetStartFrame()
    record = start
    for s in spans:
        a, b = cutlist.frame(s["in"], fps), cutlist.frame(s["out"], fps)
        media_pool.AppendToTimeline([{"mediaPoolItem": item, "startFrame": a, "endFrame": b,
                                      "trackIndex": 1, "recordFrame": record}])
        record += b - a
    items = tl.GetItemListInTrack("video", 1) or []
    for it, s in zip(items, spans):
        a, b = cutlist.frame(s["in"], fps), cutlist.frame(s["out"], fps)
        got = {"start": it.GetStart(), "duration": it.GetDuration(),
               "source_start": getattr(it, "GetSourceStartFrame", lambda: None)(),
               "want_source": a, "want_duration": b - a}
        out["items"].append(got)
    out["frames"] = (tl.GetEndFrame() - start) if items else 0
    problems = []
    if len(items) != len(spans):
        problems.append(f"{len(items)} items on V1, expected {len(spans)}")
    for i, got in enumerate(out["items"], 1):
        if got["duration"] != got["want_duration"]:
            problems.append(f"span {i}: {got['duration']} frames, expected {got['want_duration']}")
        if got["source_start"] is not None and got["source_start"] != got["want_source"]:
            problems.append(f"span {i}: source starts at {got['source_start']}, expected {got['want_source']}")
    if out["frames"] != expected:
        problems.append(f"timeline {out['frames']} frames, expected {expected}")
    out["reason"] = "; ".join(problems)
    out["ok"] = not problems
    return out


def build_tall(project, wide_tl, clip, src_width, prefix, check=None):
    """Duplicate a built 16:9 timeline as the 9:16 version and reframe every
    V1 item. Returns {name, ok, reason, props}."""
    name = f"{prefix}_{clip['name']}_9x16{AUTO}"
    out = {"name": name, "ok": False, "reason": "", "props": None}
    reframe = clip.get("reframe")
    if not reframe or "face_x" not in reframe:
        out["reason"] = "no reframe.face_x in the manifest; 9:16 skipped"
        return out
    if check:
        check()
    project.SetCurrentTimeline(wide_tl)
    tl = wide_tl.DuplicateTimeline(name)
    if not tl or tl.GetName() != name:
        out["reason"] = "DuplicateTimeline failed"
        return out
    _setup(tl, None, TALL)
    props = reframe_props(reframe, src_width)
    try:
        for it in tl.GetItemListInTrack("video", 1) or []:
            for key, value in props.items():
                api.set_property_checked(it, key, value)
    except api.WriteNotApplied as e:
        out["reason"] = str(e)
        return out
    out["props"] = {k: round(v, 4) for k, v in props.items()}
    out["ok"] = True
    return out


def plan_clips(manifest, only=None):
    clips = manifest["clips"]
    if only:
        wanted = set(only)
        missing = wanted - {c["name"] for c in clips}
        if missing:
            raise cutlist.CutlistError("no such clip(s): " + ", ".join(sorted(missing)))
        clips = [c for c in clips if c["name"] in wanted]
    return clips


def gate_clips(manifest, clips, audio=True):
    """{clip name: [failing span reasons]} from endcheck; empty lists pass."""
    rows = cutlist.endcheck({**manifest, "clips": clips}, audio=audio)
    fails = {c["name"]: [] for c in clips}
    for r in rows:
        if not r["ok"]:
            fails[r["clip"]].append(f"span {r['span']}: {r['reason']}")
    return fails
