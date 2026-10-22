"""
rpresolve.duckplan: duck music under dialogue by a depth set in dB, keyed
on whether someone is speaking rather than on how loud they are. Offline:
ffmpeg decodes and renders, numpy builds the key; no source is changed.

Run from production/:
    python3 -m rpresolve.duckplan plan DIALOGUE --out DIR [--stream N]
        [--depth 12] [--gate-db DB]
    python3 -m rpresolve.duckplan render MUSIC DIALOGUE --out DIR
        [--music-stream N] [--stream N] [--depth 12] [--gate-db DB]

A compressor keyed on the raw dialogue ducks by an amount that follows the
talker: in a measured test a loud and a quiet talker were ducked about
10 dB apart at the same settings, and a threshold low enough for the quiet
one ducked the room tone before anyone spoke. Here the key says only
"speech or not", so the depth is the number asked for, for every talker.

The key, from the dialogue's own stream:
    1. a KEY_HIGHPASS_HZ high-pass, then the RMS of every HOP_S
    2. the gate opens GATE_ABOVE_FLOOR_DB over the floor (the 10th
       percentile of 100 ms RMS, audio-report's noise floor), or at
       --gate-db. That keeps hum and room tone out of the key. The floor is
       one number for the file: when a stem cuts between rooms whose tone
       differs by GATE_ABOVE_FLOOR_DB or more, the louder room's tone opens
       the key. Give --gate-db above the louder room's tone, or plan each
       setup's stretch on its own. When FLOOR_PERCENTILE % or more of the
       100 ms frames are digital silence (a gated, strip-silenced or
       synthetic voice stem), the floor is digital silence and the gate sits
       at SILENCE_GATE_DBFS: every live stretch opens the key, so an edited
       stem whose clips carry room tone between digital gaps needs --gate-db
       above that room tone
    3. an opening shorter than MIN_OPEN_S is dropped (a click is not a
       word), and each opening is held HOLD_S, so the pauses inside a phrase
       do not pump the music
    4. the 0/1 result is smoothed by a 2-pole low-pass at SMOOTH_HZ, then
       moved LOOKAHEAD_S earlier, so the music is down before the first
       syllable. Offline, looking ahead costs nothing
The gain is -depth x key dB: 0 with no speech, -depth under speech.

plan writes into DIR (made if its parent exists, outside any git tree):
    duck.sendcmd   asendcmd lines for a volume filter named duck, one at
                   each change of SENDCMD_STEP_DB or more, for an ffmpeg
                   graph run from DIR (duckplan.json prints the line);
                   ffmpeg 6.0 names that filter duck, not volume@duck, so
                   it needs 6.0.1 or later
    duck-key.wav   the key itself as a DC signal, 0 for silence and 1 for
                   speech, mono 8 kHz 16-bit, for a compressor's sidechain.
                   duckplan.json gives the sidechaincompress setting that
                   turns it into the depth asked: ratio SC_RATIO and a
                   threshold of -depth / (1 - 1/ratio) dB. Some hosts block
                   DC on a sidechain input; ffmpeg does not
    duckplan.json  the parameters, the floor and gate, the speech spans
                   (phrases), and both usage lines

render makes the plan, ducks MUSIC through sidechaincompress keyed by
duck-key.wav, writes music-ducked.wav (24-bit WAV at the music's rate and
channels) and measures it against the music it came from:
    gr_under_speech_db   reduction where the key is fully on (>= KEY_ON)
    gr_outside_db        where the key is off (<= KEY_OFF)
    gr_lead_in_db        before the key first rises, with lead_in_s
    span_gr_db, depth_spread_db
                         the reduction in each run of full key and their
                         spread: two talkers at different levels duck alike
MUSIC and DIALOGUE start together; put them on one clock first. Music
past the dialogue's end is not ducked. A plan with no speech above the gate
is refused (exit 1) rather than written as a duck that never moves.

What it does not do: decide the depth (12 to 18 dB is a common start for
music under talk), mix, or set loudness. None of this is listening; the
ear decides whether the music breathes in the right places.
"""

