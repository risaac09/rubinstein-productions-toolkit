"""
Offline unit tests for rpresolve.api and the per-item apply-lut / apply-drx
logic in resolve_workflow.py. Resolve is never imported: every project,
timeline, item, and node graph here is a small fake object.

stdlib unittest only.

Run: python3 -m unittest discover production/tests -v
"""

import io
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["RPRESOLVE_LOCK"] = os.path.join(  # a private lock: never the live one
    tempfile.gettempdir(), f"rpresolve-test-{os.getpid()}.lock")

from rpresolve import api
from resolve_workflow import (
    apply_drx_to_item,
    apply_drx_to_items,
    apply_lut_to_item,
    lut_paths_match,
    report_item_results,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeProject:
    def __init__(self, uid, name, timelines=None):
        self.uid, self.name = uid, name
        self.timelines = timelines or []
        self.current = self.timelines[0] if self.timelines else None
        self.settings = {}
        self.ignore_writes = False

    def GetUniqueId(self): return self.uid
    def GetName(self): return self.name
    def GetCurrentTimeline(self): return self.current
    def GetTimelineCount(self): return len(self.timelines)
    def GetTimelineByIndex(self, i): return self.timelines[i - 1]

    def SetCurrentTimeline(self, tl):
        if getattr(self, "fail_set_timeline", False):
            return False
        self.current = tl
        return True

    def SetSetting(self, key, value):
        if not self.ignore_writes:
            self.settings[key] = value
        return True  # Resolve reports success even when a dialog eats it

    def GetSetting(self, key): return self.settings.get(key, "")


class FakePM:
    def __init__(self, project): self.project = project
    def GetCurrentProject(self): return self.project


class FakeTimeline:
    def __init__(self, uid, name, tc="01:00:00:00"):
        self.uid, self.name, self.tc = uid, name, tc
        self.fail_set_tc = False

    def GetUniqueId(self): return self.uid
    def GetName(self): return self.name
    def GetCurrentTimecode(self): return self.tc

    def SetCurrentTimecode(self, tc):
        if self.fail_set_tc:
            return False
        self.tc = tc
        return True


class FakeResolve:
    def __init__(self, page="edit"):
        self.page = page
        self.raise_on_open = False

    def GetCurrentPage(self): return self.page

    def OpenPage(self, page):
        if self.raise_on_open:
            raise RuntimeError("boom")
        self.page = page
        return True


class FakeGraph:
    def __init__(self, labels, luts=None, lut_report=None):
        self.labels = list(labels)
        self.luts = dict(luts or {})
        self.lut_report = lut_report  # override what GetLUT returns
        self.set_lut_ok = True
        self.drx_ok = True
        self.drx_labels = None
        self.calls = []

    def GetNumNodes(self): return len(self.labels)
    def GetNodeLabel(self, i): return self.labels[i - 1]

    def GetLUT(self, i):
        return self.lut_report if self.lut_report is not None else self.luts.get(i, "")

    def SetLUT(self, i, path):
        self.calls.append(("SetLUT", i, path))
        if self.set_lut_ok:
            self.luts[i] = path
        return self.set_lut_ok

    def ApplyGradeFromDRX(self, path, mode):
        self.calls.append(("ApplyGradeFromDRX", path, mode))
        if self.drx_ok and self.drx_labels is not None:
            self.labels = list(self.drx_labels)
        return self.drx_ok


class FakeItem:
    def __init__(self, name, graph):
        self.name, self.graph = name, graph
        self.props = {}

    def GetName(self): return self.name
    def GetNodeGraph(self): return self.graph

    def SetProperty(self, key, value):
        # Resolve stores floats at single precision; round through float32.
        if isinstance(value, float):
            value = struct.unpack("f", struct.pack("f", value))[0]
        self.props[key] = value
        return True

    def GetProperty(self, key): return self.props.get(key)


class FakeTimelineNoDRX:
    """A Timeline that would blow up if anyone called the removed API."""
    def ApplyGradeFromDRX(self, *a):
        raise AssertionError("Timeline.ApplyGradeFromDRX must never be called")


# ---------------------------------------------------------------------------
# rpresolve.api
# ---------------------------------------------------------------------------

class TestProjectPin(unittest.TestCase):
    def test_same_id_passes(self):
        proj = FakeProject("abc", "Sandbox")
        pin = api.ProjectPin(proj)
        self.assertIs(pin.check(FakePM(proj)), proj)

    def test_changed_id_raises(self):
        pin = api.ProjectPin(FakeProject("abc", "Sandbox"))
        with self.assertRaises(api.ProjectChanged):
            pin.check(FakePM(FakeProject("xyz", "Client Job")))

    def test_same_name_different_id_raises(self):
        pin = api.ProjectPin(FakeProject("abc", "Sandbox"))
        with self.assertRaises(api.ProjectChanged):
            pin.check(FakePM(FakeProject("def", "Sandbox")))

    def test_no_project_open_raises(self):
        pin = api.ProjectPin(FakeProject("abc", "Sandbox"))
        with self.assertRaises(api.ProjectChanged):
            pin.check(FakePM(None))

    def test_require_name(self):
        proj = FakeProject("abc", "Sandbox")
        pin = api.ProjectPin(proj)
        self.assertEqual(pin.require_name("Sandbox"), "Sandbox")
        self.assertEqual(pin.require_name("Sandbox", FakePM(proj)), "Sandbox")
        with self.assertRaises(api.ProjectChanged):
            pin.require_name("Client Job")

    def test_missing_unique_id_refuses_to_pin(self):
        with self.assertRaises(api.ResolveAPIError):
            api.ProjectPin(FakeProject(None, "Old"))


class TestUISnapshot(unittest.TestCase):
    def setUp(self):
        self.tl_a = FakeTimeline("t1", "Main Edit", "01:00:10:00")
        self.tl_b = FakeTimeline("t2", "Selects", "01:00:00:00")
        self.project = FakeProject("abc", "Sandbox", [self.tl_a, self.tl_b])
        self.resolve = FakeResolve("edit")

    def test_restores_page_timeline_and_timecode(self):
        with api.UISnapshot(self.resolve, self.project) as snap:
            self.resolve.OpenPage("color")
            self.project.SetCurrentTimeline(self.tl_b)
            self.tl_a.tc = "01:05:00:00"
        self.assertEqual(snap.problems, [])
        self.assertEqual(self.resolve.page, "edit")
        self.assertIs(self.project.current, self.tl_a)
        self.assertEqual(self.tl_a.tc, "01:00:10:00")

    def test_restore_errors_are_collected_not_raised(self):
        with api.UISnapshot(self.resolve, self.project) as snap:
            self.resolve.OpenPage("color")
            self.resolve.raise_on_open = True
            self.tl_a.tc = "01:05:00:00"
            self.tl_a.fail_set_tc = True
        self.assertEqual(len(snap.problems), 2)
        self.assertTrue(any("playhead" in p for p in snap.problems))
        self.assertTrue(any("page" in p for p in snap.problems))

    def test_missing_timeline_reported(self):
        with api.UISnapshot(self.resolve, self.project) as snap:
            self.project.timelines = [self.tl_b]
            self.project.current = self.tl_b
        self.assertTrue(any("not found" in p for p in snap.problems))

    def test_failed_timeline_switch_leaves_other_playhead_alone(self):
        with api.UISnapshot(self.resolve, self.project) as snap:
            self.project.SetCurrentTimeline(self.tl_b)
            self.project.fail_set_timeline = True
        self.assertIs(self.project.current, self.tl_b)
        self.assertEqual(self.tl_b.tc, "01:00:00:00")
        self.assertTrue(any("could not switch back" in p for p in snap.problems))
        self.assertFalse(any("playhead" in p for p in snap.problems))

    def test_body_exception_propagates(self):
        with self.assertRaises(ValueError):
            with api.UISnapshot(self.resolve, self.project):
                self.resolve.OpenPage("color")
                raise ValueError("body failed")
        self.assertEqual(self.resolve.page, "edit")


class TestReadback(unittest.TestCase):
    def test_setting_ok(self):
        proj = FakeProject("abc", "Sandbox")
        self.assertEqual(api.set_setting_checked(proj, "timelineFrameRate", 24), "24")

    def test_setting_mismatch_raises_with_dialog_hint(self):
        proj = FakeProject("abc", "Sandbox")
        proj.ignore_writes = True
        with self.assertRaises(api.WriteNotApplied) as ctx:
            api.set_setting_checked(proj, "timelineFrameRate", "24")
        self.assertIn("modal dialog", str(ctx.exception))

    def test_property_within_tolerance(self):
        item = FakeItem("clip", None)
        api.set_property_checked(item, "ZoomX", 1.25)

    def test_property_float32_rounding_is_not_a_failure(self):
        item = FakeItem("clip", None)
        for value in (123.45, 1920.3, -3840.7, 0.1):
            api.set_property_checked(item, "Pan", value)

    def test_property_numeric_string_readback(self):
        item = FakeItem("clip", None)
        item.SetProperty = lambda k, v: True
        item.GetProperty = lambda k: "1.2500000"
        api.set_property_checked(item, "ZoomX", 1.25)

    def test_property_out_of_tolerance_raises(self):
        item = FakeItem("clip", None)
        item.SetProperty = lambda k, v: True
        item.GetProperty = lambda k: 1.2501
        with self.assertRaises(api.WriteNotApplied):
            api.set_property_checked(item, "ZoomX", 1.25)

    def test_property_non_numeric(self):
        item = FakeItem("clip", None)
        api.set_property_checked(item, "CompositeMode", "Normal")
        item.SetProperty = lambda k, v: True
        with self.assertRaises(api.WriteNotApplied):
            api.set_property_checked(item, "CompositeMode", "Screen")


class TestGraphHelpers(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(api.graph_labels(FakeGraph(["CST", None, "Look"])), ["CST", "", "Look"])

    def test_match(self):
        g = FakeGraph(["CST", "Balance"])
        self.assertEqual(api.assert_graph_matches(g, {"num_nodes": 2, "labels": ["CST", "Balance"]}), [])

    def test_count_and_label_mismatch(self):
        g = FakeGraph(["CST", "Balanse", "Extra"])
        problems = api.assert_graph_matches(g, {"num_nodes": 2, "labels": ["CST", "Balance"]})
        self.assertIn("num_nodes: expected 2, got 3", problems)
        self.assertTrue(any(p.startswith("node 2 label") for p in problems))
        self.assertTrue(any(p.startswith("node 3 label") for p in problems))
        self.assertEqual(len(problems), 3)


class TestExitClean(unittest.TestCase):
    def test_flushes_then_exits_with_code(self):
        # Run in a child so os._exit does not end the test runner; the
        # parent reads the piped output, which is the case that was lost.
        import subprocess
        code = (
            "import sys; sys.path.insert(0, %r)\n"
            "from rpresolve import api\n"
            "print('before exit')\n"
            "api.exit_clean(3)\n"
        ) % str(Path(__file__).resolve().parent.parent)
        proc = subprocess.run([sys.executable, "-c", code], stdout=subprocess.PIPE)
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(proc.stdout.decode().strip(), "before exit")


# ---------------------------------------------------------------------------
# apply-lut / apply-drx per-item logic
# ---------------------------------------------------------------------------

class TestLutPathsMatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "LUT")
        os.makedirs(os.path.join(self.root, "Camera"))
        self.lut = os.path.join(self.root, "Camera", "GH7.cube")
        Path(self.lut).write_text("LUT_3D_SIZE 2\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_absolute_equal_after_realpath(self):
        # tempdirs on macOS live under /var -> /private/var; realpath evens it out
        self.assertTrue(lut_paths_match(self.lut, os.path.realpath(self.lut)))

    def test_relative_to_lut_root(self):
        self.assertTrue(lut_paths_match(self.lut, "Camera/GH7.cube", [self.root]))

    def test_relative_tail_without_known_root(self):
        self.assertTrue(lut_paths_match(self.lut, "Camera/GH7.cube"))

    def test_different_file(self):
        self.assertFalse(lut_paths_match(self.lut, "Camera/GH5.cube", [self.root]))
        self.assertFalse(lut_paths_match(self.lut, "/somewhere/else/GH7.cube", [self.root]))

    def test_same_basename_other_folder_under_known_root(self):
        top = os.path.join(self.root, "GH7.cube")
        Path(top).write_text("LUT_3D_SIZE 2\n")
        self.assertFalse(lut_paths_match(self.lut, "GH7.cube", [self.root]))
        self.assertTrue(lut_paths_match(top, "GH7.cube", [self.root]))

    def test_symlink_into_root_matches_in_root_path(self):
        outside = os.path.join(self.tmp.name, "repo", "Look.cube")
        os.makedirs(os.path.dirname(outside))
        Path(outside).write_text("LUT_3D_SIZE 2\n")
        link = os.path.join(self.root, "Look.cube")
        os.symlink(outside, link)
        self.assertTrue(lut_paths_match(link, "Look.cube", [self.root]))
        from resolve_workflow import _path_under
        self.assertTrue(_path_under(link, self.root, follow_links=False))
        self.assertFalse(_path_under(link, self.root))

    def test_partial_component_is_not_a_match(self):
        self.assertFalse(lut_paths_match(self.lut, "H7.cube"))

    def test_empty_readback(self):
        self.assertFalse(lut_paths_match(self.lut, ""))
        self.assertFalse(lut_paths_match(self.lut, None))


class TestApplyLut(unittest.TestCase):
    LUT = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT/Cam/GH7.cube"
    ROOTS = ("/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT",)

    def test_ok_with_readback(self):
        g = FakeGraph(["CST", "Balance"])
        ok, detail = apply_lut_to_item(FakeItem("c1", g), 1, self.LUT, self.ROOTS)
        self.assertTrue(ok, detail)
        self.assertEqual(g.calls, [("SetLUT", 1, self.LUT)])

    def test_relative_readback_accepted(self):
        g = FakeGraph(["CST"], lut_report="Cam/GH7.cube")
        ok, _ = apply_lut_to_item(FakeItem("c1", g), 1, self.LUT, self.ROOTS)
        self.assertTrue(ok)

    def test_readback_mismatch_fails(self):
        g = FakeGraph(["CST"], lut_report="Cam/Other.cube")
        ok, detail = apply_lut_to_item(FakeItem("c1", g), 1, self.LUT, self.ROOTS)
        self.assertFalse(ok)
        self.assertIn("read back", detail)

    def test_setlut_true_but_nothing_stored_fails(self):
        g = FakeGraph(["CST"], lut_report="")
        ok, _ = apply_lut_to_item(FakeItem("c1", g), 1, self.LUT, self.ROOTS)
        self.assertFalse(ok)

    def test_missing_node_fails_without_write(self):
        g = FakeGraph(["CST"])
        ok, detail = apply_lut_to_item(FakeItem("c1", g), 2, self.LUT, self.ROOTS)
        self.assertFalse(ok)
        self.assertIn("cannot create node 2", detail)
        self.assertEqual(g.calls, [])

    def test_setlut_false_fails(self):
        g = FakeGraph(["CST"])
        g.set_lut_ok = False
        ok, _ = apply_lut_to_item(FakeItem("c1", g), 1, self.LUT, self.ROOTS)
        self.assertFalse(ok)

    def test_no_graph_fails(self):
        ok, _ = apply_lut_to_item(FakeItem("c1", None), 1, self.LUT, self.ROOTS)
        self.assertFalse(ok)


class TestApplyDrx(unittest.TestCase):
    def test_per_item_graph_call_and_readback(self):
        g = FakeGraph(["old"])
        g.drx_labels = ["CST", "Balance", "Look"]
        ok, detail = apply_drx_to_item(FakeItem("c1", g), "/g/look.drx", 1)
        self.assertTrue(ok)
        self.assertIn("3 node(s)", detail)
        self.assertEqual(g.calls, [("ApplyGradeFromDRX", "/g/look.drx", 1)])

    def test_true_returning_noop_fails(self):
        # A modal dialog swallows the write: setter says True, graph unchanged.
        g = FakeGraph(["old"])
        ok, detail = apply_drx_to_item(FakeItem("c1", g), "/g/look.drx", 1)
        self.assertFalse(ok)
        self.assertIn("unchanged", detail)

    def test_same_labels_but_changed_tools_counts_as_applied(self):
        g = FakeGraph([""])
        g.tools = ["Primary"]
        g.GetToolsInNode = lambda i: list(g.tools)
        def apply(path, mode):
            g.tools = ["Primary", "Curves"]
            return True
        g.ApplyGradeFromDRX = apply
        ok, detail = apply_drx_to_item(FakeItem("c1", g), "/g/look.drx", 0)
        self.assertTrue(ok, detail)

    def test_apply_false_fails(self):
        g = FakeGraph(["old"])
        g.drx_ok = False
        ok, _ = apply_drx_to_item(FakeItem("c1", g), "/g/look.drx", 0)
        self.assertFalse(ok)

    def test_empty_graph_after_apply_fails(self):
        g = FakeGraph(["old"])
        g.drx_labels = []
        ok, detail = apply_drx_to_item(FakeItem("c1", g), "/g/look.drx", 0)
        self.assertFalse(ok)
        self.assertIn("empty", detail)

    def test_items_report_counts_and_status(self):
        good = FakeGraph(["a"]); good.drx_labels = ["x", "y"]
        bad = FakeGraph(["a"]); bad.drx_ok = False
        results = apply_drx_to_items([FakeItem("c1", good), FakeItem("c2", bad)], "/g/look.drx", 0)
        buf = io.StringIO()
        with redirect_stdout(buf):
            status = report_item_results(results, "Applied DRX grade to")
        out = buf.getvalue()
        self.assertEqual(status, 1)
        self.assertIn("[ok] c1", out)
        self.assertIn("[FAIL] c2", out)
        self.assertIn("1/2 clips", out)

    def test_all_ok_status_zero(self):
        g = FakeGraph(["a"]); g.drx_labels = ["x"]
        buf = io.StringIO()
        with redirect_stdout(buf):
            status = report_item_results(apply_drx_to_items([FakeItem("c1", g)], "/g/l.drx", 2), "Applied")
        self.assertEqual(status, 0)



class TestCommandsEndToEnd(unittest.TestCase):
    """cmd_apply_drx / cmd_apply_lut against a fake Resolve, to pin that the
    commands go through the per-item Graph path and return a status."""

    def _resolve_with(self, items):
        timeline = FakeTimelineNoDRX()
        timeline.GetItemListInTrack = lambda kind, idx: items
        project = FakeProject("abc", "Sandbox")
        project.current = timeline
        project.refreshed = False
        def refresh():
            project.refreshed = True
            return True
        project.RefreshLUTList = refresh
        resolve = FakeResolve()
        resolve.GetProjectManager = lambda: FakePM(project)
        return resolve, project

    def test_apply_drx_command(self):
        import argparse
        from unittest import mock
        import resolve_workflow as rw
        g = FakeGraph(["a"]); g.drx_labels = ["x", "y"]
        resolve, _ = self._resolve_with([FakeItem("c1", g)])
        with tempfile.NamedTemporaryFile(suffix=".drx") as drx:
            args = argparse.Namespace(drx_file=drx.name, track=1, mode=2, camera=None, config=None)
            buf = io.StringIO()
            with mock.patch.object(rw, "get_resolve", return_value=resolve), redirect_stdout(buf):
                status = rw.cmd_apply_drx(args)
            self.assertEqual(status, 0, buf.getvalue())
            self.assertEqual(g.calls, [("ApplyGradeFromDRX", str(Path(drx.name).resolve()), 2)])

    def test_apply_lut_command_reports_mismatch(self):
        import argparse
        from unittest import mock
        import resolve_workflow as rw
        g = FakeGraph(["CST"], lut_report="Other/Wrong.cube")
        resolve, project = self._resolve_with([FakeItem("c1", g)])
        with tempfile.NamedTemporaryFile(suffix=".cube") as lut:
            args = argparse.Namespace(lut_file=lut.name, track=1, node=1, camera=None, config=None)
            buf = io.StringIO()
            with mock.patch.object(rw, "get_resolve", return_value=resolve), redirect_stdout(buf):
                status = rw.cmd_apply_lut(args)
        self.assertEqual(status, 1)
        self.assertTrue(project.refreshed)
        self.assertIn("[FAIL] c1", buf.getvalue())

    def test_apply_lut_sends_symlink_path_not_target(self):
        import argparse
        from unittest import mock
        import resolve_workflow as rw
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "LUT")
            os.makedirs(root)
            target = os.path.join(tmp, "repo", "Look.cube")
            os.makedirs(os.path.dirname(target))
            Path(target).write_text("LUT_3D_SIZE 2\n")
            link = os.path.join(root, "Look.cube")
            os.symlink(target, link)
            g = FakeGraph(["CST"], lut_report="Look.cube")
            resolve, _ = self._resolve_with([FakeItem("c1", g)])
            args = argparse.Namespace(lut_file=link, track=1, node=1, camera=None, config=None)
            buf = io.StringIO()
            with mock.patch.object(rw, "get_resolve", return_value=resolve), \
                    mock.patch.object(rw.rpapi, "DEFAULT_LUT_ROOTS", (root,)), redirect_stdout(buf):
                status = rw.cmd_apply_lut(args)
            self.assertEqual(status, 0, buf.getvalue())
            self.assertEqual(g.calls, [("SetLUT", 1, os.path.abspath(link))])
            self.assertNotIn("WARNING", buf.getvalue())


PRODUCTION = Path(__file__).resolve().parent.parent


class TestMainExitPaths(unittest.TestCase):
    def test_ctrl_c_exits_130_through_exit_clean(self):
        code = (
            "import sys; sys.path.insert(0, %r)\n"
            "import resolve_workflow as rw\n"
            "def boom(args): print('working'); raise KeyboardInterrupt\n"
            "rw.cmd_info = boom\n"
            "sys.argv = ['resolve_workflow.py', 'info']\n"
            "rw.main()\n"
        ) % str(PRODUCTION)
        proc = subprocess.run([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(proc.returncode, 130)
        self.assertEqual(proc.stdout.decode().strip(), "working")
        self.assertIn("Interrupted.", proc.stderr.decode())

    def test_symlinked_script_finds_rpresolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = os.path.join(tmp, "rw.py")
            os.symlink(str(PRODUCTION / "resolve_workflow.py"), link)
            proc = subprocess.run([sys.executable, link, "--help"], cwd=tmp,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())

    def test_lone_copy_fails_with_clear_message(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            copy = os.path.join(tmp, "rw.py")
            shutil.copy(str(PRODUCTION / "resolve_workflow.py"), copy)
            proc = subprocess.run([sys.executable, copy, "--help"], cwd=tmp,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("rpresolve/ directory", proc.stderr.decode())
        self.assertNotIn("Traceback", proc.stderr.decode())


if __name__ == "__main__":
    unittest.main()
