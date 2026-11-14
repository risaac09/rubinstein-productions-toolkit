"""Synthetic clip -> sample -> store -> search, no network volume and no models (vectors are made by hand)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))  # production/, which holds footage_index and rpresolve
TMP = tempfile.mkdtemp()
os.environ["FOOTAGE_DB"] = os.path.join(TMP, "f.db")
os.environ["FOOTAGE_THUMBS"] = os.path.join(TMP, "thumbs")
os.environ["FOOTAGE_RESTRICTED_PREFIX"] = "Restricted/"
os.environ["FOOTAGE_ROOTS"] = json.dumps([["Main", "/mnt/main"]])  # tests that name roots override it
os.environ.pop("RP_PIPELINE_CONFIG", None)

from footage_index import sample, search, store  # noqa: E402


def make_clip(path):
    # 3 s red, 3 s green, 3 s blue: three visibly different scenes
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=red:s=640x360:d=3",
                    "-f", "lavfi", "-i", "color=green:s=640x360:d=3", "-f", "lavfi", "-i", "color=blue:s=640x360:d=3",
                    "-filter_complex", "[0][1][2]concat=n=3:v=1", "-r", "24", path], check=True)


def test_sample_times():
    assert len(sample.sample_times(9.0)) == 3
    assert len(sample.sample_times(400.0)) == 40
    t = sample.sample_times(12.0)
    assert t[0] == (2.0, 0.0, 4.0) and t[-1][2] == 12.0


def test_end_to_end(real_toolkit):
    root = os.path.join(TMP, "root")
    os.makedirs(os.path.join(root, "Misc"), exist_ok=True)
    make_clip(os.path.join(root, "Misc", "rgb.mp4"))
    meta, shots = sample.process(root, "Misc/rgb.mp4", 1)
    assert round(meta["duration"]) == 9 and len(shots) == 3
    reds = [np.asarray(__import__("PIL.Image", fromlist=["Image"]).open(s["thumb"]).convert("RGB")).mean((0, 1)) for s in shots]
    assert reds[0][0] > 150 and reds[1][1] > 80 and reds[2][2] > 150  # each thumb is its scene
    store_search_cascade(os.environ["FOOTAGE_DB"], meta, shots)


def test_store_search_cascade():
    # the half of test_end_to_end that needs no rpresolve, so CI keeps it: hand-made shots stand in for sample.process
    shots = [{"t": 1.5 + 3 * i, "t_in": 3.0 * i, "t_out": 3.0 * (i + 1), "thumb": f"/nonexistent-{i}.jpg"} for i in range(3)]
    store_search_cascade(os.path.join(TMP, "cascade.db"), {"duration": 9.0}, shots)


def store_search_cascade(db, meta, shots):
    c = store.connect(db)
    cid, changed = store.upsert_clip(c, "Misc/rgb.mp4", "k1", 1, 1, client=0, project="Misc", **meta)
    assert changed and store.upsert_clip(c, "Misc/rgb.mp4", "k1", 1, 1)[1] is False
    ids = [store.add_shot(c, cid, s["t"], s["t_in"], s["t_out"], s["thumb"]) for s in shots]
    basis = np.eye(store.DIM, dtype=np.float32)
    for i, sid in enumerate(ids):
        store.set_vector(c, sid, basis[i])
    store.set_caption(c, ids[2], {"subject": "blue sky", "setting": "ocean", "tags": ["blue", "water"]}, "test")
    c.commit()

    hit = search.find(c, "", vector=basis[1], limit=1)
    assert hit[0]["id"] == ids[1]  # vector search finds the green shot
    hit = search.find(c, "ocean water", vector=basis[0], limit=3, max_per_clip=3)
    captioned = [h for h in hit if h["id"] == ids[2]]
    assert captioned and captioned[0]["caption"]["setting"] == "ocean"  # FTS reached the captioned shot
    assert search.find(c, "ocean", vector=basis[0], limit=1)[0]["id"] in (ids[0], ids[2])
    # a changed key drops the old shots
    store.upsert_clip(c, "Misc/rgb.mp4", "k2", 1, 1)
    assert store.stats(c)["shots"] == 0 and store.stats(c)["vectors"] == 0 and store.stats(c)["captions"] == 0
    c.commit(); c.close()


def test_caption_tags_and_fts_fields():
    from footage_index.store import clean_tags
    assert clean_tags(["ear", "Ear", " ear ", "hair", ""] * 5) == ["ear", "hair"]
    assert clean_tags("ear, hair") == ["ear", "hair"]
    assert len(clean_tags([str(i) for i in range(20)])) == 12
    c = store.connect(os.path.join(TMP, "captions.db"))
    cid, _ = store.upsert_clip(c, "Misc/C/a.mov", "kcap", 1, 1, project="C")
    sid = store.add_shot(c, cid, 1.0, 0.0, 2.0, "/c.jpg")
    store.set_caption(c, sid, {"subject": "dog", "setting": "beach", "camera_move": "static", "lens_feel": "normal",
                               "time_of_day": "unknown", "light": "indoor lighting, cannot determine", "tags": ["ear", "ear", "ear", "sand"]}, "test")
    match = lambda q: c.execute("SELECT count(*) FROM fts_captions WHERE fts_captions MATCH ?", (q,)).fetchone()[0]
    assert match('"beach"') == 1 and match('"static" OR "normal" OR "unknown" OR "determine"') == 0
    assert json.loads(c.execute("SELECT data FROM captions WHERE shot_id=?", (sid,)).fetchone()[0])["tags"] == ["ear", "sand"]
    assert c.execute("SELECT tags FROM fts_captions WHERE rowid=?", (sid,)).fetchone()[0] == "ear sand"
    c.close()


def test_client_filter():
    c = store.connect(os.environ["FOOTAGE_DB"])
    cid, _ = store.upsert_clip(c, "Restricted/X/a.mov", "kc", 1, 1, client=1, project="X")
    sid = store.add_shot(c, cid, 1.0, 0.0, 2.0, "/nonexistent.jpg")
    v = np.zeros(store.DIM, dtype=np.float32); v[5] = 1
    store.set_vector(c, sid, v)
    c.commit()
    assert all(h["client"] == 0 for h in search.find(c, "", vector=v, client=False))
    assert search.find(c, "", vector=v)[0]["id"] == sid  # client footage is in by default


def test_twins_and_self():
    c = store.connect(os.environ["FOOTAGE_DB"])
    v = np.zeros(store.DIM, dtype=np.float32); v[7] = 1
    a, _ = store.upsert_clip(c, "Misc/T/take.mov", "ka", 1, 1, project="T")
    b, _ = store.upsert_clip(c, "Misc/T/low res/take.mp4", "kb", 1, 1, project="T")
    sa = store.add_shot(c, a, 2.0, 0.0, 4.0, "/x.jpg"); sb = store.add_shot(c, b, 2.0, 0.0, 4.0, "/y.jpg")
    store.set_vector(c, sa, v); store.set_vector(c, sb, v); c.commit()
    hits = [h["id"] for h in search.find(c, "", vector=v, limit=5) if h["id"] in (sa, sb)]
    assert len(hits) == 1  # two copies of one take fold to one result
    assert sa not in [h["id"] for h in search.similar(c, sa, limit=5)]  # a shot is not its own neighbour
    c.close()


def test_low_res_sibling_skipped(tmp_path=None):
    from footage_index import __main__ as cli
    tsv = os.path.join(TMP, "index.tsv")
    with open(tsv, "w") as f:
        f.write("Restricted/X/a.mov\t9000000\t1\t\nRestricted/X/low res/a.mp4\t6000000\t1\t\nRestricted/X/low res/only.mp4\t6000000\t1\t\n")
    assert [r[0] for r in cli.candidates(rows=list(cli._catalog_rows(tsv)))] == ["Restricted/X/a.mov", "Restricted/X/low res/only.mp4"]


def _unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


def test_shoot_methods_plan(monkeypatch):
    from footage_index import edit, embed, shoot
    basis = np.eye(store.DIM, dtype=np.float32)
    tag = {t: basis[i] for i, t in enumerate(shoot.TAGS)}
    monkeypatch.setitem(shoot._probe, "m", (np.stack([tag[t] for t in shoot.TAGS]), np.arange(len(shoot.TAGS))))
    queries = {"a plate of food": tag["food"], "a mountain landscape": tag["nature"]}
    monkeypatch.setattr(embed, "text", lambda q: queries[q])
    monkeypatch.setattr(edit, "EDITS", os.path.join(TMP, "edits"))
    monkeypatch.setattr(edit, "TOOLKIT", os.path.join(TMP, "no-toolkit"))
    c = store.connect(os.path.join(TMP, "shoots.db"))

    def clip(rel, dur, fps, vecs, faces=0):
        cid, _ = store.upsert_clip(c, rel, rel, 1, 1, client=int(rel.startswith("Restricted/")), project=rel.split("/")[1],
                                   fps=fps, duration=dur, status="done")
        for k, v in enumerate(vecs):
            sid = store.add_shot(c, cid, 2.0 + 4 * k, 4.0 * k, 4.0 * k + 4, f"/x/{cid}-{k}.jpg", motion=0.0,
                                 faces=faces, face_max=0.0, luma=0.5)
            store.set_vector(c, sid, v)

    rng = np.random.default_rng(1)
    noise = lambda: rng.normal(0, 0.02, store.DIM).astype(np.float32)
    dinner = "Misc/Outings/Dinner"
    # the low-res handoff is the best food match; its full-res sibling (same length, its stem in the name) must win
    clip(f"{dinner}/low res/P1900001_plate.mp4", 10.0, 24, [_unit(tag["food"] * 1.0 + noise())] * 2, faces=1)
    clip(f"{dinner}/P1900001.MOV", 10.0, 24, [_unit(tag["food"] * 0.97 + noise() + basis[40] * 0.2)] * 2, faces=1)
    takes = {}
    for i, dur in enumerate((11.0, 12.0, 13.0)):
        takes[i] = [_unit(tag["food"] * (0.9 - 0.1 * i) + noise() + basis[50 + i] * 0.3)] * 2
        clip(f"{dinner}/P190000{i + 2}.MOV", dur, 60 if i == 2 else 24, takes[i], faces=1)
    # a renamed render of P1900003 (same length, same picture): another copy of a used take, never a second take
    clip(f"{dinner}/render/V1-0003_P1900003.mov", 12.0, 24, takes[1], faces=1)
    for i in range(4):
        clip(f"Restricted/Hike/C{i}.MP4", 20.0 + i, 30, [_unit(tag["nature"] + noise() + basis[60 + i] * 0.3)] * 3)
    for i in range(6):
        clip(f"Misc/Other/O{i}.MP4", 30.0 + i, 30, [_unit(basis[100 + i] + noise())] * 3)
    c.commit()

    a, b = shoot.profile(c, dinner), shoot.profile(c, "Restricted/Hike")
    assert a["tags"]["food"]["strength"] > 0.5 > b["tags"]["food"]["strength"]
    assert b["tags"]["nature"]["strength"] > 0.5 > a["tags"]["nature"]["strength"]
    assert a["tags"]["people"]["strength"] > b["tags"]["people"]["strength"]
    assert a["tags"]["travel"]["evidence"] and a["fps_mix"] == {"24": 5, "60": 1}
    assert shoot.profile(c, dinner) == a  # served from the cache table
    assert shoot.profile(c, "Misc/Outings/Din")["shots"] == 0  # a prefix never takes in sibling folders

    methods = edit.load_methods(os.path.join(HERE, "fixtures", "edit-methods"))
    monkeypatch.setattr(edit, "gated_takes", lambda c, p: None)  # this fixture has no gate probes: budget 1.0
    assert edit.rank(c, dinner, methods)["methods"][0]["id"] == "dinner-test"
    top_b = edit.rank(c, "Restricted/Hike", methods)["methods"]
    assert top_b[0]["id"] == "hike-test" and top_b[0]["slot_cover"] == 1.0 and len(top_b[0]["thumbs"]) == 2
    # The take budget: a shoot with one gated take for a method of several no-reuse slots scores that share of itself.
    full = edit.rank(c, "Restricted/Hike", methods, top=None)["methods"]
    monkeypatch.setattr(edit, "gated_takes", lambda c, p: 1)
    short = {x["id"]: x for x in edit.rank(c, "Restricted/Hike", methods, top=None)["methods"]}
    for x in full:
        n = len(next(m for m in methods if m["id"] == x["id"])["slots"])
        assert short[x["id"]]["take_budget"] == round(min(1.0, 1 / n), 3)
        assert abs(short[x["id"]]["score"] - x["score"] / x["take_budget"] * min(1.0, 1 / n)) < 0.002
    # three variants sharing at most half their slots need twice the takes of one
    assert edit.take_need({"slots": [0] * 30}, 3) == 60 and edit.take_need({"slots": [0] * 30, "rules": {"reuse": True}}) == 15
    assert edit.rank(c, "Restricted/Hike", methods, top=None, variants=3)["methods"][0]["take_budget"] < 1.0

    m = next(x for x in methods if x["id"] == "dinner-test")
    fills, d, wrote = edit.plan(c, m, dinner, music="/tmp/song.wav", gate=False)
    slots = fills["slots"]
    assert all(s["path"] for s in slots) and "fills.json only" in wrote
    takes = [s["relpath"] for s in slots]
    assert len(set(takes)) == 3 and not any("low res" in t for t in takes)  # no reuse, full-res preferred
    assert slots[0]["relpath"] == f"{dinner}/P1900001.MOV" and slots[0]["low_res"]
    assert slots[2]["fps"] == 60  # half speed needs a high-frame-rate take
    for s, slot in zip(slots, m["slots"]):
        assert abs((s["out_s"] - s["in_s"]) - slot["seconds"]) < 1e-6
        assert s["in_s"] >= 0 and s["out_s"] <= 13.0
    assert json.load(open(os.path.join(d, "fills.json")))["music"] == "/tmp/song.wav"
    assert not any("V1-0003" in t for t in takes) or not any(t.endswith("P1900003.MOV") for t in takes)
    v1, p3, p2 = (c.execute("SELECT id FROM clips WHERE relpath LIKE ?", (f"%{x}",)).fetchone()[0]
                  for x in ("V1-0003_P1900003.mov", "/P1900003.MOV", "/P1900002.MOV"))
    assert edit.same_take(c, v1, p3) and not edit.same_take(c, v1, p2)
    # a slow slot on a shoot with no high-frame-rate take still fills, flagged fps_short
    hike = next(x for x in methods if x["id"] == "hike-test")
    slow = {**hike, "slots": [{**hike["slots"][0], "speed": 0.5}]}
    f = edit.plan(c, slow, "Restricted/Hike", gate=False)[0]["slots"][0]
    assert f["path"] and f["fps_short"] and f["fps"] == 30
    # all takes used once: with reuse off, a fourth slot of the same query finds only the one take left
    m4 = {**m, "slots": m["slots"][:2] * 2}
    takes4 = [s.get("relpath") for s in edit.plan(c, m4, dinner, gate=False)[0]["slots"]]
    assert len([t for t in takes4 if t]) == len({t for t in takes4 if t})
    assert not ({f"{dinner}/P1900003.MOV", f"{dinner}/render/V1-0003_P1900003.mov"} <= set(takes4))  # one take, two copies
    assert edit.trim({"duration": 3.0, "t_in": 0.0, "t_out": 3.0}, 2) == (0.5, 2.5)
    assert edit.trim({"duration": 10.0, "t_in": 8.0, "t_out": 10.0}, 3) == (7.0, 10.0)
    c.close()


def test_variants(monkeypatch):
    from footage_index import edit
    c = store.connect(os.path.join(TMP, "shoots.db"))  # the two-shoot index test_shoot_methods_plan built
    looks = os.path.join(TMP, "looks"); os.makedirs(looks, exist_ok=True)
    for lk in ("kiki", "her", "bleach"):
        open(os.path.join(looks, lk + ".json"), "w").write("{}")
    taste = os.path.join(TMP, "taste.md"); open(taste, "w").write("Kiki 130 over Her 70.\nRP Neutral over Bleach.")
    events = os.path.join(TMP, "events.jsonl")
    with open(events, "w") as f:
        f.write(json.dumps({"options": {"A": "Misc/Outings/Dinner/low res/P1900002_dish.mp4@3.0", "B": "x/y.mov@1"},
                            "choice": "A"}) + "\nnot json\n")
    for k, v in {"LOOKS": looks, "TASTE": taste, "EVENTS": events, "EDITS": os.path.join(TMP, "edits"),
                 "TOOLKIT": os.path.join(TMP, "no-toolkit")}.items():
        monkeypatch.setattr(edit, k, v)
    from footage_index import embed
    basis = np.eye(store.DIM, dtype=np.float32)
    monkeypatch.setattr(embed, "text", lambda q: basis[4] if "food" in q else basis[8])  # food, nature (TAGS order)
    methods = edit.load_methods(os.path.join(HERE, "fixtures", "edit-methods"))
    vs, root = edit.variants(c, "Misc/Outings/Dinner", 4, methods=methods, gate=False)
    assert len(vs) == 4 and os.path.exists(os.path.join(root, "variants.json"))
    plans = {}
    for v in vs:
        f = json.load(open(os.path.join(v["dir"], "fills.json")))
        m = json.load(open(os.path.join(v["dir"], "method.json")))
        plans[v["variant"]] = ({s["relpath"] for s in f["slots"] if s.get("path")}, len(m["slots"]))
        for s, slot in zip(f["slots"], m["slots"]):  # fills follow the paced method's lengths
            assert not s.get("path") or abs((s["out_s"] - s["in_s"]) - slot["seconds"]) < 1e-6
    ids = sorted(plans)
    for i in ids:
        for j in ids:
            if i < j:
                # shared takes never pass half of either plan's slots
                assert len(plans[i][0] & plans[j][0]) <= min(plans[i][1], plans[j][1]) // 2
    assert len({(v["method"], v["look"]) for v in vs}) == 4 and len({v["pace"] for v in vs}) > 1
    assert "kiki" in {v["look"] for v in vs}  # won in the profile, so offered first after the methods' own looks
    assert edit.taste(["kiki", "her", "bleach"])[1] == {"kiki"}  # a look after "over" lost
    assert any("picked before in review" in r for v in vs for r in v["reasons"])
    c.close()


# ---- gate, trim, within-method variants: synthetic shoot, probes stubbed, no models and no network volume ----
import pytest  # noqa: E402
from PIL import Image  # noqa: E402

from footage_index import edit, embed, trim  # noqa: E402
from footage_index import gate as G  # noqa: E402

_dims = {}


def fake_text(q):
    """Every distinct probe or query is its own axis (300 and up), so similarities are exact."""
    _dims.setdefault(q, 300 + len(_dims))
    v = np.zeros(store.DIM, np.float32); v[_dims[q]] = 1
    return v


SH = "Misc/Gate"


@pytest.fixture
def shoot(monkeypatch):
    monkeypatch.setattr(embed, "text", fake_text)
    monkeypatch.setattr(sys.modules[__name__], "_dims", {})  # axes are handed out in order of first use; role-fit z rides on that order
    for k, v in {"_pv": {}, "_sharp": {}, "PROBE_CACHE": None}.items():
        monkeypatch.setattr(G, k, v)
    monkeypatch.setattr(edit, "EDITS", os.path.join(TMP, "gedits"))
    monkeypatch.setattr(edit, "TOOLKIT", os.path.join(TMP, "no-toolkit"))
    c = store.connect(os.path.join(TMP, f"gate-{len(os.listdir(TMP))}.db"))
    rng, ids, n = np.random.default_rng(3), {}, [0]
    P = fake_text

    def add(rel, kind, amp, vec_parts, luma=0.5, motion=0.01, state="graded", dur=12.0, label=None, thumb=None):
        n[0] += 1
        if thumb is None:
            thumb = os.path.join(TMP, f"gt{n[0]}-{len(os.listdir(TMP))}.jpg")
            arr = (0.5 + amp * (rng.random((54, 96)) - 0.5)) if amp else np.tile(np.linspace(0.45, 0.55, 96), (54, 1))
            Image.fromarray((arr * 255).astype(np.uint8)).save(thumb)
        prof = dict(camera="DaVinci Resolve render", profile="derived") if state == "graded" else \
            dict(camera="Cam", profile="V-Log", color_space="VG")
        cid, _ = store.upsert_clip(c, rel, rel, 1, 1, client=0, fps=24, duration=dur, status="done", **prof)
        sid = store.add_shot(c, cid, 6.0, 4.0, 8.0, thumb, motion=motion, faces=0, face_max=0.0, luma=luma)
        v = sum(w * (basis[x] if isinstance(x, int) else P(x)) for x, w in vec_parts) + rng.normal(0, 0.02, store.DIM)
        store.set_vector(c, sid, _unit(v))
        ids.setdefault(label or kind, []).append(sid)
        return sid, thumb, v

    basis = np.eye(store.DIM, dtype=np.float32)
    place = iter(range(0, 250))
    plain = lambda: [(G.GOOD, 0.6), (next(place), 0.8)]
    for i in range(36):
        parts = plain() + ([(edit.THEMES["feast"], 0.5)] if i % 2 == 0 else [])
        add(f"{SH}/g/clip-{i:03d}_P{1900000 + i}.mov", "ok", 0.30 + 0.01 * i, parts)
    for i in range(8):
        add(f"{SH}/g/blur_P{2000000 + i}.mov", "blur", 0, [(G.BAD[0], 0.6), (next(place), 0.8)])
    for i in range(2):
        add(f"{SH}/g/dark_P{3000000 + i}.mov", "dark", 0.5, plain(), luma=0.02)
        add(f"{SH}/g/blown_P{3100000 + i}.mov", "blown", 0.5, plain(), luma=0.97)
    for i in range(3):
        add(f"{SH}/g/side_P{3200000 + i}.mov", "side", 0.95, [(G.GOOD, 0.6), (G.SIDE, 0.9), (next(place), 0.8)])
        add(f"{SH}/g/smear_P{3300000 + i}.mov", "smear", 0.95, plain(), motion=0.9)
    for i in range(2):
        add(f"{SH}/g/keeper_P{3400000 + i}.mov", "keeper", 0.95, [(G.BAD[1], 0.9), (G.GOOD, 0.1), (next(place), 0.8)])
    door = os.path.join(TMP, f"door-{len(os.listdir(TMP))}.jpg")  # shot through a doorway: sharp, bright enough, 60% black
    arr = 0.5 + 0.5 * (rng.random((54, 96)) - 0.5)
    arr[:, 38:] = 0.0
    Image.fromarray((arr * 255).astype(np.uint8)).save(door)
    add(f"{SH}/g/door_P3500000.mov", "door", 0, plain(), thumb=door)
    for i in range(8):
        add(f"{SH}/o/orig_P{4000000 + i}.mov", "orig", 0.5, plain(), state="original")
    # an ok take again in another folder, low res: a copy at the same second
    v0 = c.execute("SELECT s.thumb, v.embedding FROM shots s JOIN vec_shots v ON v.shot_id = s.id WHERE s.id = ?",
                   (ids["ok"][30],)).fetchone()
    add(f"{SH}/copy/low res/x_P1900030.mp4", "copy", 0, [], thumb=v0[0])
    store.set_vector(c, ids["copy"][0], np.frombuffer(v0[1], np.float32))
    # two takes of one place, both strong on a query: near-duplicates that must not both be picked
    for k in range(2):
        add(f"{SH}/g/dup{k}_P{5000000 + k}.mov", "dup", 0.9, [(G.GOOD, 0.6), (240, 0.8), ("qdup", 0.9)])
    c.commit()
    return c, ids


def test_gate_reasons_and_states(shoot):
    c, ids = shoot
    g = G.load(c, SH)
    why = {g.rows[i]["id"]: g.reason[i] for i in range(g.n)}
    for kind, reason in (("blur", "soft"), ("dark", "dark"), ("blown", "blown"), ("side", "sideways"),
                         ("smear", "motion"), ("keeper", "keeper"), ("orig", "other_state"), ("copy", "copy"),
                         ("door", "mostly_black")):
        assert {why[s] for s in ids[kind]} == {reason}, kind
    assert g.state == "graded" and g.summary()["passes"] == int(g.ok.sum()) > 10
    st = g.states
    assert set(st) == {"graded", "original:Cam/V-Log/VG"} and st["original:Cam/V-Log/VG"] == {"shots": 8, "unique": 8, "gated": None}
    assert st["graded"]["unique"] == st["graded"]["shots"] - 1 and st["graded"]["gated"] == int(g.ok.sum())  # the copy is not in the population
    assert why[ids["ok"][30]] is None  # the copy lost, not the take
    assert G.load(c, SH, state="original:Cam/V-Log/VG").state.startswith("original")
    with pytest.raises(ValueError):
        G.load(c, SH, state="nope")
    assert g.sheet(os.path.join(TMP, "gate-sheet.jpg")) and os.path.exists(os.path.join(TMP, "gate-sheet.jpg"))
    ex = G.load(c, SH, exclude={"shots": [ids["ok"][20]], "takes": ["P1900021"]})
    r = {ex.rows[i]["id"]: ex.reason[i] for i in range(ex.n)}
    assert r[ids["ok"][20]] == "excluded" and r[ids["ok"][21]] == "excluded"
    # an excluded shot takes the take's other copy at that second with it
    ex = G.load(c, SH, exclude={"shots": [ids["ok"][30]]})
    r = {ex.rows[i]["id"]: ex.reason[i] for i in range(ex.n)}
    assert r[ids["ok"][30]] == "excluded" and r[ids["copy"][0]] == "excluded"


METHOD = {"id": "m-test", "fps": 30, "rules": {}, "slots": [
    {"role": "establishing wide", "seconds": 2.0, "query": "qa", "move": "static", "speed": 1},
    {"role": "close on hands detail", "seconds": 1.5, "query": "qb", "move": "push", "speed": 1},
    {"role": "burst", "seconds": 0.4, "query": "qdup", "move": "static", "speed": 1},
    {"role": "payoff", "seconds": 1.0, "query": "qdup", "move": "static", "speed": 1}]}


def test_plan_notes_snap(shoot, monkeypatch, tmp_path):
    c, _ = shoot
    g = G.load(c, SH)
    # METHOD runs at the default 120 bpm: on a 0.5 s period the grid cuts fall at 2.0, 3.5, 4.0 and 5.0 s
    notes = {"period": 0.5, "bpm": 120.0, "onsets": [2.04, 2.1, 3.6, 4.9], "strength": [2.0, 4.0, 1.0, 2.0]}
    f = edit.fill_slots(c, METHOD, SH + "/", gate=g, trim_mode="centre", notes=notes)
    assert [s["cut_s"] for s in f["slots"]] == [2.1, 3.5, 4.0, 4.9]  # strongest in window; 1.0 is under the floor; none near 4.0
    assert [round(s["seconds"], 3) for s in f["slots"]] == [2.1, 1.4, 0.5, 0.9]
    assert f["route"] == "notes" and f["cuts_on_onset"] == 2
    beats = [round(0.5 * k, 3) for k in range(0, 40)]
    f = edit.fill_slots(c, METHOD, SH + "/", gate=g, trim_mode="centre", beats=beats)
    assert f["route"] == "grid" and "cuts_on_onset" not in f
    with pytest.raises(ValueError, match="both"):
        edit.fill_slots(c, METHOD, SH + "/", gate=g, beats=beats, notes=notes)
    bf, nf = tmp_path / "b.json", tmp_path / "n.json"
    bf.write_text(json.dumps({"beats_s": beats})); nf.write_text(json.dumps(notes))
    from footage_index import __main__ as cli
    with pytest.raises(SystemExit):
        cli.main(["plan", "m-test", SH, "--beats", str(bf), "--notes", str(nf)])


def test_pace_for_notes():
    m = {"rules": {"snap_to_beat": True}, "bpm": [100, 140]}
    assert [edit.pace_for(m, k) for k in range(3)] == [1.0, round(120 / 140, 3), round(120 / 100, 3)]  # no file: as before
    assert [edit.pace_for(m, k, 60 / 130) for k in range(3)] == [1.0, round(130 / 140, 3), round(130 / 100, 3)]
    assert edit.pace_for({"rules": {}, "bpm": [100, 140]}, 1, 0.4) == 0.85  # no flag, no change


def test_plan_gated_one_state_exclusions_diversity(shoot, monkeypatch):
    c, ids = shoot
    g = G.load(c, SH, exclude={"takes": ["P1900005"]})
    f = edit.fill_slots(c, METHOD, SH + "/", gate=g)
    ok = {g.rows[i]["id"] for i in range(g.n) if g.ok[i]}
    picks = [s for s in f["slots"] if s.get("path")]
    assert len(picks) == 4 and all(s["shot_id"] in ok for s in picks)
    assert {s["state"] for s in picks} == {"graded"} and f["state"] == "graded"  # one colour state
    assert len({s["take"] for s in picks}) == 4  # the copy of P1900030 and its original are one take
    assert "P1900005" not in {s["take"] for s in picks}
    vs = [fake_text("x")] and [np.frombuffer(c.execute("SELECT embedding FROM vec_shots WHERE shot_id=?", (s["shot_id"],)).fetchone()[0], np.float32) for s in picks]
    assert all(float(a @ b) < edit.MAX_COS for i, a in enumerate(vs) for b in vs[:i])  # the dup place is used once
    o = edit.fill_slots(c, METHOD, SH + "/", gate=G.load(c, SH, state="original:Cam/V-Log/VG"))
    assert all(s["relpath"].startswith(f"{SH}/o/") for s in o["slots"] if s.get("path"))
    # a nameless take (no camera id) falls back to edit.same_take: two copies of one picture, same length
    c2 = store.connect(os.path.join(TMP, "nameless.db"))
    v = _unit(np.random.default_rng(1).normal(size=store.DIM)).astype(np.float32)
    for rel in ("a/x.mov", "b/y.mov"):
        cid, _ = store.upsert_clip(c2, f"N/{rel}", rel, 1, 1, fps=24, duration=5.0)
        store.set_vector(c2, store.add_shot(c2, cid, 2.0, 0, 4, "/none.jpg"), v)
    k = G.take_keys(c2, {1: ("N/a/x.mov", 5.0), 2: ("N/b/y.mov", 5.0)})
    assert k[1] == k[2]


def test_plan_trim_and_beats(shoot, monkeypatch):
    c, _ = shoot
    calls = []
    monkeypatch.setattr(trim, "span", lambda r, length, handle=0.0: calls.append(length) or (1.0, 1.0 + length, "stub curve"))
    g = G.load(c, SH)
    f = edit.fill_slots(c, METHOD, SH + "/", gate=g)
    assert len(calls) == 4 and f["slots"][0]["trim"] == "stub curve" and f["slots"][0]["in_s"] == 1.0
    calls.clear()
    f = edit.fill_slots(c, METHOD, SH + "/", gate=g, trim_mode="centre")  # the centred trim stays reachable
    assert not calls and f["slots"][0]["trim"].startswith("centred") and f["slots"][0]["in_s"] == 5.0
    beats = [round(0.5 * k, 3) for k in range(1, 40)]
    f = edit.fill_slots(c, METHOD, SH + "/", gate=g, beats=beats)
    half = {round(0.25 * k, 3) for k in range(2, 80)}  # beats and half beats
    for s in f["slots"]:
        assert s["cut_s"] in half and abs((s["out_s"] - s["in_s"]) - s["seconds"]) < 1e-6
    assert f["music_in_s"] == 0.5 and edit.fill_slots(c, METHOD, SH + "/", gate=g, beats=beats, beat_start=8)["music_in_s"] == 4.5
    # a "same take" slot keeps in = the previous out
    m = {**METHOD, "slots": [METHOD["slots"][0], {**METHOD["slots"][3], "query": "same take, punch in"}]}
    s0, s1 = edit.fill_slots(c, m, SH + "/", gate=g)["slots"]
    assert s1["relpath"] == s0["relpath"] and s1["in_s"] == s0["out_s"] and "previous take" in s1["trim"]


def test_variants_within_method(shoot, tmp_path=None):
    c, _ = shoot
    m = {**METHOD, "slots": METHOD["slots"][:3]}
    vs, root, budget = edit.variants_within(c, m, SH, 3)
    assert len(vs) == 3 and {v["method"] for v in vs} == {"m-test"} and "feast" in {v["theme"] for v in vs}
    # three no-reuse variants of 3 slots, each sharing at most half with the others, need 3 * (1 + 1) = 6 takes
    assert budget["need"] == 6 and budget["short"] == (budget["gated_takes"] < 6)
    assert json.load(open(os.path.join(root, "variants.json")))["budget"] == budget
    plans = {}
    for v in vs:
        f = json.load(open(os.path.join(v["dir"], "fills.json")))
        assert f["slots"] and json.load(open(os.path.join(v["dir"], "method.json")))["id"] == "m-test"
        assert os.path.exists(os.path.join(v["dir"], "minors.jpg"))  # every pick, child-like first, for a person to read
        assert all(s["kid"] is not None for s in f["slots"] if s.get("path"))
        plans[v["variant"]] = {s["relpath"] for s in f["slots"] if s.get("path")}
    assert len({frozenset(p) for p in plans.values()}) == 3
    assert all(len(plans[i] & plans[j]) <= 1 for i in plans for j in plans if i < j)
    # a second method on the same shoot, told what the first one used, does not open on the same takes
    vs2, _, _ = edit.variants_within(c, {**m, "id": "m-test-2"}, SH, 1, avoid=[root])
    f2 = json.load(open(os.path.join(vs2[0]["dir"], "fills.json")))
    assert {s["relpath"] for s in f2["slots"] if s.get("path")} != plans[1]


def _exclude(monkeypatch, tmp_path, body):
    """Point the edits folder at tmp_path and write the shoot's exclude file there (body: a dict, or any JSON value)."""
    monkeypatch.setattr(edit, "EDITS", str(tmp_path))
    with open(edit.exclude_path(SH), "w") as f:
        json.dump(body, f)


