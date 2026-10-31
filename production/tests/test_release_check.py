"""
Tests for release-check. The folder holds synthetic bytes under house names, so
nothing here needs ffmpeg.

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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import cmd_release_check, releasecheck as rc  # noqa: E402

SLUGS = ("alpha-clip", "beta-clip")
NAMES = [f"SW999_Test_0{i}_{s}_9x16.mp4" for i, s in enumerate(SLUGS, 1)]


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = Path(tmp.name).resolve()
        self.up = self.d / "upload"
        self.up.mkdir()
        self.data = {n: f"bytes of {n}".encode() for n in NAMES}
        for n, b in self.data.items():
            (self.up / n).write_bytes(b)
        self.sums()
        self.titles = {n: f"Placeholder title {i}" for i, n in enumerate(NAMES, 1)}
        self.log = self.d / "proj" / "review" / "events.jsonl"
        self.log.parent.mkdir(parents=True)
        self.log.write_text("".join(
            json.dumps({"ts": "2026-10-31T10:00:00Z", "project": "P-0001", "step": "gate",
                        "options": {}, "choice": "approve", "device": "desktop",
                        "review_copy": f"SW999_Test_0{i}_{s}_review.mp4",
                        "review_sha256": sha(b"copy%d" % i)}) + "\n"
            for i, s in enumerate(SLUGS, 1)))
        self.stubs = self.d / "stubs.jsonl"
        self.write_stubs()

    def sums(self):
        (self.up / "SHA256SUMS").write_text("".join(f"{sha(b)}  {n}\n" for n, b in self.data.items()))

    def write_stubs(self, **over):
        self.stubs.write_text("".join(
            json.dumps({"stub": True, "file": n, "check_status": "pass", "sha256": sha(b), **over}) + "\n"
            for n, b in self.data.items()))

    def run_check(self, log=True, stubs=True):
        t = self.d / "titles.tsv"
        t.write_text("file\ttitle\n" + "".join(f"{n}\t{v}\n" for n, v in self.titles.items()))
        return rc.check(self.up, rc.read_titles(t), self.log if log else None, self.stubs if stubs else None)

    def cell(self, name, col, **kw):
        rows = self.run_check(**kw)[0]
        return next(r for r in rows if r["file"] == name)["cells"][col]


class TestReleaseCheck(Case):
    def test_clean_folder_is_all_ready(self):
        rows, orphans, verdict, _ = self.run_check()
        self.assertEqual((verdict, orphans), ("READY", []))
        self.assertTrue(all(s == "PASS" for r in rows for s, _ in r["cells"].values()))

    def test_one_corrupted_byte_fails_sha(self):
        p = self.up / NAMES[0]
        p.write_bytes(p.read_bytes()[:-1] + b"X")
        self.assertEqual(self.cell(NAMES[0], "sha")[0], "FAIL")
        self.assertEqual(self.run_check()[2], "BLOCKED")

    def test_off_rule_name_fails(self):
        (self.up / "Final Cut.mp4").write_bytes(b"x")
        rows, _, verdict, _ = self.run_check()
        self.assertEqual(next(r for r in rows if r["file"] == "Final Cut.mp4")["cells"]["name"][0], "FAIL")
        self.assertEqual(verdict, "BLOCKED")

    def test_title_of_61_characters_fails_and_60_passes(self):
        self.titles[NAMES[0]] = "t" * 61
        self.assertEqual(self.cell(NAMES[0], "title")[0], "FAIL")
        self.titles[NAMES[0]] = "t" * 60
        self.assertEqual(self.cell(NAMES[0], "title")[0], "PASS")

    def test_duplicate_title_fails_both(self):
        self.titles[NAMES[1]] = self.titles[NAMES[0]].upper()
        self.assertEqual([self.cell(n, "title")[0] for n in NAMES], ["FAIL", "FAIL"])

    def test_placeholder_fails_but_lower_case_brackets_pass(self):
        self.titles[NAMES[0]] = "Read it [SUBSTACK LINK]"
        self.assertIn("placeholder", self.cell(NAMES[0], "title")[1])
        self.titles[NAMES[0]] = "Read it [aside]"
        self.assertEqual(self.cell(NAMES[0], "title")[0], "PASS")

    def test_orphan_title_row_fails(self):
        self.titles["SW999_Test_09_gone_9x16.mp4"] = "Orphan"
        rows, orphans, verdict, _ = self.run_check()
        self.assertEqual((orphans, verdict), (["SW999_Test_09_gone_9x16.mp4"], "BLOCKED"))

    def test_sums_entry_with_no_file_blocks(self):
        (self.up / NAMES[1]).unlink()
        del self.titles[NAMES[1]]
        _, orphans, verdict, _ = self.run_check()
        self.assertEqual((orphans, verdict), ([f"{NAMES[1]} (SHA256SUMS)"], "BLOCKED"))

    def test_sums_binary_and_dot_slash_forms_parse(self):
        (self.up / "SHA256SUMS").write_text("".join(
            f"{sha(b)} *./{n}\r\n" if i else f"{sha(b)}  ./{n}\r\n"
            for i, (n, b) in enumerate(self.data.items())))
        self.assertEqual(self.cell(NAMES[0], "sha")[0], "PASS")
        self.assertEqual(self.cell(NAMES[1], "sha")[0], "PASS")

    def test_other_files_are_listed_not_checked(self):
        (self.up / "extra.srt").write_text("x")
        self.assertIn("extra.srt: not checked", rc.format_table(*self.run_check()))

    def test_spaces_in_a_title_do_not_hide_a_duplicate(self):
        self.titles[NAMES[1]] = "Placeholder  title 1"
        self.assertEqual(self.cell(NAMES[0], "title")[0], "FAIL")

    def test_file_without_a_title_row_fails(self):
        del self.titles[NAMES[1]]
        self.assertEqual(self.cell(NAMES[1], "title")[0], "FAIL")

    def test_missing_approval_line_blocks(self):
        self.log.write_text("")
        self.assertEqual(self.cell(NAMES[0], "approval")[0], "BLOCK")
        self.assertEqual(self.run_check()[2], "BLOCKED")

    def test_later_hold_blocks(self):
        with open(self.log, "a") as f:
            f.write(json.dumps({"ts": "2026-10-31T11:00:00Z", "choice": "hold",
                                "review_copy": "SW999_Test_01_alpha-clip_review.mp4",
                                "review_sha256": sha(b"copy1")}) + "\n")
        self.assertEqual(self.cell(NAMES[0], "approval")[0], "BLOCK")

    def test_no_log_or_stubs_skips_without_blocking(self):
        rows, _, verdict, _ = self.run_check(log=False, stubs=False)
        self.assertEqual((rows[0]["cells"]["approval"][0], rows[0]["cells"]["stub"][0], verdict),
                         ("SKIP", "SKIP", "READY"))

    def test_stub_checks(self):
        self.write_stubs(check_status="fail")
        self.assertEqual(self.cell(NAMES[0], "stub")[0], "FAIL")
        self.write_stubs(sha256="0" * 64)
        self.assertEqual(self.cell(NAMES[0], "stub")[1], "stub sha256 differs")
        self.stubs.write_text("")
        self.assertEqual(self.cell(NAMES[0], "stub")[0], "BLOCK")

    def test_missing_sums_file_fails_sha(self):
        (self.up / "SHA256SUMS").unlink()
        self.assertEqual(self.cell(NAMES[0], "sha")[0], "FAIL")

    def test_read_only_and_no_commands_in_output(self):
        before = sorted(p.name for p in self.up.iterdir())
        result = self.run_check()
        text = rc.format_table(*result)
        self.assertEqual(sorted(p.name for p in self.up.iterdir()), before)
        self.assertNotRegex(text, r"curl|upload |post ")
        self.assertIn(result[2], text)


class TestCommand(Case):
    def run_cmd(self, *extra):
        t = self.d / "titles.tsv"
        t.write_text("".join(f"{n}\t{v}\n" for n, v in self.titles.items()))
        p = argparse.ArgumentParser()
        cmd_release_check.add_arguments(p)
        args = p.parse_args([str(self.up), "--titles", str(t), "--review-log", str(self.log),
                             "--stubs", str(self.stubs), *extra])
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_release_check.run(args)
        return code, out.getvalue(), err.getvalue()

    def test_exit_codes_and_table(self):
        code, out, _ = self.run_cmd()
        self.assertEqual(code, 0)
        self.assertTrue(out.rstrip().endswith("READY"))
        self.log.write_text("")
        code, out, _ = self.run_cmd()
        self.assertEqual(code, 1)
        self.assertTrue(out.rstrip().endswith("BLOCKED"))

    def test_out_inside_a_git_tree_is_refused(self):
        (self.d / "repo" / ".git").mkdir(parents=True)
        code, _, err = self.run_cmd("--out", str(self.d / "repo" / "t.md"))
        self.assertEqual(code, 2)
        self.assertIn("git working tree", err)

    def test_out_outside_git_writes_the_table(self):
        out = self.d / "table.md"
        self.assertEqual(self.run_cmd("--out", str(out))[0], 0)
        self.assertIn("READY", out.read_text())

    def test_binary_stub_file_exits_2(self):
        self.stubs.write_bytes(b"\xff\xfe\x00bad")
        self.assertEqual(self.run_cmd()[0], 2)

    def test_unreadable_titles_exits_2(self):
        p = argparse.ArgumentParser()
        cmd_release_check.add_arguments(p)
        args = p.parse_args([str(self.up), "--titles", str(self.d / "none.tsv")])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cmd_release_check.run(args), 2)


if __name__ == "__main__":
    unittest.main()
