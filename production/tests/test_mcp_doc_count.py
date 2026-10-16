"""
The tool count stated in resolve-mcp.md must match the registry.

The document says "There are N tools: R reads, O offline tools (...) and W
writes." This reads N and W from that sentence and compares them with
build_registry(). A write is a tool that is not read-only and takes a
project; deliver_captions changes a file only and is counted as offline, as
the document counts it.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rpresolve.mcp import server  # noqa: E402

DOC = HERE.parent / "resolve-mcp.md"
SPEC = HERE.parent / "resolve-template-spec.md"


def stated():
    text = " ".join(DOC.read_text(encoding="utf-8").split())
    m = re.search(r"There are (\d+) tools: (\d+) reads, (\d+) offline tools "
                  r"\(.*?\) and (\d+) writes\.", text)
    if not m:
        raise AssertionError("the 'There are N tools' sentence is missing from resolve-mcp.md")
    return tuple(int(x) for x in m.groups())


class TestToolCount(unittest.TestCase):
    def setUp(self):
        self.tools = server.build_registry().tools

    def test_total(self):
        self.assertEqual(stated()[0], len(self.tools))

    def test_writes(self):
        writes = [n for n, t in self.tools.items()
                  if not t.annotations["readOnlyHint"]
                  and "project" in t.input_schema.get("properties", {})]
        self.assertEqual(stated()[3], len(writes))

    def test_parts_add_up(self):
        total, reads, offline, writes = stated()
        self.assertEqual(total, reads + offline + writes)

    def test_template_spec_agrees(self):
        text = " ".join(SPEC.read_text(encoding="utf-8").split())
        m = re.search(r"serves the same library to Claude Code as (\d+) tools", text)
        self.assertIsNotNone(m, "the tool count sentence is missing from resolve-template-spec.md")
        self.assertEqual(int(m.group(1)), len(self.tools))


if __name__ == "__main__":
    unittest.main()
