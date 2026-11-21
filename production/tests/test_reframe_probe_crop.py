"""reframe.probe reports the decoded picture size: a stream's Frame Cropping
side data is applied, as measure.probe does. Synthetic clip, no footage."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from rpresolve import detect, reframe  # noqa: E402

FF = os.path.join(os.path.dirname(detect.FFPROBE), "ffmpeg")


class TestProbeFrameCropping(unittest.TestCase):
    def test_the_crop_helper_is_the_one_measure_uses(self):
        self.assertEqual(detect.cropped_size({"width": 1920, "height": 1080}), (1920, 1080))
        sd = {"side_data_list": [{"crop_top": 8, "crop_bottom": 8, "crop_left": 0, "crop_right": 0}]}
        self.assertEqual(detect.cropped_size({"width": 1920, "height": 1088, **sd}), (1920, 1072))

    @unittest.skipUnless(os.access(detect.FFPROBE, os.X_OK) and os.access(FF, os.X_OK), "ffmpeg missing")
    def test_probe_reports_the_cropped_size(self):
        from cropfix import add_clap
        with tempfile.TemporaryDirectory() as tmp:
            plain, out = os.path.join(tmp, "plain.mp4"), os.path.join(tmp, "cropped.mp4")
            subprocess.run([FF, "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=2",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", plain], check=True)
            add_clap(plain, out, 60, 60)
            listed = subprocess.run([detect.FFPROBE, "-v", "error", "-show_streams", out],
                                    capture_output=True, text=True).stdout
            if "Frame Cropping" not in listed:
                self.skipTest("this build does not read Frame Cropping from a clap box")
            info = reframe.probe(out)
        self.assertEqual((info["width"], info["height"]), (640, 240))


if __name__ == "__main__":
    unittest.main()
