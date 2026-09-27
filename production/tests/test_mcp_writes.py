"""
Tests for the MCP write tools (rpresolve.mcp.tools_write) and the write
journal: the dry-run-then-plan_sha contract, journalling, refusals that
write nothing, and a static scan that no code the server can reach calls
LoadProject, CreateProject, SaveProject, StartRendering, any Delete*, eval
or exec.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import ast
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
PRODUCTION = HERE.parent
sys.path.insert(0, str(PRODUCTION))
sys.path.insert(0, str(HERE))

from rpresolve import api, cutlist, workflows  # noqa: E402
from rpresolve.mcp import journal, schema, server  # noqa: E402
from rpresolve.mcp.registry import ToolContext  # noqa: E402
from test_ingest import FakeProject, FakeResolve, row  # noqa: E402
from test_workflows import FakeCutFolder, FakeCutItem, FakeCutProject  # noqa: E402

REG = server.build_registry()


class Session:
    def __init__(self, project):
        self.resolve = FakeResolve(project)

    def get(self):
        return self.resolve


def call(name, args, project):
    tool = REG.get(name)
    args = schema.coerce(tool.input_schema, args)
    errors = schema.validate(tool.input_schema, args)
    if errors:
        raise AssertionError(errors)
    return tool.handler(schema.with_defaults(tool.input_schema, args),
                        ToolContext(session=Session(project)))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.realpath(self.tmp.name)
        self.journal = os.path.join(self.dir, "logs", "writes.jsonl")
        env = mock.patch.dict(os.environ, {"RPRESOLVE_MCP_JOURNAL": self.journal,
                                           "RPRESOLVE_LOCK": os.path.join(self.dir, "lock"),
                                           "RPRESOLVE_MCP_OUT": os.path.join(self.dir, "out")})
        env.start()
        self.addCleanup(env.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def events(self):
        if not os.path.exists(self.journal):
            return []
        with open(self.journal) as f:
            return [json.loads(line) for line in f]


class TestRegistry(unittest.TestCase):
    def test_write_tools_are_marked_as_writes(self):
        for name in ("ingest", "cut"):
            tool = REG.get(name)
            self.assertFalse(tool.annotations["readOnlyHint"], name)
            self.assertIn("project", tool.input_schema["required"])
            self.assertTrue(tool.input_schema["properties"]["dry_run"]["default"])


class TestIngest(Base):
    ROWS = [row("/media/card/a.mov"), row("/media/card/b.mov")]

    def run_tool(self, fake, **args):
        with mock.patch("rpresolve.mcp.tools_write.rpdetect.detect_paths",
                        return_value=(self.ROWS, ["/media/gone"])):
            return call("ingest", {"project": "Sandbox", "paths": ["/media/card"], **args},
                        fake)

    def test_dry_run_then_real_run_with_its_plan_sha(self):
        project = FakeProject()
        dry = self.run_tool(project)
        self.assertTrue(dry["dry_run"])
        self.assertEqual([r["result"] for r in dry["results"]], ["planned", "planned"])
        self.assertIn(dry["plan_sha"], dry["summary"])
        self.assertEqual(dry["missing"], ["/media/gone"])
        self.assertNotIn("plan", dry)
        self.assertEqual(dry["results"][0]["camera"], "Panasonic DC-GH7")
        self.assertIn("reason", dry["results"][0])
        self.assertEqual((project.pool.imports, self.events()), ([], []))

        real = self.run_tool(project, dry_run=False, plan_sha=dry["plan_sha"], limit=1)
        self.assertEqual(real["results"][0]["result"], "tagged")
        self.assertEqual((real["total"], real["next_offset"]), (2, 1))
        self.assertEqual(real["exit_status"], 0)
        ev = self.events()
        self.assertEqual([e["event"] for e in ev], ["started", "finished"])
        self.assertEqual(ev[0]["id"], real["journal"]["id"])
        self.assertEqual(ev[0]["project"], {"name": "Sandbox", "id": None})
        self.assertEqual(ev[1]["status"], "ok")
        self.assertEqual(os.stat(self.journal).st_mode & 0o777, 0o600)

    def test_a_real_run_needs_a_plan_sha(self):
        project = FakeProject()
        with self.assertRaisesRegex(workflows.Refused, "plan_sha"):
            self.run_tool(project, dry_run=False)
        self.assertEqual((project.pool.imports, self.events()), ([], []))

    def test_a_stale_plan_sha_is_refused_and_journalled(self):
        project = FakeProject()
        with self.assertRaisesRegex(workflows.Refused, "plan changed"):
            self.run_tool(project, dry_run=False, plan_sha="0" * 64)
        self.assertEqual(project.pool.imports, [])
        ev = self.events()
        self.assertEqual([(e["event"], e.get("status")) for e in ev],
                         [("started", None), ("finished", "error")])
        self.assertIn("plan changed", ev[1]["summary"])

    def test_wrong_project_writes_nothing(self):
        project = FakeProject()
        dry = self.run_tool(project)
        with self.assertRaises(api.ProjectChanged):
            self.run_tool(project, project="Other", dry_run=False, plan_sha=dry["plan_sha"])
        with self.assertRaises(api.ProjectChanged):
            self.run_tool(project, project_id="wrong")
        self.assertEqual(project.pool.imports, [])

    def test_no_journal_no_write(self):
        project = FakeProject()
        dry = self.run_tool(project)
        os.makedirs(os.path.dirname(self.journal))
        with open(self.journal, "w"):
            pass
        os.chmod(self.journal, 0o400)
        try:
            with self.assertRaises(journal.JournalError):
                self.run_tool(project, dry_run=False, plan_sha=dry["plan_sha"])
        finally:
            os.chmod(self.journal, 0o600)
        self.assertEqual(project.pool.imports, [])

    def test_busy_lock_writes_and_journals_nothing(self):
        project = FakeProject()
        dry = self.run_tool(project)
        with mock.patch("rpresolve.mcp.tools_write.WRITE_LOCK_WAIT", 0.2):
            with api.ResolveLock(timeout=1):
                with self.assertRaises(api.ResolveBusy):
                    self.run_tool(project, dry_run=False, plan_sha=dry["plan_sha"])
        self.assertEqual((project.pool.imports, self.events()), ([], []))

    def test_nothing_to_do_says_so(self):
        project = FakeProject()
        dry = self.run_tool(project)
        self.run_tool(project, dry_run=False, plan_sha=dry["plan_sha"])
        again = self.run_tool(project)
        self.assertTrue(again["summary"].endswith("Nothing to do."), again["summary"])

    def test_paths_must_be_absolute(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            call("ingest", {"project": "Sandbox", "paths": ["card"]}, FakeProject())


class TestCut(Base):
    def manifest(self):
        src = os.path.join(self.dir, "source.bin")
        with open(src, "wb") as f:
            f.write(b"media")
        words = os.path.join(self.dir, "w.json")
        with open(words, "w") as f:
            json.dump({"segments": [{"words": [{"word": "hello", "start": 1.0, "end": 1.5},
                                               {"word": "world.", "start": 1.6, "end": 2.0}]}]}, f)
        approved = os.path.join(self.dir, "a.md")
        with open(approved, "w") as f:
            f.write("Hello world.")
        m = cutlist.build_manifest({"hi": [(0.9, 2.1)]}, src, 25, words, approved,
                                   reframe={"face_x": 270})
        path = os.path.join(self.dir, "m.json")
        with open(path, "w") as f:
            json.dump(m, f)
        return path, src

    def test_dry_run_plans_and_real_run_needs_the_sha(self):
        manifest, src = self.manifest()
        project = FakeCutProject(FakeCutFolder([FakeCutItem(src)]))
        r = call("cut", {"project": "Sandbox", "manifest": manifest, "prefix": "T",
                         "audio": False}, project)
        self.assertEqual(r["would_create"], ["T_hi [auto]", "T_hi_9x16 [auto]"])
        self.assertIn("would create 2 timeline(s) in 'Sandbox'", r["summary"])
        self.assertIn(r["plan_sha"], r["summary"])
        self.assertEqual(self.events(), [])
        with self.assertRaisesRegex(workflows.Refused, "plan_sha"):
            call("cut", {"project": "Sandbox", "manifest": manifest, "dry_run": False}, project)
        with self.assertRaisesRegex(workflows.Refused, "plan changed"):
            call("cut", {"project": "Sandbox", "manifest": manifest, "prefix": "T",
                         "audio": False, "dry_run": False, "plan_sha": "0" * 64}, project)
        self.assertEqual([e["event"] for e in self.events()], ["started", "finished"])

    def test_prefix_is_validated(self):
        manifest, _ = self.manifest()
        with self.assertRaises(AssertionError):
            call("cut", {"project": "Sandbox", "manifest": manifest, "prefix": "a b"}, None)


FORBIDDEN = {"LoadProject", "CreateProject", "SaveProject", "StartRendering", "eval", "exec",
             "DeleteProject", "ImportProject"}


def forbidden_uses(path):
    """(line, name) for every attribute, name or string constant in the file
    that is a forbidden call: FORBIDDEN, or any Delete* method."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=str(path))
    hits = []
    for node in ast.walk(tree):
        name = (node.attr if isinstance(node, ast.Attribute) else
                node.id if isinstance(node, ast.Name) else
                node.value if isinstance(node, ast.Constant) and isinstance(node.value, str)
                else None)
        if name and (name in FORBIDDEN or (name.startswith("Delete") and name.isidentifier())):
            hits.append((node.lineno, name))
    return hits


class TestForbiddenCalls(unittest.TestCase):
    def test_nothing_the_server_reaches_calls_them(self):
        files = sorted((PRODUCTION / "rpresolve").rglob("*.py")) + [PRODUCTION / "resolve_mcp.py"]
        self.assertGreater(len(files), 15)
        found = {str(f.relative_to(PRODUCTION)): forbidden_uses(f) for f in files}
        self.assertEqual({k: v for k, v in found.items() if v}, {})

    def test_the_scan_catches_each_form(self):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write("pm.LoadProject('x')\nt.DeleteClips([])\n_safe_call(p, 'StartRendering')\n"
                    "eval('1')\n'''Never call LoadProject.'''\n")
        self.addCleanup(os.unlink, f.name)
        self.assertEqual([n for _, n in sorted(forbidden_uses(f.name))],
                         ["LoadProject", "DeleteClips", "StartRendering", "eval"])


if __name__ == "__main__":
    unittest.main()
