"""
Offline tests for rpresolve.sync: FFT cross-correlation on generated
signals (noise bursts and chirps) with known offsets, negative offsets,
clock drift from a resampled copy, polarity, the refusal of unrelated
signals and of a pair that lines up differently in different places,
windows on short overlaps, frame conversion, and the whole measurement on
files ffmpeg writes (with an audio stream that starts late in its
container). Synthetic signals only. Skipped where numpy (or, for the
files, ffmpeg) is missing.

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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import numpy as np
    from rpresolve import sync
except ImportError:  # numpy missing on this interpreter
    np = None

import resolve_workflow  # noqa: E402

needs_numpy = unittest.skipIf(np is None, "numpy not installed")
RATE = 8000


def bursts(seconds, seed=1, rate=RATE):
    """Noise in bursts of random length and loudness with gaps between: a
    stand-in for speech that correlates at one lag only."""
    rng = np.random.default_rng(seed)
    n = int(seconds * rate)
    env = np.zeros(n)
    t = 0
    while t < n:
        length = int(rng.uniform(0.1, 0.6) * rate)
        env[t:t + length] = rng.uniform(0.2, 1.0)
        t += length + int(rng.uniform(0.05, 0.5) * rate)
    return rng.standard_normal(n) * env


def lowpass(x, cutoff, rate=RATE):
    """x with everything above cutoff Hz taken out (an FFT brick wall): band
    limited, as a real recording decoded at 8 kHz is, so a copy read between
    samples keeps its timing."""
    spec = np.fft.rfft(x)
    spec[np.fft.rfftfreq(len(x), 1.0 / rate) > cutoff] = 0
    return np.fft.irfft(spec, len(x))


def chirp(seconds, rate=RATE, f0=100.0, f1=3000.0):
    t = np.arange(int(seconds * rate)) / rate
    k = (f1 - f0) / seconds
    return np.sin(2 * np.pi * (f0 * t + 0.5 * k * t * t))


def noise(seconds, seed, level=0.05, rate=RATE):
    return level * np.random.default_rng(seed).standard_normal(int(seconds * rate))


@needs_numpy
class TestCorrelation(unittest.TestCase):
    def test_xcorr_lags_match_a_direct_sum(self):
        rng = np.random.default_rng(3)
        a, b = rng.standard_normal(40), rng.standard_normal(25)
        lags, c = sync.xcorr(a, b)
        self.assertEqual((lags[0], lags[-1]), (-24, 39))
        for L in (-24, -7, 0, 5, 39):
            want = sum(a[n] * b[n - L] for n in range(len(a)) if 0 <= n - L < len(b))
            self.assertAlmostEqual(c[list(lags).index(L)], want, places=9)

    def test_peak_finds_a_positive_lag_with_sub_sample_precision(self):
        src = bursts(20)
        other = src[1234:1234 + 8 * RATE]
        r = sync.peak(src, other, min_overlap=RATE)
        self.assertAlmostEqual(r["lag"], 1234, delta=0.05)
        self.assertGreater(r["ncc"], 0.99)
        self.assertEqual(r["polarity"], "normal")

    def test_inverted_polarity_is_found_and_named(self):
        src = bursts(20)
        r = sync.peak(src, -src[800:800 + 5 * RATE], min_overlap=RATE)
        self.assertAlmostEqual(r["lag"], 800, delta=0.05)
        self.assertLess(r["ncc"], -0.99)
        self.assertEqual(r["polarity"], "inverted")

    def test_lag_window_and_min_overlap_limit_the_search(self):
        src = bursts(10)
        self.assertIsNone(sync.peak(src, src, lag_min=5, lag_max=4))
        self.assertIsNone(sync.peak(src[:100], src[:100], min_overlap=101))
        self.assertIsNone(sync.peak([], src))

    def test_decimate_and_coarse_factor(self):
        self.assertEqual(list(sync.decimate(np.arange(10.0), 4)), [1.5, 5.5])
        self.assertEqual(sync.coarse_factor(1000, 1000), 8)
        # Two 1-hour files fit at 1 kHz; two 3-hour files go down to 250 Hz.
        self.assertEqual(sync.coarse_factor(3600 * RATE, 3600 * RATE), 8)
        self.assertEqual(sync.coarse_factor(3 * 3600 * RATE, 3 * 3600 * RATE), 32)


@needs_numpy
class TestMeasureSignals(unittest.TestCase):
    def check(self, ref, other, want_s, fps=25, **kw):
        r = sync.measure_signals(ref, other, RATE, fps=fps, **kw)
        self.assertTrue(r["match"], r["reasons"])
        self.assertAlmostEqual(r["offset_s"], want_s, delta=0.0005)
        return r

    def test_other_started_later_is_a_positive_offset(self):
        src = bursts(200)
        start = int(12.3456 * RATE)
        other = src[start:start + 150 * RATE] + noise(150, 2, 0.3)
        r = self.check(src + noise(200, 3), other, start / RATE)
        self.assertEqual([w["label"] for w in r["windows"]], ["head", "w2", "tail"])
        self.assertLess(abs(r["drift"]["ppm"]), 2)
        self.assertFalse(r["drift"]["exceeds"])
        self.assertEqual(r["frames"]["placed"], 309)  # 12.3455 s * 25 = 308.64
        self.assertAlmostEqual(r["frames"]["residual_ms"], -14.5, delta=0.1)
        self.assertEqual(r["frames"]["inherent_ms"], 20.0)

    def test_other_started_earlier_is_a_negative_offset(self):
        src = bursts(120, seed=4)
        ref = src[int(7.5 * RATE):]
        r = self.check(ref, src[:100 * RATE] + noise(100, 5), -7.5)
        self.assertEqual(r["frames"]["placed"], -187)  # -187.5 rounds half up
        self.assertEqual(r["overlap"]["start_s"], 0.0)

    def test_a_chirp_lines_up_too(self):
        src = chirp(20)
        self.check(src, src[3 * RATE:18 * RATE], 3.0, fps=None)

    def test_drift_from_a_resampled_copy(self):
        # The other recorder's clock runs 50 ppm slow: its copy of 600 s holds
        # 600 * (1 - 50e-6) s of sound, so the offset grows 3 ms per minute.
        src = lowpass(bursts(640, seed=6), 1500)
        d = 50e-6
        start = 20.0
        t_other = np.arange(int(600 * RATE)) / RATE
        # Sample m of the other file holds reference time start + m / RATE * (1 + d).
        t_ref = start + t_other * (1 + d)
        other = np.interp(t_ref * RATE, np.arange(len(src)), src)
        r = sync.measure_signals(src, other, RATE, fps=25)
        self.assertTrue(r["match"], r["reasons"])
        # At reference time t the offset is start + (t - start) * d / (1 + d).
        head = r["windows"][0]["center_s"]
        self.assertAlmostEqual(r["offset_s"], start + (head - start) * d / (1 + d), delta=0.0002)
        self.assertAlmostEqual(r["drift"]["ppm"], 50 / (1 + d), delta=1.0)
        self.assertAlmostEqual(r["drift"]["ms_per_min"], 3.0, delta=0.06)
        self.assertTrue(r["drift"]["exceeds"])  # 30 ms over 600 s is 0.75 frame at 25 fps
        self.assertAlmostEqual(r["drift"]["retime_pct"], 99.995, delta=0.0002)
        self.assertLess(r["drift"]["max_residual_ms"], 0.5)
        # The second pass stretched the other by the first pass's slope, so
        # every window correlates as well as a copy should.
        self.assertAlmostEqual(r["drift_compensated_ppm"], 50, delta=3)
        self.assertGreater(min(w["ncc"] for w in r["windows"]), 0.95)

    def test_a_fast_clock_is_measured_the_same_way(self):
        src = lowpass(bursts(400, seed=14), 1500)
        d = -150e-6
        t_ref = 5.0 + np.arange(int(380 * RATE)) / RATE * (1 + d)
        other = np.interp(t_ref * RATE, np.arange(len(src)), src)
        r = sync.measure_signals(src, other, RATE, fps=25)
        self.assertTrue(r["match"], r["reasons"])
        self.assertAlmostEqual(r["drift"]["ppm"], -150 / (1 + d), delta=1.0)
        self.assertGreater(r["drift"]["retime_pct"], 100)
        self.assertIn("measured again", sync.format_summary(
            {**r, "reference": {"path": "a"}, "other": {"path": "b"}}))

    def test_a_slope_beyond_two_clocks_is_refused(self):
        # 500 ppm lies on a clean line, but no two crystal clocks drift that far
        # apart; a 0.1% pull-down (1000 ppm) is the usual cause, and is named.
        src = lowpass(bursts(340, seed=80), 1500)
        t_ref = 20.0 + np.arange(300 * RATE) / RATE * (1 + 500e-6)
        other = np.interp(t_ref * RATE, np.arange(len(src)), src)
        r = sync.measure_signals(src, other, RATE, fps=25)
        self.assertFalse(r["match"])
        self.assertLess(r["drift"]["max_residual_ms"], 1.0)
        self.assertTrue(any("pull-down" in x for x in r["reasons"]), r["reasons"])

    def test_unrelated_signals_are_not_a_match(self):
        r = sync.measure_signals(bursts(90, seed=7), bursts(60, seed=8), RATE, fps=25)
        self.assertFalse(r["match"])
        self.assertTrue(any("peak only" in x or "normalized correlation" in x
                            for x in r["reasons"]), r["reasons"])

    def test_a_pair_that_lines_up_differently_in_places_is_refused(self):
        # A call recorded at both ends: the first half of the other file holds the
        # sound 180 ms later than the second half does.
        src = bursts(400, seed=9)
        a = src[10 * RATE:190 * RATE]
        b = src[int(190.18 * RATE):int(370.18 * RATE)]
        r = sync.measure_signals(src, np.concatenate([a, b]), RATE, fps=25)
        self.assertFalse(r["match"])
        self.assertTrue(any("the windows disagree" in x for x in r["reasons"]), r["reasons"])
        offsets = sorted(g["offset_s"] for g in r["groups"])
        self.assertAlmostEqual(offsets[0], 10.0, delta=0.001)
        self.assertAlmostEqual(offsets[1], 10.18, delta=0.001)

    def test_a_loop_heard_twice_in_the_reference_is_refused(self):
        # The same 20 s loop plays at 10 s and at 40 s of the reference; the other
        # holds one copy. Both offsets line up perfectly, so neither can be chosen.
        loop = bursts(20, seed=31)
        ref = noise(70, 32, 0.01)
        ref[10 * RATE:30 * RATE] += loop
        ref[40 * RATE:60 * RATE] += loop
        other = np.concatenate([noise(3, 33, 0.01), loop, noise(3, 34, 0.01)])
        r = sync.measure_signals(ref, other, RATE, fps=25)
        self.assertFalse(r["match"])
        self.assertTrue(any("the sound repeats" in x for x in r["reasons"]), r["reasons"])
        self.assertEqual(sorted([r["coarse"]["offset_s"], r["coarse"]["second_offset_s"]]),
                         [7.0, 37.0])
        self.assertTrue(r["rival"]["match"])

    def test_a_looping_music_bed_is_refused(self):
        # A 40 s bed loops through the whole reference; the other is 100 s of it
        # from 100 s, with noise. Every multiple of 40 s lines up as well.
        ref = np.tile(bursts(40, seed=35), 7)
        other = ref[100 * RATE:200 * RATE] + noise(100, 36, 0.05)
        r = sync.measure_signals(ref, other, RATE, fps=25)
        self.assertFalse(r["match"])
        self.assertTrue(any("the sound repeats" in x for x in r["reasons"]), r["reasons"])
        self.assertIn("ambiguous", sync.format_summary(
            {**r, "reference": {"path": "a"}, "other": {"path": "b"}}))

    def test_rumble_that_drowns_the_coarse_pass_still_matches(self):
        # Low-frequency noise (wind, handling) on both mics, different on each,
        # brings the coarse peak near its runner-up; measured, the runner-up
        # fails every window, and the match stands.
        src = bursts(300, seed=60)
        other = src[20 * RATE:280 * RATE] + lowpass(noise(260, 260, 7.0), 80)
        r = sync.measure_signals(src + lowpass(noise(300, 160, 7.0), 80), other, RATE, fps=25)
        self.assertLess(r["coarse"]["peak_ratio"], sync.COARSE_MIN_RATIO)
        self.assertFalse(r["rival"]["match"])
        self.assertTrue(r["match"], r["reasons"])
        self.assertAlmostEqual(r["offset_s"], 20.0, delta=0.0005)

    @staticmethod
    def dropout(ms, seed=70):
        """The reference, and 240 s of it from 20 s that lost `ms` of samples
        at 180 s (a USB or OBS audio dropout), on one clock."""
        src = lowpass(bursts(300, seed=seed), 1500)
        other = src[20 * RATE:260 * RATE]
        k, n = 180 * RATE, int(ms * RATE / 1000)
        return src, np.concatenate([other[:k], other[k + n:]])

    def test_dropped_samples_are_refused(self):
        # A line through three windows absorbs two thirds of a 40 ms step; the
        # third left over is still far more than one clock allows.
        src, other = self.dropout(40)
        r = sync.measure_signals(src, other, RATE, fps=25)
        self.assertFalse(r["match"])
        self.assertTrue(any("dropped samples" in x for x in r["reasons"]), r["reasons"])
        self.assertAlmostEqual(r["drift"]["max_residual_ms"], 40 / 3.0, delta=0.1)
        self.assertEqual(r["drift"]["residual_limit_ms"], 4.0)  # a tenth of a 25 fps frame
        offsets = sorted(g["offset_s"] for g in r["groups"])
        self.assertAlmostEqual(offsets[-1] - offsets[0], 0.040, delta=0.0005)

    def test_a_stretch_that_lowers_the_correlation_is_dropped(self):
        # A 10 ms step fits a line well enough to pass, and that line slopes by
        # 48 ppm. Stretching the other by it smears every window, so the first
        # pass stands: each window lines up as a clean copy does.
        src, other = self.dropout(10)
        r = sync.measure_signals(src, other, RATE, fps=25)
        self.assertTrue(r["match"], r["reasons"])
        self.assertGreater(min(w["ncc"] for w in r["windows"]), 0.99)
        self.assertAlmostEqual(r["windows"][0]["offset_s"], 20.0, delta=0.0001)
        self.assertAlmostEqual(r["stretch_dropped_ppm"], 47.6, delta=1)
        self.assertNotIn("drift_compensated_ppm", r)
        self.assertIn("first pass stands", sync.format_summary(
            {**r, "reference": {"path": "a"}, "other": {"path": "b"}}))

    def test_short_overlap_measures_one_window_and_no_drift(self):
        src = bursts(40, seed=10)
        r = self.check(src, src[5 * RATE:25 * RATE], 5.0)
        self.assertEqual([w["label"] for w in r["windows"]], ["whole"])
        self.assertIsNone(r["drift"])
        self.assertIn("drift not measured", r["drift_note"])

    def test_clips_shorter_than_a_window_still_line_up(self):
        src = bursts(12, seed=11)
        r = self.check(src, src[2 * RATE:9 * RATE], 2.0, window_s=30)
        self.assertEqual(r["windows"][0]["seconds"], 7.0)

    def test_an_overlap_under_the_minimum_is_refused(self):
        src = bursts(30, seed=12)
        r = sync.measure_signals(src, src[:3 * RATE], RATE, fps=25)
        self.assertFalse(r["match"])
        self.assertIn("overlap", " ".join(r["reasons"]))

    def test_windows_spread_over_a_long_overlap(self):
        w = sync.windows(0, 3600 * RATE, RATE, 30)
        self.assertEqual(len(w), sync.MAX_WINDOWS)
        self.assertEqual((w[0][0], w[-1][0]), ("head", "tail"))
        self.assertEqual((w[0][1], w[-1][2]), (0, 3600 * RATE))
        self.assertEqual(sync.windows(0, 89 * RATE, RATE, 30), [("whole", 0, 89 * RATE)])
        self.assertEqual([w[0] for w in sync.windows(0, 90 * RATE, RATE, 30)],
                         ["head", "w2", "tail"])

    def test_audio_start_in_the_container_moves_the_offset(self):
        src = bursts(60, seed=13)
        r = sync.measure_signals(src, src[4 * RATE:50 * RATE], RATE, fps=25, ref_start_s=0.0,
                                 other_start_s=0.5)
        self.assertAlmostEqual(r["offset_s"], 3.5, delta=0.0005)


@needs_numpy
class TestFrames(unittest.TestCase):
    def test_frames_round_half_up_and_report_the_residual(self):
        f = sync.frames(1.02, 25)
        self.assertEqual((f["placed"], f["exact"]), (26, 25.5))
        self.assertAlmostEqual(f["residual_ms"], -20.0)
        f = sync.frames(-0.3, 30)
        self.assertEqual(f["placed"], -9)
        self.assertAlmostEqual(f["inherent_ms"], 16.667, places=3)

    def test_drift_fit_and_residuals(self):
        ws = [{"center_s": 0.0, "offset_s": 1.0}, {"center_s": 60.0, "offset_s": 1.001},
              {"center_s": 120.0, "offset_s": 1.002}]
        d = sync.drift(ws, 120.0, fps=25)
        self.assertAlmostEqual(d["ms_per_min"], 1.0, places=6)
        self.assertAlmostEqual(d["ppm"], 16.67, places=2)
        self.assertEqual(d["max_residual_ms"], 0.0)
        self.assertFalse(d["exceeds"])


FFMPEG = "/opt/homebrew/bin/ffmpeg"


@needs_numpy
@unittest.skipUnless(os.access(FFMPEG, os.X_OK), "ffmpeg missing")
class TestFiles(unittest.TestCase):
    """The whole measurement on files ffmpeg writes from generated audio."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        src = bursts(80, seed=21, rate=48000) * 0.3
        cls.src_wav = os.path.join(d, "room.wav")
        cls.write_wav(cls.src_wav, src)
        # The camera: 60 s of the room from 0 s, with picture, its audio stream
        # starting 0.2 s into the container.
        cls.camera = os.path.join(d, "camera.mov")
        subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                        "color=c=gray:size=64x36:rate=25:duration=60", "-itsoffset", "0.2",
                        "-t", "59.8", "-i", cls.src_wav, "-map", "0:v", "-map", "1:a",
                        "-c:v", "mpeg4", "-c:a", "pcm_s16le", cls.camera], check=True)
        # The recorder: the room from 10 s to 70 s, quieter, as a stereo WAV.
        cls.recorder = os.path.join(d, "recorder.wav")
        subprocess.run([FFMPEG, "-v", "error", "-ss", "10", "-t", "60", "-i", cls.src_wav,
                        "-af", "volume=0.5", "-ac", "2", cls.recorder], check=True)
        cls.unrelated = os.path.join(d, "elsewhere.wav")
        cls.write_wav(cls.unrelated, bursts(40, seed=22, rate=48000) * 0.3)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @staticmethod
    def write_wav(path, x, rate=48000):
        import wave
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())

    def test_probe_reads_the_audio_start_after_the_first_frame(self):
        p = sync.probe(self.camera)
        self.assertEqual(p["video"]["fps"], 25.0)
        self.assertAlmostEqual(p["audio_start_s"], 0.2, delta=0.01)
        self.assertEqual(sync.probe(self.recorder)["audio"]["channels"], 2)
        with self.assertRaisesRegex(sync.SyncError, "audio stream"):
            sync.probe(self.camera, stream=1)

    def test_measure_the_pair(self):
        r = sync.measure(self.camera, self.recorder)
        self.assertTrue(r["match"], r["reasons"])
        # The recorder starts at room time 10 s; the camera's frame 0 shows room
        # time -0.2 s (its sound starts 0.2 s in), so 10.2 s on the camera's clock.
        self.assertAlmostEqual(r["offset_s"], 10.2, delta=0.002)
        self.assertEqual(r["fps"], 25.0)
        self.assertEqual(r["frames"]["placed"], 255)
        self.assertIn("MATCH", sync.format_summary(r))

    def test_cli_exit_codes(self):
        def run(*argv):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                status = resolve_workflow.cmd_sync_measure(argparse.Namespace(
                    reference=argv[0], other=argv[1], fps=None, window=30.0, ref_stream=0,
                    other_stream=0, json="--json" in argv))
            return status, out.getvalue(), err.getvalue()
        status, out, _ = run(self.camera, self.recorder)
        self.assertEqual(status, 0)
        self.assertIn("offset: +10.2", out)
        status, out, _ = run(self.camera, self.recorder, "--json")
        self.assertEqual(json.loads(out)["frames"]["placed"], 255)
        status, out, _ = run(self.camera, self.unrelated)
        self.assertEqual(status, 1)
        self.assertIn("NO MATCH", out)
        status, _, err = run(self.camera, os.path.join(self.tmp.name, "missing.wav"))
        self.assertEqual(status, 1)
        self.assertIn("not a file", err)

    def test_mcp_tool(self):
        from rpresolve.mcp import schema, server
        from rpresolve.mcp.registry import ToolContext
        tool = server.build_registry().get("sync_measure")
        self.assertTrue(tool.annotations["readOnlyHint"])
        args = schema.with_defaults(tool.input_schema, {"reference": self.camera,
                                                        "other": self.recorder})
        self.assertEqual(schema.validate(tool.input_schema, args), [])
        r = tool.handler(args, ToolContext())
        self.assertTrue(r["match"])
        self.assertAlmostEqual(r["offset_s"], 10.2, delta=0.002)
        json.dumps(r)  # the report is plain JSON
        with self.assertRaisesRegex(ValueError, "absolute"):
            tool.handler({**args, "other": "recorder.wav"}, ToolContext())


if __name__ == "__main__":
    unittest.main()
