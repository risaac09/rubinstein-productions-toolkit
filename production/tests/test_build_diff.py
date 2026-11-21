"""
Tests for rpresolve.build_diff: manifests and timeline_items files built
here. Offline, no Resolve, no media.

Run: /usr/bin/python3 -m unittest discover production/tests -p test_build_diff.py -v
"""

import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROD = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROD))

from rpresolve import build_diff as bd  # noqa: E402

FPS = 25
# at 25 fps: span 1 is 0-2 s (50 frames), span 2 is 10-11.2 s (30 frames), span 3 is 20-21 s (25)
MANIFEST = {"version": 1, "speech": False, "source_path": "/media/take1.mov", "fps": FPS,
            "clips": [{"name": "one", "spans": [{"in": 0, "out": 2}, {"in": 10, "out": 11.2},
                                                {"in": 20, "out": 21}]}]}
START = 86400


def vrow(n, start, dur, src, track=1, uid="m1", path="/media/take1.mov", clip=None):
    return {"track": track, "index": n, "name": "take1", "start": start, "end": start + dur,
            "duration": dur, "left_offset": src, "source_start": src, "source_end": src + dur,
            "enabled": True, "media": {"name": "take1", "unique_id": uid, "File Path": path},
            "clip": clip if clip is not None else n, "transform": {}}


def arow(n, start, dur, src, track=1, uid="m1", path="/media/take1.mov"):
    r = vrow(n, start, dur, src, track, uid, path)
    del r["clip"], r["transform"]
    return r


def result(kind, rows, fps="25.0", start=START):
    return {"project": {"name": "p"}, "timeline": {"index": 1, "name": "one [auto]",
            "unique_id": "t", "fps": fps, "start_frame": start, "auto": True},
            "track_type": kind, "tracks": 1, "total": len(rows), "offset": 0,
            "returned": len(rows), "next_offset": None, "rows": rows,
            "summary": "", "note": ""}


def good_video():
    return [vrow(1, START, 50, 0), vrow(2, START + 50, 30, 250), vrow(3, START + 80, 25, 500)]


def good_audio():
    return [arow(1, START, 50, 0), arow(2, START + 50, 30, 250), arow(3, START + 80, 25, 500)]


