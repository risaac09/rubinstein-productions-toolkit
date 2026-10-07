"""
rpresolve.settingsguard: say what Resolve changed on a new timeline besides
what the build wrote. Stdlib only. Opt-in: off unless RPRESOLVE_SETTINGS_GUARD
is on, 1, yes or true.

A Blackmagic forum thread (board 12, topic 212784) reports that switching a
timeline to custom settings (useCustomSettings 1) also changes its colour
management, its input and output scaling and its monitor format, and that
some of those keys cannot be set back. The reports are from Windows and
Linux (20.3 to 21.1.1). None is from a Mac, and nothing here has reproduced
it. cut and sync switch every [auto] timeline they make to custom settings,
so with the guard enabled a Watch reads the timeline's whole settings before
and after those writes and names every key that reads differently, apart
from the keys the build writes on purpose.

    watch = Watch(timeline, WROTE_CLIP, project)
    watch.mark("new timeline")
    ...the build's writes...
    watch.mark("custom write")
    report = watch.report()    # {state, baseline, compared, changed, ...}

The rules:
    - Off by default. The guard reads only when RPRESOLVE_SETTINGS_GUARD is
      on, 1, yes or true (any case, spaces ignored), set in the environment of
      the process that runs the build (the MCP server's). Unset,
      empty and any other value, off included, leave it off: a Watch then
      makes no reads, report() is None and nothing is warned. The reason is
      that Timeline.GetSettings() and GetSetting() with no key have not been
      confirmed on Resolve 21.1.1.10, and a call that hangs inside Resolve
      cannot be caught by try/except. Enable it first on a scratch project,
      never a client project.
    - Reads only: Timeline.GetSettings() (Resolve 21.1), else the older
      GetSetting() with no key. Nothing here sets, creates or duplicates.
    - Reported, never corrected. A report says which write each key changed
      at, so a repeat write or a copy that re-resets shows up as its own stage.
    - Never raises. A read that fails ends as state "unchecked" with the
      reason in notes, and the build goes on. A guard that could read nothing
      says so; it never says "clean". A read that never returns is not a
      failure this module can see.
    - Compares by text the keys both readings hold, less the keys the build
      writes on purpose. A key only one reading holds is counted, not judged
      (a project reading has about 158 keys and a custom timeline's about
      69, so only the shared ones can be compared). None and '' differ.
"""

import os
from collections.abc import Mapping

ENV = "RPRESOLVE_SETTINGS_GUARD"
ON = ("on", "1", "yes", "true")  # the only values that enable the guard
FLAG = "useCustomSettings"
# The keys each call site writes on purpose; they are left out of the comparison.
WROTE_CLIP = (FLAG, "timelineFrameRate", "timelineResolutionWidth", "timelineResolutionHeight")
WROTE_ASPECT = (FLAG, "timelineResolutionWidth", "timelineResolutionHeight")
WROTE_SYNC = (FLAG, "timelineFrameRate")
# Resolve ties the output size to the timeline's while the output is set to match it.
OUTPUT_SIZE = ("timelineOutputResolutionWidth", "timelineOutputResolutionHeight")
OUTPUT_MATCH = "timelineOutputResMatchTimelineRes"
OWN = "the new timeline before any write"
SHOWN = 4


def enabled():
    """True only when RPRESOLVE_SETTINGS_GUARD is on, 1, yes or true (spaces
    and case ignored). Unset, empty and every other value are off."""
    return os.environ.get(ENV, "").strip().lower() in ON


def snapshot(obj):
    """(settings, via, why): every setting of a timeline or project as a
    dict, from GetSettings() (Resolve 21.1), else GetSetting() with no key
    (the older form). (None, '', why) when neither gives a dict. Never
    raises: a missing method, a call that raises and a non-dict answer all
    end as a reason."""
    why = []
    for name in ("GetSettings", "GetSetting"):
        try:
            fn = getattr(obj, name, None)
        except Exception:
            fn = None
        if not callable(fn):
            continue
        try:
            got = fn()
        except Exception as e:
            why.append(f"{name}() raised {type(e).__name__}")
            continue
        if isinstance(got, Mapping) and got:
            try:
                return {str(k): v for k, v in got.items()}, name, ""
            except Exception as e:  # a Mapping that fails while it is read
                why.append(f"{name}() gave a dict that could not be read ({type(e).__name__})")
                continue
        why.append(f"{name}() gave {'nothing' if got is None else type(got).__name__}")
    return None, "", "; ".join(why) or "the object has no GetSettings or GetSetting"


def _what(e):
    """'Type: message' for an exception, safe when its own text cannot be built."""
    try:
        return f"{type(e).__name__}: {e}"
    except Exception:
        return type(e).__name__


