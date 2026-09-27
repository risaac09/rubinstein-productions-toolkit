"""
Offline unit tests for rpresolve.ingest: the pure plan, and apply_plan
against a fake media pool (no Resolve needed). Paths are synthetic.

stdlib unittest only.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import argparse
import io
import os
import tempfile
import sys
import unicodedata
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["RPRESOLVE_LOCK"] = os.path.join(  # a private lock: never the live one
    tempfile.gettempdir(), f"rpresolve-test-{os.getpid()}.lock")

from rpresolve import api, detect, ingest


def row(path, camera="Panasonic DC-GH7", profile="V-Log", cs=detect.CS_VLOG,
        data_level="full", vfr="no", note=""):
    return {"path": path, "camera": camera, "profile": profile, "input_color_space": cs,
            "data_level": data_level, "vfr": vfr, "confidence": "high", "rule": "4", "note": note}


class FakeClip:
    def __init__(self, path, refuse=()):
        self.props = {"File Path": path, "Input Color Space": "Rec.709 (Scene)", "Data Level": "Auto"}
        self.refuse = set(refuse)
        self.writes = []

    def GetClipProperty(self, key=None):
        return dict(self.props) if key is None else self.props.get(key, "")

    def SetClipProperty(self, key, value):
        self.writes.append((key, value))
        if key not in self.refuse:
            self.props[key] = value
        return True  # Resolve reports success even when a write does not land


class FakeFolder:
    def __init__(self, name, clips=()):
        self.name, self.clips, self.subs = name, list(clips), []

    def GetName(self):
        return self.name

    def GetClipList(self):
        return list(self.clips)

    def GetSubFolderList(self):
        return list(self.subs)


class FakePool:
    def __init__(self, root, drop=(), refuse=()):
        self.root, self.current = root, root
        self.drop, self.refuse = set(drop), refuse
        self.created, self.imports = [], []

    def GetRootFolder(self):
        return self.root

    def AddSubFolder(self, parent, name):
        f = FakeFolder(name)
        parent.subs.append(f)
        self.created.append(name)
        return f

    def SetCurrentFolder(self, folder):
        self.current = folder
        return True

    def GetCurrentFolder(self):
        return self.current

    def ImportMedia(self, paths):
        self.imports.append((self.current.GetName(), list(paths)))
        clips = [FakeClip(p, self.refuse) for p in paths if p not in self.drop]
        self.current.clips.extend(clips)
        return clips


class TestPlan(unittest.TestCase):
    def test_actions_by_profile(self):
        rows = [
            row("/media/card/a.mov"),
            row("/media/card/b.mov", camera="iPhone 16 Pro Max", profile="SDR",
                cs=detect.CS_CAMERA_SDR, data_level="video", vfr="yes"),
            row("/media/card/c.braw", camera="Blackmagic PYXIS 6K", profile="BRAW",
                cs=detect.CS_CAMERA_RAW, data_level="unknown"),
            row("/media/card/d.mp4", camera="DJI Osmo Pocket 3", profile=detect.REVIEW, cs="",
                note="Normal vs D-Log M is not in the headers"),
            row("/media/card/e.mov", camera="DaVinci Resolve render", profile="derived", cs=""),
            row("/media/card/f.lrf", camera="DJI low-res proxy (.LRF)", profile="proxy", cs=""),
            row("/media/card/g.mov", camera="unknown", profile=detect.CORRUPT, cs=""),
        ]
        plan = ingest.plan_ingest(rows, [], parent="Source")
        got = [(e["action"], e["bin"], e["input_color_space"], e["data_level"]) for e in plan]
        self.assertEqual(got, [
            (ingest.TAG, ["Source", "Panasonic DC-GH7"], detect.CS_VLOG, "Full"),
            (ingest.TAG, ["Source", "iPhone 16 Pro Max"], detect.CS_CAMERA_SDR, "Video"),
            (ingest.IMPORT_ONLY, ["Source", "Blackmagic PYXIS 6K"], "", ""),
            (ingest.IMPORT_ONLY, ["Source", ingest.REVIEW_BIN], "", ""),
            (ingest.SKIP, None, "", ""),
            (ingest.SKIP, None, "", ""),
            (ingest.SKIP, None, "", ""),
        ])
        self.assertIn("VFR", plan[1]["reason"])
        self.assertIn("D-Log M", plan[3]["reason"])

    def test_existing_pool_paths_are_never_planned(self):
        nfd = unicodedata.normalize("NFD", "/media/card/Café.mov")
        plan = ingest.plan_ingest([row("/media/card/Café.mov")], [nfd])
        self.assertEqual(plan[0]["action"], ingest.SKIP)
        self.assertIn("already in the media pool", plan[0]["reason"])

    def test_a_path_listed_twice_is_planned_once(self):
        plan = ingest.plan_ingest([row("/media/card/a.mov"), row("/media/card/a.mov")], [])
        self.assertEqual([e["action"] for e in plan], [ingest.TAG, ingest.SKIP])

    def test_unknown_data_level_writes_none(self):
        plan = ingest.plan_ingest([row("/media/card/a.mov", data_level="unknown")], [])
        self.assertEqual(plan[0]["data_level"], "")

    def test_camera_bin_name_has_no_slashes(self):
        self.assertEqual(ingest.camera_bin("Atomos Ninja V / DC-GH7"), "Atomos Ninja V - DC-GH7")
        self.assertEqual(ingest.camera_bin(""), "Unknown")


class TestApply(unittest.TestCase):
    def setUp(self):
        self.old = FakeClip("/media/old/x.mov")
        self.root = FakeFolder("Master", [self.old])

    def test_imports_by_bin_and_tags_with_read_back(self):
        pool = FakePool(self.root)
        rows = [row("/media/card/a.mov"), row("/media/card/b.mov", data_level="unknown"),
                row("/media/card/d.mp4", profile=detect.REVIEW, cs="")]
        plan = ingest.plan_ingest(rows, ingest.pool_file_paths(self.root))
        checks = []
        results = ingest.apply_plan(pool, self.root, plan, check=lambda: checks.append(1))
        self.assertEqual([r["result"] for r in results], ["tagged", "tagged", "imported"])
        self.assertEqual(results[0]["input_color_space"], detect.CS_VLOG)
        self.assertEqual(results[0]["data_level"], "Full")
        self.assertEqual(results[1]["data_level"], "")
        self.assertEqual(len(checks), 2)  # one pin check per bin written
        self.assertEqual(sorted(pool.created), ["Panasonic DC-GH7", "Review", "Source"])
        self.assertEqual(self.old.writes, [])  # the pre-existing clip is never touched
        review = [c for c in pool.imports if c[0] == ingest.REVIEW_BIN][0]
        self.assertEqual(review[1], ["/media/card/d.mp4"])

    def test_a_write_that_does_not_land_fails_that_row(self):
        pool = FakePool(self.root, refuse=("Input Color Space",))
        plan = ingest.plan_ingest([row("/media/card/a.mov")], [])
        results = ingest.apply_plan(pool, self.root, plan)
        self.assertTrue(results[0]["result"].startswith("FAILED: Clip property 'Input Color Space'"))

    def test_a_path_resolve_did_not_import_fails_that_row(self):
        pool = FakePool(self.root, drop=("/media/card/b.mov",))
        plan = ingest.plan_ingest([row("/media/card/a.mov"), row("/media/card/b.mov")], [])
        results = ingest.apply_plan(pool, self.root, plan)
        self.assertEqual(results[0]["result"], "tagged")
        self.assertTrue(results[1]["result"].startswith("FAILED: not imported"))

    def test_existing_bins_are_reused(self):
        source = FakeFolder("Source")
        source.subs.append(FakeFolder("Panasonic DC-GH7"))
        self.root.subs.append(source)
        pool = FakePool(self.root)
        ingest.apply_plan(pool, self.root, ingest.plan_ingest([row("/media/card/a.mov")], []))
        self.assertEqual(pool.created, [])

    def test_report_joins_reasons(self):
        plan = ingest.plan_ingest([row("/media/card/a.mov", vfr="yes")], [])
        results = ingest.apply_plan(FakePool(self.root), self.root, plan)
        lines = ingest.format_report(plan, results).splitlines()
        self.assertEqual(lines[0].split("\t"), list(ingest.REPORT_COLUMNS))
        self.assertIn("VFR", lines[1])


class TestApplyStops(unittest.TestCase):
    def test_a_changed_project_stops_the_run_and_still_reports(self):
        root = FakeFolder("Master")
        pool = FakePool(root)
        rows = [row("/media/card/a.mov"),
                row("/media/card/b.mov", camera="iPhone 16 Pro Max", profile="SDR", cs=detect.CS_CAMERA_SDR)]
        plan = ingest.plan_ingest(rows, [])
        calls = []

        def check():
            calls.append(1)
            if len(calls) == 2:
                raise api.ProjectChanged("Open project changed")
        results = ingest.apply_plan(pool, root, plan, check=check)
        self.assertEqual(results[0]["result"], "tagged")
        self.assertTrue(results[1]["result"].startswith("FAILED: not run (ProjectChanged"))
        self.assertEqual(len(pool.imports), 1)  # nothing imported after the stop


class TestApplyBridgeFailure(unittest.TestCase):
    def test_an_import_that_raises_stops_and_reports(self):
        root = FakeFolder("Master")
        pool = FakePool(root)
        rows = [row("/media/card/a.mov"),
                row("/media/card/b.mov", camera="iPhone 16 Pro Max", profile="SDR", cs=detect.CS_CAMERA_SDR)]
        real = pool.ImportMedia

        def flaky(paths):
            if "/media/card/b.mov" in paths:
                raise RuntimeError("bridge dropped")
            return real(paths)
        pool.ImportMedia = flaky
        results = ingest.apply_plan(pool, root, ingest.plan_ingest(rows, []))
        self.assertEqual(results[0]["result"], "tagged")
        self.assertEqual(results[1]["result"], "FAILED: not run (RuntimeError: bridge dropped)")


class FakeProject:
    def __init__(self, name="Sandbox", mode="davinciYRGBColorManagedv2"):
        self.name, self.mode = name, mode
        self.root = FakeFolder("Master")
        self.pool = FakePool(self.root)

    def GetUniqueId(self):
        return "id-" + self.name

    def GetName(self):
        return self.name

    def GetSetting(self, key):
        return self.mode if key == "colorScienceMode" else ""

    def GetMediaPool(self):
        return self.pool


class FakeResolve:
    def __init__(self, project):
        self.project = project

    def GetProjectManager(self):
        return self

    def GetCurrentProject(self):
        return self.project


class TestCmdIngest(unittest.TestCase):
    def run_cmd(self, project, rows, **kw):
        import resolve_workflow as rw
        args = argparse.Namespace(paths=["/media/card"], project=kw.get("name", "Sandbox"),
                                  bin="Source", dry_run=kw.get("dry_run", False), out=None)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(rw, "get_resolve", return_value=FakeResolve(project)), \
                mock.patch.object(rw.rpdetect, "detect_paths", return_value=(rows, [])), \
                redirect_stdout(out), redirect_stderr(err):
            code = rw.cmd_ingest(args)
        return code, out.getvalue(), err.getvalue()

    def test_refuses_a_project_it_was_not_named_for(self):
        p = FakeProject()
        code, _, err = self.run_cmd(p, [row("/media/card/a.mov")], name="Other")
        self.assertEqual(code, 1)
        self.assertIn("expected 'Other'", err)
        self.assertEqual(p.pool.imports, [])

    def test_refuses_a_project_without_color_management(self):
        p = FakeProject(mode="davinciYRGB")
        code, _, err = self.run_cmd(p, [row("/media/card/a.mov")])
        self.assertEqual(code, 1)
        self.assertIn("Color Managed", err)
        self.assertEqual(p.pool.imports, [])

    def test_dry_run_writes_nothing(self):
        p = FakeProject()
        code, out, err = self.run_cmd(p, [row("/media/card/a.mov")], dry_run=True)
        self.assertEqual(code, 0)
        self.assertIn("planned", out)
        self.assertEqual(p.pool.imports, [])
        self.assertEqual(p.pool.created, [])

    def test_exit_codes_and_folder_restore(self):
        p = FakeProject()
        code, out, _ = self.run_cmd(p, [row("/media/card/a.mov")])
        self.assertEqual(code, 0)
        self.assertIn("tagged", out)
        self.assertIs(p.pool.current, p.root)  # the current bin is put back
        for extra in (row("/media/card/r.mp4", profile=detect.REVIEW, cs=""),
                      row("/media/card/v.mov", vfr="yes")):
            code, _, _ = self.run_cmd(FakeProject(), [row("/media/card/a.mov"), extra])
            self.assertEqual(code, 2, extra["path"])
        refusing = FakeProject()
        refusing.pool.refuse = ("Input Color Space",)
        self.assertEqual(self.run_cmd(refusing, [row("/media/card/a.mov")])[0], 1)


class TestSetClipPropertyChecked(unittest.TestCase):
    def test_read_back(self):
        clip = FakeClip("/media/a.mov")
        self.assertEqual(api.set_clip_property_checked(clip, "Data Level", "Video"), "Video")
        clip = FakeClip("/media/a.mov", refuse=("Data Level",))
        with self.assertRaises(api.WriteNotApplied):
            api.set_clip_property_checked(clip, "Data Level", "Video")


if __name__ == "__main__":
    unittest.main()
