"""
rpresolve.mixbuild: an offline mix from a spec, and a blind A/B packet.
ffmpeg renders, audio-report measures; nothing here touches Resolve or
Fairlight, and nothing is sent anywhere.

    python3 -m rpresolve.mixbuild render SPEC --out DIR [--report] [--dry-run]
    python3 -m rpresolve.mixbuild ab --out DIR FILE FILE [FILE ...]
                                    [--start S] [--seconds 20] [--seed N]
    python3 -m rpresolve.mixbuild presets
    python3 -m rpresolve.mixbuild preset-ab DIALOGUE.wav --out DIR
                                    [--presets lav,boom,vo] [--start S] [--seconds 20] [--seed N]

The spec is YAML (a small subset read by the parser below, so PyYAML is not
needed; JSON is accepted too). Paths are relative to the spec's folder.

    sample_rate: 48000            # default 48000
    channels: 2                   # every stem and the mix, default 2
    stems:
      dialogue:
        input: dialogue.wav
        chain:                    # steps run in order
          - highpass: 80
          - dehum: {freq: 60}     # opt-in, like every step
          - declick: {}
          - denoise: {method: candidates}
          - eq: {freq: 3000, gain: 2, q: 1}
          - deess: {}
          - compress: {threshold: -18, ratio: 3}
          - fade: {in: 0.5, out: 1.0}
          - loudnorm: {i: -16, tp: -1.5}
        candidates:               # optional: denoise settings to compare
          afftdn_6:  {method: afftdn, nr: 6}
          afftdn_18: {method: afftdn, nr: 18}
      music:
        input: music.wav
        chain: [{gain: -3}]
    mix:
      gains: {dialogue: 0, music: -12}      # dB per stem
      duck: {key: dialogue, under: [music], depth: 12}
      chain:
        - loudnorm: {i: -16, tp: -1.5}
        - limiter: {ceiling: -1.5}

Steps: gain, fade, highpass, lowpass, eq, dehum, declick, deess, denoise,
compress, loudnorm, speechnorm (mix chain only), limiter. dehum notches freq (50 or 60) and its
harmonics; declick is ffmpeg's adeclick. denoise methods: afftdn, anlmdn and
arnndn (ffmpeg built in; arnndn needs a `model` file or it is skipped),
deepfilternet (recorded as skipped in v1, installed or not: run `deep-filter`
on the input yourself and name its output as the stem input), and
`candidates`, the place a candidate goes. Each candidate renders its own stem
file; the main stem skips the placeholder. A denoiser's delay (afftdn about
25 ms, anlmdn 8 ms at 48 kHz, and it moves with the rate and ffmpeg's version)
is measured once per render and trimmed off, so the stem stays on picture.
iZotope RX is not offered (decision 26: out until a licence is confirmed on
record). A skipped step is recorded in render.json, never fatal; so is a
loudnorm that fell back to dynamic mode (it compressed, so listen).

The duck is keyed on speech activity (rpresolve.duckplan, run on the rendered
key stem), not on its level, so music goes down by `depth` dB under every
talker alike; `gate_db` sets the gate where the floor guess is wrong.

render writes into DIR: stems/NAME.wav (each stem after its chain),
candidates/NAME__CAND.wav, mixed/NAME.wav (each stem as it sits in the mix:
its gain, its ducking and the mix chain's linear steps; they sum to mix.wav,
up to the first step of the mix chain that is not linear, which is recorded),
duck/ (the duck plan and key), mix.wav and render.json (spec hash, ffmpeg
version, every filter graph, each output's hash and skips). The same spec and
inputs give byte-identical WAVs: bitexact flags, one thread, no metadata,
two-pass loudnorm. It refuses to write over a source and refuses any DIR
inside a git working tree. With --report each output is measured by
audio-report into DIR/reports/NAME/.

ab cuts the same span from each file, matches the excerpts' integrated
loudness (turning the louder ones down, so nothing clips), shuffles them to
A, B, C ..., and writes the sealed key to DIR/sealed/key.json (read-only,
hash in packet.json). Listen first, open the key after.

Numbers are not listening: a render that measures well can still sound
wrong. The A/B packet is the control for that.

Dialogue presets. A stem may say `preset: lav` (or boom, vo, music_bed)
instead of a chain; `presets` prints each one's steps and why they are
there. A preset is a starting chain, written down so a choice has a reason;
it is not a claim that it sounds better, and no preset sets loudness (that is
the mix's). Give a preset or a chain, not both; candidates need a chain.
render.json records the preset each stem used. preset-ab renders the same
dialogue through each preset (and leaves it untouched, as a reference),
then builds the blind A/B packet from the results, so the ear picks.

Dialogue-gated loudness. `speechnorm: {key: dialogue, i: -16}` in the mix
chain measures the mix's integrated loudness over the key stem's speech
spans only (duckplan's speech key, on the rendered key stem; `gate_db` as in
duck) and sets one linear gain to bring that to `i`. Music in the pauses
and under a laugh does not count. It replaces loudnorm (give one, not
both), so put a limiter after it; a true peak past `tp` (default -1.5) after
the gain is recorded in the skips. render.json holds the speech and whole-
mix loudness. The spans are joined end to end for the measurement, so a
seam is read as continuous.

Room-tone fill. A stem may say `roomtone: {}` to fill its gaps and edit
points before its chain runs, with the room tone rpresolve.roomtone finds in
the stem's own input (`source`, `stream`, `window: [start, end]` name another
or a fixed window). A gap is a run of digital silence of `min_gap` seconds
(default 0.25; `gaps: [[a, b], ...]` gives them by hand). The bed plays at
its own level through each gap, `gap_level_db` off (default 0), and fades
over `xfade` seconds (default 0.05) either side so it sits under the edges.
`points: [t, ...]` lays the bed under an edit point over +/- `edit_pad_s`
(default 0.5), `edit_level_db` down (default -6, since the clip already has
its own tone). Nothing else changes. The seam check roomtone makes on its
loop is recorded; a bed over its limit goes in the skips.

Stems for delivery. Give every stem a `role: dx`, `mx` or `fx` and render also
writes export/DX.wav, MX.wav, FX.wav and ME.wav (music and effects, no
dialogue): each is the sum of its stems' mixed/ files, so each sits at the
mix's gain, ducking and linear chain, at the mix's length and rate. Then the
sum-null check: DX + MX + FX against mix.wav, as the peak of the difference in
dBFS, must be at or below NULL_FLOOR_DBFS (-60). The residual and the floor
go in render.json ("stems"). A limiter, a dynamic loudnorm or any other step
in the mix chain that is not linear cannot be undone stem by stem, so the
mixed stems stop before it, the sum cannot null, and render exits 1 saying
so (the files are still written, for listening). Put the limiter on each
delivered stem, or accept that the stems sum to the mix before it.

Room-tone denoise. A dialogue stem may say `denoise_roomtone: {nr: 12}` to
run afftdn with its noise profile learned from a room-tone window, not
tracked while it runs: the stem's quietest window (rpresolve.roomtone find;
`window: [start, end]` and `stream` override), played ahead of the stem so
the profile is built before any speech. It runs on the stem's input before
its chain (after any roomtone fill), never on a stem whose role is mx or fx, and the
filter's delay is measured and trimmed as the other denoisers' is. It
lowers the floor by up to nr dB; a profile from a window with speech or a
tone in it takes those out too. afftdn's nf is where it takes the floor to
be: with a learned profile, a value at the window's own level cuts little
and one a few dB above reaches nr, so unless `nf` is given it is the
window's level plus NF_ABOVE_DB (15 dB); the figure used is in render.json.
Whether it sounds better is for the ear: use `candidates` or preset-ab.
"""

import argparse
import copy
import functools
import hashlib
import json
import os
import random
import re
import secrets
import shutil
import subprocess
import sys

import numpy as np

from . import audioreport as ar
from . import delivercheck as dc
from . import duckplan as dp
from . import dxmetrics as dxm
from . import roomtone as rt
from .paths import OutputRefused, in_any_git_tree

BIT_DEPTH = "pcm_s24le"
AB_TOLERANCE_LU = 0.5
RUN_TIMEOUT_S = 3600
MAX_DELAY_S = 0.25               # a denoiser delaying more than this is refused
DUCK_KEY = "duck/duck-key.wav"   # a mix input that is the duck key; stem names hold no /
# steps that distribute over a sum, so a mixed stem may carry them (loudnorm too, when linear)
LINEAR = {"gain", "fade", "highpass", "lowpass", "eq", "dehum", "speechnorm"}
# Starting chains for dialogue. Each says why, and none says it sounds better: Isaac's ear decides
# (preset-ab). No loudnorm: loudness is the mix's (speechnorm there).
PRESETS = {
    "lav": {
        "why": "A lavalier sits on the chest, so it carries proximity low end and clothing rustle "
               "and loses top. High-pass above the rustle, dip the chest boom around 250 Hz, lift "
               "the presence band a little, light afftdn for the floor, a gentle de-ess and 3:1.",
        "chain": [{"highpass": 100}, {"eq": {"freq": 250, "gain": -2, "q": 1}},
                  {"eq": {"freq": 4500, "gain": 1.5, "q": 1}},
                  {"denoise": {"method": "afftdn", "nr": 8}}, {"deess": {"intensity": 0.5}},
                  {"compress": {"threshold": -20, "ratio": 3}}]},
    "boom": {
        "why": "A boom is off-axis and further away, so it carries the room and has no proximity "
               "boost to cut. Lower high-pass, a touch of presence, afftdn a little deeper for "
               "the room, a light de-ess (the capsule is not at the mouth) and a gentler 2.5:1.",
        "chain": [{"highpass": 70}, {"denoise": {"method": "afftdn", "nr": 12}},
                  {"eq": {"freq": 3000, "gain": 1, "q": 1}}, {"deess": {"intensity": 0.3}},
                  {"compress": {"threshold": -18, "ratio": 2.5}}]},
    "vo": {
        "why": "Voice-over is a close mic in a quiet booth: no denoise (it would cost tone for "
               "little floor), a small dip at 200 Hz for the proximity, a firmer de-ess for the "
               "close capsule and 3.5:1 with a faster attack for the wider dynamic range.",
        "chain": [{"highpass": 80}, {"eq": {"freq": 200, "gain": -1, "q": 1}},
                  {"deess": {"intensity": 0.6}},
                  {"compress": {"threshold": -20, "ratio": 3.5, "attack": 10, "release": 200}}]},
    "music_bed": {
        "why": "A bed under dialogue only needs to stay out of its way: high-pass below the "
               "music's weight, low-pass the top, and two wide dips at 300 Hz and 2.5 kHz where "
               "speech lives. No denoise, de-ess or compression: the music is already mastered.",
        "chain": [{"highpass": 40}, {"lowpass": 16000},
                  {"eq": {"freq": 300, "gain": -2, "q": 0.7}},
                  {"eq": {"freq": 2500, "gain": -3, "q": 0.7}}]},
}
# biquads in double precision: in float a low high-pass leaves the mixed stems -80 dB off the mix
F64 = ":precision=f64"


