"""
Tests for rpresolve.captions (Resolve's TTML sidecar to a zero-based .srt)
and the caption rows of deliver-check: TTML parsing (clock times, frames,
offset times, br, spans, namespaces), timecodes (non-drop, drop-frame, and
where the tag is read from), the conversion on an ffmpeg-made mp4 carrying
timecode 01:00:00:00, every refusal, the TTML moved to a Trash folder,
deliver-check's duration rule, and the deliver-captions command and MCP
tool. Synthetic captions only ("Example caption one").

Run: /usr/bin/python3 -m unittest discover production/tests -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from rpresolve import captions, deliver, delivercheck as dc  # noqa: E402
from rpresolve.mcp import schema, server  # noqa: E402
from rpresolve.mcp.registry import ToolContext  # noqa: E402
from test_delivercheck import NAME16, needs_ffmpeg, statuses, tiny  # noqa: E402

WORKFLOW = HERE.parent / "resolve_workflow.py"
TTML_NS = ('xmlns="http://www.w3.org/ns/ttml" '
           'xmlns:ttp="http://www.w3.org/ns/ttml#parameter"')
TRACK = "Subtitle 1"
# Cues as Resolve times them on a timeline that starts at 01:00:00:00.
RESOLVE_CUES = [("01:00:00.040", "01:00:01.200", "Example caption one"),
                ("01:00:01.200", "01:00:02.960", "Example caption two<br/>second line")]


def tt(body, params='ttp:frameRate="25" ttp:timeBase="media"', ns=TTML_NS):
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<tt xml:lang="en" {ns} {params}>'
            f'<body><div>{body}</div></body></tt>\n')


def ps(cues):
    return "".join(f'<p begin="{a}" end="{b}">{t}</p>' for a, b, t in cues)


def secs(result):
    return [(float(a), float(b), t) for a, b, t in result["cues"]]


class TestTTML(unittest.TestCase):
    def test_resolve_shape_with_br_and_spans(self):
        r = captions.parse_ttml(tt(ps(RESOLVE_CUES) +
                                   '<p begin="01:00:03.000" end="01:00:04.000">A <span tts:color="red" '
                                   'xmlns:tts="http://www.w3.org/ns/ttml#styling">split</span>'
                                   ' <span>word</span>\n   run</p>'))
        self.assertEqual(secs(r), [(3600.04, 3601.2, "Example caption one"),
                                   (3601.2, 3602.96, "Example caption two\nsecond line"),
                                   (3603.0, 3604.0, "A split word run")])
        self.assertEqual(r["params"]["fps"], 25)
        self.assertEqual(r["empty"], 0)
        # exact arithmetic: no float drift before the shift
        self.assertEqual(r["cues"][0][0], Fraction(3600040, 1000))

    def test_frames_multiplier_and_sub_frames(self):
        p = ('ttp:frameRate="30" ttp:frameRateMultiplier="1000 1001" ttp:subFrameRate="2"')
        r = captions.parse_ttml(tt('<p begin="00:00:01:15" end="00:00:02:00.1">Example</p>', p))
        (a, b, _), = r["cues"]
        self.assertEqual(a, 1 + Fraction(15) / Fraction(30000, 1001))
        self.assertEqual(b, 2 + Fraction(1, 2) / Fraction(30000, 1001))
        # 25 fps: frame 13 is 0.52 s
        r = captions.parse_ttml(tt('<p begin="01:00:00:13" end="01:00:01:00">Example</p>'))
        self.assertEqual(float(r["cues"][0][0]), 3600.52)
        # TTML's default frame rate is 30
        r = captions.parse_ttml(tt('<p begin="00:00:00:15" end="1s">Example</p>', params=""))
        self.assertEqual(r["cues"][0][0], Fraction(1, 2))

    def test_offset_times_and_dur(self):
        p = 'ttp:frameRate="25" ttp:tickRate="10000000"'
        body = ('<p begin="12.5s" end="13s">One</p><p begin="300f" dur="25f">Two</p>'
                '<p begin="500ms" end="0.75s">Three</p><p begin="2m" end="2.5m">Four</p>'
                '<p begin="1h" end="3600.5s">Five</p><p begin="20000000t" dur="5000000t">Six</p>')
        r = captions.parse_ttml(tt(body, p))
        self.assertEqual([(float(a), float(b)) for a, b, _ in r["cues"]],
                         [(0.5, 0.75), (2.0, 2.5), (12.0, 13.0), (12.5, 13.0), (120.0, 150.0),
                          (3600.0, 3600.5)])
        self.assertEqual([t for _, _, t in r["cues"]], ["Three", "Six", "Two", "One", "Four",
                                                         "Five"])

    def test_div_and_body_begin_offset_their_captions(self):
        text = (f'<?xml version="1.0"?><tt {TTML_NS} ttp:frameRate="25"><body begin="10s">'
                '<div begin="1s"><p begin="0.5s" end="1s">Example</p>'
                '<p begin="2s" dur="1s">Two</p></div></body></tt>')
        self.assertEqual(secs(captions.parse_ttml(text)), [(11.5, 12.0, "Example"),
                                                            (13.0, 14.0, "Two")])

    def test_namespaces(self):
        cues = ps(RESOLVE_CUES[:1])
        prefixed = (f'<?xml version="1.0"?><tt:tt xmlns:tt="http://www.w3.org/ns/ttml" '
                    'xmlns:ttp="http://www.w3.org/ns/ttml#parameter" ttp:frameRate="25">'
                    '<tt:body><tt:div><tt:p begin="01:00:00:01" end="01:00:01.000">Example '
                    '<tt:span>caption</tt:span><tt:br/>one</tt:p></tt:div></tt:body></tt:tt>')
        old = ('<?xml version="1.0"?><tt xmlns="http://www.w3.org/2006/10/ttaf1" '
               'xmlns:ttp="http://www.w3.org/2006/10/ttaf1#parameter" ttp:frameRate="25">'
               f'<body><div>{cues}</div></body></tt>')
        bare = f'<tt ttp:frameRate="25" xmlns:ttp="urn:x"><body><div>{cues}</div></body></tt>'
        self.assertEqual(secs(captions.parse_ttml(prefixed)),
                         [(3600.04, 3601.0, "Example caption\none")])
        for text in (old, bare):
            with self.subTest(text=text[:60]):
                self.assertEqual(secs(captions.parse_ttml(text)),
                                 [(3600.04, 3601.2, "Example caption one")])

    def test_empty_captions_are_left_out_and_counted(self):
        r = captions.parse_ttml(tt(ps(RESOLVE_CUES) + '<p begin="5s" end="6s">  <br/> </p>'
                                   '<p begin="7s"/>'))
        self.assertEqual((len(r["cues"]), r["empty"]), (2, 2))

    def test_a_caption_takes_its_timing_from_a_timed_div(self):
        text = (f'<?xml version="1.0"?><tt {TTML_NS}><body><div begin="1s" end="3s">'
                '<p>Example caption one</p><p begin="1s">Two</p>'
                '<p begin="0.5s" end="5s">Three</p></div></body></tt>')
        self.assertEqual(secs(captions.parse_ttml(text)), [
            (1.0, 3.0, "Example caption one"), (1.5, 3.0, "Three"), (2.0, 3.0, "Two")])
        # with no end anywhere above it, a caption without one is refused
        with self.assertRaisesRegex(captions.CaptionError, "neither it nor a div"):
            captions.parse_ttml(f'<tt {TTML_NS}><body><div><p>Open</p></div></body></tt>')

    def test_smpte_time_counts_labels_as_frames(self):
        # TTML2 I.3: under SMPTE time a label's hours, minutes and seconds are
        # counted as frames at ttp:frameRate, then divided by the effective rate.
        ntsc = 'ttp:frameRate="24" ttp:frameRateMultiplier="1000 1001" ttp:timeBase="smpte"'
        fps = Fraction(24000, 1001)
        r = captions.parse_ttml(tt('<p begin="01:00:04:00" end="01:00:05:12">Example</p>',
                                   ntsc + ' ttp:markerMode="continuous"'))
        (a, b, _), = r["cues"]
        self.assertEqual(a, Fraction(86496) / fps)
        self.assertEqual(b, Fraction(86532) / fps)
        # the same count as the file's start timecode: 96 frames in, 4.004 s
        self.assertEqual(a - captions.parse_timecode("01:00:00:00", fps), Fraction(96) / fps)
        self.assertAlmostEqual(float(a - captions.parse_timecode("01:00:00:00", fps)), 4.004)
        # offset times scale the same way; frames are frames
        r = captions.parse_ttml(tt('<p begin="2s" end="120f">Example</p>',
                                   ntsc + ' ttp:markerMode="continuous"'))
        self.assertEqual(r["cues"][0][:2], (Fraction(48) / fps, Fraction(120) / fps))
        # at a whole rate a label is real time
        r = captions.parse_ttml(tt('<p begin="01:00:04:00" end="01:00:05:00">Example</p>',
                                   'ttp:frameRate="25" ttp:timeBase="smpte"'))
        self.assertEqual(secs(r), [(3604.0, 3605.0, "Example")])
        # under discontinuous markers (TTML2's default) there is no time
        # arithmetic: no dur, no timed div, no ticks
        refused = {
            "dur": tt('<p begin="01:00:04:00" dur="1s">Example</p>', ntsc),
            "offset from": (f'<tt {TTML_NS} {ntsc}><body><div begin="1s">'
                            '<p begin="01:00:04:00" end="01:00:05:00">E</p></div></body></tt>'),
            "tick": tt('<p begin="100t" end="200t">Example</p>',
                       ntsc + ' ttp:markerMode="continuous"'),
            "markerMode": tt('<p begin="1s" end="2s">E</p>', ntsc + ' ttp:markerMode="sometimes"'),
        }
        for why, text in refused.items():
            with self.subTest(why=why):
                with self.assertRaisesRegex(captions.CaptionError, why):
                    captions.parse_ttml(text)

    def test_only_span_and_br_carry_caption_text(self):
        ns = (TTML_NS + ' xmlns:ttm="http://www.w3.org/ns/ttml#metadata"'
              ' xmlns:tts="http://www.w3.org/ns/ttml#styling"')
        body = ('<p begin="1s" end="2s"><ttm:desc>a note</ttm:desc>Example caption one</p>'
                '<p begin="3s" end="4s"><ttm:agent xml:id="a1" type="person"><ttm:name '
                'type="full">Example Name</ttm:name></ttm:agent>Two <span><ttm:title>t'
                '</ttm:title>words</span></p>'
                '<p begin="5s" end="6s"><metadata><ttm:copyright>c</ttm:copyright></metadata>'
                'Three<set tts:color="red"/><ttm:actor agent="a1"/></p>'
                '<p begin="7s" end="8s">Four <x:aside xmlns:x="urn:example">not shown</x:aside>'
                '<x:empty xmlns:x="urn:example"/></p>')
        r = captions.parse_ttml(tt(body, ns=ns))
        self.assertEqual([t for _, _, t in r["cues"]],
                         ["Example caption one", "Two words", "Three", "Four"])
        # TTML's own metadata is dropped quietly; text in anything else is named
        self.assertEqual(r["dropped"], ["aside"])

    def test_sequential_time_containers_are_refused(self):
        # under seq each child counts from the end of the one before; read as
        # par, both would start at 0 s and pass every later check
        for where in ('<body timeContainer="seq"><div>', '<body><div timeContainer="seq">'):
            with self.subTest(where=where):
                text = (f'<tt {TTML_NS}>{where}<p dur="1s">One</p><p dur="1s">Two</p>'
                        '</div></body></tt>')
                with self.assertRaisesRegex(captions.CaptionError, "timeContainer 'seq'"):
                    captions.parse_ttml(text)
        text = (f'<tt {TTML_NS}><body><div><p begin="1s" end="2s" timeContainer="seq">One'
                '</p></div></body></tt>')
        with self.assertRaisesRegex(captions.CaptionError, "timeContainer 'seq' on a <p>"):
            captions.parse_ttml(text)
        # par, TTML's default, reads as before
        text = (f'<tt {TTML_NS}><body timeContainer="par"><div timeContainer=" par ">'
                '<p begin="1s" dur="1s">One</p></div></body></tt>')
        self.assertEqual(secs(captions.parse_ttml(text)), [(1.0, 2.0, "One")])

    def test_end_and_dur_together_end_at_the_earlier(self):
        # SMIL, as TTML2 12.2.2 notes: the lesser of dur and end minus begin
        r = captions.parse_ttml(tt('<p begin="1s" end="10s" dur="1s">One</p>'
                                   '<p begin="20s" end="21s" dur="5s">Two</p>'))
        self.assertEqual(secs(r), [(1.0, 2.0, "One"), (20.0, 21.0, "Two")])
        # on a div too, where it cuts off the captions inside
        text = (f'<tt {TTML_NS}><body><div begin="1s" end="10s" dur="2s">'
                '<p>Example caption one</p></div></body></tt>')
        self.assertEqual(secs(captions.parse_ttml(text)), [(1.0, 3.0, "Example caption one")])

    def test_preserved_space_keeps_line_breaks(self):
        r = captions.parse_ttml(tt('<p xml:space="preserve" begin="1s" end="2s">One\nTwo</p>'))
        self.assertEqual(r["cues"][0][2], "One\nTwo")

    def test_what_it_refuses(self):
        cases = {
            "not well-formed": "<tt><body>",
            "root is <tt>": "<html/>",
            "timing": tt('<p begin="1s" end="3s">A <span begin="2s">late</span></p>'),
            "has no end": tt('<p begin="1s">Example</p>'),
            "at or before": tt('<p begin="2s" end="1s">Example</p>'),
            "frame 25": tt('<p begin="00:00:00:25" end="1s">Example</p>'),
            "clock time": tt('<p begin="soon" end="1s">Example</p>'),
            "59": tt('<p begin="00:61:00.000" end="1s">Example</p>'),
            "timeBase": tt('<p begin="1s" end="2s">Example</p>', 'ttp:timeBase="clock"'),
            "dropMode": tt('<p begin="1s" end="2s">Example</p>',
                           'ttp:timeBase="smpte" ttp:dropMode="dropNTSC"'),
            "two numbers": tt('<p begin="1s" end="2s">E</p>', 'ttp:frameRateMultiplier="1001"'),
        }
        for why, text in cases.items():
            with self.subTest(why=why):
                with self.assertRaisesRegex(captions.CaptionError, why):
                    captions.parse_ttml(text)


class TestTimecode(unittest.TestCase):
    def test_non_drop(self):
        self.assertEqual(captions.parse_timecode("01:00:00:00", Fraction(25)), 3600)
        self.assertEqual(captions.parse_timecode("00:00:01:12", Fraction(25)), Fraction(37, 25))
        # 23.976: 86400 frames counted at 24 a label second, played at 24000/1001
        self.assertEqual(float(captions.parse_timecode("01:00:00:00", Fraction(24000, 1001))),
                         3603.6)

    def test_drop_frame(self):
        ntsc = Fraction(30000, 1001)
        self.assertEqual(captions.parse_timecode("01:00:00;00", ntsc), Fraction(107892) / ntsc)
        self.assertEqual(captions.parse_timecode("00:01:00;02", ntsc), Fraction(1800) / ntsc)
        self.assertEqual(captions.parse_timecode("00:10:00;00", Fraction(60000, 1001)),
                         Fraction(35964) / Fraction(60000, 1001))
        with self.assertRaisesRegex(captions.CaptionError, "skips"):
            captions.parse_timecode("00:01:00;01", ntsc)
        with self.assertRaisesRegex(captions.CaptionError, "only 29.97 and 59.94"):
            captions.parse_timecode("01:00:00;00", Fraction(25))
        for bad in ("01:00:00.00", "1h", "01:00:00:25"):
            with self.subTest(bad=bad), self.assertRaises(captions.CaptionError):
                captions.parse_timecode(bad, Fraction(25))

    def test_where_the_start_is_read(self):
        video = {"index": 0, "codec_type": "video", "tags": {"timecode": "01:00:00:00"}}
        data = {"index": 2, "codec_type": "data", "tags": {"timecode": "02:00:00:00"}}
        fmt = {"tags": {"timecode": "03:00:00:00"}}
        self.assertEqual(captions.file_start({"streams": [data, video], "format": fmt}),
                         ("01:00:00:00", "stream #0 (video) tag"))
        self.assertEqual(captions.file_start({"streams": [dict(video, tags={}), data]})[0],
                         "02:00:00:00")
        self.assertEqual(captions.file_start({"streams": [], "format": fmt}),
                         ("03:00:00:00", "format tag"))
        self.assertEqual(captions.file_start({"streams": []}), (None, None))


class TestSubRip(unittest.TestCase):
    def test_format_and_read_back(self):
        cues = [(Fraction(40, 1000), Fraction(1200, 1000), "Example caption one"),
                (Fraction(3661, 1), Fraction(36615, 10), "Two\nlines")]
        text = captions.format_srt(cues)
        self.assertEqual(text, "1\n00:00:00,040 --> 00:00:01,200\nExample caption one\n\n"
                               "2\n01:01:01,000 --> 01:01:01,500\nTwo\nlines\n")
        back, problems = dc.parse_srt(text)
        self.assertEqual(problems, [])
        self.assertEqual([t for _, _, t in back], ["Example caption one", "Two\nlines"])
        self.assertEqual(captions.srt_time(Fraction(1, 3)), "00:00:00,333")
        with self.assertRaises(captions.CaptionError):
            captions.srt_time(-1)


def make_tc(path, seconds=3.0, timecode="01:00:00:00", rate="25"):
    """An H.264 test clip (25 fps unless rate says otherwise) with a stereo
    tone and, unless None, a start timecode (a tmcd track and the stream
    tag, as Resolve writes)."""
    cmd = [dc.FFMPEG, "-v", "error", "-y", "-f", "lavfi",
           "-i", f"testsrc2=size=64x36:rate={rate}:duration={seconds}", "-f", "lavfi",
           "-i", f"sine=frequency=1000:sample_rate=48000:duration={seconds}",
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
           "-shortest"]
    if timecode:
        cmd += ["-timecode", timecode]
    subprocess.run(cmd + [path], stdin=subprocess.DEVNULL, check=True)
    return path


class CaptionBase(unittest.TestCase):
    """A folder per test with a copy of an ffmpeg-made clip and a Trash."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = os.path.realpath(cls.tmp.name)
        cls.master = make_tc(os.path.join(cls.root, "tc.mp4"))
        cls.bare_master = make_tc(os.path.join(cls.root, "bare.mp4"), timecode=None)
        cls.ntsc_master = make_tc(os.path.join(cls.root, "ntsc.mp4"), seconds=6.0,
                                  rate="24000/1001")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.dir = tempfile.mkdtemp(dir=self.root)
        self.trash = os.path.join(self.dir, "Trash")
        os.makedirs(self.trash)
        self.dest = tiny("youtube_16x9")

    def clip(self, cues=RESOLVE_CUES, track=TRACK, timecode=True, master=None, **kw):
        path = os.path.join(self.dir, NAME16)
        master = master or (self.master if timecode else self.bare_master)
        with open(master, "rb") as src, open(path, "wb") as out:
            out.write(src.read())
        if cues is not None:
            self.sidecar(path, cues, track, **kw)
        return path

    def sidecar(self, path, cues, track=TRACK, **kw):
        ttml = os.path.join(self.dir, f"{os.path.splitext(os.path.basename(path))[0]}_{track}.ttml")
        with open(ttml, "w", encoding="utf-8") as f:
            f.write(tt(ps(cues), **kw))
        return ttml

    def convert(self, path, **kw):
        kw.setdefault("trash_root", self.trash)
        kw.setdefault("now", 0)
        return captions.deliver_captions(path, kw.pop("dest", self.dest), **kw)


