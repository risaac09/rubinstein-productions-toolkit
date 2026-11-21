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
                has no "probe" key, so a sandbox check refuses by default). It never
                starts a render, never deletes anything it did not create, and
                names what it creates "rp-probe-...".

The document lists, per check and per call: whether the call is present (the
attribute exists and is callable), what it returned (a type, a length, the keys of a
dict, the value of a bool or number; for the fades and speed reads also the numeric values of the dict, and a string value as a length; never a name or a path), and "pass", "differs"
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


def summarise(value, show=False, values=False):
    """What a return looks like, without any name or path in it. A dict records keys only; with
    values=True it also records its bool/int/float values, and a string value as a length only."""
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
        if values:
            out["values"] = {str(k): v for k, v in value.items() if kind_of(v) in ("bool", "int", "float")}
            out["str_lens"] = {str(k): len(v) for k, v in value.items() if kind_of(v) == "str"}
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

    def call(self, label, obj, method, *args, show=False, values=False):
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
        self._row(label, method, True, summarise(self.raw(value) if isinstance(value, Guard) else value, show, values), None)
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
         "audio_item": None, "video_item": None, "clip": None, "multi_clip": None}
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
        # Q2 needs a source with several audio streams; take the first such clip at the root.
        t["multi_clip"] = next((c for c in clips if _mapping_tracks(c.GetAudioMapping()) >= 2), None)
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
    ctx.call("TimelineItem.GetFades", a, "GetFades", values=True)
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
    ctx.call("TimelineItem.GetSpeed", v, "GetSpeed", values=True)


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


def r0(ctx, t):
    """Grade surface: nodes, LUT, label, tools, CDL on the first video item (reads only)."""
    v = t["video_item"]
    g = ctx.call("TimelineItem.GetNodeGraph", v, "GetNodeGraph")
    n = ctx.call("Graph.GetNumNodes", g, "GetNumNodes") if g is not None else None
    if isinstance(n, int) and n > 0:
        for m in ("GetLUT", "GetNodeLabel", "GetToolsInNode", "GetNodeEnabled"):
            ctx.call(f"Graph.{m}[1]", g, m, 1)
        ctx.call("TimelineItem.GetCDL[1]", v, "GetCDL", 1)
    for m in ("SetLUT", "SetNodeLabel", "ApplyGradeFromDRX", "ResetAllGrades"):
        ctx.look(f"Graph.{m}", g, m)  # looked up, never called
    got = ctx.call("Project.GetSettings", t["project"], "GetSettings")
    if isinstance(got, dict):
        ctx.note("Project.GetSettings.colorScienceMode", str(got.get("colorScienceMode")))


def r5(ctx, t):
    """Input colour state of a pool clip."""
    for k in ("Input Color Space", "Input Gamma", "Data Level"):
        ctx.call(f"MediaPoolItem.GetClipProperty[{k}]", t["clip"], "GetClipProperty", k)
    ctx.look("MediaPoolItem.SetClipProperty", t["clip"], "SetClipProperty")


def r9(ctx, t):
    """Every clip property key (a key listing)."""
    ctx.call("MediaPoolItem.GetClipProperty[all]", t["clip"], "GetClipProperty")


def r10(ctx, t):
    """Every project setting key, and whether any names Camera Raw."""
    got = ctx.call("Project.GetSettings[all]", t["project"], "GetSettings")
    if isinstance(got, dict):
        hits = sorted(k for k in got if "raw" in str(k).lower())
        ctx.note("Project.GetSettings.raw_keys", f"{len(hits)} key(s) mention raw")


def s4(ctx, t):
    ctx.call("Resolve.GetEffectsList[audio]", t["resolve"], "GetEffectsList", "audio")
    ctx.call("TimelineItem.GetEffects[audio]", t["audio_item"], "GetEffects", "audio")
    ctx.look("TimelineItem.AddEffect", t["audio_item"], "AddEffect")


