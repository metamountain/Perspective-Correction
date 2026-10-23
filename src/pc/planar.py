"""Planar rectification: flatten a surface shot at an angle into a front-on view.

The rest of the package answers "how was the camera tilted" with a pure camera
rotation, ``H = K R K^-1`` -- three degrees of freedom, no shear, no scale.  A
facade, floor or document shot *at* an angle wants a different answer: "what does
this plane look like straight on".  That is a general homography (eight degrees
of freedom), and it is the one place in the project where shear and scale are
allowed, because they are what the oblique view actually contains.

Four points fix a homography exactly -- no focal length, no RANSAC, no guess.
That is why this is a *manual* tool and nothing else: the user clicks the four
corners of the surface and it flattens.  Automatic routing into planar mode was
considered and dropped on purpose.  A shot with strong perspective distortion is
exactly the case where line geometry cannot decide for you -- the focal length is
under-determined by lines alone, so "this looks like an oblique facade" is not a
reliable trigger, and guessing wrong warps a good photo into a sliver.  When in
doubt the rotation path (``H = K R K^-1``) stays in charge; planar is only for
the surface a person has pointed at.

Everything here is a pure function of its inputs and headless-testable; the GUI
is only the shell that collects four clicks and hands them to ``ReviewSession``.
"""
from __future__ import annotations

import cv2
import numpy as np


def homography_from_quad(src, dst):
    """The 3x3 homography mapping four ``src`` points onto four ``dst`` points.

    Exact for a planar correspondence: four point pairs determine the eight free
    parameters of a homography, so this is a solve, not a fit -- there is no
    inlier/outlier question and no randomness to seed.  Point order must match
    (top-left, top-right, bottom-right, bottom-left).
    """
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    if src.shape != (4, 2) or dst.shape != (4, 2):
        raise ValueError("homography_from_quad wants two (4, 2) point arrays")
    # float32, not float64: cv2.getPerspectiveTransform asserts CV_32F on its
    # inputs, so the shape check above happens in float64 and only the call
    # itself is narrowed. The solve
    # itself is done in double internally and H comes back float64, and a pixel
    # coordinate needs about three of float32's seven digits, so nothing
    # measurable is lost.
    return cv2.getPerspectiveTransform(src.astype(np.float32),
                                       dst.astype(np.float32))


FOCAL_RANGE = (0.25, 6.0)
"""Focal lengths, in multiples of the photograph's long edge, that a focal
recovered from four clicked corners may take (about 9 mm to 210 mm on a 35 mm
frame). Outside it the clicks were too rough or the quad no rectangle, and the
estimator's focal is used instead."""


def _side_vectors(quad, center):
    """``(n2, n3)``: the width and height directions of the 3D rectangle behind
    ``quad``, in camera coordinates with the focal still unapplied (x and y in
    pixels, z = 1 units) -- Zhang & He's construction. None for a degenerate quad.
    """
    tl, tr, br, bl = [np.array([x - center[0], y - center[1], 1.0])
                      for x, y in np.asarray(quad, dtype=np.float64)]
    m1, m2, m3, m4 = bl, br, tl, tr
    d2, d3 = np.dot(np.cross(m2, m4), m3), np.dot(np.cross(m3, m4), m2)
    if abs(d2) < 1e-12 or abs(d3) < 1e-12:
        return None
    k2 = np.dot(np.cross(m1, m4), m3) / d2
    k3 = np.dot(np.cross(m1, m4), m2) / d3
    return k2 * m2 - m1, k3 * m3 - m1


