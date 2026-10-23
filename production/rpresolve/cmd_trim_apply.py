"""
trim-apply: the trim-review rows an editor accepted, applied to a copy of the cut manifest. Offline; the work is rpresolve/trimapply.py.
"""
from rpresolve import cli

NAME = "trim-apply"
HELP = "Offline: apply the accepted trim-review rows to a copy of the manifest"


def add_arguments(p):
    from rpresolve import trimapply
    trimapply.add_arguments(p)


def run(args):
    from rpresolve import trimapply
    return trimapply.run(args)


if __name__ == "__main__":
    cli.script(__name__)
