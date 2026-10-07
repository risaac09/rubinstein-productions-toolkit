"""
rpresolve.cut: build [auto] timelines from a cut manifest (see cutlist.py).

For each clip, a 16:9 timeline "<prefix>_<clip> [auto]" at the manifest's
fps holding one item per span, and for each aspect asked (9x16 by default,
1x1 too) a duplicate "<prefix>_<clip>_<aspect> [auto]" at 1080x1920 or
1080x1080 whose items are reframed on the speaker. Additive only: an
existing timeline of the same name is left alone and reported, nothing else
in the project changes. Each timeline is decided on its own: a version
missing beside an existing 16:9 is built from that 16:9 when its V1 items
are still the manifest's spans (source, frames, order), and refused when
they are not.

Reframes (see reframe.py for the transform and how a crop is planned):
    - a span's own reframe[aspect] entry (x, y, scale; written by
      reframe-plan) gives that span's item its own ZoomX, ZoomY, Pan, Tilt
    - otherwise the clip's reframe.face_x (the Ep 002 form): every item
      gets ZoomX, ZoomY and Pan centred on face_x at the source's middle
      row, as before; nothing checked the face against that crop
    - neither, or an entry reframe-plan left without a crop (manual,
      no_face): that version is not built, and the result names it
      UNREFRAMED (a 9:16 is never quietly letterboxed at Zoom 1)
A version whose picture does not fill the frame is built and named: its
result carries "bars" per span (the picture covers 810 of 1920 rows, say).
An input scaling other than scaleToFit or scaleToCrop is refused before
anything is duplicated (REFUSED: not built), and so is a 16:9 whose V1
items carry their own Scaling (the Inspector's per-clip Crop, Fit, Fill or
Stretch): the transform is computed for 0, the project's setting. A
version that fails after DuplicateTimeline names the timeline it left
behind (left_behind) and how many of its items were transformed; a re-run
skips a timeline that exists, so that one is deleted by hand first.

Gates before anything is built:
    - the source item: a media-pool clip whose file has the manifest's
      sha256 (the ASR-drift lesson: cut from the file the words came from)
    - endcheck: every span passes (review is allowed); a failing span stops
      the clip unless force=True, which is for reproducing an old cut
Every write is read back: timeline settings, each item's source frames and
duration, and the reframe properties.

Switching a timeline to custom settings is reported to change more than
itself (colour management, input and output scaling). When the guard is
enabled (RPRESOLVE_SETTINGS_GUARD=on; it is off by default), settingsguard
reads each new timeline's settings around those writes and names any key that
reads differently in the result's settings_drift. It is reported, never
corrected. With the guard off, nothing is read and settings_drift is None.

A span covers source frames [round(in*fps), round(out*fps)). Resolve's
AppendToTimeline endFrame is exclusive (measured on Ep 002: the old script's
round(out*fps) - 1 lost one frame per span), so endFrame = round(out*fps).
"""

import os
import re

from . import api
from . import cutlist
from . import reframe as rf
from . import settingsguard

AUTO = " [auto]"
WIDE = (1920, 1080)
TALL = (1080, 1920)
ASPECT_SIZES = dict(rf.ASPECTS)  # "9x16": (1080, 1920), "1x1": (1080, 1080)
DEFAULT_SCALE = rf.DEFAULT_SCALE
INPUT_SCALING = "timelineInputResMismatchBehavior"
ITEM_SCALING = "Scaling"  # an item's own: 0 use the project's, then Crop, Fit, Fill, Stretch
ITEM_SCALING_NAMES = {0: "use project settings", 1: "Crop", 2: "Fit", 3: "Fill", 4: "Stretch"}


class Unreframed(ValueError):
    """A clip has no usable reframe for an aspect; that version is not built."""


