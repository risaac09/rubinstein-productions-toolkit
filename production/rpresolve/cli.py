"""
The command line behind resolve_workflow.py: one parser, one module per command.

Each command is a module rpresolve/cmd_<name>.py (the name with its hyphens as
underscores), found at import, so a new command adds files and edits no shared
one. A command module holds

    NAME = "the-name"
    HELP = "One line for the command list"    # DESCRIPTION, EPILOG: optional, printed as written
    def add_arguments(p): ...                 # its flags, on an argparse parser
    def run(args): ...                        # the work; returns the exit status
    if __name__ == "__main__":
        cli.script(__name__)

and its docstring says what the command does (production/resolve-mcp.md does
not list commands). Keep imports that need numpy inside run(): the parser is
built from every module on the stdlib-only Python 3.9 the MCP server runs on.
Tests go in production/tests/test_<name>.py; CI runs every test module there.

A command runs as `python3 resolve_workflow.py <name>`, or from production/ as
`python3 -m rpresolve.cmd_<name>`. main() returns the exit status; only
script() and resolve_workflow.py's __main__ leave through rpapi.exit_clean.
"""

import argparse
import importlib
import pkgutil
import re
import sys

import rpresolve
from rpresolve import api as rpapi
from rpresolve import config as rpconfig
from rpresolve import paths as rppaths

# The commands that wrote to Resolve are gone. Running one exits 2 and says what to
# use instead. Most have an MCP tool; the BY_HAND ones have none, and the text says
# what to do in Resolve itself.
RETIRED = {
    "new-project": "create the project and set its colour science to DaVinci YRGB Color "
                   "Managed (the MCP ingest tool refuses any other)",
    "import-media": "use the MCP ingest tool",
    "build-timeline": "build the intro and outro timeline",
    "add-subtitles": "use the MCP create_captions tool on an [auto] timeline, or "
                     "File > Import > Subtitle in Resolve (Resolve 21 places no .srt from a script)",
    "auto-subtitle": "use the MCP create_captions tool",
    "render": "use the MCP queue_render tool; it queues the job and a person starts the "
              "render on the Deliver page",
    "render-all": "use the MCP queue_render tool, once per destination; a person "
                  "starts the renders on the Deliver page",
    "clear-queue": "remove the render jobs on the Deliver page",
    "apply-lut": "use the MCP apply_grade tool on an [auto] timeline",
    "apply-drx": "use the MCP apply_grade tool on an [auto] timeline (a .drx needs its "
                 "<name>.json label manifest beside it)",
    "open-page": "click the page tab",
    "export-project": "export the project from the Project Manager, or with File > Export "
                      "Project Archive",
    "ingest": "use the MCP ingest tool",
    "cut": "use the MCP cut tool",
    "deliver-queue": "use the MCP queue_render tool with a destination",
    "captions": "use the MCP create_captions tool",
    "sync": "use the MCP sync tool",
}
BY_HAND = frozenset({"new-project", "build-timeline", "clear-queue", "open-page",
                     "export-project"})
RETIRED_MARKERS = "use the MCP trim_review_markers tool"
# The flags trim-review lost with --markers. Only trim-review gets the tombstone for them.
RETIRED_MARKER_FLAGS = ("--markers", "--timeline", "--project", "--project-id", "--dry-run",
                        "--plan-sha")

DESCRIPTION = ("Read-only and offline commands for DaVinci Resolve work. This script "
               "does not write to Resolve: the MCP server (production/resolve_mcp.py) is "
               "the only write path, and a retired command says what to use instead.")
