"""Loading and saving with metadata kept intact.

Two things here are easy to get wrong and ruin a batch run:

*   **EXIF orientation.**  A photo shot in portrait is usually stored landscape
    with an orientation tag.  Detecting lines on the stored pixels would look
    for verticals along the wrong axis.  So the tag is applied on load, and the
    tag is then reset to 1 on save -- otherwise the viewer would rotate the
    already-rotated result a second time.
*   **The focal length.**  ``FocalLengthIn35mmFilm`` is the tag that can be used
    directly; plain ``FocalLength`` is in millimetres on an unknown sensor and
    is useless without the crop factor, so it is only used when the 35 mm tag is
    missing and a crop factor can be inferred from the two together.
"""
from __future__ import annotations

import io
import math
import os

import cv2
import numpy as np
from PIL import Image, ImageOps

try:
    import piexif
except Exception:                                    # pragma: no cover
    piexif = None

READABLE = {".jpg", ".jpeg", ".jpe", ".png", ".tif", ".tiff", ".bmp", ".webp"}


class Loaded:
    __slots__ = ("bgr", "exif_bytes", "icc", "focal_35mm", "orientation", "fmt", "path")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def _focal_35mm(exif_dict):
    if not exif_dict:
        return None
    ex = exif_dict.get("Exif", {}) if isinstance(exif_dict, dict) else {}
    if piexif is None:
        return None
    v = ex.get(piexif.ExifIFD.FocalLengthIn35mmFilm)
    if v:
        try:
            f = float(v)
            if 4.0 < f < 400.0:
                return f
        except Exception:
            pass
    return None


def load(path: str) -> Loaded:
    im = Image.open(path)
    fmt = (im.format or "").upper()
    orientation = 1
    exif_bytes = None
    icc = im.info.get("icc_profile")
    focal = None

    raw = im.info.get("exif")
    if raw and piexif is not None:
        try:
            d = piexif.load(raw)
            orientation = int(d.get("0th", {}).get(piexif.ImageIFD.Orientation, 1) or 1)
            focal = _focal_35mm(d)
            exif_bytes = raw
        except Exception:
            exif_bytes = raw

    im = ImageOps.exif_transpose(im)               # honour the orientation tag
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    arr = np.asarray(im)
    if arr.ndim == 2:
        bgr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    else:
        bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    return Loaded(bgr=bgr, exif_bytes=exif_bytes, icc=icc, focal_35mm=focal,
                  orientation=orientation, fmt=fmt, path=path)


def focal_px_from_exif(src: Loaded, w: int, h: int):
    if not src.focal_35mm:
        return None
    return float(src.focal_35mm) * math.hypot(w, h) / math.hypot(36.0, 24.0)


def _fixed_exif(exif_bytes, w, h):
    """Reset orientation, update the recorded pixel dimensions, drop the stale
    thumbnail (it no longer matches the picture)."""
    if not exif_bytes or piexif is None:
        return exif_bytes
    try:
        d = piexif.load(exif_bytes)
        d.setdefault("0th", {})[piexif.ImageIFD.Orientation] = 1
        ex = d.setdefault("Exif", {})
        ex[piexif.ExifIFD.PixelXDimension] = int(w)
        ex[piexif.ExifIFD.PixelYDimension] = int(h)
        d["thumbnail"] = None
        d["1st"] = {}
        return piexif.dump(d)
    except Exception:
        return None


def save(path: str, bgr: np.ndarray, src: Loaded, settings):
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    im = Image.fromarray(rgb)
    ext = os.path.splitext(path)[1].lower()
    kw = {}
    if settings.keep_exif:
        ex = _fixed_exif(src.exif_bytes, im.width, im.height)
        if ex:
            kw["exif"] = ex
        if src.icc:
            kw["icc_profile"] = src.icc
    if ext in (".jpg", ".jpeg", ".jpe"):
        kw.update(quality=int(settings.jpeg_quality), subsampling=0, optimize=True)
    elif ext == ".png":
        kw.update(compress_level=6)
    elif ext in (".tif", ".tiff"):
        kw.update(compression="tiff_lzw")
    # write to a temporary name and rename, so an interrupted run never leaves a
    # half-written file where a photograph used to be.  Pillow infers the format
    # from the extension, and ".part" is not one, so it has to be passed
    # explicitly -- otherwise every save raises "unknown file extension".
    fmt = {".jpg": "JPEG", ".jpeg": "JPEG", ".jpe": "JPEG", ".png": "PNG",
           ".tif": "TIFF", ".tiff": "TIFF", ".bmp": "BMP", ".webp": "WEBP"}.get(ext)
    if fmt is None:
        raise ValueError(f"unsupported output extension: {ext or '(none)'}")
    tmp = path + ".part"
    im.save(tmp, format=fmt, **kw)
    os.replace(tmp, path)


def copy_through(src_path: str, dst_path: str):
    """Byte-for-byte copy, used when an image is skipped but an output is wanted."""
    if os.path.abspath(src_path) == os.path.abspath(dst_path):
        return
    # the corrected path creates the output folder before writing; the skipped
    # path has to as well, or `rectify.py folder -o newfolder` dies on the first
    # image it declines to touch
    os.makedirs(os.path.dirname(os.path.abspath(dst_path)) or ".", exist_ok=True)
    with open(src_path, "rb") as f_in, open(dst_path + ".part", "wb") as f_out:
        while True:
            chunk = f_in.read(1 << 20)
            if not chunk:
                break
            f_out.write(chunk)
    os.replace(dst_path + ".part", dst_path)


def analysis_gray(bgr: np.ndarray, max_edge: int):
    """Grayscale copy for detection, downscaled, with the scale factor.

    Detection runs on a reduced image for speed; every geometric quantity is
    scaled back to full resolution afterwards.  Because the model is expressed
    as angles plus a focal length in pixels, the only thing that needs scaling
    is the focal length -- angles are scale invariant, which is a good reason to
    keep the model in that form.
    """
    h, w = bgr.shape[:2]
    s = min(1.0, float(max_edge) / max(w, h))
    if s < 1.0:
        small = cv2.resize(bgr, (max(1, int(round(w * s))), max(1, int(round(h * s)))),
                           interpolation=cv2.INTER_AREA)
    else:
        small = bgr
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return gray, float(gray.shape[1]) / w
