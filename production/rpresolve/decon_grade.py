"""
rpresolve.decon_grade: an estimated grade per scene of a reference video, for `decon recipe --grade`.

Scenes: consecutive shots whose mean Lab stays within SCENE_DE (CIEDE2000) of the previous shot's.
Each scene gets one still from the middle of its time span, and one .cube from `lutlab transfer`.

Source image for each transfer. A transfer carries a SOURCE still toward a REFERENCE still. The
reference is the scene's still. The rebuild's footage does not exist yet, so the source is a
neutral stand-in made from that same still: each channel stretched to a common black and white
point and its mean set to the grey mean (a gray-world balance). The cube then reads "what takes
a neutral, full-range picture to this look", the thing to put on ungraded rebuild footage. When
the true ungraded source is known (verify_known), pass it instead and the cube is a direct fit.

Flags from the still. vignette: all four corners darker than the centre. skin_isolated and
sky_separated are left out: a still cannot say they are separate from the rest of the grade.

lutlab is imported here and nowhere in decon.py, so the stdlib-only parser still builds.
"""

import json
import os

from . import color, decon

SCENE_DE = 12.0        # CIEDE2000 between neighbouring shots' mean Lab; above it a new scene starts
STILL_BOX = 256        # the still is read this large (fitted inside a square)
VIGNETTE_DL = 8.0      # centre minus corner mean L*, each corner, to flag a vignette
CORNER, CENTRE = 0.15, 0.30   # corner box and centre box, as a share of each side
STRETCH = (1, 99)      # percentiles that become the stand-in's black and white
STRETCH_TO = (0.03, 0.97)


def scenes(shots, labs):
    """[[shot index, ...], ...]: a new scene whenever a shot's mean Lab is over SCENE_DE from the one before."""
    out = [[0]]
    for i in range(1, len(shots)):
        de = float(color.delta_e_2000(labs[i - 1], labs[i]))
        out[-1].append(i) if de <= SCENE_DE else out.append([i])
    return out


def neutral(img):
    """The stand-in source: img (floats 0..1) with per-channel black and white fixed and the channel means equalised."""
    import numpy as np
    lo, hi = (np.percentile(img.reshape(-1, 3), p, axis=0) for p in STRETCH)
    x = STRETCH_TO[0] + (img - lo) / np.maximum(hi - lo, 1e-6) * (STRETCH_TO[1] - STRETCH_TO[0])
    m = x.reshape(-1, 3).mean(axis=0)
    return np.clip(x * (m.mean() / np.maximum(m, 1e-6)), 0, 1)


def vignette(img):
    """True when every corner's mean L* sits VIGNETTE_DL or more under the centre's."""
    h, w = img.shape[:2]
    L = color.rec709_to_lab(img)[..., 0]
    ch, cw, kh, kw = int(h * CORNER), int(w * CORNER), int(h * (1 - CENTRE) / 2), int(w * (1 - CENTRE) / 2)
    mid = L[kh:h - kh, kw:w - kw].mean()
    corners = [L[:ch, :cw], L[:ch, -cw:], L[-ch:, :cw], L[-ch:, -cw:]]
    return bool(all(mid - c.mean() >= VIGNETTE_DL for c in corners))


def _png(path, img):
    import cv2
    import numpy as np
    cv2.imwrite(path, np.round(img[..., ::-1] * 255).astype(np.uint8))


def estimate(src, ref, size=33):
    """lutlab transfer of the source image toward the reference image (both floats 0..1, HxWx3):
    {table, stages, fit} or {refused: why}."""
    import tempfile
    from lutlab import transfer
    with tempfile.TemporaryDirectory() as tmp:
        a, b = os.path.join(tmp, "src.png"), os.path.join(tmp, "ref.png")
        _png(a, src)
        _png(b, ref)
        try:
            res = transfer.transfer(a, b, size=size)
        except transfer.Refused as e:
            return {"refused": str(e)}
    first, last = res["rows"][0]["held_out"], [r for r in res["rows"] if r["applied"]][-1]["held_out"]
    return {"table": res["table"],
            "stages": [r["stage"] for r in res["rows"][1:] if r["applied"]],
            "fit": {"sliced_w2_before": first["sliced_w2"], "sliced_w2_after": last["sliced_w2"],
                    "mean_shift_after": last["mean_shift"], "lattice_move_mean": res["lattice_move"]["mean"]}}


def grade(report, video, outdir, size=33):
    """Write outdir/sceneNN.cube, outdir/stills/sceneNN.png and outdir/grade.json. Returns the grade.json dict."""
    from lutlab import cube
    shots, fps = list(report["shots"]), report.get("fps")
    w, h = decon._dims(video)
    if len(shots) > 1 and color.rec709_to_lab(decon.frame(video, decon._mid(shots[-1], fps), decon.LAB_BOX, (w, h)))[..., 0].mean() < decon.BLACK_L:
        shots.pop()   # the end black, as recipe does
    labs = [decon.mean_lab(decon.frame(video, decon._mid(s, fps), decon.LAB_BOX, (w, h))) for s in shots]
    os.makedirs(os.path.join(outdir, "stills"), exist_ok=True)
    rows = []
    for n, idx in enumerate(scenes(shots, labs), 1):
        first, last = shots[idx[0]], shots[idx[-1]]
        t = decon._mid({"in": first["in"], "out": last["out"]}, fps)  # inside the real frames, as recipe reads
        img = decon.frame(video, t, STILL_BOX, (w, h)) / 255.0
        name = f"scene{n:02d}"
        _png(os.path.join(outdir, "stills", name + ".png"), img)
        row = {"scene": n, "shots": [shots[idx[0]]["shot"], shots[idx[-1]]["shot"]],
               "span_s": [round(first["in"], 3), round(last["out"], 3)], "still_s": round(t, 3),
               "flags": {"vignette": vignette(img)}}
        est = estimate(neutral(img), img, size)
        if "refused" in est:
            row.update(cube=None, method="lutlab transfer", refused=est["refused"])
        else:
            cube.write_cube(os.path.join(outdir, name + ".cube"), est["table"], f"{name} estimated grade", overwrite=True)
            row.update(cube=name + ".cube", method="lutlab transfer, " + "+".join(est["stages"]) + ", neutral stand-in source",
                       fit=est["fit"])
        rows.append(row)
    doc = {"grade": 1, "reference": os.path.basename(video), "scene_rule": f"shots joined while mean Lab stays within CIEDE2000 {SCENE_DE:g} of the previous shot",
           "source": "neutral stand-in from each scene's own still (per-channel stretch, gray-world mean)", "scenes": rows}
    with open(os.path.join(outdir, "grade.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    return doc


def verify_known(src_still, graded_still, size=33):
    """Median CIEDE2000 between graded_still and src_still with the estimated cube applied (stills floats 0..1,
    the true ungraded source given). The measure is in sRGB Lab, as lutlab transfer reads."""
    import numpy as np
    from lutlab import cube, transfer
    from rpstills.measure import delta_e2000
    est = estimate(src_still, graded_still, size)
    if "refused" in est:
        raise decon.DeconError(est["refused"])
    lut = {"table": est["table"], "domain_min": np.zeros(3), "domain_max": np.ones(3)}
    out = cube.sample(lut, src_still)
    de = delta_e2000(transfer.to_lab(out.reshape(-1, 3), "srgb"), transfer.to_lab(graded_still.reshape(-1, 3), "srgb"))
    return float(np.median(de))
