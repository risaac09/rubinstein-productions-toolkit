"""
Tests for the deliver-check release-ledger row stub (--ledger-row). The
check result is stubbed, so none of these need ffmpeg.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import argparse
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import cmd_deliver_check, delivercheck as dc  # noqa: E402

NAME = "SW001_Guest_01_example-clip_16x9.mp4"
REPO = Path(__file__).resolve().parent.parent


def stub(path):
    return {"file": str(path), "destination": "youtube_16x9", "checks": [],
            "counts": {"PASS": 9, "FAIL": 0, "SKIP": 2}, "status": "pass"}


def run_cmd(file, ledger, *extra):
    p = argparse.ArgumentParser()
    cmd_deliver_check.add_arguments(p)
    p.set_defaults(config=None)
    args = p.parse_args([str(file), "--dest", "youtube_16x9", "--ledger-row", str(ledger), *extra])
    with mock.patch.object(dc, "check", return_value=stub(file)):
        return cmd_deliver_check.run(args)


class TestLedgerRow(unittest.TestCase):
    def test_house_name_is_the_key(self):
        row = dc.ledger_row(stub("/x/" + NAME), sha256="ab", now="T")
        self.assertEqual(row["key"], NAME)
        self.assertIsNone(row["key_reason"])
        self.assertEqual((row["check_status"], row["sha256"], row["checked_at"]),
                         ("pass", "ab", "T"))
        for k in dc.LEDGER_BLANKS:
            self.assertEqual(row[k], "")
        self.assertTrue(row["stub"])

    def test_other_name_gets_null_key_and_reason(self):
        row = dc.ledger_row(stub("/x/final_v2.mp4"))
        self.assertIsNone(row["key"])
        self.assertIn("final_v2.mp4", row["key_reason"])

    def test_line_is_one_json_object(self):
        line = dc.ledger_line(dc.ledger_row(stub("/x/" + NAME)))
        self.assertTrue(line.endswith("\n"))
        self.assertEqual(json.loads(line)["key"], NAME)


class TestLedgerCommand(unittest.TestCase):
    def test_writes_row_with_sha256(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / NAME
            f.write_bytes(b"synthetic")
            out = Path(d) / "row.jsonl"
            with mock.patch("sys.stdout"):
                run_cmd(f, out)
            row = json.loads(out.read_text())
            self.assertEqual(row["key"], NAME)
            self.assertEqual(row["sha256"], hashlib.sha256(b"synthetic").hexdigest())

    def test_ledger_path_that_is_the_checked_file_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / NAME
            f.write_bytes(b"master")
            link = Path(d) / "link.mp4"
            link.symlink_to(f)
            for ledger in (f, link):
                with mock.patch("sys.stderr"), mock.patch("sys.stdout"):
                    self.assertEqual(run_cmd(f, ledger), 1)
                self.assertEqual(f.read_bytes(), b"master")
            state = Path(d) / "state.json"
            state.write_text("{}")
            with mock.patch("sys.stderr"), mock.patch("sys.stdout"):
                self.assertEqual(run_cmd(f, state, "--audio-state", str(state)), 1)
            self.assertEqual(state.read_text(), "{}")

    def test_path_inside_git_tree_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / NAME
            f.write_bytes(b"x")
            out = REPO / "row.jsonl"
            with mock.patch("sys.stderr"):
                self.assertEqual(run_cmd(f, out), 1)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