def s9(ctx, t):
    fmts = ctx.call("Project.GetAudioRenderFormats", t["project"], "GetAudioRenderFormats")
    if isinstance(fmts, dict) and fmts:
        ctx.call("Project.GetAudioRenderCodecs", t["project"], "GetAudioRenderCodecs", str(next(iter(fmts.values()))))


def m0(ctx, t):
    ctx.look("Resolve.Fusion", t["resolve"], "Fusion")
    for m in ("ExportFusionComp", "ImportFusionComp", "LoadFusionCompByName", "RenameFusionCompByName"):
        ctx.look(f"TimelineItem.{m}", t["video_item"], m)
    for m in ("InsertTitleIntoTimeline", "InsertFusionTitleIntoTimeline", "InsertGeneratorIntoTimeline"):
        ctx.look(f"Timeline.{m}", t["timeline"], m)


EXPORT_NAMES = ("EXPORT_AAF", "EXPORT_DRT", "EXPORT_EDL", "EXPORT_FCP_7_XML", "EXPORT_FCPXML_1_10",
                "EXPORT_OTIO", "EXPORT_ALE_CDL", "EXPORT_AAF_NEW", "EXPORT_CDL", "EXPORT_NONE")


def i0(ctx, t):
    ctx.look("MediaPool.ImportTimelineFromFile", t["pool"], "ImportTimelineFromFile")
    ctx.look("Timeline.Export", t["timeline"], "Export")
    ctx.look("Timeline.ImportIntoTimeline", t["timeline"], "ImportIntoTimeline")
    r = ctx.raw(t["resolve"])
    missing = [n for n in EXPORT_NAMES if getattr(r, n, None) is None]
    ctx.note("Resolve.export_constants", "all present" if not missing else "absent: " + ", ".join(missing))


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
    ("R0", "Grade surface: node graph, LUT, label, tools, CDL, colour science mode", r0),
    ("R5", "Input colour space, gamma and data level of a pool clip", r5),
    ("R9", "Every clip property key", r9),
    ("R10", "Every project setting key (GetSettings); any that names raw", r10),
    ("S4", "Audio effects list and an item's audio effects", s4),
    ("S9", "Audio render formats and codecs", s9),
    ("M0", "Fusion, comp export and import, title insert calls present", m0),
    ("I0", "Timeline import and export calls and export constants present", i0),
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



def s_q1(ctx, t):
    """Create a project, see whether it becomes current, switch back, delete only what this run made."""
    pm, original, name = t["pm"], t["project"].GetName(), PREFIX + "q1"
    if name in (pm.GetProjectListInCurrentFolder() or []):
        ctx.note("setup", f"refused: a project named {name} already exists; this check never deletes one it did not make")
        return
    made = pm.CreateProject(name)
    if made is None:
        ctx.note("ProjectManager.CreateProject", "returned None; nothing made, nothing saved, nothing deleted")
        return
    ctx.note("ProjectManager.CreateProject", "returned a project")
    now = pm.GetCurrentProject()
    ctx.note("ProjectManager.CreateProject.becomes_current",
             "yes" if now is not None and now.GetName() == name else "no")
    pm.SaveProject()
    pm.LoadProject(original)
    back = pm.GetCurrentProject()
    if back is None or back.GetName() != original:
        ctx.note("setup", f"the sandbox is not current again; {name} was left in place, remove it by hand")
        return
    ctx.call("ProjectManager.DeleteProject", pm, "DeleteProject", name)


def _track_mapping(text):
    try:
        data = json.loads(text) if isinstance(text, str) else text
        return (data or {}).get("track_mapping")
    except (ValueError, AttributeError, TypeError):
        return None


def _mapping_tracks(text):
    try:
        data = json.loads(text) if isinstance(text, str) else text
        return len((data or {}).get("track_mapping") or {})
    except (ValueError, AttributeError, TypeError):
        return 0


