"""
Shared fakes of the Resolve scripting objects for the MCP tool tests.

Every fake answers the getters the tools use. Any other method is still
callable, returns None and is recorded in CALLS, so a test can assert that
a read tool never reached for a setter (SetCurrentTimeline, OpenPage,
SetCurrentFolder, ...) that the fake does not implement. The setters the
write tools need are implemented, and record themselves in CALLS too.
"""

import copy
import itertools


CALLS = []
MUTATING_PREFIXES = ("Set", "Open", "Load", "Create", "Delete", "Add", "Import", "Append",
                     "Start", "Stop", "Save", "Close", "Duplicate", "Apply", "Insert",
                     "Move", "Relink", "Export", "Refresh", "Clear", "Replace")


def mutating_calls():
    return [c for c in CALLS if c[1].startswith(MUTATING_PREFIXES)]


def _log(obj, name, *args):
    CALLS.append((type(obj).__name__, name, args))


# path -> [(label, lut, tools)]: the graph ApplyGradeFromDRX leaves behind.
DRX_GRAPHS = {}


class Fake:
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def unknown(*args):
            CALLS.append((type(self).__name__, name, args))
            return None
        return unknown


class Graph(Fake):
    def __init__(self, nodes):
        self.nodes = nodes  # [(label, lut, tools)]

    def GetNumNodes(self): return len(self.nodes)
    def GetNodeLabel(self, i): return self.nodes[i - 1][0]
    def GetLUT(self, i): return self.nodes[i - 1][1]
    def GetToolsInNode(self, i): return self.nodes[i - 1][2]

    def SetLUT(self, i, path):
        _log(self, "SetLUT", i, path)
        if not 1 <= i <= len(self.nodes):
            return False
        label, _, _ = self.nodes[i - 1]
        self.nodes[i - 1] = (label, path, ["LUT: " + path.rsplit("/", 1)[-1]])
        return True

    def ApplyGradeFromDRX(self, path, mode):
        _log(self, "ApplyGradeFromDRX", path, mode)
        if path not in DRX_GRAPHS:
            return False
        self.nodes[:] = [tuple(n) for n in DRX_GRAPHS[path]]
        return True


class Clip(Fake):
    def __init__(self, name, uid, props=None):
        self.name, self.uid, self.props = name, uid, dict(props or {})

    def GetName(self): return self.name
    def GetUniqueId(self): return self.uid

    def GetClipProperty(self, key=None):
        return dict(self.props) if key is None else self.props.get(key, "")


class Item(Fake):
    def __init__(self, name, start, end, clip=None, nodes=(("", "", None),), props=None,
                 source=(0, None), version=None):
        self.name, self.start, self.end, self.clip = name, start, end, clip
        self.graph = Graph(list(nodes)) if nodes is not None else None
        self.props = dict(props or {})
        self.source, self.version = source, version

    def GetName(self): return self.name
    def GetStart(self): return self.start
    def GetEnd(self): return self.end
    def GetDuration(self): return self.end - self.start
    def GetLeftOffset(self): return 0
    def GetSourceStartFrame(self): return self.source[0]
    def GetSourceEndFrame(self): return self.source[1]
    def GetClipEnabled(self): return True
    def GetMediaPoolItem(self): return self.clip
    def GetProperty(self, key=None): return dict(self.props) if key is None else self.props.get(key)
    def GetNodeGraph(self): return self.graph
    def GetCurrentVersion(self): return self.version


