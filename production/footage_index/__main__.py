"""footage_index: index a video archive and search it by description.

  python -m footage_index [--root DIR] index [--limit N] [--only PREFIX] [--lanes 6] [--retry-errors] [--card PATH]
  python -m footage_index rekey   [--apply]        # clip keys from the card records; dry run unless --apply
  python -m footage_index cameras                  # JSON: camera strings per card folder, writes nothing
  python -m footage_index embed   [--batch 32]
  python -m footage_index caption [--limit N]
  python -m footage_index recaption-fts   # re-store captions (tag dedupe, FTS fields); no model
  python -m footage_index find "slow push-in on hands chopping herbs" [--no-client] [--fps-min 50]
                                  [--camera A7] [--project Outings] [--faces|--no-faces] [--limit 10] [--sheet DIR]
  python -m footage_index stats
  python -m footage_index shoot   Shoots/harvest-day [--refresh]
  python -m footage_index methods Shoots/harvest-day [--sheet DIR]
  python -m footage_index plan    punch-montage Shoots/harvest-day [--music PATH] [--swap SLOT [LETTER]]
  python -m footage_index plan    --variants 6 Shoots/harvest-day [--music PATH]
  python -m footage_index plan    punch-montage Shoots/river-walk --variants 4   # same method, different fills
  python -m footage_index plan    ... [--trim motion|centre] [--beats beats.json | --notes notes.json] [--state NAME] [--no-gate]
  python -m footage_index plan    METHOD PREFIX --variants 3 --avoid DIR   # DIR: another method's variants folder
  python -m footage_index gate    Shoots/river-walk [--json] [--sheet DIR] [--state NAME]
  python -m footage_index check   Shoots/river-walk PLAN_DIR [PLAN_DIR ...]   # exit 1 if a plan uses an excluded take or shot
  python -m footage_index check   Shoots/river-walk --shotlist FILE   # a hand-written shot list: exclude hits exit 1, gate flags warn
  python -m footage_index sheet   --prefix Shoots/river-walk --out DIR [--skip SUBSTR ...] [--per-clip 6] [--cols 8] [--rows 6] [--exclude FILE]   # default: the shoot's own exclude list
  python -m footage_index slots-sheet METHOD Shoots/river-walk [--state NAME]   # a lettered sheet per slot (10 slots or fewer)
  python -m footage_index compose     METHOD Shoots/river-walk PICKS.json       # {"1": "B", "2": "A"}: slot to letter
  python -m footage_index slots-sheet|compose|plan --swap ... [--variant v2 | --plan-dir PATH]   # act on a variants plan
"""
import argparse
import datetime
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config, roots as roots_mod, store

VIDEO = (".mov", ".mp4", ".m4v", ".mts", ".mxf", ".braw", ".avi", ".insv", ".mkv")
SKIP = tuple(config.get("skip_prefixes"))
STAGING = config.get("staging_prefix")
MIN_BYTES = 5_000_000
BLACK_LUMA = 0.03  # mean luma under this: no caption (the model gave black frames a shot size and mood tags)
LOG = config.get("log_file")