def s_q2(ctx, t):
    """The T36 route: remap a multi-stream source BEFORE the append, read the mapping back, then
    append and count audio tracks. The verdict needs a clip whose mapping lists more than one
    track, and a remap that was accepted and read back; otherwise it says "not testable"."""
    clip = t.get("multi_clip") or t["clip"]
    if clip is None:
        ctx.note("setup", "the sandbox media pool has no clip at its root; import one first")
        return
    before = clip.GetAudioMapping()
    streams = _mapping_tracks(before)
    if streams < 2:
        ctx.note("verdict.remap_before_append",
                 f"not testable: no clip at the pool root lists several tracks; the first lists {streams} track(s); use a clip with several streams")
        return
    def restore():
        clip.SetAudioMapping(before)
        back = ctx.call("MediaPoolItem.GetAudioMapping.after_restore", clip, "GetAudioMapping")
        ctx.note("MediaPoolItem.GetAudioMapping.restore_matches_original",
                 "could not be read back" if back is None
                 else "same as the original" if _track_mapping(back) == _track_mapping(before) else "differs from the original")
    ctx.cleanups.append(restore)
    mapping = json.dumps({"track_mapping": {"1": {"channel_idx": [1, 2], "mute": False, "type": "stereo"}}})
    accepted = ctx.call("MediaPoolItem.SetAudioMapping", clip, "SetAudioMapping", mapping)
    after = ctx.call("MediaPoolItem.GetAudioMapping.after", clip, "GetAudioMapping")
    same = _track_mapping(after) == _track_mapping(mapping)  # Resolve adds embedded_audio_channels and linked_audio
    ctx.note("MediaPoolItem.GetAudioMapping.read_back", "same as what was set" if same else f"differs from what was set; track_mapping read back: {json.dumps(_track_mapping(after))}")  # never the raw dump: linked_audio carries file paths
    tl, items = _scratch_timeline(ctx, dict(t, clip=clip), "q2")  # append the remapped clip, not the first one
    if tl is None:
        return
    per = [len(tl.GetItemListInTrack("audio", n) or []) for n in range(1, (tl.GetTrackCount("audio") or 0) + 1)]
    ctx.note("Timeline.audio_tracks_after_append",
             f"{len(per)} audio track(s), " + ", ".join(map(str, per)) + " item(s) per track")
    if accepted is not True or not same:
        verdict = "not testable: the remap was not accepted or did not read back"
    elif len(per) == 1:
        verdict = "no spill: confirmed"
    else:
        verdict = "spill remains: killed"
    ctx.note("verdict.remap_before_append", verdict)


def _norm(v):
    try:
        return json.loads(v) if isinstance(v, str) else v
    except ValueError:
        return v


def s_q3(ctx, t):
    tl, items = _scratch_timeline(ctx, t, "q3")
    if tl is None or not items:
        return
    item = items[0]
    ctx.call("TimelineItem.SetFades", item, "SetFades", {"FadeIn": 0, "FadeOut": 12})
    ctx.call("TimelineItem.GetFades.after", item, "GetFades", values=True)
    ctx.call("TimelineItem.AddTransition.audio", item, "AddTransition",
             {"type": "Cross Fade +3 dB", "category": "audio", "position": "end",
              "alignment": "left", "duration": 12})


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
        return
    item = items[0]
    before = item.GetProperties()
    ctx.call("TimelineItem.Stabilize", item, "Stabilize")
    after = item.GetProperties()
    ctx.note("TimelineItem.Stabilize.properties_changed", "yes" if before != after else "no")


def s_q6(ctx, t):
    tl, items = _scratch_timeline(ctx, t, "q6")
    if tl is None or not items:
        return
    item = items[0]
    ctx.call("TimelineItem.SetSpeed[50]", item, "SetSpeed", {"Percentage": 50.0})
    ctx.call("TimelineItem.GetSpeed.after", item, "GetSpeed", values=True)
    item.SetSpeed({"Percentage": 100.0})


def s_q7(ctx, t):
    tl, items = _scratch_timeline(ctx, t, "q7")
    if tl is None or not items:
        return
    for name in ("Cross Dissolve", "Dip to Color Dissolve"):
        got = ctx.call(f"TimelineItem.AddTransition[{name}]", items[0], "AddTransition",
                       {"type": name, "category": "simple", "position": "end",
                        "alignment": "center", "duration": 12})
        if got is not None:
            ctx.note(f"TimelineItem.AddTransition[{name}].type_read_back",
                     str((got.GetProperties() or {}).get("TransitionType")))


