"""
ograf_template_check: installed, missing and stale templates against a fake
Templates folder, and the restart notice from a faked process start time.

Nothing here touches the real Resolve folder.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rpresolve import ografinstall  # noqa: E402
from rpresolve.mcp import server  # noqa: E402
from rpresolve.mcp.registry import ToolContext  # noqa: E402


class TestOgrafInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.repo, self.inst = root / "repo", root / "installed"
        for name, body in (("t1.ograf.json", b"{}"), ("t1.js", b"export {}")):
            (self.repo / "t1").mkdir(parents=True, exist_ok=True)
            (self.repo / "t1" / name).write_bytes(body)
        (self.repo / "t1" / "motion.json").write_bytes(b"{}")  # not a checked file

    def install(self):
        (self.inst / "t1").mkdir(parents=True)
        for f in (self.repo / "t1").iterdir():
            (self.inst / "t1" / f.name).write_bytes(f.read_bytes())

    def run_check(self, started=None, **kw):
        return ografinstall.check(repo_root=self.repo, installed_root=self.inst,
                                  started_at=lambda: started, **kw)

    def statuses(self, r):
        return {x["file"]: x["status"] for x in r["rows"]}

    def test_missing(self):
        r = self.run_check()
        self.assertEqual(self.statuses(r), {"t1.js": "missing", "t1.ograf.json": "missing"})
        self.assertFalse(r["ok"])
        self.assertFalse(self.inst.exists())  # a check never creates anything

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads a chmod 000 file")
    def test_unreadable_file_is_a_row_and_the_rest_are_still_checked(self):
        self.install()
        bad = self.inst / "t1" / "t1.js"
        bad.chmod(0)
        self.addCleanup(bad.chmod, 0o644)
        r = self.run_check()
        self.assertEqual(self.statuses(r), {"t1.js": "unreadable", "t1.ograf.json": "installed"})
        row = next(x for x in r["rows"] if x["file"] == "t1.js")
        self.assertTrue(row["reason"])
        self.assertIsNone(row["installed_sha256"])
        self.assertFalse(r["ok"])

    def test_matching(self):
        self.install()
        r = self.run_check()
        self.assertEqual(set(self.statuses(r).values()), {"installed"})
        self.assertTrue(r["ok"])
        self.assertFalse(r["restart_needed"])

    def test_stale_by_one_byte(self):
        self.install()
        (self.inst / "t1" / "t1.js").write_bytes(b"export {}!")
        r = self.run_check()
        self.assertEqual(self.statuses(r)["t1.js"], "stale")
        row = next(x for x in r["rows"] if x["file"] == "t1.js")
        self.assertNotEqual(row["repo_sha256"], row["installed_sha256"])
        self.assertFalse(r["ok"])

    def test_restart_notice(self):
        self.install()
        mtime = (self.inst / "t1" / "t1.js").stat().st_mtime
        self.assertTrue(self.run_check(started=mtime - 60)["restart_needed"])
        self.assertFalse(self.run_check(started=mtime + 60)["restart_needed"])
        self.assertFalse(self.run_check(started=None)["restart_needed"])  # Resolve not running

    def test_parse_start(self):
        ps = ("Mon Oct 26 09:00:00 2026 /usr/bin/other\n"
              "Sat Oct 31 08:15:02 2026 /Applications/DaVinci Resolve/DaVinci Resolve.app"
              "/Contents/MacOS/Resolve\n")
        want = time.mktime(time.strptime("Sat Oct 31 08:15:02 2026", "%a %b %d %H:%M:%S %Y"))
        self.assertEqual(ografinstall.parse_start(ps), want)
        self.assertIsNone(ografinstall.parse_start("Mon Oct 26 09:00:00 2026 /usr/bin/other\n"))

    def test_ps_runs_in_the_c_locale(self):
        # ps localizes lstart; under a French locale the start time must still read.
        line = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/MacOS/Resolve\n"

        def fake_run(argv, **kw):
            c = (kw.get("env") or os.environ).get("LC_ALL") == "C"
            return subprocess.CompletedProcess(argv, 0, ("Sat Oct 31 08:15:02 2026 " if c
                                                         else "sam. 31 oct. 08:15:02 2026 ") + line)
        with mock.patch.dict(os.environ, {"LC_ALL": "fr_FR.UTF-8"}), \
                mock.patch.object(ografinstall.subprocess, "run", fake_run):
            self.assertEqual(ografinstall.resolve_start_time(), time.mktime(
                time.strptime("Sat Oct 31 08:15:02 2026", "%a %b %d %H:%M:%S %Y")))

    def test_only_matching_files_count_for_restart(self):
        self.install()
        (self.inst / "t1" / "t1.js").write_bytes(b"changed")
        mtime = (self.inst / "t1" / "t1.js").stat().st_mtime
        os.utime(self.inst / "t1" / "t1.ograf.json", (mtime - 999, mtime - 999))
        self.assertFalse(self.run_check(started=mtime - 1)["restart_needed"])

    def test_unknown_template(self):
        with self.assertRaisesRegex(ValueError, "no template"):
            self.run_check(template="nope")

    def test_tool_is_registered_read_only(self):
        tool = server.build_registry().tools["ograf_template_check"]
        self.assertTrue(tool.annotations["readOnlyHint"])
        self.assertFalse(tool.annotations.get("destructiveHint", False))

    def test_tool_reads_the_repo_templates(self):
        # Only the repo copies are read; the real install folder is swapped for an empty one.
        real = ografinstall.INSTALLED_ROOT
        ografinstall.INSTALLED_ROOT = str(self.inst)
        self.addCleanup(setattr, ografinstall, "INSTALLED_ROOT", real)
        ografinstall.resolve_start_time, real_ps = (lambda: None), ografinstall.resolve_start_time
        self.addCleanup(setattr, ografinstall, "resolve_start_time", real_ps)
        tool = server.build_registry().tools["ograf_template_check"]
        r = tool.handler({"template": "say-why-lower-third"}, ToolContext())
        self.assertEqual({x["status"] for x in r["rows"]}, {"missing"})
        self.assertIn("missing", r["summary"])


if __name__ == "__main__":
    unittest.main()
