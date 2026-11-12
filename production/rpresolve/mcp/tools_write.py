"""
rpresolve.mcp.tools_write: tools that add to a Resolve project, and
deliver_captions, which adds a file beside a render.

The rules every write tool keeps:
- The call names the project (project, and project_id when known); the
  open project must match, and is re-checked before every change.
- Additive only: nothing that already exists is modified. There are two exceptions:
  set_color_management, only on a fresh project (no timeline, no clip), and relink,
  which points a clip that is offline at plan time at a file that verifies as the
  same take (never an online clip, never on a name alone).
- dry_run defaults to true and returns a plan_sha. A real run must pass
  that plan_sha, and is refused when the plan has changed since.
- Every run holds the cross-process lock; a real run is journalled
  (journal.py) before it touches Resolve and after.

deliver_captions never connects to Resolve, so it names no project and
takes no lock. It keeps the rest: it writes <stem>.srt beside a render
(never over a file, never inside a git working tree) and moves Resolve's
TTML to the Trash, so it plans first, runs only with the dry run's
plan_sha, and is journalled.
"""

import os

from .. import api, cmd_assemble, cmd_effect, cmd_vertical, deliver, delivercheck, ledgercheck, settingsguard, syncbuild, workflows
from .. import detect as rpdetect
from .. import ingest as rpingest
from .. import proxies as rpproxies, relink as rprelink, trimreview
from . import journal
from .registry import WRITE, Tool

DESTRUCTIVE = {**WRITE, "destructiveHint": True}
from .tools_offline import (PAGING, REVIEW_PROPS, _abs, _existing, _limit, _page,
                            deliver_config, destination_keys)

WRITE_LOCK_WAIT = 30.0

# queue_render once took a render preset (preset, with output_dir and custom_name). A call
# that still passes one hears what to pass now, not just "unknown property".
QUEUE_RENDER_RETIRED = {
    "preset": "The render presets are gone: queue_render queues for a delivery destination. "
              "Pass 'destination' (a key in the config's destinations, such as youtube_16x9 or "
              "client_master) with 'name' and 'target_dir'.",
    "output_dir": "It went with the presets. Pass 'destination' with 'target_dir' (the folder) "
                  "and 'name' (the file name parts).",
    "custom_name": "It went with the presets. Pass 'destination' with 'name'; the house rule "
                   "names the file from those parts.",
}


def _require_sha(args):
    if not args["dry_run"] and not args.get("plan_sha"):
        raise workflows.Refused("a real run needs the plan_sha from a dry run of the same "
                                "call; run it with dry_run true first and review the plan.")


def _journalled(tool, args, run):
    """Run a real write between a started and a finished journal line."""
    record = {k: v for k, v in args.items() if k not in ("offset", "limit")}
    project = ({"name": args["project"], "id": args.get("project_id")} if "project" in args
               else None)
    wid = journal.start(tool, project, record)
    try:
        result = run()
    except BaseException as e:
        done = {k: getattr(e, k) for k in ("relinked", "stabilized", "effects_added")
                if getattr(e, k, None)}
        warn = journal.finish(wid, tool, "error", f"{type(e).__name__}: {e}", done or None)
        if warn:
            e.args = (f"{e} ({warn})",) + e.args[1:]
        raise
    status = {0: "ok", 2: "needs_review"}.get(result.get("exit_status"), "failed")
    warn = journal.finish(wid, tool, status, result.get("summary"),
                          {k: result.get(k) for k in ("counts", "would_create", "created",
                                                      "applied", "left_behind", "plan_sha",
                                                      "settings_drift_rows", "relinked", "stabilized")
                           if k in result})
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
    if not args["dry_run"]:  # the proxies outlive this call; link_proxies attaches them
        r["proxies"] = rpproxies.start(args["project"], [
            x["path"] for x in r["results"] if x["result"] in ("imported", "tagged")])
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
    # offline, before the lock
    gate = workflows.cut_gate(manifest, args.get("clips"), audio=args["audio"],
                              force=args["force"],
                              exclude=args.get("exclude") and _abs(args["exclude"], "exclude"))

    def run():
        r = workflows.cut(ctx.session.get(), args["project"], manifest, prefix=args["prefix"],
                          force=args["force"], audio=args["audio"],
                          make_9x16=args["make_9x16"], dry_run=args["dry_run"],
                          project_id=args.get("project_id"), check_cancel=ctx.check_cancel,
                          expect_sha=args.get("plan_sha"), gate=gate,
                          aspects=args.get("aspects"))
        r["created"] = [x["name"] for x in r["results"] if x.get("ok")]
        blocked = r["gate"]["blocked"]
        if r["dry_run"]:
            bars = sorted({x["timeline"] for x in r["reframes"] if x["bars"]})
            r["summary"] = (f"cut would create {len(r['would_create'])} timeline(s) in "
                            f"'{r['project']['name']}'" +
                            (f", {len(r['skipped_existing'])} already exist" if
                             r["skipped_existing"] else "") +
                            (f"; {len(blocked)} clip(s) {'forced past' if r['gate']['forced'] else 'blocked by'} "
                             "endcheck" if blocked else "") +
                            _unreframed(r["unreframed"]) + _refused(r["refused"]) +
                            (f"; BARS (the picture does not fill the frame) on {', '.join(bars)}"
                             if bars else "") +
                            (f"; {r['exclude_note']}" if r["exclude_note"] else "") +
                            f". To run it, call again with dry_run false and plan_sha "
                            f"{r['plan_sha']}.")
        else:
            bad = [x["name"] for x in r["results"]
                   if not x.get("ok") and not x.get("unreframed") and not x.get("refused")]
            bars = [x["name"] for x in r["results"] if x.get("bars")]
            r["summary"] = (f"cut created {len(r['created'])} timeline(s) in "
                            f"'{r['project']['name']}'" +
                            (f"; FAILED: {', '.join(bad)}" if bad else "") +
                            (f"; LEFT BEHIND (each exists as its failed build left it; delete "
                             f"before a re-run): {', '.join(r['left_behind'])}"
                             if r["left_behind"] else "") +
                            _unreframed([f"{x['name']}: {x['reason']}" for x in r["results"]
                                         if x.get("unreframed")]) +
                            _refused([f"{x['name']}: {x['reason']}" for x in r["results"]
                                      if x.get("refused")]) +
                            (f"; BARS (the picture does not fill the frame) on {', '.join(bars)}"
                             if bars else "") +
                            settingsguard.clause(r.get("settings_drift_rows")) +
                            (f"; {r['exclude_note']}" if r["exclude_note"] else "") +
                            ("; the UI was not fully restored" if r["ui_restore_problems"]
                             else ""))
        return r

    with api.ResolveLock(timeout=WRITE_LOCK_WAIT):
        return run() if args["dry_run"] else _journalled("cut", args, run)


def _unreframed(names):
    return (f"; UNREFRAMED (not built): {'; '.join(names)}" if names else "")


def _refused(names):
    return (f"; REFUSED (not built): {'; '.join(names)}" if names else "")


# ---------------------------------------------------------------------------
# duplicate_timeline_auto, apply_grade, queue_render
# ---------------------------------------------------------------------------

def _how(r):
    return ("" if not r["dry_run"] else _other_tracks(r) + f". To run it, call again with "
            f"dry_run false and plan_sha {r['plan_sha']}.")


def _other_tracks(r):
    """A dry run's warning when the timeline has items on video tracks it does not touch: a
    montage built with a scale-window or crop-window is on V2 and V1 holds only plates."""
    tl = r.get("timeline")  # a name, for the tools that build a timeline
    other = tl.get("other_tracks") if isinstance(tl, dict) else None
    if not other:
        return ""
    where = ", ".join(f"V{t} holds {n}" for t, n in sorted(other.items()))
    return (f"; CHECK THE TRACK: this is V{r['timeline']['track']} and {where} (a montage "
            "built with a scale-window or crop-window is on V2, V1 holds only its plates; "
            "pass track 2 for the montage)")


def _ui(r):
    return "; the UI was not fully restored" if r["ui_restore_problems"] else ""


def _not_clean(r):
    """The summary of a real queue_render that did not come out clean. A
    job that was queued but reads back different stays in Resolve's render
    queue, so it is not called FAILED: Render All would render it."""
    if r.get("job"):
        return (f"queued job {r['job'].get('JobId')} but Resolve holds different values: " +
                "; ".join(r["readback_problems"]) + ". The job stays in the render queue, NOT "
                "started: remove it or check it before rendering" + _ui(r))
    return ("queue_render FAILED, nothing queued: " +
            "; ".join(r["warnings"][:1] + r["readback_problems"]) + _ui(r))


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


def set_current_timeline(args, ctx):
    _require_sha(args)

    def run():
        r = workflows.set_current_timeline(ctx.session.get(), args["project"],
                                           args.get("timeline"), args.get("restore_to"),
                                           dry_run=args["dry_run"],
                                           project_id=args.get("project_id"),
                                           expect_sha=args.get("plan_sha"))
        was, to = r["current"]["name"], r["target"]["name"]
        if r["dry_run"]:
            r["summary"] = (f"would make '{to}' current (now "
                            f"{repr(was) if was else 'no current timeline'})" + _how(r))
        elif r["previous"] is None:
            r["summary"] = (f"made '{to}' current, read back; there was no current timeline "
                            "to put back")
        else:
            r["summary"] = (f"made '{to}' current, read back; previous was '{r['previous']}' "
                            f"(id {r['previous_unique_id']}). Put it back with restore_to "
                            "that id, or the name if it is unique")
        return r

    with api.ResolveLock(timeout=WRITE_LOCK_WAIT):
        return run() if args["dry_run"] else _journalled("set_current_timeline", args, run)


