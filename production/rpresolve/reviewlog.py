"""
rpresolve.reviewlog: append one decoded answer to <project>/review/events.jsonl.
Standard library only. The line shape is the one production/media-review-loop.md
("Recording the answers") describes and rpresolve.tastedistill reads.

The key is what `rpstills absheet` writes (DIR.key/key.json, beside the sheet
folder) or `rpresolve.mixbuild ab` writes (DIR/sealed/key.json). Its letters
decode into `options`, and `stimulus` is the sha256 of the sheet (sheet.jpg) or
the packet (packet.json) the reviewer saw. Append-only: the existing bytes are
never touched.
"""

import hashlib
import json
import os
import re
import sys
import time

from rpresolve.tastedistill import NON_LABEL, RATINGS  # the reader's own sets

STEPS = ("triage", "look", "grade", "edit", "sound", "gate")
DEVICES = ("desktop", "phone", "ipad")
PROJECT_RE = re.compile(r"^[A-Z]+-[0-9]+$")

# Question keys the taste profile groups by. The reel keys come from the reel
# fields in story-options.md; a key outside this list is logged with a WARN.
QUESTIONS = ("warmth", "skin", "contrast", "pace", "music_level",
             "reel_option", "opening", "hook_text", "crop_x", "caption_style", "ending", "tempo")


class Refused(ValueError):
    """An answer that must not be logged."""


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_key(path):
    """(options, stimulus sha256, noise_type) from a key.json, or from the sheet
    folder or packet folder that has one. Raises Refused."""
    p = os.path.abspath(path)
    if os.path.isdir(p):
        for cand in (p + ".key", os.path.join(p, "sealed")):
            if os.path.isfile(os.path.join(cand, "key.json")):
                p = os.path.join(cand, "key.json")
                break
        else:
            raise Refused(f"no key.json for {path} (looked in {path}.key and {path}/sealed)")
    try:
        with open(p, encoding="utf-8") as f:
            doc = json.load(f)
        options = {str(k): os.path.basename(v["source"]) for k, v in doc["labels"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise Refused(f"{path} is not an absheet or mixbuild key: {e}")
    if not options:
        raise Refused(f"{p} holds no labels")
    here = os.path.dirname(p)
    if os.path.basename(here) == "sealed":  # mixbuild: the packet is the stimulus
        stim = os.path.join(os.path.dirname(here), "packet.json")
    else:  # absheet: DIR.key/key.json, the sheet in DIR
        sheet = (doc.get("sheet") or {}).get("file", "sheet.jpg")
        stim = os.path.join(here[:-4] if here.endswith(".key") else here, sheet)
    if not os.path.isfile(stim):
        raise Refused(f"{stim} not found: the key's sheet or packet must sit where its tool wrote it")
    return options, _sha256(stim), doc.get("noise_type")


def _existing_ts(log):
    out = set()
    if os.path.exists(log):
        with open(log, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                if isinstance(e, dict) and e.get("ts"):
                    out.add(str(e["ts"]))
    return out


def _utc(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def append(project_dir, project, step, device, choice=None, reason=None, key=None, question=None,
           noise_type=None, repeat_of=None, warn=None):
    """Validate and append one line; returns the line (a dict). Raises Refused."""
    from rpresolve import paths
    warn = warn or (lambda m: print("WARN: " + m, file=sys.stderr))
    if not PROJECT_RE.fullmatch(project or ""):
        raise Refused(f"project id {project!r} must look like P-0001 (letters, a hyphen, digits); no names")
    if step not in STEPS:
        raise Refused(f"step {step!r} is not one of {', '.join(STEPS)}")
    if device not in DEVICES:
        raise Refused(f"device {device!r} is not one of {', '.join(DEVICES)}")
    if choice is None and not reason:
        raise Refused("give a choice, a reason, or both")
    options, stimulus, key_noise = ({}, None, None) if key is None else load_key(key)
    if choice is not None:
        ok = choice in options or choice in NON_LABEL or (not options and choice in RATINGS)
        if not ok:
            raise Refused(f"choice {choice!r} is not a label of the key ({', '.join(sorted(options)) or 'no key given'}), "
                          f"one of {', '.join(NON_LABEL)}" + ("" if options else ", or a rating 1 to 5"))
    if question and question not in QUESTIONS:
        warn(f"question {question!r} is outside the vocabulary ({', '.join(QUESTIONS)})")
    log = os.path.join(os.path.abspath(project_dir), "review", "events.jsonl")
    problem = paths.out_problem(log)
    if problem:
        raise Refused(problem)
    seen = _existing_ts(log)
    if repeat_of and repeat_of not in seen:
        raise Refused(f"--repeat-of {repeat_of}: no line in {log} has that ts")
    t = time.time()
    while _utc(t) in seen:  # ts names a line for repeat_of, so it stays unique
        t += 1
    line = {"ts": _utc(t), "project": project, "step": step, "options": options, "choice": choice,
            "reason": reason, "device": device}
    for k, v in (("question", question), ("noise_type", noise_type or key_noise),
                 ("stimulus", stimulus), ("repeat_of", repeat_of)):
        if v:
            line[k] = v
    os.makedirs(os.path.dirname(log), exist_ok=True)
    fd = os.open(log, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        size = os.fstat(fd).st_size
        lead = b"\n" if size and os.pread(fd, 1, size - 1) != b"\n" else b""  # a torn last line stays its own line
        os.write(fd, lead + (json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    return line
