"""
Tests for rpresolve.decon_effects on clips ffmpeg makes here: scrolling bars with a known
freeze, a known speed ramp, and a flash.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import delivercheck as dc, methods  # noqa: E402

try:
    import numpy  # noqa: F401
    from rpresolve import decon, decon_effects, shots  # noqa: E402
except ImportError:
    decon = None

BARS = "smptebars=s=192x108:r={r}:d={d},scroll=horizontal={h}"


def make(out, graph):
    subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-filter_complex", graph + "[o]",
                    "-map", "[o]", "-c:v", "ffv1", out], check=True, stdin=subprocess.DEVNULL)


def method_with(slot_extra, seconds=3.0):
    slot = {"role": "place", "seconds": seconds, "query": "x", "move": "static", "speed": 1.0,
            "transition_out": {"type": "cut", "frames": 0}, "effect": None, "scale": None, "text": None}
    slot.update(slot_extra)
    return {"edit_method": 1, "id": "fx-check", "name": "Fx", "summary": "s", "fits": ["motion"],
            "aspect": "16:9", "fps": 30, "length_s": seconds, "bpm": None, "look": None,
            "rules": {"reuse": False, "min_shot_s": 0.5, "snap_to_beat": False, "end": None}, "slots": [slot]}


@unittest.skipIf(decon is None, "numpy not installed")
@unittest.skipUnless(os.access(dc.FFMPEG, os.X_OK), "ffmpeg missing")
class TestDeconEffects(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        cls.freeze = os.path.join(d, "freeze.mkv")   # motion 0 to 1 s, held 1 to 2 s, motion 2 to 3 s
        make(cls.freeze, BARS.format(r=30, d=3, h=0.004) + ",split=3[a][b][c];"
             "[a]trim=0:1,setpts=PTS-STARTPTS[x];"
             "[b]trim=start_frame=29:end_frame=30,loop=loop=29:size=1,setpts=N/30/TB[y];"
             "[c]trim=1:2,setpts=PTS-STARTPTS[z];[x][y][z]concat=n=3:v=1:a=0")
        cls.ramp = os.path.join(d, "ramp.mkv")       # speed 1x at 0 s rising linearly to about 4x
        make(cls.ramp, BARS.format(r=120, d=4, h=0.003) + ",setpts='((sqrt(1+4*PTS*TB)-1)/2)/TB',fps=30")
        cls.flat = os.path.join(d, "flat.mkv")       # constant speed, no ramp, no freeze
        make(cls.flat, BARS.format(r=30, d=3, h=0.004))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def found(self, path):
        rep = shots.build(path, dissolves=False)
        return rep, decon_effects.detect(rep, path)

    def test_freeze_found_with_its_time(self):
        rep, f = self.found(self.freeze)
        self.assertEqual(len(rep["shots"]), 1)
        self.assertEqual(len(f["freezes"]), 1)
        self.assertAlmostEqual(f["freezes"][0]["start"], 1.0, delta=0.1)
        self.assertAlmostEqual(f["freezes"][0]["seconds"], 1.0, delta=0.1)
        self.assertEqual(f["ramps"], [])

    def test_ramp_found_with_its_ratio(self):
        rep, f = self.found(self.ramp)
        self.assertEqual(len(f["ramps"]), 1, f)
        self.assertGreater(f["ramps"][0]["to"], 2.5)
        self.assertEqual(f["freezes"], [])

    def test_constant_speed_has_neither(self):
        _, f = self.found(self.flat)
        self.assertEqual((f["freezes"], f["ramps"]), ([], []))

    def test_screen_flag_reads_no_freezes_or_ramps(self):
        rep = shots.build(self.freeze, dissolves=False)
        f = decon_effects.detect(rep, self.freeze, screen=True)
        self.assertEqual((f["freezes"], f["ramps"]), ([], []))
        self.assertEqual(set(f["screen"]), {"holds_per_min", "still", "looks_like_screen"})
        rep = shots.build(self.ramp, dissolves=False)
        self.assertEqual(decon_effects.detect(rep, self.ramp, screen=True)["ramps"], [])
        self.assertTrue(decon_effects.detect(rep, self.ramp)["ramps"])

    def test_screen_hint(self):
        import numpy as np
        still = np.zeros((61, 10), np.float32)
        h = decon_effects.screen_hint([(0, 1)] * 20, still, 60.0)
        self.assertEqual(h, {"holds_per_min": 20.0, "still": 1.0, "looks_like_screen": True})
        moving = np.arange(610, dtype=np.float32).reshape(61, 10)
        self.assertFalse(decon_effects.screen_hint([(0, 1)] * 20, moving, 60.0)["looks_like_screen"])
        self.assertFalse(decon_effects.screen_hint([], still, 60.0)["looks_like_screen"])

    def test_still_picture_is_not_a_freeze(self):
        still = os.path.join(self.tmp.name, "still.mkv")
        make(still, "smptebars=s=192x108:r=30:d=2")
        _, f = self.found(still)
        self.assertEqual(f["freezes"], [])

    def test_recipe_carries_effects_into_the_manifest(self):
        rep, f = self.found(self.freeze)
        rec = decon.recipe(rep, self.freeze, "fx-freeze", found=f)
        self.assertEqual(methods.validate(rec), [])
        self.assertAlmostEqual(rec["slots"][0]["freeze"]["at"], 1.0, delta=0.1)
        rep, f = self.found(self.ramp)
        rec = decon.recipe(rep, self.ramp, "fx-ramp", found=f)
        self.assertEqual(methods.validate(rec), [])
        m = methods.to_manifest(rec, [{"source_path": "/a.mov", "in": 0, "out": rec["slots"][0]["seconds"]}])
        self.assertEqual(m["effects"][0]["effect"], "retime")
        self.assertEqual(m["effects"][0]["params"]["speed"], 1.0)
        self.assertGreater(m["effects"][0]["params"]["speed_to"], 2.5)

    def test_manifest_lists_freeze_as_a_step_and_ramp_as_an_effect(self):
        fill = [{"source_path": "/a.mov", "in": 0, "out": 3}]
        m = methods.to_manifest(method_with({"freeze": {"at": 1.0, "seconds": 1.0}}), fill)
        self.assertEqual(m["effects"], [])
        self.assertTrue(any(s["control"] == "Freeze Frame" for s in m["method"]["ui_steps"]))
        m = methods.to_manifest(method_with({"ramp": [1, 3]}), fill)
        self.assertEqual(m["effects"], [{"span_index": 0, "effect": "retime", "params": {"speed": 1, "speed_to": 3}}])

    def test_schema_refuses_bad_ramp_and_freeze(self):
        for extra in ({"ramp": [1]}, {"ramp": [1, 20]}, {"ramp": [1, 2], "speed": 2.0},
                      {"ramp": [1, 2], "effect": "exposure-flash"}, {"freeze": {"at": 2.5, "seconds": 1}},
                      {"freeze": {"at": 0}}):
            self.assertNotEqual(methods.validate(method_with(extra)), [], extra)

    def test_cli_effects_flag_writes_a_valid_recipe(self):
        import json
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO
        from rpresolve import cmd_decon
        out = os.path.join(self.tmp.name, "fx.json")
        args = type("A", (), dict(action="recipe", reference=self.freeze, out=out, id="fx-cli", name=None,
                                  fits="motion", beats=None, grade=False, titles=False, effects=True, against=None,
                                  backend="scdet", no_dissolves=True))()
        buf = StringIO()
        with redirect_stdout(buf), redirect_stderr(StringIO()):
            self.assertEqual(cmd_decon.run(args), 0)
        self.assertIn("1 freeze", buf.getvalue())
        self.assertEqual(methods.validate(json.loads(Path(out).read_text())), [])

    def test_blend_frames_are_not_a_ramp(self):
        import numpy as np
        g = np.zeros((60, 4), np.float32)
        step = np.concatenate([np.full(12, 3.0), np.full(47, 1.0)])   # fast blend, then steady motion
        g[1:] = np.cumsum(step)[:, None]
        shot = {"shot": 1, "in": 0.0, "out": 2.0, "blend_in": "", "blend_out": ""}
        self.assertEqual(len(decon_effects.ramps(g, 30, [shot], [])), 1)
        shot.update(blend_in=0.0, blend_out=12 / 30)
        self.assertEqual(decon_effects.ramps(g, 30, [shot], []), [])

    def test_flash_at_a_slot_start_is_its_effect(self):
        slots = [method_with({})["slots"][0] for _ in range(2)]
        shot_list = [{"shot": 1, "in": 0.0}, {"shot": 2, "in": 1.0}]
        found = {"ramps": [], "freezes": [],
                 "flashes": [{"shot": 2, "at": 1.1}, {"shot": 1, "at": 0.6}]}
        r = decon_effects.place(slots, shot_list, found)
        self.assertEqual(slots[1]["effect"], "exposure-flash")
        self.assertIsNone(slots[0]["effect"])
        self.assertEqual(len(r["left_off"]), 1)


if __name__ == "__main__":
    unittest.main()
