"""
Tests for rpresolve.shots on footage ffmpeg makes here from lavfi sources
(three different pictures joined at known frames, 160x90 at 10 fps, FFV1 in
Matroska so every frame is exact): cut times, the CSV qc --shots counts,
the TSV rhythm reads, the very scdet filter qc attaches, and the refusals.

Run: /usr/bin/python3 -m unittest production.tests.test_shots -v
"""

import csv
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import delivercheck as dc, paths, qc, shots  # noqa: E402

SIZE, RATE = "160x90", 10
PARTS = [f"testsrc2=s={SIZE}:r={RATE}:d=1.5", f"smptebars=s={SIZE}:r={RATE}:d=1",
         f"color=c=0x203060:s={SIZE}:r={RATE}:d=1.2"]
JOINS = [1.5, 2.5]


def make(out, parts, audio=False):
    n = len(parts)
    graph = ";".join(f"{p},format=yuv420p[v{i}]" for i, p in enumerate(parts))
    graph += ";" + "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
    cmd = [dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-filter_complex", graph,
           "-map", "[v]", "-c:v", "ffv1"]
    if audio:
        cmd = [dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
               "-i", "sine=f=440:r=48000:d=1", "-c:a", "pcm_s16le"]
    proc = subprocess.run(cmd + [out], stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)


class TestShots(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        d = cls._tmp.name
        cls.cut = os.path.join(d, "three.mkv")
        make(cls.cut, PARTS)
        cls.one = os.path.join(d, "one.mkv")
        make(cls.one, PARTS[:1])
        cls.wav = os.path.join(d, "tone.wav")
        make(cls.wav, [], audio=True)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_cuts_land_on_the_joins(self):
        r = shots.build(self.cut)
        self.assertEqual(len(r["cuts"]), 2)
        for got, want in zip(r["cuts"], JOINS):
            self.assertAlmostEqual(got, want, delta=0.5 / RATE)
        self.assertEqual([s["shot"] for s in r["shots"]], [1, 2, 3])
        self.assertEqual(r["shots"][0]["in"], 0.0)
        self.assertAlmostEqual(r["shots"][-1]["out"], 3.7, delta=0.11)
        self.assertAlmostEqual(sum(s["duration"] for s in r["shots"]), r["duration_s"], delta=0.002)
        self.assertEqual((r["threshold"], r["fps"]), (qc.THRESHOLDS["scdet_threshold"], 10.0))
        self.assertIn("3 shot(s), 2 cut(s)", shots.summary(r))

    def test_one_picture_is_one_shot_and_a_high_threshold_finds_none(self):
        self.assertEqual(len(shots.build(self.one)["shots"]), 1)
        self.assertEqual(shots.build(self.cut, threshold=100)["cuts"], [])

    def test_the_csv_is_what_qc_counts_and_the_tsv_what_rhythm_reads(self):
        r = shots.build(self.cut)
        csv_path = os.path.join(self._tmp.name, "out", "shots.csv")
        tsv_path = os.path.join(self._tmp.name, "out", "shots.tsv")
        shots.write(r, csv_path)
        shots.write(r, tsv_path)
        self.assertEqual(qc.shot_count(csv_path), 3)
        self.assertEqual(qc.shot_count(tsv_path), 3)
        with open(csv_path, newline="", encoding="utf-8") as f:
            self.assertEqual(next(csv.reader(f)), list(shots.COLUMNS))
        with open(tsv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f, delimiter="\t"))  # rhythm.from_tsv's reader
        durs = [float(x["out"]) - float(x["in"]) for x in rows]
        self.assertEqual(len(durs), 3)
        self.assertTrue(all(d > 0 for d in durs))

    def test_the_scdet_filter_is_the_one_qc_attaches(self):
        seen = []

        def fake_run(cmd, timeout):
            seen.append(cmd[cmd.index("-vf") + 1])
            return subprocess.CompletedProcess(cmd, 0, "", "")
        with mock.patch.object(dc, "_run", side_effect=fake_run):
            qc._detect_pass(self.cut, qc.THRESHOLDS, True, False)
        with mock.patch.object(dc, "_run", side_effect=fake_run), \
                mock.patch.object(dc, "probe", return_value={
                    "streams": [{"codec_type": "video", "avg_frame_rate": "10/1", "duration": "3"}],
                    "format": {"duration": "3"}}):
            shots.build(self.cut)
        qc_filter = [f for f in seen[0].split(",") if f.startswith("scdet")]
        self.assertEqual(qc_filter, [seen[1]])
        self.assertEqual(seen[1], shots.scdet_filter())

    def test_refusals(self):
        with self.assertRaises(dc.CheckError):
            shots.build(self.wav)
        with self.assertRaises(dc.CheckError):
            shots.build(os.path.join(self._tmp.name, "missing.mov"))
        repo = os.path.join(self._tmp.name, "repo")
        os.makedirs(os.path.join(repo, ".git"), exist_ok=True)
        with self.assertRaises(paths.OutputRefused):
            shots.write({"shots": []}, os.path.join(repo, "shots.csv"))

    def test_cli(self):
        out = os.path.join(self._tmp.name, "cli", "shots.tsv")
        so = io.StringIO()
        with redirect_stdout(so), redirect_stderr(io.StringIO()):
            self.assertEqual(shots.main([self.cut, "--out", out]), 0)
        self.assertIn("2 cut(s)", so.getvalue())
        self.assertEqual(qc.shot_count(out), 3)
        repo = os.path.join(self._tmp.name, "repo2")
        os.makedirs(os.path.join(repo, ".git"))
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            self.assertEqual(shots.main([self.cut, "--out", os.path.join(repo, "s.csv")]), 1)
        self.assertIn("git working tree", err.getvalue())
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            self.assertEqual(shots.main([self.wav, "--out", out]), 1)
        self.assertIn("no video stream", err.getvalue())


if __name__ == "__main__":
    unittest.main()
