"""The after pane's crop rectangle and reference guides.

The fourteen crop interaction and drawing methods of ``ReviewPanel`` (in
:mod:`pc.gui`) and the twelve after-pane guide and crosshair methods live
here on a mixin.  This module never imports ``pc.gui``: it needs only
``tkinter`` (``tk.TclError`` from the grab bookkeeping), the guide geometry
from ``layout`` and the live ``GUIDE_GREY`` / ``OVERLAY`` palettes from
``ui_theme`` (shared by object identity with ``gui.py``'s re-exports).
"""
from __future__ import annotations

import tkinter as tk

from . import layout
from .ui_theme import GUIDE_GREY, OVERLAY


class _CropGuidesMixin:
    """The after pane's crop rectangle and reference guides, mixed into
    ``ReviewPanel``.

    The host supplies the state the tools keep (``_after_guides``,
    ``_guide_drag``, ``_guide_new``, ``_crop_drag_start``), the after
    canvas (``c_after``) and its photo (``_ph_a``), the preview geometry
    (``_after_off``) and the full-resolution ``session``; the mixin adds
    only the crop drag machinery and the guide and crosshair machinery,
    keeping its own ``_cross_pull``.
    """

    def _clear_crop(self):
        if self.session is not None and self.session.clear_crop_rect():
            self._refresh_crop()

    def _auto_crop(self):
        """The switch was moved: crop now, or give the whole frame back."""
        if self.session is None:
            return
        if not self.v_autocrop.get():
            if getattr(self.session, "crop_is_auto", False):
                self.session.clear_crop_rect()
                self.session.crop_is_auto = False
                self._refresh_crop()
            self._set_status_extra("auto crop off -- the whole corrected frame "
                                   "is kept, band and all")
            return
        if self.session.auto_crop():
            self.session.crop_is_auto = True
            self._refresh_crop()
            kept = (1.0 - self.session.crop_loss()) * 100.0
            self._set_status_extra(f"cropped -- keeps {kept:.0f}% of the frame, "
                                   f"cuts {100.0 - kept:.0f}%")
        else:
            self._set_status_extra("nothing to trim -- the correction opened no "
                                   "band, or the plan had already cropped it")

    def _autocrop_follow(self):
        """Re-cut after the correction changed, while the switch is on.

        Called from `_sync_from_session`, which is the one place every change
        to the angles, the strip or the mode passes through. `refresh_auto_crop`
        refuses to touch a crop the user drew, so this cannot eat a decision.
        """
        s = self.session
        if s is None or getattr(self, "v_autocrop", None) is None:
            return
        if not self.v_autocrop.get():
            return
        if s.refresh_auto_crop():
            self._refresh_crop()

    def _crop_taken_by_hand(self):
        """A dragged crop switches the automatic one off, and says so."""
        if getattr(self, "v_autocrop", None) is not None and self.v_autocrop.get():
            self.v_autocrop.set(False)
            self._set_status_extra("auto crop off -- your rectangle stands")

    def _refresh_crop(self):
        """Re-bake the veil for a changed crop, without re-rendering the image.

        The picture behind it cannot have changed -- the crop is applied on
        save, not in the preview -- so a full redraw would re-warp and, with a
        live fill, re-inpaint an image identical to the one already on screen.
        That pause is itself a kind of jump.  Only the flat veil is recomposed from
        the cached un-veiled frame, which is what ``_show_after`` does.
        """
        if getattr(self, "_ph_a", None) is None:
            self._schedule_redraw()      # nothing on screen to draw over yet
            return
        self._show_after(self.session.crop_rect)
        self._set_status(self.session.status_text())

    def _on_crop_press(self, event):
        if getattr(self, "_ph_a", None) is None:
            return
        # Every gesture that starts here (handle drag, guide pull, rectangle
        # drag) ends in a session change, so the snapshot goes down before
        # the first pixel of it.
        self._push_undo()
        x = event.x - self._after_off[0]
        y = event.y - self._after_off[1]
        iw, ih = self._ph_a.width(), self._ph_a.height()
        # Crop handles (4 corners + 4 mid-edges) always have dominance.
        grab = self._grab_handle(x, y, iw, ih)
        if grab is not None:
            self._crop_drag_start = grab
            return
        # Reference guides: grab an existing one, else pull a new one from
        # the band around the picture's edge.
        hit = self._after_guide_at(x, y, iw, ih)
        if hit is not None:
            kind, idx = hit
            self._guide_drag = (kind, idx)
            try:
                self.c_after.grab_set()
            except tk.TclError:
                pass
            return
        # The guide band: GUIDE_BAND_PX outside and inside every picture edge.
        # A press there creates a guide and is already dragging it, so the
        # gesture is one pull; `_on_guide_release` drops it again if the press
        # never travelled (a click is not a pull).
        band = self._guide_band_kind(x, y, iw, ih)
        if band is not None:
            pos = float(x if band == "v" else y)
            self._after_guides.append((band, pos))
            self._guide_drag = (band, len(self._after_guides) - 1)
            self._guide_new = pos
            try:
                self.c_after.grab_set()
            except tk.TclError:
                pass
            self._draw_after_guides()
            return
        # A press inside the kept region pans the whole rectangle; a press in the
        # cut-away area (or on an uncropped frame) draws a new one from scratch.
        rect = self.session.crop_rect or (0.0, 0.0, 1.0, 1.0)
        cx0, cy0, cx1, cy1 = (rect[i] * (iw if i % 2 == 0 else ih) for i in range(4))
        if cx0 <= x <= cx1 and cy0 <= y <= cy1:
            self._crop_drag_start = ("move", x - cx0, y - cy0)
        else:
            self._crop_drag_start = ("corner", x, y)

    def _guide_band_kind(self, x, y, iw, ih):
        """'v' or 'h' when (x, y), in after-image pixels, lies in the guide band
        around the picture's edge, else None.  Left/right edges give a vertical
        guide, top/bottom a horizontal one; in a corner the nearer edge wins."""
        b = layout.GUIDE_BAND_PX
        if not (-b <= x <= iw + b and -b <= y <= ih + b):
            return None
        dx = min(abs(x), abs(iw - x))
        dy = min(abs(y), abs(ih - y))
        if dx > b and dy > b:
            return None
        return "v" if dx <= dy else "h"

    def _grab_handle(self, x, y, iw, ih, radius=14):
        """Answer a press on a crop handle with what the drag should do.

        A corner answers with the *opposite* corner: that point then plays
        exactly the role the first click plays when a rectangle is drawn from
        nothing, so adjusting an existing crop and drawing a new one are one
        drag implementation rather than two.  An edge midpoint answers with
        its own name; the drag then moves just that edge along its axis --
        top and bottom vertically, left and right horizontally -- which is how
        you nudge one side without re-placing the other three.

        The same full-frame default `_show_after` draws, so the
        handles it shows on an uncropped photograph are the handles this
        grabs.  Drawing a handle nobody can pick up is worse than drawing none.
        """
        x0, y0, x1, y1 = self.session.crop_rect or (0.0, 0.0, 1.0, 1.0)
        cx0, cy0, cx1, cy1 = x0 * iw, y0 * ih, x1 * iw, y1 * ih
        for (hx, hy), opposite in (((cx0, cy0), (cx1, cy1)),
                                   ((cx1, cy0), (cx0, cy1)),
                                   ((cx0, cy1), (cx1, cy0)),
                                   ((cx1, cy1), (cx0, cy0))):
            if abs(x - hx) <= radius and abs(y - hy) <= radius:
                return ("corner",) + opposite
        for name, hx, hy in (("top", 0.5 * (cx0 + cx1), cy0),
                             ("bottom", 0.5 * (cx0 + cx1), cy1),
                             ("left", cx0, 0.5 * (cy0 + cy1)),
                             ("right", cx1, 0.5 * (cy0 + cy1))):
            if abs(x - hx) <= radius and abs(y - hy) <= radius:
                return ("edge", name)
        return None

    def _edge_drag_rect(self, name, x, y, iw, ih):
        """The rectangle a mid-edge drag produces: the stored crop with just
        that edge moved to the cursor, clamped to the frame and so it cannot
        cross its opposite edge.  The other three edges are untouched."""
        x0, y0, x1, y1 = self.session.crop_rect or (0.0, 0.0, 1.0, 1.0)
        rx0, ry0, rx1, ry1 = x0 * iw, y0 * ih, x1 * iw, y1 * ih
        if name == "top":
            ry0 = max(0.0, min(y, ry1))
        elif name == "bottom":
            ry1 = min(ih, max(y, ry0))
        elif name == "left":
            rx0 = max(0.0, min(x, rx1))
        else:
            rx1 = min(iw, max(x, rx0))
        return rx0, ry0, rx1, ry1

    def _move_drag_rect(self, offx, offy, x, y, iw, ih):
        """The rectangle a drag-inside produces: the stored crop translated so the
        grabbed point tracks the cursor, then clipped to the frame.  Push it past
        an edge and that border becomes the new crop border -- what went outside
        is cut away rather than dragging along for the ride."""
        x0, y0, x1, y1 = self.session.crop_rect or (0.0, 0.0, 1.0, 1.0)
        w = (x1 - x0) * iw
        h = (y1 - y0) * ih
        mx0, my0 = x - offx, y - offy
        rx0 = max(0.0, min(iw, mx0))
        ry0 = max(0.0, min(ih, my0))
        rx1 = max(rx0, min(iw, mx0 + w))
        ry1 = max(ry0, min(ih, my0 + h))
        return rx0, ry0, rx1, ry1

    def _on_crop_motion(self, event):
        """Show the move cursor over the kept region so a pan is discoverable;
        the default pointer everywhere else.  A handle keeps its own grab -- the
        interior is where the whole rectangle moves."""
        if getattr(self, "_ph_a", None) is None:
            return
        x = event.x - self._after_off[0]
        y = event.y - self._after_off[1]
        iw, ih = self._ph_a.width(), self._ph_a.height()
        # The guide band shows which way the guide will run; a crop handle,
        # which outranks it on press, keeps the plain pointer.
        band = (self._guide_band_kind(x, y, iw, ih)
                if self._grab_handle(x, y, iw, ih) is None else None)
        if band is not None:
            cur = "sb_h_double_arrow" if band == "v" else "sb_v_double_arrow"
        else:
            move = False
            if 0 <= x <= iw and 0 <= y <= ih:
                rect = self.session.crop_rect or (0.0, 0.0, 1.0, 1.0)
                cx0, cy0, cx1, cy1 = (rect[i] * (iw if i % 2 == 0 else ih) for i in range(4))
                not_full = (cx0 > 0 or cy0 > 0 or cx1 < iw or cy1 < ih)
                if (cx0 <= x <= cx1 and cy0 <= y <= cy1 and not_full
                        and self._grab_handle(x, y, iw, ih) is None):
                    move = True
            cur = "fleur" if move else ""
        if self.c_after.cget("cursor") != cur:
            self.c_after.config(cursor=cur)

    def _on_crop_drag(self, event):
        if self._guide_drag is not None:
            self._on_guide_drag(event)
            return
        if self._crop_drag_start is None or getattr(self, "_ph_a", None) is None:
            return
        self.c_after.delete("crop_overlay")
        x1 = event.x - self._after_off[0]
        y1 = event.y - self._after_off[1]
        iw, ih = self._ph_a.width(), self._ph_a.height()
        x1 = max(0, min(iw, x1))
        y1 = max(0, min(ih, y1))
        if self._crop_drag_start[0] == "edge":
            rx0, ry0, rx1, ry1 = self._edge_drag_rect(self._crop_drag_start[1],
                                                      x1, y1, iw, ih)
        elif self._crop_drag_start[0] == "move":
            rx0, ry0, rx1, ry1 = self._move_drag_rect(self._crop_drag_start[1],
                                                      self._crop_drag_start[2],
                                                      x1, y1, iw, ih)
        else:
            rx0, ry0, rx1, ry1 = (self._crop_drag_start[1], self._crop_drag_start[2],
                                  x1, y1)
        # Re-bake the veil with the in-progress rectangle; session.crop_rect is still
        # the last settled one, so the live rect has to be passed as fractions.
        self._show_after((rx0 / iw, ry0 / ih, rx1 / iw, ry1 / ih))

    def _on_crop_release(self, event):
        if self._guide_drag is not None:
            self._on_guide_release(event)
            return
        if self._crop_drag_start is None or getattr(self, "_ph_a", None) is None:
            return
        x1 = event.x - self._after_off[0]
        y1 = event.y - self._after_off[1]
        iw, ih = self._ph_a.width(), self._ph_a.height()
        x1 = max(0, min(iw, x1))
        y1 = max(0, min(ih, y1))
        kind = self._crop_drag_start[0]
        start = self._crop_drag_start
        self._crop_drag_start = None
        cur = self.session.crop_rect or (0.0, 0.0, 1.0, 1.0)
        if kind == "edge":
            rx0, ry0, rx1, ry1 = self._edge_drag_rect(start[1], x1, y1, iw, ih)
        elif kind == "move":
            rx0, ry0, rx1, ry1 = self._move_drag_rect(start[1], start[2], x1, y1, iw, ih)
        else:
            rx0, ry0, rx1, ry1 = (start[1], start[2], x1, y1)
        if kind == "corner":
            # A click that never moved is a click, not a failed crop.  Now that
            # the rectangle is always live, saying "too small" on every stray
            # press in the after pane would be noise, and noise is how a real
            # warning gets ignored.
            if abs(x1 - start[1]) < 3 and abs(y1 - start[2]) < 3:
                self._refresh_crop()
                return
        else:
            # Edge or move: no edge translated means nothing happened, and "crop
            # set" for a click would be noise in the same way a stray press used
            # to read as "too small".
            if max(abs(a - b) * (iw if i % 2 == 0 else ih)
                   for i, (a, b) in enumerate(zip((rx0, ry0, rx1, ry1), cur))) < 3.0:
                self._refresh_crop()
                return
        ok = self.session.set_crop_rect(rx0, ry0, rx1, ry1, iw, ih)
        if ok:
            self._crop_taken_by_hand()
        self._refresh_crop()
        self._set_status_extra("crop set" if ok else "crop too small, ignored")

    def _draw_crop_outline(self, rx0, ry0, rx1, ry1):
        """The kept rectangle's border and handles on the after canvas.

        The shade that used to go here is now baked into the picture itself
        (``darken_outside_crop``), so only the outline is drawn -- a Tk item has no
        alpha for a flat veil, which is why the dimming lives in the array.
        """
        tag = "crop_overlay"
        x0, x1 = sorted((rx0, rx1))
        y0, y1 = sorted((ry0, ry1))
        self.c_after.delete(tag)
        self.c_after.create_rectangle(x0, y0, x1, y1, outline=OVERLAY["crop"],
                                      width=2, tags=tag)
        # Handles: the four corners and the midpoints of the four edges.
        # Drawn because a grab region nobody can see is a feature nobody finds;
        # sized to the radius `_grab_handle` accepts.  The midpoints are how an
        # edge moves on its own -- top/bottom vertically, left/right
        # horizontally -- instead of re-placing the whole rectangle.
        # Turned INWARD at the edges rather than centred on them.  A handle
        # centred on the rectangle's corner hangs half its width outside it, and
        # when the crop is the whole frame that half falls off the canvas and is
        # clipped -- the top row came out as slivers, and the corner ones sat at
        # y = -5.  Nudging each one inside by its own half-width keeps every
        # handle whole and still on the line it grabs.
        h = 5
        for hx, hy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1),
                       (0.5 * (x0 + x1), y0), (0.5 * (x0 + x1), y1),
                       (x0, 0.5 * (y0 + y1)), (x1, 0.5 * (y0 + y1))):
            cx = min(max(hx, x0 + h), x1 - h) if x1 - x0 > 2 * h else hx
            cy = min(max(hy, y0 + h), y1 - h) if y1 - y0 > 2 * h else hy
            self.c_after.create_rectangle(cx - h, cy - h, cx + h, cy + h,
                                          fill=OVERLAY["crop"], outline=OVERLAY["crop_edge"],
                                          width=1, tags=tag)

    def _cross_orientation(self, event):
        """Guide kind from pointer position: 'h' or 'v'.

        Distance to the nearest *panel* edge (or center) decides: small dy
        means top/bottom border → horizontal guide; small dx means left/right
        gutter → vertical guide.  Independent of where the image sits inside."""
        w, h = max(1, self.winfo_width()), max(1, self.winfo_height())
        dx = min(event.x, abs(event.x - w // 2), abs(w - event.x))
        dy = min(event.y, abs(event.y - h // 2), abs(h - event.y))
        return "h" if dy <= dx else "v"

    def _on_cross_motion(self, event=None):
        """Pointer over the black cross: cursor shows drag direction.

        LR (left/right gutter) → vertical double arrow (drag a v-guide up/down).
        UD (top/bottom border) → horizontal double arrow (drag an h-guide left/right)."""
        if event is None:
            return
        if getattr(self, "_ph_a", None) is None:
            return
        kind = self._cross_orientation(event)
        cur = "sb_h_double_arrow" if kind == "v" else "sb_v_double_arrow"
        self.config(cursor=cur)

    def _on_cross_leave(self, _event=None):
        """Left the cross: reset cursor."""
        self.config(cursor="")

    def _on_cross_press(self, event):
        """Start pulling a guide out of the cross."""
        if getattr(self, "_ph_a", None) is None:
            return
        self._cross_pull = self._cross_orientation(event)

    def _on_cross_pull(self, event):
        """Preview the guide while the pointer is still over the cross."""
        if getattr(self, "_cross_pull", None) is None:
            return
        self._cross_preview(event)

    def _on_cross_drop(self, event):
        """Drop the guide if it landed on the corrected pane, else discard it."""
        kind = getattr(self, "_cross_pull", None)
        self._cross_pull = None
        self.c_after.delete("guide_preview")
        if kind is None or getattr(self, "_ph_a", None) is None:
            return
        pos = self._after_pos(event, kind)
        if pos is None:
            return
        self._after_guides.append((kind, pos))
        self._draw_after_guides()

    def _after_pos(self, event, kind):
        """Pointer position as an offset inside the after image, or None.

        Uses the *root* coordinates, because the event belongs to the panel
        while the answer is wanted in the canvas's own space."""
        try:
            cx = event.x_root - self.c_after.winfo_rootx()
            cy = event.y_root - self.c_after.winfo_rooty()
        except tk.TclError:
            return None
        aox, aoy = self._after_off
        iw, ih = self._ph_a.width(), self._ph_a.height()
        x, y = cx - aox, cy - aoy
        if not (0 <= x <= iw and 0 <= y <= ih):
            return None
        return float(y if kind == "h" else x)

    def _cross_preview(self, event):
        """A grey dashed line following the pointer, before the guide is committed."""
        self.c_after.delete("guide_preview")
        kind = getattr(self, "_cross_pull", None)
        pos = self._after_pos(event, kind) if kind else None
        if pos is None:
            return
        aox, aoy = self._after_off
        iw, ih = self._ph_a.width(), self._ph_a.height()
        if kind == "h":
            self.c_after.create_line(aox, aoy + pos, aox + iw, aoy + pos,
                                     fill=GUIDE_GREY, width=1, dash=(3, 3),
                                     tags="guide_preview")
        else:
            self.c_after.create_line(aox + pos, aoy, aox + pos, aoy + ih,
                                     fill=GUIDE_GREY, width=1, dash=(3, 3),
                                     tags="guide_preview")

    def _after_guide_at(self, x, y, iw, ih):
        """Return (kind, index) of a guide within grab distance, else None."""
        for i, (kind, pos) in enumerate(self._after_guides):
            if kind == "v":
                if abs(x - pos) <= 15 and 0 <= y <= ih:
                    return ("v", i)
            else:
                if abs(y - pos) <= 15 and 0 <= x <= iw:
                    return ("h", i)
        return None

    def _on_guide_drag(self, event):
        """Move the grabbed guide to follow the cursor (allow overshoot)."""
        if self._guide_drag is None or getattr(self, "_ph_a", None) is None:
            return
        kind, idx = self._guide_drag
        aox, aoy = self._after_off
        if kind == "v":
            pos = event.x - aox
        else:
            pos = event.y - aoy
        self._after_guides[idx] = (kind, pos)
        self._draw_after_guides()

    def _on_guide_release(self, event):
        """Finalise or delete the guide based on whether it left the bounds."""
        if self._guide_drag is None:
            return
        kind, idx = self._guide_drag
        aox, aoy = self._after_off
        iw, ih = self._ph_a.width(), self._ph_a.height()
        pos = self._after_guides[idx][1]
        margin = 20
        start = getattr(self, "_guide_new", None)
        self._guide_new = None
        if start is not None and abs(pos - start) < layout.GUIDE_CLICK_PX:
            # Pressed in the band and let go: a click, not a pull.
            del self._after_guides[idx]
            self._guide_drag = None
            try:
                self.c_after.grab_release()
            except tk.TclError:
                pass
            self._draw_after_guides()
            self._notify("guides: press on the picture's edge and drag inward")
            return
        if start is not None:
            self._notify("guide added -- drag it back off the picture to remove it")
        if kind == "v":
            if pos < -margin or pos > iw + margin:
                del self._after_guides[idx]
            else:
                self._after_guides[idx] = ("v", max(0.0, min(iw, pos)))
        else:
            if pos < -margin or pos > ih + margin:
                del self._after_guides[idx]
            else:
                self._after_guides[idx] = ("h", max(0.0, min(ih, pos)))
        self._guide_drag = None
        try:
            self.c_after.grab_release()
        except tk.TclError:
            pass
        self._draw_after_guides()

    def _draw_after_guides(self):
        """Reference guides on Q2 (the corrected pane).

        Pulled off the black cross by dragging; drawn as plain grey lines so
        they read as instruments, not content.  Never composited into the
        saved frame."""
        self.c_after.delete("after_guide")
        if getattr(self, "_ph_a", None) is None:
            return
        aox, aoy = self._after_off
        iw, ih = self._ph_a.width(), self._ph_a.height()
        for kind, pos in self._after_guides:
            if kind == "v":
                x = aox + pos
                self.c_after.create_line(x, aoy, x, aoy + ih,
                                         fill=GUIDE_GREY, width=1,
                                         tags="after_guide")
            else:
                y = aoy + pos
                self.c_after.create_line(aox, y, aox + iw, y,
                                         fill=GUIDE_GREY, width=1,
                                         tags="after_guide")
