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
import shutil
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

    def test_find_bursts(self):
        def frames(steps):   # grey frames whose mean step from one to the next is each value in steps
            f = [np.zeros(16, np.float32)]
            for v in steps:
                f.append(f[-1] + (v if len(f) % 2 else -v))
            return np.array(f)
        quiet = [0.5] * 6
        self.assertEqual(shots.find_bursts(frames(quiet + [30] * 5 + quiet), 25), [(6, 11)])
        self.assertEqual(shots.find_bursts(frames(quiet + [30, 30, 2, 30, 30] + quiet), 25), [])  # a still frame inside
        self.assertEqual(shots.find_bursts(frames([20] * 30), 25), [])          # steady motion is no burst
        self.assertEqual(shots.find_bursts(frames(quiet + [30] * 12 + quiet), 25), [])  # too long for a transition
        self.assertEqual(shots.find_bursts(None, 25), [])

    def test_bursts_are_cuts_in_an_adaptive_build_only(self):
        S = f"s={SIZE}:r={RATE}"
        graph = (f"smptebars={S}:d=2,format=yuv420p[a];color=c=0x2060a0:{S}:d=2,format=yuv420p[b];"
                 "[a][b]xfade=transition=slideleft:duration=0.5:offset=1.5[v]")
        path = os.path.join(self._tmp.name, "slide.mkv")
        proc = subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-filter_complex", graph,
                               "-map", "[v]", "-c:v", "ffv1", path], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        from unittest import mock
        self.assertEqual(shots.build(path)["cuts"], [1.6])   # scdet takes this slide as a cut
        # a burst in clear space becomes a cut at its middle; one on the existing cut adds nothing
        # (13, 17) holds the cut at 1.6 s and (17, 20) starts 2 frames after it: both are that transition
        with mock.patch.object(shots, "find_bursts", return_value=[(5, 9), (13, 17), (17, 20)]):
            r = shots.build(path, adaptive=True)
            self.assertEqual(r["bursts"], [0.8])
            self.assertEqual(r["cuts"], [0.8, 1.6])
            self.assertEqual([x["into"] for x in r["shots"]], ["start", "cut", "cut"])
            self.assertEqual(shots.build(path)["bursts"], [])   # not adaptive: not read

    def test_a_slow_dip_spans_its_whole_fade(self):
        # 10 fps: A 0-19, fade out over frames 12-19 (8 frames, more than RAMP_FRAMES), 3 black,
        # B fades in over 8 frames: the span is the whole fall and rise, as the dissolve pass sees it
        S = f"s={SIZE}:r={RATE}"
        parts = [f"testsrc2={S}:d=2,fade=t=out:st=1.2:d=0.8", f"color=c=black:{S}:d=0.3",
                 f"smptebars={S}:d=2,fade=t=in:st=0:d=0.8"]
        path = os.path.join(self._tmp.name, "slowdip.mkv")
        make(path, parts)
        r = shots.build(path)
        self.assertEqual(len(r["dips"]), 1, r["dips"])
        self.assertAlmostEqual(r["dips"][0]["start"], 1.2, delta=1.5 / RATE)
        self.assertAlmostEqual(r["dips"][0]["end"], 3.1, delta=1.5 / RATE)
        self.assertEqual([s["into"] for s in r["shots"]], ["start", "dip"])

    def test_a_dip_and_a_flash_are_found_and_plain_files_have_none(self):
        # frames at 10 fps: A 0-14, cut, B 15-29 with a 2-frame fade out, 2 black, C 34-49 after a
        # 2-frame fade in (the dip), then D 63-77 with one white frame at 63
        S = f"s={SIZE}:r={RATE}"
        parts = [f"testsrc2={S}:d=1.5", f"smptebars={S}:d=1.5,fade=t=out:st=1.3:d=0.2",
                 f"color=c=black:{S}:d=0.2", f"color=c=0x6090c0:{S}:d=0.4,fade=t=in:st=0:d=0.2",
                 f"color=c=0x6090c0:{S}:d=1.2", f"color=c=0x404040:{S}:d=1.5",
                 f"color=c=white:{S}:d=0.1", f"color=c=0x404040:{S}:d=1.4"]
        path = os.path.join(self._tmp.name, "dip.mkv")
        make(path, parts)
        r = shots.build(path)
        self.assertEqual(len(r["dips"]), 1)
        d = r["dips"][0]
        self.assertAlmostEqual(d["start_frame"], 28, delta=1)
        self.assertAlmostEqual(d["end_frame"], 34, delta=1)
        self.assertEqual([s["into"] for s in r["shots"]][:3], ["start", "cut", "dip"])
        self.assertAlmostEqual(r["shots"][2]["blend_in"], d["start"])
        self.assertAlmostEqual(r["cuts"][0], 1.5, delta=1 / RATE)
        self.assertEqual(len(r["flashes"]), 1)
        self.assertAlmostEqual(r["flashes"][0], 6.3, delta=1 / RATE)
        for plain in (self.cut, self.one):
            q = shots.build(plain)
            self.assertEqual((q["dips"], q["flashes"]), ([], []))
        self.assertIn("1 dip(s)", shots.summary(r))

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

    def dim_clip(self, d):
        clip = os.path.join(d, "dim.mkv")
        make(clip, [f"{p},hue=s=0,eq=brightness=-0.35:contrast=0.3" for p in PARTS])  # scdet scores 6-7, under 10
        return clip

    def test_cuts_in_dark_black_and_white_footage_are_found_when_adaptive(self):
        with tempfile.TemporaryDirectory() as d:
            r = shots.build(self.dim_clip(d), dissolves=False, adaptive=True)
        self.assertEqual(r["cuts"], [1.5, 2.5])
        self.assertTrue(r["low_contrast"] and r["black_and_white"])
        self.assertLess(r["threshold_used"], r["threshold"])

    def test_the_default_and_an_explicit_threshold_are_never_scaled(self):
        with tempfile.TemporaryDirectory() as d:
            clip = self.dim_clip(d)
            plain = shots.build(clip, dissolves=False)
            fixed = shots.build(clip, threshold=10, dissolves=False, adaptive=True)
        for r in (plain, fixed):
            self.assertEqual((r["threshold_used"], r["cuts"], r["low_contrast"]), (10, [], False))

    def test_a_long_black_head_does_not_drag_the_threshold_down(self):
        # 10 s of black, then 6 s of handheld motion whose frame scores sit between 4 and 6.5, under 10
        with tempfile.TemporaryDirectory() as d:
            clip = os.path.join(d, "head.mkv")
            make(clip, ["color=c=black:s=640x360:r=24:d=10",
                        "testsrc2=s=1280x720:r=24:d=6,crop=640:360:x='320+200*sin(9*t)':y='180+120*sin(13*t)'"])
            r = shots.build(clip, dissolves=False, adaptive=True)
        self.assertGreater(r["contrast"], 15)
        self.assertEqual(r["threshold_used"], r["threshold"])
        self.assertLessEqual(len(r["cuts"]), 2)

    def test_a_weak_cut_in_the_first_frames_is_an_encoder_artefact_when_adaptive(self):
        info = {"streams": [{"codec_type": "video", "avg_frame_rate": "10/1"}], "format": {"duration": "10"}}
        scores = {0.1: 12.0, 5.0: 30.0}  # the head one scores 10 to 20, as an artefact does

        def parse(text, t):
            return [at for at, sc in scores.items() if sc >= t]
        with tempfile.NamedTemporaryFile(suffix=".mkv") as f, \
                mock.patch.object(shots.dc, "check_tools"), mock.patch.object(shots.dc, "probe", return_value=info), \
                mock.patch.object(shots.dc, "duration", return_value=10.0), \
                mock.patch.object(shots, "look", return_value=(10.0, 50.0)), \
                mock.patch.object(shots.dc, "_run", return_value=mock.Mock(returncode=0, stderr="")), \
                mock.patch.object(shots.qc, "parse_scdet", side_effect=parse):
            self.assertEqual(shots.build(f.name, dissolves=False, adaptive=True)["cuts"], [5.0])
            self.assertEqual(shots.build(f.name, dissolves=False)["cuts"], [0.1, 5.0])

    def test_a_hard_cut_in_the_first_frames_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            clip = os.path.join(d, "head.mkv")
            make(clip, [f"color=c=black:s={SIZE}:r={RATE}:d=0.2", f"testsrc2=s={SIZE}:r={RATE}:d=1"])
            self.assertEqual(shots.build(clip, dissolves=False, adaptive=True)["cuts"], [0.2])

    def test_a_failed_sampling_run_is_an_error_not_a_reading(self):
        bad = mock.Mock(returncode=1, stdout=b"", stderr=b"No such filter")
        with mock.patch.object(shots.subprocess, "run", return_value=bad), self.assertRaises(dc.CheckError):
            shots.look(self.cut)

    def test_a_file_too_short_to_sample_has_no_contrast_reading(self):
        with tempfile.TemporaryDirectory() as d:
            clip = os.path.join(d, "tiny.mkv")
            make(clip, [f"testsrc2=s={SIZE}:r={RATE}:d=0.2"])
            r = shots.build(clip, dissolves=False, adaptive=True)
        self.assertEqual((r["contrast"], r["low_contrast"], r["threshold_used"]), (None, False, r["threshold"]))

    def test_look_timeout_is_a_check_error(self):
        with mock.patch.object(shots.subprocess, "run", side_effect=subprocess.TimeoutExpired("ffmpeg", 1)):
            with self.assertRaises(dc.CheckError):
                shots.look(self.cut)

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
class TestBudget(unittest.TestCase):
    def test_a_file_over_the_budget_is_refused_naming_the_limit_and_the_way_out(self):
        proc = mock.Mock(returncode=0, stderr="")
        with mock.patch.object(dc, "check_tools"), mock.patch("os.path.isfile", return_value=True), \
                mock.patch.object(dc, "probe", return_value={"streams": [{"codec_type": "video", "avg_frame_rate": "25/1"}]}), \
                mock.patch.object(dc, "duration", return_value=3 * 3600.0), mock.patch.object(dc, "_run", return_value=proc), \
                mock.patch.object(shots, "grey_frames", side_effect=AssertionError("decoded")):
            with self.assertRaises(dc.CheckError) as cm:
                shots.build("long.mov")
        self.assertIn("1 h", str(cm.exception))
        self.assertIn("--no-dissolves", str(cm.exception))


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


