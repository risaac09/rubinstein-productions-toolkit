"""
rpresolve.relink: decide which file a clip that Resolve cannot find should be
pointed at. Pure functions over plain dicts, so the matching is tested with
no Resolve and no media; the media-pool walk and the write live in
workflows.relink.

A clip is relinked only to a file that verifies as the same take. For video
that is four facts that must all be known on both sides and all agree:
start timecode, frame count (one frame of slack), resolution and frame
rate. A name is never enough, and a clip with no timecode on either side is
UNVERIFIABLE, not guessed. Stills and audio-only clips are not verified by
this version and are never relinked (UNVERIFIABLE): Resolve's Date Created
for a still is not known to be the capture time, and a burst shares its
second.
"""

import os
import re
import unicodedata

VERIFIED = "VERIFIED"
AMBIGUOUS = "AMBIGUOUS"
NO_MATCH = "NO MATCH"
UNVERIFIABLE = "UNVERIFIABLE"

FRAME_SLACK = 1
FPS_SLACK = 0.011  # Resolve reports 29.97 for 30000/1001
MAX_CANDIDATES = 6
STILL_EXTS = {".rw2", ".dng", ".cr2", ".cr3", ".arw", ".nef", ".orf", ".raf", ".jpg", ".jpeg",
              ".png", ".tif", ".tiff", ".heic", ".heif", ".gif", ".bmp", ".webp"}
AUDIO_EXTS = {".wav", ".aif", ".aiff", ".mp3", ".m4a", ".aac", ".flac", ".bwf"}
# Properties that must read the same after a relink. A change is reported as
# drift; the tool never sets one back.
STABLE_PROPS = ("Start TC", "Frames", "Resolution", "FPS", "Input Color Space", "Data Level")

_TC = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})[:;.](\d{2,3})$")


def nfc(s):
    """HFS/APFS and SMB treat Unicode normalization forms as one name."""
    return unicodedata.normalize("NFC", s)


def kind_of(path, type_prop=""):
    """'still', 'audio' or 'video' for a clip, by extension first, then Resolve's Type."""
    ext = os.path.splitext(path)[1].lower()
    if ext in STILL_EXTS:
        return "still"
    if ext in AUDIO_EXTS:
        return "audio"
    t = str(type_prop).lower()
    if t == "audio":
        return "audio"
    if t in ("still", "image"):
        return "still"
    return "video"


def normalize_tc(value):
    """'22:10:10;28' and '22:10:10:28' are the same timecode here (drop or not);
    returns 'HH:MM:SS:FF' or '' when value is not a timecode."""
    m = _TC.match(str(value or "").strip())
    if not m:
        return ""
    h, mi, s, f = m.groups()
    return f"{int(h):02d}:{mi}:{s}:{int(f):02d}"


