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
manifest's `music` {path, in} is cut by ffmpeg into a bed (musicbed.py: the in point and
the length of the picture plus the end black, a one second fade out baked into a 24-bit
48 kHz WAV) and the bed goes on A1 from the first frame. SetFades is not used: it reads back
and does not render (resolve-traps.md T18). A dissolve is added with
TimelineItem.AddTransition (Cross Dissolve, centred on the cut) and read back; any other
transition is listed under not_built (the manifest's `effects` and `ui_steps` are other
tools' work).
Everything placed is read back.

Windows over the outgoing shot (fusion-macros.md, "Transitions"). A scale-window or
crop-window leaves the picture around the window transparent, and on one track that is
black. The operator's rule is that the outgoing shot keeps playing under the window, so a
span that carries one of those effects in the manifest's `effects` gets a plate: the previous span's clip, counted on from its out point for as many frames as the
window needs (windowplate.py: 37 for scale-window, 26 for crop-window, never more than the
span), placed on V1 at the span's first frame with the previous span's static scale. Higher
tracks draw on top, so the montage then goes on V2 with every span in the order and
numbering it always had, and V1 holds only the plates. A manifest without such a span is
built on V1 alone, as before; the result's `track` says which. A plate needs the outgoing
source to run on past its out point: the plan refuses when ffprobe finds less, and says
so in the dry run. The first span has no shot before it; its window opens over black and
the plan lists that under not_built. apply_effect, apply_grade, vertical_apply and
reframe_apply all take `track`: pass the result's `track` for the montage, and grade V1
too, since the plates are picture.

Colour (resolve-traps.md T30). A clip with no Input Color Space tag keeps Rec.709, so a log
file imported here would render in log. The dry run therefore runs `detect` on every source
and lists, per file, the input colour space detect found and what the clip reads when it is
already in the 'methods [auto]' bin. The real run does what `ingest` does for its own imports
and no more: a clip this run imports (a new clip, even when the same file sits in another bin)
and detect can pin gets Input Color Space (and Data Level) through the checked write, on a
colour-managed project only. A clip that was in the pool before the run is never written, by
clip id; when detect calls it log and it reads Rec.709 the run reports a problem. A file detect
cannot pin gets a note and no tag.
"""

import json
import math
import os

from rpresolve import api, cli, cutlist, ledgercheck
from rpresolve import colormgmt as cm
from rpresolve import detect as rpdetect
from rpresolve import ingest as rpingest
from rpresolve import delivercheck as dc
from rpresolve import musicbed, transitions
from rpresolve import squeeze as rpsqueeze
from rpresolve import paths as rppaths
from rpresolve import windowplate
from rpresolve.workflows import AUTO, Refused, _check, check_exclude, plan_sha

NAME = "assemble"
HELP = "Offline: print the timeline a method manifest would be assembled into"

BIN = "methods [auto]"
SIZE = (3840, 2160)
TOUCH_S = 0.02         # two spans of one file this close in time are one continuous take
# detect's spaces that are display video already; any other pinned space needs its input tag
DISPLAY_SPACES = {rpdetect.CS_REC709, rpdetect.CS_CAMERA_SDR, rpdetect.CS_SRGB}


def _black_frames(manifest, fps):
    end = str((manifest.get("method") or {}).get("end") or "")
    try:
        return int(round(float(end.split(":")[1]) * fps)) if end.startswith("black:") else 0
    except (IndexError, ValueError):
        return 0


def plan(manifest, exclude=None):
    """The plan a manifest (a path or the dict) assembles into: {fps, spans: [{index,
    path, in, frames, scale, continues, window?, plate?}], frames, black_frames, music,
    track, not_built}. A span with a scale-window or crop-window after the first has
    `window` (the effect) and `plate` (frames of the outgoing shot it needs); `track` is
    the video track the montage goes on, 2 when any span has a plate, else 1.
    `exclude` (absolute path) is the operator's exclude list; `exclude` in the plan is its
    path, takes and hash, with `matches` empty (a match is refused), or None with
    `exclude_note` saying nothing was checked.
    Raises Refused on a manifest that does not validate or holds more than one clip, or
    names an excluded take (a span's source or the music)."""
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
    ex = check_exclude(
        exclude,
        lambda takes: [f"span {i + 1} {os.path.basename(src)} ({take})"
                       for i, src, take in cutlist.exclude_matches(manifest, takes)]
        + [f"music {os.path.basename(src)} ({take})"
           for src, take in cutlist.exclude_music_matches(manifest, takes)],
        "the manifest")
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
        win = windowplate.window_effect(manifest.get("effects"), i)
        if win and i:
            row["window"], row["plate"] = win, windowplate.plate_frames(win, n)
        elif win:
            not_built.append(f"the {win} on item 1: no shot before it to open over, so it "
                             "opens over black")
        tr = span.get("transition")
        if tr and tr.get("type") == "dissolve" and i and int(tr.get("frames") or 0) > 0:
            row["transition"] = {"type": "Cross Dissolve", "frames": int(tr["frames"])}
        elif tr:
            not_built.append(f"the {tr.get('type')} into item {i + 1} "
                             f"({tr.get('frames')} frames): placed as a cut")
        spans.append(row)
        prev = (path, span["out"])
    not_built += [f"the {e['effect']} into item {e['span_index'] + 1}: not built by this tool "
                  "(the draft farm draws it)" for e in manifest.get("effects") or []
                  if e.get("effect") in transitions.IDS]
    try:  # the draft farm desqueezes by this record; say so where the build does not
        wide = {p: rpsqueeze.answer(p)[0] for p in sorted(set(paths.values()))}
    except rpsqueeze.SqueezeError as e:
        raise Refused(str(e))
    not_built += [f"{os.path.basename(p)}: recorded as {f:g}x anamorphic; this tool sets neither the "
                  "clip's pixel aspect ratio nor the timeline's shape" for p, f in wide.items()
                  if f and f != 1 and not p.lower().endswith(".braw")]  # Resolve reads a BRAW's own tag
    music = manifest.get("music")
    if music is not None and not (isinstance(music, dict) and os.path.isabs(str(music.get("path")))):
        raise Refused("music is {path (absolute), in}")
    return {"fps": fps, "spans": spans, "frames": sum(r["frames"] for r in spans),
            "black_frames": _black_frames(manifest, fps),
            "music": {"path": music["path"], "in": float(music.get("in") or 0)} if music else None,
            "track": 2 if any(r.get("plate") for r in spans) else 1,
            "sound": bool(manifest.get("camera_sound")),
            "not_built": not_built, "exclude": ex,
            "exclude_note": cutlist.exclude_note(ex)}


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


def _all_clips(root):
    """Every clip in the media pool, all bins."""
    stack = [root]
    while stack:
        folder = stack.pop()
        yield from folder.GetClipList() or []
        stack.extend(folder.GetSubFolderList() or [])


def _pool_paths(root):
    """The normalised File Path of every clip in the media pool, all bins."""
    return {rpingest.norm_path(p) for p in (c.GetClipProperty("File Path") for c in _all_clips(root))
            if p}


def _wrong(detected, current):
    """A problem line when detect pins a space that is not display video (log, HLG) and the
    clip reads Rec.709: the picture would render unconverted."""
    if (detected and detected not in DISPLAY_SPACES and detected != rpdetect.CS_CAMERA_RAW
            and str(current or "").startswith("Rec.709")):
        return (f"detect says '{detected}' and the clip reads '{current}'; it was in the pool "
                f"before this run, so it is left as it is. Set its Input Color Space in "
                f"Resolve or it renders without its input transform (T30)")
    return ""


def colour_plan(project, root, p):
    """Read-only: {managed, science, sources, notes}. One source row per picture file, in
    span order: {path, camera, profile, detected, data_level, in_pool, current, action,
    flag, note}. detected is detect's input colour space ('' when it cannot pin the file),
    in_pool whether the file is a clip anywhere in the pool, current what the clip the
    timeline will use reads (None when this run will import it). The build reuses a clip in
    the 'methods [auto]' bin and imports any other file as a new clip, so action is 'left'
    (in that bin before this run: never written), 'tag' (a new import detect can pin: the
    build writes it, even when the file also sits in another bin) or 'none' (nothing to
    write; the note says why)."""
    science = str(project.GetSetting("colorScienceMode") or "")
    managed = science.startswith("davinciYRGBColorManaged")
    paths = list(dict.fromkeys(r["path"] for r in p["spans"]))
    notes, err = [], ""
    try:
        rows, _ = rpdetect.detect_paths(paths)
    except rpdetect.DetectError as e:
        rows, err = [], f"detect did not run: {e}"
        notes.append(err)
    folder = _bin(None, root, make=False)  # the build's own test: a File Path in this bin is reused
    in_bin = {c.GetClipProperty("File Path"): c for c in folder.GetClipList() or []} if folder else {}
    pool = _pool_paths(root)
    det = {rpingest.norm_path(r["path"]): r for r in rows}
    ent = {rpingest.norm_path(e["path"]): e
           for e in rpingest.plan_ingest(rows, list(in_bin), parent=BIN)}
    sources = []
    for path in paths:
        key = rpingest.norm_path(path)
        d, e, clip = det.get(key), ent.get(key), in_bin.get(path)
        row = {"path": path, "camera": (d or {}).get("camera", ""),
               "profile": (d or {}).get("profile", ""),
               "detected": (d or {}).get("input_color_space", ""),
               "data_level": rpingest.DATA_LEVELS.get((d or {}).get("data_level", ""), ""),
               "in_pool": key in pool,
               "current": str(clip.GetClipProperty("Input Color Space") or "") if clip else None,
               "action": "none", "flag": "", "note": ""}
        if clip is not None:
            row["action"] = "left"
            row["flag"] = _wrong(row["detected"], row["current"]) if managed else ""
        elif e is None:
            row["note"] = err or "detect gave no row for this file; not tagged"
        elif not managed:
            row["note"] = "project is not colour managed; nothing is written"
        elif e["action"] == rpingest.TAG:
            row["action"] = "tag"
            if row["in_pool"]:
                row["note"] = ("also in another bin; this run imports its own copy and tags "
                               "that one, the existing clip is not touched")
        else:
            row["note"] = e["reason"] + "; not tagged"
        sources.append(row)
    if not managed:
        notes.append(f"colorScienceMode is '{science}': Input Color Space tags only take effect "
                     "under DaVinci YRGB Color Managed, so none is written")
    spaces = sorted({r["detected"] for r in sources if r["detected"]})
    if len(spaces) > 1:
        notes.append("the sources carry more than one input colour space ("
                     + "; ".join(spaces) + "): one edit uses one camera profile")
    notes.extend(f"{os.path.basename(r['path'])}: {r['note']}" for r in sources
                 if r["note"] and managed and not err)
    return {"managed": managed, "science": science, "sources": sources, "notes": notes}


def _video_seconds(path):
    """The picture's length in seconds (its video stream, else the container); None when
    ffprobe is missing or reads no length (a BRAW file, say)."""
    try:
        dc.check_tools(ffmpeg=False)
        info = dc.probe(path)
    except (dc.ToolMissing, dc.CheckError):
        return None
    streams = [x for x in info.get("streams") or [] if x.get("codec_type") == "video"]
    for d in [x.get("duration") for x in streams] + [(info.get("format") or {}).get("duration")]:
        try:
            if float(d) > 0:
                return float(d)
        except (TypeError, ValueError):
            pass
    return None


def plate_handles(p):
    """The plates the plan puts on V1, one row each: {into, window, frames, record, source,
    handle_s}. record is the frame (from the timeline's start) where the span and its plate
    begin; handle_s the seconds of the outgoing source after its out point, None when
    ffprobe cannot read the file (the read-back then catches a short plate). Raises Refused
    when a source ends inside its plate, the way the draft farm does."""
    rows, record = [], 0
    for prev, r in zip([None] + p["spans"], p["spans"]):
        if r.get("plate"):
            have = _video_seconds(prev["path"])
            row = {"into": r["index"], "window": r["window"], "frames": r["plate"],
                   "record": record, "source": os.path.basename(prev["path"]),
                   "handle_s": None}
            if have is not None:
                row["handle_s"] = round(have - (prev["in"] + prev["frames"] / p["fps"]), 3)
                if row["handle_s"] < r["plate"] / p["fps"] - 0.5 / p["fps"]:
                    raise Refused(
                        f"item {r['index']}: the {r['window']} opens over item {prev['index']} "
                        f"playing on for {r['plate']} frames, and {row['source']} has "
                        f"{max(row['handle_s'], 0):.2f} s after its out point "
                        f"({r['plate'] / p['fps']:.2f} s needed). Pick a later out point or "
                        "a longer take, or another effect.")
            rows.append(row)
        record += r["frames"]
    return rows


def bed_plan(p, manifest, bed_dir, tl_fps):
    """The music bed this plan places on A1 (musicbed.spec), or None without music. The bed
    goes in bed_dir, else beside the manifest file; refused when neither is known, when
    the folder sits inside a git working tree (the bed is client audio), or when the music
    runs out before the bed does (ffprobe, so the dry run says it, not only the real run)."""
    if not p["music"]:
        return None
    if bed_dir is None and isinstance(manifest, str):
        bed_dir = os.path.dirname(os.path.abspath(manifest))
    if not bed_dir or not os.path.isabs(bed_dir):
        raise Refused("the music is baked into a bed file: give bed_dir, an absolute folder "
                      "(the manifest has no file path to put it beside)")
    if rppaths.in_any_git_tree(bed_dir):
        raise Refused(f"bed_dir {bed_dir} is inside a git working tree. The bed is cut from "
                      "the client's music; write it outside any repository.")
    try:
        bed = musicbed.spec(p["music"]["path"], p["music"]["in"],
                            round((p["frames"] + p["black_frames"]) / tl_fps, 4), bed_dir)
        if not os.path.isfile(bed["path"]):  # a bed already cut was long enough
            musicbed.check_source(bed["src"], bed["in"], bed["seconds"])
        return bed
    except musicbed.BedError as e:
        raise Refused(str(e))


def _bake(bed):
    """Make the bed file unless the same one (same name) is already there; 'baked' or
    'reused'. A failure is a refusal: nothing has gone into Resolve yet."""
    if os.path.isfile(bed["path"]):
        return "reused"
    try:
        musicbed.bake(bed["src"], bed["in"], bed["seconds"], bed["fade_s"], bed["path"])
    except musicbed.BedError as e:
        raise Refused(f"the music bed was not made, nothing was written to Resolve: {e}")
    return "baked"


def apply_assemble(resolve, project_name, name, manifest, width=SIZE[0], height=SIZE[1],
                   dry_run=True, project_id=None, expect_sha=None, check_cancel=None,
                   bed_dir=None, exclude=None, ledger=None, draft=None, override=None):
    """Build the manifest as a new timeline `name` (must end ' [auto]'). Refused, writing
    nothing: a name that exists, a manifest whose fps is not the project's timeline rate
    (a scripted timeline takes the project's), a source or music file that is missing, a
    music bed that cannot be made or has no safe folder (bed_dir, see bed_plan), a window
    span whose outgoing source ends inside its plate (plate_handles).
    Returns {project, timeline, rate, size, track, plan, colour, bed, plates, plan_sha,
    dry_run, created, items, audio, problems, left_behind, ui_restore_problems,
    exit_status}; track is the video track the montage is on (2 when plates are on V1),
    plates the plate rows (plate_handles, each with `placed` after a real run); bed is the music
    bed's spec and its result ('would bake', 'would reuse', then 'baked' or 'reused'), and
    the plan hash holds the spec; colour is colour_plan's rows (shown by the dry run,
    completed by the real one); exit_status 1 when a read-back
    differs or a clip that was in the pool is log by detect and reads Rec.709. A timeline that was made and is not right is left in place and
    named under left_behind: nothing is deleted. ledger (the absolute path of a drafts.json)
    and draft (the picked plan) check the manifest's effects against what the draft drew
    (ledgercheck): the dry run flags each one marked not_drawn, a real run refuses unless
    override names it, and a ledger that is not this manifest's is refused; `ledger` in the
    result is the report, and it is part of the plan hash."""
    from rpresolve import deliver
    if not name.endswith(AUTO):
        raise Refused(f"the timeline name must end with '{AUTO}' (got '{name}').")
    p = plan(manifest, exclude)
    led = ledger and ledgercheck.load(ledger, draft)
    report = ledgercheck.check(led, ledgercheck.must_match(led, manifest), override)
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
    colour = colour_plan(project, root, p)
    tl_colour = {k: str(project.GetSetting(k) or "") for k in cm.TIMELINE_COLOUR_KEYS}
    bed = bed_plan(p, manifest, bed_dir, tl_fps)
    plates = plate_handles(p)
    sha = plan_sha("assemble", pin.unique_id, name, rate, width, height, p, colour, bed, plates,
                   report, tl_colour)
    out = {"project": {"name": pin.name, "id": pin.unique_id}, "timeline": name, "rate": rate,
           "size": [width, height], "track": p["track"], "plan": p, "colour": colour,
           "timeline_colour": tl_colour, "plates": plates, "exclude": p["exclude"], "exclude_note": p["exclude_note"],
           "bed": bed and {**bed, "result": "would reuse" if os.path.isfile(bed["path"])
                           else "would bake"},
           "plan_sha": sha, "ledger": report,
           "dry_run": dry_run,
           "created": None, "items": [], "transitions": [], "audio": None, "camera_audio": [], "problems": [], "left_behind": None,
           "ui_restore_problems": [], "exit_status": 0}
    if dry_run:
        return out
    ledgercheck.refuse_unless_overridden(report)
    if expect_sha and expect_sha != sha:
        raise Refused("the plan changed since the dry run (the manifest, the name, the "
                      "project's frame rate or a source's colour differ); run the dry run "
                      "again and review it")
    if bed:
        out["bed"]["result"] = _bake(bed)
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
    music = out["bed"]["path"] if p["music"] else None  # the baked bed, never the original
    want = sorted({r["path"] for r in p["spans"]} | ({music} if music else set()))
    new = [x for x in want if x not in have]
    imported = set()
    before = {c.GetUniqueId() for c in _all_clips(root)} if new else set()
    if new:
        for c in media_pool.ImportMedia(new) or []:
            have[c.GetClipProperty("File Path")] = c
            imported.add(c.GetClipProperty("File Path"))
    gone = [os.path.basename(x) for x in want if x not in have]
    if gone:
        raise api.WriteNotApplied("not imported: " + ", ".join(gone))
    check()
    _colour(out, have, imported, before)
    tl = media_pool.CreateEmptyTimeline(name)
    if not tl or tl.GetName() != name:
        raise api.WriteNotApplied(f"CreateEmptyTimeline('{name}') failed")
    out["created"] = {"name": name, "unique_id": api._safe_call(tl, "GetUniqueId")}
    problems = out["problems"]
    try:
        # a timeline with custom settings holds its own colour spaces, so the project's are
        # copied across; a project that reads none of them has nothing to copy
        colour = [(k, v) for k, v in out["timeline_colour"].items() if v]
        for key, value in (("useCustomSettings", "1"), ("timelineResolutionWidth", str(width)),
                           ("timelineResolutionHeight", str(height)),
                           ("timelineInputResMismatchBehavior", "scaleToCrop"), *colour):
            if not tl.SetSetting(key, value) or str(tl.GetSetting(key)) != value:
                problems.append(f"timeline setting {key} reads '{tl.GetSetting(key)}', "
                                f"wanted '{value}'")
        if not project.SetCurrentTimeline(tl):
            raise api.WriteNotApplied(f"could not make '{name}' current to place clips on it")
        track = p["track"]
        if track > 1 and not (tl.AddTrack("video")
                              and int(tl.GetTrackCount("video") or 0) >= track):
            raise api.WriteNotApplied(f"could not add V{track} to '{name}', which the plates "
                                      "under the windows need")
        infos, ends, prev_end, record = [], [], None, int(tl.GetStartFrame())
        for r in p["spans"]:
            sf = _clip_fps(have[r["path"]], tl_fps)
            start = prev_end if r["continues"] and prev_end is not None else int(round(r["in"] * sf))
            prev_end = start + int(math.ceil(r["frames"] * sf / tl_fps - 1e-6))  # T1
            infos.append({"mediaPoolItem": have[r["path"]], "startFrame": start,
                          "endFrame": prev_end, "mediaType": 1, "trackIndex": track})
            if track > 1:  # a track above V1 is placed by frame, not appended
                infos[-1]["recordFrame"] = record
            ends.append((prev_end, sf))
            record += r["frames"]
        check()
        media_pool.AppendToTimeline(infos)
        items = tl.GetItemListInTrack("video", track) or []
        if len(items) != len(p["spans"]):
            problems.append(f"V{track} holds {len(items)} item(s), the plan {len(p['spans'])}")
        for r, it, info in zip(p["spans"], items, infos):
            row = {"index": r["index"], "name": it.GetName(), "frames": it.GetDuration(),
                   "source_start": api._safe_call(it, "GetSourceStartFrame")}
            if row["frames"] != r["frames"]:
                problems.append(f"item {r['index']} is {row['frames']} frames, planned {r['frames']}")
            if track > 1 and it.GetStart() != info["recordFrame"]:
                problems.append(f"item {r['index']} sits at frame {it.GetStart() - tl.GetStartFrame()}, "
                                f"planned {info['recordFrame'] - tl.GetStartFrame()}")
            if r["scale"]:
                for k in ("ZoomX", "ZoomY"):
                    try:
                        row[k] = api.set_property_checked(it, k, r["scale"])
                    except api.WriteNotApplied as e:
                        problems.append(f"item {r['index']}: {e}")
            out["items"].append(row)
        if p["sound"]:
            check()
            out["camera_audio"] = _camera_audio(tl, media_pool, have, p, infos, items, tl_fps,
                                                problems)
        _plates(out, tl, media_pool, have, p, ends, tl_fps, check)
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
            out["audio"] = _music(tl, media_pool, have[music], p, tl_fps, problems,
                                  track=2 if p["sound"] else 1)
    except api.ProjectChanged as e:
        problems.append(f"stopped, the open project changed: {e}")
    except api.ResolveAPIError as e:
        problems.append(f"{type(e).__name__}: {e}")


def _plates(out, tl, media_pool, have, p, ends, tl_fps, check):
    """The plates on V1: each window span's previous clip, from where that span's source
    stopped, at the window span's first frame, with the previous span's static scale. Read
    back: how many, where, how long, from which source frame."""
    problems, first = out["problems"], int(tl.GetStartFrame())
    infos = []
    for row in out["plates"]:
        k = row["into"] - 1
        prev_end, sf = ends[k - 1]
        infos.append({"mediaPoolItem": have[p["spans"][k - 1]["path"]], "startFrame": prev_end,
                      "endFrame": prev_end + int(math.ceil(row["frames"] * sf / tl_fps - 1e-6)),
                      "mediaType": 1, "trackIndex": 1, "recordFrame": first + row["record"]})
    if not infos:
        return
    check()
    media_pool.AppendToTimeline(infos)
    got = tl.GetItemListInTrack("video", 1) or []
    if len(got) != len(infos):
        problems.append(f"V1 holds {len(got)} plate(s), the plan {len(infos)}")
    for row, it, info in zip(out["plates"], got, infos):
        scale = p["spans"][row["into"] - 2]["scale"]
        row["placed"] = {"name": it.GetName(), "start": it.GetStart() - first,
                         "frames": it.GetDuration(),
                         "source_start": api._safe_call(it, "GetSourceStartFrame")}
        if (row["placed"]["start"], row["placed"]["frames"]) != (row["record"], row["frames"]):
            problems.append(f"the plate under item {row['into']} sits at frame "
                            f"{row['placed']['start']} for {row['placed']['frames']} frames, "
                            f"planned {row['record']} for {row['frames']}; the outgoing "
                            "source may be too short")
        # the plate is the outgoing shot counted on from its out point: a plate that starts
        # anywhere else (a held frame, the span's own footage) is the wrong picture
        got_src = row["placed"]["source_start"]
        if got_src is not None and got_src != info["startFrame"]:
            problems.append(f"the plate under item {row['into']} starts at source frame "
                            f"{got_src}, planned {info['startFrame']} (the outgoing shot's out point)")
        clip_of = api._safe_call(it, "GetMediaPoolItem")
        if clip_of is not None and api._safe_call(clip_of, "GetUniqueId") != \
                api._safe_call(info["mediaPoolItem"], "GetUniqueId"):
            problems.append(f"the plate under item {row['into']} is {it.GetName()}, not the "
                            "outgoing shot")
        if scale:
            for key in ("ZoomX", "ZoomY"):
                try:
                    api.set_property_checked(it, key, scale)
                except api.WriteNotApplied as e:
                    problems.append(f"the plate under item {row['into']}: {e}")


def _colour(out, have, imported, before):
    """Before the timeline exists: tag what this run imported and detect pinned, through the
    checked write ingest uses (Input Color Space, then Data Level), and report any clip that
    was already in the pool. A clip is written only when ImportMedia returned it in this run
    and its id is not in `before`, the ids in the pool ahead of the import; a row planned
    'tag' whose clip turns out to have been there is read and reported like any other."""
    problems = out["problems"]
    for row in out["colour"]["sources"]:
        clip, base = have[row["path"]], os.path.basename(row["path"])
        if row["action"] == "tag" and row["path"] in imported and clip.GetUniqueId() not in before:
            try:
                row["current"] = api.set_clip_property_checked(
                    clip, "Input Color Space", row["detected"])
                if row["data_level"]:
                    api.set_clip_property_checked(clip, "Data Level", row["data_level"])
                row["result"] = "tagged"
            except api.WriteNotApplied as e:
                row["result"] = f"FAILED: {e}"
                problems.append(f"colour: {base}: {e}")
        elif row["action"] in ("tag", "left"):
            if row["action"] == "tag":
                row["result"] = "not written: the clip was already in the pool"
            row["current"] = str(clip.GetClipProperty("Input Color Space") or "")
            row["flag"] = _wrong(row["detected"], row["current"]) if out["colour"]["managed"] else ""
            if row["flag"]:
                problems.append(f"colour: {base}: {row['flag']}")


def _camera_audio(tl, media_pool, have, p, infos, items, tl_fps, problems):
    """Each span's own camera sound on A1 under its picture: the same clip, the same source
    frames and the same record frame as the picture item (read from the item, so a montage
    on V2 and a plate-free V1 line up alike). Read back: how many, where, how long. A source
    with no audio places nothing and the count says so."""
    first = int(tl.GetStartFrame())
    audio = [{"mediaPoolItem": i["mediaPoolItem"], "startFrame": i["startFrame"],
              "endFrame": i["endFrame"], "mediaType": 2, "trackIndex": 1,
              "recordFrame": it.GetStart()} for i, it in zip(infos, items)]
    media_pool.AppendToTimeline(audio)
    got = tl.GetItemListInTrack("audio", 1) or []
    if len(got) != len(audio):
        problems.append(f"A1 holds {len(got)} camera sound item(s), the plan {len(audio)} "
                        "(a source with no audio places none)")
    rows = []
    for r, it, pic in zip(p["spans"], got, items):
        row = {"index": r["index"], "name": it.GetName(), "start": it.GetStart() - first,
               "frames": it.GetDuration()}
        if (it.GetStart(), it.GetDuration()) != (pic.GetStart(), pic.GetDuration()):
            problems.append(f"camera sound {r['index']} sits at frame {row['start']} for "
                            f"{row['frames']} frames, its picture at {pic.GetStart() - first} "
                            f"for {pic.GetDuration()}")
        rows.append(row)
    return rows


def _music(tl, media_pool, clip, p, tl_fps, problems, track=1):
    """The baked bed on A<track> (A1, or A2 under camera sound) from the first frame, as long
    as the picture plus the end black.
    Its in point and fade are in the file, so there is no SetFades here (T18)."""
    total = p["frames"] + p["black_frames"]
    af = _clip_fps(clip, tl_fps)
    af = af if 20 < af < 70 else tl_fps  # an audio file's FPS property is often empty
    if track > 1 and not (tl.AddTrack("audio") and int(tl.GetTrackCount("audio") or 0) >= track):
        problems.append(f"could not add A{track} for the music under the camera sound")
        return None
    media_pool.AppendToTimeline([{"mediaPoolItem": clip, "startFrame": 0,
                                  "endFrame": int(math.ceil(total * af / tl_fps - 1e-6)),
                                  "mediaType": 2, "trackIndex": track,
                                  "recordFrame": tl.GetStartFrame()}])
    items = tl.GetItemListInTrack("audio", track) or []
    if len(items) != 1:
        problems.append(f"A{track} holds {len(items)} item(s), expected the music alone")
        return None
    it = items[0]
    row = {"name": it.GetName(), "start": it.GetStart() - tl.GetStartFrame(),
           "frames": it.GetDuration()}
    if row["start"] != 0 or abs(row["frames"] - total) > 1:
        problems.append(f"the music sits at frame {row['start']} for {row['frames']} frames, "
                        f"planned 0 for {total}")
    return row


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------

def add_arguments(p):
    p.add_argument("manifest", help="a method manifest (methods.to_manifest), as JSON")
    p.add_argument("--exclude", help="the operator's exclude list (JSON with `takes`); a "
                   "manifest naming an excluded take is refused")


def run(args):
    try:
        p = plan(os.path.abspath(args.manifest),
                 args.exclude and os.path.abspath(args.exclude))
    except Refused as e:
        print(f"assemble: {e}")
        return 2
    for r in p["spans"]:
        win = f"  {r['window']}, {r['plate']} f of #{r['index'] - 1} playing on" if r.get("plate") else ""
        print(f"#{r['index']:<3} {r['frames']:>4} f  in {r['in']:9.3f} s"
              f"{'  scale ' + format(r['scale'], 'g') if r['scale'] else ''}"
              f"{'  (same take, continued)' if r['continues'] else ''}{win}  "
              f"{os.path.basename(r['path'])}")
    if p["track"] > 1:
        print(f"the montage goes on V{p['track']}; V1 holds a plate under each window span")
    print(f"{len(p['spans'])} item(s), {p['frames']} frames at {p['fps']:g} fps, then "
          f"{p['black_frames']} of black" + (
              f"; music from {p['music']['in']:.2f} s, baked with a "
              f"{musicbed.fade_for((p['frames'] + p['black_frames']) / p['fps']):g} s fade out"
              if p["music"] else "; no music"))
    for line in p["not_built"]:
        print("not built: " + line)
    if p["exclude"]:
        print(f"exclude list {p['exclude']['path']}: {len(p['exclude']['takes'])} take(s), none in "
              "the manifest or its music" + (f"; {p['exclude']['not_checked']}" if p["exclude"]["not_checked"] else ""))
    else:
        print(p["exclude_note"])
    return 0


if __name__ == "__main__":
    cli.script(__name__)
