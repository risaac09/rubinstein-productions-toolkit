"""
Tests for rpresolve.interchange: manifests built here, and where a source
must be probed, clips made here with ffmpeg lavfi. No real media, no Resolve.

Run: /usr/bin/python3 -m unittest discover production/tests -p test_interchange.py -v
"""

import contextlib
import io
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rpresolve import captions, cutlist, detect
from rpresolve import delivercheck as dc
from rpresolve import interchange as ic

NTSC30 = Fraction(30000, 1001)
NTSC24 = Fraction(24000, 1001)
NTSC60 = Fraction(60000, 1001)
TC = r"(\d\d):(\d\d):(\d\d)[:;](\d\d)"
# CMX 3600 columns: event, two spaces, 8-character reel, channel field of 5,
# C, eight spaces, then source in/out and record in/out.
EVENT = re.compile(r"^(\d{3})  ([A-Z0-9_ ]{8}) (V {4}|AA {3}) C {8}"
                   r"(\d\d:\d\d:\d\d[:;]\d\d) (\d\d:\d\d:\d\d[:;]\d\d) "
                   r"(\d\d:\d\d:\d\d[:;]\d\d) (\d\d:\d\d:\d\d[:;]\d\d)$")
# The CMX reader of the rhythm report (open PR), copied here so the two stay compatible.
RHYTHM_EVENT = re.compile(r"^\s*\d+\s+\S+\s+(\S+)\s+\S+(?:\s+\d+)?\s+" + r"\s+".join([TC] * 4))
NO_MEDIA = "/nonexistent-interchange-test/clip one.mov"


def manifest(spans, fps=None, source=NO_MEDIA, name="cut", extra_clips=()):
    m = {"version": 1, "speech": False, "source_path": source,
         "clips": [{"name": name, "spans": spans}] + list(extra_clips)}
    if fps is not None:
        m["fps"] = fps
    return cutlist.validate_manifest(m, allow_no_speech=True)


def media(rate, tc=None, frames=None, audio=True, size=(1920, 1080)):
    """A probe() result without a file."""
    return {"rate": rate, "tc": tc, "frames": frames, "size": size,
            "audio": {"streams": 1, "channels": 2, "rate": 48000} if audio else None}


def tc_to_frames(tc, rate):
    return int(captions.parse_timecode(tc, rate) * rate)


def events(text):
    """[(channel, src_in, src_out, rec_in, rec_out)] as timecode strings."""
    out = []
    for line in text.splitlines():
        m = EVENT.match(line)
        if m:
            out.append((m.group(3).strip(),) + m.groups()[3:])
    return out


def fcp_seconds(value):
    assert value.endswith("s"), value
    return Fraction(value[:-1])


TIME_ATTRS = ("offset", "start", "duration", "tcStart", "audioStart", "audioDuration",
              "frameDuration")


def make_clip(path, seconds=4.0, rate="30000/1001", timecode="01:02:03;04", audio=True):
    """An H.264 clip with a tone and a start timecode, from lavfi."""
    cmd = [dc.FFMPEG, "-v", "error", "-y", "-f", "lavfi",
           "-i", f"testsrc2=size=64x36:rate={rate}:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=1000:sample_rate=48000:duration={seconds}",
                "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-shortest"]
    if timecode:
        cmd += ["-timecode", timecode]
    subprocess.run(cmd + [path], stdin=subprocess.DEVNULL, check=True)
    return path


