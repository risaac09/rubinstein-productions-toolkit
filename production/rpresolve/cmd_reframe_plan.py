"""
reframe-plan: a static crop per span for 9:16 and 1:1, centred on the speaker's
face (macOS Vision) and checked on sampled frames; writes a manifest copy and a
report. The MCP cut tool builds the versions from that copy.
"""

import json
import os
import sys

from rpresolve import cli
from rpresolve import paths as rppaths

NAME = "reframe-plan"
HELP = ("Offline: a static crop per span for 9:16 and 1:1, centred on the face, checked on "
        "sampled frames")


def add_arguments(p):
    p.add_argument("manifest")
    p.add_argument("--aspects", nargs="+", choices=["9x16", "1x1"], default=["9x16", "1x1"],
                   help="Versions to plan (default both)")
    p.add_argument("--samples", type=int, default=8,
                   help="Frames sampled per span (default 8; at least --per-second a second)")
    p.add_argument("--per-second", type=float, default=0.5,
                   help="Fewest samples per second of a span (default 0.5)")
    p.add_argument("--speaker-x", type=float,
                   help="The speaker's face x in source pixels: choose the face nearest it "
                        "(for a two-up call recording)")
    p.add_argument("--speaker-y", type=float,
                   help="With --speaker-x, the face's y in source pixels (distance in both)")
    p.add_argument("--subject-region", nargs=4, type=float, metavar=("X0", "Y0", "X1", "Y1"),
                   help="Only faces centred inside this source-pixel rectangle can be the "
                        "subject (leaves out an audience)")
    p.add_argument("--min-face-height", type=float, metavar="SHARE",
                   help="Ignore faces shorter than this share of the frame height (0 to 1)")
    p.add_argument("--min-checked", type=float, default=0.5, metavar="SHARE",  # reframe.MIN_CHECKED_SHARE
                   help="Share of a span's samples that must have a checked face before its "
                        "crop can pass (default 0.5, never fewer than 3)")
    p.add_argument("--prefer", choices=["present", "largest"], default="present",
                   help="Which face is the subject when nothing names it: present in the most "
                        "samples (default) or the largest of those present in at least half as "
                        "many")
    p.add_argument("--only", nargs="+", metavar="CLIP", help="Only these clips")
    p.add_argument("--allow-bars", action="store_true",
                   help="Try the Ep 002 punch-in (2.25) first when a clip names no scale, and "
                        "keep it when it holds the face though the picture does not fill the "
                        "frame (black bars); such crops are named 'bars'")
    p.add_argument("--out", required=True,
                   help="The manifest copy with per-span reframes (refused inside this repo); "
                        "the report goes beside it as <stem>.reframe.tsv and .json")


def run(args):
    """Plan a static crop per span for the 9:16 and 1:1 versions, centred on
    the speaker's face, and check the face stays inside it on every sampled
    frame (see rpresolve/reframe.py). Writes a copy of the manifest with the
    per-span reframes (--out) and the report beside it (<out stem>.reframe.tsv
    and .json). Offline. Exit 0 every crop holds, 2 any span needs a person
    (manual reframe or split, no face, a crop kept with bars, or a flagged
    span: a choice of face, a face off its track, an unchecked sample, a
    span past the source's end), 1 error."""
    from rpresolve import cutlist, reframe
    out = os.path.abspath(args.out)
    problem = rppaths.out_problem(out, any_git_tree=True)
    if not problem and os.path.realpath(args.manifest) == os.path.realpath(out):
        problem = f"{out} is the manifest itself; write the copy somewhere else."
    stem = os.path.splitext(out)[0] + ".reframe"
    for p in (stem + ".tsv", stem + ".json"):
        problem = problem or rppaths.out_problem(p, any_git_tree=True)
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    try:
        m = cutlist.load_manifest(args.manifest, allow_no_speech=True)
        report = reframe.plan(m, aspects=args.aspects, samples=args.samples,
                              per_second=args.per_second, speaker_x=args.speaker_x,
                              speaker_y=getattr(args, "speaker_y", None),
                              only=args.only, allow_bars=args.allow_bars,
                              min_checked=getattr(args, "min_checked", reframe.MIN_CHECKED_SHARE),
                              subject_region=getattr(args, "subject_region", None),
                              min_face_height=getattr(args, "min_face_height", None),
                              prefer=getattr(args, "prefer", reframe.PREFER_PRESENT),
                              progress=lambda d, n: print(f"  span {d} of {n}", file=sys.stderr))
    except (cutlist.CutlistError, reframe.ReframeError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    for r in report["rows"]:
        print(reframe.line(r))
    cli.write_private(out, json.dumps(reframe.apply(m, report), indent=1) + "\n")
    tsv_path, json_path = reframe.write_report(stem, report, cli.write_private)
    print(reframe.summary(report), file=sys.stderr)
    print(f"Wrote {out}, {tsv_path} and {json_path}", file=sys.stderr)
    return 2 if reframe.needs_person(report["rows"]) else 0


if __name__ == "__main__":
    cli.script(__name__)