class Timeline(Fake):
    def __init__(self, name, uid, fps=25.0, start=86400, end=86400, tracks=None, settings=None):
        self.name, self.uid, self.fps, self.start, self.end = name, uid, fps, start, end
        self.tracks = tracks or {"video": [[]], "audio": [[]]}  # kind -> [track items]
        self.settings = dict(settings or {})
        self.disabled = set()  # (kind, index) of tracks switched off
        # Caption items CreateSubtitlesFromAudio makes; 0 is a Resolve that
        # returns True and places nothing.
        self.auto_captions = 3

    def GetName(self): return self.name
    def GetUniqueId(self): return self.uid
    def GetStartFrame(self): return self.start
    def GetEndFrame(self): return self.end
    def GetTrackCount(self, kind): return len(self.tracks.get(kind, []))
    def GetIsTrackEnabled(self, kind, index):
        # Like Resolve 21.0.4.5: every track of a timeline that is not current reads False.
        project = getattr(self, "project", None)
        if project is not None and project.current is not self:
            return False
        return (kind, index) not in self.disabled

    def GetSetting(self, key=None):
        return self.fps if key == "timelineFrameRate" else self.settings.get(key)

    def GetItemListInTrack(self, kind, index):
        tracks = self.tracks.get(kind, [])
        return list(tracks[index - 1]) if 1 <= index <= len(tracks) else None

    def CreateSubtitlesFromAudio(self, settings=None):
        # Live (2026-09-29): on the current timeline, from the Edit page, it returns
        # True once the items are on a subtitle track; from the Deliver page it
        # returns False and makes nothing. Other pages, and a timeline that is not
        # current, are untried; this fake refuses both, so a test shows the caller
        # made the timeline current and opened the Edit page.
        _log(self, "CreateSubtitlesFromAudio", dict(settings or {}))
        project = self.__dict__.get("project")  # a Fake answers any other name
        if project is not None and project.current is not self:
            return False
        resolve = project.__dict__.get("resolve") if project is not None else None
        if resolve is not None and resolve.page != "edit":
            return False
        if not self.auto_captions:
            return True
        items = [Item(f"Caption {n}", self.start + 12 * n, self.start + 12 * n + 10, None,
                      nodes=None) for n in range(self.auto_captions)]
        subs = self.tracks.setdefault("subtitle", [])
        empty = next((t for t in subs if not t), None)
        if empty is None:
            subs.append(items)
        else:
            empty.extend(items)
        return True

    def DuplicateTimeline(self, name=None):
        _log(self, "DuplicateTimeline", name)
        project = getattr(self, "project", None)
        tracks = {k: [[_copy_item(it) for it in track] for track in v]
                  for k, v in self.tracks.items()}
        dup = Timeline(name or self.name + " copy", f"{self.uid}-dup{len(CALLS)}", self.fps,
                       self.start, self.end, tracks)
        if project is not None:
            project.add(dup)
            project.current = dup  # Resolve makes the copy current (spike 18)
        return dup


def _copy_item(it):
    new = copy.copy(it)
    new.graph = Graph([tuple(n) for n in it.graph.nodes]) if it.graph else None
    return new


class Folder(Fake):
    def __init__(self, name, clips=(), subs=()):
        self.name, self.clips, self.subs = name, list(clips), list(subs)

    def GetName(self): return self.name
    def GetClipList(self): return list(self.clips)
    def GetSubFolderList(self): return list(self.subs)


class Pool(Fake):
    def __init__(self, root):
        self.root = root

    def GetRootFolder(self): return self.root

    def ImportMedia(self, paths):
        _log(self, "ImportMedia", list(paths))
        return [Clip(p.rsplit("/", 1)[-1], f"import-{len(CALLS)}") for p in paths]

    def AppendToTimeline(self, items):
        # Resolve 21.0.4.5 (spike 18): True for an imported .srt, and nothing placed.
        _log(self, "AppendToTimeline", items)
        return True


