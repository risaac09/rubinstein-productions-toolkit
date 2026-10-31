"""
Tests for the deliver-check approval row (--review-log). The check result is
stubbed as in test_deliver_ledger.py, so none of these need ffmpeg.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import argparse
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import cli, cmd_deliver_check, delivercheck as dc, reviewlog  # noqa: E402

NAME = "SW999_Test_01_example-clip_9x16.mp4"
COPY = "SW999_Test_01_example-clip_review.mp4"


def stub(path):
    return {"file": str(path), "destination": "youtube_16x9", "checks": [],
            "counts": {"PASS": 9, "FAIL": 0, "SKIP": 2}, "status": "pass"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = Path(tmp.name).resolve()
        self.f = self.d / "out" / NAME
        self.f.parent.mkdir()
        self.f.write_bytes(b"deliverable")
        self.copy = self.d / "review-copy" / COPY
        self.copy.parent.mkdir()
        self.copy.write_bytes(b"review copy")
        self.log = self.d / "proj" / "review" / "events.jsonl"

    def line(self, choice="approve", copy=COPY, review_sha=None, ts="2026-10-31T10:00:00Z"):
        self.log.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log, "a") as fh:
            fh.write(json.dumps({"ts": ts, "project": "P-0001", "step": "gate", "options": {},
                                 "choice": choice, "device": "desktop", "review_copy": copy,
                                 "review_sha256": review_sha or sha(b"review copy")}) + "\n")

    def run_cmd(self, *extra):
        p = argparse.ArgumentParser()
        cmd_deliver_check.add_arguments(p)
        p.set_defaults(config=None)
        args = p.parse_args([str(self.f), "--dest", "youtube_16x9", *extra])
        out = io.StringIO()
        with mock.patch.object(dc, "check", return_value=stub(self.f)), contextlib.redirect_stdout(out):
            code = cmd_deliver_check.run(args)
        return code, out.getvalue()

    def row(self):
        return dc.approval_row(str(self.f), str(self.log), sha(b"deliverable"))[0]


class TestApprovalRow(Case):
    def test_matching_line_passes(self):
        self.line()
        self.assertEqual(self.row()["status"], "PASS")

    def test_go_line_passes(self):
        self.line("go")
        self.assertEqual(self.row()["status"], "PASS")

    def test_sha_mismatch_with_copy_beside_the_file_warns(self):
        self.line(review_sha="0" * 64)
        (self.f.parent / COPY).write_bytes(b"review copy")
        r = self.row()
        self.assertEqual(r["status"], "WARN")
        self.assertIn("sha mismatch", r["found"])

    def test_copy_beside_the_file_with_the_logged_sha_passes(self):
        self.line()
        (self.f.parent / COPY).write_bytes(b"review copy")
        self.assertEqual(self.row()["status"], "PASS")

    def test_no_line_for_the_stem_warns(self):
        self.line(copy="SW999_Test_02_other_review.mp4")
        self.assertEqual(self.row()["status"], "WARN")

    def test_a_longer_slug_is_another_cut(self):
        self.line(copy="SW999_Test_01_example-clip-v2_review.mp4")
        self.assertEqual(self.row()["status"], "WARN")

    def test_a_newer_hold_on_another_copy_withdraws_the_approval(self):
        self.line()
        self.line("hold", copy="SW999_Test_01_example-clip_review2.mp4", review_sha="1" * 64,
                  ts="2026-10-31T11:00:00Z")
        self.assertEqual(self.row()["status"], "WARN")

    def test_line_without_ts_does_not_crash(self):
        self.line(ts="")
        self.assertEqual(self.row()["status"], "PASS")

    def test_later_hold_withdraws_the_approval(self):
        self.line()
        self.line("hold", ts="2026-10-31T11:00:00Z")
        r = self.row()
        self.assertEqual(r["status"], "WARN")
        self.assertIn("hold", r["found"])

    def test_clip_naming_its_own_sha_passes(self):
        self.line(copy="whatever.mp4", review_sha=sha(b"deliverable"))
        self.assertEqual(self.row()["status"], "PASS")

    def test_missing_log_warns_and_no_flag_skips(self):
        self.assertEqual(dc.approval_row(str(self.f), str(self.log))[0]["status"], "WARN")
        self.assertEqual(dc.approval_row(str(self.f), None)[0]["status"], "SKIP")


class TestCommand(Case):
    def test_exit_code_is_unchanged_in_every_case(self):
        code, out = self.run_cmd()
        self.assertEqual(code, 0)
        self.assertNotIn("approval", out)
        code, out = self.run_cmd("--review-log", str(self.log))  # no log yet
        self.assertEqual(code, 0)
        self.assertIn("WARN  approval", out)
        self.line()
        code, out = self.run_cmd("--review-log", str(self.log))
        self.assertEqual(code, 0)
        self.assertIn("PASS  approval", out)

    def test_ledger_row_carries_the_approval_reference(self):
        self.line()
        out = self.d / "row.jsonl"
        self.run_cmd("--review-log", str(self.log), "--ledger-row", str(out))
        self.assertIn(sha(b"review copy"), json.loads(out.read_text())["approval"])

    def test_ledger_approval_stays_blank_without_a_match(self):
        out = self.d / "row.jsonl"
        self.run_cmd("--review-log", str(self.log), "--ledger-row", str(out))
        self.assertEqual(json.loads(out.read_text())["approval"], "")

    def test_ledger_cannot_overwrite_the_review_log(self):
        self.line()
        before = self.log.read_bytes()
        with mock.patch("sys.stderr"):
            code, _ = self.run_cmd("--review-log", str(self.log), "--ledger-row", str(self.log))
        self.assertEqual(code, 1)
        self.assertEqual(self.log.read_bytes(), before)


class TestReviewCopy(Case):
    def test_append_records_the_copy_name_and_sha(self):
        line = reviewlog.append(str(self.d / "proj"), "P-0001", "gate", "desktop", "approve",
                                review_copy=str(self.copy))
        self.assertEqual((line["review_copy"], line["review_sha256"]), (COPY, sha(b"review copy")))
        self.assertEqual(self.row()["status"], "PASS")

    def test_review_copy_must_be_a_real_file(self):
        with self.assertRaises(reviewlog.Refused):
            reviewlog.append(str(self.d / "proj"), "P-0001", "gate", "desktop", "approve",
                             review_copy=str(self.d / "nope.mp4"))

    def test_hold_written_by_the_tool_withdraws_the_approval(self):
        for choice in ("approve", "hold"):
            reviewlog.append(str(self.d / "proj"), "P-0001", "gate", "desktop", choice,
                             review_copy=str(self.copy))
        self.assertEqual(self.row()["status"], "WARN")

    def test_cli_flag_logs_the_copy(self):
        code = cli.main(["review-log", "--dir", str(self.d / "proj"), "--project", "P-0001", "--step", "gate",
                         "--device", "desktop", "--choice", "approve", "--review-copy", str(self.copy)])
        self.assertEqual(code, 0)
        self.assertEqual(self.row()["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
