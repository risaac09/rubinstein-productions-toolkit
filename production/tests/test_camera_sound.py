"""
camera_sound against the shared fakes: each clip's own sound lands on A1 under its picture
(same clip, source frames, record frame), the picture is left alone, a spilled second stream
is reported and never deleted, and the refusals (not [auto], A1 not empty, no plan_sha).

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import resolve_fakes as rf  # noqa: E402
from rpresolve import workflows  # noqa: E402
from test_mcp_writes2 import Base, call, run_twice  # noqa: E402

TL = "All [auto]"


class TestCameraSound(Base):
    def setUp(self):
        super().setUp()
        fast = rf.Clip("F.MP4", "clip-f", {"File Path": "/w/F.MP4", "FPS": "50"})
        self.auto = rf.Timeline(TL, "tl-auto", 25.0, 86400, 86560, {
            "video": [[rf.Item("P1.MOV", 86400, 86450, self.clip, source=(10, 60),
                               props={"RotationAngle": 90}),
                       rf.Item("Cross Dissolve", 86450, 86452, None, nodes=None),
                       rf.Item("F.MP4", 86452, 86502, fast, source=(0, 100)),
                       rf.Item("P2.MOV", 86502, 86560, self.other, source=(5, 63))]],
            "audio": [[]]})
        self.project.add(self.auto)
        rf.CALLS.clear()

    def test_each_clip_gets_its_own_sound_under_its_picture(self):
        picture = [(i.start, i.end, i.props) for i in self.auto.tracks["video"][0]]
        dry = call("camera_sound", {"timeline": TL}, self.resolve)
        self.assertEqual([t["index"] for t in dry["targets"]], [1, 3, 4])
        self.assertEqual([s["position"] for s in dry["skipped"]], [2])
        self.assertEqual(self.auto.tracks["audio"][0], [])  # the dry run placed nothing
        real = call("camera_sound", {"timeline": TL, "dry_run": False, "plan_sha": dry["plan_sha"]},
                    self.resolve)
        a1 = self.auto.tracks["audio"][0]
        self.assertEqual([(i.clip, i.start, i.end, i.source) for i in a1],
                         [(self.clip, 86400, 86450, (10, 60)), (a1[1].clip, 86452, 86502, (0, 100)),
                          (self.other, 86502, 86560, (5, 63))])
        self.assertEqual((real["exit_status"], real["problems"], len(real["placed"])), (0, [], 3))
        self.assertEqual([(i.start, i.end, i.props) for i in self.auto.tracks["video"][0]], picture)
        self.assertEqual(self.ui(), ("cut", self.here))  # the current timeline was put back
        self.assertEqual(self.journal(), ["started", "finished"])

    def test_a_spilled_stream_is_reported_and_nothing_is_deleted(self):
        self.other.audio_streams = 2
        dry, real = run_twice("camera_sound", {"timeline": TL}, self.resolve)
        self.assertEqual((self.auto.GetTrackCount("audio"), len(self.auto.tracks["audio"][0])), (2, 3))
        self.assertEqual(real["exit_status"], 1)
        self.assertIn("new track(s) A2 to A2", real["problems"][0])
        self.assertFalse([c for c in rf.CALLS if c[1] in ("DeleteClips", "DeleteTrack")])

    def test_a_source_with_no_sound_is_a_problem(self):
        self.other.props["Type"] = "Video"
        dry, real = run_twice("camera_sound", {"timeline": TL}, self.resolve)
        self.assertEqual(real["exit_status"], 1)
        self.assertIn("no sound placed", real["problems"][0])

    def test_refusals(self):
        with self.assertRaisesRegex(workflows.Refused, "not an \\[auto\\]"):
            call("camera_sound", {"timeline": "Edit"}, self.resolve)
        with self.assertRaisesRegex(workflows.Refused, "plan_sha"):
            call("camera_sound", {"timeline": TL, "dry_run": False}, self.resolve)
        stale = call("camera_sound", {"timeline": TL}, self.resolve)["plan_sha"]
        self.auto.tracks["video"][0][0].end += 1
        with self.assertRaisesRegex(workflows.Refused, "plan changed"):
            call("camera_sound", {"timeline": TL, "dry_run": False, "plan_sha": stale}, self.resolve)
        self.auto.tracks["audio"][0].append(rf.Item("Music", 86400, 86500, self.clip, nodes=None))
        with self.assertRaisesRegex(workflows.Refused, "already holds 1 item"):
            call("camera_sound", {"timeline": TL}, self.resolve)


if __name__ == "__main__":
    unittest.main()