def test_build_gate_reads_the_exclude_file(shoot, monkeypatch, tmp_path):
    c, ids = shoot
    monkeypatch.setattr(edit, "EDITS", str(tmp_path))
    why = lambda g: {g.rows[i]["id"]: g.reason[i] for i in range(g.n)}
    assert "excluded" not in why(edit.build_gate(c, SH)).values()  # no file, nothing excluded
    _exclude(monkeypatch, tmp_path, {"takes": ["P1900005"], "shots": [ids["ok"][7]], "why": {"P1900005": "a minor"}, "ask_owner": []})
    r = why(edit.build_gate(c, SH))
    assert r[ids["ok"][5]] == r[ids["ok"][7]] == "excluded" and r[ids["ok"][6]] != "excluded"


def test_exclude_file_with_an_unread_key_is_refused(shoot, monkeypatch, tmp_path):
    c, ids = shoot
    _exclude(monkeypatch, tmp_path, {"why": {"P1900005": "a minor"}, "exclude": ["P1900005"]})  # a key the gate does not read
    with pytest.raises(ValueError, match=r"gate-exclude\.json: unknown key 'exclude'"):
        edit.build_gate(c, SH)
    _exclude(monkeypatch, tmp_path, {"takes": "P1900005"})  # a string would be read one letter at a time
    with pytest.raises(ValueError, match="'takes' must be a list"):
        edit.build_gate(c, SH)
    _exclude(monkeypatch, tmp_path, ["P1900005"])
    with pytest.raises(ValueError, match="expected an object"):
        edit.build_gate(c, SH)
    with open(edit.exclude_path(SH), "w") as f:
        f.write("{")
    with pytest.raises(ValueError, match="not valid JSON"):
        edit.build_gate(c, SH)


