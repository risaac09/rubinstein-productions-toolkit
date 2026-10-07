"""
Shared fakes of the Resolve scripting objects for the MCP tool tests.

Every fake answers the getters the tools use. Any other method is still
callable, returns None and is recorded in CALLS, so a test can assert that
a read tool never reached for a setter (SetCurrentTimeline, OpenPage,
SetCurrentFolder, ...) that the fake does not implement. The setters the
write tools need are implemented, and record themselves in CALLS too.
"""

import contextlib
import copy
import itertools
import os
from unittest import mock


CALLS = []
MUTATING_PREFIXES = ("Set", "Open", "Load", "Create", "Delete", "Add", "Import", "Append",
                     "Start", "Stop", "Save", "Close", "Duplicate", "Apply", "Insert",
                     "Move", "Relink", "Export", "Refresh", "Clear", "Replace")


def mutating_calls():
    return [c for c in CALLS if c[1].startswith(MUTATING_PREFIXES)]


def _log(obj, name, *args):
    CALLS.append((type(obj).__name__, name, args))


def _put_env(name, value):
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


def set_env(test, name, value):
    """Set the environment variable `name` to `value` (None unsets it) for one
    test. The whole environment is put back when the test ends, so nothing
    leaks into the next one."""
    patcher = mock.patch.dict(os.environ)
    patcher.start()
    test.addCleanup(patcher.stop)
    _put_env(name, value)


@contextlib.contextmanager
def scoped_env(name, value):
    """`name` set to `value` (None unsets it) inside the block only; the whole
    environment is as it was after it."""
    with mock.patch.dict(os.environ):
        _put_env(name, value)
        yield


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
        self.linked = None  # (audio clip, offset in timeline frames) after AutoSyncAudio

    def has(self, kind):
        """Whether the clip carries picture ('video') or sound ('audio'), from
        its Type property as Resolve reports it ("Video + Audio", "Audio")."""
        kind_prop = str(self.props.get("Type", "Video + Audio")).lower()
        return kind in kind_prop

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

    def SetProperty(self, key, value):
        # A key in `ignored` answers True and keeps its old value, as a Resolve
        # with a modal dialog open does.
        _log(self, "SetProperty", key, value)
        if key not in self.__dict__.get("ignored", ()):
            self.props[key] = value
        return True

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
        self.subtypes = {}  # (kind, index) -> audio type of a track AddTrack made
        self.markers = {}   # float frame from the start -> marker dict, as GetMarkers gives
        self.refuse_add_track = False  # AddTrack returns True and adds nothing
        self.tc_start = False  # the start frame follows the rate (01:00:00:00), as a new timeline's

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

    def GetSettings(self):
        # As the 21.1 README says: the project's settings while the timeline follows them, its
        # own over them once it is custom. Unlogged, like the other getters. The project is read
        # through __dict__ because a Fake answers any other attribute name.
        project = self.__dict__.get("project")
        got = dict(project.settings) if project is not None else {}
        if self.settings.get("useCustomSettings") == "1":
            got.update(self.settings)
            got["timelineFrameRate"] = self.fps
        return got

    def GetItemListInTrack(self, kind, index):
        tracks = self.tracks.get(kind, [])
        return list(tracks[index - 1]) if 1 <= index <= len(tracks) else None

    def holds_items(self):
        return any(track for tracks in self.tracks.values() for track in tracks)

    def SetSetting(self, key, value):
        _log(self, "SetSetting", key, value)
        if key == "timelineFrameRate":
            # Fixed once the timeline holds a clip, as in Resolve.
            if self.holds_items():
                return False
            self.fps = float(value)
            if self.tc_start:
                self.start = self.end = int(round(3600 * self.fps))
        if key == "useCustomSettings" and str(value) == "1":
            # Opt-in, off by default: a project's flip_resets (a dict) is what a forum thread
            # reports Resolve changing the first time a timeline goes custom, and
            # flip_each_time makes a repeat write change it again. Neither is verified here.
            project = self.__dict__.get("project")
            flip = project.__dict__.get("flip_resets") if project is not None else None
            if flip and (not self.__dict__.get("flipped") or project.__dict__.get("flip_each_time")):
                self.settings.update(flip)
                self.flipped = True
        self.settings[key] = value
        return True

    def AddTrack(self, kind, *sub):
        _log(self, "AddTrack", kind, *sub)
        if self.refuse_add_track or kind not in ("video", "audio", "subtitle"):
            return True
        self.tracks.setdefault(kind, []).append([])
        if kind == "audio":  # mono when no type is given, as the README says
            self.subtypes[(kind, len(self.tracks[kind]))] = (sub[0] if sub and isinstance(
                sub[0], str) else "mono")
        return True

    def GetTrackSubType(self, kind, index):
        if kind != "audio":
            return ""
        return self.subtypes.get((kind, index), "stereo")

    def AddMarker(self, frame, color, name, note, duration, custom=""):
        # One marker per frame, a colour Resolve offers, inside the timeline.
        _log(self, "AddMarker", frame, color, name, note, duration, custom)
        f = float(frame)
        if (f in self.markers or color not in MARKER_COLORS or f < 0
                or f >= self.end - self.start or duration < 1):
            return False
        self.markers[f] = {"color": color, "duration": float(duration), "note": note,
                           "name": name, "customData": custom}
        return True

    def GetMarkers(self):
        return {k: dict(v) for k, v in self.markers.items()}

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
        if project is not None and project.__dict__.get("dup_copies_settings"):
            # Unverified: whether Resolve's copy of a custom timeline keeps its settings. Off by
            # default, which keeps the copy starting from the project's as it always did here.
            dup.settings = dict(self.settings)
            dup.flipped = self.__dict__.get("flipped", False)
        if project is not None:
            project.add(dup)
            project.current = dup  # Resolve makes the copy current (spike 18)
        return dup


