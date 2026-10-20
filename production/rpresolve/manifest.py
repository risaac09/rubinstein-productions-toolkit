"""
rpresolve.manifest: write the delivery manifest and checksums for a
delivery folder laid out <delivery>/<destination key>/<files>, in the shape
production/delivery-manifest.md specifies, and re-check the checksums.
Facts come from ffprobe and ffmpeg (through delivercheck) and the files'
bytes; nothing is guessed. Offline, stdlib only.

    manifest.md     the header table, one row per file, the licences block
    checksums.txt   `shasum -a 256` lines with paths relative to the delivery
                    root, covering every delivered file and manifest.md, so
                    `shasum -a 256 -c checksums.txt` run there verifies all
"""

import hashlib
import os
import re
from fractions import Fraction

from . import deliver, delivercheck as dc

MANIFEST = "manifest.md"
CHECKSUMS = "checksums.txt"
SHA_RE = re.compile(r"[0-9a-f]{64}")
VIDEO_EXTS = {".mp4", ".mov"}
IGNORED = {".ds_store"}


class ManifestError(RuntimeError):
    """The delivery cannot be described truthfully, or would overwrite."""


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan(root):
    """[(destination key, file name)] sorted, from the subfolders of root.
    Raises ManifestError for a nested folder, a file type with no row, or no
    files. Files directly in root (the manifest itself) are not scanned."""
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise ManifestError(f"{root} is not a folder")
    found = []
    for sub in sorted(os.listdir(root)):
        p = os.path.join(root, sub)
        if not os.path.isdir(p):
            continue
        for name in sorted(os.listdir(p)):
            if name.lower() in IGNORED:
                continue
            if os.path.isdir(os.path.join(p, name)):
                raise ManifestError(f"{sub}/{name} is a folder; a destination folder holds files only")
            if os.path.splitext(name)[1].lower() not in VIDEO_EXTS | {".srt"}:
                raise ManifestError(f"{sub}/{name}: not a deliverable (mp4, mov or srt); "
                                    "move it out or deliver it by the return package")
            found.append((sub, name))
    if not found:
        raise ManifestError(f"{root} has no destination subfolders with files")
    return found


def timecode(seconds, fps):
    """hh:mm:ss.ff, frames counted at fps (a Fraction)."""
    frames = round(Fraction(seconds).limit_denominator(100000) * fps)
    whole = int(round(float(fps)))
    s, ff = divmod(frames, whole)
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}.{ff:02d}"


def _srt_row(path):
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        cues, problems = dc.parse_srt(f.read())
    if problems:
        raise ManifestError(f"{path}: not a valid SRT ({problems[0]})")
    end = cues[-1][1]
    s, cs = divmod(int(round(end * 100)), 100)
    return {"codec": "n/a", "res": "n/a", "fps": "n/a", "loud": "n/a",
            "dur": f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}.{cs:02d}"}


def _video_row(path, loudness):
    info = dc.probe(path)
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    if not video:
        raise ManifestError(f"{path}: no video stream")
    fps = dc.rate(video.get("r_frame_rate"))
    secs = dc.duration(info)
    if fps is None or secs is None:
        raise ManifestError(f"{path}: ffprobe reports no frame rate or duration")
    codec = video.get("codec_name", "?")
    if video.get("profile") and codec == "prores":
        codec += f" {video['profile']}"
    loud = "n/a"
    if loudness and any(s.get("codec_type") == "audio" for s in streams):
        m = dc.measure_loudness(path)
        if m["integrated_lufs"] is None or m["true_peak_dbtp"] is None:
            raise ManifestError(f"{path}: ffmpeg gave no loudness reading")
        loud = f"{m['integrated_lufs']:.1f} LUFS, {m['true_peak_dbtp']:.1f} dBTP"
    return {"codec": f"{codec}, {video.get('pix_fmt', '?')}",
            "res": f"{video.get('width')}x{video.get('height')}",
            "fps": f"{fps.numerator}/{fps.denominator}" if fps.denominator != 1 else str(fps.numerator),
            "loud": loud, "dur": timecode(secs, fps)}


def _cell(text):
    text = str(text).replace("|", "\\|").replace("\n", " ").strip()
    return text or "none"


