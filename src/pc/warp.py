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


YAW_FORESHORTEN = 0.5
"""Exponent of the horizontal compression after a yaw correction.

User-directed 2026-09-26, asked for many times: "Verkürzung auf 70% bei
altbau", AFTER the full HA correction, not instead of it. A facade seen at
yaw θ is foreshortened to cos θ of its width; the rotation undoes that and
stretches it by 1/cos θ -- geometrically true, visually far too wide on a
steep corner view. The result is compressed in x by ``cos(θ) ** k``:

    k = 0    true frontal proportions (the full 1/cos θ stretch)
    k = 0.5  halfway, on a log scale, between the photograph and frontal
    k = 1    the photograph's own width at the centre

At k = 0.5 altbau.jpeg (θ = 60°) gets sqrt(0.5) = 0.707 -- the 70% asked for
-- while 30° keeps 93%, 10° keeps 99.6% and a straight photograph is
untouched. A pure x-scale keeps every horizontal horizontal and every
vertical vertical, so straightness is exactly what the rotation delivered.

NOT the tangent damping that was built and removed the same day: that
shrank the yaw itself and left the lines visibly slanted.
"""


def yaw_x_scale(yaw: float, k: float = YAW_FORESHORTEN) -> float:
    """Horizontal compression factor that follows a yaw correction."""
    c = abs(math.cos(yaw))
    return c ** k if c > 1e-6 else 1.0


def build(w: int, h: int, f: float, roll: float, pitch: float,
          yaw: float = 0.0, max_area: float = 4.0) -> np.ndarray:
    """Build the correction homography: K·R·K⁻¹, then the yaw x-compression.

    The rotation undoes the camera's rotation relative to the scene; it alone
    decides what comes out straight.  With a yaw in play it is followed by a
    pure horizontal scale about the image centre (`yaw_x_scale`), which
    changes proportions only -- see `YAW_FORESHORTEN` for why and how much.
    Without a yaw the scale is 1 and this is the plain rotation, bit for bit.
    """
    K = G.intrinsics(f, w / 2.0, h / 2.0)
    R = G.correction_rotation(roll, pitch, yaw)
    H = G.homography(K, R)
    s = yaw_x_scale(yaw)
    if s != 1.0:
        cx = w / 2.0
        S = np.array([[s, 0.0, cx * (1.0 - s)],
                      [0.0, 1.0, 0.0],
                      [0.0, 0.0, 1.0]])
        H = S @ H
    return H


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



def _in_strip(line_segs, strip, img_w):
    """The segments whose midpoint falls inside the facade strip.

    Returns *line_segs* untouched when there is no strip, or when too few
    survive to describe a facade -- four is the floor `_whole_frame` already
    works to, and a box around three lines is not a facade.
    """
    if strip is None or line_segs is None or len(line_segs) < 4:
        return line_segs
    x0, x1 = float(strip[0]) * img_w, float(strip[1]) * img_w
    mid = (line_segs[:, 0] + line_segs[:, 2]) / 2.0
    keep = (mid >= min(x0, x1)) & (mid <= max(x0, x1))
    return line_segs[keep] if int(keep.sum()) >= 4 else line_segs


STRIP_CROP_MARGIN = 0.20
"""Margin left/right of the facade strip when it bounds an auto-crop.

Distinct from ``settings.reframe_margin`` (0.30 by default) -- that is a
different, general line-motif margin ``_whole_frame`` applies for a different
reason (how much surrounding scene to keep around a detected facade box, not a
hard user-drawn boundary). This is the number Ledger A.4 measured 2026-09-20:
strip+20% beat strip+30% by 8x. Pinned to that finding, not the configurable
dial, on purpose -- widening it would quietly re-import the worse ratio.
"""


