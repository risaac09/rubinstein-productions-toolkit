"""
card: read, check and keep the record of a copied camera card (production/card-record.md).

    card show PATH                     the record, its proofs and what is still open
    card check PATH...                 one line per card: PASS, or FAIL naming S1 to S5; exit 1 on any
                                       failure; a root checks every card under it; --quick skips hashing
    card init --retrofit FOLDER --cam CAM
                                       a folder with no record: write its SHA256SUMS and a schema 2 record
    card set PATH --squeeze F [--clip ID]   record the squeeze answer (1 is spherical); --project SLUG
    card mv PATH DEST_PARENT           move a card folder and its sidecars on the same volume, verify
                                       by hash, and append the move to the record's history

Every writing subcommand is a dry run until --apply, refuses to write inside a git tree, and
never deletes anything or overwrites a SHA256SUMS, offsite.txt or squeeze.json. `set` and `mv`
replace the card.json atomically, keeping every key it had. Never connects to Resolve.
"""

import json
import os
import sys

from rpresolve import card as rpcard
from rpresolve import cli
from rpresolve import squeeze as rpsqueeze

NAME = "card"
HELP = "Offline: show, check, retrofit, answer and move a copied card's record"


def add_arguments(p):
    p.add_argument("action", choices=("show", "check", "init", "set", "mv"))
    p.add_argument("paths", nargs="+", help="a path in or beside a card copy; check: or a root of many; mv: PATH DEST_PARENT")
    p.add_argument("--apply", action="store_true", help="write (init, set, mv); without it they only say what they would do")
    p.add_argument("--quick", action="store_true", help="check, mv: compare the file set and counts, skip hashing")
    p.add_argument("--json", action="store_true", help="show: print the loaded card as JSON")
    p.add_argument("--depth", type=int, default=6, help="check: how deep under a root to look for cards (default 6)")
    p.add_argument("--retrofit", action="store_true", help="init: required; the folder has no record")
    p.add_argument("--cam", help="init: camera code, 2 to 12 of A-Z 0-9")
    p.add_argument("--squeeze", type=float, metavar="FACTOR", help="set: the squeeze factor; 1 is spherical")
    p.add_argument("--clip", action="append", metavar="ID", help="set: only this clip (clip id or file name; repeatable)")
    p.add_argument("--project", help="set: the project slug")


def _fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    return 1


def show(path, as_json):
    c = rpcard.load(path)
    if as_json:
        print(json.dumps(c, indent=2, default=str))
        return 0
    r = c["record"] or {}
    answered = [k for k in c["clips"] if f"squeeze {k['clip_id']}" not in c["open"]]
    print(f"{c['root']}\n  record    schema {c['version'] or 'none'}"
          f"{', ' + r['origin'] if r.get('origin') else ''}, {r.get('cam', '?')} {r.get('card_id', '?')}, "
          f"{r.get('files', '?')} files, {r.get('bytes', '?')} bytes")
    sh = r.get("shoot") or {}
    print(f"  shoot     {', '.join(sh.get('days') or []) or '(not recorded)'}   project {r.get('project') or '(none)'}")
    print(f"  clips     {len(c['clips'])}, squeeze answered for {len(answered)}"
          + ("  (legacy squeeze.json folded in)" if c["legacy"] else ""))
    print(f"  sums      {len(c['sums']['files'])} files" if c["sums"]["present"] else "  sums      none")
    off = c["offsite"]
    print(f"  offsite   {off['remote']} at {off['checked_utc']}" if off["ok"] else "  offsite   none")
    for p in c["problems"]:
        print(f"  problem   {p}")
    for o in c["open"]:
        print(f"  open      {o}")
    return 0


def check(paths, quick, depth):
    folders = []
    for p in paths:
        try:
            folders.append((rpcard.find(p), None))
        except rpcard.CardError:
            if not os.path.isdir(p):
                return _fail(f"{p}: not found")
            found = rpcard.cards(p, depth)
            if not found:
                return _fail(f"{p}: no card copies under it")
            folders += [(f, os.path.abspath(p)) for f in found]
    bad = 0
    for f, root in folders:
        label = os.path.relpath(f, root) if root else f
        try:
            res = rpcard.check(rpcard.load(f), full=not quick)
        except (OSError, rpcard.CardError) as e:
            res = {s: [str(e)] if s == "S1" else [] for s in rpcard.STANDARDS}
        failing = [s for s in rpcard.STANDARDS if res[s]]
        if not failing:
            print(f"PASS  {label}  S1 S2 S3 S4 S5")
            continue
        bad += 1
        why = "; ".join(f"{s} {res[s][0]}" + (f" (+{len(res[s]) - 1} more)" if len(res[s]) > 1 else "") for s in failing)
        print(f"FAIL  {label}  {' '.join(failing)}: {why}")
    print(f"card check: {len(folders)} card(s), {bad} fail", file=sys.stderr)
    return 1 if bad else 0


def _dry(args, lines):
    for ln in lines:
        print(("" if args.apply else "would: ") + ln)
    if not args.apply:
        print("dry run: nothing written; pass --apply")


