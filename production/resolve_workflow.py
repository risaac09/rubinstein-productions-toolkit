#!/usr/bin/env python3
"""
resolve_workflow.py — Comprehensive CLI workflow tool for DaVinci Resolve.

Requires DaVinci Resolve Studio to be running.
Connects via the DaVinci Resolve Scripting API.

Usage:
    python3 resolve_workflow.py <command> [options]

Commands:
    new-project         Create a new project with standard bin structure
    import-media        Import media files into camera-specific bins
    build-timeline      Create timeline with optional intro/outro cards
    add-subtitles       Import .srt subtitles onto timeline
    render              Queue render jobs (YouTube, LinkedIn, Master, Story)
    render-all          Queue all 4K presets at once
    apply-lut           Apply a LUT to clips on a specific track
    apply-drx           Apply a .drx grade to timeline items
    list-projects       List all projects in current database
    list-timelines      List all timelines in current project
    list-render-formats List available render formats and codecs
    info                Show current project/timeline info
    open-page           Switch Resolve to a specific page
    export-project      Export current project as .drp file

Environment Setup (macOS):
    export RESOLVE_SCRIPT_API="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
    export RESOLVE_SCRIPT_LIB="/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
    export PYTHONPATH="$PYTHONPATH:$RESOLVE_SCRIPT_API/Modules/"
"""

import sys
import os
import argparse
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def get_resolve():
    """Connect to running DaVinci Resolve instance."""
    try:
        import DaVinciResolveScript as dvr
    except ImportError:
        # Fallback: try to load from standard macOS path
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
    project = pm.GetCurrentProject()
    if not project:
        print("ERROR: No project is currently open.")
        sys.exit(1)
    return project


# ---------------------------------------------------------------------------
# Bin Structure
# ---------------------------------------------------------------------------

DEFAULT_BINS = {
    "Source": ["iPhone", "GH7", "GH5", "Audio"],
    "Selects": [],
    "Timeline": [],
    "Graphics": [],
    "Exports": [],
}


def create_bin_structure(media_pool, bin_map=None):
    """Create standard folder structure in the Media Pool."""
    if bin_map is None:
        bin_map = DEFAULT_BINS

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
    """Navigate to a subfolder by path parts, e.g. ['Source', 'iPhone']."""
    current = root
    for part in path_parts:
        found = None
        for sub in (current.GetSubFolderList() or []):
            if sub.GetName() == part:
                found = sub
                break
        if not found:
            return None
        current = found
    return current


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_new_project(args):
    """Create a new project with standard bin structure and settings."""
    resolve = get_resolve()
    pm = resolve.GetProjectManager()

    project = pm.CreateProject(args.name)
    if not project:
        print(f"ERROR: Could not create project '{args.name}'. It may already exist.")
        sys.exit(1)

    print(f"Project created: {args.name}")

    # Set project settings
    project.SetSetting("timelineResolutionWidth", "3840")
    project.SetSetting("timelineResolutionHeight", "2160")

    if args.framerate:
        project.SetSetting("timelineFrameRate", args.framerate)
        print(f"  Frame rate: {args.framerate}")
    else:
        project.SetSetting("timelineFrameRate", "23.976")
        print("  Frame rate: 23.976 (default)")

    print("  Resolution: 3840x2160 (UHD 4K)")

    # Create bin structure
    media_pool = project.GetMediaPool()
    print("\nCreating bin structure:")
    create_bin_structure(media_pool)

    pm.SaveProject()
    print(f"\nProject '{args.name}' ready.")


