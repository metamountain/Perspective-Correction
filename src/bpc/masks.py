"""Region masks: keep the fit on the building.

The idea a user reaches for here is SAM -- segment the scene, keep the
building, drop the trees.  That is the right instinct and the wrong first step,
because it costs a torch dependency and hundreds of megabytes before anyone has
shown that masking helps at all.  So this module is the *seam*: a mask is just a
boolean array the size of the analysis image, and any producer can fill it.

Two producers ship:

``off``       no mask.
``birefnet``  a segmenter: one matte, the salient object, the rest ignored.

A third, ``file``, reads a PNG from anywhere -- painted by hand, written by
``--mask-export``, or produced by some other tool.

A fourth was here and is gone.  ``auto`` was a cheap texture heuristic --
excess green, incoherent local gradients, sky connected to the top edge -- and
it needed no model at all, which was its whole appeal.  It was removed because
it is a *pixel* statistic where the question is about *objects*: on a stripped
web JPEG it took the horizontals the focal estimate needed, so it measurably
hurt exactly the photographs that had least to spare (pitch max 5.58 -> 10.05
deg with the focal length unknown).  A segmenter does not have that failure and
is now cheap enough to be the only answer.  The history is in docs/masking.md.
"""
from __future__ import annotations

import os

import cv2
import numpy as np


def resolve(mask_file: str, image_path: str) -> str:
    """Accept either one PNG or a folder holding one mask per image.

    A batch needs one mask per photograph, so pointing at a folder and matching
    on the file stem is what makes an external segmenter -- SAM in ComfyUI, say
    -- actually usable here rather than a one-image demo.
    """
    if not mask_file:
        raise ValueError("--mask file needs --mask-file")
    if not os.path.isdir(mask_file):
        return mask_file
    stem = os.path.splitext(os.path.basename(image_path or ""))[0]
    for ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"):
        for cand in (stem + ext, stem + "_mask" + ext, stem + "-mask" + ext):
            p = os.path.join(mask_file, cand)
            if os.path.exists(p):
                return p
    raise ValueError(f"no mask for {stem} in {mask_file}")


def _imread(path: str, flags: int):
    """``cv2.imread`` that also opens non-ASCII paths.

    ``cv2.imread`` goes through the C runtime and mangles non-ASCII characters on
    Windows, so a mask whose name carries an umlaut -- the stem of its own
    photograph -- reads as None and ``--mask file`` dies on exactly the photos a
    segmenter was pointed at.  ``np.fromfile`` opens through Python (Unicode-safe)
    and ``cv2.imdecode`` decodes the same bytes; ASCII names take the fast path.
    """
    img = cv2.imread(path, flags)
    if img is not None:
        return img
    try:
        buf = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None                     # missing/unreadable -> caller reports it
    return cv2.imdecode(buf, flags) if buf.size else None


def load(path: str, shape, invert: bool = False) -> np.ndarray:
    """A painted PNG.  White means "ignore this region" unless ``invert``.

    ``invert`` exists because a segmenter naturally outputs the *subject* --
    SAM hands back the building in white -- which is the opposite convention.
    """
    img = _imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"cannot read mask {path}")
    # ultralytics replaces cv2.imread with its own wrapper on import -- to
    # support non-ASCII paths -- and that wrapper returns (h, w, 1) for a
    # greyscale read where OpenCV returns (h, w).  It is the first SAM backend
    # tried, so merely *having* it installed silently broke --mask file, which
    # is the bridge --mask-export writes for.  Squeeze rather than test for the
    # patch: a mask is two dimensional by definition.
    while img.ndim > 2:
        img = img[..., 0]
    if img.shape[:2] != tuple(shape[:2]):
        img = cv2.resize(img, (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST)
    m = img > 127
    return ~m if invert else m


def protect_structure(mask: np.ndarray, seg: np.ndarray, min_len: float,
                      radius: int = 9) -> np.ndarray:
    """Un-mask anything a long straight line runs through.

    The coherence cue measures whether *one* direction dominates locally.  A
    half-timbered facade has *two* -- posts and rails -- so a beam grid scores as
    incoherent and gets masked as if it were foliage.  That was visible the
    moment the mask was drawn on screen: the auto mask was speckling the timber
    frame of a barn it was supposed to be measuring.

    Rather than invent a better texture statistic, use the evidence already in
    hand.  Vegetation does not produce long straight segments; architecture is
    made of them.  So whatever a long detected line passes through is protected,
    which cannot mask away the very structure the fit needs.
    """
    if mask is None or len(seg) == 0:
        return mask
    keep = np.zeros(mask.shape[:2], np.uint8)
    lengths = np.hypot(seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1])
    long_ones = seg[lengths >= min_len]
    if len(long_ones) == 0:
        return mask
    for x0, y0, x1, y1 in long_ones:
        cv2.line(keep, (int(round(x0)), int(round(y0))), (int(round(x1)), int(round(y1))),
                 255, 1, cv2.LINE_8)
    keep = cv2.dilate(keep, np.ones((radius, radius), np.uint8))
    return mask & ~(keep.astype(bool))


