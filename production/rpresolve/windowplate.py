"""
rpresolve.windowplate: the effects that open a window over the shot before them, and how
many frames of that shot show through.

A scale-window leaves the outgoing shot visible until its window reaches full frame (frame
37 of the macro, fusion-macros.md), a crop-window until its rectangle does (frame 26). The
operator's rule (2026-11-10): that shot keeps playing under the window, never black and
never a frozen frame. Those frames of the outgoing source, counted on from its out point,
are the plate. The build (cmd_assemble) puts them on V1 under the montage on V2; the draft
farm (drafts) draws the same frames, so a draft and a build show one thing.

Stdlib only.
"""

# Macro frames before the window is full frame: the plate runs this long (or the span's
# length, when the span is shorter and never gets there).
PLATE_FRAMES = {"scale-window": 37, "crop-window": 26}


def window_effect(effects, index):
    """The window effect id a manifest's `effects` put on span `index` (from 0), or None."""
    for e in effects or []:
        if isinstance(e, dict) and e.get("span_index") == index and e.get("effect") in PLATE_FRAMES:
            return e["effect"]
    return None


def plate_frames(effect, span_frames):
    """Frames of the outgoing shot the window needs on a span of `span_frames` frames."""
    return min(PLATE_FRAMES[effect], int(span_frames))
