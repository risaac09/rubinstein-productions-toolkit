"""
Offline tests for the library modules the CLI and the MCP server share:
rpresolve.config, paths, grade, render, and the api additions. Fake Resolve
objects only.

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


class FakeTimeline:
    def GetName(self): return "Cut [auto]"


class FakeRenderProject:
    def __init__(self, codecs=None, queue_ok=True):
        self.codecs = {"H.265 Main": "H265"} if codecs is None else codecs
        self.queue_ok, self.format_codec, self.settings, self.jobs = queue_ok, {}, None, []

    def GetCurrentTimeline(self): return FakeTimeline()
    def GetRenderCodecs(self, fmt): return self.codecs
    def SetCurrentRenderFormatAndCodec(self, fmt, codec):
        self.format_codec = {"format": fmt, "codec": codec}
        return True
    def GetCurrentRenderFormatAndCodec(self): return self.format_codec
    def SetRenderSettings(self, s): self.settings = s; return True
    def AddRenderJob(self):
        if not self.queue_ok:
            return ""
        jid = f"job-{len(self.jobs) + 1}"
        self.jobs.append({"JobId": jid, "TargetDir": self.settings["TargetDir"],
                          "OutputFilename": self.settings["CustomName"] + ".mp4"})
        return jid
    def GetRenderJobList(self): return list(self.jobs)
    def GetRenderJobStatus(self, jid): return {"JobStatus": "Ready", "CompletionPercentage": 0}
    def StartRendering(self, *a): raise AssertionError("rendering must never start")


class TestRender(unittest.TestCase):
    PRESETS = {"youtube": {"name": "YouTube 4K", "resolution": {"width": 3840, "height": 2160},
                           "format": "mp4", "codec": "H265", "codec_fallbacks": [], "suffix": "_yt"}}

    def test_queue_and_read_back(self):
        p = FakeRenderProject()
        with tempfile.TemporaryDirectory() as d:
            r = render.queue_render_job(p, "youtube", d, self.PRESETS)
            self.assertEqual(r["job_id"], "job-1")
            self.assertIsNone(r["error"])
            self.assertTrue(r["codec"]["matched"])
            self.assertEqual(r["settings"]["CustomName"], "Cut [auto]_yt")
            job, problems = render.render_job_readback(
                p, "job-1", {"TargetDir": str(Path(d).resolve())})
            self.assertEqual((job["JobId"], problems), ("job-1", []))
            _, problems = render.render_job_readback(p, "job-1", {"TargetDir": "/elsewhere"})
            self.assertTrue(problems)
            p.jobs[0]["TargetDir"] += "/"      # Resolve's own trailing slash is the same folder
            p.jobs[0]["FormatWidth"] = "3840"  # and a number as text is the same number
            _, problems = render.render_job_readback(
                p, "job-1", {"TargetDir": str(Path(d).resolve()), "FormatWidth": 3840})
            self.assertEqual(problems, [])
            self.assertEqual(render.render_job_readback(p, "nope")[0], None)
            self.assertEqual(render.list_jobs(p)[0]["JobStatus"], "Ready")

    def test_errors_are_values_not_exceptions(self):
        self.assertIn("Unknown preset", render.queue_render_job(FakeRenderProject(), "nope", "/tmp", self.PRESETS)["error"])
        self.assertIn("No codecs", render.queue_render_job(FakeRenderProject(codecs={}), "youtube", "/tmp", self.PRESETS)["error"])
        r = render.queue_render_job(FakeRenderProject(queue_ok=False), "youtube", "/tmp", self.PRESETS)
        self.assertIsNone(r["job_id"])
        self.assertIn("Failed to queue", r["error"])


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
        """Connected to Resolve, the process runs in the C locale; the
        config's non-ASCII note must still load (the MCP queue_render bug)."""
        import subprocess
        env = dict(os.environ, LC_ALL="C", LANG="C", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0")
        code = ("import sys; sys.path.insert(0, %r); from rpresolve.config import load_config; "
                "print('\\u2014' in load_config(warn=print)['render_presets']['story']['note'])"
                % str(Path(__file__).resolve().parent.parent))
        out = subprocess.run([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        self.assertEqual(out.returncode, 0, out.stderr.decode()[-300:])
        self.assertEqual(out.stdout.strip(), b"True")

if __name__ == "__main__":
    unittest.main()
