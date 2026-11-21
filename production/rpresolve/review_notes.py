"""
review_notes: a numbered, versioned change list from client review markers.

A round of review markers (the marker TSV of production/cue-marker-sync.md) is
appended to a ledger as numbered notes; notes are closed against the cut that
fixed them; a report lists what is still open. Offline, standard library only,
no Resolve connection. The ledger is a TSV that names the client's notes, so it
is never written inside a git working tree.

  python3 -m rpresolve.review_notes add --markers M.tsv --cut NAME --ledger L.tsv
  python3 -m rpresolve.review_notes resolve --ledger L.tsv --note N-001 --in NAME
  python3 -m rpresolve.review_notes resolve --ledger L.tsv --note N-001 --wontfix --reason TEXT
  python3 -m rpresolve.review_notes report --ledger L.tsv [--rounds-allowed N]
"""

import argparse
import contextlib
import fcntl
import os
import re
import sys
import time
from fractions import Fraction

from rpresolve import paths

MARKER_COLUMNS = ["frame", "timecode", "duration_frames", "name", "colour", "note", "fps"]
LEDGER_COLUMNS = ["note_id", "round", "cut", "timeline_frame", "timecode", "text",
                  "status", "resolved_in", "reason"]
STATUSES = ("open", "done", "wontfix")
TC_RE = re.compile(r"^(\d\d):(\d\d):(\d\d)[:;](\d\d)$")
ID_RE = re.compile(r"^N-(\d{3,})$")
LOCK_WAIT = 10.0  # seconds a command waits for another one on the same ledger folder
CONTROL = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")


class NotesError(ValueError):
    """Bad input, named; main() prints it and exits 1 (2 when it is a usage mistake)."""

    def __init__(self, msg, usage=False):
        super().__init__(msg)
        self.usage = usage


def read_tsv(path, what):
    """(header, [(line number, fields)]) of a TSV file, strictly."""
    if os.path.isdir(path):
        raise NotesError(f"{what} {path} is a directory; give a file path")
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            text = f.read()
    except FileNotFoundError:
        raise NotesError(f"{what} {path} not found")
    except (OSError, UnicodeDecodeError) as e:
        raise NotesError(f"{what} {path} cannot be read as UTF-8 text: {e}")
    if not text.strip():
        raise NotesError(f"{what} {path} is empty: no header row")
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    rows = []
    for n, line in enumerate(lines, 1):
        if line.endswith("\r"):
            line = line[:-1]
        if CONTROL.search(line):
            raise NotesError(f"{what} line {n}: control character in a field")
        rows.append((n, line.split("\t")))
    return rows[0][1], rows[1:]


def check_timecode(n, frame, tc, fps):
    m = TC_RE.match(tc)
    if not m:
        raise NotesError(f"markers line {n}: timecode {tc!r} is not HH:MM:SS:FF or HH:MM:SS;FF")
    nominal = round(fps)
    h, mi, s, ff = map(int, m.groups())
    if mi > 59 or s > 59 or ff >= nominal:
        raise NotesError(f"markers line {n}: timecode {tc} has a field out of range at {nominal} frames")
    # Frame is the authority: a fractional rate (drop-frame or not) has more than one way
    # to write a timecode, so the arithmetic is skipped there and only the shape is held.
    if fps.denominator == 1 and ((h * 60 + mi) * 60 + s) * nominal + ff != frame:
        raise NotesError(f"markers line {n}: timecode {tc} does not match frame {frame}")


