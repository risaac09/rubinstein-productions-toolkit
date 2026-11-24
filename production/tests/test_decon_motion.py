"""Tests for rpresolve.decon_motion: the rule on synthetic grey frames (numpy only), and real reads of
ffmpeg-made clips: a punch-in at a known time, and a region that lights at known times."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import delivercheck as dc  # noqa: E402

try:
    import numpy as np
    from rpresolve import decon_motion as dm
except ImportError:
    np = None

TEXTURE = "geq=lum='128+90*sin(X/5)*sin(Y/7)'"


def make(path, graph):
    subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-filter_complex", graph, "-map", "[v]",
                    "-c:v", "ffv1", path], check=True, stdin=subprocess.DEVNULL)


@unittest.skipIf(np is None, "numpy not installed")
class TestRule(unittest.TestCase):
    def frames(self, levels):
        return np.array([np.full((8, 8), v, np.uint8) for v in levels])

    def test_a_step_and_a_move(self):
        g = self.frames([50] * 20 + [120] * 20)                       # one jump that holds: a step
        got = dm.from_frames(g, 10)
        self.assertEqual([(e["t"], e["kind"]) for e in got], [(2.0, "step")])
        ramp = self.frames([50] * 10 + list(range(50, 150, 10)) + [150] * 10)   # eased over 2 s: a move
        got = dm.from_frames(ramp, 10)
        self.assertEqual([e["kind"] for e in got], ["move"])
        self.assertAlmostEqual(got[0]["t"], 1.0, delta=0.3)

    def test_quiet_floor_cut_and_flash(self):
        self.assertEqual(dm.from_frames(self.frames([50] * 20 + [53] * 20), 10), [])          # under the floor
        g = self.frames([50] * 20 + [120] * 20)
        self.assertEqual(dm.from_frames(g, 10, cuts=[2.0]), [])                                # a cut's own change
        self.assertEqual(dm.from_frames(self.frames([50] * 20 + [200] + [50] * 20), 10), [])   # a flash returns

    def test_roi_text(self):
        self.assertEqual(dm.parse_roi("1,2,30,40"), (1, 2, 30, 40))
        with self.assertRaises(ValueError):
            dm.parse_roi("1,2,3")


@unittest.skipIf(np is None, "numpy not installed")
@unittest.skipUnless(os.access(dc.FFMPEG, os.X_OK), "ffmpeg missing")
class TestReads(unittest.TestCase):
    def test_a_punch_in_inside_one_take(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "punch.mkv")
            graph = (f"color=c=gray:s=640x360:r=25:d=4,{TEXTURE},"
                     "scale=w='iw*(1+0.8*clip((t-2)/0.8,0,1))':h=-2:eval=frame,crop=640:360[v]")
            make(path, graph)
            got = dm.find(path)
            self.assertEqual(len(got), 1, got)
            self.assertAlmostEqual(got[0], 2.0, delta=0.4)

    def test_region_turns_on_and_off(self):
        with tempfile.TemporaryDirectory() as d:
            ref, rb = os.path.join(d, "ref.mkv"), os.path.join(d, "rb.mkv")
            box = "drawbox=x={x}:y=20:w=60:h=60:c=white:t=fill:enable='between(t,{a},{b})'"
            make(ref, "color=c=0x202020:s=320x180:r=25:d=4[b];[b]" + box.format(x=20, a=0.5, b=1.5) + ",drawbox=x=240:y=100:w=60:h=60:c=white:t=fill:enable='between(t,2.5,3.5)'[v]")
            make(rb, "color=c=0x202020:s=320x180:r=25:d=4[b];[b]" + box.format(x=20, a=0.6, b=1.7) + ",drawbox=x=240:y=100:w=60:h=60:c=white:t=fill:enable='between(t,2.5,3.5)'[v]")
            left, right = (0, 0, 160, 180), (160, 0, 160, 180)
            for got, want in zip([e["t"] for e in dm.events(ref, roi=left)], (0.5, 1.5)):
                self.assertAlmostEqual(got, want, delta=0.05)
            t = dm.region_table(ref, [left, right], rb)
            r = t["rows"]
            for got, want in zip(sum((x["ref"] for x in r), []), (0.5, 1.5, 2.5, 3.5)):
                self.assertAlmostEqual(got, want, delta=0.05)
            self.assertEqual([x["region"] for x in r], ["1", "2"])
            self.assertAlmostEqual(r[0]["diff"][0], 0.1, delta=0.05)
            self.assertAlmostEqual(r[0]["diff"][1], 0.2, delta=0.05)
            self.assertAlmostEqual(r[1]["diff"][0], 0.0, delta=0.05)
            self.assertIn("1 (region 1)", dm.summary_table(t))
            with self.assertRaises(dc.CheckError):
                dm.read(ref, (0, 0, 999, 10))


if __name__ == "__main__":
    unittest.main()
