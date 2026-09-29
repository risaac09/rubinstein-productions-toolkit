"""
Tests for the resolve_workflow.py deliver commands: deliver-check and
deliver-fix-loudness run as subprocesses on ffmpeg-made media (exit 0 pass,
1 fail, 2 tool missing; a config overlay given before or after the
command), and deliver-queue called in-process against the Resolve fakes.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import resolve_fakes as rf  # noqa: E402
import resolve_workflow  # noqa: E402
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


class TestQueueCommand(unittest.TestCase):
    def setUp(self):
        rf.CALLS.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.realpath(self.tmp.name)
        env = mock.patch.dict(os.environ, {"RPRESOLVE_LOCK": os.path.join(self.dir, "lock")})
        env.start()
        self.addCleanup(env.stop)
        tl = rf.Timeline("Clip [auto]", "tl-1", "25", tracks={
            "video": [[]], "audio": [[]], "subtitle": [[rf.Item("Sub", 0, 10, nodes=None)]]})
        self.project = rf.Project(timelines=[tl], current=tl)
        self.resolve = rf.Resolve(self.project)

    def tearDown(self):
        self.tmp.cleanup()

    def ns(self, **kw):
        base = dict(config=None, deliver_config=None, project="RP Automation Sandbox",
                    project_id=None, timeline="Clip [auto]", dest="linkedin_16x9",
                    target_dir=self.dir, show="SW", episode="1", guest="Guest", index="1",
                    slug="example-clip", client=None, dry_run=False, plan_sha=None)
        base.update(kw)
        return argparse.Namespace(**base)

    def call(self, **kw):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(resolve_workflow, "get_resolve", return_value=self.resolve), \
                redirect_stdout(out), redirect_stderr(err):
            status = resolve_workflow.cmd_deliver_queue(self.ns(**kw))
        return status, out.getvalue(), err.getvalue()

    def test_dry_run_then_queue(self):
        status, out, _ = self.call(dry_run=True)
        self.assertEqual(status, 0)
        self.assertIn("Dry run: nothing queued", out)
        sha = out.split("plan_sha: ")[1].split()[0]
        self.assertEqual(self.project.jobs, [])
        status, out, err = self.call(plan_sha=sha)
        self.assertEqual(status, 0, err)
        self.assertIn("NOT started", out)
        self.assertIn("OutputFilename='SW001_Guest_01_example-clip_16x9.mp4'", out)
        self.assertIn("unverified", out)
        self.assertEqual(len(self.project.jobs), 1)
        self.assertNotIn("StartRendering", [c[1] for c in rf.CALLS])

    def test_refusals_exit_1(self):
        status, _, err = self.call(guest="Two Words", dry_run=True)
        self.assertEqual(status, 1)
        self.assertIn("guest", err)
        status, _, err = self.call(project="Another Project", dry_run=True)
        self.assertEqual(status, 1)
        status, _, err = self.call(plan_sha="0" * 64)
        self.assertEqual(status, 1)
        self.assertIn("plan changed", err)
        self.assertEqual(self.project.jobs, [])


if __name__ == "__main__":
    unittest.main()
