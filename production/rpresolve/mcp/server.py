"""
rpresolve.mcp.server: assemble the registry and run the protocol loop.
"""

from .protocol import Server
from .registry import Registry
from .session import ResolveSession
from .stdio import FramedWriter, LineReader, hard_exit
from . import tools_read

INSTRUCTIONS = """\
Tools for DaVinci Resolve through the Rubinstein Productions toolkit.
- Call resolve_status first. Resolve is edited live by a person: never assume which project is open.
- Every write tool must name the open project exactly (project, and project_id when you have it).
- Write tools default to a dry run. Show the plan, then run for real with the plan_sha it returned.
- Additive only: nothing that existed before is modified; grades go only onto timelines whose name ends " [auto]"; renders are queued, never started.
- Output files never go inside a git repository.
- Tool results name client media, people and transcripts. Never paste them into commits, pull requests or anything public.
"""


def build_registry():
    registry = Registry()
    tools_read.register(registry)
    return registry


def main(in_fd, out_fd):
    server = Server(LineReader(in_fd), FramedWriter(out_fd), build_registry(),
                    instructions=INSTRUCTIONS, exit_fn=hard_exit,
                    session_factory=ResolveSession)
    try:
        server.serve()
    except SystemExit as e:
        hard_exit(e.code if isinstance(e.code, int) else 1)
    except BaseException:
        import traceback, sys
        traceback.print_exc(file=sys.stderr)
        hard_exit(1)
