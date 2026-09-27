"""
rpresolve.api: connection, guard rails, and read-back helpers for the
DaVinci Resolve scripting API. Stdlib only; importable without Resolve
installed (DaVinciResolveScript is imported lazily inside connect()).

Safety rules for every script built on this module:
    1. Act only on the project that is already open. Pin it with
       ProjectPin at start and call check() before each batch of writes;
       Isaac edits live, so a script must stop if he switched projects.
    2. Never call ProjectManager.LoadProject or CreateProject. Opening or
       creating projects is a human decision made in the Resolve UI. The
       one exception is resolve_workflow.py new-project, which Isaac runs
       by hand to create a named project; nothing automated may call it
       or copy its CreateProject call.
    3. UI state writes (page, current timeline, playhead) happen only in
       the sandbox project or with an explicit opt-in flag, and are wrapped
       in UISnapshot so the UI is put back afterwards.
    4. Read back every write. Resolve setters return True for writes that
       did not apply (an open modal dialog swallows them), so the value
       read back is the only evidence a write landed.

Verified against Resolve Studio 21.0.4.5:
    - The Python client can segfault on interpreter shutdown after some
      calls, and piped stdout is lost unless flushed first. End scripts
      with exit_clean(code).
    - Unknown resolve.CONSTANT names return None silently; check them.
"""

import errno
import fcntl
import math
import os
import sys
import time

SCRIPT_MODULE_DIR = (
    "/Library/Application Support/Blackmagic Design/DaVinci Resolve/"
    "Developer/Scripting/Modules"
)

# Folders Resolve scans for LUTs on macOS. Graph.GetLUT may report a path
# relative to one of these rather than the absolute path passed to SetLUT.
# ~/DaVinci Resolve/LUT is where the user LUTs actually live on the M4 Max
# (read from disk 2026-09-26); the ~/Library folder is kept for other setups.
DEFAULT_LUT_ROOTS = (
    "/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT",
    os.path.expanduser("~/DaVinci Resolve/LUT"),
    os.path.expanduser(
        "~/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT"
    ),
)

# Resolve stores item properties at single precision, so a value like
# 123.45 reads back as 123.4499969. Numbers match when within this
# absolute tolerance or within FLOAT32_REL_TOLERANCE of the wanted value
# (float32 has about 7 significant digits; 1e-6 relative covers rounding).
NUMERIC_TOLERANCE = 1e-6
FLOAT32_REL_TOLERANCE = 1e-6


class ResolveAPIError(RuntimeError):
    """Base class for errors raised by this module."""


class ResolveUnavailable(ResolveAPIError):
    """DaVinciResolveScript could not be imported or Resolve is not running."""


class ProjectChanged(ResolveAPIError):
    """The open project is not the one the script started on."""


class WriteNotApplied(ResolveAPIError):
    """A setter ran but reading the value back shows it did not take."""


# ---------------------------------------------------------------------------
# Connection and exit
# ---------------------------------------------------------------------------

def connect():
    """Return the running Resolve object, or raise ResolveUnavailable."""
    try:
        import DaVinciResolveScript as dvr
    except ImportError:
        if SCRIPT_MODULE_DIR not in sys.path:
            sys.path.append(SCRIPT_MODULE_DIR)
        try:
            import DaVinciResolveScript as dvr
        except ImportError:
            raise ResolveUnavailable(
                "Cannot import DaVinciResolveScript. Make sure DaVinci Resolve "
                "is running and the scripting environment variables are set "
                "(see resolve_workflow.py --help)."
            )

    resolve = dvr.scriptapp("Resolve")
    if not resolve:
        raise ResolveUnavailable(
            "Could not connect to DaVinci Resolve. Make sure DaVinci Resolve "
            "Studio is running and external scripting is set to Local."
        )
    return resolve


