"""
settingsguard: the settings Resolve changes on a new timeline besides what
cut and sync wrote, read and reported and never corrected. Pure tests of the
module, then cut and sync's call sites against the shared Resolve fakes, and
against thin fakes that cannot report their settings. Synthetic setting names
and values only; nothing here touches Resolve.

Run: /usr/bin/python3 -m unittest -v production.tests.test_settingsguard
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
os.environ.setdefault("RPRESOLVE_LOCK", os.path.join(
    tempfile.gettempdir(), f"rpresolve-test-{os.getpid()}.lock"))

import resolve_fakes as rf  # noqa: E402
from rpresolve import api, cut, cutlist, settingsguard as sg, syncbuild as sb, workflows  # noqa: E402

_SAVED_SWITCH = {}


def setUpModule():
    # An inherited kill switch would hide the guard from every test here.
    _SAVED_SWITCH["value"] = os.environ.pop(sg.ENV_OFF, None)


def tearDownModule():
    if _SAVED_SWITCH.get("value") is not None:
        os.environ[sg.ENV_OFF] = _SAVED_SWITCH["value"]


BEFORE = {"isAutoColorManage": "0", "rcmPresetMode": "Custom", "colorSpaceOutputGamma": "Gamma 2.4"}
AFTER = {"isAutoColorManage": "1", "rcmPresetMode": "SDR", "colorSpaceOutputGamma": ""}
CLIP = {"name": "hi", "spans": [{"in": 0.9, "out": 2.1}, {"in": 2.9, "out": 4.1}],
        "reframe": {"face_x": 270}}
WORDS = [("hello", 1.0, 1.5), ("world.", 1.6, 2.0), ("again", 3.0, 3.4), ("friend.", 3.5, 3.9)]


class Stub:
    """Reports the dicts it is given through GetSettings, one per call, the last repeating."""

    def __init__(self, *dicts):
        self.dicts, self.calls = list(dicts), 0

    def GetSettings(self):
        got = self.dicts[min(self.calls, len(self.dicts) - 1)]
        self.calls += 1
        return got


class TestCompare(unittest.TestCase):
    def test_what_the_build_wrote_is_left_out(self):
        before = {"a": "0", "b": "x", "useCustomSettings": "0", "timelineFrameRate": "25",
                  "timelineResolutionWidth": "3840"}
        after = {"a": "1", "b": "x", "useCustomSettings": "1", "timelineFrameRate": 25.0,
                 "timelineResolutionWidth": "1920"}
        d = sg.compare(before, after, sg.WROTE_CLIP)
        self.assertEqual(d["changed"], [{"key": "a", "before": "0", "after": "1"}])
        self.assertEqual(d["compared"], 2)

    def test_a_different_frame_rate_is_left_out_only_where_the_build_wrote_it(self):
        before, after = {"timelineFrameRate": "25", "a": "0"}, {"timelineFrameRate": "24", "a": "0"}
        self.assertEqual(sg.compare(before, after, sg.WROTE_CLIP)["changed"], [])
        self.assertEqual([r["key"] for r in sg.compare(before, after, sg.WROTE_ASPECT)["changed"]],
                         ["timelineFrameRate"])

    def test_none_and_empty_differ_and_numbers_match_as_numbers(self):
        d = sg.compare({"g": None, "h": "", "n": "1", "m": "25"},
                       {"g": "", "h": "", "n": 1, "m": 25.0})
        self.assertEqual(d["changed"], [{"key": "g", "before": None, "after": ""}])

    def test_keys_only_one_side_holds_are_counted_not_judged(self):
        before = {"shared": "1", "p1": "x", "p2": "y", "useCustomSettings": "0"}
        after = {"shared": "1", "t1": "z", "useCustomSettings": "1"}
        d = sg.compare(before, after, sg.WROTE_SYNC)
        self.assertEqual((d["changed"], d["compared"], d["before_only"], d["after_only"]),
                         ([], 1, 2, ["t1"]))

    def test_output_size_follows_the_timeline_only_where_the_build_wrote_the_size(self):
        before = {"timelineOutputResolutionWidth": "3840", "timelineOutputResMatchTimelineRes": "1"}
        after = {"timelineOutputResolutionWidth": "1920", "timelineOutputResMatchTimelineRes": "1"}
        self.assertEqual(sg.compare(before, after, sg.WROTE_CLIP)["changed"], [])
        self.assertEqual([r["key"] for r in sg.compare(before, after, sg.WROTE_SYNC)["changed"]],
                         ["timelineOutputResolutionWidth"])
        off_b = dict(before, timelineOutputResMatchTimelineRes="0")
        off_a = dict(after, timelineOutputResMatchTimelineRes="0")
        self.assertEqual([r["key"] for r in sg.compare(off_b, off_a, sg.WROTE_CLIP)["changed"]],
                         ["timelineOutputResolutionWidth"])


class TestSnapshot(unittest.TestCase):
    def test_tiers_and_reasons(self):
        class A:
            def GetSettings(self): return {"a": 1}

        class B:
            def GetSetting(self, key=None): return {"b": 2} if key is None else None

        class C:
            def GetSetting(self, key): return None  # a key is required

        class E:
            def GetSettings(self): raise RuntimeError("boom")
            def GetSetting(self, key=None): return {"e": 5}

        class F:
            def GetSettings(self): return {}

        class G:
            def GetSettings(self): return None

        self.assertEqual(sg.snapshot(A()), ({"a": 1}, "GetSettings", ""))
        self.assertEqual(sg.snapshot(B()), ({"b": 2}, "GetSetting", ""))
        self.assertEqual(sg.snapshot(E()), ({"e": 5}, "GetSetting", ""))
        snap, via, why = sg.snapshot(C())
        self.assertIsNone(snap)
        self.assertIn("TypeError", why)
        self.assertIn("no GetSettings or GetSetting", sg.snapshot(object())[2])
        self.assertIn("no GetSettings or GetSetting", sg.snapshot(None)[2])
        self.assertIn("GetSettings() gave dict", sg.snapshot(F())[2])
        self.assertIn("gave nothing", sg.snapshot(G())[2])

    def test_the_answer_is_a_copy(self):
        got = {"a": 1}

        class A:
            def GetSettings(self): return got
        snap = sg.snapshot(A())[0]
        snap["a"] = 2
        self.assertEqual(got["a"], 1)


class TestWatch(unittest.TestCase):
    def test_stages_and_transient(self):
        s0 = {"a": "0", "b": "0", "c": "0"}
        s1 = {"a": "1", "b": "1", "c": "0"}
        s2 = {"a": "1", "b": "0", "c": "0"}
        w = sg.Watch(Stub(s0, s1, s2), sg.WROTE_CLIP)
        w.mark("new"), w.mark("first"), w.mark("second")
        r = w.report()
        self.assertEqual(r["state"], "drift")
        self.assertEqual(r["changed"], [{"key": "a", "before": "0", "after": "1", "at": "first"}])
        self.assertEqual(r["transient"], ["b"])
        self.assertEqual(r["compared"], 3)
        self.assertEqual(r["stages"], [{"stage": "new -> first", "changed": 2},
                                       {"stage": "first -> second", "changed": 1}])
        self.assertIs(w.report(), r)  # cached until the next mark
        self.assertTrue(sg.warnings(w.report())[0].startswith("settings drift: 1 of"))
        json.dumps(r)

    def test_no_change_is_clean_and_says_nothing(self):
        w = sg.Watch(Stub({"a": "0"}), sg.WROTE_CLIP)
        w.mark("x"), w.mark("y")
        r = w.report()
        self.assertEqual(r["state"], "clean")
        self.assertEqual((sg.warnings(r), sg.lines(r), sg.tail(r)), ([], [], ""))

    def test_one_reading_is_no_report(self):
        w = sg.Watch(Stub({"a": "0"}), sg.WROTE_CLIP)
        self.assertIsNone(w.report())
        w.mark("x")
        self.assertIsNone(w.report())

    def test_unreadable_is_unchecked_never_clean_and_never_raises(self):
        class Boom:
            def GetSettings(self): raise RuntimeError("boom")
            def GetSetting(self, key=None): raise TypeError("needs a key")

        class Prop:
            @property
            def GetSettings(self): raise ValueError("no")

        for obj in (Boom(), Prop(), object()):
            w = sg.Watch(obj, sg.WROTE_CLIP, project=Boom())
            w.mark("a"), w.mark("b")
            r = w.report()
            self.assertEqual(r["state"], "unchecked")
            self.assertEqual(r["changed"], [])
            self.assertEqual(w.tail(), "")
            self.assertEqual(len(sg.warnings(r)), 1)
            self.assertTrue(sg.warnings(r)[0].startswith("settings drift not checked:"))
        w = sg.Watch(Boom(), sg.WROTE_CLIP)
        w.mark("a"), w.mark("b")
        self.assertIn("RuntimeError", " ".join(w.report()["notes"]))

    def test_a_later_read_that_fails_is_a_note(self):
        class Once:
            calls = 0

            def GetSettings(self):
                Once.calls += 1
                if Once.calls > 1:
                    raise RuntimeError("late")
                return {"a": "0"}
        w = sg.Watch(Once(), sg.WROTE_CLIP)
        w.mark("a"), w.mark("b")
        r = w.report()
        self.assertEqual(r["state"], "unchecked")
        self.assertIn("at 'b'", " ".join(r["notes"]))

    def test_the_project_stands_in_for_an_unreadable_first_reading(self):
        tl = Stub(None, {"a": "1"})
        project = Stub({"a": "0"})
        w = sg.Watch(tl, sg.WROTE_CLIP, project)
        w.mark("new"), w.mark("custom")
        r = w.report()
        self.assertEqual(r["baseline"], "the project's settings")
        self.assertEqual(r["state"], "drift")
        self.assertIn("stand in", " ".join(r["notes"]))
        # With neither readable, later marks make no reads at all.
        tl = Stub(None)
        w = sg.Watch(tl, sg.WROTE_CLIP, Stub(None))
        w.mark("new")
        calls = tl.calls
        w.mark("custom")
        self.assertEqual(tl.calls, calls)
        self.assertEqual(w.report()["state"], "unchecked")

    def test_the_kill_switch_reads_nothing_and_says_nothing(self):
        for value in ("off", "OFF", " 0 ", "no", "false"):
            stub = Stub({"a": "0"})
            with mock.patch.dict(os.environ, {sg.ENV_OFF: value}):
                w = sg.Watch(stub, sg.WROTE_CLIP)
                w.mark("x"), w.mark("y")
            self.assertEqual(stub.calls, 0, value)
            r = w.report()
            self.assertIsNone(r, value)
            self.assertEqual((sg.warnings(r), sg.tail(r), sg.digest([("T", r)])), ([], "", []))
        stub = Stub({"a": "0"})
        with mock.patch.dict(os.environ, {sg.ENV_OFF: "maybe"}):  # not a recognised value: stays on
            w = sg.Watch(stub, sg.WROTE_CLIP)
            w.mark("x"), w.mark("y")
        self.assertEqual(w.report()["state"], "clean")

    def test_a_last_reading_that_fails_is_unchecked_unless_drift_was_seen(self):
        class Flaky:
            def __init__(self, *dicts):
                self.dicts, self.n = list(dicts), 0

            def GetSettings(self):
                self.n += 1
                if self.n > len(self.dicts):
                    raise RuntimeError("gone")
                return self.dicts[self.n - 1]
        w = sg.Watch(Flaky({"a": "0"}, {"a": "0"}), sg.WROTE_CLIP)
        for stage in ("one", "two", "three"):
            w.mark(stage)
        r = w.report()
        self.assertEqual(r["state"], "unchecked")
        self.assertIn("nothing could be read at 'three'", " ".join(r["notes"]))
        self.assertTrue(sg.warnings(r)[0].startswith("settings drift not checked"))
        w = sg.Watch(Flaky({"a": "0"}, {"a": "1"}), sg.WROTE_CLIP)
        for stage in ("one", "two", "three"):
            w.mark(stage)
        r = w.report()
        self.assertEqual(r["state"], "drift")
        self.assertIn("nothing could be read at 'three'", " ".join(r["notes"]))

    def test_hostile_objects_never_raise(self):
        class Odd(Exception):
            def __str__(self):
                raise RuntimeError("no text")

        class BadMap(dict):
            def items(self):
                raise RuntimeError("items")

        class A:
            def GetSettings(self): raise Odd()

        class B:
            def GetSettings(self): return BadMap(a=1)
        for obj in (A(), B()):
            w = sg.Watch(obj, sg.WROTE_CLIP)
            w.mark("x"), w.mark("y")
            self.assertEqual(w.report()["state"], "unchecked")
        self.assertEqual(sg._what(Odd()), "Odd")

    def test_only_getters_are_reached_for(self):
        class Spy:
            def __init__(self): self.seen = []
            def GetSettings(self): self.seen.append("GetSettings"); return {"a": "1"}

            def __getattr__(self, name):
                self.seen.append(name)
                raise AttributeError(name)
        tl, project = Spy(), Spy()
        w = sg.Watch(tl, sg.WROTE_CLIP, project)
        w.mark("a"), w.mark("b"), w.mark("c", Spy())
        w.report(), w.tail()
        self.assertTrue(set(tl.seen + project.seen) <= {"GetSettings", "GetSetting"}, tl.seen)
        src = Path(sg.__file__).read_text(encoding="utf-8")
        for token in ("SetSetting", "SetSettings", "Create", "Duplicate", "Delete"):
            self.assertNotIn(token, src)


class TestText(unittest.TestCase):
    def drifted(self):
        s0 = {f"k{i}": "0" for i in range(6)}
        s1 = dict({f"k{i}": "1" for i in range(5)}, k5="")
        w = sg.Watch(Stub(s0, s1), sg.WROTE_CLIP)
        w.mark("a"), w.mark("b")
        return w.report()

    def test_wording(self):
        r = self.drifted()
        (text,) = sg.warnings(r)
        self.assertIn("6 of 6 setting(s)", text)
        self.assertIn("k0 '0' -> '1'", text)
        self.assertIn("and 2 more", text)
        self.assertIn("reported, not corrected", text)
        rows = sg.lines(r)
        self.assertEqual(len(rows), 6)
        self.assertIn("k5 '0' -> '' (at b)", rows)
        self.assertEqual(sg.tail(r), "; settings drift on it: 6 setting(s)")
        self.assertIn("from '0' to '1'", sg.explain(r, "k0"))
        self.assertEqual(sg.explain(r, "nope"), "")
        self.assertEqual((sg.tail(None), sg.explain(None, "k0"), sg.warnings(None)), ("", "", []))

    def test_explain_answers_only_for_a_refusal_that_names_the_key(self):
        r = self.drifted()
        self.assertIn("from '0' to '1'", sg.explain(r, "k0", "REFUSED: k0 reads 'x'"))
        self.assertEqual(sg.explain(r, "k0", "REFUSED: item 1 has its own Scaling 3"), "")

    def test_two_read_methods_are_never_compared(self):
        class Both:
            def __init__(self):
                self.n = 0

            def GetSettings(self):
                self.n += 1
                if self.n > 1:
                    raise RuntimeError("gone")
                return {"a": "0"}

            def GetSetting(self, key=None):
                return {"a": "1"} if key is None else None
        w = sg.Watch(Both(), sg.WROTE_CLIP)
        w.mark("one"), w.mark("two")
        r = w.report()
        self.assertEqual(r["state"], "unchecked")
        self.assertIn("read with GetSetting() after GetSettings()", " ".join(r["notes"]))

    def test_keys_only_a_custom_timeline_holds_are_judged_between_custom_readings(self):
        w = sg.Watch(Stub({"a": "0"}, {"a": "0", "t": "x"}, {"a": "0", "t": "y"}), sg.WROTE_CLIP)
        for stage in ("one", "two", "three"):
            w.mark(stage)
        r = w.report()
        self.assertEqual(r["state"], "drift")
        self.assertEqual([(c["key"], c["before"], c["after"], c["at"]) for c in r["changed"]],
                         [("t", "x", "y", "three")])

    def test_clause_names_timelines_that_drifted_alike_together(self):
        rep = self.drifted()
        text = sg.clause(sg.digest([("A [auto]", rep), ("B [auto]", rep), ("C [auto]", None)]))
        self.assertEqual(text.count("setting(s) ("), 1)
        self.assertIn("A [auto], B [auto]: 6 setting(s) (", text)

    def test_digest_and_clause(self):
        clean = sg.Watch(Stub({"a": "0"}), sg.WROTE_CLIP)
        clean.mark("a"), clean.mark("b")
        blind = sg.Watch(object(), sg.WROTE_CLIP)
        blind.mark("a"), blind.mark("b")
        rows = sg.digest([("T [auto]", self.drifted()), ("U [auto]", clean.report()),
                          ("V [auto]", None), ("W [auto]", blind.report())])
        self.assertEqual([(r["timeline"], r["state"], r["count"]) for r in rows],
                         [("T [auto]", "drift", 6), ("W [auto]", "unchecked", 0)])
        text = sg.clause(rows)
        self.assertIn("; SETTINGS DRIFT (", text)
        self.assertIn("T [auto]: 6 setting(s) (k0 '0' -> '1'", text)
        self.assertIn(", ...)", text)
        self.assertIn("; SETTINGS NOT CHECKED on W [auto]", text)
        self.assertEqual((sg.clause([]), sg.clause(None)), ("", ""))
        for s in [text] + sg.warnings(self.drifted()) + sg.lines(self.drifted()):
            self.assertNotIn("—", s)
        json.dumps(rows)


class World(unittest.TestCase):
    def setUp(self):
        rf.CALLS.clear()
        self.source = rf.Clip("source.mp4", "clip-1", {"File Path": "/nowhere/source.mp4",
                                                       "Resolution": "1280x720", "FPS": 25,
                                                       "Type": "Video"})
        self.home = rf.Timeline("Home", "tl-home")
        self.project = rf.Project(timelines=[self.home], current=self.home,
                                  root=rf.Folder("Master", [self.source]))

    def flip(self, **knobs):
        self.project.settings.update(BEFORE)
        self.project.flip_resets = dict(AFTER)
        for k, v in knobs.items():
            setattr(self.project, k, v)

    def wide(self, fps=25):
        return cut.build_clip(self.project, self.project.pool, self.source, CLIP, fps, "T")

    def timeline(self, name):
        return cut.timeline_names(self.project)[name]

    def tall(self):
        return cut.build_aspect(self.project, self.timeline("T_hi [auto]"), CLIP, (1280, 720),
                                "T", "1x1")

    def sets(self):
        return [(c[2][0], c[2][1]) for c in rf.CALLS if c[1] == "SetSetting"]


class TestBuilds(World):
    def test_a_clean_build_is_clean_and_writes_nothing_extra(self):
        r = self.wide()
        self.assertTrue(r["ok"], r["reason"])
        d = r["settings_drift"]
        self.assertEqual((d["state"], d["baseline"]), ("clean", sg.OWN))
        self.assertEqual([s["stage"] for s in d["stages"]],
                         ["new timeline -> first custom write",
                          "first custom write -> second custom write"])
        self.assertEqual(r["warnings"], [])
        t = self.tall()
        self.assertTrue(t["ok"], t["reason"])
        self.assertEqual((t["settings_drift"]["state"], t["settings_drift"]["baseline"]),
                         ("clean", "the 16:9"))
        self.assertEqual([s["stage"] for s in t["settings_drift"]["stages"]],
                         ["16:9 -> copy", "copy -> custom write"])
        self.assertFalse([w for w in t["warnings"] if w.startswith("settings drift")])
        self.assertEqual(self.sets(), [
            ("useCustomSettings", "1"), ("timelineFrameRate", "25"), ("useCustomSettings", "1"),
            ("timelineResolutionWidth", "1920"), ("timelineResolutionHeight", "1080"),
            ("useCustomSettings", "1"), ("timelineResolutionWidth", "1080"),
            ("timelineResolutionHeight", "1080")])

    def test_a_flip_is_reported_and_nothing_is_set_back(self):
        self.flip()
        r = self.wide()
        self.assertTrue(r["ok"], r["reason"])
        d = r["settings_drift"]
        self.assertEqual(d["state"], "drift")
        self.assertEqual([c["key"] for c in d["changed"]], sorted(AFTER))
        self.assertEqual({c["at"] for c in d["changed"]}, {"first custom write"})
        self.assertEqual(d["compared"], 4)  # colorScienceMode and the three flipped keys
        gamma = next(c for c in d["changed"] if c["key"] == "colorSpaceOutputGamma")
        self.assertEqual((gamma["before"], gamma["after"]), ("Gamma 2.4", ""))
        self.assertEqual(len(r["warnings"]), 1)
        self.assertTrue(r["warnings"][0].startswith("settings drift: 3 of 4 setting(s)"))
        self.assertEqual(self.timeline("T_hi [auto]").settings["isAutoColorManage"], "1")
        self.assertEqual(self.project.settings["isAutoColorManage"], "0")
        self.assertEqual([c for c in rf.CALLS if c[1] == "SetSettings"], [])
        written = self.sets()
        self.setUp()  # the same build without the flip makes the same writes
        self.wide()
        self.assertEqual(self.sets(), written)

    def test_a_copy_is_compared_with_its_16x9(self):
        self.flip()  # the fake's copy starts from the project, so it differs until its own write
        self.wide()
        d = self.tall()["settings_drift"]
        self.assertEqual(d["state"], "clean")
        self.assertEqual(d["transient"], sorted(AFTER))
        self.setUp()
        self.flip(dup_copies_settings=True)
        self.wide()
        d = self.tall()["settings_drift"]
        self.assertEqual((d["state"], d["transient"]), ("clean", []))
        self.assertEqual(d["stages"][0], {"stage": "16:9 -> copy", "changed": 0})

    def test_a_second_write_that_resets_again_is_told_apart(self):
        self.flip(flip_each_time=True)
        real = rf.Timeline.SetSetting

        def restoring(tl, key, value):
            done = real(tl, key, value)
            if key == "timelineFrameRate":  # something puts one key back after the first write
                tl.settings["isAutoColorManage"] = "0"
            return done
        with mock.patch.object(rf.Timeline, "SetSetting", restoring):
            r = self.wide()
        at = {c["key"]: c["at"] for c in r["settings_drift"]["changed"]}
        self.assertEqual(at["isAutoColorManage"], "second custom write")
        self.assertEqual(at["rcmPresetMode"], "first custom write")

    def test_a_failed_version_keeps_its_report_and_its_reason(self):
        self.wide()
        self.timeline("T_hi [auto]").settings["videoMonitorFormat"] = "HD 1080p 24"
        self.project.settings["videoMonitorFormat"] = "HD 1080p 25"
        real = rf._copy_item

        def stubborn(it):
            new = real(it)
            new.ignored = {"Pan"}
            return new
        with mock.patch.object(rf, "_copy_item", stubborn):
            t = self.tall()
        self.assertEqual(t["left_behind"], "T_hi_1x1 [auto]")
        self.assertEqual(t["settings_drift"]["state"], "drift")
        self.assertEqual([c["key"] for c in t["settings_drift"]["changed"]], ["videoMonitorFormat"])
        self.assertIn("LEFT BEHIND: 'T_hi_1x1 [auto]' exists", t["reason"])
        self.assertTrue(t["reason"].endswith("; settings drift on it: 1 setting(s)"))

    def test_settings_that_cannot_be_read_do_not_fail_the_build(self):
        with mock.patch.object(rf.Timeline, "GetSettings", side_effect=RuntimeError("boom")):
            r = self.wide()
            t = self.tall()
        self.assertTrue(r["ok"], r["reason"])
        self.assertTrue(t["ok"], t["reason"])
        for x in (r, t):
            self.assertEqual(x["settings_drift"]["state"], "unchecked")
            self.assertTrue([w for w in x["warnings"] if w.startswith("settings drift not checked")])
        self.assertIn("RuntimeError", " ".join(r["settings_drift"]["notes"]))

    def test_a_failed_16x9_names_the_drift_in_its_error(self):
        self.flip()
        real = rf.Timeline.SetSetting

        def stubborn(tl, key, value):
            return True if key == "timelineFrameRate" else real(tl, key, value)
        with mock.patch.object(rf.Timeline, "SetSetting", stubborn):
            with self.assertRaises(api.WriteNotApplied) as cm:
                self.wide(fps=24)
        msg = str(cm.exception)
        self.assertIn("LEFT BEHIND: 'T_hi [auto]' exists, empty", msg)
        self.assertTrue(msg.endswith("; settings drift on it: 3 setting(s)"), msg)
        self.setUp()  # without the flip the message is what it was
        with mock.patch.object(rf.Timeline, "SetSetting", stubborn):
            with self.assertRaises(api.WriteNotApplied) as cm:
                self.wide(fps=24)
        self.assertNotIn("settings drift", str(cm.exception))


class TestNewTimeline(World):
    def test_report_and_unchanged_writes(self):
        self.flip()
        rep = {}
        sb.new_timeline(self.project, self.project.pool, "S [auto]", "25", report=rep)
        d = rep["settings_drift"]
        self.assertEqual((d["state"], len(d["changed"])), ("drift", 3))
        self.assertEqual(self.sets(), [("useCustomSettings", "1"), ("timelineFrameRate", "25")])

    def test_without_a_report_it_works_as_before(self):
        tl = sb.new_timeline(self.project, self.project.pool, "S [auto]", "25")
        self.assertEqual(tl.GetName(), "S [auto]")

    def test_a_failed_rate_read_back_names_the_drift(self):
        self.flip()
        real = rf.Timeline.SetSetting

        def stubborn(tl, key, value):
            return True if key == "timelineFrameRate" else real(tl, key, value)
        with mock.patch.object(rf.Timeline, "SetSetting", stubborn):
            with self.assertRaises(api.WriteNotApplied) as cm:
                sb.new_timeline(self.project, self.project.pool, "S [auto]", "24")
        self.assertTrue(str(cm.exception).endswith("; settings drift on it: 3 setting(s)"))


class ThinItem:
    def __init__(self, start, dur, src):
        self.start, self.dur, self.src, self.props = start, dur, src, {}

    def GetStart(self): return self.start
    def GetDuration(self): return self.dur
    def GetSourceStartFrame(self): return self.src
    def SetProperty(self, k, v): self.props[k] = v; return True
    def GetProperty(self, k): return self.props.get(k)


class ThinTimeline:
    """A timeline whose GetSetting needs a key and which has no GetSettings."""

    def __init__(self, name, start=90000):
        self.name, self.start, self.items, self.settings = name, start, [], {}

    def GetName(self): return self.name
    def GetStartFrame(self): return self.start
    def GetEndFrame(self): return self.start + sum(i.dur for i in self.items)
    def SetSetting(self, k, v): self.settings[k] = v; return True
    def GetSetting(self, k): return self.settings.get(k)
    def GetItemListInTrack(self, kind, idx): return list(self.items)

    def DuplicateTimeline(self, name):
        t = ThinTimeline(name, self.start)
        t.items = [ThinItem(i.start, i.dur, i.src) for i in self.items]
        return t


class ThinPool:
    def __init__(self): self.current = None

    def CreateEmptyTimeline(self, name):
        self.current = ThinTimeline(name)
        return self.current

    def AppendToTimeline(self, infos):
        for info in infos:
            self.current.items.append(ThinItem(info["recordFrame"],
                                               info["endFrame"] - info["startFrame"],
                                               info["startFrame"]))
        return True


class ThinProject:
    def SetCurrentTimeline(self, tl): return True


class TestThinFakes(unittest.TestCase):
    def test_a_timeline_that_reports_no_settings_still_builds(self):
        r = cut.build_clip(ThinProject(), ThinPool(), object(), CLIP, 25, "T")
        self.assertTrue(r["ok"], r["reason"])
        self.assertEqual(r["settings_drift"]["state"], "unchecked")
        self.assertTrue(r["warnings"][0].startswith("settings drift not checked:"))
        wide = ThinTimeline("T_hi [auto]")
        wide.items = [ThinItem(90000, 44, 25)]
        t = cut.build_tall(ThinProject(), wide, CLIP, 1280, "T")
        self.assertTrue(t["ok"], t["reason"])
        self.assertEqual(t["settings_drift"]["state"], "unchecked")


def span_entry(x, y=360.0, scale=1.5):
    return {"x": x, "y": y, "scale": scale, "src": [1280, 720], "area": [0, 0, 1280, 720],
            "status": "pass"}


class TestCutWorkflow(World):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = os.path.realpath(tmp.name)
        src = os.path.join(d, "source.mp4")
        with open(src, "wb") as f:
            f.write(b"synthetic media")
        words = os.path.join(d, "w.json")
        with open(words, "w") as f:
            json.dump({"segments": [{"words": [{"word": w, "start": a, "end": b}
                                               for w, a, b in WORDS]}]}, f)
        approved = os.path.join(d, "a.md")
        with open(approved, "w") as f:
            f.write("Hello world. Again friend.")
        self.source.props["File Path"] = src
        m = cutlist.build_manifest({"hi": [(0.9, 2.1), (2.9, 4.1)]}, src, 25, words, approved)
        for s, e in zip(m["clips"][0]["spans"], (span_entry(360), span_entry(400))):
            s["reframe"] = {"1x1": e}
        self.path = os.path.join(d, "m.json")
        with open(self.path, "w") as f:
            json.dump(m, f)
        self.resolve = rf.Resolve(self.project, page="edit")

    def run_cut(self, **kw):
        return workflows.cut(self.resolve, "RP Automation Sandbox", self.path, prefix="T",
                             audio=False, aspects=["1x1"], **kw)

    def test_clean(self):
        r = self.run_cut()
        self.assertEqual(r["exit_status"], 0, [x["reason"] for x in r["results"]])
        self.assertEqual(r["settings_drift_rows"], [])
        self.assertEqual([x["settings_drift"]["state"] for x in r["results"]], ["clean", "clean"])
        self.assertEqual(sg.clause(r["settings_drift_rows"]), "")

    def test_a_dry_run_reads_nothing(self):
        with mock.patch.object(rf.Timeline, "GetSettings") as g:
            r = self.run_cut(dry_run=True)
        g.assert_not_called()
        self.assertEqual((r["settings_drift_rows"], r["results"]), ([], []))
        self.assertEqual(rf.mutating_calls(), [])

    def test_a_flip_is_in_the_digest_and_the_exit_status_is_unchanged(self):
        self.flip()
        r = self.run_cut()
        self.assertEqual(r["exit_status"], 0, [x["reason"] for x in r["results"]])
        self.assertEqual([(x["timeline"], x["state"], x["count"]) for x in r["settings_drift_rows"]],
                         [("T_hi [auto]", "drift", 3)])
        self.assertIn("; SETTINGS DRIFT (", sg.clause(r["settings_drift_rows"]))
        self.assertEqual(r["results"][0]["settings_drift"]["state"], "drift")
        self.assertEqual(r["results"][1]["settings_drift"]["state"], "clean")
        self.assertEqual(r["left_behind"], [])

    def test_the_mcp_summary_and_journal_name_a_flip(self):
        from rpresolve.mcp import schema, server
        from rpresolve.mcp.registry import ToolContext
        tool = server.build_registry().get("cut")
        outer = self

        class Session:
            def get(s):
                return outer.resolve
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = os.path.realpath(tmp.name)
        env = mock.patch.dict(os.environ, {"RPRESOLVE_MCP_JOURNAL": os.path.join(d, "writes.jsonl"),
                                           "RPRESOLVE_LOCK": os.path.join(d, "lock")})
        env.start()
        self.addCleanup(env.stop)

        def call(args):
            args = schema.with_defaults(tool.input_schema, args)
            self.assertEqual(schema.validate(tool.input_schema, args), [])
            return tool.handler(args, ToolContext(session=Session()))
        self.flip()
        base = {"project": "RP Automation Sandbox", "manifest": self.path, "prefix": "T",
                "audio": False, "aspects": ["1x1"]}
        dry = call(base)
        self.assertNotIn("SETTINGS", dry["summary"])
        r = call({**base, "dry_run": False, "plan_sha": dry["plan_sha"]})
        self.assertIn("; SETTINGS DRIFT (", r["summary"])
        self.assertIn("T_hi [auto]: 3 setting(s)", r["summary"])
        with open(os.path.join(d, "writes.jsonl"), encoding="utf-8") as f:
            events = [json.loads(line) for line in f]
        self.assertEqual(events[-1]["event"], "finished")
        self.assertIn("isAutoColorManage", json.dumps(events[-1]))

    def test_a_scaling_flip_is_named_beside_the_existing_refusal(self):
        self.project.settings[cut.INPUT_SCALING] = "scaleToCrop"
        self.project.flip_resets = {cut.INPUT_SCALING: "scaleToFit"}
        r = self.run_cut()
        tall = r["results"][1]
        self.assertTrue(tall["refused"])
        self.assertIn("the plan was made for 'scaleToCrop'", tall["reason"])
        self.assertIn("settings drift: Resolve changed it from 'scaleToCrop' to 'scaleToFit'",
                      tall["reason"])
        self.assertEqual(r["exit_status"], 1)
        self.assertEqual([x["timeline"] for x in r["settings_drift_rows"]], ["T_hi [auto]"])


if __name__ == "__main__":
    unittest.main()
