#!/usr/bin/env python3
"""
grade.py — Generic programmatic grader for RP skill evals.

Usage:
  python3 lib/grade.py <skill-name>
  python3 lib/grade.py outreach-email-manager

Walks <skill-name>/iteration-<latest>/<eval-name>/{with_skill,baseline}/output.md,
applies programmatic checks from <skill-name>/evals.json, writes grading.json
per run with qualitative assertions stubbed for LLM grading.

Supported check rules:
  regex_absent   — none of `patterns` may match body text (quote-aware)
  regex_present  — at least one of `patterns` must match body text
  word_count     — body word count must fall in [min, max]
  word_count_max — body word count must be <= max
  line_count     — body line count must fall in [min, max]

Body extraction strips email headers (TO:/SUBJECT:) and signature blocks.
The `quote_aware` flag on regex checks skips text inside quotation marks
(single, double, or smart quotes), preventing false positives when a skill
correctly echoes prospect language back.
"""
import json
import re
import sys
from pathlib import Path

HARNESS_ROOT = Path(__file__).parent.parent


def extract_body(text: str) -> str:
    """Strip email headers and signature block. For non-email outputs, return as-is."""
    lines = text.splitlines()

    # If it doesn't look like an email, just return the full text
    has_headers = any(
        l.strip().lower().startswith(("to:", "subject:", "from:"))
        for l in lines[:8]
    )
    if not has_headers:
        return text.strip()

    body_lines = []
    in_body = False
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith(("to:", "subject:", "cc:", "bcc:", "from:")):
            continue
        if not in_body and stripped:
            in_body = True
        if in_body:
            if re.match(r"^[-—]+\s*(isaac|$)", stripped.lower()):
                break
            if stripped.lower() in ("best,", "warmly,", "thanks,", "sincerely,", "regards,", "best regards,"):
                break
            body_lines.append(line)
    return "\n".join(body_lines).strip()


def strip_quotes(text: str) -> str:
    """Remove anything inside quotation marks for quote-aware checks."""
    # Handles: "..." '...' “...” ‘...’
    patterns = [
        r'"[^"]*"',
        r"'[^']*'",
        r'\u201c[^\u201d]*\u201d',  # curly double
        r'\u2018[^\u2019]*\u2019',  # curly single
    ]
    for p in patterns:
        text = re.sub(p, " ", text)
    return text


def word_count(text: str) -> int:
    return len(text.split())


def line_count(text: str) -> int:
    # Count non-empty lines
    return sum(1 for l in text.splitlines() if l.strip())


def check_regex_absent(body: str, patterns: list, quote_aware: bool = True) -> tuple[bool, str]:
    check_text = strip_quotes(body) if quote_aware else body
    hits = []
    for pat in patterns:
        m = re.search(pat, check_text, re.IGNORECASE)
        if m:
            hits.append(f"'{m.group(0)}'")
    if hits:
        return False, f"found: {', '.join(hits)}"
    return True, "none of the banned patterns appeared"


def check_regex_present(body: str, patterns: list) -> tuple[bool, str]:
    hits = []
    for pat in patterns:
        m = re.search(pat, body, re.IGNORECASE)
        if m:
            hits.append(m.group(0))
    if hits:
        return True, f"matched: {hits[0]}"
    return False, "none of the required patterns matched"


def check_word_count(body: str, min_w: int = 0, max_w: int = 10000) -> tuple[bool, str]:
    wc = word_count(body)
    if min_w <= wc <= max_w:
        return True, f"{wc} words (range {min_w}-{max_w})"
    return False, f"{wc} words (outside range {min_w}-{max_w})"


def check_word_count_max(body: str, max_w: int) -> tuple[bool, str]:
    wc = word_count(body)
    if wc <= max_w:
        return True, f"{wc} words (<= {max_w})"
    return False, f"{wc} words (> {max_w})"


def check_line_count(body: str, min_l: int = 0, max_l: int = 10000) -> tuple[bool, str]:
    lc = line_count(body)
    if min_l <= lc <= max_l:
        return True, f"{lc} lines (range {min_l}-{max_l})"
    return False, f"{lc} lines (outside range {min_l}-{max_l})"


