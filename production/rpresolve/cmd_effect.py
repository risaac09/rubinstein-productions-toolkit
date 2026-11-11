"""
effect: the Fusion macros in production/fusion-macros/ that the MCP apply_effect tool
puts on [auto] timeline items. Offline, the command lists each macro with the tools it
wires between MediaIn1 and MediaOut1, and checks that every macro parses.

apply_effect(), the work behind the MCP tool, adds a Fusion comp to each named clip of
an ' [auto]' timeline (TimelineItem.AddFusionComp), pastes the macro's tools into it,
wires them between the comp's MediaIn1 and MediaOut1, and reads the comp back. The
comp it adds is new, so nothing that existed before is changed; a timeline whose name
does not end ' [auto]' is refused, as apply_grade refuses one. A whitelisted `params`
dict (PARAMS) sets text, size, centre, exposure-flash gain, retime speed and reframe-pan positions; it is
validated first, part of the plan, and read back. refresh() then toggles pass-through on
each added tool, because a scripted comp otherwise renders the old picture.
"""

import copy
import math
import os
import re

from rpresolve import api, cli, ledgercheck
from rpresolve.workflows import AUTO, Refused, _wanted_items, plan_sha

NAME = "effect"
HELP = "Offline: list the Fusion macros apply_effect applies and check that each parses"

MACRO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "fusion-macros")

# How each macro sits between MediaIn1 and MediaOut1: the (tool, input) pairs MediaIn1
# feeds, and the tool whose Output feeds MediaOut1. The macros carry no MediaIn or
# MediaOut, so this is the one place the wiring is written down (fusion-macros.md).
WIRING = {
    "crop-window": {"in": [("Merge1", "Foreground")], "out": "Merge1"},
    "cross-dissolve-short": {"in": [("Merge1", "Foreground")], "out": "Merge1"},
    "dip-to-black": {"in": [("BrightnessContrast1", "Input")], "out": "BrightnessContrast1"},
    "exposure-flash": {"in": [("ColorCorrector1", "Input")], "out": "ColorCorrector1"},
    # The subject goes over the text through the matte: MediaIn1 is the background of
    # the text merge and the foreground of the matted merge.
    "person-matte-text": {"in": [("Merge1", "Background"), ("Merge2", "Foreground")],
                          "out": "Merge2"},
    "reframe-pan": {"in": [("Transform1", "Input")], "out": "Transform1"},
    "retime": {"in": [("TimeStretcher1", "Input")], "out": "TimeStretcher1"},
    "scale-window": {"in": [("Transform1", "Input")], "out": "Transform1"},
    "text-word": {"in": [("Merge1", "Background")], "out": "Merge1"},
    "zoom-push": {"in": [("Transform1", "Input")], "out": "Transform1"},
}

# Macros whose animation belongs at the end of an item (an outgoing fade): their keyframes
# are moved so the last one sits on the item's last frame. The rest start at frame 0.
END_ANCHORED = {"dip-to-black"}

# The params apply_effect takes, per macro (fusion-macros.md). Anything else is refused.
PARAMS = {
    "exposure-flash": {"gain"},
    "person-matte-text": {"text", "size"},
    "reframe-pan": {"pan_from", "pan_to", "pan_base"},
    "retime": {"speed", "speed_to"},
    "text-word": {"text", "size", "centre"},
    "zoom-push": {"centre"},
}
REQUIRED = {"reframe-pan": ("pan_from", "pan_to"), "retime": ("speed",)}
# Macros whose keys depend on the item's length in source frames (Fusion comp time on an
# item counts source frames, 0 at its first frame).
NEEDS_LENGTH = {"reframe-pan", "retime"}

COMP_PREFIX = "rp-fx "  # the added comp's name: "rp-fx <id>"


# ---------------------------------------------------------------------------
# .setting files: a Lua table, read into the dict Fusion's Python Paste() takes
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r"""\s*(?:
    (?P<str>"(?:[^"\\]|\\.)*")
  | (?P<num>-?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<punct>[{}\[\]=,;()])
)""", re.X)


