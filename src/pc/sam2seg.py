"""SAM2 box+point prompt segmentation for click-to-select masking.

The user drags a box over the building in the review window (primary prompt);
Shift+click adds positive points, Alt+click adds negative ones, and each gesture
asks SAM2 to segment what they mean.  The mask comes back as an ignore region
(True = outside the selection) that ``review.apply_sam_mask`` merges with the
paint brush.

The interpreter split mirrors BiRefNet: the GUI runs in the system Python, which
has tkinter but no torch; SAM2 runs in ComfyUI's ``python_embeded``, which has
torch and the official ``sam2`` package but no tkinter.  So ``run_subprocess``
writes a small standalone script -- image path, box, points, checkpoint -- and
spawns that interpreter on it.  The child loads the model, predicts one mask and
saves it as a PNG next to the photograph's analysis output; the parent reads the
PNG back with ``load_mask_png``.  One model load per process is the price of the
split, paid once per click gesture rather than once per photograph in a batch.

Everything that does not need torch -- path arithmetic, prompt validation, the
mask round-trip -- lives here and is tested without a GPU.  The torch side is
lazy: nothing in this module imports torch or sam2 at load time, so the default
install stays numpy + OpenCV + Pillow.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from typing import List, Optional, Tuple

import cv2
import numpy as np

_LOCK = threading.Lock()
_CACHE = {}

# A prompt box smaller than this in analysis-res pixels is a stray click, not a
# selection; SAM2 would happily segment a 3-pixel box and the result reads as
# "it masked everything".
MIN_BOX_PX = 8

DEFAULT_COMFY_PYTHON = r"D:\ComfyUI_windows_portable\ComfyUI\python_embeded\python.exe"
DEFAULT_CKPT = os.path.join("models", "sam2", "sam2.1_hiera_base_plus.pt")
DEFAULT_CFGDIR = os.path.join("models", "sam2", "configs")

# The child script, executed by the ComfyUI interpreter.  It is written out to a
# temp file and run as ``python <script>`` rather than passed with -c, because a
# multi-line program with embedded quotes does not survive a Windows command
# line.  Every value that enters it goes through _q(), which escapes for the
# string literal it lands in -- the predecessor built these with bare f-strings
# and a path containing a quote (or a backslash sequence Python reinterpreted)
# produced "cannot read 'D:\\...jpg'": the inner single quotes closed the
# literal early.
_CHILD_SCRIPT = '''
import os
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import sys
import traceback

import cv2
import numpy as np


def _q(v):
    return str(v).replace(chr(92), chr(92) * 2).replace(chr(39), chr(92) + chr(39))


IMAGE = {_image}
OUT = {_out}
CKPT = {_ckpt}
CFGDIR = {_cfgdir}
BOX = {_box}
POINTS = {_points}
DEVICE = {_device}

try:
    import torch
    from hydra import initialize_config_dir
    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor

    dev = DEVICE or ("cuda" if torch.cuda.is_available() else "cpu")
    img = cv2.imread(IMAGE, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("cannot read " + IMAGE)
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

    initialize_config_dir(config_dir=CFGDIR, version_base=None)
    model = build_sam2("sam2/sam2_hiera_b+", CKPT, device=dev)
    pred = SAM2ImagePredictor(model)
    pred.set_image(rgb)

    kw = dict(multimask_output=False)
    if BOX is not None:
        kw["box"] = np.array(BOX, dtype=np.float32)
    if POINTS:
        coords = [p[:2] for p in POINTS]
        labels = [p[2] for p in POINTS]
        kw["point_coords"] = np.array(coords, dtype=np.float32)
        kw["point_labels"] = np.array(labels, dtype=np.int32)
    if "box" not in kw and "point_coords" not in kw:
        raise RuntimeError("no prompt")

    masks, scores, _ = pred.predict(**kw)
    mask = bool(masks[0])
    h, w = rgb.shape[:2]
    if mask.shape[:2] != (h, w):
        mask = cv2.resize(mask.astype(np.uint8), (w, h),
                          interpolation=cv2.INTER_NEAREST).astype(bool)
    # white = selected (the building); the parent inverts to its ignore
    # convention when it reads the PNG back
    cv2.imwrite(OUT, (mask * 255).astype(np.uint8))
    print("OK " + OUT)
except Exception:
    traceback.print_exc()
    sys.exit(1)
'''


def _q(v) -> str:
    """A value made safe for the string literal it lands in inside ``_CHILD_SCRIPT``.

    The child receives its inputs as Python source, so a backslash or quote in a
    path would otherwise close the literal early and the child would die with a
    syntax error that names no file -- which is exactly how the predecessor's
    "cannot read 'D:\\...jpg'" failure looked.
    """
    return str(v).replace("\\", "\\\\").replace("'", "\\'")


def _default_ckpt() -> str:
    """The vendored SAM2 checkpoint: ``models/sam2/`` beside the code."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, DEFAULT_CKPT)


