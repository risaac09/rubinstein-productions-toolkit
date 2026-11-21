"""Tests for decon fetch. yt-dlp is mocked (no network); the "download" is a tiny ffmpeg clip."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import cmd_decon, decon_fetch, delivercheck as dc, paths  # noqa: E402

URL = "https://example.com/video/abc123"


def make_clip(path, seconds=2):
    subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c=red:s=64x64:r=25:d={seconds}", "-pix_fmt", "yuv420p", path],
                   check=True, stdin=subprocess.DEVNULL)


def fake_ytdlp(out_dir, claimed=2.0):
    """A subprocess.run stand-in that writes what yt-dlp would."""
    calls = []

    def run(cmd, **kw):
        if os.path.basename(cmd[0]) == "yt-dlp" or cmd[0] == "/fake/yt-dlp":
            calls.append(cmd)
            make_clip(os.path.join(out_dir, "abc123.mp4"))
            with open(os.path.join(out_dir, "abc123.info.json"), "w") as f:
                json.dump({"id": "abc123", "title": "T", "uploader": "U", "duration": claimed,
                           "fps": 25, "width": 64, "height": 64}, f)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, **kw)
    return run, calls


real_run = subprocess.run


def _parse(argv):
    p = argparse.ArgumentParser()
    cmd_decon.add_arguments(p)
    return p.parse_args(argv)


@unittest.skipUnless(os.access(dc.FFMPEG, os.X_OK), "ffmpeg missing")
class TestFetch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "refs")
        os.makedirs(self.out)
        # tmp may sit under a git tree on some machines; the real check is tested separately
        patcher = mock.patch.object(paths, "out_problem", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_writes_ref_json(self):
        run, calls = fake_ytdlp(self.out)
        with mock.patch("subprocess.run", run):
            rec = decon_fetch.fetch(URL, self.out, "/fake/yt-dlp")
        self.assertNotIn("shell", str(calls))
        self.assertEqual(calls[0][calls[0].index("-o") + 1], os.path.join(self.out, "%(id)s.%(ext)s"))
        for flag in ("--write-info-json", "--no-playlist"):
            self.assertIn(flag, calls[0])
        with open(os.path.join(self.out, "abc123.ref.json")) as f:
            disk = json.load(f)
        self.assertEqual(disk, rec)
        for k, v in {"url": URL, "id": "abc123", "title": "T", "uploader": "U", "duration": 2.0,
                     "fps": 25, "width": 64, "height": 64, "file": "abc123.mp4"}.items():
            self.assertEqual(disk[k], v, k)
        self.assertEqual(len(disk["sha256"]), 64)
        self.assertRegex(disk["fetched_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_duration_mismatch_fails(self):
        run, _ = fake_ytdlp(self.out, claimed=5.0)
        with mock.patch("subprocess.run", run), self.assertRaises(dc.CheckError) as cm:
            decon_fetch.fetch(URL, self.out, "/fake/yt-dlp")
        self.assertIn("duration mismatch", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(self.out, "abc123.ref.json")))

    def test_no_video_stream_fails(self):
        def run(cmd, **kw):
            if cmd[0] == "/fake/yt-dlp":
                subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                                "sine=d=2", os.path.join(self.out, "abc123.m4a")], check=True)
                with open(os.path.join(self.out, "abc123.info.json"), "w") as f:
                    json.dump({"id": "abc123", "duration": 2.0}, f)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return real_run(cmd, **kw)
        with mock.patch("subprocess.run", run), self.assertRaises(dc.CheckError) as cm:
            decon_fetch.fetch(URL, self.out, "/fake/yt-dlp")
        self.assertIn("no video stream", str(cm.exception))

    def test_non_http_refused_before_ytdlp(self):
        with mock.patch("subprocess.run") as m:
            for bad in ("file:///etc/passwd", "ftp://x/y", "javascript:alert(1)", "abc", "https://"):
                with self.assertRaises(ValueError):
                    decon_fetch.fetch(bad, self.out, "/fake/yt-dlp")
        m.assert_not_called()

    def test_missing_ytdlp(self):
        with mock.patch("shutil.which", return_value=None), self.assertRaises(dc.ToolMissing) as cm:
            decon_fetch.fetch(URL, self.out)
        self.assertIn("brew install yt-dlp", str(cm.exception))

    def test_cli_exit_codes(self):
        with redirect_stderr(StringIO()) as se, mock.patch("shutil.which", return_value=None):
            self.assertEqual(cmd_decon.run(_parse(["fetch", URL, "--out-dir", self.out])), 1)
        self.assertIn("yt-dlp", se.getvalue())
        run, _ = fake_ytdlp(self.out)
        so = StringIO()
        with mock.patch("subprocess.run", run), redirect_stdout(so), redirect_stderr(StringIO()):
            code = cmd_decon.run(_parse(["fetch", URL, "--out-dir", self.out, "--yt-dlp", "/fake/yt-dlp"]))
        self.assertEqual(code, 0)
        self.assertIn("abc123", so.getvalue())


class TestFetchRefusals(unittest.TestCase):
    def test_git_tree_out_dir_refused(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".git"))
            with mock.patch("subprocess.run") as m, self.assertRaises(paths.OutputRefused):
                decon_fetch.fetch(URL, os.path.join(d, "refs"), "/fake/yt-dlp")
            m.assert_not_called()


if __name__ == "__main__":
    unittest.main()
