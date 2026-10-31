"""
rpresolve.mcp.tools_offline: tools that never connect to Resolve.

detect and measure read media headers and frames with ffprobe, ffmpeg and
exiftool; survey reads Resolve's project databases from disk (safe while
Resolve is open); endcheck and selects read a transcript, an approved text
and, for endcheck, the source audio; deliver_check reads a rendered file
with ffprobe and ffmpeg and checks it against a delivery destination;
sync_measure correlates two recordings' audio; trim_review reads a source's
audio and its word JSON and writes a review TSV; shoot_table, qc,
pipeline_proof, overlay_check, perceive and audio_report read media with ffprobe
and ffmpeg (the last two write a packet or report folder); deliver_manifest
previews a delivery manifest and re-checks its checksums, writing nothing.

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
from .. import shoot_table as rpshoot
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
# reframe_plan
# ---------------------------------------------------------------------------

def _framing_plan(args, ctx):
    """reframe_plan with items: a framing plan for a timeline's items."""
    from .. import reframe
    if bool(args.get("items")) == bool(args.get("manifest")):
        raise ValueError("give a manifest (a cut's spans) or items (a timeline's clips): one "
                         "of them, not both and not neither")
    aspects = args.get("aspects") or [reframe.MASTER]
    if len(aspects) != 1:
        raise ValueError("a framing plan for items is for one shape; give one aspect")
    items = []
    for it in args["items"]:
        it = dict(it)
        it["path"] = _abs(it["path"], "items.path")
        if it.get("stills"):
            it["stills"] = [_existing(p, "items.stills") for p in it["stills"]]
        elif not os.path.isfile(it["path"]):
            raise ValueError(f"{it['path']} does not exist; give stills for a file that is "
                             "not on this machine or that ffmpeg cannot read (BRAW)")
        items.append(it)
    out = _out(args) or _auto_out("framing-plan", ".json")
    stem = os.path.splitext(out)[0]
    problem = paths.out_problem(stem + ".tsv", any_git_tree=True)
    if problem:
        raise paths.OutputRefused(problem)
    try:
        p = reframe.plan_items(items, aspect=aspects[0], master_size=args.get("master_size"),
                               scaling=args["input_scaling"], samples=args["samples"],
                               per_second=args["per_second"], min_checked=args["min_checked"],
                               subject_region=args.get("subject_region"),
                               min_face_height=args.get("min_face_height"),
                               prefer=args["prefer"], allow_bars=args["allow_bars"],
                               check_cancel=ctx.check_cancel)
    except BaseException:
        _drop_empty(None if args.get("out") else out)
        raise
    write = lambda q, text: paths.write_private(q, text, any_git_tree=True)  # noqa: E731
    write(out, json.dumps(p, indent=1) + "\n")
    write(stem + ".tsv", reframe.tsv(p["rows"]))
    n = p["counts"]
    person = reframe.needs_person(p["rows"])
    result = {"summary": (f"framing plan for {len(p['items'])} item(s) at {aspects[0]} "
                          f"{p['out'][0]}x{p['out'][1]}: {n['none']} need nothing, "
                          f"{n[reframe.PASS] - n['none']} pass with a move, "
                          f"{n[reframe.FALLBACK]} fallback, {n[reframe.MANUAL]} manual, "
                          f"{n[reframe.NO_FACE]} no face"),
              "counts": n, "verdict": "review" if person else "pass", "plan": out,
              "tsv": stem + ".tsv"}
    result.update(_page(p["rows"], args))
    result["lines"] = [reframe.line(r) for r in result["rows"]]
    return result


