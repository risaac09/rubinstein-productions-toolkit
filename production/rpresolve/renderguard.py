"""
rpresolve.renderguard: put the Deliver page back the way it was found.

SetRenderSettings changes what the Deliver page shows, and a second pass over
Resolve 21.1 found two keys it did not put back: AudioSampleRate left the
Audio tab on 48000 instead of "Same as project", and SelectAllFrames flipped
the render range to Entire Timeline (the call to undo it returned False).
Resolve has no GetRenderSettings, so a script cannot read most of what it
writes. The guard takes a snapshot of what can be read, puts it back on exit
(also when the block raised), reads it again and says what did not come back:

    with RenderGuard(project, timeline) as guard:
        guard.touch(["AudioSampleRate", "SelectAllFrames"])
        project.SetRenderSettings({...})
    guard.warnings()

What it reads and restores:
    - the format and codec (GetCurrentRenderFormatAndCodec)
    - the render mode (GetCurrentRenderMode: 0 Individual clips, 1 Single clip)
    - the timeline's mark In and Out (GetMarkInOut), which is the range the
      In/Out Range option renders
    - the touched render settings, only when this Resolve has a
      GetRenderSettings that answers with a dict (21.1.1.10 has none)

Every touched key it could not read is listed as unrestorable: it says what
to reset by hand. Never starts a render. Never raises out of exit.
"""

# Keys known to leave the Deliver page changed with no way to read them back
# (atlas, 2026-10-23, Resolve Studio 21.1.1.10): what to check by hand.
HAND_RESET = {
    "AudioSampleRate": "the Audio tab's Sample Rate (it may have read 'Same as project' before)",
    "SelectAllFrames": "the render range (In/Out Range or Entire Timeline)",
}


def _call(obj, name, *args):
    fn = getattr(obj, name, None) if obj is not None else None
    if not callable(fn):
        return None
    try:
        return fn(*args)
    except Exception:
        return None


def _marks(timeline):
    """The timeline's mark In/Out as {kind: (in, out)}; {} for none or unreadable."""
    got = _call(timeline, "GetMarkInOut")
    out = {}
    if isinstance(got, dict):
        for kind in ("video", "audio"):
            row = got.get(kind)
            if isinstance(row, dict) and row.get("in") is not None and row.get("out") is not None:
                out[kind] = (row["in"], row["out"])
    return out


def _settings(project):
    got = _call(project, "GetRenderSettings")
    return dict(got) if isinstance(got, dict) else None


class RenderGuard:
    def __init__(self, project, timeline=None):
        self.project, self.timeline = project, timeline
        self.touched = []
        self.before = {}
        self.restored, self.mismatched, self.problems = [], [], []

    def touch(self, keys):
        """Name the SetRenderSettings keys the block is about to write."""
        self.touched += [k for k in keys if k not in self.touched]

    def _read(self):
        return {"format/codec": _call(self.project, "GetCurrentRenderFormatAndCodec") or {},
                "render mode": _call(self.project, "GetCurrentRenderMode"),
                "mark in/out": _marks(self.timeline) if self.timeline is not None else None,
                "settings": _settings(self.project)}

    def __enter__(self):
        self.before = self._read()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self._restore()
        except Exception as e:  # a restore never hides the block's own error
            self.problems.append(f"the render guard failed: {type(e).__name__}: {e}")
        return False

    def _put(self, what, wanted, now, set_fn):
        """Set `what` back to `wanted` when `now` differs, then read it again."""
        if now() == wanted:
            return
        try:
            set_fn()
        except Exception as e:
            self.problems.append(f"{what}: {type(e).__name__}: {e}")
        got = now()
        if got == wanted:
            self.restored.append(what)
        else:
            self.mismatched.append({"what": what, "wanted": wanted, "got": got})

    def _restore(self):
        b, p = self.before, self.project
        fmt = b["format/codec"]
        if fmt.get("format") and fmt["format"] != "unknown":
            self._put("format/codec", fmt, lambda: _call(p, "GetCurrentRenderFormatAndCodec") or {},
                      lambda: _call(p, "SetCurrentRenderFormatAndCodec", fmt["format"],
                                    fmt.get("codec")))
        else:
            self.problems.append("the Deliver page had no format set before, so a changed "
                                 "format stays selected there")
        if b["render mode"] in (0, 1):
            self._put("render mode", b["render mode"], lambda: _call(p, "GetCurrentRenderMode"),
                      lambda: _call(p, "SetCurrentRenderMode", b["render mode"]))
        else:
            self.problems.append(f"the render mode read as {b['render mode']!r} before, so it "
                                 "stays as it is")
        if b["mark in/out"] is not None:
            self._restore_marks(b["mark in/out"])
        old = b["settings"]
        if old is not None:
            for key in self.touched:
                if key in old:
                    self._put(key, old[key], lambda k=key: (_settings(p) or {}).get(k),
                              lambda k=key: p.SetRenderSettings({k: old[k]}))

    def _restore_marks(self, old):
        tl = self.timeline
        now = lambda: _marks(tl)
        if now() == old:
            return

        def put():
            if not old:
                _call(tl, "ClearMarkInOut", "all")
            for kind, (a, z) in old.items():
                _call(tl, "SetMarkInOut", a, z, kind)
        self._put("mark in/out", old, now, put)

    @property
    def unrestorable(self):
        """Touched keys whose earlier value could not be read, so were not put back."""
        old = self.before.get("settings")
        return [k for k in self.touched if old is None or k not in old]

    def report(self):
        return {"restored": list(self.restored), "mismatched": list(self.mismatched),
                "unrestorable": self.unrestorable, "problems": list(self.problems)}

    def warnings(self):
        out = list(self.problems)
        for m in self.mismatched:
            out.append(f"could not put the Deliver page's {m['what']} back to {m['wanted']!r}; "
                       f"Resolve reads {m['got']!r}")
        return out

    def hand_reset(self):
        """One line naming the touched settings Resolve cannot read, so nothing
        put them back ('' for none). Not a warning: it holds on every render."""
        hand = [k for k in self.unrestorable if k in HAND_RESET]
        return ("Resolve cannot read these Deliver settings, so they were not put back: "
                + "; ".join(f"{k} ({HAND_RESET[k]})" for k in hand)) if hand else ""
