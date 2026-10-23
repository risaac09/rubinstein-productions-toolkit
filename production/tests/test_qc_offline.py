"""
Tests for the offline rows of rpresolve.qc (VP-23a, VP-20): the Instagram 30 fps
WARN, leading black as a WARN, sha256 and safe_zone, on lavfi media made here
(small 9:16 H.264 files). Every threshold is a working default; these tests pin
the rows, not the numbers' standing. Skipped where ffmpeg is missing ("ffmpeg
missing"); safe_zone also where numpy is ("needs numpy").

Run: /usr/bin/python3 -m unittest production.tests.test_qc_offline -v
"""

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import config as rpconfig, deliver, delivercheck as dc, qc  # noqa: E402

try:
    import numpy  # noqa: F401
except ImportError:  # pragma: no cover
    numpy = None

WORKFLOW = Path(__file__).resolve().parent.parent / "resolve_workflow.py"
CFG = rpconfig.load_config("/nonexistent/config.json")
SOCIAL = deliver.destination(CFG, "social_9x16")
PLAIN = dict(SOCIAL, key="master", aspect="16x9")


def _have_ffmpeg():
    if not all(os.access(t, os.X_OK) for t in (dc.FFMPEG, dc.FFPROBE)):
        return False
    enc = subprocess.run([dc.FFMPEG, "-hide_banner", "-encoders"], stdin=subprocess.DEVNULL,
                         capture_output=True, text=True).stdout
    return "libx264" in enc


def _make(out, graph, tone=True):
    cmd = [dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-filter_complex",
           graph + (";sine=f=440:r=48000:d=4,volume=0.3[a]" if tone else ""),
           "-map", "[v]"] + (["-map", "[a]"] if tone else []) + \
          ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "4", out]
    p = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr)


def _row(r, name):
    return next(c for c in r["checks"] if c["check"] == name)