def _swap_setup(monkeypatch, tmp_path, **fills):
    """Point the edits folder at tmp_path, give METHOD a fills.json (extra keys, such as state, ride along), and stub the
    sheet maker. Returns the list the thumbs of each sheet are appended to."""
    sheets = []
    monkeypatch.setattr(edit, "_absheet", lambda paths, out, title=None: sheets.append(list(paths)) or {"labels": {"A": 1, "B": 2}})
    monkeypatch.setattr(edit, "EDITS", str(tmp_path))
    os.makedirs(edit.out_dir(METHOD["id"], SH), exist_ok=True)
    with open(os.path.join(edit.out_dir(METHOD["id"], SH), "fills.json"), "w") as f:
        json.dump({"slots": [{"slot": n} for n in range(1, 5)], **fills}, f)
    return sheets


def _thumbs(c, like):
    return {r[0] for r in c.execute("SELECT s.thumb FROM shots s JOIN clips cl ON cl.id = s.clip_id WHERE cl.relpath LIKE ?", (like,))}


def test_swap_does_not_offer_an_excluded_take(shoot, monkeypatch, tmp_path):
    c, ids = shoot
    sheets = _swap_setup(monkeypatch, tmp_path)
    dup = {c.execute("SELECT thumb FROM shots WHERE id = ?", (i,)).fetchone()[0] for i in ids["dup"]}  # best match for slot 4's query
    edit.swap(c, METHOD, SH, 4, gate=False)  # the ungated path still works, and offers a dup take
    assert dup & set(sheets[-1])
    edit.swap(c, METHOD, SH, 4)  # control: the default is the gate, and with no exclude list it still offers a dup take
    assert dup & set(sheets[-1])
    _exclude(monkeypatch, tmp_path, {"takes": ["P5000000", "P5000001"]})
    r = edit.swap(c, METHOD, SH, 4)  # the list now applies to the same call
    assert r["letters"] == ["A", "B"] and sheets[-1] and not dup & set(sheets[-1])
    edit.swap(c, METHOD, SH, 4, gate=False)  # and gate=False still opts out of it
    assert dup & set(sheets[-1])


def test_swap_draws_from_the_state_the_plan_was_made_with(shoot, monkeypatch, tmp_path):
    """Slot 2: the one slot with two free takes in the original state, as well as in the graded one."""
    c, _ = shoot
    graded, original = _thumbs(c, f"{SH}/g/%") | _thumbs(c, f"{SH}/copy/%"), _thumbs(c, f"{SH}/o/%")
    sheets = _swap_setup(monkeypatch, tmp_path)
    edit.swap(c, METHOD, SH, 2)  # fills.json names no state: the gate's own pick, graded
    assert sheets[-1] and set(sheets[-1]) <= graded
    edit.swap(c, METHOD, SH, 2, state="original:Cam/V-Log/VG")  # an explicit state wins
    assert sheets[-1] and set(sheets[-1]) <= original
    sheets = _swap_setup(monkeypatch, tmp_path, state="original:Cam/V-Log/VG")  # a plan made with --state original
    edit.swap(c, METHOD, SH, 2)
    assert sheets[-1] and set(sheets[-1]) <= original
    edit.swap(c, METHOD, SH, 2, state="graded")
    assert sheets[-1] and set(sheets[-1]) <= graded


def _manifest(d, spans, root="/Volumes/Main"):
    """A hand-built v2 manifest, no fills.json: spans is [(relpath, in, out)]."""
    os.makedirs(d, exist_ok=True)
    ids = {r: f"s{k + 1}" for k, r in enumerate(dict.fromkeys(r for r, _, _ in spans))}
    m = {"version": 2, "speech": False, "fps": 24, "sources": [{"id": i, "path": f"{root}/{r}"} for r, i in ids.items()],
         "clips": [{"name": "m-test", "spans": [{"source": ids[r], "in": a, "out": b} for r, a, b in spans]}]}
    with open(os.path.join(d, "manifest.json"), "w") as f:
        json.dump(m, f)
    return str(d)


def test_check_fails_a_manifest_that_names_excluded_footage(shoot, monkeypatch, tmp_path, capsys):
    from footage_index import __main__ as cli
    c, ids = shoot
    ok = lambda i: f"{SH}/g/clip-{i:03d}_P{1900000 + i}.mov"
    clean = _manifest(tmp_path / "clean", [(ok(1), 1.0, 3.0), (ok(2), 0.0, 2.0)])
    monkeypatch.setattr(edit, "EDITS", str(tmp_path))
    with pytest.raises(ValueError, match="no exclude list"):  # a mistyped shoot cannot pass by finding nothing to check
        edit.check(c, SH, [clean])
    _exclude(monkeypatch, tmp_path, {"takes": ["P1900005", ok(9)], "shots": [ids["ok"][7], ids["ok"][30]]})
    copy = f"{SH}/copy/low res/x_P1900030.mp4"  # a low-res copy of ok 30: the shot's exclusion takes it along
    take = _manifest(tmp_path / "take", [(ok(1), 1, 3), (ok(5), 9, 11), (ok(9), 0, 2)])  # 9 to 11 s is past the take's only keyframe window
    shot = _manifest(tmp_path / "shot", [(ok(7), 5, 6), (ok(7), 8, 10), (copy, 3, 5)])
    away = _manifest(tmp_path / "away", [(f"{SH}/z/not-indexed_P1900005.mov", 0, 2)], root="/mnt/other")
    assert edit.check(c, SH, [clean]) == []
    t = edit.check(c, SH, [take])
    assert len(t) == 2 and "span 2" in t[0] and "excluded take P1900005" in t[0] and f"excluded take {ok(9)}" in t[1] and "span 3" in t[1]
    s = edit.check(c, SH, [shot])
    assert len(s) == 2 and "span 1" in s[0] and str(ids["ok"][7]) in s[0] and "span 3" in s[1] and str(ids["copy"][0]) in s[1]  # 8 to 10 s clears the window
    assert len(edit.check(c, SH, [away])) == 1  # not in the index: found by the take id in the file name
    assert len(edit.check(c, SH, [clean, take, shot, away])) == 5
    nowhere = edit.check(c, SH, [str(tmp_path / "nowhere"), take])  # a plan that cannot be read is a line, and the rest still run
    assert len(nowhere) == 3 and "cannot read manifest.json" in nowhere[0] and "span 2" in nowhere[1]
    with open(os.path.join(clean, "manifest.json"), "w") as f:
        json.dump({"sources": [], "clips": []}, f)
    assert "no spans" in edit.check(c, SH, [clean])[0]
    # a hand-built manifest that is malformed is a line naming its plan, and the plans after it still run
    bad = {"unknown source": {"sources": [], "clips": [{"spans": [{"source": "s9", "in": 0, "out": 1}]}]},
           "no in/out": {"sources": [{"id": "s1", "path": f"/V/{ok(1)}"}], "clips": [{"spans": [{"source": "s1", "start": 0, "end": 1}]}]},
           "not an object": []}
    dirs = []
    for name, body in bad.items():
        dirs.append(str(tmp_path / name.replace(" ", "-").replace("/", "-")))
        os.makedirs(dirs[-1])
        with open(os.path.join(dirs[-1], "manifest.json"), "w") as f:
            json.dump(body, f)
    m = edit.check(c, SH, dirs + [take])
    assert len(m) == 3 + 2 and all("malformed manifest" in x and x.endswith("not checked") and d in x for x, d in zip(m, dirs))
    assert "span 2" in m[3]
    # the CLI exits non-zero naming the spans, and prints a one-line pass for clean plans
    monkeypatch.setattr(store, "connect", lambda *a, **k: c)
    with pytest.raises(SystemExit) as e:
        cli.main(["check", SH, take, shot])
    assert "span 2" in str(e.value) and "4 span(s) use footage" in str(e.value) and "0 plan(s) not checked" in str(e.value)
    with pytest.raises(SystemExit) as e:  # plans that could not be read are counted apart from spans
        cli.main(["check", SH, take, str(tmp_path / "nowhere"), dirs[0]])
    assert "2 span(s) use footage" in str(e.value) and "2 plan(s) not checked" in str(e.value)
    ok_plan = _manifest(tmp_path / "ok", [(ok(1), 1.0, 3.0)])
    cli.main(["check", SH, ok_plan])
    assert "1 plan(s) clean" in capsys.readouterr().out


def test_plan_swap_defaults_to_the_gate(shoot, monkeypatch, tmp_path, capsys):
    from footage_index import __main__ as cli
    c, ids = shoot
    sheets = _swap_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(store, "connect", lambda *a, **k: c)
    monkeypatch.setattr(edit, "get_method", lambda mid: METHOD)
    dup = {c.execute("SELECT thumb FROM shots WHERE id = ?", (i,)).fetchone()[0] for i in ids["dup"]}
    original = _thumbs(c, f"{SH}/o/%")
    cli.main(["plan", "m-test", SH, "--swap", "4"])  # control: no exclude list, the gate still offers a dup take
    assert dup & set(sheets[-1])
    _exclude(monkeypatch, tmp_path, {"takes": ["P5000000", "P5000001"]})
    cli.main(["plan", "m-test", SH, "--swap", "4"])  # the exclude list applies without a flag
    assert sheets[-1] and not dup & set(sheets[-1])
    cli.main(["plan", "m-test", SH, "--swap", "4", "--no-gate"])  # --no-gate opts out
    assert dup & set(sheets[-1])
    cli.main(["plan", "m-test", SH, "--swap", "2", "--state", "original:Cam/V-Log/VG"])  # --state reaches the gate
    assert sheets[-1] and set(sheets[-1]) <= original
    assert "sheet:" in capsys.readouterr().out


@pytest.fixture
def composed(shoot, monkeypatch, tmp_path):
    """The synthetic shoot with METHOD planned (centred trims) under tmp_path, a toolkit stand-in that writes a manifest, and a
    sheet maker that seals each key as rpstills.absheet does (<out>.key/key.json). Its letters run backwards from the input
    order, so a test cannot pass by reading a letter as a position. Returns (c, sheets, fills, d): sheets[slot] is {letter:
    thumb} in candidate order, kept for the test only; no command prints it."""
    import types
    c, _ = shoot
    sheets = {}

    def absheet(paths, out, title=None):
        labels = {k: {"source": os.path.abspath(p)} for k, p in zip("ABCDEFGHI"[:len(paths)][::-1], paths)}
        os.makedirs(out + ".key", exist_ok=True)
        with open(out + ".key/key.json", "w") as f:
            json.dump({"labels": labels}, f)
        sheets[int(title.split()[1])] = {k: v["source"] for k, v in labels.items()}
        return {"labels": labels}

    def to_manifest(method, picks):
        return {"sources": [{"id": f"s{k}", "path": x["source_path"]} for k, x in enumerate(picks, 1)],
                "clips": [{"name": method["id"], "spans": [{"source": f"s{k}", "in": x["in"], "out": x["out"]}
                                                           for k, x in enumerate(picks, 1)]}]}

    toolkit = types.ModuleType("rpresolve")
    toolkit.methods = types.SimpleNamespace(to_manifest=to_manifest)
    monkeypatch.setitem(sys.modules, "rpresolve", toolkit)
    monkeypatch.setattr(edit, "_absheet", absheet)
    monkeypatch.setattr(edit, "EDITS", str(tmp_path))
    fills, d, wrote = edit.plan(c, METHOD, SH, trim_mode="centre")
    assert wrote == "fills.json and manifest.json"
    return c, sheets, fills, d


