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


def limit(roll: float, pitch: float, settings, yaw: float = 0.0,
          focal_is_a_guess: bool = False):
    """Apply strengths, damping and hard caps.
    Returns ``(roll, pitch, yaw, clamped)``.

    ``focal_is_a_guess`` damps the pitch, and only the pitch.  Pitch scales
    linearly with the assumed focal length while roll does not depend on it at
    all, so an unknown lens puts a proportional error on exactly one of the two
    corrections.  The error is roughly symmetric, but its *consequences* are
    not: verticals left slightly converging read as an ordinary photograph,
    while verticals splayed outwards at the top read as a mistake.  Measured on
    the 40 scene benchmark, damping by 0.85 cut over-corrections from 15 to 9
    with no loss of mean accuracy (2.23 deg -> 2.14 deg).

    Yaw is gated on ``correct_horizontal`` and capped tighter than pitch: it is
    estimated from one dominant horizontal vanishing point, so a wrong or weak
    one shears the frame sideways -- which reads as a mistake faster than an
    under-corrected vertical does.  A yaw past its cap clamps the whole
    correction, exactly like roll or pitch, and the existing refuse-beyond-limit
    path then decides whether that is a refusal.
    """
    r = roll * settings.roll_strength if settings.correct_roll else 0.0
    p = pitch * settings.pitch_strength if settings.correct_pitch else 0.0
    y = yaw * settings.horizontal_strength if settings.correct_horizontal else 0.0
    if focal_is_a_guess:
        p *= settings.uncertain_pitch_damping
    rmax = math.radians(settings.max_roll_deg)
    pmax = math.radians(settings.max_pitch_deg)
    ymax = math.radians(settings.max_horizontal_deg)
    clamped = abs(r) > rmax or abs(p) > pmax or abs(y) > ymax
    return (float(np.clip(r, -rmax, rmax)), float(np.clip(p, -pmax, pmax)),
            float(np.clip(y, -ymax, ymax)), clamped)


def _max_safe_yaw(w: int, h: int, f: float, roll: float, pitch: float,
                  max_area: float = 4.0) -> float:
    """Largest |yaw| (radians) whose warped quad stays under ``max_area``× source."""
    lo, hi = 0.0, math.radians(85)
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        K = G.intrinsics(f, w / 2.0, h / 2.0)
        R = G.correction_rotation(roll, pitch, mid)
        H = G.homography(K, R)
        q = warped_quad(H, w, h)
        if quad_area(q) / float(w * h) <= max_area:
            lo = mid
        else:
            hi = mid
    return lo


def build(w: int, h: int, f: float, roll: float, pitch: float,
          yaw: float = 0.0, max_area: float = 4.0) -> np.ndarray:
    """Build the correction homography: pure K·R·K⁻¹ camera rotation.

    This is the only mathematically valid perspective correction — it undoes
    the camera's rotation relative to the scene.  No 4-point remapping or
    1/cos stretching is applied, because those change proportions rather than
    correcting them.  The output canvas may be larger than the input (the
    rotation opens up corners that the fill band covers); it is never smaller.
    """
    K = G.intrinsics(f, w / 2.0, h / 2.0)
    R = G.correction_rotation(roll, pitch, yaw)
    return G.homography(K, R)


def _facade_perspective(w: int, h: int, vert_segs: np.ndarray,
                        horiz_segs: np.ndarray) -> np.ndarray | None:
    """Estimate 4 facade corners from line segments and build a perspective
    transform that maps them to a fronto-parallel rectangle.

    The left/right edges come from the extreme vertical lines; the top/bottom
    from the extreme horizontal lines.  The target rectangle is sized so the
    facade fills ~70% of the frame (margin handled by the caller's reframe).

    Returns a 3×3 homography or None if the geometry is degenerate.
    """
    # Vertical lines: x-position at mid-height
    v_mids_x = (vert_segs[:, 0] + vert_segs[:, 2]) / 2.0
    h_mids_y = (horiz_segs[:, 1] + horiz_segs[:, 3]) / 2.0

    # Use the 5th and 95th percentiles to be robust against outliers
    x_left = float(np.percentile(v_mids_x, 5))
    x_right = float(np.percentile(v_mids_x, 95))
    y_top = float(np.percentile(h_mids_y, 5))
    y_bot = float(np.percentile(h_mids_y, 95))

    # Sanity: the facade must be a reasonable fraction of the frame
    fw = x_right - x_left
    fh = y_bot - y_top
    if fw < w * 0.1 or fh < h * 0.1 or fw > w * 1.2 or fh > h * 1.2:
        return None

    # Source 4 corners (top-left, top-right, bottom-right, bottom-left)
    src = np.float32([[x_left, y_top], [x_right, y_top],
                      [x_right, y_bot], [x_left, y_bot]])

    # Target: centred rectangle, facade fills 70% of the frame
    fill = 0.70
    tw, th = w * fill, h * fill
    tx, ty = (w - tw) / 2.0, (h - th) / 2.0
    dst = np.float32([[tx, ty], [tx + tw, ty],
                      [tx + tw, ty + th], [tx, ty + th]])

    H = cv2.getPerspectiveTransform(src, dst)
    return H


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


