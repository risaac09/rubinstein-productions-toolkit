"""
rpresolve.mcp.stdio: keep the protocol stream clean.

MCP's stdio transport is stdin and stdout, and three things here would
corrupt it: Resolve's native library and the toolkit's own functions print
to stdout, and child processes (ffmpeg, ffprobe, exiftool) inherit stdin
and can read the next request off it. isolate_stdio() moves the protocol to
private descriptors before anything else is imported: fd 1 then points at
stderr, and fd 0 at /dev/null.
"""

import os
import sys
import threading


def isolate_stdio():
    """Return (in_fd, out_fd), private descriptors for the protocol. After
    this call fd 0 is /dev/null and fd 1 is a copy of stderr, so any stray
    write lands in the log and no child can read a request."""
    try:
        sys.stdout.flush()
    except Exception:
        pass
    try:
        out_fd = os.dup(1)
        in_fd = os.dup(0)
    except OSError as e:
        sys.stderr.write(f"rpresolve MCP server: stdin and stdout must be open pipes "
                         f"from the MCP client ({e}); exiting.\n")
        hard_exit(2)
    os.dup2(2, 1)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    return in_fd, out_fd


class FramedWriter:
    """Writes one message per line to a descriptor, whole, under a lock
    (the reader thread answers ping while a tool runs)."""

    def __init__(self, fd):
        self.fd = fd
        self.lock = threading.Lock()

    def write(self, data):
        with self.lock:
            view = memoryview(data)
            while view:
                n = os.write(self.fd, view)
                view = view[n:]


class LineReader:
    """Yields complete lines (bytes, without the newline) from a descriptor
    until end of file."""

    def __init__(self, fd, chunk=65536):
        self.fd, self.chunk = fd, chunk

    def __iter__(self):
        buf = b""
        while True:
            data = os.read(self.fd, self.chunk)
            if not data:
                if buf.strip():
                    yield buf
                return
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                yield line


def hard_exit(code=0):
    """Flush and leave via os._exit: the Resolve client can segfault while
    the interpreter shuts down, which would eat the exit status."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(int(code or 0))