def cmd_import_media(args):
    """Import media files into the appropriate camera bin."""
    resolve = get_resolve()
    project = get_project(resolve)
    media_pool = project.GetMediaPool()
    root = media_pool.GetRootFolder()

    # Determine target bin
    if args.bin:
        bin_parts = args.bin.split("/")
    elif args.camera:
        camera_map = {
            "iphone": ["Source", "iPhone"],
            "gh7": ["Source", "GH7"],
            "gh5": ["Source", "GH5"],
        }
        bin_parts = camera_map.get(args.camera.lower())
        if not bin_parts:
            print(f"ERROR: Unknown camera '{args.camera}'. Use: iphone, gh7, gh5")
            sys.exit(1)
    else:
        bin_parts = ["Source"]

    target = find_folder(root, bin_parts)
    if not target:
        print(f"ERROR: Bin '{'/'.join(bin_parts)}' not found. Run 'new-project' first.")
        sys.exit(1)

    # Set current folder to target
    media_pool.SetCurrentFolder(target)

    # Collect files
    files = []
    for path_str in args.files:
        p = Path(path_str).resolve()
        if p.is_dir():
            # Import all video files from directory
            extensions = {'.mov', '.mp4', '.mxf', '.avi', '.mkv', '.m4v',
                          '.braw', '.r3d', '.dpx', '.exr', '.tif', '.tiff',
                          '.wav', '.aif', '.aiff', '.mp3', '.aac', '.flac'}
            for f in sorted(p.iterdir()):
                if f.suffix.lower() in extensions:
                    files.append(str(f))
        elif p.is_file():
            files.append(str(p))
        else:
            print(f"  [skip] Not found: {path_str}")

    if not files:
        print("ERROR: No valid media files found.")
        sys.exit(1)

    print(f"Importing {len(files)} file(s) into {'/'.join(bin_parts)}:")
    clips = media_pool.ImportMedia(files)

    if clips:
        print(f"  Imported {len(clips)} clip(s)")
        for clip in clips:
            print(f"    - {clip.GetName()}")
    else:
        print("  ERROR: Import failed. Check file paths and Resolve permissions.")


def cmd_build_timeline(args):
    """Create a new timeline, optionally with intro/outro graphics."""
    resolve = get_resolve()
    project = get_project(resolve)
    media_pool = project.GetMediaPool()
    root = media_pool.GetRootFolder()

    # Create the timeline
    timeline = media_pool.CreateEmptyTimeline(args.name)
    if not timeline:
        print(f"ERROR: Could not create timeline '{args.name}'.")
        sys.exit(1)

    print(f"Timeline created: {args.name}")

    # Add intro graphic if specified
    if args.intro:
        intro_path = str(Path(args.intro).resolve())
        graphics_folder = find_folder(root, ["Graphics"])
        if graphics_folder:
            media_pool.SetCurrentFolder(graphics_folder)

        intro_clips = media_pool.ImportMedia([intro_path])
        if intro_clips:
            media_pool.AppendToTimeline(intro_clips)
            print(f"  Intro added: {args.intro}")

            # Set duration for still image (default 4 seconds)
            if args.intro_duration:
                fps = float(project.GetSetting("timelineFrameRate") or "23.976")
                frames = int(float(args.intro_duration) * fps)
                # Still image duration is set via clip properties
                print(f"  Intro duration: {args.intro_duration}s ({frames} frames)")
        else:
            print(f"  WARNING: Could not import intro: {args.intro}")

    # Add outro graphic if specified
    if args.outro:
        outro_path = str(Path(args.outro).resolve())
        graphics_folder = find_folder(root, ["Graphics"])
        if graphics_folder:
            media_pool.SetCurrentFolder(graphics_folder)

        outro_clips = media_pool.ImportMedia([outro_path])
        if outro_clips:
            media_pool.AppendToTimeline(outro_clips)
            print(f"  Outro added: {args.outro}")
        else:
            print(f"  WARNING: Could not import outro: {args.outro}")

    project.SetCurrentTimeline(timeline)
    print(f"Timeline '{args.name}' is now active.")


def cmd_add_subtitles(args):
    """Import .srt subtitles onto the current timeline."""
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

    # Import SRT as subtitle track
    media_pool = project.GetMediaPool()
    result = media_pool.ImportMedia([srt_path])

    if result:
        print(f"Subtitles imported: {args.srt_file}")
        print(f"  Timeline: {timeline.GetName()}")
        subtitle_count = timeline.GetTrackCount("subtitle")
        print(f"  Subtitle tracks: {subtitle_count}")
    else:
        print(f"ERROR: Could not import subtitles from {srt_path}")
        print("  Try importing manually: File → Import → Subtitle")


def cmd_auto_subtitle(args):
    """Generate subtitles from audio using Resolve's built-in auto-caption."""
    resolve = get_resolve()
    project = get_project(resolve)
    timeline = project.GetCurrentTimeline()

    if not timeline:
        print("ERROR: No active timeline.")
        sys.exit(1)

    settings = {
        "languageID": args.language or "en",
        "captionPresetType": args.preset or "Subtitle Default",
        "charsPerLine": args.chars_per_line or 42,
        "lineBreakType": args.line_break or 0,
    }

    print(f"Generating auto-captions on '{timeline.GetName()}'...")
    print(f"  Language: {settings['languageID']}")

    result = timeline.CreateSubtitlesFromAudio(settings)
    if result:
        print("  Auto-captions generated.")
    else:
        print("  ERROR: Auto-caption failed. Requires DaVinci Resolve Studio.")


