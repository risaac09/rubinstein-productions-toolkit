"""
Tests for rpresolve.manifest and the deliver-manifest command: a delivery
folder of tiny lavfi-made files gets manifest.md and checksums.txt, compared
with a golden manifest after the volatile cells (checksums, loudness) are
normalised; overwrite refusal; --verify catches a changed, missing and
unlisted file; `shasum -a 256 -c` accepts checksums.txt when it exists.

Run: /usr/bin/python3 -m unittest production.tests.test_manifest -v
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from rpresolve import manifest as mf  # noqa: E402
from test_delivercheck import CFG, make, needs_ffmpeg  # noqa: E402

WORKFLOW = HERE.parent / "resolve_workflow.py"
CLIP = "SW001_Guest_01_example-clip_16x9.mp4"
MASTER = "Client_example-slug_master.mov"
SHA = "a" * 64
ARGS = ["--project", "example-slug", "--approval", "AP-1", "--prepared-by", "A B, producer",
        "--review-copy", "review.mp4", "--review-sha256", SHA, "--delivered", "2026-10-20"]


def run(*args):
    return subprocess.run([sys.executable, str(WORKFLOW), *args], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True)


def normalise(text):
    text = re.sub(r"\b[0-9a-f]{64}\b", "<sha256>", text)
    return re.sub(r"-?\d+\.\d LUFS, -?\d+\.\d dBTP", "<loudness>", text)


GOLDEN = """\
# Delivery manifest: example-slug

| | |
|---|---|
| Project | example-slug |
| Delivered | 2026-10-20 |
| Prepared by | A B, producer |
| Approval reference | AP-1 |
| Approved review copy | review.mp4 |
| Review copy SHA-256 | <sha256> |
| Machine-assisted steps | none stated |
| Credits | none requested |
| Platform notes | none looked up |

## Files

| File | Format | Codec and container | Resolution | Frame rate | Duration | Loudness | Checksum | Destination | Approval reference |
|---|---|---|---|---|---|---|---|---|---|
| Client_example-slug_master.mov | mov | prores HQ, yuv422p10le | 64x36 | 25 | 00:00:02.00 | <loudness> | <sha256> | client_master | AP-1 |
| SW001_Guest_01_example-clip_16x9.mp4 | mp4 | h264, yuv420p | 64x36 | 25 | 00:00:02.00 | <loudness> | <sha256> | youtube_16x9 | AP-1 |
| SW001_Guest_01_example-clip_16x9.srt | srt | n/a | n/a | n/a | 00:00:01.50 (hundredths) | n/a | <sha256> | youtube_16x9 | AP-1 |

## Licences used