def max_inscribed_rect(quad: np.ndarray, aspect: float | None):
    """Largest axis-aligned rectangle of the given aspect inside ``quad``.

    Unlike :func:`inscribed_rect` the position is not anchored anywhere -- the
    rectangle may sit wherever it fits best.  With the half-height written as
    ``hh = hw / aspect`` every edge of the convex quad is one linear constraint
    on ``(cx, cy, hw)``, so the maximum is a vertex of four half-planes and is
    found by trying all C(4, 3) triples exactly.  No search, no local optimum:
    the answer is the largest rectangle, period, which is what "maximise the
    untouched pixels" means.

    Returns ``[x0, y0, x1, y1]`` or ``None`` when nothing of positive area fits
    (a degenerate quad).  The batch path keeps :func:`inscribed_rect` on
    purpose: it anchors on the mapped image centre so the composition survives,
    and that is worth a little area there.  A crop pressed by hand wants the
    area.
    """
    if aspect is None or aspect <= 0:
        aspect = 1.0
    q = np.asarray(quad, dtype=float)
    c = q.mean(axis=0)
    n = len(q)
    rows, bounds = [], []
    for i in range(n):
        a, b = q[i], q[(i + 1) % n]
        e = b - a
        nx, ny = float(e[1]), float(-e[0])
        if nx * (c[0] - a[0]) + ny * (c[1] - a[1]) > 0:
            nx, ny = -nx, -ny               # outward normal
        d = nx * a[0] + ny * a[1]
        # the farthest rect corner from the edge is at distance
        # |nx|*hw + |ny|*hh along the normal, so the constraint is
        #   nx*cx + ny*cy + (|nx| + |ny|/aspect)*hw <= d
        rows.append((nx, ny, abs(nx) + abs(ny) / aspect))
        bounds.append(d)
    best = None
    m = len(rows)
    for i in range(m):
        for j in range(i + 1, m):
            for k in range(j + 1, m):
                A = np.array([[rows[i][0], rows[i][1], rows[i][2]],
                              [rows[j][0], rows[j][1], rows[j][2]],
                              [rows[k][0], rows[k][1], rows[k][2]]])
                if abs(np.linalg.det(A)) < 1e-12:
                    continue
                try:
                    cx, cy, hw = np.linalg.solve(A, [bounds[i], bounds[j], bounds[k]])
                except np.linalg.LinAlgError:
                    continue
                cx, cy, hw = float(cx), float(cy), float(hw)
                if hw <= 0:
                    continue
                if all(r[0] * cx + r[1] * cy + r[2] * hw <= bnd + 1e-9
                       for r, bnd in zip(rows, bounds)):
                    if best is None or hw > best[2]:
                        best = (cx, cy, hw)
    if best is None:
        return None
    cx, cy, hw = best
    hh = hw / aspect
    return np.array([cx - hw, cy - hh, cx + hw, cy + hh])


def _whole_frame(H, quad, img_w, img_h, settings, area_ratio):
    """The full warped quad on a canvas big enough to hold it.

    Nothing of the photograph is discarded; the corners the rotation opens up
    are filled by ``apply``.  When the quad inflates beyond ``max_area_ratio``
    times the source, the output is cropped (centred on the warped image centre)
    to that limit — this trims the Telea fill / garbage-pixel zones that a large
    yaw warp opens up at the edges.  The crop never makes the output smaller
    than the source.  ``keep_size`` scales the result back to the original
    pixel dimensions, so a batch keeps a consistent size.
    """
    x0, y0 = quad.min(axis=0)
    x1, y1 = quad.max(axis=0)
    ow, oh = int(round(x1 - x0)), int(round(y1 - y0))
    if ow < 8 or oh < 8:
        return None
    # Crop to max_area_ratio × source when the quad inflates too much.
    # This trims the fill/garbage zones at the edges of a large-yaw warp.
    max_area = getattr(settings, "max_area_ratio", 4.0)
    if ow * oh > max_area * img_w * img_h:
        s_crop = math.sqrt(img_w * img_h / (ow * oh) * max_area)
        ow_c, oh_c = int(round(ow * s_crop)), int(round(oh * s_crop))
        # Centre the crop on the warped image centre
        cx_q = (x0 + x1) / 2.0
        cy_q = (y0 + y1) / 2.0
        cx_c = cx_q - ow_c / (2.0 * s_crop)
        cy_c = cy_q - oh_c / (2.0 * s_crop)
        T = np.array([[s_crop, 0, -cx_c * s_crop],
                      [0, s_crop, -cy_c * s_crop],
                      [0, 0, 1]], dtype=float)
        # Never smaller than source
        ow, oh = max(ow_c, img_w), max(oh_c, img_h)
        if settings.keep_size:
            s = min(img_w / float(ow), img_h / float(oh))
            S = np.array([[s, 0, 0], [0, s, 0], [0, 0, 1]], dtype=float)
            return S @ T @ H, img_w, img_h, 1.0, area_ratio
        return T @ H, ow, oh, 1.0, area_ratio
    T = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]], dtype=float)
    if settings.keep_size:
        s = min(img_w / float(ow), img_h / float(oh))
        S = np.array([[s, 0, 0], [0, s, 0], [0, 0, 1]], dtype=float)
        return S @ T @ H, img_w, img_h, 1.0, area_ratio
    return T @ H, max(ow, 1), max(oh, 1), 1.0, area_ratio


