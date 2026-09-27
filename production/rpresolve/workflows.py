"""
rpresolve.workflows: the orchestration behind the CLI's detect, ingest,
survey and cut, as functions that return a dict and raise typed errors.
The CLI and the MCP server call these, so both enforce the same gates.

Nothing here prints or exits the interpreter. Preconditions that fail raise
Refused (a ResolveAPIError); Resolve problems raise api's own errors;
detect.ToolMissing and cutlist.CutlistError pass through.

Write paths (ingest, cut) pin the open project by name (and unique id when
given), re-check the pin before every batch of writes, and take a
`check_cancel` callable that raises to stop at the next safe point. A dry
run returns a plan_sha; a real run given that sha refuses when the plan it
would carry out differs from the one the dry run showed.
"""

import hashlib
import json
import os
import subprocess
from pathlib import Path

from . import api
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
