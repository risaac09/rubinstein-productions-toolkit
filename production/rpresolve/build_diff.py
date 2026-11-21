"""
rpresolve.build_diff: the edit that was planned against the timeline that was
built. Offline, read-only, standard library only. Nothing touches Resolve and
nothing is written.

Run from production/:
    python3 -m rpresolve.build_diff --manifest MANIFEST.json --items ITEMS.json [--clip NAME]

MANIFEST is a cut manifest, read through cutlist.load_manifest. ITEMS is the
saved output of the timeline_items read: one result, or a list of results,
for example one with track_type video and one with track_type audio. --items
takes several files; pages are stitched in order, and a page that a next_offset
promises but the files lack is a FAIL row.

The plan is what cut builds: the clip's spans laid end to end on V1 from the
timeline's start frame. Frames are counted as cut counts them: cutlist.frames
at the manifest's own fps number, rounding half up, so a span plays source
frames [frame(in), frame(out)) and an item must hold exactly that start and
length. A source end is not compared; the length gives it.

Sound is expected as interchange places it: audio_lead and audio_tail move the
sound's start and end in record and source frames, and the neighbouring span's
sound gives way by the same amount. cut ignores both fields, so a timeline cut
builds from a manifest that carries them FAILs with "not built".

Anything the tool could not compare is a WARN row, printed and counted in the
last line ("N rows not compared"); WARN does not change the exit code.

One table, FAIL rows first, then WARN. Exit 0 no FAIL, 1 any FAIL, 2 usage or bad input.
"""

import argparse
import json
import os
import sys

from rpresolve import cutlist
from rpresolve.deliver import exact_fps

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


def load_items(paths):
    """{"start_frame", "fps", "video": [rows], "audio": [rows], "audio_read",
    "page_problems"} from timeline_items files, checked key by key. Pages of
    one track type are stitched in the order given."""
    results = []
    for path in paths:
        raw = _load_json(path, "items")
        got = [raw] if isinstance(raw, dict) else raw
        if not isinstance(got, list) or not got or not all(isinstance(r, dict) for r in got):
            raise DiffError(f"items {path}: expected a timeline_items result or a list of them")
        results += got
    out = {"start_frame": None, "fps": None, "video": [], "audio": [], "audio_read": False,
           "page_problems": []}
    expect = {}  # per kind, the offset its next page must carry
    for n, res in enumerate(results, 1):
        where = f"items result {n}"
        kind = res.get("track_type")
        if kind not in ("video", "audio"):
            raise DiffError(f"{where}: track_type is {kind!r}, not video or audio")
        if not isinstance(res.get("rows"), list):
            raise DiffError(f"{where}: no 'rows' list")
        offset, nxt = res.get("offset", 0), res.get("next_offset")
        if not _whole(offset) or not (nxt is None or _whole(nxt)):
            raise DiffError(f"{where}: offset and next_offset must be whole numbers")
        due = expect.get(kind, 0)
        if due is not None and offset != due:
            out["page_problems"].append(f"{kind} page at offset {offset} where {due} was due")
        expect[kind] = nxt
        tl = res.get("timeline") or {}
        if kind == "video":
            if out["start_frame"] is None:
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
            if kind == "audio":
                _need(row, "source_start", w)
            out[kind].append(row)
    for kind, due in expect.items():
        if due is not None:
            out["page_problems"].append(f"{kind} read ends with next_offset {due} and no page after it")
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


def path_match(a, b):
    """True (same file), False (another file), None (differs only in letter
    case: not decided) for two paths. Both on disk: os.path.samefile. Else
    the real paths (symlinks resolved) are compared."""
    try:
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
    except OSError:
        pass
    ra, rb = os.path.realpath(a), os.path.realpath(b)
    if ra == rb:
        return True
    return None if ra.lower() == rb.lower() else False


def audio_plan(clip, fps):
    """Per span, the sound as interchange.timeline places it, in frames:
    (record start, record end, source start). Lead and tail move it, and the
    neighbour gives way."""
    sp = clip["spans"]
    lead = [cutlist.frame(x.get("audio_lead", 0), fps) for x in sp]
    tail = [cutlist.frame(x.get("audio_tail", 0), fps) for x in sp]
    out, rec = [], 0
    for k, x in enumerate(sp):
        n = cutlist.frames(x, fps)
        pt = tail[k - 1] if k else 0
        nl = lead[k + 1] if k + 1 < len(sp) else 0
        out.append((rec - lead[k] + pt, rec + n + tail[k] - nl,
                    cutlist.frame(x["in"], fps) - lead[k] + pt))
        rec += n
    return out


