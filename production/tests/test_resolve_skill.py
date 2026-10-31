"""
The resolve-media-lane skill names only tools and modules that exist.

Every backticked name in its "MCP tools" section must be a tool in
build_registry() (and every registry tool must be named there); every
cmd_<name> it mentions must be production/rpresolve/cmd_<name>.py.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROD = HERE.parent
sys.path.insert(0, str(PROD))

SKILL = PROD / "skills" / "resolve-media-lane" / "SKILL.md"

try:
    from rpresolve.mcp.server import build_registry
    REGISTRY = build_registry()
except ImportError:  # something absent here: skip, do not fail
    REGISTRY = None


def section(text, heading):
    m = re.search(r"^## %s\n(.*?)(?=^## |\Z)" % re.escape(heading), text, re.S | re.M)
    return m.group(1) if m else ""


class ResolveSkill(unittest.TestCase):
    def setUp(self):
        self.text = SKILL.read_text(encoding="utf-8")

    def test_frontmatter(self):
        m = re.match(r"---\nname: (\S+)\ndescription: (.+)\n---\n", self.text)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), SKILL.parent.name)

    def test_cmd_modules_exist(self):
        mods = set(re.findall(r"`(cmd_\w+)`", self.text))
        self.assertTrue(mods)
        for mod in mods:
            self.assertTrue((PROD / "rpresolve" / (mod + ".py")).is_file(), mod)

    @unittest.skipIf(REGISTRY is None, "rpresolve.mcp.server not importable here")
    def test_mcp_tools_match_registry(self):
        body = section(self.text, "MCP tools")
        self.assertTrue(body, "MCP tools section missing")
        lines = [l for l in body.splitlines() if re.match(r"(Reads|Offline|Writes)\b", l)]
        self.assertEqual(len(lines), 3)
        named = set(re.findall(r"`([a-z_]+)`", "\n".join(lines)))
        registered = {t.name for t in REGISTRY.list()}
        self.assertEqual(named - registered, set(), "named but not registered")
        self.assertEqual(registered - named, set(), "registered but not named")


if __name__ == "__main__":
    unittest.main()
