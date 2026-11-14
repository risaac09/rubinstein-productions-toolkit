"""
contract-probe: check the scripting calls this toolkit relies on, and the ones it
could rely on, against the Resolve that is running, and print one JSON document.

Every check is read-only or sandbox-only.

  read-only     reads through the scripting API and changes nothing: no setter, no
                page change, no timeline switch, no import, no render. The probe
                hands Resolve to the check through a guard that refuses any method
                whose name does not start with Get or Is, so a check cannot write
                by mistake. A call that is only looked up (is it there?) is never
                made.
  sandbox-only  writes to the project, then puts back what it can (a cleanup stack runs even
                when a check raises). Use it only in a project made for it: it is the one
                command here that writes, so cli.py's "does not write to Resolve" holds for
                every other command. Off unless
                --sandbox-checks is given AND the open project's name equals the
                sandbox named in the config (key "probe", field "sandbox_project",
                set in an overlay file passed with --config; the shipped config
                leaves it null, so a sandbox check refuses by default). It never
                starts a render, never deletes anything it did not create, and
                names what it creates "rp-probe-...".

The document lists, per check and per call: whether the call is present (the
attribute exists and is callable), what it returned (a type, a length, the keys of a
dict, the value of a bool or number; never a name or a path), and "pass", "differs"
or "no_golden" against a golden file (default: production/tests/fixtures/
contract_probe_golden.json). The golden file records what the documentation says (its types were taken from a fake
modelled on the stub, then checked row by row against the stub text);
a "differs" row is a finding, not a failure. Exit status is 0 unless Resolve is not
running (1), or --strict was given and a row differs (3).

The probe never prints a project, clip, timeline or file name.
"""

import json
import os
import sys
from pathlib import Path

from rpresolve import cli
from rpresolve import config as rpconfig

NAME = "contract-probe"
HELP = "Check the scripting API against the running Resolve (read-only by default)"

GOLDEN_DEFAULT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "contract_probe_golden.json"
PREFIX = "rp-probe-"
READ_PREFIXES = ("Get", "Is")

# Object kinds the checks pass around. A check names the call by "Kind.Method".
SANDBOX_FIELD = "sandbox_project"


class Refused(Exception):
    """A read-only check reached for a method that is not a read."""


def add_arguments(p):
    p.add_argument("--out", help="write the JSON document here instead of standard output")
    p.add_argument("--golden", default=str(GOLDEN_DEFAULT),
                   help="golden file of expected rows (default: the one in production/tests/fixtures)")
    p.add_argument("--only", help="comma-separated check ids to run, such as Q0,Q5")
    p.add_argument("--sandbox-checks", action="store_true",
                   help="also run the sandbox-only checks; refused unless the open project is "
                        "the sandbox named in the config")
    p.add_argument("--strict", action="store_true", help="exit 3 when any row differs from the golden file")


def kind_of(value):
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, (list, tuple)):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return "object"


def summarise(value, show=False):
    """What a return looks like, without any name or path in it."""
    kind = kind_of(value)
    out = {"type": kind}
    if kind in ("bool", "int", "float"):
        out["value"] = value
    elif kind in ("list", "str"):
        out["len"] = len(value)
        if show and kind == "str":
            out["value"] = value
    elif kind == "dict":
        out["keys"] = sorted(str(k) for k in value)
    return out


class Guard:
    """Wraps a scripting object; in read mode refuses any method that is not a read."""

    def __init__(self, obj, ctx):
        object.__setattr__(self, "_obj", obj)
        object.__setattr__(self, "_ctx", ctx)

    def __getattr__(self, name):
        attr = getattr(self._obj, name)
        if not callable(attr):
            return attr
        ctx = self._ctx

        def call(*args, **kwargs):
            if ctx.read_only and not name.startswith(READ_PREFIXES):
                raise Refused(f"{name} is not a read")
            return ctx.wrap(attr(*args, **kwargs))
        return call


