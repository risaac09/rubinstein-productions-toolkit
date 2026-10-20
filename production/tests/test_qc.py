"""
Tests for rpresolve.qc and the `qc` command on media made here with ffmpeg
lavfi sources (testsrc2, sine, a black segment, a frozen segment, a silent
gap, a loud and a DC-offset tone; 160x90, 10 fps, a few seconds, PCM audio
in .mov). Every row has a pass and a fail case, and the parsers are pinned
on canned ffmpeg log text. Skipped where ffmpeg, ffprobe or libx264 is
missing.

Run: /usr/bin/python3 -m unittest production.tests.test_qc -v
"""

import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import config as rpconfig, deliver, delivercheck as dc, qc  # noqa: E402

WORKFLOW = Path(__file__).resolve().parent.parent / "resolve_workflow.py"
CFG = rpconfig.load_config("/nonexistent/config.json")

VID = "testsrc2=s=160x90:r=10:d={d}"
TONE = "sine=f=1000:r=48000:d={d},volume=0.5"


def _have_tools():
    if not all(os.access(t, os.X_OK) for t in (dc.FFMPEG, dc.FFPROBE)):
        return False
    enc = subprocess.run([dc.FFMPEG, "-hide_banner", "-encoders"], stdin=subprocess.DEVNULL,
                         capture_output=True, text=True).stdout
    return "libx264" in enc


def _make(out, graph):
    """A .mov from a filter_complex graph that ends in [v] and [a]."""
    proc = subprocess.run(
        [dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-filter_complex", graph,
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "pcm_s16le", out], stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)


def _cat(parts, kind):
    """concat of parts (filter strings) as one stream: kind 'v' or 'a'."""
    n = len(parts)
    labels = "".join(f"[{kind}{i}]" for i in range(n))
    body = ";".join(f"{p}[{kind}{i}]" for i, p in enumerate(parts))
    flag = "v=1:a=0" if kind == "v" else "v=0:a=1"
    return f"{body};{labels}concat=n={n}:{flag}[{kind}]"