@needs_ffmpeg
class TestDeliverCaptions(CaptionBase):
    def test_dry_run_then_convert(self):
        path = self.clip()
        ttml = deliver.resolve_sidecars(path)[0][1]
        dry = self.convert(path, dry_run=True)
        self.assertEqual((dry["status"], dry["written"]), ("planned", False))
        self.assertFalse(os.path.exists(deliver.sidecar_path(path)))
        self.assertEqual(dry["start"]["timecode"], "01:00:00:00")
        self.assertEqual(dry["start"]["seconds"], 3600.0)
        self.assertEqual(dry["cues"], {"count": 2, "first": [0.04, 1.2], "last": [1.2, 2.96]})
        r = self.convert(path, expect_sha=dry["plan_sha"])
        self.assertEqual((r["status"], r["verified"]), ("pass", True))
        srt = deliver.sidecar_path(path)
        with open(srt, "rb") as f:
            raw = f.read()
        self.assertNotIn(b"\r", raw)
        self.assertEqual(raw.decode("utf-8"),
                         "1\n00:00:00,040 --> 00:00:01,200\nExample caption one\n\n"
                         "2\n00:00:01,200 --> 00:00:02,960\nExample caption two\nsecond line\n")
        # the TTML is in the Trash folder, and nowhere beside the file
        self.assertEqual(r["trashed"], os.path.join(self.trash, "deliver-captions-" +
                                                    _stamp(0), os.path.basename(ttml)))
        self.assertTrue(os.path.isfile(r["trashed"]))
        self.assertFalse(os.path.exists(ttml))
        self.assertEqual(deliver.resolve_sidecars(path), [])
        # and deliver-check's caption row passes
        row = _row(dc.check(path, self.dest, loudness=False), "captions")
        self.assertEqual(row["status"], dc.PASS, row)
        with self.assertRaisesRegex(captions.CaptionError, "already exists"):
            self.convert(path)

    def test_keep_ttml(self):
        path = self.clip()
        r = self.convert(path, keep_ttml=True)
        self.assertIsNone(r["trashed"])
        self.assertEqual(len(deliver.resolve_sidecars(path)), 1)
        self.assertEqual(os.listdir(self.trash), [])

    def test_no_sidecar(self):
        with self.assertRaisesRegex(captions.CaptionError, "no Resolve caption sidecar"):
            self.convert(self.clip(cues=None))

    def test_two_sidecars_need_a_track(self):
        path = self.clip()
        other = self.sidecar(path, RESOLVE_CUES[:1], "Subtitle 2")
        with self.assertRaisesRegex(captions.CaptionError, "Subtitle 1, Subtitle 2.*--track"):
            self.convert(path)
        with self.assertRaisesRegex(captions.CaptionError, "no sidecar for track 'Subtitle 9'"):
            self.convert(path, track="Subtitle 9")
        self.assertFalse(os.path.exists(deliver.sidecar_path(path)))
        r = self.convert(path, track="Subtitle 2")
        self.assertEqual(r["cues"]["count"], 1)
        self.assertTrue(os.path.isfile(r["trashed"]))
        self.assertFalse(os.path.exists(other))
        self.assertEqual([t for t, _ in deliver.resolve_sidecars(path)], ["Subtitle 1"])
        self.assertTrue(any("left beside the file" in w for w in r["warnings"]))

    def test_cues_outside_the_video_are_refused_with_the_numbers(self):
        path = self.clip([("01:00:01.000", "01:00:04.000", "Example caption one")])
        with self.assertRaisesRegex(captions.CaptionError,
                                    r"end after the video's 3\.000 s plus 0\.5 s "
                                    r"\(cue 1 runs 1\.000 s to 4\.000 s\)"):
            self.convert(path)
        self.assertFalse(os.path.exists(deliver.sidecar_path(path)))
        self.assertEqual(len(deliver.resolve_sidecars(path)), 1)
        os.remove(deliver.resolve_sidecars(path)[0][1])
        self.sidecar(path, [("00:59:59.000", "01:00:01.000", "Example caption one")])
        with self.assertRaisesRegex(captions.CaptionError, r"start before 0 s \(cue 1 at "
                                                           r"-1\.000 s\)"):
            self.convert(path)
        # within half a second of the end is fine
        os.remove(deliver.resolve_sidecars(path)[0][1])
        self.sidecar(path, [("01:00:02.000", "01:00:03.400", "Example caption one")])
        self.assertEqual(self.convert(path)["status"], "pass")

    def test_no_timecode_and_hour_offset_cues_is_refused(self):
        path = self.clip(timecode=False)
        with self.assertRaisesRegex(captions.CaptionError,
                                    r"no timecode tag.*3600\.040 s, outside the file's 3\.000 s"):
            self.convert(path)
        self.assertFalse(os.path.exists(deliver.sidecar_path(path)))

    def test_no_timecode_and_zero_based_cues_convert_as_they_are(self):
        path = self.clip([("00:00:00.500", "00:00:01.500", "Example caption one")],
                         timecode=False)
        r = self.convert(path)
        self.assertEqual((r["start"]["timecode"], r["cues"]["first"]), (None, [0.5, 1.5]))
        self.assertTrue(any("no timecode tag" in w for w in r["warnings"]))

    def test_an_existing_srt_is_never_overwritten(self):
        path = self.clip()
        with open(deliver.sidecar_path(path), "w", encoding="utf-8") as f:
            f.write("keep me")
        with self.assertRaisesRegex(captions.CaptionError, "already exists"):
            self.convert(path)
        with open(deliver.sidecar_path(path), encoding="utf-8") as f:
            self.assertEqual(f.read(), "keep me")
        self.assertEqual(len(deliver.resolve_sidecars(path)), 1)

    def test_only_a_sidecar_destination(self):
        path = self.clip()
        with self.assertRaisesRegex(captions.CaptionError, "burnin captions"):
            self.convert(path, dest=tiny("linkedin_9x16"))

    def test_a_changed_plan_is_refused(self):
        path = self.clip()
        dry = self.convert(path, dry_run=True)
        os.remove(deliver.resolve_sidecars(path)[0][1])
        self.sidecar(path, RESOLVE_CUES[:1])
        with self.assertRaisesRegex(captions.CaptionError, "plan changed"):
            self.convert(path, expect_sha=dry["plan_sha"])
        self.assertFalse(os.path.exists(deliver.sidecar_path(path)))

    def test_a_trash_that_fails_loses_nothing(self):
        path = self.clip()
        ttml = deliver.resolve_sidecars(path)[0][1]
        with mock.patch.object(dc, "move_to_trash",
                               side_effect=PermissionError(1, "not permitted")):
            with self.assertRaisesRegex(captions.CaptionError, "still beside the file"):
                self.convert(path)
        self.assertTrue(os.path.isfile(ttml))
        self.assertTrue(os.path.isfile(deliver.sidecar_path(path)))

    def test_a_bad_read_back_removes_the_srt(self):
        path = self.clip()
        with mock.patch.object(captions, "format_srt",
                               return_value="1\n00:00:00,040 --> 00:00:01,200\nOther\n"):
            with self.assertRaisesRegex(captions.CaptionError, "did not read back"):
                self.convert(path)
        self.assertFalse(os.path.exists(deliver.sidecar_path(path)))
        self.assertEqual(len(deliver.resolve_sidecars(path)), 1)

    def test_smpte_captions_on_a_23976_file(self):
        # a TTML from another tool in SMPTE time: 01:00:04:00 is 96 frames past
        # the file's 01:00:00:00, 4.004 s at 24000/1001
        smpte = ('ttp:frameRate="24" ttp:frameRateMultiplier="1000 1001" '
                 'ttp:timeBase="smpte" ttp:markerMode="continuous"')
        path = self.clip([("01:00:04:00", "01:00:05:00", "Example caption one")],
                         master=self.ntsc_master, params=smpte)
        r = self.convert(path, dry_run=True)
        self.assertEqual(r["start"]["fps"], "24000/1001")
        self.assertAlmostEqual(r["cues"]["first"][0], 4.004, places=6)
        self.assertAlmostEqual(r["cues"]["first"][1], 5.005, places=6)

    def test_text_outside_ttml_content_is_left_out_with_a_warning(self):
        path = self.clip([("01:00:00.040", "01:00:01.200", 'Example caption one<x:aside '
                           'xmlns:x="urn:example">not shown</x:aside>')])
        r = self.convert(path)
        with open(deliver.sidecar_path(path), encoding="utf-8") as f:
            self.assertNotIn("not shown", f.read())
        self.assertTrue(any("left out" in w and "<aside>" in w for w in r["warnings"]),
                        r["warnings"])

    def test_refused_inside_a_git_tree(self):
        os.makedirs(os.path.join(self.dir, ".git"))
        with self.assertRaisesRegex(captions.CaptionError, "git working tree"):
            self.convert(self.clip())