class TestRateAndTimecode(unittest.TestCase):
    def test_parse_rate(self):
        for given, want in (("29.97", NTSC30), (29.97, NTSC30), (23.976, NTSC24), ("59.94", NTSC60),
                            (25.0, Fraction(25)), ("30000/1001", NTSC30),
                            (float(NTSC30), NTSC30), (12.5, Fraction(25, 2)), (24, Fraction(24))):
            self.assertEqual(ic.parse_rate(given), want, given)
        with self.assertRaises(ic.InterchangeError):
            ic.parse_rate("fast")

    def test_drop_frame_across_minute_boundaries(self):
        known = {NTSC30: {0: "00:00:00;00", 1799: "00:00:59;29", 1800: "00:01:00;02",
                          3597: "00:01:59;29", 3598: "00:02:00;02", 17981: "00:09:59;29",
                          17982: "00:10:00;00", 17983: "00:10:00;01", 19781: "00:10:59;29",
                          19782: "00:11:00;02", 107892: "01:00:00;00"},
                 NTSC60: {3599: "00:00:59;59", 3600: "00:01:00;04", 35963: "00:09:59;59",
                          35964: "00:10:00;00", 215784: "01:00:00;00"}}
        for rate, table in known.items():
            for frames, label in table.items():
                self.assertEqual(ic.timecode(frames, rate, True), label, (rate, frames))
                self.assertEqual(tc_to_frames(label, rate), frames)
        # every frame around the first eleven minute boundaries reads back to itself
        for rate in (NTSC30, NTSC60):
            per_min = round(rate) * 60
            for minute in range(1, 12):
                for f in range(minute * per_min - 12, minute * per_min + 12):
                    self.assertEqual(tc_to_frames(ic.timecode(f, rate, True), rate), f)

    def test_non_drop(self):
        self.assertEqual(ic.timecode(90000, Fraction(25), False), "01:00:00:00")
        self.assertEqual(ic.timecode(86400, NTSC24, False), "01:00:00:00")
        self.assertEqual(ic.timecode(1800, NTSC30, False), "00:01:00:00")

    def test_fcp_time(self):
        self.assertEqual(ic.fcp_time(90, NTSC30), "90090/30000s")
        self.assertEqual(ic.fcp_time(30000, NTSC30), "1001s")
        self.assertEqual(ic.fcp_time(0, NTSC30), "0s")
        self.assertEqual(ic.fcp_time(50, Fraction(25)), "2s")
        self.assertEqual(ic.fcp_time(13, Fraction(25)), "13/25s")

    def test_reel(self):
        self.assertEqual(ic.reel("clip one.mov"), "CLIPONE")
        self.assertEqual(ic.reel("A001_C002_long-name.mov"), "A001_C00")
        self.assertEqual(ic.reel("--.mov"), "AX")