def _tokens(text):
    text = re.sub(r"--[^\n]*", "", text)  # Lua line comments
    pos, out = 0, []
    while pos < len(text):
        if text[pos:].strip() == "":
            break
        m = _TOKEN.match(text, pos)
        if not m:
            raise ValueError(f"cannot read the macro at character {pos}: {text[pos:pos + 20]!r}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()
    return out


def parse_setting(text):
    """A .setting file's Lua table as Python: a typed table `Name { ... }` becomes a
    dict with '__ctor': Name, positional entries take keys 1, 2, ... as in Lua, and
    `ordered()` keeps its tools in file order."""
    toks = _tokens(text)
    i = 0

    def peek(k=0):
        return toks[i + k] if i + k < len(toks) else (None, None)

    def take(want=None):
        nonlocal i
        tok = peek()
        if tok[0] is None or (want and tok[1] != want):
            raise ValueError(f"expected {want or 'a value'} in the macro, found {tok[1]!r}")
        i += 1
        return tok

    def value():
        kind, v = peek()
        if kind == "str":
            take()
            return bytes(v[1:-1], "utf-8").decode("unicode_escape")
        if kind == "num":
            take()
            return float(v) if re.search(r"[.eE]", v) else int(v)
        if kind == "name":
            take()
            if v in ("true", "false"):
                return v == "true"
            if v == "nil":
                return None
            if peek()[1] == "(":  # ordered() and the like
                take("(")
                take(")")
            t = table()
            t["__ctor"] = v
            return t
        if v == "{":
            return table()
        raise ValueError(f"unexpected {v!r} in the macro")

    def table():
        take("{")
        out, n = {}, 0
        while peek()[1] != "}":
            kind, v = peek()
            if v == "[":
                take("[")
                key = value()
                take("]")
                take("=")
                out[key] = value()
            elif kind == "name" and peek(1)[1] == "=":
                take()
                take("=")
                out[v] = value()
            else:
                n += 1
                out[n] = value()
            if peek()[1] in (",", ";"):
                take()
        take("}")
        return out

    result = value()
    if peek()[0] is not None:
        raise ValueError("text after the macro's closing brace")
    return result


def macro_path(effect):
    if effect not in WIRING:
        raise Refused(f"no macro '{effect}'; the macros are {', '.join(sorted(WIRING))}.")
    return os.path.join(MACRO_DIR, effect + ".setting")


def load_macro(effect):
    """(text, parsed) for a macro id; refuses an unknown id or a macro whose tools do
    not include every tool its wiring names."""
    path = macro_path(effect)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    try:
        parsed = parse_setting(text)
    except ValueError as e:
        raise Refused(f"{os.path.basename(path)}: {e}")
    tools = parsed.get("Tools") or {}
    w = WIRING[effect]
    missing = sorted(({t for t, _ in w["in"]} | {w["out"]}) - set(tools))
    if missing:
        raise Refused(f"{os.path.basename(path)} has no tool(s) {missing}, which its wiring names.")
    return text, parsed


def _move_keys(parsed, move):
    """A copy of the parsed macro with every keyframe's time, and its handles' times, put
    through move(). A key lands on a whole frame."""
    tools = {}
    for name, spec in parsed["Tools"].items():
        if isinstance(spec, dict) and isinstance(spec.get("KeyFrames"), dict):
            keys = {}
            for f, key in spec["KeyFrames"].items():
                if isinstance(f, int):
                    at = int(round(move(f)))
                    if isinstance(key, dict):  # a handle keeps its (moved) distance from its key
                        key = {**key, **{h: {**key[h], 1: at + move(key[h][1]) - move(f)}
                                         for h in ("LH", "RH")
                                         if isinstance(key.get(h), dict) and 1 in key[h]}}
                    f = at
                keys[f] = key
            spec = {**spec, "KeyFrames": keys}
        tools[name] = spec
    return {**parsed, "Tools": tools}


def shifted(parsed, offset):
    """A copy of the parsed macro with every keyframe moved by offset frames."""
    return _move_keys(parsed, lambda f: f + offset) if offset else parsed


def scaled(parsed, k):
    """A copy of the parsed macro with every keyframe time multiplied by k. The macros are
    keyed in timeline frames and comp time on an item counts source frames (trap 4), so k
    is the item's source frames over its timeline frames: 1.25 for 30 fps on 24."""
    return _move_keys(parsed, lambda f: f * k) if k and abs(k - 1) > 1e-9 else parsed


def comp_timing(parsed, effect, frames, source_frames):
    """(key_scale, key_offset) for one item: the scale that puts the macro's keys in the
    item's comp time, then the move that ends an END_ANCHORED macro on its last frame."""
    ok = all(isinstance(x, (int, float)) and x > 0 for x in (frames, source_frames))
    k = round(source_frames / frames, 6) if ok else 1
    length = source_frames if ok else frames
    return k, end_offset(scaled(parsed, k), length) if effect in END_ANCHORED else 0


def end_offset(parsed, frames):
    """The move that puts the macro's last keyframe on frame `frames - 1`."""
    last = max((f for spec in parsed["Tools"].values() if isinstance(spec, dict)
                for f in (spec.get("KeyFrames") or {}) if isinstance(f, int)), default=None)
    return 0 if last is None or not frames else int(frames) - 1 - last


def tool_names(parsed):
    return [k for k in (parsed.get("Tools") or {}) if k != "__ctor"]


def validate_params(effect, params):
    """The params as a plain dict, or Refused: only the keys this macro accepts, each
    the right kind and range, and the ones the macro cannot run without."""
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise Refused("params is an object of the macro's settings")
    allowed = PARAMS.get(effect, set())
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise Refused(f"{effect} does not accept param(s) {unknown}; it takes "
                      f"{sorted(allowed) or 'none'}.")
    missing = [k for k in REQUIRED.get(effect, ()) if k not in params]
    if missing:
        raise Refused(f"{effect} needs param(s) {missing}.")
    if "speed_to" in params and "speed" not in params:
        raise Refused("speed_to needs speed.")
    for k, v in params.items():
        if k == "text":
            ok = isinstance(v, str) and v.strip() != "" and len(v) <= 80
        else:
            ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            if ok and k in ("speed", "speed_to", "size"):
                ok = v > 0
            elif ok and k == "gain":
                ok = 1 <= v <= 20  # the flash's peak; the macro's own is 2
            elif ok:
                ok = 0 <= v <= 1  # centre, pan_*: a fraction of the frame
        if not ok:
            raise Refused(f"param {k} is {v!r}: " + (
                "a word of 1 to 80 characters" if k == "text" else
                "a number above 0" if k in ("speed", "speed_to", "size") else
                "a number from 1 to 20" if k == "gain" else
                "a number from 0 to 1"))
    return dict(params)


def _hermite(n, v1, d0, d1, v0=0):
    """Two keys over n frames, 0 at v0 and n at v1, with handles a third of the way along
    at slopes d0 and d1, written as .setting files store them (absolute; _set_inputs turns
    them into the offsets SetKeyFrames reads)."""
    return {0: {1: v0, "RH": {1: n / 3.0, 2: v0 + d0 * n / 3.0}},
            n: {1: v1, "LH": {1: n - n / 3.0, 2: v1 - d1 * n / 3.0}}}


def apply_params(parsed, effect, params, n):
    """(the macro with params written in, the checks to read back). n is the item's
    length in source frames. Keys that depend on the item are built here, not in the
    .setting: retime's SourceTime (source frame = integral of the speed, a linear speed
    change when speed_to is given) and reframe-pan's X."""
    macro = copy.deepcopy(parsed)
    tools, checks = macro["Tools"], []

    def put(tool, key, value, param):
        tools[tool].setdefault("Inputs", {})[key] = {"Value": value}
        checks.append({"param": param, "tool": tool, "input": key, "want": value})

    def keys(spline, frames, param):
        tools[spline]["KeyFrames"] = frames
        checks.append({"param": param, "tool": spline, "frame": n, "want": frames[n][1]})

    if "text" in params:
        put("Text1", "StyledText", params["text"], "text")
    if "size" in params:
        put("Text1", "Size", params["size"], "size")
    if "centre" in params:
        c = {1: params["centre"], 2: 0.5}
        put("Transform1" if effect == "zoom-push" else "Merge1",
            "Pivot" if effect == "zoom-push" else "Center", c, "centre")
    if "gain" in params:
        g = params["gain"]
        spline = tools["ColorCorrector1MasterRGBGain"]
        first = spline["KeyFrames"][min(f for f in spline["KeyFrames"] if isinstance(f, int))]
        first[1] = g
        if isinstance(first.get("RH"), dict):
            first["RH"][2] = g  # the handle stays level with its key, as in the .setting
        checks.append({"param": "gain", "tool": "ColorCorrector1MasterRGBGain", "frame": 0,
                       "want": g})
    if effect == "retime":
        s0 = params["speed"]
        s1 = params.get("speed_to", s0)
        keys("TimeStretcher1SourceTime", _hermite(n, (s0 + s1) / 2.0 * n, s0, s1), "speed")
    if effect == "reframe-pan":
        base = params.get("pan_base", 0.5)
        a, b = 0.5 + base - params["pan_from"], 0.5 + base - params["pan_to"]
        keys("XYPath1X", _hermite(n, b, (b - a) / n, (b - a) / n, a), "pan_to")
    return macro, checks


# ---------------------------------------------------------------------------
# apply_effect
# ---------------------------------------------------------------------------

def _comp_names(item):
    return list(api._safe_call(item, "GetFusionCompNameList") or [])


MODIFIERS = ("BezierSpline", "XYPath", "PolyPath")


def _value(v):
    """A parsed input value as Fusion's SetInput takes it: a FuID as its string, a point
    as {1: x, 2: y}; None for a structured value (ColorCurves) this does not carry."""
    if not isinstance(v, dict):
        return v
    if v.get("__ctor") == "FuID":
        return v.get(1)
    if "__ctor" in v or not all(isinstance(k, int) for k in v):
        return None
    return v


def relative_handles(keyframes):
    """The macro's keyframes with each LH and RH as an offset from its key: a .setting
    file stores handles as absolute (frame, value), SetKeyFrames reads them as offsets
    (found live, Resolve 21.2: absolute handles overshoot a 1.0 to 1.15 zoom to 1.7)."""
    out = {}
    for frame, key in keyframes.items():
        if not isinstance(frame, int) or not isinstance(key, dict):
            continue
        k = dict(key)
        for h in ("LH", "RH"):
            if isinstance(k.get(h), dict) and 1 in k[h] and 2 in k[h]:
                k[h] = {1: k[h][1] - frame, 2: k[h][2] - key[1]}
        out[frame] = k
    return out


def _set_inputs(comp, tool, inputs, specs, links, skipped, animated):
    """Set one tool's (or modifier's) inputs from the macro: a value with SetInput, an
    animated input by adding its modifier and copying the keyframes, a link to another
    tool into links (connected once every tool exists)."""
    for key, spec in inputs.items():
        if key == "__ctor" or not isinstance(spec, dict):
            continue
        src = spec.get("SourceOp")
        if src is None:
            v = _value(spec.get("Value"))
            if v is None:
                skipped.append(f"{tool.Name}.{key}")
            else:
                tool.SetInput(key, v)
            continue
        mod = specs.get(src) or {}
        if mod.get("__ctor") in MODIFIERS:
            tool.AddModifier(key, mod["__ctor"])
            out = tool[key].GetConnectedOutput()
            m = out.GetTool() if out else None
            if not m:
                skipped.append(f"{tool.Name}.{key} (modifier)")
                continue
            m.SetAttrs({"TOOLS_Name": src})
            if mod.get("KeyFrames"):
                m.SetKeyFrames(relative_handles(mod["KeyFrames"]), True)
                animated.append((tool.Name, key, src))
            _set_inputs(comp, m, mod.get("Inputs") or {}, specs, links, skipped, animated)
        else:
            links.append((tool.Name, key, src))


def _build(comp, parsed):
    """Add the macro's tools to comp: each tool by AddTool under its macro name, its
    inputs, keyframes and links. Returns (the inputs it could not carry, the animated
    inputs as (tool, input, spline))."""
    specs = {k: v for k, v in parsed["Tools"].items() if k != "__ctor"}
    skipped, links, animated = [], [], []
    for name, spec in specs.items():
        if spec.get("__ctor") in MODIFIERS:
            continue
        t = comp.AddTool(spec["__ctor"], -32768, -32768)
        if not t:
            skipped.append(f"{name} (AddTool {spec['__ctor']} failed)")
            continue
        t.SetAttrs({"TOOLS_Name": name})
    for name, spec in specs.items():
        t = comp.FindTool(name)
        if t and spec.get("__ctor") not in MODIFIERS:
            _set_inputs(comp, t, spec.get("Inputs") or {}, specs, links, skipped, animated)
    for name, key, src in links:
        other = comp.FindTool(src)
        if other:
            comp.FindTool(name).ConnectInput(key, other)
        else:
            skipped.append(f"{name}.{key} (no {src})")
    return skipped, animated


def _tool_list(comp):
    got = comp.GetToolList(False)
    return list(got.values()) if isinstance(got, dict) else list(got or [])


def _refreshable(tool):
    """Not MediaIn/MediaOut and not a spline or path modifier."""
    kind = str(getattr(tool, "ID", "") or "")
    return not (kind.startswith(("MediaIn", "MediaOut")) or kind in MODIFIERS
                or str(tool.Name).startswith(("MediaIn", "MediaOut")))


def refresh(comp):
    """Toggle TOOLB_PassThrough on, then off, on every added tool, and return their
    names. A comp built by script reads back whole and still renders the old picture
    until this is done (found live, Resolve 21.2, trap 3)."""
    done = []
    for tool in _tool_list(comp):
        if _refreshable(tool):
            tool.SetAttrs({"TOOLB_PassThrough": True})
            tool.SetAttrs({"TOOLB_PassThrough": False})
            done.append(tool.Name)
    return done


def _same(got, want):
    if isinstance(want, dict):
        return isinstance(got, dict) and all(
            _same(got.get(k, got.get(float(k))), w) for k, w in want.items())
    if isinstance(want, (int, float)):
        return isinstance(got, (int, float)) and abs(got - want) <= 1e-6 * max(1.0, abs(want))
    return got == want


def _read_params(comp, checks):
    """({what: value read}, problems) for each param the build wrote."""
    got, problems = {}, []
    for c in checks:
        t = comp.FindTool(c["tool"])
        if "frame" in c:
            kf = (t.GetKeyFrames() or {}) if t else {}
            hit = next((k for f, k in kf.items() if int(round(float(f))) == c["frame"]), None)
            v = hit.get(1, hit.get(1.0)) if isinstance(hit, dict) else None
            label = f"{c['tool']}@{c['frame']}"
        else:
            v = t.GetInput(c["input"]) if t else None
            label = f"{c['tool']}.{c['input']}"
        got[label] = v
        if not _same(v, c["want"]):
            problems.append(f"param {c['param']}: {label} reads {v!r}, set {c['want']!r}")
    return got, problems


def _read_back(comp, effect, parsed, animated=()):
    """What the comp holds after the build: each macro tool and modifier present, each
    animated input's keyframes on the macro's frames and its value between two keys
    within theirs (no handle overshoot), MediaIn1 on the wired inputs, the out tool on
    MediaOut1's input. Returns a list of problems."""
    w, problems = WIRING[effect], []
    for tname, key, sname in animated:
        keys = sorted((f, k[1]) for f, k in parsed["Tools"][sname]["KeyFrames"].items()
                      if isinstance(f, int))
        tool = comp.FindTool(tname)
        for (f0, v0), (f1, v1) in zip(keys, keys[1:]):
            mid = tool.GetInput(key, (f0 + f1) / 2.0) if tool else None
            if not isinstance(mid, (int, float)):
                continue  # a point input: its X and Y splines are checked on their own
            if not min(v0, v1) - 1e-3 <= mid <= max(v0, v1) + 1e-3:
                problems.append(f"{tname}.{key} reads {mid:.3f} between frames {f0} and {f1}, "
                                f"outside {v0} to {v1}")
    specs = {k: v for k, v in parsed["Tools"].items() if k != "__ctor"}
    for name, spec in specs.items():
        t = comp.FindTool(name)
        if not t:
            problems.append(f"{name} missing")
        elif spec.get("KeyFrames"):
            kf = t.GetKeyFrames() or {}  # a spline answers {frame: key}
            got = sorted(int(round(float(f))) for f in kf)
            want = sorted(k for k in spec["KeyFrames"] if isinstance(k, int))
            if want and want[0] < 0 and 0 in got and 0 not in want:
                # Fusion adds a key at frame 0 when the first key sits before it (T25); it
                # is on the curve, so the value check above covers it.
                got.remove(0)
            if got != want:
                problems.append(f"{name} keyframes at {got}, the macro has {want}")
    media_in, media_out = comp.FindTool("MediaIn1"), comp.FindTool("MediaOut1")
    if not media_in or not media_out:
        return problems + ["the comp has no MediaIn1 or MediaOut1"]
    for tool, inp in w["in"]:
        t = comp.FindTool(tool)
        conn = t[inp].GetConnectedOutput() if t else None
        if not conn or conn.GetTool().Name != "MediaIn1":
            problems.append(f"{tool}.{inp} is not fed by MediaIn1")
    conn = media_out["Input"].GetConnectedOutput()
    if not conn or conn.GetTool().Name != w["out"]:
        problems.append(f"MediaOut1 is not fed by {w['out']}")
    problems += [f"{t.Name} is still pass-through" for t in _tool_list(comp)
                 if _refreshable(t) and t.GetAttrs("TOOLB_PassThrough")]
    return problems


def _wire(comp, effect):
    w = WIRING[effect]
    media_in, media_out = comp.FindTool("MediaIn1"), comp.FindTool("MediaOut1")
    for tool, inp in w["in"]:
        t = comp.FindTool(tool)
        if t:  # a tool AddTool could not make is named by the read-back
            t.ConnectInput(inp, media_in)
    if comp.FindTool(w["out"]):
        media_out.ConnectInput("Input", comp.FindTool(w["out"]))


def _source_frames(it):
    """The item's length in source frames, or None when the item does not say."""
    a, b = (api._safe_call(it, "GetSourceStartFrame"), api._safe_call(it, "GetSourceEndFrame"))
    ok = all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (a, b))
    return int(round(b - a)) if ok and b > a else None


