"""The batch window: every method of ``App`` lives here on a mixin.

``App`` in :mod:`pc.gui` is now a small shell (the Tk root class plus the
setup, ComfyUI and app mixins); everything the window does -- building the
status bar and the panel, the batch queue and its worker, theme switching,
fullscreen, drag and drop -- is on ``_AppMixin`` here.  This module never
imports ``pc.gui``: the review panel the window hosts is passed in as the
shell's ``_REVIEW_PANEL_CLS`` class attribute, and every other need is
standard library, theme names or the other ``pc`` modules.
"""
from __future__ import annotations

import os
import queue
import sys
import threading

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import ImageTk

from . import __version__, layout, prefs
from .config import Settings
from .imageio import READABLE
from .inpaint import split_url as _split_url
from .pipeline import ERROR, OK, SKIPPED, process
from .ui_theme import (
    INK, THEMES,
    _attach_tooltip, _dark_titlebar, _hex_rgba, _logo_image,
    _retint_bg, _set_window_icon, _wordmark_image,
    apply_theme,
)

_MAC = sys.platform == "darwin"   # gui.py keeps its own copy for RB / ALT_MASK


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


class _AppMixin:
    """All of the batch window's methods, mixed into ``App`` in :mod:`pc.gui`.

    The host is the window itself (``_ROOT_CLASS`` plus the ``_AfterOwner``,
    ``_SetupMixin`` and ``_ComfyMixin`` bases); the methods build the widgets
    and keep their state on ``self``.  The review panel they host is the
    host's ``_REVIEW_PANEL_CLS`` attribute, set by the shell, so this module
    never imports :mod:`pc.gui` back.
    """

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
        if self._remembered.get("mask_mode") in ("off", "file"):
            self.v_mask.set(self._remembered["mask_mode"])
        if self._remembered.get("mask_file"):
            self.v_maskpath.set(self._remembered["mask_file"])
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
        # `_REVIEW_PANEL_CLS(self)` is still constructing, so they exist before it.
        self.v_output = tk.StringVar()
        self.items = []                       # files and/or folders, in order
        self._cur = -1                        # index into items the panel is on

        # The window is the brand header and the cross -- nothing else.  No
        # paned split, no options strip, no start bar below: the results list
        # lives in the cross's lower-left field and the batch controls in the
        # lower-right one, so a resize grows four equal fields and re-decides
        # nothing.
        self.review = self._REVIEW_PANEL_CLS(self)
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
        if s.mask_mode == "file":
            s.mask_file = (self.v_maskpath.get()
                           or self._remembered.get("mask_file", ""))
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
