"""Line overlays: one colour table and one draw path for both panes.

The detected lines used to reach the screen two ways with two looks: OpenCV-
baked into the before image (BGR constants in `preview.py`, thickness 2) and
Tk canvas lines on the after pane (hex constants in `gui.py`, 1 px).  Both
panes of the review window now draw Tk canvas lines at width 1 through
`draw`, so a detected line is 1 px at the displayed size on each side, and
every line-overlay colour is read from `LINE` -- the one table for both
panes.  `preview.py`'s baked path reads the same entries (the values are
BGR, OpenCV's base), so there is no second set of constants for the same
meaning.
"""
from __future__ import annotations

import math

# The one table for every line overlay (BGR, OpenCV's base).
LINE = {
    # what the detector found on the original (before pane)
    "v_inlier":   (80, 220, 90),    # vertical inliers -- these drove the fit
    "v_outlier":  (60, 210, 240),   # vertical candidates rejected as outliers
    "h_line":     (235, 170, 60),   # horizontal lines
    "horizon":    (200, 80, 220),   # the implied horizon
    "struck":     (130, 130, 130),  # struck out by hand
    "masked":     (70, 70, 235),    # removed by the region mask
    "vp_mark":    (0, 128, 255),    # a vanishing point itself, as a ring
    # "Check lines" re-runs the detector on the RESULT (after pane): green
    # where a line came out truly straight, red/amber where it still leans.
    "check_ok":     (122, 255, 57), # #39ff7a
    "check_v_off":  (90, 90, 255),  # #ff5a5a
    "check_h_off":  (58, 176, 255), # #ffb03a
    # the second M-LSD pass, drawn beside the primary detector's colours and
    # therefore in a different hue family so the two can be told apart
    "mlsd_ok":      (255, 229, 0),  # #00e5ff
    "mlsd_v_off":   (3, 255, 118),  # #76ff03
    "mlsd_h_off":   (0, 255, 255),  # #ffff00
}


def hex_(bgr):
    """A BGR triple to the hex Tk wants -- the only place a colour changes form."""
    return "#%02x%02x%02x" % (bgr[2], bgr[1], bgr[0])


def draw(canvas, segs, ox, oy, colour, scale=1.0, width=1, tags=None):
    """``segs`` (N, 4) on a Tk canvas: 1 px at the displayed size.

    ``scale`` maps the segments' coordinate space onto canvas pixels; both
    panes pass the displayed scale, which is what keeps a line 1 px on screen
    in each pane rather than in the image it used to be baked into.
    """
    kw = {"fill": hex_(colour), "width": width}
    if tags is not None:
        kw["tags"] = tags
    for x0, y0, x1, y1 in segs:
        canvas.create_line(ox + x0 * scale, oy + y0 * scale,
                           ox + x1 * scale, oy + y1 * scale, **kw)


def off_angle_deg(x0, y0, x1, y1):
    """Degrees the segment leans from its nearest axis (horizontal or vertical).

    ``atan2(min(|dx|,|dy|), max(|dx|,|dy|))`` gives that in one formula: the
    smaller of the two extents IS the offset from whichever axis the larger
    one is closest to, so there is no separate branch for "vertical-ish" vs
    "horizontal-ish" -- classifying the segment for colour purposes (see
    `check_colour`) is a different question from measuring how far off it is.
    """
    dx, dy = abs(x1 - x0), abs(y1 - y0)
    return math.degrees(math.atan2(min(dx, dy), max(dx, dy, 1e-9)))


def check_colour(x0, y0, x1, y1, tol_deg, ok, v_off, h_off):
    """A check line's colour: straight within ``tol_deg`` of an axis, else the
    off colour of its orientation.  The primary detector's pass and the M-LSD
    second pass both colour through this, in two palettes."""
    if off_angle_deg(x0, y0, x1, y1) <= tol_deg:
        return ok
    return v_off if abs(y1 - y0) >= abs(x1 - x0) else h_off


def draw_check(canvas, segs, ox, oy, ok, v_off, h_off, tol_deg, width=1, tags=None):
    """One "Check lines" pass: every segment in its own colour, 1 px at the
    displayed size (segments already in canvas coordinates)."""
    for x0, y0, x1, y1 in segs:
        colour = check_colour(x0, y0, x1, y1, tol_deg, ok, v_off, h_off)
        kw = {"fill": hex_(colour), "width": width}
        if tags is not None:
            kw["tags"] = tags
        canvas.create_line(ox + x0, oy + y0, ox + x1, oy + y1, **kw)


def infinite_line_points(line, w, h):
    """Two clipped endpoints where an (a, b, c) line crosses the [0, w] x [0, h] frame.

    Clipping to the image rectangle keeps the horizon inside the photograph on
    a letterboxed Tk canvas (OpenCV clips to the image automatically, Tk does
    not). Shared by `preview._draw_infinite_line` and `gui._draw_before_lines`.
    """
    a, b, c = line
    w, h = float(w), float(h)
    eps = 1e-6
    pts = []
    if abs(b) > 1e-9:
        for x in (0.0, w):
            y = -(a * x + c) / b
            if -eps <= y <= h + eps:
                pts.append((x, min(max(y, 0.0), h)))
    if abs(a) > 1e-9:
        for y in (0.0, h):
            x = -(b * y + c) / a
            if -eps <= x <= w + eps:
                pts.append((min(max(x, 0.0), w), y))
    # Deduplicate corner hits where the line passes through an exact image corner.
    uniq = []
    for p in pts:
        if all(abs(p[0] - q[0]) > 1e-3 or abs(p[1] - q[1]) > 1e-3 for q in uniq):
            uniq.append(p)
    return uniq[:2]
