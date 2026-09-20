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
        # Reachable: `intersect` calls this on `cross(l1, l2)`, which is the
        # zero vector when the two lines are the same line -- a duplicate
        # segment surviving the merge is enough.
        #
        # It used to return [0, -1, 0] with a comment explaining that this is
        # "up" in the image's y-down convention, "not down". That reasoning was
        # moot and the value was inconsistent. Moot, because the sign fix two
        # lines below ERASES direction on every other path: a vanishing point
        # is a line through the origin, not a ray, so normalize_vp([0, -1, 0])
        # already comes back [0, 1, 0]. Inconsistent, because the fallback was
        # the only return that skipped that fix, so the function broke the very
        # invariant the fix exists for -- its own comment says "so that equal
        # vanishing points compare equal", and normalize_vp(0) did not compare
        # equal to normalize_vp(anything pointing the same way).
        return np.array([0.0, 1.0, 0.0])
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


def rotation_from_two_vps(vp_h: np.ndarray, vp_v: np.ndarray,
                          K: np.ndarray) -> np.ndarray:
    """Rotation that squares ONE facade, built from its own two vanishing points.

    The rows of a rotation matrix ARE the axes of the frame it rotates into.
    Write the facade's horizontal direction in row 0 and its vertical in row 1
    and both land on the image axes by construction -- there is no optimisation
    to converge and no third estimate to disagree with the first two.  Both
    vanishing points go exactly to infinity, at 0 and 90 degrees.

    This is the difference from composing ``(roll, pitch, yaw)``: those come
    from three separate estimates -- the vertical vanishing point, whichever
    horizontal cluster had the most lines, and a focal length from a fourth
    source -- and nothing in that chain forces the two directions to end up
    perpendicular.  Measured on Platte_1.jpg, the composed angles leave the
    facade 6.12 degrees out of square; this leaves 0.15.

    ``vp_h`` must belong to the SAME facade as ``vp_v``.  On a corner view that
    means restricting the horizontal evidence to one face first (the ROI strip)
    -- the derivation assumes one plane, so one plane is a precondition, not a
    refinement.  See `horizontalauto theorie.md`.

    Noise leaves the two back-projected directions not quite perpendicular;
    Gram-Schmidt takes the nearest exactly-orthonormal frame, which is the most
    a measurement can honestly claim.
    """
    Kinv = np.linalg.inv(K)
    dx = Kinv @ np.asarray(vp_h, dtype=float)
    dy = Kinv @ np.asarray(vp_v, dtype=float)
    nx, ny = np.linalg.norm(dx), np.linalg.norm(dy)
    if nx < 1e-12 or ny < 1e-12:
        return np.eye(3)
    dx, dy = dx / nx, dy / ny
    # A vanishing point has no sign -- it is where a line family meets, and the
    # family runs both ways.  Pick the pair that leaves the picture upright and
    # unmirrored: image y grows downward, so the vertical points down.
    if dy[1] < 0:
        dy = -dy
    if dx[0] < 0:
        dx = -dx
    dy = dy - (dy @ dx) * dx
    n = np.linalg.norm(dy)
    if n < 1e-9:                      # the two directions collapsed: refuse
        return np.eye(3)
    dy = dy / n
    R = np.vstack([dx, dy, np.cross(dx, dy)])
    if np.linalg.det(R) < 0:
        R[2] = -R[2]
    return R


def roll_pitch_yaw_from_rotation(R: np.ndarray) -> tuple:
    """Decompose *R* into the ``(roll, pitch, yaw)`` ``correction_rotation`` takes.

    The pipeline speaks in three angles everywhere -- the sliders, the caps in
    ``warp.limit``, the report, the saved sidecar -- so a rotation that comes
    from somewhere else still has to arrive in that form.  This changes where
    the angles come from, not what downstream does with them.

    Exact inverse of ``correction_rotation`` up to floating point: that one
    composes ``rot_y(-yaw) @ rot_x(pitch) @ rot_z(-roll)``, so the world
    vertical is ``R.T @ UP`` (yaw leaves it fixed), and what remains after
    dividing the tilt out is the yaw alone.
    """
    R = np.asarray(R, dtype=float)
    u = R.T @ UP
    roll, pitch = roll_pitch_from_up(u / max(float(np.linalg.norm(u)), 1e-12))
    Y = R @ (rot_x(pitch) @ rot_z(-roll)).T      # == rot_y(-yaw)
    yaw = -float(np.arctan2(Y[0, 2], Y[0, 0]))
    return roll, pitch, yaw


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
