"""
rpresolve.mcp.journal: an append-only record of every real write.

One JSON line per event in $RPRESOLVE_MCP_JOURNAL, else
~/Library/Logs/rpresolve-mcp/writes.jsonl (mode 0600: it names client
media). A "started" line is written and synced before the write touches
Resolve, and a "finished" line after, so a crash in between still leaves
a record of what was attempted. A write whose "started" line cannot be
written does not happen.
"""

import json
import os
import time
import uuid


class JournalError(RuntimeError):
    """The journal could not be written; the write was not attempted."""


def path():
    return os.environ.get("RPRESOLVE_MCP_JOURNAL") or os.path.expanduser(
        "~/Library/Logs/rpresolve-mcp/writes.jsonl")


def _append(event):
    p = path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(event, default=str, sort_keys=True) + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def start(tool, project, args):
    """Record an attempted write; returns its id. Raises JournalError."""
    wid = uuid.uuid4().hex[:12]
    try:
        _append({"event": "started", "id": wid, "tool": tool, "project": project,
                 "args": args, "pid": os.getpid(),
                 "time": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    except OSError as e:
        raise JournalError(f"cannot write the journal at {path()} ({e}); nothing was written "
                           "to Resolve.")
    return wid


def finish(wid, tool, status, summary, detail=None):
    """Record the outcome. Returns None, or a warning string if the line
    could not be written (the write already happened; never raise here)."""
    try:
        _append({"event": "finished", "id": wid, "tool": tool, "status": status,
                 "summary": summary, "detail": detail, "pid": os.getpid(),
                 "time": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    except OSError as e:
        return f"the journal's finished line for {wid} could not be written: {e}"
    return None
