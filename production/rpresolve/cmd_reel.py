"""
reel: a talking-head cut manifest and a music track as a beat-aligned social
reel plan (reel.json), and a 9:16 preview of it. Offline; the work is
rpresolve/reel.py.
"""

from rpresolve import cli, reel

NAME = "reel"
HELP = "Offline: beat-aligned social reel plan from a cut manifest and music, and its preview"


def add_arguments(p):
    reel.add_arguments(p)


def run(args):
    return reel.run(args)


if __name__ == "__main__":
    cli.script(__name__)