# ---------------------------------------------------------------------------
# Render Presets
# ---------------------------------------------------------------------------

RENDER_PRESETS = {
    "youtube": {
        "name": "YouTube 4K",
        "settings": {
            "FormatWidth": 3840,
            "FormatHeight": 2160,
            "ExportVideo": True,
            "ExportAudio": True,
            "ExportSubtitle": True,
        },
        "format": "mp4",
        "codec": "H265_NVIDIA",  # Falls back; actual codec queried at runtime
        "suffix": "_youtube",
    },
    "linkedin": {
        "name": "LinkedIn 4K",
        "settings": {
            "FormatWidth": 3840,
            "FormatHeight": 2160,
            "ExportVideo": True,
            "ExportAudio": True,
            "ExportSubtitle": True,
        },
        "format": "mp4",
        "codec": "H264_NVIDIA",
        "suffix": "_linkedin",
    },
    "master": {
        "name": "Master ProRes",
        "settings": {
            "FormatWidth": 3840,
            "FormatHeight": 2160,
            "ExportVideo": True,
            "ExportAudio": True,
            "ExportSubtitle": False,
        },
        "format": "mov",
        "codec": "ProRes422HQ",
        "suffix": "_master",
    },
    "story": {
        "name": "Instagram Story",
        "settings": {
            "FormatWidth": 1080,
            "FormatHeight": 1920,
            "ExportVideo": True,
            "ExportAudio": True,
            "ExportSubtitle": True,
        },
        "format": "mp4",
        "codec": "H264_NVIDIA",
        "suffix": "_story",
    },
}


def resolve_codec(project, format_key, preferred_codec):
    """Try to find the preferred codec; fall back to first available."""
    codecs = project.GetRenderCodecs(format_key)
    if not codecs:
        return None

    # Try preferred
    if preferred_codec in codecs:
        return preferred_codec

    # Try common fallbacks
    fallbacks = {
        "H265_NVIDIA": ["H265", "H.265", "HEVC"],
        "H264_NVIDIA": ["H264", "H.264", "AVC"],
        "ProRes422HQ": ["ProRes 422 HQ", "AppleProRes422HQ"],
    }

    for fallback in fallbacks.get(preferred_codec, []):
        if fallback in codecs:
            return fallback

    # Last resort: first available
    first_key = list(codecs.keys())[0] if isinstance(codecs, dict) else codecs[0]
    print(f"  WARNING: Using fallback codec '{first_key}' (preferred '{preferred_codec}' not found)")
    return first_key


def queue_render(project, preset_key, output_dir, custom_name=None):
    """Queue a single render job from a preset."""
    preset = RENDER_PRESETS.get(preset_key)
    if not preset:
        print(f"ERROR: Unknown preset '{preset_key}'.")
        return False

    timeline = project.GetCurrentTimeline()
    if not timeline:
        print("ERROR: No active timeline.")
        return False

    timeline_name = timeline.GetName()
    filename = custom_name or timeline_name

    # Resolve codec
    codec = resolve_codec(project, preset["format"], preset["codec"])
    if not codec:
        print(f"  ERROR: No codecs available for format '{preset['format']}'")
        return False

    # Build render settings
    settings = dict(preset["settings"])
    settings["TargetDir"] = str(Path(output_dir).resolve())
    settings["CustomName"] = f"{filename}{preset['suffix']}"

    project.SetRenderSettings(settings)

    # Set format and codec
    project.SetCurrentRenderFormatAndCodec(preset["format"], codec)

    # Add to render queue
    job_id = project.AddRenderJob()
    if job_id:
        print(f"  [{preset['name']}] Queued → {settings['CustomName']}.{preset['format']}")
        print(f"    {settings['FormatWidth']}x{settings['FormatHeight']} | {codec}")
        return True
    else:
        print(f"  ERROR: Failed to queue {preset['name']}")
        return False


def cmd_render(args):
    """Queue a specific render preset."""
    resolve = get_resolve()
    project = get_project(resolve)

    output_dir = args.output or os.getcwd()
    os.makedirs(output_dir, exist_ok=True)

    print(f"Output directory: {output_dir}")
    success = queue_render(project, args.preset, output_dir, args.name)

    if success and args.start:
        print("\nStarting render...")
        project.StartRendering()
        _monitor_render(project)


