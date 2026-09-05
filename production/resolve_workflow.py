#!/usr/bin/env python3
"""
resolve_workflow.py — Comprehensive CLI workflow tool for DaVinci Resolve.

Requires DaVinci Resolve Studio to be running.
Connects via the DaVinci Resolve Scripting API.

Usage:
    python3 resolve_workflow.py <command> [options]

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

Known API limits (verified against the Blackmagic scripting docs, not a live
session — confirm against your Resolve version):
    - The scripting API cannot create color-page nodes. apply-lut/apply-drx
      targeting a node index beyond what the clip already has will fail with
      an explicit error rather than silently doing nothing. Add the node in
      the Color page first (or apply a .drx that already contains it).
    - Subtitle *styling* (font/color/position) has no scripting entry point;
      add-subtitles places the track, styling stays a manual Edit-page step.
    - The "story" vertical preset resizes the canvas only. It does not
      reframe subjects — do that per-clip before rendering vertical.
"""

import sys
import os
import json
import argparse
import time
from pathlib import Path

CONFIG_PATH_DEFAULT = Path(__file__).resolve().parent / "resolve-config.json"

DEFAULT_CONFIG = {
    "bins": {
        "Source": ["iPhone", "GH7", "GH5", "Audio"],
        "Selects": [],
        "Timeline": [],
        "Graphics": [],
        "Exports": [],
    },
    "cameras": {
        "iphone": {"bin": ["Source", "iPhone"], "clip_color": "Blue"},
        "gh7": {"bin": ["Source", "GH7"], "clip_color": "Orange"},
        "gh5": {"bin": ["Source", "GH5"], "clip_color": "Yellow"},
    },
    "default_resolution": {"width": 3840, "height": 2160},
    "default_framerate": "23.976",
    "color_science_mode": "davinciYRGBColorManagedv2",
    "render_presets": {
        "youtube": {
            "name": "YouTube 4K", "resolution": {"width": 3840, "height": 2160},
            "format": "mp4", "codec": "H265", "codec_fallbacks": ["HEVC", "H.265"],
            "suffix": "_youtube",
        },
        "linkedin": {
            "name": "LinkedIn 4K", "resolution": {"width": 3840, "height": 2160},
            "format": "mp4", "codec": "H264", "codec_fallbacks": ["H.264", "AVC"],
            "suffix": "_linkedin",
        },
        "master": {
            "name": "Master ProRes", "resolution": {"width": 3840, "height": 2160},
            "format": "mov", "codec": "ProRes422HQ",
            "codec_fallbacks": ["Apple ProRes 422 HQ"], "suffix": "_master",
        },
        "story": {
            "name": "Instagram Story", "resolution": {"width": 1080, "height": 1920},
            "format": "mp4", "codec": "H264", "codec_fallbacks": ["H.264", "AVC"],
            "suffix": "_story",
            "note": "Resize only — no automatic reframe. Set per-clip Pan/Zoom before rendering vertical.",
        },
    },
}

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
    """Load resolve-config.json, falling back to built-in defaults for any
    missing top-level key. Never raises — a missing or malformed config
    degrades to defaults with a printed warning."""
    path = Path(config_path) if config_path else CONFIG_PATH_DEFAULT
    config = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy

    if not path.exists():
        return config

    try:
        with open(path) as f:
            user_config = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"WARNING: Could not read config '{path}': {e}. Using defaults.")
        return config

    for key, value in user_config.items():
        config[key] = value
    return config


# ---------------------------------------------------------------------------
# Pure logic — no Resolve dependency, unit-testable offline
# ---------------------------------------------------------------------------