def quad_focal(quad, center):
    """Focal length in pixels implied by a quad that is a rectangle in 3D, or None.

    Zhang & He, "Whiteboard scanning and image enhancement" (2004): the two
    vanishing points of a rectangle's sides must be orthogonal directions, which
    fixes ``f`` once the principal point ``center`` is assumed. None when the
    quad has a pair of parallel sides (that vanishing point is at infinity and
    says nothing about ``f``) or when the answer is not a real focal length.
    """
    sv = _side_vectors(quad, center)
    if sv is None:
        return None
    n2, n3 = sv
    den = n2[2] * n3[2]
    # Sides parallel in the photograph leave n2 or n3 with no z part, and the
    # rest of the expression is then rounding noise, not a focal.
    scale = np.linalg.norm(n2[:2]) * np.linalg.norm(n3[:2])
    if abs(den) < 1e-9 * max(scale, 1e-12):
        return None
    f2 = -(n2[0] * n3[0] + n2[1] * n3[1]) / den
    return float(np.sqrt(f2)) if f2 > 0 else None


def true_aspect(quad, center, focal):
    """Width / height of the 3D rectangle behind ``quad`` at focal ``focal`` px.

    ``n2`` and ``n3`` (:func:`_side_vectors`) are the rectangle's two side
    directions; their lengths through the calibration matrix are the true side
    lengths up to one common scale.
    """
    sv = _side_vectors(quad, center)
    if sv is None or not focal or focal <= 0:
        return None
    n2, n3 = sv
    a = np.diag([1.0 / focal ** 2, 1.0 / focal ** 2, 1.0])
    num, den = float(n2 @ a @ n2), float(n3 @ a @ n3)
    if num <= 0 or den <= 0:
        return None
    return float(np.sqrt(num / den))


def facade_angle(quad, center, focal):
    """How far the rectangle's width turns away from the image plane, radians.

    The rectangle's own yaw: 0 for a facade seen square on, 60 deg for one seen
    as obliquely as altbau. It is the angle horizontal auto would rotate by, so
    the same ``warp.yaw_x_scale`` can follow it (Fable's review, 2026-10-23:
    checked 30.0 / 45.0 / 60.0 deg on synthetic facades, 29.7 / 44.5 / 59.1 at
    8 deg pitch).
    """
    sv = _side_vectors(quad, center)
    if sv is None or not focal or focal <= 0:
        return None
    n2 = sv[0]
    dx, dy, dz = n2[0] / focal, n2[1] / focal, n2[2]
    return float(np.arctan2(abs(dz), np.hypot(dx, dy)))


