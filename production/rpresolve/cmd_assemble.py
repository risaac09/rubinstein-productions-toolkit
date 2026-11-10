"""
assemble: a method manifest (methods.to_manifest) as a new ' [auto]' timeline, built
with MediaPool.AppendToTimeline.

import_timeline reads FCPXML, which carries one frame rate for source and record, so it
cannot place a 30 or 60 fps clip on a 24 fps timeline. This command can: every span is
appended as a clip info whose source frames are counted at the clip's own rate
(resolve-traps.md T1).

Offline, the command prints the plan: each span's file, its in point and its length in
timeline frames, and the music. `apply_assemble`, the work behind the MCP tool, is the
guarded write. It makes one new timeline and nothing else that could be mistaken for
someone's work: the clips it needs are imported into a bin named 'methods [auto]' (made
when missing), the timeline takes the project's frame rate (T2) and gets its own size
and fill-and-crop scaling, a span's static scale becomes the item's zoom, and the
manifest's `music` {path, in} goes on A1 from the first frame, running on through the
method's end black and faded out over it. A dissolve is added with
TimelineItem.AddTransition (Cross Dissolve, centred on the cut) and read back; any other
transition is listed under not_built (the manifest's `effects` and `ui_steps` are other
tools' work).
Everything placed is read back.
"""

import json
import math
import os

from rpresolve import api, cli, cutlist
from rpresolve.workflows import AUTO, Refused, _check, plan_sha

NAME = "assemble"
HELP = "Offline: print the timeline a method manifest would be assembled into"

BIN = "methods [auto]"
SIZE = (3840, 2160)
MIN_FADE = 12          # frames of music fade when the method ends without black
TOUCH_S = 0.02         # two spans of one file this close in time are one continuous take


def _black_frames(manifest, fps):
    end = str((manifest.get("method") or {}).get("end") or "")
    try:
        return int(round(float(end.split(":")[1]) * fps)) if end.startswith("black:") else 0
    except (IndexError, ValueError):
        return 0


def plan(manifest):
    """The plan a manifest (a path or the dict) assembles into: {fps, spans: [{index,
    path, in, frames, scale, continues}], frames, black_frames, music, not_built}.
    Raises Refused on a manifest that does not validate or holds more than one clip."""
    if isinstance(manifest, str):
        try:
            with open(manifest, encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, ValueError) as e:
            raise Refused(f"cannot read the manifest: {e}")
    try:
        cutlist.validate_manifest(manifest, allow_no_speech=True)
    except cutlist.CutlistError as e:
        raise Refused(str(e))
    if len(manifest["clips"]) != 1:
        raise Refused("a method manifest holds one clip")
    fps = float(manifest["fps"])
    paths = {s["id"]: s["path"] for s in cutlist.sources(manifest)}
    spans, not_built, prev = [], [], None
    for i, span in enumerate(manifest["clips"][0]["spans"]):
        path = paths[span.get("source") or next(iter(paths))]
        n = cutlist.frame(span["out"], fps) - cutlist.frame(span["in"], fps)
        if n < 1:
            raise Refused(f"span {i} is shorter than a frame")
        row = {"index": i + 1, "path": path, "in": span["in"], "frames": n,
               "scale": span.get("scale"),
               "continues": bool(prev and prev[0] == path and abs(span["in"] - prev[1]) < TOUCH_S)}
        tr = span.get("transition")
        if tr and tr.get("type") == "dissolve" and i and int(tr.get("frames") or 0) > 0:
            row["transition"] = {"type": "Cross Dissolve", "frames": int(tr["frames"])}
        elif tr:
            not_built.append(f"the {tr.get('type')} into item {i + 1} "
                             f"({tr.get('frames')} frames): placed as a cut")
        spans.append(row)
        prev = (path, span["out"])
    music = manifest.get("music")
    if music is not None and not (isinstance(music, dict) and os.path.isabs(str(music.get("path")))):
        raise Refused("music is {path (absolute), in}")
    return {"fps": fps, "spans": spans, "frames": sum(r["frames"] for r in spans),
            "black_frames": _black_frames(manifest, fps),
            "music": {"path": music["path"], "in": float(music.get("in") or 0)} if music else None,
            "not_built": not_built}


def _clip_fps(clip, fallback):
    try:
        f = float(str(clip.GetClipProperty("FPS")).split()[0])
        return f if 1 < f < 1000 else fallback
    except (TypeError, ValueError, IndexError):
        return fallback


def _bin(media_pool, root, make):
    for f in root.GetSubFolderList() or []:
        if f.GetName() == BIN:
            return f
    return media_pool.AddSubFolder(root, BIN) if make else None


