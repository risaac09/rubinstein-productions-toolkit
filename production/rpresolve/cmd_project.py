"""
project: start a project from a camera card, resumable (production/project-start.md).

    project start CARD [--project SLUG] [--new-project] [--cam CAM] [--apply]
                                  copy and verify (a card volume), record the project, identify,
                                  list open questions, index, contact sheets, then the Resolve colour
                                  step; stops at the first step that needs a person; run it again to resume
    project answer SLUG [--squeeze F [--clip ID]...] [--resolved] [--apply]
                                  record a squeeze answer through `card set`, or that a person made the
                                  Resolve project and set its colour
    project status SLUG           the nine steps, the next command for steps 7 to 9, and whether the
                                  card may be formatted

State is <work root>/<slug>/project.json. Paths come from the JSON file named by
$RP_PIPELINE_CONFIG (landing_root, work_root, indexer, offsite_remote, sheets_root;
project.example.json); each can be given on the command line instead. Every step that writes is
a dry run until --apply. A card inside <work root>/X/ belongs to project X; a different --project
is refused unless --new-project. Progress goes to stderr. Never connects to Resolve. Exit 0 done, 2 stopped for a person, 1 refused.
"""

import sys

from rpresolve import cli
from rpresolve import project as rpproject

NAME = "project"
HELP = "Offline: start a project from a camera card, resumable; answer its questions; status"


def add_arguments(p):
    p.add_argument("action", choices=("start", "answer", "status"))
    p.add_argument("target", help="start: a card volume or a card folder with its record; answer, status: the project slug")
    p.add_argument("--project", help="start: the project slug")
    p.add_argument("--new-project", action="store_true",
                   help="start: --project may differ from the work folder the card is in")
    p.add_argument("--cam", help="start: camera code for a card volume (2 to 12 of A-Z 0-9)")
    p.add_argument("--apply", action="store_true", help="write; without it the steps that write only say what they would do")
    p.add_argument("--squeeze", type=float, metavar="FACTOR", help="answer: the squeeze factor; 1 is spherical")
    p.add_argument("--clip", action="append", metavar="ID", help="answer: only this clip (repeatable)")
    p.add_argument("--resolved", action="store_true", help="answer: a person created the Resolve project and set its colour")
    p.add_argument("--config", help="the pipeline config (default $RP_PIPELINE_CONFIG)")
    p.add_argument("--landing", help="where a copied card lands")
    p.add_argument("--work-root", help="where project folders live")
    p.add_argument("--sheets-root", help="where contact sheets are written (outside any git tree)")
    p.add_argument("--indexer", help="the indexer command, run with: index --card CARD")
    p.add_argument("--remote", help="the offsite rclone remote name")


def run(args):
    try:
        cfg = rpproject.load_config(args.config, {"landing_root": args.landing, "work_root": args.work_root,
                                                   "sheets_root": args.sheets_root, "indexer": args.indexer,
                                                   "offsite_remote": args.remote})
        if args.action == "start":
            return rpproject.start(cfg, args.target, args.project, args.cam, args.apply,
                                   say=lambda m: print(m, flush=True), new_project=args.new_project)
        if args.action == "answer":
            return rpproject.answer(cfg, args.target, args.apply, args.squeeze, args.clip or (), args.resolved)
        return rpproject.status(cfg, args.target)
    except (rpproject.ProjectError, rpproject.rpcard.CardError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    cli.script(__name__)
