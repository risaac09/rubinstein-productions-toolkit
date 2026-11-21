"""
Tests for rpresolve.review_notes: marker files and ledgers made here in temp
folders. No Resolve, no media.

Run: /usr/bin/python3 -m unittest discover production/tests -p test_review_notes.py -v
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROD = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROD))

from rpresolve import review_notes as rn

HEADER = "frame\ttimecode\tduration_frames\tname\tcolour\tnote\tfps"
LEDGER_HEADER = "note_id\tround\tcut\ttimeline_frame\ttimecode\ttext\tstatus\tresolved_in\treason"


def markers(*rows, header=HEADER):
    return "\n".join([header, *rows]) + "\n"


R1 = ("48\t00:00:02:00\t1\tstory:logo too early\tRed\tmove after the line\t24/1",
      "240\t00:00:10:00\t1\tsnd:music loud\tBlue\t\t24/1")


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.dir = Path(self._t.name)
        self.addCleanup(self._t.cleanup)
        self.ledger = self.dir / "ledger.tsv"

    def mk(self, name, text):
        p = self.dir / name
        p.write_text(text, encoding="utf-8")
        return str(p)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = rn.main([str(a) for a in argv])
        return code, out.getvalue(), err.getvalue()

    def add(self, mfile, cut="show_v01"):
        return self.run_cli("add", "--markers", mfile, "--cut", cut, "--ledger", self.ledger)

    def lines(self):
        return self.ledger.read_text(encoding="utf-8").splitlines()

    def seed(self):
        self.assertEqual(self.add(self.mk("m1.tsv", markers(*R1)))[0], 0)


class TestAdd(Base):
    def test_first_add_numbers_notes_and_round(self):
        code, out, _ = self.add(self.mk("m1.tsv", markers(*R1)))
        self.assertEqual(code, 0)
        self.assertIn("2 notes", out)
        rows = [l.split("\t") for l in self.lines()]
        self.assertEqual(rows[0], LEDGER_HEADER.split("\t"))
        self.assertEqual(rows[1], ["N-001", "1", "show_v01", "48", "00:00:02:00",
                                   "story:logo too early | move after the line", "open", "", ""])
        self.assertEqual(rows[2][0], "N-002")
        self.assertEqual(rows[2][5], "snd:music loud")

    def test_second_round_continues_ids_and_round(self):
        self.seed()
        m2 = self.mk("m2.tsv", markers("96\t00:00:04:00\t1\tstory:trim\tRed\t\t24/1"))
        self.assertEqual(self.add(m2, cut="show_v02")[0], 0)
        last = self.lines()[-1].split("\t")
        self.assertEqual(last[:3], ["N-003", "2", "show_v02"])

    def test_ids_never_reused_after_resolve(self):
        self.seed()
        self.run_cli("resolve", "--ledger", self.ledger, "--note", "N-002", "--in", "show_v02")
        self.add(self.mk("m2.tsv", markers("96\t00:00:04:00\t1\tstory:x\tRed\t\t24/1")), cut="show_v02")
        self.assertEqual(self.lines()[-1].split("\t")[0], "N-003")

    def test_same_file_twice_refuses_and_leaves_ledger(self):
        m = self.mk("m1.tsv", markers(*R1))
        self.add(m)
        before = self.ledger.read_bytes()
        code, _, err = self.add(m)
        self.assertEqual(code, 1)
        self.assertIn("already in the ledger", err)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_same_markers_against_new_cut_is_allowed(self):
        self.seed()
        self.assertEqual(self.add(self.mk("m1b.tsv", markers(*R1)), cut="show_v02")[0], 0)

    def test_header_only_is_valid_zero_notes_no_round(self):
        code, out, _ = self.add(self.mk("m.tsv", markers()))
        self.assertEqual(code, 0)
        self.assertIn("0 notes", out)
        self.assertFalse(self.ledger.exists())
        self.seed()
        self.add(self.mk("m0.tsv", markers()), cut="show_v02")
        self.add(self.mk("m2.tsv", markers("96\t00:00:04:00\t1\tstory:x\tRed\t\t24/1")), cut="show_v02")
        self.assertEqual(self.lines()[-1].split("\t")[1], "2")

    def test_empty_file(self):
        code, _, err = self.add(self.mk("m.tsv", ""))
        self.assertEqual(code, 1)
        self.assertIn("empty", err)
        self.assertFalse(self.ledger.exists())

    def test_missing_column(self):
        h = "frame\ttimecode\tduration_frames\tname\tcolour\tnote"
        code, _, err = self.add(self.mk("m.tsv", markers("1\t00:00:00:01\t1\ta\tRed\t", header=h)))
        self.assertEqual(code, 1)
        self.assertIn("fps", err)
        self.assertNotIn("Traceback", err)

    def test_row_with_extra_tab_in_text(self):
        code, _, err = self.add(self.mk("m.tsv", markers("1\t00:00:00:01\t1\ta\tRed\tbad\ttab\t24/1")))
        self.assertEqual(code, 1)
        self.assertIn("line 2", err)

    def test_newline_inside_note_splits_row_and_is_refused(self):
        code, _, err = self.add(self.mk("m.tsv", markers("1\t00:00:00:01\t1\ta\tRed\tfirst\nsecond\t24/1")))
        self.assertEqual(code, 1)
        self.assertFalse(self.ledger.exists())

    def test_control_character_in_note(self):
        code, _, err = self.add(self.mk("m.tsv", markers("1\t00:00:00:01\t1\ta\tRed\tx\x0by\t24/1")))
        self.assertEqual(code, 1)
        self.assertIn("control", err)

    def test_bad_frame_and_timecode_disagreement(self):
        for row in ("x\t00:00:00:01\t1\ta\tRed\t\t24/1", "-1\t00:00:00:01\t1\ta\tRed\t\t24/1",
                    "25\t00:00:00:01\t1\ta\tRed\t\t24/1", "1\tnope\t1\ta\tRed\t\t24/1",
                    "1\t00:00:00:01\t1\ta\tRed\t\t0/0"):
            code, _, err = self.add(self.mk("m.tsv", markers(row)))
            self.assertEqual(code, 1, row)
            self.assertNotIn("Traceback", err)
        self.assertFalse(self.ledger.exists())

    def test_ntsc_rate_uses_nominal_frames(self):
        row = "48\t00:00:02:00\t1\ta\tRed\t\t24000/1001"
        self.assertEqual(self.add(self.mk("m.tsv", markers(row)))[0], 0)

    def test_empty_name_refused(self):
        code, _, _ = self.add(self.mk("m.tsv", markers("1\t00:00:00:01\t1\t\tRed\t\t24/1")))
        self.assertEqual(code, 1)

    def test_bad_cut_name(self):
        m = self.mk("m.tsv", markers(*R1))
        for cut in ("", "a\tb", "a\nb"):
            self.assertEqual(self.add(m, cut=cut)[0], 1)

    def test_missing_marker_file_and_bad_utf8(self):
        self.assertEqual(self.add(str(self.dir / "nope.tsv"))[0], 1)
        p = self.dir / "bin.tsv"
        p.write_bytes(b"\xff\xfe\x00bad")
        code, _, err = self.add(str(p))
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)

    def test_ledger_in_git_tree_refused(self):
        (self.dir / "repo" / ".git").mkdir(parents=True)
        led = self.dir / "repo" / "ledger.tsv"
        code, _, err = self.run_cli("add", "--markers", self.mk("m.tsv", markers(*R1)),
                                    "--cut", "c", "--ledger", led)
        self.assertEqual(code, 1)
        self.assertIn("git working tree", err)
        self.assertFalse(led.exists())

    def test_ledger_is_directory(self):
        d = self.dir / "adir"
        d.mkdir()
        code, _, err = self.run_cli("add", "--markers", self.mk("m.tsv", markers(*R1)),
                                    "--cut", "c", "--ledger", d)
        self.assertEqual(code, 1)
        self.assertIn("directory", err)

    def test_no_temp_files_left(self):
        self.seed()
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["ledger.tsv", "m1.tsv"])


class TestResolve(Base):
    def res(self, *a):
        return self.run_cli("resolve", "--ledger", self.ledger, *a)

    def test_done(self):
        self.seed()
        code, out, _ = self.res("--note", "N-001", "--in", "show_v02")
        self.assertEqual(code, 0)
        row = self.lines()[1].split("\t")
        self.assertEqual(row[6:], ["done", "show_v02", ""])
        self.assertEqual(self.lines()[2].split("\t")[6], "open")

    def test_wontfix_needs_reason(self):
        self.seed()
        self.assertEqual(self.res("--note", "N-001", "--wontfix")[0], 2)
        code, _, _ = self.res("--note", "N-001", "--wontfix", "--reason", "out of scope")
        self.assertEqual(code, 0)
        self.assertEqual(self.lines()[1].split("\t")[6:], ["wontfix", "", "out of scope"])

    def test_reason_with_tab_refused(self):
        self.seed()
        code, _, _ = self.res("--note", "N-001", "--wontfix", "--reason", "a\tb")
        self.assertEqual(code, 1)

    def test_in_and_wontfix_together_or_neither(self):
        self.seed()
        self.assertEqual(self.res("--note", "N-001", "--in", "v2", "--wontfix", "--reason", "r")[0], 2)
        self.assertEqual(self.res("--note", "N-001")[0], 2)
        self.assertEqual(self.res("--note", "N-001", "--in", "v2", "--reason", "r")[0], 2)

    def test_unknown_note(self):
        self.seed()
        before = self.ledger.read_bytes()
        code, _, err = self.res("--note", "N-099", "--in", "v2")
        self.assertEqual(code, 1)
        self.assertIn("N-099", err)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_already_done(self):
        self.seed()
        self.res("--note", "N-001", "--in", "v2")
        before = self.ledger.read_bytes()
        code, _, err = self.res("--note", "N-001", "--in", "v3")
        self.assertEqual(code, 1)
        self.assertIn("already", err)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_missing_ledger_and_directory(self):
        self.assertEqual(self.res("--note", "N-001", "--in", "v2")[0], 1)
        d = self.dir / "d"
        d.mkdir()
        code, _, _ = self.run_cli("resolve", "--ledger", d, "--note", "N-001", "--in", "v2")
        self.assertEqual(code, 1)

    def test_ledger_in_git_tree_refused(self):
        self.seed()
        (self.dir / "repo" / ".git").mkdir(parents=True)
        led = self.dir / "repo" / "l.tsv"
        led.write_bytes(self.ledger.read_bytes())
        before = led.read_bytes()
        code, _, err = self.run_cli("resolve", "--ledger", led, "--note", "N-001", "--in", "v2")
        self.assertEqual(code, 1)
        self.assertEqual(led.read_bytes(), before)

    def test_hand_edited_duplicate_note_id(self):
        self.seed()
        lines = self.lines()
        lines[2] = lines[2].replace("N-002", "N-001")
        self.ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
        before = self.ledger.read_bytes()
        for argv in (("resolve", "--ledger", self.ledger, "--note", "N-001", "--in", "v2"),
                     ("report", "--ledger", self.ledger),
                     ("add", "--markers", self.mk("m.tsv", markers(*R1)), "--cut", "c",
                      "--ledger", self.ledger)):
            code, _, err = self.run_cli(*argv)
            self.assertEqual(code, 1, argv[0])
            self.assertIn("duplicate", err)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_damaged_ledger_rows(self):
        self.seed()
        good = self.lines()
        for bad in (["wrong header"] + good[1:], good[:2] + ["N-002\t1"],
                    good[:1] + [good[1].replace("\topen\t", "\tmaybe\t")],
                    good[:1] + [good[1].replace("N-001", "X-1")],
                    good[:1] + [good[1].replace("\topen\t\t", "\tdone\t\t")]):
            self.ledger.write_text("\n".join(bad) + "\n", encoding="utf-8")
            code, _, err = self.run_cli("report", "--ledger", self.ledger)
            self.assertEqual(code, 1)
            self.assertNotIn("Traceback", err)


class TestReport(Base):
    def rep(self, *a):
        return self.run_cli("report", "--ledger", self.ledger, *a)

    def test_report_groups_rounds_open_first_with_counts(self):
        self.seed()
        self.add(self.mk("m2.tsv", markers("96\t00:00:04:00\t1\tstory:trim\tRed\t\t24/1")), cut="show_v02")
        self.run_cli("resolve", "--ledger", self.ledger, "--note", "N-001", "--in", "show_v02")
        self.run_cli("resolve", "--ledger", self.ledger, "--note", "N-003", "--wontfix", "--reason", "taste")
        code, out, _ = self.rep()
        self.assertEqual(code, 0)
        self.assertLess(out.index("## Round 1"), out.index("## Round 2"))
        r1 = out[out.index("## Round 1"):out.index("## Round 2")]
        self.assertIn("1 open, 1 done, 0 wontfix", r1)
        self.assertLess(r1.index("N-002"), r1.index("N-001"))
        self.assertIn("0 open, 0 done, 1 wontfix", out)
        self.assertIn("taste", out)
        self.assertIn("show_v02", out)

    def test_rounds_allowed_line_never_fails(self):
        self.seed()
        _, out, _ = self.rep("--rounds-allowed", "2")
        self.assertIn("1 of 2", out)
        self.add(self.mk("m2.tsv", markers("96\t00:00:04:00\t1\tstory:t\tRed\t\t24/1")), cut="show_v02")
        code, out, _ = self.rep("--rounds-allowed", "2")
        self.assertEqual(code, 0)
        self.assertIn("rounds are used up", out)
        code, out, _ = self.rep("--rounds-allowed", "1")
        self.assertEqual(code, 0)
        self.assertIn("rounds are used up", out)

    def test_rounds_allowed_must_be_positive_int(self):
        self.seed()
        self.assertEqual(self.rep("--rounds-allowed", "0")[0], 2)
        self.assertEqual(self.rep("--rounds-allowed", "x")[0], 2)

    def test_report_writes_nothing(self):
        self.seed()
        before = self.ledger.read_bytes()
        self.rep()
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_report_missing_ledger(self):
        code, _, err = self.rep()
        self.assertEqual(code, 1)
        self.assertIn("not found", err)


class TestUsage(Base):
    def test_help_and_unknown_flag_exit_2_and_do_nothing(self):
        for argv in (["--help"], ["add", "--help"], ["report", "-h"], ["add", "--bogus"],
                     ["nosuch"], [], ["report", "--ledger", str(self.ledger), "--bogus"]):
            code, out, err = self.run_cli(*argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("usage", (out + err).lower())
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_module_runs_as_main_and_stdlib_only(self):
        r = subprocess.run([sys.executable, "-m", "rpresolve.review_notes", "--help"], cwd=PROD,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("usage", (r.stdout + r.stderr).lower())
        src = (PROD / "rpresolve" / "review_notes.py").read_text(encoding="utf-8")
        self.assertNotIn(chr(0x2014), src)


if __name__ == "__main__":
    unittest.main()
