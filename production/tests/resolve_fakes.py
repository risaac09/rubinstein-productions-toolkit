"""
Shared fakes of the Resolve scripting objects for the MCP tool tests.

Every fake answers the getters the tools use. Any other method is still
callable, returns None and is recorded in CALLS, so a test can assert that
a read tool never reached for a setter (SetCurrentTimeline, OpenPage,
SetCurrentFolder, ...) that the fake does not implement.
"""

CALLS = []
MUTATING_PREFIXES = ("Set", "Open", "Load", "Create", "Delete", "Add", "Import", "Append",
                     "Start", "Stop", "Save", "Close", "Duplicate", "Apply", "Insert",
                     "Move", "Relink", "Export", "Refresh", "Clear", "Replace")


def mutating_calls():
    return [c for c in CALLS if c[1].startswith(MUTATING_PREFIXES)]


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
    def __init__(self, name, uid, fps=25.0, start=86400, end=86400, tracks=None):
        self.name, self.uid, self.fps, self.start, self.end = name, uid, fps, start, end
        self.tracks = tracks or {"video": [[]], "audio": [[]]}  # kind -> [track items]

    def GetName(self): return self.name
    def GetUniqueId(self): return self.uid
    def GetStartFrame(self): return self.start
    def GetEndFrame(self): return self.end
    def GetTrackCount(self, kind): return len(self.tracks.get(kind, []))

    def GetSetting(self, key=None):
        return self.fps if key == "timelineFrameRate" else None

    def GetItemListInTrack(self, kind, index):
        tracks = self.tracks.get(kind, [])
        return list(tracks[index - 1]) if 1 <= index <= len(tracks) else None


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


class Project(Fake):
    def __init__(self, name="RP Automation Sandbox", uid="sandbox-id", timelines=(),
                 current=None, root=None, jobs=(), rendering=False):
        self.name, self.uid = name, uid
        self.timelines = list(timelines)
        self.current = current
        self.pool = Pool(root or Folder("Master"))
        self.jobs = [dict(j) for j in jobs]
        self.rendering = rendering

    def GetName(self): return self.name
    def GetUniqueId(self): return self.uid
    def GetTimelineCount(self): return len(self.timelines)

    def GetTimelineByIndex(self, i):
        return self.timelines[i - 1] if 1 <= i <= len(self.timelines) else None

    def GetCurrentTimeline(self): return self.current
    def GetMediaPool(self): return self.pool
    def GetSetting(self, key=None): return {"colorScienceMode": "davinciYRGBColorManagedv2"}.get(key)
    def GetRenderJobList(self): return [dict(j) for j in self.jobs]

    def GetRenderJobStatus(self, job_id):
        job = next((j for j in self.jobs if j["JobId"] == job_id), None)
        return {"JobStatus": job.get("_status", "Ready"), "CompletionPercentage": 0} if job else {}

    def IsRenderingInProgress(self): return self.rendering

    def GetCurrentRenderFormatAndCodec(self):
        return {"format": "mov", "codec": "ProRes422HQ"}


class ProjectManager(Fake):
    def __init__(self, project):
        self.project = project

    def GetCurrentProject(self): return self.project


class Resolve(Fake):
    def __init__(self, project, page="edit"):
        self.pm, self.page = ProjectManager(project), page

    def GetProjectManager(self): return self.pm
    def GetProductName(self): return "DaVinci Resolve Studio"
    def GetVersionString(self): return "21.0.4.5"
    def GetCurrentPage(self): return self.page


class Session:
    """Stands in for ResolveSession."""

    def __init__(self, resolve):
        self.resolve = resolve

    def get(self):
        return self.resolve

    def project(self):
        pm = self.resolve.GetProjectManager()
        return self.resolve, pm, pm.GetCurrentProject()