def _default_cfgdir() -> str:
    """The config YAMLs the official ``sam2`` package needs at load time."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, DEFAULT_CFGDIR)


def available(python_exe: str = "", ckpt: str = "") -> bool:
    """Whether the subprocess route can run on this machine.

    Two halves, checked separately so an error can name the missing one: the
    ComfyUI interpreter (torch + sam2 live there, not here) and the checkpoint
    it would load.  Neither import is attempted -- importing torch in the GUI's
    Python is precisely what this module exists to avoid.
    """
    py = python_exe or DEFAULT_COMFY_PYTHON
    c = ckpt or _default_ckpt()
    if not os.path.isfile(py):
        return False
    if not os.path.isfile(c):
        return False
    return True


def _load(ckpt: str, cfgdir: str, device: str = "") -> dict:
    """Build the predictor once per (checkpoint, device) per process.

    Only ever called from inside a child spawned by ``run_subprocess`` -- in
    the GUI's own Python this would import torch, which is not installed there
    and is the whole reason for the subprocess bridge.  Cached because loading
    324 MB of weights per prediction would dwarf the prediction itself.
    """
    key = (os.path.abspath(ckpt), device)
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]

        import torch
        from hydra import initialize_config_dir
        from sam2.build_sam import build_sam2
        from sam2.sam2_image_predictor import SAM2ImagePredictor

        dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
        if not os.path.isfile(ckpt):
            raise FileNotFoundError("SAM2 checkpoint not found: " + ckpt)
        initialize_config_dir(config_dir=cfgdir, version_base=None)
        model = build_sam2("sam2/sam2_hiera_b+", ckpt, device=dev)
        pred = SAM2ImagePredictor(model)
        entry = {"pred": pred, "device": dev}
        _CACHE[key] = entry
        return entry


def predict_box(bgr: np.ndarray, box: Tuple[int, int, int, int], ckpt: str,
                cfgdir: str, device: str = "") -> np.ndarray:
    """The SAM2 mask for one box prompt, True inside the selection.

    Runs in-process, so it needs torch + sam2 in *this* interpreter -- the child
    script uses the same code path via ``_load``.  Kept beside
    ``predict_box_and_points`` because a test can exercise the box-only contract
    without fabricating points.
    """
    return predict_box_and_points(bgr, box, [], ckpt, cfgdir, device)


def predict_box_and_points(bgr: np.ndarray, box: Optional[Tuple[int, int, int, int]],
                           points: List[Tuple[int, int, int]], ckpt: str, cfgdir: str,
                           device: str = "") -> np.ndarray:
    """The SAM2 mask for a box and/or point prompts, True inside the selection.

    ``box`` is (x0, y0, x1, y1) in ``bgr`` pixels; each point is (x, y, label)
    with label 1 = positive (inside), 0 = negative (outside).  At least one of
    the two must be given -- SAM2 has nothing to segment without a prompt.

    Returns a boolean array at ``bgr``'s resolution; the caller decides what
    "inside" means for its own convention.
    """
    if box is None and not points:
        raise ValueError("SAM2 needs a box or at least one point prompt")
    if box is not None:
        x0, y0, x1, y1 = [int(round(v)) for v in box]
        h, w = bgr.shape[:2]
        x0, x1 = max(0, min(w - 1, x0)), max(0, min(w - 1, x1))
        y0, y1 = max(0, min(h - 1, y0)), max(0, min(h - 1, y1))
        if (x1 - x0) < MIN_BOX_PX or (y1 - y0) < MIN_BOX_PX:
            raise ValueError("SAM2 box prompt is too small to segment "
                             "({}x{})".format(x1 - x0, y1 - y0))

    entry = _load(ckpt, cfgdir, device)
    pred = entry["pred"]
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    pred.set_image(rgb)

    kw: dict = {"multimask_output": False}
    if box is not None:
        kw["box"] = np.array([x0, y0, x1, y1], dtype=np.float32)
    if points:
        coords = [(int(p[0]), int(p[1])) for p in points]
        labels = [int(p[2]) for p in points]
        kw["point_coords"] = np.array(coords, dtype=np.float32)
        kw["point_labels"] = np.array(labels, dtype=np.int32)

    with _LOCK:
        masks, _, _ = pred.predict(**kw)
    mask = bool(masks[0])
    h, w = bgr.shape[:2]
    if mask.shape[:2] != (h, w):
        mask = cv2.resize(mask.astype(np.uint8), (w, h),
                          interpolation=cv2.INTER_NEAREST).astype(bool)
    return mask


def _imread_unicode(path: str, flags: int):
    """``cv2.imread`` that also opens non-ASCII paths.

    The mask's filename is the photograph's stem, and a German street name is a
    perfectly good stem; ``cv2.imread`` mangles it on Windows and returns None.
    """
    img = cv2.imread(path, flags)
    if img is not None:
        return img
    try:
        buf = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    return cv2.imdecode(buf, flags) if buf.size else None


def mask_png_path(stem: str, out_dir: str) -> str:
    """Where the child writes its mask: ``<out_dir>/<stem>.png``.

    The directory is created here rather than in the child, so a missing folder
    fails in the parent with a clear message instead of surfacing as a child
    traceback one subprocess boundary away.
    """
    os.makedirs(out_dir, exist_ok=True)
    return os.path.join(out_dir, stem + ".png")


def load_mask_png(path: str, shape) -> np.ndarray:
    """The mask PNG back as an ignore array at ``shape`` (True = ignore).

    The child stores the *selection* -- white where SAM2 says "this is the
    building" -- because that is what a segmenter naturally outputs.  This
    inverts it to the project's convention (``masks.load``: white means ignore)
    and resizes if the mask was written at a different resolution than the one
    the fit runs on, so a stale PNG from an earlier analysis size still lines
    up instead of crashing the indexing.
    """
    img = _imread_unicode(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError("cannot read mask " + path)
    while img.ndim > 2:
        img = img[..., 0]
    h, w = shape[:2]
    if img.shape[:2] != (h, w):
        img = cv2.resize(img, (w, h), interpolation=cv2.INTER_NEAREST)
    selected = img > 127
    return ~selected


def _child_script_path(image_path: str, out_dir: str) -> str:
    """A temp file holding the generated child script, named after the image.

    Written beside the output rather than in a shared temp dir so two review
    windows working on different photographs cannot clobber each other's
    scripts, and the name carries the stem for anyone reading the process list.
    """
    stem = os.path.splitext(os.path.basename(image_path))[0]
    return os.path.join(out_dir, ".sam2_" + stem + ".py")


def run_subprocess(image_path: str, box: Optional[Tuple[int, int, int, int]],
                   points: List[Tuple[int, int, int]], python_exe: str = "",
                   out_dir: str = "", ckpt: str = "", cfgdir: str = "",
                   device: str = "", timeout: float = 300.0) -> str:
    """Run one SAM2 prediction in the ComfyUI interpreter; return the mask PNG path.

    Generates ``_CHILD_SCRIPT`` with this call's values baked in, writes it next
    to where the mask will land, and spawns ``python_exe`` on it.  The parent
    does not need torch at any point -- the model never loads in this process.

    Raises ``RuntimeError`` naming what failed when the child exits non-zero or
    times out; the GUI shows that string rather than a traceback.
    """
    py = python_exe or DEFAULT_COMFY_PYTHON
    if not os.path.isfile(py):
        raise RuntimeError("ComfyUI python not found: " + py)
    c = ckpt or _default_ckpt()
    if not os.path.isfile(c):
        raise RuntimeError("SAM2 checkpoint not found: " + c)
    cfg = cfgdir or _default_cfgdir()
    if not os.path.isdir(cfg):
        raise RuntimeError("SAM2 config dir not found: " + cfg)

    out_dir = out_dir or os.path.join(os.path.dirname(os.path.abspath(image_path)),
                                      "sam2_masks")
    mask_path = mask_png_path(os.path.splitext(os.path.basename(image_path))[0], out_dir)
    script_path = _child_script_path(image_path, out_dir)

    box_lit = "None" if box is None else "[" + ", ".join(_q(v) for v in box) + "]"
    pts_lit = ("[" + ", ".join("[" + ", ".join(_q(v) for v in p) + "]" for p in points)
               + "]") if points else "[]"
    script = (_CHILD_SCRIPT
              .replace("{_image}", "'" + _q(image_path) + "'")
              .replace("{_out}", "'" + _q(mask_path) + "'")
              .replace("{_ckpt}", "'" + _q(c) + "'")
              .replace("{_cfgdir}", "'" + _q(cfg) + "'")
              .replace("{_box}", box_lit)
              .replace("{_points}", pts_lit)
              .replace("{_device}", "'" + _q(device) + "'"))
    with open(script_path, "w", encoding="utf-8") as fh:
        fh.write(script)

    t0 = time.time()
    try:
        proc = subprocess.run([py, script_path], capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("SAM2 timed out after {:.0f}s".format(timeout))
    finally:
        # the script is single-use; leaving one per photograph would litter the
        # output folder
        try:
            os.remove(script_path)
        except OSError:
            pass

    if proc.returncode != 0 or not os.path.isfile(mask_path):
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError("SAM2 failed after {:.1f}s:\n{}".format(
            time.time() - t0, detail[-2000:]))
    return mask_path
