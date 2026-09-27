#!/usr/bin/env python3
"""
resolve_workflow.py — Comprehensive CLI workflow tool for DaVinci Resolve.

Requires DaVinci Resolve Studio to be running.
Connects via the DaVinci Resolve Scripting API.

Usage:
    python3 resolve_workflow.py <command> [options]

    Imports the sibling rpresolve/ package, so run it from production/ or
    through a symlink; a copy of this file alone will not start.

Commands:
    new-project         Create a new project with standard bin structure
    import-media        Import media files into camera-specific bins (recursive)
    build-timeline       Create timeline with optional intro/outro cards
    add-subtitles        Import .srt subtitles onto timeline
    auto-subtitle        Generate subtitles from audio (Resolve Studio only)
    render               Queue a render preset
    render-all            Queue all render presets at once
    clear-queue           Delete all pending render jobs
    apply-lut             Apply a LUT to clips on a track (optionally filtered by --camera)
    apply-drx             Apply a .drx grade to clips on a track (optionally filtered by --camera)
    list-projects         List all projects in current database
    list-timelines        List all timelines in current project
    list-render-formats    List available render formats and codecs
    info                   Show current project/timeline info
    open-page              Switch Resolve to a specific page
    export-project         Export current project as .drp file
    survey                 Read-only survey of every project on disk (offline;
                           runs resolve_survey.py, no Resolve connection)
    detect                 Offline: classify camera and picture profile from
                           file headers (ffprobe, exiftool); never touches Resolve
    ingest                 Import classified media into camera bins and tag each
                           clip's input color space (RCM projects you name)
    measure                Offline: luma, clipping, legal range and skin numbers
                           from a render; camera-match numbers per segment
    manifest               Offline: build a cut manifest from a CUTS script, a
                           vertcut TSV or a video-use edl.json
    endcheck               Offline: check every cut's end word, that it sits in
                           a pause of the audio, and that it says only what the
                           approved text says
    selects                Offline: propose spans that sit inside the approved text
    cut                    Build [auto] 16:9 and 9:16 timelines from a manifest

Config:
    Camera bins, clip-color tags, and render presets are loaded from
    resolve-config.json (next to this script, or --config <path>). If the
    file is missing, built-in generic defaults are used. Copy and edit the
    config for your own camera names and delivery presets — don't edit the
    Python for that.

Environment Setup (macOS):
    export RESOLVE_SCRIPT_API="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
    export RESOLVE_SCRIPT_LIB="/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
    export PYTHONPATH="$PYTHONPATH:$RESOLVE_SCRIPT_API/Modules/"

Known API limits (live-verified against Resolve Studio 21.0.4.5):
    - Timeline.ApplyGradeFromDRX(path, mode, items) does not exist in 21.0.4;
      the scripting README example for it is stale. The working call is per
      item: item.GetNodeGraph().ApplyGradeFromDRX(path, gradeMode) -> Bool,
      gradeMode 0 = no keyframes, 1 = source timecode aligned, 2 = start
      frames aligned. It very likely replaces the item's node graph.
    - Grades go through the Graph object from item.GetNodeGraph():
      GetNumNodes(), GetNodeLabel(i), GetLUT(i), SetLUT(i, path),
      GetToolsInNode(i), SetNodeEnabled(i, bool). Node indexes are 1-based.
      TimelineItem.GetNumNodes/SetLUT/GetLUT are deprecated aliases.
    - SetLUT only accepts a LUT Resolve has scanned (Project.RefreshLUTList()
      rescans), and GetLUT may report it relative to a LUT folder. apply-lut
      reads each LUT back and reports a mismatch as FAIL.
    - GetToolsInNode(i) returns None for a node whose corrections are all at
      default; it is reliable only for OFX nodes.
    - The scripting API cannot create color-page nodes. apply-lut targeting
      a node index beyond what the clip already has fails with an explicit
      error. Add the node in the Color page first (or apply a .drx that
      already contains it).
    - Unknown resolve.CONSTANT names return None silently.
    - The Python client can segfault on interpreter shutdown after some
      calls, and piped output is lost unless flushed first. main() exits
      through rpresolve.api.exit_clean (flush, then os._exit).
    - Subtitle *styling* (font/color/position) has no scripting entry point;
      add-subtitles places the track, styling stays a manual Edit-page step.
    - The "story" vertical preset resizes the canvas only. It does not
      reframe subjects — do that per-clip before rendering vertical.
"""

import sys
import os
import json
import argparse
import subprocess
import time
from pathlib import Path

# rpresolve is a sibling package; put this file's real directory on the
# path so a symlinked or differently-cwd'd run still finds it. A lone copy
# of this file elsewhere (e.g. Resolve's Scripts menu) still needs the
# rpresolve/ directory copied next to it.
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
try:
    from rpresolve import api as rpapi  # noqa: E402
    from rpresolve import detect as rpdetect  # noqa: E402
    from rpresolve import ingest as rpingest  # noqa: E402
    from rpresolve import config as rpconfig  # noqa: E402
    from rpresolve import paths as rppaths  # noqa: E402
    from rpresolve import render as rprender  # noqa: E402
    from rpresolve.config import CONFIG_PATH_DEFAULT, DEFAULT_CONFIG  # noqa: E402,F401
    from rpresolve.grade import (  # noqa: E402,F401  (re-exported for callers and tests)
        _path_under, apply_drx_to_item, apply_drx_to_items, apply_lut_to_item,
        apply_lut_to_items, graph_fingerprint, lut_paths_match)
    from rpresolve.render import pick_codec  # noqa: E402,F401
    from rpresolve import workflows as rpwork  # noqa: E402
except ImportError as e:
    sys.exit(f"ERROR: cannot import rpresolve ({e}). Keep resolve_workflow.py next to its "
             "rpresolve/ directory (production/ in the toolkit), or symlink the script.")

# The survey decodes zstd blobs, which needs compression.zstd (Python 3.14+).
# This script runs on the system python3 (3.9), so survey runs the sibling
# resolve_survey.py as a subprocess under this interpreter instead.
SURVEY_PYTHON = rpwork.SURVEY_PYTHON
SURVEY_SCRIPT = rpwork.SURVEY_SCRIPT

VALID_FRAMERATES = {"23.976", "24", "25", "29.97", "30", "50", "59.94", "60"}
MEDIA_EXTENSIONS = {
    '.mov', '.mp4', '.mxf', '.avi', '.mkv', '.m4v',
    '.braw', '.r3d', '.dpx', '.exr', '.tif', '.tiff',
    '.wav', '.aif', '.aiff', '.mp3', '.aac', '.flac',
}


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config(config_path=None):
    """resolve-config.json with defaults for missing keys (rpresolve.config);
    a malformed file prints a warning and degrades to defaults."""
    return rpconfig.load_config(config_path, warn=print)


# ---------------------------------------------------------------------------
# Pure logic — no Resolve dependency, unit-testable offline
# ---------------------------------------------------------------------------