# ---------------------------------------------------------------------------
# set_color_management, timeline_from_clips
# ---------------------------------------------------------------------------

def set_color_management(args, ctx):
    _require_sha(args)

    def run():
        r = workflows.set_color_management(
            ctx.session.get(), args["project"], args.get("preset"), dry_run=args["dry_run"],
            project_id=args.get("project_id"), check_cancel=ctx.check_cancel,
            expect_sha=args.get("plan_sha"))
        name = r["project"]["name"]
        if not r["would_change"]:
            r["summary"] = (f"'{name}' already has the '{r['preset']}' settings; nothing to set")
        elif r["dry_run"]:
            r["summary"] = (f"would set {', '.join(r['would_change'])} on '{name}' (a fresh "
                            f"project, '{r['preset']}' preset)" + _how(r))
        elif r["failed"]:
            kept = ", ".join(a["key"] for a in r["applied"]) or "none"
            r["summary"] = (f"set_color_management FAILED on {r['failed']['key']}: "
                            f"{r['failed']['problem']} Nothing is rolled back; keys already "
                            f"set and left as they are: {kept}")
        elif r["drift"]:
            r["summary"] = ("set " + ", ".join(a["key"] for a in r["applied"]) + f" on '{name}', "
                            "but Resolve changed something afterwards: " + "; ".join(r["drift"]))
        else:
            r["summary"] = (f"set {', '.join(a['key'] for a in r['applied'])} on '{name}' and "
                            "read each back")
        return r

    with api.ResolveLock(timeout=WRITE_LOCK_WAIT):
        return run() if args["dry_run"] else _journalled("set_color_management", args, run)


def timeline_from_clips(args, ctx):
    _require_sha(args)

    def run():
        r = workflows.timeline_from_clips(
            ctx.session.get(), args["project"], args["name"], bin=args.get("bin"),
            clips=args.get("clips"), order=args["order"], dry_run=args["dry_run"],
            project_id=args.get("project_id"), check_cancel=ctx.check_cancel,
            expect_sha=args.get("plan_sha"), fps=args.get("fps"), width=args.get("width"),
            height=args.get("height"))
        n, skipped, conf = len(r["plan"]), len(r["skipped"]), len(r["conformed"])
        extra = ((f"; {skipped} item(s) in the bin skipped (not placeable)" if skipped else "") +
                 (f"; {conf} clip(s) at another frame rate than the timeline, conformed by "
                  "Resolve to real time" if conf else ""))
        if r["dry_run"]:
            r["summary"] = (f"would build '{r['timeline']}' from {n} clip(s), "
                            f"{r['total_frames']} frames at {r['rate']} fps, in {r['order']} "
                            f"order, in the open bin '{r['lands_in_bin']}'" + extra + _how(r))
        elif r["problems"]:
            r["summary"] = (f"timeline_from_clips FAILED: '{r['timeline']}' was made but does "
                            "not match the plan, and is left in the project for you to check or "
                            "delete: " + "; ".join(r["problems"][:2]) + _ui(r))
        else:
            r["summary"] = (f"built '{r['timeline']}' from {n} clip(s), every item read back in "
                            "order" + extra + _ui(r))
        return r

    with api.ResolveLock(timeout=WRITE_LOCK_WAIT):
        r = run() if args["dry_run"] else _journalled("timeline_from_clips", args, run)
    page = _page(r["plan"], args)
    r["plan"] = page.pop("rows")
    r.update(page)
    return r


def import_timeline(args, ctx):
    _require_sha(args)

    def run():
        r = workflows.import_timeline(
            ctx.session.get(), args["project"], args["name"], args["file"],
            dry_run=args["dry_run"], project_id=args.get("project_id"),
            check_cancel=ctx.check_cancel, expect_sha=args.get("plan_sha"),
            exclude=args.get("exclude") and _abs(args["exclude"], "exclude"))
        n = sum(1 for e in r["events"] if e["kind"] == "video")
        na = len(r["events"]) - n
        nt = len(r["transitions"])
        what = f"{n} video clip(s), {na} audio clip(s) and {nt} transition(s) at {r['rate']} fps"
        excl = ("; " + r["exclude_note"] if r["exclude_note"] else "") + (
            f"; {len(r['unchecked'])} event(s) could not be matched to a file and were not "
            "checked against the exclude list" if r["unchecked"] else "")
        missing = (f"; {len(r['media_missing'])} media file(s) missing" if r["media_missing"]
                   else "") + excl
        if r["dry_run"]:
            r["summary"] = (f"would import '{os.path.basename(r['file'])}' as '{r['timeline']}': "
                            f"{what}, source clips into the open bin '{r['lands_in_bin']}'" +
                            missing + _how(r))
        elif r["problems"]:
            r["summary"] = (f"import_timeline FAILED: '{r['timeline']}' was made but does not "
                            "match the file, and is left in the project for you to check or "
                            "delete: " + "; ".join(r["problems"][:2]) + excl + _ui(r))
        else:
            kinds = sorted({t["placed_as"] for t in r["transitions"] if t.get("placed_as")})
            r["summary"] = (f"imported '{r['timeline']}': {what}; every video clip read back at "
                            "its record position" +
                            (f", every transition in place (placed as {', '.join(kinds)})"
                             if nt else "") +
                            excl + _ui(r))
        return r

    with api.ResolveLock(timeout=WRITE_LOCK_WAIT):
        r = run() if args["dry_run"] else _journalled("import_timeline", args, run)
    page = _page(r["events"], args)
    r["events"] = page.pop("rows")
    r.update(page)
    return r


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


def reframe_apply(args, ctx):
    _require_sha(args)
    plan = _abs(args["plan"], "plan")

    def run():
        r = workflows.reframe_apply(ctx.session.get(), args["project"], args["timeline"], plan,
                                    track=args["track"], items=args.get("items"),
                                    allow_bars=args["allow_bars"], dry_run=args["dry_run"],
                                    project_id=args.get("project_id"),
                                    check_cancel=ctx.check_cancel,
                                    expect_sha=args.get("plan_sha"))
        tl, n = r["timeline"]["name"], len(r["targets"])
        rest = (f"; {len(r['unchanged'])} unchanged" if r["unchanged"] else "") + \
               (f"; {len(r['refused'])} refused" if r["refused"] else "")
        if any("squeeze" in w for w in r["warnings"]):
            rest += "; squeezed items: the anamorphic transform is unchecked live, look at them"
        if r["dry_run"]:
            r["summary"] = (f"would set Zoom, Pan and Tilt on {n} item(s) on '{tl}' V"
                            f"{r['timeline']['track']}{rest}" + _how(r))
        elif not n:
            r["summary"] = f"nothing to write on '{tl}'{rest}"
        else:
            ok = sum(1 for x in r["results"] if x["ok"])
            r["applied"] = ok
            r["summary"] = (f"set and read back the framing of {ok} of {len(r['results'])} "
                            f"item(s) on '{tl}'{rest}" +
                            (f"; FRAMING LEAKED to {len(r['leaks'])} item(s) elsewhere"
                             if r["leaks"] else "; no other timeline changed"))
        return r

    with api.ResolveLock(timeout=WRITE_LOCK_WAIT):
        return run() if args["dry_run"] else _journalled("reframe_apply", args, run)


def stabilize(args, ctx):
    _require_sha(args)

    def run():
        r = workflows.stabilize(ctx.session.get(), args["project"], args["timeline"],
                                track=args["track"], items=args.get("items"),
                                max_items=args.get("max_items"), dry_run=args["dry_run"],
                                project_id=args.get("project_id"),
                                check_cancel=ctx.check_cancel, expect_sha=args.get("plan_sha"))
        tl, n = r["timeline"]["name"], len(r["targets"])
        rest = r["remaining"]
        sample = ((f" ({n} of {r['planned_total']} item(s); call again with "
                   + (f"item {rest[0]}" if len(rest) == 1 else f"items {rest[0]} to {rest[-1]}")
                   + " for the rest)") if rest else "")
        if not r["dry_run"]:
            r["stabilized"] = r["results"]
        if r["dry_run"]:
            r["summary"] = (f"would call Stabilize() on {n} item(s) on '{tl}' V"
                            f"{r['timeline']['track']}{sample}; it can take a long time on large "
                            "items, and the effect cannot be read back" + _how(r))
        else:
            ok = sum(1 for x in r["results"] if x["ok"])
            secs = sum(x["elapsed_s"] for x in r["results"])
            r["summary"] = (f"Stabilize() returned True for {ok} of {len(r['results'])} item(s) "
                            f"on '{tl}' in {secs:.1f} s{sample}; the effect is NOT verified "
                            "(stabilization is not visible through GetProperty)" + _ui(r))
        return r

    with api.ResolveLock(timeout=WRITE_LOCK_WAIT):
        return run() if args["dry_run"] else _journalled("stabilize", args, run)


def _skipped_note(r):
    """'; N item(s) skipped' when the track held items that are not clips (apply_effect, vertical_apply)."""
    return f"; {len(r['skipped'])} item(s) skipped (no media pool clip; see skipped)" if r["skipped"] else ""


