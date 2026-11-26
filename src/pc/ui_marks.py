"""The hand-mark, planar-rectangle and strip-ruler tools of the review pane.

The methods by which ``ReviewPanel`` (in :mod:`pc.gui`) toggles the line and
PC Rectangle tools, places and nudges their corners, clears the marks, draws
the before-pane overlays, and builds and drags the facade-strip rulers live
here on a mixin.  This module never imports ``pc.gui``: it needs only the
standard library, the theme names and the shared layout helpers.
"""
from __future__ import annotations

import tkinter as tk

from . import layout
from .ui_theme import INK, OVERLAY, ui_font


class _MarksMixin:
    """The mark, planar-rectangle and strip-ruler methods, mixed into
    ``ReviewPanel`` in :mod:`pc.gui`.

    The host supplies the widgets and helpers these methods drive (the
    ``c_before`` canvas, the ``v_mark`` / ``v_rect`` / ``v_strip`` switches,
    ``session``, the loupe, ``_exclusive``, ``_notify``,
    ``_sync_from_session``, ``_set_status``, ``_redraw``,
    ``_drag_preview``); the methods only add canvas items and the
    ``_pending_mark`` / ``_rect_pending`` / ``_mark_drag`` state on
    ``self``.
    """

    def _on_mark_toggle(self):
        """Entering or leaving marking mode; a half-finished line is forgotten
        rather than left dangling."""
        on = self.v_mark.get()
        if on:
            self._exclusive("v_mark")
            self._pending_mark = None
            self._loupe_hide()
            s = self.session
            if s is not None:
                had_quad = bool(s.planar_quad)
                if s.set_facade_mode("lines"):
                    if had_quad:
                        self._notify("line tool -- the rectangle is put away; "
                                     "the rectangle tool brings it back")
                    self._sync_from_session()
        else:
            self._pending_mark = None
            self._mark_drag = None
            self._loupe_hide()
        self._set_status(self.session.status_text())
        self._redraw()

    def _on_rect_toggle(self):
        """Entering or leaving PC Rectangle mode (four corners)."""
        on = self.v_rect.get()
        if on:
            self._exclusive("v_rect")
            s = self.session
            if s is not None:
                had_lines = len(s.control_lines) + len(s.control_hlines) > 0
                if s.set_facade_mode("rect"):
                    if had_lines:
                        self._notify("PC Rectangle -- your drawn lines are put away; "
                                     "the line tool brings them back")
                    self._sync_from_session()
            self._rect_pending = []
            # The glass comes up WITH the tool, before the first point. Placing
            # a facade corner is the finest aim this window asks for, and the
            # first corner is aimed at exactly like the other three -- it used
            # to appear only once a corner was down, which is after the moment
            # it was needed (2026-09-20, user-directed).
            self._loupe_show()
        else:
            self._rect_pending = []
            self._mark_drag = None
            self._loupe_hide()
        self._set_status(self.session.status_text())
        self._redraw()

    def _click_rect(self, x, y, event=None):
        """Four clicks place the facade corners; a click on a placed corner
        grabs it for nudging.  The fourth click closes the quad and the
        rectified preview appears in the after pane.

        ``x, y`` are canvas pixels (photo-relative).  The session stores
        full-resolution pixels, so divide by ``_before_scale`` before storing
        and pass the same scale to hit-tests."""
        fs = self._before_scale
        fx, fy = x / fs, y / fs
        # Grab an existing corner first (nudge).
        hit = self.session.pick_planar_corner(fx, fy, display_scale=fs)
        if hit is not None:
            self._rect_drag = hit
            # Nudging a corner is the finest aim this tool asks for, and the
            # glass had gone with the fourth click.
            self._loupe_show()
            if event is not None:
                self._loupe_move(event)
            return
        # Place the next corner. CAD order: four points, closed on the fourth.
        i = len(self.session.planar_quad)
        if i >= 4:
            # Closed. A click on empty ground does NOT start over: wiping four
            # placed corners because somebody missed a handle is a destructive
            # answer to a miss. Right-click clears, and says so.
            self._set_status("PC Rectangle is closed -- drag a corner to adjust, "
                             "right-click to clear")
            return
        # A quad is an answer about this facade, and so are marks and the strip.
        # Whichever arrives last is the instruction; the others stand down
        # rather than sitting there looking as if they still applied.
        if i == 0:
            self._planar_takes_over()
        self.session.set_planar_point(i, fx, fy)
        n = len(self.session.planar_quad)
        if n >= 4:
            self._loupe_hide()
            self._set_status("PC Rectangle closed -- drag any corner to adjust")
        else:
            # No order is prescribed any more: `planar.order_quad` sorts the
            # four into top-left, top-right, bottom-right, bottom-left whatever
            # sequence they arrive in, so the facade no longer comes out turned
            # depending on which corner somebody started with.
            self._set_status(f"PC Rectangle: corner {n} of 4 -- click the next one "
                             f"(any order)")
        self._redraw()

    def _rect_rubber(self, event):
        """The edge under construction, from the last corner to the cursor.

        Bound to plain <Motion>, not <B1-Motion>. It used to be the latter --
        motion with the button HELD -- while the corners are placed by clicking,
        so once the button came back up the band vanished and the only way to
        see it was to hold the button down and drag, which is not how a point
        gets placed. That is the whole of "gummiband ist schrottig".

        With three corners down the closing edge back to the first is drawn too,
        so the shape reads as a closed quad before the fourth click lands.
        """
        if self.session is None or getattr(self, "v_rect", None) is None:
            return
        self.c_before.delete("rect_rubber")
        if not self.v_rect.get():
            return
        quad = self.session.planar_quad
        if not quad or len(quad) >= 4:
            return                      # nothing started, or already closed
        ox, oy = self._before_off
        sc = self._before_scale
        lx, ly = quad[-1]
        self.c_before.create_line(ox + lx * sc, oy + ly * sc, event.x, event.y,
                                  fill=OVERLAY["sam"], width=2, dash=(4, 4),
                                  tags="rect_rubber")
        if len(quad) >= 3:
            fx, fy = quad[0]
            self.c_before.create_line(event.x, event.y, ox + fx * sc, oy + fy * sc,
                                      fill=OVERLAY["sam"], width=2, dash=(4, 4),
                                      tags="rect_rubber")

    def _planar_takes_over(self):
        """Stand the rotation-path instructions down when a quad starts.

        The quad replaces the rotation preview entirely, so horizontal auto,
        the facade strip and the yaw slider stop having any effect the moment
        it closes. Leaving them switched on shows controls that look live and
        are not -- the failure this project keeps finding. Say what went.
        """
        s = self.session
        if s is None:
            return
        gone = []
        if getattr(self, "v_correct_horizontal", None) is not None \
                and self.v_correct_horizontal.get():
            self.v_correct_horizontal.set(False)
            s.settings = s.settings.replace(correct_horizontal=False)
            # The mode switch is the other face of the flag; left on
            # "Horizontal auto" it claimed a mode that was just switched off.
            self.v_corr_mode.set("auto")
            self._mode_frames["auto"].tkraise()
            gone.append("horizontal auto")
        if self._tool_active("v_strip"):
            self.v_strip.set(False)
            s.strip = None
            gone.append("facade strip")
        # The crop belongs to its mode (`ReviewSession.set_facade_mode` swaps
        # it), so this one is the rectangle's own; a fresh quad starts clean
        # and gets quad + 20 % once its fourth corner is down.
        if s.crop_rect is not None:
            s.clear_crop_rect()
            s.crop_is_auto = False
            gone.append("the crop")
        if gone:
            self._set_status("PC Rectangle takes over: " + " and ".join(gone)
                             + " switched off")

    def _rect_drag_move(self, event):
        """Nudge the grabbed corner; the rectified preview follows live."""
        if self._rect_drag is None or self.session is None:
            return
        x = (event.x - self._before_off[0]) / self._before_scale
        y = (event.y - self._before_off[1]) / self._before_scale
        self.session.set_planar_point(self._rect_drag, x, y)
        self.c_before.delete("rect_quad")
        self._draw_rect_quad()
        self._drag_preview()

    def _rect_drag_release(self):
        if self._rect_drag is None:
            return
        self._rect_drag = None
        self._drag_preview_end()
        self._loupe_hide()
        self._sync_from_session()

    def _clear_marks(self):
        # The button lives in the masking panel now, which is built once and
        # exists before a photograph is open -- so the empty case is reachable.
        if self.session is None:
            return
        # Both kinds, always: `a() or b()` stopped at the first that removed
        # something and left every horizontal line behind a vertical one.
        had_v = self.session.clear_control_lines()
        had_h = self.session.clear_control_lines(kind="h")
        if had_v or had_h:
            self._sync_from_session()

    def _click_mark(self, x, y, event=None):
        """Two clicks make one control line; a click on an existing one removes
        it.

        Removal shares the same gesture on purpose: the alternative is a
        modifier key nobody discovers, and a mark placed by mistake has to be as
        easy to take back as it was to make.  The orientation (vertical or
        horizontal) comes from the selector beside the Mark toggle, so pick/add/
        remove all stay inside one array.
        """
        # Both planes are on screen at once now, so an index means nothing
        # without the array it came from -- hence (index, kind) everywhere.
        # Verticals are searched first; with one line of each under the
        # cursor the vertical wins.  A tiebreak, not nearest-wins.
        for k in ("v", "h"):
            ep = self.session.pick_control_line_endpoint(
                x, y, display_scale=self._before_scale, radius=10.0, kind=k)
            if ep is not None:
                self._pending_mark = None
                self._mark_drag = (ep, k)
                # Nudging an endpoint is the finest placement this window asks
                # for -- finer than drawing the line in the first place, which
                # at least has two chances to be right.  The glass belongs here
                # even more than there.
                self._loupe_show()
                if event is not None:
                    self._loupe_move(event)   # show the point immediately, not on first motion
                return
        if self._pending_mark is None:
            for k in ("v", "h"):
                hit = self.session.pick_control_line(
                    x, y, display_scale=self._before_scale, kind=k)
                if hit is not None:
                    self.session.remove_control_line(hit, kind=k)
                    self._sync_from_session()
                    return
        if self._pending_mark is None:
            self._pending_mark = (x, y)
            self._mark_moved = False
            self._set_status("marking an edge: click the other end -- whichever "
                             "way it leans decides the plane\n"
                             "(as far from the first point as the structure allows)")
            self._loupe_show()
            if event is not None:
                self._loupe_move(event)   # show the point immediately, not on first motion
            self._redraw()
            return
        x0, y0 = self._pending_mark
        self._pending_mark = None
        # Marker or planar, not both (2026-09-20, user). A placed quad ignores
        # roll, pitch and yaw completely, so a line drawn while one is in force
        # would change nothing and look broken. The newest instruction wins.
        if self.session.drop_planar_for("a marker line"):
            if getattr(self, "v_rect", None) is not None:
                self.v_rect.set(False)
            self._set_status("PC Rectangle cleared -- the marker line is the "
                             "instruction now")
        added = self.session.add_control_line(x0, y0, x, y,
                                              display_scale=self._before_scale,
                                              kind=self._kind_for(x0, y0, x, y))
        if added is None:
            self._set_status("too short to be trusted -- mark the full length of "
                             "the structure, not a few pixels of it")
            self._redraw()
            return
        self._sync_from_session()

    def _mark_rubber(self, event):
        """Draw the line being dragged, from the pressed point to the cursor.

        Canvas-drawn and cleared on release, like every other in-progress
        overlay here: it is interaction state, not detection, and must appear
        without waiting for a re-render.
        """
        self._mark_moved = True
        ox, oy = self._before_off
        x0, y0 = self._pending_mark
        self.c_before.delete("mark_rubber")
        # Tinted by the plane this drag would land in, recomputed on every
        # motion: the classification is invisible otherwise until too late.
        dx, dy = (event.x - ox, event.y - oy)
        col = (OVERLAY["mark_v"]
               if self._kind_for(x0, y0, dx, dy) == "v"
               else OVERLAY["mark_h"])
        self.c_before.create_line(ox + x0, oy + y0, ox + dx, oy + dy,
                                  fill=col, width=2, dash=(4, 3),
                                  tags="mark_rubber")

    def _mark_commit(self, event):
        """Finish a dragged mark on release.

        Rubberband and two-click are the same gesture with and without
        movement, which is why this hangs off `_pending_mark` rather than a
        mode of its own: press, drag, release places the line; press, release,
        press still does too, and a press that never moved falls through to
        `_click_mark`, so **clicking an existing mark to delete it keeps
        working**. Losing that was the trap -- the removal gesture has no other
        home.
        """
        self.c_before.delete("mark_rubber")
        x0, y0 = self._pending_mark
        self._pending_mark = None
        self._mark_moved = False
        self._loupe_hide()              # the drag is over
        x = event.x - self._before_off[0]
        y = event.y - self._before_off[1]
        # Marker or planar, not both (2026-09-20, user). A placed quad ignores
        # roll, pitch and yaw completely, so a line drawn while one is in force
        # would change nothing and look broken. The newest instruction wins.
        if self.session.drop_planar_for("a marker line"):
            if getattr(self, "v_rect", None) is not None:
                self.v_rect.set(False)
            self._set_status("PC Rectangle cleared -- the marker line is the "
                             "instruction now")
        added = self.session.add_control_line(x0, y0, x, y,
                                              display_scale=self._before_scale,
                                              kind=self._kind_for(x0, y0, x, y))
        if added is None:
            self._set_status("too short to be trusted -- mark the full length of "
                             "the structure, not a few pixels of it")
            self._redraw()
            return
        self._sync_from_session()

    def _on_mark_drag(self, event):
        """Drag an endpoint of a placed control line to refine its position."""
        (line_idx, ep_idx), kind = self._mark_drag
        x = event.x - self._before_off[0]
        y = event.y - self._before_off[1]
        self.session.move_control_line_endpoint(line_idx, ep_idx, x, y,
                                                display_scale=self._before_scale,
                                                kind=kind, refit=False)
        self.c_before.delete("marks")
        self._draw_marks()
        self._drag_preview()

    # -- facade strip rulers ----------------------------------------------------
    def _strip_bounds(self):
        """The facade strip as fractions of frame width, or None when off/invalid.

        Mirrors `_apply_strip`'s validity test so the rulers and the estimator
        never disagree about whether a strip is in force."""
        v = getattr(self, "v_strip", None)
        if v is None or not v.get():
            return None
        try:
            x0 = float(self.v_strip_x0.get()) / 100.0
            x1 = float(self.v_strip_x1.get()) / 100.0
        except ValueError:
            return None
        if not (0.0 <= x0 < x1 <= 1.0 and (x1 - x0) >= 0.02):
            return None
        return x0, x1

    def _draw_horiz_vp_markers(self, pw: int, ph_: int):
        """P3: show horizontal VPs as dots on the before pane.

        Filled dot = dominant (index 0), hollow = others.  Off-screen VPs
        (at infinity) are skipped silently.
        """
        vps = self.session.model.horiz_vps[:4]
        if not vps:
            return
        ox, oy = self._before_off
        for i, vp in enumerate(vps):
            # vp is a 3-element homogeneous point (x, y, w) in full-res pixels
            vw = vp[2] if abs(vp[2]) > 1e-9 else 1.0
            px = ox + (vp[0] / vw) * self._before_scale
            py = oy + (vp[1] / vw) * self._before_scale
            if not (-20 <= px <= pw + 20 and -20 <= py <= ph_ + 20):
                continue  # off-screen (VP at infinity or far outside)
            r = 5 if i == 0 else 4
            col = INK["accent"] if i == 0 else INK["dim"]
            self.c_before.create_oval(px - r, py - r, px + r, py + r,
                                      outline=col, width=2,
                                      fill=col if i == 0 else "",
                                      tags="horiz_vp")

    def _draw_strip_rulers(self):
        """Two draggable vertical rulers on the before pane marking the facade strip.

        The spinboxes are a numeric fine-tune; these are the visual way to see
        where the strip falls and drag it.  Only the sides *outside* the strip get
        ignored, so they carry the wash -- the strip itself stays clean.  Ruler
        look (stipple, #9fd8ff, tick + label) differs from the reference guides
        on the after pane (`_draw_after_guides`); these are handles, so the lines
        are solid and a touch heavier than a grid line."""
        self.c_before.delete("strip_ruler")
        strip = self._strip_bounds()
        if strip is None:
            return
        x0f, x1f = strip
        ox, oy = self._before_off
        iw, ih = self._ph_b.width(), self._ph_b.height()
        px0 = ox + x0f * iw
        px1 = ox + x1f * iw
        wash = dict(fill=INK["field"], stipple="gray50")
        self.c_before.create_rectangle(ox, oy, px0, oy + ih, **wash, tags="strip_ruler")
        self.c_before.create_rectangle(px1, oy, ox + iw, oy + ih, **wash, tags="strip_ruler")
        # Each ruler carries its own reading. The spinboxes hold the same two
        # numbers, but they are on the far side of the window from the line you
        # are dragging, and a strip has to be placed EXACTLY -- the value belongs
        # where the eye already is (2026-09-20, user-directed). Both labels face
        # into the strip so neither falls off the frame edge.
        for pxf, frac, side in ((px0, x0f, 1), (px1, x1f, -1)):
            self.c_before.create_line(pxf, oy, pxf, oy + ih, fill=OVERLAY["strip"],
                                      width=2, tags="strip_ruler")
            cy = self._strip_handle_y(pxf, oy, ih)
            self.c_before.create_oval(pxf - 7, cy - 7, pxf + 7, cy + 7,
                                      outline=OVERLAY["strip"], width=2, fill=INK["field"],
                                      tags="strip_ruler")
            self.c_before.create_text(pxf + side * 9, oy + 12,
                                      text=f"{frac * 100:.0f}%",
                                      fill=OVERLAY["strip"], font=ui_font(8),
                                      anchor="w" if side > 0 else "e",
                                      tags="strip_ruler")

    def _strip_handle_y(self, px, oy, ih):
        """Canvas y of the handle on the ruler at *px*: mid-height, unless the
        tool palette sits there (`layout.strip_handle_y`)."""
        bar = getattr(self, "_addbar", None)
        box = None
        if bar is not None and bar.winfo_ismapped():
            bx, by = bar.winfo_x(), bar.winfo_y()
            box = (bx, by, bx + bar.winfo_width(), by + bar.winfo_height())
        return layout.strip_handle_y(px, oy + ih // 2, oy, oy + ih, box)

    def _strip_ruler_at(self, x, y=None):
        """Which ROI ruler (0=left, 1=right) sits near the handle, or None.

        The grab zone is the small circle in the middle of each line, not the
        whole line: a click meant for the picture does not accidentally drag
        the strip."""
        strip = self._strip_bounds()
        if strip is None or getattr(self, "_ph_b", None) is None:
            return None
        ox, oy = self._before_off
        iw, ih = self._ph_b.width(), self._ph_b.height()
        for i, f in enumerate(strip):
            px = ox + f * iw
            cy = self._strip_handle_y(px, oy, ih)
            if abs(x - px) <= 14:
                if y is None or abs(y - cy) <= 14:
                    return i
        return None

    def _on_strip_drag_start(self, idx):
        self._strip_drag = idx
        try:
            self.c_before.grab_set()
        except tk.TclError:
            pass

    def _on_strip_drag_move(self, event):
        """Move the grabbed ruler, clamped to the frame and to the other edge.

        The strip must stay >= 2 % wide (the same floor `_apply_strip` enforces) so a
        drag can never collapse it into an invalid, ignored state.  Updates the
        spinbox vars live; the refit happens once, on release."""
        if self._strip_drag is None or self.session is None:
            return
        ox = self._before_off[0]
        iw = self._ph_b.width()
        if iw <= 0:
            return
        f = (event.x - ox) / iw
        gap = 0.02
        if self._strip_drag == 0:
            f = min(max(f, 0.0), float(self.v_strip_x1.get()) / 100.0 - gap)
            self.v_strip_x0.set(round(f * 100.0, 1))
        else:
            f = max(min(f, 1.0), float(self.v_strip_x0.get()) / 100.0 + gap)
            self.v_strip_x1.set(round(f * 100.0, 1))
        self._draw_strip_rulers()

    def _on_strip_drag_release(self):
        if self._strip_drag is None:
            return
        self._strip_drag = None
        try:
            self.c_before.grab_release()
        except tk.TclError:
            pass
        self._apply_strip()

    def _draw_marks(self):
        """Control lines (vertical and horizontal), over the preview.

        Drawn by the canvas rather than burnt into the rendered image because
        they are interaction state, not detection: they have to appear the
        instant a click lands, without waiting for a re-render, and the pending
        first point has to be visible while it is still only half a line.  The
        width scales with the displayed size so a mark stays even on a small
        photograph instead of reading as a thick bar (see layout.mark_line_width).
        """
        ox, oy = self._before_off
        ph_b = getattr(self, "_ph_b", None)
        short = min(ph_b.width(), ph_b.height()) if ph_b is not None else 0
        lw = layout.mark_line_width(short)
        # Handles turn INWARD at the picture's edge instead of hanging over it.
        # A mark drawn along a facade at the frame's edge put its endpoint dots
        # and its delete button partly outside the photograph -- measured 4 px
        # out for a dot and 8 for the button -- where they are clipped, and
        # where a handle you cannot fully see is a handle you cannot aim at.
        iw = ph_b.width() if ph_b is not None else 0
        ih = ph_b.height() if ph_b is not None else 0

        def inside(cx, cy, rad):
            """Canvas coords for a handle of radius ``rad``, kept in frame."""
            return (min(max(cx, ox + rad), ox + max(iw - rad, rad)),
                    min(max(cy, oy + rad), oy + max(ih - rad, rad)))
        active_v = self.session.control_active
        for i, (x0, y0, x1, y1) in enumerate(
                self.session.control_lines_for_display(self._before_scale)):
            col = OVERLAY["mark_v"] if active_v else OVERLAY["mark_v_off"]
            self.c_before.create_line(ox + x0, oy + y0, ox + x1, oy + y1,
                                      fill=col, width=lw, tags="marks")
            for ex, ey in ((x0, y0), (x1, y1)):
                hx, hy = inside(ox + ex, oy + ey, 4)
                self.c_before.create_oval(hx - 4, hy - 4, hx + 4, hy + 4,
                                          fill=col, outline="", tags="marks")
            mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            self._draw_delete_handle(*inside(ox + mx, oy + my, 8), col,
                                     tag=(f"mark_del_v_{i}", "marks"))
        active_h = len(self.session.control_hlines) >= 2
        for i, (x0, y0, x1, y1) in enumerate(
                self.session.control_lines_for_display(
                    self._before_scale, kind="h")):
            col = OVERLAY["mark_h"] if active_h else OVERLAY["mark_h_off"]
            self.c_before.create_line(ox + x0, oy + y0, ox + x1, oy + y1,
                                      fill=col, width=lw, arrow="both",
                                      arrowshape=(9, 11, 4), tags="marks")
            for ex, ey in ((x0, y0), (x1, y1)):
                hx, hy = inside(ox + ex, oy + ey, 4)
                self.c_before.create_oval(hx - 4, hy - 4, hx + 4, hy + 4,
                                          fill=col, outline="", tags="marks")
            mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            self._draw_delete_handle(*inside(ox + mx, oy + my, 8), col,
                                     tag=(f"mark_del_h_{i}", "marks"))
        pend = getattr(self, "_pending_mark", None)
        if pend is not None:
            px, py = ox + pend[0], oy + pend[1]
            # One point leans no way yet, so it wears neither plane colour.
            self.c_before.create_oval(px - 6, py - 6, px + 6, py + 6,
                                       outline=OVERLAY["mark_handle"], width=2, tags="marks")

    def _draw_rect_quad(self):
        """Draw the four PC Rectangle corners and connecting edges on the before pane."""
        quad = self.session.planar_quad if self.session else []
        if not quad:
            return
        ox, oy = self._before_off
        s = self._before_scale
        pts = [(ox + px * s, oy + py * s) for px, py in quad]
        col = OVERLAY["sam"]
        n = len(pts)
        for i in range(n - 1):
            self.c_before.create_line(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1],
                                      fill=col, width=2, tags="rect_quad")
        if n >= 4:
            self.c_before.create_line(pts[3][0], pts[3][1], pts[0][0], pts[0][1],
                                      fill=col, width=2, tags="rect_quad")
        for i, (cx, cy) in enumerate(pts):
            r = 6
            self.c_before.create_rectangle(cx - r, cy - r, cx + r, cy + r,
                                           fill=INK["field"], outline=col, width=2,
                                           tags="rect_quad")
            self.c_before.create_text(cx + r + 8, cy - r - 4, text=str(i + 1),
                                      fill=col, font=ui_font(8),
                                      anchor="w", tags="rect_quad")

    def _draw_delete_handle(self, cx, cy, col, tag=""):
        r = 8
        kw = {"tags": tag} if tag else {}
        self.c_before.create_oval(cx - r, cy - r, cx + r, cy + r,
                                  fill=INK["field"], outline=col, width=2, **kw)
        d = 4
        # An X, not a plus: the plus reads as "add", which is the opposite of
        # what the handle does.
        self.c_before.create_line(cx - d, cy - d, cx + d, cy + d, fill=col, **kw)
        self.c_before.create_line(cx - d, cy + d, cx + d, cy - d, fill=col, **kw)