def collect_media_files(paths, extensions=MEDIA_EXTENSIONS):
    """Recursively collect media files from a list of file/directory paths.
    Directories are walked with rglob so nested camera-card structures
    (DCIM/100_PANA/, DCIM/1xxAPPLE/) are found, not just the top level."""
    files = []
    skipped = []
    for path_str in paths:
        p = Path(path_str).resolve()
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix.lower() in extensions:
                    files.append(str(f))
        elif p.is_file():
            files.append(str(p))
        else:
            skipped.append(path_str)
    return files, skipped


def duration_to_frames(seconds, fps):
    """Convert a duration in seconds to a frame count at the given fps."""
    return int(round(float(seconds) * float(fps)))


def parse_framerate(value):
    """Validate a --framerate string against known Resolve timeline rates.
    Returns (value, is_known) — unknown values are still passed through
    (Resolve may support rates this list doesn't) but flagged for the
    caller to warn on."""
    return (value, value in VALID_FRAMERATES)


def safe_fps(raw_value, default=24.0):
    """Parse a framerate string that may come back from Resolve in an
    unexpected shape; never raises."""
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def get_resolve():
    """Connect to running DaVinci Resolve instance."""
    try:
        return rpapi.connect()
    except rpapi.ResolveUnavailable as e:
        print(f"ERROR: {e}")
        sys.exit(1)


def get_project(resolve):
    """Get current project or exit."""
    pm = resolve.GetProjectManager()
    if not pm:
        print("ERROR: Could not get Project Manager from Resolve.")
        sys.exit(1)
    project = pm.GetCurrentProject()
    if not project:
        print("ERROR: No project is currently open.")
        sys.exit(1)
    return project


def get_root_folder(project):
    media_pool = project.GetMediaPool()
    if not media_pool:
        print("ERROR: Could not get Media Pool for this project.")
        sys.exit(1)
    root = media_pool.GetRootFolder()
    if not root:
        print("ERROR: Could not get Media Pool root folder.")
        sys.exit(1)
    return media_pool, root


# ---------------------------------------------------------------------------
# Bin Structure
# ---------------------------------------------------------------------------

def create_bin_structure(media_pool, bin_map):
    root = media_pool.GetRootFolder()
    existing = {f.GetName(): f for f in (root.GetSubFolderList() or [])}
    created = []

    for parent_name, children in bin_map.items():
        if parent_name in existing:
            parent_folder = existing[parent_name]
            print(f"  [exists] {parent_name}/")
        else:
            parent_folder = media_pool.AddSubFolder(root, parent_name)
            if parent_folder:
                print(f"  [created] {parent_name}/")
                created.append(parent_name)
            else:
                print(f"  [FAILED] Could not create {parent_name}/")
                continue

        if children:
            child_existing = {f.GetName(): f for f in (parent_folder.GetSubFolderList() or [])}
            for child_name in children:
                if child_name in child_existing:
                    print(f"  [exists]   {parent_name}/{child_name}/")
                else:
                    child = media_pool.AddSubFolder(parent_folder, child_name)
                    if child:
                        print(f"  [created]   {parent_name}/{child_name}/")
                        created.append(f"{parent_name}/{child_name}")
                    else:
                        print(f"  [FAILED]   Could not create {parent_name}/{child_name}/")

    return created


def find_folder(root, path_parts):
    """Navigate to a subfolder by path parts, e.g. ['Source', 'iPhone'].
    Warns (does not fail) if more than one sibling shares a name, since the
    Media Pool allows duplicate folder names and this always picks the
    first — a real ambiguity, not a bug we can silently resolve."""
    current = root
    for part in path_parts:
        siblings = current.GetSubFolderList() or []
        matches = [s for s in siblings if s.GetName() == part]
        if not matches:
            return None
        if len(matches) > 1:
            print(f"  WARNING: {len(matches)} folders named '{part}' under this parent; using the first.")
        current = matches[0]
    return current


# ---------------------------------------------------------------------------
# Commands — Project / Media
# ---------------------------------------------------------------------------

def cmd_new_project(args):
    resolve = get_resolve()
    pm = resolve.GetProjectManager()
    config = load_config(args.config)

    existing = pm.GetProjectListInCurrentFolder() or []
    if any(p.lower() == args.name.lower() for p in existing):
        print(f"ERROR: A project named '{args.name}' already exists in this folder.")
        print("  Choose a different name, or open the existing project manually.")
        sys.exit(1)

    project = pm.CreateProject(args.name)
    if not project:
        print(f"ERROR: Could not create project '{args.name}' (permissions or database error).")
        sys.exit(1)

    print(f"Project created: {args.name}")

    res = config["default_resolution"]
    w_ok = project.SetSetting("timelineResolutionWidth", str(res["width"]))
    h_ok = project.SetSetting("timelineResolutionHeight", str(res["height"]))
    print(f"  Resolution: {res['width']}x{res['height']}" + ("" if (w_ok and h_ok) else "  [WARNING: setting may not have applied]"))

    framerate = args.framerate or config["default_framerate"]
    _, known = parse_framerate(framerate)
    if not known:
        print(f"  WARNING: '{framerate}' is not a common Resolve timeline rate — double-check it.")
    fps_ok = project.SetSetting("timelineFrameRate", framerate)
    print(f"  Frame rate: {framerate}" + ("" if fps_ok else "  [WARNING: setting may not have applied]"))

    color_mode = config.get("color_science_mode")
    if color_mode:
        cs_ok = project.SetSetting("colorScienceMode", color_mode)
        actual = project.GetSetting("colorScienceMode")
        if actual == color_mode:
            print(f"  Color science: {color_mode}")
        else:
            print(f"  WARNING: requested color science '{color_mode}', Resolve reports '{actual}' — verify manually.")

    media_pool = project.GetMediaPool()
    print("\nCreating bin structure:")
    create_bin_structure(media_pool, config["bins"])

    pm.SaveProject()
    print(f"\nProject '{args.name}' ready.")


def cmd_import_media(args):
    resolve = get_resolve()
    project = get_project(resolve)
    media_pool, root = get_root_folder(project)
    config = load_config(args.config)

    clip_color = None
    if args.bin:
        bin_parts = args.bin.split("/")
    elif args.camera:
        cam = config["cameras"].get(args.camera.lower())
        if not cam:
            valid = ", ".join(sorted(config["cameras"].keys()))
            print(f"ERROR: Unknown camera '{args.camera}'. Configured cameras: {valid}")
            sys.exit(1)
        bin_parts = cam["bin"]
        clip_color = cam.get("clip_color")
    else:
        bin_parts = ["Source"]

    target = find_folder(root, bin_parts)
    if not target:
        print(f"ERROR: Bin '{'/'.join(bin_parts)}' not found. Run 'new-project' first.")
        sys.exit(1)

    media_pool.SetCurrentFolder(target)

    files, skipped = collect_media_files(args.files)
    for s in skipped:
        print(f"  [skip] Not found: {s}")

    if not files:
        print("ERROR: No valid media files found (searched directories recursively).")
        sys.exit(1)

    print(f"Importing {len(files)} file(s) into {'/'.join(bin_parts)}:")
    clips = media_pool.ImportMedia(files)

    if clips:
        print(f"  Imported {len(clips)} clip(s)")
        for clip in clips:
            name = clip.GetName() if clip else "?"
            tagged = ""
            if clip_color and clip:
                ok = clip.SetClipColor(clip_color)
                tagged = f" [tagged {clip_color}]" if ok else " [WARNING: tag failed]"
            print(f"    - {name}{tagged}")
        if clip_color:
            print(f"  Camera tag: clips colored '{clip_color}' — use --camera {args.camera} on apply-lut/apply-drx to target only these later.")
    else:
        print("  ERROR: Import failed. Check file paths and Resolve permissions.")


