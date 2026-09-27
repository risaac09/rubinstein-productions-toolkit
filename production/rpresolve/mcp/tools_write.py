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

from .. import api, workflows
from .. import detect as rpdetect
from .. import ingest as rpingest
from . import journal
from .registry import WRITE, Tool
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