class Context:
    def __init__(self, golden, read_only):
        self.golden = golden
        self.read_only = read_only
        self.rows = []
        self.cleanups = []
        self.check_id = ""

    def wrap(self, value):
        if value is None or isinstance(value, (bool, int, float, str, bytes)):
            return value
        if isinstance(value, (list, tuple)):
            return [self.wrap(v) for v in value]
        if isinstance(value, dict):
            return value
        return Guard(value, self)

    def raw(self, obj):
        return object.__getattribute__(obj, "_obj") if isinstance(obj, Guard) else obj

    def _grade(self, key, present, returned):
        gold = self.golden.get(key)
        if gold is None:
            return "no_golden"
        if gold.get("present") is not None and gold["present"] != present:
            return "differs"
        want = gold.get("type")
        if want and returned is not None and returned.get("type") != want:
            return "differs"
        return "pass"

    def look(self, label, obj, method):
        """Is the call there? Never makes it."""
        if obj is None:
            self._row(label, method, None, None, "no target object in the open project")
            return None
        present = callable(getattr(self.raw(obj), method, None))
        self._row(label, method, present, None, None)
        return present

    def call(self, label, obj, method, *args, show=False):
        """Make a call and record it. In read mode a non-read method is refused."""
        if obj is None:
            self._row(label, method, None, None, "no target object in the open project")
            return None
        present = callable(getattr(self.raw(obj), method, None))
        if not present:
            self._row(label, method, False, None, None)
            return None
        if self.read_only and not method.startswith(READ_PREFIXES):
            self._row(label, method, True, None, "refused: not a read")
            return None
        try:
            value = getattr(obj, method)(*args)
        except Refused as e:
            self._row(label, method, True, None, f"refused: {e}")
            return None
        except Exception as e:  # a real Resolve can raise on a bad state
            self._row(label, method, True, None, f"raised {type(e).__name__}")
            return None
        self._row(label, method, True, summarise(self.raw(value) if isinstance(value, Guard) else value, show), None)
        return value

    def note(self, label, text):
        self._row(label, "-", None, None, text)

    def _row(self, label, method, present, returned, note):
        key = f"{self.check_id}:{label}"
        row = {"check": self.check_id, "call": label, "present": present}
        if returned is not None:
            row["returned"] = returned
        if note:
            row["note"] = note
        row["golden"] = self._grade(key, present, returned) if present is not None else "no_golden"
        self.rows.append(row)


# ---------------------------------------------------------------------------
# Targets: objects a read check can reach without changing anything
# ---------------------------------------------------------------------------

def targets(ctx, resolve):
    t = {"resolve": resolve, "pm": None, "project": None, "pool": None, "timeline": None,
         "audio_item": None, "video_item": None, "clip": None}
    t["pm"] = resolve.GetProjectManager()
    if t["pm"] is not None:
        t["project"] = t["pm"].GetCurrentProject()
    p = t["project"]
    if p is not None:
        t["pool"] = p.GetMediaPool()
        t["timeline"] = p.GetCurrentTimeline()
    if t["pool"] is not None:
        folder = t["pool"].GetRootFolder()
        clips = folder.GetClipList() if folder is not None else []
        t["clip"] = clips[0] if clips else None
    tl = t["timeline"]
    if tl is not None:
        for kind, key in (("audio", "audio_item"), ("video", "video_item")):
            if (tl.GetTrackCount(kind) or 0) >= 1:
                items = tl.GetItemListInTrack(kind, 1) or []
                t[key] = items[0] if items else None
    return t


# ---------------------------------------------------------------------------
# Read-only checks
# ---------------------------------------------------------------------------

def q0(ctx, t):
    r = t["resolve"]
    ctx.call("Resolve.GetVersionString", r, "GetVersionString", show=True)
    ctx.call("Resolve.GetProductName", r, "GetProductName", show=True)
    ctx.call("Resolve.IsStudio", r, "IsStudio")


def q1(ctx, t):
    pm, p = t["pm"], t["project"]
    for m in ("CreateProject", "SaveProject", "CloseProject", "LoadProject", "DeleteProject"):
        ctx.look(f"ProjectManager.{m}", pm, m)
    for m in ("SaveCurrentProjectSettingsAsNewPreset", "SetProjectSettingsPreset",
              "ExportProjectSettingsPreset", "ImportProjectSettingsPreset"):
        ctx.look(f"Project.{m}", p, m)
    ctx.call("ProjectManager.GetProjectListInCurrentFolder", pm, "GetProjectListInCurrentFolder")
    ctx.call("Project.GetProjectSettingsPresetList", p, "GetProjectSettingsPresetList")
    ctx.call("Project.GetSettings", p, "GetSettings")


def q2(ctx, t):
    ctx.look("MediaPoolItem.SetAudioMapping", t["clip"], "SetAudioMapping")
    ctx.look("TimelineItem.SetSourceAudioChannelMapping", t["audio_item"], "SetSourceAudioChannelMapping")
    ctx.call("MediaPoolItem.GetAudioMapping", t["clip"], "GetAudioMapping")
    ctx.call("TimelineItem.GetSourceAudioChannelMapping", t["audio_item"], "GetSourceAudioChannelMapping")
    ctx.call("MediaPoolItem.GetClipProperty", t["clip"], "GetClipProperty", "Audio Ch")


