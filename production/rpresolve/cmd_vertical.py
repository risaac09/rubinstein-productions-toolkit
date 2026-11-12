"""
vertical: the 9:16 finish step for a montage that was built at 16:9.

`plan`, `draft` and `review` are the offline half (this command's CLI). `plan` takes the
montage's items (name, source path, in and out in source seconds, zoom, and the
16:9 crop_x when the build had one, and `window`, scale-window or crop-window, on an item whose
window opens over the shot before it), picks one crop centre cx per item (a share
of the source width), writes crops.json and a numbered review sheet. Its --out folder
must not exist yet; the command makes it. `draft` cuts a silent 540x960 preview, preview.mp4,
from crops.json: each item from its source at its crop, in order, the item number in the
corner, and records beside it (preview.json) a hash of the items it drew. The operator watches
it before anything is written to Resolve. `review` sets crops.json's "reviewed" to true once a
person or agent has read the sheet and watched the preview, and refuses when preview.mp4 is
not beside crops.json or was drawn from other items (a centre changed since): run `draft` again.

Centre choice, in this order: a hand override ("<item name>" or "#<item number>"
in the overrides file) always wins; else, only with --centre face, the face planner
(reframe.plan_items for 1080x1920 at scaleToCrop) when it passes or falls back,
cx = its x / source width; else the edge rule, 0.5 + (crop_x - 0.5) * (1 - 2*hw) with a
crop_x, 0.5 without. --centre edge is the default and skips the face planner. hw is half
the portrait window's share of the source width, 0.5 * (9/16) / (width / height). Every
centre is clamped to [hw/zoom, 1 - hw/zoom]. Consecutive items cut from one take with
touching in and out share the first one's centre, so a take does not jump between cuts.
The face planner follows the face it can track, which may be a bystander at the frame
edge, so the sheet exists and apply refuses a crops.json that has not been reviewed.

`apply_vertical`, the work behind the MCP tool, is the guarded Resolve write. It
refuses a timeline whose name does not end ' [auto]'. When the target does not
exist it duplicates source_timeline to it (workflows.duplicate_auto) and sets
1080x1920 on that new timeline only; an existing target must already be
1080x1920. Per item it sets the item's own Scaling to 3 (Fill; a duplicated
timeline's scaleToCrop reads back and still renders fit to width), Pan to
(0.5 - cx) * 1080 * zoom and Tilt to 0, and keeps the item's own zoom (asserted
equal to crops.json's). A Fusion comp named 'rp-fx zoom-push' gets
Transform1.Pivot = (cx, 0.5); 'rp-fx text-word' gets Merge1.Center = (cx, 0.5) and
Text1.Size refitted for portrait. Comps are then re-rendered with
cmd_effect.refresh and everything is read back. An 'rp-fx person-matte-text' comp gets
Text1.Center x = cx (its y kept) and Text1.Size refitted like text-word's; its matte is NOT
moved: the matte sits on the subject in the source frame, which the crop does not move, and a
Magic Mask has no position to set (the stand-in rectangle's Center is plain, but moving it
would take it off the subject). While the comp still holds SubjectMatte_PLACEHOLDER the plan
says so in the comp's `notes`. Every other Fusion comp on an item is left as it was and named
in the result's unmoved_comps. apply_vertical does not look for the preview; `review` is where it is required.

A montage with a window effect sits on V2 over plates on V1 (cmd_assemble). When `track` is
above 1, an item on the track below that starts where montage item N (N > 1) starts and plays
the same file as item N - 1 is a plate, and gets item N - 1's crop: Scaling 3, Pan from that
cx with the plate's own zoom, Tilt 0, so the outgoing shot does not move at the cut. Found
from the timeline alone; anything else on that track is left alone and named in
`unframed_below`. The offline `draft` preview draws them where an item's `window` says one opens:
see draft_preview.
"""

import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import tempfile

from rpresolve import api, cli
from rpresolve import paths as rppaths
from rpresolve import windowplate
from rpresolve.workflows import AUTO, Refused, plan_sha

NAME = "vertical"
HELP = "Offline: plan 9:16 crop centres for a montage (crops.json and a review sheet), mark it reviewed"

OUT_SIZE = (1080, 1920)
ASPECT = "9x16"
TOUCH_S = 0.05          # seconds between one item's out and the next's in that still count as one take
FILL = 3                # Resolve's item Scaling: 0 project, 1 crop, 2 fit, 3 fill, 4 stretch
ZOOM_TOLERANCE = 0.01
READ_TOLERANCE = 1e-3
COMP_ZOOM, COMP_TEXT, COMP_MATTE = "rp-fx zoom-push", "rp-fx text-word", "rp-fx person-matte-text"
KINDS = {COMP_ZOOM: "zoom-push", COMP_TEXT: "text-word", COMP_MATTE: "person-matte-text"}
MATTE_PLACEHOLDER = "SubjectMatte_PLACEHOLDER"
MATTE_TITLE_Y = 0.55    # Text1.Center y of the macro, kept when the word is moved across
TEXT_CAP = 0.09         # Text1.Size ceiling at 9:16
TEXT_K = 0.336          # size * letters: 0.048 for seven letters, about 80% of the frame width