def _strip_x_range_warped(H, strip, img_w, img_h):
    """The facade strip's x-extent, warped into the same space as ``quad``.

    ``strip`` is ORIGINAL-image-space fractions of width; ``quad`` (and the
    rectangles ``inscribed_rect``/``max_inscribed_rect`` return) live in
    WARPED/output space, which under a real yaw is not a scaled copy of the
    original x-axis -- a large yaw can inflate the canvas to several times the
    source width on one side and compress it on the other. Warping the strip's
    four corners through ``H`` and taking the x-extent of the result is the
    only way to compare the two honestly.
    """
    x0, x1 = float(min(strip)) * img_w, float(max(strip)) * img_w
    corners = np.array([[x0, 0.0], [x0, float(img_h)],
                        [x1, 0.0], [x1, float(img_h)]])
    warped = G.apply_h(H, corners)
    return float(warped[:, 0].min()), float(warped[:, 0].max())


def _strip_band(H, quad, strip, img_w, img_h):
    """The strip plus :data:`STRIP_CROP_MARGIN`, warped and clipped to ``quad``.

    Returns ``(x0, x1)`` in warped space, or ``None`` when the strip is
    degenerate or the band collapses. The clip mirrors ``_whole_frame``'s own
    rule for its facade-box margin: the margin only ever eats into picture
    that exists, never into the fill zones a warp opened up.
    """
    wx0, wx1 = _strip_x_range_warped(H, strip, img_w, img_h)
    if wx1 <= wx0:
        return None
    margin = STRIP_CROP_MARGIN * (wx1 - wx0)
    x0 = max(wx0 - margin, float(quad[:, 0].min()))
    x1 = min(wx1 + margin, float(quad[:, 0].max()))
    return (x0, x1) if x1 > x0 else None


def _clip_quad_to_x_band(quad, x0, x1):
    """Sutherland-Hodgman clip of the convex ``quad`` to ``x0 <= x <= x1``.

    The result stays convex (an intersection of convex sets), so it is a
    legal input to ``_inside``/``inscribed_rect``/``max_inscribed_rect``
    unchanged -- clipping to a vertical band can only add at most two
    vertices to a quadrilateral.
    """
    def clip(pts, keep, bound):
        if len(pts) == 0:
            return pts
        out, n = [], len(pts)
        for i in range(n):
            cur, nxt = pts[i], pts[(i + 1) % n]
            ci, ni = keep(cur[0], bound), keep(nxt[0], bound)
            if ci:
                out.append(cur)
            if ci != ni:
                t = (bound - cur[0]) / (nxt[0] - cur[0])
                out.append(np.array([bound, cur[1] + t * (nxt[1] - cur[1])]))
        return np.array(out)
    poly = clip(np.asarray(quad, dtype=float), lambda x, b: x >= b, x0)
    return clip(poly, lambda x, b: x <= b, x1)


def _strip_bounded_rect(quad, H, strip, img_w, img_h, aspect):
    """The largest ``aspect`` rectangle inside the strip band, or ``None``.

    Deliberately :func:`max_inscribed_rect`, not :func:`inscribed_rect`: the
    latter anchors on the original frame's mapped centre, which on a corner
    view sits outside the user's chosen strip more often than not (that is
    the whole reason strips exist) and the anchored search then degenerates
    to a zero-area rectangle at that boundary. Once a strip has been placed,
    the strip -- not the original centre -- is the composition intent.
    """
    band = _strip_band(H, quad, strip, img_w, img_h)
    if band is None:
        return None
    clipped = _clip_quad_to_x_band(quad, band[0], band[1])
    if len(clipped) < 3:
        return None
    rect = max_inscribed_rect(clipped, aspect)
    if rect is None:
        return None
    rw, rh = rect[2] - rect[0], rect[3] - rect[1]
    return rect if rw >= 8 and rh >= 8 else None


