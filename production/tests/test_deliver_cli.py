"""
Tests for the resolve_workflow.py deliver commands: deliver-check and
deliver-fix-loudness run as subprocesses on ffmpeg-made media (exit 0 pass,
1 fail, 2 tool missing; a config overlay given before or after the
command). Queueing a render is the MCP queue_render tool's
(test_deliver_queue.py).

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from rpresolve import deliver  # noqa: E402
from test_delivercheck import SRT, make, needs_ffmpeg  # noqa: E402

WORKFLOW = HERE.parent / "resolve_workflow.py"
NAME = "SW001_Guest_01_example-clip_16x9.mp4"
# Test-sized frames for the two destinations used here, as an overlay.
OVERLAY = {"destinations": {"linkedin_16x9": {"resolution": {"width": 64, "height": 36}},
                            "youtube_16x9": {"resolution": {"width": 64, "height": 36}}}}


def run(*args, env=None):
    return subprocess.run([sys.executable, str(WORKFLOW), *args], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, env={**os.environ, **(env or {})})


@needs_ffmpeg
class TestCheckCommands(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = os.path.realpath(cls.tmp.name)
        cls.config = os.path.join(cls.dir, "overlay.json")
        with open(cls.config, "w", encoding="utf-8") as f:
            json.dump(OVERLAY, f)
        cls.good = make(os.path.join(cls.dir, NAME))
        with open(deliver.sidecar_path(cls.good), "w", encoding="utf-8") as f:
            f.write(SRT)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_pass_fail_and_tool_missing(self):
        p = run("deliver-check", self.good, "--dest", "linkedin_16x9", "--config", self.config,
                "--fps", "23.976")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("PASS  loudness", p.stdout)
        p = run("--config", self.config, "deliver-check", self.good, "--dest", "youtube_16x9",
                "--json")
        self.assertEqual(p.returncode, 1)  # -16 LUFS against YouTube's -14
        r = json.loads(p.stdout)
        self.assertEqual(sorted(c["check"] for c in r["checks"] if c["status"] == "FAIL"),
                         ["loudness", "video_codec"])
        p = run("deliver-check", self.good, "--dest", "linkedin_16x9", "--config", self.config,
                env={"RPRESOLVE_FFPROBE": "/nonexistent/ffprobe"})
        self.assertEqual(p.returncode, 2)
        self.assertIn("ffprobe not found", p.stderr)

    def test_bad_arguments(self):
        p = run("deliver-check", self.good, "--dest", "linkedin_16x9", "--config",
                "/nonexistent/overlay.json")
        self.assertEqual(p.returncode, 1)
        self.assertIn("not found", p.stderr)
        p = run("deliver-check", self.good, "--dest", "nowhere", "--config", self.config)
        self.assertEqual(p.returncode, 1)
        self.assertIn("unknown destination", p.stderr)
        p = run("deliver-check", self.good, "--dest", "linkedin_16x9", "--config", self.config,
                "--fps", "fast")
        self.assertEqual(p.returncode, 1)

    def test_fix_loudness(self):
        d = tempfile.mkdtemp(dir=self.dir)
        path = make(os.path.join(d, NAME), lufs=-18.0)
        with open(deliver.sidecar_path(path), "w", encoding="utf-8") as f:
            f.write(SRT)
        p = run("deliver-fix-loudness", path, "--dest", "linkedin_16x9", "--config", self.config)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("bit-identical: yes", p.stdout)
        self.assertIn("the original is untouched", p.stdout)
        self.assertTrue(os.path.isfile(path[:-4] + ".loudfix.mp4"))
        p = run("deliver-fix-loudness", path, "--dest", "linkedin_16x9", "--config", self.config)
        self.assertEqual(p.returncode, 1)
        self.assertIn("already exists", p.stderr)


class TestBadOverlay(unittest.TestCase):
    """An overlay named with --config that does not parse stops the deliver
    commands; it is never quietly replaced by the defaults."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.realpath(self.tmp.name)
        self.config = os.path.join(self.dir, "overlay.json")
        with open(self.config, "w", encoding="utf-8") as f:
            f.write('{"destinations": {"linkedin_16x9": {"resolution": '
                    '{"width": 64, "height": 36}},}}')
        self.file = os.path.join(self.dir, NAME)
        open(self.file, "w").close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_check_commands_exit_1(self):
        for cmd in ("deliver-check", "deliver-fix-loudness"):
            with self.subTest(cmd=cmd):
                p = run(cmd, self.file, "--dest", "linkedin_16x9", "--config", self.config)
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn("could not read config", p.stderr)
                self.assertNotIn("Using defaults", p.stderr)
                p = run("--config", self.config, cmd, self.file, "--dest", "linkedin_16x9")
                self.assertEqual(p.returncode, 1)
                self.assertIn("could not read config", p.stderr)

if __name__ == "__main__":
    unittest.main()