SHEET_W, SHEET_H, SHEET_ROWS = 560, 316, 6   # cells 3 across: 1680 x 1896 a sheet (even sizes for JPEG 4:2:0)
SHEET_MAX_BYTES = 1024 * 1024
PREVIEW_SIZE, PREVIEW_FPS, PREVIEW_NAME = (540, 960), 30, "preview.mp4"
PREVIEW_STAMP = "preview.json"   # beside the preview: {"items_sha": hash of the items it drew}


class VerticalError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# plan (offline)
# ---------------------------------------------------------------------------

def half_width(size):
    """Half the 9:16 window's share of the source width."""
    return 0.5 * (9.0 / 16.0) / (size[0] / float(size[1]))


def clamp_cx(cx, size, zoom):
    lo = half_width(size) / zoom
    return 0.5 if lo > 1 - lo else min(max(cx, lo), 1 - lo)


def edge_cx(crop_x, size):
    if crop_x is None:
        return 0.5
    return 0.5 + (crop_x - 0.5) * (1 - 2 * half_width(size))


def _items(items, probe):
    rows = []
    for i, it in enumerate(items, 1):
        for k in ("name", "path", "in", "out"):
            if it.get(k) is None:
                raise VerticalError(f"item {i}: give {k}")
        zoom = float(it.get("zoom") or 1.0)
        if zoom <= 0 or float(it["out"]) <= float(it["in"]):
            raise VerticalError(f"item {i} ({it['name']}): zoom must be above 0 and out after in")
        size = it.get("size")
        if not size:
            info = (probe or _probe)(it["path"])
            size = [info["width"], info["height"]]
        win = it.get("window")
        if win is not None and win not in windowplate.PLATE_FRAMES:
            raise VerticalError(f"item {i} ({it['name']}): window is one of "
                                f"{', '.join(sorted(windowplate.PLATE_FRAMES))}")
        rows.append({"index": i, "name": it["name"], "path": it["path"], "in": float(it["in"]),
                     "out": float(it["out"]), "zoom": zoom, "crop_x": it.get("crop_x"),
                     "size": [int(size[0]), int(size[1])], "window": win})
    return rows


def _probe(path):
    from rpresolve import reframe
    return reframe.probe(path)


def _override(overrides, row):
    for key in (f"#{row['index']}", row["name"]):
        if key in (overrides or {}):
            v = overrides[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1:
                raise VerticalError(f"override for {key}: a centre is a share of the width, "
                                    f"0 to 1 (got {v!r})")
            return float(v)
    return None


def _takes(rows):
    """Indexes (into rows) grouped by consecutive items of one take."""
    groups = []
    for n, r in enumerate(rows):
        prev = rows[n - 1] if n else None
        if (prev and os.path.normpath(prev["path"]) == os.path.normpath(r["path"])
                and abs(prev["out"] - r["in"]) <= TOUCH_S):
            groups[-1].append(n)
        else:
            groups.append([n])
    return groups


def _face(rows, group, planner):
    """(cx, status, reason) from the face planner for a take, or (None, status, reason)."""
    head, last = rows[group[0]], rows[group[-1]]
    item = {"name": head["name"], "path": head["path"], "in": head["in"], "out": last["out"]}
    try:
        res = planner([item], aspect=ASPECT, master_size=list(OUT_SIZE), scaling="scaleToCrop")
        got = res["items"][0]
    except Exception as e:  # shortcut: any planner failure falls back to the edge rule; the reason is kept
        return None, "face_error", str(e)
    e = got.get("entry") or {}
    if got.get("status") in ("pass", "fallback") and e.get("x") is not None:
        return e["x"] / float((e.get("src") or head["size"])[0]), got["status"], got.get("reason", "")
    return None, got.get("status") or "no_face", got.get("reason", "")


def plan(items, overrides=None, mode="edge", planner=None, probe=None):
    """crops.json as a dict: {aspect, reviewed: False, items: [{index, name, path, in, out,
    cx, how, status, reason, zoom, source_size, shared_with?}]}. mode 'edge' (the default)
    uses the edge rule only; mode 'face' tries the face planner (planner(items, aspect=,
    master_size=, scaling=) -> reframe.plan_items' shape; default reframe.plan_items) and
    falls back to the edge rule."""
    if mode not in ("face", "edge"):
        raise VerticalError("mode is 'face' or 'edge'")
    if mode == "face" and planner is None:
        from rpresolve import reframe
        planner = reframe.plan_items
    rows = _items(items, probe)
    out = []
    for group in _takes(rows):
        head = rows[group[0]]
        hand = _override(overrides, head)
        if hand is not None:
            cx, how, status, reason = hand, "hand", "hand", ""
        else:
            cx, status, reason = (_face(rows, group, planner) if mode == "face"
                                  else (None, "edge", ""))
            how = "face" if cx is not None else "edge"
            if cx is None:
                cx = edge_cx(head["crop_x"], head["size"])
        for n in group:
            r = rows[n]
            own = _override(overrides, r) if n != group[0] else None
            c, h, s = (own, "hand", "hand") if own is not None else (cx, how, status)
            row = {"index": r["index"], "name": r["name"], "path": r["path"], "in": r["in"],
                   "out": r["out"], "cx": round(clamp_cx(c, r["size"], r["zoom"]), 4), "how": h,
                   "status": s if n == group[0] or own is not None else "shared",
                   "reason": reason if n == group[0] and own is None else "",
                   "zoom": r["zoom"], "source_size": r["size"]}
            if r["window"]:
                row["window"] = r["window"]
            if n != group[0] and own is None:
                row["shared_with"] = rows[group[0]]["index"]
            out.append(row)
    return {"aspect": ASPECT, "reviewed": False, "items": out}


def load_crops(src):
    """A crops.json (path or dict) checked for shape. Returns the dict."""
    if not isinstance(src, dict):
        try:
            with open(src, encoding="utf-8") as f:
                src = json.load(f)
        except (OSError, ValueError) as e:
            raise Refused(f"cannot read the crops file: {e}")
    if src.get("aspect") != ASPECT or not src.get("items"):
        raise Refused("not a 9x16 crops.json: it needs aspect '9x16' and items")
    for it in src["items"]:
        cx, z = it.get("cx"), it.get("zoom")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (cx, z)) \
                or not 0 < cx < 1 or z <= 0:
            raise Refused(f"item {it.get('index')} ({it.get('name')}): cx must be between 0 and 1 "
                          "and zoom above 0")
        if it.get("window") is not None and it["window"] not in windowplate.PLATE_FRAMES:
            raise Refused(f"item {it.get('index')} ({it.get('name')}): window is one of "
                          f"{', '.join(sorted(windowplate.PLATE_FRAMES))}")
    return src


