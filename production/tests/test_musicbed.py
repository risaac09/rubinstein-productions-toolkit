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
    """A 440 Hz mono tone at 48 kHz, an mp4 with a black picture by default, a WAV with
    video=False; with fade, faded out over its last `fade` seconds."""
    af = [f"afade=t=out:st={seconds - fade}:d={fade}"] if fade else []
    cmd = (["-f", "lavfi", "-i", f"color=c=black:s=64x36:r=24:d={seconds}"] if video else []) \
        + ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}"] \
        + (["-af", ",".join(af)] if af else []) \
        + (["-c:v", "mpeg4", "-c:a", "aac", "-b:a", "192k", "-shortest"] if video else [])
    run(cmd + [path])
    return path


def graph_mp4(path, picture_s, audio_graph):
    """An mp4 of picture_s seconds of black picture and the audio the filter graph makes
    (ending in [a]), so a test can shape the sound in time."""
    run(["-f", "lavfi", "-i", f"color=c=black:s=64x36:r=24:d={picture_s}",
         "-filter_complex", audio_graph, "-map", "0:v", "-map", "[a]",
         "-c:v", "mpeg4", "-c:a", "aac", "-b:a", "192k", path])
    return path


def stepped(path, seconds, drop_db, at):
    """A tone that falls by drop_db dB at `at` seconds and stays there to the end."""
    return graph_mp4(path, seconds, f"sine=frequency=440:sample_rate=48000:duration={seconds},"
                     f"volume=enable='gte(t,{at})':volume=-{drop_db}dB[a]")


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

    def test_the_bed_starts_at_the_in_point_and_keeps_both_channels(self):
        # 4 s of silence then a tone, stereo: a cut that ignored the in point would start silent
        src = os.path.join(self.d, "shaped.wav")
        run(["-filter_complex", "anullsrc=r=48000:cl=mono:d=4[s];"
             "sine=frequency=440:sample_rate=48000:duration=8[t];"
             "[s][t]concat=n=2:v=0:a=1,aformat=channel_layouts=stereo[a]", "-map", "[a]", src])
        out = os.path.join(self.d, "shaped-bed.wav")
        musicbed.bake(src, 2.0, 5.0, 1.0, out)
        s, secs = self.streams(out)
        self.assertEqual(s["channels"], 2)
        self.assertAlmostEqual(secs, 5.0, delta=0.01)
        # the first 2 s of the bed are the last 2 s of the silence, then the tone begins
        self.assertLess(dc.window_level(out, 5.0, 0.5), -80)
        self.assertGreater(dc.window_level(out, 2.5, 0.5), -40)

    def test_the_fade_ramps_down_over_the_last_second_and_ends_at_the_last_sample(self):
        out = os.path.join(self.d, "ramp.wav")
        musicbed.bake(self.song, 2.0, 5.0, 1.0, out)
        full = dc.window_level(out, 3.0, 1.5)
        start, middle, end = (dc.window_level(out, 1.0, 0.1), dc.window_level(out, 0.55, 0.1),
                              dc.window_level(out, 0.1, 0.1))
        # a linear ramp over the last second: about 0.4 dB down where it starts, 6 dB at its
        # middle, 25 dB in the last 100 ms. A fade that ended early or was only a short ramp
        # followed by silence misses the first or the second window.
        self.assertAlmostEqual(start - full, -0.4, delta=1.0)
        self.assertAlmostEqual(middle - full, -6.0, delta=2.0)
        self.assertAlmostEqual(end - full, -24.8, delta=3.0)
        self.assertGreater(start, middle)
        self.assertGreater(middle, end)

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
class TestSource(unittest.TestCase):
    """The dry run asks ffprobe whether the music runs long enough, before bake() does."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.song = tone(os.path.join(os.path.realpath(self.tmp.name), "song.wav"), 12, video=False)

    def test_the_length_is_read_from_the_audio(self):
        self.assertAlmostEqual(musicbed.source_seconds(self.song), 12.0, delta=0.01)

    def test_a_source_with_enough_music_from_the_in_point_passes(self):
        musicbed.check_source(self.song, 2.0, 5.0)
        musicbed.check_source(self.song, 7.0, 5.0)  # up to the last frame

    def test_a_source_that_runs_out_is_refused_with_the_figures(self):
        with self.assertRaisesRegex(musicbed.BedError,
                                    r"song.wav holds 2.00 s of music from 10 s, the bed needs 5.00 s"):
            musicbed.check_source(self.song, 10.0, 5.0)

    def test_a_file_ffprobe_cannot_read_is_a_bed_error(self):
        junk = os.path.join(os.path.dirname(self.song), "junk.wav")
        Path(junk).write_bytes(b"not audio")
        with self.assertRaises(musicbed.BedError):
            musicbed.source_seconds(junk)

    def test_a_length_ffprobe_does_not_give_passes_and_bake_decides(self):
        with mock.patch.object(musicbed, "source_seconds", return_value=None):
            musicbed.check_source(self.song, 10.0, 5.0)


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

    def test_the_row_reads_the_window_3_s_to_1_5_s_before_the_end(self):
        # loud only from 5 s to 6.5 s of 8 (3 s to 1.5 s from the end), silent elsewhere:
        # a reference read anywhere else sees silence and cannot find a drop
        only_there = graph_mp4(os.path.join(self.d, "window.mp4"), 8,
                               "sine=frequency=440:sample_rate=48000:duration=8,"
                               "volume=enable='lt(t,5)+gt(t,6.5)':volume=-90dB[a]")
        row = self.row(only_there)
        self.assertEqual(row["status"], dc.PASS, row)
        self.assertRegex(row["found"], r"window -2\d(\.\d)? dB")

    def test_the_12_db_boundary_a_tail_11_db_down_fails_and_13_db_down_passes(self):
        # a step half a second before the end, so the last 100 ms sit wholly in the new level
        eleven = self.row(stepped(os.path.join(self.d, "d11.mp4"), 8, 11, 7.5))
        thirteen = self.row(stepped(os.path.join(self.d, "d13.mp4"), 8, 13, 7.5))
        self.assertEqual((eleven["status"], thirteen["status"]), (dc.FAIL, dc.PASS), (eleven, thirteen))
        self.assertIn("(11.0 dB lower)", eleven["found"])
        self.assertIn("(13.0 dB lower)", thirteen["found"])

    def test_sound_that_stops_before_the_picture_ends_fails_the_row(self):
        # 8 s of music under 10 s of picture: ffmpeg reads no sound in the last 100 ms,
        # which is the bed-too-short case this row is for, and not an error that drops the rest
        short = graph_mp4(os.path.join(self.d, "stops.mp4"), 10,
                          "sine=frequency=440:sample_rate=48000:duration=8[a]")
        self.assertIsNone(dc.window_level(short, 0.1, 0.1))
        r = dc.check(short, self.dest)
        row = next(x for x in r["checks"] if x["check"] == "tail")
        self.assertEqual((row["status"], row["found"]), (dc.FAIL, "no audio in the last 100 ms"))
        self.assertGreater(len(r["checks"]), 5)  # the other rows were still read

    def test_the_mcp_tool_takes_expect_fade(self):
        from rpresolve.mcp import schema, server
        from rpresolve.mcp.registry import ToolContext
        tool = server.build_registry().get("deliver_check")
        hard = tone(os.path.join(self.d, "hard.mp4"), 8)

        def call(**kw):
            args = {"file": hard, "destination": "social_9x16", **kw}
            self.assertEqual(schema.validate(tool.input_schema, args), [])
            out = tool.handler(schema.with_defaults(tool.input_schema, args), ToolContext())
            return next((x for x in out["checks"] if x["check"] == "tail"), None), out

        self.assertIsNone(call()[0])
        row, out = call(expect_fade=True)
        self.assertEqual(row["status"], dc.FAIL)
        self.assertIn("tail", out["summary"])
        self.assertIsNone(call(destination="social_9x16_music", expect_fade=False)[0])
        self.assertEqual(call(destination="social_9x16_music")[0]["status"], dc.FAIL)
        self.assertTrue(schema.validate(tool.input_schema, {"file": hard, "destination": "x",
                                                            "expect_fade": "yes"}))

    def test_the_cli_parser_takes_expect_fade(self):
        import contextlib
        import io
        from rpresolve import cli
        hard = tone(os.path.join(self.d, "hard.mp4"), 8)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = cli.main(["deliver-check", hard, "--dest", "social_9x16", "--expect-fade",
                             "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(next(x for x in json.loads(out.getvalue())["checks"]
                              if x["check"] == "tail")["status"], dc.FAIL)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            cli.main(["deliver-check", hard, "--dest", "social_9x16", "--json"])
        self.assertNotIn("tail", [x["check"] for x in json.loads(out.getvalue())["checks"]])

    def test_a_tail_fade_that_is_not_true_or_false_is_an_error(self):
        dest = {**self.dest, "tail_fade": "yes"}
        with self.assertRaisesRegex(dc.CheckError, "tail_fade must be true or false"):
            dc.check(os.path.join(self.d, "x.mp4"), dest)


class TestConfig(unittest.TestCase):
    def test_only_the_music_montage_destination_asks_for_the_tail_row(self):
        asking = [k for k in deliver.destination_keys(CFG)
                  if deliver.destination(CFG, k).get("tail_fade")]
        self.assertEqual(asking, ["social_9x16_music"])


if __name__ == "__main__":
    unittest.main()
