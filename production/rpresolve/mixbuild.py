"""
rpresolve.mixbuild: an offline mix from a spec, and a blind A/B packet.
ffmpeg renders, audio-report measures; nothing here touches Resolve or
Fairlight, and nothing is sent anywhere.

    python3 -m rpresolve.mixbuild render SPEC --out DIR [--report] [--dry-run]
    python3 -m rpresolve.mixbuild ab --out DIR FILE FILE [FILE ...]
                                    [--start S] [--seconds 20] [--seed N]

The spec is YAML (a small subset read by the parser below, so PyYAML is not
needed; JSON is accepted too). Paths are relative to the spec's folder.

    sample_rate: 48000            # default 48000
    channels: 2                   # every stem and the mix, default 2
    stems:
      dialogue:
        input: dialogue.wav
        chain:                    # steps run in order
          - highpass: 80
          - denoise: {method: afftdn, nr: 12}
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
      duck: {key: dialogue, under: [music], threshold: -30, ratio: 8}
      chain:
        - loudnorm: {i: -16, tp: -1.5}
        - limiter: {ceiling: -1.5}

Steps: gain, fade, highpass, lowpass, eq, deess, denoise, compress, loudnorm,
limiter. denoise methods: afftdn and arnndn (ffmpeg built in; arnndn needs a
`model` file or it is skipped), deepfilternet (recorded as skipped in v1, installed or
not: run `deep-filter` on the input yourself and name its output as the stem input), and `candidates`, the place a candidate
goes. Each candidate renders its own stem file; the main stem skips the
placeholder. iZotope RX is not offered (decision 26: out until a licence is
confirmed on record). A skipped step is recorded in render.json, never fatal.

render writes into DIR: stems/NAME.wav, candidates/NAME__CAND.wav, mix.wav and
render.json (spec hash, ffmpeg version, every filter graph, each output's
hash and skips). The same spec and inputs give byte-identical WAVs: bitexact
flags, one thread, no metadata, two-pass loudnorm. It refuses to write over
a source and refuses any DIR inside a git working tree. With --report each
output is measured by audio-report into DIR/reports/NAME/.

ab cuts the same span from each file, matches the excerpts' integrated
loudness (turning the louder ones down, so nothing clips), shuffles them to
A, B, C ..., and writes the sealed key to DIR/sealed/key.json (read-only,
hash in packet.json). Listen first, open the key after.

Numbers are not listening: a render that measures well can still sound
wrong. The A/B packet is the control for that.
"""

import argparse
import hashlib
import json
import os
import random
import re
import secrets
import shutil
import sys

from . import audioreport as ar
from . import delivercheck as dc
from .paths import OutputRefused, in_any_git_tree

BIT_DEPTH = "pcm_s24le"
AB_TOLERANCE_LU = 0.5
RUN_TIMEOUT_S = 3600


class SpecError(ValueError):
    """The spec cannot be read or does not make sense."""


