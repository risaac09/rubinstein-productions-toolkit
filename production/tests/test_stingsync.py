"""
Tests for rpresolve.stingsync and the `sting-sync` command. The WAVs are
synthesized here (clicks at known samples, a loud tonal swell ahead of one
click); the lockup and cues are small neutral files written by the test.

Run: /usr/bin/python3 -m unittest production.tests.test_stingsync -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import stingsync as ss  # noqa: E402

WORKFLOW = Path(__file__).resolve().parent.parent / "resolve_workflow.py"
RATE = 48000
FRAME_MS = 1001 * 1000 / 24000
KEYS = [{"id": "cut_start", "frame": 20, "ms": 834.167}, {"id": "lock", "frame": 78, "ms": 3253.25}]
LOCKUP = {"fps": "24000/1001", "keyframes": KEYS}


def wav(path, clicks_ms, swell_ms=None):
    """6 s mono 16-bit: a unit click at each time; optionally a loud 8 kHz tone
    from swell_ms for 40 ms (a high first difference that is not the hit)."""
    x = np.zeros(RATE * 6)
    for ms in clicks_ms:
        x[int(round(ms * RATE / 1000))] = 0.5
    if swell_ms is not None:
        a = int(swell_ms * RATE / 1000)
        t = np.arange(int(0.04 * RATE))
        x[a:a + len(t)] += 0.3 * np.sin(2 * np.pi * 8000 * t / RATE)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((x * 32767).astype("<i2").tobytes())


def cue(cid, frame, onset, **extra):
    return {"cue_id": cid, "kind": "hit", "frame": frame, "onset_ms": onset,
            "measured_from": "m.wav", **extra}


class StingSync(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)

    def p(self, name):
        return os.path.join(self.d.name, name)

    def rows(self, clicks, cues, stem=None, mix=None, **kw):
        wav(self.p("m.wav"), clicks, mix)
        if stem is not None:
            wav(self.p("s.wav"), stem)
        return ss.check(LOCKUP, {"cues": cues}, master=self.p("m.wav"),
                        stem=self.p("s.wav") if stem is not None else None, **kw)

    def test_on_time_passes(self):
        r = self.rows([834.167, 3253.25], [cue("a", 20, 834.167), cue("b", 78, 3253.25)])
        self.assertEqual([x["status"] for x in r], ["PASS", "PASS"])
        self.assertAlmostEqual(r[0]["offset_ms"], 0, delta=0.1)

    def test_two_frames_late_fails_on_the_lag_side(self):
        on = 3253.25 + 2 * FRAME_MS
        r = self.rows([on], [cue("b", 80, on)])
        self.assertEqual(r[0]["status"], "FAIL")
        self.assertAlmostEqual(r[0]["offset_ms"], -83.4, delta=0.1)
        self.assertIn("lags", r[0]["reasons"][0])
        self.assertEqual(r[0]["keyframe"], "lock")

    def test_one_frame_early_fails_unless_intended(self):
        on = 3253.25 - FRAME_MS
        r = self.rows([on], [cue("b", 77, on)])
        self.assertEqual(r[0]["status"], "FAIL")
        self.assertIn("leads", r[0]["reasons"][0])
        r = self.rows([on], [cue("b", 77, on, intended_offset_ms=41.7)])
        self.assertEqual(r[0]["status"], "PASS")

    def test_transient_stem_finds_a_hit_the_mix_misses(self):
        click = 3253.25
        cues = [cue("b", 78, click)]
        # A tone starts 30 ms before the click: the mix's first difference
        # reaches the threshold on the tone, the stem holds the click alone.
        stem = self.rows([click], cues, stem=[click], mix=click - 30)
        self.assertEqual(stem[0]["status"], "PASS")
        mix = self.rows([click], cues, mix=click - 30)
        self.assertEqual(mix[0]["status"], "FAIL")
        self.assertIn("declared onset", mix[0]["reasons"][0])

    def test_a_cue_without_measured_from_is_refused(self):
        c = cue("b", 78, 3253.25)
        del c["measured_from"]
        with self.assertRaisesRegex(ss.StingError, "measured_from"):
            self.rows([3253.25], [c])

    def test_a_declared_onset_5_ms_off_fails(self):
        r = self.rows([3253.25], [cue("b", 78, 3258.25)])
        self.assertEqual(r[0]["status"], "FAIL")
        self.assertIn("declared onset", r[0]["reasons"][0])

    def test_no_keyframe_at_the_frame_fails(self):
        r = self.rows([3253.25], [cue("b", 50, 3253.25)])
        self.assertEqual(r[0]["status"], "FAIL")
        self.assertIn("no lockup keyframe", r[0]["reasons"][0])

    def test_malformed_fields_are_refused_not_crashed(self):
        for bad in ({"frame": None}, {"onset_ms": "3253"}):
            with self.assertRaisesRegex(ss.StingError, "must be numbers"):
                self.rows([3253.25], [{**cue("b", 78, 3253.25), **bad}])
        with self.assertRaisesRegex(ss.StingError, "numeric frame"):
            ss.check({"keyframes": [{"id": "x"}]}, {"cues": [cue("b", 78, 1)]}, master=self.p("m.wav"))

    def test_both_lockup_shapes_and_fail_first(self):
        wav(self.p("m.wav"), [834.167, 3253.25 + 100])
        cues = {"cut": "bumper", "cues": [cue("a", 78, 3253.25 + 100), cue("b", 20, 834.167)]}
        two = {"cuts": {"long": {"keyframes": []}, "bumper": LOCKUP}}
        r = ss.check(two, cues, master=self.p("m.wav"))
        self.assertEqual([x["cue_id"] for x in r], ["a", "b"])
        r = ss.check(two, cues, master=self.p("m.wav"), cut="bumper")
        self.assertEqual(r[0]["status"], "FAIL")
        with self.assertRaises(ss.StingError):
            ss.check(two, cues, master=self.p("m.wav"), cut="long")

    def test_command_exit_codes(self):
        wav(self.p("m.wav"), [834.167, 3253.25 + 100])
        for name, body in (("l.json", LOCKUP),
                           ("c.json", {"cues": [cue("a", 20, 834.167)]}),
                           ("bad.json", {"cues": [cue("b", 78, 3253.25)]})):
            with open(self.p(name), "w") as f:
                json.dump(body, f)

        def run(*a):
            r = subprocess.run([sys.executable, str(WORKFLOW), "sting-sync", *a],
                               capture_output=True, text=True)
            return r.returncode, r.stdout, r.stderr

        base = ["--lockup", self.p("l.json"), "--wav", self.p("m.wav")]
        self.assertEqual(run(*base, "--cues", self.p("c.json"))[0], 0)
        status, out, _ = run(*base, "--cues", self.p("bad.json"), "--json")
        self.assertEqual((status, json.loads(out)[0]["status"]), (1, "FAIL"))
        self.assertEqual(run(*base, "--cues", self.p("nope.json"))[0], 2)
        self.assertEqual(run("--lockup", self.p("l.json"), "--cues", self.p("c.json"))[0], 2)


if __name__ == "__main__":
    unittest.main()