RULES = {
    "regex_absent": lambda body, a: check_regex_absent(body, a["patterns"], a.get("quote_aware", True)),
    "regex_present": lambda body, a: check_regex_present(body, a["patterns"]),
    "word_count": lambda body, a: check_word_count(body, a.get("min", 0), a.get("max", 10000)),
    "word_count_max": lambda body, a: check_word_count_max(body, a["max"]),
    "line_count": lambda body, a: check_line_count(body, a.get("min", 0), a.get("max", 10000)),
}


def grade_run(output_path: Path, assertions: list, existing_grading: dict = None) -> dict:
    """Grade a run. If existing_grading is provided, preserve qualitative grades from it."""
    if not output_path.exists():
        return {"error": f"output missing: {output_path}", "expectations": []}

    content = output_path.read_text()
    body = extract_body(content)
    results = []

    # Build lookup of existing qualitative grades by assertion_id
    existing_qual = {}
    if existing_grading:
        for e in existing_grading.get("expectations", []):
            aid = e.get("assertion_id")
            if aid and e.get("passed") is not None and e.get("evidence") != "pending_llm_grade":
                existing_qual[aid] = {"passed": e["passed"], "evidence": e["evidence"]}

    for a in assertions:
        entry = {"text": a["text"], "assertion_id": a.get("id", "")}
        if a.get("check") == "qualitative":
            aid = a.get("id")
            # Preserve existing LLM grade if present
            if aid in existing_qual:
                entry["passed"] = existing_qual[aid]["passed"]
                entry["evidence"] = existing_qual[aid]["evidence"]
            else:
                entry["passed"] = None
                entry["evidence"] = "pending_llm_grade"
        elif a.get("check") == "programmatic":
            rule = a.get("rule")
            fn = RULES.get(rule)
            if fn:
                passed, ev = fn(body, a)
            else:
                passed, ev = False, f"unknown rule: {rule}"
            entry["passed"] = passed
            entry["evidence"] = ev
        results.append(entry)

    return {
        "body_word_count": word_count(body),
        "body_line_count": line_count(body),
        "expectations": results,
    }


def find_latest_iteration(skill_dir: Path) -> Path:
    iterations = sorted([d for d in skill_dir.iterdir() if d.is_dir() and d.name.startswith("iteration-")])
    if not iterations:
        raise SystemExit(f"No iteration-N directories found in {skill_dir}")
    return iterations[-1]


def main():
    if len(sys.argv) < 2:
        print("Usage: grade.py <skill-name>", file=sys.stderr)
        sys.exit(1)

    skill_name = sys.argv[1]
    skill_dir = HARNESS_ROOT / skill_name
    if not skill_dir.exists():
        print(f"Skill dir not found: {skill_dir}", file=sys.stderr)
        sys.exit(1)

    evals_config = json.loads((skill_dir / "evals.json").read_text())
    iteration = find_latest_iteration(skill_dir)
    print(f"Grading {skill_name} in {iteration.name}")

    universal = evals_config.get("universal_assertions", [])
    summary = {"runs": []}

    for eval_def in evals_config["evals"]:
        name = eval_def["name"]
        all_assertions = universal + eval_def.get("fixture_assertions", [])
        for mode in ("with_skill", "baseline"):
            output_path = iteration / name / mode / "output.md"
            grading_path = iteration / name / mode / "grading.json"
            # Load existing grading to preserve qualitative grades
            existing = None
            if grading_path.exists():
                try:
                    existing = json.loads(grading_path.read_text())
                except json.JSONDecodeError:
                    existing = None
            result = grade_run(output_path, all_assertions, existing_grading=existing)
            grading_path.parent.mkdir(parents=True, exist_ok=True)
            grading_path.write_text(json.dumps(result, indent=2))
            prog = [e for e in result.get("expectations", []) if e.get("passed") is not None]
            passed_prog = sum(1 for e in prog if e["passed"])
            summary["runs"].append({
                "eval_name": name, "mode": mode,
                "prog_passed": passed_prog, "prog_total": len(prog),
                "body_words": result.get("body_word_count", 0),
            })
            print(f"  {name:<38} {mode:<12} prog: {passed_prog}/{len(prog)}  words: {result.get('body_word_count', 0)}")

    (iteration / "programmatic_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nWrote {iteration}/programmatic_summary.json")


if __name__ == "__main__":
    main()
