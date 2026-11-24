"""
Offline tests for rpresolve.scene_colour and the scene-colour command.
Worked numbers on hand-built packets, then one run on real perceive packets
of ffmpeg-generated frames. No Resolve, no real footage.

Run: /usr/bin/python3 -m unittest production.tests.test_scene_colour -v
"""

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import numpy as np  # noqa: F401
    from rpresolve import cmd_scene_colour, measure, perceive, scene_colour as sc
except ImportError:  # numpy missing on this interpreter
    np = None

needs_numpy = unittest.skipIf(np is None, "numpy not installed")

# Sharma, Wu and Dalal (2005), table 1, pair 1: CIEDE2000 = 2.0425.
LAB_1 = [50.0, 2.6772, -79.7751]
LAB_2 = [50.0, 0.0, -82.7485]


def cine(**over):
    c = {"source": {"file": "/media/a.mov", "t": 1.0},
         "neutral_cast": {"a": -1.2, "b": 3.4, "pct_of_frame": 12.0},
         "faces": [{"skin": None},
                   {"skin": {"lab": [62.0, 14.0, 18.0], "hue_deg": 126.5, "hue_off_skin_line_deg": 3.5,
                             "warnings": ["cheek near clip"]}}],
         "palette": [{"hex": "#000000", "lab": [10.0 * i, 0.0, 0.0], "chroma": 0.0, "hue_deg": 0.0,
                      "share_pct": 10.0} for i in range(9)]}
    c.update(over)
    return c


@needs_numpy
class TestDistances(unittest.TestCase):
    def test_hue_arc_takes_the_short_way_round(self):
        self.assertEqual(sc.hue_arc_deg(350, 10), 20)
        self.assertEqual(sc.hue_arc_deg(10, 350), 20)
        self.assertEqual(sc.hue_arc_deg(0, 180), 180)
        self.assertEqual(sc.hue_arc_deg(-30, 30), 60)
        self.assertEqual(sc.hue_arc_deg(721, 0), 1)

    def test_palette_distance_is_ciede2000_weighted_by_share(self):
        pal = [{"lab": LAB_1, "hue_deg": 271.9, "share_pct": 60.0},
               {"lab": [50.0, 0.0, 0.0], "share_pct": 40.0}]          # grey: no hue, chroma derived as 0
        ref = [{"lab": LAB_2, "hue_deg": 270.0, "share_pct": 50.0},
               {"lab": [50.0, 0.0, 0.0], "share_pct": 50.0}]
        d = sc.palette_distance(pal, ref)
        self.assertEqual(d["de2000"], 1.23)        # 0.6 x 2.0425 + 0.4 x 0
        self.assertEqual(d["hue_arc_deg"], 1.9)    # the grey pair carries no hue

    def test_palette_distance_without_a_palette_is_none(self):
        self.assertEqual(sc.palette_distance([], [{"lab": LAB_2, "share_pct": 1}]),
                         {"de2000": None, "hue_arc_deg": None})


