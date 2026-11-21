"""
decon: take a finished video apart and score a rebuild. `decon recipe REF --out
recipe.json` writes the reference's cuts, dissolves, end black and (with --beats) tempo
as an edit method that methods.validate passes (`--grade` adds a folder of per-scene .cube grades); `--against METHOD` checks the round trip
on a render of a known method. `decon compare REF REBUILD --out compare.json [--sheet
sheet.png]` gives cut-time error, rhythm and colour distance and one score out of 100.
`decon fetch URL --out-dir DIR` downloads one reference with yt-dlp (study only, outside any git
tree). The rest is offline; the work is rpresolve/decon.py, the cuts come from rpresolve/shots.py.
"""

import json
import os
import sys

from rpresolve import cli

NAME = "decon"
HELP = "Offline: a finished video as an edit-method recipe, and a rebuild scored against it"


def _shots_flags(p):
    p.add_argument("--backend", choices=("scdet", "pyscenedetect"), default="scdet",
                   help="hard-cut detector, as shots (pyscenedetect finds more on real edits)")
    p.add_argument("--no-dissolves", action="store_true", help="skip the dissolve pass (handheld footage)")


def add_arguments(p):
    sub = p.add_subparsers(dest="action", required=True)
    f = sub.add_parser("fetch", help="download one reference with yt-dlp and record it (study only, never committed)")
    f.add_argument("url", help="an http or https video URL")
    f.add_argument("--out-dir", required=True, help="folder for the video, info json and <id>.ref.json, outside any "
                   "git tree. References are for study only: never commit or publish them.")
    f.add_argument("--yt-dlp", help="path to yt-dlp (default: on PATH)")
    r = sub.add_parser("recipe", help="a reference video as an edit method (timing only)")
    r.add_argument("reference")
    r.add_argument("--out", required=True, help="recipe .json, outside any git tree")
    r.add_argument("--id", default="reference-recipe", help="the method id (lowercase words and hyphens)")
    r.add_argument("--name", help="the method name")
    r.add_argument("--fits", default="motion", help="comma-separated fits tags (placeholder: motion)")
    r.add_argument("--beats", help="a beat map (beats_s) on the reference's own clock, for bpm and snap")
    r.add_argument("--grade", action="store_true",
                   help="also estimate a grade per scene as .cube files in OUT's sibling folder (OUT minus .json, plus .grade)")
    r.add_argument("--against", help="a method id or path this video was built from: check the round trip")
    _shots_flags(r)
    c = sub.add_parser("compare", help="score a rebuild against its reference")
    c.add_argument("reference")
    c.add_argument("rebuild")
    c.add_argument("--out", required=True, help="compare .json, outside any git tree")
    c.add_argument("--sheet", help="side-by-side PNG of the matched shots, outside any git tree")
    _shots_flags(c)


def _fetch(args):
    from rpresolve import decon_fetch, delivercheck as dc, paths
    try:
        rec = decon_fetch.fetch(args.url, args.out_dir, args.yt_dlp)
    except (dc.ToolMissing, dc.CheckError, paths.OutputRefused, OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"fetched {rec['id']}: {rec['duration']} s, {rec['width']}x{rec['height']}, {rec['file']}")
    print(f"Wrote {os.path.join(args.out_dir, rec['id'] + '.ref.json')}", file=sys.stderr)
    return 0


def run(args):
    if args.action == "fetch":
        return _fetch(args)
    from rpresolve import delivercheck as dc, paths
    try:
        from rpresolve import decon, methods, shots
    except ModuleNotFoundError as e:
        print(f"ERROR: decon needs {e.name}, which this Python does not have", file=sys.stderr)
        return 1
    try:
        for out in (args.out, getattr(args, "sheet", None)):
            problem = out and paths.out_problem(out, any_git_tree=True)
            if problem:
                raise paths.OutputRefused(problem)
        decon.check_outputs([args.reference, getattr(args, "rebuild", None), args.beats if args.action == "recipe" else None],
                            [args.out, getattr(args, "sheet", None)])
        dissolves = not args.no_dissolves
        ref = shots.build(args.reference, dissolves=dissolves, backend=args.backend)
        if args.action == "recipe":
            fits = [f.strip() for f in args.fits.split(",") if f.strip()]
            doc = decon.recipe(ref, args.reference, args.id, args.name, fits, args.beats)
            msg = f"recipe: {len(doc['slots'])} slots, {doc['length_s']} s, end {doc['rules']['end']}"
            ok = True
            if args.grade:
                from rpresolve import decon_grade
                gdir = os.path.splitext(args.out)[0] + ".grade"
                problem = paths.out_problem(gdir, any_git_tree=True)
                if problem:
                    raise paths.OutputRefused(problem)
                g = decon_grade.grade(ref, args.reference, gdir)
                msg += f"\ngrade: {len(g['scenes'])} scenes, {sum(1 for s in g['scenes'] if s['cube'])} cubes in {gdir}"
            if args.against:
                trip = decon.match_method(doc, methods.load(args.against))
                ok = trip["ok"]
                msg += (f"\nround trip against {args.against}: slots {trip['slots'][0]} vs {trip['slots'][1]}, "
                        f"worst {trip['max_error_frames']} frames: {'pass' if trip['ok'] else 'FAIL'}")
        else:
            rb = shots.build(args.rebuild, dissolves=dissolves, backend=args.backend)
            doc = decon.compare(ref, rb, args.reference, args.rebuild, args.sheet)
            msg, ok = decon.summary_compare(doc), True
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1)
    except (dc.ToolMissing, dc.CheckError, OSError, ValueError, ImportError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(msg)
    print(f"Wrote {args.out}" + (f" and {args.sheet}" if getattr(args, "sheet", None) else ""), file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    cli.script(__name__)
