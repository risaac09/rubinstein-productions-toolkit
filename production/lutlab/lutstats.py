"""lutlab.lutstats: how far a 3D LUT sits from the transform it claims, in Delta E 2000. numpy only.

  ~/.venvs/rp-tools/bin/python -m lutlab lutstats check LUT.cube --from vlog --to di [--interp tetrahedral|trilinear]
  ~/.venvs/rp-tools/bin/python -m lutlab lutstats roundtrip --from vlog --to di [--size 33|65] [--interp ...]

check samples a LUT where footage lands (natural colours, spread as pipeline.natural_samples spreads them)
and along the neutral axis, and compares it with the exact `cst` transform for --from/--to. A LUT exported
from somewhere else can be checked the same way.
roundtrip bakes FROM->TO and TO->FROM tables of --size and sends the same samples there and back through
both, the way two LUT nodes would, and compares with where they started (also in 10-bit code values).

Delta E 2000 is measured as port.diff measures it: codes clipped to 0..1, decoded to linear Rec.709,
negatives to 0, CIELAB with diffuse white at L* 100. Scene-referred values above white keep counting
(L* past 100). The neutral axis runs from the space's black code to 1.0; sub-black codes are noise
footroom, and a cst clips them anyway. Interpolation defaults to tetrahedral (ffmpeg lut3d's default and one
of Resolve's two settings); trilinear is lutlab's own cube.sample. Report only: no pass or fail."""

import json

import numpy as np

from . import cst, cube, spaces

INTERP = ("tetrahedral", "trilinear")
NEUTRAL_POINTS = 1024
CODES = 1023  # 10-bit full range, for the round trip's code error


def tetra(lut, rgb):
    """Tetrahedral sample of a LUT, same contract as cube.sample (which is trilinear): from the cell's low
    corner walk to its high corner one axis at a time, largest fraction first. R=G=B stays on the diagonal."""
    t = lut["table"]
    n = t.shape[0]
    p = (np.asarray(rgb, dtype=np.float64) - lut["domain_min"]) / (lut["domain_max"] - lut["domain_min"])
    p = np.clip(p, 0, 1) * (n - 1)
    i = np.minimum(np.floor(p).astype(int), n - 2)
    f = p - i
    order = np.argsort(-f, axis=-1, kind="stable")
    fs = np.take_along_axis(f, order, -1)
    w = np.concatenate([1 - fs[..., :1], fs[..., :-1] - fs[..., 1:], fs[..., -1:]], -1)
    out = w[..., 0:1] * t[i[..., 0], i[..., 1], i[..., 2]]
    for k in range(3):
        ax = order[..., k:k + 1]
        np.put_along_axis(i, ax, np.take_along_axis(i, ax, -1) + 1, -1)
        out = out + w[..., k + 1:k + 2] * t[i[..., 0], i[..., 1], i[..., 2]]
    return out


def sample(lut, rgb, interp="tetrahedral"):
    if interp not in INTERP:
        raise ValueError(f"interp must be one of {INTERP}")
    return tetra(lut, rgb) if interp == "tetrahedral" else cube.sample(lut, rgb)


def unit_lut(table):
    return {"table": table, "domain_min": np.zeros(3), "domain_max": np.ones(3), "title": ""}


def natural(space, ev=(-6.0, 4.0), rel=np.arange(0.0, 0.33, 0.03), hue_step=5):
    """pipeline.natural_samples on the 0..1 domain, kept here so lutstats needs no scipy (a test holds the
    two equal): greys from -6 to +4 stops around 18%, chroma up to about the ColorChecker's strongest patch,
    every 5 degrees of OKLab hue, as code values in `space`."""
    e = np.arange(ev[0], ev[1] + 1e-9, 0.5)
    L = np.cbrt(0.18 * 2.0 ** e)
    h = np.radians(np.arange(0, 360, hue_step))
    LL, RR, HH = np.meshgrid(L, rel, h, indexing="ij")
    lab = np.stack([LL, RR * LL * np.cos(HH), RR * LL * np.sin(HH)], -1).reshape(-1, 3)
    lin = spaces.oklab_to_lin709(lab)
    codes = spaces.from_lin709(lin[lin.min(-1) >= -0.05 * np.abs(lin).max(-1)], space)
    return codes[((codes >= 0) & (codes <= 1)).all(-1)]


def neutral(space, n=NEUTRAL_POINTS):
    black = float(np.clip(spaces.from_lin709(np.zeros(3), space)[0], 0, 1))
    return np.repeat(np.linspace(black, 1.0, n)[:, None], 3, -1)


def lab(codes, space):
    return spaces.cielab(np.maximum(spaces.to_lin709(np.clip(codes, 0, 1), space), 0))


def stats(x):
    x = np.asarray(x, dtype=np.float64).ravel()
    return {"n": int(x.size), "mean": round(float(x.mean()), 4), "p99": round(float(np.percentile(x, 99)), 4),
            "max": round(float(x.max()), 4)}


def check(lut, src, dst, interp="tetrahedral"):
    """Delta E 2000 between the LUT and the exact cst transform src -> dst, on natural colours and the neutral
    axis, both as code values in src."""
    r = {"mode": "check", "from": src, "to": dst, "size": int(lut["table"].shape[0]), "interp": interp}
    for key, x in (("natural", natural(src)), ("neutral", neutral(src))):
        r[key] = stats(spaces.delta_e2000(lab(cst.transform(x, src, dst), dst), lab(sample(lut, x, interp), dst)))
    return r


def roundtrip(src, dst, size=33, interp="tetrahedral"):
    """src -> dst -> src through two baked tables of `size`: Delta E 2000 and 10-bit code error against the
    start, on natural colours and the neutral axis."""
    there, back = unit_lut(cst.table(src, dst, size)), unit_lut(cst.table(dst, src, size))
    r = {"mode": "roundtrip", "from": src, "to": dst, "size": size, "interp": interp, "code_error_10bit": {}}
    for key, x in (("natural", natural(src)), ("neutral", neutral(src))):
        y = sample(back, sample(there, x, interp), interp)
        r[key] = stats(spaces.delta_e2000(lab(x, src), lab(y, src)))
        r["code_error_10bit"][key] = stats(np.abs(y - x).max(-1) * CODES)
    return r


def summary(r):
    arrow = "<->" if r["mode"] == "roundtrip" else "->"
    s = lambda k: f"{k} dE2000 mean {r[k]['mean']} p99 {r[k]['p99']} max {r[k]['max']}"
    return f"{r['mode']} {r['from']} {arrow} {r['to']} {r['size']}^3 {r['interp']}: {s('natural')}; {s('neutral')}"


def add_arguments(p):
    sub = p.add_subparsers(dest="mode", required=True)
    c = sub.add_parser("check", help="a LUT against the exact transform it claims")
    c.add_argument("lut")
    rt = sub.add_parser("roundtrip", help="FROM -> TO -> FROM through two baked tables")
    rt.add_argument("--size", type=int, default=33, choices=cst.SIZES)
    for q in (c, rt):
        q.add_argument("--from", dest="src", required=True, choices=list(spaces.SPACES))
        q.add_argument("--to", dest="dst", required=True, choices=list(spaces.SPACES))
        q.add_argument("--interp", default="tetrahedral", choices=INTERP)


def run(a):
    if a.mode == "check":
        r = check(cube.read_cube(a.lut), a.src, a.dst, a.interp)
        r["lut"] = a.lut
    else:
        r = roundtrip(a.src, a.dst, a.size, a.interp)
    print(json.dumps(r, indent=1))
    print(summary(r))
    return 0
