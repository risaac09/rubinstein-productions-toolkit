"""
Offline tests for library modules the MCP server uses: rpresolve.config,
paths, grade, render, and the api additions (config and paths also serve the
offline commands of resolve_workflow.py). Fake Resolve objects only.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import api, config, grade, paths, render


class TestConfig(unittest.TestCase):
    def test_defaults_and_warning_hook(self):
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "c.json")
            with open(bad, "w") as f:
                f.write("{not json")
            seen = []
            cfg = config.load_config(bad, warn=seen.append)
            self.assertEqual(cfg, config.DEFAULT_CONFIG)
            self.assertTrue(seen and seen[0].startswith("WARNING"))
            self.assertEqual(config.load_config(bad), config.DEFAULT_CONFIG)  # silent without warn
            good = os.path.join(d, "g.json")
            with open(good, "w") as f:
                f.write('{"default_framerate": "25"}')
            self.assertEqual(config.load_config(good)["default_framerate"], "25")


class TestPaths(unittest.TestCase):
    def test_any_git_tree(self):
        with tempfile.TemporaryDirectory() as d:
            repo = os.path.join(d, "other-repo")
            os.makedirs(repo)
            subprocess.run(["git", "init", "-q", repo], check=True)
            inside = os.path.join(repo, "sub", "out.md")
            outside = os.path.join(d, "loose", "out.md")
            self.assertTrue(paths.in_any_git_tree(inside))
            self.assertFalse(paths.in_any_git_tree(outside))
            self.assertIsNone(paths.out_problem(inside))  # this repo's rule alone lets it pass
            self.assertIn("git working tree", paths.out_problem(inside, any_git_tree=True))
            with self.assertRaises(paths.OutputRefused):
                paths.write_private(inside, "x", any_git_tree=True)
            os.makedirs(os.path.dirname(outside))
            self.assertEqual(paths.write_private(outside, "ok", any_git_tree=True), outside)

    def test_default_out_dir_honours_the_environment(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("RPRESOLVE_MCP_OUT")
            os.environ["RPRESOLVE_MCP_OUT"] = os.path.join(d, "out")
            try:
                self.assertTrue(os.path.isdir(paths.default_out_dir()))
            finally:
                if old is None:
                    del os.environ["RPRESOLVE_MCP_OUT"]
                else:
                    os.environ["RPRESOLVE_MCP_OUT"] = old


class FakeGraph:
    def __init__(self, nodes):
        self.nodes = nodes  # [(label, lut, tools)]

    def GetNumNodes(self): return len(self.nodes)
    def GetNodeLabel(self, i): return self.nodes[i - 1][0]
    def GetLUT(self, i): return self.nodes[i - 1][1]
    def GetToolsInNode(self, i): return self.nodes[i - 1][2]


class FakeGroup:
    def GetName(self): return "A3 output"


class FakeItem:
    def __init__(self, nodes, version=None, group=None):
        self.graph, self.version, self.group = FakeGraph(nodes), version, group

    def GetNodeGraph(self): return self.graph
    def GetCurrentVersion(self): return self.version
    def GetColorGroup(self): return self.group


class TestGrade(unittest.TestCase):
    def test_read_grade_and_default_graph(self):
        item = FakeItem([("CST OUT 709", "", ["OFX: Color Space Transform"])],
                        {"versionName": "Version 1", "versionType": 0}, FakeGroup())
        g = grade.read_grade(item)
        self.assertEqual(g["num_nodes"], 1)
        self.assertEqual(g["nodes"][0]["label"], "CST OUT 709")
        self.assertEqual(g["version"], {"name": "Version 1", "type": "local"})
        self.assertEqual(g["color_group"], "A3 output")
        self.assertFalse(grade.is_default_graph(g["fingerprint"]))
        self.assertTrue(grade.is_default_graph(grade.read_grade(FakeItem([("", "", None)]))["fingerprint"]))
        self.assertTrue(grade.is_default_graph(()))
        remote = FakeItem([("", "", None)], {"versionName": "V", "versionType": 1})
        self.assertEqual(grade.item_version(remote)["type"], "remote")


class FakeQueue:
    def __init__(self, jobs): self.jobs = jobs
    def GetRenderJobList(self): return list(self.jobs)
    def GetRenderJobStatus(self, jid):
        return {"JobStatus": "Ready", "CompletionPercentage": 0} if jid == "job-1" else {}


class TestRender(unittest.TestCase):
    def test_list_jobs_merges_each_jobs_status_and_leaves_the_queue_alone(self):
        jobs = [{"JobId": "job-1", "TargetDir": "/out"}, {"JobId": "job-2"}, {"TargetDir": "/x"}]
        rows = render.list_jobs(FakeQueue(jobs))
        self.assertEqual([r.get("JobId") for r in rows], ["job-1", "job-2", None])
        self.assertEqual((rows[0]["JobStatus"], rows[0]["CompletionPercentage"]), ("Ready", 0))
        self.assertEqual((rows[1]["JobStatus"], rows[1]["Error"]), (None, None))
        self.assertEqual(rows[2]["JobStatus"], None)
        self.assertNotIn("JobStatus", jobs[0])


class FakePM:
    def __init__(self, project): self.project = project
    def GetCurrentProject(self): return self.project


class FakeProj:
    def __init__(self, name="Sandbox", uid="id-1"): self.name, self.uid = name, uid
    def GetName(self): return self.name
    def GetUniqueId(self): return self.uid
    def GetMediaPool(self): return None


class FakeResolve:
    def __init__(self, project): self.pm = FakePM(project)
    def GetProjectManager(self): return self.pm


class TestApiAdditions(unittest.TestCase):
    def test_pin_for_write(self):
        r = FakeResolve(FakeProj())
        pm, project, pin = api.pin_for_write(r, "Sandbox", "id-1")
        self.assertEqual(pin.unique_id, "id-1")
        with self.assertRaises(api.ProjectChanged):
            api.pin_for_write(r, "Other")
        with self.assertRaises(api.ProjectChanged):
            api.pin_for_write(r, "Sandbox", "id-2")
        with self.assertRaises(api.ProjectChanged):
            api.current_project(FakeResolve(None))
        with self.assertRaises(api.ResolveAPIError):
            api.media_pool_root(FakeProj())



class TestConfigLocale(unittest.TestCase):
    def test_config_reads_as_utf8_under_the_c_locale(self):
        """Connected to Resolve, the process runs in the C locale, where a
        default open() decodes ASCII. A config with a non-ASCII character in
        it must still load (the MCP queue_render bug, found with a note in
        the repo config that has since gone, so this writes its own)."""
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            overlay = os.path.join(d, "overlay.json")
            with open(overlay, "w", encoding="utf-8") as f:
                f.write('{"note": "resize only \u2014 no reframe"}')
            env = dict(os.environ, LC_ALL="C", LANG="C", PYTHONUTF8="0",
                       PYTHONCOERCECLOCALE="0")
            code = ("import sys; sys.path.insert(0, %r); from rpresolve.config import "
                    "load_config; cfg = load_config(%r, warn=print, strict=True); "
                    "print('\\u2014' in cfg['note'])"
                    % (str(Path(__file__).resolve().parent.parent), overlay))
            out = subprocess.run([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
        self.assertEqual(out.returncode, 0, out.stderr.decode()[-300:])
        self.assertEqual(out.stdout.strip(), b"True")

if __name__ == "__main__":
    unittest.main()