def apply_effect(resolve, project_name, timeline, effect, track=1, items=None, dry_run=False,
                 project_id=None, check_cancel=None, expect_sha=None, params=None, spans=None,
                 ledger=None, draft=None, override=None):
    """Add a Fusion comp named 'rp-fx <effect>' to each clip (items: 1-based clip numbers on
    one video track; or spans: the manifest's 0-based span indices; default all; not both) of
    an ' [auto]' timeline, paste the macro's tools into it, wire them between MediaIn1 and
    MediaOut1, and read the comp back. Clips are numbered as vertical_apply numbers them: a
    transition is a track item with no media pool clip (T29) and is not counted, so clip n is
    span n - 1. An item that already has any Fusion comp is refused (the new comp would
    replace the one that renders). Returns {project, timeline, effect,
    targets, refused, results, plan_sha, dry_run, exit_status}; each row has the clip number
    as index and the raw track position; exit_status 1 when any item failed its read-back.
    ledger (the absolute path of a drafts.json) and draft (the picked plan) check the effect
    against what the draft drew (ledgercheck): the dry run flags a span the draft marked
    not_drawn, a real run refuses unless override names the effect; `ledger` in the result is
    the report, and it is part of the plan hash."""
    if items is not None and spans is not None:
        raise Refused("give items (clip numbers, from 1) or spans (manifest span indices, from 0), "
                      "not both.")
    if spans is not None and not spans:
        raise Refused("spans is empty; leave it out to take every clip.")
    led = ledger and ledgercheck.load(ledger, draft)
    text, parsed = load_macro(effect)
    params = validate_params(effect, params)  # before anything is read from Resolve
    tools = tool_names(parsed)
    pm, project, pin = api.pin_for_write(resolve, project_name, project_id)
    _, tl = api.find_timeline(project, timeline)
    tl_name, tl_id = tl.GetName(), tl.GetUniqueId()
    if not tl_name.endswith(AUTO):
        raise Refused(f"'{tl_name}' is not an [auto] timeline. Effects go only onto timelines "
                      "these tools made; duplicate it first (duplicate_timeline_auto).")
    tracks = int(tl.GetTrackCount("video") or 0)
    if not 1 <= track <= tracks:
        raise Refused(f"'{tl_name}' has {tracks} video track(s).")
    all_items = tl.GetItemListInTrack("video", track) or []
    # Clip n sits at raw track position positions[n - 1]; transition items have none.
    positions = [p for p, it in enumerate(all_items, 1) if api.is_clip(it)]
    shift = 0 if spans is None else 1  # span i is clip i + 1
    wanted = _wanted_items(items if spans is None else [s + 1 for s in spans], len(positions))
    bad = [n - shift for n in wanted if not 1 <= n <= len(positions)]
    if bad:
        raise Refused(f"no {'span' if shift else 'clip'}(s) {bad} on V{track} ({len(positions)} "
                      f"clip(s) in {len(all_items)} item(s); items with no media pool clip, "
                      f"transitions first, are not counted).")
    comp_name = COMP_PREFIX + effect
    targets, refused = [], []
    for n in wanted:
        it = all_items[positions[n - 1] - 1]
        names = _comp_names(it)
        frames = api._safe_call(it, "GetDuration")
        row = {"index": n, "position": positions[n - 1], "name": it.GetName(),
               "item_id": api._safe_call(it, "GetUniqueId"),
               "comps": names, "frames": frames, "source_frames": _source_frames(it)}
        row["key_scale"], row["key_offset"] = comp_timing(parsed, effect, frames,
                                                          row["source_frames"])
        why = []
        # AddFusionComp makes the new comp the one that renders, so an item that has
        # a comp already would change: refuse it (this also stops a second run).
        if names:
            why.append(f"it already has Fusion comp(s) ({', '.join(names)}); a new comp "
                       "would replace the one that renders")
        if effect in END_ANCHORED and not (isinstance(frames, (int, float)) and frames > 0):
            why.append("its length cannot be read, so the fade cannot be put at its end")
        if effect in NEEDS_LENGTH and not row["source_frames"]:
            why.append("its length in source frames cannot be read, so the keys cannot be built")
        if why:
            refused.append({**row, "reasons": why})
        else:
            targets.append(row)
    if not targets:
        raise Refused("no item may take the effect: " + "; ".join(
            f"#{r['index']} {r['name']}: {', '.join(r['reasons'])}" for r in refused))
    report = ledgercheck.check(led, [(effect, t["index"] - 1) for t in targets], override)
    sha = plan_sha("effect", pin.unique_id, tl_id, track, effect, text,
                   [(t["index"], t["position"], t["item_id"], t["comps"], t["frames"],
                     t["source_frames"]) for t in targets], sorted(params.items()), report)
    out = {"project": {"name": pin.name, "id": pin.unique_id},
           "timeline": {"name": tl_name, "unique_id": tl_id, "track": track,
                        "other_tracks": api.other_video_tracks(tl, track)},
           "effect": {"id": effect, "comp": comp_name, "tools": tools,
                      "wiring": {"in": [f"{t}.{i}" for t, i in WIRING[effect]["in"]],
                                 "out": WIRING[effect]["out"]}},
           "params": params, "targets": targets, "refused": refused, "results": [], "plan_sha": sha,
           "ledger": report, "dry_run": dry_run, "exit_status": 0}
    if dry_run:
        return out
    ledgercheck.refuse_unless_overridden(report)
    if expect_sha and expect_sha != sha:
        raise Refused("the plan changed since the dry run (the items, their comps or the "
                      "macro differ); run the dry run again and review it")
    try:
        _apply_all(out, targets, all_items, parsed, effect, comp_name, pm, pin, check_cancel,
                   params)
    except BaseException as e:
        e.effects_added = list(out["results"])
        raise
    out["exit_status"] = 1 if any(not r["ok"] for r in out["results"]) else 0
    return out


