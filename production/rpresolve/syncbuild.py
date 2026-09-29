"""
rpresolve.syncbuild: the Resolve half of sync. From two media-pool clips
and an offset measured by rpresolve.sync, a stacked multitrack ' [auto]'
timeline: the reference on V1/A1, the other recording on the next track at
the offset. Stdlib only; workflows.sync pins the project, plans and puts
the UI back.

What Resolve 21.0.4.5 does, and what this module does about it:
    - MediaPool.AppendToTimeline appends to the current timeline and
      returns a truthy value even when it places nothing (spike 18), so
      the new timeline is made current first and every placement is read
      back from the tracks: its media, start, source start and length.
    - A track index that does not exist yet places nothing, so every track
      is made with Timeline.AddTrack first and counted again after.
    - recordFrame counts absolute timeline frames (a 25 fps timeline
      starts at 90000 for 01:00:00:00), so placements are planned relative
      to the start and added to GetStartFrame() once the timeline exists.
    - startFrame and endFrame count source frames at the source's own
      rate, and endFrame is exclusive (cut.py, measured on Ep 002).
    - Rates come spelled short ('29.97'); every frame here is counted at
      the rate that stands for (deliver.exact_fps: 30000/1001), and the
      string goes only to SetSetting.
    - MediaPool.AutoSyncAudio(items, settings) links the audio into the
      clips it syncs, changing media-pool items, so it runs only on clips
      this run imported. What it did is read back through a second
      ' [auto]' timeline and compared with the measured offset; its own
      answer is never trusted alone.
"""

import os

from . import api, cutlist
from .deliver import exact_fps
from .deliver import fps_number as _number  # the leading number of a property ('25.000')

AUTO = " [auto]"


class SyncError(RuntimeError):
    """rpresolve.sync could not measure the audio. Defined here, in a module
    that needs no numpy, so callers can catch it where numpy is missing."""


# The timeline frame rates Resolve offers, as it spells them.
RESOLVE_RATES = ("23.976", "24", "25", "29.97", "30", "47.952", "48", "50", "59.94", "60",
                 "72", "95.904", "96", "100", "119.88", "120")
# A length converted between the source's rate and the timeline's may round by a
# frame; the record and source start frames are planned whole and must read back exactly.
LENGTH_TOLERANCE_FRAMES = 1


def rate_string(fps):
    """A frame rate (29.97002997) as Resolve spells it ('29.97'), or None
    when Resolve offers no such timeline rate."""
    try:
        f = float(fps)
    except (TypeError, ValueError):
        return None
    best = min(RESOLVE_RATES, key=lambda r: abs(float(r) - f))
    return best if abs(float(best) - f) < 0.01 else None


def drift_words(d, fps=None):
    """A measurement's drift (rpresolve.sync.drift) in words: the drift,
    how far the ends of the overlap sit from a placement made at its
    midpoint, and, past the threshold, the retime that leaves only the
    rounding."""
    text = (f"{d['ms_per_min']:+.3f} ms/min ({d['ppm']:+.2f} ppm), {d['over_overlap_ms']:+.1f} "
            f"ms over the overlap; placed by the drift line at the overlap's midpoint, its ends "
            f"sit up to {d['worst_ms']:.1f} ms ({d['worst_frames']:.2f} frame) from the "
            "placement, the rounding and half the drift together")
    if d["exceeds"]:
        at = d["retime_offset_s"]
        text += (f"; OVER {d['threshold_frames']:g} frame and left as measured: retiming the "
                 f"other clip to {d['retime_pct']:.5f}% about its first frame, with that frame at "
                 f"{at:+.4f} s" + (f" (frame {cutlist.frame(at, fps):+d})" if fps else "") +
                 ", would leave only the rounding")
    return text


def _uid(obj):
    return api._safe_call(obj, "GetUniqueId")


def walk(root):
    """[(bin path, clip)] for every clip under root."""
    out, stack = [], [("", root)]
    while stack:
        path, folder = stack.pop()
        for clip in folder.GetClipList() or []:
            out.append((path, clip))
        for sub in folder.GetSubFolderList() or []:
            stack.append((f"{path}/{sub.GetName()}".lstrip("/"), sub))
    return out