def q3(ctx, t):
    a = t["audio_item"]
    ctx.look("TimelineItem.SetFades", a, "SetFades")
    ctx.look("TimelineItem.AddTransition", a, "AddTransition")
    ctx.call("TimelineItem.GetFades", a, "GetFades")
    ctx.call("TimelineItem.GetProperties", a, "GetProperties")


def q4(ctx, t):
    p = t["project"]
    ctx.look("Project.SetRenderSettings", p, "SetRenderSettings")
    ctx.look("Project.GetRenderSettings", p, "GetRenderSettings")  # documented absent
    ctx.call("Project.GetRenderJobList", p, "GetRenderJobList")
    ctx.call("Project.GetRenderFormats", p, "GetRenderFormats")
    ctx.call("Project.GetAudioRenderFormats", p, "GetAudioRenderFormats")


def q5(ctx, t):
    v = t["video_item"]
    ctx.look("TimelineItem.Stabilize", v, "Stabilize")
    props = ctx.call("TimelineItem.GetProperties", v, "GetProperties")
    if isinstance(props, dict):
        hits = sorted(k for k in props if "stabil" in str(k).lower())
        ctx.note("TimelineItem.GetProperties.stabilizer_keys", f"{len(hits)} key(s) mention stabilization")


def q6(ctx, t):
    v = t["video_item"]
    ctx.look("TimelineItem.SetSpeed", v, "SetSpeed")
    ctx.call("TimelineItem.GetSpeed", v, "GetSpeed")


def q7(ctx, t):
    ctx.look("TimelineItem.AddTransition", t["video_item"], "AddTransition")
    effects = ctx.call("Resolve.GetEffectsList", t["resolve"], "GetEffectsList", "video")
    if isinstance(effects, list):
        names = [str(e.get("name", "")) if isinstance(e, dict) else "" for e in effects]
        ctx.note("Resolve.GetEffectsList.lists_transitions",
                 "an entry named like a dissolve: " + ("yes" if any("dissolve" in n.lower() for n in names) else "no"))


def q8(ctx, t):
    ctx.look("Resolve.ValidateDCTL", t["resolve"], "ValidateDCTL")
    ctx.look("Resolve.EncryptDCTL", t["resolve"], "EncryptDCTL")
    ctx.look("Project.RefreshLUTList", t["project"], "RefreshLUTList")
    ctx.look("TimelineItem.ExportLUT", t["video_item"], "ExportLUT")
    ctx.look("TimelineItem.SetCDL", t["video_item"], "SetCDL")
    ctx.look("TimelineItem.GetCDL", t["video_item"], "GetCDL")


def q9(ctx, t):
    v = t["video_item"]
    for m in ("AddFusionComp", "AddEffect", "DeleteEffects", "CreateMagicMask", "DeleteFusionCompByName"):
        ctx.look(f"TimelineItem.{m}", v, m)
    ctx.call("TimelineItem.GetFusionCompCount", v, "GetFusionCompCount")
    ctx.look("Resolve.Fusion", t["resolve"], "Fusion")


def q10(ctx, t):
    ctx.call("Resolve.GetFairlightPresets", t["resolve"], "GetFairlightPresets")
    ctx.look("Project.ApplyFairlightPresetToCurrentTimeline", t["project"], "ApplyFairlightPresetToCurrentTimeline")
    ctx.call("Timeline.GetTrackCount", t["timeline"], "GetTrackCount", "audio")
    ctx.call("Timeline.GetTrackSubType", t["timeline"], "GetTrackSubType", "audio", 1)


def q11(ctx, t):
    ctx.call("Timeline.GetNormalizeAudioModes", t["timeline"], "GetNormalizeAudioModes")
    ctx.look("Timeline.NormalizeAudioLevel", t["timeline"], "NormalizeAudioLevel")
    ctx.look("TimelineItem.AnalyzeAudioLevels", t["audio_item"], "AnalyzeAudioLevels")


def q12(ctx, t):
    p = t["project"]
    ctx.look("Project.StartRendering", p, "StartRendering")  # looked up, never called
    ctx.call("Project.IsRenderingInProgress", p, "IsRenderingInProgress")
    ctx.look("MediaPool.ImportMedia", t["pool"], "ImportMedia")
    ctx.look("MediaPool.AppendToTimeline", t["pool"], "AppendToTimeline")
    ctx.look("MediaPool.DeleteTimelines", t["pool"], "DeleteTimelines")