def _items_sha(crops):
    return plan_sha("vertical preview", crops["items"])


def mark_reviewed(path):
    """Set reviewed, but only when preview.mp4 beside the plan was drawn from these items."""
    crops = load_crops(path)
    folder = os.path.dirname(os.path.abspath(path))
    try:
        with open(os.path.join(folder, PREVIEW_STAMP), encoding="utf-8") as f:
            stamp = json.load(f).get("items_sha")
    except (OSError, ValueError, AttributeError):
        stamp = None
    if not os.path.isfile(os.path.join(folder, PREVIEW_NAME)) or stamp is None:
        raise VerticalError(f"no {PREVIEW_NAME} from `vertical draft` beside {path}. Run "
                            f"vertical draft {path}, watch the preview, then review.")
    if stamp != _items_sha(crops):
        raise VerticalError(f"{PREVIEW_NAME} beside {path} was drawn from other crops (a centre "
                            f"or zoom changed since). Run vertical draft {path} again, watch it, "
                            "then review.")
    crops["reviewed"] = True
    rppaths.write_private(path, json.dumps(crops, indent=1) + "\n")
    return crops


# ---------------------------------------------------------------------------
# the review sheet (ffmpeg only: no font is needed, the item number is drawn in segments)
# ---------------------------------------------------------------------------

_SEGS = {"0": "abcdef", "1": "bc", "2": "abdeg", "3": "abcdg", "4": "bcfg", "5": "acdfg",
         "6": "acdefg", "7": "abc", "8": "abcdefg", "9": "abcdfg"}
_SEG_BOX = {"a": (0, 0, 14, 3), "b": (11, 0, 3, 14), "c": (11, 12, 3, 14), "d": (0, 23, 14, 3),
            "e": (0, 12, 3, 14), "f": (0, 0, 3, 14), "g": (0, 11, 14, 3)}


def _digits(n, x0=4, y0=4):
    text = str(n)
    parts = [f"drawbox=x={x0 - 2}:y={y0 - 2}:w={20 * len(text) + 2}:h=30:color=black:t=fill"]
    for k, ch in enumerate(text):
        for s in _SEGS[ch]:
            x, y, w, h = _SEG_BOX[s]
            parts.append(f"drawbox=x={x0 + 20 * k + x}:y={y0 + y}:w={w}:h={h}:color=white:t=fill")
    return parts


def frame_filter(row, number=None):
    """The ffmpeg filter that fits a frame into a sheet cell and draws the crop box (yellow),
    its centre (red) and a ruler along the bottom (ticks every tenth, long at the middle)."""
    sw, sh = row["source_size"]
    k = min(SHEET_W / float(sw), SHEET_H / float(sh))
    fw, fh = max(2, int(sw * k)), max(2, int(sh * k))
    half = half_width(row["source_size"]) / row["zoom"]
    boxh = fh / row["zoom"]
    f = [f"scale={fw}:{fh}", f"pad={SHEET_W}:{SHEET_H}:0:0",
         f"drawbox=x={(row['cx'] - half) * fw:.0f}:y={(fh - boxh) / 2:.0f}:w={2 * half * fw:.0f}:"
         f"h={boxh:.0f}:color=yellow:t=3",
         f"drawbox=x={row['cx'] * fw:.0f}:y=0:w=1:h={fh}:color=red:t=fill",
         f"drawbox=x=0:y={fh - 14}:w={fw}:h=14:color=black:t=fill"]
    f += [f"drawbox=x={min(fw - 1, k_ * fw // 10)}:y={fh - 14}:w=1:h={14 if k_ == 5 else 8}:"
          "color=white:t=fill" for k_ in range(11)]
    return ",".join(f + (_digits(number) if number else []))


def frame_times(row):
    a, b = row["in"], row["out"]
    mid = (a + b) / 2.0
    return [mid] if b - a <= 1.0 else [a + 0.1 * (b - a), mid, a + 0.9 * (b - a)]


def _has_hw(ffmpeg):
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True,
                             text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return False
    return "h264_videotoolbox" in out


def _run(cmd):
    p = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=300)
    return p.returncode, p.stderr.decode(errors="replace").strip()[-300:]