def good_items():
    return [result("video", good_video()), result("audio", good_audio())]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, obj, raw=None):
        p = os.path.join(self.tmp.name, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(raw if raw is not None else json.dumps(obj))
        return p

    def run_diff(self, items, manifest=None, extra=()):
        m = self.write("m.json", MANIFEST if manifest is None else manifest)
        i = self.write("i.json", items)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = bd.main(["--manifest", m, "--items", i, *extra])
        return code, out.getvalue(), err.getvalue()

    def row(self, out, check):
        lines = [ln for ln in out.splitlines() if ln[6:].startswith(check)]
        self.assertEqual(len(lines), 1, f"{check!r} in:\n{out}")
        return lines[0]


class TableRows(Base):
    def test_all_pass(self):
        code, out, _ = self.run_diff(good_items())
        self.assertEqual(code, 0, out)
        self.assertFalse([ln for ln in out.splitlines() if ln.startswith("FAIL")])

    def test_item_count(self):
        v = good_video()[:2]
        a = good_audio()[:2]
        code, out, _ = self.run_diff([result("video", v), result("audio", a)])
        self.assertEqual(code, 1)
        self.assertIn("FAIL", self.row(out, "item count"))
        self.assertIn("2 on V1, plan has 3", out)

    def test_record_position(self):
        v = good_video()
        v[1] = vrow(2, START + 52, 30, 250)
        a = good_audio()
        code, out, _ = self.run_diff([result("video", v), result("audio", a)])
        self.assertEqual(code, 1)
        self.assertIn("FAIL", self.row(out, "span 2"))
        self.assertIn("no item at record", out)

    def test_source_range(self):
        v = good_video()
        v[2] = vrow(3, START + 80, 25, 501)
        code, out, _ = self.run_diff([result("video", v), result("audio", good_audio())])
        self.assertEqual(code, 1)
        self.assertIn("source starts at 501, plan 500", self.row(out, "span 3"))

    def test_duration_one_frame_short(self):
        v = good_video()
        v[0] = vrow(1, START, 49, 0)
        code, out, _ = self.run_diff([result("video", v), result("audio", good_audio())])
        self.assertEqual(code, 1)
        self.assertIn("49 frames, plan 50", self.row(out, "span 1"))

    def test_rounding_rule_half_up(self):
        m = copy.deepcopy(MANIFEST)
        m["clips"][0]["spans"] = [{"in": 0.02, "out": 0.1}]  # 0.5 -> 1, 2.5 -> 3: 2 frames
        v = [vrow(1, START, 2, 1)]
        a = [arow(1, START, 2, 1)]
        code, out, _ = self.run_diff([result("video", v), result("audio", a)], manifest=m)
        self.assertEqual(code, 0, out)

    def test_gap(self):
        v = good_video()
        v[1] = vrow(2, START + 52, 28, 250)
        v[2] = vrow(3, START + 80, 25, 500)
        code, out, _ = self.run_diff([result("video", v), result("audio", good_audio())])
        self.assertEqual(code, 1)
        self.assertIn("FAIL", self.row(out, "gaps"))
        self.assertIn("2 frame(s) at record 50", out)

    def test_overlap(self):
        v = good_video()
        v[1] = vrow(2, START + 48, 32, 250)
        code, out, _ = self.run_diff([result("video", v), result("audio", good_audio())])
        self.assertEqual(code, 1)
        self.assertIn("FAIL", self.row(out, "overlaps"))
        self.assertIn("2 frame(s) at record 48", out)

    def test_audio_missing_under_video(self):
        a = good_audio()[:1] + good_audio()[2:]
        code, out, _ = self.run_diff([result("video", good_video()), result("audio", a)])
        self.assertEqual(code, 1)
        self.assertIn("FAIL", self.row(out, "audio"))
        self.assertIn("span 2: no audio item", out)

    def test_audio_detached_by_a_move(self):
        a = good_audio()
        a[1] = arow(2, START + 60, 30, 250)  # the picture stayed, the sound moved
        code, out, _ = self.run_diff([result("video", good_video()), result("audio", a)])
        self.assertEqual(code, 1)
        self.assertIn("span 2: audio runs 60-90, plan 50-80", out)

    def test_extra_item_on_v1(self):
        v = good_video() + [vrow(4, START + 105, 10, 900)]
        code, out, _ = self.run_diff([result("video", v), result("audio", good_audio())])
        self.assertEqual(code, 1)
        self.assertIn("FAIL", self.row(out, "extra items"))
        self.assertIn("V1 item at record 105", out)

    def test_extra_item_on_v2(self):
        v = good_video() + [vrow(1, START, 50, 0, track=2)]
        code, out, _ = self.run_diff([result("video", v), result("audio", good_audio())])
        self.assertEqual(code, 1)
        self.assertIn("V2 item at record 0", out)


class Hostile(Base):
    def test_missing_key(self):
        v = good_video()
        del v[1]["duration"]
        code, out, err = self.run_diff([result("video", v), result("audio", good_audio())])
        self.assertEqual(code, 2)
        self.assertIn("duration", err)
        self.assertNotIn("Traceback", err)

    def test_missing_rows_key(self):
        r = result("video", good_video())
        del r["rows"]
        code, _, err = self.run_diff([r, result("audio", good_audio())])
        self.assertEqual(code, 2)
        self.assertIn("rows", err)

    def test_empty_item_list(self):
        code, out, _ = self.run_diff([result("video", []), result("audio", [])])
        self.assertEqual(code, 1)
        self.assertIn("0 on V1, plan has 3", out)
        self.assertIn("no item at record", out)

    def test_two_items_same_record_frame(self):
        v = good_video()
        v.insert(1, vrow(9, START + 50, 30, 250))
        code, out, _ = self.run_diff([result("video", v), result("audio", good_audio())])
        self.assertEqual(code, 1)
        self.assertIn("FAIL", self.row(out, "overlaps"))
        self.assertIn("FAIL", self.row(out, "extra items"))

    def test_mixed_rate_manifest_refused(self):
        m = copy.deepcopy(MANIFEST)
        m["fps"] = [25, 30]
        code, _, err = self.run_diff(good_items(), manifest=m)
        self.assertEqual(code, 2)
        self.assertIn("fps", err)

    def test_timeline_rate_differs(self):
        items = [result("video", good_video(), fps="30.0"), result("audio", good_audio())]
        code, out, _ = self.run_diff(items)
        self.assertEqual(code, 1)
        self.assertIn("FAIL", self.row(out, "frame rate"))

    def test_ntsc_rate_matches(self):
        m = copy.deepcopy(MANIFEST)
        m["fps"] = 29.97
        m["clips"][0]["spans"] = [{"in": 0, "out": 1}]
        v = [vrow(1, START, 30, 0)]
        a = [arow(1, START, 30, 0)]
        code, out, _ = self.run_diff([result("video", v, fps="29.97"),
                                      result("audio", a, fps="29.97")], manifest=m)
        self.assertEqual(code, 0, out)

    def test_clip_absent_from_manifest(self):
        code, _, err = self.run_diff(good_items(), extra=("--clip", "nope"))
        self.assertEqual(code, 2)
        self.assertIn("nope", err)
        self.assertIn("one", err)

    def test_several_clips_need_clip_flag(self):
        m = copy.deepcopy(MANIFEST)
        m["clips"].append({"name": "two", "spans": [{"in": 0, "out": 1}]})
        code, _, err = self.run_diff(good_items(), manifest=m)
        self.assertEqual(code, 2)
        self.assertIn("--clip", err)
        code, out, _ = self.run_diff(good_items(), manifest=m, extra=("--clip", "one"))
        self.assertEqual(code, 0, out)

    def test_span_clip_absent_from_timeline(self):
        v = [good_video()[0], good_video()[2]]
        a = [good_audio()[0], good_audio()[2]]
        code, out, _ = self.run_diff([result("video", v), result("audio", a)])
        self.assertEqual(code, 1)
        self.assertIn("FAIL", self.row(out, "span 2"))

    def test_item_from_wrong_media(self):
        v = good_video()
        v[1] = vrow(2, START + 50, 30, 250, path="/media/other.mov")
        code, out, _ = self.run_diff([result("video", v), result("audio", good_audio())])
        self.assertEqual(code, 1)
        self.assertIn("other.mov", self.row(out, "span 2"))

    def test_audio_on_unexpected_track(self):
        a = [arow(i + 1, r["start"], r["duration"], r["source_start"], track=2)
             for i, r in enumerate(good_audio())]
        code, out, _ = self.run_diff([result("video", good_video()), result("audio", a)])
        self.assertEqual(code, 1)
        self.assertIn("FAIL", self.row(out, "audio"))
        self.assertIn("on A2, expected A1", out)

    def test_no_audio_read_supplied(self):
        code, out, _ = self.run_diff([result("video", good_video())])
        self.assertEqual(code, 1)
        self.assertIn("no audio read", out)

    def test_no_sound_planned(self):
        m = copy.deepcopy(MANIFEST)
        m["camera_sound"] = False
        code, out, _ = self.run_diff([result("video", good_video())], manifest=m)
        self.assertEqual(code, 0, out)

    def test_single_result_object_accepted(self):
        m = copy.deepcopy(MANIFEST)
        m["camera_sound"] = False
        code, out, _ = self.run_diff(result("video", good_video()), manifest=m)
        self.assertEqual(code, 0, out)

    def test_incomplete_page(self):
        r = result("video", good_video()[:2])
        r["next_offset"] = 2
        code, out, _ = self.run_diff([r, result("audio", good_audio())])
        self.assertEqual(code, 1)
        self.assertIn("FAIL", self.row(out, "pages"))
        self.assertIn("next_offset 2", out)

    def test_non_json_items(self):
        m = self.write("m.json", MANIFEST)
        i = self.write("i.json", None, raw="not json {")
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = bd.main(["--manifest", m, "--items", i])
        self.assertEqual(code, 2)
        self.assertIn("i.json", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())

    def test_non_json_manifest(self):
        m = self.write("m.json", None, raw="nope")
        i = self.write("i.json", good_items())
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = bd.main(["--manifest", m, "--items", i])
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err.getvalue())

    def test_missing_file(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = bd.main(["--manifest", "/nonexistent/m.json", "--items", "/nonexistent/i.json"])
        self.assertEqual(code, 2)

    def test_non_integer_frame(self):
        v = good_video()
        v[0]["start"] = "86400"
        code, _, err = self.run_diff([result("video", v), result("audio", good_audio())])
        self.assertEqual(code, 2)
        self.assertIn("start", err)

    def test_nothing_written(self):
        m, i = self.write("m.json", MANIFEST), self.write("i.json", good_items())
        before = {f: os.path.getmtime(os.path.join(self.tmp.name, f)) for f in os.listdir(self.tmp.name)}
        with contextlib.redirect_stdout(io.StringIO()):
            bd.main(["--manifest", m, "--items", i])
        after = {f: os.path.getmtime(os.path.join(self.tmp.name, f)) for f in os.listdir(self.tmp.name)}
        self.assertEqual(before, after)


def split_manifest(**spans):
    m = copy.deepcopy(MANIFEST)
    for idx, kv in spans.items():
        m["clips"][0]["spans"][int(idx[1:])].update(kv)
    return m


class SplitEdits(Base):
    """Audio as interchange places it: the neighbour gives way."""

    def test_j_cut_built_as_planned_passes(self):
        m = split_manifest(s1={"audio_lead": 0.2})  # 5 frames
        a = [arow(1, START, 45, 0), arow(2, START + 45, 35, 245), arow(3, START + 80, 25, 500)]
        code, out, _ = self.run_diff([result("video", good_video()), result("audio", a)], manifest=m)
        self.assertEqual(code, 0, out)

    def test_l_cut_built_as_planned_passes(self):
        m = split_manifest(s0={"audio_tail": 0.2})
        a = [arow(1, START, 55, 0), arow(2, START + 55, 25, 255), arow(3, START + 80, 25, 500)]
        code, out, _ = self.run_diff([result("video", good_video()), result("audio", a)], manifest=m)
        self.assertEqual(code, 0, out)

    def test_cut_ignores_lead_so_the_diff_says_so(self):
        m = split_manifest(s1={"audio_lead": 0.2})
        code, out, _ = self.run_diff(good_items(), manifest=m)
        self.assertEqual(code, 1)
        self.assertIn("audio_lead", self.row(out, "audio"))
        self.assertIn("not built", out)

    def test_audio_source_offset_wrong(self):
        a = good_audio()
        a[1] = arow(2, START + 50, 30, 251)
        code, out, _ = self.run_diff([result("video", good_video()), result("audio", a)])
        self.assertEqual(code, 1)
        self.assertIn("audio source starts at 251, plan 250", out)

    def test_one_unedited_audio_item_under_the_cut(self):
        a = [arow(1, START, 105, 0)]
        code, out, _ = self.run_diff([result("video", good_video()), result("audio", a)])
        self.assertEqual(code, 1)
        self.assertIn("span 2", self.row(out, "audio"))


class Review2(Base):
    def test_disabled_item(self):
        v = good_video()
        v[1]["enabled"] = False
        code, out, _ = self.run_diff([result("video", v), result("audio", good_audio())])
        self.assertEqual(code, 1)
        self.assertIn("disabled", self.row(out, "span 2"))

    def test_same_basename_other_card(self):
        m = copy.deepcopy(MANIFEST)
        m["source_path"] = "/cardA/take1.mov"
        v = [vrow(i + 1, r["start"], r["duration"], r["source_start"], path="/cardA/take1.mov")
             for i, r in enumerate(good_video())]
        v[1] = vrow(2, START + 50, 30, 250, path="/cardB/take1.mov")
        a = [arow(i + 1, r["start"], r["duration"], r["source_start"]) for i, r in enumerate(v)]
        code, out, _ = self.run_diff([result("video", v), result("audio", a)], manifest=m)
        self.assertEqual(code, 1)
        self.assertIn("/cardB/take1.mov", self.row(out, "span 2"))

    def test_no_path_is_a_warning_and_counted(self):
        v = good_video()
        v[0]["media"] = {"name": "take1", "unique_id": "m1"}
        code, out, _ = self.run_diff([result("video", v), result("audio", good_audio())])
        self.assertEqual(code, 0, out)
        self.assertIn("WARN", self.row(out, "span 1"))
        self.assertIn("not compared by path", out)
        self.assertIn("1 rows not compared", out)

    def test_clean_run_says_zero_not_compared(self):
        _, out, _ = self.run_diff(good_items())
        self.assertIn("0 rows not compared", out)

    def test_v2_title_with_no_clip(self):
        v = good_video()
        t = vrow(1, START, 50, 0, track=2)
        t["clip"] = None
        code, out, _ = self.run_diff([result("video", v + [t]), result("audio", good_audio())])
        self.assertEqual(code, 1)
        self.assertIn("V2 item at record 0", self.row(out, "extra items"))

    def test_ntsc_df_timeline_against_25(self):
        items = [result("video", good_video(), fps="29.97 DF"), result("audio", good_audio())]
        code, out, _ = self.run_diff(items)
        self.assertEqual(code, 1)
        self.assertIn("FAIL", self.row(out, "frame rate"))

    def test_unparseable_timeline_rate(self):
        items = [result("video", good_video(), fps="fast"), result("audio", good_audio())]
        code, out, _ = self.run_diff(items)
        self.assertEqual(code, 1)
        self.assertIn("FAIL", self.row(out, "frame rate"))

    def paged(self):
        v = good_video()
        p1 = result("video", v[:2])
        p1["next_offset"], p1["total"], p1["returned"] = 2, 3, 2
        p2 = result("video", v[2:])
        p2["offset"], p2["total"], p2["returned"] = 2, 3, 1
        return p1, p2, result("audio", good_audio())

    def test_two_pages_in_one_list(self):
        p1, p2, a = self.paged()
        code, out, _ = self.run_diff([p1, p2, a])
        self.assertEqual(code, 0, out)

    def test_two_pages_in_two_files(self):
        p1, p2, a = self.paged()
        m = self.write("m.json", MANIFEST)
        files = [self.write(f"i{n}.json", x) for n, x in enumerate([p1, p2, a])]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = bd.main(["--manifest", m, "--items", *files])
        self.assertEqual(code, 0, out.getvalue())

    def test_second_page_missing(self):
        p1, _, a = self.paged()
        code, out, _ = self.run_diff([p1, a])
        self.assertEqual(code, 1)
        self.assertIn("FAIL", self.row(out, "pages"))


class Cli(unittest.TestCase):
    def sub(self, *args):
        return subprocess.run([sys.executable, "-m", "rpresolve.build_diff", *args], cwd=PROD,
                              capture_output=True, text=True)

    def test_help(self):
        r = self.sub("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("--manifest", r.stdout)

    def test_unknown_flag(self):
        r = self.sub("--bogus")
        self.assertEqual(r.returncode, 2)
        self.assertIn("usage", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_missing_required(self):
        r = self.sub("--manifest", "x")
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
