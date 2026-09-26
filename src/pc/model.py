"""The joint camera model: vertical direction and focal length together.

The brief asked for this explicitly -- "Vertikalen + Horizont gemeinsam als
geometrisches Modell behandeln, statt zwei unabhaengige Erkennungen".  The model
is three numbers:

    roll, pitch   the tilt of the world vertical in the camera frame
    f             focal length in pixels

Everything else is derived.  The horizon is *not* searched for separately: it is
the polar line of the vertical vanishing point with respect to the image of the
absolute conic, ``K^-T u``, so once ``(roll, pitch, f)`` are fixed the horizon is
fixed too.  Horizontal lines then serve two purposes only -- they pin down ``f``
(via orthogonality with the vertical direction) and they cross-validate the
result.  That ordering is deliberate: windows, balconies and roof edges throw
off enormous numbers of false horizontal candidates, so they are never allowed
to drive the vertical estimate, only to check it.

One consequence worth knowing: **roll does not depend on f, pitch does.**  Roll
is ``atan2(u_x, -u_y)`` of a direction whose x and y both carry a factor 1/f,
which cancels.  So a wrong focal length tilts the correction but never the
levelling -- which is why a guessed focal length is allowed to level a photo but
gets its pitch correction capped.
"""
from __future__ import annotations

import math

import numpy as np

from . import geometry as G
from . import vanishing as V
from .optimize import nelder_mead


class Model:
    __slots__ = ("roll", "pitch", "yaw", "f", "f_source", "confidence", "vp", "up",
                 "vert_inliers", "horiz_vps", "horizon_support", "diagnostics",
                 "detect_info")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def roll_deg(self):
        return math.degrees(self.roll)

    @property
    def pitch_deg(self):
        return math.degrees(self.pitch)

    @property
    def yaw_deg(self):
        return math.degrees(self.yaw)


# --------------------------------------------------------------------------
# focal length
# --------------------------------------------------------------------------
def focal_px_from_35mm(f35: float, w: int, h: int) -> float:
    """A 35 mm-equivalent focal length is defined on the frame diagonal."""
    return float(f35) * math.hypot(w, h) / math.hypot(36.0, 24.0)


def focal_35mm_from_px(f_px: float, w: int, h: int) -> float:
    return float(f_px) * math.hypot(36.0, 24.0) / math.hypot(w, h)


def _geometric_focal(vp_vert, horiz_hyps, cx, cy, w, h):
    """Focal length from orthogonality with the horizontal vanishing points,
    together with an honest estimate of how much that number is worth.

    ``f**2 = -(v_z - c) . (v_h - c)`` is exact but its *conditioning* collapses
    as the vertical vanishing point recedes: for a nearly upright camera ``v_z``
    is thousands of pixels away, and an angular wobble of half a degree in it --
    well inside the inlier band -- moves the implied focal length by tens of
    percent.  Measured on synthetic scenes with a true 28 mm lens, the raw
    estimate returned 42 mm at 3 deg of tilt and 64 mm at -6 deg, which then
    doubled the pitch correction.  So the sensitivity is measured directly, by
    perturbing ``v_z`` by the inlier threshold and watching the answer move, and
    is returned as a log-space sigma for the caller to weigh against its prior.

    Returns ``(focal_px, sigma_log, quality)``; ``focal_px`` is ``None`` when no
    horizontal vanishing point yields a usable value.
    """
    lo, hi = 0.25 * max(w, h), 6.0 * max(w, h)
    cands, wts = [], []
    for hy in horiz_hyps:
        f = G.focal_from_orthogonal(vp_vert, hy.vp, cx, cy)
        if f is not None and lo <= f <= hi:
            cands.append(f)
            wts.append(hy.score)
    if not cands:
        return None, float("inf"), 0.0

    cands = np.asarray(cands, dtype=float)
    wts = np.asarray(wts, dtype=float)
    order = np.argsort(cands)
    cands, wts = cands[order], wts[order]
    cw = np.cumsum(wts) / wts.sum()
    med = float(cands[int(np.searchsorted(cw, 0.5))])

    # how far the candidates disagree among themselves
    spread = float(np.average(np.abs(np.log(cands / med)), weights=wts))

    # how far the answer moves when v_z is nudged by the inlier band
    sens = _focal_sensitivity(vp_vert, horiz_hyps, cx, cy, med, lo, hi)

    sigma = math.sqrt(spread ** 2 + sens ** 2) + 0.02
    return med, float(sigma), float(np.exp(-sigma / 0.25))


