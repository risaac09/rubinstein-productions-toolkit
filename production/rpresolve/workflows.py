"""
rpresolve.workflows: the orchestration behind the CLI's detect, ingest,
survey and cut, as functions that return a dict and raise typed errors.
The CLI and the MCP server call these, so both enforce the same gates.

Nothing here prints or exits the interpreter. Preconditions that fail raise
Refused (a ResolveAPIError); Resolve problems raise api's own errors;
detect.ToolMissing and cutlist.CutlistError pass through.

Write paths (ingest, cut, duplicate_auto, apply_grade, queue_render and its
destination form, queue_destination, create_captions, sync,
trim_review_markers) pin the open project by name (and unique id when
given), re-check the pin before every batch of writes, and take a
`check_cancel` callable that raises to stop at the next safe point. A dry
run returns a plan_sha; a real run given that sha refuses when the plan it
would carry out differs from the one the dry run showed.
"""

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from . import api, grade
from . import detect as rpdetect
from . import ingest as rpingest

SURVEY_PYTHON = "/opt/homebrew/bin/python3.14"
SURVEY_SCRIPT = Path(__file__).resolve().parent.parent / "resolve_survey.py"


class Refused(api.ResolveAPIError):
    """A precondition failed; nothing was written."""


def plan_sha(*parts):
    """sha256 of the canonical JSON of parts: what a dry run showed."""
    blob = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _check(pin, pm, check_cancel):
    def check():
        if check_cancel:
            check_cancel()
        pin.check(pm)
    return check


# ---------------------------------------------------------------------------
# detect (offline)
# ---------------------------------------------------------------------------

def detect(paths):
    """Classify media under paths. Returns {rows, missing, summary, counts,
    exit_status}: exit_status 0 all pinned, 2 any needs review. Raises
    Refused when no video file was found; ToolMissing passes through."""
    rows, missing = rpdetect.detect_paths(paths)
    if not rows:
        raise Refused("No video files found (directories are searched recursively).")
    corrupt = sum(1 for r in rows if r["profile"] == rpdetect.CORRUPT)
    review = sum(1 for r in rows if rpdetect.needs_review(r)) - corrupt
    return {"rows": rows, "missing": missing, "summary": rpdetect.summarize(rows),
            "counts": {"pinned": len(rows) - review - corrupt, "review": review,
                       "corrupt": corrupt},
            "exit_status": 2 if review or corrupt else 0}


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------

def ingest(resolve, project_name, paths, parent="Source", dry_run=False, project_id=None,
           rows=None, check_cancel=None, expect_sha=None):
    """Detect, plan and (unless dry_run) import and tag, in the open project
    named project_name. Returns {project, dry_run, plan, results, counts,
    missing, flagged, exit_status, plan_sha}. exit_status: 0 done as planned,
    2 something needs a person (Review, corrupt, VFR), 1 any FAILED row."""
    pm, project, pin = api.pin_for_write(resolve, project_name, project_id)
    mode = str(project.GetSetting("colorScienceMode") or "")
    if not mode.startswith("davinciYRGBColorManaged"):
        raise Refused(f"colorScienceMode is '{mode}'. Input color space tags only take effect "
                      "under DaVinci YRGB Color Managed; set it in Project Settings first.")
    missing = []
    if rows is None:
        rows, missing = rpdetect.detect_paths(paths)
    if not rows:
        raise Refused("No video files found (directories are searched recursively).")

    project = pin.check(pm)
    media_pool, root = api.media_pool_root(project)
    plan = rpingest.plan_ingest(rows, rpingest.pool_file_paths(root), parent=parent)
    sha = plan_sha("ingest", pin.unique_id, plan)
    if not dry_run and expect_sha and expect_sha != sha:
        raise Refused("the plan changed since the dry run (files or the media pool differ); "
                      "run the dry run again and review it")
    if dry_run:
        results = [{"path": e["path"], "bin": "/".join(e["bin"] or []), "action": e["action"],
                    "result": "planned" if e["action"] != rpingest.SKIP else "skipped",
                    "input_color_space": e["input_color_space"], "data_level": e["data_level"]}
                   for e in plan]
    else:
        previous = media_pool.GetCurrentFolder()
        try:
            results = rpingest.apply_plan(media_pool, root, plan,
                                          check=_check(pin, pm, check_cancel))
        finally:
            if previous:
                media_pool.SetCurrentFolder(previous)
    counts = {}
    for r in results:
        key = r["result"].split(":")[0]
        counts[key] = counts.get(key, 0) + 1
    # A Review file, a corrupt file, or a VFR clip that must be conformed to
    # CFR before editing needs a person.
    flagged = [e["path"] for e in plan
               if (e["bin"] and e["bin"][-1] == rpingest.REVIEW_BIN)
               or e["profile"] == rpdetect.CORRUPT or e["reason"].startswith("VFR")]
    failed = any(r["result"].startswith("FAILED") for r in results)
    return {"project": {"name": pin.name, "id": pin.unique_id}, "dry_run": dry_run,
            "plan": plan, "results": results, "counts": counts, "missing": missing,
            "flagged": flagged, "plan_sha": sha,
            "exit_status": 1 if failed else (2 if flagged else 0)}


# ---------------------------------------------------------------------------
# survey (offline, Python 3.14 subprocess)
# ---------------------------------------------------------------------------

def survey_command(out, json_out=None, projects=None, projects_dir=None, metadata_cache=None,
                   no_metadata_cache=False, tree_labels=None, python=SURVEY_PYTHON,
                   script=SURVEY_SCRIPT):
    """The argv that runs resolve_survey.py."""
    cmd = [python, str(script), "--out", out]
    if json_out:
        cmd += ["--json", json_out]
    if projects:
        cmd += ["--projects", *projects]
    if projects_dir:
        cmd += ["--projects-dir", projects_dir]
    if metadata_cache:
        cmd += ["--metadata-cache", metadata_cache]
    if no_metadata_cache:
        cmd.append("--no-metadata-cache")
    if tree_labels:
        cmd += ["--tree-labels", *tree_labels]
    return cmd


def survey(out, json_out=None, projects=None, projects_dir=None, metadata_cache=None,
           no_metadata_cache=False, tree_labels=None, capture=False, timeout=None):
    """Run the read-only survey (it reads Project.db snapshots, never
    Resolve). Returns {returncode, out, json_out, stdout_tail, stderr_tail};
    with capture=False the survey's own output goes to this process's
    stdout and stderr. Raises Refused when Python 3.14 or the script is
    missing."""
    if not os.path.exists(SURVEY_PYTHON):
        raise Refused(f"{SURVEY_PYTHON} not found. The survey needs Python 3.14+ "
                      "(compression.zstd); install it with Homebrew (python@3.14).")
    if not SURVEY_SCRIPT.exists():
        raise Refused(f"{SURVEY_SCRIPT} not found; keep resolve_survey.py next to "
                      "resolve_workflow.py.")
    cmd = survey_command(out, json_out, projects, projects_dir, metadata_cache,
                         no_metadata_cache, tree_labels)
    kw = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "encoding": "utf-8",
          "errors": "replace"} if capture else {}
    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, timeout=timeout, **kw)
    except subprocess.TimeoutExpired:
        raise Refused(f"the survey did not finish within {timeout}s")
    tail = (lambda t: (t or "")[-2000:]) if capture else (lambda t: "")
    return {"returncode": proc.returncode, "out": out, "json_out": json_out,
            "stdout_tail": tail(proc.stdout if capture else ""),
            "stderr_tail": tail(proc.stderr if capture else "")}


# ---------------------------------------------------------------------------
# cut
# ---------------------------------------------------------------------------

def cut_gate(manifest_path, only=None, audio=True, force=False):
    """The offline half of cut: load, check the source sha256, run
    endcheck. Returns {manifest, clips (to build), blocked {clip: [reasons]},
    forced}. Raises Refused when the source does not match or no clip may
    be built; CutlistError passes through."""
    from . import cut as rpcut, cutlist
    m = cutlist.load_manifest(manifest_path)
    clips = rpcut.plan_clips(m, only)
    src = m["source_path"]
    if not os.path.isfile(src) or cutlist.sha256_file(src) != m["source_sha256"]:
        raise Refused(f"{src} is missing or no longer matches the manifest's sha256; the "
                      "words were read from a different file.")
    fails = rpcut.gate_clips(m, clips, audio=audio)
    blocked = {k: v for k, v in fails.items() if v}
    if blocked and not force:
        clips = [c for c in clips if c["name"] not in blocked]
    if not clips:
        raise Refused("no clip passed endcheck; nothing to build.")
    return {"manifest": m, "clips": clips, "blocked": blocked, "forced": bool(force and blocked)}