def _whole_frame(H, quad, img_w, img_h, settings, area_ratio,
                 line_segs: np.ndarray | None = None, strip=None):
    """The full warped quad on a canvas big enough to hold it.

    Nothing of the photograph is discarded; the corners the rotation opens up
    are filled by ``apply``.  When line segments are available, the output is
    cropped to the **facade bounding box** (warped line endpoints) plus a
    margin — this trims the Telea fill / garbage-pixel zones that a large-yaw
    warp opens up at the edges without shrinking the facade itself.

    KEY: The facade keeps its source pixel count.  K·R·K⁻¹ at large yaw
    inflates the facade (500px → 1200px); we scale the output back so the
    facade is ~the same size as in the source.  The output canvas is then
    sized to fit the scaled facade + margin, never smaller than source.

    ``strip`` (already gated by the caller to "a strip was placed and a real
    yaw fired") narrows the result to :data:`STRIP_CROP_MARGIN` around the
    facade the user pointed at.  This is the path that actually matters for a
    real horizontal-auto correction: ``crop="auto"`` falls back to here at
    essentially every yaw large enough for a strip to matter, because the
    coverage loss blows through ``crop_max_loss`` long before that.
    """
    x0, y0 = quad.min(axis=0)
    x1, y1 = quad.max(axis=0)
    ow, oh = int(round(x1 - x0)), int(round(y1 - y0))
    if ow < 8 or oh < 8:
        return None
    band = _strip_band(H, quad, strip, img_w, img_h) if strip is not None else None

    # If we have line segments, crop to the facade bounding box + margin.
    # The front edge (nearest vertical building edge) keeps its source pixel
    # height — no downscale.  The output grows to fit the warped facade; it
    # never shrinks below the source dimensions.
    if line_segs is not None and len(line_segs) >= 4:
        pts = np.column_stack([line_segs[:, 0], line_segs[:, 1],
                               line_segs[:, 2], line_segs[:, 3]]).reshape(-1, 2)

        # Warp to get facade position in output space
        warped_pts = G.apply_h(H, pts)
        qx0, qy0 = quad[:, 0].min(), quad[:, 1].min()
        qx1, qy1 = quad[:, 0].max(), quad[:, 1].max()
        # A TOLERANCE, not a margin: it decides which warped points count as
        # part of the facade, and widening it only lets more far-flung points
        # in. Raising this to 0.20 was my misreading of "facade + 20 %"; the
        # margin is applied to the finished box below, where it belongs.
        pad = 0.05 * max(qx1 - qx0, qy1 - qy0)
        inside = ((warped_pts[:, 0] >= qx0 - pad) & (warped_pts[:, 0] <= qx1 + pad) &
                  (warped_pts[:, 1] >= qy0 - pad) & (warped_pts[:, 1] <= qy1 + pad))
        if inside.sum() >= 4:
            wp = warped_pts[inside]
            fx0, fy0 = wp[:, 0].min(), wp[:, 1].min()
            fx1, fy1 = wp[:, 0].max(), wp[:, 1].max()

            # A magnification floor was tried here and removed: measured on
            # Platte_1 the facade runs 0.05 to 0.39 source pixels per output
            # pixel end to end, so no threshold separates "smear" from
            # "subject" -- the whole warp is interpolation and a cut-off that
            # bit at all would have eaten the building.
            # The whole facade plus 20 %, and only where there is picture to
            # take it from (user: "wenn vorhanden"). The quad is the outline of
            # the real picture, so the margin stops at it; the facade box
            # itself is never shrunk by the clip.
            mx, my = 0.20 * (fx1 - fx0), 0.20 * (fy1 - fy0)
            gx0, gy0 = max(fx0 - mx, quad[:, 0].min()), max(fy0 - my, quad[:, 1].min())
            gx1, gy1 = min(fx1 + mx, quad[:, 0].max()), min(fy1 + my, quad[:, 1].max())
            fx0, fy0 = min(fx0, gx0), min(fy0, gy0)
            fx1, fy1 = max(fx1, gx1), max(fy1, gy1)
            fw, fh = fx1 - fx0, fy1 - fy0

            # No downscale: the warped facade keeps its full pixel count.
            # s_scale=1.0 means "what the warp produced is what you get."
            # The output grows (never shrinks) to fit the inflated quad.
            s_scale = 1.0

            # Margin: 20% of facade size on each side for context
            margin_frac = getattr(settings, "reframe_margin", 0.20)
            mx, my = fw * margin_frac, fh * margin_frac

            # Crop region in warped space
            cx0 = max(x0, fx0 - mx)
            cy0 = max(y0, fy0 - my)
            cx1 = min(x1, fx1 + mx)
            cy1 = min(y1, fy1 + my)
            # A facade strip narrows the x-range further: the user pointed at
            # ONE facade, and the box above was built from every line in
            # `line_segs` regardless of which facade it belongs to.
            if band is not None:
                cx0, cx1 = max(cx0, band[0]), min(cx1, band[1])
            ow_c, oh_c = int(round(cx1 - cx0)), int(round(cy1 - cy0))

            # Never smaller than source (max quality: no reduction) --
            # EXCEPT on the axis a strip band just restricted: the user asked
            # for that facade specifically, and re-widening back to img_w
            # would quietly re-admit the region the strip was meant to
            # exclude (measured: the band is narrower than img_w in most
            # corner-view/yaw combinations, so this is the common case once
            # a strip is in play, not an edge case).
            if band is None:
                ow_c = max(ow_c, img_w)
            oh_c = max(oh_c, img_h)

            # Transform: translate to crop origin only (no scale-back)
            T = np.array([[1, 0, -cx0], [0, 1, -cy0], [0, 0, 1]], dtype=float)
            H_out = T @ H

            if settings.keep_size:
                s2 = min(img_w / float(ow_c), img_h / float(oh_c))
                S2 = np.array([[s2, 0, 0], [0, s2, 0], [0, 0, 1]], dtype=float)
                return S2 @ H_out, img_w, img_h, 1.0, area_ratio
            return H_out, ow_c, oh_c, 1.0, area_ratio

    # Fallback: no line segments -- use the full quad, or the strip band when
    # one is active. This is the branch that matters most in practice: a real
    # horizontal-auto yaw blows through `crop_max_loss` and lands here even
    # without any detected lines to build a facade box from, and until now it
    # had no way to know which facade the user meant.
    if band is not None:
        bx0, bx1 = band
        ow_b = int(round(bx1 - bx0))
        if ow_b >= 8:
            T = np.array([[1, 0, -bx0], [0, 1, -y0], [0, 0, 1]], dtype=float)
            if settings.keep_size:
                s = min(img_w / float(ow_b), img_h / float(oh))
                S = np.array([[s, 0, 0], [0, s, 0], [0, 0, 1]], dtype=float)
                return S @ T @ H, img_w, img_h, 1.0, area_ratio
            return T @ H, max(ow_b, 1), max(oh, 1), 1.0, area_ratio
    T = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]], dtype=float)
    if settings.keep_size:
        s = min(img_w / float(ow), img_h / float(oh))
        S = np.array([[s, 0, 0], [0, s, 0], [0, 0, 1]], dtype=float)
        return S @ T @ H, img_w, img_h, 1.0, area_ratio
    return T @ H, max(ow, 1), max(oh, 1), 1.0, area_ratio


