"""
lock-check: check one lock handoff folder (production/lock-handoff.md, "The checker's
job") and print PASS, or FAIL with the first mismatch named. Offline and read-only:
it opens files in the folder and writes nothing anywhere. Standard library only; the
OTIO file is read as plain JSON.

    python3 resolve_workflow.py lock-check <project-slug>_lock_vNN

Checks, in this order, stopping at the first that fails:
    the folder   exists, is a folder, is named <slug>_lock_vNN
    lock.json    JSON object; every name in its files is one of the spec's fixed names
    the listing  the folder holds exactly lock.json, SHA256SUMS and the files lock.json
                 lists (no extra entry, no missing one, no symlink, no sub-folder)
    SHA256SUMS   strict sha256sum format (lowercase hex, two spaces, LF), no repeats,
                 names exactly the listed files and lock.json
    hashes       each file's bytes against SHA256SUMS and against lock.json
    timeline     timeline.otio and lock.json agree on the rate and start timecode
    duration     duration_frames equal in lock.json, timeline.otio, the last V1
                 record-out of timeline.edl and sound-handoff.json
    sound        timeline, fps and start_timecode in sound-handoff.json equal lock.json's
    items        items_sha256 against the item list rebuilt from timeline.otio
    consent      every consent-list.tsv row has approved exactly "yes"
    markers      markers.tsv against the marker schema in production/cue-marker-sync.md

Exit 0 pass, 1 fail, 2 usage.
"""

import datetime
import hashlib
import json
import os
import re
import sys
from fractions import Fraction

from rpresolve import cli
from rpresolve.markers import MARKER_COLORS

NAME = "lock-check"
HELP = "Offline: check a lock handoff folder and print PASS or the first FAIL"

REQUIRED = ("timeline.otio", "timeline.edl", "paper-edit.tsv", "markers.tsv", "cue-sheet.tsv",
            "consent-list.tsv", "sound-handoff.json")
OPTIONAL = ("guide.mov",)
FOLDER = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*_lock_v\d\d")
HEX = re.compile(r"[0-9a-f]{64}")
SUMS_LINE = re.compile(r"([0-9a-f]{64})  (.+)")
TC = r"(\d\d):(\d\d):(\d\d)([:;])(\d\d)"
# CMX 3600: cut, dissolve, wipe (W and three digits), key; D and W carry a transition length.
EDL_EVENT = re.compile(r"^\d{3,}\s+\S+\s+(\S+)\s+(?:C|D|W\d{3}|K)(?:\s+\d{1,3})?\s+" + r"\s+".join([TC] * 4) + r"\s*$")
LOCKED_AT = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z")
LANES = ("story:", "snd:", "fx:")
MARKER_COLUMNS = ["frame", "timecode", "duration_frames", "name", "colour", "note", "fps"]
CONSENT_COLUMNS = ["subject", "kind", "timeline_frame", "release_ref", "approved", "approved_by",
                   "approved_on"]


PAPER_COLUMNS = ["order", "clip_id", "source_in", "source_out", "beat", "note"]
CUE_COLUMNS = ["cue_id", "kind", "title", "timeline_frame", "duration_frames", "source", "licence_row"]


class Fail(Exception):
    pass


def add_arguments(p):
    p.add_argument("folder", help="the lock folder, <project-slug>_lock_vNN")


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def need(cond, msg):
    if not cond:
        raise Fail(msg)


def read_bytes(folder, name):
    with open(os.path.join(folder, name), "rb") as f:
        return f.read()


def read_json(folder, name):
    try:
        return json.loads(read_bytes(folder, name).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError) as e:
        raise Fail(f"{name} is not valid JSON ({type(e).__name__})")


def read_tsv(folder, name, columns):
    """The rows below a header that must read exactly `columns`; header only is valid."""
    try:
        text = read_bytes(folder, name).decode("utf-8")
    except UnicodeDecodeError:
        raise Fail(f"{name} is not UTF-8")
    need("\r" not in text, f"{name} holds a carriage return; lines end in LF only")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    need(text != "", f"{name} is empty; a file with nothing to say is still written header only")
    need(lines and lines[0].split("\t") == columns,
         f"{name} header must be exactly: {chr(9).join(columns)}")
    rows = []
    for n, line in enumerate(lines[1:], 2):
        cells = line.split("\t")
        need(len(cells) == len(columns), f"{name} line {n} has {len(cells)} cells, wanted {len(columns)} cells")
        rows.append((n, dict(zip(columns, cells))))
    return rows


def rate_of(text, where):
    try:
        r = Fraction(text)
    except (TypeError, ValueError, ZeroDivisionError):
        raise Fail(f"{where} fps {text!r} is not a frame rate")
    need(r > 0, f"{where} fps {text!r} is not a frame rate")
    return r