def cut(resolve, project_name, manifest_path, prefix="SW", only=None, force=False, audio=True,
        make_9x16=True, dry_run=False, project_id=None, check_cancel=None, expect_sha=None,
        gate=None):
    """Build [auto] timelines from a manifest in the open project named
    project_name. Returns {project, gate, source_item, would_create,
    skipped_existing, results, ui_restore_problems, plan_sha, exit_status}.
    A dry run stops before anything is created."""
    from . import cut as rpcut
    gate = gate or cut_gate(manifest_path, only, audio, force)
    m, clips = gate["manifest"], gate["clips"]
    pm, project, pin = api.pin_for_write(resolve, project_name, project_id)
    media_pool, root = api.media_pool_root(project)
    item = rpcut.find_source_item(root, m["source_sha256"], os.path.getsize(m["source_path"]))
    if item is None:
        raise Refused("no media-pool clip in this project matches the manifest source's "
                      "sha256. Import the source first.")
    src_width = int(str(item.GetClipProperty("Resolution") or "0x0").split("x")[0] or 0)
    existing = rpcut.timeline_names(project)
    skipped = [f"{prefix}_{c['name']}{rpcut.AUTO}" for c in clips
               if f"{prefix}_{c['name']}{rpcut.AUTO}" in existing]
    todo = [c for c in clips if f"{prefix}_{c['name']}{rpcut.AUTO}" not in existing]
    would = []
    for c in todo:
        would.append(f"{prefix}_{c['name']}{rpcut.AUTO}")
        if make_9x16 and (c.get("reframe") or {}).get("face_x") is not None:
            would.append(f"{prefix}_{c['name']}_9x16{rpcut.AUTO}")
    sha = plan_sha("cut", pin.unique_id, prefix, m["source_sha256"], make_9x16,
                   [{"name": c["name"], "spans": c["spans"], "reframe": c.get("reframe")}
                    for c in todo])
    out = {"project": {"name": pin.name, "id": pin.unique_id},
           "gate": {"blocked": gate["blocked"], "forced": gate["forced"]},
           "source_item": {"name": item.GetName(), "file_path": item.GetClipProperty("File Path")},
           "would_create": would, "skipped_existing": skipped, "results": [],
           "ui_restore_problems": [], "plan_sha": sha, "dry_run": dry_run, "exit_status": 0}
    if dry_run:
        return out
    if expect_sha and expect_sha != sha:
        raise Refused("the plan changed since the dry run (manifest, project or existing "
                      "timelines differ); run the dry run again and review it")
    check = _check(pin, pm, check_cancel)
    failed = False
    snap = api.UISnapshot(resolve, project)
    with snap:
        for clip in todo:
            r = rpcut.build_clip(project, media_pool, item, clip, m["fps"], prefix, check=check)
            r["kind"] = "16x9"
            out["results"].append(r)
            failed |= not r["ok"]
            if r["ok"] and make_9x16:
                wide = rpcut.timeline_names(project).get(r["name"])
                t = rpcut.build_tall(project, wide, clip, src_width, prefix, check=check)
                t["kind"] = "9x16"
                out["results"].append(t)
                failed |= not t["ok"]
    out["ui_restore_problems"] = snap.problems
    out["exit_status"] = 1 if failed else 0
    return out


# ---------------------------------------------------------------------------
# duplicate, grade, queue a render ([auto] timelines, the MCP write tools)
# ---------------------------------------------------------------------------

AUTO = " [auto]"
TRACK_KINDS = ("video", "audio", "subtitle")


def _item_frames(tl):
    """Per track kind and index, each item's (start, end, source start,
    source end): what a duplicate must reproduce."""
    out = {}
    for kind in TRACK_KINDS:
        for t in range(1, int(api._safe_call(tl, "GetTrackCount", kind) or 0) + 1):
            out[f"{kind}{t}"] = [(api._safe_call(it, "GetStart"), api._safe_call(it, "GetEnd"),
                                  api._safe_call(it, "GetSourceStartFrame"),
                                  api._safe_call(it, "GetSourceEndFrame"))
                                 for it in tl.GetItemListInTrack(kind, t) or []]
    return out


def duplicate_auto(resolve, project_name, timeline, new_name=None, dry_run=False,
                   project_id=None, expect_sha=None):
    """Duplicate a timeline as a new ' [auto]' timeline, compare every
    item's frames with the origin, and put the current timeline and page
    back (DuplicateTimeline makes the copy current). Returns {project,
    origin, new_name, plan_sha, dry_run, created, mismatches,
    ui_restore_problems, exit_status}."""
    pm, project, pin = api.pin_for_write(resolve, project_name, project_id)
    _, origin = api.find_timeline(project, timeline)
    origin_name, origin_id = origin.GetName(), origin.GetUniqueId()
    name = new_name or (origin_name if origin_name.endswith(AUTO) else origin_name + AUTO)
    if name == origin_name:
        raise Refused(f"'{origin_name}' is already an [auto] timeline; give new_name.")
    if not name.endswith(AUTO):
        raise Refused(f"new_name must end with '{AUTO}' (got '{name}').")
    count = int(project.GetTimelineCount() or 0)
    if any(project.GetTimelineByIndex(i).GetName() == name for i in range(1, count + 1)):
        raise Refused(f"a timeline named '{name}' already exists; nothing is overwritten.")
    frames = _item_frames(origin)
    sha = plan_sha("duplicate", pin.unique_id, origin_id, name, frames)
    out = {"project": {"name": pin.name, "id": pin.unique_id},
           "origin": {"name": origin_name, "unique_id": origin_id,
                      "items": sum(len(v) for v in frames.values())},
           "new_name": name, "plan_sha": sha, "dry_run": dry_run, "created": None,
           "mismatches": [], "ui_restore_problems": [], "exit_status": 0}
    if dry_run:
        return out
    if expect_sha and expect_sha != sha:
        raise Refused("the plan changed since the dry run (the timeline or its items differ); "
                      "run the dry run again and review it")
    snap = api.UISnapshot(resolve, project)
    with snap:
        project = pin.check(pm)
        dup = origin.DuplicateTimeline(name)
        if not dup:
            raise api.WriteNotApplied(f"DuplicateTimeline('{name}') failed")
        _, dup = api.find_timeline(project, name)
        got = _item_frames(dup)
    out["created"] = {"name": name, "unique_id": dup.GetUniqueId()}
    out["mismatches"] = [f"{k}: origin {frames.get(k)} copy {got.get(k)}"
                         for k in sorted(set(frames) | set(got)) if frames.get(k) != got.get(k)]
    out["ui_restore_problems"] = snap.problems
    out["exit_status"] = 1 if out["mismatches"] else 0
    return out


def _fingerprints(items):
    return [grade.graph_fingerprint(g) if g else None
            for g in (api._safe_call(it, "GetNodeGraph") for it in items)]


def _media_id(item):
    return api._safe_call(api._safe_call(item, "GetMediaPoolItem"), "GetUniqueId")


def _shared_items(project, target_tl_id, media_ids):
    """Every video item, on any timeline but the target, that uses one of
    the target's media: where a grade could leak."""
    found = []
    count = int(project.GetTimelineCount() or 0)
    for i in range(1, count + 1):
        tl = project.GetTimelineByIndex(i)
        if not tl or tl.GetUniqueId() == target_tl_id:
            continue
        for t in range(1, int(tl.GetTrackCount("video") or 0) + 1):
            for n, it in enumerate(tl.GetItemListInTrack("video", t) or [], 1):
                if _media_id(it) in media_ids:
                    found.append((f"{tl.GetName()} V{t} #{n}", it))
    return found


def resolve_lut(path):
    """An existing .cube: absolute, or relative to one of Resolve's LUT
    folders. Returns the absolute path; raises Refused."""
    p = os.path.expanduser(path)
    candidates = [p] if os.path.isabs(p) else [os.path.join(r, p) for r in api.DEFAULT_LUT_ROOTS]
    for c in candidates:
        if os.path.isfile(c):
            return c
    raise Refused(f"LUT '{path}' not found" + ("" if os.path.isabs(p) else
                  " under Resolve's LUT folders (" + ", ".join(api.DEFAULT_LUT_ROOTS) + ")"))


