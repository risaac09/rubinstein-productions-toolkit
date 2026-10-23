"""
pacing: words per minute, pause ratio and caption reading speed of a cut. Offline; the work is rpresolve/pacing.py.
"""

from rpresolve import cli

NAME = "pacing"
HELP = "Offline: words per minute, pauses and caption reading speed of a cut"


def add_arguments(p):
    from rpresolve import pacing
    pacing.add_arguments(p)


def run(args):
    from rpresolve import pacing
    return pacing.run(args)


if __name__ == "__main__":
    cli.script(__name__)
