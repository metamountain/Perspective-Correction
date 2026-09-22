"""Interactive review state: manual correction and line editing.

Kept deliberately free of any GUI toolkit.  Everything the manual mode does --
re-fit from a hand-picked subset of lines, nudge the angles by hand, render a
before/after pair -- is a pure function of state here, so it can be tested
headlessly and driven from Tkinter, a notebook or a future web front end alike.

The interaction the brief asked for is "Slider zur manuellen Korrektur oder
Auswahl von Linien zur Bestaetigung bzw. Loeschung".  Both are supported and
they compose: disabling the roof rafters that the detector mistook for
verticals re-fits the model, and the sliders then start from that better fit
instead of from the bad one.

A third way in was added after reading how Hugin does it: a **vertical control
line**, Hugin's ``t2`` control point.  The user clicks two points on something
they know is vertical in the world -- a door jamb, a downpipe, a building corner
-- and that assertion outranks the detector entirely.  It is the answer to the
case no amount of striking-out fixes: when the lines the detector found are all
real but all belong to the wrong plane, there is nothing to delete, only
something to state.
"""
from __future__ import annotations

import math
import os

import cv2
import numpy as np

from . import geometry as G
from . import imageio as IO
from . import lines as L
from . import masks as MK
from . import model as M
from . import planar as P
from . import preview as PV
from . import warp as W

AUTO, MANUAL = "auto", "manual"


def _drop_touching(ls, mask):
    """A LineSet without the segments that touch ``mask``.

    Kept beside the session rather than inside it because it is the same
    operation for both pools and both kinds of line, and the point of the layer
    registry is that there is exactly one of it.
    """
    if mask is None or ls is None or not len(ls.seg):
        return ls
    keep = MK.untouched(ls.seg, mask)
    if keep.all():
        return ls
    if not keep.any():
        # A mask that swallows a whole pool is a user error, not a fit: keeping
        # nothing would make the estimator answer from noise. Say nothing and
        # keep the pool -- refit's own floors then decide.
        return ls
    return ls.subset(keep)


