"""
Tests for the opt-in qc rows of VP-20: sync, colour and stabiliser, on media
made here from lavfi sources and seeded noise (a few seconds each). Every
threshold is a working default; these tests pin the rows, not the numbers'
standing. Skipped where ffmpeg ("ffmpeg missing") or numpy is missing.

Run: /usr/bin/python3 -m unittest production.tests.test_qc_vp20 -v
"""

import os
import subprocess
import sys
import re
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import delivercheck as dc, qc  # noqa: E402

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

WORKFLOW = Path(__file__).resolve().parent.parent / "resolve_workflow.py"
RATE = 48000


def _have():
    if np is None or not all(os.access(t, os.X_OK) for t in (dc.FFMPEG, dc.FFPROBE)):
        return False
    enc = subprocess.run([dc.FFMPEG, "-hide_banner", "-encoders"], stdin=subprocess.DEVNULL,
                         capture_output=True, text=True).stdout
    return "libx264" in enc


def _ff(*args):
    p = subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", *args],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr)


def _wav(path, x):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def _row(r, name):
    return next(c for c in r["checks"] if c["check"] == name)


def _video(out, vf_graph, audio=None):
    """vf_graph: a lavfi graph ending in [v]; audio: a wav to carry, or none."""
    cmd = ["-filter_complex", vf_graph]
    if audio:
        cmd += ["-i", audio]
    cmd += ["-map", "[v]"] + (["-map", "0:a"] if audio else []) + \
        ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-t", "12", out]
    _ff(*cmd)