def apply_grade(resolve, project_name, timeline, lut=None, drx=None, track=1, items=None,
                overwrite=False, dry_run=False, project_id=None, check_cancel=None,
                expect_sha=None):
    """Apply a LUT (lut={path, node}) or a .drx still (drx={path, mode,
    manifest}) to items on one video track of an ' [auto]' timeline, read
    each back, and check that no item on any other timeline sharing the
    same media changed. Refuses a remote grade version always, and a graph
    that is not default unless overwrite. Returns {project, timeline, grade,
    targets, results, leaks, plan_sha, dry_run, ui_restore_problems,
    exit_status}."""
    if bool(lut) == bool(drx):
        raise Refused("give exactly one of lut or drx.")
    pm, project, pin = api.pin_for_write(resolve, project_name, project_id)
    _, tl = api.find_timeline(project, timeline)
    tl_name, tl_id = tl.GetName(), tl.GetUniqueId()
    if not tl_name.endswith(AUTO):
        raise Refused(f"'{tl_name}' is not an [auto] timeline. Grades go only onto timelines "
                      "these tools made; duplicate it first (duplicate_timeline_auto).")
    tracks = int(tl.GetTrackCount("video") or 0)
    if not 1 <= track <= tracks:
        raise Refused(f"'{tl_name}' has {tracks} video track(s).")
    all_items = tl.GetItemListInTrack("video", track) or []
    wanted = sorted(set(items)) if items else list(range(1, len(all_items) + 1))
    bad = [n for n in wanted if not 1 <= n <= len(all_items)]
    if bad:
        raise Refused(f"no item(s) {bad} on V{track} ({len(all_items)} item(s)).")
    if lut:
        lut_path = resolve_lut(lut["path"])
        spec = {"kind": "lut", "path": lut_path, "node": int(lut.get("node") or 1)}
    else:
        drx_path = os.path.expanduser(drx["path"])
        if not os.path.isfile(drx_path):
            raise Refused(f".drx '{drx_path}' not found.")
        man_path = os.path.expanduser(drx.get("manifest") or os.path.splitext(drx_path)[0] + ".json")
        if not os.path.isfile(man_path):
            raise Refused(f"no label manifest at {man_path}: a .drx is applied only with its "
                          "expected {num_nodes, labels} beside it, so the result can be read back.")
        with open(man_path, encoding="utf-8") as f:
            manifest = json.load(f)
        spec = {"kind": "drx", "path": drx_path, "mode": int(drx.get("mode") or 0),
                "manifest": manifest}
    targets, refusals = [], []
    for n in wanted:
        it = all_items[n - 1]
        g = grade.read_grade(it)
        fp = g["fingerprint"] if g else None
        why = []
        if g is None:
            why.append("no node graph")
        else:
            if (g.get("version") or {}).get("type") == "remote":
                why.append("its current grade version is remote (shared across timelines)")
            if not grade.is_default_graph(fp):
                why.append(f"its graph is not default ({g['num_nodes']} node(s))")
        if spec["kind"] == "lut" and g and spec["node"] > g["num_nodes"]:
            why.append(f"it has {g['num_nodes']} node(s); the API cannot add node {spec['node']}")
        entry = {"index": n, "name": it.GetName(), "media_id": _media_id(it),
                 "fingerprint": fp}
        # A remote version is shared by every timeline using the clip, so
        # grading it would change timelines these tools did not make: never,
        # whatever overwrite says.
        remote = bool(g) and (g.get("version") or {}).get("type") == "remote"
        hard = (g is None or remote or
                (spec["kind"] == "lut" and g and spec["node"] > g["num_nodes"]))
        if why and (hard or not overwrite):
            refusals.append({**entry, "reasons": why})
        else:
            entry["overwrites"] = why
            targets.append(entry)
    shown = {k: v for k, v in spec.items() if k != "manifest"}
    sha = plan_sha("grade", pin.unique_id, tl_id, track, shown,
                   [(t["index"], t["name"], t["fingerprint"]) for t in targets], overwrite)
    out = {"project": {"name": pin.name, "id": pin.unique_id},
           "timeline": {"name": tl_name, "unique_id": tl_id, "track": track},
           "grade": shown, "targets": [{k: v for k, v in t.items() if k != "fingerprint"}
                                       for t in targets],
           "refused": refusals, "results": [], "leaks": [], "plan_sha": sha,
           "dry_run": dry_run, "ui_restore_problems": [], "exit_status": 0}
    if not targets:
        raise Refused("no item may be graded: " + "; ".join(
            f"#{r['index']} {r['name']}: {', '.join(r['reasons'])}" for r in refusals))
    if dry_run:
        return out
    if expect_sha and expect_sha != sha:
        raise Refused("the plan changed since the dry run (items or their grades differ); "
                      "run the dry run again and review it")
    media = {t["media_id"] for t in targets if t["media_id"]}
    shared = _shared_items(project, tl_id, media)
    before = _fingerprints([it for _, it in shared])
    untouched = [it for n, it in enumerate(all_items, 1) if n not in {t["index"] for t in targets}]
    untouched_before = _fingerprints(untouched)
    snap = api.UISnapshot(resolve, project)
    with snap:
        if spec["kind"] == "lut":
            refresh = getattr(project, "RefreshLUTList", None)
            if refresh:
                refresh()
        for t in targets:
            if check_cancel:
                check_cancel()
            pin.check(pm)
            it = all_items[t["index"] - 1]
            if spec["kind"] == "lut":
                ok, detail = grade.apply_lut_to_item(it, spec["node"], spec["path"],
                                                     api.DEFAULT_LUT_ROOTS)
            else:
                ok, detail = grade.apply_drx_to_item(it, spec["path"], spec["mode"])
                if ok:
                    miss = api.assert_graph_matches(it.GetNodeGraph(), spec["manifest"])
                    if miss:
                        ok, detail = False, "read back against the manifest: " + "; ".join(miss)
            out["results"].append({"index": t["index"], "name": t["name"], "ok": ok,
                                   "detail": detail})
    after = _fingerprints([it for _, it in shared])
    out["leaks"] = [label for (label, _), a, b in zip(shared, before, after) if a != b]
    if _fingerprints(untouched) != untouched_before:
        out["leaks"].append(f"{tl_name}: an item that was not a target changed")
    out["ui_restore_problems"] = snap.problems
    failed = any(not r["ok"] for r in out["results"]) or out["leaks"]
    out["exit_status"] = 1 if failed else 0
    return out


