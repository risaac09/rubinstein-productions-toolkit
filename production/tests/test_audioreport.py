"""
Tests for rpresolve.audioreport: the sample counts on generated arrays
(numpy only), then whole reports on files ffmpeg writes from lavfi sine,
noise and silence.
"""

import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import numpy as np
    from rpresolve import audioreport as ar
    from rpresolve import delivercheck as dc
    from rpresolve.paths import OutputRefused
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

needs_numpy = unittest.skipUnless(HAVE_NUMPY, "needs numpy")
RATE = 48000
FFMPEG = os.environ.get("RPRESOLVE_FFMPEG", "/opt/homebrew/bin/ffmpeg")
SCRIPT = str(Path(__file__).resolve().parent.parent / "resolve_workflow.py")


def sine(seconds, freq=440.0, amp=0.25, rate=RATE):
    t = np.arange(int(seconds * rate)) / rate
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def stats(x, rate=RATE, t0=0.0, blocks=1):
    """_Stats over x (mono or (n, ch)), fed in `blocks` reads that start on
    window edges, as samples() reads them."""
    x = x.reshape(len(x), -1)
    st = ar._Stats(rate, x.shape[1], t0)
    windows = -(-len(x) // st.win)
    for part in np.array_split(np.arange(windows), blocks):
        st.add(x[part[0] * st.win:(part[-1] + 1) * st.win])
    return st.result(-50.0, 0.5)


class TestParsers(unittest.TestCase):
    @needs_numpy
    def test_parse_time(self):
        self.assertEqual(ar.parse_time("75"), 75.0)
        self.assertEqual(ar.parse_time("1:15"), 75.0)
        self.assertEqual(ar.parse_time("0:01:15.5"), 75.5)
        for bad in ("", "1::2", "a", "1:2:3:4", "-5"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ar.parse_time(bad)

    @needs_numpy
    def test_parse_framelog(self):
        text = ("[Parsed_ebur128_0 @ 0x1] t: 0.0999792  TARGET:-23 LUFS    M:-120.7 S:-120.7"
                "     I: -70.0 LUFS       LRA:   0.0 LU  FTPK: -38.1 dBFS  TPK: -38.1 dBFS\n"
                "[Parsed_ebur128_0 @ 0x1] t: 1.49998    TARGET:-23 LUFS    M: -41.1 S: -42.0"
                "     I: -41.1 LUFS       LRA:   0.0 LU\n")
        self.assertEqual(ar.parse_framelog(text),
                         [(0.0999792, -120.7, -120.7), (1.49998, -41.1, -42.0)])

    @needs_numpy
    def test_runs(self):
        s, e = ar._runs(np.array([1, 1, 0, 1, 0, 0, 1, 1, 1], bool))
        self.assertEqual((s.tolist(), e.tolist()), ([0, 3, 6], [2, 4, 9]))


@needs_numpy
class TestSampleCounts(unittest.TestCase):
    def test_a_clean_sine(self):
        r = stats(sine(3))
        c = r["channels"][0]
        self.assertAlmostEqual(c["peak_dbfs"], -12.04, delta=0.02)
        self.assertAlmostEqual(c["crest_db"], 3.01, delta=0.05)
        self.assertAlmostEqual(c["dc_offset"], 0.0, delta=1e-4)
        self.assertEqual((c["clip_runs"], c["full_scale_samples"]), (0, 0))
        self.assertEqual((r["clicks"], r["clips"], r["silences"]), ([], [], []))

    def test_clicks_are_found_and_timed(self):
        x = sine(3)
        for i in (24100, 72100, 120100):
            x[i] += 0.3
        r = stats(x)
        self.assertEqual(len(r["clicks"]), 3)
        for got, want in zip(r["clicks"], (24100 / RATE, 72100 / RATE, 120100 / RATE)):
            self.assertAlmostEqual(got, want, delta=0.001)

    def test_noise_is_not_clicks(self):
        x = np.random.default_rng(5).normal(0, 0.1, RATE * 3).astype(np.float32)
        self.assertEqual(stats(x)["clicks"], [])

    def test_digital_silence_is_not_clicks(self):
        self.assertEqual(stats(np.zeros(RATE * 2, np.float32))["clicks"], [])

    def test_clipping_at_full_scale(self):
        x = np.clip(sine(2, amp=1.5), -1, 1)
        r = stats(np.stack([x, x], axis=1))
        for c in r["channels"]:
            self.assertGreater(c["clip_runs"], 800)  # two flat tops per cycle at 440 Hz
            self.assertGreater(c["full_scale_samples"], 0)
        self.assertTrue(r["clips"])

    def test_clipping_turned_down_afterwards_still_counts(self):
        x = np.clip(sine(2, amp=1.5), -1, 1) * 0.5
        c = stats(x)["channels"][0]
        self.assertGreater(c["clip_runs"], 800)
        self.assertEqual(c["full_scale_samples"], 0)

    def test_a_held_level_below_a_later_peak_is_not_clipping(self):
        # Flat tops at 0.5 for a second, then one louder clean peak: the flat tops
        # were not at the channel's peak, so they are not clips.
        x = np.concatenate([np.clip(sine(1, amp=1.0), -0.5, 0.5), sine(1, amp=0.9)])
        self.assertEqual(stats(x, blocks=2)["channels"][0]["clip_runs"], 0)

    def test_silence_runs_on_the_file_clock(self):
        x = np.concatenate([sine(1), np.zeros(RATE, np.float32), sine(1)])
        r = stats(x, t0=10.0)
        self.assertEqual(r["silences"], [[11.0, 12.0]])
        self.assertAlmostEqual(r["noise_floor_dbfs"], -15.1, delta=0.2)  # digital silence left out

    def test_a_short_gap_is_not_a_silence(self):
        x = np.concatenate([sine(1), np.zeros(RATE // 5, np.float32), sine(1)])
        self.assertEqual(stats(x)["silences"], [])

    def test_octave_share(self):
        bands = stats(sine(2, freq=1000))["octave_bands_db"]
        self.assertGreater(bands["1000"], -0.5)
        self.assertLess(bands["125"], -40)

    def test_dropouts_are_digital_silence_inside_the_range(self):
        z = np.zeros(RATE, np.float32)
        r = stats(np.concatenate([z, sine(1), z, sine(1), np.full(RATE, 1e-4, np.float32), sine(1), z]))
        # The head and the tail are not dropouts; quiet room tone is a silence, not a dropout.
        self.assertEqual(r["dropouts"], [[2.0, 3.0]])
        self.assertEqual(r["silences"], [[0.0, 1.0], [2.0, 3.0], [4.0, 5.0], [6.0, 7.0]])

    def test_stereo(self):
        x, n = sine(1), np.random.default_rng(3).normal(0, 0.1, (RATE, 2)).astype(np.float32)
        self.assertIsNone(stats(x)["stereo"])
        self.assertEqual(stats(np.stack([x, x], 1))["stereo"], {"correlation": 1.0, "balance_db": 0.0})
        self.assertEqual(stats(np.stack([x, -x], 1))["stereo"]["correlation"], -1.0)
        self.assertAlmostEqual(stats(n)["stereo"]["correlation"], 0.0, delta=0.02)
        self.assertAlmostEqual(stats(np.stack([x, x * 0.5], 1))["stereo"]["balance_db"], 6.0,
                               delta=0.05)

    def test_reads_in_blocks_agree_with_one_read(self):
        x = np.concatenate([sine(2), np.zeros(RATE, np.float32), sine(2)])
        x[30100] += 0.3
        one, many = stats(x), stats(x, blocks=3)
        self.assertEqual(one["clicks"], many["clicks"])
        self.assertEqual(one["silences"], many["silences"])
        self.assertEqual(one["channels"][0]["peak_dbfs"], many["channels"][0]["peak_dbfs"])


@needs_numpy
class TestFlagsAndCompare(unittest.TestCase):
    def report(self, **kw):
        r = {"channels": [{"clip_runs": 0, "dc_offset": 0.0, "crest_db": 10.0, "rms_dbfs": -20.0},
                          {"clip_runs": 0, "dc_offset": 0.0, "crest_db": 10.0, "rms_dbfs": -21.0}],
             "stereo": {"correlation": 0.4, "balance_db": 1.0}, "clips": [], "dropouts": [],
             "true_peak_dbtp": -2.0, "integrated_lufs": -16.0, "clicks": [],
             "octave_bands_db": {"1000": -3.0, "4000": -10.0}, "lra_lu": 6.0,
             "max_short_term_lufs": -12.0, "noise_floor_dbfs": -60.0, "spread_db": 40.0}
        r.update(kw)
        return r

    def test_clean_has_no_flags(self):
        self.assertEqual(ar.flags(self.report(), dict(ar.DEFAULTS, target_lufs=-16.0)), [])

    def test_each_flag(self):
        r = self.report(channels=[{"clip_runs": 5, "dc_offset": 0.02, "crest_db": 9.0,
                                   "rms_dbfs": -20.0},
                                  {"clip_runs": 0, "dc_offset": 0.0, "crest_db": None,
                                   "rms_dbfs": -60.0}],
                        clips=[0.5, 0.75, 1.0, 1.25], dropouts=[[3.0, 4.0]], true_peak_dbtp=-0.4,
                        integrated_lufs=-19.0, clicks=[1.25], stereo={"correlation": -0.5,
                                                                      "balance_db": 40.0})
        got = {f["flag"]: f for f in ar.flags(r, dict(ar.DEFAULTS, target_lufs=-16.0))}
        self.assertEqual(list(got), ["clipping", "dropout", "clicks", "true_peak", "dead_channel",
                                     "phase", "dc_offset", "loudness"])
        self.assertEqual(got["clipping"]["at_s"], [0.5, 1.0])  # listed FLAG_GAP_S apart
        self.assertEqual(got["dropout"]["at_s"], [3.0])
        self.assertEqual(got["clicks"]["at_s"], [1.25])
        self.assertIn("channel 2 sits 40 dB", got["dead_channel"]["note"])
        self.assertEqual((got["loudness"]["value"], got["loudness"]["threshold"]), (-19.0, -16.0))
        self.assertIn("-3.0 LU off the -16.0 target", got["loudness"]["note"])

    def test_dual_mono(self):
        r = self.report(stereo={"correlation": 1.0, "balance_db": 0.0})
        self.assertEqual([f["flag"] for f in ar.flags(r, dict(ar.DEFAULTS, target_lufs=None))],
                         ["dual_mono"])

    def test_no_target_no_loudness_flag(self):
        self.assertEqual(ar.flags(self.report(integrated_lufs=-30.0),
                                  dict(ar.DEFAULTS, target_lufs=None)), [])

    def test_compare_is_this_minus_the_reference(self):
        ref = self.report(integrated_lufs=-14.0,
                          octave_bands_db={"1000": -4.0, "4000": None})
        d = ar.compare(self.report(), ref)
        self.assertEqual(d["integrated_lufs"], -2.0)
        self.assertEqual(d["crest_db"], 0.0)
        self.assertEqual(d["octave_bands_db"], {"1000": 1.0, "4000": None})


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", head[:8]
    return struct.unpack(">II", head[16:24])


@needs_numpy
@unittest.skipUnless(os.access(FFMPEG, os.X_OK), "ffmpeg missing")
class TestReports(unittest.TestCase):
    """Whole reports on files ffmpeg writes from generated audio."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        # 5 s of 1 kHz, 2 s of digital silence, 3 s of pink noise; 16-bit stereo.
        cls.fx = os.path.join(d, "fixture.wav")
        subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "sine=f=1000:r=48000:d=5",
                        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=2",
                        "-f", "lavfi", "-i", "anoisesrc=r=48000:c=pink:a=0.1:d=3:seed=7",
                        "-filter_complex",
                        "[0][1][2]concat=n=3:v=0:a=1,aformat=s16:channel_layouts=stereo",
                        cls.fx], check=True)
        cls.quiet = os.path.join(d, "quiet.wav")
        subprocess.run([FFMPEG, "-v", "error", "-i", cls.fx, "-af", "volume=0.5", "-c:a",
                        "pcm_s24le", cls.quiet], check=True)
        # Planted faults in mono 16-bit: a burst clipped at full scale at 1.0 s, a
        # click at 2.5 s, a second of digital silence from 3.0 s.
        x = np.concatenate([sine(1, amp=0.1), sine(0.5, amp=2.0), sine(1.5, amp=0.1),
                            np.zeros(RATE, np.float32), sine(1, amp=0.1)])
        x[int(2.5 * RATE)] += 0.4
        cls.faults = os.path.join(d, "faults.wav")
        import wave
        with wave.open(cls.faults, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
        cls.video = os.path.join(d, "picture-only.mov")
        subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                        "color=c=gray:size=64x36:rate=25:duration=1", "-c:v", "mpeg4",
                        cls.video], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def out(self, name):
        return os.path.join(self.tmp.name, name)

    def test_the_whole_file(self):
        r = ar.report(self.fx, self.out("whole"))
        self.assertEqual((r["sample_rate"], len(r["channels"]), r["codec"]),
                         (48000, 2, "pcm_s16le"))
        want = dc.measure_loudness(self.fx)
        self.assertEqual(r["integrated_lufs"], want["integrated_lufs"])
        self.assertEqual(r["true_peak_dbtp"], want["true_peak_dbtp"])
        self.assertEqual(r["silences"], [[5.0, 7.0]])
        self.assertEqual(r["clicks"], [])
        self.assertEqual(r["dropouts"], [[5.0, 7.0]])
        self.assertEqual([f["flag"] for f in r["flags"]], ["dropout", "dual_mono"])
        self.assertGreater(r["octave_bands_db"]["1000"], -1.0)
        self.assertIsNotNone(r["max_short_term_lufs"])
        self.assertNotIn("frames", r)
        with open(self.out("whole/audio-report.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), r)
        self.assertEqual(png_size(r["images"]["loudness"]), (ar.PLOT_W, ar.PLOT_H))
        png_size(r["images"]["spectrogram"])

    def test_every_planted_fault_is_flagged_at_its_time(self):
        r = ar.report(self.faults, self.out("faults"))
        got = {f["flag"]: f for f in r["flags"]}
        self.assertEqual(sorted(got), ["clicks", "clipping", "dropout", "true_peak"])
        self.assertTrue(all(1.0 <= t < 1.5 for t in got["clipping"]["at_s"]), got["clipping"])
        # The burst's hard gain steps at 1.0 and 1.5 s are discontinuities as well;
        # its clipped flat tops in between are not counted again as clicks.
        self.assertEqual(len(r["clicks"]), 3)
        for got, want in zip(r["clicks"], (1.0, 1.5, 2.5)):
            self.assertAlmostEqual(got, want, delta=0.001)
        self.assertEqual(r["dropouts"], [[3.0, 4.0]])
        self.assertIn("(at 3 s)", ar.format_summary(r))

    def test_a_range_keeps_the_file_clock(self):
        r = ar.report(self.fx, self.out("range"), start=4.0, end=8.0)
        self.assertEqual(r["range_s"], [4.0, 8.0])
        self.assertEqual(r["silences"], [[5.0, 7.0]])

    def test_against_a_reference(self):
        r = ar.report(self.fx, self.out("vs"), reference=self.quiet)
        d = r["reference"]["difference"]
        self.assertAlmostEqual(d["integrated_lufs"], 6.0, delta=0.1)
        self.assertAlmostEqual(d["true_peak_dbtp"], 6.0, delta=0.1)
        for band, v in d["octave_bands_db"].items():
            if v is not None:
                self.assertAlmostEqual(v, 0.0, delta=0.1, msg=band)
        self.assertEqual(r["reference"]["numbers"]["file"], self.quiet)

    def test_a_target_flags_the_distance(self):
        r = ar.report(self.fx, self.out("target"), target_lufs=-14.0)
        self.assertTrue(any("off the -14.0 target" in f["note"] for f in r["flags"]), r["flags"])

    def test_refuses_a_folder_in_a_git_tree(self):
        inside = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audio-report-out")
        with self.assertRaises(OutputRefused):
            ar.report(self.fx, inside)
        self.assertFalse(os.path.exists(inside))

    def test_errors(self):
        with self.assertRaisesRegex(ar.ReportError, "no stream 1"):
            ar.report(self.fx, self.out("s1"), stream=1)
        with self.assertRaisesRegex(ar.ReportError, "0 audio stream"):
            ar.report(self.video, self.out("v"))
        with self.assertRaisesRegex(ar.ReportError, "after --start"):
            ar.report(self.fx, self.out("e"), start=5.0, end=4.0)

    def cli(self, *argv):
        return subprocess.run([sys.executable, SCRIPT, "audio-report", *argv],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")

    def test_cli(self):
        p = self.cli(self.fx, "-o", self.out("cli"), "--dest", "youtube_16x9", "--end", "0:05")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Loudness   I ", p.stdout)
        self.assertIn("FLAG       integrated", p.stdout)  # a sine at -21 LUFS against -14
        p = self.cli(self.fx, "-o", self.out("cli-json"), "--json", "--target", "-21")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["thresholds"]["target_lufs"], -21.0)
        p = self.cli(self.fx, "-o", self.out("cli-bad"), "--start", "x")
        self.assertEqual((p.returncode, p.stdout), (1, ""))
        self.assertIn("not a time", p.stderr)


if __name__ == "__main__":
    unittest.main()
