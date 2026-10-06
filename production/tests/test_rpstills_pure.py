"""
Offline tests for rpstills.cluster and rpstills.cull: the pure logic over
an index, with no images, Vision or NAS. stdlib unittest only.

Run: /usr/bin/python3 production/tests/test_rpstills_pure.py
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpstills import cluster, cull


def row(i, t, faces=1, quality=0.5, eyes=0.3, sharp=100.0, phash="0" * 16, h=0.2, face_hash="0" * 16):
    return {"id": f"F{i:03d}", "time": t, "subsec": None, "phash": phash, "face_hash": face_hash, "error": None,
            "proxy": f"proxies/F{i:03d}.jpg", "jpeg": f"/shoot/F{i:03d}.JPG",
            "faces": [{"x": 0.4, "y": 0.2, "w": 0.15, "h": h, "confidence": 0.9, "quality": quality,
                       "eye_left": eyes, "eye_right": eyes} for _ in range(faces)],
            "sharpness": [sharp] * faces}


class ClusterTest(unittest.TestCase):
    def test_segments_bursts_runs(self):
        rows = [row(1, "2026-09-12T16:00:00"), row(2, "2026-09-12T16:00:01"),   # burst of two, same hash
                row(3, "2026-09-12T16:00:20", phash="f" * 16),                   # same run, new burst
                row(4, "2026-09-12T16:02:00", faces=3),                          # new run (gap and class)
                row(5, "2026-09-12T17:00:00")]                                   # new segment (58 min gap)
        c = cluster.build(rows)
        self.assertEqual([len(s["frames"]) for s in c["segments"]], [4, 1])
        self.assertEqual(c["bursts"][0], ["F001", "F002"])
        self.assertEqual(len(c["bursts"]), 4)
        self.assertEqual([(r["class"], len(r["frames"])) for r in c["runs"]],
                         [("solo", 3), ("group", 1), ("solo", 1)])

    def test_person_change_splits_a_solo_run(self):
        rows = [row(1, "2026-09-12T16:00:00"), row(2, "2026-09-12T16:00:05"),
                row(3, "2026-09-12T16:00:17", face_hash="f" * 16),   # 12 s pause and a new face
                row(4, "2026-09-12T16:00:19", face_hash="f" * 16),
                row(5, "2026-09-12T16:00:21", face_hash="0" * 16)]   # big hash jump alone also splits
        runs = cluster.build(rows)["runs"]
        self.assertEqual([r["frames"] for r in runs], [["F001", "F002"], ["F003", "F004"], ["F005"]])

    def test_small_background_face_does_not_count(self):
        r = row(1, "2026-09-12T16:00:00")
        r["faces"].append({"x": 0.9, "y": 0.1, "w": 0.02, "h": 0.02, "confidence": 0.6})
        self.assertEqual(cluster.frame_class(r), "solo")

    def test_bad_rows_are_skipped(self):
        rows = [row(1, "2026-09-12T16:00:00"), dict(row(2, "2026-09-12T16:00:01"), error="boom")]
        self.assertEqual(len(cluster.build(rows)["runs"][0]["frames"]), 1)


class CullTest(unittest.TestCase):
    def test_one_pick_per_burst_and_quality_order(self):
        rows = [row(1, "2026-09-12T16:00:00", quality=0.9), row(2, "2026-09-12T16:00:01", quality=0.95),  # one burst
                row(3, "2026-09-12T16:00:10", quality=0.6, phash="f" * 16),
                row(4, "2026-09-12T16:00:20", quality=0.7, phash="a" * 16, eyes=0.1),               # eyes closed
                row(5, "2026-09-12T16:00:30", quality=0.4, phash="5" * 16)]
        c = cluster.build(rows)
        cu = cull.build(rows, c)
        picks = cu["proposal"][0]["picks"]
        self.assertEqual(picks[0], "F002")           # best of the burst
        self.assertNotIn("F001", picks)              # the burst gives one pick
        self.assertEqual(len(picks), 3)
        self.assertEqual(cu["scores"]["F004"]["eyes"], 0.0)
        self.assertLess(cu["scores"]["F004"]["score"], cu["scores"]["F003"]["score"])

    def test_no_face_run_proposes_nothing(self):
        rows = [row(1, "2026-09-12T16:00:00", faces=0)]
        cu = cull.build(rows, cluster.build(rows))
        self.assertEqual(cu["proposal"][0]["picks"], [])
        self.assertEqual(cu["scores"]["F001"]["score"], 0.0)


if __name__ == "__main__":
    unittest.main()
