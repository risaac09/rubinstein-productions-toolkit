"""CI stand-ins. A clean runner has no torch, and the tests never reach it: embed.text is patched and the probes are
stubbed. The stand-in is installed only when the real module is missing, so a developer machine still runs the real one.
rpresolve is the toolkit's own and is always real; the tests that need ffprobe or the face probes skip without them."""
import importlib.util
import shutil
import sys
import types

import pytest

# embed.py imports torch and decorates two functions with torch.no_grad(); nothing else runs without a model
if importlib.util.find_spec("torch") is None:
    m = types.ModuleType("torch")
    m.no_grad = lambda: (lambda f: f)
    sys.modules["torch"] = m


@pytest.fixture
def real_toolkit():
    """For a test that needs ffmpeg, ffprobe and the Swift face probe through rpresolve (macOS with swiftc)."""
    if not (shutil.which("ffprobe") and shutil.which("ffmpeg") and shutil.which("swiftc")):
        pytest.skip("needs ffmpeg, ffprobe and swiftc (the face probe); CI has no swiftc")
