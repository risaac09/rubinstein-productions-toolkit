"""
release-check: one READY or BLOCKED table over an upload folder. Read-only.

Per video file in the folder (flat; SHA256SUMS is the only other file read):
  sha       the file matches its SHA256SUMS line (manifest.sha256)
  name      the house rule (deliver.parse_name)
  stub      a ledger stub (deliver-check --ledger-row) says pass for this sha256
  approval  an approve or go line in the review log names it
            (delivercheck.find_approval); SKIP without a log
  title     60 characters or fewer, unique, no [UPPER CASE] placeholder, and
            a row for the file in the titles TSV (file<TAB>title)
A title row for a file that is not in the folder is an orphan and FAILs.
A FAIL or a missing approval or stub BLOCKs; a SKIP does not.
"""

import json
import os
import re

from rpresolve import deliver, delivercheck as dc, manifest as mf

SUMS = "SHA256SUMS"
TITLE_MAX = 60
PLACEHOLDER = re.compile(r"\[[A-Z][A-Z0-9 _-]*\]")
COLUMNS = ("sha", "name", "stub", "approval", "title")


class ReleaseError(RuntimeError):
    """An input cannot be read."""


def read_sums(folder):
    """{file name: sha256} from SHA256SUMS ('<sha256>  <name>'); {} when absent."""
    path = os.path.join(folder, SUMS)
    out = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                h, _, name = line.rstrip("\n").partition("  ")
                if mf.SHA_RE.fullmatch(h.lower()):
                    out[name.lstrip("*")] = h.lower()
    return out


def read_titles(path):
    """[(file, title)] from a TSV; blank, # and a leading 'file' header skipped."""
    try:
        with open(path, encoding="utf-8") as f:
            rows = [ln.rstrip("\r\n").split("\t", 1) for ln in f if ln.strip() and not ln.startswith("#")]
    except OSError as e:
        raise ReleaseError(f"cannot read {path}: {e.strerror}")
    if rows and rows[0][0].strip().lower() == "file":
        rows = rows[1:]
    return [(r[0].strip(), r[1].strip() if len(r) > 1 else "") for r in rows]


def read_stubs(path):
    """{file name: newest stub} from a JSONL file or a folder of them."""
    out = {}
    try:
        files = ([os.path.join(path, n) for n in sorted(os.listdir(path))]
                 if os.path.isdir(path) else [path])
        for p in files:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and row.get("file"):
                        out[row["file"]] = row
    except OSError as e:
        raise ReleaseError(f"cannot read {path}: {e.strerror}")
    return out


def _title_problem(title, count):
    if not title:
        return "empty title"
    if len(title) > TITLE_MAX:
        return f"{len(title)} characters (max {TITLE_MAX})"
    m = PLACEHOLDER.search(title)
    if m:
        return f"placeholder {m.group(0)}"
    if count > 1:
        return "duplicate title"
    return None


def check(folder, titles, review_log=None, stubs=None):
    """(rows, orphans, verdict). rows: [{file, cells: {column: (status, note)}}]
    with status PASS, FAIL, BLOCK or SKIP; orphans: title rows with no file."""
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        raise ReleaseError(f"{folder} is not a folder")
    names = sorted(n for n in os.listdir(folder) if os.path.isfile(os.path.join(folder, n))
                   and os.path.splitext(n)[1].lower() in mf.VIDEO_EXTS)
    sums, stub_rows = read_sums(folder), (read_stubs(stubs) if stubs else None)
    events = None
    if review_log:
        from rpresolve import tastedistill
        try:
            events = tastedistill.read_events(review_log)[0]
        except OSError as e:
            raise ReleaseError(f"cannot read {review_log}: {e.strerror}")
    by_file = {}
    for n, t in titles:
        by_file.setdefault(n, []).append(t)
    seen = {}
    for ts in by_file.values():
        for t in ts:
            seen[t.casefold()] = seen.get(t.casefold(), 0) + 1
    rows = []
    for name in names:
        path = os.path.join(folder, name)
        actual, cells = mf.sha256(path), {}
        want = sums.get(name)
        cells["sha"] = (("FAIL", "no SHA256SUMS file or line") if want is None else
                        ("PASS", "") if want == actual else ("FAIL", "does not match SHA256SUMS"))
        try:
            deliver.parse_name(name)
            cells["name"] = ("PASS", "")
        except deliver.NameRuleError as e:
            cells["name"] = ("FAIL", str(e))
        if stub_rows is None:
            cells["stub"] = ("SKIP", "--stubs not given")
        else:
            s = stub_rows.get(name)
            cells["stub"] = (("BLOCK", "no ledger stub") if s is None else
                             ("FAIL", "stub is not a pass") if s.get("check_status") != "pass" else
                             ("FAIL", "stub sha256 differs") if s.get("sha256") != actual else ("PASS", ""))
        if events is None:
            cells["approval"] = ("SKIP", "--review-log not given")
        else:
            line, why = dc.find_approval(events, path, actual)
            cells["approval"] = ("PASS", line.get("ts", "")) if line else ("BLOCK", why)
        ts = by_file.get(name)
        if not ts:
            cells["title"] = ("FAIL", "no title row")
        else:
            bad = [p for p in (_title_problem(t, seen[t.casefold()]) for t in ts) if p]
            if len(ts) > 1:
                bad.append("two title rows")
            cells["title"] = ("FAIL", "; ".join(bad)) if bad else ("PASS", "")
        rows.append({"file": name, "cells": cells})
    orphans = sorted(n for n in by_file if n not in names)
    states = {s for r in rows for s, _ in r["cells"].values()}
    verdict = "READY" if rows and not orphans and not states & {"FAIL", "BLOCK"} else "BLOCKED"
    return rows, orphans, verdict


def format_table(rows, orphans, verdict):
    lines = ["| file | " + " | ".join(COLUMNS) + " | verdict |", "|---|" + "---|" * (len(COLUMNS) + 1)]
    notes = []
    for r in rows:
        c = r["cells"]
        bad = any(s in ("FAIL", "BLOCK") for s, _ in c.values())
        lines.append(f"| {r['file']} | " + " | ".join(c[k][0] for k in COLUMNS)
                     + f" | {'BLOCKED' if bad else 'READY'} |")
        notes += [f"{r['file']}: {k}: {c[k][1]}" for k in COLUMNS if c[k][0] in ("FAIL", "BLOCK") and c[k][1]]
    notes += [f"{n}: title row has no file in the folder (orphan)" for n in orphans]
    if not rows:
        notes.append("no mp4 or mov files in the folder")
    return "\n".join(lines + [""] + notes + ["", verdict]) + "\n"
