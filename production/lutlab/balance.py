"""lutlab.balance: per-shot exposure and white balance for video, onto a neutral reference or a skin target.

    python3 -m lutlab balance SHOT [SHOT ...] --camera pyxis|--space vlog [--frames 8]
            [--neutral-box X0 Y0 X1 Y1] [--face X Y W H] [--wb-from SHOT|FILE.json:SHOT]
            [--target-lab L a b | --targets FILE.json] [--target-L 64] [--hero SHOT] [--out DIR] [--name NAME]

A shot is a video file or a folder of frames (PNG, TIFF, JPEG), read as code values in the camera's space
(lutlab.spaces: vlog, bmdfilm5, logc3, logc4, di). N frames are sampled evenly through the middle 80% of a
clip, or evenly through the sorted folder.

The house rule (the D1 decision for stills): neutralise on a neutral reference, set exposure on the skin,
never push skin hue to a number. The default mode, neutral, solves
    ev      exposure, stops           the cheek's L* onto --target-L (64)
    mired   white balance shift       the neutral's a*, b* onto 0, 0
    tint    green-magenta shift       (positive is toward magenta)
The neutral is --neutral-box in every frame (normalised x0 y0 x1 y1), else the garment or card below the
face (rpstills.skin.neutral_mask). A shot with none is refused (status no_neutral) unless --wb-from names a
shot in the same light: that shot is solved first, and this one takes its mired and tint and solves EV alone
onto the cheek L*. The reference may also be a converged neutral row of an earlier balance.json
(--wb-from FILE.json:SHOT). Skin hue is left where the complexion puts it.

Target mode (--target-lab L a b, or --targets FILE.json) is for matching shots to one hero look: the cheek's
whole Lab onto the target. FILE.json is {"default": [L, a, b], "<shot name>": [L, a, b]}; a shot takes its
own entry, else "default". (A `perceive` loader belongs in TARGET_SOURCES.)

The face comes from Vision (macOS) on each sampled frame, or --face X Y W H (normalised, one box for the
shot, read as a box sample). Frames with no face are left out and counted.

The solve is rpstills.develop.solve over (ev, mired, tint). The correction is a gain per channel in the
camera's *linear* channels: 2^ev, then the white-balance gains from a Planckian white (Kim et al. 2002) mired
away from 6500 K, then the tint gain on green. The skin and the neutral are read as Lab of the corrected
linear picture rendered as plain sRGB, no tone curve, so "L* 64" is a number to compare shots by, not a
look. Measured medians across the sampled frames.

Output (never inside a git tree): balance.json (a row per shot and the set table), balance.md, and per shot
NAME.cc (an ASC CDL in the camera's code values, for Resolve) and NAME.cube (the same CDL baked, for ffmpeg
lut3d or Resolve). A gain in linear light is an offset in a log curve, so the CDL is a least-squares fit of
slope and offset per channel over the camera's range; row.cdl_error is its CIEDE2000 against the exact
correction (mean, max). Rows: ev, mired, temp_k (the Kelvin the correction moves a 6500 K white to), tint,
before and after (skin Lab, neutral a* b*, delta_e to the target), status converged|pinned|not_converged|
no_neutral|no_face|unreadable, cdl, cdl_error. Exit status 1 when any shot is not converged.

The set table is rpstills audit's: every shot's skin after the correction against the set's median (or
--hero), d_E00, d_L, d_hue_deg, before and after."""

import json
import os
import subprocess
from pathlib import Path

import numpy as np

from . import cdl as cdlmod, cube, guard, spaces

SOLVE_W = 640                           # frames are solved at this width
MIRED_REF = 1e6 / 6500
BOUNDS = {"ev": (-3.0, 3.0), "mired": (-100.0, 100.0), "tint": (-40.0, 40.0)}
IMAGE_EXTS = (".png", ".tif", ".tiff", ".jpg", ".jpeg")
VIDEO_MARGIN = 0.10
TARGET_L = 64.0
NO_NEUTRAL = ("no neutral reference in the shot: shoot a grey card, pass --neutral-box, or --wb-from SHOT "
              "(a shot in the same light)")