@needs_ffmpeg
class TestDeliverCheckCaptionTimes(CaptionBase):
    """deliver-check's sidecar row against the video's duration."""

    def srt(self, path, text):
        with open(deliver.sidecar_path(path), "w", encoding="utf-8") as f:
            f.write(text)

    def test_an_srt_timed_from_the_timeline_fails(self):
        path = self.clip(cues=None)
        self.srt(path, "1\n01:00:00,040 --> 01:00:01,200\nExample caption one\n")
        r = dc.check(path, self.dest, loudness=False)
        row = _row(r, "captions")
        self.assertEqual(row["status"], dc.FAIL, dc.format_report(r))
        self.assertIn("end after the video's 3.000 s", row["found"])
        self.assertIn("3600.040 s to 3601.200 s", row["found"])

    def test_the_half_second_rule(self):
        path = self.clip(cues=None)
        self.srt(path, "1\n00:00:02,000 --> 00:00:03,400\nExample caption one\n")
        self.assertEqual(statuses(dc.check(path, self.dest, loudness=False))["captions"],
                         dc.PASS)
        os.remove(deliver.sidecar_path(path))
        self.srt(path, "1\n00:00:02,000 --> 00:00:03,600\nExample caption one\n")
        self.assertEqual(statuses(dc.check(path, self.dest, loudness=False))["captions"],
                         dc.FAIL)

    def test_a_missing_srt_beside_resolves_ttml_says_what_to_run(self):
        path = self.clip()
        row = _row(dc.check(path, self.dest, loudness=False), "captions")
        self.assertEqual(row["status"], dc.FAIL)
        self.assertIn("Subtitle 1.ttml", row["note"])
        self.assertIn(f"run deliver-captions on {NAME16}", row["note"])
        self.dir = tempfile.mkdtemp(dir=self.root)  # no TTML beside this one
        row = _row(dc.check(self.clip(cues=None), self.dest, loudness=False), "captions")
        self.assertEqual(row["status"], dc.FAIL)
        self.assertNotIn("note", row)

    def test_the_loudness_fix_waits_for_the_captions(self):
        # The fixed file's check would fail its captions row, so --replace refuses up front,
        # and a fix without it says why its check fails.
        path = self.clip()
        with self.assertRaisesRegex(dc.CheckError, "Run deliver-captions on it first"):
            dc.fix_loudness(path, self.dest, replace=True, trash_root=self.trash, now=0)
        self.assertFalse(os.path.exists(dc.fixed_path(path)))
        r = dc.fix_loudness(path, self.dest)
        self.assertTrue(any("not been made into an .srt" in w for w in r["warnings"]))
        self.assertEqual(_row(r["check"], "captions")["status"], dc.FAIL)
        os.remove(r["fixed"])
        captions.deliver_captions(path, self.dest, trash_root=self.trash, now=0)
        # once the .srt is there, --replace goes ahead (this H.264 test clip then fails the
        # YouTube codec row, which is not what this test is about)
        r = dc.fix_loudness(path, self.dest, replace=True, trash_root=self.trash, now=0)
        self.assertEqual(_row(r["check"], "captions")["status"], dc.PASS)
        self.assertFalse(any("not been made into an .srt" in w for w in r["warnings"]))

    def test_the_duration_read(self):
        self.assertEqual(dc.duration({"streams": [{"codec_type": "video", "duration": "2.5"}],
                                      "format": {"duration": "2.9"}}), 2.5)
        self.assertEqual(dc.duration({"streams": [{"codec_type": "video"}],
                                      "format": {"duration": "2.9"}}), 2.9)
        self.assertIsNone(dc.duration({"streams": [], "format": {}}))
        self.assertEqual(dc.cue_time_problems([(0, 99, "x")], None), [])


