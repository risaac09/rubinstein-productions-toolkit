#!/usr/bin/env python3
"""
resolve_workflow.py: the read-only and offline commands for DaVinci Resolve work.

This script does not write to Resolve. Writes go through the MCP server,
production/resolve_mcp.py, which imports media, builds [auto] timelines, grades
them, queues render jobs, makes captions and trim-review markers, and stacks
dual-system sound: a dry run by default, a plan_sha for the real run, and a
journal line for every write. It creates no project and starts no render:
projects are created by hand in Resolve, and a person starts each render on the
Deliver page. See production/resolve-mcp.md, which says what replaced each
retired command.

Usage:
    python3 resolve_workflow.py <command> [options]

    Imports the sibling rpresolve/ package, so run it from production/ or
    through a symlink; a copy of this file alone will not start.

Commands that read Resolve (Resolve Studio must be running; nothing is changed):
    list-projects         List all projects in current database
    list-timelines        List all timelines in current project
    list-render-formats   List available render formats and codecs
    info                  Show current project/timeline info

Commands that never connect to Resolve:
    survey                Read-only survey of every project on disk (runs
                          resolve_survey.py, no Resolve connection)
    detect                Classify camera and picture profile from file headers
                          (ffprobe, exiftool)
    shoot-table           One CSV row per clip: camera, start, duration, fps,
                          size, profile, lens, audio, with flags
    measure               Luma, clipping, legal range and skin numbers from a
                          render; camera-match numbers per segment
    perceive              One frame as a folder of scopes (waveform, parade,
                          vectorscope, histogram, false colour), a Lab palette
                          and numbers, optionally against a reference still
    pipeline-proof        Compare a test render with a reference render: per-frame
                          luma and chroma shift, legal or full range, PSNR, SSIM,
                          VMAF where ffmpeg has libvmaf; PASS or FAIL per row
    manifest              Build a cut manifest from a CUTS script, a vertcut
                          TSV or a video-use edl.json
    endcheck              Check every cut's end word, that it sits in a pause
                          of the audio, and that it says only what the
                          approved text says
    selects               Propose spans that sit inside the approved text
    reframe-plan          A static crop per span for 9:16 and 1:1, centred on
                          the speaker's face (macOS Vision) and checked on
                          sampled frames; writes a manifest copy and a report
    tilt-fixture          A short clip with markers, and the Pan, Tilt and Zoom to
                          type into Resolve, for the render that settles Tilt's sign
    tilt-check            Find the markers in the still you rendered and say whether
                          Pan and Tilt landed where reframe-plan's convention puts them
    deliver-check         Check a rendered file against its destination (codec,
                          size, fps, colour tags, audio, captions, loudness,
                          name); exit 0 pass, 1 fail, 2 tool missing
    qc                    One FAIL-first table for a rendered file: deliver-check's
                          rows plus ffmpeg blackdetect, freezedetect, scdet,
                          ebur128, astats and silencedetect; exit 0 pass, 1 fail,
                          2 tool missing
    deliver-captions      Turn Resolve's <stem>_<track>.ttml sidecar into a
                          zero-based <stem>.srt beside the render
    deliver-fix-loudness  Two-pass loudnorm to the destination's target, video
                          copied untouched, into <stem>.loudfix<ext>
    deliver-manifest      Write manifest.md and checksums.txt for a delivery folder
                          from ffprobe and the files' bytes; --verify re-checks
                          checksums.txt; refuses to overwrite without --overwrite
    sync-measure          Where one recording sits against another from their
                          audio: offset in seconds and frames, clock drift,
                          confidence; refuses to call a weak match a match
    trim-review           Silence, filler and repeat review of an edit as a
                          TSV; deletes nothing
    story-packet          A shoot as story material in one Markdown file: per
                          clip in capture order, the shoot-table row, an
                          audio energy line, the words as phrases, labels
    sting-sync            Does the intro's sound sting land on its picture
                          keyframes? Re-measures each hit from the audio;
                          signed offsets in frames and ms, FAIL first

Retired: the commands that wrote to Resolve. Running one exits 2 and says what
to use instead: the MCP tool, or what to do by hand in Resolve when no tool does
it (RETIRED below; the same table is in production/resolve-mcp.md).

Config:
    deliver-check, qc, deliver-captions and deliver-fix-loudness read delivery
    destinations from resolve-config.json (next to this script). An overlay
    kept outside any repository goes in with --config <path>.

Environment setup and Resolve's API limits: production/resolve-mcp.md.
"""

import sys
import os
import json
import argparse
import re

# rpresolve is a sibling package; put this file's real directory on the
# path so a symlinked or differently-cwd'd run still finds it. A lone copy
# of this file elsewhere (e.g. Resolve's Scripts menu) still needs the
# rpresolve/ directory copied next to it.
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
try:
    from rpresolve import api as rpapi  # noqa: E402
    from rpresolve import detect as rpdetect  # noqa: E402
    from rpresolve import shoot_table as rpshoot  # noqa: E402
    from rpresolve import config as rpconfig  # noqa: E402
    from rpresolve import paths as rppaths  # noqa: E402
    from rpresolve import workflows as rpwork  # noqa: E402
except ImportError as e:
    sys.exit(f"ERROR: cannot import rpresolve ({e}). Keep resolve_workflow.py next to its "
             "rpresolve/ directory (production/ in the toolkit), or symlink the script.")

# The survey decodes zstd blobs, which needs compression.zstd (Python 3.14+).
# This script runs on the system python3 (3.9), so survey runs the sibling
# resolve_survey.py as a subprocess under this interpreter instead.
SURVEY_PYTHON = rpwork.SURVEY_PYTHON
SURVEY_SCRIPT = rpwork.SURVEY_SCRIPT

# The commands that wrote to Resolve are gone. Running one exits 2 and says what to
# use instead. Most have an MCP tool; the BY_HAND ones have none, and the text says
# what to do in Resolve itself.
RETIRED = {
    "new-project": "create the project and set its colour science to DaVinci YRGB Color "
                   "Managed (the MCP ingest tool refuses any other)",
    "import-media": "use the MCP ingest tool",
    "build-timeline": "build the intro and outro timeline",
    "add-subtitles": "use the MCP create_captions tool on an [auto] timeline, or "
                     "File > Import > Subtitle in Resolve (Resolve 21 places no .srt from a script)",
    "auto-subtitle": "use the MCP create_captions tool",
    "render": "use the MCP queue_render tool; it queues the job and a person starts the "
              "render on the Deliver page",
    "render-all": "use the MCP queue_render tool, once per destination; a person "
                  "starts the renders on the Deliver page",
    "clear-queue": "remove the render jobs on the Deliver page",
    "apply-lut": "use the MCP apply_grade tool on an [auto] timeline",
    "apply-drx": "use the MCP apply_grade tool on an [auto] timeline (a .drx needs its "
                 "<name>.json label manifest beside it)",
    "open-page": "click the page tab",
    "export-project": "export the project from the Project Manager, or with File > Export "
                      "Project Archive",
    "ingest": "use the MCP ingest tool",
    "cut": "use the MCP cut tool",
    "deliver-queue": "use the MCP queue_render tool with a destination",
    "captions": "use the MCP create_captions tool",
    "sync": "use the MCP sync tool",
}
BY_HAND = frozenset({"new-project", "build-timeline", "clear-queue", "open-page",
                     "export-project"})