class Refused(ValueError):
    """A shot that cannot be balanced; args[0] is the reason and .status the row's status."""
    def __init__(self, msg, status):
        super().__init__(msg)
        self.status = status


def load_targets(path):
    """{name: [L, a, b]} from a plain JSON file; "default" is the fallback. ValueError when malformed."""
    with open(path) as f:
        doc = json.load(f)
    if not isinstance(doc, dict) or any(not (isinstance(v, list) and len(v) == 3) for v in doc.values()):
        raise ValueError(f"{path}: want {{\"default\": [L, a, b], \"<shot>\": [L, a, b]}}")
    return doc


TARGET_SOURCES = {"json": load_targets}  # perceive: add its loader here, same {name: [L, a, b]} result


# ---------------------------------------------------------------- the correction

def _planck_rgb(mired):
    """Linear Rec.709 RGB of the Planckian white at 1e6 / mired kelvin (Kim et al. 2002 xy), green = 1."""
    T = float(np.clip(1e6 / mired, 1667.0, 25000.0))
    xc = (-0.2661239e9 / T ** 3 - 0.2343589e6 / T ** 2 + 0.8776956e3 / T + 0.179910 if T < 4000
          else -3.0258469e9 / T ** 3 + 2.1070379e6 / T ** 2 + 0.2226347e3 / T + 0.240390)
    yc = (-1.1063814 * xc ** 3 - 1.34811020 * xc ** 2 + 2.18555832 * xc - 0.20219683 if T < 2222
          else -0.9549476 * xc ** 3 - 1.37418593 * xc ** 2 + 2.09137015 * xc - 0.16748867 if T < 4000
          else 3.0817580 * xc ** 3 - 5.87338670 * xc ** 2 + 3.75112997 * xc - 0.37001483)
    rgb = np.linalg.solve(spaces.XYZ["709"], [xc / yc, 1.0, (1 - xc - yc) / yc])
    return rgb / rgb[1]


def gains(ev, mired, tint):
    """Per-channel gain on the camera's linear channels."""
    wb = _planck_rgb(MIRED_REF + mired) / _planck_rgb(MIRED_REF)
    return 2.0 ** ev * wb * np.array([1.0, 2.0 ** (-tint / 50), 1.0])


def _dec(space):
    return spaces.SPACES[spaces.check_space(space)][0]


def _enc(space):
    return spaces.SPACES[space][1]


def _to709(cam_lin, space):
    gamut = spaces.SPACES[space][2]
    return cam_lin if gamut == "709" else spaces._apply(spaces.gamut_matrix(gamut, "709"), cam_lin)


def fit_cdl(g, space):
    """(cdl, {"mean", "max"}): slope and offset per channel in the camera's code values, fitted to the gain g
    over a geometric ramp of linear levels; the error is CIEDE2000 on a grey ramp and a skin tone."""
    dec, enc = _dec(space), _enc(space)
    lin = np.geomspace(0.004, 0.8, 40)
    fit = [np.polyfit(enc(lin), enc(lin * g[k]), 1) for k in range(3)]
    c = dict(cdlmod.IDENTITY, slope=np.array([f[0] for f in fit]), offset=np.array([f[1] for f in fit]))
    skin709 = np.array([0.30, 0.20, 0.15])
    test709 = np.concatenate([np.outer(np.geomspace(0.02, 0.6, 12), np.ones(3)), np.outer([0.5, 1, 2], skin709)])
    codes = np.clip(spaces.from_lin709(test709, space), 0, 1)
    exact = enc(dec(codes) * g)
    lab = lambda x: spaces.cielab(np.maximum(spaces.to_lin709(np.clip(x, 0, 1), space), 0))
    de = spaces.delta_e2000(lab(exact), lab(cdlmod.cdl_apply(codes, c)))
    return c, {"mean": round(float(de.mean()), 3), "max": round(float(de.max()), 3)}


# ---------------------------------------------------------------- frames

def _duration(clip):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(clip)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        raise Refused(f"ffprobe could not read {clip}: {r.stderr.strip()[-200:]}", "unreadable")