def reframe_props(reframe, src_width, tall_width=TALL[0]):
    """ZoomX/ZoomY/Pan for a 9:16 crop centred on reframe['face_x'] (source
    pixels), at reframe['scale'] timeline pixels per source pixel (default
    2.25). Resolve first fits the source to the timeline width, so the zoom
    divides out that fit. The Ep 002 script's math, generalised."""
    scale = float(reframe.get("scale", DEFAULT_SCALE))
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
    {name, frames_expected, frames, items, ok, reason, left_behind, warnings,
    settings_drift}: a timeline created and then failing its read-back is
    named there. settings_drift is settingsguard's report on the timeline's
    settings around the custom-settings writes (None when the guard is off,
    which is the default, or none was made)."""
    name = f"{prefix}_{clip['name']}{AUTO}"
    spans = clip["spans"]
    expected = cutlist.clip_frames(clip, fps)
    out = {"name": name, "frames_expected": expected, "frames": None, "items": [], "ok": False,
           "reason": "", "left_behind": None, "warnings": [], "settings_drift": None}
    if check:
        check()
    tl = media_pool.CreateEmptyTimeline(name)
    if not tl or tl.GetName() != name:
        out["reason"] = "CreateEmptyTimeline failed"
        if tl:
            out["left_behind"] = tl.GetName()
            out["reason"] += f"; LEFT BEHIND: it made '{tl.GetName()}' instead"
        return out
    project.SetCurrentTimeline(tl)
    watch = settingsguard.Watch(tl, settingsguard.WROTE_CLIP, project)
    watch.mark("new timeline")  # still following the project's settings
    # Frame rate is fixed once a timeline holds a clip; set it while empty.
    # Resolve reads it back as a float (25.0), so compare numbers.
    tl.SetSetting("useCustomSettings", "1")
    tl.SetSetting("timelineFrameRate", str(int(fps)) if float(fps).is_integer() else str(fps))
    watch.mark("first custom write")
    got = tl.GetSetting("timelineFrameRate")
    try:
        rate_ok = abs(float(got) - float(fps)) < 1e-3
    except (TypeError, ValueError):
        rate_ok = False
    try:
        if not rate_ok:
            raise api.WriteNotApplied(f"timelineFrameRate: wrote {fps}, read back {got!r}")
        _setup(tl, fps, WIDE)
        watch.mark("second custom write")  # _setup writes the flag again
    except api.WriteNotApplied as e:
        raise api.WriteNotApplied(f"{e}; LEFT BEHIND: '{name}' exists, empty; delete it "
                                  "before a re-run" + watch.tail())
    out["settings_drift"] = watch.report()
    out["warnings"] += settingsguard.warnings(out["settings_drift"])
    start = tl.GetStartFrame()
    record = start
    for s in spans:
        a, b = cutlist.frame(s["in"], fps), cutlist.frame(s["out"], fps)
        media_pool.AppendToTimeline([{"mediaPoolItem": item, "startFrame": a, "endFrame": b,
                                      "trackIndex": 1, "recordFrame": record}])
        record += b - a
    out["items"], out["frames"], problems = read_back_wide(tl, clip, fps)
    out["reason"] = "; ".join(problems)
    out["ok"] = not problems
    if problems:
        out["left_behind"] = name
        out["reason"] += (f"; LEFT BEHIND: '{name}' exists as built; delete it before a re-run, "
                          "which skips a timeline that exists")
    return out


def read_back_wide(tl, clip, fps, source_item=None):
    """(items, frames, problems): a 16:9's V1 items against the clip's
    spans, in order: each one's duration and source start, the count, and
    the timeline's length. With source_item, each item must come from that
    media-pool clip (when the item says which)."""
    spans = clip["spans"]
    items = tl.GetItemListInTrack("video", 1) or []
    rows, problems = [], []
    for it, s in zip(items, spans):
        a, b = cutlist.frame(s["in"], fps), cutlist.frame(s["out"], fps)
        rows.append({"start": it.GetStart(), "duration": it.GetDuration(),
                     "source_start": getattr(it, "GetSourceStartFrame", lambda: None)(),
                     "want_source": a, "want_duration": b - a})
    frames = (tl.GetEndFrame() - tl.GetStartFrame()) if items else 0
    if len(items) != len(spans):
        problems.append(f"{len(items)} items on V1, expected {len(spans)}")
    for i, (it, got) in enumerate(zip(items, rows), 1):
        if got["duration"] != got["want_duration"]:
            problems.append(f"span {i}: {got['duration']} frames, expected {got['want_duration']}")
        if got["source_start"] is not None and got["source_start"] != got["want_source"]:
            problems.append(f"span {i}: source starts at {got['source_start']}, expected {got['want_source']}")
        if source_item is not None:
            clip_of = getattr(it, "GetMediaPoolItem", lambda: None)()
            if clip_of is not None and clip_of.GetUniqueId() != source_item.GetUniqueId():
                problems.append(f"span {i} comes from the media-pool clip "
                                f"'{clip_of.GetName()}', and the manifest's source is "
                                f"'{source_item.GetName()}'")
    expected = cutlist.clip_frames(clip, fps)
    if frames != expected:
        problems.append(f"timeline {frames} frames, expected {expected}")
    return rows, frames, problems


def _setting(obj, key):
    """GetSetting(key) as a string, or '' when the object cannot say."""
    try:
        got = obj.GetSetting(key)
    except Exception:
        return ""
    return "" if got is None else str(got)


def input_scaling(project, timeline=None):
    """(scaling, note): how Resolve fits a source of another size, from the
    timeline's setting, else the project's. When neither reports it,
    Resolve's default scaleToFit (spike 15 read it there) and a note."""
    for obj in (timeline, project):
        got = _setting(obj, INPUT_SCALING) if obj is not None else ""
        if got:
            return got, ""
    return rf.SCALE_TO_FIT, f"{INPUT_SCALING} not reported; Resolve's default scaleToFit assumed"