@unittest.skipUnless(_have(), "ffmpeg or numpy missing")
class Vp20Rows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        t = cls._tmp.name
        rng = np.random.default_rng(7)
        noise = (rng.standard_normal(RATE * 12) * 0.2).astype(np.float32)
        cls.ref = os.path.join(t, "ref.wav")
        _wav(cls.ref, noise)
        late = np.concatenate([np.zeros(int(0.2 * RATE), np.float32), noise])[:RATE * 12]
        cls.late_wav = os.path.join(t, "late.wav")
        _wav(cls.late_wav, late)
        cls.other = os.path.join(t, "other.wav")
        _wav(cls.other, (np.random.default_rng(99).standard_normal(RATE * 12) * 0.2).astype(np.float32))
        pic = "color=c=gray:s=108x192:r=25:d=12[v]"
        cls.inplace = os.path.join(t, "inplace.mov")
        cls.delayed = os.path.join(t, "delayed.mov")
        cls.unrelated = os.path.join(t, "unrelated.mov")
        _video(cls.inplace, pic, cls.ref)
        _video(cls.delayed, pic, cls.late_wav)
        _video(cls.unrelated, pic, cls.other)
        cls.gray = os.path.join(t, "gray.mp4")
        cls.black = os.path.join(t, "black.mp4")
        cls.hot = os.path.join(t, "hot.mp4")
        cls.clamped = os.path.join(t, "clamped.mp4")
        _video(cls.gray, "color=c=gray:s=108x192:r=25:d=3[v]")
        _video(cls.black, "color=c=black:s=108x192:r=25:d=3[v]")
        _video(cls.hot, "color=c=gray:s=108x192:r=25:d=3,format=yuv420p,lutyuv=y=250[v]")
        _video(cls.clamped, "color=c=gray:s=108x192:r=25:d=3,format=yuv420p,lutyuv=y=235[v]")
        # a static textured picture (testsrc2 carries moving parts, which a sub-pixel read sees)
        pat = os.path.join(t, "pat.png")
        _ff("-f", "lavfi", "-i", "nullsrc=s=270x480", "-vf",
            "geq=lum='128+50*sin(X/7)*cos(Y/11)+35*sin((X+2*Y)/4)':cb=128:cr=128", "-frames:v", "1", pat)
        src = f"movie={pat},loop=loop=-1:size=1,fps=25,trim=duration=12,setpts=N/25/TB"
        cls.steady = os.path.join(t, "steady.mp4")
        cls.shaky = os.path.join(t, "shaky.mp4")
        cls.bump = os.path.join(t, "bump.mp4")
        _video(cls.steady, src + ",crop=216:384:25:25[v]")
        _video(cls.shaky, src + ",crop=216:384:x='25+10*sin(n*2.3)':y='25+10*sin(n*3.7+1)'[v]")
        # steady except for one 0.8 s bump, which only a burst landing on it sees
        _video(cls.bump, src + ",crop=216:384:x='25+if(between(t,4.9,5.7),8*sin(n*2.3),0)':y=25[v]")
        # letterboxed grey; grey with a 0.5 s blown patch between two of six evenly spaced samples
        cls.boxed = os.path.join(t, "boxed.mp4")
        cls.flash = os.path.join(t, "flash.mp4")
        cls.crushed = os.path.join(t, "crushed.mp4")
        cls.clipped = os.path.join(t, "clipped.mp4")
        _video(cls.boxed, "color=c=gray:s=192x108:r=25:d=3,drawbox=x=0:y=0:w=192:h=14:color=black:t=fill,"
                          "drawbox=x=0:y=94:w=192:h=14:color=black:t=fill[v]")
        cls.barhot = os.path.join(t, "barhot.mp4")  # blown text in the bottom bar for 0.4 s, between two bar probes
        _video(cls.barhot, "color=c=gray:s=192x108:r=25:d=12,drawbox=x=0:y=0:w=192:h=14:color=black:t=fill,"
                           "drawbox=x=0:y=94:w=192:h=14:color=black:t=fill,format=yuv420p,"
                           "drawbox=x=20:y=98:w=100:h=6:color=white:t=fill:enable='between(t,0.9,1.3)'[v]")
        _video(cls.flash, "color=c=gray:s=108x192:r=25:d=12[a];color=c=gray:s=30x20:r=25:d=12,format=yuv420p,"
                          "lutyuv=y=240[b];[a][b]overlay=x=10:y=10:enable='between(t,6.0,6.5)'[v]")
        _video(cls.crushed, "color=c=gray:s=108x192:r=25:d=3,format=yuv420p,lutyuv=y=2[v]")
        _video(cls.clipped, "color=c=gray:s=108x192:r=25:d=3,format=yuv420p,lutyuv=y=254[v]")
        # the stream says 108x192, Frame Cropping leaves 108x144
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from cropfix import add_clap
        cls.cropped = os.path.join(t, "cropped.mp4")
        add_clap(cls.gray, cls.cropped, 24, 24)
        # a minute of noise with the sound a hair slow in the second file (250 ppm: 15 ms over the minute)
        n = RATE * 60
        long_ref = (rng.standard_normal(n) * 0.2).astype(np.float32)
        cls.long_ref = os.path.join(t, "long_ref.wav")
        _wav(cls.long_ref, long_ref)
        slow = np.interp(np.arange(n) * (1 + 250e-6), np.arange(n), long_ref).astype(np.float32)
        cls.long_slow_wav = os.path.join(t, "long_slow.wav")
        _wav(cls.long_slow_wav, slow)
        cls.long_inplace = os.path.join(t, "long_inplace.mov")
        cls.long_slow = os.path.join(t, "long_slow.mov")
        for out, wav in ((cls.long_inplace, cls.long_ref), (cls.long_slow, cls.long_slow_wav)):
            _ff("-f", "lavfi", "-i", "color=c=gray:s=108x192:r=25:d=60", "-i", wav, "-map", "0:v", "-map", "1:a",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-t", "60", out)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    # sync
    def test_sync_skips_without_a_reference(self):
        row = _row(qc.run(self.inplace), "sync")
        self.assertEqual(row["status"], qc.SKIP)

    def test_sync_passes_a_file_sitting_on_its_reference(self):
        row = _row(qc.run(self.inplace, sync_ref=self.ref), "sync")
        self.assertEqual(row["status"], qc.PASS, row)

    def test_sync_fails_a_file_200_ms_late(self):
        row = _row(qc.run(self.delayed, sync_ref=self.ref), "sync")
        self.assertEqual(row["status"], qc.FAIL, row)
        self.assertIn("ms", row["found"])

    def test_sync_warns_when_the_sound_is_unrelated(self):
        row = _row(qc.run(self.unrelated, sync_ref=self.ref), "sync")
        self.assertEqual((row["status"], row["found"]), (qc.WARN, "no match"))

    def test_sync_tolerance_is_a_threshold(self):
        th = qc.parse_overrides(["sync_fail_frames=20", "sync_warn_frames=20"])
        self.assertEqual(_row(qc.run(self.delayed, sync_ref=self.ref, thresholds=th), "sync")["status"],
                         qc.PASS)

    def test_sync_threshold_is_in_frames_at_the_files_rate(self):
        # 200 ms late: five frames at 25 fps, so a 5.5 frame tolerance passes it and 4.5 fails it
        for frames, want in ((5.5, qc.WARN), (4.5, qc.FAIL)):
            th = qc.parse_overrides([f"sync_fail_frames={frames}"])
            row = _row(qc.run(self.delayed, sync_ref=self.ref, thresholds=th), "sync")
            self.assertEqual(row["status"], want, row)
        self.assertIn("40.0 ms at 25 fps", _row(qc.run(self.delayed, sync_ref=self.ref), "sync")["expected"])

    def test_sync_half_a_frame_warns_and_a_frame_fails(self):
        th = qc.parse_overrides(["sync_fail_frames=20"])
        # 200 ms is 5 frames: over the 0.5 frame warn line and under a 20 frame fail line
        self.assertEqual(_row(qc.run(self.delayed, sync_ref=self.ref, thresholds=th), "sync")["status"], qc.WARN)

    def test_sync_reads_several_windows_and_reports_drift_as_its_own_value(self):
        row = _row(qc.run(self.long_inplace, sync_ref=self.long_ref), "sync")
        self.assertEqual(row["status"], qc.PASS, row)
        self.assertRegex(row["found"], r"drift [+-]0\.\d ms over")
        self.assertRegex(row["note"], r"[4-9] windows")
        slow = _row(qc.run(self.long_slow, sync_ref=self.long_ref), "sync")
        m = re.search(r"drift ([+-][\d.]+) ms over", slow["found"])
        self.assertTrue(m, slow)
        self.assertAlmostEqual(abs(float(m.group(1))), 15.0, delta=3.0)  # 250 ppm over about 55 s of overlap
        self.assertEqual(slow["status"], qc.PASS, slow)  # 15 ms is under half a frame (20 ms)
        tight = qc.parse_overrides(["sync_warn_frames=0.25"])
        row = _row(qc.run(self.long_slow, sync_ref=self.long_ref, thresholds=tight), "sync")
        self.assertEqual(row["status"], qc.WARN, row)  # the drift alone warns, and never fails
        self.assertNotEqual(row["status"], qc.FAIL)

    def test_sync_with_a_reference_that_has_no_sound_warns_and_keeps_the_table(self):
        r = qc.run(self.inplace, sync_ref=self.gray)
        self.assertEqual(_row(r, "sync")["status"], qc.WARN)
        self.assertTrue(_row(r, "sha256"))

    # colour
    def test_colour_skips_unless_asked(self):
        self.assertEqual(_row(qc.run(self.gray), "colour")["status"], qc.SKIP)

    def test_colour_passes_mid_grey(self):
        row = _row(qc.run(self.gray, colour=True), "colour")
        self.assertEqual(row["status"], qc.PASS, row)

    def test_colour_reports_the_share_clamped_at_legal_white_and_stays_advisory(self):
        row = _row(qc.run(self.clamped, colour=True), "colour")  # Y 235 everywhere
        self.assertEqual(row["status"], qc.PASS, row)
        self.assertIn("100.00% exactly at Y 235", row["found"])
        self.assertIn("0.00% over Y 235", row["found"])

    def test_colour_reads_a_picture_that_audio_outruns_and_a_one_second_clip(self):
        long_audio = os.path.join(self._tmp.name, "long_audio.mkv")
        _ff("-f", "lavfi", "-i", "color=c=gray:s=108x192:r=5:d=60", "-f", "lavfi", "-i", "sine=d=62",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", long_audio)
        short = os.path.join(self._tmp.name, "one_second.mp4")
        _ff("-f", "lavfi", "-i", "color=c=gray:s=108x192:r=25:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", short)
        for clip in (long_audio, short):
            row = _row(qc.run(clip, colour=True), "colour")
            self.assertEqual(row["status"], qc.PASS, (clip, row))

    def test_colour_reports_the_low_side_without_warning(self):
        row = _row(qc.run(self.black, colour=True), "colour")  # legal black, Y 16
        self.assertEqual(row["status"], qc.PASS, row)
        self.assertIn("under Y 16", row["note"])

    def test_colour_warns_over_one_percent_above_legal_white_but_not_at_hard_clipping(self):
        row = _row(qc.run(self.hot, colour=True), "colour")  # Y 250 everywhere
        self.assertEqual(row["status"], qc.WARN, row)
        self.assertIn("100.00% over Y 235", row["found"])

    def test_colour_fails_at_hard_clipping_high_and_low(self):
        for f in (self.clipped, self.crushed):
            row = _row(qc.run(f, colour=True), "colour")
            self.assertEqual(row["status"], qc.FAIL, (f, row))
        self.assertEqual(qc.run(self.crushed, colour=True)["status"], "fail")

    def test_colour_masks_letterbox_bars(self):
        row = _row(qc.run(self.boxed, colour=True), "colour")
        self.assertEqual(row["status"], qc.PASS, row)
        self.assertIn("bars masked", row["note"])
        self.assertIn("0.00% low", row["found"])

    def test_colour_warns_when_hot_pixels_sit_inside_the_masked_bars(self):
        row = _row(qc.run(self.barhot, colour=True), "colour")
        self.assertEqual(row["status"], qc.WARN, row)
        self.assertIn("inside the masked bars", row["found"])

    def test_a_partly_read_file_does_not_pass_the_colour_row(self):
        from unittest import mock
        from rpresolve import measure
        real = measure.luma_stream

        def short(*a, **kw):  # ffmpeg exits 0 on a file cut short: the stream just ends early
            for i, y in enumerate(real(*a, **kw)):
                if i >= 14:
                    return
                yield y
        with mock.patch.object(measure, "luma_stream", short):
            row = _row(qc.run(self.flash, colour=True), "colour")  # 12 s at 1.5 a second is 18 frames
        self.assertEqual((row["status"], row["found"]), (qc.WARN, "not read"), row)
        self.assertIn("ends early", row["note"])

    def test_colour_sees_a_short_blown_patch_that_six_samples_missed(self):
        row = _row(qc.run(self.flash, colour=True), "colour")  # 600 of 20736 px, 0.5 s of 12 s
        self.assertEqual(row["status"], qc.WARN, row)
        self.assertRegex(row["found"], r"[12]\.\d\d% over Y 235")
        self.assertIn("first at", row["note"])

    def test_colour_found_says_how_many_frames_it_read(self):
        row = _row(qc.run(self.flash, colour=True), "colour")  # 12 s at 1.5 a second
        self.assertIn("18 frames read", row["found"])

    def test_colour_and_stabiliser_read_a_frame_cropped_file(self):
        r = qc.run(self.cropped, colour=True, stabiliser=True)
        for name in ("colour", "stabiliser"):
            row = _row(r, name)
            self.assertNotEqual(row["found"], "not read", row)

    def test_a_failed_colour_read_is_a_warn_row_that_keeps_the_table(self):
        from unittest import mock
        from rpresolve import measure
        with mock.patch.object(measure, "luma_pass", side_effect=measure.MeasureError("ffmpeg could not read a frame at 1s")):
            r = qc.run(self.gray, colour=True)
        row = _row(r, "colour")
        self.assertEqual((row["status"], row["found"]), (qc.WARN, "not read"))
        self.assertIn("could not read a frame", row["note"])
        self.assertTrue(_row(r, "sha256"))
        self.assertEqual(r["status"], qc.run(self.gray)["status"])  # the WARN does not change the outcome

    def test_a_failed_stabiliser_read_is_a_warn_row_that_keeps_the_table(self):
        from unittest import mock
        real = subprocess.run

        def run(cmd, *a, **kw):  # only the stabiliser's grey-picture read breaks
            if "format=gray" in " ".join(map(str, cmd)):
                return subprocess.CompletedProcess(cmd, 1, b"", b"broken pipe")
            return real(cmd, *a, **kw)
        with mock.patch.object(qc.subprocess, "run", side_effect=run):
            r = qc.run(self.steady, stabiliser=True)
        row = _row(r, "stabiliser")
        self.assertEqual((row["status"], row["found"]), (qc.WARN, "not read"))
        self.assertIn("could not sample frames", row["note"])
        self.assertTrue(_row(r, "sha256"))

    def test_colour_skips_a_full_range_file(self):
        full = os.path.join(self._tmp.name, "full.mp4")
        _ff("-f", "lavfi", "-i", "color=c=gray:s=108x192:r=25:d=2", "-vf", "format=yuvj420p", "-c:v", "libx264", full)
        row = _row(qc.run(full, colour=True), "colour")
        self.assertEqual(row["status"], qc.SKIP, row)

    # stabiliser
    def test_stabiliser_skips_unless_asked(self):
        self.assertEqual(_row(qc.run(self.steady), "stabiliser")["status"], qc.SKIP)

    def test_stabiliser_passes_a_locked_off_shot(self):
        row = _row(qc.run(self.steady, stabiliser=True), "stabiliser")
        self.assertEqual(row["status"], qc.PASS, row)

    def test_stabiliser_warns_on_residual_shake_and_never_fails(self):
        r = qc.run(self.shaky, stabiliser=True)
        self.assertEqual(_row(r, "stabiliser")["status"], qc.WARN, _row(r, "stabiliser"))
        self.assertEqual(r["status"], "pass")

    def test_stabiliser_catches_one_bump_that_three_fixed_bursts_would_miss(self):
        row = _row(qc.run(self.bump, stabiliser=True), "stabiliser")
        self.assertEqual(row["status"], qc.WARN, row)  # the worst burst, not the median of twelve
        self.assertRegex(row["note"], r"12 bursts of 12 frames, [1-3] over")
        self.assertIn("advisory", row["note"])
        self.assertIn("advisory", row["expected"])

    def test_stabiliser_reads_a_fraction_of_a_pixel(self):
        rng = np.random.default_rng(3)
        k = np.fft.fftfreq(440)[None, :] ** 2 + np.fft.fftfreq(260)[:, None] ** 2
        big = np.fft.ifft2(np.fft.fft2(rng.standard_normal((260, 440))) * np.exp(-k * 100)).real * 40 + 128
        fy, fx = np.fft.fftfreq(260)[:, None], np.fft.fftfreq(440)[None, :]
        crop = lambda a: a[40:220, 60:380]  # noqa: E731
        for dx, dy in ((0.0, 0.0), (0.2, 0.0), (0.4, -0.3), (-1.3, 0.6), (3.0, 0.0)):
            moved = np.fft.ifft2(np.fft.fft2(big) * np.exp(-2j * np.pi * (fx * dx + fy * dy))).real
            got = qc._shift(np, crop(moved), crop(big))
            self.assertAlmostEqual(got[0], dx, delta=0.05)
            self.assertAlmostEqual(got[1], dy, delta=0.05)

    # the command
    # privacy (always on) and the MCP flags
    def tagged(self, name, *tags):
        out = os.path.join(self._tmp.name, name)
        meta = [x for t in tags for x in ("-metadata", t)]
        _ff("-i", self.gray, "-c", "copy", *meta, out)
        return out

    def test_privacy_passes_a_clean_file_and_names_no_value(self):
        r = _row(qc.run(self.gray), "privacy")
        self.assertEqual(r["status"], qc.PASS, r)

    def test_privacy_warns_on_each_planted_tag_and_prints_keys_only(self):
        for tag in ("location=+41.0000-071.0000/", "serial_number=SN123456", "artist=Some Person",
                    "comment=exported from /Users/someone/clip.mov", "title=write me at a@example.org"):
            r = _row(qc.run(self.tagged("t.mkv", tag)), "privacy")
            self.assertEqual(r["status"], qc.WARN, (tag, r))
            self.assertNotIn(tag.split("=", 1)[1], r["found"] + str(r.get("note")))
            self.assertIn(tag.split("=")[0].lower(), r["found"].lower())

    def test_camera_id_and_name_warn_on_a_delivery_only(self):
        def info(k, v):
            return {"format": {"tags": {k: v}}, "streams": []}
        for k, v in (("com.example.cameraName", "Some Person"),
                     ("com.example.cameraUniqueID", "0123456789abcdef"), ("camera_id", "0123456789abcdef")):
            self.assertEqual(qc._privacy_row(info(k, v), delivery=True)["status"], qc.WARN, k)
            self.assertEqual(qc._privacy_row(info(k, v))["status"], qc.PASS, k)  # an original or proxy
            self.assertNotIn(v, qc._privacy_row(info(k, v), delivery=True)["found"])
        for v in ("Back", "front"):
            self.assertEqual(qc._privacy_row(info("com.apple.quicktime.camera.identifier", v),
                                             delivery=True)["status"], qc.PASS)
        self.assertIn("pass --dest", qc._privacy_row(info("camera_id", "0123456789abcdef"))["note"])
        self.assertNotIn("0123456789abcdef", qc._privacy_row(info("camera_id", "0123456789abcdef"))["note"])
        self.assertNotIn("pass --dest", qc._privacy_row(info("camera_id", "x"), delivery=True)["note"])
        # the other private tags still warn without a destination
        self.assertEqual(qc._privacy_row(info("serial_number", "SN1"))["status"], qc.WARN)

    def test_a_camera_id_alone_on_a_file_without_a_destination_passes(self):
        r = qc.run(self.tagged("cam.mkv", "com.example.cameraUniqueID=0123456789abcdef"))
        self.assertEqual(_row(r, "privacy")["status"], qc.PASS)

    def test_privacy_ignores_a_camera_label_and_a_clip_id(self):
        r = qc.run(self.tagged("lab.mkv", "com.example.cameraName=A", "clip_id=0123456789abcdef"))
        self.assertEqual(_row(r, "privacy")["status"], qc.PASS)

    def test_privacy_ignores_a_plain_title_and_never_fails(self):
        r = qc.run(self.tagged("ok.mkv", "title=Example clip"))
        self.assertEqual(_row(r, "privacy")["status"], qc.PASS)
        planted = qc.run(self.tagged("p.mkv", "location=+41.0000-071.0000/"))
        self.assertEqual({c["status"] for c in planted["checks"] if c["check"] == "privacy"}, {qc.WARN})
        self.assertEqual(r["status"], planted["status"])  # a WARN never changes the outcome

    def test_mcp_qc_exposes_sync_colour_and_stabiliser(self):
        from rpresolve.mcp import schema, server
        from rpresolve.mcp.registry import ToolContext
        tool = server.build_registry().get("qc")
        props = tool.input_schema["properties"]
        for k in ("sync_ref", "colour", "stabiliser"):
            self.assertIn(k, props)
        args = {"file": self.gray, "colour": True, "stabiliser": True, "sync_ref": self.ref}
        self.assertEqual(schema.validate(tool.input_schema, args), [])
        r = tool.handler(schema.with_defaults(tool.input_schema, args), ToolContext())
        rows = {c["check"]: c for c in r["checks"]}
        self.assertNotIn("not run", rows["colour"]["found"])
        self.assertNotIn("not run", rows["stabiliser"]["found"])
        self.assertNotEqual(rows["sync"]["found"], "no reference")
        plain = tool.handler(schema.with_defaults(tool.input_schema, {"file": self.gray}), ToolContext())
        self.assertEqual({c["check"]: c for c in plain["checks"]}["colour"]["found"], "not run")
        with self.assertRaises(Exception):
            tool.handler(schema.with_defaults(tool.input_schema, {"file": self.gray, "sync_ref": "rel.wav"}),
                         ToolContext())

    def test_cli_flags_add_the_rows(self):
        p = subprocess.run([sys.executable, str(WORKFLOW), "qc", self.gray, "--colour", "--stabiliser",
                            "--sync-ref", self.ref], capture_output=True, text=True,
                           env=dict(os.environ, RPRESOLVE_FFMPEG=dc.FFMPEG, RPRESOLVE_FFPROBE=dc.FFPROBE))
        self.assertEqual(p.stderr, "")  # exit 1 is a FAIL row (no audio here), not an error
        self.assertIn("colour", p.stdout)
        self.assertIn("stabiliser", p.stdout)
        self.assertIn("sync", p.stdout)


def _textured_video(out, shaky, frames=125):
    """320x180 grey: a fixed textured background, a large textured subject in the
    middle that jitters 3 px a frame, and with `shaky` the whole picture shaking."""
    rng = np.random.default_rng(3)

    def tex(h, w):
        r = rng.standard_normal((h, w))
        for ax in (0, 1):
            r = np.apply_along_axis(lambda v: np.convolve(v, np.ones(3) / 3, "same"), ax, r)
        return r * 40 + 128
    bg, subj = tex(180, 320), tex(100, 140)
    raw = bytearray()
    for _ in range(frames):
        f = np.roll(bg, tuple(int(v) for v in rng.integers(-3, 4, 2)), (0, 1)) if shaky else bg.copy()
        y, x = 40 + int(rng.integers(-3, 4)), 90 + int(rng.integers(-3, 4))
        f[y:y + 100, x:x + 140] = subj
        raw += np.clip(f, 0, 255).astype(np.uint8).tobytes()
    p = subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "rawvideo",
                        "-pix_fmt", "gray", "-s", "320x180", "-r", "25", "-i", "-", "-c:v", "libx264",
                        "-crf", "12", "-pix_fmt", "yuv420p", out], input=bytes(raw), capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode())


@unittest.skipUnless(_have(), "ffmpeg or numpy missing")
class StabiliserRegions(unittest.TestCase):
    """The stabiliser row reads the picture's corners, not the whole frame."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.moving = os.path.join(cls._tmp.name, "moving.mp4")
        cls.shaking = os.path.join(cls._tmp.name, "shaking.mp4")
        _textured_video(cls.moving, shaky=False)
        _textured_video(cls.shaking, shaky=True)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_a_subject_moving_in_a_steady_picture_is_not_shake(self):
        row = _row(qc.run(self.moving, stabiliser=True), "stabiliser")
        self.assertEqual(row["status"], qc.PASS, row)

    def test_a_shaking_picture_with_a_moving_subject_still_warns(self):
        row = _row(qc.run(self.shaking, stabiliser=True), "stabiliser")
        self.assertEqual(row["status"], qc.WARN, row)


if __name__ == "__main__":
    unittest.main()
