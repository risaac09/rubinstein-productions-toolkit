"""
Offline tests for rpresolve.renderloop: lavfi fixtures rendered by ffmpeg, checked
by the tools that already exist, fixed by a caller-supplied step. No Resolve.
Skipped where ffmpeg is missing ("ffmpeg missing") or numpy is ("needs numpy").

Run: /usr/bin/python3 -m unittest production.tests.test_renderloop -v
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import numpy  # noqa: F401
    from rpresolve import delivercheck as dc, renderloop as rl
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

HAVE_FFMPEG = shutil.which("ffmpeg") is not None or (
    HAVE_NUMPY and os.path.exists(dc.FFMPEG))

needs_numpy = unittest.skipUnless(HAVE_NUMPY, "needs numpy")
needs_ffmpeg = unittest.skipUnless(HAVE_FFMPEG, "ffmpeg missing")


@needs_numpy
@needs_ffmpeg
class TestLoop(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.work, True)

    def test_graphic_converges(self):
        """No alpha in round 1; the fix switches to ProRes 4444 with alpha."""
        spec = "yuva444p12le"
        r = rl.run_loop("graphic", {"pix_fmt": "yuv422p10le", "expect_pix_fmt": spec},
                        lambda rec: dict(rec["params"], pix_fmt=spec), self.work)
        self.assertTrue(r["converged"], r["table"])
        self.assertEqual(len(r["rounds"]), 2)
        self.assertEqual(r["rounds"][0]["rows"][0]["status"], "FAIL")  # FAIL first
        self.assertIn("graphic r2", r["table"])

    def test_sound_converges_by_gain(self):
        """+10 dB too loud; the fix moves the gain by the measured offset."""
        def fix(rec):
            off = rec["raw"]["integrated_lufs"] - rec["params"]["target_lufs"]
            return dict(rec["params"], gain_db=round(rec["params"]["gain_db"] - off, 1))
        r = rl.run_loop("sound", {"gain_db": 10, "target_lufs": -16}, fix, self.work)
        self.assertTrue(r["converged"], r["table"])
        self.assertLessEqual(len(r["rounds"]), rl.ROUNDS)
        self.assertGreater(len(r["rounds"]), 1)

    def test_cut_converges(self):
        r = rl.run_loop("cut", {"black_s": 1.0}, lambda rec: dict(rec["params"], black_s=0),
                        self.work)
        self.assertTrue(r["converged"], r["table"])
        self.assertEqual(r["rounds"][0]["rows"][0]["status"], "FAIL")

    def test_grade_converges_against_reference(self):
        ref = rl.make_grade_ref(self.work)

        def fix(rec):
            d = rec["raw"]["luma_p50_delta_ire"]
            return dict(rec["params"], brightness=round(rec["params"]["brightness"] - d / 100, 3))
        r = rl.run_loop("grade", {"brightness": 0.2, "tol_ire": 2, "ref": ref}, fix, self.work)
        self.assertTrue(r["converged"], r["table"])

    def test_cap_and_hand_back(self):
        """A fix that changes nothing runs the full cap, then hands back with the table."""
        r = rl.run_loop("cut", {"black_s": 1.0}, lambda rec: rec["params"], self.work)
        self.assertFalse(r["converged"])
        self.assertTrue(r["handed_back"])
        self.assertEqual(len(r["rounds"]), rl.ROUNDS)
        self.assertIn("Handed back after 5 round(s)", r["table"])

    def test_fix_can_give_up_early(self):
        r = rl.run_loop("cut", {"black_s": 1.0}, lambda rec: None, self.work)
        self.assertTrue(r["handed_back"])
        self.assertEqual(len(r["rounds"]), 1)

    def test_work_folder_in_git_tree_refused(self):
        here = str(Path(__file__).resolve().parent / "renderloop-work")
        with self.assertRaises(rl.LoopError):
            rl.run_loop("cut", {"black_s": 0}, lambda rec: None, here)
        self.assertFalse(os.path.exists(here))

    def test_unknown_kind(self):
        with self.assertRaises(rl.LoopError):
            rl.run_loop("poster", {}, lambda rec: None, self.work)


@needs_numpy
@needs_ffmpeg
class TestSpecWrong(unittest.TestCase):
    """The known limit: the loop checks the render against the spec, not the spec."""

    def setUp(self):
        self.work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.work, True)

    def test_unmeetable_spec_never_converges(self):
        """overlay-check always wants alpha; a spec asking yuv420p cannot be met."""
        r = rl.run_loop("graphic", {"pix_fmt": "yuva444p12le", "expect_pix_fmt": "yuv420p"},
                        lambda rec: dict(rec["params"], pix_fmt="yuv420p"), self.work)
        self.assertTrue(r["handed_back"])
        self.assertEqual(len(r["rounds"]), rl.ROUNDS)

    def test_wrong_but_meetable_spec_passes(self):
        """A -30 LUFS target on a house that delivers at -16 converges and reports PASS."""
        def fix(rec):
            off = rec["raw"]["integrated_lufs"] - rec["params"]["target_lufs"]
            return dict(rec["params"], gain_db=round(rec["params"]["gain_db"] - off, 1))
        r = rl.run_loop("sound", {"gain_db": 0, "target_lufs": -30}, fix, self.work)
        self.assertTrue(r["converged"], r["table"])
        self.assertLess(r["rounds"][-1]["raw"]["integrated_lufs"], -28)


if __name__ == "__main__":
    unittest.main()