def pick_codec(preferred_name, fallback_names, codecs_dict):
    """Given GetRenderCodecs()'s {description: name} dict, find the best
    match for a desired codec NAME (the dict's values, not its keys — the
    value side is what SetCurrentRenderFormatAndCodec expects).

    Returns (description, name, matched) where matched is False if we fell
    back to "first available" rather than finding what was asked for.
    """
    if not codecs_dict:
        return (None, None, False)

    by_name = {name: desc for desc, name in codecs_dict.items()}

    if preferred_name in by_name:
        return (by_name[preferred_name], preferred_name, True)

    for fallback in fallback_names or []:
        if fallback in by_name:
            return (by_name[fallback], fallback, True)

    first_desc, first_name = next(iter(codecs_dict.items()))
    return (first_desc, first_name, False)


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
        import DaVinciResolveScript as dvr
    except ImportError:
        script_module = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
        if script_module not in sys.path:
            sys.path.append(script_module)
        try:
            import DaVinciResolveScript as dvr
        except ImportError:
            print("ERROR: Cannot import DaVinciResolveScript.")
            print("Make sure DaVinci Resolve is running and environment variables are set.")
            print("See --help for environment setup instructions.")
            sys.exit(1)

    resolve = dvr.scriptapp("Resolve")
    if not resolve:
        print("ERROR: Could not connect to DaVinci Resolve.")
        print("Make sure DaVinci Resolve Studio is running.")
        sys.exit(1)
    return resolve


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
    """Queue a single render job from a preset. Returns job_id (str) or None."""
    preset = presets.get(preset_key)
    if not preset:
        print(f"ERROR: Unknown preset '{preset_key}'. Configured presets: {', '.join(presets.keys())}")
        return None

    timeline = project.GetCurrentTimeline()
    if not timeline:
        print("ERROR: No active timeline.")
        return None

    filename = custom_name or timeline.GetName()

    codecs = project.GetRenderCodecs(preset["format"])
    desc, codec_name, matched = pick_codec(preset["codec"], preset.get("codec_fallbacks"), codecs)
    if not codec_name:
        print(f"  ERROR: No codecs available for format '{preset['format']}'.")
        return None
    if not matched:
        print(f"  WARNING: preferred codec '{preset['codec']}' not found; using '{codec_name}' ({desc}) instead.")

    codec_ok = project.SetCurrentRenderFormatAndCodec(preset["format"], codec_name)
    actual = project.GetCurrentRenderFormatAndCodec() or {}
    if not codec_ok or actual.get("codec") != codec_name or actual.get("format") != preset["format"]:
        print(f"  WARNING: requested format/codec '{preset['format']}/{codec_name}', Resolve reports {actual} — render may not match the preset.")

    res = preset["resolution"]
    settings = {
        "TargetDir": str(Path(output_dir).resolve()),
        "CustomName": f"{filename}{preset['suffix']}",
        "FormatWidth": res["width"],
        "FormatHeight": res["height"],
        "ExportVideo": True,
        "ExportAudio": True,
    }
    settings_ok = project.SetRenderSettings(settings)
    if not settings_ok:
        print(f"  WARNING: SetRenderSettings reported failure for '{preset['name']}' — verify Deliver page before rendering.")

    if preset.get("note"):
        print(f"  NOTE: {preset['note']}")

    job_id = project.AddRenderJob()
    if job_id:
        print(f"  [{preset['name']}] Queued (job {job_id}) -> {settings['CustomName']}.{preset['format']}")
        print(f"    {res['width']}x{res['height']} | {codec_name}")
        return job_id
    else:
        print(f"  ERROR: Failed to queue {preset['name']}")
        return None


def cmd_render(args):
    resolve = get_resolve()
    project = get_project(resolve)
    config = load_config(args.config)

    output_dir = args.output or os.getcwd()
    os.makedirs(output_dir, exist_ok=True)

    print(f"Output directory: {output_dir}")
    job_id = queue_render(project, args.preset, output_dir, config["render_presets"], args.name)

    if job_id and args.start:
        print("\nStarting render...")
        project.StartRendering()
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
    for preset_key in preset_keys:
        job_id = queue_render(project, preset_key, output_dir, presets_cfg, args.name)
        if job_id:
            job_ids.append(job_id)

    print(f"\n{len(job_ids)}/{len(preset_keys)} render job(s) queued.")

    if job_ids and args.start:
        print("\nStarting render...")
        project.StartRendering()
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


def cmd_apply_lut(args):
    resolve = get_resolve()
    project = get_project(resolve)
    timeline = project.GetCurrentTimeline()

    if not timeline:
        print("ERROR: No active timeline.")
        sys.exit(1)

    lut_path = str(Path(args.lut_file).resolve())
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
        return

    print(f"Applying LUT to {len(items)} clip(s) on V{track_index}, node {node_index}:")
    print(f"  LUT: {lut_path}")

    applied, node_errors = 0, 0
    for item in items:
        num_nodes = item.GetNumNodes()
        if node_index > num_nodes:
            print(f"  [FAIL] {item.GetName()}: only has {num_nodes} node(s); the scripting API cannot create node {node_index}.")
            print(f"         Add the node in the Color page first, then re-run.")
            node_errors += 1
            continue
        result = item.SetLUT(node_index, lut_path)
        if result:
            print(f"  [ok] {item.GetName()}")
            applied += 1
        else:
            print(f"  [FAIL] {item.GetName()}")

    print(f"\nApplied to {applied}/{len(items)} clips." + (f" ({node_errors} skipped: missing node)" if node_errors else ""))


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
        return

    print(f"Applying DRX grade to {len(items)} clip(s) on V{track_index}:")
    print(f"  Grade: {drx_path}")
    print(f"  Mode: {grade_mode}")
    print("  NOTE: a DRX grade replaces the clip's existing node graph, including any LUT set via apply-lut.")

    result = timeline.ApplyGradeFromDRX(drx_path, grade_mode, items)
    if result:
        print(f"  Applied to {len(items)} clips.")
    else:
        print("  ERROR: Grade application failed.")


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

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        args.func(args)
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