def _take_of(c, thumb):
    g = edit.build_gate(c, SH)
    return next(g.take[i] for i in range(g.n) if os.path.abspath(g.rows[i]["thumb"]) == thumb)


def _read(d, name):
    with open(os.path.join(d, name)) as f:
        return f.read()


def test_slots_sheet_makes_a_sealed_sheet_per_slot(composed):
    c, sheets, fills, d = composed
    r = edit.slots_sheet(c, METHOD, SH)
    assert r["dir"] == d and sorted(r["sheets"]) == [1, 2, 3, 4] and not r["skipped"]
    for n, s in r["sheets"].items():
        assert s["sheet"] == os.path.join(d, f"swap-{n}", "sheet.jpg") and s["letters"] == sorted(sheets[n])
        assert os.path.exists(os.path.join(d, f"swap-{n}.key", "key.json"))
        mine = {_take_of(c, t) for t in sheets[n].values()}
        assert not mine & {f["take"] for f in fills["slots"] if f["slot"] != n}  # the plan's other takes are not offered
    # the key stays in its sealed files: nothing the call returns names a take
    blob = json.dumps(r)
    assert not any(t in blob for ts in sheets.values() for t in ts.values())
    assert not any(t in blob for f in fills["slots"] for t in (f["relpath"], f["take"]))
    # a slot with fewer than two free takes gets no sheet, keeps its take, and the others still run
    short = {**METHOD, "id": "m-skip", "slots": [METHOD["slots"][0], {**METHOD["slots"][1], "seconds": 99.0}]}
    edit.plan(c, short, SH, trim_mode="centre")
    r = edit.slots_sheet(c, short, SH)
    assert sorted(r["sheets"]) == [1] and list(r["skipped"]) == [2] and "nothing to choose between" in r["skipped"][2]
    with pytest.raises(ValueError, match="slot 2: no sheet"):
        edit.compose(c, short, SH, {"2": "A"})


def test_compose_applies_one_pick_per_slot(composed):
    c, sheets, fills, d = composed
    edit.slots_sheet(c, METHOD, SH)
    letter = lambda n, k: list(sheets[n])[k]  # the k-th candidate's letter on slot n's sheet
    assert len(sheets[4]) == 5
    # Slot 4's last candidate is a candidate only while slot 2 still holds its take: once slot 2 is swapped, that take is freed,
    # ranks higher on slot 4, and pushes it out of the top five. Applied one swap at a time, the second pick is refused.
    picks = {"2": letter(2, 1), "4": letter(4, 4)}
    plan_before = {n: _read(d, n) for n in ("fills.json", "manifest.json")}
    edit.swap(c, METHOD, SH, 2, letter(2, 1), trim_mode="centre")
    with pytest.raises(ValueError, match="no longer a free candidate"):
        edit.swap(c, METHOD, SH, 4, letter(4, 4), trim_mode="centre")
    for n, text in plan_before.items():
        with open(os.path.join(d, n), "w") as f:
            f.write(text)
    r = edit.compose(c, METHOD, SH, {"2": picks["2"].lower(), "4": picks["4"]}, trim_mode="centre")  # the same two picks (one in lower case), read against the plan the sheets were made from
    new = r["fills"]["slots"]
    assert r["wrote"] == "fills.json and manifest.json" and r["dir"] == d
    assert new[1]["thumb"] == list(sheets[2].values())[1] and new[3]["thumb"] == list(sheets[4].values())[4]
    assert new[1]["take"] != fills["slots"][1]["take"] and new[3]["take"] != fills["slots"][3]["take"]
    assert new[0] == fills["slots"][0] and new[2] == fills["slots"][2]  # slots with no pick keep their take
    assert json.loads(_read(d, "fills.json")) == r["fills"] and r["fills"]["state"] == fills["state"]
    spans = json.loads(_read(d, "manifest.json"))["clips"][0]["spans"]  # the manifest follows the picks
    srcs = {x["id"]: x["path"] for x in json.loads(_read(d, "manifest.json"))["sources"]}
    assert [srcs[sp["source"]] for sp in spans] == [f["path"] for f in new]
    assert [(sp["in"], sp["out"]) for sp in spans] == [(f["in_s"], f["out_s"]) for f in new]


def test_compose_refuses_and_writes_nothing(composed, monkeypatch, tmp_path):
    c, sheets, fills, d = composed
    edit.slots_sheet(c, METHOD, SH)
    letter = lambda n, k: list(sheets[n])[k]
    snap = lambda: {n: _read(d, n) for n in ("fills.json", "manifest.json")}
    before = snap()

    def refused(picks, why):
        with pytest.raises(ValueError, match=why):
            edit.compose(c, METHOD, SH, picks, trim_mode="centre")
        assert snap() == before

    shared = next(t for t in sheets[2].values() if t in sheets[4].values())  # one take on two slots' sheets
    refused({"2": next(k for k, t in sheets[2].items() if t == shared), "4": next(k for k, t in sheets[4].items() if t == shared)},
            "slots 2 and 4 picked the same take")
    refused({"1": "Z"}, "slot 1: no letter Z on that sheet")
    refused({"9": "A"}, "the method has slots 1 to 4")
    for bad in ({}, {"x": "A"}, ["A"], {"1": "A", "01": "B"}):
        refused(bad, "picks: expected")
    # a take the exclude list rules out after the sheet was made: refused, and the good pick beside it is not written either
    _exclude(monkeypatch, tmp_path, {"takes": [_take_of(c, list(sheets[2].values())[1])]})
    refused({"1": letter(1, 1), "2": letter(2, 1)}, "slot 2 .: that take is on the shoot's exclude list")
    assert edit.compose(c, METHOD, SH, {"1": letter(1, 1)}, trim_mode="centre")["fills"]["slots"][0]["thumb"] == list(sheets[1].values())[1]
    shutil.rmtree(os.path.join(d, "swap-3.key"))
    before = snap()
    refused({"3": "A"}, "slot 3: no sheet")


def test_slots_sheet_applies_the_exclude_list(composed, monkeypatch, tmp_path):
    c, sheets, fills, d = composed
    edit.slots_sheet(c, METHOD, SH)
    gone = _take_of(c, list(sheets[2].values())[1])
    assert gone in {_take_of(c, t) for s in sheets.values() for t in s.values()}  # control: with no list it is offered
    _exclude(monkeypatch, tmp_path, {"takes": [gone]})
    assert sorted(edit.slots_sheet(c, METHOD, SH)["sheets"]) == [1, 2, 3, 4]
    assert gone not in {_take_of(c, t) for s in sheets.values() for t in s.values()}  # now on no sheet
    edit.slots_sheet(c, METHOD, SH, gate=False)  # and gate=False still opts out of it
    assert gone in {_take_of(c, t) for s in sheets.values() for t in s.values()}


def test_compose_judges_a_take_not_a_file(composed, monkeypatch):
    """Slot 4's pick is another file of the take picked on slot 2: one take, so picking both is picking it twice."""
    c, sheets, fills, d = composed
    edit.slots_sheet(c, METHOD, SH)
    l2, l4 = list(sheets[2])[1], next(k for k, t in sheets[4].items() if t not in sheets[2].values())
    take2, thumb4, real = _take_of(c, sheets[2][l2]), sheets[4][l4], edit._slot_cands

    def cands(*a):
        return [{**r, "take": take2} if os.path.abspath(r["thumb"]) == thumb4 else r for r in real(*a)]

    monkeypatch.setattr(edit, "_slot_cands", cands)
    before = {n: _read(d, n) for n in ("fills.json", "manifest.json")}
    with pytest.raises(ValueError, match="slots 2 and 4 picked the same take"):
        edit.compose(c, METHOD, SH, {"2": l2, "4": l4}, trim_mode="centre")
    assert {n: _read(d, n) for n in before} == before


def test_compose_allows_one_take_twice_when_the_method_reuses(composed):
    c, sheets, _, _ = composed
    rm = {**METHOD, "id": "m-reuse", "rules": {"reuse": True}}
    edit.plan(c, rm, SH, trim_mode="centre")
    edit.slots_sheet(c, rm, SH)
    shared = next(t for t in sheets[2].values() if t in sheets[4].values())
    letter = lambda n: next(k for k, t in sheets[n].items() if t == shared)
    r = edit.compose(c, rm, SH, {"2": letter(2), "4": letter(4)}, trim_mode="centre")
    assert r["fills"]["slots"][1]["take"] == r["fills"]["slots"][3]["take"] == _take_of(c, shared)


def test_slots_sheet_and_compose_follow_the_plans_colour_state(composed, monkeypatch):
    """The gate is built once per call, from the state given, else the plan's (the synthetic shoot has too few original takes
    to sheet a whole method from, so this watches the state the gate is asked for; test_swap_draws_from_the_state... shows the
    state decides the takes)."""
    c, sheets, fills, d = composed
    state, own, real, seen = "original:Cam/V-Log/VG", fills["state"], edit.build_gate, []
    monkeypatch.setattr(edit, "build_gate", lambda c_, prefix, st=None: seen.append(st) or real(c_, prefix, st))
    assert own and own != state
    edit.slots_sheet(c, METHOD, SH)
    assert seen == [own]  # none given: the plan's
    edit.slots_sheet(c, METHOD, SH, state=state)
    assert seen[1:] == [state]  # an explicit state wins
    edit.slots_sheet(c, METHOD, SH)  # the plan's sheets again
    edit.compose(c, METHOD, SH, {"1": list(sheets[1])[1]}, trim_mode="centre")
    assert seen[-1] == own
    with pytest.raises(ValueError, match="no longer a free candidate"):  # the original state has no such take for slot 1
        edit.compose(c, METHOD, SH, {"1": list(sheets[1])[1]}, trim_mode="centre", state=state)
    assert seen[-1] == state and len(seen) == 5


def test_compose_trims_as_asked(composed, monkeypatch):
    c, sheets, _, _ = composed
    edit.slots_sheet(c, METHOD, SH)
    calls = []
    monkeypatch.setattr(trim, "span", lambda r, length, handle=0.0: calls.append(length) or (1.0, 1.0 + length, "stub curve"))
    k = list(sheets[2])[1]
    s = edit.compose(c, METHOD, SH, {"2": k}, trim_mode="centre")["fills"]["slots"][1]
    assert not calls and s["trim"].startswith("centred") and s["in_s"] != 1.0  # --trim centre never reads the curve
    s = edit.compose(c, METHOD, SH, {"2": k})["fills"]["slots"][1]  # motion is the default
    assert calls == [1.5] and s["trim"] == "stub curve" and (s["in_s"], s["out_s"]) == (1.0, 2.5)


def test_swap_and_compose_keep_the_beat_laid_cut(composed, monkeypatch):
    """A plan made with --beats has slot lengths and music cuts off the nominal; a pick takes the slot's, not the method's."""
    c, sheets, _, d = composed
    beats = [round(0.5 * k, 3) for k in range(1, 40)]
    nominal = [edit.slot_seconds(METHOD, s) for s in METHOD["slots"]]
    plan = edit.plan(c, METHOD, SH, trim_mode="centre", beats=beats)[0]["slots"]
    assert [s["seconds"] for s in plan] != nominal and all(s["cut_s"] for s in plan)
    asked, real = [], edit.candidates
    monkeypatch.setattr(edit, "candidates", lambda *a, **k: asked.append(k.get("seconds")) or real(*a, **k))
    edit.slots_sheet(c, METHOD, SH)
    assert asked == [s["seconds"] for s in plan]  # a take must fit the length the plan laid
    kept = lambda f, old: (f["seconds"], f["cut_s"]) == (old["seconds"], old["cut_s"]) and f["thumb"] != old["thumb"] \
        and abs(f["out_s"] - f["in_s"] - f["seconds"]) < 1e-6
    other = lambda n: next(k for k, t in sheets[n].items() if t != os.path.abspath(plan[n - 1]["thumb"]))  # a take the plan does not hold
    assert kept(edit.swap(c, METHOD, SH, 3, other(3), trim_mode="centre")["fills"]["slots"][2], plan[2])
    edit.plan(c, METHOD, SH, trim_mode="centre", beats=beats)  # start again from the beat-laid plan
    edit.slots_sheet(c, METHOD, SH)
    t3 = _take_of(c, sheets[3][other(3)])
    l2 = next(k for k, t in sheets[2].items() if t != os.path.abspath(plan[1]["thumb"]) and _take_of(c, t) != t3)
    new = edit.compose(c, METHOD, SH, {"2": l2, "3": other(3)}, trim_mode="centre")["fills"]["slots"]
    assert kept(new[1], plan[1]) and kept(new[2], plan[2]) and new[0] == plan[0] and new[3] == plan[3]
    seconds = [f["seconds"] for f in new]
    assert [s["seconds"] for s in json.loads(_read(d, "method.json"))["slots"]] == seconds  # method.json follows the plan's cuts
    spans = json.loads(_read(d, "manifest.json"))["clips"][0]["spans"]
    assert [round(sp["out"] - sp["in"], 6) for sp in spans] == seconds  # and so does the manifest


def test_per_slot_sheets_are_for_short_methods(composed):
    c, _, _, _ = composed
    long = {**METHOD, "id": "m-long", "slots": (METHOD["slots"] * 3)[:11]}
    edit._short({**METHOD, "slots": (METHOD["slots"] * 3)[:10]})  # ten is allowed
    for call in (lambda: edit.slots_sheet(c, long, SH), lambda: edit.compose(c, long, SH, {"1": "A"})):
        with pytest.raises(ValueError, match="m-long has 11 slots; per-slot sheets are for 10 or fewer"):
            call()
    with pytest.raises(ValueError, match="no plan at"):
        edit.slots_sheet(c, {**METHOD, "id": "m-unplanned"}, SH)


def test_cli_slots_sheet_and_compose(composed, monkeypatch, tmp_path, capsys):
    from footage_index import __main__ as cli
    c, sheets, fills, d = composed
    monkeypatch.setattr(store, "connect", lambda *a, **k: c)
    monkeypatch.setattr(edit, "get_method", lambda mid: METHOD)
    cli.main(["slots-sheet", "m-test", SH])
    out = capsys.readouterr().out
    assert "slot 4:" in out and "letters" in out and "4 sheet(s)" in out
    assert not any(t in out for ts in sheets.values() for t in ts.values())  # the key is not printed
    picks = tmp_path / "picks.json"
    picks.write_text(json.dumps({"1": list(sheets[1])[1]}))
    cli.main(["compose", "m-test", SH, str(picks), "--trim", "centre"])
    assert "wrote fills.json and manifest.json" in capsys.readouterr().out
    assert json.loads(_read(d, "fills.json"))["slots"][0]["thumb"] == list(sheets[1].values())[1]
    _exclude(monkeypatch, tmp_path, {"takes": [_take_of(c, list(sheets[2].values())[1])]})
    picks.write_text(json.dumps({"2": list(sheets[2])[1]}))
    with pytest.raises(SystemExit, match="exclude list"):
        cli.main(["compose", "m-test", SH, str(picks), "--trim", "centre"])


def _variant(d, name="v2-top", method=None):
    """Move the plan folder d under <edits>/<shoot>-<method>-variants/<name>/, as plan --variants writes it, so the plain
    <edits>/<shoot>-<method>/ no longer exists."""
    root = os.path.join(edit.EDITS, f"{edit.slug(SH)}-{(method or METHOD)['id']}-variants")
    os.makedirs(root, exist_ok=True)
    return shutil.move(d, os.path.join(root, name))


