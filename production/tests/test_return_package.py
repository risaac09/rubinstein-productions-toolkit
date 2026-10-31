"""
Tests for rpresolve.returnpackage and the return-package command, on tiny
text files standing in for media. deliver_check is stubbed, so no ffmpeg,
numpy or Resolve is needed.

Run: /usr/bin/python3 -m unittest production.tests.test_return_package -v
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rpresolve import config as rpconfig, returnpackage as rp  # noqa: E402

CFG = rpconfig.load_config("/nonexistent/config.json")
CLIP = "SW001_Guest_01_example-clip_16x9.mp4"


def put(root, rel, text):
    p = Path(root, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def stub(status="pass", failing=()):
    return lambda config, path, sub: {"status": "fail" if os.path.basename(path) in failing else status}


class ReturnPackage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        t = self.tmp
        put(t, "src/A01/DCIM/P1000001.MP4", "take one")
        put(t, "src/A01/DCIM/P1000002.MP4", "scrap")
        put(t, "src/B01/C0001.MP4", "other camera")
        put(t, f"fin/youtube_16x9/{CLIP}", "final")
        put(t, f"fin/youtube_16x9/{CLIP[:-4]}.srt", "captions")
        put(t, "ses/call.txt", "transcript")
        put(t, "p.drp", "project")
        self.out = os.path.join(t, "out")
        os.mkdir(self.out)

    def build(self, check=None, out=None, **kw):
        t = self.tmp
        return rp.build(out or self.out, "eng-001", "2026-11", "AP-1", "A B, producer",
                        [f"{t}/src/A01", f"{t}/src/B01"], f"{t}/fin", CFG,
                        project_file=f"{t}/p.drp", session=f"{t}/ses", check=check or stub(), **kw)

    def test_pass_builds_and_verifies(self):
        pkg = self.build()
        self.assertTrue(pkg.endswith("2026-11_eng-001"))
        self.assertTrue(os.path.isfile(f"{pkg}/01_originals/A01/DCIM/P1000001.MP4"))
        self.assertTrue(os.path.isfile(f"{pkg}/03_captions/{CLIP[:-4]}.srt"))
        self.assertTrue(os.path.isfile(f"{pkg}/04_project/p.drp"))
        self.assertTrue(os.path.isfile(f"{pkg}/05_session/call.txt"))
        self.assertTrue(os.path.isfile(f"{pkg}/manifest.md"))
        self.assertIn("  ./A01/DCIM/P1000001.MP4\n", Path(pkg, "01_originals/SHA256SUMS").read_text())
        res = rp.verify(pkg)
        self.assertTrue(res and all(s == "OK" for s, _ in res), res)
        self.assertTrue(os.path.isfile(f"{self.tmp}/src/A01/DCIM/P1000001.MP4"))  # copied, not moved

    def test_fail_blocks_and_builds_nothing(self):
        with self.assertRaises(rp.PackageError) as cm:
            self.build(check=stub(failing=(CLIP,)))
        self.assertIn(f"youtube_16x9/{CLIP}: deliver_check FAIL", str(cm.exception))
        self.assertEqual(os.listdir(self.out), [])

    def test_missing_result_blocks(self):
        from rpresolve import delivercheck as dc

        def boom(config, path, sub):
            raise dc.CheckError("unreadable")
        with self.assertRaises(rp.PackageError) as cm:
            self.build(check=boom)
        self.assertIn("no deliver_check result", str(cm.exception))

    def test_stray_final_blocks(self):
        put(self.tmp, "fin/unknown_dest/x.mp4", "x")
        with self.assertRaises(rp.PackageError) as cm:
            self.build()
        self.assertIn("unknown_dest/x.mp4", str(cm.exception))

    def test_tampered_missing_and_extra_files_fail_verify(self):
        pkg = self.build()
        put(pkg, "01_originals/A01/DCIM/P1000001.MP4", "changed")
        os.remove(f"{pkg}/05_session/call.txt")
        put(pkg, "02_finals/youtube_16x9/extra.mp4", "x")
        res = dict((r, s) for s, r in rp.verify(pkg))
        self.assertEqual(res["01_originals/A01/DCIM/P1000001.MP4"], "FAILED")
        self.assertEqual(res["05_session/call.txt"], "MISSING")
        self.assertEqual(res["02_finals/youtube_16x9/extra.mp4"], "NOT LISTED")
        put(pkg, f"02_finals/youtube_16x9/{CLIP}", "tampered final")
        self.assertIn(("FAILED", f"02_finals/youtube_16x9/{CLIP}"), rp.verify(pkg))

    def test_output_inside_git_tree_refused(self):
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        with self.assertRaises(rp.PackageError) as cm:
            self.build(out=os.path.join(repo, "sub"))
        self.assertIn("git working tree", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(repo, "sub")))

    def test_existing_package_not_overwritten(self):
        self.build()
        with self.assertRaises(rp.PackageError):
            self.build()

    def test_symlink_and_nested_output_refused(self):
        os.symlink(f"{self.tmp}/ses", f"{self.tmp}/src/A01/link")
        with self.assertRaises(rp.PackageError) as cm:
            self.build()
        self.assertIn("symlink", str(cm.exception))
        os.remove(f"{self.tmp}/src/A01/link")
        with self.assertRaises(rp.PackageError) as cm:
            self.build(out=f"{self.tmp}/src/A01/pkg")
        self.assertIn("contain one another", str(cm.exception))

    def test_bad_engagement_id(self):
        with self.assertRaises(rp.PackageError):
            rp.build(self.out, "Some Client", "2026-11", "AP-1", "x", [], "", CFG)


if __name__ == "__main__":
    unittest.main()
