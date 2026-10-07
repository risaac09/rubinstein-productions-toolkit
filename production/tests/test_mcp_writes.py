"""
Tests for the MCP write tools (rpresolve.mcp.tools_write) and the write
journal: the dry-run-then-plan_sha contract, journalling, refusals that
write nothing, and the static write-path guard over every non-test .py
under production/: no file names LoadProject, CreateProject, SaveProject,
StartRendering, any Delete*, eval or exec (or the other calls that open,
create, export or close a project), and nothing outside rpresolve/ (the
library and the MCP server) names a call that changes the open project
or the Resolve UI. No file is exempt, the legacy resolve_workflow.py
script included.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import ast
import builtins
import json
import os
import re
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

    def test_a_project_without_colour_management_is_refused_and_nothing_is_written(self):
        project = FakeProject(mode="davinciYRGB")
        with self.assertRaisesRegex(workflows.Refused, "Color Managed"):
            self.run_tool(project)
        with self.assertRaisesRegex(workflows.Refused, "Color Managed"):
            self.run_tool(project, dry_run=False, plan_sha="0" * 64)
        self.assertEqual((project.pool.imports, project.pool.created), ([], []))


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


# The write-path guard. api.py's rules put every Resolve write behind one layer, the
# library (production/rpresolve/) that the MCP server calls: a pinned project, a plan the
# caller confirms, a read-back, the UI put back, a journal line. This scan fails the build if
# any other non-test file under production/ reaches Resolve's write API, or if any file,
# the layer included, makes a call api.py rules out. It reads names, so it is a tripwire for
# a call written out in source, not a sandbox: a name built at run time gets past it.

# Never, in any file: opening, creating, closing, importing, restoring, archiving, saving or
# exporting a project is a human decision made in Resolve (api.py rule 2); starting a render
# is the person's step; nothing is deleted; no eval or exec.
FORBIDDEN = {"LoadProject", "CreateProject", "SaveProject", "StartRendering", "eval", "exec",
             "DeleteProject", "ImportProject", "CloseProject", "RestoreProject",
             "ArchiveProject", "ExportProject"}

# Writes to the open project, its media pool, timelines, grades, render queue and settings,
# and the UI state writes (page, current timeline, playhead) that api.py rule 3 wraps in
# UISnapshot. Only the layer may make them; everything else under production/ is read-only
# or offline. The names are those the layer calls today, plus the verbs Resolve's API uses
# for changes, so a write the layer does not use yet is caught too.
WRITE_CALLS = {"AddMarker", "AddRenderJob", "AddSubFolder", "AddTrack", "AppendToTimeline",
               "ApplyGradeFromDRX", "AutoSyncAudio", "CreateEmptyTimeline",
               "CreateSubtitlesFromAudio", "DuplicateTimeline", "ImportMedia", "OpenPage",
               "RefreshLUTList", "SetClipColor", "SetClipProperty", "SetCurrentFolder",
               "SetCurrentRenderFormatAndCodec", "SetCurrentRenderMode", "SetCurrentTimecode",
               "SetCurrentTimeline", "SetEnd", "SetLUT", "SetProperty", "SetRenderSettings",
               "SetSetting"}
WRITE_VERB = re.compile(r"^(?:Set|Add|Create|Import|Append|Insert|Apply|Duplicate|Open|Start|"
                        r"Stop|Move|Replace|Relink|Link|Unlink|Clear|Update|Remove|Reset)[A-Z]")

WRITE_LAYER = "rpresolve"


def _names(path):
    """(line, name) for every attribute, name or string constant in the file."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        name = (node.attr if isinstance(node, ast.Attribute) else
                node.id if isinstance(node, ast.Name) else
                node.value if isinstance(node, ast.Constant) and isinstance(node.value, str)
                else None)
        if name:
            yield node.lineno, name


def forbidden_uses(path):
    """(line, name) for every attribute, name or string constant in the file
    that is a forbidden call: FORBIDDEN, or any Delete* method."""
    return [(line, name) for line, name in _names(path)
            if name in FORBIDDEN or (name.startswith("Delete") and name.isidentifier())]


def write_uses(path):
    """(line, name) for every name in the file that is a write call: WRITE_CALLS, or a
    CamelCase name that starts with one of Resolve's change verbs (a Python builtin such as
    ImportError is not one)."""
    return [(line, name) for line, name in _names(path)
            if name in WRITE_CALLS or (name.isidentifier() and WRITE_VERB.match(name)
                                       and not hasattr(builtins, name))]