class ReviewSession:
    """Everything needed to review and fix one image."""

    def __init__(self, path: str, settings, image=None):
        self.path = path
        self.settings = settings
        # Interactive auto-crop threshold: the user sees the result on screen
        # and "Reset crop" undoes it, so the trim may cost more frame than the
        # batch gate allows.  This is NOT ``settings.crop_max_loss`` (which
        # governs the warp plan's pad-vs-crop decision) — it is the ceiling for
        # how much the *auto-crop button* may remove without asking.
        self.auto_crop_threshold = max(settings.crop_max_loss, 0.30)
        if image is None:
            self.src = IO.load(path)
            self.bgr = self.src.bgr
        else:
            self.src = IO.Loaded(bgr=image, exif_bytes=None, icc=None, focal_35mm=None,
                                 orientation=1, fmt="", path=path)
            self.bgr = image
        self.h, self.w = self.bgr.shape[:2]

        self.gray, self.scale = IO.analysis_gray(self.bgr, settings.detect_max_edge)
        self._small = cv2.resize(self.bgr, (self.gray.shape[1], self.gray.shape[0]),
                                 interpolation=cv2.INTER_AREA) \
            if self.bgr.shape[:2] != self.gray.shape[:2] else self.bgr
        self.detect_error = ""
        self._detect()

        self.mode = AUTO
        self.show_mask = True
        self.mask_alpha = 0.28
        self.mask_color = (60, 60, 200)  # BGR; the GUI swatch overrides this
        # Hand-painted ignore region, analysis-image resolution, or None until
        # the brush is first used.  `_paint_struck` remembers which lines this
        # paint struck out, so erasing part of it can hand exactly those back
        # without disturbing anything struck by other means.
        self.paint = None
        self._paint_struck = np.zeros(len(self.vert), dtype=bool)
        self.manual_roll = 0.0
        self.manual_pitch = 0.0
        self.manual_yaw = 0.0
        self.manual_focal_35mm = 0.0
        # Hugin-style vertical control lines, in analysis-image coordinates.
        # (N, 4) of x0, y0, x1, y1.  Two arrays: verticals drive roll/pitch,
        # horizontals drive yaw -- the same "a person stated it" evidence, split
        # by which vanishing plane the line belongs to.
        self.control_lines = np.zeros((0, 4))
        self.control_hlines = np.zeros((0, 4))
        # Manual crop, as per-edge trims in fractions of the corrected canvas.
        # The preview is rendered at a few hundred pixels and the file is saved
        # at full size, so a rectangle in pixels would mean two different things;
        # fractions mean the same thing at every scale.  Each edge is
        # (enabled: bool, frac: float) where frac is 0..0.5.
        self._crop_edges = {
            "top":    (False, 0.0),
            "bottom": (False, 0.0),
            "left":   (False, 0.0),
            "right":  (False, 0.0),
        }
        self.model = None
        # Planar (four-point) correction: the quad in FULL-resolution image
        # coordinates, ordered top-left, top-right, bottom-right, bottom-left.
        # Fewer than four points means "still being placed".  It is a separate
        # answer from the rotation path -- a general homography with shear and
        # scale, for the surface a person has pointed at (see planar.py).
        self.planar_quad = []
        self.planar_active = False
        # Horizontal-evidence region: a vertical strip (x0, x1) in ANALYSIS-
        # image pixels, or None.  In a corner view the two facades have two
        # horizontal vanishing points and the estimator takes whichever has
        # more support -- not necessarily the facade you mean to straighten.
        # The strip says "the yaw comes from the horizontals in here"; the
        # verticals stay global on purpose, because both facades share the
        # world-vertical VP and restricting them would only burn evidence.
        self.strip = None
        # Every automatic source lands here and STAYS here, separate from the
        # union, so that switching source replaces only the source and the hand
        # work underneath it survives.  Merging them into one array was what made
        # "which of these four things put that red there" unanswerable.
        self.source_mask = None
        # SAM2 click-to-select: the resulting ignore mask (True = ignore), or
        # None until computed.  The *prompts* -- box and rework points -- live
        # in the GUI, in frame fractions; the session only ever sees the result.
        self.sam_mask = None
        # One switch over the whole union (user, 2026-09-15): whatever the
        # layers ignore, keep, and keep what they ignore.  It belongs here and
        # not per source, because "invert" is a statement about the RESULT --
        # per-source flags would need an answer for what inverting two of four
        # sources means, and there isn't one.
        self.invert_mask = False
        # "mask active": whether the union acts at all, with every layer left
        # standing.  A flag here rather than a GUI trick, because the old switch
        # worked by calling `set_mask("off")` -- which only replaces the SOURCE,
        # and `_detect` re-applies the paint at the end anyway.  Measured on the
        # test asset with a brushed quarter: wash 25.0%, pitch +0.0369, 73 of 76
        # lines, byte for byte identical with the box ticked and unticked.
        self.mask_enabled = True
        self.refit()

    # -- detection -------------------------------------------------------
    def _detect(self):
        """Run the line front end.  Separate from ``__init__`` because changing
        the mask has to redo it, not just refit."""
        try:
            (_, self.vert, self.horiz, self.detector,
             self.detect_info) = L.prepare(self.gray, self.settings, self._small, self.path)
            self.detect_error = ""
        except Exception as exc:
            # a missing mask file must not take the window down; say so instead
            self.detect_error = str(exc)
            safe = self.settings.replace(mask_mode="off")
            (_, self.vert, self.horiz, self.detector,
             self.detect_info) = L.prepare(self.gray, safe, self._small, self.path)
        # The automatic source keeps its own slot.  `prepare` hands its mask
        # back in detect_info; taking a copy here is what lets a later source
        # switch replace only the source while paint and SAM stay untouched.
        self.source_mask = (self.detect_info or {}).get("mask")
        # per-line manual state: True = may be used, False = struck out by the user
        self.enabled = np.ones(len(self.vert), dtype=bool)
        # A re-detect rebuilds the mask from the automatic sources, so the
        # hand-painted region has to be laid back over it -- otherwise switching
        # detector or mask source silently discards what the user painted.
        self._apply_paint()
        # SAM's selection is hand work too.  Re-detect used to re-apply the
        # paint and forget this, so picking a different mask source kept
        # what you brushed and silently dropped what you selected -- two
        # manual layers treated alike on arrival and differently on the
        # next automatic change.  ``sam_mask`` is not set until after the first
        # _detect in __init__, so guard that one call.
        if getattr(self, "sam_mask", None) is not None:
            self._apply_sam_mask()

    def set_mask(self, mode, path="", invert=None):
        """Switch the region mask and re-detect.

        Belongs here rather than only in the batch settings: the point of a mask
        is that you can see what it removed, and you can only judge that while
        looking at the picture."""
        self.settings = self.settings.replace(
            mask_mode=mode, mask_file=path or self.settings.mask_file,
            mask_invert=self.settings.mask_invert if invert is None else invert)
        self._detect()
        self.refit()
        return self.detect_error

    def set_detector(self, name):
        """Switch the line detector and re-detect, for the same reason as
        ``set_mask``: which detector was right for a photograph is a question
        about that photograph, and the only place to answer it is in front of
        the lines it found.  Errors -- a missing checkout, missing weights --
        come back as a string rather than an exception, because a detector that
        will not load must not take the review window down mid-batch.
        """
        before = self.settings.detector
        self.settings = self.settings.replace(detector=name)
        try:
            self._detect()
        except Exception as exc:
            self.settings = self.settings.replace(detector=before)
            self._detect()
            self.refit()
            return str(exc)
        self.refit()
        return self.detect_error

    @property
    def mask_active(self):
        info = getattr(self, "detect_info", None) or {}
        return info.get("mask") is not None

    # -- fitting ---------------------------------------------------------
    # One ignore mask, assembled from named layers.  Every contributor is the
    # same kind of thing -- a boolean array at analysis resolution plus which
    # line pools it speaks for -- so there is one union and one rule, instead of
    # four mechanisms that each had to be remembered separately.  They ADD: a
    # pixel is ignored when any layer ignores it, because each layer is an
    # independent claim and ignoring twice costs nothing.
    #
    # `strip` is the only layer that does not speak for both pools.  That is a
    # measurement, not a carve-out: a strip that also cut verticals left the
    # angles alone (pitch within 0.4 deg on every asset tried) and dropped
    # confidence by about 0.11 every single time, because confidence is
    # multiplicative and counts verticals.  It would refuse photographs that are
    # corrected today and buy nothing for it.  One string here flips that back.
    LAYER_SCOPE = (("source", "vh"), ("paint", "vh"),
                   ("sam", "vh"), ("strip", "h"))

    def _strip_layer(self):
        """The facade strip as an ignore layer: everything outside it.

        The strip used to be a filter on line midpoints, which made it the one
        masking idea with its own mechanism, its own place in the pipeline and
        its own failure mode.  As a layer it is just another claim about which
        pixels do not count.
        """
        if self.strip is None:
            return None
        gh, gw = self.gray.shape[:2]
        # Fractions in, analysis pixels out. The conversion lives at the two
        # points that need pixels and nowhere else, so there is never a stored
        # number whose frame has to be guessed.
        x0 = int(round(self.strip[0] * gw))
        x1 = int(round(self.strip[1] * gw))
        x0, x1 = max(0, min(x0, gw)), max(0, min(x1, gw))
        if x1 <= x0:
            return None
        out = np.ones((gh, gw), dtype=bool)
        out[:, x0:x1] = False
        return out

    # The registry above names the layers; these are the attributes they live
    # in.  Spelled out rather than guessed from the name, because guessing is
    # what broke it: `layer()` mapped only "sam" and fell through to
    # `getattr(self, "source")`, which does not exist -- so `layer("source")`
    # was None for every photograph ever opened and the automatic mask (file,
    # BiRefNet, gdino) was in the union in name only.
    LAYER_ATTR = {"source": "source_mask", "sam": "sam_mask", "paint": "paint"}

    def layer(self, name):
        """One ignore layer by name, or None when it is not in force."""
        if name == "strip":
            return self._strip_layer()
        return getattr(self, self.LAYER_ATTR[name], None)

    def ignore_mask(self, pool="vh"):
        """Union of every layer that speaks for ``pool``; None when empty.

        ``pool`` is "v", "h", or "vh" for everything -- what the red wash shows.
        """
        if not getattr(self, "mask_enabled", True):
            return None            # the switch means the union stops acting
        out = None
        for name, scope in self.LAYER_SCOPE:
            if not any(p in scope for p in pool):
                continue
            arr = self.layer(name)
            if arr is None or not arr.any():
                continue
            out = arr.copy() if out is None else np.logical_or(out, arr)
        if out is not None and getattr(self, "invert_mask", False):
            out = ~out
        return out

    def _refresh_mask(self):
        """Recompute the shown mask from the layers.  One place, one rule."""
        if self.detect_info is not None:
            self.detect_info["mask"] = self.ignore_mask("vh")

    def refit(self):
        """Re-run the estimator over the currently enabled lines.

        Vertical control lines, when there are enough of them, **replace** the
        detected pool rather than joining it.  That is Hugin's semantics and it
        is the only reading that makes them worth having: a user who marks two
        door jambs is not adding two votes to three hundred, they are saying the
        three hundred were beside the point.  Adding them with a large weight
        instead would mean choosing how large, and the answer would be "large
        enough to win", which is the same thing with a fudge factor in it.
        """
        gh, gw = self.gray.shape[:2]
        settings = self.settings
        drew_v = len(self.control_lines) >= 2
        drew_h = len(self.control_hlines) >= 2
        if drew_v:
            vert = L.LineSet(self.control_lines)
            # two lines already determine a vanishing point, and the floor of
            # four exists to keep the detector from fitting noise -- which is
            # not what these are
            settings = settings.replace(min_vertical_lines=2)
        else:
            vert = self.vert.subset(self.enabled)
        if drew_h:
            # A person drew these; like the verticals they replace the detected
            # pool rather than joining it, and the detector's support floor is
            # dropped for them -- refusing a little hand-stated evidence because
            # there is little of it is right for a detector and wrong here.
            horiz = L.LineSet(self.control_hlines)
            settings = settings.replace(min_horizontal_support=0.0,
                                        correct_horizontal=True)
        else:
            # The strip is a mask layer now, applied below with every other
            # one, so there is nothing to filter here.  It used to be the single
            # masking idea with its own mechanism and its own place in the
            # pipeline, which is exactly the chaos the registry removes.
            horiz = self.horiz
        # An annotator that TOUCHES the mask is not evidence -- for the
        # DETECTOR. The mask is aimed at what a detector finds and a person
        # would not: sky, branches, parked cars. A hand-drawn control line is
        # the opposite case. Somebody could see the mask while they drew across
        # it and drew anyway, which is a statement, not an accident; dropping it
        # discards the more reliable of the two. The same reasoning `would_skip`
        # already applies to the confidence gate -- refusing evidence for being
        # scarce is right for a detector and wrong for a person.
        # (2026-09-20, user: "maske und marker sollten sich vertragen".)
        if not drew_v:
            vert = _drop_touching(vert, self.ignore_mask("v"))
        if not drew_h:
            horiz = _drop_touching(horiz, self.ignore_mask("h"))
        exif_px = IO.focal_px_from_exif(self.src, self.w, self.h) \
            if self.settings.use_exif_focal else None
        # A strip means one facade has been pointed at, which is the precondition
        # for building the rotation from that facade's own two vanishing points.
        m = M.estimate(vert, horiz, gw, gh, settings,
                       exif_px * self.scale if exif_px else None,
                       single_facade=self.strip is not None)
        if m.f:
            m.f = m.f / self.scale
            # Sanity clamp: focal below 5% of frame width is unphysical
            # (extreme fisheye).  Fall back to the 28 mm prior.
            f_min = 0.05 * max(self.w, self.h)
            if m.f < f_min:
                m.f = M.focal_px_from_35mm(
                    self.settings.default_focal_35mm, self.w, self.h)
                m.f_source = "clamped"
        # map the inlier mask back onto the full line list, so the overlay can
        # distinguish "not an inlier" from "struck out by the user"
        full = np.zeros(len(self.vert), dtype=bool)
        if (not self.control_active
                and m.vert_inliers is not None
                and len(m.vert_inliers) == int(self.enabled.sum())):
            full[np.flatnonzero(self.enabled)] = m.vert_inliers
        m.vert_inliers = full
        self.model = m
        if self.mode == AUTO:
            self.manual_roll, self.manual_pitch, self.manual_yaw = m.roll, m.pitch, m.yaw
            self.manual_focal_35mm = M.focal_35mm_from_px(m.f, self.w, self.h) if m.f else 0.0
        return m

    # -- manual controls -------------------------------------------------
    def set_manual(self, roll_deg=None, pitch_deg=None, yaw_deg=None,
                   focal_35mm=None):
        self.mode = MANUAL
        if roll_deg is not None:
            self.manual_roll = math.radians(roll_deg)
        if pitch_deg is not None:
            self.manual_pitch = math.radians(pitch_deg)
        if yaw_deg is not None:
            self.manual_yaw = math.radians(yaw_deg)
        if focal_35mm is not None:
            self.manual_focal_35mm = float(focal_35mm)

    def use_auto_angles(self):
        """Go back to the angles the estimator found, and keep everything else.

        Distinct from ``reset_to_auto`` on purpose.  Reset throws away every
        judgement the user made -- struck-out lines, vertical control lines, a
        hand-drawn crop -- which is the right thing after a wrong turn and much
        too much after a mis-dragged slider.  Wanting the found angles back is
        the common case and should not cost the rest of the work.
        """
        self.mode = AUTO
        if self.model is not None:
            self.manual_roll, self.manual_pitch, self.manual_yaw = (
                self.model.roll, self.model.pitch, self.model.yaw)
            self.manual_focal_35mm = (M.focal_35mm_from_px(self.model.f, self.w, self.h)
                                      if self.model.f else 0.0)
        return self.model

    def reset_to_auto(self):
        self.mode = AUTO
        self.enabled[:] = True
        self.control_lines = np.zeros((0, 4))
        self.control_hlines = np.zeros((0, 4))
        self._crop_edges = {k: (False, 0.0) for k in self._crop_edges}
        self.strip = None
        self.refit()

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
        roll, pitch, f, _ = self.current_angles()
        yaw = self.current_yaw()
        if abs(roll) < 1e-9 and abs(pitch) < 1e-9 and abs(yaw) < 1e-9:
            return False
        H = W.build(self.w, self.h, f, roll, pitch, yaw, max_area=self.settings.max_area_ratio)
        _segs = None
        if len(self.vert) or len(self.horiz):
            _parts = [x.seg for x in (self.vert, self.horiz) if len(x)]
            if _parts:
                _segs = np.concatenate(_parts, axis=0)
        planned = W.plan(self.w, self.h, H, self.settings, line_segs=_segs, yaw=yaw,
                         strip=self.strip)
        if planned is None:
            return False
        H_total, ow, oh, _, _ = planned
        quad = W.warped_quad(H_total, self.w, self.h)
        rect = W.max_inscribed_rect(quad, self.w / float(self.h))
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
        return self.set_crop_rect(x0, y0, x1, y1, ow, oh)

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
        if self.crop_loss() > self.auto_crop_threshold:
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

    @property
    def control_active(self):
        """True when the user's own verticals are driving the fit."""
        return len(self.control_lines) >= 2

    MIN_CONTROL_LENGTH_FRAC = 0.08

    def _ctrl_attr(self, kind):
        """Which array a control line of ``kind`` lives in."""
        return "control_lines" if kind == "v" else "control_hlines"

    def add_control_line(self, x0, y0, x1, y1, display_scale: float = 1.0,
                         kind: str = "v"):
        """Assert that this segment is level in the world -- Hugin's ``t2`` (a
        vertical) or ``h2`` (a horizontal), chosen by ``kind``.

        Two points on one structure, and Hugin's own advice is to put them "as
        far apart from each other as possible": the direction of a short segment
        is poorly conditioned, and the whole point of drawing it by hand is that
        it should be better evidence than anything the detector found.  A
        segment shorter than ``MIN_CONTROL_LENGTH_FRAC`` of the short edge is
        refused rather than quietly accepted, because a mis-click that lands two
        points near each other would otherwise steer the entire fit.

        Returns the new index, or ``None`` if it was refused.
        """
        inv = self.scale / max(display_scale, 1e-9)
        seg = np.array([[x0 * inv, y0 * inv, x1 * inv, y1 * inv]], dtype=float)
        gh, gw = self.gray.shape[:2]
        if float(np.hypot(seg[0, 2] - seg[0, 0], seg[0, 3] - seg[0, 1])) < \
                self.MIN_CONTROL_LENGTH_FRAC * min(gw, gh):
            return None
        attr = self._ctrl_attr(kind)
        setattr(self, attr, np.vstack([getattr(self, attr), seg]))
        self.refit()
        return len(getattr(self, attr)) - 1

    def remove_control_line(self, index: int, kind: str = "v"):
        attr = self._ctrl_attr(kind)
        arr = getattr(self, attr)
        if not (0 <= index < len(arr)):
            return False
        setattr(self, attr, np.delete(arr, index, axis=0))
        self.refit()
        return True

    def move_control_line_endpoint(self, index: int, endpoint_idx: int,
                                   x: float, y: float,
                                   display_scale: float = 1.0, kind: str = "v"):
        """Move one endpoint of an existing control line to (x, y)."""
        attr = self._ctrl_attr(kind)
        arr = getattr(self, attr)
        if not (0 <= index < len(arr)):
            return False
        inv = self.scale / max(display_scale, 1e-9)
        col = endpoint_idx * 2
        arr[index, col] = x * inv
        arr[index, col + 1] = y * inv
        setattr(self, attr, arr)
        self.refit()
        return True

    def pick_control_line_endpoint(self, x: float, y: float,
                                   display_scale: float = 1.0,
                                   radius: float = 10.0, kind: str = "v"):
        """(line_index, endpoint_idx) of the nearest endpoint within radius, or None."""
        arr = getattr(self, self._ctrl_attr(kind))
        if len(arr) == 0:
            return None
        inv = self.scale / max(display_scale, 1e-9)
        px, py = x * inv, y * inv
        best_dist = radius * inv
        best = None
        for i in range(len(arr)):
            for e in (0, 1):
                dx = arr[i, e * 2] - px
                dy = arr[i, e * 2 + 1] - py
                d = float(np.hypot(dx, dy))
                if d < best_dist:
                    best_dist = d
                    best = (i, e)
        return best

    def clear_control_lines(self, kind: str = "v"):
        attr = self._ctrl_attr(kind)
        had = len(getattr(self, attr))
        setattr(self, attr, np.zeros((0, 4)))
        if had:
            self.refit()
        return had

    def pick_control_line(self, x: float, y: float, display_scale: float = 1.0,
                          radius: float = 12.0, kind: str = "v"):
        """Index of the control line nearest a click, or ``None``."""
        return self._nearest(getattr(self, self._ctrl_attr(kind)), x, y,
                             display_scale, radius)

    def control_lines_for_display(self, display_scale: float = 1.0, kind: str = "v"):
        """The control lines in displayed-image pixels, for drawing."""
        arr = getattr(self, self._ctrl_attr(kind))
        if len(arr) == 0:
            return np.zeros((0, 4))
        return arr * (display_scale / max(self.scale, 1e-9))

    def _apply_crop(self, img):
        """Cut the manual rectangle out of a rendered result, at any size."""
        if self.crop_rect is None:
            return img
        h, w = img.shape[:2]
        x0, y0, x1, y1 = self.crop_rect
        a, b = int(round(x0 * w)), int(round(x1 * w))
        c, d = int(round(y0 * h)), int(round(y1 * h))
        if b - a < 8 or d - c < 8:
            return img
        return img[c:d, a:b]

    def toggle_line(self, index: int):
        if 0 <= index < len(self.enabled):
            self.enabled[index] = not self.enabled[index]
            self.refit()
            return True
        return False

    def disable_lines_by_angle(self, min_deg: float):
        """Strike out every candidate leaning more than ``min_deg`` off vertical.

        The one-click version of "delete the slanted ones": roof rafters and
        gable edges are what most often drag a facade fit off, and they are
        exactly the lines with the largest lean.
        """
        lean = np.degrees(self.vert.angle_to_vert)
        changed = (lean > min_deg) & self.enabled
        self.enabled[changed] = False
        if changed.any():
            self.refit()
        return int(changed.sum())

    def pick_line(self, x: float, y: float, display_scale: float = 1.0, radius: float = 12.0):
        """Index of the candidate nearest to a click, or ``None``.

        ``x``/``y`` are in displayed-image pixels; ``display_scale`` is
        displayed / original.  Distance is to the segment, not to its infinite
        line, so clicking above a short window mullion does not select it.
        """
        return self._nearest(self.vert.seg, x, y, display_scale, radius)

    def _nearest(self, seg, x, y, display_scale, radius):
        if len(seg) == 0:
            return None
        inv = self.scale / max(display_scale, 1e-9)
        p = np.array([x * inv, y * inv])
        a, b = seg[:, :2], seg[:, 2:]
        ab = b - a
        t = np.clip(np.sum((p - a) * ab, axis=1) / np.maximum(np.sum(ab * ab, axis=1), 1e-9), 0, 1)
        closest = a + ab * t[:, None]
        d = np.linalg.norm(closest - p, axis=1)
        i = int(np.argmin(d))
        return i if d[i] <= radius * inv else None

    def paint_ignore(self, pts, display_scale: float = 1.0, radius: float = 24.0,
                     erase: bool = False):
        """Paint (or erase) a region of the ignore mask by hand.

        The manual answer to a segmenter that picked the wrong subject: a stroke
        across the parked car says "that is not the building" outright, instead
        of arguing with a text prompt about it.

        Unlike every automatic source this is a *decision*, so it deliberately
        skips the two heuristics that second-guess a computed mask --
        ``protect_structure``, which hands long straight lines back, and
        ``credible``, which can refuse a mask wholesale.  What you paint is
        ignored; what you erase comes back.

        Returns the share of the frame painted, so the caller can say so.
        """
        if not pts:
            return float(self.paint.mean()) if self.paint is not None else 0.0
        h, w = self.gray.shape[:2]
        if self.paint is None:
            self.paint = np.zeros((h, w), dtype=bool)
        inv = self.scale / max(display_scale, 1e-9)
        r = max(1, int(round(radius * inv)))
        buf = self.paint.astype(np.uint8)
        for x, y in pts:
            cv2.circle(buf, (int(round(x * inv)), int(round(y * inv))), r,
                       0 if erase else 1, -1)
        self.paint = buf.astype(bool)
        self._apply_paint()
        self.refit()
        return float(self.paint.mean())

    def _apply_paint(self):
        """Merge the painted region into the shown mask and strike what it covers.

        Re-derived from scratch each time rather than accumulated, so erasing is
        just painting with a zero: the lines this paint had struck are handed
        back first, then whatever the current region covers is struck again.
        """
        # getattr: `_detect` runs once from `__init__` before these exist
        paint = getattr(self, "paint", None)
        self._paint_struck = getattr(self, "_paint_struck", np.zeros(0, dtype=bool))
        if len(self._paint_struck) == len(self.enabled):
            self.enabled[self._paint_struck] = True     # release the old claim
        self._paint_struck = np.zeros(len(self.vert), dtype=bool)
        # The switch suppresses the union, so it must suppress the strikes too:
        # the release above has already run, which is what "off without clearing
        # the painted region" means -- the paint stays, it stops acting.
        # No `_refresh_mask` on this path: the first `_detect` runs from
        # `__init__` before `self.strip` exists, and the strip layer reads it.
        # The callers that can reach here with a live session refresh their own.
        if paint is None or not paint.any() or not getattr(self, "mask_enabled", True):
            return
        self._refresh_mask()
        seg = self.vert.seg
        if len(seg):
            ph, pw = paint.shape[:2]

            def inside(xs, ys):
                xi = np.clip(np.round(xs).astype(int), 0, pw - 1)
                yi = np.clip(np.round(ys).astype(int), 0, ph - 1)
                return paint[yi, xi]

            # Both endpoints, matching `masks.drop_by_endpoints`: a line that
            # merely crosses the painted edge still has real evidence outside it.
            hit = inside(seg[:, 0], seg[:, 1]) & inside(seg[:, 2], seg[:, 3])
            self._paint_struck = hit
            self.enabled[hit] = False

    def clear_mask(self):
        """Throw the hand-drawn mask away and give back the lines it struck.

        Here rather than in the window because the release rule lives here:
        `_apply_paint` hands the old claim back before it makes a new one, and
        the GUI's own version of this zeroed `_paint_struck` by hand without
        that line.  Measured on the test asset: brush the top half, twenty of
        seventy-six lines go, press Clear Mask -- and fifty-six stayed, with the
        record of which twenty they were thrown away, so nothing could ever hand
        them back.  The red went; the correction did not move.

        The automatic source is NOT touched: the button says painted mask and
        SAM selection, and the source has its own control beside it.
        """
        had = self.paint is not None or self.sam_mask is not None
        self.paint = None
        self.sam_mask = None
        self._apply_paint()          # releases, then returns on `paint is None`
        self._refresh_mask()
        self.refit()
        return had

    # -- SAM2 click-to-select --------------------------------------------
    def apply_sam_mask(self, ignore: np.ndarray):
        """Install a SAM2-produced ignore mask (True = ignore) and refit.

        It is merged into ``detect_info["mask"]`` the same way the paint brush
        does, so the two sources compose: a pixel is ignored when either the
        paint or the SAM segment says so.

        ``ignore`` may arrive at **either** resolution and is resized here.
        That is not politeness -- SAM segments the file, so every real caller
        holds a full-resolution mask, while `paint` and `detect_info["mask"]`
        are analysis-res.  Requiring the caller to convert is what left this
        method unreachable: the GUI computed the mask and then kept it to
        itself rather than hand over the wrong shape.
        """
        if ignore is not None:
            gh, gw = self.gray.shape[:2]
            if ignore.shape[:2] != (gh, gw):
                ignore = cv2.resize(ignore.astype(np.uint8), (gw, gh),
                                    interpolation=cv2.INTER_NEAREST).astype(bool)
        self.sam_mask = ignore
        self._apply_sam_mask()
        self.refit()

    def _apply_sam_mask(self):
        """Recompute the shown mask now that the SAM layer changed.

        This was eight branches enumerating which of three arrays were present
        and OR-ing the live ones in the right order -- and it still had to know,
        at each branch, what "removing the SAM mask" should leave behind.  The
        registry answers all of that by construction: ask for the union, get the
        union.  Adding a fifth source now costs one line in `LAYER_SCOPE`.
        """
        self._refresh_mask()

    # -- current correction ----------------------------------------------
    def current_angles(self):
        """``(roll, pitch, focal_px, clamped)`` actually in force.

        Four values, not the three this used to name -- the fourth says whether
        `warp.limit` hit a cap. Every return path in the body has four.
        """
        if len(self.control_lines):
            # The DRAWN LINE is the switch (2026-09-20, user-directed). There
            # used to be an "h-marker" checkbox in the control column that armed
            # this, while the lines it consumed were drawn with a tool on the
            # other side of the window -- a control whose precondition lived
            # somewhere else, which could refuse a click and then untick itself.
            # A line somebody drew is already the decision; nothing else has to
            # be switched on to mean it.
            # Roll/pitch from the vertical mark's bearing: no VP, no model.
            f = (self.model.f if self.model and self.model.f
                 else M.focal_px_from_35mm(self.settings.default_focal_35mm,
                                           self.w, self.h))
            roll, pitch = self._marker_roll_pitch(f)
            return roll, pitch, f, False
        if self.mode == MANUAL:
            roll, pitch = self.manual_roll, self.manual_pitch
            f35 = self.manual_focal_35mm
            f = M.focal_px_from_35mm(f35, self.w, self.h) if f35 > 0 else (
                self.model.f if self.model and self.model.f
                else M.focal_px_from_35mm(self.settings.default_focal_35mm,
                                          self.w, self.h))
            return roll, pitch, f, False
        if self.model is None or not self.model.f:
            return 0.0, 0.0, M.focal_px_from_35mm(self.settings.default_focal_35mm,
                                                  self.w, self.h), False
        guessed = self.model.f_source in ("default", "prior", "none", "refined")
        roll, pitch, _yaw, clamped = W.limit(self.model.roll, self.model.pitch,
                                             self.settings,
                                             focal_is_a_guess=guessed)
        return roll, pitch, self.model.f, clamped

    def marker_yaw(self):
        """Yaw from the horizontal control lines, or ``None`` when none exist.

        Each drawn segment gives a direction in camera coordinates; un-rotating
        it by the roll and pitch in force turns that into a world bearing, and
        the yaw that sends the bearing onto the x-axis is its ``atan2``. Several
        lines are averaged the wrap-aware way -- as vectors, because the mean of
        179 and -179 degrees is not zero.

        This lived in the GUI, computed once when a checkbox was ticked and
        cached on the session. It is a property of the lines, so it is computed
        from the lines, and a line added or deleted changes the answer without
        anything having to remember to recompute it.
        """
        # len(), not truthiness: these pools are arrays, and the truth
        # value of an empty array is a ValueError rather than False.
        if len(self.control_hlines) == 0:
            return None
        f = (self.model.f if self.model and self.model.f
             else M.focal_px_from_35mm(self.settings.default_focal_35mm,
                                       self.w, self.h))
        if len(self.control_lines):
            roll, pitch = self._marker_roll_pitch(f)
        elif self.model and self.model.f:
            roll, pitch, _y, _c = W.limit(self.model.roll, self.model.pitch,
                                          self.settings)
        else:
            roll, pitch = 0.0, 0.0
        cx, cy = self.w / 2.0, self.h / 2.0
        sin_sum = cos_sum = 0.0
        n = 0
        for seg in self.control_hlines:
            x0, y0, x1, y1 = (float(seg[0]), float(seg[1]),
                              float(seg[2]), float(seg[3]))
            r0 = np.array([(x0 - cx) / f, (y0 - cy) / f, 1.0])
            r1 = np.array([(x1 - cx) / f, (y1 - cy) / f, 1.0])
            d3 = r1 - r0
            dn = float(np.linalg.norm(d3))
            if dn < 1e-9:
                continue
            w = G.rot_z(-roll) @ G.rot_x(-pitch) @ (d3 / dn)
            a = math.atan2(float(w[2]), float(w[0]))
            sin_sum += math.sin(a)
            cos_sum += math.cos(a)
            n += 1
        if not n:
            return None
        return math.atan2(sin_sum, cos_sum)

    def _marker_roll_pitch(self, f: float):
        """Roll and pitch from vertical control line(s).

        Each V-line's vanishing point gives the camera's apparent direction of
        world-vertical.  ``roll_pitch_from_up`` decomposes that into the two
        corrections that straighten it.  2+ lines: average the up-vectors.
        Without a vertical mark: (0, 0).
        """
        from . import geometry as G
        n = len(self.control_lines)
        if n < 1:
            return 0.0, 0.0
        cx, cy = self.w / 2.0, self.h / 2.0
        ups = []
        for i in range(n):
            seg = self.control_lines[i]
            x0, y0, x1, y1 = (float(seg[0]), float(seg[1]),
                              float(seg[2]), float(seg[3]))
            dx, dy = x1 - x0, y1 - y0
            ln = math.hypot(dx, dy)
            if ln < 1e-6:
                continue
            # Vanishing point of the line (intersection with horizon).
            # Parametric: p(t) = p0 + t*d.  VP is at t → ∞ direction,
            # but for a finite segment we find where two points' rays meet.
            # Simpler: the VP in the image is along the line's direction,
            # at the point where the line would hit the horizon.  For roll/
            # pitch we only need the *direction* of the line in 3D camera
            # coords, which is the normalised difference of the two ray
            # directions from the principal point.
            r0 = np.array([(x0 - cx) / f, (y0 - cy) / f, 1.0])
            r1 = np.array([(x1 - cx) / f, (y1 - cy) / f, 1.0])
            # The line's 3D direction in camera coords is r1 - r0 (both are
            # points on rays from the camera; their difference is parallel to
            # the world line).  Normalise and use as the up-vector candidate.
            d3 = r1 - r0
            dn = np.linalg.norm(d3)
            if dn < 1e-9:
                continue
            u = d3 / dn
            # Ensure u points "up" in the image (negative y component).
            if u[1] > 0:
                u = -u
            ups.append(u)
        if not ups:
            return 0.0, 0.0
        if len(ups) == 1:
            u = ups[0]
        else:
            u = np.mean(ups, axis=0)
            un = np.linalg.norm(u)
            if un < 1e-9:
                return 0.0, 0.0
            u = u / un
        roll, pitch = G.roll_pitch_from_up(u)
        return roll, pitch

    def current_yaw(self):
        """Yaw in radians actually in force, 0 when horizontal correction is off.

        In manual mode this is the slider value **verbatim** — no cap is applied.
        The ``max_horizontal_deg`` limit in ``warp.limit()`` is a batch-era guard
        against an unattended run shearing a frame nobody looks at; in the review
        window the user sees the result and decides whether 75 deg looks right.
        The slider's own range (-90..+90) is the only guard, which is the
        mathematical limit for a folded angle.

        In auto mode it is the model's yaw through the same limits, which is
        where ``correct_horizontal`` gates it to zero.  A hand-drawn horizontal
        A hand-drawn horizontal overrides the model's yaw -- it is a direct
        bearing constraint that needs no vanishing point, and drawing one IS
        the instruction to use it.

        The old version read the marker yaw TWICE: once behind the mode flag
        and once after it, ungated. Only the first was meant; the second
        overrode the model whenever a stale value was lying about, and it
        survived because switching the mode off happened to clear it."""
        hm = self.marker_yaw()
        if hm is not None:
            return hm
        if self.mode == MANUAL:
            return self.manual_yaw
        if self.model is None or not self.model.f:
            return 0.0
        guessed = self.model.f_source in ("default", "prior", "none", "refined")
        return W.limit(self.model.roll, self.model.pitch, self.settings,
                       yaw=self.model.yaw, focal_is_a_guess=guessed)[2]

    def would_skip(self):
        """``None`` if the image would be corrected, else the reason it would not.

        Vertical control lines count as a decision, exactly like moving a
        slider.  Without this the feature defeats itself: the confidence score
        is largely a count of supporting lines, two is far below what it expects
        of a detector, and a photograph the user had just told the truth about
        came back "SKIP, conf=0.04, weakest: count".  Refusing evidence because
        there is little of it is right when a detector produced it and wrong
        when a person did.

        **A hand-placed facade strip is on that list too** (2026-09-20,
        user-directed, measured). ``strip`` restricts the horizontals to one
        wall, which on a corner view *is* the correction -- one camera rotation
        can level one facade, never two. Measured over 11 corner views: a strip
        read off the photograph by eye beats the blanket 20-80% default
        (+0.364 deg of horizontal lean against +0.131), and it drops **7 of
        those 11 from OK to SKIPPED**, confidence falling 0.67 -> 0.20 on
        ``35559_XXL`` and 0.40 -> 0.05 on ``images-(1)``, weakest term
        ``stability`` in six of the seven. On ``Platte`` all three strips tried
        returned the same yaw to within 0.5 deg while confidence ran
        0.20 / 0.49 / 0.54 -- the strip moved the confidence and not the answer.
        The fall is therefore an artifact of counting evidence rather than a
        judgement about the correction, and it lands on exactly the photographs
        the feature exists for. Drawing it is a decision, so it counts as one.

        **This is the review path only.** ``pipeline.process`` -- the unattended
        run -- keeps its veto, and the comment there says why it must.
        """
        if self.mode == MANUAL or self.control_active \
                or len(self.control_hlines) >= 2 or self.strip is not None:
            return None
        if self.model is None:
            return "no model"
        if self.model.confidence < self.settings.min_confidence:
            weakest = self.model.diagnostics.get("weakest_term", "")
            return (self.model.diagnostics.get("reason", "low confidence") +
                    f" (conf={self.model.confidence:.2f}" +
                    (f"; weakest: {weakest})" if weakest else ")"))
        roll, pitch, _, _ = self.current_angles()
        yaw = self.current_yaw()
        if math.degrees(math.hypot(roll, pitch, yaw)) < self.settings.min_correction_deg:
            return "already upright"
        return None

    # -- rendering -------------------------------------------------------
    def render_before(self, max_edge=900, show_lines=True):
        canvas = self.bgr.copy()
        inv = 1.0 / self.scale
        info = getattr(self, "detect_info", None) or {}
        # The mask wash is independent of the line overlay: a region you excluded
        # stays worth seeing -- and its opacity adjustable -- whether or not the
        # detected lines are on screen.  Coupling it to show_lines made the whole
        # opacity control dead the moment "Lines" was switched off.
        if self.show_mask and self.mask_alpha > 0.001:
            mc = getattr(self, "mask_color", None) or (60, 60, 200)
            PV.tint_mask(canvas, info.get("mask"), colour=mc, alpha=self.mask_alpha)
        if show_lines:
            # A struck line is gone from view, not recoloured: the user excluded
            # it on purpose (stroke, strike-slanted or a click), so drawing it in
            # grey only argues for putting it back.  It stays out of the fit --
            # `enabled` decides that -- and simply stops being shown.
            used = self.vert.subset(self.enabled)
            m = self.model
            dropped = info.get("masked_out")
            if dropped is not None and len(dropped):
                PV.draw_lines(canvas, dropped * inv, PV.RED, 1)
            if len(self.horiz):
                PV.draw_lines(canvas, self.horiz.seg * inv, PV.BLUE, 1)
            if len(used):
                inl = m.vert_inliers[self.enabled] if m is not None else np.zeros(len(used), bool)
                PV.draw_lines(canvas, used.seg[~inl] * inv, PV.YELLOW, 1)
                PV.draw_lines(canvas, used.seg[inl] * inv, PV.GREEN, 2)
            if m is not None and m.f:
                K = G.intrinsics(m.f, self.w / 2.0, self.h / 2.0)
                PV._draw_infinite_line(canvas, G.horizon_line(m.up, K), PV.MAGENTA, 2)
        return _fit(canvas, max_edge)

    def render_after(self, max_edge=900, apply_crop=True):
        """The corrected frame, at preview size.

        ``apply_crop=False`` returns the *whole* corrected frame with the
        hand-drawn rectangle left un-cut.  That is what a crop tool needs and
        what the review window asks for: a preview that cuts as you drag comes
        back a different size, gets re-fitted into the pane at a different
        scale, and the picture leaps under the cursor mid-gesture.  Worse than
        the leap, the next drag is then measured against a frame that is
        already smaller than the one the fractions are stored against, so the
        second rectangle lands somewhere nobody dragged.  Shading the discarded
        part instead keeps one coordinate system for the whole session.

        When a planar quad (four facade corners) is placed, the rectified view
        replaces the rotation-based preview entirely -- it is the direct answer
        to "straighten this facade" and does not go through roll/pitch/yaw at
        all."""
        if len(self.planar_quad) >= 4:
            return self.planar_rectified(max_edge=max_edge)
        roll, pitch, f, _ = self.current_angles()
        yaw = self.current_yaw()
        crop = self._apply_crop if apply_crop else (lambda img: img)
        if abs(roll) < 1e-9 and abs(pitch) < 1e-9 and abs(yaw) < 1e-9:
            return _fit(crop(self.bgr), max_edge)
        # render the preview from a reduced copy: a 24 MP warp per slider tick
        # is unusable, and the geometry is scale invariant apart from f
        s = min(1.0, float(max_edge) / max(self.w, self.h))
        small = cv2.resize(self.bgr, (max(1, int(self.w * s)), max(1, int(self.h * s))),
                           interpolation=cv2.INTER_AREA) if s < 1.0 else self.bgr
        sh, sw = small.shape[:2]
        H = W.build(sw, sh, f * s, roll, pitch, yaw, max_area=self.settings.max_area_ratio)
        # Pass detected line segments so plan() can use the motif reframe
        # (facade + margin, centred) instead of the whole-frame pad.
        _segs = None
        if len(self.vert) or len(self.horiz):
            _parts = [x.seg for x in (self.vert, self.horiz) if len(x)]
            if _parts:
                _segs = np.concatenate(_parts, axis=0)
        planned = W.plan(sw, sh, H, self.settings, line_segs=_segs, yaw=yaw,
                         strip=self.strip)
        if planned is None:
            return _fit(self.bgr, max_edge)
        H_total, ow, oh, _, _ = planned
        # fit again: with crop="auto" the plan may keep the whole frame and pad
        # it, which is *larger* than the input, and a preview that ignores the
        # size it was asked for overflows the pane it was drawn for
        out = W.apply(small, H_total, ow, oh, self.settings)
        out = self._fill_preview(out, H_total, sw, sh, ow, oh)
        # Fit FIRST so the final array is at display size, then project the
        # markers onto it.  Drawing before _fit would leave the marker coords
        # in OW×OH space while the array is smaller → wrong position.
        out = _fit(crop(out), max_edge)
        fit_s = out.shape[1] / float(ow) if ow > 0 else 1.0
        # Project hand-drawn marker lines through the warp.  H_total maps
        # preview-space → OW×OH output space; scale original→preview before,
        # then scale the result by fit_s to land on the final array.
        if len(self.control_hlines) > 0 or len(self.control_lines) > 0:
            for arr, col in ((self.control_hlines, (80, 160, 80)),
                             (self.control_lines, (200, 80, 200))):
                if len(arr) == 0:
                    continue
                pts = np.zeros((len(arr), 2, 2), dtype=np.float32)
                for i in range(len(arr)):
                    pts[i, 0] = [arr[i, 0] * s, arr[i, 1] * s]
                    pts[i, 1] = [arr[i, 2] * s, arr[i, 3] * s]
                mapped = cv2.perspectiveTransform(pts.reshape(-1, 1, 2), H_total)
                for i in range(len(arr)):
                    a = (int(mapped[2 * i, 0, 0] * fit_s),
                         int(mapped[2 * i, 0, 1] * fit_s))
                    b = (int(mapped[2 * i + 1, 0, 0] * fit_s),
                         int(mapped[2 * i + 1, 0, 1] * fit_s))
                    cv2.line(out, a, b, col, 2)
        return out

    def _fill_preview(self, out, H_total, sw, sh, ow, oh):
        """Fill the band in the preview, for a backend cheap enough to redraw.

        The save path fills unconditionally; this one does not, and the split is
        about cost rather than correctness.  ``telea`` needs no model and costs
        milliseconds at preview size, so a user who picks it sees what they are
        choosing.  ``lama`` and ``comfyui`` load a model and take seconds -- per
        slider tick that is unusable, so the preview keeps the pad and
        ``status_text`` says the fill happens on save.

        Order matters and matches ``save()``: warp, then fill, then crop.  A
        refusal above ``--fill-max-share`` comes back as the un-filled band
        rather than an exception, because a preview is not the place to fail.
        """
        from . import inpaint as FILL
        if not FILL.previews_live(self.settings):
            return out
        try:
            hole = W.filled_region(H_total, sw, sh, ow, oh)
            preview = self.settings.replace(fill_max_edge=FILL.PREVIEW_MAX_EDGE)
            filled, _note = FILL.fill(out, hole, preview)
            return filled
        except FILL.FillUnavailable:
            return out

    def render_pair(self, max_edge=900):
        return self.render_before(max_edge), self.render_after(max_edge)

    def planned_size(self):
        """``(w, h)`` the saved file would have, or ``None`` if nothing warps.

        Geometry only -- `warp.plan` works on the quad, never on the pixels --
        so it is cheap enough to ask on every status refresh.
        """
        roll, pitch, f, _clamped = self.current_angles()
        yaw = self.current_yaw()
        if not f or (abs(roll) < 1e-9 and abs(pitch) < 1e-9 and abs(yaw) < 1e-9):
            return None
        H = W.build(self.w, self.h, f, roll, pitch, yaw,
                    max_area=self.settings.max_area_ratio)
        segs = [x.seg for x in (self.vert, self.horiz) if len(x)]
        planned = W.plan(self.w, self.h, H, self.settings,
                         line_segs=np.concatenate(segs, axis=0) if segs else None,
                         yaw=yaw, strip=self.strip)
        if planned is None:
            return None
        return int(planned[1]), int(planned[2])

    def size_note(self):
        """A warning when the saved frame would be much larger than the source.

        Squaring a facade that runs away from the camera stretches its far end,
        and the canvas grows to hold it: the left facade of Platte_1.jpg goes
        from 1320x742 to 7022x2167 -- **fifteen times the area** -- and nothing
        said so until the file was on disk. The threshold is 1.5x, from the
        09-17 review's F3.

        Not phrased as a refusal. The big canvas is the correct answer to a
        40-degree yaw; it is just an answer somebody should see coming.
        """
        size = self.planned_size()
        if not size:
            return ""
        ratio = (size[0] * size[1]) / float(max(self.w * self.h, 1))
        if ratio <= 1.5:
            return ""
        return (f"saved size {size[0]}x{size[1]} -- {ratio:.1f}x the original "
                f"area; crop, or lower 'detail (px kept)'")

    def status_text(self):
        roll, pitch, f, clamped = self.current_angles()
        f35 = M.focal_35mm_from_px(f, self.w, self.h) if f else 0.0
        skip = self.would_skip()
        conf = self.model.confidence if self.model else 0.0
        src = self.model.f_source if self.model else "-"
        head = ("MANUAL" if self.mode == MANUAL
                else "MARKED" if (self.control_active
                                  or len(self.control_hlines) >= 2)
                else ("SKIP" if skip else "AUTO"))
        mask_note = ""
        if self.detect_error:
            mask_note = f"mask problem: {self.detect_error}"
        elif self.settings.mask_mode == "off":
            mask_note = "mask: off"
        elif self.mask_active:
            info = self.detect_info or {}
            dropped = info.get("masked_out")
            n = 0 if dropped is None else len(dropped)
            mask_note = f"mask: {self.settings.mask_mode}, {n} line(s) removed"
        else:
            mask_note = f"mask: {self.settings.mask_mode} produced nothing"
        conf_note = (f"conf={conf:.2f}" if not self.control_active
                     else "conf=n/a (you stated the verticals)")
        yaw_note = (f"  yaw={math.degrees(self.current_yaw()):+.2f}deg"
                    if abs(self.current_yaw()) > 1e-9 else "")
        parts = [f"{head}  roll={math.degrees(roll):+.2f}deg  pitch={math.degrees(pitch):+.2f}deg{yaw_note}",
                 f"f={f35:.0f}mm ({src if self.mode == AUTO else 'manual'})  {conf_note}  "
                 f"lines={int(self.enabled.sum())}/{len(self.vert)}"]
        parts.append(mask_note)
        if self.control_active:
            parts.append(f"{len(self.control_lines)} vertical control line(s) in "
                         f"force -- the detected verticals are not being used")
        elif len(self.control_lines) == 1:
            parts.append("1 vertical control line -- one more is needed before "
                         "they take over, since two determine a vanishing point")
        if len(self.control_hlines) >= 2:
            parts.append(f"{len(self.control_hlines)} horizontal control line(s) in "
                         f"force -- the detected horizontals are not being used")
        elif len(self.control_hlines) == 1:
            parts.append("1 horizontal control line -- one more is needed before "
                         "they take over, since two determine a vanishing point")
        fill_mode = getattr(self.settings, "fill", "none")
        if fill_mode not in ("", "none"):
            from . import inpaint as FILL
            if FILL.previews_live(self.settings):
                slow = fill_mode not in FILL.LIVE_MODES
                parts.append(f"fill: {fill_mode}"
                             + (" -- live preview on, each redraw waits for it"
                                if slow else ""))
            else:
                parts.append(f"fill: {fill_mode} -- runs on save; tick 'live "
                             f"fill preview' to see it here")
        # The preview no longer cuts the crop out -- it shades it -- so the
        # crop has to be stated.  A rectangle that only exists as a dimmed area
        # on screen is exactly the kind of thing that gets forgotten before the
        # save, and the save is where it becomes permanent.
        if self.crop_rect is not None:
            loss = self.crop_loss() * 100.0
            parts.append(f"crop: the shaded area is cut on save "
                         f"-- keeps {100.0 - loss:.0f}%, cuts {loss:.0f}% of the frame")
            if fill_mode not in ("", "none"):
                parts.append("fill and crop are two answers to the same band; "
                             "the crop discards what the fill invents")
        if self.strip is not None:
            gw_s = self.gray.shape[1]
            n_in = int(L.in_xband(self.horiz.seg, self.strip[0] * gw_s,
                                  self.strip[1] * gw_s).sum())
            parts.append(f"region: horizontal evidence restricted to the "
                         f"selected strip ({n_in} of {len(self.horiz)} lines) -- "
                         f"the yaw is taken from that facade only")
        note = self.size_note()
        if note:
            parts.append(note)
        if clamped:
            parts.append("correction hit the configured limit")
        # P4: warn when two horizontal VPs have meaningful support (two-facade)
        if self.model is not None and self.settings.correct_horizontal:
            supports = self.model.diagnostics.get("horiz_supports", [])
            if sum(1 for s in supports[:2] if s > 0.2) >= 2:
                parts.append("two horizontal directions detected — yaw follows "
                             "the dominant facade; use facade strip to choose")
        if skip:
            parts.append(f"would skip: {skip}")
        return "\n".join(parts)

    # -- output ----------------------------------------------------------
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
    def planar_is_set(self) -> bool:
        """Four corners down: the planar rectification is what the user sees."""
        return len(self.planar_quad) >= 4

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
        if len(self.planar_quad) < 4:
            return None
        return P.transform_for(np.array(self.planar_quad))

    def planar_rectified(self, max_edge=None):
        """The rectified frame from the four corners.

        ``max_edge`` renders from a reduced copy -- a full-resolution warp per
        corner drag is unusable on a 24 MP frame, and the homography scales
        with the image, so warping small and calling it a preview is exact up
        to the preview's own resolution.  Saving passes nothing and gets the
        full-size result."""
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
            return cv2.warpPerspective(img, S @ H @ np.linalg.inv(S),
                                       (max(1, int(w * s)), max(1, int(h * s))))
        return cv2.warpPerspective(self.bgr, H, (w, h))

    def save_planar(self, dst_path: str) -> str:
        """Write the rectified view.  No fill band exists to fill: the output
        is exactly the warped quad."""
        out = self.planar_rectified()
        if out is None:
            raise ValueError("planar correction needs four corners")
        os.makedirs(os.path.dirname(os.path.abspath(dst_path)) or ".", exist_ok=True)
        IO.save(dst_path, out, self.src, self.settings)
        return dst_path

    def save(self, dst_path: str, on_stage=None):
        """Write the corrected image using whatever is currently in force.

        ``on_stage(name, canvas_mpx)`` is called before each slow step. This
        work runs on the caller's thread -- the window is frozen while it does
        -- so the callback exists to let a GUI paint one line saying what is
        happening before it stops answering. Measured on a 12 MPx photograph:
        the warp is 30 ms and the fill 3 to 13 seconds, so "which stage" is the
        whole of the information.
        """
        def _stage(name, mpx=0.0):
            if on_stage is not None:
                on_stage(name, mpx)
        roll, pitch, f, _ = self.current_angles()
        yaw = self.current_yaw()
        if (abs(roll) < 1e-12 and abs(pitch) < 1e-12 and abs(yaw) < 1e-12
                and self.crop_rect is None):
            IO.copy_through(self.path, dst_path)
            return dst_path
        if abs(roll) < 1e-12 and abs(pitch) < 1e-12 and abs(yaw) < 1e-12:
            # nothing to straighten, but the user cropped by hand
            out = self._apply_crop(self.bgr)
            os.makedirs(os.path.dirname(os.path.abspath(dst_path)) or ".", exist_ok=True)
            IO.save(dst_path, out, self.src, self.settings)
            return dst_path
        # keep_size=False: the corrected image must never be SMALLER than the
        # original.  With keep_size=True, _whole_frame would scale an inflated
        # quad (large yaw) back down to source dimensions — that is exactly the
        # shrinkage the user forbade.  keep_size=False lets the output grow to
        # fit the full warped frame; for small roll/pitch the quad barely
        # inflates so the output stays close to source size either way.
        save_settings = self.settings.replace(keep_size=False)
        H = W.build(self.w, self.h, f, roll, pitch, yaw, max_area=save_settings.max_area_ratio)
        _segs = None
        if len(self.vert) or len(self.horiz):
            _parts = [x.seg for x in (self.vert, self.horiz) if len(x)]
            if _parts:
                _segs = np.concatenate(_parts, axis=0)
        planned = W.plan(self.w, self.h, H, save_settings, line_segs=_segs, yaw=yaw,
                         strip=self.strip)
        if planned is None:
            IO.copy_through(self.path, dst_path)
            return dst_path
        H_total, ow, oh, _, _ = planned
        mpx = ow * oh / 1e6
        _stage("warping", mpx)
        out = W.apply(self.bgr, H_total, ow, oh, save_settings)
        if getattr(save_settings, "fill", "none") not in ("", "none"):
            from . import inpaint as FILL
            hole = W.filled_region(H_total, self.w, self.h, ow, oh)
            _stage("fill:" + str(save_settings.fill), mpx)
            out, _note = FILL.fill(out, hole, save_settings)
        out = self._apply_crop(out)
        _stage("writing", mpx)
        os.makedirs(os.path.dirname(dst_path) or ".", exist_ok=True)
        IO.save(dst_path, out, self.src, self.settings)
        return dst_path


