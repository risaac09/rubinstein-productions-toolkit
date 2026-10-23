"""
roomtone: find the room tone a recording holds (find) and loop it into a bed with
seams that do not pump (bed). Offline; the work is rpresolve/roomtone.py.
"""

import argparse

from rpresolve import cli

NAME = "roomtone"
HELP = "Offline: find a recording's room tone and loop it into a bed"


def add_arguments(p):
    try:
        from rpresolve import roomtone
    except ModuleNotFoundError as e:
        if e.name != "numpy":
            raise
        # The parser is built on the stdlib-only Python too; any use says numpy is missing.
        p.usage = "%(prog)s needs numpy, which this Python does not have"
        p.add_argument("args", nargs=argparse.REMAINDER, help="(this command needs numpy)")
        return
    roomtone.add_arguments(p)


def run(args):
    from rpresolve import roomtone
    return roomtone.run(args)


if __name__ == "__main__":
    cli.script(__name__)