def apply_effect(args, ctx):
    _require_sha(args)

    def run():
        r = cmd_effect.apply_effect(ctx.session.get(), args["project"], args["timeline"],
                                    args["effect"], track=args["track"], items=args.get("items"),
                                    dry_run=args["dry_run"], project_id=args.get("project_id"),
                                    check_cancel=ctx.check_cancel,
                                    expect_sha=args.get("plan_sha"),
                                    params=args.get("params"), spans=args.get("spans"),
                                    ledger=args.get("ledger") and _abs(args["ledger"], "ledger"),
                                    draft=args.get("draft"), override=args.get("override"))
        tl, n, e = r["timeline"]["name"], len(r["targets"]), r["effect"]["id"]
        skipped = f"; {len(r['refused'])} item(s) refused" if r["refused"] else ""
        skipped += _skipped_note(r)
        if r["dry_run"]:
            r["summary"] = (f"would add a Fusion comp '{r['effect']['comp']}' with the {e} macro "
                            f"to {n} item(s) on '{tl}' V{r['timeline']['track']}{skipped}"
                            + ledgercheck.summary(r["ledger"]) + _how(r))
        else:
            ok = sum(1 for x in r["results"] if x["ok"])
            r["applied"] = ok
            r["summary"] = (f"added and read back the {e} comp on {ok} of {len(r['results'])} "
                            f"item(s) on '{tl}'{skipped}{ledgercheck.summary(r['ledger'])}; look at it on the "
                            "Fusion page")
        return r

    with api.ResolveLock(timeout=WRITE_LOCK_WAIT):
        return run() if args["dry_run"] else _journalled("apply_effect", args, run)


def _colour_line(r):
    """What the colour rows of an assemble_timeline result say, for its summary."""
    c, rows = r["colour"], r["colour"]["sources"]
    tag = [x for x in rows if x["action"] == "tag"]
    bits = ([f"{len(tag)} to tag" if r["dry_run"] else
             f"{sum(1 for x in tag if x.get('result') == 'tagged')} of {len(tag)} tagged"]
            if tag else [])
    flagged = [os.path.basename(x["path"]) for x in rows if x["flag"]]
    if flagged:
        bits.append("in the pool before this run, log by detect and reading Rec.709: "
                    + ", ".join(flagged))
    bits.extend(c["notes"])
    return "; colour: " + "; ".join(bits) if bits else ""


def assemble_timeline(args, ctx):
    _require_sha(args)
    manifest = _abs(args["manifest"], "manifest")

    def run():
        r = cmd_assemble.apply_assemble(ctx.session.get(), args["project"], args["name"], manifest,
                                        width=args["width"], height=args["height"],
                                        dry_run=args["dry_run"], project_id=args.get("project_id"),
                                        expect_sha=args.get("plan_sha"),
                                        check_cancel=ctx.check_cancel,
                                        bed_dir=args.get("bed_dir") and _abs(args["bed_dir"], "bed_dir"),
                                        exclude=args.get("exclude") and _abs(args["exclude"], "exclude"),
                                        ledger=args.get("ledger") and _abs(args["ledger"], "ledger"),
                                        draft=args.get("draft"), override=args.get("override"))
        p = r["plan"]
        what = (f"{len(p['spans'])} item(s), {p['frames']} frames at {r['rate']} fps"
                + (f", montage on V{p['track']} over {len(r['plates'])} plate(s) on V1 (the shot "
                   f"before each window playing on): pass track {p['track']} to apply_effect, "
                   "apply_grade, vertical_apply and reframe_apply, and grade V1 too"
                   if p["track"] > 1 else "")
                + (f", music bed on A1 ({r['bed']['fade_s']:g} s fade baked in)"
                   if p["music"] else ""))
        skipped = f"; not built: {'; '.join(p['not_built'])}" if p["not_built"] else ""
        skipped += (f"; {r['exclude_note']}" if r["exclude_note"] else "")
        skipped += ledgercheck.summary(r["ledger"]) + _colour_line(r)
        if r["dry_run"]:
            r["summary"] = f"would build '{r['timeline']}' with {what}{skipped}" + _how(r)
        elif r["problems"]:
            r["summary"] = (f"built '{r['timeline']}' with problems, left in place: "
                            + "; ".join(r["problems"]))
        else:
            r["summary"] = f"built and read back '{r['timeline']}': {what}{skipped}"
        return r

    with api.ResolveLock(timeout=WRITE_LOCK_WAIT):
        return run() if args["dry_run"] else _journalled("assemble_timeline", args, run)


def vertical_apply(args, ctx):
    _require_sha(args)
    crops = _abs(args["crops"], "crops")

    def run():
        r = cmd_vertical.apply_vertical(ctx.session.get(), args["project"], args["timeline"], crops,
                                        source_timeline=args.get("source_timeline"),
                                        track=args["track"], dry_run=args["dry_run"],
                                        project_id=args.get("project_id"),
                                        expect_sha=args.get("plan_sha"),
                                        check_cancel=ctx.check_cancel,
                                        text_size_override=args.get("text_size"))
        tl, n = r["timeline"]["name"], len(r["targets"])
        comps = sum(len(t["comps"]) for t in r["targets"])
        plates = len(r["plates"])
        stand_ins = sum(1 for t in r["targets"] for c in t["comps"] if c["notes"])
        made = (f"duplicate '{r['source_timeline']}' to '{tl}' at 1080x1920, then "
                if r["create"] else "")
        if r["dry_run"]:
            r["summary"] = (f"would {made}set Scaling 3, Pan and Tilt on {n} item(s) on '{tl}' V"
                            f"{r['timeline']['track']} and move {comps} rp-fx comp(s)"
                            + (f"; frame {plates} plate(s) on V{r['timeline']['track'] - 1} "
                               "with the crop of the item each continues" if plates else "")
                            + (f"; leave {len(r['unframed_below'])} other item(s) there as they are"
                               if r["unframed_below"] else "")
                            + _skipped_note(r)
                            + (f"; {stand_ins} person-matte-text comp(s) still hold the stand-in "
                               "matte, which is not moved (see the comp's notes)" if stand_ins else "")
                            + _how(r))
        elif r.get("duplicate", {}).get("mismatches"):
            r["summary"] = f"the copy '{tl}' does not match its source; nothing was framed"
        else:
            both = r["results"] + r["plate_results"]
            ok = sum(1 for x in both if x["ok"])
            r["applied"] = ok
            r["summary"] = (f"set and read back the 9:16 framing of {ok} of {len(both)} "
                            f"item(s) on '{tl}'" + (f" ({len(r['plate_results'])} of them plates)"
                                                    if r["plate_results"] else "")
                            + _skipped_note(r) + "; export a still of each and look before queueing")
        return r

    with api.ResolveLock(timeout=WRITE_LOCK_WAIT):
        return run() if args["dry_run"] else _journalled("vertical_apply", args, run)


def queue_render(args, ctx):
    _require_sha(args)
    config = deliver_config()
    target = _abs(args["target_dir"], "target_dir") if args.get("target_dir") else None
    ctx.check_cancel()

    def run():
        r = workflows.queue_render(ctx.session.get(), args["project"], args["timeline"],
                                   output_dir=target, destination=args["destination"],
                                   name_parts=args.get("name") or {}, config=config,
                                   dry_run=args["dry_run"], project_id=args.get("project_id"),
                                   expect_sha=args.get("plan_sha"),
                                   check_cancel=ctx.check_cancel)
        tl, d = r["timeline"]["name"], r["destination"]["name"]
        # Added after plan_sha is fixed, so it never moves the sha; it never refuses.
        floor, rate = deliver.destination(config, args["destination"]).get("min_fps"), \
            deliver.exact_fps(r["timeline"]["fps"])
        try:  # a bad min_fps in a config overlay must not fail a job already queued
            floor = float(delivercheck.parse_fps(floor)) if floor else None
        except ValueError:
            floor = None
        if floor and rate is not None and rate < floor - 0.005:
            r["warnings"].append(f"the timeline runs at {r['timeline']['fps']} fps, under {d}'s "
                                 f"{floor:.2f} fps floor; it is queued as it is, the frame "
                                 "rate stays the timeline's.")
        side = f" with sidecar {os.path.basename(r['sidecar'])}" if r["sidecar"] else ""
        if r["dry_run"]:
            r["summary"] = (f"would queue '{tl}' for {d} as {r['output']}{side} (not started); "
                            f"this sets the Deliver page's {', '.join(r['deliver_changed'])}; "
                            "the job takes these from the Deliver page as it stands: "
                            f"{', '.join(r['carried_over'])}" + _how(r))
        elif r["exit_status"] == 0:
            made = f" (made the folder {r['made_folder']})" if r.get("made_folder") else ""
            r["summary"] = (f"queued '{tl}' for {d} as {r['output']}{side}{made}, job "
                            f"{r['job']['JobId']}; NOT started, the operator starts renders. Resolve's "
                            "job list does not report " +
                            (", ".join(r["unverified"]) or "nothing else") +
                            "; check the rendered file with deliver_check" + _ui(r) +
                            (f"; {hand}" if (hand := (r.get("render_guard") or {})
                                             .get("hand_reset")) else ""))
        else:
            r["summary"] = _not_clean(r)
        return r

    with api.ResolveLock(timeout=WRITE_LOCK_WAIT):
        return run() if args["dry_run"] else _journalled("queue_render", args, run)


# ---------------------------------------------------------------------------
# create_captions (Resolve) and deliver_captions (offline, a file beside a render)
# ---------------------------------------------------------------------------

