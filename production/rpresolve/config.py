"""
rpresolve.config: resolve-config.json (camera bins, clip colours, render
presets, delivery destinations) with built-in defaults for any missing key.
Stdlib only.

Most top-level keys in a config file replace the default outright. The two
delivery keys, "deliver" (house loudness tolerances and colour tags) and
"destinations", merge field by field instead, so an overlay kept outside
this repository (--config, $RPRESOLVE_CONFIG) can change one value, or add
a private target_dir, without restating the rest. An overlay's delivery
keys lie over this repository's resolve-config.json, which lies over the
built-in defaults (load_config).
"""

import json
import os
from pathlib import Path

# production/resolve-config.json, beside resolve_workflow.py.
CONFIG_PATH_DEFAULT = Path(__file__).resolve().parent.parent / "resolve-config.json"

# Keys whose value merges into the default field by field (merge()).
MERGED_KEYS = ("deliver", "destinations")


def _web(name, aspect, width, height, codec, lufs, captions):
    """A web destination: mp4, AAC 48 kHz stereo, a loudness target."""
    h265 = codec == "H265"
    return {
        "name": name, "naming": "clip", "aspect": aspect, "target_dir": None,
        "format": "mp4", "codec": codec,
        "codec_fallbacks": ["HEVC", "H.265"] if h265 else ["H.264", "AVC"],
        "resolution": {"width": width, "height": height},
        "audio": {"codec": "aac", "resolve_codec": "aac", "sample_rate": 48000,
                  "bit_depth": None, "channels": 2, "bitrate_kbps": 320},
        "loudness": {"integrated_lufs": lufs},
        "captions": captions,
        # Web files are tagged Rec.709-A, which writes the transfer as bt709 (1-1-1);
        # "Gamma 2.4" writes it unspecified and players guess. Isaac compared both
        # renders and chose Rec.709-A on 2026-09-29 (pipeline note, spike 21).
        "color": {"resolve": {"GammaTag": "Rec.709-A"}, "expect": {"color_transfer": "bt709"}},
        # Further SetRenderSettings keys, sent one by one; a refusal is a warning.
        "resolve": {"NetworkOptimization": True},
        # What ffprobe should read back. H.264 for the web is 8-bit 4:2:0;
        # H.265 may be Main or Main10, so its bit depth is not fixed.
        "expect": {"codec_name": "hevc" if h265 else "h264", "profile": None,
                   "pix_fmt_family": "420", "bit_depth": None if h265 else 8},
    }


DEFAULT_CONFIG = {
    "bins": {
        "Source": ["iPhone", "GH7", "GH5", "Audio"],
        "Selects": [],
        "Timeline": [],
        "Graphics": [],
        "Exports": [],
    },
    "cameras": {
        "iphone": {"bin": ["Source", "iPhone"], "clip_color": "Blue"},
        "gh7": {"bin": ["Source", "GH7"], "clip_color": "Orange"},
        "gh5": {"bin": ["Source", "GH5"], "clip_color": "Yellow"},
    },
    "default_resolution": {"width": 3840, "height": 2160},
    "default_framerate": "23.976",
    "color_science_mode": "davinciYRGBColorManagedv2",
    "render_presets": {
        "youtube": {
            "name": "YouTube 4K", "resolution": {"width": 3840, "height": 2160},
            "format": "mp4", "codec": "H265", "codec_fallbacks": ["HEVC", "H.265"],
            "suffix": "_youtube",
        },
        "linkedin": {
            "name": "LinkedIn 4K", "resolution": {"width": 3840, "height": 2160},
            "format": "mp4", "codec": "H264", "codec_fallbacks": ["H.264", "AVC"],
            "suffix": "_linkedin",
        },
        "master": {
            "name": "Master ProRes", "resolution": {"width": 3840, "height": 2160},
            "format": "mov", "codec": "ProRes422HQ",
            "codec_fallbacks": ["Apple ProRes 422 HQ"], "suffix": "_master",
        },
        "story": {
            "name": "Instagram Story", "resolution": {"width": 1080, "height": 1920},
            "format": "mp4", "codec": "H264", "codec_fallbacks": ["H.264", "AVC"],
            "suffix": "_story",
            "note": "Resize only, no automatic reframe. Set per-clip Pan/Zoom before rendering vertical.",
        },
    },
    # House delivery rules every destination starts from (rpresolve.deliver).
    # A destination's own "loudness" and "color" override these field by field.
    "deliver": {
        "loudness": {"tolerance_lu": 0.5, "true_peak_max_dbtp": -1.0,
                     "fix_true_peak_margin_db": 0.5},
        # Resolve's tag strings go to SetRenderSettings; "expect" is what
        # ffprobe should read back. Which transfer Resolve writes for
        # "Gamma 2.4" is unverified until a real render, so the check only
        # reports it (null). Web deliverables are limited ("tv") range; a
        # full-range render (yuvj*, or flagged pc) plays crushed or lifted.
        "color": {
            "resolve": {"ColorSpaceTag": "Rec.709", "GammaTag": "Gamma 2.4"},
            "expect": {"color_primaries": "bt709", "color_space": "bt709",
                       "color_transfer": None, "color_range": "tv"},
        },
        "frame_rates": ["24000/1001", "24", "25", "30000/1001", "30", "50", "60000/1001", "60"],
        # The Deliver page's Data Burn-in for every destination job: "None",
        # so a timecode burn-in kept for review copies never reaches a
        # deliverable. A destination may name another; null leaves whatever
        # the Deliver page holds.
        "data_burn_in": "None",
    },
    "destinations": {
        "youtube_16x9": _web("YouTube 16:9 UHD", "16x9", 3840, 2160, "H265", -14.0, "sidecar"),
        "youtube_16x9_hd": _web("YouTube 16:9 HD", "16x9", 1920, 1080, "H265", -14.0, "sidecar"),
        "linkedin_16x9": _web("LinkedIn 16:9", "16x9", 1920, 1080, "H264", -16.0, "sidecar"),
        "linkedin_9x16": _web("LinkedIn 9:16", "9x16", 1080, 1920, "H264", -16.0, "burnin"),
        "linkedin_1x1": _web("LinkedIn 1:1", "1x1", 1080, 1080, "H264", -16.0, "burnin"),
        "substack_16x9": _web("Substack 16:9", "16x9", 1920, 1080, "H264", -16.0, "sidecar"),
        "client_master": {
            "name": "Client master ProRes 422 HQ",
            "naming": "master", "aspect": None, "target_dir": None,
            "format": "mov", "codec": "ProRes422HQ", "codec_fallbacks": ["Apple ProRes 422 HQ"],
            "resolution": "timeline",
            # "lpcm" as Resolve's AudioCodec string is unverified until a live queue.
            "audio": {"codec": "lpcm", "resolve_codec": "lpcm", "sample_rate": 48000,
                      "bit_depth": 24, "channels": None, "bitrate_kbps": None},
            "loudness": {"integrated_lufs": None},
            "captions": "none",
            "resolve": {},
            "expect": {"codec_name": "prores", "profile": "HQ", "pix_fmt_family": "422",
                       "bit_depth": 10},
        },
    },
}