def init(args):
    if not args.retrofit:
        return _fail("init needs --retrofit (a folder that has no record)")
    if len(args.paths) != 1 or not os.path.isdir(args.paths[0]):
        return _fail("init takes one existing folder")
    if not args.cam:
        return _fail("init needs --cam CAM")
    root = os.path.abspath(args.paths[0]).rstrip(os.sep)
    rpcard.refuse_git(root)
    if os.path.exists(root + rpcard.RECORD):
        return _fail(f"{root + rpcard.RECORD} exists; a record is never overwritten")
    lst = rpcard.listing(root)
    if not lst:
        return _fail(f"no files in {root}")
    lhash = rpcard.listing_hash(lst)
    rec = {"card_id": lhash[:12], "listing_hash": lhash, "volume_uuid": None, "cam": args.cam,
           "files": len(lst), "bytes": sum(s for _, s in lst), "offloaded_utc": rpcard.now(),
           "schema": dict(rpcard.SCHEMA), "origin": "retrofit",
           "shoot": rpcard.shoot_from(root, [r for r, _ in lst]), "project": None, "answers": {},
           "clips": rpcard.derive_clips(f"{args.cam}-{lhash[:6]}", [r for r, _ in lst]), "history": []}
    bad = rpcard.validate(rec)
    if bad:
        return _fail("; ".join(bad))
    sums = root + rpcard.SUMS
    have = rpcard.read_sums(root) if os.path.exists(sums) else None
    steps = [f"hash {len(lst)} file(s) and write {sums}" if not have else f"keep {sums} (it exists; it must verify)",
             f"write {root + rpcard.RECORD}: schema 2, retrofit, card_id {rec['card_id']}, {len(rec['clips'])} clip(s)"]
    _dry(args, steps)
    if not args.apply:
        return 0
    if have:
        card = {"root": root, "sums": have, "record": rec}
        problems = rpcard.verify_sums(card, full=True)
        if problems:
            return _fail("existing SHA256SUMS does not verify: " + "; ".join(problems))
    else:
        text = "".join(f"{rpcard.sha256_file(os.path.join(root, r))}  {r}\n" for r, _ in lst)
        if rpcard.listing(root) != lst:
            return _fail("the folder changed while it was hashed; nothing written")
        rpcard.write_new(sums, text)
    rpcard.write_new(root + rpcard.RECORD, rpcard.dumps(rec))
    return 0


def set_(args):
    if len(args.paths) != 1:
        return _fail("set takes one path")
    if args.squeeze is None and not args.project:
        return _fail("set wants --squeeze FACTOR or --project SLUG")
    card = rpcard.load(args.paths[0])
    f = None if args.squeeze is None else (int(args.squeeze) if float(args.squeeze).is_integer() else args.squeeze)
    rec = rpcard.plan_set(card, default=None if args.clip else f,
                          clips={c: f for c in args.clip or []} if f is not None else None, project=args.project)
    lines = []
    if f is not None:
        lines.append(f"squeeze {f:g} for {len(args.clip)} clip(s)" if args.clip else f"squeeze default {f:g}")
    if args.project:
        lines.append(f"project {args.project}")
    if card["version"] == 1:
        lines.append("upgrade the record from schema 1 to 2")
    _dry(args, [f"replace {card['root'] + rpcard.RECORD}: " + ", ".join(lines)])
    if args.apply:
        rpcard.write_record(card["root"], rec)
    return 0


def mv(args):
    if len(args.paths) != 2:
        return _fail("mv takes the card and the destination folder")
    card = rpcard.load(args.paths[0])
    if card["version"] is None:
        return _fail(f"{card['name']}: no readable record; run init --retrofit first")
    root, dest = card["root"], os.path.abspath(args.paths[1]).rstrip(os.sep)
    if not os.path.isdir(dest):
        return _fail(f"destination missing: {dest} (it is never created)")
    if os.path.dirname(root) == dest:
        return _fail("the card is already there")
    if dest == root or dest.startswith(root + os.sep):
        return _fail("the destination is inside the card")
    rpcard.refuse_git(root, dest)
    if os.stat(os.path.dirname(root)).st_dev != os.stat(dest).st_dev:
        return _fail("a move across volumes means a copy and a delete; not done here (offload.sh copies, then you delete)")
    names = [card["name"]] + [card["name"] + s for s in rpcard.SIBLINGS if os.path.isfile(root + s)]
    clash = [n for n in names if os.path.lexists(os.path.join(dest, n))]
    if clash:
        return _fail(f"destination already has {', '.join(clash)}; nothing is overwritten")
    problems = rpcard.verify_sums(card, full=False)
    if problems:
        return _fail("the card does not match its SHA256SUMS before the move: " + "; ".join(problems))
    new = os.path.join(dest, card["name"])
    _dry(args, [f"move {', '.join(names)} to {dest}, verify {'the file set' if args.quick else 'by hash'}, "
                f"append the move to history"])
    if not args.apply:
        return 0
    rec, old_parent, moved = rpcard.upgrade(card), os.path.dirname(root), []
    try:
        for n in names:
            os.rename(os.path.join(old_parent, n), os.path.join(dest, n))
            moved.append(n)
        problems = rpcard.verify_sums({**card, "root": new, "sums": rpcard.read_sums(new)}, full=not args.quick)
    except OSError as e:
        problems = [str(e)]
    if problems:
        for n in reversed(moved):
            os.rename(os.path.join(dest, n), os.path.join(old_parent, n))
        return _fail("move undone, it did not verify: " + "; ".join(problems))
    rec["history"] = list(rec["history"]) + [{"at": rpcard.now(), "event": "moved", "from": root, "to": new}]
    rpcard.write_record(new, rec)
    print(f"moved {root} -> {new}")
    return 0


def run(args):
    """Never connects to Resolve. Exit status: 0 done, 1 a card fails a check or a usage error."""
    try:
        if args.action == "show":
            return show(args.paths[0], args.json) if len(args.paths) == 1 else _fail("show takes one path")
        if args.action == "check":
            return check(args.paths, args.quick, args.depth)
        return {"init": init, "set": set_, "mv": mv}[args.action](args)
    except (rpcard.CardError, rpsqueeze.SqueezeError) as e:
        return _fail(e)


if __name__ == "__main__":
    cli.script(__name__)