def review_sheets(crops, out_dir, ffmpeg=None):
    """Write review-01.jpg ... to out_dir: six items a sheet, each a row of its frames (one for
    an item up to a second long, else near its start, middle and end) with the crop box and
    ruler drawn, the item's number in the corner. Returns the paths."""
    from rpresolve import reframe
    ffmpeg = ffmpeg or reframe.FFMPEG
    hw = _has_hw(ffmpeg)
    paths, rows = [], crops["items"]
    os.makedirs(out_dir, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for page, start in enumerate(range(0, len(rows), SHEET_ROWS), 1):
            cells, layout = [], []
            for r_, row in enumerate(rows[start:start + SHEET_ROWS]):
                for c_, t in enumerate(frame_times(row)):
                    jpg = os.path.join(tmp, f"{row['index']}-{c_}.jpg")
                    vf = frame_filter(row, row["index"] if c_ == 0 else None)
                    for use_hw in ((True, False) if hw else (False,)):
                        cmd = ([ffmpeg, "-v", "error", "-y"] + (["-hwaccel", "videotoolbox"]
                               if use_hw else []) + ["-ss", f"{t:.3f}", "-i", "file:" + row["path"],
                               "-an", "-frames:v", "1", "-vf", vf, "-pix_fmt", "yuvj420p",
                               "-q:v", "3", jpg])
                        code, err = _run(cmd)
                        if code == 0 and os.path.exists(jpg):
                            break
                    else:
                        raise VerticalError(f"item {row['index']} ({row['name']}): ffmpeg could "
                                            f"not read the frame at {t:.2f}s: {err}")
                    cells.append(jpg)
                    layout.append(f"{c_ * SHEET_W}_{r_ * SHEET_H}")
            sheet = os.path.join(out_dir, f"review-{page:02d}.jpg")
            for q in (4, 8, 14, 24):
                cmd = [ffmpeg, "-v", "error", "-y"]
                for c in cells:
                    cmd += ["-i", c]
                code, err = _run(cmd + ["-filter_complex", _sheet_graph(len(cells), layout),
                                        "-map", "[o]", "-frames:v", "1", "-q:v", str(q), sheet])
                if code != 0:
                    raise VerticalError(f"ffmpeg could not build {sheet}: {err}")
                if os.path.getsize(sheet) < SHEET_MAX_BYTES:
                    break
            paths.append(sheet)
    return paths


def _sheet_graph(n, layout):
    if n == 1:
        return "[0:v]null[o]"
    ins = "".join(f"[{i}:v]" for i in range(n))
    return f"{ins}xstack=inputs={n}:layout={'|'.join(layout)}:fill=black[o]"

# ---------------------------------------------------------------------------
# the preview (ffmpeg only): the framing, watched before anything is written to Resolve
# ---------------------------------------------------------------------------

def preview_filter(row):
    """The ffmpeg filter that cuts the window the review sheet draws (9/16 of the zoomed height
    wide, centred on cx, the zoomed height tall, level with the middle), scales it to the
    preview and draws the item's number in the corner. A source narrower than 9:16 gives a
    window as wide as it is; a zoom below 1 is held at 1, since the crop cannot leave the frame."""
    sw, sh = row["source_size"]
    w, h = (sw, sw * 16 / 9) if sw * 16 < sh * 9 else (sh * 9 / 16, sh)
    z = max(row["zoom"], 1.0)
    w, h = max(2, int(w / z)), max(2, int(h / z))
    x = min(max(int(row["cx"] * sw - w / 2), 0), sw - w)
    return ",".join([f"crop={w}:{h}:{x}:{(sh - h) // 2}", "scale=%d:%d" % PREVIEW_SIZE, "setsar=1",
                     f"fps={PREVIEW_FPS}"] + _digits(row["index"]) + ["format=yuv420p"])


def window_filter(row, prev):
    """The filter_complex for an item with a `window` (an item after the first): the window
    opens over the previous item's shot playing on from its out point, framed with that item's
    crop, as the V1 plate is in the build (vertical_apply), by the draft farm's own window draw.
    The frame counts are the macro's, read at the preview's rate. Input 0 is the item, input 1
    the previous item's file from its out point."""
    from rpresolve import drafts
    w, h = PREVIEW_SIZE

    def zoomed(r):
        return {"scale": r["zoom"]} if r["zoom"] > 1 else {}
    back = drafts.span_filter(zoomed(prev), [], w, h, PREVIEW_FPS, 1.0 / PREVIEW_FPS,
                              cx=prev["cx"])[0]
    vf = drafts.span_filter(zoomed(row), [{"effect": row["window"]}], w, h, PREVIEW_FPS,
                            row["out"] - row["in"], back=back, cx=row["cx"])[0]
    return ",".join([vf] + _digits(row["index"]) + ["format=yuv420p"])


def draft_preview(crops, out_dir, ffmpeg=None):
    """Write out_dir/preview.mp4 from crops.json (a path or the dict): a silent 540x960 cut of
    every item from its source (path, in, out) at its crop centre, joined in order. An item with
    a `window` (scale-window or crop-window) after the first opens over the previous item's
    shot playing on, framed with that item's crop: the plate the build puts on V1 (window_filter).
    The picture is client media, so a folder inside any git working tree is refused. Returns the
    path. Nothing here needs crops.json to be reviewed; the preview is what the review is read
    with."""
    from rpresolve import drafts, reframe
    c = load_crops(crops)
    out_dir = os.path.abspath(out_dir)
    if rppaths.in_any_git_tree(out_dir):
        raise VerticalError(f"{out_dir} is inside a git working tree. The preview shows client "
                            "media; write it outside any repository.")
    ffmpeg = ffmpeg or reframe.FFMPEG
    hw = _has_hw(ffmpeg)
    os.makedirs(out_dir, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out_dir) as tmp:
        def cut(k_row):
            k, row = k_row
            prev = c["items"][k - 1] if k and row.get("window") else None
            if prev:
                try:
                    drafts._check_plate(prev["path"], prev["out"], windowplate.plate_frames(
                        row["window"], round((row["out"] - row["in"]) * PREVIEW_FPS)),
                        PREVIEW_FPS, f"item {row['index']} ({row['name']})")
                except drafts.DraftError as e:
                    raise VerticalError(str(e))
            part = os.path.join(tmp, f"{row['index']:03d}.mp4")
            for use_hw in ((True, False) if hw else (False,)):
                code, err = _run([ffmpeg, "-v", "error", "-y"]
                                 + (["-hwaccel", "videotoolbox"] if use_hw else [])
                                 + ["-ss", f"{row['in']:.4f}", "-t", f"{row['out'] - row['in']:.4f}",
                                    "-i", "file:" + row["path"]]
                                 + (["-ss", f"{prev['out']:.4f}", "-i", "file:" + prev["path"],
                                     "-an", "-filter_complex", window_filter(row, prev)] if prev
                                    else ["-an", "-vf", preview_filter(row)])
                                 + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                                    "-write_tmcd", "0", part])
                if code == 0 and os.path.exists(part):
                    return part
            raise VerticalError(f"item {row['index']} ({row['name']}): ffmpeg could not cut "
                                f"{row['in']:.2f}s to {row['out']:.2f}s: {err}")
        with concurrent.futures.ThreadPoolExecutor(os.cpu_count() or 1) as pool:
            parts = list(pool.map(cut, enumerate(c["items"])))
        lst, joined = os.path.join(tmp, "parts.txt"), os.path.join(tmp, PREVIEW_NAME)
        with open(lst, "w", encoding="utf-8") as f:
            f.writelines("file '" + p.replace("'", "'\\''") + "'\n" for p in parts)
        code, err = _run([ffmpeg, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                          "-c", "copy", "-movflags", "+faststart", joined])
        if code != 0:
            raise VerticalError(f"ffmpeg could not join the preview: {err}")
        path = os.path.join(out_dir, PREVIEW_NAME)
        os.replace(joined, path)
        rppaths.write_private(os.path.join(out_dir, PREVIEW_STAMP),
                              json.dumps({"items_sha": _items_sha(c)}) + "\n")
    return path


