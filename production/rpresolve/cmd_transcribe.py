"""
transcribe: speech to Whisper JSON, SubRip and text, from a chosen backend. Offline; the work is rpresolve/transcribe.py.
"""

from rpresolve import cli

NAME = "transcribe"
HELP = "Offline: transcribe media to Whisper JSON, SRT and text"


def add_arguments(p):
    from rpresolve import transcribe
    transcribe.add_arguments(p)


def run(args):
    from rpresolve import transcribe
    return transcribe.run(args)


if __name__ == "__main__":
    cli.script(__name__)