NEAR_EDGE_MAX_SCALE = 3.0


def _wf_keep_near_edge(wf, img_w, img_h, settings, yaw):
    """Apply the no-downsampling floor to a ``_whole_frame`` plan."""
    if wf is None:
        return None
    H_total, ow, oh, coverage, area_ratio = wf
    H_total, ow, oh = _keep_near_edge(H_total, ow, oh, img_w, img_h, settings, yaw)
    return H_total, ow, oh, coverage, area_ratio


def min_magnification(H_total: np.ndarray, img_w: int, img_h: int,
                      out_w: int, out_h: int, n: int = 9) -> float:
    """Smallest local linear magnification (output px per source px) that the
    warp applies to any part of the source still visible in the output.

    Below 1.0 means that part of the photograph is being **downsampled** -- real
    detail thrown away -- and it is invisible in the output size, because a
    canvas can grow while one edge is still being squeezed.  Measured
    2026-09-20 with horizontal auto on: the canvas grew to 2.3-7x the source
    area and the near edge was still sampled at 0.71-0.95.

    Only points that actually land inside the output canvas count; a corner the
    crop discards must not dictate the scale.
    """
    xs = np.linspace(0.02, 0.98, n) * img_w
    ys = np.linspace(0.02, 0.98, n) * img_h
    gx, gy = np.meshgrid(xs, ys)
    base = np.stack([gx.ravel(), gy.ravel()], axis=1)
    d = max(1.0, min(img_w, img_h) / 200.0)
    dx = base + np.array([d, 0.0])
    dy = base + np.array([0.0, d])
    q = cv2.perspectiveTransform(base.reshape(-1, 1, 2), H_total).reshape(-1, 2)
    qx = cv2.perspectiveTransform(dx.reshape(-1, 1, 2), H_total).reshape(-1, 2)
    qy = cv2.perspectiveTransform(dy.reshape(-1, 1, 2), H_total).reshape(-1, 2)
    inside = ((q[:, 0] >= 0) & (q[:, 0] <= out_w) &
              (q[:, 1] >= 0) & (q[:, 1] <= out_h))
    if not inside.any():
        return 1.0
    sx = np.linalg.norm(qx - q, axis=1) / d
    sy = np.linalg.norm(qy - q, axis=1) / d
    return float(np.minimum(sx, sy)[inside].min())


