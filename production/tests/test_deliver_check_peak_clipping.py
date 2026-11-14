"""
Tests for the deliver-check peak and clipping rows, the tail-not-asserted line and the
4:5 social destination, on audio made here with ffmpeg: a clean tone, a tone over the
ceiling, a flat-topped (clipped) tone, and a clean full-scale tone.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

from rpresolve import deliver, delivercheck as dc  # noqa: E402
from test_delivercheck import CFG, needs_ffmpeg, statuses, tiny  # noqa: E402

SR = 48000


def make_master(path, expr, seconds=5):
    """A tiny ProRes master (the client_master shape) whose stereo 24-bit PCM sound is
    the aevalsrc expression `expr` (a function of t)."""
    subprocess.run([dc.FFMPEG, "-v", "error", "-y", "-f", "lavfi",
                    "-i", f"testsrc2=size=64x36:rate=24000/1001:duration={seconds}",
                    "-f", "lavfi", "-i", f"aevalsrc=exprs={expr}:c=stereo:s={SR}:d={seconds}",
                    "-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le",
                    "-c:a", "pcm_s24le", "-shortest", path],
                   stdin=subprocess.DEVNULL, check=True)
    return path


def sine(amp, hz=997):
    return f"{amp}*sin(2*PI*{hz}*t)"


class TestFlatRuns(unittest.TestCase):
    def test_a_clean_full_scale_sine_has_no_flat_run_and_a_clipped_one_does(self):
        t = np.arange(SR) / SR
        for hz in (50, 997):
            clean = np.round(np.sin(2 * np.pi * hz * t) * 8388607) / 8388608  # 24 bit
            self.assertEqual(dc.flat_runs([clean[:, None].astype("f4")], SR)["runs"], 0, hz)
        clipped = np.clip(np.sin(2 * np.pi * 300 * t) * 1.6, -1, 1).astype("f4")
        r = dc.flat_runs([clipped[:, None]], SR)
        self.assertEqual(r["runs"], 600)  # two crests per cycle, 300 cycles
        self.assertGreaterEqual(r["longest"], dc.CLIP_RUN)

    def test_a_run_across_a_chunk_edge_counts_once(self):
        x = np.zeros((20, 2), "f4")
        x[8:13, 0] = 1.0  # five samples, split 3 and 2 by the edge at 11
        r = dc.flat_runs([x[:11], x[11:]], SR)
        self.assertEqual((r["runs"], r["samples"], r["longest"]), (1, 5, 5))
        self.assertAlmostEqual(r["first_s"], 8 / SR)

    def test_a_flat_run_below_full_scale_or_shorter_than_the_rule_is_not_clipping(self):
        x = np.zeros((20, 1), "f4")
        x[2:9] = 0.5          # long and flat, but at half scale
        x[12:12 + dc.CLIP_RUN - 1] = -1.0
        self.assertEqual(dc.flat_runs([x], SR)["runs"], 0)


@needs_ffmpeg
class TestPeakAndClippingRows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        cls.clean = make_master(str(d / "Client_clean_master.mov"), sine(0.5))
        cls.over = make_master(str(d / "Client_over_master.mov"), sine(0.95))
        cls.clipped = make_master(str(d / "Client_clipped_master.mov"),
                                  "clip(1.6*sin(2*PI*300*t)\\,-1\\,1)")
        cls.full = make_master(str(d / "Client_full_master.mov"), sine(1.0))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def rows(self, path, key="client_master", **kw):
        return statuses(dc.check(path, tiny(key), **kw))

    def test_a_clean_tone_passes_both_on_a_destination_with_no_loudness_target(self):
        r = self.rows(self.clean)
        self.assertEqual((r["loudness"], r["true_peak"], r["clipping"]),
                         (dc.SKIP, dc.PASS, dc.PASS))

    def test_a_tone_over_the_ceiling_fails_the_peak_row_with_the_value_found(self):
        res = dc.check(self.over, tiny("client_master"))
        row = next(x for x in res["checks"] if x["check"] == "true_peak")
        self.assertEqual(row["status"], dc.FAIL)
        self.assertIn("dBTP", row["found"])
        self.assertIn("-1", row["expected"])
        self.assertEqual(statuses(res)["clipping"], dc.PASS)

    def test_a_clipped_tone_fails_clipping_and_names_the_first_run(self):
        res = dc.check(self.clipped, tiny("client_master"))
        row = next(x for x in res["checks"] if x["check"] == "clipping")
        self.assertEqual(row["status"], dc.FAIL)
        self.assertIn("flat run", row["found"])
        self.assertIn(" s", row["found"])
        self.assertEqual(res["status"], "fail")

    def test_a_clean_full_scale_tone_passes_clipping_and_fails_only_the_peak(self):
        r = self.rows(self.full)
        self.assertEqual((r["clipping"], r["true_peak"]), (dc.PASS, dc.FAIL))

    def test_a_destination_naming_a_ceiling_uses_it(self):
        dest = tiny("client_master")
        dest["loudness"] = {"integrated_lufs": None, "true_peak_max_dbtp": -7.0}
        self.assertEqual(statuses(dc.check(self.clean, dest))["true_peak"], dc.FAIL)  # -6 dBTP
        dest["loudness"]["true_peak_max_dbtp"] = -5.0
        self.assertEqual(statuses(dc.check(self.clean, dest))["true_peak"], dc.PASS)

    def test_the_rows_are_skipped_not_run_when_loudness_is_off(self):
        r = self.rows(self.clipped, loudness=False)
        self.assertEqual((r["true_peak"], r["clipping"]), (dc.SKIP, dc.SKIP))

    def test_the_tail_line_appears_only_when_the_tail_was_not_asserted(self):
        off = dc.check(self.clean, tiny("client_master"))
        self.assertIs(off["tail_asserted"], False)
        self.assertIn("tail not asserted", dc.format_report(off))
        on = dc.check(self.clean, tiny("client_master"), expect_fade=True)
        self.assertIs(on["tail_asserted"], True)
        self.assertNotIn("tail not asserted", dc.format_report(on))
        self.assertEqual(statuses(on)["tail"], dc.FAIL)  # a steady tone does not fade


class TestSocial4x5(unittest.TestCase):
    def test_the_destination_is_complete_and_named_with_its_aspect(self):
        d = deliver.destination(CFG, "social_4x5")
        self.assertEqual((d["aspect"], d["resolution"]), ("4x5", {"width": 1080, "height": 1350}))
        name = deliver.deliverable_name("SW", 1, "Guest", 1, "example-clip", "4x5", "mp4")
        self.assertEqual(name, "SW001_Guest_01_example-clip_4x5.mp4")
        self.assertEqual(deliver.name_problems(name, d), [])
        self.assertTrue(deliver.name_problems(name.replace("4x5", "1x1"), d))


if __name__ == "__main__":
    unittest.main()
