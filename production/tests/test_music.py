"""
Tests for rpresolve.music on generated click tracks (invented names): tempo and
steadiness are measured, a re-run keeps a person's tags, playlists are written
beside the library, and suggest takes half and double time. Files are never touched.

Run: /usr/bin/python3 -m unittest production.tests.test_music -v
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import music, music_tag  # noqa: E402

try:
    import numpy as np
except ImportError:  # the stdlib-only 3.9 CI job
    np = None

needs_audio = unittest.skipIf(np is None or shutil.which("ffmpeg") is None, "numpy or ffmpeg missing")
SR = 16000


def write_clicks(path, times, secs=24.0):
    y = np.zeros(int(SR * secs))
    n = int(0.03 * SR)
    burst = np.sin(2 * np.pi * 80 * np.arange(n) / SR) * np.exp(-np.arange(n) / (0.006 * SR))
    for t in times:
        i = int(t * SR)
        y[i:i + n] += burst[:max(0, min(n, len(y) - i))]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(y, -1, 1) * 30000).astype("<i2").tobytes())


class Pure(unittest.TestCase):
    def test_steadiness(self):
        self.assertTrue(music.steadiness([i * 0.5 for i in range(20)])[1])
        wobble = [i * 0.5 + (0.2 if i % 2 else 0) for i in range(20)]
        self.assertFalse(music.steadiness(wobble)[1])
        self.assertFalse(music.steadiness([0, 0.5, 1.0])[1])

    def test_tempo_gap_counts_half_and_double(self):
        self.assertEqual(music.tempo_gap(120, [118, 124]), 0)
        self.assertEqual(music.tempo_gap(60, [118, 124]), 0)
        self.assertEqual(music.tempo_gap(240, [118, 124]), 0)
        self.assertGreater(music.tempo_gap(100, [118, 124]), 0)

    def test_suggest_orders_by_gap_and_filters(self):
        lib = {"tracks": {
            "a": {"path": "a.wav", "bpm": 121, "steady": True, "feel": ["calm"]},
            "b": {"path": "b.wav", "bpm": 112, "steady": True, "feel": ["calm"]},
            "c": {"path": "c.wav", "bpm": 120, "steady": False},
            "d": {"path": "d.wav", "bpm": 120, "steady": True, "error": "x"}}}
        got = music.suggest(lib, bpm=[118, 124])
        self.assertEqual([t["path"] for t in got], ["a.wav", "b.wav"])
        self.assertEqual(len(music.suggest(lib, bpm=[118, 124], steady=False)), 3)
        self.assertEqual([t["path"] for t in music.suggest(lib, bpm=[118, 124], feel=["calm"])], ["a.wav", "b.wav"])

    def test_import_never_overwrites_a_person(self):
        lib = {"tracks": {"h": {"path": "p.wav", "genre": "house", "genre_source": "isaac"}}}
        n = music.import_tags(lib, {"p.wav": {"genre": "techno", "feel": ["calm"]}})
        self.assertEqual(n, 1)
        self.assertEqual(lib["tracks"]["h"]["genre"], "house")
        self.assertEqual(lib["tracks"]["h"]["feel"], ["calm"])

    def test_pick_keeps_a_likely_runner_up(self):
        self.assertEqual(music_tag.pick([0.6, 0.3, 0.1], ["a", "b", "c"]), ["a", "b"])
        self.assertEqual(music_tag.pick([0.9, 0.08, 0.02], ["a", "b", "c"]), ["a"])

    def test_duplicate_paths_are_kept_in_playlists(self):
        lib = {"tracks": {"h": {"path": "a/p.wav", "dupes": ["b/p.wav"], "bpm": 120}}}
        self.assertEqual(music.playlists(lib)["tempo-110-125"], ["a/p.wav", "b/p.wav"])

    def test_stale_playlists_go_but_a_persons_file_stays(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        lib = {"tracks": {"h": {"path": "p.wav", "bpm": 120, "feel": ["calm"]}}}
        music.write_playlists(d, lib)
        mine = os.path.join(d, "playlists", "favourites.m3u")
        open(mine, "w").write("#EXTM3U\n")
        lib["tracks"]["h"]["feel"] = ["dark"]
        music.write_playlists(d, lib)
        self.assertFalse(os.path.exists(os.path.join(d, "playlists", "feel-calm.m3u")))
        self.assertTrue(os.path.exists(os.path.join(d, "playlists", "feel-dark.m3u")))
        self.assertTrue(os.path.exists(mine))

    def test_suggest_by_method_and_any_feel(self):
        lib = {"tracks": {"a": {"path": "a.wav", "bpm": 121, "steady": True, "feel": ["calm"]},
                          "b": {"path": "b.wav", "bpm": 121, "steady": True, "feel": ["dark"]}}}
        self.assertEqual([t["path"] for t in music.suggest(lib, method="tempo-ladder", feel=["dark", "x"])], ["b.wav"])

    def test_playlists_by_band_and_feel(self):
        lib = {"tracks": {"h": {"path": "x/p.wav", "bpm": 120, "feel": ["calm"]}}}
        self.assertEqual(sorted(music.playlists(lib)), ["feel-calm", "tempo-110-125"])


@needs_audio
class Index(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        sub = os.path.join(self.d, "folder-a")
        os.makedirs(sub)
        self.steady = os.path.join(sub, "steady tone.wav")
        self.loose = os.path.join(self.d, "loose tone.wav")
        write_clicks(self.steady, [0.25 + i * 0.5 for i in range(46)])
        rng = np.random.RandomState(3)
        write_clicks(self.loose, np.cumsum(rng.uniform(0.2, 0.9, 40)))

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_index_measures_keeps_edits_and_leaves_files(self):
        before = {p: hashlib.sha256(open(p, "rb").read()).hexdigest() for p in (self.steady, self.loose)}
        lib = music.index(self.d, workers=2)
        rows = {t["path"]: t for t in lib["tracks"].values()}
        s = rows[os.path.join("folder-a", "steady tone.wav")]
        self.assertAlmostEqual(s["bpm"], 120, delta=3)
        self.assertTrue(s["steady"])
        self.assertIsNotNone(s["lufs"])
        self.assertFalse(rows["loose tone.wav"]["steady"])
        self.assertTrue(os.path.isfile(os.path.join(self.d, "library.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.d, "playlists", "tempo-110-125.m3u")))
        key = next(h for h, t in lib["tracks"].items() if t["path"].endswith("steady tone.wav"))
        music.set_fields(lib, key[:8], genre="house", feel=["calm"])
        music.save_library(self.d, lib)
        lib2 = music.index(self.d, workers=2)
        self.assertEqual(lib2["tracks"][key]["genre"], "house")
        self.assertEqual(lib2["tracks"][key]["feel_source"], "isaac")
        self.assertEqual(music.counts(lib2)["with_genre"], 1)
        after = {p: hashlib.sha256(open(p, "rb").read()).hexdigest() for p in before}
        self.assertEqual(before, after)
        shutil.copy(self.steady, os.path.join(self.d, "same bytes.wav"))
        lib3 = music.index(self.d, workers=2)
        self.assertEqual(len(lib3["tracks"]), 2)
        self.assertEqual(len(lib3["tracks"][key].get("dupes", [])) + 1, 2)
        self.assertTrue(os.path.isfile(self.steady))


if __name__ == "__main__":
    unittest.main()