def reframe_plan(args, ctx):
    from .. import reframe
    if args.get("items") or not args.get("manifest"):
        return _framing_plan(args, ctx)
    args = dict(args, aspects=args.get("aspects") or ["9x16", "1x1"])
    if reframe.MASTER in args["aspects"] and not args.get("master_size"):
        raise ValueError("the master shape needs master_size: the timeline's width and height")
    manifest_path = _existing(args["manifest"], "manifest")
    m = cutlist.load_manifest(manifest_path, allow_no_speech=True)
    out = _out(args) or _auto_out("reframe-manifest", ".json")
    try:
        if os.path.realpath(manifest_path) == os.path.realpath(out):
            raise ValueError(f"out {out} is the manifest itself; give another path for the copy.")
        stem = os.path.splitext(out)[0] + ".reframe"
        for p in (stem + ".tsv", stem + ".json"):
            problem = paths.out_problem(p, any_git_tree=True)
            if problem:
                raise paths.OutputRefused(problem)
        report = reframe.plan(m, aspects=args["aspects"], samples=args["samples"],
                              per_second=args["per_second"], speaker_x=args.get("speaker_x"),
                              speaker_y=args.get("speaker_y"),
                              only=args.get("clips"), allow_bars=args["allow_bars"],
                              min_checked=args["min_checked"],
                              subject_region=args.get("subject_region"),
                              min_face_height=args.get("min_face_height"),
                              prefer=args["prefer"], master_size=args.get("master_size"),
                              scaling=args["input_scaling"], squeeze=args.get("squeeze"),
                              check_cancel=ctx.check_cancel,
                              progress=ctx.progress)
    except BaseException:
        _drop_empty(None if args.get("out") else out)
        raise
    write = lambda p, text: paths.write_private(p, text, any_git_tree=True)  # noqa: E731
    write(out, json.dumps(reframe.apply(m, report), indent=1) + "\n")
    tsv_path, json_path = reframe.write_report(stem, report, write)
    person = reframe.needs_person(report["rows"])
    result = {"summary": reframe.summary(report), "counts": report["counts"],
              "verdict": "review" if person else "pass", "rule": report["rule"],
              "manifest": out, "tsv": tsv_path, "report": json_path}
    result.update(_page(report["rows"], args))
    result["lines"] = [reframe.line(r) for r in result["rows"]]
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
    config = deliver_config()
    dest = deliver.destination(config, args["destination"])
    size = None
    if args.get("size"):
        w, h = args["size"].split("x")
        size = (int(w), int(h))
    if args.get("fps"):
        dc.parse_fps(args["fps"])
    ctx.check_cancel()
    r = dc.check(path, dest, fps=args.get("fps"), size=size, loudness=args["loudness"],
                 folders=deliver.subfolders(config))
    if args.get("audio_state"):
        from .. import audiostate
        dc.add_rows(r, audiostate.compare(
            audiostate.load(_existing(args["audio_state"], "audio_state")), dest))
    c = r["counts"]
    bad = [x["check"] for x in r["checks"] if x["status"] == dc.FAIL]
    r["summary"] = (f"deliver_check {os.path.basename(path)} as {dest['key']}: "
                    f"{c[dc.PASS]} pass, {c[dc.FAIL]} fail, {c[dc.SKIP]} skipped" +
                    (f"; FAILED: {', '.join(bad)}" if bad else "; every asserted rule passes"))
    return r


# ---------------------------------------------------------------------------
# sync_measure and trim_review
# ---------------------------------------------------------------------------

def sync_measure(args, ctx):
    try:
        from .. import sync as rpsync
    except ImportError as e:
        raise RuntimeError(f"sync_measure needs numpy ({e}); run the server on /usr/bin/python3.")
    ref = _existing(args["reference"], "reference")
    other = _existing(args["other"], "other")
    out = _out(args)
    ctx.check_cancel()
    r = rpsync.measure(ref, other, fps=args.get("fps"), window_s=args["window_s"],
                       check_cancel=ctx.check_cancel)
    result = {"summary": rpsync.format_summary(r).strip(), "match": r["match"],
              "offset_s": r["offset_s"], "frames": r["frames"], "drift": r["drift"],
              "reasons": r["reasons"], "report": r}
    if out:
        result["file"] = paths.write_private(out, _json(r), any_git_tree=True)
    return result


def review_opts(args):
    """trimreview keyword options from tool arguments."""
    return {"silence_db": "auto" if args["auto_threshold"] else args["silence_db"],
            "min_silence_s": args["min_silence_s"], "tighten_s": args["tighten_s"],
            "cut_s": args["cut_s"], "soft": args["soft_fillers"]}


def trim_review(args, ctx):
    from .. import trimreview as tr
    if bool(args.get("manifest")) == bool(args.get("source")):
        raise ValueError("give exactly one of manifest (a cut manifest) or source (a media file "
                         "with words).")
    out = _out(args)
    ctx.check_cancel()
    if args.get("manifest"):
        m = cutlist.load_manifest(_existing(args["manifest"], "manifest"))
        words = cutlist.load_words(_existing(args["words"], "words")) if args.get("words") else None
        r = tr.review_manifest(m, words, audio=args["audio"], **review_opts(args))
    else:
        source = _existing(args["source"], "source")
        words = cutlist.load_words(_existing(args["words"], "words")) if args.get("words") \
            else None
        r = tr.review_source(source, words, audio=args["audio"], **review_opts(args))
    rows = r["rows"]
    result = {"summary": tr.summary(rows), "counts": tr.counts(rows),
              "thresholds": sorted({t for t in r["thresholds"] if t is not None})}
    result.update(_page(rows, args))
    result["file"] = paths.write_private(out or _auto_out("trim-review", ".tsv"), tr.tsv(rows),
                                         any_git_tree=True)
    return result


REVIEW_PROPS = {
    "silence_db": {"type": "number", "maximum": 0, "default": -45.0,
                   "description": "Silence is audio below this dBFS (default -45)."},
    "auto_threshold": {"type": "boolean", "default": False,
                       "description": "Set the threshold from the recording instead: its "
                       "quietest tenth plus 10 dB, kept within -70..-30 dBFS."},
    "min_silence_s": {"type": "number", "minimum": 0.1, "maximum": 60, "default": 0.8,
                      "description": "Shortest silence listed, in seconds."},
    "tighten_s": {"type": "number", "minimum": 0.1, "maximum": 60, "default": 1.2,
                  "description": "A silence this long suggests tighten."},
    "cut_s": {"type": "number", "minimum": 0.1, "maximum": 600, "default": 2.5,
              "description": "A silence this long suggests cut-candidate."},
    "soft_fillers": {"type": "boolean", "default": False,
                     "description": "Also list 'like', 'you know' and 'I mean' (low "
                     "confidence, suggestion keep)."},
    "audio": {"type": "boolean", "default": True,
              "description": "Read the source audio for silences (false: words only)."},
}