EPILOG = """
Examples:
  # List available render codecs (use this to fix a destination's codec in resolve-config.json)
  python3 resolve_workflow.py list-render-formats

  # Show project info
  python3 resolve_workflow.py info

  # Survey every project's grades, timelines and media (offline, read-only)
  python3 resolve_workflow.py survey --out /path/outside/repo/survey.md --json /path/outside/repo/survey.json

  # Classify cameras and picture profiles offline; exits 2 if any row needs review
  python3 resolve_workflow.py detect /path/to/card/ --out detect.tsv

  # Measure a render; camera-match numbers for two cameras by time range
  python3 resolve_workflow.py measure render.mov --segment A=0-30 --segment B=30-60 --hero A

  # One frame as scopes and numbers, against a reference still
  python3 resolve_workflow.py perceive render.mov --at 12.5 --ref still.png --out /path/outside/repo/pkt

  # Reframes: plan and check a crop per span (the MCP cut tool builds the versions)
  python3 resolve_workflow.py reframe-plan manifest.json --out /path/outside/repo/manifest.reframed.json

  # After the render: captions to a zero-based .srt, loudness, then the check
  python3 resolve_workflow.py deliver-captions /path/to/renders/SW001_Guest_01_example-clip_16x9.mp4 \\
      --dest youtube_16x9
  python3 resolve_workflow.py deliver-fix-loudness /path/to/renders/SW001_Guest_01_example-clip_16x9.mp4 \\
      --dest youtube_16x9 --replace
  python3 resolve_workflow.py deliver-check /path/to/renders/SW001_Guest_01_example-clip_16x9.mp4 \\
      --dest youtube_16x9 --fps 23.976

  # Dual-system sound: where one recording sits against another (the MCP sync tool stacks them)
  python3 resolve_workflow.py sync-measure /path/to/A001.MOV /path/to/ZOOM0001.WAV

  # What a mix sounds like as numbers and pictures: loudness over time, spectrogram, flags
  python3 resolve_workflow.py audio-report /path/to/mix.wav -o /path/outside/repo/mix-report \\
      --dest youtube_16x9 --start 1:00 --end 2:30

  # Trim review: a TSV of proposals (nothing is cut)
  python3 resolve_workflow.py trim-review manifest.json --out /path/outside/repo/review.tsv
        """


# ---------------------------------------------------------------------------
# Shared by the commands
# ---------------------------------------------------------------------------

def safe_fps(raw_value, default=24.0):
    """Parse a framerate string that may come back from Resolve in an
    unexpected shape; never raises."""
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return default


def get_resolve():
    """Connect to running DaVinci Resolve instance."""
    try:
        return rpapi.connect()
    except rpapi.ResolveUnavailable as e:
        print(f"ERROR: {e}")
        sys.exit(1)


def get_project(resolve):
    """Get current project or exit."""
    pm = resolve.GetProjectManager()
    if not pm:
        print("ERROR: Could not get Project Manager from Resolve.")
        sys.exit(1)
    project = pm.GetCurrentProject()
    if not project:
        print("ERROR: No project is currently open.")
        sys.exit(1)
    return project


def write_private(path, text):
    """Write a file that names private media and words; refused in the repo."""
    try:
        rppaths.write_private(path, text)
    except rppaths.OutputRefused as e:
        raise SystemExit(f"ERROR: {e}")


def deliver_config(args):
    """The config for the deliver commands: --config given after the
    command or before it. A named file that is missing, unreadable or not a
    JSON object stops the command (exit 1); the lenient load_config would
    warn and carry on with the defaults, checking against the wrong rules."""
    path = getattr(args, "deliver_config", None) or args.config
    try:
        return rpconfig.load_config(path, strict=True)
    except rpconfig.ConfigError as e:
        raise SystemExit(f"ERROR: {e}")


def size(text):
    """'3840x2160' -> (3840, 2160); raises ValueError."""
    m = re.fullmatch(r"(\d+)x(\d+)", str(text or "").strip())
    if not m:
        raise ValueError(f"--size must be WIDTHxHEIGHT, such as 3840x2160 (got {text!r}).")
    return int(m.group(1)), int(m.group(2))


def deliver_arguments(p):
    """--dest and --config, as the deliver commands take them."""
    p.add_argument("--dest", required=True,
                   help="Destination key from resolve-config.json (youtube_16x9, "
                        "linkedin_9x16, client_master, ...)")
    p.add_argument("--config", dest="deliver_config",
                   help="A config overlay kept outside this repo (may also go before "
                        "the command)")


