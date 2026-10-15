"""lutlab.report: plots (Pillow only) and the Markdown report for a fitted look."""

import json

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import model, spaces

# ColorChecker 24, sRGB 8-bit (X-Rite published values), in chart order.
PATCHES = [
    ("dark skin", (115, 82, 68)), ("light skin", (194, 150, 130)), ("blue sky", (98, 122, 157)),
    ("foliage", (87, 108, 67)), ("blue flower", (133, 128, 177)), ("bluish green", (103, 189, 170)),
    ("orange", (214, 126, 44)), ("purplish blue", (80, 91, 166)), ("moderate red", (193, 90, 99)),
    ("purple", (94, 60, 108)), ("yellow green", (157, 188, 64)), ("orange yellow", (224, 163, 46)),
    ("blue", (56, 61, 150)), ("green", (70, 148, 73)), ("red", (175, 54, 60)),
    ("yellow", (231, 199, 31)), ("magenta", (187, 86, 149)), ("cyan", (8, 133, 161)),
    ("white", (243, 243, 242)), ("neutral 8", (200, 200, 200)), ("neutral 6.5", (160, 160, 160)),
    ("neutral 5", (122, 122, 121)), ("neutral 3.5", (85, 85, 85)), ("black", (52, 52, 52)),
]
SKIN = (0, 1)
NAMED_HUES = [(29, "red"), (55, "orange"), (85, "amber"), (110, "yellow"), (142, "green"), (170, "green-cyan"),
              (195, "cyan"), (230, "azure"), (264, "blue"), (300, "violet"), (328, "magenta")]
DISPLAY = ("r709", "srgb")


def hue_name(deg):
    d = [(abs((deg - h + 180) % 360 - 180), n) for h, n in NAMED_HUES]
    return min(d)[1]


def warmth(deg):
    deg %= 360
    if 35 <= deg < 125:
        return "warm"
    if 165 <= deg < 285:
        return "cool"
    if 125 <= deg < 165:
        return "green"
    return "magenta"


# --- patches through a look --------------------------------------------------

def through(apply, lin, in_space, out):
    """Colours given as plain sRGB light (linear Rec.709, scene reflectance) through
    the look, with a grey of the same input luminance beside each one.

    A display-referred look (r709/srgb in) is fed the sRGB code values, and its
    'before' is those codes as its own input space displays them, so an identity
    LUT reads as no change. A log look is fed the light encoded in its space, and
    its 'before' is the plain sRGB rendering. Returns OKLab before, grey before,
    after, grey after, and before/after as output code values for swatches."""
    lin = np.asarray(lin, dtype=np.float64)
    if in_space in DISPLAY:
        x = spaces.srgb_encode(lin)
        before = spaces.to_lin709(x, in_space)
    else:
        x = spaces.from_lin709(lin, in_space)
        before = lin
    grey = np.repeat(spaces.luminance(before)[..., None], 3, -1)
    xg = spaces.from_lin709(grey, in_space)
    after, after_g = np.clip(apply(x), 0, 1), np.clip(apply(xg), 0, 1)
    ok = lambda v: spaces.oklab(np.maximum(v, 0))
    return (ok(before), ok(grey), ok(spaces.to_lin709(after, out)), ok(spaces.to_lin709(after_g, out)),
            np.clip(spaces.from_lin709(before, out), 0, 1), after)


def patch_table(apply, in_space, out):
    """apply: input codes -> output codes. Rows of OKLab before/after and a density number:
    how much darker the colour lands than a grey of the same input luminance, against
    the same gap before the look (negative = denser)."""
    lin = spaces.srgb_decode(np.array([c for _, c in PATCHES], dtype=np.float64) / 255)
    lab_b, lab_bg, lab_a, lab_ag, before, after = through(apply, lin, in_space, out)
    de = spaces.delta_e2000(spaces.cielab(spaces.oklab_to_lin709(lab_b)), spaces.cielab(spaces.oklab_to_lin709(lab_a)))
    rows = []
    for i, (name, _) in enumerate(PATCHES):
        lb, cb, hb = spaces.lch(lab_b[i])
        la, ca, ha = spaces.lch(lab_a[i])
        rows.append({
            "patch": name, "L_before": 100 * lb, "L_after": 100 * la, "C_before": 100 * cb, "C_after": 100 * ca,
            "h_before": np.degrees(hb) % 360, "h_after": np.degrees(ha) % 360,
            "dh": float(np.degrees(np.angle(np.exp(1j * (ha - hb))))) if min(cb, ca) > 0.01 else 0.0,
            "density": 100 * ((lab_a[i, 0] - lab_ag[i, 0]) - (lab_b[i, 0] - lab_bg[i, 0])),
            "de2000": float(de[i]),
        })
    return rows, before, after