none
"""


@needs_ffmpeg
class TestManifest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = tempfile.mkdtemp()
        os.makedirs(f"{cls.src}/youtube_16x9")
        os.makedirs(f"{cls.src}/client_master")
        make(f"{cls.src}/youtube_16x9/{CLIP}", seconds=2.0, rate="25")
        make(f"{cls.src}/client_master/{MASTER}", codec="prores", seconds=2.0, rate="25")
        Path(f"{cls.src}/youtube_16x9/{CLIP[:-4]}.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,500\nHi\n")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.src, ignore_errors=True)

    def setUp(self):
        self.d = tempfile.mkdtemp()
        shutil.rmtree(self.d)
        shutil.copytree(self.src, self.d)
        self.addCleanup(shutil.rmtree, self.d, True)

    def test_golden_manifest_and_shasum(self):
        r = run("deliver-manifest", self.d, *ARGS)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(normalise((Path(self.d) / "manifest.md").read_text()), GOLDEN)
        lines = (Path(self.d) / "checksums.txt").read_text().splitlines()
        self.assertEqual([ln.split("  ", 1)[1] for ln in lines],
                         ["client_master/" + MASTER, "manifest.md", "youtube_16x9/" + CLIP,
                          "youtube_16x9/" + CLIP[:-4] + ".srt"])
        if shutil.which("shasum"):
            p = subprocess.run(["shasum", "-a", "256", "-c", "checksums.txt"], cwd=self.d,
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_checksum_column_matches_bytes(self):
        run("deliver-manifest", self.d, *ARGS)
        text = (Path(self.d) / "manifest.md").read_text()
        want = mf.sha256(f"{self.d}/youtube_16x9/{CLIP}")
        self.assertIn(f"| {want} |", text)

    def test_refuses_overwrite_unless_asked(self):
        self.assertEqual(run("deliver-manifest", self.d, *ARGS).returncode, 0)
        before = (Path(self.d) / "manifest.md").read_text()
        r = run("deliver-manifest", self.d, *ARGS, "--project", "other")
        self.assertEqual(r.returncode, 1)
        self.assertIn("already exists", r.stderr)
        self.assertEqual((Path(self.d) / "manifest.md").read_text(), before)
        self.assertEqual(run("deliver-manifest", self.d, *ARGS, "--overwrite").returncode, 0)

    def test_verify_finds_changed_missing_and_unlisted(self):
        run("deliver-manifest", self.d, *ARGS)
        r = run("deliver-manifest", self.d, "--verify")
        self.assertEqual(r.returncode, 0, r.stdout)
        with open(f"{self.d}/youtube_16x9/{CLIP}", "ab") as f:
            f.write(b"x")
        os.remove(f"{self.d}/client_master/{MASTER}")
        shutil.copy(f"{self.d}/youtube_16x9/{CLIP}", f"{self.d}/youtube_16x9/Extra_x_master.mp4")
        r = run("deliver-manifest", self.d, "--verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"youtube_16x9/{CLIP}: FAILED", r.stdout)
        self.assertIn(f"client_master/{MASTER}: MISSING", r.stdout)
        self.assertIn("youtube_16x9/Extra_x_master.mp4: NOT LISTED", r.stdout)

    def test_verify_accepts_dot_slash_paths_and_reports_strays(self):
        run("deliver-manifest", self.d, *ARGS)
        c = Path(self.d) / "checksums.txt"
        c.write_text("".join(ln.replace("  ", "  ./", 1) + "\n" for ln in c.read_text().splitlines()))
        Path(f"{self.d}/youtube_16x9/notes.txt").write_text("x")
        r = run("deliver-manifest", self.d, "--verify")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.count("OK"), 4)
        self.assertIn("youtube_16x9/notes.txt: NOT LISTED", r.stdout)

    def test_timecode_rounds_to_the_frame(self):
        from fractions import Fraction
        self.assertEqual(mf.timecode(10.01, Fraction(30000, 1001)), "00:00:10.00")

    def test_refusals(self):
        # a name that fails the house rule does not ship
        shutil.copy(f"{self.d}/youtube_16x9/{CLIP}", f"{self.d}/youtube_16x9/bad name.mp4")
        r = run("deliver-manifest", self.d, *ARGS)
        self.assertEqual(r.returncode, 1)
        self.assertIn("bad name.mp4", r.stderr)
        self.assertFalse(os.path.exists(f"{self.d}/manifest.md"))
        os.remove(f"{self.d}/youtube_16x9/bad name.mp4")
        # approval and the review copy are required
        r = run("deliver-manifest", self.d, "--project", "example-slug")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--approval", r.stderr)
        # a destination folder the config does not know
        os.rename(f"{self.d}/youtube_16x9", f"{self.d}/nowhere")
        self.assertEqual(run("deliver-manifest", self.d, *ARGS).returncode, 1)

    def test_licence_rows(self):
        text, _ = mf.build(self.d, CFG, "example-slug", "AP-1", "p", "r.mp4", SHA, "2026-10-20",
                           licences=["Track A|CC-BY 4.0|Credit the artist"], loudness=False)
        self.assertIn("| Track A | CC-BY 4.0 | Credit the artist |", text)
        self.assertIn("| n/a |", text.split("## Files")[1])


if __name__ == "__main__":
    unittest.main()
