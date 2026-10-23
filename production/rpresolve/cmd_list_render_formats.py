"""
list-render-formats: list the render formats and codecs Resolve offers, to fix
a destination's format and codec in resolve-config.json. Reads Resolve (Resolve
Studio must be running) and changes nothing.
"""

from rpresolve import cli

NAME = "list-render-formats"
HELP = "List available render formats and codecs"


def add_arguments(p):
    """No flags."""


def run(args):
    resolve = cli.get_resolve()
    project = cli.get_project(resolve)

    formats = project.GetRenderFormats()
    if not formats:
        print("No render formats available.")
        return

    print("Available render formats (format key -> extension):\n")
    for fmt_key, extension in formats.items():
        print(f"  [{fmt_key}] .{extension}")
        codecs = project.GetRenderCodecs(fmt_key)
        if codecs:
            for description, name in codecs.items():
                print(f"      {name}  ({description})")
        print()
    print("Pass the [format key] and a codec NAME (right column above) into a destination in resolve-config.json.")


if __name__ == "__main__":
    cli.script(__name__)
