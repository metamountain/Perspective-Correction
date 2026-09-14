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
    """
    quad = np.asarray(quad, dtype=np.float64)
    p0, p1, p2, p3 = quad
    top = float(np.linalg.norm(p1 - p0)); bottom = float(np.linalg.norm(p2 - p3))
    left = float(np.linalg.norm(p3 - p0)); right = float(np.linalg.norm(p2 - p1))
    return max(1, int(round(max(top, bottom)))), max(1, int(round(max(left, right))))


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