def read_frames(shot, n):
    """[(label, float RGB code values 0..1 at SOLVE_W wide)] for a video file or a folder of frames."""
    import cv2
    from . import verify
    from .pairs import read_image
    p = Path(shot)
    if p.is_dir():
        files = sorted(f for f in p.iterdir() if f.suffix.lower() in IMAGE_EXTS)
        if not files:
            raise Refused(f"{shot}: no PNG, TIFF or JPEG frames", "unreadable")
        pick = [files[i] for i in sorted({int(round(v)) for v in np.linspace(0, len(files) - 1, min(n, len(files)))})]
        imgs = [(f.name, read_image(f)) for f in pick]
    elif p.is_file():
        try:
            w, h = verify.probe_size(p)
            d = _duration(p)
            ts = np.linspace(d * VIDEO_MARGIN, d * (1 - VIDEO_MARGIN), n)
            imgs = [(f"t{t:.2f}s", verify.decode_frame(p, float(t), SOLVE_W, int(round(SOLVE_W * h / w)))) for t in ts]
        except (RuntimeError, OSError, ValueError) as e:
            raise Refused(f"{shot}: {e}", "unreadable")
    else:
        raise Refused(f"{shot}: no such file or folder", "unreadable")
    out = []
    for label, im in imgs:
        if im.shape[1] != SOLVE_W:
            im = cv2.resize(im, (SOLVE_W, int(round(im.shape[0] * SOLVE_W / im.shape[1]))), interpolation=cv2.INTER_AREA)
        out.append((label, np.asarray(im, np.float64)))
    return out


def detect_faces(frames, space):
    """Vision's largest face per frame ([face or None]), on each frame as plain sRGB (a flat log frame hides
    faces); Refused when Vision cannot run."""
    import tempfile
    import cv2
    from rpstills import vision
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for i, (_, im) in enumerate(frames):
            paths.append(os.path.join(tmp, f"{i}.png"))
            disp = spaces.srgb_encode(np.clip(_to709(_dec(space)(np.clip(im, 0, 1)), space), 0, 1))
            cv2.imwrite(paths[-1], np.round(disp[..., ::-1] * 255).astype(np.uint8))
        det = vision.faces(paths, landmarks=True)
        if det is None:
            raise Refused("the Vision helper cannot run (it needs macOS and swiftc): pass --face X Y W H", "no_face")
        found = [max((det.get(p) or {}).get("faces") or [], key=lambda f: f["w"] * f["h"], default=None) for p in paths]
    return found


# ---------------------------------------------------------------- measuring and solving

def _srgb(cam_lin, g, space):
    return spaces.srgb_encode(np.clip(_to709(cam_lin * g, space), 0, 1))


def prepare(frames, space, face, neutral_box, faces=detect_faces, need_neutral=True):
    """Per usable frame {"label", "lin" (camera linear), "face_px", "sample", "mask"} and the labels left out.
    Refused (no_face / no_neutral) when no frame is usable; mask is None when need_neutral is False."""
    from rpstills import measure, skin
    dets = [face] * len(frames) if face else faces(frames, space)
    dec, used, left = _dec(space), [], []
    for (label, code), f in zip(frames, dets):
        if f is None:
            left.append(label)
            continue
        lin = dec(np.clip(code, 0, 1))
        H, W = lin.shape[:2]
        px = skin.to_pixels(f, W, H)
        mask = None
        if need_neutral:
            if neutral_box:
                mask = np.zeros((H, W), bool)
                mask[int(round(neutral_box[1] * H)):int(round(neutral_box[3] * H)),
                     int(round(neutral_box[0] * W)):int(round(neutral_box[2] * W))] = True
            else:  # taken once on the shot as it is; grey-world would take a coloured shirt for grey
                mask = skin.neutral_mask(_srgb(lin, np.ones(3), space), px)
            if mask is None or not mask.any():
                left.append(label)
                continue
        used.append({"label": label, "lin": lin, "face_px": px, "mask": mask,
                     "sample": "cheek" if all((f.get("landmarks") or {}).get(k) for k in ("leftEye", "rightEye", "outerLips")) else "box"})
    if not used:
        if need_neutral and any(d is not None for d in dets):
            raise Refused(NO_NEUTRAL, "no_neutral")
        raise Refused("no face in any sampled frame", "no_face")
    return used, left