# ---------------------------------------------------------------------------
# apply (the guarded Resolve write)
# ---------------------------------------------------------------------------

def text_size(text, cap=TEXT_CAP):
    """Text1.Size for a word at 9:16: about 80% of the width for the longest word, cap at `cap`."""
    n = max((len(w) for w in str(text or "").split()), default=1) or 1
    return round(min(cap, TEXT_K / n), 4)


def _num(v):
    try:
        return None if v is None or v == "" or isinstance(v, bool) else float(v)
    except (TypeError, ValueError):
        return None


def _pt(v):
    """(x, y) from a Fusion point as a dict ({1: x, 2: y}, keys int, float or text) or a pair."""
    try:
        if isinstance(v, dict):
            by = {int(float(k)): float(x) for k, x in v.items()}
            return by[1], by[2]
        return float(v[0]), float(v[1])
    except (TypeError, ValueError, KeyError, IndexError):
        return None


def _close(a, b, tol=READ_TOLERANCE):
    return a is not None and b is not None and abs(a - b) <= tol


def _timeline_size(tl):
    def read(key):
        v = _num(api._safe_call(tl, "GetSetting", key))
        return int(v) if v else None
    return read("timelineResolutionWidth"), read("timelineResolutionHeight")


def _timeline_named(project, name):
    for i in range(1, int(project.GetTimelineCount() or 0) + 1):
        tl = project.GetTimelineByIndex(i)
        if tl and tl.GetName() == name:
            return tl
    return None


def _comps(item):
    """The item's rp-fx comps this step moves: [{comp, kind, tool, ...values}]."""
    names = list(api._safe_call(item, "GetFusionCompNameList") or [])
    out = []
    for name in names:
        if name not in KINDS:
            continue
        out.append({"comp": name, "kind": KINDS[name], "object": item.GetFusionCompByName(name)})
    return out


def _unmoved(targets, items):
    """Every Fusion comp this step leaves as it was: the items' comps other than the three it
    moves (zoom-push, text-word, person-matte-text)."""
    return [{"index": t["index"], "name": t["name"], "comp": name}
            for t, it in zip(targets, items)
            for name in api._safe_call(it, "GetFusionCompNameList") or []
            if name not in KINDS]


def _fusion_plan(c, cx, text_override, text_cap):
    """(inputs to set, notes) for one comp."""
    comp = c["object"]
    if c["kind"] == "zoom-push":
        return {"Transform1.Pivot": (cx, 0.5)}, []
    word = y = None
    t1 = comp.FindTool("Text1") if comp else None
    if t1:
        word = t1.GetInput("StyledText")
        y = _pt(t1.GetInput("Center"))
    size = text_override if text_override is not None else text_size(word, text_cap)
    if c["kind"] == "text-word":
        return {"Merge1.Center": (cx, 0.5), "Text1.Size": size}, []
    notes = []
    if comp and comp.FindTool(MATTE_PLACEHOLDER):
        notes.append(f"{MATTE_PLACEHOLDER} is not moved: it sits on the subject in the source frame, "
                     "which the crop does not move. It is a one-frame stand-in; replace it with the "
                     "tracked Person Magic Mask (fusion-macros.md) before delivery.")
    return {"Text1.Center": (cx, y[1] if y else MATTE_TITLE_Y), "Text1.Size": size}, notes


