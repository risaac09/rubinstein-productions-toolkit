"""
Offline tests for rpresolve.packet. A lavfi clip run through perceive, and
hand-written audio, words and filmstrip inputs; no Resolve, no real footage.
Output goes to a temp folder outside any git tree.

Run: /usr/bin/python3 -m unittest production.tests.test_packet -v
"""

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import numpy as np
    from rpresolve import measure, packet, perceive, sidecar
except ImportError:  # numpy missing on this interpreter
    np = None

needs_numpy = unittest.skipIf(np is None, "needs numpy")
WORDS = {"segments": [{"words": [
    {"word": " early", "start": 0.2, "end": 0.5},
    {"word": " hello", "start": 4.0, "end": 4.4},
    {"word": " there.", "start": 4.5, "end": 5.0},
    {"word": " late", "start": 9.0, "end": 9.4},
]}]}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@needs_numpy
class TestMoment(unittest.TestCase):
    def test_frame_and_span(self):
        self.assertEqual(packet.moment(t_s=1.23456), {"kind": "frame", "t_s": 1.235})
        self.assertEqual(packet.moment(start_s=1, end_s=2)["kind"], "span")

    def test_rejects_mixed_or_backwards(self):
        for kw in ({}, {"t_s": 1, "start_s": 0, "end_s": 2}, {"start_s": 2, "end_s": 1}, {"start_s": 1}):
            with self.assertRaises(packet.PacketError):
                packet.moment(**kw)