def skin_family(apply, in_space, out):
    """Many skin tones, not two patches: OKLab hue 45-70 deg, chroma 0.03-0.10,
    lightness 0.45-0.85 as plain sRGB, through the look. Medians of the hue
    rotation, chroma ratio, lightness change and density (as in patch_table)."""
    L, C, H = np.meshgrid(np.linspace(0.45, 0.85, 9), np.linspace(0.03, 0.10, 8), np.radians(np.linspace(45, 70, 6)),
                          indexing="ij")
    lab = np.stack([L, C * np.cos(H), C * np.sin(H)], -1).reshape(-1, 3)
    lab_b, lab_bg, lab_a, lab_ag, _, _ = through(apply, np.clip(spaces.oklab_to_lin709(lab), 0, 1), in_space, out)
    lb, cb, hb = spaces.lch(lab_b)
    la, ca, ha = spaces.lch(lab_a)
    return {"samples": int(len(lab)),
            "hue_shift_deg": round(float(np.median(np.degrees(np.angle(np.exp(1j * (ha - hb)))))), 1),
            "chroma_ratio": round(float(np.median(ca / cb)), 3),
            "lightness_change": round(float(np.median(100 * (la - lb))), 1),
            "density": round(float(np.median(100 * ((lab_a[:, 0] - lab_ag[:, 0]) - (lab_b[:, 0] - lab_bg[:, 0])))), 1)}


# --- drawing -----------------------------------------------------------------

def _font(size=14):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


