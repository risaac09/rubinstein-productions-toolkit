"""
Tests that production/platform-specs.md has a row for every destination key in
production/resolve-config.json, and none for a key the config lacks.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "production" / "resolve-config.json"
DOC = ROOT / "production" / "platform-specs.md"


def config_keys():
    return set(json.loads(CONFIG.read_text())["destinations"])


def table_keys():
    """Keys in the second column of the table under '## The table'."""
    text = DOC.read_text()
    section = text.split("## The table", 1)[1].split("\n## ", 1)[0]
    keys = set()
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) > 1:
            m = re.fullmatch(r"`([a-z0-9_]+)`", cells[1])
            if m:
                keys.add(m.group(1))
    return keys


class TestPlatformSpecsRows(unittest.TestCase):
    def test_parsers_find_something(self):
        self.assertTrue(config_keys())
        self.assertTrue(table_keys())

    def test_every_config_key_has_a_row(self):
        missing = sorted(config_keys() - table_keys())
        self.assertEqual(missing, [], "destination keys with no row in platform-specs.md")

    def test_no_row_for_an_unknown_key(self):
        extra = sorted(table_keys() - config_keys())
        self.assertEqual(extra, [], "table rows naming keys absent from resolve-config.json")


if __name__ == "__main__":
    unittest.main()
