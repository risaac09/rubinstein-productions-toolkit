"""
wordsnap: Whisper word times moved onto the speech edges in the source audio. Offline;
the work is rpresolve/wordsnap.py.
"""

import argparse

from rpresolve import cli

NAME = "wordsnap"
HELP = "Offline: snap Whisper word times to the speech edges in the source audio"


def add_arguments(p):
    try:
        from rpresolve import wordsnap
    except ModuleNotFoundError as e:
        if e.name != "numpy":
            raise
        # The parser is built on the stdlib-only Python too; any use says numpy is missing.
        p.usage = "%(prog)s needs numpy, which this Python does not have"
        p.add_argument("args", nargs=argparse.REMAINDER, help="(this command needs numpy)")
        return
    wordsnap.add_arguments(p)


def run(args):
    from rpresolve import wordsnap
    return wordsnap.run(args)


if __name__ == "__main__":
    cli.script(__name__)