READ_CHECKS = [
    ("Q0", "Build and product stamp", q0),
    ("Q1", "Project create and settings presets: calls present, reads", q1),
    ("Q2", "Audio mapping: calls present, current mapping", q2),
    ("Q3", "Fades and audio transition: calls present, current fades", q3),
    ("Q4", "Render colour tags: calls present, job list, formats", q4),
    ("Q5", "Stabilizer: call present, no stabilizer key among item properties", q5),
    ("Q6", "Speed: calls present, current speed", q6),
    ("Q7", "Transitions: call present, effects list", q7),
    ("Q8", "DCTL and LUT: calls present", q8),
    ("Q9", "Fusion and Magic Mask: calls present, comp count", q9),
    ("Q10", "Fairlight presets and track format reads", q10),
    ("Q11", "Level calls: modes list, calls present", q11),
    ("Q12", "Render start and import: calls present, rendering flag", q12),
]


# ---------------------------------------------------------------------------
# Sandbox-only checks. Each creates its own "rp-probe-" objects and puts back the
# state it changed. None starts a render; none deletes what it did not create.
# ---------------------------------------------------------------------------

def _scratch_timeline(ctx, t, tag):
    pool = t["pool"]
    clip = t["clip"]
    if pool is None or clip is None:
        ctx.note("setup", "the sandbox media pool has no clip at its root; import one first")
        return None, None
    tl = pool.CreateEmptyTimeline(PREFIX + tag)
    if tl is None:
        ctx.note("setup", "CreateEmptyTimeline returned None")
        return None, None
    # AppendToTimeline acts on the current timeline: make the scratch one current, and put the
    # previous one back at cleanup.
    previous = t["timeline"]
    ctx.cleanups.append(lambda: pool.DeleteTimelines([tl]))
    if previous is not None:
        ctx.cleanups.append(lambda: t["project"].SetCurrentTimeline(previous))
    t["project"].SetCurrentTimeline(tl)
    items = pool.AppendToTimeline([{"mediaPoolItem": clip}]) or []
    return tl, items


def _drop(t, tl):
    """Scratch timelines are deleted by the cleanup stack that run_probe always runs."""


def s_q1(ctx, t):
    pm, original = t["pm"], t["project"].GetName()
    made = pm.CreateProject(PREFIX + "q1")
    ctx.note("ProjectManager.CreateProject", "returned a project" if made is not None else "returned None")
    now = pm.GetCurrentProject()
    ctx.note("ProjectManager.CreateProject.becomes_current",
             "yes" if now is not None and now.GetName() == PREFIX + "q1" else "no")
    pm.SaveProject()
    pm.LoadProject(original)
    ctx.call("ProjectManager.DeleteProject", pm, "DeleteProject", PREFIX + "q1")


def s_q2(ctx, t):
    """The T36 route: remap a multi-stream source BEFORE the append, read the mapping back, then
    append and count audio tracks. One run confirms or kills it: see the verdict note."""
    clip = t["clip"]
    if clip is None:
        ctx.note("setup", "the sandbox media pool has no clip at its root; import one first")
        return
    before = clip.GetAudioMapping()
    if before:
        ctx.cleanups.append(lambda: clip.SetAudioMapping(before))
    mapping = json.dumps({"track_mapping": {"1": {"channel_idx": [1, 2], "mute": False, "type": "stereo"}}})
    ctx.call("MediaPoolItem.SetAudioMapping", clip, "SetAudioMapping", mapping)
    after = ctx.call("MediaPoolItem.GetAudioMapping.after", clip, "GetAudioMapping")
    ctx.note("MediaPoolItem.GetAudioMapping.read_back",
             "differs from what was set" if _norm(after) != _norm(mapping) else "same as what was set")
    tl, items = _scratch_timeline(ctx, t, "q2")
    if tl is None:
        return
    per = [len(tl.GetItemListInTrack("audio", n) or []) for n in range(1, (tl.GetTrackCount("audio") or 0) + 1)]
    ctx.note("Timeline.audio_tracks_after_append",
             f"{len(per)} audio track(s), " + ", ".join(map(str, per)) + " item(s) per track")
    ctx.note("verdict.remap_before_append", "no spill: confirmed" if len(per) == 1 else
             "spill remains: killed (needs the clip to have more than one stream to mean anything)")


