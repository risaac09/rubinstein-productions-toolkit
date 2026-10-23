"""
trim-review: silence, filler and repeat review of an edit as a TSV; deletes
nothing. The MCP trim_review_markers tool marks an [auto] timeline from it.
"""

import os
import sys

from rpresolve import cli
from rpresolve import paths as rppaths

NAME = "trim-review"
HELP = "Silence and filler review as a TSV; deletes nothing"


def _review_opts(args):
    db = args.silence_db
    if db != "auto":
        try:
            db = float(db)
        except ValueError:
            raise ValueError(f"--silence-db takes dBFS (such as -45) or auto (got {db!r}).")
    return {"silence_db": db, "min_silence_s": args.min_silence, "tighten_s": args.tighten,
            "cut_s": args.cut, "soft": args.soft_fillers}


def add_arguments(p):
    p.add_argument("input", help="A cut manifest (.json), or the source media file")
    p.add_argument("--words", help="mlx_whisper word JSON (default: the manifest's); without "
                                   "it a source gets silences only")
    p.add_argument("--out", help="Write the TSV here instead of stdout (refused in this repo)")
    p.add_argument("--silence-db", default=str(-45.0),
                   help="Silence below this dBFS, or 'auto' (the quiet floor plus 10 dB); "
                        "default -45")
    p.add_argument("--min-silence", type=float, default=0.8,
                   help="Shortest silence listed, seconds (default 0.8)")
    p.add_argument("--tighten", type=float, default=1.2,
                   help="A silence this long suggests tighten (default 1.2 s)")
    p.add_argument("--cut", type=float, default=2.5,
                   help="A silence this long suggests cut-candidate (default 2.5 s)")
    p.add_argument("--soft-fillers", action="store_true",
                   help="Also list 'like', 'you know' and 'I mean' (low confidence, keep)")
    p.add_argument("--no-audio", action="store_true", help="Words only: no silences")


def run(args):
    """Silence and filler review, offline, to a TSV (start, end, kind, text,
    confidence, suggestion) from a cut manifest or a whole source with its
    words JSON. Nothing is cut, rippled or deleted. Exit 0 done, 1 refused
    or failed."""
    from rpresolve import cutlist, trimreview as tr
    manifest = tr.is_manifest(args.input)
    try:
        opts = _review_opts(args)
        if manifest:
            m = cutlist.load_manifest(args.input)
            paths_ = [s["path"] for s in cutlist.sources(m)]
        else:
            paths_ = [args.input]
        words = cutlist.load_words(args.words) if args.words else None
    except (cutlist.CutlistError, OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    source = paths_[0]
    for path_ in paths_:
        if not os.path.isfile(path_):
            print(f"ERROR: the source {path_} is not a file.", file=sys.stderr)
            return 1
    problem = rppaths.out_problem(args.out) if args.out else None
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    try:
        if manifest:
            r = tr.review_manifest(m, words, audio=not args.no_audio, **opts)
        else:
            r = tr.review_source(source, words, audio=not args.no_audio, **opts)
    except (cutlist.CutlistError, OSError, ValueError) as e:  # words are read per source now
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    text = tr.tsv(r["rows"])
    if args.out:
        cli.write_private(args.out, text)
        print(f"Wrote {len(r['rows'])} row(s) to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    thr = sorted({t for t in r["thresholds"] if t is not None})
    print(tr.summary(r["rows"]) + (f" Silence below {', '.join(f'{t:g}' for t in thr)} dBFS."
                                   if thr else ""), file=sys.stderr)
    return 0


if __name__ == "__main__":
    cli.script(__name__)
