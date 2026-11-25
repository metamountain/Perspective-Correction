"""The loupe: a full-resolution magnifier for placing marks precisely.

The five methods by which ``ReviewPanel`` (in :mod:`pc.gui`) parks, follows,
shows and hides the magnifying glass over the before picture live here on a
mixin.  This module never imports ``pc.gui``: it needs only the standard
library, ``numpy`` for the crop buffer, the loupe geometry from ``layout``
and the live ``INK`` / ``OVERLAY`` palettes plus ``_to_photo`` from
``ui_theme`` (shared by object identity with ``gui.py``'s re-exports).
"""
from __future__ import annotations

import sys

import tkinter as tk

import numpy as np

from . import layout
from .ui_theme import INK, OVERLAY, _to_photo

# macOS Tk reports Option as state bit 0x0010; Windows and Linux say 0x0008.
_MAC = sys.platform == "darwin"
_ALT_MASK = 0x0010 if _MAC else 0x0008


class _LoupeMixin:
    """The loupe, mixed into ``ReviewPanel``.

    The host supplies the state the tool keeps (``_loupe``, ``_loupe_ph``,
    ``_loupe_center``), the canvases (``c_before``, ``c_after``), the preview
    geometry (``_before_off``, ``_before_scale``) and the full-resolution
    ``session``; the mixin adds only the glass geometry and the show/hide/
    move machinery.
    """

    # -- the loupe: full-resolution magnifier for placing marks precisely ---
    def _loupe_park(self, size):
        """Where the glass sits: the black gutter BETWEEN the two pictures.

        "Centre of the panel" put it half over the mask row, because the panel
        is pictures on top and controls underneath and its middle is in the
        controls.  The cross's middle is the gap between the two preview panes,
        level with them -- the one place on this window where nothing is drawn
        and nothing can be clicked.
        """
        # Screen coordinates, then back into this panel's frame.  `winfo_x` is
        # relative to a widget's OWN parent, and these canvases live inside the
        # cross's cells while the glass is placed on the panel -- so mixing the
        # two put it in the middle of the picture instead of beside it.
        try:
            px, py = self.winfo_rootx(), self.winfo_rooty()
            bx, bw = self.c_before.winfo_rootx(), self.c_before.winfo_width()
            by, bh = self.c_before.winfo_rooty(), self.c_before.winfo_height()
            ax = self.c_after.winfo_rootx()
        except tk.TclError:
            return (self.winfo_width() - size) // 2, (self.winfo_height() - size) // 2
        cx = (bx + bw + ax) // 2 - px
        cy = by + bh // 2 - py
        return max(0, cx - size // 2), max(0, cy - size // 2)

    def _loupe_follow(self, size, event=None):
        """Where the glass goes: beside the cursor, by ``LOUPE_OFFSET``.

        The glass follows the pointer.  It was parked in the gutter -- 8 px
        wide, so a 362 px glass sat half over the before picture and hid the
        very corners being aimed at -- and then jumped onto the after picture.
        Below-right of the pointer, flipped to the other side where the panel
        ends, so it never covers the point it magnifies.
        ``event`` is a c_before event; without one the pointer is asked.
        """
        try:
            px, py = self.winfo_rootx(), self.winfo_rooty()
            pw, ph = self.winfo_width(), self.winfo_height()
            if event is not None:
                mx = self.c_before.winfo_rootx() + event.x - px
                my = self.c_before.winfo_rooty() + event.y - py
            else:
                mx = self.winfo_pointerx() - px
                my = self.winfo_pointery() - py
        except tk.TclError:
            return self._loupe_park(size)
        off = layout.LOUPE_OFFSET
        x = mx + off if mx + off + size <= pw else mx - off - size
        y = my + off if my + off + size <= ph else my - off - size
        return max(0, int(x)), max(0, int(y))

    def _loupe_show(self):
        """Create the glass.  A canvas placed over the panel, not a Toplevel:
        it only has to live while the cursor is over the before field, which
        is inside this window, and a second window is chrome nobody asked for."""
        if self._loupe is not None:
            return
        short = max(1, min(self.c_before.winfo_width(),
                           self.c_before.winfo_height()))
        size, _crop, _off = layout.loupe(short)
        w = tk.Canvas(self, width=size, height=size, bg=INK["cross"],
                      highlightthickness=1, highlightbackground=INK["line"])
        # Parked where it will live, immediately.  It used to be created off
        # view and only slid into place on the first MOTION, so pressing to
        # start a mark showed nothing at all until you moved -- and seeing
        # the point before you move it is the entire purpose of the glass.
        w.place(**dict(zip(("x", "y"), self._loupe_follow(size))))
        # Raise via the raw window command: on a Canvas both `lift()` and
        # `tkraise()` are the item-stacking commands (they need an item id), so
        # neither raises the widget itself.  `raise <window>` does.
        w.tk.call("raise", w._w)
        # The loupe floats over c_before; without forwarding, clicks land on
        # the loupe and are swallowed.  Relay button events to c_before at the
        # equivalent coordinate so the mark and stroke gestures work through it.
        def _forward(event):
            lx = event.x_root - self.c_before.winfo_rootx()
            ly = event.y_root - self.c_before.winfo_rooty()
            # state must ride along: the glass overlaps the before field, and a
            # drag over it loses Alt (and friends) unless the regenerated
            # event carries the modifier bits.
            self.c_before.event_generate(
                event.type, x=int(lx), y=int(ly), buttons=event.buttons,
                state=getattr(event, "state", 0))
        w.bind("<Button-1>", _forward)
        w.bind("<ButtonRelease-1>", _forward)
        w.bind("<B1-Motion>", _forward)

        # A fast move can land the pointer on the glass before it moves away;
        # without this the before field stops hearing motion and the glass
        # stays where it is, under the pointer, for good.
        def _forward_motion(event):
            self.c_before.event_generate(
                "<Motion>", x=int(event.x_root - self.c_before.winfo_rootx()),
                y=int(event.y_root - self.c_before.winfo_rooty()),
                state=getattr(event, "state", 0))
        w.bind("<Motion>", _forward_motion)
        self._loupe = w
        self._loupe_ph = None
        self._loupe_center = None

    def _loupe_hide(self):
        if self._loupe is None:
            return
        self._loupe.destroy()
        self._loupe = None
        self._loupe_ph = None
        self._loupe_center = None

    def _loupe_move(self, event):
        """Magnify the full-resolution image around the cursor.

        The crop is centred on the pixel under the cursor and never clamped --
        off-image runs show as dark -- so the crosshair at the window's centre
        always marks exactly that pixel, even at the frame's edge.  Magnifying
        the preview instead would show big soft pixels and buy nothing."""
        if self._loupe is None or self.session is None:
            return
        short = max(1, min(self.c_before.winfo_width(),
                           self.c_before.winfo_height()))
        size, crop, off = layout.loupe(short)
        W, H = self.session.w, self.session.h
        # Magnify around the damped point, not the raw cursor: the mark end is
        # where the line goes, and the glass must be centred on it.
        px = int(round((event.x - self._before_off[0]) / self._before_scale))
        py = int(round((event.y - self._before_off[1]) / self._before_scale))
        # Alt: damp the crop centre so the glass lags the cursor by LOUPE_MAG,
        # making fine aiming possible.  Without Alt the centre tracks 1:1.
        alt = bool(getattr(event, "state", 0) & _ALT_MASK)
        if alt and self._loupe_center is not None:
            f = 1.0 / layout.LOUPE_MAG
            cx = self._loupe_center[0] + (px - self._loupe_center[0]) * f
            cy = self._loupe_center[1] + (py - self._loupe_center[1]) * f
        else:
            cx, cy = px, py
        self._loupe_center = (cx, cy)
        x0, y0 = int(round(cx)) - crop // 2, int(round(cy)) - crop // 2
        buf = np.zeros((crop, crop, 3), dtype=np.uint8)
        ix0, iy0 = max(0, x0), max(0, y0)
        ix1, iy1 = min(W, x0 + crop), min(H, y0 + crop)
        if ix1 > ix0 and iy1 > iy0:
            buf[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0] = \
                self.session.bgr[iy0:iy1, ix0:ix1]
        ph, _s = _to_photo(buf, (size, size))
        c = self._loupe
        c.delete("all")
        c.create_image(0, 0, anchor="nw", image=ph)
        self._loupe_ph = ph          # the canvas does not keep a reference
        m = size // 2
        arm = max(20, size // 6)
        # Cyan while Alt is held: the only way to see the damping is active,
        # since a half-speed glass is easy to mistake for a full-speed one.
        cross = OVERLAY["loupe_alt"] if alt else OVERLAY["loupe"]
        for x1, y1, x2, y2 in ((m - arm, m, m + arm, m), (m, m - arm, m, m + arm)):
            c.create_line(x1, y1, x2, y2, fill=cross, width=2)
        # Round, and it says "glass" rather than "a second window". Tk cannot
        # clip a canvas, so the corners are covered rather than cut: four arcs
        # of the cross colour outside the circle, then a ring on top.
        # Square, filled to the edge.  It was round for a while, masked out of a
        # square canvas -- and since Tk gives a Canvas no alpha, the corners had
        # to be painted black, which reads as a hole cut badly rather than as
        # glass.  Round with nothing behind it is not on offer here, so: square,
        # every pixel of it picture, and one hairline to say where it ends.
        c.create_rectangle(1, 1, size - 1, size - 1,
                           outline=INK["line"], width=2)
        c.place(**dict(zip(("x", "y"), self._loupe_follow(size, event))))
