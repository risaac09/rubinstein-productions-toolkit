"""
rpresolve.mcp.tools_offline: tools that never connect to Resolve.

detect and measure read media headers and frames with ffprobe, ffmpeg and
exiftool; survey reads Resolve's project databases from disk (safe while
Resolve is open); endcheck and selects read a transcript, an approved text
and, for endcheck, the source audio; deliver_check reads a rendered file
with ffprobe and ffmpeg and checks it against a delivery destination.

Destinations come from production/resolve-config.json, with the overlay at
$RPRESOLVE_CONFIG laid over it when that is set (deliver_config()).

Every path argument must be absolute (the server's working directory is
not the caller's). Output files name client media and words, so they are
refused inside any git working tree; with no `out` given, a full result
that does not fit in one reply is written under default_out_dir().
"""

import json
import os
import tempfile
import time

from .. import cutlist, paths, workflows
from .. import detect as rpdetect
from ..config import ConfigError, load_config
from .registry import READ, Tool

MAX_PATHS = 500


def _abs(path, field):
    p = os.path.expanduser(path)
    if not os.path.isabs(p):
        raise ValueError(f"{field} must be an absolute path (got '{path}').")
    return p


def _existing(path, field):
    p = _abs(path, field)
    if not os.path.exists(p):
        raise ValueError(f"{field} {p} does not exist.")
    return p


def _out(args, field="out"):
    """The caller's output path, checked before any slow work, or None."""
    if not args.get(field):
        return None
    p = _abs(args[field], field)
    problem = paths.out_problem(p, any_git_tree=True)
    if problem:
        raise paths.OutputRefused(problem)
    return p


def _auto_out(stem, ext):
    """A new, unique file under default_out_dir()."""
    fd, p = tempfile.mkstemp(prefix=f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}-",
                             suffix=ext, dir=paths.default_out_dir())
    os.close(fd)
    return p


def _drop_empty(path):
    """Remove an auto-created output file that nothing was written to."""
    try:
        if path and os.path.getsize(path) == 0:
            os.remove(path)
    except OSError:
        pass


def _page(rows, args):
    offset, limit = args["offset"], args["limit"]
    page = rows[offset:offset + limit]
    nxt = offset + len(page)
    return {"total": len(rows), "offset": offset, "returned": len(page),
            "next_offset": nxt if nxt < len(rows) else None, "rows": page}


PAGING = {
    "offset": {"type": "integer", "minimum": 0, "default": 0,
               "description": "First row to return."},
}


def _limit(default, maximum):
    return {"type": "integer", "minimum": 1, "maximum": maximum, "default": default,
            "description": "Most rows to return in this reply."}


def _out_prop(what):
    return {"type": "string", "description": f"Absolute path to also write {what} to. "
            "Refused inside any git working tree: the output names client media."}


# ---------------------------------------------------------------------------
# detect
# ---------------------------------------------------------------------------

def detect(args, ctx):
    out = _out(args)
    targets = [_abs(p, "paths") for p in args["paths"]]
    ctx.check_cancel()
    r = workflows.detect(targets)
    rows = r["rows"]
    result = {"summary": r["summary"], "counts": r["counts"],
              "status": "review" if r["exit_status"] else "pinned", "missing": r["missing"]}
    result.update(_page(rows, args))
    if out or result["next_offset"] is not None:
        result["file"] = paths.write_private(out or _auto_out("detect", ".json"),
                                             rpdetect.format_json(rows), any_git_tree=True)
    return result


# ---------------------------------------------------------------------------
# survey
# ---------------------------------------------------------------------------