def _text(value):
    return None if value is None else str(value)  # None (no value) is not ''


def _same(a, b):
    if a == b:
        return True
    try:  # '25' and '25.0' are one frame rate
        return a is not None and b is not None and abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return False


def compare(before, after, wrote=()):
    """Where two settings dicts differ. Returns {compared, changed,
    before_only, after_only}. changed is [{key, before, after}] (text; None
    stays None) for the keys both dicts hold, less `wrote`. A key only one
    dict holds is counted (before_only) or listed (after_only), never
    called changed."""
    skip = set(wrote)
    if "timelineResolutionWidth" in skip and _text(after.get(OUTPUT_MATCH, "1")) != "0":
        skip.update(OUTPUT_SIZE)
    shared = sorted((set(before) & set(after)) - skip)
    changed = []
    for key in shared:
        a, b = _text(before[key]), _text(after[key])
        if not _same(a, b):
            changed.append({"key": key, "before": a, "after": b})
    return {"compared": len(shared), "changed": changed,
            "before_only": len(set(before) - set(after) - skip),
            "after_only": sorted(set(after) - set(before) - skip)}


def _unchecked(baseline, notes):
    return {"state": "unchecked", "via": "", "baseline": baseline, "wrote": [], "compared": 0,
            "changed": [], "transient": [], "stages": [], "before_only": 0, "after_only": [],
            "notes": list(notes)}


class Watch:
    """One timeline's settings read at named moments, and what changed
    between them. timeline: what mark() reads unless it is handed another
    object (a copy). wrote: the keys the build writes on purpose. project:
    its settings stand in for the first reading when the timeline's own
    cannot be read. baseline: how a report names the first reading. Nothing
    here writes to Resolve, and nothing raises."""

    def __init__(self, timeline, wrote, project=None, baseline=OWN):
        self.timeline, self.wrote, self.project = timeline, tuple(wrote), project
        self.baseline, self.via = baseline, ""
        self.off = not enabled()  # off (the default): no reads and no report, so no warnings
        self.marks, self.notes, self._done = [], [], None  # marks: [(stage, dict or None)]

    def mark(self, stage, obj=None):
        """Read now and file the reading under `stage`."""
        self._done = None
        try:
            self.marks.append((stage, self._read(stage, obj)))
        except Exception as e:  # a guard never fails a build
            self.notes.append(f"at '{stage}': {_what(e)}")
            self.marks.append((stage, None))

    def _read(self, stage, obj):
        if self.off or not enabled():
            return None
        first = not self.marks
        if not first and self.marks[0][1] is None:
            return None  # no baseline to compare with, so no reason to read on
        snap, via, why = snapshot(self.timeline if obj is None else obj)
        if snap is None and first and self.project is not None:
            snap, via, why2 = snapshot(self.project)
            if snap is not None:
                self.baseline = "the project's settings"
                self.notes.append("the timeline's own settings could not be read before the "
                                  f"build ({why}); the project's stand in for them")
            else:
                why = f"{why}; the project: {why2}"
        if snap is None:
            self.notes.append(f"at '{stage}': {why}")
        elif not self.via:
            self.via = via
        elif via != self.via:  # two methods can word keys differently: never compare across them
            self.notes.append(f"at '{stage}': read with {via}() after {self.via}(), so it is "
                              "not compared")
            return None
        return snap

    def report(self):
        """The report dict, or None while fewer than two readings exist and
        nothing went wrong."""
        if self._done is None:
            try:
                self._done = self._build()
            except Exception as e:  # a guard never fails a build
                self._done = _unchecked(self.baseline, [f"the settings guard failed: {_what(e)}"])
        return self._done

    def tail(self):
        return tail(self.report())

    def _build(self):
        if self.off or (len(self.marks) < 2 and not self.notes):
            return None
        read = [(s, d) for s, d in self.marks if d is not None]
        if not self.marks or self.marks[0][1] is None or len(read) < 2:
            return _unchecked(self.baseline,
                              self.notes or ["Resolve gave no settings to compare"])
        base = read[0][1]
        last_stage, last = read[-1]
        final = compare(base, last, self.wrote)
        at = {}
        for stage, snap in read[1:]:
            for row in compare(base, snap, self.wrote)["changed"]:
                at.setdefault(row["key"], stage)
        stages = [{"stage": f"{a} -> {b}", "changed": len(compare(x, y, self.wrote)["changed"])}
                  for (a, x), (b, y) in zip(read, read[1:])]
        changed = [dict(r, at=at.get(r["key"], last_stage)) for r in final["changed"]]
        seen = {r["key"] for r in changed}
        # A project baseline lacks the keys only a custom timeline holds, so the baseline cannot
        # judge them; a change between two custom readings stands in for it.
        for (_, x), (b, y) in zip(read[1:], read[2:]):
            for row in compare(x, y, self.wrote)["changed"]:
                if row["key"] not in base and row["key"] not in seen:
                    changed.append(dict(row, at=b))
                    seen.add(row["key"])
        kept = {r["key"] for r in changed}
        notes = list(self.notes)
        partial = self.marks[-1][1] is None  # the last reading failed: not a full comparison
        if partial:
            notes.append(f"nothing could be read at '{self.marks[-1][0]}'; compared up to "
                         f"'{last_stage}'")
        state = "drift" if changed else ("unchecked" if partial else "clean")
        if not final["compared"]:
            state = "unchecked"
            notes.append("no setting was read both before and after the build")
        return {"state": state, "via": self.via, "baseline": self.baseline,
                "wrote": list(self.wrote), "compared": final["compared"], "changed": changed,
                "transient": sorted(k for k in at if k not in kept), "stages": stages,
                "before_only": final["before_only"], "after_only": final["after_only"],
                "notes": notes}


