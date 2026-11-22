"""Tests for rpresolve.decon_steps: the step rule on per-step change shares (numpy only), and a real
read of a file where a block of a still frame changes once (ffmpeg)."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import delivercheck as dc, methods  # noqa: E402

try:
    import numpy as np
    from rpresolve import decon_steps as ds
except ImportError:
    np = None


@unittest.skipIf(np is None, "numpy not installed")
class TestSteps(unittest.TestCase):
    def test_rule(self):
        calm = [0.0] * 10
        ch = np.array(calm + [0.05] + calm)
        self.assertEqual(ds.from_changes(ch, 10), [1.1])
        self.assertEqual(ds.from_changes(ch, 10, cuts=[1.2]), [])                # near a cut: the cut
        self.assertEqual(ds.from_changes(np.array(calm + [0.01] + calm), 10), [])  # under SHARE
        busy = np.array(calm + [0.05] + [0.02] * 10)
        self.assertEqual(ds.from_changes(busy, 10), [])                           # the screen keeps moving
        self.assertEqual(ds.from_changes(np.array([0.05] * 3), 10), [])

    @unittest.skipUnless(os.access(dc.FFMPEG, os.X_OK), "ffmpeg missing")
    def test_a_menu_opening_on_a_still_screen(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "menu.mkv")
            graph = ("color=c=0x303030:s=320x180:r=10:d=3,format=gray[a];"
                     "color=c=0xd0d0d0:s=80x60:r=10:d=3,format=gray[m];"
                     "[a][m]overlay=x=40:y=40:enable='gte(t,1.5)'[v]")
            subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-filter_complex", graph,
                            "-map", "[v]", "-c:v", "ffv1", path], check=True, stdin=subprocess.DEVNULL)
            got = ds.find(path)
            self.assertEqual(len(got), 1, got)
            self.assertAlmostEqual(got[0], 1.5, delta=0.11)

    def test_schema_steps(self):
        m = methods.load("punch-montage")
        self.assertEqual(methods.validate(dict(m, steps=[1.0, 2.5])), [])
        self.assertTrue(methods.validate(dict(m, steps=[2.5, 1.0])))
        self.assertTrue(methods.validate(dict(m, steps="1")))


if __name__ == "__main__":
    unittest.main()