SCENE_LIST = (
    "Timecode List:,00:00:01.500,00:00:02.500\n"
    "Scene Number,Start Frame,Start Timecode,Start Time (seconds),End Frame,End Timecode,End Time (seconds),"
    "Length (frames),Length (timecode),Length (seconds)\n"
    "1,1,00:00:00.000,0.000,15,00:00:01.500,1.500,15,00:00:01.500,1.500\n"
    "2,16,00:00:01.500,1.500,25,00:00:02.500,2.500,10,00:00:01.000,1.000\n"
    "3,26,00:00:02.500,2.500,37,00:00:03.700,3.700,12,00:00:01.200,1.200\n")


@needs_numpy
class TestPySceneDetectBackend(unittest.TestCase):
    def test_the_scene_list_gives_the_starts_after_the_first(self):
        self.assertEqual(shots.parse_scene_list(SCENE_LIST), [1.5, 2.5])
        self.assertEqual(shots.parse_scene_list(SCENE_LIST.split("\n", 1)[1]), [1.5, 2.5])  # --skip-cuts form

    def test_a_list_without_its_header_is_an_error(self):
        with self.assertRaises(dc.CheckError):
            shots.parse_scene_list("1,1,00:00:00.000,0.000\n")
        with self.assertRaises(dc.CheckError):
            shots.parse_scene_list("Scene Number,Start\n1,0\n2,1.5\n")

    def test_missing_program_names_the_install_and_the_way_out(self):
        with mock.patch.object(shots.shutil, "which", return_value=None):
            with self.assertRaises(dc.ToolMissing) as cm:
                shots.scenedetect_exe()
        self.assertIn("uv tool install", str(cm.exception))
        self.assertIn("--backend scdet", str(cm.exception))

    def test_an_unknown_backend_is_refused(self):
        with tempfile.NamedTemporaryFile(suffix=".mkv") as f, \
                mock.patch.object(shots.dc, "check_tools"), \
                mock.patch.object(shots.dc, "probe", return_value={"streams": [{"codec_type": "video", "avg_frame_rate": "10/1"}],
                                                                    "format": {"duration": "3.7"}}):
            with self.assertRaises(ValueError):
                shots.build(f.name, backend="nope", dissolves=False)

    @unittest.skipUnless(os.access(dc.FFMPEG, os.X_OK), "ffmpeg missing")
    @unittest.skipUnless(shutil.which("scenedetect"), "scenedetect not installed")
    def test_the_program_finds_the_joins(self):
        with tempfile.TemporaryDirectory() as d:
            clip = os.path.join(d, "three.mkv")
            make(clip, PARTS)
            report = shots.build(clip, backend="pyscenedetect")
        self.assertEqual(report["backend"], "pyscenedetect")
        self.assertEqual(len(report["cuts"]), len(JOINS))
        for got, want in zip(report["cuts"], JOINS):
            self.assertAlmostEqual(got, want, delta=1 / RATE)
        self.assertIn("by PySceneDetect", shots.summary(report))