def create_captions(args, ctx):
    _require_sha(args)
    config = deliver_config()
    ctx.check_cancel()

    def run():
        r = workflows.create_captions(ctx.session.get(), args["project"], args["timeline"],
                                      language=args["language"], dry_run=args["dry_run"],
                                      project_id=args.get("project_id"),
                                      expect_sha=args.get("plan_sha"), config=config,
                                      check_cancel=ctx.check_cancel)
        tl, st = r["timeline"]["name"], r["settings"]
        how = (f"{st['SUBTITLE_CHARS_PER_LINE']} characters per line, "
               f"{st['SUBTITLE_LINE_BREAK']} ({r['shape']})")
        if r["dry_run"]:
            r["summary"] = (f"would transcribe '{tl}' in {r['language']} with {how}; nothing is "
                            "on its subtitle tracks yet. The timeline is made current and the "
                            "Edit page opened for the call, and both are put back after" +
                            _how(r))
        elif r["exit_status"] == 0:
            f = r["first_item"] or {}
            r["summary"] = (f"made {r['items']} caption item(s) on '{tl}' with {how}; the first "
                            f"on subtitle track {f.get('track')}, frames {f.get('start')} to "
                            f"{f.get('end')}" + _ui(r))
        else:
            r["summary"] = (f"create_captions FAILED on '{tl}': no caption item is on the "
                            "timeline" + (" although Resolve's call returned True" if
                                          r["returned"] else "") +
                            "; check it has audio and that this is Resolve Studio" + _ui(r))
        if r.get("warnings"):
            r["summary"] += " WARNING: " + " ".join(r["warnings"])
        return r

    with api.ResolveLock(timeout=WRITE_LOCK_WAIT):
        return run() if args["dry_run"] else _journalled("create_captions", args, run)


def deliver_captions(args, ctx):
    from .. import captions
    _require_sha(args)
    path = _existing(args["file"], "file")
    dest = deliver.destination(deliver_config(), args["destination"])
    ctx.check_cancel()

    def run():
        r = captions.deliver_captions(path, dest, track=args.get("track"),
                                      keep_ttml=args["keep_ttml"], dry_run=args["dry_run"],
                                      expect_sha=args.get("plan_sha"))
        c, st = r["cues"], r["start"]
        shift = (f"taking off its start {st['timecode']} ({st['seconds']:.3f} s)"
                 if st["timecode"] else "with no timecode to take off")
        span = f"{c['count']} cue(s), {c['first'][0]:.3f} s to {c['last'][1]:.3f} s"
        if r["dry_run"]:
            r["summary"] = (f"would write {os.path.basename(r['srt'])} from "
                            f"{os.path.basename(r['ttml'])}, {shift}: {span} of a "
                            f"{r['duration']:.3f} s video; the TTML " +
                            ("stays" if args["keep_ttml"] else "goes to the Trash") + _how(r))
        else:
            r["summary"] = (f"wrote {r['srt']} ({span}, read back and matching the TTML), "
                            f"{shift}; the TTML " +
                            (f"is in {os.path.dirname(r['trashed'])}" if r["trashed"]
                             else "stays beside the file") +
                            ". Next: deliver-fix-loudness if needed, then deliver_check")
        r["exit_status"] = 0
        return r

    return run() if args["dry_run"] else _journalled("deliver_captions", args, run)


# ---------------------------------------------------------------------------
# sync and trim_review_markers
# ---------------------------------------------------------------------------

def sync(args, ctx):
    _require_sha(args)
    try:
        from .. import sync as rpsync  # numpy, before the lock
    except ImportError as e:
        raise RuntimeError(f"sync needs numpy ({e}); run the server on /usr/bin/python3.")
    if args.get("bin"):
        for field in ("reference", "other"):
            _existing(args[field], field)
    ctx.check_cancel()
    # The correlation runs here, before the lock; a short locked read gives it the files.
    measure = None
    with api.ResolveLock(timeout=WRITE_LOCK_WAIT):
        got = workflows.sync_inputs(ctx.session.get(), args["project"], args["reference"],
                                    args["other"], bin=args.get("bin"),
                                    project_id=args.get("project_id"),
                                    autosync=args["autosync"], name=args.get("name"))
    if got:
        kw = {"check_cancel": ctx.check_cancel}
        if args["window_s"]:
            kw["window_s"] = args["window_s"]
        measure = workflows.precomputed_measure(rpsync.measure, got[0], got[1], **kw)
    ctx.check_cancel()

    def run():
        r = workflows.sync(ctx.session.get(), args["project"], args["reference"], args["other"],
                           name=args.get("name"), bin=args.get("bin"), autosync=args["autosync"],
                           window_s=args["window_s"], dry_run=args["dry_run"],
                           project_id=args.get("project_id"), expect_sha=args.get("plan_sha"),
                           check_cancel=ctx.check_cancel, measure=measure)
        m, f = r["measurement"], r["measurement"]["frames"]
        d = m.get("drift") or {}
        where = (f"offset {m['offset_s']:+.4f} s, placed at {f['placed']:+d} frame(s) at "
                 f"{r['rate']} fps (residual {f['residual_ms']:+.1f} ms)")
        drift = ("; drift " + syncbuild.drift_words(d, f["fps"]) if d else
                 f"; {m.get('drift_note', '')}") + "".join(
                     f"; note: {n}" for n in m.get("notes") or [])
        if r["dry_run"]:
            r["summary"] = (f"would build '{r['timeline']}' with {r['other']['name']} on "
                            f"{'V2/A2' if any(p['kind'] == 'video' and p['role'] == 'other' for p in r['plan']) else 'A2'}"
                            f": {where}{drift}" +
                            (f"; imports both files into a new bin '{r['bin']}'" if r["bin"]
                             else "") +
                            ("; then AutoSyncAudio, checked against this offset" if
                             args["autosync"] else "") + _how(r))
        else:
            ok = sum(1 for p in (r["built"] or {}).get("placements", []) if p["ok"])
            n = len((r["built"] or {}).get("placements", []))
            a = r["autosync"]
            r["summary"] = (f"built '{r['timeline']}': {ok} of {n} placement(s) read back as "
                            f"planned; {where}{drift}" +
                            (f"; AutoSyncAudio {a['verdict']}" if a else "") +
                            settingsguard.clause(r.get("settings_drift_rows")) +
                            (f"; PROBLEMS: {'; '.join(r['problems'])}" if r["problems"] else "") +
                            _ui(r))
        return r

    with api.ResolveLock(timeout=WRITE_LOCK_WAIT):
        return run() if args["dry_run"] else _journalled("sync", args, run)


def trim_review_markers(args, ctx):
    from .. import cutlist
    from .tools_offline import review_opts
    _require_sha(args)
    if bool(args.get("manifest")) == bool(args.get("source")):
        raise ValueError("give exactly one of manifest (a cut manifest) or source (a media file).")
    if args.get("manifest"):
        m = cutlist.load_manifest(_existing(args["manifest"], "manifest"))
        one = cutlist.single_source(m, "trim_review_markers")
        source = one["path"]
        words_path = _existing(args["words"], "words") if args.get("words") else one["words"]
    else:
        source = _existing(args["source"], "source")
        words_path = _existing(args["words"], "words") if args.get("words") else None
    words = cutlist.load_words(words_path) if words_path else None  # offline, before the lock
    ctx.check_cancel()
    with api.ResolveLock(timeout=WRITE_LOCK_WAIT):  # a short read: which ranges the timeline plays
        ranges = workflows.trim_ranges(ctx.session.get(), args["project"], args["timeline"],
                                       source, project_id=args.get("project_id"))
    review = (ranges, trimreview.review_source(source, words, ranges=ranges, audio=args["audio"],
                                               **review_opts(args)))  # offline, before the lock
    ctx.check_cancel()

    def run():
        r = workflows.trim_review_markers(ctx.session.get(), args["project"], args["timeline"],
                                          source, words, audio=args["audio"],
                                          review_opts=review_opts(args), dry_run=args["dry_run"],
                                          project_id=args.get("project_id"),
                                          expect_sha=args.get("plan_sha"),
                                          check_cancel=ctx.check_cancel, review=review)
        tl, c = r["timeline"]["name"], r["counts"]["kind"]
        kinds = ", ".join(f"{n} {k}" for k, n in c.items() if n)
        why = {}
        for x in r["refused"]:
            why[x["reason"]] = why.get(x["reason"], 0) + 1
        refused = ("; refused, left unmarked: " + ", ".join(f"{n} ({k})" for k, n in why.items())
                   if why else "")
        if r["dry_run"]:
            r["summary"] = (f"would add {len(r['planned'])} marker(s) to '{tl}' ({kinds or 'no rows'})"
                            f"{refused}; nothing is cut" + _how(r))
        else:
            ok = sum(1 for x in r["results"] if x["ok"])
            r["summary"] = (f"added {ok} of {len(r['results'])} marker(s) to '{tl}', each read "
                            f"back{refused}; nothing was cut" +
                            (f"; PROBLEMS: {'; '.join(r['problems'])}" if r.get("problems")
                             else "") + _ui(r))
        return r

    with api.ResolveLock(timeout=WRITE_LOCK_WAIT):
        return run() if args["dry_run"] else _journalled("trim_review_markers", args, run)


def link_proxies(args, ctx):
    _require_sha(args)

    def run():
        r = workflows.link_proxies(ctx.session.get(), args["project"], folder=args.get("folder"),
                                   dry_run=args["dry_run"], project_id=args.get("project_id"),
                                   check_cancel=ctx.check_cancel, expect_sha=args.get("plan_sha"))
        c = ", ".join(f"{n} {k}" for k, n in sorted(r["counts"].items()))
        r["summary"] = f"proxies in {r['folder']}: {c or 'no BRAW clips'}" + _how(r)
        return r

    with api.ResolveLock(timeout=WRITE_LOCK_WAIT):
        return run() if args["dry_run"] else _journalled("link_proxies", args, run)