def _show(value, width=40):
    if value is None:
        return "None"
    text = str(value)
    return "'" + (text if len(text) <= width else text[:width - 3] + "...") + "'"


def _pair(row):
    return f"{row['key']} {_show(row['before'])} -> {_show(row['after'])}"


def warnings(report):
    """0 or 1 line for a result's warnings list: drift, or 'not checked'."""
    if not report:
        return []
    if report.get("state") == "drift":
        changed = report["changed"]
        more = len(changed) - SHOWN
        text = (f"settings drift: {len(changed)} of {report['compared']} setting(s) read "
                f"differently from {report['baseline']} after the custom-settings write ("
                + ", ".join(_pair(r) for r in changed[:SHOWN])
                + (f" and {more} more" if more > 0 else "")
                + "); reported, not corrected: each stays as Resolve left it. Check this "
                "timeline's settings against the project's before grading or rendering from it")
        return [text]
    if report.get("state") == "unchecked":
        why = "; ".join(report.get("notes") or []) or "no reason given"
        return [f"settings drift not checked: {why}; this timeline's colour management and "
                "scaling may differ from the project's"]
    return []


def lines(report):
    """One line per changed key. Nothing outside the tests calls it since the
    cut and sync commands of resolve_workflow.py were retired; the MCP summaries
    use clause()."""
    if not report or report.get("state") != "drift":
        return []
    return [f"{_pair(r)} (at {r['at']})" for r in report["changed"]]


def tail(report):
    """Text to append to a failure message ('' when nothing drifted)."""
    if not report or report.get("state") != "drift":
        return ""
    return f"; settings drift on it: {len(report['changed'])} setting(s)"


def explain(report, key, reason=None):
    """' (settings drift: ...)' when `key` is among the changed settings and,
    when a refusal's `reason` is given, that reason names the key."""
    if reason is not None and key not in reason:
        return ""
    for r in (report or {}).get("changed") or []:
        if r["key"] == key:
            return (f" (settings drift: Resolve changed it from {_show(r['before'])} to "
                    f"{_show(r['after'])} when the custom settings were switched on)")
    return ""


def digest(pairs):
    """Compact rows for a result's top level, from [(timeline name, report)]:
    one for each timeline that drifted or could not be checked."""
    rows = []
    for name, rep in pairs:
        state = (rep or {}).get("state")
        if state == "drift":
            rows.append({"timeline": name, "state": state, "count": len(rep["changed"]),
                         "keys": [_pair(r) for r in rep["changed"][:SHOWN]], "note": ""})
        elif state == "unchecked":
            rows.append({"timeline": name, "state": state, "count": 0, "keys": [],
                         "note": "; ".join(rep.get("notes") or [])})
    return rows


def clause(rows):
    """Summary text for digest rows (leading '; '), '' for none."""
    rows = rows or []
    drift = [r for r in rows if r["state"] == "drift"]
    blind = [r["timeline"] for r in rows if r["state"] == "unchecked"]
    text = ""
    if drift:
        groups = {}  # timelines that drifted the same way are named together
        for r in drift:
            groups.setdefault((r["count"], tuple(r["keys"])), []).append(r["timeline"])
        text += ("; SETTINGS DRIFT (read differently after the custom-settings write; reported, "
                 "not corrected): "
                 + "; ".join(", ".join(names) + ": " + str(count) + " setting(s) ("
                             + ", ".join(keys) + (", ..." if count > len(keys) else "") + ")"
                             for (count, keys), names in groups.items()))
    if blind:
        text += "; SETTINGS NOT CHECKED on " + ", ".join(blind)
    return text
