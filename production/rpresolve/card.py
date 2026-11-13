"""
rpresolve.card: the card record, read and checked in one place. Spec: production/card-record.md.

A copied card is a folder with proofs and answers beside it:

    <parent>/<folder>/                 the card copy
    <parent>/<folder>.card.json        the record (schema 1 from offload.sh, schema 2 here)
    <parent>/<folder>.SHA256SUMS       one sha256 per file, written by offload.sh
    <parent>/<folder>.offsite.txt      written by `offload.sh safe` once an offsite copy verifies
    <parent>/<folder>.squeeze.json     legacy squeeze answers, read as answers.squeeze

    load(path)      any path in or beside a card -> the record, the proofs and what is open
    validate(rec)   problems with a record as stored; [] when sound
    check(card)     the five standards S1 to S5, each a list of problems
    cards(root)     every card folder under a root

The standards: S1 a sound record beside the folder; S2 a SHA256SUMS that verifies the folder;
S3 a squeeze answer for every clip (a record, or a BRAW file's own tag); S4 an offsite.txt
for this card; S5 the folder named <CAM>-<id6>. Stdlib only.
"""

import datetime
import hashlib
import json
import os
import re

from . import paths as rppaths
from . import sidecar, squeeze

RECORD, SUMS, OFFSITE, LEGACY_SQUEEZE = ".card.json", ".SHA256SUMS", ".offsite.txt", squeeze.SUFFIX
SIBLINGS = (RECORD, SUMS, OFFSITE, LEGACY_SQUEEZE)
SEVEN = ("card_id", "listing_hash", "volume_uuid", "cam", "files", "bytes", "offloaded_utc")
KEYS_V2 = SEVEN + ("schema", "origin", "shoot", "project", "answers", "clips", "history")
OPTIONAL = ("note",)  # written by hand on two early retrofits
SCHEMA = {"name": "card.record", "version": 2}
STANDARDS = ("S1", "S2", "S3", "S4", "S5")

VIDEO = {".mov", ".mp4", ".m4v", ".mxf", ".braw", ".mts", ".m2ts", ".avi", ".r3d"}
PROXY_DIRS = {"proxy", "proxies"}
JUNK_DIRS = {".Spotlight-V100", ".fseventsd", ".Trashes", ".TemporaryItems", "System Volume Information"}
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_AT, _DAY, _HEX = sidecar._AT, re.compile(r"^\d{4}-\d{2}-\d{2}$"), re.compile(r"^[0-9a-f]+$")


class CardError(ValueError):
    """A card that cannot be found or written; the message is one line."""


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- finding a card ---------------------------------------------------------

def find(path):
    """The card folder for any path in or beside a card copy (a file inside, the folder,
    or one of its sidecars). A folder with a record or a SHA256SUMS beside it is a card,
    whatever its name."""
    p = os.path.abspath(path)
    for suf in SIBLINGS:
        if p.endswith(suf) and os.path.isfile(p) and os.path.isdir(p[:-len(suf)]):
            return p[:-len(suf)]
    d = p if os.path.isdir(p) else os.path.dirname(p)
    while True:
        if os.path.isdir(d) and any(os.path.isfile(d + s) for s in (RECORD, SUMS)):
            return d
        up = os.path.dirname(d)
        if up == d:
            raise CardError(f"{path}: not in or beside a card copy (no .card.json or .SHA256SUMS beside a folder)")
        d = up


def cards(root, depth=6):
    """Every card folder under `root` (or `root` itself when it is one), sorted."""
    root = os.path.abspath(root)
    found = []
    for d, dirs, files in os.walk(root):
        here = {f[:-len(s)] for f in files for s in (RECORD, SUMS) if f.endswith(s)}
        mine = sorted(n for n in here if n in dirs)
        found += [os.path.join(d, n) for n in mine]
        dirs[:] = sorted(n for n in dirs if n not in mine)
        if d[len(root):].count(os.sep) >= depth:
            dirs[:] = []
    return found


# ---- the folder itself -------------------------------------------------------

