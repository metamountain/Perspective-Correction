"""Region masks: keep the fit on the building.

The idea a user reaches for here is SAM -- segment the scene, keep the
building, drop the trees.  That is the right instinct and the wrong first step,
because it costs a torch dependency and hundreds of megabytes before anyone has
shown that masking helps at all.  So this module is the *seam*: a mask is just a
boolean array the size of the analysis image, and any producer can fill it.

Two producers ship:

``off``       no mask.
``file``      a PNG from anywhere -- painted by hand, or the output of another
              tool, such as the SAM box select in the window.

More were here and are gone.  ``auto`` was a cheap texture heuristic --
excess green, incoherent local gradients, sky connected to the top edge -- and
it needed no model at all, which was its whole appeal.  It was removed because
it is a *pixel* statistic where the question is about *objects*: on a stripped
web JPEG it took the horizontals the focal estimate needed, so it measurably
hurt exactly the photographs that had least to spare (pitch max 5.58 -> 10.05
deg with the focal length unknown).  The history is in docs/masking.md.  The
learned-model segmenters (``birefnet`` and ``gdino``) were retired the other
way: in practice the manual brush, with SAM's box select, masked better and
cost less.  ``build`` still accepts the retired names and returns no mask, so
an old settings object does not abort a batch.
"""
from __future__ import annotations

import os

import cv2
import numpy as np

from . import imageio


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
    img = imageio.imread_unicode(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"cannot read mask {path}")
    # ultralytics replaces cv2.imread with its own wrapper on import -- to
    # support non-ASCII paths -- and that wrapper returns (h, w, 1) for a
    # greyscale read where OpenCV returns (h, w).  Merely *having* it installed
    # silently broke --mask file, which is how an external tool's masks get in.
    # Squeeze rather than test for the patch: a mask is two dimensional by
    # definition.
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
    # Any other name -- including the retired "birefnet" and "gdino" -- is
    # accepted and ignored rather than raising, so an old settings object does
    # not abort a batch.
    return None, ""


def build(bgr: np.ndarray, settings, image_path: str = "", seg=None):
    """``(mask_or_None, note)`` for whichever source(s) are configured.

    ``mask_mode`` is one name ("file") or several, comma-joined.  Several are
    **added**: a pixel is ignored when any source ignores it, because each
    source is an independent claim about what is not the building and dropping
    evidence twice costs nothing while keeping clutter one source missed costs
    a wrong correction.
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


def untouched(seg: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Which segments do NOT touch the mask anywhere along their length.

    Named for what it RETURNS. It was `touches`, and it returned `~hit` -- the
    survivors -- so the name and the first line of the docstring both said the
    opposite of the value. Its one caller reads it as a keep-mask and is
    correct; a second caller trusting the name would have inverted the mask.

    The simple rule (user, 2026-09-15): an annotator that touches the mask is
    not evidence.  Sampled rather than rasterised, because a segment is a line
    and twelve points along it settle the question at analysis resolution.

    Measured before adopting it, against `drop_by_endpoints`: over eight pool
    photographs it keeps 46 % of detected lines where endpoints keeps 48 %.  The
    docstring below feared this rule would "discard straddling lines wholesale";
    at this resolution it costs about a twentieth of the evidence, and buys a
    rule a person can hold in their head.
    """
    if len(seg) == 0 or mask is None:
        return np.ones(len(seg), dtype=bool)
    h, w = mask.shape[:2]
    hit = np.zeros(len(seg), dtype=bool)
    for f in np.linspace(0.0, 1.0, 12):
        x = np.clip(((seg[:, 0] * (1 - f)) + seg[:, 2] * f).astype(int), 0, w - 1)
        y = np.clip(((seg[:, 1] * (1 - f)) + seg[:, 3] * f).astype(int), 0, h - 1)
        hit |= mask[y, x]
    return ~hit


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