def scaling_problem(scaling):
    """'' for an input scaling the transform is known for, else why not."""
    if scaling in rf.SCALINGS:
        return ""
    return (f"{INPUT_SCALING} is '{scaling}'; the reframe transform is known for "
            f"{' and '.join(rf.SCALINGS)} only")


def item_scaling(items):
    """(problems, note) for timeline items' own Scaling property: each must
    be 0 (use the project's setting), since the transform is computed from
    the input scaling. An item that does not report it is counted in the
    note, and 0 is assumed for it."""
    problems, unknown = [], 0
    for i, it in enumerate(items, 1):
        try:
            got = it.GetProperty(ITEM_SCALING)
        except Exception:
            got = None
        if got is None or got == "":
            unknown += 1
            continue
        try:
            n = int(float(got))
        except (TypeError, ValueError):
            problems.append(f"item {i} reads Scaling {got!r}")
            continue
        if n != 0:
            problems.append(f"item {i} has its own Scaling {n} "
                            f"({ITEM_SCALING_NAMES.get(n, 'unknown')})")
    note = (f"{unknown} of {len(items)} V1 items did not report their {ITEM_SCALING}; 0 (use "
            "project settings) assumed" if unknown else "")
    return problems, note


def parse_resolution(text):
    """(width, height) from a media-pool clip's Resolution ('1280x720');
    (0, None) when it says nothing usable."""
    m = re.match(r"\s*(\d+)\s*[xX]\s*(\d+)", str(text or ""))
    return (int(m.group(1)), int(m.group(2))) if m else (0, None)


def _src_size(src):
    """(width, height) from a (w, h) pair or a bare width (height unknown)."""
    if isinstance(src, (tuple, list)):
        return int(src[0]), (int(src[1]) if len(src) > 1 and src[1] else None)
    return int(src), None


