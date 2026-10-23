"""
interchange: one clip of a cut manifest as a CMX 3600 EDL, FCPXML 1.10 or OpenTimelineIO
JSON for another editor. Offline; the work is rpresolve/interchange.py.
"""

from rpresolve import cli

NAME = "interchange"
HELP = "Offline: one clip of a cut manifest as an EDL, FCPXML or OTIO file"


def add_arguments(p):
    from rpresolve import interchange
    interchange.add_arguments(p)


def run(args):
    from rpresolve import interchange
    return interchange.run(args)


if __name__ == "__main__":
    cli.script(__name__)
