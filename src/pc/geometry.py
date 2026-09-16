"""Projective geometry for upright correction.

Everything here is written so that a vanishing point at infinity -- the case of
a perfectly level camera, which is the single most common input -- is an
ordinary, well behaved value rather than a division by zero.  Vanishing points
are therefore kept as unit-norm homogeneous 3-vectors and never dehomogenised
in any code path that has to work for every image.

Camera convention is OpenCV's: x right, y down, z forward.  "Up" in the image
is therefore ``(0, -1, 0)``.
"""
from __future__ import annotations

import numpy as np

UP = np.array([0.0, -1.0, 0.0])


# --------------------------------------------------------------------------
# lines and points
# --------------------------------------------------------------------------
def lines_from_segments(seg: np.ndarray) -> np.ndarray:
    """Homogeneous lines through segments ``(N, 4)`` = ``x0, y0, x1, y1``.

    Normalised so that ``a**2 + b**2 == 1``; then ``|line . (x, y, 1)|`` is the
    euclidean point-to-line distance in pixels.
    """
    p0 = np.column_stack([seg[:, 0], seg[:, 1], np.ones(len(seg))])
    p1 = np.column_stack([seg[:, 2], seg[:, 3], np.ones(len(seg))])
    lines = np.cross(p0, p1)
    n = np.linalg.norm(lines[:, :2], axis=1)
    n[n == 0] = 1e-12
    return lines / n[:, None]


def segment_midpoints(seg: np.ndarray) -> np.ndarray:
    return np.column_stack([(seg[:, 0] + seg[:, 2]) * 0.5, (seg[:, 1] + seg[:, 3]) * 0.5])


def segment_directions(seg: np.ndarray) -> np.ndarray:
    d = np.column_stack([seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1]])
    n = np.linalg.norm(d, axis=1)
    n[n == 0] = 1e-12
    return d / n[:, None]


def segment_lengths(seg: np.ndarray) -> np.ndarray:
    return np.hypot(seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1])


