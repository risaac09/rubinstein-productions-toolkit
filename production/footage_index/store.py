"""sqlite store: clips, sampled shots, SigLIP vectors (sqlite-vec), captions (FTS5)."""
import json
import os
import re
import sqlite3
import subprocess

import sqlite_vec

from . import cards, config, roots

DB = config.get("db")
DIM = 768

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS clips(
  id INTEGER PRIMARY KEY, relpath TEXT UNIQUE NOT NULL, key TEXT NOT NULL, clip_key TEXT,
  bytes INTEGER, mtime INTEGER, client INTEGER NOT NULL DEFAULT 0, project TEXT,
  camera TEXT, profile TEXT, color_space TEXT, fps REAL, width INTEGER, height INTEGER,
  duration REAL, codec TEXT, status TEXT NOT NULL DEFAULT 'new', error TEXT, indexed_at TEXT);
CREATE TABLE IF NOT EXISTS shots(
  id INTEGER PRIMARY KEY, clip_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
  t REAL NOT NULL, t_in REAL NOT NULL, t_out REAL NOT NULL, thumb TEXT NOT NULL,
  motion REAL, faces INTEGER, face_max REAL, luma REAL, UNIQUE(clip_id, t));
CREATE VIRTUAL TABLE IF NOT EXISTS vec_shots USING vec0(shot_id INTEGER PRIMARY KEY, embedding float[{DIM}]);
CREATE TABLE IF NOT EXISTS captions(shot_id INTEGER PRIMARY KEY, data TEXT NOT NULL, model TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS fts_captions USING fts5(text, tags);
"""


# The toolkit's rpresolve.sidecar.card_key rule, copied (a test holds the two equal): <card folder>/<stem>, the card
# folder being the nearest folder named <CAM>-<id6>. Anything else gets a content key: bytes and lowercase file name.
CARD_DIR = re.compile(r"^[A-Za-z0-9]+-[0-9a-f]{6}$")


def split_rel(relpath):
    """The path inside its root, from a stored relpath ('Extra:shoots/x' -> 'shoots/x')."""
    return roots.split(relpath)[1]


def clip_key(relpath, bytes_, abspath=None):
    """Stable across a move: the card record's clip id when a record sits beside the clip's card folder (abspath given), else
    the card id from a <CAM>-<id6> folder name, else bytes and lowercase name."""
    cid = cards.clip_id(abspath) if abspath else None
    if cid:
        return f"card:{cid}"
    parts = os.path.normpath(split_rel(relpath)).split(os.sep)
    for i in range(len(parts) - 2, -1, -1):
        if CARD_DIR.match(parts[i]):
            return f"card:{parts[i]}/{os.path.splitext(parts[-1])[0]}"
    return f"file:{bytes_}:{parts[-1].lower()}"


def placement(relpath):
    """(client flag, project) from where a clip sits: restricted when under the restricted_prefix setting (never, when it is
    empty); the project is the second path component, else the whole path."""
    prefix = config.get("restricted_prefix")
    return int(bool(prefix) and split_rel(relpath).startswith(prefix)), relpath.split("/")[1] if "/" in relpath else relpath


def needs_migration(c):
    return "clip_key" not in {r[1] for r in c.execute("PRAGMA table_info(clips)")}


def migrate(c):
    """Add clips.clip_key and fill it from relpath and bytes (no disk read). Idempotent; touches no shots, vectors or captions."""
    if needs_migration(c):
        c.execute("ALTER TABLE clips ADD COLUMN clip_key TEXT")
    for r in c.execute("SELECT id, relpath, bytes FROM clips WHERE clip_key IS NULL").fetchall():
        c.execute("UPDATE clips SET clip_key=? WHERE id=?", (clip_key(r["relpath"], r["bytes"]), r["id"]))
    c.execute("CREATE INDEX IF NOT EXISTS clips_clip_key ON clips(clip_key)")
    c.commit()


def rekey_plan(c, abspath_of):
    """[(clip id, relpath, old clip_key, new clip_key)] for every clip whose key would change now. abspath_of(relpath) is the
    file's path on this machine, or None when its root is not mounted; a clip whose file is gone keeps its key."""
    out = []
    for r in c.execute("SELECT id, relpath, bytes, clip_key FROM clips ORDER BY id").fetchall():
        p = abspath_of(r["relpath"])
        if p is None or not os.path.exists(p):
            continue
        new = clip_key(r["relpath"], r["bytes"], p)
        if new != r["clip_key"]:
            out.append((r["id"], r["relpath"], r["clip_key"], new))
    return out


def duration_of(path):
    """Seconds by ffprobe, or None when the file cannot be read."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                             capture_output=True, text=True, timeout=60).stdout.strip()
        return float(out)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def repoint(c, present, found):
    """A clip that moved keeps its row, shots, vectors and captions. present: every relpath seen this run; found:
    [(relpath, bytes, mtime, abspath or None)] for the same. A stored row whose path is gone and whose clip_key matches
    exactly one new path is re-pointed; several on either side are left to prune and a fresh index. When both sides know a
    duration they must agree to 0.1 s. Returns the number moved."""
    db = {r["relpath"] for r in c.execute("SELECT relpath FROM clips")}
    gone, new = {}, {}
    for r in c.execute("SELECT id, relpath, clip_key, duration FROM clips"):
        if r["relpath"] not in present:
            gone.setdefault(r["clip_key"], []).append(r)
    for rel, size, mtime, abspath in found:
        if rel not in db:
            new.setdefault(clip_key(rel, size, abspath), []).append((rel, size, mtime, abspath))
    moved = 0
    for k, olds in gone.items():
        if k is None or len(olds) != 1 or len(new.get(k, ())) != 1:
            continue
        rel, size, mtime, abspath = new[k][0]
        d = duration_of(abspath) if abspath and olds[0]["duration"] else None
        if d is not None and abs(d - olds[0]["duration"]) > 0.1:
            continue
        c.execute("UPDATE clips SET relpath=?, bytes=?, mtime=?, key=?, clip_key=?, client=?, project=? WHERE id=?",
                  (rel, size, mtime, f"{size}:{mtime}", k, *placement(rel), olds[0]["id"]))
        moved += 1
    return moved


def connect(path=DB, readonly=False):
    """readonly opens the file as it is: no schema, no WAL pragma, nothing written (safe beside a running indexer)."""
    if readonly:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
    else:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        c = sqlite3.connect(path, timeout=60)
    c.enable_load_extension(True)
    sqlite_vec.load(c)
    c.enable_load_extension(False)
    c.row_factory = sqlite3.Row
    if readonly:
        return c
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript(SCHEMA)
    return c


def upsert_clip(c, relpath, key, bytes_, mtime, abspath=None, **meta):
    """Insert or refresh a clip row. A changed key resets it to 'new' and drops its shots."""
    row = c.execute("SELECT id, key FROM clips WHERE relpath=?", (relpath,)).fetchone()
    if row and row["key"] == key:
        return row["id"], False
    if row:
        drop_shots(c, row["id"])
        c.execute("UPDATE clips SET key=?, clip_key=?, bytes=?, mtime=?, status='new', error=NULL WHERE id=?",
                  (key, clip_key(relpath, bytes_, abspath), bytes_, mtime, row["id"]))
        cid = row["id"]
    else:
        cid = c.execute("INSERT INTO clips(relpath, key, clip_key, bytes, mtime) VALUES (?,?,?,?,?)",
                        (relpath, key, clip_key(relpath, bytes_, abspath), bytes_, mtime)).lastrowid
    if meta:
        set_clip(c, cid, **meta)
    return cid, True


def set_clip(c, cid, **meta):
    cols = ", ".join(f"{k}=?" for k in meta)
    c.execute(f"UPDATE clips SET {cols} WHERE id=?", (*meta.values(), cid))


def drop_shots(c, cid):
    rows = c.execute("SELECT id, thumb FROM shots WHERE clip_id=?", (cid,)).fetchall()
    for sid, thumb in rows:
        if thumb and os.path.exists(thumb):
            os.remove(thumb)
        c.execute("DELETE FROM vec_shots WHERE shot_id=?", (sid,))
        c.execute("DELETE FROM captions WHERE shot_id=?", (sid,))
        c.execute("DELETE FROM fts_captions WHERE rowid=?", (sid,))
    c.execute("DELETE FROM shots WHERE clip_id=?", (cid,))


def prune(c, keep_relpaths):
    """Drop clips no longer under a root (moved or deleted), with their shots and thumbnails."""
    gone = [r for r in c.execute("SELECT id, relpath FROM clips") if r["relpath"] not in keep_relpaths]
    for r in gone:
        drop_shots(c, r["id"])
        c.execute("DELETE FROM clips WHERE id=?", (r["id"],))
    return len(gone)


def add_shot(c, cid, t, t_in, t_out, thumb, motion=None, faces=None, face_max=None, luma=None):
    return c.execute("INSERT OR REPLACE INTO shots(clip_id,t,t_in,t_out,thumb,motion,faces,face_max,luma)"
                     " VALUES (?,?,?,?,?,?,?,?,?)",
                     (cid, t, t_in, t_out, thumb, motion, faces, face_max, luma)).lastrowid


def set_vector(c, sid, vec):
    c.execute("DELETE FROM vec_shots WHERE shot_id=?", (sid,))
    c.execute("INSERT INTO vec_shots(shot_id, embedding) VALUES (?, ?)", (sid, sqlite_vec.serialize_float32(list(vec))))


FTS_FIELDS = ("subject", "setting", "action", "shot_size", "time_of_day", "light")  # lens_feel, camera_move: near-constant
FILLER = ("unknown", "cannot determine", "not specified", "unspecified", "n/a")


def clean_tags(tags):
    """Lowercase, drop blanks, dedupe in order, cap at 12; deduping first so a looped tag can't fill the cap."""
    if isinstance(tags, str):
        tags = tags.split(",")
    return list(dict.fromkeys(s for s in (str(t).strip().lower() for t in tags or []) if s))[:12]


def set_caption(c, sid, data, model):
    data = {**data, "tags": clean_tags(data.get("tags"))}
    c.execute("INSERT OR REPLACE INTO captions(shot_id, data, model) VALUES (?,?,?)", (sid, json.dumps(data), model))
    vals = (str(data.get(k) or "").strip() for k in FTS_FIELDS)
    text = " ".join(v for v in vals if v and not any(f in v.lower() for f in FILLER))
    c.execute("DELETE FROM fts_captions WHERE rowid=?", (sid,))
    c.execute("INSERT INTO fts_captions(rowid, text, tags) VALUES (?,?,?)", (sid, text, " ".join(data.get("tags") or [])))


def stats(c):
    q = lambda s: c.execute(s).fetchone()[0]
    return {"clips": q("SELECT count(*) FROM clips"),
            "clips_done": q("SELECT count(*) FROM clips WHERE status='done'"),
            "clips_error": q("SELECT count(*) FROM clips WHERE status='error'"),
            "clips_undecodable": q("SELECT count(*) FROM clips WHERE status='undecodable'"),
            "shots": q("SELECT count(*) FROM shots"),
            "vectors": q("SELECT count(*) FROM vec_shots"),
            "captions": q("SELECT count(*) FROM captions")}