def _headroom(u, x, space):
    """The EV at which the neutral's brightest 1% just stays under the clip: a white shirt far brighter than the
    cheek clips at the cheek's exposure, and the pixels left (the stripes, the shade) are not the neutral. White
    balance is a ratio, so the neutral is read at this EV, never above x's own."""
    from rpstills import measure
    m = _to709(u["lin"][u["mask"]] * gains(0.0, x[1], x[2]), space)
    peak = float(np.percentile(m.max(-1), 99))
    return np.log2(0.98 * float(spaces.srgb_decode(measure.CLIP_AT)) / peak) if peak > 0 else np.inf


def measurer(used, space):
    """x -> the (cheek Lab, neutral Lab or None) medians over the frames at x = (ev, mired, tint)."""
    from rpstills import measure
    from rpstills.develop import Unmeasurable

    def at(x):
        g = gains(*x)
        skin_labs, neu = [], []
        for u in used:
            rgb = _srgb(u["lin"], g, space)
            s = measure.skin_lab(rgb, u["face_px"], u["sample"])
            skin_labs.append([s["L"], s["a"], s["b"]])
            if u["mask"] is not None:
                cap = _headroom(u, x, space)
                n = measure.neutral_lab(rgb if cap >= x[0] else _srgb(u["lin"], gains(cap, x[1], x[2]), space), u["mask"])
                if n is None:
                    raise Unmeasurable("the neutral reference is clipped at every pixel")
                neu.append([n["L"], n["a"], n["b"]])
        return np.median(skin_labs, 0), (np.median(neu, 0) if neu else None)
    return at


def wb_row(row, name, space):
    """{"mired", "tint", "from"} from a converged neutral row; Refused otherwise."""
    if not row or (row.get("mode"), row.get("status"), row.get("space")) != ("neutral", "converged", space):
        raise Refused(f"--wb-from {name}: not a converged neutral-mode row in {space}", "no_neutral")
    return {"mired": float(row["mired"]), "tint": float(row["tint"]), "from": name}


def balance_shot(name, frames, space, *, face=None, neutral_box=None, target_lab=None, target_L=TARGET_L,
                 wb=None, faces=detect_faces):
    """The row for one shot (frames from read_frames). Raises Refused for a shot that cannot be balanced."""
    from rpstills import develop
    mode = "target" if target_lab is not None else "neutral"
    target = np.array(target_lab if mode == "target" else [target_L, 0.0, 0.0], float)
    used, left = prepare(frames, space, face, neutral_box, faces, need_neutral=mode == "neutral" and not wb)
    at = measurer(used, space)

    def vec(skin_lab, neu):
        if mode == "target":
            return skin_lab
        return np.array([skin_lab[0], *(neu[1:] if neu is not None else target[1:])])

    def measure_fn(x):
        return vec(*at(x))

    x0 = [0.0, 0.0, 0.0]
    bounds = [BOUNDS["ev"], BOUNDS["mired"], BOUNDS["tint"]]
    free = ["ev"] if wb else list(develop.AXES)
    if wb:
        x0[1:] = wb["mired"], wb["tint"]
        bounds[1:] = [(wb["mired"],) * 2, (wb["tint"],) * 2]
    s0, n0 = at(x0)
    res = develop.solve(measure_fn, x0, bounds, target, free, tol=develop.AIM)
    x = np.array(res["x"])
    s1, n1 = at(x)
    out = vec(s1, n1)
    st = develop.status(out, target, free, res["free"], res["pinned"])
    g = gains(*x)
    cdl, err = fit_cdl(g, space)
    lab = lambda v: [round(float(c), 2) for c in v]
    side = lambda s, n: {"skin": lab(s), "neutral_ab": lab(n[1:]) if n is not None else None}
    return {"shot": name, "mode": mode, "space": space, "status": st, "converged": st == "converged",
            "ev": round(float(x[0]), 3), "mired": round(float(x[1]), 2), "temp_k": round(1e6 / (MIRED_REF + x[1])),
            "tint": round(float(x[2]), 2), "gains": [round(float(v), 5) for v in g],
            "before": dict(side(s0, n0), delta_e=round(float(np.linalg.norm(vec(s0, n0) - target)), 3)),
            "after": dict(side(s1, n1), delta_e=round(float(np.linalg.norm(out - target)), 3)),
            "target": lab(target) if not wb else [lab(target)[0], None, None],
            "pinned": res["pinned"], "iterations": res["iterations"],
            "frames": {"used": [u["label"] for u in used], "left_out": left},
            "wb_from": wb["from"] if wb else None,
            "cdl": {k: (v.tolist() if hasattr(v, "tolist") else v) for k, v in cdl.items()}, "cdl_error": err}