def listing(folder):
    """[(relpath, size)] of the files on a card copy, sorted bytewise, OS junk left out,
    as offload.sh lists a card."""
    out = []
    for d, dirs, files in os.walk(folder):
        dirs[:] = [n for n in dirs if n not in JUNK_DIRS]
        for f in files:
            full = os.path.join(d, f)
            if f == ".DS_Store" or f.startswith("._") or os.path.islink(full):
                continue
            out.append((os.path.relpath(full, folder).replace(os.sep, "/"), os.path.getsize(full)))
    return sorted(out, key=lambda t: t[0].encode())


def listing_hash(lst):
    text = "".join(f"{r} {s}\n" for r, s in lst)
    return hashlib.sha256(text.encode()).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def derive_clips(prefix, relpaths):
    """[{clip_id, relpath, proxy}] for the video originals among `relpaths`. A video in a
    Proxy folder is the proxy of the original beside that folder with the same stem; a
    proxy with no original is listed as a clip of its own."""
    vids = [r for r in relpaths if os.path.splitext(r)[1].lower() in VIDEO]
    def is_proxy(r):
        return any(c.lower() in PROXY_DIRS for c in r.split("/")[:-1])
    originals = {(os.path.dirname(r), sidecar.stem(r)): r for r in vids if not is_proxy(r)}
    proxy_of, orphans = {}, []
    for r in vids:
        if not is_proxy(r):
            continue
        d = os.path.dirname(r)
        while d and os.path.basename(d).lower() not in PROXY_DIRS:
            d = os.path.dirname(d)
        orig = originals.get((os.path.dirname(d), sidecar.stem(r)))
        if orig:
            proxy_of.setdefault(orig, r)
        else:
            orphans.append(r)
    keep = sorted([r for r in vids if not is_proxy(r)] + orphans)
    return [{"clip_id": f"{prefix}/{sidecar.stem(r)}", "relpath": r, "proxy": proxy_of.get(r)} for r in keep]


# ---- the proofs --------------------------------------------------------------

def read_sums(folder):
    """{path, present, files: {relpath: hash}, bad: [lines]} for <folder>.SHA256SUMS."""
    out = {"path": folder + SUMS, "present": os.path.isfile(folder + SUMS), "files": {}, "bad": []}
    if out["present"]:
        with open(out["path"], encoding="utf-8", errors="replace") as f:
            for n, line in enumerate(f, 1):
                h, sep, name = line.rstrip("\n").partition("  ")
                if sep and re.fullmatch(r"[0-9a-f]{64}", h) and name and name not in out["files"]:
                    out["files"][name] = h
                else:
                    out["bad"].append(n)
    return out


def read_offsite(folder, card_id):
    """{path, present, ok, remote, checked_utc}; ok when it names this card and a remote."""
    out = {"path": folder + OFFSITE, "present": os.path.isfile(folder + OFFSITE),
           "ok": False, "remote": None, "checked_utc": None}
    if out["present"]:
        kv = {}
        with open(out["path"], encoding="utf-8", errors="replace") as f:
            for line in f:
                k, _, v = line.strip().partition(" ")
                kv[k] = v
        out.update(remote=kv.get("remote") or None, checked_utc=kv.get("checked_utc") or None)
        out["ok"] = bool(card_id and kv.get("card_id") == card_id and out["remote"] and out["checked_utc"])
    return out


def verify_sums(card, full=True):
    """Problems with a folder against its SHA256SUMS and record (S2); [] when it verifies.
    full=False compares the file set and the record's counts and skips the hashing."""
    sums = card["sums"]
    if not sums["present"]:
        return ["no SHA256SUMS beside the folder"]
    bad = [f"SHA256SUMS line {n} is not '<sha256>  <path>'" for n in sums["bad"][:3]]
    lst = listing(card["root"])
    here, listed = {r for r, _ in lst}, set(sums["files"])
    for what, names in (("listed but missing", listed - here), ("on disk but not listed", here - listed)):
        if names:
            bad.append(f"{len(names)} file(s) {what}: {', '.join(sorted(names)[:3])}")
    rec = card["record"] or {}
    for key, have in (("files", len(lst)), ("bytes", sum(s for _, s in lst))):
        if isinstance(rec.get(key), int) and rec[key] != have:
            bad.append(f"record says {key} {rec[key]}, folder has {have}")
    if full:
        differs = [r for r in sorted(listed & here)
                   if sha256_file(os.path.join(card["root"], r)) != sums["files"][r]]
        if differs:
            bad.append(f"{len(differs)} file(s) differ from SHA256SUMS: {', '.join(differs[:3])}")
    return bad


