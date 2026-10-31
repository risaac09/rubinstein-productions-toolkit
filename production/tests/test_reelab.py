"""
reel-ab on lavfi 1080x1920 fixtures at different loudness (synthetic; no client media).
Skips without ffmpeg. No numpy.

Run: /usr/bin/python3 -m unittest -v production.tests.test_reelab
"""

import contextlib
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rpresolve import cli, delivercheck as dc, reelab, reviewlog, tastedistill  # noqa: E402

try:
    dc.check_tools(ffmpeg=True)
    HAVE_FFMPEG = True
except dc.ToolMissing:
    HAVE_FFMPEG = False


def make(path, gain_db, w=1080, h=1920, seconds=4):
    subprocess.run([dc.FFMPEG, "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=s={w}x{h}:r=30:d={seconds}", "-f", "lavfi", "-i",
                    f"sine=f=440:r=48000:d={seconds}", "-af", f"volume={gain_db}dB", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path], check=True)


def top_level_atoms(path):
    out, size = [], os.path.getsize(path)
    with open(path, "rb") as f:
        pos = 0
        while pos < size:
            f.seek(pos)
            n, name = struct.unpack(">I4s", f.read(8))
            out.append(name.decode())
            pos += n or size - pos
    return out


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not found")
class ReelAbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = os.path.realpath(cls.tmp.name)
        cls.srcs = []
        for name, gain in (("a", -30), ("b", -20), ("c", -26)):
            p = os.path.join(cls.root, f"SW999_Test_{name}.mp4")
            make(p, gain)
            cls.srcs.append(p)
        cls.wide = os.path.join(cls.root, "SW999_Test_wide.mp4")
        make(cls.wide, -20, 1920, 1080)
        cls.out = os.path.join(cls.root, "packet")
        cls.packet = reelab.build(cls.srcs, cls.out, 0.5, 2.0, seed=11)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def probe(self, path):
        info = dc.probe(path)
        v = next(s for s in info["streams"] if s["codec_type"] == "video")
        return v, float(info["format"]["duration"])

    def test_outputs_probe_as_phone_video(self):
        for lab in "ABC":
            p = os.path.join(self.out, f"{lab}.mp4")
            v, dur = self.probe(p)
            self.assertEqual((v["width"], v["height"], v["pix_fmt"], v["profile"]), (720, 1280, "yuv420p", "High"))
            self.assertLessEqual(abs(dur - 2.0), 1 / 30)
            self.assertLess(os.path.getsize(p), 25 * 1024 * 1024)
            atoms = top_level_atoms(p)
            self.assertLess(atoms.index("moov"), atoms.index("mdat"))

    def test_loudness_matched(self):
        vals = [dc.measure_loudness(os.path.join(self.out, f"{lab}.mp4"))["integrated_lufs"] for lab in "ABC"]
        self.assertLessEqual(max(vals) - min(vals), 0.5)
        self.assertLessEqual(self.packet["spread_lu"], 0.5)

    def test_key_maps_every_letter_and_stays_out_of_the_folder(self):
        key_path = os.path.join(reelab.key_dir(self.out), "key.json")
        key = json.loads(Path(key_path).read_text())
        self.assertEqual(sorted(key["labels"]), ["A", "B", "C"])
        self.assertEqual({v["source"] for v in key["labels"].values()}, set(self.srcs))
        self.assertFalse(any("key" in n for n in os.listdir(self.out)))
        self.assertFalse(os.path.exists(os.path.join(self.out, "sealed")))

    def test_order_is_not_the_identity_across_seeds(self):
        for seed in range(50):
            self.assertNotEqual(reelab.order(3, seed), [0, 1, 2])
            self.assertNotEqual(reelab.order(2, seed), [0, 1])

    def test_order_equals_absheet_order(self):
        try:
            from rpstills import absheet
        except ImportError:
            self.skipTest("rpstills needs Pillow (the rp-tools venv)")
        for n in (2, 3, 4):
            for seed in range(20):
                self.assertEqual(reelab.order(n, seed), absheet.order(n, seed))

    def test_review_log_decodes_the_key(self):
        proj = os.path.join(self.root, "proj")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["review-log", "--dir", proj, "--project", "P-0001", "--step", "edit", "--device", "phone",
                             "--choice", "B", "--key", self.out, "--question", "reel_option"])
        self.assertEqual(code, 0, err.getvalue())
        events, bad = tastedistill.read_events(os.path.join(proj, "review", "events.jsonl"))
        self.assertEqual((len(events), bad), (1, 0))
        line = events[0][1]
        key = json.loads(Path(reelab.key_dir(self.out), "key.json").read_text())
        self.assertEqual(line["options"], {k: os.path.basename(v["source"]) for k, v in key["labels"].items()})
        self.assertEqual(line["stimulus"], reviewlog._sha256(os.path.join(self.out, "packet.json")))

    def test_refusals(self):
        with self.assertRaisesRegex(reelab.ReelAbError, "exists"):
            reelab.build(self.srcs, self.out, 0.5, 2.0)
        with tempfile.TemporaryDirectory() as g:
            subprocess.run(["git", "init", "-q", g], check=True)
            with self.assertRaisesRegex(reelab.ReelAbError, "git working tree"):
                reelab.build(self.srcs, os.path.join(g, "p"), 0.5, 2.0)
        new = os.path.join(self.root, "refused")
        with self.assertRaisesRegex(reelab.ReelAbError, "9:16"):
            reelab.build([self.srcs[0], self.wide], new, 0, 1.0)
        with self.assertRaisesRegex(reelab.ReelAbError, "past the shortest"):
            reelab.build(self.srcs, new, 3.5, 2.0)
        with self.assertRaisesRegex(reelab.ReelAbError, "named twice"):
            reelab.build([self.srcs[0], self.srcs[0]], new, 0, 1.0)
        with self.assertRaisesRegex(reelab.ReelAbError, "at most 30"):
            reelab.build(self.srcs, new, float("nan"), 1.0)
        with self.assertRaisesRegex(reelab.ReelAbError, "2 to 4"):
            reelab.build(self.srcs[:1], new, 0, 1.0)
        with self.assertRaisesRegex(reelab.ReelAbError, "at most 30"):
            reelab.build(self.srcs, new, 0, 31.0)
        self.assertFalse(os.path.exists(new) or os.path.exists(new + ".key"))

    def test_cli_refusal_exits_2(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = cli.main(["reel-ab", *self.srcs, "--out", self.out])
        self.assertEqual(code, 2)

    def test_opening_sheet(self):
        try:
            import PIL  # noqa: F401
            from rpstills import absheet  # noqa: F401
        except ImportError:
            self.skipTest("rpstills needs Pillow (the rp-tools venv)")
        out = os.path.join(self.root, "with-opening")
        reelab.build(self.srcs[:2], out, 0, 1.0, seed=3, opening=True)
        self.assertTrue(os.path.getsize(os.path.join(out, "frame0.jpg")) > 0)


if __name__ == "__main__":
    unittest.main()
