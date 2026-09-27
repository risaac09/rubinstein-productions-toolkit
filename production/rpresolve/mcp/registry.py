"""
rpresolve.mcp.registry: tools and the context a handler runs in.

A Tool is a name, a description, an input JSON Schema, MCP annotations and
a handler(args, ctx) returning a dict. Handlers raise to fail; the protocol
turns any exception into an isError result.
"""

READ = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
        "openWorldHint": False}
WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False,
         "openWorldHint": False}


class Cancelled(Exception):
    """The client cancelled the request; stop at the next safe point."""


class Tool:
    def __init__(self, name, description, input_schema, handler, title=None,
                 annotations=None):
        self.name = name
        self.title = title or name.replace("_", " ")
        self.description = description
        self.input_schema = input_schema
        self.handler = handler
        self.annotations = dict(annotations or READ)

    def describe(self, with_title=True):
        d = {"name": self.name, "description": self.description,
             "inputSchema": self.input_schema, "annotations": dict(self.annotations)}
        if with_title:
            d["title"] = self.title
            d["annotations"]["title"] = self.title
        return d


class Registry:
    def __init__(self):
        self.tools = {}

    def add(self, tool):
        if tool.name in self.tools:
            raise ValueError(f"tool '{tool.name}' registered twice")
        self.tools[tool.name] = tool
        return tool

    def get(self, name):
        return self.tools.get(name)

    def list(self):
        return list(self.tools.values())


class ToolContext:
    """What a handler may use: the Resolve session, a cancellation check
    (raises Cancelled), and progress reporting."""

    def __init__(self, session=None, is_cancelled=None, send_progress=None, config=None):
        self.session = session
        self._is_cancelled = is_cancelled or (lambda: False)
        self._send_progress = send_progress
        self.config = config or {}

    def cancelled(self):
        return self._is_cancelled()

    def check_cancel(self):
        """Raise Cancelled if the client has cancelled; pass as check_cancel."""
        if self._is_cancelled():
            raise Cancelled("cancelled by the client")

    def progress(self, done, total=None, message=None):
        if self._send_progress:
            self._send_progress(done, total, message)
