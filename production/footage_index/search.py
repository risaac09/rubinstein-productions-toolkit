"""Hybrid search: SigLIP text-to-image KNN fused (reciprocal rank) with FTS5 over captions, then filters."""
import json
import re

import numpy as np
import sqlite_vec

from . import embed

RRF_K = 60
POOL = 300


STOP = set("the and with for from into onto that this then than are was were has have its out off one two any all "
           "same shot shots clip take frame".split())


def _fts_query(q):
    # stopwords would match nearly every caption and hand rank 0 to whatever happens to be captioned
    words = [w for w in re.findall(r"[a-z0-9]+", q.lower()) if len(w) > 2 and w not in STOP]
    return " OR ".join(f'"{w}"' for w in words)


TWIN_COS = 0.97  # copies of one take (export, low-res handoff, render) embed almost identically


_prefix_cache = {}  # one entry: the last shoot's matrix


def shoot_prefix(p):
    """A shoot folder as a relpath prefix ending in '/', so 'Outings/Harbor' never takes in 'Outings/Harbor Loop'."""
    return p.strip("/") + "/"


def prefix_vectors(c, prefix):
    """(shot ids, n x DIM matrix) for every embedded shot under a relpath prefix; cached while the count holds."""
    n = c.execute("SELECT count(*) FROM shots s JOIN clips cl ON cl.id = s.clip_id JOIN vec_shots v ON v.shot_id = s.id "
                  "WHERE substr(cl.relpath, 1, ?) = ?", (len(prefix), prefix)).fetchone()[0]
    key = (c.execute("PRAGMA database_list").fetchone()[2], prefix)
    hit = _prefix_cache.get(key)
    if hit and len(hit[0]) == n:
        return hit
    rows = c.execute("SELECT v.shot_id, v.embedding FROM shots s JOIN clips cl ON cl.id = s.clip_id "
                     "JOIN vec_shots v ON v.shot_id = s.id WHERE substr(cl.relpath, 1, ?) = ?",
                     (len(prefix), prefix)).fetchall()
    if not rows:
        return [], np.zeros((0, 768), np.float32)
    _prefix_cache.clear()
    _prefix_cache[key] = out = ([r[0] for r in rows], np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows]))
    return out


def find(c, query, limit=10, client=True, fps_min=None, camera=None, project=None, faces=None,
         moving=None, max_per_clip=2, vector=None, exclude=(), prefix=None, exclude_clips=()):
    """exclude drops shot ids; exclude_clips drops clips by relpath. prefix scores every shot under that relpath
    prefix directly (one shoot), not a KNN pool of the whole index. Each result's sim is its SigLIP cosine to the
    query (None for a caption-only hit)."""
    ranks, sims = {}, {}
    filtered = (not client) or any(x is not None for x in (fps_min, camera, project, faces, moving))
    pool = POOL * 10 if filtered else POOL  # filters run after ranking, so draw a deeper pool first
    v = embed.text(query) if vector is None else vector
    if prefix is not None:
        ids, m = prefix_vectors(c, prefix)
        cos = m @ np.asarray(v, np.float32) if ids else []
        hits = sorted(zip(ids, cos), key=lambda x: -x[1])[:POOL * 10]  # deep: exclusions and per-clip caps run later
    else:
        # vec0 distance is L2; on unit vectors cosine = 1 - d^2 / 2
        hits = [(r[0], 1 - r[1] ** 2 / 2) for r in c.execute(
            "SELECT shot_id, distance FROM vec_shots WHERE embedding MATCH ? AND k = ?",
            (sqlite_vec.serialize_float32(list(v)), pool))]
    for i, (sid, sim) in enumerate(hits):
        ranks[sid] = ranks.get(sid, 0) + 1 / (RRF_K + i)
        sims[sid] = float(sim)
    fq = _fts_query(query)
    if fq:
        # captions cover every 3rd keyframe at most; weight text by that coverage so a sparse caption set can't outrank vision
        n_cap, n_shot = c.execute("SELECT (SELECT count(*) FROM captions), (SELECT count(*) FROM shots)").fetchone()
        w = min(1.0, 3 * n_cap / max(n_shot, 1))
        for i, r in enumerate(c.execute("SELECT rowid FROM fts_captions WHERE fts_captions MATCH ? ORDER BY rank LIMIT ?",
                                        (fq, pool))):
            ranks[r[0]] = ranks.get(r[0], 0) + w / (RRF_K + i)
    for x in exclude:
        ranks.pop(x, None)
    if not ranks:
        return []
    where, args = ["s.id IN (%s)" % ",".join("?" * len(ranks))], list(ranks)
    if not client:
        where.append("cl.client = 0")
    if fps_min:
        where.append("cl.fps >= ?"); args.append(fps_min)
    if camera:
        where.append("cl.camera LIKE ?"); args.append(f"%{camera}%")
    if project:
        where.append("cl.relpath LIKE ?"); args.append(f"%{project}%")
    if prefix is not None:
        where.append("substr(cl.relpath, 1, ?) = ?"); args += [len(prefix), prefix]
    if faces is not None:
        where.append("COALESCE(s.faces,0) > 0" if faces else "COALESCE(s.faces,0) = 0")
    if moving is not None:
        where.append("COALESCE(s.motion,0) >= 0.02" if moving else "COALESCE(s.motion,0) < 0.02")
    rows = c.execute(f"""SELECT s.id, s.t, s.t_in, s.t_out, s.thumb, s.motion, s.faces, s.clip_id, cl.relpath, cl.camera,
                         cl.profile, cl.fps, cl.duration, cl.client, cap.data AS caption
                         FROM shots s JOIN clips cl ON cl.id = s.clip_id LEFT JOIN captions cap ON cap.shot_id = s.id
                         WHERE {' AND '.join(where)}""", args).fetchall()
    vecs = {sid: np.frombuffer(e, dtype=np.float32) for sid, e in c.execute(
        "SELECT shot_id, embedding FROM vec_shots WHERE shot_id IN (%s)" % ",".join("?" * len(rows)), [r["id"] for r in rows])}
    out, per_clip, kept = [], {}, []
    for r in sorted(rows, key=lambda r: -ranks[r["id"]]):
        if per_clip.get(r["relpath"], 0) >= max_per_clip or r["relpath"] in exclude_clips:
            continue
        v = vecs.get(r["id"])
        if v is not None and any(float(v @ k) > TWIN_COS for k in kept):
            continue  # same picture as a better-ranked result: a copy of the same take
        if v is not None:
            kept.append(v)
        per_clip[r["relpath"]] = per_clip.get(r["relpath"], 0) + 1
        d = dict(r)
        d["score"] = round(ranks[r["id"]], 5)
        d["sim"] = round(sims[r["id"]], 4) if r["id"] in sims else None
        d["caption"] = json.loads(d["caption"]) if d["caption"] else None
        out.append(d)
        if len(out) >= limit:
            break
    return out


def similar(c, shot_id, **kw):
    row = c.execute("SELECT embedding FROM vec_shots WHERE shot_id = ?", (shot_id,)).fetchone()
    if not row:
        return []
    return find(c, "", vector=np.frombuffer(row[0], dtype=np.float32), exclude=(shot_id,), **kw)
