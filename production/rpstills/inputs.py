"""rpstills.inputs: read a still the way finish and audit measure it, upright and in sRGB.

    rgb, info = inputs.read_upright(path)    # float32 HxWx3 sRGB 0..1, and what was done to get it

Pixels are decoded by Pillow (8-bit), or by OpenCV for what Pillow would cut
to 8 bits (16-bit TIFF). Then:

  colour       an embedded RGB ICC profile that is not sRGB (Display P3,
               Adobe RGB, ProPhoto) is converted to sRGB with Pillow's
               ImageCms, relative colorimetric. A file with no profile is
               taken as sRGB, and info["colour"] says so. A 16-bit file
               with such a profile is converted at 8 bits (the row says so);
               a 16-bit sRGB or untagged one keeps its 16 bits. A profile
               that is not RGB is not converted and is named in info["colour"].
  orientation  the EXIF/TIFF orientation tag (1 to 8) is applied, turning or
               flipping the pixels upright, the picture Vision sees. A bad
               or missing tag is 1.

info: {"orientation": tag, "orientation_applied": tag != 1, "icc": profile
description or None, "srgb_icc": the embedded profile is sRGB, "colour":
one line for the row}.
"""

import io

import cv2
import numpy as np


def upright(a, o):
    """a (H, W[, C]) turned or flipped upright for EXIF orientation o (1 to 8)."""
    if o == 2:
        return a[:, ::-1]
    if o == 3:
        return a[::-1, ::-1]
    if o == 4:
        return a[::-1]
    if o in (5, 6, 7, 8):
        t = a.swapaxes(0, 1)                        # 5: transpose
        return {5: t, 6: t[:, ::-1], 7: t[::-1, ::-1], 8: t[::-1]}[o]
    return a


def _decode(path):
    """(uint8 or uint16 RGB array, orientation tag, ICC bytes or None, turned): turned is True when the decoder
    already applied the tag. Pillow decodes what it can; a file it cannot open (16-bit RGB TIFF) goes to OpenCV.
    Both turn a TIFF by its tag while decoding (Pillow 10 and OpenCV 4.10, whatever flag OpenCV is given) and
    leave a JPEG as stored, so the tag is applied here to JPEGs only. test_rpstills_inputs pins that."""
    from PIL import Image
    tiff = path.lower().endswith((".tif", ".tiff"))
    o, icc = 1, None
    try:
        with Image.open(path) as im:
            o = int(im.getexif().get(0x0112) or 1)
            o = o if 1 <= o <= 8 else 1
            icc = im.info.get("icc_profile")
            bits = np.max(im.tag_v2.get(258, 8)) if tiff else 8  # Pillow opens a 16-bit RGB TIFF as 8-bit RGB
            if bits <= 8 and im.mode in ("RGB", "RGBA", "L", "LA", "P"):
                return np.asarray(im.convert("RGB")), o, icc, tiff
    except (OSError, ValueError, TypeError):
        pass
    a = cv2.imread(path, cv2.IMREAD_UNCHANGED | cv2.IMREAD_IGNORE_ORIENTATION)
    if a is None or a.dtype not in (np.uint8, np.uint16):
        raise ValueError("unreadable image (8- or 16-bit JPEG or TIFF wanted)")
    if a.ndim == 2:
        a = np.repeat(a[..., None], 3, 2)
    return cv2.cvtColor(np.ascontiguousarray(a[..., :3]), cv2.COLOR_BGR2RGB), o, icc, tiff


def to_srgb8(rgb, icc):
    """(RGB, colour line, profile description, is sRGB) from RGB tagged with icc bytes: the same array when no
    conversion is needed, else uint8 in sRGB (a uint16 source is taken to 8 bits first). ValueError when the
    profile cannot be used."""
    from PIL import Image, ImageCms
    try:
        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        name = ImageCms.getProfileDescription(src).strip() or "unnamed"
        if "srgb" in name.lower():
            return rgb, "sRGB profile", name, True
        if src.profile.xcolor_space.strip() != "RGB":
            return rgb, f"profile {name} is not RGB, taken as sRGB", name, False
        if rgb.dtype == np.uint16:  # saturating scale in OpenCV: no float64 copy of the frame
            rgb = cv2.convertScaleAbs(rgb, alpha=255 / 65535)
        out = ImageCms.profileToProfile(Image.fromarray(rgb), src, ImageCms.createProfile("sRGB"), outputMode="RGB",
                                        renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC)
        return np.asarray(out), f"converted from {name} to sRGB", name, False
    except (ImageCms.PyCMSError, OSError) as e:
        raise ValueError(f"cannot convert its ICC profile to sRGB ({e})") from None


def read_upright(path):
    """(float32 HxWx3 sRGB 0..1 upright, info). ValueError when the file cannot be read or its profile used."""
    rgb, o, icc, turned = _decode(path)
    info = {"orientation": o, "orientation_applied": o != 1, "icc": None, "srgb_icc": False,
            "colour": "no profile, taken as sRGB"}
    if icc:
        deep = rgb.dtype == np.uint16
        out8, colour, info["icc"], info["srgb_icc"] = to_srgb8(rgb, icc)
        info["colour"] = colour
        if colour.startswith("converted"):
            rgb = out8
            if deep:
                info["colour"] += " (16-bit source taken to 8-bit for the conversion)"
    out = (rgb if turned else upright(rgb, o)).astype(np.float32, order="C")
    out /= np.iinfo(rgb.dtype).max
    return out, info