def item_plans(clip, aspect, src, scaling=rf.SCALE_TO_FIT):
    """One transform per span for the clip's `aspect` version, before
    anything is written: [{span, source, props, fills, bars, review}].
    A span's own reframe[aspect] wins; else the clip's face_x (ZoomX,
    ZoomY, Pan only, as the Ep 002 script wrote them). Raises Unreframed
    naming the span when there is neither, or when reframe-plan left the
    span without a crop."""
    out = ASPECT_SIZES[aspect]
    sw, sh = _src_size(src)
    clip_rf = clip.get("reframe") or {}
    plans = []
    if not clip.get("spans"):
        raise Unreframed(f"no reframe for {aspect}: the clip has no spans")
    if not sw:
        raise Unreframed(f"the source clip reports no resolution, so no {aspect} crop can be placed")
    for i, span in enumerate(clip["spans"], 1):
        e = (span.get("reframe") or {}).get(aspect)
        if e is not None:
            if e.get("x") is None or e.get("scale") is None:
                raise Unreframed(f"span {i} has no {aspect} crop (reframe-plan: "
                                 f"{e.get('status', 'no x or scale')}"
                                 + (f": {e['reason']}" if e.get("reason") else "") + ")")
            planned = [int(v) for v in e.get("src") or []]
            if planned and sh and planned != [sw, sh]:
                raise Unreframed(f"span {i}: its {aspect} crop was planned on a "
                                 f"{planned[0]}x{planned[1]} source, and this clip is {sw}x{sh}")
            if not sh:
                raise Unreframed(f"span {i}: the source height is unknown, so its {aspect} "
                                 "crop cannot be placed")
            y = e.get("y")
            scale, centre = float(e["scale"]), (float(e["x"]), sh / 2.0 if y is None else float(y))
            area = e.get("area") or (0, 0, sw, sh)
            props = rf.resolve_props((sw, sh), out, scale, centre, scaling)
            source, review = "span", e.get("review", "")
        elif clip_rf.get("face_x") is not None:
            scale = float(clip_rf.get("scale", DEFAULT_SCALE))
            centre = (float(clip_rf["face_x"]), sh / 2.0 if sh else None)
            if sh:
                props = rf.resolve_props((sw, sh), out, scale, centre, scaling)
                props.pop("Tilt")
            else:
                props = reframe_props(clip_rf, sw, out[0])
            area, source, review = ((0, 0, sw, sh) if sh else None), "clip", ""
        else:
            raise Unreframed(f"span {i}: no reframe for {aspect}, and the clip has no "
                             "reframe.face_x")
        fills = rf.fills(out, scale, centre, area) if area else None
        plans.append({"span": i, "source": source, "props": props, "fills": fills,
                      "bars": "" if fills in (True, None) else rf.bars_text(out, scale, centre, area),
                      "review": review})
    return plans