@unittest.skipUnless(_have_ffmpeg(), "ffmpeg missing")
class Offline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        t = cls._tmp.name
        pat = "testsrc2=s=108x192:r={r}:d={d}"
        cls.f = {k: os.path.join(t, k + ".mp4") for k in
                 ("fps30", "fps24", "fps2997", "fade", "longblack", "midblack", "caption", "clean")}
        _make(cls.f["fps30"], pat.format(r=30, d=4) + "[v]")
        _make(cls.f["fps24"], pat.format(r=24, d=4) + "[v]")
        _make(cls.f["fps2997"], pat.format(r="30000/1001", d=4) + "[v]")
        blk = "color=c=black:s=108x192:r=30:d={d}"
        cat = "{}[v0];{}[v1];[v0][v1]concat=n=2:v=1:a=0[v]"
        _make(cls.f["fade"], cat.format(blk.format(d=1), pat.format(r=30, d=3)))
        _make(cls.f["longblack"], cat.format(blk.format(d=2.5), pat.format(r=30, d=1.5)))
        _make(cls.f["midblack"], "{}[v0];{}[v1];{}[v2];[v0][v1][v2]concat=n=3:v=1:a=0[v]".format(
            pat.format(r=30, d=1.5), blk.format(d=1), pat.format(r=30, d=1.5)))
        # moving picture with a static white bar burned into the bottom margin
        moving = "mandelbrot=s=108x192:r=30:end_scale=0.05"
        _make(cls.f["caption"], moving + ",drawbox=x=10:y=150:w=88:h=20:color=white:t=fill,"
              "drawbox=x=14:y=154:w=80:h=12:color=black:t=fill[v]")
        _make(cls.f["clean"], moving + "[v]")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def run_qc(self, key, dest=SOCIAL, **kw):
        return qc.run(self.f[key], dest, **kw)

    # social_fps
    def test_30_fps_passes(self):
        self.assertEqual(_row(self.run_qc("fps30"), "social_fps")["status"], qc.PASS)

    def test_2997_passes_within_tolerance(self):
        self.assertEqual(_row(self.run_qc("fps2997"), "social_fps")["status"], qc.PASS)

    def test_24_fps_warns_and_does_not_fail(self):
        r = self.run_qc("fps24")
        row = _row(r, "social_fps")
        self.assertEqual(row["status"], qc.WARN)
        self.assertIn("working default", row["expected"])
        self.assertNotIn(qc.FAIL, [c["status"] for c in r["checks"] if c["check"] == "social_fps"])

    def test_fps_row_skips_off_social(self):
        self.assertEqual(_row(self.run_qc("fps24", PLAIN), "social_fps")["status"], qc.SKIP)
        self.assertEqual(_row(self.run_qc("fps24", None), "social_fps")["status"], qc.SKIP)

    def test_threshold_override(self):
        th = qc.parse_overrides(["social_min_fps=20"])
        self.assertEqual(_row(self.run_qc("fps24", thresholds=th), "social_fps")["status"], qc.PASS)

    # leading black
    def test_leading_black_under_2s_warns(self):
        row = _row(self.run_qc("fade", None), "blackdetect")
        self.assertEqual(row["status"], qc.WARN)
        self.assertIn("fade-in", row["note"])

    def test_leading_black_over_2s_fails(self):
        self.assertEqual(_row(self.run_qc("longblack", None), "blackdetect")["status"], qc.FAIL)

    def test_mid_programme_black_fails(self):
        self.assertEqual(_row(self.run_qc("midblack", None), "blackdetect")["status"], qc.FAIL)

    def test_clean_has_no_black_row_warning(self):
        self.assertEqual(_row(self.run_qc("clean", None), "blackdetect")["status"], qc.PASS)

    # sha256
    def test_sha256_reported_and_asserted(self):
        want = hashlib.sha256(Path(self.f["clean"]).read_bytes()).hexdigest()
        r = self.run_qc("clean", None)
        self.assertEqual(_row(r, "sha256")["found"], want)
        self.assertEqual(_row(r, "sha256")["status"], qc.SKIP)
        self.assertEqual(_row(self.run_qc("clean", None, sha256=want.upper()), "sha256")["status"], qc.PASS)
        bad = self.run_qc("clean", None, sha256="0" * 64)
        self.assertEqual(_row(bad, "sha256")["status"], qc.FAIL)
        self.assertEqual(bad["status"], "fail")

    # safe_zone
    @unittest.skipUnless(numpy, "needs numpy")
    def test_clean_vertical_passes_safe_zone(self):
        row = _row(self.run_qc("clean"), "safe_zone")
        self.assertEqual(row["status"], qc.PASS)
        self.assertEqual(_row(self.run_qc("clean", PLAIN), "safe_zone")["status"], qc.SKIP)

    @unittest.skipUnless(numpy, "needs numpy")
    def test_burned_in_bar_in_bottom_margin_warns(self):
        r = self.run_qc("caption", thresholds=qc.parse_overrides([]))
        row = _row(r, "safe_zone")
        self.assertEqual(row["status"], qc.WARN)
        self.assertIn("bottom", row["note"])

    @unittest.skipUnless(numpy, "needs numpy")
    def test_safe_zone_threshold_raised_passes(self):
        th = qc.parse_overrides(["safe_persist_frac=1"])
        self.assertEqual(_row(self.run_qc("caption", thresholds=th), "safe_zone")["status"], qc.PASS)

    # cli
    def test_help_documents_the_flags(self):
        out = subprocess.run([sys.executable, str(WORKFLOW), "qc", "--help"],
                             capture_output=True, text=True).stdout
        for word in ("--sha256", "social_fps", "safe_zone"):
            self.assertIn(word, out)

    def test_cli_prints_the_rows(self):
        out = subprocess.run([sys.executable, str(WORKFLOW), "qc", self.f["fps24"], "--json"],
                             capture_output=True, text=True)
        self.assertIn('"sha256"', out.stdout)


if __name__ == "__main__":
    unittest.main()