def queue_render(resolve, project_name, timeline, preset_key=None, output_dir=None, presets=None,
                 custom_name=None, dry_run=False, project_id=None, expect_sha=None,
                 destination=None, name_parts=None, config=None, check_cancel=None,
                 volumes_root="/Volumes"):
    """Queue one render job for a timeline from a preset, never start it.
    Makes the timeline current only while queueing, then puts back the
    current timeline, page and the Deliver page's format and codec. The
    other Deliver fields it sets cannot be read back or restored, so every
    result lists them. Refuses when a render is running or the output file
    exists. Returns {project, timeline, preset, output, plan_sha, dry_run,
    job, readback_problems, deliver_changed, warnings, ui_restore_problems,
    exit_status}.

    With destination (a key in config["destinations"]) instead of a preset,
    output_dir is the target folder (default: the destination's
    target_dir) and name_parts name the file; see queue_destination."""
    if destination:
        return queue_destination(resolve, project_name, timeline, destination, output_dir,
                                 name_parts, config=config, dry_run=dry_run,
                                 project_id=project_id, expect_sha=expect_sha,
                                 check_cancel=check_cancel, volumes_root=volumes_root)
    from . import render
    if not presets or not preset_key or not output_dir:
        raise Refused("give a preset and an output folder, or a destination.")
    pm, project, pin = api.pin_for_write(resolve, project_name, project_id)
    _, tl = api.find_timeline(project, timeline)
    preset = presets.get(preset_key)
    if not preset:
        raise Refused(f"unknown preset '{preset_key}'; configured: {', '.join(presets)}")
    if not os.path.isdir(output_dir):
        raise Refused(f"output_dir {output_dir} is not an existing folder.")
    if project.IsRenderingInProgress():
        raise Refused("a render is running; queue after it finishes.")
    base = (custom_name or tl.GetName()) + preset["suffix"]
    target = os.path.join(os.path.realpath(output_dir), f"{base}.{preset['format']}")
    if os.path.exists(target):
        raise Refused(f"{target} already exists; a render would overwrite it. Choose another "
                      "custom_name or folder.")
    sha = plan_sha("render", pin.unique_id, tl.GetUniqueId(), preset_key, preset,
                   os.path.realpath(output_dir), base)
    out = {"project": {"name": pin.name, "id": pin.unique_id},
           "timeline": {"name": tl.GetName(), "unique_id": tl.GetUniqueId()},
           "preset": {"key": preset_key, **preset}, "output": target, "plan_sha": sha,
           "dry_run": dry_run, "job": None, "readback_problems": [],
           "deliver_changed": list(render.DELIVER_FIELDS), "warnings": [],
           "ui_restore_problems": [], "exit_status": 0}
    if dry_run:
        return out
    if expect_sha and expect_sha != sha:
        raise Refused("the plan changed since the dry run; run the dry run again and review it")
    snap = api.UISnapshot(resolve, project)
    with snap:
        project = pin.check(pm)
        fmt0 = project.GetCurrentRenderFormatAndCodec() or {}
        if not project.SetCurrentTimeline(tl):
            raise api.WriteNotApplied(f"could not make '{tl.GetName()}' current to queue it")
        try:
            q = render.queue_render_job(project, preset_key, os.path.realpath(output_dir),
                                        presets, custom_name)
        finally:
            _restore_format(project, fmt0, out["warnings"])
    out["warnings"] = q["warnings"] + out["warnings"]
    out["ui_restore_problems"] = snap.problems
    if q["error"] or not q["job_id"]:
        out["exit_status"] = 1
        out["warnings"].insert(0, q["error"] or "no job id")
        return out
    job, problems = render.render_job_readback(
        project, q["job_id"], {"TargetDir": os.path.realpath(output_dir),
                               "TimelineName": tl.GetName(),
                               "FormatWidth": preset["resolution"]["width"],
                               "FormatHeight": preset["resolution"]["height"]})
    out["job"] = job
    out["readback_problems"] = problems
    out["exit_status"] = 1 if problems else 0
    return out


def _restore_format(project, fmt0, warnings):
    """Put the Deliver page's format and codec back to fmt0."""
    if fmt0.get("format") and fmt0.get("format") != "unknown":
        if not (project.SetCurrentRenderFormatAndCodec(fmt0["format"], fmt0.get("codec"))
                and (project.GetCurrentRenderFormatAndCodec() or {}) == fmt0):
            warnings.append(f"could not put the Deliver format/codec back to {fmt0}")
    else:
        warnings.append("the Deliver page had no format set before, so the queued job's "
                        "format stays selected there")


def _restore_mode(project, mode0, warnings):
    """Put the Deliver page's render mode back to mode0 (0 Individual
    clips, 1 Single clip), when it was read and has changed."""
    if mode0 not in (0, 1):
        warnings.append(f"the Deliver page's render mode read as {mode0!r} before queueing, "
                        "so it stays on Single clip")
    elif project.GetCurrentRenderMode() != mode0:
        if not (project.SetCurrentRenderMode(mode0) and project.GetCurrentRenderMode() == mode0):
            warnings.append(f"could not put the Deliver page's render mode back to {mode0}")


def _subtitle_tracks(project, tl):
    """(items per enabled subtitle track, items per disabled one, whether
    the on/off state could be read). Resolve 21.0.4.5 answers
    GetIsTrackEnabled with False for every track, video included, of a
    timeline that is not current (spike 19), so the state is read only on
    the current timeline. Otherwise every track counts as enabled, and the
    real queue reads it again once it has made the timeline current."""
    cur = api._safe_call(project, "GetCurrentTimeline")
    readable = bool(cur) and api._safe_call(cur, "GetUniqueId") == tl.GetUniqueId()
    on, off = [], []
    for i in range(1, int(api._safe_call(tl, "GetTrackCount", "subtitle") or 0) + 1):
        count = len(tl.GetItemListInTrack("subtitle", i) or [])
        if readable and api._safe_call(tl, "GetIsTrackEnabled", "subtitle", i) is False:
            off.append(count)
        else:
            on.append(count)
    return on, off, readable


def _remove_if_empty(folder):
    """rmdir a folder this run made, only while it is still empty. True
    when it is gone."""
    try:
        os.rmdir(folder)
        return True
    except OSError:
        return False


def _timeline_size(project, tl):
    """(width, height) of a timeline, falling back to the project's."""
    def read(key):
        for obj in (tl, project):
            v = api._safe_call(obj, "GetSetting", key)
            try:
                if v not in (None, "") and int(float(v)) > 0:
                    return int(float(v))
            except (TypeError, ValueError):
                pass
        return None
    return read("timelineResolutionWidth"), read("timelineResolutionHeight")