# ---- the record --------------------------------------------------------------

def _factor_problems(where, f):
    return [f"{where}: {factor_msg}" for factor_msg in [squeeze.factor_problem(f)] if factor_msg]


def validate(rec):
    """Problems with a record as stored, as strings; [] when sound. Schema 1 is the seven
    keys offload.sh writes; schema 2 adds schema, origin, shoot, project, answers, clips, history."""
    if not isinstance(rec, dict):
        return ["record is not an object"]
    bad = []
    if not (isinstance(rec.get("card_id"), str) and re.fullmatch(r"[0-9a-f]{12}", rec["card_id"])):
        bad.append("card_id must be 12 hex characters")
    if not (isinstance(rec.get("listing_hash"), str) and re.fullmatch(r"[0-9a-f]{64}", rec["listing_hash"])):
        bad.append("listing_hash must be 64 hex characters")
    if not (rec.get("volume_uuid") is None or isinstance(rec.get("volume_uuid"), str)):
        bad.append("volume_uuid must be a string or null")
    if not (isinstance(rec.get("cam"), str) and re.fullmatch(r"[A-Z0-9]{2,12}", rec["cam"])):
        bad.append("cam must be 2 to 12 of A-Z 0-9")
    for k in ("files", "bytes"):
        if not (isinstance(rec.get(k), int) and not isinstance(rec.get(k), bool) and rec[k] >= 0):
            bad.append(f"{k} must be a whole number")
    if not (isinstance(rec.get("offloaded_utc"), str) and _AT.match(rec["offloaded_utc"])):
        bad.append("offloaded_utc must be YYYY-MM-DDTHH:MM:SSZ")
    if "schema" not in rec:
        return bad  # schema 1: nothing more is required
    if rec["schema"] != SCHEMA:
        bad.append(f"schema must be {json.dumps(SCHEMA)}")
        if isinstance(rec["schema"], dict) and isinstance(rec["schema"].get("version"), int) \
                and rec["schema"]["version"] > SCHEMA["version"]:
            return bad  # a newer record: its other keys are not ours to judge
    bad += [f"{k} missing" for k in KEYS_V2 if k not in rec]
    extra = sorted(set(rec) - set(KEYS_V2) - set(OPTIONAL))
    if extra:
        bad.append("unknown top-level keys: " + ", ".join(extra))
    if "origin" in rec and rec["origin"] not in ("offload", "retrofit"):
        bad.append("origin must be offload or retrofit")
    bad += _shoot_problems(rec.get("shoot", {})) if "shoot" in rec else []
    if "project" in rec and not (rec["project"] is None or (isinstance(rec["project"], str) and SLUG.match(rec["project"]))):
        bad.append("project must be a slug (a-z 0-9 and hyphens) or null")
    prefix = f"{rec['cam']}-{rec['card_id'][:6]}" if not any("cam" in b or "card_id" in b for b in bad) else None
    clip_ids = set()
    if "clips" in rec:
        if not isinstance(rec["clips"], list):
            bad.append("clips must be a list")
        for c in rec["clips"] if isinstance(rec["clips"], list) else []:
            ok = isinstance(c, dict) and set(c) == {"clip_id", "relpath", "proxy"} \
                and isinstance(c["clip_id"], str) and isinstance(c["relpath"], str) \
                and (c["proxy"] is None or isinstance(c["proxy"], str))
            if not ok:
                bad.append("each clip is {clip_id, relpath, proxy}")
                continue
            if c["clip_id"] in clip_ids:
                bad.append(f"clip_id {c['clip_id']} appears twice (two files share a stem)")
            clip_ids.add(c["clip_id"])
            if prefix and not c["clip_id"].startswith(prefix + "/"):
                bad.append(f"clip_id {c['clip_id']} does not start with {prefix}/")
            for rel in (c["relpath"], c["proxy"]):
                if rel and (rel.startswith("/") or ".." in rel.split("/")):
                    bad.append(f"{rel}: a clip path is relative to the folder")
    if "answers" in rec:
        bad += _answers_problems(rec["answers"], clip_ids)
    if "history" in rec:
        h = rec["history"]
        if not (isinstance(h, list) and all(isinstance(e, dict) and isinstance(e.get("at"), str)
                                            and isinstance(e.get("event"), str) for e in h)):
            bad.append("history must be a list of {at, event, ...}")
    return bad


