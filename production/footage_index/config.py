"""Settings for the footage indexer, in one place.

Precedence, highest first: an environment variable (FOOTAGE_<KEY>, upper case; a few older names are listed in ENV), the
"footage_index" section of the JSON file named by RP_PIPELINE_CONFIG, the default below. With neither set the tool needs
only a folder: `python -m footage_index --root DIR index`. config.example.json shows every key.

Path values expand "~". Keys that default to a place under data_dir follow it when data_dir moves.
"""
import functools
import json
import os
import sys

PRODUCTION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the folder holding rpresolve/ and this package

# key -> (default, older env name or None). A callable default gets the resolved settings so far.
DEFAULTS = {
    "data_dir": "~/.local/share/footage-index",   # the database, thumbnails, log and edit plans live under here
    "db": lambda s: os.path.join(s["data_dir"], "footage.db"),
    "thumbs": lambda s: os.path.join(s["data_dir"], "thumbs"),
    "log_file": lambda s: os.path.join(s["data_dir"], "footage-index.log"),
    "edits_dir": lambda s: os.path.join(s["data_dir"], "edits"),  # plans: <edits_dir>/<shoot>-<method>/
    "cache_dir": "~/.cache/footage-index",
    "roots": [],                    # [] = one root, from --root or FOOTAGE_ROOT; see roots.py for the entry shape
    "skip_prefixes": ["#recycle/"],  # relpath prefixes never indexed
    "staging_prefix": "",           # a folder whose copies lose to the same take elsewhere ("" = none)
    "restricted_prefix": "",        # relpaths under this prefix are flagged and left out of searches that pass --no-client ("" = none)
    "required_folder": "",          # a folder every root must contain, or the index run refuses (guards against a wrong mount; "" = no check)
    "review_log": "",               # a JSON-lines log of picks from past reviews ("" = none)
    "taste_profile": "",            # a Markdown file of look preferences ("" = none)
    "methods_dir": lambda s: os.path.join(PRODUCTION, "edit-methods"),
    "tools_python": lambda s: sys.executable,  # the interpreter (a venv) that has the rpstills and lutlab packages
    "ollama_url": "http://127.0.0.1:11434",
    "vlm": "qwen2.5vl:7b",
    "siglip": "google/siglip2-base-patch16-384",
    "siglip_device": "",            # "" = mps when present, else cpu
    "slot_floor": 0.10,             # SigLIP cosine a slot's best match must clear
}
ENV = {"db": "FOOTAGE_DB", "thumbs": "FOOTAGE_THUMBS", "edits_dir": "FOOTAGE_EDITS", "taste_profile": "FOOTAGE_TASTE",
       "review_log": "FOOTAGE_EVENTS", "methods_dir": "FOOTAGE_METHODS", "ollama_url": "OLLAMA_HOST_URL",
       "vlm": "FOOTAGE_VLM", "siglip": "FOOTAGE_SIGLIP", "siglip_device": "FOOTAGE_SIGLIP_DEVICE",
       "slot_floor": "FOOTAGE_SLOT_FLOOR", "roots": "FOOTAGE_ROOTS"}
PATHS = {"data_dir", "db", "thumbs", "log_file", "edits_dir", "cache_dir", "review_log", "taste_profile", "methods_dir", "tools_python"}


def _file():
    p = os.environ.get("RP_PIPELINE_CONFIG", "").strip()
    if not p:
        return {}
    with open(os.path.expanduser(p), encoding="utf-8") as f:
        return (json.load(f) or {}).get("footage_index") or {}


def load():
    """The resolved settings dict. Reads the config file and the environment fresh on each call."""
    given, out = _file(), {}
    unknown = set(given) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown footage_index setting(s) in {os.environ['RP_PIPELINE_CONFIG']}: {', '.join(sorted(unknown))}")
    for k, d in DEFAULTS.items():
        env = os.environ.get(ENV.get(k) or f"FOOTAGE_{k.upper()}")
        if env not in (None, ""):
            v = env if k == "roots" else _coerce(env, d)
        elif k in given and not (given[k] == "" and callable(d)):  # "" for a derived default means "use the default"
            v = given[k]
        else:
            v = d(out) if callable(d) else d
        out[k] = os.path.expanduser(v) if k in PATHS and isinstance(v, str) else v
    return out


def _coerce(text, default):
    if isinstance(default, float):
        return float(text)
    if isinstance(default, list):
        return json.loads(text) if text.lstrip().startswith("[") else [text]
    return text


@functools.lru_cache(maxsize=1)
def _cached(_key):
    return load()


def get(key):
    """One setting. Cached per (config path, environment), so a test that changes either sees the change."""
    env = tuple(sorted((k, v) for k, v in os.environ.items() if k.startswith("FOOTAGE_") or k in ("RP_PIPELINE_CONFIG", "OLLAMA_HOST_URL")))
    return _cached(env)[key]
