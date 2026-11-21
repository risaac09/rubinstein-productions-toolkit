"""
clip-keywords-check: validate a clip-keywords plan (TSV: clip_id, keywords,
description) offline. Reports unknown columns, empty or duplicate ids, control
characters, tabs or newlines inside values and over-long values. Never connects
to Resolve; exit 0 when the plan is clean, 1 otherwise. The write is the
clip_keywords MCP tool.
"""

from rpresolve import clipkeywords

NAME = "clip-keywords-check"
HELP = "Check a clip keywords plan (TSV) offline"


def add_arguments(p):
    p.add_argument("plan", help="Path of the plan TSV")


def run(args):
    try:
        with open(args.plan, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as e:
        print(f"cannot read the plan: {e}")
        return 1
    rows, problems = clipkeywords.parse_plan(text)
    for p in problems:
        print(p)
    if problems:
        return 1
    print(f"plan is clean: {len(rows)} row(s)")
    return 0
