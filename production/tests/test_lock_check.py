"""
Offline tests for the lock-check command (lock-handoff.md, the checker's job).
The fixture folder is built here with the repo's own interchange writers; the
checker is read-only and tests assert it leaves the folder byte for byte alone.

Run: python3 -m unittest discover production/tests -p test_lock_check.py -v
"""

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rpresolve import cli, cmd_lock_check as lc
from rpresolve import interchange as ic
from test_interchange import manifest

FPS = "24000/1001"
MARKER_HEAD = "frame\ttimecode\tduration_frames\tname\tcolour\tnote\tfps\n"
CONSENT_HEAD = "subject\tkind\ttimeline_frame\trelease_ref\tapproved\tapproved_by\tapproved_on\n"
OTHER = {"paper-edit.tsv": "order\tclip_id\tsource_in\tsource_out\tbeat\tnote\n",
         "cue-sheet.tsv": "cue_id\tkind\ttitle\ttimeline_frame\tduration_frames\tsource\tlicence_row\n"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def items_hash(items):
    return sha(json.dumps(sorted(items, key=lambda i: (i["track"], i["record_in"])),
                          sort_keys=True, separators=(",", ":")).encode())


def build(root, slug="example-slug_lock_v01", guide=False, mutate=None):
    """Write a valid lock folder under root and return its path. mutate(files, lock, sound)
    may change the file bytes (dict name -> bytes) or the two dicts before hashing."""
    m = manifest([{"in": 1.0, "out": 3.0}, {"in": 5.0, "out": 6.5}, {"in": 8, "out": 10}], fps=24000 / 1001)
    otio = json.loads(ic.build(m, "otio")[0])
    ids = ["CARD-a/P1000110", "CARD-a/P1000111", "CARD-b/P1000112"]
    clips = otio["tracks"]["children"][0]["children"]
    items, t = [], 0
    for n, (c, cid) in enumerate(zip(clips, ids), 1):
        c["metadata"] = {"clip_id": cid}
        d, s = int(c["source_range"]["duration"]["value"]), int(c["source_range"]["start_time"]["value"])
        items.append({"track": 1, "index": n, "name": c["name"], "record_in": t, "record_out": t + d,
                      "clip_id": cid, "source_in": s, "source_out": s + d})
        t += d
    files = {"timeline.otio": json.dumps(otio, indent=2).encode(),
             "timeline.edl": ic.build(m, "edl")[0].encode(),
             "markers.tsv": (MARKER_HEAD + f"0\t00:00:00:00\t1\tstory:hook\tBlue\t\t{FPS}\n"
                             f"24\t00:00:01:00\t2\tsnd:sting\tGreen\tx\t{FPS}\n").encode(),
             "consent-list.tsv": (CONSENT_HEAD + "kid\tchild\t10\tR-1\tyes\tIA\t2026-11-01\n").encode()}
    files.update({k: v.encode() for k, v in OTHER.items()})
    lock = {"schema": {"name": "shared.lock-handoff", "version": 1}, "project": "example-slug",
            "lock_version": 1, "locked_at": "2026-11-02T15:00:00Z",
            "timeline": {"name": "cut", "fps": FPS, "start_timecode": "01:00:00:00", "duration_frames": t},
            "items_sha256": items_hash(items), "files": {}}
    sound = {"schema": {"name": "sound.handoff", "version": 1}, "timeline": "cut", "fps": FPS,
             "start_timecode": "01:00:00:00", "duration_frames": t,
             "track_layout": [{"track": "A1", "role": "dialogue"}], "stems_plan": ["dialogue"]}
    if guide:
        files["guide.mov"] = b"guide"
    if mutate:
        mutate(files, lock, sound)
    files["sound-handoff.json"] = json.dumps(sound).encode()
    lock["files"] = {k: sha(v) for k, v in files.items()}
    if mutate and getattr(mutate, "after_hash", None):
        mutate.after_hash(files, lock)
    files["lock.json"] = json.dumps(lock, indent=2).encode()
    sums = "".join(f"{sha(v)}  {k}\n" for k, v in sorted(files.items()))
    files["SHA256SUMS"] = sums.encode()
    folder = Path(root) / slug
    folder.mkdir()
    for k, v in files.items():
        (folder / k).write_bytes(v)
    return folder


def mut(fn, after=None):
    fn.after_hash = after
    return fn


def snapshot(folder):
    return {p.name: p.read_bytes() for p in Path(folder).iterdir() if p.is_file() and not p.is_symlink()}


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.tmp = self._t.name
        self.addCleanup(self._t.cleanup)

    def fails(self, folder, *needles):
        before = snapshot(folder) if Path(folder).is_dir() else None
        msg = lc.check(str(folder))
        self.assertIsNotNone(msg, "expected a FAIL")
        for n in needles:
            self.assertIn(n, msg)
        if before is not None:
            self.assertEqual(snapshot(folder), before, "the checker wrote to the folder")
        return msg

    def rehash(self, folder, name, data=None):
        """Rewrite one file and keep lock.json and SHA256SUMS in step, so only the named fault shows."""
        p = Path(folder)
        if data is not None:
            (p / name).write_bytes(data)
        lock = json.loads((p / "lock.json").read_text())
        if name in lock["files"]:
            lock["files"][name] = sha((p / name).read_bytes())
        (p / "lock.json").write_text(json.dumps(lock, indent=2))
        sums = "".join(f"{sha((p / n).read_bytes())}  {n}\n"
                       for n in sorted(list(lock["files"]) + ["lock.json"]))
        (p / "SHA256SUMS").write_text(sums)


class Acceptance(Base):
    def test_valid_folder_passes(self):
        f = build(self.tmp)
        self.assertIsNone(lc.check(str(f)))

    def test_valid_folder_with_guide_passes(self):
        self.assertIsNone(lc.check(str(build(self.tmp, guide=True))))

    def test_missing_file(self):
        f = build(self.tmp)
        (f / "cue-sheet.tsv").unlink()
        self.fails(f, "cue-sheet.tsv")

    def test_file_not_in_sums(self):
        f = build(self.tmp)
        lines = [l for l in (f / "SHA256SUMS").read_text().splitlines(True) if "markers.tsv" not in l]
        (f / "SHA256SUMS").write_text("".join(lines))
        self.fails(f, "markers.tsv", "SHA256SUMS")

    def test_file_not_in_lock_json(self):
        f = build(self.tmp)
        lock = json.loads((f / "lock.json").read_text())
        del lock["files"]["markers.tsv"]
        (f / "lock.json").write_text(json.dumps(lock))
        self.rehash(f, "lock.json")
        self.fails(f, "markers.tsv", "lock.json")

    def test_hash_mismatch_against_sums(self):
        f = build(self.tmp)
        (f / "paper-edit.tsv").write_bytes(b"changed\n")
        self.fails(f, "paper-edit.tsv", "hash")

    def test_hash_mismatch_against_lock_json_only(self):
        f = build(self.tmp)
        lock = json.loads((f / "lock.json").read_text())
        lock["files"]["paper-edit.tsv"] = "0" * 64
        (f / "lock.json").write_text(json.dumps(lock))
        self.rehash(f, "lock.json")
        self.fails(f, "paper-edit.tsv", "hash")

    def test_lock_json_hash_mismatch_against_sums(self):
        f = build(self.tmp)
        (f / "lock.json").write_text((f / "lock.json").read_text() + "\n ")
        self.fails(f, "lock.json")

    def edit_dur(self, f, which, delta=1):
        if which == "lock":
            lock = json.loads((f / "lock.json").read_text())
            lock["timeline"]["duration_frames"] += delta
            (f / "lock.json").write_text(json.dumps(lock))
            self.rehash(f, "lock.json")
        elif which == "sound":
            s = json.loads((f / "sound-handoff.json").read_text())
            s["duration_frames"] += delta
            self.rehash(f, "sound-handoff.json", json.dumps(s).encode())
        elif which == "edl":
            lines = (f / "timeline.edl").read_text().splitlines()
            i = max(n for n, l in enumerate(lines) if l[14:19].strip() == "V")
            parts = lines[i].split()
            h, m, s_, fr = parts[-1].split(":")
            parts[-1] = f"{h}:{m}:{s_}:{int(fr) + delta:02d}"
            lines[i] = " ".join(parts)
            self.rehash(f, "timeline.edl", ("\n".join(lines) + "\n").encode())
        elif which == "otio":
            d = json.loads((f / "timeline.otio").read_text())
            c = d["tracks"]["children"][0]["children"][-1]
            c["source_range"]["duration"]["value"] += delta
            self.rehash(f, "timeline.otio", json.dumps(d).encode())

    def test_duration_off_by_one_in_each_place(self):
        for which in ("lock", "sound", "edl", "otio"):
            with self.subTest(which=which):
                with tempfile.TemporaryDirectory() as d:
                    f = build(d)
                    self.edit_dur(f, which)
                    self.fails(f, "duration")

    def test_items_hash_mismatch(self):
        f = build(self.tmp)
        d = json.loads((f / "timeline.otio").read_text())
        d["tracks"]["children"][0]["children"][0]["name"] = "renamed.mov"
        self.rehash(f, "timeline.otio", json.dumps(d).encode())
        self.fails(f, "items_sha256")

    def test_items_hash_ignores_audio(self):
        f = build(self.tmp)
        d = json.loads((f / "timeline.otio").read_text())
        d["tracks"]["children"].append({"OTIO_SCHEMA": "Track.1", "kind": "Audio", "name": "A1",
                                        "children": [{"OTIO_SCHEMA": "Clip.2", "name": "x"}]})
        self.rehash(f, "timeline.otio", json.dumps(d).encode())
        self.assertIsNone(lc.check(str(f)))

    def test_consent_row_not_yes(self):
        f = build(self.tmp)
        self.rehash(f, "consent-list.tsv", (CONSENT_HEAD + "kid\tchild\t10\tR-1\tno\t\t\n").encode())
        self.fails(f, "consent-list.tsv", "approved")

    def test_markers_fail_schema(self):
        f = build(self.tmp)
        bad = MARKER_HEAD + f"0\t00:00:00:00\t1\thook\tBlue\t\t{FPS}\n"  # no lane prefix
        self.rehash(f, "markers.tsv", bad.encode())
        self.fails(f, "markers.tsv", "name")


class Markers(Base):
    def marker_fault(self, body, *needles, head=MARKER_HEAD):
        with tempfile.TemporaryDirectory() as d:
            f = build(d)
            self.rehash(f, "markers.tsv", (head + body).encode())
            self.fails(f, "markers.tsv", *needles)

    def test_faults(self):
        r = lambda fr="0", tc="00:00:00:00", d="1", n="story:a", c="Blue", note="", fps=FPS: \
            "\t".join([fr, tc, d, n, c, note, fps]) + "\n"
        self.marker_fault(r(fr="x"), "frame")
        self.marker_fault(r(fr="-1", tc="00:00:00:00"), "frame")
        self.marker_fault(r(tc="00:00:00:01"), "timecode")
        self.marker_fault(r(d="0"), "duration_frames")
        self.marker_fault(r(n="other:a"), "name")
        self.marker_fault(r(n="story:"), "name")
        self.marker_fault(r(c=""), "colour")
        self.marker_fault(r(fps="25"), "fps")
        self.marker_fault(r(fr="24", tc="00:00:01:00") + r(fr="0"), "sorted")
        self.marker_fault("0\t00:00:00:00\t1\tstory:a\tBlue\n", "cells")
        self.marker_fault(r() + r(fr="0", n="story:a")[:-1] + "\textra\n", "cells")

    def test_wrong_header(self):
        self.marker_fault("", "header", head=MARKER_HEAD.replace("note", "notes"))

    def test_header_only_is_valid(self):
        for name, head in (("markers.tsv", MARKER_HEAD), ("consent-list.tsv", CONSENT_HEAD)):
            with tempfile.TemporaryDirectory() as d:
                f = build(d)
                self.rehash(f, name, head.encode())
                self.assertIsNone(lc.check(str(f)), name)

    def test_empty_markers_file_is_not_valid(self):
        with tempfile.TemporaryDirectory() as d:
            f = build(d)
            self.rehash(f, "markers.tsv", b"")
            self.fails(f, "markers.tsv")


class Hostile(Base):
    def lock_files(self, f, fn):
        lock = json.loads((f / "lock.json").read_text())
        fn(lock)
        (f / "lock.json").write_text(json.dumps(lock))
        sums = (f / "SHA256SUMS").read_text().splitlines()
        (f / "SHA256SUMS").write_text("".join(
            l + "\n" if "lock.json" not in l else f"{sha((f / 'lock.json').read_bytes())}  lock.json\n"
            for l in sums))

    def test_traversal_and_absolute_names_in_lock_json(self):
        for bad in ("../outside.txt", "/etc/passwd", "sub/a.tsv", "..", ".", "", "a\\b"):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as d:
                f = build(d)
                self.lock_files(f, lambda l: l["files"].update({bad: "0" * 64}))
                self.fails(f, "lock.json")

    def test_traversal_name_in_sums(self):
        f = build(self.tmp)
        (f / "SHA256SUMS").write_text((f / "SHA256SUMS").read_text() + f"{'0' * 64}  ../x\n")
        self.fails(f, "SHA256SUMS")

    def test_extra_file_in_folder(self):
        f = build(self.tmp)
        (f / "notes.txt").write_text("hi")
        self.fails(f, "notes.txt")

    def test_extra_directory_in_folder(self):
        f = build(self.tmp)
        (f / "sub").mkdir()
        self.fails(f, "sub")

    def test_symlink_pointing_outside(self):
        f = build(self.tmp)
        outside = Path(self.tmp) / "outside.tsv"
        outside.write_bytes((f / "cue-sheet.tsv").read_bytes())
        (f / "cue-sheet.tsv").unlink()
        (f / "cue-sheet.tsv").symlink_to(outside)
        self.fails(f, "cue-sheet.tsv", "symlink")

    def test_dangling_symlink(self):
        f = build(self.tmp)
        (f / "paper-edit.tsv").unlink()
        (f / "paper-edit.tsv").symlink_to(Path(self.tmp) / "nope")
        self.fails(f, "paper-edit.tsv")

    def test_uppercase_hex_in_sums(self):
        f = build(self.tmp)
        t = (f / "SHA256SUMS").read_text().splitlines(True)
        t[0] = t[0][:64].upper() + t[0][64:]
        (f / "SHA256SUMS").write_text("".join(t))
        self.fails(f, "SHA256SUMS")

    def test_uppercase_hex_in_lock_json(self):
        f = build(self.tmp)
        self.lock_files(f, lambda l: l["files"].update(
            {"cue-sheet.tsv": l["files"]["cue-sheet.tsv"].upper()}))
        self.fails(f, "lock.json")

    def test_crlf_sums(self):
        f = build(self.tmp)
        (f / "SHA256SUMS").write_bytes((f / "SHA256SUMS").read_bytes().replace(b"\n", b"\r\n"))
        self.fails(f, "SHA256SUMS")

    def test_duplicate_sums_entry(self):
        f = build(self.tmp)
        first = (f / "SHA256SUMS").read_text().splitlines(True)[0]
        (f / "SHA256SUMS").write_text((f / "SHA256SUMS").read_text() + first)
        self.fails(f, "SHA256SUMS", "twice")

    def test_malformed_sums_line(self):
        f = build(self.tmp)
        (f / "SHA256SUMS").write_text((f / "SHA256SUMS").read_text() + "garbage\n")
        self.fails(f, "SHA256SUMS")

    def test_approved_variants(self):
        for v in ("Yes", " yes ", "", "YES", "y", "true"):
            with self.subTest(v=v), tempfile.TemporaryDirectory() as d:
                f = build(d)
                self.rehash(f, "consent-list.tsv",
                            (CONSENT_HEAD + f"kid\tchild\t10\tR-1\t{v}\tIA\t2026-11-01\n").encode())
                self.fails(f, "consent-list.tsv", "approved")

    def test_consent_short_row(self):
        f = build(self.tmp)
        self.rehash(f, "consent-list.tsv", (CONSENT_HEAD + "kid\tchild\n").encode())
        self.fails(f, "consent-list.tsv")

    def test_lock_json_not_json(self):
        f = build(self.tmp)
        (f / "lock.json").write_bytes(b"{nope")
        (f / "SHA256SUMS").write_text("".join(
            l if "lock.json" not in l else f"{sha(b'{nope')}  lock.json\n"
            for l in (f / "SHA256SUMS").read_text().splitlines(True)))
        self.fails(f, "lock.json")

    def test_lock_json_shapes(self):
        for body in (b"[]", b"[1,2]", b"null", b'"x"', b"7", b"{}", b'{"files": []}',
                     b'{"files": {}, "timeline": [], "items_sha256": 1}', b"\xff\xfe"):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as d:
                f = build(d)
                (f / "lock.json").write_bytes(body)
                (f / "SHA256SUMS").write_text("".join(
                    l if "lock.json" not in l else f"{sha(body)}  lock.json\n"
                    for l in (f / "SHA256SUMS").read_text().splitlines(True)))
                self.fails(f, "lock.json")

    def test_sound_handoff_shapes(self):
        for body in (b"[]", b"{nope", b'{"timeline": 1}', b"{}"):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as d:
                f = build(d)
                self.rehash(f, "sound-handoff.json", body)
                self.fails(f, "sound-handoff.json")

    def test_otio_shapes(self):
        for body in (b"[]", b"{nope", b"{}", b'{"tracks": []}', b'{"tracks": {"children": [1]}}',
                     b'{"tracks": {"children": []}}'):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as d:
                f = build(d)
                self.rehash(f, "timeline.otio", body)
                self.fails(f, "timeline.otio")

    def test_edl_without_events(self):
        f = build(self.tmp)
        self.rehash(f, "timeline.edl", b"TITLE: x\nFCM: NON-DROP FRAME\n")
        self.fails(f, "timeline.edl")

    def test_binary_in_tsv(self):
        f = build(self.tmp)
        self.rehash(f, "markers.tsv", b"\xff\xfe\x00")
        self.fails(f, "markers.tsv")

    def test_folder_missing(self):
        msg = lc.check(os.path.join(self.tmp, "nope_lock_v01"))
        self.assertIn("does not exist", msg)

    def test_folder_is_a_file(self):
        p = Path(self.tmp) / "x_lock_v01"
        p.write_text("x")
        self.assertIn("not a folder", lc.check(str(p)))

    def test_folder_name(self):
        for name in ("example-slug", "example-slug_lock_v1", "example-slug_lock_v001", "_lock_v01",
                     "example-slug_lock_vAB", "example-slug_lock_v01x", "Example_lock_v01"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as d:
                f = build(d, slug=name)
                self.assertIn("<slug>_lock_vNN", lc.check(str(f)))

    def test_trailing_slash_and_dot(self):
        f = build(self.tmp)
        self.assertIsNone(lc.check(str(f) + "/"))
        cwd = os.getcwd()
        try:
            os.chdir(f)
            self.assertIsNone(lc.check("."))
        finally:
            os.chdir(cwd)

    def test_sound_handoff_other_fields_must_equal_lock(self):
        for key, val in (("timeline", "other"), ("fps", "25"), ("start_timecode", "00:00:00:00")):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as d:
                f = build(d)
                s = json.loads((f / "sound-handoff.json").read_text())
                s[key] = val
                self.rehash(f, "sound-handoff.json", json.dumps(s).encode())
                self.fails(f, key)


class Command(Base):
    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(argv)
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_pass_exit_0(self):
        f = build(self.tmp)
        code, out, _ = self.run_cli(["lock-check", str(f)])
        self.assertEqual((code, out.split()[0]), (0, "PASS"))

    def test_fail_exit_1_names_first_mismatch(self):
        f = build(self.tmp)
        (f / "cue-sheet.tsv").unlink()
        code, out, _ = self.run_cli(["lock-check", str(f)])
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("FAIL"))
        self.assertIn("cue-sheet.tsv", out)
        self.assertNotIn("Traceback", out)

    def test_usage_exit_2(self):
        for argv in (["lock-check"], ["lock-check", "--nope", "x"], ["lock-check", "a", "b"]):
            with self.subTest(argv=argv):
                code, out, err = self.run_cli(argv)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertIn("usage", err)

    def test_help_prints_usage_only(self):
        code, out, err = self.run_cli(["lock-check", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("usage", out)

    def test_help_creates_nothing(self):
        before = set(os.listdir(self.tmp))
        self.run_cli(["lock-check", "--help"])
        self.run_cli(["lock-check", "--bogus"])
        self.assertEqual(set(os.listdir(self.tmp)), before)

    def test_missing_folder_exit_1(self):
        code, out, _ = self.run_cli(["lock-check", os.path.join(self.tmp, "a_lock_v01")])
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("FAIL"))


if __name__ == "__main__":
    unittest.main()
