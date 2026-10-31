"""
release-check: one READY or BLOCKED table over an upload folder. Reads
SHA256SUMS, the house file names, ledger stubs, the review log and a titles TSV.
Read-only: it writes nothing beside the media, prints no post or upload command,
and uploads nothing. Exit 0 READY, 1 BLOCKED, 2 an input cannot be read.
"""

import os
import sys

from rpresolve import cli

NAME = "release-check"
HELP = "Offline: one READY or BLOCKED table over an upload folder (sha, name, stub, approval, title)"


def add_arguments(p):
    p.add_argument("folder", help="Upload folder: video files plus SHA256SUMS")
    p.add_argument("--titles", required=True, metavar="TSV",
                   help="file<TAB>title per line (a 'file' header line is skipped)")
    p.add_argument("--review-log", metavar="PATH",
                   help="review/events.jsonl: an approve or go line must name each file")
    p.add_argument("--stubs", metavar="PATH",
                   help="Ledger stubs from deliver-check --ledger-row: a JSONL file or a folder of them")
    p.add_argument("--out", metavar="PATH", help="Write the table here (refused inside any git tree) "
                   "instead of stdout")


def out_problem(args):
    """Why --out would overwrite an input, or None: it sits in the upload folder (or a
    destination subfolder, which may be a link elsewhere) or the stubs folder, or is the
    titles, log or stubs file. samefile, so a differently cased path counts too."""
    from rpresolve import paths
    folder = args.folder
    dirs = [folder] + [os.path.join(folder, n) for n in os.listdir(folder) if os.path.isdir(os.path.join(folder, n))]
    dirs += [args.stubs] if args.stubs and os.path.isdir(args.stubs) else []
    here = os.path.dirname(os.path.abspath(args.out))
    while True:
        if os.path.isdir(here) and any(os.path.samefile(here, d) for d in dirs):
            return f"{args.out} is inside {here}, an input folder; write the table elsewhere"
        if os.path.dirname(here) == here:
            break
        here = os.path.dirname(here)
    return paths.source_problem([args.out], [p for p in (args.titles, args.review_log, args.stubs) if p])


def run(args):
    from rpresolve import paths, releasecheck as rc
    try:
        if not os.path.isdir(args.folder):
            raise rc.ReleaseError(f"{args.folder} is not a folder")
        problem = args.out and out_problem(args)  # before the hashing, which can take minutes
        if problem:
            raise paths.OutputRefused(problem)
        rows, orphans, verdict, other = rc.check(args.folder, rc.read_titles(args.titles),
                                          args.review_log, args.stubs)
        text = rc.format_table(rows, orphans, verdict, other)
        if args.out:
            paths.write_private(args.out, text)
        else:
            sys.stdout.write(text)
    except (rc.ReleaseError, paths.OutputRefused) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    return 0 if verdict == "READY" else 1


if __name__ == "__main__":
    cli.script(__name__)
