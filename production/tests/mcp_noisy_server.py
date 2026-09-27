"""A test server whose one tool makes every kind of stray output the real
server must survive: a Python print, a raw write to fd 1, a child writing
to stdout, and a child that reads stdin (cat), which would swallow the next
request if stdin were still the protocol pipe. Used by test_mcp_stdio."""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from rpresolve.mcp.stdio import isolate_stdio  # noqa: E402

IN_FD, OUT_FD = isolate_stdio()

from rpresolve.mcp.protocol import Server  # noqa: E402
from rpresolve.mcp.registry import Registry, Tool  # noqa: E402
from rpresolve.mcp.stdio import FramedWriter, LineReader, hard_exit  # noqa: E402


def noisy(args, ctx):
    print("python print")
    os.write(1, b"native write to fd 1\n")
    subprocess.run(["/bin/echo", "child stdout"])
    cat = subprocess.run(["/bin/cat"], stdout=subprocess.PIPE, timeout=5)
    return {"summary": "noisy done", "cat_read": cat.stdout.decode()}


registry = Registry()
registry.add(Tool("noisy", "makes noise", {"type": "object", "properties": {},
                                            "additionalProperties": False}, noisy))
server = Server(LineReader(IN_FD), FramedWriter(OUT_FD), registry, exit_fn=hard_exit)
try:
    server.serve()
except SystemExit as e:
    hard_exit(e.code if isinstance(e.code, int) else 1)