def _shoot_problems(s):
    if not isinstance(s, dict) or set(s) != {"first_utc", "last_utc", "days", "cameras"}:
        return ["shoot must be {first_utc, last_utc, days, cameras}"]
    bad = [f"shoot.{k} must be YYYY-MM-DDTHH:MM:SSZ or null" for k in ("first_utc", "last_utc")
           if not (s[k] is None or (isinstance(s[k], str) and _AT.match(s[k])))]
    if not (isinstance(s["days"], list) and all(isinstance(d, str) and _DAY.match(d) for d in s["days"])):
        bad.append("shoot.days must be a list of YYYY-MM-DD")
    if not (isinstance(s["cameras"], list) and all(isinstance(c, str) for c in s["cameras"])):
        bad.append("shoot.cameras must be a list of strings")
    return bad


def _answers_problems(a, clip_ids):
    if not isinstance(a, dict):
        return ["answers must be an object"]
    sq = a.get("squeeze")
    if sq is None:
        return []
    if not isinstance(sq, dict) or not isinstance(sq.get("clips", {}), dict):
        return ["answers.squeeze must be {default, clips, by, at}"]
    bad = []
    if sq.get("default") is not None:
        bad += _factor_problems("answers.squeeze.default", sq["default"])
    for cid, f in sorted((sq.get("clips") or {}).items()):
        bad += _factor_problems(f"answers.squeeze.clips.{cid}", f)
        if clip_ids and cid not in clip_ids:
            bad.append(f"answers.squeeze names {cid}, which is not a clip of this card")
    if not isinstance(sq.get("by"), str) or not (sq.get("at") is None or isinstance(sq.get("at"), str)):
        bad.append("answers.squeeze needs by (a string) and at (a time or null)")
    return bad


def dumps(rec):
    """The record as JSON text: the seven offload.sh keys first, in its order, then the rest."""
    keys = [k for k in KEYS_V2 if k in rec] + [k for k in rec if k not in KEYS_V2]
    return json.dumps({k: rec[k] for k in keys}, indent=2, ensure_ascii=False) + "\n"


def _fold_legacy(root, clips):
    """The legacy squeeze.json beside a folder as an answers.squeeze, its file names turned
    into clip ids; None when there is none."""
    p = root + LEGACY_SQUEEZE
    if not os.path.isfile(p):
        return None
    doc = squeeze.load(p)
    by_name = {os.path.basename(c["relpath"]): c["clip_id"] for c in clips}
    return {"default": doc["default"], "clips": {by_name.get(n, n): f for n, f in doc["clips"].items()},
            "by": "person", "at": _utc(os.path.getmtime(p))}


def load(path):
    """Everything known about the card at `path` (in or beside the copy):

    root, name         the folder and its name
    record             the record as stored, or None
    version            1, 2, or None when there is no readable record
    problems           validate(record), or why there is none (S1)
    clips              the originals: the record's list, else derived from SHA256SUMS or the folder
    answers            the record's answers, with a legacy squeeze.json folded in as squeeze
    sums, offsite      the proofs (see read_sums, read_offsite)
    open               what is still open: "squeeze <clip_id>", "offsite", "project"
    """
    root = find(path)
    out = {"root": root, "name": os.path.basename(root), "record": None, "version": None,
           "problems": [], "legacy": None}
    try:
        with open(root + RECORD, encoding="utf-8") as f:
            rec = json.load(f)
        out["record"], out["version"] = rec, (2 if isinstance(rec, dict) and "schema" in rec else 1)
        out["problems"] = validate(rec)
        if not isinstance(rec, dict):
            out["record"] = rec = None
            out["version"] = None
    except FileNotFoundError:
        rec = None
        out["problems"] = ["no card.json beside the folder"]
    except (OSError, ValueError) as e:
        rec = None
        out["problems"] = [f"cannot read the record ({e})"]
    out["sums"] = read_sums(root)
    out["offsite"] = read_offsite(root, rec.get("card_id") if rec else None)
    prefix = f"{rec['cam']}-{rec['card_id'][:6]}" if rec and isinstance(rec.get("cam"), str) \
        and isinstance(rec.get("card_id"), str) else out["name"]
    stored = rec.get("clips") if rec else None
    out["clips"] = stored if isinstance(stored, list) and out["version"] == 2 \
        else derive_clips(prefix, sorted(out["sums"]["files"]) or [r for r, _ in listing(root)])
    answers = dict(rec.get("answers") or {}) if rec and isinstance(rec.get("answers"), dict) else {}
    try:
        legacy = _fold_legacy(root, out["clips"]) if "squeeze" not in answers else None
    except squeeze.SqueezeError as e:
        legacy = None
        out["problems"].append(str(e))
    if legacy:
        answers["squeeze"], out["legacy"] = legacy, root + LEGACY_SQUEEZE
    out["answers"] = answers
    out["open"] = _open(out)
    return out