@needs_numpy
class TestCutOutranksDissolve(unittest.TestCase):
    def build_with(self, backend, cuts, dissolves):
        info = {"streams": [{"codec_type": "video", "avg_frame_rate": "10/1"}], "format": {"duration": "10"}}
        diss = [{"start_frame": int(a * 10), "end_frame": int(b * 10), "start": a, "end": b, "duration": b - a}
                for a, b in dissolves]
        with tempfile.NamedTemporaryFile(suffix=".mkv") as f, \
                mock.patch.object(shots.dc, "check_tools"), mock.patch.object(shots.dc, "probe", return_value=info), \
                mock.patch.object(shots.dc, "duration", return_value=10.0), \
                mock.patch.object(shots, "pyscenedetect_cuts", return_value=cuts), \
                mock.patch.object(shots.dc, "_run", return_value=mock.Mock(returncode=0, stderr="")), \
                mock.patch.object(shots.qc, "parse_scdet", return_value=cuts), \
                mock.patch.object(shots, "grey_frames", return_value=None), \
                mock.patch.object(shots, "find_dissolves", return_value=diss):
            return shots.build(f.name, backend=backend)

    def test_pyscenedetect_keeps_its_cut_and_drops_the_dissolve_around_it(self):
        r = self.build_with("pyscenedetect", [4.0, 7.0], [(3.8, 4.3), (5.0, 5.6)])
        self.assertEqual(r["cuts"], [4.0, 7.0])
        self.assertEqual([(d["start"], d["end"]) for d in r["dissolves"]], [(5.0, 5.6)])

    def test_scdet_lets_the_dissolve_take_its_cut(self):
        r = self.build_with("scdet", [4.0, 7.0], [(3.8, 4.3), (5.0, 5.6)])
        self.assertEqual(r["cuts"], [7.0])
        self.assertEqual(len(r["dissolves"]), 2)