import argparse
import json
import math
import os
import subprocess
import sys

import numpy as np

from . import audioreport as ar
from . import delivercheck as dc
from . import dxmetrics as dxm
from . import paths

HOP_S = 0.01
KEY_HIGHPASS_HZ = 100
FLOOR_FRAME_S = 0.1
FLOOR_PERCENTILE = 10
GATE_ABOVE_FLOOR_DB = 10.0
SILENCE_GATE_DBFS = -70.0        # the gate when the floor is digital silence
MIN_OPEN_S = 0.05
HOLD_S = 0.3
SMOOTH_HZ = 8.0
LOOKAHEAD_S = 0.12
DEPTH_DB = 12.0
MAX_DEPTH_DB = 40.0
KEY_RATE = 8000
SC_RATIO = 20
SC_ATTACK_MS = 5
SC_RELEASE_MS = 50
SENDCMD_STEP_DB = 0.1
KEY_ON, KEY_OFF = 0.99, 0.01
MIN_SPAN_S = 0.2                 # a run of full key shorter than this gets no depth of its own
RUN_TIMEOUT_S = 3600


def params():
    return {"hop_s": HOP_S, "key_highpass_hz": KEY_HIGHPASS_HZ,
            "floor_percentile": FLOOR_PERCENTILE, "gate_above_floor_db": GATE_ABOVE_FLOOR_DB,
            "silence_gate_dbfs": SILENCE_GATE_DBFS,
            "min_open_s": MIN_OPEN_S, "hold_s": HOLD_S, "smooth_hz": SMOOTH_HZ,
            "lookahead_s": LOOKAHEAD_S, "key_rate": KEY_RATE, "sc_ratio": SC_RATIO,
            "sc_attack_ms": SC_ATTACK_MS, "sc_release_ms": SC_RELEASE_MS,
            "sendcmd_step_db": SENDCMD_STEP_DB, "key_on": KEY_ON, "key_off": KEY_OFF}


def _lowpass2(x, fc, fs):
    """2-pole Butterworth low-pass (bilinear), causal."""
    k = math.tan(math.pi * fc / fs)
    q = math.sqrt(0.5)
    n = 1 / (1 + k / q + k * k)
    b0, a1, a2 = k * k * n, 2 * (k * k - 1) * n, (1 - k / q + k * k) * n
    y = np.zeros(len(x))
    x1 = x2 = y1 = y2 = 0.0
    for i, v in enumerate(x.tolist()):
        out = b0 * (v + 2 * x1 + x2) - a1 * y1 - a2 * y2
        x2, x1, y2, y1 = x1, v, y1, out
        y[i] = out
    return y


def key(power, hop_s, gate_db=None):
    """(key 0..1 per hop, floor dBFS or None for digital silence, gate dBFS,
    phrases as hop index pairs) from the dialogue's mean power per hop."""
    g = max(1, int(round(FLOOR_FRAME_S / hop_s)))
    m = len(power) // g * g
    p100 = power[:m].reshape(-1, g).mean(axis=1) if m else power
    live = p100[p100 > 0]
    if not len(live):
        raise ar.ReportError("the dialogue is digital silence: nothing to key on")
    if len(p100) - len(live) >= len(p100) * FLOOR_PERCENTILE / 100:
        floor, auto = None, SILENCE_GATE_DBFS   # the percentile lands on digital silence
    else:
        floor = 10 * math.log10(float(np.percentile(live, FLOOR_PERCENTILE)))
        auto = floor + GATE_ABOVE_FLOOR_DB
    gate = auto if gate_db is None else float(gate_db)
    on = power > 10 ** (gate / 10)
    for a, b in zip(*ar._runs(on)):
        if (b - a) * hop_s < MIN_OPEN_S:
            on[a:b] = False
    h = int(round(HOLD_S / hop_s))
    c = np.concatenate(([0], np.cumsum(on)))
    i = np.arange(len(on))
    held = (c[i + 1] - c[np.maximum(i - h, 0)]) > 0     # any opening in the last h hops
    k = np.clip(_lowpass2(held.astype(np.float64), SMOOTH_HZ, 1 / hop_s), 0.0, 1.0)
    la = int(round(LOOKAHEAD_S / hop_s))
    k = np.concatenate((k[la:], np.repeat(k[-1:], min(la, len(k)))))
    phrases = []
    for a, b in zip(*ar._runs(on)):
        if phrases and a - phrases[-1][1] <= h:
            phrases[-1][1] = int(b)
        else:
            phrases.append([int(a), int(b)])
    return k, floor, gate, phrases


