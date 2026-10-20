"""
Offline tests for rpresolve.perceive. Synthetic arrays and ffmpeg-generated
frames only; no Resolve, no real footage. Faces come from boxes written
here, not from macOS Vision.

Run: /usr/bin/python3 -m unittest production.tests.test_perceive -v
"""

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zlib
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import numpy as np
    from rpresolve import color, measure, perceive
except ImportError:  # numpy missing on this interpreter
    np = None

needs_numpy = unittest.skipIf(np is None, "numpy not installed")
SKIN = (0.80, 0.60, 0.50)


def flat(rgb, h=60, w=80):
    return np.broadcast_to(np.array(rgb, float), (h, w, 3)).copy()


@needs_numpy
class TestImages(unittest.TestCase):
    def test_png_round_trip(self):
        img = (np.arange(4 * 5 * 3) % 256).astype(np.uint8).reshape(4, 5, 3)
        data = perceive.png_bytes(img)
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        idat = data.index(b"IDAT")
        size = int.from_bytes(data[idat - 4:idat], "big")
        rows = zlib.decompress(data[idat + 4:idat + 4 + size])
        self.assertEqual(rows, b"".join(b"\x00" + img[y].tobytes() for y in range(4)))

    def test_downscale_keeps_small_frames_and_box_averages_large_ones(self):
        small = flat((0.2, 0.4, 0.6), 10, 20)
        self.assertIs(perceive.downscale(small, 20), small)
        big = np.zeros((4, 8, 3))
        big[:, ::2] = 1.0
        out = perceive.downscale(big, 4)
        self.assertEqual(out.shape, (2, 4, 3))
        self.assertTrue(np.allclose(out, 0.5))

    def test_waveform_puts_mid_grey_on_the_50_ire_row(self):
        img = perceive.waveform(flat((0.5, 0.5, 0.5)), w=64, h=101)
        trace = img[:, :, 1].astype(int) - img[:, :, 2].astype(int)  # amber lines have no blue
        self.assertEqual(img.shape, (101, 64, 3))
        row = int(np.argmax(img[:, 10, 2]))  # blue: the trace only
        self.assertEqual(row, 50)
        self.assertGreater(trace.max(), 0)

    def test_parade_has_one_third_per_channel(self):
        img = perceive.parade(flat((1.0, 0.0, 0.0)), w=96, h=101)
        self.assertEqual(img.shape, (101, 96, 3))
        # Red at full scale: the top row of the first third carries the trace,
        # green and blue sit on the bottom row of theirs.
        self.assertGreater(int(img[0, 5, 0]), 200)
        self.assertGreater(int(img[100, 40, 1]), 200)
        self.assertGreater(int(img[100, 80, 2]), 200)

    def test_vectorscope_places_red_and_draws_the_skin_line(self):
        size = 200
        img = perceive.vectorscope(flat((1.0, 0.0, 0.0)), size)
        c, r = size // 2, size // 2 - 4
        cb, cr = perceive._cbcr(np.array([1.0, 0.0, 0.0]))
        x, y = int(round(c + cb / 0.5 * r)), int(round(c - cr / 0.5 * r))
        self.assertEqual(tuple(img[y, x]), (255, 0, 0))
        ang = np.radians(measure.SKIN_LINE_DEG)
        t = r * 0.5
        self.assertEqual(tuple(img[int(round(c - t * np.sin(ang))), int(round(c + t * np.cos(ang)))]),
                         (255, 140, 0))

    def test_histogram_end_spike_and_shape(self):
        img = perceive.histogram(flat((1.0, 1.0, 1.0)))
        self.assertEqual(img.shape, (perceive.HIST_H, perceive.HIST_W, 3))
        self.assertTrue((img[:, -1] == 255).all())  # every channel clipped: a white bar at the right
        self.assertEqual(perceive.histogram(flat((0.5, 0.5, 0.5)), w=500, h=50).shape, (50, 500, 3))

    def test_sheet_layout(self):
        imgs = {"frame": np.full((90, 160, 3), 10, np.uint8), "falsecolour": np.full((90, 160, 3), 20, np.uint8),
                "waveform": np.full((256, 512, 3), 30, np.uint8), "vectorscope": np.full((384, 384, 3), 40, np.uint8),
                "palette": np.full((48, 512, 3), 50, np.uint8)}
        out = perceive.sheet(imgs, w=1280)
        self.assertEqual(out.shape, (360 + 256 + 32, 1280, 3))
        self.assertEqual((out[0, 0, 0], out[0, 1279, 0], out[400, 0, 0], out[400, 1279, 0], out[-1, 0, 0]),
                         (10, 20, 30, 40, 50))

    def test_false_colour_bands(self):
        img, shares = perceive.false_colour(flat((0.40, 0.40, 0.40)))
        by = {s["band"]: s["pct"] for s in shares}
        self.assertEqual(by["green"], 100.0)
        self.assertEqual(tuple(img[0, 0]), (0, 210, 0))
        _, shares = perceive.false_colour(flat((1.0, 1.0, 1.0)))
        self.assertEqual({s["band"]: s["pct"] for s in shares}["red"], 100.0)
        _, shares = perceive.false_colour(flat((0.7, 0.7, 0.7)))
        self.assertEqual(sum(s["pct"] for s in shares), 0.0)
        full = flat((0.7, 0.7, 0.7))
        full[:3, :4] = 1.0  # a highlight a downscale would average away
        img, shares = perceive.false_colour(perceive.downscale(full, 10), full)
        self.assertEqual({s["band"]: s["pct"] for s in shares}["red"], 0.25)
        self.assertEqual(img.shape, (7, 10, 3))


