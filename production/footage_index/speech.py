"""Speech in the index: the screened transcripts `vertcut transcribe` writes, searchable by what was said.

A transcript folder mirrors the clips' relpaths under one extra top folder (the pool): <dir>/<pool>/<clip dir>/<stem>.json,
with <stem>.kinds.json beside it. Only the screened .json is read, never a .raw.json, and a segment the screen withheld
(singing, music, a hallucination: its text is a bracketed label) is not stored. Re-reading a clip replaces its rows."""
import json
import os
import re

from . import store

LABELS = frozenset(("[singing]", "[music]", "[inaudible]"))  # what the screen writes in place of withheld text


def ingest(c, folder):
    """Store every screened transcript under folder whose clip is in the index. Returns (clips stored, segments stored,
    transcripts with no clip in the index)."""
    by_stem, dup = {}, set()
    for r in c.execute("SELECT id, relpath FROM clips"):
        k = os.path.splitext(r["relpath"])[0]
        dup.add(k) if k in by_stem else by_stem.setdefault(k, r["id"])
    for k in dup:  # two clips share a stem: a transcript cannot say which, so it counts as unmatched
        del by_stem[k]
    clips = segs = orphans = 0
    for dirpath, _, names in os.walk(folder):
        for n in sorted(names):
            if not n.endswith(".json") or n.endswith((".kinds.json", ".raw.json")):
                continue
            rel = os.path.relpath(os.path.join(dirpath, n[:-5]), folder).split(os.sep, 1)
            cid = by_stem.get(rel[1]) if len(rel) == 2 else None
            if cid is None:
                orphans += 1
                continue
            with open(os.path.join(dirpath, n)) as f:
                rows = [(s["start"], s["end"], s["text"].strip()) for s in json.load(f).get("segments", [])
                        if not s.get("redacted") and s.get("text", "").strip() and s["text"].strip() not in LABELS]
            store.drop_speech(c, cid)
            for t_in, t_out, text in rows:
                sid = c.execute("INSERT INTO speech(clip_id, t_in, t_out, text) VALUES (?,?,?,?)", (cid, t_in, t_out, text)).lastrowid
                c.execute("INSERT INTO fts_speech(rowid, text) VALUES (?,?)", (sid, text))
            clips, segs = clips + 1, segs + len(rows)
    return clips, segs, orphans


def say(c, query, limit=10, client=True):
    """Segments whose words match every word of the query, best first: relpath, in, out and the words.
    client=False leaves out clips flagged as client footage, as find does."""
    fq = " ".join(f'"{w}"' for w in re.findall(r"[^\W_]+", query))
    if not fq:
        return []
    return [dict(r) for r in c.execute(
        "SELECT cl.relpath, s.t_in, s.t_out, s.text FROM fts_speech f JOIN speech s ON s.id = f.rowid "
        "JOIN clips cl ON cl.id = s.clip_id WHERE fts_speech MATCH ? AND (? OR cl.client = 0) ORDER BY rank LIMIT ?", (fq, client, limit))]