def find_clip(root, ref):
    """The one media-pool clip `ref` names: its unique id, else its file's
    absolute path, else its clip name. Returns (clip, None) or (None, why)."""
    clips = [c for _, c in walk(root)]
    by_id = [c for c in clips if _uid(c) == ref]
    if by_id:
        return by_id[0], None
    if os.path.isabs(os.path.expanduser(ref)):
        want = os.path.realpath(os.path.expanduser(ref))
        by_path = [c for c in clips if c.GetClipProperty("File Path") and
                   os.path.realpath(c.GetClipProperty("File Path")) == want]
        if len(by_path) == 1:
            return by_path[0], None
        if len(by_path) > 1:
            return None, (f"{len(by_path)} clips in the media pool use {ref}; name one by its "
                          "unique id")
        return None, f"no clip in the media pool uses {ref}"
    by_name = [c for c in clips if c.GetName() == ref]
    if len(by_name) == 1:
        return by_name[0], None
    if len(by_name) > 1:
        return None, f"{len(by_name)} clips are named '{ref}'; name one by its unique id or path"
    return None, f"no clip in the media pool is named or has the unique id '{ref}'"


def clip_info(clip):
    """What placing a clip needs: {name, uid, path, fps, frames, video,
    channels}. fps and frames are None when Resolve does not report them."""
    kind = str(clip.GetClipProperty("Type") or "")
    frames = _number(clip.GetClipProperty("Frames"))
    return {"name": clip.GetName(), "uid": _uid(clip),
            "path": clip.GetClipProperty("File Path") or "",
            "fps": exact_fps(clip.GetClipProperty("FPS")),
            "frames": int(frames) if frames is not None else None,
            "video": "video" in kind.lower(),
            "channels": int(_number(clip.GetClipProperty("Audio Ch")) or 0)}


def audio_subtype(channels):
    """The AddTrack audio type for a clip's channel count."""
    n = int(channels or 0)
    return {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}.get(n, f"adaptive{n}" if 2 < n <= 36
                                                            else "stereo")


def plan(ref, other, offset_frames, fps):
    """The placements, relative to the timeline's start: the reference on
    V1 (when it has picture) and A1 at the head, the other on V2/A2 (a
    camera) or A2 (audio only) offset_frames later. A negative offset puts
    the reference that much after the start instead, so nothing is
    trimmed. Lengths are in timeline frames. Returns {appends,
    placements, tracks}."""
    ref_rec, oth_rec = max(0, -offset_frames), max(0, offset_frames)
    placements, appends, tracks = [], [], []
    for role, info, rec, track in (("reference", ref, ref_rec, 1), ("other", other, oth_rec, 2)):
        length = cutlist.frame(info["frames"] / float(info["fps"]), fps)
        media_type = None if info["video"] else 2
        appends.append({"role": role, "startFrame": 0, "endFrame": info["frames"],
                        "trackIndex": track, "record": rec, "mediaType": media_type})
        kinds = (["video"] if info["video"] else []) + (["audio"] if info["channels"] else [])
        for kind in kinds:
            placements.append({"role": role, "kind": kind, "track": track, "record": rec,
                               "source_start": 0, "length": length})
            sub = audio_subtype(info["channels"]) if kind == "audio" else None
            tracks.append({"kind": kind, "index": track, "subtype": sub})
    return {"appends": appends, "placements": placements, "tracks": tracks}


def new_timeline(project, media_pool, name, rate):
    """Create an empty timeline, make it current (AppendToTimeline works on
    the current one) and set its frame rate while it is empty, reading
    each back. Returns the timeline; raises WriteNotApplied."""
    tl = media_pool.CreateEmptyTimeline(name)
    if not tl or tl.GetName() != name:
        raise api.WriteNotApplied(f"CreateEmptyTimeline('{name}') failed")
    if not project.SetCurrentTimeline(tl) or _uid(project.GetCurrentTimeline()) != _uid(tl):
        raise api.WriteNotApplied(f"could not make '{name}' current to place clips on it")
    tl.SetSetting("useCustomSettings", "1")
    tl.SetSetting("timelineFrameRate", rate)
    got = _number(tl.GetSetting("timelineFrameRate"))
    if got is None or abs(got - float(rate)) > 1e-3:
        raise api.WriteNotApplied(f"timelineFrameRate on '{name}': wrote {rate}, read back "
                                  f"{tl.GetSetting('timelineFrameRate')!r}")
    return tl


