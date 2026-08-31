"""M-LSD: a learned line segment detector, as an alternative front end.

Why: LSD returns *fragments*.  On a real barn at 1600 px analysis resolution it
produced 4823 raw segments, of which 503 survived filtering, with a **median
length of 56 px** -- 3.5 % of the long edge -- and only 29 segments longer than
a tenth of the short edge.  A vanishing point estimated from hundreds of 3 %
fragments is intrinsically noisier than one estimated from a few dozen long
structural edges, because a line's angular precision scales with its length.

M-LSD is trained on wireframe data and returns few, long, structural segments
instead.  It is small enough not to be a burden: the 512 tiny model is 2.5 MB
and the large 6.1 MB, both Apache-2.0, both vendored in ``models/``.

    Gu et al., "Towards Real-time and Light-weight Line Segment Detection",
    AAAI 2022.  https://github.com/navervision/mlsd

The decoding below follows the reference implementation's `pred_lines`
(Apache-2.0, NAVER Corp.) -- see models/LICENSE.mlsd.  Requires a TFLite
runtime, which is an optional dependency: ``pip install tflite-runtime``.
"""
from __future__ import annotations

import os
import threading

import cv2
import numpy as np

_LOCK = threading.Lock()
_CACHE = {}

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLED = os.path.normpath(os.path.join(HERE, "..", "..", "models"))
DEFAULT_MODEL = "M-LSD_512_large_fp32.tflite"


class MLSDUnavailable(RuntimeError):
    pass


def _interpreter_class():
    try:
        from tflite_runtime.interpreter import Interpreter
        return Interpreter
    except Exception:
        pass
    try:                                   # the renamed successor package
        from ai_edge_litert.interpreter import Interpreter
        return Interpreter
    except Exception:
        pass
    try:                                   # full TensorFlow, if it happens to be there
        import tensorflow as tf
        return tf.lite.Interpreter
    except Exception as exc:
        raise MLSDUnavailable(
            "M-LSD needs a TFLite runtime: pip install tflite-runtime"
        ) from exc


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
    raise MLSDUnavailable(f"M-LSD model not found: {path or DEFAULT_MODEL}")


def _load(path: str):
    """One interpreter per model path per process.

    Reloading a 6 MB model for every file in a batch is pure waste, and the
    interpreter is not thread safe, hence the lock around inference too.
    """
    with _LOCK:
        if path not in _CACHE:
            Interpreter = _interpreter_class()
            interp = Interpreter(model_path=path)
            interp.allocate_tensors()
            _CACHE[path] = (interp, interp.get_input_details(),
                            interp.get_output_details())
        return _CACHE[path]


def detect(bgr: np.ndarray, model_path: str = "", score_thr: float = 0.10,
           dist_thr: float = 20.0) -> np.ndarray:
    """Line segments as ``(N, 4)`` = ``x0, y0, x1, y1`` in ``bgr``'s coordinates."""
    path = resolve_model(model_path)
    interp, inp, out = _load(path)

    size = int(inp[0]["shape"][1])
    h, w = bgr.shape[:2]
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    resized = cv2.resize(rgb, (size, size), interpolation=cv2.INTER_AREA)
    # the network takes RGBA with a constant alpha; the values stay in 0..255
    batch = np.concatenate(
        [resized, np.ones((size, size, 1), dtype=resized.dtype) * 255], axis=-1
    ).astype(np.float32)[None, ...]

    with _LOCK:
        interp.set_tensor(inp[0]["index"], batch)
        interp.invoke()
        pts = interp.get_tensor(out[0]["index"])[0]
        scores = interp.get_tensor(out[1]["index"])[0]
        vmap = interp.get_tensor(out[2]["index"])[0]

    start, end = vmap[:, :, :2], vmap[:, :, 2:]
    dist = np.sqrt(np.sum((start - end) ** 2, axis=-1))

    keep = scores > score_thr
    if not np.any(keep):
        return np.zeros((0, 4))
    ys, xs = pts[keep, 0].astype(int), pts[keep, 1].astype(int)
    ok = dist[ys, xs] > dist_thr
    if not np.any(ok):
        return np.zeros((0, 4))
    ys, xs = ys[ok], xs[ok]
    d = vmap[ys, xs, :]
    seg = np.column_stack([xs + d[:, 0], ys + d[:, 1], xs + d[:, 2], ys + d[:, 3]])

    # the displacement map is half the network's input resolution
    seg *= 2.0
    seg[:, 0] *= w / float(size)
    seg[:, 2] *= w / float(size)
    seg[:, 1] *= h / float(size)
    seg[:, 3] *= h / float(size)
    return seg


def available(model_path: str = "") -> bool:
    try:
        resolve_model(model_path)
        _interpreter_class()
        return True
    except Exception:
        return False
