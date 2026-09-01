"""Line segment detection, merging, classification and weighting.

The detector chain is LSD -> FastLineDetector -> Canny+HoughLinesP.  LSD is the
accurate one and is what darktable's ashift uses; the other two only exist
because OpenCV dropped and re-added LSD several times and because contrib
builds are not always present.  Whichever runs, everything downstream sees the
same ``(N, 4)`` endpoint array.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from . import geometry as G
from . import masks as MK


# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------
def _lsd(gray: np.ndarray):
    try:
        det = cv2.createLineSegmentDetector(cv2.LSD_REFINE_ADV, _scale=0.8,
                                            _sigma_scale=0.6, _quant=2.0,
                                            _ang_th=22.5, _density_th=0.7,
                                            _n_bins=1024)
    except Exception:
        try:
            det = cv2.createLineSegmentDetector()
        except Exception:
            return None
    try:
        out = det.detect(gray)
    except Exception:
        return None
    seg = out[0] if isinstance(out, tuple) else out
    if seg is None or len(seg) == 0:
        return None
    return np.asarray(seg, dtype=float).reshape(-1, 4), "lsd"


def _fld(gray: np.ndarray):
    try:
        det = cv2.ximgproc.createFastLineDetector()
    except Exception:
        return None
    try:
        seg = det.detect(gray)
    except Exception:
        return None
    if seg is None or len(seg) == 0:
        return None
    return np.asarray(seg, dtype=float).reshape(-1, 4), "fld"


def _hough(gray: np.ndarray, min_len: float):
    v = float(np.median(gray))
    lo = int(max(0, 0.66 * v))
    hi = int(min(255, 1.33 * v))
    edges = cv2.Canny(gray, lo, hi, L2gradient=True)
    seg = cv2.HoughLinesP(edges, 1, np.pi / 720.0, threshold=int(max(30, min_len * 0.6)),
                          minLineLength=float(min_len), maxLineGap=float(max(3.0, min_len * 0.25)))
    if seg is None or len(seg) == 0:
        return None
    return np.asarray(seg, dtype=float).reshape(-1, 4), "hough"


def _mlsd(bgr, settings):
    from . import mlsd as M
    seg = M.detect(bgr, getattr(settings, "mlsd_model", ""),
                   getattr(settings, "mlsd_score_thr", 0.10),
                   getattr(settings, "mlsd_dist_thr", 20.0))
    if seg is None or len(seg) == 0:
        return None
    return np.asarray(seg, dtype=float).reshape(-1, 4), "mlsd"


def gate_by(seg: np.ndarray, guide: np.ndarray, angle_tol_deg: float = 6.0,
            dist_tol: float = 8.0, extent_margin: float = 0.15) -> np.ndarray:
    """Keep only segments that lie along one of the ``guide`` segments.

    The point of combining two detectors.  M-LSD is trained on wireframes and
    knows which edges are *structural*, but it decodes endpoints from a 256x256
    displacement map, so its geometry is coarse.  LSD has sub-pixel endpoints
    but no idea what a building is and happily returns every plank seam and
    twig.  Using M-LSD's segments purely as a spatial gate over LSD's keeps the
    judgement of the first and the precision of the second.
    """
    if len(seg) == 0 or len(guide) == 0:
        return seg
    atol = math.radians(angle_tol_deg)
    ang_s = np.arctan2(seg[:, 3] - seg[:, 1], seg[:, 2] - seg[:, 0]) % math.pi
    ang_g = np.arctan2(guide[:, 3] - guide[:, 1], guide[:, 2] - guide[:, 0]) % math.pi
    mid = G.segment_midpoints(seg)
    gl = G.lines_from_segments(guide)                 # normalised: |a,b| == 1
    g0 = guide[:, :2]
    gd = np.column_stack([guide[:, 2] - guide[:, 0], guide[:, 3] - guide[:, 1]])
    glen = np.linalg.norm(gd, axis=1)
    glen[glen < 1e-9] = 1e-9
    gd = gd / glen[:, None]

    da = np.abs(ang_s[:, None] - ang_g[None, :])
    da = np.minimum(da, math.pi - da)
    perp = np.abs(mid @ gl[:, :2].T + gl[:, 2][None, :])
    t = (mid[:, None, :] - g0[None, :, :]) * gd[None, :, :]
    t = t.sum(axis=2)
    margin = extent_margin * glen[None, :]
    within = (t >= -margin) & (t <= glen[None, :] + margin)
    ok = ((da <= atol) & (perp <= dist_tol) & within).any(axis=1)
    return seg[ok]


def detect_segments(gray: np.ndarray, min_len: float, detector: str = "auto",
                    bgr=None, settings=None):
    """Return ``(segments, detector_name)``; segments may be empty."""
    if detector in ("hybrid", "union"):
        if bgr is None:
            bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        guide = _mlsd(bgr, settings)
        base = _lsd(gray)
        if base is None:
            return (guide[0], "mlsd") if guide else (np.zeros((0, 4)), "none")
        if guide is None:
            return base[0], "lsd"
        if detector == "union":
            return np.vstack([base[0], guide[0]]), "union"
        gated = gate_by(base[0], guide[0],
                        dist_tol=getattr(settings, "hybrid_dist_tol", 8.0))
        # never gate the evidence away entirely
        if len(gated) < max(8, getattr(settings, "min_vertical_lines", 4) * 2):
            return base[0], "lsd(hybrid fallback)"
        return gated, "hybrid"
    if detector == "mlsd":
        if bgr is None:
            bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        got = _mlsd(bgr, settings)
        if got is not None:
            return got
        return np.zeros((0, 4)), "mlsd"
    chain = {"auto": (_lsd, _fld), "lsd": (_lsd,), "fld": (_fld,), "hough": ()}[detector]
    for fn in chain:
        got = fn(gray)
        if got is not None:
            return got
    got = _hough(gray, min_len)
    if got is not None:
        return got
    return np.zeros((0, 4)), "none"


# --------------------------------------------------------------------------
# cleaning
# --------------------------------------------------------------------------
def drop_border_segments(seg: np.ndarray, w: int, h: int, margin: float) -> np.ndarray:
    """Discard segments that hug the image frame.

    The frame itself, vignetting boundaries and sensor edges produce perfectly
    straight, perfectly vertical lines that carry no information about the
    scene but win on length, so they would dominate a length-weighted fit.
    darktable drops these too.
    """
    if len(seg) == 0 or margin <= 0:
        return seg
    mid = G.segment_midpoints(seg)
    near_x = (mid[:, 0] < margin) | (mid[:, 0] > w - margin)
    near_y = (mid[:, 1] < margin) | (mid[:, 1] > h - margin)
    span_x = np.abs(seg[:, 2] - seg[:, 0])
    span_y = np.abs(seg[:, 3] - seg[:, 1])
    return seg[~((near_x & (span_x < margin * 2)) | (near_y & (span_y < margin * 2)))]


def merge_collinear(seg: np.ndarray, angle_tol_deg: float = 2.0,
                    offset_tol: float = 3.0, gap_tol: float = 24.0) -> np.ndarray:
    """Join segments that are the same physical edge broken into pieces.

    LSD readily splits a six storey facade corner into eight fragments at every
    balcony.  Un-merged, that edge is eight short lines whose individual
    directions are noisy; merged, it is one long, precisely oriented line.
    Since the fit is length-weighted this materially changes the answer.
    """
    if len(seg) < 2:
        return seg
    ang = np.arctan2(seg[:, 3] - seg[:, 1], seg[:, 2] - seg[:, 0]) % math.pi
    length = G.segment_lengths(seg)
    order = np.argsort(-length)
    used = np.zeros(len(seg), dtype=bool)
    out = []
    atol = math.radians(angle_tol_deg)
    for i in order:
        if used[i]:
            continue
        used[i] = True
        ai = ang[i]
        d = np.array([math.cos(ai), math.sin(ai)])
        n = np.array([-d[1], d[0]])
        p0 = seg[i, :2]
        ti = (seg[i].reshape(2, 2) - p0) @ d
        lo, hi = float(ti.min()), float(ti.max())
        members = [i]
        # Grow the run rather than testing every fragment against the seed.
        # A facade corner broken at each of six balconies is a *chain*: piece
        # three is 75 px from the seed but only 15 px from piece two, so a
        # seed-only test merges two of the six and leaves four short, noisily
        # oriented lines behind -- which then out-vote the one long precise one.
        grew = True
        while grew:
            grew = False
            for j in order:
                if used[j]:
                    continue
                da = abs(ang[j] - ai)
                da = min(da, math.pi - da)
                if da > atol:
                    continue
                q = seg[j].reshape(2, 2) - p0
                if np.max(np.abs(q @ n)) > offset_tol:
                    continue
                tj = q @ d
                gap = max(float(tj.min()) - hi, lo - float(tj.max()))
                if gap > gap_tol:
                    continue
                used[j] = True
                members.append(j)
                lo, hi = min(lo, float(tj.min())), max(hi, float(tj.max()))
                grew = True
        a = p0 + d * lo
        b = p0 + d * hi
        out.append([a[0], a[1], b[0], b[1]])
    return np.asarray(out, dtype=float)


# --------------------------------------------------------------------------
# classification / weighting
# --------------------------------------------------------------------------
class LineSet:
    """Segments plus everything derived from them, computed once."""

    __slots__ = ("seg", "lines", "mid", "dir", "length", "weight", "angle_to_vert")

    def __init__(self, seg: np.ndarray):
        self.seg = seg
        self.lines = G.lines_from_segments(seg) if len(seg) else np.zeros((0, 3))
        self.mid = G.segment_midpoints(seg) if len(seg) else np.zeros((0, 2))
        self.dir = G.segment_directions(seg) if len(seg) else np.zeros((0, 2))
        self.length = G.segment_lengths(seg) if len(seg) else np.zeros(0)
        self.weight = self.length.copy()
        # angle to the image vertical axis, in [0, pi/2]
        if len(seg):
            a = np.abs(np.arctan2(self.dir[:, 0], self.dir[:, 1]))
            self.angle_to_vert = np.minimum(a, math.pi - a)
        else:
            self.angle_to_vert = np.zeros(0)

    def __len__(self):
        return len(self.seg)

    def subset(self, mask: np.ndarray) -> "LineSet":
        out = LineSet.__new__(LineSet)
        out.seg = self.seg[mask]
        out.lines = self.lines[mask]
        out.mid = self.mid[mask]
        out.dir = self.dir[mask]
        out.length = self.length[mask]
        out.weight = self.weight[mask]
        out.angle_to_vert = self.angle_to_vert[mask]
        return out


def angular_prior(angle: np.ndarray, window: float, softness: float = 0.35) -> np.ndarray:
    """Down-weight lines the further they lean away from the expected axis.

    A hard angular window alone throws away a 31 deg facade edge and fully
    trusts a 29 deg roof rafter.  A smooth prior inside the window makes the fit
    prefer the lines that were probably vertical in the world.

    ``softness`` does nearly all the work, and the hard window almost none.
    Measured on half-timbered scenes -- posts, rails and a great many diagonal
    braces -- narrowing the window from 32 deg to 18 deg changed the worst pitch
    error by 0.04 deg, while sharpening the prior from 0.6 to 0.35 cut it from
    3.24 deg to 1.36 deg, with no cost on plain facades.  The dangerous line in
    half-timbered work is not the steep 45 deg brace, which falls outside any
    window; it is the shallow 20 deg one, which sits deep inside it and can only
    be handled by weighting.  See docs/accuracy.md.
    """
    x = np.clip(angle / max(window, 1e-6), 0.0, 1.0)
    return np.exp(-(x * x) / (2.0 * softness * softness))


def split_by_orientation(ls: LineSet, vertical_window_deg: float,
                         horizontal_window_deg: float, softness: float = 0.35):
    """Return ``(vertical, horizontal)`` line sets with weights already applied."""
    if len(ls) == 0:
        return ls, ls
    vw = math.radians(vertical_window_deg)
    hw = math.radians(horizontal_window_deg)
    a_v = ls.angle_to_vert
    a_h = math.pi / 2.0 - a_v
    vmask = a_v <= vw
    hmask = a_h <= hw
    vert = ls.subset(vmask)
    horiz = ls.subset(hmask)
    # Two factors: how long the segment is, and how well its direction fits the
    # pool.  The mask is deliberately *not* a third one -- it either removes a
    # line or it does not, and that decision is taken in prepare() on the
    # endpoints.  A mask that also nudged every surviving line's weight would
    # make the segmenter a soft influence on the whole fit rather than a
    # judgement about which lines belong to the building.
    if len(vert):
        vert.weight = vert.length * angular_prior(vert.angle_to_vert, vw, softness)
    if len(horiz):
        horiz.weight = horiz.length * angular_prior(math.pi / 2.0 - horiz.angle_to_vert,
                                                    hw, softness)
    return vert, horiz


def _evidence_lost(before: np.ndarray, after: np.ndarray) -> float:
    """Fraction of straight-line *length* a mask removed.

    Length, not segment count: masking a hundred short twigs and masking one
    facade edge are not the same event, and only the second one matters.  This
    is the number ``masks.credible`` judges on, reported so a user can see how
    close a mask came to that limit instead of only learning when it trips.
    """
    if len(before) == 0:
        return 0.0
    w0 = float(G.segment_lengths(before).sum())
    if w0 <= 0:
        return 0.0
    w1 = float(G.segment_lengths(after).sum()) if len(after) else 0.0
    return max(0.0, 1.0 - w1 / w0)


def prepare(gray: np.ndarray, settings, bgr=None, image_path: str = "") -> tuple:
    """Full front end: detect, clean, merge, classify.  Returns
    ``(all_lines, vertical, horizontal, detector_name)``."""
    h, w = gray.shape[:2]
    min_len = max(8.0, settings.min_line_length_frac * min(w, h))
    seg, name = detect_segments(gray, min_len, settings.detector, bgr, settings)
    if len(seg):
        seg = seg[G.segment_lengths(seg) >= min_len]
    if len(seg):
        seg = drop_border_segments(seg, w, h, settings.border_margin_px)
    mask, masked_out, mask_note = None, np.zeros((0, 4)), ""
    mask_share, evidence_lost, mask_refused = 0.0, 0.0, False
    if len(seg) and bgr is not None and getattr(settings, "mask_mode", "off") != "off":
        # detection happens before masking, not after: a mask producer may want
        # the lines (the old SAM route scored its regions by them), and the
        # credibility guard needs the unmasked evidence to compare against.
        mask, mask_note = MK.build(bgr, settings, image_path, seg)
        if mask is not None:
            # a long straight line is architecture, whatever the texture
            # statistics think; protect it before anything is down-weighted
            mask = MK.protect_structure(mask, seg, min_len * 1.8)
        if mask is not None:
            kept = MK.drop_by_endpoints(seg, mask)
            ok, why = MK.credible(seg, kept)
            mask_share = float(mask.mean())
            evidence_lost = _evidence_lost(seg, kept)
            if not ok:
                mask, kept, mask_note, mask_refused = None, seg, why, True
                evidence_lost = 0.0
            # keep what was removed, so the preview can show it.  A mask the
            # user cannot see is a mask the user cannot trust: when the
            # segmenter removes the wrong half of a building, the only symptom
            # otherwise is a quietly worse answer.
            kept_set = {tuple(r) for r in kept.tolist()}
            masked_out = np.array([r for r in seg.tolist()
                                   if tuple(r) not in kept_set], dtype=float)
            if len(masked_out) == 0:
                masked_out = np.zeros((0, 4))
            seg = kept
    if len(seg) and settings.merge_lines:
        seg = merge_collinear(seg, gap_tol=max(12.0, 0.02 * max(w, h)))
        seg = seg[G.segment_lengths(seg) >= min_len]
    ls = LineSet(seg if len(seg) else np.zeros((0, 4)))
    vert, horiz = split_by_orientation(ls, settings.vertical_window_deg,
                                       settings.horizontal_window_deg,
                                       settings.angular_softness)
    info = {"mask": mask, "masked_out": masked_out, "mask_note": mask_note,
            "mask_share": mask_share, "evidence_lost": evidence_lost,
            "mask_refused": mask_refused}
    if settings.merge_horizontal and len(horiz):
        # Merge the horizontal pool only.  The two pools have opposite needs: a
        # facade offers many unoccluded verticals, and merging them destroys
        # independent measurements, but its few horizontals are routinely broken
        # in half by a tree, and a vanishing point is located by the baseline of
        # the lines that vote for it.
        hseg = merge_collinear(horiz.seg, angle_tol_deg=1.5, offset_tol=3.0,
                               gap_tol=max(30.0, 0.12 * max(w, h)))
        hseg = hseg[G.segment_lengths(hseg) >= min_len]
        if len(hseg):
            merged = LineSet(hseg)
            _, horiz = split_by_orientation(merged, settings.vertical_window_deg,
                                            settings.horizontal_window_deg,
                                            settings.angular_softness)
    return ls, vert, horiz, name, info
