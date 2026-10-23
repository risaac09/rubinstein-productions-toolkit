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

from rpresolve import delivercheck as dc, paths, qc  # noqa: E402

try:
    import numpy as np
    from rpresolve import shots  # noqa: E402
except ImportError:  # numpy missing on this interpreter
    np = None

needs_numpy = unittest.skipIf(np is None, "numpy not installed")  # the stdlib-only 3.9 CI job

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


@needs_numpy
@unittest.skipUnless(os.access(dc.FFMPEG, os.X_OK), "ffmpeg missing")
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
        with open(self.cut, "rb") as f:
            before = f.read()
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            self.assertEqual(shots.main([self.cut, "--out", self.cut]), 1)
        self.assertIn("is the source", err.getvalue())
        with open(self.cut, "rb") as f:
            self.assertEqual(f.read(), before)


def stack(plan, seed=0, noise=0.4):
    """Grey frames (n, 576) from a plan: ("hold", name, n), ("blend", from, to, n) or ("pan", name, n)."""
    rng = np.random.default_rng(seed)
    pics = {k: rng.integers(30, 220, shots.GREY_W * shots.GREY_H).astype(float) for k in "ABCD"}
    out = []
    for part in plan:
        if part[0] == "hold":
            out += [pics[part[1]]] * part[2]
        elif part[0] == "pan":
            out += [np.roll(pics[part[1]], 7 * i) for i in range(part[2])]
        else:  # the blend's first and last frames are the pictures themselves
            a, b, n = pics[part[1]], pics[part[2]], part[3]
            out += [(1 - t) * a + t * b for t in np.linspace(0, 1, n)]
    f = np.array(out)
    return f + rng.normal(0, noise, f.shape)


@needs_numpy
class TestFindDissolves(unittest.TestCase):
    def test_lengths_found_with_their_frames(self):
        for n in (5, 8, 15, 30, 60):
            f = stack([("hold", "A", 20), ("blend", "A", "B", n), ("hold", "B", 20)])
            got = shots.find_dissolves(f, 25)
            self.assertEqual(len(got), 1, n)
            self.assertAlmostEqual(got[0]["start_frame"], 19, delta=1, msg=n)
            self.assertAlmostEqual(got[0]["end_frame"], 19 + n - 1, delta=1, msg=n)

    def test_hard_cuts_are_not_dissolves(self):
        f = stack([("hold", "A", 20), ("hold", "B", 20), ("hold", "C", 20)])
        self.assertEqual(shots.find_dissolves(f, 25), [])

    def test_a_cut_beside_a_dissolve_is_not_swallowed(self):
        f = stack([("hold", "A", 20), ("hold", "B", 15), ("blend", "B", "C", 12), ("hold", "C", 20),
                   ("hold", "D", 20)])
        got = shots.find_dissolves(f, 25)
        self.assertEqual(len(got), 1)
        self.assertAlmostEqual(got[0]["start_frame"], 34, delta=1)
        self.assertAlmostEqual(got[0]["end_frame"], 45, delta=1)

    def test_motion_is_not_a_dissolve(self):
        self.assertEqual(shots.find_dissolves(stack([("pan", "A", 60), ("hold", "B", 20)]), 25), [])

    def test_two_dissolves_in_a_row(self):
        f = stack([("hold", "A", 15), ("blend", "A", "B", 10), ("hold", "B", 15), ("blend", "B", "C", 20),
                   ("hold", "C", 15)])
        self.assertEqual(len(shots.find_dissolves(f, 25)), 2)


@needs_numpy
class TestDissolveFootage(unittest.TestCase):
    """The same through ffmpeg: xfade between two still charts (160x90 at 10 fps)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.files = {}
        for dur in (0.5, 1.0, 2.0):
            out = os.path.join(cls._tmp.name, f"x{dur}.mkv")
            graph = (f"smptebars=s={SIZE}:r={RATE}:d=4,format=yuv420p[a];"
                     f"pal100bars=s={SIZE}:r={RATE}:d=4,format=yuv420p[b];"
                     f"[a][b]xfade=transition=fade:duration={dur}:offset=1.5[v]")
            proc = subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-filter_complex",
                                   graph, "-map", "[v]", "-c:v", "ffv1", out], stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr)
            cls.files[dur] = out
        cls.cut = os.path.join(cls._tmp.name, "cut.mkv")
        make(cls.cut, PARTS)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_dissolves_of_three_lengths(self):
        for dur, path in self.files.items():
            r = shots.build(path)
            self.assertEqual(len(r["dissolves"]), 1, dur)
            d = r["dissolves"][0]
            self.assertAlmostEqual(d["start"], 1.5, delta=0.25, msg=dur)
            self.assertAlmostEqual(d["end"], 1.5 + dur, delta=0.25, msg=dur)
            self.assertEqual(r["cuts"], [], dur)  # a cut scdet caught inside it is the dissolve
            self.assertEqual([s["into"] for s in r["shots"]], ["start", "dissolve"])
            self.assertIn("1 dissolve(s)", shots.summary(r))

    def test_hard_cuts_stay_cuts(self):
        r = shots.build(self.cut)
        self.assertEqual((len(r["cuts"]), r["dissolves"]), (2, []))
        self.assertEqual([s["into"] for s in r["shots"]], ["start", "cut", "cut"])
        self.assertEqual(len(shots.build(self.cut, dissolves=False)["cuts"]), 2)

    def test_the_table_names_the_blend(self):
        r = shots.build(self.files[1.0])
        rows = shots.table(r).splitlines()
        self.assertEqual(rows[0], ",".join(shots.COLUMNS))
        self.assertEqual(rows[2].split(",")[4], "dissolve")
        self.assertEqual(float(rows[2].split(",")[5]), r["dissolves"][0]["start"])


if __name__ == "__main__":
    unittest.main()
