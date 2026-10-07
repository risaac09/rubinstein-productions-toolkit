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
    measure               Luma, clipping, legal range and skin numbers from a
                          render; camera-match numbers per segment
    manifest              Build a cut manifest from a CUTS script, a vertcut
                          TSV or a video-use edl.json
    endcheck              Check every cut's end word, that it sits in a pause
                          of the audio, and that it says only what the
                          approved text says
    selects               Propose spans that sit inside the approved text
    reframe-plan          A static crop per span for 9:16 and 1:1, centred on
                          the speaker's face (macOS Vision) and checked on
                          sampled frames; writes a manifest copy and a report
    deliver-check         Check a rendered file against its destination (codec,
                          size, fps, colour tags, audio, captions, loudness,
                          name); exit 0 pass, 1 fail, 2 tool missing
    deliver-captions      Turn Resolve's <stem>_<track>.ttml sidecar into a
                          zero-based <stem>.srt beside the render
    deliver-fix-loudness  Two-pass loudnorm to the destination's target, video
                          copied untouched, into <stem>.loudfix<ext>
    sync-measure          Where one recording sits against another from their
                          audio: offset in seconds and frames, clock drift,
                          confidence; refuses to call a weak match a match
    trim-review           Silence, filler and repeat review of an edit as a
                          TSV; deletes nothing

Retired: the commands that wrote to Resolve. Running one prints the MCP tool
to use instead (RETIRED below; the same table is in production/resolve-mcp.md).

Config:
    deliver-check, deliver-captions and deliver-fix-loudness read delivery
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
    "render-all": "use the MCP queue_render tool, once per preset or destination; a person "
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
        m = cutlist.load_manifest(args.manifest)
        report = reframe.plan(m, aspects=args.aspects, samples=args.samples,
                              per_second=args.per_second, speaker_x=args.speaker_x,
                              only=args.only, allow_bars=args.allow_bars,
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


# ---------------------------------------------------------------------------
# Sync measure and trim review
# ---------------------------------------------------------------------------

def _fps_arg(text):
    """--fps as a float (23.976, 25, 24000/1001); raises ValueError."""
    from rpresolve import delivercheck as dc
    return float(dc.parse_fps(text))


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
                    "the only write path, and a retired command prints the tool that replaced it.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List available render codecs (use this to fix resolve-config.json presets)
  python3 resolve_workflow.py list-render-formats

  # Show project info
  python3 resolve_workflow.py info

  # Survey every project's grades, timelines and media (offline, read-only)
  python3 resolve_workflow.py survey --out /path/outside/repo/survey.md --json /path/outside/repo/survey.json

  # Classify cameras and picture profiles offline; exits 2 if any row needs review
  python3 resolve_workflow.py detect /path/to/card/ --out detect.tsv

  # Measure a render; camera-match numbers for two cameras by time range
  python3 resolve_workflow.py measure render.mov --segment A=0-30 --segment B=30-60 --hero A

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
    sp.add_argument("--only", nargs="+", metavar="CLIP", help="Only these clips")
    sp.add_argument("--allow-bars", action="store_true",
                    help="Keep the default scale when it holds the face but the picture does "
                         "not fill the frame (black bars); such crops are named 'bars'")
    sp.add_argument("--out", required=True,
                    help="The manifest copy with per-span reframes (refused inside this repo); "
                         "the report goes beside it as <stem>.reframe.tsv and .json")
    sp.set_defaults(func=cmd_reframe_plan)

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
