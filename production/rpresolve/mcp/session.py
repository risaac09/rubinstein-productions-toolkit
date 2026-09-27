"""
rpresolve.mcp.session: the Resolve connection, made lazily and probed on
reuse. The server starts in every Claude Code session, so it never
connects at startup; the first tool that needs Resolve does.

A fresh project manager and project are read on every call: Isaac may
switch projects between calls, and a cached project object would pin the
wrong one. Only the top-level resolve object is kept, and it is probed
with GetVersionString() before reuse; a dead one is dropped and replaced.
RPRESOLVE_MCP_NO_RESOLVE=1 makes every connect fail (tests).
"""

import os

from .. import api


class ResolveSession:
    def __init__(self, connect=None):
        self._connect = connect or api.connect
        self.resolve = None

    def get(self):
        """The live resolve object, or raise api.ResolveUnavailable."""
        if os.environ.get("RPRESOLVE_MCP_NO_RESOLVE") == "1":
            raise api.ResolveUnavailable("Resolve access is disabled (RPRESOLVE_MCP_NO_RESOLVE=1).")
        if self.resolve is not None:
            try:
                if self.resolve.GetVersionString():
                    return self.resolve
            except Exception:
                pass
            self.resolve = None
        self.resolve = self._connect()
        return self.resolve

    def project(self):
        """(resolve, pm, project) for the open project."""
        resolve = self.get()
        pm, project = api.current_project(resolve)
        return resolve, pm, project

    def pin_for_write(self, name, unique_id=None):
        """(resolve, pm, project, pin) for a write naming its project."""
        resolve = self.get()
        pm, project, pin = api.pin_for_write(resolve, name, unique_id)
        return resolve, pm, project, pin
