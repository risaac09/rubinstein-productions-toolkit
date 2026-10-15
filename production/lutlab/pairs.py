"""lutlab.pairs: fit the look model from image pairs instead of a LUT.

Two sources:
  RAW + camera JPEG  the RAW developed flat and linear (darktable-cli: no
                     tone curve, camera white balance, linear Rec.709), the
                     camera's own JPEG as the target. Input space lin709,
                     output srgb.
  frame pairs        a source frame (log) and the graded frame, exported as
                     16-bit TIFF or PNG at the same framing.
Each pair is aligned (ECC homography, OpenCV), blurred a little, masked
(frame edges, strong edges, clipped pixels), and split into 32 px blocks on a
checkerboard: one half is binned into a 3D grid of mean colours to fit on, the
other half is held out to report the error."""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from . import fit, model, report, spaces

DARKTABLE = "/Applications/darktable.app/Contents/MacOS/darktable-cli"
WORK_PX = 1200


def develop_raw(raw, tmp, px=2000):
    """RAW -> linear Rec.709 float image (H, W, 3), camera white balance, no tone curve."""
    exe = shutil.which("darktable-cli") or DARKTABLE
    if not os.path.exists(exe):
        raise SystemExit(f"darktable-cli not found (looked on PATH and at {DARKTABLE})")
    out = Path(tmp) / (Path(raw).stem + ".tif")
    conf = Path(tmp) / "dtconf"
    conf.mkdir(exist_ok=True)
    cmd = [exe, str(raw), str(out), "--width", str(px), "--height", str(px), "--apply-custom-presets", "false",
           "--icc-type", "LIN_REC709", "--core", "--configdir", str(conf), "--library", ":memory:",
           "--conf", "plugins/darkroom/workflow=none", "--conf", "plugins/darkroom/chromatic-adaptation=legacy",
           "--conf", "plugins/imageio/format/tiff/bpp=16"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode or not out.exists():
        raise RuntimeError(f"darktable-cli failed on {raw}: {r.stderr[-500:]}")
    return read_image(out)


def read_image(path):
    """Any 8/16-bit image -> float RGB in 0..1, EXIF orientation applied for JPEGs."""
    if str(path).lower().endswith((".jpg", ".jpeg")):
        im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        return np.asarray(im, dtype=np.float64) / 255
    a = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if a is None:
        raise ValueError(f"cannot read {path}")
    scale = {np.dtype("uint8"): 255, np.dtype("uint16"): 65535}.get(a.dtype, 1)
    return a[..., 2::-1].astype(np.float64) / scale


def _resize(a, w, h):
    return cv2.resize(a.astype(np.float32), (w, h), interpolation=cv2.INTER_AREA).astype(np.float64)


def align(src, dst, src_preview):
    """Warp src onto dst's frame. src_preview: a display-like version of src for matching."""
    g1 = cv2.cvtColor(src_preview.astype(np.float32), cv2.COLOR_RGB2GRAY)
    g2 = cv2.cvtColor(dst.astype(np.float32), cv2.COLOR_RGB2GRAY)
    warp = np.eye(3, dtype=np.float32)
    try:
        _, warp = cv2.findTransformECC(g2, g1, warp, cv2.MOTION_HOMOGRAPHY,
                                       (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 200, 1e-6), None, 5)
        ok = True
    except cv2.error:
        ok = False
    h, w = dst.shape[:2]
    out = cv2.warpPerspective(src.astype(np.float32), warp, (w, h), flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP)
    return out.astype(np.float64), ok, warp


def sample_pair(src, dst, src_preview, margin=0.10, edge_pct=75, block=32):
    """Aligned, blurred, masked pixel samples: (src, dst, held_out flag, info)."""
    h, w = dst.shape[:2]
    s = WORK_PX / max(h, w)
    W, H = int(round(w * s)), int(round(h * s))
    dst = _resize(dst, W, H)
    src = _resize(src, W, H)
    src_preview = _resize(src_preview, W, H)
    src, ok, warp = align(src, dst, src_preview)
    srcb = cv2.GaussianBlur(src.astype(np.float32), (0, 0), 1.5).astype(np.float64)
    dstb = cv2.GaussianBlur(dst.astype(np.float32), (0, 0), 1.5).astype(np.float64)
    g = cv2.cvtColor(dstb.astype(np.float32), cv2.COLOR_RGB2GRAY)
    grad = np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
    yy, xx = np.mgrid[0:H, 0:W]
    m = (xx > margin * W) & (xx < (1 - margin) * W) & (yy > margin * H) & (yy < (1 - margin) * H)
    m &= grad < np.percentile(grad, edge_pct)
    m &= (dstb.max(-1) < 0.985) & (dstb.min(-1) > 0.01)
    # each channel clips at its own level once white balance has scaled it
    clip = src.reshape(-1, 3).max(0)
    m &= (srcb < 0.97 * clip).all(-1) & (srcb.min(-1) > 0)
    held = (((yy // block) + (xx // block)) % 2) == 1
    info = {"aligned": bool(ok), "work_size": [W, H], "pixels_used": int(m.sum()),
            "homography": np.round(warp, 5).tolist()}
    return srcb[m], dstb[m], held[m], info, (src, dst)


def bin_samples(src, dst, coord, bins=32, min_count=3):
    """Mean src and dst per cell of a bins^3 grid over `coord` (src mapped to 0..1)."""
    c = np.clip(coord, 0, 1 - 1e-9)
    idx = (c * bins).astype(int)
    key = idx[:, 0] + bins * idx[:, 1] + bins * bins * idx[:, 2]
    uniq, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
    sm = np.zeros((len(uniq), 3))
    dm = np.zeros((len(uniq), 3))
    np.add.at(sm, inv, src)
    np.add.at(dm, inv, dst)
    keep = cnt >= min_count
    return sm[keep] / cnt[keep, None], dm[keep] / cnt[keep, None], cnt[keep].astype(np.float64)


def _rel_chroma(xs, in_space):
    lab = spaces.oklab(np.maximum(spaces.to_lin709(xs, in_space), 0))
    return np.hypot(lab[:, 1], lab[:, 2]) / np.maximum(lab[:, 0], 1e-3)


def _fit_binned(xs, ys, cnt, in_space, out):
    rel = _rel_chroma(xs, in_space)
    w = np.sqrt(cnt)
    tone_w = w * np.exp(-(rel / 0.06) ** 2)
    keep = tone_w > 1e-3 * tone_w.max()
    p = fit.fit(in_space, out, xs, ys, w, xs[keep], ys[keep], tone_w[keep])
    return p, keep


def _finish(p, name, outdir, xs, ys, cnt, test_x, test_y, sources, previews):
    p["fit_error"] = {"held-out pixels": fit.errors(p, test_x, test_y), "fitted bins": fit.errors(p, xs, ys)}
    p["source"] = {"file": ", ".join(Path(s).name for s in sources), "pairs": len(sources), "bins": int(len(xs)),
                   "pixels": int(cnt.sum()), "output_range": [0.0, 1.0]}
    p["skin_family"] = report.skin_family(lambda c: model.evaluate(p, c), p["in"], p["out"])
    (outdir / "params.json").write_text(json.dumps(p, indent=1) + "\n")
    rows, before, after = report.patch_table(lambda c: model.evaluate(p, c), p["in"], p["out"])
    rel = _rel_chroma(xs, p["in"])
    grey = rel < 0.04 if (rel < 0.04).sum() >= 20 else rel <= np.sort(rel)[min(19, len(rel) - 1)]
    xt, yt = xs[grey], ys[grey]
    e = model.exposure(spaces.luminance(spaces.to_lin709(xt, p["in"])))
    o = np.argsort(e)
    report.plot_tone(outdir / "tone.png", p, e[o], fit.y_code(yt[o], p["out"]), yt[o])
    report.plot_hue(outdir / "hue_shift.png", outdir / "hue_sat.png", p)
    ramp_in = spaces.from_lin709(np.repeat((0.18 * 2.0 ** np.linspace(-6, 3, 256))[:, None], 3, 1), p["in"])
    report.plot_ramp(outdir / "ramp.png", ramp_in, model.evaluate(p, ramp_in), p["out"])
    report.plot_patches(outdir / "patches.png", before, np.clip(after, 0, 1))
    de = spaces.delta_e2000(spaces.cielab(spaces.to_lin709(np.clip(test_y, 0, 1), p["out"])),
                            spaces.cielab(spaces.to_lin709(np.clip(model.evaluate(p, test_x), 0, 1), p["out"])))
    report.plot_hist(outdir / "residual.png", de, "What the numbers miss: Delta E 2000, held-out pixels")
    images = {"tone curve": "tone.png", "hue vs hue": "hue_shift.png", "hue vs sat": "hue_sat.png",
              "neutral ramp": "ramp.png", "patches": "patches.png", "held-out error": "residual.png"}
    for i, (src, dst) in enumerate(previews):
        side = np.concatenate([np.clip(spaces.from_lin709(spaces.to_lin709(src, p["in"]), "srgb"), 0, 1),
                               np.clip(model.evaluate(p, src), 0, 1), dst], 1)
        Image.fromarray((side * 255 + 0.5).astype(np.uint8)).save(outdir / f"compare-{i + 1}.jpg", quality=88)
        images[f"pair {i + 1}: plain | model | target"] = f"compare-{i + 1}.jpg"
    notes = ("Fitted from image pairs: there is no residual, so `build` gives the model alone. The tone and colour "
             "numbers only know the colours these images contain; knots far from them hold their neighbours' values.")
    if p["in"] == "lin709":
        notes += (" The RAW develop's exposure scale is arbitrary, so `mid` and the patch lightness columns carry the "
                  "camera's exposure offset as well as its tone; read hue, chroma and contrast, not absolute lightness.")
    nt = [[f"{e[i]:+.1f} stops", f"{xt[o][k][0]:.4f}", " ".join(f"{v:.3f}" for v in yt[o][k]),
           " ".join(f"{v:.3f}" for v in model.evaluate(p, xt[o][k]))]
          for k in np.unique(np.linspace(0, len(o) - 1, 7).astype(int)) for i in [o[k]]]
    (outdir / "report.md").write_text(report.markdown(p, f"{name}: fitted from pairs", rows, nt, images, notes))
    return p


def fit_raw_jpeg(pairs, outdir, name):
    xs_all, ys_all, cnt_all, tx, ty, previews = [], [], [], [], [], []
    with tempfile.TemporaryDirectory() as tmp:
        for raw, jpg in pairs:
            dst = read_image(jpg)
            src = develop_raw(raw, tmp, px=2 * WORK_PX)
            if (src.shape[0] > src.shape[1]) != (dst.shape[0] > dst.shape[1]):
                raise ValueError(f"{raw} and {jpg} differ in orientation")
            dst = _resize(dst, src.shape[1], src.shape[0])
            # match brightness roughly for alignment only
            prev = np.clip(spaces.srgb_encode(src * (0.18 / max(np.median(spaces.luminance(src)), 1e-6))), 0, 1)
            s, d, held, info, (sw, dw) = sample_pair(src, dst, prev)
            if not info["aligned"]:
                print(f"warning: {Path(jpg).name}: alignment did not converge; using the unwarped frame")
            xs, ys, cnt = bin_samples(s[~held], d[~held], spaces.di_encode(np.maximum(s[~held], 0)) / 0.8)
            xs_all.append(xs), ys_all.append(ys), cnt_all.append(cnt)
            tx.append(s[held]), ty.append(d[held])
            previews.append((sw[::2, ::2], dw[::2, ::2]))
    xs, ys, cnt = np.concatenate(xs_all), np.concatenate(ys_all), np.concatenate(cnt_all)
    p, _ = _fit_binned(xs, ys, cnt, "lin709", "srgb")
    tx, ty = np.concatenate(tx), np.concatenate(ty)
    pick = np.random.default_rng(0).choice(len(tx), min(len(tx), 200000), replace=False)
    return _finish(p, name, outdir, xs, ys, cnt, tx[pick], ty[pick], [r for r, _ in pairs], previews)


def fit_frames(pairs, in_space, out, outdir, name):
    spaces.check_space(in_space)
    xs_all, ys_all, cnt_all, tx, ty, previews = [], [], [], [], [], []
    for srcp, dstp in pairs:
        src, dst = read_image(srcp), read_image(dstp)
        src = _resize(src, dst.shape[1], dst.shape[0])
        s, d, held, info, (sw, dw) = sample_pair(src, dst, src)
        xs, ys, cnt = bin_samples(s[~held], d[~held], s[~held])
        xs_all.append(xs), ys_all.append(ys), cnt_all.append(cnt)
        tx.append(s[held]), ty.append(d[held])
        previews.append((sw[::2, ::2], dw[::2, ::2]))
    xs, ys, cnt = np.concatenate(xs_all), np.concatenate(ys_all), np.concatenate(cnt_all)
    p, _ = _fit_binned(xs, ys, cnt, in_space, out)
    tx, ty = np.concatenate(tx), np.concatenate(ty)
    pick = np.random.default_rng(0).choice(len(tx), min(len(tx), 200000), replace=False)
    return _finish(p, name, outdir, xs, ys, cnt, tx[pick], ty[pick], [s for s, _ in pairs], previews)
