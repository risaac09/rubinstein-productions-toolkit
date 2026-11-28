"""
gate: check every draft in a draft-farm folder before anyone watches it, and
fix its loudness. Reads DIR/drafts.json (rpresolve.drafts). With --dest, a draft
off that destination's loudness target first gets a <plan>.loudfix.mp4 beside it
(delivercheck.fix_loudness: audio only, the video stream bit-identical), and the
DIR/send copy that matches the draft byte for byte is refreshed from it; the
original draft is never touched. Then the file the reviewer gets is checked:
qc's rows without a destination, with the destination's true-peak ceiling;
duration against the draft's planned frames (a lost or doubled span); loudness
against the target; scdet against the plan's hard cuts (spans minus one, less the
joints that carry a dissolve, dip, wipe or drawn transition), advisory because
motion in action footage reads as cuts; black that runs out the end under the
head allowance as a fade-out WARN; and a flash row (WARN only). Writes
DIR/gate.json. Exit 0 no FAIL, 1 any FAIL (or an unreadable folder), 2 ffmpeg or
ffprobe missing. WARN rows never fail the gate.
"""

import filecmp
import json
import os
import re
import shutil
import subprocess
import sys

from rpresolve import cli

NAME = "gate"
HELP = "Offline: QC and loudness-fix every draft in a draft-farm folder before review"

# shortcut: a whole-frame mean-luma proxy for a flash test (Harding/ITU-R BT.1702 judge
# luminance in cd/m2 over a quarter of the screen); it misses small-area flashes and
# saturated red. WARN only; upgrade to an area-aware test before using it as a gate.
FLASH_DELTA = 25.0          # YAVG step (0-255) that counts as one luminance transition
FLASH_MAX_PER_S = 3         # flashes (two opposing transitions) allowed in any one second
TRANSITION_TYPES = ("dissolve", "dip", "wipe")


def add_arguments(p):
    p.add_argument("dir", help="A draft-farm output folder (holds drafts.json)")
    p.add_argument("--dest", help="Destination key from resolve-config.json whose loudness "
                   "target the drafts are held to and fixed to (e.g. youtube_16x9_music)")
    p.add_argument("--plans", metavar="DIR",
                   help="The folder holding the plan folders (<plan>/manifest.json), for "
                   "ledgers that do not name their manifest")
    p.add_argument("--config", dest="deliver_config",
                   help="A config overlay kept outside this repo (may also go before the command)")
    p.add_argument("--json", action="store_true", help="Print the result as JSON")


def hard_cuts(manifest):
    """Cuts a render of this one-clip manifest should show to scdet."""
    from rpresolve import transitions
    spans = manifest["clips"][0]["spans"]
    joints = {i for i, sp in enumerate(spans)
              if i and ((sp.get("transition") or {}).get("type") in TRANSITION_TYPES)}
    joints |= {e.get("span_index") for e in manifest.get("effects") or []
               if e.get("effect") in transitions.IDS and e.get("span_index")}
    return max(0, len(spans) - 1 - len(joints))


def flashes(yavg, fps, delta=FLASH_DELTA):
    """Flash count in the worst one-second window of a mean-luma series. A flash is two
    opposing transitions of at least delta."""
    steps = []
    for i in range(1, len(yavg)):
        d = yavg[i] - yavg[i - 1]
        if abs(d) >= delta:
            steps.append((i, 1 if d > 0 else -1))
    trans = [steps[0]] if steps else []
    for s in steps[1:]:
        if s[1] != trans[-1][1]:  # a run of steps the same way is one transition
            trans.append(s)
    win = max(1, int(round(fps)))
    worst, at, lo = 0, None, 0
    for hi in range(len(trans)):
        while trans[hi][0] - trans[lo][0] >= win:
            lo += 1
        n = (hi - lo + 1) // 2
        if n > worst:
            worst, at = n, trans[lo][0] / fps
    return worst, at