class SpecError(ValueError):
    """The spec cannot be read or does not make sense."""


NULL_FLOOR_DBFS = -60.0          # DX + MX + FX against the mix must null below this
ROLES = ("dx", "mx", "fx")
DENOISE_RT_KEYS = ("nr", "nf", "window", "stream")
NF_ABOVE_DB = 15.0               # afftdn nf, when not given: the window's level plus this
MIN_PROFILE_S = 0.25
PROFILE_BLOCK = 1024


class MixError(RuntimeError):
    """ffmpeg failed, or a measurement could not be made."""


class NullError(MixError):
    """The stems do not sum to the mix. The files and render.json are written
    first; the manifest is on .manifest."""


# ---------------------------------------------------------------------------
# The spec: a small YAML subset, or JSON
# ---------------------------------------------------------------------------

def _split_top(text, sep=","):
    parts, depth, quote, cur = [], 0, None, []
    for ch in text:
        if quote:
            quote = None if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    tail = "".join(cur)
    if tail.strip() or parts:
        parts.append(tail)
    return parts


def _scalar(s):
    s = s.strip()
    if s in ("", "~", "null", "Null"):
        return None
    if s in ("true", "True"):
        return True
    if s in ("false", "False"):
        return False
    if s[0] in "\"'" and len(s) > 1 and s[-1] == s[0]:
        return s[1:-1]
    if s[0] == "[" and s[-1] == "]":
        return [_scalar(p) for p in _split_top(s[1:-1])]
    if s[0] == "{" and s[-1] == "}":
        out = {}
        for p in _split_top(s[1:-1]):
            k, sep, v = p.partition(":")
            if not sep:
                raise SpecError(f"bad flow map entry: {p.strip()!r}")
            out[_scalar(k)] = _scalar(v)
        return out
    for cast in (int, float):
        try:
            return cast(s)
        except ValueError:
            pass
    return s


def _strip_comment(line):
    quote = None
    for i, ch in enumerate(line):
        if quote:
            quote = None if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


_KEY = re.compile(r"^([^\s\[\]{},:'\"#-][^:]*|\"[^\"]*\"|'[^']*'):(\s+(.*))?$")


def _parse_block(lines, i, indent):
    n, text, _ = lines[i]
    if text == "-" or text.startswith("- "):
        out = []
        while i < len(lines) and lines[i][0] == indent and (
                lines[i][1] == "-" or lines[i][1].startswith("- ")):
            _, text, no = lines[i]
            rest = text[1:].strip()
            if not rest:
                if i + 1 >= len(lines) or lines[i + 1][0] <= indent:
                    out.append(None)
                    i += 1
                else:
                    val, i = _parse_block(lines, i + 1, lines[i + 1][0])
                    out.append(val)
            elif _KEY.match(rest) and rest[0] not in "[{":
                lines[i] = (indent + 2, rest, no)  # the rest of the line opens a map
                val, i = _parse_block(lines, i, indent + 2)
                out.append(val)
            else:
                out.append(_scalar(rest))
                i += 1
        return out, i
    out = {}
    while i < len(lines) and lines[i][0] == indent:
        _, text, no = lines[i]
        m = _KEY.match(text)
        if not m:
            raise SpecError(f"line {no}: expected 'key: value', got {text!r}")
        key = _scalar(m.group(1))
        rest = (m.group(3) or "").strip()
        if key in out:
            raise SpecError(f"line {no}: key {key!r} given twice")
        if rest:
            out[key] = _scalar(rest)
            i += 1
        elif i + 1 < len(lines) and (lines[i + 1][0] > indent or (
                lines[i + 1][0] == indent and lines[i + 1][1].startswith("-"))):
            out[key], i = _parse_block(lines, i + 1, lines[i + 1][0])
        else:
            out[key] = None
            i += 1
    return out, i


def parse_yaml(text):
    lines = []
    for no, raw in enumerate(text.splitlines(), 1):
        body = _strip_comment(raw).rstrip()
        if not body.strip():
            continue
        if "\t" in body[:len(body) - len(body.lstrip())]:
            raise SpecError(f"line {no}: tabs are not allowed in indentation")
        lines.append((len(body) - len(body.lstrip()), body.strip(), no))
    if not lines:
        raise SpecError("the spec is empty")
    val, i = _parse_block(lines, 0, lines[0][0])
    if i < len(lines):
        raise SpecError(f"line {lines[i][2]}: unexpected indentation")
    return val