def cmd_build_timeline(args):
    resolve = get_resolve()
    project = get_project(resolve)
    media_pool, root = get_root_folder(project)

    timeline = media_pool.CreateEmptyTimeline(args.name)
    if not timeline:
        print(f"ERROR: Could not create timeline '{args.name}'.")
        sys.exit(1)

    print(f"Timeline created: {args.name}")
    project.SetCurrentTimeline(timeline)

    fps = safe_fps(project.GetSetting("timelineFrameRate"), default=24.0)

    for label, path_arg, duration_arg in (
        ("Intro", args.intro, args.intro_duration),
        ("Outro", args.outro, None),
    ):
        if not path_arg:
            continue
        clip_path = str(Path(path_arg).resolve())
        graphics_folder = find_folder(root, ["Graphics"])
        if graphics_folder:
            media_pool.SetCurrentFolder(graphics_folder)

        imported = media_pool.ImportMedia([clip_path])
        if not imported:
            print(f"  WARNING: Could not import {label.lower()}: {path_arg}")
            continue

        appended = media_pool.AppendToTimeline(imported)
        if not appended:
            print(f"  WARNING: {label} imported but could not be appended to the timeline.")
            continue

        print(f"  {label} added: {path_arg}")

        if label == "Intro" and duration_arg:
            item = appended[0]
            frames = duration_to_frames(duration_arg, fps)
            start = item.GetStart()
            try:
                ok = item.SetEnd(start + frames - 1)
            except Exception as e:
                ok = False
                print(f"  WARNING: could not resize intro duration ({e})")
            if ok:
                actual_frames = item.GetEnd() - item.GetStart() + 1
                actual_sec = actual_frames / fps
                print(f"  Intro duration set: {actual_sec:.2f}s ({actual_frames} frames)")
            else:
                print(f"  WARNING: requested {duration_arg}s intro duration did not apply — check manually.")

    print(f"Timeline '{args.name}' is now active.")


def cmd_add_subtitles(args):
    resolve = get_resolve()
    project = get_project(resolve)
    timeline = project.GetCurrentTimeline()

    if not timeline:
        print("ERROR: No active timeline.")
        sys.exit(1)

    srt_path = str(Path(args.srt_file).resolve())
    if not os.path.exists(srt_path):
        print(f"ERROR: File not found: {srt_path}")
        sys.exit(1)

    media_pool = project.GetMediaPool()
    before = timeline.GetTrackCount("subtitle")

    imported = media_pool.ImportMedia([srt_path])
    if not imported:
        print(f"ERROR: Could not import {srt_path} into the Media Pool.")
        sys.exit(1)

    appended = media_pool.AppendToTimeline(imported)
    after = timeline.GetTrackCount("subtitle")

    if appended and after > before:
        print(f"Subtitles placed on timeline: {args.srt_file}")
        print(f"  Subtitle tracks: {before} -> {after}")
    elif appended:
        print(f"Subtitle clip appended, but subtitle track count is unchanged ({after}).")
        print("  Resolve may have placed it as a regular clip rather than a subtitle track.")
        print("  Verify in the Edit page; if wrong, remove it and use File > Import > Subtitle manually.")
    else:
        print(f"ERROR: Imported {args.srt_file} into the Media Pool but could not append it to the timeline.")
        print("  Fallback: File > Import > Subtitle in the Resolve UI.")
        sys.exit(1)


AUTO_CAPTION_LANGUAGE_CONSTANTS = {
    "en": "AUTO_CAPTION_ENGLISH",
}


def cmd_auto_subtitle(args):
    resolve = get_resolve()
    project = get_project(resolve)
    timeline = project.GetCurrentTimeline()

    if not timeline:
        print("ERROR: No active timeline.")
        sys.exit(1)

    lang_code = (args.language or "en").lower()
    lang_attr = AUTO_CAPTION_LANGUAGE_CONSTANTS.get(lang_code, f"AUTO_CAPTION_{lang_code.upper()}")
    language_const = getattr(resolve, lang_attr, None)
    if language_const is None:
        print(f"ERROR: Resolve has no constant '{lang_attr}' for language '{lang_code}' on this version.")
        print("  Check Resolve's auto-caption language list in the UI for the exact name.")
        sys.exit(1)

    preset_const = getattr(resolve, "AUTO_CAPTION_SUBTITLE_DEFAULT", None)
    line_break_const = getattr(resolve, "AUTO_CAPTION_LINE_SINGLE", None)

    lang_key = getattr(resolve, "SUBTITLE_LANGUAGE", None)
    preset_key = getattr(resolve, "SUBTITLE_CAPTION_PRESET", None)
    chars_key = getattr(resolve, "SUBTITLE_CHARS_PER_LINE", None)
    linebreak_key = getattr(resolve, "SUBTITLE_LINE_BREAK", None)
    gap_key = getattr(resolve, "SUBTITLE_GAP", None)

    missing = [n for n, v in (
        ("SUBTITLE_LANGUAGE", lang_key), ("SUBTITLE_CAPTION_PRESET", preset_key),
        ("SUBTITLE_CHARS_PER_LINE", chars_key), ("SUBTITLE_LINE_BREAK", linebreak_key),
        ("SUBTITLE_GAP", gap_key),
    ) if v is None]
    if missing:
        print(f"ERROR: Resolve is missing subtitle constants on this version: {', '.join(missing)}")
        print("  Auto-caption settings schema may have changed — check the scripting API docs for your Resolve version.")
        sys.exit(1)

    settings = {
        lang_key: language_const,
        preset_key: preset_const,
        chars_key: args.chars_per_line or 42,
        linebreak_key: line_break_const,
        gap_key: args.gap or 0,
    }

    print(f"Generating auto-captions on '{timeline.GetName()}'...")
    print(f"  Language: {lang_code}")

    result = timeline.CreateSubtitlesFromAudio(settings)
    if result:
        print("  Auto-captions generated.")
    else:
        print("  ERROR: Auto-caption failed. Requires DaVinci Resolve Studio, and audio on the timeline.")


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def queue_render(project, preset_key, output_dir, presets, custom_name=None):
    """Queue a single render job from a preset (rpresolve.render) and print
    what happened. Returns job_id (str) or None."""
    r = rprender.queue_render_job(project, preset_key, output_dir, presets, custom_name)
    for w in r["warnings"]:
        print(f"  {w}" if w.startswith("NOTE") else f"  WARNING: {w}")
    if r["error"]:
        print(f"  ERROR: {r['error']}")
    if r["job_id"]:
        s, c = r["settings"], r["codec"]
        print(f"  [{r['name']}] Queued (job {r['job_id']}) -> {s['CustomName']}.{r['format']}")
        print(f"    {s['FormatWidth']}x{s['FormatHeight']} | {c['used']}")
    return r["job_id"]