def apply_assemble(resolve, project_name, name, manifest, width=SIZE[0], height=SIZE[1],
                   dry_run=True, project_id=None, expect_sha=None, check_cancel=None):
    """Build the manifest as a new timeline `name` (must end ' [auto]'). Refused, writing
    nothing: a name that exists, a manifest whose fps is not the project's timeline rate
    (a scripted timeline takes the project's), a source or music file that is missing.
    Returns {project, timeline, rate, size, plan, plan_sha, dry_run, created, items,
    audio, problems, left_behind, ui_restore_problems, exit_status}; exit_status 1 when a
    read-back differs. A timeline that was made and is not right is left in place and
    named under left_behind: nothing is deleted."""
    from rpresolve import deliver
    if not name.endswith(AUTO):
        raise Refused(f"the timeline name must end with '{AUTO}' (got '{name}').")
    p = plan(manifest)
    missing = sorted({r["path"] for r in p["spans"] if not os.path.isfile(r["path"])}
                     | ({p["music"]["path"]} if p["music"]
                        and not os.path.isfile(p["music"]["path"]) else set()))
    if missing:
        raise Refused("not on this machine: " + ", ".join(os.path.basename(m) for m in missing))
    pm, project, pin = api.pin_for_write(resolve, project_name, project_id)
    media_pool, root = api.media_pool_root(project)
    count = int(project.GetTimelineCount() or 0)
    if any(project.GetTimelineByIndex(i).GetName() == name for i in range(1, count + 1)):
        raise Refused(f"a timeline named '{name}' already exists; nothing is overwritten.")
    rate = str(project.GetSetting("timelineFrameRate") or "")
    tl_fps = deliver.exact_fps(rate)
    if not tl_fps or abs(tl_fps - p["fps"]) > 0.01:
        raise Refused(f"the manifest runs at {p['fps']:g} fps and the project's timelines at "
                      f"'{rate}'; a scripted timeline takes the project's rate.")
    sha = plan_sha("assemble", pin.unique_id, name, rate, width, height, p)
    out = {"project": {"name": pin.name, "id": pin.unique_id}, "timeline": name, "rate": rate,
           "size": [width, height], "plan": p, "plan_sha": sha, "dry_run": dry_run,
           "created": None, "items": [], "transitions": [], "audio": None, "problems": [], "left_behind": None,
           "ui_restore_problems": [], "exit_status": 0}
    if dry_run:
        return out
    if expect_sha and expect_sha != sha:
        raise Refused("the plan changed since the dry run (the manifest, the name or the "
                      "project's frame rate differ); run the dry run again and review it")
    snap = api.UISnapshot(resolve, project)
    with snap:
        project = pin.check(pm)
        _build(out, project, media_pool, root, name, p, tl_fps, width, height,
               _check(pin, pm, check_cancel))
    out["ui_restore_problems"] = snap.problems
    if out["problems"] and out["created"]:
        out["left_behind"] = name
    out["exit_status"] = 1 if out["problems"] else 0
    return out


