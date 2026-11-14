"""contract-probe against a fake Resolve: read-only checks write nothing, sandbox
checks refuse outside the named sandbox, and rows grade against the golden file."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rpresolve import cmd_contract_probe as probe  # noqa: E402

GOLDEN = os.path.join(os.path.dirname(__file__), "fixtures", "contract_probe_golden.json")
CALLS = []


def _w(path, text):
    with open(path, "w") as f:
        f.write(text)


class Obj:
    """A fake scripting object: only the methods a subclass defines exist."""

    def __getattr__(self, name):
        raise AttributeError(name)

    def _log(self, name, *args):
        CALLS.append((type(self).__name__, name, args))


class Item(Obj):
    def GetFades(self): return {"FadeIn": 0, "FadeOut": 0}
    def SetFades(self, f): self._log("SetFades", f); return True
    def AddTransition(self, o): self._log("AddTransition", o); return None
    def GetProperties(self): return {"Pan": 0.0, "AudioVolume": 0.0}
    def SetProperties(self, p): self._log("SetProperties", p); return True
    def Stabilize(self): self._log("Stabilize"); return True
    def GetSpeed(self): return {"Percentage": 100.0}
    def SetSpeed(self, o): self._log("SetSpeed", o); return True
    def GetFusionCompCount(self): return 0
    def AddFusionComp(self): self._log("AddFusionComp"); return Comp(self)
    def ExportFusionComp(self, path, i): _w(path, "comp"); return True
    def ImportFusionComp(self, path): return None
    def LoadFusionCompByName(self, n): return None
    def RenameFusionCompByName(self, a, b): return True
    def GetNodeGraph(self, layer=None): return Graph()
    def GetColorGroup(self): return getattr(self, "group", None)
    def AssignToColorGroup(self, g): self.group = g; return True
    def GetEffects(self, c): return []
    def CreateMagicMask(self, m): self._log("CreateMagicMask", m); return False
    def GetSourceAudioChannelMapping(self): return "{}"
    def SetSourceAudioChannelMapping(self, m): return True
    def AddEffect(self, c, i): return None
    def DeleteEffects(self, e): return True
    def DeleteFusionCompByName(self, n): return False
    def ExportLUT(self, t, p): return True
    def SetCDL(self, c): self.cdl = {k: c[k] for k in ("Slope", "Offset", "Power")}; return True
    def GetCDL(self, n): return getattr(self, "cdl", {})
    def AnalyzeAudioLevels(self, s): return {"Integrated": -30.0, "TruePeak": -5.0}


class Graph(Obj):
    def GetNumNodes(self): return 1
    def GetLUT(self, n): return ""
    def GetNodeLabel(self, n): return ""
    def GetToolsInNode(self, n): return []
    def GetNodeEnabled(self, n): return True
    def SetLUT(self, n, p): self._log("SetLUT"); return True
    def SetNodeLabel(self, n, l): return True
    def ApplyGradeFromDRX(self, p, m): return True
    def ResetAllGrades(self): self._log("ResetAllGrades"); return True


class Comp(Obj):
    def __init__(self, item): self.item = item
    def AddTool(self, i, *a): self._log("AddTool", i); return Obj()


class Group(Obj):
    def GetName(self): return "rp-probe-look"
    def GetPostClipNodeGraph(self): return Graph()


class Clip(Obj):
    mapping = "{}"
    def GetAudioMapping(self): return self.mapping
    def SetAudioMapping(self, m): self._log("SetAudioMapping", m); self.mapping = m; return True
    def GetClipProperty(self, k=None): return "2" if k else {"Input Color Space": "Rec.709", "File Name": "x"}
    def SetClipProperty(self, k, v): self._log("SetClipProperty", k); return True


class Folder(Obj):
    def GetClipList(self): return [Clip()]


class Timeline(Obj):
    tracks = 1
    def GetTrackCount(self, k): return self.tracks
    def GetItemListInTrack(self, k, i): return [Item()] if i <= self.tracks else []
    def GetTrackSubType(self, k, i): return "stereo"
    def GetNormalizeAudioModes(self): return ["Sample Peak Program"]
    def NormalizeAudioLevel(self, items, o): self._log("NormalizeAudioLevel"); return True
    def AddTrack(self, kind, sub=None): self._log("AddTrack", sub); self.tracks += 1; return True
    def InsertTitleIntoTimeline(self, n): self._log("InsertTitleIntoTimeline", n); return Item()
    def InsertFusionTitleIntoTimeline(self, n): return Item()
    def InsertGeneratorIntoTimeline(self, n): return Item()
    def Export(self, path, kind, sub): self._log("Export", kind); _w(path, "x"); return True
    def ImportIntoTimeline(self, p, o=None): return True


class Pool(Obj):
    def GetRootFolder(self): return Folder()
    def ImportMedia(self, c): self._log("ImportMedia"); return []
    SPILL = True      # a multi-stream source spills onto A2 and up unless it was remapped first;
                      # "always" models a remap that changes nothing
    def AppendToTimeline(self, c):
        self._log("AppendToTimeline")
        remapped = c[0]["mediaPoolItem"].mapping != "{}"
        self.tl.tracks = 2 if self.SPILL == "always" or (self.SPILL and not remapped) else 1
        return [Item()]
    def DeleteTimelines(self, t): self._log("DeleteTimelines"); return True
    def ImportTimelineFromFile(self, p, o=None): return None
    def CreateEmptyTimeline(self, n): self._log("CreateEmptyTimeline", n); self.tl = Timeline(); return self.tl


class Project(Obj):
    def __init__(self, name): self.name = name
    def GetName(self): return self.name
    def GetMediaPool(self): return Pool()
    def GetCurrentTimeline(self): return Timeline()
    def SetCurrentTimeline(self, t): self._log("SetCurrentTimeline"); return True
    def GetProjectSettingsPresetList(self): return []
    def GetSettings(self): return {"colorScienceMode": "davinciYRGB"}
    def GetSetting(self, k=None): return "davinciYRGB" if k else {"colorScienceMode": "davinciYRGB", "timelineFrameRate": "24"}
    def AddColorGroup(self, n): self._log("AddColorGroup", n); return Group()
    def DeleteColorGroup(self, g): self._log("DeleteColorGroup"); return True
    def GetAudioRenderCodecs(self, e): return {"Linear PCM": "lpcm"}
    def SaveCurrentProjectSettingsAsNewPreset(self, n): return True
    def SetProjectSettingsPreset(self, n): return True
    def ExportProjectSettingsPreset(self, n, p): return True
    def ImportProjectSettingsPreset(self, p, n=None): return True
    def SetRenderSettings(self, s): self._log("SetRenderSettings", s); return True
    def GetRenderJobList(self): return []
    def GetRenderFormats(self): return {}
    def GetAudioRenderFormats(self): return {"WAV": "wav"}
    def RefreshLUTList(self): return True
    def ApplyFairlightPresetToCurrentTimeline(self, n): return True
    def StartRendering(self, ids, i=False): self._log("StartRendering"); return True
    def IsRenderingInProgress(self): return False


class PM(Obj):
    def __init__(self, project): self.project = project
    def GetCurrentProject(self): return self.project
    def GetProjectListInCurrentFolder(self): return ["x"]
    def CreateProject(self, n, p=None): self._log("CreateProject", n); return Project(n)
    def SaveProject(self): return True
    def CloseProject(self, p): return True
    def LoadProject(self, n): self._log("LoadProject", n); return Project(n)
    def DeleteProject(self, n): self._log("DeleteProject", n); return True


class Resolve(Obj):
    def __init__(self, name="any"): self.pm = PM(Project(name))
    def GetProjectManager(self): return self.pm
    def GetVersionString(self): return "21.2.0.6"
    def GetProductName(self): return "DaVinci Resolve Studio"
    def IsStudio(self): return True
    EXPORT_AAF = EXPORT_DRT = EXPORT_EDL = EXPORT_FCP_7_XML = EXPORT_FCPXML_1_10 = 1
    EXPORT_OTIO = EXPORT_ALE_CDL = EXPORT_AAF_NEW = EXPORT_CDL = EXPORT_NONE = 2
    def GetEffectsList(self, m=None): return [{"name": "Cross Dissolve"}]
    def GetFairlightPresets(self): return ["p1"]
    def ValidateDCTL(self, s): self._log("ValidateDCTL"); return None
    def EncryptDCTL(self, p, o=None): return True
    def Fusion(self): return Obj()


def run(name="any", **kw):
    CALLS.clear()
    golden = probe.load_golden(GOLDEN)
    return probe.run_probe(Resolve(name), golden, kw.pop("config", {}), **kw)


def rows(doc):
    return [r for c in doc["checks"] for r in c["rows"]]


class ReadOnly(unittest.TestCase):
    def test_makes_no_write_call(self):
        doc = run()
        self.assertEqual(CALLS, [])
        self.assertTrue(any(r["present"] for r in rows(doc)))

    def test_sandbox_checks_skipped_by_default(self):
        doc = run()
        sb = [c for c in doc["checks"] if c["mode"] == "sandbox-only"]
        self.assertTrue(sb)
        for c in sb:
            self.assertTrue(c["status"].startswith("skipped"))
            self.assertEqual(c["rows"], [])

    def test_guard_refuses_a_setter(self):
        ctx = probe.Context({}, read_only=True)
        item = ctx.wrap(Item())
        with self.assertRaises(probe.Refused):
            item.SetFades({})
        ctx.check_id = "T"
        ctx.call("x", item, "Stabilize")
        self.assertIn("refused", ctx.rows[-1]["note"])
        self.assertEqual(CALLS, [])

    def test_absent_call_is_reported_absent(self):
        doc = run()
        row = [r for r in rows(doc) if r["call"] == "Project.GetRenderSettings"][0]
        self.assertFalse(row["present"])
        self.assertEqual(row["golden"], "pass")

    def test_output_has_no_names(self):
        text = json.dumps(run("Secret Project"))
        self.assertNotIn("Secret Project", text)

    def test_only_filters(self):
        doc = run(only="Q0")
        self.assertEqual({c["id"] for c in doc["checks"] if c["mode"] == "read-only"}, {"Q0"})


class Sandbox(unittest.TestCase):
    CONFIG = {"probe": {"sandbox_project": "box"}}

    def test_refused_when_no_sandbox_named(self):
        doc = run("box", sandbox_checks=True)
        for c in doc["checks"]:
            if c["mode"] == "sandbox-only":
                self.assertIn("no sandbox project is named", c["status"])
        self.assertEqual(CALLS, [])

    def test_refused_when_project_differs(self):
        doc = run("other", sandbox_checks=True, config=self.CONFIG)
        self.assertFalse(doc["project_is_sandbox"])
        for c in doc["checks"]:
            if c["mode"] == "sandbox-only":
                self.assertIn("not the sandbox", c["status"])
        self.assertEqual(CALLS, [])

    def test_runs_in_the_sandbox_and_never_starts_a_render(self):
        doc = run("box", sandbox_checks=True, config=self.CONFIG)
        ran = [c for c in doc["checks"] if c["mode"] == "sandbox-only"]
        self.assertTrue(all(c["status"] == "ran" for c in ran), [c["status"] for c in ran])
        names = [c[1] for c in CALLS]
        self.assertIn("SetFades", names)
        self.assertNotIn("StartRendering", names)
        for call in CALLS:
            if call[1] in ("CreateEmptyTimeline", "CreateProject"):
                self.assertTrue(str(call[2][0]).startswith(probe.PREFIX))

    def test_cleanup_runs_when_a_check_raises(self):
        orig = probe.SANDBOX_CHECKS
        def boom(ctx, t):
            probe._scratch_timeline(ctx, t, "boom")
            raise RuntimeError("x")
        probe.SANDBOX_CHECKS = [("Q2", "boom", boom)]
        try:
            doc = run("box", sandbox_checks=True, config=self.CONFIG)
        finally:
            probe.SANDBOX_CHECKS = orig
        names = [c[1] for c in CALLS]
        self.assertIn("DeleteTimelines", names)
        status = [c["status"] for c in doc["checks"] if c["mode"] == "sandbox-only"][0]
        self.assertTrue(status.startswith("raised RuntimeError"))

    def test_scratch_timeline_is_made_current_before_the_append(self):
        run("box", sandbox_checks=True, config=self.CONFIG, only="Q6")
        names = [c[1] for c in CALLS]
        self.assertLess(names.index("SetCurrentTimeline"), names.index("AppendToTimeline"))

    def _q2(self, spill):
        # The audio-mapping route: remap, read back, append, count tracks.
        Pool.SPILL = spill
        try:
            doc = run("box", sandbox_checks=True, config=self.CONFIG, only="Q2")
        finally:
            Pool.SPILL = True
        return doc, {r["call"]: r for c in doc["checks"] if c["mode"] == "sandbox-only" for r in c["rows"]}

    def test_audio_mapping_is_set_before_the_append_and_read_back(self):
        doc, rows = self._q2(True)
        names = [c[1] for c in CALLS]
        self.assertLess(names.index("SetAudioMapping"), names.index("AppendToTimeline"))
        self.assertEqual(rows["MediaPoolItem.GetAudioMapping.read_back"]["note"], "same as what was set")

    def test_audio_mapping_verdict_confirms_when_the_spill_is_gone(self):
        doc, rows = self._q2(True)
        self.assertEqual(rows["verdict.remap_before_append"]["note"], "no spill: confirmed")

    def test_audio_mapping_verdict_kills_when_the_spill_remains(self):
        doc, rows = self._q2("always")
        self.assertTrue(rows["verdict.remap_before_append"]["note"].startswith("spill remains: killed"))

    def test_audio_mapping_is_put_back_at_cleanup(self):
        self._q2(False)
        sets = [c[2][0] for c in CALLS if c[1] == "SetAudioMapping"]
        self.assertEqual(sets[-1], "{}")

    def _sb(self, only):
        doc = run("box", sandbox_checks=True, config=self.CONFIG, only=only)
        return {r["call"]: r for c in doc["checks"] if c["mode"] == "sandbox-only" for r in c["rows"]}

    def test_cdl_read_back_is_compared(self):
        self.assertEqual(self._sb("R3")["TimelineItem.GetCDL.read_back"]["note"], "same as set")

    def test_each_track_format_reports_its_subtype(self):
        rows = self._sb("S2")
        self.assertEqual(sorted(k for k in rows if k.startswith("Timeline.GetTrackSubType[")),
                         ["Timeline.GetTrackSubType[5.1]", "Timeline.GetTrackSubType[adaptive]",
                          "Timeline.GetTrackSubType[mono]", "Timeline.GetTrackSubType[stereo]"])

    def test_timeline_exports_write_five_files_that_are_then_removed(self):
        rows = self._sb("I1")
        files = [r["note"] for k, r in rows.items() if k.endswith(".file")]
        self.assertEqual(len(files), 5)
        self.assertTrue(all("byte" in n for n in files), files)
        import glob, tempfile
        self.assertEqual(glob.glob(os.path.join(tempfile.gettempdir(), "rp-probe-*")), [])

    def test_color_group_is_removed_at_cleanup(self):
        self._sb("R7")
        self.assertIn("DeleteColorGroup", [c[1] for c in CALLS])

    def test_no_forbidden_grade_call_is_made_by_the_read_checks(self):
        run("box")
        self.assertEqual([c for c in CALLS if c[1] in ("SetLUT", "ResetAllGrades")], [])

    def test_flag_missing_skips_even_in_the_sandbox(self):
        doc = run("box", config=self.CONFIG)
        self.assertEqual(CALLS, [])
        self.assertTrue(doc["project_is_sandbox"])


class Golden(unittest.TestCase):
    def test_every_row_passes_the_shipped_golden_on_the_fake(self):
        doc = run()
        bad = [r for r in rows(doc) if r["golden"] == "differs"]
        self.assertEqual(bad, [])
        self.assertEqual(doc["summary"]["no_golden"] - sum(1 for r in rows(doc) if r["present"] is None), 0)

    def test_a_changed_golden_reads_differs(self):
        doc = probe.run_probe(Resolve(), {"Q0:Resolve.IsStudio": {"present": True, "type": "str"}}, {}, only="Q0")
        row = [r for r in rows(doc) if r["call"] == "Resolve.IsStudio"][0]
        self.assertEqual(row["golden"], "differs")


if __name__ == "__main__":
    unittest.main()
