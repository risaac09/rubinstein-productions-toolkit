"""
taste-profile: distil a project's review log (review/events.jsonl) into a taste
profile. Dry run by default: the profile prints to stdout. A file is written only
with --out, and never inside a git working tree. The profile orders options and
never picks. Reads one file and touches neither Resolve nor the log.
"""

from rpresolve import cli

NAME = "taste-profile"
HELP = "Distil review/events.jsonl into a taste profile (prints; --out to write)"


def add_arguments(p):
    p.add_argument("log", help="Path to the project's review/events.jsonl")
    p.add_argument("--out", help="Write taste-profile.md here (outside any git tree)")


def run(args):
    from rpresolve import paths, tastedistill
    try:
        text = tastedistill.build(args.log)
    except OSError as e:
        print(f"ERROR: {e}")
        return 1
    if args.out:
        try:
            paths.write_private(args.out, text, any_git_tree=True)
        except (paths.OutputRefused, OSError) as e:
            print(f"ERROR: {e}")
            return 1
        print(f"Wrote {args.out}")
        return 0
    print(text)
    return 0


if __name__ == "__main__":
    cli.script(__name__)