RETIRED_MARKERS = "use the MCP trim_review_markers tool"
# The flags trim-review lost with --markers. Only trim-review gets the tombstone for them.
RETIRED_MARKER_FLAGS = ("--markers", "--timeline", "--project", "--project-id", "--dry-run",
                        "--plan-sha")


# ---------------------------------------------------------------------------
# Pure logic — no Resolve dependency, unit-testable offline
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Info / Utility Commands (read-only)
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
    print("Pass the [format key] and a codec NAME (right column above) into a destination in resolve-config.json.")


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
        print(f"\nRender queue: {len(jobs)} job(s) pending (remove jobs on the Deliver page).")


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


_PROOF_TRAP = ("Resolve decodes log H.264 about 25 code values darker than ffmpeg. Never compare a "
               "Resolve render against an ffmpeg decode of a log source; the level-shift row would "
               "blame the pipeline for the decode. Compare render against render, or against "
               "Resolve's own decode exported as a file.")


def cmd_pipeline_proof(args):
    """Compare a test render with a reference (see rpresolve/proof.py). Exit
    0 on PASS, 1 on FAIL or an unreadable file."""
    try:
        from rpresolve import proof as rpproof
    except ImportError as e:
        print(f"ERROR: pipeline-proof needs numpy ({e}). Use /usr/bin/python3.", file=sys.stderr)
        return 1
    problem = _out_problem(args.json) if args.json else None
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    try:
        report = rpproof.compare(args.test, args.reference, frames=args.frames, thresholds={
            "psnr": args.psnr, "ssim": args.ssim, "vmaf": args.vmaf,
            "luma_shift": args.luma_shift, "chroma_shift": args.chroma_shift})
    except rpproof.MeasureError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    sys.stdout.write(rpproof.format_report(report))
    if args.json:
        rpproof.write_json(report, args.json)
    return 0 if report["passed"] else 1


def cmd_shoot_table(args):
    """Write the shoot table CSV (see rpresolve/shoot_table.py). Never
    connects to Resolve. Exit status: 0 clean, 2 when any row carries a
    flag, 1 on a usage error, missing tools or no file found."""
    problem = _out_problem(args.out) if args.out else None
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    offsets = {}
    for item in args.offset or []:
        cam, sep, secs = item.rpartition("=")
        try:
            offsets[cam] = float(secs)
        except ValueError:
            cam = ""
        if not sep or not cam:
            print(f"ERROR: --offset wants CAM=SECONDS (got '{item}')", file=sys.stderr)
            return 1
    try:
        rows, missing = rpshoot.build(args.paths, long_min=args.long_min, weak_db=args.weak_db,
                                      offsets=offsets)
    except rpdetect.ToolMissing as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    for m in missing:
        print(f"  [skip] Not found: {m}", file=sys.stderr)
    if not rows:
        print("ERROR: no video files found", file=sys.stderr)
        return 1
    bad = rpshoot.unknown_offsets(rows, offsets)
    if bad:
        print("ERROR: --offset names no camera in the table: " + ", ".join(bad)
              + " (cameras: " + ", ".join(sorted({r['camera'] for r in rows})) + ")", file=sys.stderr)
        return 1
    text = rpshoot.format_csv(rows)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Wrote {len(rows)} row(s) to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    print(rpshoot.summarize(rows), file=sys.stderr)
    return 2 if any(r["flags"] for r in rows) else 0


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


