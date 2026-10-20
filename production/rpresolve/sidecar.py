"""
rpresolve.sidecar: the one envelope every JSON sidecar carries (VP-140), and
the helpers that build and check it. Spec: production/sidecar-envelope.md.
Stdlib only; the Resolve MCP server runs it on Python 3.9.

    card_key(path)      the clip id, <card folder>/<stem>
    shared_ids(paths)   ids two or more paths share (never join on these)
    source_sha256(path) the hash offload.sh recorded for a card file, or None
    header(...)         the top-level fields of a sidecar
    validate(doc)       a list of problems; empty means the envelope is sound
"""

import datetime
import os
import re
import subprocess

LANES = ("cine", "sound", "story", "fusion")

# The card folder rule: <CAM>-<first 6 hex of the card id>, as offload.sh names it.
CARD_DIR = re.compile(r"^[A-Za-z0-9]+-[0-9a-f]{6}$")
_NAME = re.compile(r"^(cine|sound|story|fusion)\.[A-Za-z0-9][A-Za-z0-9_-]*$")
_AT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def stem(path):
    return os.path.splitext(os.path.basename(path))[0]


def _card_dir(path):
    """(index into the path parts of the card folder, parts), or (None, parts)."""
    parts = os.path.normpath(path).split(os.sep)
    for i in range(len(parts) - 2, -1, -1):
        if CARD_DIR.match(parts[i]):
            return i, parts
    return None, parts


def card_key(path):
    """The clip id: <card folder>/<stem>, the card folder being the nearest
    folder above named <CAM>-<id6>, else the file's own folder. A card keeps
    its DCIM tree, so the own folder alone (100_PANA) would repeat across
    cards."""
    i, parts = _card_dir(path)
    parent = parts[:-1]
    card = parts[i] if i is not None else (parent[-1] if parent else "")
    return f"{card}/{stem(path)}"


def shared_ids(paths):
    """{id: [paths]} for each clip id two or more of the paths share."""
    by = {}
    for p in paths:
        by.setdefault(card_key(p), []).append(p)
    return {k: v for k, v in by.items() if len(v) > 1}


def source_sha256(path):
    """The sha256 offload.sh recorded for this file in <CAM>-<id6>.SHA256SUMS
    beside the card folder (lines are '<hash>  <path relative to the card
    folder>'). None when the file is not under a card folder or no sums file
    lists it. Never re-hashes."""
    i, parts = _card_dir(path)
    if i is None:
        return None
    sums = os.sep.join(parts[:i] + [parts[i] + ".SHA256SUMS"]) or os.sep
    rel = "/".join(parts[i + 1:])
    try:
        with open(sums, encoding="utf-8") as f:
            for line in f:
                h, sep, name = line.rstrip("\n").partition("  ")
                if sep and name == rel:
                    return h
    except OSError:
        pass
    return None


def _toolkit_commit():
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           cwd=os.path.dirname(os.path.abspath(__file__)),
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except Exception:
        return None


def header(name, version, path, transfer=None, primaries=None, tool=None, clip_id=...):
    """The envelope's top-level fields. `transfer` and `primaries` are
    {tag, read_as} or None. `clip_id` defaults to card_key(path); pass None
    for a sidecar that covers more than one clip. Add the lane key yourself:
    header(...)[lane] = payload."""
    path = os.path.abspath(path)
    return {
        "schema": {"name": name, "version": version},
        "clip_id": card_key(path) if clip_id is ... else clip_id,
        "source": {"path": path, "sha256": source_sha256(path)},
        "transfer": transfer,
        "primaries": primaries,
        "generated_by": {"tool": tool or ("rpresolve." + name.split(".", 1)[-1]),
                         "version": _toolkit_commit()},
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _is_tag_pair(v):
    return (isinstance(v, dict) and set(v) == {"tag", "read_as"}
            and all(isinstance(x, str) for x in v.values()))


def validate(doc):
    """Problems with a sidecar's envelope, as strings; [] when it is sound.
    The lane payload itself is not inspected."""
    if not isinstance(doc, dict):
        return ["sidecar is not an object"]
    bad = []
    schema = doc.get("schema")
    lane = None
    if not isinstance(schema, dict):
        bad.append("schema missing")
    else:
        n, v = schema.get("name"), schema.get("version")
        if not isinstance(n, str) or not _NAME.match(n):
            bad.append("schema.name must be <lane>.<tool> with lane one of " + ", ".join(LANES))
        else:
            lane = n.split(".", 1)[0]
        if not isinstance(v, int) or isinstance(v, bool):
            bad.append("schema.version must be an integer")
    present = [k for k in LANES if k in doc]
    if len(present) != 1:
        bad.append("exactly one lane key of " + ", ".join(LANES) + " is required, found: "
                   + (", ".join(present) or "none"))
    elif lane and present[0] != lane:
        bad.append(f"lane key {present[0]} does not match schema lane {lane}")
    allowed = {"schema", "clip_id", "source", "transfer", "primaries",
               "generated_by", "generated_at"} | set(LANES)
    extra = sorted(set(doc) - allowed)
    if extra:
        bad.append("unknown top-level keys: " + ", ".join(extra))
    if "clip_id" not in doc or not (doc["clip_id"] is None or isinstance(doc["clip_id"], str)):
        bad.append("clip_id must be a string or null")
    src = doc.get("source")
    if not isinstance(src, dict) or set(src) != {"path", "sha256"}:
        bad.append("source must be {path, sha256}")
    else:
        if not isinstance(src["path"], str) or not os.path.isabs(src["path"]):
            bad.append("source.path must be an absolute path")
        if not (src["sha256"] is None or (isinstance(src["sha256"], str)
                                          and re.fullmatch(r"[0-9a-f]{64}", src["sha256"]))):
            bad.append("source.sha256 must be 64 hex characters or null")
    for k in ("transfer", "primaries"):
        if k not in doc or not (doc[k] is None or _is_tag_pair(doc[k])):
            bad.append(f"{k} must be null or {{tag, read_as}}")
        elif lane == "cine" and doc[k] is None:
            bad.append(f"a cine sidecar must name {k} (picture tools name their curve)")
    gb = doc.get("generated_by")
    if (not isinstance(gb, dict) or set(gb) != {"tool", "version"}
            or not isinstance(gb["tool"], str)
            or not (gb["version"] is None or isinstance(gb["version"], str))):
        bad.append("generated_by must be {tool, version} with version a string or null")
    at = doc.get("generated_at")
    if not isinstance(at, str) or not _AT.match(at):
        bad.append("generated_at must be YYYY-MM-DDTHH:MM:SSZ")
    return bad
