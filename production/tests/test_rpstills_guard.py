"""
Offline tests for three rpstills fixes: the git-tree guard on the shoot commands, a proxy that
must come from its own source, unique ids for X1.jpg and X1.jpeg, and a Vision batch that fails
without losing the others. Images are synthetic; Vision is mocked.

Run: ~/.venvs/rp-tools/bin/python -m unittest production.tests.test_rpstills_guard
"""

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rpstills import __main__ as cli, index, vision  # noqa: E402


def jpeg(path, value):
    Image.fromarray(np.full((60, 80, 3), value, np.uint8)).save(path, "JPEG")


@unittest.skipUnless(shutil.which("git"), "needs git")
class GitTreeGuard(unittest.TestCase):
    def test_every_shoot_command_refuses_a_git_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            out = os.path.join(tmp, "out")
            argv = {"index": ["index", tmp, out], "run": ["run", tmp, out, "--session", "s"],
                    "cluster": ["cluster", out], "cull": ["cull", out], "crops": ["crops", out],
                    "review": ["review", out, "--session", "s"]}
            for name, args in argv.items():
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(cli.main(args), 1, name)
                self.assertIn("git working tree", err.getvalue(), name)
            self.assertFalse(os.path.exists(out))


class ProxyAndIds(unittest.TestCase):
    def test_stale_proxy_from_another_source_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = os.path.join(tmp, "proxies"); os.makedirs(pdir)
            a, b = os.path.join(tmp, "a.jpg"), os.path.join(tmp, "b.jpg")
            jpeg(a, 30); jpeg(b, 220)
            ra = index.read_one(a, pdir, "P1")
            proxy = os.path.join(pdir, "P1.jpg")
            with Image.open(proxy) as im:
                self.assertLess(np.asarray(im).mean(), 100)
            shutil.copy(b, a)                      # a different picture under the same stem
            index.read_one(a, pdir, "P1")
            with Image.open(proxy) as im:
                self.assertGreater(np.asarray(im).mean(), 150)
            before = os.stat(proxy).st_mtime_ns
            index.read_one(a, pdir, "P1")          # same source again: reused
            self.assertEqual(os.stat(proxy).st_mtime_ns, before)
            self.assertIsNone(ra["error"])

    def test_jpg_and_jpeg_of_one_stem_get_different_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = [os.path.join(tmp, "X1.jpg"), os.path.join(tmp, "X1.jpeg")]
            ids = index.frame_ids(files, tmp)
            self.assertEqual(len(set(ids)), 2)
            self.assertEqual(sorted(ids), ["X1.jpeg", "X1.jpg"])


class VisionBatches(unittest.TestCase):
    def test_failing_batch_keeps_the_others_and_loses_only_its_bad_file(self):
        images = [f"/x/{i}.jpg" for i in range(6)]

        def fake(exe, flag, chunk):
            if "/x/3.jpg" in chunk:
                return None                        # the batch holding 3 fails, and 3 alone fails again
            return {p: {"faces": [], "people": [], "error": None} for p in chunk}

        with mock.patch.object(vision, "binary", return_value="/bin/true"), \
                mock.patch.object(vision, "BATCH", 3), mock.patch.object(vision, "_batch", side_effect=fake):
            out = vision.faces(images)
        self.assertEqual(sorted(out), [p for p in images if p != "/x/3.jpg"])


if __name__ == "__main__":
    unittest.main()