def _keep_near_edge(H_total, out_w, out_h, img_w, img_h, settings, yaw):
    """Scale the plan up until nothing is sampled below 1:1.

    ``H = K R K^-1`` at a large yaw inflates the receding edge five- to
    sevenfold, and ``_whole_frame`` then scales the whole output back so the
    facade keeps *about* its source pixel count.  On average that is right; at
    the **near** edge it is a loss, because the average is dragged up by the
    edge that was inflated.  The near edge is where the real resolution is.

    So: measure the worst magnification still visible, and scale up by its
    reciprocal so the worst becomes exactly 1.0.  Bounded by
    ``max_area_ratio`` -- a photograph that would need more than that keeps as
    much as the bound allows rather than producing a canvas nobody asked for.

    Only applies when a yaw is being corrected; with yaw 0 the roll/pitch warp
    does not have this asymmetry and every output size in the suite would move.
    """
    if abs(yaw) < 1e-9 or not getattr(settings, "preserve_near_edge", True):
        return H_total, out_w, out_h
    worst = min_magnification(H_total, img_w, img_h, out_w, out_h)
    if worst >= 0.999:
        return H_total, out_w, out_h
    # HOW MANY PIXELS TO KEEP (user, 2026-09-20).  Growing the canvas is normal
    # when a facade is foreshortened; how far to grow it is a taste, so it is a
    # dial rather than a rule:
    #
    #   keep_pixels = 1.0  maximum pixels -- scale up until nothing is sampled
    #                      below 1:1.  Biggest file, no photographed detail
    #                      discarded.
    #   keep_pixels = 0.0  minimum pixels -- no scaling at all.  Smallest file,
    #                      the near edge loses resolution.
    #
    # Interpolated geometrically, because these are scale factors: the halfway
    # point between 1x and 1.4x is 1.18x, not 1.20x.
    k = float(getattr(settings, "keep_pixels", 0.5))
    k = min(1.0, max(0.0, k))
    full = 1.0 / worst
    scale_wanted = full ** k
    if scale_wanted <= 1.0 + 1e-9:
        return H_total, out_w, out_h

    # `max_area_ratio` deliberately does NOT bound this.  Its own comment says
    # what it is for -- "crop canvas to this x source area (trim fill zones)" --
    # which is about how much invented border to carry, not about how finely the
    # photograph is sampled.  Letting it clamp the floor left two assets at 0.84
    # and 0.98 when the whole point is 1.0.  The floor gets its own bound
    # instead: it may enlarge the planned canvas by at most `NEAR_EDGE_MAX_SCALE`
    # linearly, which is generous for every yaw the caps allow and still stops a
    # degenerate plan from asking for a canvas nobody can hold.
    s = min(scale_wanted, NEAR_EDGE_MAX_SCALE)
    if s <= 1.0 + 1e-9:
        return H_total, out_w, out_h
    S = np.array([[s, 0, 0], [0, s, 0], [0, 0, 1]], dtype=float)
    return S @ H_total, max(1, int(round(out_w * s))), max(1, int(round(out_h * s)))