def _int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _float(value):
    try:
        v = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _resolution(value):
    m = re.match(r"^\s*(\d+)\s*[xX]\s*(\d+)\s*$", str(value or ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


def clip_facts(props):
    """The comparable facts of a clip from Resolve's GetClipProperty() dict."""
    path = props.get("File Path") or ""
    return {"path": path, "kind": kind_of(path, props.get("Type", "")),
            "tc": normalize_tc(props.get("Start TC")), "frames": _int(props.get("Frames")),
            "resolution": _resolution(props.get("Resolution")), "fps": _float(props.get("FPS"))}


def _ratio(text):
    m = re.match(r"^(\d+)/(\d+)$", str(text or ""))
    if m and int(m.group(2)):
        return int(m.group(1)) / int(m.group(2))
    return _float(text)


def probe_facts(info, start_tc):
    """The same facts from an ffprobe JSON (-show_format -show_streams). start_tc
    is captions.file_start(info)[0], passed in so this stays pure."""
    video = next((s for s in info.get("streams") or [] if s.get("codec_type") == "video"), None)
    if not video:
        return {"kind": "audio", "tc": normalize_tc(start_tc), "frames": None,
                "resolution": None, "fps": None}
    fps = _ratio(video.get("avg_frame_rate")) or _ratio(video.get("r_frame_rate"))
    frames = _int(video.get("nb_frames"))
    if frames is None:
        dur = _float(video.get("duration")) or _float((info.get("format") or {}).get("duration"))
        frames = round(dur * fps) if dur and fps else None
    w, h = _int(video.get("width")), _int(video.get("height"))
    return {"kind": "video", "tc": normalize_tc(start_tc), "frames": frames,
            "resolution": (w, h) if w and h else None, "fps": fps}


def compare(clip, cand):
    """(True, '') when the candidate verifies as the clip's file, else (False, reason).
    Every fact must be known on both sides and agree."""
    missing = [k for k in ("tc", "frames", "resolution", "fps") if not clip.get(k) or not cand.get(k)]
    if missing:
        side = [k for k in missing if not clip.get(k)]
        return False, ("Resolve reports no " + ", ".join(side) if side
                       else "the file has no " + ", ".join(missing))
    if cand["resolution"] != clip["resolution"]:
        return False, f"resolution {cand['resolution']} is not {clip['resolution']}"
    if abs(cand["fps"] - clip["fps"]) > FPS_SLACK:
        return False, f"frame rate {cand['fps']:.3f} is not {clip['fps']:.3f}"
    if abs(cand["frames"] - clip["frames"]) > FRAME_SLACK:
        return False, f"{cand['frames']} frames is not {clip['frames']}"
    if cand["tc"] != clip["tc"]:
        return False, f"start timecode {cand['tc']} is not {clip['tc']}"
    return True, ""


def index_roots(roots, walk=os.walk):
    """{NFC file name: [absolute paths]} for every file under the roots, in
    root order (earlier roots win ties). Hidden and AppleDouble files are left out."""
    index = {}
    for root in roots:
        for dirpath, dirnames, files in walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(files):
                if name.startswith("."):
                    continue
                index.setdefault(nfc(name), []).append(os.path.join(dirpath, name))
    return index


def rank_candidates(candidates, votes):
    """Candidates ordered by how many of this run's offline clips also have a
    same-named file in the candidate's folder, then by path; capped."""
    return sorted(candidates, key=lambda c: (-votes.get(os.path.dirname(c), 0), c))[:MAX_CANDIDATES]


def folder_votes(offline_names, index):
    """{folder: number of offline clip names it holds}."""
    votes = {}
    for name in offline_names:
        for folder in {os.path.dirname(p) for p in index.get(nfc(name), ())}:
            votes[folder] = votes.get(folder, 0) + 1
    return votes


def decide(clip, probed, roots):
    """Status for one offline clip. probed is [(path, facts or None, size)] in
    candidate order. Verified candidates of identical size are copies of one
    file: the earliest root wins, then the shortest path. Verified candidates
    of different sizes are AMBIGUOUS and nothing is relinked.

    Returns {status, path, size, reason, alternates}."""
    out = {"status": NO_MATCH, "path": "", "size": None, "reason": "", "alternates": 0}
    if clip["kind"] != "video":
        out.update(status=UNVERIFIABLE, reason=f"{clip['kind']} clips are not verified by this tool")
        return out
    if not probed:
        out["reason"] = "no file with this name under the search roots"
        return out
    verified, why = [], []
    for path, facts, size in probed:
        if facts is None:
            why.append(f"{os.path.basename(os.path.dirname(path))}: could not be read")
            continue
        ok, reason = compare(clip, facts)
        if ok:
            verified.append((path, size))
        else:
            why.append(f"{os.path.basename(os.path.dirname(path)) or '.'}: {reason}")
    if not verified:
        no_data = [w for w in why if "no " in w and "Resolve reports" in w]
        out.update(status=UNVERIFIABLE if no_data and len(no_data) == len(why) else NO_MATCH,
                   reason="; ".join(why[:3]))
        return out
    sizes = {s for _, s in verified}
    if len(sizes) > 1:
        out.update(status=AMBIGUOUS, reason=f"{len(verified)} verified files differ in size: "
                   + ", ".join(sorted(p for p, _ in verified)[:3]))
        return out
    chosen = min(verified, key=lambda v: (roots_rank(v[0], roots), len(v[0]), v[0]))
    out.update(status=VERIFIED, path=chosen[0], size=chosen[1], alternates=len(verified) - 1,
               reason="timecode, frames, resolution and frame rate match")
    return out


def roots_rank(path, roots):
    for i, r in enumerate(roots):
        if path == r or path.startswith(r.rstrip("/") + "/"):
            return i
    return len(roots)


def drift(before, after):
    """{property: (before, after)} for the stable properties that read differently."""
    return {k: (before.get(k), after.get(k)) for k in STABLE_PROPS
            if str(before.get(k, "")) != str(after.get(k, ""))}