def load_spec(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    try:
        spec = json.loads(text) if text.lstrip()[:1] in ("{", "[") else parse_yaml(text)
    except ValueError as e:
        if isinstance(e, SpecError):
            raise
        raise SpecError(f"{path}: not valid JSON ({e})")
    return validate_spec(spec, os.path.dirname(os.path.abspath(path)))


# ---------------------------------------------------------------------------
# Steps: each returns a list of ffmpeg filters
# ---------------------------------------------------------------------------

def _num(v, name, lo=None, hi=None):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise SpecError(f"{name} must be a number, got {v!r}")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise SpecError(f"{name} {v} is outside {lo} to {hi}")
    return float(v)


def _f(x):
    return f"{x:.6f}".rstrip("0").rstrip(".") or "0"


def _lin(db):
    return _f(10 ** (db / 20.0))


def _params(raw, main, allowed, step):
    p = dict(raw) if isinstance(raw, dict) else ({} if raw is None else {main: raw})
    bad = sorted(set(p) - set(allowed))
    if bad:
        raise SpecError(f"{step}: unknown setting {', '.join(map(str, bad))} "
                        f"(allowed: {', '.join(sorted(allowed))})")
    return p


class Ctx:
    """What a step may need: the rate, how long the signal is here, and a
    place to say what was skipped. probe is False when only the settings
    are being checked, so no delay is measured."""

    def __init__(self, rate, duration, skips, where, probe=True):
        self.rate, self.duration, self.skips, self.where = rate, duration, skips, where
        self.probe = probe
        self.speech_gain = None  # speechnorm's measured gain, set once the mix is measured

    def skip(self, step, why):
        self.skips.append({"where": self.where, "step": step, "why": why})
        return []


def s_gain(raw, ctx):
    p = _params(raw, "db", ("db",), "gain")
    return [f"volume={_f(_num(p.get('db', 0), 'gain db', -60, 40))}dB"]


def s_fade(raw, ctx):
    p = _params(raw, "in", ("in", "out", "curve"), "fade")
    out = []
    curve = p.get("curve", "tri")
    if "in" in p:
        out.append(f"afade=t=in:st=0:d={_f(_num(p['in'], 'fade in', 0.001, 600))}:curve={curve}")
    if "out" in p:
        d = _num(p["out"], "fade out", 0.001, 600)
        dur = d if ctx.duration is None else ctx.duration  # None: checking settings only
        if d > dur:
            raise SpecError(f"{ctx.where}: fade out of {d} s is longer than the signal")
        out.append(f"afade=t=out:st={_f(dur - d)}:d={_f(d)}:curve={curve}")
    if not out:
        raise SpecError("fade needs in or out")
    return out


def s_highpass(raw, ctx):
    p = _params(raw, "freq", ("freq", "poles"), "highpass")
    return [f"highpass=f={_f(_num(p.get('freq', 80), 'highpass freq', 10, 2000))}"
            f":p={int(_num(p.get('poles', 2), 'poles', 1, 2))}{F64}"]


def s_lowpass(raw, ctx):
    p = _params(raw, "freq", ("freq", "poles"), "lowpass")
    if _num(p.get("freq", 16000), "lowpass freq", 100, 24000) >= ctx.rate / 2:
        raise SpecError(f"lowpass freq {p.get('freq', 16000)} is at or above half the "
                        f"{ctx.rate} Hz sample rate")
    return [f"lowpass=f={_f(_num(p.get('freq', 16000), 'lowpass freq', 100, 24000))}"
            f":p={int(_num(p.get('poles', 2), 'poles', 1, 2))}{F64}"]


def s_eq(raw, ctx):
    p = _params(raw, "freq", ("type", "freq", "gain", "q"), "eq")
    f = _f(_num(p.get("freq", 1000), "eq freq", 20, 20000))
    g = _f(_num(p.get("gain", 0), "eq gain", -24, 24))
    kind = p.get("type", "peak")
    q = _f(_num(p.get("q", 1.0), "eq q", 0.05, 20))
    if kind == "peak":
        return [f"equalizer=f={f}:t=q:w={q}:g={g}{F64}"]
    if kind in ("lowshelf", "highshelf"):
        return [f"{kind}=f={f}:g={g}{F64}"]
    raise SpecError(f"eq type {kind!r}: use peak, lowshelf or highshelf")


def s_dehum(raw, ctx):
    p = _params(raw, "freq", ("freq", "harmonics", "q"), "dehum")
    f = _num(p.get("freq", 60), "dehum freq", 40, 70)
    h = int(_num(p.get("harmonics", 4), "dehum harmonics", 1, 20))
    q = _f(_num(p.get("q", 10), "dehum q", 1, 100))
    return [f"bandreject=f={_f(f * k)}:width_type=q:w={q}{F64}" for k in range(1, h + 1)]


def s_declick(raw, ctx):
    p = _params(raw, "threshold", ("threshold", "window"), "declick")
    return _aligned(f"adeclick=w={_f(_num(p.get('window', 55), 'declick window', 10, 100))}"
                    f":t={_f(_num(p.get('threshold', 2), 'declick threshold', 1, 100))}", ctx)


def s_deess(raw, ctx):
    p = _params(raw, "intensity", ("intensity", "max", "freq"), "deess")
    i = _f(_num(p.get("intensity", 0.5), "deess intensity", 0, 1))
    m = _f(_num(p.get("max", 0.5), "deess max", 0, 1))
    fr = _f(_num(p.get("freq", 0.5), "deess freq", 0, 1))
    return [f"deesser=i={i}:m={m}:f={fr}:s=o"]


def s_compress(raw, ctx):
    p = _params(raw, "ratio", ("threshold", "ratio", "attack", "release", "makeup"), "compress")
    return [f"acompressor=threshold={_lin(_num(p.get('threshold', -18), 'threshold', -60, 0))}"
            f":ratio={_f(_num(p.get('ratio', 3), 'ratio', 1, 20))}"
            f":attack={_f(_num(p.get('attack', 20), 'attack', 0.01, 2000))}"
            f":release={_f(_num(p.get('release', 250), 'release', 0.01, 9000))}"
            f":makeup={_lin(_num(p.get('makeup', 0), 'makeup', 0, 36))}"]


def _deepfilter():
    return shutil.which("deep-filter") or shutil.which("deepFilter")


@functools.lru_cache(maxsize=None)
def delay_samples(filt, rate):
    """How many samples `filt` delays a signal at `rate`, measured by cross-
    correlating a pulsed noise against what comes out. afftdn's and anlmdn's
    windows follow the rate (not exactly in proportion) and ffmpeg's version."""
    n = 2 * rate
    t = np.arange(n) / rate
    x = (np.random.default_rng(3).uniform(-0.5, 0.5, n)
         * (1 - 0.9 * (0.5 + 0.5 * np.sin(2 * np.pi * 4 * t)))).astype("<f4")
    cmd = [dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-f", "f32le", "-ar", str(rate),
           "-ac", "1", "-i", "-", "-af",
           f"{filt},aformat=sample_fmts=flt:sample_rates={rate}:channel_layouts=mono",
           "-f", "f32le", "-"]
    try:
        proc = subprocess.run(cmd, input=x.tobytes(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=RUN_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise MixError(f"ffmpeg timed out measuring the delay of {filt}")
    y = np.frombuffer(proc.stdout, "<f4").astype(np.float64)
    if proc.returncode != 0 or len(y) < n // 2:
        raise MixError(f"could not measure the delay of {filt}: "
                       + proc.stderr.decode(errors="replace").strip()[-300:])
    y = y[:n]
    m = 1 << (2 * n - 1).bit_length()
    c = np.fft.irfft(np.fft.rfft(y, m) * np.conj(np.fft.rfft(x, m)), m)[:int(MAX_DELAY_S * rate)]
    lag = int(np.argmax(c))
    if c[lag] < 0.3 * np.linalg.norm(x) * np.linalg.norm(y):
        raise MixError(f"{filt} at {rate} Hz: no clear delay to take out (it changed the "
                       f"signal too much, or delays it more than {MAX_DELAY_S} s)")
    return lag


def _aligned(filt, ctx):
    """filt with its delay taken out: the end padded by the delay first, the
    start trimmed by it after, so the output lines up with the input at the
    same length. The aformat brings arnndn (always 48 kHz out) back to the rate."""
    fmt = f"aformat=sample_rates={ctx.rate}"
    if not ctx.probe:
        return [filt, fmt]
    n = delay_samples(filt, ctx.rate)
    if not n:
        return [filt, fmt]
    return [f"apad=pad_len={n}", filt, fmt, f"atrim=start_sample={n}", "asetpts=PTS-STARTPTS"]


def s_denoise(raw, ctx):
    p = _params(raw, "method", ("method", "nr", "nf", "tn", "s", "model", "mix"), "denoise")
    method = p.get("method", "afftdn")
    if method == "candidates":
        return []  # only meaningful in a candidate render; the main render drops it
    if method == "afftdn":
        return _aligned(f"afftdn=nr={_f(_num(p.get('nr', 12), 'nr', 0.01, 97))}"
                        f":nf={_f(_num(p.get('nf', -50), 'nf', -80, -20))}"
                        f":tn={1 if p.get('tn') else 0}", ctx)
    if method == "anlmdn":
        return _aligned(f"anlmdn=s={_f(_num(p.get('s', 0.00001), 's', 0.00001, 10000))}", ctx)
    if method == "arnndn":
        model = p.get("model")
        if not model or not os.path.isfile(model):
            return ctx.skip("denoise arnndn", "needs a `model` file (an .rnnn); none given or found")
        return _aligned(f"arnndn=m={model.replace(chr(92), '/').replace(':', chr(92) + ':')}"
                        f":mix={_f(_num(p.get('mix', 1), 'mix', -1, 1))}", ctx)
    if method == "deepfilternet":
        if not _deepfilter():
            return ctx.skip("denoise deepfilternet", "deep-filter is not installed")
        ctx.skip("denoise deepfilternet", "installed, but run it on the input first and name "
                 "the result as the stem input; mixbuild does not shell out to it")
        return []
    raise SpecError(f"denoise method {method!r}: use afftdn, anlmdn, arnndn, deepfilternet "
                    "or candidates")


def s_loudnorm(raw, ctx):
    p = _params(raw, "i", ("i", "tp", "lra"), "loudnorm")
    return [f"loudnorm=I={_f(_num(p.get('i', -16), 'loudnorm i', -70, -5))}"
            f":TP={_f(_num(p.get('tp', -1.5), 'loudnorm tp', -9, 0))}"
            f":LRA={_f(_num(p.get('lra', 11), 'loudnorm lra', 1, 50))}"]


def s_speechnorm(raw, ctx):
    p = _params(raw, "key", ("key", "i", "tp", "gate_db"), "speechnorm")
    if not isinstance(p.get("key"), str):
        raise SpecError("speechnorm needs key: the stem whose speech sets the loudness")
    _num(p.get("i", -16), "speechnorm i", -70, -5)
    _num(p.get("tp", -1.5), "speechnorm tp", -9, 0)
    if p.get("gate_db") is not None:
        _num(p["gate_db"], "speechnorm gate_db", -120, 0)
    if not ctx.probe:
        return []
    if ctx.speech_gain is None:  # a dry run: nothing has been measured
        ctx.skip("speechnorm", "dry run: the gain is measured at render; 0 dB is shown")
    return [f"volume={_f(ctx.speech_gain or 0.0)}dB"]


def s_limiter(raw, ctx):
    p = _params(raw, "ceiling", ("ceiling", "attack", "release"), "limiter")
    ceil = _num(p.get("ceiling", -1.5), "limiter ceiling", -24, 0)
    # four times oversampled, so it limits inter-sample peaks, not just samples
    return [f"aresample={ctx.rate * 4}",
            f"alimiter=limit={_lin(ceil)}:level=disabled"
            f":attack={_f(_num(p.get('attack', 5), 'attack', 0.1, 80))}"
            f":release={_f(_num(p.get('release', 50), 'release', 1, 8000))}",
            f"aresample={ctx.rate}"]


STEPS = {"gain": s_gain, "fade": s_fade, "highpass": s_highpass, "lowpass": s_lowpass,
         "eq": s_eq, "dehum": s_dehum, "declick": s_declick, "deess": s_deess,
         "denoise": s_denoise, "compress": s_compress, "loudnorm": s_loudnorm,
         "speechnorm": s_speechnorm, "limiter": s_limiter}


def _step(item):
    if isinstance(item, str):
        return item, None
    if not isinstance(item, dict) or len(item) != 1:
        raise SpecError(f"a chain step is one name with its settings, got {item!r}")
    (name, raw), = item.items()
    if name not in STEPS:
        raise SpecError(f"unknown step {name!r} (known: {', '.join(sorted(STEPS))})")
    return name, raw


def _check_chain(steps, rate, where):
    """Every step's settings, checked now rather than halfway through a render."""
    ctx = Ctx(rate, None, [], where, probe=False)
    for item in steps:
        name, raw = _step(item)
        STEPS[name](raw, ctx)


ROOMTONE_KEYS = ("source", "stream", "window", "gaps", "min_gap", "xfade", "gap_level_db",
                 "points", "edit_pad_s", "edit_level_db")
MAX_FILLS = 100  # gaps and points together: the envelope is built per span


def _roomtone_cfg(raw, base, name):
    cfg = {} if raw is None else raw
    if not isinstance(cfg, dict):
        raise SpecError(f"stem {name}: roomtone is a map of settings ({{}} for the defaults)")
    bad = sorted(set(cfg) - set(ROOMTONE_KEYS))
    if bad:
        raise SpecError(f"stem {name}: roomtone: unknown setting {', '.join(map(str, bad))} "
                        f"(allowed: {', '.join(ROOMTONE_KEYS)})")
    out = dict(cfg)
    if out.get("source"):
        out["source"] = os.path.normpath(os.path.join(base, os.path.expanduser(str(out["source"]))))
    out["stream"] = int(_num(out.get("stream", 0), "roomtone stream", 0, 31))
    w = out.get("window")
    if w is not None:
        if not isinstance(w, list) or len(w) != 2:
            raise SpecError(f"stem {name}: roomtone window is [start, end] in seconds")
        a, b = _num(w[0], "roomtone window start", 0), _num(w[1], "roomtone window end", 0)
        if b - a < rt.MIN_SOURCE_S:
            raise SpecError(f"stem {name}: roomtone window is {b - a:g} s; a loop needs at "
                            f"least {rt.MIN_SOURCE_S:g} s")
        out["window"] = [a, b]
    g = out["gaps"] = out.get("gaps", "auto")
    if g != "auto":
        if not isinstance(g, list):
            raise SpecError(f"stem {name}: roomtone gaps is auto or a list of [start, end]")
        out["gaps"] = []
        for x in g:
            if not (isinstance(x, list) and len(x) == 2):
                _bad_gap(name, x)
            a, b = _num(x[0], "gap start", 0), _num(x[1], "gap end", 0)
            if b <= a:
                _bad_gap(name, x)
            out["gaps"].append([a, b])
    out["points"] = [_num(t, "edit point", 0) for t in (out.get("points") or [])]
    out["min_gap"] = _num(out.get("min_gap", 0.25), "roomtone min_gap", 0.02, 60)
    out["xfade"] = _num(out.get("xfade", 0.05), "roomtone xfade", 0.001, 5)
    out["edit_pad_s"] = _num(out.get("edit_pad_s", 0.5), "roomtone edit_pad_s", 0.01, 10)
    out["gap_level_db"] = _num(out.get("gap_level_db", 0), "roomtone gap_level_db", -40, 12)
    out["edit_level_db"] = _num(out.get("edit_level_db", -6), "roomtone edit_level_db", -40, 12)
    return out


def _dn_cfg(raw, name):
    cfg = {} if raw is None else raw
    if not isinstance(cfg, dict) or set(cfg) - set(DENOISE_RT_KEYS):
        raise SpecError(f"stem {name}: denoise_roomtone takes {', '.join(DENOISE_RT_KEYS)}")
    out = {"nr": _num(cfg.get("nr", 12), "denoise_roomtone nr", 0.01, 97),
           "nf": None if cfg.get("nf") is None else _num(cfg["nf"], "denoise_roomtone nf", -80, -10),
           "stream": int(_num(cfg.get("stream", 0), "denoise_roomtone stream", 0, 31))}
    w = cfg.get("window")
    if w is not None:
        if not isinstance(w, list) or len(w) != 2:
            raise SpecError(f"stem {name}: denoise_roomtone window is [start, end] in seconds")
        a, b = _num(w[0], "window start", 0), _num(w[1], "window end", 0)
        if b - a < MIN_PROFILE_S:
            raise SpecError(f"stem {name}: denoise_roomtone window is {b - a:g} s; a profile "
                            f"needs at least {MIN_PROFILE_S:g} s")
        out["window"] = [a, b]
    return out


def _bad_gap(name, x):
    raise SpecError(f"stem {name}: a roomtone gap is [start, end] with end after start, got {x!r}")


def validate_spec(spec, base):
    if not isinstance(spec, dict):
        raise SpecError("the spec must be a map")
    bad = sorted(set(spec) - {"sample_rate", "channels", "stems", "mix"})
    if bad:
        raise SpecError(f"unknown top-level key {', '.join(bad)}")
    spec["sample_rate"] = int(_num(spec.get("sample_rate", 48000), "sample_rate", 8000, 192000))
    spec["channels"] = int(_num(spec.get("channels", 2), "channels", 1, 2))
    stems = spec.get("stems")
    if not isinstance(stems, dict) or not stems:
        raise SpecError("stems: give at least one stem")
    for name, st in stems.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", str(name)) or "__" in str(name):
            raise SpecError(f"stem name {name!r}: letters, digits, _ and - only, no double underscore")
        if not isinstance(st, dict) or not st.get("input"):
            raise SpecError(f"stem {name}: needs an input file")
        bad = sorted(set(st) - {"input", "chain", "candidates", "preset", "roomtone", "role",
                                "denoise_roomtone"})
        if bad:
            raise SpecError(f"stem {name}: unknown key {', '.join(bad)}")
        st["input"] = os.path.normpath(os.path.join(base, os.path.expanduser(str(st["input"]))))
        if st.get("preset") is not None:
            if st["preset"] not in PRESETS:
                raise SpecError(f"stem {name}: unknown preset {st['preset']!r} "
                                f"(known: {', '.join(PRESETS)})")
            if st.get("chain"):
                raise SpecError(f"stem {name}: give a preset or a chain, not both")
            st["chain"] = copy.deepcopy(PRESETS[st["preset"]]["chain"])
        if "roomtone" in st:
            st["roomtone"] = _roomtone_cfg(st["roomtone"], base, name)
        if "role" in st:
            st["role"] = str(st["role"]).lower()
            if st["role"] not in ROLES:
                raise SpecError(f"stem {name}: role {st['role']!r}: use dx, mx or fx")
        if "denoise_roomtone" in st:
            if st.get("role", "dx") != "dx":
                raise SpecError(f"stem {name}: denoise_roomtone is for dialogue (role dx) only")
            st["denoise_roomtone"] = _dn_cfg(st["denoise_roomtone"], name)
        st["chain"] = st.get("chain") or []
        if not isinstance(st["chain"], list):
            raise SpecError(f"stem {name}: chain must be a list")
        if any(_step(i)[0] == "speechnorm" for i in st["chain"]):
            raise SpecError(f"stem {name}: speechnorm belongs in the mix chain")
        _check_chain(st["chain"], spec["sample_rate"], f"stem {name}")
        cands = st.get("candidates") or {}
        if not isinstance(cands, dict):
            raise SpecError(f"stem {name}: candidates must be a map of name to denoise settings")
        for c in cands:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", str(c)) or "__" in str(c):
                raise SpecError(f"candidate name {c!r}: letters, digits, _ and - only, no double underscore")
            _check_chain([{"denoise": cands[c]}], spec["sample_rate"], f"stem {name} candidate {c}")
        if cands and not any(_step(i)[0] == "denoise" and isinstance(_step(i)[1], dict)
                             and _step(i)[1].get("method") == "candidates" for i in st["chain"]):
            raise SpecError(f"stem {name}: candidates need a `denoise: {{method: candidates}}` step")
        st["candidates"] = cands
    unroled = [n for n, st in stems.items() if "role" not in st]
    if len(unroled) < len(stems) and unroled:
        raise SpecError(f"stems without a role: {', '.join(unroled)} (roles are all or none: the "
                        "stems must cover the mix for the sum to null)")
    mix = spec.get("mix") or {}
    bad = sorted(set(mix) - {"gains", "duck", "chain"})
    if bad:
        raise SpecError(f"mix: unknown key {', '.join(bad)}")
    for name in mix.get("gains") or {}:
        if name not in stems:
            raise SpecError(f"mix gains names a stem that does not exist: {name}")
    duck = mix.get("duck")
    if duck:
        bad = sorted(set(duck) - {"key", "under", "depth", "gate_db"})
        if bad:
            raise SpecError(f"duck: unknown key {', '.join(bad)} (the duck is keyed on speech "
                            "activity: give depth in dB, and gate_db if the gate needs setting)")
        under = duck.get("under")
        under = [under] if isinstance(under, str) else under
        if duck.get("key") not in stems or not under or any(u not in stems for u in under) \
                or duck["key"] in under:
            raise SpecError("duck: key and under must be different stems of this spec")
        duck["under"] = under
        duck["depth"] = _num(duck.get("depth", dp.DEPTH_DB), "duck depth", 0.1, dp.MAX_DEPTH_DB)
        if duck.get("gate_db") is not None:
            duck["gate_db"] = _num(duck["gate_db"], "duck gate_db", -120, 0)
    mix["chain"] = mix.get("chain") or []
    _check_chain(mix["chain"], spec["sample_rate"], "mix")
    sn = [(i, _step(x)[1]) for i, x in enumerate(mix["chain"]) if _step(x)[0] == "speechnorm"]
    if sn:
        if len(sn) > 1 or any(_step(i)[0] == "loudnorm" for i in mix["chain"]):
            raise SpecError("mix: one speechnorm, and not with loudnorm (it replaces it)")
        # a bare `speechnorm: dialogue` is the key; make it the map the rest reads
        mix["chain"][sn[0][0]] = {"speechnorm": _params(sn[0][1], "key", ("key", "i", "tp", "gate_db"),
                                                         "speechnorm")}
        if mix["chain"][sn[0][0]]["speechnorm"].get("key") not in stems:
            raise SpecError("speechnorm: key must be a stem of this spec")
    spec["mix"] = mix
    return spec


# ---------------------------------------------------------------------------
# Graphs
# ---------------------------------------------------------------------------

def _head(spec):
    layout = "stereo" if spec["channels"] == 2 else "mono"
    return f"aformat=sample_fmts=fltp:sample_rates={spec['sample_rate']}:channel_layouts={layout}"


def chain_filters(steps, spec, duration, where, skips, candidate=None, speech_gain=None):
    """The filters for a chain, split at the loudnorm step (at most one):
    (before, loudnorm_or_None, after). speech_gain is speechnorm's measured dB."""
    ctx = Ctx(spec["sample_rate"], duration, skips, where)
    ctx.speech_gain = speech_gain
    parts = [[], None, []]
    seen_ln = False
    for item in steps:
        name, raw = _step(item)
        if name == "denoise" and isinstance(raw, dict) and raw.get("method") == "candidates":
            fl = STEPS["denoise"](candidate, ctx) if candidate else []
        else:
            fl = STEPS[name](raw, ctx)
        if name == "loudnorm":
            if seen_ln:
                raise SpecError(f"{where}: one loudnorm per chain")
            seen_ln = True
            parts[1] = fl[0]
        else:
            parts[2 if seen_ln else 0].extend(fl)
    return parts


def probe_duration(path):
    proc = dc._run([dc.FFPROBE, "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=nw=1:nk=1", dc._src(path)], dc.PROBE_TIMEOUT_S)
    try:
        return float(proc.stdout.strip())
    except ValueError:
        raise MixError(f"ffprobe could not read the length of {path}: {proc.stderr.strip()[-200:]}")


def _ffmpeg(inputs, graph, out, extra=(), level="error"):
    cmd = [dc.FFMPEG, "-hide_banner", "-nostdin", "-nostats", "-v", level, "-y",
           "-threads", "1", "-filter_threads", "1", "-filter_complex_threads", "1"]
    for p in inputs:
        cmd += ["-i", dc._src(p)]
    cmd += ["-filter_complex", graph, "-map", "[out]", "-map_metadata", "-1",
            "-fflags", "+bitexact", "-flags:a", "+bitexact", *extra]
    cmd += ["-c:a", BIT_DEPTH, "-f", "wav", out]
    return dc._run(cmd, RUN_TIMEOUT_S)


def _loudnorm_pass1(inputs, graph_before, ln):
    """Measure the signal where loudnorm sits; returns the measured values."""
    cmd = [dc.FFMPEG, "-hide_banner", "-nostdin", "-nostats", "-threads", "1", "-filter_threads", "1",
           "-filter_complex_threads", "1"]
    for p in inputs:
        cmd += ["-i", dc._src(p)]
    g = f"{graph_before},{ln}:print_format=json[out]"
    proc = dc._run(cmd + ["-filter_complex", g, "-map", "[out]", "-f", "null", "-"], RUN_TIMEOUT_S)
    m = list(re.finditer(r"\{[^{}]*\}", proc.stderr))
    if proc.returncode != 0 or not m:
        raise MixError("loudnorm could not measure: " + proc.stderr.strip()[-300:])
    got = json.loads(m[-1].group(0))
    for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset"):
        if got.get(k) in ("-inf", "inf", None):
            raise MixError(f"loudnorm read {k}={got.get(k)}: the signal there is silent")
    return got


def render_graph(inputs, head, parts, out_path, fmt, where, skips, measured=None):
    """Render `head` (a filter_complex that ends in an unlabelled filter)
    plus the chain `parts` to out_path atomically, ending in `fmt` (loudnorm
    puts out 192 kHz). A loudnorm runs two-pass: measure the chain up to it
    (or take `measured`, so a mixed stem gets the mix's gain), then apply it
    linearly; when loudnorm falls back to dynamic that goes into skips.
    Returns (graph, None or {measured, linear})."""
    before, ln, after = parts
    body = ",".join([head, *before])
    got = None
    if ln:
        got = measured or _loudnorm_pass1(inputs, body, ln)
        ln = (f"{ln}:measured_I={got['input_i']}:measured_TP={got['input_tp']}"
              f":measured_LRA={got['input_lra']}:measured_thresh={got['input_thresh']}"
              f":offset={got['target_offset']}:linear=true:print_format=json")
        body += "," + ln
    graph = ",".join([body, *after, fmt]) + "[out]"
    part = out_path + ".part"
    try:
        proc = _ffmpeg(inputs, graph, part, level="info" if ln else "error")
        if proc.returncode != 0:
            raise MixError("ffmpeg failed: " + proc.stderr.strip()[-400:])
        os.replace(part, out_path)
    finally:
        if os.path.exists(part):
            os.remove(part)
    if not ln:
        return graph, None
    kind = dc._loudnorm_json(proc.stderr).get("normalization_type")
    if kind != "linear":
        skips.append({"where": where, "step": "loudnorm",
                      "why": f"fell back to {kind}: a linear gain would have pushed the true "
                             "peak over tp or the range over lra, so it compressed. Listen "
                             "before using it"})
    return graph, {"measured": got, "linear": kind == "linear"}


def mix_graph(spec, duration, names, solo=None):
    """(head, inputs): the mix filter_complex up to the chain, and what each
    input reads, a stem name or DUCK_KEY (the speech-activity key from
    duckplan). The key is read once per ducked stem, as its own input, not
    split: a split feeding two consumers made the render vary from run to
    run. With solo, only that stem plays, with the gain and ducking it has in
    the mix: a mixed stem."""
    mix = spec["mix"]
    gains = mix.get("gains") or {}
    duck = mix.get("duck")
    head = _head(spec)
    play = [solo] if solo else list(names)
    under = [n for n in play if duck and n in duck["under"]]
    inputs = play + [DUCK_KEY] * len(under)
    # every input runs a second past the end and is cut back after the mix: a stream
    # that ends inside a block makes the last block of a two-input filter vary
    dur = _f(round(duration, 3))
    pad = _f(round(duration + 1, 3))
    # the mono key copied to each channel at unity: an upmix would put it 3 dB down,
    # and the duck 3 dB short
    key_head = f"aformat=sample_fmts=fltp:sample_rates={spec['sample_rate']}:channel_layouts=mono" + (
        ",pan=stereo|c0=c0|c1=c0" if spec["channels"] == 2 else "")
    lines = []
    for i, n in enumerate(inputs):
        g = _f(_num(gains.get(n, 0), "mix gain", -60, 40)) if i < len(play) else "0"
        h = head if i < len(play) else key_head
        # fixed frame sizes: the limiter and loudnorm behind are block dependent
        lines.append(f"[{i}:a]{h},volume={g}dB,apad=whole_dur={pad},asetnsamples=n=1024:p=1[g{i}]")
    final = []
    for i, n in enumerate(play):
        if n in under:
            k = len(play) + under.index(n)
            lines.append(f"[g{i}][g{k}]{dp.sidechain_filter(duck['depth'])},"
                         f"asetnsamples=n=1024:p=1[d{i}]")
            final.append(f"d{i}")
        else:
            final.append(f"g{i}")
    lines.append("".join(f"[{x}]" for x in final)
                 + f"amix=inputs={len(final)}:normalize=0:duration=longest:dropout_transition=0,"
                 f"atrim=end={dur},asetnsamples=n=4096:p=0")
    return ";".join(lines), inputs


def linear_prefix(steps, loudnorm_linear):
    """The mix chain's steps up to the first that does not distribute over a
    sum, and that step's name (None when all do)."""
    for i, item in enumerate(steps):
        name = _step(item)[0]
        if name not in LINEAR and not (name == "loudnorm" and loudnorm_linear):
            return steps[:i], name
    return steps, None


# ---------------------------------------------------------------------------
# Dialogue-gated loudness
# ---------------------------------------------------------------------------

def _ffmpeg_bytes(cmd, data=None):
    try:
        return subprocess.run(cmd, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=RUN_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise MixError("ffmpeg timed out")


def _read_graph(inputs, graph, channels):
    """The graph's output as float samples, (n, channels), unclipped."""
    cmd = [dc.FFMPEG, "-hide_banner", "-nostdin", "-nostats", "-v", "error", "-threads", "1",
           "-filter_threads", "1", "-filter_complex_threads", "1"]
    for p in inputs:
        cmd += ["-i", dc._src(p)]
    proc = _ffmpeg_bytes(cmd + ["-filter_complex", graph, "-map", "[out]", "-f", "f32le", "-"])
    if proc.returncode != 0:
        raise MixError("ffmpeg could not read the mix: " + proc.stderr.decode(errors="replace")[-300:])
    return np.frombuffer(proc.stdout, "<f4").reshape(-1, channels)


def _ebur(x, rate):
    """{integrated_lufs, true_peak_dbtp} of float samples (n, channels)."""
    proc = _ffmpeg_bytes([dc.FFMPEG, "-hide_banner", "-nostdin", "-nostats", "-v", "info", "-f", "f32le",
                          "-ar", str(rate), "-ac", str(x.shape[1]), "-i", "-", "-af",
                          "ebur128=peak=true", "-f", "null", "-"], x.astype("<f4").tobytes())
    try:
        got = dc.parse_ebur128(proc.stderr.decode(errors="replace"))
    except dc.CheckError as e:
        raise MixError(f"could not measure loudness: {e}")
    if got["integrated_lufs"] is None or got["integrated_lufs"] < -69.9:
        raise MixError("the speech spans hold no measurable loudness (under 400 ms, or silent)")
    return got


def speech_loudness(x, rate, spans):
    """({speech, whole} loudness dicts, speech seconds): the mix x measured
    over the speech spans only, joined end to end, and over all of it."""
    cut = [x[int(round(a * rate)):int(round(b * rate))] for a, b in spans]
    cut = [c for c in cut if len(c)]
    if not cut:
        raise MixError("no speech spans fall inside the mix")
    joined = np.concatenate(cut)
    return {"speech": _ebur(joined, rate), "whole": _ebur(x, rate)}, len(joined) / rate


# ---------------------------------------------------------------------------
# Room-tone fill
# ---------------------------------------------------------------------------

def find_gaps(path, stream, min_gap):
    """[[start, end]] of the runs of digital silence of min_gap seconds or more."""
    fp = dxm.frame_power(path, stream, 0.01)
    hop = fp["hop_s"]
    a, b = ar._runs(fp["power"] <= 0)
    return [[round(i * hop, 3), round(j * hop, 3)] for i, j in zip(a, b) if (j - i) * hop >= min_gap]


def fill_envelope(n, rate, gaps, points, cfg):
    """The bed's gain over time, mono: 1 (less gap_level_db) through each gap
    with a linear ramp of xfade either side, edit_level_db at an edit point
    falling away linearly over edit_pad_s. Overlaps take the larger."""
    env = np.zeros(n)
    x, pad = cfg["xfade"], cfg["edit_pad_s"]
    gl, el = 10 ** (cfg["gap_level_db"] / 20), 10 ** (cfg["edit_level_db"] / 20)
    for a, b in gaps:
        i, j = max(0, int((a - x) * rate)), min(n, int((b + x) * rate) + 1)
        t = np.arange(i, j) / rate
        env[i:j] = np.maximum(env[i:j], gl * np.clip((t - (a - x)) / x, 0, 1)
                              * np.clip(((b + x) - t) / x, 0, 1))
    for q in points:
        i, j = max(0, int((q - pad) * rate)), min(n, int((q + pad) * rate) + 1)
        t = np.arange(i, j) / rate
        env[i:j] = np.maximum(env[i:j], el * np.clip(1 - np.abs(t - q) / pad, 0, 1))
    return env


def fill_roomtone(name, src, cfg, out_dir, spec, skips):
    """Lay matched room tone into the gaps and edit points of one stem's
    input. Returns (path to use as the stem's input, record). Nothing to
    fill: the input itself, and a note in skips."""
    stream = cfg["stream"]
    dur = probe_duration(src)
    gaps = cfg["gaps"] if cfg["gaps"] != "auto" else find_gaps(src, stream, cfg["min_gap"])
    points = cfg["points"]
    if not gaps and not points:
        skips.append({"where": f"stem {name}", "step": "roomtone",
                      "why": "no digital-silence gaps and no edit points: the input is unchanged"})
        return src, None
    if len(gaps) + len(points) > MAX_FILLS:
        raise MixError(f"stem {name}: {len(gaps)} gaps and {len(points)} points; at most "
                       f"{MAX_FILLS}. Raise roomtone min_gap or give the gaps")
    bed_src = cfg.get("source") or src
    sub = os.path.join(out_dir, "roomtone")
    os.makedirs(sub, exist_ok=True)
    win = cfg.get("window") or [None, None]
    try:
        bed = rt.bed(bed_src, os.path.join(sub, f"{name}-bed.wav"), dur + 1, stream, win[0], win[1])
    except (ar.ReportError, ValueError, OutputRefused) as e:
        raise MixError(f"stem {name}: room tone: {e}")
    if not bed["seam_ok"]:
        skips.append({"where": f"stem {name}", "step": "roomtone",
                      "why": f"the bed's loop seam is {bed['seam_dev_db']:+} dB (limit "
                             f"{rt.SEAM_MAX_DB} dB): listen to the fill"})
    brate = bed["sample_rate"]
    chans = ar.audio_stream(os.path.join(sub, f"{name}-bed.wav"))["channels"]
    raw = _ffmpeg_bytes([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-i",
                         dc._src(os.path.join(sub, f"{name}-bed.wav")), "-f", "f32le", "-"])
    if raw.returncode != 0:
        raise MixError(f"stem {name}: could not read the bed: {raw.stderr.decode(errors='replace')[-200:]}")
    x = np.frombuffer(raw.stdout, "<f4").reshape(-1, chans)
    env = fill_envelope(len(x), brate, gaps, points, cfg).astype(np.float32)
    fill = os.path.join(sub, f"{name}-fill.wav")
    dp._ffmpeg_write([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "f32le",
                      "-ar", str(brate), "-ac", str(chans), "-i", "-", "-map_metadata", "-1",
                      "-fflags", "+bitexact", "-flags:a", "+bitexact", "-c:a", "pcm_f32le",
                      "-f", "wav"], fill, (x * env[:, None]).tobytes())
    # laid under the input with the mix recipe: padded, fixed frames, cut back after
    h = _head(spec)
    pad, d = _f(round(dur + 1, 3)), _f(round(dur, 3))
    ends = f"apad=whole_dur={pad},asetnsamples=n=1024:p=1"
    graph = (f"[0:a]{h},{ends}[g0];[1:a]{h},{ends}[g1];[g0][g1]amix=inputs=2:normalize=0:"
             f"duration=longest:dropout_transition=0,atrim=end={d},asetnsamples=n=4096:p=0")
    out = os.path.join(sub, f"{name}-filled.wav")
    render_graph([src, fill], graph, ([], None, []), out, h, f"stem {name} roomtone", skips)
    rec = {"source": bed_src, "stream": stream, "window_s": bed["window_s"], "bed_curve": bed["curve"],
           "seam_dev_db": bed["seam_dev_db"], "gaps": gaps, "points": points,
           "gap_level_db": cfg["gap_level_db"], "edit_level_db": cfg["edit_level_db"],
           "xfade_s": cfg["xfade"], "edit_pad_s": cfg["edit_pad_s"]}
    return out, rec


def denoise_roomtone(name, src, cfg, out_dir, spec, skips):
    """afftdn on one stem's input with the noise profile learned from a room-
    tone window. The window is played ahead of the stem (cut to whole blocks,
    so asendcmd's stop lands on a frame edge before any stem sample), sample_noise
    runs over it, and the window and the filter's delay are trimmed off after.
    Returns (path, record)."""
    win = cfg.get("window")
    if win is None:
        try:
            row = rt.find_stream(src, cfg["stream"])
        except (ar.ReportError, ValueError) as e:
            raise MixError(f"stem {name}: denoise_roomtone: {e}")
        if row["status"] != "ok":
            raise MixError(f"stem {name}: denoise_roomtone: no room-tone window ({row['note']}); "
                           "give window: [start, end]")
        win = [row["start_s"], row["end_s"]]
    rate = spec["sample_rate"]
    if win[1] > probe_duration(src) + 0.01:
        raise MixError(f"stem {name}: denoise_roomtone window {win} runs past the end of the input")
    k = int((win[1] - win[0]) * rate) // PROFILE_BLOCK * PROFILE_BLOCK
    if k < MIN_PROFILE_S * rate:
        raise MixError(f"stem {name}: denoise_roomtone window {win} is under {MIN_PROFILE_S:g} s")
    a = int(round(win[0] * rate))
    nf = cfg["nf"]
    if nf is None:
        fp = dxm.frame_power(src, cfg["stream"], rt.HOP_S)
        i, j = int(win[0] / fp["hop_s"]), max(int(win[1] / fp["hop_s"]), int(win[0] / fp["hop_s"]) + 1)
        level = rt._db(float(fp["power"][i:j].mean()))
        if level is None:
            raise MixError(f"stem {name}: denoise_roomtone window {win} is digital silence: "
                           "there is no noise to learn")
        nf = min(-10.0, round(level + NF_ABOVE_DB, 1))
    filt = f"afftdn=nr={_f(cfg['nr'])}:nf={_f(nf)}:tn=0"
    n = delay_samples(filt, rate)
    h = _head(spec)
    stop = _f((k - PROFILE_BLOCK / 2) / rate)  # half a block early: no rounding in the string can move it a block
    graph = (f"[1:a:{cfg['stream']}]{h},atrim=start_sample={a}:end_sample={a + k},asetpts=PTS-STARTPTS,"
             f"asetnsamples=n={PROFILE_BLOCK}:p=0[w];[0:a]{h},asetnsamples=n={PROFILE_BLOCK}:p=0[s];"
             f"[w][s]concat=n=2:v=0:a=1,asendcmd=c='0 afftdn sn start;{stop} afftdn sn stop',"
             f"apad=pad_len={n},{filt},atrim=start_sample={k + n},asetpts=PTS-STARTPTS")
    out = os.path.join(_mkdir(os.path.join(out_dir, "roomtone")), f"{name}-denoised.wav")
    gr, _ = render_graph([src, src], graph, ([], None, []), out, h, f"stem {name} denoise_roomtone", skips)
    return out, {"window_s": [round(a / rate, 3), round((a + k) / rate, 3)], "nr": cfg["nr"],
                 "nf": nf, "delay_samples": n, "graph": gr}


def _read_file(path, channels):
    proc = _ffmpeg_bytes([dc.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-i", dc._src(path),
                          "-f", "f32le", "-"])
    if proc.returncode != 0:
        raise MixError(f"could not read {path}: {proc.stderr.decode(errors='replace')[-200:]}")
    return np.frombuffer(proc.stdout, "<f4").astype(np.float64).reshape(-1, channels)


def export_stems(spec, out_dir, mixed, mix_out, mix_dur, stop, skips):
    """(record, {label: path}): DX, MX, FX and ME written from the mixed stems,
    and the sum-null of DX + MX + FX against the mix."""
    stems = spec["stems"]
    by = {r: [n for n in stems if stems[n]["role"] == r] for r in ROLES}
    groups = {r.upper(): by[r] for r in ROLES if by[r]}
    if by["mx"] or by["fx"]:
        groups["ME"] = by["mx"] + by["fx"]
    h = _head(spec)
    ends = f"apad=whole_dur={_f(round(mix_dur + 1, 3))},asetnsamples=n=1024:p=1"
    files = {}
    for label, ns in groups.items():
        files[label] = os.path.join(_mkdir(os.path.join(out_dir, "export")), f"{label}.wav")
        graph = ";".join(f"[{i}:a]{h},{ends}[g{i}]" for i in range(len(ns))) + ";" + \
            "".join(f"[g{i}]" for i in range(len(ns))) + \
            f"amix=inputs={len(ns)}:normalize=0:duration=longest:dropout_transition=0," \
            f"atrim=end={_f(round(mix_dur, 3))},asetnsamples=n=4096:p=0"
        render_graph([mixed[n] for n in ns], graph, ([], None, []), files[label], h,
                     f"export {label}", skips)
    ch = spec["channels"]
    parts = [_read_file(files[r.upper()], ch) for r in ROLES if by[r]]
    mix = _read_file(mix_out, ch)
    rec = {"floor_dbfs": NULL_FLOOR_DBFS, "roles": {r: by[r] for r in ROLES},
           "files": sorted(files)}
    if any(len(x) != len(mix) for x in parts):
        rec.update(residual_dbfs=None, ok=False, reason="the stems and the mix differ in length")
        return rec, files
    peak = float(np.abs(sum(parts) - mix).max())
    resid = round(20 * np.log10(peak), 1) if peak > 0 else -200.0
    rec.update(residual_dbfs=resid, ok=bool(resid <= NULL_FLOOR_DBFS))
    if not rec["ok"]:
        rec["reason"] = (f"{stop} on the mix chain is not linear, so the stems, which stop before "
                         "it, cannot sum to the mix" if stop else
                         "the stems do not sum to the mix; something on the mix bus is not linear")
    return rec, files


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _ffmpeg_version():
    proc = dc._run([dc.FFMPEG, "-version"], 30)
    return (proc.stdout.splitlines() or ["?"])[0]


def _check_targets(out_dir, targets, sources):
    if in_any_git_tree(out_dir):
        raise OutputRefused(f"{out_dir} is inside a git working tree. Renders name media; "
                            "write them outside any repository.")
    src = {os.path.realpath(s) for s in sources}
    for t in targets:
        if os.path.realpath(t) in src:
            raise OutputRefused(f"{t} is a source file. Mixes are rendered on copies, never over an input.")


def _flat(head, parts, fmt):
    before, ln, after = parts
    return ",".join([head, *before, *([ln] if ln else []), *after, fmt]) + "[out]"


def render(spec, out_dir, spec_path=None, report=False, dry_run=False):
    """Render every stem (and candidate) and the mix into out_dir. Returns
    the manifest dict, also written as render.json (not on dry_run)."""
    dc.check_tools(ffmpeg=True)
    stems = spec["stems"]
    names = list(stems)
    plan = []  # (kind, key, source file, out path, candidate settings)
    for n in names:
        st = stems[n]
        plan.append(("stem", n, st["input"], os.path.join(out_dir, "stems", f"{n}.wav"), None))
        for c, settings in st["candidates"].items():
            plan.append(("candidate", f"{n}__{c}", st["input"],
                         os.path.join(out_dir, "candidates", f"{n}__{c}.wav"), settings))
    mix_out = os.path.join(out_dir, "mix.wav")
    mixed = {n: os.path.join(out_dir, "mixed", f"{n}.wav") for n in names}
    duck = spec["mix"].get("duck")
    duck_dir = os.path.join(out_dir, "duck")
    duck_files = [os.path.join(duck_dir, x) for x in ("duck.sendcmd", "duck-key.wav", "duckplan.json")]
    sources = [stems[n]["input"] for n in names] + ([spec_path] if spec_path else [])
    for s in sources[:len(names)]:
        if not os.path.isfile(s):
            raise SpecError(f"input not found: {s}")
    tones = [n for n in names if "roomtone" in stems[n]]
    dns = [n for n in names if "denoise_roomtone" in stems[n]]
    labels = ["DX", "MX", "FX", "ME"] if any("role" in stems[n] for n in names) else []
    tone_files = [os.path.join(out_dir, "roomtone", f"{n}-{x}.wav") for n in tones
                  for x in ("bed", "fill", "filled")]
    _check_targets(out_dir, [p[3] for p in plan] + list(mixed.values()) + duck_files + tone_files
                   + [os.path.join(out_dir, "roomtone", f"{n}-bed.json") for n in tones]
                   + [os.path.join(out_dir, "roomtone", f"{n}-denoised.wav") for n in dns]
                   + [os.path.join(out_dir, "export", f"{x}.wav") for x in labels]
                   + [mix_out, os.path.join(out_dir, "render.json")], sources)
    skips, graphs, outputs = [], {}, {}
    srcs = {n: stems[n]["input"] for n in names}   # what each chain reads: the fill's result, if any
    tone_info, spans_by, sn_info, dn_info, stems_info = {}, {}, None, {}, None
    head = "[0:a]" + _head(spec)
    fmt = _head(spec)
    files = {DUCK_KEY: os.path.join(out_dir, DUCK_KEY)}  # mix input -> file
    duck_info = None
    if not dry_run:
        for sub in ("stems", "mixed") + (("candidates",) if len(plan) > len(names) else ()):
            os.makedirs(os.path.join(out_dir, sub), exist_ok=True)
    for n in tones:
        if dry_run:
            skips.append({"where": f"stem {n}", "step": "roomtone",
                          "why": "dry run: gaps are found at render, so the fill is not shown"})
            continue
        srcs[n], rec = fill_roomtone(n, srcs[n], stems[n]["roomtone"], out_dir, spec, skips)
        if rec:
            tone_info[n] = rec
            outputs[f"roomtone/{n}"] = srcs[n]
    for n in dns:
        if dry_run:
            skips.append({"where": f"stem {n}", "step": "denoise_roomtone",
                          "why": "dry run: the window is found at render, so the denoise is not shown"})
            continue
        srcs[n], dn_info[n] = denoise_roomtone(n, srcs[n], stems[n]["denoise_roomtone"], out_dir,
                                               spec, skips)
        outputs[f"roomtone/{n}-denoised"] = srcs[n]
    for kind, key, src, out, cand in plan:
        n = key.split("__")[0]
        src = srcs[n]
        parts = chain_filters(stems[n]["chain"], spec, probe_duration(src), f"stem {key}", skips,
                              candidate=cand)
        if dry_run:
            graphs[key] = _flat(head, parts, fmt)
            continue
        graphs[key], _ = render_graph([src], head, parts, out, fmt, f"stem {key}", skips)
        outputs[key] = out
        if kind == "stem":
            files[n] = out
        if kind == "stem" and duck and n == duck["key"]:
            # keyed on the key stem as rendered, which is what plays in the mix; planned
            # now so a key with no speech fails before the other stems render
            os.makedirs(duck_dir, exist_ok=True)
            try:
                p = dp.write_plan(dp.plan(out, 0, duck["depth"], duck.get("gate_db")), duck_dir)
            except ar.ReportError as e:
                raise MixError(f"duck: {e} (in a mixbuild spec the gate is mix.duck.gate_db)")
            duck_info = {k: p[k] for k in ("depth_db", "floor_dbfs", "gate_dbfs", "gate_from",
                                           "speech_s", "first_speech_s", "sidechain")}
            duck_info["key_sha256"] = sha256(files[DUCK_KEY])
            spans_by[n] = p["speech_spans"]
    # the mix is as long as its longest stem; a dry run reads the inputs for that
    mix_dur = max(probe_duration(files.get(n) or stems[n]["input"]) for n in names)
    mix_head, mix_inputs = mix_graph(spec, mix_dur, names)
    gain = None
    if not dry_run:
        sn_info, gain = speechnorm_gain(spec, spec["mix"]["chain"], mix_head, mix_inputs, files,
                                        mix_dur, spans_by, fmt, skips)
    parts = chain_filters(spec["mix"]["chain"], spec, mix_dur, "mix", skips, speech_gain=gain)
    ln = None
    if dry_run:
        graphs["mix"] = _flat(mix_head, parts, fmt)
    else:
        graphs["mix"], ln = render_graph([files[x] for x in mix_inputs], mix_head, parts, mix_out,
                                         fmt, "mix", skips)
        outputs["mix"] = mix_out
    # mixed stems: each as it plays in the mix, through the mix chain as far as it is
    # linear, with the mix's own loudnorm gain, so together they sum to the mix there
    steps, stop = linear_prefix(spec["mix"]["chain"], ln is None or ln["linear"])
    if stop:
        skips.append({"where": "mixed stems", "step": stop,
                      "why": f"not linear, so the mixed stems stop before it: they sum to the "
                             f"mix as it was before {stop}"})
    for n in names:
        h, ins = mix_graph(spec, mix_dur, names, solo=n)
        parts = chain_filters(steps, spec, mix_dur, f"mixed {n}", [], speech_gain=gain)
        if dry_run:
            graphs[f"mixed/{n}"] = _flat(h, parts, fmt)
            continue
        graphs[f"mixed/{n}"], _ = render_graph([files[x] for x in ins], h, parts, mixed[n], fmt,
                                               f"mixed {n}", skips, measured=ln and ln["measured"])
        outputs[f"mixed/{n}"] = mixed[n]
    if labels and not dry_run:
        stems_info, ex = export_stems(spec, out_dir, mixed, mix_out, mix_dur, stop, skips)
        outputs.update({f"export/{k}": v for k, v in ex.items()})
        if not stems_info["ok"]:
            skips.append({"where": "stems", "step": "sum-null", "why": stems_info["reason"]})
    manifest = {"spec": os.path.basename(spec_path) if spec_path else None,
                "spec_sha256": hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest(),
                "ffmpeg": _ffmpeg_version(), "graphs": graphs, "skipped": skips, "duck": duck_info,
                "presets": {n: {"preset": stems[n]["preset"], "why": PRESETS[stems[n]["preset"]]["why"]}
                            for n in names if stems[n].get("preset")},
                "roomtone": tone_info, "speechnorm": sn_info, "denoise_roomtone": dn_info,
                "stems": stems_info,
                "outputs": {k: {"file": os.path.relpath(v, out_dir), "sha256": sha256(v)}
                            for k, v in outputs.items()}}
    if dry_run:
        return manifest
    if report:
        manifest["reports"] = {}
        for k, v in outputs.items():
            r = ar.report(v, _mkdir(os.path.join(out_dir, "reports", k)), target_lufs=None)
            manifest["reports"][k] = {x: r.get(x) for x in (
                "integrated_lufs", "lra_lu", "true_peak_dbtp", "noise_floor_dbfs", "spread_db",
                "flags")}
        for k in outputs:
            c = k.split("__")
            if len(c) == 2 and c[0] in manifest["reports"]:
                a, b = manifest["reports"][c[0]], manifest["reports"][k]
                if a["noise_floor_dbfs"] is not None and b["noise_floor_dbfs"] is not None:
                    b["noise_floor_change_db"] = round(b["noise_floor_dbfs"] - a["noise_floor_dbfs"], 1)
                    b["loudness_change_lu"] = round(b["integrated_lufs"] - a["integrated_lufs"], 1)
    with open(os.path.join(out_dir, "render.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
    if stems_info and not stems_info["ok"]:
        err = NullError(f"STEMS DO NOT NULL: DX + MX + FX against the mix leaves "
                        f"{stems_info['residual_dbfs']} dBFS (floor {NULL_FLOOR_DBFS:g}): "
                        f"{stems_info['reason']}")
        err.manifest = manifest
        raise err
    return manifest


def speechnorm_gain(spec, chain, mix_head, mix_inputs, files, mix_dur, spans_by, fmt, skips):
    """(record, gain dB) for the mix chain's speechnorm step, or (None, None):
    the mix as it is just before the step, measured over the key stem's speech."""
    steps = [_step(i) for i in chain]
    at = [i for i, (name, _) in enumerate(steps) if name == "speechnorm"]
    if not at:
        return None, None
    raw = steps[at[0]][1]
    key, target = raw["key"], _num(raw.get("i", -16), "speechnorm i")
    if key not in spans_by or raw.get("gate_db") is not None:
        try:
            pp = dp.plan(files[key], 0, dp.DEPTH_DB, raw.get("gate_db"))
        except ar.ReportError as e:
            raise MixError(f"speechnorm: {e} (set gate_db in the speechnorm step)")
        spans_by[key] = pp["speech_spans"]
    pre = chain_filters(chain[:at[0]], spec, mix_dur, "mix", [])
    x = _read_graph([files[n] for n in mix_inputs], _flat(mix_head, pre, fmt), spec["channels"])
    got, speech_s = speech_loudness(x, spec["sample_rate"], spans_by[key])
    gain = round(target - got["speech"]["integrated_lufs"], 3)
    if abs(gain) > 40:
        raise MixError(f"speechnorm would move the mix {gain:+} dB; check the key stem and gate_db")
    peak = got["whole"]["true_peak_dbtp"]
    tp = _num(raw.get("tp", -1.5), "speechnorm tp")
    if peak is not None and peak + gain > tp:
        skips.append({"where": "mix", "step": "speechnorm",
                      "why": f"the true peak would be {peak + gain:+.1f} dBTP after the gain (past "
                             f"{tp:g}): put a limiter after it"})
    return {"key": key, "target_lufs": target, "speech_lufs": got["speech"]["integrated_lufs"],
            "whole_lufs": got["whole"]["integrated_lufs"], "gain_db": gain,
            "speech_s": round(speech_s, 2), "spans": len(spans_by[key]),
            "true_peak_after_dbtp": None if peak is None else round(peak + gain, 1)}, gain


def _mkdir(p):
    os.makedirs(p, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Blind A/B
# ---------------------------------------------------------------------------

def _excerpt(src, out, start, seconds, rate, channels, gain_db=0.0, depth="pcm_f32le"):
    af = f"aformat=sample_fmts=fltp:sample_rates={rate}:channel_layouts=" + (
        "stereo" if channels == 2 else "mono")
    if gain_db:
        af += f",volume={gain_db:.6f}dB"
    cmd = [dc.FFMPEG, "-hide_banner", "-nostdin", "-nostats", "-v", "error", "-y", "-threads", "1",
           "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}", "-i", dc._src(src), "-map", "0:a:0",
           "-af", af, "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact",
           "-c:a", depth, "-f", "wav", out]
    proc = dc._run(cmd, RUN_TIMEOUT_S)
    if proc.returncode != 0:
        raise MixError(f"could not cut an excerpt of {src}: {proc.stderr.strip()[-300:]}")


def _lufs(path):
    summary, _ = ar.loudness(path)
    v = summary["integrated_lufs"]
    if v is None or v == float("-inf") or v < -69.9:
        raise MixError(f"{path} is silent in the excerpt; pick another --start")
    return v


def ab_packet(files, out_dir, start=0.0, seconds=20.0, seed=None, rate=48000):
    """Cut the same span from each file, match integrated loudness (only
    turning down), shuffle to A, B, C ..., seal the key. Returns packet.json's
    content plus the matched spread in LU."""
    dc.check_tools(ffmpeg=True)
    if len(files) < 2:
        raise SpecError("an A/B packet needs at least two files")
    if len(files) > 26:
        raise SpecError("at most 26 files")
    for f in files:
        if not os.path.isfile(f):
            raise SpecError(f"not found: {f}")
    key_path = os.path.join(out_dir, "sealed", "key.json")
    labels = [chr(65 + i) for i in range(len(files))]
    _check_targets(out_dir, [os.path.join(out_dir, f"{x}.wav") for x in labels] + [key_path], files)
    if os.path.exists(key_path):
        raise OutputRefused(f"{key_path} exists: a sealed key is never replaced. Use a new folder.")
    chans = 2 if max(ar.audio_stream(f)["channels"] for f in files) >= 2 else 1
    seed = secrets.randbits(32) if seed is None else int(seed)
    order = list(range(len(files)))
    random.Random(seed).shuffle(order)  # label i holds files[order[i]]
    os.makedirs(os.path.join(out_dir, "sealed"), exist_ok=True)
    work = os.path.join(out_dir, "sealed", "work")
    os.makedirs(work, exist_ok=True)
    try:
        raw, loud = [], []
        for i, f in enumerate(files):
            p = os.path.join(work, f"raw{i}.wav")
            _excerpt(f, p, start, seconds, rate, chans)
            raw.append(p)
            loud.append(_lufs(p))
        target = min(loud)
        gains = [target - v for v in loud]
        measured = []
        for _ in range(3):  # gating makes the match close, not exact; trim once or twice
            outs = []
            for i, lab in enumerate(labels):
                src_i = order[i]
                out = os.path.join(out_dir, f"{lab}.wav")
                _excerpt(files[src_i], out, start, seconds, rate, chans, gains[src_i], BIT_DEPTH)
                outs.append(_lufs(out))
            measured = outs
            if max(measured) - min(measured) <= 0.05 or _ == 2:
                break
            mean = min(measured)
            for i in range(len(labels)):
                gains[order[i]] += mean - measured[i]
    finally:
        shutil.rmtree(work, ignore_errors=True)
    key = {"seed": seed, "start_s": start, "seconds": seconds,
           "labels": {lab: {"source": os.path.abspath(files[order[i]]),
                            "gain_db": round(gains[order[i]], 3)} for i, lab in enumerate(labels)}}
    with open(key_path, "w", encoding="utf-8") as f:
        json.dump(key, f, indent=1)
    os.chmod(key_path, 0o400)
    spread = round(max(measured) - min(measured), 3)
    packet = {"labels": labels, "start_s": start, "seconds": seconds, "sample_rate": rate,
              "channels": chans, "matched_lufs": {lab: round(measured[i], 2)
                                                  for i, lab in enumerate(labels)},
              "spread_lu": spread, "key_sha256": sha256(key_path),
              "files": {lab: {"file": f"{lab}.wav", "sha256": sha256(os.path.join(out_dir, f"{lab}.wav"))}
                        for lab in labels}}
    with open(os.path.join(out_dir, "packet.json"), "w", encoding="utf-8") as f:
        json.dump(packet, f, indent=1)
    with open(os.path.join(out_dir, "README.txt"), "w", encoding="utf-8") as f:
        f.write("Blind A/B. Listen to the files in any order, write down which you prefer and why,\n"
                "then open sealed/key.json. The excerpts are loudness matched to within "
                f"{AB_TOLERANCE_LU} LU,\nso level is not a clue. sealed/key.json's hash is in "
                "packet.json; if the file has changed since, the test is void.\n")
    return packet


def preset_ab(dialogue, out_dir, presets=("lav", "boom", "vo"), start=0.0, seconds=20.0, seed=None):
    """Render `dialogue` through each preset and build one blind A/B packet
    of the results and the untouched dialogue. It says nothing about which is
    better: the ear does, and the sealed key says which file was which."""
    bad = [x for x in presets if x not in PRESETS]
    if bad:
        raise SpecError(f"unknown preset {', '.join(bad)} (known: {', '.join(PRESETS)})")
    if len(set(presets)) != len(presets):
        raise SpecError("a preset is named twice")
    if not os.path.isfile(dialogue):
        raise SpecError(f"not found: {dialogue}")
    files = [dialogue]
    for name in presets:
        spec = validate_spec({"stems": {"dialogue": {"input": os.path.abspath(dialogue),
                                                     "preset": name}}}, "/")
        d = os.path.join(out_dir, "renders", name)
        render(spec, d)
        files.append(os.path.join(d, "stems", "dialogue.wav"))
    return ab_packet(files, os.path.join(out_dir, "packet"), start, seconds, seed)


def presets_text():
    out = []
    for name, p in PRESETS.items():
        steps = ", ".join(f"{k} {json.dumps(v)}" for item in p["chain"] for k, v in item.items())
        out.append(f"{name}: {steps}\n  {p['why']}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m rpresolve.mixbuild", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render", help="render stems and the mix from a spec")
    r.add_argument("spec")
    r.add_argument("--out", required=True, help="output folder, outside any git tree")
    r.add_argument("--report", action="store_true", help="measure every output with audio-report")
    r.add_argument("--dry-run", action="store_true", help="print the filter graphs, render nothing")
    a = sub.add_parser("ab", help="build a blind A/B packet from two or more files")
    a.add_argument("files", nargs="+")
    a.add_argument("--out", required=True)
    a.add_argument("--start", type=float, default=0.0)
    a.add_argument("--seconds", type=float, default=20.0)
    a.add_argument("--seed", type=int, default=None)
    sub.add_parser("presets", help="list the dialogue presets and why each step is there")
    q = sub.add_parser("preset-ab", help="render one dialogue file through presets, blind A/B packet")
    q.add_argument("dialogue")
    q.add_argument("--out", required=True)
    q.add_argument("--presets", default="lav,boom,vo",
                   help="comma list of: " + ", ".join(PRESETS))
    q.add_argument("--start", type=float, default=0.0)
    q.add_argument("--seconds", type=float, default=20.0)
    q.add_argument("--seed", type=int, default=None)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "presets":
            print(presets_text())
        elif args.cmd == "preset-ab":
            p = preset_ab(args.dialogue, os.path.abspath(args.out), args.presets.split(","),
                          args.start, args.seconds, args.seed)
            print(f"{', '.join(p['labels'])} written; matched within {p['spread_lu']} LU; "
                  "the key is sealed in packet/sealed/key.json")
        elif args.cmd == "render":
            spec = load_spec(args.spec)
            m = render(spec, os.path.abspath(args.out), os.path.abspath(args.spec),
                       report=args.report, dry_run=args.dry_run)
            if args.dry_run:
                for k, g in m["graphs"].items():
                    print(f"{k}: {g}")
            else:
                for k, o in m["outputs"].items():
                    print(f"{k}: {o['file']}  {o['sha256'][:12]}")
            for s in m["skipped"]:
                print(f"skipped {s['step']} in {s['where']}: {s['why']}", file=sys.stderr)
        else:
            p = ab_packet(args.files, os.path.abspath(args.out), args.start, args.seconds, args.seed)
            print(f"{', '.join(p['labels'])} written; matched within {p['spread_lu']} LU; "
                  "the key is sealed in sealed/key.json")
    except NullError as e:
        print(f"mixbuild: {e}", file=sys.stderr)
        return 1
    except (SpecError, MixError, OutputRefused, ar.ReportError, dc.CheckError, dc.ToolMissing) as e:
        print(f"mixbuild: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
