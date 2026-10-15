"""rpstills.times: a time for frames that carry none in their EXIF.

Frame grabs from a video, screenshots and some phone exports have no
DateTimeOriginal, and without a time the cluster stage cannot tell a gap from
a run. The times come from, in this order:

  exif       the file's own DateTimeOriginal (index.py reads it; this module
             is only asked for the rest)
  sidecar    a file the person writes: --times FILE, or times.json or
             times.tsv in the shoot folder. JSON is either {"<frame id>":
             "2026-01-15T10:00:00", ...} or {"starts": {"<prefix>": "<ISO>"},
             "interval": 4}; TSV is "id<TAB>ISO time", # for comments
  filename   a stamp in the name: 20260115-100000, 2026-01-15_10-00-00,
             20260115T1000 (seconds optional)
  sequence   numbered grabs, <prefix>_<n> or <prefix>-<n>, at --interval
             seconds apart from the prefix's start (--start, or the
             sidecar's "starts"): time = start + (n - 1) * interval

Each row records which one it used as time_source. A frame none of them
reaches keeps time None, as before. A guessed time is never dressed up as a
camera time: a sequence time is a position in a grab, not a clock.
"""

import datetime as _dt
import json
import os
import re

STAMP = re.compile(r"(?P<y>20\d{2})[-_]?(?P<mo>\d{2})[-_]?(?P<d>\d{2})[T_ -]?"
                   r"(?P<h>\d{2})[-_:]?(?P<mi>\d{2})(?:[-_:]?(?P<s>\d{2}))?(?!\d)")
SEQUENCE = re.compile(r"^(?P<prefix>.*?[A-Za-z].*?)[_-](?P<n>\d{1,6})$")


def parse_iso(text):
    """A datetime from an ISO-ish string, or None."""
    try:
        return _dt.datetime.fromisoformat(str(text).strip().replace("Z", ""))
    except (TypeError, ValueError):
        return None


def load_sidecar(path):
    """{"by_id": {id: datetime}, "starts": {prefix: datetime}, "interval":
    float or None} from a JSON or TSV file. Raises ValueError on a time it
    cannot read, naming the line or key."""
    out = {"by_id": {}, "starts": {}, "interval": None}
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if path.lower().endswith(".json"):
        doc = json.loads(text)
        if not isinstance(doc, dict):
            raise ValueError(f"{path}: expected a JSON object")
        for key, value in doc.items():
            if key == "starts" and isinstance(value, dict):
                for prefix, when in value.items():
                    t = parse_iso(when)
                    if t is None:
                        raise ValueError(f"{path}: starts[{prefix!r}] is not an ISO time: {when!r}")
                    out["starts"][prefix] = t
            elif key == "interval":
                out["interval"] = float(value)
            else:
                t = parse_iso(value)
                if t is None:
                    raise ValueError(f"{path}: {key!r} is not an ISO time: {value!r}")
                out["by_id"][key] = t
        return out
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        t = parse_iso(parts[1]) if len(parts) >= 2 else None
        if t is None:
            raise ValueError(f"{path} line {n}: expected <id><TAB><ISO time>")
        out["by_id"][parts[0].strip()] = t
    return out


def default_sidecar(shoot):
    """times.json or times.tsv in the shoot folder, else None."""
    for name in ("times.json", "times.tsv"):
        p = os.path.join(shoot, name)
        if os.path.isfile(p):
            return p
    return None


def from_filename(name):
    """A datetime from a stamp in the file name, or None."""
    m = STAMP.search(name)
    if not m:
        return None
    try:
        return _dt.datetime(int(m["y"]), int(m["mo"]), int(m["d"]), int(m["h"]), int(m["mi"]),
                            int(m["s"] or 0))
    except ValueError:
        return None


def sequence_of(stem):
    """(prefix, n) for a numbered grab stem such as shot_001, or None."""
    m = SEQUENCE.match(stem)
    return (m["prefix"], int(m["n"])) if m else None


def resolve(stem, filename, sidecar=None, interval=None, start=None):
    """(datetime or None, source) for a frame without an EXIF time. `start`
    is a datetime applied to every prefix without one of its own."""
    sidecar = sidecar or {"by_id": {}, "starts": {}, "interval": None}
    if stem in sidecar["by_id"]:
        return sidecar["by_id"][stem], "sidecar"
    t = from_filename(filename)
    if t is not None:
        return t, "filename"
    seq = sequence_of(stem)
    step = interval if interval is not None else sidecar.get("interval")
    if seq and step is not None:
        base = sidecar["starts"].get(seq[0]) or start
        if base is not None:
            return base + _dt.timedelta(seconds=(seq[1] - 1) * float(step)), "sequence"
    return None, None
