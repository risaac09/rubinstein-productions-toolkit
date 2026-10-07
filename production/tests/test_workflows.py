"""
Offline tests for rpresolve.workflows (the orchestration behind the MCP
server's tools; resolve_workflow.py calls only its survey and detect
functions) and api.ResolveLock. Fake Resolve objects and synthetic files
only.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["RPRESOLVE_LOCK"] = os.path.join(  # a private lock: never the live one
    tempfile.gettempdir(), f"rpresolve-test-{os.getpid()}.lock")
sys.path.insert(0, str(Path(__file__).resolve().parent))  # the shared fakes in test_ingest

from rpresolve import api, cutlist, detect, workflows
from test_ingest import FakeProject, FakeResolve, row


class Cancelled(Exception):
    pass


class TestDetect(unittest.TestCase):
    def test_counts_and_refusal(self):
        rows = [row("/media/a.mov"), row("/media/b.mp4", profile=detect.REVIEW, cs="")]
        with mock.patch.object(workflows.rpdetect, "detect_paths", return_value=(rows, ["/gone"])):
            r = workflows.detect(["/media"])
        self.assertEqual(r["counts"], {"pinned": 1, "review": 1, "corrupt": 0})
        self.assertEqual((r["exit_status"], r["missing"]), (2, ["/gone"]))
        with mock.patch.object(workflows.rpdetect, "detect_paths", return_value=([], [])):
            with self.assertRaises(workflows.Refused):
                workflows.detect(["/empty"])


class TestIngest(unittest.TestCase):
    def test_dry_run_sha_guards_the_real_run(self):
        rows = [row("/media/card/a.mov")]
        dry = workflows.ingest(FakeResolve(FakeProject()), "Sandbox", [], dry_run=True, rows=rows)
        self.assertEqual(dry["results"][0]["result"], "planned")
        project = FakeProject()
        with self.assertRaises(workflows.Refused):
            workflows.ingest(FakeResolve(project), "Sandbox", [], rows=rows, expect_sha="0" * 64)
        self.assertEqual(project.pool.imports, [])
        real = workflows.ingest(FakeResolve(project), "Sandbox", [], rows=rows,
                                expect_sha=dry["plan_sha"])
        self.assertEqual((real["results"][0]["result"], real["exit_status"]), ("tagged", 0))
        self.assertIs(project.pool.current, project.root)

    def test_refusals_write_nothing(self):
        p = FakeProject(mode="davinciYRGB")
        with self.assertRaises(workflows.Refused):
            workflows.ingest(FakeResolve(p), "Sandbox", [], rows=[row("/media/a.mov")])
        with self.assertRaises(api.ProjectChanged):
            workflows.ingest(FakeResolve(FakeProject()), "Other", [], rows=[row("/media/a.mov")])
        with self.assertRaises(api.ProjectChanged):
            workflows.ingest(FakeResolve(FakeProject()), "Sandbox", [], project_id="wrong",
                             rows=[row("/media/a.mov")])
        self.assertEqual(p.pool.imports, [])

    def test_cancel_stops_before_the_next_bin(self):
        rows = [row("/media/a.mov"),
                row("/media/b.mov", camera="iPhone 16 Pro Max", profile="SDR", cs="Rec.709 Gamma 2.4")]
        calls = []

        def cancel():
            calls.append(1)
            if len(calls) > 1:
                raise Cancelled("stop")
        project = FakeProject()
        r = workflows.ingest(FakeResolve(project), "Sandbox", [], rows=rows, check_cancel=cancel)
        self.assertEqual(r["results"][0]["result"], "tagged")
        self.assertTrue(r["results"][1]["result"].startswith("FAILED: not run (Cancelled"))
        self.assertEqual(r["exit_status"], 1)
        self.assertEqual(len(project.pool.imports), 1)


class TestSurvey(unittest.TestCase):
    def test_command_and_missing_interpreter(self):
        cmd = workflows.survey_command("/out/s.md", "/out/s.json", ["A"], no_metadata_cache=True,
                                       python="py", script="s.py")
        self.assertEqual(cmd, ["py", "s.py", "--out", "/out/s.md", "--json", "/out/s.json",
                               "--projects", "A", "--no-metadata-cache"])
        with mock.patch.object(workflows, "SURVEY_PYTHON", "/nonexistent/python3.14"):
            with self.assertRaises(workflows.Refused):
                workflows.survey("/tmp/s.md")


class FakeCutItem:
    def __init__(self, path):
        self.path = path

    def GetClipProperty(self, key=None):
        return {"File Path": self.path, "Resolution": "1280x720"}.get(key)

    def GetName(self):
        return os.path.basename(self.path)


class FakeCutFolder:
    def __init__(self, clips):
        self.clips = clips

    def GetClipList(self): return self.clips
    def GetSubFolderList(self): return []


class FakeCutPool:
    def __init__(self, root): self.root = root
    def GetRootFolder(self): return self.root


class FakeCutProject:
    def __init__(self, root):
        self.pool = FakeCutPool(root)

    def GetName(self): return "Sandbox"
    def GetUniqueId(self): return "id-Sandbox"
    def GetMediaPool(self): return self.pool
    def GetTimelineCount(self): return 0


class TestCut(unittest.TestCase):
    def test_dry_run_plans_and_creates_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "source.bin")
            with open(src, "wb") as f:
                f.write(b"media")
            words = os.path.join(d, "w.json")
            with open(words, "w") as f:
                json.dump({"segments": [{"words": [{"word": "hello", "start": 1.0, "end": 1.5},
                                                   {"word": "world.", "start": 1.6, "end": 2.0}]}]}, f)
            approved = os.path.join(d, "a.md")
            with open(approved, "w") as f:
                f.write("Hello world.")
            manifest = os.path.join(d, "m.json")
            m = cutlist.build_manifest({"hi": [(0.9, 2.1)]}, src, 25, words, approved,
                                       reframe={"face_x": 270})
            with open(manifest, "w") as f:
                json.dump(m, f)
            project = FakeCutProject(FakeCutFolder([FakeCutItem(src)]))
            r = workflows.cut(FakeResolve(project), "Sandbox", manifest, prefix="T", dry_run=True,
                              audio=False)
            self.assertEqual(r["would_create"], ["T_hi [auto]", "T_hi_9x16 [auto]"])
            self.assertEqual((r["results"], r["dry_run"]), ([], True))
            self.assertEqual(len(r["plan_sha"]), 64)
            with open(src, "ab") as f:
                f.write(b"changed")
            with self.assertRaises(workflows.Refused):
                workflows.cut_gate(manifest, audio=False)


class TestResolveLock(unittest.TestCase):
    def test_a_second_holder_waits_then_gives_up(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "resolve.lock")
            with api.ResolveLock(path=path):
                with self.assertRaises(api.ResolveBusy):
                    with api.ResolveLock(timeout=0.3, path=path):
                        pass
            with api.ResolveLock(timeout=0.3, path=path):
                pass  # released on exit


if __name__ == "__main__":
    unittest.main()
