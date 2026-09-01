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


def load(path: str, shape, invert: bool = False) -> np.ndarray:
    """A painted PNG.  White means "ignore this region" unless ``invert``.

    ``invert`` exists because a segmenter naturally outputs the *subject* --
    SAM hands back the building in white -- which is the opposite convention.
    """
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
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


def build(bgr: np.ndarray, settings, image_path: str = "", seg=None):
    """``(mask_or_None, note)`` for whichever source is configured."""
    mode = getattr(settings, "mask_mode", "off")
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
    return None, ""


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
