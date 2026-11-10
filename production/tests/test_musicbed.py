"""
Tests for rpresolve.musicbed and the deliver_check tail row, on tones made here with ffmpeg.

The bed is the manifest's music cut and faded by ffmpeg into a 24-bit 48 kHz WAV (T18: a
SetFades call reads back and did not render). The tail row reads the final file: the last
100 ms against the window from 3 s to 1.5 s before the end, 12 dB lower or it fails.
Skipped where ffmpeg or ffprobe is missing.

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from rpresolve import config as rpconfig  # noqa: E402
from rpresolve import deliver, musicbed  # noqa: E402
from rpresolve import delivercheck as dc  # noqa: E402

needs_ffmpeg = unittest.skipUnless(
    all(os.access(t, os.X_OK) for t in (dc.FFMPEG, dc.FFPROBE)), "ffmpeg or ffprobe missing")
CFG = rpconfig.load_config("/nonexistent/config.json")


def run(cmd):
    subprocess.run([dc.FFMPEG, "-v", "error", "-y", "-nostdin"] + cmd, stdin=subprocess.DEVNULL,
                   check=True)


def tone(path, seconds, fade=None, video=True):
    """A 440 Hz tone at 48 kHz stereo, an mp4 with a black picture by default, a WAV with
    video=False; with fade, faded out over its last `fade` seconds."""
    af = [f"afade=t=out:st={seconds - fade}:d={fade}"] if fade else []
    cmd = (["-f", "lavfi", "-i", f"color=c=black:s=64x36:r=24:d={seconds}"] if video else []) \
        + ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}"] \
        + (["-af", ",".join(af)] if af else []) \
        + (["-c:v", "mpeg4", "-c:a", "aac", "-b:a", "192k", "-shortest"] if video else [])
    run(cmd + [path])
    return path


@needs_ffmpeg
class TestBake(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = os.path.realpath(self.tmp.name)
        self.song = tone(os.path.join(self.d, "song.wav"), 12, video=False)

    def streams(self, path):
        info = dc.probe(path)
        return info["streams"][0], dc.duration(info)

    def test_the_bed_is_24_bit_48k_wav_of_the_asked_length_and_fades_out(self):
        out = os.path.join(self.d, "beds", "bed.wav")  # the folder is made
        got = musicbed.bake(self.song, 2.0, 5.0, 1.0, out)
        s, secs = self.streams(out)
        self.assertEqual((s["codec_name"], int(s["sample_rate"]), s["channels"]),
                         ("pcm_s24le", 48000, 1))
        self.assertAlmostEqual(secs, 5.0, delta=0.01)
        self.assertAlmostEqual(got, 5.0, delta=0.01)
        # the tail fell by far more than the 12 dB the deliver_check row asks for
        tail, mid = dc.window_level(out, 0.1, 0.1), dc.window_level(out, 3.0, 1.5)
        self.assertGreater(mid - tail, 12)
        self.assertEqual(os.listdir(os.path.dirname(out)), ["bed.wav"])  # no .part left behind

    def test_an_unfaded_cut_of_the_same_tone_does_not_fall(self):
        # the control: without the fade the tail is as loud as the middle
        out = os.path.join(self.d, "plain.wav")
        run(["-ss", "2", "-t", "5", "-i", self.song, "-c:a", "pcm_s24le", out])
        self.assertLess(dc.window_level(out, 3.0, 1.5) - dc.window_level(out, 0.1, 0.1), 1)

    def test_a_source_that_runs_out_is_refused_and_leaves_no_file(self):
        out = os.path.join(self.d, "short.wav")
        with self.assertRaisesRegex(musicbed.BedError, "holds .* s of music from 10 s"):
            musicbed.bake(self.song, 10.0, 5.0, 1.0, out)
        self.assertEqual(os.listdir(self.d), ["song.wav"])

    def test_a_fade_over_a_quarter_of_the_bed_or_below_zero_is_refused(self):
        for fade in (1.5, 0, -1):
            with self.assertRaisesRegex(musicbed.BedError, "fade"):
                musicbed.bake(self.song, 0, 5.0, fade, os.path.join(self.d, "x.wav"))

    def test_a_missing_source_is_a_bed_error(self):
        with self.assertRaisesRegex(musicbed.BedError, "ffmpeg could not cut"):
            musicbed.bake(os.path.join(self.d, "none.wav"), 0, 5.0, 1.0,
                          os.path.join(self.d, "x.wav"))


class TestSpec(unittest.TestCase):
    def test_fade_is_one_second_and_never_over_a_quarter(self):
        self.assertEqual(musicbed.FADE_S, 1.0)
        self.assertEqual(musicbed.fade_for(20), 1.0)
        self.assertEqual(musicbed.fade_for(2.25), 0.5625)

    def test_the_name_follows_the_source_in_point_length_and_fade(self):
        with tempfile.TemporaryDirectory() as d:
            song = os.path.join(d, "song.m4a")
            Path(song).write_bytes(b"x")
            a = musicbed.spec(song, 8.0, 20.0, d)
            self.assertEqual(a, musicbed.spec(song, 8.0, 20.0, d))
            self.assertEqual(os.path.dirname(a["path"]), d)
            self.assertTrue(os.path.basename(a["path"]).startswith("song.bed-"))
            for other in (musicbed.spec(song, 9.0, 20.0, d), musicbed.spec(song, 8.0, 21.0, d)):
                self.assertNotEqual(a["path"], other["path"])
            Path(song).write_bytes(b"changed")
            self.assertNotEqual(a["path"], musicbed.spec(song, 8.0, 20.0, d)["path"])
            with self.assertRaises(musicbed.BedError):
                musicbed.spec(os.path.join(d, "none.m4a"), 0, 5, d)


@needs_ffmpeg
class TestTailRow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = os.path.realpath(self.tmp.name)
        self.dest = deliver.destination(CFG, "social_9x16_music")

    def row(self, path, dest=None, **kw):
        r = dc.check(path, dest or self.dest, **kw)
        return next((x for x in r["checks"] if x["check"] == "tail"), None)

    def test_a_faded_tone_passes_and_the_row_holds_both_levels(self):
        row = self.row(tone(os.path.join(self.d, "faded.mp4"), 8, fade=1))
        self.assertEqual(row["status"], dc.PASS, row)
        self.assertRegex(row["found"], r"last 100 ms -?[\d.]+ dB, window -?[\d.]+ dB \([\d.]+ dB lower\)")

    def test_an_unfaded_tone_fails_with_both_numbers(self):
        row = self.row(tone(os.path.join(self.d, "hard.mp4"), 8))
        self.assertEqual(row["status"], dc.FAIL, row)
        self.assertRegex(row["found"], r"last 100 ms -?[\d.]+ dB, window -?[\d.]+ dB \(-?[\d.]+ dB lower\)")
        self.assertIn("at least 12 dB below", row["expected"])

    def test_a_short_file_or_no_audio_or_loudness_off_skips_with_a_note(self):
        short = tone(os.path.join(self.d, "short.mp4"), 3, fade=1)
        self.assertEqual((self.row(short)["status"], self.row(short)["note"]),
                         (dc.SKIP, "the file is under 4 s"))
        silent = os.path.join(self.d, "silent.mp4")
        run(["-f", "lavfi", "-i", "color=c=black:s=64x36:r=24:d=6", "-c:v", "mpeg4", silent])
        self.assertEqual(self.row(silent)["note"], "no audio stream")
        full = tone(os.path.join(self.d, "full.mp4"), 8)
        off = self.row(full, loudness=False)
        self.assertEqual((off["status"], off["note"]), (dc.SKIP, "loudness reading is off"))

    def test_a_destination_without_tail_fade_has_no_row_until_expect_fade_turns_it_on(self):
        hard = tone(os.path.join(self.d, "hard.mp4"), 8)
        plain = deliver.destination(CFG, "social_9x16")
        self.assertNotIn("tail_fade", plain)
        self.assertIsNone(self.row(hard, plain))
        self.assertEqual(self.row(hard, plain, expect_fade=True)["status"], dc.FAIL)
        self.assertIsNone(self.row(hard, expect_fade=False))  # and off for the one that has it

    def test_a_tail_fade_that_is_not_true_or_false_is_an_error(self):
        dest = {**self.dest, "tail_fade": "yes"}
        with self.assertRaisesRegex(dc.CheckError, "tail_fade must be true or false"):
            dc.check(os.path.join(self.d, "x.mp4"), dest)

    def test_the_cli_flag_turns_it_on_for_another_destination(self):
        from rpresolve import cmd_deliver_check
        hard = tone(os.path.join(self.d, "hard.mp4"), 8)
        with mock.patch("sys.stdout") as out:
            code = cmd_deliver_check.run(mock.Mock(
                file=hard, dest="social_9x16", deliver_config=None, config=None, fps=None,
                size=None, audio_state=None, boxes=None, no_loudness=False, ledger_row=None,
                review_log=None, json=True, expect_fade=True))
        text = "".join(c.args[0] for c in out.write.call_args_list)
        self.assertEqual(code, 1)
        self.assertEqual(next(x for x in json.loads(text)["checks"] if x["check"] == "tail")
                         ["status"], dc.FAIL)


class TestConfig(unittest.TestCase):
    def test_only_the_music_montage_destination_asks_for_the_tail_row(self):
        asking = [k for k in deliver.destination_keys(CFG)
                  if deliver.destination(CFG, k).get("tail_fade")]
        self.assertEqual(asking, ["social_9x16_music"])


if __name__ == "__main__":
    unittest.main()
