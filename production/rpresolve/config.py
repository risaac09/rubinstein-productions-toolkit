"""
rpresolve.config: resolve-config.json (camera bins, clip colours, render
presets, delivery destinations) with built-in defaults for any missing key.
Stdlib only.

Most top-level keys in a config file replace the default outright. The two
delivery keys, "deliver" (house loudness tolerances and colour tags) and
"destinations", merge field by field instead, so an overlay kept outside
this repository (--config) can change one value, or add a private
target_dir, without restating the rest.
"""

import json
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
        # reports it (null).
        "color": {
            "resolve": {"ColorSpaceTag": "Rec.709", "GammaTag": "Gamma 2.4"},
            "expect": {"color_primaries": "bt709", "color_space": "bt709",
                       "color_transfer": None},
        },
        "frame_rates": ["24000/1001", "24", "25", "30000/1001", "30", "50", "60000/1001", "60"],
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


def load_config(config_path=None, warn=None):
    """Load resolve-config.json, falling back to built-in defaults for any
    missing top-level key. Never raises: a missing or malformed config
    degrades to defaults, and `warn` (if given) receives the warning text."""
    path = Path(config_path) if config_path else CONFIG_PATH_DEFAULT
    config = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy

    if not path.exists():
        return config

    # UTF-8 always: Resolve's scripting library leaves the process in the C
    # locale once connected, where a default open() decodes as ASCII.
    try:
        with open(path, encoding="utf-8") as f:
            user_config = json.load(f)
    except (ValueError, OSError) as e:  # JSONDecodeError and UnicodeDecodeError
        if warn:
            warn(f"WARNING: Could not read config '{path}': {e}. Using defaults.")
        return config

    for key, value in user_config.items():
        if key in MERGED_KEYS and isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key] = merge(config[key], value)
        else:
            config[key] = value
    return config


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
