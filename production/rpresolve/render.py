"""
rpresolve.render: queue a render job for a delivery destination, and read
it back. Never starts rendering: that stays a person's decision.

Resolve can set render settings but not read them (there is no
GetRenderSettings), so queueing overwrites the project's current Deliver
page settings; only the format and codec can be read and put back. The
job itself is read back from GetRenderJobList, which reports some of the
fields set here and not others; the ones it does not report are listed as
unverified, and the rendered file is checked instead (delivercheck).
"""

import os


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


def queue_destination_job(project, dest, steps):
    """Queue one job for the current timeline from a delivery destination
    (rpresolve.deliver) and its SetRenderSettings steps (render_steps).
    Sets the format and codec, the render mode to Single clip (one file,
    under the name the steps set), then each step in order, and reads the
    mode back before AddRenderJob. When the mode does not read back as
    Single clip or a required step is refused, nothing is queued. Returns
    {job_id, error, warnings, codec, refused_required, refused_optional}.
    Prints nothing."""
    out = {"job_id": None, "error": None, "warnings": [], "codec": None,
           "refused_required": [], "refused_optional": []}
    codecs = project.GetRenderCodecs(dest["format"]) or {}
    desc, codec_name, matched = pick_codec(dest["codec"], dest.get("codec_fallbacks"), codecs)
    out["codec"] = {"requested": dest["codec"], "used": codec_name, "description": desc,
                    "matched": matched}
    if not codec_name:
        out["error"] = f"No codecs available for format '{dest['format']}'."
        return out
    if not matched:
        out["error"] = (f"codec '{dest['codec']}' (or a fallback: "
                        f"{', '.join(dest.get('codec_fallbacks') or []) or 'none'}) is not "
                        f"offered for '{dest['format']}'; Resolve offers "
                        f"{', '.join(sorted(codecs.values()))}. Nothing was queued.")
        return out
    codec_ok = project.SetCurrentRenderFormatAndCodec(dest["format"], codec_name)
    actual = project.GetCurrentRenderFormatAndCodec() or {}
    if not codec_ok or actual.get("codec") != codec_name or actual.get("format") != dest["format"]:
        out["error"] = (f"could not set the Deliver format/codec to {dest['format']}/"
                        f"{codec_name}; Resolve reports {actual}. Nothing was queued.")
        return out
    from .deliver import SINGLE_CLIP
    if not project.SetCurrentRenderMode(SINGLE_CLIP):
        out["error"] = ("Resolve refused the render mode Single clip "
                        f"(SetCurrentRenderMode({SINGLE_CLIP})). Nothing was queued.")
        return out
    for step in steps:
        if not project.SetRenderSettings(step["settings"]):
            keys = ", ".join(f"{k}={v!r}" for k, v in step["settings"].items())
            (out["refused_required"] if step["required"] else out["refused_optional"]).append(keys)
    for keys in out["refused_optional"]:
        out["warnings"].append(f"Resolve refused {keys}; the job is queued without it.")
    if out["refused_required"]:
        out["error"] = ("Resolve refused " + "; ".join(out["refused_required"]) +
                        ". Nothing was queued; the Deliver page keeps the settings that did "
                        "take.")
        return out
    mode = project.GetCurrentRenderMode()
    if mode != SINGLE_CLIP:
        out["error"] = (f"the render mode reads back as {mode!r} after Single clip "
                        f"({SINGLE_CLIP}) was set; a job in Individual clips mode writes files "
                        "under names nothing here checked. Nothing was queued.")
        return out
    out["job_id"] = project.AddRenderJob() or None
    if not out["job_id"]:
        out["error"] = f"AddRenderJob failed for {dest['name']}"
    return out


def queued_outputs(project):
    """The real paths every job in the render queue writes to."""
    out = set()
    for job in project.GetRenderJobList() or []:
        folder, name = job.get("TargetDir"), job.get("OutputFilename")
        if folder and name:
            out.add(os.path.realpath(os.path.join(str(folder), str(name))))
    return out


def unreported_jobs(project):
    """JobIds of queued jobs that do not report TargetDir and OutputFilename,
    which queued_outputs() cannot see: a clash with them goes unchecked."""
    return [job.get("JobId") for job in project.GetRenderJobList() or []
            if not (job.get("TargetDir") and job.get("OutputFilename"))]


def readback_report(job, exact, loose):
    """Compare a queued job with what was asked for. exact: fields whose
    mismatch fails the queue (paths, names, numbers). loose: fields where
    Resolve may word the same value differently (codec names), each wanting
    one of several spellings; a mismatch there is a warning. Returns
    (problems, warnings, unverified): unverified lists the fields the job
    does not report at all."""
    problems, warnings, unverified = [], [], []
    for key, value in exact.items():
        got = job.get(key)
        if got is None:
            unverified.append(key)
        elif not _same(key, got, value):
            problems.append(f"{key}: queued {got!r}, expected {value!r}")
    for key, values in loose.items():
        got = job.get(key)
        if got is None:
            unverified.append(key)
        elif str(got).casefold() not in {str(v).casefold() for v in values if v}:
            warnings.append(f"{key}: Resolve reports {got!r}, asked for {values[0]!r}; check "
                            "the Deliver page before rendering.")
    return problems, warnings, unverified


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


def _same(key, got, want):
    """Compare a queued job field with what was asked for, allowing for
    Resolve's own normalisation: folders by resolved path (a trailing slash
    or symlink is the same folder), numbers as numbers, and a frame rate
    within 0.01% whatever its wording ("23.976023", "29.97 DF"; how
    GetRenderJobList writes it is unverified)."""
    if key == "TargetDir":
        norm = lambda p: os.path.realpath(str(p).rstrip("/") or "/")
        return norm(got) == norm(want)
    if key == "FrameRate":
        from .deliver import fps_number
        a, b = fps_number(got), fps_number(want)
        return a is not None and b is not None and abs(a - b) <= 1e-4 * b
    try:
        return float(got) == float(want)
    except (TypeError, ValueError):
        return str(got) == str(want)
