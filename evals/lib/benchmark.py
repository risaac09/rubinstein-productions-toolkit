#!/usr/bin/env python3
"""
benchmark.py — Generate a markdown scorecard from graded eval runs.

Usage:
  python3 lib/benchmark.py <skill-name>

Reads the latest iteration's grading.json files and produces benchmark.md
at iteration-N/benchmark.md with aggregate scores, per-eval breakdown,
and delta analysis between with_skill and baseline modes.
"""
import json
import sys
from pathlib import Path
from statistics import mean

HARNESS_ROOT = Path(__file__).parent.parent


def find_latest_iteration(skill_dir: Path) -> Path:
    iterations = sorted([d for d in skill_dir.iterdir() if d.is_dir() and d.name.startswith("iteration-")])
    if not iterations:
        raise SystemExit(f"No iteration-N directories found in {skill_dir}")
    return iterations[-1]


def main():
    if len(sys.argv) < 2:
        print("Usage: benchmark.py <skill-name>", file=sys.stderr)
        sys.exit(1)

    skill_name = sys.argv[1]
    skill_dir = HARNESS_ROOT / skill_name
    iteration = find_latest_iteration(skill_dir)
    evals_config = json.loads((skill_dir / "evals.json").read_text())

    rows = []
    for eval_def in evals_config["evals"]:
        name = eval_def["name"]
        for mode in ("with_skill", "baseline"):
            grading_path = iteration / name / mode / "grading.json"
            if not grading_path.exists():
                continue
            grading = json.loads(grading_path.read_text())
            exps = grading.get("expectations", [])
            # Split programmatic vs qualitative by looking at assertion definitions
            prog_ids = set()
            qual_ids = set()
            for a in evals_config.get("universal_assertions", []) + eval_def.get("fixture_assertions", []):
                if a.get("check") == "programmatic":
                    prog_ids.add(a["id"])
                elif a.get("check") == "qualitative":
                    qual_ids.add(a["id"])
            prog_passed = sum(1 for e in exps if e.get("assertion_id") in prog_ids and e.get("passed") is True)
            prog_total = sum(1 for e in exps if e.get("assertion_id") in prog_ids)
            qual_passed = sum(1 for e in exps if e.get("assertion_id") in qual_ids and e.get("passed") is True)
            qual_total = sum(1 for e in exps if e.get("assertion_id") in qual_ids)
            rows.append({
                "eval": name, "mode": mode,
                "prog": (prog_passed, prog_total),
                "qual": (qual_passed, qual_total),
                "words": grading.get("body_word_count", 0),
            })

    # Aggregate
    def agg(mode):
        r = [row for row in rows if row["mode"] == mode]
        prog_p = sum(row["prog"][0] for row in r)
        prog_t = sum(row["prog"][1] for row in r)
        qual_p = sum(row["qual"][0] for row in r)
        qual_t = sum(row["qual"][1] for row in r)
        words = [row["words"] for row in r]
        return prog_p, prog_t, qual_p, qual_t, words

    ws_pp, ws_pt, ws_qp, ws_qt, ws_words = agg("with_skill")
    bl_pp, bl_pt, bl_qp, bl_qt, bl_words = agg("baseline")
    ws_prog_pct = round(100 * ws_pp / ws_pt, 1) if ws_pt else 0
    bl_prog_pct = round(100 * bl_pp / bl_pt, 1) if bl_pt else 0
    ws_qual_pct = round(100 * ws_qp / ws_qt, 1) if ws_qt else 0
    bl_qual_pct = round(100 * bl_qp / bl_qt, 1) if bl_qt else 0
    ws_mean = round(mean(ws_words), 0) if ws_words else 0
    bl_mean = round(mean(bl_words), 0) if bl_words else 0

    # Write benchmark.md
    from datetime import date
    md = f"""# {skill_name} — Eval Results ({iteration.name})

**Date:** {date.today()}
**Sample:** {len(rows)//2} fixtures × 2 modes = {len(rows)} runs

---

## Aggregate

| | with_skill | baseline | Delta |
|---|---|---|---|
| Programmatic | {ws_prog_pct}% ({ws_pp}/{ws_pt}) | {bl_prog_pct}% ({bl_pp}/{bl_pt}) | {ws_prog_pct-bl_prog_pct:+.1f}pp |
| Qualitative | {ws_qual_pct}% ({ws_qp}/{ws_qt}) | {bl_qual_pct}% ({bl_qp}/{bl_qt}) | {ws_qual_pct-bl_qual_pct:+.1f}pp |
| Mean word count | {int(ws_mean)} | {int(bl_mean)} | {int(ws_mean)-int(bl_mean):+d} ({round(100*(ws_mean-bl_mean)/bl_mean, 0) if bl_mean else 0:+.0f}%) |

---

## Per-eval scorecard

| Eval | Mode | Prog | Qual | Words |
|---|---|---|---|---|
"""
    for row in rows:
        md += f"| {row['eval']} | {row['mode']} | {row['prog'][0]}/{row['prog'][1]} | {row['qual'][0]}/{row['qual'][1]} | {row['words']} |\n"

    # Shared failures
    shared_fails = {}
    for eval_def in evals_config["evals"]:
        name = eval_def["name"]
        for mode in ("with_skill", "baseline"):
            grading_path = iteration / name / mode / "grading.json"
            if not grading_path.exists():
                continue
            grading = json.loads(grading_path.read_text())
            for e in grading.get("expectations", []):
                if e.get("passed") is False:
                    key = (e.get("assertion_id", ""), e.get("text", ""))
                    shared_fails.setdefault(key, {"with_skill": 0, "baseline": 0})[mode] += 1

    md += "\n---\n\n## Failure modes (assertions that failed)\n\n"
    md += "| Assertion | with_skill fails | baseline fails |\n|---|---|---|\n"
    for (aid, text), counts in sorted(shared_fails.items(), key=lambda x: -(x[1]["with_skill"] + x[1]["baseline"])):
        md += f"| {text[:70]} | {counts['with_skill']} | {counts['baseline']} |\n"

    md += f"\n---\n\nRaw outputs: `{iteration.name}/<eval-name>/<mode>/output.md`\n"
    md += f"Per-run grading: `{iteration.name}/<eval-name>/<mode>/grading.json`\n"
    md += f"\nRegenerate: `python3 ../lib/grade.py {skill_name} && python3 ../lib/benchmark.py {skill_name}`\n"

    benchmark_path = iteration / "benchmark.md"
    benchmark_path.write_text(md)
    print(f"Wrote {benchmark_path}")
    print(f"\nAggregate — with_skill: prog {ws_prog_pct}% qual {ws_qual_pct}% words {int(ws_mean)}")
    print(f"          — baseline:   prog {bl_prog_pct}% qual {bl_qual_pct}% words {int(bl_mean)}")


if __name__ == "__main__":
    main()