def log(msg):
    line = f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def _catalog_rows(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 3:
                yield p[0], int(p[1] or 0), int(p[2] or 0), p[3] if len(p) > 3 else ""


def _live_rows(root):
    """A root with no catalog: walk its mount (read-only), skipping the folders the catalog skips and the root's own."""
    mount = root.mount
    for d, dirs, files in os.walk(mount):
        rel_d = os.path.relpath(d, mount) + "/"
        rel_d = "" if rel_d == "./" else rel_d
        dirs[:] = [x for x in dirs if not (rel_d + x + "/").startswith(SKIP) and x != "#recycle"
                   and not (root.skip_dirs and re.search(root.skip_dirs, x, re.I))
                   and (not root.include or any((rel_d + x + "/").startswith(i) or i.startswith(rel_d + x + "/") for i in root.include))]
        for n in files:
            try:
                st = os.stat(os.path.join(d, n))
            except OSError:
                continue
            yield os.path.relpath(os.path.join(d, n), mount), st.st_size, int(st.st_mtime), ""


def candidates(only=None, root=None, rows=None):
    given = rows
    """Video rows of one root (paths inside it), deduped by (bytes, file name), non-staging copies first. root None is the
    first root; a root with no catalog is walked live."""
    rows = []
    if given is None:
        root = root or roots_mod.load()[0]
    src = given if given is not None else _catalog_rows(root.catalog) if root.catalog else _live_rows(root)
    for rel, size, mtime, sha in src:
        if not rel.lower().endswith(VIDEO) or rel.startswith(SKIP) or os.path.basename(rel).startswith("._"):
            continue
        if size < MIN_BYTES or (only and not rel.startswith(only)) or (root and not roots_mod.wanted(root, rel)):
            continue
        rows.append((rel, size, mtime, sha))
    # a "low res/" handoff beside its full-res original is the same take: index the original only
    full = {(os.path.dirname(r[0]), os.path.splitext(os.path.basename(r[0]))[0].lower()) for r in rows if "/low res/" not in r[0]}
    rows = [r for r in rows if "/low res/" not in r[0] or
            (os.path.dirname(r[0]).replace("/low res", ""), os.path.splitext(os.path.basename(r[0]))[0].lower()) not in full]
    rows.sort(key=lambda r: (bool(STAGING) and r[0].startswith(STAGING), r[0]))
    seen, out = set(), []
    for r in rows:
        k = (r[1], os.path.basename(r[0]).lower())
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def cmd_index(a):
    rts = roots_mod.load()
    if a.card:
        return _index_card(a, rts)
    for r in rts:
        if not r.mount:
            sys.exit(roots_mod.NO_ROOT)
        if not os.path.isdir(r.mount) or not os.listdir(r.mount):
            sys.exit(f"{r.name}: {r.mount} is missing or empty (is the volume mounted?)")
        req = config.get("required_folder")
        if req and r is rts[0] and not os.path.isdir(os.path.join(r.mount, req)):
            sys.exit(f"{r.name}: {r.mount} has no {req!r} folder; refusing to index (wrong mount?)")
    c = store.connect()
    if store.needs_migration(c):
        sys.exit("footage.db predates clip_key: run `python -m footage_index migrate` first")
    # a full walk of every root; --only narrows what is sampled, never what counts as present
    allrows, where = [], {}
    for r in rts:
        found = candidates(root=r)
        if not found and not r.catalog:
            sys.exit(f"{r.name}: nothing under {r.mount}; refusing to prune a live-walked root against an empty walk")
        for rel, size, mtime, sha in found:
            q = roots_mod.qualify(r, rel, rts)
            allrows.append((q, size, mtime, sha))
            where[q] = (r, rel)
    present = {q for q, *_ in allrows}
    if a.retry_errors:  # a decode fix landed: give every error and undecodable clip another go
        n = c.execute("UPDATE clips SET status='new', error=NULL WHERE status IN ('error','undecodable')").rowcount
        c.commit()
        log(f"index: {n} error clip(s) reset for retry")
    log(f"index: re-pointed {store.repoint(c, present, [(q, s_, m, os.path.join(where[q][0].mount, where[q][1])) for q, s_, m, _ in allrows])} moved clip(s)")
    c.commit()
    todo = []
    for rel, size, mtime, _sha in allrows:
        if a.only and not rel.startswith(a.only):
            continue
        # size:mtime, not sha: a catalog may fill sha in over time and a key change would redo the clip
        cid, _changed = store.upsert_clip(c, rel, f"{size}:{mtime}", size, mtime, os.path.join(where[rel][0].mount, where[rel][1]),
                                          **dict(zip(("client", "project"), store.placement(rel))))
        if c.execute("SELECT status FROM clips WHERE id=?", (cid,)).fetchone()[0] not in ("done", "undecodable"):
            todo.append((cid, rel))
    if not a.only:
        # a row the candidate rules no longer pick (a low res twin, a skipped folder) but whose file is still there keeps its
        # captions: only a file that is gone is pruned (a run against a half-mounted volume would otherwise drop them)
        held = {r["relpath"] for r in c.execute("SELECT relpath FROM clips") if r["relpath"] not in present
                and any(os.path.exists(p) for p in roots_mod.local_paths(r["relpath"], rts))}
        log(f"index: pruned {store.prune(c, present | held)} clip(s) no longer under a root; {len(held)} kept (file still there, not a candidate)")
    c.commit()
    _sample(c, todo, where, a)


def _ollama_up():
    import urllib.request
    try:
        urllib.request.urlopen(config.get("ollama_url") + "/api/tags", timeout=3).read()
        return True
    except OSError:
        return False


def _index_card(a, rts):
    """index --card PATH: index, embed and caption one card folder under any root. No prune and no re-point: a card is
    added to, never subtracted from. Captions only when Ollama answers; the card is indexed and embedded either way."""
    path = os.path.abspath(os.path.expanduser(a.card))
    hit = next(((r, base) for r in rts for base in (r.mount, r.volume) if path == base or path.startswith(base + "/")), None)
    if not hit or not os.path.isdir(path):
        sys.exit(f"{a.card}: not a folder under a configured root ({', '.join(f'{r.name} {r.mount}' for r in rts)})")
    root, base = hit
    inner = os.path.relpath(path, base)
    prefix = "" if inner == "." else inner + "/"
    walked = [(prefix + rel, size, mtime, "") for rel, size, mtime, _ in _live_rows(root._replace(mount=os.path.join(root.mount, inner), include=(), skip_dirs=None))]
    c = store.connect()
    if store.needs_migration(c):
        sys.exit("footage.db predates clip_key: run `python -m footage_index migrate` first")
    where, todo = {}, []
    for rel, size, mtime, _sha in candidates(rows=walked):
        q = roots_mod.qualify(root, rel, rts)
        where[q] = (root, rel)
        cid, _ = store.upsert_clip(c, q, f"{size}:{mtime}", size, mtime, os.path.join(root.mount, rel),
                                   **dict(zip(("client", "project"), store.placement(q))))
        if c.execute("SELECT status FROM clips WHERE id=?", (cid,)).fetchone()[0] not in ("done", "undecodable"):
            todo.append((cid, q))
    c.commit()
    log(f"index --card: {len(where)} clip(s) under {path}, {len(todo)} new")
    _sample(c, todo, where, a)
    qprefix = roots_mod.qualify(root, prefix, rts)
    cmd_embed(argparse.Namespace(batch=32, prefix=qprefix))
    if _ollama_up():
        cmd_caption(argparse.Namespace(limit=None, every=3, prefix=qprefix))
    else:
        log("index --card: captions skipped, Ollama is not answering on 127.0.0.1:11434; the card is indexed and embedded")


def _sample(c, todo, where, a):
    from . import sample
    todo = todo[: a.limit] if a.limit else todo
    log(f"index: {len(todo)} clip(s) to sample, {a.lanes} lanes")
    t0, done, errs = time.time(), 0, 0
    with ThreadPoolExecutor(a.lanes) as pool:
        futs = {pool.submit(sample.process, where[rel][0].mount, where[rel][1], cid): (cid, rel) for cid, rel in todo}
        for fut in as_completed(futs):
            cid, rel = futs[fut]
            try:
                meta, shots = fut.result()
                gone = meta.pop("undecodable", None)
                store.drop_shots(c, cid)
                for s in shots:
                    store.add_shot(c, cid, s["t"], s["t_in"], s["t_out"], s["thumb"], s["motion"], s["faces"],
                                   s["face_max"], s["luma"])
                store.set_clip(c, cid, status="done" if shots else "undecodable", error=None if shots else gone,
                               indexed_at=datetime.datetime.now().isoformat(timespec="seconds"), **meta)
                done += 1
            except Exception as e:  # one bad file never stops the run
                store.set_clip(c, cid, status="error", error=f"{type(e).__name__}: {e}"[:500])
                errs += 1
            c.commit()
            if (done + errs) % 25 == 0:
                log(f"index: {done + errs}/{len(todo)} ({errs} errors), {time.time() - t0:.0f}s")
    log(f"index: finished {done} ok, {errs} errors in {time.time() - t0:.0f}s; {store.stats(c)}")


def _restore(bak, path):
    import shutil
    for ext in ("-wal", "-shm"):  # a stale WAL beside the restored file would be replayed onto it
        if os.path.exists(path + ext):
            os.remove(path + ext)
    shutil.copyfile(bak, path)


def cmd_migrate(a):
    """Add clip_key to an existing footage.db. Backs the file up first and restores it if the row counts move."""
    path = store.DB
    bak = f"{path}.bak-{a.date}"
    if os.path.exists(bak):
        sys.exit(f"{bak} exists; move it aside first")
    count = lambda c: {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("clips", "shots", "captions")}
    c = store.connect(readonly=True)
    before = count(c)
    c.close()
    src = sqlite3.connect(path)  # the online backup API, so a WAL file beside the db is carried into the copy
    dst = sqlite3.connect(bak)
    src.backup(dst)
    dst.close(); src.close()
    print(f"backup {bak}\nbefore {before}")
    c = store.connect()
    try:
        store.migrate(c)
        after = count(c)
    except Exception:
        c.close()
        _restore(bak, path)
        raise
    c.close()
    print(f"after  {after}")
    if after != before:
        _restore(bak, path)
        sys.exit(f"counts moved, restored {bak}")
    print("migrate: counts equal")


def _abspath_of(relpath):
    r, rel = roots_mod.split(relpath)
    return os.path.join(r.mount, rel) if os.path.isdir(r.mount) and os.listdir(r.mount) else None


def cmd_rekey(a):
    """Re-derive every clip's clip_key from the card records now beside the footage folders. Dry run unless --apply: it prints
    the counts and examples. --apply backs the db up first, changes only clips.clip_key, and restores the copy if the row
    counts of clips, shots, vectors or captions move."""
    c = store.connect(readonly=not a.apply)
    if store.needs_migration(c):
        sys.exit("footage.db predates clip_key: run `python -m footage_index migrate` first")
    plan = store.rekey_plan(c, _abspath_of)
    total = c.execute("SELECT count(*) FROM clips").fetchone()[0]
    form = lambda k: (k or "none").split(":", 1)[0]
    moves = {}
    for _id, _rel, old, new in plan:
        moves[f"{form(old)} -> {form(new)}"] = moves.get(f"{form(old)} -> {form(new)}", 0) + 1
    print(json.dumps({"clips": total, "would_change": len(plan), "unchanged_or_unreadable": total - len(plan), "by_form": moves,
                      "examples": [{"relpath": r, "old": o, "new": n} for _i, r, o, n in plan[:10]],
                      "applied": False if not a.apply else None}, indent=1))
    if not a.apply:
        print("dry run: nothing written; --apply to write")
        return
    bak = f"{store.DB}.bak-rekey-{a.date}"
    if os.path.exists(bak):
        sys.exit(f"{bak} exists; move it aside first")
    count = lambda: {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("clips", "shots", "vec_shots", "captions")}
    before = count()
    dst = sqlite3.connect(bak)
    sqlite3.connect(store.DB).backup(dst)
    dst.close()
    print(f"backup {bak}\nbefore {before}")
    try:
        c.executemany("UPDATE clips SET clip_key=? WHERE id=?", [(new, i) for i, _r, _o, new in plan])
        c.commit()
        after = count()
    except Exception:
        c.close()
        _restore(bak, store.DB)
        raise
    print(f"after  {after}")
    if after != before:
        c.close()
        _restore(bak, store.DB)
        sys.exit(f"counts moved, restored {bak}")
    print(f"rekey: {len(plan)} clip key(s) updated, counts equal")


def cmd_cameras(a):
    """JSON: per card folder, the distinct camera strings (exif Make and Model, as detect read them) the index holds for its
    clips. Writes nothing. A card folder is the one with a record beside it, else a <CAM>-<id6> folder name."""
    from . import cards
    c = store.connect(readonly=True)
    out, loose = {}, 0
    for r in c.execute("SELECT relpath, camera FROM clips WHERE camera IS NOT NULL AND camera != ''"):
        p = _abspath_of(r["relpath"])
        folder = cards.folder_of(p) if p else None
        if folder:
            root, rel = roots_mod.split(r["relpath"])
            name = roots_mod.qualify(root, os.path.relpath(folder, root.mount))
        else:
            parts = store.split_rel(r["relpath"]).split("/")
            i = next((i for i in range(len(parts) - 2, -1, -1) if store.CARD_DIR.match(parts[i])), None)
            if i is None:
                loose += 1
                continue
            name = "/".join(parts[:i + 1])
        out.setdefault(name, set()).add(r["camera"])
    print(json.dumps({"cards": {k: sorted(v) for k, v in sorted(out.items())}, "clips_not_in_a_card": loose}, indent=1))


def cmd_embed(a):
    from . import embed
    c = store.connect()
    pre = getattr(a, "prefix", None)
    rows = c.execute("SELECT id, thumb FROM shots WHERE id NOT IN (SELECT shot_id FROM vec_shots)"
                     + (" AND clip_id IN (SELECT id FROM clips WHERE substr(relpath, 1, ?) = ?)" if pre else ""),
                     (len(pre), pre) if pre else ()).fetchall()
    log(f"embed: {len(rows)} shot(s)")
    t0 = time.time()
    for i in range(0, len(rows), a.batch):
        chunk = [r for r in rows[i:i + a.batch] if os.path.exists(r["thumb"])]
        if not chunk:
            continue
        for r, v in zip(chunk, embed.images([r["thumb"] for r in chunk])):
            store.set_vector(c, r["id"], v)
        c.commit()
    log(f"embed: done in {time.time() - t0:.0f}s; {store.stats(c)}")


def cmd_caption(a):
    from . import caption
    c = store.connect()
    # every 3rd keyframe of a clip (about one per 12 s): Qwen-VL runs ~3-4 s a shot here, SigLIP covers the rest
    pre = getattr(a, "prefix", None)
    q = ("SELECT id, thumb FROM (SELECT *, row_number() OVER (PARTITION BY clip_id ORDER BY t) - 1 AS n FROM shots)"
         f" WHERE n % {int(a.every)} = 0 AND id NOT IN (SELECT shot_id FROM captions)"
         f" AND (luma IS NULL OR luma >= {BLACK_LUMA})"  # a black frame gets no caption and no model call
         + (" AND clip_id IN (SELECT id FROM clips WHERE substr(relpath, 1, ?) = ?)" if pre else "") + " ORDER BY clip_id, t")
    rows = c.execute(q + (f" LIMIT {int(a.limit)}" if a.limit else ""), (len(pre), pre) if pre else ()).fetchall()
    log(f"caption: {len(rows)} shot(s) with {caption.MODEL}")
    t0, errs = time.time(), 0
    for n, r in enumerate(rows, 1):
        try:
            store.set_caption(c, r["id"], caption.caption(r["thumb"]), caption.MODEL)
        except Exception as e:
            errs += 1
            log(f"caption: shot {r['id']} failed: {type(e).__name__}: {e}"[:300])
        if n % 50 == 0:
            c.commit()
            log(f"caption: {n}/{len(rows)}, {(time.time() - t0) / n:.2f}s/shot")
    c.commit()
    log(f"caption: done, {errs} errors; {store.stats(c)}")


def cmd_recaption_fts(a):
    """Re-store every caption through set_caption (tag dedupe, current FTS fields); no model call."""
    c = store.connect()
    rows = c.execute("SELECT shot_id, data, model FROM captions").fetchall()
    for r in rows:
        store.set_caption(c, r["shot_id"], json.loads(r["data"]), r["model"])
    c.commit()
    log(f"recaption-fts: {len(rows)} caption(s) re-stored; {store.stats(c)}")


def cmd_find(a):
    from . import search
    c = store.connect()
    res = search.find(c, a.query, limit=a.limit, client=a.client, fps_min=a.fps_min, camera=a.camera,
                      project=a.project, faces=a.faces, moving=a.moving)
    for i, r in enumerate(res, 1):
        cap = r["caption"] or {}
        print(f"{i:2d}. {r['relpath']}  @{r['t_in']:.1f}-{r['t_out']:.1f}s  {r['camera'] or '?'} {r['profile'] or ''} "
              f"{(r['fps'] or 0):.0f}fps  score {r['score']}\n    {cap.get('subject', '')} | {cap.get('action', '')} | "
              f"{cap.get('shot_size', '')} {cap.get('camera_move', '')}\n    thumb {r['thumb']}")
    if a.json:
        print(json.dumps(res, indent=1, default=str))
    if a.sheet and res:
        subprocess.run([config.get("tools_python"), "-m", "rpstills", "absheet",
                        *[r["thumb"] for r in res[:9]], "--out", a.sheet, "--seed", str(int(time.time()) % 10000)],
                       cwd=config.PRODUCTION, check=True)
        with open(os.path.join(a.sheet, "results.json"), "w") as f:
            json.dump(res, f, indent=1, default=str)
        print(f"sheet: {a.sheet}/sheet.jpg (key sealed beside it)")


def cmd_shoot(a):
    from . import shoot
    p = shoot.profile(store.connect(), a.prefix, refresh=a.refresh)
    print(f"{p['prefix']}: {p['clips']} clips, {p['shots']} shots, {p['vectors']} vectors, {p['captions']} captions; "
          f"fps {p['fps_mix']}, moving {p['moving_share']:.0%}, faces {p['faces_share']:.0%}, dark {p['dark_share']:.0%}")
    for t, v in p["tags"].items():
        print(f"  {t:9s} {v['strength']:.2f}  {'; '.join(v['evidence'][:2])}")
    if a.json:
        print(json.dumps(p, indent=1))


def cmd_methods(a):
    from . import edit
    res = edit.rank(store.connect(), a.prefix)
    if not res["methods"]:
        sys.exit(f"no methods in {edit.METHODS}")
    for i, m in enumerate(res["methods"], 1):
        print(f"{i}. {m['id']}  {m['name']}  score {m['score']} (tags {m['tag_score']}, slots {m['slot_cover']:.0%})\n"
              f"   {m['summary']}")
        for t in m["thumbs"]:
            print(f"   slot {t['slot']} {t['role']}: sim {t['sim']}  {t['thumb']}")
    if a.sheet:
        letters = edit.method_sheet(res["methods"], a.sheet)
        print(f"sheet: {a.sheet}/sheet.jpg  " + ", ".join(f"{k}={v}" for k, v in sorted(letters.items())))


def cmd_gate(a):
    from . import edit
    g = edit.build_gate(store.connect(readonly=True), a.prefix, a.state)
    s = g.summary()
    print(f"{s['prefix']}: {s['shots']} shots, {s['unique']} after one colour state and one copy per take, {s['passes']} pass (colour state {s['state']})")
    for why, n in s["rejects"].items():
        print(f"  {why:12s} {n}")
    for k, v in s["states"].items():
        print(f"  state {k}: {v['shots']} shots, {v['unique']} unique" + (f", {v['gated']} pass" if v["gated"] is not None else ""))
    if a.sheet:
        print("sheet:", g.sheet(os.path.join(a.sheet, "gate.jpg")))
    if a.json:
        print(json.dumps(s, indent=1))


def cmd_check(a):
    from . import edit
    if not a.plans and not a.shotlist:
        raise ValueError("give a PLAN_DIR, a --shotlist FILE, or both")
    c, bad, notes = store.connect(readonly=True), [], []
    if a.shotlist:
        bad, notes = edit.check_shotlist(c, a.prefix, a.shotlist)
        print("\n".join(f"warning: {w}" for w in notes))
        if bad:
            sys.exit("\n".join(bad) + f"\n{len(bad)} shot(s) use footage the exclude list rules out")
        print(f"{a.shotlist}: no excluded shots, {len(notes)} warning(s)")
    bad = edit.check(c, a.prefix, a.plans) if a.plans else []
    if bad:
        skipped = sum(b.endswith("not checked") for b in bad)  # a plan that could not be read is a line, not a span
        music = sum(": music " in b and not b.endswith("not checked") for b in bad)  # the music file: missing, unreadable or excluded
        sys.exit("\n".join(bad) + f"\n{len(bad) - skipped - music} span(s) use footage the exclude list rules out or open a window the previous shot cannot play under; "
                 f"{music} music file problem(s); {skipped} plan(s) not checked")
    if a.plans:
        print(f"{len(a.plans)} plan(s) clean against {edit.exclude_path(a.prefix)}")


def cmd_sheet(a):
    from . import sheet
    r = sheet.make(store.connect(readonly=True), a.prefix, a.out, skip=a.skip, per_clip=a.per_clip, cols=a.cols, rows=a.rows, exclude=a.exclude)
    for p in r["sheets"]:
        print(p)
    print(f"{r['clips']} clip(s), {r['cells']} cell(s), {len(r['sheets'])} sheet(s) in {a.out}; cells in {a.out}/sheets.json")


def _print_fills(fills, d, wrote):
    for s in fills["slots"]:
        print(f"{s['slot']:2d}. {s['role']:14s} " + (f"{s['relpath']}  {s['in_s']:.2f}-{s['out_s']:.2f}s  sim {s['sim']}"
                                                   if s.get("path") else f"UNFILLED: {s['unfilled']}"))
    if fills.get("route"):
        print(f"cuts placed by the {fills['route']}" + (f"; {fills['cuts_on_onset']} of {len(fills['slots'])} on a played note"
                                                         if fills["route"] == "notes" else ""))
    print(f"wrote {wrote} to {d}")


def _plan_dir(a):
    """The plan folder --plan-dir or --variant names (None: the default <edits>/<shoot>-<method>/)."""
    from . import edit
    return edit.variant_dir(a.method, a.prefix, a.variant) if a.variant is not None else a.plan_dir


def cmd_slots_sheet(a):
    from . import edit
    r = edit.slots_sheet(store.connect(), edit.get_method(a.method), a.prefix, state=a.state, plan_dir=_plan_dir(a))
    for n, s in r["sheets"].items():
        print(f"slot {n}: {s['sheet']}  letters {', '.join(s['letters'])}")
    for n, why in r["skipped"].items():
        print(f"{why} (no sheet; the slot keeps its take)")
    print(f"{len(r['sheets'])} sheet(s) in {r['dir']}; answer with a picks file {{\"1\": \"B\", ...}} and compose")


def cmd_compose(a):
    from . import edit
    with open(a.picks) as f:
        picks = json.load(f)
    r = edit.compose(store.connect(), edit.get_method(a.method), a.prefix, picks, trim_mode=a.trim, state=a.state,
                     plan_dir=_plan_dir(a))
    _print_fills(r["fills"], r["dir"], r["wrote"])


def cmd_plan(a):
    from . import edit
    c = store.connect()
    kw = {"trim_mode": a.trim, "state": a.state, "gate": False if a.no_gate else None}
    if a.beats:
        with open(a.beats) as f:
            kw["beats"] = json.load(f)["beats_s"]
        kw["beat_start"] = a.beat_start
    if a.notes:
        if a.beats:
            sys.exit("--beats and --notes both given; a plan takes one of them")
        with open(a.notes) as f:
            kw["notes"] = json.load(f)
        kw["beat_start"] = a.beat_start
    if (a.variant is not None or a.plan_dir is not None) and (a.variants or not a.swap):
        sys.exit("--variant and --plan-dir pick the plan folder for --swap")
    if a.variants:
        if not a.variants.isdigit():  # bare --variants swallowed the prefix: N defaults to 6
            a.args.insert(0, a.variants); a.variants = "6"
        if len(a.args) not in (1, 2) or int(a.variants) < 1:
            sys.exit("plan --variants N <folder-prefix>  |  plan METHOD <folder-prefix> --variants N")
        if len(a.args) == 2:  # a method is named: hold it, vary only the fills
            vs, root, budget = edit.variants_within(c, edit.get_method(a.args[0]), a.args[1], int(a.variants), music=a.music,
                                            avoid=a.avoid or (), **kw)
            for v in vs:
                print(f"v{v['variant']} {v['method']:18s} theme {v['theme'] or '-':8s} score {v['score']}  {v['takes']} takes"
                      + (f", unfilled {v['unfilled']}" if v["unfilled"] else "")
                      + (f"\n    {v['plate_note']}" if v["plate_note"] else ""))
            if budget["short"]:
                print(f"take budget: {budget['gated_takes']} gated takes for {budget['need']:g} needed by {a.variants} variants; "
                      "fewer variants, or a reuse method, would fill")
            print(f"wrote {len(vs)} plans and variants.json to {root}")
            return
        vs, root = edit.variants(c, a.args[0], int(a.variants), music=a.music, **kw)
        for v in vs:
            print(f"v{v['variant']} {v['method']:18s} look {v['look'] or '-':14s} pace {v['pace']:<5} score {v['score']}  "
                  f"{v['takes']} takes" + (f", unfilled {v['unfilled']}" if v["unfilled"] else "") + f"\n    {'; '.join(v['reasons'])}"
                  + (f"\n    {v['plate_note']}" if v["plate_note"] else ""))
        print(f"wrote {len(vs)} plans and variants.json to {root}")
        return
    if len(a.args) != 2:
        sys.exit("plan <method-id> <folder-prefix>")
    a.method, a.prefix = a.args
    m = edit.get_method(a.method)
    if a.swap:
        if len(a.swap) > 2 or not a.swap[0].isdigit():
            sys.exit("--swap SLOT [LETTER]")
        r = edit.swap(c, m, a.prefix, int(a.swap[0]), a.swap[1] if len(a.swap) > 1 else None,
                      gate=kw["gate"], state=a.state, trim_mode=a.trim, plan_dir=_plan_dir(a))
        if "sheet" in r:
            print(f"sheet: {r['sheet']}  letters {', '.join(r['letters'])}; answer with --swap {a.swap[0]} LETTER")
            return
        fills, d, wrote = r["fills"], r["dir"], r["wrote"]
    else:
        fills, d, wrote = edit.plan(c, m, a.prefix, music=a.music, **kw)
    _print_fills(fills, d, wrote)


def _plan_opts(s):
    g = s.add_mutually_exclusive_group()
    g.add_argument("--variant", metavar="vK", help="act on that plan of <edits>/<shoot>-<method>-variants/ (as 'plan METHOD PREFIX --variants N' wrote it)")
    g.add_argument("--plan-dir", metavar="PATH", help="act on the plan in this folder (a variants folder, say) instead of <edits>/<shoot>-<method>/")


def main(argv=None):
    p = argparse.ArgumentParser(prog="footage_index")
    p.add_argument("--root", metavar="DIR", help="the footage folder, when no roots are configured (same as FOOTAGE_ROOT)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("index"); s.add_argument("--limit", type=int); s.add_argument("--only"); s.add_argument("--lanes", type=int, default=6); s.add_argument("--retry-errors", action="store_true"); s.add_argument("--card", metavar="PATH")
    s = sub.add_parser("embed"); s.add_argument("--batch", type=int, default=32)
    s = sub.add_parser("caption"); s.add_argument("--limit", type=int); s.add_argument("--every", type=int, default=3)
    s = sub.add_parser("find"); s.add_argument("query"); s.add_argument("--limit", type=int, default=10)
    s.add_argument("--no-client", dest="client", action="store_false", default=True); s.add_argument("--fps-min", type=float); s.add_argument("--camera")
    s.add_argument("--project"); s.add_argument("--sheet"); s.add_argument("--json", action="store_true")
    s.add_argument("--faces", dest="faces", action="store_true", default=None); s.add_argument("--no-faces", dest="faces", action="store_false")
    s.add_argument("--moving", dest="moving", action="store_true", default=None); s.add_argument("--static", dest="moving", action="store_false")
    sub.add_parser("stats")
    sub.add_parser("recaption-fts")
    sub.add_parser("cameras")
    s = sub.add_parser("rekey"); s.add_argument("--apply", action="store_true"); s.add_argument("--date", default=datetime.date.today().isoformat())
    s = sub.add_parser("migrate"); s.add_argument("--date", default=datetime.date.today().isoformat())
    s = sub.add_parser("shoot"); s.add_argument("prefix"); s.add_argument("--refresh", action="store_true"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("methods"); s.add_argument("prefix"); s.add_argument("--sheet")
    s = sub.add_parser("plan"); s.add_argument("args", nargs="*", metavar="[METHOD] PREFIX"); s.add_argument("--music")
    s.add_argument("--variants", nargs="?", const="6", metavar="N", help="N distinct plans across methods, looks and pacing (6 is the usual)")
    s.add_argument("--swap", nargs="+", metavar="SLOT [LETTER]")
    s.add_argument("--trim", choices=["motion", "centre"], default="motion", help="in/out from the motion curve, or centred on the keyframe window")
    s.add_argument("--beats", help="JSON with beats_s: slots are counted in beats of the method's tempo and laid on this track")
    s.add_argument("--notes", help="notes.json (period, onsets, strength) instead of --beats: cuts on the grid move onto the strongest played note near them")
    s.add_argument("--beat-start", type=int, default=0, help="index of the track beat the edit starts on (with --beats or --notes)")
    s.add_argument("--state", help="colour state to edit in (default: the one with the most gated shots)")
    s.add_argument("--no-gate", action="store_true", help="fill from raw search results, no quality gate")
    s.add_argument("--avoid", action="append", metavar="DIR",
                   help="another method's variants folder on this shoot: its takes count as already used (repeatable)")
    _plan_opts(sub.choices["plan"])
    s = sub.add_parser("gate"); s.add_argument("prefix"); s.add_argument("--json", action="store_true")
    s.add_argument("--sheet", metavar="DIR"); s.add_argument("--state")
    s = sub.add_parser("check"); s.add_argument("prefix"); s.add_argument("plans", nargs="*", metavar="PLAN_DIR")
    s.add_argument("--shotlist", metavar="FILE", help='{"shots": [{"name", "source", "in"}]}: exclude hits fail, gate flags warn')
    s = sub.add_parser("sheet"); s.add_argument("--prefix", required=True); s.add_argument("--out", required=True)
    s.add_argument("--skip", action="append", default=[], metavar="SUBSTR"); s.add_argument("--exclude", metavar="FILE")
    s.add_argument("--per-clip", type=int, default=6); s.add_argument("--cols", type=int, default=8); s.add_argument("--rows", type=int, default=6)
    s = sub.add_parser("slots-sheet"); s.add_argument("method"); s.add_argument("prefix"); s.add_argument("--state"); _plan_opts(s)
    s = sub.add_parser("compose"); s.add_argument("method"); s.add_argument("prefix"); s.add_argument("picks", metavar="PICKS.json")
    s.add_argument("--trim", choices=["motion", "centre"], default="motion"); s.add_argument("--state"); _plan_opts(s)
    a = p.parse_args(argv)
    if a.root:
        os.environ["FOOTAGE_ROOT"] = a.root
    if a.cmd == "stats":
        print(json.dumps(store.stats(store.connect()), indent=1))
    else:
        try:
            {"index": cmd_index, "embed": cmd_embed, "caption": cmd_caption, "recaption-fts": cmd_recaption_fts, "migrate": cmd_migrate, "rekey": cmd_rekey, "cameras": cmd_cameras, "find": cmd_find,
             "shoot": cmd_shoot, "methods": cmd_methods, "plan": cmd_plan, "gate": cmd_gate, "check": cmd_check, "sheet": cmd_sheet,
             "slots-sheet": cmd_slots_sheet, "compose": cmd_compose}[a.cmd](a)
        except ValueError as e:
            sys.exit(str(e))


if __name__ == "__main__":
    main()
