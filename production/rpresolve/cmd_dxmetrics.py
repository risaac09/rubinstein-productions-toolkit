"""
dxmetrics: before-and-after numbers for a dialogue chain with no clean reference
(measure, lag, compare). Offline; the work is rpresolve/dxmetrics.py.
"""

import argparse

from rpresolve import cli

NAME = "dxmetrics"
HELP = "Offline: before-and-after dialogue numbers with no clean reference"


def add_arguments(p):
    try:
        from rpresolve import dxmetrics
    except ModuleNotFoundError as e:
        if e.name != "numpy":
            raise
        # The parser is built on the stdlib-only Python too; any use says numpy is missing.
        p.usage = "%(prog)s needs numpy, which this Python does not have"
        p.add_argument("args", nargs=argparse.REMAINDER, help="(this command needs numpy)")
        return
    dxmetrics.add_arguments(p)


def run(args):
    from rpresolve import dxmetrics
    return dxmetrics.run(args)


if __name__ == "__main__":
    cli.script(__name__)