class Plot:
    def __init__(self, xr, yr, title, xlabel, ylabel, w=900, h=420, margin=(70, 36, 20, 50)):
        self.im = Image.new("RGB", (w, h), (250, 250, 248))
        self.d = ImageDraw.Draw(self.im)
        self.xr, self.yr, self.m, self.w, self.h = xr, yr, margin, w, h
        l, t, r, b = margin
        self.d.rectangle([l, t, w - r, h - b], outline=(120, 120, 120))
        f = _font(15)
        self.d.text((l, 8), title, fill=(20, 20, 20), font=f)
        self.d.text((w // 2 - 40, h - 22), xlabel, fill=(60, 60, 60), font=_font(13))
        self.d.text((6, t - 20), ylabel, fill=(60, 60, 60), font=_font(13))

    def px(self, x, y):
        l, t, r, b = self.m
        fx = (np.asarray(x, float) - self.xr[0]) / (self.xr[1] - self.xr[0])
        fy = (np.asarray(y, float) - self.yr[0]) / (self.yr[1] - self.yr[0])
        return l + fx * (self.w - l - r), self.h - b - fy * (self.h - t - b)

    def ticks(self, xs, ys, fmt="{:g}"):
        f = _font(12)
        for x in xs:
            X, Y = self.px(x, self.yr[0])
            self.d.line([X, Y, X, Y + 4], fill=(120, 120, 120))
            self.d.text((X - 10, Y + 6), fmt.format(x), fill=(80, 80, 80), font=f)
        for y in ys:
            X, Y = self.px(self.xr[0], y)
            self.d.line([X - 4, Y, X, Y], fill=(120, 120, 120))
            self.d.text((X - 44, Y - 7), fmt.format(y), fill=(80, 80, 80), font=f)

    def line(self, xs, ys, color, width=2):
        X, Y = self.px(xs, ys)
        self.d.line(list(zip(X.tolist(), Y.tolist())), fill=color, width=width)

    def dots(self, xs, ys, color, r=2):
        X, Y = self.px(xs, ys)
        for x, y in zip(X, Y):
            self.d.ellipse([x - r, y - r, x + r, y + r], fill=color)

    def guide(self, x=None, y=None):
        if x is not None:
            X, _ = self.px(x, 0)
            self.d.line([X, self.m[1], X, self.h - self.m[3]], fill=(200, 200, 200))
        if y is not None:
            _, Y = self.px(0, y)
            self.d.line([self.m[0], Y, self.w - self.m[2], Y], fill=(200, 200, 200))

    def legend(self, items):
        f = _font(12)
        x, y = self.w - self.m[2] - 170, self.m[1] + 8
        for color, text in items:
            self.d.rectangle([x, y + 4, x + 14, y + 10], fill=color)
            self.d.text((x + 20, y), text, fill=(40, 40, 40), font=f)
            y += 16

    def save(self, path):
        self.im.save(path)


def hue_strip_colour(deg):
    lab = np.array([0.72, 0.12 * np.cos(np.radians(deg)), 0.12 * np.sin(np.radians(deg))])
    return tuple(int(v) for v in np.clip(spaces.srgb_encode(np.clip(spaces.oklab_to_lin709(lab), 0, 1)) * 255, 0, 255))


BAND_COLOURS = [(40, 60, 140), (60, 140, 70), (200, 120, 30)]


def plot_tone(path, p, e, target_y, target_rgb):
    lo, hi = max(float(np.min(e)), -10), min(float(np.max(e)), 10)
    pl = Plot((lo, hi), (0, 1.02), "Tone curve on the neutral axis", "input, stops from 18% grey", "output code")
    pl.ticks(range(int(np.ceil(lo)), int(hi) + 1, 2), [0, 0.25, 0.5, 0.75, 1])
    pl.guide(x=0)
    t = p["tone"]
    for x in (t["toe_start"], t["shoulder_start"]):
        if lo < x < hi:
            pl.guide(x=x)
    for i, c in enumerate([(210, 60, 60), (60, 160, 60), (60, 90, 210)]):
        pl.line(e, target_rgb[:, i], c, 1)
    k = slice(None, None, max(1, len(e) // 120))
    pl.dots(e[k], target_y[k], (90, 90, 90), 2)
    xs = np.linspace(lo, hi, 400)
    pl.line(xs, model.tone_curve(xs, t), (10, 10, 10), 2)
    pl.legend([((90, 90, 90), "source, luminance"), ((10, 10, 10), "fitted curve"), ((210, 60, 60), "source R, G, B")])
    pl.save(path)


def plot_hue(path_shift, path_sat, p):
    a = model.arrays(p)
    hs = np.linspace(0, 360, 361)
    w = model.hue_weights(np.radians(hs))
    lim = max(5.0, float(np.abs(a["rot"]).max()) * 1.2)
    pl = Plot((0, 360), (-lim, lim), "Hue vs hue: rotation by hue (OKLab degrees)", "input hue, OKLab degrees", "rotation")
    pl.ticks(range(0, 361, 30), np.round(np.linspace(-lim, lim, 5), 1))
    pl.guide(y=0)
    for i, b in enumerate(model.BANDS):
        pl.line(hs, w @ a["rot"][i], BAND_COLOURS[i])
    pl.legend([(BAND_COLOURS[i], b) for i, b in enumerate(model.BANDS)])
    _strip(pl)
    pl.save(path_shift)
    hi = max(1.3, float(a["sat"].max()) * 1.1)
    lo = min(0.7, float(a["sat"].min()) * 0.9)
    pl = Plot((0, 360), (lo, hi), "Hue vs saturation: chroma gain by hue and lightness band", "input hue, OKLab degrees", "gain")
    pl.ticks(range(0, 361, 30), np.round(np.linspace(lo, hi, 5), 2))
    pl.guide(y=1)
    for i, b in enumerate(model.BANDS):
        pl.line(hs, np.exp(w @ np.log(a["sat"][i])), BAND_COLOURS[i])
    pl.legend([(BAND_COLOURS[i], b) for i, b in enumerate(model.BANDS)])
    _strip(pl)
    pl.save(path_sat)


def _strip(pl):
    for deg in range(0, 360, 2):
        x0, y0 = pl.px(deg, pl.yr[0])
        x1, _ = pl.px(deg + 2, pl.yr[0])
        pl.d.rectangle([x0, y0 - 8, x1, y0 - 1], fill=hue_strip_colour(deg + 1))


def plot_ramp(path, in_codes, out_codes, out, steps=17):
    """Neutral ramp: the source's output on R=G=B, and the same with the tint exaggerated 4x."""
    idx = np.linspace(0, len(in_codes) - 1, steps).astype(int)
    lab = spaces.oklab(spaces.to_lin709(np.clip(out_codes[idx], 0, 1), out))
    loud = lab.copy()
    loud[:, 1:] *= 4
    rows = [np.clip(out_codes[idx], 0, 1),
            np.clip(spaces.from_lin709(np.clip(spaces.oklab_to_lin709(loud), 0, 1), out), 0, 1)]
    cw, ch = 50, 50
    im = Image.new("RGB", (cw * steps, ch * 2 + 24), (250, 250, 248))
    d = ImageDraw.Draw(im)
    d.text((4, 2), "Neutral ramp through the look; bottom row: tint x4", fill=(20, 20, 20), font=_font(13))
    for r, row in enumerate(rows):
        for i, c in enumerate(row):
            d.rectangle([i * cw, 24 + r * ch, (i + 1) * cw - 1, 24 + (r + 1) * ch - 1], fill=tuple(int(v * 255 + 0.5) for v in c))
    im.save(path)


def plot_patches(path, before, after):
    s = 110
    im = Image.new("RGB", (6 * s, 4 * s + 24), (250, 250, 248))
    d = ImageDraw.Draw(im)
    d.text((4, 2), "ColorChecker 24: left half before, right half through the look", fill=(20, 20, 20), font=_font(13))
    for i in range(24):
        x, y = (i % 6) * s, 24 + (i // 6) * s
        d.rectangle([x, y, x + s // 2, y + s - 2], fill=tuple(int(v * 255 + 0.5) for v in before[i]))
        d.rectangle([x + s // 2, y, x + s - 2, y + s - 2], fill=tuple(int(v * 255 + 0.5) for v in after[i]))
        d.text((x + 3, y + 3), str(i + 1), fill=(255, 255, 255), font=_font(11))
    im.save(path)


def plot_hist(path, de, title):
    de = np.asarray(de)
    hi = max(1.0, float(np.percentile(de, 99.5)) if de.size else 1.0)
    bins = np.linspace(0, hi, 41)
    h, _ = np.histogram(np.clip(de, 0, hi), bins)
    frac = h / max(1, h.sum())
    pl = Plot((0, hi), (0, max(frac.max() * 1.1, 0.01)), title, "Delta E 2000", "share of samples")
    pl.ticks(np.round(np.linspace(0, hi, 6), 1), np.round(np.linspace(0, frac.max() * 1.1, 4), 3))
    for i, f in enumerate(frac):
        x0, y0 = pl.px(bins[i], 0)
        x1, y1 = pl.px(bins[i + 1], f)
        pl.d.rectangle([x0 + 1, y1, x1 - 1, y0], fill=(80, 110, 160))
    pl.save(path)


# --- words -------------------------------------------------------------------

def _f(v, d=2):
    return f"{v:+.{d}f}"


def plain_words(p, rows, neutral_table, skin=None):
    """Sentences a person can act on, from the numbers."""
    t, a = p["tone"], model.arrays(p)
    ref = float((spaces.from_lin709(np.array([0.18 * 2 ** 0.05]), p["out"])[0]
                 - spaces.from_lin709(np.array([0.18 * 2 ** -0.05]), p["out"])[0]) / 0.1)
    out = []
    mode = {"per_channel": "on each of R, G and B separately (film-like: steep contrast also raises saturation "
                            "and bends hue a little, before any colour number acts)",
            "luminance": "to luminance, with the scene's colour ratios kept",
            "max_rgb": "to the largest channel, with the scene's colour ratios kept"}[t.get("mode", "per_channel")]
    out.append(f"- **Tone.** 18% grey lands at code {t['mid']:.3f}. Contrast through mid grey is "
               f"{t['contrast']:.3f} code values per stop, {t['contrast'] / ref:.2f}x a plain "
               f"{p['out']} encode of the same light. The straight part runs from {t['toe_start']:+.1f} to "
               f"{t['shoulder_start']:+.1f} stops; below it the toe settles to black at {t['black']:.3f} "
               f"(strength {t['toe_strength']:.2f}), above it the shoulder rolls to white at {t['white']:.3f} "
               f"(strength {t['shoulder_strength']:.2f}; small = long soft tail, large = settles within a stop or two). "
               f"The curve is applied {mode}.")
    split = [(l, np.degrees(np.arctan2(b, a_)) % 360, 100 * np.hypot(a_, b)) for l, (a_, b) in zip(model.SPLIT_L, a["split"])]
    strong = [s for s in split if s[2] >= 0.5]
    if strong:
        parts = [f"L {l:.2f}: {warmth(h)} ({hue_name(h)}, {amt:.1f})" for l, h, amt in strong]
        out.append("- **Greys.** Neutrals pick up a tint (OKLab chroma points; about 1 is a just-visible cast, 3+ is "
                   "obvious): " + "; ".join(parts) + ".")
    else:
        out.append("- **Greys.** Neutrals stay neutral (no tint above 0.5 OKLab chroma points at any level).")
    band_sat = np.exp(np.log(a["sat"]).mean(1))
    out.append("- **Saturation by lightness.** " + ", ".join(f"{b} x{g:.2f}" for b, g in zip(model.BANDS, band_sat))
               + "; strong colours get a further x" + ", x".join(f"{g:.2f}" for g in a["roll"])
               + f" at OKLab chroma {', '.join(f'{c:.2f}' for c in model.ROLL_C)}.")
    mids = a["rot"][1]
    order = np.argsort(-np.abs(mids))[:3]
    out.append("- **Hue shifts (mids).** " + "; ".join(
        f"{model.HUES[j][4:]} {mids[j]:+.1f} deg (toward {hue_name(int(model.HUES[j][:3]) + 30 * np.sign(mids[j]))})"
        for j in order) + ".")
    sk = [rows[i] for i in SKIN]
    skin = "; ".join(
        f"{r['patch']}: hue {r['h_before']:.0f} -> {r['h_after']:.0f} deg ({_f(r['dh'], 1)}, "
        f"{'toward yellow' if r['dh'] > 0 else 'toward red/pink'}), chroma {r['C_before']:.1f} -> {r['C_after']:.1f}, "
        f"lightness {r['L_before']:.1f} -> {r['L_after']:.1f}, density {_f(r['density'], 1)}"
        for r in sk)
    out.append("- The hue and saturation numbers act after the tone curve; the patch rows below give the net "
               "effect of everything together.")
    out.append(f"- **Skin (ColorChecker patches).** {skin}.")
    if p.get("skin_family"):
        f = p["skin_family"]
        out.append(f"- **Skin family** ({f['samples']} skin tones, OKLab hue 45-70, medians): hue {f['hue_shift_deg']:+.1f} deg "
                   f"({'toward yellow' if f['hue_shift_deg'] > 0 else 'toward red/pink'}), chroma x{f['chroma_ratio']:.2f}, "
                   f"lightness {f['lightness_change']:+.1f} L points, density {f['density']:+.1f} "
                   "(negative = darker than a grey of equal input luminance, relative to before).")
    chrom = [r for r in rows[:18]]
    dens = float(np.mean([r["density"] for r in chrom]))
    out.append(f"- **Density.** Across the 18 colour patches, colours land {abs(dens):.1f} L points "
               f"{'darker' if dens < 0 else 'lighter'} against a grey of the same input luminance than they did before "
               f"the look ({'adds' if dens < -0.5 else 'removes' if dens > 0.5 else 'about neutral'} density).")
    return "\n".join(out)


def table(headers, rows):
    s = "| " + " | ".join(headers) + " |\n|" + "---|" * len(headers) + "\n"
    return s + "".join("| " + " | ".join(str(c) for c in r) + " |\n" for r in rows)


def markdown(p, title, rows, neutral_table, images, notes=""):
    fe = p.get("fit_error", {})
    s = [f"# {title}", "", f"Input `{p['in']}` ({spaces.SPACES[p['in']][3]}), output `{p['out']}` "
         f"({spaces.SPACES[p['out']][3]}). Source: {p.get('source', {}).get('file', '?')}.", ""]
    if notes:
        s += [notes, ""]
    s += ["## In plain words", "", plain_words(p, rows, neutral_table), ""]
    s += ["## How well the numbers explain it", "",
          "Delta E 2000 between the source and the model at each stage (CIELAB, D65). "
          "Under 1 is invisible side by side; 2-3 is visible on a careful look.", ""]
    hdr = ["samples", "stage", "median", "p95", "max"]
    rws = []
    for k, v in fe.items():
        for stage, st in v.items():
            if st.get("n"):
                rws.append([k, {"tone": "tone curve only", "split": "+ split tone", "full": "+ colour (all numbers)"}.get(stage, stage),
                            st["median"], st["p95"], st["max"]])
    s += [table(hdr, rws), ""]
    s += ["## Neutral axis", "", table(["input", "input code", "source out", "model out"], neutral_table), ""]
    s += ["## ColorChecker through the look", "",
          "OKLab x100. dh is the hue rotation in degrees (positive moves orange toward yellow); "
          "density is the colour's lightness against a grey of equal input luminance, relative to before the look "
          "(negative = denser).", ""]
    s += [table(["#", "patch", "L", "C", "hue", "dh", "density", "dE2000 vs before"],
                [[i + 1, r["patch"], f"{r['L_before']:.1f} -> {r['L_after']:.1f}", f"{r['C_before']:.1f} -> {r['C_after']:.1f}",
                  f"{r['h_before']:.0f} -> {r['h_after']:.0f}", f"{r['dh']:+.1f}", f"{r['density']:+.1f}", f"{r['de2000']:.1f}"]
                 for i, r in enumerate(rows)]), ""]
    s += ["## Plots", ""] + [f"![{k}]({v})" for k, v in images.items()] + [""]
    s += ["## Parameters", "", "Edit `params.json` one value at a time and rebuild with `lutlab build`.", "",
          "```json", json.dumps({k: p[k] for k in ("tone", "split_tone", "sat_rolloff", "hue_lum")}, indent=1), "```", ""]
    return "\n".join(s)