class TestNoDrift(unittest.TestCase):
    """Over an hour of cut, every record time is the sum of whole-frame
    spans, so nothing creeps however long the timeline runs."""

    @staticmethod
    def spans(seed):
        rng, t, out, total = random.Random(seed), 0.0, [], 0.0
        while total < 3700:
            a = round(t + rng.uniform(0.1, 3.0), 4)
            b = round(a + rng.uniform(5.0, 20.0), 4)
            out.append({"in": a, "out": b})
            total += b - a
            t = b
        return out

    def test_hour_at_three_rates(self):
        for rate, fps, drop in ((NTSC24, "23.976", False), (Fraction(25), 25, False),
                                (NTSC30, 29.97, True)):
            with self.subTest(rate=str(rate)):
                spans = self.spans(int(rate))
                m = manifest(spans, fps=fps)
                tl = ic.timeline(m, media=media(rate))
                fr = fps if isinstance(fps, (int, float)) else rate  # cut's count: the manifest's number
                want = [cutlist.frames(s, fr) for s in spans]
                total = sum(want)
                self.assertGreater(total / rate, 3600)
                self.assertEqual(tl["drop"], drop)
                base = tc_to_frames("01:00:00;00" if drop else "01:00:00:00", rate)
                self.assertEqual(tl["record_start"], base)

                # EDL: picture events butt end to end and add up to the spans
                ev = [e for e in events(ic.edl(tl)) if e[0] == "V"]
                self.assertEqual(len(ev), len(spans))
                rec = base
                for (ch, si, so, ri, ro), s, n in zip(ev, spans, want):
                    self.assertEqual(tc_to_frames(ri, rate), rec)
                    self.assertEqual(tc_to_frames(ro, rate) - tc_to_frames(ri, rate), n)
                    self.assertEqual(tc_to_frames(si, rate), cutlist.frame(s["in"], fr))
                    self.assertEqual(tc_to_frames(so, rate), cutlist.frame(s["out"], fr))
                    rec += n
                self.assertEqual(tc_to_frames(ev[-1][4], rate), base + total)

                # FCPXML: every time a frame multiple, offsets exact to the frame
                root = ET.fromstring(ic.fcpxml(tl).split("\n", 2)[2])
                fd = 1 / rate
                seq = root.find("library/event/project/sequence")
                self.assertEqual(fcp_seconds(seq.get("duration")), total * fd)
                self.assertEqual(fcp_seconds(seq.get("tcStart")), base * fd)
                clips = seq.findall("spine/asset-clip")
                at = base
                for c, n in zip(clips, want):
                    self.assertEqual(fcp_seconds(c.get("offset")), at * fd)
                    self.assertEqual(fcp_seconds(c.get("duration")), n * fd)
                    at += n
                for el in root.iter():
                    for k in TIME_ATTRS:
                        if el.get(k) is not None:
                            self.assertEqual((fcp_seconds(el.get(k)) / fd).denominator, 1,
                                             (el.tag, k, el.get(k)))

                # OTIO: the video track's durations add up to the same frames
                doc = json.loads(ic.otio(tl))
                video = doc["tracks"]["children"][0]["children"]
                self.assertEqual(sum(c["source_range"]["duration"]["value"] for c in video), total)
                self.assertEqual([c["source_range"]["start_time"]["value"] for c in video],
                                 [cutlist.frame(s["in"], fr) for s in spans])
                self.assertEqual(doc["global_start_time"]["value"], base)

    def test_spans_count_frames_as_cut_does(self):
        # cut runs cutlist.frame on the manifest's 29.97; 30000/1001 would put
        # 17.334 s on frame 520, cut puts it on 519
        self.assertEqual(cutlist.frame(17.334, NTSC30), 520)
        for fps in (None, "30000/1001"):
            tl = ic.timeline(manifest([{"in": 17.334, "out": 20.0}], fps=29.97), fps=fps,
                             media=media(NTSC30))
            self.assertEqual(tl["events"][0]["v_in"], cutlist.frame(17.334, 29.97))
            self.assertEqual(tl["events"][0]["v_in"], 519)
            self.assertEqual(tl["rate"], NTSC30)

    def test_one_hour_span_lands_on_the_hour(self):
        # 23.976 non-drop labels run 3.6 s an hour behind the clock: the
        # label says so, the frame count does not drift.
        for fps, end in ((29.97, "02:00:00;00"), (25, "02:00:00:00"), (23.976, "01:59:56:10"),
                         (59.94, "02:00:00;00")):
            with self.subTest(fps=fps):
                tl = ic.timeline(manifest([{"in": 0.0, "out": 3600.0}], fps=fps),
                                 media=media(ic.parse_rate(fps)))
                v = [e for e in events(ic.edl(tl)) if e[0] == "V"]
                self.assertEqual(v[0][4], end)