def refused_row(name, space, e):
    return {"shot": name, "space": space, "status": e.status, "converged": False, "error": str(e)}


# ---------------------------------------------------------------- the set

def set_table(rows, hero=None):
    """{"reference", "shots": [{shot, d_E00, d_E00_before, d_L, d_hue_deg, neutral_ab}], "spread"} (audit's)."""
    from rpstills import audit
    done = [r for r in rows if r.get("after")]
    view = lambda r, side: {"file": r["shot"], "skin_L": r[side]["skin"][0], "skin_a": r[side]["skin"][1],
                            "skin_b": r[side]["skin"][2]}
    after = [view(r, "after") for r in done]
    ref = audit.reference(after, hero) if after else None
    if not ref:
        return {"reference": None, "shots": [], "spread": {}}
    shots = []
    for r, a in zip(done, after):
        dev = audit.deviations(a, ref)
        shots.append({"shot": r["shot"], "d_E00": dev["d_E00"], "d_L": dev["d_L"], "d_hue_deg": dev["d_hue_deg"],
                      "d_E00_before": audit.deviations(view(r, "before"), ref)["d_E00"],
                      "neutral_ab": r["after"]["neutral_ab"]})
    return {"reference": ref, "shots": shots, "spread": audit.spread(after)}


def table_md(rows, st):
    lines = ["| shot | status | ev | mired | temp K | tint | skin L* a* b* after | neutral a* b* | dE00 to set | before |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    dev = {s["shot"]: s for s in st["shots"]}
    for r in rows:
        if not r.get("after"):
            lines.append(f"| {r['shot']} | {r['status']} | | | | | | | {r.get('error', '')} | |")
            continue
        d = dev.get(r["shot"], {})
        n = r["after"]["neutral_ab"]
        lines.append(f"| {r['shot']} | {r['status']} | {r['ev']:+.2f} | {r['mired']:+.1f} | {r['temp_k']} | {r['tint']:+.1f} | "
                     f"{' '.join(f'{v:.1f}' for v in r['after']['skin'])} | {' '.join(f'{v:.1f}' for v in n) if n else 'wb-from'} | "
                     f"{d.get('d_E00')} | {d.get('d_E00_before')} |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- the run

def stem(shot):
    return "".join(ch if ch.isalnum() or ch in "-_ ." else "_" for ch in shot)


def write_outputs(rows, st, out, name, space, overwrite=False):
    """balance.json, balance.md, and NAME.cc / NAME.cube for each balanced shot. FileExistsError, before
    anything is written, when one of them exists."""
    out = Path(out)
    doc = {"tool": "lutlab balance", "space": space, "shots": rows, "set": st}
    names = ["balance.json", "balance.md"] + [f"{stem(r['shot'])}.{e}" for r in rows if r.get("cdl") for e in ("cc", "cube")]
    clash = [str(out / f) for f in names if (out / f).exists()]
    if clash and not overwrite:
        raise FileExistsError(", ".join(clash))
    files = [cube.write_text(str(out / "balance.json"), json.dumps(doc, indent=1) + "\n", overwrite),
             cube.write_text(str(out / "balance.md"), f"# {name}: balance\n\n" + table_md(rows, st), overwrite)]
    grid = cube.lattice(33)
    for r in rows:
        if r.get("cdl"):
            c = {k: np.asarray(v) if k != "sat" else v for k, v in r["cdl"].items()}
            sm = stem(r["shot"])
            files.append(cube.write_text(str(out / f"{sm}.cc"), cdlmod.cdl_xml(c, sm), overwrite))
            table = np.clip(cdlmod.cdl_apply(grid.reshape(-1, 3), c), 0, 1).reshape(grid.shape)
            files.append(cube.write_cube(str(out / f"{sm}.cube"), table, f"{sm} balance", overwrite=overwrite))
    return files


def add_arguments(p):
    p.add_argument("shots", nargs="+", help="video files or folders of frames")
    p.add_argument("--camera")
    p.add_argument("--space")
    p.add_argument("--frames", type=int, default=8)
    p.add_argument("--neutral-box", nargs=4, type=float, metavar=("X0", "Y0", "X1", "Y1"))
    p.add_argument("--face", nargs=4, type=float, metavar=("X", "Y", "W", "H"))
    p.add_argument("--wb-from", metavar="SHOT|FILE.json:SHOT")
    p.add_argument("--target-lab", nargs=3, type=float, metavar=("L", "A", "B"))
    p.add_argument("--targets", metavar="FILE.json")
    p.add_argument("--target-L", type=float, default=TARGET_L)
    p.add_argument("--hero", metavar="SHOT")
    p.add_argument("--out")
    p.add_argument("--name", default="balance")
    p.add_argument("--overwrite", action="store_true")


def run(a, ap):
    if bool(a.camera) == bool(a.space):
        ap.error("give --camera or --space")
    space = spaces.target(a.camera)[0] if a.camera else spaces.check_space(a.space)
    if a.target_lab and a.targets:
        ap.error("give --target-lab or --targets, not both")
    targets = load_targets(a.targets) if a.targets else {}
    names = [Path(s).stem if Path(s).is_file() else Path(s).name for s in a.shots]
    if len({stem(n) for n in names}) != len(names):
        ap.error("two shots share a name; rename one")
    face = ({"x": a.face[0], "y": a.face[1], "w": a.face[2], "h": a.face[3]} if a.face else None)
    out = guard.out_dir(a.out, a.name)
    wb_name, wb_file, wb, wb_err = None, None, None, None
    if a.wb_from in names:
        wb_name = a.wb_from
    elif a.wb_from:
        wb_file, _, wb_name = a.wb_from.rpartition(":")
        try:
            with open(wb_file) as f:
                prior = json.load(f)["shots"]
            wb = wb_row(next((r for r in prior if r["shot"] == wb_name), None), wb_name, space)
        except (OSError, ValueError, KeyError, Refused) as e:
            wb_err = e if isinstance(e, Refused) else Refused(f"--wb-from {a.wb_from}: {e}", "no_neutral")
    rows = {}
    for name, shot in sorted(zip(names, a.shots), key=lambda t: t[0] != wb_name):  # an in-run reference first
        if wb_err and name != wb_name:
            rows[name] = refused_row(name, space, wb_err)
            continue
        try:
            tl = a.target_lab or targets.get(name) or targets.get("default")
            if targets and tl is None:
                raise Refused(f"{a.targets}: no entry for {name} and no default", "no_target")
            rows[name] = balance_shot(name, read_frames(shot, a.frames), space, face=face, neutral_box=a.neutral_box,
                                      target_lab=tl, target_L=a.target_L, wb=None if wb_file is None and name == wb_name else wb)
        except Refused as e:
            rows[name] = refused_row(name, space, e)
        except (RuntimeError, ValueError) as e:
            rows[name] = refused_row(name, space, Refused(str(e), "unreadable"))
        if name == wb_name and not wb_file:  # the reference's own result, or its failure, goes to the rest
            try:
                wb = wb_row(rows[name], name, space)
            except Refused as e:
                wb_err = Refused(f"--wb-from {name}: {rows[name].get('error') or rows[name]['status']}", "no_neutral")
    ordered = [rows[n] for n in names]
    try:
        st = set_table(ordered, a.hero)
        done = write_outputs(ordered, st, out, a.name, space, a.overwrite)
    except (ValueError, FileExistsError) as e:
        ap.error(str(e))
    for f in done:
        print(f)
    print(table_md(ordered, st))
    return 0 if all(r["converged"] for r in ordered) else 1
