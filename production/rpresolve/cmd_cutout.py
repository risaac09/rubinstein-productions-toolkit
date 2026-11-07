"""
cutout: the person in a span of a clip as a ProRes 4444 clip with alpha, through macOS
Vision's person segmentation. Offline; the work is rpresolve/cutout.py.
"""

from rpresolve import cli
from rpresolve import cutout

NAME = "cutout"
HELP = "Offline: cut the person out of a span of a clip into a ProRes 4444 clip with alpha"


def add_arguments(p):
    cutout.add_arguments(p)


def run(args):
    return cutout.run(args)


if __name__ == "__main__":
    cli.script(__name__)