def plan(img_w: int, img_h: int, H: np.ndarray, settings,
         line_segs: np.ndarray | None = None, yaw: float = 0.0):
    """Work out the output canvas.

    Returns ``(H_total, out_w, out_h, coverage, area_ratio)`` where ``coverage``
    is the fraction of the original frame that survives -- useful in the log and
    as a sanity gate.

    ``crop="auto"`` is the reason this is not just a crop.  Correcting a strong
    convergence and then cropping to the largest inscribed rectangle can cost a
    quarter of the picture, and a quarter of the picture is a real loss to trade
    for straight verticals -- often a worse one than the convergence was.  So
    auto crops only while the loss stays small (``crop_max_loss``, 5 % by
    default) and otherwise keeps the whole frame and pads the corners the
    rotation opened up.  The choice is per photograph, because whether the loss
    is small is a property of the photograph and not of the folder.

    When ``line_segs`` is provided (N×2×2 endpoints in full-resolution pixels)
    and the auto-crop would otherwise fall back to the whole frame, the plan
    instead crops to the bounding box of the detected lines plus a margin.
    This targets the architectural subject and discards the pad zones that a
    large yaw warp opens up (grass, sky, inpainted fill).
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
    if settings.crop == "auto" and (1.0 - coverage) > settings.crop_max_loss:
        # The rotation opened up corners; keep the whole frame and let the
        # fill band cover what the warp invented.  A reframe that scales the
        # facade down to fit a source-sized canvas would shrink the image,
        # which is forbidden: the output must never be smaller than the input.
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


def apply_undistorted(img: np.ndarray, H_total: np.ndarray, out_w: int, out_h: int,
                      settings, undist_map: tuple[np.ndarray, np.ndarray]):
    """Render the plan with a radial-distortion remap composed in.

    ``undist_map`` is ``(map_x, map_y)`` from ``distortion.undistort_map`` —
    per-pixel source coordinates that undo the lens's radial distortion at
    full resolution.  The composition (Stage 5: one resample, never two):

    1. For each output pixel, invert H_total to find where in the *undistorted*
       image it came from.
    2. Look up that location in the undistortion map to find the actual source
       pixel in the distorted image.

    The result is a single ``cv2.remap`` call — no intermediate buffer.
    """
    flags = _INTERP.get(settings.interpolation, cv2.INTER_LANCZOS4)
    colour = pad_colour(getattr(settings, "pad", "edge"))
    map_x, map_y = undist_map

    H_inv = np.linalg.inv(H_total)
    ys, xs = np.mgrid[0:out_h, 0:out_w]
    ones = np.ones_like(xs)
    pts = np.stack([xs.ravel(), ys.ravel(), ones.ravel()], axis=0).astype(np.float64)
    src_pts = H_inv @ pts
    src_pts /= src_pts[2:3, :]
    sx = src_pts[0].reshape(out_h, out_w)
    sy = src_pts[1].reshape(out_h, out_w)

    sh, sw = img.shape[:2]
    sx_c = np.clip(sx, 0, sw - 1)
    sy_c = np.clip(sy, 0, sh - 1)

    composed_x = _sample_map(map_x, sx_c, sy_c)
    composed_y = _sample_map(map_y, sx_c, sy_c)

    border = cv2.BORDER_CONSTANT if colour is not None else cv2.BORDER_REPLICATE
    bval = colour if colour is not None else 0
    return cv2.remap(img, composed_x.astype(np.float32), composed_y.astype(np.float32),
                     flags, borderMode=border, borderValue=bval)


def _sample_map(m: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Bilinear sample of a 2-D map at fractional (x, y) coordinates."""
    h, w = m.shape
    x0 = np.clip(np.floor(x).astype(int), 0, w - 1)
    y0 = np.clip(np.floor(y).astype(int), 0, h - 1)
    x1 = np.clip(x0 + 1, 0, w - 1)
    y1 = np.clip(y0 + 1, 0, h - 1)
    fx = (x - x0).astype(np.float32)
    fy = (y - y0).astype(np.float32)
    return (m[y0, x0] * (1 - fx) * (1 - fy) + m[y0, x1] * fx * (1 - fy) +
            m[y1, x0] * (1 - fx) * fy + m[y1, x1] * fx * fy)


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