def normalize_vp(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    if n < 1e-12:
        # no direction to preserve; fall back to "up" (negative y in the
        # image's y-down convention), not down
        return np.array([0.0, -1.0, 0.0])
    v = v / n
    # fix the sign so that equal vanishing points compare equal
    k = int(np.argmax(np.abs(v)))
    return -v if v[k] < 0 else v


def bearing_to_vp(vp: np.ndarray, mid: np.ndarray) -> np.ndarray:
    """2-D direction from image points ``mid`` (N, 2) towards ``vp``.

    Works for ``vp[2] == 0`` (point at infinity), where the direction is simply
    ``vp[:2]`` for every location.  This is the piece the original
    Image-Rectification code got wrong: it computed ``vp[:2] / vp[2]`` first.
    """
    return vp[:2][None, :] - vp[2] * mid


def angular_residual(vp: np.ndarray, mid: np.ndarray, direction: np.ndarray) -> np.ndarray:
    """Undirected angle (radians, in ``[0, pi/2]``) between each segment and the
    ray from its midpoint to ``vp``."""
    g = bearing_to_vp(vp, mid)
    gn = np.linalg.norm(g, axis=1)
    gn[gn < 1e-12] = 1e-12
    cross = np.abs(direction[:, 0] * g[:, 1] - direction[:, 1] * g[:, 0]) / gn
    return np.arcsin(np.clip(cross, 0.0, 1.0))


def vp_from_two(l1: np.ndarray, l2: np.ndarray) -> np.ndarray:
    return normalize_vp(np.cross(l1, l2))


def refine_vp(lines: np.ndarray, mid: np.ndarray, weights: np.ndarray,
              vp0: np.ndarray, iters: int = 6) -> np.ndarray:
    """Weighted least squares refinement of a vanishing point.

    Minimises ``sum_i w_i * sin(theta_i)**2`` where ``theta_i`` is the angular
    residual of line *i*.  Because ``line . vp == |g| * sin(theta)`` for a
    length-normalised line whose midpoint is ``mid`` (``g`` = bearing to the
    vanishing point), dividing each algebraic residual by ``|g|**2`` turns the
    cheap eigenvector solution into the angular one.  Iterating that reweighting
    is plain IRLS and converges in a handful of passes.
    """
    if len(lines) < 2:
        return normalize_vp(vp0)
    v = normalize_vp(vp0)
    for _ in range(iters):
        g = bearing_to_vp(v, mid)
        gn2 = np.sum(g * g, axis=1)
        gn2[gn2 < 1e-12] = 1e-12
        w = weights / gn2
        m = (lines * w[:, None]).T @ lines
        try:
            _, vecs = np.linalg.eigh(m)
        except np.linalg.LinAlgError:
            break
        nxt = normalize_vp(vecs[:, 0])
        if np.linalg.norm(nxt - v) < 1e-12:
            v = nxt
            break
        v = nxt
    return v


# --------------------------------------------------------------------------
# camera
# --------------------------------------------------------------------------
def intrinsics(f: float, cx: float, cy: float) -> np.ndarray:
    return np.array([[f, 0.0, cx], [0.0, f, cy], [0.0, 0.0, 1.0]])


def rot_x(a: float) -> np.ndarray:
    c, s = np.cos(a), np.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def rot_z(a: float) -> np.ndarray:
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def rot_y(a: float) -> np.ndarray:
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def up_vector(vp: np.ndarray, K: np.ndarray) -> np.ndarray:
    """World-vertical direction in camera coordinates, as a unit vector that
    points *up* in the image."""
    u = np.linalg.inv(K) @ vp
    n = np.linalg.norm(u)
    if n < 1e-12:
        return UP.copy()
    u = u / n
    if float(u @ UP) < 0:
        u = -u
    return u


def roll_pitch_from_up(u: np.ndarray) -> tuple:
    """Decompose the tilt of ``u`` into (roll, pitch) in radians.

    ``roll`` is rotation about the optical axis -- what a photographer calls a
    crooked horizon -- and is independent of the focal length.  ``pitch`` is the
    remaining out-of-plane tilt, the part that makes verticals converge; it
    scales with the assumed focal length.
    """
    r = float(np.hypot(u[0], u[1]))
    roll = float(np.arctan2(u[0], -u[1]))
    pitch = float(np.arctan2(u[2], r))
    return roll, pitch


def up_from_roll_pitch(roll: float, pitch: float) -> np.ndarray:
    """Inverse of :func:`roll_pitch_from_up`."""
    return rot_z(roll) @ rot_x(-pitch) @ UP


def correction_rotation(roll: float, pitch: float, yaw: float = 0.0) -> np.ndarray:
    """Rotation that removes ``roll``, then ``pitch``, then ``yaw``.

    For the world vertical ``u`` that produced ``(roll, pitch)``,
    ``correction_rotation(roll, pitch, yaw) @ u == (0, -1, 0)`` for any ``yaw``:
    a yaw is a rotation about the world vertical and leaves it fixed, which is
    why ``(roll, pitch)`` can be read off ``u`` alone and yaw has to come from a
    horizontal vanishing point instead.  With ``yaw`` chosen from that point the
    same rotation also sends the dominant horizontal direction onto the image
    x-axis -- the facade becomes fronto-parallel.

    The default ``yaw == 0`` is exactly the old two-angle rotation, which has no
    yaw component and so "straightens the verticals without needlessly changing
    the horizontal perspective".  Yaw is the one case where changing the
    horizontal perspective is the point; it is gated and capped in
    ``warp.limit``, not here.
    """
    return rot_y(-yaw) @ rot_x(pitch) @ rot_z(-roll)


def homography(K: np.ndarray, R: np.ndarray) -> np.ndarray:
    H = K @ R @ np.linalg.inv(K)
    return H / H[2, 2]


def apply_h(H: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Map ``(N, 2)`` points through a homography."""
    p = np.column_stack([pts, np.ones(len(pts))]) @ H.T
    w = p[:, 2:3]
    w = np.where(np.abs(w) < 1e-12, 1e-12, w)
    return p[:, :2] / w


def image_corners(w: int, h: int) -> np.ndarray:
    return np.array([[0.0, 0.0], [w, 0.0], [w, h], [0.0, h]])


def plane_normals(lines: np.ndarray, K: np.ndarray) -> np.ndarray:
    """Unit normals of the back-projected interpretation planes, ``K^T l``.

    A world-vertical line satisfies ``normal . up == 0``; a world-horizontal
    direction ``b`` satisfies ``b . up == 0`` too, so both constraints live on
    the same unit sphere and can go into one cost function.
    """
    n = lines @ K
    norm = np.linalg.norm(n, axis=1)
    norm[norm < 1e-12] = 1e-12
    return n / norm[:, None]


def bearings(vps: np.ndarray, K: np.ndarray) -> np.ndarray:
    """Unit 3-D bearings of vanishing points, ``K^-1 v``."""
    b = vps @ np.linalg.inv(K).T
    norm = np.linalg.norm(b, axis=1)
    norm[norm < 1e-12] = 1e-12
    return b / norm[:, None]


def horizon_line(u: np.ndarray, K: np.ndarray) -> np.ndarray:
    """Image of the horizon: the polar line of the vertical vanishing point
    with respect to the image of the absolute conic, i.e. ``K^-T u``."""
    h = np.linalg.inv(K).T @ u
    n = np.linalg.norm(h[:2])
    return h / (n if n > 1e-12 else 1e-12)


def focal_from_orthogonal(vp1: np.ndarray, vp2: np.ndarray, cx: float, cy: float):
    """Focal length implied by two orthogonal vanishing points, or ``None``.

    With a centred principal point, square pixels and no skew, orthogonality
    gives ``f**2 = -(v1 - c) . (v2 - c)``.  Returns ``None`` when either point
    is at infinity or the dot product has the wrong sign (which means the two
    directions cannot be orthogonal under any focal length).
    """
    if abs(vp1[2]) < 1e-9 or abs(vp2[2]) < 1e-9:
        return None
    a = vp1[:2] / vp1[2] - np.array([cx, cy])
    b = vp2[:2] / vp2[2] - np.array([cx, cy])
    f2 = -float(a @ b)
    if not np.isfinite(f2) or f2 <= 1.0:
        return None
    return float(np.sqrt(f2))