def focal_from_horizon(vp_vert, horiz, cx, cy, w, h, settings, horiz_hyps=None):
    """Focal length from the *position of the horizon*.

    This is the estimator that matters for web JPEGs, which almost never carry a
    focal length.  Once the vertical vanishing point is known the horizon has
    only **one** unknown left: in coordinates centred on the principal point it
    is the polar line ``omega * v_z`` = ``[a_x/f^2, a_y/f^2, 1]`` with
    ``a = v_z - c``, so its normal is parallel to ``a`` -- already known -- and
    only its signed distance ``d = -f^2 / |a|`` is free.  One scalar, and
    ``f = sqrt(-d |a|)`` recovers the focal length from it.

    That one scalar is also better conditioned than the focal length itself.
    ``pitch = atan2(f, |a|)``, so ``pitch ~ sqrt(-d / |a|)``: a 50 % error in the
    horizon position is a 25 % error in the correction angle, where a 50 % error
    in an *assumed* focal length is a 50 % error in the correction.

    The votes come from vanishing points found by sequential RANSAC, not from
    raw pairwise intersections.  Intersecting arbitrary pairs of horizontal
    lines was tried first and is wrong: only lines that are parallel *in the
    world* meet on the horizon, and a facade edge crossing a paving joint meets
    it in the middle of the picture.  On a 28 mm test scene that flooded the
    vote with roughly 7000 meaningless crossings against a few hundred real
    ones and put the answer at 36 mm.  Consensus first, votes second.

    Returns ``(focal_px, sigma_log, support)``.
    """
    if abs(vp_vert[2]) < 1e-12 or len(horiz) < 3:
        return None, float("inf"), 0.0
    a = vp_vert[:2] / vp_vert[2] - np.array([cx, cy])
    na = float(np.linalg.norm(a))
    if na < 1e-6:
        return None, float("inf"), 0.0
    ahat = a / na

    if horiz_hyps is None:
        horiz_hyps = V.search_sequential(horiz, w, h, settings, "horizontal", k=6)
    if not horiz_hyps:
        return None, float("inf"), 0.0

    f_lo, f_hi = 0.25 * max(w, h), 6.0 * max(w, h)
    est = []                                   # (f, sigma_log, weight)
    for hy in horiz_hyps:
        if abs(hy.vp[2]) < 1e-12:
            continue                           # at infinity: carries no focal length
        p = hy.vp[:2] / hy.vp[2] - np.array([cx, cy])
        d = float(p @ ahat)
        if d >= 0:
            continue                           # horizon lies opposite the vertical vp
        f = math.sqrt(-d * na)
        if not (f_lo <= f <= f_hi):
            continue

        # How precisely does this vanishing point locate the horizon?  Its own
        # angular uncertainty sigma_theta becomes a positional uncertainty
        # R * sigma_theta along the horizon normal, where R is its distance from
        # the principal point.  A vanishing point three million pixels away --
        # which is what a facade seen straight on produces -- pins the horizon to
        # no better than a few hundred pixels, and its vote must be discounted
        # accordingly.  Ignoring this is how a 24 mm scene was read as 68 mm.
        sub = horiz.subset(hy.inliers)
        n_eff = max(int(hy.inliers.sum()), 1)
        if n_eff >= 2:
            res = G.angular_residual(hy.vp, sub.mid, sub.dir)
            rms = float(np.sqrt(np.average(res ** 2, weights=np.maximum(sub.weight, 1e-9))))
        else:
            rms = math.radians(settings.inlier_threshold_deg)
        sigma_theta = max(rms, math.radians(0.05)) / math.sqrt(n_eff)
        R = float(np.linalg.norm(p))
        sigma_d = R * sigma_theta
        sigma_log_f = 0.5 * sigma_d / max(abs(d), 1e-6)
        est.append((f, sigma_log_f, hy.score))

    if not est:
        return None, float("inf"), 0.0

    usable = [e for e in est if e[1] < 0.25]
    if len(usable) >= 2:
        # several well conditioned directions: they must also agree with each
        # other, which is the actual cross-check.  A single confidently wrong
        # cluster cannot pass this; two independent facade directions can.
        fs = np.array([e[0] for e in usable])
        wts = np.array([1.0 / max(e[1], 1e-3) ** 2 for e in usable])
        f_hat = float(math.exp(np.average(np.log(fs), weights=wts)))
        spread = float(np.average(np.abs(np.log(fs / f_hat)), weights=wts))
        if spread > 0.25:
            return None, float("inf"), 0.0     # they disagree: nothing is known
        sigma = math.sqrt(1.0 / wts.sum()) + spread + 0.03
        support = float(sum(e[2] for e in usable) / max(sum(e[2] for e in est), 1e-9))
        return f_hat, float(sigma), support

    if len(usable) == 1:
        # One direction fixes one point on the horizon, not the line.  A facade
        # photographed straight on gives exactly this and the focal length is
        # then genuinely not determined by the lines; a wide sigma leaves the
        # prior in charge instead of pretending otherwise.
        return float(usable[0][0]), 0.55, 0.3

    return None, float("inf"), 0.0


