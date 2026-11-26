"""Tkinter review window, with a rudimentary unattended batch.

One screen.  Each photograph opens for review and the decision can be overridden by hand:
sliders for roll, pitch and focal length, and a clickable overlay for striking
out the lines the detector should not have trusted.

Anything that is not toolkit plumbing lives in :mod:`pc.review`, which has no
Tkinter dependency and is covered by the test suite; the presentation layer
(palettes, fonts, glyphs, window chrome, tooltips) lives in
:mod:`pc.ui_theme`, re-exported from here for the callers that have always
found it in this module. This file is the shell.

Tkinter ships with the python.org installer on Windows, which is the target
platform.  On Linux it may need a separate package (``python3-tk``); the CLI
works without it.
"""
from __future__ import annotations

import logging
import math
import os
import sys
import queue
import threading
import time
import traceback

log = logging.getLogger("pc.gui")

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# Drag and drop is not in the standard library.  tkinterdnd2 provides it and is
# a small pure-Tcl extension, but the window has to work without it, so the drop
# zone doubles as a click target and says which mode it is in.
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _ROOT_CLASS = TkinterDnD.Tk
    HAVE_DND = True
except Exception:                                    # pragma: no cover
    DND_FILES = None
    _ROOT_CLASS = tk.Tk
    HAVE_DND = False

# macOS Tk numbers the right mouse button 2 and reports Option as state bit
# 0x0010; Windows and Linux say 3 and 0x0008 (2026-10-23, untested on a Mac).
_MAC = sys.platform == "darwin"
RB = "2" if _MAC else "3"
ALT_MASK = 0x0010 if _MAC else 0x0008
SHIFT_MASK = 0x0001     # Tk reports Shift as bit 0 on every platform

from PIL import ImageTk

from . import __version__
from . import layout
from . import lineoverlay as LO
from . import prefs
from .config import Settings
from .imageio import READABLE
from .pipeline import ERROR, OK, SKIPPED, process
from .inpaint import split_url as _split_url
from .review import AUTO, MANUAL, ReviewSession, darken_outside_crop

# The presentation layer lives in ui_theme; the names below stay importable
# from pc.gui for the callers (and the tests) that have always used them there.
from .ui_theme import (
    INK, OVERLAY, THEMES,
    ICON_WHITE, MASK_SWATCHES,
    GUIDE_GREY, CHECK_LINE_TOL_DEG,
    FONT_DIR, ICON_FONT, LOGO_FILE,
    _DRAWN_GLYPHS,
    _shorten_middle, _relative_luminance, contrast_ratio, on_accent,
    _clip_segment, ui_font,
    apply_theme, _retint_bg,
    _gradient_photo, _to_photo,
    _logo_image, _hex_rgba, _wordmark_image,
    _icon_pil, _load_key_icon, _glyph_pil, _beholder_pil,
    _set_window_icon, _dark_titlebar,
    _attach_tooltip,
)

# The setup, update and model-download dialogs the window drives.
from .ui_setup import _SetupMixin
# The ComfyUI settings window and its controls.
from .ui_comfy import _ComfyMixin
# The SAM2 box-prompt segmentation tool.
from .ui_sam import _SamMixin
# The loupe: the full-resolution magnifier over the before picture.
from .ui_loupe import _LoupeMixin
# The after pane's crop rectangle and its reference guides.
from .ui_crop import _CropGuidesMixin
# The mask panel and the mask brush: paint, erase, size, preview.
from .ui_brush import _BrushMaskMixin

# Names the window below does not use itself but that callers have always
# imported from pc.gui; keeping them listed so a re-export is visible.
__all__ = [
    "INK", "OVERLAY", "THEMES",
    "ICON_WHITE", "MASK_SWATCHES",
    "GUIDE_GREY", "CHECK_LINE_TOL_DEG",
    "FONT_DIR", "ICON_FONT", "LOGO_FILE",
    "_DRAWN_GLYPHS",
    "_shorten_middle", "_relative_luminance", "contrast_ratio", "on_accent",
    "_clip_segment", "ui_font",
    "apply_theme", "_retint_bg",
    "_gradient_photo", "_to_photo",
    "_logo_image", "_hex_rgba", "_wordmark_image",
    "_icon_pil", "_load_key_icon", "_glyph_pil", "_beholder_pil",
    "_set_window_icon", "_dark_titlebar",
    "_attach_tooltip",
]


# Only the detectors that work and earn a place; must match cli.py's
# --detector choices.  mlsd needs the TFLite runtime (ai-edge-litert).
DETECTORS = ("lsd", "mlsd")

RULER_MARGIN = 20

# A fast double-click on the Before pane is a LOAD gesture, not two edits:
# Tk delivers the two <Button-1> presses first and only then the
# <Double-Button-1>, so the panel must remember the state from before the
# FIRST press and restore it when the double arrives. This window covers the
# OS double-click time (Windows default 500 ms) with margin; a snapshot older
# than this is stale and is dropped rather than restored.
_DBL_WINDOW_MS = 700


_CPU_LIVE_TOLD = False      # once per run: see ReviewPanel._live_cpu_check


def _lama_installed():
    """Without importing it: simple_lama imports torch, which takes seconds."""
    from . import depinstall
    return depinstall.present("simple_lama_inpainting") and depinstall.present("torch")


class _NoticeLabel:
    """Stands in for the fill/ComfyUI label of the retired defaults panel.

    Callers still say ``lbl_fill.configure(text=...)``; the text now lands in
    the notice on the picture, where it is read, instead of in a label that
    was never on screen."""

    def __init__(self, app):
        self._app = app
        self._text = ""

    def configure(self, text="", **_kw):
        self._text = text or ""
        if self._text:
            self._app._notify(self._text)

    config = configure

    def cget(self, key):
        return self._text if key == "text" else ""


# ==========================================================================
# review window
# ==========================================================================
class _AfterOwner:
    """Mixin: auto-pruning Tk ``after()`` scheduler and cancellation.

    Both ``ReviewPanel`` and ``App`` need identical after-job tracking so
    scheduled callbacks are cancelled on destroy instead of firing against
    dead widgets.  Shared here as a mixin so the logic lives in exactly one
    place.  The host class initialises ``self._after_jobs`` as a ``set``
    before any ``_schedule_after`` call.
    """

    def _schedule_after(self, ms, fn):
        """Schedule an after() callback, auto-pruning its ID once it fires."""
        box = []

        def _run():
            if box:
                self._after_jobs.discard(box[0])
            if self.winfo_exists():
                fn()

        job = self.after(ms, _run)
        box.append(job)
        self._after_jobs.add(job)
        return job

    def _cancel_after_jobs(self):
        """Cancel every tracked after() ID; safe to call repeatedly."""
        for job in list(self._after_jobs):
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass  # already fired or cancelled
        self._after_jobs.clear()


