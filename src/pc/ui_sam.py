"""SAM2 box-prompt segmentation for the review window.

The nine methods by which ``ReviewPanel`` (in :mod:`pc.gui`) drags a box
over the before picture, runs SAM2 on it in a worker thread, and draws the
prompt box, the point prompts and the selection result live here on a
mixin.  This module never imports ``pc.gui``: it needs only the standard
library, ``sam2seg`` for the segmentation, and the live ``OVERLAY`` palette
from ``ui_theme`` (shared by object identity with ``gui.py``'s re-export).
"""
from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger("pc.gui")

from . import sam2seg
from .ui_theme import OVERLAY

_SHIFT_MASK = 0x0001  # Tk reports Shift as bit 0 on every platform


class _SamMixin:
    """SAM2 box-prompt segmentation, mixed into ``ReviewPanel``.

    The host supplies the state the tool keeps (``v_sam``, ``_sam_box``,
    ``_sam_box_px``, ``_sam_points``, ``_sam_selection``,
    ``_sam_ignore_full``, ``_busy_sam``), the canvas geometry
    (``c_before``, ``_before_off``, ``_before_scale``), ``session`` and the
    helpers the methods call (``_notify``, ``_exclusive``, ``_redraw``,
    ``_set_busy``, ``_wait_stage``, ``_wait_hide``, ``_app``,
    ``_schedule_after``, ``_sync_from_session``, ``_set_status``); the mixin
    adds only the drag handlers, the worker-thread round trip to
    ``sam2seg`` and the prompt drawing.
    """

    # -- SAM2 box-prompt segmentation --------------------------------------
    def _on_sam_toggle(self):
        """Entering or leaving box-select; the caller owns the flip.

        It used to flip `v_sam` itself, which was fine while a plain Button
        was the only way in.  A palette toggle *button* carries the variable,
        so Tk flips it first and a second flip here would cancel it out and
        the tool would look dead.  Read-only, like the other three.
        """
        if self.v_sam.get():
            # Nothing to run it with: say so and where it comes from, rather
            # than let a drag end in an error nobody can act on.
            if not (sam2seg.local_available() or sam2seg.available()):
                self.v_sam.set(False)
                self._notify("SAM is not installed -- install it with 'deps' at the "
                             "bottom right (box select, ~200 MB).")
                return
            self._exclusive("v_sam")
        else:
            self._sam_box = None
            self._sam_box_px = None
            self._sam_points = []
            self._sam_selection = None
            self.c_before.delete("sam_prompts")
            self._redraw()

    def _on_sam_press(self, event):
        if not self.v_sam.get():
            return
        ox, oy = self._before_off
        x = (event.x - ox) / self._before_scale
        y = (event.y - oy) / self._before_scale
        self._sam_drag_start = (x, y)
        # Shift+drag adds to the selection: the new segment joins the old one
        # and the crop box grows to cover both boxes.
        self._sam_adding = (bool(event.state & _SHIFT_MASK)
                            and getattr(self.session, "sam_mask", None) is not None)
        if not self._sam_adding:
            self._sam_box = None

    def _on_sam_drag(self, event):
        if not self.v_sam.get() or not hasattr(self, '_sam_drag_start'):
            return
        ox, oy = self._before_off
        x0, y0 = self._sam_drag_start
        x1 = (event.x - ox) / self._before_scale
        y1 = (event.y - oy) / self._before_scale
        self.c_before.delete("sam_prompts")
        # Canvas coordinates are origin PLUS image coordinates times the display
        # scale.  The press divided by the scale to reach image space; adding the
        # offset alone left the rubber band short of the cursor by that factor --
        # measured 36 to 107 px adrift at scale 1.553, which is what "the box is
        # somewhere else than the mouse" was.  `_draw_sam_prompts` had it right
        # all along, which is why the box jumped into place on release.
        sc = self._before_scale
        cx0, cy0 = min(x0, x1), min(y0, y1)
        cx1, cy1 = max(x0, x1), max(y0, y1)
        self.c_before.create_rectangle(ox + cx0 * sc, oy + cy0 * sc,
                                       ox + cx1 * sc, oy + cy1 * sc,
                                       outline=OVERLAY["sam"], width=2, tags="sam_prompts")

    def _on_sam_release(self, event):
        if not self.v_sam.get() or not hasattr(self, '_sam_drag_start'):
            return
        ox, oy = self._before_off
        x0, y0 = self._sam_drag_start
        del self._sam_drag_start
        x1 = (event.x - ox) / self._before_scale
        y1 = (event.y - oy) / self._before_scale
        cx0, cy0 = min(x0, x1), min(y0, y1)
        cx1, cy1 = max(x0, x1), max(y0, y1)
        if (cx1 - cx0) < sam2seg.MIN_BOX_PX or (cy1 - cy0) < sam2seg.MIN_BOX_PX:
            return  # a stray click, not a selection -- see sam2seg.MIN_BOX_PX
        # SAM2 expects pixel coordinates [x0, y0, x1, y1], not normalised.
        # The child script feeds the array straight to pred.predict(box=...),
        # which treats values as pixels -- 0.15 means 0.15 px, i.e. the corner,
        # and the model selects the whole frame.
        W, H = self.session.w, self.session.h
        self._sam_box = (cx0 / W, cy0 / H, cx1 / W, cy1 / H)  # keep normalised for drawing
        self._sam_box_px = (int(cx0), int(cy0), int(cx1), int(cy1))  # pixels for SAM2
        self._draw_sam_prompts()
        self._on_sam_apply()

    def _on_sam_apply(self):
        if self._sam_box is None:
            return
        box_px = getattr(self, '_sam_box_px', None) or self._sam_box
        pts = getattr(self, '_sam_points', None) or None
        self._busy_sam = True
        self._set_busy(True)
        # SAM2 runs in a thread, so the watch cursor does move -- but a cursor
        # says "busy" and not "with what, and for how long".
        self._wait_stage("sam")

        local = sam2seg.local_available()
        app = self._app()

        def back(fn):
            # Through the App's queue when there is one: `after` from a worker
            # thread is unreliable in Tk (the deps menu found it), so the
            # result could sit there with the wait notice up for good.
            if app is not None:
                app.queue.put(("call", fn))
            else:
                self._schedule_after(0, fn)

        def _run():
            try:
                log.info("SAM apply: box_px=%s pts=%d image=%s local=%s",
                         list(box_px), len(pts or ()),
                         os.path.basename(self.session.path), local)
                if local:
                    ignore = sam2seg.segment_local(self.session.bgr, box_px)
                else:
                    png = sam2seg.run_subprocess(
                        self.session.path, box_px, pts,
                        ckpt=getattr(self.session.settings, "sam_model", ""),
                        device=getattr(self.session.settings, "sam_device", ""))
                    ignore = sam2seg.load_mask_png(png, (self.session.h, self.session.w))
                log.info("SAM apply done")
                back(lambda m=ignore, b=box_px: self._on_sam_done(m, b))
            except Exception as exc:
                log.exception("SAM apply failed")
                back(lambda e=exc: self._on_sam_fail(e))

        threading.Thread(target=_run, daemon=True).start()

    def _on_sam_done(self, ignore, box=None):
        self._busy_sam = False
        self._set_busy(False)
        self._wait_hide()
        # The last full-resolution segment, valid only while the session still
        # holds a SAM selection (`_sam_selection` is reset by every redraw).
        prev_ign = (getattr(self, "_sam_ignore_full", None)
                    if getattr(self.session, "sam_mask", None) is not None else None)
        prev_box = getattr(self.session, "sam_box", None)
        if (getattr(self, "_sam_adding", False) and prev_ign is not None
                and prev_ign.shape == ignore.shape):
            ignore = ignore & prev_ign              # kept = old kept OR new kept
            if box is not None and prev_box is not None:
                box = (min(box[0], prev_box[0]), min(box[1], prev_box[1]),
                       max(box[2], prev_box[2]), max(box[3], prev_box[3]))
            if box is not None:
                W_, H_ = self.session.w, self.session.h
                self._sam_box = (box[0] / W_, box[1] / H_, box[2] / W_, box[3] / H_)
        self._sam_adding = False
        self._sam_ignore_full = ignore
        self._sam_selection = ~ignore
        # Hand the result to the session, which is the whole point of selecting
        # the building: until this call the segment only drew a green outline
        # and a percentage, and the estimator never saw it.  `apply_sam_mask`
        # resizes to analysis-res and refits.
        self.session.apply_sam_mask(ignore, box)
        # The dragged box is the crop box: a new selection is a new framing,
        # so it is applied even over a hand crop -- and said, with how to
        # change it.
        self.session.crop_is_auto = True
        if box is not None and self.session.auto_crop():
            self.session.crop_is_auto = True
            if getattr(self, "v_autocrop", None) is not None:
                self.v_autocrop.set(True)
            self._notify("crop set to the SAM box -- drag its edges to widen it")
        self._sync_from_session()
        self._draw_sam_prompts()
        frac = self._sam_selection.mean() if self._sam_selection is not None else 0
        self._set_status(f"SAM selection: {frac:.1%} of frame")

    def _on_sam_fail(self, exc):
        self._busy_sam = False
        self._set_busy(False)
        self._wait_hide()
        log.error("SAM failed: %s", exc)
        self._set_status(f"SAM error: {exc}")

    def _draw_sam_prompts(self):
        self.c_before.delete("sam_prompts")
        if not self.v_sam.get():
            return
        ox, oy = self._before_off
        s = self._before_scale
        # Box
        if self._sam_box is not None:
            x0, y0, x1, y1 = self._sam_box
            self.c_before.create_rectangle(ox + x0 * self.session.w * s, oy + y0 * self.session.h * s,
                                           ox + x1 * self.session.w * s, oy + y1 * self.session.h * s,
                                           outline=OVERLAY["sam"], width=2, tags="sam_prompts")
        # Points
        for px, py, pos in (getattr(self, '_sam_points', None) or []):
            col = OVERLAY["sam"] if pos else OVERLAY["sam_neg"]
            self.c_before.create_oval(ox + px * s - 4, oy + py * s - 4,
                                      ox + px * s + 4, oy + py * s + 4,
                                      fill=col, outline="", tags="sam_prompts")
        # Selection indicator: green border in the off-border area
        sel = getattr(self, "_sam_selection", None)
        if sel is not None and sel.any():
            cw = self.c_before.winfo_width()
            ch = self.c_before.winfo_height()
            self.c_before.create_rectangle(2, 2, cw - 2, ch - 2,
                                           outline=OVERLAY["sam"], width=3,
                                           tags="sam_prompts")

    def _on_sam_right_click(self, event):
        if not self.v_sam.get():
            return
        self._sam_box = None
        self._sam_box_px = None
        self._sam_points = []
        self._sam_selection = None
        self.c_before.delete("sam_prompts")
