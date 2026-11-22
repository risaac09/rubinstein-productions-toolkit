"""
decon: take a finished video apart and score a rebuild. `decon recipe REF --out
recipe.json` writes the reference's cuts, dissolves, end black and (with --beats) tempo
as an edit method that methods.validate passes (`--effects` adds flashes, freeze frames and speed ramps; `--grade` adds a folder of per-scene .cube grades;
`--moves` each shot's camera move from feature tracking, D3 and D4, with a .moves.json beside it); `--against METHOD` checks the round trip
on a render of a known method. `decon compare REF REBUILD --out compare.json [--sheet
sheet.png]` gives cut-time error, rhythm and colour distance and one score out of 100.
`--titles` on either reads on-screen text with macOS Vision (rpresolve/decon_titles.py): the
recipe carries it as `titles`, compare scores title timing.
`decon sound REF --out sound.json` maps the sound (loudness curve, speech or music spans, hits at cuts); `recipe --sound` carries it into the recipe.
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
    r.add_argument("--titles", action="store_true",
                   help="also read on-screen text (in, out, box, animation) into the recipe's titles (macOS Vision)")
    r.add_argument("--effects", action="store_true",
                   help="also put flashes, freeze frames and speed ramps on the slots (one more decode)")
    r.add_argument("--sound", action="store_true",
                   help="also map the sound (loudness curve, speech or music spans, hits at cuts) into the recipe's `sound` field")
    r.add_argument("--kinds", help="with --sound: a <name>.kinds.json from `vertcut transcribe` (default: the one beside the "
                   "video, else `vertcut kinds` on the audio alone)")
    r.add_argument("--moves", action="store_true",
                   help="also measure each shot's camera move (zoom, pan, tilt, shake, speed ramp) into the slots' "
                   "move, role and scale, with the detail in OUT minus .json, plus .moves.json (needs OpenCV)")
    r.add_argument("--against", help="a method id or path this video was built from: check the round trip")
    _shots_flags(r)
    so = sub.add_parser("sound", help="a reference video's sound map: loudness curve, speech or music spans, hits at cuts")
    so.add_argument("reference")
    so.add_argument("--out", required=True, help="sound .json, outside any git tree")
    so.add_argument("--kinds", help="a <name>.kinds.json (default: the one beside the video, else `vertcut kinds` on the audio)")
    _shots_flags(so)
    c = sub.add_parser("compare", help="score a rebuild against its reference")
    c.add_argument("reference")
    c.add_argument("rebuild")
    c.add_argument("--out", required=True, help="compare .json, outside any git tree")
    c.add_argument("--sheet", help="side-by-side PNG of the matched shots, outside any git tree")
    c.add_argument("--titles", action="store_true", help="also score title timing (macOS Vision)")
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


def _titles(report, video):
    from rpresolve import decon, decon_titles
    fps = report.get("fps")
    return decon_titles.detect(video, fps, round(report["duration_s"] * fps) if fps else 0, decon._dims(video))


def run(args):
    if args.action == "fetch":
        return _fetch(args)
    from rpresolve import delivercheck as dc, paths
    try:
        from rpresolve import audioreport, cutlist, decon, decon_sound, methods, shots
    except ModuleNotFoundError as e:
        print(f"ERROR: decon needs {e.name}, which this Python does not have", file=sys.stderr)
        return 1
    try:
        for out in (args.out, getattr(args, "sheet", None)):
            problem = out and paths.out_problem(out, any_git_tree=True)
            if problem:
                raise paths.OutputRefused(problem)
        decon.check_outputs([args.reference, getattr(args, "rebuild", None), args.beats if args.action == "recipe" else None,
                             getattr(args, "kinds", None)],
                            [args.out, getattr(args, "sheet", None)])
        dissolves = not args.no_dissolves
        ref = shots.build(args.reference, dissolves=dissolves, backend=args.backend, adaptive=True)
        if args.action == "recipe":
            fits = [f.strip() for f in args.fits.split(",") if f.strip()]
            titles = _titles(ref, args.reference) if args.titles else None
            found = None
            if args.effects:
                from rpresolve import decon_effects
                found = decon_effects.detect(ref, args.reference)
            sm = decon_sound.build(args.reference, ref, args.kinds) if getattr(args, "sound", False) else None
            doc = decon.recipe(ref, args.reference, args.id, args.name, fits, args.beats, titles, found, sm)
            msg = f"recipe: {len(doc['slots'])} slots, {doc['length_s']} s, end {doc['rules']['end']}"
            if titles is not None:
                msg += f", {len(titles)} titles"
            if found is not None:
                n = {k: sum(p["kind"] == k for p in found["placed"]) for k in ("flash", "freeze", "ramp")}
                msg += (f"\neffects: {n['flash']} flash, {n['freeze']} freeze, {n['ramp']} ramp placed"
                        f"; {len(found['left_off'])} flash left off (not at a slot's start)")
            ok = True
            if sm:
                msg += "\n" + decon_sound.summary(sm)
            if args.grade:
                from rpresolve import decon_grade
                gdir = os.path.splitext(args.out)[0] + ".grade"
                problem = paths.out_problem(gdir, any_git_tree=True)
                if problem:
                    raise paths.OutputRefused(problem)
                g = decon_grade.grade(ref, args.reference, gdir)
                msg += f"\ngrade: {len(g['scenes'])} scenes, {sum(1 for s in g['scenes'] if s['cube'])} cubes in {gdir}"
            if args.moves:
                from rpresolve import decon_moves
                side = os.path.splitext(args.out)[0] + ".moves.json"
                problem = paths.out_problem(side, any_git_tree=True)
                if problem:
                    raise paths.OutputRefused(problem)
                decon.check_outputs([args.reference, args.beats, getattr(args, "kinds", None), args.out], [side])
                mv = decon_moves.measure(ref, args.reference, len(doc["slots"]))
                doc = decon_moves.apply(doc, mv)
                with open(side, "w", encoding="utf-8") as f:
                    json.dump(mv, f, indent=1)
                msg += f"\n{decon_moves.summary(mv)} ({side})"
            if args.against:
                trip = decon.match_method(doc, methods.load(args.against))
                ok = trip["ok"]
                msg += (f"\nround trip against {args.against}: slots {trip['slots'][0]} vs {trip['slots'][1]}, "
                        f"worst {trip['max_error_frames']} frames: {'pass' if trip['ok'] else 'FAIL'}")
        elif args.action == "sound":
            doc = decon_sound.build(args.reference, ref, args.kinds)
            msg, ok = decon_sound.summary(doc), True
        else:
            rb = shots.build(args.rebuild, dissolves=dissolves, backend=args.backend, adaptive=True)
            titles = (_titles(ref, args.reference), _titles(rb, args.rebuild)) if args.titles else None
            doc = decon.compare(ref, rb, args.reference, args.rebuild, args.sheet, titles)
            msg, ok = decon.summary_compare(doc), True
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1)
    except (dc.ToolMissing, dc.CheckError, audioreport.ReportError, cutlist.CutlistError, OSError, ValueError,
            ImportError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(msg)
    print(f"Wrote {args.out}" + (f" and {args.sheet}" if getattr(args, "sheet", None) else ""), file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    cli.script(__name__)
