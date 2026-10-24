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
from types import SimpleNamespace

import cv2
import numpy as np

from . import geocal as GC
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
    keep = _touch_keep(ls, mask)
    return ls if keep is None else ls.subset(keep)


def _touch_keep(ls, mask):
    """Which segments of ``ls`` survive ``mask``, or ``None`` when all do.

    Split out of `_drop_touching` so refit can tell which of its lines the fit
    actually saw: the inlier overlay maps the fit's answer back by position.
    """
    if mask is None or ls is None or not len(ls.seg):
        return None
    keep = MK.untouched(ls.seg, mask)
    if keep.all():
        return None
    if not keep.any():
        # A mask that swallows a whole pool is a user error, not a fit: keeping
        # nothing would make the estimator answer from noise. Say nothing and
        # keep the pool -- refit's own floors then decide.
        return None
    return keep


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
        self.mask_alpha = 0.50
        self.mask_color = (20, 255, 57)  # BGR neon green; the GUI swatch overrides this
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
        # Lines OR rectangle, never both (user, 2026-10-23). The one put away
        # waits here and comes back on the way back.
        self.facade_mode = "lines"
        self._stashed_lines = (np.zeros((0, 4)), np.zeros((0, 4)))
        self._stashed_quad = []
        # The crop goes with its mode: its fractions are of THAT mode's canvas,
        # and on the other one they cut somewhere nobody chose (user,
        # 2026-10-23: the saved rectangle kept 24 % because the line mode's
        # auto crop had come along).
        self._stashed_crop = ({k: (False, 0.0) for k in self._crop_edges}, False)
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
        # The box the user dragged for SAM (full-resolution px), which is also
        # the crop box (user, 2026-10-24: "SAM-Box sollte Crop-Box sein").
        self.sam_box = None
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
        # Who to tell while a fill is working: ``on_fill_step(label, done)``,
        # set by the window, None under the CLI. An attribute rather than a
        # parameter threaded through `render_after` -> `_fill_preview`, because
        # it is the same kind of thing as `mask_alpha` and `show_mask` -- how
        # this session is being LOOKED at, which the batch path has no opinion
        # about.
        self.on_fill_step = None
        self.refit()

    # -- detection -------------------------------------------------------
    def _detect(self):
        """Run the line front end.  Separate from ``__init__`` because changing
        the mask has to redo it, not just refit."""
        try:
            (self.all_lines, self.vert, self.horiz, self.detector,
             self.detect_info) = L.prepare(self.gray, self.settings, self._small, self.path)
            self.detect_error = ""
        except Exception as exc:
            # a missing mask file must not take the window down; say so instead
            self.detect_error = str(exc)
            safe = self.settings.replace(mask_mode="off")
            (self.all_lines, self.vert, self.horiz, self.detector,
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
            horiz = self._strip_horizontals()
        # An annotator that TOUCHES the mask is not evidence -- for the
        # DETECTOR. The mask is aimed at what a detector finds and a person
        # would not: sky, branches, parked cars. A hand-drawn control line is
        # the opposite case. Somebody could see the mask while they drew across
        # it and drew anyway, which is a statement, not an accident; dropping it
        # discards the more reliable of the two. The same reasoning `would_skip`
        # already applies to the confidence gate -- refusing evidence for being
        # scarce is right for a detector and wrong for a person.
        # (2026-09-20, user: "maske und marker sollten sich vertragen".)
        # Positions in self.vert of the lines the fit sees, so its inliers can
        # be mapped back.  The mask used to drop lines without this, the
        # lengths no longer matched, and with any mask on the overlay showed
        # no inlier at all (Alte_Scheune: 300 of 390, then 0).
        vidx = None if drew_v else np.flatnonzero(self.enabled)
        if not drew_v:
            keep = _touch_keep(vert, self.ignore_mask("v"))
            if keep is not None:
                vert, vidx = vert.subset(keep), vidx[keep]
        if not drew_h:
            horiz = _drop_touching(horiz, self.ignore_mask("h"))
        exif_px = IO.focal_px_from_exif(self.src, self.w, self.h) \
            if self.settings.use_exif_focal else None
        # A strip means one facade has been pointed at, which is the precondition
        # for building the rotation from that facade's own two vanishing points.
        m = M.estimate(vert, horiz, gw, gh, settings,
                       exif_px * self.scale if exif_px else None,
                       single_facade=self.strip is not None,
                       learned_focal=GC.provider(self.bgr, self.scale, settings))
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
        if (not self.control_active and vidx is not None
                and m.vert_inliers is not None
                and len(m.vert_inliers) == len(vidx)):
            full[vidx] = m.vert_inliers
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
            # Auto (no yaw) is centred too ("bei Auto soll das auch so sein",
            # 2026-10-24), in the photograph's own aspect.
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

        The painted mask counts only from PAINT_FRAMES_SHARE of the frame on
        (user, 2026-10-24: "Maskierung manuell muss anders bewertet werden, da
        oft nur punktweise, um Annotatoren zu killen" -- "grundsaetzlich wird
        aber eher ausserhalb POI maskiert"): a few dabs that switch off lines
        say nothing about where the subject is, a broad wash does. The strip
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
                                   display_scale: float = 1.0, kind: str = "v",
                                   refit: bool = True):
        """Move one endpoint of an existing control line to (x, y).

        ``refit=False`` leaves the fit for later: a drag moves the endpoint on
        every motion event, and a 30 ms refit each time is most of the lag
        (2026-10-23); the drag refits at its own, slower rate and on release."""
        attr = self._ctrl_attr(kind)
        arr = getattr(self, attr)
        if not (0 <= index < len(arr)):
            return False
        inv = self.scale / max(display_scale, 1e-9)
        col = endpoint_idx * 2
        arr[index, col] = x * inv
        arr[index, col + 1] = y * inv
        setattr(self, attr, arr)
        if refit:
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
        self.sam_box = None
        self._apply_paint()          # releases, then returns on `paint is None`
        self._refresh_mask()
        self.refit()
        return had

    # -- SAM2 click-to-select --------------------------------------------
    def apply_sam_mask(self, ignore: np.ndarray, box=None):
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
        self.sam_box = (None if box is None or ignore is None
                        else tuple(float(v) for v in box))
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
            # Pitch is only as good as the f it back-projected with (f-sweep: a 0.5x-2x
            # f error swings the pitch 12.6->41.8 deg). A true default/prior f (not
            # determined from the lines) would wreck the image, so drop the pitch then;
            # a refined/exif/manual f is a real estimate, so keep it. Roll is f-robust.
            f_is_guess = (self.model is None or self.model.f is None
                          or self.model.f_source in ("default", "prior", "none"))
            roll, pitch = self._marker_roll_pitch(f, pitch_ok=not f_is_guess)
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
        # The yaw goes in too, or `clamped` cannot see a yaw past its cap and
        # the status never says the correction hit the limit (current_yaw caps
        # it either way).
        roll, pitch, _yaw, clamped = W.limit(self.model.roll, self.model.pitch,
                                             self.settings, yaw=self.model.yaw,
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
        to_full = 1.0 / self.scale
        sin_sum = cos_sum = 0.0
        n = 0
        for seg in self.control_hlines:
            x0, y0, x1, y1 = (float(seg[0]) * to_full, float(seg[1]) * to_full,
                              float(seg[2]) * to_full, float(seg[3]) * to_full)
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

    def _marker_roll_pitch(self, f: float, pitch_ok: bool = True):
        """Roll and pitch from vertical control line(s).

        Each V-line back-projects to a plane through the camera centre, and the
        world-up vector lies in every such plane.  With two or more lines that
        pins the up-vector to the direction perpendicular to all the plane
        normals -- the smallest-eigenvalue eigenvector of their outer-product
        sum -- which is a proper 3-D solve, so pitch is recovered instead of
        assumed zero.  One line leaves pitch undetermined, so it corrects roll
        only.  Without a vertical mark: (0, 0).
        """
        from . import geometry as G
        n = len(self.control_lines)
        if n < 1:
            return 0.0, 0.0
        cx, cy = self.w / 2.0, self.h / 2.0
        to_full = 1.0 / self.scale
        rays = []
        for i in range(n):
            seg = self.control_lines[i]
            x0, y0, x1, y1 = (float(seg[0]) * to_full, float(seg[1]) * to_full,
                              float(seg[2]) * to_full, float(seg[3]) * to_full)
            r0 = np.array([(x0 - cx) / f, (y0 - cy) / f, 1.0])
            r1 = np.array([(x1 - cx) / f, (y1 - cy) / f, 1.0])
            if np.linalg.norm(r1 - r0) < 1e-9:
                continue
            rays.append((r0, r1))
        if not rays:
            return 0.0, 0.0
        if len(rays) == 1:
            # A single line: the up-vector is any direction in its plane, so
            # pitch is undetermined -- correct roll only, as before.
            r0, r1 = rays[0]
            u = r1 - r0
            u = u / np.linalg.norm(u)
            if u[1] > 0:
                u = -u
            return G.roll_pitch_from_up(u)
        # 2+ lines: the up-vector lies in every line's back-projected plane.
        # Each plane's normal is r0 x r1; the direction perpendicular to all of
        # them is the smallest-eigenvalue eigenvector of sum_i n_i n_i^T.
        M = np.zeros((3, 3))
        for r0, r1 in rays:
            nm = np.cross(r0, r1)
            nn = np.linalg.norm(nm)
            if nn < 1e-12:
                continue
            nm = nm / nn
            M += np.outer(nm, nm)
        w, vecs = np.linalg.eigh(M)
        u = vecs[:, 0]
        if u[1] > 0:
            u = -u
        roll, pitch = G.roll_pitch_from_up(u)
        if not pitch_ok:
            # The pitch is only as good as the focal length it back-projected
            # with; when f is a default guess the pitch is garbage (measured
            # 12.6 -> 41.8 deg across a 0.5x-2x focal sweep). The roll is
            # f-robust, so keep it and drop the pitch.
            pitch = 0.0
        return roll, pitch

    def _rectify_active(self):
        """Is the facade-to-rectangle warp in force?

        Horizontal auto on a SELECTED facade, nothing drawn by hand: the
        verticals come from the automatic vertical vanishing point, the
        horizontals from the dominant horizontal one.  A drawn line or a manual angle is the user's own
        instruction and keeps the rotation path.
        """
        m = self.model
        # No facade selection required any more (2026-10-21, measured on
        # wohnheim): without a strip or mask this used to fall back to the
        # camera rotation, which aims at the same dominant horizontal vanishing
        # point anyway but bent the plumb verticals by up to 4.7 deg.  On a
        # corner view a strip or mask still decides WHICH facade becomes the
        # rectangle -- by deciding which lines feed `horiz_vps`.
        return (self.settings.correct_horizontal
                and not len(self.control_lines) and not len(self.control_hlines)
                and self.mode != MANUAL and m is not None and m.vp is not None
                and bool(m.horiz_vps))

    def _build_H(self, w, h, f, roll, pitch, yaw, max_area=4.0):
        """The one place a correction homography is made (preview, save, crop).

        Horizontal auto is a WARP that turns the facade into a rectangle, with
        the length adjustment that compensates the foreshortening of the
        horizontals (`warp.rectify`); everything else is the camera rotation.
        Falls back to the rotation when the warp is degenerate.

        Hand-drawn lines come first (user, 2026-10-20): every drawn line comes
        out EXACTLY vertical or horizontal, nothing in between.  Per direction,
        one drawn line is helped by the detector's vanishing point and two or
        more decide alone (`warp.vp_from_lines`)."""
        gw = self.gray.shape[1]
        k = w / float(gw)

        def to_build(v):
            v = np.asarray(v, float)
            return np.array([v[0] * k, v[1] * k, v[2]])

        if len(self.control_lines) or len(self.control_hlines):
            m = self.model
            # Detector help: the vertical VP always (Auto corrects verticals);
            # the horizontal one only with horizontal auto on, else the
            # horizontals stay as they are (a point at infinity on the x-axis).
            dv = self._data_vertical_vp()
            fv = to_build(dv) if dv is not None else np.array([0.0, 1.0, 0.0])
            fh = (to_build(m.horiz_vps[0])
                  if self.settings.correct_horizontal and m is not None and m.horiz_vps
                  else np.array([1.0, 0.0, 0.0]))
            vv = W.vp_from_lines(np.asarray(self.control_lines, float) * k, fv)
            vh = W.vp_from_lines(np.asarray(self.control_hlines, float) * k, fh)
            # With horizontal auto the drawn rectangle comes out in its true
            # proportions, squares square (user, 2026-10-23); without it the
            # horizontals are left as they are and so is the width.
            H = (W.rectify_metric(vv, vh, w, h, f) if self.settings.correct_horizontal
                 else W.rectify(vv, vh, w, h))
            if H is not None:
                return H
        if self._rectify_active():
            dv = self._data_vertical_vp()
            H = W.rectify_metric(to_build(dv if dv is not None else self.model.vp),
                                 to_build(self.model.horiz_vps[0]), w, h, f)
            if H is not None:
                return H
        return W.build(w, h, f, roll, pitch, yaw, max_area=max_area)

    # How steep a horizontal may stand in the photograph once a facade strip is
    # placed: up to just short of the vertical window. The detector's own
    # window (`horizontal_window_deg`, 32) drops the horizontals of a strongly
    # foreshortened facade, which slope 38-60 deg -- measured on rendered
    # building-sized cubes, 2 horizontals found in the strip against 33-37
    # verticals, so horizontal auto had no vanishing point for exactly the
    # facades it exists for ("das ist der haerteste Contest", user 2026-10-23).
    # Without a strip nobody has said which wall counts, and the narrow window
    # keeps roof edges and diagonals out of the vote as before.
    STRIP_HORIZ_GAP_DEG = 4.0
    FOLD_MAX_FORESHORTEN = 3.0
    CROP_TO_INTEREST = True   # auto crop frames the line-dense region (see auto_crop)
    SOFT_YAW_DEG = 15.0       # up to this yaw the auto crop is centred on the outline's middle
    FOLD_FRAME_TOL = 0.05     # how far the facade may poke out of a plan before the quad path frames it

    def _plan_segs(self, width):
        """The detected segments in the pixels of a ``width``-wide frame.

        `warp.plan` frames the motif from them in ITS OWN pixels, and they are
        stored in the analysis image's. Passing them unscaled gave the preview
        (``sw`` wide) and the full-size crop/save (``self.w``) two different
        canvases whenever the analysis image was a third size -- a 2400 px
        rendered street put the auto crop's box in the ground on the preview.
        """
        parts = [x.seg for x in (self.vert, self.horiz) if len(x)]
        if not parts:
            return None
        segs = np.concatenate(parts, axis=0).astype(float)
        segs[:, :4] *= width / float(self.gray.shape[1])
        return segs

    def _strip_horizontals(self):
        """The horizontal pool: the detector's, or with a strip a wider one."""
        ls = getattr(self, "all_lines", None)
        if self.strip is None or ls is None or len(ls) == 0:
            return self.horiz
        st = self.settings
        window = 90.0 - st.vertical_window_deg - self.STRIP_HORIZ_GAP_DEG
        if window <= st.horizontal_window_deg:
            return self.horiz
        _v, horiz = L.split_by_orientation(ls, st.vertical_window_deg, window,
                                           st.angular_softness)
        # Nothing inside the strip: refit falls back to the whole frame, and
        # that fallback must be the detector's own pool, not the wide one.
        band = self.layer("strip")
        if band is None or not len(horiz.seg) or not MK.untouched(horiz.seg, band).any():
            return self.horiz
        return horiz

    def _data_vertical_vp(self):
        """Where the photograph's vertical lines actually converge (gray px).

        The 2D warp needs the LINES' vanishing point, not `model.vp`: the model's
        point comes out of the camera fit (focal x pitch, blended with priors)
        and can sit far from the lines.  Measured 2026-10-20 on wohnheim.jpg:
        the verticals are parallel (their own point at y = -322822, i.e. at
        infinity) while `model.vp` said y = -7197 -- a 7.4 deg pitch that is not
        in the picture -- and the warp, exact through that point, tilted every
        vertical by +4 deg.  Least squares over the model's vertical inliers
        (all detected verticals when it has none), each line weighted by its
        length so a long edge outvotes a short one."""
        seg = np.asarray(getattr(self.vert, "seg", np.zeros((0, 4))), float)
        if seg.ndim != 2 or len(seg) == 0:
            return None
        seg = seg[:, :4]
        m = self.model
        inl = None if m is None else getattr(m, "vert_inliers", None)
        if inl is not None and len(inl) == len(seg) and np.any(inl):
            seg = seg[np.asarray(inl, bool)]
        rows = []
        for x1, y1, x2, y2 in seg:
            l = np.cross([x1, y1, 1.0], [x2, y2, 1.0])
            n = np.hypot(l[0], l[1])
            if n > 1e-12:
                rows.append(l / n * np.hypot(x2 - x1, y2 - y1))
        if len(rows) < 2:
            return None
        return np.linalg.svd(np.array(rows))[2][-1]

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

    def directions_diverge(self, ratio=0.5, min_support=0.5):
        """Two (or more) comparably-strong, DIFFERENT horizontal directions.

        User-directed (2026-09-26): a corner view mixes two facades' worth of
        horizontal evidence into one pitch/yaw estimate, which is exactly why
        the old corner-view pitch damping existed and was then removed --
        "keine Daempfung waere am besten" (no damping is the cleaner answer).
        The replacement is manual, not automatic: point the strip at one
        facade instead of guessing how much to trust the mixed evidence. This
        only detects the case that needs pointing at; it never places the
        strip itself, no auto_facade_corners-style guess -- the caller's job
        is to ask, per `planar.py`'s own stated principle for exactly this
        situation ("guessing wrong warps a good photo into a sliver").

        A no-op (returns False) once `self.strip` is already set: the
        question has already been answered for this photograph.
        """
        if self.strip is not None or self.model is None:
            return False
        supports = (self.model.diagnostics or {}).get("horiz_supports") or []
        if len(supports) < 2:
            return False
        top, second = supports[0], supports[1]
        return top >= min_support and second >= min_support and second >= ratio * top

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

    def render_after(self, max_edge=900, apply_crop=True, fill="full"):
        """The corrected frame, at preview size.

        ``fill`` is how the band is filled: ``"full"`` as the settings say,
        ``"none"`` not at all (a drag in progress: a preview a person is
        steering wants speed, not paint), ``"defer"`` the stand-in now and,
        when the chosen backend is a slow one shown live, the job for it left
        in ``self.deferred_fill`` for the caller to run when it has time
        (user, 2026-10-23: "erst telea, dann relaxed lama hinterher").

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
        self.preview_map = None
        self.deferred_fill = None
        if self.active_quad() is not None:
            r = self._planar_warp(max_edge)
            if r is None:
                return _fit(self.bgr, max_edge)
            out, Hs, sw, sh, ow, oh, s = r
            crop = self._apply_crop if apply_crop else (lambda img: img)
            raw = out
            out = self._fill_preview(out, Hs, sw, sh, ow, oh, mode=fill)
            self._defer_fill(raw, Hs, sw, sh, ow, oh, fill, crop)
            self.preview_map = ("warp", s, Hs, 1.0)
            return crop(out)
        roll, pitch, f, _ = self.current_angles()
        yaw = self.current_yaw()
        crop = self._apply_crop if apply_crop else (lambda img: img)
        if abs(roll) < 1e-9 and abs(pitch) < 1e-9 and abs(yaw) < 1e-9:
            out = _fit(crop(self.bgr), max_edge)
            self.preview_map = ("scale", out.shape[1] / float(self.w))
            return out
        # render the preview from a reduced copy: a 24 MP warp per slider tick
        # is unusable, and the geometry is scale invariant apart from f
        s = min(1.0, float(max_edge) / max(self.w, self.h))
        small = cv2.resize(self.bgr, (max(1, int(self.w * s)), max(1, int(self.h * s))),
                           interpolation=cv2.INTER_AREA) if s < 1.0 else self.bgr
        sh, sw = small.shape[:2]
        H = self._build_H(sw, sh, f * s, roll, pitch, yaw, max_area=self.settings.max_area_ratio)
        # Pass detected line segments so plan() can use the motif reframe
        # (facade + margin, centred) instead of the whole-frame pad.
        _segs = self._plan_segs(sw)
        planned = W.plan(sw, sh, H, self.settings, line_segs=_segs, yaw=yaw,
                         strip=self.strip)
        if planned is None:
            return _fit(self.bgr, max_edge)
        H_total, ow, oh, _, _ = planned
        # fit again: with crop="auto" the plan may keep the whole frame and pad
        # it, which is *larger* than the input, and a preview that ignores the
        # size it was asked for overflows the pane it was drawn for
        out = W.apply(small, H_total, ow, oh, self.settings)
        raw = out
        out = self._fill_preview(out, H_total, sw, sh, ow, oh, mode=fill)
        # Everything after the fill, as one step, so a deferred fill can run
        # it again on its own result. The marks are copied now: the job may
        # run after they have moved, and is then thrown away, but must not
        # read an array half way through being changed.
        marks = (np.array(self.control_hlines, copy=True),
                 np.array(self.control_lines, copy=True))
        scale = self.scale

        def finish(img):
            return self._finish_after(img, crop, max_edge, ow, s, H_total, marks, scale)
        out = finish(out)
        fit_s = out.shape[1] / float(ow) if ow > 0 else 1.0
        self.preview_map = ("warp", s, H_total, fit_s)
        self._defer_fill(raw, H_total, sw, sh, ow, oh, fill, finish)
        return out

    def _defer_fill(self, raw, H_total, sw, sh, ow, oh, fill, finish):
        """Leave the slow backend's job in ``deferred_fill`` (see render_after).

        ``(raw, hole, settings, finish)``: fill ``raw`` over ``hole`` with
        ``settings``, then ``finish`` the result. Pure on its inputs, so it
        may run off the Tk thread.
        """
        from . import inpaint as FILL
        mode = getattr(self.settings, "fill", "none")
        if (fill != "defer" or mode in FILL.LIVE_MODES
                or not FILL.previews_live(self.settings)):
            return
        hole = W.filled_region(H_total, sw, sh, ow, oh)
        settings = self.settings.replace(fill_max_edge=FILL.PREVIEW_MAX_EDGE)
        b = self._fill_box(raw.shape)
        if b is None:
            self.deferred_fill = (raw.copy(), hole, settings, finish)
            return
        x0, y0, x1, y1 = b
        if not hole[y0:y1, x0:x1].any():
            return
        base = raw.copy()

        def paste(sub):
            img = base.copy()
            img[y0:y1, x0:x1] = sub
            return finish(img)
        self.deferred_fill = (base[y0:y1, x0:x1].copy(), hole[y0:y1, x0:x1], settings, paste)

    def _finish_after(self, out, crop, max_edge, ow, s, H_total, marks, scale):
        """Crop, fit and draw the hand-made marks on a filled after frame."""
        # Fit FIRST so the final array is at display size, then project the
        # markers onto it.  Drawing before _fit would leave the marker coords
        # in OW×OH space while the array is smaller → wrong position.
        out = _fit(crop(out), max_edge)
        fit_s = out.shape[1] / float(ow) if ow > 0 else 1.0
        # Project hand-drawn marker lines through the warp.  H_total maps
        # preview-space → OW×OH output space; scale original→preview before,
        # then scale the result by fit_s to land on the final array.
        if len(marks[0]) > 0 or len(marks[1]) > 0:
            for arr, col in ((marks[0], (80, 160, 80)),
                             (marks[1], (200, 80, 200))):
                if len(arr) == 0:
                    continue
                # control lines are in GRAY px; the warp H_total maps SMALL px
                # (w*s x h*s) -> output. gray -> small is * (s/scale), not * s
                # (off by a factor of `scale` whenever scale != 1.0).
                pts = np.zeros((len(arr), 2, 2), dtype=np.float32)
                for i in range(len(arr)):
                    pts[i, 0] = [arr[i, 0] * s / scale, arr[i, 1] * s / scale]
                    pts[i, 1] = [arr[i, 2] * s / scale, arr[i, 3] * s / scale]
                mapped = cv2.perspectiveTransform(pts.reshape(-1, 1, 2), H_total)
                for i in range(len(arr)):
                    a = (int(mapped[2 * i, 0, 0] * fit_s),
                         int(mapped[2 * i, 0, 1] * fit_s))
                    b = (int(mapped[2 * i + 1, 0, 0] * fit_s),
                         int(mapped[2 * i + 1, 0, 1] * fit_s))
                    cv2.line(out, a, b, col, 2)
        return out

    def source_to_preview(self, x, y):
        """Where a full-resolution source pixel lands on the last render_after
        array, or None (planar view, failed plan, nothing rendered yet)."""
        m = getattr(self, "preview_map", None)
        if m is None:
            return None
        if m[0] == "scale":
            return x * m[1], y * m[1]
        _kind, s, H, fit_s = m
        q = H @ np.array([x * s, y * s, 1.0])
        if abs(q[2]) < 1e-9:
            return None
        return q[0] / q[2] * fit_s, q[1] / q[2] * fit_s

    # The preview fills only what the crop keeps, plus this much of the canvas
    # on each side so a small drag outward still shows picture. Outside it the
    # band stays dark under the veil (user, 2026-10-23: "zu krasser telea
    # Anteil"): on a steep facade most of the canvas is band, and painting all
    # of it showed mostly invented picture that the crop then throws away.
    PREVIEW_FILL_CROP_MARGIN = 0.05

    def _fill_box(self, shape):
        """``(x0, y0, x1, y1)`` in pixels of a ``shape`` frame: the crop plus
        PREVIEW_FILL_CROP_MARGIN, or None for the whole frame. The fill runs on
        this window alone, so the band left dark outside it is never read as
        picture by the fill (it pulled the fill dark at the crop's edge)."""
        r = self.crop_rect
        if r is None:
            return None
        hh, ww = shape[:2]
        m = self.PREVIEW_FILL_CROP_MARGIN
        return (int(max(0.0, r[0] - m) * ww), int(max(0.0, r[1] - m) * hh),
                int(np.ceil(min(1.0, r[2] + m) * ww)), int(np.ceil(min(1.0, r[3] + m) * hh)))

    def _fill_preview(self, out, H_total, sw, sh, ow, oh, mode="full"):
        """Fill the band in the preview -- with the chosen backend, or a stand-in.

        The save path fills unconditionally.  This one has a cost limit, and
        the question is what to show while a slow backend is the chosen one.
        It used to show the raw band: a grey pad where the saved file will have
        picture, on every redraw, with only a line of status text to explain the
        difference.  Now telea stands in (user, 2026-09-22).  It costs 38 ms at
        ``PREVIEW_MAX_EDGE`` against lama's seconds, and it answers the question
        the preview is actually being asked -- *how much is being invented, and
        where* -- which is a question about the HOLE, not about the backend.
        It does not answer *what will it look like*; ``status_text`` says which
        backend is on screen and which one writes the file, so the stand-in is
        never mistaken for the output.

        ``live_fill_preview`` remains the way to see the real thing: then the
        chosen backend runs here and each redraw waits for it, which is what
        ticking that box asks for.

        Order matters and matches ``save()``: warp, then fill, then crop.  A
        refusal above ``--fill-max-share`` comes back as the un-filled band
        rather than an exception, because a preview is not the place to fail.
        """
        from . import inpaint as FILL
        if mode == "none" or getattr(self.settings, "fill", "none") in ("", "none"):
            return out
        preview = self.settings.replace(fill_max_edge=FILL.PREVIEW_MAX_EDGE)
        if mode == "defer" or not FILL.previews_live(self.settings):
            preview = preview.replace(fill=FILL.PREVIEW_STANDIN)
        # A stand-in is 38 ms and wants no indicator; the real backend on the
        # live path is seconds and does. Only the slow case gets the listener,
        # so a slider tick does not flash a bar at every redraw.
        step = (getattr(self, "on_fill_step", None)
                if preview.fill not in FILL.LIVE_MODES else None)
        try:
            hole = W.filled_region(H_total, sw, sh, ow, oh)
            b = self._fill_box(out.shape)
            if b is None:
                filled, _note = FILL.fill(out, hole, preview, on_step=step)
                return filled
            x0, y0, x1, y1 = b
            if not hole[y0:y1, x0:x1].any():
                return out
            sub, _note = FILL.fill(out[y0:y1, x0:x1], hole[y0:y1, x0:x1], preview,
                                   on_step=step)
            filled = out.copy()
            filled[y0:y1, x0:x1] = sub
            return filled
        except FILL.FillUnavailable:
            return out

    def render_pair(self, max_edge=900):
        return self.render_before(max_edge), self.render_after(max_edge)

    def planned_size(self):
        """``(w, h)`` the saved file would have, or ``None`` if nothing warps.

        Geometry only -- `warp.plan` works on the quad, never on the pixels --
        so it is cheap enough to ask on every status refresh.

        It is `output_size`, the size save writes.  It used to plan on its own
        without save's ``keep_size`` and ``keep_centre`` and told the status
        line "saved size 3726x2366" on Platte_1 while the save wrote 2883x1831.
        """
        if self.active_quad() is None:
            roll, pitch, f, _clamped = self.current_angles()
            yaw = self.current_yaw()
            if not f or (abs(roll) < 1e-9 and abs(pitch) < 1e-9
                         and abs(yaw) < 1e-9):
                return None
        return self.output_size()

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

    def state_key(self):
        """Everything a person can change on this photograph, as one value.

        Compared against the value taken when the photograph finished opening
        (and after each Save) to answer "was anything changed?" before another
        photograph replaces this one."""
        import hashlib

        def arr(a):
            if a is None:
                return None
            a = np.ascontiguousarray(a)
            return hashlib.sha1(a.tobytes()).hexdigest() + str(a.shape)

        s = self.settings
        return (self.mode,
                round(self.manual_roll, 6), round(self.manual_pitch, 6),
                round(self.manual_yaw, 6), round(self.manual_focal_35mm, 3),
                arr(self.control_lines), arr(self.control_hlines),
                tuple(sorted(self._crop_edges.items())),
                tuple(map(tuple, self.planar_quad)), self.planar_active,
                self.strip, arr(self.enabled), arr(self.paint), arr(self.sam_mask),
                self.invert_mask, self.mask_enabled,
                getattr(s, "correct_horizontal", None), getattr(s, "fill", None),
                getattr(s, "keep_pixels", None), getattr(s, "mask_mode", None))

    def values_text(self):
        """The correction in numbers, one quiet line for the window's foot
        (user, 2026-10-24: "show correction values ... decent, not too
        flashy"): the angles the warp applies, the focal it rests on and
        where that came from, and the confidence."""
        roll, pitch, f, clamped = self.current_angles()
        yaw = self.current_yaw()
        f35 = M.focal_35mm_from_px(f, self.w, self.h) if f else 0.0
        src = self.model.f_source if self.model else "-"
        conf = self.model.confidence if self.model else 0.0
        sep = "   ·   "
        parts = [f"roll {math.degrees(roll):+.1f}°",
                 f"pitch {math.degrees(pitch):+.1f}°",
                 f"yaw {math.degrees(yaw):+.1f}°",
                 f"f {f35:.0f} mm ({src})",
                 f"conf {conf:.2f}"]
        return sep.join(parts) + (sep + "at the limit" if clamped else "")

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
        if self._rectify_active():
            # The facade-to-rectangle warp is the correction, not roll/pitch/focal
            # (none of which enter it), so the first line names the warp and drops
            # those three.  Every other line is unchanged.
            first = "HORIZONTAL AUTO (warp)  facade -> rectangle, squares kept square"
            if len(self.planar_quad) < 4 and self.ha_fold_quad() is not None:
                first += ("  -- its vanishing line crosses the photo, so the "
                          "canvas is framed around the facade (move the facade "
                          "strip to frame another)")
        else:
            first = (f"{head}  roll={math.degrees(roll):+.2f}deg  "
                     f"pitch={math.degrees(pitch):+.2f}deg{yaw_note}")
        parts = [first,
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
                # Name BOTH backends. The preview is filled either way now, so
                # "runs on save" alone would read as a promise that what is on
                # screen is what gets written -- and it is not: the band you
                # see was drawn by the stand-in.
                parts.append(f"fill: {fill_mode} on save -- the band shown here "
                             f"is {FILL.PREVIEW_STANDIN}, standing in for it; "
                             f"tick 'live' to preview {fill_mode} itself")
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
        preview fell back to the uncorrected photo and Save refused. User,
        2026-10-23: "Warps muessen kontrolliert, nicht verworfen werden" -- and
        squares must stay squares there too ("das ist der haerteste Contest").

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
        # unbounded magnification there (user: "rechtzeitig croppen, darf nicht
        # abschmieren"). The near end stays at least a third of the far end's
        # distance from the vanishing point, so no part of the facade is
        # foreshortened more than FOLD_MAX_FORESHORTEN times the rest.
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
        # A mask makes the crop box: what it keeps + STRIP_CROP_MARGIN (user,
        # 2026-10-24: "Maskierung soll Crop-Box generieren").
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

    def output_size(self):
        """``(ow, oh)`` the next :meth:`save` will actually write, at full
        source resolution -- geometry only, no pixel warp, so it is cheap
        enough to call on every redraw.

        Mirrors ``save()``'s own plan exactly (same angles, same
        ``keep_size=True``, same ``strip``) so the two can never disagree.
        Returns the source size when there is nothing to straighten (the same
        case ``save()`` special-cases into a copy-through) or when the plan is
        degenerate, matching what a save would actually produce in each case.
        """
        if self.active_quad() is not None:
            t = self.planar_homography()
            if t is not None:
                _H, cw, ch = t
                x0, y0, x1, y1 = self.crop_rect or (0.0, 0.0, 1.0, 1.0)
                return int(round((x1 - x0) * cw)), int(round((y1 - y0) * ch))
        roll, pitch, f, _ = self.current_angles()
        yaw = self.current_yaw()
        if abs(roll) < 1e-12 and abs(pitch) < 1e-12 and abs(yaw) < 1e-12:
            return self.w, self.h
        save_settings = self.settings.replace(keep_size=True)
        H = self._build_H(self.w, self.h, f, roll, pitch, yaw, max_area=save_settings.max_area_ratio)
        _segs = self._plan_segs(self.w)
        planned = W.plan(self.w, self.h, H, save_settings, line_segs=_segs, yaw=yaw,
                         strip=self.strip)
        if planned is None:
            return self.w, self.h
        _H, ow, oh = W.keep_centre(planned[0], int(planned[1]), int(planned[2]),
                                   self.w, self.h, _segs)
        return ow, oh

    def save(self, dst_path: str, on_stage=None):
        """Write the corrected image using whatever is currently in force.

        ``on_stage(name, canvas_mpx)`` is called before each slow step. This
        work runs on the caller's thread -- the window is frozen while it does
        -- so the callback exists to let a GUI paint one line saying what is
        happening before it stops answering. Measured on a 12 MPx photograph:
        the warp is 30 ms and the fill 3 to 13 seconds, so "which stage" is the
        whole of the information.
        """
        self.last_fill_note = ""
        def _stage(name, mpx=0.0):
            if on_stage is not None:
                on_stage(name, mpx)
        if self.active_quad() is not None:
            # A placed rectangle, or horizontal auto on a facade whose warp
            # would fold: framed around the facade, never refused.
            return self.save_planar(dst_path, on_stage=on_stage)
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
        # keep_size=True here, only here (2026-09-26, user-directed: "lange
        # Kante gleich viel Pixel, nicht doppelt so viele" -- at False a large
        # yaw grew the long edge well past the source). Tried as a global
        # default first and reverted: it broke four unrelated tests
        # (strip-cropping, the keep_pixels dial, fill sizing) that build a
        # bare Settings() and never meant to opt into this. Scoped to just
        # the save the user actually looks at instead. The "detail (px kept)"
        # dial still decides how far past source size the near edge may push
        # the canvas: 0 = source long edge, 1 = every near-edge pixel kept.
        save_settings = self.settings.replace(keep_size=True)
        H = self._build_H(self.w, self.h, f, roll, pitch, yaw, max_area=save_settings.max_area_ratio)
        _segs = self._plan_segs(self.w)
        planned = W.plan(self.w, self.h, H, save_settings, line_segs=_segs, yaw=yaw,
                         strip=self.strip)
        if planned is None:
            # Was a silent copy of the ORIGINAL (found 2026-09-26 by driving
            # the real window over the Horizontal pool): Save "succeeded" and
            # wrote an uncorrected file. `plan` gives up when the warp folds --
            # at a large yaw part of the frame lies past the vanishing line
            # (Mariánské Hory at 60 deg: quad ~500x the source). Not trimmed
            # to a safe yaw either: that would be a damping. Say so instead.
            raise ValueError(
                f"This correction cannot be drawn: at yaw {math.degrees(yaw):.1f} deg "
                f"the photograph folds past its vanishing line. Lower the yaw, "
                f"or place the facade strip on one wall.")
        H_total, ow, oh, _, _ = planned
        # The motif keeps its pixels (user, 2026-10-23); a pure scale, so the
        # crop fractions taken on the preview still hold.
        H_total, ow, oh = W.keep_centre(H_total, ow, oh, self.w, self.h, _segs)
        mpx = ow * oh / 1e6
        _stage("warping", mpx)
        out = W.apply(self.bgr, H_total, ow, oh, save_settings)
        if getattr(save_settings, "fill", "none") not in ("", "none"):
            from . import inpaint as FILL
            hole = W.filled_region(H_total, self.w, self.h, ow, oh)
            _stage("fill:" + str(save_settings.fill), mpx)
            # The fill is the whole of the wait on a save -- 3 to 13 s against
            # 30 ms for the warp -- so it is the one stage worth a bar rather
            # than a sentence. `on_fill_step` is the same listener the live
            # preview uses, so both paths show one indicator, not two.
            # Kept so the window can say when lama fell back to telea.
            out, self.last_fill_note = FILL.fill(
                out, hole, save_settings, on_step=getattr(self, "on_fill_step", None))
        out = self._apply_crop(out)
        _stage("writing", mpx)
        os.makedirs(os.path.dirname(dst_path) or ".", exist_ok=True)
        IO.save(dst_path, out, self.src, self.settings)
        self._log_correction(dst_path, roll, pitch, f, yaw, save_settings)
        return dst_path

    def _log_correction(self, dst_path, roll, pitch, f, yaw, settings):
        """Real before/after verification, written to `correction_log/`.

        User-directed (2026-09-26): "messe auch nach Korrektur Werte und
        trage sie in Listen ein" -- re-detect horizontals on the SOURCE and
        on the FILE JUST WRITTEN (read back from disk, not the in-memory
        array -- the point is to verify what actually landed on disk), the
        same before/after pattern `cli.py`'s `_hpc_save` already used for the
        batch path. The review window never wrote this; every "before/after"
        number discussed for the GUI path this session was a reconstruction,
        never a measurement. A diagnostic must never break a real save, so
        every failure here is swallowed after `on_stage` has already
        returned -- the photograph is already on disk by this point.
        """
        try:
            from . import correction_log as CLOG
            from .pipeline import measure_horizontals
            from . import __version__
            from .masks import _imread
            before = measure_horizontals(self.bgr, settings, f)
            # _imread, not cv2.imread: the latter returns None for a path
            # with an umlaut, and German street names are ordinary stems.
            out_bgr = _imread(dst_path, cv2.IMREAD_COLOR)
            after = (measure_horizontals(out_bgr, settings, f) if out_bgr is not None
                     else {"n_lines": 0, "yaw_deg": None, "support": 0.0})
            # M-LSD segments of both images, user-directed 2026-09-26: the
            # sidecar holds every segment, the record only the summary.
            lines_before = CLOG.line_records(self.bgr, settings)
            lines_after = (CLOG.line_records(out_bgr, settings) if out_bgr is not None
                           else None)
            before["lines"] = CLOG.line_summary(lines_before)
            if lines_after is not None:
                after["lines"] = CLOG.line_summary(lines_after)
            result = SimpleNamespace(
                src=self.path, status="OK",
                roll_deg=math.degrees(roll), pitch_deg=math.degrees(pitch),
                yaw_deg=math.degrees(yaw),
                confidence=(self.model.confidence if self.model else 0.0),
                focal_35mm=M.focal_35mm_from_px(f, self.w, self.h),
                focal_source=(self.model.f_source if self.model else "none"),
                clamped=False, strip=self.strip)
            rec = CLOG.build_record(result, before, after, __version__)
            folder = "correction_log"
            stem = os.path.splitext(os.path.basename(self.path))[0]
            CLOG.write_record(folder, stem, rec)
            CLOG.write_lines(folder, stem, lines_before, lines_after)
            CLOG.append_csv(folder, CLOG.record_to_row(rec))
        except Exception:
            pass


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
