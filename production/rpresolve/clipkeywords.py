"""
rpresolve.clipkeywords: write theme keywords and a description onto media pool
clips from a TSV plan, so footage can be filtered by theme inside Resolve.

The plan: a header row, then one row per clip. Columns: clip_id (required: a
pool clip's unique id, or its exact name when that names one clip), keywords
(semicolon-separated), description. Cells are split on tabs, rows on newlines;
there is no quoting, so a tab or newline inside a value is a problem the check
reports. Not run against a live Resolve: the metadata keys "Keywords" and
"Description" are the ones the Metadata panel shows, and Resolve keeps keywords
as one comma-separated string (both unverified).

The write is narrow: keywords are appended after the text already there (never
removed, reordered or rewritten), and the description is set only where the
clip's is empty. Each write is read back.
"""

import os

from . import api
from .workflows import Refused, _check, _walk_bins, plan_sha

COLUMNS = ("clip_id", "keywords", "description")
MAX_ID, MAX_KEYWORD, MAX_KEYWORDS_TOTAL, MAX_DESCRIPTION, MAX_ROWS = 200, 80, 1000, 2000, 5000


def _norm(word):
    return " ".join(word.split()).casefold()


def _control(s):
    return any(ord(c) < 32 or ord(c) == 127 for c in s)


def parse_plan(text):
    """(rows, problems). Each row: {line, clip_id, keywords (list), description}."""
    lines = text.replace("\r\n", "\n").split("\n")
    numbered = [(i + 1, ln) for i, ln in enumerate(lines) if ln.strip()]
    if not numbered:
        return [], ["the plan is empty: it needs a header row and at least one row"]
    problems = []
    head = numbered[0][1].split("\t")
    cols = [h.strip() for h in head]
    for h in cols:
        if h not in COLUMNS:
            problems.append(f"line {numbered[0][0]}: unknown column '{h}' (known: {', '.join(COLUMNS)})")
    if "clip_id" not in cols:
        problems.append("the header has no clip_id column")
    if len(set(cols)) != len(cols):
        problems.append("the header repeats a column")
    if problems:
        return [], problems
    rows, seen = [], {}
    if len(numbered) == 1:
        problems.append("the plan has no rows after the header")
    if len(numbered) - 1 > MAX_ROWS:
        problems.append(f"the plan has more than {MAX_ROWS} rows")
    for n, ln in numbered[1:]:
        cells = ln.split("\t")
        if len(cells) != len(cols):
            problems.append(f"line {n}: {len(cells)} cells, expected {len(cols)} (a tab or newline "
                            "inside a value splits it; the plan has no quoting)")
            continue
        cell = dict(zip(cols, cells))
        bad = [c for c, v in cell.items() if _control(v)]
        if bad:
            problems.append(f"line {n}: control character in {', '.join(bad)}")
            continue
        cid = cell["clip_id"].strip()
        desc = cell.get("description", "").strip()
        words = []
        for w in cell.get("keywords", "").split(";"):
            w = " ".join(w.split())
            if w and _norm(w) not in [_norm(x) for x in words]:
                words.append(w)
        if not cid:
            problems.append(f"line {n}: empty clip_id")
        elif len(cid) > MAX_ID:
            problems.append(f"line {n}: clip_id is {len(cid)} characters, over {MAX_ID}")
        elif cid in seen:
            problems.append(f"line {n}: duplicate clip_id '{cid}' (first on line {seen[cid]})")
        else:
            seen[cid] = n
        if any("," in w for w in words):
            problems.append(f"line {n}: a keyword has a comma; Resolve separates keywords by commas")
        if any(len(w) > MAX_KEYWORD for w in words):
            problems.append(f"line {n}: a keyword is over {MAX_KEYWORD} characters")
        if sum(len(w) + 2 for w in words) > MAX_KEYWORDS_TOTAL:
            problems.append(f"line {n}: keywords total over {MAX_KEYWORDS_TOTAL} characters")
        if len(desc) > MAX_DESCRIPTION:
            problems.append(f"line {n}: description is {len(desc)} characters, over {MAX_DESCRIPTION}")
        if not words and not desc:
            problems.append(f"line {n}: no keywords and no description")
        rows.append({"line": n, "clip_id": cid, "keywords": words, "description": desc})
    return rows, problems


def _tokens(text):
    return [t.strip() for t in (text or "").split(",") if t.strip()]


def merge_keywords(existing, wanted):
    """(new text, words added). The existing text is kept byte for byte; only
    words not already there (case and whitespace ignored) follow it."""
    have = {_norm(t) for t in _tokens(existing)}
    added = []
    for w in wanted:
        w = " ".join(w.split())
        if _norm(w) not in have:
            have.add(_norm(w))
            added.append(w)
    if not added:
        return existing or "", []
    base = (existing or "").rstrip(" ,")
    return (base + ", " if base else "") + ", ".join(added), added


