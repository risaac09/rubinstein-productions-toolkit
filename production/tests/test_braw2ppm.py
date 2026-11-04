"""
Tests for production/lutlab/braw: the PPM writer on its own, with any C++
compiler, and the helper on a real clip where the Blackmagic RAW SDK is
installed. The SDK's sample clip holds one frame, so a late frame of a real
clip needs BRAW_TEST_CLIP, a clip of your own.

Run: python3 -m unittest production.tests.test_braw2ppm -v
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

BRAW = Path(__file__).resolve().parent.parent / "lutlab" / "braw"
CXX = shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
SDK = Path(os.environ.get("BRAW_SDK", "/Applications/Blackmagic RAW/Blackmagic RAW SDK/Mac"))
SAMPLE = SDK.parent / "Media" / "sample.braw"

# Frames whose buffers run past their pixels, as the SDK's do; pixel i of frame f is f*1000+i.
DRIVER = r"""
#include "ppmwriter.h"
int main() {
    const uint32_t w = 5, h = 3; const size_t pad = 4096;
    for (uint16_t f = 0; f < 40; f++) {
        std::vector<uint16_t> buf((size_t)w * h * 3 + pad, 0xBEEF);
        for (size_t i = 0; i < (size_t)w * h * 3; i++) buf[i] = f * 1000 + i;
        if (!write_ppm16(stdout, buf.data(), w, h)) return 1;
    }
}
"""


def ppm_frames(data):
    """[(width, height, pixel bytes)] from PPMs back to back, each read by its own header."""
    frames, at = [], 0
    while at < len(data):
        end = at
        for _ in range(3):
            end = data.index(b"\n", end) + 1
        magic, size, peak = data[at:end].split(b"\n")[:3]
        assert (magic, peak) == (b"P6", b"65535"), data[at:end]
        w, h = map(int, size.split())
        frames.append((w, h, data[end:end + w * h * 6]))
        at = end + w * h * 6
    return frames


@unittest.skipUnless(CXX, "needs a C++ compiler")
class Writer(unittest.TestCase):
    def test_a_late_frame_of_a_stream_is_whole(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, exe = os.path.join(tmp, "d.cpp"), os.path.join(tmp, "d")
            Path(src).write_text(DRIVER)
            subprocess.run([CXX, "-std=c++14", f"-I{BRAW}", src, "-o", exe], check=True)
            data = subprocess.run([exe], capture_output=True, check=True).stdout
        frames = ppm_frames(data)
        self.assertEqual(len(frames), 40)
        for f in (0, 39):  # padding that leaked would add up, so the last frame is the one that shows it
            w, h, px = frames[f]
            self.assertEqual((w, h), (5, 3))
            self.assertEqual(px, b"".join((f * 1000 + i).to_bytes(2, "big") for i in range(45)))


@unittest.skipUnless(SAMPLE.exists() and shutil.which("clang++"), "needs the Blackmagic RAW SDK")
class Helper(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.exe = os.path.join(cls.tmp.name, "braw2ppm")
        subprocess.run(["sh", str(BRAW / "build.sh")], check=True, capture_output=True,
                       env=dict(os.environ, BRAW2PPM_OUT=cls.exe))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_tool(self, *args):
        return subprocess.run([self.exe, *map(str, args)], capture_output=True)

    def test_file_and_stream_hold_the_same_frame(self):
        out = os.path.join(self.tmp.name, "f.ppm")
        self.assertEqual(self.run_tool(SAMPLE, 0, out).returncode, 0)
        streamed = self.run_tool(SAMPLE, "last", 1, "-")
        self.assertEqual(streamed.returncode, 0, streamed.stderr)
        self.assertEqual(Path(out).read_bytes(), streamed.stdout)
        self.assertIn(b"header 4608x2592 decoded 4608x2592", streamed.stderr)

    def test_spherical_opens_the_clip(self):
        self.assertEqual(self.run_tool("--spherical", SAMPLE, "mid", 3, "-").returncode, 0)

    def test_start_past_the_end_fails(self):
        self.assertEqual(self.run_tool(SAMPLE, 99999, "-").returncode, 4)

    def test_usage(self):
        self.assertEqual(self.run_tool(SAMPLE).returncode, 1)
        self.assertEqual(self.run_tool(SAMPLE, 0, 0, "-").returncode, 1)
        for start in ("-1", "end", "2x", "xs", "-1s"):
            self.assertEqual(self.run_tool(SAMPLE, start, "-").returncode, 1, start)

    @unittest.skipUnless(os.environ.get("BRAW_TEST_CLIP"), "set BRAW_TEST_CLIP to a clip of many frames")
    def test_late_frames_of_a_real_clip(self):
        clip = os.environ["BRAW_TEST_CLIP"]
        last = self.run_tool(clip, "last", "-")
        self.assertEqual(last.returncode, 0, last.stderr)
        n = int(last.stderr.split(b" frames ")[1].split()[0])
        tail = ppm_frames(self.run_tool(clip, n - 3, 3, "-").stdout)
        self.assertEqual(len(tail), 3)
        self.assertEqual(tail[-1], ppm_frames(last.stdout)[0])


if __name__ == "__main__":
    unittest.main()
