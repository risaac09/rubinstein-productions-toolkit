"""
rpresolve.manifestup: write a version 2 copy of a cut manifest.

    python3 -m rpresolve.manifestup MANIFEST --out NEW.json

Reads a manifest of either version (cutlist.load_manifest), writes the same
cut as version 2 (cutlist.to_v2): one source with the id "main", the v1
source keys moved into it, every span, clip and reframe as it was. Never in
place: --out is refused when it is the manifest, already exists, or lies
inside a git working tree. There is no way back to version 1.
"""

import argparse
import json
import os
import sys

from . import cutlist, paths


def add_arguments(p):
    p.add_argument("manifest", help="a cut manifest, version 1 or 2")
    p.add_argument("--out", required=True, help="the version 2 copy, outside any git working tree")


def run(args):
    try:
        problem = paths.out_problem(args.out, any_git_tree=True)
        if problem:
            raise paths.OutputRefused(problem)
        if os.path.exists(args.out):
            raise paths.OutputRefused(f"{args.out} exists; a manifest copy is never written over "
                                      "another file")
        m = cutlist.load_manifest(args.manifest, allow_no_speech=True)
        out = cutlist.to_v2(m)
        paths.write_private(args.out, json.dumps(out, indent=2) + "\n", any_git_tree=True)
    except (OSError, ValueError) as e:  # CutlistError, OutputRefused, bad JSON
        print(f"manifestup: {e}", file=sys.stderr)
        return 2
    print(f"manifest version {cutlist.version(m)} -> 2: {len(out['clips'])} clip(s) -> {args.out}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="python3 -m rpresolve.manifestup", description=__doc__.split("\n\n")[0])
    add_arguments(p)
    return run(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