def _item_plan(it, crop, n, text_size_override, text_cap):
    """The row for one item: what will be written, and its refusals (reasons)."""
    cx, zoom = float(crop["cx"]), float(crop["zoom"])
    why = []
    if it.GetName() != crop["name"]:
        why.append(f"is named '{it.GetName()}', crops.json has '{crop['name']}'")
    from rpresolve.workflows import _item_path
    from rpresolve import ingest as rpingest
    path = _item_path(it)
    if crop.get("path") and path and rpingest.norm_path(path) != rpingest.norm_path(crop["path"]):
        why.append("plays a different file than crops.json planned")
    zx, zy = (_num(api._safe_call(it, "GetProperty", k)) for k in ("ZoomX", "ZoomY"))
    if zx is None or zy is None or abs(zx - zoom) > ZOOM_TOLERANCE or abs(zy - zoom) > ZOOM_TOLERANCE:
        why.append(f"has zoom {zx}/{zy}, crops.json has {zoom:g}")
    comps = _comps(it)
    row = {"index": n, "name": crop["name"], "cx": cx, "zoom": zoom,
           "props": {"Scaling": FILL, "Pan": round((0.5 - cx) * OUT_SIZE[0] * zoom, 4), "Tilt": 0},
           "comps": [dict(zip(("set", "notes"), _fusion_plan(c, cx, text_size_override, text_cap)),
                          comp=c["comp"]) for c in comps], "reasons": why}
    return row, comps


def _plates_below(base, track, items, crops):
    """(plates, unframed) for the track under the montage. A plate starts where montage item N
    (N > 1) starts and plays the file of item N - 1; it takes that item's cx and its own zoom.
    Anything else on the track is returned in unframed, never guessed at. `slot` is the plate's
    place among that track's clips (kept for the duplicate, whose items are new objects)."""
    if track < 2:
        return [], []
    from rpresolve.workflows import _item_path
    from rpresolve import ingest as rpingest
    start_of = {it.GetStart(): n for n, it in enumerate(items, 1)}
    plates, unframed = [], []
    for slot, it in enumerate(api.clip_items(base, track - 1), 1):
        n, path = start_of.get(it.GetStart()), _item_path(it)
        zx, zy = (_num(api._safe_call(it, "GetProperty", k)) for k in ("ZoomX", "ZoomY"))
        if n == 1:
            why = "sits under montage item 1, which continues nothing"
        elif n is None:
            why = "starts where no montage item starts"
        elif not path or rpingest.norm_path(path) != rpingest.norm_path(_item_path(items[n - 2])):
            why = f"does not play the file of montage item {n - 1}"
        elif zx is None or zy is None or zx <= 0 or abs(zx - zy) > ZOOM_TOLERANCE:
            why = f"has zoom {zx}/{zy}"
        else:
            cx = float(crops[n - 2]["cx"])
            plates.append({"slot": slot, "name": it.GetName(), "under": n, "continues": n - 1,
                           "cx": cx, "zoom": zx, "index": n, "comps": [],
                           "props": {"Scaling": FILL, "Pan": round((0.5 - cx) * OUT_SIZE[0] * zx, 4),
                                     "Tilt": 0}})
            continue
        unframed.append({"slot": slot, "name": it.GetName(), "start": it.GetStart(), "why": why})
    return plates, unframed


