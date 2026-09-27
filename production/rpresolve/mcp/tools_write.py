"""
rpresolve.mcp.tools_write: tools that add to a Resolve project.

The rules every write tool keeps:
- The call names the project (project, and project_id when known); the
  open project must match, and is re-checked before every change.
- Additive only: nothing that already exists is modified.
- dry_run defaults to true and returns a plan_sha. A real run must pass
  that plan_sha, and is refused when the plan has changed since.
- Every run holds the cross-process lock; a real run is journalled
  (journal.py) before it touches Resolve and after.
"""

import os

from .. import api, paths, workflows
from ..config import load_config
from .. import detect as rpdetect
from .. import ingest as rpingest
from . import journal
from .registry import WRITE, Tool

DESTRUCTIVE = {**WRITE, "destructiveHint": True}
from .tools_offline import _abs, _existing, _page

WRITE_LOCK_WAIT = 30.0


def _require_sha(args):
    if not args["dry_run"] and not args.get("plan_sha"):
        raise workflows.Refused("a real run needs the plan_sha from a dry run of the same "
                                "call; run it with dry_run true first and review the plan.")


def _journalled(tool, args, run):
    """Run a real write between a started and a finished journal line."""
    record = {k: v for k, v in args.items() if k not in ("offset", "limit")}
    wid = journal.start(tool, {"name": args["project"], "id": args.get("project_id")}, record)
    try:
        result = run()
    except BaseException as e:
        warn = journal.finish(wid, tool, "error", f"{type(e).__name__}: {e}")
        if warn:
            e.args = (f"{e} ({warn})",) + e.args[1:]
        raise
    status = {0: "ok", 2: "needs_review"}.get(result.get("exit_status"), "failed")
    warn = journal.finish(wid, tool, status, result.get("summary"),
                          {k: result.get(k) for k in ("counts", "would_create", "created",
                                                      "plan_sha") if k in result})
    result["journal"] = {"id": wid, "path": journal.path()}
    if warn:
        result["journal"]["warning"] = warn
    return result


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------

def ingest(args, ctx):
    _require_sha(args)
    targets = [_abs(p, "paths") for p in args["paths"]]
    ctx.check_cancel()
    rows, missing = rpdetect.detect_paths(targets)  # offline, before the lock

    def run():
        return _summarised(workflows.ingest(
            ctx.session.get(), args["project"], targets, parent=args["parent"],
            dry_run=args["dry_run"], project_id=args.get("project_id"), rows=rows,
            check_cancel=ctx.check_cancel, expect_sha=args.get("plan_sha")))

    with api.ResolveLock(timeout=WRITE_LOCK_WAIT):
        r = run() if args["dry_run"] else _journalled("ingest", args, run)
    r["missing"] = missing
    # One row per file: the result, plus the plan's camera, profile and reason
    # (why a file is skipped, sent to Review, or needs conforming).
    by_path = {e["path"]: e for e in r.pop("plan")}
    rows = [{**x, **{k: by_path.get(x["path"], {}).get(k) for k in ("camera", "profile", "reason")}}
            for x in r.pop("results")]
    page = _page(rows, args)
    r["results"] = page.pop("rows")
    r.update(page)
    return r


def _summarised(r):
    c = ", ".join(f"{n} {k}" for k, n in sorted(r["counts"].items()))
    verb = "would" if r["dry_run"] else "did"
    r["summary"] = (f"ingest into '{r['project']['name']}' {verb}: {c}" +
                    (f"; {len(r['flagged'])} need a person" if r["flagged"] else "") +
                    ("" if not r["dry_run"] else
                     ". Nothing to do." if all(e["action"] == rpingest.SKIP for e in r["plan"])
                     else f". To run it, call again with dry_run false and plan_sha "
                          f"{r['plan_sha']}."))
    return r


# ---------------------------------------------------------------------------
# cut
# ---------------------------------------------------------------------------