def _focal_sensitivity(vp_vert, horiz_hyps, cx, cy, f0, lo, hi, probe_deg=0.5):
    """Log-space spread of the focal estimate under a small rotation of ``v_z``."""
    if f0 <= 0:
        return 1.0
    v = G.normalize_vp(vp_vert)
    # an orthonormal pair spanning the tangent plane at v on the sphere
    tmp = np.array([1.0, 0.0, 0.0]) if abs(v[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = np.cross(v, tmp)
    e1 /= max(np.linalg.norm(e1), 1e-12)
    e2 = np.cross(v, e1)
    d = math.radians(probe_deg)
    out = []
    for pert in (e1, -e1, e2, -e2):
        vp = G.normalize_vp(v * math.cos(d) + pert * math.sin(d))
        vals = [G.focal_from_orthogonal(vp, hy.vp, cx, cy) for hy in horiz_hyps]
        vals = [x for x in vals if x is not None and lo <= x <= hi]
        if vals:
            out.append(float(np.median(vals)))
    if not out:
        return 1.0
    s_z = float(np.max(np.abs(np.log(np.asarray(out) / f0))))
    return max(s_z, _horizontal_sensitivity(vp_vert, horiz_hyps, cx, cy, f0, lo, hi,
                                            probe_deg))


def _horizontal_sensitivity(vp_vert, horiz_hyps, cx, cy, f0, lo, hi, probe_deg=0.5):
    """The same probe, applied to the HORIZONTAL vanishing points.

    Added 2026-09-26: only ``v_z`` was ever wobbled, and on a frontal facade
    the horizontal vanishing point is the ill-conditioned one -- the
    horizontals are nearly parallel, the point sits tens of thousands of
    pixels out, and ``f**2 = -(v_z - c).(v_h - c)`` becomes the product of a
    tiny and a huge number. Measured on the ultra-wide Antibes facade: the
    estimator reported 37 mm at sigma 0.26 (i.e. "measured") for a photograph
    taken at about 12 mm, pushing the pitch to 50 deg. The wobble here is a
    true angular one -- the bearing ``K^-1 v_h`` rotated by ``probe_deg`` -- and
    a wobble that leaves no usable estimate at all makes the estimate
    worthless (infinite sigma, so `_blend_focal` drops it), not a probe to
    skip: losing the answer to half a degree is the strongest possible
    evidence that it was never there. (A first version returned 1.0 there;
    the Antibes facade then still blended to 37 mm on a number that did not
    exist.)
    """
    if f0 <= 0 or not horiz_hyps:
        return 0.0
    K = G.intrinsics(f0, cx, cy)
    Ki = np.linalg.inv(K)
    d = math.radians(probe_deg)
    bases = []
    for hy in horiz_hyps:
        b = Ki @ np.asarray(hy.vp, dtype=float)
        n = np.linalg.norm(b)
        if n < 1e-12:
            continue
        b = b / n
        tmp = np.array([0.0, 1.0, 0.0]) if abs(b[1]) < 0.9 else np.array([1.0, 0.0, 0.0])
        e1 = np.cross(b, tmp)
        e1 /= max(np.linalg.norm(e1), 1e-12)
        bases.append((b, e1, np.cross(b, e1)))
    if not bases:
        return 0.0
    for k in range(4):
        vals = []
        for b, e1, e2 in bases:
            pert = (e1, -e1, e2, -e2)[k]
            vp = K @ (b * math.cos(d) + pert * math.sin(d))
            f = G.focal_from_orthogonal(vp_vert, vp, cx, cy)
            if f is not None and lo <= f <= hi:
                vals.append(f)
        if not vals:
            return float("inf")
    # Only the TOTAL loss is acted on. A graded version (the log spread of
    # the four wobbled answers) was measured the same day and moved
    # well-conditioned photographs for no shown gain: Struktur-Graubeige-2's
    # confidence fell 0.50 -> 0.32 and altbau's focal 117 -> 112 mm, which
    # would silently re-scale the HA results the user calibrated the x-
    # compression against. The loss case is the only one that is certain.
    return 0.0


def _tilt_prior_focal(vp_vert, f_center, sigma_f, cx, cy, sigma_pitch_deg):
    """Resolve the focal/tilt degeneracy with a prior on BOTH, not on f alone.

    Without EXIF, and without a horizontal vanishing point that constrains it,
    one vertical vanishing point fixes only the RATIO of focal length to tilt:
    12 mm tilted 24 deg and 28 mm tilted 50 deg explain the same converging
    lines, and either straightens them. Holding f at the 28 mm default then
    picks whatever tilt that implies -- 50 deg on the ultra-wide Antibes
    facade, past the 30 deg cap, so it is clamped and the verticals stay
    converging (user, 2026-09-26: "geht HA wenn man Objektiv auf 12 mm
    runterzieht, warum schafft es das Programm nicht automatisch?").

    So walk the curve of equally good (f, pitch) pairs and take the most
    plausible point: the focal prior (log-normal around the blended value,
    its own sigma) times a tilt prior (half-normal, ``sigma_pitch_deg``). A
    photograph with a moderate tilt barely moves; one that would need an
    implausible tilt at 28 mm slides toward a wider lens instead.
    """
    if vp_vert is None or abs(vp_vert[2]) < 1e-12 or sigma_pitch_deg <= 0:
        return f_center                 # parallel verticals: no tilt to trade
    sp = math.radians(sigma_pitch_deg)
    best_f, best_cost = f_center, None
    for t in np.linspace(-3.0, 3.0, 241):
        f = f_center * math.exp(t * sigma_f)
        u = G.up_vector(vp_vert, G.intrinsics(f, cx, cy))
        _, pitch = G.roll_pitch_from_up(u)
        cost = 0.5 * t * t + 0.5 * (pitch / sp) ** 2
        if best_cost is None or cost < best_cost:
            best_f, best_cost = f, cost
    return best_f


def _combine(f1, s1, f2, s2):
    """Inverse-variance merge of two independent focal estimates."""
    have1 = f1 is not None and math.isfinite(s1)
    have2 = f2 is not None and math.isfinite(s2)
    if have1 and have2:
        w1, w2 = 1.0 / max(s1, 1e-3) ** 2, 1.0 / max(s2, 1e-3) ** 2
        f = math.exp((w1 * math.log(f1) + w2 * math.log(f2)) / (w1 + w2))
        return f, 1.0 / math.sqrt(w1 + w2)
    if have1:
        return f1, s1
    if have2:
        return f2, s2
    return None, float("inf")


def _blend_focal(f_prior, sigma_prior, f_geo, sigma_geo):
    """Inverse-variance combination in log space.

    Switching hard between "trust EXIF" and "trust geometry" is what produced
    the 64 mm estimate above: a single badly conditioned measurement won
    outright.  Weighting each by its own uncertainty means a sharp measurement
    can still override a vague prior, while a vague measurement barely moves it.
    """
    if f_geo is None or not math.isfinite(sigma_geo):
        return f_prior, "prior", 0.0
    wp = 1.0 / max(sigma_prior, 1e-3) ** 2
    wg = 1.0 / max(sigma_geo, 1e-3) ** 2
    f = math.exp((wp * math.log(f_prior) + wg * math.log(f_geo)) / (wp + wg))
    share = wg / (wp + wg)
    return f, ("geometric" if share > 0.6 else "blended" if share > 0.15 else "prior"), share


# --------------------------------------------------------------------------
# joint refinement
# --------------------------------------------------------------------------
def _cost_terms(params, vert, horiz_vps, horiz_w, cx, cy, f_prior, f_sigma):
    roll, pitch, logf = params
    f = math.exp(logf)
    if not (1e-3 < f < 1e7):
        return 1e12
    K = G.intrinsics(f, cx, cy)
    u = G.up_from_roll_pitch(roll, pitch)

    # vertical lines: their interpretation-plane normals must be perpendicular
    # to the world vertical.  sin(deviation) = |n . u|.
    n = G.plane_normals(vert.lines, K)
    rv = n @ u
    # Cauchy loss -- one mis-classified rafter must not drag the whole fit
    c2 = math.sin(math.radians(2.5)) ** 2
    lv = np.log1p((rv * rv) / c2)
    cost = float(np.sum(vert.weight * lv) / max(vert.weight.sum(), 1e-9))

    if len(horiz_vps):
        b = G.bearings(horiz_vps, K)
        rh = b @ u
        lh = np.log1p((rh * rh) / c2)
        cost += 0.5 * float(np.sum(horiz_w * lh) / max(horiz_w.sum(), 1e-9))

    if f_prior is not None:
        cost += 0.5 * ((math.log(f / f_prior)) / f_sigma) ** 2
    return cost


def estimate(vert, horiz, w: int, h: int, settings, exif_focal_px=None,
             single_facade: bool = False) -> Model:
    """Estimate ``(roll, pitch, f)`` and a confidence for one image.

    ``single_facade`` says the horizontal evidence has been restricted to ONE
    plane -- the ROI strip did it, or the caller knows the frame holds a single
    facade.  That licenses the two-vanishing-point rotation below, which is
    exact for a plane and wrong for a corner seen whole.  See
    `horizontalauto theorie.md`.
    """
    cx, cy = w / 2.0, h / 2.0
    diag = {"n_vertical": len(vert), "n_horizontal": len(horiz)}

    if len(vert) < settings.min_vertical_lines:
        return Model(roll=0.0, pitch=0.0, f=None, f_source="none", confidence=0.0,
                     yaw=0.0, vp=None, up=G.UP.copy(),
                     vert_inliers=np.zeros(len(vert), bool),
                     horiz_vps=[], horizon_support=0.0,
                     diagnostics=dict(diag, reason="too few vertical lines"))

    hyps = V.search(vert, w, h, settings, "vertical")
    par = V.parallel_hypothesis(vert, settings)
    if par is not None:
        hyps.append(par)
    if not hyps:
        return Model(roll=0.0, pitch=0.0, f=None, f_source="none", confidence=0.0,
                     yaw=0.0, vp=None, up=G.UP.copy(),
                     vert_inliers=np.zeros(len(vert), bool),
                     horiz_vps=[], horizon_support=0.0,
                     diagnostics=dict(diag, reason="no vertical vanishing point"))

    horiz_hyps = V.search(horiz, w, h, settings, "horizontal", n_hypotheses=6) if len(horiz) >= 2 else []
    horiz_seq = V.search_sequential(horiz, w, h, settings, "horizontal", k=6) \
        if len(horiz) >= 3 and settings.focal_estimate in ("horizon", "both") else []

    # ---- focal length priors -------------------------------------------
    f_default = focal_px_from_35mm(settings.default_focal_35mm, w, h)
    if settings.focal_35mm > 0:
        f_prior, f_source, f_sigma = focal_px_from_35mm(settings.focal_35mm, w, h), "manual", 0.12
    elif exif_focal_px:
        f_prior, f_source, f_sigma = float(exif_focal_px), "exif", 0.20
    else:
        f_prior, f_source, f_sigma = f_default, "default", 0.60

    # ---- pick the hypothesis the horizon agrees with --------------------
    best = None
    for hy in hyps:
        # Which geometric estimators to consult is a measured decision, not a
        # taste one; see docs/accuracy.md.  On a 40 scene benchmark without
        # EXIF the two-vanishing-point estimator beat the 28 mm prior
        # (mean 2.12 deg vs 2.23 deg, worst 6.7 deg vs 7.2 deg) while the
        # horizon estimator lost badly to it (mean 3.86 deg, worst 16.2 deg),
        # so "vp" is the default and "horizon" has to be asked for.
        mode = settings.focal_estimate
        if mode in ("vp", "both"):
            f_vp, sigma_vp, f_quality = _geometric_focal(hy.vp, horiz_hyps, cx, cy, w, h)
        else:
            f_vp, sigma_vp, f_quality = None, float("inf"), 0.0
        if mode in ("horizon", "both"):
            f_hz, sigma_hz, hz_support = focal_from_horizon(
                hy.vp, horiz, cx, cy, w, h, settings, horiz_seq)
        else:
            f_hz, sigma_hz, hz_support = None, float("inf"), 0.0
        # the horizon estimate is the primary one; the two-vanishing-point value
        # is a second opinion, and both are weighed against the prior by their
        # own uncertainty rather than by a hand-set precedence order
        f_geo, sigma_geo = _combine(f_hz, sigma_hz, f_vp, sigma_vp)
        if f_source == "manual":
            f_use, src, share = f_prior, "manual", 0.0
        else:
            f_use, kind, share = _blend_focal(f_prior, f_sigma, f_geo, sigma_geo)
            src = f_source if kind == "prior" else (
                "horizon" if (f_hz is not None and sigma_hz <= sigma_vp) else kind)
        support = _horizon_support(hy.vp, f_use, cx, cy, horiz_hyps, horiz)
        total = hy.score * (0.55 + 0.45 * support)
        if best is None or total > best[0]:
            best = (total, hy, f_use, src, support,
                    f_quality * (0.5 + 0.5 * share), sigma_geo)

    _, hy, f_use, f_src, support, f_quality, sigma_geo = best
    diag["focal_blend"] = {"prior_35mm": round(focal_35mm_from_px(f_prior, w, h), 1),
                           "blended_35mm": round(focal_35mm_from_px(f_use, w, h), 1),
                           "sigma_geo": (round(sigma_geo, 3) if math.isfinite(sigma_geo)
                                         else None),
                           "source": f_src}
    # Nothing but the 28 mm default (plus at most a vague geometric hint)
    # knows the focal length here, so let a tilt prior share the decision --
    # see `_tilt_prior_focal`. EXIF and a hand-set focal are knowledge and are
    # left alone.
    if f_source == "default" and getattr(settings, "tilt_prior_deg", 0) > 0:
        s_use = (f_sigma if not math.isfinite(sigma_geo)
                 else 1.0 / math.sqrt(1.0 / f_sigma ** 2 + 1.0 / max(sigma_geo, 1e-3) ** 2))
        f_tp = _tilt_prior_focal(hy.vp, f_use, s_use, cx, cy, settings.tilt_prior_deg)
        if abs(math.log(f_tp / f_use)) > 0.02:
            diag["focal_blend"]["tilt_prior_35mm"] = round(focal_35mm_from_px(f_tp, w, h), 1)
            f_use, f_src = f_tp, "tilt prior"
    u = G.up_vector(hy.vp, G.intrinsics(f_use, cx, cy))
    roll, pitch = G.roll_pitch_from_up(u)

    # ---- joint (roll, pitch, f) refinement ------------------------------
    inl_set = vert.subset(hy.inliers) if hy.inliers.any() else vert
    hv = np.array([x.vp for x in horiz_hyps]) if horiz_hyps else np.zeros((0, 3))
    hw = np.array([x.score for x in horiz_hyps]) if horiz_hyps else np.zeros(0)
    # Refine the focal length only when the geometry actually constrains it.
    #
    # A horizontal vanishing point near infinity says "this direction is
    # perpendicular to up" -- a statement about roll, carrying no focal length
    # information at all, since K^-1 v is independent of f when v[2] == 0.  Left
    # in a three parameter fit it still moves the cost, because growing f tilts
    # `up` out of the image plane and shrinks every residual by the unit-norm
    # normalisation.  The optimiser duly ran the focal length up: a true 28 mm
    # scene at 3 deg of tilt came back as 42.5 mm, tripling the pitch
    # correction.  So when `sigma_geo` says the measurement is vague, f is held
    # fixed at the blended value and only (roll, pitch) are fitted.
    # Focal lower bound for the refinement: never allow f below 5% of frame.
    # Without this, Nelder-Mead can drive f to unphysical values (e.g. 29px on
    # a 1320px frame) where K·R·K⁻¹ produces wrong proportions instead of
    # correcting them.  The bound is set BEFORE the optimiser so it never
    # explores the fisheye regime.
    f_floor = math.log(0.05 * max(w, h))
    fit_focal = settings.refine and math.isfinite(sigma_geo) and sigma_geo < 0.35
    if settings.refine and len(inl_set) >= 2:
        if fit_focal:
            def fn(p):
                pf = max(p[2], f_floor)  # clamp inside the cost function
                return _cost_terms([p[0], p[1], pf], inl_set, hv, hw, cx, cy, f_prior, f_sigma)
            x0 = np.array([roll, pitch, math.log(f_use)])
            step = [math.radians(0.6), math.radians(0.6), 0.05]
        else:
            lf = math.log(f_use)
            fn = lambda p: _cost_terms([p[0], p[1], lf], inl_set, hv, hw, cx, cy,
                                       f_prior, f_sigma)
            x0 = np.array([roll, pitch])
            step = [math.radians(0.6), math.radians(0.6)]
        x, _, _ = nelder_mead(fn, x0, step, max_iter=250)
        moved_f = abs(x[2] - math.log(f_use)) if fit_focal else 0.0
        if abs(x[0] - roll) < math.radians(6) and abs(x[1] - pitch) < math.radians(10) \
                and moved_f < 0.35:
            roll, pitch = float(x[0]), float(x[1])
            if fit_focal:
                f_use = max(float(math.exp(x[2])), 0.05 * max(w, h))
                f_src = "refined"
        u = G.up_from_roll_pitch(roll, pitch)
    # Kept for the stability term below: that test refits roll/pitch on halves
    # of the VERTICAL inliers, so it has to be judged against the angles the
    # vertical evidence produced -- not against angles the facade rotation has
    # since adjusted. Comparing the two measures a difference of METHOD and
    # reports it as noise.
    roll_v, pitch_v = roll, pitch

    # ---- horizontal (yaw) de-convergence ------------------------------
    # The third camera angle.  It cannot be read off the vertical vanishing
    # point -- a yaw is a rotation about the world vertical and leaves it
    # fixed -- so it comes from the dominant horizontal one instead: level the
    # frame with (roll, pitch), and the angle that remaining horizontal
    # direction makes with the image x-axis is the yaw.  Gated on support,
    # because a single weak horizontal cluster is evidence about one window
    # row, not about the camera; and folded to [-90, 90], because a line has
    # no direction and the two antipodal readings differ by a half-turn.
    yaw = 0.0
    if settings.correct_horizontal:
        dom = horiz_hyps[0] if horiz_hyps else None
        if dom is not None and dom.support >= settings.min_horizontal_support:
            if single_facade and hy.vp is not None:
                # One plane, two directions: build the rotation from the facade's
                # OWN axes instead of composing three separately estimated
                # angles.  Its horizontals and verticals then land on the image
                # axes by construction rather than by agreement -- measured on
                # Platte_1.jpg, 0.15 degrees out of square against 6.12 for the
                # composed version.  The angles are read back out afterwards
                # because every consumer downstream still speaks in three.
                f_orth = G.focal_from_orthogonal(hy.vp, dom.vp, cx, cy)
                if f_orth:
                    # The window and the rating it earns have to agree. This
                    # accepted anything inside a factor of TWO and then rated it
                    # 0.85 -- claiming the focal is well determined in exactly
                    # the case where the two independent estimates contradict
                    # each other by 80%. A closed-form constraint is only
                    # CONFIRMED evidence while it lands near what the vertical
                    # evidence found independently; outside that the premise
                    # (the two vanishing points are perpendicular) is the thing
                    # in doubt, and the honest move is to keep the old value
                    # and say so.
                    if 0.75 * f_use <= f_orth <= 1.33 * f_use:
                        f_use, f_src = f_orth, "orthogonal"
                    else:
                        diag["ortho_focal_rejected"] = round(f_orth, 1)
                Rf = G.rotation_from_two_vps(dom.vp, hy.vp,
                                             G.intrinsics(f_use, cx, cy))
                roll, pitch, yaw = G.roll_pitch_yaw_from_rotation(Rf)
                u = G.up_from_roll_pitch(roll, pitch)
                diag["facade_rotation"] = "two vanishing points, one plane"
            else:
                b = G.bearings(np.array([dom.vp]), G.intrinsics(f_use, cx, cy))[0]
                wv = G.rot_x(pitch) @ G.rot_z(-roll) @ b
                yaw = math.atan2(-wv[2], wv[0])
                yaw = (yaw + math.pi / 2.0) % math.pi - math.pi / 2.0
        elif dom is not None:
            # the feature is on but the evidence is too weak to trust: record
            # why yaw reads zero instead of leaving it silent
            diag["yaw_skipped"] = (f"horizontal VP support {dom.support:.2f} "
                                   f"< gate {settings.min_horizontal_support:.2f}")
        else:
            diag["yaw_skipped"] = "no horizontal VP found"

    # Sanity clamp: a focal length below ~5% of the frame width is unphysical
    # (it would imply an extreme fisheye).  The geometric estimator can produce
    # such values when the VP is near the image centre and the line support is
    # weak.  Fall back to the prior so K·R·K⁻¹ stays well-conditioned.
    # CRITICAL: roll/pitch must be RECOMPUTED after the clamp, because they
    # were derived from the (wrong) focal via up_vector(K(f)).  With f=29px
    # the up-vector points in a completely different direction than with
    # f=600px, so the angles are garbage.
    f_min = 0.05 * max(w, h)
    if f_use < f_min:
        diag["focal_clamped"] = f"estimated {f_use:.1f}px < minimum {f_min:.0f}px; using prior {f_prior:.0f}px"
        f_use = f_prior
        f_src = "clamped"
        # Recompute roll/pitch with the corrected focal
        u = G.up_vector(hy.vp, G.intrinsics(f_use, cx, cy))
        roll, pitch = G.roll_pitch_from_up(u)

    conf, cdiag = _confidence(vert, hy, support, f_src, f_quality, roll_v, pitch_v,
                              f_use, cx, cy, w, h, settings, hv, hw)
    diag.update(cdiag)
    diag["hypotheses"] = len(hyps)
    diag["horizontal_vps"] = len(horiz_hyps)
    diag["horiz_supports"] = [x.support for x in horiz_hyps[:4]]
    diag["yaw_deg"] = round(math.degrees(yaw), 3)

    return Model(roll=roll, pitch=pitch, yaw=yaw, f=f_use, f_source=f_src,
                 confidence=conf,
                 vp=G.normalize_vp(G.intrinsics(f_use, cx, cy) @ u), up=u,
                 vert_inliers=hy.inliers, horiz_vps=[x.vp for x in horiz_hyps],
                 horizon_support=support, diagnostics=diag)


def _horizon_support(vp_vert, f, cx, cy, horiz_hyps, horiz):
    """How much of the horizontal line energy sits on the implied horizon.

    This is the cross-check.  If the vertical estimate is right, the vanishing
    points of the horizontal structure must land on the horizon it implies.  If
    they do not, the vertical estimate is describing something that is not a
    vertical, and the image should be left alone.
    """
    if not horiz_hyps:
        return 0.0
    K = G.intrinsics(f, cx, cy)
    u = G.up_vector(vp_vert, K)
    b = G.bearings(np.array([x.vp for x in horiz_hyps]), K)
    dev = np.abs(b @ u)                       # sin of the angle off the horizon
    w = np.array([x.score for x in horiz_hyps], dtype=float)
    good = np.exp(-(dev / math.sin(math.radians(3.0))) ** 2)
    return float(np.sum(w * good) / max(w.sum(), 1e-9))


def _confidence(vert, hy, support, f_src, f_quality, roll, pitch, f, cx, cy,
                w, h, settings, hv, hw):
    """A 0..1 belief that correcting this image is the right thing to do.

    Deliberately multiplicative: every factor can veto.  The cost of a false
    negative is an unchanged photo; the cost of a false positive is a ruined
    one.
    """
    d = {}
    inl = hy.inliers
    n_inl = int(inl.sum())
    d["inlier_lines"] = n_inl

    # 1. how much of the vertical evidence agrees
    share = float(vert.weight[inl].sum() / max(vert.weight.sum(), 1e-9)) if n_inl else 0.0
    c_share = min(1.0, share / 0.55)
    d["inlier_share"] = round(share, 3)

    # 2. enough independent lines at all
    c_count = min(1.0, (n_inl - 1) / 6.0) if n_inl >= 2 else 0.0

    # 3. the inliers must be spread across the frame -- a vanishing point voted
    #    for by one window frame in one corner is not evidence about the camera
    if n_inl >= 2:
        xs = vert.mid[inl, 0]
        spread = float(xs.max() - xs.min()) / max(w, 1)
        c_spread = min(1.0, spread / 0.45)
    else:
        c_spread = 0.0
    d["x_spread"] = round(c_spread, 3)

    # 4. horizon agreement
    c_horizon = 0.45 + 0.55 * support
    d["horizon_support"] = round(support, 3)

    # 5. how much we trust the focal length (pitch scales with it, roll does not)
    c_focal = {"exif": 1.0, "manual": 1.0, "horizon": 0.85 + 0.15 * f_quality,
               "geometric": 0.80 + 0.20 * f_quality,
               "blended": 0.75 + 0.20 * f_quality, "refined": 0.72,
               # A focal from f^2 = -(v1-c).(v2-c), accepted only while it
               # agrees with the independent estimate -- see estimate(). It was
               # falling through to the 0.60 "unknown source" default, which is
               # simply the wrong rating for a closed-form constraint that two
               # measurements confirm. It is a RATING, not a gate release: the
               # min_confidence veto in pipeline.process stands (see f9659fc --
               # the unattended path is where nobody is watching), and in the
               # review panel would_skip already exempts a hand-placed strip,
               # so this moves a displayed number there and not a decision.
               "orthogonal": 0.85,
               "default": 0.60, "none": 0.40}.get(f_src, 0.6)
    d["focal_source"] = f_src

    # 6. stability: refit on random halves of the inliers and see whether the
    #    answer moves.  A fit that swings by a degree between subsets is not a
    #    measurement, it is a coin toss.
    c_stab, stab_deg = _stability(vert, inl, f, cx, cy, roll, pitch, settings)
    d["stability_deg"] = round(stab_deg, 3)

    conf = c_share * c_count * c_spread * c_horizon * c_focal * c_stab
    terms = dict(share=c_share, count=c_count, spread=c_spread, horizon=c_horizon,
                 focal=c_focal, stability=c_stab)
    d["confidence_terms"] = {k: round(v, 3) for k, v in terms.items()}
    # Name the factor that did the vetoing.  With a multiplicative confidence a
    # single weak term decides the outcome, and "conf=0.20" on its own tells the
    # user nothing about which dial to turn.
    weakest = min(terms.items(), key=lambda kv: kv[1])
    d["weakest_term"] = f"{weakest[0]} {weakest[1]:.2f}"
    return float(np.clip(conf, 0.0, 1.0)), d


def _stability(vert, inl, f, cx, cy, roll, pitch, settings, n_draws=6):
    idx = np.flatnonzero(inl)
    if len(idx) < 4:
        return 0.5, float("nan")
    rng = np.random.default_rng(settings.seed + 1)
    K = G.intrinsics(f, cx, cy)
    angles = []
    for _ in range(n_draws):
        pick = rng.choice(idx, size=max(2, len(idx) // 2), replace=False)
        sub = vert.subset(np.isin(np.arange(len(vert)), pick))
        vp = G.refine_vp(sub.lines, sub.mid, sub.weight,
                         G.normalize_vp(K @ G.up_from_roll_pitch(roll, pitch)))
        r, p = G.roll_pitch_from_up(G.up_vector(vp, K))
        angles.append((math.degrees(r), math.degrees(p)))
    a = np.asarray(angles)
    spread = float(np.hypot(a[:, 0].std(), a[:, 1].std()))
    return float(np.clip(math.exp(-spread / 0.8), 0.15, 1.0)), spread
