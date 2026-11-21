"""
fusion-dump: the parser on every tracked .setting and a hand-made .comp, the diff,
and the export against a mocked API (no Resolve). Stdlib only.

Run: python3 production/tests/test_fusion_dump.py
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PRODUCTION = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PRODUCTION))

from rpresolve import api as rpapi  # noqa: E402
from rpresolve import cli, cmd_fusion_dump, fusiondump  # noqa: E402

COMP = """{
	Tools = ordered() {
		MediaIn1 = MediaIn {
			ViewInfo = OperatorInfo { Pos = { 110, 49.5 } },
			Inputs = { Layer = Input { Value = FuID { "Video" } }, Gain = Input { Value = -1.5 } }
		},
		Blur1 = Blur {
			ViewInfo = OperatorInfo { Pos = { 220, 49.5 } },
			Inputs = {
				XBlurSize = Input { SourceOp = "Blur1XBlurSize", Source = "Value" },
				Input = Input { SourceOp = "MediaIn1", Source = "Output" }
			}
		},
		Blur1XBlurSize = BezierSpline {
			KeyFrames = { [0] = { 0, RH = { 3, 0 } }, [10] = { 5, LH = { 7, 5 } } }
		},
		MediaOut1 = MediaOut {
			ViewInfo = OperatorInfo { Pos = { 330, 49.5 } },
			Inputs = { Input = Input { SourceOp = "Blur1", Source = "Output" } }
		}
	},
	ActiveTool = "Blur1"
}
"""


def settings():
    out = subprocess.run(["git", "ls-files", "*.setting"], cwd=PRODUCTION, capture_output=True,
                         text=True, check=True).stdout.split()
    return [PRODUCTION / p for p in out]


class Parse(unittest.TestCase):
    def test_every_tracked_setting_parses(self):
        files = settings()
        self.assertTrue(files)
        for p in files:
            with self.subTest(p.name):
                t = fusiondump.parse(p.read_text(encoding="utf-8"))
                self.assertTrue(t["nodes"])
                for link in t["links"]:  # every link ends at a tool the file holds
                    self.assertIn(link["to"], {**t["nodes"], **t["modifiers"]})

    def test_crop_window_tree(self):
        t = fusiondump.parse((PRODUCTION / "fusion-macros" / "crop-window.setting").read_text())
        self.assertEqual(t["nodes"]["Rectangle1"]["type"], "RectangleMask")
        self.assertEqual(t["modifiers"]["Rectangle1Width"], {"type": "BezierSpline", "keyframes": 4})
        self.assertIn({"from": "Rectangle1Width", "output": "Value", "to": "Rectangle1",
                       "input": "Width"}, t["links"])

    def test_comp(self):
        t = fusiondump.parse(COMP)
        self.assertEqual(list(t["nodes"]), ["MediaIn1", "Blur1", "MediaOut1"])
        self.assertEqual(t["nodes"]["MediaIn1"]["pos"], [110, 49.5])
        self.assertEqual(t["nodes"]["MediaIn1"]["inputs"], {"Layer": "Video", "Gain": -1.5})
        self.assertEqual(t["modifiers"], {"Blur1XBlurSize": {"type": "BezierSpline", "keyframes": 2}})
        self.assertEqual(len(t["links"]), 3)

    def test_not_a_comp(self):
        with self.assertRaises(ValueError):
            fusiondump.parse("{ Foo = 1 }")
        with self.assertRaises(ValueError):
            fusiondump.parse("{ Tools = ")


class Diff(unittest.TestCase):
    def test_equal(self):
        t = fusiondump.parse(COMP)
        self.assertEqual(fusiondump.diff(t, json.loads(json.dumps(t))), [])

    def test_unequal(self):
        t = fusiondump.parse(COMP)
        want = json.loads(json.dumps(t))
        del want["nodes"]["Blur1"]
        want["nodes"]["Glow1"] = {"type": "Glow"}
        want["nodes"]["MediaOut1"]["type"] = "Saver"
        want["links"] = [x for x in want["links"] if x["to"] != "MediaOut1"]
        want["links"].append({"from": "Glow1", "to": "MediaOut1", "input": "Input"})
        got = fusiondump.diff(t, want)
        for line in ("missing node Glow1 (Glow)", "extra node Blur1 (Blur)",
                     "type mismatch MediaOut1: MediaOut, intended Saver",
                     "missing link Glow1 -> MediaOut1.Input",
                     "extra link Blur1 -> MediaOut1.Input"):
            self.assertIn(line, got)

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            comp, tree, bad = (os.path.join(d, n) for n in ("a.comp", "t.json", "b.json"))
            Path(comp).write_text(COMP)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(["fusion-dump", "parse", comp, "--out", tree]), 0)
                self.assertEqual(cli.main(["fusion-dump", "diff", tree, tree]), 0)
                want = fusiondump.load_tree(tree)
                want["nodes"].pop("Blur1")
                Path(bad).write_text(json.dumps(want))
                self.assertEqual(cli.main(["fusion-dump", "diff", tree, bad]), 1)


class FakeItem:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def ExportFusionComp(self, path, idx):
        self.calls.append((path, idx))
        if self.ok:
            Path(path).write_text(COMP)
        return self.ok


class FakeTimeline:
    def __init__(self, items):
        self.items = items

    def GetItemListInTrack(self, kind, track):
        return self.items if (kind, track) == ("video", 1) else []

    def GetUniqueId(self):
        return "tl-id"

    def GetName(self):
        return "Night"


class FakeProject:
    def __init__(self, tl):
        self.tl = tl

    def GetName(self):
        return "Sandbox"

    def GetUniqueId(self):
        return "pid"

    def GetTimelineCount(self):
        return 1

    def GetTimelineByIndex(self, i):
        return self.tl


class FakePM:
    def __init__(self, project):
        self.project = project

    def GetCurrentProject(self):
        return self.project


class FakeResolve:
    def __init__(self, items):
        self.pm = FakePM(FakeProject(FakeTimeline(items)))

    def GetProjectManager(self):
        return self.pm


class Export(unittest.TestCase):
    def run_export(self, resolve, out, project="Sandbox", pid="pid", item=1):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            status = cmd_fusion_dump.export(resolve, project, pid, "Night", item, 1, 2, out)
        return status, err.getvalue()

    def test_writes_only_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            item = FakeItem()
            out = os.path.join(d, "x.comp")
            self.assertEqual(self.run_export(FakeResolve([item]), out)[0], 0)
            self.assertEqual(item.calls, [(out, 2)])
            self.assertEqual(fusiondump.parse(Path(out).read_text())["nodes"]["Blur1"]["type"], "Blur")

    def test_wrong_project_or_id_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            item = FakeItem()
            r = FakeResolve([item])
            self.assertEqual(self.run_export(r, os.path.join(d, "x.comp"), project="Other")[0], 2)
            self.assertEqual(self.run_export(r, os.path.join(d, "x.comp"), pid="nope")[0], 2)
            self.assertEqual(item.calls, [])

    def test_refused_in_a_git_tree(self):
        item = FakeItem()
        status, err = self.run_export(FakeResolve([item]), str(PRODUCTION / "x.comp"))
        self.assertEqual(status, 1)
        self.assertIn("git working tree", err)
        self.assertEqual(item.calls, [])

    def test_bad_item_and_failed_export(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.run_export(FakeResolve([FakeItem()]), os.path.join(d, "x.comp"), item=3)[0], 1)
            self.assertEqual(self.run_export(FakeResolve([FakeItem(ok=False)]), os.path.join(d, "y.comp"))[0], 1)

    def test_unavailable(self):
        with mock.patch.object(rpapi, "connect", side_effect=rpapi.ResolveUnavailable("down")):
            args = cli.build_parser().parse_args(
                ["fusion-dump", "export", "--project", "P", "--project-id", "i", "--timeline", "T",
                 "--item", "1", "--out", "/nonexistent-dir/x.comp"])
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cmd_fusion_dump.run(args), 1)


if __name__ == "__main__":
    unittest.main()
