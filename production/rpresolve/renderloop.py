"""
Render-and-check loop (VP-144): one runner per artifact type. Each round renders
a lavfi fixture (standing in for a Deliver render), runs the checks that already
exist for that type, and prints a FAIL-first table with a version line. A
caller-supplied fix step turns the table into new render inputs and the loop
runs again, at most ROUNDS times, then hands back with the table.

    graphic   overlay-check             (alpha master: ProRes 4444)
    sound     audio-report flags        (loudness target, true peak, clipping, clicks)
    cut       qc                        (black, freeze, loudness, clipping, silence)
    grade     perceive --ref            (luma against a reference still)

Offline: ffmpeg writes every fixture. It never connects to Resolve and never
starts a render there; point a runner at a real Deliver render by calling
check() on that file.

The known limit: the loop proves the render meets the spec, not that the spec
is right. A spec that cannot be met hands back at the cap; a wrong spec that
can be met passes (see tests/test_renderloop.py, TestSpecWrong).

    fix(rec) -> new params dict, or None to stop and hand back
    rec = {"n", "params", "rows", "raw"}   raw is the checker's own result
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile

from . import delivercheck as dc
from . import paths

ROUNDS = 5
ORDER = {"FAIL": 0, "WARN": 1, "PASS": 2, "SKIP": 3, "INFO": 3}


class LoopError(RuntimeError):
    """The loop cannot start: a bad kind or a work folder inside a git tree."""


def _ffmpeg(*args):
    dc.check_tools(ffmpeg=True)
    p = subprocess.run([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", *args],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if p.returncode:
        raise LoopError(f"ffmpeg failed: {p.stderr.strip()[:300]}")


def _row(check, ok, detail):
    return {"check": check, "status": "PASS" if ok else "FAIL", "detail": detail}


# --- graphic -----------------------------------------------------------------
# params: pix_fmt (what is rendered), expect_pix_fmt (the spec)

def render_graphic(p, out):
    from . import overlay
    prores = ["-c:v", "prores_ks", "-profile:v", "4444" if overlay.has_alpha(p["pix_fmt"]) else "3"]
    _ffmpeg("-f", "lavfi", "-i", "color=c=0x3ad06e:s=128x72:r=25:d=1", "-vf",
            f"format={p['pix_fmt']}", *prores, "-pix_fmt", p["pix_fmt"], out)


def check_graphic(path, p):
    from . import overlay
    r = overlay.check(path, expect=[("pix_fmt", p["expect_pix_fmt"])], clear_at=(), fps="25")
    return [{"check": x["row"], "status": x["status"], "detail": x["detail"]}
            for x in r["rows"]], r


# --- sound -------------------------------------------------------------------
# params: gain_db (render), target_lufs (the spec)

def render_sound(p, out):
    _ffmpeg("-f", "lavfi", "-i", f"sine=f=1000:r=48000:d=3,volume={p['gain_db']}dB",
            "-c:a", "pcm_s16le", out)


def check_sound(path, p):
    from . import audioreport as ar
    th = dict(ar.DEFAULTS, target_lufs=p["target_lufs"])
    r = ar.measure(path, th=th)
    flags = ar.flags(r, th)
    rows = [{"check": f["flag"], "status": "FAIL", "detail": f["note"]} for f in flags]
    return rows or [_row("audio-report", True, f"no flags, {r['integrated_lufs']} LUFS")], r


# --- cut ---------------------------------------------------------------------
# params: black_s (seconds of black in the middle; the spec allows none)

def render_cut(p, out):
    b = p["black_s"]
    vf = (f"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(t,1,{1 + b})'"
          if b else "null")
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=160x90:r=25:d=3", "-f", "lavfi", "-i",
            "sine=f=1000:r=48000:d=3,volume=-18dB", "-vf", vf, "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-c:a", "aac", out)


def check_cut(path, p):
    from . import qc
    r = qc.run(path)
    return [{"check": x["check"], "status": x["status"],
             "detail": f"found {x['found']}, expected {x['expected']}"} for x in r["checks"]], r


# --- grade -------------------------------------------------------------------
# params: brightness (render), tol_ire (the spec); the reference is made once.

def render_grade(p, out):
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=160x90:r=25:d=1", "-vf",
            f"eq=brightness={p['brightness']}", "-c:v", "libx264", "-pix_fmt", "yuv420p", out)


def check_grade(path, p):
    from . import perceive
    out = os.path.join(os.path.dirname(path), os.path.basename(path) + ".perceive")
    shutil.rmtree(out, ignore_errors=True)
    d = perceive.perceive(path, out, ref=p["ref"], faces=False)["cine"]["compare"]["luma_ire_delta"]["p50"]
    return [_row("luma_vs_ref", abs(d) <= p["tol_ire"],
                 f"luma p50 {d:+} IRE against the reference (tolerance {p['tol_ire']})")], \
        {"luma_p50_delta_ire": d}


def make_grade_ref(work):
    ref = os.path.join(work, "ref.png")
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=160x90:r=25:d=1", "-frames:v", "1", ref)
    return ref


KINDS = {"graphic": (render_graphic, check_graphic, "mov"),
         "sound": (render_sound, check_sound, "wav"),
         "cut": (render_cut, check_cut, "mp4"),
         "grade": (render_grade, check_grade, "mp4")}


def version_line(kind, n, params, rows):
    sha = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:8]
    fails = sum(r["status"] == "FAIL" for r in rows)
    return f"{kind} r{n} {sha} {'FAIL' if fails else 'PASS'} ({fails} fail of {len(rows)} rows)"


def format_table(rec):
    lines = [rec["version"]]
    lines += [f"  {r['status']:<4}  {r['check']:<14} {r['detail']}" for r in rec["rows"]]
    return "\n".join(lines)


def run_loop(kind, params, fix, work=None, rounds=ROUNDS):
    """Render, check, fix, again, at most `rounds` times. `work` is a new or
    empty folder outside any git tree (default: a fresh temp folder).
    Returns {kind, converged, handed_back, rounds, table}."""
    if kind not in KINDS:
        raise LoopError(f"unknown artifact type {kind!r}; one of {', '.join(KINDS)}")
    render, check, ext = KINDS[kind]
    work = work or tempfile.mkdtemp(prefix="renderloop-")
    if paths.in_any_git_tree(work):
        raise LoopError(f"{work} is inside a git working tree; the fixtures name media.")
    os.makedirs(work, exist_ok=True)
    params, history = dict(params), []
    for n in range(1, rounds + 1):
        path = os.path.join(work, f"{kind}-r{n}.{ext}")
        render(params, path)
        rows, raw = check(path, params)
        rows = sorted(rows, key=lambda r: ORDER.get(r["status"], 3))
        rec = {"n": n, "params": params, "rows": rows, "raw": raw, "file": path,
               "version": version_line(kind, n, params, rows)}
        history.append(rec)
        if not any(r["status"] == "FAIL" for r in rows):
            break
        nxt = fix(rec) if n < rounds else None
        if nxt is None:
            break
        params = dict(nxt)
    ok = not any(r["status"] == "FAIL" for r in history[-1]["rows"])
    return {"kind": kind, "converged": ok, "handed_back": not ok, "rounds": history,
            "table": "\n\n".join(format_table(r) for r in history)
                     + ("" if ok else f"\n\nHanded back after {len(history)} round(s): "
                        "the last table above still has FAIL rows.")}