def flash_row(path, fps):
    from rpresolve import delivercheck as dc, qc
    try:
        proc = subprocess.run([dc.FFMPEG, "-hide_banner", "-nostats", "-nostdin", "-i", path,
                               "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-",
                               "-an", "-f", "null", "-"], capture_output=True, text=True,
                              timeout=dc.READ_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc = None
    yavg = [float(v) for v in re.findall(r"lavfi\.signalstats\.YAVG=([\d.]+)", proc.stdout)] if proc else []
    if not proc or proc.returncode != 0 or not yavg:
        return {"check": "flash", "status": qc.WARN, "found": "unreadable",
                "expected": "a luma series", "source": "gate"}
    n, at = flashes(yavg, fps)
    ok = n <= FLASH_MAX_PER_S
    return {"check": "flash", "status": qc.PASS if ok else qc.WARN,
            "found": f"{n} flashes in one second" + (f" at {at:.2f}s" if n else ""),
            "expected": f"<= {FLASH_MAX_PER_S} a second (whole-frame luma, advisory)",
            "source": "gate"}


def _manifest(d, plans):
    path = d.get("manifest") or (os.path.join(plans, d["plan"], "manifest.json") if plans else None)
    if not path or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _send_twin(folder, draft_file):
    """The send copy that is byte-identical to the draft, or None."""
    if not folder or not os.path.isdir(folder):
        return None
    size = os.path.getsize(draft_file)
    for name in sorted(os.listdir(folder)):
        p = os.path.join(folder, name)
        if name.endswith(".mp4") and os.path.getsize(p) == size and filecmp.cmp(p, draft_file, shallow=False):
            return p
    return None


def _fix(d, dest, send_dir):
    """(file to check, rows, warnings): the draft itself when its loudness is on target,
    else its fixed copy (made now, or checked from an earlier run)."""
    from rpresolve import delivercheck as dc, qc
    loud = dest["loudness"]
    target, tol = float(loud["integrated_lufs"]), float(loud["tolerance_lu"])
    try:
        i = dc.measure_loudness(d["file"])["integrated_lufs"]
    except dc.CheckError:
        i = None
    want = f"{target:g} LUFS +/- {tol:g} LU"
    if i is None:  # no audio stream, or silence: nothing to fix; the loudness row says so
        return d["file"], [], []
    if abs(i - target) <= tol + 1e-9:
        return d["file"], [], []
    fixed = dc.fixed_path(d["file"])
    twin = _send_twin(send_dir, d["file"])  # found before any fix, while it still matches
    done = None if twin or not os.path.isfile(fixed) else _send_twin(send_dir, fixed)
    kept = os.path.lexists(fixed)
    if kept:
        same, warnings = dc.video_hash(fixed) == dc.video_hash(d["file"]), [
            f"{os.path.basename(fixed)} is from an earlier run and was checked, not redone; "
            "move it away to fix again (for another target, say)"]
    else:
        res = dc.fix_loudness(d["file"], dest, fps=str(d["fps"]), size=tuple(d["size"]))
        same, warnings = res["video_identical"], res["warnings"]
    rows = [{"check": "loudness_fix", "status": qc.PASS if same else qc.FAIL,
             "found": f"draft at {i} LUFS; {os.path.basename(fixed)} "
                      + ("kept from an earlier run" if kept else "written") if same
             else "the fixed copy's video differs from the draft's",
             "expected": f"{want}, video bit-identical", "source": "gate"}]
    if not same:
        return d["file"], rows, warnings
    if twin or done:
        if twin:
            shutil.copyfile(fixed, twin)
        rows.append({"check": "send_copy", "status": qc.PASS,
                     "found": f"{os.path.basename(twin or done)} carries the fixed sound",
                     "expected": "the review copy is the checked file", "source": "gate"})
    elif send_dir:
        rows.append({"check": "send_copy", "status": qc.WARN,
                     "found": "no send copy matches the draft byte for byte; not refreshed",
                     "expected": "the review copy is the checked file", "source": "gate"})
    return fixed, rows, warnings


def _tail_fade(row, duration, th):
    """qc fails any black after the head; black that runs out the end and is shorter than
    the head allowance is a designed fade-out: WARN, as qc does for a fade-in."""
    from rpresolve import qc
    m = re.fullmatch(r"([\d.]+)s for ([\d.]+)s", row["found"] or "")
    if (row["status"] == qc.FAIL and m and duration
            and abs(float(m.group(1)) + float(m.group(2)) - duration) <= 0.2
            and float(m.group(2)) < th["black_lead_max_s"]):
        return dict(row, status=qc.WARN, note="trailing black (fade-out)")
    return row


def gate_one(d, dest, plans, send_dir):
    from rpresolve import qc
    manifest = _manifest(d, plans)
    cuts = hard_cuts(manifest) if manifest else None
    path, fix_rows, warnings = _fix(d, dest, send_dir) if dest else (d["file"], [], [])
    th = dict(qc.THRESHOLDS)
    if dest:
        th["true_peak_max_dbtp"] = float(dest["loudness"]["true_peak_max_dbtp"])
    planned = d["frames"] / float(d["fps"])
    r = qc.run(path, None, shots=None if cuts is None else cuts + 1, thresholds=th,
               expected_duration=planned)
    from rpresolve import delivercheck as dc
    actual = dc.duration(dc.probe(path))
    rows = []
    for x in r["checks"]:
        if x["check"] == "deliver_check":
            continue
        if x["check"] == "blackdetect":
            x = _tail_fade(x, actual, th)
        if x["check"] == "scdet" and x["status"] == qc.FAIL:
            # motion and flashes in action footage read as cuts; the duration row catches a lost span
            x = dict(x, status=qc.WARN, note="scdet is advisory here (motion reads as cuts)")
        if x["check"] == "loudness" and dest and x["found"] != "no audio stream":
            m = re.match(r"(-?[\d.]+) LUFS", x["found"] or "")
            target, tol = (float(dest["loudness"][k]) for k in ("integrated_lufs", "tolerance_lu"))
            ok = bool(m) and abs(float(m.group(1)) - target) <= tol + 1e-9
            x = dict(x, status=(qc.PASS if ok else qc.FAIL) if m else qc.WARN,
                     expected=f"{target:g} LUFS +/- {tol:g} LU")
        rows.append(x)
    if cuts is None:
        rows = [x for x in rows if x["check"] != "scdet"]
        rows.append({"check": "scdet", "status": qc.SKIP, "found": "no manifest",
                     "expected": "the plan's manifest (drafts.json 'manifest', or --plans)",
                     "source": "gate"})
    rows.append(flash_row(path, float(d["fps"])))
    rows += fix_rows
    order = {qc.FAIL: 0, qc.WARN: 1, qc.PASS: 2, qc.SKIP: 3}
    rows.sort(key=lambda x: order[x["status"]])
    counts = {s: sum(1 for x in rows if x["status"] == s) for s in (qc.PASS, qc.FAIL, qc.WARN, qc.SKIP)}
    return {"plan": d["plan"], "file": d["file"], "checked": path, "checks": rows, "counts": counts,
            "warnings": warnings, "status": "fail" if counts[qc.FAIL] else "pass"}


def run(args):
    """Gate every draft. Exit 0 no FAIL, 1 any FAIL or an unreadable folder, 2 a tool missing."""
    from rpresolve import deliver, delivercheck as dc
    try:
        ledger = os.path.join(args.dir, "drafts.json")
        if not os.path.isfile(ledger):
            raise ValueError(f"{args.dir}: no drafts.json (run the draft farm first)")
        with open(ledger, encoding="utf-8") as f:
            doc = json.load(f)
        dest = None
        if args.dest:
            dest = deliver.destination(cli.deliver_config(args), args.dest)
            if not deliver.has_loudness_target(dest):
                raise ValueError(f"'{args.dest}' has no loudness target to hold drafts to")
        send_dir = (doc.get("send") or {}).get("dir")
        results = [gate_one(d, dest, args.plans, send_dir) for d in doc.get("drafts") or []]
    except dc.ToolMissing as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except (dc.CheckError, deliver.DeliverError, ValueError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    out = {"dir": os.path.abspath(args.dir), "destination": args.dest, "drafts": results,
           "status": "fail" if any(r["status"] == "fail" for r in results) else "pass"}
    with open(os.path.join(args.dir, "gate.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, default=str)
    if args.json:
        print(json.dumps(out, indent=1, default=str))
    else:
        for r in results:
            c = r["counts"]
            print(f"{r['plan']}: {r['status'].upper()}  ({c['FAIL']} fail, {c['WARN']} warn, {c['PASS']} pass)")
            for x in r["checks"]:
                if x["status"] in ("FAIL", "WARN"):
                    print(f"  {x['status']:<4}  {x['check']:<16} found {x['found']}  expected {x['expected']}"
                          + (f"  ({x['note']})" if x.get("note") else ""))
            for w in r["warnings"]:
                print(f"  note  {w}")
        print(f"gate: {out['status']}; {os.path.join(args.dir, 'gate.json')}")
    return 1 if out["status"] == "fail" else 0


if __name__ == "__main__":
    cli.script(__name__)
