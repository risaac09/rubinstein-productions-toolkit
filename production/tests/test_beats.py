"""
Tests for rpresolve.beats on synthetic drum loops made here with numpy: a
kick on every beat, a hi-hat on every off-beat (louder than the kick and
bright, the case that pulls a tracker half a beat off), with and without a
backbeat snare and a sustained pad, at 97 and 120 BPM; a silent intro; a
WAV through ffmpeg; the beats.json shape rhythm reads; the refusals.

Run: /usr/bin/python3 -m unittest production.tests.test_beats -v
"""

import io
import json
import os
import sys
import tempfile
import unittest
import wave
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import beats, paths  # noqa: E402

PHASE_MS = 20.0


def loop(bpm, seconds=20.0, hat=1.5, white=False, snare=0.0, pad=True, lead=0.0, seed=0,
         sr=beats.SR):
    """(samples, true beat times): kick on the beat, hat on the off-beat."""
    rng = np.random.default_rng(seed)
    n = int(sr * (lead + seconds))
    y, beat = np.zeros(n), 60.0 / bpm
    t = np.arange(int(0.25 * sr)) / sr
    kick = np.sin(2 * np.pi * (55 + 110 * np.exp(-t * 35)) * t) * np.exp(-t * 14)
    hn = int(0.05 * sr)
    noise = rng.standard_normal(hn + 1)
    hh = (noise[1:] if white else np.diff(noise)) * np.exp(-np.arange(hn) / sr * 90)
    ts = np.arange(int(0.15 * sr)) / sr
    sn = (0.6 * rng.standard_normal(len(ts)) + np.sin(2 * np.pi * 185 * ts)) * np.exp(-ts * 25)

    def add(at, x, gain):
        i = int(round(at * sr))
        seg = y[i:i + len(x)]
        seg += gain * x[:len(seg)]

    truth = []
    for k in range(int(seconds / beat)):
        at = lead + k * beat
        truth.append(at)
        add(at, kick, 0.9)
        if snare and k % 2:
            add(at, sn, snare)
        add(at + beat / 2, hh, hat)
    if pad:
        tt = np.arange(n) / sr
        y[int(lead * sr):] += (0.12 * np.sin(2 * np.pi * 220 * tt)
                               + 0.08 * np.sin(2 * np.pi * 277 * tt))[int(lead * sr):]
    return y / np.abs(y).max() * 0.8, np.array(truth)


def errors_ms(found, truth):
    return np.array([(t - truth[np.argmin(np.abs(truth - t))]) * 1000 for t in found])


class TestTracker(unittest.TestCase):
    def check(self, bpm, **kw):
        y, truth = loop(bpm, **kw)
        tempo, found, phase = beats.beat_map(y)
        self.assertAlmostEqual(tempo, bpm, delta=1.0)
        self.assertGreaterEqual(len(found), len(truth) - 2)
        err = errors_ms(found, truth)
        self.assertLess(np.abs(err).max(), PHASE_MS, f"{bpm} BPM {kw}: {np.round(err, 1)}")
        return phase

    def test_97_and_120_bpm_lock_on_the_kick_not_the_off_beat_hat(self):
        for bpm in (97, 120):
            for kw in ({}, {"white": True, "hat": 1.0}, {"snare": 0.7},
                       {"pad": False, "hat": 3.0}, {"hat": 0.3, "seed": 4}):
                with self.subTest(bpm=bpm, **kw):
                    self.check(bpm, **kw)

    def test_the_broadband_flux_alone_takes_the_off_beat_and_the_phase_check_says_so(self):
        y, truth = loop(120, hat=3.0)
        env, low = beats.onset_strength(y)
        fps = beats.SR / beats.HOP
        first = beats.track(env, fps, beats.tempo(env, fps)) / fps
        self.assertGreater(np.median(np.abs(errors_ms(first, truth))), 200)  # half a beat off
        self.assertEqual(self.check(120, hat=3.0), "low band")

    def test_a_silent_intro_gets_no_beats(self):
        y, truth = loop(97, lead=3.0)
        _, found, _ = beats.beat_map(y)
        self.assertGreater(found[0], 3.0 - PHASE_MS / 1000)
        self.assertLess(np.abs(errors_ms(found, truth)).max(), PHASE_MS)

    def test_silence_is_an_error(self):
        with self.assertRaises(beats.BeatsError):
            beats.beat_map(np.zeros(beats.SR * 6))

    def test_a_prior_moves_an_ambiguous_tempo(self):
        y, _ = loop(75, hat=0.0, pad=False)          # kicks alone at 75: 150 is the octave above
        self.assertAlmostEqual(beats.beat_map(y, prior_bpm=75)[0], 75, delta=1.0)


def write_wav(path, y, sr=44100):
    """Stereo 16-bit at 44.1 kHz, so ffmpeg resamples and downmixes."""
    x = np.interp(np.arange(int(len(y) * sr / beats.SR)) * beats.SR / sr, np.arange(len(y)), y)
    pcm = (np.repeat(x[:, None], 2, axis=1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


class TestFile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.wav = os.path.join(cls._tmp.name, "loop.wav")
        y, cls.truth = loop(97, snare=0.7)
        write_wav(cls.wav, y)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_build_write_and_the_shape_rhythm_reads(self):
        r = beats.build(self.wav)
        self.assertTrue(r["proposal"])
        self.assertEqual(r["method"], beats.METHOD)
        self.assertIn(r["phase"], ("kept", "low band"))
        self.assertAlmostEqual(r["tempo"], 97, delta=1.0)
        self.assertAlmostEqual(r["duration_s"], 20.0, delta=0.01)
        self.assertEqual(r["beats_s"], sorted(r["beats_s"]))
        self.assertLess(np.abs(errors_ms(np.array(r["beats_s"]), self.truth)).max(), PHASE_MS)
        for key in ("sr", "hop", "bands_hz", "prior_bpm", "tightness", "low_hz", "phase_margin"):
            self.assertIn(key, r["params"])
        self.assertEqual(len(r["params"]["bands_hz"]) - 1, 7)
        out = os.path.join(self._tmp.name, "maps", "beats.json")
        beats.write(r, out)
        with open(out, encoding="utf-8") as f:
            back = json.load(f)
        self.assertEqual(back["beats_s"], r["beats_s"])  # rhythm.load_beats reads this key
        self.assertIn(f"{r['tempo']:g} BPM, {len(r['beats_s'])} beat(s)", beats.summary(r))

    def test_short_or_missing_files_are_refused(self):
        short = os.path.join(self._tmp.name, "short.wav")
        write_wav(short, loop(120, seconds=2.0)[0])
        with self.assertRaises(beats.BeatsError):
            beats.build(short)
        with self.assertRaises(beats.BeatsError):
            beats.build(os.path.join(self._tmp.name, "missing.wav"))

    def test_cli(self):
        out = os.path.join(self._tmp.name, "cli", "beats.json")
        so = io.StringIO()
        with redirect_stdout(so), redirect_stderr(io.StringIO()):
            self.assertEqual(beats.main([self.wav, "--out", out]), 0)
        self.assertIn("BPM", so.getvalue())
        self.assertTrue(os.path.isfile(out))
        repo = os.path.join(self._tmp.name, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            self.assertEqual(beats.main([self.wav, "--out", os.path.join(repo, "b.json")]), 1)
        self.assertIn("git working tree", err.getvalue())
        with self.assertRaises(paths.OutputRefused):
            beats.write({}, os.path.join(repo, "b.json"))


if __name__ == "__main__":
    unittest.main()
