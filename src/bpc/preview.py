"""Debug overlays: what the detector saw and what it decided."""
from __future__ import annotations

import math

import cv2
import numpy as np

from . import geometry as G

GREEN = (80, 220, 90)      # vertical inliers -- these drove the fit
YELLOW = (60, 210, 240)    # vertical candidates rejected as outliers
BLUE = (235, 170, 60)      # horizontal lines
MAGENTA = (200, 80, 220)   # the implied horizon


def _draw_lines(canvas, seg, colour, thickness=2):
    for x0, y0, x1, y1 in seg:
        cv2.line(canvas, (int(round(x0)), int(round(y0))), (int(round(x1)), int(round(y1))),
                 colour, thickness, cv2.LINE_AA)


def _draw_infinite_line(canvas, line, colour, thickness=2):
    h, w = canvas.shape[:2]
    a, b, c = line
    pts = []
    if abs(b) > 1e-9:
        pts += [(0.0, -c / b), (float(w), -(a * w + c) / b)]
    if abs(a) > 1e-9:
        pts += [(-c / a, 0.0), (-(b * h + c) / a, float(h))]
    pts = [p for p in pts if -1e5 < p[0] < 1e5 and -1e5 < p[1] < 1e5]
    if len(pts) >= 2:
        cv2.line(canvas, (int(pts[0][0]), int(pts[0][1])), (int(pts[1][0]), int(pts[1][1])),
                 colour, thickness, cv2.LINE_AA)


def overlay(bgr, vert, horiz, model, scale=1.0, result_text=""):
    """Annotated copy of the input showing the evidence behind the decision."""
    canvas = bgr.copy()
    inv = 1.0 / scale if scale else 1.0

    if len(horiz):
        _draw_lines(canvas, horiz.seg * inv, BLUE, max(1, int(round(inv))))
    if len(vert):
        inl = model.vert_inliers if model is not None and model.vert_inliers is not None \
            else np.zeros(len(vert), bool)
        if len(inl) == len(vert):
            _draw_lines(canvas, vert.seg[~inl] * inv, YELLOW, max(1, int(round(inv))))
            _draw_lines(canvas, vert.seg[inl] * inv, GREEN, max(2, int(round(inv * 2))))
        else:
            _draw_lines(canvas, vert.seg * inv, GREEN, max(2, int(round(inv * 2))))

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
