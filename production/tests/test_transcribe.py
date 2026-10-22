"""
Tests for rpresolve.transcribe: the fake backend end to end, the JSON read by
cutlist, pacing and captionlint, and the missing-backend messages.
Synthetic and deterministic; no numpy, ffmpeg or model needed.

Run: python3 -m unittest production.tests.test_transcribe -v
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import captionlint, cutlist, pacing, transcribe as tr  # noqa: E402

SEGS = {"segments": [
    {"start": 2.0, "end": 3.6, "text": " and a second line", "words": [
        {"word": " and", "start": 2.0, "end": 2.2}, {"word": " a", "start": 2.2, "end": 2.3},
        {"word": " second", "start": 2.3, "end": 2.9}, {"word": " line", "start": 2.9, "end": 3.6}]},
    {"start": 0.0, "end": 1.5, "text": " Hello there", "words": [
        {"word": " Hello", "start": 0.0, "end": 0.7}, {"word": " there", "start": 0.8, "end": 1.5}]},
    {"start": 5.0, "end": 6.0, "text": "no words here"},
    {"start": 7.0, "end": 7.5, "text": "  "},
]}


def cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = tr.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class FakeEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src = os.path.join(self.tmp.name, "segs.json")
        with open(self.src, "w") as f:
            json.dump(SEGS, f)
        self.media = os.path.join(self.tmp.name, "a.wav")
        self.out = {k: os.path.join(self.tmp.name, "t." + k) for k in ("json", "srt", "txt")}
        code, _, err = cli(self.media, "--backend", "fake", "--source", self.src,
                           "--language", "en", "--out", self.out["json"],
                           "--srt", self.out["srt"], "--txt", self.out["txt"])
        self.assertEqual((code, err), (0, ""))

    def test_json_sorted_and_clean(self):
        with open(self.out["json"]) as f:
            doc = json.load(f)
        self.assertEqual(doc["language"], "en")
        self.assertEqual(doc["transcribe"], {"backend": "fake"})
        self.assertEqual([s["text"] for s in doc["segments"]],
                         ["Hello there", "and a second line", "no words here"])
        self.assertNotIn("words", doc["segments"][2])
        self.assertEqual(doc["text"], "Hello there and a second line no words here")

    def test_cutlist_and_pacing_read_it(self):
        words = cutlist.load_words(self.out["json"])
        self.assertEqual([w["word"] for w in words], ["Hello", "there", "and", "a", "second", "line"])
        rep = pacing.build(words_path=self.out["json"], captions_path=self.out["srt"])
        self.assertEqual(rep["words"], 6)
        self.assertEqual(rep["captions"]["cues"], 3)

    def test_srt_and_captionlint(self):
        doc = captionlint.load(self.out["srt"])
        self.assertEqual(doc["problems"], [])
        self.assertEqual(len(doc["cues"]), 3)
        self.assertEqual(doc["cues"][0][:2], (0.0, 1.5))
        self.assertNotEqual(captionlint.lint(doc)["status"], "fail")

    def test_txt(self):
        with open(self.out["txt"]) as f:
            self.assertEqual(f.read(), "Hello there\nand a second line\nno words here\n")

    def test_stdout_when_no_out(self):
        code, out, _ = cli(self.media, "--backend", "fake", "--source", self.src)
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)["segments"]), 3)


class FakeInput(unittest.TestCase):
    def run_fake(self, content):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "s.json")
            with open(src, "w") as f:
                f.write(content)
            return cli("x.wav", "--backend", "fake", "--source", src)

    def test_bare_list_accepted(self):
        code, out, _ = self.run_fake('[{"start": 0, "end": 1, "text": "hi"}]')
        self.assertEqual(code, 0)

    def test_bad_inputs_exit_2(self):
        for content, needle in (("nope", "cannot read"), ('{"a": 1}', "no segments"),
                                ('[{"text": "x"}]', "no start and end"),
                                ('[{"start": 2, "end": 1, "text": "x"}]', "ends before"),
                                ("[]", "no speech"),
                                ('[{"start": "a", "end": 1, "text": "x"}]', "not usable")):
            code, _, err = self.run_fake(content)
            self.assertEqual(code, 2, content)
            self.assertIn(needle, err)

    def test_missing_source(self):
        code, _, err = cli("x.wav", "--backend", "fake")
        self.assertEqual(code, 2)
        self.assertIn("--source", err)


class MissingBackends(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.media = os.path.join(self.tmp.name, "a.wav")
        Path(self.media).write_bytes(b"")
        env = {k: v for k, v in os.environ.items() if not k.startswith("RPRESOLVE_WHISPER")}
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def test_mlx_not_installed_names_d9(self):
        with mock.patch.dict(sys.modules, {"mlx_whisper": None}):
            code, _, err = cli(self.media, "--backend", "mlx-whisper")
        self.assertEqual(code, 2)
        self.assertIn("not importable", err)
        self.assertIn("D9", err)

    def test_mlx_no_model_downloads_nothing(self):
        fake = mock.Mock()
        with mock.patch.dict(sys.modules, {"mlx_whisper": fake}):
            code, _, err = cli(self.media, "--backend", "mlx-whisper")
            code2, _, err2 = cli(self.media, "--backend", "mlx-whisper", "--model",
                                 "mlx-community/not-cached-here")
        self.assertEqual((code, code2), (2, 2))
        self.assertIn("no model", err)
        self.assertIn("not on disk", err2)
        self.assertIn("D9", err2)
        fake.transcribe.assert_not_called()

    def test_mlx_with_local_model_runs(self):
        fake = mock.Mock()
        fake.transcribe.return_value = {"segments": [
            {"start": 0, "end": 1, "text": " hi", "words": [{"word": " hi", "start": 0, "end": 1}]}]}
        with mock.patch.dict(sys.modules, {"mlx_whisper": fake}):
            code, out, _ = cli(self.media, "--backend", "mlx-whisper", "--model", self.tmp.name,
                               "--language", "en")
        self.assertEqual(code, 0)
        _, kw = fake.transcribe.call_args
        self.assertEqual((kw["path_or_hf_repo"], kw["word_timestamps"], kw["language"]),
                         (self.tmp.name, True, "en"))
        self.assertEqual(json.loads(out)["segments"][0]["words"][0]["word"], "hi")

    def test_whisper_cpp_absent(self):
        with mock.patch.object(tr.shutil, "which", return_value=None):
            code, _, err = cli(self.media, "--backend", "whisper.cpp")
        self.assertEqual(code, 2)
        self.assertIn("whisper-cli", err)

    def test_whisper_cpp_needs_model(self):
        with mock.patch.object(tr.shutil, "which", return_value="/bin/whisper-cli"):
            code, _, err = cli(self.media, "--backend", "whisper.cpp")
        self.assertEqual(code, 2)
        self.assertIn("ggml model", err)

    def test_resolve_stub(self):
        code, _, err = cli(self.media, "--backend", "resolve")
        self.assertEqual(code, 2)
        self.assertIn("later backend", err)

    def test_missing_media(self):
        code, _, err = cli(os.path.join(self.tmp.name, "gone.wav"), "--backend", "mlx-whisper")
        self.assertEqual(code, 2)
        self.assertIn("no such media", err)


class WhisperCppShape(unittest.TestCase):
    def test_tokens_join_into_words(self):
        data = {"transcription": [{"offsets": {"from": 0, "to": 1000}, "text": " Hello world",
                "tokens": [{"text": "[_BEG_]", "offsets": {"from": 0, "to": 0}},
                           {"text": " Hel", "offsets": {"from": 0, "to": 300}},
                           {"text": "lo", "offsets": {"from": 300, "to": 500}},
                           {"text": " world", "offsets": {"from": 500, "to": 1000}}]}]}
        seg = tr.normalize(tr._from_cpp(data))[0]
        self.assertEqual([(w["word"], w["start"], w["end"]) for w in seg["words"]],
                         [("Hello", 0.0, 0.5), ("world", 0.5, 1.0)])


if __name__ == "__main__":
    unittest.main()
