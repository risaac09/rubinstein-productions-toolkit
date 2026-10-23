"""
rpresolve.audiostate: what Resolve's scripting API will say about a
timeline's audio, and what it will not. Read only; stdlib only.

    state = read(project, timeline)
    rows = compare(state, dest)        # delivercheck rows against a destination

The report holds the audio tracks (type, name, switched on, item count), the
audio items with their Audio* properties (volume, pan, Voice Isolation,
Dialogue Leveler, as the item reports them) and each source's audio mapping,
the Deliver page's format, codec and render mode, the audio fields of the
queued render jobs, and the project's loudness-meter settings. `unreadable`
names what the API cannot read, so a report never reads as complete.
"""

import json

# Where the API stops (atlas, Resolve Studio 21.1.1.10, 2026-10-23).
UNREADABLE = (
    "mixer faders, pan, sends, buses, VCA and groups",
    "clip and track EQ, Dynamics, Fairlight effects and plug-in parameters",
    "AI Music Remixer stems",
    "the Deliver page's Audio tab (sample rate, bit depth, normalisation) and render range: "
    "SetRenderSettings writes them and nothing reads them; the queued jobs below report some",
    "loudness: no integrated LUFS or true peak (measure the rendered file)",
)
JOB_KEYS = ("JobId", "TimelineName", "AudioCodec", "AudioSampleRate", "AudioBitDepth")
LOUDNESS_KEYS = ("limitAudioMeterLUFS", "limitAudioMeterLoudnessScale", "limitAudioMeterHighLevel",
                 "limitAudioMeterLowLevel", "limitAudioMeterDisplayMode")


def _call(obj, name, *args):
    fn = getattr(obj, name, None) if obj is not None else None
    if not callable(fn):
        return None
    try:
        return fn(*args)
    except Exception:
        return None


def _mapping(item):
    clip = _call(item, "GetMediaPoolItem")
    got = _call(clip, "GetAudioMapping")
    if isinstance(got, str):
        try:
            got = json.loads(got)
        except ValueError:
            return {"raw": got}
    return got if isinstance(got, dict) else None


def read(project, timeline):
    """The audio state of `timeline`. Reads only; a call that fails leaves its
    field None or empty, never an error."""
    tracks, items, note = [], [], []
    for i in range(1, int(_call(timeline, "GetTrackCount", "audio") or 0) + 1):
        listed = _call(timeline, "GetItemListInTrack", "audio", i) or []
        tracks.append({"index": i, "type": _call(timeline, "GetTrackSubType", "audio", i),
                       "name": _call(timeline, "GetTrackName", "audio", i),
                       "enabled": _call(timeline, "GetIsTrackEnabled", "audio", i),
                       "items": len(listed),
                       "voice_isolation": _call(timeline, "GetVoiceIsolationState", i)})
        for it in listed:
            props = _call(it, "GetProperty")
            items.append({"track": i, "name": _call(it, "GetName"),
                          "start": _call(it, "GetStart"), "end": _call(it, "GetEnd"),
                          "properties": {k: v for k, v in (props or {}).items()
                                         if str(k).startswith("Audio")},
                          "voice_isolation": _call(it, "GetVoiceIsolationState"),
                          "audio_mapping": _mapping(it)})
    if not tracks:
        note.append("the timeline has no audio tracks")
    jobs = [{k: j.get(k) for k in JOB_KEYS if k in j}
            for j in _call(project, "GetRenderJobList") or []
            if j.get("TimelineName") == _call(timeline, "GetName")]
    rs = _call(project, "GetRenderSettings")
    return {"timeline": _call(timeline, "GetName"),
            "tracks": tracks, "items": items,
            "render": {"format_codec": _call(project, "GetCurrentRenderFormatAndCodec"),
                       "mode": _call(project, "GetCurrentRenderMode"),
                       "settings": rs if isinstance(rs, dict) else None, "jobs": jobs},
            "loudness_settings": {k: v for k, v in
                                  ((k, _call(project, "GetSetting", k)) for k in LOUDNESS_KEYS)
                                  if v not in (None, "")},
            "unreadable": list(UNREADABLE), "notes": note}


def compare(state, dest):
    """Rows {check, status, found, expected} for the audio fields a queued job
    reports (codec, sample rate, bit depth) against dest['audio']. SKIP when
    no job or no field is reported (the Deliver page itself cannot be read),
    and for a codec that reads differently (spelling is Resolve's own)."""
    from .delivercheck import FAIL, PASS, SKIP
    want = dest["audio"]
    expect = {"AudioCodec": want.get("resolve_codec") or want["codec"],
              "AudioSampleRate": want["sample_rate"], "AudioBitDepth": want.get("bit_depth")}
    jobs = state["render"]["jobs"]
    rows = []
    for key, exp in expect.items():
        if exp is None:
            continue
        seen = [(j["JobId"], j[key]) for j in jobs if j.get(key) not in (None, "")]
        name = "job_" + {"AudioCodec": "audio_codec", "AudioSampleRate": "audio_sample_rate",
                         "AudioBitDepth": "audio_bit_depth"}[key]
        if not seen:
            rows.append({"check": name, "status": SKIP, "found": "not reported",
                         "expected": exp, "note": "no queued job for this timeline reports it"})
            continue
        bad = [f"{jid}: {v}" for jid, v in seen if not _same(v, exp)]
        found = "; ".join(f"{jid}: {v}" for jid, v in seen)
        if bad and key == "AudioCodec":  # Resolve words codecs its own way: never a FAIL here
            rows.append({"check": name, "status": SKIP, "found": found, "expected": exp,
                         "note": "differs, but the job list words codecs its own way; the "
                                 "rendered file's audio_codec row decides"})
            continue
        rows.append({"check": name, "status": FAIL if bad else PASS, "found": found,
                     "expected": exp,
                     **({"note": "queued job differs from the destination"} if bad else {})})
    return rows


def load(path):
    """An audio-state JSON written by `audio-state -o`. Raises ValueError."""
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
        state["render"]["jobs"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ValueError(f"{path} is not an audio-state report ({type(e).__name__}: {e})")
    return state


def _same(got, want):
    try:
        return float(got) == float(want)
    except (TypeError, ValueError):
        return str(got).casefold() == str(want).casefold()


def format_report(state):
    lines = [f"audio state of '{state['timeline']}'"]
    for t in state["tracks"]:
        lines.append(f"  track A{t['index']}  {t['type'] or '?'}  {t['name'] or ''}  "
                     f"enabled {t['enabled']}  {t['items']} item(s)")
    for it in state["items"]:
        lines.append(f"  item A{it['track']} {it['name']}  " + ", ".join(
            f"{k}={v}" for k, v in sorted(it["properties"].items())))
    r = state["render"]
    lines.append(f"  deliver page: {r['format_codec']}, render mode {r['mode']}")
    for j in r["jobs"]:
        lines.append("  job " + ", ".join(f"{k}={v}" for k, v in j.items()))
    if state["loudness_settings"]:
        lines.append("  meter: " + ", ".join(f"{k}={v}" for k, v in
                                             state["loudness_settings"].items()))
    lines += [f"  note: {n}" for n in state["notes"]]
    lines.append("  the API cannot read: " + "; ".join(state["unreadable"]))
    return "\n".join(lines) + "\n"