def exit_clean(code=0):
    """Flush both streams, then leave via os._exit so the Resolve client's
    shutdown segfault cannot eat buffered output or the exit status."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(int(code or 0))


# ---------------------------------------------------------------------------
# Project pinning
# ---------------------------------------------------------------------------

class ProjectPin:
    """Remember which project a script started on so it can refuse to keep
    writing after the open project changes underneath it."""

    def __init__(self, project):
        if not project:
            raise ProjectChanged("No project is open in Resolve.")
        self.unique_id = project.GetUniqueId()
        self.name = project.GetName()
        if not self.unique_id:
            raise ResolveAPIError(
                f"Project '{self.name}' returned no unique id; cannot pin it."
            )

    def check(self, project_manager):
        """Re-read the open project and return it, or raise ProjectChanged
        if it is not the pinned one."""
        current = project_manager.GetCurrentProject()
        if not current:
            raise ProjectChanged(
                f"No project is open; the script started on '{self.name}'. Stopping."
            )
        current_id = current.GetUniqueId()
        if current_id != self.unique_id:
            raise ProjectChanged(
                f"Open project changed from '{self.name}' to "
                f"'{current.GetName()}' while the script was running. Stopping."
            )
        return current

    def require_name(self, expected, project_manager=None):
        """Raise ProjectChanged unless the open project is named `expected`.
        With a project manager the name is re-read live; without one the
        name captured at pin time is used."""
        if project_manager is not None:
            current = self.check(project_manager)
            name = current.GetName()
        else:
            name = self.name
        if name != expected:
            raise ProjectChanged(
                f"Open project is '{name}', expected '{expected}'. Stopping."
            )
        return name


def current_project(resolve):
    """(project_manager, project) for the open project, or raise
    ResolveUnavailable / ProjectChanged. Never exits the interpreter, so a
    long-lived caller (the MCP server) survives."""
    pm = resolve.GetProjectManager()
    if not pm:
        raise ResolveUnavailable("Could not get the Project Manager from Resolve.")
    project = pm.GetCurrentProject()
    if not project:
        raise ProjectChanged("No project is open in Resolve.")
    return pm, project


def media_pool_root(project):
    """(media_pool, root_folder) or raise ResolveAPIError."""
    media_pool = project.GetMediaPool()
    root = media_pool.GetRootFolder() if media_pool else None
    if not root:
        raise ResolveAPIError("Could not get the media pool root folder.")
    return media_pool, root


def pin_for_write(resolve, name, unique_id=None):
    """(pm, project, pin) for a write that names its project. Raises
    ProjectChanged unless the open project is named `name` and, when
    unique_id is given, carries that id: names can repeat across
    project-manager folders, ids cannot."""
    pm, project = current_project(resolve)
    pin = ProjectPin(project)
    pin.require_name(name, pm)
    if unique_id and pin.unique_id != unique_id:
        raise ProjectChanged(f"Open project '{pin.name}' has id {pin.unique_id}, "
                             f"expected {unique_id}. Stopping.")
    return pm, project, pin


class ResolveBusy(ResolveAPIError):
    """Another process holds the Resolve lock."""


class ResolveLock:
    """Cross-process lock around Resolve work. Every Claude Code session
    starts its own MCP server, and the CLI can run beside them; the lock
    keeps two of them from interleaving writes (or UI snapshots) in the one
    open project. An fcntl.flock on RPRESOLVE_LOCK (default
    ~/Library/Caches/rpresolve/resolve.lock), released when the holder
    exits even if it crashes. Waits up to `timeout` seconds, then raises
    ResolveBusy."""

    def __init__(self, timeout=30.0, path=None):
        self.timeout = timeout
        self.path = path or os.environ.get("RPRESOLVE_LOCK") or os.path.expanduser(
            "~/Library/Caches/rpresolve/resolve.lock")
        self.fd = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError as e:
                if e.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                    self._close()
                    raise
                if time.monotonic() >= deadline:
                    self._close()
                    raise ResolveBusy("Another Claude session or the CLI is using Resolve; "
                                      f"waited {self.timeout:.0f}s. Try again when it finishes.")
                time.sleep(0.2)

    def __exit__(self, *exc):
        self._close()
        return False

    def _close(self):
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None


# ---------------------------------------------------------------------------
# UI snapshot / restore
# ---------------------------------------------------------------------------

def _safe_call(obj, method, *args):
    fn = getattr(obj, method, None) if obj is not None else None
    if fn is None:
        return None
    try:
        return fn(*args)
    except Exception:
        return None


def _timeline_key(timeline):
    """(unique id, name) for a timeline; either may be None."""
    if not timeline:
        return (None, None)
    return (_safe_call(timeline, "GetUniqueId"), _safe_call(timeline, "GetName"))


def _find_timeline(project, unique_id, name):
    count = _safe_call(project, "GetTimelineCount") or 0
    by_name = None
    for i in range(1, int(count) + 1):
        tl = _safe_call(project, "GetTimelineByIndex", i)
        if not tl:
            continue
        tl_id, tl_name = _timeline_key(tl)
        if unique_id and tl_id == unique_id:
            return tl
        if by_name is None and name and tl_name == name:
            by_name = tl
    # A name match is only trusted when no id was recorded.
    return None if unique_id else by_name


class UISnapshot:
    """Context manager: record the current page, timeline, and playhead
    timecode on entry and put them back on exit. Restoring is best effort
    and never raises; problems are collected in self.problems and also
    returned by restore()."""

    def __init__(self, resolve, project):
        self.resolve = resolve
        self.project = project
        self.page = None
        self.timeline_id = None
        self.timeline_name = None
        self.timecode = None
        self.problems = []

    def capture(self):
        self.page = _safe_call(self.resolve, "GetCurrentPage")
        timeline = _safe_call(self.project, "GetCurrentTimeline")
        self.timeline_id, self.timeline_name = _timeline_key(timeline)
        self.timecode = _safe_call(timeline, "GetCurrentTimecode") if timeline else None
        return self

    def restore(self):
        problems = []
        try:
            if self.timeline_id or self.timeline_name:
                current = _safe_call(self.project, "GetCurrentTimeline")
                cur_id, cur_name = _timeline_key(current)
                same = (cur_id == self.timeline_id) if self.timeline_id else (cur_name == self.timeline_name)
                if not same:
                    target = _find_timeline(self.project, self.timeline_id, self.timeline_name)
                    if not target:
                        problems.append(f"timeline '{self.timeline_name}' not found; left on '{cur_name}'")
                    elif not _safe_call(self.project, "SetCurrentTimeline", target):
                        problems.append(f"could not switch back to timeline '{self.timeline_name}'")
                    else:
                        current = target
                        same = True

                # The playhead was captured on the original timeline; never
                # move the playhead of whatever timeline is open instead.
                if self.timecode and current and same:
                    if _safe_call(current, "GetCurrentTimecode") != self.timecode:
                        if not _safe_call(current, "SetCurrentTimecode", self.timecode):
                            problems.append(f"could not restore playhead to {self.timecode}")

            if self.page and _safe_call(self.resolve, "GetCurrentPage") != self.page:
                if not _safe_call(self.resolve, "OpenPage", self.page):
                    problems.append(f"could not reopen page '{self.page}'")
        except Exception as e:  # never raise out of a restore
            problems.append(f"restore error: {type(e).__name__}: {e}")
        self.problems = problems
        return problems

    def __enter__(self):
        return self.capture()

    def __exit__(self, exc_type, exc, tb):
        try:
            self.restore()
        except Exception as e:
            self.problems.append(f"restore error: {type(e).__name__}: {e}")
        return False


# ---------------------------------------------------------------------------
# Read-back helpers
# ---------------------------------------------------------------------------

def set_setting_checked(obj, key, value):
    """SetSetting(key, str(value)) on a project or timeline, then require
    GetSetting(key) to equal str(value). Returns the value read back."""
    wanted = str(value)
    obj.SetSetting(key, wanted)
    got = obj.GetSetting(key)
    if str(got) != wanted:
        raise WriteNotApplied(
            f"Setting '{key}': wrote '{wanted}', read back '{got}'. "
            "Check whether a modal dialog is open in Resolve; it blocks "
            "setting writes while the setter still reports success."
        )
    return got


def _as_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def set_property_checked(item, key, value):
    """SetProperty(key, value) on a timeline item, then GetProperty(key)
    must match: numbers within float32 rounding (NUMERIC_TOLERANCE absolute
    or FLOAT32_REL_TOLERANCE relative), anything else exactly.
    Returns the value read back."""
    item.SetProperty(key, value)
    got = item.GetProperty(key)
    want_num = _as_number(value)
    got_num = _as_number(got)
    if want_num is not None and got_num is not None:
        ok = math.isclose(want_num, got_num, rel_tol=FLOAT32_REL_TOLERANCE, abs_tol=NUMERIC_TOLERANCE)
    else:
        ok = got == value
    if not ok:
        raise WriteNotApplied(
            f"Property '{key}': wrote {value!r}, read back {got!r}. "
            "Check whether a modal dialog is open in Resolve."
        )
    return got


def set_clip_property_checked(clip, key, value):
    """SetClipProperty(key, value) on a media-pool item, then require
    GetClipProperty(key) to equal str(value) exactly. Verified on Resolve
    21.0.4.5 for 'Input Color Space' (sandbox spike 4). Returns the value
    read back."""
    wanted = str(value)
    clip.SetClipProperty(key, wanted)
    got = clip.GetClipProperty(key)
    if str(got) != wanted:
        raise WriteNotApplied(
            f"Clip property '{key}': wrote '{wanted}', read back '{got}'. "
            "Check the value is one Resolve offers for this clip, and whether "
            "a modal dialog is open in Resolve."
        )
    return got


# ---------------------------------------------------------------------------
# Node graph helpers (Graph from TimelineItem.GetNodeGraph(); 1-based)
# ---------------------------------------------------------------------------

def graph_labels(graph):
    """Labels of nodes 1..GetNumNodes(); unlabeled nodes come back as ''."""
    count = graph.GetNumNodes() or 0
    return [graph.GetNodeLabel(i) or "" for i in range(1, int(count) + 1)]


def assert_graph_matches(graph, manifest):
    """Compare a node graph against {"num_nodes": int, "labels": [str]}.
    Either key may be omitted. Returns a list of mismatch strings; empty
    means the graph matches."""
    mismatches = []
    count = int(graph.GetNumNodes() or 0)
    expected_count = manifest.get("num_nodes")
    if expected_count is not None and count != int(expected_count):
        mismatches.append(f"num_nodes: expected {expected_count}, got {count}")

    expected_labels = manifest.get("labels")
    if expected_labels is not None:
        actual = graph_labels(graph)
        for i in range(max(len(expected_labels), len(actual))):
            want = expected_labels[i] if i < len(expected_labels) else None
            got = actual[i] if i < len(actual) else None
            if want != got:
                mismatches.append(f"node {i + 1} label: expected {want!r}, got {got!r}")
    return mismatches