def build_aspect(project, wide_tl, clip, src, prefix, aspect="9x16", check=None, scaling=None):
    """Duplicate a built 16:9 timeline as the clip's `aspect` version (9x16
    or 1x1): set its resolution, give each V1 item its span's transform and
    read every property back. Returns {name, kind, ok, reason, unreframed,
    refused, left_behind, props, items, bars, warnings, input_scaling,
    settings_drift}.
    Nothing is duplicated for a version with no reframe (unreframed), an
    input scaling the transform is not known for, or one other than
    `scaling` (the one the plan was made for, when given) (refused); a
    failure after the duplicate names it in left_behind."""
    name = f"{prefix}_{clip['name']}_{aspect}{AUTO}"
    out = {"name": name, "kind": aspect, "ok": False, "reason": "", "unreframed": False,
           "refused": False, "left_behind": None, "props": None, "items": [], "bars": [],
           "warnings": [], "input_scaling": None, "settings_drift": None}
    planned = scaling
    scaling, note = input_scaling(project, wide_tl)
    out["input_scaling"] = scaling
    problem = scaling_problem(scaling)
    if not problem and planned is not None and scaling != planned:
        problem = (f"{INPUT_SCALING} reads '{scaling}' on the 16:9, and the plan was made for "
                   f"'{planned}'; run the dry run again")
    own, item_note = item_scaling(wide_tl.GetItemListInTrack("video", 1) or [])
    if not problem and own:
        problem = (f"on the 16:9, {'; '.join(own)}; the transform is computed for 0 (use "
                   "project settings), so set it back in the Inspector or cut under a new prefix")
    if problem:
        out.update(refused=True, reason=f"REFUSED: {problem}; the {aspect} version was not built")
        return out
    try:
        plans = item_plans(clip, aspect, src, scaling)
    except Unreframed as e:
        out.update(unreframed=True, reason=f"UNREFRAMED: {e}; the {aspect} version was not built")
        return out
    if check:
        check()
    project.SetCurrentTimeline(wide_tl)
    # The baseline is the 16:9, read from its own handle before the copy exists (the copy
    # becomes current), so a version reports only what its own write did.
    watch = settingsguard.Watch(wide_tl, settingsguard.WROTE_ASPECT, baseline="the 16:9")
    watch.mark("16:9")
    tl = wide_tl.DuplicateTimeline(name)
    if not tl:
        out["reason"] = "DuplicateTimeline failed"
        return out

    seen = {"items": None}

    def left(reason, where=None):
        out["left_behind"] = where or name
        done = len(out["items"])
        state = ("no item transformed" if not done else
                 f"{done} of its {seen['items']} V1 items transformed and the rest untransformed")
        out["settings_drift"] = watch.report()
        out["reason"] = (f"{reason}; LEFT BEHIND: '{out['left_behind']}' exists with {state}; "
                         "delete it before a re-run, which skips a timeline that exists"
                         + settingsguard.tail(out["settings_drift"]))
        return out
    if tl.GetName() != name:
        return left(f"DuplicateTimeline made '{tl.GetName()}', expected '{name}'", tl.GetName())
    watch.mark("copy", tl)
    try:
        _setup(tl, None, ASPECT_SIZES[aspect])
    except api.WriteNotApplied as e:
        watch.mark("custom write", tl)
        return left(str(e))
    watch.mark("custom write", tl)
    out["settings_drift"] = watch.report()
    got_scaling, note = input_scaling(project, tl)
    out["input_scaling"] = got_scaling
    if got_scaling != scaling:
        return left(f"{INPUT_SCALING} reads '{got_scaling}' on {name} and '{scaling}' where it "
                    "was duplicated from, so no item was transformed")
    items = tl.GetItemListInTrack("video", 1) or []
    seen["items"] = len(items)
    if not items:
        return left(f"no items on V1 of {name}")
    own, item_note = item_scaling(items)
    if own:
        return left(f"on {name}, {'; '.join(own)}; the transform is computed for 0 (use project "
                    "settings)")
    if all(p["source"] == "clip" for p in plans):
        # The clip-level form: one transform for every V1 item, as before.
        plans = [dict(plans[0], span=n) for n in range(1, len(items) + 1)]
    elif len(items) != len(plans):
        return left(f"{len(items)} items on V1 of {name}, expected {len(plans)} (one per span)")
    try:
        for it, p in zip(items, plans):
            got = {k: api.set_property_checked(it, k, v) for k, v in p["props"].items()}
            out["items"].append({"span": p["span"], "source": p["source"],
                                 "props": {k: round(v, 4) for k, v in p["props"].items()},
                                 "read_back": {k: round(float(v), 4) for k, v in got.items()},
                                 "fills": p["fills"], "bars": p["bars"]})
    except api.WriteNotApplied as e:
        return left(str(e))
    distinct = {tuple(sorted(i["props"].items())) for i in out["items"]}
    out["props"] = out["items"][0]["props"] if len(distinct) == 1 else None
    out["bars"] = [f"span {p['span']}: {p['bars']}" for p in plans if p["bars"]]
    warnings = [w for w in (note, item_note) if w]
    if any(p["source"] == "clip" for p in plans):
        warnings.append("reframed from the clip's hand-set face_x; nothing checked the face "
                        "inside this crop (reframe-plan does)")
    warnings += [f"span {p['span']}: {p['review']}" for p in plans if p["review"]]
    if any(abs(p["props"].get("Tilt", 0.0)) > 1e-6 for p in plans):
        warnings.append("Tilt is set on at least one item; its sign (up is positive) is not yet "
                        "confirmed on a render, so check a still")
    warnings += settingsguard.warnings(out["settings_drift"])
    out["warnings"] = warnings
    out["ok"] = True
    return out


def build_tall(project, wide_tl, clip, src_width, prefix, check=None):
    """The 9:16 version (build_aspect with aspect 9x16); kept for callers of
    the first form."""
    return build_aspect(project, wide_tl, clip, src_width, prefix, "9x16", check)


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
