"""
beats: a beat map for a piece of music, as a proposal. Offline; the work is
rpresolve/beats.py.
"""

import argparse

from rpresolve import cli

NAME = "beats"
HELP = "Offline: a beat map proposal for a music file"


def add_arguments(p):
    try:
        from rpresolve import beats
    except ModuleNotFoundError as e:
        if e.name != "numpy":
            raise
        # The parser is built on the stdlib-only Python too; any use says numpy is missing.
        p.usage = "%(prog)s needs numpy, which this Python does not have"
        p.add_argument("args", nargs=argparse.REMAINDER, help="(this command needs numpy)")
        return
    beats.add_arguments(p)


def run(args):
    from rpresolve import beats
    return beats.run(args)


if __name__ == "__main__":
    cli.script(__name__)