class ConfigError(ValueError):
    """A config file that is missing or cannot be read, under strict=True."""


def load_config(config_path=None, warn=None, strict=False):
    """Load resolve-config.json, falling back to built-in defaults for any
    missing top-level key.

    With a named config_path (an overlay kept outside this repository),
    the delivery keys (MERGED_KEYS) come from the defaults, then the repo's
    resolve-config.json, then the overlay, each merged field by field; so
    an edit to resolve-config.json still holds under an overlay that only
    adds a target_dir. The named file's other keys replace the defaults,
    as they always have.

    By default it never raises: a missing or malformed file is left out,
    and `warn` (if given) receives the warning text. With strict, a named
    config_path that is missing, and any config file that cannot be read,
    parsed as JSON, or is not a JSON object, raises ConfigError instead;
    the delivery commands use this, so an overlay that changes a loudness
    target is never silently dropped."""
    config = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    repo = Path(CONFIG_PATH_DEFAULT)
    named = Path(config_path) if config_path else None
    if named is not None and os.path.realpath(named) == os.path.realpath(repo):
        named = None
    base = _read(repo, False, warn, strict)
    if base:
        _lay(config, base, MERGED_KEYS if named is not None else None)
    if named is not None:
        over = _read(named, True, warn, strict)
        if over:
            _lay(config, over)
    return config


def _read(path, named, warn, strict):
    """The JSON object in a config file, or None when there is none to use."""
    if not path.exists():
        if strict and named:
            raise ConfigError(f"config {path} not found.")
        return None
    # UTF-8 always: Resolve's scripting library leaves the process in the C
    # locale once connected, where a default open() decodes as ASCII.
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("the top level must be a JSON object; this one is a "
                             f"{type(data).__name__}")
    except (ValueError, OSError) as e:  # JSONDecodeError and UnicodeDecodeError
        if strict:
            raise ConfigError(f"could not read config '{path}': {e}.")
        if warn:
            warn(f"WARNING: Could not read config '{path}': {e}. " +
                 ("Using the defaults and resolve-config.json without it." if named
                  else "Using defaults."))
        return None
    return data


def _lay(config, data, keys=None):
    """Lay a config file's keys (or only `keys`) over config, in place."""
    for key, value in data.items():
        if keys is not None and key not in keys:
            continue
        if key in MERGED_KEYS and isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key] = merge(config[key], value)
        else:
            config[key] = value


def merge(base, over):
    """base with over laid on top: dicts merge key by key, recursively;
    anything else (lists, numbers, null) replaces. Neither input changes."""
    out = json.loads(json.dumps(base))
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = json.loads(json.dumps(value))
    return out
