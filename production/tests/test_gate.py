"""
Tests for rpresolve.cmd_gate: the hard-cut count from a manifest, the flash
counter, the fade-out rule, and one gate run on a synthetic draft folder whose
draft is too loud (fixed copy written, send copy refreshed, original untouched,
a second run idempotent). The run needs ffmpeg.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import filecmp
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rpresolve import cmd_gate as gate, delivercheck as dc, qc  # noqa: E402
from test_delivercheck import ENCODERS, make, tiny  # noqa: E402


def spans(n):
    return [{"in": i, "out": i + 1} for i in range(n)]


class TestHardCuts(unittest.TestCase):
    def test_plain_spans_cut_between_each(self):
        self.assertEqual(gate.hard_cuts({"clips": [{"spans": spans(5)}]}), 4)

    def test_dissolves_and_drawn_transitions_are_not_hard_cuts(self):
        sp = spans(5)
        sp[2]["transition"] = {"type": "dissolve", "frames": 12}
        sp[0]["transition"] = {"type": "dissolve", "frames": 12}  # span 0 has no joint before it
        m = {"clips": [{"spans": sp}], "effects": [{"span_index": 3, "effect": "luma-fade"},
                                                   {"span_index": 4, "effect": "speed-ramp"}]}
        self.assertEqual(gate.hard_cuts(m), 2)


class TestFlashes(unittest.TestCase):
    def test_steady_and_single_step_are_no_flash(self):
        self.assertEqual(gate.flashes([100.0] * 48, 24)[0], 0)
        self.assertEqual(gate.flashes([20.0] * 24 + [200.0] * 24, 24)[0], 0)

    def test_strobe_counts_flashes_in_one_second(self):
        y = ([20.0] * 2 + [200.0] * 2) * 12  # six flashes a second at 24 fps
        n, at = gate.flashes(y, 24)
        self.assertGreater(n, gate.FLASH_MAX_PER_S)
        self.assertIsNotNone(at)

    def test_a_slow_ramp_is_one_transition(self):
        y = [20.0 + 30 * i for i in range(7)] + [200.0] * 20
        self.assertEqual(gate.flashes(y, 24)[0], 0)


class TestTailFade(unittest.TestCase):
    def row(self, found):
        return {"check": "blackdetect", "status": qc.FAIL, "found": found, "expected": "x"}

    def test_black_running_out_the_end_is_a_warn(self):
        r = gate._tail_fade(self.row("8.90s for 1.10s"), 10.0, qc.THRESHOLDS)
        self.assertEqual(r["status"], qc.WARN)

    def test_black_in_the_middle_stays_a_fail(self):
        r = gate._tail_fade(self.row("4.00s for 1.10s"), 10.0, qc.THRESHOLDS)
        self.assertEqual(r["status"], qc.FAIL)


@unittest.skipUnless("libx264" in ENCODERS, "ffmpeg or ffprobe (with libx264) missing")
class TestGateRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.draft = make(os.path.join(self.tmp, "P.mp4"), size="64x36", rate="24",
                          lufs=-8.0, seconds=3)
        send = os.path.join(self.tmp, "send")
        os.makedirs(send)
        self.send_a = os.path.join(send, "A.mp4")
        shutil.copyfile(self.draft, self.send_a)
        mpath = os.path.join(self.tmp, "manifest.json")
        with open(mpath, "w") as f:
            json.dump({"clips": [{"spans": spans(3)}]}, f)
        self.d = {"plan": "P", "file": self.draft, "size": [64, 36], "fps": 24, "frames": 72,
                  "manifest": mpath}
        self.dest = tiny("youtube_16x9")

    def statuses(self, res):
        return {r["check"]: r["status"] for r in res["checks"]}

    def test_a_loud_draft_is_fixed_and_its_send_copy_refreshed(self):
        before = dc.video_hash(self.draft), os.path.getsize(self.draft)
        res = gate.gate_one(self.d, self.dest, None, os.path.dirname(self.send_a))
        s = self.statuses(res)
        fixed = dc.fixed_path(self.draft)
        self.assertEqual(res["checked"], fixed)
        self.assertEqual(s["loudness_fix"], qc.PASS)
        self.assertEqual(s["loudness"], qc.PASS)
        self.assertEqual(s["duration"], qc.PASS)
        self.assertEqual(s["send_copy"], qc.PASS)
        self.assertTrue(filecmp.cmp(self.send_a, fixed, shallow=False))
        self.assertEqual((dc.video_hash(self.draft), os.path.getsize(self.draft)), before)
        again = gate.gate_one(self.d, self.dest, None, os.path.dirname(self.send_a))
        self.assertEqual(self.statuses(again)["send_copy"], qc.PASS)

    def test_a_draft_with_no_audio_fails_a_loudness_gate_and_is_not_fixed(self):
        import subprocess
        mute = os.path.join(self.tmp, "M.mp4")
        subprocess.run([dc.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=64x36:rate=24:duration=3", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", mute], stdin=subprocess.DEVNULL, check=True)
        res = gate.gate_one(dict(self.d, file=mute, plan="M"), self.dest, None, None)
        self.assertEqual(res["checked"], mute)
        self.assertFalse(os.path.exists(dc.fixed_path(mute)))
        self.assertEqual(self.statuses(res)["loudness"], qc.FAIL)
        self.assertEqual(res["status"], "fail")
        quiet = gate.gate_one(dict(self.d, file=mute, plan="M"), None, None, None)
        self.assertNotEqual(self.statuses(quiet).get("loudness"), qc.FAIL)  # no target, no fail

    def test_without_a_destination_nothing_is_written(self):
        res = gate.gate_one(self.d, None, None, os.path.dirname(self.send_a))
        self.assertEqual(res["checked"], self.draft)
        self.assertFalse(os.path.exists(dc.fixed_path(self.draft)))
        self.assertNotIn("loudness_fix", self.statuses(res))


if __name__ == "__main__":
    unittest.main()
