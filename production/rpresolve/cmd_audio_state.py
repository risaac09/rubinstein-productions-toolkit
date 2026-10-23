"""
audio-state: report what Resolve's API exposes about a timeline's audio (tracks,
items, Voice Isolation and Dialogue Leveler properties, audio mapping, the
Deliver page's format and queued jobs' audio fields, the loudness-meter
settings) and name what it cannot read. Reads Resolve (Resolve Studio must be
running) and changes nothing. Save the JSON and hand it to
`deliver-check --audio-state` to compare the queued jobs with a destination.
"""

import json

from rpresolve import cli

NAME = "audio-state"
HELP = "Read a timeline's audio state from Resolve (read only)"


def add_arguments(p):
    p.add_argument("--timeline", help="Timeline name (default: the current one)")
    p.add_argument("-o", "--out", help="Write the JSON here (outside any git tree)")
    p.add_argument("--json", action="store_true", help="Print the state as JSON")


def run(args):
    from rpresolve import api, audiostate, paths
    resolve = cli.get_resolve()
    project = cli.get_project(resolve)
    if args.timeline:
        try:
            _, tl = api.find_timeline(project, args.timeline)
        except api.ResolveAPIError as e:
            print(f"ERROR: {e}")
            return 1
    else:
        tl = project.GetCurrentTimeline()
    if not tl:
        print("ERROR: no timeline to read.")
        return 1
    state = audiostate.read(project, tl)
    if args.out:
        try:
            paths.write_private(args.out, json.dumps(state, indent=1, default=str),
                                any_git_tree=True)
        except paths.OutputRefused as e:
            print(f"ERROR: {e}")
            return 1
    print(json.dumps(state, indent=1, default=str) if args.json else audiostate.format_report(state),
          end="" if not args.json else "\n")
    return 0


if __name__ == "__main__":
    cli.script(__name__)
