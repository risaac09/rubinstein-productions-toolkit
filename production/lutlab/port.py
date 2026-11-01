"""lutlab.port: move a LUT from one camera's log to another's, and compare LUTs.

A LUT only knows the code values it expects. port builds a lattice in the
target camera's encoding, converts each node through scene linear into the
encoding the LUT was made for, and samples the LUT there. No model, no fit:
inside the source LUT's domain the result is the same LUT, re-addressed.

diff samples two LUTs (same input space) on the colours real scenes contain
and reports Delta E 2000 between their outputs."""

import numpy as np

from . import cube, fit, model, pipeline, spaces


def port_table(lut, src_space, dst_space, size=cube.DEFAULT_SIZE, ev=0.0):
    """Table (size^3 x 3) for dst_space input, and the share of natural scene
    colours (pipeline.natural_samples, in dst_space) that land outside the
    source LUT's domain and are clamped there: a range or gamut the source LUT
    never saw. Lattice corners no scene reaches are not counted."""
    to_src = lambda codes: spaces.from_lin709(spaces.to_lin709(codes, dst_space) * 2.0 ** ev, src_space)
    unit = {"domain_min": np.zeros(3), "domain_max": np.ones(3)}
    nat = to_src(pipeline.natural_samples(unit, dst_space))
    outside = ~((nat >= lut["domain_min"] - 1e-9) & (nat <= lut["domain_max"] + 1e-9)).all(-1)
    share = float(outside.mean()) if len(nat) else 0.0
    return cube.sample(lut, to_src(cube.lattice(size))), share


def monitor_copy(table, size, resample):
    """The in-camera copy of a table: 33 points, clipped to 0..1. `table` is reused when it is already 33
    points; otherwise resample(33) builds the 33-point one (putting a LUT onto a new lattice, not clipping a
    bigger one, so the copy is the LUT and not a subsample)."""
    return np.clip(table if (table is not None and size == 33) else resample(33), 0, 1)


def diff(lut_a, lut_b, in_space, out="r709"):
    """Delta E 2000 between two LUTs' outputs on natural colours: overall stats
    and the median by output hue (the 12 model knots)."""
    codes = pipeline.natural_samples(lut_a, in_space)
    inside = ((codes >= lut_b["domain_min"]) & (codes <= lut_b["domain_max"])).all(-1)
    codes = codes[inside]
    lab = lambda y: spaces.cielab(np.maximum(spaces.to_lin709(np.clip(y, 0, 1), out), 0))
    ya, yb = cube.sample(lut_a, codes), cube.sample(lut_b, codes)
    de = spaces.delta_e2000(lab(ya), lab(yb))
    L, C, h = spaces.lch(spaces.oklab(np.maximum(spaces.to_lin709(np.clip(ya, 0, 1), out), 0)))
    knot = np.round((np.degrees(h) % 360) / model.HUE_STEP).astype(int) % len(model.HUES)
    by_hue = {model.HUES[k]: round(float(np.median(de[(knot == k) & (C > 0.03)])), 2)
              for k in range(len(model.HUES)) if ((knot == k) & (C > 0.03)).sum() >= 10}
    return {"samples": int(len(de)), "all": fit.stats(de), "by_hue": by_hue}
