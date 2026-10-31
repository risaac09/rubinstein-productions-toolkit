"""rpstills.absheet: an A/B/C decision sheet, the one picture sent when a
choice between candidates is needed.

    python -m rpstills absheet IMG IMG [IMG ...] --out DIR [--seed N] [--title T]

Takes 2 to 9 images (looks, crops, retouch strengths of one frame), shuffles
them by seed, never keeping the order given (so a letter says nothing about
which came first or which is new), and lays them out in a grid with a large
letter on each: A, B, C ... It writes

  DIR/sheet.jpg   at most MAX_EDGE px on the long edge and under MAX_BYTES
                  (the JPEG quality steps down QUALITIES until it fits), sRGB
  DIR/key.json    each letter -> its source file and size, the seed, the
                  grid, the sheet's size, bytes and quality

The answer is a letter; key.json turns it back into a file.

Legible at phone size: the grid is the one that brings the sheet nearest
TARGET_ASPECT (a portrait screen), so the pictures are as large as a phone
can show them, and each letter's font size is LABEL_SHARE of its cell's
short side (a capital is drawn about 0.75 of that tall), white on a
near-black badge, readable on light and dark pictures alike. Each image
keeps its aspect inside equal cells, letterboxed on a dark ground; EXIF
rotation is applied, and a picture tagged with another ICC profile
(Display P3, Adobe RGB) is converted to sRGB, so equal colours look equal
on the sheet. Pillow draws everything (no drawtext needed). Output shows
client pictures, so DIR inside any git working tree is refused, as is an
input that is DIR/sheet.jpg itself.
"""

import io
import json
import math
import os
import random
import secrets
import sys
import tempfile

MIN_IMAGES, MAX_IMAGES = 2, 9
MAX_EDGE = 2000              # px, long edge of the sheet
MAX_BYTES = 1024 * 1024      # the sheet stays under this
QUALITIES = (90, 84, 78, 70, 62, 54, 46, 38)
TARGET_ASPECT = 0.75         # width / height the grid aims for: a portrait phone or iPad
GAP = 16                     # px between cells and around the edge
GROUND = (18, 18, 18)
LABEL_SHARE = 0.16           # font size (em), share of the cell's short side; capitals are ~0.75 of it
LABEL_MIN_PX = 40
BADGE = (12, 12, 12)
INK = (255, 255, 255)
TITLE_SHARE = 0.045          # title band height, share of the sheet's long edge
LETTERS = "ABCDEFGHI"


def order(n, seed):
    """A permutation of range(n) from the seed, never the identity for n >= 2."""
    perm = list(range(n))
    random.Random(seed).shuffle(perm)
    if n >= 2 and perm == sorted(perm):
        perm = perm[1:] + perm[:1]
    return perm


def grid(n, cell_aspect):
    """(cols, rows) whose sheet aspect is nearest TARGET_ASPECT (log scale)."""
    best = None
    for cols in range(1, n + 1):
        rows = math.ceil(n / cols)
        err = abs(math.log(cols * cell_aspect / rows / TARGET_ASPECT))
        if best is None or err < best[0] - 1e-9:
            best = (err, cols, rows)
    return best[1], best[2]


def to_srgb(im, path):
    """im as 8-bit RGB in sRGB: converted from its embedded ICC profile when
    that is not sRGB (an untagged picture is taken as sRGB)."""
    from PIL import ImageCms
    icc = im.info.get("icc_profile")
    if not icc:
        return im.convert("RGB")
    try:
        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        if "srgb" in ImageCms.getProfileDescription(src).lower():
            return im.convert("RGB")
        base = im if im.mode in ("RGB", "CMYK", "L") else im.convert("L" if im.mode == "LA" else "RGB")
        return ImageCms.profileToProfile(base, src, ImageCms.createProfile("sRGB"), outputMode="RGB")
    except ImageCms.PyCMSError as e:
        raise ValueError(f"{path}: cannot convert its ICC profile to sRGB ({e})") from None


