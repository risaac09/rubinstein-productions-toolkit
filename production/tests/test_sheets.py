"""
Tests for `sheets` (rpresolve/cmd_sheets.py) and the draft farm's proxy lookup and software
retry, on synthetic clips: a cell per frame, labelled with stem and second, a squeeze record
stretches a cell and an unanswered clip says so, a Proxy folder and .mp4 proxies are found,
and a failed hardware job is drawn again in software.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rpresolve import cmd_sheets, drafts  # noqa: E402

FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
try:
    import PIL  # noqa: F401
except ImportError:
    PIL = None


def clip(path, secs=4, size="320x180"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=s={size}:r=24:d={secs}", "-pix_fmt", "yuv420p", path], check=True)


class Tmp(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sheets-test-")
        self.addCleanup(shutil.rmtree, self.dir, True)


class ProxyLookup(Tmp):
    def test_mp4_is_accepted_like_mov(self):
        Path(self.dir, "A001.mp4").write_text("")
        self.assertEqual(drafts.media_path("/w/A001.braw", self.dir),
                         os.path.join(self.dir, "A001.mp4"))

    def test_camera_proxy_folder_in_a_card_copy(self):
        card = os.path.join(self.dir, "BMD-abc123")
        Path(self.dir, "BMD-abc123.card.json").write_text("{}")
        os.makedirs(os.path.join(card, "Proxy"))
        os.makedirs(os.path.join(card, "Clips"))
        Path(card, "Proxy", "A002.MP4").write_text("")
        got = drafts.media_path(os.path.join(card, "Clips", "A002.braw"))
        self.assertEqual(got, os.path.join(card, "Proxy", "A002.MP4"))


class SoftwareRetry(Tmp):
    def test_hardware_failure_retries_once_without_hwaccel(self):
        out = os.path.join(self.dir, "o.mp4")
        job = {"cmd": ["ffmpeg", "-hwaccel", "videotoolbox", "-i", "x"], "out": out}
        calls = []

        def fake(cmd, **kw):
            calls.append(list(cmd))
            if len(calls) == 2:
                Path(out).write_text("x")
            return mock.Mock(returncode=0 if len(calls) == 2 else 1, stderr="no hw", stdout="")

        with mock.patch.object(drafts.subprocess, "run", fake):
            drafts.run_job(job)
        self.assertEqual(len(calls), 2)
        self.assertIn("-hwaccel", calls[0])
        self.assertNotIn("-hwaccel", calls[1])
        self.assertIn("software_retry", job)

    def test_software_failure_is_not_retried(self):
        job = {"cmd": ["ffmpeg", "-i", "x"], "out": os.path.join(self.dir, "o.mp4")}
        with mock.patch.object(drafts.subprocess, "run",
                               return_value=mock.Mock(returncode=1, stderr="bad", stdout="")) as run:
            with self.assertRaises(drafts.DraftError):
                drafts.run_job(job)
        self.assertEqual(run.call_count, 1)


@unittest.skipUnless(FFMPEG and PIL, "needs ffmpeg and Pillow")
class Sheets(Tmp):
    def draw(self, folder, **kw):
        out = os.path.join(self.dir, "out")
        r = cmd_sheets.make(folder, out, **kw)
        with open(os.path.join(out, "sheets.json")) as fh:
            return r, json.load(fh)

    def test_cells_labels_and_squeeze(self):
        card = os.path.join(self.dir, "card")
        clip(os.path.join(card, "DCIM", "a.mp4"))
        clip(os.path.join(card, "DCIM", "b.mp4"))
        clip(os.path.join(card, "Proxy", "skipped.mp4"))
        Path(card, "squeeze.json").write_text(json.dumps({"clips": {"a.mp4": 1.5}}))
        r, idx = self.draw(card, per_clip=3, cols=3, rows=1)
        self.assertEqual((r["clips"], r["cells"], len(r["sheets"])), (2, 6, 2))
        a = [c for c in idx if c["path"].endswith("a.mp4")]
        self.assertEqual([c["second"] for c in a], [0.667, 2.0, 3.333])
        self.assertTrue(all(os.path.isabs(c["path"]) for c in idx))
        self.assertEqual({c["squeeze"] for c in a}, {1.5})
        self.assertEqual({c["squeeze"] for c in idx if c["path"].endswith("b.mp4")}, {None})
        self.assertEqual(cmd_sheets.label("/x/a.mp4", 2.0, None), ("a 2.0s", "squeeze open"))
        self.assertEqual(cmd_sheets.label("/x/a.mp4", 2.0, 1.5), ("a 2.0s", "1.5x"))
        self.assertEqual(cmd_sheets.label("/x/a.mp4", 2.0, 1), ("a 2.0s", ""))
        self.assertEqual((idx[0]["sheet"], idx[0]["row"], idx[0]["col"]), (1, 1, 1))

    def test_refuses_output_in_a_git_tree(self):
        subprocess.run(["git", "init", "-q", os.path.join(self.dir, "repo")], check=True)
        args = mock.Mock(folder=self.dir, out=os.path.join(self.dir, "repo", "o"),
                         per_clip=1, cols=1, rows=1, proxy_dir=None)
        self.assertEqual(cmd_sheets.run(args), 1)


if __name__ == "__main__":
    unittest.main()