def sidechain_filter(depth_db):
    """sidechaincompress settings that duck by depth_db when the key is at 1."""
    thr_db = -depth_db / (1 - 1 / SC_RATIO)
    return (f"sidechaincompress=threshold={10 ** (thr_db / 20):.6f}:ratio={SC_RATIO}"
            f":attack={SC_ATTACK_MS}:release={SC_RELEASE_MS}:knee=1:detection=peak:makeup=1")


def plan(dialogue, stream=0, depth_db=DEPTH_DB, gate_db=None):
    """The duck plan for one dialogue stream. The key array rides along under
    '_key' and '_hop' (left out of the JSON)."""
    dc.check_tools(ffmpeg=True)
    if not 0 < depth_db <= MAX_DEPTH_DB:
        raise ValueError(f"--depth is in dB, above 0 and at most {MAX_DEPTH_DB:g}")
    fp = dxm.frame_power(dialogue, stream, HOP_S, af=f"highpass=f={KEY_HIGHPASS_HZ}")
    if not len(fp["power"]):
        raise ar.ReportError(f"no audio decoded from {dialogue}")
    hop = fp["hop_s"]
    k, floor, gate, phrases = key(fp["power"], hop, gate_db)
    gate_from = "--gate-db" if gate_db is not None else "floor" if floor is not None \
        else "digital silence"
    if not phrases:
        raise ar.ReportError(f"no speech in {dialogue} rises above the gate ({gate:.1f} dBFS, "
                             f"from the {gate_from}): nothing to duck under. Give --gate-db "
                             "below the speech")
    spans = [[round(a * hop, 2), round(b * hop, 2)] for a, b in phrases]
    return {"dialogue": dialogue, "stream": stream,
            "duration_s": round(fp["samples"] / fp["sample_rate"], 3), "depth_db": depth_db,
            "floor_dbfs": round(floor, 1) if floor is not None else None,
            "gate_dbfs": round(gate, 1), "gate_from": gate_from,
            "speech_spans": spans, "speech_s": round(sum(b - a for a, b in spans), 2),
            "first_speech_s": spans[0][0] if spans else None,
            "sidechain": sidechain_filter(depth_db),
            "sidechain_usage": ("[1:a]aresample=<music rate>,apad[k];[0:a]aformat=sample_fmts=dbl:"
                                "channel_layouts=<music channels>c[m];[m][k]"
                                + sidechain_filter(depth_db) + "[out]  (input 1: duck-key.wav)"),
            "sendcmd_usage": (f"asetnsamples=n=<{HOP_S * 1000:g} ms of samples>:p=0,"
                              "asendcmd=f=duck.sendcmd,volume@duck=volume=1"),
            "params": params(), "_key": k, "_hop": hop}


def sendcmd_lines(k, hop_s, depth_db):
    """asendcmd lines for volume@duck: the gain at each change of
    SENDCMD_STEP_DB or more, and wherever it comes to rest."""
    rest = np.where(k < 0.005, 0.0, np.where(k > 0.995, 1.0, k))
    lines, last = [], None
    for i, v in enumerate(rest.tolist()):
        g = -depth_db * v
        if last is None or abs(g - last) >= SENDCMD_STEP_DB or (v in (0.0, 1.0) and g != last):
            lines.append(f"{i * hop_s:.3f} volume@duck volume {10 ** (g / 20):.6f};")
            last = g
    return lines