def _apply_all(out, targets, all_items, parsed, effect, comp_name, pm, pin, check_cancel,
               params=None):
    for t in targets:
        if check_cancel:
            check_cancel()
        pin.check(pm)
        it = all_items[t["position"] - 1]
        row = {"index": t["index"], "position": t["position"], "name": t["name"],
               "item_id": t["item_id"]}
        # In the results before the comp exists, so an error mid-build still names it.
        row.update(ok=False, detail="interrupted while building the comp")
        out["results"].append(row)
        comp = it.AddFusionComp()
        if not comp:
            row["detail"] = "AddFusionComp() returned None"
            continue
        # AddFusionComp names the new comp "Composition N" and makes it the item's
        # current one; the new name is the one not in the list before.
        added = [c for c in _comp_names(it) if c not in t["comps"]]
        if len(added) == 1:
            it.RenameFusionCompByName(added[0], comp_name)
        macro = shifted(scaled(parsed, t["key_scale"]), t["key_offset"])
        checks = []
        if params or effect in NEEDS_LENGTH:
            macro, checks = apply_params(macro, effect, params or {}, t["source_frames"])
        comp.Lock()
        try:
            skipped, animated = _build(comp, macro)
            _wire(comp, effect)
        finally:
            comp.Unlock()
        # After Unlock: the toggle has to be evaluated, which a locked comp may defer.
        refresh(comp)
        problems = _read_back(comp, effect, macro, animated)
        if checks:
            row["params"], bad = _read_params(comp, checks)
            problems += bad
        if comp_name not in _comp_names(it):
            problems.append(f"the comp is not named '{comp_name}'")
        if skipped:
            row["skipped"] = skipped
        row["ok"] = not problems
        row["detail"] = ("comp read back: tools present, wired MediaIn1 to MediaOut1"
                         if not problems else "; ".join(problems))


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------

def add_arguments(p):
    """No flags."""


def run(args):
    status = 0
    for effect in sorted(WIRING):
        try:
            _, parsed = load_macro(effect)
        except (Refused, OSError) as e:
            print(f"{effect}: REFUSED {e}")
            status = 2
            continue
        w = WIRING[effect]
        print(f"{effect}: {', '.join(tool_names(parsed))}; MediaIn1 -> "
              f"{', '.join(f'{t}.{i}' for t, i in w['in'])}; {w['out']} -> MediaOut1")
    return status


if __name__ == "__main__":
    cli.script(__name__)
