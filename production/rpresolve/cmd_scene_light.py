"""
scene-light: the light block of the per-shot scene model, one per shot. Each
shot is a video or still (measured here, as `measure` does) or a measure
report saved with `measure --json`. Prints one line per shot; --json writes
[{file, light, taste, review}] for the scene model. See production/scene-light.md.
"""

import json
import sys

from rpresolve import cli
from rpresolve import paths as rppaths

NAME = "scene-light"
HELP = "Offline: the scene model's light block (key, ratio, skin L*, shadow) per shot"


def add_arguments(p):
    p.add_argument("shots", nargs="+", help="Video or still files, or measure --json reports (.json)")
    p.add_argument("--samples", type=int, default=10, help="Frames to sample per file (default 10)")
    p.add_argument("--json", help="Also write the records as JSON here")


def run(args):
    """Exit 1 when a shot cannot be read; nothing is written then."""
    try:
        from rpresolve import measure as rpmeasure
        from rpresolve import scene_light
    except ImportError as e:
        print(f"ERROR: scene-light needs numpy ({e}). Use /usr/bin/python3.", file=sys.stderr)
        return 1
    problem = rppaths.out_problem(args.json, any_git_tree=True) if args.json else None
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    records = []
    for shot in args.shots:
        try:
            if shot.lower().endswith(".json"):
                with open(shot, encoding="utf-8") as f:
                    report = json.load(f)
                if not isinstance(report, dict) or "transfer" not in report:
                    raise ValueError("not a measure --json report")
                report.setdefault("file", shot)
            else:
                report = rpmeasure.measure(shot, samples=args.samples)
        except (OSError, ValueError, rpmeasure.MeasureError) as e:
            print(f"ERROR: {shot}: {e}", file=sys.stderr)
            return 1
        records.append(scene_light.shot_record(report))
        print(scene_light.format_line(records[-1]))
    if args.json:
        cli.write_private(args.json, json.dumps(records, indent=2) + "\n")
        print(f"Wrote {args.json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    cli.script(__name__)