def load_markers(path):
    """[(frame, timecode, text)] from a marker TSV; header only gives []."""
    header, rows = read_tsv(path, "markers")
    missing = [c for c in MARKER_COLUMNS if c not in header]
    if missing:
        raise NotesError(f"markers {path}: missing column {', '.join(missing)}")
    ix = {c: header.index(c) for c in MARKER_COLUMNS}
    out = []
    for n, f in rows:
        if len(f) != len(header):
            raise NotesError(f"markers line {n}: {len(f)} fields, the header has {len(header)} "
                             "(a tab or newline inside a field is not allowed)")
        if not f[ix["frame"]].isdigit():
            raise NotesError(f"markers line {n}: frame {f[ix['frame']]!r} is not a whole number from 0")
        frame = int(f[ix["frame"]])
        m = re.match(r"^(\d+)(?:/(\d+))?$", f[ix["fps"]])
        if not m or int(m.group(2) or 1) == 0 or int(m.group(1)) == 0:
            raise NotesError(f"markers line {n}: fps {f[ix['fps']]!r} is not a rate like 24000/1001")
        check_timecode(n, frame, f[ix["timecode"]], Fraction(int(m.group(1)), int(m.group(2) or 1)))
        name, note = f[ix["name"]], f[ix["note"]]
        if not name.strip():
            raise NotesError(f"markers line {n}: empty name")
        out.append((frame, f[ix["timecode"]], f"{name} | {note}" if note.strip() else name))
    return out


def norm(text):
    """Duplicate-detection key: surrounding whitespace stripped, inner runs collapsed, case kept."""
    return " ".join(text.split())


def load_ledger(path):
    """[dict] of ledger rows; every damage is a NotesError naming the line."""
    header, rows = read_tsv(path, "ledger")
    if header != LEDGER_COLUMNS:
        raise NotesError(f"ledger {path}: header is not {', '.join(LEDGER_COLUMNS)}")
    out, seen = [], {}
    for n, f in rows:
        if len(f) != len(LEDGER_COLUMNS):
            raise NotesError(f"ledger line {n}: {len(f)} fields, expected {len(LEDGER_COLUMNS)}")
        r = dict(zip(LEDGER_COLUMNS, f))
        if not ID_RE.match(r["note_id"]):
            raise NotesError(f"ledger line {n}: note_id {r['note_id']!r} is not N-001 style")
        if r["note_id"] in seen:
            raise NotesError(f"ledger line {n}: duplicate note_id {r['note_id']} "
                             f"(first on line {seen[r['note_id']]}); fix it by hand")
        seen[r["note_id"]] = n
        if not r["round"].isdigit() or int(r["round"]) < 1 or not r["timeline_frame"].isdigit():
            raise NotesError(f"ledger line {n}: round and timeline_frame must be whole numbers")
        if r["status"] not in STATUSES:
            raise NotesError(f"ledger line {n}: status {r['status']!r} is not open, done or wontfix")
        if (r["status"] == "done") != bool(r["resolved_in"]) or (
                r["status"] == "wontfix" and not r["reason"]):
            raise NotesError(f"ledger line {n}: {r['note_id']} status {r['status']} "
                             "disagrees with resolved_in or reason")
        out.append(r)
    return out


def write_ledger(path, rows):
    problem = paths.out_problem(path, any_git_tree=True)
    if problem:
        raise NotesError(problem)
    text = "".join("\t".join(r) + "\n" for r in [LEDGER_COLUMNS] + [[x[c] for c in LEDGER_COLUMNS] for x in rows])
    with paths.atomic_path(path) as tmp:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)


@contextlib.contextmanager
def ledger_lock(path):
    """Hold an exclusive flock on the ledger's folder for a whole read-modify-write.
    The folder, not a sidecar file, so nothing is left behind; it also serialises two
    ledgers in one folder, which is cheap and harmless."""
    folder = os.path.dirname(os.path.realpath(path)) or "."
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError as e:
        raise NotesError(f"cannot lock the folder of {path}: {e}")
    try:
        end = time.monotonic() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= end:
                    raise NotesError(f"ledger is busy: another review_notes command holds {folder}; "
                                     "nothing written, try again")
                time.sleep(0.02)
        yield
    finally:
        os.close(fd)  # closing releases the lock


