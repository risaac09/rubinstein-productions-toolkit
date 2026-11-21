"""
Tests for rpresolve.musicmap and the beats part of decon.compare, on clips ffmpeg makes
here: a click track at a known BPM under flat-colour shots cut on the beats.

Run: ~/.venvs/rp-tools/bin/python -m unittest production.tests.test_musicmap -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import delivercheck as dc  # noqa: E402

try:
    import numpy as np
    from rpresolve import cmd_music_map, decon, musicmap, shots  # noqa: E402
except ImportError:  # numpy missing on this interpreter
    np = None

BPM, FPS, SECS = 120, 25, 24
PALETTE = ["0xe8e8e8", "0x202080", "0xe0c020", "0x208020"]


def click_wav(path, bpm=BPM, secs=SECS, quiet_after=None):
    """Kick-like thumps on every beat; louder in the first half so there are two sections."""
    sr, n = 16000, int(16000 * secs)
    y, t = np.zeros(n), np.arange(int(0.2 * sr)) / sr
    thump = np.sin(2 * np.pi * (55 + 110 * np.exp(-t * 35)) * t) * np.exp(-t * 14)
    for k in range(int(secs * bpm / 60)):
        i = int(round(k * 60 / bpm * sr))
        gain = 0.9 if quiet_after is None or i / sr < quiet_after else 0.05
        y[i:i + len(thump)] += gain * thump[:n - i]
    import wave
    with wave.open(path, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(sr)
        w.writeframes((y * 32767).astype("<i2").tobytes())


def video(out, wav, cuts_beats, secs=SECS, bpm=BPM):
    """Flat colour shots cut on the given beat numbers, with wav as the audio (wav None: silent)."""
    edges = [0] + [round(b * 60 / bpm * FPS) for b in cuts_beats] + [round(secs * FPS)]
    parts = [f"color=c={PALETTE[i % 4]}:s=192x108:r={FPS}:d={(b - a) / FPS},format=yuv420p[v{i}]"
             for i, (a, b) in enumerate(zip(edges, edges[1:]))]
    graph = ";".join(parts) + ";" + "".join(f"[v{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=0[v]"
    cmd = [dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y"] + (["-i", wav] if wav else [])
    cmd += ["-filter_complex", graph, "-map", "[v]"] + (["-map", "0:a", "-c:a", "aac"] if wav else [])
    subprocess.run(cmd + ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(secs), out],
                   check=True, stdin=subprocess.DEVNULL)


@unittest.skipIf(np is None, "numpy not installed")
@unittest.skipUnless(os.access(dc.FFMPEG, os.X_OK), "ffmpeg missing")
class TestMusicMap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        cls.wav = os.path.join(d, "click.wav")
        click_wav(cls.wav, quiet_after=12.0)
        cls.on = os.path.join(d, "on.mp4")
        video(cls.on, cls.wav, [4, 8, 12, 16, 20, 24, 32, 40])       # every cut on a beat
        cls.off = os.path.join(d, "off.mp4")
        video(cls.off, cls.wav, [3.5, 7.5, 12.5, 16.5, 21.5, 27.5, 33.5, 41.5])  # between beats
        cls.silent = os.path.join(d, "silent.mp4")
        video(cls.silent, None, [4, 8, 12, 16, 20, 24, 32, 40])
        cls.report = shots.build(cls.on, dissolves=False)
        cls.mm = musicmap.build(cls.on)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_beat_period_within_one_percent(self):
        gaps = np.diff(self.mm["beats_s"])
        self.assertAlmostEqual(float(np.median(gaps)), 60 / BPM, delta=0.01 * 60 / BPM)
        self.assertAlmostEqual(self.mm["bpm"], BPM, delta=0.01 * BPM)

    def test_every_cut_within_a_frame_of_a_beat_in_the_table(self):
        table = musicmap.cut_table(self.report, self.mm)
        self.assertEqual(len(table), 8)
        for r in table:
            self.assertLessEqual(abs(r["offset_frames"]), 1.0, r)

    def test_two_sections_loud_then_quiet(self):
        s = self.mm["sections"]
        self.assertEqual([x["label"] for x in s], ["s1", "s2"])
        self.assertEqual([x["level"] for x in s], ["high", "low"])
        self.assertAlmostEqual(s[0]["end_s"], 12.0, delta=1.5)
        table = musicmap.cut_table(self.report, self.mm)
        self.assertEqual(table[0]["section"], "s1")
        self.assertEqual(table[-1]["section"], "s2")

    def test_map_reads_as_a_beat_map(self):
        from rpresolve import rhythm
        p = os.path.join(self.tmp.name, "mm.json")
        with open(p, "w") as f:
            json.dump(self.mm, f)
        self.assertEqual(len(rhythm.load_beats(p)), len(self.mm["beats_s"]))

    def test_compare_self_with_audio_is_100_with_a_beats_part(self):
        r = decon.compare(self.report, self.report, self.on, self.on)
        self.assertEqual(r["score"], 100.0)
        self.assertEqual(r["parts"]["beats"], 1.0)
        self.assertEqual(len(r["parts"]), 8)  # main's seven plus beats

    def test_compare_off_beat_rebuild_loses_on_the_beats_part(self):
        rb = shots.build(self.off, dissolves=False)
        r = decon.compare(self.report, rb, self.on, self.off)
        self.assertGreater(r["on_beat"]["share"][0] - r["on_beat"]["share"][1], 0.5)
        self.assertLess(r["parts"]["beats"], 0.5)

    def test_compare_without_audio_leaves_the_part_out_and_says_so(self):
        sil = shots.build(self.silent, dissolves=False)
        r = decon.compare(self.report, sil, self.on, self.silent)
        self.assertNotIn("beats", r["parts"])
        self.assertIn("not scored", decon.summary_compare(r))
        self.assertIn("no audio", r["on_beat"]["skipped"])

    def test_command_writes_the_map_and_table(self):
        sj, out = os.path.join(self.tmp.name, "shots.json"), os.path.join(self.tmp.name, "out.json")
        with open(sj, "w") as f:
            json.dump(self.report, f)
        ap = __import__("argparse").ArgumentParser()
        cmd_music_map.add_arguments(ap)
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(cmd_music_map.run(ap.parse_args([self.on, "--out", out, "--shots", sj])), 0)
        got = json.loads(Path(out).read_text())
        self.assertEqual(len(got["cuts"]), 8)
        self.assertIn("beats_s", got)

    def test_no_audio_is_an_error(self):
        with self.assertRaises(musicmap.MusicMapError):
            musicmap.build(self.silent)


if __name__ == "__main__":
    unittest.main()