def relink(args, ctx):
    _require_sha(args)
    roots = []
    for p in args["search_roots"]:
        root = _existing(p, "search_roots")
        if not os.path.isdir(root):
            raise ValueError(f"search_roots {root} is not a folder.")
        roots.append(os.path.normpath(root))
    ctx.check_cancel()
    index = rprelink.index_roots(roots, check=ctx.check_cancel)  # the walk, before the lock

    def run():
        r = workflows.relink(ctx.session.get(), args["project"], roots, folder=args.get("folder"),
                             dry_run=args["dry_run"], project_id=args.get("project_id"),
                             max_items=args.get("max_items"), check_cancel=ctx.check_cancel,
                             expect_sha=args.get("plan_sha"), index=index)
        sc = r["status_counts"]
        offline = sum(sc.values())
        left = "; ".join(f"{n} {k}" for k, n in sorted(sc.items()) if k != "VERIFIED")
        left = f"; not relinked: {left}" if left else ""
        if r["dry_run"]:
            where = ", ".join(f"{v} ({n})" for v, n in sorted(r["offline_volumes"].items(),
                                                              key=lambda kv: -kv[1])[:5])
            r["summary"] = (f"would relink {sc.get('VERIFIED', 0)} of {offline} offline clip(s) "
                            f"({r['online']} online clip(s) are not touched){left}. Offline paths are "
                            f"under: {where or 'none'}; check that every share and drive you expect "
                            "is mounted, since an unmounted one reads as offline" + _how(r))
        else:
            c = r["counts"]
            r["summary"] = (f"relinked {c.get('relinked', 0)} of {offline} offline clip(s), each "
                            f"read back{left}"
                            + (f"; {c['FAILED']} FAILED" if c.get("FAILED") else "")
                            + (f"; {c['not attempted (max_items)']} not attempted (max_items): "
                               "dry run again to plan the rest"
                               if c.get("not attempted (max_items)") else "")
                            + (f"; PROBLEMS: {'; '.join(r['problems'])}" if r["problems"] else "")
                            + _ui(r))
        return r

    with api.ResolveLock(timeout=WRITE_LOCK_WAIT):
        r = run() if args["dry_run"] else _journalled("relink", args, run)
    page = _page(r.pop("results"), args)
    r["results"] = page.pop("rows")
    r.update(page)
    return r


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
        "[auto]' timeline per clip from the source's frames, plus reframed 9:16 and 1:1 "
        "versions ('<prefix>_<clip>_9x16 [auto]', '_1x1 [auto]'; aspects) whose items each "
        "take their span's crop (from reframe_plan) or the clip's face_x, reading every item "
        "and transform back. A manifest with several sources builds the 16:9 with each item from "
        "its own source's media-pool clip (import every source first) and no reframed version. "
        "A version with no reframe is not built and is named UNREFRAMED; "
        "one whose picture does not fill the frame is built and named BARS. Clips that fail "
        "endcheck are skipped unless force is true. Each timeline that already exists is "
        "skipped; a missing version beside an existing 16:9 is built from it when its items "
        "still match the manifest's spans, and refused when they do not. When the settings "
        "guard is enabled (RPRESOLVE_SETTINGS_GUARD=on; off by default), a timeline whose "
        "settings read differently after its custom-settings write is named SETTINGS DRIFT "
        "(settings_drift_rows); it is reported, nothing is set back, and the exit status does "
        "not change. "
        "Pass the operator's exclude list as exclude: a manifest in which a span of a clip "
        "asked for plays from a source on it is refused (endcheck or not), and the list's hash "
        "is part of the plan_sha. Omitted, nothing is checked and the result's exclude_note "
        "says so. "
        "Dry run first; the real run needs its plan_sha.",
        {"type": "object", "properties": {
            **PROJECT,
            "manifest": {"type": "string", "description": "Absolute path of the manifest JSON."},
            "exclude": {"type": "string",
                        "description": "Absolute path of the operator's exclude list, JSON "
                        "with `takes`, a list of file stems, names or path suffixes. A "
                        "manifest naming one as a span's source is refused."},
            "prefix": {"type": "string", "pattern": "^[A-Za-z0-9-]{1,24}$", "default": "SW",
                       "description": "Timeline name prefix."},
            "clips": {"type": "array", "items": {"type": "string"},
                      "description": "Only these clip names."},
            "force": {"type": "boolean", "default": False,
                      "description": "Build clips that fail endcheck too (reported)."},
            "audio": {"type": "boolean", "default": True,
                      "description": "Run endcheck's audio check."},
            "make_9x16": {"type": "boolean", "default": True},
            "aspects": {"type": "array", "items": {"type": "string", "enum": ["9x16", "1x1"]},
                        "uniqueItems": True,
                        "description": "Versions to build beside the 16:9 (default 9x16; "
                        "[] for none). Each item takes its span's reframe from reframe_plan, "
                        "else the clip's face_x; a version with neither is named unreframed "
                        "and not built."},
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
        "set_current_timeline",
        "Make an ' [auto]' timeline the project's current timeline, so frame-reading tools "
        "(which render only the current timeline) can look at it, then read the current timeline "
        "back. Returns {previous, previous_unique_id, current}; pass the previous_unique_id (or "
        "the name, if unique) as restore_to to put the operator's own timeline back. restore_to "
        "is the one exception to [auto]-only: it accepts any timeline of the project, by name "
        "or unique id. Changes nothing else: no "
        "page switch, no playhead move beyond what Resolve itself does. Give exactly one of "
        "timeline or restore_to. Dry run first; the real run needs its plan_sha.",
        {"type": "object", "properties": {
            **PROJECT,
            "timeline": {"type": "string", "pattern": " \\[auto\\]$",
                         "description": "Name of the ' [auto]' timeline to make current."},
            "restore_to": {"type": "string", "minLength": 1,
                           "description": "Unique id or name of the timeline to put back (the "
                           "`previous_unique_id` or `previous` an earlier call returned); "
                           "any timeline of the project is accepted, [auto] or not."},
            **DRY_RUN},
         "required": ["project"], "additionalProperties": False},
        set_current_timeline, title="Make an [auto] timeline current", annotations=WRITE))
    registry.add(Tool(
        "set_color_management",
        "Set the colour-management Project Settings that `ingest` needs (preset 'managed': "
        "colorScienceMode davinciYRGBColorManagedv2) on the open project, each value read "
        "back, then every key read again to catch one Resolve reset. Refuses unless the "
        "project is fresh: no timelines and no clips in the media pool, because changing "
        "colour science under existing work changes how it looks. Never creates, loads or "
        "saves a project: a person makes the project in Resolve, then calls this. Dry run "
        "first; the real run needs its plan_sha.",
        {"type": "object", "properties": {
            **PROJECT,
            "preset": {"type": "string", "enum": ["managed"], "default": "managed",
                       "description": "The settings to apply (rpresolve.colormgmt.PRESETS)."},
            **DRY_RUN},
         "required": ["project"], "additionalProperties": False},
        set_color_management, title="Set colour management on a fresh project",
        annotations=DESTRUCTIVE))
    registry.add(Tool(
        "timeline_from_clips",
        "Build a new timeline whose name ends ' [auto]' from media-pool clips, one after "
        "another in a stated order: the clips directly in a bin (bin, as a path from the root "
        "such as 'GH7'), or clips named in `clips` (unique ids, absolute file paths or "
        "names). order is name or path (natural order, so P2 comes before P10) or given. "
        "Picture only, on V1, following the project's frame rate and size; a clip at another "
        "frame rate is conformed by Resolve to real time and the plan says so. fps 'filmed' "
        "instead makes the timeline at the rate the clips were filmed at, each cut to the "
        "frames its file holds; clips filmed at different rates are refused. width and height "
        "set the timeline's size. Items in a bin "
        "that cannot be placed (timelines, audio-only clips) are listed under skipped. Every "
        "item is read back (the clip, its first source frame, its length, no gap or overlap). "
        "The timeline is added to the media-pool bin that is open in Resolve (lands_in_bin). "
        "Nothing existing is modified or removed; a timeline that does not read back is named "
        "under left_behind. The current timeline, playhead and page are put back. Dry run "
        "first; the real run needs its plan_sha.",
        {"type": "object", "properties": {
            **PROJECT,
            "name": {"type": "string", "pattern": " \\[auto\\]$", "minLength": 8,
                     "description": "Name for the timeline; must end ' [auto]'."},
            "bin": {"type": "string", "minLength": 1,
                    "description": "A bin path from the root; its clips are placed. Give this "
                    "or clips."},
            "clips": {"type": "array", "minItems": 1, "maxItems": 500, "uniqueItems": True,
                      "items": {"type": "string", "minLength": 1},
                      "description": "Clips to place, by unique id, absolute file path or "
                      "clip name. Give this or bin."},
            "order": {"type": "string", "enum": ["name", "path", "given"], "default": "name",
                      "description": "name or path: natural order of the clip name or file "
                      "path. given: the order of clips (or the bin as Resolve lists it)."},
            "fps": {"type": "string", "enum": ["filmed"],
                    "description": "filmed: the timeline takes the rate the clips were filmed "
                    "at. Default: the project's."},
            "width": {"type": "integer", "minimum": 16, "maximum": 16384,
                      "description": "Timeline width; give height too."},
            "height": {"type": "integer", "minimum": 16, "maximum": 16384,
                       "description": "Timeline height; give width too."},
            **DRY_RUN,
            **PAGING, "limit": _limit(100, 500)},
         "required": ["project", "name"], "additionalProperties": False},
        timeline_from_clips, title="Build a timeline from clips", annotations=WRITE))
    registry.add(Tool(
        "import_timeline",
        "Import an FCPXML file as a new timeline whose name ends ' [auto]', its source clips "
        "imported into the media-pool bin open in Resolve (lands_in_bin). The plan lists what "
        "the file says: every clip with its lane, record position and length, the transitions, "
        "and media files missing on this machine. After the real run every video clip is read "
        "back at its record position and length on some video track (and its ZoomX where the "
        "file set a static scale), and every transition at its position and length, with the "
        "kind Resolve placed (a Dip to Color imports as a Cross Dissolve). Keyframed "
        "transforms and retimes are not read back. Nothing "
        "existing is modified or removed; a timeline that does not read back is named under "
        "left_behind. The current timeline, playhead and page are put back. Pass the operator's "
        "exclude list as exclude: a file with a clip that plays from a file on it is refused, "
        "and the list's hash is part of the plan_sha. Omitted, nothing is checked and the "
        "result's exclude_note says so. Dry run first; the real run needs its plan_sha.",
        {"type": "object", "properties": {
            **PROJECT,
            "name": {"type": "string", "pattern": " \\[auto\\]$", "minLength": 8,
                     "description": "Name for the timeline; must end ' [auto]'."},
            "file": {"type": "string", "minLength": 1,
                     "description": "Absolute path of the .fcpxml file (rpresolve.interchange "
                     "or reel write one)."},
            "exclude": {"type": "string",
                        "description": "Absolute path of the operator's exclude list, JSON "
                        "with `takes`, a list of file stems, names or path suffixes. A file "
                        "with a clip from one is refused."},
            **DRY_RUN,
            **PAGING, "limit": _limit(100, 500)},
         "required": ["project", "name", "file"], "additionalProperties": False},
        import_timeline, title="Import a timeline from FCPXML", annotations=WRITE))
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
            "track": {"type": "integer", "minimum": 1, "default": 1,
                      "description": "The video track (default V1). A montage assembled with a "
                      "scale-window or crop-window is on track 2; V1 holds only its plates."},
            "items": {"type": "array", "minItems": 1, "items": {"type": "integer", "minimum": 1},
                      "uniqueItems": True,
                      "description": "Raw item positions on the track, from 1, a dissolve counted "
                                     "(timeline_items index; default: all)."},
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
        "apply_effect",
        "Apply a Fusion macro from production/fusion-macros (effect: its id) to items on one "
        "video track of an ' [auto]' timeline only: add a new Fusion comp named 'rp-fx "
        "<effect>' to each clip, build the macro's tools in it with their keyframes, wire them "
        "between MediaIn1 and MediaOut1, and read the comp back (tools, keyframes, no handle "
        "overshoot, wiring). Nothing existing changes: the comp is new, and an item that "
        "already has a Fusion comp is refused, since the new comp would become the one "
        "that renders, unless all its comps are emptied 'rp-fx' comps (only MediaIn1 and "
        "MediaOut1), which are built into again (plan row 'reuse'). Values are the macro's own; three need "
        "an eye before delivery (person-matte-text's placeholder matte, exposure-flash's "
        "strength, text-word's font and colour; fusion-macros.md). params sets the macro's "
        "whitelisted values (text, size, centre; gain for exposure-flash; speed, speed_to for "
        "retime, a constant speed set on the item itself with no comp; pan_from, pan_to, pan_base, "
        "fade_out for reframe-pan); an unknown key for the macro is refused and each value "
        "is read back. Clips are counted as vertical_apply counts them: a dissolve is a track "
        "item with no clip and is not a number, so items are clip numbers from 1 and spans are "
        "the manifest's span indices from 0 (clip n is span n - 1); give one of them, not both. "
        "Each target row names the clip number and the raw track position. Dry run first; the "
        "real run needs its plan_sha. With ledger (the draft farm's drafts.json) the effect "
        "is checked against what the draft drew: a span it marked not_drawn is flagged in the "
        "dry run and refused in the real run unless override names the effect.",
        {"type": "object", "properties": {
            **PROJECT,
            "timeline": {"type": "string",
                         "description": "An ' [auto]' timeline's name or unique id."},
            "effect": {"type": "string", "enum": sorted(cmd_effect.WIRING),
                       "description": "The macro id (fusion-macros.md)."},
            "track": {"type": "integer", "minimum": 1, "default": 1,
                      "description": "The video track (default V1). A montage assembled with a "
                      "scale-window or crop-window is on track 2; V1 holds only its plates."},
            "items": {"type": "array", "minItems": 1, "items": {"type": "integer", "minimum": 1},
                      "uniqueItems": True,
                      "description": "Clip numbers on the track, from 1, transitions not "
                                     "counted (default: all clips). Not with spans."},
            "spans": {"type": "array", "minItems": 1,
                      "items": {"type": "integer", "minimum": 0}, "uniqueItems": True,
                      "description": "The manifest's span indices, from 0 (an effects entry's "
                                     "span_index): span i is clip i + 1. Not with items."},
            "ledger": {"type": "string",
                       "description": "Absolute path of the plan's drafts.json from the draft "
                       "farm. The build applies only what the draft drew: the dry run flags "
                       "each effect the draft marked not_drawn, the real run refuses unless "
                       "override names it. Omitted, nothing is checked and the result says so."},
            "draft": {"type": "string",
                      "description": "The picked plan's name in the ledger's drafts; needed "
                      "when the file holds more than one."},
            "override": {"type": "array", "items": {"type": "string"}, "uniqueItems": True,
                         "description": "Effect names to apply although the draft did not draw "
                         "them. Part of the plan_sha, so name them in the dry run too."},
            "params": {"type": "object", "additionalProperties": False, "properties": {
                "text": {"type": "string", "minLength": 1, "maxLength": 80},
                "size": {"type": "number"},
                "centre": {"type": "number", "minimum": 0, "maximum": 1},
                "gain": {"type": "number", "minimum": 1, "maximum": 20},
                "speed": {"type": "number"}, "speed_to": {"type": "number"},
                "pan_from": {"type": "number", "minimum": 0, "maximum": 1},
                "pan_to": {"type": "number", "minimum": 0, "maximum": 1},
                "pan_base": {"type": "number", "minimum": 0, "maximum": 1},
                "fade_out": {"type": "number", "minimum": 0, "maximum": 5}},
                "description": "Macro settings: text, size (text-word, person-matte-text); "
                               "centre, 0 to 1 across the frame (zoom-push, text-word); gain, 1 to 20, default 2 "
                               "(exposure-flash); speed "
                               "and optional speed_to, which makes a ramp comp (retime); "
                               "pan_from, pan_to, optional pan_base, optional fade_out in "
                               "seconds, 0 to 5 (reframe-pan: the picture also fades to "
                               "black over the item's last fade_out seconds)."},
            **DRY_RUN},
         "required": ["project", "timeline", "effect"], "additionalProperties": False},
        apply_effect, title="Apply a Fusion effect", annotations=WRITE))
    registry.add(Tool(
        "assemble_timeline",
        "Build a method manifest (methods.to_manifest, with optional music {path, in}) as a "
        "new ' [auto]' timeline with MediaPool.AppendToTimeline: every span is cut from its "
        "clip at the clip's own frame rate, so 30 and 60 fps footage lands on a 24 fps "
        "timeline (import_timeline reads FCPXML, which holds one rate for both). The clips "
        "it needs are imported into a bin named 'methods [auto]', the new timeline gets its "
        "own size and fill-and-crop scaling, a span's static scale becomes the item's zoom, "
        "and the music goes on A1 from the first frame through the end black as a bed that "
        "ffmpeg cuts first (the in point and a one second fade out baked into a 24-bit 48 kHz "
        "WAV beside the manifest, or in bed_dir; refused inside a git tree). SetFades is not "
        "used: it reads back and does not render (T18). "
        "Nothing existing changes. Refused: a name that exists or does not end ' [auto]', a "
        "manifest whose fps is not the project's timeline rate, a missing file, a song that "
        "runs out before the bed does. A dissolve is "
        "added with AddTransition (Cross Dissolve, centred on the cut) and its length read "
        "back; any other transition is placed as a cut and named in the plan; effects are "
        "apply_effect's work. Colour (T30): detect runs on every source and the result lists, "
        "per file, the input colour space detect found and what a clip already in the methods "
        "[auto] bin reads. On a colour-managed project a clip this run imports and detect can "
        "pin gets Input Color Space and Data Level (written and read back; a file in another "
        "bin is imported again as a new clip, which is tagged); a clip that was in the "
        "pool is never written, and one that is log by detect and reads Rec.709 is a "
        "problem; a file detect cannot pin gets a note. An unmanaged project is not tagged. "
        "Every item's length is read back. A scale-window or crop-window on any span after the "
        "first opens over the shot before it playing on: the montage then goes on V2 (every "
        "span in its order and at its frame, so item N is still span N) and V1 holds a plate "
        "under each window span, the previous clip counted on from where its span stopped "
        "(37 frames for scale-window, 26 for crop-window, or the span's length when shorter). "
        "Refused when that source ends inside its plate. The result's track says where the "
        "montage is: pass it as track to apply_effect, apply_grade, reframe_apply, stabilize "
        "and vertical_apply, and grade V1 too. With no such span the montage is on V1 alone. "
        "Pass the operator's exclude list as exclude: a manifest naming a take on it is "
        "refused (a span's source or the music file), and the list's hash is part of the plan_sha. "
        "Omitted, nothing is checked and the result's exclude_note says so. With ledger (the draft farm's "
        "drafts.json) and draft, each manifest effect the draft marked not_drawn is flagged in "
        "the dry run and refused in the real run unless override names it; a ledger that is "
        "not this manifest's is refused. "
        "Dry run first; the real run needs its plan_sha.",
        {"type": "object", "properties": {
            **PROJECT,
            "name": {"type": "string", "description": "The new timeline's name, ending ' [auto]'."},
            "manifest": {"type": "string",
                         "description": "Absolute path of the method manifest JSON."},
            "exclude": {"type": "string",
                        "description": "Absolute path of the operator's exclude list, JSON "
                        "with `takes`, a list of file stems, names or path suffixes. A "
                        "manifest naming one is refused."},
            "ledger": {"type": "string",
                       "description": "Absolute path of the plan's drafts.json from the draft "
                       "farm. The build applies only what the draft drew: the dry run flags "
                       "each effect the draft marked not_drawn, the real run refuses unless "
                       "override names it. Omitted, nothing is checked and the result says so."},
            "draft": {"type": "string",
                      "description": "The picked plan's name in the ledger's drafts; needed "
                      "when the file holds more than one."},
            "override": {"type": "array", "items": {"type": "string"}, "uniqueItems": True,
                         "description": "Effect names to apply although the draft did not draw "
                         "them. Part of the plan_sha, so name them in the dry run too."},
            "bed_dir": {"type": "string",
                        "description": "Absolute folder for the baked music bed; default the "
                        "manifest's own folder. Refused inside a git working tree."},
            "width": {"type": "integer", "minimum": 16, "default": cmd_assemble.SIZE[0]},
            "height": {"type": "integer", "minimum": 16, "default": cmd_assemble.SIZE[1]},
            **DRY_RUN},
         "required": ["project", "name", "manifest"], "additionalProperties": False},
        assemble_timeline, title="Assemble a method timeline", annotations=WRITE))
    registry.add(Tool(
        "vertical_apply",
        "Finish a 16:9 ' [auto]' montage as 9:16 from a reviewed crops.json (written offline by "
        "`resolve_workflow.py vertical plan`, marked by `vertical review` once its sheet has "
        "been read). When the target timeline does not exist, source_timeline is duplicated to "
        "it and the copy alone is set to 1080x1920; an existing target must already be "
        "1080x1920 and is never resized. On each item of one video track it sets the item's "
        "own Scaling to 3 (fill), Pan to (0.5 - cx) x 1080 x zoom and Tilt to 0, moves the "
        "pivot of an 'rp-fx zoom-push' comp, the centre and size of an 'rp-fx text-word' "
        "comp and the word of an 'rp-fx person-matte-text' comp to the crop centre (its matte "
        "stays on the subject and is not moved), re-renders those comps and reads everything back. Refused, "
        "writing nothing: a target not ending ' [auto]', crops.json not reviewed, an item "
        "count, name, file or zoom that differs from crops.json. On track 2 or above, a plate "
        "on the track below (an item that starts where montage item N starts and plays the file "
        "of item N - 1) gets item N - 1's crop, with its own zoom, so the outgoing shot does not "
        "move at the cut; other items on that track are left alone and named in unframed_below. "
        "Dry run first; the real run needs its plan_sha.",
        {"type": "object", "properties": {
            **PROJECT,
            "timeline": {"type": "string",
                         "description": "The 9:16 target's name, ending ' [auto]'."},
            "crops": {"type": "string",
                      "description": "Absolute path of the reviewed crops.json."},
            "source_timeline": {"type": "string",
                                "description": "The 16:9 timeline to duplicate when the target "
                                               "does not exist yet (name or unique id)."},
            "track": {"type": "integer", "minimum": 1, "default": 1,
                      "description": "The video track (default V1). A montage assembled with a "
                      "scale-window or crop-window is on track 2; V1 holds only its plates."},
            "text_size": {"type": "number", "minimum": 0.001, "maximum": 1,
                          "description": "Text1.Size for every text-word comp (default: fitted "
                                         "to each word)."},
            **DRY_RUN},
         "required": ["project", "timeline", "crops"], "additionalProperties": False},
        vertical_apply, title="Finish a montage as 9:16", annotations=DESTRUCTIVE))
    registry.add(Tool(
        "reframe_apply",
        "Set ZoomX, ZoomY, Pan and Tilt on video items of one track of an ' [auto]' timeline "
        "from a framing plan (reframe_plan with items), and read each back. Each item takes "
        "the plan entry for its file; the props are computed for this timeline's size and "
        "input scaling, which must be the plan's. Refused: a timeline not ending ' [auto]', "
        "an item with its own Scaling, a clip of another resolution than planned, an entry "
        "with no crop, a zoom below the fill scale (black would show) unless allow_bars. An "
        "entry whose move is 'none' writes nothing: centre fill already keeps the head. "
        "After the real run every item on other timelines using the same media is compared "
        "and any change is reported as a leak. Dry run first; the real run needs its "
        "plan_sha.",
        {"type": "object", "properties": {
            **PROJECT,
            "timeline": {"type": "string",
                         "description": "An ' [auto]' timeline's name or unique id."},
            "plan": {"type": "string",
                     "description": "Absolute path of the framing plan JSON reframe_plan wrote."},
            "track": {"type": "integer", "minimum": 1, "default": 1,
                      "description": "The video track (default V1). A montage assembled with a "
                      "scale-window or crop-window is on track 2; V1 holds only its plates."},
            "items": {"type": "array", "minItems": 1, "items": {"type": "integer", "minimum": 1},
                      "uniqueItems": True,
                      "description": "Raw item positions on the track, from 1, a dissolve counted "
                                     "(timeline_items index; default: all)."},
            "allow_bars": {"type": "boolean", "default": False,
                           "description": "Allow a scale below the fill scale (black bars)."},
            **DRY_RUN},
         "required": ["project", "timeline", "plan"], "additionalProperties": False},
        reframe_apply, title="Apply a framing plan", annotations=DESTRUCTIVE))
    registry.add(Tool(
        "stabilize",
        "Call Resolve's TimelineItem.Stabilize() on video items of one track of an ' [auto]' "
        "timeline only (any other timeline is refused). It makes the timeline current, opens "
        "the Color page and puts the playhead at each item's start, then calls Stabilize() "
        "item by item and records what it returned (True or False) and the seconds it took. "
        "Stabilize() uses each item's current stabilizer settings (mode, strength, smooth, "
        "zoom) from the Color page, which the API cannot set. THE EFFECT IS NOT VERIFIED: "
        "stabilization is not visible through GetProperty (a 2026-09-26 spike found "
        "SmartReframe's result invisible there; the same is assumed), so the tool reports "
        "only what Stabilize() returned. It can take a long time on large (6K) items, and the "
        "call holds the Resolve lock throughout, so one call does at most max_items (default "
        "10, at most 25) and names the rest; stabilize a small sample first and check it by eye. A cancel stops before the next "
        "item. The current timeline, page and playhead are put back. Dry run first; the real "
        "run needs its plan_sha.",
        {"type": "object", "properties": {
            **PROJECT,
            "timeline": {"type": "string",
                         "description": "An ' [auto]' timeline's name or unique id."},
            "track": {"type": "integer", "minimum": 1, "default": 1,
                      "description": "The video track (default V1). A montage assembled with a "
                      "scale-window or crop-window is on track 2; V1 holds only its plates."},
            "items": {"type": "array", "minItems": 1, "items": {"type": "integer", "minimum": 1},
                      "uniqueItems": True,
                      "description": "Raw item positions on the track, from 1, a dissolve counted "
                                     "(timeline_items index; default: all)."},
            "max_items": {"type": "integer", "minimum": 1, "maximum": 25, "default": 10,
                          "description": "Stabilize at most this many items per call, the first "
                          "of the selection (default 10, at most 25: one call holds the Resolve "
                          "lock); the result's remaining lists the item numbers left."},
            **DRY_RUN},
         "required": ["project", "timeline"], "additionalProperties": False},
        stabilize, title="Stabilize items", annotations=DESTRUCTIVE))
    registry.add(Tool(
        "queue_render",
        "Queue one render job for a timeline for a delivery destination (with name and "
        "target_dir); a destination is the only way to queue a render. It never starts a "
        "render: the operator starts renders. A destination sets format, codec, size, frame rate, "
        "audio, colour tags and captions, and names the file by the house rule "
        "(SW001_Guest_01_example-clip_16x9.mp4, Client_slug_master.mov, or for a music "
        "montage Client_slug_9x16.mp4); it warns, and still queues, when files in the target "
        "folder or its destination subfolders carry another client prefix; it is refused "
        "when the folder is missing, on an unmounted share or in a git repository, when the "
        "file or its sidecar exists or a queued job writes it, and when captions are wanted "
        "but the timeline has no subtitle track. The timeline is made current only while "
        "queueing; the current timeline, page and the Deliver page's format and codec are put "
        "back. The other Deliver fields it sets cannot be read back or restored, and every "
        "result lists them, with what Resolve's job list holds. Refused while a render runs. "
        "Dry run first; the real run needs its plan_sha.",
        {"type": "object", "properties": {
            **PROJECT,
            "timeline": {"type": "string", "description": "Timeline name or unique id."},
            "destination": {"type": "string", "minLength": 1,
                            "description": "A delivery destination, a key in the config's "
                            "destinations (checked when called, so an overlay edit needs no "
                            "restart; at start: " + ", ".join(destination_keys()) + ")."},
            "name": {"type": "object", "properties": {
                "show": {"type": "string", "pattern": f"^{deliver.SHOW}$",
                         "description": "Show code in capitals, such as SW."},
                "episode": {"type": "integer", "minimum": 0, "maximum": 999},
                "guest": {"type": "string", "pattern": f"^{deliver.WORD}$",
                          "description": "Letters and digits only."},
                "index": {"type": "integer", "minimum": 1, "maximum": 99,
                          "description": "Clip number."},
                "slug": {"type": "string", "pattern": f"^{deliver.SLUG}$",
                         "description": "Lowercase words joined by hyphens."},
                "client": {"type": "string", "pattern": f"^{deliver.WORD}$",
                           "description": "client_master and montage destinations only."}},
                "additionalProperties": False,
                "description": "Show, episode, guest, index and slug; for client_master and "
                "the montage destinations (social_9x16_music), client and slug."},
            "target_dir": {"type": "string",
                           "description": "Absolute path of an existing folder outside any "
                           "git repository (default: the destination's target_dir in the "
                           "config)."},
            **DRY_RUN},
         "required": ["project", "timeline", "destination"],
         "additionalProperties": False},
        queue_render, title="Queue a render", annotations=WRITE,
        retired_args=QUEUE_RENDER_RETIRED))
    registry.add(Tool(
        "create_captions",
        "Transcribe an ' [auto]' timeline's audio into captions with Resolve's auto captions "
        "(Timeline.CreateSubtitlesFromAudio, Resolve Studio). Characters per line and line "
        "breaks follow the timeline's shape from the config's deliver.captions (default 42 on "
        "one line for landscape, 16 on two lines for portrait, 24 on two lines for square), so "
        "burnt-in captions fit a 9:16 frame. Refused when the timeline already has any subtitle "
        "item (additive only), while a render runs, and when Resolve lacks a constant the "
        "settings need. The timeline is made current and the Edit page opened for the call "
        "(from the Deliver page Resolve returns False); the current timeline and page are put "
        "back. The subtitle tracks are read back: a call that returns True but "
        "leaves no item is a failure. Can take about as long as the timeline runs. Dry run "
        "first; the real run needs its plan_sha. The result's warnings name spans where a "
        "source's <name>.kinds.json sidecar (from `vertcut transcribe`) says the audio is sung "
        "or music, and sources with no sidecar: Resolve's auto captions cannot skip them, so "
        "relabel or delete those caption blocks by hand.",
        {"type": "object", "properties": {
            **PROJECT,
            "timeline": {"type": "string",
                         "description": "An ' [auto]' timeline's name or unique id."},
            "language": {"type": "string", "enum": sorted(workflows.CAPTION_LANGUAGES),
                         "default": "en", "description": "The spoken language."},
            **DRY_RUN},
         "required": ["project", "timeline"], "additionalProperties": False},
        create_captions, title="Create auto captions", annotations=WRITE))
    registry.add(Tool(
        "sync",
        "Stack dual-system sound on a new ' [auto]' timeline: the reference camera clip on "
        "V1/A1, the other recording on A2 (or a second camera on V2/A2) at the offset measured "
        "from their audio (as sync_measure), then read every placement back: start and source "
        "start exactly as planned, length within a frame. reference and other are media-pool "
        "clips (unique id, file path or name), or, with bin, files imported into that new bin "
        "at the pool's root, which the run owns. Only then may autosync run Resolve's "
        "AutoSyncAudio on them; its result is read back through a second [auto] timeline and "
        "compared with the measured offset, never trusted alone. A weak or inconsistent match is "
        "refused; a placement that leaves either end of the overlap more than half a frame out "
        "(rounding plus half the drift) is reported with the retime that would cancel the "
        "drift, never corrected. When the settings guard is enabled "
        "(RPRESOLVE_SETTINGS_GUARD=on; off by default), a timeline whose settings read "
        "differently after its custom-settings write is named SETTINGS DRIFT "
        "(settings_drift_rows); it is reported, nothing is set back, and the exit status does "
        "not change. "
        "Making a multicam clip stays a hand step. Dry run first; the real run needs its "
        "plan_sha.",
        {"type": "object", "properties": {
            **PROJECT,
            "reference": {"type": "string", "minLength": 1,
                          "description": "The camera clip: a pool clip's unique id, file path "
                          "or name; with bin, an absolute file path."},
            "other": {"type": "string", "minLength": 1,
                      "description": "The audio file or second camera, the same way."},
            "bin": {"type": "string", "pattern": "^[A-Za-z0-9 _.()-]{1,80}$",
                    "description": "Import the two files into this new bin (it must not exist)."},
            "name": {"type": "string", "pattern": " \\[auto\\]$",
                     "description": "Timeline name; must end ' [auto]' (default: '<reference> "
                     "sync [auto]')."},
            "autosync": {"type": "boolean", "default": False,
                         "description": "With bin: also run AutoSyncAudio on the imported clips "
                         "and check it."},
            "window_s": {"type": "number", "minimum": 5, "maximum": 300, "default": 30,
                         "description": "Seconds per measuring window."},
            **DRY_RUN},
         "required": ["project", "reference", "other"], "additionalProperties": False},
        sync, title="Sync dual-system sound", annotations=WRITE))
    registry.add(Tool(
        "trim_review_markers",
        "Add trim-review rows as markers on an ' [auto]' timeline: long silences (Blue), "
        "fillers (Yellow), repeats (Purple) and Whisper loops (Red) in the parts of the source "
        "the timeline plays, each with its suggestion and confidence in the note, each read "
        "back with GetMarkers. It adds markers only: nothing is cut, rippled or deleted. A row "
        "that would land on a frame already holding a marker is refused and reported. Give a "
        "cut manifest (its source and words), or a source with its words. The timeline is made "
        "current while marking and the UI is put back. Dry run first; the real run needs its "
        "plan_sha.",
        {"type": "object", "properties": {
            **PROJECT,
            "timeline": {"type": "string",
                         "description": "An ' [auto]' timeline's name or unique id."},
            "manifest": {"type": "string", "description": "Absolute path of a cut manifest."},
            "source": {"type": "string",
                       "description": "Absolute path of the source media, instead of a manifest."},
            "words": {"type": "string",
                      "description": "Absolute path of the word JSON (default: the manifest's; "
                      "without it, silences only)."},
            **REVIEW_PROPS, **DRY_RUN},
         "required": ["project", "timeline"], "additionalProperties": False},
        trim_review_markers, title="Mark silences and fillers", annotations=WRITE))
    registry.add(Tool(
        "relink",
        "Point the open project's offline clips (File Path missing on disk) at files under "
        "search_roots that verify as the same take: start timecode, frame count (one frame of "
        "slack), resolution and frame rate must all be known and agree. A name alone never "
        "relinks; stills and audio-only clips are reported UNVERIFIABLE and left alone; two "
        "verified files of different sizes are AMBIGUOUS and left alone. Online clips are never "
        "touched. Uses Resolve's RelinkClips (the folder only), which should leave the clip's "
        "identity, timeline items, grade and tags alone; each clip is read back, and a property "
        "that reads differently afterwards is reported, never set back. That has not yet been "
        "confirmed on a live project: try it on a scratch project first. This is the one write "
        "that changes an existing clip, and only an offline one. An unmounted share or drive "
        "reads as offline, so check the dry run's offline paths. Keep search_roots narrow: they "
        "are walked while the lock is held. max_items relinks a sample first; dry run again "
        "before the rest. Dry run first; the real run needs its plan_sha.",
        {"type": "object", "properties": {
            **PROJECT,
            "search_roots": {"type": "array", "minItems": 1, "maxItems": 20,
                             "items": {"type": "string"},
                             "description": "Absolute folders to search for the files (walked "
                             "recursively; earlier folders win between identical copies)."},
            "folder": {"type": "string",
                       "description": "Only the clips in this media pool bin and below, "
                       "e.g. 'Source/GH7'. Default: the whole pool."},
            "max_items": {"type": "integer", "minimum": 1, "maximum": 5000,
                          "description": "Relink at most this many verified clips (a sample)."},
            **DRY_RUN,
            "offset": {"type": "integer", "minimum": 0, "default": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100}},
         "required": ["project", "search_roots"], "additionalProperties": False},
        relink, title="Relink offline clips", annotations=DESTRUCTIVE))
    registry.add(Tool(
        "link_proxies",
        "Link each BRAW clip of the open project to its ProRes LT proxy (made in the background "
        "after ingest, ~/Movies/RPProxies/<project>) and set the project to prefer proxies; "
        "every link is read back. A clip no timeline uses then takes the rate it was filmed at. A clip whose proxy has not landed is reported and left "
        "alone, so run it again later. The source file, grades and timelines are not touched. "
        "Dry run first; the real run needs its plan_sha.",
        {"type": "object", "properties": {
            **PROJECT,
            "folder": {"type": "string", "description": "Absolute folder holding the proxies. "
                       "Default: ~/Movies/RPProxies/<project>."},
            **DRY_RUN},
         "required": ["project"], "additionalProperties": False},
        link_proxies, title="Link BRAW proxies", annotations=DESTRUCTIVE))
    registry.add(Tool(
        "deliver_captions",
        "Offline, never Resolve: turn the caption sidecar Resolve renders beside a file for a "
        "sidecar destination ('<stem>_<track>.ttml', IMSC1, timed from the timeline's timecode "
        "such as 01:00:00:00) into the zero-based '<stem>.srt' a platform reads. It takes off "
        "the file's start timecode (ffprobe's timecode tag), refuses when any cue would start "
        "before 0 or end past the video, never overwrites an .srt, reads the new .srt back "
        "against the TTML, and moves the TTML to ~/.Trash unless keep_ttml. Refused for a "
        "burn-in or no-captions destination, inside a git working tree, and when several "
        "tracks' sidecars sit there and track names none. It writes and moves files, so it "
        "plans first like the write tools: dry run, then the real run with its plan_sha.",
        {"type": "object", "properties": {
            "file": {"type": "string", "description": "Absolute path of the rendered file."},
            "destination": {"type": "string", "minLength": 1,
                            "description": "The sidecar destination it was rendered for (at "
                            "start: " + ", ".join(destination_keys()) + ")."},
            "track": {"type": "string", "minLength": 1,
                      "description": "The subtitle track whose sidecar to convert, when Resolve "
                      "wrote more than one."},
            "keep_ttml": {"type": "boolean", "default": False,
                          "description": "Leave the .ttml beside the file."},
            **DRY_RUN},
         "required": ["file", "destination"], "additionalProperties": False},
        deliver_captions, title="Captions sidecar to .srt", annotations=WRITE))
