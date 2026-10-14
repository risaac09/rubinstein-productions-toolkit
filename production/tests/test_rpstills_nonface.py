"""
Offline tests for the rpstills changes that came out of wide frame grabs of a
stage: tiled face detection, a subject rule that keeps an audience out of the
count, whole-body person boxes, a frame score so a run with no face still gets
picks, and times for frames with no EXIF. Synthetic images generated here and
a fake Vision: no real media, no macOS Vision, no network.

Run: /usr/bin/python3 production/tests/test_rpstills_nonface.py
"""

import datetime as dt
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

from rpstills import __main__ as cli  # noqa: E402
from rpstills import cluster, cull, frames, index, review, times, vision  # noqa: E402

ZERO, ONES = "0" * 16, "f" * 16


def metric(sharp=300.0, mean=0.4, contrast=0.2, clip_lo=0.0, clip_hi=0.0):
    return {"sharp": sharp, "mean": mean, "contrast": contrast, "clip_lo": clip_lo, "clip_hi": clip_hi}


def person(x=0.4, y=0.1, w=0.2, h=0.8):
    return {"x": x, "y": y, "w": w, "h": h, "confidence": 0.9}


def face(h=0.1, x=0.4, y=0.2):
    return {"x": x, "y": y, "w": h / 2.0, "h": h, "confidence": 0.9, "quality": 0.5,
            "eye_left": 0.3, "eye_right": 0.3}


def row(i, t, faces=(), m=None, people=(), phash=ZERO, prefix="G"):
    return {"id": f"{prefix}{i:03d}", "time": t, "subsec": None, "phash": phash, "face_hash": None,
            "error": None, "proxy": f"proxies/{prefix}{i:03d}.jpg", "jpeg": f"/shoot/{prefix}{i:03d}.jpg",
            "faces": list(faces), "sharpness": [100.0] * len(faces), "people": list(people),
            "frame": m}