def survey(args, ctx):
    given = _out(args)
    if given and args["json"] and given.lower().endswith(".json"):
        raise ValueError("out is the Markdown report and the JSON goes beside it with a .json "
                         "suffix; give an out path that does not end in .json.")
    out = given or _auto_out("survey", ".md")
    json_out = os.path.splitext(out)[0] + ".json" if args["json"] else None
    try:
        if json_out:
            problem = paths.out_problem(json_out, any_git_tree=True)
            if problem:
                raise paths.OutputRefused(problem)
        ctx.check_cancel()
        r = workflows.survey(out, json_out, projects=args.get("projects"),
                             tree_labels=args.get("tree_labels"), capture=True,
                             timeout=args["timeout"])
    except BaseException:
        _drop_empty(None if given else out)
        raise
    ok = r["returncode"] == 0
    if not ok:
        _drop_empty(None if given else out)
    r["summary"] = (f"survey written to {out}" + (f" and {json_out}" if json_out else "")
                    if ok else f"survey failed (exit {r['returncode']}); see stderr_tail")
    r["ok"] = ok
    return r


# ---------------------------------------------------------------------------
# measure
# ---------------------------------------------------------------------------

FRAMES_INLINE = 30


def measure(args, ctx):
    try:
        from .. import measure as rpmeasure
    except ImportError as e:
        raise RuntimeError(f"measure needs numpy ({e}); run the server on /usr/bin/python3.")
    path = _existing(args["file"], "file")
    out = _out(args)
    segments = [rpmeasure.parse_segment(s) for s in args.get("segments") or []]
    hero = args.get("hero")
    if hero and hero not in [s[0] for s in segments]:
        raise ValueError(f"hero '{hero}' is not one of the segment labels.")
    report = rpmeasure.measure(path, samples=args["samples"], segments=segments, hero=hero,
                               faces=args["faces"], check_cancel=ctx.check_cancel,
                               progress=ctx.progress)
    file = paths.write_private(out or _auto_out("measure", ".json"),
                               _json(report), any_git_tree=True)
    result = {"summary": rpmeasure.format_summary(report).strip(), "file": file}
    result["report"] = {k: v for k, v in report.items() if k != "frames"}
    if len(report["frames"]) <= FRAMES_INLINE:
        result["report"]["frames"] = report["frames"]
    else:
        result["frames_note"] = (f"{len(report['frames'])} per-frame results are in {file}, "
                                 "not inline.")
    return result


def _json(obj):
    return json.dumps(obj, indent=1, default=str) + "\n"


# ---------------------------------------------------------------------------
# endcheck
# ---------------------------------------------------------------------------

def endcheck(args, ctx):
    manifest = cutlist.load_manifest(_existing(args["manifest"], "manifest"))
    out = _out(args)
    only = args.get("clips")
    if only:
        names = {c["name"] for c in manifest["clips"]}
        unknown = sorted(set(only) - names)
        if unknown:
            raise ValueError(f"not in the manifest: {', '.join(unknown)}; "
                             f"it has {', '.join(sorted(names))}.")
        manifest = {**manifest, "clips": [c for c in manifest["clips"] if c["name"] in only]}
    rows = cutlist.endcheck(manifest, audio=args["audio"], check_cancel=ctx.check_cancel,
                            progress=ctx.progress)
    n = cutlist.endcheck_counts(rows)
    verdict = "fail" if n["fail"] else ("review" if n["review"] else "pass")
    result = {"summary": (f"endcheck: {len(rows)} span(s): {n['pass']} pass, {n['review']} "
                          f"review, {n['fail']} fail" +
                          ("" if args["audio"] else " (audio not checked)")),
              "verdict": verdict, "counts": n}
    result.update(_page(rows, args))
    result["lines"] = [cutlist.endcheck_line(r) for r in result["rows"]]
    if out or result["next_offset"] is not None:
        result["file"] = paths.write_private(out or _auto_out("endcheck", ".json"),
                                             _json(rows), any_git_tree=True)
    return result


# ---------------------------------------------------------------------------
# selects
# ---------------------------------------------------------------------------

def selects(args, ctx):
    if args["min_seconds"] >= args["max_seconds"]:
        raise ValueError("min_seconds must be less than max_seconds.")
    words = cutlist.load_words(_existing(args["words"], "words"))
    approved = cutlist.load_approved(_existing(args["approved"], "approved"))
    out = _out(args)
    ctx.check_cancel()
    rows = cutlist.selects(words, approved, args["min_seconds"], args["max_seconds"])
    result = {"summary": f"selects: {len(rows)} proposal(s) of {args['min_seconds']:g} to "
                         f"{args['max_seconds']:g} s inside the approved text"}
    result.update(_page(rows, args))
    if out or result["next_offset"] is not None:
        result["file"] = paths.write_private(out or _auto_out("selects", ".tsv"),
                                             cutlist.selects_tsv(rows), any_git_tree=True)
    return result