def test_swap_sheet_and_compose_act_on_a_variants_folder(composed, monkeypatch, tmp_path):
    c, sheets, fills, d = composed
    v = _variant(d)
    with pytest.raises(ValueError, match="no plan at"):  # the old resolution is still the default
        edit.slots_sheet(c, METHOD, SH)
    r = edit.slots_sheet(c, METHOD, SH, plan_dir=v)
    assert r["dir"] == v and sorted(r["sheets"]) == [1, 2, 3, 4] and os.path.exists(os.path.join(v, "swap-3.key", "key.json"))
    assert not os.path.exists(d)
    letter = lambda n, k: list(sheets[n])[k]
    new = edit.compose(c, METHOD, SH, {"1": letter(1, 1)}, trim_mode="centre", plan_dir=v)
    assert new["dir"] == v and new["fills"]["slots"][0]["thumb"] == list(sheets[1].values())[1]
    assert json.loads(_read(v, "fills.json")) == new["fills"] and "manifest.json" in new["wrote"]
    assert edit.swap(c, METHOD, SH, 2, letter(2, 1), trim_mode="centre", plan_dir=v)["dir"] == v
    assert not os.path.exists(d)  # nothing was written to the plain folder
    # the exclude list applies in a variants folder as it does in a plain one
    _exclude(monkeypatch, tmp_path, {"takes": [_take_of(c, list(sheets[3].values())[1])]})
    with pytest.raises(ValueError, match="exclude list"):
        edit.compose(c, METHOD, SH, {"3": letter(3, 1)}, trim_mode="centre", plan_dir=v)
    with pytest.raises(ValueError, match="holds a plan of other-method"):  # a variants folder of another method
        with open(os.path.join(v, "fills.json"), "w") as f:
            json.dump({**fills, "method": "other-method"}, f)
        edit.slots_sheet(c, METHOD, SH, plan_dir=v)


def test_variant_dir_resolves_vk_under_the_method_variants_folder(composed):
    c, sheets, fills, d = composed
    v1, v10 = _variant(d, "v1-top"), os.path.join(os.path.dirname(d), f"{edit.slug(SH)}-{METHOD['id']}-variants", "v10-night")
    os.makedirs(v10)
    assert edit.variant_dir(METHOD["id"], SH, "v1") == v1  # v1 does not take in v10
    assert edit.variant_dir(METHOD["id"], SH, "v10") == v10 and edit.variant_dir(METHOD["id"], SH, "v1-top") == v1
    with pytest.raises(ValueError, match=r"no variant 'v2'.*v1-top, v10-night"):
        edit.variant_dir(METHOD["id"], SH, "v2")
    with pytest.raises(ValueError, match="no variant 'v1'"):
        edit.variant_dir("m-none", SH, "v1")
    os.makedirs(os.path.join(os.path.dirname(v1), "v1-dusk"))
    with pytest.raises(ValueError, match="several variant 'v1'"):
        edit.variant_dir(METHOD["id"], SH, "v1")


def test_variant_dir_ignores_backups_and_files(composed):
    c, sheets, fills, d = composed
    v1 = _variant(d, "v1-top")
    root = os.path.dirname(v1)
    shutil.copytree(v1, v1 + ".bak-old")  # the real variants folders hold dated backups of a plan
    for stray in ("variants.json", "variants.json.bak-old", "v1-notes"):
        open(os.path.join(root, stray), "w").close()
    assert edit.variant_dir(METHOD["id"], SH, "v1") == v1 and edit.variant_dir(METHOD["id"], SH, "v1-top") == v1
    with pytest.raises(ValueError) as e:
        edit.variant_dir(METHOD["id"], SH, "v2")
    assert "v1-top.bak-old" in str(e.value) and "variants.json" not in str(e.value) and "v1-notes" not in str(e.value)


def test_cli_variant_and_plan_dir(composed, monkeypatch, tmp_path, capsys):
    from footage_index import __main__ as cli
    c, sheets, fills, d = composed
    monkeypatch.setattr(store, "connect", lambda *a, **k: c)
    monkeypatch.setattr(edit, "get_method", lambda mid: METHOD)
    v = _variant(d, "v3-top")
    cli.main(["slots-sheet", "m-test", SH, "--variant", "v3"])
    assert "4 sheet(s)" in capsys.readouterr().out
    assert os.path.exists(os.path.join(v, "swap-1.key", "key.json"))
    picks = tmp_path / "picks.json"
    picks.write_text(json.dumps({"1": list(sheets[1])[1]}))
    cli.main(["compose", "m-test", SH, str(picks), "--trim", "centre", "--plan-dir", v])
    assert f"to {v}" in capsys.readouterr().out
    cli.main(["plan", "m-test", SH, "--swap", "2", "--variant", "v3"])
    assert "sheet:" in capsys.readouterr().out and os.path.exists(os.path.join(v, "swap-2.key", "key.json"))
    for argv, why in ((["plan", "m-test", SH, "--variant", "v3"], "--variant and --plan-dir pick the plan folder for --swap"),
                      (["compose", "m-test", SH, str(picks), "--variant", "v9"], "no variant 'v9'"),
                      (["slots-sheet", "m-test", SH, "--plan-dir", str(tmp_path / "nowhere")], "no plan at")):
        with pytest.raises(SystemExit, match=why):
            cli.main(argv)
    with pytest.raises(SystemExit):  # one or the other
        cli.main(["slots-sheet", "m-test", SH, "--variant", "v3", "--plan-dir", v])
    # an empty value never falls back to the default folder; a file is not a plan
    (tmp_path / "afile").write_text("x")
    for argv, why in ((["slots-sheet", "m-test", SH, "--variant", ""], "no variant ''"),
                      (["slots-sheet", "m-test", SH, "--plan-dir", ""], "empty plan folder"),
                      (["plan", "m-test", SH, "--swap", "1", "--plan-dir", ""], "empty plan folder"),
                      (["slots-sheet", "m-test", SH, "--plan-dir", str(tmp_path / "afile")], "no plan at"),
                      # plan --variants builds plans; the picker flags are for --swap only
                      (["plan", "m-test", SH, "--variants", "2", "--variant", "v3"], "pick the plan folder for --swap"),
                      (["plan", "m-test", SH, "--variants", "2", "--plan-dir", v], "pick the plan folder for --swap"),
                      (["plan", "m-test", SH, "--variants", "2", "--swap", "1", "--variant", "v3"], "pick the plan folder for --swap")):
        with pytest.raises(SystemExit, match=why):
            cli.main(argv)


def test_check_covers_the_manifests_music_file(shoot, monkeypatch, tmp_path, capsys):
    from footage_index import __main__ as cli
    c, ids = shoot
    ok = lambda i: f"{SH}/g/clip-{i:03d}_P{1900000 + i}.mov"
    song = tmp_path / "Night Drive.wav"
    song.write_bytes(b"x")
    locked = tmp_path / "locked.wav"
    locked.write_bytes(b"x")
    locked.chmod(0)
    _exclude(monkeypatch, tmp_path, {"takes": ["P1900005"]})

    def plan(name, music):
        d = _manifest(tmp_path / name, [(ok(1), 1.0, 3.0)])
        with open(os.path.join(d, "manifest.json")) as f:
            m = json.load(f)
        if music is not None:
            m["music"] = music
        with open(os.path.join(d, "manifest.json"), "w") as f:
            json.dump(m, f)
        return d

    assert edit.check(c, SH, [plan("none", None), plan("fine", {"path": str(song), "in": 0.0})]) == []
    gone = edit.check(c, SH, [plan("gone", {"path": str(tmp_path / "missing.wav"), "in": 0})])
    assert len(gone) == 1 and "music" in gone[0] and "missing.wav: not on this machine" in gone[0]
    if os.geteuid() != 0:
        assert "not readable" in edit.check(c, SH, [plan("locked", {"path": str(locked), "in": 0})])[0]
    # an exclude list that names the music file, by stem, basename or path suffix, ignoring case
    for take in ("night drive", "NIGHT DRIVE.WAV", f"{tmp_path.name}/Night Drive"):
        _exclude(monkeypatch, tmp_path, {"takes": [take]})
        hit = edit.check(c, SH, [plan("hit", {"path": str(song), "in": 0})])
        assert len(hit) == 1 and f"excluded take {take}" in hit[0] and "Night Drive.wav" in hit[0]
    _exclude(monkeypatch, tmp_path, {"takes": ["P1900005"]})
    odd = edit.check(c, SH, [plan("odd", "song.wav"), plan("odd2", {"in": 0})])
    assert len(odd) == 2 and all("music has no path" in x and x.endswith("not checked") for x in odd)
    # only the path is read, as the toolkit's exclude_music_matches does: "in" may be missing
    assert edit.check(c, SH, [plan("noin", {"path": str(song)})]) == []
    # the spans and the music are both reported, and the CLI counts the music apart from the spans
    monkeypatch.setattr(store, "connect", lambda *a, **k: c)
    both = _manifest(tmp_path / "both", [(ok(5), 0.0, 2.0)])
    with open(os.path.join(both, "manifest.json")) as f:
        m = json.load(f)
    m["music"] = {"path": str(tmp_path / "missing.wav"), "in": 0}
    with open(os.path.join(both, "manifest.json"), "w") as f:
        json.dump(m, f)
    with pytest.raises(SystemExit) as e:
        cli.main(["check", SH, both])
    assert "1 span(s) use footage" in str(e.value) and "1 music file problem(s)" in str(e.value) and "0 plan(s) not checked" in str(e.value)
    locked.chmod(0o600)


def test_trim_rules():
    ts = np.arange(1, 121) / trim.FPS   # 10 s of curve
    still = np.full(len(ts), 0.01)
    peak = still.copy(); peak[(ts > 3.9) & (ts < 4.1)] = 0.05; peak[(ts > 6.0) & (ts < 6.3)] = 0.6
    a, b, why = trim.choose(ts, peak, 3.0, 0.25, 10.0)
    assert abs((a + b) / 2 - 4.0) < 0.1 and "peak" in why  # the subject's apex, not the camera whip at 6 s
    whip = np.full(len(ts), 0.3); whip[(ts > 5.0) & (ts < 5.2)] = 0.1
    a, b, why = trim.choose(ts, whip, 3.0, 0.25, 10.0)
    assert abs((a + b) / 2 - 5.1) < 0.15 and "least smeared" in why  # a window all over the cap takes its stillest frame
    move = still.copy(); move[(ts > 0.3) & (ts <= 4)] = 0.3; move[(ts > 6.2) & (ts < 6.8)] = 0.3
    a, b, why = trim.choose(ts, move, 3.0, 1.5, 10.0)
    assert a >= 3.99 and b <= 6.2 and "settled" in why  # opens after the move, ends before the next smear
    edge = still.copy(); edge[ts < 0.3] = 0.9
    a, b, _ = trim.choose(ts, edge, 0.1, 0.5, 10.0)
    assert a >= trim.EDGE
    late = still.copy(); late[ts > 9.9] = 0.9
    a, b, _ = trim.choose(ts, late, 9.9, 0.5, 10.0)
    assert b <= 10.0 - trim.EDGE
    with pytest.raises(ValueError):
        trim.choose(ts, still, 1.0, 9.8, 10.0)  # no room for 0.2 s margins
    beats = [0.5 * k for k in range(1, 20)]
    assert trim.beat_units(0.25, 120) == 0.5 and trim.beat_units(0.2, 150) == 0.5 and trim.beat_units(4.0, 120) == 8
    assert trim.beat_units(0.05, 120) == 0.5  # never under half a beat
    with pytest.raises(ValueError, match="at least two"):
        trim.fit_grid([1.0], 120)
    assert trim.fit_grid(beats, 120) == beats and trim.fit_grid(beats, 70) == beats[::2] and len(trim.fit_grid(beats, 230)) == 2 * len(beats) - 1
    assert trim.beat_time(beats, 1.5) == 1.25 and trim.beat_time(beats, -1) == 0.0 and trim.beat_time(beats, 19) == 10.0
    assert trim.continue_span(4.0, 1.0, 10.0)[:2] == (4.0, 5.0) and trim.continue_span(9.5, 1.0, 10.0) is None


def test_trim_curve_from_video(monkeypatch):
    path = os.path.join(TMP, "still-then-moving.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=gray:s=320x180:d=3:r=24",
                    "-f", "lavfi", "-i", "testsrc2=s=320x180:d=3:r=24", "-filter_complex", "[0][1]concat=n=2:v=1", path], check=True)
    ts, m = trim.curve(path, 0.0, 6.0)
    assert m[ts < 2.8].max() < 0.01 < m[ts > 3.3].mean()
    monkeypatch.setattr(trim, "ROOTS", [TMP])
    a, b, why = trim.span({"relpath": "still-then-moving.mp4", "duration": 6.0, "t": 3.0}, 1.0)
    assert b <= 3.1 and "settled" in why  # a hold on the still half, not the moving one


def test_role_names_the_probe_before_the_query():
    """A face slot reads the face probe even when its query says 'wide'; a role no row names reads its query."""
    from footage_index import edit as E, gate as G

    class Fake:
        n, motion_z = 3, np.zeros(3)

        def zt(self, text):
            return {G.SIZES["close"]: np.array([2.0, 0.0, -1.0]), G.SIZES["wide"]: np.array([-1.0, 0.0, 2.0]),
                    G.SUBJECTS["figure"]: np.array([0.0, 2.0, 0.0])}.get(text, np.zeros(3))

    fit, z = E.role_fit(Fake(), {"role": "face", "query": "a person in front of a wide place"})
    assert z.argmax() == 0 and fit[0] == E.ROLE_W * 2.0
    assert E.role_fit(Fake(), {"role": "figure", "query": "one person small in a wide view"})[1].argmax() == 1
    assert E.role_fit(Fake(), {"role": "b-roll", "query": "a wide view of the valley"})[1].argmax() == 2
    assert E.role_fit(Fake(), {"role": "b-roll", "query": "nothing a row names"})[1] is None
    # A face slot also wants a detected face, while one is left; other roles do not.
    Fake.face = np.array([0.0, 0.05, 0.05])
    every = np.ones(3, bool)
    assert E.role_mask(Fake(), {"role": "face"}, z, every).tolist() == [True, False, False]  # floor first, face has none left
    assert E.role_mask(Fake(), {"role": "face"}, None, every).tolist() == [False, True, True]
    assert E.role_mask(Fake(), {"role": "figure"}, None, every).all()


def test_trim_hold_penalises_mid_pan(monkeypatch):
    ts = np.arange(1, 121) / trim.FPS   # 10 s of curve
    pan = np.full(len(ts), 0.01); pan[(ts > 4.5) & (ts < 5.5)] = 0.15   # a pan right at the keyframe, inside any hold there
    a, b, _ = trim.choose(ts, pan, 5.0, 3.0, 10.0)
    assert not ((ts > a + trim.SETTLE_S) & (ts < b - trim.SETTLE_S) & (pan > 0.1)).any()   # the still window beats the mid-pan one
    monkeypatch.setattr(trim, "MID_W", 0.0)
    a0, b0, _ = trim.choose(ts, pan, 5.0, 3.0, 10.0)
    assert ((ts > a0 + trim.SETTLE_S) & (ts < b0 - trim.SETTLE_S) & (pan > 0.1)).any()    # without the penalty the pan window wins
    monkeypatch.undo()
    # busy handheld everywhere, except one stretch that is still inside and shakes only at its edges: edge motion does not lose
    m = np.full(len(ts), 0.06)
    m[(ts > 3.3) & (ts <= 4.7)] = 0.01
    m[(ts > 3.0) & (ts <= 3.3)] = 0.09; m[(ts > 4.7) & (ts <= 5.0)] = 0.09
    a, b, _ = trim.choose(ts, m, 4.0, 2.0, 10.0)
    assert 3.0 <= a <= 3.4   # lands on the still stretch, edge motion and all, not on the busy 0.06 elsewhere