def cmd_render(args):
    resolve = get_resolve()
    project = get_project(resolve)
    config = load_config(args.config)

    output_dir = args.output or os.getcwd()
    os.makedirs(output_dir, exist_ok=True)

    print(f"Output directory: {output_dir}")
    # The lock covers queueing and starting, not the monitor loop: a render
    # can run for an hour and must not lock every MCP session out of Resolve.
    with rpapi.ResolveLock():
        job_id = queue_render(project, args.preset, output_dir, config["render_presets"], args.name)
        if job_id and args.start:
            print("\nStarting render...")
            project.StartRendering()
    if job_id and args.start:
        _monitor_render(project, [job_id])


def cmd_render_all(args):
    resolve = get_resolve()
    project = get_project(resolve)
    config = load_config(args.config)

    output_dir = args.output or os.getcwd()
    os.makedirs(output_dir, exist_ok=True)

    presets_cfg = config["render_presets"]
    preset_keys = [p.strip() for p in args.presets.split(",")] if args.presets else list(presets_cfg.keys())

    print(f"Output directory: {output_dir}")
    print(f"Queuing {len(preset_keys)} preset(s):\n")

    job_ids = []
    with rpapi.ResolveLock():  # queueing and starting only; see cmd_render
        for preset_key in preset_keys:
            job_id = queue_render(project, preset_key, output_dir, presets_cfg, args.name)
            if job_id:
                job_ids.append(job_id)
        print(f"\n{len(job_ids)}/{len(preset_keys)} render job(s) queued.")
        if job_ids and args.start:
            print("\nStarting render...")
            project.StartRendering()
    if job_ids and args.start:
        _monitor_render(project, job_ids)


def cmd_clear_queue(args):
    resolve = get_resolve()
    project = get_project(resolve)
    jobs = project.GetRenderJobList() or []
    if not jobs:
        print("Render queue is already empty.")
        return
    result = project.DeleteAllRenderJobs()
    if result:
        print(f"Cleared {len(jobs)} render job(s) from the queue.")
    else:
        print("ERROR: Could not clear the render queue.")


def _monitor_render(project, job_ids):
    """Poll each queued job by its actual job id (GetRenderJobStatus takes
    a job id string, not an index) until all have a terminal JobStatus."""
    terminal = {"Complete", "Failed", "Cancelled"}
    statuses = {jid: "Queued" for jid in job_ids}

    time.sleep(1)  # give StartRendering a moment before the first poll
    while True:
        for jid in job_ids:
            status = project.GetRenderJobStatus(jid) or {}
            statuses[jid] = status.get("JobStatus", statuses[jid])

        pct_line = " | ".join(f"{jid}: {statuses[jid]}" for jid in job_ids)
        print(f"\r  {pct_line}    ", end="", flush=True)

        if all(s in terminal for s in statuses.values()):
            break
        if not project.IsRenderingInProgress() and any(s not in terminal for s in statuses.values()):
            # Rendering stopped but a job never reported terminal — don't spin forever.
            break
        time.sleep(2)

    print()
    for jid in job_ids:
        s = statuses[jid]
        mark = "OK" if s == "Complete" else "FAIL" if s in ("Failed", "Cancelled") else "?"
        print(f"  [{mark}] job {jid}: {s}")


# ---------------------------------------------------------------------------
# LUT / Grade Application
# ---------------------------------------------------------------------------

def _filter_items_by_camera(items, camera_key, config):
    """Filter timeline items to those whose media pool clip carries the
    clip-color tag for the given camera key (set at import-media time)."""
    cam = config["cameras"].get(camera_key.lower())
    if not cam:
        valid = ", ".join(sorted(config["cameras"].keys()))
        print(f"ERROR: Unknown camera '{camera_key}'. Configured cameras: {valid}")
        sys.exit(1)
    target_color = cam.get("clip_color")
    if not target_color:
        print(f"ERROR: Camera '{camera_key}' has no clip_color configured — cannot filter by it.")
        sys.exit(1)

    kept = []
    for item in items:
        mpi = item.GetMediaPoolItem()
        color = mpi.GetClipColor() if mpi else None
        if color == target_color:
            kept.append(item)
    return kept


def report_item_results(results, verb):
    """Print one ok/FAIL line per (name, ok, detail) and a count. Returns
    the exit status: 0 when every item succeeded, 1 otherwise."""
    applied = 0
    for name, ok, detail in results:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}: {detail}")
        applied += 1 if ok else 0
    failed = len(results) - applied
    print(f"\n{verb} {applied}/{len(results)} clips." + (f" {failed} FAILED." if failed else ""))
    return 0 if failed == 0 else 1


def cmd_apply_lut(args):
    resolve = get_resolve()
    project = get_project(resolve)
    timeline = project.GetCurrentTimeline()

    if not timeline:
        print("ERROR: No active timeline.")
        sys.exit(1)

    # abspath keeps a symlink into Resolve's LUT folder as the in-folder
    # path Resolve scanned; realpath would send SetLUT the link target.
    lut_path = os.path.abspath(os.path.expanduser(args.lut_file))
    if not os.path.exists(lut_path):
        print(f"ERROR: LUT file not found: {lut_path}")
        sys.exit(1)

    track_index = args.track or 1
    node_index = args.node or 1

    items = timeline.GetItemListInTrack("video", track_index) or []
    if args.camera:
        config = load_config(args.config)
        items = _filter_items_by_camera(items, args.camera, config)

    if not items:
        print(f"No clips found on video track {track_index}" + (f" tagged for camera '{args.camera}'." if args.camera else "."))
        return 0

    print(f"Applying LUT to {len(items)} clip(s) on V{track_index}, node {node_index}:")
    print(f"  LUT: {lut_path}")
    if not any(_path_under(lut_path, root, follow_links=False) or _path_under(lut_path, root)
               for root in rpapi.DEFAULT_LUT_ROOTS):
        print("  WARNING: LUT is outside Resolve's LUT folders; SetLUT only accepts LUTs Resolve has scanned.")

    # SetLUT needs a LUT Resolve has scanned; rescan so a freshly copied
    # .cube is visible. Older builds may lack the call.
    refresh = getattr(project, "RefreshLUTList", None)
    if refresh:
        refresh()

    results = apply_lut_to_items(items, node_index, lut_path, rpapi.DEFAULT_LUT_ROOTS)
    return report_item_results(results, "Applied LUT to")


