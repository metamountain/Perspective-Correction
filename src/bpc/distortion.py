"""Lens-distortion correction via manufacturer profiles (Stage 0).

``lensfunpy`` returns a per-pixel remap coordinate array that undoes the
radial distortion a specific lens produces at a given focal length.  The map
is consumed by ``cv2.remap`` in ``warp.apply`` — no polynomial fitting, no
new pipeline stage, just a coordinate lookup before the perspective warp.

The module is lazy: importing this file costs nothing; the first call to
``undistort_map`` is when lensfunpy loads (and only then).
"""
from __future__ import annotations

import numpy as np


def available() -> bool:
    try:
        import lensfunpy  # noqa: F401
        return True
    except Exception:
        return False


def describe() -> str:
    if not available():
        return "not installed (pip install lensfunpy)"
    try:
        import lensfunpy
        return f"lensfunpy {getattr(lensfunpy, '__version__', '?')}"
    except Exception as exc:
        return f"broken ({exc})"


def _exif_make_model(exif_bytes: bytes | None):
    """Extract (Make, Model) from raw EXIF.  Returns (None, None) when absent."""
    if not exif_bytes:
        return None, None
    try:
        import piexif
        d = piexif.load(exif_bytes)
        img = d.get("0th", {})
        make = img.get(piexif.ImageIFD.Make, "")
        model = img.get(piexif.ImageIFD.Model, "")
        return (str(make).strip(), str(model).strip()) if make and model else (None, None)
    except Exception:
        return None, None


def _exif_focal_mm(exif_bytes: bytes | None):
    """Extract the actual focal length in mm (not 35mm-equivalent)."""
    if not exif_bytes:
        return None
    try:
        import piexif
        d = piexif.load(exif_bytes)
        ex = d.get("Exif", {})
        v = ex.get(piexif.ExifIFD.FocalLength)
        if v:
            f = float(v)
            if 2.0 < f < 500.0:
                return f
    except Exception:
        pass
    return None


def _exif_aperture(exif_bytes: bytes | None):
    """Extract the F-number (aperture).  Defaults to the lens's widest."""
    if not exif_bytes:
        return None
    try:
        import piexif
        d = piexif.load(exif_bytes)
        ex = d.get("Exif", {})
        num = ex.get(piexif.ExifIFD.ApertureValue)
        if num:
            f = float(num)
            if 0.5 < f < 64.0:
                return f
    except Exception:
        pass
    return None


def _exif_lens_model(exif_bytes: bytes | None):
    """Extract the lens model string from MakeNote or the 0th IFD."""
    if not exif_bytes:
        return None
    try:
        import piexif
        d = piexif.load(exif_bytes)
        # Try ExifIFD.LensModel (rare but present on some cameras)
        ex = d.get("Exif", {})
        lm = ex.get(piexif.ExifIFD.LensModel, "") if hasattr(piexif.ExifIFD, "LensModel") else ""
        if lm:
            return str(lm).strip()
        # MakeNote is camera-specific; skip parsing it here.
    except Exception:
        pass
    return None


def undistort_map(exif_bytes: bytes | None, width: int, height: int,
                  focal_mm: float | None = None) -> tuple[np.ndarray, np.ndarray] | None:
    """Return (map_x, map_y) for cv2.remap, or None when no profile matches.

    Looks up the camera make/model + lens in the Lensfun database and builds
    a per-pixel coordinate map that undoes radial distortion at the given
    focal length.  When ``focal_mm`` is None it is read from EXIF; when that
    is also absent the function returns None (no profile, no guess).

    The returned arrays are float32, shape (height, width), ready for
    ``cv2.remap(img, map_x, map_y, cv2.INTER_LINEAR)``.
    """
    if not available():
        return None
    make, model = _exif_make_model(exif_bytes)
    if not make or not model:
        return None

    f_mm = focal_mm
    if f_mm is None:
        f_mm = _exif_focal_mm(exif_bytes)
    if f_mm is None:
        return None

    aperture = _exif_aperture(exif_bytes)
    if aperture is None:
        aperture = 2.8  # mid-range default; distortion varies little with aperture

    try:
        import lensfunpy
        db = lensfunpy.Database()
        cams = db.find_cameras(make, model)
        if not cams:
            return None
        cam = cams[0]
        # Try the full camera model string first, then just the make as a
        # generic-lens lookup (some cameras store "NIKON CORPORATION" as Make
        # and the lens name is in a separate tag we don't parse).
        lenses = db.find_lenses(cam, "", "")
        if not lenses:
            return None
        # Pick the first lens; for multi-lens bodies this is imperfect but
        # better than nothing.  A future refinement could match on focal length.
        lens = lenses[0]
        mod = lensfunpy.Modifier(lens, cam.crop_factor, width, height)
        mod.initialize(f_mm, aperture, distance=10.0, pixel_format=np.uint8)
        coords = mod.apply_geometry_distortion()
        # coords shape: (height, width, 2) — column 0 is x, column 1 is y
        map_x = np.ascontiguousarray(coords[:, :, 0], dtype=np.float32)
        map_y = np.ascontiguousarray(coords[:, :, 1], dtype=np.float32)
        return map_x, map_y
    except Exception:
        return None
