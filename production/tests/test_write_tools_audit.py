"""
Fences from the 2026-10-31 audit on the Resolve write tools, against the shared
fakes: offline work runs before the Resolve lock, path matches ignore Unicode
normal form, apply_grade's plan covers the grade files' bytes, and a stabilize
plan goes stale when the track moves.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import os
import sys
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import resolve_fakes as rf  # noqa: E402
from rpresolve import api, markers, relink as rprelink, trimreview, workflows  # noqa: E402
from rpresolve.mcp import schema, server  # noqa: E402
from rpresolve.mcp.registry import ToolContext  # noqa: E402
import test_mcp_writes2 as w2  # noqa: E402
import test_relink as trl  # noqa: E402
import test_trim_markers as ttm  # noqa: E402

HELD = []  # 1 while a ResolveLock is held by the tool under test


class TrackedLock(api.ResolveLock):
    def __enter__(self):
        super().__enter__()
        HELD.append(1)
        return self

    def __exit__(self, *exc):
        HELD.pop()
        return super().__exit__(*exc)


def lock_state_of(fn, seen):
    """Wrap fn so each call records whether a lock was held."""
    def wrapped(*a, **kw):
        seen.append(bool(HELD))
        return fn(*a, **kw)
    return wrapped


class LockCase:
    def setUp(self):
        super().setUp()
        HELD.clear()
        patch = mock.patch.object(api, "ResolveLock", TrackedLock)
        patch.start()
        self.addCleanup(patch.stop)


class TestTrimOffline(LockCase, ttm.Base):
    def call(self, args):
        tool = server.build_registry().get("trim_review_markers")
        args = schema.with_defaults(tool.input_schema, {"project": ttm.P, **args})
        return tool.handler(args, ToolContext(session=rf.Session(self.resolve)))

    def test_the_review_runs_before_the_lock_and_the_marks_follow(self):
        seen = []
        with mock.patch.object(trimreview, "review_source",
                               lock_state_of(trimreview.review_source, seen)):
            base = {"timeline": "Clip [auto]", "source": self.source, "words": self.words_path,
                    "audio": False}
            dry = self.call(base)
            r = self.call({**base, "dry_run": False, "plan_sha": dry["plan_sha"]})
        self.assertEqual(seen, [False, False])  # once per call, never under the lock
        self.assertIn("added 3 of 3", r["summary"])


class TestRelinkOffline(LockCase, trl.Base):
    def test_the_walk_of_the_roots_runs_before_the_lock(self):
        seen = []
        with mock.patch.object(rprelink, "index_roots", lock_state_of(rprelink.index_roots, seen)):
            dry = self.run_tool()
        self.assertEqual(seen, [False])
        self.assertEqual(dry["status_counts"], {"VERIFIED": 2})


class TestGradeFileBytes(w2.Base):
    def setUp(self):
        super().setUp()
        self.lut = os.path.join(self.dir, "Look.cube")
        Path(self.lut).write_text("LUT_3D_SIZE 2\n")
        self.auto = self.make_auto()

    def test_a_lut_changed_after_the_dry_run_is_refused(self):
        args = {"timeline": "Edit [auto]", "lut": {"path": self.lut}}
        dry = w2.call("apply_grade", args, self.resolve)
        Path(self.lut).write_text("LUT_3D_SIZE 3\n")  # same path, other bytes
        rf.CALLS.clear()
        with self.assertRaisesRegex(workflows.Refused, "plan changed"):
            w2.call("apply_grade", {**args, "dry_run": False, "plan_sha": dry["plan_sha"]},
                    self.resolve)
        self.assertEqual(rf.mutating_calls(), [])

    def test_an_unchanged_lut_still_runs(self):
        dry, real = w2.run_twice("apply_grade", {"timeline": "Edit [auto]",
                                                 "lut": {"path": self.lut}}, self.resolve)
        self.assertEqual([r["ok"] for r in real["results"]], [True, True])


class TestStabilizeStale(w2.Base):
    def test_a_plan_is_refused_after_the_track_moved(self):
        auto = self.make_auto()
        items = auto.tracks["video"][0]
        dry = w2.call("stabilize", {"timeline": "Edit [auto]", "items": [1]}, self.resolve)
        items[0].end += 5  # the item was trimmed after the dry run
        rf.CALLS.clear()
        with self.assertRaisesRegex(workflows.Refused, "plan changed"):
            w2.call("stabilize", {"timeline": "Edit [auto]", "items": [1], "dry_run": False,
                                  "plan_sha": dry["plan_sha"]}, self.resolve)
        self.assertEqual([c for c in rf.CALLS if c[1] == "Stabilize"], [])

    def test_a_plan_that_already_ran_cannot_be_replayed(self):
        self.make_auto()
        args = {"timeline": "Edit [auto]", "items": [1]}
        dry, real = w2.run_twice("stabilize", args, self.resolve)
        rf.CALLS.clear()
        with self.assertRaisesRegex(workflows.Refused, "already ran"):
            w2.call("stabilize", {**args, "dry_run": False, "plan_sha": dry["plan_sha"]},
                    self.resolve)
        self.assertEqual([c for c in rf.CALLS if c[1] == "Stabilize"], [])
        again = w2.call("stabilize", args, self.resolve)  # a fresh dry run is a fresh plan
        self.assertNotEqual(again["plan_sha"], dry["plan_sha"])

    def test_a_plan_is_refused_after_an_item_was_added(self):
        auto = self.make_auto()
        dry = w2.call("stabilize", {"timeline": "Edit [auto]", "items": [1]}, self.resolve)
        auto.tracks["video"][0].append(rf.Item("P3.MOV", 86500, 86560, self.clip))
        with self.assertRaisesRegex(workflows.Refused, "plan changed"):
            w2.call("stabilize", {"timeline": "Edit [auto]", "items": [1], "dry_run": False,
                                  "plan_sha": dry["plan_sha"]}, self.resolve)


class TestNormalForm(unittest.TestCase):
    NFC = unicodedata.normalize("NFC", "/w/Café.mov")
    NFD = unicodedata.normalize("NFD", "/w/Café.mov")

    def test_the_two_spellings_differ_as_strings(self):
        self.assertNotEqual(self.NFC, self.NFD)

    def test_source_items_matches_an_nfd_clip_path_to_an_nfc_source(self):
        clip = rf.Clip("Café.mov", "c1", {"File Path": self.NFD, "FPS": "25"})
        tl = rf.Timeline("T [auto]", "t1", 25.0, 100, 150, {
            "video": [[rf.Item("Café.mov", 100, 150, clip, source=(0, 50))]], "audio": [[]]})
        items, _ = markers.source_items(tl, self.NFC)
        self.assertEqual(len(items["video"]) if isinstance(items, dict) else len(items), 1)


if __name__ == "__main__":
    unittest.main()