def target_size(quad, center=None, focal=None, long_edge=None, foreshorten=False):
    """Output ``(w, h)`` for a quad: its true proportions, never fewer pixels.

    **Proportions** (2026-10-23, measured on synthetic boxes): the apparent edge
    lengths -- the document-scanner convention this used before -- keep the
    foreshortening, so a 2:1 facade seen at 40 deg came out 1.28:1, 36 % too
    narrow. With ``center`` given, the true aspect is recovered from the quad
    itself (:func:`quad_focal`); when the quad cannot fix the focal -- a pair of
    sides parallel in the photograph, or a result outside :data:`FOCAL_RANGE`
    times ``long_edge`` -- ``focal`` (the estimator's) stands in. With neither,
    the apparent aspect is kept as before.

    **No compression by default** (user, 2026-10-23: "es muessen nach
    Entzerrung wieder Quadrate rauskommen"). Measured on rendered Rubik cubes
    (4x4 squares a face, yaw 25-55 deg): the true aspect gives cells of 1.000
    with the true focal and 0.93-1.06 with the estimator's (no EXIF); the
    horizontal-auto compression, the default for a few hours that day, made
    them 0.66-0.95. ``foreshorten=True`` still applies ``warp.yaw_x_scale`` of
    the facade's own yaw (:func:`facade_angle`), as horizontal auto does.

    **Size**: at least the longer of each pair of opposite edges and at least
    the quad's bounding box, so the warp ADDS pixels for the perspective
    expansion instead of compressing them; the aspect is then reached by growing
    one side, never by shrinking the other.
    """
    quad = np.asarray(quad, dtype=np.float64)
    p0, p1, p2, p3 = quad
    top = float(np.linalg.norm(p1 - p0)); bottom = float(np.linalg.norm(p2 - p3))
    left = float(np.linalg.norm(p3 - p0)); right = float(np.linalg.norm(p2 - p1))
    w = max(1, int(round(max(top, bottom))))
    h = max(1, int(round(max(left, right))))
    # Never shrink below the source quad's bounding box: the warp must add
    # pixels for the perspective expansion, not compress existing ones.
    bbox_w = int(np.ceil(max(p0[0], p1[0], p2[0], p3[0]) - min(p0[0], p1[0], p2[0], p3[0])))
    bbox_h = int(np.ceil(max(p0[1], p1[1], p2[1], p3[1]) - min(p0[1], p1[1], p2[1], p3[1])))
    w = max(w, bbox_w)
    h = max(h, bbox_h)
    aspect = None
    if center is not None:
        f = quad_focal(quad, center)
        lo, hi = FOCAL_RANGE
        edge = long_edge or max(bbox_w, bbox_h)
        if f is None or not (lo * edge <= f <= hi * edge):
            f = focal
        elif focal:
            # Both, geometric mean: over 3263 synthetic box faces with 1.5 px
            # click noise and the estimator's focal off by up to 30 %, the
            # quad alone erred 9.1 % mean / 21.6 % p90, the estimator alone
            # 8.7 / 19.0, the blend 6.9 / 15.4 (apparent edges: 84 / 194).
            f = float(np.sqrt(f * focal))
        aspect = true_aspect(quad, center, f) if f else None
        if aspect is not None and foreshorten:
            # Rectangle follows horizontal auto (user-calibrated sqrt(cos yaw),
            # "Verkuerzung auf 70% bei altbau"): one facade, one width, whichever
            # tool straightened it.
            from . import warp as W
            theta = facade_angle(quad, center, f)
            if theta is not None:
                aspect *= W.yaw_x_scale(theta)
    if aspect is not None and aspect > 0:
        if w / float(h) < aspect:
            w = int(round(h * aspect))
        else:
            h = int(round(w / aspect))
    return max(1, w), max(1, h)


def quad_area(quad):
    """Twice the signed area of a four-point quad (shoelace).  Zero => degenerate."""
    q = np.asarray(quad, dtype=np.float64)
    x, y = q[:, 0], q[:, 1]
    return float(np.sum(x * np.roll(y, -1)) - np.sum(y * np.roll(x, -1)))


def order_quad(quad):
    """The four corners as top-left, top-right, bottom-right, bottom-left.

    Whatever order they were clicked in. ``transform_for`` maps corner 0 to the
    output's top-left and corner 1 to its top-right, so before this the picture
    came out turned a quarter or a half depending on which corner somebody
    happened to start with -- and the status line had to teach the order, which
    is a user interface apologising for its arithmetic.

    Sorted by angle around the centroid, which puts them in cyclic order for any
    quad however sheared, then rotated so the cycle starts at the corner nearest
    the top-left. Image y grows DOWNWARD, so increasing angle runs clockwise on
    screen and the cycle from top-left is exactly TL, TR, BR, BL.
    """
    q = np.asarray(quad, dtype=np.float64)
    if len(q) != 4:
        return q
    c = q.mean(axis=0)
    order = np.argsort(np.arctan2(q[:, 1] - c[1], q[:, 0] - c[0]))
    q = q[order]
    return np.roll(q, -int(np.argmin(q[:, 0] + q[:, 1])), axis=0)


CROP_MARGIN = 0.20
"""Picture kept around the rectified quad, as a share of its width/height per
side (user, 2026-10-23: "rechteck modus crop +20%. lieber manuell wenn nötig").
The quad alone cut everything outside the four clicks; with the margin the
rest is there to crop by hand. The same 20 % as ``warp.STRIP_CROP_MARGIN``."""


