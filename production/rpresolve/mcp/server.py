"""
rpresolve.mcp.server: assemble the registry and run the protocol loop.
"""

from .protocol import Server
from .registry import Registry
from .session import ResolveSession
from .stdio import FramedWriter, LineReader, hard_exit
from . import tools_offline, tools_read, tools_write

INSTRUCTIONS = """\
Tools for DaVinci Resolve through the Rubinstein Productions toolkit.
- detect, survey, measure, endcheck, selects, reframe_plan, deliver_check, deliver_captions, sync_measure and trim_review never connect to Resolve; paths must be absolute.
- Before any Resolve tool, call resolve_status. Resolve is edited live by a person: never assume which project is open.
- Every write tool must name the open project exactly (project, and project_id when you have it).
- Write tools default to a dry run. Show the plan, then run for real with the plan_sha it returned.
- Additive only: nothing that existed before is modified; grades go only onto timelines whose name ends " [auto]"; renders are queued, never started.
- Deliverables: queue_render with a destination names the file by the house rule. After Isaac renders it: deliver_captions for a sidecar destination (Resolve's .ttml to a zero-based .srt), the loudness fix, then deliver_check.
- Captions: create_captions transcribes an [auto] timeline with line lengths for its shape; queue a captioned destination after it.
- Dual-system sound: sync_measure first; sync stacks the pair on a new [auto] timeline at that offset. Drift is reported, never corrected; a multicam clip stays a hand step.
- Reframes: reframe_plan writes a copy of the manifest with a crop per span, checked against the face on sampled frames; cut builds the 9:16 and 1:1 versions from that copy (aspects). A version with no crop is named unreframed and not built.
- Project setup: a person makes the project in Resolve; set_color_management then sets its colour science (a fresh, empty project only), and ingest tags the media.
- Ordered timelines: timeline_from_clips builds a new [auto] timeline from the clips in a bin, or named clips, in name, path or given order; picture only, every item read back.
- Trim review proposes and deletes nothing: trim_review writes the TSV, trim_review_markers marks an [auto] timeline.
- Output files never go inside a git repository.
- Tool results name client media, people and transcripts. Never paste them into commits, pull requests or anything public.
"""


def build_registry():
    registry = Registry()
    tools_read.register(registry)
    tools_offline.register(registry)
    tools_write.register(registry)
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
