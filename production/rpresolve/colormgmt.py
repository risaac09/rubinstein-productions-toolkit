"""
rpresolve.colormgmt: the Project Settings a colour-managed project needs,
as data, and the check that a project is fresh enough to take them.

The write tool set_color_management exists because `ingest` refuses a
project that is not DaVinci YRGB Color Managed, and no tool set that. It
is deliberately narrow, to keep the server's rule that a person edits
Resolve live and nothing that already exists is modified:

    - It writes only the keys of a named preset, in order, each read back.
    - It writes only on a FRESH project: no timelines and no clip anywhere
      in the media pool. Changing colour science under existing grades,
      tags or timelines changes how they look; on an empty project it
      changes nothing anybody has made.
    - It never creates, loads or saves a project (ProjectManager's
      CreateProject and LoadProject stay forbidden by the name scan in
      tests/test_mcp_writes.py: a person makes the project, in Resolve).

A preset is a tuple of (key, value) pairs. Add one only after setting it
through the API has been checked on a live Resolve with
tests/live_mcp_sandbox.py: Resolve's scripting README documents the value
strings for colorScienceMode, but not the colour-space names, and a key
Resolve resets when another changes (the Output DRT, after the output
colour space) shows up as drift in the final read-back below.

"managed" is the one key `ingest` needs (any colorScienceMode starting
davinciYRGBColorManaged passes its gate; v2 is what this repo's config,
fakes and live project use). Everything else stays at Resolve's default.
"""

from . import syncbuild as sb

PRESETS = {
    "managed": (("colorScienceMode", "davinciYRGBColorManagedv2"),),
}
DEFAULT_PRESET = "managed"


def plan_rows(project, keys):
    """One row per key: what the project holds now, what the preset wants,
    and whether the tool would set it or keep it."""
    rows = []
    for key, wanted in keys:
        now = str(project.GetSetting(key) or "")
        rows.append({"key": key, "now": now, "wanted": wanted,
                     "action": "keep" if now == wanted else "set"})
    return rows


def not_fresh(project, root):
    """Why the project is not fresh, as a list of reasons ([] when it is):
    its timelines and the clips in its media pool, which are what a settings
    change would reach. Empty bins do not count."""
    reasons = []
    timelines = int(project.GetTimelineCount() or 0)
    if timelines:
        reasons.append(f"{timelines} timeline(s)")
    clips = len(sb.walk(root))
    if clips:
        reasons.append(f"{clips} clip(s) in the media pool")
    return reasons


def final_drift(project, keys):
    """After the writes, every key read again: a key another write made
    Resolve reset reads differently from the preset. Returns problem strings."""
    out = []
    for key, wanted in keys:
        got = str(project.GetSetting(key) or "")
        if got != wanted:
            out.append(f"{key}: now '{got}', the preset wants '{wanted}'")
    return out