def _guard(out_dir, names, sources):
    if paths.in_any_git_tree(out_dir):
        raise paths.OutputRefused(f"{out_dir} is inside a git working tree. The output names "
                                  "media; write it outside any repository.")
    outs = [os.path.join(out_dir, n) for n in names]
    for o in outs:
        problem = paths.out_problem(o, any_git_tree=True)
        if problem:
            raise paths.OutputRefused(problem)
    problem = dxm.source_problem(outs, sources)
    if problem:
        raise paths.OutputRefused(problem)
    if not os.path.isdir(out_dir):
        os.mkdir(out_dir)  # one level only: the parent must exist


def _ffmpeg_write(cmd, out, data=None):
    """Run an ffmpeg command that writes `out` through a .part file."""
    tmp = out + ".part.wav"
    try:
        proc = subprocess.run(cmd + [dc._src(tmp)], input=data, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=RUN_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise ar.ReportError(f"ffmpeg timed out writing {out}")
    if proc.returncode != 0:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise ar.ReportError(f"ffmpeg could not write {out}: "
                             f"{proc.stderr.decode(errors='replace').strip()[-300:]}")
    os.replace(tmp, out)


def write_plan(p, out_dir):
    """duck.sendcmd, duck-key.wav and duckplan.json into out_dir."""
    k, hop = p["_key"], p["_hop"]
    with open(os.path.join(out_dir, "duck.sendcmd"), "w", encoding="utf-8") as f:
        f.write("\n".join(sendcmd_lines(k, hop, p["depth_db"])) + "\n")
    n = int(round(p["duration_s"] * KEY_RATE))
    kk = np.interp(np.arange(n) / KEY_RATE, np.arange(len(k)) * hop, k).astype("<f4")
    _ffmpeg_write([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "f32le",
                   "-ar", str(KEY_RATE), "-ac", "1", "-i", "-", "-map_metadata", "-1",
                   "-c:a", "pcm_s16le", "-f", "wav"],
                  os.path.join(out_dir, "duck-key.wav"), kk.tobytes())
    p["files"] = {"sendcmd": os.path.join(out_dir, "duck.sendcmd"),
                  "key": os.path.join(out_dir, "duck-key.wav"),
                  "plan": os.path.join(out_dir, "duckplan.json")}
    with open(p["files"]["plan"], "w", encoding="utf-8") as f:
        json.dump({a: b for a, b in p.items() if not a.startswith("_")}, f, indent=1)
    return p


def make_plan(dialogue, out_dir, stream=0, depth_db=DEPTH_DB, gate_db=None):
    _guard(out_dir, ("duck.sendcmd", "duck-key.wav", "duckplan.json"), (dialogue,))
    return write_plan(plan(dialogue, stream, depth_db, gate_db), out_dir)


def measure(x, y, k, rate, depth_db):
    """Gain reduction of y against x (same length, mono) where the key k
    (per sample) is on, off, and before it first rises."""
    def gr(a, b):
        pa, pb = float((a ** 2).sum()), float((b ** 2).sum())
        return round(10 * math.log10(pa / pb), 2) if pa > 0 and pb > 0 else None
    on, off = k >= KEY_ON, k <= KEY_OFF
    rises = np.flatnonzero(k > KEY_OFF)
    lead = int(rises[0]) if len(rises) else len(k)
    spans = [gr(x[a:b], y[a:b]) for a, b in zip(*ar._runs(on)) if (b - a) / rate >= MIN_SPAN_S]
    spans = [v for v in spans if v is not None]
    under = gr(x[on], y[on])
    return {"gr_under_speech_db": under, "gr_outside_db": gr(x[off], y[off]),
            "gr_lead_in_db": gr(x[:lead], y[:lead]), "lead_in_s": round(lead / rate, 2),
            "span_gr_db": spans,
            "depth_spread_db": round(max(spans) - min(spans), 2) if spans else None,
            "depth_error_db": round(under - depth_db, 2) if under is not None else None}


def render(music, dialogue, out_dir, music_stream=0, stream=0, depth_db=DEPTH_DB, gate_db=None):
    """The plan, music-ducked.wav, and the measured reduction."""
    out = os.path.join(out_dir, "music-ducked.wav")
    _guard(out_dir, ("duck.sendcmd", "duck-key.wav", "duckplan.json", "music-ducked.wav"),
           (music, dialogue))
    p = plan(dialogue, stream, depth_db, gate_db)
    info = ar.audio_stream(music, music_stream)
    rate = info["sample_rate"]
    key_wav = os.path.join(out_dir, "duck-key.wav")
    write_plan(p, out_dir)
    # aformat pins the music's channels: ffmpeg 6.0 otherwise folds them to the mono key's
    graph = (f"[1:a]aresample={rate},apad[k];[0:a:{music_stream}]aformat=sample_fmts=dbl:"
             f"channel_layouts={info['channels']}c[m];[m][k]{p['sidechain']}[out]")
    _ffmpeg_write([dc.FFMPEG, "-hide_banner", "-nostdin", "-nostats", "-v", "error", "-y",
                   "-i", dc._src(music), "-i", dc._src(key_wav), "-filter_complex", graph,
                   "-map", "[out]", "-map_metadata", "-1", "-c:a", "pcm_s24le", "-f", "wav"], out)
    x, _ = dxm.decode(music, music_stream)
    y, _ = dxm.decode(out)
    n = min(len(x), len(y))
    kk = np.interp(np.arange(n) / rate, np.arange(len(p["_key"])) * p["_hop"], p["_key"],
                   right=0.0)
    p["music"], p["music_stream"], p["out"] = music, music_stream, out
    p["measured"] = measure(x[:n], y[:n], kk, rate, depth_db)
    return write_plan(p, out_dir)


def summary(p):
    floor = "digital silence" if p["floor_dbfs"] is None else f"{p['floor_dbfs']} dBFS"
    lines = [f"{os.path.basename(p['dialogue'])} s{p['stream']}: floor {floor}, "
             f"gate {p['gate_dbfs']} dBFS ({p['gate_from']}), {len(p['speech_spans'])} phrase(s), "
             f"{p['speech_s']} s of speech, first at {p['first_speech_s']} s; depth "
             f"{p['depth_db']:g} dB",
             "Files: " + ", ".join(p["files"].values())]
    m = p.get("measured")
    if m:
        lines.append(f"Measured on {os.path.basename(p['out'])}: reduction under speech "
                     f"{m['gr_under_speech_db']} dB, outside {m['gr_outside_db']} dB, lead-in "
                     f"{m['gr_lead_in_db']} dB over {m['lead_in_s']} s; per phrase "
                     f"{m['span_gr_db']} (spread {m['depth_spread_db']} dB)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def add_arguments(p):
    sub = p.add_subparsers(dest="action", required=True)
    pl = sub.add_parser("plan", help="the key, as a sendcmd file and a sidechain key WAV")
    pl.add_argument("dialogue")
    r = sub.add_parser("render", help="duck a music file by the plan and measure it")
    r.add_argument("music")
    r.add_argument("dialogue")
    r.add_argument("--music-stream", type=int, default=0)
    for q in (pl, r):
        q.add_argument("--out", required=True, help="Folder, outside any git tree")
        q.add_argument("--stream", type=int, default=0, help="Dialogue audio stream")
        q.add_argument("--depth", type=float, default=DEPTH_DB, help="Duck depth in dB")
        q.add_argument("--gate-db", type=float,
                       help="Gate threshold in dBFS (default: floor + 10 dB)")


def run(args):
    """Exit 0 written; 1 not (no speech above the gate included)."""
    try:
        if args.action == "plan":
            p = make_plan(args.dialogue, args.out, args.stream, args.depth, args.gate_db)
        else:
            p = render(args.music, args.dialogue, args.out, args.music_stream, args.stream,
                       args.depth, args.gate_db)
    except (ar.ReportError, dc.CheckError, dc.ToolMissing, paths.OutputRefused, ValueError,
            OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(summary(p))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m rpresolve.duckplan",
                                 description="Duck music under dialogue by a set depth, keyed "
                                             "on speech activity. Offline.")
    add_arguments(ap)
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