def cut(args, ctx):
    _require_sha(args)
    manifest = _existing(args["manifest"], "manifest")
    ctx.check_cancel()
    gate = workflows.cut_gate(manifest, args.get("clips"), audio=args["audio"],
                              force=args["force"])  # offline, before the lock

    def run():
        r = workflows.cut(ctx.session.get(), args["project"], manifest, prefix=args["prefix"],
                          force=args["force"], audio=args["audio"],
                          make_9x16=args["make_9x16"], dry_run=args["dry_run"],
                          project_id=args.get("project_id"), check_cancel=ctx.check_cancel,
                          expect_sha=args.get("plan_sha"), gate=gate)
        r["created"] = [x["name"] for x in r["results"] if x.get("ok")]
        blocked = r["gate"]["blocked"]
        if r["dry_run"]:
            r["summary"] = (f"cut would create {len(r['would_create'])} timeline(s) in "
                            f"'{r['project']['name']}'" +
                            (f", {len(r['skipped_existing'])} already exist" if
                             r["skipped_existing"] else "") +
                            (f"; {len(blocked)} clip(s) {'forced past' if r['gate']['forced'] else 'blocked by'} "
                             "endcheck" if blocked else "") +
                            f". To run it, call again with dry_run false and plan_sha "
                            f"{r['plan_sha']}.")
        else:
            bad = [x["name"] for x in r["results"] if not x.get("ok")]
            r["summary"] = (f"cut created {len(r['created'])} timeline(s) in "
                            f"'{r['project']['name']}'" +
                            (f"; FAILED: {', '.join(bad)}" if bad else "") +
                            ("; the UI was not fully restored" if r["ui_restore_problems"]
                             else ""))
        return r

    with api.ResolveLock(timeout=WRITE_LOCK_WAIT):
        return run() if args["dry_run"] else _journalled("cut", args, run)


# ---------------------------------------------------------------------------
# duplicate_timeline_auto, apply_grade, queue_render
# ---------------------------------------------------------------------------

def _how(r):
    return ("" if not r["dry_run"] else f". To run it, call again with dry_run false and "
            f"plan_sha {r['plan_sha']}.")


def _ui(r):
    return "; the UI was not fully restored" if r["ui_restore_problems"] else ""


def duplicate_timeline_auto(args, ctx):
    _require_sha(args)

    def run():
        r = workflows.duplicate_auto(ctx.session.get(), args["project"], args["timeline"],
                                     args.get("new_name"), dry_run=args["dry_run"],
                                     project_id=args.get("project_id"),
                                     expect_sha=args.get("plan_sha"))
        o = r["origin"]
        if r["dry_run"]:
            r["summary"] = (f"would duplicate '{o['name']}' ({o['items']} item(s)) as "
                            f"'{r['new_name']}'" + _how(r))
        else:
            r["summary"] = (f"duplicated '{o['name']}' as '{r['new_name']}'" +
                            (f"; {len(r['mismatches'])} track(s) differ from the origin"
                             if r["mismatches"] else "; every item matches the origin") + _ui(r))
        return r

    with api.ResolveLock(timeout=WRITE_LOCK_WAIT):
        return run() if args["dry_run"] else _journalled("duplicate_timeline_auto", args, run)


def apply_grade(args, ctx):
    _require_sha(args)

    def run():
        r = workflows.apply_grade(ctx.session.get(), args["project"], args["timeline"],
                                  lut=args.get("lut"), drx=args.get("drx"), track=args["track"],
                                  items=args.get("items"), overwrite=args["overwrite"],
                                  dry_run=args["dry_run"], project_id=args.get("project_id"),
                                  check_cancel=ctx.check_cancel,
                                  expect_sha=args.get("plan_sha"))
        g, tl = r["grade"], r["timeline"]["name"]
        what = (f"LUT {os.path.basename(g['path'])} on node {g['node']}" if g["kind"] == "lut"
                else f"{os.path.basename(g['path'])} (mode {g['mode']})")
        over = sum(1 for t in r["targets"] if t.get("overwrites"))
        skipped = f"; {len(r['refused'])} item(s) refused" if r["refused"] else ""
        if r["dry_run"]:
            r["summary"] = (f"would apply {what} to {len(r['targets'])} item(s) on '{tl}'" +
                            (f", replacing {over} existing grade(s)" if over else "") +
                            skipped + _how(r))
        else:
            ok = sum(1 for x in r["results"] if x["ok"])
            r["summary"] = (f"applied {what} to {ok} of {len(r['results'])} item(s) on '{tl}'" +
                            skipped +
                            (f"; GRADE LEAKED to {len(r['leaks'])} item(s) elsewhere"
                             if r["leaks"] else "; no other timeline changed") + _ui(r))
        return r

    with api.ResolveLock(timeout=WRITE_LOCK_WAIT):
        return run() if args["dry_run"] else _journalled("apply_grade", args, run)


