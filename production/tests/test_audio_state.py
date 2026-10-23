"""
audio-state: the read-only audio report, the command, its comparison with a
destination and the deliver-check hook. Fake Resolve objects only; Resolve is
never touched and no render is started.

Run: /usr/bin/python3 -m unittest -v production.tests.test_audio_state
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import resolve_fakes as rf  # noqa: E402
from rpresolve import audiostate, cli, cmd_audio_state, cmd_deliver_check, delivercheck as dc  # noqa: E402

DEST = {"key": "t", "audio": {"codec": "lpcm", "resolve_codec": "lpcm", "sample_rate": 48000,
                              "bit_depth": 24}}


class ClipWithMapping(rf.Clip):
    def GetAudioMapping(self):
        return json.dumps({"track_count": 1, "embedded_audio_channels": 2})


def build(jobs=()):
    clip = ClipWithMapping("a.wav", "clip-1")
    item = rf.Item("a.wav", 0, 100, clip=clip,
                   props={"AudioVolume": -3.0, "AudioVoiceIsolationEnabled": True,
                          "AudioVoiceIsolationAmount": 60, "Pan": 1})
    tl = rf.Timeline("Cut", "tl-1", tracks={"video": [[]], "audio": [[item], []]})
    p = rf.Project(timelines=[tl], current=tl, jobs=jobs)
    p.settings["limitAudioMeterLUFS"] = "-23"
    return p, tl


class TestRead(unittest.TestCase):
    def test_reports_tracks_items_mapping_and_loudness_settings(self):
        p, tl = build()
        s = audiostate.read(p, tl)
        self.assertEqual([(t["index"], t["type"], t["items"]) for t in s["tracks"]],
                         [(1, "stereo", 1), (2, "stereo", 0)])
        props = s["items"][0]["properties"]
        self.assertEqual(props, {"AudioVolume": -3.0, "AudioVoiceIsolationEnabled": True,
                                 "AudioVoiceIsolationAmount": 60})  # Pan is not an Audio* key
        self.assertEqual(s["items"][0]["audio_mapping"]["embedded_audio_channels"], 2)
        self.assertEqual(s["loudness_settings"], {"limitAudioMeterLUFS": "-23"})
        self.assertEqual(s["render"]["format_codec"], {"format": "mov", "codec": "ProRes422HQ"})

    def test_says_what_it_cannot_read_and_never_calls_anything_that_writes(self):
        p, tl = build()
        before = len(rf.mutating_calls())
        s = audiostate.read(p, tl)
        self.assertEqual(len(rf.mutating_calls()), before)
        text = audiostate.format_report(s)
        self.assertIn("the API cannot read", text)
        for word in ("mixer", "EQ", "Deliver page's Audio tab", "loudness"):
            self.assertIn(word, text)
        self.assertIsNone(s["render"]["settings"])  # no GetRenderSettings on this Resolve


class TestCompare(unittest.TestCase):
    def job(self, **kw):
        return {"JobId": "j1", "TimelineName": "Cut", "AudioCodec": "lpcm",
                "AudioSampleRate": 48000, "AudioBitDepth": 24, **kw}

    def rows(self, **kw):
        p, tl = build(jobs=[self.job(**kw)])
        return {r["check"]: r for r in audiostate.compare(audiostate.read(p, tl), DEST)}

    def test_matching_job_passes(self):
        self.assertEqual({r["status"] for r in self.rows().values()}, {dc.PASS})

    def test_sample_rate_and_codec_mismatches_fail(self):
        rows = self.rows(AudioSampleRate=44100, AudioCodec="aac")
        self.assertEqual(rows["job_audio_sample_rate"]["status"], dc.FAIL)
        self.assertEqual(rows["job_audio_codec"]["status"], dc.FAIL)
        self.assertEqual(rows["job_audio_bit_depth"]["status"], dc.PASS)

    def test_no_job_is_a_skip_not_a_pass(self):
        p, tl = build()
        rows = audiostate.compare(audiostate.read(p, tl), DEST)
        self.assertEqual({r["status"] for r in rows}, {dc.SKIP})


class TestCommands(unittest.TestCase):
    def setUp(self):
        self.p, self.tl = build(jobs=[{"JobId": "j1", "TimelineName": "Cut",
                                       "AudioCodec": "lpcm", "AudioSampleRate": 44100,
                                       "AudioBitDepth": 24}])
        self.resolve = rf.Resolve(self.p)
        self.tmp = tempfile.mkdtemp()  # outside any git tree
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def run_cmd(self, argv):
        class A:  # the parsed arguments
            timeline, out, json = None, None, False
        a = A()
        a.__dict__.update(argv)
        out = io.StringIO()
        with mock.patch.object(cli, "get_resolve", return_value=self.resolve), \
                mock.patch.object(cli, "get_project", return_value=self.p), \
                contextlib.redirect_stdout(out):
            status = cmd_audio_state.run(a)
        return status, out.getvalue()

    def test_command_prints_the_report(self):
        status, out = self.run_cmd({})
        self.assertEqual(status, 0)
        self.assertIn("audio state of 'Cut'", out)

    def test_command_writes_json_and_refuses_a_git_tree(self):
        path = os.path.join(self.tmp, "state.json")
        self.assertEqual(self.run_cmd({"out": path})[0], 0)
        self.assertEqual(json.loads(Path(path).read_text())["timeline"], "Cut")
        here = str(HERE / "state.json")  # inside this repository
        self.assertEqual(self.run_cmd({"out": here})[0], 1)
        self.assertFalse(os.path.exists(here))

    def test_unknown_timeline_is_an_error_status(self):
        self.assertEqual(self.run_cmd({"timeline": "nope"})[0], 1)

    def test_deliver_check_adds_the_job_rows(self):
        path = os.path.join(self.tmp, "state.json")
        self.run_cmd({"out": path})
        base = {"file": "/x.mov", "destination": "t", "checks": [], "counts": {}, "status": "pass"}

        class A:
            file, dest, fps, size, no_loudness, json = "/x.mov", "t", None, None, True, True
            audio_state = path
        out = io.StringIO()
        with mock.patch.object(dc, "check", return_value=dict(base, counts={dc.PASS: 0, dc.FAIL: 0, dc.SKIP: 0})), \
                mock.patch("rpresolve.deliver.destination", return_value=DEST), \
                mock.patch("rpresolve.deliver.subfolders", return_value={}), \
                mock.patch.object(cli, "deliver_config", return_value={}), \
                contextlib.redirect_stdout(out):
            status = cmd_deliver_check.run(A())
        r = json.loads(out.getvalue())
        self.assertEqual(status, 1)  # the queued job says 44100, the destination 48000
        self.assertEqual([c["check"] for c in r["checks"] if c["status"] == dc.FAIL],
                         ["job_audio_sample_rate"])
        self.assertEqual(r["counts"][dc.FAIL], 1)

    def test_load_refuses_a_file_that_is_not_a_report(self):
        bad = os.path.join(self.tmp, "bad.json")
        Path(bad).write_text("{}")
        with self.assertRaises(ValueError):
            audiostate.load(bad)


if __name__ == "__main__":
    unittest.main()
