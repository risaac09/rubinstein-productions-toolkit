"""
rpresolve.framing_rules and its wiring in reframe.py: true-resolution maths, shot
size, look, rule choice and targets, edge checks, and plan_items end to end on a fake
reader and detector. Faces are given, not found.

    cd production && /usr/bin/python3 -m unittest tests.test_framing_rules
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rpresolve import framing_rules as fr  # noqa: E402
from rpresolve import reframe  # noqa: E402

PYXIS = (6048, 4032)
UHD, TALL = (3840, 2160), (2160, 3840)
LUMA = (60, 40)


class TestNativeAndLimits(unittest.TestCase):
    def test_pyxis_open_gate(self):
        # 16:9: the crop is 6048 wide, 6048 * 9/16 = 3402 high; 6048/3840 = 3402/2160 = 1.575.
        a = fr.limits(PYXIS, 1.0, UHD)
        self.assertEqual((a["crop"], a["stored"]), ([6048, 3402], [6048, 3402]))
        self.assertAlmostEqual(a["max_zoom"], 1.575, places=3)
        # 9:16: 4032 * 9/16 = 2268 wide; 2268/2160 = 4032/3840 = 1.05.
        b = fr.limits(PYXIS, 1.0, TALL)
        self.assertEqual(b["stored"], [2268, 4032])
        self.assertAlmostEqual(b["max_zoom"], 1.05, places=3)

    def test_s5ii_5_9k(self):
        # 5888x3312 is exactly 16:9: 5888/3840 = 1.5333.
        self.assertAlmostEqual(fr.limits((5888, 3312), 1.0, UHD)["max_zoom"], 1.533, places=3)
        # 9:16: 3312 * 9/16 = 1863 wide; 1863/2160 = 3312/3840 = 0.8625: rows 3312 < 3840.
        t = fr.limits((5888, 3312), 1.0, TALL)
        self.assertEqual(t["stored"], [1863, 3312])
        self.assertAlmostEqual(t["max_zoom"], 0.8625, places=2)
        self.assertLess(t["max_zoom"], 1)

    def test_s5ii_6k_open_gate(self):
        # 9:16: 3968 * 9/16 = 2232; 2232/2160 = 3968/3840 = 1.0333.
        t = fr.limits((5952, 3968), 1.0, TALL)
        self.assertEqual(t["stored"], [2232, 3968])
        self.assertAlmostEqual(t["max_zoom"], 1.033, places=3)
        # 16:9: 5952 wide, 3348 high; 5952/3840 = 3348/2160 = 1.55.
        w = fr.limits((5952, 3968), 1.0, UHD)
        self.assertEqual(w["stored"], [5952, 3348])
        self.assertAlmostEqual(w["max_zoom"], 1.55, places=3)

    def test_anamorphic_pyxis_open_gate(self):
        # 6048x4032 at 1.5x is 9072x4032 desqueezed. 16:9: 4032 * 16/9 = 7168 wide, which is
        # 7168 / 1.5 = 4778.67 stored columns; margin min(4778.67/3840, 4032/2160) = 1.2444.
        a = fr.limits(PYXIS, 1.5, UHD)
        self.assertEqual((a["crop"], a["stored"]), ([7168, 4032], [4779, 4032]))
        self.assertAlmostEqual(a["max_zoom"], 1.244, places=3)
        # 9:16: 2268 desqueezed = 1512 stored columns; 1512/2160 = 0.7 (rows 4032/3840 = 1.05).
        b = fr.limits(PYXIS, 1.5, TALL)
        self.assertEqual(b["stored"], [1512, 4032])
        self.assertAlmostEqual(b["max_zoom"], 0.7, places=3)

    def test_anamorphic_6_5(self):
        # 4832x4032 at 1.5x is 7248x4032 (1.798, wider than 16:9): same 7168 wide crop,
        # 4778.67 stored columns, margin 1.2444. 9:16 is 1512 columns again: 0.7.
        a = fr.limits((4832, 4032), 1.5, UHD)
        self.assertEqual(a["crop"], [7168, 4032])
        self.assertAlmostEqual(a["max_zoom"], 1.244, places=3)
        self.assertAlmostEqual(fr.limits((4832, 4032), 1.5, TALL)["max_zoom"], 0.7, places=3)

    def test_hand_case_on_a_uhd_timeline(self):
        # Timeline 3840x2160, the Pyxis fills at scale 3840/6048 = 0.6349 (rounded up).
        out = UHD
        fill = reframe.fill_scale(out, (0, 0) + PYXIS)
        # zoom 1.5: 3840 / (0.6349 * 1.5) = 4032 columns, margin 1.05: native.
        n = fr.native(PYXIS, PYXIS, out, fill * 1.5, fr.deliver_of("master", out))
        self.assertTrue(n["native"])
        self.assertAlmostEqual(n["margin"], 1.05, places=2)
        # zoom 1.6: 3780 columns, margin 0.984: below native.
        n = fr.native(PYXIS, PYXIS, out, fill * 1.6, fr.deliver_of("master", out))
        self.assertFalse(n["native"])
        self.assertAlmostEqual(n["margin"], 0.984, places=2)
        self.assertEqual(n["cols"], 3780)

    def test_native_text_names_the_upscale(self):
        n = fr.native(PYXIS, PYXIS, (1080, 1920), 0.4762 * 1.2, TALL)
        self.assertIn(" BELOW ", fr.native_text(n))
        self.assertNotIn("BELOW", fr.native_text(fr.native(PYXIS, PYXIS, (1080, 1920), 0.4762, TALL)))


class TestSubjectAndRule(unittest.TestCase):
    def test_shot_size_bands(self):
        # share = 1.6 * face_h * scale / out_h. With face_h 100, scale 1, out_h 1000 per share:
        # out_h = 160 / share.
        def size(share):
            return fr.shot_size(100, 1.0, 160.0 / share)
        for share, name in ((0.80, "ECU"), (0.75, "ECU"), (0.5, "CU"), (0.3, "MCU"),
                            (0.25, "MS"), (0.15, "MLS"), (0.10, "FS"), (0.05, "WS")):
            self.assertEqual(size(share), name, share)

    def test_look_of(self):
        self.assertIsNone(fr.look_of(None))
        self.assertIsNone(fr.look_of(0.34))
        self.assertIsNone(fr.look_of(-0.34))
        self.assertEqual(fr.look_of(0.35), "right")
        self.assertEqual(fr.look_of(-0.6), "left")

    def test_choose(self):
        wide, tall = (1920, 1080), (1080, 1920)
        self.assertEqual(fr.choose(wide, "hand", "left", True, "rules"), fr.HAND)
        self.assertEqual(fr.choose(wide, "saliency", None, False, "rules"), fr.SALIENCY)
        self.assertEqual(fr.choose(wide, "face", "left", True, "rules"), fr.AS_SHOT)
        self.assertEqual(fr.choose(wide, "face", "left", False, "centre"), fr.FACE)
        self.assertEqual(fr.choose(wide, "face", "left", True, "centre"), fr.AS_SHOT)
        self.assertEqual(fr.choose(wide, "face", "left", False, "rules"), fr.LOOK_ROOM)
        self.assertEqual(fr.choose(wide, "face", None, False, "rules"), fr.CENTRED)
        self.assertEqual(fr.choose(tall, "face", "left", False, "rules"), fr.CENTRED)
        self.assertEqual(fr.FACE, "face centre")

    def test_target_puts_the_eye_line_on_the_upper_third(self):
        face = {"x": 3000.0, "y": 1500.0, "w": 300.0, "h": 360.0}
        out, scale = (1920, 1080), 0.5
        # eye = 1500 - 180 + 0.2 * 360 = 1392; cy = 1392 + 1080 / 3 = 1752.
        cx, cy = fr.target(fr.CENTRED, face, out, scale)
        self.assertEqual((cx, cy), (3000.0, 1752.0))
        p = fr.placement(face, out, scale, (cx, cy))
        self.assertAlmostEqual(p["eye_line"], 1 / 3.0, places=3)
        self.assertAlmostEqual(p["third_px"], 0.0, places=1)
        self.assertAlmostEqual(p["face_x"], 0.5, places=3)
        # Other rules keep the face centre.
        self.assertEqual(fr.target(fr.FACE, face, out, scale), (3000.0, 1500.0))

    def test_look_room_puts_the_face_on_the_third_away_from_its_look(self):
        face = {"x": 3000.0, "y": 1500.0, "w": 300.0, "h": 360.0}
        out, scale = (1920, 1080), 0.5
        # Looking right: the crop centre moves 1920/(6*0.5) = 640 right of the face, so the
        # face sits 640 * 0.5 = 320 px left of the middle: 960 - 320 = 640 = 1/3 of 1920.
        c = fr.target(fr.LOOK_ROOM, face, out, scale, "right")
        self.assertEqual(c[0], 3640.0)
        self.assertAlmostEqual(fr.placement(face, out, scale, c)["face_x"], 1 / 3.0, places=3)
        c = fr.target(fr.LOOK_ROOM, face, out, scale, "left")
        self.assertEqual(c[0], 2360.0)
        p = fr.placement(face, out, scale, c)
        self.assertAlmostEqual(p["face_x"], 2 / 3.0, places=3)
        self.assertAlmostEqual(p["eye_line"], 1 / 3.0, places=3)


class TestEdges(unittest.TestCase):
    WIN = (1000.0, 0.0, 2000.0, 1000.0)

    def test_cut_faces(self):
        straddle = (950.0, 400.0, 1100.0, 600.0)   # crosses the left edge at 1000
        inside = (1200.0, 400.0, 1400.0, 600.0)
        outside = (100.0, 400.0, 300.0, 600.0)
        self.assertEqual(fr.cut_faces([straddle], self.WIN), ["left"])
        self.assertEqual(fr.cut_faces([inside], self.WIN), [])
        self.assertEqual(fr.cut_faces([outside], self.WIN), [])
        self.assertEqual(fr.cut_faces([straddle, inside, outside], self.WIN), ["left"])

    def frame(self, fn, size=(100, 40)):
        return bytes(fn(x, y) for y in range(size[1]) for x in range(size[0]))

    def test_bright_edges(self):
        size, src = (100, 40), (1000, 400)
        win = (200.0, 0.0, 800.0, 400.0)   # luma x 20..80, band 4% of 60 = 2.4: cols 20..22
        stripe = self.frame(lambda x, y: 255 if 20 <= x < 23 else 100)
        # left band mean 255; crop mean (3 * 255 + 57 * 100) / 60 = 107.75; 255 >= 172.
        found = fr.bright_edges([stripe], size, src, win)
        self.assertEqual([f[0] for f in found], ["left"])
        self.assertEqual(found[0][1:], (255, 108))
        self.assertEqual(fr.bright_edges([self.frame(lambda x, y: 128)], size, src, win), [])
        # Dark with noise: 255 in the band is out, 40 against 20 passes the ratio but not the
        # minimum luma of 96.
        noisy = self.frame(lambda x, y: 40 if x == 20 else 20)
        self.assertEqual(fr.bright_edges([noisy], size, src, win), [])
        self.assertEqual(fr.bright_edges([], size, src, win), [])


# ---------------------------------------------------------------------------
# Integration through reframe.plan_items
# ---------------------------------------------------------------------------

class Reader:
    def __init__(self, size, frame=None, luma_size=LUMA):
        self.size, self.luma_size, self.duration, self.squeeze = size, luma_size, 0.0, None
        self.frame = frame or bytes([128]) * (luma_size[0] * luma_size[1])

    def read(self, times, folder):
        out = []
        for t in times:
            path = os.path.join(folder, f"t{t:012.3f}.png")
            open(path, "wb").close()
            out.append((t, path, self.frame))
        return out


def faces(*boxes, src=PYXIS, yaw=None):
    """A detector answering every frame with these boxes (x0, y0, x1, y1 in source px)."""
    def one(b):
        d = {"x": b[0] / src[0], "y": b[1] / src[1], "w": (b[2] - b[0]) / src[0],
             "h": (b[3] - b[1]) / src[1], "confidence": 0.9}
        if yaw is not None:
            d["yaw"] = yaw
        return d
    return lambda paths: {p: [one(b) for b in boxes] for p in paths}


MAIN = (2850.0, 1300.0, 3150.0, 1660.0)   # 300x360 face, centre x 3000


def plan(aspect="9x16", detect=None, size=PYXIS, frame=None, hand=None, **kw):
    it = {"name": "c", "path": "/media/c.braw", "in": 0.0, "out": 8.0}
    return reframe.plan_items([it], aspect=aspect, reader_for=lambda i: Reader(size, frame),
                              detect=detect or faces(MAIN, src=size), hand=hand, **kw)


class TestPlanItems(unittest.TestCase):
    def test_every_crop_carries_the_framing_record(self):
        p = plan("9x16")
        f = p["items"][0]["entry"]["framing"]
        self.assertEqual((f["subject"], f["rule"]), ("face", fr.FACE))
        # 9:16 fill: 2268x4032 stored columns and rows, deliver 2160x3840, margin 1.05.
        self.assertEqual((f["native"]["cols"], f["native"]["rows"]), (2268, 4032))
        self.assertEqual(f["native"]["deliver"], [2160, 3840])
        self.assertAlmostEqual(f["native"]["margin"], 1.05, places=2)
        self.assertAlmostEqual(f["max_zoom"], 1.05, places=2)
        row = p["rows"][0]
        self.assertEqual((row["subject"], row["rule"]), ("face", fr.FACE))
        self.assertEqual((row["native_px"], row["deliver"]), ("2268x4032", "2160x3840"))
        self.assertAlmostEqual(row["margin"], 1.05, places=2)
        self.assertAlmostEqual(row["max_zoom"], 1.05, places=2)
        self.assertIn("shot", row)
        for col in ("subject", "rule", "native_px", "deliver", "margin", "max_zoom"):
            self.assertIn(col, reframe.FRAMING_COLUMNS)
        text = reframe.line(row)
        self.assertIn("subject face", text)
        self.assertIn(f"rule {fr.FACE}", text)
        self.assertIn("margin 1.05", text)
        self.assertNotIn(" BELOW ", text)
        self.assertIn("subject", reframe.tsv(p["rows"]).splitlines()[0])
        self.assertEqual(p["counts"]["below_native"], 0)

    def test_rules_mode_with_a_turned_face_uses_look_room(self):
        p = plan("16x9", detect=faces(MAIN, yaw=0.6), framing="rules")
        f = p["items"][0]["entry"]["framing"]
        self.assertEqual((f["rule"], f["look"]), (fr.LOOK_ROOM, "right"))
        review = " ".join(p["items"][0]["review"])
        self.assertIn("look room", review)
        self.assertIn("confirmed", review)
        self.assertIn("looking right", reframe.line(p["rows"][0]))
        # Without rules mode the same face stays on the face centre, and no sign line.
        q = plan("16x9", detect=faces(MAIN, yaw=0.6))
        self.assertEqual(q["items"][0]["entry"]["framing"]["rule"], fr.FACE)
        self.assertNotIn("look room", " ".join(q["items"][0]["review"]))

    def test_a_hand_zoom_past_native_is_flagged(self):
        # 16:9 deliverable 3840x2160: fill is 6048x3402 under the crop (margin 1.575). Zoom 1.6
        # leaves 6048/1.6 = 3780 columns < 3840; zoom 1.5 leaves 4032: native.
        over = plan("16x9", hand={1: (0.5, None, 1.6)})
        r = " ".join(over["items"][0]["review"])
        self.assertIn(" BELOW ", r)
        self.assertEqual(over["counts"]["below_native"], 1)
        self.assertIn(" BELOW ", reframe.line(over["rows"][0]))
        self.assertEqual(over["items"][0]["entry"]["framing"]["subject"], "hand")
        ok = plan("16x9", hand={1: (0.5, None, 1.5)})
        self.assertNotIn(" BELOW ", " ".join(ok["items"][0]["review"]))
        self.assertEqual(ok["counts"]["below_native"], 0)

    def test_require_native_flags_an_hd_source(self):
        hd = (1920, 1080)
        box = (880.0, 300.0, 1040.0, 500.0)
        # Margin 1920/3840 = 0.5 at fill. Native to no zoom, so only require_native flags it.
        d = plan("16x9", detect=faces(box, src=hd), size=hd)
        self.assertNotIn(" BELOW ", " ".join(d["items"][0]["review"]))
        self.assertEqual(d["counts"]["below_native"], 1)   # the row still says it
        s = plan("16x9", detect=faces(box, src=hd), size=hd, require_native=True)
        self.assertIn(" BELOW ", " ".join(s["items"][0]["review"]))
        self.assertIn("an upscale of 2.00x", " ".join(s["items"][0]["review"]))
        report = {"counts": s["counts"], "rows": s["rows"], "spans": [1]}
        self.assertIn("1 with fewer source pixels than the deliverable", reframe.summary(report))

    def test_a_bright_stripe_at_the_crop_edge_is_flagged(self):
        # 9:16 crop: 2268 wide centred on x 3000 (window 1866..4134), luma x 18.5..41.3 of 60.
        # Columns 17 to 20 at 255 over a grey 100: left band (col 18) 255 against a crop mean
        # of about 130.
        w, h = LUMA
        frame = bytes(255 if 17 <= x <= 20 else 100 for y in range(h) for x in range(w))
        p = plan("9x16", frame=frame)
        review = " ".join(p["items"][0]["review"])
        self.assertIn("the left edge is bright", review)
        self.assertNotIn("the right edge is bright", review)
        self.assertIn("left edge is bright", p["rows"][0]["edges"])
        self.assertNotIn("bright", " ".join(plan("9x16")["items"][0]["review"]))

    def test_a_second_face_across_the_crop_edge_is_flagged(self):
        # Window 1866..4134: a face from 4050 to 4250 crosses the right edge.
        other = (4050.0, 1300.0, 4250.0, 1500.0)
        p = plan("9x16", detect=faces(MAIN, other))
        self.assertIn("a second face is cut at the right edge", " ".join(p["items"][0]["review"]))
        clear = (4300.0, 1300.0, 4500.0, 1500.0)
        q = plan("9x16", detect=faces(MAIN, clear))
        self.assertNotIn("second face", " ".join(q["items"][0]["review"]))

    def test_a_bogus_framing_mode_raises(self):
        with self.assertRaises(reframe.ReframeError):
            plan("9x16", framing="bogus")


if __name__ == "__main__":
    unittest.main()