def ensure_track(tl, kind, index, subtype=None):
    """Add tracks of kind until index exists, counting again after each,
    and read back the audio type of a track it added. Returns a problem
    string or None."""
    added = False
    while int(tl.GetTrackCount(kind) or 0) < index:
        before = int(tl.GetTrackCount(kind) or 0)
        if kind == "audio":
            tl.AddTrack(kind, subtype or "stereo")
        else:
            tl.AddTrack(kind)
        if int(tl.GetTrackCount(kind) or 0) != before + 1:
            return f"AddTrack('{kind}') did not add a track ({before} before and after)"
        added = True
    if added and kind == "audio" and subtype:
        got = api._safe_call(tl, "GetTrackSubType", "audio", index)
        if got and got != subtype:
            return f"A{index} was added as {got}, wanted {subtype}"
    return None


def read_back(tl, placements, clips, start):
    """Each planned placement against the track: exactly one item of that
    clip there, at exactly its planned start and source start, and its
    length within LENGTH_TOLERANCE_FRAMES. Returns (rows, problems); an
    item on any track that no placement explains is a problem too."""
    # Items are known by track and position: Resolve hands back a new wrapper
    # object for the same item on every GetItemListInTrack call.
    rows, problems, seen = [], [], set()
    for p in placements:
        uid = clips[p["role"]]["uid"]
        items = tl.GetItemListInTrack(p["kind"], p["track"]) or []
        mine = [(n, it) for n, it in enumerate(items)
                if _uid(api._safe_call(it, "GetMediaPoolItem")) == uid]
        tag = f"{p['role']} {p['kind'][0].upper()}{p['track']}"
        row = {**p, "found": len(mine), "got": None, "ok": False}
        if len(mine) != 1:
            problems.append(f"{tag}: {len(mine)} item(s) of the clip there, expected 1")
            rows.append(row)
            continue
        n, it = mine[0]
        seen.add((p["kind"], p["track"], n))
        got = {"record": it.GetStart() - start, "source_start": api._safe_call(it, "GetSourceStartFrame"),
               "length": it.GetEnd() - it.GetStart()}
        row["got"] = got
        bad = [f"{k} {got[k]} (planned {p[k]})" for k in ("record", "source_start", "length")
               if got[k] is not None and abs(float(got[k]) - p[k]) >
               (LENGTH_TOLERANCE_FRAMES if k == "length" else 0)]
        if bad:
            problems.append(f"{tag}: " + ", ".join(bad))
        row["ok"] = not bad
        rows.append(row)
    for kind in ("video", "audio"):
        for t in range(1, int(tl.GetTrackCount(kind) or 0) + 1):
            for n, it in enumerate(tl.GetItemListInTrack(kind, t) or []):
                if (kind, t, n) not in seen:
                    problems.append(f"{kind[0].upper()}{t}: an item no placement explains "
                                    f"({api._safe_call(it, 'GetName')})")
    return rows, problems


def append(media_pool, clip, a, start):
    """One AppendToTimeline call for a planned append; returns what Resolve
    returned, as a bool (it proves nothing)."""
    info = {"mediaPoolItem": clip, "startFrame": a["startFrame"], "endFrame": a["endFrame"],
            "trackIndex": a["trackIndex"], "recordFrame": start + a["record"]}
    if a["mediaType"]:
        info["mediaType"] = a["mediaType"]
    return bool(media_pool.AppendToTimeline([info]))