# ---------------------------------------------------------------------------
# deliver_check
# ---------------------------------------------------------------------------

def deliver_config():
    """resolve-config.json with the overlay at $RPRESOLVE_CONFIG, if set.
    A named overlay that is missing, unreadable or not a JSON object raises
    ValueError (never a quiet fall back to the defaults), as does a
    resolve-config.json that does not parse."""
    path = os.environ.get("RPRESOLVE_CONFIG")
    if path and not os.path.isfile(path):
        raise ValueError(f"RPRESOLVE_CONFIG names {path}, which is not a file.")
    try:
        return load_config(path or None, strict=True)
    except ConfigError as e:
        raise ValueError(f"RPRESOLVE_CONFIG: {e}" if path else str(e))


def destination_keys():
    """For the schemas: the configured destinations, or the defaults when
    the overlay cannot be read (the handler then reports why)."""
    from .. import deliver
    try:
        return deliver.destination_keys(deliver_config())
    except ValueError:
        return deliver.destination_keys(load_config())


def deliver_check(args, ctx):
    from .. import deliver, delivercheck as dc
    path = _existing(args["file"], "file")
    dest = deliver.destination(deliver_config(), args["destination"])
    size = None
    if args.get("size"):
        w, h = args["size"].split("x")
        size = (int(w), int(h))
    if args.get("fps"):
        dc.parse_fps(args["fps"])
    ctx.check_cancel()
    r = dc.check(path, dest, fps=args.get("fps"), size=size, loudness=args["loudness"])
    c = r["counts"]
    bad = [x["check"] for x in r["checks"] if x["status"] == dc.FAIL]
    r["summary"] = (f"deliver_check {os.path.basename(path)} as {dest['key']}: "
                    f"{c[dc.PASS]} pass, {c[dc.FAIL]} fail, {c[dc.SKIP]} skipped" +
                    (f"; FAILED: {', '.join(bad)}" if bad else "; every asserted rule passes"))
    return r


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------

def _obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required),
            "additionalProperties": False}