class ReviewPanel(tk.Frame, _AfterOwner, _SamMixin, _LoupeMixin, _CropGuidesMixin, _BrushMaskMixin):
    # The review UI, embedded in the batch window.  One frame for the whole
    # task: a second window split one photograph across two frames and stole
    # focus on every double-click.  Built once; `load` swaps the photograph
    # into the same widgets.
    def __init__(self, master):
        super().__init__(master, background=INK["cross"])
        # The cross: two previews on top, loader and controls below -- divided
        # by a flat dark cross and ringed by a dark border, both
        # CROSS_GAP/CROSS_BORDER wide. One grid of uniform rows and columns makes
        # the split a property of the layout rather than an agreement between two
        # arrangements; `load` destroys and rebuilds three of the four fields,
        # the loader survives. The rows are broken vertically (2026-10-19,
        # user-directed): the pictures on top take the larger share, see
        # CROSS_TOP_WEIGHT / CROSS_BOT_WEIGHT below.
        b = layout.CROSS_BORDER
        g = layout.CROSS_GAP // 2
        # The cross and the border ARE this widget's own background showing
        # through the grid gaps -- there is no separate bar to click. Children
        # capture their own events, so a <Motion> that reaches `self` is a
        # pointer over the gutter or the border and nowhere else. That is what
        # makes "drag a ruler out of the black cross" implementable at all.
        self.bind("<Motion>", self._on_cross_motion)
        self.bind("<Leave>", self._on_cross_leave)
        # Guides are pulled out of the cross itself. Only events that reach the
        # panel are over the gutter or the border -- every child canvas eats its
        # own -- so no hit-testing against the fields is needed.
        self.bind("<Button-1>", self._on_cross_press)
        self.bind("<B1-Motion>", self._on_cross_pull)
        self.bind("<ButtonRelease-1>", self._on_cross_drop)
        # The cross is broken vertically (2026-10-19, user-directed): the lower
        # row sizes to its own content (per-photograph controls + run bar, the
        # batch defaults are gone and the adjustments panel opens collapsed) and
        # the upper row takes every remaining pixel, so the two preview images
        # are as tall as the window allows.  No uniform group: a fixed ratio
        # would clip the run bar whenever the controls' request outgrows it.
        self.rowconfigure(0, weight=layout.CROSS_TOP_WEIGHT)
        self.rowconfigure(1, weight=layout.CROSS_BOT_WEIGHT)
        self.columnconfigure(0, weight=1, uniform="cross_cols")
        self.columnconfigure(1, weight=1, uniform="cross_cols")
        self.cell_before = ttk.Frame(self)
        self.cell_after = ttk.Frame(self)
        self.loader = ttk.Frame(self)              # rebuilt by the batch window
        self.cell_ui = ttk.Frame(self)
        # Each cell carries half the cross on its inner sides and the full
        # border on its outer ones, so the dark shows through at exactly
        # CROSS_GAP between fields and CROSS_BORDER around the panel.
        # One width all round, the top included (user, 2026-10-21): see
        # layout.CROSS_PX.
        sd = layout.CROSS_SIDE
        self.cell_before.grid(row=0, column=0, sticky="nsew", padx=(sd, g), pady=(b, g))
        self.cell_after.grid(row=0, column=1, sticky="nsew", padx=(g, sd), pady=(b, g))
        self.loader.grid(row=1, column=0, sticky="nsew", padx=(sd, g), pady=(g, b))
        self.cell_ui.grid(row=1, column=1, sticky="nsew", padx=(g, sd), pady=(g, b))
        self.session = None
        self.settings = None
        self.dest_path = None
        self.on_saved = None
        # Fired however the review goes away -- saved, kept, or closed.  A
        # queue that only advances on Save stalls forever on the first
        # photograph someone skips.
        self.on_closed = None
        self._closed_sent = False
        self._busy = False
        self._before_scale = 1.0
        self._redraw_tries = 0
        # Fit-once-then-lock: the (before, after) canvas size the picture is
        # rendered at.  Set on the first real draw, reused for every redraw, so
        # a change in the control strip's height (mode switch, collapse) cannot
        # re-scale the picture and make it jump.  Reset to None on a new photo.
        self._img_box = None
        # Guide state exists before any photograph: the after canvas takes
        # presses and releases while it is still empty, and `_on_crop_release`
        # used to raise AttributeError on `_guide_drag` there.
        self._after_guides = []
        self._guide_drag = None
        self._guide_new = None
        self._crop_drag_start = None
        # Tracking set for all after() IDs scheduled by this panel.
        # Cancelled in destroy() to avoid "invalid command name" on teardown.
        self._after_jobs = set()
        self._show_hint()

    def _show_hint(self):
        # The cross is shown from the first frame, not after the first load:
        # two empty image slots and both control columns, so the window has one
        # shape whether or not a photograph is in it.  A default Settings gives
        # the controls their values; `load` rebuilds with the real ones.
        self._clear_cells()
        self.session = None
        self.settings = Settings()
        self._build()

    def _clear_cells(self):
        """Empty the three fields this panel rebuilds; the loader is not one.

        The loader field belongs to the batch window and must survive a
        `load`, which destroys and rebuilds everything else.
        """
        for cell in (self.cell_before, self.cell_after, self.cell_ui):
            for w in cell.winfo_children():
                if getattr(w, "_bpc_persistent", False):
                    continue      # the batch window's own frames, not a photograph's
                w.destroy()

    def load(self, path, settings, dest_path, overwrite=False, on_saved=None,
             on_closed=None, position=""):
        # Before the old session goes: each tool leaves by its own handler, and
        # those handlers clean up against the picture the work belongs to.
        self._put_down_tools()
        self.settings = settings
        self.dest_path = dest_path
        self.on_saved = on_saved
        self.on_closed = on_closed
        self._closed_sent = False
        self._busy = False
        self._before_scale = 1.0
        self._redraw_tries = 0
        self._img_box = None
        self._after_guides = []
        self._guide_drag = None
        self._guide_new = None
        self._cross_pull = None
        self._clear_cells()
        try:
            self.session = ReviewSession(path, settings)
        except Exception as exc:
            ttk.Label(self.cell_before, foreground="red", justify="left",
                      text=f"cannot open image:\n{exc}").pack(expand=True)
            self._fire_closed()
            return
        app = self._app()
        if app is not None:
            W, H = self.session.w, self.session.h
            title = (f"{position}  {os.path.basename(path)}  {W}×{H}  |  "
                     f"Perspective Correction  v{__version__}")
            app.title(title.strip())
        # The mode switch mirrors the horizontal-auto state the photograph
        # opens with, and it must be set BEFORE `_build`: `_build` calls
        # `_show_mode`, which enforces whatever the switch says. Set after,
        # the switch still said "horizontal" from the last photo, `_show_mode`
        # turned HA on for this one, and then the switch was put back to
        # "auto" -- Auto shown, HA applied (user, 2026-10-24: "HA kommt noch
        # zum Tragen, obwohl Auto-Modus suggeriert wird").
        was = self.v_corr_mode.get()
        self.v_corr_mode.set("horizontal" if self.settings.correct_horizontal else "auto")
        self._build()
        self._mode_frames[self.v_corr_mode.get()].tkraise()
        if was == "horizontal" and self.v_corr_mode.get() == "auto":
            self._schedule_after(300, lambda: self._notify(
                "new photo opens in Auto -- horizontal auto was on for the last one"))
        self.v_overwrite.set(bool(overwrite))
        self._refresh_file_names()
        self.v_alpha.set(self.session.mask_alpha)
        self.session.on_fill_step = self._fill_step
        self._show_mask_state()
        # Small correction, small band: take the crop rather than leave a band
        # that would otherwise need a generative model to fill.  Visible, shaded
        # and undoable with "Reset crop" -- see `auto_crop_if_cheap`.
        if self.session.auto_crop_if_cheap():
            self._refresh_crop()
        # A corner view mixes two facades' worth of horizontal evidence into
        # one estimate (2026-09-26, user-directed, replaces the removed
        # corner-view pitch damping): point at it rather than guess. Opens
        # the strip rulers already showing and says why, instead of a
        # silently-wrong auto estimate the user has to notice is off first.
        if self.session.directions_diverge():
            # ROI stays OFF by default (user, 2026-10-20): the strip is a
            # deliberate choice, not an auto-applied one. We still point at the
            # divergence so the user can pick a facade if they want to.
            self._set_status("multiple main directions detected -- pick a facade with the Facade strip if you want to")
        self._schedule_after(60, self._sync_from_session)
        # "Unchanged" is the state once opening has settled -- the automatic
        # crop and the widget sync above are the program's, not the user's.
        self._mark_clean()
        self._schedule_after(120, self._mark_clean)
        # Once per run, not per photograph: the first one is where it matters.
        if not getattr(self, "_lama_told", False):
            self._lama_told = True
            self._schedule_after(400, self._fill_fallback_notice)

    def _fill_fallback_notice(self, note=""):
        """lama chosen and not installed: say telea fills instead, and how to
        get lama (user, 2026-10-23: on the first Mac it fell back silently).
        ``note`` is a save's fill note, which names the fallback when it ran."""
        if not self.winfo_exists():
            return
        if note:
            if "unavailable" not in note:
                return False
        elif self.v_fill.get() != "lama" or _lama_installed():
            return False
        self._notify("lama is not installed -- telea fills the band instead. "
                     "Install it with 'deps' at the bottom right.")
        return True

    def _fire_closed(self):
        if self.on_closed and not self._closed_sent:
            self._closed_sent = True
            self.on_closed()

    def _close(self):
        # The old window's X button: fire the queue hook, then clear the panel.
        self._fire_closed()
        self._show_hint()

    def destroy(self):
        """Every way this window goes away drops its listener.

        `_save` and `_skip` call `destroy()` directly, so hanging the removal
        off `_on_close` would leave a dead widget in the App's list after the
        commonest exit of all.  `_show_comfy_state` catches and drops a stale
        listener anyway -- but that net is for the unexpected, not for the
        normal path.
        """
        self._cancel_after_jobs()
        self._unregister_comfy()
        super().destroy()

    def _target_path(self):
        """Where Save writes: the original, or the ``_corr`` copy beside it."""
        return self.session.path if self.v_overwrite.get() else self.dest_path

    def _refresh_file_names(self):
        """Name the photograph on each side of the cross.  `before` is the input;
        `after` is where Save will write it (the original itself when overwrite is
        set).  Only the middle of a long name is elided -- the extension and the
        _corr suffix always survive, so the end that identifies a file never goes."""
        if self.session is None:
            return
        src = os.path.basename(self.session.path)
        dst = os.path.basename(self._target_path())
        self._before_title = f"before  {_shorten_middle(src)}"
        self._after_title = f"after  {_shorten_middle(dst)}"
        self._schedule_redraw()

    # -- layout ----------------------------------------------------------
    def _build(self):
        # No brand header here: the panel sits inside the batch window, which
        # carries its own.
        # The controls are one of the four fields, not a strip under two of
        # them: `cell_ui` is the lower-right box of the cross and everything
        # from the hint down to the Save row lives inside it.
        # This frame is the per-photograph controls (the sliders and the Save
        # row) -- the manual-review instrument, which is the product. It is
        # packed side="top" WITH expand so it absorbs any spare height the lower
        # row has left over (the row now sizes to the taller of this field and
        # the loader, and the batch defaults that used to clip first are gone):
        # the controls keep their full height and the spare lands above them,
        # never the run bar at the foot.
        top = ttk.Frame(self.cell_ui, padding=0)
        top.pack(side="top", fill="x", anchor="n")

        # The two previews are the upper fields.  Nothing here divides
        # anything: the split between them is the cross's own grid, so the two
        # preview fields and the two lower fields are the same size by
        # construction rather than by two arrangements agreeing.
        # width/height 1: a tk.Canvas asks for 378x265 by default, and that
        # request is what pushed the Save row off the bottom of a 1080p window
        # -- the picture claimed a size it had not earned while the buttons
        # took what was left.  The canvases expand into their field instead.
        # P15: name the photograph on each side.  `before` is what you are
        # correcting; `after` is where Save will write it.  The names are filled
        # in by `_refresh_file_names` once the overwrite decision is known.
        # The picture titles are drawn into the pictures (user, 2026-10-20:
        # "Bildtitel als Overlay, alles Bild"): no label row above the canvas.
        self._before_title = "before"
        self.c_before = tk.Canvas(self.cell_before, bg=INK["field"],
                                  highlightthickness=0, width=1, height=1)
        self.c_before.pack(fill="both", expand=True)
        # The add triggers live in the before-image's own top-left corner, in front
        # of the picture: a grey "+" for images and a grey folder for a folder.
        # Grey rather than accent -- they are part of the ground, not a call to
        # action; the cross keeps its one colour of emphasis elsewhere.  The frame
        # is re-created on every `load` (the canvas is rebuilt), so the buttons
        # track the panel's lifecycle for free.
        addbar = tk.Frame(self.c_before, bg=INK["field"])
        self._addbar = addbar
        # Stacked downwards, not across (2026-09-13, user-directed): this corner
        # is a tool palette, and a palette reads as a column.  Load is the big one
        # -- it is the only thing to press on an empty window, so it earns the
        # size; the tools below it are small and uniform.
        if getattr(self, "_palette_blank", None) is None:
            self._palette_blank = tk.PhotoImage(width=1, height=1)
        _side, _gap = layout.tool_key()
        self._add_img = ImageTk.PhotoImage(_load_key_icon("E145", "add"))
        self.add_btn = tk.Button(addbar,
                                 image=self._add_img, compound="center",
                                 width=_side, height=_side,
                                 relief="flat", bd=0, cursor="hand2",
                                 background=INK["field"], foreground=INK["dim"],
                                 activebackground=INK["line"],
                                 activeforeground=INK["text"],
                                 padx=0, pady=0, highlightthickness=0, command=self._on_add_files)
        self.add_btn.pack(side="top")
        _attach_tooltip(self.add_btn, "Add image files")
        self._folder_img = ImageTk.PhotoImage(_load_key_icon("E2C7", "folder"))
        self.add_folder_btn = tk.Button(addbar, image=self._folder_img, relief="flat",
                                        bd=0, cursor="hand2", background=INK["field"],
                                        width=_side, height=_side, compound="center",
                                        padx=0, pady=0, highlightthickness=0, command=self._on_add_folder)
        self.add_folder_btn.pack(side="top", pady=(_gap, 0))
        _attach_tooltip(self.add_folder_btn, "Add a folder of images")
        self._paste_img = ImageTk.PhotoImage(_load_key_icon("E14F", "paste"))
        self.add_paste_btn = tk.Button(addbar, image=self._paste_img, relief="flat",
                                       bd=0, cursor="hand2", background=INK["field"],
                                       width=_side, height=_side, compound="center",
                                       padx=0, pady=0, highlightthickness=0, command=self._on_paste)
        self.add_paste_btn.pack(side="top", pady=(_gap, 0))
        _attach_tooltip(self.add_paste_btn, "Paste screenshot from clipboard")
        # Placed rather than packed: the frame is a child of the canvas and sits
        # over its top-left corner, in front of whatever the picture shows.  A
        # placed child is drawn above the canvas's own content and stays fixed at
        # that corner -- it does not scroll or rescale with the image.
        # Vertically centred on the field's left edge (user, 2026-10-21:
        # "tools too high up, middle them out").
        # One unbroken column. It was split at mid-height for the facade
        # strip's handle (2026-10-23) and the gap read as a missing tool
        # (user, 2026-10-24: "Loch in tools", "zwischen Paste und Mark Luecke").
        # The handle dodges the palette instead (`_strip_handle_y`).
        addbar.place(x=8, rely=0.5, anchor="w")
        # Lines and Mask draw on *this* image, so their switches sit on it --
        # top-right, opposite the add icons, and mirroring the Grid switch on the
        # after pane (2026-09-13, user-directed).  Made once and reused because
        # `_build` re-runs on every load: fresh variables here would reset both
        # toggles for each photograph and leave the old canvas' checkbuttons
        # pointing at dead ones.
        if getattr(self, "v_show_lines", None) is None:
            self.v_show_lines = tk.BooleanVar(value=True)
            self.v_show_mask = tk.BooleanVar(value=True)
            self.v_mask_color = tk.StringVar(value=OVERLAY["mask_default"])
            self.v_mask_alpha = tk.DoubleVar(value=0.50)
        ovbar = tk.Frame(self.c_before, bg=INK["field"])
        self._ovbar = ovbar
        ttk.Checkbutton(ovbar, text="Lines", command=self._schedule_redraw,
                        variable=self.v_show_lines).pack(side="left", padx=(4, 0))
        ttk.Checkbutton(ovbar, text="Mask", command=self._toggle_mask,
                        variable=self.v_show_mask).pack(side="left", padx=4, pady=2)
        # Mask colour + opacity: the wash is a fixed red by default, but a user
        # judging a red facade needs another colour.  The swatch opens the system
        # colour picker; the slider reuses the same variable as the mask panel's
        # "mask opacity" so both stay in sync.
        self._mask_swatch = tk.Button(ovbar, width=3, relief="flat", bd=0,
                                      bg=self.v_mask_color.get(),
                                      command=self._pick_mask_color)
        self._mask_swatch.pack(side="left", padx=(6, 2))
        _attach_tooltip(self._mask_swatch, "Mask overlay colour")
        ttk.Scale(ovbar, from_=0.05, to=1.0, variable=self.v_mask_alpha,
                  orient="horizontal", length=80,
                  command=lambda _v: self._on_mask_alpha()).pack(side="left", padx=2)
        ovbar.place(relx=1.0, x=-8, y=8, anchor="ne")
        self._after_title = "after"
        self.c_after = tk.Canvas(self.cell_after, bg=INK["field"],
                                 highlightthickness=0, width=1, height=1)
        self.c_after.pack(fill="both", expand=True)
        # The grid is a ruler laid over the *corrected* frame -- the instrument
        # you judge the result with -- so its switch belongs on that image rather
        # than in a control box across the window (2026-09-13, user-directed).
        # Top-right of the after pane, mirroring the add icons in the before
        # pane's top-left.  Built here, beside the canvas it sits on, because
        # `_build` re-runs on every load and a bar parented to the previous
        # canvas dies with it; the variables are made once so the switch does not
        # flip itself off each time a photograph opens.
        if getattr(self, "v_after_lines", None) is None:
            self.v_after_lines = tk.BooleanVar(value=False)
        if getattr(self, "v_after_outline", None) is None:
            self.v_after_outline = tk.BooleanVar(value=False)
        gridbar = tk.Frame(self.c_after, bg=INK["field"])
        self._gridbar = gridbar
        _ol = ttk.Checkbutton(gridbar, text="Outline", command=self._toggle_outline,
                              variable=self.v_after_outline)
        _ol.pack(side="left", padx=(4, 0))
        _attach_tooltip(_ol, "Draw the photograph's own border where the warp put it")
        _cl = ttk.Checkbutton(gridbar, text="Check lines", command=self._schedule_redraw,
                              variable=self.v_after_lines)
        _cl.pack(side="left", padx=(4, 0))
        _attach_tooltip(_cl, "Re-detect lines on the corrected picture:\n"
                             "green = truly vertical or level, red = still leans.")
        gridbar.place(relx=1.0, x=-8, y=8, anchor="ne")
        self._pending_mark = None
        self._pair_snap = None        # pre-first-press state of a fast double pair
        self._pair_snap_t = 0.0       # event time of that first press
        self._after_off = (0, 0)
        self._crop_drag_start = None
        self._strip_drag = None         # which ROI ruler is grabbed: 0=left, 1=right
        if getattr(self, "_loupe", None) is not None:   # _build re-runs on load
            self._loupe.destroy()
        self._loupe = None            # magnifying-glass canvas over the cross, or None
        self._loupe_center = None     # damped crop centre (image px) for Alt tracking
        self._drag_job = None         # throttled after-preview while dragging
        self._fill_gen = 0            # bumps on every render; a stale fill is dropped
        self._fill_after = None       # timer that starts the deferred fill
        self._fill_thread = None
        self._fill_q = queue.Queue()
        # A rebuild (new image loaded) kills the glass; it is raised again on
        # the next mark press rather than kept up while the mode is on.
        if self._tool_active("v_mark"):
            pass  # nothing to restore: the loupe now lives only during a drag
        self.c_before.bind("<Button-1>", self._on_click_before)
        # A fast double-click on the Before pane opens the load dialog and
        # undoes the two clicks it is made of -- see `_on_before_double_click`.
        self.c_before.bind("<Double-Button-1>", self._on_before_double_click)
        self.c_before.bind("<Motion>", self._on_before_motion)
        self.c_before.bind("<B1-Motion>", self._on_before_b1motion)
        self.c_before.bind("<ButtonRelease-1>", self._on_before_b1release)
        # Photoshop's gestures, because this is a brush and those are the ones in
        # everybody's hands already: left paints, right erases, Alt+right dragged
        # sideways sizes the pen.  The Alt bindings are declared first only for
        # readability -- Tk picks the more specific pattern regardless of order.
        self.c_before.bind("<Alt-ButtonPress-1>", self._on_alt_erase_press)
        self.c_before.bind(f"<Alt-ButtonPress-{RB}>", self._on_pen_size_start)
        self.c_before.bind(f"<Alt-B{RB}-Motion>", self._on_pen_size_drag)
        # The sizing drag had a press and a motion and no END. Alt+right
        # release arrives as <Alt-ButtonRelease-3>, which nothing was bound
        # to, so `_pen_anchor` stayed set forever -- and `_on_erase_motion`
        # bails out while it is set. After sizing the pen once, right-drag
        # erase silently degraded to erasing the press and release points
        # only, with the whole dragged stroke dropped. Measured, not read.
        self.c_before.bind(f"<Alt-ButtonRelease-{RB}>", self._on_pen_size_end)
        # Krita's gesture as well (user, 2026-10-20: "wie krita left shift
        # click drag"): Shift+left dragged sideways sizes the pen.  Both stay
        # bound.  With the brush down these handlers fall through to the plain
        # left-button ones, so Shift+click elsewhere still clicks.
        self.c_before.bind("<Shift-ButtonPress-1>", self._on_shift_press)
        self.c_before.bind("<Shift-B1-Motion>", self._on_shift_motion)
        self.c_before.bind("<Shift-ButtonRelease-1>", self._on_shift_release)
        # Alt+left had a press binding and no motion or release of its own,
        # working only through Tk falling back to the modifier-less pattern.
        # Bound explicitly rather than left to that rule.
        self.c_before.bind("<Alt-B1-Motion>", self._on_before_b1motion)
        self.c_before.bind("<Alt-ButtonRelease-1>", self._on_before_b1release)
        # ONE bind per sequence. Tk's bind() *replaces* a handler for the same
        # sequence rather than adding to it, so binding both here left
        # `_on_sam_right_click` silently dead -- SAM's right-click (clear the
        # prompt points) did nothing at all. Dispatch instead; both handlers
        # already guard themselves (`v_sam` / `_brush_live`), so they cannot
        # both act.
        self.c_before.bind(f"<ButtonPress-{RB}>", self._on_right_press)
        self.c_before.bind(f"<B{RB}-Motion>", self._on_erase_motion)
        self.c_before.bind(f"<ButtonRelease-{RB}>", self._on_erase_release)
        self.c_after.bind("<ButtonPress-1>", self._on_crop_press)
        self.c_after.bind("<B1-Motion>", self._on_crop_drag)
        self.c_after.bind("<ButtonRelease-1>", self._on_crop_release)
        self.c_after.bind("<Motion>", self._on_crop_motion)
        self.c_after.bind("<Leave>", lambda e: self.c_after.config(cursor=""))
        for c in (self.c_before, self.c_after):
            c.bind("<Configure>", lambda e: self._schedule_redraw())
        # The before slot is a drop target too, so a file can land on the picture
        # area as well as the strip above it.  Guarded: under a test root there
        # is no batch window to receive the drop.
        if HAVE_DND and self._app() is not None:
            self.c_before.drop_target_register(DND_FILES)
            self.c_before.dnd_bind("<<Drop>>", self._app()._on_drop)

        # -- the control strip: one grid, one module per topic ------------------
        # (2026-10-20, user-directed: nothing folds out, everything open, as
        # small as it goes, rows tight, sorted into a grid by topic, no jumping.)
        #   row 0   mode switch | fill band | crop
        #   row 1   the active mode's controls (all three share ONE cell, so the
        #           strip is as tall as the tallest and never changes height)
        #   row 2   Auto / Reset | Keep, Close, Save
        # Each topic is its own `_strip_*` method taking the cell it fills, so a
        # topic can change without touching its neighbours.  The mode lives on
        # the panel (`v_corr_mode`, made once) because `_build` destroys every
        # child and rebuilds on each photograph.
        if getattr(self, "v_corr_mode", None) is None:
            self.v_corr_mode = tk.StringVar(value="auto")
        self.v_roll = tk.DoubleVar(value=0.0)
        self.v_pitch = tk.DoubleVar(value=0.0)
        self.v_focal = tk.DoubleVar(value=28.0)
        self.v_correct_horizontal = tk.BooleanVar(value=self.settings.correct_horizontal)
        self.v_yaw = tk.DoubleVar(value=0.0)
        # Made once: a preference about output size, not a property of the
        # picture.  The trace goes on with it (a `ttk.Scale` fires `command`
        # only when the widget itself moves, so the variable is watched).
        if getattr(self, "v_keep_px", None) is None:
            self.v_keep_px = tk.DoubleVar(value=self.settings.keep_pixels)
            self.v_keep_px.trace_add("write", lambda *_a: self._apply_keep_px())
        # Tool-state variables, made once for the same reason (a rebuilt
        # variable is a stale one: the tool died from the second photograph on).
        if getattr(self, "v_stroke", None) is None:
            self.v_stroke = tk.BooleanVar(value=False)
            self.v_stroke_w = tk.IntVar(value=60)
        if getattr(self, "v_mark", None) is None:
            self.v_mark = tk.BooleanVar(value=False)
        if getattr(self, "v_rect", None) is None:
            self.v_rect = tk.BooleanVar(value=False)
            self._rect_pending = []
        if getattr(self, "v_sam", None) is None:
            self.v_sam = tk.BooleanVar(value=False)
            self._sam_box = None
            self._sam_box_px = None
            self._sam_points = []
            self._sam_selection = None
        if getattr(self, "v_autocrop", None) is None:
            self.v_autocrop = tk.BooleanVar(value=True)
        if getattr(self, "v_livefill", None) is None:
            self.v_livefill = tk.BooleanVar(value=self._cfg().live_fill_preview)
        self.v_fill = tk.StringVar(value=self._cfg().fill or "none")
        self.v_overwrite = tk.BooleanVar(value=False)

        # Three rows read top to bottom as the workflow (user: logical, dense,
        # controls fill their menus): 1 WHICH correction (mode switch, with the
        # two actions that act on the angles), 2 ITS values (full width), 3 the
        # OUTPUT (fill, crop, then keep / close / save).  Four equal columns, so
        # every cell is filled edge to edge.
        strip = ttk.Frame(top)
        self._strip = strip
        for _c in range(4):
            strip.columnconfigure(_c, weight=1, uniform="strip")

        def cell(row, col, span=1):
            f = ttk.Frame(strip)
            f.grid(row=row, column=col, columnspan=span, sticky="nsew")
            return f

        # Q4's top row is split in three: the mode switch (user, 2026-10-20),
        # the active mode light, the others dark.  Below it the mode's values,
        # then the output.
        self._btns2 = cell(0, 0, 4)               # row 0: the mode switch
        self._adj = cell(1, 0, 4)                 # row 1: the active mode's values
        self._btns = cell(2, 0, 4)                # row 2: the output
        self._strip_mode(self._btns2)
        self._strip_controls(self._adj)
        # Auto-fit / Reset act on the angles, so they sit with the angle values.
        _ang = ttk.Frame(self._adj)
        _ang.grid(row=0, column=1, sticky="nsew")
        self._strip_angles(_ang)
        self._strip_fill(self._btns)
        self._strip_crop(self._btns)
        self._strip_actions(self._btns)
        # The palette in the picture's top-left corner can only be finished
        # here: it toggles the variables above.  It is the one way to the
        # tools: the Manual mode that repeated its keys is gone (user,
        # 2026-10-21) -- a drawn line overrules the detector in any mode.
        self._build_tool_palette()
        self._show_mode()

        # -- assembly: who gives up height first ---------------------------
        # `pack` hands each child its requested height in call order, and the
        # cavity left over goes to the expanding one, so the strip claims its
        # height first and the picture takes whatever survives.
        strip.pack(side="top", fill="x", anchor="n")
        # The status text no longer has a label of its own: it is drawn as a
        # faint overlay inside the after picture field (see
        # _draw_status_overlay), so the strip sits directly under the pictures
        # and the field is taller.  The text is kept as a plain string here.
        self._status_text = ""

    def _strip_mode(self, row):
        """Topic: which correction is being driven (Auto / Horizontal auto)."""
        tips = {
            "auto": "Straighten converging verticals and level the frame:\n"
                    "roll, pitch and focal length, a camera rotation.",
            "horizontal": "Turn an obliquely shot facade into a frontal view:\n"
                          "verticals plumb AND horizontals level, a true rectangle.\n"
                          "For textures, street-view grabs and elevation tracing.\n"
                          "On a corner view place the facade strip on one wall.",
        }
        for _txt, _val in (("Auto", "auto"), ("Horizontal auto", "horizontal")):
            _rb = ttk.Radiobutton(row, text=_txt, value=_val,
                                  style="Mode.Toolbutton",
                                  variable=self.v_corr_mode,
                                  command=self._show_mode)
            _rb.pack(side="left", fill="x", expand=True)
            _attach_tooltip(_rb, tips[_val])

    @staticmethod
    def _put(row, widget, weight=2):
        """Put a widget in the next column of an output row.  Every column has
        a weight and every widget fills its cell (`nsew`), so the row has no
        gaps and no background showing: the whole width is controls."""
        col = getattr(row, "_ncols", 0)
        row._ncols = col + 1
        widget.grid(row=0, column=col, sticky="nsew")
        row.columnconfigure(col, weight=weight)
        return widget

    def _strip_fill(self, row):
        """Topic: how the opened band is filled."""
        self._put(row, ttk.Label(row, text="fill", anchor="center"), 0)
        fbox = ttk.Combobox(row, textvariable=self.v_fill, width=6,
                            style="Compact.TCombobox", state="readonly",
                            values=["none", "telea", "lama", "comfyui"])
        self._put(row, fbox, 2)
        fbox.bind("<<ComboboxSelected>>", lambda e: self._apply_fill())
        _lf = ttk.Checkbutton(row, text="live", variable=self.v_livefill,
                              style="Compact.Toolbutton",
                              command=self._apply_fill)
        self._put(row, _lf, 1)
        _attach_tooltip(
            _lf,
            "Preview the fill you CHOSE, instead of the telea stand-in.\n"
            "The band is always filled here -- telea draws it in 38 ms\n"
            "so you can see how much is being invented. Tick this to\n"
            "see what lama will actually put there: about a second per\n"
            "redraw (measured at preview size), fine once and unusable\n"
            "while a slider moves. Off by default.")
        # ComfyUI needs a server address and a workflow; the unnamed default
        # graph is the inpainting one.  Same window as the batch panel's.
        self.btn_comfy = ttk.Button(row, text="ComfyUI", style="Compact.TButton",
                                    command=self._open_comfy)
        self._put(row, self.btn_comfy, 2)
        _attach_tooltip(self.btn_comfy, "Configure the ComfyUI server address and inpainting workflow")
        # Status text for the ComfyUI setup; a tooltip-sized note, kept out of
        # the row so an empty one costs no width.
        self.lbl_comfy = ttk.Label(self._strip, text="", style="Dim.TLabel")
        self._register_comfy()

    def _strip_crop(self, row):
        """Topic: the crop.  A SWITCH, not a one-shot (user, 2026-09-21): as a
        button it fired once when the photograph opened and never again."""
        _b = ttk.Checkbutton(row, text="Auto crop", variable=self.v_autocrop,
                             style="Compact.Toolbutton",
                             command=self._auto_crop)
        self._put(row, _b, 2)
        _attach_tooltip(
            _b,
            "Keep the crop following the correction.\n"
            "Switches itself off when you drag a crop by hand --\n"
            "a rectangle you drew is a decision, not a suggestion.")
        # One tooltip per widget: Tk's bind() REPLACES a handler.
        _b = ttk.Button(row, text="Reset crop", style="Compact.TButton",
                        command=self._clear_crop)
        self._put(row, _b, 2)
        _attach_tooltip(
            _b,
            "Remove the crop entirely, including one you drew.\n"
            "Different from switching Auto crop off: that only lets go\n"
            "of the automatic rectangle and leaves yours alone.")

    def _strip_controls(self, cell):
        """Topic: the three modes' controls, stacked in ONE grid cell so the
        strip never changes height when the mode does."""
        self._mode_frames = {k: ttk.Frame(cell)
                             for k in ("auto", "horizontal")}
        for f in self._mode_frames.values():
            f.grid(row=0, column=0, sticky="nsew")
        cell.columnconfigure(0, weight=1)

        a = self._mode_frames["auto"]
        self._slider_h(a, 0, "roll", self.v_roll, -20, 20, 0.1, "%.2f")
        self._slider_h(a, 1, "pitch", self.v_pitch, -30, 30, 0.1, "%.2f")
        self._slider_h(a, 2, "focal", self.v_focal, 8, 200, 1, "%.0f")

        # Horizontal: yaw -- a 2D plane extraction, not a perspective
        # correction.  The line is the switch (a drawn horizontal gives the yaw,
        # delete it and the vanishing point has it back): `marker_yaw`.
        h = self._mode_frames["horizontal"]
        _hchk = ttk.Checkbutton(h, text="horizontal auto (yaw)",
                                style="Compact.TCheckbutton",
                                variable=self.v_correct_horizontal,
                                command=self._on_horizontal_toggle)
        _hchk.grid(row=0, column=0, sticky="w")
        _attach_tooltip(
            _hchk,
            "Send the facade's horizontal vanishing point to infinity:\n"
            "the wall comes out frontal, horizontals level.\n"
            "A drawn horizontal line overrules the detector.\n"
            "On a corner view only one wall can be frontal.")
        self._yaw_scale = ttk.Scale(h, from_=-60, to=60, variable=self.v_yaw,
                                    orient="horizontal", length=layout.SLIDER_MIN,
                                    command=lambda _v: self._on_slider())
        self._yaw_scale.grid(row=0, column=1, sticky="ew", padx=3)
        self._yaw_spin = ttk.Spinbox(h, textvariable=self.v_yaw, from_=-60, to=60,
                                      increment=0.1, format="%.2f", width=6,
                                      style="Compact.TSpinbox",
                                      command=self._on_slider)
        self._yaw_spin.grid(row=0, column=2, sticky="e")
        self._yaw_spin.bind("<Return>", lambda _e: self._on_slider())
        h.columnconfigure(1, weight=1, minsize=layout.SLIDER_MIN)
        h.columnconfigure(4, weight=1, minsize=layout.SLIDER_MIN)
        # How much resolution the yaw warp may throw away.  Built by hand: the
        # write lives on the variable's trace, not on the scale's command.
        ttk.Label(h, text="detail (px kept)").grid(
            row=0, column=3, sticky="w", padx=(layout.CTRL_COL_GAP_PX, 0))
        self._keep_px_scale = ttk.Scale(h, from_=0.0, to=1.0, length=layout.SLIDER_MIN,
                                        variable=self.v_keep_px,
                                        orient="horizontal")
        self._keep_px_scale.grid(row=0, column=4, sticky="ew", padx=3)
        self._keep_px_read = ttk.Label(h, text="", width=7, anchor="e",
                                       style="Value.TLabel")
        self._keep_px_read.grid(row=0, column=5, sticky="e")
        _help = (
                 "How much detail the straightening keeps.\n"
                 "\n"
                 "1 = full detail. Nothing is thrown away; every part\n"
                 "stays at least 1:1 with the original. Largest file.\n"
                 "0 = smallest file. The near end of a slanted facade is\n"
                 "shrunk, losing about 17% of its fine detail.\n"
                 "\n"
                 "Why it exists: squaring a facade that runs away from\n"
                 "the camera stretches its far end five to seven times.\n"
                 "Fitting that back down squeezes the NEAR end -- the\n"
                 "half that was closest and holds the real detail.\n"
                 "\n"
                 "Size is the price, not the trade: turning it up costs\n"
                 "a bigger file and buys back detail. It never crops.\n"
                 "\n"
                 "Does nothing until a yaw is active: horizontal auto\n"
                 "(yaw), a horizontal you drew, or the yaw slider by hand.")
        _attach_tooltip(self._keep_px_scale, _help)
        _attach_tooltip(self._keep_px_read, _help)
        self._sync_yaw_controls()
        self._update_keep_px_read()
        # The variable outlives the session; push the shown value through so
        # what the slider says is what the save uses on the second photograph.
        if self.session is not None:
            self._apply_keep_px(redraw=False)

    def _strip_angles(self, row):
        """Topic: the two actions that act on the angles.  Named so they do not
        repeat the mode switch's "Auto"."""
        for text, cmd, tip in (
                ("Auto-fit", self._use_auto,
                 "Apply the auto-computed correction (roll + pitch)"),
                ("Reset", self._reset, "Reset all angles to zero")):
            b = ttk.Button(row, text=text, style="Compact.TButton", command=cmd)
            b.pack(side="left", fill="x", expand=True)
            _attach_tooltip(b, tip)

    def _strip_actions(self, row):
        """Topic: leaving this photograph.  Never behind anything: a queue
        advances on Save."""
        def btn(text, cmd, tip, weight=2, style="Compact.TButton"):
            b = ttk.Button(row, text=text, style=style, command=cmd)
            self._put(row, b, weight)
            _attach_tooltip(b, tip)
        btn("Skip", self._skip, "Go to the next photograph without writing anything")
        btn("Close", self._close, "Close this image without saving", 1)
        btn("Save As…", self._save_as,
            "Choose a file name and location for the corrected image")
        _cb = ttk.Checkbutton(row, text="overwrite", style="Compact.Toolbutton",
                              variable=self.v_overwrite,
                              command=self._refresh_file_names)
        self._put(row, _cb, 2)
        _attach_tooltip(_cb, "Replace the source file instead of writing a new one")
        btn("Save", self._save, "Save the corrected image and advance to the next",
            3, "Accent.Compact.TButton")

    def _slider_h(self, parent, col, label, var, lo, hi, inc, fmt):
        """One compact slider: label, track, number -- three grid columns, so
        several sit side by side in one row instead of stacking."""
        c = col * 3
        gap = layout.CTRL_COL_GAP_PX if col else 0
        ttk.Label(parent, text=label).grid(row=0, column=c, sticky="w", padx=(gap, 0))
        sc = ttk.Scale(parent, from_=lo, to=hi, variable=var, orient="horizontal",
                       length=layout.SLIDER_MIN, command=lambda _v: self._on_slider())
        sc.grid(row=0, column=c + 1, sticky="ew", padx=3)
        parent.columnconfigure(c + 1, weight=1, minsize=layout.SLIDER_MIN)
        sp = ttk.Spinbox(parent, textvariable=var, from_=lo, to=hi, increment=inc,
                         format=fmt, width=6, style="Compact.TSpinbox",
                         command=self._on_slider)
        sp.grid(row=0, column=c + 2, sticky="e")
        sp.bind("<Return>", lambda _e: self._on_slider())

    def _toggle_outline(self):
        on = self.v_after_outline.get()
        self._notify("outline on -- the photograph's border after the warp"
                     if on else "outline off")
        self._schedule_redraw()

    def _draw_after_outline(self, arr, iw, aox, aoy):
        """The source frame's border, mapped through the correction onto the
        after picture: shows how far the warp moved each edge and where the
        fill begins. Sampled along each edge rather than at the corners only,
        so a mapping that is not a single homography still draws true."""
        self.c_after.delete("outline")
        if not self.v_after_outline.get() or self.session is None or arr is None:
            return
        k = iw / float(arr.shape[1])          # preview array -> photo on screen
        w, h, n = float(self.session.w), float(self.session.h), 24
        edge = ([(w * t / n, 0.0) for t in range(n)] + [(w, h * t / n) for t in range(n)]
                + [(w * (1 - t / n), h) for t in range(n)] + [(0.0, h * (1 - t / n)) for t in range(n)])
        pts = []
        for x, y in edge:
            q = self.session.source_to_preview(x, y)
            if q is None:
                return
            pts.append((aox + q[0] * k, aoy + q[1] * k))
        # Only on the picture, never out into the field (user, 2026-10-21:
        # "dont expand for outline"): each piece is clipped to the picture's
        # rectangle, so a border the warp threw off-frame is simply not drawn
        # -- clipping the closed polygon instead would trace the picture's own
        # edge and pass it off as the border.
        box = (aox, aoy, aox + iw, aoy + arr.shape[0] * k)
        for a, b in zip(pts, pts[1:] + pts[:1]):
            seg = _clip_segment(a, b, box)
            if seg is not None:
                self.c_after.create_line(*seg, fill=OVERLAY["outline"], width=2,
                                         tags="outline")

    def _show_mode(self):
        """Raise the active mode's controls.  Both share one cell, so
        nothing is packed or forgotten and the strip never changes height.

        The mode switch IS the horizontal-auto switch: choosing "horizontal"
        turns the yaw on, choosing "auto" turns it off. The flag stops the two faces of the
        same switch from re-triggering each other."""
        self._mode_top = self.v_corr_mode.get()
        self._mode_frames[self._mode_top].tkraise()
        # Both faces are AUTOMATIC modes. After a slider was moved the session
        # is manual, and the switch used to flip only the yaw flag: the
        # slider's yaw stayed in force, so "Auto" still looked like horizontal
        # auto, and "Horizontal auto" never engaged (user, 2026-10-24: "auto
        # aktiver Schalter, dennoch haengt Programm im HA Modus"). Pressing it
        # now goes back to the estimator's angles, and says so.
        s = self.session
        if s is not None and s.mode == MANUAL:
            s.use_auto_angles()
            self._notify(f"{'horizontal auto' if self._mode_top == 'horizontal' else 'auto'}"
                         " -- back to the automatic angles; the slider changes are gone")
            if self._mode_top == "auto" and not self.v_correct_horizontal.get():
                self._sync_from_session()
        if self._mode_top == "horizontal" and not self.v_correct_horizontal.get():
            self._syncing_mode = True
            self.v_correct_horizontal.set(True)
            self._on_horizontal_toggle()
            self._syncing_mode = False
        elif self._mode_top == "auto" and self.v_correct_horizontal.get():
            self._syncing_mode = True
            self.v_correct_horizontal.set(False)
            self._on_horizontal_toggle()
            self._syncing_mode = False

    def _apply_detector(self):
        """Switch detector and say what it found.

        Three of the choices are optional dependencies that may not be
        installed, so the failure has to be visible *and* the widget has to
        stop claiming a detector that is not in force -- the session rolls the
        setting back, and the combobox follows it rather than the click."""
        err = self.session.set_detector(self.v_detector.get())
        if err:
            self.v_detector.set(self.session.settings.detector)
            self.lbl_mask.configure(text=err)
            # On the picture as well: the combobox snapping back with the
            # reason only in a small label read as a stuck control.
            self._notify(f"{err} -- still on {self.session.settings.detector}")
        else:
            msg = (f"{self.session.detector}: {len(self.session.vert)} vertical "
                   f"candidate(s), {len(self.session.horiz)} horizontal")
            self.lbl_mask.configure(text=msg)
            self._notify(f"detector {msg}")
        if self.session.mode == AUTO:
            self._sync_from_session()
        else:
            self._redraw()

    def _finish_strip(self):
        """Put the facade strip away and say so, if one was up."""
        s = self.session
        had = s is not None and s.strip is not None
        if self._tool_active("v_strip"):
            self.v_strip.set(False)
        if s is not None and s.strip is not None:
            s.clear_strip()
        self.c_before.delete("strip_ruler")
        if had:
            self._set_status("facade strip cleared -- it only shapes the yaw")
        return had

    def _apply_strip(self, _event=None):
        """Restrict horizontal evidence to an x-strip (corner views).  Off or an
        empty/invalid strip leaves ``strip`` at None -- the unfiltered frame.
        Re-estimates only in AUTO; a manual take-over keeps its angles until the
        user returns to Auto, so the strip cannot yank a correction they set.

        A valid strip also turns horizontal (yaw) correction on: the strip only
        shapes the yaw, and the yaw is gated on ``correct_horizontal`` -- left off,
        the rulers would show and change nothing.  Clearing the strip does not force
        the flag back off; a hand-set yaw or an independent choice stays put."""
        s = self.session
        if s is None:
            return
        strip = None
        if self.v_strip.get():
            try:
                x0 = float(self.v_strip_x0.get()) / 100.0
                x1 = float(self.v_strip_x1.get()) / 100.0
            except ValueError:
                return
            if 0.0 <= x0 < x1 <= 1.0 and (x1 - x0) >= 0.02:
                strip = (x0, x1)          # fractions, the one unit for a strip
        s.strip = strip
        if strip is not None and not s.settings.correct_horizontal:
            self.v_correct_horizontal.set(True)
            # Sets the flag, enables the yaw slider, and refits in AUTO -- with
            # strip already in place above, so the strip is what gets applied.
            self._on_horizontal_toggle()
            return
        if s.mode == AUTO:
            s.refit()
            self._sync_from_session()
        else:
            self._redraw()
        # The strip changes which facade the yaw is taken from, so the automatic
        # crop must follow it even when the session is not in AUTO mode.
        self._autocrop_follow()

    # -- ComfyUI, which lives on the App -----------------------------------
    # A fixed list, and it must stay one.  `_sync_comfy` copies from the App's
    # `_settings()`, which is a *whole* Settings -- strength, confidence, crop,
    # detector, mask.  Widening this to everything would silently overwrite the
    # per-photograph decisions this window owns with the batch panel's.
    COMFY_KEYS = ("comfy_url", "comfy_workflow", "comfy_unet", "comfy_clip",
                  "comfy_vae")

    def _app(self):
        """The batch window, when there is one.  ``None`` under a test root."""
        app = self.master
        return app if hasattr(app, "_open_comfy") else None

    def _on_add_files(self):
        app = self._app()
        if app is not None:
            app._add_files()

    def _on_add_folder(self):
        app = self._app()
        if app is not None:
            app._add_folder()

    def _on_paste(self):
        app = self._app()
        if app is None:
            return
        try:
            app._paste_screenshot()
        except Exception as exc:
            messagebox.showerror("Paste", str(exc))

    def _cfg(self):
        """The settings in force, whether or not a session is loaded yet.

        The cross is built at construction time with no session, so any read of
        ``self.session.settings`` during that build would raise; this falls back
        to the panel's own copy, which `_show_hint` sets to a default."""
        return self.session.settings if self.session is not None else self.settings

    def _register_comfy(self):
        """Listen for the App's verdict rather than polling for it.

        The App already computes the four-state light off the UI thread and
        posts it through its queue; this window only has to be told. Registered
        rather than read, so a state that changes while the window is open
        reaches it -- an indicator that is only correct at open time is worse
        than none.
        """
        app = self._app()
        # The cross is built at construction time, before the batch window has
        # finished wiring its ComfyUI state; `load` rebuilds and registers then.
        if app is None or not hasattr(app, "_comfy_listeners"):
            return
        # Idempotent: `load` rebuilds the widgets, and a second registration
        # would deliver every state change twice.
        if self._on_comfy_state not in app._comfy_listeners:
            app._comfy_listeners.append(self._on_comfy_state)
        self._on_comfy_state(*app._comfy_state)

    def _unregister_comfy(self):
        app = self._app()
        if app is not None and self._on_comfy_state in app._comfy_listeners:
            app._comfy_listeners.remove(self._on_comfy_state)

    def _on_comfy_state(self, state, text):
        if self.v_fill.get() != "comfyui":
            self.lbl_comfy.configure(text="")
            return
        label, ink = App.COMFY_LIGHTS.get(state, App.COMFY_LIGHTS["down"])
        self.lbl_comfy.configure(text=f"ComfyUI: {label} -- {text}",
                                 foreground=INK[ink])

    def _open_comfy(self):
        app = self._app()
        if app is None:
            messagebox.showinfo("ComfyUI", "open the batch window to reach the "
                                           "ComfyUI settings", parent=self)
            return
        app._open_comfy()
        app._test_comfy()

    def _sync_comfy(self):
        """Take the App's current ComfyUI settings, not the ones this window
        opened with.

        ``self.session.settings`` is a snapshot from `_review_single`. Without
        this, choosing a workflow in the settings window while a review is open
        would change the light and not the file that gets written -- the
        settings would appear to have been ignored.
        """
        app = self._app()
        if app is None:
            return
        live = app._settings()
        self.session.settings = self.session.settings.replace(
            **{k: getattr(live, k) for k in self.COMFY_KEYS})

    def _apply_fill(self):
        """The session owns the settings the save reads; the window only shows
        them.  Writing ``self.settings`` here made the whole control a no-op."""
        if self.session is None:
            return
        self.session.settings = self.session.settings.replace(
            fill=self.v_fill.get(),
            live_fill_preview=bool(getattr(self, "v_livefill", None)
                                   and self.v_livefill.get()))
        if self.v_fill.get() == "comfyui":
            # Choosing it is asking for it -- the same rule the batch panel
            # follows. A mode that silently needs six settings nobody was shown
            # is the quiet failure this project keeps arguing against.
            self._open_comfy()
        else:
            self.lbl_comfy.configure(text="")
        self._fill_fallback_notice()
        self._schedule_redraw()

    def _update_keep_px_read(self):
        """Name the end you are near, because "0.50" on its own explains nothing."""
        lbl = getattr(self, "_keep_px_read", None)
        if lbl is None:
            return
        v = float(self.v_keep_px.get())
        lbl.configure(text="max px" if v >= 0.995 else
                           "min px" if v <= 0.005 else f"{v:.2f}")

    def _apply_keep_px(self, redraw=True):
        """The session owns the settings the save reads, exactly as `_apply_fill`.

        Reached from the variable's trace, so it also runs with no photograph
        loaded and has to survive that.  `_build` calls it with *redraw* off:
        the canvases have no size yet there, and a redraw that early only
        spends the starved-canvas retry budget.
        """
        self._update_keep_px_read()
        s = self.session
        if s is None:
            return
        s.settings = s.settings.replace(
            keep_pixels=float(self.v_keep_px.get()))
        if redraw:
            self._schedule_redraw()

    # -- state -----------------------------------------------------------
    def _sync_from_session(self):
        if not self.winfo_exists():
            return
        roll, pitch, f, _ = self.session.current_angles()
        yaw = self.session.current_yaw()
        from .model import focal_35mm_from_px
        self.v_roll.set(round(math.degrees(roll), 2))
        self.v_pitch.set(round(math.degrees(pitch), 2))
        self.v_yaw.set(round(math.degrees(yaw), 2))
        if f:
            self.v_focal.set(round(focal_35mm_from_px(f, self.session.w, self.session.h), 0))
        self._redraw()
        self._autocrop_follow()
        self._sync_yaw_controls()

    def _yaw_is_live(self):
        """Is a yaw actually in force, by any of the routes that produce one?

        The detail dial's own tooltip names three -- horizontal auto, a drawn
        horizontal, and the slider by hand -- and the greying followed only the
        first. Measured on Platte_1: one hand-drawn horizontal with the
        checkbox off yaws the picture by 2.88 degrees, and both controls sat
        greyed out while it did. A control that is dead while the thing it
        controls is running is worse than no control, because it says the
        opposite of what is happening.
        """
        sess = self.session
        if sess is None:
            return False
        if getattr(self, "v_correct_horizontal", None) is not None \
                and self.v_correct_horizontal.get():
            return True
        if sess.mode != AUTO:      # manual: the slider IS the correction
            return True
        return sess.marker_yaw() is not None

    def _sync_yaw_controls(self):
        """Enable or grey the yaw slider, its box and the detail dial together.

        One place, because they answer one question. They were set in three,
        each from `correct_horizontal` directly.
        """
        state = "normal" if self._yaw_is_live() else "disabled"
        for w in ("_yaw_scale", "_yaw_spin", "_keep_px_scale"):
            widget = getattr(self, w, None)
            if widget is not None:
                try:
                    widget.configure(state=state)
                except tk.TclError:
                    pass

    def _on_horizontal_toggle(self):
        """Auto mode: yaw from the horizontal vanishing point.

        No longer switches anything else off. It used to turn the h-marker
        checkbox off, which was one half of a hand-written mutual exclusion
        between two mode flags -- a second mechanism beside `_exclusive`, and
        pairwise code that never grew to include the manual slider. There is
        only one flag here now, and a drawn horizontal simply outranks it: see
        `ReviewSession.current_yaw`.
        """
        on = self.v_correct_horizontal.get()
        s = self.session
        if on and s is not None and s.drop_planar_for("horizontal auto"):
            # The other direction of the same rule: a placed quad ignores
            # roll, pitch and yaw entirely, so switching the yaw back on with
            # one in force would have changed nothing visible at all.
            if getattr(self, "v_rect", None) is not None:
                self.v_rect.set(False)
            self._set_status("PC Rectangle cleared -- horizontal auto is back in charge")
        if s is None:
            return
        s.settings = s.settings.replace(correct_horizontal=on)
        self._sync_yaw_controls()
        if on and self.session.mode == AUTO:
            self.session.refit()
            self._sync_from_session()
        elif not on and self.session.mode == AUTO:
            self.v_yaw.set(0.0)
        if not on:
            # Switching horizontal auto off leaves nothing behind (user,
            # 2026-09-21). The strip exists only to choose which facade the
            # YAW is taken from; with no yaw it filters horizontal evidence
            # for a correction that is not running, and its two rulers still
            # sit on the picture claiming to do something.
            self._finish_strip()
            self._schedule_redraw()
        # The checkbox is the second face of the mode switch, so it moves the
        # mode too. `_syncing_mode` is set when the mode switch drove this
        # call, so the two faces do not chase each other around.
        if not getattr(self, "_syncing_mode", False):
            self.v_corr_mode.set("horizontal" if on else "auto")
            self._mode_frames[self.v_corr_mode.get()].tkraise()
        # The correction changed (a yaw came in or went away), so the automatic
        # crop must follow it even when the session is not in AUTO mode.
        self._autocrop_follow()

    def _on_slider(self):
        if self.session is None:
            return
        self.session.set_manual(roll_deg=self.v_roll.get(), pitch_deg=self.v_pitch.get(),
                                focal_35mm=self.v_focal.get(), yaw_deg=self.v_yaw.get())
        self._schedule_redraw()

    def _use_auto(self):
        """Back to the angles the estimator found, keeping the rest of the work.

        The neighbouring Reset is the big hammer -- it also re-enables every
        struck line, drops the vertical marks and the hand-drawn crop.  Wanting
        the found angles back after a mis-dragged slider is the common case and
        should not cost all of that.
        """
        if self.session is None:
            return
        self.session.use_auto_angles()
        self._sync_from_session()

    def _reset(self):
        if self.session is None:
            return
        self.session.reset_to_auto()
        self._sync_from_session()

    # Every drawing tool, and the handler that owns entering and leaving it.
    # `_on_click_before` tests them in a fixed order, so two tools on at once is
    # not "both available" -- it is the earlier one winning every click while the
    # later one looks broken.  That is exactly how the mask brush went dead: SAM
    # is tested first, so leaving SAM on made the brush unreachable.
    _TOOL_MODES = (("v_mark", "_on_mark_toggle"),
                   ("v_rect", "_on_rect_toggle"),
                   ("v_stroke", "_on_stroke_toggle"),
                   ("v_sam", "_on_sam_toggle"))

    def _put_down_tools(self):
        """Put every drawing tool down.  For a new photograph (user, 2026-09-22).

        A tool is a statement about the picture in front of you -- "I am placing
        corners on this facade" -- and the next photograph is a different
        facade.  The palette is built once and its variables outlive the
        session, so the rectangle tool stayed armed across a load: measured,
        `v_rect` True before and after, which means the button still looked
        pressed, the loupe was still up, and the first click on the new picture
        placed a corner nobody asked for.

        Through `_exclusive`, so each tool leaves by its own handler -- a half
        drawn polygon, a pointer grab and a dangling first point all get
        cleaned up rather than stranded behind a cleared flag.

        The facade strip is put down here too, though it is not in
        `_TOOL_MODES`: that registry is about which tool wins a CLICK, and the
        strip takes no clicks.  It is still a statement about one facade in one
        photograph, and a fresh session starts with `strip = None` regardless,
        so leaving its button lit would show an armed ruler over a session that
        has no strip.
        """
        self._exclusive(keep=None)
        v = getattr(self, "v_strip", None)
        if v is not None and v.get():
            v.set(False)

    def _exclusive(self, keep):
        """Put down every tool except ``keep``.

        Each one is switched off through its own handler rather than by setting
        the variable, because leaving a mode is not free: the brush has a half
        painted stroke and a pointer grab, marking has a dangling first point,
        and marking has a dangling first point.  Clearing the flag silently
        would strand all of it.

        Re-entrant by way of `_switching`: those handlers may themselves ask for
        exclusivity, and without the guard the first pair would bounce forever.
        """
        if getattr(self, "_switching", False):
            return
        self._switching = True
        try:
            for var, handler in self._TOOL_MODES:
                if var == keep:
                    continue
                v = getattr(self, var, None)
                if v is not None and v.get():
                    v.set(False)
                    getattr(self, handler)()
        finally:
            self._switching = False

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

    @staticmethod
    def _kind_for(x0, y0, x1, y1):
        """Which plane a drawn segment asserts, read off the segment itself.

        One Mark tool, not two (2026-09-15, user): the orientation *is* the
        drawing.  A segment that falls more than it runs is a vertical, else
        a horizontal -- and the rubberband is tinted by this while it is
        being dragged, so the answer is visible before release rather than
        after.  Exactly 45 deg counts as vertical: an arbitrary tiebreak, but
        a stated one, and a facade edge somebody is tracing is never within a
        degree of the diagonal.
        """
        return "v" if abs(y1 - y0) >= abs(x1 - x0) else "h"

    def _wait_show(self, text):
        """One line over the picture saying what is running, and roughly how long.

        No animated bar, and that is deliberate rather than lazy: the work it
        reports runs on THIS thread, so nothing of ours would move while it
        ran. A bar frozen mid-sweep reads as a hang; a sentence that says
        "filling the band -- about 9 s" and then sits there reads as work.
        Threading the save to animate a bar would buy motion and cost a whole
        class of half-written-file bugs.
        """
        if getattr(self, "_wait", None) is None:
            self._wait = tk.Frame(self, bg=INK["panel"],
                                  highlightbackground=INK["line"],
                                  highlightthickness=1)
            self._wait_lbl = tk.Label(self._wait, text="", bg=INK["panel"],
                                      fg=INK["text"], padx=18, pady=10)
            self._wait_lbl.pack()
        self._wait_lbl.configure(text=text)
        # Level with the PICTURES, not the middle of the panel. The panel is
        # pictures on top and controls underneath, so its centre is the seam
        # between them and a box placed there sits half over each -- the same
        # mistake the loupe made before `_loupe_park` was written.
        try:
            y = (self.c_before.winfo_rooty() - self.winfo_rooty()
                 + self.c_before.winfo_height() // 2)
            self._wait.place(relx=0.5, y=y, anchor="center")
        except tk.TclError:
            self._wait.place(relx=0.5, rely=0.35, anchor="center")
        self._wait.lift()
        try:
            self.update_idletasks()      # paint it BEFORE we stop answering
        except tk.TclError:
            pass

    # The loader bar: a hairline at the right edge of the AFTER pane, filling
    # upward.  Four px wide, inset from the frame, drawn straight onto the
    # canvas rather than as a widget -- Canvas has no alpha and no gradient, so
    # two flat rectangles in the palette's own `line` and `text` is the whole
    # thing, and it recolours with the theme because both are read at draw time.
    _BAR_W = 4
    _BAR_PAD = 12

    def _bar_show(self, done, label=""):
        """Fill the loader to ``done`` (0..1) and repaint it now.

        Positions come from `inpaint.fill`'s step callback, which fires when a
        piece of work has FINISHED.  Nothing interpolates between them: the bar
        sits still while the model loads and jumps when it is loaded, because
        that is the truth about where the time went.  `update_idletasks` is
        what makes it visible at all -- the fill runs on this thread, so the
        only chance to paint is inside the callback.
        """
        c = getattr(self, "c_after", None)
        if c is None:
            return
        try:
            w, h = c.winfo_width(), c.winfo_height()
            if min(w, h) < 40:
                return
            x1 = w - self._BAR_PAD
            x0 = x1 - self._BAR_W
            y0, y1 = self._BAR_PAD, h - self._BAR_PAD
            c.delete("loadbar")
            c.create_rectangle(x0, y0, x1, y1, outline="", fill=INK["line"],
                               tags="loadbar")
            frac = min(max(float(done), 0.0), 1.0)
            top = y1 - (y1 - y0) * frac
            if y1 - top >= 1:
                c.create_rectangle(x0, top, x1, y1, outline="",
                                   fill=INK["text"], tags="loadbar")
            c.tag_raise("loadbar")
            self.update_idletasks()
        except tk.TclError:
            pass

    def _bar_hide(self):
        c = getattr(self, "c_after", None)
        if c is None:
            return
        try:
            c.delete("loadbar")
        except tk.TclError:
            pass

    def _fill_step(self, label, done):
        """`inpaint.fill`'s step callback: move the bar, say the step."""
        self._bar_show(done, label)
        self._wait_show(f"{label}  --  {int(round(done * 100)):d}%")

    def _wait_stage(self, name, mpx=0.0):
        """Callback for a long job: name the stage and estimate what is left."""
        from . import progress as PR
        pretty = {"warping": "straightening", "writing": "writing the file"}
        label = pretty.get(name, name.replace("fill:", "filling the band, "))
        eta = PR.humanise(PR.estimate(name, mpx,
                                      share=0.5 if name.startswith("fill:") else 1.0))
        self._wait_show(f"{label}{'  --  ' + eta if eta else ''}")

    def _wait_hide(self):
        self._bar_hide()
        w = getattr(self, "_wait", None)
        if w is not None:
            try:
                w.place_forget()
            except tk.TclError:
                pass

    def _set_busy(self, busy: bool):
        """Hourglass over both previews while a model is working.

        The watch lives on the canvases, not the window, because the canvases
        are what the pointer is over; setting it on the panel would be hidden
        behind them.  Both get it so the indicator survives whichever side the
        mouse happens to be on when inference finishes.
        """
        for c in (getattr(self, "c_before", None), getattr(self, "c_after", None)):
            if c is not None:
                c.config(cursor="watch" if busy else "")

    def _on_before_motion(self, event):
        if getattr(self, "_busy_sam", False):
            return  # keep the hourglass; motion must not reset it mid-inference
        if getattr(self, "_loupe", None) is not None:
            self._loupe_move(event)
        self._rect_rubber(event)
        if self._tool_active("v_strip") \
                and getattr(self, "_ph_b", None) is not None:
            oy = self._before_off[1]
            ih = self._ph_b.height()
            near = any(abs(event.y - (oy + ty)) <= 5 for ty in (0.0, ih))
            self.c_before.config(cursor="sb_v_double_arrow" if near else "")
        else:
            cur = self.c_before.cget("cursor")
            if cur != "":
                self.c_before.config(cursor="")
        self._brush_cursor_indicator(event)

    def _on_strip_icon_click(self, event=None):
        """Toggle ROI on/off; apply the strip from the rulers."""
        self._apply_strip()

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

    def _show_after(self, frac_rect):
        """Draw the whole after pane: the corrected frame with the outside-crop veil
        baked in (``darken_outside_crop``), its grid and rulers, and the kept
        rectangle's outline.

        ``frac_rect`` is the crop as fractions of the frame, or ``None`` for the whole
        frame.  Re-baking from the cached un-veiled array (``_after_base``) is cheap --
        no re-warp, no re-inpaint -- so it can run on every drag tick and still feel
        live, which a stipple overlay could not because Tk has no per-item alpha.
        """
        base = getattr(self, "_after_base", None)
        if base is None:
            return
        box = self._after_box
        arr = (darken_outside_crop(base, frac_rect)
               if frac_rect is not None else base)
        # Fit the FULL canvas, exactly as the before pane does. This used to
        # inset by RULER_MARGIN on all four sides to guarantee a border strip
        # for the guides, which cost the corrected image 11.5 % of its area
        # against the original beside it (measured 648x486 vs 625x446 at
        # 1920x1200) -- the two panes then showed the same photograph at two
        # different scales, which is the one thing a before/after comparison
        # must not do. **The ruler zone is the black cross, outside the
        # images** (user, 2026-09-14), so no pixels need to be taken from
        # inside the frame to host it.
        ph, _ = _to_photo(arr, box)
        # Centred on both axes (user, 2026-10-21: "always on middle axis") --
        # in the LIVE canvas, like the before pane. It used the locked fit box,
        # so after fullscreen or a resize the picture kept its old offset and
        # sat 64 px off the axis while the original beside it was centred.
        live = getattr(self, "_after_live", None) or box
        aox = (live[0] - ph.width()) // 2
        aoy = (live[1] - ph.height()) // 2
        self._after_off = (aox, aoy)
        self.c_after.delete("all")
        self._paint_ground(self.c_after)
        self.c_after.create_image(aox, aoy, anchor="nw", image=ph)
        self._ph_a = ph
        self._draw_after_guides()
        self._draw_after_lines(arr, ph.width(), ph.height())
        self._draw_after_outline(arr, ph.width(), aox, aoy)
        # The crop outline used to be suppressed while the planar quad was up,
        # because the two drew over each other.  With planar gone there is
        # nothing to yield to, and the flag it tested went with the tool -- this
        # read `planar_on` after the parameter was removed, which the compiler
        # cannot see and only a loaded photograph would hit.
        iw, ih = ph.width(), ph.height()
        x0, y0, x1, y1 = (frac_rect or (0.0, 0.0, 1.0, 1.0))
        self._draw_crop_outline(aox + x0 * iw, aoy + y0 * ih,
                                aox + x1 * iw, aoy + y1 * ih)
        # Q2: output dimensions + crop size, bottom-left.
        # `arr.shape` is the PREVIEW array (render_after scales to the canvas
        # widget's on-screen size, see `_redraw`) -- it is not what `save()`
        # will write. Ledger item 15 (2026-09-26, user-caught): this label
        # claimed to show "output dimensions" while actually showing the
        # preview's pixel size, which can be many times smaller than the real
        # save. `session.output_size()` mirrors `save()`'s own plan exactly
        # (same angles, same strip) so the two can never disagree again.
        ow, oh = self.session.output_size()
        cw = max(1, int(round((x1 - x0) * ow)))
        ch = max(1, int(round((y1 - y0) * oh)))
        # Q2: output dimensions + crop size, top-left, inside the picture (the
        # picture sits flush to the top of the field, so the label lives over its
        # corner with a small backing so it reads on any photo).
        label_x = aox + layout.LABEL_INSET_PX
        label_y = aoy + layout.LABEL_INSET_PX
        label_text = f"{self._after_title}   {ow}×{oh}  {cw}×{ch}"
        tid = self.c_after.create_text(label_x, label_y, anchor="nw",
                                       text=label_text, fill=OVERLAY["label"],
                                       font=ui_font(9))
        bb = self.c_after.bbox(tid)
        if bb:
            pad = 2
            self.c_after.create_rectangle(bb[0] - pad, bb[1] - pad,
                                          bb[2] + pad, bb[3] + pad,
                                          fill=INK["bg"], outline="")
            self.c_after.tag_raise(tid)
        # The redraw above wiped the canvas, so the faint status word has to be
        # laid down again here to survive it.
        self._draw_status_overlay()

    def _tool_active(self, var_name):
        """True if the named tool toggle variable exists and is active."""
        v = getattr(self, var_name, None)
        return v is not None and v.get()

    def _on_click_before(self, event):
        # Empty cross: the before slot doubles as the pick target.  Returning
        # "break" stops this handler chain -- with no session there is nothing
        # else to click, and `_before_off` was never laid out.
        if self.session is None:
            app = self._app()
            if app is not None:
                app._add_files()
            return "break"
        x = event.x - self._before_off[0]
        y = event.y - self._before_off[1]
        # Remember the state BEFORE this press: if this turns out to be the
        # first press of a fast pair, `_on_before_double_click` restores it so
        # neither click of the double leaves an edit behind.  A second press
        # inside `_DBL_WINDOW_MS` keeps the first press's snapshot untouched.
        now = getattr(event, "time", 0) or int(time.monotonic() * 1000)
        if not (self._pair_snap is not None
                and 0 <= now - self._pair_snap_t <= _DBL_WINDOW_MS):
            self._pair_snap = self._snap_before_clicks()
            self._pair_snap_t = now
        # Priority dispatch: ruler grab (most specific), then each active tool
        # in order, falling through to line toggle (no tool active) as default.
        if self._tool_active("v_strip"):
            hit = self._strip_ruler_at(event.x, event.y)
            if hit is not None:
                self._on_strip_drag_start(hit)
                return
        for var, handler, args in (
            ("v_sam", self._on_sam_press, (event,)),
            ("v_stroke", self._stroke_start, (x, y)),
            ("v_mark", self._click_mark, (x, y, event)),
            ("v_rect", self._click_rect, (x, y, event)),
        ):
            if self._tool_active(var):
                handler(*args)
                return
        idx = self.session.pick_line(x, y, display_scale=self._before_scale)
        if idx is None:
            return
        self.session.toggle_line(idx)
        if self.session.mode == AUTO:
            self._sync_from_session()
        else:
            self._redraw()

    def _on_before_double_click(self, event):
        """A fast left double-click on the Before pane opens the load dialog.

        The two presses that make up the double have already run through
        `_on_click_before`, so a control mark, a pending mark point, quad
        corners, paint or a line toggle may be on the table.  None of it is an
        edit the user asked for, so the state from before the FIRST press is
        restored, the half-open gesture state is cleared, and only then the
        same dialog as the "add files" button opens.  Modifier doubles keep
        their own meaning (Shift sizes the pen, Alt erases), and with no
        session the first press already opened the dialog, so the double adds
        nothing.
        """
        if self.session is None:
            return "break"
        if getattr(event, "state", 0) & (SHIFT_MASK | ALT_MASK):
            return "break"
        now = getattr(event, "time", 0) or int(time.monotonic() * 1000)
        if (self._pair_snap is not None
                and 0 <= now - self._pair_snap_t <= _DBL_WINDOW_MS):
            self._restore_before_clicks(self._pair_snap)
        self._pair_snap = None
        self._on_add_files()
        return "break"

    def _snap_before_clicks(self):
        """The click-editable state, before the next press of a potential pair.

        The session side is the session's: `snapshot()` owns the control lines,
        the candidate `enabled` flags, the paint mask, the planar quad, the
        facade strip and the crop (both cleared by a quad start), and the
        settings.  What the panel adds here is Tk state only -- the switches
        `_planar_takes_over` may turn and the active mode frame -- and a
        restore that misses one of those leaves the window disagreeing with
        the session it just put back."""
        v_corr = getattr(self, "v_correct_horizontal", None)
        v_strip = getattr(self, "v_strip", None)
        v_mode = getattr(self, "v_corr_mode", None)
        return {
            "session": self.session.snapshot(),
            "corr_h": v_corr.get() if v_corr is not None else None,
            "strip_on": v_strip.get() if v_strip is not None else None,
            "mode": v_mode.get() if v_mode is not None else None,
        }

    def _restore_before_clicks(self, snap):
        """Put back the state `_snap_before_clicks` took, and close every
        gesture the two presses may have left half-open (a pending mark point,
        an in-flight stroke, a grabbed corner or ruler)."""
        s = self.session
        s.restore(snap["session"])
        if snap["corr_h"] is not None and getattr(self, "v_correct_horizontal", None) is not None:
            self.v_correct_horizontal.set(snap["corr_h"])
        if snap["strip_on"] is not None and getattr(self, "v_strip", None) is not None:
            self.v_strip.set(snap["strip_on"])
        if snap["mode"] is not None and getattr(self, "v_corr_mode", None) is not None:
            self.v_corr_mode.set(snap["mode"])
            # A quad start may have raised the "auto" frame over the active
            # one; the restored mode owns the top again.
            self._mode_frames[snap["mode"]].tkraise()
        # Half-open gestures the two presses may have left behind:
        self._pending_mark = None
        self._mark_moved = False
        self._mark_drag = None
        self._rect_drag = None
        self._strip_drag = None
        self._stroke_pts = []
        if hasattr(self, "_sam_drag_start"):
            del self._sam_drag_start
        for tag in ("mark_rubber", "stroke_preview"):
            self.c_before.delete(tag)
        if s.mode == AUTO:
            self._sync_from_session()
        else:
            self._redraw()

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

    # -- line brush ------------------------------------------------------
    def _build_tools(self, parent):
        """The lower-left tools field: line editing and masking. Built once by App
        into a persistent frame (App._build runs a single time; `_add` only
        refreshes the file list), so it survives every load.

        Line brush -- drag a broad stroke over the before pane; every candidate
        line the stroke crosses is erased (deactivated) at once, like a pencil.
        The width doubles as the hit radius, so what you paint is what erases.
        Masking -- moved here from the lower-right cell: source + BiRefNet model
        picker.  The line detector also lives here now, moved out of the
        lower-right adjustments panel: what-the-estimator-sees belongs beside the
        input image, not with the angles that edit the result."""
        # FIND controls, beside the input image: which detector feeds the
        # estimator.
        # One grid, one TOPIC per row, every cell filled edge to edge (user,
        # 2026-10-20: compact, no half-empty areas, sorted by topic -- not
        # arbitrarily):
        #   row 0  LINES      detector | clear marks (3 cells)
        #   row 1  MASK       active | invert all | opacity | clear mask
        g = ttk.Frame(parent)
        g.pack(fill="x")
        for _c, _w in enumerate((0, 2, 2, 2, 2)):
            g.columnconfigure(_c, weight=_w)

        def put(w, row, col):
            w.grid(row=row, column=col, sticky="nsew")
            return w

        for _r, _t in enumerate(("lines", "mask")):
            put(ttk.Label(g, text=_t, anchor="w", width=9), _r, 0)

        # -- row 0: lines --------------------------------------------------
        self.v_detector = tk.StringVar(value=self.settings.detector
                                       if self.settings.detector in DETECTORS else "lsd")
        dbox = put(ttk.Combobox(g, textvariable=self.v_detector, width=6,
                                style="Compact.TCombobox", state="readonly",
                                values=list(DETECTORS)), 0, 1)
        dbox.bind("<<ComboboxSelected>>", lambda e: self._apply_detector())
        # No brush-width field: the pen is sized on the picture, Shift+left
        # or Alt+right dragged sideways (user, 2026-10-20).
        _b = ttk.Button(g, text="Clear marks", style="Compact.TButton",
                        command=self._clear_marks)
        _b.grid(row=0, column=2, columnspan=3, sticky="nsew")
        _attach_tooltip(_b, "Remove all manually placed control lines,\n"
                            "vertical and horizontal.")

        # -- where the mask comes from: no row any more ---------------------
        # The source combobox, mask folder, BiRefNet model picker and file
        # polarity were for testing only (user, 2026-10-24: "mask from file
        # Zeile soll weg"). The brush, SAM and the Grounding DINO key are the
        # mask sources; their models come from `deps`. The variables stay,
        # hidden, because the gdino key and a reload drive them.
        self.v_maskmode = tk.StringVar(value=self.settings.mask_mode)
        self.v_maskinv = tk.BooleanVar(value=self.settings.mask_invert)

        # -- row 1: what is done with the mask -------------------------------
        if getattr(self, "v_mask_active", None) is None:
            self.v_mask_active = tk.BooleanVar(value=True)
        self.msk_active_cb = put(ttk.Checkbutton(
            g, text="mask active", style="Compact.Toolbutton",
            variable=self.v_mask_active, command=self._on_mask_active_toggle), 1, 1)
        _attach_tooltip(self.msk_active_cb,
                        "Toggle mask on/off without clearing the painted region")
        self.v_invert = tk.BooleanVar(value=False)
        _cb = put(ttk.Checkbutton(g, text="invert ALL", style="Compact.Toolbutton",
                                  variable=self.v_invert,
                                  command=self._on_invert_toggle), 1, 2)
        _attach_tooltip(_cb, "Swap it: what is red becomes the part that "
                             "counts, and the rest is ignored.\n"
                             "Applies to every source at once - brush, SAM "
                             "and the facade strip together.")
        self.v_alpha = tk.DoubleVar(value=0.50)
        op = put(ttk.Frame(g), 1, 3)
        ttk.Label(op, text="opacity").pack(side="left", padx=(4, 4))
        ttk.Scale(op, from_=0.0, to=1.0, variable=self.v_alpha, orient="horizontal",
                  command=lambda _v: self._on_alpha()).pack(side="left", fill="x", expand=True)
        _b = put(ttk.Button(g, text="Clear Mask", style="Compact.TButton",
                            command=self._clear_mask), 1, 4)
        _attach_tooltip(_b, "Remove the painted mask and the SAM selection,\n"
                            "and give back the lines they struck out.\n"
                            "The mask source above is left as it is.")
        # Mask status text: its own thin row under the grid, and only as tall as
        # the words in it.
        self.lbl_mask = ttk.Label(parent, text="", wraplength=760, justify="left",
                                  style="Dim.TLabel")
        self.lbl_mask.pack(anchor="w")
        self.v_gdino_prompt = tk.StringVar(value=self.settings.gdino_prompt or "building")


    def _on_before_b1motion(self, event):
        if getattr(self, "_loupe", None) is not None:
            self._loupe_move(event)
        if self._tool_active("v_sam"):
            self._on_sam_drag(event)
            return
        if getattr(self, "_rect_drag", None) is not None:
            self._rect_drag_move(event)
            return
        if getattr(self, "_strip_drag", None) is not None:
            self._on_strip_drag_move(event)
            return
        if getattr(self, "_mark_drag", None) is not None:
            self._on_mark_drag(event)
            return
        if getattr(self, "_pending_mark", None) is not None:
            self._mark_rubber(event)
            return
        if self._tool_active("v_stroke"):
            self._on_stroke_drag(event)

    def _on_before_b1release(self, event):
        # A Shift+drag pen sizing whose Shift came up before the button ends
        # here, not in `_on_shift_release`; a kept anchor then swallowed the
        # next right-drag erase (OpenCode review, 2026-10-24).
        if getattr(self, "_pen_anchor", None) is not None:
            return self._on_pen_size_end(event)
        if self._tool_active("v_sam"):
            self._on_sam_release(event)
            return
        if getattr(self, "_rect_drag", None) is not None:
            self._rect_drag_release()
            return
        if getattr(self, "_strip_drag", None) is not None:
            self._on_strip_drag_release()
            return
        if getattr(self, "_pending_mark", None) is not None and getattr(self, "_mark_moved", False):
            self._mark_commit(event)
            self._loupe_hide()          # a released drag always clears it
            return
        if getattr(self, "_mark_drag", None) is not None:
            self._mark_drag = None
            self._drag_preview_end()
            self._loupe_hide()
            self.session.refit()
            self._sync_from_session()
            return
        if self._tool_active("v_stroke"):
            self._on_stroke_release(event)

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

    # -- brush: right erases, Alt+right sizes the pen ----------------------
    def _on_right_press(self, event):
        """Right-click: SAM prompt reset when SAM is on, erase stroke otherwise."""
        if self._tool_active("v_sam"):
            self._on_sam_right_click(event)
            return "break"
        # Rectangle: right-click clears the quad. A LEFT click no longer does,
        # because wiping four placed corners because somebody missed a handle is
        # a destructive answer to a miss; clearing is now a gesture of its own.
        if self._tool_active("v_rect"):
            if self.session is not None and self.session.planar_quad:
                self.session.clear_planar()
                self.c_before.delete("rect_rubber")
                self._set_status("PC Rectangle cleared -- click the first corner")
                self._redraw()
            return "break"
        return self._on_erase_press(event)

    # -- drawing ---------------------------------------------------------
    def _drag_preview(self):
        """Re-render the after picture at most every ``DRAG_PREVIEW_MS``.

        The before field follows the pointer on the canvas alone; the after
        picture is re-rendered at a throttled rate without fill (`_redraw`
        sees the drag), and in full when the drag ends.
        """
        if self._drag_job is not None:
            return
        if not getattr(self, "_drag_said", False):
            self._drag_said = True
            self._set_status_extra("dragging -- the corrected picture updates "
                                   "without fill; the full preview follows on release")

        def tick():
            if not self.winfo_exists():
                return
            self._drag_job = None
            if self.session is None:
                return
            if getattr(self, "_mark_drag", None) is not None:
                self.session.refit()
            self._redraw()
        self._drag_job = self._schedule_after(layout.DRAG_PREVIEW_MS, tick)

    def _drag_preview_end(self):
        self._drag_said = False
        if self._drag_job is not None:
            try:
                self.after_cancel(self._drag_job)
            except tk.TclError:
                pass
            self._drag_job = None

    # -- deferred fill: the stand-in now, the slow backend once things settle --
    def _fill_schedule(self):
        """Run the session's `deferred_fill` after a quiet ``DEFERRED_FILL_MS``.

        Every render cancels the timer and bumps `_fill_gen`, so the slow
        fill runs once, for the picture that stayed on screen.
        """
        if self._fill_after is not None:
            try:
                self.after_cancel(self._fill_after)
            except tk.TclError:
                pass
        self._fill_after = self._schedule_after(layout.DEFERRED_FILL_MS, self._fill_start)

    def _fill_start(self):
        if not self.winfo_exists():
            return
        self._fill_after = None
        s = self.session
        job = getattr(s, "deferred_fill", None) if s is not None else None
        if job is None:
            return
        if self._fill_thread is not None and self._fill_thread.is_alive():
            # One model, one job: wait for the running one, then do the latest.
            self._fill_after = self._schedule_after(150, self._fill_start)
            return
        s.deferred_fill = None
        raw, hole, settings, finish = job
        gen, q = self._fill_gen, self._fill_q

        def work():
            # Off the Tk thread: numpy, the model and the queue only.
            try:
                from . import inpaint as FILL
                img, _note = FILL.fill(raw, hole, settings)
                q.put((gen, finish(img), None))
            except Exception as exc:              # reported on the Tk thread
                q.put((gen, None, exc))
        self._fill_thread = threading.Thread(target=work, daemon=True)
        self._fill_thread.start()
        self._set_status_extra(f"preview: quick fill now -- {settings.fill} follows")
        self._schedule_after(60, self._fill_poll)

    def _fill_poll(self):
        if not self.winfo_exists():
            return
        try:
            gen, img, err = self._fill_q.get_nowait()
        except queue.Empty:
            if self._fill_thread is not None and self._fill_thread.is_alive():
                self._schedule_after(60, self._fill_poll)
            return
        if err is not None:
            logging.getLogger("pc.gui").warning("deferred fill failed: %s", err)
            self._set_status_extra(f"the slow fill failed ({err}); "
                                   "the quick fill stays on screen")
            return
        self._live_cpu_check()
        if gen != self._fill_gen or self.session is None or img is None:
            return                                # the picture moved on meanwhile
        self._after_base = img
        self._show_after(self.session.crop_rect)
        self._set_status(self.session.status_text())

    def _live_cpu_check(self):
        """lama on the CPU takes seconds per preview (7 s at 1000 px measured on
        32 cores, 2026-10-23): switch the live preview off once and say so
        (user: "1" -- off where there is no GPU). Decided after the first lama
        fill, because asking for the device means importing torch, which would
        cost every start seconds. Saves keep using lama."""
        global _CPU_LIVE_TOLD
        if _CPU_LIVE_TOLD:
            return
        from . import inpaint as FILL
        if FILL.lama_device() != "cpu":
            return
        _CPU_LIVE_TOLD = True
        v = getattr(self, "v_livefill", None)
        if v is None or not v.get():
            return
        v.set(False)
        self._apply_fill()
        self._notify("lama runs on the CPU here (several seconds per preview) -- "
                     "live preview switched off, saves still use lama. "
                     "Tick 'live' to see lama in the preview again.")

    def _fill_wait(self, timeout=60.0):
        """Before a save: no second model call while the preview's is running."""
        if self._fill_after is not None:
            try:
                self.after_cancel(self._fill_after)
            except tk.TclError:
                pass
            self._fill_after = None
        t = self._fill_thread
        if t is not None and t.is_alive():
            t.join(timeout)

    def _schedule_redraw(self):
        if self._busy:
            return
        self._busy = True
        self._schedule_after(60, self._redraw)

    def _paint_ground(self, canvas):
        """Lay the theme's gradient under everything else on *canvas*.

        Only Phosphor carries a `grad`; for every other palette the canvas keeps
        its flat `field` and this does nothing.  It has to run after each
        `delete("all")` rather than once at build, because that call takes the
        ground with it -- the same reason the tool icons are re-rendered on a
        theme switch instead of re-coloured.
        """
        grad = INK.get("grad")
        if not grad:
            return
        w, h = canvas.winfo_width(), canvas.winfo_height()
        if min(w, h) < 2:
            return
        ph = _gradient_photo(w, h, grad[0], grad[1],
                             grad[2] if len(grad) > 2 else None)
        canvas.create_image(0, 0, anchor="nw", image=ph, tags="ground")
        canvas.tag_lower("ground")
        # A PhotoImage nothing holds is collected and the item goes blank: the
        # reference must outlive the canvas item, not the function.
        if getattr(self, "_grounds", None) is None:
            self._grounds = {}
        self._grounds[str(canvas)] = ph

    def _draw_empty(self):
        """The startup cross has no session: show a drop prompt, draw nothing
        else.  Runs instead of the real render so an empty canvas never reaches
        `self.session.*` and spams the error log on every <Configure>."""
        if not hasattr(self, "c_before"):
            return
        for c in (self.c_before, self.c_after):
            if not c.winfo_ismapped():
                return
            c.delete("all")
            self._paint_ground(c)
        b = (self.c_before.winfo_width(), self.c_before.winfo_height())
        if min(b) >= 20:
            self.c_before.create_text(
                b[0] // 2, b[1] // 2, anchor="center",
                text="Drop a photograph here\nor click the + to pick one",
                fill=INK["dim"], font=ui_font(13))
        self._set_status("")

    def _redraw(self):
        if not self.winfo_exists():
            return
        self._busy = False
        if self.session is None:
            self._draw_empty()
            return
        try:
            # The full canvas, both panes, no inset. These were each shrunk by
            # 2 * RULER_MARGIN to reserve a border strip for the guides, which
            # cost every preview 40 px in each direction and capped the picture
            # well short of the frame it had. **The ruler zone is the black
            # cross, outside the images** (user, 2026-09-14), so nothing needs
            # to be taken from inside them. Identical expressions for the two
            # panes on purpose: the same photograph must not appear at two
            # scales side by side, and the surest way to keep that true is that
            # there is only one rule.
            box_b = (max(1, self.c_before.winfo_width()),
                     max(1, self.c_before.winfo_height()))
            box_a = (max(1, self.c_after.winfo_width()),
                     max(1, self.c_after.winfo_height()))
            # An unmapped canvas is starved as well, and its winfo_* values are
            # stale -- the last size it had, not zero -- so the <20 test alone
            # misses it and would "draw" into a widget with no place on screen.
            starved = (not self.c_before.winfo_ismapped()
                       or not self.c_after.winfo_ismapped()
                       or min(box_b) < 20 or min(box_a) < 20)
            if starved:
                # Waiting for the first layout, not polling: on a window too
                # short to give the canvas 20 px this used to reschedule
                # itself forever at 8 Hz.  A handful of tries, then leave the
                # canvas blank and stop -- the next <Configure> brings the
                # picture back, because a successful redraw resets the count.
                if self._redraw_tries < 6:
                    self._redraw_tries += 1
                    self._schedule_after(120, self._redraw)
                return
            self._redraw_tries = 0
            # Fit-once-then-lock (see _img_box in __init__): render the picture
            # at the canvas size it FIRST filled, not the live size. Switching
            # the correction mode changes the strip's height and resizes the
            # canvas; re-rendering at the new size re-scales the picture and it
            # jumps. Lock the render box so the picture keeps one size for the
            # whole photo; it is centred in the live canvas (box_b/box_a stay
            # the live size for the offset below -- clipped if it shrank,
            # padded if it grew).
            if self._img_box is None:
                self._img_box = (box_b, box_a)
            lock_b, lock_a = self._img_box
            # show_lines=False on purpose: the detected lines are drawn on the
            # canvas (`_draw_before_lines`), 1 px at the displayed size like
            # the after pane's check lines.  Baked into the image they scaled
            # with it and read thicker.  The mask wash stays in the image.
            before = self.session.render_before(max_edge=max(lock_b), show_lines=False)
            # Un-cropped on purpose: `_show_after` bakes a veil over what the crop
            # discards, so the picture keeps one size and one scale for the whole
            # session instead of leaping every time a corner moves.
            self._fill_gen += 1
            dragging = (getattr(self, "_rect_drag", None) is not None
                        or getattr(self, "_mark_drag", None) is not None)
            # While dragging: no fill and half the pixels -- the picture is
            # being steered, not judged, and `_to_photo` scales it back up.
            after = self.session.render_after(
                max_edge=max(lock_a) // 2 if dragging else max(lock_a),
                apply_crop=False, fill="none" if dragging else "defer")
            # A live fill reports its steps through `_fill_step`, which raises
            # the "filled -- 100%" notice; the render is synchronous, so once it
            # returns the fill is done and the notice must go (it stayed up for
            # good once live lama became the default).
            if not getattr(self, "_busy", False):
                self._wait_hide()
            self._after_base = after      # un-veiled, preview-sized; _show_after bakes it
            self._after_box = lock_a
            self._after_live = box_a      # centred in this, fitted to lock_a
            import logging as _log
            _log.getLogger("pc.gui").info(
                "render_after done: shape=%s mean=%.1f",
                getattr(after, "shape", "?"),
                float(after.mean()) if after is not None else -1)
            ph_b, s_b = _to_photo(before, lock_b)
            # scale from the *original* image to what is on screen
            self._before_scale = s_b * (before.shape[1] / self.session.w)
            # Centred on both axes, like the after pane (user, 2026-10-21).
            self._before_off = ((box_b[0] - ph_b.width()) // 2,
                                (box_b[1] - ph_b.height()) // 2)
            self.c_before.delete("all")
            self._paint_ground(self.c_before)
            self._sam_selection = None
            self.c_before.create_image(self._before_off[0], self._before_off[1],
                                       anchor="nw", image=ph_b)
            self._ph_b = ph_b
            self._draw_before_lines()
            # No grid on this pane (2026-09-13, user-directed).  The grid is a
            # ruler for judging the *corrected* frame -- a true vertical should
            # run along a grid line -- so it belongs on the after pane only.  On
            # the original it measures nothing and only competes with the lines
            # the detector drew, which is what this pane is for.
            self._draw_marks()
            self._draw_strip_rulers()
            self._draw_sam_prompts()
            # P3: horizontal VP markers on the before pane
            if (self.session.model is not None
                    and self.v_correct_horizontal.get()):
                self._draw_horiz_vp_markers(ph_b.width(), ph_b.height())
            # Q1: original image dimensions, top-left, inside the picture.  The
            # picture now sits flush to the top of the field, so the label lives
            # over its corner with a small backing so it reads on any photo.
            label_x = self._before_off[0] + layout.LABEL_INSET_PX
            label_y = self._before_off[1] + layout.LABEL_INSET_PX
            # The tool palette is centred on the left edge; start the title
            # right of it only where the two would share a row.
            _bar = getattr(self, "_addbar", None)
            try:
                if (_bar is not None and _bar.winfo_ismapped()
                        and _bar.winfo_y() <= label_y + layout.LABEL_INSET_PX * 3
                        and label_y <= _bar.winfo_y() + _bar.winfo_height()):
                    label_x = max(label_x, _bar.winfo_x() + _bar.winfo_width()
                                  + layout.LABEL_INSET_PX)
            except tk.TclError:
                pass
            label_text = f"{self._before_title}   {self.session.w}×{self.session.h}"
            tid = self.c_before.create_text(label_x, label_y, anchor="nw",
                                            text=label_text, fill=OVERLAY["label"],
                                            font=ui_font(9))
            bb = self.c_before.bbox(tid)
            if bb:
                pad = 2
                self.c_before.create_rectangle(bb[0] - pad, bb[1] - pad,
                                               bb[2] + pad, bb[3] + pad,
                                               fill=INK["bg"], outline="")
                self.c_before.tag_raise(tid)
            # Rectangle quad LAST: drawn over everything (loupe, marks, guides)
            self._draw_rect_quad()
            self._show_after(self.session.crop_rect)
            if not dragging:
                # The drag says what is happening; the size note costs a
                # full-size plan per throttled frame (2026-10-23).
                self._set_status(self.session.status_text())
            if getattr(self.session, "deferred_fill", None) is not None:
                self._fill_schedule()
        except Exception:
            tb = traceback.format_exc()
            try:
                log = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "..", "..", "pc_errors.log")
                with open(log, "a", encoding="utf-8") as fh:
                    fh.write(tb + "\n" + "=" * 60 + "\n")
            except OSError:
                pass
            self._set_status("preview failed (full log: pc_errors.log):\n" + tb)

    def _build_tool_palette(self):
        """Fill the picture-corner palette with the drawing tools.

        Toggle *buttons*, not checkboxes (`indicatoron=False`): a tool is either
        the one in your hand or it is not, and a pressed-in button says that at a
        glance where a tick box does not.  Stacked under the load icons in the
        same column, so everything you can do to the original is in one place on
        the original, rather than in a row across the far side of the window.
        """
        bar = getattr(self, "_addbar", None)
        if bar is None or not bar.winfo_exists():
            return
        for w in getattr(self, "_palette_btns", []):
            try:
                w.destroy()
            except tk.TclError:
                pass
        self._palette_btns = []
        self._palette_imgs = []

        # A Checkbutton sizes `width`/`height` in TEXT units, so a glyph button
        # comes out oblong however you count -- and a tool key has to be square.
        # With an image attached they are PIXELS instead, which is what this
        # 1x1 blank buys: a real 36x36 key with the glyph centred on it.  Kept
        # on self because Tk drops an image nobody references and the button
        # then silently loses its size.
        if getattr(self, "_palette_blank", None) is None:
            self._palette_blank = tk.PhotoImage(width=1, height=1)
        side, gap = layout.tool_key()

        # Every key in this column is built here, and that is the point. Until
        # 2026-09-20 three of them were assembled by hand beside this function
        # and drifted in three ways at once, none of which is about taste:
        #   * `highlightthickness=2` made those keys 4 px larger than the rest,
        #     in a vertical column where the misalignment is the first thing
        #     the eye finds;
        #   * they lit up in INK["accent"] when held while the others lit up in
        #     INK["line"], so "which tool am I holding" -- the single most
        #     important state here -- had two different answers;
        #   * their glyphs were drawn with pixel arithmetic inline
        #     (`gs // 10`, `gs // 4`), which the hard rules forbid for exactly
        #     this outcome: a size tuned in the window is a size no test sees.
        # The accent won, because that is what an accent is for and two keys
        # already used it.
        def tool(glyph, var, command, tip, invert=False, lead=False,
                 emoji=False):
            # Inverted is for the mask brush ALONE (2026-09-15, user): it is the
            # one tool whose glyph stands for the thing it paints, so a dark
            # circle on a light key reads as the brush tip itself.  Inverting
            # the whole palette instead made every tool shout equally, which is
            # no emphasis at all.
            # One look for every key (user, 2026-10-24: "alles soll gleich
            # aussehen", "lieber weiss wenn aktiv, sonst mittelgrau"): the
            # glyph is grey at rest and turns white while its tool is held; the
            # key itself never changes colour. The brush used to be inverted
            # at rest, so it looked held when it was not. `invert` is kept in
            # the signature for callers but no longer changes the look.
            fg, fg_on = INK["dim"], INK["text"]
            # A name draws; a hex codepoint quotes the icon font. The font is
            # still right for anything it says well -- the dinosaur is a pun
            # the user put there and it stays -- but the six tool marks are
            # drawn, because the stock set has no vocabulary for them and
            # borrowing the nearest picture is what put an eyedropper on a
            # building selector.
            def render(colour):
                if _DRAWN_GLYPHS and glyph in _DRAWN_GLYPHS:
                    return _glyph_pil(glyph, layout.tool_glyph(), colour)
                return _icon_pil(glyph, layout.tool_glyph(), colour,
                                 emoji=emoji, mono=emoji)
            img, img_on = render(fg), render(fg_on)
            if img is not None and img_on is not None:
                self._palette_imgs.append(ImageTk.PhotoImage(img))
                self._palette_imgs.append(ImageTk.PhotoImage(img_on))
                pics = dict(image=self._palette_imgs[-2],
                            selectimage=self._palette_imgs[-1], text="")
            else:
                pics = dict(image=self._palette_blank, text=chr(0x25CF))
            b = tk.Checkbutton(bar, variable=var, command=command,
                               compound="center", **pics,
                               indicatoron=False, width=side, height=side,
                               padx=0, pady=0, highlightthickness=0,
                               bd=0, relief="flat",
                               font=ui_font(20), cursor="hand2",
                               background=INK["field"],
                               foreground=fg,
                               activebackground=INK["line"],
                               activeforeground=INK["text"],
                               selectcolor=INK["field"])
            # A double gap above the first tool: the load group and the tool
            # group are different kinds of thing, and one skipped pitch says so
            # without a separator line.
            b.pack(side="top", pady=(gap * 2 if lead else gap, 0))
            _attach_tooltip(b, tip)
            self._palette_btns.append(b)
            return b

        tool("mark", self.v_mark, self._on_mark_toggle,
             "Mark (M): click two points on a straight edge.\n"
             "It comes out exactly vertical or horizontal --\n"
             "the way it leans decides which. Click a line to\n"
             "remove it. 2 lines of a direction override the\n"
             "detector. Hold Alt for a slow, fine loupe.",
             lead=True)
        tool("rect", self.v_rect, self._on_rect_toggle,
             "PC Rectangle: click the 4 corners of one wall,\n"
             "in any order -- it comes out front-on in its real\n"
             "proportions, with 20 % kept around it to crop by hand.\n"
             "Drag a corner to adjust; right-click clears.")
        # Brush and box-select joined Mark here (2026-09-15, user: "either the
        # toolbar or the marker, I would prefer only the tool").  They were
        # text controls in the lower-left field while Mark was in both places,
        # so a tool was either duplicated or somewhere else than its siblings.
        # Kept under the old attribute name because a test presses the real
        # widget -- the brush once broke by having the box and the click
        # handler read two different variables, which only pressing catches.
        # A round brush gets a round button.  The first attempt used a
        # shaded SQUARE glyph, which is exactly the wrong promise for a
        # tool whose whole point is that it paints circles.
        # A solid disc, not a ring and not a brush: the tool paints a filled
        # circle, so the key shows one, dark inside the way a masking tool
        # reads in an image editor.  Measured: 24x24, mirror 100% on both
        # axes, centre fully covered.
        # Picked by measurement, not by name -- "CircleRingBadge" sounds wrong
        # and renders as a perfectly symmetric hollow circle, while the one
        # actually called StatusCircleOuter is lopsided with a filled centre.
        self._brush_chk = tool("brush", self.v_stroke, self._on_stroke_toggle,
                               "Brush (B): paint over regions to exclude from\n"
                               "line detection.\n"
                               "Left drag paints, right drag or Alt+left erases.\n"
                               "Shift+left or Alt+right dragged sideways sizes\n"
                               "the pen.",
                               invert=True)
        # The Grounding DINO key (the dinosaur) is gone (user, 2026-10-24: "Dino
        # ist zu gross, nervt ... Tool weg"): its model is 690 MB, and SAM's box
        # does the same job with 184 MB. `--mask gdino` stays in the CLI.
        self._sam_btn = tool("sam", self.v_sam, self._on_sam_toggle,
                             "SAM (S): drag a box around the building;\n"
                             "everything outside is masked out.\n"
                             "Right-click clears the prompt.")
        # Strike slanted (vertical candidates > 18 deg off plumb) was removed
        # from the palette 2026-10-23 (user: "geht ja sowieso"): the fit already
        # copes with them. `ReviewSession.disable_lines_by_angle` stays.
        # ROI / Facade strip: dark-light-dark vertical stripes.  Click opens a
        # popup with the two % spinboxes; the rulers on the before pane are the
        # primary interaction.
        if getattr(self, "v_strip", None) is None:
            self.v_strip = tk.BooleanVar(value=False)
            self.v_strip_x0 = tk.DoubleVar(value=20.0)
            self.v_strip_x1 = tk.DoubleVar(value=80.0)
        # It was three solid bars, light-dark-light, which read as a barcode
        # and said nothing about measuring. Two rulers with the span between
        # them is the actual idea, and it is now in the same pen as the rest.
        tool("strip", self.v_strip, self._on_strip_icon_click,
             "Facade strip: restrict horizontal evidence\n"
             "to one facade on corner views.\n"
             "Drag the two rulers on the before picture.")
        # NOTE: this used to say the quad had left the palette and that
        # nothing in the window pointed at it -- sitting directly above
        # `tool("rect", ...)`. The quad came back and the comment stayed, which
        # is the exact shape of defect this project keeps paying for: a reader
        # trusts the prose over the line of code beside it.

    def _draw_before_lines(self):
        """The detected lines on the before pane, as Tk canvas lines.

        One thing with the after pane's check lines: both go through
        `lineoverlay`, width 1 at the displayed size, colours from the one
        table in `lineoverlay.LINE`.  Baked into the photo they scaled with
        it and read thicker than the after pane; on the canvas both panes
        are 1 px.  `render_before` no longer draws them (show_lines=False);
        it still bakes the mask wash, which belongs to the image.
        """
        if self.session is None or not self.v_show_lines.get():
            return
        ox, oy = self._before_off
        s = self._before_scale
        lo = self.session.line_overlay()
        # Inliers last, so the fit's evidence sits on top, like the bake did.
        for role, key in (("masked", "masked"), ("horiz", "h_line"),
                          ("v_outlier", "v_outlier"), ("v_inlier", "v_inlier")):
            segs = lo[role]
            if segs is not None and len(segs):
                LO.draw(self.c_before, segs, ox, oy, LO.LINE[key], scale=s,
                        tags="before_lines")
        hline = self.session.horizon_line()
        if hline is not None:
            pts = LO.infinite_line_points(hline, self.session.w, self.session.h)
            if len(pts) >= 2:
                (x0, y0), (x1, y1) = pts[0], pts[1]
                self.c_before.create_line(ox + x0 * s, oy + y0 * s,
                                          ox + x1 * s, oy + y1 * s,
                                          fill=LO.hex_(LO.LINE["horizon"]),
                                          width=1, tags="before_lines")

    def _draw_after_lines(self, arr, iw, ih):
        """Re-detect lines on the *corrected* frame, as a check on the correction.

        The grid says where vertical is; this says where the photograph actually
        ended up.  Run on the result rather than the original, so a residual lean
        shows as a line that still slopes -- the direct way to see that a
        horizontal correction came out too weak, instead of inferring it from the
        before pane.

        Diagnostic, so it is off by default and deliberately cheap-ish: it runs
        on the preview-sized array already in hand, not the full-resolution
        frame, and only when the switch is on.
        """
        if not getattr(self, "v_after_lines", None) or not self.v_after_lines.get():
            return
        if arr is None or self.session is None:
            return
        try:
            import cv2 as _cv2
            from . import lines as _L
            small = _cv2.resize(arr, (iw, ih), interpolation=_cv2.INTER_AREA)
            gray = _cv2.cvtColor(small, _cv2.COLOR_BGR2GRAY)
            min_len = max(8.0, self.session.settings.min_line_length_frac * min(iw, ih))
            seg, _name = _L.detect_segments(gray, min_len,
                                            self.session.settings.detector,
                                            small, self.session.settings)
        except Exception:
            return                     # a diagnostic must never break the pane
        if seg is None or not len(seg):
            return
        # Both passes draw through `lineoverlay.draw_check`: one colour rule,
        # 1 px at the displayed size, colours from the one table.
        LO.draw_check(self.c_after, seg, *self._after_off,
                      LO.LINE["check_ok"], LO.LINE["check_v_off"],
                      LO.LINE["check_h_off"], CHECK_LINE_TOL_DEG, tags="after_lines")
        # Second pass: M-LSD in a distinct colour (cyan) so both detectors
        # are visible for comparison.  Only when the primary is not already mlsd.
        if self.session.settings.detector != "mlsd":
            try:
                seg_m, _ = _L.detect_segments(gray, min_len, "mlsd",
                                              small, self.session.settings)
            except Exception:
                seg_m = None
            if seg_m is not None and len(seg_m):
                LO.draw_check(self.c_after, seg_m, *self._after_off,
                              LO.LINE["mlsd_ok"], LO.LINE["mlsd_v_off"],
                              LO.LINE["mlsd_h_off"], CHECK_LINE_TOL_DEG, tags="after_lines")

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

    def _set_status(self, text):
        # The status word no longer has a label: it is kept as a plain string
        # and drawn as a faint overlay inside the after picture field (see
        # _draw_status_overlay).
        self._status_text = text or ""
        self._draw_status_overlay()
        self._show_values()

    def _show_values(self):
        """Refresh the correction values in the window's foot (`App._values_lbl`).
        Rides on `_set_status`, which every change of the correction calls."""
        lbl = getattr(self.master, "_values_lbl", None)
        if lbl is None:
            return
        try:
            text = self.session.values_text() if self.session is not None else ""
        except Exception:                        # a half-built session: say nothing
            text = ""
        lbl.configure(text=text)

    def _set_status_extra(self, text):
        self._set_status(text)

    def _notify(self, text):
        """A state-change prompt (fullscreen on/off ...). Shown above the status
        text for layout.NOTICE_MS, so a redraw cannot wipe it the moment it lands."""
        self._notice = text or ""
        job = getattr(self, "_notice_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._notice_job = self._schedule_after(layout.NOTICE_MS, self._clear_notice)
        self._draw_status_overlay()

    def _clear_notice(self):
        if not self.winfo_exists():
            return
        self._notice = ""
        self._notice_job = None
        self._draw_status_overlay()

    def _draw_status_overlay(self):
        # The status word, drawn faintly inside the after field so the pictures
        # keep their full height (the old label sat above the strip and cost the
        # field a band).  It is re-drawn at the end of every after-pane redraw,
        # because that redraw wipes the canvas.  No backing: where the picture
        # covers the corner the word simply rides over it, dim enough to read as
        # a watermark.
        c = getattr(self, "c_after", None)
        if c is None:
            return
        c.delete("status_ov")
        notice = getattr(self, "_notice", "") or ""
        text = getattr(self, "_status_text", "") or ""
        w = c.winfo_width()
        h = c.winfo_height()
        if notice:
            # Top centre: the top-left corner is the after title's.
            c.create_text(
                w // 2, layout.LABEL_INSET_PX, anchor="n",
                text=notice, fill=INK["text"],
                font=ui_font(layout.CTRL_FONT_PT),
                width=max(60, w // 2),
                justify="center", tags="status_ov")
        if not text:
            return
        c.create_text(
            layout.LABEL_INSET_PX, h - layout.LABEL_INSET_PX, anchor="sw",
            text=text, fill=OVERLAY["status_faint"],
            font=ui_font(layout.CTRL_FONT_PT),
            width=max(60, w - 2 * layout.LABEL_INSET_PX),
            justify="left", tags="status_ov")

    # -- output ----------------------------------------------------------
    def _save(self):
        dst = self._target_path()
        # Asked once per photograph, because replacing an original is the one
        # action here nothing can undo -- and in a queue of thirty the checkbox
        # was ticked long before this particular picture came up.
        if (self.v_overwrite.get()
                and not messagebox.askyesno(
                    "Overwrite", f"Replace the original?\n\n{dst}", parent=self)):
            return False
        # The file that gets written must use the ComfyUI settings as they are
        # now, not as they were when this window opened.
        self._sync_comfy()
        try:
            # PC Rectangle (planar) takes priority over the rotation path:
            # if four corners are set, save the rectified facade.
            if len(self.session.planar_quad) >= 4:
                self._wait_show("rectifying the facade")
                self._fill_wait()
                self.session.save_planar(dst, on_stage=self._wait_stage)
            else:
                self._fill_wait()
                self.session.save(dst, on_stage=self._wait_stage)
        except Exception as exc:
            self._wait_hide()
            messagebox.showerror("Save", str(exc), parent=self)
            return False
        self._wait_hide()
        self._fill_fallback_notice(getattr(self.session, "last_fill_note", "") or "")
        self._clean_key = self.session.state_key()
        # A hand-placed strip is the one thing in this window that cannot be
        # recovered by looking at the result: it says where THIS building's
        # corner falls, and it was gone the moment the photograph closed.
        self._remember_strip()
        if self.on_saved:
            self.on_saved(self.session.path, dst)
        self._fire_closed()
        return True

    def is_dirty(self):
        """Has anything been changed since the photograph opened or was saved?"""
        s = self.session
        return s is not None and s.state_key() != getattr(self, "_clean_key", None)

    def _mark_clean(self):
        if not self.winfo_exists():
            return
        if self.session is not None:
            self._clean_key = self.session.state_key()

    def _remember_strip(self):
        """Store the facade strip beside the photograph, as fractions.

        Failure here must never cost the save: the picture is already written,
        and a sidecar that could not be created is worth a status line, not an
        exception thrown on top of a completed action.
        """
        s = self.session
        strip = getattr(s, "strip", None) if s is not None else None
        if not strip:
            return
        try:
            from . import correction_log as CLOG
            stem = os.path.splitext(os.path.basename(s.path))[0]
            CLOG.remember_strip("correction_log", stem, strip)
        except Exception as exc:        # never break a finished save
            self._set_status(f"strip not stored: {exc}")

    def _save_as(self):
        from tkinter import filedialog
        base = os.path.basename(self.session.path)
        default = os.path.splitext(base)[0] + "_corr.jpg"
        dst = filedialog.asksaveasfilename(
            parent=self, title="Save corrected image", defaultextension=".jpg",
            initialfile=default,
            filetypes=[("JPEG", "*.jpg *.jpeg"), ("PNG", "*.png"),
                       ("All files", "*.*")])
        if not dst:
            return
        self._sync_comfy()
        try:
            if len(self.session.planar_quad) >= 4:
                self._wait_show("rectifying the facade")
                self._fill_wait()
                self.session.save_planar(dst, on_stage=self._wait_stage)
            else:
                self._fill_wait()
                self.session.save(dst, on_stage=self._wait_stage)
        except Exception as exc:
            self._wait_hide()
            messagebox.showerror("Save As", str(exc), parent=self)
            return
        self._wait_hide()
        self._fill_fallback_notice(getattr(self.session, "last_fill_note", "") or "")
        self._remember_strip()
        if self.on_saved:
            self.on_saved(self.session.path, dst)
        self._fire_closed()

    def _skip(self):
        """Next photograph, nothing written (user, 2026-10-20: "Skip instead of
        Keep original").  Keep original copied the untouched file to the
        output -- a batch-era gesture; in manual review a copy nobody asked
        for is clutter, and leaving a photo alone means writing nothing."""
        self._set_status("skipped -- nothing written")
        self._fire_closed()


# ==========================================================================
# batch window
# ==========================================================================
class App(_ROOT_CLASS, _AfterOwner, _SetupMixin, _ComfyMixin):
    def __init__(self, initial=None, start_maximized=True, start_fullscreen=False,
                 in_taskbar=True):
        super().__init__()
        # Tracking set for all after() IDs — must be before first _schedule_after call
        self._after_jobs = set()
        if not in_taskbar:
            # Off-screen windows of the tests and tools: a tool window has no
            # taskbar button on Windows (user, 2026-10-21). Elsewhere a no-op.
            try:
                self.attributes("-toolwindow", True)
            except tk.TclError:
                pass
        self.title(f"Perspective Correction  v{__version__}")
        # The window opens maximized, so this is the size it *restores* to.
        # It used to be a hardcoded 1920x1080, which is too small on a 1440p or
        # 4K monitor and larger than the screen in both directions on a
        # 1366x768 laptop. `layout.initial_window` takes a share of the real
        # screen instead and clamps it both ways; it is pure arithmetic and
        # tested at five resolutions in tests/test_layout.py. F11 toggles
        # borderless fullscreen for the review work; `run()` opens in it.
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        gw, gh = layout.initial_window(sw, sh)
        self.geometry(f"{gw}x{gh}")
        # Floor follows the screen (layout.min_window): at most 1366x768, the
        # size the control strip was measured to fit, never larger than the
        # display. It was a fixed 1920x1080 that hung off smaller screens.
        self.minsize(*layout.min_window(sw, sh))
        apply_theme(self)
        self._last_applied_theme = "Minimal Black"
        # Deferred: the caption is only honoured once the window is mapped, and
        # that happens in mainloop -- a direct call here would be reset on show.
        self._schedule_after(0, lambda: _dark_titlebar(self))
        self._icon = _set_window_icon(self)
        self.queue = queue.Queue()
        self.worker = None
        self.stop_flag = threading.Event()
        self.results = {}
        # Loaded before the widgets, because some of them show a remembered
        # value as their initial state rather than being set afterwards.
        self._remembered = prefs.load()
        self._build()
        # The remembered "output" folder is deliberately NOT restored any more
        # (2026-10-21): with the output row gone nothing on screen would show
        # where Save writes, and the stored one pointed into an off-limits
        # sibling checkout.  Save writes <name>_corr next to the source; Save
        # As... is the way to put it elsewhere.
        # The mask setup is machine configuration like the model path: a mode
        # picked once and a path typed once must survive a restart, or both are
        # re-picked on every launch.  (The output folder stays offered, not
        # forced -- writing somewhere new is a decision; masking with a saved
        # mask is not.)
        if self._remembered.get("mask_mode") in ("off", "file", "birefnet", "gdino"):
            self.v_mask.set(self._remembered["mask_mode"])
        key = "birefnet_model" if self.v_mask.get() in ("birefnet", "gdino") else "mask_file"
        if self._remembered.get(key):
            self.v_maskpath.set(self._remembered[key])
        self._refresh_items()          # opens on the drop stage, not the work one
        # a remembered path is offered, never forced: the selector still says off
        if initial:
            self._add(list(initial))
        self._fullscreen = start_fullscreen
        self.bind("<F11>", lambda _e: self._toggle_fullscreen())
        # Ctrl+V pastes a picture (or copied files) without the paste icon.
        self.bind("<Control-v>", self._paste_key)
        self.bind("<Control-V>", self._paste_key)
        if _MAC:
            self.bind("<Command-v>", self._paste_key)
            self.bind("<Command-V>", self._paste_key)
        self.bind("<Escape>", self._esc)
        self.bind("<m>", lambda _e: self._tool_key("v_mark", "_on_mark_toggle"))
        self.bind("<b>", lambda _e: self._tool_key("v_stroke", "_on_stroke_toggle"))
        self.bind("<s>", lambda _e: self._tool_key("v_sam", "_on_sam_toggle"))
        self._pump_closing = False
        self._pump_job = self._schedule_after(120, self._pump)
        if start_fullscreen:
            self._schedule_after(50, self._enter_fullscreen)
        elif start_maximized:
            self._schedule_after(50, self._maximize)

    def destroy(self):
        """Stop the queue timer before destroying the application window."""
        self._pump_closing = True
        self._cancel_after_jobs()
        super().destroy()

    def _maximize(self):
        try:
            self.state("zoomed")
        except tk.TclError:          # platforms without a zoomed state
            pass
        _dark_titlebar(self)         # a state change can re-lay the caption

    def _set_status(self, text):
        """App-level status word: the review panel owns the label."""
        rev = getattr(self, "review", None)
        if rev is not None:
            rev._set_status(text)

    def _notify(self, text):
        rev = getattr(self, "review", None)
        if rev is not None:
            rev._notify(text)

    def _enter_fullscreen(self):
        """Open borderless: no title bar, no menu -- the review working state.
        F11 returns to the windowed view."""
        try:
            self.attributes("-fullscreen", True)
        except tk.TclError:
            pass
        self._fullscreen = True
        if hasattr(self, "v_fullscreen"):
            self.v_fullscreen.set(True)
        self._notify("fullscreen -- F11 or ESC exits")

    def _toggle_fullscreen(self):
        """F11: borderless fullscreen and back.  Maximized keeps the title bar
        and menu; this drops both, which is what a long review session wants."""
        self._fullscreen = not self._fullscreen
        try:
            self.attributes("-fullscreen", self._fullscreen)
        except tk.TclError:
            pass
        if hasattr(self, "v_fullscreen"):
            self.v_fullscreen.set(self._fullscreen)
        if self._fullscreen:
            self._notify("fullscreen -- F11 or ESC exits")
        else:
            self._maximize()
            # Windows re-creates the caption after the state change, so the
            # dark title bar does not stick on the first pass.
            self._schedule_after(100, lambda: _dark_titlebar(self))
            self._schedule_after(400, lambda: _dark_titlebar(self))
            self._notify("windowed -- F11 for fullscreen")

    def _esc(self, _e):
        """ESC exits fullscreen (F11 still toggles).  A no-op while windowed, so
        it never steals ESC from a dialog or entry."""
        if self._fullscreen:
            self._toggle_fullscreen()

    def _tool_key(self, var_name, handler_name):
        """Flip one tool mode from the keyboard.

        Every one of these was already a trip to a button.  All four handlers
        read their variable and none flips it, so the flip belongs here --
        the same contract the palette buttons rely on.

        Typing is not a shortcut: a key that lands while an entry, spinbox or
        combobox has focus belongs to that widget, so it is ignored here.
        """
        w = self.focus_get()
        if w is not None:
            try:
                if w.winfo_class() in ("Entry", "TEntry", "Spinbox", "TSpinbox",
                                       "TCombobox", "Text"):
                    return
            except Exception:
                pass
        r = getattr(self, "review", None)
        if r is None or getattr(r, "session", None) is None:
            return
        v = getattr(r, var_name, None)
        if v is None:
            return
        v.set(not v.get())
        getattr(r, handler_name)()

    def _switch_theme(self, theme_name):
        """Swap the active palette: update INK in place, re-apply ttk styles,
        then walk tk widgets whose explicit bg/fg still hold the old colours."""
        old = dict(INK)
        new = THEMES.get(theme_name)
        if new is None or theme_name == getattr(self, "_last_applied_theme", None):
            return
        self._last_applied_theme = theme_name
        INK.update(new)
        # INK is updated, never rebuilt, so a key only one palette defines would
        # outlive it: switch Phosphor -> Dark Grey and its flat panes would keep
        # the dark ramp. A palette without a gradient has to say so.
        if "grad" not in new:
            INK.pop("grad", None)
        apply_theme(self, new)
        _dark_titlebar(self)
        _retint_bg(self, old, new)
        # A walk cannot fix a PhotoImage: the icons carry their tint in their
        # PIXELS, rendered once at build with the palette that was current then.
        # Rebuilding the palette re-renders them, and it is also the only thing
        # that reaches those keys at all -- `_retint_bg` matches widgets by
        # class, and the tool keys are Checkbuttons, which it did not walk.
        rev = getattr(self, "review", None)
        if rev is not None and hasattr(rev, "_build_tool_palette"):
            try:
                rev._build_tool_palette()
                # Same argument for the ground: it is pixels, and the walk above
                # cannot re-tint pixels. Only a redraw lays a new one down.
                if hasattr(rev, "_schedule_redraw"):
                    rev._schedule_redraw()
            except tk.TclError:
                pass                       # window going away mid-switch
        # The status-bar wordmark is a rendered image too, tinted INK["text"]
        # into its pixels at build time; a theme switch re-renders it.
        sb = getattr(self, "_status_bar", None)
        llbl = getattr(sb, "_logo_lbl", None) if sb is not None else None
        if llbl is not None:
            logo = _logo_image(layout.FOOT_LOGO_PX, _hex_rgba(INK["text"]))
            if logo is not None:
                try:
                    photo = ImageTk.PhotoImage(logo)
                    llbl.configure(image=photo)
                    llbl.image = photo
                except tk.TclError:
                    pass
        wlbl = getattr(sb, "_wordmark_lbl", None) if sb is not None else None
        if wlbl is not None:
            mark = _wordmark_image(14, _hex_rgba(INK["text"]))
            if mark is not None:
                try:
                    photo = ImageTk.PhotoImage(mark)
                    wlbl.configure(image=photo)
                    wlbl.image = photo
                except tk.TclError:
                    pass

    def _build_status_bar(self):
        """The slim foot of the window: wordmark left, theme + update right.

        Packed side="bottom" before the cross so it claims its strip first and
        the cross (fill=both, expand) takes every pixel above it. A child of the
        App, not the review panel, so `load` never rebuilds it.
        """
        bar = ttk.Frame(self)
        bar.pack(side="bottom", fill="x", padx=8, pady=(0, 1))
        # Wordmark in the left corner: the v07 typography, tinted to the active
        # palette so it follows the theme like every other mark.
        # The pyramid-and-eye mark sits large in front of it, tinted the same.
        logo = _logo_image(layout.FOOT_LOGO_PX, _hex_rgba(INK["text"]))
        if logo is not None:
            photo = ImageTk.PhotoImage(logo)
            lbl = ttk.Label(bar, image=photo)
            lbl.image = photo
            lbl.pack(side="left", padx=(2, 6))
            bar._logo_lbl = lbl
        mark = _wordmark_image(14, _hex_rgba(INK["text"]))
        if mark is not None:
            photo = ImageTk.PhotoImage(mark)
            lbl = ttk.Label(bar, image=photo)
            lbl.image = photo
            lbl.pack(side="left", padx=(2, 8))
            bar._wordmark_lbl = lbl
        # The running status word lives here as a slim low-contrast stripe: it
        # is read, not operated on, so it sits at the foot of the window where
        # the eye rests, out of the controls field.
        self._status_lbl = ttk.Label(bar, text="", style="Dim.TLabel", anchor="w")
        self._status_lbl.pack(side="left", fill="x", expand=True, padx=(8, 8))
        # The correction in numbers, exactly centred on the window ("exakt
        # mittig"): placed by fraction rather than packed, so the wordmark on
        # the left and the buttons on the right cannot push it off centre.
        # Dim, like the status word: read, not operated on.
        self._values_lbl = ttk.Label(bar, text="", style="Dim.TLabel", anchor="center")
        self._values_lbl.place(relx=0.5, rely=0.5, anchor="center")
        # Theme + update in the right corner, moved here from the killed brand row.
        self.btn_update = ttk.Button(bar, text="update", width=8)
        self.btn_update.pack(side="right", padx=(0, 4))
        self.btn_update.configure(command=self._check_for_update)
        _attach_tooltip(self.btn_update,
                        "Look for a newer version on GitHub.\n"
                        "Shows what changed and asks before taking it.\n"
                        "Works with git or with a downloaded copy.\n"
                        "Refuses if you edited the program's files here --\n"
                        "nothing of yours is overwritten.\n"
                        "Restart the program afterwards.")
        self.btn_deps = ttk.Button(bar, text="deps", width=8, command=self._install_deps)
        self.btn_deps.pack(side="right", padx=(0, 4))
        _attach_tooltip(self.btn_deps,
                        "Install the optional packages that are missing:\n"
                        "  lama -- the good fill for the empty corners\n"
                        "      (needs torch: a large download, minutes)\n"
                        "  mlsd -- a second line detector\n"
                        "  tkinterdnd2 -- drag photos onto the window\n"
                        "  GeoCalib -- a better focal guess for photos\n"
                        "      without EXIF (offered only once torch is\n"
                        "      there; ~140 MB)\n"
                        "Lists what is missing and asks first. Without them\n"
                        "the program still works (telea fills instead).")
        # The whole batch, rudimentary: one button, which turns into its own
        # stop button and counter while it runs.
        self.btn_batch = ttk.Button(bar, text="batch", width=12, command=self._batch_click)
        self.btn_batch.pack(side="right", padx=(0, 8))
        _attach_tooltip(self.btn_batch,
                        "Correct every added photograph without looking:\n"
                        "Auto mode, written as <name>_corr next to each one.\n"
                        "Asks once before it starts; click again to stop.\n"
                        "Photographs it is unsure about are skipped and\n"
                        "open for review afterwards.")
        self._theme_var = tk.StringVar(value="Minimal Black")
        self._theme_var.trace_add(
            "write", lambda *_a: self._switch_theme(self._theme_var.get()))
        self._theme_combo = ttk.Combobox(bar, textvariable=self._theme_var,
                                         values=list(THEMES.keys()),
                                         state="readonly", width=14)
        self._theme_combo.pack(side="right", padx=(0, 8), pady=0)
        return bar

    def _build(self):
        # The window is the cross and a slim status bar at the foot -- nothing
        # else. The brand row is gone: the mark lives only in the OS title bar
        # (`_set_window_icon`), the wordmark in the status bar's left corner, and
        # the theme + update controls in its right corner. Packed side="bottom"
        # before the cross so it claims its strip first and the cross takes the
        # rest; a child of the App, not the review panel, so `load` never rebuilds
        # it.
        self._status_bar = self._build_status_bar()

        # Both are read by the review panel's <Configure>, which fires while
        # `ReviewPanel(self)` is still constructing, so they exist before it.
        self.v_output = tk.StringVar()
        self.items = []                       # files and/or folders, in order
        self._cur = -1                        # index into items the panel is on

        # The window is the brand header and the cross -- nothing else.  No
        # paned split, no options strip, no start bar below: the results list
        # lives in the cross's lower-left field and the batch controls in the
        # lower-right one, so a resize grows four equal fields and re-decides
        # nothing.
        self.review = ReviewPanel(self)
        # No margin of its own: the panel's CROSS_SIDE is the whole side edge.
        self.review.pack(fill="both", expand=True, padx=0, pady=(0, 4))

        # The loader is the cross's lower-left field, not a strip across the
        # top of the window.  Four fields of the same size, previews above and
        # the two controls below, is a layout that can be checked at a glance;
        # a loader banner over a split pane is one that has to be learned.
        # It is a child of the review panel but not of anything the panel
        # rebuilds -- `load` destroys the other three fields, never this one.
        # The field is one of four equal boxes; the *content* stays compact
        # and top-aligned rather than stretching to fill it (the controls'
        # field, by contrast, may use all its space).
        # Tools field: the lower-left quarter hosts the review tools -- line
        # editing now, masking next. Built once here (App._build runs a single
        # time; `_add` only refreshes the file list), so it survives every load.
        # Packed first so it sits above the file list.
        tools = ttk.Frame(self.review.loader, padding=(6, 6, 6, 2))
        tools._bpc_persistent = True
        tools.pack(fill="x")
        self.review._build_tools(tools)
        # The detector combobox lives in the review panel's tools field; keep
        # the App-level StringVar in sync so _settings() reads the right value.
        self.review.v_detector.trace_add("write",
                                         lambda *_a: self.v_detector.set(
                                             self.review.v_detector.get()))

        # The add triggers live in the before-image corner (grey +/folder icons);
        # the file listbox is gone -- the queue lives in self.items and what will
        # be processed shows in the results tree once a run has happened. The output
        # destination row used to sit here too, but it is a batch concern and crowds
        # the results tree; it now rides the Start/Stop bar (see `bar` below), which
        # frees this space for the tree.

        # The batch is a rudiment now (user, 2026-10-21: "keep it but only
        # rudimentary"): no defaults panel, no results table, no run bar with
        # progress and output path -- one footer button (`_build_status_bar`).
        # These variables are what `_settings()` feeds every `review.load`; the
        # widgets that once edited them were never on screen after 2026-10-19
        # and are gone.  The review strip's own controls override them per
        # photograph.
        self.v_strength = tk.DoubleVar(value=1.0)
        self.v_conf = tk.DoubleVar(value=Settings.min_confidence)
        self.v_maxpitch = tk.DoubleVar(value=Settings.max_pitch_deg)
        self.v_crop = tk.StringVar(value=Settings.crop)
        self.v_recursive = tk.BooleanVar(value=False)
        self.v_overwrite = tk.BooleanVar(value=False)
        self.v_review = tk.BooleanVar(value=True)
        # The detector combobox lives in the tools field (_build_tools);
        # App.v_detector follows it so _settings() reads the right value.
        self.v_detector = tk.StringVar(value=Settings.detector)
        self.v_jpegq = tk.IntVar(value=int(self._remembered.get("jpeg_quality", Settings.jpeg_quality)))
        self.v_jpegq.trace_add("write", lambda *_a: prefs.save(jpeg_quality=self.v_jpegq.get()))
        self.v_mask = tk.StringVar(value=Settings.mask_mode)
        self.v_maskpath = tk.StringVar(value="")
        self.v_fill = tk.StringVar(value=Settings.fill)
        # Fill/ComfyUI verdicts: once a label in the killed panel, now the
        # notice on the picture, so they are seen rather than written nowhere.
        self.lbl_fill = _NoticeLabel(self)

        # The ComfyUI settings live in their own window rather than in this
        # panel.  Two reasons, both found by looking: the panel is hidden until
        # a folder is loaded, so the server could not be set up first at all;
        # and these controls only matter for one of four fill modes, which is a
        # poor bargain for six widgets of permanent clutter.
        self.v_comfy_host = tk.StringVar()
        self.v_comfy_port = tk.StringVar()
        host, port = _split_url(self._remembered.get("comfy_url", Settings.comfy_url))
        self.v_comfy_host.set(host)
        self.v_comfy_port.set(port)
        self.v_comfy_wf = tk.StringVar(value=self._remembered.get("comfy_workflow", ""))
        self.v_comfy_wf_pick = tk.StringVar()   # what the chooser shows
        self.cb_comfy_wf = None
        self.v_comfy_models = {}
        for key in ("comfy_unet", "comfy_clip", "comfy_vae"):
            var = tk.StringVar(value=self._remembered.get(key, "") or "(from the workflow)")
            var.trace_add("write", lambda *_a, k=key: self._remember_model(k))
            self.v_comfy_models[key] = var
        self.cb_comfy_models = {}
        # The detail half, not a second copy of the label: "not checked --
        # not checked" is what the pair rendered before.
        self._comfy_state = ("unknown", "press Test connection to check the "
                                        "server and the workflow")
        # Review windows that want the same verdict.  Pushed to rather than
        # polled: the check runs on a worker and lands through `self.queue`, so
        # there is one place that knows the answer changed.
        self._comfy_listeners = []
        self._comfy_models_cache = {}
        # Editing the address invalidates the verdict: a green light beside a
        # port nobody has asked yet answers a question no longer on screen.
        for var in (self.v_comfy_host, self.v_comfy_port):
            var.trace_add("write", lambda *_a: self._show_comfy_state(
                "unknown", "the address changed -- press Test connection"))
        self._batch_total = 0

        # Built once and kept withdrawn: the popup costs nothing on screen
        # until it is opened, and the server can be configured before there
        # is any work to do.
        self._build_comfy_popup()

    # -- settings --------------------------------------------------------
    def _settings(self):
        s = Settings()
        # The pad colour is an output preference the review window remembers:
        # picked once, it applies to every later photograph and batch.
        if self._remembered.get("pad"):
            s.pad = self._remembered["pad"]
        s.jpeg_quality = int(self.v_jpegq.get())
        s.pitch_strength = s.roll_strength = float(self.v_strength.get())
        s.min_confidence = float(self.v_conf.get())
        s.max_pitch_deg = float(self.v_maxpitch.get())
        s.crop = self.v_crop.get()
        s.detector = self.v_detector.get()
        s.fill = self.v_fill.get()
        s.comfy_url = self._comfy_url()
        s.comfy_workflow = self.v_comfy_wf.get()
        for key, var in self.v_comfy_models.items():
            chosen = var.get()
            # The placeholder is not a filename; it means "whatever the
            # workflow says", which is the default and must stay empty.
            setattr(s, key, "" if chosen.startswith("(") else chosen)
        s.mask_mode = self.v_mask.get()
        path = self.v_maskpath.get() or self._remembered.get(
            "birefnet_model" if self.v_mask.get() in ("birefnet", "gdino") else "mask_file", "")
        if s.mask_mode == "file":
            s.mask_file = path
        elif s.mask_mode in ("birefnet", "gdino"):
            s.birefnet_model = path
        if s.mask_mode == "gdino":
            rp = getattr(self.review, "v_gdino_prompt", None)
            s.gdino_prompt = (rp.get() if rp is not None else "") or "building"
        return s

    def _check_fill(self):
        """Report the fill backend now, not once per photograph.

        Imported here rather than at module level so the window still opens on
        an interpreter without torch -- which, on Windows, is the interpreter
        that has Tkinter."""
        mode = self.v_fill.get()
        if mode == "none":
            self.lbl_fill.configure(text="")
            return
        if mode == "comfyui":
            # Choosing it is asking for it: nothing about a ComfyUI fill works
            # until an address and a workflow are settled, and a mode that
            # silently needs six settings nobody was shown is the kind of quiet
            # failure this project keeps arguing against.
            self._open_comfy()
            self._test_comfy()
            return
        from . import inpaint as FILL
        s = self._settings()
        text = FILL.describe(mode, s)
        if not FILL.available(mode, s):
            # It does not fail: `inpaint.fill` falls back to telea (2026-10-10).
            text = (f"{mode} unavailable, telea fills instead -- " + text
                    + ("  ('deps' installs it)" if mode == "lama" else ""))
        self.lbl_fill.configure(text=text)

    def _add(self, paths):
        added = 0
        new_files = []
        first_new = len(self.items)
        for p in paths:
            p = os.path.abspath(p)
            if not os.path.exists(p):
                continue
            # Opening something already in the list opens it again; it used to
            # be skipped, so re-opening a photograph (or retrying after Cancel)
            # silently did nothing.
            if os.path.isdir(p):
                files = self._expand(p)
                if not files:
                    continue
                new_files.extend(files)
            elif os.path.splitext(p)[1].lower() in READABLE:
                new_files.append(p)
            else:
                continue
            if p not in self.items:
                self.items.append(p)
            added += 1
        if added:
            # User, 2026-10-21: opening something replaces what is on screen --
            # it is never appended to a queue.  Changes on the current photo are
            # offered for saving first; unchanged, the new one simply opens.  A
            # folder (or several files at once) is walked one by one; a single
            # file just opens.
            if not self._leave_current():
                self._refresh_items()
                return added
            self._cur = first_new
            folder = any(os.path.isdir(os.path.abspath(p)) for p in paths)
            if folder or len(new_files) > 1:
                self._walk(new_files)
                self._notify(f"{len(new_files)} photograph(s) -- Save, Skip or "
                             "Close moves to the next")
            else:
                self._review_queue = []
                self._review_total = 0
                self._walking = False
                src = new_files[0]
                self.review.load(src, self._settings(), self._dest_corr(src),
                                 overwrite=self.v_overwrite.get(),
                                 on_saved=self._forget_saved)
        self._refresh_items()
        return added

    def _leave_current(self):
        """May the photograph on screen be replaced?  Asks only if it changed."""
        r = self.review
        if r.session is None or not r.is_dirty():
            return True
        name = os.path.basename(r.session.path)
        ans = messagebox.askyesnocancel(
            "Unsaved changes",
            f"Save the changes to {name} first?\n\n"
            "Yes saves it, No discards the changes, Cancel stays on it.",
            parent=self)
        if ans is None:
            self._notify(f"stayed on {name} -- nothing opened")
            return False
        if ans:
            r.on_closed = None          # the old walk must not advance now
            if not r._save():
                return False
            self._notify(f"saved {name}")
        else:
            self._notify(f"changes to {name} discarded")
        return True

    def _add_files(self):
        pats = " ".join("*" + e for e in sorted(READABLE))
        chosen = filedialog.askopenfilenames(
            title="choose one or more photos",
            filetypes=[("images", pats), ("all files", "*.*")])
        self._add(list(chosen))

    def _add_folder(self):
        d = filedialog.askdirectory(title="folder with photos")
        if d:
            self._add([d])

    def _paste_key(self, _e=None):
        """Ctrl+V anywhere in the window -- except in a text field, where it
        is that field's own paste (same rule as the tool keys)."""
        w = self.focus_get()
        try:
            if w is not None and w.winfo_class() in ("Entry", "TEntry", "Spinbox",
                                                     "TSpinbox", "TCombobox", "Text"):
                return None
        except Exception:
            pass
        self._paste_screenshot()
        return "break"

    def _paste_screenshot(self):
        """Grab an image from the clipboard and load it into the queue.

        Saved as a JPG in the output directory so the existing ``_add`` /
        review path handles it without any special-casing."""
        from PIL import ImageGrab
        img = ImageGrab.grabclipboard()
        # Files copied in Explorer arrive as a list of paths, not an image.
        if isinstance(img, list):
            n = self._add([p for p in img if isinstance(p, str)])
            self._notify(f"pasted {n} file(s)" if n else "nothing usable on the clipboard")
            return
        if img is None:
            self._notify("no image on the clipboard -- copy one, then Ctrl+V")
            return
        if img.mode != "RGB":
            img = img.convert("RGB")
        out_dir = self.v_output.get() or os.getcwd()
        os.makedirs(out_dir, exist_ok=True)
        import time as _time
        stamp = _time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(out_dir, f"screenshot_{stamp}.jpg")
        n = 1
        while os.path.exists(path):
            path = os.path.join(out_dir, f"screenshot_{stamp}_{n}.jpg")
            n += 1
        img.save(path, "JPEG", quality=int(self._settings().jpeg_quality),
                 subsampling=0, optimize=True)
        self._add([path])

    def _clear(self):
        self.items = []
        self._review_queue = []
        self._refresh_items()

    def _on_drop(self, event):
        """Tk hands the drop over as a Tcl list, so paths with spaces arrive
        brace-quoted; splitlist is what unpacks that correctly."""
        try:
            paths = self.tk.splitlist(event.data)
        except Exception:
            paths = [event.data]
        n = self._add(list(paths))
        if n == 0:
            self.review._set_status("nothing usable in that drop")

    def _set_stage(self):
        """Make sure the one persistent layout is on screen. There are no stages.

        There used to be two -- an empty landing screen that was nothing but a
        giant drop target, and a work screen that appeared when files arrived.
        The cross layout replaced both: the two preview slots and the two
        control columns build from the first frame, so an empty window is the
        same window with nothing in it. The loader is the cross's lower-left
        field rather than the whole screen, and the before slot doubles as the
        drop target, so nothing is buried by keeping the rest visible.

        What is left for this method is assembly, not reshaping: re-map
        anything that is not yet mapped. Nothing is hidden and nothing is
        destroyed, so widget state -- a folder chosen, a detector picked, a
        ComfyUI address typed -- survives emptying the list, and the window
        never changes shape underneath whoever is looking at it.
        """
        # Nothing left to re-map: the run bar and results list are gone
        # (2026-10-21) and everything else is built once inside the cross.
        return

    def _refresh_items(self):
        if self._cur >= len(self.items):
            self._cur = len(self.items) - 1
        self._set_stage()

    def _review_single(self):
        """Open the *current* image in the review window.

        The results tree is the normal path once a run has happened; this reviews
        whatever the panel is currently on (the newest added by default) without
        needing a run first."""
        if not self.items:
            messagebox.showinfo("Review", "add an image first")
            return
        i = self._cur if 0 <= self._cur < len(self.items) else len(self.items) - 1
        item = self.items[i]
        if os.path.isdir(item):
            inside = [f for f in self._expand(item) if os.path.isfile(f)]
            if not inside:
                messagebox.showinfo("Review", "that folder has no readable images")
                return
            item = inside[0]
        self.review.load(item, self._settings(), self._dest_corr(item),
                         overwrite=self.v_overwrite.get(),
                         on_saved=self._forget_saved)

    # -- one at a time ---------------------------------------------------
    def _dest_corr(self, src):
        """The ``_corr`` copy, never the original.

        ``_dest`` folds the overwrite decision into the path, which is right for
        an unattended run and wrong for the review window: there the checkbox is
        per photograph, so the window needs both candidates and picks one.
        """
        stem, ext = os.path.splitext(os.path.basename(src))
        out_dir = self.v_output.get() or os.path.dirname(src)
        return os.path.join(out_dir, f"{stem}_corr{ext}")

    def _forget_saved(self, src, dst):
        """Drop a photograph from the list once it has been written.

        What is left in the list is then exactly what is left to do, which is
        the only reading of it that survives a session long enough to be
        interrupted.
        """
        if src in self.items:
            self.items.remove(src)
            self._refresh_items()

    def _walk(self, files):
        """Review ``files`` one photograph at a time.

        Chained rather than looped: Tk has one event loop, and a `for` around a
        review either blocks it or opens them all at once.  Each photograph's
        `on_closed` opens the next, whichever way it was left -- Save, Skip or
        Close -- so the queue advances instead of stalling.  A new walk
        replaces any walk under way (user, 2026-10-21: never append)."""
        if not files:
            return
        self._review_queue = list(files)
        self._review_total = len(files)
        self._walking = True
        self._open_next_review()

    def _open_next_review(self):
        if not self.winfo_exists():
            return
        queue_left = getattr(self, "_review_queue", [])
        if not queue_left:
            if getattr(self, "_review_total", 0) > 1:
                self._notify(f"reviewed {self._review_total} photograph(s) -- "
                             "add more with +, the folder icon, a drop or Ctrl+V")
            self._review_total = 0
            self._walking = False
            return
        src = queue_left.pop(0)
        done = self._review_total - len(queue_left)
        self.review.load(src, self._settings(), self._dest_corr(src),
                         overwrite=self.v_overwrite.get(),
                         on_saved=self._forget_saved,
                         # Deferred: `on_closed` fires while the panel is being
                         # rebuilt, and loading inside that rebuild is asking for
                         # a half-dead parent.
                         on_closed=lambda: self._schedule_after(50, self._open_next_review),
                         position=f"[{done}/{self._review_total}]"
                                  if self._review_total > 1 else "")

    # -- run -------------------------------------------------------------
    def _expand(self, item):
        """One selection entry -> the photographs it stands for."""
        if os.path.isfile(item):
            return [item]
        if self.v_recursive.get():
            return [os.path.join(b, n) for b, _, names in os.walk(item)
                    for n in sorted(names, key=str.casefold)
                    if os.path.splitext(n)[1].lower() in READABLE]
        return [os.path.join(item, n) for n in sorted(os.listdir(item), key=str.casefold)
                if os.path.splitext(n)[1].lower() in READABLE
                and os.path.isfile(os.path.join(item, n))]

    def _files(self):
        """Expand the selection: single images stay as they are, folders are
        listed (recursively if asked)."""
        out, seen = [], set()
        for item in self.items:
            found = self._expand(item)
            for f in found:
                k = os.path.abspath(f)
                if k not in seen:
                    seen.add(k)
                    out.append(f)
        return out

    def _dest(self, src):
        if self.v_overwrite.get():
            return src
        stem, ext = os.path.splitext(os.path.basename(src))
        out_dir = self.v_output.get() or os.path.dirname(src)
        return os.path.join(out_dir, f"{stem}_corr{ext}")

    def _batch_click(self):
        """Idle: start the unattended run.  Running: stop it."""
        if self.worker is not None and self.worker.is_alive():
            self.stop_flag.set()
            self.btn_batch.configure(text="stopping...")
            return
        self._start()

    def _start(self):
        files = self._files()
        if not files:
            self._notify("batch: add photographs first (+, folder, drop or Ctrl+V)")
            return
        if not self._ensure_birefnet_model():
            return
        if not messagebox.askyesno(
                "Batch", f"Correct {len(files)} photograph(s) unattended?\n\n"
                         "Each is written as <name>_corr next to its original; "
                         "the ones it is not sure about are left alone and "
                         "opened for review afterwards."):
            return
        self.results = {OK: [], SKIPPED: [], ERROR: []}
        self._batch_total = len(files)
        self.stop_flag.clear()
        self.btn_batch.configure(text=f"stop 0/{len(files)}")
        self._notify(f"batch started: {len(files)} photograph(s) -- "
                     "click 'stop' in the footer to end it")
        settings = self._settings()
        # Destinations resolved here, on the Tk thread: `_dest` reads a Tk
        # variable, and touching one from the worker raises unless mainloop
        # happens to be running -- every file then "failed" with no reason.
        jobs = [(src, self._dest(src)) for src in files]
        self.worker = threading.Thread(target=self._run, args=(jobs, settings), daemon=True)
        self.worker.start()

    def _run(self, jobs, settings):
        for i, (src, dst) in enumerate(jobs, 1):
            if self.stop_flag.is_set():
                self.queue.put(("done", "stopped"))
                return
            try:
                r = process(src, dst, settings)
                self.queue.put(("row", (i, r.status, src, r.reason)))
            except Exception as exc:              # never let one file kill the run
                self.queue.put(("row", (i, ERROR, src, str(exc))))
        self.queue.put(("done", "finished"))

    def _pump(self):
        if not self.winfo_exists():
            return
        self._pump_job = None
        try:
            while not self._pump_closing:
                kind, payload = self.queue.get_nowait()
                if kind == "row":
                    i, status, src, why = payload
                    self.results.setdefault(status, []).append(src)
                    if status == ERROR:
                        self._log_error(f"batch: {src}: {why}")
                    self.btn_batch.configure(text=f"stop {i}/{self._batch_total}")
                elif kind == "comfy":
                    self._comfy_result(*payload)
                elif kind == "done":
                    self._batch_done(payload)
                elif kind == "call":           # any worker's way back to Tk
                    payload()
        except queue.Empty:
            pass
        if not self._pump_closing:
            self._pump_job = self._schedule_after(120, self._pump)

    def _batch_done(self, how):
        self.btn_batch.configure(text="batch")
        res = self.results
        ok, skip, err = (len(res.get(k, [])) for k in (OK, SKIPPED, ERROR))
        msg = f"batch {how}: {ok} written, {skip} skipped, {err} failed"
        left = res.get(SKIPPED, []) + res.get(ERROR, [])
        for src in res.get(OK, []):
            if src in self.items:
                self.items.remove(src)
        if left and self.v_review.get():
            msg += f" -- reviewing the {len(left)} left over"
            self._walk(left)
        if err:
            msg += " (reasons in pc_errors.log)"
        self._notify(msg)

    def _log_error(self, text):
        """The same file the preview's failures go to."""
        try:
            log = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "..", "pc_errors.log")
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(text + "\n" + "=" * 60 + "\n")
        except OSError:
            pass


def run(initial=None) -> int:
    # Maximized with its title bar, not borderless fullscreen (user, 2026-10-23:
    # "viele lieben das nicht"). F11 still toggles fullscreen.
    App(initial, start_maximized=True).mainloop()
    return 0