def production_files(root):
    """Every non-test .py under root: the library, the MCP server, the scripts."""
    return sorted(f for f in Path(root).rglob("*.py")
                  if "tests" not in f.relative_to(root).parts and "__pycache__" not in f.parts)


def scan_production(root):
    """{relative path: [(line, name)]} for every file that breaks the guard: a forbidden call
    anywhere, or a write call outside the write layer (root/rpresolve/). No file is exempt."""
    found = {}
    for f in production_files(root):
        rel = f.relative_to(root)
        hits = forbidden_uses(f)
        if rel.parts[0] != WRITE_LAYER:
            hits += write_uses(f)
        if hits:
            found[str(rel)] = sorted(set(hits))
    return found


class TestForbiddenCalls(unittest.TestCase):
    def test_no_file_under_production_breaks_the_write_path_guard(self):
        files = {str(f.relative_to(PRODUCTION)) for f in production_files(PRODUCTION)}
        # The glob must not quietly shrink: the script, the launcher and the survey are
        # scanned like the library is.
        self.assertGreater(len(files), 30)
        for must in ("resolve_workflow.py", "resolve_mcp.py", "resolve_survey.py",
                     "rpresolve/api.py", "rpresolve/workflows.py", "rpresolve/mcp/tools_write.py"):
            self.assertIn(must, files)
        self.assertEqual(scan_production(PRODUCTION), {})

    def test_the_layer_does_make_the_write_calls(self):
        # The guard is not vacuous: the sanctioned layer names real write calls.
        layer = [f for f in production_files(PRODUCTION)
                 if f.relative_to(PRODUCTION).parts[0] == WRITE_LAYER]
        named = {name for f in layer for _, name in write_uses(f)}
        self.assertTrue({"ImportMedia", "AddRenderJob", "SetLUT", "AddMarker",
                         "CreateEmptyTimeline", "DuplicateTimeline"} <= named, named)

    def test_the_scan_catches_each_form(self):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write("pm.LoadProject('x')\nt.DeleteClips([])\n_safe_call(p, 'StartRendering')\n"
                    "eval('1')\n'''Never call LoadProject.'''\npm.ExportProject(n, p)\n"
                    "pm.CloseProject(p)\n")
        self.addCleanup(os.unlink, f.name)
        self.assertEqual([n for _, n in sorted(forbidden_uses(f.name))],
                         ["LoadProject", "DeleteClips", "StartRendering", "eval",
                          "ExportProject", "CloseProject"])

    def test_the_guard_fails_a_write_in_any_script_and_names_it(self):
        # A scratch tree shaped like production/: a write call in the legacy script, in the
        # launcher or in a new script fails the guard; the same call in the layer passes; test
        # code is not scanned. The script gets no exemption.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "rpresolve" / "mcp").mkdir(parents=True)
            (root / "tests").mkdir()
            files = {
                "resolve_workflow.py": "pm.CreateProject(name)\npm.SaveProject()\n",
                "resolve_mcp.py": "project.StartRendering()\n",
                "new_tool.py": "pool.ImportMedia(files)\nitem.SetClipColor('Blue')\n"
                               "project.DeleteAllRenderJobs()\nresolve.OpenPage('color')\n",
                "survey_helper.py": "project.GetName()\nraise ImportError('x')\n",
                "rpresolve/ok.py": "pool.ImportMedia(files)\ntimeline.SetSetting('k', 'v')\n",
                "rpresolve/mcp/bad.py": "project.DeleteAllRenderJobs()\npm.ExportProject(n, p)\n",
                "tests/test_fake.py": "pm.CreateProject('x')\n",
            }
            for rel, text in files.items():
                (root / rel).write_text(text, encoding="utf-8")
            found = scan_production(root)
        self.assertEqual(found, {
            "resolve_workflow.py": [(1, "CreateProject"), (2, "SaveProject")],
            "resolve_mcp.py": [(1, "StartRendering")],
            "new_tool.py": [(1, "ImportMedia"), (2, "SetClipColor"), (3, "DeleteAllRenderJobs"),
                            (4, "OpenPage")],
            "rpresolve/mcp/bad.py": [(1, "DeleteAllRenderJobs"), (2, "ExportProject")],
        })

    def test_a_write_verb_the_layer_does_not_use_yet_is_caught_outside_it(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "script.py").write_text("tl.InsertGeneratorIntoTimeline('x')\n"
                                            "pool.RelinkClips(c, p)\n", encoding="utf-8")
            found = scan_production(root)
        self.assertEqual(found, {"script.py": [(1, "InsertGeneratorIntoTimeline"),
                                               (2, "RelinkClips")]})


if __name__ == "__main__":
    unittest.main()