def confirm(path, ids_texts):
    """Re-read the ledger and refuse to report success for a row that is not in it."""
    have = {r["note_id"]: r for r in load_ledger(path)}
    missing = [i for i, (kind, val) in ids_texts.items() if i not in have or have[i][kind] != val]
    if missing:
        raise NotesError(f"after writing, {', '.join(missing)} not in the ledger {path}; "
                         "do not trust this run, check the file")


def clean_name(value, what):
    if not value.strip() or CONTROL.search(value) or "\t" in value:
        raise NotesError(f"{what} must be non-empty text with no tab or newline")
    return value.strip()


def cmd_add(args):
    cut = clean_name(args.cut, "--cut")
    problem = paths.out_problem(args.ledger, any_git_tree=True)
    if problem:
        raise NotesError(problem)
    new = load_markers(args.markers)
    if not new:
        print("0 notes added (the marker file has a header and no rows; no round counted)")
        return 0
    with ledger_lock(args.ledger):
        rows = load_ledger(args.ledger) if os.path.exists(args.ledger) else []
        have = {(r["cut"], r["timeline_frame"], norm(r["text"])): r["note_id"] for r in rows}
        fresh = [m for m in new if (cut, str(m[0]), norm(m[2])) not in have]
        if not fresh:
            raise NotesError(f"{args.markers}: every marker is already in the ledger for {cut}; "
                             "this file was added before, nothing written")
        elsewhere = {(r["timeline_frame"], norm(r["text"])) for r in rows if r["cut"] != cut}
        if all((str(f), norm(t)) in elsewhere for f, _, t in new):
            others = ", ".join(sorted({r["cut"] for r in rows if r["cut"] != cut}))
            print(f"warning: every marker already sits in the ledger under another cut ({others}); "
                  f"check that {cut} is the cut name you meant", file=sys.stderr)
        rnd = max([int(r["round"]) for r in rows] + [0]) + 1
        nid = max([int(ID_RE.match(r["note_id"]).group(1)) for r in rows] + [0])
        added = {}
        for i, (frame, tc, text) in enumerate(fresh, 1):
            row = {"note_id": f"N-{nid + i:03d}", "round": str(rnd), "cut": cut,
                   "timeline_frame": str(frame), "timecode": tc, "text": text,
                   "status": "open", "resolved_in": "", "reason": ""}
            rows.append(row)
            added[row["note_id"]] = ("text", text)
        write_ledger(args.ledger, rows)
        confirm(args.ledger, added)
    print(f"round {rnd}: {len(fresh)} notes added, N-{nid + 1:03d} to N-{nid + len(fresh):03d}")
    repeats = [(f, have[(cut, str(f), norm(t))]) for f, _, t in new if (cut, str(f), norm(t)) in have]
    if repeats:
        print(f"{len(repeats)} already present, not added: "
              + ", ".join(f"{i} (frame {f})" for f, i in repeats))
    return 0


def cmd_resolve(args):
    if args.wontfix == bool(args.in_cut) or (args.wontfix and not args.reason) or (
            args.reason and not args.wontfix):
        raise NotesError("give --in CUT, or --wontfix with --reason TEXT, not both", usage=True)
    problem = paths.out_problem(args.ledger, any_git_tree=True)
    if problem:
        raise NotesError(problem)
    with ledger_lock(args.ledger):
        rows = load_ledger(args.ledger)
        row = next((r for r in rows if r["note_id"] == args.note), None)
        if row is None:
            raise NotesError(f"no note {args.note} in {args.ledger}")
        if row["status"] != "open":
            raise NotesError(f"{args.note} is already {row['status']}; nothing changed")
        if args.wontfix:
            row["status"], row["reason"] = "wontfix", clean_name(args.reason, "--reason")
        else:
            row["status"], row["resolved_in"] = "done", clean_name(args.in_cut, "--in")
        write_ledger(args.ledger, rows)
        r2 = next((r for r in load_ledger(args.ledger) if r["note_id"] == args.note), None)
        if r2 is None or r2["status"] != row["status"]:
            raise NotesError(f"after writing, {args.note} is not {row['status']} in the ledger "
                             f"{args.ledger}; do not trust this run, check the file")
    print(f"{args.note} {row['status']}")
    return 0


