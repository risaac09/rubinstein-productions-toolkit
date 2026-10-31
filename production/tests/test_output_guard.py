"""
CLI outputs that name private media are refused inside ANY git working tree,
not only this repository (the MCP tools already did). Stdlib only; the
backends are stubbed, so no model, numpy, ffmpeg or Resolve is needed.

Run: python3 -m unittest production.tests.test_output_guard -v
"""

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import cmd_detect, cmd_endcheck, cmd_selects, cmd_shoot_table, transcribe as tr  # noqa: E402
from rpresolve import proof  # noqa: E402
from rpresolve.paths import OutputRefused  # noqa: E402

SEGS = {"segments": [{"start": 0.0, "end": 1.0, "text": "hello"}]}


def quiet(fn, *a):
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        return fn(*a), err.getvalue()


class OutputGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(os.path.realpath(self.tmp.name))
        self.repo = root / "other-repo"
        self.repo.mkdir()
        try:
            subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("git not available")
        self.loose = root / "loose"
        self.loose.mkdir()
        self.src = root / "segs.json"
        self.src.write_text(json.dumps(SEGS))

    def test_transcribe_refuses_all_three_before_the_backend(self):
        for flag in ("--out", "--srt", "--txt"):
            target = self.repo / "t.out"
            with mock.patch.object(tr, "run_backend") as backend:
                code, err = quiet(tr.main, ["x.wav", "--backend", "fake", "--source",
                                            str(self.src), flag, str(target)])
            self.assertEqual(code, 2, flag)
            self.assertIn("inside a git working tree", err)
            backend.assert_not_called()
            self.assertFalse(target.exists(), flag)

    def test_transcribe_writes_outside(self):
        out = self.loose / "t.json"
        code, _ = quiet(tr.main, ["x.wav", "--backend", "fake", "--source", str(self.src),
                                  "--out", str(out), "--txt", str(self.loose / "t.txt")])
        self.assertEqual(code, 0)
        self.assertTrue(out.exists() and (self.loose / "t.txt").exists())

    def _detect(self, out):
        args = argparse.Namespace(paths=["x"], out=str(out), json=False)
        result = {"rows": [], "missing": [], "summary": "", "exit_status": 0}
        with mock.patch.object(cmd_detect.rpwork, "detect", return_value=result), \
                mock.patch.object(cmd_detect.rpdetect, "format_tsv", return_value="a\tb\n"):
            return quiet(cmd_detect.run, args)

    def test_detect(self):
        code, err = self._detect(self.repo / "d.tsv")
        self.assertEqual(code, 1)
        self.assertIn("inside a git working tree", err)
        self.assertFalse((self.repo / "d.tsv").exists())
        code, _ = self._detect(self.loose / "d.tsv")
        self.assertEqual(code, 0)
        self.assertEqual((self.loose / "d.tsv").read_text(), "a\tb\n")

    def _shoot(self, out):
        args = argparse.Namespace(paths=["x"], out=str(out), offset=None, long_min=1,
                                  weak_db=-30, audio_seconds=1)
        rows = [{"camera": "A", "flags": []}]
        t = cmd_shoot_table
        with mock.patch.object(t.rpshoot, "build", return_value=(rows, [])), \
                mock.patch.object(t.rpshoot, "format_csv", return_value="c\n"), \
                mock.patch.object(t.rpshoot, "summarize", return_value=""):
            return quiet(t.run, args)

    def test_shoot_table(self):
        code, err = self._shoot(self.repo / "s.csv")
        self.assertEqual(code, 1)
        self.assertIn("inside a git working tree", err)
        self.assertFalse((self.repo / "s.csv").exists())
        code, _ = self._shoot(self.loose / "s.csv")
        self.assertEqual(code, 0)
        self.assertTrue((self.loose / "s.csv").exists())

    def test_endcheck_and_selects_refuse_before_any_work(self):
        code, err = quiet(cmd_endcheck.run, argparse.Namespace(
            manifest="missing.json", no_audio=True, json=str(self.repo / "e.json")))
        self.assertEqual(code, 1)
        self.assertIn("inside a git working tree", err)
        code, err = quiet(cmd_selects.run, argparse.Namespace(
            words="missing.json", approved="missing.txt", min_seconds=1, max_seconds=2,
            out=str(self.repo / "s.tsv")))
        self.assertEqual(code, 1)
        self.assertIn("inside a git working tree", err)

    def test_proof_write_json(self):
        with self.assertRaises(OutputRefused):
            proof.write_json({"a": 1}, str(self.repo / "p.json"))
        self.assertFalse((self.repo / "p.json").exists())
        proof.write_json({"a": 1}, str(self.loose / "p.json"))
        self.assertTrue((self.loose / "p.json").exists())


if __name__ == "__main__":
    unittest.main()