def cmd_apply_drx(args):
    resolve = get_resolve()
    project = get_project(resolve)
    timeline = project.GetCurrentTimeline()

    if not timeline:
        print("ERROR: No active timeline.")
        sys.exit(1)

    drx_path = str(Path(args.drx_file).resolve())
    if not os.path.exists(drx_path):
        print(f"ERROR: DRX file not found: {drx_path}")
        sys.exit(1)

    track_index = args.track or 1
    grade_mode = args.mode or 0

    items = timeline.GetItemListInTrack("video", track_index) or []
    if args.camera:
        config = load_config(args.config)
        items = _filter_items_by_camera(items, args.camera, config)

    if not items:
        print(f"No clips found on video track {track_index}" + (f" tagged for camera '{args.camera}'." if args.camera else "."))
        return 0

    print(f"Applying DRX grade to {len(items)} clip(s) on V{track_index}:")
    print(f"  Grade: {drx_path}")
    print(f"  Mode: {grade_mode}")
    print("  NOTE: a DRX grade replaces the clip's existing node graph, including any LUT set via apply-lut.")

    results = apply_drx_to_items(items, drx_path, grade_mode)
    return report_item_results(results, "Applied DRX grade to")


# ---------------------------------------------------------------------------
# Info / Utility Commands
# ---------------------------------------------------------------------------

def cmd_list_projects(args):
    resolve = get_resolve()
    pm = resolve.GetProjectManager()
    projects = pm.GetProjectListInCurrentFolder()

    if projects:
        print(f"Projects ({len(projects)}):")
        for p in projects:
            print(f"  - {p}")
    else:
        print("No projects found.")