def s_q8(ctx, t):
    r = t["resolve"]
    ctx.call("Resolve.ValidateDCTL[ok]", r, "ValidateDCTL",
             "__DEVICE__ float3 transform(int p_Width, int p_Height, int p_X, int p_Y, "
             "float p_R, float p_G, float p_B) { return make_float3(p_R, p_G, p_B); }")
    ctx.call("Resolve.ValidateDCTL[bad]", r, "ValidateDCTL", "this is not a dctl")


def s_q9(ctx, t):
    tl, items = _scratch_timeline(ctx, t, "q9")
    if tl is None or not items:
        return
    item = items[0]
    ctx.call("TimelineItem.AddFusionComp", item, "AddFusionComp")
    ctx.call("TimelineItem.GetFusionCompCount.after", item, "GetFusionCompCount")
    ctx.call("TimelineItem.CreateMagicMask[F]", item, "CreateMagicMask", "F")


def s_q10(ctx, t):
    presets = t["resolve"].GetFairlightPresets() or []
    ctx.note("Resolve.GetFairlightPresets", f"{len(presets)} preset(s); none applied (applying switches the timeline)")


def s_q11(ctx, t):
    tl, items = _scratch_timeline(ctx, t, "q11")
    if tl is None or not items:
        return
    item = items[0]
    ctx.call("TimelineItem.SetProperties[AudioVolume]", item, "SetProperties", {"AudioVolume": -6.0})
    props = item.GetProperties() or {}
    ctx.note("TimelineItem.AudioVolume.read_back", str(props.get("AudioVolume")))
    item.SetProperties({"AudioVolume": 0.0})


def s_q12(ctx, t):
    ctx.note("Project.StartRendering", "never called by this probe; start a render by hand and read the job status")


def _tmpdir(ctx):
    import shutil
    import tempfile
    d = tempfile.mkdtemp(prefix="rp-probe-")
    ctx.cleanups.append(lambda: shutil.rmtree(d, ignore_errors=True))
    return d


def s_r3(ctx, t):
    tl, items = _scratch_timeline(ctx, t, "r3")
    if tl is None or not items:
        return
    item = items[0]
    ctx.call("TimelineItem.SetCDL", item, "SetCDL", {"NodeIndex": 1, "Slope": "1.1 1.1 1.1",
                                                     "Offset": "0 0 0", "Power": "1 1 1", "Saturation": 0.9})
    got = ctx.call("TimelineItem.GetCDL.after", item, "GetCDL", 1)
    slope = str((got or {}).get("Slope", "")).split() if isinstance(got, dict) else []
    try:
        ok = len(slope) == 3 and all(abs(float(x) - 1.1) < 1e-3 for x in slope)
    except ValueError:
        ok = False
    ctx.note("TimelineItem.GetCDL.read_back", "same as set" if ok else "differs from set or not read")


def s_r7(ctx, t):
    tl, items = _scratch_timeline(ctx, t, "r7")
    if tl is None or not items:
        return
    project = t["project"]
    group = ctx.call("Project.AddColorGroup", project, "AddColorGroup", PREFIX + "look")
    if group is None:
        return
    raw = ctx.raw(group)
    ctx.cleanups.append(lambda: project.DeleteColorGroup(raw))
    ctx.call("TimelineItem.AssignToColorGroup", items[0], "AssignToColorGroup", raw)
    back = items[0].GetColorGroup()
    ctx.note("TimelineItem.GetColorGroup.read_back",
             "the group assigned" if back is not None and back.GetName() == PREFIX + "look" else "not the group assigned")
    post = raw.GetPostClipNodeGraph()
    ctx.note("ColorGroup.GetPostClipNodeGraph", "present" if post is not None else "None")