def queue_render(args, ctx):
    _require_sha(args)
    out_dir = _abs(args["output_dir"], "output_dir")
    problem = paths.out_problem(os.path.join(out_dir, "x"), any_git_tree=True)
    if problem:
        raise paths.OutputRefused(problem.replace(os.path.join(out_dir, "x"), out_dir))
    presets = load_config()["render_presets"]

    def run():
        r = workflows.queue_render(ctx.session.get(), args["project"], args["timeline"],
                                   args["preset"], out_dir, presets, args.get("custom_name"),
                                   dry_run=args["dry_run"], project_id=args.get("project_id"),
                                   expect_sha=args.get("plan_sha"))
        tl, name = r["timeline"]["name"], r["preset"]["name"]
        changed = ", ".join(r["deliver_changed"])
        if r["dry_run"]:
            r["summary"] = (f"would queue '{tl}' as {name} to {r['output']} (not started); this "
                            f"sets the Deliver page's {changed}" + _how(r))
        elif r["exit_status"] == 0:
            r["summary"] = (f"queued '{tl}' as {name} to {r['output']}, job "
                            f"{r['job']['JobId']}; NOT started, Isaac starts renders. Deliver "
                            f"page fields now changed: {changed}" + _ui(r))
        else:
            r["summary"] = ("queue_render FAILED: " +
                            "; ".join(r["warnings"][:1] + r["readback_problems"]) + _ui(r))
        return r

    with api.ResolveLock(timeout=WRITE_LOCK_WAIT):
        return run() if args["dry_run"] else _journalled("queue_render", args, run)


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------

PROJECT = {
    "project": {"type": "string", "minLength": 1,
                "description": "The open project's exact name (resolve_status shows it)."},
    "project_id": {"type": "string", "description": "The open project's unique id."},
}
DRY_RUN = {
    "dry_run": {"type": "boolean", "default": True,
                "description": "Plan only (the default). Show the plan, then run for real."},
    "plan_sha": {"type": "string", "pattern": "^[0-9a-f]{64}$",
                 "description": "The plan_sha a dry run of this same call returned; "
                 "required for a real run."},
}