def _cap_canvas(result, img_w: int, img_h: int, settings):
    """Scale a finished plan down when the canvas runs past ``max_area_ratio``.

    config.py describes that setting as "crop canvas to this x source area",
    and until now it bounded only the near-edge upscale -- a different
    quantity. The canvas itself had no ceiling: squaring the left facade of
    Platte_1.jpg (1320x742) produced 6985x2170, FIFTEEN times the source area.
    The same warp on a 24 MP original asks for roughly 360 megapixels, which is
    not a large file but a failed allocation.

    Scaled, not cropped. The framing was decided above by code that went to
    some trouble to keep the facade; throwing part of it away here would undo
    that. A photograph that needs more canvas than the bound allows gets the
    whole picture at lower magnification, which is the answer a person would
    give.
    """
    if result is None:
        return None
    cap = float(getattr(settings, "max_area_ratio", 0.0) or 0.0)
    if cap <= 0:
        return result
    H_total, ow, oh = result[0], int(result[1]), int(result[2])
    limit = cap * float(img_w) * float(img_h)
    area = float(ow) * float(oh)
    if area <= limit or area <= 0:
        return result
    s = math.sqrt(limit / area)
    S = np.array([[s, 0.0, 0.0], [0.0, s, 0.0], [0.0, 0.0, 1.0]], dtype=float)
    return ((S @ H_total, max(1, int(round(ow * s))), max(1, int(round(oh * s))))
            + tuple(result[3:]))


def plan(img_w: int, img_h: int, H: np.ndarray, settings,
         line_segs: np.ndarray | None = None, yaw: float = 0.0,
         strip=None):
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

    ``strip`` is the facade strip as fractions of the width. On a corner view
    it is the only thing that knows WHICH facade was meant: the strip restricts
    the horizontals, but the verticals still span both faces, so a bounding box
    over all the lines frames the whole building and the crop keeps the face
    nobody chose. Given a strip, the box is taken over the lines inside it.
    """
    quad = warped_quad(H, img_w, img_h)
    area_ratio = quad_area(quad) / float(img_w * img_h)
    # A strip only means anything once a real rotation has actually separated
    # the facades; at yaw==0 (no horizontal-auto, no h-marker, no manual yaw)
    # the frame is still fronto-parallel and the ordinary centred crop already
    # frames it correctly, so an inactive strip changes nothing.
    strip_active = strip if (strip is not None and abs(yaw) > 1e-9) else None

    if settings.crop == "none":
        wf = _whole_frame(H, quad, img_w, img_h, settings, area_ratio,
                          _in_strip(line_segs, strip, img_w), strip=strip_active)
        return _cap_canvas(_wf_keep_near_edge(wf, img_w, img_h, settings, yaw),
                           img_w, img_h, settings)

    centre = G.apply_h(H, np.array([[img_w / 2.0, img_h / 2.0]]))[0]
    aspect = (img_w / img_h) if settings.crop in ("aspect", "auto") else None
    if settings.crop == "inside" and aspect is None:
        aspect = img_w / img_h
    rect = None
    if strip_active is not None:
        # The strip picked one facade; the ordinary centred inscribed_rect
        # anchors on the WHOLE frame's mapped centre, which on a corner view
        # sits outside that facade more often than not, so bound the crop to
        # the strip instead (Ledger A.4, measured 2026-09-20: strip+20%).
        rect = _strip_bounded_rect(quad, H, strip_active, img_w, img_h, aspect)
    if rect is None:
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
        wf = _whole_frame(H, quad, img_w, img_h, settings, area_ratio,
                          _in_strip(line_segs, strip, img_w), strip=strip_active)
        return _cap_canvas(_wf_keep_near_edge(wf, img_w, img_h, settings, yaw),
                           img_w, img_h, settings)

    if settings.keep_size:
        s = min(img_w / rw, img_h / rh)
        ow, oh = img_w, img_h
    else:
        s = 1.0
        ow, oh = int(round(rw)), int(round(rh))
    S = np.array([[s, 0, -rect[0] * s], [0, s, -rect[1] * s], [0, 0, 1]], dtype=float)
    Ht, ow, oh = _keep_near_edge(S @ H, max(ow, 1), max(oh, 1), img_w, img_h,
                                 settings, yaw)
    return _cap_canvas((Ht, ow, oh, float(coverage), area_ratio),
                       img_w, img_h, settings)


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