def queue_destination(resolve, project_name, timeline, key, target_dir, name_parts, config=None,
                      dry_run=False, project_id=None, expect_sha=None, check_cancel=None,
                      volumes_root="/Volumes"):
    """Queue one job that renders a timeline for a delivery destination
    (rpresolve.deliver), named from name_parts into target_dir; never start
    it. Refused, with every reason, when: the destination or name parts are
    invalid; a render is running; the destination has a fixed size and the
    timeline another shape (or a size that cannot be read), since Resolve
    would scale the picture into the frame with bars; the target folder is
    missing, under /Volumes without its share mounted, or inside a git
    working tree; the file or its sidecar already exists, or a queued job
    already writes it; captions are wanted and the timeline has no subtitle
    track (or only empty ones). A required Deliver setting that Resolve
    refuses stops the job from being queued. The queued job is read back
    from GetRenderJobList. Returns {project, timeline, destination, output,
    sidecar, settings, plan_sha, dry_run, job, readback_problems,
    unverified, deliver_changed, carried_over, codec, warnings,
    ui_restore_problems, exit_status}. carried_over names the render
    settings the job takes from whatever the Deliver page last held."""
    from . import deliver, render
    from .config import load_config
    config = config or load_config()
    try:
        dest = deliver.destination(config, key)
        filename = deliver.name_for(dest, name_parts)
    except deliver.DeliverError as e:
        raise Refused(str(e))
    target_dir = target_dir or dest.get("target_dir")
    if not target_dir:
        raise Refused(f"no target folder: give one, or set target_dir for '{key}' in a config "
                      "overlay kept outside this repository.")
    target_dir = os.path.expanduser(str(target_dir))
    pm, project, pin = api.pin_for_write(resolve, project_name, project_id)
    _, tl = api.find_timeline(project, timeline)
    if project.IsRenderingInProgress():
        raise Refused("a render is running; queue after it finishes.")
    subtitle_counts, subtitle_off, track_state_read = _subtitle_tracks(project, tl)
    fps_raw = (api._safe_call(tl, "GetSetting", "timelineFrameRate") or
               api._safe_call(project, "GetSetting", "timelineFrameRate"))
    fps = deliver.fps_number(fps_raw)
    size = _timeline_size(project, tl)
    problems = (deliver.shape_problems(dest, size, f"timeline '{tl.GetName()}'") +
                deliver.timeline_problems(dest, subtitle_counts, subtitle_off) +
                deliver.output_problems(target_dir, filename, dest,
                                        queued=render.queued_outputs(project),
                                        volumes_root=volumes_root,
                                        subfolder=dest.get("subfolder")))
    if problems:
        raise Refused(" ".join(problems))
    try:
        steps = deliver.render_steps(dest, deliver.output_folder(target_dir, dest), filename,
                                     size=size, fps=fps)
    except deliver.DeliverError as e:
        raise Refused(str(e))
    # The destination's own subfolder of the target folder (deliver.output_folder).
    real_dir = deliver.output_folder(target_dir, dest)
    output = os.path.join(real_dir, filename)
    core = steps[0]["settings"]
    sha = plan_sha("deliver", pin.unique_id, tl.GetUniqueId(), key, dest, real_dir, filename,
                   steps)
    out = {"project": {"name": pin.name, "id": pin.unique_id},
           "timeline": {"name": tl.GetName(), "unique_id": tl.GetUniqueId(), "fps": fps_raw,
                        "size": list(size), "subtitle_tracks": subtitle_counts,
                        "subtitle_tracks_disabled": subtitle_off},
           "destination": {k: dest.get(k) for k in ("key", "name", "format", "codec",
                                                    "resolution", "audio", "loudness",
                                                    "captions", "data_burn_in", "color")},
           "output": output,
           "sidecar": deliver.sidecar_path(output) if dest["captions"] == "sidecar" else None,
           "settings": steps, "plan_sha": sha, "dry_run": dry_run, "job": None,
           "readback_problems": [], "unverified": [],
           "deliver_changed": (["format/codec (put back)", "render mode Single clip (put back)"]
                               + deliver.changed_fields(steps)),
           "carried_over": deliver.carried_over(steps),
           "codec": None, "warnings": [], "ui_restore_problems": [], "exit_status": 0,
           "made_folder": None}
    unnamed = render.unreported_jobs(project)
    if unnamed:
        out["warnings"].append(f"{len(unnamed)} job(s) already in the render queue do not report "
                               "their output file (TargetDir and OutputFilename), so a clash with "
                               "them could not be checked: " + ", ".join(map(str, unnamed)))
    if dry_run and dest["captions"] in ("sidecar", "burnin") and not track_state_read:
        out["warnings"].append("whether the subtitle tracks are switched on is checked when the "
                               "job is queued: Resolve reports it only for the current timeline")
    if fps is None:
        out["warnings"].append(f"the timeline's frame rate ({fps_raw!r}) could not be read, so "
                               "FrameRate is not set; check fps with deliver-check.")
    if dry_run:
        return out
    if expect_sha and expect_sha != sha:
        raise Refused("the plan changed since the dry run; run the dry run again and review it")
    restore_warnings = []
    snap = api.UISnapshot(resolve, project)
    with snap:
        if check_cancel:
            check_cancel()
        project = pin.check(pm)
        fmt0 = project.GetCurrentRenderFormatAndCodec() or {}
        mode0 = project.GetCurrentRenderMode()
        if not project.SetCurrentTimeline(tl):
            raise api.WriteNotApplied(f"could not make '{tl.GetName()}' current to queue it")
        # Now current, so the subtitle tracks' on/off state reads true (_subtitle_tracks).
        late = deliver.timeline_problems(dest, *_subtitle_tracks(project, tl)[:2])
        if late:
            raise Refused(" ".join(late) + " Nothing was queued.")
        made = not os.path.isdir(real_dir)
        if made:
            try:
                os.mkdir(real_dir)
            except OSError as e:
                raise api.WriteNotApplied(f"could not make the destination folder "
                                          f"{real_dir}: {e}")
        try:
            q = render.queue_destination_job(project, dest, steps)
        except BaseException:
            if made:
                _remove_if_empty(real_dir)
            raise
        finally:
            _restore_format(project, fmt0, restore_warnings)
            _restore_mode(project, mode0, restore_warnings)
    out["codec"] = q["codec"]
    out["warnings"] += q["warnings"] + restore_warnings
    out["ui_restore_problems"] = snap.problems
    out["made_folder"] = real_dir if made else None
    if q["error"] or not q["job_id"]:
        out["exit_status"] = 1
        out["warnings"].insert(0, q["error"] or "no job id")
        if made and _remove_if_empty(real_dir):  # nothing was queued into it
            out["made_folder"] = None
        return out
    job = next((j for j in project.GetRenderJobList() or [] if j.get("JobId") == q["job_id"]),
               None)
    if job is None:
        out["exit_status"] = 1
        out["readback_problems"] = [f"job {q['job_id']} is not in the render queue"]
        return out
    out["job"] = dict(job)
    exact = {"TargetDir": real_dir, "OutputFilename": filename, "TimelineName": tl.GetName(),
             "FormatWidth": core["FormatWidth"], "FormatHeight": core["FormatHeight"],
             "AudioSampleRate": int(dest["audio"]["sample_rate"])}
    if dest["audio"].get("bit_depth"):
        exact["AudioBitDepth"] = int(dest["audio"]["bit_depth"])
    if fps:
        exact["FrameRate"] = fps
    # The job list names the container its own way: 'QuickTime' for mov.
    loose = {"VideoFormat": [dest["format"], {"mov": "QuickTime", "mp4": "MP4"}[dest["format"]]],
             "VideoCodec": [q["codec"]["used"], q["codec"]["description"]],
             "AudioCodec": [dest["audio"].get("resolve_codec") or dest["audio"]["codec"]]}
    problems, warnings, unverified = render.readback_report(job, exact, loose)
    unverified += [k for k in deliver.changed_fields(steps)
                   if k not in exact and k not in loose and job.get(k) is None]
    out["readback_problems"] = problems
    out["warnings"] += warnings
    out["unverified"] = unverified
    out["exit_status"] = 1 if problems else 0
    return out


# ---------------------------------------------------------------------------
# create_captions: Resolve's auto captions on an [auto] timeline
# ---------------------------------------------------------------------------

# Language codes to Resolve's AUTO_CAPTION_* language constants (the "Auto
# Caption Settings" section of the scripting README, 21.0.4.5).
CAPTION_LANGUAGES = {
    "auto": "AUTO_CAPTION_AUTO", "da": "AUTO_CAPTION_DANISH", "nl": "AUTO_CAPTION_DUTCH",
    "en": "AUTO_CAPTION_ENGLISH", "fr": "AUTO_CAPTION_FRENCH", "de": "AUTO_CAPTION_GERMAN",
    "it": "AUTO_CAPTION_ITALIAN", "ja": "AUTO_CAPTION_JAPANESE", "ko": "AUTO_CAPTION_KOREAN",
    "zh-hans": "AUTO_CAPTION_MANDARIN_SIMPLIFIED",
    "zh-hant": "AUTO_CAPTION_MANDARIN_TRADITIONAL", "no": "AUTO_CAPTION_NORWEGIAN",
    "pt": "AUTO_CAPTION_PORTUGUESE", "ru": "AUTO_CAPTION_RUSSIAN", "es": "AUTO_CAPTION_SPANISH",
    "sv": "AUTO_CAPTION_SWEDISH"}
CAPTION_LINE_BREAKS = {"single": "AUTO_CAPTION_LINE_SINGLE",
                       "double": "AUTO_CAPTION_LINE_DOUBLE"}
# After CreateSubtitlesFromAudio returns True with no items yet, how long to
# look again before calling it a failure (the live call returned only once
# the items were there; this covers a Resolve that returns early).
CAPTION_SETTLE_S = 3.0
# The page CreateSubtitlesFromAudio is called from. Live, 2026-09-29: from the
# Deliver page it returns False and makes nothing (0.2 s to 11 s); from the
# Edit page it returns True with the items made (22 in 36 s). Other pages are
# untried.
CAPTION_PAGE = "edit"


def _subtitle_items(tl):
    """(items per subtitle track in track order, {track, start, end} of the
    first item on the first track that has one, or None)."""
    counts, first = [], None
    for i in range(1, int(api._safe_call(tl, "GetTrackCount", "subtitle") or 0) + 1):
        items = tl.GetItemListInTrack("subtitle", i) or []
        counts.append(len(items))
        if items and first is None:
            first = {"track": i, "start": api._safe_call(items[0], "GetStart"),
                     "end": api._safe_call(items[0], "GetEnd")}
    return counts, first


def _constant(resolve, name):
    """resolve.<name>, or None when this Resolve does not define it: an
    unknown constant reads as None, silently. The known ones are floats,
    and some are 0.0, so only None counts as missing."""
    return getattr(resolve, name, None)


