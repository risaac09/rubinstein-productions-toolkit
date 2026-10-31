"""capture-readout-gaps.md covers every capture-brief setting and cites only
functions that exist in measure.py and perceive.py. Text and AST only: no
numpy import."""

import ast
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PROD = os.path.dirname(HERE)
BRIEF = os.path.join(PROD, "capture-brief.md")
GAPS = os.path.join(PROD, "capture-readout-gaps.md")
MODULES = ("measure", "perceive")
LIST_START = re.compile(r"^(- |\d+\. )")
MARKER = re.compile(r"<!-- setting-ids: (.*) -->$")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def brief_blocks():
    """[(groups, rows)] per `setting-ids` marker: groups are the comma
    separated ids (a `+` joins several settings held by one list line), rows
    the list lines or table body rows that follow the marker."""
    lines = read(BRIEF).splitlines()
    out = []
    for i, line in enumerate(lines):
        m = MARKER.match(line)
        if not m:
            continue
        rows, j = [], i + 1
        while j < len(lines) and (LIST_START.match(lines[j]) or lines[j].startswith("|")):
            if not lines[j].startswith(("| Item", "|---")):
                rows.append(lines[j])
            j += 1
        out.append(([g.strip() for g in m.group(1).split(",")], rows))
    return out


def gap_rows():
    body = read(GAPS).split("<!-- readout-gaps -->")[1].split("<!-- /readout-gaps -->")[0]
    rows = {}
    for line in body.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0] not in ("Id", "---"):
            rows[cells[0]] = cells
    return rows


def defined(module):
    tree = ast.parse(read(os.path.join(PROD, "rpresolve", module + ".py")))
    return {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}


class CaptureReadoutGaps(unittest.TestCase):
    def test_every_brief_setting_has_a_row(self):
        blocks = brief_blocks()
        self.assertGreaterEqual(len(blocks), 7)
        ids = {s for groups, _ in blocks for g in groups for s in g.split("+")}
        rows = gap_rows()
        self.assertEqual(sorted(ids - set(rows)), [], "settings in the brief with no row")
        self.assertEqual(sorted(set(rows) - ids), [], "rows for settings the brief does not list")

    def test_markers_match_the_lists_they_precede(self):
        for groups, rows in brief_blocks():
            if groups == ["monitoring-lut"]:
                continue  # prose line under each camera table, not a list
            self.assertEqual(len(rows), len(groups), f"marker {groups} vs {len(rows)} items")

    def test_verdicts_and_citations(self):
        funcs = {m: defined(m) for m in MODULES}
        for sid, (_, _, verdict, note) in gap_rows().items():
            self.assertIn(verdict, ("yes", "partly", "no"), sid)
            cited = re.findall(r"`(measure|perceive)\.(\w+)`", note)
            if verdict != "no":
                self.assertTrue(cited, f"{sid}: a {verdict} must cite a function")
            for mod, fn in cited:
                self.assertIn(fn, funcs[mod], f"{sid}: {mod}.{fn} does not exist")

    def test_yes_rows_still_read_what_they_claim(self):
        # The two confirmed settings rest on perceive.read recording the frame size.
        tree = ast.parse(read(os.path.join(PROD, "rpresolve", "perceive.py")))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "read")
        text = ast.unparse(fn)
        self.assertIn("'width': info['width']", text)
        self.assertIn("'height': info['height']", text)
        self.assertEqual([k for k, v in gap_rows().items() if v[2] == "yes"],
                         ["resolution", "landscape-framing"])

    def test_count_line_matches_table(self):
        rows = gap_rows()
        tally = {v: sum(1 for r in rows.values() if r[2] == v) for v in ("yes", "partly", "no")}
        want = (f"Count: {len(rows)} settings. {tally['yes']} confirmed, "
                f"{tally['partly']} partly, {tally['no']} not.")
        self.assertIn(want, read(GAPS))


if __name__ == "__main__":
    unittest.main()
