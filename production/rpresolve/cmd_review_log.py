"""
review-log: append one answer to <project dir>/review/events.jsonl, the log that
taste-profile reads. The key (an absheet DIR.key/key.json, a mixbuild
sealed/key.json, or the folder that has one) decodes the letter into the options
and sets `stimulus` to the sha256 of the sheet or packet. Append-only; refuses a
log inside any git working tree, an unknown letter or step, a project id that is
not like P-0001, and a --repeat-of that names no line. A question outside the
vocabulary is logged with a warning. Exit 2 on a refusal. Touches neither Resolve
nor the media.
"""

import json
import sys

from rpresolve import cli

NAME = "review-log"
HELP = "Append one decoded answer to review/events.jsonl"


def add_arguments(p):
    p.add_argument("--dir", required=True, help="the project folder; the log is DIR/review/events.jsonl")
    p.add_argument("--project", required=True, help="project id like P-0001, never a name")
    p.add_argument("--step", required=True, help="triage, look, grade, edit, sound or gate")
    p.add_argument("--device", required=True, help="desktop, phone or ipad")
    p.add_argument("--choice", help="a letter of the key, a rating 1 to 5 (no key), approve, go, none or hold")
    p.add_argument("--reason", help="the reviewer's line, verbatim")
    p.add_argument("--key", help="key.json, or the sheet or packet folder that has one")
    p.add_argument("--question", help="what was asked, e.g. warmth, ending, tempo, hook_text")
    p.add_argument("--noise-type", help="denoise decisions: rumble, wind, hiss, room or other:WORD")
    p.add_argument("--repeat-of", help="the ts of the line this repeats")
    p.add_argument("--review-copy", metavar="FILE", help="the review copy this approve, go or hold is about; "
                   "logs its name and sha256 for deliver-check --review-log")


def run(args):
    from rpresolve import reviewlog
    try:
        line = reviewlog.append(args.dir, args.project, args.step, args.device, args.choice, args.reason,
                                args.key, args.question, args.noise_type, args.repeat_of,
                                review_copy=args.review_copy)
    except (reviewlog.Refused, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print(json.dumps(line, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    cli.script(__name__)