def build(project, media_pool, name, rate, fps, clips, the_plan, check=None):
    """Make the stacked timeline and read every placement back. clips is
    {"reference": clip, "other": clip, plus their clip_info under
    "reference_info"/"other_info"}. Returns {name, unique_id,
    start_frame, placements, returned, problems}."""
    if check:
        check()
    tl = new_timeline(project, media_pool, name, rate)
    start = tl.GetStartFrame()
    out = {"name": name, "unique_id": _uid(tl), "start_frame": start, "placements": [],
           "returned": {}, "problems": []}
    by_role = {a["role"]: a for a in the_plan["appends"]}
    for role in ("reference", "other"):
        a = by_role[role]
        for t in the_plan["tracks"]:
            if t["index"] == a["trackIndex"]:
                problem = ensure_track(tl, t["kind"], t["index"], t["subtype"])
                if problem:
                    out["problems"].append(problem)
        busy = [f"{t['kind'][0].upper()}{t['index']}" for t in the_plan["tracks"]
                if t["index"] == a["trackIndex"] and
                (tl.GetItemListInTrack(t["kind"], t["index"]) or [])]
        if busy:
            out["problems"].append(f"{', '.join(busy)} already hold items before the {role} "
                                   "was placed; it was not placed")
            continue
        if check:
            check()
        out["returned"][role] = append(media_pool, clips[role], a, start)
    infos = {"reference": clips["reference_info"], "other": clips["other_info"]}
    rows, problems = read_back(tl, the_plan["placements"], infos, start)
    out["placements"] = rows
    out["problems"] += problems
    out["timeline"] = tl
    return out


# ---------------------------------------------------------------------------
# AutoSyncAudio: offered on clips this run imported; checked, never trusted
# ---------------------------------------------------------------------------

AUTOSYNC_CONSTANTS = ("AUDIO_SYNC_MODE", "AUDIO_SYNC_WAVEFORM", "AUDIO_SYNC_CHANNEL_NUMBER",
                      "AUDIO_SYNC_CHANNEL_MIX", "AUDIO_SYNC_RETAIN_EMBEDDED_AUDIO",
                      "AUDIO_SYNC_RETAIN_VIDEO_METADATA")


def autosync_settings(resolve):
    """(settings, missing): waveform sync on the mix of every channel,
    keeping the camera's own audio and metadata. missing names any
    resolve.CONSTANT this Resolve does not define (they read as None)."""
    c = {n: getattr(resolve, n, None) for n in AUTOSYNC_CONSTANTS}
    missing = [n for n, v in c.items() if v is None]
    if missing:
        return None, missing
    return {c["AUDIO_SYNC_MODE"]: c["AUDIO_SYNC_WAVEFORM"],
            c["AUDIO_SYNC_CHANNEL_NUMBER"]: c["AUDIO_SYNC_CHANNEL_MIX"],
            c["AUDIO_SYNC_RETAIN_EMBEDDED_AUDIO"]: True,
            c["AUDIO_SYNC_RETAIN_VIDEO_METADATA"]: True}, []


def clip_props(clip):
    props = api._safe_call(clip, "GetClipProperty")
    return dict(props) if isinstance(props, dict) else {}


def implied_offset(tl, ref_info, oth_info, fps):
    """Where Resolve put the other file's first frame against the
    reference, from a timeline holding the synced reference: the audio
    item of the other file (its start and source start) against the
    reference's video item. None when the other file is not an item of its
    own there."""
    video = [it for t in range(1, int(tl.GetTrackCount("video") or 0) + 1)
             for it in tl.GetItemListInTrack("video", t) or []
             if _uid(api._safe_call(it, "GetMediaPoolItem")) == ref_info["uid"]]
    audio = [it for t in range(1, int(tl.GetTrackCount("audio") or 0) + 1)
             for it in tl.GetItemListInTrack("audio", t) or []
             if _uid(api._safe_call(it, "GetMediaPoolItem")) == oth_info["uid"]]
    if len(video) != 1 or len(audio) != 1:
        return None
    v, a = video[0], audio[0]
    return ((a.GetStart() - v.GetStart()) / float(fps)
            - (api._safe_call(a, "GetSourceStartFrame") or 0) / float(oth_info["fps"])
            + (api._safe_call(v, "GetSourceStartFrame") or 0) / float(ref_info["fps"]))