def transform_for(quad, margin=0.0, center=None, focal=None, long_edge=None):
    """``(H, w, h)`` mapping a source quad to an axis-aligned rectangle.

    ``H`` sends the four quad corners onto the four corners of a rectangle the
    size :func:`target_size` gives, inset by ``margin`` times its width (left and
    right) and height (top and bottom) on a canvas that much larger.  At
    ``margin=0`` the whole output is the warped quad -- no band to fill; with a
    margin, part of it may lie outside the photograph and is the fill's job.
    ``center``, ``focal`` and ``long_edge`` go to :func:`target_size` and buy
    the true proportions. Raises on a degenerate (collinear or repeated-point)
    quad rather than returning a broken homography that would warp the image
    into a sliver.
    """
    # Sorted here rather than where the corners are stored: dragging a corner
    # must keep hold of the one that was grabbed, so the stored quad keeps the
    # click order and only the arithmetic sees the canonical one.
    q = order_quad(np.asarray(quad, dtype=np.float64))
    if abs(quad_area(q)) < 1e-6:
        raise ValueError("planar quad is degenerate (collinear or repeated points)")
    w, h = target_size(q, center=center, focal=focal, long_edge=long_edge)
    mx, my = int(round(margin * w)), int(round(margin * h))
    dst = np.array([[mx, my], [mx + w - 1, my], [mx + w - 1, my + h - 1],
                    [mx, my + h - 1]], dtype=np.float64)
    return homography_from_quad(q, dst), w + 2 * mx, h + 2 * my


KEEP_MARGIN = 0.50
"""How much picture the rectified canvas keeps around the quad, as a fraction
of the quad's own width and height on each side -- never more than the
photograph itself reaches. The default crop stays at :data:`CROP_MARGIN`; the
rest is there so a hand crop can be dragged OUT (user, 2026-10-23: "maxime dass
keine pixel verloren gehen, oder nur moderat"). Measured on the Muenzstrasse
row: quad + 20 % kept 14-33 % of the photograph, the whole photograph would
have needed 7-21x the canvas, nearly all of it fill."""


def canvas_for(quad, src_w, src_h, center=None, focal=None, long_edge=None,
               keep=KEEP_MARGIN, crop=CROP_MARGIN):
    """``(H, cw, ch, box)``: the rectified quad on a canvas with room around it.

    The quad becomes the rectangle :func:`target_size` gives. The canvas
    reaches ``keep`` times that rectangle beyond it on every side, cut back to
    where the warped photograph actually ends, and never smaller than the
    ``crop`` box. ``box`` is ``(x0, y0, x1, y1)`` in canvas pixels: the quad
    plus ``crop`` -- what :func:`transform_for` used to make the whole canvas,
    and now the default crop.
    """
    q = order_quad(np.asarray(quad, dtype=np.float64))
    if abs(quad_area(q)) < 1e-6:
        raise ValueError("planar quad is degenerate (collinear or repeated points)")
    w, h = target_size(q, center=center, focal=focal, long_edge=long_edge)
    rect = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float64)
    H0 = homography_from_quad(q, rect)
    kx0, ky0 = -keep * w, -keep * h
    kx1, ky1 = (w - 1) + keep * w, (h - 1) + keep * h
    # Where the photograph ends, from its border sampled densely. A border
    # point behind the camera (w <= 0, past the vanishing line) means the
    # outline runs off to infinity, so nothing is cut back at all.
    n = 64
    t = np.linspace(0.0, 1.0, n)
    border = np.concatenate([
        np.stack([t * (src_w - 1), np.zeros(n)], 1),
        np.stack([np.full(n, src_w - 1.0), t * (src_h - 1)], 1),
        np.stack([t * (src_w - 1), np.full(n, src_h - 1.0)], 1),
        np.stack([np.zeros(n), t * (src_h - 1)], 1)])
    hp = np.c_[border, np.ones(len(border))] @ H0.T
    sign = np.sign((np.c_[q, np.ones(4)] @ H0.T)[0, 2])
    if np.all(hp[:, 2] * sign > 1e-12):
        p = hp[:, :2] / hp[:, 2:3]
        kx0, ky0 = max(kx0, p[:, 0].min()), max(ky0, p[:, 1].min())
        kx1, ky1 = min(kx1, p[:, 0].max()), min(ky1, p[:, 1].max())
    # Centred on the quad (user, 2026-10-23: "roi centered"): the photograph
    # usually ends sooner on one side, and the facade then sat in a corner of
    # its own canvas. The narrower side's room is used on both.
    rx = max(0.0, min(-kx0, kx1 - (w - 1)))
    ry = max(0.0, min(-ky0, ky1 - (h - 1)))
    kx0, kx1, ky0, ky1 = -rx, (w - 1) + rx, -ry, (h - 1) + ry
    bx0, by0 = -crop * w, -crop * h
    bx1, by1 = (w - 1) + crop * w, (h - 1) + crop * h
    x0, y0 = int(np.floor(min(kx0, bx0))), int(np.floor(min(ky0, by0)))
    x1, y1 = int(np.ceil(max(kx1, bx1))), int(np.ceil(max(ky1, by1)))
    T = np.array([[1.0, 0.0, -x0], [0.0, 1.0, -y0], [0.0, 0.0, 1.0]])
    box = (bx0 - x0, by0 - y0, bx1 - x0, by1 - y0)
    return T @ H0, x1 - x0 + 1, y1 - y0 + 1, box