PLATE = lambda effect, seconds, fps=24: trim.plate_need(effect, seconds, fps)


def test_plate_constants_match_the_toolkit_and_need_includes_margin():
    assert trim.PLATE_FRAMES == {"scale-window": 37, "crop-window": 26}  # windowplate.PLATE_FRAMES in the toolkit
    assert abs(PLATE("scale-window", 4.0) - (37 / 24 + trim.PLATE_MARGIN)) < 1e-9
    assert abs(PLATE("crop-window", 4.0) - (26 / 24 + trim.PLATE_MARGIN)) < 1e-9
    assert abs(PLATE("crop-window", 0.25) - (6 / 24 + trim.PLATE_MARGIN)) < 1e-9  # a short span never gets to full frame: its own frames
    assert PLATE("zoom-push", 4.0) == 0.0 and PLATE(None, 4.0) == 0.0
    w = {**METHOD, "fps": 24, "slots": [METHOD["slots"][0], {**METHOD["slots"][1], "effect": "scale-window"}]}
    assert edit._handle(w, 1) == PLATE("scale-window", 1.5) and edit._handle(w, 0) == 0.0 and edit._handle(w, 2) == 0.0


def test_trim_leaves_the_window_handle():
    ts = np.arange(1, 121) / trim.FPS
    still = np.full(len(ts), 0.01)
    h = PLATE("scale-window", 3.0)  # 1.64 s
    a, b, _ = trim.choose(ts, still, 9.5, 1.0, 10.0)
    assert b > 10.0 - h  # without a handle the hold runs to the 0.2 s edge, the case the draft farm refused
    a, b, _ = trim.choose(ts, still, 9.5, 1.0, 10.0, h)
    assert b <= 10.0 - h + 1e-9 and abs((b - a) - 1.0) < 1e-6
    a, b, _ = trim.choose(ts, still, 9.5, 0.25, 10.0, h)  # a burst keeps it too
    assert b <= 10.0 - h + 1e-9
    with pytest.raises(ValueError):
        trim.choose(ts, still, 1.0, 8.5, 10.0, h)  # the take cannot give both the slot and the plate
    assert trim.continue_span(7.6, 1.0, 10.0) and trim.continue_span(7.6, 1.0, 10.0, h) is None


def test_fills_leave_the_window_handle(shoot, monkeypatch, tmp_path):
    c, _ = shoot
    seen = []
    monkeypatch.setattr(trim, "span", lambda r, length, handle=0.0: seen.append((length, handle)) or (1.0, 1.0 + length, "stub"))
    w = {**METHOD, "fps": 24, "slots": [METHOD["slots"][0], {**METHOD["slots"][1], "effect": "scale-window"},
                                        {**METHOD["slots"][3], "effect": "crop-window"}]}
    f = edit.fill_slots(c, w, SH + "/", gate=G.load(c, SH))
    assert [h for _, h in seen] == [PLATE("scale-window", 1.5), PLATE("crop-window", 1.0), 0.0]
    assert not any(s.get("plate_short") for s in f["slots"])
    # centred trim leaves it too, and a take that cannot give it is marked
    r = {"duration": 6.0, "t_in": 5.0, "t_out": 6.0, "t": 5.5}
    h = PLATE("scale-window", 1.5)
    assert edit.trim(r, 1.0)[1] == 6.0 and edit.trim(r, 1.0, h)[1] <= 6.0 - h + 1e-9
    base = {"id": 1, "thumb": "t", "sim": 0, "fps": 30, "relpath": "x.mov", "t": 5.2}
    ok = edit.fill(w, w["slots"][0], 1, {**base, **r}, None, 1.0, h)
    assert "plate_short" not in ok and ok["out_s"] <= 6.0 - h + 1e-9
    short = edit.fill(w, w["slots"][0], 1, {**base, "duration": 1.2, "t_in": 0.0, "t_out": 1.2}, None, 1.0, h)
    assert "plate plus 0.1s margin" in short["plate_short"] and "refuses below the plate" in short["plate_short"]
    nolen = edit.fill(w, w["slots"][0], 1, {**base, "duration": None, "t_in": 0.0, "t_out": 1.2}, (0.0, 1.0, "x"), 1.0, h)
    assert "plate_short" not in nolen  # no length in the index: nothing to compare, so no false mark
    note = edit._write(w, {"slots": [short], "shoot": SH, "method": w["id"]}, str(tmp_path / "short"))
    assert "slot(s) 1 leave less than the window plate plus 0.1s margin" in note and "only below the plate itself" in note


def test_swap_candidates_and_picks_keep_the_handle(shoot, monkeypatch):
    c, _ = shoot
    w = {**METHOD, "fps": 24, "slots": [METHOD["slots"][0], {**METHOD["slots"][1], "effect": "scale-window"}]}
    seen = []
    monkeypatch.setattr(edit, "candidates", lambda c, m, s, p, **kw: seen.append(kw["seconds"]) or [])
    edit._slot_cands(c, w, 1, SH, {"slots": [{"slot": 1, "seconds": 2.0}, {"slot": 2, "seconds": 1.5}]}, False)
    assert seen == [2.0 + PLATE("scale-window", 1.5)]


def _with_effects(d, effects, fps=24):
    path = os.path.join(d, "manifest.json")
    m = json.load(open(path))
    m["fps"], m["effects"] = fps, [{"span_index": i, "effect": e} for i, e in effects]
    json.dump(m, open(path, "w"))
    return d


def test_check_fails_a_window_the_previous_shot_cannot_play_under(shoot, monkeypatch, tmp_path):
    c, _ = shoot
    ok = lambda i: f"{SH}/g/clip-{i:03d}_P{1900000 + i}.mov"  # 12 s takes
    _exclude(monkeypatch, tmp_path, {})
    build = lambda name, out, effect, span_s=2.0: _with_effects(
        _manifest(tmp_path / name, [(ok(1), out - 2, out), (ok(2), 0.0, span_s)]), [(1, effect)])
    # scale-window: 37 frames = 1.54 s; the draft farm's test is 0.02 s under that
    assert edit.check(c, SH, [build("fine", 10.0, "scale-window")]) == []  # 2.0 s left
    assert edit.check(c, SH, [build("edge", 10.45, "scale-window")]) == []  # 1.55 s left
    line, = edit.check(c, SH, [build("tight", 11.0, "scale-window")])  # 1.0 s left: the case the farm refused
    assert "span 2 scale-window" in line and "1.00s after its out point" in line and "needs 1.54s" in line
    assert edit.check(c, SH, [build("crop-ok", 10.9, "crop-window")]) == []  # 26 frames = 1.08 s, 1.1 s left
    assert len(edit.check(c, SH, [build("crop-bad", 11.1, "crop-window")])) == 1
    assert edit.check(c, SH, [build("short-span", 11.6, "crop-window", 0.25)]) == []  # a 6-frame span needs only 6 frames
    assert edit.check(c, SH, [_with_effects(_manifest(tmp_path / "none", [(ok(1), 9.0, 11.0), (ok(2), 0.0, 2.0)]), [(1, "zoom-push")])]) == []
    assert edit.check(c, SH, [build("a", 11.0, "scale-window"), build("fine", 10.0, "scale-window")]) == [line.replace("tight", "a")]


def test_check_names_the_unfilled_slots_of_a_fills_only_plan(shoot, monkeypatch, tmp_path, capsys):
    from footage_index import __main__ as cli
    c, _ = shoot
    _exclude(monkeypatch, tmp_path, {})
    d = tmp_path / "short-plan"
    os.makedirs(d)
    json.dump({"slots": [{"slot": 1, "path": "/x"}, {"slot": 2, "path": None, "unfilled": "no unused take long enough"},
                         {"slot": 3, "path": None, "unfilled": "x"}]}, open(d / "fills.json", "w"))
    line, = edit.check(c, SH, [str(d)])
    assert "slot(s) 2, 3 unfilled" in line and "not a complete plan" in line and line.endswith("not checked")
    with pytest.raises(SystemExit) as e:  # non-zero, and counted as a plan not checked
        cli.main(["check", SH, str(d)])
    assert "slot(s) 2, 3 unfilled" in str(e.value) and "1 plan(s) not checked" in str(e.value)
    os.remove(d / "fills.json")
    assert "cannot read manifest.json" in edit.check(c, SH, [str(d)])[0]  # no fills.json: the old message


def test_rerun_into_a_fixed_folder_cannot_leave_a_stale_manifest_that_passes(shoot, monkeypatch, tmp_path):
    c, _ = shoot
    _exclude(monkeypatch, tmp_path, {})
    ok = lambda i: f"{SH}/g/clip-{i:03d}_P{1900000 + i}.mov"
    d = _manifest(tmp_path / "plan", [(ok(1), 0.0, 2.0), (ok(2), 0.0, 2.0)])
    assert edit.check(c, SH, [d]) == []  # an old complete plan
    json.dump({"slots": [{"slot": 1, "path": "/x", "seconds": 2.0}, {"slot": 2, "path": None, "unfilled": "x", "seconds": 2.0}]},
              open(os.path.join(d, "fills.json"), "w"))  # the new run left slot 2 unfilled but the manifest is still here
    line, = edit.check(c, SH, [d])
    assert "slot(s) 2 unfilled" in line and line.endswith("not checked")
    # the planner removes it: a fills-only write deletes the manifest of the run before
    w = {**METHOD, "slots": METHOD["slots"][:2]}
    fills = {"fills": 1, "method": w["id"], "shoot": SH, "slots": [{"slot": 1, "role": "r", "seconds": 2.0, "path": "/x"},
                                                                    {"slot": 2, "role": "r", "seconds": 1.5, "path": None}]}
    assert "fills.json only" in edit._write_files(w, fills, d)
    assert not os.path.exists(os.path.join(d, "manifest.json")) and os.path.exists(os.path.join(d, "fills.json"))
    # a manifest of another size than fills.json's slots is stale too, with every slot filled
    d2 = _manifest(tmp_path / "plan2", [(ok(1), 0.0, 2.0), (ok(2), 0.0, 2.0)])
    json.dump({"slots": [{"slot": n, "path": "/x"} for n in (1, 2, 3)]}, open(os.path.join(d2, "fills.json"), "w"))
    line, = edit.check(c, SH, [d2])
    assert "2 spans but fills.json has 3 slots" in line and line.endswith("not checked")
    json.dump({"slots": [{"slot": n, "path": "/x"} for n in (1, 2)]}, open(os.path.join(d2, "fills.json"), "w"))
    assert edit.check(c, SH, [d2]) == []


def test_check_rounds_the_out_frame_like_the_draft_farm(shoot, monkeypatch, tmp_path):
    c, _ = shoot
    ok = lambda i: f"{SH}/g/clip-{i:03d}_P{1900000 + i}.mov"
    _exclude(monkeypatch, tmp_path, {})
    c.execute("UPDATE clips SET duration = 11.51 WHERE relpath = ?", (ok(1),))
    build = lambda name, dur: (c.execute("UPDATE clips SET duration = ? WHERE relpath = ?", (dur, ok(1))),
                               _with_effects(_manifest(tmp_path / name, [(ok(1), 7.98333, 9.98333), (ok(2), 0.0, 2.0)]),
                                             [(1, "scale-window")]))[1]
    # out 9.98333 s is frame 240 at 24 fps (10.0 s): the farm sees 1.51 s left of 1.5208 needed and refuses; unrounded it is 1.527
    line, = edit.check(c, SH, [build("farm-refuses", 11.51)])
    assert "span 2 scale-window" in line and "1.51s after its out point" in line
    assert edit.check(c, SH, [build("clear", 11.54)]) == []  # 1.54 s left after frame 240


def test_variants_print_the_plate_note(shoot, monkeypatch, tmp_path, capsys):
    from footage_index import __main__ as cli
    c, _ = shoot
    monkeypatch.setattr(store, "connect", lambda *a, **k: c)
    v = {"variant": 1, "dir": "d", "method": "m-test", "theme": None, "look": None, "pace": "x", "score": 1.0, "takes": 3,
         "unfilled": [], "reasons": ["r"], "plate_note": "slot(s) 2 leave less than the window plate plus 0.1s margin"}
    monkeypatch.setattr(edit, "get_method", lambda mid: METHOD)
    monkeypatch.setattr(edit, "variants_within", lambda *a, **k: ([v], "root", {"short": False}))
    monkeypatch.setattr(edit, "variants", lambda *a, **k: ([v], "root"))
    cli.main(["plan", "m-test", SH, "--variants", "1"])
    cli.main(["plan", "--variants", "1", SH])
    out = capsys.readouterr().out
    assert out.count("slot(s) 2 leave less than the window plate") == 2


def test_candidates_need_a_keyframe_the_handle_leaves_reachable(shoot):
    c, _ = shoot
    c.execute("UPDATE shots SET t = 11.8, t_in = 11.0, t_out = 12.0")  # 12 s takes, keyframe near the end
    g = G.load(c, SH)
    slot = METHOD["slots"][0]
    h = PLATE("scale-window", 3.0)  # 1.64 s
    run = lambda handle, gate: edit.candidates(c, METHOD, slot, SH + "/", limit=5, gate=gate, seconds=2.0 + handle, handle=handle,
                                               ctx={"need": 2.0 + handle})
    assert run(0.0, g)  # without a handle the take is a candidate
    assert run(h, g) == [] and run(h, None) == []  # trim.choose would end the hold by 12 - 1.64 and lose the keyframe at 11.8
    c.execute("UPDATE shots SET t = 10.0")
    assert run(h, G.load(c, SH)) and run(h, None)  # a keyframe before the handle stays reachable


def test_plan_with_too_few_takes_is_caught_by_check(shoot, monkeypatch, tmp_path):
    c, _ = shoot
    _exclude(monkeypatch, tmp_path, {})
    big = {**METHOD, "id": "m-big", "slots": [{**METHOD["slots"][0], "query": "qa"}] * 60}
    fills, d, wrote = edit.plan(c, big, SH, trim_mode="centre")
    empty = [s["slot"] for s in fills["slots"] if not s.get("path")]
    assert empty and "unfilled" in wrote
    line, = edit.check(c, SH, [d])
    assert f"slot(s) {', '.join(map(str, empty))} unfilled" in line


def test_plate_frames_equal_the_toolkits(real_toolkit):
    from rpresolve import windowplate
    assert trim.PLATE_FRAMES == windowplate.PLATE_FRAMES


# ---- contact sheets and the shot-list check ----
from footage_index import sheet  # noqa: E402

SS = "Misc/Sheet"


@pytest.fixture
def lot(tmp_path):
    """Three clips under SS (one in a 'Low Res' folder), 10 shots each; clip b is 4:3 solid green, the rest 16:9 noise."""
    c, rng = store.connect(str(tmp_path / "s.db")), np.random.default_rng(1)
    ids = {}
    for rel, size, luma in [("b/B_P1900002.mov", (160, 120), 0.5), ("a/A_P1900001.mov", (384, 216), 0.5),
                            ("a/Low Res/C_P1900003.mov", (384, 216), 0.5), ("a/D_P1900004.mov", (384, 216), 0.02)]:
        cid, _ = store.upsert_clip(c, f"{SS}/{rel}", rel, 1, 1, client=0, fps=25, duration=40.0, status="done")
        for k in range(10):
            th = str(tmp_path / f"{cid}-{k}.jpg")
            if "B_" in rel:
                Image.new("RGB", size, (0, 200, 0)).save(th)
            else:
                Image.fromarray(rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8)).save(th)
            ids[(rel, k)] = store.add_shot(c, cid, 2.0 + 4 * k, 0, 4, th, motion=0.01 * k, faces=0, face_max=0.0, luma=luma)
    c.commit()
    return c, ids, tmp_path