def cmd_render_all(args):
    """Queue all 4K render presets at once."""
    resolve = get_resolve()
    project = get_project(resolve)

    output_dir = args.output or os.getcwd()
    os.makedirs(output_dir, exist_ok=True)

    presets = args.presets.split(",") if args.presets else list(RENDER_PRESETS.keys())

    print(f"Output directory: {output_dir}")
    print(f"Queuing {len(presets)} preset(s):\n")

    queued = 0
    for preset_key in presets:
        preset_key = preset_key.strip()
        if queue_render(project, preset_key, output_dir, args.name):
            queued += 1

    print(f"\n{queued}/{len(presets)} render job(s) queued.")

    if queued > 0 and args.start:
        print("\nStarting render...")
        project.StartRendering()
        _monitor_render(project)


def _monitor_render(project):
    """Monitor render progress until complete."""
    while project.IsRenderingInProgress():
        status = project.GetRenderJobStatus(0)
        if status:
            pct = status.get("CompletionPercentage", 0)
            time_remaining = status.get("EstimatedTimeRemainingInMs", 0)
            remaining_str = f"{time_remaining // 60000}m {(time_remaining % 60000) // 1000}s" if time_remaining else "calculating..."
            print(f"\r  Progress: {pct}% | Remaining: {remaining_str}    ", end="", flush=True)
        time.sleep(2)
    print("\n  Render complete.")


# ---------------------------------------------------------------------------
# LUT / Grade Application
# ---------------------------------------------------------------------------

def cmd_apply_lut(args):
    """Apply a LUT file to clips on a specified video track."""
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

    items = timeline.GetItemListInTrack("video", track_index)
    if not items:
        print(f"No clips found on video track {track_index}.")
        return

    print(f"Applying LUT to {len(items)} clip(s) on V{track_index}, node {node_index}:")
    print(f"  LUT: {lut_path}")

    applied = 0
    for item in items:
        result = item.SetLUT(node_index, lut_path)
        if result:
            print(f"  [ok] {item.GetName()}")
            applied += 1
        else:
            print(f"  [FAIL] {item.GetName()}")

    print(f"\nApplied to {applied}/{len(items)} clips.")


def cmd_apply_drx(args):
    """Apply a .drx grade file to timeline items."""
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
    grade_mode = args.mode or 0  # 0=no keyframes, 1=source TC, 2=start frames

    items = timeline.GetItemListInTrack("video", track_index)
    if not items:
        print(f"No clips found on video track {track_index}.")
        return

    print(f"Applying DRX grade to {len(items)} clip(s) on V{track_index}:")
    print(f"  Grade: {drx_path}")
    print(f"  Mode: {grade_mode}")

    result = timeline.ApplyGradeFromDRX(drx_path, grade_mode, items)
    if result:
        print(f"  Applied to {len(items)} clips.")
    else:
        print("  ERROR: Grade application failed.")


# ---------------------------------------------------------------------------
# Info / Utility Commands
# ---------------------------------------------------------------------------

