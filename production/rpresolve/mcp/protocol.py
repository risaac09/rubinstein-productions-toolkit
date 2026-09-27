"""
rpresolve.mcp.protocol: JSON-RPC 2.0 and the MCP methods a tools server
needs: initialize, ping, tools/list, tools/call, and the notifications
initialized, cancelled and progress. No Resolve knowledge here.

Concurrency: a reader thread parses lines, answers ping at once, records
cancellations and queues requests; the main loop runs requests one at a
time. Resolve's scripting bridge is not known to be thread-safe, and the
project pin and UI snapshot assume nothing runs in between, so tool calls
never overlap. A request cancelled before it starts is dropped; one
cancelled while running sees ctx.cancelled() at its next safe point and
gets no response (the spec's "SHOULD NOT respond").

Errors: protocol faults are JSON-RPC errors; anything inside a tool,
argument validation included, is an isError result the model can read and
correct. Tracebacks go to stderr only.
"""

import contextlib
import io
import json
import queue
import sys
import threading
import traceback

from . import SERVER_NAME, SERVER_TITLE, __version__
from .registry import Cancelled, ToolContext
from .schema import validate, with_defaults

SUPPORTED = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
STRUCTURED_SINCE = "2025-06-18"
BATCH_VERSIONS = ("2025-03-26",)
MAX_TEXT = 60000

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL = (
    -32700, -32600, -32601, -32602, -32603)

_EOF = object()


def log(*parts):
    print("[rpresolve-mcp]", *parts, file=sys.stderr, flush=True)