def create_captions(resolve, project_name, timeline, language="en", dry_run=False,
                    project_id=None, expect_sha=None, config=None, check_cancel=None):
    """Transcribe an ' [auto]' timeline's audio into captions with Resolve's
    Timeline.CreateSubtitlesFromAudio, in the open project named
    project_name. Characters per line and line breaks come from the
    config's deliver.captions for the timeline's shape (landscape, portrait
    or square, from its resolution). Additive only: refused when the
    timeline already has any subtitle item, while a render runs, or when
    Resolve lacks a constant the settings need. The timeline is made
    current and the Edit page opened for the call (Resolve transcribes the
    current timeline, and returns False from the Deliver page), and the
    current timeline and page are put back after. The call returning
    True proves nothing, so the subtitle tracks are read back: at least one
    item must be there. Returns {project, timeline, language, shape,
    settings, subtitle_tracks_before, subtitle_tracks_after, items,
    first_item, returned, plan_sha, dry_run, ui_restore_problems,
    exit_status}."""
    from . import deliver
    from .config import load_config
    config = config or load_config()
    code = str(language or "en").strip().lower()
    if code not in CAPTION_LANGUAGES:
        raise Refused(f"unknown caption language '{language}'; use one of "
                      f"{', '.join(sorted(CAPTION_LANGUAGES))}.")
    pm, project, pin = api.pin_for_write(resolve, project_name, project_id)
    _, tl = api.find_timeline(project, timeline)
    tl_name, tl_id = tl.GetName(), tl.GetUniqueId()
    if not tl_name.endswith(AUTO):
        raise Refused(f"'{tl_name}' is not an [auto] timeline. Captions go only onto timelines "
                      "these tools made; duplicate it first (duplicate_timeline_auto).")
    if project.IsRenderingInProgress():
        raise Refused("a render is running; make captions after it finishes.")
    before = _subtitle_items(tl)[0]
    if any(before):
        raise Refused(f"'{tl_name}' already has {sum(before)} subtitle item(s) on "
                      f"{sum(1 for n in before if n)} track(s); captions are only added to a "
                      "timeline that has none (additive only). Duplicate the timeline without "
                      "its captions, or remove them by hand.")
    size = _timeline_size(project, tl)
    shape = deliver.frame_shape(size)
    if shape is None:
        raise Refused(f"the resolution of '{tl_name}' could not be read ({size[0]}x{size[1]}), "
                      "so its frame shape, and with it the caption line length, is unknown.")
    try:
        chosen = deliver.caption_settings(config, shape)
    except deliver.DeliverError as e:
        raise Refused(f"'{tl_name}' is {size[0]}x{size[1]}, {shape}: {e}")
    names = {"SUBTITLE_LANGUAGE": CAPTION_LANGUAGES[code],
             "SUBTITLE_CHARS_PER_LINE": chosen["chars_per_line"],
             "SUBTITLE_LINE_BREAK": CAPTION_LINE_BREAKS[chosen["line_break"]]}
    wanted = ["SUBTITLE_LANGUAGE", "SUBTITLE_CHARS_PER_LINE", "SUBTITLE_LINE_BREAK",
              names["SUBTITLE_LANGUAGE"], names["SUBTITLE_LINE_BREAK"]]
    consts = {n: _constant(resolve, n) for n in wanted}
    missing = [n for n in wanted if consts[n] is None]
    if missing:
        raise Refused("this Resolve does not define " + ", ".join(f"resolve.{n}" for n in missing)
                      + " (an unknown constant reads as None), so the caption settings cannot "
                      "be given; check the scripting README's Auto Caption Settings.")
    settings = {consts["SUBTITLE_LANGUAGE"]: consts[names["SUBTITLE_LANGUAGE"]],
                consts["SUBTITLE_CHARS_PER_LINE"]: chosen["chars_per_line"],
                consts["SUBTITLE_LINE_BREAK"]: consts[names["SUBTITLE_LINE_BREAK"]]}
    sha = plan_sha("captions", pin.unique_id, tl_id, code, names, shape, list(size), before)
    out = {"project": {"name": pin.name, "id": pin.unique_id},
           "timeline": {"name": tl_name, "unique_id": tl_id, "size": list(size)},
           "language": code, "shape": shape, "settings": names,
           "subtitle_tracks_before": before, "subtitle_tracks_after": None, "items": 0,
           "first_item": None, "returned": None, "plan_sha": sha, "dry_run": dry_run,
           "ui_restore_problems": [], "exit_status": 0}
    if dry_run:
        return out
    if expect_sha and expect_sha != sha:
        raise Refused("the plan changed since the dry run (the timeline, its captions or the "
                      "caption settings differ); run the dry run again and review it")
    snap = api.UISnapshot(resolve, project)
    with snap:
        if check_cancel:
            check_cancel()
        project = pin.check(pm)
        if project.IsRenderingInProgress():
            raise Refused("a render started; nothing was transcribed.")
        if not project.SetCurrentTimeline(tl) or api._safe_call(
                api._safe_call(project, "GetCurrentTimeline"), "GetUniqueId") != tl_id:
            raise api.WriteNotApplied(f"could not make '{tl_name}' current to transcribe it")
        if api._safe_call(resolve, "GetCurrentPage") != CAPTION_PAGE and not (
                resolve.OpenPage(CAPTION_PAGE) and resolve.GetCurrentPage() == CAPTION_PAGE):
            raise api.WriteNotApplied(f"could not open the {CAPTION_PAGE} page to transcribe "
                                      f"'{tl_name}' (from the Deliver page Resolve returns False)")
        if any(_subtitle_items(tl)[0]):
            raise Refused(f"'{tl_name}' gained subtitle items since the plan; nothing was "
                          "transcribed.")
        out["returned"] = bool(tl.CreateSubtitlesFromAudio(settings))
        after, first = _subtitle_items(tl)
        deadline = time.monotonic() + (CAPTION_SETTLE_S if out["returned"] else 0)
        while not any(after) and time.monotonic() < deadline:
            time.sleep(0.5)
            after, first = _subtitle_items(tl)
        out["first_item"] = first
    out["subtitle_tracks_after"] = after
    out["items"] = sum(after)
    out["ui_restore_problems"] = snap.problems
    out["exit_status"] = 0 if out["items"] > 0 else 1
    return out


# ---------------------------------------------------------------------------
# sync: dual-system sound as a stacked multitrack [auto] timeline
# ---------------------------------------------------------------------------

SYNC_KEYS = ("coarse", "overlap", "windows", "offset_s", "head_offset_s", "tail_offset_s", "drift",
             "drift_note", "frames", "polarity", "match", "reasons", "groups", "rival",
             "notes", "thresholds")


def _file_key(path):
    st = os.stat(path)
    return [path, st.st_size, int(st.st_mtime)]