GDINO_PAD_FRAC = 0.04   # pad around the detected box before cropping


def default_gdino_dir() -> str:
    """Where the vendored Grounding DINO model lives: ``models/GroundingDINO/``."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "models", "GroundingDINO")


_GDINO_CACHE = {}


def _gdino_load(model_dir: str):
    """Load (and cache) the Grounding DINO processor + model for one directory."""
    import torch
    from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection

    key = os.path.abspath(model_dir)
    if key not in _GDINO_CACHE:
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        proc = AutoProcessor.from_pretrained(model_dir, local_files_only=True)
        model = (AutoModelForZeroShotObjectDetection
                 .from_pretrained(model_dir, local_files_only=True).to(dev).eval())
        _GDINO_CACHE[key] = (proc, model, dev)
    return _GDINO_CACHE[key]


def gdino_box(bgr: np.ndarray, prompt: str, model_dir: str):
    """The best ``prompt`` box in ``bgr`` pixels: ``(x0, y0, x1, y1), score``.

    A full-frame fallback with score 0 when nothing clears the threshold, so a
    miss degrades to "matte the whole frame" rather than crashing the run.
    """
    import torch
    from PIL import Image

    proc, model, dev = _gdino_load(model_dir)
    h, w = bgr.shape[:2]
    pil = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    inp = proc(text=[prompt], images=pil, return_tensors="pt").to(dev)
    with torch.no_grad():
        res = model(**inp)
    det = proc.post_process_grounded_object_detection(
        res, inp["input_ids"], threshold=0.20, text_threshold=0.20,
        target_sizes=[(h, w)])[0]
    scores = det["scores"].cpu().numpy()
    if len(scores) == 0:
        return (0, 0, w, h), 0.0
    boxes = det["boxes"].cpu().numpy()
    labels = [str(x) for x in det["text_labels"]]
    want = prompt.lower()
    keep = [i for i, lab in enumerate(labels) if want in lab.lower()]
    if not keep:
        keep = list(range(len(scores)))
    best = int(max(keep, key=lambda i: float(scores[i])))
    return (int(boxes[best][0]), int(boxes[best][1]),
            int(boxes[best][2]), int(boxes[best][3])), float(scores[best])


def _gdino_compose(h, w, x0, y0, x1, y1, crop_ignore: np.ndarray) -> np.ndarray:
    """Full-frame ignore mask from a detected box and its matte.

    Outside the (padded) box is not the subject, so it is ignored outright; inside,
    the BiRefNet matte of the crop decides.  ``crop_ignore`` is True where the fit
    should not look, at the crop's own resolution.
    """
    ignore = np.ones((h, w), bool)
    ci = crop_ignore
    if ci.shape[:2] != (y1 - y0, x1 - x0):
        ci = cv2.resize(ci.astype(np.uint8), (x1 - x0, y1 - y0),
                        interpolation=cv2.INTER_NEAREST).astype(bool)
    ignore[y0:y1, x0:x1] = ci
    return ignore


def gdino_mask(bgr: np.ndarray, settings):
    """``(ignore_mask, note)`` for ``--mask gdino``.

    A text prompt finds the subject's box, BiRefNet mattes inside it, and
    everything outside the box is dropped -- so a competing foreground object
    that BiRefNet would otherwise grab on its own is cut away by the crop.
    """
    from . import birefnet as BN

    weights = getattr(settings, "birefnet_model", "")
    if not weights:
        raise ValueError("--mask gdino needs --birefnet-model <weights> for the matte")
    prompt = (getattr(settings, "gdino_prompt", "") or "building").strip() or "building"
    model_dir = getattr(settings, "gdino_model", "") or default_gdino_dir()

    h, w = bgr.shape[:2]
    box, score = gdino_box(bgr, prompt, model_dir)
    x0, y0, x1, y1 = box
    px = int(GDINO_PAD_FRAC * w)
    py = int(GDINO_PAD_FRAC * h)
    x0 = max(0, x0 - px); y0 = max(0, y0 - py)
    x1 = min(w, x1 + px); y1 = min(h, y1 + py)
    if (x1 - x0) < 8 or (y1 - y0) < 8:
        raise ValueError("--mask gdino: the detected box is too small to matte")

    crop = bgr[y0:y1, x0:x1]
    fg = BN.foreground(crop, weights,
                       device=getattr(settings, "birefnet_device", ""),
                       res=getattr(settings, "birefnet_res", 0))
    ci = fg < float(getattr(settings, "birefnet_threshold", 0.5))
    spx = BN.shrink_px_for(ci.shape, getattr(settings, "birefnet_shrink_frac", 0.008))
    if spx > 0:
        k = np.ones((2 * spx + 1,) * 2, np.uint8)
        ci = cv2.erode(ci.astype(np.uint8), k).astype(bool)
    ignore = _gdino_compose(h, w, x0, y0, x1, y1, ci)
    return ignore, ("GDINO '{p}' box {b} score {s:.2f}; BiRefNet inside, "
                    "{m:.0f}% of frame ignored".format(
                        p=prompt, b=box, s=score, m=float(ignore.mean()) * 100))


def gdino_available(model_dir: str = "") -> bool:
    """Can this interpreter run ``--mask gdino``?  Needs transformers and the weights."""
    import importlib.util

    if importlib.util.find_spec("transformers") is None:
        return False
    d = model_dir or default_gdino_dir()
    if not os.path.isdir(d) or not os.path.isfile(os.path.join(d, "config.json")):
        return False
    return any(f.endswith(".safetensors") for f in os.listdir(d))


def _build_one(mode: str, bgr: np.ndarray, settings, image_path: str = ""):
    """``(mask_or_None, note)`` for a single source name."""
    if mode in ("off", "auto"):
        # "auto" was the cheap texture heuristic; accepted and ignored rather
        # than raising, so an old command line or a remembered setting does not
        # abort a batch
        return None, ""
    if mode == "file":
        return load(resolve(settings.mask_file, image_path), bgr.shape,
                    getattr(settings, "mask_invert", False)), "painted mask"
    if mode == "birefnet":
        from . import birefnet as BN
        path = getattr(settings, "birefnet_model", "")
        if not path:
            raise ValueError("--mask birefnet needs --birefnet-model <weights>")
        return BN.build_mask(
            bgr, path,
            threshold=getattr(settings, "birefnet_threshold", BN.DEFAULT_THRESHOLD),
            device=getattr(settings, "birefnet_device", ""),
            res=getattr(settings, "birefnet_res", 0),
            shrink_frac=getattr(settings, "birefnet_shrink_frac", 0.008))
    if mode == "gdino":
        return gdino_mask(bgr, settings)
    return None, ""


def build(bgr: np.ndarray, settings, image_path: str = "", seg=None):
    """``(mask_or_None, note)`` for whichever source(s) are configured.

    ``mask_mode`` is one name ("birefnet") or several, comma-joined
    ("file,birefnet").  Several are **added**: a pixel is ignored when any
    source ignores it, because each source is an independent claim about what
    is not the building and dropping evidence twice costs nothing while keeping
    clutter one source missed costs a wrong correction.

    Note that gdino already runs BiRefNet inside its box, so ticking both it
    and birefnet gives the full-frame matte union -- which is the plain
    birefnet result, and throws away the crop that made gdino worth picking.
    """
    mode = getattr(settings, "mask_mode", "off")
    parts = [p.strip() for p in str(mode).split(",") if p.strip()]
    parts = [p for p in parts if p not in ("off", "auto")]
    if not parts:
        return None, ""
    if len(parts) == 1:
        return _build_one(parts[0], bgr, settings, image_path)

    built, notes = [], []
    for p in parts:
        m, n = _build_one(p, bgr, settings, image_path)
        if m is not None:
            built.append(m)
            notes.append(f"{p}: {n}" if n else p)
    if not built:
        return None, ""
    out = built[0]
    for m in built[1:]:
        out = np.logical_or(out, m)
    return out, " + ".join(notes) + f" -> {out.mean() * 100:.0f}% ignored"


MAX_EVIDENCE_LOST = 0.55


def credible(before: np.ndarray, after: np.ndarray, max_lost: float = MAX_EVIDENCE_LOST):
    """``(ok, reason)`` -- is what this mask removed clutter, or the building?

    Judged on **line evidence lost, not pixels covered**, and the difference is
    not academic.  Measured on six real barns: one photograph has 64 % of its
    frame masked and loses 1.5 % of its vertical line weight -- a grassy
    foreground, entirely harmless -- while another masks 71 % and loses
    **74.5 %**, because that barn is painted green and the vegetation cue took
    the wall for foliage.  A coverage test rejects the harmless one and passes
    the dangerous one.

    Being the outermost check it also catches what no individual cue can, which
    is why it matters most for an external segmenter: a SAM mask applied with
    the wrong polarity, or one belonging to a different photograph, both show up
    here as nearly all the evidence vanishing.
    """
    if len(before) == 0:
        return True, ""
    w_before = float(np.hypot(before[:, 2] - before[:, 0],
                              before[:, 3] - before[:, 1]).sum())
    if w_before <= 0:
        return True, ""
    w_after = float(np.hypot(after[:, 2] - after[:, 0],
                             after[:, 3] - after[:, 1]).sum()) if len(after) else 0.0
    lost = 1.0 - w_after / w_before
    if lost > max_lost:
        return False, (f"mask removed {lost * 100:.0f}% of the line evidence "
                       f"(over {max_lost * 100:.0f}%) and was ignored -- wrong "
                       f"polarity, wrong image, or it is masking the building")
    return True, ""


def drop_by_endpoints(seg: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Keep every segment unless **both** its endpoints lie inside the mask.

    The strictest reading of what a mask is for, and deliberately the most
    conservative one: a line is discarded only when there is no doubt at all
    that it belongs to the masked region.  Anything crossing the boundary --
    a facade edge running down into shrubbery, a roofline against the sky --
    keeps its full say, because the half of it that is on the building is real
    evidence and the fit is length-weighted anyway.

    Two rules it replaced, in order.  First a sampled threshold that dropped a
    segment once 60 % of five points along it fell inside; that discarded
    straddling lines wholesale, and which side of the threshold a line landed on
    turned on a sample or two.  Then a per-segment weight equal to the visible
    fraction, which is gentler but makes the mask a soft influence on every line
    rather than a decision about a few.  Endpoints are unambiguous, need no
    constant, and cannot half-remove anything.
    """
    if len(seg) == 0 or mask is None:
        return seg
    h, w = mask.shape[:2]
    x0 = np.clip(seg[:, 0].astype(int), 0, w - 1)
    y0 = np.clip(seg[:, 1].astype(int), 0, h - 1)
    x1 = np.clip(seg[:, 2].astype(int), 0, w - 1)
    y1 = np.clip(seg[:, 3].astype(int), 0, h - 1)
    both_inside = mask[y0, x0] & mask[y1, x1]
    return seg[~both_inside]


def drop_masked(seg: np.ndarray, mask: np.ndarray, samples: int = 5,
                tolerance: float = 0.6) -> np.ndarray:
    """Remove segments that lie mostly inside the mask.

    Superseded in the pipeline by ``drop_by_endpoints``, which needs no
    threshold at all.  Kept because a sampled test is the right primitive when
    the question really is "how much of this lies inside", and because the
    threshold behaviour is worth keeping tested.
    """
    if len(seg) == 0 or mask is None:
        return seg
    h, w = mask.shape[:2]
    t = np.linspace(0.0, 1.0, samples)[None, :]
    xs = np.clip((seg[:, 0:1] + (seg[:, 2:3] - seg[:, 0:1]) * t).astype(int), 0, w - 1)
    ys = np.clip((seg[:, 1:2] + (seg[:, 3:4] - seg[:, 1:2]) * t).astype(int), 0, h - 1)
    inside = mask[ys, xs].mean(axis=1)
    return seg[inside < tolerance]
