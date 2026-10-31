"""
Tests for rpresolve.scene (scene_cli.swift). Synthetic PNGs written here with
the standard library; skipped without swiftc / macOS.
"""

import os
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import scene  # noqa: E402

W, H = 160, 120


def write_png(path, pixel):
    rows = b"".join(b"\x00" + b"".join(bytes((pixel(x, y),) * 3) for x in range(W)) for y in range(H))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def checker(x, y, shift=0):
    return 255 if ((x + shift) // 8 + y // 8) % 2 else 0


def soft(x, y):
    return 108 + 40 * ((x // 8 + y // 8) % 2)


@unittest.skipUnless(sys.platform == "darwin", "needs macOS")
class SceneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not scene.scene_binary():
            raise unittest.SkipTest("swiftc build unavailable")
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        cls.sharp, cls.soft, cls.shifted = (os.path.join(d, n) for n in ("sharp.png", "soft.png", "shifted.png"))
        write_png(cls.sharp, checker)
        write_png(cls.soft, soft)
        write_png(cls.shifted, lambda x, y: checker(x, y, 4))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_sharp_beats_soft(self):
        self.assertGreater(scene.sharpness(self.sharp)["frame"], scene.sharpness(self.soft)["frame"])

    def test_motion(self):
        same = scene.motion(self.sharp, self.sharp)["mad"]
        moved = scene.motion(self.sharp, self.shifted)["mad"]
        self.assertLess(same, 0.001)
        self.assertGreater(moved, same + 0.05)

    def test_saliency_box_inside_frame(self):
        doc = scene.saliency(self.sharp)
        self.assertEqual((doc["width"], doc["height"]), (W, H))
        for b in doc["attention"] + doc["objectness"]:
            self.assertGreaterEqual(b["x"], -1e-6)
            self.assertGreaterEqual(b["y"], -1e-6)
            self.assertLessEqual(b["x"] + b["w"], 1 + 1e-6)
            self.assertLessEqual(b["y"] + b["h"], 1 + 1e-6)

    def test_sharpness_ratio_and_read(self):
        doc = scene.sharpness(self.sharp)
        if doc["ratio"] is not None:
            self.assertAlmostEqual(doc["ratio"], doc["region"] / doc["frame"])
        got = scene.read(self.sharp, previous=self.shifted)
        self.assertEqual(got["depth"], {"depth": None, "reason": "no model"})
        self.assertIn("motion", got)

    def test_depth_no_model(self):
        self.assertEqual(scene.depth(self.sharp), {"depth": None, "reason": "no model"})


if __name__ == "__main__":
    unittest.main()