@needs_numpy
class TestBlock(unittest.TestCase):
    def test_fields_from_perceive_and_detect(self):
        b = sc.block(cine(), {"hue_shift_shadows_vs_highlights_deg": -24.5},
                     {"path": "/media/a.mov", "input_color_space": "Panasonic V-Gamut/V-Log"})
        self.assertEqual((b["cast_a"], b["cast_b"]), (-1.2, 3.4))
        self.assertEqual((b["skin_hue_deg"], b["skin_off_line_deg"]), (126.5, 3.5))
        self.assertEqual(b["skin_lab"], [62.0, 14.0, 18.0])
        self.assertEqual(b["skin_warnings"], ["cheek near clip"])
        self.assertEqual(len(b["palette"]), 8)
        self.assertEqual(set(b["palette"][0]), {"lab", "hue_deg", "share_pct"})
        self.assertEqual(b["tint_shift_deg"], -24.5)
        self.assertEqual(b["input_space"], "Panasonic V-Gamut/V-Log")
        self.assertEqual(b["withheld"], {})
        self.assertIn("skin_hue_deg", b["taste"])
        self.assertIn("harmony", b["review"])

    def test_missing_numbers_are_null_with_a_reason(self):
        b = sc.block(cine(neutral_cast=None, faces="not measured (--no-faces)"), None,
                     {"path": "/media/a.mov", "input_color_space": "", "note": "no CaptureGamma"})
        for k in ("cast_a", "skin_hue_deg", "skin_lab", "tint_shift_deg", "input_space"):
            self.assertIsNone(b[k])
            self.assertIn(k, b["withheld"])
        self.assertEqual(b["withheld"]["skin_lab"], "not measured (--no-faces)")
        self.assertIn("--targets", b["withheld"]["tint_shift_deg"])
        self.assertIn("no CaptureGamma", b["withheld"]["input_space"])

    def test_a_log_source_withholds_every_picture_number(self):
        c = cine()
        c["source"]["transfer_note"] = "tagged log: every number here misreads it"
        b = sc.block(c, {"hue_shift_shadows_vs_highlights_deg": 5.0}, None)
        for k in sc.PICTURE:
            self.assertIsNone(b[k])
            self.assertIn("tagged log", b["withheld"][k])

    def test_between_two_shots(self):
        a = sc.block(cine(), None, None)
        b = sc.block(cine(neutral_cast={"a": 0.3, "b": 1.4}, faces=[{"skin": {
            "lab": [60, 12, 16], "hue_deg": 350.0, "hue_off_skin_line_deg": 227.0}}]), None, None)
        a["skin_hue_deg"] = 10.0
        d = sc.between(a, b)
        self.assertEqual(d["skin_hue_arc_deg"], 20.0)
        self.assertEqual(d["cast_delta"], {"a": 1.5, "b": -2.0})
        self.assertEqual(d["palette"]["de2000"], 0.0)


@needs_numpy
@unittest.skipUnless(os.access(measure.FFMPEG if np is not None else "", os.X_OK), "ffmpeg missing")
class TestOnPerceivePackets(unittest.TestCase):
    def test_blocks_from_real_packets_through_the_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            folders = []
            for name, src in (("grey", "color=c=0x666666:size=320x180"), ("bars", "smptehdbars=size=320x180")):
                still = os.path.join(tmp, name + ".png")
                subprocess.run([measure.FFMPEG, "-v", "error", "-f", "lavfi", "-i", src,
                                "-frames:v", "1", "-y", still], check=True)
                folders.append(os.path.join(tmp, name))
                perceive.perceive(still, folders[-1], faces=False, with_targets=True)
            detect = os.path.join(tmp, "detect.json")
            Path(detect).write_text(json.dumps([{"path": os.path.join(tmp, "bars.png"),
                                                 "input_color_space": "Rec.709 Gamma 2.4"}]))
            buf = io.StringIO()
            with redirect_stdout(buf):
                status = cmd_scene_colour.run(argparse.Namespace(packets=folders, detect=detect, out=None))
            self.assertEqual(status, 0)
            grey, bars = json.loads(buf.getvalue())["shots"]
            g, b = grey["colour"], bars["colour"]
            self.assertAlmostEqual(g["cast_a"], 0, delta=0.5)
            self.assertAlmostEqual(g["cast_b"], 0, delta=0.5)
            self.assertIsNone(g["tint_shift_deg"])           # a flat grey has no shadows or highlights
            self.assertIn("band", g["withheld"]["tint_shift_deg"])
            self.assertIn("input_space", g["withheld"])
            self.assertEqual(b["input_space"], "Rec.709 Gamma 2.4")
            self.assertIsNotNone(b["tint_shift_deg"])
            self.assertGreater(len(b["palette"]), 1)
            self.assertIn("skin_hue_deg", b["withheld"])     # --no-faces
            self.assertGreater(bars["from_previous"]["palette"]["de2000"], 5)

    def test_a_folder_without_a_packet_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(sc.SceneColourError):
                sc.shots([tmp])
            with self.assertRaises(sc.SceneColourError):
                sc.shots([], detect_rows={"path": "x"})
            Path(tmp, "packet.json").write_text('{"schema": "cine.perceive", "cine": {}}')
            with self.assertRaises(sc.SceneColourError):
                sc.shots([tmp])


if __name__ == "__main__":
    unittest.main()