def sync(resolve, project_name, reference, other, name=None, bin=None, autosync=False,
         window_s=None, dry_run=False, project_id=None, expect_sha=None, check_cancel=None,
         measure=None, probe=None):
    """Measure where `other` sits against `reference` from their audio
    (rpresolve.sync) and build a stacked multitrack ' [auto]' timeline in
    the open project named project_name: the reference on V1/A1, the other
    on A2 (or V2/A2 for a second camera) at the measured offset, every
    placement read back: its start and source start exactly as planned,
    its length within a frame (rate conversion rounds).

    reference and other name media-pool clips (unique id, file path or
    clip name). With bin they are files instead, imported into a new bin
    of that name at the pool's root, which the run then owns; only then
    may autosync run MediaPool.AutoSyncAudio on them, and its result is
    read back through a second ' [auto]' timeline and compared with the
    measured offset. A measurement that is not a match is refused; drift
    over the threshold is reported (exit_status 2), never corrected.
    measure and probe default to rpresolve.sync's (numpy); tests pass
    their own.
    Returns {project, mode, reference, other, measurement, timeline,
    would_create, bin, rate, offset_frames, plan, plan_sha, dry_run,
    imported, built, autosync, problems, ui_restore_problems,
    exit_status}."""
    from . import deliver, syncbuild as sb
    if measure is None or (bin and probe is None):
        from . import sync as rpsync  # numpy
        measure, probe = measure or rpsync.measure, probe or rpsync.probe
    pm, project, pin = api.pin_for_write(resolve, project_name, project_id)
    media_pool, root = api.media_pool_root(project)
    importing = bool(bin)
    if autosync and not importing:
        raise Refused("AutoSyncAudio links the audio into the clips it syncs, which changes "
                      "media-pool items; it runs only on clips this run imports (give bin).")
    if importing:
        paths = []
        for p in (reference, other):
            full = os.path.expanduser(p)
            if not os.path.isabs(full) or not os.path.isfile(full):
                raise Refused(f"with bin, reference and other are absolute paths of files; "
                              f"{p} is not one.")
            paths.append(os.path.realpath(full))
        if paths[0] == paths[1]:
            raise Refused("reference and other are the same file.")
        if any(f.GetName() == bin for f in root.GetSubFolderList() or []):
            raise Refused(f"a bin named '{bin}' already exists at the media pool's root; the run "
                          "imports into a bin it makes and owns. Choose another name.")
        pooled = {os.path.realpath(c.GetClipProperty("File Path"))
                  for _, c in sb.walk(root) if c.GetClipProperty("File Path")}
        already = [p for p in paths if p in pooled]
        if already:
            raise Refused(f"{', '.join(already)} is already in the media pool; name the pool "
                          "clip instead of importing it again (and without autosync).")
        probes = [probe(p) for p in paths]
        if not probes[0]["video"] or not probes[0]["video"]["fps"]:
            raise Refused(f"the reference {paths[0]} has no video frame rate; the reference is "
                          "the camera clip, whose rate the timeline takes.")
        fps = probes[0]["video"]["fps"]
        infos = []
        for p, pr in zip(paths, probes):
            f = pr["video"]["fps"] if pr["video"] and pr["video"]["fps"] else fps
            infos.append({"name": os.path.basename(p), "uid": None, "path": p, "fps": f,
                          "frames": int(round(pr["duration"] * f)), "video": bool(pr["video"]),
                          "channels": pr["audio"]["channels"]})
        clips = {}
    else:
        clips, infos = {}, []
        for role, ref in (("reference", reference), ("other", other)):
            clip, why = sb.find_clip(root, ref)
            if clip is None:
                raise Refused(f"{role}: {why}.")
            clips[role] = clip
            infos.append(sb.clip_info(clip))
        if infos[0]["uid"] == infos[1]["uid"]:
            raise Refused("reference and other are the same media-pool clip.")
        for role, info in zip(("reference", "other"), infos):
            if not info["path"] or not os.path.isfile(info["path"]):
                raise Refused(f"the {role} clip's file {info['path'] or '(none)'} is not on disk "
                              "(offline media); its audio cannot be measured.")
            if not info["fps"] or not info["frames"]:
                raise Refused(f"Resolve does not report the {role} clip's FPS and Frames.")
        if not infos[0]["video"]:
            raise Refused("the reference clip has no video; the reference is the camera clip, "
                          "whose rate the timeline takes.")
        paths = [infos[0]["path"], infos[1]["path"]]
        fps = infos[0]["fps"]
    rate = sb.rate_string(fps)
    if rate is None:
        raise Refused(f"the reference runs at {fps:g} fps, which is not a timeline frame rate "
                      "Resolve offers.")
    tl_fps = deliver.exact_fps(rate)  # frames count at 30000/1001; the string is for SetSetting
    stem = os.path.splitext(infos[0]["name"])[0]
    name = name or f"{stem} sync{AUTO}"
    if not name.endswith(AUTO):
        raise Refused(f"the timeline name must end with '{AUTO}' (got '{name}').")
    vname = name[:-len(AUTO)] + f" (AutoSyncAudio){AUTO}"
    existing = {project.GetTimelineByIndex(i).GetName()
                for i in range(1, int(project.GetTimelineCount() or 0) + 1)}
    clash = [n for n in ([name, vname] if autosync else [name]) if n in existing]
    if clash:
        raise Refused(f"a timeline named '{clash[0]}' already exists; nothing is overwritten.")
    settings = None
    if autosync:
        settings, missing = sb.autosync_settings(resolve)
        if missing:
            raise Refused("this Resolve does not define " + ", ".join(
                f"resolve.{n}" for n in missing) + " (an unknown constant reads as None), so "
                "AutoSyncAudio's settings cannot be given.")
    kw = {"fps": tl_fps, "check_cancel": check_cancel}
    if window_s:
        kw["window_s"] = window_s
    try:
        report = measure(paths[0], paths[1], **kw)
    except sb.SyncError as e:
        raise Refused(f"the audio could not be measured: {e}")
    measurement = {k: report.get(k) for k in SYNC_KEYS if k in report}
    if not report["match"]:
        groups = ("; the windows agree in groups: " + "; ".join(
            f"{g['offset_s']:+.4f} s ({', '.join(g['windows'])})" for g in report["groups"])
            if report.get("groups") else "")
        raise Refused("the two recordings do not match well enough to place: " +
                      "; ".join(report["reasons"]) + groups + ". Nothing was built.")
    offset_frames = report["frames"]["placed"]
    the_plan = sb.plan(infos[0], infos[1], offset_frames, tl_fps)
    drift = report.get("drift") or {}
    sha = plan_sha("sync", pin.unique_id, "import" if importing else "pool",
                   [_file_key(p) for p in paths], bin, name, bool(autosync), rate,
                   round(report["offset_s"], 4), offset_frames, bool(drift.get("exceeds")),
                   the_plan["placements"] if not importing else
                   [{k: v for k, v in p.items() if k != "length"} for p in the_plan["placements"]])
    out = {"project": {"name": pin.name, "id": pin.unique_id},
           "mode": "import" if importing else "pool",
           "reference": {k: infos[0][k] for k in ("name", "uid", "path", "fps", "frames")},
           "other": {k: infos[1][k] for k in ("name", "uid", "path", "fps", "frames")},
           "measurement": measurement, "timeline": name,
           "would_create": [name] + ([vname] if autosync else []), "bin": bin, "rate": rate,
           "offset_frames": offset_frames, "plan": the_plan["placements"], "plan_sha": sha,
           "dry_run": dry_run, "imported": [], "built": None, "autosync": None, "problems": [],
           "ui_restore_problems": [], "exit_status": 0}
    if importing:
        out["lengths_note"] = ("lengths are from ffprobe until the files are imported; the real "
                               "run places them at the lengths Resolve reports")
    if dry_run:
        return out
    if expect_sha and expect_sha != sha:
        raise Refused("the plan changed since the dry run (files, clips, timelines or the "
                      "measured offset differ); run the dry run again and review it")
    check = _check(pin, pm, check_cancel)
    snap = api.UISnapshot(resolve, project)
    previous = media_pool.GetCurrentFolder() if importing else None
    with snap:
        try:
            check()
            if importing:
                # The new timelines go into the run's bin too (CreateEmptyTimeline
                # adds to the current folder); the folder is put back after.
                clips, infos = _import_pair(media_pool, root, bin, paths, rate, out)
                build_plan = sb.plan(infos[0], infos[1], offset_frames, tl_fps)
            else:
                build_plan = the_plan
            clips.update({"reference_info": infos[0], "other_info": infos[1]})
            built = sb.build(project, media_pool, name, rate, tl_fps, clips, build_plan,
                             check)
            tl = built.pop("timeline")
            out["built"] = built
            out["problems"] += built["problems"]
            if autosync:
                out["autosync"] = _autosync(resolve, project, media_pool, clips, infos, tl,
                                            built, build_plan, vname, rate, report["offset_s"],
                                            settings, check)
                out["problems"] += out["autosync"]["problems"]
        finally:
            if previous:
                media_pool.SetCurrentFolder(previous)
    out["ui_restore_problems"] = snap.problems
    out["exit_status"] = (1 if out["problems"] else
                          2 if drift.get("exceeds") or (out["autosync"] or {}).get("verdict")
                          == "unverifiable" else 0)
    return out


def _uid(obj):
    return api._safe_call(obj, "GetUniqueId")


def _import_pair(media_pool, root, bin, paths, rate, out):
    """Make the run's bin at the pool's root, open it, import the two files
    there and read back what Resolve made of them. Leaves the bin open (the
    caller puts the folder back). Returns ({reference, other} clips,
    [reference info, other info])."""
    from . import syncbuild as sb
    folder = media_pool.AddSubFolder(root, bin)
    if not folder or not any(f.GetName() == bin for f in root.GetSubFolderList() or []):
        raise api.WriteNotApplied(f"could not make the bin '{bin}'")
    if not media_pool.SetCurrentFolder(folder):
        raise api.WriteNotApplied(f"could not open the bin '{bin}' to import into it")
    got = media_pool.ImportMedia(paths) or []
    by_path = {os.path.realpath(c.GetClipProperty("File Path") or ""): c for c in got if c}
    out["imported"] = [{"path": p, "uid": _uid(by_path.get(p))} for p in paths]
    if any(p not in by_path for p in paths):
        raise api.WriteNotApplied("ImportMedia did not return a clip for " + ", ".join(
            p for p in paths if p not in by_path) + f"; what it did import is in the bin "
            f"'{bin}'. Nothing was built.")
    clips = {"reference": by_path[paths[0]], "other": by_path[paths[1]]}
    infos = [sb.clip_info(clips["reference"]), sb.clip_info(clips["other"])]
    if not infos[0]["fps"] or sb.rate_string(infos[0]["fps"]) != rate or \
            not infos[0]["frames"] or not infos[1]["frames"] or not infos[1]["fps"]:
        raise api.WriteNotApplied(
            f"Resolve reports the imported clips as {infos[0]['fps']} fps / "
            f"{infos[0]['frames']} frames and {infos[1]['fps']} fps / {infos[1]['frames']} "
            f"frames, which does not fit the plan at {rate} fps; they stay in the bin "
            f"'{bin}'. Nothing was built.")
    return clips, infos


