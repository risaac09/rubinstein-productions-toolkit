#!/usr/bin/env python3
"""
resolve_workflow.py: the read-only and offline commands for DaVinci Resolve work.

This script does not write to Resolve. Writes go through the MCP server,
production/resolve_mcp.py, which imports media, builds [auto] timelines, grades
them, queues render jobs, makes captions and trim-review markers, and stacks
dual-system sound: a dry run by default, a plan_sha for the real run, and a
journal line for every write. It creates no project and starts no render:
projects are created by hand in Resolve, and a person starts each render on the
Deliver page. See production/resolve-mcp.md, which says what replaced each
retired command.

Usage:
    python3 resolve_workflow.py <command> [options]
    python3 resolve_workflow.py --help          (the commands, one line each)

    Imports the sibling rpresolve/ package, so run it from production/ or
    through a symlink; a copy of this file alone will not start.

Commands: each is one module, rpresolve/cmd_<name>.py, whose docstring says what
it does; from production/ it also runs alone as python3 -m rpresolve.cmd_<name>.
rpresolve/cli.py holds the parser and says how to add a command. Four commands
read Resolve (Resolve Studio must be running; nothing is changed): list-projects,
list-timelines, list-render-formats and info. Every other one never connects.

Retired: the commands that wrote to Resolve. Running one exits 2 and says what
to use instead: the MCP tool, or what to do by hand in Resolve when no tool does
it (RETIRED in rpresolve/cli.py; the same table is in production/resolve-mcp.md).

Config:
    The commands that read delivery destinations take them from
    resolve-config.json (next to this script). An overlay kept outside any
    repository goes in with --config <path>.

Environment setup and Resolve's API limits: production/resolve-mcp.md.
"""

import sys
import os

# rpresolve is a sibling package; put this file's real directory on the
# path so a symlinked or differently-cwd'd run still finds it. A lone copy
# of this file elsewhere (e.g. Resolve's Scripts menu) still needs the
# rpresolve/ directory copied next to it.
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
try:
    from rpresolve import api as rpapi  # noqa: E402
    from rpresolve import cli  # noqa: E402
except ImportError as e:
    sys.exit(f"ERROR: cannot import rpresolve ({e}). Keep resolve_workflow.py next to its "
             "rpresolve/ directory (production/ in the toolkit), or symlink the script.")

main = cli.main

# Each command's run() under its old name here (cmd_measure, cmd_qc, ...), for
# code that imports this script.
for _m in cli.commands():
    globals()["cmd_" + _m.NAME.replace("-", "_")] = _m.run


if __name__ == "__main__":
    rpapi.exit_clean(main())