@needs_numpy
class TestNumbers(unittest.TestCase):
    def test_luma_and_contrast(self):
        img = np.zeros((10, 10, 3))
        img[:, 5:] = 1.0
        n = perceive.luma_numbers(img)
        self.assertEqual((n["ire"]["p1"], n["ire"]["p99"]), (0.0, 100.0))
        self.assertAlmostEqual(n["display_contrast_stops"], np.log2(1 / perceive.DISPLAY_BLACK), places=2)
        self.assertEqual(perceive.luma_numbers(flat((0.5, 0.5, 0.5)))["display_contrast_stops"], 0.0)

    def test_neutral_cast_reads_warm_greys(self):
        lab = color.rec709_to_lab(flat((0.52, 0.50, 0.46)).reshape(-1, 3))
        cast = perceive.colour_numbers(lab)["neutral_cast"]
        self.assertGreater(cast["b"], 1.0)
        self.assertEqual(cast["pct_of_frame"], 100.0)
        saturated = color.rec709_to_lab(flat((1.0, 0.0, 0.0)).reshape(-1, 3))
        self.assertIsNone(perceive.colour_numbers(saturated)["neutral_cast"])

    def test_palette_finds_two_colours_and_their_shares(self):
        img = np.zeros((10, 10, 3))
        img[:7] = (0.8, 0.2, 0.4)
        img[7:] = (0.2, 0.4, 0.6)
        px = img.reshape(-1, 3)
        pal = perceive.palette(px, color.rec709_to_lab(px), k=4)
        self.assertEqual([p["share_pct"] for p in pal], [70.0, 30.0])
        self.assertEqual(pal[0]["hex"], "#cc3366")
        strip = perceive.palette_strip(pal, w=100, h=4)
        self.assertEqual(tuple(strip[0, 0]), (0xcc, 0x33, 0x66))
        self.assertEqual(tuple(strip[0, 99]), (0x33, 0x66, 0x99))

    def test_face_lighting_ratio(self):
        img = flat((0.1, 0.2, 0.8), 200, 200)
        img[40:160, 60:100] = SKIN                       # the face's left half, lit
        img[40:160, 100:140] = np.array(SKIN) * 0.6      # its right half, 0.6 of the signal
        box = {"x": 0.30, "y": 0.20, "w": 0.40, "h": 0.60}
        f = perceive.face_entry(img, box)
        self.assertEqual(f["lighting"]["lit_side"], "left")
        self.assertAlmostEqual(f["lighting"]["stops"], 2.4 * np.log2(1 / 0.6), places=2)
        self.assertLess(abs(f["skin"]["hue_off_skin_line_deg"]), 15)
        self.assertEqual(f["area_pct"], 24.0)
        self.assertEqual(f["centre"], [0.5, 0.5])
        none = perceive.face_entry(flat((0.1, 0.2, 0.8), 200, 200), box)
        self.assertIsNone(none["skin"])
        self.assertIsNone(none["lighting"])

    def test_withhold_ratios(self):
        numbers = {"luma": {"display_contrast_stops": 5.0},
                   "faces": [{"lighting": {"ratio": 2.0}}, {"lighting": None}]}
        perceive.withhold_ratios(numbers)
        self.assertIsNone(numbers["luma"]["display_contrast_stops"])
        self.assertEqual([f["lighting"] for f in numbers["faces"]], [None, None])
        perceive.withhold_ratios({"luma": {"display_contrast_stops": 1.0}, "faces": "not measured"})

    def test_compare(self):
        def numbers(p50, chroma, cast_b, skin):
            return {"luma": {"ire": {"p1": 1.0, "p10": 5.0, "p50": p50, "p90": 80.0, "p99": 95.0},
                             "display_contrast_stops": 6.0},
                    "chroma": {"p50": chroma, "p90": 40.0},
                    "neutral_cast": {"a": 0.0, "b": cast_b, "pct_of_frame": 10.0},
                    "palette": [{"lab": [50.0, 20.0, 20.0], "share_pct": 60.0},
                                {"lab": [30.0, 0.0, -20.0], "share_pct": 40.0}],
                    "faces": [{"skin": {"lab": skin}}]}
        out = perceive.compare(numbers(50.0, 22.0, 4.0, [60.0, 15.0, 18.0]),
                               numbers(45.0, 20.0, 1.0, [58.0, 12.0, 15.0]))
        self.assertEqual(out["luma_ire_delta"]["p50"], 5.0)
        self.assertEqual(out["chroma_p50_vs_ref_pct"], 10.0)
        self.assertEqual(out["neutral_cast_delta"], {"a": 0.0, "b": 3.0})
        self.assertEqual(out["palette_de2000"], 0.0)
        want = float(color.delta_e_2000(np.array([60.0, 15.0, 18.0]), np.array([58.0, 12.0, 15.0])))
        self.assertAlmostEqual(out["skin_de2000"], round(want, 2))


