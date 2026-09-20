"""Manhattan-world line scheme for architectural photos.

Why this exists
---------------
``lines.split_by_orientation`` decides vertical vs horizontal by how far a
segment leans from the *image* axes inside a fixed window.  That is a proxy: it
cannot tell a facade edge from a roof rafter that happens to sit inside the
window, and in a corner view it mixes the two facades' horizontals together.

This module measures lines against the scene's own geometry instead.  Under the
Manhattan-world assumption a building has three mutually orthogonal direction
families, each converging to one vanishing point::

    V_v   verticals             (off the horizon, far up or down)
    V_h1  horizontals, facade A (on the horizon)
    V_h2  horizontals, facade B (on the horizon)

Facade A is the plane spanned by {V_v, V_h1}; facade B by {V_v, V_h2}; the two
planes meet at a right angle.  That frame -- not the image axes -- is the scheme
every line is measured against.  A segment belongs to the building when its
direction points at one of the three vanishing points within tolerance; a brace,
rafter or tree branch points at none of them and is set aside.

Nothing is deleted.  ``filter_by_vanishing_points`` partitions the full set into
``relevant`` (feeds the fit) and ``ignored`` (kept, drawn dim), so a wrong scheme
is visible instead of quietly discarding evidence.

The horizontal-correction rule
------------------------------
When only the horizon is being levelled for one facade, the second plane is a
contaminant: its horizontals point at V_h2 and would pull the horizon off.  With
``horizontal_correction_only=True`` the scheme keeps a single reference plane
{V_v, V_h1}; every line pointing at V_h2 is ignored.  Verticals stay in both
modes -- a direction alone cannot say which facade a vertical belongs to, and the
fit needs them either way.

Integration note: this is a classifier, not a corrector.  It reuses
``vanishing.search`` / ``search_sequential`` and the existing orientation split
to *find* the frame, then partitions lines.  The roll/pitch/f fit and the
``H = K R K^-T`` warp are untouched; only which lines vote changes.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from . import geometry as G
from . import preview as PV
from . import vanishing as V
from .lines import LineSet, split_by_orientation


class ArchitectureScheme:
    """The building's orthogonal frame plus the relevant/ignored partition."""

    def __init__(self, ls: LineSet, w: int, h: int, settings):
        self.ls = ls
        self.w, self.h = int(w), int(h)
        self.settings = settings
        # scheme vanishing points (unit-norm homogeneous 3-vectors) or None
        self.vp_vert = None
        self.vp_h1 = None
        self.vp_h2 = None
        self.support: dict[str, float] = {}
        self.established = False
        self.degraded = False
        self._detected = False
        # per-line results, indexed like ls.seg
        self.deviation_deg = np.zeros(len(ls))
        self.plane = np.full(len(ls), -1, dtype=int)   # -1 ignored, 0 v, 1 h1, 2 h2
        self.relevant: LineSet | None = None
        self.ignored: LineSet | None = None

    @classmethod
    def from_image(cls, gray, settings, bgr=None):
        """Build a scheme straight from an image via the normal front end."""
        from . import lines as L
        ls, _vert, _horiz, _name, _info = L.prepare(gray, settings, bgr)
        h, w = gray.shape[:2]
        return cls(ls, w, h, settings)

    # -- step 1: find the frame ------------------------------------------
    def detect(self):
        """Locate the vertical and the horizontal vanishing points.

        Reuses the project's own RANSAC so the scheme agrees with what the fit
        will use.  The frame is *established* only when it has a vertical and at
        least one horizontal direction; otherwise ``filter`` refuses to touch the
        lines rather than partitioning on a weak hypothesis."""
        s = self.settings
        vert, horiz = split_by_orientation(
            self.ls, s.vertical_window_deg, s.horizontal_window_deg,
            getattr(s, "angular_softness", 0.35))

        vh = V.search(vert, self.w, self.h, s, "vertical", n_hypotheses=1)
        if vh:
            self.vp_vert = vh[0].vp
            self.support["v"] = float(vh[0].support)

        # sequential search surfaces genuinely distinct horizontal directions;
        # the dominant one is facade A, the next (if any) is facade B.
        hs = V.search_sequential(horiz, self.w, self.h, s, "horizontal", k=4)
        if len(hs) >= 1:
            self.vp_h1 = hs[0].vp
            self.support["h1"] = float(hs[0].support)
        if len(hs) >= 2:
            self.vp_h2 = hs[1].vp
            self.support["h2"] = float(hs[1].support)

        self.established = self.vp_vert is not None and self.vp_h1 is not None
        # Degraded means "the frame is not solid enough to trust fully": either
        # it is incomplete (no vertical, or no dominant horizontal) so the core
        # partition is refused, or a second plane was found but only weakly --
        # below the same share floor the sequential search uses -- so its lines
        # may be mis-attributed.  Computed here, at detection, because it is a
        # property of the frame rather than a side effect of filtering.
        h2_weak = self.vp_h2 is not None and self.support.get("h2", 0.0) < 0.04
        self.degraded = (not self.established) or h2_weak
        self._detected = True
        return self

    # -- step 2: partition -------------------------------------------------
    def filter_by_vanishing_points(self, horizontal_correction_only: bool = False):
        """Split ``ls`` into ``(relevant, ignored)`` by deviation from the frame.

        A line is relevant when its direction points at an active vanishing point
        within 12 degrees.  **Not a setting**: `Settings` has no
        ``scheme_tol_deg`` field, and the ``getattr`` below is a hook for a
        caller that constructs its own settings object, not a knob a user can
        reach.  This said "settings.scheme_tol_deg (default 12)", which sent
        a reader looking for a control that does not exist.  In horizontal-only mode
        the secondary plane {V_v, V_h2} drops out of the active set, so its lines
        are ignored.  If the frame was never established the whole set is kept as
        relevant and nothing is filtered -- a weak scheme must not discard
        evidence."""
        if not self._detected:
            self.detect()
        n = len(self.ls)
        empty = LineSet(np.zeros((0, 4)))
        if not self.established:
            # `degraded` was already set in detect(); the frame is incomplete so
            # nothing is partitioned -- keep every line as relevant.
            self.relevant, self.ignored = self.ls, empty
            return self.relevant, self.ignored

        tol = math.radians(getattr(self.settings, "scheme_tol_deg", 12.0))
        active = [("v", 0, self.vp_vert)]
        if self.vp_h1 is not None:
            active.append(("h1", 1, self.vp_h1))
        if self.vp_h2 is not None and not horizontal_correction_only:
            active.append(("h2", 2, self.vp_h2))

        dev = np.full(n, math.pi / 2)      # worst case: perpendicular to all of them
        plane = np.full(n, -1, dtype=int)
        for _name, code, vp in active:
            res = G.angular_residual(vp, self.ls.mid, self.ls.dir)   # [0, pi/2]
            better = res < dev
            dev[better] = res[better]
            plane[better] = code

        mask = dev <= tol
        self.deviation_deg = np.degrees(dev)
        self.plane = plane
        self.relevant = self.ls.subset(mask)
        self.ignored = self.ls.subset(~mask)
        return self.relevant, self.ignored

    # -- step 3: preview ---------------------------------------------------
    def draw_preview(self, bgr):
        """Render the partition on a copy of ``bgr``: relevant green, ignored dim.

        The colours come from ``preview`` rather than from here. They were
        written inline -- (0, 200, 0) for the lines that count, (96, 96, 96)
        for the ones that do not -- while `preview.py` drew the same two ideas
        as GREEN (80, 220, 90) and GREY (130, 130, 130). Two renderings of the
        same geometry that did not agree on what green means, which is an
        awkward place to start from when the open question is which of the two
        to show by default.
        """
        out = bgr.copy()
        if self.ignored is not None and len(self.ignored):
            for x0, y0, x1, y1 in self.ignored.seg:
                cv2.line(out, (int(x0), int(y0)), (int(x1), int(y1)),
                         PV.GREY, 1, cv2.LINE_AA)
        if self.relevant is not None and len(self.relevant):
            for x0, y0, x1, y1 in self.relevant.seg:
                cv2.line(out, (int(x0), int(y0)), (int(x1), int(y1)),
                         PV.GREEN, 2, cv2.LINE_AA)
        for vp in (self.vp_vert, self.vp_h1, self.vp_h2):
            if vp is None or abs(vp[2]) < 1e-9:
                continue
            x, y = int(vp[0] / vp[2]), int(vp[1] / vp[2])
            cv2.circle(out, (x, y), 6, PV.VP_MARK, 2, cv2.LINE_AA)
        return out

    def summary(self):
        """One line of status for the review panel or a log."""
        if not self.established:
            return "scheme: not established -- no filtering"

        def fmt(name, vp):
            if vp is None:
                return f"{name}=--"
            if abs(vp[2]) < 1e-9:
                ang = math.degrees(math.atan2(vp[0], vp[1]))
                return f"{name}=inf({ang:.0f}deg)"
            return f"{name}=({vp[0] / vp[2]:.0f},{vp[1] / vp[2]:.0f})"

        rel = len(self.relevant) if self.relevant is not None else 0
        igd = len(self.ignored) if self.ignored is not None else 0
        return (f"scheme: {fmt('v', self.vp_vert)} "
                f"{fmt('h1', self.vp_h1)} {fmt('h2', self.vp_h2)}"
                f" | relevant {rel}, ignored {igd}")
