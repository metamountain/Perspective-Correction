"""DeepLSD: a learned *refinement* of LSD, as a third front end.

Where M-LSD replaces LSD with a wireframe network -- few long segments, coarse
endpoints -- DeepLSD keeps LSD's sub-pixel endpoint fitting and replaces only
the thing LSD is bad at: deciding *where* a line is in the first place.  The
network regresses a distance field and an angle field over the image, and those
two fields are handed to LSD in place of the raw image gradient.  So the
endpoints still come from a least-squares fit on a support region, which is the
half of LSD this project measured as its strength ("long and coarse loses to
short and sharp", CLAUDE.md), while the support regions come from a network
that has seen what a building edge looks like through foliage and JPEG blocking.

    Pautrat et al., "DeepLSD: Line Segment Detection and Refinement with
    Deep Image Gradients", CVPR 2023.  https://github.com/cvg/DeepLSD (MIT).

**Three optional pieces, and none of them ship here.**  torch, the `deeplsd`
package itself (it is not on PyPI -- it is a checkout), and `pytlsd`, which is a
C++ extension that has no wheels and builds from source.  That is a heavy chain
next to numpy + OpenCV + Pillow, which is exactly why this is a selectable
detector and not a default, and why every failure below names the missing piece
rather than saying "no backend".

Weights are ~98 MB and are *not* vendored (`models/` is gitignored for them):

    curl -L -o models/deeplsd_md.tar https://cvg-data.inf.ethz.ch/DeepLSD/deeplsd_md.tar

`deeplsd_md` is trained on MegaDepth and is the outdoor/generic model, which is
what architectural photography is.  `deeplsd_wireframe` is the indoor one.
"""
from __future__ import annotations

import os
import threading

import cv2
import numpy as np

_LOCK = threading.Lock()
_CACHE = {}

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
BUNDLED = os.path.join(ROOT, "models")
CHECKOUT = os.path.join(ROOT, "tools", "DeepLSD")
DEFAULT_MODEL = "deeplsd_md.tar"
WEIGHTS_URL = "https://cvg-data.inf.ethz.ch/DeepLSD/deeplsd_md.tar"


class DeepLSDUnavailable(RuntimeError):
    pass


def _import_deeplsd():
    """The `deeplsd` package, from an install or from `tools/DeepLSD`.

    The checkout is added to ``sys.path`` rather than installed because it is a
    research repository with a training pipeline attached; the four modules
    inference needs import cleanly on their own, and installing it would put
    its `configs/` and `scripts/` on the path of every process that imports
    this package.
    """
    try:
        from deeplsd.models.deeplsd_inference import DeepLSD  # noqa: F401
        return DeepLSD
    except ImportError:
        pass
    if os.path.isdir(os.path.join(CHECKOUT, "deeplsd")):
        import sys
        if CHECKOUT not in sys.path:
            sys.path.insert(0, CHECKOUT)
        try:
            from deeplsd.models.deeplsd_inference import DeepLSD
            return DeepLSD
        except ImportError as exc:
            raise DeepLSDUnavailable(
                f"the DeepLSD checkout is there but will not import: {exc}. "
                "It needs torch, omegaconf, scikit-image and pytlsd "
                "(pip install omegaconf scikit-image pytlsd; pytlsd builds "
                "from source and wants cmake and a C++ compiler)") from exc
    raise DeepLSDUnavailable(
        "DeepLSD is not installed. Clone it beside the tools folder:\n"
        f"    git clone https://github.com/cvg/DeepLSD {CHECKOUT}\n"
        "    pip install omegaconf scikit-image pytlsd")


def resolve_model(path: str = "") -> str:
    if path and os.path.isfile(path):
        return path
    if path and os.path.isdir(path):
        cand = os.path.join(path, DEFAULT_MODEL)
        if os.path.isfile(cand):
            return cand
    cand = os.path.join(BUNDLED, path or DEFAULT_MODEL)
    if os.path.isfile(cand):
        return cand
    raise DeepLSDUnavailable(
        f"DeepLSD weights not found: {path or DEFAULT_MODEL}. They are not "
        f"bundled (98 MB):\n    curl -L -o models/{DEFAULT_MODEL} {WEIGHTS_URL}")


def _pick_device(device: str = ""):
    import torch
    if device:
        return torch.device(device)
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _load(path: str, device: str = "", grad_nfa: bool = True):
    """One network per (weights, device, setting), because a batch reloads
    otherwise -- 98 MB and a CUDA context per photograph."""
    import torch
    DeepLSD = _import_deeplsd()
    key = (path, device, bool(grad_nfa))
    with _LOCK:
        if key not in _CACHE:
            conf = {
                "detect_lines": True,
                "line_detection_params": {
                    "merge": False,
                    # the estimator here is length-weighted and wants many
                    # independent measurements, not a few merged ones; the
                    # merge experiment in CLAUDE.md is the same argument
                    "filtering": True,
                    "grad_thresh": 3,
                    "grad_nfa": bool(grad_nfa),
                },
            }
            ckpt = torch.load(str(path), map_location="cpu", weights_only=False)
            net = DeepLSD(conf)
            net.load_state_dict(ckpt["model"])
            _CACHE[key] = net.to(_pick_device(device)).eval()
        return _CACHE[key]


def detect(bgr: np.ndarray, model_path: str = "", device: str = "",
           grad_nfa: bool = True) -> np.ndarray:
    """Line segments as ``(N, 4)`` = ``x0, y0, x1, y1`` in ``bgr``'s coordinates."""
    import torch
    path = resolve_model(model_path)
    net = _load(path, device, grad_nfa)
    dev = _pick_device(device)

    gray = bgr if bgr.ndim == 2 else cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    x = torch.tensor(gray, dtype=torch.float, device=dev)[None, None] / 255.0
    with _LOCK, torch.no_grad():
        out = net({"image": x})
    lines = np.asarray(out["lines"][0], dtype=float)   # (N, 2, 2), (x, y) pairs
    if lines.size == 0:
        return np.zeros((0, 4))
    return lines.reshape(-1, 4)


def available(model_path: str = "", device: str = "") -> bool:
    try:
        resolve_model(model_path)
        _import_deeplsd()
        import torch  # noqa: F401
        return True
    except Exception:
        return False


def describe(model_path: str = "", device: str = "") -> str:
    """One line for ``--detector-info`` and the diagnostics header."""
    parts = []
    try:
        parts.append(f"weights: {resolve_model(model_path)}")
    except DeepLSDUnavailable as exc:
        parts.append(str(exc).splitlines()[0])
    try:
        _import_deeplsd()
        parts.append("package: ok")
    except DeepLSDUnavailable as exc:
        parts.append(str(exc).splitlines()[0])
    try:
        import torch
        parts.append(f"torch {torch.__version__} on {_pick_device(device)}")
    except Exception:
        parts.append("torch: not importable")
    return "; ".join(parts)
