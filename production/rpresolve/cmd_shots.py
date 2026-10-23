"""
shots: the shot list of a source file from ffmpeg scdet, at qc's threshold, as CSV
or TSV. Offline; the work is rpresolve/shots.py.
"""

from rpresolve import cli

NAME = "shots"
HELP = "Offline: the shot list of a source file, from ffmpeg scdet"


def add_arguments(p):
    from rpresolve import shots
    shots.add_arguments(p)


def run(args):
    from rpresolve import shots
    return shots.run(args)


if __name__ == "__main__":
    cli.script(__name__)
