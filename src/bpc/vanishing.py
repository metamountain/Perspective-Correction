"""Vanishing point search.

Differences from the naive RANSAC in chsasank/Image-Rectification, all of which
matter on real building photos:

* residuals are angular and computed via :func:`geometry.bearing_to_vp`, so a
  vanishing point at infinity scores correctly instead of scoring zero;
* the minimal sample is rejected unless the two lines are far enough apart in
  angle, which stops nearly-parallel pairs from voting a wildly leveraged point
  into first place;
* candidates are constrained by an orientation and a distance prior, so the
  "vertical" search cannot return the roof direction;
* several distinct hypotheses survive instead of one, and the choice between
  them is deferred until the horizon can be used to arbitrate;
* the RNG is seeded, so two runs on one file give the same file out.
"""
from __future__ import annotations

import math

import numpy as np

from . import geometry as G


class Hypothesis:
    __slots__ = ("vp", "inliers", "score", "support")

    def __init__(self, vp, inliers, score, support):
        self.vp = vp
        self.inliers = inliers
        self.score = float(score)
        self.support = float(support)

    def __repr__(self):
        return f"Hypothesis(vp={np.round(self.vp, 4)}, score={self.score:.1f})"


def _plausible_vertical(vp, cx, cy, min_dist, max_lean_deg):
    """Reject vanishing points that cannot be the vertical one.

    Two independent gates.  *Direction*: seen from the principal point the
    vertical vanishing point must lie within ``max_lean_deg`` of the image's
    vertical axis -- more lean than that is a rolled camera beyond anything
    worth auto-correcting, or a misdetection.  *Distance*: it must be far
    outside the frame.  A vanishing point inside the picture means the verticals
    converge within the visible area, which no photograph of a building does;
    darktable rejects that case outright too.
    """
    if abs(vp[2]) < 1e-9:                      # at infinity: only direction applies
        dx, dy = vp[0], vp[1]
    else:
        dx = vp[0] / vp[2] - cx
        dy = vp[1] / vp[2] - cy
        if math.hypot(dx, dy) < min_dist:
            return False
    lean = abs(math.atan2(abs(dx), abs(dy)))
    return lean <= math.radians(max_lean_deg)


def _plausible_horizontal(vp, cx, cy, min_dist):
    if abs(vp[2]) < 1e-9:
        dx, dy = vp[0], vp[1]
    else:
        dx = vp[0] / vp[2] - cx
        dy = vp[1] / vp[2] - cy
        if math.hypot(dx, dy) < min_dist:
            return False
    return abs(math.atan2(abs(dy), abs(dx))) <= math.radians(45.0)


def _normalize_rows(v):
    """Row-wise :func:`geometry.normalize_vp`.

    Same contract, including the sign convention that makes equal vanishing
    points compare equal -- without it the non-maximum suppression below would
    treat ``v`` and ``-v`` as different hypotheses.
    """
    norm = np.linalg.norm(v, axis=1)
    bad = norm < 1e-12
    norm[bad] = 1.0
    out = v / norm[:, None]
    out[bad] = (0.0, 1.0, 0.0)
    lead = np.abs(out).argmax(axis=1)
    flip = out[np.arange(len(out)), lead] < 0
    out[flip] *= -1.0
    return out


def _offsets(vps, cx, cy):
    """Row-wise offset from the principal point, finite and infinite alike.

    Returns ``(dx, dy, at_infinity)``; for a point at infinity the offset is the
    direction itself, which is what the orientation prior needs and what a
    dehomogenising version would turn into a division by zero.
    """
    at_inf = np.abs(vps[:, 2]) < 1e-9
    w = np.where(at_inf, 1.0, vps[:, 2])
    dx = np.where(at_inf, vps[:, 0], vps[:, 0] / w - cx)
    dy = np.where(at_inf, vps[:, 1], vps[:, 1] / w - cy)
    return dx, dy, at_inf


def _plausible_vertical_rows(vps, cx, cy, min_dist, max_lean_deg):
    dx, dy, at_inf = _offsets(vps, cx, cy)
    far = at_inf | (np.hypot(dx, dy) >= min_dist)
    lean = np.abs(np.arctan2(np.abs(dx), np.abs(dy)))
    return far & (lean <= math.radians(max_lean_deg))


def _plausible_horizontal_rows(vps, cx, cy, min_dist):
    dx, dy, at_inf = _offsets(vps, cx, cy)
    far = at_inf | (np.hypot(dx, dy) >= min_dist)
    return far & (np.abs(np.arctan2(np.abs(dy), np.abs(dx))) <= math.radians(45.0))


def _score(ls, vp, thr_rad):
    res = G.angular_residual(vp, ls.mid, ls.dir)
    inl = res <= thr_rad
    if not inl.any():
        return inl, 0.0
    # soft score inside the band: a line at half the threshold counts more than
    # one just scraping in, which stabilises the choice between near ties
    soft = 1.0 - (res[inl] / thr_rad) ** 2
    return inl, float(np.sum(ls.weight[inl] * (0.35 + 0.65 * soft)))