def _norm(v):
    try:
        return json.loads(v) if isinstance(v, str) else v
    except ValueError:
        return v


def s_q3(ctx, t):
    tl, items = _scratch_timeline(ctx, t, "q3")
    if tl is None or not items:
        _drop(t, tl)
        return
    item = items[0]
    ctx.call("TimelineItem.SetFades", item, "SetFades", {"FadeIn": 0, "FadeOut": 12})
    ctx.call("TimelineItem.GetFades.after", item, "GetFades")
    ctx.call("TimelineItem.AddTransition.audio", item, "AddTransition",
             {"type": "Cross Fade +3 dB", "category": "audio", "position": "end",
              "alignment": "left", "duration": 12})
    _drop(t, tl)


def s_q4(ctx, t):
    p = t["project"]
    for tag, gamma in (("Rec.709", "Gamma 2.4"), ("Rec.709", "Rec.709-A"), ("Rec.709", "rp-probe-not-a-tag")):
        ctx.call(f"Project.SetRenderSettings[{gamma}]", p, "SetRenderSettings",
                 {"ColorSpaceTag": tag, "GammaTag": gamma})
    ctx.call("Project.SetRenderSettings[restore]", p, "SetRenderSettings",
             {"ColorSpaceTag": "Same as Project", "GammaTag": "Same as Project"})
    ctx.note("render", "no job added and none started; tags put back to Same as Project (the earlier values "
                       "cannot be read); a person renders and ffprobe reads the tags")


def s_q5(ctx, t):
    tl, items = _scratch_timeline(ctx, t, "q5")
    if tl is None or not items:
        _drop(t, tl)
        return
    item = items[0]
    before = item.GetProperties()
    ctx.call("TimelineItem.Stabilize", item, "Stabilize")
    after = item.GetProperties()
    ctx.note("TimelineItem.Stabilize.properties_changed", "yes" if before != after else "no")
    _drop(t, tl)


def s_q6(ctx, t):
    tl, items = _scratch_timeline(ctx, t, "q6")
    if tl is None or not items:
        _drop(t, tl)
        return
    item = items[0]
    ctx.call("TimelineItem.SetSpeed[50]", item, "SetSpeed", {"Percentage": 50.0})
    ctx.call("TimelineItem.GetSpeed.after", item, "GetSpeed")
    item.SetSpeed({"Percentage": 100.0})
    _drop(t, tl)


def s_q7(ctx, t):
    tl, items = _scratch_timeline(ctx, t, "q7")
    if tl is None or not items:
        _drop(t, tl)
        return
    for name in ("Cross Dissolve", "Dip to Color Dissolve"):
        got = ctx.call(f"TimelineItem.AddTransition[{name}]", items[0], "AddTransition",
                       {"type": name, "category": "simple", "position": "end",
                        "alignment": "center", "duration": 12})
        if got is not None:
            ctx.note(f"TimelineItem.AddTransition[{name}].type_read_back",
                     str((got.GetProperties() or {}).get("TransitionType")))
    _drop(t, tl)


def s_q8(ctx, t):
    r = t["resolve"]
    ctx.call("Resolve.ValidateDCTL[ok]", r, "ValidateDCTL",
             "__DEVICE__ float3 transform(int p_Width, int p_Height, int p_X, int p_Y, "
             "float p_R, float p_G, float p_B) { return make_float3(p_R, p_G, p_B); }")
    ctx.call("Resolve.ValidateDCTL[bad]", r, "ValidateDCTL", "this is not a dctl")


def s_q9(ctx, t):
    tl, items = _scratch_timeline(ctx, t, "q9")
    if tl is None or not items:
        _drop(t, tl)
        return
    item = items[0]
    ctx.call("TimelineItem.AddFusionComp", item, "AddFusionComp")
    ctx.call("TimelineItem.GetFusionCompCount.after", item, "GetFusionCompCount")
    ctx.call("TimelineItem.CreateMagicMask[F]", item, "CreateMagicMask", "F")
    _drop(t, tl)


def s_q10(ctx, t):
    presets = t["resolve"].GetFairlightPresets() or []
    ctx.note("Resolve.GetFairlightPresets", f"{len(presets)} preset(s); none applied (applying switches the timeline)")