def diff(m, clip, fps, items):
    rows = []
    base = items["start_frame"]
    plan, at = [], 0
    aplan = audio_plan(clip, fps)
    for i, s in enumerate(clip["spans"], 1):
        n = cutlist.frames(s, fps)
        plan.append({"n": i, "rec": at, "len": n, "src": cutlist.frame(s["in"], fps),
                     "path": cutlist.source_for(m, s)["path"],
                     "split": bool(s.get("audio_lead") or s.get("audio_tail"))})
        at += n
    v1 = sorted((r for r in items["video"] if r["track"] == 1 and r.get("clip") is not None),
                key=lambda r: r["start"])
    upper = [r for r in items["video"] if r["track"] != 1 and r.get("clip") is not None]
    # a V1 item with no clip is a transition; elsewhere it is a solid or a title, unplanned
    transitions = [r for r in items["video"] if r["track"] == 1 and r.get("clip") is None]
    unplanned = [r for r in items["video"] if r["track"] != 1 and r.get("clip") is None]

    if items["page_problems"]:
        rows.append(_row("FAIL", "pages", "; ".join(items["page_problems"])))

    rate = exact_fps(items["fps"])
    if rate is None:
        rows.append(_row("FAIL", "frame rate", f"timeline rate {items['fps']!r} is not readable"))
    elif abs(rate - fps) > RATE_TOLERANCE:
        rows.append(_row("FAIL", "frame rate", f"timeline {rate:g}, manifest {fps:g}"))
    else:
        rows.append(_row("PASS", "frame rate", f"{rate:g}"))

    rows.append(_row("PASS" if len(v1) == len(plan) else "FAIL", "item count",
                     f"{len(v1)} on V1, plan has {len(plan)}"))
    if transitions:
        rows.append(_row("WARN", "transitions",
                         f"{len(transitions)} transition item(s) on V1 not compared"))

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
        bad, warn = [], []
        if it["duration"] != p["len"]:
            bad.append(f"{it['duration']} frames, plan {p['len']}")
        if it["source_start"] != p["src"]:
            bad.append(f"source starts at {it['source_start']}, plan {p['src']}")
        if "enabled" not in it:
            warn.append("enabled not in ITEMS, not compared")
        elif it["enabled"] is not True:
            bad.append("disabled")
        path = (it.get("media") or {}).get("File Path")
        if not path:
            warn.append("not compared by path: the item names no file")
        else:
            same = path_match(path, p["path"])
            if same is None:
                warn.append("path differs only in case, not compared")
            elif not same:
                bad.append(f"plays {path}, plan {p['path']}")
        status = "FAIL" if bad else "WARN" if warn else "PASS"
        rows.append(_row(status, f"span {p['n']}", "; ".join(bad + warn) or
                         f"record {p['rec']}, {p['len']} frames, source {p['src']}"))

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
        bad, warn = [], []
        for p, (a0, a1, asrc) in zip(plan, aplan):
            v = found.get(p["n"])
            if v is None:
                continue
            vm = v.get("media") or {}
            near = []
            for a in items["audio"]:
                if not (a["start"] - base < p["rec"] + p["len"] and a["end"] - base > p["rec"]):
                    continue
                am = a.get("media") or {}
                if am.get("unique_id") is not None and vm.get("unique_id") is not None:
                    same = am["unique_id"] == vm["unique_id"]
                elif am.get("File Path"):
                    same = path_match(am["File Path"], vm.get("File Path") or p["path"])
                else:
                    same = None
                if same is not False:
                    near.append((a, same))
            if not near:
                bad.append(f"span {p['n']}: no audio item under it")
                continue
            exact = [(a, same) for a, same in near if a["start"] - base == a0
                     and a["end"] - base == a1 and a["source_start"] == asrc]
            good = [(a, same) for a, same in exact if a["track"] == SOUND_TRACK]
            if good:
                a, same = good[0]
                if a.get("enabled") is False:
                    bad.append(f"span {p['n']}: audio disabled")
                elif "enabled" not in a:
                    warn.append(f"span {p['n']}: audio enabled not in ITEMS, not compared")
                if same is not True:
                    warn.append(f"span {p['n']}: audio source not compared")
                continue
            if exact:
                bad.append(f"span {p['n']}: audio on " + ", ".join(
                    f"A{t}" for t in sorted({a['track'] for a, _ in exact})) + f", expected A{SOUND_TRACK}")
                continue
            a = next((x for x, _ in near if x["track"] == SOUND_TRACK), near[0][0])
            why = []
            if a["start"] - base != a0 or a["end"] - base != a1:
                why.append(f"audio runs {a['start'] - base}-{a['end'] - base}, plan {a0}-{a1}")
            if a["source_start"] != asrc:
                why.append(f"audio source starts at {a['source_start']}, plan {asrc}")
            if p["split"] and a["start"] - base == p["rec"] and a["end"] - base == p["rec"] + p["len"]:
                why.append("the manifest's audio_lead or audio_tail is not built (cut ignores both)")
            bad.append(f"span {p['n']}: " + "; ".join(why))
        rows.append(_row("FAIL" if bad else "WARN" if warn else "PASS", "audio under video",
                         "; ".join(bad + warn) or f"{len(found)} video item(s) each have their audio on "
                                                  f"A{SOUND_TRACK}, source in step"))

    extra = [f"V1 item at record {r['start'] - base} ({r['duration']} frames)"
             for r in v1 if id(r) not in matched]
    extra += [f"V{r['track']} item at record {r['start'] - base} ({r['duration']} frames)"
              for r in upper]
    extra += [f"V{r['track']} item at record {r['start'] - base} "
              f"({r['duration']} frames, no clip: a title or solid)" for r in unplanned]
    rows.append(_row("FAIL" if extra else "PASS", "extra items", "; ".join(extra) or "none"))
    order = {"FAIL": 0, "WARN": 1, "PASS": 2}
    rows.sort(key=lambda r: order[r["status"]])  # stable
    return rows


def render(rows):
    n = {k: sum(1 for r in rows if r["status"] == k) for k in ("FAIL", "WARN", "PASS")}
    table = "\n".join(f"{r['status']:<5} {r['check']:<18} {r['detail']}" for r in rows)
    return f"{table}\n{n['FAIL']} FAIL, {n['WARN']} WARN, {n['PASS']} PASS; {n['WARN']} rows not compared"


def main(argv=None):
    p = argparse.ArgumentParser(prog="python3 -m rpresolve.build_diff", allow_abbrev=False,
                                description=__doc__.split("\n\n")[0])
    p.add_argument("--manifest", required=True, help="a cut manifest (see rpresolve/cutlist.py)")
    p.add_argument("--items", required=True, nargs="+",
                   help="saved timeline_items output (video, and audio); several files are pages in order")
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
