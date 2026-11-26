"""Headless crop and planar geometry for the review session.

The two cohesive geometry blocks of ``ReviewSession`` -- the manual and
automatic crop with its kept-mask helpers, and the facade strip with the
planar (four-point) rectification and the horizontal-auto fold -- live
here as one mixin.  ``review.ReviewSession`` mixes the class in; this
module depends only on the geometry layers below it (warp, planar,
imageio) and on the standard library, so it stays importable on its own.
"""
from __future__ import annotations

import math
import os

import cv2
import numpy as np

from . import imageio as IO
from . import planar as P
from . import warp as W


class _ReviewCropPlanarMixin:
    """Crop, kept-mask, facade-strip and planar-rectification geometry.

    Mixed into ``ReviewSession``; the methods read and write the session's
    own state (``_crop_edges``, ``strip``, ``planar_quad``, the mask
    layers, ...), which is why it is a mixin rather than a standalone
    object.  No GUI toolkit and no session import: headless by design.
    """

    # -- crop and kept-mask geometry -------------------------------
    @property
    def crop_rect(self):
        """Derived (x0, y0, x1, y1) in 0..1, or None if no edge is trimmed."""
        top_en, top_f = self._crop_edges["top"]
        bot_en, bot_f = self._crop_edges["bottom"]
        lft_en, lft_f = self._crop_edges["left"]
        rgt_en, rgt_f = self._crop_edges["right"]
        x0 = lft_f if lft_en else 0.0
        y0 = top_f if top_en else 0.0
        x1 = 1.0 - rgt_f if rgt_en else 1.0
        y1 = 1.0 - bot_f if bot_en else 1.0
        if x0 == 0.0 and y0 == 0.0 and x1 == 1.0 and y1 == 1.0:
            return None
        return (x0, y0, x1, y1)

    def set_crop_edge(self, edge: str, enabled: bool, frac: float = 0.0):
        """Enable or disable trimming of one edge by a fraction of the frame.

        Four independent edges rather than one rectangle, because the padded
        band is rarely symmetric: a pitch correction opens it at the top and
        leaves the bottom alone, and a photographer who wants only that band
        gone should not have to re-draw the other three sides to say so.
        Clamped to half the frame, so no edge can cross the opposite one.
        """
        if edge not in self._crop_edges:
            return False
        frac = max(0.0, min(0.5, float(frac)))
        self._crop_edges[edge] = (bool(enabled), frac)
        return True

    def set_crop_rect(self, x0, y0, x1, y1, shown_w, shown_h):
        """Set all four edges at once from a dragged rectangle, in displayed pixels.

        Deliberately applied *after* the correction rather than instead of it.
        The automatic crop has to guess how much of the frame is worth trading
        for straight verticals; once the whole frame is kept and padded, that
        trade becomes a thing you can see, and dragging a rectangle over it is a
        better answer than any threshold.  It is also the only way to keep an
        asymmetric composition -- the automatic rectangle is anchored on the
        image centre, and a photographer who framed the building off-centre
        wants to keep it there.

        A drag is just the four-edge case of ``set_crop_edge``; nothing here
        stores a rectangle of its own.
        """
        # Whoever set it last owns it: `refresh_auto_crop` reads this to
        # decide whether it may replace what is there. A hand-drawn rectangle
        # is a decision and the automatic one must not overwrite it.
        self.crop_is_auto = False
        if shown_w <= 0 or shown_h <= 0:
            return False
        x0, x1 = sorted((float(x0) / shown_w, float(x1) / shown_w))
        y0, y1 = sorted((float(y0) / shown_h, float(y1) / shown_h))
        x0, y0 = max(0.0, x0), max(0.0, y0)
        x1, y1 = min(1.0, x1), min(1.0, y1)
        if (x1 - x0) < 0.05 or (y1 - y0) < 0.05:
            return False
        self._crop_edges["left"]   = (True, x0)
        self._crop_edges["top"]    = (True, y0)
        self._crop_edges["right"]  = (True, 1.0 - x1)
        self._crop_edges["bottom"] = (True, 1.0 - y1)
        return True

    def auto_crop(self):
        """Trim to the largest rectangle that contains no invented pixel.

        The same frame ``warp.plan`` computes for ``crop="auto"``, minus the
        ``crop_max_loss`` gate: the gate exists to stop a batch quietly throwing
        a quarter of every picture away, and a button pressed by hand is not
        quiet.  It keeps the original aspect ratio.

        The rectangle itself is **not** the plan's: the plan anchors on the
        mapped image centre so the composition survives, and pays for that in
        area.  A crop pressed by hand wants the other trade -- the largest
        rectangle that fits anywhere, which is ``warp.max_inscribed_rect``.
        On an asymmetric correction (roll *and* pitch) the centred rectangle
        is measurably smaller than the best one, and the difference is what
        made this button read as "random" rather than "maximal".

        This is the answer to the padded band that does not involve inventing
        anything.  Filling the band -- telea, lama, comfyui -- makes up pixels
        the camera never saw; cutting to here makes up none, at the cost of the
        frame it cuts.  For most corrections that cost is a few per cent, which
        is why this is worth a button rather than a model.

        Returns ``False``, having changed nothing, when there is no band to cut
        -- an uncorrected photograph, or one the plan already cropped.
        """
        if self.active_quad() is not None:
            # On the rectified view the automatic crop is the quad + 20 %;
            # the canvas around it is there to drag the crop out by hand.
            return self._planar_default_crop()
        roll, pitch, f, _ = self.current_angles()
        yaw = self.current_yaw()
        if abs(roll) < 1e-9 and abs(pitch) < 1e-9 and abs(yaw) < 1e-9:
            return False
        H = self._build_H(self.w, self.h, f, roll, pitch, yaw, max_area=self.settings.max_area_ratio)
        _segs = self._plan_segs(self.w)
        planned = W.plan(self.w, self.h, H, self.settings, line_segs=_segs, yaw=yaw,
                         strip=self.strip)
        if planned is None:
            return False
        H_total, ow, oh, _, _ = planned
        quad = W.warped_quad(H_total, self.w, self.h)
        # Search only where a crop can land (2026-09-26, user-caught "auto
        # crop freaked"): the canvas, and with a facade strip placed, the
        # strip + 20% -- the same band `plan` bounds itself to. Searching the
        # whole quad and clamping afterwards returned a rectangle that fitted
        # the quad somewhere off-canvas and was then cut to a sliver.
        region = W.clip_to_box(quad, 0.0, 0.0, float(ow), float(oh))
        yawed = abs(yaw) > 1e-9
        if self.strip is not None and yawed and len(region) >= 3:
            band = W._strip_band(H_total, quad, self.strip, self.w, self.h)
            if band is not None:
                region = W._clip_quad_to_x_band(region, band[0], band[1])
        # A mask says what matters (user, 2026-10-24: "Maskierung muss relevant
        # sein ... nur der unmaskierte Ausschnitt, wie ROI"): the kept region,
        # warped, + STRIP_CROP_MARGIN bounds the search like a facade strip
        # does, ahead of the centre of interest; the centring then centres on it.
        kept = self._kept_box(H_total, ow, oh) if self.strip is None else None
        if kept is not None and len(region) >= 3:
            region = W.clip_to_box(region, *kept)
        if len(region) < 3:
            return False
        # Free aspect once a yaw is in play: the output's proportions are not
        # the photograph's any more, and the source aspect on a steep
        # trapezoid parks the rectangle in its tallest corner.
        # A soft horizontal-auto correction (user, 2026-10-24: "bei soften
        # HA-Korrekturen Auto-Crop auf Mittelpunkt Outline (Trapez)") keeps
        # the crop on the middle of the warped outline instead of letting it
        # slide to wherever it fits best or to the line-dense region.
        soft = yawed and math.degrees(abs(yaw)) <= self.SOFT_YAW_DEG and self.strip is None
        src_area = lambda r: W.source_area(H_total, r)
        if soft:
            rect = W.centred_free_rect(region, W.poly_centroid(region), score=src_area)
        else:
            # Auto (no yaw) is centred too, in the photograph's own aspect.
            rect = (W.max_free_rect(region, score=src_area) if yawed
                    else W.inscribed_rect(region, self.w / float(self.h),
                                          W.poly_centroid(region)))
        if rect is None:
            return False
        x0, y0, x1, y1 = (float(t) for t in rect)
        # The plan may have cropped already, in which case the quad runs past
        # the canvas and the inscribed rectangle with it.  Clamping then gives
        # the whole frame back, and `set_crop_rect` is left to decide that a
        # rectangle trimming nothing is not worth storing.
        x0, x1 = max(0.0, min(float(x0), ow)), max(0.0, min(float(x1), ow))
        y0, y1 = max(0.0, min(float(y0), oh)), max(0.0, min(float(y1), oh))
        if (x1 - x0) < 8 or (y1 - y0) < 8:
            return False
        # The tolerance is FRINGE, not one pixel: the warped quad's boundary is
        # fuzzy by exactly that much (see below), so "does this rectangle cover
        # the canvas" is only decidable within it.  A crop short of the frame by
        # less than the fringe trims resampling noise, not content -- the saved
        # file would differ by a couple of boundary rows at most.
        if (x1 - x0) >= ow - W.FRINGE and (y1 - y0) >= oh - W.FRINGE:
            return False        # nothing was padded; nothing to trim
        # Inset by the same margin `warp.filled_region` grows the hole by, and
        # only once the guards above have decided there is a band at all -- the
        # "nothing to trim" test compares against the full canvas, and three
        # pixels of inset would slip under it and store a rectangle that trims
        # only the inset.
        #
        # The inscribed rectangle is exact against the *quad*, but the resampler
        # leaves a sub-pixel fringe along that diagonal edge, which is why the
        # fill path dilates before inpainting.  Measured on a 9 deg pitch: zero
        # invented pixels inside the rectangle at grow=0, and 88 at grow=3, all
        # in the outermost three rows of one corner.  The inset costs 0.9
        # percentage points of frame there (35.7% -> 36.6%) and makes "contains
        # no invented pixel" true against the definition the rest of the module
        # uses, not merely against the quad.
        inset = float(W.FRINGE)
        x0, y0 = min(x0 + inset, x1), min(y0 + inset, y1)
        x1, y1 = max(x1 - inset, x0), max(y1 - inset, y0)
        # Keep headroom above the eave when a fill is active: the fill invents
        # the sky above the roof, so a crop that starts at the roofline (the
        # fill-free rectangle) leaves none.  The eave is the topmost long
        # horizontal; pull the top edge up to leave EAVE_HEADROOM of the crop
        # height above it, but only ever lower y0 (never crop into the building).
        fill_mode = getattr(self.settings, "fill", None)
        if fill_mode not in (None, "", "none"):
            gx = 0.5 * W.AUTO_CROP_GROW_X * (x1 - x0)
            gy = 0.5 * W.AUTO_CROP_GROW_Y * (y1 - y0)
            qx0, qy0 = (float(v) for v in np.min(quad, axis=0))
            qx1, qy1 = (float(v) for v in np.max(quad, axis=0))
            x0, x1 = max(0.0, qx0, x0 - gx), min(float(ow), qx1, x1 + gx)
            y0, y1 = max(0.0, qy0, y0 - gy), min(float(oh), qy1, y1 + gy)
        # Centre of interest (user, 2026-10-23: "Dichte LSD", "Himmel fliegt
        # raus"): with a yaw in play and no strip, frame where the lines are.
        # With a fill the box is the crop; without one it only trims the
        # fill-free rectangle, which must stay free of invented pixels.
        box = kept
        if box is None and self.CROP_TO_INTEREST and yawed and self.strip is None and not soft:
            k = self.w / float(self.gray.shape[1])
            parts = [np.asarray(x.seg, float)[:, :4] * k for x in (self.vert, self.horiz) if len(x)]
            box = W.interest_box(H_total, np.concatenate(parts), ow, oh) if parts else None
        if box is not None:
            bx0, by0, bx1, by1 = box
            if fill_mode not in (None, "", "none"):
                qx0, qy0 = (float(v) for v in np.min(quad, axis=0))
                qx1, qy1 = (float(v) for v in np.max(quad, axis=0))
                nx0, ny0 = max(bx0, qx0), max(by0, qy0)
                nx1, ny1 = min(bx1, qx1), min(by1, qy1)
            else:
                nx0, ny0, nx1, ny1 = max(x0, bx0), max(y0, by0), min(x1, bx1), min(y1, by1)
            if nx1 - nx0 >= 8 and ny1 - ny0 >= 8:
                x0, y0, x1, y1 = nx0, ny0, nx1, ny1
        if fill_mode not in (None, "", "none") and len(self.horiz):
            segs = np.asarray(self.horiz.seg, float)[:, :4]
            segs = segs * (self.w / self.gray.shape[1])
            ye = W.eave_y(H_total, segs, float(ow), x0, x1)
            if ye is not None and y0 > ye - W.EAVE_HEADROOM * (y1 - ye):
                y0 = max(0.0, ye - W.EAVE_HEADROOM * (y1 - ye))
        return self.set_crop_rect(x0, y0, x1, y1, ow, oh)

    KEPT_BOX_MAX_SHARE = 0.95  # a mask keeping more than this frames nothing
    PAINT_FRAMES_SHARE = 0.15  # painted mask frames the crop only from this share on

    def _kept_mask(self):
        """What the SAM selection / automatic mask keeps (analysis pixels, True =
        kept), or None when neither is in force or it keeps nearly everything.

        The painted mask counts only from PAINT_FRAMES_SHARE of the frame on:
        a painted mask is not an answer about the subject the way an automatic
        one is -- it is often just point dabs that switch off lines, placed
        outside the subject. A few dabs that switch off lines say nothing
        about where the subject is, a broad wash does. The strip
        has its own band."""
        if not getattr(self, "mask_enabled", True):
            return None
        ign = None
        for name in ("source", "sam", "paint"):
            arr = self.layer(name)
            if name == "paint" and arr is not None and arr.mean() < self.PAINT_FRAMES_SHARE:
                continue
            if arr is not None and arr.any():
                ign = arr.astype(bool) if ign is None else (ign | arr.astype(bool))
        if ign is None:
            return None
        if getattr(self, "invert_mask", False):
            ign = ~ign
        keep = ~ign
        if not keep.any() or keep.mean() > self.KEPT_BOX_MAX_SHARE:
            return None
        return keep

    def _kept_source_x(self):
        """``(x0, x1)`` full-resolution px spanned by the kept region, or None."""
        keep = self._kept_mask()
        if keep is None:
            return None
        box = self._sam_crop_box()
        if box is not None:
            return box[0], box[2]
        cols = np.flatnonzero(keep.any(axis=0))
        k = self.w / float(keep.shape[1])
        return float(cols[0] * k), float((cols[-1] + 1) * k)

    def _sam_crop_box(self):
        """The dragged SAM box in full-resolution px while its selection is in
        force and the mask is on, else None."""
        box = getattr(self, "sam_box", None)
        if box is None or self.sam_mask is None or not getattr(self, "mask_enabled", True):
            return None
        if getattr(self, "invert_mask", False):
            return None          # inverted, the box is what is thrown away
        x0, y0, x1, y1 = box
        if max(box) <= 1.0:      # the GUI's fallback hands frame fractions
            x0, x1, y0, y1 = x0 * self.w, x1 * self.w, y0 * self.h, y1 * self.h
        x0, x1 = sorted((max(0.0, x0), min(float(self.w), x1)))
        y0, y1 = sorted((max(0.0, y0), min(float(self.h), y1)))
        if x1 - x0 < 8 or y1 - y0 < 8:
            return None
        return x0, y0, x1, y1

    def _kept_box(self, H_total, ow, oh):
        """``(x0, y0, x1, y1)`` output px around what the SAM/automatic mask
        keeps, + ``W.STRIP_CROP_MARGIN`` on every side, or None without a mask.

        The kept region's outline (analysis pixels) is mapped through
        ``H_total``; points that land behind the camera are dropped."""
        keep = self._kept_mask()
        if keep is None:
            return None
        box = self._sam_crop_box()
        if box is not None:
            # The dragged box IS the crop: its corners, no margin. A
            # homography keeps its edges straight, so the corners bound it.
            bx0, by0, bx1, by1 = box
            pts = np.array([[bx0, by0], [bx1, by0], [bx1, by1], [bx0, by1]], float)
            margin = 0.0
        else:
            cs, _ = cv2.findContours(keep.astype(np.uint8), cv2.RETR_EXTERNAL,
                                     cv2.CHAIN_APPROX_NONE)
            if not cs:
                return None
            pts = np.concatenate([c.reshape(-1, 2) for c in cs]).astype(float)
            pts *= self.w / float(keep.shape[1])
            margin = W.STRIP_CROP_MARGIN
        hp = np.c_[pts, np.ones(len(pts))] @ np.asarray(H_total, float).T
        # A homography is defined up to scale, sign included: "in front of the
        # camera" is the side most of the region maps to.
        side = np.sign(np.median(hp[:, 2])) or 1.0
        ok = hp[:, 2] * side > 1e-12
        if ok.sum() < 3:
            return None
        xy = hp[ok, :2] / hp[ok, 2:3]
        x0, y0 = xy.min(axis=0)
        x1, y1 = xy.max(axis=0)
        mx, my = margin * (x1 - x0), margin * (y1 - y0)
        x0, x1 = max(0.0, x0 - mx), min(float(ow), x1 + mx)
        y0, y1 = max(0.0, y0 - my), min(float(oh), y1 + my)
        if x1 - x0 < 8 or y1 - y0 < 8:
            return None
        return float(x0), float(y0), float(x1), float(y1)

    def auto_crop_if_cheap(self):
        """Trim the padded band away when doing so costs little frame.

        A small correction opens a band a few per cent wide, and there are only
        two honest answers to it: invent pixels, or lose that frame.  When the
        loss is this small the second is plainly the better one -- nobody wants
        a generative model, a checkpoint and three seconds of inference to
        replace 2 % of sky that cropping removes for free.

        The threshold is ``auto_crop_threshold`` (30 % in interactive mode;
        see ``__init__``).  It cannot be ``settings.crop_max_loss``, however much
        a second number offends: the padded band exists precisely *because* the
        plan's gate was exceeded, so reusing that gate here would mean never
        firing.  Measured on rendered scenes, with the fitted angle rather than
        the requested one:

        ==========  ==========  ==========  ==========
        pitch       band        crop costs  batch does
        ==========  ==========  ==========  ==========
        0.5 deg     3.2 %       6.5 %       crops
        1.0 deg     3.4 %       7.5 %       crops
        2.0 deg     4.1 %       9.4 %       pads
        3.0 deg     5.0 %       12.0 %      pads
        9.0 deg     8.9 %       23.0 %      pads
        ==========  ==========  ==========  ==========

        Cropping costs about **2.5x the band**, because the rectangle keeps the
        original aspect ratio and stays anchored on the mapped centre.  The 30 %
        gate therefore covers corrections up to roughly 9 degrees, which is
        where most of them are.

        Thirty per cent taken without being asked would be indefensible in the
        batch. It is defensible here for one reason: this runs in the review
        window, the result is on screen with the discarded part shaded, and it
        becomes a file only when the user presses Save. "Reset crop" undoes it.

        Returns True when it cropped.  Never overrides a crop already there.
        """
        if self.crop_rect is not None:
            return False
        if not self.auto_crop():
            return False
        if self.crop_loss() <= self.auto_crop_threshold:
            self.crop_is_auto = True
            return True
        self.clear_crop_rect()
        return False

    def refresh_auto_crop(self):
        """Recompute the automatic crop for the correction now in force.

        ``auto_crop_if_cheap`` runs once, when the photograph opens, and
        refuses to touch a crop that already exists. Both of those are right
        for a one-shot. Neither is right for a MODE: switch horizontal auto on,
        or move the facade strip, and the correction changes completely while
        the crop stays where it was computed for a different picture -- which
        is what "the auto crop is not actualising" means (user, 2026-09-21).

        A crop the USER drew is never touched. ``crop_is_auto`` records who put
        the current one there, because "is there a crop" cannot answer "may I
        replace it".

        Returns True when the crop changed.
        """
        if self.crop_rect is not None and not getattr(self, "crop_is_auto", False):
            return False                      # a hand-drawn crop is a decision
        before = self.crop_rect
        self.clear_crop_rect()
        self.crop_is_auto = False
        if not self.auto_crop():
            return before is not None
        # The 0.30 gate is calibrated for small pitch bands (auto_crop_if_cheap).
        # A horizontal-auto crop is a facade-to-rectangle warp and is inherently
        # large (measured 0.77 loss on Platte_1); clearing it here would leave
        # the auto crop "not actualising" the moment the mode it was computed
        # for is in force. The manual _auto_crop keeps it regardless of loss, so
        # match that when the rectify warp is active.
        # The rectangle's crop is quad + 20 % of a canvas built with room to
        # drag it out, so its loss is large by design -- not a band to refuse.
        # A mask-made crop is the user's framing too: what they kept.
        if self.crop_loss() > self.auto_crop_threshold and not self._rectify_active()                 and self.active_quad() is None and self._kept_mask() is None:
            self.clear_crop_rect()
            return before is not None
        self.crop_is_auto = True
        return self.crop_rect != before

    def crop_loss(self):
        """Fraction of the corrected frame the current crop discards."""
        if self.crop_rect is None:
            return 0.0
        x0, y0, x1, y1 = self.crop_rect
        return 1.0 - max(0.0, x1 - x0) * max(0.0, y1 - y0)

    def clear_crop_rect(self):
        had = any(en for en, _ in self._crop_edges.values())
        self._crop_edges = {k: (False, 0.0) for k in self._crop_edges}
        return had

    # -- horizontal-evidence region (x-band) -----------------------------
    def set_strip(self, x0, x1, display_scale: float = 1.0) -> bool:
        """Restrict the *horizontal* line evidence to a vertical strip.

        ``x0``/``x1`` are displayed-image pixels; the band is stored as
        FRACTIONS of the image width, 0..1.

        One unit for the strip, everywhere. It used to be analysis pixels here
        and fractions on the Result -- two fields of the same name holding two
        different things, which is the shape that produced two of this
        project's unit bugs. Fractions are the only form that survives a change
        of analysis resolution, and they are what the rulers already display.

        A strip narrower than 5 % of the frame is refused -- a mis-drag that
        selects almost nothing should not pass as a deliberate choice."""
        inv = self.scale / max(display_scale, 1e-9)
        gh, gw = self.gray.shape[:2]
        ax0, ax1 = sorted((float(x0) * inv, float(x1) * inv))
        ax0, ax1 = max(0.0, ax0), min(float(gw), ax1)
        if (ax1 - ax0) < 0.05 * gw:
            return False
        self.strip = (ax0 / gw, ax1 / gw)
        self.refit()
        return True

    def clear_strip(self) -> bool:
        had = self.strip is not None
        self.strip = None
        if had:
            self.refit()
        return had

    # -- planar (four-point) correction ---------------------------------
    def set_facade_mode(self, mode: str) -> bool:
        """Switch between drawn lines ("lines") and the PC Rectangle ("rect").

        The side that goes is moved aside, not deleted, and the side that comes
        back is restored exactly; then the fit runs again. Returns True when the
        mode changed.
        """
        if mode not in ("lines", "rect"):
            raise ValueError(mode)
        if mode == self.facade_mode:
            return False
        if mode == "rect":
            self._stashed_lines = (self.control_lines, self.control_hlines)
            self.control_lines = np.zeros((0, 4))
            self.control_hlines = np.zeros((0, 4))
            self.planar_quad = list(self._stashed_quad)
            self._stashed_quad = []
        else:
            self._stashed_quad = list(self.planar_quad)
            self.planar_quad = []
            self.control_lines, self.control_hlines = self._stashed_lines
            self._stashed_lines = (np.zeros((0, 4)), np.zeros((0, 4)))
        crop = (dict(self._crop_edges), getattr(self, "crop_is_auto", False))
        self._crop_edges, self.crop_is_auto = dict(self._stashed_crop[0]), self._stashed_crop[1]
        self._stashed_crop = crop
        self.facade_mode = mode
        self.refit()
        return True

    def planar_is_set(self) -> bool:
        """Four corners down: the planar rectification is what the user sees."""
        return len(self.planar_quad) >= 4

    def active_quad(self):
        """The quad the planar path rectifies: the hand-placed one, else the
        facade horizontal auto would fold (:meth:`ha_fold_quad`); None when
        neither is in force."""
        if len(self.planar_quad) >= 4:
            return np.array(self.planar_quad, float)
        return self.ha_fold_quad()

    def ha_fold_quad(self):
        """Horizontal auto on a facade whose vanishing line crosses the photo.

        A strongly foreshortened facade sends its horizontals to a vanishing
        point near or inside the frame; warping the WHOLE photograph then folds
        the part beyond that line over, and `warp.plan` used to give up -- the
        preview fell back to the uncorrected photo and Save refused. The
        answer is that a warp is kept under control, never discarded -- and
        squares must stay squares there too.

        So the warp is kept and only its CANVAS changes: the facade is framed
        by four lines, two through each vanishing point, at the facade strip's
        edges (or the extent of the horizontal evidence) and at the extent of
        the vertical lines between them, and that quad goes the PC Rectangle
        way (`planar.canvas_for`: true proportions, room to drag the crop out,
        never past the photograph). Same geometry as the warp, since a quad
        whose sides meet at the two vanishing points is rectified by the same
        map up to scale. Full-resolution pixels, or None when the warp does
        not fold or there is nothing to frame."""
        if not self._rectify_active():
            return None
        key = (id(self.model), self.state_key())
        cache = getattr(self, "_fold_cache", None)
        if cache is not None and key is not None and cache[0] == key:
            return cache[1]
        q = self._ha_fold_quad_uncached()
        self._fold_cache = (key, q)
        return q

    def _ha_fold_quad_uncached(self):
        roll, pitch, f, _ = self.current_angles()
        yaw = self.current_yaw()
        H = self._build_H(self.w, self.h, f, roll, pitch, yaw,
                          max_area=self.settings.max_area_ratio)
        planned = W.plan(self.w, self.h, H, self.settings,
                         line_segs=self._plan_segs(self.w),
                         yaw=yaw, strip=self.strip)
        # Without a strip a plan that exists is taken as it is. With one, the
        # plan must also SHOW that facade: on a close, steep view (rendered
        # cube, 16 mm, 22 deg up) the plan succeeded and framed a sliver of sky
        # and street beside the wall (user: "rechte [Seite] fail").
        # A mask frames the facade like a strip does (user, 2026-10-24: "nur
        # der unmaskierte Ausschnitt ... wie ROI").
        kept_x = self._kept_source_x() if self.strip is None else None
        if planned is not None and self.strip is None and kept_x is None:
            return None
        gw = self.gray.shape[1]
        k = self.w / float(gw)
        dv = self._data_vertical_vp()
        vv = np.asarray(dv if dv is not None else self.model.vp, float)
        vh = np.asarray(self.model.horiz_vps[0], float)
        vv = np.array([vv[0] * k, vv[1] * k, vv[2]])
        vh = np.array([vh[0] * k, vh[1] * k, vh[2]])
        if self.strip is not None:
            x0, x1 = self.strip[0] * self.w, self.strip[1] * self.w
        elif kept_x is not None:
            x0, x1 = kept_x
        else:
            hs = np.asarray(getattr(self.horiz, "seg", np.zeros((0, 4))), float)
            if len(hs) == 0:
                return None
            xs = hs[:, [0, 2]].ravel() * k
            x0, x1 = float(np.percentile(xs, 2)), float(np.percentile(xs, 98))
            # A vanishing point inside the span means the lines on its two
            # sides belong to two walls receding towards it; lines through a
            # point inside the quad cross over (Marianske Hory: VP at x=137 of
            # 1024 gave a 1970x11665 canvas). Keep the side with more lines.
            if abs(vh[2]) > 1e-12:
                vx = vh[0] / vh[2]
                if x0 < vx < x1:
                    mid = (hs[:, 0] + hs[:, 2]) * 0.5 * k
                    gap = 0.05 * self.w
                    if (mid > vx).sum() >= (mid < vx).sum():
                        x0 = max(x0, vx + gap)
                    else:
                        x1 = min(x1, vx - gap)
        # Crop in time: a wall drawn right up to its vanishing point needs an
        # unbounded magnification there. The near end stays at least a third
        # of the far end's distance from the vanishing point, so no part of
        # the facade is foreshortened more than FOLD_MAX_FORESHORTEN times
        # the rest.
        if abs(vh[2]) > 1e-12:
            vx = vh[0] / vh[2]
            if vx <= x0:
                x0 = max(x0, vx + (x1 - vx) / self.FOLD_MAX_FORESHORTEN)
            elif vx >= x1:
                x1 = min(x1, vx - (vx - x0) / self.FOLD_MAX_FORESHORTEN)
        vs = np.asarray(getattr(self.vert, "seg", np.zeros((0, 4))), float)
        if len(vs):
            mid = (vs[:, 0] + vs[:, 2]) * 0.5 * k
            inside = (mid >= x0) & (mid <= x1)
            keep = self._kept_mask() if kept_x is not None else None
            if keep is not None:
                # Only lines on what the mask keeps set the height: a near
                # column beside the facade is inside the x range but masked.
                gh_, gw_ = keep.shape
                mx = np.clip(((vs[:, 0] + vs[:, 2]) * 0.5).astype(int), 0, gw_ - 1)
                my = np.clip(((vs[:, 1] + vs[:, 3]) * 0.5).astype(int), 0, gh_ - 1)
                on = inside & keep[my, mx]
                inside = on if on.any() else inside
            sel = vs[inside]
            sel = sel if len(sel) else vs
            # Each end slid along its own line to the horizontal vanishing
            # point onto the strip's middle: the top and bottom edges then hug
            # the wall. Taking the raw extremes put the tallest (nearest) edge's
            # height at the middle, and the quad overshot the facade threefold.
            xc = (x0 + x1) / 2.0
            pts = np.c_[sel[:, [0, 2]].ravel() * k, sel[:, [1, 3]].ravel() * k,
                        np.ones(2 * len(sel))]
            at = np.cross(np.cross(np.broadcast_to(vh, pts.shape), pts),
                          np.array([1.0, 0.0, -xc]))
            ok = np.abs(at[:, 2]) > 1e-12
            ys = at[ok, 1] / at[ok, 2] if ok.any() else pts[:, 1]
            y0, y1 = float(ys.min()), float(ys.max())
        else:
            y0, y1 = 0.1 * self.h, 0.9 * self.h
        if x1 - x0 < 8 or y1 - y0 < 8:
            return None
        yc, xc = (y0 + y1) / 2.0, (x0 + x1) / 2.0
        left = np.cross(vv, [x0, yc, 1.0]); right = np.cross(vv, [x1, yc, 1.0])
        top = np.cross(vh, [xc, y0, 1.0]); bottom = np.cross(vh, [xc, y1, 1.0])
        corners = []
        for a, b in ((top, left), (top, right), (bottom, right), (bottom, left)):
            p = np.cross(a, b)
            if abs(p[2]) < 1e-12:
                return None
            corners.append(p[:2] / p[2])
        q = np.array(corners)
        if not np.all(np.isfinite(q)) or abs(P.quad_area(q)) < 64:
            return None
        # Only a convex quad (TL, TR, BR, BL turning one way) is a facade.
        e = np.roll(q, -1, axis=0) - q
        turn = e[:, 0] * np.roll(e, -1, axis=0)[:, 1] - e[:, 1] * np.roll(e, -1, axis=0)[:, 0]
        if not (np.all(turn > 0) or np.all(turn < 0)):
            return None
        if planned is not None:
            H_total, ow, oh = planned[0], planned[1], planned[2]
            p = np.c_[q, np.ones(4)] @ np.asarray(H_total, float).T
            if np.all(p[:, 2] * p[0, 2] > 0):
                p = p[:, :2] / p[:, 2:3]
                tol = self.FOLD_FRAME_TOL
                if (p[:, 0].min() >= -tol * ow and p[:, 0].max() <= (1 + tol) * ow
                        and p[:, 1].min() >= -tol * oh and p[:, 1].max() <= (1 + tol) * oh):
                    return None
        return q

    def drop_planar_for(self, reason: str) -> bool:
        """Put the quad away because something incompatible arrived.

        A placed quad REPLACES the rotation path entirely -- `render_before`
        does not go through roll, pitch or yaw at all -- so it cannot quietly
        coexist with the things that feed that path. Marker lines, horizontal
        auto and the facade strip are all instructions about a correction the
        quad is not using, and leaving both in force meant the newer one
        appeared to do nothing (2026-09-20, user: "marker oder planar").

        Returns True when there was something to drop, so the caller can say so
        instead of a control silently going dead.
        """
        if not self.planar_quad:
            return False
        self.clear_planar()
        self.planar_dropped_because = reason
        return True

    def set_planar_point(self, i: int, x: float, y: float) -> None:
        """Place or move corner ``i`` of the quad (full-resolution pixels)."""
        while len(self.planar_quad) <= i:
            self.planar_quad.append((0.0, 0.0))
        self.planar_quad[i] = (float(x), float(y))
        if len(self.planar_quad) >= 4 and (self.crop_rect is None
                                            or getattr(self, "crop_is_auto", False)):
            self._planar_default_crop()

    def _planar_default_crop(self) -> bool:
        """Crop the rectified canvas to the quad + ``planar.CROP_MARGIN``.

        The canvas keeps more (``planar.KEEP_MARGIN``) so the crop can be
        dragged outward; this is only where it starts. Marked automatic, so
        moving a corner moves it along and a hand-drawn crop is left alone."""
        try:
            t = self.planar_homography()
        except ValueError:
            return False
        if t is None:
            return False
        _H, cw, ch = t
        x0, y0, x1, y1 = self._planar_box
        # A mask makes the crop box: what it keeps + STRIP_CROP_MARGIN.
        kept = self._kept_box(_H, cw, ch) if self.strip is None else None
        if kept is not None:
            x0, y0, x1, y1 = kept[0], kept[1], kept[2] - 1, kept[3] - 1
        self._crop_edges["left"] = (True, max(0.0, x0 / cw))
        self._crop_edges["top"] = (True, max(0.0, y0 / ch))
        self._crop_edges["right"] = (True, max(0.0, 1.0 - (x1 + 1) / cw))
        self._crop_edges["bottom"] = (True, max(0.0, 1.0 - (y1 + 1) / ch))
        self.crop_is_auto = True
        return True

    def pick_planar_corner(self, x: float, y: float, display_scale: float):
        """Index of the corner under a full-resolution point, or None.

        The radius is a constant number of *screen* pixels, so the target size
        does not depend on how far the photograph is zoomed."""
        r = 14.0 / max(display_scale, 1e-9)
        for i, (px, py) in enumerate(self.planar_quad):
            if math.hypot(px - x, py - y) <= r:
                return i
        return None

    def clear_planar(self) -> None:
        self.planar_quad = []

    def planar_homography(self):
        """``(H, w, h)`` for the current quad, or None until four corners exist.

        Raises on a degenerate quad rather than returning a homography that
        would flatten the photograph into a sliver."""
        if self.active_quad() is None:
            return None
        # True proportions need a focal: the quad's own, blended with the
        # estimator's (planar.target_size). A failed estimate only costs the
        # blend, never the rectangle.
        try:
            focal = self.current_angles()[2]
        except Exception:
            focal = None
        H, cw, ch, self._planar_box = P.canvas_for(
            self.active_quad(), self.w, self.h,
            center=(self.w / 2.0, self.h / 2.0), focal=focal,
            long_edge=max(self.w, self.h))
        return H, cw, ch

    def planar_rectified(self, max_edge=None):
        """The rectified frame from the four corners, un-filled.

        ``max_edge`` renders from a reduced copy -- a full-resolution warp per
        corner drag is unusable on a 24 MP frame, and the homography scales
        with the image, so warping small and calling it a preview is exact up
        to the preview's own resolution.  Saving passes nothing and gets the
        full-size result."""
        r = self._planar_warp(max_edge)
        return None if r is None else r[0]

    def _planar_warp(self, max_edge=None):
        """``(out, Hs, sw, sh, ow, oh, s)``: the warp, the homography from the
        (possibly reduced) source of ``sw x sh`` onto the ``ow x oh`` canvas,
        and the reduction ``s``; None until four corners exist."""
        t = self.planar_homography()
        if t is None:
            return None
        H, w, h = t
        if max_edge and max(w, h) > max_edge:
            s = float(max_edge) / max(w, h)
            S = np.array([[s, 0.0, 0.0], [0.0, s, 0.0], [0.0, 0.0, 1.0]])
            # S H S^-1, not H S.  `H` maps full-resolution source pixels to the
            # full-size canvas; here both ends are scaled by `s`, so the source
            # has to be scaled *back up* before H sees it and the result scaled
            # down again -- which is a similarity conjugation, not a product.
            # `H @ S` scales twice and returns a different picture entirely:
            # measured 154 grey levels of mean difference against the full-size
            # warp, where the conjugation gives 8.5 (resampling noise). It made
            # the corner drag preview show something the saved file would not,
            # the same class of failure the always-live crop is built to avoid.
            img = cv2.resize(self.bgr,
                             (max(1, int(self.w * s)), max(1, int(self.h * s))),
                             interpolation=cv2.INTER_AREA)
            Hs = S @ H @ np.linalg.inv(S)
            ow, oh = max(1, int(w * s)), max(1, int(h * s))
            sh, sw = img.shape[:2]
            return cv2.warpPerspective(img, Hs, (ow, oh)), Hs, sw, sh, ow, oh, s
        return cv2.warpPerspective(self.bgr, H, (w, h)), H, self.w, self.h, w, h, 1.0

    def save_planar(self, dst_path: str, on_stage=None) -> str:
        """Write the rectified view.

        The canvas keeps ``planar.KEEP_MARGIN`` of picture around the quad and
        the crop (by default the quad + ``planar.CROP_MARGIN``) is cut FIRST,
        so the fill only ever works on what is written. Whatever of that lies
        outside the photograph is filled like the rotation path's band."""
        self.last_fill_note = ""
        r = self._planar_warp(None)
        if r is None:
            raise ValueError("planar correction needs four corners")
        out, H, _sw, _sh, ow, oh, _s = r
        mpx = ow * oh / 1e6
        if on_stage is not None:
            on_stage("warping", mpx)
        fill = getattr(self.settings, "fill", "none")
        hole = W.filled_region(H, self.w, self.h, ow, oh)             if fill not in ("", "none") else None
        out = self._apply_crop(out)
        if hole is not None:
            hole = self._apply_crop(hole)
            if hole.any():
                from . import inpaint as FILL
                if on_stage is not None:
                    on_stage("fill:" + str(fill), mpx)
                out, self.last_fill_note = FILL.fill(
                    out, hole, self.settings, on_step=getattr(self, "on_fill_step", None))
        if on_stage is not None:
            on_stage("writing", mpx)
        os.makedirs(os.path.dirname(os.path.abspath(dst_path)) or ".", exist_ok=True)
        IO.save(dst_path, out, self.src, self.settings)
        return dst_path
