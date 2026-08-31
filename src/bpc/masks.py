"""Region masks: keep the fit on the building.

The idea a user reaches for here is SAM -- segment the scene, keep the
building, drop the trees.  That is the right instinct and the wrong first step,
because it costs a torch dependency and hundreds of megabytes before anyone has
shown that masking helps at all.  So this module is the *seam*: a mask is just a
boolean array the size of the analysis image, and any producer can fill it.

Two producers ship:

``off``     no mask.
``auto``    a cheap vegetation and sky suppressor -- no model, no download.

A third, ``file``, reads a PNG the user painted, which is also the escape hatch
for anyone who does want to run SAM: write the mask out, point at it.  If the
cheap version proves its worth, a segmentation model slots in behind the same
one-line interface.
"""
from __future__ import annotations

import os

import cv2
import numpy as np


def vegetation_and_sky(bgr: np.ndarray) -> np.ndarray:
    """True where the pixel probably is not building.

    Vegetation: excess green, plus chaotic local edge orientation.  A facade has
    a dominant direction at almost every point; a tree canopy has none, so the
    entropy of the local gradient orientation separates the two without any
    colour assumption -- useful for a bare winter tree, which is not green at
    all but is exactly the case that broke the eaves line.

    Sky: bright, unsaturated or blue, and connected to the top of the frame.
    """
    b, g, r = (bgr[..., i].astype(np.float32) for i in range(3))
    green = g - np.maximum(r, b)
    veg = cv2.GaussianBlur(green, (0, 0), 3.0) > 8.0

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    # coherence of the structure tensor: 1 where one direction dominates,
    # 0 where orientation is isotropic.  Twigs score low, brickwork high.
    k = 15
    jxx = cv2.boxFilter(gx * gx, cv2.CV_32F, (k, k))
    jyy = cv2.boxFilter(gy * gy, cv2.CV_32F, (k, k))
    jxy = cv2.boxFilter(gx * gy, cv2.CV_32F, (k, k))
    tr = jxx + jyy
    det = jxx * jyy - jxy * jxy
    disc = np.sqrt(np.maximum(tr * tr - 4 * det, 0.0))
    coherence = np.where(tr > 1e-6, disc / np.maximum(tr, 1e-6), 0.0)
    busy = cv2.boxFilter((mag > np.percentile(mag, 70)).astype(np.float32),
                         cv2.CV_32F, (k, k))
    chaotic = (coherence < 0.35) & (busy > 0.25)

    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    bright = hsv[..., 2].astype(np.float32) > 170
    pale = hsv[..., 1].astype(np.float32) < 60
    bluish = (hsv[..., 0].astype(np.float32) > 90) & (hsv[..., 0].astype(np.float32) < 130)
    sky_like = bright & (pale | bluish)
    sky = _connected_to_top(sky_like)

    mask = (veg | chaotic | sky).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8))
    return mask.astype(bool)


def _connected_to_top(candidate: np.ndarray) -> np.ndarray:
    """Keep only components that touch the top edge -- sky does, a white wall
    does not."""
    m = candidate.astype(np.uint8)
    n, labels = cv2.connectedComponents(m)
    if n <= 1:
        return np.zeros_like(candidate)
    top = np.unique(labels[0, :])
    keep = np.isin(labels, top[top != 0])
    return keep


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
    if img.shape[:2] != tuple(shape[:2]):
        img = cv2.resize(img, (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST)
    m = img > 127
    return ~m if invert else m


def build(bgr: np.ndarray, settings, image_path: str = "") -> "np.ndarray | None":
    mode = getattr(settings, "mask_mode", "off")
    if mode == "off":
        return None
    if mode == "file":
        return load(resolve(settings.mask_file, image_path), bgr.shape,
                    getattr(settings, "mask_invert", False))
    return vegetation_and_sky(bgr)


def drop_masked(seg: np.ndarray, mask: np.ndarray, samples: int = 5,
                tolerance: float = 0.6) -> np.ndarray:
    """Remove segments that lie mostly inside the mask.

    Sampled along the segment rather than tested at the midpoint: a line running
    from a wall into a tree is half evidence, and dropping it only when most of
    it is masked keeps the useful half from being thrown away with the rest.
    """
    if len(seg) == 0 or mask is None:
        return seg
    h, w = mask.shape[:2]
    t = np.linspace(0.0, 1.0, samples)[None, :]
    xs = np.clip((seg[:, 0:1] + (seg[:, 2:3] - seg[:, 0:1]) * t).astype(int), 0, w - 1)
    ys = np.clip((seg[:, 1:2] + (seg[:, 3:4] - seg[:, 1:2]) * t).astype(int), 0, h - 1)
    inside = mask[ys, xs].mean(axis=1)
    return seg[inside < tolerance]
