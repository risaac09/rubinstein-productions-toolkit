"""
Tests for the MCP server core (rpresolve.mcp): the JSON-RPC and MCP
protocol over real pipes, argument validation, stdio isolation, and the
server end to end as a subprocess with Resolve access disabled. Stdlib only;
no Resolve needed, so these also run in CI.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import json
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rpresolve.mcp import protocol, schema  # noqa: E402
from rpresolve.mcp.registry import READ, Registry, Tool  # noqa: E402
from rpresolve.mcp.stdio import FramedWriter, LineReader  # noqa: E402

SERVER = HERE.parent / "resolve_mcp.py"
OBJ = {"type": "object", "properties": {}, "additionalProperties": False}


class Harness:
    """A Server on two pipes, run in a thread; send() and recv() speak to it."""

    def __init__(self, registry):
        self.req_r, self.req_w = os.pipe()
        self.resp_r, self.resp_w = os.pipe()
        self.exit_codes = []
        self.server = protocol.Server(LineReader(self.req_r), FramedWriter(self.resp_w),
                                      registry, instructions="be careful",
                                      exit_fn=self.exit_codes.append)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.buf = b""

    def _run(self):
        try:
            self.server.serve()
        except SystemExit:
            pass

    def send(self, msg):
        data = msg if isinstance(msg, bytes) else json.dumps(msg).encode()
        os.write(self.req_w, data + b"\n")

    def recv(self, timeout=5.0):
        deadline = time.time() + timeout
        while b"\n" not in self.buf:
            if time.time() > deadline:
                raise AssertionError("no response")
            import select
            r, _, _ = select.select([self.resp_r], [], [], 0.1)
            if r:
                self.buf += os.read(self.resp_r, 65536)
        line, self.buf = self.buf.split(b"\n", 1)
        return json.loads(line)

    def nothing(self, wait=0.3):
        import select
        r, _, _ = select.select([self.resp_r], [], [], wait)
        return not r and b"\n" not in self.buf

    def close(self):
        os.close(self.req_w)
        self.thread.join(5)


def init(h, version="2025-06-18"):
    h.send({"jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": version, "capabilities": {},
                       "clientInfo": {"name": "t", "version": "1"}}})
    r = h.recv()
    h.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return r


def registry(extra=()):
    reg = Registry()
    gate = threading.Event()

    def echo(args, ctx):
        return {"summary": "echo", "args": args}

    def slow(args, ctx):
        gate.wait(5)
        return {"summary": "slow", "cancelled": ctx.cancelled()}

    def boom(args, ctx):
        raise ValueError("nope")

    def bye(args, ctx):
        sys.exit(3)

    def chatty(args, ctx):
        print("hello from a tool")
        return {"summary": "chatty"}

    def big(args, ctx):
        return {"summary": "big", "rows": ["x" * 100] * 1000}

    def typed(args, ctx):
        return {"summary": "typed", "n_type": type(args["n"]).__name__}

    reg.add(Tool("echo", "echo", {"type": "object", "properties": {
        "n": {"type": "integer", "minimum": 1, "default": 1}, "s": {"type": "string"}},
        "required": ["s"], "additionalProperties": False}, echo, annotations=READ))
    reg.add(Tool("slow", "slow", OBJ, slow))
    reg.add(Tool("boom", "boom", OBJ, boom))
    reg.add(Tool("bye", "bye", OBJ, bye))
    reg.add(Tool("chatty", "chatty", OBJ, chatty))
    reg.add(Tool("big", "big", OBJ, big))
    reg.add(Tool("typed", "typed", {"type": "object", "properties": {"n": {"type": "integer"}},
                                    "additionalProperties": False}, typed))
    return reg, gate


class TestProtocol(unittest.TestCase):
    def setUp(self):
        self.reg, self.gate = registry()
        self.h = Harness(self.reg)

    def tearDown(self):
        self.gate.set()
        try:
            self.h.close()
        except OSError:
            pass

    def test_initialize_negotiates(self):
        r = init(self.h, "2025-06-18")["result"]
        self.assertEqual(r["protocolVersion"], "2025-06-18")
        self.assertEqual(r["capabilities"], {"tools": {"listChanged": False}})
        self.assertEqual(r["instructions"], "be careful")
        self.assertIn("title", r["serverInfo"])
        self.assertTrue(self.h.nothing())  # initialized is a notification: no reply

    def test_unknown_version_gets_the_latest(self):
        r = init(self.h, "1999-01-01")["result"]
        self.assertEqual(r["protocolVersion"], protocol.SUPPORTED[0])

    def test_old_version_has_no_structured_content(self):
        init(self.h, "2024-11-05")
        self.h.send({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": "echo", "arguments": {"s": "x"}}})
        r = self.h.recv()["result"]
        self.assertNotIn("structuredContent", r)
        self.assertEqual(json.loads(r["content"][0]["text"])["args"], {"s": "x", "n": 1})

    def test_tools_list_and_call(self):
        init(self.h)
        self.h.send({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = {t["name"]: t for t in self.h.recv()["result"]["tools"]}
        self.assertEqual(set(tools), {"echo", "slow", "boom", "bye", "chatty", "big", "typed"})
        self.assertTrue(tools["echo"]["annotations"]["readOnlyHint"])
        self.h.send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                     "params": {"name": "echo", "arguments": {"s": "hi", "n": 2}}})
        r = self.h.recv()["result"]
        self.assertFalse(r["isError"])
        self.assertEqual(r["structuredContent"]["args"], {"s": "hi", "n": 2})

    def test_errors(self):
        init(self.h)
        self.h.send({"jsonrpc": "2.0", "id": 1, "method": "nope"})
        self.assertEqual(self.h.recv()["error"]["code"], protocol.METHOD_NOT_FOUND)
        self.h.send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                     "params": {"name": "missing", "arguments": {}}})
        self.assertEqual(self.h.recv()["error"]["code"], protocol.INVALID_PARAMS)
        self.h.send(b"{not json")
        r = self.h.recv()
        self.assertEqual((r["id"], r["error"]["code"]), (None, protocol.PARSE_ERROR))
        self.h.send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                     "params": {"name": "echo", "arguments": {"n": 0, "extra": 1}}})
        r = self.h.recv()["result"]
        self.assertTrue(r["isError"])
        text = r["content"][0]["text"]
        self.assertIn("missing required 's'", text)
        self.assertIn("unknown property 'extra'", text)
        self.assertIn("below the minimum", text)

    def test_tool_failures_are_results_and_the_server_lives(self):
        init(self.h)
        for rid, name in ((1, "boom"), (2, "bye")):
            self.h.send({"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                         "params": {"name": name, "arguments": {}}})
            r = self.h.recv()["result"]
            self.assertTrue(r["isError"], name)
        self.h.send({"jsonrpc": "2.0", "id": 3, "method": "ping"})
        self.assertEqual(self.h.recv(), {"jsonrpc": "2.0", "id": 3, "result": {}})

    def test_prints_are_captured_not_sent(self):
        init(self.h)
        self.h.send({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": "chatty", "arguments": {}}})
        r = self.h.recv()["result"]
        self.assertEqual(r["structuredContent"]["log"], "hello from a tool\n")

    def test_ping_answers_while_a_tool_runs(self):
        init(self.h)
        self.h.send({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": "slow", "arguments": {}}})
        time.sleep(0.2)
        self.h.send({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        self.assertEqual(self.h.recv()["id"], 2)
        self.gate.set()
        self.assertEqual(self.h.recv()["id"], 1)

    def test_cancellation(self):
        init(self.h)
        self.h.send({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": "slow", "arguments": {}}})
        time.sleep(0.2)
        # queued behind the running call, then cancelled: dropped, never run
        self.h.send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                     "params": {"name": "echo", "arguments": {"s": "x"}}})
        self.h.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                     "params": {"requestId": 2, "reason": "test"}})
        # the running call is cancelled too: it gets no response
        self.h.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                     "params": {"requestId": 1}})
        time.sleep(0.2)
        self.gate.set()
        self.h.send({"jsonrpc": "2.0", "id": 3, "method": "ping"})
        self.assertEqual(self.h.recv()["id"], 3)
        self.assertTrue(self.h.nothing(0.5))

    def test_batches_only_under_2025_03_26(self):
        init(self.h, "2025-06-18")
        self.h.send([{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
        self.assertEqual(self.h.recv()["error"]["code"], protocol.INVALID_REQUEST)

    def test_a_batch_gets_one_array_reply(self):
        init(self.h, "2025-03-26")
        self.h.send([{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                     {"jsonrpc": "2.0", "method": "notifications/initialized"},
                     {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                      "params": {"name": "echo", "arguments": {"s": "b"}}}])
        reply = self.h.recv()
        self.assertIsInstance(reply, list)
        self.assertEqual([r["id"] for r in reply], [1, 2])

    def test_oversized_results_drop_the_structured_copy(self):
        init(self.h)
        self.h.send({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": "big", "arguments": {}}})
        r = self.h.recv()["result"]
        self.assertNotIn("structuredContent", r)
        self.assertIn("[truncated", r["content"][0]["text"])
        self.assertLess(len(r["content"][0]["text"]), protocol.MAX_TEXT + 200)

    def test_whole_floats_arrive_as_integers(self):
        init(self.h)
        self.h.send({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": "typed", "arguments": {"n": 2.0}}})
        self.assertEqual(self.h.recv()["result"]["structuredContent"]["n_type"], "int")

    def test_eof_exits_zero(self):
        init(self.h)
        self.h.close()
        self.assertEqual(self.h.exit_codes, [0])


class TestSchema(unittest.TestCase):
    S = {"type": "object", "required": ["paths"], "additionalProperties": False, "properties": {
        "paths": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        "mode": {"type": "string", "enum": ["a", "b"]},
        "n": {"type": "integer", "minimum": 1, "maximum": 5},
        "name": {"type": "string", "pattern": r" \[auto\]$"}}}

    def test_valid_and_invalid(self):
        self.assertEqual(schema.validate(self.S, {"paths": ["/x"], "n": 3, "name": "T [auto]"}), [])
        errs = schema.validate(self.S, {"paths": [], "mode": "c", "n": 9, "name": "T", "z": 1})
        self.assertEqual(len(errs), 5, errs)
        self.assertTrue(schema.validate(self.S, {"paths": [""]}))
        self.assertTrue(schema.validate(self.S, {"paths": ["/x"], "n": True}))  # bool is no integer
        self.assertEqual(schema.validate(self.S, {"paths": ["/x"], "n": 2.0}), [])
        self.assertEqual(schema.with_defaults({"properties": {"k": {"default": 4}}}, {}), {"k": 4})


class TestStdioIsolation(unittest.TestCase):
    def test_noise_never_reaches_the_protocol(self):
        p = subprocess.Popen([sys.executable, str(HERE / "mcp_noisy_server.py")],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        reqs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": {"name": "noisy", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 3, "method": "ping"}]
        out, err = p.communicate(b"".join(json.dumps(r).encode() + b"\n" for r in reqs), timeout=30)
        lines = [json.loads(l) for l in out.decode().splitlines()]  # every line is JSON
        by_id = {m["id"]: m for m in lines}
        self.assertEqual(set(by_id), {1, 2, 3})  # the ping after cat still arrived
        self.assertEqual(by_id[2]["result"]["structuredContent"]["cat_read"], "")
        self.assertIn(b"native write to fd 1", err)
        self.assertIn(b"child stdout", err)
        self.assertEqual(p.returncode, 0)


class TestEndToEnd(unittest.TestCase):
    def test_server_without_resolve(self):
        env = dict(os.environ, RPRESOLVE_MCP_NO_RESOLVE="1")
        p = subprocess.Popen([sys.executable, str(SERVER)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        reqs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "resolve_status", "arguments": {}}}]
        out, err = p.communicate(b"".join(json.dumps(r).encode() + b"\n" for r in reqs), timeout=60)
        by_id = {m["id"]: m for m in (json.loads(l) for l in out.decode().splitlines())}
        self.assertEqual(p.returncode, 0, err.decode()[-500:])
        self.assertIn("resolve_status", [t["name"] for t in by_id[2]["result"]["tools"]])
        status = by_id[3]["result"]["structuredContent"]
        self.assertFalse(status["resolve"]["connected"])
        self.assertIn("server", status)


if __name__ == "__main__":
    unittest.main()