class MixError(RuntimeError):
    """ffmpeg failed, or a measurement could not be made."""


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
    place to say what was skipped."""

    def __init__(self, rate, duration, skips, where):
        self.rate, self.duration, self.skips, self.where = rate, duration, skips, where

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
        if ctx.duration is None or d > ctx.duration:
            raise SpecError(f"{ctx.where}: fade out of {d} s is longer than the signal")
        out.append(f"afade=t=out:st={_f(ctx.duration - d)}:d={_f(d)}:curve={curve}")
    if not out:
        raise SpecError("fade needs in or out")
    return out


def s_highpass(raw, ctx):
    p = _params(raw, "freq", ("freq", "poles"), "highpass")
    return [f"highpass=f={_f(_num(p.get('freq', 80), 'highpass freq', 10, 2000))}"
            f":p={int(_num(p.get('poles', 2), 'poles', 1, 2))}"]


def s_lowpass(raw, ctx):
    p = _params(raw, "freq", ("freq", "poles"), "lowpass")
    return [f"lowpass=f={_f(_num(p.get('freq', 16000), 'lowpass freq', 100, 24000))}"
            f":p={int(_num(p.get('poles', 2), 'poles', 1, 2))}"]


def s_eq(raw, ctx):
    p = _params(raw, "freq", ("type", "freq", "gain", "q"), "eq")
    f = _f(_num(p.get("freq", 1000), "eq freq", 20, 20000))
    g = _f(_num(p.get("gain", 0), "eq gain", -24, 24))
    kind = p.get("type", "peak")
    q = _f(_num(p.get("q", 1.0), "eq q", 0.05, 20))
    if kind == "peak":
        return [f"equalizer=f={f}:t=q:w={q}:g={g}"]
    if kind in ("lowshelf", "highshelf"):
        return [f"{kind}=f={f}:g={g}"]
    raise SpecError(f"eq type {kind!r}: use peak, lowshelf or highshelf")


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


def s_denoise(raw, ctx):
    p = _params(raw, "method", ("method", "nr", "nf", "tn", "model", "mix"), "denoise")
    method = p.get("method", "afftdn")
    if method == "candidates":
        return []  # only meaningful in a candidate render; the main render drops it
    if method == "afftdn":
        return [f"afftdn=nr={_f(_num(p.get('nr', 12), 'nr', 0.01, 97))}"
                f":nf={_f(_num(p.get('nf', -50), 'nf', -80, -20))}"
                f":tn={1 if p.get('tn') else 0}"]
    if method == "arnndn":
        model = p.get("model")
        if not model or not os.path.isfile(model):
            return ctx.skip("denoise arnndn", "needs a `model` file (an .rnnn); none given or found")
        return [f"arnndn=m={model.replace(chr(92), '/').replace(':', chr(92) + ':')}"
                f":mix={_f(_num(p.get('mix', 1), 'mix', -1, 1))}"]
    if method == "deepfilternet":
        if not _deepfilter():
            return ctx.skip("denoise deepfilternet", "deep-filter is not installed")
        ctx.skip("denoise deepfilternet", "installed, but run it on the input first and name "
                 "the result as the stem input; mixbuild does not shell out to it")
        return []
    raise SpecError(f"denoise method {method!r}: use afftdn, arnndn, deepfilternet or candidates")


def s_loudnorm(raw, ctx):
    p = _params(raw, "i", ("i", "tp", "lra"), "loudnorm")
    return [f"loudnorm=I={_f(_num(p.get('i', -16), 'loudnorm i', -70, -5))}"
            f":TP={_f(_num(p.get('tp', -1.5), 'loudnorm tp', -9, 0))}"
            f":LRA={_f(_num(p.get('lra', 11), 'loudnorm lra', 1, 50))}"]


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
         "eq": s_eq, "deess": s_deess, "denoise": s_denoise, "compress": s_compress,
         "loudnorm": s_loudnorm, "limiter": s_limiter}


def _step(item):
    if isinstance(item, str):
        return item, None
    if not isinstance(item, dict) or len(item) != 1:
        raise SpecError(f"a chain step is one name with its settings, got {item!r}")
    (name, raw), = item.items()
    if name not in STEPS:
        raise SpecError(f"unknown step {name!r} (known: {', '.join(sorted(STEPS))})")
    return name, raw


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
        bad = sorted(set(st) - {"input", "chain", "candidates"})
        if bad:
            raise SpecError(f"stem {name}: unknown key {', '.join(bad)}")
        st["input"] = os.path.normpath(os.path.join(base, os.path.expanduser(str(st["input"]))))
        st["chain"] = st.get("chain") or []
        if not isinstance(st["chain"], list):
            raise SpecError(f"stem {name}: chain must be a list")
        for item in st["chain"]:
            _step(item)
        cands = st.get("candidates") or {}
        if not isinstance(cands, dict):
            raise SpecError(f"stem {name}: candidates must be a map of name to denoise settings")
        for c in cands:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", str(c)) or "__" in str(c):
                raise SpecError(f"candidate name {c!r}: letters, digits, _ and - only, no double underscore")
        if cands and not any(_step(i)[0] == "denoise" and isinstance(_step(i)[1], dict)
                             and _step(i)[1].get("method") == "candidates" for i in st["chain"]):
            raise SpecError(f"stem {name}: candidates need a `denoise: {{method: candidates}}` step")
        st["candidates"] = cands
    mix = spec.get("mix") or {}
    bad = sorted(set(mix) - {"gains", "duck", "chain"})
    if bad:
        raise SpecError(f"mix: unknown key {', '.join(bad)}")
    for name in mix.get("gains") or {}:
        if name not in stems:
            raise SpecError(f"mix gains names a stem that does not exist: {name}")
    duck = mix.get("duck")
    if duck:
        bad = sorted(set(duck) - {"key", "under", "threshold", "ratio", "attack", "release"})
        if bad:
            raise SpecError(f"duck: unknown key {', '.join(bad)}")
        under = duck.get("under")
        under = [under] if isinstance(under, str) else under
        if duck.get("key") not in stems or not under or any(u not in stems for u in under) \
                or duck["key"] in under:
            raise SpecError("duck: key and under must be different stems of this spec")
        duck["under"] = under
    mix["chain"] = mix.get("chain") or []
    for item in mix["chain"]:
        _step(item)
    spec["mix"] = mix
    return spec


# ---------------------------------------------------------------------------
# Graphs
# ---------------------------------------------------------------------------

def _head(spec):
    layout = "stereo" if spec["channels"] == 2 else "mono"
    return f"aformat=sample_fmts=fltp:sample_rates={spec['sample_rate']}:channel_layouts={layout}"


def chain_filters(steps, spec, duration, where, skips, candidate=None):
    """The filters for a chain, split at the loudnorm step (at most one):
    (before, loudnorm_or_None, after)."""
    ctx = Ctx(spec["sample_rate"], duration, skips, where)
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


def _ffmpeg(inputs, graph, out, extra=()):
    cmd = [dc.FFMPEG, "-hide_banner", "-nostdin", "-nostats", "-v", "error", "-y",
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


def render_graph(inputs, head, parts, out_path):
    """Render `head` (a filter_complex that ends in an unlabelled filter)
    plus the chain `parts` to out_path atomically. A loudnorm runs two-pass:
    measure the chain up to it, then apply it linearly. Returns the graph."""
    before, ln, after = parts
    body = ",".join([head, *before])
    if ln:
        got = _loudnorm_pass1(inputs, body, ln)
        ln = (f"{ln}:measured_I={got['input_i']}:measured_TP={got['input_tp']}"
              f":measured_LRA={got['input_lra']}:measured_thresh={got['input_thresh']}"
              f":offset={got['target_offset']}:linear=true")
        body += "," + ln
    graph = ",".join([body, *after, "anull"]) + "[out]"
    part = out_path + ".part"
    try:
        proc = _ffmpeg(inputs, graph, part)
        if proc.returncode != 0:
            raise MixError("ffmpeg failed: " + proc.stderr.strip()[-400:])
        os.replace(part, out_path)
    finally:
        if os.path.exists(part):
            os.remove(part)
    return graph


def mix_graph(spec, duration, skips, names):
    """(head, parts, inputs): the mix filter_complex up to the chain, the
    chain parts, and the stem each input reads. The key of a duck is read
    once more per ducked stem, as its own input, not split: a split feeding
    two consumers made the render vary from run to run."""
    mix = spec["mix"]
    gains = mix.get("gains") or {}
    duck = mix.get("duck")
    head = _head(spec)
    inputs = list(names)
    under = duck["under"] if duck else []
    inputs += [duck["key"]] * len(under) if duck else []
    # every input runs a second past the end and is cut back after the mix: a stream
    # that ends inside a block makes the last block of a two-input filter vary
    dur = _f(round(duration, 3))
    pad = _f(round(duration + 1, 3))
    lines = []
    for i, n in enumerate(inputs):
        g = _f(_num(gains.get(n, 0), "mix gain", -60, 40)) if i < len(names) else "0"
        # fixed frame sizes: the limiter and loudnorm behind are block dependent
        lines.append(f"[{i}:a]{head},volume={g}dB,apad=whole_dur={pad},asetnsamples=n=1024:p=1[g{i}]")
    final = []
    for i, n in enumerate(names):
        if n in under:
            sc = (f"threshold={_lin(_num(duck.get('threshold', -30), 'duck threshold', -60, 0))}"
                  f":ratio={_f(_num(duck.get('ratio', 8), 'duck ratio', 1, 20))}"
                  f":attack={_f(_num(duck.get('attack', 20), 'duck attack', 0.01, 2000))}"
                  f":release={_f(_num(duck.get('release', 400), 'duck release', 0.01, 9000))}")
            k = len(names) + under.index(n)
            lines.append(f"[g{i}][g{k}]sidechaincompress={sc},asetnsamples=n=1024:p=1[d{i}]")
            final.append(f"d{i}")
        else:
            final.append(f"g{i}")
    lines.append("".join(f"[{x}]" for x in final)
                 + f"amix=inputs={len(final)}:normalize=0:duration=longest:dropout_transition=0,"
                 f"atrim=end={dur},asetnsamples=n=4096:p=0")
    return ";".join(lines), chain_filters(mix["chain"], spec, duration, "mix", skips), inputs


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


def _flat(head, parts):
    before, ln, after = parts
    return ",".join([head, *before, *([ln] if ln else []), *after]) + "[out]"


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
    sources = [stems[n]["input"] for n in names] + ([spec_path] if spec_path else [])
    for s in sources[:len(names)]:
        if not os.path.isfile(s):
            raise SpecError(f"input not found: {s}")
    _check_targets(out_dir, [p[3] for p in plan] + [mix_out, os.path.join(out_dir, "render.json")],
                   sources)
    skips, graphs, outputs = [], {}, {}
    head = "[0:a]" + _head(spec)
    stem_paths = {}
    if not dry_run:
        os.makedirs(os.path.join(out_dir, "stems"), exist_ok=True)
        if any(p[0] == "candidate" for p in plan):
            os.makedirs(os.path.join(out_dir, "candidates"), exist_ok=True)
    for kind, key, src, out, cand in plan:
        n = key.split("__")[0]
        parts = chain_filters(stems[n]["chain"], spec, probe_duration(src), f"stem {key}", skips,
                              candidate=cand)
        if dry_run:
            graphs[key] = _flat(head, parts)
            continue
        graphs[key] = render_graph([src], head, parts, out)
        outputs[key] = out
        if kind == "stem":
            stem_paths[n] = out
    # the mix is as long as its longest stem; a dry run reads the inputs for that
    mix_dur = max(probe_duration(stem_paths.get(n) or stems[n]["input"]) for n in names)
    mix_head, parts, mix_inputs = mix_graph(spec, mix_dur, skips, names)
    if dry_run:
        graphs["mix"] = _flat(mix_head, parts)
    else:
        graphs["mix"] = render_graph([stem_paths[n] for n in mix_inputs], mix_head, parts, mix_out)
        outputs["mix"] = mix_out
    manifest = {"spec": os.path.basename(spec_path) if spec_path else None,
                "spec_sha256": hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest(),
                "ffmpeg": _ffmpeg_version(), "graphs": graphs, "skipped": skips,
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
    return manifest


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
    args = ap.parse_args(argv)
    try:
        if args.cmd == "render":
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
    except (SpecError, MixError, OutputRefused, ar.ReportError, dc.CheckError, dc.ToolMissing) as e:
        print(f"mixbuild: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
