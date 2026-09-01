"""Building the correction homography, limiting it, and cropping.

The homography is always ``K R K^-1`` -- a pure camera rotation.  That is the
one decision here that most changes the output compared with the reference
implementation, which built a general projective transform from two vanishing
points followed by an affine "make the axes orthogonal" step.  A general
projective transform can shear, stretch one side of the frame to several times
the other, and turn a building into a trapezoidal smear.  A rotation cannot: it
has three degrees of freedom, all of which correspond to something a
photographer could have done with the tripod, and two of which we deliberately
leave at zero.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from . import geometry as G

_INTERP = {"lanczos": cv2.INTER_LANCZOS4, "cubic": cv2.INTER_CUBIC,
           "linear": cv2.INTER_LINEAR, "nearest": cv2.INTER_NEAREST}


def limit(roll: float, pitch: float, settings, focal_is_a_guess: bool = False):
    """Apply strengths, damping and hard caps.  Returns ``(roll, pitch, clamped)``.

    ``focal_is_a_guess`` damps the pitch, and only the pitch.  Pitch scales
    linearly with the assumed focal length while roll does not depend on it at
    all, so an unknown lens puts a proportional error on exactly one of the two
    corrections.  The error is roughly symmetric, but its *consequences* are
    not: verticals left slightly converging read as an ordinary photograph,
    while verticals splayed outwards at the top read as a mistake.  Measured on
    the 40 scene benchmark, damping by 0.85 cut over-corrections from 15 to 9
    with no loss of mean accuracy (2.23 deg -> 2.14 deg).
    """
    r = roll * settings.roll_strength if settings.correct_roll else 0.0
    p = pitch * settings.pitch_strength if settings.correct_pitch else 0.0
    if focal_is_a_guess:
        p *= settings.uncertain_pitch_damping
    rmax = math.radians(settings.max_roll_deg)
    pmax = math.radians(settings.max_pitch_deg)
    clamped = abs(r) > rmax or abs(p) > pmax
    return float(np.clip(r, -rmax, rmax)), float(np.clip(p, -pmax, pmax)), clamped


def build(w: int, h: int, f: float, roll: float, pitch: float) -> np.ndarray:
    K = G.intrinsics(f, w / 2.0, h / 2.0)
    return G.homography(K, G.correction_rotation(roll, pitch))


def warped_quad(H: np.ndarray, w: int, h: int) -> np.ndarray:
    return G.apply_h(H, G.image_corners(w, h))


def quad_area(q: np.ndarray) -> float:
    x, y = q[:, 0], q[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _inside(quad: np.ndarray, pts: np.ndarray) -> bool:
    """All points strictly inside the convex quad (vertices in order)."""
    n = len(quad)
    sign = None
    for i in range(n):
        a, b = quad[i], quad[(i + 1) % n]
        e = b - a
        cross = e[0] * (pts[:, 1] - a[1]) - e[1] * (pts[:, 0] - a[0])
        s = np.sign(cross)
        s = s[s != 0]
        if len(s) == 0:
            continue
        if sign is None:
            sign = s[0]
        if np.any(s != sign):
            return False
    return True


def inscribed_rect(quad: np.ndarray, aspect: float | None, centre: np.ndarray,
                   iters: int = 40):
    """Largest axis-aligned rectangle inside ``quad``, centred on ``centre``.

    Binary search on the half-width.  Anchoring the rectangle at the mapped
    image centre rather than optimising its position too is a deliberate
    simplification: it keeps the composition the photographer framed, and it is
    monotone in the search variable, so 40 bisection steps land on the exact
    boundary rather than on a local optimum.
    """
    if aspect is None:
        aspect = 1.0
    lo, hi = 0.0, float(np.max(np.abs(quad - centre)) * 2.0 + 1.0)
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        hw, hh = mid, mid / aspect
        corners = centre + np.array([[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]])
        if _inside(quad, corners):
            lo = mid
        else:
            hi = mid
    hw, hh = lo, lo / aspect
    return np.array([centre[0] - hw, centre[1] - hh, centre[0] + hw, centre[1] + hh])


def _whole_frame(H, quad, img_w, img_h, settings, area_ratio):
    """The full warped quad on a canvas big enough to hold it.

    Nothing of the photograph is discarded; the corners the rotation opens up
    are filled by ``apply``.  ``keep_size`` scales the result back to the
    original pixel dimensions, so a batch keeps a consistent size.
    """
    x0, y0 = quad.min(axis=0)
    x1, y1 = quad.max(axis=0)
    ow, oh = int(round(x1 - x0)), int(round(y1 - y0))
    if ow < 8 or oh < 8:
        return None
    T = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]], dtype=float)
    if settings.keep_size:
        s = min(img_w / float(ow), img_h / float(oh))
        S = np.array([[s, 0, 0], [0, s, 0], [0, 0, 1]], dtype=float)
        return S @ T @ H, img_w, img_h, 1.0, area_ratio
    return T @ H, max(ow, 1), max(oh, 1), 1.0, area_ratio


def plan(img_w: int, img_h: int, H: np.ndarray, settings):
    """Work out the output canvas.

    Returns ``(H_total, out_w, out_h, coverage, area_ratio)`` where ``coverage``
    is the fraction of the original frame that survives -- useful in the log and
    as a sanity gate.

    ``crop="auto"`` is the reason this is not just a crop.  Correcting a strong
    convergence and then cropping to the largest inscribed rectangle can cost a
    quarter of the picture, and a quarter of the picture is a real loss to trade
    for straight verticals -- often a worse one than the convergence was.  So
    auto crops only while the loss stays small (``max_crop_loss``, 5 % by
    default) and otherwise keeps the whole frame and pads the corners the
    rotation opened up.  The choice is per photograph, because whether the loss
    is small is a property of the photograph and not of the folder.
    """
    quad = warped_quad(H, img_w, img_h)
    area_ratio = quad_area(quad) / float(img_w * img_h)

    if settings.crop == "none":
        return _whole_frame(H, quad, img_w, img_h, settings, area_ratio)

    centre = G.apply_h(H, np.array([[img_w / 2.0, img_h / 2.0]]))[0]
    aspect = (img_w / img_h) if settings.crop in ("aspect", "auto") else None
    if settings.crop == "inside" and aspect is None:
        aspect = img_w / img_h
    rect = inscribed_rect(quad, aspect, centre)
    rw, rh = rect[2] - rect[0], rect[3] - rect[1]
    if rw < 8 or rh < 8:
        return None

    coverage = (rw * rh) / quad_area(quad) if quad_area(quad) > 0 else 0.0
    if settings.crop == "auto" and (1.0 - coverage) > settings.max_crop_loss:
        return _whole_frame(H, quad, img_w, img_h, settings, area_ratio)

    if settings.keep_size:
        s = min(img_w / rw, img_h / rh)
        ow, oh = img_w, img_h
    else:
        s = 1.0
        ow, oh = int(round(rw)), int(round(rh))
    S = np.array([[s, 0, -rect[0] * s], [0, s, -rect[1] * s], [0, 0, 1]], dtype=float)
    return S @ H, max(ow, 1), max(oh, 1), float(coverage), area_ratio


def pad_colour(spec: str):
    """``(b, g, r)`` for a colour spec, or ``None`` meaning "extend the edge".

    Accepts ``edge``, a colour name, ``#rrggbb``/``#rgb``, or ``r,g,b``.  A
    spec that cannot be read falls back to ``edge`` rather than raising: a
    mistyped colour should not abort a batch that is otherwise fine, and the
    fallback is the safe-looking one.
    """
    s = (spec or "edge").strip().lower()
    if s in ("", "edge", "replicate", "extend"):
        return None
    named = {"black": (0, 0, 0), "white": (255, 255, 255), "grey": (128, 128, 128),
             "gray": (128, 128, 128), "mid": (128, 128, 128)}
    if s in named:
        return named[s]
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) == 6:
            try:
                r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
                return (b, g, r)
            except ValueError:
                return None
        return None
    parts = [p for p in s.replace(";", ",").split(",") if p.strip()]
    if len(parts) == 3:
        try:
            r, g, b = (max(0, min(255, int(float(p)))) for p in parts)
            return (b, g, r)
        except ValueError:
            return None
    return None


def apply(img: np.ndarray, H_total: np.ndarray, out_w: int, out_h: int, settings):
    """Render the plan.

    ``pad`` decides what fills the corners a rotation opens up, and only matters
    when the plan kept the whole frame rather than cropping into it:

    ``edge``    extend the border colour outwards (the default).  It reads as a
                soft vignette rather than a defect, which is what makes an
                un-cropped result usable straight out of the batch.
    a colour    ``black``, ``white``, ``#rrggbb`` or ``r,g,b``.  Honest and
                obvious, and the right choice when the result is going into a
                layout that will crop it anyway, when a smeared edge would be
                mistaken for real content, or when the fill is going to be
                replaced -- by a manual crop, or by inpainting.
    """
    flags = _INTERP.get(settings.interpolation, cv2.INTER_LANCZOS4)
    colour = pad_colour(getattr(settings, "pad", "edge"))
    if colour is not None:
        return cv2.warpPerspective(img, H_total, (out_w, out_h), flags=flags,
                                   borderMode=cv2.BORDER_CONSTANT,
                                   borderValue=colour)
    return cv2.warpPerspective(img, H_total, (out_w, out_h), flags=flags,
                               borderMode=cv2.BORDER_REPLICATE)


FRINGE = 3
"""Pixels of sub-pixel fringe the resampler leaves along the warped edge.

One number, two users that must agree: ``filled_region`` grows the hole by it
so an inpaint covers the fringe, and ``ReviewSession.auto_crop`` insets by it so
a rectangle it calls clean does not end on the same contaminated rows.
"""


def filled_region(H_total: np.ndarray, src_w: int, src_h: int,
                  out_w: int, out_h: int, grow: int = FRINGE) -> np.ndarray:
    """True where the output has no source pixel behind it.

    Warping a white frame the same way is the only reliable way to know: the
    quad corners give the outline but not the sub-pixel fringe the resampler
    leaves, and that fringe is what shows as a dark rim after inpainting.  Grown
    by a few pixels for the same reason.
    """
    ones = np.full((src_h, src_w), 255, np.uint8)
    valid = cv2.warpPerspective(ones, H_total, (out_w, out_h), flags=cv2.INTER_NEAREST,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    hole = (valid < 128).astype(np.uint8)
    if grow > 0:
        hole = cv2.dilate(hole, np.ones((2 * grow + 1,) * 2, np.uint8))
    return hole.astype(bool)