@needs_numpy
@unittest.skipUnless(os.access(measure.FFMPEG if np is not None else "", os.X_OK), "ffmpeg missing")
class TestAssemble(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        cls.clip = t / "clip.mp4"
        subprocess.run([measure.FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=6",
                        "-pix_fmt", "yuv420p", str(cls.clip)], check=True)
        cls.cine = t / "cine"
        perceive.perceive(str(cls.clip), str(cls.cine), at=4.2, faces=False)
        cls.audio = t / "audio"
        cls.audio.mkdir()
        (cls.audio / "report.json").write_text(json.dumps({"lufs_i": -23.1, "flags": []}))
        (cls.audio / "spectrogram.png").write_bytes(b"\x89PNG fake")
        cls.words = t / "clip.json"
        cls.words.write_text(json.dumps(WORDS))
        cls.strip = t / "strip.png"
        cls.strip.write_bytes(b"\x89PNG strip")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def build(self, name, m=None, **kw):
        out = os.path.join(self.tmp.name, name)
        args = dict(perceive_dir=str(self.cine), audio_dir=str(self.audio), words_json=str(self.words),
                    filmstrip=str(self.strip))
        args.update(kw)
        return out, packet.assemble(out, m or packet.moment(t_s=4.2), **args)

    def test_index_is_a_sound_envelope_in_reading_order(self):
        out, idx = self.build("a")
        self.assertEqual(sidecar.validate(idx), [])
        self.assertEqual(sidecar.validate(json.load(open(os.path.join(out, "index.json")))), [])
        self.assertEqual(idx["schema"], {"name": "story.packet", "version": 1})
        self.assertEqual([p["part"] for p in idx["story"]["parts"]],
                         ["sheet", "numbers", "words", "sound", "filmstrip"])

    def test_values_match_their_sources(self):
        out, idx = self.build("b")
        src = json.load(open(self.cine / "packet.json"))
        for k in ("clip_id", "source", "transfer", "primaries"):
            self.assertEqual(idx[k], src[k])
        for p in idx["story"]["parts"]:
            self.assertEqual(sha(os.path.join(out, p["path"])), p["sha256"])
        self.assertEqual(sha(os.path.join(out, "cine/packet.json")), sha(self.cine / "packet.json"))
        self.assertEqual(sha(os.path.join(out, "cine/sheet.png")), sha(self.cine / "sheet.png"))
        self.assertEqual(sha(os.path.join(out, "sound/report.json")), sha(self.audio / "report.json"))
        self.assertTrue(os.path.exists(os.path.join(out, "sound/spectrogram.png")))

    def test_words_are_the_window_verbatim(self):
        out, _ = self.build("c")  # frame at 4.2, pad 2: 2.2 to 6.2
        w = json.load(open(os.path.join(out, "words.json")))
        self.assertEqual([x["word"] for x in w["words"]], ["hello", "there."])
        self.assertEqual(w["words"][0], {"start": 4.0, "end": 4.4, "word": "hello"})
        self.assertIn("hello there.", w["phrases"][0])

    def test_span_takes_overlapping_words_only(self):
        out, idx = self.build("d", packet.moment(start_s=0, end_s=4.3), perceive_dir=None)
        w = json.load(open(os.path.join(out, "words.json")))
        self.assertEqual([x["word"] for x in w["words"]], ["early", "hello"])
        self.assertEqual(idx["story"]["moment"]["kind"], "span")

    def test_only_given_parts_are_listed(self):
        out, idx = self.build("e", audio_dir=None, filmstrip=None)
        self.assertEqual([p["part"] for p in idx["story"]["parts"]], ["sheet", "numbers", "words"])
        self.assertFalse(os.path.exists(os.path.join(out, "sound")))

    def test_words_alone_needs_the_source(self):
        out = os.path.join(self.tmp.name, "f")
        with self.assertRaises(packet.PacketError):
            packet.assemble(out, packet.moment(t_s=4), words_json=str(self.words))
        idx = packet.assemble(out, packet.moment(t_s=4), words_json=str(self.words), source=str(self.clip))
        self.assertEqual(sidecar.validate(idx), [])

    def test_enveloped_audio_for_another_clip_is_refused_and_leaves_nothing(self):
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        doc = sidecar.header("sound.audio-report", 1, str(self.clip), clip_id="X-aaaaaa/other")
        doc["sound"] = {}
        (other / "report.json").write_text(json.dumps(doc))
        out = os.path.join(self.tmp.name, "g")
        with self.assertRaises(packet.PacketError):
            packet.assemble(out, packet.moment(t_s=4), perceive_dir=str(self.cine), audio_dir=str(other))
        self.assertFalse(os.path.exists(out))

    def test_perceive_frame_must_be_the_moment(self):
        out = os.path.join(self.tmp.name, "m")
        with self.assertRaises(packet.PacketError):
            packet.assemble(out, packet.moment(t_s=1.0), perceive_dir=str(self.cine))
        packet.assemble(out, packet.moment(start_s=4.0, end_s=5.0), perceive_dir=str(self.cine))

    def test_malformed_perceive_packet_is_a_packet_error(self):
        bad = Path(self.tmp.name) / "bad"
        bad.mkdir()
        (bad / "packet.json").write_text("{}")
        with self.assertRaises(packet.PacketError):
            packet.assemble(os.path.join(self.tmp.name, "n"), packet.moment(t_s=4.2), perceive_dir=str(bad))

    def test_audio_must_be_a_sound_sidecar(self):
        out = os.path.join(self.tmp.name, "o")
        doc = json.load(open(self.cine / "packet.json"))
        wrong = Path(self.tmp.name) / "wrong"
        wrong.mkdir()
        (wrong / "report.json").write_text(json.dumps(doc))
        with self.assertRaises(packet.PacketError):
            packet.assemble(out, packet.moment(t_s=4.2), perceive_dir=str(self.cine), audio_dir=str(wrong))
        self.assertFalse(os.path.exists(out))

    def test_refuses_git_tree_nonempty_and_no_input(self):
        repo = Path(__file__).resolve().parent.parent
        with self.assertRaises(packet.PacketError):
            packet.assemble(str(repo / "nope"), packet.moment(t_s=1), perceive_dir=str(self.cine))
        full = Path(self.tmp.name) / "full"
        full.mkdir()
        (full / "x").write_text("x")
        with self.assertRaises(packet.PacketError):
            packet.assemble(str(full), packet.moment(t_s=1), perceive_dir=str(self.cine))
        with self.assertRaises(packet.PacketError):
            packet.assemble(os.path.join(self.tmp.name, "h"), packet.moment(t_s=1))

    def test_cli(self):
        out = os.path.join(self.tmp.name, "cli")
        self.assertEqual(packet.main([out, "--at", "4.2", "--perceive", str(self.cine), "--words", str(self.words)]), 0)
        with redirect_stderr(io.StringIO()):
            self.assertEqual(packet.main([out + "2", "--perceive", str(self.cine)]), 2)


if __name__ == "__main__":
    unittest.main()
