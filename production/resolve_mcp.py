#!/usr/bin/python3
"""
resolve_mcp.py: the Rubinstein toolkit's MCP server for DaVinci Resolve.

Register it with Claude Code (user scope):

    claude mcp add --scope user --transport stdio resolve -- \\
        /usr/bin/python3 -X faulthandler /path/to/production/resolve_mcp.py

It speaks MCP over stdin and stdout. The protocol moves to private
descriptors before any other import (see rpresolve/mcp/stdio.py): Resolve's
library and the toolkit print, and child processes inherit stdin. Nothing
connects to Resolve until a tool needs it. See production/resolve-mcp.md.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from rpresolve.mcp.stdio import isolate_stdio  # noqa: E402

IN_FD, OUT_FD = isolate_stdio()

from rpresolve.mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main(IN_FD, OUT_FD)