@needs_numpy
class TestOutFolder(unittest.TestCase):
    def test_refuses_git_trees_and_used_folders(self):
        here = str(Path(__file__).resolve().parent / "pkt")
        self.assertIn("git working tree", perceive.out_dir_problem(here))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(perceive.out_dir_problem(os.path.join(tmp, "new")))
            self.assertIsNone(perceive.out_dir_problem(tmp))  # empty
            Path(tmp, "x").write_text("x")
            self.assertIn("not an empty folder", perceive.out_dir_problem(tmp))
            self.assertIn("does not exist", perceive.out_dir_problem(os.path.join(tmp, "a", "b")))


@needs_numpy
@unittest.skipUnless(os.access(measure.FFMPEG if np is not None else "", os.X_OK), "ffmpeg missing")
class TestOnGeneratedFrames(unittest.TestCase):
    def test_packet_on_a_clip_against_a_still(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip, still, out = (os.path.join(tmp, n) for n in ("grey.mov", "bars.png", "pkt"))
            subprocess.run([measure.FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                            "color=c=0x666666:size=320x180:rate=24:duration=2",
                            "-pix_fmt", "yuv422p10le", "-c:v", "prores_ks", "-y", clip], check=True)
            subprocess.run([measure.FFMPEG, "-v", "error", "-f", "lavfi", "-i", "smptehdbars=size=320x180",
                            "-frames:v", "1", "-y", still], check=True)
            packet = perceive.perceive(os.path.relpath(clip), out, at=1.0, ref=still, faces=False)
            on_disk = json.loads(Path(out, "packet.json").read_text())
            names = sorted(os.listdir(out))
            ref_names = sorted(os.listdir(os.path.join(out, "ref")))
            with self.assertRaises(perceive.PerceiveError):
                perceive.perceive(clip, out, faces=False)  # the folder is no longer empty
            with self.assertRaises(perceive.PerceiveError):
                perceive.perceive(clip, os.path.join(tmp, "late"), at=9.0, faces=False)
            from unittest import mock
            with mock.patch.object(perceive, "write_png", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    perceive.perceive(clip, os.path.join(tmp, "failed"), faces=False)
            self.assertFalse(os.path.exists(os.path.join(tmp, "failed")))
        self.assertEqual(on_disk, json.loads(json.dumps(packet)))
        pngs = sorted(f + ".png" for f in perceive.LEGEND)
        self.assertEqual(names, sorted(pngs + ["packet.json", "ref"]))
        self.assertEqual(ref_names, pngs)
        self.assertEqual(packet["source"]["t"], 1.0)
        self.assertEqual(packet["source"]["file"], os.path.abspath(clip))
        self.assertAlmostEqual(packet["luma"]["ire"]["p50"], 40.0, delta=1.0)  # 0x66 = 40%
        self.assertEqual({b["band"]: b["pct"] for b in packet["false_colour"]}["green"], 100.0)
        self.assertEqual(packet["faces"], "not measured (--no-faces)")
        self.assertEqual(packet["reference"]["files"]["frame"], "ref/frame.png")
        self.assertLess(packet["compare"]["display_contrast_stops_delta"], 0)
        self.assertEqual(packet["generated_by"], "rpresolve.perceive")
        self.assertEqual(set(packet["legend"]), set(packet["files"]))
        self.assertNotIn("transfer_note", packet["source"])

    def test_numbers_agree_with_measure_and_hlg_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip, hlg = os.path.join(tmp, "src.mov"), os.path.join(tmp, "hlg.mov")
            subprocess.run([measure.FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                            "testsrc2=size=320x180:rate=24:duration=2",
                            "-pix_fmt", "yuv422p10le", "-c:v", "prores_ks", "-y", clip], check=True)
            subprocess.run([measure.FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24:duration=1",
                            "-vf", "setparams=color_trc=arib-std-b67", "-pix_fmt", "yuv422p10le",
                            "-c:v", "prores_ks", "-y", hlg], check=True)
            packet = perceive.perceive(clip, os.path.join(tmp, "a"), faces=False)
            report = measure.measure(clip, samples=1, faces=False)
            flagged = perceive.perceive(hlg, os.path.join(tmp, "b"), faces=False)
        self.assertEqual(packet["source"]["t"], report["frames"][0]["t"])
        for k in ("p1", "p50", "p99"):
            self.assertEqual(packet["luma"]["ire"][k], report["frames"][0]["luma"][k])
        self.assertEqual(packet["clipping"], report["frames"][0]["clipping"])
        self.assertEqual(packet["schema"], {"name": "cine.perceive", "version": 1})
        self.assertEqual(packet["transfer"]["tag"], "untagged")
        self.assertIsNotNone(packet["luma"]["display_contrast_stops"])
        if flagged["source"]["tags"]["color_transfer"] != "arib-std-b67":
            self.skipTest("this ffmpeg did not write the transfer tag")
        self.assertIn("HLG", flagged["source"]["transfer_note"])
        self.assertIsNone(flagged["luma"]["display_contrast_stops"])

    def test_cli(self):
        import resolve_workflow as rw
        with tempfile.TemporaryDirectory() as tmp:
            still, out = os.path.join(tmp, "s.png"), os.path.join(tmp, "pkt")
            subprocess.run([measure.FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90",
                            "-frames:v", "1", "-y", still], check=True)
            args = argparse.Namespace(file=still, out=out, at=None, ref=None, no_faces=True)
            stdout, err = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(err):
                self.assertEqual(rw.cmd_perceive(args), 0)
                self.assertEqual(rw.cmd_perceive(args), 1)
        self.assertIn("luma IRE p1/p10/p50/p90/p99", stdout.getvalue())
        self.assertIn("not an empty folder", err.getvalue())


if __name__ == "__main__":
    unittest.main()