def _fit(img, max_edge):
    s = min(1.0, float(max_edge) / max(img.shape[:2]))
    if s >= 1.0:
        return img
    return cv2.resize(img, (max(1, int(img.shape[1] * s)), max(1, int(img.shape[0] * s))),
                      interpolation=cv2.INTER_AREA)


def darken_outside_crop(bgr, rect, keep=0.25):
    """A copy of ``bgr`` with everything outside ``rect`` dimmed to ``keep`` of its
    original brightness -- a flat 75 % black veil for ``keep=0.25``.

    ``rect`` is ``(x0, y0, x1, y1)`` as fractions of the frame; ``None`` leaves the
    image untouched.  The preview bakes this into the displayed array because a Tk
    canvas item has no alpha channel and a stipple dither reads lighter than the flat
    fill it stands in for.  Each cut-away pixel is dimmed exactly once, so corners do
    not compound.
    """
    if rect is None:
        return bgr
    h, w = bgr.shape[:2]
    fx0, fy0, fx1, fy1 = (min(1.0, max(0.0, float(v))) for v in rect)
    x0, x1 = sorted((int(round(fx0 * w)), int(round(fx1 * w))))
    y0, y1 = sorted((int(round(fy0 * h)), int(round(fy1 * h))))
    mask = np.ones((h, w), dtype=bool)      # True where the veil goes
    mask[y0:y1, x0:x1] = False              # ...except the kept rectangle
    out = bgr.copy()
    out[mask] = (out[mask].astype(np.float32) * float(keep)).astype(np.uint8)
    return out