def clip_keywords(resolve, project_name, rows, dry_run=True, project_id=None, check_cancel=None,
                  expect_sha=None, exists=os.path.exists):
    """Plan, and unless dry_run write, keywords and descriptions onto pool clips of
    the open project. Returns {project, dry_run, plan_sha, results, counts, problems,
    exit_status}: 0 all written or already there, 2 some row reported and left alone
    (not found, ambiguous, offline, another description), 1 any write failed."""
    pm, project, pin = api.pin_for_write(resolve, project_name, project_id)
    _, root = api.media_pool_root(project)
    clips = [c for _, c in _walk_bins(root)]
    by_id, by_name = {}, {}
    for c in clips:
        by_id[c.GetUniqueId()] = c
        by_name.setdefault(c.GetName(), []).append(c)

    plan = []
    for r in rows:
        e = {"clip": r["clip_id"], "line": r["line"], "name": "", "id": "", "kw": r["keywords"],
             "desc": r["description"], "result": "planned", "reason": "", "keywords_added": [],
             "description": "none", "new_kw": "", "set_desc": False}
        clip = by_id.get(r["clip_id"])
        if clip is None:
            named = by_name.get(r["clip_id"], [])
            if len(named) > 1:
                e.update(result="ambiguous", reason=f"{len(named)} clips are named '{r['clip_id']}'; use a unique id")
            elif not named:
                e.update(result="not found", reason="no pool clip has this id or name")
            else:
                clip = named[0]
        if clip is not None:
            e.update(id=clip.GetUniqueId(), name=clip.GetName())
            path = (clip.GetClipProperty("File Path") or "")
            if not path:
                e.update(result="refused", reason="not a file-backed clip")
            elif not exists(path):
                e.update(result="offline", reason="the clip's file is missing on disk")
            else:
                meta = clip.GetMetadata() or {}
                kw, desc = meta.get("Keywords") or "", (meta.get("Description") or "").strip()
                e["new_kw"], e["keywords_added"] = merge_keywords(kw, r["keywords"])
                if r["description"] and not desc:
                    e["description"], e["set_desc"] = "set", True
                elif r["description"] and desc == r["description"]:
                    e["description"] = "already the same"
                elif r["description"]:
                    e["description"] = "kept: the clip has another description"
                e["before"] = [kw, desc]
                if not e["keywords_added"] and not e["set_desc"]:
                    e["result"] = "unchanged"
        plan.append(e)
    sha = plan_sha("clip_keywords", pin.unique_id,
                   [[e["clip"], e["id"], e["result"], e["keywords_added"], e["set_desc"], e["desc"],
                     e.get("before")] for e in plan])
    out = {"project": {"name": pin.name, "id": pin.unique_id}, "dry_run": dry_run, "plan_sha": sha,
           "problems": [], "keywords_written": []}
    if not dry_run:
        if expect_sha != sha:
            raise Refused("the plan changed since the dry run (the pool's metadata or the plan "
                          "differ); run the dry run again and review it")
        check = _check(pin, pm, check_cancel)
        try:
            for e in plan:
                if e["result"] != "planned":
                    continue
                check()
                e["result"] = _write_one(by_id[e["id"]], e)
                if e["result"] == "written":
                    out["keywords_written"].append({"clip": e["clip"], "id": e["id"], "name": e["name"]})
                else:
                    out["problems"].append(f"{e['name']}: {e['result']}")
        except BaseException as stop:
            stop.keywords_written = list(out["keywords_written"])
            if out["keywords_written"]:
                stop.args = (f"{stop.args[0] if stop.args else stop} ({len(out['keywords_written'])} "
                             "clip(s) already written; the journal lists them)",) + tuple(stop.args[1:])
            raise
    out["results"] = [{k: e[k] for k in ("line", "clip", "id", "name", "result", "reason",
                                         "keywords_added", "description")} for e in plan]
    counts = {}
    for e in plan:
        k = e["result"].split(":")[0]
        counts[k] = counts.get(k, 0) + 1
    out["counts"] = counts
    failed = any(e["result"].startswith("FAILED") for e in plan)
    left = any(e["result"] in ("not found", "ambiguous", "offline", "refused")
               or (e["result"] == "unchanged" and e["description"].startswith("kept"))
               for e in plan)
    out["exit_status"] = 1 if failed else (2 if left else 0)
    return out


def _write_one(clip, e):
    """Write one clip's keywords and description, read each back: 'written' or 'FAILED: ...'."""
    if e["keywords_added"]:
        if not clip.SetMetadata("Keywords", e["new_kw"]):
            return "FAILED: SetMetadata('Keywords') returned false"
        got = clip.GetMetadata("Keywords") or ""
        if [_norm(t) for t in _tokens(got)] != [_norm(t) for t in _tokens(e["new_kw"])]:
            return f"FAILED: Keywords read '{got}' after the write"
    if e["set_desc"]:
        if not clip.SetMetadata("Description", e["desc"]):
            return "FAILED: SetMetadata('Description') returned false"
        got = (clip.GetMetadata("Description") or "").strip()
        if got != e["desc"]:
            return f"FAILED: Description reads '{got}' after the write"
    return "written"