def s_s2(ctx, t):
    tl, items = _scratch_timeline(ctx, t, "s2")
    if tl is None:
        return
    for sub in ("mono", "stereo", "5.1", "adaptive1"):  # the scratch timeline is deleted at cleanup
        before = tl.GetTrackCount("audio") or 0
        ctx.call(f"Timeline.AddTrack[{sub}]", tl, "AddTrack", "audio", sub)
        after = tl.GetTrackCount("audio") or 0
        ctx.note(f"Timeline.GetTrackSubType[{sub}]",
                 str(tl.GetTrackSubType("audio", after)) if after > before else "no track made")


def s_s7(ctx, t):
    tl, items = _scratch_timeline(ctx, t, "s7")
    if tl is None:
        return
    audio = (tl.GetItemListInTrack("audio", 1) or [None])[0]
    modes = [m for m in (tl.GetNormalizeAudioModes() or []) if "peak" not in m.lower()]
    if audio is None or not modes:
        ctx.note("setup", "no audio item or no loudness mode on the scratch timeline")
        return
    ctx.call("TimelineItem.AnalyzeAudioLevels.before", audio, "AnalyzeAudioLevels", modes[0])
    ctx.call("Timeline.NormalizeAudioLevel", tl, "NormalizeAudioLevel", [audio],
             {"normalizationMode": modes[0], "targetLoudness": -23.0})
    ctx.call("TimelineItem.AnalyzeAudioLevels.after", audio, "AnalyzeAudioLevels", modes[0])


def s_m1(ctx, t):
    tl, items = _scratch_timeline(ctx, t, "m1")
    if tl is None or not items:
        return
    item = items[0]
    comp = ctx.call("TimelineItem.AddFusionComp", item, "AddFusionComp")
    if comp is None:
        return
    ctx.call("FusionComp.AddTool[TextPlus]", comp, "AddTool", "TextPlus")
    path = os.path.join(_tmpdir(ctx), "comp.comp")
    ctx.call("TimelineItem.ExportFusionComp", item, "ExportFusionComp", path, 1)
    ctx.note("TimelineItem.ExportFusionComp.file", f"{os.path.getsize(path)} byte(s)" if os.path.exists(path) else "no file")


def s_m3(ctx, t):
    tl, items = _scratch_timeline(ctx, t, "m3")
    if tl is None:
        return
    titled = ctx.call("Timeline.InsertTitleIntoTimeline", tl, "InsertTitleIntoTimeline", "Text")
    if titled is not None:
        ctx.call("TimelineItem.GetFusionCompCount", titled, "GetFusionCompCount")


def s_i1(ctx, t):
    tl, items = _scratch_timeline(ctx, t, "i1")
    if tl is None:
        return
    r = ctx.raw(t["resolve"])
    d = _tmpdir(ctx)
    none = getattr(r, "EXPORT_NONE", None)
    for name, ext in (("EXPORT_FCPXML_1_10", "fcpxml"), ("EXPORT_FCP_7_XML", "xml"), ("EXPORT_OTIO", "otio"),
                      ("EXPORT_EDL", "edl"), ("EXPORT_AAF", "aaf")):
        const = getattr(r, name, None)
        if const is None:
            ctx.note(f"Timeline.Export[{name}]", "constant absent")
            continue
        sub = getattr(r, "EXPORT_AAF_NEW", none) if name == "EXPORT_AAF" else none
        path = os.path.join(d, "t." + ext)
        ctx.call(f"Timeline.Export[{name}]", tl, "Export", path, const, sub)
        ctx.note(f"Timeline.Export[{name}].file", f"{os.path.getsize(path)} byte(s)" if os.path.exists(path) else "no file")


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
    ("R3", "SetCDL on a scratch item and read it back", s_r3),
    ("R7", "A colour group: add, assign, read back", s_r7),
    ("S2", "AddTrack for each audio track format and the subtype it reads back", s_s2),
    ("S7", "Analyse, normalise to a loudness, analyse again", s_s7),
    ("M1", "A Fusion comp with a Text+ tool, exported to a file", s_m1),
    ("M3", "Insert a standard title on a scratch timeline", s_m3),
    ("I1", "Export a scratch timeline in five formats", s_i1),
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
