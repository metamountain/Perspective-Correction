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
from . import model as M
from . import preview as PV
from . import warp as W

AUTO, MANUAL = "auto", "manual"


class ReviewSession:
    """Everything needed to review and fix one image."""

    def __init__(self, path: str, settings, image=None):
        self.path = path
        self.settings = settings
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
        self.manual_roll = 0.0
        self.manual_pitch = 0.0
        self.manual_focal_35mm = 0.0
        # Hugin-style vertical control lines, in analysis-image coordinates.
        # (N, 4) of x0, y0, x1, y1.
        self.control_lines = np.zeros((0, 4))
        # Manual crop, as fractions of the corrected canvas rather than pixels.
        # The preview is rendered at a few hundred pixels and the file is saved
        # at full size, so a rectangle in pixels would mean two different things;
        # fractions mean the same thing at every scale.
        self.crop_rect = None            # (x0, y0, x1, y1) in 0..1
        self.model = None
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
        # per-line manual state: True = may be used, False = struck out by the user
        self.enabled = np.ones(len(self.vert), dtype=bool)

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

    @property
    def mask_active(self):
        info = getattr(self, "detect_info", None) or {}
        return info.get("mask") is not None

    # -- fitting ---------------------------------------------------------
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
        if len(self.control_lines) >= 2:
            vert = L.LineSet(self.control_lines)
            # two lines already determine a vanishing point, and the floor of
            # four exists to keep the detector from fitting noise -- which is
            # not what these are
            settings = settings.replace(min_vertical_lines=2)
        else:
            vert = self.vert.subset(self.enabled)
        exif_px = IO.focal_px_from_exif(self.src, self.w, self.h) \
            if self.settings.use_exif_focal else None
        m = M.estimate(vert, self.horiz, gw, gh, settings,
                       exif_px * self.scale if exif_px else None)
        if m.f:
            m.f = m.f / self.scale
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
            self.manual_roll, self.manual_pitch = m.roll, m.pitch
            self.manual_focal_35mm = M.focal_35mm_from_px(m.f, self.w, self.h) if m.f else 0.0
        return m

    # -- manual controls -------------------------------------------------
    def set_manual(self, roll_deg=None, pitch_deg=None, focal_35mm=None):
        self.mode = MANUAL
        if roll_deg is not None:
            self.manual_roll = math.radians(roll_deg)
        if pitch_deg is not None:
            self.manual_pitch = math.radians(pitch_deg)
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
            self.manual_roll, self.manual_pitch = self.model.roll, self.model.pitch
            self.manual_focal_35mm = (M.focal_35mm_from_px(self.model.f, self.w, self.h)
                                      if self.model.f else 0.0)
        return self.model

    def reset_to_auto(self):
        self.mode = AUTO
        self.enabled[:] = True
        self.control_lines = np.zeros((0, 4))
        self.crop_rect = None
        self.refit()

    @property
    def control_active(self):
        """True when the user's own verticals are driving the fit."""
        return len(self.control_lines) >= 2

    MIN_CONTROL_LENGTH_FRAC = 0.08

    def add_control_line(self, x0, y0, x1, y1, display_scale: float = 1.0):
        """Assert that this segment is vertical in the world -- Hugin's ``t2``.

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
        self.control_lines = np.vstack([self.control_lines, seg])
        self.refit()
        return len(self.control_lines) - 1

    def remove_control_line(self, index: int):
        if not (0 <= index < len(self.control_lines)):
            return False
        self.control_lines = np.delete(self.control_lines, index, axis=0)
        self.refit()
        return True

    def clear_control_lines(self):
        had = len(self.control_lines)
        self.control_lines = np.zeros((0, 4))
        if had:
            self.refit()
        return had

    def pick_control_line(self, x: float, y: float, display_scale: float = 1.0,
                          radius: float = 12.0):
        """Index of the control line nearest a click, or ``None``."""
        return self._nearest(self.control_lines, x, y, display_scale, radius)

    def control_lines_for_display(self, display_scale: float = 1.0):
        """The control lines in displayed-image pixels, for drawing."""
        if len(self.control_lines) == 0:
            return np.zeros((0, 4))
        return self.control_lines * (display_scale / max(self.scale, 1e-9))

    def set_crop_rect(self, x0, y0, x1, y1, shown_w, shown_h):
        """Crop the corrected image by hand, in displayed-pixel coordinates.

        Deliberately applied *after* the correction rather than instead of it.
        The automatic crop has to guess how much of the frame is worth trading
        for straight verticals; once the whole frame is kept and padded, that
        trade becomes a thing you can see, and dragging a rectangle over it is a
        better answer than any threshold.  It is also the only way to keep an
        asymmetric composition -- the automatic rectangle is anchored on the
        image centre, and a photographer who framed the building off-centre
        wants to keep it there.
        """
        if shown_w <= 0 or shown_h <= 0:
            return False
        x0, x1 = sorted((float(x0) / shown_w, float(x1) / shown_w))
        y0, y1 = sorted((float(y0) / shown_h, float(y1) / shown_h))
        x0, y0 = max(0.0, x0), max(0.0, y0)
        x1, y1 = min(1.0, x1), min(1.0, y1)
        if (x1 - x0) < 0.05 or (y1 - y0) < 0.05:
            return False                 # a stray click, not a crop
        self.crop_rect = (x0, y0, x1, y1)
        return True

    def clear_crop_rect(self):
        had = self.crop_rect is not None
        self.crop_rect = None
        return had

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

    # -- current correction ----------------------------------------------
    def current_angles(self):
        """``(roll, pitch, focal_px)`` actually in force, limits applied."""
        if self.mode == MANUAL:
            roll, pitch = self.manual_roll, self.manual_pitch
            f35 = self.manual_focal_35mm
            f = M.focal_px_from_35mm(f35, self.w, self.h) if f35 > 0 else (
                self.model.f if self.model and self.model.f
                else M.focal_px_from_35mm(self.settings.default_focal_35mm, self.w, self.h))
            return roll, pitch, f, False
        if self.model is None or not self.model.f:
            return 0.0, 0.0, M.focal_px_from_35mm(self.settings.default_focal_35mm,
                                                  self.w, self.h), False
        guessed = self.model.f_source in ("default", "prior", "none", "refined")
        roll, pitch, clamped = W.limit(self.model.roll, self.model.pitch,
                                       self.settings, guessed)
        return roll, pitch, self.model.f, clamped

    def would_skip(self):
        """``None`` if the image would be corrected, else the reason it would not.

        Vertical control lines count as a decision, exactly like moving a
        slider.  Without this the feature defeats itself: the confidence score
        is largely a count of supporting lines, two is far below what it expects
        of a detector, and a photograph the user had just told the truth about
        came back "SKIP, conf=0.04, weakest: count".  Refusing evidence because
        there is little of it is right when a detector produced it and wrong
        when a person did.
        """
        if self.mode == MANUAL or self.control_active:
            return None
        if self.model is None:
            return "no model"
        if self.model.confidence < self.settings.min_confidence:
            weakest = self.model.diagnostics.get("weakest_term", "")
            return (self.model.diagnostics.get("reason", "low confidence") +
                    f" (conf={self.model.confidence:.2f}" +
                    (f"; weakest: {weakest})" if weakest else ")"))
        roll, pitch, _, _ = self.current_angles()
        if math.degrees(math.hypot(roll, pitch)) < self.settings.min_correction_deg:
            return "already upright"
        return None

    # -- rendering -------------------------------------------------------
    def render_before(self, max_edge=900, show_lines=True):
        img = self.bgr
        if not show_lines:
            return _fit(img, max_edge)
        struck = self.vert.subset(~self.enabled)
        used = self.vert.subset(self.enabled)
        m = self.model
        canvas = self.bgr.copy()
        inv = 1.0 / self.scale
        info = getattr(self, "detect_info", None) or {}
        if self.show_mask and self.mask_alpha > 0.001:
            PV.tint_mask(canvas, info.get("mask"), alpha=self.mask_alpha)
            dropped = info.get("masked_out")
            if dropped is not None and len(dropped):
                PV._draw_lines(canvas, dropped * inv, PV.RED, 1)
        if len(self.horiz):
            PV._draw_lines(canvas, self.horiz.seg * inv, PV.BLUE, 1)
        if len(struck):
            PV._draw_lines(canvas, struck.seg * inv, PV.GREY, 1)
        if len(used):
            inl = m.vert_inliers[self.enabled] if m is not None else np.zeros(len(used), bool)
            PV._draw_lines(canvas, used.seg[~inl] * inv, PV.YELLOW, 1)
            PV._draw_lines(canvas, used.seg[inl] * inv, PV.GREEN, 2)
        if m is not None and m.f:
            K = G.intrinsics(m.f, self.w / 2.0, self.h / 2.0)
            PV._draw_infinite_line(canvas, G.horizon_line(m.up, K), PV.MAGENTA, 2)
        return _fit(canvas, max_edge)

    def render_after(self, max_edge=900):
        roll, pitch, f, _ = self.current_angles()
        if abs(roll) < 1e-9 and abs(pitch) < 1e-9:
            return _fit(self._apply_crop(self.bgr), max_edge)
        # render the preview from a reduced copy: a 24 MP warp per slider tick
        # is unusable, and the geometry is scale invariant apart from f
        s = min(1.0, float(max_edge) / max(self.w, self.h))
        small = cv2.resize(self.bgr, (max(1, int(self.w * s)), max(1, int(self.h * s))),
                           interpolation=cv2.INTER_AREA) if s < 1.0 else self.bgr
        sh, sw = small.shape[:2]
        H = W.build(sw, sh, f * s, roll, pitch)
        planned = W.plan(sw, sh, H, self.settings)
        if planned is None:
            return _fit(self.bgr, max_edge)
        H_total, ow, oh, _, _ = planned
        # fit again: with crop="auto" the plan may keep the whole frame and pad
        # it, which is *larger* than the input, and a preview that ignores the
        # size it was asked for overflows the pane it was drawn for
        out = self._apply_crop(W.apply(small, H_total, ow, oh, self.settings))
        return _fit(out, max_edge)

    def render_pair(self, max_edge=900):
        return self.render_before(max_edge), self.render_after(max_edge)

    def status_text(self):
        roll, pitch, f, clamped = self.current_angles()
        f35 = M.focal_35mm_from_px(f, self.w, self.h) if f else 0.0
        skip = self.would_skip()
        conf = self.model.confidence if self.model else 0.0
        src = self.model.f_source if self.model else "-"
        head = ("MANUAL" if self.mode == MANUAL
                else "MARKED" if self.control_active
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
        parts = [f"{head}  roll={math.degrees(roll):+.2f}deg  pitch={math.degrees(pitch):+.2f}deg",
                 f"f={f35:.0f}mm ({src if self.mode == AUTO else 'manual'})  {conf_note}  "
                 f"lines={int(self.enabled.sum())}/{len(self.vert)}"]
        parts.append(mask_note)
        if self.control_active:
            parts.append(f"{len(self.control_lines)} vertical control line(s) in "
                         f"force -- the detected verticals are not being used")
        elif len(self.control_lines) == 1:
            parts.append("1 vertical control line -- one more is needed before "
                         "they take over, since two determine a vanishing point")
        if clamped:
            parts.append("correction hit the configured limit")
        if skip:
            parts.append(f"would skip: {skip}")
        return "\n".join(parts)

    # -- output ----------------------------------------------------------
    def save(self, dst_path: str):
        """Write the corrected image using whatever is currently in force."""
        roll, pitch, f, _ = self.current_angles()
        if abs(roll) < 1e-12 and abs(pitch) < 1e-12 and self.crop_rect is None:
            IO.copy_through(self.path, dst_path)
            return dst_path
        if abs(roll) < 1e-12 and abs(pitch) < 1e-12:
            # nothing to straighten, but the user cropped by hand
            out = self._apply_crop(self.bgr)
            os.makedirs(os.path.dirname(os.path.abspath(dst_path)) or ".", exist_ok=True)
            IO.save(dst_path, out, self.src, self.settings)
            return dst_path
        H = W.build(self.w, self.h, f, roll, pitch)
        planned = W.plan(self.w, self.h, H, self.settings)
        if planned is None:
            IO.copy_through(self.path, dst_path)
            return dst_path
        H_total, ow, oh, _, _ = planned
        out = self._apply_crop(W.apply(self.bgr, H_total, ow, oh, self.settings))
        os.makedirs(os.path.dirname(os.path.abspath(dst_path)) or ".", exist_ok=True)
        IO.save(dst_path, out, self.src, self.settings)
        return dst_path


def _fit(img, max_edge):
    s = min(1.0, float(max_edge) / max(img.shape[:2]))
    if s >= 1.0:
        return img
    return cv2.resize(img, (max(1, int(img.shape[1] * s)), max(1, int(img.shape[0] * s))),
                      interpolation=cv2.INTER_AREA)
