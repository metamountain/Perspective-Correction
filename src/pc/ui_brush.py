"""The mask panel and the mask brush: paint, erase, size, preview.

The mask control methods and the brush/stroke painting methods that
``ReviewPanel`` (in :mod:`pc.gui`) carries for the excluded region live here
on a mixin.  This module never imports ``pc.gui``: it needs only the standard
library, the ``AUTO`` mode from ``review``, and the shared ``INK`` / ``OVERLAY``
palettes plus ``MASK_SWATCHES`` from ``ui_theme`` (shared by object identity
with ``gui.py``'s re-exports).
"""
from __future__ import annotations

import tkinter as tk

from .review import AUTO
from .ui_theme import INK, MASK_SWATCHES, OVERLAY


class _BrushMaskMixin:
    """The mask panel and the mask brush, mixed into ``ReviewPanel``.

    The host supplies the state the tool keeps (``session``, ``_stroke_pts``,
    ``_pen_anchor``, ``_stroke_grabbed``, ``_stroke_erasing``), the before
    canvas (``c_before``) and its geometry (``_before_off``,
    ``_before_scale``), and the panel's variables, labels and status helpers;
    the mixin adds only the mask controls and the paint/erase/size/preview
    machinery.
    """

    # -- mask controls: source, tint, invert, show --------------------------
    def _apply_mask(self):
        if not self.session:
            return                     # no photo loaded; nothing to mask yet
        if not self.session.mask_enabled:
            # Off has to mean off, and `set_mask("off")` was not it: it replaces
            # the SOURCE, and `_detect` re-applies the paint at the end of the
            # same call, so a brushed mask went on acting.  Measured on the test
            # asset -- wash 25.0%, pitch +0.0369, 73 of 76 lines, identical with
            # the box ticked and unticked.  `mask_enabled` on the session
            # suppresses the union itself, which is what the box claims.
            self.lbl_mask.configure(text="mask inactive")
            self._sync_from_session()
            return
        mode = self.v_maskmode.get()
        if mode == "file" and not self.session.settings.mask_file:
            mode = "off"               # the folder picker is gone with its row
            self.v_maskmode.set(mode)
        err = self.session.set_mask(mode, invert=bool(self.v_maskinv.get()))
        self.lbl_mask.configure(text=err or "")
        if self.session.mode == AUTO:
            self._sync_from_session()
        else:
            self._redraw()

    def _clear_mask(self):
        """Throw away the painted mask and the SAM selection, and refit.

        The session owns the release rule (`ReviewSession.clear_mask`); this
        half only clears the SAM *prompts*, which live up here in frame
        fractions.  It used to zero `_paint_struck` itself and skip the release,
        so the lines the brush had struck stayed struck with no record of which
        they were -- the red vanished and the correction never moved.

        The mask SOURCE is deliberately left alone: the button says painted mask
        and SAM selection, and setting the combobox to "off" from here left the
        combobox and `settings.mask_mode` saying different things.
        """
        if not self.session:
            return
        self.session.clear_mask()
        self._sam_box = None
        self._sam_box_px = None
        self._sam_points = []
        self._sam_selection = None
        self.lbl_mask.configure(text="")
        self.c_before.delete("sam_prompts")
        self._sync_from_session()

    def _on_alpha(self):
        if not self.session:
            return
        self.session.mask_alpha = float(self.v_alpha.get())
        self._schedule_redraw()

    def _pick_mask_color(self):
        win = tk.Toplevel(self)
        win.title("Mask overlay colour")
        win.resizable(False, False)
        win.transient(self)
        win.configure(bg=INK["panel"])
        # Open directly below the swatch button so the eye never has to travel.
        sw = getattr(self, "_mask_swatch", None)
        if sw is not None:
            try:
                x = sw.winfo_rootx()
                y = sw.winfo_rooty() + sw.winfo_height() + 4
                win.geometry(f"+{x}+{y}")
            except tk.TclError:
                pass
        presets = list(MASK_SWATCHES)
        cur = self.v_mask_color.get()

        def pick(col):
            self.v_mask_color.set(col)
            sw = getattr(self, "_mask_swatch", None)
            if sw is not None:
                sw.configure(bg=col)
            self._sync_mask_color()
            win.destroy()

        for i, col in enumerate(presets):
            r_, c_ = divmod(i, 4)
            b = tk.Canvas(win, width=36, height=36, bg=col,
                          highlightthickness=0, bd=0)
            b.grid(row=r_, column=c_, padx=2, pady=2)
            b.bind("<Button-1>", lambda _e, cc=col: pick(cc))
        # Highlight the current colour with a ring.
        for i, col in enumerate(presets):
            if col.lower() == cur.lower():
                r_, c_ = divmod(i, 4)
                slaves = win.grid_slaves(r_, c_)
                if slaves:
                    slaves[0].configure(highlightthickness=2,
                                        highlightbackground="white")
        tk.Button(win, text="Cancel", command=win.destroy,
                  bg=INK["panel"], fg=INK["text"]).grid(
            row=4, column=0, columnspan=4, pady=(6, 0))

    def _on_mask_alpha(self):
        # Keep the mask panel's slider in sync (same variable would be cleaner,
        # but v_alpha is created later in _build_tools and may not exist yet).
        va = getattr(self, "v_alpha", None)
        if va is not None:
            va.set(self.v_mask_alpha.get())
        if self.session is None:
            return
        self.session.mask_alpha = float(self.v_mask_alpha.get())
        # Throttled: a full re-warp per slider tick is the lag.  The existing
        # _schedule_redraw already coalesces to one redraw per event-loop turn.
        self._schedule_redraw()

    def _sync_mask_color(self):
        """Convert the hex colour from the swatch into a BGR tuple for review."""
        h = self.v_mask_color.get().lstrip("#")
        if len(h) == 6:
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            if self.session is not None:
                self.session.mask_color = (b, g, r)  # BGR for OpenCV
        self._schedule_redraw()

    def _show_mask_state(self):
        """Make the mask panel show what the NEW session actually has.

        One rule, one direction: on load the boxes follow the session.  They
        were set once when the panel was built and never touched again, while
        every photograph brings a fresh `ReviewSession` with `mask_enabled`
        True and `invert_mask` False.  Measured over two photographs: untick
        "mask active", tick "invert ALL sources", load the next image --
        boxes said active=False invert=True, session said True and False.
        Both boxes lied about the picture in front of them.

        Resetting rather than carrying the choices over is the honest reading:
        the paint and the SAM selection belong to one photograph and are gone
        at this point anyway, so the switches over them start fresh too.  The
        SOURCE is different -- it lives in Settings, which the batch panel owns
        -- so the combobox is shown from there rather than reset.
        """
        s = self.session
        if s is None:
            return
        if getattr(self, "v_mask_active", None) is not None:
            self.v_mask_active.set(bool(s.mask_enabled))
        if getattr(self, "v_invert", None) is not None:
            self.v_invert.set(bool(s.invert_mask))
        if getattr(self, "v_maskmode", None) is not None:
            self.v_maskmode.set(s.settings.mask_mode)
        if getattr(self, "v_maskinv", None) is not None:
            self.v_maskinv.set(bool(s.settings.mask_invert))

    def _on_mask_active_toggle(self):
        """Toggle mask active/inactive without clearing the painted region."""
        if not self.session:
            return
        self.session.mask_enabled = bool(self.v_mask_active.get())
        self.session._apply_paint()      # release or re-strike, one rule
        self.session._refresh_mask()     # and the wash follows the same switch
        self.session.refit()
        self._apply_mask()

    def _on_invert_toggle(self):
        """Flip the whole mask and re-fit."""
        if not self.session:
            return
        self.session.invert_mask = bool(self.v_invert.get())
        self.session._refresh_mask()
        self.session.refit()
        self._sync_from_session()

    def _toggle_mask(self):
        """Show the excluded region and the lines it removed.

        A mask the user cannot see is a mask the user cannot trust: when a
        segmenter takes out the wrong half of a building, the only other symptom
        is a quietly worse answer."""
        if self.session is None:
            return
        self.session.show_mask = bool(self.v_show_mask.get())
        self._schedule_redraw()

    # -- brush cursor: the double ring at the pointer -----------------------
    def _brush_cursor_indicator(self, event):
        """Show a black-outlined circle at the cursor when the mask brush is
        active, so the user can see the effective paint size before committing."""
        if not self._brush_live():
            self.c_before.delete("brush_cursor")
            return
        r = max(8, int(self.v_stroke_w.get()))
        self._draw_brush_ring(event.x, event.y, r)

    def _draw_brush_ring(self, cx, cy, r):
        """The round brush outline, at canvas coords, radius r.

        One definition, because it is drawn from two places now -- following the
        cursor, and standing still while a sizing drag changes r. A double ring,
        white outside and black inside: a single black one is invisible against
        a dark facade and against the field colour itself, and one of the two
        always contrasts whatever is underneath.
        """
        self.c_before.delete("brush_cursor")
        self.c_before.create_oval(cx - r - 1, cy - r - 1, cx + r + 1, cy + r + 1,
                                  outline="white", width=1, tags="brush_cursor")
        self.c_before.create_oval(cx - r, cy - r, cx + r, cy + r,
                                  outline="black", width=1, tags="brush_cursor")

    # -- brush: right erases, Alt+right sizes the pen ----------------------
    def _on_stroke_toggle(self):
        # Leaving the mode clears any half-painted stroke so it never lingers.
        if getattr(self, "v_stroke", None) is not None and not self.v_stroke.get():
            self._stroke_release_grab()
            self.c_before.delete("stroke_preview")
            self.c_before.delete("brush_cursor")
            self._stroke_pts = []
        else:
            self._exclusive("v_stroke")
            self._set_status("mask brush: drag to paint the ignored region, "
                             "right-drag to erase it, Alt+right-drag sizes the pen")

    def _stroke_release_grab(self):
        # A stroke grabs the pointer so a release *outside* the pane still lands
        # here and clears the preview -- without it, letting go off-canvas leaves
        # the dashed line painted with no button held down.
        if getattr(self, "_stroke_grabbed", False):
            try:
                self.c_before.grab_release()
            except tk.TclError:
                pass
            self._stroke_grabbed = False

    def _brush_live(self):
        return self.session is not None and bool(self.v_stroke.get())

    def _on_pen_size_start(self, event):
        """Remember where the sizing drag began, and how wide the pen was then.

        Anchored rather than incremental so the width tracks the pointer: drag
        back to where you started and you get the width you started with, which
        an accumulating step does not give you.
        """
        if not self._brush_live():
            return
        self._pen_anchor = (event.x, event.y, int(self.v_stroke_w.get()))
        return "break"

    def _on_pen_size_end(self, _event=None):
        """End a pen-size drag.

        Its only job is to forget the anchor, and that is exactly why it was
        missing: nothing visibly breaks at the end of the sizing gesture. The
        damage lands later, on the next erase.
        """
        self._pen_anchor = None
        return "break"

    def _on_pen_size_drag(self, event):
        anchor = getattr(self, "_pen_anchor", None)
        if anchor is None or not self._brush_live():
            return
        x0, y0, w0 = anchor
        # Half a pixel of width per pixel of travel: the full 8..160 range then
        # fits a comfortable drag rather than needing the whole screen.
        self.v_stroke_w.set(max(8, min(160, w0 + (event.x - x0) // 2)))
        self._set_status_extra(f"pen width {int(self.v_stroke_w.get())} px")
        # Redrawn at the point the drag STARTED, not at the pointer: sizing
        # travels sideways, so following the cursor would slide the preview away
        # from the spot whose brush size you are actually judging.
        self._draw_brush_ring(x0, y0, max(8, int(self.v_stroke_w.get())))
        return "break"

    def _on_shift_press(self, event):
        if self._brush_live():
            return self._on_pen_size_start(event)
        return self._on_click_before(event)

    def _on_shift_motion(self, event):
        if getattr(self, "_pen_anchor", None) is not None:
            return self._on_pen_size_drag(event)
        return self._on_before_b1motion(event)

    def _on_shift_release(self, event):
        if getattr(self, "_pen_anchor", None) is not None:
            return self._on_pen_size_end(event)
        return self._on_before_b1release(event)

    def _on_alt_erase_press(self, event):
        """Alt+Left-click erases mask paint (same as right-click)."""
        if not self._brush_live():
            return
        self._stroke_erasing = True
        self._stroke_start(event.x - self._before_off[0],
                           event.y - self._before_off[1])
        return "break"

    def _on_erase_press(self, event):
        if not self._brush_live():
            return
        self._stroke_erasing = True
        self._stroke_start(event.x - self._before_off[0],
                           event.y - self._before_off[1])
        return "break"

    def _on_erase_motion(self, event):
        if getattr(self, "_pen_anchor", None) is not None:
            return "break"                    # an Alt-drag that lost its modifier
        if not self._brush_live():
            return
        self._on_stroke_drag(event)
        return "break"

    def _on_erase_release(self, event):
        self._pen_anchor = None
        if not self._brush_live():
            return
        self._on_stroke_release(event)
        return "break"

    def _stroke_start(self, x, y):
        self._stroke_pts = [(x, y)]
        try:
            self.c_before.grab_set()
            self._stroke_grabbed = True
        except tk.TclError:
            self._stroke_grabbed = False
        self._draw_stroke_preview()

    def _on_stroke_drag(self, event):
        if self.session is None or not getattr(self, "_stroke_pts", None):
            return
        x = event.x - self._before_off[0]
        y = event.y - self._before_off[1]
        pts = self._stroke_pts
        lx, ly = pts[-1]
        if (x - lx) ** 2 + (y - ly) ** 2 < 4.0:      # sub-pixel jitter: skip
            return
        pts.append((x, y))
        self._draw_stroke_preview()

    def _on_stroke_release(self, event):
        self._stroke_release_grab()
        if self.session is None or not getattr(self, "_stroke_pts", None):
            return
        x = event.x - self._before_off[0]
        y = event.y - self._before_off[1]
        self._stroke_pts.append((x, y))
        w = max(8, int(self.v_stroke_w.get()))
        # The spinbox has always been the brush *radius*, not its diameter --
        # "what you paint is what toggles" -- so it is passed through unhalved.
        share = self.session.paint_ignore(self._stroke_pts,
                                          display_scale=self._before_scale,
                                          radius=w,
                                          erase=bool(getattr(self, "_stroke_erasing", False)))
        self._stroke_pts = []
        self._stroke_erasing = False
        self.c_before.delete("stroke_preview")
        if self.session.mode == AUTO:
            self._sync_from_session()
        else:
            self._redraw()
        self._set_status_extra(f"mask brush: {share * 100:.0f}% of the frame ignored")

    def _draw_stroke_preview(self):
        """Show exactly what the stroke will paint, where it will paint it.

        Three things were wrong and all three mattered.  The points are stored in
        *image* coordinates (`_on_click_before` subtracts `_before_off`) but were
        drawn straight onto the *canvas*, so the preview sat a whole offset away
        from the pointer.  The width used was the radius, so the guide was half
        the size of the mark it left.  And it was dashed yellow, which reads as a
        selection marquee rather than as paint.  It is now solid dark red at the
        true diameter: the same colour family as the wash it adds to, so what you
        see under the pointer is what you get.
        """
        pts = self._stroke_pts
        if not pts:
            return
        ox, oy = self._before_off
        r = max(8, int(self.v_stroke_w.get()))      # the spinbox is the radius
        self.c_before.delete("stroke_preview")
        if len(pts) < 2:                       # a line needs two points; show a dot
            x, y = pts[0]
            self.c_before.create_oval(ox + x - r, oy + y - r,
                                      ox + x + r, oy + y + r,
                                      outline="", fill=OVERLAY["paint"],
                                      tags="stroke_preview")
            return
        flat = [c for p in pts for c in (ox + p[0], oy + p[1])]
        self.c_before.create_line(flat, width=2 * r, fill=OVERLAY["paint"],
                                  capstyle="round", joinstyle="round",
                                  tags="stroke_preview")