def s_q11(ctx, t):
    tl, items = _scratch_timeline(ctx, t, "q11")
    if tl is None or not items:
        _drop(t, tl)
        return
    item = items[0]
    ctx.call("TimelineItem.SetProperties[AudioVolume]", item, "SetProperties", {"AudioVolume": -6.0})
    props = item.GetProperties() or {}
    ctx.note("TimelineItem.AudioVolume.read_back", str(props.get("AudioVolume")))
    item.SetProperties({"AudioVolume": 0.0})
    _drop(t, tl)


def s_q12(ctx, t):
    ctx.note("Project.StartRendering", "never called by this probe; start a render by hand and read the job status")


SANDBOX_CHECKS = [
    ("Q2", "Remap a clip to one stereo track, append audio-only, count tracks, restore", s_q2),
    ("Q3", "SetFades and audio AddTransition on a scratch timeline", s_q3),
    ("Q4", "SetRenderSettings with each colour tag pair; no render", s_q4),
    ("Q5", "Stabilize a scratch item and compare its properties", s_q5),
    ("Q6", "SetSpeed 50 percent and back", s_q6),
    ("Q7", "AddTransition by name", s_q7),
    ("Q8", "ValidateDCTL with a good and a bad source", s_q8),
    ("Q9", "AddFusionComp and CreateMagicMask without strokes", s_q9),
    ("Q10", "Fairlight preset names (nothing applied)", s_q10),
    ("Q11", "AudioVolume set, read back, restored", s_q11),
    ("Q12", "Render start is not called", s_q12),
    # Last: it switches the open project, which stales every object taken from it.
    ("Q1", "Create a project, see whether it becomes current, switch back, delete it", s_q1),
]


# ---------------------------------------------------------------------------

def load_golden(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def sandbox_name(config):
    block = config.get("probe") if isinstance(config, dict) else None
    name = block.get(SANDBOX_FIELD) if isinstance(block, dict) else None
    return name if isinstance(name, str) and name else None


def run_probe(resolve, golden, config, only=None, sandbox_checks=False):
    """Run the checks against `resolve` and return the document."""
    ctx = Context(golden, read_only=True)
    guarded = ctx.wrap(resolve)
    t = targets(ctx, guarded)
    project = t["project"]
    name = project.GetName() if project is not None else None
    sandbox = sandbox_name(config)
    in_sandbox = bool(sandbox) and name == sandbox
    wanted = {s.strip() for s in only.split(",")} if only else None

    checks = []
    for cid, title, fn in READ_CHECKS:
        if wanted and cid not in wanted:
            continue
        ctx.check_id, start = cid, len(ctx.rows)
        status = "ran"
        try:
            fn(ctx, t)
        except Refused as e:
            status = f"refused: {e}"
        checks.append({"id": cid, "mode": "read-only", "title": title, "status": status,
                       "rows": ctx.rows[start:]})

    for cid, title, fn in SANDBOX_CHECKS:
        if wanted and cid not in wanted:
            continue
        entry = {"id": cid, "mode": "sandbox-only", "title": title, "rows": []}
        if not sandbox_checks:
            entry["status"] = "skipped: --sandbox-checks not given"
        elif not in_sandbox:
            entry["status"] = ("refused: the open project is not the sandbox named in the config"
                               if sandbox else "refused: no sandbox project is named in the config")
        elif project is None:
            entry["status"] = "refused: no open project"
        else:
            wctx = Context(golden, read_only=False)
            wctx.check_id = f"S-{cid}"
            raw_t = {k: (ctx.raw(v) if isinstance(v, Guard) else v) for k, v in t.items()}
            try:
                fn(wctx, raw_t)
                entry["status"] = "ran"
            except Exception as e:
                entry["status"] = f"raised {type(e).__name__}"
            finally:
                for undo in reversed(wctx.cleanups):
                    try:
                        undo()
                    except Exception:
                        entry["status"] += "; cleanup failed"
            entry["rows"] = wctx.rows
        checks.append(entry)

    rows = [r for c in checks for r in c["rows"]]
    summary = {g: sum(1 for r in rows if r["golden"] == g) for g in ("pass", "differs", "no_golden")}
    return {"document": "contract-probe", "version": 1,
            "project_is_sandbox": in_sandbox, "sandbox_checks_requested": bool(sandbox_checks),
            "summary": summary, "checks": checks}


def run(args):
    resolve = cli.get_resolve()
    config = rpconfig.load_config(args.config)
    doc = run_probe(resolve, load_golden(args.golden), config, args.only, args.sandbox_checks)
    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        sys.stdout.write(text)
    return 3 if args.strict and doc["summary"]["differs"] else 0


if __name__ == "__main__":
    cli.script(__name__)
