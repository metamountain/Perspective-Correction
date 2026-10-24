"""SAM2 box+point prompt segmentation for click-to-select masking.

The user drags a box over the building in the review window; that box is the
only prompt the window can produce.  The code below still accepts positive and
negative point prompts, but nothing in the window produces them -- there are no
Shift or Alt point bindings.  A comment must not describe a gesture that does
not exist.  The mask comes back as an ignore region
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
import time
from typing import List, Optional, Tuple

import cv2
import numpy as np

# A prompt box smaller than this is a stray click, not a selection; SAM2 would
# happily segment a 3-pixel box and the result reads as "it masked everything".
#
# IMAGE pixels, not analysis pixels. SAM2 segments the full-resolution file, so
# the box handed to it is in that frame, and so is the drag the GUI measures.
# The comment said analysis-res while the only gate in the project measured
# image pixels -- and that gate used its own literal 5, so this constant was
# read by nothing. One number, one frame, one place.
MIN_BOX_PX = 8

_COMFY_CANDIDATES = (
    r"D:\ComfyUI_windows_portable\python_embeded\python.exe",
    r"D:\ComfyUI_windows_portable\ComfyUI\python_embeded\python.exe",
    r"C:\ComfyUI_windows_portable\python_embeded\python.exe",
)


def default_comfy_python() -> str:
    """The ComfyUI interpreter, or the first candidate so an error can name it.

    A single hardcoded path is a single point of being wrong, and it WAS wrong:
    it carried an extra `ComfyUI` segment, so `available()` said no and every
    SAM gesture was dead with nothing to read anywhere. `PC_COMFY_PYTHON` wins
    when set, so a machine that keeps it elsewhere needs no edit here.
    """
    env = os.environ.get("PC_COMFY_PYTHON", "")
    if env and os.path.isfile(env):
        return env
    for p in _COMFY_CANDIDATES:
        if os.path.isfile(p):
            return p
    return _COMFY_CANDIDATES[0]


DEFAULT_COMFY_PYTHON = default_comfy_python()
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
# sam2 builds through torch.jit, which walks type hints with inspect.getsource
# and dies on Enum._generate_next_value_ in this interpreter -- OSError, inside
# the child, where nobody sees it. The scripting buys nothing here: one image,
# one forward pass. Set before torch is imported or it has no effect.
os.environ["PYTORCH_JIT"] = "0"

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
CFGNAME = {_cfgname}
BOX = {_box}
POINTS = {_points}
DEVICE = {_device}

try:
    import torch
    from hydra import initialize_config_dir
    from hydra.core.global_hydra import GlobalHydra
    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor

    dev = DEVICE or ("cuda" if torch.cuda.is_available() else "cpu")
    # np.fromfile + imdecode, not cv2.imread: cv2's own file IO cannot open a
    # non-ASCII path on Windows (an umlaut in the name was "cannot read").
    img = cv2.imdecode(np.fromfile(IMAGE, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("cannot read " + IMAGE)
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

    # Clear first.  Something in this interpreter's site-packages has already
    # initialised Hydra by the time sam2 is imported, and a second
    # initialize_config_dir then raises -- which killed every SAM run with a
    # traceback nobody saw, because it happens inside the child process.
    GlobalHydra.instance().clear()
    initialize_config_dir(config_dir=CFGDIR, version_base=None)
    model = build_sam2(CFGNAME, CKPT, device=dev)
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
    # `bool(array)` on anything bigger than one element raises -- this line
    # had never run, because the model never got far enough to reach it.
    # Squeeze first: SAM hands back (1, H, W) with multimask_output off.
    mask = np.asarray(masks[0]).squeeze().astype(bool)
    h, w = rgb.shape[:2]
    if mask.shape[:2] != (h, w):
        mask = cv2.resize(mask.astype(np.uint8), (w, h),
                          interpolation=cv2.INTER_NEAREST).astype(bool)
    # white = selected (the building); the parent inverts to its ignore
    # convention when it reads the PNG back
    ok, png = cv2.imencode(".png", (mask * 255).astype(np.uint8))
    if not ok:
        raise RuntimeError("cannot encode the mask")
    png.tofile(OUT)
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


_SIZE_NAMES = (("base_plus", "b+"), ("large", "l"), ("small", "s"), ("tiny", "t"))


def config_for(ckpt: str) -> str:
    """The hydra config name that matches ``ckpt``, e.g. ``sam2.1/sam2.1_hiera_b+``.

    Derived rather than hardcoded, because the two must agree and nothing said
    so.  The child asked for ``sam2/sam2_hiera_b+`` -- a SAM 2.0 config -- while
    the shipped checkpoint is ``sam2.1_hiera_base_plus.pt``, and torch rejected
    the mismatch with three unexpected state_dict keys, inside the subprocess,
    where the traceback went nowhere.  The checkpoint's own filename carries
    both the version and the size; this reads them off it.
    """
    stem = os.path.basename(ckpt).rsplit(".", 1)[0]
    ver = "sam2.1" if stem.startswith("sam2.1") else "sam2"
    size = "b+"
    for long, short in _SIZE_NAMES:
        if long in stem:
            size = short
            break
    return f"{ver}/{ver}_hiera_{size}"


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
    py = python_exe or default_comfy_python()
    c = ckpt or _default_ckpt()
    if not os.path.isfile(py):
        return False
    if not os.path.isfile(c):
        return False
    return True


# -- in-process route (2026-10-24) ----------------------------------------
# SAM 2.1 small through transformers in the GUI's own Python, installed by
# `deps` into models/sam2.1-hiera-small. transformers' own image processor
# needs torchvision, which would have to match the torch build exactly, so the
# preprocessing is done here: resize to 1024x1024 (bilinear), scale to 0..1,
# ImageNet mean/std, box in the 1024 frame; the 256x256 logits go back up
# through 1024 to the photograph's size and are cut at 0. Measured on
# Alte_Scheune: 0.66 s on CUDA, 2.0 s on a CPU, the barn selected whole.
_SAM_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_SAM_STD = np.array([0.229, 0.224, 0.225], np.float32)
_SAM_SIDE = 1024
_local = {}


def local_dir() -> str:
    from .depinstall import SAM2_DIR
    return SAM2_DIR


def local_available() -> bool:
    """transformers, torch and the four weight files -- without importing torch."""
    import importlib.util
    from .depinstall import sam2_weights_ok
    try:
        mods = all(importlib.util.find_spec(m) is not None for m in ("torch", "transformers"))
    except (ImportError, ValueError):
        return False
    return mods and sam2_weights_ok()


def _local_model():
    if "model" not in _local:
        import torch
        from transformers import Sam2Model
        if torch.cuda.is_available():
            dev = "cuda"
        elif getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
            dev = "mps"
        else:
            dev = "cpu"
        model = Sam2Model.from_pretrained(local_dir(), local_files_only=True).to(dev).eval()
        _local["model"], _local["device"] = model, dev
    return _local["model"], _local["device"]


def segment_local(bgr: np.ndarray, box: Tuple[int, int, int, int]) -> np.ndarray:
    """Ignore mask for ``bgr`` (True = outside the selection) from a pixel box."""
    import torch
    import torch.nn.functional as F
    h, w = bgr.shape[:2]
    model, dev = _local_model()
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    x = cv2.resize(rgb, (_SAM_SIDE, _SAM_SIDE), interpolation=cv2.INTER_LINEAR)
    x = ((x.astype(np.float32) / 255.0 - _SAM_MEAN) / _SAM_STD).transpose(2, 0, 1)[None]
    sx, sy = _SAM_SIDE / w, _SAM_SIDE / h
    x0, y0, x1, y1 = box
    b = torch.tensor([[[x0 * sx, y0 * sy, x1 * sx, y1 * sy]]], dtype=torch.float32)
    with torch.no_grad():
        out = model(pixel_values=torch.from_numpy(np.ascontiguousarray(x)).to(dev),
                    input_boxes=b.to(dev), multimask_output=False)
    lo = out.pred_masks.reshape(1, 1, *out.pred_masks.shape[-2:]).float().cpu()
    up = F.interpolate(lo, (_SAM_SIDE, _SAM_SIDE), mode="bilinear", align_corners=False)
    up = F.interpolate(up, (h, w), mode="bilinear", align_corners=False)
    return ~(up[0, 0].numpy() > 0)


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
    py = python_exe or default_comfy_python()
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
              .replace("{_cfgname}", "'" + _q(config_for(c)) + "'")
              .replace("{_box}", box_lit)
              .replace("{_points}", pts_lit)
              .replace("{_device}", "'" + _q(device) + "'"))
    with open(script_path, "w", encoding="utf-8") as fh:
        fh.write(script)

    t0 = time.time()
    try:
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        proc = subprocess.run([py, script_path], capture_output=True, text=True,
                              timeout=timeout, **kwargs)
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
