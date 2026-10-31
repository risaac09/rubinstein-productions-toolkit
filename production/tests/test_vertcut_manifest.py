"""
Tests for `vertcut manifest`: a synthetic cuts worksheet becomes a version 2 cut
manifest that rpresolve reads back with the same in/out the worksheet gives
rpresolve.cutlist directly, at 29.97 and 23.976 as well as whole rates. Sources
come from ffmpeg lavfi; nothing here is real media.

Run: /usr/bin/python3 -m unittest discover production/tests -p test_vertcut_manifest.py -v
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "tools" / "vertcut" / "lib"
sys.path.insert(0, str(ROOT / "production"))
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"

try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

WORDS = ("alpha bravo charlie delta echo foxtrot golf hotel the india juliet kilo lima mike "
         "november oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee").split()
RATES = ((29.97, "30000/1001"), (23.976, "24000/1001"), (25.0, "25"))


def load():
    sys.path.insert(0, str(LIB))
    spec = importlib.util.spec_from_file_location("vertcut_manifest", LIB / "vertcut_manifest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_words(path):
    """One word every half second, each 0.4 s long; the ninth is a dangling "the"."""
    with open(path, "w") as f:
        json.dump({"segments": [{"words": [{"word": " " + w, "start": i * 0.5, "end": i * 0.5 + 0.4}
                                           for i, w in enumerate(WORDS)]}]}, f)


@unittest.skipUnless(HAVE_PIL, "Pillow missing (vertcut needs it)")
class TestManifest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vm = load()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = os.path.realpath(cls.tmp.name)
        cls.words = os.path.join(cls.dir, "words.json")
        write_words(cls.words)
        cls.src = os.path.join(cls.dir, "src.mov")
        Path(cls.src).write_bytes(b"synthetic")
        cls.tsv = os.path.join(cls.dir, "clips.tsv")
        with open(cls.tsv, "w") as f:
            f.write("# id\tin\tout\ttitle\n"
                    "1\t00:00:02:15\t00:00:05:10\ttimecode row\n"
                    "2\t6.5\t8.9\tseconds row\n"
                    "3\t0:09\t0:11\tminutes row\n")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def make(self, tsv, fps, offset=0.0, name="m.json"):
        out = os.path.join(self.dir, name)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            self.vm.main([self.src, tsv, "--words", self.words, "-o", out,
                          "--fps", str(fps), "--offset", str(offset)])
        return out, err.getvalue()

    def test_round_trip_matches_cutlist_at_ntsc_and_whole_rates(self):
        from rpresolve import cutlist
        for fps, _ in RATES:
            for offset in (0.0, 1.5):
                with self.subTest(fps=fps, offset=offset):
                    out, _ = self.make(self.tsv, fps, offset)
                    m = cutlist.load_manifest(out)  # the v2 validator
                    self.assertEqual(m["version"], 2)
                    want = cutlist.spans_from_vertcut_tsv(self.tsv, fps, offset)
                    got = {c["name"]: [(s["in"], s["out"]) for s in c["spans"]] for c in m["clips"]}
                    self.assertEqual(got, want)

    def test_timecode_row_is_ntsc_correct(self):
        from rpresolve import cutlist
        out, _ = self.make(self.tsv, 29.97)
        span = cutlist.load_manifest(out)["clips"][0]["spans"][0]
        self.assertAlmostEqual(span["in"], (2 * 30 + 15) * 1001 / 30000, places=9)
        self.assertAlmostEqual(span["out"], (5 * 30 + 10) * 1001 / 30000, places=9)

    def test_unpadded_timecode_is_refused(self):
        bad = os.path.join(self.dir, "bad.tsv")
        Path(bad).write_text("1\t0:0:1:5\t0:0:2:0\tx\n")
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.make(bad, 29.97, name="bad.json")

    def test_mid_word_cut_warns_and_dangling_end_fails_endcheck(self):
        from rpresolve import cutlist
        ws = os.path.join(self.dir, "edge.tsv")
        with open(ws, "w") as f:
            f.write("1\t0.0\t2.45\tclean end\n"     # after "echo" (2.0-2.4)
                    "2\t0.0\t3.2\tinside a word\n"   # inside "golf" (3.0-3.4)
                    "3\t0.0\t4.45\tends on the\n")   # after "the" (4.0-4.4)
        out, err = self.make(ws, 25.0, name="edge.json")
        self.assertIn("2 out at 3.200 s is inside the word 'golf'", err)
        self.assertNotIn("1 out", err)
        rows = cutlist.endcheck(cutlist.load_manifest(out), audio=False)
        self.assertEqual([r["ok"] for r in rows], [True, True, False])
        self.assertIn("the", rows[2]["reason"])

    def test_negative_offset_past_zero_is_refused(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.make(self.tsv, 25.0, offset=-3.0, name="neg.json")

    def test_output_inside_a_repository_is_refused(self):
        inside = ROOT / "vertcut-manifest-refused.json"
        self.addCleanup(lambda: inside.exists() and inside.unlink())
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.vm.main([self.src, self.tsv, "--words", self.words, "-o", str(inside), "--fps", "25"])
        self.assertFalse(inside.exists())


@unittest.skipUnless(HAVE_PIL and os.access(FFMPEG, os.X_OK), "ffmpeg or Pillow missing")
class TestFcpxml(unittest.TestCase):
    def test_fcpxml_frame_counts_match_the_manifest(self):
        from rpresolve import cutlist, interchange
        vm = load()
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            words = os.path.join(d, "w.json")
            write_words(words)
            for fps, rate in RATES:
                with self.subTest(fps=fps):
                    src = os.path.join(d, "SW999_Test_%s.mov" % rate.replace("/", "_"))
                    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                                    "testsrc2=size=64x36:rate=%s:duration=12" % rate,
                                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", src],
                                   stdin=subprocess.DEVNULL, check=True)
                    tsv = os.path.join(d, "c.tsv")
                    Path(tsv).write_text("1\t00:00:02:15\t00:00:05:10\ta\n")
                    m = vm.build(src, tsv, words, fps)
                    text, rep = interchange.build(cutlist.validate_manifest(m), "fcpxml", clip="1")
                    ET.fromstring(text)
                    want = cutlist.clip_frames(m["clips"][0], Fraction(rate))
                    self.assertEqual(rep["frames"], want)
                    self.assertTrue(rep["probed"])


if __name__ == "__main__":
    unittest.main()