def apply_vertical(resolve, project_name, timeline, crops, source_timeline=None, track=1,
                   dry_run=True, project_id=None, expect_sha=None, check_cancel=None,
                   text_size_override=None, text_cap=TEXT_CAP):
    """Finish a 16:9 [auto] montage as 9:16 from crops.json (a path or the dict). timeline: the
    target's name, which must end ' [auto]'; if it does not exist, source_timeline is duplicated
    to it and the copy set to 1080x1920 (an existing target must already be 1080x1920). Per item
    on video track `track`: Scaling 3, Pan, Tilt 0, the zoom checked; per rp-fx comp: pivot or
    centre and size; each comp re-rendered with cmd_effect.refresh; everything read back.
    Refused, writing nothing: a target not ending ' [auto]', crops.json not reviewed, an item
    count, name, file or zoom that differs from crops.json, an existing target of another size.
    On `track` above 1, plates on the track below take the crop of the item they continue
    (_plates_below): `plates` is the plan, `plate_results` the read-backs, `unframed_below`
    what on that track is not a plate. Returns {project, timeline, source_timeline, create,
    targets, plates, unframed_below, skipped, unmoved_comps, results, plate_results, plan_sha, dry_run,
    exit_status}; exit_status 1 when a read-back differs (a plate's included). unmoved_comps names
    each item and Fusion comp left as it was (every comp but the three moved), dry run or real."""
    from rpresolve import cmd_effect, workflows
    c = load_crops(crops)
    if not c.get("reviewed"):
        raise Refused("crops.json is not reviewed: read the review sheet, fix any centre in the "
                      "overrides, then run `vertical review`. A face planner can pick a bystander.")
    if not timeline.endswith(AUTO):
        raise Refused(f"'{timeline}' is not an [auto] timeline name. The 9:16 goes only onto "
                      "timelines these tools made.")
    pm, project, pin = api.pin_for_write(resolve, project_name, project_id)
    tl = _timeline_named(project, timeline)
    if tl is None:
        if not source_timeline:
            raise Refused(f"no timeline '{timeline}'; give source_timeline to duplicate it from")
        _, base = api.find_timeline(project, source_timeline)
    else:
        base = tl  # source_timeline is only the seed for a missing target; a re-run writes the copy in place
        if _timeline_size(tl) != OUT_SIZE:
            raise Refused(f"'{timeline}' exists at {_timeline_size(tl)}; an existing timeline's "
                          "size is never changed. Name a new target, or set 1080x1920 by hand.")
    tracks = int(base.GetTrackCount("video") or 0)
    if not 1 <= track <= tracks:
        raise Refused(f"'{base.GetName()}' has {tracks} video track(s).")
    raw = api._safe_call(base, "GetItemListInTrack", "video", track) or []
    positions = api.clip_positions(raw)
    items = [raw[p - 1] for p in positions]
    skipped = api.skipped_items(raw, track, positions)
    if track > 1:
        below = api._safe_call(base, "GetItemListInTrack", "video", track - 1) or []
        skipped += api.skipped_items(below, track - 1, api.clip_positions(below))
    if len(items) != len(c["items"]):
        raise Refused(f"'{base.GetName()}' V{track} has {len(items)} item(s), crops.json has "
                      f"{len(c['items'])}{api.skipped_text(skipped)}")
    targets, refused, kept = [], [], []
    for n, (it, crop) in enumerate(zip(items, c["items"]), 1):
        row, comps = _item_plan(it, crop, n, text_size_override, text_cap)
        (refused if row["reasons"] else targets).append(row)
        kept.append(comps)
    if refused:
        raise Refused("; ".join(f"#{r['index']} {r['name']} {'; '.join(r['reasons'])}"
                                for r in refused))
    plates, unframed = _plates_below(base, track, items, c["items"])
    sha = plan_sha("vertical", pin.unique_id, timeline, base.GetUniqueId(), tl is None, track,
                   [(t["index"], t["name"], t["props"], t["comps"]) for t in targets],
                   *(([(x["slot"], x["name"], x["under"], x["props"]) for x in plates], unframed)
                     if plates or unframed else ()))
    out = {"project": {"name": pin.name, "id": pin.unique_id},
           "timeline": {"name": timeline, "track": track, "size": list(OUT_SIZE)},
           "source_timeline": base.GetName() if tl is None else None, "create": tl is None,
           "targets": targets, "plates": plates, "unframed_below": unframed,
           "skipped": skipped,
           "unmoved_comps": _unmoved(targets, items), "results": [], "plate_results": [],
           "plan_sha": sha, "dry_run": dry_run, "exit_status": 0}
    if dry_run:
        return out
    if expect_sha and expect_sha != sha:
        raise Refused("the plan changed since the dry run (the items, crops or timelines "
                      "differ); run the dry run again and review it")
    if tl is None:
        out["duplicate"] = workflows.duplicate_auto(
            resolve, project_name, source_timeline, new_name=timeline, dry_run=False,
            project_id=project_id)
        if out["duplicate"]["mismatches"]:
            out["exit_status"] = 1
            return out
        project = pin.check(pm)
        tl = _timeline_named(project, timeline)
        with api.UISnapshot(resolve, project):
            project.SetCurrentTimeline(tl)
            tl.SetSetting("useCustomSettings", "1")
            api.set_setting_checked(tl, "timelineResolutionWidth", str(OUT_SIZE[0]))
            api.set_setting_checked(tl, "timelineResolutionHeight", str(OUT_SIZE[1]))
        out["created_size"] = list(_timeline_size(tl))
        items = api.clip_items(tl, track)
        if len(items) != len(targets):
            raise Refused(f"the copy has {len(items)} item(s), expected {len(targets)}")
        kept = [_comps(it) for it in items]
        out["unmoved_comps"] = _unmoved(targets, items)  # the copy's own comps
        below = api.clip_items(tl, track - 1) if plates else []
        if plates and [b.GetName() for b in below] != \
                [b.GetName() for b in api.clip_items(base, track - 1)]:
            raise Refused(f"the copy has {len(below)} clip(s) on V{track - 1}, the source differs")
    else:
        below = api.clip_items(tl, track - 1) if plates else []
    refresh = getattr(cmd_effect, "refresh", None)
    # under a 3840-wide current timeline every pan would land at 1080/3840 of the shift (T27)
    with api.timeline_current(resolve, project, tl):
        for t, it, comps in zip(targets, items, kept):
            if check_cancel:
                check_cancel()
            pin.check(pm)
            out["results"].append(_write_item(t, it, comps, refresh))
        for x in plates:
            if check_cancel:
                check_cancel()
            pin.check(pm)
            out["plate_results"].append(_write_item(x, below[x["slot"] - 1], [], refresh))
    out["exit_status"] = 1 if any(not r["ok"] for r in out["results"] + out["plate_results"]) else 0
    return out


