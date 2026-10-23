"""lutlab.matte: a person matte for lutlab balance, so the solve reads the person and not the room.

    --matte vision|grabcut|FILE|FOLDER

A matte is a float HxW 0..1 the size of the frame, 1 on the person whose face the solve reads. Sources:
  vision    macOS Vision foreground instance under the face (rpstills person_matte helper): 0.2 s a frame at
            2400 px, soft hair edge, takes one person out of a group, deterministic. Needs macOS and swiftc.
  grabcut   rpstills.background.subject_mask: headless and seeded, but it follows the face box and torso
            seed rather than the person, and leaks into a wall or takes a second person; the fallback.
  FILE      one grey image (or its alpha), the same for every frame of a locked-off shot.
  FOLDER    one image per frame, matched by file stem (a Resolve Depth Map or Magic Mask export, thresholded
            to a matte by the caller). 16-bit and RGB images are read as luminance.
Resolve's own Depth Map is a soft depth, not a segmentation (a nearer second person is brighter than the
subject), and Magic Mask needs a click per clip and cannot be exported through the API: both reach here as
FILE or FOLDER. Numbers in production/lutlab-balance.md."""

import os
import tempfile
from pathlib import Path

import numpy as np

SOURCES = ("vision", "grabcut")
IMAGE_EXTS = (".png", ".tif", ".tiff", ".jpg", ".jpeg")


class MatteError(ValueError):
    pass


def read_matte(path, size):
    """float32 (h, w) 0..1 from an image file (alpha when it has one), resized to size = (h, w)."""
    import cv2
    im = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if im is None:
        raise MatteError(f"{path}: not a readable image")
    if im.ndim == 3:
        im = im[..., 3] if im.shape[2] == 4 else im[..., :3].mean(-1)
    im = im.astype(np.float32)
    im /= 65535.0 if im.max() > 255 else 255.0 if im.max() > 1 else 1.0
    return np.clip(cv2.resize(im, (size[1], size[0]), interpolation=cv2.INTER_LINEAR), 0, 1)


def _vision(frames, space, dets):
    import cv2
    from . import balance
    from rpstills import vision
    with tempfile.TemporaryDirectory() as tmp:
        jobs = []
        for i, ((_, code), f) in enumerate(zip(frames, dets)):
            if f is not None:
                src = os.path.join(tmp, f"{i}.png")
                cv2.imwrite(src, np.round(balance.display(code, space)[..., ::-1] * 255).astype(np.uint8))
                jobs.append((src, os.path.join(tmp, f"{i}.matte.png"), f["x"] + f["w"] / 2, f["y"] + f["h"] / 2, i))
        ok = vision.person_mattes([j[:4] for j in jobs])
        if ok is None:
            raise MatteError("the Vision matte helper cannot run (it needs macOS and swiftc): "
                             "pass --matte grabcut or a matte file")
        out = [None] * len(frames)
        for j, good in zip(jobs, ok):
            if good:
                out[j[4]] = read_matte(j[1], frames[j[4]][1].shape[:2])
    return out


def _grabcut(frames, space, dets):
    from . import balance
    from rpstills import background, skin
    out = []
    for (_, code), f in zip(frames, dets):
        m = None
        if f is not None:
            H, W = code.shape[:2]
            m, _ = background.subject_mask(balance.display(code, space).astype(np.float32), skin.to_pixels(f, W, H))
        out.append(m)
    return out


def matter(spec):
    """frames, space, dets -> [matte or None per frame] for a --matte spec; MatteError when it cannot be read."""
    if spec == "vision":
        return _vision
    if spec == "grabcut":
        return _grabcut
    p = Path(spec)
    if p.is_file():
        return lambda frames, space, dets: [read_matte(p, c.shape[:2]) for _, c in frames]
    if p.is_dir():
        files = {f.stem: f for f in p.iterdir() if f.suffix.lower() in IMAGE_EXTS}

        def from_folder(frames, space, dets):
            out = []
            for label, c in frames:
                f = files.get(Path(label).stem)
                if f is None:
                    raise MatteError(f"{spec}: no matte named {Path(label).stem} for frame {label}")
                out.append(read_matte(f, c.shape[:2]))
            return out
        return from_folder
    raise MatteError(f"--matte {spec}: not vision, grabcut, an image or a folder of images")