def test_sheet_selects_orders_and_skips(lot):
    c, ids, tmp = lot
    r = sheet.make(c, SS, str(tmp / "o"), skip=["LOW res"], per_clip=3, cols=4, rows=2)
    cells = json.load(open(tmp / "o" / "sheets.json"))
    assert (r["clips"], r["cells"], len(r["sheets"])) == (3, 9, 2)
    order = [x["relpath"] for x in cells[::3]]
    assert order == sorted(order) and not any("Low Res" in x["relpath"] for x in cells)
    assert [x["second"] for x in cells[:3]] == [2.0, 18.0, 38.0]  # first, middle, last of ten (index 4.5 rounds to 4)
    assert (cells[3]["sheet"], cells[3]["row"], cells[3]["col"]) == (1, 1, 4) and cells[8]["sheet"] == 2 and cells[8]["row"] == 1
    assert cells[0]["path"] == "/Volumes/Main/" + cells[0]["relpath"] and cells[0]["fps"] == 25


def test_sheet_keeps_aspect_and_flags(lot):
    c, ids, tmp = lot
    sheet.make(c, SS, str(tmp / "o"), skip=["low res"], per_clip=1)
    cells = json.load(open(tmp / "o" / "sheets.json"))
    im = np.asarray(Image.open(tmp / "o" / "sheet-01.jpg").convert("RGB")).astype(int)
    k = [x["relpath"].endswith("B_P1900002.mov") for x in cells].index(True)
    x0 = k * sheet.IMG_W
    green = (im[:sheet.IMG_H, x0:x0 + sheet.IMG_W, 1] > 150) & (im[:sheet.IMG_H, x0:x0 + sheet.IMG_W, 0] < 80)
    ys, xs = np.nonzero(green)
    w, h = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
    assert abs(w / h - 4 / 3) < 0.03 and h >= sheet.IMG_H - 2  # fits the height, letterboxed at the sides, not stretched
    assert "dark" in next(x for x in cells if x["relpath"].endswith("D_P1900004.mov"))["flags"]


def test_sheet_marks_an_excluded_take(lot):
    c, ids, tmp = lot
    ex = tmp / "ex.json"
    ex.write_text(json.dumps({"takes": ["P1900002"], "why": "note"}))
    sheet.make(c, SS, str(tmp / "o"), skip=["low res"], per_clip=2, exclude=str(ex))
    cells = json.load(open(tmp / "o" / "sheets.json"))
    assert {x["relpath"].rsplit("/", 1)[-1] for x in cells if "excluded" in x["flags"]} == {"B_P1900002.mov"}
    im = np.asarray(Image.open(tmp / "o" / "sheet-01.jpg").convert("RGB")).astype(int)
    k = [x["relpath"].endswith("B_P1900002.mov") for x in cells].index(True)
    assert im[60, k * sheet.IMG_W + 1].tolist()[0] > 150 and im[60, k * sheet.IMG_W + 1].tolist()[1] < 90  # red border
    with pytest.raises(ValueError, match="no exclude file"):
        sheet.make(c, SS, str(tmp / "o"), exclude=str(tmp / "nope.json"))


def test_check_shotlist(lot, monkeypatch, capsys):
    from footage_index import __main__ as cli
    c, ids, tmp = lot
    monkeypatch.setattr(edit, "EDITS", str(tmp))
    with open(edit.exclude_path(SS), "w") as f:
        json.dump({"takes": ["P1900002"]}, f)
    mk = lambda *shots: str(tmp / "sl.json") if json.dump({"shots": list(shots)}, open(tmp / "sl.json", "w")) is None else None
    good = {"name": "ok", "source": f"/Volumes/Main/{SS}/a/A_P1900001.mov", "in": 10}
    bad = {"name": "gone", "source": f"/mnt/x/{SS}/b/B_P1900002.mov", "in": 5}
    dark = {"name": "dim", "source": f"/Volumes/Main/{SS}/a/D_P1900004.mov", "in": 9}
    away = {"name": "away", "source": f"/Volumes/Main/{SS}/z/not-here.mov", "in": 1}
    errs, warns = edit.check_shotlist(c, SS, mk(good, bad, dark, away))
    assert len(errs) == 1 and errs[0].startswith("gone:") and "excluded take P1900002" in errs[0]
    assert any(w.startswith("dim:") and "dark" in w for w in warns) and any(w.startswith("away:") and "not in the index" in w for w in warns)
    assert not any(w.startswith("ok:") for w in warns)
    c.execute("UPDATE shots SET t_in=20, t_out=24 WHERE clip_id=(SELECT id FROM clips WHERE relpath LIKE '%A_P1900001%') AND t=22")
    c.commit()
    with open(edit.exclude_path(SS), "w") as f:
        json.dump({"shots": [ids[("a/A_P1900001.mov", 5)]]}, f)
    before = {"name": "runs-in", "source": f"/Volumes/Main/{SS}/a/A_P1900001.mov", "in": 18}  # no out: starts 2s before the window
    assert len(edit.check_shotlist(c, SS, mk(before))[0]) == 1 and "overlaps excluded shot" in edit.check_shotlist(c, SS, mk(before))[0][0]
    assert not edit.check_shotlist(c, SS, mk({**before, "out": 19}))[0]  # an out that stops short is clear
    with pytest.raises(ValueError, match="expected"):
        edit.check_shotlist(c, SS, mk({**before, "out": 17}))
    with open(edit.exclude_path(SS), "w") as f:
        json.dump({"takes": ["P1900002"]}, f)
    monkeypatch.setattr(store, "connect", lambda *a, **k: c)
    with pytest.raises(SystemExit) as e:  # warnings do not change the exit code; an excluded shot does
        cli.main(["check", SS, "--shotlist", mk(good, bad)])
    assert "1 shot(s) use footage" in str(e.value)
    cli.main(["check", SS, "--shotlist", mk(good, dark, away)])
    assert "no excluded shots, 2 warning(s)" in capsys.readouterr().out
    with pytest.raises(ValueError, match="expected"):
        edit.check_shotlist(c, SS, mk({"name": "x"}))


def test_snap_keeps_a_cut_off_a_note_the_previous_cut_took():
    from footage_index import edit
    notes = {"period": 0.5, "onsets": [1.1], "strength": [3.0]}
    assert edit.snap_to_onset(1.0, notes) == (1.1, True) and edit.snap_to_onset(1.2, notes) == (1.1, True)
    with pytest.raises(ValueError):
        edit.check_notes({"onsets": [], "strength": []})


# ---- stable clip key, configurable roots, migration ----
import functools  # noqa: E402
import sqlite3  # noqa: E402

from footage_index import roots as roots_mod  # noqa: E402


def test_clip_key_card_and_content():
    k = store.clip_key("Restricted/X/CAMB-a1b2c3/DCIM/100_CAMA/P1900001.MOV", 5)
    assert k == "card:CAMB-a1b2c3/P1900001"
    assert store.clip_key("Extra:Restricted/Y/CAMB-a1b2c3/P1900001.mov", 9) == k  # root and folder depth do not matter
    assert store.clip_key("Outings/A/Clip.MOV", 7) == store.clip_key("Elsewhere/clip.mov", 7) == "file:7:clip.mov"
    assert store.clip_key("Outings/a.mov", 7) != store.clip_key("Outings/a.mov", 8)


def test_clip_key_matches_the_toolkit(real_toolkit):
    from rpresolve import sidecar
    for p in ["Restricted/X/CAMB-a1b2c3/DCIM/100_CAMA/P1900001.MOV", "Outings/A/100_CAMA/a.mov"]:
        if "CAMB-" in p:
            assert store.clip_key(p, 1) == "card:" + sidecar.card_key(p)


def test_roots_config(monkeypatch, tmp_path):
    monkeypatch.delenv("FOOTAGE_ROOTS")
    monkeypatch.delenv("FOOTAGE_ROOT", raising=False)
    assert roots_mod.load()[0].mount == "" and roots_mod.local_paths("a/b.mov") == []  # nothing configured: database-only commands still work
    monkeypatch.setenv("FOOTAGE_ROOT", str(tmp_path))
    d = roots_mod.load()
    assert [r.name for r in d] == ["Footage"] and roots_mod.volume_path("a/b.mov") == f"{tmp_path}/a/b.mov"
    assert d[0].catalog is None and roots_mod.local_paths("a/b.mov")[0] == f"{tmp_path}/a/b.mov"
    monkeypatch.delenv("FOOTAGE_ROOT")
    monkeypatch.setenv("FOOTAGE_ROOTS", json.dumps([["Main", "/m/a"], ["Extra", "/m/x"]]))
    r = roots_mod.load()
    assert roots_mod.qualify(r[0], "a/b.mov", r) == "a/b.mov" and roots_mod.qualify(r[1], "a/b.mov", r) == "Extra:a/b.mov"
    assert roots_mod.split("Extra:a/b.mov", r) == (r[1], "a/b.mov") and roots_mod.split("a/b.mov", r) == (r[0], "a/b.mov")
    assert roots_mod.volume_path("Extra:a/b.mov") == "/Volumes/Extra/a/b.mov"
    assert store.split_rel("Foo:bar/x.mov") == "Foo:bar/x.mov" and store.split_rel("Extra:a/x.mov") == "a/x.mov"
    f = tmp_path / "roots.json"
    f.write_text(json.dumps([{"name": "Extra", "mount": "/m/x", "volume": "/Volumes/X2"}]))
    monkeypatch.setenv("FOOTAGE_ROOTS", str(f))
    assert roots_mod.load()[0].volume == "/Volumes/X2"
    monkeypatch.setenv("FOOTAGE_ROOTS", json.dumps([["A:b", "/m"]]))
    with pytest.raises(ValueError):
        roots_mod.load()


@pytest.fixture
def idx(monkeypatch, tmp_path):
    """A live-walked root in tmp, a private db, sample.process stubbed to one fake shot per clip."""
    from footage_index import __main__ as cli
    mount = tmp_path / "mnt"
    mount.mkdir()
    db = str(tmp_path / "i.db")
    monkeypatch.setattr(store, "DB", db)
    monkeypatch.setattr(store, "connect", functools.partial(store.connect, db))
    monkeypatch.setattr(cli, "LOG", str(tmp_path / "log"))
    monkeypatch.setattr(cli, "MIN_BYTES", 1)
    monkeypatch.setenv("FOOTAGE_ROOTS", json.dumps([{"name": "Extra", "mount": str(mount)}]))
    monkeypatch.setattr(sample, "process", lambda root, rel, cid: (
        {"duration": 9.0}, [{"t": 1.0, "t_in": 0.0, "t_out": 2.0, "thumb": f"/nonexistent-{cid}.jpg", "motion": 0, "faces": 0,
                              "face_max": 0, "luma": 0.5}]))
    def run(*argv):
        cli.main(["index", *argv])
    return mount, db, run


def _put(mount, rel, n):
    p = mount / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * n)


def _counts(db):
    c = sqlite3.connect(db)
    return {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("clips", "shots", "captions")}


def test_moved_folder_keeps_shots_and_captions(idx):
    mount, db, run = idx
    _put(mount, "Restricted/Old/CAMB-a1b2c3/DCIM/P1900001.MOV", 100)   # a card clip
    _put(mount, "Outings/Old/loose.mov", 200)                         # a content-keyed clip
    _put(mount, "Outings/Gone/bye.mov", 300)
    run()
    c = store.connect()
    ids = {r["relpath"]: r["id"] for r in c.execute("SELECT id, relpath FROM clips")}
    for sid in [r[0] for r in c.execute("SELECT id FROM shots")]:
        store.set_caption(c, sid, {"subject": "hands"}, "m")
    c.commit(); c.close()
    before = _counts(db)
    assert before == {"clips": 3, "shots": 3, "captions": 3}
    os.rename(mount / "Restricted/Old", mount / "Restricted/New")
    os.rename(mount / "Outings/Old", mount / "Outings/Newer")
    os.remove(mount / "Outings/Gone/bye.mov")
    run()
    c = store.connect()
    rows = {r["relpath"]: r["id"] for r in c.execute("SELECT id, relpath FROM clips")}
    assert sorted(rows) == sorted(["Restricted/New/CAMB-a1b2c3/DCIM/P1900001.MOV", "Outings/Newer/loose.mov"])
    assert rows["Restricted/New/CAMB-a1b2c3/DCIM/P1900001.MOV"] == ids["Restricted/Old/CAMB-a1b2c3/DCIM/P1900001.MOV"]
    assert rows["Outings/Newer/loose.mov"] == ids["Outings/Old/loose.mov"]
    assert _counts(db) == {"clips": 2, "shots": 2, "captions": 2}  # the key that matched nothing was pruned, no more
    assert {r["status"] for r in c.execute("SELECT status FROM clips")} == {"done"}
    assert [(r["client"], r["project"]) for r in c.execute("SELECT client, project FROM clips WHERE relpath LIKE 'Restricted/%'")] == [(1, "New")]


def test_a_twin_copy_does_not_split_a_moved_clip(idx):
    mount, db, run = idx
    _put(mount, "A/a.mov", 50)
    run()
    cid = store.connect().execute("SELECT id FROM clips").fetchone()[0]
    os.rename(mount / "A", mount / "B")
    _put(mount, "C/a.mov", 50)  # same bytes and name: candidates() already folds twins into one take
    run()
    rows = store.connect().execute("SELECT id FROM clips").fetchall()
    assert [r["id"] for r in rows] == [cid]


def test_second_root_is_qualified(idx, monkeypatch, tmp_path):
    mount, db, run = idx
    other = tmp_path / "w"
    other.mkdir()
    monkeypatch.setenv("FOOTAGE_ROOTS", json.dumps([["Extra", str(mount)], ["Other", str(other)]]))
    _put(mount, "a/one.mov", 10)
    (other / "z").mkdir()
    (other / "z" / "two.mov").write_bytes(b"y" * 20)
    run()
    assert sorted(r["relpath"] for r in store.connect().execute("SELECT relpath FROM clips")) == ["Other:z/two.mov", "a/one.mov"]


def test_migrate_old_database(idx, monkeypatch, capsys):
    mount, db, run = idx
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE clips(id INTEGER PRIMARY KEY, relpath TEXT UNIQUE NOT NULL, key TEXT NOT NULL, bytes INTEGER, "
                    "mtime INTEGER, client INTEGER NOT NULL DEFAULT 0, project TEXT, camera TEXT, profile TEXT, color_space TEXT, "
                    "fps REAL, width INTEGER, height INTEGER, duration REAL, codec TEXT, status TEXT NOT NULL DEFAULT 'new', "
                    "error TEXT, indexed_at TEXT);"
                    "CREATE TABLE shots(id INTEGER PRIMARY KEY, clip_id INTEGER NOT NULL, t REAL NOT NULL, t_in REAL NOT NULL, "
                    "t_out REAL NOT NULL, thumb TEXT NOT NULL, motion REAL, faces INTEGER, face_max REAL, luma REAL);"
                    "CREATE TABLE captions(shot_id INTEGER PRIMARY KEY, data TEXT NOT NULL, model TEXT NOT NULL);"
                    "INSERT INTO clips(relpath,key,bytes) VALUES ('Outings/a.mov','1:1',7),('C/CAMB-a1b2c3/p.mov','1:1',9);"
                    "INSERT INTO shots(clip_id,t,t_in,t_out,thumb) VALUES (1,1,0,2,'x');"
                    "INSERT INTO captions VALUES (1,'{}','m');")
    c.commit(); c.close()
    from footage_index import __main__ as cli
    with pytest.raises(SystemExit):
        run()  # refuses an unmigrated db
    cli.main(["migrate", "--date", "t"])
    assert os.path.exists(db + ".bak-t") and "counts equal" in capsys.readouterr().out
    keys = [r[0] for r in sqlite3.connect(db).execute("SELECT clip_key FROM clips ORDER BY id")]
    assert keys == ["file:7:a.mov", "card:CAMB-a1b2c3/p"] and _counts(db) == {"clips": 2, "shots": 1, "captions": 1}
    cli.main(["migrate", "--date", "t2"])  # idempotent