class TestEDL(unittest.TestCase):
    def setUp(self):
        spans = [{"in": 1.0, "out": 3.0}, {"in": 4.0, "out": 6.5}, {"in": 59.9, "out": 60.2}]
        self.tl = ic.timeline(manifest(spans, fps=29.97, name="twenty_60"), media=media(NTSC30))
        self.text = ic.edl(self.tl)

    def test_header_and_columns(self):
        lines = self.text.splitlines()
        self.assertEqual(lines[:3], ["TITLE: twenty_60", "FCM: DROP FRAME", ""])
        body = lines[3:]
        self.assertEqual(len(body), 12)  # V and AA per span, each with its comment
        for i in range(0, len(body), 2):
            m = EVENT.match(body[i])
            self.assertIsNotNone(m, body[i])
            self.assertEqual(int(m.group(1)), i // 2 + 1)
            self.assertEqual(m.group(2), "CLIPONE ")
            self.assertEqual(body[i + 1], "* FROM CLIP NAME: clip one.mov")
            self.assertIsNotNone(RHYTHM_EVENT.match(body[i]))
        self.assertEqual([e[0] for e in events(self.text)], ["V", "AA"] * 3)

    def test_minute_boundary_span(self):
        ch, si, so, ri, ro = events(self.text)[4]
        self.assertEqual((ch, si, so), ("V", "00:00:59;25", "00:01:00;06"))
        # nine frames, though the labels differ by eleven
        self.assertEqual(tc_to_frames(so, NTSC30) - tc_to_frames(si, NTSC30), 9)
        self.assertEqual(ri, "01:00:04;15")

    def test_non_drop_and_no_sound(self):
        tl = ic.timeline(manifest([{"in": 0.5, "out": 2.5}], fps=25),
                         media=media(Fraction(25), audio=False))
        text = ic.edl(tl)
        self.assertIn("FCM: NON-DROP FRAME", text)
        self.assertEqual(events(text),
                         [("V", "00:00:00:13", "00:00:02:13", "01:00:00:00", "01:00:02:00")])

    def test_event_limit(self):
        spans = [{"in": i * 2.0, "out": i * 2.0 + 1.0} for i in range(500)]
        tl = ic.timeline(manifest(spans, fps=25), media=media(Fraction(25)))
        with self.assertRaisesRegex(ic.InterchangeError, "1000 EDL events"):
            ic.edl(tl)
        self.assertEqual(len(events(ic.edl(ic.timeline(manifest(spans, fps=25),
                                                       media=media(Fraction(25), audio=False))))),
                         500)

    def test_ndf_source_timecode_keeps_ndf(self):
        tl = ic.timeline(manifest([{"in": 0.0, "out": 1.0}], fps=29.97),
                         media=media(NTSC30, tc="10:00:00:00"))
        self.assertFalse(tl["drop"])
        self.assertEqual(events(ic.edl(tl))[0][1:3], ("10:00:00:00", "10:00:01:00"))

    def test_source_timecode_wraps_at_midnight(self):
        for rate, fps, tc, want in ((Fraction(25), 25, "23:59:58:00", ("23:59:59:00", "00:00:04:00")),
                                    (NTSC30, 29.97, "23:59:58;00", ("23:59:59;00", "00:00:04;00"))):
            with self.subTest(fps=fps):
                tl = ic.timeline(manifest([{"in": 1.0, "out": 6.0}], fps=fps),
                                 media=media(rate, tc=tc))
                self.assertEqual([e[1:3] for e in events(ic.edl(tl))], [want, want])
        self.assertEqual(ic.timecode(24 * 107892 + 5, NTSC30, True), "00:00:00;05")


class TestFCPXML(unittest.TestCase):
    def setUp(self):
        spans = [{"in": 1.0, "out": 3.0}, {"in": 4.0, "out": 6.5, "audio_lead": 0.4},
                 {"in": 59.9, "out": 60.2, "audio_tail": 0.2}, {"in": 70.0, "out": 71.0}]
        self.tl = ic.timeline(manifest(spans, fps=29.97),
                              media=media(NTSC30, tc="02:00:00;00", frames=3000, size=(3840, 2160)),
                              media_root="/Volumes/Media Drive/day 1", title="A & B <cut>")
        self.text = ic.fcpxml(self.tl)
        self.root = ET.fromstring(self.text.split("\n", 2)[2])

    def test_document(self):
        self.assertTrue(self.text.startswith(
            '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>'))
        self.assertEqual(self.root.tag, "fcpxml")
        self.assertEqual(self.root.get("version"), "1.10")
        fmt = self.root.find("resources/format")
        self.assertEqual((fmt.get("frameDuration"), fmt.get("width"), fmt.get("height")),
                         ("1001/30000s", "3840", "2160"))
        asset = self.root.find("resources/asset")
        self.assertEqual(asset.get("format"), fmt.get("id"))
        start = tc_to_frames("02:00:00;00", NTSC30)
        self.assertEqual(fcp_seconds(asset.get("start")), start / NTSC30)
        self.assertEqual(fcp_seconds(asset.get("duration")), 3000 / NTSC30)
        self.assertEqual(asset.get("hasAudio"), "1")
        rep = asset.find("media-rep")
        self.assertEqual(rep.get("kind"), "original-media")
        self.assertEqual(rep.get("src"), "file:///Volumes/Media%20Drive/day%201/clip%20one.mov")
        project = self.root.find("library/event/project")
        self.assertEqual(project.get("name"), "A & B <cut>")
        seq = project.find("sequence")
        self.assertEqual((seq.get("format"), seq.get("tcFormat")), ("r1", "DF"))
        for c in seq.findall("spine/asset-clip"):
            self.assertEqual(c.get("ref"), asset.get("id"))

    def test_every_time_is_a_frame_multiple(self):
        fd = Fraction(1001, 30000)
        seen = 0
        for el in self.root.iter():
            for k in TIME_ATTRS:
                if el.get(k) is not None:
                    seen += 1
                    self.assertEqual((fcp_seconds(el.get(k)) / fd).denominator, 1, (el.tag, k))
        self.assertGreater(seen, 20)

    def test_split_edits(self):
        start = tc_to_frames("02:00:00;00", NTSC30)
        clips = self.root.findall("library/event/project/sequence/spine/asset-clip")
        got = [(c.get("audioStart"), c.get("audioDuration")) for c in clips]
        t = lambda f: ic.fcp_time(f, NTSC30)
        # span 2 leads by 12 frames, so span 1's sound ends 12 frames early;
        # span 3 trails by 6, so span 4's sound starts 6 frames late
        self.assertEqual(got, [(t(start + 30), t(48)), (t(start + 108), t(87)),
                               (t(start + 1795), t(15)), (t(start + 2104), t(24))])
        # sound adds up to the picture: one unbroken run
        self.assertEqual(sum(fcp_seconds(d) for _, d in got), self.tl["frames"] / NTSC30)


class TestOTIO(unittest.TestCase):
    def setUp(self):
        spans = [{"in": 1.0, "out": 3.0}, {"in": 4.0, "out": 6.5, "audio_lead": 0.4},
                 {"in": 8.0, "out": 9.0}]
        self.tl = ic.timeline(manifest(spans, fps=25), media=media(Fraction(25), tc="00:10:00:00"))
        self.doc = json.loads(ic.otio(self.tl))

    def test_schema(self):
        d = self.doc
        self.assertEqual(d["OTIO_SCHEMA"], "Timeline.1")
        self.assertEqual(d["global_start_time"], {"OTIO_SCHEMA": "RationalTime.1", "rate": 25.0,
                                                  "value": 90000.0})
        self.assertEqual(d["tracks"]["OTIO_SCHEMA"], "Stack.1")
        tracks = d["tracks"]["children"]
        self.assertEqual([(t["OTIO_SCHEMA"], t["kind"]) for t in tracks],
                         [("Track.1", "Video"), ("Track.1", "Audio")])
        for t in tracks:
            for c in t["children"]:
                self.assertEqual(c["OTIO_SCHEMA"], "Clip.2")
                self.assertEqual(c["active_media_reference_key"], "DEFAULT_MEDIA")
                ref = c["media_references"]["DEFAULT_MEDIA"]
                self.assertEqual(ref["OTIO_SCHEMA"], "ExternalReference.1")
                self.assertEqual(ref["target_url"],
                                 "file:///nonexistent-interchange-test/clip%20one.mov")
                for r in (c["source_range"], ref["available_range"]):
                    self.assertEqual(r["OTIO_SCHEMA"], "TimeRange.1")
                    for k in ("start_time", "duration"):
                        self.assertEqual(r[k]["OTIO_SCHEMA"], "RationalTime.1")
                        self.assertEqual(r[k]["rate"], 25.0)
                        self.assertTrue(float(r[k]["value"]).is_integer())

    def test_sound_track(self):
        video, sound = (t["children"] for t in self.doc["tracks"]["children"])
        rng = lambda c: (c["source_range"]["start_time"]["value"],
                         c["source_range"]["duration"]["value"])
        start = 15000  # 00:10:00:00 at 25
        self.assertEqual([rng(c) for c in video],
                         [(start + 25, 50), (start + 100, 63), (start + 200, 25)])
        self.assertEqual([rng(c) for c in sound],
                         [(start + 25, 40), (start + 90, 73), (start + 200, 25)])
        self.assertEqual(sum(d for _, d in map(rng, video)), sum(d for _, d in map(rng, sound)))


class TestRefusals(unittest.TestCase):
    def tl(self, spans, **kw):
        kw.setdefault("media", media(Fraction(25)))
        return ic.timeline(manifest(spans, fps=25), **kw)

    def test_split_rules(self):
        two = [{"in": 1.0, "out": 2.0}, {"in": 3.0, "out": 4.0}]
        bad = [
            ([{"in": 1.0, "out": 2.0, "audio_lead": 0.2}, two[1]], "needs a span before"),
            ([two[0], {"in": 3.0, "out": 4.0, "audio_tail": 0.2}], "needs a span after"),
            ([{"in": 1.0, "out": 2.0, "audio_tail": 0.2}, {"in": 3.0, "out": 4.0, "audio_lead": 0.2}],
             "keep one"),
            ([two[0], {"in": 3.0, "out": 4.0, "audio_lead": -1}], "at or above 0"),
            ([two[0], {"in": 3.0, "out": 4.0, "audio_lead": "1"}], "at or above 0"),
            ([{"in": 1.0, "out": 1.2}, {"in": 3.0, "out": 4.0, "audio_lead": 0.4}], "no sound"),
            ([{"in": 1.0, "out": 1.01}], "no whole frame"),
            ([{"in": -0.5, "out": 1.0}], "before the first frame"),
            ([two[0], {"in": 3.0, "out": 4.0, "audio_lead": float("inf")}], "finite"),
            ([{"in": 0.0, "out": float("inf")}], "finite"),
        ]
        for spans, why in bad:
            with self.subTest(why=why), self.assertRaisesRegex(ic.InterchangeError, why):
                self.tl(spans)
        with self.assertRaisesRegex(ic.InterchangeError, "needs sound"):
            self.tl([two[0], {"in": 3.0, "out": 4.0, "audio_lead": 0.2}],
                    media=media(Fraction(25), audio=False))

    def test_rate_and_length(self):
        with self.assertRaisesRegex(ic.InterchangeError, "one rate"):
            self.tl([{"in": 1.0, "out": 2.0}], media=media(NTSC30))
        with self.assertRaisesRegex(ic.InterchangeError, "past the end"):
            self.tl([{"in": 1.0, "out": 5.0}], media=media(Fraction(25), frames=100))
        with self.assertRaisesRegex(ic.InterchangeError, "pass --fps"):
            ic.timeline(manifest([{"in": 1.0, "out": 2.0}]), media=None)
        with self.assertRaisesRegex(ic.InterchangeError, "not absolute"):
            self.tl([{"in": 1.0, "out": 2.0}], media_root="relative/dir")

    def test_drop_frame_timecode_above_60_fps(self):
        with self.assertRaises(ic.InterchangeError) as cm:
            ic.timeline(manifest([{"in": 1.0, "out": 2.0}], fps=119.88),
                        media=media(Fraction(120000, 1001), tc="22:11:23;000"))
        self.assertIn("not supported", str(cm.exception))
        self.assertNotIn("caption", str(cm.exception))

    def test_clip_choice(self):
        m = manifest([{"in": 1.0, "out": 2.0}], fps=25,
                     extra_clips=[{"name": "other", "spans": [{"in": 5.0, "out": 6.0}]}])
        with self.assertRaisesRegex(ic.InterchangeError, "cut, other"):
            ic.timeline(m)
        with self.assertRaisesRegex(ic.InterchangeError, "no clip named"):
            ic.timeline(m, clip="missing")
        self.assertEqual(ic.timeline(m, clip="other")["events"][0]["v_in"], 125)

    def test_unprobed_source_is_named(self):
        tl = ic.timeline(manifest([{"in": 1.0, "out": 2.0}], fps=25))
        self.assertEqual(len(tl["warnings"]), 1)
        self.assertIn("not probed", tl["warnings"][0])
        self.assertIn("(assumed)", ic.summary(ic.report(tl, "edl")))


class TestProbedSource(unittest.TestCase):
    """Real files from lavfi: the camera's timecode reaches every format."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = os.path.realpath(cls.tmp.name)
        cls.df = make_clip(os.path.join(cls.dir, "take one.mov"))
        cls.mute = make_clip(os.path.join(cls.dir, "mute.mov"), rate="25", timecode="10:00:00:00",
                             audio=False)
        cls.decimal = make_clip(os.path.join(cls.dir, "decimal.mov"), seconds=2.0, rate="29.97",
                                timecode="01:00:00;00")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_probe(self):
        p = ic.probe(self.df)
        self.assertEqual((p["rate"], p["tc"], p["size"]), (NTSC30, "01:02:03;04", (64, 36)))
        self.assertEqual(p["audio"]["rate"], 48000)
        self.assertGreaterEqual(p["frames"], 119)
        self.assertIsNone(ic.probe(self.mute)["audio"])

    def test_every_format_carries_the_source_timecode(self):
        m = manifest([{"in": 0.5, "out": 1.5}, {"in": 2.0, "out": 3.0, "audio_lead": 0.2}],
                     fps=29.97, source=self.df)
        start = tc_to_frames("01:02:03;04", NTSC30)
        text, rep = ic.build(m, "edl")
        self.assertTrue(rep["probed"])
        self.assertEqual(rep["warnings"], [])
        self.assertEqual(rep["source_tc"], "01:02:03;04")
        self.assertIn("FCM: DROP FRAME", text)
        v = [e for e in events(text) if e[0] == "V"]
        self.assertEqual(tc_to_frames(v[0][1], NTSC30), start + 15)
        self.assertEqual(tc_to_frames(v[1][1], NTSC30), start + 60)

        text, _ = ic.build(m, "fcpxml")
        root = ET.fromstring(text.split("\n", 2)[2])
        asset = root.find("resources/asset")
        self.assertEqual(fcp_seconds(asset.get("start")), start / NTSC30)
        self.assertEqual(asset.find("media-rep").get("src"),
                         "file://" + self.dir.replace(" ", "%20") + "/take%20one.mov")
        clip = root.findall("library/event/project/sequence/spine/asset-clip")[1]
        self.assertEqual(fcp_seconds(clip.get("start")), (start + 60) / NTSC30)
        self.assertEqual(fcp_seconds(clip.get("audioStart")), (start + 54) / NTSC30)

        doc = json.loads(ic.build(m, "otio")[0])
        ref = doc["tracks"]["children"][0]["children"][0]["media_references"]["DEFAULT_MEDIA"]
        self.assertEqual(ref["available_range"]["start_time"]["value"], start)

    def test_mute_source_has_no_sound_events(self):
        m = manifest([{"in": 1.0, "out": 2.0}], source=self.mute)  # fps from the probe
        text, rep = ic.build(m, "edl")
        self.assertEqual(rep["fps"], "25")
        self.assertEqual(events(text),
                         [("V", "10:00:01:00", "10:00:02:00", "01:00:00:00", "01:00:01:00")])
        self.assertEqual(json.loads(ic.build(m, "otio")[0])["tracks"]["children"][0]["kind"], "Video")
        self.assertEqual(len(json.loads(ic.build(m, "otio")[0])["tracks"]["children"]), 1)
        asset = ET.fromstring(ic.build(m, "fcpxml")[0].split("\n", 2)[2]).find("resources/asset")
        self.assertEqual(asset.get("hasAudio"), "0")
        self.assertIsNone(asset.get("audioRate"))

    def test_decimal_rate_source_reads_as_ntsc(self):
        # lavfi rate=29.97 (like `ffmpeg -r 29.97`) probes as 2997/100
        info, _ = detect.run_ffprobe(self.decimal)
        self.assertEqual(captions.file_rate(info), Fraction(2997, 100))
        self.assertEqual(ic.probe(self.decimal)["rate"], NTSC30)
        for fps in (29.97, None):
            with self.subTest(fps=fps):
                m = manifest([{"in": 0.5, "out": 1.5}], fps=fps, source=self.decimal)
                text, rep = ic.build(m, "edl")
                self.assertEqual((rep["fps"], rep["drop_frame"]), ("30000/1001", True))
                self.assertIn("FCM: DROP FRAME", text)
                self.assertEqual(events(text)[0][1], "01:00:00;15")
                fmt = ET.fromstring(ic.build(m, "fcpxml")[0].split("\n", 2)[2]).find("resources/format")
                self.assertEqual(fmt.get("frameDuration"), "1001/30000s")

    def test_wrong_rate_refused(self):
        with self.assertRaisesRegex(ic.InterchangeError, "one rate"):
            ic.build(manifest([{"in": 1.0, "out": 2.0}], fps=25, source=self.df), "edl")


class TestCommandLine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.realpath(self.tmp.name)
        self.manifest = os.path.join(self.dir, "m.json")
        m = manifest([{"in": 1.0, "out": 2.0}], fps=25,
                     extra_clips=[{"name": "other", "spans": [{"in": 5.0, "out": 6.0}]}])
        with open(self.manifest, "w", encoding="utf-8") as f:
            json.dump(m, f)

    def tearDown(self):
        self.tmp.cleanup()

    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ic.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_writes_each_format(self):
        for fmt in ic.FORMATS:
            out = os.path.join(self.dir, "cut." + fmt)
            code, stdout, stderr = self.main(self.manifest, "--format", fmt, "--out", out,
                                             "--clip", "other", "--title", "Other cut")
            self.assertEqual(code, 0, stderr)
            self.assertTrue(stdout.startswith(f"{fmt} Other cut: 1 span(s), 25 frames"), stdout)
            self.assertIn("not probed", stderr)
            with open(out, encoding="utf-8") as f:
                self.assertIn("Other cut", f.read())

    def test_refusals(self):
        out = os.path.join(self.dir, "cut.edl")
        code, _, err = self.main(self.manifest, "--format", "edl", "--out", out)
        self.assertEqual(code, 2)
        self.assertIn("--clip", err)
        self.assertFalse(os.path.exists(out))
        inside = str(Path(__file__).resolve().parent / "interchange-refused.edl")
        code, _, err = self.main(self.manifest, "--format", "edl", "--out", inside, "--clip", "other")
        self.assertEqual(code, 2)
        self.assertIn("git working tree", err)
        self.assertFalse(os.path.exists(inside))
        code, _, err = self.main(os.path.join(self.dir, "missing.json"), "--format", "otio",
                                 "--out", out)
        self.assertEqual(code, 2)

    def test_out_never_replaces_the_manifest_or_the_source(self):
        src = os.path.join(self.dir, "take.mov")
        with open(src, "wb") as f:
            f.write(b"camera original")
        os.link(src, os.path.join(self.dir, "linked.mov"))
        mpath = os.path.join(self.dir, "m2.json")
        with open(mpath, "w", encoding="utf-8") as f:
            json.dump(manifest([{"in": 1.0, "out": 2.0}], fps=25, source=src), f)
        with open(mpath, "rb") as f:
            mbytes = f.read()
        for out, label in ((src, "source"), (os.path.join(self.dir, "linked.mov"), "source"),
                           (mpath, "manifest")):
            with self.subTest(out=os.path.basename(out)):
                code, _, err = self.main(mpath, "--format", "edl", "--out", out)
                self.assertEqual(code, 2)
                self.assertIn(f"is the {label}", err)
        with open(src, "rb") as f:
            self.assertEqual(f.read(), b"camera original")
        with open(mpath, "rb") as f:
            self.assertEqual(f.read(), mbytes)

    def test_hard_link_into_a_git_tree_keeps_its_content(self):
        repo = os.path.join(self.dir, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        tracked = os.path.join(repo, "cut.edl")
        with open(tracked, "w", encoding="utf-8") as f:
            f.write("tracked\n")
        outside = os.path.join(self.dir, "cut.edl")
        os.link(tracked, outside)
        code, _, err = self.main(self.manifest, "--format", "edl", "--out", outside, "--clip", "other")
        self.assertEqual(code, 0, err)
        with open(tracked, encoding="utf-8") as f:
            self.assertEqual(f.read(), "tracked\n")
        with open(outside, encoding="utf-8") as f:
            self.assertTrue(f.read().startswith("TITLE: other"))


if __name__ == "__main__":
    unittest.main()