def tc_frames(h, m, s, sep, f, rate, drop):
    """Frames from a timecode, by interchange's own reader; the separator must match drop."""
    need(sep == (";" if drop else ":"), "a timecode separator (in timeline.edl or lock.json start_timecode) disagrees with the EDL's FCM line "
         f"({'DROP' if drop else 'NON-DROP'} FRAME wants '{';' if drop else ':'}')")
    from rpresolve import interchange
    return interchange.tc_frames(f"{h}:{m}:{s}{sep}{f}", rate)


def check_folder(path):
    need(os.path.exists(path), f"{path} does not exist")
    need(os.path.isdir(path), f"{path} is not a folder")
    base = os.path.basename(os.path.abspath(path))
    need(FOLDER.fullmatch(base), f"{base} is not named <slug>_lock_vNN (lowercase slug, two-digit version)")
    d = os.path.realpath(path)
    while True:
        need(not os.path.lexists(os.path.join(d, ".git")), f"{base} sits inside a git working tree ({d}); a lock folder never does")
        up = os.path.dirname(d)
        if up == d:
            break
        d = up


def check_lock(folder):
    need(os.path.isfile(os.path.join(folder, "lock.json")), "lock.json is missing")
    lock = read_json(folder, "lock.json")
    need(isinstance(lock, dict), "lock.json is not a JSON object")
    files, tl = lock.get("files"), lock.get("timeline")
    need(lock.get("schema") == {"name": "shared.lock-handoff", "version": 1},
         'lock.json schema must be {"name": "shared.lock-handoff", "version": 1}')
    la = lock.get("locked_at")
    need(isinstance(la, str) and LOCKED_AT.fullmatch(la), "lock.json locked_at is not ISO 8601 UTC (YYYY-MM-DDTHH:MM:SSZ)")
    try:
        datetime.datetime.strptime(la[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise Fail("lock.json locked_at is not a real date and time")
    need(isinstance(lock.get("project"), str) and is_int(lock.get("lock_version")) and 1 <= lock["lock_version"] <= 99,
         "lock.json project or lock_version is missing or invalid")
    need(isinstance(files, dict), "lock.json has no files object")
    need(isinstance(tl, dict), "lock.json has no timeline object")
    for n, h in files.items():
        need(n in REQUIRED + OPTIONAL, f"lock.json lists {n!r}, which is not one of the spec's file names")
        need(isinstance(h, str) and HEX.fullmatch(h), f"lock.json hash for {n} is not 64 lowercase hex")
    for n in REQUIRED:
        need(n in files, f"{n} is missing from lock.json")
    need(is_int(tl.get("duration_frames")), "lock.json timeline.duration_frames is not an integer")
    for k in ("name", "fps", "start_timecode"):
        need(isinstance(tl.get(k), str), f"lock.json timeline.{k} is not a string")
    need(isinstance(lock.get("items_sha256"), str) and HEX.fullmatch(lock["items_sha256"]),
         "lock.json items_sha256 is not 64 lowercase hex")
    return lock


def check_name(folder, lock):
    want = f"{lock['project']}_lock_v{lock['lock_version']:02d}"
    got = os.path.basename(os.path.abspath(folder))
    need(got == want, f"folder name {got} does not match lock.json project and lock_version ({want})")


def check_listing(folder, files):
    on_disk = set(os.listdir(folder))
    for n in sorted(on_disk):
        need(n in files or n in ("lock.json", "SHA256SUMS"),
             f"{n} is in the folder but not in lock.json (or SHA256SUMS)")
    for n in sorted(set(files) | {"lock.json", "SHA256SUMS"}):
        need(n in on_disk, f"{n} is missing from the folder")
        need(not os.path.islink(os.path.join(folder, n)), f"{n} is a symlink")
        need(os.path.isfile(os.path.join(folder, n)), f"{n} is not a regular file")


def check_sums(folder, files):
    try:
        text = read_bytes(folder, "SHA256SUMS").decode("utf-8")
    except UnicodeDecodeError:
        raise Fail("SHA256SUMS is not UTF-8")
    need("\r" not in text, "SHA256SUMS has CR line ends; sha256sum format is LF")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    sums = {}
    for n, line in enumerate(lines, 1):
        m = SUMS_LINE.fullmatch(line)
        need(m, f"SHA256SUMS line {n} is not '<64 lowercase hex>  <name>'")
        need(m.group(2) not in sums, f"SHA256SUMS names {m.group(2)} twice")
        sums[m.group(2)] = m.group(1)
    for n in sorted(set(files) | {"lock.json"}):
        need(n in sums, f"{n} is not in SHA256SUMS")
    for n in sorted(sums):
        need(n in files or n == "lock.json", f"SHA256SUMS names {n}, which is not in the folder spec or lock.json")
    return sums


def check_hashes(folder, sums, files):
    for n in sorted(sums):
        got = hashlib.sha256(read_bytes(folder, n)).hexdigest()
        need(got == sums[n], f"{n} hash does not match SHA256SUMS")
        if n in files:
            need(got == files[n], f"{n} hash does not match lock.json")


def frames(v, rate, where):
    """A RationalTime dict as whole frames at the timeline rate."""
    need(isinstance(v, dict) and isinstance(v.get("value"), (int, float))
         and not isinstance(v.get("value"), bool) and isinstance(v.get("rate"), (int, float)),
         f"timeline.otio {where} is not a RationalTime")
    need(abs(v["rate"] - float(rate)) < 1e-3, f"timeline.otio {where} rate {v['rate']} is not {float(rate):.3f}")
    need(v["value"] == int(v["value"]) and v["value"] >= 0, f"timeline.otio {where} is not a whole frame count")
    return int(v["value"])


def otio_items(doc, rate):
    """(items, duration, start_frames, other_tracks): video items as the spec's items hash builds
    them; duration is the longest video track; other_tracks is [(name, frames)] for the rest."""
    need(isinstance(doc, dict) and isinstance(doc.get("tracks"), dict)
         and isinstance(doc["tracks"].get("children"), list), "timeline.otio has no tracks.children list")
    start = frames(doc.get("global_start_time"), rate, "global_start_time")
    items, duration, track_no, longest, other = [], 0, 0, 0, []
    for tr in doc["tracks"]["children"]:
        need(isinstance(tr, dict) and isinstance(tr.get("children"), list), "timeline.otio has a malformed track")
        if tr.get("kind") != "Video":
            end = 0
            for c in tr["children"]:
                rng = c.get("source_range") if isinstance(c, dict) else None
                if isinstance(rng, dict):
                    end += frames(rng.get("duration"), rate, "duration")
            longest = max(longest, end)
            other.append((tr.get("name") or tr.get("kind"), end))
            continue
        track_no += 1
        t, index = 0, 0
        for c in tr["children"]:
            need(isinstance(c, dict), "timeline.otio has a malformed track child")
            kind = str(c.get("OTIO_SCHEMA", "")).split(".")[0]
            need(kind in ("Clip", "Gap"), f"timeline.otio holds a {kind or 'unnamed'} item on V{track_no}, which this check does not read")
            rng = c.get("source_range")
            need(isinstance(rng, dict), f"timeline.otio V{track_no} item has no source_range")
            d = frames(rng.get("duration"), rate, "duration")
            if kind == "Clip":
                index += 1
                s = frames(rng.get("start_time"), rate, "start_time")
                meta = c.get("metadata")
                cid = meta.get("clip_id") if isinstance(meta, dict) else None
                need(isinstance(c.get("name"), str) and isinstance(cid, str) and cid,
                     f"timeline.otio V{track_no} item {index} has no name or metadata.clip_id")
                items.append({"track": track_no, "index": index, "name": c["name"], "record_in": t,
                              "record_out": t + d, "clip_id": cid, "source_in": s, "source_out": s + d})
            t += d
        duration = max(duration, t)
    need(items, "timeline.otio has no video items")
    return items, duration, start, other


def edl_drop(text):
    m = re.search(r"^FCM:\s*(NON-DROP|DROP) FRAME\s*$", text, re.M)
    return bool(m) and m.group(1) == "DROP"


def edl_events(text, rate, start_tc, drop):
    """[(record_in, record_out)] of the V events, in file order, from the start timecode. An event
    whose record in equals its record out is the zero-length half of a dissolve: not an item."""
    out = []
    for line in text.splitlines():
        if re.match(r"^\d{3,}\s", line):
            m = EDL_EVENT.match(line)
            need(m, f"timeline.edl event line is not HH:MM:SS:FF CMX 3600: {line.strip()[:60]!r}")
            if m.group(1) == "V":
                g = m.groups()
                a, b = tc_frames(*g[11:16], rate, drop) - start_tc, tc_frames(*g[16:21], rate, drop) - start_tc
                if a != b:
                    out.append((a, b))
    need(out, "timeline.edl has no V1 event")
    return out


def check_markers(folder, rate, fps, drop):
    from rpresolve import interchange
    prev = None
    for n, r in read_tsv(folder, "markers.tsv", MARKER_COLUMNS):
        w = f"markers.tsv line {n}"
        need(re.fullmatch(r"\d+", r["frame"]), f"{w}: frame {r['frame']!r} is not a non-negative integer")
        fr = int(r["frame"])
        need(r["timecode"] == interchange.timecode(fr, rate, drop),
             f"{w}: timecode {r['timecode']!r} does not match frame {fr} at {fps}")
        need(re.fullmatch(r"\d+", r["duration_frames"]) and int(r["duration_frames"]) >= 1,
             f"{w}: duration_frames {r['duration_frames']!r} is not an integer of 1 or more")
        need(r["name"].startswith(LANES) and r["name"].split(":", 1)[1] != "",
             f"{w}: name {r['name']!r} must be a lane prefix ({', '.join(LANES)}) then text")
        need(r["colour"] in MARKER_COLORS, f"{w}: colour {r['colour']!r} is not a Resolve marker colour")
        need(r["fps"] == fps, f"{w}: fps {r['fps']!r} is not the timeline rate {fps}")
        key = (fr, r["name"])
        need(prev is None or prev <= key, f"{w}: rows are not sorted by frame, then name")
        prev = key


def check_consent(folder):
    for n, r in read_tsv(folder, "consent-list.tsv", CONSENT_COLUMNS):
        need(r["approved"] == "yes", f"consent-list.tsv line {n}: approved is {r['approved']!r}, not 'yes'")


def verify(folder):
    check_folder(folder)
    lock = check_lock(folder)
    files, tl = lock["files"], lock["timeline"]
    check_name(folder, lock)
    check_listing(folder, files)
    check_hashes(folder, check_sums(folder, files), files)
    rate = rate_of(tl["fps"], "lock.json")
    start = re.fullmatch(TC, tl["start_timecode"])
    need(start, f"lock.json start_timecode {tl['start_timecode']!r} is not HH:MM:SS:FF")
    edl_text = read_bytes(folder, "timeline.edl").decode("utf-8", "replace")
    drop = edl_drop(edl_text)
    start_frames = tc_frames(*start.groups(), rate, drop)
    doc = read_json(folder, "timeline.otio")
    items, otio_dur, otio_start, other = otio_items(doc, rate)
    need(otio_start == start_frames, "timeline.otio global_start_time does not equal lock.json start_timecode")
    sound = read_json(folder, "sound-handoff.json")
    need(isinstance(sound, dict), "sound-handoff.json is not a JSON object")
    ev = edl_events(edl_text, rate, start_frames, drop)
    durs = {"lock.json": tl["duration_frames"], "timeline.otio": otio_dur,
            "timeline.edl": ev[-1][1],
            "sound-handoff.json": sound.get("duration_frames")}
    need(all(is_int(v) for v in durs.values()) and len(set(durs.values())) == 1,
         "duration_frames disagrees: " + ", ".join(f"{k} {v}" for k, v in durs.items()))
    v1 = [(i["record_in"], i["record_out"]) for i in items if i["track"] == 1]
    need(len(ev) == len(v1), f"timeline.edl has {len(ev)} V1 events, timeline.otio has {len(v1)} V1 items")
    for n, (e, o) in enumerate(zip(ev, v1), 1):
        need(e == o, f"timeline.edl V1 event {n} runs record frames {e[0]}-{e[1]}, timeline.otio item {n} runs {o[0]}-{o[1]}")
    need(sound.get("schema") == {"name": "sound.handoff", "version": 1},
         'sound-handoff.json schema must be {"name": "sound.handoff", "version": 1}')
    for name, n in other:
        need(n <= tl["duration_frames"], f"timeline.otio track {name} runs {n} frames, longer than duration_frames {tl['duration_frames']}")
    for k, lk in (("timeline", "name"), ("fps", "fps"), ("start_timecode", "start_timecode")):
        need(sound.get(k) == tl[lk], f"sound-handoff.json {k} {sound.get(k)!r} does not equal lock.json timeline.{lk} {tl[lk]!r}")
    ordered = sorted(items, key=lambda i: (i["track"], i["record_in"]))
    got = hashlib.sha256(json.dumps(ordered, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()
    need(got == lock["items_sha256"], "items_sha256 does not match the items rebuilt from timeline.otio")
    check_consent(folder)
    check_markers(folder, rate, tl["fps"], drop)
    for name, cols in (("paper-edit.tsv", PAPER_COLUMNS), ("cue-sheet.tsv", CUE_COLUMNS)):
        read_tsv(folder, name, cols)


def check(folder):
    """None when the folder passes, else the first mismatch as one line."""
    try:
        verify(folder)
    except Fail as e:
        return str(e)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError) as e:
        return f"could not read the folder ({type(e).__name__}: {e})"
    return None


def run(args):
    msg = check(args.folder)
    if msg:
        print(f"FAIL {msg}")
        return 1
    print(f"PASS {os.path.basename(os.path.abspath(args.folder))}")
    return 0


if __name__ == "__main__":
    cli.script(__name__)
