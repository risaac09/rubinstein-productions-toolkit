"""
Offline unit tests for rpresolve.ingest: the pure plan, and apply_plan
against a fake media pool (no Resolve needed). Paths are synthetic.

stdlib unittest only.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import sys
import unicodedata
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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


class TestSetClipPropertyChecked(unittest.TestCase):
    def test_read_back(self):
        clip = FakeClip("/media/a.mov")
        self.assertEqual(api.set_clip_property_checked(clip, "Data Level", "Video"), "Video")
        clip = FakeClip("/media/a.mov", refuse=("Data Level",))
        with self.assertRaises(api.WriteNotApplied):
            api.set_clip_property_checked(clip, "Data Level", "Video")


if __name__ == "__main__":
    unittest.main()