# ---------------------------------------------------------------------------
# shoot_table, qc, pipeline_proof, deliver_manifest, perceive, audio_report,
# overlay_check
# ---------------------------------------------------------------------------

def _numpy(tool, e):
    return RuntimeError(f"{tool} needs numpy ({e}); run the server on /usr/bin/python3.")


def _parse_size(text):
    w, h = text.split("x")
    return int(w), int(h)


def shoot_table(args, ctx):
    out = _out(args)
    targets = [_abs(p, "paths") for p in args["paths"]]
    offsets = {}
    for item in args.get("camera_offset") or []:
        cam, sep, secs = item.rpartition("=")
        if not sep or not cam:
            raise ValueError(f"camera_offset wants CAM=SECONDS (got '{item}').")
        try:
            offsets[cam] = float(secs)
        except ValueError:
            raise ValueError(f"camera_offset wants CAM=SECONDS (got '{item}').")
    ctx.check_cancel()
    rows, missing = rpshoot.build(targets, long_min=args["long_min"], weak_db=args["weak_db"],
                                  offsets=offsets, audio_seconds=args["audio_seconds"])
    if not rows:
        raise ValueError("no video files found.")
    bad = rpshoot.unknown_offsets(rows, offsets)
    if bad:
        raise ValueError("camera_offset names no camera in the table: " + ", ".join(bad) +
                         " (cameras: " + ", ".join(sorted({r["camera"] for r in rows})) + ").")
    result = {"summary": rpshoot.summarize(rows), "missing": missing}
    result.update(_page(rows, args))
    if out or result["next_offset"] is not None:
        result["file"] = paths.write_private(out or _auto_out("shoot-table", ".csv"),
                                             rpshoot.format_csv(rows), any_git_tree=True)
    return result


def qc(args, ctx):
    from .. import deliver, delivercheck as dc, qc as rpqc
    path = _existing(args["file"], "file")
    th = rpqc.parse_overrides(args.get("set"))
    if (args.get("fps") or args.get("size")) and not args.get("destination"):
        raise ValueError("fps and size assert against a destination: give destination.")
    dest = folders = None
    if args.get("destination"):
        config = deliver_config()
        dest, folders = deliver.destination(config, args["destination"]), deliver.subfolders(config)
    if args.get("fps"):
        dc.parse_fps(args["fps"])
    shots = _existing(args["shots"], "shots") if args.get("shots") else None
    words = _existing(args["words"], "words") if args.get("words") else None
    stems = [_existing(s, "stems") for s in args.get("stems") or []] or None
    ctx.check_cancel()
    r = rpqc.run(path, dest, config_folders=folders, fps=args.get("fps"),
                 size=_parse_size(args["size"]) if args.get("size") else None,
                 expected_duration=args.get("duration"), thresholds=th,
                 shots=rpqc.shot_count(shots) if shots else None,
                 shot_cut_times=rpqc.shot_cuts(shots) if shots else None,
                 words=rpqc.load_words(words) if words else None, stems=stems,
                 sha256=args.get("sha256"))
    r["summary"] = rpqc.format_report(r).strip().splitlines()[-1]
    return r


def pipeline_proof(args, ctx):
    try:
        from .. import proof as rpproof
    except ImportError as e:
        raise _numpy("pipeline_proof", e)
    test, ref = _existing(args["test"], "test"), _existing(args["reference"], "reference")
    out = _out(args)
    ctx.check_cancel()
    r = rpproof.compare(test, ref, frames=args["frames"], thresholds={
        k: args.get(k) for k in ("psnr", "ssim", "vmaf", "luma_shift", "chroma_shift")})
    result = {"summary": rpproof.format_report(r).strip().splitlines()[-1], "passed": r["passed"],
              "rows": r["rows"]}
    if out:
        result["file"] = paths.write_private(out, _json(r), any_git_tree=True)
    return result


def deliver_manifest(args, ctx):
    """The manifest as text, or a checksums.txt re-check. Writes nothing."""
    from .. import delivercheck as dc, manifest as mf
    folder = _existing(args["folder"], "folder")
    ctx.check_cancel()
    if args["verify"]:
        rows = [{"status": s, "file": rel} for s, rel in mf.verify(folder)]
        bad = [r["file"] for r in rows if r["status"] != "OK"]
        return {"summary": f"checksums.txt: {len(rows)} file(s), " +
                (f"not OK: {', '.join(bad)}" if bad else "all OK"), "ok": not bad, "rows": rows}
    need = ("project", "approval", "prepared_by", "review_copy", "review_sha256")
    missing = [f for f in need if not args.get(f)]
    if missing:
        raise ValueError("a manifest preview needs " + ", ".join(missing) + ".")
    dc.check_tools(ffmpeg=args["loudness"])
    text, sums = mf.build(
        folder, deliver_config(), args["project"], args["approval"], args["prepared_by"],
        args["review_copy"], args["review_sha256"], args.get("delivered") or time.strftime("%Y-%m-%d"),
        machine_steps=args["machine_steps"], credits=args["credits"],
        platform_notes=args["platform_notes"], licences=args.get("licences") or [],
        loudness=args["loudness"])
    return {"summary": f"manifest.md for {len(sums)} file(s), not written; the CLI writes it "
                       "(deliver-manifest)", "manifest": text,
            "checksums": [{"file": rel, "sha256": h} for rel, h in sums]}


