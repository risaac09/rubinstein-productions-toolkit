"""
renderguard: the Deliver page put back after SetRenderSettings. Fake Resolve
objects only; nothing here touches Resolve or starts a render.

Run: /usr/bin/python3 -m unittest -v production.tests.test_renderguard
"""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import resolve_fakes as rf  # noqa: E402
from rpresolve.renderguard import RenderGuard  # noqa: E402


class MarkedTimeline(rf.Timeline):
    """A timeline that keeps a mark In/Out, as GetMarkInOut/SetMarkInOut do."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.marks = {}

    def GetMarkInOut(self):
        return {k: {"in": a, "out": z} for k, (a, z) in self.marks.items()}

    def SetMarkInOut(self, a, z, kind="all"):
        self.marks.update({k: (a, z) for k in (("video", "audio") if kind == "all" else (kind,))})
        return True

    def ClearMarkInOut(self, kind="all"):
        self.marks.clear()
        return True


class ReadableSettings(rf.Project):
    """A Resolve that does have a GetRenderSettings (21.1.1.10 has none)."""

    def GetRenderSettings(self):
        return dict(self.render_settings)


class TestRenderGuard(unittest.TestCase):
    def setUp(self):
        self.tl = MarkedTimeline("A", "tl-a")
        self.tl.marks = {"video": (10, 90), "audio": (10, 90)}
        self.project = rf.Project(timelines=[self.tl], current=self.tl)

    def test_restores_format_mode_and_marks(self):
        p = self.project
        with RenderGuard(p, self.tl) as g:
            p.SetCurrentRenderFormatAndCodec("mp4", "H264")
            p.SetCurrentRenderMode(0)
            self.tl.ClearMarkInOut()
        self.assertEqual(p.fmt, {"format": "mov", "codec": "ProRes422HQ"})
        self.assertEqual(p.render_mode, 1)
        self.assertEqual(self.tl.marks, {"video": (10, 90), "audio": (10, 90)})
        self.assertEqual(g.report()["mismatched"], [])
        self.assertEqual(g.warnings(), [])
        self.assertEqual(sorted(g.restored), ["format/codec", "mark in/out", "render mode"])

    def test_restores_when_the_block_raises(self):
        p = self.project
        with self.assertRaises(RuntimeError):
            with RenderGuard(p, self.tl):
                p.SetCurrentRenderMode(0)
                raise RuntimeError("boom")
        self.assertEqual(p.render_mode, 1)

    def test_readback_mismatch_is_reported(self):
        p = self.project
        with RenderGuard(p, self.tl) as g:
            p.SetCurrentRenderMode(0)
            p.stuck_mode = True  # now SetCurrentRenderMode says yes and changes nothing
        self.assertEqual(p.render_mode, 0)
        self.assertEqual(g.report()["mismatched"], [{"what": "render mode", "wanted": 1, "got": 0}])
        self.assertIn("render mode back to 1", g.warnings()[0])

    def test_unreadable_keys_are_named_for_a_hand_reset(self):
        with RenderGuard(self.project, self.tl) as g:
            g.touch(["AudioSampleRate", "SelectAllFrames", "TargetDir"])
        self.assertEqual(g.unrestorable, ["AudioSampleRate", "SelectAllFrames", "TargetDir"])
        text = g.hand_reset()
        self.assertIn("AudioSampleRate", text)
        self.assertIn("SelectAllFrames", text)
        self.assertNotIn("TargetDir", text)

    def test_readable_settings_are_put_back_and_read_again(self):
        p = ReadableSettings(timelines=[self.tl], current=self.tl)
        p.render_settings = {"AudioSampleRate": 44100}
        with RenderGuard(p, self.tl) as g:
            g.touch(["AudioSampleRate"])
            p.SetRenderSettings({"AudioSampleRate": 48000})
        self.assertEqual(p.render_settings["AudioSampleRate"], 44100)
        self.assertIn("AudioSampleRate", g.restored)
        self.assertEqual(g.unrestorable, [])
        self.assertEqual(g.hand_reset(), "")

    def test_readable_setting_that_will_not_restore_is_a_mismatch(self):
        p = ReadableSettings(timelines=[self.tl], current=self.tl)
        p.render_settings = {"SelectAllFrames": False}
        with RenderGuard(p, self.tl) as g:
            g.touch(["SelectAllFrames"])
            p.SetRenderSettings({"SelectAllFrames": True})
            p.refuse = {"SelectAllFrames"}  # the reset is refused, as the atlas saw
        self.assertEqual(g.report()["mismatched"],
                         [{"what": "SelectAllFrames", "wanted": False, "got": True}])

    def test_never_raises_from_exit(self):
        class Broken(rf.Project):
            def GetCurrentRenderMode(self):
                raise RuntimeError("gone")
        with RenderGuard(Broken(), None) as g:
            pass
        self.assertTrue(g.problems)


if __name__ == "__main__":
    unittest.main()
