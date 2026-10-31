"""
vertcut review variants, ledger row stubs, the destination loudness default and the 59.94 output rate.
Synthetic lavfi sources; skips without Pillow or ffmpeg.

Run: /usr/bin/python3 -m unittest -v production.tests.test_vertcut_review
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
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "production"))
CUT = ROOT / "tools" / "vertcut" / "lib" / "vertcut_cut.py"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or FFMPEG.replace("ffmpeg", "ffprobe")

try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

NEEDS = unittest.skipUnless(HAVE_PIL and os.access(FFMPEG, os.X_OK), "Pillow or ffmpeg missing")
NAME = ("--show", "SW", "--episode", "999", "--guest", "Test")


def load():
    spec = importlib.util.spec_from_file_location("vertcut_cut", CUT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@NEEDS
class TestOutRate(unittest.TestCase):
    def test_rates(self):
        v = load()
        for src, want in ((25.0, Fraction(25)), (30000 / 1001, Fraction(30000, 1001)), (30.0, Fraction(30)),
                          (60000 / 1001, Fraction(30000, 1001)), (60.0, Fraction(30)), (50.0, Fraction(30))):
            with self.subTest(src=src):
                self.assertEqual(v.out_rate(src), want)


@NEEDS
class TestReviewAndRelease(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = os.path.realpath(cls.tmp.name)
        cls.src = os.path.join(cls.root, "SW999_Test_src.mp4")
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=1280x720:rate=60000/1001:duration=3",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", cls.src], check=True)
        cls.tsv = os.path.join(cls.root, "clips.tsv")
        Path(cls.tsv).write_text("1\t0.2\t2.2\tA test\n")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def cut(self, *extra):
        return subprocess.run([sys.executable, str(CUT), self.src, self.tsv, "--no-captions", *NAME, *extra],
                              capture_output=True, text=True)

    def test_ledger_row_destination_loudness_and_rate(self):
        out, ledger = os.path.join(self.root, "led-out"), os.path.join(self.root, "ledger.jsonl")
        p = self.cut("-o", out, "--destination", "social_9x16", "--ledger-row", ledger)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("--lufs not given; using social_9x16's target, -14 LUFS", p.stderr)
        rows = [json.loads(line) for line in Path(ledger).read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["stub"], rows[0]["file"], rows[0]["key"], rows[0]["destination"]),
                         (True, "SW999_Test_01_a-test_9x16.mp4", "SW999_Test_01_a-test_9x16.mp4", "social_9x16"))
        self.assertEqual(len(rows[0]["sha256"]), 64)
        rate = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                               "-of", "csv=p=0", os.path.join(out, rows[0]["file"])], capture_output=True, text=True)
        self.assertEqual(rate.stdout.strip(), "30000/1001")   # a 59.94 source, not 30
        again = self.cut("-o", os.path.join(self.root, "led-out2"), "--ledger-row", ledger)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(len(Path(ledger).read_text().splitlines()), 2)   # added to, never replaced

    def test_an_explicit_lufs_is_not_announced_and_a_ledger_in_a_repo_is_refused(self):
        out = os.path.join(self.root, "quiet-out")
        p = self.cut("-o", out, "--destination", "social_9x16", "--lufs", "-16", "--dry-run")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("--lufs not given", p.stderr)
        repo = os.path.join(self.root, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        p = self.cut("-o", out, "--ledger-row", os.path.join(repo, "l.jsonl"), "--dry-run")
        self.assertEqual(p.returncode, 1)
        self.assertIn("git working tree", p.stderr)

    def test_review_packet_key_decodes_through_review_log(self):
        from rpresolve import cli, reelab, reviewlog, tastedistill
        packet = os.path.join(self.root, "packet")
        p = self.cut("-o", packet, "--only", "1", "--review", "--review-seed", "5",
                     "--variant", "x=-0.8", "--variant", "x=0.8")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(sorted(os.listdir(packet)), ["A.mp4", "B.mp4", "README.txt", "packet.json"])
        key = json.loads(Path(reelab.key_dir(packet), "key.json").read_text())
        names = ["variant1-x--0.8.mp4", "variant2-x-0.8.mp4"]
        self.assertEqual(sorted(os.path.basename(v["source"]) for v in key["labels"].values()), names)
        proj = os.path.join(self.root, "proj")
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = cli.main(["review-log", "--dir", proj, "--project", "P-0001", "--step", "edit", "--device", "phone",
                             "--choice", "A", "--key", packet, "--question", "reel_option"])
        self.assertEqual(code, 0, err.getvalue())
        events, bad = tastedistill.read_events(os.path.join(proj, "review", "events.jsonl"))
        self.assertEqual((len(events), bad), (1, 0))
        self.assertEqual(sorted(events[0][1]["options"].values()), names)
        self.assertEqual(events[0][1]["stimulus"], reviewlog._sha256(os.path.join(packet, "packet.json")))

    def test_review_refusals(self):
        out = os.path.join(self.root, "refused")
        cases = (("--only", "1", "--review", "--variant", "x=0"),                         # one variant
                 ("--only", "1", "--review", "--variant", "x=0", "--variant", "x=0"),     # the same twice
                 ("--review", "--variant", "x=0", "--variant", "x=1"),                    # no --only
                 ("--only", "1", "--review", "--variant", "x=0", "--variant", "hook=on"),  # no hook in the row
                 ("--only", "1", "--review", "--variant", "x=0", "--variant", "bogus=1"),
                 ("--only", "1", "--variant", "x=0"))                                     # variants without --review
        for extra in cases:
            with self.subTest(extra=extra):
                p = self.cut("-o", out, *extra)
                self.assertEqual(p.returncode, 1, p.stderr)
                self.assertFalse(os.path.exists(out))


if __name__ == "__main__":
    unittest.main()