def perceive(args, ctx):
    try:
        from .. import perceive as rpperceive
    except ImportError as e:
        raise _numpy("perceive", e)
    path = _existing(args["file"], "file")
    ref = _existing(args["ref"], "ref") if args.get("ref") else None
    out = _abs(args["out"], "out")
    ctx.check_cancel()
    packet = rpperceive.perceive(path, out, at=args.get("at"), ref=ref, faces=args["faces"],
                                 with_targets=args["targets"])
    return {"summary": rpperceive.format_summary(packet, out).strip(), "folder": out,
            "packet": os.path.join(out, "packet.json")}


def audio_report(args, ctx):
    try:
        from .. import audioreport as ar
    except ImportError as e:
        raise _numpy("audio_report", e)
    from .. import deliver
    path = _existing(args["file"], "file")
    out = _abs(args["out"], "out")
    th, target = {}, args.get("target")
    if args.get("destination"):
        loud = deliver.destination(deliver_config(), args["destination"])["loudness"]
        th = {k: loud[k] for k in ("true_peak_max_dbtp", "tolerance_lu")}
        target = loud.get("integrated_lufs") if target is None else target
    start = ar.parse_time(args["start"]) if args.get("start") else None
    end = ar.parse_time(args["end"]) if args.get("end") else None
    ref = _existing(args["reference"], "reference") if args.get("reference") else None
    ctx.check_cancel()
    r = ar.report(path, out, stream=args["stream"], start=start, end=end, reference=ref,
                  ref_stream=args["ref_stream"], target_lufs=target, th=th)
    # The window-by-window curves are in audio-report.json, not in the reply.
    return {"summary": ar.format_summary(r).strip(), "flags": r["flags"], "images": r["images"],
            "file": os.path.join(out, "audio-report.json")}