@unittest.skipUnless(_have_tools(), "needs ffmpeg and ffprobe with libx264")
class QcTable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        t = cls._tmp.name
        cls.f = {k: os.path.join(t, k + ".mov") for k in
                 ("clean", "black", "freeze", "silent", "loud", "dc", "mute")}
        clean_v, clean_a = VID.format(d=3), TONE.format(d=3)
        _make(cls.f["clean"], f"{clean_v}[v];{clean_a}[a]")
        # picture: 1 s of test pattern, 1 s of black, 1 s of pattern again (2 cuts)
        _make(cls.f["black"], _cat([VID.format(d=1), "color=c=black:s=160x90:r=10:d=1",
                                    VID.format(d=1)], "v") + ";" + TONE.format(d=3) + "[a]")
        # picture: 1 s of pattern, then 3 s of a flat colour (1 cut, one 3 s freeze)
        _make(cls.f["freeze"], _cat([VID.format(d=1), "color=c=blue:s=160x90:r=10:d=3"], "v")
              + ";" + TONE.format(d=4) + "[a]")
        # sound: 1 s of tone, 1 s of digital silence, 1 s of tone
        _make(cls.f["silent"], f"{clean_v}[v];" + _cat(
            [TONE.format(d=1), "anullsrc=r=48000:cl=mono,atrim=duration=1", TONE.format(d=1)],
            "a"))
        _make(cls.f["loud"], f"{clean_v}[v];aevalsrc=1.0*sin(2*PI*1000*t):s=48000:d=3[a]")
        _make(cls.f["dc"], f"{clean_v}[v];aevalsrc=0.3*sin(2*PI*1000*t)+0.2:s=48000:d=3[a]")
        # a picture with no sound at all
        subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
                        "-i", clean_v, "-c:v", "libx264", "-pix_fmt", "yuv420p", cls.f["mute"]],
                       stdin=subprocess.DEVNULL, check=True)
        cls.shots3 = cls._shots(t, 3)
        cls.shots5 = cls._shots(t, 5)

    @staticmethod
    def _shots(tmp, n):
        path = os.path.join(tmp, f"shots{n}.csv")
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["shot", "note"])
            for i in range(n):
                w.writerow([f"S{i + 1}", "x"])
        return path

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def rows(self, key, **kw):
        r = qc.run(self.f[key], **kw)
        return r, {x["check"]: x for x in r["checks"]}

    def test_clean_file_passes_every_ffmpeg_row(self):
        r, by = self.rows("clean", expected_duration=3.0, shots=1)
        for name in ("duration", "blackdetect", "freezedetect", "scdet", "true_peak",
                     "astats_peak", "astats_dc", "silencedetect"):
            self.assertEqual(by[name]["status"], qc.PASS, (name, by[name]))
        self.assertEqual(by["loudness"]["status"], qc.SKIP)  # no destination, no target
        self.assertEqual(by["deliver_check"]["status"], qc.SKIP)
        self.assertEqual(r["status"], "pass")

    def test_blackdetect_fails_on_a_black_segment(self):
        r, by = self.rows("black")
        self.assertEqual(by["blackdetect"]["status"], qc.FAIL)
        self.assertIn("1.00s", by["blackdetect"]["found"])
        self.assertEqual(r["status"], "fail")

    def test_blackdetect_threshold_override(self):
        th = qc.parse_overrides(["black_min_s=2"])
        _, by = self.rows("black", thresholds=th)
        self.assertEqual(by["blackdetect"]["status"], qc.PASS)

    def test_freezedetect_fails_on_a_frozen_segment(self):
        _, by = self.rows("freeze")
        self.assertEqual(by["freezedetect"]["status"], qc.FAIL)
        self.assertIn("1.00s to the end", by["freezedetect"]["found"])

    def test_scdet_against_the_shot_list(self):
        _, by = self.rows("black", shots=3)  # two cuts, three shots
        self.assertEqual(by["scdet"]["status"], qc.PASS)
        self.assertEqual(by["scdet"]["found"], "2 cuts")
        _, by = self.rows("black", shots=5)
        self.assertEqual(by["scdet"]["status"], qc.FAIL)
        _, by = self.rows("black", shots=5, thresholds=qc.parse_overrides(["scdet_tolerance=2"]))
        self.assertEqual(by["scdet"]["status"], qc.PASS)
        _, by = self.rows("black")
        self.assertEqual(by["scdet"]["status"], qc.SKIP)  # no shot list

    def test_shot_count_reads_rows_after_the_header(self):
        self.assertEqual(qc.shot_count(self.shots3), 3)
        empty = os.path.join(self._tmp.name, "empty.csv")
        Path(empty).write_text("shot\n")
        with self.assertRaises(dc.CheckError):
            qc.shot_count(empty)

    def test_duration_against_the_lock(self):
        _, by = self.rows("clean", expected_duration=3.0)
        self.assertEqual(by["duration"]["status"], qc.PASS)
        _, by = self.rows("clean", expected_duration=4.0)
        self.assertEqual(by["duration"]["status"], qc.FAIL)

    def test_loudness_and_true_peak_against_a_destination(self):
        dest = deliver.destination(CFG, "youtube_16x9")
        _, by = self.rows("clean", dest=dest)  # a -24 LUFS tone against the -14 target
        self.assertEqual(by["loudness"]["status"], qc.FAIL)
        self.assertEqual(by["true_peak"]["status"], qc.PASS)

    def test_true_peak_fails_on_a_full_scale_tone(self):
        _, by = self.rows("loud")
        self.assertEqual(by["true_peak"]["status"], qc.FAIL)
        self.assertEqual(by["astats_peak"]["status"], qc.FAIL)

    def test_astats_dc_fails_on_an_offset(self):
        _, by = self.rows("dc")
        self.assertEqual(by["astats_dc"]["status"], qc.FAIL)
        self.assertEqual(by["astats_peak"]["status"], qc.PASS)

    def test_silencedetect_fails_on_a_gap(self):
        _, by = self.rows("silent")
        self.assertEqual(by["silencedetect"]["status"], qc.FAIL)
        self.assertIn("1.00s for 1.00s", by["silencedetect"]["found"])

    def test_a_file_with_no_av_streams_fails(self):
        with mock.patch.object(dc, "probe", return_value={"streams": [], "format": {}}):
            r = qc.run(self.f["clean"])
        self.assertEqual(r["checks"][0]["check"], "streams")
        self.assertEqual(r["status"], "fail")

    def test_no_audio_skips_the_audio_rows(self):
        r, by = self.rows("mute")
        for name in ("loudness", "true_peak", "astats_peak", "astats_dc", "silencedetect"):
            self.assertEqual(by[name]["status"], qc.SKIP, name)
        self.assertEqual(r["status"], "pass")

    def test_fail_rows_come_first_and_deliver_check_rows_are_joined(self):
        dest = deliver.destination(CFG, "youtube_16x9")
        r = qc.run(self.f["black"], dest, config_folders=deliver.subfolders(CFG))
        statuses = [x["status"] for x in r["checks"]]
        self.assertEqual(statuses, sorted(statuses, key=[qc.FAIL, qc.PASS, qc.SKIP].index))
        self.assertEqual(statuses[0], qc.FAIL)
        sources = {x["source"] for x in r["checks"]}
        self.assertEqual(sources, {"deliver_check", "ffmpeg"})
        self.assertIn("video_codec", {x["check"] for x in r["checks"]})

    def test_override_rejects_unknown_keys_and_non_numbers(self):
        with self.assertRaises(ValueError):
            qc.parse_overrides(["nope=1"])
        with self.assertRaises(ValueError):
            qc.parse_overrides(["black_min_s=soon"])
        self.assertEqual(qc.parse_overrides(["black_min_s=2"])["black_min_s"], 2.0)
        self.assertEqual(qc.THRESHOLDS["black_min_s"], 0.5)  # the defaults are not mutated

    def cli(self, *args, env=None):
        return subprocess.run([sys.executable, str(WORKFLOW), "qc", *args],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              env={**os.environ, **(env or {})})

    def test_cli_exit_codes_and_json(self):
        ok = self.cli(self.f["clean"], "--json", "--duration", "3", "--shots", self.shots3,
                      "--set", "scdet_tolerance=2")
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertEqual(json.loads(ok.stdout)["status"], "pass")
        bad = self.cli(self.f["black"], "--json")
        self.assertEqual(bad.returncode, 1)
        self.assertEqual(json.loads(bad.stdout)["checks"][0]["status"], qc.FAIL)
        text = self.cli(self.f["black"])
        self.assertIn("FAIL  blackdetect", text.stdout)
        self.assertTrue(text.stdout.rstrip().endswith("FAIL"))

    def test_cli_errors(self):
        self.assertEqual(self.cli(os.path.join(self._tmp.name, "missing.mov")).returncode, 1)
        self.assertEqual(self.cli(self.f["clean"], "--set", "bogus=1").returncode, 1)
        self.assertEqual(self.cli(self.f["clean"], "--dest", "nowhere").returncode, 1)
        self.assertEqual(self.cli(self.f["clean"], "--fps", "25").returncode, 1)
        gone = self.cli(self.f["clean"], env={"RPRESOLVE_FFMPEG": "/nonexistent/ffmpeg"})
        self.assertEqual(gone.returncode, 2, gone.stderr)


