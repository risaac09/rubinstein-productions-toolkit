"""
shots: the shot list of a source file from ffmpeg scdet, at qc's threshold, as CSV
or TSV. Offline; the work is rpresolve/shots.py.
"""

import argparse

from rpresolve import cli

NAME = "shots"
HELP = "Offline: the shot list of a source file, from ffmpeg scdet"


def add_arguments(p):
    try:
        from rpresolve import shots
    except ModuleNotFoundError as e:
        if e.name != "numpy":
            raise
        # The parser is built on the stdlib-only Python too; any use says numpy is missing.
        p.usage = "%(prog)s needs numpy, which this Python does not have"
        p.add_argument("args", nargs=argparse.REMAINDER, help="(this command needs numpy)")
        return
    shots.add_arguments(p)


def run(args):
    from rpresolve import shots
    return shots.run(args)


if __name__ == "__main__":
    cli.script(__name__)