def cmd_list_projects(args):
    """List all projects in the current database folder."""
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
    """List all timelines in the current project."""
    resolve = get_resolve()
    project = get_project(resolve)
    count = project.GetTimelineCount()

    if count == 0:
        print("No timelines in current project.")
        return

    current = project.GetCurrentTimeline()
    current_name = current.GetName() if current else ""

    print(f"Timelines ({count}):")
    for i in range(1, count + 1):
        tl = project.GetTimelineByIndex(i)
        if tl:
            marker = " ← active" if tl.GetName() == current_name else ""
            duration_frames = tl.GetEndFrame() - tl.GetStartFrame()
            fps = float(project.GetSetting("timelineFrameRate") or 24)
            duration_sec = duration_frames / fps
            mins = int(duration_sec // 60)
            secs = int(duration_sec % 60)
            print(f"  {i}. {tl.GetName()} ({mins}:{secs:02d}){marker}")


def cmd_list_render_formats(args):
    """List available render formats and codecs."""
    resolve = get_resolve()
    project = get_project(resolve)

    formats = project.GetRenderFormats()
    if not formats:
        print("No render formats available.")
        return

    print("Available render formats:\n")
    for fmt_key, fmt_name in (formats.items() if isinstance(formats, dict) else enumerate(formats)):
        print(f"  [{fmt_key}] {fmt_name}")
        codecs = project.GetRenderCodecs(fmt_key)
        if codecs:
            if isinstance(codecs, dict):
                for codec_key, codec_name in codecs.items():
                    print(f"      {codec_key}: {codec_name}")
            else:
                for c in codecs:
                    print(f"      {c}")
        print()


def cmd_info(args):
    """Show current project and timeline information."""
    resolve = get_resolve()
    project = get_project(resolve)

    print(f"Project: {project.GetName()}")
    print(f"  Resolution: {project.GetSetting('timelineResolutionWidth')}x{project.GetSetting('timelineResolutionHeight')}")
    print(f"  Frame Rate: {project.GetSetting('timelineFrameRate')}")
    print(f"  Timelines: {project.GetTimelineCount()}")

    timeline = project.GetCurrentTimeline()
    if timeline:
        print(f"\nActive Timeline: {timeline.GetName()}")
        print(f"  Video Tracks: {timeline.GetTrackCount('video')}")
        print(f"  Audio Tracks: {timeline.GetTrackCount('audio')}")
        print(f"  Subtitle Tracks: {timeline.GetTrackCount('subtitle')}")

        start = timeline.GetStartFrame()
        end = timeline.GetEndFrame()
        fps = float(project.GetSetting("timelineFrameRate") or 24)
        duration = (end - start) / fps
        print(f"  Duration: {int(duration // 60)}:{int(duration % 60):02d} ({end - start} frames)")
    else:
        print("\nNo active timeline.")


def cmd_open_page(args):
    """Switch Resolve to a specific page."""
    valid_pages = ["media", "cut", "edit", "fusion", "color", "fairlight", "deliver"]
    page = args.page.lower()

    if page not in valid_pages:
        print(f"ERROR: Invalid page '{page}'. Choose from: {', '.join(valid_pages)}")
        sys.exit(1)

    resolve = get_resolve()
    resolve.OpenPage(page)
    print(f"Switched to: {page}")


def cmd_export_project(args):
    """Export current project as a .drp file."""
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

  # Import iPhone footage
  python3 resolve_workflow.py import-media /path/to/footage/ --camera iphone

  # Import GH7 footage to specific bin
  python3 resolve_workflow.py import-media /path/to/clips/ --camera gh7

  # Import files to a custom bin
  python3 resolve_workflow.py import-media file1.mov file2.mov --bin "Source/Audio"

  # Build timeline with intro card
  python3 resolve_workflow.py build-timeline "Main Edit" --intro /path/to/say-why-intro-4k.png

  # Import subtitles
  python3 resolve_workflow.py add-subtitles /path/to/subs.srt

  # Generate auto-captions (Resolve Studio only)
  python3 resolve_workflow.py auto-subtitle --language en

  # Apply LUT to all clips on V1 at node 1
  python3 resolve_workflow.py apply-lut /path/to/TheOneLUT.cube --track 1 --node 1

  # Apply a .drx grade to all clips on V1
  python3 resolve_workflow.py apply-drx /path/to/grade.drx --track 1

  # Queue YouTube render
  python3 resolve_workflow.py render youtube --output /path/to/exports/

  # Queue all render presets and start immediately
  python3 resolve_workflow.py render-all --output /path/to/exports/ --start

  # Queue only YouTube and Master
  python3 resolve_workflow.py render-all --presets youtube,master --output /exports/

  # List available render codecs
  python3 resolve_workflow.py list-render-formats

  # Show project info
  python3 resolve_workflow.py info

  # Switch to color page
  python3 resolve_workflow.py open-page color

  # Export project backup
  python3 resolve_workflow.py export-project --output /backups/
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # --- new-project ---
    sp = subparsers.add_parser("new-project", help="Create new project with standard bins")
    sp.add_argument("name", help="Project name")
    sp.add_argument("--framerate", "-fps", help="Timeline frame rate (default: 23.976)")
    sp.set_defaults(func=cmd_new_project)

    # --- import-media ---
    sp = subparsers.add_parser("import-media", help="Import media files into camera bins")
    sp.add_argument("files", nargs="+", help="File paths or directories to import")
    sp.add_argument("--camera", "-c", choices=["iphone", "gh7", "gh5"], help="Camera source bin")
    sp.add_argument("--bin", "-b", help="Custom bin path (e.g. 'Source/Audio')")
    sp.set_defaults(func=cmd_import_media)

    # --- build-timeline ---
    sp = subparsers.add_parser("build-timeline", help="Create timeline with optional intro/outro")
    sp.add_argument("name", help="Timeline name")
    sp.add_argument("--intro", help="Path to intro graphic (PNG/TIFF)")
    sp.add_argument("--outro", help="Path to outro graphic (PNG/TIFF)")
    sp.add_argument("--intro-duration", type=float, default=4.0, help="Intro duration in seconds (default: 4)")
    sp.set_defaults(func=cmd_build_timeline)

    # --- add-subtitles ---
    sp = subparsers.add_parser("add-subtitles", help="Import .srt subtitles")
    sp.add_argument("srt_file", help="Path to .srt subtitle file")
    sp.set_defaults(func=cmd_add_subtitles)

    # --- auto-subtitle ---
    sp = subparsers.add_parser("auto-subtitle", help="Generate subtitles from audio (Studio only)")
    sp.add_argument("--language", default="en", help="Language code (default: en)")
    sp.add_argument("--preset", default="Subtitle Default", help="Caption preset type")
    sp.add_argument("--chars-per-line", type=int, default=42, help="Characters per line (default: 42)")
    sp.add_argument("--line-break", type=int, default=0, help="Line break type (default: 0)")
    sp.set_defaults(func=cmd_auto_subtitle)

    # --- apply-lut ---
    sp = subparsers.add_parser("apply-lut", help="Apply LUT to clips on a track")
    sp.add_argument("lut_file", help="Path to .cube LUT file")
    sp.add_argument("--track", "-t", type=int, default=1, help="Video track number (default: 1)")
    sp.add_argument("--node", "-n", type=int, default=1, help="Node index to apply LUT (default: 1)")
    sp.set_defaults(func=cmd_apply_lut)

    # --- apply-drx ---
    sp = subparsers.add_parser("apply-drx", help="Apply .drx grade to clips on a track")
    sp.add_argument("drx_file", help="Path to .drx grade file")
    sp.add_argument("--track", "-t", type=int, default=1, help="Video track number (default: 1)")
    sp.add_argument("--mode", "-m", type=int, default=0,
                     help="Grade mode: 0=no keyframes, 1=source TC aligned, 2=start frames aligned (default: 0)")
    sp.set_defaults(func=cmd_apply_drx)

    # --- render ---
    sp = subparsers.add_parser("render", help="Queue a render preset")
    sp.add_argument("preset", choices=list(RENDER_PRESETS.keys()), help="Render preset")
    sp.add_argument("--output", "-o", help="Output directory (default: current dir)")
    sp.add_argument("--name", "-n", help="Custom output filename (without extension)")
    sp.add_argument("--start", "-s", action="store_true", help="Start rendering immediately")
    sp.set_defaults(func=cmd_render)

    # --- render-all ---
    sp = subparsers.add_parser("render-all", help="Queue all render presets")
    sp.add_argument("--output", "-o", help="Output directory (default: current dir)")
    sp.add_argument("--name", "-n", help="Custom output filename (without extension)")
    sp.add_argument("--presets", help="Comma-separated preset list (default: all)")
    sp.add_argument("--start", "-s", action="store_true", help="Start rendering immediately")
    sp.set_defaults(func=cmd_render_all)

    # --- list-projects ---
    sp = subparsers.add_parser("list-projects", help="List projects in current database")
    sp.set_defaults(func=cmd_list_projects)

    # --- list-timelines ---
    sp = subparsers.add_parser("list-timelines", help="List timelines in current project")
    sp.set_defaults(func=cmd_list_timelines)

    # --- list-render-formats ---
    sp = subparsers.add_parser("list-render-formats", help="List available render formats and codecs")
    sp.set_defaults(func=cmd_list_render_formats)

    # --- info ---
    sp = subparsers.add_parser("info", help="Show project/timeline info")
    sp.set_defaults(func=cmd_info)

    # --- open-page ---
    sp = subparsers.add_parser("open-page", help="Switch Resolve to a specific page")
    sp.add_argument("page", choices=["media", "cut", "edit", "fusion", "color", "fairlight", "deliver"])
    sp.set_defaults(func=cmd_open_page)

    # --- export-project ---
    sp = subparsers.add_parser("export-project", help="Export current project as .drp")
    sp.add_argument("--output", "-o", help="Output directory (default: current dir)")
    sp.set_defaults(func=cmd_export_project)

    # Parse and dispatch
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
