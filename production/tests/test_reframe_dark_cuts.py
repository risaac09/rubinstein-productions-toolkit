"""
Tests for reframe.span_cuts on dark, noisy footage: scdet scores a cut as
min(mean frame difference, its change from the last frame's), so sensor noise
lifts the floor and a cut out of a dark shot scores under the threshold. The
mean-luma jump across a cut is the second cue. Clips are made here with
ffmpeg lavfi (320x180, 2 s a shot); skipped where ffmpeg is missing
("ffmpeg missing").

Run: /usr/bin/python3 -m unittest production.tests.test_reframe_dark_cuts -v
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import delivercheck as dc, qc, reframe  # noqa: E402

HAVE_FFMPEG = os.access(dc.FFMPEG, os.X_OK)


def _clip(path, noise, second=True):
    dark = f"color=c=0x0a0a0c:s=320x180:r=25:d=2,noise=alls={noise}:allf=t"
    lit = (f"color=c=0x303030:s=320x180:r=25:d=2,drawbox=x=100:y=40:w=120:h=90:color=0x606050:t=fill,"
           f"noise=alls={noise}:allf=t") if second else dark
    subprocess.run([dc.FFMPEG, "-hide_banner", "-v", "error", "-y", "-f", "lavfi", "-i", dark,
                    "-f", "lavfi", "-i", lit, "-filter_complex", "[0][1]concat=n=2:v=1[o]",
                    "-map", "[o]", "-pix_fmt", "yuv420p", path], check=True)


class ParseLumaJumps(unittest.TestCase):
    def test_a_jump_of_the_mean_luma_between_frames_is_a_cut_at_the_new_frame(self):
        text = "\n".join(f"[Parsed_metadata_2 @ 0x1] frame:{i} pts:{i} pts_time:{i * 0.04:g}\n"
                         f"[Parsed_metadata_2 @ 0x1] lavfi.signalstats.YAVG={y}"
                         for i, y in enumerate([20.0, 20.5, 19.8, 60.0, 60.4]))
        self.assertEqual(reframe.parse_luma_jumps(text, 8.0), [0.12])
        self.assertEqual(reframe.parse_luma_jumps(text, 50.0), [])

    def _jumps(self, ys):
        text = "\n".join(f"frame:{i} pts:{i} pts_time:{i * 0.04:g}\nlavfi.signalstats.YAVG={y}"
                         for i, y in enumerate(ys))
        return reframe.parse_luma_jumps(text, 8.0)

    def test_a_fade_a_flash_and_a_flicker_are_not_cuts_and_a_step_is(self):
        self.assertEqual(self._jumps([100 - 10 * i for i in range(10)]), [])  # a fade
        self.assertEqual(self._jumps([20] * 5 + [90, 90] + [20] * 5), [])  # a two-frame flash
        self.assertEqual(self._jumps([20, 60] * 8), [])  # a flicker
        self.assertEqual(len(self._jumps([20] * 5 + [90] * 5)), 1)


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg missing")
class DarkCuts(unittest.TestCase):
    def test_a_noisy_dark_cut_that_scdet_alone_misses_is_found(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "cut.mp4")
            _clip(p, 30)
            proc = subprocess.run([dc.FFMPEG, "-hide_banner", "-nostats", "-i", p, "-vf",
                                   f"scdet=t={qc.THRESHOLDS['scdet_threshold']}", "-f", "null", "-"],
                                  capture_output=True, text=True)
            self.assertEqual(qc.parse_scdet(proc.stderr, qc.THRESHOLDS["scdet_threshold"]), [])
            cuts = reframe.span_cuts(p, 0.0, 4.0)
            self.assertEqual(len(cuts), 1, cuts)
            self.assertAlmostEqual(cuts[0], 2.0, delta=0.05)

    def test_a_noisy_dark_shot_with_no_cut_stays_quiet(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "nocut.mp4")
            _clip(p, 30, second=False)
            self.assertEqual(reframe.span_cuts(p, 0.0, 4.0), [])

    def test_ten_bit_footage_reads_as_eight_bit_does(self):
        # luma ramps 3 levels a frame for a second: no cut at any bit depth
        ramp = "color=c=0x202020:s=320x180:r=25:d=4,geq=lum='32+3*min(N,25)':cb=128:cr=128"
        with tempfile.TemporaryDirectory() as t:
            for fmt in ("yuv420p", "yuv422p10le"):
                p = os.path.join(t, fmt + ".mov")
                subprocess.run([dc.FFMPEG, "-hide_banner", "-v", "error", "-y", "-f", "lavfi", "-i", ramp,
                                "-c:v", "ffv1" if "10" in fmt else "libx264", "-pix_fmt", fmt, p], check=True)
                self.assertEqual(reframe.span_cuts(p, 0.0, 4.0), [], fmt)


if __name__ == "__main__":
    unittest.main()
