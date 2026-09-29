"""
rpresolve.markers: the Resolve half of trim-review. Review rows
(rpresolve.trimreview) as markers on an ' [auto]' timeline, one per row,
each read back with GetMarkers. It adds markers and nothing else: no cut,
ripple, move or delete. Stdlib only; workflows.trim_review_markers pins
the project, plans and puts the UI back.

Where a row lands: the timeline items whose media is the review's source
file map source seconds to timeline frames (an item starting at timeline
frame S with source start frame F, at the source's own rate, shows source
second t at S + (t - F / source fps) * timeline fps). Items that play at
another speed are skipped and reported, since that mapping would not hold.

Timeline markers are addressed by frames from the timeline's start (the
scripting README: GetMarkers returns {96.0: {...}} for a marker "at
timeline offset 96"), unlike AppendToTimeline's recordFrame, which counts
absolute frames. Resolve holds one marker per frame, so a row that would
land on a frame already holding a marker is refused and reported, never
moved or merged.
"""

import os

from . import api, cutlist
from .deliver import fps_number as _number  # the leading number of a property ('25.000')
from .trimreview import COLORS

# The marker colours Resolve offers.
MARKER_COLORS = ("Blue", "Cyan", "Green", "Yellow", "Red", "Pink", "Purple", "Fuchsia",
                 "Rose", "Lavender", "Sky", "Mint", "Lemon", "Sand", "Cocoa", "Cream")
CUSTOM = "rpresolve-trim-review"
SPEED_TOLERANCE_FRAMES = 1


def source_items(tl, source):
    """(items, skipped) for the timeline items whose media file is
    `source`: video tracks first, audio tracks when no video item uses it.
    Each item: {kind, track, start, end, src_fps, src_start_s, src_end_s,
    item}. skipped: [why] for items that cannot be mapped."""
    want = os.path.realpath(source)
    found, skipped = {"video": [], "audio": []}, []
    for kind in ("video", "audio"):
        for t in range(1, int(api._safe_call(tl, "GetTrackCount", kind) or 0) + 1):
            for it in tl.GetItemListInTrack(kind, t) or []:
                clip = api._safe_call(it, "GetMediaPoolItem")
                path = api._safe_call(clip, "GetClipProperty", "File Path") or ""
                if not path or os.path.realpath(path) != want:
                    continue
                label = f"{kind[0].upper()}{t} at {it.GetStart()}"
                fps = _number(api._safe_call(clip, "GetClipProperty", "FPS"))
                s0, s1 = (api._safe_call(it, "GetSourceStartFrame"),
                          api._safe_call(it, "GetSourceEndFrame"))
                if not fps or s0 is None:
                    skipped.append(f"{label}: its source frame rate or start cannot be read")
                    continue
                found[kind].append({"kind": kind, "track": t, "start": it.GetStart(),
                                    "end": it.GetEnd(), "src_fps": fps, "src_start": s0,
                                    "src_end": s1, "item": it})
    items = found["video"] or found["audio"]
    return items, skipped


def mappable(items, tl_fps):
    """(items at 100% speed with src_start_s/src_end_s set, skipped).
    Speed is judged by the source frames an item spans against its length
    on the timeline."""
    ok, skipped = [], []
    for it in items:
        length = it["end"] - it["start"]
        want = length * it["src_fps"] / tl_fps
        if it["src_end"] is not None and abs((it["src_end"] - it["src_start"]) - want) > \
                SPEED_TOLERANCE_FRAMES + 1:
            skipped.append(f"{it['kind'][0].upper()}{it['track']} at {it['start']}: it spans "
                           f"{it['src_end'] - it['src_start']:g} source frames over {length} "
                           "timeline frames (retimed); not mapped")
            continue
        it = dict(it, src_start_s=it["src_start"] / it["src_fps"])
        it["src_end_s"] = it["src_start_s"] + length / float(tl_fps)
        ok.append(it)
    return ok, skipped


def ranges(items):
    """The merged source ranges [(a, b)] the items show, in seconds."""
    out = []
    for a, b in sorted((it["src_start_s"], it["src_end_s"]) for it in items):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def marker_for(row):
    """(color, name, note) for a review row."""
    kind = row["kind"]
    length = row["source_end"] - row["source_start"]
    name = (f"silence {length:.1f} s" if kind == "silence" else f"{kind}: {row['text']}")[:60]
    note = (f"trim-review: {row['suggestion']} ({row['confidence']} confidence). "
            f"{row['text']}. Source {row['source_start']:.2f} to {row['source_end']:.2f} s. "
            "Proposed only; nothing was cut.")
    return COLORS[kind], name, note


def plan(rows, items, tl_start, tl_fps, existing):
    """Markers for rows, as frames from the timeline's start. Each row
    lands once per item that shows its start. Returns (planned, refused):
    planned [{frame, color, name, note, duration, custom, row}], refused
    [{row, frame, reason}] for a frame that already holds a marker or that
    an earlier row takes."""
    planned, refused, taken = [], [], set(existing)
    for n, r in enumerate(rows, 1):
        for it in items:
            if not it["src_start_s"] <= r["source_start"] < it["src_end_s"]:
                continue
            at = it["start"] + cutlist.frame(r["source_start"] - it["src_start_s"], tl_fps)
            end = it["start"] + cutlist.frame(min(r["source_end"], it["src_end_s"]) -
                                              it["src_start_s"], tl_fps)
            frame = at - tl_start
            if frame in taken:
                refused.append({"row": r, "frame": frame, "reason":
                                "a marker is already at this frame" if frame in existing else
                                "an earlier row takes this frame"})
                continue
            taken.add(frame)
            color, name, note = marker_for(r)
            planned.append({"frame": frame, "color": color, "name": name, "note": note,
                            "duration": max(1, min(end, it["end"]) - at),
                            "custom": f"{CUSTOM}:{n}", "row": r})
    return planned, refused


def existing_frames(tl):
    """{frame: marker} of the timeline's markers, keys as ints."""
    got = api._safe_call(tl, "GetMarkers") or {}
    return {int(round(float(k))): dict(v) for k, v in got.items()}


def add(tl, planned, check=None):
    """AddMarker for each planned marker, reading each back with
    GetMarkers: colour, name, note, duration and custom data must be what
    was asked. Returns [{frame, name, returned, ok, problem}]."""
    results = []
    for m in planned:
        if check:
            check()
        returned = bool(tl.AddMarker(m["frame"], m["color"], m["name"], m["note"],
                                     m["duration"], m["custom"]))
        got = existing_frames(tl).get(m["frame"])
        problem = None
        if got is None:
            problem = f"no marker at frame {m['frame']} after AddMarker (returned {returned})"
        else:
            want = {"color": m["color"], "name": m["name"], "note": m["note"],
                    "duration": m["duration"], "customData": m["custom"]}
            bad = [k for k, v in want.items() if (_number(got.get(k)) != v if k == "duration"
                                                  else got.get(k) != v)]
            if bad:
                problem = "read back differs in " + ", ".join(bad)
        results.append({"frame": m["frame"], "name": m["name"], "returned": returned,
                        "ok": problem is None, "problem": problem})
    return results