def overlay_check(args, ctx):
    try:
        from .. import overlay
    except ImportError as e:
        raise _numpy("overlay_check", e)
    path = _existing(args["file"], "file")
    values = overlay.load_values(_existing(args["values"], "values")) if args.get("values") else None
    clear_at = [] if args["no_clear"] else args.get("clear_at", [0.0])
    ctx.check_cancel()
    r = overlay.check(
        path, expect=[overlay.parse_expect(s) for s in args.get("expect") or []],
        fps=args.get("fps"), clear_at=clear_at, hold=args.get("hold"),
        alpha_mode=args["alpha_mode"], values=values,
        samples=[overlay.parse_sample(s) for s in args.get("sample") or []],
        fringe_max=args.get("fringe_max", overlay.FRINGE_MAX),
        de_max=args.get("de_max", overlay.DE_MAX),
        sample_size=args.get("sample_box", overlay.SAMPLE_BOX))
    r["summary"] = overlay.format_report(r).strip().splitlines()[0]
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
        "reframe_plan",
        "Plan a static crop per span of a cut manifest for its 9:16, 1:1, 4:5 or 16:9 "
        "versions or the master shape, or (with items) a framing plan for the clips of a "
        "timeline that reframe_apply writes, centred on the subject's face, and check it: frames are sampled across each span (ffmpeg), "
        "faces found by macOS Vision and tracked across the samples, the primary face chosen "
        "(present in the most samples, then the larger; or the one nearest speaker_x and "
        "speaker_y; or, with prefer largest, the largest), the crop centred on its median "
        "centre inside the picture area, and every sampled face box (with a 10% margin, "
        "clipped to the picture) mapped through that crop must stay inside the frame. The "
        "scale is the clip's reframe.scale, else derived from the source and the aspect: the "
        "widest scale that fills the frame (a zoom of about 4 for a 5952x2646 source into "
        "9:16). With allow_bars and no clip scale the Ep 002 punch-in (2.25, calibrated on "
        "720p) is tried first, and kept when it holds the face though it leaves bars. A "
        "crop passes only when enough samples had a checked face (min_checked, default half, "
        "never fewer than 3): a face seen in one frame of fifteen is a manual span, not a "
        "pass. Where the footage has an audience, name the subject with subject_region or "
        "min_face_height. Footage with no speech needs a manifest with \"speech\": false and "
        "only source_path and clips (no words, approved text or hash); cut still needs a "
        "speech manifest. A span with no face gets no crop. Writes a copy of the manifest "
        "with per-span reframes (cut reads them) and a TSV and JSON report beside it. "
        "Heads: the face box stops at the brows, so the head's top is taken 0.6 face heights "
        "above it; the crop moves up, never down, to keep the whole head and 0.65 face "
        "heights of room above it where the picture has that room. A head past the top of "
        "the picture, or a crop that cannot give the room the picture holds, is a fallback "
        "with the reason. The master shape keeps the shot as shot (centre fill) and moves "
        "only for a head; 'move' says what changed (none, zoom, pan, tilt). Pan and Tilt "
        "are in Resolve's units, confirmed on six stills exported from Resolve 2026-10-15. An anamorphic source "
        "is planned desqueezed: squeeze from the manifest, the argument or the file's tag. "
        "Never connects to Resolve.",
        _obj({"manifest": {"type": "string",
                           "description": "Absolute path of the cut manifest (it is not changed). "
                           "Give this or items."},
              "items": {"type": "array", "minItems": 1, "items": {
                  "type": "object", "properties": {
                      "name": {"type": "string"},
                      "path": {"type": "string", "description": "The clip's file, absolute, as "
                               "Resolve's media pool names it (reframe_apply matches on it)."},
                      "in": {"type": "number", "minimum": 0, "description": "Source seconds."},
                      "out": {"type": "number", "minimum": 0, "description": "Source seconds."},
                      "squeeze": {"type": "number", "minimum": 0.5, "maximum": 3},
                      "stills": {"type": "array", "items": {"type": "string"},
                                 "description": "Images of the picture to judge in place of "
                                 "the file (BRAW, which ffmpeg cannot read): any size of the "
                                 "source's shape."},
                      "src": {"type": "array", "items": {"type": "integer", "minimum": 1},
                              "minItems": 2, "maxItems": 2,
                              "description": "With stills: the source's size as Resolve reads "
                              "it (6048x4032 for the Pyxis)."}},
                  "required": ["path"], "additionalProperties": False},
                  "description": "A timeline's clips, for a framing plan (one aspect)."},
              "master_size": {"type": "array", "items": {"type": "integer", "minimum": 1},
                              "minItems": 2, "maxItems": 2,
                              "description": "The master timeline's width and height."},
              "input_scaling": {"type": "string", "enum": ["scaleToFit", "scaleToCrop"],
                                "default": "scaleToFit",
                                "description": "The timeline's input scaling (Scale full frame "
                                "with crop is scaleToCrop)."},
              "squeeze": {"type": "number", "minimum": 0.5, "maximum": 3,
                          "description": "The source's anamorphic squeeze, when the manifest "
                          "and file do not say."},
              "aspects": {"type": "array", "items": {"type": "string", "enum": [
                              "9x16", "1x1", "4x5", "16x9", "master"]},
                          "minItems": 1, "uniqueItems": True,
                          "description": "Versions to plan (default 9x16 and 1x1 for a "
                          "manifest, master for items)."},
              "samples": {"type": "integer", "minimum": 2, "maximum": 120, "default": 8,
                          "description": "Frames sampled per span."},
              "per_second": {"type": "number", "minimum": 0, "maximum": 5, "default": 0.5,
                             "description": "Fewest samples per second of a span."},
              "speaker_x": {"type": "number", "minimum": 0,
                            "description": "The speaker's face x in source pixels: take the "
                            "face nearest it (two faces, as in a two-up call recording)."},
              "speaker_y": {"type": "number", "minimum": 0,
                            "description": "With speaker_x, the face's y in source pixels: "
                            "the nearest face by distance in both."},
              "subject_region": {"type": "array", "items": {"type": "number", "minimum": 0},
                                 "minItems": 4, "maxItems": 4,
                                 "description": "x0, y0, x1, y1 in source pixels: only faces "
                                 "whose centre is inside can be the subject (leave out an "
                                 "audience)."},
              "min_face_height": {"type": "number", "minimum": 0.001, "maximum": 1,
                                  "description": "Ignore faces shorter than this share of "
                                  "the frame height (an audience in the distance)."},
              "min_checked": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5,
                              "description": "The share of a span's samples that must have a "
                              "checked face before its crop can pass (never fewer than 3)."},
              "prefer": {"type": "string", "enum": ["present", "largest"], "default": "present",
                         "description": "Which face is the subject when nothing names it: "
                         "present in the most samples, or the largest of those present in at "
                         "least half as many."},
              "clips": {"type": "array", "items": {"type": "string"},
                        "description": "Only these clip names."},
              "allow_bars": {"type": "boolean", "default": False,
                             "description": "Try the Ep 002 punch-in (2.25) first when the clip "
                             "names no scale, and keep it when it holds the face but leaves "
                             "black bars (status 'bars', named in the report)."},
              **PAGING, "limit": _limit(50, 400),
              "out": _out_prop("the manifest copy (the .reframe.tsv and .reframe.json "
                               "reports go beside it), or the framing plan")}, []),
        reframe_plan, title="Plan reframes", annotations=READ))
    registry.add(Tool(
        "sync_measure",
        "Where one recording sits against another, from the sound both heard (dual-system "
        "audio: a camera clip and a recorder's file, or two cameras): FFT cross-correlation of "
        "the two mono signals at 8 kHz, on windows across the overlap. Returns the offset in "
        "seconds (where the other file's first frame lands on the reference, positive when it "
        "started later; with drift, the drift line's value at the overlap's midpoint) and in "
        "frames at fps with the half-frame residual of frame placement, clock drift (ms per "
        "minute, ppm, over the overlap, how far the placement leaves either end, and the "
        "retime that would cancel it), and each window's normalized correlation and "
        "peak-to-next-peak ratio. A weak, repeating or inconsistent result is not called a "
        "match, and says why. Never connects to Resolve.",
        _obj({"reference": {"type": "string",
                            "description": "Absolute path of the reference (the camera clip)."},
              "other": {"type": "string",
                        "description": "Absolute path of the other recording."},
              "fps": {"type": "number", "minimum": 1, "maximum": 240,
                      "description": "Frame rate for frames (default: the reference's)."},
              "window_s": {"type": "number", "minimum": 5, "maximum": 300, "default": 30,
                           "description": "Seconds per measuring window."},
              "out": _out_prop("the full JSON report")}, ["reference", "other"]),
        sync_measure, title="Measure dual-system sync", annotations=READ))
    registry.add(Tool(
        "trim_review",
        "Silence and filler review of an edit, as proposals: long silences from the source "
        "audio, filler words (um, uh, erm; hmm, mm, ah), words said twice back to back, and "
        "stretches where Whisper looped, from the mlx_whisper word JSON. Each row: start, end, "
        "kind, text, confidence, suggestion (keep, tighten, cut-candidate). Over a cut "
        "manifest, times are on each clip's timeline with the source time beside them; over a "
        "source, in source seconds. Writes the TSV to a file; nothing is cut. Never connects "
        "to Resolve.",
        _obj({"manifest": {"type": "string",
                           "description": "Absolute path of a cut manifest (its words and "
                           "source are used)."},
              "source": {"type": "string",
                         "description": "Absolute path of a media file, instead of a manifest."},
              "words": {"type": "string",
                        "description": "Absolute path of the word JSON (default: the "
                        "manifest's; without it a source gets silences only)."},
              **REVIEW_PROPS, **PAGING, "limit": _limit(100, 500),
              "out": _out_prop("the TSV")}),
        trim_review, title="Review silences and fillers", annotations=READ))
    registry.add(Tool(
        "deliver_check",
        "Check a rendered deliverable against a delivery destination from resolve-config.json, "
        "offline with ffprobe and ffmpeg: file name rule, container, video codec, size, fps "
        "as an exact rational, pixel format, colour tags and range (limited), audio codec, "
        "channels and sample rate, captions (a sidecar .srt that parses, or none at all), and "
        "integrated loudness and true peak. Every rule is PASS, FAIL or SKIP with what was "
        "found and expected. Reads the whole file for loudness; set loudness false to skip "
        "that. Never connects to Resolve.",
        _obj({"file": {"type": "string", "description": "Absolute path of the rendered file."},
              "destination": {"type": "string", "minLength": 1,
                              "description": "The destination it was rendered for: a key "
                              "in the config's destinations, checked when called, so an "
                              "overlay edit needs no restart (at start: " +
                              ", ".join(destination_keys()) + ")."},
              "fps": {"type": "string", "pattern": r"^\d+(\.\d+)?(/\d+)?$",
                      "description": "The timeline's frame rate to assert (23.976, 25, "
                      "24000/1001)."},
              "size": {"type": "string", "pattern": r"^\d+x\d+$",
                       "description": "WIDTHxHEIGHT to assert for a destination that renders "
                       "at the timeline's size."},
              "loudness": {"type": "boolean", "default": True,
                           "description": "Measure loudness (reads the whole file)."},
              "audio_state": {"type": "string",
                              "description": "Absolute path of an audio-state JSON (the "
                              "audio-state command's -o): also compares the audio codec, "
                              "sample rate and bit depth the queued Resolve jobs report with "
                              "the destination. Never connects to Resolve."}},
             ["file", "destination"]),
        deliver_check, title="Check a deliverable", annotations=READ))
    registry.add(Tool(
        "shoot_table",
        "One CSV row per clip of a shoot: camera, start, duration, fps, size, profile, lens and "
        "audio, with long-take, VFR, weak-audio and unexpected-camera flags, a per-camera "
        "offset, and an audit of capture audio over its first audio_seconds (streams, noise "
        "floor, clipping, dead channel, dual mono, room tone). Never connects to Resolve.",
        _obj({"paths": {"type": "array", "minItems": 1, "maxItems": MAX_PATHS,
                        "items": {"type": "string"},
                        "description": "Absolute paths of media files or folders (searched "
                        "recursively)."},
              "camera_offset": {"type": "array", "items": {"type": "string"},
                         "description": "CAM=SECONDS: a clock offset added to local_start for "
                         "one camera name as detect names it."},
              "long_min": {"type": "number", "minimum": 0, "default": rpshoot.LONG_TAKE_MIN,
                           "description": "Flag takes longer than this many minutes."},
              "weak_db": {"type": "number", "default": rpshoot.WEAK_AUDIO_DB,
                          "description": "Flag audio whose mean volume is below this dB."},
              "audio_seconds": {"type": "number", "minimum": 1, "default": rpshoot.AUDIO_SECONDS,
                                "description": "Audit at most this many seconds of audio per "
                                "clip."},
              **PAGING, "limit": _limit(50, 200),
              "out": _out_prop("every row as CSV")}, ["paths"]),
        shoot_table, title="Tabulate a shoot", annotations=READ))
    registry.add(Tool(
        "qc",
        "One FAIL-first QC table for a rendered file: ffmpeg blackdetect, freezedetect, scdet, "
        "ebur128, astats and silencedetect rows, plus deliver_check's rows when a destination "
        "is given. Sound rows (lra, phone_proxy, true peak and silence at each cut, "
        "speech_vs_rest with words, stems_sum with stems), sha256, and for a social "
        "destination social_fps and safe_zone. WARN rows never fail the table. Never connects "
        "to Resolve.",
        _obj({"file": {"type": "string", "description": "Absolute path of the rendered file."},
              "destination": {"type": "string", "minLength": 1,
                              "description": "A key in resolve-config.json's destinations "
                              "(checked when called): adds deliver_check's rows and the "
                              "loudness target."},
              "fps": {"type": "string", "pattern": r"^\d+(\.\d+)?(/\d+)?$",
                      "description": "The timeline's frame rate to assert (with destination)."},
              "size": {"type": "string", "pattern": r"^\d+x\d+$",
                       "description": "WIDTHxHEIGHT to assert for a timeline-size destination."},
              "duration": {"type": "number", "minimum": 0,
                           "description": "Expected duration in seconds (the lock)."},
              "shots": {"type": "string", "description": "Absolute path of a shot list CSV: the "
                        "cut count is checked against its rows minus one."},
              "words": {"type": "string", "description": "Absolute path of the word JSON of this "
                        "file: adds speech_vs_rest."},
              "stems": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                        "description": "Absolute paths of stem files: adds stems_sum."},
              "sha256": {"type": "string", "pattern": r"^[0-9a-fA-F]{64}$",
                         "description": "Expected sha256 of the file."},
              "set": {"type": "array", "items": {"type": "string"},
                      "description": "KEY=VALUE threshold overrides (keys are in "
                      "rpresolve/qc.py THRESHOLDS)."}}, ["file"]),
        qc, title="QC a render", annotations=READ))
    registry.add(Tool(
        "pipeline_proof",
        "Compare a test render with a reference render, for the G1 proof: per-frame luma and "
        "chroma shift, range, gamma or level shift, PSNR, SSIM and VMAF (where ffmpeg has "
        "libvmaf), PASS or FAIL per row. Compare render against render, or against Resolve's "
        "own decode exported as a file, never against an ffmpeg decode of a log source. Never "
        "connects to Resolve.",
        _obj({"test": {"type": "string", "description": "Absolute path of the render under test."},
              "reference": {"type": "string", "description": "Absolute path of the reference."},
              "frames": {"type": "integer", "minimum": 1, "maximum": 3000, "default": 300,
                         "description": "Frames to compare from the start."},
              "psnr": {"type": "number", "description": "Minimum PSNR in dB (default 45)."},
              "ssim": {"type": "number", "description": "Minimum SSIM (default 0.99)."},
              "vmaf": {"type": "number", "description": "Minimum VMAF (default 95)."},
              "luma_shift": {"type": "number",
                             "description": "Largest mean luma shift in 8-bit codes (default 1.5)."},
              "chroma_shift": {"type": "number", "description": "Largest mean chroma shift in "
                               "8-bit codes (default 1.5)."},
              "out": _out_prop("the full JSON report, per-frame shifts included")},
             ["test", "reference"]),
        pipeline_proof, title="Prove a pipeline", annotations=READ))
    registry.add(Tool(
        "deliver_manifest",
        "Preview the manifest.md and checksums.txt of a delivery folder "
        "(<folder>/<destination key>/<files>) from ffprobe and the files' bytes, or with "
        "verify re-check checksums.txt against the files. Writes nothing: the CLI's "
        "deliver-manifest writes the two files. Never connects to Resolve.",
        _obj({"folder": {"type": "string", "description": "Absolute path of the delivery root."},
              "verify": {"type": "boolean", "default": False,
                         "description": "Re-check checksums.txt instead of previewing."},
              "project": {"type": "string", "description": "Project slug, as used in the file "
                          "names."},
              "approval": {"type": "string", "description": "Approval reference id."},
              "prepared_by": {"type": "string", "description": "Name and role."},
              "review_copy": {"type": "string",
                              "description": "File name of the approved review copy."},
              "review_sha256": {"type": "string", "pattern": r"^[0-9a-f]{64}$",
                                "description": "SHA-256 of the review copy, lowercase hex."},
              "delivered": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$",
                            "description": "Delivery date, YYYY-MM-DD (default today)."},
              "machine_steps": {"type": "string", "default": "none stated"},
              "credits": {"type": "string", "default": "none requested"},
              "platform_notes": {"type": "string", "default": "none looked up"},
              "licences": {"type": "array", "items": {"type": "string"},
                           "description": "ASSET|LICENCE|ATTRIBUTION rows."},
              "loudness": {"type": "boolean", "default": True,
                           "description": "Measure each file's loudness (false: n/a)."}},
             ["folder"]),
        deliver_manifest, title="Preview a delivery manifest", annotations=READ))
    registry.add(Tool(
        "perceive",
        "One frame of a video or still as scopes, a Lab palette and numbers, for reading light "
        "and colour: a packet folder with packet.json, with face, skin and lighting-ratio "
        "numbers, and optionally a comparison with a reference. Needs a new or empty folder "
        "outside any git working tree. Never connects to Resolve.",
        _obj({"file": {"type": "string", "description": "Absolute path of a video or still."},
              "out": {"type": "string", "description": "Absolute path of a new folder for the "
                      "packet, outside any git working tree: the packet holds frames of the "
                      "media."},
              "at": {"type": "number", "minimum": 0,
                     "description": "Frame time in seconds (default the middle)."},
              "ref": {"type": "string", "description": "Absolute path of a reference still (or "
                      "clip) to compare against."},
              "faces": {"type": "boolean", "default": True,
                        "description": "Face, skin and lighting-ratio numbers."},
              "targets": {"type": "boolean", "default": False,
                          "description": "Also write targets.json (numbers only)."}},
             ["file", "out"]),
        perceive, title="Perceive a frame", annotations=READ))
    registry.add(Tool(
        "audio_report",
        "Loudness over time, spectrogram, peaks, clipping, clicks, silences and octave balance "
        "of one audio stream, as audio-report.json, loudness.png and spectrogram.png in a "
        "folder outside any git working tree; the reply carries the summary and flags. "
        "Optionally against a destination's loudness target, or a reference file. Never "
        "connects to Resolve.",
        _obj({"file": {"type": "string", "description": "Absolute path of the media file."},
              "out": {"type": "string", "description": "Absolute path of the report folder "
                      "(made if its parent exists), outside any git working tree."},
              "start": {"type": "string", "description": "Seconds, m:ss or h:mm:ss (default 0)."},
              "end": {"type": "string", "description": "Where to stop (default the end)."},
              "stream": {"type": "integer", "minimum": 0, "default": 0,
                         "description": "Audio stream (0 is the first)."},
              "destination": {"type": "string", "minLength": 1,
                              "description": "A key in resolve-config.json's destinations: "
                              "its loudness target and true-peak ceiling."},
              "target": {"type": "number",
                         "description": "Integrated loudness target in LUFS (over destination's)."},
              "reference": {"type": "string",
                            "description": "Absolute path of a reference file, read whole."},
              "ref_stream": {"type": "integer", "minimum": 0, "default": 0,
                             "description": "Audio stream of the reference."}},
             ["file", "out"]),
        audio_report, title="Report on audio", annotations=READ))
    registry.add(Tool(
        "overlay_check",
        "Check a graphic master rendered with alpha: tags, frame rate, clear frames, opaque "
        "frames, edge fringe and brand colour at named pixels, each PASS, FAIL or SKIP. Never "
        "connects to Resolve.",
        _obj({"file": {"type": "string", "description": "Absolute path of the graphic master."},
              "expect": {"type": "array", "items": {"type": "string"},
                         "description": "KEY=VALUE tags the file must carry: color_range, "
                         "color_space, color_primaries, color_transfer or pix_fmt."},
              "fps": {"type": "string", "pattern": r"^\d+(\.\d+)?(/\d+)?$",
                      "description": "The frame rate to assert (23.976, 25, 24000/1001)."},
              "clear_at": {"type": "array", "items": {"type": "number", "minimum": 0},
                           "description": "Times with nothing visible (default the first frame)."},
              "no_clear": {"type": "boolean", "default": False,
                           "description": "Skip the clear row, for a graphic that starts visible."},
              "hold": {"type": "number", "minimum": 0,
                       "description": "The time of the opaque, fringe and brand rows (default "
                       "mid-file)."},
              "alpha_mode": {"type": "string", "enum": ["straight", "premultiplied"],
                             "default": "straight",
                             "description": "How the timeline will read the alpha."},
              "values": {"type": "string", "description": "Absolute path of a JSON of name -> "
                         "'#rrggbb', kept outside the repo."},
              "sample": {"type": "array", "items": {"type": "string"},
                         "description": "NAME=X,Y[@SECONDS]: a pixel that must match the value "
                         "NAME."},
              "sample_box": {"type": "integer", "minimum": 1,
                             "description": "Side of the square each sample averages, odd."},
              "fringe_max": {"type": "number", "minimum": 0,
                             "description": "Edge error allowed, 10-bit codes at the median "
                             "edge pixel."},
              "de_max": {"type": "number", "minimum": 0,
                         "description": "CIEDE2000 allowed at a brand sample."}}, ["file"]),
        overlay_check, title="Check a graphic master", annotations=READ))
