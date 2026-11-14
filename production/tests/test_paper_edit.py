"""
Offline tests for the paper-edit command. Synthetic sources, words and text only.

Run: python3 -m unittest discover production/tests -v
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rpresolve import cli, cmd_assemble, cutlist
from test_cutlist import SPEECH, words_from

HEAD = "order\tsource\tin\tout\tbeat\tspeaker\ttext\treuse\n"
SAID = "people act like the map is everything"   # SPEECH, 1.0 to 2.7 s
GOOD = [("1", "a", "1.0", "2.72", "open", "x", SAID, ""),
        ("2", "b", "0", "2", "close", "y", "", "")]


class Case(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.tmp = self._t.name
        self.addCleanup(self._t.cleanup)
        self.src = {}
        for sid in "ab":
            p = os.path.join(self.tmp, sid + ".bin")
            Path(p).write_bytes(sid.encode())
            w = os.path.join(self.tmp, sid + ".words.json")
            Path(w).write_text(json.dumps(words_from(SPEECH)))
            self.src[sid] = {"id": sid, "path": p, "words": w, "duration": 10, "fps": 25}

    def go(self, rows=GOOD, edit=None, length="3.72", extra=(), head=HEAD, src=None):
        tsv = os.path.join(self.tmp, "edit.tsv")
        Path(tsv).write_text(head + "".join("\t".join(r) + "\n" for r in rows))
        sj = os.path.join(self.tmp, "src.json")
        Path(sj).write_text(json.dumps(list((src or self.src).values())))
        argv = ["paper-edit", tsv, "--sources", sj, "--fps", "25"] + (["--length", length] if length else []) + list(extra)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def status(self, out, name):
        return next(l.split()[0] for l in out.splitlines() if l[8:].startswith(name))


class TestChecks(Case):
    def test_valid_passes(self):
        code, out, _ = self.go()
        self.assertEqual(code, 0, out)
        self.assertEqual({l.split()[0] for l in out.splitlines() if l[0] != " "}, {"PASS"})

    def fault(self, name, status, code, **kw):
        c, out, _ = self.go(**kw)
        self.assertEqual(self.status(out, name), status, out)
        self.assertEqual(c, code, out)

    def test_unknown_source(self):
        self.fault("sources", "FAIL", 1, rows=[GOOD[0], ("2", "zz", "0", "2", "", "", "", "")])

    def test_missing_file(self):
        os.remove(self.src["b"]["path"])
        self.fault("sources", "FAIL", 1)

    def test_wrong_hash(self):
        self.src["b"]["sha256"] = "0" * 64
        self.fault("sources", "FAIL", 1)

    def test_span_past_source(self):
        self.fault("bounds", "FAIL", 1, rows=[GOOD[0], ("2", "b", "9", "11", "", "", "", "")], length="3.72")

    def test_backwards_span(self):
        self.fault("bounds", "FAIL", 1, rows=[GOOD[0], ("2", "b", "2", "1", "", "", "", "")], length=None)

    def test_overlap_and_reuse(self):
        rows = [GOOD[0], ("2", "a", "2.0", "3.0", "", "x", "", "")]
        self.fault("overlap", "FAIL", 1, rows=rows, length=None)
        rows[1] = rows[1][:7] + ("yes",)
        self.fault("overlap", "PASS", 2, rows=rows, length=None)   # review: the join is not adjacent

    def test_off_grid_time(self):
        self.fault("timecode", "FAIL", 1, rows=[GOOD[0], ("2", "b", "0.013", "2", "", "", "", "")], length=None)

    def test_frame_field_too_high(self):
        self.fault("timecode", "FAIL", 1, rows=[GOOD[0], ("2", "b", "00:00:00:25", "00:00:02:00", "", "", "", "")], length=None)

    def test_source_rate_differs(self):
        self.src["b"]["fps"] = 30
        self.fault("timecode", "REVIEW", 2, length=None)

    def test_text_not_said(self):
        bad = ("1", "a", "1.0", "2.72", "", "x", "quarterly revenue exceeded every forecast we made", "")
        self.fault("text", "FAIL", 1, rows=[bad, GOOD[1]])

    def test_frankenbite(self):
        rows = [GOOD[0], ("2", "a", "3.6", "4.88", "", "x", "", "")]
        self.fault("frankenbite", "REVIEW", 2, rows=rows, length="3.0")
        rows[1] = ("2", "a", "2.72", "3.72", "", "x", "", "")        # adjacent: no flag
        self.fault("frankenbite", "PASS", 0, rows=rows, length="2.72")

    def test_length_off_by_one_frame(self):
        self.fault("length", "FAIL", 1, length="3.76")
        c, out, _ = self.go(length="3.76", extra=["--length-tolerance", "1"])
        self.assertEqual(c, 0, out)

    def test_order_column(self):
        self.fault("order", "FAIL", 1, rows=[("2",) + GOOD[0][1:], ("1",) + GOOD[1][1:]])


class TestManifest(Case):
    def test_round_trip(self):
        m = os.path.join(self.tmp, "m.json")
        code, out, err = self.go(extra=["--manifest-out", m])
        self.assertEqual(code, 0, out + err)
        got = cutlist.load_manifest(m)                       # the manifest validator accepts it
        self.assertEqual(cutlist.version(got), 2)
        self.assertEqual([s["source"] for s in got["clips"][0]["spans"]], ["a", "b"])
        plan = cmd_assemble.plan(m)                          # and assemble's planner reads it unchanged
        self.assertEqual(len(plan["spans"]), 2)
        self.assertEqual(plan["frames"], 93)                 # 43 + 50 frames at 25 fps

    def test_no_manifest_on_failure(self):
        m = os.path.join(self.tmp, "m.json")
        code, _, _ = self.go(length="9", extra=["--manifest-out", m])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(m))

    def test_speechless_edit_is_a_speech_false_manifest(self):
        for s in self.src.values():
            del s["words"]
        m = os.path.join(self.tmp, "m.json")
        rows = [GOOD[0][:6] + ("", ""), GOOD[1]]
        code, out, err = self.go(rows=rows, extra=["--manifest-out", m])
        self.assertIn(code, (0, 2), out + err)
        self.assertEqual(json.loads(Path(m).read_text())["speech"], False)

    def test_deterministic(self):
        a, b = os.path.join(self.tmp, "a.json"), os.path.join(self.tmp, "b.json")
        r1 = self.go(extra=["--manifest-out", a])
        r2 = self.go(extra=["--manifest-out", b])
        self.assertEqual(r1[:2], r2[:2])
        self.assertEqual(Path(a).read_bytes(), Path(b).read_bytes())


if __name__ == "__main__":
    unittest.main()