def _write_item(t, it, comps, refresh):
    row = {"index": t["index"], "name": t["name"], "ok": False, "problems": [], "read_back": {},
           "comps": []}
    try:
        for k, v in t["props"].items():
            row["read_back"][k] = api.set_property_checked(it, k, v)
    except api.WriteNotApplied as e:
        row["problems"].append(str(e))
        return row
    for k in ("ZoomX", "ZoomY"):
        got = _num(api._safe_call(it, "GetProperty", k))
        row["read_back"][k] = got
        if not _close(got, t["zoom"], ZOOM_TOLERANCE):
            row["problems"].append(f"{k} reads {got}, expected {t['zoom']:g}")
    by_name = {c["comp"]: c for c in comps}
    for plan_ in t["comps"]:
        res = {"comp": plan_["comp"], "read_back": {}}
        c = by_name.get(plan_["comp"])  # by name, never by position: a copy may lose or reorder a comp
        if c is None:
            row["problems"].append(f"{plan_['comp']}: the copy has no comp of that name")
            row["comps"].append(res)
            continue
        comp = c["object"]
        if comp is None:
            row["problems"].append(f"{c['comp']}: the comp could not be opened")
            row["comps"].append(res)
            continue
        comp.Lock()
        try:
            for key, want in plan_["set"].items():
                tool_name, inp = key.split(".")
                tool = comp.FindTool(tool_name)
                if not tool:
                    row["problems"].append(f"{c['comp']}: no tool {tool_name}")
                    continue
                tool.SetInput(inp, {1: want[0], 2: want[1]} if isinstance(want, tuple) else want)
        finally:
            comp.Unlock()
        if refresh:
            res["refreshed"] = list(refresh(comp) or [])
        else:
            row["problems"].append(f"{c['comp']}: cmd_effect.refresh is missing; re-render the "
                                   "comp by hand")
        for key, want in plan_["set"].items():
            tool = comp.FindTool(key.split(".")[0])
            got = tool.GetInput(key.split(".")[1]) if tool else None
            got = _pt(got) if isinstance(want, tuple) else _num(got)
            res["read_back"][key] = got
            ok = (got is not None and all(_close(g, w) for g, w in zip(got, want))
                  if isinstance(want, tuple) else _close(got, want))
            if not ok:
                row["problems"].append(f"{c['comp']}: {key} reads {got}, expected {want}")
        row["comps"].append(res)
    row["ok"] = not row["problems"]
    return row


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------

def add_arguments(p):
    p.add_argument("action", choices=["plan", "draft", "review"],
                   help="plan: write crops.json and the review sheet; draft: cut the 9:16 preview "
                        "from crops.json; review: mark crops.json reviewed (do it after reading "
                        "the sheet and watching the preview)")
    p.add_argument("file", help="plan: the items JSON (a list, or {items: [...]}); draft and "
                                "review: crops.json")
    p.add_argument("--overrides", help="plan: JSON {\"<item name>\": cx, \"#<item number>\": cx}")
    p.add_argument("--out", help="plan: the folder for crops.json and review-NN.jpg, which must "
                                 "not exist yet (outside any git tree); draft: the folder for "
                                 "preview.mp4 (default: crops.json's folder)")
    p.add_argument("--centre", choices=["face", "edge"], default="edge",
                   help="plan: the edge rule only (default) or try the face planner first")
    p.add_argument("--no-sheet", action="store_true", help="plan: write crops.json only")


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run(args):
    """vertical plan ITEMS --out DIR [--overrides FILE] [--centre face] [--no-sheet]; vertical
    draft DIR/crops.json [--out DIR]; vertical review DIR/crops.json. Offline. The Resolve
    write is the MCP vertical_apply tool."""
    try:
        if args.action == "review":
            mark_reviewed(args.file)
            print(f"{args.file}: reviewed")
            return 0
        if args.action == "draft":
            path = draft_preview(args.file, args.out or os.path.dirname(os.path.abspath(args.file)))
            print(f"Wrote {path}. Send it to the operator and ask for the items to move, then: "
                  f"vertical review {args.file}", file=sys.stderr)
            return 0
        if not args.out:
            print("ERROR: plan needs --out", file=sys.stderr)
            return 1
        out = os.path.abspath(args.out)
        if os.path.exists(out):
            print(f"ERROR: --out {args.out} already exists. vertical plan makes the folder "
                  "itself, so give a path that does not exist yet.", file=sys.stderr)
            return 1
        problem = rppaths.out_problem(out, any_git_tree=True)
        if problem:
            print(f"ERROR: {problem}", file=sys.stderr)
            return 1
        items = _load(args.file)
        crops = plan(items["items"] if isinstance(items, dict) else items,
                     overrides=_load(args.overrides) if args.overrides else None, mode=args.centre)
        os.makedirs(out)
        try:
            if not args.no_sheet:
                crops["sheets"] = [os.path.basename(s) for s in review_sheets(crops, out)]
            path = os.path.join(out, "crops.json")
            rppaths.write_private(path, json.dumps(crops, indent=1) + "\n")
        except BaseException:  # the folder is ours and must not exist for a re-run
            shutil.rmtree(out, ignore_errors=True)
            raise
    except (VerticalError, OSError, ValueError, KeyError, subprocess.SubprocessError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    for r in crops["items"]:
        print(f"#{r['index']:<3} {r['name']:<24} cx {r['cx']:.4f}  {r['how']:<5} {r['status']}"
              + (f"  | {r['reason']}" if r["reason"] else ""))
    print(f"Wrote {path}. Read the sheet(s), then: vertical draft {path}, then vertical review "
          f"{path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    cli.script(__name__)