# ---------------------------------------------------------------------------
# The parser
# ---------------------------------------------------------------------------

class _Parser(argparse.ArgumentParser):
    """An argument parser that says what replaced a retired command or flag,
    instead of listing the choices that remain."""

    def error(self, message):
        m = re.search(r"invalid choice: '([^']*)'", message)
        if m and m.group(1) in RETIRED:
            name = m.group(1)
            how = (" and has no MCP tool; do it by hand in Resolve: "
                   if name in BY_HAND else ": ")
            self.exit(2, f"{self.prog}: '{name}' is retired{how}{RETIRED[name]}. See "
                         "production/resolve-mcp.md.\n")
        super().error(message)

    def parse_args(self, args=None, namespace=None):
        ns, extras = self.parse_known_args(args, namespace)
        if extras:
            # Only trim-review lost --markers and its flags; any other command that is
            # given one of them gets the ordinary unrecognized-arguments error.
            if getattr(ns, "command", None) == "trim-review" and any(
                    a.split("=", 1)[0] in RETIRED_MARKER_FLAGS for a in extras):
                self.exit(2, f"{self.prog}: trim-review no longer takes "
                             f"{', '.join(RETIRED_MARKER_FLAGS[:-1])} or "
                             f"{RETIRED_MARKER_FLAGS[-1]}: it writes the TSV only. To mark an "
                             f"[auto] timeline, {RETIRED_MARKERS}. See "
                             "production/resolve-mcp.md.\n")
            self.error("unrecognized arguments: " + " ".join(extras))
        return ns


def commands():
    """Every command module, rpresolve/cmd_*.py, in order of command name."""
    found = [importlib.import_module("rpresolve." + m.name)
             for m in pkgutil.iter_modules(rpresolve.__path__)
             if m.name.startswith("cmd_") and m.name.isidentifier()]  # not "cmd_qc copy.py"
    return sorted(found, key=lambda m: m.NAME)


def build_parser(module=None):
    """The resolve_workflow parser, one subcommand per command module; with
    module, a parser for that one command run as `python3 -m`."""
    raw = argparse.RawDescriptionHelpFormatter
    if module is not None:
        parser = _Parser(prog="python3 -m rpresolve.cmd_" + module.NAME.replace("-", "_"),
                         description=getattr(module, "DESCRIPTION", None) or module.HELP,
                         epilog=getattr(module, "EPILOG", None), formatter_class=raw)
        module.add_arguments(parser)
        parser.set_defaults(func=module.run, command=module.NAME, config=None)
        return parser
    parser = _Parser(prog="resolve_workflow", description=DESCRIPTION, formatter_class=raw,
                     epilog=EPILOG)
    parser.add_argument("--config", help="Path to resolve-config.json (default: alongside this script)")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    for m in commands():
        sp = subparsers.add_parser(m.NAME, help=m.HELP, formatter_class=raw,
                                   description=getattr(m, "DESCRIPTION", None),
                                   epilog=getattr(m, "EPILOG", None))
        m.add_arguments(sp)
        sp.set_defaults(func=m.run)
    return parser


def main(argv=None, module=None):
    """Parse argv (default: the command line) and run the command; return its
    exit status. --help and argparse's usage errors raise SystemExit."""
    parser = build_parser(module)
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 1

    try:
        status = args.func(args)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        status = 130
    except SystemExit as e:
        if isinstance(e.code, int) or e.code is None:
            status = e.code
        else:
            print(e.code, file=sys.stderr)
            status = 1
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
        status = 1
    # Commands return an int status; ones that just print return None (= 0).
    return status or 0


def script(name):
    """A command module's __main__: run it and leave through exit_clean, as
    resolve_workflow.py does (the Resolve client can crash on shutdown)."""
    rpapi.exit_clean(main(module=sys.modules[name]))