def _autosync(resolve, project, media_pool, clips, infos, tl, built, build_plan, vname, rate,
              offset_s, settings, check):
    """Run AutoSyncAudio on the two clips this run imported, then check it:
    the stacked timeline read again, the reference clip's properties before
    and after, and a second [auto] timeline holding the synced reference,
    whose items say where Resolve put the other file. Returns {returned,
    changed_properties, verification_timeline, implied_offset_s, verdict,
    problems}."""
    from . import deliver, syncbuild as sb
    fps = deliver.exact_fps(rate)
    res = {"returned": None, "changed_properties": [], "verification_timeline": vname,
           "implied_offset_s": None, "verdict": None, "problems": []}
    check()
    before = sb.clip_props(clips["reference"])
    res["returned"] = bool(media_pool.AutoSyncAudio([clips["reference"], clips["other"]],
                                                    settings))
    after = sb.clip_props(clips["reference"])
    res["changed_properties"] = sorted(k for k in set(before) | set(after)
                                       if before.get(k) != after.get(k))
    rows, _ = sb.read_back(tl, build_plan["placements"],
                           {"reference": infos[0], "other": infos[1]}, built["start_frame"])
    was = [(p["role"], p["kind"], p["track"], p["got"]) for p in built["placements"]]
    now = [(p["role"], p["kind"], p["track"], p["got"]) for p in rows]
    if was != now:
        res["problems"].append("AutoSyncAudio changed the stacked timeline: " + "; ".join(
            f"{a[0]} {a[1][0].upper()}{a[2]} {a[3]} -> {b[3]}" for a, b in zip(was, now)
            if a != b))
    check()
    vt = sb.new_timeline(project, media_pool, vname, rate)
    problem = sb.ensure_track(vt, "audio", 2, sb.audio_subtype(infos[1]["channels"]))
    if problem:
        res["problems"].append(problem)
    sb.append(media_pool, clips["reference"], {"startFrame": 0, "endFrame": infos[0]["frames"],
                                               "trackIndex": 1, "record": 0, "mediaType": None},
              vt.GetStartFrame())
    implied = sb.implied_offset(vt, infos[0], infos[1], fps)
    if implied is None:
        res["verdict"] = "unverifiable"
        res["note"] = ("the synced reference shows no item of the other file on its own, so "
                       "where Resolve put it cannot be read back; the stacked timeline stands "
                       "on the measured offset alone")
    else:
        res["implied_offset_s"] = round(implied, 4)
        res["verdict"] = "agrees" if abs(implied - offset_s) <= 1.0 / fps + 1e-9 else "disagrees"
        if res["verdict"] == "disagrees":
            res["problems"].append(f"AutoSyncAudio put the other file at {implied:+.4f} s, the "
                                   f"measured offset is {offset_s:+.4f} s: more than a frame "
                                   "apart")
    if not res["returned"]:
        res["problems"].append("AutoSyncAudio returned False")
    return res


# ---------------------------------------------------------------------------
# trim-review markers: review rows as markers on an [auto] timeline
# ---------------------------------------------------------------------------

def trim_review_markers(resolve, project_name, timeline, source, words=None, audio=True,
                        review_opts=None, dry_run=False, project_id=None, expect_sha=None,
                        check_cancel=None):
    """Review the parts of `source` an ' [auto]' timeline plays
    (rpresolve.trimreview: silences, fillers, repeats) and add one marker
    per row there, reading each back with GetMarkers. Adds markers only:
    nothing is cut, rippled, moved or deleted. A row that would land on a
    frame already holding a marker is refused and reported. words: the
    list from cutlist.load_words, or None for silences only. Returns
    {project, timeline, source, items, skipped_items, rows, counts,
    thresholds, planned, refused, results, plan_sha, dry_run,
    ui_restore_problems, exit_status}."""
    from . import deliver, markers as mk, trimreview as tr
    pm, project, pin = api.pin_for_write(resolve, project_name, project_id)
    _, tl = api.find_timeline(project, timeline)
    tl_name, tl_id = tl.GetName(), tl.GetUniqueId()
    if not tl_name.endswith(AUTO):
        raise Refused(f"'{tl_name}' is not an [auto] timeline. Markers go only onto timelines "
                      "these tools made; duplicate it first (duplicate_timeline_auto).")
    tl_fps = deliver.exact_fps(api._safe_call(tl, "GetSetting", "timelineFrameRate") or
                               api._safe_call(project, "GetSetting", "timelineFrameRate"))
    if not tl_fps:
        raise Refused(f"the frame rate of '{tl_name}' could not be read.")
    found, skipped = mk.source_items(tl, source)
    items, slow = mk.mappable(found, tl_fps)
    skipped += slow
    if not items:
        raise Refused(f"no item on '{tl_name}' plays {source} at its own speed" +
                      (": " + "; ".join(skipped) if skipped else "") + ".")
    if check_cancel:
        check_cancel()
    review = tr.review_source(source, words, ranges=mk.ranges(items), audio=audio,
                              **(review_opts or {}))
    existing = mk.existing_frames(tl)
    planned, refused = mk.plan(review["rows"], items, tl.GetStartFrame(), tl_fps, set(existing))
    shown = [{k: v for k, v in m.items() if k != "row"} for m in planned]
    sha = plan_sha("trim-markers", pin.unique_id, tl_id, sorted(existing), shown)
    out = {"project": {"name": pin.name, "id": pin.unique_id},
           "timeline": {"name": tl_name, "unique_id": tl_id, "fps": tl_fps,
                        "markers_before": len(existing)},
           "source": source,
           "items": [{k: it[k] for k in ("kind", "track", "start", "end", "src_start_s",
                                         "src_end_s")} for it in items],
           "skipped_items": skipped, "rows": len(review["rows"]),
           "counts": tr.counts(review["rows"]), "thresholds": review["thresholds"],
           "planned": shown,
           "refused": [{"frame": r["frame"], "reason": r["reason"], "kind": r["row"]["kind"],
                        "source_start": r["row"]["source_start"]} for r in refused],
           "results": [], "problems": [], "plan_sha": sha, "dry_run": dry_run,
           "ui_restore_problems": [], "exit_status": 0}
    if dry_run:
        return out
    if expect_sha and expect_sha != sha:
        raise Refused("the plan changed since the dry run (the timeline, its markers or the "
                      "review differ); run the dry run again and review it")
    problems = []
    snap = api.UISnapshot(resolve, project)
    with snap:
        if check_cancel:
            check_cancel()
        project = pin.check(pm)
        if not project.SetCurrentTimeline(tl) or _uid(project.GetCurrentTimeline()) != tl_id:
            raise api.WriteNotApplied(f"could not make '{tl_name}' current to mark it")
        before = mk.existing_frames(tl)
        if before != existing:
            raise Refused(f"the markers on '{tl_name}' changed since the plan; nothing was added.")
        out["results"] = mk.add(tl, planned, check=_check(pin, pm, check_cancel))
        after = mk.existing_frames(tl)
    changed = sorted(f for f in before if after.get(f) != before[f])
    extra = sorted(set(after) - set(before) - {m["frame"] for m in planned})
    if changed:
        problems.append(f"{len(changed)} marker(s) that were there before changed: frames "
                        f"{changed[:10]}")
    if extra:
        problems.append(f"{len(extra)} marker(s) appeared that were not planned: frames "
                        f"{extra[:10]}")
    out["problems"] = problems
    out["ui_restore_problems"] = snap.problems
    out["exit_status"] = 1 if problems or any(not r["ok"] for r in out["results"]) else 0
    return out