# ---------------------------------------------------------------------------
# Automatic facade corner detection (GLNet-style)
# ---------------------------------------------------------------------------

def _line_intercept_y(seg):
    """Y-intercept of a line segment [x0,y0,x1,y1]. None if vertical."""
    x0, y0, x1, y1 = float(seg[0]), float(seg[1]), float(seg[2]), float(seg[3])
    dx = x1 - x0
    if abs(dx) < 1e-9:
        return None
    m = (y1 - y0) / dx
    return y0 - m * x0


def _line_intercept_x(seg):
    """X-intercept of a line segment [x0,y0,x1,y1]. None if horizontal or vertical."""
    x0, y0, x1, y1 = float(seg[0]), float(seg[1]), float(seg[2]), float(seg[3])
    dx = x1 - x0
    dy = y1 - y0
    if abs(dx) < 1e-9:
        return None  # vertical line: no x-intercept in the usual sense
    m = dy / dx
    if abs(m) < 1e-9:
        return None  # horizontal line: parallel to x-axis
    return -(y0 - m * x0) / m


def _intersect(l1, l2):
    """Intersection of two line segments treated as infinite lines. Returns (x,y) or None."""
    x1, y1, x2, y2 = float(l1[0]), float(l1[1]), float(l1[2]), float(l1[3])
    x3, y3, x4, y4 = float(l2[0]), float(l2[1]), float(l2[2]), float(l2[3])
    a1, b1 = y2 - y1, x1 - x2
    c1 = a1 * x1 + b1 * y1
    a2, b2 = y4 - y3, x3 - x4
    c2 = a2 * x3 + b2 * y3
    det = a1 * b2 - a2 * b1
    if abs(det) < 1e-9:
        return None
    return ((b2 * c1 - b1 * c2) / det, (a1 * c2 - a2 * c1) / det)


