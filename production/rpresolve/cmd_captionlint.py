"""
captionlint: a caption file checked against reading rules, as a FAIL-first table. Offline; the work is rpresolve/captionlint.py.
"""

from rpresolve import cli

NAME = "captionlint"
HELP = "Offline: check a caption file against reading rules"


def add_arguments(p):
    from rpresolve import captionlint
    captionlint.add_arguments(p)


def run(args):
    from rpresolve import captionlint
    return captionlint.run(args)


if __name__ == "__main__":
    cli.script(__name__)
