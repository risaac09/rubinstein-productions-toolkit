"""
The card-folder name is <CAM>-<id6> (sidecar.CARD_DIR), never the old
date-led form. The docs under production/ and docs/ must not name the old one,
and the example they give must match the regex.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))

from rpresolve import sidecar  # noqa: E402

OLD = re.compile(r"YYYYMMDD_<CAM>_<card>|\b\d{8}_[A-Za-z0-9]+_[A-Z]\d{2}\b")


class CardFolderName(unittest.TestCase):
    def test_old_pattern_is_gone(self):
        hits = []
        for top in ("production", "docs"):
            for p in (ROOT / top).rglob("*.md"):
                for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                    if OLD.search(line):
                        hits.append("%s:%d" % (p.relative_to(ROOT), n))
        self.assertEqual(hits, [])

    def test_documented_example_matches_the_regex(self):
        text = (ROOT / "production" / "capture-standard.md").read_text(encoding="utf-8")
        m = re.search(r"for example `([^`]+)`", text[text.index("## Cards and file names"):])
        self.assertTrue(m)
        self.assertTrue(sidecar.CARD_DIR.match(m.group(1)), m.group(1))


if __name__ == "__main__":
    unittest.main()