def load(path):
    """An upright 8-bit sRGB PIL image. 16-bit TIFFs Pillow cannot open go
    through OpenCV (as stored, taken as sRGB)."""
    from PIL import Image, ImageOps
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode in ("RGB", "L", "RGBA", "P", "CMYK", "LA"):
                return to_srgb(im, path)
    except OSError:
        pass
    import numpy as np
    from .audit import read_rgb
    try:
        rgb = read_rgb(path)
    except ValueError as e:
        raise ValueError(f"{path}: {e}") from None
    return Image.fromarray((np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8))


def font(px):
    from PIL import ImageFont
    return ImageFont.load_default(size=px)


def layout(sizes, title=None):
    """Sheet geometry for image sizes [(w, h)]: {"cols", "rows", "cell": (w, h),
    "sheet": (w, h), "title_h"}."""
    aspects = sorted(w / h for w, h in sizes)
    cell_aspect = aspects[len(aspects) // 2]
    cols, rows = grid(len(sizes), cell_aspect)
    title_h = int(TITLE_SHARE * MAX_EDGE) if title else 0
    # the sheet is cols * cw + (cols + 1) * GAP wide and rows * ch + (rows + 1) * GAP + title_h tall
    ch = int(min((MAX_EDGE - (cols + 1) * GAP) / cols / cell_aspect, (MAX_EDGE - (rows + 1) * GAP - title_h) / rows))
    cw = int(ch * cell_aspect)
    sw, sh = cols * cw + (cols + 1) * GAP, rows * ch + (rows + 1) * GAP + title_h
    return {"cols": cols, "rows": rows, "cell": (cw, ch), "sheet": (sw, sh), "title_h": title_h}


def render(images, title=None):
    """(PIL sheet, layout) for images already in label order."""
    from PIL import Image, ImageDraw
    geo = layout([im.size for im in images], title)
    cw, ch = geo["cell"]
    sheet = Image.new("RGB", geo["sheet"], GROUND)
    draw = ImageDraw.Draw(sheet)
    if title:
        f = font(max(12, int(geo["title_h"] * 0.62)))
        draw.text((GAP, geo["title_h"] / 2 + GAP / 2), title, fill=INK, font=f, anchor="lm")
    label_px = max(LABEL_MIN_PX, int(LABEL_SHARE * min(cw, ch)))
    lf = font(label_px)
    pad = max(6, label_px // 4)
    boxes = []
    for i, im in enumerate(images):
        r, c = divmod(i, geo["cols"])
        x0, y0 = GAP + c * (cw + GAP), geo["title_h"] + GAP + r * (ch + GAP)
        tile = im.copy()
        tile.thumbnail((cw, ch), Image.Resampling.LANCZOS)
        sheet.paste(tile, (x0 + (cw - tile.width) // 2, y0 + (ch - tile.height) // 2))
        letter = LETTERS[i]
        l, t, rr, b = draw.textbbox((0, 0), letter, font=lf, stroke_width=max(1, label_px // 24))
        bw, bh = rr - l + 2 * pad, b - t + 2 * pad
        draw.rounded_rectangle((x0 + pad, y0 + pad, x0 + pad + bw, y0 + pad + bh), radius=pad, fill=BADGE)
        draw.text((x0 + 2 * pad - l, y0 + 2 * pad - t), letter, font=lf, fill=INK,
                  stroke_width=max(1, label_px // 24), stroke_fill=INK)
        boxes.append([x0 + pad, y0 + pad, bw, bh])
    geo["label_px"] = label_px
    geo["badges"] = boxes
    return sheet, geo


def encode(sheet):
    """(jpeg bytes, quality): the highest quality in QUALITIES under MAX_BYTES."""
    from PIL import ImageCms
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    for q in QUALITIES:
        buf = io.BytesIO()
        sheet.save(buf, "JPEG", quality=q, subsampling=0 if q >= 70 else 2, icc_profile=icc, optimize=True)
        if buf.tell() < MAX_BYTES:
            return buf.getvalue(), q
    raise ValueError(f"the sheet does not fit in {MAX_BYTES} bytes even at quality {QUALITIES[-1]}")


def _atomic(path, data):
    folder = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".absheet-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def refuse_git(out_dir):
    from rpresolve.paths import out_problem  # lazy: rpresolve is stdlib-only, imported only to write
    problem = out_problem(os.path.join(out_dir, "sheet.jpg"), any_git_tree=True)
    if problem:
        raise ValueError(problem)


def key_dir(out_dir):
    """Where key.json goes: beside out_dir, never in it, so serving out_dir shows the reviewer only the sheet."""
    return os.fspath(out_dir).rstrip(os.sep) + ".key"


def build(paths, out_dir, seed=None, title=None):
    """Write sheet.jpg in out_dir and key.json in key_dir(out_dir); returns the key document.
    ValueError for a wrong count, an unreadable image or a refused folder."""
    if not MIN_IMAGES <= len(paths) <= MAX_IMAGES:
        raise ValueError(f"{len(paths)} images; a sheet takes {MIN_IMAGES} to {MAX_IMAGES}")
    out_dir = os.path.abspath(out_dir)
    refuse_git(out_dir)
    seed = secrets.randbelow(2 ** 31) if seed is None else int(seed)
    paths = [os.path.abspath(p) for p in paths]
    sheet_path = os.path.realpath(os.path.join(out_dir, "sheet.jpg"))
    if sheet_path in {os.path.realpath(p) for p in paths}:
        raise ValueError(f"{sheet_path} is one of the images and would be overwritten; give another --out")
    images = [load(p) for p in paths]
    perm = order(len(paths), seed)
    sheet, geo = render([images[i] for i in perm], title)
    data, quality = encode(sheet)
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(key_dir(out_dir), exist_ok=True)
    _atomic(os.path.join(out_dir, "sheet.jpg"), data)
    doc = {"tool": "rpstills absheet", "seed": seed, "title": title,
           "labels": {LETTERS[k]: {"source": paths[i], "input_index": i, "size": list(images[i].size)}
                      for k, i in enumerate(perm)},
           "sheet": {"file": "sheet.jpg", "size": list(sheet.size), "bytes": len(data), "quality": quality,
                     "grid": [geo["cols"], geo["rows"]], "cell": list(geo["cell"]), "label_px": geo["label_px"]},
           "params": {"max_edge": MAX_EDGE, "max_bytes": MAX_BYTES, "target_aspect": TARGET_ASPECT,
                      "label_share": LABEL_SHARE, "label_min_px": LABEL_MIN_PX}}
    _atomic(os.path.join(key_dir(out_dir), "key.json"), (json.dumps(doc, indent=1, sort_keys=True) + "\n").encode())
    return doc


def summary(doc):
    s = doc["sheet"]
    labels = sorted(doc["labels"].items())
    base = [os.path.basename(v["source"]) for _, v in labels]
    if len(set(base)) < len(base):  # one frame in several look folders: name the folder too
        base = [os.path.join(os.path.basename(os.path.dirname(v["source"])), b) for (_, v), b in zip(labels, base)]
    names = ", ".join(f"{k}={b}" for (k, _), b in zip(labels, base))
    return (f"sheet.jpg {s['size'][0]}x{s['size'][1]}, {s['bytes'] / 1024:.0f} KiB at q{s['quality']}, "
            f"{len(doc['labels'])} images in {s['grid'][0]}x{s['grid'][1]} (seed {doc['seed']}): {names}")


def add_arguments(p):
    p.add_argument("images", nargs="+", metavar="IMG", help=f"{MIN_IMAGES} to {MAX_IMAGES} images (JPEG, PNG, TIFF)")
    p.add_argument("--out", required=True, help="folder for sheet.jpg (key.json goes to OUT.key, apart from it); never inside a git tree")
    p.add_argument("--seed", type=int, help="shuffle seed (default: a random one, recorded in key.json)")
    p.add_argument("--title", help="a line of text across the top of the sheet")


def run(args):
    try:
        doc = build(args.images, args.out, args.seed, args.title)
    except (ValueError, OSError) as e:
        print(f"rpstills absheet: {e}", file=sys.stderr)
        return 1
    print(os.path.join(os.path.abspath(args.out), "sheet.jpg"))
    print("key: " + os.path.join(key_dir(os.path.abspath(args.out)), "key.json"))
    print(summary(doc))
    return 0