def cmd_report(args):
    rows = load_ledger(args.ledger)
    rounds = sorted({int(r["round"]) for r in rows})
    out = ["# Review change list", ""]
    if not rows:
        out.append("No notes.")
    for rnd in rounds:
        mine = [r for r in rows if int(r["round"]) == rnd]
        counts = {s: sum(r["status"] == s for r in mine) for s in STATUSES}
        cuts = ", ".join(dict.fromkeys(r["cut"] for r in mine))
        out += [f"## Round {rnd} (against {cuts})", "",
                f"{counts['open']} open, {counts['done']} done, {counts['wontfix']} wontfix", ""]
        for r in sorted(mine, key=lambda r: (r["status"] != "open", int(r["note_id"][2:]))):
            where = f"{r['timecode']} (frame {r['timeline_frame']})"
            if r["status"] == "open":
                tail = ""
            elif r["status"] == "done":
                tail = f" [done in {r['resolved_in']}]"
            else:
                tail = f" [wontfix: {r['reason']}]"
            box = "[ ]" if r["status"] == "open" else "[x]"
            text = "\\" + r["text"] if r["text"][:1] in ("=", "+", "-", "@") else r["text"]
            out.append(f"- {box} {r['note_id']} {where}: {text}{tail}")
        out.append("")
    if args.rounds_allowed:
        used = len(rounds)
        out.append(f"Rounds: {used} of {args.rounds_allowed} used."
                   if used < args.rounds_allowed else
                   f"Rounds: {used} of {args.rounds_allowed} used. The rounds are used up; "
                   "a further round is outside the scope.")
    print("\n".join(out).rstrip())
    return 0


def positive_int(s):
    if not s.isdigit() or int(s) < 1:
        raise argparse.ArgumentTypeError(f"{s!r} is not a whole number of 1 or more")
    return int(s)


def build_parser():
    p = argparse.ArgumentParser(prog="python3 -m rpresolve.review_notes", allow_abbrev=False,
                                add_help=False, description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", allow_abbrev=False, add_help=False, help="append one round of markers")
    a.add_argument("--markers", required=True, help="marker TSV of one review round")
    a.add_argument("--cut", required=True, help="cut version name the notes were made against")
    a.add_argument("--ledger", required=True, help="ledger TSV, outside any git working tree")
    r = sub.add_parser("resolve", allow_abbrev=False, add_help=False, help="close one note")
    r.add_argument("--ledger", required=True)
    r.add_argument("--note", required=True, help="note id, for example N-001")
    r.add_argument("--in", dest="in_cut", help="cut version name that addressed it")
    r.add_argument("--wontfix", action="store_true")
    r.add_argument("--reason", help="why, required with --wontfix")
    t = sub.add_parser("report", allow_abbrev=False, add_help=False, help="print the Markdown change list")
    t.add_argument("--ledger", required=True)
    t.add_argument("--rounds-allowed", type=positive_int, help="rounds the scope allows")
    return p, {"add": a, "resolve": r, "report": t}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    p, subs = build_parser()
    if not argv or "-h" in argv or "--help" in argv:
        (subs.get(argv[0], p) if argv else p).print_help()
        return 2  # usage was asked for or missing: nothing else runs
    try:
        args = p.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    try:
        return {"add": cmd_add, "resolve": cmd_resolve, "report": cmd_report}[args.cmd](args)
    except NotesError as e:
        if e.usage:
            subs[args.cmd].print_usage(sys.stderr)
        print(f"review_notes: {e}", file=sys.stderr)
        return 2 if e.usage else 1
    except (OSError, paths.OutputRefused) as e:
        print(f"review_notes: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