def _copy_item(it):
    new = copy.copy(it)
    new.graph = Graph([tuple(n) for n in it.graph.nodes]) if it.graph else None
    new.props = dict(it.props)  # a duplicate's transform is its own, as in Resolve
    return new


class Folder(Fake):
    def __init__(self, name, clips=(), subs=()):
        self.name, self.clips, self.subs = name, list(clips), list(subs)

    def GetName(self): return self.name
    def GetClipList(self): return list(self.clips)
    def GetSubFolderList(self): return list(self.subs)


MARKER_COLORS = ("Blue", "Cyan", "Green", "Yellow", "Red", "Pink", "Purple", "Fuchsia",
                 "Rose", "Lavender", "Sky", "Mint", "Lemon", "Sand", "Cocoa", "Cream")
# path -> clip properties ImportMedia gives the clip it makes for that file.
IMPORT_PROPS = {}


class Pool(Fake):
    def __init__(self, root, project=None):
        self.root, self.project, self.current = root, project, root
        self.autosync_separate = True  # the synced audio is an item of its own on a timeline
        self.autosync_offset = 0  # timeline frames: where the synced audio's start lands

    def GetRootFolder(self): return self.root
    def GetCurrentFolder(self): return self.current

    def SetCurrentFolder(self, folder):
        _log(self, "SetCurrentFolder", getattr(folder, "name", folder))
        self.current = folder
        return True

    def AddSubFolder(self, parent, name):
        _log(self, "AddSubFolder", getattr(parent, "name", parent), name)
        folder = Folder(name)
        parent.subs.append(folder)
        return folder

    def ImportMedia(self, paths):
        _log(self, "ImportMedia", list(paths))
        clips = [Clip(p.rsplit("/", 1)[-1], f"import-{len(CALLS)}-{n}",
                      {"File Path": p, **IMPORT_PROPS.get(p, {})})
                 for n, p in enumerate(paths)]
        self.current.clips.extend(clips)
        return clips

    def CreateEmptyTimeline(self, name):
        _log(self, "CreateEmptyTimeline", name)
        fps = float(self.project.settings.get("timelineFrameRate", 25)) if self.project else 25.0
        start = int(round(3600 * fps))
        tl = Timeline(name, f"new-{len(CALLS)}", fps, start, start,
                      tracks={"video": [[]], "audio": [[]]})
        tl.tc_start = True
        if self.project is not None:
            self.project.add(tl)
        return tl

    def AppendToTimeline(self, items):
        # Resolve 21.0.4.5 (spike 18): True for an imported .srt, and nothing placed.
        # A clipInfo lands on the current timeline, on its trackIndex only when that
        # track exists; the call is truthy whether or not anything was placed.
        _log(self, "AppendToTimeline", items)
        tl = self.project.current if self.project is not None else None
        placed = []
        for info in items or []:
            if not isinstance(info, dict) or tl is None:
                continue
            placed += _place(tl, info)
        return placed or True

    def AutoSyncAudio(self, items, settings):
        _log(self, "AutoSyncAudio", [getattr(i, "name", i) for i in items], dict(settings))
        video = [c for c in items if c.has("video")]
        audio = [c for c in items if not c.has("video")]
        if not video or not audio:
            return False
        video[0].linked = (audio[0], self.autosync_offset)
        video[0].props["Synced Audio"] = audio[0].name
        return True


