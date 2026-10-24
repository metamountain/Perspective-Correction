"""A learned focal-length guess from GeoCalib (optional, needs torch).

User, 2026-10-24: "lass uns geo auch noch draufpacken, aber nur installieren
wenn erfolgversprechend". Measured before it was built (55 cases with a known
focal: 48 rendered cube streets plus 7 photographs with EXIF, EXIF ignored):

- where the vanishing points decide the focal (22 cases) GeoCalib is worse
  than the geometry -- |log f err| 0.27 against 0.07 -- so it is not asked;
- where only the priors decide (33 cases: frontal facades, the f : tilt
  ambiguity) the geometric mean of the estimator's guess and GeoCalib's cut
  the error from 0.27 to 0.18 (p90 0.60 -> 0.39).

So `model.estimate` calls :func:`focal_px` only when nothing geometric decided
and blends, never replaces. ~1.5 s on a CPU at GeoCalib's 320 px.

GeoCalib (github.com/cvg/GeoCalib, code Apache-2.0, weights CC BY 4.0, see
CREDITS) imports kornia and torchvision at module level but uses them only to
resize its input; when they are absent a torch-only stand-in is registered so
the install stays GeoCalib + its 116 MB weights on top of torch.

Never raises: no GeoCalib, no weights or a failure is ``None`` and the
estimator carries on as it did without it.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types

from .depinstall import (GEOCALIB_URL as PACKAGE_URL, GEOCALIB_WEIGHTS as WEIGHTS,
                         GEOCALIB_WEIGHTS_SIZE as WEIGHTS_SIZE, geocalib_weights_ok)

_model = None
_cache = {}
LAST_NOTE = ""


def _present(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def available():
    """Installed and its weights on disk -- without importing torch.

    ``PC_LEARNED_FOCAL=0`` turns it off: the test runner sets it, so results
    do not depend on whether this machine happens to have GeoCalib (CI does
    not), and so parallel workers do not each load torch (measured: 11
    workers, 13 GB, a fast run past 10 minutes)."""
    if os.environ.get("PC_LEARNED_FOCAL", "1") == "0":
        return False
    return _present("geocalib") and _present("torch") and geocalib_weights_ok()


def _stand_ins():
    """Register the stand-ins; returns the names that were added, so `_load`
    can take them out again once GeoCalib holds its own references. Left in
    `sys.modules` an empty ``torchvision`` (no ``__spec__``) made transformers
    crash on import -- SAM failed with "torchvision.__spec__ is None" whenever
    GeoCalib had run first (2026-10-24)."""
    added = [n for n in ("kornia", "kornia.geometry", "torchvision") if n not in sys.modules]
    if not _present("kornia"):
        import torch.nn.functional as F

        def resize(img, size, side="short", antialias=True, align_corners=None,
                   interpolation="bilinear"):
            return F.interpolate(img, size=tuple(int(s) for s in size), mode=interpolation,
                                 antialias=antialias, align_corners=False)
        kornia = types.ModuleType("kornia")
        kornia.geometry = types.ModuleType("kornia.geometry")
        kornia.geometry.transform = types.SimpleNamespace(resize=resize)
        sys.modules.setdefault("kornia", kornia)
        sys.modules.setdefault("kornia.geometry", kornia.geometry)
    if not _present("torchvision"):
        sys.modules.setdefault("torchvision", types.ModuleType("torchvision"))
    return [n for n in added if n in sys.modules]


def _load():
    global _model
    if _model is None:
        import torch
        added = _stand_ins()
        try:
            from geocalib import GeoCalib
        finally:
            for name in added:
                sys.modules.pop(name, None)
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        _model = GeoCalib(weights=WEIGHTS).to(dev).eval()
        _model._pc_device = dev
    return _model


def focal_px(bgr):
    """GeoCalib's focal length for ``bgr`` in its own pixels, or None."""
    global LAST_NOTE
    key = (bgr.shape, bgr[::max(1, bgr.shape[0] // 16), ::max(1, bgr.shape[1] // 16)].tobytes())
    if key in _cache:
        return _cache[key]
    f = None
    try:
        if available():
            import cv2
            import torch
            model = _load()
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            t = torch.from_numpy(rgb).permute(2, 0, 1).float().div(255.0).to(model._pc_device)
            with torch.no_grad():
                cam = model.calibrate(t)["camera"]
            fy = cam.f.reshape(-1)[-1]
            f = float(fy)
            if not (f > 0):
                f = None
            LAST_NOTE = ""
    except Exception as exc:                    # optional: never break a photograph
        LAST_NOTE = f"GeoCalib failed: {type(exc).__name__}: {exc}"
        f = None
    _cache[key] = f
    return f


def provider(bgr, scale, settings):
    """The ``learned_focal`` callable `model.estimate` takes, or None.

    ``scale`` converts this photograph's pixels to the analysis image's. Lazy,
    so the 1.5 s are only spent when the estimator actually asks."""
    if not getattr(settings, "learned_focal", True) or not available():
        return None

    def get():
        f = focal_px(bgr)
        return f * scale if f else None
    return get