class Project(Fake):
    def __init__(self, name="RP Automation Sandbox", uid="sandbox-id", timelines=(),
                 current=None, root=None, jobs=(), rendering=False):
        self.name, self.uid = name, uid
        self.timelines = []
        for tl in timelines:
            self.add(tl)
        self.fmt = {"format": "mov", "codec": "ProRes422HQ"}
        self.render_settings = {}
        self._ids = itertools.count(1)
        self.current = current
        self.pool = Pool(root or Folder("Master"))
        self.jobs = [dict(j) for j in jobs]
        self.rendering = rendering
        self.refuse = set()  # SetRenderSettings keys this fake refuses
        self.render_mode = 1  # 0 Individual clips, 1 Single clip
        self.stuck_mode = False  # SetCurrentRenderMode says yes and changes nothing
        self.settings = {"colorScienceMode": "davinciYRGBColorManagedv2",
                         "timelineResolutionWidth": "3840", "timelineResolutionHeight": "2160"}

    def GetName(self): return self.name
    def GetUniqueId(self): return self.uid
    def GetTimelineCount(self): return len(self.timelines)

    def GetTimelineByIndex(self, i):
        return self.timelines[i - 1] if 1 <= i <= len(self.timelines) else None

    def GetCurrentTimeline(self): return self.current
    def GetMediaPool(self): return self.pool
    def GetSetting(self, key=None): return self.settings.get(key)
    def GetRenderJobList(self): return [dict(j) for j in self.jobs]

    def GetRenderJobStatus(self, job_id):
        job = next((j for j in self.jobs if j["JobId"] == job_id), None)
        return {"JobStatus": job.get("_status", "Ready"), "CompletionPercentage": 0} if job else {}

    def IsRenderingInProgress(self): return self.rendering

    def GetCurrentRenderFormatAndCodec(self):
        return dict(self.fmt)

    def add(self, tl):
        tl.project = self
        self.timelines.append(tl)

    def SetCurrentTimeline(self, tl):
        _log(self, "SetCurrentTimeline", getattr(tl, "name", tl))
        self.current = tl
        return True

    def GetRenderCodecs(self, fmt):
        return {"mov": {"Apple ProRes 422 HQ": "ProRes422HQ"},
                "mp4": {"H.264": "H264", "H.265": "H265"}}.get(fmt, {})

    def SetCurrentRenderFormatAndCodec(self, fmt, codec):
        _log(self, "SetCurrentRenderFormatAndCodec", fmt, codec)
        if fmt == "unknown":
            return False
        self.fmt = {"format": fmt, "codec": codec}
        return True

    def GetCurrentRenderMode(self):
        return self.render_mode

    def SetCurrentRenderMode(self, mode):
        _log(self, "SetCurrentRenderMode", mode)
        if mode not in (0, 1):
            return False
        if not self.stuck_mode:
            self.render_mode = mode
        return True

    def SetRenderSettings(self, settings):
        # Resolve keeps every field until something sets it again.
        _log(self, "SetRenderSettings", settings)
        if self.refuse & set(settings):
            return False
        self.render_settings.update(settings)
        return True

    def RefreshLUTList(self):
        _log(self, "RefreshLUTList")
        return True

    def AddRenderJob(self):
        _log(self, "AddRenderJob")
        rs = self.render_settings
        jid = f"job-{next(self._ids)}"
        self.jobs.append({"JobId": jid, "RenderJobName": f"Job {len(self.jobs) + 1}",
                          "TimelineName": self.current.GetName(),
                          "TargetDir": rs.get("TargetDir"),
                          "OutputFilename": f"{rs.get('CustomName')}.{self.fmt['format']}",
                          "FormatWidth": rs.get("FormatWidth"),
                          "FormatHeight": rs.get("FormatHeight"),
                          "VideoFormat": self.fmt["format"], "VideoCodec": self.fmt["codec"],
                          **{k: rs[k] for k in ("FrameRate", "AudioCodec", "AudioSampleRate",
                                                "AudioBitDepth") if k in rs}})
        return jid


class ProjectManager(Fake):
    def __init__(self, project):
        self.project = project

    def GetCurrentProject(self): return self.project


class Resolve(Fake):
    # Constants as Resolve 21.0.4.5 answers them (read live, 2026-09-29): floats.
    SUBTITLE_LANGUAGE = 0.0
    SUBTITLE_CAPTION_PRESET = 1.0
    SUBTITLE_CHARS_PER_LINE = 2.0
    SUBTITLE_LINE_BREAK = 3.0
    SUBTITLE_GAP = 4.0
    AUTO_CAPTION_AUTO = 0.0
    AUTO_CAPTION_ENGLISH = 3.0
    AUTO_CAPTION_SUBTITLE_DEFAULT = 0.0
    AUTO_CAPTION_LINE_SINGLE = 1.0
    AUTO_CAPTION_LINE_DOUBLE = 2.0

    def __init__(self, project, page="edit"):
        self.pm, self.page = ProjectManager(project), page
        project.resolve = self

    def __getattr__(self, name):
        # An unknown resolve.CONSTANT reads as None, silently, as in Resolve.
        if name.isupper():
            return None
        return Fake.__getattr__(self, name)

    def GetProjectManager(self): return self.pm
    def GetProductName(self): return "DaVinci Resolve Studio"
    def GetVersionString(self): return "21.0.4.5"
    def GetCurrentPage(self): return self.page

    def OpenPage(self, page):
        _log(self, "OpenPage", page)
        self.page = page
        return True


class Session:
    """Stands in for ResolveSession."""

    def __init__(self, resolve):
        self.resolve = resolve

    def get(self):
        return self.resolve

    def project(self):
        pm = self.resolve.GetProjectManager()
        return self.resolve, pm, pm.GetCurrentProject()
