"""
rpresolve.render: queue a render job from a resolve-config.json preset and
read it back. Never starts rendering: that stays a person's decision.

Resolve can set render settings but not read them (there is no
GetRenderSettings), so queueing overwrites the project's current Deliver
page settings; only the format and codec can be read and put back. The
job itself is read back from GetRenderJobList.
"""

from pathlib import Path

# The Deliver fields SetRenderSettings writes here, for callers to report.
DELIVER_FIELDS = ("TargetDir", "CustomName", "FormatWidth", "FormatHeight",
                  "ExportVideo", "ExportAudio")


def pick_codec(preferred_name, fallback_names, codecs_dict):
    """Given GetRenderCodecs()'s {description: name} dict, find the best
    match for a desired codec NAME (the dict's values, not its keys; the
    value side is what SetCurrentRenderFormatAndCodec expects).

    Returns (description, name, matched) where matched is False if we fell
    back to "first available" rather than finding what was asked for.
    """
    if not codecs_dict:
        return (None, None, False)

    by_name = {name: desc for desc, name in codecs_dict.items()}

    if preferred_name in by_name:
        return (by_name[preferred_name], preferred_name, True)

    for fallback in fallback_names or []:
        if fallback in by_name:
            return (by_name[fallback], fallback, True)

    first_desc, first_name = next(iter(codecs_dict.items()))
    return (first_desc, first_name, False)


def queue_render_job(project, preset_key, output_dir, presets, custom_name=None):
    """Queue one render job for the current timeline from a preset. Returns
    {job_id, name, error, warnings, codec: {requested, used, description,
    matched}, settings}. error names why nothing was queued (job_id None);
    warnings are non-fatal. Prints nothing."""
    out = {"job_id": None, "name": None, "error": None, "warnings": [], "codec": None,
           "settings": None}
    preset = presets.get(preset_key)
    if not preset:
        out["error"] = (f"Unknown preset '{preset_key}'. Configured presets: "
                        f"{', '.join(presets.keys())}")
        return out
    out["name"] = preset["name"]
    timeline = project.GetCurrentTimeline()
    if not timeline:
        out["error"] = "No active timeline."
        return out
    filename = custom_name or timeline.GetName()

    codecs = project.GetRenderCodecs(preset["format"])
    desc, codec_name, matched = pick_codec(preset["codec"], preset.get("codec_fallbacks"), codecs)
    out["codec"] = {"requested": preset["codec"], "used": codec_name, "description": desc,
                    "matched": matched}
    if not codec_name:
        out["error"] = f"No codecs available for format '{preset['format']}'."
        return out
    if not matched:
        out["warnings"].append(f"preferred codec '{preset['codec']}' not found; using "
                               f"'{codec_name}' ({desc}) instead.")
    codec_ok = project.SetCurrentRenderFormatAndCodec(preset["format"], codec_name)
    actual = project.GetCurrentRenderFormatAndCodec() or {}
    if not codec_ok or actual.get("codec") != codec_name or actual.get("format") != preset["format"]:
        out["warnings"].append(f"requested format/codec '{preset['format']}/{codec_name}', Resolve "
                               f"reports {actual}; the render may not match the preset.")

    res = preset["resolution"]
    settings = {
        "TargetDir": str(Path(output_dir).resolve()),
        "CustomName": f"{filename}{preset['suffix']}",
        "FormatWidth": res["width"],
        "FormatHeight": res["height"],
        "ExportVideo": True,
        "ExportAudio": True,
    }
    out["settings"] = settings
    if not project.SetRenderSettings(settings):
        out["warnings"].append(f"SetRenderSettings reported failure for '{preset['name']}'; "
                               "verify the Deliver page before rendering.")
    if preset.get("note"):
        out["warnings"].append(f"NOTE: {preset['note']}")
    out["job_id"] = project.AddRenderJob() or None
    if not out["job_id"]:
        out["error"] = f"Failed to queue {preset['name']}"
    return out


def list_jobs(project):
    """GetRenderJobList with each job's status merged in."""
    jobs = []
    for job in project.GetRenderJobList() or []:
        job = dict(job)
        jid = job.get("JobId")
        status = (project.GetRenderJobStatus(jid) or {}) if jid else {}
        job["JobStatus"] = status.get("JobStatus")
        job["CompletionPercentage"] = status.get("CompletionPercentage")
        job["Error"] = status.get("Error")
        jobs.append(job)
    return jobs


def render_job_readback(project, job_id, want=None):
    """The queued job as Resolve lists it, and mismatches against `want`
    ({TargetDir, OutputFilename, TimelineName, FormatWidth, FormatHeight}
    subsets). Returns (job or None, [mismatch strings])."""
    job = next((j for j in project.GetRenderJobList() or [] if j.get("JobId") == job_id), None)
    if job is None:
        return None, [f"job {job_id} is not in the render queue"]
    problems = []
    for key, value in (want or {}).items():
        got = job.get(key)
        if got is not None and str(got) != str(value):
            problems.append(f"{key}: queued {got!r}, expected {value!r}")
    return job, problems
