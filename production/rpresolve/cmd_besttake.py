"""
besttake: a proposal order for the takes of one answer. Offline; the work is
rpresolve/besttake.py.
"""

import argparse

from rpresolve import cli

NAME = "besttake"
HELP = "Offline: rank the takes of one answer as a proposal"


def add_arguments(p):
    try:
        from rpresolve import besttake
    except ModuleNotFoundError as e:
        if e.name != "numpy":
            raise
        # The parser is built on the stdlib-only Python too; any use says numpy is missing.
        p.usage = "%(prog)s needs numpy, which this Python does not have"
        p.add_argument("args", nargs=argparse.REMAINDER, help="(this command needs numpy)")
        return
    besttake.add_arguments(p)


def run(args):
    from rpresolve import besttake
    return besttake.run(args)


if __name__ == "__main__":
    cli.script(__name__)
