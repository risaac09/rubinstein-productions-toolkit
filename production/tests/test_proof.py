"""
Offline tests for rpresolve.proof and the pipeline-proof command. Every clip
is made with ffmpeg lavfi at test time; no Resolve, no real footage.

Run: /usr/bin/python3 -m unittest production.tests.test_proof -v
"""

import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import numpy  # noqa: F401  (rpresolve.measure imports it)
    from rpresolve import measure, proof
    import resolve_workflow
except ImportError:
    proof = None

needs = unittest.skipIf(proof is None or not shutil.which(measure.FFMPEG), "needs numpy and ffmpeg")

SRC = "gradients=size=160x96:rate=10:duration=1.5:speed=0.0001:c0=black:c1=white:seed=1"


def make(path, vf="null", full=False, tag=None, rate=10):
    """Lossless H.264 clip from the lavfi gradient, passed through vf. tag
    rewrites the colour_range flag in the bitstream without touching the
    pixels (-color_range would convert them); rate is the frame rate."""
    rng = (["-bsf:v", "h264_metadata=video_full_range_flag=" + ("1" if tag == "pc" else "0")]
           if tag else ["-color_range", "pc" if full else "tv"])
    vf = ("scale=out_range=full" if full else "scale=out_range=limited") + ",format=yuv420p," + vf
    cmd = [measure.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", SRC, "-vf", vf + f",fps={rate}",
           "-c:v", "libx264", "-qp", "0"] + rng + [str(path)]
    subprocess.run(cmd, check=True, stdin=subprocess.DEVNULL)


@needs
class ProofTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        cls.ref, cls.same, cls.gamma, cls.full = (d / n for n in ("ref.mp4", "same.mp4", "gamma.mp4", "full.mp4"))
        make(cls.ref)
        make(cls.same)
        # a gamma curve across the legal span: endpoints stay, midtones move
        make(cls.gamma, "lutyuv=y='pow(clip((val-16)/219,0,1),0.7)*219+16'")
        make(cls.full, full=True)
        # same frame count as ref (15), half the frame rate, so twice the duration
        cls.slow = d / "slow.mp4"
        make(cls.slow, "setpts=2*PTS", rate=5)
        # legal-range pixels tagged pc, and full-range pixels tagged tv
        cls.legal_as_pc, cls.full_as_tv = d / "legal_as_pc.mp4", d / "full_as_tv.mp4"
        make(cls.legal_as_pc, tag="pc")
        make(cls.full_as_tv, full=True, tag="tv")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def rows(self, report):
        return {r["check"]: r for r in report["rows"]}

    def test_identical_pair_passes(self):
        report = proof.compare(str(self.same), str(self.ref))
        self.assertTrue(report["passed"], proof.format_report(report))
        self.assertEqual(self.rows(report)["range"]["status"], "PASS")

    def test_gamma_curve_fails_with_level_row(self):
        report = proof.compare(str(self.gamma), str(self.ref))
        self.assertFalse(report["passed"])
        row = self.rows(report)["gamma or level shift"]
        self.assertEqual(row["status"], "FAIL")
        self.assertIn("codes", row["value"])
        self.assertIn("gamma about", row["value"])
        self.assertEqual(self.rows(report)["range"]["status"], "PASS")

    def test_range_mismatch_is_detected(self):
        report = proof.compare(str(self.full), str(self.ref))
        row = self.rows(report)["range"]
        self.assertEqual(row["status"], "FAIL")
        self.assertIn("test full", row["value"])
        self.assertIn("ref legal", row["value"])
        self.assertFalse(report["passed"])

    def test_vmaf_reported_not_available_when_missing(self):
        with mock.patch.object(proof, "has_libvmaf", return_value=False):
            report = proof.compare(str(self.same), str(self.ref))
        row = self.rows(report)["vmaf"]
        self.assertEqual(row["status"], "n/a")
        self.assertIn("not available", row["value"])
        self.assertTrue(report["passed"])

    def test_fps_row_fails_when_rates_differ_and_counts_match(self):
        report = proof.compare(str(self.slow), str(self.ref))
        rows = self.rows(report)
        self.assertEqual(rows["frames"]["status"], "PASS", proof.format_report(report))
        self.assertEqual(rows["fps"]["status"], "FAIL")
        self.assertIn("test 5/1, ref 10/1", rows["fps"]["value"])
        self.assertFalse(report["passed"])
        self.assertEqual(self.rows(proof.compare(str(self.same), str(self.ref)))["fps"]["status"], "PASS")

    def test_range_tag_disagreeing_with_extremes_is_flagged(self):
        for clip, said in ((self.legal_as_pc, "test tagged pc but measures legal"),
                           (self.full_as_tv, "test tagged tv but measures full")):
            row = self.rows(proof.compare(str(clip), str(self.ref)))["range tag"]
            self.assertEqual(row["status"], "WARN")
            self.assertIn(said, row["value"])
        report = proof.compare(str(self.same), str(self.ref))
        self.assertNotIn("range tag", self.rows(report))

    def test_flagged_range_tag_does_not_fail_by_itself(self):
        report = proof.compare(str(self.legal_as_pc), str(self.ref))
        self.assertEqual(self.rows(report)["range"]["status"], "FAIL")  # tag used: pc against tv
        self.assertEqual(self.rows(report)["range tag"]["status"], "WARN")
        self.assertEqual([r["check"] for r in report["rows"] if r["status"] == "FAIL"].count("range tag"), 0)

    @unittest.skipUnless(proof is not None and shutil.which(measure.FFMPEG) and proof.has_libvmaf(),
                         "this ffmpeg build has no libvmaf; the success path cannot run here")
    def test_libvmaf_success_path_scores_an_identical_pair(self):
        score = proof.vmaf(str(self.same), str(self.ref), proof.MAX_FRAMES)
        self.assertIsNotNone(score)
        self.assertGreaterEqual(score, proof.DEFAULTS["vmaf"])
        row = self.rows(proof.compare(str(self.same), str(self.ref)))["vmaf"]
        self.assertEqual(row["status"], "PASS")

    def test_thresholds_are_overridable(self):
        report = proof.compare(str(self.gamma), str(self.ref),
                               thresholds={"luma_shift": 200, "chroma_shift": 200, "psnr": 1, "ssim": 0, "vmaf": 0})
        self.assertTrue(report["passed"], proof.format_report(report))

    def test_report_lists_fail_rows_first(self):
        text = proof.format_report(proof.compare(str(self.gamma), str(self.ref)))
        lines = [l for l in text.splitlines() if l.startswith("  ")]
        statuses = [l.split()[0] for l in lines]
        self.assertEqual(statuses, sorted(statuses, key=lambda s: s != "FAIL"))
        self.assertTrue(text.rstrip().endswith("RESULT: FAIL"))

    def test_cli_exit_status_and_help_trap(self):
        def run(*argv):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err), \
                    mock.patch.object(sys, "argv", ["resolve_workflow.py", *argv]), \
                    mock.patch.object(resolve_workflow.rpapi, "exit_clean", side_effect=lambda code=0: sys.exit(code)):
                try:
                    code = resolve_workflow.main()
                except SystemExit as e:
                    code = e.code
            return code or 0, out.getvalue()
        code, out = run("pipeline-proof", str(self.same), str(self.ref))
        self.assertEqual(code, 0, out)
        code, out = run("pipeline-proof", str(self.gamma), str(self.ref))
        self.assertEqual(code, 1, out)
        _, helptext = run("pipeline-proof", "-h")
        self.assertIn("25 code values darker", helptext)


if __name__ == "__main__":
    unittest.main()
