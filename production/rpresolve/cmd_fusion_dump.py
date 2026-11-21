"""
fusion-dump: read a Fusion comp back from Resolve and check its node tree.

    export  ExportFusionComp on one timeline item to a .comp file. Reads Resolve
            and changes nothing in it; the project name (and id) must match the
            open project or it exits 2. The output is refused inside any git tree.
    parse   a .comp or .setting to a JSON tree: nodes, links, modifiers. Offline.
    diff    two trees (parsed, and a hand-written intended one): missing and extra
            nodes, type mismatches, missing and extra links. Exit 0 equal, 1 not.

The tree shape is in rpresolve/fusiondump.py.
"""

import json
import os
import sys

from rpresolve import api as rpapi
from rpresolve import cli, fusiondump
from rpresolve import paths as rppaths

NAME = "fusion-dump"
HELP = "Export a Fusion comp, parse it to a node tree, diff two trees"


def add_arguments(p):
    sub = p.add_subparsers(dest="action", required=True)
    e = sub.add_parser("export", help="ExportFusionComp one timeline item to a .comp")
    e.add_argument("--project", required=True, help="The open project's name")
    e.add_argument("--project-id", required=True, help="The open project's unique id")
    e.add_argument("--timeline", required=True, help="Timeline name or unique id")
    e.add_argument("--item", required=True, type=int, help="1-based item on the video track")
    e.add_argument("--track", type=int, default=1, help="Video track (default 1)")
    e.add_argument("--comp", type=int, default=1, help="Fusion comp index on the item (default 1)")
    e.add_argument("--out", required=True, help="The .comp to write, outside any git tree")
    pa = sub.add_parser("parse", help="A .comp or .setting to a JSON tree")
    pa.add_argument("file")
    pa.add_argument("--out", required=True, help="tree.json to write")
    d = sub.add_parser("diff", help="Compare a parsed tree with an intended one")
    d.add_argument("tree")
    d.add_argument("intended")


def export(resolve, project, project_id, timeline, item, track, comp, out):
    """Export the comp; returns the exit status. Writes only `out`."""
    problem = rppaths.out_problem(out, any_git_tree=True)
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    try:
        _, proj, _ = rpapi.pin_for_write(resolve, project, project_id)
        _, tl = rpapi.find_timeline(proj, timeline)
    except rpapi.ProjectChanged as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except rpapi.ResolveAPIError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    items = tl.GetItemListInTrack("video", track) or []
    if not 1 <= item <= len(items):
        print(f"ERROR: item {item} is not on video track {track} ({len(items)} item(s)).",
              file=sys.stderr)
        return 1
    if not items[item - 1].ExportFusionComp(out, comp) or not os.path.isfile(out):
        print(f"ERROR: ExportFusionComp wrote nothing (comp {comp} on item {item}).",
              file=sys.stderr)
        return 1
    print(f"Wrote {out}")
    return 0


def run(args):
    if args.action == "export":
        try:
            resolve = rpapi.connect()
        except rpapi.ResolveUnavailable as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        return export(resolve, args.project, args.project_id, args.timeline, args.item,
                      args.track, args.comp, args.out)
    if args.action == "parse":
        try:
            with open(args.file, encoding="utf-8") as f:
                tree = fusiondump.parse(f.read())
        except (OSError, ValueError) as e:
            print(f"ERROR: {args.file}: {e}", file=sys.stderr)
            return 1
        cli.write_private(args.out, json.dumps(tree, indent=2) + "\n")
        print(f"{len(tree['nodes'])} node(s), {len(tree['links'])} link(s), "
              f"{len(tree['modifiers'])} modifier(s) -> {args.out}")
        return 0
    try:
        lines = fusiondump.diff(fusiondump.load_tree(args.tree), fusiondump.load_tree(args.intended))
    except (OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print("\n".join(lines) if lines else "The trees match.")
    return 1 if lines else 0


if __name__ == "__main__":
    cli.script(__name__)