class Parsers(unittest.TestCase):
    def test_parse_black_freeze_scdet_silence_astats(self):
        log = "\n".join([
            "[blackdetect @ 0x1] black_start:2 black_end:3 black_duration:1",
            "[Parsed_freezedetect_1 @ 0x1] lavfi.freezedetect.freeze_start: 2",
            "[Parsed_freezedetect_1 @ 0x1] lavfi.freezedetect.freeze_duration: 3",
            "[Parsed_scdet_2 @ 0x1] lavfi.scd.score: 34.3, lavfi.scd.time: 2",
            "[Parsed_scdet_2 @ 0x1] lavfi.scd.score: 3.0, lavfi.scd.time: 2.1",
            "[silencedetect @ 0x1] silence_start: 1.5",
            "[silencedetect @ 0x1] silence_end: 2.5 | silence_duration: 1",
            "[silencedetect @ 0x1] silence_start: 4",
            "[Parsed_astats_1 @ 0x1] Channel: 1",
            "[Parsed_astats_1 @ 0x1] Peak level dB: -99.0",
            "[Parsed_astats_1 @ 0x1] Overall",
            "[Parsed_astats_1 @ 0x1] DC offset: -0.020000",
            "[Parsed_astats_1 @ 0x1] Peak level dB: -3.098039"])
        self.assertEqual(qc.parse_black(log), [(2.0, 3.0, 1.0)])
        self.assertEqual(qc.parse_freeze(log), [(2.0, 3.0)])
        self.assertEqual(qc.parse_freeze("freeze_start: 1"), [(1.0, None)])
        self.assertEqual(qc.parse_scdet(log, 10), [2.0])
        self.assertEqual(qc.parse_silence(log), [(1.5, 1.0), (4.0, None)])
        self.assertEqual(qc.parse_astats(log), {"peak_db": -3.098039, "dc": -0.02})
        self.assertEqual(qc.parse_astats("nothing"), {"peak_db": None, "dc": None})


if __name__ == "__main__":
    unittest.main()
