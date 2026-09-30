"""
Offline tests for rpresolve.reframe: the crop transform against hand-computed
Zoom/Pan, the inside-crop check, choosing the primary face, spans with no
face, the widest-filled fallback, the picture area, and the planner end to
end on a fake source. Synthetic boxes (Vision's JSON shape) and generated
frames only: no real media, no Resolve.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from rpresolve import cutlist, reframe, vision  # noqa: E402

TALL, SQUARE = reframe.ASPECTS["9x16"], reframe.ASPECTS["1x1"]
HD720, HD, UHD = (1280, 720), (1920, 1080), (3840, 2160)


def full(size):
    return (0, 0, size[0], size[1])


def face(cx, cy, w, h, src):
    """A Vision box (normalized, top-left origin) centred on (cx, cy) source px."""
    return {"x": (cx - w / 2.0) / src[0], "y": (cy - h / 2.0) / src[1],
            "w": w / float(src[0]), "h": h / float(src[1]), "confidence": 0.9}


def box(cx, cy, w, h):
    return (cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)


class TestTransform(unittest.TestCase):
    """Zoom = scale / fit, Pan = (sw/2 - cx) * scale, Tilt = (cy - sh/2) * scale,
    fit = min(W/sw, H/sh) under scaleToFit."""

    def props(self, src, out, scale, centre):
        return {k: round(v, 4) for k, v in reframe.resolve_props(src, out, scale, centre).items()}

    def test_9x16_at_the_default_scale(self):
        # The Ep 002 numbers: face_x 270 on a 1280x720 source reads back Zoom 2.6667, Pan 832.5.
        self.assertEqual(self.props(HD720, TALL, 2.25, (270, 360)),
                         {"ZoomX": 2.6667, "ZoomY": 2.6667, "Pan": 832.5, "Tilt": 0.0})
        # 1920x1080: fit 1080/1920 = 0.5625, so 2.25 is zoom 4; (960 - 700) * 2.25 = 585.
        self.assertEqual(self.props(HD, TALL, 2.25, (700, 540)),
                         {"ZoomX": 4.0, "ZoomY": 4.0, "Pan": 585.0, "Tilt": 0.0})
        # 3840x2160: fit 0.28125, zoom 8; (1920 - 1500) * 2.25 = 945.
        self.assertEqual(self.props(UHD, TALL, 2.25, (1500, 1080)),
                         {"ZoomX": 8.0, "ZoomY": 8.0, "Pan": 945.0, "Tilt": 0.0})

    def test_9x16_widest_filled(self):
        # Filling 1920 rows needs 1920/sh output px per source px: 2.6667, 1.7778, 0.8889.
        for src, want_scale in ((HD720, 2.6667), (HD, 1.7778), (UHD, 0.8889)):
            s = reframe.fill_scale(TALL, full(src))
            self.assertEqual(s, want_scale, src)
            zoom = reframe.resolve_props(src, TALL, s, (src[0] / 2, src[1] / 2))["ZoomX"]
            self.assertAlmostEqual(zoom, 1920 / 1080 * 1920 / 1080, places=3)  # 3.1605
            self.assertTrue(reframe.fills(TALL, s, (src[0] / 2, src[1] / 2), full(src)))

    def test_1x1(self):
        # 1280x720 into 1080x1080: fit min(0.84375, 1.5) = 0.84375.
        self.assertEqual(self.props(HD720, SQUARE, 2.25, (270, 300)),
                         {"ZoomX": 2.6667, "ZoomY": 2.6667, "Pan": 832.5, "Tilt": -135.0})
        # Widest filled: 1080/sh, so 1.5, 1.0, 0.5, each a zoom of 16/9.
        for src, want_scale, centre, pan in ((HD720, 1.5, (270, 360), 555.0),
                                              (HD, 1.0, (700, 540), 260.0),
                                              (UHD, 0.5, (1500, 1080), 210.0)):
            s = reframe.fill_scale(SQUARE, full(src))
            self.assertEqual(s, want_scale)
            self.assertEqual(self.props(src, SQUARE, s, centre),
                             {"ZoomX": 1.7778, "ZoomY": 1.7778, "Pan": pan, "Tilt": 0.0})

    def test_scale_to_crop_and_unknown_scaling(self):
        # scaleToCrop fits by the larger ratio: 1920/720 for 9:16 from 720p.
        p = reframe.resolve_props(HD720, TALL, 2.6667, (640, 360), reframe.SCALE_TO_CROP)
        self.assertAlmostEqual(p["ZoomX"], 1.0, places=3)
        with self.assertRaises(reframe.ReframeError):
            reframe.resolve_props(HD720, TALL, 2.25, (640, 360), "stretch")

    def test_a_letterboxed_picture_leaves_bars_at_the_default(self):
        # A two-up call recording: 1280x720 whose picture is rows 180..540 only.
        area = (0, 180, 1280, 540)
        centre = reframe.place((400, 400), TALL, 2.25, area)
        self.assertEqual(reframe.bars_text(TALL, 2.25, centre, area),
                         "the picture covers 810 of 1920 rows")
        s = reframe.fill_scale(TALL, area)
        self.assertEqual(s, 5.3334)  # 1920/360, rounded up so no bar opens
        self.assertTrue(reframe.fills(TALL, s, reframe.place((400, 400), TALL, s, area), area))

    def test_the_centre_is_clamped_inside_the_picture(self):
        # A face at x 100 cannot centre a 480-wide window without leaving the frame.
        self.assertEqual(reframe.place((100, 360), TALL, 2.25, full(HD720)), (240.0, 360.0))
        self.assertEqual(reframe.place((1250, 360), TALL, 2.25, full(HD720)), (1040.0, 360.0))


class TestCheck(unittest.TestCase):
    # 9:16 from 1280x720 at 2.25, centred on (640, 360): the window is x 400..880.
    C = (640, 360)

    def slack(self, b):
        return reframe.slack(b, TALL, 2.25, self.C)

    def test_inside(self):
        # Grown by 10%: 592..688 -> output 432..648; 290..410 -> 802.5..1072.5.
        self.assertAlmostEqual(self.slack((600, 300, 680, 400)), 432.0)

    def test_touching_counts_as_inside(self):
        b = (410, 300, 510, 400)  # grown left edge 400 -> output x 0
        self.assertAlmostEqual(self.slack(b), 0.0)
        self.assertEqual(reframe.check([(1.0, b)], TALL, 2.25, self.C)["inside"], 1)

    def test_outside_by_how_much(self):
        b = (400, 300, 500, 400)  # grown left edge 390 -> output x -22.5
        self.assertAlmostEqual(self.slack(b), -22.5)
        c = reframe.check([(1.0, (600, 300, 680, 400)), (2.0, b)], TALL, 2.25, self.C)
        self.assertEqual((c["inside"], c["total"], c["share"]), (1, 2, 0.5))
        self.assertEqual((c["worst_t"], c["worst_px"]), (2.0, -22.5))

    def test_near_the_right_and_bottom_edges(self):
        # Grown right edge 879 -> output 1077.75: inside by 2.25.
        self.assertAlmostEqual(self.slack((791, 300, 871, 400)), 2.25)
        # 1:1 at 2.25: the window is 480 source px tall, rows 120..600.
        self.assertLess(reframe.slack((600, 500, 700, 600), SQUARE, 2.25, self.C), 0)
        self.assertGreater(reframe.slack((600, 480, 700, 540), SQUARE, 2.25, self.C), 0)

    def test_a_face_that_moves_across_the_span(self):
        # A 150-wide face walking from x 690 to 1010 on a 1920x1080 source.
        xs = [690, 770, 850, 930, 1010]
        boxes = [(float(i), box(x, 400, 150, 180)) for i, x in enumerate(xs)]
        c = reframe.check(boxes, TALL, 2.25, (850, 540))
        self.assertEqual((c["inside"], c["total"]), (3, 5))
        self.assertIn(c["worst_t"], (0.0, 4.0))
        self.assertAlmostEqual(c["worst_px"], -22.5)  # grown extent 600..1100 vs window 610..1090
        wide = reframe.fill_scale(TALL, full(HD))
        self.assertEqual(reframe.check(boxes, TALL, wide, (850, 540))["inside"], 5)


class TestFaces(unittest.TestCase):
    def samples(self, spec, src=HD720):
        """spec: per sample, a list of (cx, cy, w, h) faces."""
        return [(float(t), [reframe.box_px(face(*f, src), src) for f in faces])
                for t, faces in enumerate(spec)]

    def test_linking_follows_each_face_whatever_order_vision_lists_them(self):
        left, right = (400, 360, 160, 160), (900, 360, 150, 150)
        spec = [[left, right], [right, left]] * 3
        tracks = reframe.link(self.samples(spec))
        self.assertEqual(sorted(len(t) for t in tracks), [6, 6])
        for tr in tracks:
            xs = {round((b[0] + b[2]) / 2) for _, b in tr}
            self.assertEqual(len(xs), 1)

    def test_presence_beats_size_and_the_other_face_is_flagged(self):
        small, big = (400, 360, 120, 120), (900, 360, 200, 200)
        spec = [[small, big], [small, big], [small], [small, big], [small], [small, big]]
        tracks = reframe.link(self.samples(spec))
        pick, rule, note = reframe.choose(tracks)
        self.assertEqual(round(reframe.track_stats(tracks[pick])["x"]), 400)
        self.assertEqual(rule, reframe.RULE)
        self.assertIn("two faces", note)  # in 4 of 6, at least half: it may be the speaker

    def two_up(self, speaker_in, speaker_size):
        """A two-up call (picture rows 200..520): a listener at x 1000 in all 8
        samples, a speaker at x 400 in the first `speaker_in` of them."""
        listener = (1000, 360, 150, 150)
        speaker = (400, 360, speaker_size, speaker_size)
        return [[listener] + ([speaker] if t < speaker_in else []) for t in range(8)]

    def test_a_speaker_vision_misses_is_not_lost_quietly(self):
        # The speaker in 6 of 8, the listener in 8 of 8, the same size: the
        # listener wins on presence, and the choice is flagged.
        spec = self.two_up(6, 150)
        sp = reframe.plan_span([(float(t), [face(*f, HD720) for f in fs])
                                for t, fs in enumerate(spec)],
                               HD720, (0, 200, 1280, 520), ["1x1"])
        self.assertEqual(sp["face"]["x"], 1000.0)
        self.assertTrue(any("two faces" in r and "speaker_x" in r for r in sp["review"]),
                        sp["review"])
        # Named, the speaker is taken.
        sp = reframe.plan_span([(float(t), [face(*f, HD720) for f in fs])
                                for t, fs in enumerate(spec)],
                               HD720, (0, 200, 1280, 520), ["1x1"], speaker_x=400)
        self.assertEqual(sp["face"]["x"], 400.0)

    def test_a_smaller_rival_in_most_samples_is_flagged(self):
        # The speaker in 7 of 8 at 0.7 of the listener's area.
        tracks = reframe.link(self.samples(self.two_up(7, 150 * 0.7 ** 0.5)))
        pick, _, note = reframe.choose(tracks)
        self.assertEqual(round(reframe.track_stats(tracks[pick])["x"]), 1000)
        self.assertIn("two faces", note)
        self.assertIn("70% of its size", note)

    def test_a_rival_in_under_half_the_samples_is_no_rival(self):
        tracks = reframe.link(self.samples(self.two_up(3, 150)))
        self.assertEqual(reframe.choose(tracks)[2], "")

    def test_two_faces_equally_present_the_larger_wins_and_is_flagged(self):
        a, b = (400, 360, 160, 160), (900, 360, 150, 150)
        tracks = reframe.link(self.samples([[a, b]] * 6))
        pick, _, note = reframe.choose(tracks)
        self.assertEqual(round(reframe.track_stats(tracks[pick])["x"]), 400)
        self.assertIn("two faces", note)
        self.assertIn("speaker_x", note)
        # A much smaller second face is no rival.
        tracks = reframe.link(self.samples([[a, (900, 360, 100, 100)]] * 6))
        self.assertEqual(reframe.choose(tracks)[2], "")

    def test_speaker_x_names_the_face(self):
        a, b = (400, 360, 160, 160), (900, 360, 150, 150)
        tracks = reframe.link(self.samples([[a, b]] * 6))
        pick, rule, note = reframe.choose(tracks, speaker_x=1000)
        self.assertEqual(round(reframe.track_stats(tracks[pick])["x"]), 900)
        self.assertIn("speaker_x 1000", rule)
        self.assertEqual(note, "")
        pick, _, note = reframe.choose(tracks, speaker_x=640)
        self.assertIn("px from speaker_x", note)
        # A face in one sample only is never taken for the speaker, however near.
        spec = [[a, b]] + [[a]] * 5
        tracks = reframe.link(self.samples(spec))
        pick, _, _ = reframe.choose(tracks, speaker_x=900)
        self.assertEqual(round(reframe.track_stats(tracks[pick])["x"]), 400)


class TestPlanSpan(unittest.TestCase):
    def spec(self, faces_per_sample, src=HD720):
        return [(float(t), [face(*f, src) for f in fs]) for t, fs in enumerate(faces_per_sample)]

    def test_no_face_gets_no_centre(self):
        sp = reframe.plan_span(self.spec([[]] * 8), HD720, full(HD720), ["9x16", "1x1"])
        for a in ("9x16", "1x1"):
            self.assertEqual(sp["aspects"][a]["status"], reframe.NO_FACE)
            self.assertIn("no face in any of the 8", sp["aspects"][a]["reason"])
            self.assertNotIn("x", reframe.entry(sp["aspects"][a], HD720, None, None, []))
        black = reframe.plan_span(self.spec([[]] * 3), HD720, None, ["9x16"])
        self.assertIn("black", black["aspects"]["9x16"]["reason"])

    def test_pass_at_the_default_scale(self):
        sp = reframe.plan_span(self.spec([[(700, 540, 200, 220)]] * 8, HD), HD, full(HD),
                               ["9x16"])
        p = sp["aspects"]["9x16"]
        self.assertEqual((p["status"], p["scale"], p["x"], p["y"]), (reframe.PASS, 2.25, 700.0, 540.0))
        self.assertEqual(p["check"]["inside"], 8)
        self.assertEqual(p["props"], {"ZoomX": 4.0, "ZoomY": 4.0, "Pan": 585.0, "Tilt": 0.0})
        self.assertEqual(sp["review"], [])

    def test_fallback_when_the_face_leaves_the_default_crop(self):
        xs = [690, 770, 850, 930, 1010]
        sp = reframe.plan_span(self.spec([[(x, 400, 150, 180)] for x in xs], HD), HD, full(HD),
                               ["9x16"])
        p = sp["aspects"]["9x16"]
        self.assertEqual(p["status"], reframe.FALLBACK)
        self.assertEqual(p["scale"], 1.7778)
        self.assertEqual(p["check"]["inside"], 5)
        self.assertIn("leaves the crop on 2 of 5", p["reason"])
        self.assertEqual([t["label"] for t in p["tried"]], ["default", "widest filled"])

    def test_the_middle_of_the_extent_when_the_median_loses_the_face(self):
        # A 150 px face at x 500 for 5 samples, then 600, 700, 800 (1920x1080, 9:16 at
        # 2.25: a 480 px window). The median (500) loses the last sample; the grown
        # boxes span 410..890, exactly 480, so a crop centred at 650 holds all 8.
        xs = [500] * 5 + [600, 700, 800]
        sp = reframe.plan_span(self.spec([[(x, 540, 150, 150)] for x in xs], HD), HD, full(HD),
                               ["9x16"])
        self.assertEqual(sp["tracks"], 1)
        p = sp["aspects"]["9x16"]
        self.assertEqual((p["status"], p["centre"], p["scale"], p["x"], p["y"]),
                         (reframe.PASS, "extent", 2.25, 650.0, 540.0))
        self.assertEqual((p["check"]["inside"], p["check"]["total"], p["check"]["worst_px"]),
                         (8, 8, 0.0))
        self.assertIn("the median centre (x 500) loses the face on 2 of 8", p["reason"])
        self.assertEqual([(t["label"], t["centre"]) for t in p["tried"]],
                         [("default", "median"), ("default", "extent")])
        self.assertEqual(p["props"]["Pan"], (960 - 650) * 2.25)
        self.assertEqual(reframe.entry(p, HD, full(HD), sp["face"], [])["centre"], "extent")
        row = reframe.rows({"spans": [dict(sp, clip="c", span=1, hand_face_x=None,
                                           **{"in": 0, "out": 1})]})[0]
        self.assertEqual(row["centre"], "extent")

    def test_manual_only_when_no_centre_holds(self):
        # Steps of 100 px or less stay on one track. Grown extent 410..980 (570 px):
        # wider than the 480 px window at 2.25, inside the 607.5 px one at the
        # widest filled scale, where the median (550) still loses the last sample.
        xs = [500] * 4 + [600, 700, 800, 890]
        sp = reframe.plan_span(self.spec([[(x, 540, 150, 150)] for x in xs], HD), HD, full(HD),
                               ["9x16"])
        self.assertEqual((sp["tracks"], sp["review"]), (1, []))
        p = sp["aspects"]["9x16"]
        self.assertEqual((p["status"], p["centre"], p["scale"]), (reframe.FALLBACK, "extent", 1.7778))
        self.assertIn("middle of the face's extent", p["reason"])
        # 410..1090 (680 px) fits neither window.
        xs = [500] * 3 + [600, 700, 800, 900, 1000]
        p = reframe.plan_span(self.spec([[(x, 540, 150, 150)] for x in xs], HD), HD, full(HD),
                              ["9x16"])["aspects"]["9x16"]
        self.assertEqual(p["status"], reframe.MANUAL)
        self.assertIn("neither the median centre nor the middle of the face's extent", p["reason"])

    def test_fallback_when_the_default_leaves_bars(self):
        area = (0, 180, 1280, 540)
        sp = reframe.plan_span(self.spec([[(400, 360, 160, 160)]] * 6), HD720, area,
                               ["9x16", "1x1"])
        tall, square = sp["aspects"]["9x16"], sp["aspects"]["1x1"]
        self.assertEqual((tall["status"], tall["scale"]), (reframe.FALLBACK, 5.3334))
        self.assertIn("810 of 1920 rows", tall["reason"])
        self.assertTrue(tall["fills"])
        self.assertEqual((square["status"], square["scale"]), (reframe.FALLBACK, 3.0))
        e = reframe.entry(tall, HD720, area, sp["face"], sp["review"])
        self.assertEqual((e["x"], e["y"], e["scale"], e["src"], e["area"]),
                         (400.0, 360.0, 5.3334, [1280, 720], [0, 180, 1280, 540]))
        self.assertEqual(len(sp["track"]), 6)
        self.assertEqual(sp["track"][0][0], 0.0)

    def test_bars_only_when_asked(self):
        # A face that holds at 2.25 in the letterboxed picture but not at the fill scale.
        area = (0, 180, 1280, 540)
        spec = self.spec([[(400, 360, 200, 160)]] * 6)
        p = reframe.plan_span(spec, HD720, area, ["9x16"])["aspects"]["9x16"]
        self.assertEqual(p["status"], reframe.MANUAL)
        self.assertIn("810 of 1920 rows (the face stays inside on all 6 samples)", p["reason"])
        sp = reframe.plan_span(spec, HD720, area, ["9x16"], allow_bars=True)
        p = sp["aspects"]["9x16"]
        self.assertEqual((p["status"], p["scale"], p["fills"]), (reframe.BARS, 2.25, False))
        self.assertIn("kept with bars", p["reason"])
        e = reframe.entry(p, HD720, area, sp["face"], sp["review"])
        self.assertEqual((e["scale"], e["bars"]), (2.25, "the picture covers 810 of 1920 rows"))
        self.assertEqual(len(reframe.needs_person(reframe.rows(
            {"spans": [dict(sp, clip="c", span=1, hand_face_x=None, **{"in": 0, "out": 1})]}))), 1)

    def test_a_crop_that_sets_a_tilt_says_its_sign_is_unconfirmed(self):
        # 1:1 at 2.25 from 1920x1080: a 480-row window, so a face at y 400 moves
        # the crop off the middle row and Tilt is (400 - 540) * 2.25. A face on
        # the middle row sets none.
        def span(i, y):
            sp = reframe.plan_span(self.spec([[(700, y, 150, 150)]] * 8, HD), HD, full(HD),
                                   ["1x1"])
            return dict(sp, clip="c", span=i, hand_face_x=None, **{"in": 0, "out": 1})
        report = {"spans": [span(1, 400), span(2, 540)]}
        report["rows"] = reframe.rows(report)
        report["counts"] = reframe.counts(report["rows"])
        up, level = report["rows"]
        self.assertEqual((up["status"], up["tilt"], level["tilt"]), (reframe.PASS, -315.0, 0.0))
        self.assertIn("Tilt -315 assumes Resolve's y axis points up", up["reason"])
        self.assertNotIn("Tilt", level["reason"])
        self.assertEqual(report["counts"]["tilt"], 1)
        self.assertIn("1 set a Tilt, whose sign no render has confirmed", reframe.summary(report))
        self.assertIn("Tilt -315", reframe.line(up))

    def test_manual_when_nothing_fits(self):
        area = (0, 180, 1280, 540)
        sp = reframe.plan_span(self.spec([[(400, 360, 200, 160)]] * 6), HD720, area, ["9x16"])
        p = sp["aspects"]["9x16"]
        self.assertEqual(p["status"], reframe.MANUAL)
        self.assertIn("manual reframe or a split", p["reason"])
        e = reframe.entry(p, HD720, area, sp["face"], sp["review"])
        self.assertEqual(set(e), {"status", "reason"})

    def test_a_face_missing_from_some_samples_is_flagged(self):
        spec = [[(700, 540, 200, 220)]] * 5 + [[]] * 3
        sp = reframe.plan_span(self.spec(spec, HD), HD, full(HD), ["9x16"])
        self.assertEqual(sp["face_samples"], 5)
        self.assertIn("no face is checked at 5, 6, 7 s (3 of 8 samples)", sp["review"][0])

    def test_one_missing_sample_is_unchecked_and_flagged(self):
        # 7 of 8 is above any presence threshold; the eighth is still not checked.
        spec = [[(700, 540, 150, 150)]] * 7 + [[]]
        sp = reframe.plan_span(self.spec(spec, HD), HD, full(HD), ["9x16"])
        c = sp["aspects"]["9x16"]["check"]
        self.assertEqual((c["inside"], c["checked"], c["unchecked"], c["total"], c["share"]),
                         (7, 7, 1, 8, 0.875))
        self.assertEqual(sp["unchecked"], [7.0])
        self.assertTrue(any("no face is checked at 7 s" in r for r in sp["review"]))
        e = reframe.entry(sp["aspects"]["9x16"], HD, full(HD), sp["face"], sp["review"])
        self.assertEqual(e["inside"], [7, 8])
        self.assertIn("no face is checked", e["review"])

    def test_a_face_that_jumps_off_its_track_is_checked_in_its_place(self):
        # A 150 px face at x 700 in 7 samples and at x 1000 in the 8th: the jump
        # (two face widths) is past the linking distance, so it starts a track of
        # its own. That box is checked against the crop, and the span is flagged.
        spec = [[(700, 540, 150, 150)]] * 7 + [[(1000, 540, 150, 150)]]
        sp = reframe.plan_span(self.spec(spec, HD), HD, full(HD), ["9x16"])
        self.assertEqual((sp["tracks"], sp["face_samples"]), (2, 7))
        self.assertEqual([r[0] for r in sp["off_track"]], [7.0])
        self.assertEqual(sp["unchecked"], [])
        self.assertTrue(any("off its track at 7 s (x 1000)" in r for r in sp["review"]))
        p = sp["aspects"]["9x16"]
        self.assertEqual((p["check"]["checked"], p["check"]["total"]), (8, 8))
        eighth = reframe.box_px(face(1000, 540, 150, 150, HD), HD)
        inside = reframe.slack(eighth, TALL, p["scale"], (p["x"], p["y"])) >= 0
        # Whatever crop is chosen, a status that claims the face holds holds the eighth too.
        self.assertTrue(inside or p["status"] == reframe.MANUAL, p)
        # At the old crop (the median, x 700 at 2.25) the eighth sample is outside.
        self.assertLess(reframe.slack(eighth, TALL, 2.25, (700.0, 540.0)), 0)

    def test_a_face_far_off_the_path_or_held_by_another_track_is_not_taken(self):
        far = [[(700, 540, 150, 150)]] * 7 + [[(1600, 540, 150, 150)]]
        sp = reframe.plan_span(self.spec(far, HD), HD, full(HD), ["9x16"])
        self.assertEqual((sp["off_track"], sp["unchecked"]), ([], [7.0]))
        self.assertEqual(sp["aspects"]["9x16"]["check"]["unchecked"], 1)
        # The listener, in every sample, is someone: never taken for the speaker.
        two = [[(700, 540, 150, 150), (1000, 540, 150, 150)]] * 7 + [[(1000, 540, 150, 150)]]
        sp = reframe.plan_span(self.spec(two, HD), HD, full(HD), ["9x16"], speaker_x=700)
        self.assertEqual(sp["face"]["x"], 700.0)
        self.assertEqual((sp["off_track"], sp["unchecked"]), ([], [7.0]))
        self.assertTrue(any("no face is checked at 7 s" in r for r in sp["review"]))


class TestSampling(unittest.TestCase):
    def test_span_times(self):
        self.assertEqual(reframe.span_times(0, 4), [0.25, 0.75, 1.25, 1.75, 2.25, 2.75, 3.25, 3.75])
        self.assertEqual(len(reframe.span_times(10, 35)), 13)  # 25 s at 0.5 a second
        self.assertEqual(len(reframe.span_times(0, 3600)), reframe.MAX_SAMPLES)
        t = reframe.span_times(10, 35)
        self.assertTrue(10 < t[0] and t[-1] < 35)

    def test_picture_area(self):
        w, h = 64, 36
        letterbox = bytes([16] * (w * 9) + [120] * (w * 18) + [16] * (w * 9))
        self.assertEqual(reframe.picture_area([letterbox], (w, h)), (0, 9, 64, 27))
        # A black frame adds nothing; a pillarboxed frame widens nothing.
        black = bytes([0] * (w * h))
        self.assertEqual(reframe.picture_area([black, letterbox], (w, h)), (0, 9, 64, 27))
        self.assertIsNone(reframe.picture_area([black], (w, h)))
        pillar = bytes(([16] * 8 + [120] * 48 + [16] * 8) * h)
        self.assertEqual(reframe.picture_area([pillar], (w, h)), (8, 0, 56, 36))


class FakeReader:
    """A 1280x720 source of letterboxed frames (picture rows 180..540). With
    a duration, a time at or past it fails as ffmpeg does."""
    size = HD720

    def __init__(self, duration=0.0):
        self.times = []
        self.duration = duration

    def read(self, times, folder):
        w, h = self.size
        luma = bytes([16] * (w * 180) + [120] * (w * 360) + [16] * (w * 180))
        out = []
        for t in times:
            if self.duration and t >= self.duration:
                raise reframe.ReframeError(f"ffmpeg could not read the frame at {t:.3f}s")
            png = os.path.join(folder, f"{t:.3f}.png")
            open(png, "wb").close()
            self.times.append(t)
            out.append((t, png, luma))
        return out


def fake_detect(paths):
    """Two faces in every frame, the left one larger; none after 100 s."""
    out = {}
    for p in paths:
        t = float(os.path.basename(p)[:-4])
        out[p] = [] if t > 100 else [face(400, 360, 160, 160, HD720), face(900, 360, 150, 150, HD720)]
    return out


def synthetic_manifest(d):
    words = os.path.join(d, "w.json")
    with open(words, "w") as f:
        json.dump({"segments": [{"words": [{"word": "hello", "start": 1.0, "end": 1.5}]}]}, f)
    return {"version": 1, "source_path": os.path.join(d, "source.mp4"), "source_sha256": "0" * 64,
            "fps": 25, "words": words, "approved_text": os.path.join(d, "a.md"),
            "clips": [{"name": "a_30", "spans": [{"in": 10.0, "out": 20.0}, {"in": 30.0, "out": 34.0}],
                       "reframe": {"face_x": 270}},
                      {"name": "b_30", "spans": [{"in": 110.0, "out": 118.0}]}]}


class TestPlanManifest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.realpath(self.tmp.name)
        self.m = synthetic_manifest(self.dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_plan_rows_and_the_manifest_copy(self):
        reader = FakeReader()
        seen = []
        report = reframe.plan(self.m, reader=reader, detect=fake_detect,
                              progress=lambda d, n: seen.append((d, n)))
        self.assertEqual(seen, [(1, 3), (2, 3), (3, 3)])
        self.assertEqual(len(reader.times), 8 + 8 + 8)  # 10 s and 4 s spans take the 8 minimum
        rows = report["rows"]
        self.assertEqual([(r["clip"], r["span"], r["aspect"], r["status"]) for r in rows],
                         [("a_30", 1, "9x16", "fallback"), ("a_30", 1, "1x1", "fallback"),
                          ("a_30", 2, "9x16", "fallback"), ("a_30", 2, "1x1", "fallback"),
                          ("b_30", 1, "9x16", "no_face"), ("b_30", 1, "1x1", "no_face")])
        self.assertEqual((rows[0]["face_x"], rows[0]["hand_face_x"], rows[0]["dx"]), (400.0, 270, 130.0))
        self.assertIn("two faces", rows[0]["review"])
        self.assertEqual(report["counts"]["no_face"], 2)
        self.assertEqual(len(reframe.needs_person(rows)), 6)  # two faces flagged, and no face
        copy = reframe.apply(self.m, report)
        cutlist.validate_manifest(copy)
        self.assertNotIn("reframe", self.m["clips"][0]["spans"][0])  # the input is untouched
        self.assertEqual(copy["clips"][0]["reframe"], {"face_x": 270})
        e = copy["clips"][0]["spans"][0]["reframe"]
        self.assertEqual((e["9x16"]["x"], e["9x16"]["scale"], e["1x1"]["scale"]), (400.0, 5.3334, 3.0))
        self.assertIn("two faces", e["9x16"]["review"])
        self.assertEqual(copy["clips"][1]["spans"][0]["reframe"]["9x16"]["status"], "no_face")
        self.assertEqual(copy["reframe_plan"]["size"], [1280, 720])
        text = reframe.tsv(rows)
        self.assertEqual(text.splitlines()[0].split("\t"), list(reframe.COLUMNS))
        self.assertEqual(len(text.splitlines()), 7)

    def test_speaker_x_and_only(self):
        report = reframe.plan(self.m, aspects=["9x16"], speaker_x=900, only=["a_30"],
                              reader=FakeReader(), detect=fake_detect)
        self.assertEqual([r["face_x"] for r in report["rows"]], [900.0, 900.0])
        self.assertEqual([r["review"] for r in report["rows"]], ["", ""])
        with self.assertRaises(reframe.ReframeError):
            reframe.plan(self.m, only=["nope"], reader=FakeReader(), detect=fake_detect)
        with self.assertRaises(reframe.ReframeError):
            reframe.plan(self.m, aspects=["4x5"], reader=FakeReader(), detect=fake_detect)

    def test_a_span_past_the_source_end_is_read_up_to_it(self):
        # b_30 runs 110..118 s: on a 115 s source its last 3 of 8 samples are past
        # the end, and a span wholly past it gets no crop; neither stops the plan.
        self.m["clips"].append({"name": "c_30", "spans": [{"in": 120.0, "out": 125.0}]})
        reader = FakeReader(duration=115.0)
        report = reframe.plan(self.m, aspects=["9x16"], reader=reader, detect=fake_detect)
        self.assertTrue(all(t < 115.0 for t in reader.times))
        b, c = report["spans"][2], report["spans"][3]
        self.assertEqual(b["samples"], 5)
        self.assertIn("runs past the source's end (115 s): 3 of 8 sample times were not read",
                      "; ".join(b["review"]))
        self.assertEqual(c["aspects"]["9x16"]["status"], reframe.NO_FACE)
        self.assertIn("lies past the source's end", c["aspects"]["9x16"]["reason"])
        self.assertEqual(len(report["rows"]), 4)

    def test_a_missing_ffprobe_is_an_error_line(self):
        import resolve_workflow as rw
        from rpresolve import detect
        path = os.path.join(self.dir, "m.json")
        with open(path, "w") as f:
            json.dump(self.m, f)
        args = argparse.Namespace(manifest=path, out=os.path.join(self.dir, "copy.json"),
                                  aspects=["9x16"], samples=8, per_second=0.5, speaker_x=None,
                                  only=None, allow_bars=False)

        def missing(*a, **k):
            raise detect.ToolMissing("cannot run ffprobe: no such file")
        err = io.StringIO()
        with mock.patch.object(reframe, "run_ffprobe", missing), redirect_stderr(err), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(rw.cmd_reframe_plan(args), 1)
        self.assertIn("ERROR: cannot run ffprobe", err.getvalue())

    def test_no_vision_is_an_error_not_a_guess(self):
        with self.assertRaisesRegex(reframe.ReframeError, "Vision"):
            reframe.plan(self.m, reader=FakeReader(), detect=lambda paths: None)

    def test_cli_refuses_the_repo_and_the_manifest_itself(self):
        import resolve_workflow as rw
        path = os.path.join(self.dir, "m.json")
        with open(path, "w") as f:
            json.dump(self.m, f)
        for out, words in ((str(HERE / "reframed.json"), "inside this repository"),
                           (path, "the manifest itself")):
            args = argparse.Namespace(manifest=path, out=out, aspects=["9x16"], samples=8,
                                      per_second=0.5, speaker_x=None, only=None,
                                      allow_bars=False)
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                self.assertEqual(rw.cmd_reframe_plan(args), 1)
            self.assertIn(words, err.getvalue())
        self.assertFalse((HERE / "reframed.json").exists())

    def test_cli_writes_the_copy_and_the_report(self):
        import resolve_workflow as rw
        path = os.path.join(self.dir, "m.json")
        with open(path, "w") as f:
            json.dump(self.m, f)
        out = os.path.join(self.dir, "out", "m.reframed.json")
        os.makedirs(os.path.dirname(out))
        args = argparse.Namespace(manifest=path, out=out, aspects=["9x16"], samples=8,
                                  per_second=0.5, speaker_x=900, only=["a_30"],
                                  allow_bars=False)
        with mock.patch.object(reframe, "SourceReader", lambda p: FakeReader()), \
                mock.patch.object(reframe.vision, "face_boxes", fake_detect):
            stdout = io.StringIO()
            with redirect_stderr(io.StringIO()), redirect_stdout(stdout):
                self.assertEqual(rw.cmd_reframe_plan(args), 0)
        self.assertIn("FALLBACK", stdout.getvalue())
        with open(out) as f:
            copy = json.load(f)
        self.assertEqual(copy["clips"][0]["spans"][1]["reframe"]["9x16"]["x"], 900.0)
        self.assertTrue(os.path.exists(os.path.join(self.dir, "out", "m.reframed.reframe.tsv")))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "out", "m.reframed.reframe.json")))


class TestMCPTool(unittest.TestCase):
    def test_reframe_plan_through_the_registry(self):
        from rpresolve.mcp import schema, server
        from rpresolve.mcp.registry import ToolContext
        tool = server.build_registry().get("reframe_plan")
        self.assertTrue(tool.annotations["readOnlyHint"])
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            path = os.path.join(d, "m.json")
            with open(path, "w") as f:
                json.dump(synthetic_manifest(d), f)
            args = {"manifest": path, "out": os.path.join(d, "copy.json"), "limit": 2}
            self.assertEqual(schema.validate(tool.input_schema, args), [])
            with mock.patch.object(reframe, "SourceReader", lambda p: FakeReader()), \
                    mock.patch.object(reframe.vision, "face_boxes", fake_detect):
                r = tool.handler(schema.with_defaults(tool.input_schema, args), ToolContext())
            self.assertEqual((r["total"], r["returned"], r["next_offset"]), (6, 2, 2))
            self.assertEqual(r["verdict"], "review")
            self.assertIn("2 no face", r["summary"])
            for key in ("manifest", "tsv", "report"):
                self.assertTrue(os.path.exists(r[key]), key)
            with self.assertRaisesRegex(ValueError, "manifest itself"):
                tool.handler(schema.with_defaults(tool.input_schema,
                                                  {"manifest": path, "out": path}), ToolContext())
            self.assertTrue(schema.validate(tool.input_schema, {"manifest": path,
                                                                "aspects": ["4x5"]}))


FFMPEG = reframe.FFMPEG


@unittest.skipUnless(os.access(FFMPEG, os.X_OK), "ffmpeg missing")
class TestOnAGeneratedClip(unittest.TestCase):
    """Real ffmpeg frames: a grey 320x180 clip letterboxed to rows 45..135."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clip = os.path.join(self.tmp.name, "bars.mov")
        subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                        "color=c=0x808080:size=320x180:rate=25:duration=2,"
                        "drawbox=x=0:y=0:w=320:h=45:color=black:t=fill,"
                        "drawbox=x=0:y=135:w=320:h=45:color=black:t=fill",
                        "-c:v", "prores_ks", "-pix_fmt", "yuv422p10le", "-y", self.clip], check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reader_and_picture_area(self):
        reader = reframe.SourceReader(self.clip)
        self.assertEqual(reader.size, (320, 180))
        frames = reader.read([0.5, 1.5], self.tmp.name)
        self.assertTrue(all(os.path.getsize(png) > 0 for _, png, _ in frames))
        self.assertEqual(reframe.picture_area([l for _, _, l in frames], reader.size),
                         (0, 45, 320, 135))

    def test_a_span_past_the_end_of_a_real_clip(self):
        # The clip is 2 s long; a span of 1..5 s reads the samples before 2 s only.
        m = {"source_path": self.clip, "clips": [{"name": "c", "spans": [{"in": 1.0, "out": 5.0}]}]}
        report = reframe.plan(m, aspects=["9x16"], samples=4,
                              detect=lambda paths: {p: [] for p in paths})
        sp = report["spans"][0]
        self.assertEqual(sp["samples"], 1)  # of 1.5, 2.5, 3.5 and 4.5 s, only 1.5 is in the clip
        self.assertIn("3 of 4 sample times were not read", "; ".join(sp["review"]))

    def test_no_face_through_real_vision(self):
        if not (sys.platform == "darwin" and vision.vision_binary()):
            self.skipTest("macOS Vision helper unavailable")
        m = {"source_path": self.clip, "clips": [{"name": "c", "spans": [{"in": 0.2, "out": 1.8}]}]}
        report = reframe.plan(m, samples=3)
        self.assertEqual([r["status"] for r in report["rows"]], ["no_face", "no_face"])
        self.assertEqual(report["spans"][0]["area"], [0, 45, 320, 135])


if __name__ == "__main__":
    unittest.main()