def auto_facade_corners(vert_segs, horiz_segs, img_w, img_h):
    """Detect 4 facade corners from line segments (GLNet-style).

    Splits lines into 4 positional buckets relative to the image center,
    picks the extreme line per bucket, and computes their pairwise
    intersections.  Falls back to the next-most-extreme line if an
    intersection lands outside the image.

    Returns ``[lu, ur, rl, ll]`` (top-left, top-right, bottom-right,
    bottom-left) as a list of ``(x, y)`` tuples in image pixels, or None
    if fewer than 2 verticals and 2 horizontals are available.
    """
    if len(vert_segs) < 2 or len(horiz_segs) < 2:
        return None

    cx, cy = img_w / 2.0, img_h / 2.0

    # Bucket by position relative to image center
    upper = [s for s in horiz_segs if (float(s[1]) + float(s[3])) / 2.0 < cy]
    lower = [s for s in horiz_segs if (float(s[1]) + float(s[3])) / 2.0 > cy]
    left = [s for s in vert_segs if (float(s[0]) + float(s[2])) / 2.0 < cx]
    right = [s for s in vert_segs if (float(s[0]) + float(s[2])) / 2.0 > cx]

    # Fallback: if a bucket is empty, use all lines of that orientation
    if not upper:
        upper = list(horiz_segs)
    if not lower:
        lower = list(horiz_segs)
    if not left:
        left = list(vert_segs)
    if not right:
        right = list(vert_segs)

    # Sort each bucket by extremity (using mid-point, not intercept —
    # intercepts are unreliable for short segments and near-parallel lines)
    # Upper: topmost by mid-y (smallest y = highest in image)
    upper.sort(key=lambda s: (float(s[1]) + float(s[3])) / 2.0)
    # Lower: bottommost by mid-y (largest y = lowest in image)
    lower.sort(key=lambda s: (float(s[1]) + float(s[3])) / 2.0, reverse=True)
    # Left: leftmost by mid-x
    left.sort(key=lambda s: (float(s[0]) + float(s[2])) / 2.0)
    # Right: rightmost by mid-x
    right.sort(key=lambda s: (float(s[0]) + float(s[2])) / 2.0, reverse=True)

    def in_image(pt):
        """Point must be strictly inside the image frame."""
        if pt is None:
            return False
        x, y = pt
        return (0 <= x < img_w) and (0 <= y < img_h)

    # Strategy: try combinations of lines from each bucket.  Start with the
    # most extreme lines and work inward until all 4 intersections are inside
    # the image.  For busy facades where the outermost lines intersect outside
    # the frame, this naturally finds the innermost usable combination.
    n_try = min(15, max(len(upper), len(lower), len(left), len(right)))
    for i_up in range(min(n_try, len(upper))):
        for i_lo in range(min(n_try, len(lower))):
            for i_le in range(min(n_try, len(left))):
                for i_ri in range(min(n_try, len(right))):
                    up = upper[i_up]
                    lo = lower[i_lo]
                    le = left[i_le]
                    ri = right[i_ri]

                    lu = _intersect(le, up)   # top-left
                    ur = _intersect(up, ri)   # top-right
                    rl = _intersect(ri, lo)   # bottom-right
                    ll = _intersect(lo, le)   # bottom-left

                    if all(in_image(p) for p in (lu, ur, rl, ll)):
                        return [lu, ur, rl, ll]

    # Fallback: relax to "at least 3 of 4 inside" and clamp the outlier
    # to the nearest image border.  This handles facades where one corner
    # (usually top) is above the frame.
    best = None
    for i_up in range(min(5, len(upper))):
        for i_lo in range(min(5, len(lower))):
            for i_le in range(min(5, len(left))):
                for i_ri in range(min(5, len(right))):
                    up = upper[i_up]
                    lo = lower[i_lo]
                    le = left[i_le]
                    ri = right[i_ri]
                    pts = [_intersect(le, up), _intersect(up, ri),
                           _intersect(ri, lo), _intersect(lo, le)]
                    if any(p is None for p in pts):
                        continue
                    n_in = sum(1 for p in pts if in_image(p))
                    if n_in >= 3:
                        # Clamp out-of-image points to the border
                        clamped = []
                        for p in pts:
                            x = max(0, min(img_w - 1, p[0]))
                            y = max(0, min(img_h - 1, p[1]))
                            clamped.append((x, y))
                        if best is None or n_in > best[0]:
                            best = (n_in, clamped)
    if best is not None:
        return best[1]

    return None