def cmd_list_timelines(args):
    resolve = get_resolve()
    project = get_project(resolve)
    count = project.GetTimelineCount()

    if count == 0:
        print("No timelines in current project.")
        return

    current = project.GetCurrentTimeline()
    current_name = current.GetName() if current else ""
    fps = safe_fps(project.GetSetting("timelineFrameRate"), default=24.0)

    print(f"Timelines ({count}):")
    for i in range(1, count + 1):
        tl = project.GetTimelineByIndex(i)
        if tl:
            marker = " <- active" if tl.GetName() == current_name else ""
            duration_frames = tl.GetEndFrame() - tl.GetStartFrame()
            duration_sec = duration_frames / fps
            mins = int(duration_sec // 60)
            secs = int(duration_sec % 60)
            print(f"  {i}. {tl.GetName()} ({mins}:{secs:02d}){marker}")


def cmd_list_render_formats(args):
    resolve = get_resolve()
    project = get_project(resolve)

    formats = project.GetRenderFormats()
    if not formats:
        print("No render formats available.")
        return

    print("Available render formats (format key -> extension):\n")
    for fmt_key, extension in formats.items():
        print(f"  [{fmt_key}] .{extension}")
        codecs = project.GetRenderCodecs(fmt_key)
        if codecs:
            for description, name in codecs.items():
                print(f"      {name}  ({description})")
        print()
    print("Pass the [format key] and a codec NAME (right column above) into resolve-config.json presets.")


def cmd_info(args):
    resolve = get_resolve()
    project = get_project(resolve)

    print(f"Project: {project.GetName()}")
    print(f"  Resolution: {project.GetSetting('timelineResolutionWidth')}x{project.GetSetting('timelineResolutionHeight')}")
    print(f"  Frame Rate: {project.GetSetting('timelineFrameRate')}")
    print(f"  Color Science: {project.GetSetting('colorScienceMode')}")
    print(f"  Timelines: {project.GetTimelineCount()}")

    timeline = project.GetCurrentTimeline()
    if timeline:
        print(f"\nActive Timeline: {timeline.GetName()}")
        print(f"  Video Tracks: {timeline.GetTrackCount('video')}")
        print(f"  Audio Tracks: {timeline.GetTrackCount('audio')}")
        print(f"  Subtitle Tracks: {timeline.GetTrackCount('subtitle')}")

        fps = safe_fps(project.GetSetting("timelineFrameRate"), default=24.0)
        start = timeline.GetStartFrame()
        end = timeline.GetEndFrame()
        duration = (end - start) / fps
        print(f"  Duration: {int(duration // 60)}:{int(duration % 60):02d} ({end - start} frames)")
    else:
        print("\nNo active timeline.")

    jobs = project.GetRenderJobList() or []
    if jobs:
        print(f"\nRender queue: {len(jobs)} job(s) pending (run 'clear-queue' to reset).")


def cmd_open_page(args):
    valid_pages = ["media", "cut", "edit", "fusion", "color", "fairlight", "deliver"]
    page = args.page.lower()

    if page not in valid_pages:
        print(f"ERROR: Invalid page '{page}'. Choose from: {', '.join(valid_pages)}")
        sys.exit(1)

    resolve = get_resolve()
    resolve.OpenPage(page)
    print(f"Switched to: {page}")


def cmd_export_project(args):
    resolve = get_resolve()
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()

    if not project:
        print("ERROR: No project open.")
        sys.exit(1)

    name = project.GetName()
    output = args.output or os.getcwd()
    filepath = str(Path(output).resolve() / f"{name}.drp")

    result = pm.ExportProject(name, filepath)
    if result:
        print(f"Exported: {filepath}")
    else:
        print(f"ERROR: Export failed for '{name}'.")


# ---------------------------------------------------------------------------
# Offline survey (no Resolve connection)
# ---------------------------------------------------------------------------

def build_survey_command(args, python=SURVEY_PYTHON, script=SURVEY_SCRIPT):
    """The argv that runs resolve_survey.py for a parsed 'survey' command."""
    return rpwork.survey_command(args.out, args.json, args.projects, args.projects_dir,
                                 args.metadata_cache, args.no_metadata_cache, args.tree_labels,
                                 python=python, script=script)


def cmd_survey(args):
    """Run the read-only project survey. It reads Project.db snapshots from
    disk and never connects to Resolve, so it is safe while Resolve is open."""
    sys.stdout.flush()
    try:
        return rpwork.survey(args.out, args.json, args.projects, args.projects_dir,
                             args.metadata_cache, args.no_metadata_cache,
                             args.tree_labels)["returncode"]
    except rpwork.Refused as e:
        print(f"ERROR: {e}")
        return 1


# ---------------------------------------------------------------------------
# Offline detect (no Resolve connection)
# ---------------------------------------------------------------------------

def _out_problem(out):
    """Why --out cannot be used (inside this public repo, a directory, not
    writable), or None. Checked before any slow work."""
    return rppaths.out_problem(out)


def cmd_detect(args):
    """Classify each media file's camera and picture profile from its
    headers and write a TSV (or JSON). Never connects to Resolve.
    A pinned row carries an input_color_space at high or medium confidence;
    anything the survey grades lower is 'review' with an empty one.
    Exit status: 0 when every file is pinned, 2 when any row is 'review',
    'corrupt' or low confidence, 1 when the tools are missing or no file
    was found. A file that fails to probe becomes a 'review' row and the
    run goes on."""
    # The same guards as the survey, checked before any file is probed:
    # rows carry full media paths, and this repository is public.
    problem = _out_problem(args.out) if args.out else None
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    try:
        r = rpwork.detect(args.paths)
    except (rpdetect.ToolMissing, rpwork.Refused) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    rows = r["rows"]
    for m in r["missing"]:
        print(f"  [skip] Not found: {m}", file=sys.stderr)

    text = rpdetect.format_json(rows) if args.json else rpdetect.format_tsv(rows)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Wrote {len(rows)} row(s) to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    print(r["summary"], file=sys.stderr)
    return r["exit_status"]


def cmd_ingest(args):
    """Classify the media with detect, import it into camera bins under
    --bin, and tag each imported clip's Input Color Space and Data Level,
    reading every write back. For RCM projects made for this pipeline:
    --project must name the open project, and nothing already in its media
    pool is touched. Exit status: 0 all tagged or imported as planned; 2
    when any file went to Review, was skipped as corrupt, or is VFR; 1 on
    any failed import or write, or a refused precondition."""
    problem = _out_problem(args.out) if args.out else None
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    # detect probes files, not Resolve: run it before taking the lock.
    try:
        found = rpwork.detect(args.paths)
    except (rpdetect.ToolMissing, rpwork.Refused) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    resolve = get_resolve()
    try:
        with rpapi.ResolveLock():
            r = rpwork.ingest(resolve, args.project, args.paths, parent=args.bin,
                              dry_run=args.dry_run, rows=found["rows"])
        r["missing"] = found["missing"]
    except rpapi.ProjectChanged as e:
        print(f"ERROR: {e} ingest writes clip properties, so it runs only on the "
              "project named with --project.", file=sys.stderr)
        return 1
    except (rpapi.ResolveAPIError, rpdetect.ToolMissing) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    for m in r["missing"]:
        print(f"  [skip] Not found: {m}", file=sys.stderr)
    text = rpingest.format_report(r["plan"], r["results"])
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Wrote {len(r['results'])} row(s) to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    print("ingest: " + ", ".join(f"{v} {k}" for k, v in sorted(r["counts"].items()))
          + (" (dry run, nothing written)" if args.dry_run else ""), file=sys.stderr)
    return r["exit_status"]


def cmd_measure(args):
    """Measure a rendered file (see rpresolve/measure.py). Prints a summary;
    --json writes the full report. Exit 1 when the file cannot be read."""
    try:
        from rpresolve import measure as rpmeasure
    except ImportError as e:
        print(f"ERROR: measure needs numpy ({e}). Use /usr/bin/python3.", file=sys.stderr)
        return 1
    problem = _out_problem(args.json) if args.json else None
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    try:
        segments = [rpmeasure.parse_segment(s) for s in args.segment or []]
        if args.hero and args.hero not in [s[0] for s in segments]:
            print(f"ERROR: --hero {args.hero} is not one of the segment labels.", file=sys.stderr)
            return 1
        report = rpmeasure.measure(args.file, samples=args.samples, segments=segments,
                                   hero=args.hero, faces=not args.no_faces)
    except rpmeasure.MeasureError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    sys.stdout.write(rpmeasure.format_summary(report))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
            f.write("\n")
        print(f"Wrote {args.json}", file=sys.stderr)
    return 0


def _write_private(path, text):
    """Write a file that names private media and words; refused in the repo."""
    try:
        rppaths.write_private(path, text)
    except rppaths.OutputRefused as e:
        raise SystemExit(f"ERROR: {e}")


def cmd_manifest(args):
    """Build a cut manifest (see rpresolve/cutlist.py) and write it to --out."""
    from rpresolve import cutlist
    problem = _out_problem(args.out)
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    try:
        if args.from_cuts:
            spans = cutlist.spans_from_cuts_script(args.from_cuts)
        elif args.from_tsv:
            spans = cutlist.spans_from_vertcut_tsv(args.from_tsv, args.fps, args.offset)
        else:
            spans = cutlist.spans_from_edl(args.from_edl)
        reframe = {"face_x": args.face_x} if args.face_x is not None else None
        m = cutlist.build_manifest(spans, os.path.abspath(args.source), args.fps,
                                   os.path.abspath(args.words), os.path.abspath(args.approved),
                                   reframe=reframe)
    except (cutlist.CutlistError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    _write_private(args.out, json.dumps(m, indent=1) + "\n")
    n = sum(len(c["spans"]) for c in m["clips"])
    print(f"Wrote {len(m['clips'])} clip(s), {n} span(s) to {args.out}", file=sys.stderr)
    return 0


def cmd_endcheck(args):
    """Check a manifest. Exit 0 all pass, 2 any review, 1 any fail."""
    from rpresolve import cutlist
    try:
        m = cutlist.load_manifest(args.manifest)
        rows = cutlist.endcheck(m, audio=not args.no_audio)
    except (cutlist.CutlistError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    for r in rows:
        print(cutlist.endcheck_line(r))
    n = cutlist.endcheck_counts(rows)
    fails, reviews = n["fail"], n["review"]
    print(f"endcheck: {len(rows)} span(s): {n['pass']} pass, {reviews} review, "
          f"{fails} fail" + (" (audio not checked)" if args.no_audio else ""), file=sys.stderr)
    if args.json:
        _write_private(args.json, json.dumps(rows, indent=1, default=str) + "\n")
    return 1 if fails else (2 if reviews else 0)


def cmd_selects(args):
    """Propose spans inside the approved text, as a TSV (in, out, seconds,
    coverage, end words, text). Isaac picks; nothing is cut."""
    from rpresolve import cutlist
    try:
        words = cutlist.load_words(args.words)
        approved = cutlist.load_approved(args.approved)
    except (cutlist.CutlistError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    rows = cutlist.selects(words, approved, args.min_seconds, args.max_seconds)
    text = cutlist.selects_tsv(rows)
    if args.out:
        _write_private(args.out, text)
        print(f"Wrote {len(rows)} proposal(s) to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


def cmd_cut(args):
    """Build [auto] timelines from a manifest in the open project named with
    --project. Exit 0 all built and read back, 1 on any failure."""
    from rpresolve import cutlist
    try:
        gate = rpwork.cut_gate(args.manifest, args.only, audio=not args.no_audio,
                               force=args.force)
    except (cutlist.CutlistError, OSError, rpwork.Refused) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    for name, reasons in gate["blocked"].items():
        print(f"  {'FORCED' if args.force else 'BLOCKED'} {name}: " + " | ".join(reasons),
              file=sys.stderr)
    resolve = get_resolve()
    try:
        with rpapi.ResolveLock():
            r = rpwork.cut(resolve, args.project, args.manifest, prefix=args.prefix,
                           make_9x16=not args.no_9x16, gate=gate)
    except rpapi.ProjectChanged as e:
        print(f"ERROR: {e} cut builds timelines, so it runs only on the project named with "
              "--project.", file=sys.stderr)
        return 1
    except rpapi.ResolveAPIError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    for name in r["skipped_existing"]:
        print(f"  skip  {name} (exists; left untouched)")
    for x in r["results"]:
        if x["kind"] == "16x9":
            print(f"  {'made ' if x['ok'] else 'FAIL '} {x['name']}  {x['frames']} frames "
                  f"(expected {x['frames_expected']}) {x['reason']}")
        else:
            print(f"  {'made ' if x['ok'] else 'FAIL '} {x['name']}  {x['props'] or ''} {x['reason']}")
    if r["ui_restore_problems"]:
        print("  UI restore: " + "; ".join(r["ui_restore_problems"]), file=sys.stderr)
    return r["exit_status"]


def _locked(fn):
    """Run a Resolve write command under the cross-process Resolve lock, so
    it cannot interleave with an MCP session's writes."""
    def run(args):
        try:
            with rpapi.ResolveLock():
                return fn(args)
        except rpapi.ResolveBusy as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
    run.__name__, run.__doc__ = fn.__name__, fn.__doc__
    return run


cmd_apply_lut = _locked(cmd_apply_lut)
cmd_apply_drx = _locked(cmd_apply_drx)
cmd_clear_queue = _locked(cmd_clear_queue)


# ---------------------------------------------------------------------------
# CLI Parser
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="resolve_workflow",
        description="Comprehensive CLI workflow tool for DaVinci Resolve.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create a new project with standard bins
  python3 resolve_workflow.py new-project "March Shoot"

  # Create project at 29.97fps
  python3 resolve_workflow.py new-project "Interview" --framerate 29.97

  # Import iPhone footage (recursively, tags clips by camera color)
  python3 resolve_workflow.py import-media /path/to/footage/ --camera iphone

  # Import files to a custom bin
  python3 resolve_workflow.py import-media file1.mov file2.mov --bin "Source/Audio"

  # Build timeline with intro card
  python3 resolve_workflow.py build-timeline "Main Edit" --intro /path/to/intro-4k.png

  # Import subtitles
  python3 resolve_workflow.py add-subtitles /path/to/subs.srt

  # Generate auto-captions (Resolve Studio only)
  python3 resolve_workflow.py auto-subtitle --language en

  # Apply LUT only to clips tagged as GH7 on V1
  python3 resolve_workflow.py apply-lut /path/to/GH7ToRec709.cube --track 1 --camera gh7

  # Apply a .drx grade to all clips on V1
  python3 resolve_workflow.py apply-drx /path/to/grade.drx --track 1

  # Queue YouTube render
  python3 resolve_workflow.py render youtube --output /path/to/exports/

  # Queue all render presets and start immediately
  python3 resolve_workflow.py render-all --output /path/to/exports/ --start

  # Clear a stale render queue before requeuing
  python3 resolve_workflow.py clear-queue

  # List available render codecs (use this to fix resolve-config.json presets)
  python3 resolve_workflow.py list-render-formats

  # Show project info
  python3 resolve_workflow.py info

  # Switch to color page
  python3 resolve_workflow.py open-page color

  # Export project backup
  python3 resolve_workflow.py export-project --output /backups/

  # Survey every project's grades, timelines and media (offline, read-only)
  python3 resolve_workflow.py survey --out /path/outside/repo/survey.md --json /path/outside/repo/survey.json

  # Classify cameras and picture profiles offline; exits 2 if any row needs review
  python3 resolve_workflow.py detect /path/to/card/ --out detect.tsv

  # Import a card into camera bins and tag input color spaces (RCM project only)
  python3 resolve_workflow.py ingest /path/to/card/ --project "My New Project" --dry-run

  # Measure a render; camera-match numbers for two cameras by time range
  python3 resolve_workflow.py measure render.mov --segment A=0-30 --segment B=30-60 --hero A
        """,
    )
    parser.add_argument("--config", help="Path to resolve-config.json (default: alongside this script)")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    sp = subparsers.add_parser("new-project", help="Create new project with standard bins")
    sp.add_argument("name", help="Project name")
    sp.add_argument("--framerate", help="Timeline frame rate (default: from config)")
    sp.set_defaults(func=cmd_new_project)

    sp = subparsers.add_parser("import-media", help="Import media files into camera bins (recursive)")
    sp.add_argument("files", nargs="+", help="File paths or directories to import")
    sp.add_argument("--camera", "-c", help="Camera key from resolve-config.json")
    sp.add_argument("--bin", "-b", help="Custom bin path (e.g. 'Source/Audio')")
    sp.set_defaults(func=cmd_import_media)

    sp = subparsers.add_parser("build-timeline", help="Create timeline with optional intro/outro")
    sp.add_argument("name", help="Timeline name")
    sp.add_argument("--intro", help="Path to intro graphic (PNG/TIFF)")
    sp.add_argument("--outro", help="Path to outro graphic (PNG/TIFF)")
    sp.add_argument("--intro-duration", type=float, default=4.0, help="Intro duration in seconds (default: 4)")
    sp.set_defaults(func=cmd_build_timeline)

    sp = subparsers.add_parser("add-subtitles", help="Import .srt subtitles")
    sp.add_argument("srt_file", help="Path to .srt subtitle file")
    sp.set_defaults(func=cmd_add_subtitles)

    sp = subparsers.add_parser("auto-subtitle", help="Generate subtitles from audio (Studio only)")
    sp.add_argument("--language", default="en", help="Language code (default: en)")
    sp.add_argument("--chars-per-line", type=int, default=42, help="Characters per line (default: 42)")
    sp.add_argument("--gap", type=int, default=0, help="Gap setting (default: 0)")
    sp.set_defaults(func=cmd_auto_subtitle)

    sp = subparsers.add_parser("apply-lut", help="Apply LUT to clips on a track")
    sp.add_argument("lut_file", help="Path to .cube LUT file")
    sp.add_argument("--track", "-t", type=int, default=1, help="Video track number (default: 1)")
    sp.add_argument("--node", "-n", type=int, default=1, help="Node index to apply LUT (default: 1)")
    sp.add_argument("--camera", help="Only apply to clips tagged for this camera (see resolve-config.json)")
    sp.set_defaults(func=cmd_apply_lut)

    sp = subparsers.add_parser("apply-drx", help="Apply .drx grade to clips on a track")
    sp.add_argument("drx_file", help="Path to .drx grade file")
    sp.add_argument("--track", "-t", type=int, default=1, help="Video track number (default: 1)")
    sp.add_argument("--mode", "-m", type=int, default=0,
                     choices=[0, 1, 2],
                     help="Grade mode: 0=no keyframes, 1=source TC aligned, 2=start frames aligned (default: 0)")
    sp.add_argument("--camera", help="Only apply to clips tagged for this camera (see resolve-config.json)")
    sp.set_defaults(func=cmd_apply_drx)

    sp = subparsers.add_parser("render", help="Queue a render preset")
    sp.add_argument("preset", help="Render preset key from resolve-config.json")
    sp.add_argument("--output", "-o", help="Output directory (default: current dir)")
    sp.add_argument("--name", help="Custom output filename (without extension)")
    sp.add_argument("--start", "-s", action="store_true", help="Start rendering immediately")
    sp.set_defaults(func=cmd_render)

    sp = subparsers.add_parser("render-all", help="Queue all render presets")
    sp.add_argument("--output", "-o", help="Output directory (default: current dir)")
    sp.add_argument("--name", help="Custom output filename (without extension)")
    sp.add_argument("--presets", help="Comma-separated preset list (default: all)")
    sp.add_argument("--start", "-s", action="store_true", help="Start rendering immediately")
    sp.set_defaults(func=cmd_render_all)

    sp = subparsers.add_parser("clear-queue", help="Delete all pending render jobs")
    sp.set_defaults(func=cmd_clear_queue)

    sp = subparsers.add_parser("list-projects", help="List projects in current database")
    sp.set_defaults(func=cmd_list_projects)

    sp = subparsers.add_parser("list-timelines", help="List timelines in current project")
    sp.set_defaults(func=cmd_list_timelines)

    sp = subparsers.add_parser("list-render-formats", help="List available render formats and codecs")
    sp.set_defaults(func=cmd_list_render_formats)

    sp = subparsers.add_parser("info", help="Show project/timeline info")
    sp.set_defaults(func=cmd_info)

    sp = subparsers.add_parser("open-page", help="Switch Resolve to a specific page")
    sp.add_argument("page", choices=["media", "cut", "edit", "fusion", "color", "fairlight", "deliver"])
    sp.set_defaults(func=cmd_open_page)

    sp = subparsers.add_parser("export-project", help="Export current project as .drp")
    sp.add_argument("--output", "-o", help="Output directory (default: current dir)")
    sp.set_defaults(func=cmd_export_project)

    sp = subparsers.add_parser(
        "survey", help="Read-only survey of every project on disk (offline, no Resolve API)")
    sp.add_argument("--out", required=True,
                    help="Markdown report path (refused inside this repository)")
    sp.add_argument("--json", help="Also write the survey as JSON here")
    sp.add_argument("--projects", nargs="+", metavar="NAME",
                    help="Only these projects (default: every project folder)")
    sp.add_argument("--projects-dir", help="Resolve's Projects folder (default: the disk database)")
    sp.add_argument("--metadata-cache",
                    help="Resolve's ProjectMetadataCache/Metadata.db to cross-check against "
                         "(default: the live one)")
    sp.add_argument("--no-metadata-cache", action="store_true",
                    help="Skip the cross-check against Resolve's metadata cache")
    sp.add_argument("--tree-labels", nargs="+", metavar="LABEL",
                    help="Node labels that mark the house node tree (default: the survey's)")
    sp.set_defaults(func=cmd_survey)

    sp = subparsers.add_parser(
        "detect", help="Offline: classify camera and picture profile from file headers")
    sp.add_argument("paths", nargs="+", help="Media files or directories (searched recursively)")
    sp.add_argument("--out", "-o", help="Write the table here instead of stdout")
    sp.add_argument("--json", action="store_true", help="Emit JSON instead of TSV")
    sp.set_defaults(func=cmd_detect)

    sp = subparsers.add_parser(
        "ingest", help="Import classified media into camera bins and tag input color spaces")
    sp.add_argument("paths", nargs="+", help="Media files or directories (searched recursively)")
    sp.add_argument("--project", required=True,
                    help="Name of the open project; ingest refuses any other")
    sp.add_argument("--bin", default="Source", help="Parent bin for the camera bins (default: Source)")
    sp.add_argument("--dry-run", action="store_true",
                    help="Classify and plan against the media pool; write nothing")
    sp.add_argument("--out", "-o", help="Write the report here instead of stdout")
    sp.set_defaults(func=cmd_ingest)

    sp = subparsers.add_parser(
        "measure", help="Offline: luma, clipping, legal range and skin numbers from a render")
    sp.add_argument("file", help="Rendered video or still")
    sp.add_argument("--samples", type=int, default=10, help="Frames to sample (default 10)")
    sp.add_argument("--segment", action="append", metavar="LABEL=START-END",
                    help="A camera's time range in seconds; repeat per camera")
    sp.add_argument("--hero", help="Segment label whose skin chroma the others are compared to")
    sp.add_argument("--no-faces", action="store_true", help="Skip face and skin measurement")
    sp.add_argument("--json", help="Also write the full report as JSON here")
    sp.set_defaults(func=cmd_measure)

    sp = subparsers.add_parser("manifest", help="Offline: build a cut manifest")
    src = sp.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-cuts", metavar="PY", help="A Python file with a literal CUTS dict")
    src.add_argument("--from-tsv", metavar="TSV", help="A vertcut cuts TSV")
    src.add_argument("--from-edl", metavar="JSON", help="A video-use edl.json")
    sp.add_argument("--source", required=True, help="The source media file")
    sp.add_argument("--fps", type=float, required=True, help="Source frame rate")
    sp.add_argument("--words", required=True, help="mlx_whisper word-level JSON of the source")
    sp.add_argument("--approved", required=True, help="The approved text (essay) clips must stay inside")
    sp.add_argument("--offset", type=float, default=0.0, help="Seconds added to TSV timecodes")
    sp.add_argument("--face-x", type=float, help="Speaker's face centre in source pixels, for 9:16")
    sp.add_argument("--out", required=True, help="Manifest JSON path (refused inside this repo)")
    sp.set_defaults(func=cmd_manifest)

    sp = subparsers.add_parser("endcheck", help="Offline: check every cut's end, pause and text")
    sp.add_argument("manifest")
    sp.add_argument("--no-audio", action="store_true", help="Word and text checks only")
    sp.add_argument("--json", help="Also write the full results here")
    sp.set_defaults(func=cmd_endcheck)

    sp = subparsers.add_parser("selects", help="Offline: propose spans inside the approved text")
    sp.add_argument("words", help="mlx_whisper word-level JSON")
    sp.add_argument("--approved", required=True, help="The approved text (essay)")
    sp.add_argument("--min-seconds", type=float, default=20.0)
    sp.add_argument("--max-seconds", type=float, default=65.0)
    sp.add_argument("--out", help="Write the TSV here instead of stdout")
    sp.set_defaults(func=cmd_selects)

    sp = subparsers.add_parser("cut", help="Build [auto] 16:9 and 9:16 timelines from a manifest")
    sp.add_argument("manifest")
    sp.add_argument("--project", required=True, help="Name of the open project; cut refuses any other")
    sp.add_argument("--prefix", default="SW", help="Timeline name prefix (default SW)")
    sp.add_argument("--only", nargs="+", metavar="CLIP", help="Only these clips")
    sp.add_argument("--no-9x16", action="store_true", help="Skip the 9:16 versions")
    sp.add_argument("--no-audio", action="store_true", help="Skip the audio part of the gate")
    sp.add_argument("--force", action="store_true",
                    help="Build clips that fail endcheck (to reproduce an old cut); reported loudly")
    sp.set_defaults(func=cmd_cut)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        rpapi.exit_clean(1)

    try:
        status = args.func(args)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        status = 130
    except SystemExit as e:
        if isinstance(e.code, int) or e.code is None:
            status = e.code
        else:
            print(e.code, file=sys.stderr)
            status = 1
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
        status = 1
    # Commands return an int status; ones that just print return None (= 0).
    rpapi.exit_clean(status or 0)


if __name__ == "__main__":
    main()