def squeeze_answer(card, clip):
    """(factor, where) for one clip dict of a loaded card: "record" (answers.squeeze, which
    includes a folded legacy file), "file tag" (a BRAW file's own), or (None, None)."""
    sq = card["answers"].get("squeeze") or {}
    f = (sq.get("clips") or {}).get(clip["clip_id"], sq.get("default"))
    if f is not None:
        return float(f), "record"
    tag = squeeze.file_tag(os.path.join(card["root"], clip["relpath"]))
    return (tag, "file tag") if tag is not None else (None, None)


def _open(card):
    out = [f"squeeze {c['clip_id']}" for c in card["clips"] if squeeze_answer(card, c)[0] is None]
    if not card["offsite"]["ok"]:
        out.append("offsite")
    if not (card["record"] or {}).get("project"):
        out.append("project")
    return out


def check(card, full=True):
    """{S1..S5: [problems]} for a loaded card; an empty list is a pass."""
    rec = card["record"] or {}
    res = {"S1": list(card["problems"]),
           "S2": verify_sums(card, full),
           "S3": [o for o in card["open"] if o.startswith("squeeze ")],
           "S4": [] if card["offsite"]["ok"] else
                 ["no offsite.txt for this card (run offload.sh safe)" if not card["offsite"]["present"]
                  else "offsite.txt does not name this card, a remote and a time"],
           "S5": []}
    if isinstance(rec.get("cam"), str) and isinstance(rec.get("card_id"), str):
        want = f"{rec['cam']}-{rec['card_id'][:6]}"
        if card["name"] != want:
            res["S5"].append(f"folder is {card['name']}, the standard name is {want}")
    else:
        res["S5"].append("no record to name the folder from")
    return res


# ---- writing -----------------------------------------------------------------

def refuse_git(*paths):
    for p in paths:
        if rppaths.in_any_git_tree(p):
            raise CardError(f"{p} is inside a git working tree; card sidecars are written outside any repository")


def shoot_from(root, relpaths):
    """The shoot block from file times: first and last write, and the UTC days between.
    Cameras are left empty; reading exif is the index's job."""
    times = sorted(os.path.getmtime(os.path.join(root, r)) for r in relpaths)
    if not times:
        return {"first_utc": None, "last_utc": None, "days": [], "cameras": []}
    return {"first_utc": _utc(times[0]), "last_utc": _utc(times[-1]),
            "days": sorted({_utc(t)[:10] for t in times}), "cameras": []}


def upgrade(card):
    """A schema 2 record from a loaded card with a schema 1 record (or a v2 one unchanged).
    The seven keys and any other key keep their values."""
    rec = dict(card["record"])
    if card["version"] == 2:
        return rec
    rels = [r for r, _ in listing(card["root"])]
    rec.update(schema=dict(SCHEMA), origin="retrofit" if "note" in rec else "offload",
               shoot=shoot_from(card["root"], rels), project=None,
               answers={k: v for k, v in card["answers"].items()}, clips=card["clips"], history=[])
    return rec


def write_record(root, rec):
    """Replace <root>.card.json atomically. The only writer that touches an existing sidecar,
    and only `card set` and `card mv` call it."""
    refuse_git(root)
    with rppaths.atomic_path(root + RECORD) as tmp:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(dumps(rec))


def write_new(path, text):
    """Create a file that must not exist yet."""
    try:
        with open(path, "x", encoding="utf-8") as f:
            f.write(text)
    except FileExistsError:
        raise CardError(f"{path} exists; a sidecar is never overwritten")