def _row(result, name):
    return next(x for x in result["checks"] if x["check"] == name)


def _stamp(now):
    import time
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(now))


def run(*args, env=None):
    return subprocess.run([sys.executable, str(WORKFLOW), *args], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, env={**os.environ, **(env or {})})


@needs_ffmpeg
class TestCommandAndTool(CaptionBase):
    def overlay(self):
        path = os.path.join(self.dir, "overlay.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"destinations": {"youtube_16x9": {"resolution":
                                                         {"width": 64, "height": 36}}}}, f)
        return path

    def test_the_command(self):
        path = self.clip()
        ttml = deliver.resolve_sidecars(path)[0][1]
        p = run("deliver-captions", path, "--dest", "youtube_16x9", "--keep-ttml")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("taken off every cue", p.stdout)
        self.assertIn("kept beside the file", p.stdout)
        self.assertTrue(os.path.isfile(ttml))
        p = run("deliver-captions", path, "--dest", "youtube_16x9", "--json")
        self.assertEqual(p.returncode, 1)
        self.assertIn("already exists", p.stderr)
        os.remove(deliver.sidecar_path(path))
        home = os.path.join(self.dir, "home")
        os.makedirs(os.path.join(home, ".Trash"))
        p = run("deliver-captions", path, "--dest", "youtube_16x9", "--json",
                env={"HOME": home})
        self.assertEqual(p.returncode, 0, p.stderr)
        r = json.loads(p.stdout)
        self.assertTrue(r["trashed"].startswith(os.path.join(home, ".Trash",
                                                             "deliver-captions-")))
        self.assertFalse(os.path.exists(ttml))
        p = run("deliver-captions", path, "--dest", "linkedin_9x16")
        self.assertEqual(p.returncode, 1)
        p = run("deliver-captions", path, "--dest", "youtube_16x9",
                env={"RPRESOLVE_FFPROBE": "/nonexistent/ffprobe"})
        self.assertEqual(p.returncode, 2)

    def test_the_mcp_tool_plans_then_writes_with_the_plan_sha(self):
        path = self.clip()
        journal = os.path.join(self.dir, "writes.jsonl")
        env = {"RPRESOLVE_CONFIG": self.overlay(), "RPRESOLVE_MCP_JOURNAL": journal,
               "HOME": self.dir}
        os.makedirs(os.path.join(self.dir, ".Trash"), exist_ok=True)
        tool = server.build_registry().get("deliver_captions")

        def call(args):
            errors = schema.validate(tool.input_schema, args)
            if errors:
                raise AssertionError(errors)
            with mock.patch.dict(os.environ, env):
                return tool.handler(schema.with_defaults(tool.input_schema, args), ToolContext())

        base = {"file": path, "destination": "youtube_16x9"}
        dry = call(base)
        self.assertTrue(dry["dry_run"])
        self.assertIn("would write", dry["summary"])
        self.assertIn(dry["plan_sha"], dry["summary"])
        self.assertFalse(os.path.exists(journal))
        with self.assertRaisesRegex(Exception, "plan_sha"):
            call({**base, "dry_run": False})
        self.assertFalse(os.path.exists(deliver.sidecar_path(path)))
        r = call({**base, "dry_run": False, "plan_sha": dry["plan_sha"]})
        self.assertIn("read back and matching", r["summary"])
        self.assertTrue(os.path.isfile(deliver.sidecar_path(path)))
        with open(journal, encoding="utf-8") as f:
            events = [json.loads(line) for line in f]
        self.assertEqual([(e["event"], e["tool"]) for e in events],
                         [("started", "deliver_captions"), ("finished", "deliver_captions")])
        self.assertIsNone(events[0]["project"])
        self.assertEqual(events[1]["status"], "ok")
        with self.assertRaises(AssertionError):
            call({**base, "track": ""})


if __name__ == "__main__":
    unittest.main()