def cmd_reframe_plan(args):
    """Plan a static crop per span for the 9:16 and 1:1 versions, centred on
    the speaker's face, and check the face stays inside it on every sampled
    frame (see rpresolve/reframe.py). Writes a copy of the manifest with the
    per-span reframes (--out) and the report beside it (<out stem>.reframe.tsv
    and .json). Offline. Exit 0 every crop holds, 2 any span needs a person
    (manual reframe or split, no face, a crop kept with bars, or a flagged
    span: a choice of face, a face off its track, an unchecked sample, a
    span past the source's end), 1 error."""
    from rpresolve import cutlist, reframe
    out = os.path.abspath(args.out)
    problem = _out_problem(out)
    if not problem and os.path.realpath(args.manifest) == os.path.realpath(out):
        problem = f"{out} is the manifest itself; write the copy somewhere else."
    stem = os.path.splitext(out)[0] + ".reframe"
    for p in (stem + ".tsv", stem + ".json"):
        problem = problem or _out_problem(p)
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    try:
        m = cutlist.load_manifest(args.manifest, allow_no_speech=True)
        report = reframe.plan(m, aspects=args.aspects, samples=args.samples,
                              per_second=args.per_second, speaker_x=args.speaker_x,
                              speaker_y=getattr(args, "speaker_y", None),
                              only=args.only, allow_bars=args.allow_bars,
                              min_checked=getattr(args, "min_checked", reframe.MIN_CHECKED_SHARE),
                              subject_region=getattr(args, "subject_region", None),
                              min_face_height=getattr(args, "min_face_height", None),
                              prefer=getattr(args, "prefer", reframe.PREFER_PRESENT),
                              progress=lambda d, n: print(f"  span {d} of {n}", file=sys.stderr))
    except (cutlist.CutlistError, reframe.ReframeError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    for r in report["rows"]:
        print(reframe.line(r))
    _write_private(out, json.dumps(reframe.apply(m, report), indent=1) + "\n")
    tsv_path, json_path = reframe.write_report(stem, report, _write_private)
    print(reframe.summary(report), file=sys.stderr)
    print(f"Wrote {out}, {tsv_path} and {json_path}", file=sys.stderr)
    return 2 if reframe.needs_person(report["rows"]) else 0


TILT_STEPS = """\
Hand steps (only a person presses Render). In a scratch Resolve project:
  1. Make a timeline of {size} with input scaling set to {scaling_ui}.
  2. Import {video}; put it on the timeline.
  3. In the Inspector, set Zoom X and Zoom Y to {zoom}, Pan to {pan} and Tilt to {tilt}.
  4. Deliver ONE still of any frame (PNG or JPEG, {size} or any size of that shape).
  5. Run: python3 resolve_workflow.py tilt-check <the still> --fixture {folder}
White marker: the crop centre, so it must land at the middle of the frame. Red marker: the middle of
the source. Yellow marker: above the white one, so you can see which way is up.
Pan and Tilt were confirmed on 2026-10-15; a run now re-checks a new Resolve version."""


TILT_SCALING_UI = {"scaleToFit": "Scale entire image to fit (the default)",
                   "scaleToCrop": "Scale full frame with crop"}


def cmd_tilt_fixture(args):
    """Write the Tilt fixture clip and tilt-expect.json into --out (outside
    any repository) and print the hand steps. Needs ffmpeg."""
    from rpresolve import reframe
    folder = os.path.abspath(args.out)
    for name in (reframe.TILT_VIDEO, reframe.TILT_EXPECT_FILE):
        problem = _out_problem(os.path.join(folder, name))
        if problem:
            print(f"ERROR: {problem}", file=sys.stderr)
            return 1
    try:
        expect = reframe.tilt_fixture(folder, variant=getattr(args, "variant", "9x16"))
    except (reframe.ReframeError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    p = expect["props"]
    print(TILT_STEPS.format(video=os.path.join(folder, reframe.TILT_VIDEO), zoom=p["ZoomX"],
                            pan=p["Pan"], tilt=p["Tilt"], folder=folder,
                            size="x".join(str(v) for v in expect["out"]),
                            scaling_ui=TILT_SCALING_UI[expect["input_scaling"]]))
    return 0


def cmd_tilt_check(args):
    """Find the fixture's markers in a rendered still and say whether Pan and
    Tilt landed where reframe-plan's convention puts them. Writes
    tilt-result.json beside the fixture. Exit 0 both confirmed, 2 inverted or
    unclear, 1 error. It confirms nothing by itself: the constant that drops
    the unconfirmed note changes in a commit that cites this output."""
    from rpresolve import reframe
    folder = os.path.abspath(args.fixture)
    try:
        with open(os.path.join(folder, reframe.TILT_EXPECT_FILE), encoding="utf-8") as f:
            expect = json.load(f)
        w, h, rgb = reframe.read_rgb(os.path.abspath(args.still))
        result = reframe.tilt_check(rgb, (w, h), expect, tolerance=args.tolerance)
    except (reframe.ReframeError, OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    result["still"] = os.path.basename(args.still)
    result["input_scaling"] = expect.get("input_scaling")
    path = os.path.join(folder, reframe.TILT_RESULT_FILE)
    problem = _out_problem(path)
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    _write_private(path, json.dumps(result, indent=1) + "\n")
    f = result["found"]
    print(f"render {w}x{h}; white marker found at {f['a']}, expected {result['expected']['a']}; "
          f"red marker found at {f['b']}, expected {result['expected']['b']} "
          f"(tolerance {result['tolerance']:g} px)")
    print(f"Pan {result['pan'].upper()}, Tilt {result['tilt'].upper()}")
    for n in result["notes"]:
        print(f"  {n}")
    if result["ok"]:
        print("Both match this module's convention. To drop the unconfirmed note, change "
              "TILT_CONFIRMED in rpresolve/reframe.py in a commit that cites this result.")
    print(f"Wrote {path}", file=sys.stderr)
    return 0 if result["ok"] else 2


# ---------------------------------------------------------------------------
# Deliver: check the render, make its captions, fix its loudness
# ---------------------------------------------------------------------------

def _deliver_config(args):
    """The config for the deliver commands: --config given after the
    command or before it. A named file that is missing, unreadable or not a
    JSON object stops the command (exit 1); the lenient load_config would
    warn and carry on with the defaults, checking against the wrong rules."""
    path = getattr(args, "deliver_config", None) or args.config
    try:
        return rpconfig.load_config(path, strict=True)
    except rpconfig.ConfigError as e:
        raise SystemExit(f"ERROR: {e}")


def _size(text):
    """'3840x2160' -> (3840, 2160); raises ValueError."""
    m = re.fullmatch(r"(\d+)x(\d+)", str(text or "").strip())
    if not m:
        raise ValueError(f"--size must be WIDTHxHEIGHT, such as 3840x2160 (got {text!r}).")
    return int(m.group(1)), int(m.group(2))


def cmd_deliver_check(args):
    """Check a rendered file against its destination. Exit 0 all pass, 1 any
    fail (or an unreadable file), 2 ffprobe or ffmpeg missing."""
    from rpresolve import deliver, delivercheck as dc
    try:
        config = _deliver_config(args)
        dest = deliver.destination(config, args.dest)
        size = _size(args.size) if args.size else None
        if args.fps:
            dc.parse_fps(args.fps)
        r = dc.check(args.file, dest, fps=args.fps, size=size, loudness=not args.no_loudness,
                     folders=deliver.subfolders(config))
    except dc.ToolMissing as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except (dc.CheckError, deliver.DeliverError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(r, indent=1, default=str))
    else:
        sys.stdout.write(dc.format_report(r))
    return 0 if r["status"] == "pass" else 1


def cmd_overlay_check(args):
    """Check a graphic rendered with alpha (see rpresolve/overlay.py). Exit 0
    no FAIL row, 1 any FAIL (or an unreadable file), 2 ffprobe or ffmpeg missing."""
    try:
        from rpresolve import measure as rpmeasure, overlay
    except ImportError as e:
        print(f"ERROR: overlay-check needs numpy ({e}). Use /usr/bin/python3.", file=sys.stderr)
        return 1
    try:
        expect = [overlay.parse_expect(s) for s in args.expect or []]
        samples = [overlay.parse_sample(s) for s in args.sample or []]
        values = overlay.load_values(args.values) if args.values else None
        clear_at = [] if args.no_clear else (args.clear_at or [0.0])
        r = overlay.check(args.file, expect=expect, fps=args.fps, clear_at=clear_at,
                          hold=args.hold, alpha_mode=args.alpha_mode, values=values,
                          samples=samples,
                          fringe_max=overlay.FRINGE_MAX if args.fringe_max is None else args.fringe_max,
                          de_max=overlay.DE_MAX if args.de_max is None else args.de_max,
                          sample_size=overlay.SAMPLE_BOX if args.sample_box is None else args.sample_box)
    except overlay.ToolMissing as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except (rpmeasure.MeasureError, OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(r, indent=1))
    else:
        sys.stdout.write(overlay.format_report(r))
    return 0 if r["status"] == "pass" else 1


def cmd_qc(args):
    """The QC table: deliver-check's rows (with --dest) plus the ffmpeg
    rows. Exit 0 all pass, 1 any fail (or an unreadable file), 2 ffprobe or
    ffmpeg missing."""
    from rpresolve import deliver, delivercheck as dc, qc
    try:
        th = qc.parse_overrides(args.set)
        if (args.fps or args.size) and not args.dest:
            raise ValueError("--fps and --size assert against a destination: add --dest.")
        dest = folders = None
        if args.dest:
            config = _deliver_config(args)
            dest, folders = deliver.destination(config, args.dest), deliver.subfolders(config)
        size = _size(args.size) if args.size else None
        if args.fps:
            dc.parse_fps(args.fps)
        r = qc.run(args.file, dest, config_folders=folders, fps=args.fps, size=size,
                   expected_duration=args.duration,
                   shots=qc.shot_count(args.shots) if args.shots else None, thresholds=th)
    except dc.ToolMissing as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except (dc.CheckError, deliver.DeliverError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(r, indent=1, default=str))
    else:
        sys.stdout.write(qc.format_report(r))
    return 0 if r["status"] == "pass" else 1


def cmd_deliver_captions(args):
    """Turn Resolve's TTML sidecar beside a render into a zero-based
    <stem>.srt, read it back, and (unless --keep-ttml) move the TTML to
    ~/.Trash. Exit 0 written and verified, 1 refused or failed, 2 ffprobe
    missing."""
    from rpresolve import captions, deliver, delivercheck as dc
    try:
        dest = deliver.destination(_deliver_config(args), args.dest)
        r = captions.deliver_captions(args.file, dest, track=args.track,
                                      keep_ttml=args.keep_ttml)
    except dc.ToolMissing as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except (dc.CheckError, deliver.DeliverError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(r, indent=1, default=str))
    else:
        sys.stdout.write(captions.format_result(r))
    for w in r["warnings"]:
        print(f"  WARNING: {w}", file=sys.stderr)
    return 0 if r["status"] == "pass" else 1


def cmd_deliver_fix_loudness(args):
    """Normalise a render's loudness to its destination's target into
    <stem>.loudfix<ext>, then check it. --replace swaps it in only after
    the check passes, moving the original to ~/.Trash. Exit 0 the fixed
    file passes, 1 it does not (or the fix failed), 2 ffmpeg missing."""
    from rpresolve import deliver, delivercheck as dc
    try:
        dest = deliver.destination(_deliver_config(args), args.dest)
        size = _size(args.size) if args.size else None
        if args.fps:
            dc.parse_fps(args.fps)
        r = dc.fix_loudness(args.file, dest, replace=args.replace, fps=args.fps, size=size)
    except dc.ToolMissing as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except (dc.CheckError, deliver.DeliverError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(r, indent=1, default=str))
    else:
        m, out = r["measured"], r["result_loudnorm"]
        print(f"Measured:  I {m['input_i']} LUFS, true peak {m['input_tp']} dBTP, "
              f"LRA {m['input_lra']} LU")
        print(f"loudnorm:  {out.get('normalization_type')} mode, output I {out.get('output_i')} "
              f"LUFS, true peak {out.get('output_tp')} dBTP")
        sys.stdout.write(dc.format_report(r["check"]))
        print(f"Video stream bit-identical: {'yes' if r['video_identical'] else 'NO'}")
        if r["replaced"]:
            print(f"Replaced: {r['fixed']} (the original is in {r['trashed']})")
        else:
            print(f"Fixed file: {r['fixed']} (the original is untouched)")
    for w in r["warnings"]:
        print(f"  WARNING: {w}", file=sys.stderr)
    return 0 if r["status"] == "pass" else 1


def cmd_deliver_manifest(args):
    """Write manifest.md and checksums.txt at a delivery root, or with
    --verify re-check checksums.txt. Exit 0 written or all OK, 1 refused or
    a mismatch, 2 ffprobe or ffmpeg missing."""
    import time
    from rpresolve import deliver as rpdeliver, delivercheck as dc, manifest as mf
    try:
        if args.verify:
            results = mf.verify(args.folder)
            for status, rel in results:
                print(f"{rel}: {status}")
            return 0 if all(s == "OK" for s, _ in results) else 1
        missing = [f for f in ("project", "approval", "prepared_by", "review_copy",
                               "review_sha256") if not getattr(args, f)]
        if missing:
            raise mf.ManifestError("required to write a manifest: --" +
                                   ", --".join(m.replace("_", "-") for m in missing))
        dc.check_tools(ffmpeg=not args.no_loudness)
        text, sums = mf.build(
            args.folder, _deliver_config(args), args.project, args.approval, args.prepared_by,
            args.review_copy, args.review_sha256, args.delivered or time.strftime("%Y-%m-%d"),
            machine_steps=args.machine_steps, credits=args.credits,
            platform_notes=args.platform_notes, licences=args.licence,
            loudness=not args.no_loudness)
        paths = mf.write(args.folder, text, sums, overwrite=args.overwrite)
    except dc.ToolMissing as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except (mf.ManifestError, dc.CheckError, rpdeliver.DeliverError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print("Wrote " + ", ".join(paths))
    return 0


# ---------------------------------------------------------------------------
# Sync measure and trim review
# ---------------------------------------------------------------------------

def _fps_arg(text):
    """--fps as a float (23.976, 25, 24000/1001); raises ValueError."""
    from rpresolve import delivercheck as dc
    return float(dc.parse_fps(text))


def cmd_perceive(args):
    """Write a perception packet for one frame (see rpresolve/perceive.py).
    Exit 1 when the frame cannot be read or the folder is refused."""
    try:
        from rpresolve import measure as rpmeasure, perceive as rpperceive
    except ImportError as e:
        print(f"ERROR: perceive needs numpy ({e}). Use /usr/bin/python3.", file=sys.stderr)
        return 1
    try:
        packet = rpperceive.perceive(args.file, args.out, at=args.at, ref=args.ref,
                                     faces=not args.no_faces)
    except (rpmeasure.MeasureError, rpperceive.PerceiveError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    sys.stdout.write(rpperceive.format_summary(packet, args.out))
    return 0


def cmd_sync_measure(args):
    """Measure where <other> sits against <reference> from the sound both
    recorded (offline; rpresolve/sync.py). Exit 0 a match, 2 a match whose
    placement leaves either end of the overlap more than half a frame out
    (frame rounding plus half the drift; reported, never corrected), 1 no
    match or a file that cannot be read."""
    try:
        from rpresolve import sync as rpsync
    except ImportError as e:
        print(f"ERROR: sync-measure needs numpy ({e}). Use /usr/bin/python3.", file=sys.stderr)
        return 1
    try:
        fps = _fps_arg(args.fps) if args.fps else None
        r = rpsync.measure(args.reference, args.other, fps=fps, window_s=args.window,
                           ref_stream=args.ref_stream, other_stream=args.other_stream)
    except (rpsync.SyncError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(r, indent=1, default=str))
    else:
        sys.stdout.write(rpsync.format_summary(r))
    if not r["match"]:
        return 1
    return 2 if (r.get("drift") or {}).get("exceeds") else 0


def cmd_audio_report(args):
    """Loudness over time, spectrogram, peaks, clipping, clicks, silences and
    octave balance of one audio stream, as JSON and two PNGs in --out
    (offline; rpresolve/audioreport.py). Exit 0 the report is written,
    flags or not; 1 it could not be."""
    try:
        from rpresolve import audioreport as ar, deliver, delivercheck as dc
    except ImportError as e:
        print(f"ERROR: audio-report needs numpy ({e}). Use /usr/bin/python3.", file=sys.stderr)
        return 1
    try:
        th, target = {}, args.target
        if args.dest:
            loud = deliver.destination(_deliver_config(args), args.dest)["loudness"]
            th = {k: loud[k] for k in ("true_peak_max_dbtp", "tolerance_lu")}
            target = loud.get("integrated_lufs") if target is None else target
        start = ar.parse_time(args.start) if args.start else None
        end = ar.parse_time(args.end) if args.end else None
        r = ar.report(args.file, args.out, stream=args.stream, start=start, end=end,
                      reference=args.ref, ref_stream=args.ref_stream, target_lufs=target, th=th)
    except (ar.ReportError, dc.ToolMissing, deliver.DeliverError, ValueError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(r, indent=1))
    else:
        sys.stdout.write(ar.format_summary(r))
    return 0


def _review_opts(args):
    db = args.silence_db
    if db != "auto":
        try:
            db = float(db)
        except ValueError:
            raise ValueError(f"--silence-db takes dBFS (such as -45) or auto (got {db!r}).")
    return {"silence_db": db, "min_silence_s": args.min_silence, "tighten_s": args.tighten,
            "cut_s": args.cut, "soft": args.soft_fillers}


def cmd_trim_review(args):
    """Silence and filler review, offline, to a TSV (start, end, kind, text,
    confidence, suggestion) from a cut manifest or a whole source with its
    words JSON. Nothing is cut, rippled or deleted. Exit 0 done, 1 refused
    or failed."""
    from rpresolve import cutlist, trimreview as tr
    manifest = tr.is_manifest(args.input)
    try:
        opts = _review_opts(args)
        if manifest:
            m = cutlist.load_manifest(args.input)
            source = m["source_path"]
            words = cutlist.load_words(args.words or m["words"])
        else:
            source = args.input
            words = cutlist.load_words(args.words) if args.words else None
    except (cutlist.CutlistError, OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if not os.path.isfile(source):
        print(f"ERROR: the source {source} is not a file.", file=sys.stderr)
        return 1
    problem = _out_problem(args.out) if args.out else None
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    try:
        if manifest:
            r = tr.review_manifest(m, words, audio=not args.no_audio, **opts)
        else:
            r = tr.review_source(source, words, audio=not args.no_audio, **opts)
    except cutlist.CutlistError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    text = tr.tsv(r["rows"])
    if args.out:
        _write_private(args.out, text)
        print(f"Wrote {len(r['rows'])} row(s) to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    thr = sorted({t for t in r["thresholds"] if t is not None})
    print(tr.summary(r["rows"]) + (f" Silence below {', '.join(f'{t:g}' for t in thr)} dBFS."
                                   if thr else ""), file=sys.stderr)
    return 0


def cmd_sting_sync(args):
    """Sting timing against the lockup keyframes (VP-146). Exit 0 pass,
    1 any FAIL, 2 an input missing, unreadable or breaking the contract."""
    from rpresolve import stingsync as ss
    try:
        rows = ss.run(args.lockup, args.cues, master=args.wav, stem=args.transient, cut=args.cut,
                      lead_ms=args.lead_ms, lag_ms=args.lag_ms, tol_ms=args.tol_ms)
    except ss.StingError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(json.dumps(rows, indent=1) + "\n" if args.json else ss.format_table(rows))
    return int(any(r["status"] == ss.FAIL for r in rows))


def cmd_story_packet(args):
    """One Markdown packet from a shoot table, words and labels, for the
    selects board. Scores and chooses nothing. Exit 0 written, 1 failed."""
    from rpresolve import cutlist, storypacket as sp
    problem = _out_problem(args.out) if args.out else None
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    if args.words and not os.path.isdir(args.words):
        print(f"ERROR: --words {args.words} is not a folder.", file=sys.stderr)
        return 1
    if args.strips:
        # Strips show people: refused in any git tree, not only this one.
        problem = rppaths.out_problem(os.path.join(args.strips, "strip.jpg"), any_git_tree=True)
        if problem:
            print(f"ERROR: --strips: {problem}", file=sys.stderr)
            return 1
        os.makedirs(args.strips, exist_ok=True)
    try:
        text, summary = sp.build(args.table, args.words, args.labels, audio=not args.no_audio,
                                 title=args.title, strips_dir=args.strips,
                                 link_base=os.path.dirname(os.path.abspath(args.out))
                                 if args.out else None)
    except (sp.PacketError, cutlist.CutlistError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if args.out:
        _write_private(args.out, text)
        print(f"Wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    print(summary, file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# CLI Parser
# ---------------------------------------------------------------------------

class _Parser(argparse.ArgumentParser):
    """An argument parser that says what replaced a retired command or flag,
    instead of listing the choices that remain."""

    def error(self, message):
        m = re.search(r"invalid choice: '([^']*)'", message)
        if m and m.group(1) in RETIRED:
            name = m.group(1)
            how = (" and has no MCP tool; do it by hand in Resolve: "
                   if name in BY_HAND else ": ")
            self.exit(2, f"{self.prog}: '{name}' is retired{how}{RETIRED[name]}. See "
                         "production/resolve-mcp.md.\n")
        super().error(message)

    def parse_args(self, args=None, namespace=None):
        ns, extras = self.parse_known_args(args, namespace)
        if extras:
            # Only trim-review lost --markers and its flags; any other command that is
            # given one of them gets the ordinary unrecognized-arguments error.
            if getattr(ns, "command", None) == "trim-review" and any(
                    a.split("=", 1)[0] in RETIRED_MARKER_FLAGS for a in extras):
                self.exit(2, f"{self.prog}: trim-review no longer takes "
                             f"{', '.join(RETIRED_MARKER_FLAGS[:-1])} or "
                             f"{RETIRED_MARKER_FLAGS[-1]}: it writes the TSV only. To mark an "
                             f"[auto] timeline, {RETIRED_MARKERS}. See "
                             "production/resolve-mcp.md.\n")
            self.error("unrecognized arguments: " + " ".join(extras))
        return ns


def main():
    parser = _Parser(
        prog="resolve_workflow",
        description="Read-only and offline commands for DaVinci Resolve work. This script "
                    "does not write to Resolve: the MCP server (production/resolve_mcp.py) is "
                    "the only write path, and a retired command says what to use instead.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List available render codecs (use this to fix a destination's codec in resolve-config.json)
  python3 resolve_workflow.py list-render-formats

  # Show project info
  python3 resolve_workflow.py info

  # Survey every project's grades, timelines and media (offline, read-only)
  python3 resolve_workflow.py survey --out /path/outside/repo/survey.md --json /path/outside/repo/survey.json

  # Classify cameras and picture profiles offline; exits 2 if any row needs review
  python3 resolve_workflow.py detect /path/to/card/ --out detect.tsv

  # Measure a render; camera-match numbers for two cameras by time range
  python3 resolve_workflow.py measure render.mov --segment A=0-30 --segment B=30-60 --hero A

  # One frame as scopes and numbers, against a reference still
  python3 resolve_workflow.py perceive render.mov --at 12.5 --ref still.png --out /path/outside/repo/pkt

  # Reframes: plan and check a crop per span (the MCP cut tool builds the versions)
  python3 resolve_workflow.py reframe-plan manifest.json --out /path/outside/repo/manifest.reframed.json

  # After the render: captions to a zero-based .srt, loudness, then the check
  python3 resolve_workflow.py deliver-captions /path/to/renders/SW001_Guest_01_example-clip_16x9.mp4 \\
      --dest youtube_16x9
  python3 resolve_workflow.py deliver-fix-loudness /path/to/renders/SW001_Guest_01_example-clip_16x9.mp4 \\
      --dest youtube_16x9 --replace
  python3 resolve_workflow.py deliver-check /path/to/renders/SW001_Guest_01_example-clip_16x9.mp4 \\
      --dest youtube_16x9 --fps 23.976

  # Dual-system sound: where one recording sits against another (the MCP sync tool stacks them)
  python3 resolve_workflow.py sync-measure /path/to/A001.MOV /path/to/ZOOM0001.WAV

  # What a mix sounds like as numbers and pictures: loudness over time, spectrogram, flags
  python3 resolve_workflow.py audio-report /path/to/mix.wav -o /path/outside/repo/mix-report \\
      --dest youtube_16x9 --start 1:00 --end 2:30

  # Trim review: a TSV of proposals (nothing is cut)
  python3 resolve_workflow.py trim-review manifest.json --out /path/outside/repo/review.tsv
        """,
    )
    parser.add_argument("--config", help="Path to resolve-config.json (default: alongside this script)")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    sp = subparsers.add_parser("list-projects", help="List projects in current database")
    sp.set_defaults(func=cmd_list_projects)

    sp = subparsers.add_parser("list-timelines", help="List timelines in current project")
    sp.set_defaults(func=cmd_list_timelines)

    sp = subparsers.add_parser("list-render-formats", help="List available render formats and codecs")
    sp.set_defaults(func=cmd_list_render_formats)

    sp = subparsers.add_parser("info", help="Show project/timeline info")
    sp.set_defaults(func=cmd_info)

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
        "shoot-table", help="Offline: one CSV row per clip with flags for long, VFR and weak-audio takes")
    sp.add_argument("paths", nargs="+", help="Absolute paths of media files or folders (searched recursively)")
    sp.add_argument("--out", "-o", help="Write the CSV here instead of stdout")
    sp.add_argument("--offset", action="append", metavar="CAM=SECONDS",
                    help="Clock offset added to local_start for one camera name as detect names it (repeatable)")
    sp.add_argument("--long-min", type=float, default=rpshoot.LONG_TAKE_MIN,
                    help="Flag takes longer than this many minutes (default %(default)s)")
    sp.add_argument("--weak-db", type=float, default=rpshoot.WEAK_AUDIO_DB,
                    help="Flag audio whose mean volume is below this dB (default %(default)s)")
    sp.set_defaults(func=cmd_shoot_table)

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

    sp = subparsers.add_parser(
        "perceive", help="Offline: one frame as scopes, a Lab palette and numbers, for reading "
                         "light and colour")
    sp.add_argument("file", help="Video or still")
    sp.add_argument("--out", required=True,
                    help="A new folder for the packet (refused inside any git working tree)")
    sp.add_argument("--at", type=float, help="Frame time in seconds (default the middle)")
    sp.add_argument("--ref", help="A reference still (or the middle of a clip) to compare against")
    sp.add_argument("--no-faces", action="store_true", help="Skip face, skin and lighting-ratio numbers")
    sp.set_defaults(func=cmd_perceive)

    sp = subparsers.add_parser(
        "pipeline-proof", formatter_class=argparse.RawDescriptionHelpFormatter,
        help="Offline: compare a test render with a reference; PASS or FAIL per row",
        description="Compare a test render with a reference (another render, or Resolve's own "
                    "decode exported as a file). Rows: frames, range, gamma or level shift, luma "
                    "and chroma shift, PSNR, SSIM, VMAF (\"not available\" when ffmpeg has no "
                    "libvmaf). Exit 0 on PASS, 1 on FAIL.",
        epilog="KNOWN TRAP: " + _PROOF_TRAP)
    sp.add_argument("test", help="The render under test")
    sp.add_argument("reference", help="The reference render, or Resolve's decode exported as a file")
    sp.add_argument("--frames", type=int, default=300, help="Frames to compare from the start (default 300)")
    sp.add_argument("--psnr", type=float, help="Minimum PSNR in dB (default 45, for a flat clip)")
    sp.add_argument("--ssim", type=float, help="Minimum SSIM (default 0.99)")
    sp.add_argument("--vmaf", type=float, help="Minimum VMAF (default 95)")
    sp.add_argument("--luma-shift", type=float, help="Largest mean luma shift in 8-bit codes (default 1.5)")
    sp.add_argument("--chroma-shift", type=float, help="Largest mean chroma shift in 8-bit codes (default 1.5)")
    sp.add_argument("--json", help="Also write the full report, per-frame shifts included, here")
    sp.set_defaults(func=cmd_pipeline_proof)

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

    sp = subparsers.add_parser(
        "reframe-plan", help="Offline: a static crop per span for 9:16 and 1:1, centred on the "
        "face, checked on sampled frames")
    sp.add_argument("manifest")
    sp.add_argument("--aspects", nargs="+", choices=["9x16", "1x1"], default=["9x16", "1x1"],
                    help="Versions to plan (default both)")
    sp.add_argument("--samples", type=int, default=8,
                    help="Frames sampled per span (default 8; at least --per-second a second)")
    sp.add_argument("--per-second", type=float, default=0.5,
                    help="Fewest samples per second of a span (default 0.5)")
    sp.add_argument("--speaker-x", type=float,
                    help="The speaker's face x in source pixels: choose the face nearest it "
                         "(for a two-up call recording)")
    sp.add_argument("--speaker-y", type=float,
                    help="With --speaker-x, the face's y in source pixels (distance in both)")
    sp.add_argument("--subject-region", nargs=4, type=float, metavar=("X0", "Y0", "X1", "Y1"),
                    help="Only faces centred inside this source-pixel rectangle can be the "
                         "subject (leaves out an audience)")
    sp.add_argument("--min-face-height", type=float, metavar="SHARE",
                    help="Ignore faces shorter than this share of the frame height (0 to 1)")
    sp.add_argument("--min-checked", type=float, default=0.5, metavar="SHARE",  # reframe.MIN_CHECKED_SHARE
                    help="Share of a span's samples that must have a checked face before its "
                         "crop can pass (default 0.5, never fewer than 3)")
    sp.add_argument("--prefer", choices=["present", "largest"], default="present",
                    help="Which face is the subject when nothing names it: present in the most "
                         "samples (default) or the largest of those present in at least half as "
                         "many")
    sp.add_argument("--only", nargs="+", metavar="CLIP", help="Only these clips")
    sp.add_argument("--allow-bars", action="store_true",
                    help="Try the Ep 002 punch-in (2.25) first when a clip names no scale, and "
                         "keep it when it holds the face though the picture does not fill the "
                         "frame (black bars); such crops are named 'bars'")
    sp.add_argument("--out", required=True,
                    help="The manifest copy with per-span reframes (refused inside this repo); "
                         "the report goes beside it as <stem>.reframe.tsv and .json")
    sp.set_defaults(func=cmd_reframe_plan)

    sp = subparsers.add_parser(
        "tilt-fixture", help="Offline: write the clip and numbers for the render that settles "
        "Tilt's sign (needs ffmpeg); prints the hand steps")
    sp.add_argument("--out", required=True,
                    help="A new folder outside any repository for tilt-fixture.mp4 and tilt-expect.json")
    sp.add_argument("--variant", choices=("9x16", "master", "1x1", "9x16-6k", "master-fit",
                                          "16x9-crop"), default="9x16",
                    help="9x16: UHD into 1080x1920 at scale-to-fit (default). master: 6048x4032 "
                         "into 5952x2646 at scale-full-frame-with-crop, a festival project's setup. The "
                         "rest vary the shapes and scaling (reframe.TILT_VARIANTS)")
    sp.set_defaults(func=cmd_tilt_fixture)

    sp = subparsers.add_parser(
        "tilt-check", help="Offline: find the fixture's markers in the still you rendered and "
        "say whether Pan and Tilt landed where reframe-plan puts them (exit 0 both, 2 not)")
    sp.add_argument("still", help="The rendered still (PNG, JPEG, TIFF)")
    sp.add_argument("--fixture", required=True, help="The folder tilt-fixture wrote")
    sp.add_argument("--tolerance", type=float, metavar="PX",
                    help="Pixels the markers may be off (default 1%% of the render width, at least 4)")
    sp.set_defaults(func=cmd_tilt_check)

    def deliver_common(sp):
        sp.add_argument("--dest", required=True,
                        help="Destination key from resolve-config.json (youtube_16x9, "
                             "linkedin_9x16, client_master, ...)")
        sp.add_argument("--config", dest="deliver_config",
                        help="A config overlay kept outside this repo (may also go before "
                             "the command)")

    sp = subparsers.add_parser(
        "deliver-check", help="Offline: check a rendered file against its destination")
    sp.add_argument("file")
    deliver_common(sp)
    sp.add_argument("--fps", help="The timeline's frame rate to assert (23.976, 25, 24000/1001)")
    sp.add_argument("--size", help="WIDTHxHEIGHT to assert for a timeline-size destination")
    sp.add_argument("--no-loudness", action="store_true",
                    help="Skip the loudness read (it reads the whole file)")
    sp.add_argument("--json", action="store_true", help="Print the result as JSON")
    sp.set_defaults(func=cmd_deliver_check)

    sp = subparsers.add_parser(
        "qc", help="Offline: one FAIL-first QC table for a rendered file")
    sp.add_argument("file")
    sp.add_argument("--dest", help="Destination key from resolve-config.json; adds "
                    "deliver-check's rows and the loudness target")
    sp.add_argument("--config", dest="deliver_config",
                    help="A config overlay kept outside this repo (may also go before the command)")
    sp.add_argument("--fps", help="The timeline's frame rate to assert (with --dest)")
    sp.add_argument("--size", help="WIDTHxHEIGHT to assert for a timeline-size destination")
    sp.add_argument("--duration", type=float, metavar="SECONDS",
                    help="Expected duration (the lock); adds a duration row")
    sp.add_argument("--shots", metavar="CSV",
                    help="Shot list: a header row, then one row per shot; the cut count is checked "
                    "against rows minus one")
    sp.add_argument("--set", action="append", metavar="KEY=VALUE",
                    help="Override a threshold (repeatable); keys are in rpresolve/qc.py THRESHOLDS")
    sp.add_argument("--json", action="store_true", help="Print the result as JSON")
    sp.set_defaults(func=cmd_qc)

    sp = subparsers.add_parser(
        "overlay-check",
        help="Offline: alpha, tags, clear and opaque frames, edge fringe and brand colour "
             "of a graphic master")
    sp.add_argument("file")
    sp.add_argument("--expect", action="append", metavar="KEY=VALUE",
                    help="A tag the file must carry: color_range, color_space, color_primaries, "
                         "color_transfer or pix_fmt; repeat per tag")
    sp.add_argument("--fps", help="The frame rate to assert (23.976, 25, 24000/1001)")
    sp.add_argument("--clear-at", action="append", type=float, metavar="SECONDS",
                    help="A time with nothing visible; repeat (default the first frame)")
    sp.add_argument("--no-clear", action="store_true",
                    help="Skip the clear row, for a graphic that starts visible")
    sp.add_argument("--hold", type=float, metavar="SECONDS",
                    help="The time of the opaque, fringe and brand rows (default mid-file)")
    sp.add_argument("--alpha-mode", choices=("straight", "premultiplied"), default="straight",
                    help="How the timeline will read the alpha (default straight)")
    sp.add_argument("--values", help="JSON of name -> '#rrggbb', kept outside this repo")
    sp.add_argument("--sample", action="append", metavar="NAME=X,Y[@SECONDS]",
                    help="A pixel that must match the value NAME; repeat")
    sp.add_argument("--sample-box", type=int,
                    help="Side of the square each sample averages, odd; 1 for a hairline "
                         "(default: SAMPLE_BOX in rpresolve/overlay.py)")
    sp.add_argument("--fringe-max", type=float,
                    help="Edge error allowed, 10-bit codes at the median edge pixel "
                         "(default: FRINGE_MAX in rpresolve/overlay.py)")
    sp.add_argument("--de-max", type=float,
                    help="CIEDE2000 allowed at a brand sample (default: DE_MAX in "
                         "rpresolve/overlay.py)")
    sp.add_argument("--json", action="store_true", help="Print the result as JSON")
    sp.set_defaults(func=cmd_overlay_check)

    sp = subparsers.add_parser(
        "deliver-captions",
        help="Offline: Resolve's .ttml sidecar to a zero-based <stem>.srt beside the render")
    sp.add_argument("file")
    deliver_common(sp)
    sp.add_argument("--track", help="The subtitle track's sidecar to convert, when Resolve "
                                    "wrote more than one (<stem>_<track>.ttml)")
    sp.add_argument("--keep-ttml", action="store_true",
                    help="Leave the .ttml beside the file (default: moved to ~/.Trash)")
    sp.add_argument("--json", action="store_true", help="Print the result as JSON")
    sp.set_defaults(func=cmd_deliver_captions)

    sp = subparsers.add_parser(
        "deliver-fix-loudness", help="Offline: normalise loudness, video copied untouched")
    sp.add_argument("file")
    deliver_common(sp)
    sp.add_argument("--replace", action="store_true",
                    help="After the fixed file passes, move the original to ~/.Trash and give "
                         "the fix its name")
    sp.add_argument("--fps", help="The timeline's frame rate to assert in the check")
    sp.add_argument("--size", help="WIDTHxHEIGHT to assert for a timeline-size destination")
    sp.add_argument("--json", action="store_true", help="Print the result as JSON")
    sp.set_defaults(func=cmd_deliver_fix_loudness)

    sp = subparsers.add_parser(
        "deliver-manifest",
        help="Offline: write manifest.md and checksums.txt for a delivery folder, or --verify")
    sp.add_argument("folder", help="Delivery root: <folder>/<destination key>/<files>")
    sp.add_argument("--config", dest="deliver_config",
                    help="A config overlay kept outside this repo (may also go before "
                         "the command)")
    sp.add_argument("--verify", action="store_true",
                    help="Re-check checksums.txt against the files; writes nothing")
    sp.add_argument("--overwrite", action="store_true",
                    help="Replace an existing manifest.md and checksums.txt")
    sp.add_argument("--project", help="Project slug, as used in the file names")
    sp.add_argument("--approval", help="Approval reference id")
    sp.add_argument("--prepared-by", help="Name and role")
    sp.add_argument("--review-copy", help="File name of the approved review copy")
    sp.add_argument("--review-sha256", help="SHA-256 of the review copy, lowercase hex")
    sp.add_argument("--delivered", help="Delivery date (default: today)")
    sp.add_argument("--machine-steps", default="none stated")
    sp.add_argument("--credits", default="none requested")
    sp.add_argument("--platform-notes", default="none looked up")
    sp.add_argument("--licence", action="append", default=[], metavar="ASSET|LICENCE|ATTRIBUTION",
                    help="A licence row; repeat for more (default: none)")
    sp.add_argument("--no-loudness", action="store_true",
                    help="Record loudness as n/a (skips reading each file's audio)")
    sp.set_defaults(func=cmd_deliver_manifest)

    sp = subparsers.add_parser(
        "sync-measure", help="Offline: where one recording sits against another, from their "
        "audio (offset, drift, confidence)")
    sp.add_argument("reference", help="The reference: the camera clip")
    sp.add_argument("other", help="The other recording: an audio file or a second camera")
    sp.add_argument("--fps", help="Frame rate for the offset in frames (default: the "
                                  "reference's)")
    sp.add_argument("--window", type=float, default=30.0,
                    help="Seconds per measuring window (default 30)")
    sp.add_argument("--ref-stream", type=int, default=0, help="Audio stream of the reference "
                                                              "(0 is the first)")
    sp.add_argument("--other-stream", type=int, default=0, help="Audio stream of the other")
    sp.add_argument("--json", action="store_true", help="Print the whole report as JSON")
    sp.set_defaults(func=cmd_sync_measure)

    sp = subparsers.add_parser(
        "audio-report", help="Offline: loudness over time, spectrogram, peaks, clipping, clicks, "
        "silences and octave balance of one audio stream, as JSON and PNGs")
    sp.add_argument("file")
    sp.add_argument("-o", "--out", required=True,
                    help="Folder for audio-report.json, loudness.png and spectrogram.png, "
                         "outside any git tree (made if its parent exists)")
    sp.add_argument("--start", help="Where to start: seconds, m:ss or h:mm:ss (default 0)")
    sp.add_argument("--end", help="Where to stop (default: the end)")
    sp.add_argument("--stream", type=int, default=0, help="Audio stream (0 is the first)")
    sp.add_argument("--dest", help="Take the loudness target and true-peak ceiling from this "
                                   "destination in resolve-config.json")
    sp.add_argument("--config", dest="deliver_config",
                    help="A config overlay kept outside this repo (may also go before the "
                         "command)")
    sp.add_argument("--target", type=float, help="Integrated loudness target in LUFS "
                                                 "(over --dest's)")
    sp.add_argument("--ref", help="A reference file to compare with, read whole")
    sp.add_argument("--ref-stream", type=int, default=0, help="Audio stream of the reference")
    sp.add_argument("--json", action="store_true", help="Print the whole report as JSON")
    sp.set_defaults(func=cmd_audio_report)

    sp = subparsers.add_parser(
        "trim-review", help="Silence and filler review as a TSV; deletes nothing")
    sp.add_argument("input", help="A cut manifest (.json), or the source media file")
    sp.add_argument("--words", help="mlx_whisper word JSON (default: the manifest's); without "
                                    "it a source gets silences only")
    sp.add_argument("--out", help="Write the TSV here instead of stdout (refused in this repo)")
    sp.add_argument("--silence-db", default=str(-45.0),
                    help="Silence below this dBFS, or 'auto' (the quiet floor plus 10 dB); "
                         "default -45")
    sp.add_argument("--min-silence", type=float, default=0.8,
                    help="Shortest silence listed, seconds (default 0.8)")
    sp.add_argument("--tighten", type=float, default=1.2,
                    help="A silence this long suggests tighten (default 1.2 s)")
    sp.add_argument("--cut", type=float, default=2.5,
                    help="A silence this long suggests cut-candidate (default 2.5 s)")
    sp.add_argument("--soft-fillers", action="store_true",
                    help="Also list 'like', 'you know' and 'I mean' (low confidence, keep)")
    sp.add_argument("--no-audio", action="store_true", help="Words only: no silences")
    sp.set_defaults(func=cmd_trim_review)

    sp = subparsers.add_parser(
        "story-packet", help="Offline: a shoot as story material in one Markdown file")
    sp.add_argument("table", help="The shoot-table CSV (one row per clip)")
    sp.add_argument("--words", help="Folder of mlx_whisper word JSON, one <stem>.json per clip")
    sp.add_argument("--labels", help="JSONL of picture labels: file, start_s, end_s, text")
    sp.add_argument("--title", help="The packet's title (default: the table's name)")
    sp.add_argument("--out", help="Write the packet here instead of stdout (refused in this repo)")
    sp.add_argument("--no-audio", action="store_true", help="Skip the energy line (no decode)")
    sp.add_argument("--strips", help="Write a contact strip per clip (8 frames, 2 rows) into this folder "
                                     "and link it from each card (refused in any git tree)")
    sp.set_defaults(func=cmd_story_packet)

    sp = subparsers.add_parser(
        "sting-sync", help="Offline: does the sting's sound land on the lockup's picture keyframes")
    sp.add_argument("--lockup", required=True, help="lockup.json (one cut, or {cuts: {long, bumper}})")
    sp.add_argument("--cues", required=True, help="cues.json with measured hit onsets")
    sp.add_argument("--cut", choices=["long", "bumper"], help="Which cut (default: the cues' cut, else long)")
    sp.add_argument("--wav", help="The sting master; each hit is re-measured within 150 ms of its picture event")
    sp.add_argument("--transient", help="The transient stem; measured instead of the master when given")
    sp.add_argument("--lead-ms", type=float, default=40.0, help="Fail a lead over this (default 40)")
    sp.add_argument("--lag-ms", type=float, default=60.0, help="Fail a lag over this (default 60)")
    sp.add_argument("--tol-ms", type=float, default=1.0, help="Fail a declared onset this far off (default 1)")
    sp.add_argument("--json", action="store_true", help="Rows as JSON instead of the table")
    sp.set_defaults(func=cmd_sting_sync)

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