def _place(tl, info):
    clip = info["mediaPoolItem"]
    kinds = [k for k, t in (("video", 1), ("audio", 2))
             if info.get("mediaType") in (None, t) and clip.has(k)]
    track = int(info.get("trackIndex", 1))
    s0, s1 = info.get("startFrame", 0), info.get("endFrame")
    clip_fps = float(str(clip.props.get("FPS") or tl.fps).split()[0])
    length = int(round((s1 - s0) * tl.fps / clip_fps))
    rec = int(info.get("recordFrame", tl.end))
    placed = []
    for kind in kinds:
        tracks = tl.tracks.setdefault(kind, [])
        if track > len(tracks):
            continue
        it = Item(clip.name, rec, rec + length, clip, nodes=None if kind == "audio" else
                  (("", "", None),), source=(s0, s1))
        tracks[track - 1].append(it)
        placed.append(it)
        tl.end = max(tl.end, it.end)
    pool = tl.project.pool if getattr(tl, "project", None) is not None else None
    if clip.linked and "audio" in kinds and pool is not None and pool.autosync_separate:
        audio, offset = clip.linked
        a_tracks = tl.tracks.get("audio", [])
        if track + 1 <= len(a_tracks):
            a_fps = float(str(audio.props.get("FPS") or tl.fps).split()[0])
            skip = max(0, -offset)
            a_len = int(audio.props.get("Frames", length)) * tl.fps / a_fps - skip
            start = rec + max(0, offset)
            end = min(rec + length, start + int(round(a_len)))
            it = Item(audio.name, start, end, audio, nodes=None,
                      source=(int(round(skip * a_fps / tl.fps)), None))
            a_tracks[track].append(it)
            placed.append(it)
    return placed


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
        self.pool = Pool(root or Folder("Master"), self)
        self.jobs = [dict(j) for j in jobs]
        self.rendering = rendering
        self.refuse = set()  # SetRenderSettings keys this fake refuses
        self.render_mode = 1  # 0 Individual clips, 1 Single clip
        self.stuck_mode = False  # SetCurrentRenderMode says yes and changes nothing
        self.flip_resets = {}  # settings a timeline's first useCustomSettings write changes
        self.flip_each_time = False  # a repeat write changes them again
        self.dup_copies_settings = False  # DuplicateTimeline keeps a custom timeline's settings
        self.settings = {"colorScienceMode": "davinciYRGBColorManagedv2",
                         "timelineResolutionWidth": "3840", "timelineResolutionHeight": "2160",
                         "timelineFrameRate": "25"}

    def GetName(self): return self.name
    def GetUniqueId(self): return self.uid
    def GetTimelineCount(self): return len(self.timelines)

    def GetTimelineByIndex(self, i):
        return self.timelines[i - 1] if 1 <= i <= len(self.timelines) else None

    def GetCurrentTimeline(self): return self.current
    def GetMediaPool(self): return self.pool
    def GetSetting(self, key=None): return self.settings.get(key)
    def GetSettings(self): return dict(self.settings)
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
    # AutoSyncAudio's keys and values, named as in the scripting README's Audio Sync
    # Settings. The two channel values are the README's; the others are stand-ins that
    # no live Resolve has confirmed yet.
    AUDIO_SYNC_MODE = 10.0
    AUDIO_SYNC_CHANNEL_NUMBER = 11.0
    AUDIO_SYNC_RETAIN_EMBEDDED_AUDIO = 12.0
    AUDIO_SYNC_RETAIN_VIDEO_METADATA = 13.0
    AUDIO_SYNC_WAVEFORM = 20.0
    AUDIO_SYNC_TIMECODE = 21.0
    AUDIO_SYNC_CHANNEL_AUTOMATIC = -1
    AUDIO_SYNC_CHANNEL_MIX = -2

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


class SettingsReads:
    """Context manager: records each GetSettings() and each GetSetting() with
    no key on the fake timelines and projects, the two forms the settings guard
    reads with. A keyed GetSetting is the build's own read-back and is not
    counted. `calls` holds 'Timeline.GetSettings' style names."""

    def __init__(self):
        self.calls = []
        self._patches = []

    def __enter__(self):
        for cls in (Timeline, Project):
            for name in ("GetSettings", "GetSetting"):
                def spy(obj, *args, _real=getattr(cls, name), _cls=cls, _name=name, **kw):
                    key = args[0] if args else kw.get("key")
                    if _name == "GetSettings" or key is None:
                        self.calls.append(f"{_cls.__name__}.{_name}")
                    return _real(obj, *args, **kw)
                patch = mock.patch.object(cls, name, spy)
                patch.start()
                self._patches.append(patch)
        return self

    def __exit__(self, *exc):
        while self._patches:
            self._patches.pop().stop()