# ---- index repairs: decode fallback, undecodable, retry, caption fields, black frames, desqueeze, Work root ----
def test_grab_falls_back_to_software_and_remembers(monkeypatch):
    calls = []

    def fake(hw, path, t, out, vf, timeout):
        calls.append(hw)
        return (False, "hw failed") if hw else (True, "")
    monkeypatch.setattr(sample, "_ffmpeg_frame", fake)
    st = {}
    assert sample.grab("p", 1.0, "o", "vf", None, [], st) and calls == [True, False]
    assert sample.grab("p", 2.0, "o", "vf", None, [], st) and calls == [True, False, False]  # no second hardware try


def test_undecodable_clip_gets_a_status_and_a_reason(idx, monkeypatch):
    mount, db, run = idx
    _put(mount, "a/bad.mov", 10)
    _put(mount, "a/good.mov", 20)
    ok = sample.process
    monkeypatch.setattr(sample, "process", lambda root, rel, cid: (
        ({"duration": 9.0, "undecodable": "moov atom not found"}, []) if rel.endswith("bad.mov") else ok(root, rel, cid)))
    run()
    c = store.connect()
    got = {r["relpath"]: (r["status"], r["error"]) for r in c.execute("SELECT relpath, status, error FROM clips")}
    assert got == {"a/bad.mov": ("undecodable", "moov atom not found"), "a/good.mov": ("done", None)}
    assert store.stats(c)["clips_undecodable"] == 1 and store.stats(c)["clips_error"] == 0
    seen = []
    monkeypatch.setattr(sample, "process", lambda root, rel, cid: seen.append(rel) or ({"duration": 9.0}, []))
    run()
    assert seen == []  # undecodable is not retried every week
    run("--retry-errors")
    assert seen == ["a/bad.mov"]


def test_caption_schema_drops_the_two_constant_fields():
    from footage_index import caption
    assert "lens_feel" not in caption.FIELDS and "camera_move" not in caption.FIELDS
    assert "lens_feel" not in caption.PROMPT and "camera_move" not in caption.PROMPT


def test_caption_skips_black_frames(monkeypatch):
    from footage_index import __main__ as cli, caption
    db = os.path.join(TMP, "black.db")
    c = store.connect(db)
    cid = c.execute("INSERT INTO clips(relpath,key) VALUES ('x.mov','k')").lastrowid
    for i, luma in enumerate([0.01, 0.5, 0.5, 0.01]):  # every 3rd shot (n 0 and 3) is the captioning candidate
        store.add_shot(c, cid, float(i), 0, 1, f"/t{i}.jpg", luma=luma)
    c.commit(); c.close()
    seen = []
    monkeypatch.setattr(store, "connect", functools.partial(store.connect, db))
    monkeypatch.setattr(cli, "LOG", os.path.join(TMP, "l"))
    monkeypatch.setattr(caption, "caption", lambda thumb: seen.append(thumb) or {"subject": "s"})
    cli.main(["caption"])
    assert seen == []  # both candidates are black: no model call


def test_desqueeze_stretches_before_the_scale(monkeypatch):
    seen = {}
    monkeypatch.setattr(sample, "desqueeze_factor", lambda p: 1.33)
    monkeypatch.setattr(sample, "probe", lambda p: {"codec": "h264", "width": 1, "height": 1, "fps": 24.0, "duration": 1.0})
    monkeypatch.setattr(sample, "detect", types.SimpleNamespace(detect_paths=lambda ps: ([{}], [])), raising=False)
    monkeypatch.setattr(sample, "vision", types.SimpleNamespace(face_boxes=lambda t: {}), raising=False)
    monkeypatch.setattr(sample, "THUMBS", os.path.join(TMP, "dq"))
    monkeypatch.setattr(sample, "grab", lambda path, t, out, vf, fps=None, why=None, state=None: seen.setdefault("vf", vf) and False)
    meta, shots = sample.process("/r", "a.mov", 1)
    assert seen["vf"] == "scale=trunc(iw*1.33/2)*2:ih,scale=384:-2" and shots == [] and meta["undecodable"] == "no frames"


def test_work_root_is_limited_and_skips_prep_folders(monkeypatch, tmp_path):
    from footage_index import __main__ as cli
    for rel in ["Current/Shoot/a.mov", "Current/Shoot/renders/r.mov", "Current/Shoot/BMD-1/Proxy/p.mov", "Current/prep/x.mov",
                "Current/Cache/c.mov", "Trials/t.mov", "Cache/z.mov", "Current/Shoot/#recycle/g.mov"]:
        _put(tmp_path, rel, 10)
    monkeypatch.setattr(cli, "MIN_BYTES", 1)
    w = roots_mod.Root("Extra", str(tmp_path), "/Volumes/Extra", None, ("Current/",), "render|cache|proxy|proxies|prep")
    assert [r[0] for r in cli.candidates(root=w)] == ["Current/Shoot/a.mov"]


def test_prune_keeps_a_row_whose_file_is_still_there(idx, monkeypatch):
    mount, db, run = idx
    _put(mount, "a/keep.mov", 10)
    _put(mount, "a/gone.mov", 20)
    _put(mount, "a/other.mov", 30)
    run()
    c = store.connect()
    for sid in [r[0] for r in c.execute("SELECT id FROM shots WHERE clip_id=(SELECT id FROM clips WHERE relpath='a/keep.mov')")]:
        store.set_caption(c, sid, {"subject": "s"}, "m")
    c.commit(); c.close()
    from footage_index import __main__ as cli
    os.remove(mount / "a/gone.mov")
    orig = cli.candidates
    # the candidate rules stop picking keep.mov (as the low res rule did) but the file is on disk
    monkeypatch.setattr(cli, "candidates", lambda only=None, root=None: [r for r in orig(only, root) if "keep" not in r[0]])
    run()
    assert [r["relpath"] for r in store.connect().execute("SELECT relpath FROM clips")] == ["a/keep.mov", "a/other.mov"]
    assert _counts(db)["captions"] == 1


# ---- the index reads the card record: clip ids, rekey, index --card, cameras ----
from footage_index import cards  # noqa: E402


def _record(mount, folder, cam="CAMA", cid="d4e5f61234ab", clips=None):
    """A card record beside the folder, as offload.sh writes it (schema 1: seven keys), plus an optional schema-2 clips list."""
    rec = {"card_id": cid, "listing_hash": "0" * 64, "volume_uuid": None, "cam": cam, "files": 1, "bytes": 1, "offloaded_utc": "2025-01-01T00:00:00Z"}
    if clips is not None:
        rec.update(schema={"name": "card.record", "version": 2}, origin="retrofit", shoot={}, project=None, answers={}, clips=clips, history=[])
    (mount / (folder + ".card.json")).write_text(json.dumps(rec))


def test_record_names_a_clip_whatever_the_folder_is_called(tmp_path):
    cards.clear()
    _put(tmp_path, "Old Shoot/DCIM/100_CAMA/P1900001.MOV", 10)
    p = str(tmp_path / "Old Shoot/DCIM/100_CAMA/P1900001.MOV")
    assert cards.clip_id(p) is None and store.clip_key("Old Shoot/DCIM/100_CAMA/P1900001.MOV", 10, p) == "file:10:p1900001.mov"
    _record(tmp_path, "Old Shoot")
    cards.clear()
    assert cards.clip_id(p) == "CAMA-d4e5f6/P1900001"
    assert store.clip_key("Old Shoot/DCIM/100_CAMA/P1900001.MOV", 10, p) == "card:CAMA-d4e5f6/P1900001"
    # a schema 2 record's own clip list wins
    _record(tmp_path, "Old Shoot", clips=[{"clip_id": "CAMA-d4e5f6/Renamed", "relpath": "DCIM/100_CAMA/P1900001.MOV", "proxy": None}])
    cards.clear()
    assert cards.clip_id(p) == "CAMA-d4e5f6/Renamed"


def test_record_clip_id_matches_the_toolkit(real_toolkit, tmp_path):
    from rpresolve import sidecar
    cards.clear()
    _put(tmp_path, "Old Shoot/DCIM/P1900002.MOV", 10)
    _record(tmp_path, "Old Shoot")
    p = str(tmp_path / "Old Shoot/DCIM/P1900002.MOV")
    assert cards.clip_id(p) == sidecar.card_key(p)


def test_rekey_dry_run_then_apply_keeps_shots_and_captions(idx, capsys):
    mount, db, run = idx
    from footage_index import __main__ as cli
    cards.clear()
    _put(mount, "Old Shoot/DCIM/P1900001.MOV", 100)
    _put(mount, "Loose/x.mov", 50)
    run()
    c = store.connect()
    for sid in [r[0] for r in c.execute("SELECT id FROM shots")]:
        store.set_caption(c, sid, {"subject": "s"}, "m")
    c.commit(); c.close()
    before = _counts(db)
    _record(mount, "Old Shoot")  # the record appears beside the old folder
    cards.clear()
    cli.main(["rekey"])
    out = capsys.readouterr().out
    assert '"would_change": 1' in out and "file -> card" in out and "dry run" in out
    assert [r[0] for r in sqlite3.connect(db).execute("SELECT clip_key FROM clips ORDER BY relpath")] == ["file:50:x.mov", "file:100:p1900001.mov"]
    cli.main(["rekey", "--apply", "--date", "t"])
    assert "counts equal" in capsys.readouterr().out and os.path.exists(db + ".bak-rekey-t")
    assert [r[0] for r in sqlite3.connect(db).execute("SELECT clip_key FROM clips ORDER BY relpath")] == ["file:50:x.mov", "card:CAMA-d4e5f6/P1900001"]
    assert _counts(db) == before
    cli.main(["rekey"])
    assert '"would_change": 0' in capsys.readouterr().out


def test_index_card_indexes_one_folder_and_skips_captions_without_ollama(idx, monkeypatch, capsys):
    mount, db, run = idx
    from footage_index import __main__ as cli
    _put(mount, "Other/a.mov", 10)
    _put(mount, "Card1/DCIM/b.mov", 20)
    _put(mount, "Card1/DCIM/c.mov", 30)
    run("--only", "Other")
    monkeypatch.setattr(cli, "_ollama_up", lambda: False)
    cli.main(["index", "--card", str(mount / "Card1")])
    rels = sorted(r["relpath"] for r in store.connect().execute("SELECT relpath FROM clips"))
    assert rels == ["Card1/DCIM/b.mov", "Card1/DCIM/c.mov", "Other/a.mov"]
    assert "captions skipped" in open(cli.LOG).read()
    with pytest.raises(SystemExit):
        cli.main(["index", "--card", "/nonexistent/folder"])


def test_cameras_lists_distinct_strings_per_card(idx, capsys):
    mount, db, run = idx
    from footage_index import __main__ as cli
    cards.clear()
    for rel in ["Old Shoot/a.mov", "Old Shoot/b.mov", "CAMB-abcdef/c.mov", "Loose/d.mov"]:
        _put(mount, rel, 10 + len(rel))
    _record(mount, "Old Shoot")
    cards.clear()
    run()
    c = store.connect()
    for rel, cam in [("Old Shoot/a.mov", "Maker CAM-A"), ("Old Shoot/b.mov", "Maker CAM-AX"), ("CAMB-abcdef/c.mov", "Maker CAM-B"),
                     ("Loose/d.mov", "GoPro HERO")]:
        c.execute("UPDATE clips SET camera=? WHERE relpath=?", (cam, rel))
    c.commit(); c.close()
    capsys.readouterr()
    snap = lambda: sqlite3.connect(db).execute("SELECT group_concat(relpath || clip_key || ifnull(camera, '')) FROM clips").fetchone()
    before = snap()
    cli.main(["cameras"])
    got = json.loads(capsys.readouterr().out)
    assert got == {"cards": {"Old Shoot": ["Maker CAM-A", "Maker CAM-AX"], "CAMB-abcdef": ["Maker CAM-B"]}, "clips_not_in_a_card": 1}
    assert snap() == before  # writes nothing


def test_refresh_squeeze_redraws_and_reembeds_without_touching_captions(idx, monkeypatch, capsys):
    from PIL import Image
    from footage_index import __main__ as cli
    mount, db, run = idx
    make_clip(str(mount / "a.mp4"))
    c = store.connect()
    cid = c.execute("INSERT INTO clips(relpath,key,clip_key,status,width,height,fps,duration) VALUES ('a.mp4','k','file:1:a.mp4','done',640,360,24,9)").lastrowid
    thumb = os.path.join(TMP, "refresh-a.jpg")
    Image.new("RGB", (384, 216)).save(thumb)  # plain 16:9
    sid = store.add_shot(c, cid, 1.5, 0, 3, thumb, luma=0.5)
    store.set_vector(c, sid, [0.0] * 768)
    store.set_caption(c, sid, {"subject": "kept"}, "m")
    c.commit(); c.close()
    from footage_index import embed
    monkeypatch.setattr(embed, "images", lambda paths: [[1.0] + [0.0] * 767 for _ in paths])
    monkeypatch.setattr(sample, "desqueeze_factor", lambda p: 1.5)
    cli.main(["refresh-squeeze"])
    out = capsys.readouterr().out
    assert '"redraw": 1' in out and '"to_desqueezed": 1' in out and "dry run" in out
    assert Image.open(thumb).size == (384, 216)  # dry run wrote nothing
    before = _counts(db)
    cli.main(["refresh-squeeze", "--apply", "--backup", db + ".bak-d"])
    assert "counts equal" in capsys.readouterr().out and os.path.exists(db + ".bak-d")
    w, h = Image.open(thumb).size
    assert abs(w / h - 640 * 1.5 / 360) < 0.05  # drawn desqueezed 1.5x
    assert _counts(db) == before
    c = store.connect()
    assert c.execute("SELECT data FROM captions").fetchone()[0] == '{"subject": "kept", "tags": []}'
    assert c.execute("SELECT count(*) FROM vec_shots WHERE shot_id=?", (sid,)).fetchone()[0] == 1
    # the reverse: a record of 1 over thumbnails drawn at a 1.5x file tag redraws plain; unexplained stretch is left alone
    monkeypatch.setattr(sample, "desqueeze_factor", lambda p: None)
    monkeypatch.setattr(sample, "file_tag_factor", lambda p: 1.5)
    cli.main(["refresh-squeeze", "--apply", "--backup", db + ".bak-e"])
    assert abs(Image.open(thumb).size[0] / Image.open(thumb).size[1] - 640 / 360) < 0.05
    monkeypatch.setattr(sample, "file_tag_factor", lambda p: None)
    Image.new("RGB", (384, 190)).save(thumb)  # 1.14x, no tag explains it
    capsys.readouterr()
    cli.main(["refresh-squeeze"])
    assert '"unexplained": 1' in capsys.readouterr().out
