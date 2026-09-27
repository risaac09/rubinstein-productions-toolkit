"""
rpresolve.config: resolve-config.json (camera bins, clip colours, render
presets) with built-in defaults for any missing key. Stdlib only.
"""

import json
from pathlib import Path

# production/resolve-config.json, beside resolve_workflow.py.
CONFIG_PATH_DEFAULT = Path(__file__).resolve().parent.parent / "resolve-config.json"

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
        config[key] = value
    return config