class Server:
    def __init__(self, reader, writer, registry, instructions="", exit_fn=None,
                 session_factory=None, config=None):
        self.reader, self.writer, self.registry = reader, writer, registry
        self.instructions = instructions
        self.exit_fn = exit_fn
        self.session_factory = session_factory or (lambda: None)
        self.session = None
        self.config = config or {}
        self.version = SUPPORTED[0]
        self.initialized = False
        self.queue = queue.Queue()
        self.cancelled = set()
        self.running = None
        self.lock = threading.Lock()

    # -- output --------------------------------------------------------------

    def send(self, msg):
        data = json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        try:
            self.writer.write(data + b"\n")
        except (BrokenPipeError, OSError):
            log("client closed the output pipe; exiting")
            self._exit(0)

    def reply(self, rid, result):
        self.send({"jsonrpc": "2.0", "id": rid, "result": result})

    def error(self, rid, code, message):
        self.send({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})

    def _exit(self, code):
        if self.exit_fn:
            self.exit_fn(code)
        raise SystemExit(code)

    # -- input -----------------------------------------------------------------

    def read_loop(self):
        for line in self.reader:
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except ValueError as e:
                self.error(None, PARSE_ERROR, f"parse error: {e}")
                continue
            if isinstance(msg, list):
                if self.version not in BATCH_VERSIONS or not msg:
                    self.error(None, INVALID_REQUEST, "batch requests are not supported "
                               f"under protocol {self.version}")
                    continue
                for m in msg:
                    self._route(m)
                continue
            self._route(msg)
        self.queue.put(_EOF)

    def _route(self, msg):
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            self.error(msg.get("id") if isinstance(msg, dict) else None, INVALID_REQUEST,
                       "not a JSON-RPC 2.0 message")
            return
        method = msg.get("method")
        if method is None:
            return  # a response to something we never send; ignore
        if "id" not in msg:
            self._notification(method, msg.get("params") or {})
        elif method == "ping":
            self.reply(msg["id"], {})
        else:
            self.queue.put(msg)

    def _notification(self, method, params):
        if method == "notifications/initialized":
            self.initialized = True
        elif method == "notifications/cancelled":
            rid = params.get("requestId")
            with self.lock:
                self.cancelled.add(rid)
            if params.get("reason"):
                log(f"request {rid} cancelled: {params['reason']}")

    def _is_cancelled(self, rid):
        with self.lock:
            return rid in self.cancelled

    # -- main loop -------------------------------------------------------------

    def serve(self):
        t = threading.Thread(target=self.read_loop, name="mcp-reader", daemon=True)
        t.start()
        while True:
            msg = self.queue.get()
            if msg is _EOF:
                log("stdin closed; exiting")
                self._exit(0)
            rid = msg.get("id")
            if self._is_cancelled(rid):
                continue
            with self.lock:
                self.running = rid
            try:
                self.handle(msg)
            except SystemExit:
                raise
            except Exception:
                log("internal error:\n" + traceback.format_exc())
                self.error(rid, INTERNAL, "internal error; see the server log")
            finally:
                with self.lock:
                    self.running = None

    def handle(self, msg):
        rid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if not isinstance(params, dict):
            self.error(rid, INVALID_PARAMS, "params must be an object")
            return
        if method == "initialize":
            self.reply(rid, self.initialize(params))
        elif method == "tools/list":
            with_title = self.version >= STRUCTURED_SINCE
            self.reply(rid, {"tools": [t.describe(with_title) for t in self.registry.list()]})
        elif method == "tools/call":
            self.call(rid, params)
        else:
            if not self.initialized and method != "initialize":
                log(f"'{method}' before initialize")
            self.error(rid, METHOD_NOT_FOUND, f"method not found: {method}")

    def initialize(self, params):
        asked = params.get("protocolVersion")
        self.version = asked if asked in SUPPORTED else SUPPORTED[0]
        client = params.get("clientInfo") or {}
        log(f"initialize: client {client.get('name')} {client.get('version')}, "
            f"asked {asked}, using {self.version}")
        server_info = {"name": SERVER_NAME, "version": __version__}
        if self.version >= STRUCTURED_SINCE:
            server_info["title"] = SERVER_TITLE
        result = {"protocolVersion": self.version,
                  "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": server_info}
        if self.instructions:
            result["instructions"] = self.instructions
        return result

    # -- tools/call ------------------------------------------------------------

    def call(self, rid, params):
        name, args = params.get("name"), params.get("arguments")
        if not isinstance(name, str):
            self.error(rid, INVALID_PARAMS, "tools/call needs a tool name")
            return
        tool = self.registry.get(name)
        if tool is None:
            self.error(rid, INVALID_PARAMS, f"unknown tool: {name}")
            return
        args = {} if args is None else args
        problems = validate(tool.input_schema, args) if isinstance(args, dict) else \
            ["arguments: expected object"]
        if problems:
            self.reply(rid, self._result({"summary": "invalid arguments", "errors": problems},
                                         True))
            return
        token = ((params.get("_meta") or {}).get("progressToken"))

        def send_progress(done, total=None, message=None):
            if token is None:
                return
            p = {"progressToken": token, "progress": done}
            if total is not None:
                p["total"] = total
            if message:
                p["message"] = message
            self.send({"jsonrpc": "2.0", "method": "notifications/progress", "params": p})

        if self.session is None:
            self.session = self.session_factory()
        ctx = ToolContext(self.session, lambda: self._is_cancelled(rid), send_progress, self.config)
        captured = io.StringIO()
        is_error = False
        try:
            with contextlib.redirect_stdout(captured):
                result = tool.handler(with_defaults(tool.input_schema, args), ctx)
            if not isinstance(result, dict):
                result = {"summary": str(result)}
        except Cancelled:
            log(f"{name}: cancelled by the client; no response sent")
            return
        except (Exception, SystemExit) as e:  # SystemExit: never let a tool end the server
            log(f"{name} failed:\n" + traceback.format_exc())
            msg = str(e) if not isinstance(e, SystemExit) else f"exited: {e.code}"
            result, is_error = {"summary": f"{type(e).__name__}: {msg}"}, True
        printed = captured.getvalue()
        if printed:
            sys.stderr.write(printed)
            result.setdefault("log", printed[-4000:])
        if self._is_cancelled(rid):
            log(f"{name}: cancelled while running; no response sent")
            return
        self.reply(rid, self._result(result, is_error))

    def _result(self, result, is_error):
        text = json.dumps(result, ensure_ascii=False, indent=1, default=str)
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT] + ("\n... [truncated; ask for fewer items with limit/offset, "
                                      "or write the full result to a file with out]")
        out = {"content": [{"type": "text", "text": text}], "isError": bool(is_error)}
        if self.version >= STRUCTURED_SINCE and not is_error:
            out["structuredContent"] = json.loads(json.dumps(result, default=str))
        return out