def _build(out, project, media_pool, root, name, p, tl_fps, width, height, check):
    folder = _bin(media_pool, root, make=True)
    if not folder or not media_pool.SetCurrentFolder(folder):
        raise api.WriteNotApplied(f"could not open the bin '{BIN}'")
    have = {c.GetClipProperty("File Path"): c for c in folder.GetClipList() or []}
    want = sorted({r["path"] for r in p["spans"]} | ({p["music"]["path"]} if p["music"] else set()))
    new = [x for x in want if x not in have]
    if new:
        for c in media_pool.ImportMedia(new) or []:
            have[c.GetClipProperty("File Path")] = c
    gone = [os.path.basename(x) for x in want if x not in have]
    if gone:
        raise api.WriteNotApplied("not imported: " + ", ".join(gone))
    tl = media_pool.CreateEmptyTimeline(name)
    if not tl or tl.GetName() != name:
        raise api.WriteNotApplied(f"CreateEmptyTimeline('{name}') failed")
    out["created"] = {"name": name, "unique_id": api._safe_call(tl, "GetUniqueId")}
    problems = out["problems"]
    try:
        for key, value in (("useCustomSettings", "1"), ("timelineResolutionWidth", str(width)),
                           ("timelineResolutionHeight", str(height)),
                           ("timelineInputResMismatchBehavior", "scaleToCrop")):
            if not tl.SetSetting(key, value) or str(tl.GetSetting(key)) != value:
                problems.append(f"timeline setting {key} reads '{tl.GetSetting(key)}', "
                                f"wanted '{value}'")
        if not project.SetCurrentTimeline(tl):
            raise api.WriteNotApplied(f"could not make '{name}' current to place clips on it")
        infos, prev_end = [], None
        for r in p["spans"]:
            sf = _clip_fps(have[r["path"]], tl_fps)
            start = prev_end if r["continues"] and prev_end is not None else int(round(r["in"] * sf))
            prev_end = start + int(math.ceil(r["frames"] * sf / tl_fps - 1e-6))  # T1
            infos.append({"mediaPoolItem": have[r["path"]], "startFrame": start,
                          "endFrame": prev_end, "mediaType": 1, "trackIndex": 1})
        check()
        media_pool.AppendToTimeline(infos)
        items = tl.GetItemListInTrack("video", 1) or []
        if len(items) != len(p["spans"]):
            problems.append(f"V1 holds {len(items)} item(s), the plan {len(p['spans'])}")
        for r, it, info in zip(p["spans"], items, infos):
            row = {"index": r["index"], "name": it.GetName(), "frames": it.GetDuration(),
                   "source_start": api._safe_call(it, "GetSourceStartFrame")}
            if row["frames"] != r["frames"]:
                problems.append(f"item {r['index']} is {row['frames']} frames, planned {r['frames']}")
            if r["scale"]:
                for k in ("ZoomX", "ZoomY"):
                    try:
                        row[k] = api.set_property_checked(it, k, r["scale"])
                    except api.WriteNotApplied as e:
                        problems.append(f"item {r['index']}: {e}")
            out["items"].append(row)
        for r in p["spans"]:  # centred on the cut, so it needs handles either side
            if r.get("transition") and len(items) >= r["index"]:
                want = r["transition"]["frames"]
                t = api._safe_call(items[r["index"] - 2], "AddTransition",
                                   {"type": r["transition"]["type"], "category": "simple",
                                    "position": "end", "alignment": "center", "duration": want})
                got = api._safe_call(t, "GetDuration") if t else None
                out["transitions"].append({"into": r["index"], "frames": got, "planned": want})
                if got != want:
                    problems.append(f"dissolve into item {r['index']}: AddTransition "
                                    f"{'failed (no handles?)' if t is None else f'made {got} frames, planned {want}'}")
        if p["music"]:
            check()
            out["audio"] = _music(tl, media_pool, have[p["music"]["path"]], p, tl_fps, problems)
    except api.ProjectChanged as e:
        problems.append(f"stopped, the open project changed: {e}")
    except api.ResolveAPIError as e:
        problems.append(f"{type(e).__name__}: {e}")


def _music(tl, media_pool, clip, p, tl_fps, problems):
    """The track on A1 from the first frame, as long as the picture plus the end black,
    faded out over the black (or MIN_FADE frames)."""
    total = p["frames"] + p["black_frames"]
    af = _clip_fps(clip, tl_fps)
    af = af if 20 < af < 70 else tl_fps  # an audio file's FPS property is often empty
    a0 = int(round(p["music"]["in"] * af))
    media_pool.AppendToTimeline([{"mediaPoolItem": clip, "startFrame": a0,
                                  "endFrame": a0 + int(math.ceil(total * af / tl_fps - 1e-6)),
                                  "mediaType": 2, "trackIndex": 1,
                                  "recordFrame": tl.GetStartFrame()}])
    items = tl.GetItemListInTrack("audio", 1) or []
    if len(items) != 1:
        problems.append(f"A1 holds {len(items)} item(s), expected the music alone")
        return None
    it = items[0]
    row = {"name": it.GetName(), "start": it.GetStart() - tl.GetStartFrame(),
           "frames": it.GetDuration(), "fade_out": None}
    if row["start"] != 0 or abs(row["frames"] - total) > 1:
        problems.append(f"the music sits at frame {row['start']} for {row['frames']} frames, "
                        f"planned 0 for {total}")
    fade = max(p["black_frames"], MIN_FADE)
    if api._safe_call(it, "SetFades", {"FadeOut": fade}):  # T18
        got = api._safe_call(it, "GetFades") or {}
        row["fade_out"] = got.get("FadeOut")
    if row["fade_out"] != fade:
        problems.append(f"the music's fade out reads {row['fade_out']}, wanted {fade} frames")
    return row


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------

def add_arguments(p):
    p.add_argument("manifest", help="a method manifest (methods.to_manifest), as JSON")


def run(args):
    try:
        p = plan(os.path.abspath(args.manifest))
    except Refused as e:
        print(f"assemble: {e}")
        return 2
    for r in p["spans"]:
        print(f"#{r['index']:<3} {r['frames']:>4} f  in {r['in']:9.3f} s"
              f"{'  scale ' + format(r['scale'], 'g') if r['scale'] else ''}"
              f"{'  (same take, continued)' if r['continues'] else ''}  {os.path.basename(r['path'])}")
    print(f"{len(p['spans'])} item(s), {p['frames']} frames at {p['fps']:g} fps, then "
          f"{p['black_frames']} of black" + (
              f"; music from {p['music']['in']:.2f} s" if p["music"] else "; no music"))
    for line in p["not_built"]:
        print("not built: " + line)
    return 0


if __name__ == "__main__":
    cli.script(__name__)
