"""
rpresolve.build_diff: the edit that was planned against the timeline that was
built. Offline, read-only, standard library only. Nothing touches Resolve and
nothing is written.

Run from production/:
    python3 -m rpresolve.build_diff --manifest MANIFEST.json --items ITEMS.json [--clip NAME]

MANIFEST is a cut manifest, read through cutlist.load_manifest. ITEMS is the
saved output of the timeline_items read: one result, or a list of results,
for example one with track_type video and one with track_type audio. Every
page must be saved (a result with a next_offset is refused).

The plan is what cut builds: the clip's spans laid end to end on V1 from the
timeline's start frame. Frames are counted as cut counts them: cutlist.frames
at the manifest's own fps number, rounding half up, so a span plays source
frames [frame(in), frame(out)) and an item must hold exactly that start and
length. A source end is not compared; the length gives it.

One table, FAIL rows first. Exit 0 all pass, 1 any FAIL, 2 usage or bad input.
"""

import argparse
import json
import os
import sys

from rpresolve import cutlist

RATE_TOLERANCE = 0.01  # 29.97 as a number against Resolve's 29.97003
SOUND_TRACK = 1        # where cut and assemble_timeline put the camera sound


class DiffError(ValueError):
    """Bad input, named."""


def _whole(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _need(row, key, where, whole=True):
    if key not in row:
        raise DiffError(f"{where}: no '{key}'")
    if whole and not _whole(row[key]):
        raise DiffError(f"{where}: '{key}' is {row[key]!r}, not a whole number")
    return row[key]


def _load_json(path, what):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError as e:
        raise DiffError(f"{what} {path}: {e.strerror or e}") from None
    except ValueError as e:  # bad JSON or bad UTF-8
        raise DiffError(f"{what} {path} is not JSON: {e}") from None


def load_items(path):
    """({"start_frame", "fps", "video": [rows], "audio": [rows]}) from a
    timeline_items file, checked key by key."""
    raw = _load_json(path, "items")
    results = [raw] if isinstance(raw, dict) else raw
    if not isinstance(results, list) or not results or not all(isinstance(r, dict) for r in results):
        raise DiffError(f"items {path}: expected a timeline_items result or a list of them")
    out = {"start_frame": None, "fps": None, "video": [], "audio": [], "audio_read": False}
    for n, res in enumerate(results, 1):
        where = f"items result {n}"
        kind = res.get("track_type")
        if kind not in ("video", "audio"):
            raise DiffError(f"{where}: track_type is {kind!r}, not video or audio")
        if not isinstance(res.get("rows"), list):
            raise DiffError(f"{where}: no 'rows' list")
        if res.get("next_offset") is not None:
            raise DiffError(f"{where}: next_offset is {res['next_offset']}; save every page of "
                            "timeline_items (raise its limit)")
        tl = res.get("timeline") or {}
        if kind == "video":
            out["start_frame"] = _need(tl, "start_frame", f"{where}: timeline")
            out["fps"] = tl.get("fps")
        else:
            out["audio_read"] = True
        for i, row in enumerate(res["rows"], 1):
            w = f"{where}, row {i}"
            if not isinstance(row, dict):
                raise DiffError(f"{w}: not an object")
            for key in ("track", "start", "end", "duration"):
                _need(row, key, w)
            if kind == "video" and row.get("clip") is not None:
                _need(row, "source_start", w)
            out[kind].append(row)
    if out["start_frame"] is None:
        raise DiffError("items hold no video result; save timeline_items with track_type video")
    return out


def load_plan(path, clip_name):
    """(manifest, clip, fps) through the repo's own loader."""
    try:
        m = cutlist.load_manifest(path, allow_no_speech=True)
    except OSError as e:
        raise DiffError(f"manifest {path}: {e.strerror or e}") from None
    except cutlist.CutlistError as e:
        raise DiffError(f"manifest {path}: {e}") from None
    except ValueError as e:
        raise DiffError(f"manifest {path} is not JSON: {e}") from None
    fps = m.get("fps")
    if isinstance(fps, bool) or not isinstance(fps, (int, float)) or fps <= 0:
        raise DiffError(f"manifest {path}: 'fps' is {fps!r}; one positive number is needed "
                        "(a manifest holds one frame rate)")
    names = [c["name"] for c in m["clips"]]
    if clip_name is None:
        if len(names) != 1:
            raise DiffError(f"manifest holds {len(names)} clips ({', '.join(names)}); name one with --clip")
        clip_name = names[0]
    if clip_name not in names:
        raise DiffError(f"no clip {clip_name!r} in the manifest; it has {', '.join(names)}")
    return m, m["clips"][names.index(clip_name)], fps


def _row(status, check, detail):
    return {"status": status, "check": check, "detail": detail}


def diff(m, clip, fps, items):
    rows = []
    base = items["start_frame"]
    plan, at = [], 0
    for i, s in enumerate(clip["spans"], 1):
        n = cutlist.frames(s, fps)
        plan.append({"n": i, "rec": at, "len": n, "src": cutlist.frame(s["in"], fps),
                     "file": os.path.basename(cutlist.source_for(m, s)["path"])})
        at += n
    v1 = sorted((r for r in items["video"] if r["track"] == 1 and r.get("clip") is not None),
                key=lambda r: r["start"])
    upper = [r for r in items["video"] if r["track"] != 1 and r.get("clip") is not None]
    transitions = sum(1 for r in items["video"] if r.get("clip") is None)

    tl_fps = items["fps"]
    try:
        tl_fps = float(tl_fps)
    except (TypeError, ValueError):
        tl_fps = None
    if tl_fps is None:
        rows.append(_row("PASS", "frame rate", "timeline rate not in ITEMS, not compared"))
    elif abs(tl_fps - fps) > RATE_TOLERANCE:
        rows.append(_row("FAIL", "frame rate", f"timeline {tl_fps:g}, manifest {fps:g}"))
    else:
        rows.append(_row("PASS", "frame rate", f"{tl_fps:g}"))

    ok = len(v1) == len(plan)
    rows.append(_row("PASS" if ok else "FAIL", "item count",
                     f"{len(v1)} on V1, plan has {len(plan)}" +
                     (f" ({transitions} transition item(s) not counted)" if transitions else "")))

    by_start = {}
    for r in v1:
        by_start.setdefault(r["start"] - base, []).append(r)
    matched, found = set(), {}
    for p in plan:
        cands = by_start.get(p["rec"])
        if not cands:
            near = min(v1, key=lambda r: abs(r["start"] - base - p["rec"]), default=None)
            hint = f"; nearest item starts at {near['start'] - base}" if near else ""
            rows.append(_row("FAIL", f"span {p['n']}", f"no item at record {p['rec']}{hint}"))
            continue
        it = cands.pop(0)
        matched.add(id(it))
        found[p["n"]] = it
        bad = []
        if it["duration"] != p["len"]:
            bad.append(f"{it['duration']} frames, plan {p['len']}")
        if it["source_start"] != p["src"]:
            bad.append(f"source starts at {it['source_start']}, plan {p['src']}")
        path = (it.get("media") or {}).get("File Path")
        if path and os.path.basename(path) != p["file"]:
            bad.append(f"plays {os.path.basename(path)}, plan {p['file']}")
        rows.append(_row("FAIL" if bad else "PASS", f"span {p['n']}",
                         "; ".join(bad) or f"record {p['rec']}, {p['len']} frames, source {p['src']}"))

    gaps, overlaps, end = [], [], base
    for r in v1:
        d = r["start"] - end
        if d > 0:
            gaps.append(f"{d} frame(s) at record {end - base}")
        elif d < 0:
            overlaps.append(f"{-d} frame(s) at record {r['start'] - base}")
        end = max(end, r["end"])
    for name, found_list in (("gaps", gaps), ("overlaps", overlaps)):
        rows.append(_row("FAIL" if found_list else "PASS", name,
                         "; ".join(found_list) or "none on V1"))

    if m.get("camera_sound") is False:
        rows.append(_row("PASS", "audio under video", "the plan has no sound (camera_sound false)"))
    elif not items["audio_read"]:
        rows.append(_row("FAIL", "audio under video",
                         "no audio read in ITEMS; save timeline_items with track_type audio"))
    else:
        bad = []
        for p in plan:
            v = found.get(p["n"])
            if v is None:
                continue
            uid = (v.get("media") or {}).get("unique_id")
            under = [a for a in items["audio"] if a["start"] <= v["start"] and a["end"] >= v["end"]
                     and (uid is None or (a.get("media") or {}).get("unique_id") in (None, uid))]
            if not under:
                bad.append(f"span {p['n']}: no audio item under it")
            elif not any(a["track"] == SOUND_TRACK for a in under):
                tracks = sorted({a["track"] for a in under})
                bad.append(f"span {p['n']}: audio on " + ", ".join(f"A{t}" for t in tracks) +
                           f", expected A{SOUND_TRACK}")
        rows.append(_row("FAIL" if bad else "PASS", "audio under video",
                         "; ".join(bad) or f"{len(found)} video item(s) each have audio on A{SOUND_TRACK}"))

    extra = [f"V1 item at record {r['start'] - base} ({r['duration']} frames)"
             for r in v1 if id(r) not in matched]
    extra += [f"V{r['track']} item at record {r['start'] - base} ({r['duration']} frames)" for r in upper]
    rows.append(_row("FAIL" if extra else "PASS", "extra items", "; ".join(extra) or "none"))
    rows.sort(key=lambda r: r["status"] != "FAIL")  # stable: FAIL first
    return rows


def render(rows):
    return "\n".join(f"{r['status']:<5} {r['check']:<18} {r['detail']}" for r in rows)


def main(argv=None):
    p = argparse.ArgumentParser(prog="python3 -m rpresolve.build_diff", allow_abbrev=False,
                                description=__doc__.split("\n\n")[0])
    p.add_argument("--manifest", required=True, help="a cut manifest (see rpresolve/cutlist.py)")
    p.add_argument("--items", required=True, help="saved timeline_items output (video, and audio)")
    p.add_argument("--clip", help="the manifest clip (needed when it holds more than one)")
    args = p.parse_args(argv)
    try:
        m, clip, fps = load_plan(args.manifest, args.clip)
        items = load_items(args.items)
        rows = diff(m, clip, fps, items)
    except DiffError as e:
        print(f"build_diff: {e}", file=sys.stderr)
        return 2
    print(render(rows))
    return 1 if any(r["status"] == "FAIL" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