def build(root, config, project, approval, prepared_by, review_copy, review_sha,
          delivered, machine_steps="none stated", credits="none requested",
          platform_notes="none looked up", licences=(), loudness=True):
    """The manifest text and the list of (relative path, sha256) for root.
    Every file's destination must be a configured one and its name must pass
    that destination's name rule (a failing name does not ship)."""
    if not SHA_RE.fullmatch(review_sha):
        raise ManifestError("review copy SHA-256 must be 64 lowercase hex characters")
    root = os.path.abspath(root)
    rows, sums = [], []
    subs = deliver.subfolders(config)
    for sub, name in scan(root):
        rel = f"{sub}/{name}"
        path = os.path.join(root, rel)
        if sub.casefold() not in subs:
            raise ManifestError(f"{rel}: folder '{sub}' is not a configured destination's subfolder")
        key = subs[sub.casefold()]
        ext = os.path.splitext(name)[1].lower()
        if ext != ".srt":
            problems = deliver.name_problems(name, deliver.destination(config, key))
            if problems:
                raise ManifestError(f"{rel}: " + "; ".join(problems))
        facts = _srt_row(path) if ext == ".srt" else _video_row(path, loudness)
        digest = sha256(path)
        sums.append((rel, digest))
        rows.append(f"| {_cell(name)} | {ext[1:]} | {facts['codec']} | {facts['res']} | "
                    f"{facts['fps']} | {facts['dur']} | {facts['loud']} | {digest} | "
                    f"{_cell(key)} | {_cell(approval)} |")
    lic = ["| Asset | Licence | Attribution required |", "|---|---|---|"]
    for item in licences:
        parts = [p.strip() for p in item.split("|")]
        if len(parts) != 3:
            raise ManifestError(f"--licence wants 'asset|licence|attribution', got {item!r}")
        lic.append("| " + " | ".join(_cell(p) for p in parts) + " |")
    text = "\n".join([
        f"# Delivery manifest: {_cell(project)}", "",
        "| | |", "|---|---|",
        f"| Project | {_cell(project)} |", f"| Delivered | {_cell(delivered)} |",
        f"| Prepared by | {_cell(prepared_by)} |", f"| Approval reference | {_cell(approval)} |",
        f"| Approved review copy | {_cell(review_copy)} |", f"| Review copy SHA-256 | {review_sha} |",
        f"| Machine-assisted steps | {_cell(machine_steps)} |", f"| Credits | {_cell(credits)} |",
        f"| Platform notes | {_cell(platform_notes)} |", "",
        "## Files", "",
        "| File | Format | Codec and container | Resolution | Frame rate | Duration | Loudness "
        "| Checksum | Destination | Approval reference |",
        "|---|---|---|---|---|---|---|---|---|---|", *rows, "",
        "## Licences used", "", *(lic if len(lic) > 2 else ["none"]), ""])
    return text, sums


def write(root, text, sums, overwrite=False):
    """Write manifest.md, then checksums.txt (which also covers the manifest).
    Refuses to replace either unless overwrite. Returns the two paths."""
    root = os.path.abspath(root)
    mpath, cpath = os.path.join(root, MANIFEST), os.path.join(root, CHECKSUMS)
    existing = [p for p in (mpath, cpath) if os.path.exists(p)]
    if existing and not overwrite:
        raise ManifestError(f"{', '.join(os.path.basename(p) for p in existing)} already "
                            "exists; a manifest is not edited after delivery. Pass --overwrite "
                            "to replace it (or keep the old one and write a new delivery folder)")
    # Both files go through a temp name so a failure leaves neither half-written.
    lines = sums + [(MANIFEST, hashlib.sha256(text.encode("utf-8")).hexdigest())]
    body = "".join(f"{h}  {rel}\n" for rel, h in sorted(lines))
    for path, data in ((mpath, text), (cpath, body)):
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            f.write(data)
    os.replace(mpath + ".tmp", mpath)
    os.replace(cpath + ".tmp", cpath)
    return mpath, cpath


def verify(root):
    """[(status, path)] re-checking checksums.txt: OK, FAILED, MISSING for a
    listed file, NOT LISTED for a delivered file the list lacks. Raises
    ManifestError when checksums.txt is absent or malformed."""
    root = os.path.abspath(root)
    cpath = os.path.join(root, CHECKSUMS)
    if not os.path.isfile(cpath):
        raise ManifestError(f"no {CHECKSUMS} in {root}")
    out, listed = [], set()
    with open(cpath, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line:
                continue
            h, sep, rel = line.partition("  ")
            h = h.lower()
            rel = rel[2:] if rel.startswith("./") else rel
            if not sep or not SHA_RE.fullmatch(h) or rel.startswith(("/", "..")) or ".." in rel.split("/"):
                raise ManifestError(f"{CHECKSUMS} line {n} is not '<sha256>  <relative path>'")
            listed.add(rel)
            path = os.path.join(root, rel)
            out.append(("MISSING" if not os.path.isfile(path)
                        else "OK" if sha256(path) == h else "FAILED", rel))
    for sub in sorted(os.listdir(root)):
        if os.path.isdir(os.path.join(root, sub)):
            for name in sorted(os.listdir(os.path.join(root, sub))):
                if name.lower() not in IGNORED and f"{sub}/{name}" not in listed:
                    out.append(("NOT LISTED", f"{sub}/{name}"))
    return out