def register(registry):
    registry.add(Tool(
        "detect",
        "Classify media files by camera and picture profile from their headers (ffprobe, "
        "exiftool) and propose each one's Input Color Space and Data Level. Directories are "
        "searched recursively. A row is 'pinned' at high or medium confidence, else 'review'. "
        "Never connects to Resolve.",
        _obj({"paths": {"type": "array", "minItems": 1, "maxItems": MAX_PATHS,
                        "items": {"type": "string"},
                        "description": "Absolute paths of media files or folders."},
              **PAGING, "limit": _limit(100, 500),
              "out": _out_prop("every row as JSON")}, ["paths"]),
        detect, title="Detect camera profiles", annotations=READ))
    registry.add(Tool(
        "survey",
        "Survey Resolve's projects from the project databases on disk: timelines, media, "
        "input colour spaces, LUTs and grades per project, as a Markdown report (and JSON). "
        "Never connects to Resolve, so it is safe while Resolve is open. Can take minutes; "
        "returns the report's path.",
        _obj({"projects": {"type": "array", "items": {"type": "string"},
                           "description": "Only these project names (default: all)."},
              "tree_labels": {"type": "array", "items": {"type": "string"},
                              "description": "Only projects under these library labels."},
              "out": _out_prop("the Markdown report (the JSON goes beside it)"),
              "json": {"type": "boolean", "default": True,
                       "description": "Also write the survey as JSON."},
              "timeout": {"type": "integer", "minimum": 60, "maximum": 7200, "default": 1800,
                          "description": "Seconds before the survey is stopped."}}),
        survey, title="Survey Resolve projects", annotations=READ))
    registry.add(Tool(
        "measure",
        "Measure a rendered or source video file: luma percentiles, clipping, 8-bit legal "
        "range, skin tone in faces found by macOS Vision (Lab, hue off the skin line), and "
        "optionally how well labelled camera segments match a hero camera (Delta E 2000). "
        "Samples evenly spaced frames. The full report is written to a file.",
        _obj({"file": {"type": "string", "description": "Absolute path of the video."},
              "samples": {"type": "integer", "minimum": 1, "maximum": 120, "default": 10,
                          "description": "Frames to sample."},
              "segments": {"type": "array",
                           "items": {"type": "string", "pattern": r"^[^=]+=[\d.]+-[\d.]+$"},
                           "description": "Camera segments as 'label=start-end' in seconds."},
              "hero": {"type": "string", "description": "The segment label to match to."},
              "faces": {"type": "boolean", "default": True,
                        "description": "Find faces and measure skin."},
              "out": _out_prop("the full JSON report")}, ["file"]),
        measure, title="Measure a video", annotations=READ))
    registry.add(Tool(
        "endcheck",
        "Check a cut manifest's spans: does each out-point land in a real pause (source audio), "
        "on the intended end words (word-level transcript), and inside the approved text? "
        "Verdict pass, review (a paraphrase to read) or fail, with a suggested clean out-point "
        "when one is near. Changes nothing.",
        _obj({"manifest": {"type": "string", "description": "Absolute path of the manifest JSON."},
              "clips": {"type": "array", "items": {"type": "string"},
                        "description": "Only these clip names."},
              "audio": {"type": "boolean", "default": True,
                        "description": "Check the source audio (needs the source file)."},
              **PAGING, "limit": _limit(50, 200),
              "out": _out_prop("every row as JSON")}, ["manifest"]),
        endcheck, title="Check cut end points", annotations=READ))
    registry.add(Tool(
        "selects",
        "Propose clip spans from a word-level transcript that stay inside an approved text "
        "(an essay or script): runs of whole sentences between min_seconds and max_seconds, "
        "best coverage first, non-overlapping. Proposals only; nothing is cut.",
        _obj({"words": {"type": "string",
                        "description": "Absolute path of the Whisper JSON with word timestamps."},
              "approved": {"type": "string",
                           "description": "Absolute path of the approved text (Markdown)."},
              "min_seconds": {"type": "number", "minimum": 1, "default": 20},
              "max_seconds": {"type": "number", "minimum": 2, "maximum": 600, "default": 65},
              **PAGING, "limit": _limit(20, 200),
              "out": _out_prop("every proposal as TSV")}, ["words", "approved"]),
        selects, title="Propose selects", annotations=READ))
    registry.add(Tool(
        "deliver_check",
        "Check a rendered deliverable against a delivery destination from resolve-config.json, "
        "offline with ffprobe and ffmpeg: file name rule, container, video codec, size, fps "
        "as an exact rational, pixel format, colour tags, audio codec, channels and sample "
        "rate, captions (a sidecar .srt that parses, or none at all), and integrated loudness "
        "and true peak. Every rule is PASS, FAIL or SKIP with what was found and expected. "
        "Reads the whole file for loudness; set loudness false to skip that. Never connects "
        "to Resolve.",
        _obj({"file": {"type": "string", "description": "Absolute path of the rendered file."},
              "destination": {"type": "string", "enum": destination_keys(),
                              "description": "The destination it was rendered for."},
              "fps": {"type": "string", "pattern": r"^\d+(\.\d+)?(/\d+)?$",
                      "description": "The timeline's frame rate to assert (23.976, 25, "
                      "24000/1001)."},
              "size": {"type": "string", "pattern": r"^\d+x\d+$",
                       "description": "WIDTHxHEIGHT to assert for a destination that renders "
                       "at the timeline's size."},
              "loudness": {"type": "boolean", "default": True,
                           "description": "Measure loudness (reads the whole file)."}},
             ["file", "destination"]),
        deliver_check, title="Check a deliverable", annotations=READ))
