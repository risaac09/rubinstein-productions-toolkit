"""
duckplan: duck music under dialogue by a set depth, keyed on speech activity (plan,
render). Offline; the work is rpresolve/duckplan.py.
"""

import argparse

from rpresolve import cli

NAME = "duckplan"
HELP = "Offline: duck music under dialogue by a set depth, keyed on speech"


def add_arguments(p):
    try:
        from rpresolve import duckplan
    except ModuleNotFoundError as e:
        if e.name != "numpy":
            raise
        # The parser is built on the stdlib-only Python too; any use says numpy is missing.
        p.usage = "%(prog)s needs numpy, which this Python does not have"
        p.add_argument("args", nargs=argparse.REMAINDER, help="(this command needs numpy)")
        return
    duckplan.add_arguments(p)


def run(args):
    from rpresolve import duckplan
    return duckplan.run(args)


if __name__ == "__main__":
    cli.script(__name__)