def search(ls, w, h, settings, orientation="vertical", n_hypotheses=None):
    """RANSAC over a line set.  Returns hypotheses, best first."""
    n_hypotheses = settings.n_hypotheses if n_hypotheses is None else n_hypotheses
    if len(ls) < 2:
        return []
    cx, cy = w / 2.0, h / 2.0
    min_dist = settings.min_vp_distance_frac * max(w, h)
    thr = math.radians(settings.inlier_threshold_deg)
    rng = np.random.default_rng(settings.seed)

    if orientation == "vertical":
        ok_rows = lambda v: _plausible_vertical_rows(
            v, cx, cy, min_dist, settings.vertical_window_deg + 8.0)
        axis_angle = ls.angle_to_vert
    else:
        ok_rows = lambda v: _plausible_horizontal_rows(v, cx, cy, min_dist * 0.25)
        axis_angle = math.pi / 2.0 - ls.angle_to_vert

    n = len(ls)
    p = ls.weight / ls.weight.sum() if ls.weight.sum() > 0 else np.full(n, 1.0 / n)
    # the minimal sample needs two lines that actually intersect at a
    # well-conditioned point; require a minimum angular separation
    min_sep = math.radians(0.6)
    iters = settings.ransac_iters

    # Draw every minimal sample and build every candidate in one pass.
    # `rng.choice` with a probability vector rebuilds a cumulative distribution
    # on each call, and `np.cross` carries heavy per-call overhead on 3-vectors;
    # measured over 800 iterations on 390 lines the two together were 37 ms of a
    # 62 ms loop, and both all but vanish when batched.  Scoring, the remaining
    # 25 ms, is the algorithm itself and stays a loop.
    cdf = np.cumsum(p)
    cdf[-1] = 1.0
    idx = np.clip(np.searchsorted(cdf, rng.random((iters, 2))), 0, n - 1)
    i, j = idx[:, 0], idx[:, 1]
    keep = (i != j) & (np.abs(axis_angle[i] - axis_angle[j]) >= min_sep)
    i, j = i[keep], j[keep]
    if len(i) == 0:
        return []

    a, b = ls.lines[i], ls.lines[j]
    vps = _normalize_rows(np.column_stack([
        a[:, 1] * b[:, 2] - a[:, 2] * b[:, 1],
        a[:, 2] * b[:, 0] - a[:, 0] * b[:, 2],
        a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]]))
    vps = vps[np.all(np.isfinite(vps), axis=1)]
    if len(vps):
        vps = vps[ok_rows(vps)]

    found = []
    for vp in vps:
        inl, sc = _score(ls, vp, thr)
        if sc <= 0:
            continue
        found.append((sc, vp, inl))

    if not found:
        return []

    found.sort(key=lambda t: -t[0])
    out = []
    for sc, vp, inl in found:
        if len(out) >= n_hypotheses:
            break
        # non-maximum suppression: hypotheses are the same one if they agree
        # on the sphere to within a few degrees
        if any(abs(float(vp @ h_.vp)) > math.cos(math.radians(2.0)) for h_ in out):
            continue
        sub = ls.subset(inl)
        vp_ref = G.refine_vp(sub.lines, sub.mid, sub.weight, vp)
        if not ok_rows(vp_ref[None, :])[0]:
            vp_ref = vp
        inl2, sc2 = _score(ls, vp_ref, thr)
        total = float(ls.weight.sum()) or 1.0
        out.append(Hypothesis(vp_ref, inl2, sc2, float(ls.weight[inl2].sum()) / total))
    out.sort(key=lambda x: -x.score)
    return out


def parallel_hypothesis(ls, settings):
    """The "already upright" hypothesis: verticals meet at infinity.

    Worth testing explicitly.  RANSAC samples pairs of lines, and for a photo
    that is already straight every pair gives a slightly different, very distant
    point; none of them is the exact answer, and the winner is whichever noise
    realisation happened to gather the most votes.  Evaluating the exact
    parallel model as a candidate lets a straight photo be recognised as
    straight instead of being nudged by a fraction of a degree.
    """
    if len(ls) == 0:
        return None
    thr = math.radians(settings.inlier_threshold_deg)
    # dominant direction of the vertical pool, as a point at infinity
    d = ls.dir * np.sign(ls.dir[:, 1])[:, None]
    mean = (d * ls.weight[:, None]).sum(axis=0)
    if np.linalg.norm(mean) < 1e-9:
        return None
    mean /= np.linalg.norm(mean)
    vp = G.normalize_vp(np.array([mean[0], mean[1], 0.0]))
    inl, sc = _score(ls, vp, thr)
    total = float(ls.weight.sum()) or 1.0
    return Hypothesis(vp, inl, sc, float(ls.weight[inl].sum()) / total)


def search_sequential(ls, w, h, settings, orientation="horizontal", k=6,
                      min_share=0.04):
    """Find several *genuinely different* vanishing points, not near duplicates.

    Plain RANSAC over one line set returns the same dominant point again and
    again; non-maximum suppression then leaves one answer where a building may
    honestly have three or four horizontal directions (two facades, the paving,
    a wall running off at an angle).  Removing the inliers before searching
    again -- sequential RANSAC -- surfaces the weaker directions, and it is
    those that pin down the horizon: one direction fixes a point on the horizon,
    several fix the line.

    Returns hypotheses whose ``inliers`` masks index into ``ls``.
    """
    remaining = np.ones(len(ls), dtype=bool)
    total = float(ls.weight.sum()) or 1.0
    out = []
    for _ in range(k):
        if remaining.sum() < 3:
            break
        idx = np.flatnonzero(remaining)
        sub = ls.subset(remaining)
        got = search(sub, w, h, settings, orientation, n_hypotheses=1)
        if not got:
            break
        hy = got[0]
        share = float(sub.weight[hy.inliers].sum()) / total
        if share < min_share or not hy.inliers.any():
            break
        full = np.zeros(len(ls), dtype=bool)
        full[idx[hy.inliers]] = True
        out.append(Hypothesis(hy.vp, full, hy.score, share))
        remaining[idx[hy.inliers]] = False
    return out