class TestFrameMeasures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, name, im):
        path = os.path.join(self.dir, name)
        im.save(path, "JPEG", quality=95)
        return path

    def checker(self, size=(400, 300), cell=8):
        im = Image.new("L", size, 40)
        d = ImageDraw.Draw(im)
        for y in range(0, size[1], cell):
            for x in range(0, size[0], cell):
                if (x // cell + y // cell) % 2:
                    d.rectangle([x, y, x + cell - 1, y + cell - 1], fill=200)
        return im.convert("RGB")

    def test_sharp_beats_blurred(self):
        sharp = frames.metrics(self.save("s.jpg", self.checker()))
        soft = frames.metrics(self.save("b.jpg", self.checker().filter(ImageFilter.GaussianBlur(9))))
        self.assertGreater(sharp["sharp"], 20 * soft["sharp"])
        self.assertFalse(frames.is_blurry(sharp))
        self.assertTrue(frames.is_blurry(soft))

    def test_the_numbers_of_a_known_image(self):
        m = frames.metrics(self.save("flat.jpg", Image.new("RGB", (200, 100), (128, 128, 128))))
        self.assertAlmostEqual(m["mean"], 0.5, delta=0.01)
        self.assertLess(m["contrast"], 0.01)
        self.assertEqual((m["clip_lo"], m["clip_hi"]), (0.0, 0.0))
        self.assertLess(m["sharp"], 1.0)

    def test_a_missing_file_has_no_measures(self):
        self.assertIsNone(frames.metrics(os.path.join(self.dir, "nope.jpg")))

    def test_exposure(self):
        self.assertEqual(frames.exposure_score(None), 0.0)
        self.assertEqual(frames.exposure_score(metric(mean=0.02)), 0.0)          # a lens cap
        self.assertEqual(frames.exposure_score(metric(mean=0.97)), 0.0)          # a white sheet
        self.assertEqual(frames.exposure_score(metric()), 1.0)
        self.assertEqual(frames.exposure_score(metric(contrast=0.03)), 0.25)     # fog
        self.assertEqual(frames.exposure_score(metric(clip_hi=0.25)), 0.0)       # a quarter blown
        self.assertEqual(frames.exposure_score(metric(clip_hi=0.1)), 0.6)
        self.assertEqual(frames.exposure_score(metric(clip_lo=0.75)), 0.5)       # mostly crushed
        # A dark stage with a lit performer is a good frame: dark mean, strong contrast, black
        # surround that is not crushed past half the frame.
        self.assertEqual(frames.exposure_score(metric(mean=0.15, contrast=0.25, clip_lo=0.3)), 1.0)

    def test_a_night_scene_scores_through_the_real_metrics(self):
        im = Image.new("L", (400, 300), 12)
        ImageDraw.Draw(im).rectangle([150, 60, 250, 240], fill=200)
        m = frames.metrics(self.save("night.jpg", im.convert("RGB")))
        self.assertLess(m["mean"], 0.25)
        self.assertEqual(frames.exposure_score(m), 1.0)

    def test_composition(self):
        self.assertIsNone(frames.composition_score([]))
        self.assertIsNone(frames.composition_score(None))
        self.assertEqual(frames.composition_score([person()]), 1.0)               # centred, big, headroom
        self.assertEqual(frames.composition_score([person(x=1 / 3.0 - 0.1)]), 1.0)  # on a third line
        tiny = person(x=0.9, y=0.0, w=0.05, h=0.1)
        self.assertEqual(frames.composition_score([tiny]), 0.1667)
        big = person(x=0.0, y=0.05, w=1.0, h=0.95)                                 # fills the frame
        self.assertEqual(frames.composition_score([big]), 0.65)                    # size 0.125 of 1, placed, headroom
        # The largest person is the subject.
        self.assertEqual(frames.composition_score([tiny, person()]), 1.0)

    def test_frame_score(self):
        self.assertEqual(frames.frame_score(1.0, 1.0, None), 1.0)
        self.assertEqual(frames.frame_score(0.5, 1.0, None), 0.7)
        self.assertEqual(frames.frame_score(1.0, 1.0, 0.0), 0.75)
        self.assertEqual(frames.frame_score(0.5, 0.5, 1.0), 0.625)
        self.assertEqual(frames.frame_score(1.0, 0.0, 1.0), 0.0)                   # blank, whatever else


class TestSubjectRule(unittest.TestCase):
    def test_an_audience_is_not_a_group(self):
        r = {"faces": [face(0.20)] + [face(0.05) for _ in range(30)]}
        self.assertEqual(cluster.frame_class(r), "solo")
        self.assertEqual(len(cluster.counted_faces(r)), 1)

    def test_similar_faces_all_count(self):
        self.assertEqual(cluster.frame_class({"faces": [face(0.2), face(0.15)]}), "pair")
        self.assertEqual(cluster.frame_class({"faces": [face(0.2), face(0.15), face(0.12)]}), "group")

    def test_a_face_under_the_minimum_never_counts(self):
        self.assertEqual(cluster.frame_class({"faces": [face(0.03)]}), "none")
        self.assertEqual(cluster.frame_class({"faces": []}), "none")

    def test_the_ratio_is_a_parameter(self):
        r = {"faces": [face(0.20), face(0.08)]}
        self.assertEqual(cluster.frame_class(r), "solo")
        self.assertEqual(cluster.frame_class(r, ratio=0.3), "pair")
        self.assertEqual(cluster.frame_class(r, min_face=0.04, ratio=0.0), "pair")

    def test_the_params_are_reported(self):
        c = cluster.build([row(1, "2026-01-15T10:00:00", faces=[face()])])
        self.assertEqual((c["params"]["min_face"], c["params"]["subject_ratio"]), (0.04, 0.5))


class TestCullFrames(unittest.TestCase):
    def build(self, rows):
        c = cluster.build(rows)
        return c, cull.build(rows, c)

    def stage(self):
        """Six frames 4 s apart, none with a face: one soft, one blank, two near-duplicates,
        two different good ones."""
        t = lambda s: (dt.datetime(2026, 1, 15, 10, 0, 0) + dt.timedelta(seconds=4 * s)).isoformat()
        return [row(0, t(0), m=metric(sharp=12.0), phash="0" * 15 + "1"),               # out of focus
                row(1, t(1), m=metric(sharp=400.0, mean=0.01), phash="0" * 15 + "2"),    # blank
                row(2, t(2), m=metric(sharp=300.0), phash=ZERO),
                row(3, t(3), m=metric(sharp=310.0), phash="0" * 15 + "3"),                # 2 bits from #2
                row(4, t(4), m=metric(sharp=350.0), phash=ONES),                          # a different picture
                row(5, t(5), m=metric(sharp=100.0, contrast=0.05), phash="f" * 8 + "0" * 8)]

    def test_a_run_with_no_face_gets_picks(self):
        rows = self.stage()
        clusters, doc = self.build(rows)
        self.assertEqual([r["class"] for r in clusters["runs"]], ["none"])
        (p,) = doc["proposal"]
        self.assertEqual((p["kind"], p["class"]), ("frame", "none"))
        self.assertEqual(p["picks"], ["G004", "G003"])          # sharpest distinct, then the next distinct
        for fid, why in (("G000", "blurry"), ("G001", "blank")):
            self.assertEqual(doc["scores"][fid]["score"], 0.0, why)
        self.assertTrue(doc["scores"]["G000"]["blurry"])
        self.assertEqual(doc["scores"]["G001"]["exposure"], 0.0)
        self.assertEqual(doc["scores"]["G004"]["kind"], "frame")
        self.assertNotIn("G002", p["picks"])                     # a near-duplicate of G003, ranked below it

    def test_the_best_of_a_soft_run_is_not_a_pick(self):
        rows = [row(i, f"2026-01-15T10:00:{4 * i:02d}", m=metric(sharp=s), phash=f"{i:016x}")
                for i, s in enumerate((8.0, 22.0, 35.0))]
        _, doc = self.build(rows)
        self.assertEqual(doc["proposal"][0]["picks"], [])
        self.assertEqual({v["score"] for v in doc["scores"].values()}, {0.0})

    def test_a_person_adds_composition(self):
        rows = [row(0, "2026-01-15T10:00:00", m=metric(sharp=300.0), phash=ZERO),
                row(1, "2026-01-15T10:00:04", m=metric(sharp=300.0), phash=ONES, people=[person()])]
        _, doc = self.build(rows)
        a, b = doc["scores"]["G000"], doc["scores"]["G001"]
        self.assertIsNone(a["composition"])
        self.assertEqual((b["composition"], b["people"]), (1.0, 1))
        self.assertEqual(doc["proposal"][0]["picks"][0], "G001")

    def test_one_pick_per_burst(self):
        rows = [row(0, "2026-01-15T10:00:00", m=metric(sharp=300.0), phash=ZERO),
                row(1, "2026-01-15T10:00:01", m=metric(sharp=320.0), phash=ZERO)]
        c, doc = self.build(rows)
        self.assertEqual(len(c["bursts"]), 1)
        self.assertEqual(doc["proposal"][0]["picks"], ["G001"])

    def test_how_many_a_long_run_proposes(self):
        run = {"frames": [str(i) for i in range(11)]}
        self.assertEqual(cull.frame_pick_count(run), 2)
        run = {"frames": [str(i) for i in range(27)]}
        self.assertEqual(cull.frame_pick_count(run), 2)
        run = {"frames": [str(i) for i in range(36)]}
        self.assertEqual(cull.frame_pick_count(run), 3)
        run = {"frames": [str(i) for i in range(500)]}
        self.assertEqual(cull.frame_pick_count(run), cull.NONE_MAX)

    def test_an_index_from_before_the_measures_proposes_nothing_as_it_always_did(self):
        rows = [row(i, f"2026-01-15T10:00:{4 * i:02d}", m=None, phash=f"{i:016x}") for i in range(3)]
        _, doc = self.build(rows)
        self.assertEqual(doc["proposal"][0]["picks"], [])
        self.assertEqual(doc["proposal"][0]["ranked"], ["G000", "G001", "G002"])

    def test_face_runs_are_still_ranked_by_the_face(self):
        rows = [row(i, f"2026-01-15T10:00:{4 * i:02d}", faces=[face(0.2)], m=metric(), phash=f"{i * 0x1111:016x}")
                for i in range(4)]
        for i, r in enumerate(rows):
            r["faces"][0]["quality"] = 0.2 + 0.2 * i
            r["sharpness"] = [100.0 + i]
        c, doc = self.build(rows)
        self.assertEqual(c["runs"][0]["class"], "solo")
        p = doc["proposal"][0]
        self.assertEqual((p["kind"], p["picks"][0]), ("face", "G003"))
        self.assertEqual(doc["scores"]["G003"]["kind"], "face")

    def near_duplicates(self, source):
        rows = [row(0, "2026-01-15T10:00:00", faces=[face(0.2)], phash=ZERO),
                row(1, "2026-01-15T10:00:04", faces=[face(0.2)], phash="0" * 15 + "3"),   # 2 bits away
                row(2, "2026-01-15T10:00:08", faces=[face(0.2)], phash=ONES)]
        for r, q in zip(rows, (0.9, 0.8, 0.7)):
            r["faces"][0]["quality"] = q
            if source is not False:
                r["time_source"] = source
        return self.build(rows)[1]["proposal"][0]["picks"]

    def test_a_face_run_of_frame_grabs_skips_a_frame_that_is_all_but_identical(self):
        self.assertEqual(self.near_duplicates("sequence"), ["G000", "G002"])
        self.assertEqual(self.near_duplicates("filename"), ["G000", "G002"])
        self.assertEqual(self.near_duplicates(None), ["G000", "G001", "G002"])   # no time at all (a corrupt EXIF date too): kept

    def test_camera_jpegs_keep_every_frame(self):
        # Two near-identical shots can differ in the eyes or the expression, which a hash cannot see.
        self.assertEqual(self.near_duplicates("exif"), ["G000", "G001", "G002"])
        self.assertEqual(self.near_duplicates(False), ["G000", "G001", "G002"])   # an index from before time_source

    def test_the_summary_names_frame_picks(self):
        _, doc = self.build(self.stage())
        self.assertEqual(cull.summary(doc),
                         "proposed 2 picks across 1 runs; 2 of them chosen by the frame itself in 1 of 1 "
                         "runs with no subject face")
        faces_only = self.build([row(0, "2026-01-15T10:00:00", faces=[face(0.2)])])[1]
        self.assertEqual(cull.summary(faces_only), "proposed 1 picks across 1 runs")

    def test_params_are_reported(self):
        _, doc = self.build(self.stage())
        for k in ("none_picks", "none_floor", "near_duplicate", "near_duplicate_face", "blur_below"):
            self.assertIn(k, doc["params"])

    def test_the_review_sheet_labels_frame_scores(self):
        rows = self.stage()
        with tempfile.TemporaryDirectory() as d:
            c, doc = self.build(rows)
            path = review.build(d, "t", "/shoot", rows, c, doc)
            html = Path(path).read_text()
        data = json.loads(html.split("const DATA = ")[1].split(";\nconst KEY")[0])
        by = {f["id"]: f for f in data["frames"]}
        self.assertEqual(by["G004"]["kind"], "frame")
        self.assertIn("exposure", by["G004"]["why"])
        self.assertEqual(by["G000"]["why"], "frame score: sharp rank 0.0, exposure 1.0")


class TestTiles(unittest.TestCase):
    def test_the_grid_covers_the_image_with_overlap(self):
        boxes = index.tile_boxes(2976, 1324)
        self.assertEqual(len(boxes), 6)
        self.assertEqual((min(b[0] for b in boxes), min(b[1] for b in boxes)), (0, 0))
        self.assertEqual((max(b[2] for b in boxes), max(b[3] for b in boxes)), (2976, 1324))
        row0 = sorted(b for b in boxes if b[1] == 0)
        for a, b in zip(row0, row0[1:]):
            overlap = a[2] - b[0]
            self.assertAlmostEqual(overlap / float(a[2] - a[0]), 0.3, delta=0.01)
        # every pixel column is in some tile
        covered = set()
        for b in boxes:
            covered.update(range(b[0], b[2]))
        self.assertEqual(len(covered), 2976)

    def test_a_portrait_image_swaps_the_grid(self):
        boxes = index.tile_boxes(1000, 3000)
        self.assertEqual(len({b[0] for b in boxes}), 2)
        self.assertEqual(len({b[1] for b in boxes}), 3)

    def test_a_face_in_a_tile_maps_back_to_the_frame(self):
        f = dict(face(0.5, x=0.25, y=0.5), quality=0.7)
        out = index.tile_to_frame(f, (1000, 200, 2000, 700), 3000, 1000)
        self.assertAlmostEqual(out["x"], (1000 + 0.25 * 1000) / 3000)
        self.assertAlmostEqual(out["y"], (200 + 0.5 * 500) / 1000)
        self.assertAlmostEqual(out["h"], 0.5 * 500 / 1000)
        self.assertAlmostEqual(out["w"], f["w"] * 1000 / 3000)
        self.assertEqual(out["quality"], 0.7)

    def test_duplicates_across_tiles_are_merged_and_the_larger_kept(self):
        a = {"x": 0.30, "y": 0.30, "w": 0.04, "h": 0.08}
        b = {"x": 0.305, "y": 0.302, "w": 0.038, "h": 0.076}         # the same face from the next tile
        c = {"x": 0.70, "y": 0.30, "w": 0.04, "h": 0.08}
        kept = index.merge_faces([b, a, c])
        self.assertEqual(len(kept), 2)
        self.assertIn(a, kept)
        inside = {"x": 0.31, "y": 0.31, "w": 0.01, "h": 0.02}          # centre inside a larger box
        self.assertEqual(len(index.merge_faces([a, inside])), 1)

    def test_a_small_face_crop_carries_no_identity(self):
        tmp = tempfile.mkdtemp()
        try:
            Image.new("RGB", (800, 400), (90, 90, 90)).save(os.path.join(tmp, "p.jpg"))
            small = [dict(face(0.05), x=0.4, y=0.4)]
            big = [dict(face(0.2), x=0.4, y=0.4)]
            if index.imagehash is not None:
                self.assertIsNone(index.face_hash(os.path.join(tmp, "p.jpg"), small))
                self.assertIsNotNone(index.face_hash(os.path.join(tmp, "p.jpg"), big))
            self.assertIsNone(index.face_hash(os.path.join(tmp, "p.jpg"), []))
        finally:
            shutil.rmtree(tmp)


def fake_vision(base, tiles_by_k, people=None):
    """A stand-in for vision.faces. base: {frame id: [faces]} for proxies; tiles_by_k:
    {(frame id, tile index): [faces in tile coordinates]} for tile images."""
    calls = []

    def faces(images):
        calls.append(list(images))
        out = {}
        for p in images:
            name = os.path.basename(p)[:-4]
            if "/tiles/" in p:
                fid, k = name.rsplit("_t", 1)
                fs = tiles_by_k.get((fid, int(k)), [])
                out[p] = {"width": 10, "height": 10, "faces": fs, "people": [], "error": None}
            else:
                out[p] = {"width": 10, "height": 10, "faces": base.get(name, []),
                          "people": (people or {}).get(name, []), "error": None}
        return out
    faces.calls = calls
    return faces


class TestBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.realpath(self.tmp.name)
        self.shoot = os.path.join(self.dir, "shoot")
        os.makedirs(self.shoot)
        for name in ("S_001", "S_002", "S_003"):
            im = Image.new("RGB", (1200, 600), (60, 50, 40))
            d = ImageDraw.Draw(im)
            for i in range(0, 1200, 30):
                d.line([(i, 0), (i, 600)], fill=(200, 190, 180), width=2)
            im.save(os.path.join(self.shoot, name + ".jpg"), "JPEG", quality=90)
        self.out = os.path.join(self.dir, "out")

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, fv, **kw):
        logs = []
        with mock.patch.object(vision, "faces", fv):
            rows = index.build(self.shoot, self.out, workers=2, log=logs.append, **kw)
        return {r["id"]: r for r in rows}, logs

    def test_tiles_find_a_face_the_proxy_missed_and_clean_up(self):
        fv = fake_vision({"S_001": [face(0.2, x=0.45, y=0.3)]},
                         {("S_002", 1): [dict(face(0.4, x=0.5, y=0.25), quality=0.6)]},
                         people={"S_003": [person()]})
        rows, logs = self.build(fv)
        a, b, c = rows["S_001"], rows["S_002"], rows["S_003"]
        self.assertEqual((a["face_source"], a["tiled"]), ("proxy", False))     # had a subject face: no second pass
        self.assertEqual((b["face_source"], b["tiled"]), ("tiles", True))
        self.assertEqual((c["face_source"], c["tiled"]), (None, True))         # tiled, nothing found
        # the face is at half the width of tile 1 of the 3x2 grid over a 1200x600 frame
        box = index.tile_boxes(1200, 600)[1]
        self.assertEqual(len(b["faces"]), 1)
        self.assertAlmostEqual(b["faces"][0]["x"], (box[0] + 0.5 * (box[2] - box[0])) / 1200, places=3)
        self.assertEqual(b["faces"][0]["quality"], 0.6)
        self.assertEqual(c["people"][0]["h"], 0.8)
        self.assertEqual(rows["S_001"]["people"], [])
        for r in rows.values():
            self.assertIsNotNone(r["frame"])
            self.assertGreater(r["frame"]["sharp"], 0)
        self.assertTrue(any("tiled pass on 2 frames" in m for m in logs), logs)
        self.assertTrue(any("tiles found a subject face on 1 more frames" in m for m in logs), logs)
        self.assertFalse(os.path.exists(os.path.join(self.out, "tiles")))       # tiles are removed
        tile_calls = [c for c in fv.calls if any("/tiles/" in p for p in c)]
        self.assertEqual(sum(len(c) for c in tile_calls), 12)                    # 2 frames x 6 tiles

    def test_tiles_off_and_all(self):
        fv = fake_vision({}, {("S_001", 0): [face(0.2)]})
        rows, _ = self.build(fv, tiles="off")
        self.assertTrue(all(not r["tiled"] and not r["faces"] for r in rows.values()))
        self.assertFalse(any("/tiles/" in p for c in fv.calls for p in c))
        fv = fake_vision({"S_001": [face(0.2, x=0.1, y=0.1)]}, {("S_001", 4): [face(0.2, x=0.4, y=0.4)]})
        rows, _ = self.shoot_again(fv, tiles="all")
        self.assertTrue(all(r["tiled"] for r in rows.values()))
        self.assertEqual(len(rows["S_001"]["faces"]), 2)                         # base face kept, tile face added
        self.assertEqual(rows["S_001"]["face_source"], "tiles")

    def shoot_again(self, fv, **kw):
        shutil.rmtree(self.out, ignore_errors=True)
        return self.build(fv, **kw)

    def test_a_bad_mode_is_refused(self):
        with self.assertRaises(ValueError):
            self.build(fake_vision({}, {}), tiles="sometimes")

    def test_no_vision_leaves_faces_empty_and_skips_the_tiles(self):
        with mock.patch.object(vision, "faces", lambda images: None):
            logs = []
            rows = index.build(self.shoot, self.out, workers=2, log=logs.append)
        self.assertTrue(all(r["faces"] == [] and not r["tiled"] for r in rows))
        self.assertTrue(any("Vision helper unavailable" in m for m in logs))

    def test_the_index_round_trips_and_older_ones_gain_the_measures(self):
        fv = fake_vision({}, {})
        self.build(fv)
        rows = index.load(self.out)
        for r in rows:
            r.pop("frame")
        with open(os.path.join(self.out, "index.jsonl"), "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        rows = index.augment_frame_metrics(self.out, index.load(self.out), log=lambda m: None)
        self.assertTrue(all(r["frame"] for r in rows))
        self.assertTrue(all("frame" in r for r in index.load(self.out)))
        self.assertIs(index.augment_frame_metrics(self.out, rows), rows)         # nothing left to add

    def test_times_come_from_a_sidecar_the_name_or_the_sequence(self):
        fv = fake_vision({}, {})
        rows, logs = self.build(fv)
        self.assertTrue(all(r["time"] is None and r["time_source"] is None for r in rows.values()))
        self.assertTrue(any("3 frames have no time" in m for m in logs), logs)
        shutil.rmtree(self.out)
        rows, _ = self.build(fv, interval=4.0, start="2026-01-15T10:00:00")
        self.assertEqual([rows[k]["time"] for k in ("S_001", "S_002", "S_003")],
                         ["2026-01-15T10:00:00", "2026-01-15T10:00:04", "2026-01-15T10:00:08"])
        self.assertEqual({r["time_source"] for r in rows.values()}, {"sequence"})
        shutil.rmtree(self.out)
        with open(os.path.join(self.shoot, "times.json"), "w") as f:
            json.dump({"S_002": "2026-01-15T12:00:00"}, f)
        rows, _ = self.build(fv, interval=4.0, start="2026-01-15T10:00:00")
        self.assertEqual((rows["S_002"]["time"], rows["S_002"]["time_source"]), ("2026-01-15T12:00:00", "sidecar"))
        self.assertEqual(rows["S_003"]["time_source"], "sequence")

    def test_a_global_start_is_not_applied_across_several_sequences(self):
        # Camera counters (IMG_1234) and a grab prefix in one folder: one --start cannot be right for both.
        for old, new in (("S_001", "IMG_1234"), ("S_002", "IMG_1235")):
            os.rename(os.path.join(self.shoot, old + ".jpg"), os.path.join(self.shoot, new + ".jpg"))
        rows, logs = self.build(fake_vision({}, {}), interval=4.0, start="2026-01-15T10:00:00")
        self.assertTrue(any("not applied" in m for m in logs), logs)
        self.assertEqual({r["time"] for r in rows.values()}, {None})
        shutil.rmtree(self.out)
        with open(os.path.join(self.shoot, "times.json"), "w") as f:
            json.dump({"starts": {"IMG": "2026-01-15T10:00:00", "S": "2026-01-15T11:00:00"}, "interval": 4}, f)
        rows, _ = self.build(fake_vision({}, {}))
        self.assertEqual(rows["S_003"]["time"], "2026-01-15T11:00:08")
        self.assertEqual(rows["IMG_1234"]["time_source"], "sequence")

    def test_the_measures_come_from_the_workers_without_a_second_pass(self):
        with mock.patch.object(frames, "metrics", side_effect=AssertionError("re-read a proxy")):
            rows, _ = self.build(fake_vision({}, {}))
        self.assertTrue(all(r["frame"] and r["frame"]["sharp"] > 0 for r in rows.values()))

    def test_a_frame_that_fails_mid_tiling_leaves_no_files(self):
        real = Image.Image.save
        count = {"n": 0}

        def flaky(self, path, *a, **k):
            if "_t" in os.path.basename(str(path)) and "S_002" in str(path):
                count["n"] += 1
                if count["n"] == 3:
                    raise OSError("disk full")
            return real(self, path, *a, **k)
        with mock.patch.object(Image.Image, "save", flaky):
            rows, logs = self.build(fake_vision({}, {}))
        self.assertTrue(any("no tiles for S_002" in m for m in logs), logs)
        self.assertFalse(os.path.exists(os.path.join(self.out, "tiles")))

    def test_a_bad_start_is_an_error(self):
        with self.assertRaises(ValueError):
            self.build(fake_vision({}, {}), interval=4.0, start="yesterday")

    def test_the_cli_runs_end_to_end(self):
        fv = fake_vision({"S_001": [face(0.2)]}, {})
        with mock.patch.object(vision, "faces", fv), redirect_stdout(io.StringIO()):
            rc = cli.main(["run", self.shoot, self.out, "--session", "t", "--tiles", "off",
                           "--interval", "4", "--start", "2026-01-15T10:00:00"])
        self.assertEqual(rc, 0)
        for name in ("index.jsonl", "clusters.json", "cull.json", "review.html"):
            self.assertTrue(os.path.exists(os.path.join(self.out, name)), name)
        with mock.patch.object(vision, "faces", fv), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = cli.main(["run", self.shoot, self.out, "--session", "t", "--start", "not a time"])
        self.assertEqual(rc, 1)


class TestTimes(unittest.TestCase):
    def test_a_stamp_in_the_name(self):
        f = times.from_filename
        self.assertEqual(f("20260115-100512_cam_a_clip.mov"), dt.datetime(2026, 1, 15, 10, 5, 12))
        self.assertEqual(f("20260115-1000_x.mov"), dt.datetime(2026, 1, 15, 10, 0, 0))
        self.assertEqual(f("shot 2026-01-15_10-05-12.png"), dt.datetime(2026, 1, 15, 10, 5, 12))
        self.assertEqual(f("2026-01-15T10:00.png"), dt.datetime(2026, 1, 15, 10, 0, 0))
        self.assertIsNone(f("shot_001.jpg"))
        self.assertIsNone(f("20261340-9999.jpg"))        # not a date

    def test_sequence_stems(self):
        self.assertEqual(times.sequence_of("shot_001"), ("shot", 1))
        self.assertEqual(times.sequence_of("take-12"), ("take", 12))
        self.assertEqual(times.sequence_of("my_clip_007"), ("my_clip", 7))
        self.assertIsNone(times.sequence_of("0001"))
        self.assertIsNone(times.sequence_of("IMG"))

    def test_precedence_and_source(self):
        start = dt.datetime(2026, 1, 15, 10, 0, 0)
        side = {"by_id": {"A_001": dt.datetime(2026, 1, 1)}, "starts": {"B": dt.datetime(2026, 2, 2)},
                "interval": 2.0}
        self.assertEqual(times.resolve("A_001", "A_001.jpg", side, 4.0, start), (dt.datetime(2026, 1, 1), "sidecar"))
        self.assertEqual(times.resolve("A_002", "20260115-1100_A_002.jpg", side, 4.0, start)[1], "filename")
        t, src = times.resolve("B_003", "B_003.jpg", side, None, start)         # the sidecar's start and interval
        self.assertEqual((t, src), (dt.datetime(2026, 2, 2, 0, 0, 4), "sequence"))
        t, src = times.resolve("C_003", "C_003.jpg", side, 4.0, start)           # the global start
        self.assertEqual((t, src), (dt.datetime(2026, 1, 15, 10, 0, 8), "sequence"))
        self.assertEqual(times.resolve("C_003", "C_003.jpg", None, None, start), (None, None))   # no interval
        self.assertEqual(times.resolve("C_003", "C_003.jpg", None, 4.0, None), (None, None))     # no start
        self.assertEqual(times.resolve("0001", "0001.jpg", None, 4.0, start), (None, None))      # not a sequence

    def test_sidecar_files(self):
        with tempfile.TemporaryDirectory() as d:
            j = os.path.join(d, "times.json")
            Path(j).write_text(json.dumps({"A_001": "2026-01-15T10:00:00", "starts": {"B": "2026-01-15 11:00:00"},
                                           "interval": 4}))
            s = times.load_sidecar(j)
            self.assertEqual(s["by_id"]["A_001"], dt.datetime(2026, 1, 15, 10, 0))
            self.assertEqual((s["starts"]["B"], s["interval"]), (dt.datetime(2026, 1, 15, 11, 0), 4.0))
            t = os.path.join(d, "times.tsv")
            Path(t).write_text("# comment\nA_001\t2026-01-15T10:00:00\n\nA_002\t2026-01-15T10:00:04\n")
            self.assertEqual(sorted(times.load_sidecar(t)["by_id"]), ["A_001", "A_002"])
            self.assertEqual(times.default_sidecar(d), j)
            os.remove(j)
            self.assertEqual(times.default_sidecar(d), t)
            os.remove(t)
            self.assertIsNone(times.default_sidecar(d))

    def test_a_bad_sidecar_names_what_is_wrong(self):
        with tempfile.TemporaryDirectory() as d:
            j = os.path.join(d, "t.json")
            Path(j).write_text(json.dumps({"A_001": "soon"}))
            with self.assertRaisesRegex(ValueError, "'A_001' is not an ISO time"):
                times.load_sidecar(j)
            Path(j).write_text(json.dumps({"starts": {"B": "later"}}))
            with self.assertRaisesRegex(ValueError, "starts\\['B'\\]"):
                times.load_sidecar(j)
            Path(j).write_text("[]")
            with self.assertRaisesRegex(ValueError, "expected a JSON object"):
                times.load_sidecar(j)
            t = os.path.join(d, "t.tsv")
            Path(t).write_text("A_001\tnever\n")
            with self.assertRaisesRegex(ValueError, "line 1"):
                times.load_sidecar(t)


class TestSwiftHelper(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("swiftc"), "swiftc unavailable")
    def test_the_vision_helper_type_checks(self):
        proc = subprocess.run(["swiftc", "-typecheck", str(HERE.parent / "rpstills" / "vision_stills.swift")],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_the_helper_writes_people(self):
        src = (HERE.parent / "rpstills" / "vision_stills.swift").read_text()
        self.assertIn("VNDetectHumanRectanglesRequest", src)
        self.assertIn('"people": people', src)

    def test_the_wrapper_passes_people_through(self):
        line = json.dumps({"path": "/x/a.jpg", "width": 10, "height": 5, "faces": [],
                           "people": [person()]})
        proc = mock.Mock(returncode=0, stdout=line + "\n")
        with mock.patch.object(vision, "binary", lambda: "/bin/true"), \
                mock.patch.object(vision.subprocess, "run", return_value=proc):
            out = vision.faces(["/x/a.jpg"])
        self.assertEqual(out["/x/a.jpg"]["people"][0]["h"], 0.8)


if __name__ == "__main__":
    unittest.main()
