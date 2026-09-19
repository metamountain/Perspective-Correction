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


def target_size(quad):
    """Output ``(w, h)`` from a quad's apparent edge lengths.

    The width is the longer of the top and bottom edges, the height the longer of
    the left and right -- the document-scanner convention.  It preserves the
    *apparent* aspect of the surface as it sits in the photograph; recovering the
    true aspect would need the focal length, which a frontal facade does not give.

    The result is then scaled up (never down) so that the output canvas covers
    at least the source quad's bounding box.  This guarantees the warp ADDS
    pixels for the perspective expansion instead of compressing them -- a facade
    shot at an angle has apparent edges shorter than its true size, and mapping
    those short lengths onto the output would squeeze the image.
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
    return w, h


def quad_area(quad):
    """Twice the signed area of a four-point quad (shoelace).  Zero => degenerate."""
    q = np.asarray(quad, dtype=np.float64)
    x, y = q[:, 0], q[:, 1]
    return float(np.sum(x * np.roll(y, -1)) - np.sum(y * np.roll(x, -1)))


def transform_for(quad):
    """``(H, w, h)`` mapping a source quad to an axis-aligned rectangle at the origin.

    ``H`` sends the four quad corners onto the four corners of a ``(w, h)`` canvas,
    so the whole output is covered by the warped quad -- no band to fill, which is
    what separates planar rectification from the rotation path.  Raises on a
    degenerate (collinear or repeated-point) quad rather than returning a broken
    homography that would warp the image into a sliver.
    """
    q = np.asarray(quad, dtype=np.float64)
    if abs(quad_area(q)) < 1e-6:
        raise ValueError("planar quad is degenerate (collinear or repeated points)")
    w, h = target_size(q)
    dst = np.array([[0.0, 0.0], [w - 1, 0.0], [w - 1, h - 1], [0.0, h - 1]], dtype=np.float64)
    return homography_from_quad(q, dst), w, h


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
