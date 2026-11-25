"""Debug overlays: what the detector saw and what it decided."""
from __future__ import annotations

import cv2
import numpy as np

from . import geometry as G
from .lineoverlay import LINE, infinite_line_points

# Aliases kept for the callers that read them by name (e.g.
# `scheme.ArchitectureScheme.draw_preview`).  The values live in
# `lineoverlay.LINE`, the one table for every line overlay: the before pane's
# detection colours and the after pane's check-line colours, read by both
# renderers from one place instead of each keeping its own set.
GREEN = LINE["v_inlier"]
MAGENTA = LINE["horizon"]
GREY = LINE["struck"]
VP_MARK = LINE["vp_mark"]


def draw_lines(canvas, seg, colour, thickness=2):
    """Draw ``seg`` (N, 4) on ``canvas``.

    Public, and the underscore is gone on purpose: `review.py` has been
    calling this across the module boundary for a long time, and an underscore
    that two modules ignore is not privacy, it is a note that went unread. The
    name now says what is true -- this is the shared primitive both renderers
    of "what the estimator saw" are built on.
    """
    for x0, y0, x1, y1 in seg:
        cv2.line(canvas, (int(round(x0)), int(round(y0))), (int(round(x1)), int(round(y1))),
                 colour, thickness, cv2.LINE_AA)


def draw_detected(canvas, inv, dropped, horiz, vert_out, vert_in):
    """The detected lines on a BGR canvas: what the mask removed, the
    horizontals, and the verticals split into the two classes -- inliers
    thicker than the rest, so the fit's evidence reads as the fit's evidence.

    Segments are in their own pixel space and ``inv`` maps them onto the
    canvas (1.0 when they are already canvas-sized).  One block for every
    baked rendering: `overlay` and `review.render_before` each used to carry
    a copy of it.
    """
    thin = max(1, int(round(inv)))
    if dropped is not None and len(dropped):
        draw_lines(canvas, dropped * inv, LINE["masked"], thin)
    if horiz is not None and len(horiz):
        draw_lines(canvas, horiz * inv, LINE["h_line"], thin)
    if vert_out is not None and len(vert_out):
        draw_lines(canvas, vert_out * inv, LINE["v_outlier"], thin)
    if vert_in is not None and len(vert_in):
        draw_lines(canvas, vert_in * inv, LINE["v_inlier"], max(2, int(round(inv * 2))))


def _draw_infinite_line(canvas, line, colour, thickness=2):
    h, w = canvas.shape[:2]
    pts = infinite_line_points(line, w, h)
    if len(pts) >= 2:
        cv2.line(canvas, (int(pts[0][0]), int(pts[0][1])), (int(pts[1][0]), int(pts[1][1])),
                 colour, thickness, cv2.LINE_AA)


def tint_mask(canvas, mask, colour=(60, 60, 200), alpha=0.28):
    """Wash the masked region so it is obvious what was excluded."""
    if mask is None:
        return canvas
    # The mask is painted at analysis resolution (long edge `detect_max_edge`,
    # 1600 by default) while the canvas is the full photograph, so this resize
    # is an upscale of several times.  It used to be INTER_NEAREST, which has no
    # sub-pixel blending: every round edge `cv2.circle` painted arrived here as a
    # staircase of right angles, and the brush read as rectangular on screen even
    # though nothing anywhere draws a rectangle.  Linear gives a soft edge, and
    # since this is only a wash -- nothing downstream tests it -- a fractional
    # edge is exactly what is wanted.
    m = mask.astype(np.uint8) * 255
    if m.shape[:2] != canvas.shape[:2]:
        m = cv2.resize(m, (canvas.shape[1], canvas.shape[0]),
                       interpolation=cv2.INTER_LINEAR)
    sel = m > 0
    if not sel.any():
        return canvas
    # Blend per pixel by the resized coverage, not by a hard in/out test, so the
    # soft edge survives into the picture instead of being rounded back to a
    # step.  Only the touched pixels are converted to float: the canvas can be
    # several thousand pixels on a side and a full-frame float copy is not free.
    a = (m[sel].astype(np.float32) / 255.0 * alpha)[:, None]
    canvas[sel] = (canvas[sel] * (1.0 - a)
                   + np.asarray(colour, np.float32) * a).astype(np.uint8)
    return canvas


def overlay(bgr, vert, horiz, model, scale=1.0, result_text="", info=None):
    """Annotated copy of the input showing the evidence behind the decision."""
    canvas = bgr.copy()
    inv = 1.0 / scale if scale else 1.0
    info = info or {}
    tint_mask(canvas, info.get("mask"))
    # No model: every vertical is drawn as a rejected candidate, the same as
    # before this block moved into `draw_detected`.
    inl = model.vert_inliers if model is not None and model.vert_inliers is not None \
        else np.zeros(len(vert), bool)
    if len(inl) == len(vert):
        v_out, v_in = vert.seg[~inl], vert.seg[inl]
    else:
        v_out, v_in = np.empty((0, 4)), vert.seg
    draw_detected(canvas, inv, info.get("masked_out"), horiz.seg, v_out, v_in)

    if model is not None and model.f:
        h, w = canvas.shape[:2]
        # `scale` rescales the *segments*, which are in analysis-resolution
        # coordinates.  The focal length is not: the pipeline already converts it
        # back to full-resolution pixels.  Scaling it again here pushed the
        # horizon off the canvas entirely on any image large enough to be
        # downscaled for analysis, which is every real photograph.
        K = G.intrinsics(model.f, w / 2.0, h / 2.0)
        _draw_infinite_line(canvas, G.horizon_line(model.up, K), MAGENTA, 2)

    if result_text:
        _banner(canvas, result_text)
    return canvas


def _banner(canvas, text):
    h, w = canvas.shape[:2]
    fs = max(0.45, w / 1400.0)
    lines = text.split("\n")
    pad = int(8 * fs) + 4
    lh = int(26 * fs)
    box_h = lh * len(lines) + pad
    cv2.rectangle(canvas, (0, 0), (w, box_h), (24, 24, 24), -1)
    for i, ln in enumerate(lines):
        cv2.putText(canvas, ln, (pad, int(lh * (i + 0.8))), cv2.FONT_HERSHEY_SIMPLEX,
                    fs * 0.62, (240, 240, 240), max(1, int(fs * 1.4)), cv2.LINE_AA)


def side_by_side(before, after, labels=("before", "after"), max_edge=1600):
    h = max(before.shape[0], after.shape[0])
    def fit(img):
        s = h / img.shape[0]
        return cv2.resize(img, (max(1, int(round(img.shape[1] * s))), h),
                          interpolation=cv2.INTER_AREA)
    a, b = fit(before), fit(after)
    gap = np.full((h, 8, 3), 32, np.uint8)
    out = np.hstack([a, gap, b])
    s = min(1.0, max_edge / max(out.shape[1], out.shape[0]))
    if s < 1.0:
        out = cv2.resize(out, (int(out.shape[1] * s), int(out.shape[0] * s)),
                         interpolation=cv2.INTER_AREA)
    _banner(out, f"{labels[0]}   |   {labels[1]}")
    return out