def register(registry):
    registry.add(Tool(
        "ingest",
        "Import media into the open project's media pool under camera bins (parent/Camera "
        "model) and tag each clip's Input Color Space and Data Level from detect, reading "
        "every tag back. Files already in the pool are skipped; nothing existing is changed. "
        "Needs a colour-managed project. Dry run first; the real run needs its plan_sha.",
        {"type": "object", "properties": {
            **PROJECT,
            "paths": {"type": "array", "minItems": 1, "maxItems": 500,
                      "items": {"type": "string"},
                      "description": "Absolute paths of media files or folders."},
            "parent": {"type": "string", "minLength": 1, "default": "Source",
                       "description": "The bin the camera bins go under."},
            **DRY_RUN,
            "offset": {"type": "integer", "minimum": 0, "default": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100}},
         "required": ["project", "paths"], "additionalProperties": False},
        ingest, title="Ingest media", annotations=WRITE))
    registry.add(Tool(
        "cut",
        "Build clip timelines from a cut manifest in the open project: one '<prefix>_<clip> "
        "[auto]' timeline per clip from the source's frames, plus a 9:16 reframed copy when "
        "the clip has a face position, reading every item back. Clips that fail endcheck are "
        "skipped unless force is true; timelines that already exist are skipped. Dry run "
        "first; the real run needs its plan_sha.",
        {"type": "object", "properties": {
            **PROJECT,
            "manifest": {"type": "string", "description": "Absolute path of the manifest JSON."},
            "prefix": {"type": "string", "pattern": "^[A-Za-z0-9-]{1,24}$", "default": "SW",
                       "description": "Timeline name prefix."},
            "clips": {"type": "array", "items": {"type": "string"},
                      "description": "Only these clip names."},
            "force": {"type": "boolean", "default": False,
                      "description": "Build clips that fail endcheck too (reported)."},
            "audio": {"type": "boolean", "default": True,
                      "description": "Run endcheck's audio check."},
            "make_9x16": {"type": "boolean", "default": True},
            **DRY_RUN},
         "required": ["project", "manifest"], "additionalProperties": False},
        cut, title="Cut clip timelines", annotations=WRITE))
    registry.add(Tool(
        "duplicate_timeline_auto",
        "Duplicate a timeline as a new timeline whose name ends ' [auto]' (default: the "
        "same name plus ' [auto]'), then compare every item's timeline and source frames with "
        "the origin. The origin is not touched. Duplicating makes the copy current in Resolve, "
        "so the current timeline and page are put back. Dry run first; the real run needs "
        "its plan_sha.",
        {"type": "object", "properties": {
            **PROJECT,
            "timeline": {"type": "string", "description": "Origin timeline name or unique id."},
            "new_name": {"type": "string", "pattern": " \\[auto\\]$",
                         "description": "Name for the copy; must end ' [auto]'."},
            **DRY_RUN},
         "required": ["project", "timeline"], "additionalProperties": False},
        duplicate_timeline_auto, title="Duplicate a timeline as [auto]", annotations=WRITE))
    registry.add(Tool(
        "apply_grade",
        "Apply a LUT (to one node) or a .drx grade still (with its {num_nodes, labels} "
        "manifest beside it) to items on one video track of an ' [auto]' timeline only, and "
        "read each back. Always refuses an item whose grade version is remote (shared with "
        "other timelines); refuses one whose graph is not default unless overwrite is true. After the real run it checks every item on other "
        "timelines that uses the same media, and reports any grade that changed there as a "
        "leak. Applying a .drx opens the Color page; the page and timeline are put back. Dry "
        "run first; the real run needs its plan_sha.",
        {"type": "object", "properties": {
            **PROJECT,
            "timeline": {"type": "string",
                         "description": "An ' [auto]' timeline's name or unique id."},
            "track": {"type": "integer", "minimum": 1, "default": 1},
            "items": {"type": "array", "items": {"type": "integer", "minimum": 1},
                      "uniqueItems": True,
                      "description": "Item numbers on the track, from 1 (default: all)."},
            "lut": {"type": "object", "properties": {
                "path": {"type": "string",
                         "description": "A .cube, absolute or relative to Resolve's LUT folder."},
                "node": {"type": "integer", "minimum": 1, "default": 1}},
                "required": ["path"], "additionalProperties": False},
            "drx": {"type": "object", "properties": {
                "path": {"type": "string", "description": "Absolute path of the .drx."},
                "mode": {"type": "integer", "enum": [0, 1, 2], "default": 0,
                         "description": "0 no keyframes, 1 source timecode, 2 start frames."},
                "manifest": {"type": "string",
                             "description": "Expected-labels JSON (default: beside the .drx)."}},
                "required": ["path"], "additionalProperties": False},
            "overwrite": {"type": "boolean", "default": False,
                          "description": "Also replace grades that are not default."},
            **DRY_RUN},
         "required": ["project", "timeline"], "additionalProperties": False},
        apply_grade, title="Apply a grade", annotations=DESTRUCTIVE))
    registry.add(Tool(
        "queue_render",
        "Queue one render job for a timeline from a resolve-config.json preset. It never "
        "starts a render: Isaac starts renders. The timeline is made current only while "
        "queueing; the current timeline, page and the Deliver page's format and codec are put "
        "back. The other Deliver fields it sets (target folder, name, size, video and audio "
        "on) cannot be read back or restored, and every result lists them. Refused while a "
        "render runs or when the output file exists. Dry run first; the real run needs its "
        "plan_sha.",
        {"type": "object", "properties": {
            **PROJECT,
            "timeline": {"type": "string", "description": "Timeline name or unique id."},
            "preset": {"type": "string", "enum": sorted(load_config()["render_presets"]),
                       "description": "A render preset from resolve-config.json."},
            "output_dir": {"type": "string",
                           "description": "Absolute path of an existing folder outside any "
                           "git repository."},
            "custom_name": {"type": "string", "pattern": "^[A-Za-z0-9 _.()-]{1,120}$",
                            "description": "File name before the preset suffix (default: the "
                            "timeline name)."},
            **DRY_RUN},
         "required": ["project", "timeline", "preset", "output_dir"],
         "additionalProperties": False},
        queue_render, title="Queue a render", annotations=WRITE))
