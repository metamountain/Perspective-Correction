"""Tkinter batch window with a manual review mode.

Two screens.  The *batch* screen runs a folder and logs OK / SKIPPED / ERROR.
The *review* screen opens one image and lets the decision be overridden by hand:
sliders for roll, pitch and focal length, and a clickable overlay for striking
out the lines the detector should not have trusted.

Anything that is not toolkit plumbing lives in :mod:`bpc.review`, which has no
Tkinter dependency and is covered by the test suite; this file is the shell.

Tkinter ships with the python.org installer on Windows, which is the target
platform.  On Linux it may need a separate package (``python3-tk``); the CLI
works without it.
"""
from __future__ import annotations

import math
import os
import queue
import threading
import traceback

import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

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

import cv2
import numpy as np
from PIL import Image, ImageTk

from . import prefs
from .config import Settings
from .imageio import READABLE
from .pipeline import ERROR, OK, SKIPPED, process
from .inpaint import join_url as _join_url, split_url as _split_url
from .review import AUTO, MANUAL, ReviewSession

STATUS_COLOUR = {OK: "#5ac37f", SKIPPED: "#e0b24c", ERROR: "#ef6b6b"}

# Both windows offer the same list, and it has to match cli.py's --detector
# choices; a name only one of the two knows about is a bug report waiting to
# happen.  mlsd/hybrid/union need a TFLite runtime and the deep-* three need
# torch and a DeepLSD checkout, so several of these can fail to load -- which
# is why both windows report the failure rather than falling back.
DETECTORS = ("auto", "lsd", "fld", "hough", "mlsd", "hybrid", "union",
             "deeplsd", "deep-hybrid", "deep-union")

# --------------------------------------------------------------------------
# theme
# --------------------------------------------------------------------------
# One dark palette, one accent, three type sizes.  Photographs are judged
# against what surrounds them, and a light chrome around a picture shifts every
# perceived tone in it -- which is the whole reason image editors are dark.  The
# restraint is not decoration either: everything here competes for attention
# with the photograph, and loses on purpose.
INK = {
    "bg":      "#16181c",   # window
    "panel":   "#1d2025",   # raised surfaces
    "field":   "#101216",   # inputs, canvases, the image well
    "line":    "#2b2f36",   # hairlines, borders
    "text":    "#e6e8ec",
    "dim":     "#8b929c",   # secondary text
    "accent":  "#4da3ff",
    "ok":      "#5ac37f",
    "warn":    "#e0b24c",
    "err":     "#ef6b6b",
}

# Grotesque first, then whatever the platform has.  Numbers get a mono face so
# columns of angles line up -- a table of readings that does not align is harder
# to scan than one with fewer readings in it.
_UI_FAMILIES = ("Inter", "Segoe UI Variable", "Segoe UI", "Helvetica Neue", "DejaVu Sans")
_MONO_FAMILIES = ("JetBrains Mono", "Cascadia Mono", "Consolas", "DejaVu Sans Mono")


def _pick_family(root, candidates, fallback):
    try:
        from tkinter import font as tkfont
        have = set(tkfont.families(root))
    except Exception:
        return fallback
    for c in candidates:
        if c in have:
            return c
    return fallback


def apply_theme(root):
    """Dark, flat, and quiet.  Returns ``(ui_family, mono_family)``."""
    ui = _pick_family(root, _UI_FAMILIES, "TkDefaultFont")
    mono = _pick_family(root, _MONO_FAMILIES, "TkFixedFont")
    try:
        from tkinter import font as tkfont
        for name, fam, size in (("TkDefaultFont", ui, 10), ("TkTextFont", ui, 10),
                                ("TkMenuFont", ui, 10), ("TkHeadingFont", ui, 10),
                                ("TkFixedFont", mono, 10)):
            f = tkfont.nametofont(name)
            f.configure(family=fam, size=size)
    except Exception:
        pass

    root.configure(background=INK["bg"])
    st = ttk.Style(root)
    try:
        st.theme_use("clam")            # the only stock theme that takes colours
    except Exception:
        pass
    st.configure(".", background=INK["bg"], foreground=INK["text"],
                 fieldbackground=INK["field"], bordercolor=INK["line"],
                 lightcolor=INK["panel"], darkcolor=INK["panel"],
                 focuscolor=INK["accent"], troughcolor=INK["field"],
                 insertcolor=INK["text"], font=(ui, 10))
    st.configure("TFrame", background=INK["bg"])
    st.configure("Panel.TFrame", background=INK["panel"])
    st.configure("TLabel", background=INK["bg"], foreground=INK["text"])
    st.configure("Dim.TLabel", foreground=INK["dim"])
    st.configure("Head.TLabel", foreground=INK["dim"], font=(ui, 9))
    st.configure("Value.TLabel", foreground=INK["text"], font=(mono, 10))
    st.configure("Title.TLabel", foreground=INK["text"], font=(ui, 15))

    st.configure("TButton", background=INK["panel"], foreground=INK["text"],
                 borderwidth=0, focusthickness=0, padding=(12, 6))
    st.map("TButton",
           background=[("pressed", INK["line"]), ("active", INK["line"])],
           foreground=[("disabled", INK["dim"])])
    st.configure("Accent.TButton", background=INK["accent"], foreground="#0b1017",
                 padding=(14, 7))
    st.map("Accent.TButton", background=[("active", "#6bb4ff"),
                                         ("disabled", INK["line"])])

    st.configure("TEntry", padding=6, borderwidth=0)
    st.configure("TCombobox", padding=4, borderwidth=0, arrowcolor=INK["dim"])
    st.map("TCombobox", fieldbackground=[("readonly", INK["field"])],
           foreground=[("readonly", INK["text"])])
    st.configure("TCheckbutton", background=INK["bg"], foreground=INK["text"])
    st.map("TCheckbutton", background=[("active", INK["bg"])])
    st.configure("TScale", background=INK["bg"], troughcolor=INK["field"])
    st.configure("TProgressbar", background=INK["accent"], troughcolor=INK["field"],
                 borderwidth=0, thickness=4)
    st.configure("Treeview", background=INK["field"], fieldbackground=INK["field"],
                 foreground=INK["text"], borderwidth=0, rowheight=24)
    st.configure("Treeview.Heading", background=INK["bg"], foreground=INK["dim"],
                 borderwidth=0, font=(ui, 9))
    st.map("Treeview", background=[("selected", INK["line"])],
           foreground=[("selected", INK["text"])])
    st.configure("TSpinbox", arrowcolor=INK["dim"], borderwidth=0, padding=4)
    st.configure("TLabelframe", background=INK["bg"], bordercolor=INK["line"])
    st.configure("TLabelframe.Label", background=INK["bg"], foreground=INK["dim"])
    return ui, mono


def _to_photo(bgr, box):
    """BGR array -> PhotoImage, letterboxed into ``box`` = (w, h)."""
    bw, bh = max(box[0], 1), max(box[1], 1)
    h, w = bgr.shape[:2]
    s = min(bw / w, bh / h, 4.0)
    if s <= 0:
        s = 1.0
    out = cv2.resize(bgr, (max(1, int(w * s)), max(1, int(h * s))),
                     interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
    rgb = cv2.cvtColor(out, cv2.COLOR_BGR2RGB)
    return ImageTk.PhotoImage(Image.fromarray(rgb)), s


# --------------------------------------------------------------------------
# Beholder pyramid -- the all-seeing eye in a triangle, this project's mark.
# Drawn twice from one set of normalized points so they agree: as Tk canvas
# geometry for the window headers, and via PIL for the OS taskbar icon.
# --------------------------------------------------------------------------
def _beholder_points(w, h):
    """Map the pyramid / eye / pupil into a ``w`` x ``h`` box."""
    tri = ((0.50 * w, 0.08 * h), (0.10 * w, 0.92 * h), (0.90 * w, 0.92 * h))
    eye_box = (0.30 * w, 0.50 * h, 0.70 * w, 0.72 * h)
    pcx, pcy, pr = 0.50 * w, 0.60 * h, 0.05 * w
    pupil_box = (pcx - pr, pcy - pr, pcx + pr, pcy + pr)
    return tri, eye_box, pupil_box


def _emblem_size(root):
    """DPI-flexible emblem size: ~28px base, scales with the display, clamped."""
    try:
        scale = max(0.75, min(root.winfo_fpixels("1i") / 96.0, 2.0))
    except Exception:
        scale = 1.0
    return max(20, min(40, int(round(28 * scale))))


def _draw_eye_pyramid(canvas):
    """Draw the mark on a Tk canvas sized to its own width/height."""
    w = max(int(canvas.winfo_width()), 1)
    h = max(int(canvas.winfo_height()), 1)
    tri, eye_box, pupil_box = _beholder_points(w, h)
    col = "#ffffff"
    canvas.create_polygon(*tri, outline=col, width=2, fill="")
    canvas.create_oval(eye_box, outline=col, width=2, fill="")
    canvas.create_oval(pupil_box, outline=col, fill=col, width=1)


LOGO_FILE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "..", "..", "Logo_BPC.png"))


def _logo_image(size, colour):
    """The shipped mark at ``size``, recoloured to ``colour``, or ``None``.

    ``Logo_BPC.png`` is a black silhouette on transparency, and this window's
    ground is dark, so drawing it as it comes gives a black shape on a black
    panel.  What is used is its **alpha channel**; the colour comes from the
    palette, which also means the mark follows the theme instead of fighting it.

    ``None`` when the file is absent: the logo is an asset, not a dependency,
    and a window that refuses to open because a PNG is missing would be a far
    worse bug than a missing logo.
    """
    try:
        src = Image.open(LOGO_FILE).convert("RGBA")
    except Exception:
        return None
    src = src.resize((size, size), Image.LANCZOS)
    tint = Image.new("RGBA", (size, size), colour)
    tint.putalpha(src.getchannel("A"))
    return tint


def _beholder_pil(size=64):
    """The mark as a black-background RGBA PIL image, for ``iconphoto``.

    Backed on black rather than left transparent: a taskbar's own colour is not
    knowable, and a white-on-nothing glyph disappears on half of them.
    """
    from PIL import ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    mark = _logo_image(size, "#ffffff")
    if mark is not None:
        img.alpha_composite(mark)
        return img
    d = ImageDraw.Draw(img)
    col = (255, 255, 255, 255)
    tri, eye_box, pupil_box = _beholder_points(size, size)
    lw = max(1, int(round(size / 32)))
    d.line([tri[0], tri[1], tri[2], tri[0]], fill=col, width=lw, joint="curve")
    d.ellipse(eye_box, outline=col, width=lw)
    d.ellipse(pupil_box, fill=col)
    return img


def _set_window_icon(win):
    """Set the OS taskbar icon; returns the PhotoImage ref (store it to avoid GC)."""
    try:
        photo = ImageTk.PhotoImage(_beholder_pil(64))
        win.iconphoto(True, photo)
        return photo
    except Exception:
        return None


def _attach_tooltip(widget, text):
    """A plain hover tooltip; no dependency beyond Tk itself."""
    tip = {"t": None}

    def show(_e=None):
        if tip["t"] is not None:
            return
        t = tk.Toplevel(widget)
        t.wm_overrideredirect(True)
        x = widget.winfo_rootx() + 12
        y = widget.winfo_rooty() + widget.winfo_height() + 4
        t.wm_geometry(f"+{x}+{y}")
        lbl = tk.Label(t, text=text, background="#0b1017", foreground=INK["text"],
                       font=("TkDefaultFont", 9), padx=8, pady=4, relief="solid",
                       borderwidth=1, wraplength=320, justify="left")
        lbl.pack()
        tip["t"] = t

    def hide(_e=None):
        if tip["t"] is not None:
            tip["t"].destroy()
            tip["t"] = None

    widget.bind("<Enter>", show)
    widget.bind("<Leave>", hide)


def _brand_header(parent):
    """Emblem + wordmark, packed at the top of a window."""
    bar = ttk.Frame(parent)
    bar.pack(fill="x", side="top")
    size = _emblem_size(parent)
    mark = _logo_image(size, INK["text"])
    if mark is not None:
        photo = ImageTk.PhotoImage(mark)
        lbl = ttk.Label(bar, image=photo)
        lbl.image = photo               # a Label keeps no reference of its own
        lbl.pack(side="left", padx=(2, 8), pady=4)
    else:
        # Drawn only when the PNG is missing.  On `<Configure>` rather than
        # after `update_idletasks`, because a canvas that has not been mapped
        # yet reports a width of 1 -- which drew the whole mark into a single
        # pixel and looked like a stray white dot beside the title.
        cv = tk.Canvas(bar, width=size, height=size, bg=INK["bg"],
                       highlightthickness=0)
        cv.pack(side="left", padx=(2, 8), pady=4)
        cv.bind("<Configure>",
                lambda e, c=cv: (c.delete("all"), _draw_eye_pyramid(c)))
    title = ttk.Label(bar, text="Batch Perspective Correction", style="Title.TLabel")
    title.pack(side="left", anchor="w")
    _attach_tooltip(title, "Batch perspective correction for architectural photographs")
    return bar


# ==========================================================================
# review window
# ==========================================================================
class ReviewWindow(tk.Toplevel):
    def __init__(self, master, path, settings, dest_path, on_saved=None,
                 overwrite=False, on_closed=None, position=""):
        super().__init__(master)
        self.title(f"{position}  {os.path.basename(path)}".strip())
        self.geometry("1280x820")
        self.minsize(900, 600)
        apply_theme(self)
        self.configure(background=INK["bg"])
        self._icon = _set_window_icon(self)
        self.settings = settings
        self.dest_path = dest_path
        self.on_saved = on_saved
        # Fired however the window goes away -- saved, kept, or closed by the
        # window manager.  A queue that only advances on Save stalls forever on
        # the first photograph someone closes with the X.
        self.on_closed = on_closed
        self._closed_sent = False
        self._busy = False
        self._before_scale = 1.0

        try:
            self.session = ReviewSession(path, settings)
        except Exception as exc:
            messagebox.showerror("Review", f"cannot open image:\n{exc}", parent=master)
            self._fire_closed()
            self.destroy()
            return

        self._build()
        self.v_overwrite.set(bool(overwrite))
        self.v_alpha.set(self.session.mask_alpha)
        # Small correction, small band: take the crop rather than leave a band
        # that would otherwise need a generative model to fill.  Visible, shaded
        # and undoable with "Reset crop" -- see `auto_crop_if_cheap`.
        if self.session.auto_crop_if_cheap():
            self._refresh_crop()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(60, self._sync_from_session)

    def _fire_closed(self):
        if self.on_closed and not self._closed_sent:
            self._closed_sent = True
            self.on_closed()

    def _on_close(self):
        self._fire_closed()
        self.destroy()

    def _target_path(self):
        """Where Save writes: the original, or the ``_corr`` copy beside it."""
        return self.session.path if self.v_overwrite.get() else self.dest_path

    # -- layout ----------------------------------------------------------
    def _build(self):
        _brand_header(self)
        top = ttk.Frame(self, padding=6)
        top.pack(fill="both", expand=True)

        panes = ttk.Frame(top)
        panes.pack(fill="both", expand=True)
        panes.columnconfigure(0, weight=1)
        panes.columnconfigure(1, weight=1)
        panes.rowconfigure(1, weight=1)

        ttk.Label(panes, text="before  (click a line to strike it out / bring it back)"
                  ).grid(row=0, column=0, sticky="w")
        ttk.Label(panes, text="after").grid(row=1 - 1, column=1, sticky="w")

        self.c_before = tk.Canvas(panes, bg=INK["field"], highlightthickness=0)
        self.c_before.grid(row=1, column=0, sticky="nsew", padx=(0, 3))
        self.c_after = tk.Canvas(panes, bg=INK["field"], highlightthickness=0)
        self.c_after.grid(row=1, column=1, sticky="nsew", padx=(3, 0))
        self._pending_mark = None
        self._after_off = (0, 0)
        self._crop_drag_start = None
        self.c_before.bind("<Button-1>", self._on_click_before)
        self.c_after.bind("<ButtonPress-1>", self._on_crop_press)
        self.c_after.bind("<B1-Motion>", self._on_crop_drag)
        self.c_after.bind("<ButtonRelease-1>", self._on_crop_release)
        for c in (self.c_before, self.c_after):
            c.bind("<Configure>", lambda e: self._schedule_redraw())

        self.status = tk.Text(top, height=4, wrap="word", relief="flat",
                              borderwidth=0, highlightthickness=0, padx=10, pady=8,
                              background=INK["field"], foreground=INK["dim"],
                              font=("TkFixedFont",))
        self.status.pack(fill="x", pady=(6, 4))
        self.status.bind("<Key>", lambda e: "break")

        ctl = ttk.Frame(top, padding=(0, 8, 0, 0))
        ctl.pack(fill="x")
        self.v_roll = tk.DoubleVar(value=0.0)
        self.v_pitch = tk.DoubleVar(value=0.0)
        self.v_focal = tk.DoubleVar(value=28.0)
        self._slider(ctl, 0, "roll (level)", self.v_roll, -20, 20, "deg")
        self._slider(ctl, 1, "pitch (verticals)", self.v_pitch, -30, 30, "deg")
        self._slider(ctl, 2, "focal length", self.v_focal, 8, 200, "mm eq")

        # The detector belongs beside the mask, not in the batch panel only:
        # both change what the estimator is looking at rather than what it does
        # with it, and both can only be judged against the lines on screen.
        det = ttk.Frame(top, padding=(0, 8, 0, 0))
        det.pack(fill="x")
        self.v_detector = tk.StringVar(value=self.settings.detector)
        ttk.Label(det, text="line detector", width=18).grid(row=0, column=0, sticky="w")
        dbox = ttk.Combobox(det, textvariable=self.v_detector, width=11,
                            state="readonly", values=list(DETECTORS))
        dbox.grid(row=0, column=1, sticky="w", padx=(6, 6))
        dbox.bind("<<ComboboxSelected>>", lambda e: self._apply_detector())
        det.columnconfigure(2, weight=1)

        msk = ttk.Frame(top, padding=(0, 8, 0, 0))
        msk.pack(fill="x")
        self.v_maskmode = tk.StringVar(value=self.settings.mask_mode)
        ttk.Label(msk, text="source", width=18).grid(row=0, column=0, sticky="w")
        box = ttk.Combobox(msk, textvariable=self.v_maskmode, width=8, state="readonly",
                           values=["off", "file", "birefnet"])
        box.grid(row=0, column=1, sticky="w", padx=(6, 6))
        box.bind("<<ComboboxSelected>>", lambda e: self._apply_mask())
        ttk.Button(msk, text="mask folder...", command=self._pick_mask_folder
                   ).grid(row=0, column=2, sticky="w")
        ttk.Button(msk, text="BiRefNet model...", command=self._pick_birefnet_model
                   ).grid(row=0, column=4, sticky="w", padx=(10, 0))
        self.v_maskinv = tk.BooleanVar(value=self.settings.mask_invert)
        ttk.Checkbutton(msk, text="mask marks what to KEEP",
                        variable=self.v_maskinv, command=self._apply_mask
                        ).grid(row=0, column=3, sticky="w", padx=10)
        self.lbl_mask = ttk.Label(msk, text="", wraplength=760, justify="left")
        self.lbl_mask.grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))
        self.v_alpha = tk.DoubleVar(value=0.28)
        ttk.Label(msk, text="mask opacity", width=18).grid(row=2, column=0, sticky="w")
        ttk.Scale(msk, from_=0.0, to=1.0, variable=self.v_alpha, orient="horizontal",
                  command=lambda _v: self._on_alpha()).grid(row=2, column=1, columnspan=3,
                                                            sticky="ew", padx=6)
        msk.columnconfigure(3, weight=1)

        fill_row = ttk.Frame(top, padding=(0, 8, 0, 0))
        fill_row.pack(fill="x")
        self.v_fill = tk.StringVar(value=self.session.settings.fill or "none")
        ttk.Label(fill_row, text="fill band", width=18).grid(row=0, column=0, sticky="w")
        fbox = ttk.Combobox(fill_row, textvariable=self.v_fill, width=11,
                            state="readonly", values=["none", "telea", "lama", "comfyui"])
        fbox.grid(row=0, column=1, sticky="w", padx=(6, 6))
        fbox.bind("<<ComboboxSelected>>", lambda e: self._apply_fill())
        ttk.Button(fill_row, text="pad colour...",
                   command=self._pick_pad_colour).grid(row=0, column=2, sticky="w")
        self.lbl_pad_colour = tk.Label(fill_row, fg=INK["text"], width=4,
                                       relief="flat", font=("TkDefaultFont", 8))
        self.lbl_pad_colour.grid(row=0, column=3, sticky="w", padx=(6, 0))
        ttk.Button(fill_row, text="edge",
                   command=self._pad_edge).grid(row=0, column=4, sticky="w", padx=(6, 0))
        self._sync_pad_swatch()

        btns = ttk.Frame(top, padding=(0, 8))
        btns.pack(fill="x")
        ttk.Button(btns, text="Auto", command=self._use_auto).pack(side="left")
        ttk.Button(btns, text="Reset", command=self._reset).pack(side="left", padx=6)
        self.v_mark = tk.BooleanVar(value=False)
        ttk.Checkbutton(btns, text="Mark vertical",
                        variable=self.v_mark, command=self._on_mark_toggle
                        ).pack(side="left", padx=(12, 0))
        ttk.Button(btns, text="Clear marks",
                   command=self._clear_marks).pack(side="left", padx=6)
        ttk.Button(btns, text="Strike slanted",
                   command=self._strike_slanted).pack(side="left", padx=(6, 12))
        ttk.Button(btns, text="Auto crop",
                    command=self._auto_crop).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Reset crop",
                    command=self._clear_crop).pack(side="left", padx=(0, 6))
        ttk.Checkbutton(btns, text="Lines", command=self._schedule_redraw,
                         variable=self._mk_show()).pack(side="left")
        ttk.Checkbutton(btns, text="Mask", command=self._toggle_mask,
                         variable=self._mk_mask()).pack(side="left", padx=6)
        ttk.Button(btns, text="Save", command=self._save,
                   style="Accent.TButton").pack(side="right")
        self.v_overwrite = tk.BooleanVar(value=False)
        ttk.Checkbutton(btns, text="overwrite original",
                        variable=self.v_overwrite).pack(side="right", padx=8)
        ttk.Button(btns, text="Keep original",
                   command=self._keep).pack(side="right", padx=6)

    def _mk_show(self):
        self.v_show_lines = tk.BooleanVar(value=True)
        return self.v_show_lines

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
        else:
            self.lbl_mask.configure(
                text=f"{self.session.detector}: {len(self.session.vert)} vertical "
                     f"candidate(s), {len(self.session.horiz)} horizontal")
        if self.session.mode == AUTO:
            self._sync_from_session()
        else:
            self._redraw()

    def _apply_mask(self):
        mode = self.v_maskmode.get()
        if mode == "file" and not self.session.settings.mask_file:
            if not self._pick_mask_folder(apply_now=False):
                self.v_maskmode.set(self.session.settings.mask_mode)
                return
        if mode == "birefnet" and not self.session.settings.birefnet_model:
            if not self._pick_birefnet_model(apply_now=False):
                self.v_maskmode.set(self.session.settings.mask_mode)
                return
        err = self.session.set_mask(mode, invert=bool(self.v_maskinv.get()))
        self.lbl_mask.configure(text=err or "")
        if self.session.mode == AUTO:
            self._sync_from_session()
        else:
            self._redraw()

    def _pick_birefnet_model(self, apply_now=True):
        """Point at BiRefNet weights and say what they are.

        A folder of these holds look-alikes -- HR, lite, matting, 2K variants
        that run at different resolutions -- and the architecture has to sit
        beside them, so the choice is described immediately rather than after a
        failed batch."""
        from . import birefnet as BN
        p = filedialog.askopenfilename(
            title="BiRefNet weights",
            filetypes=[("BiRefNet weights", "*.safetensors *.pth *.pt"),
                       ("all files", "*.*")], parent=self)
        if not p:
            return False
        try:
            BN._arch_dir(p)
        except BN.BiRefNetUnavailable as exc:
            # remembering a checkpoint that cannot be loaded is worse than not
            # remembering one: every later run fails with the same message and
            # nothing points at the file dialog as the cause
            messagebox.showerror("BiRefNet model", str(exc))
            return False
        self.session.settings = self.session.settings.replace(birefnet_model=p)
        prefs.save(birefnet_model=p)     # typed once, not once per session
        self.lbl_mask.configure(text=BN.describe(p))
        if apply_now:
            self.v_maskmode.set("birefnet")
            self._apply_mask()
        return True

    def _pick_mask_folder(self, apply_now=True):
        """A folder of one mask per photo is what ``--mask-export`` writes; a
        single PNG is the hand-painted case."""
        d = filedialog.askdirectory(title="folder of mask images (one per photo)",
                                    parent=self)
        if not d:
            return False
        self.session.settings = self.session.settings.replace(mask_file=d)
        prefs.save(mask_file=d)
        if apply_now:
            self.v_maskmode.set("file")
            self._apply_mask()
        return True

    def _on_alpha(self):
        self.session.mask_alpha = float(self.v_alpha.get())
        self._schedule_redraw()

    def _apply_fill(self):
        """The session owns the settings the save reads; the window only shows
        them.  Writing ``self.settings`` here made the whole control a no-op."""
        self.session.settings = self.session.settings.replace(fill=self.v_fill.get())
        self._schedule_redraw()

    def _pick_pad_colour(self):
        """What fills the corners *before* any generation, and all that fills
        them when the fill is off.  One setting, ``--pad``, not a second one."""
        current = self.session.settings.pad
        col = colorchooser.askcolor(
            initial=current if current.startswith("#") else "#000000",
            title="pad colour", parent=self)
        hexval = col[1] if col[1] else None
        if not hexval:
            return
        self.session.settings = self.session.settings.replace(pad=hexval)
        self._sync_pad_swatch()
        self._schedule_redraw()

    def _pad_edge(self):
        """Back to extending the border colour, which is the default and has no
        swatch to show."""
        self.session.settings = self.session.settings.replace(pad="edge")
        self._sync_pad_swatch()
        self._schedule_redraw()

    def _sync_pad_swatch(self):
        """The swatch must report what ``pad`` actually is.  It defaults to
        ``edge``, which is not a colour, so a black square would be a lie."""
        pad = self.session.settings.pad
        if pad.startswith("#"):
            self.lbl_pad_colour.configure(bg=pad, text="")
        else:
            self.lbl_pad_colour.configure(bg=INK["field"], text=pad[:4])

    def _mk_mask(self):
        self.v_show_mask = tk.BooleanVar(value=True)
        return self.v_show_mask

    def _toggle_mask(self):
        """Show the excluded region and the lines it removed.

        A mask the user cannot see is a mask the user cannot trust: when a
        segmenter takes out the wrong half of a building, the only other symptom
        is a quietly worse answer."""
        self.session.show_mask = bool(self.v_show_mask.get())
        self._schedule_redraw()

    def _slider(self, parent, row, label, var, lo, hi, unit):
        ttk.Label(parent, text=label, width=18).grid(row=row, column=0, sticky="w")
        sc = ttk.Scale(parent, from_=lo, to=hi, variable=var, orient="horizontal",
                       command=lambda _v: self._on_slider())
        sc.grid(row=row, column=1, sticky="ew", padx=6)
        parent.columnconfigure(1, weight=1)
        lbl = ttk.Label(parent, width=12)
        lbl.grid(row=row, column=2, sticky="e")
        setattr(self, f"_lbl_{row}", (lbl, var, unit))

    def _update_slider_labels(self):
        for row in range(3):
            lbl, var, unit = getattr(self, f"_lbl_{row}")
            lbl.configure(text=f"{var.get():+.2f} {unit}" if unit == "deg"
                          else f"{var.get():.0f} {unit}")

    # -- state -----------------------------------------------------------
    def _sync_from_session(self):
        roll, pitch, f, _ = self.session.current_angles()
        from .model import focal_35mm_from_px
        self.v_roll.set(round(math.degrees(roll), 2))
        self.v_pitch.set(round(math.degrees(pitch), 2))
        if f:
            self.v_focal.set(round(focal_35mm_from_px(f, self.session.w, self.session.h), 0))
        self._redraw()

    def _on_slider(self):
        self.session.set_manual(roll_deg=self.v_roll.get(), pitch_deg=self.v_pitch.get(),
                                focal_35mm=self.v_focal.get())
        self._schedule_redraw()

    def _strike_slanted(self):
        n = self.session.disable_lines_by_angle(18.0)
        if self.session.mode == AUTO:
            self._sync_from_session()
        else:
            self._redraw()
        self._set_status_extra(f"struck out {n} slanted candidate(s)")

    def _use_auto(self):
        """Back to the angles the estimator found, keeping the rest of the work.

        The neighbouring Reset is the big hammer -- it also re-enables every
        struck line, drops the vertical marks and the hand-drawn crop.  Wanting
        the found angles back after a mis-dragged slider is the common case and
        should not cost all of that.
        """
        self.session.use_auto_angles()
        self._sync_from_session()

    def _reset(self):
        self.session.reset_to_auto()
        self._sync_from_session()

    def _on_mark_toggle(self):
        """Entering or leaving vertical-marking mode; a half-finished line is
        forgotten rather than left dangling."""
        self._pending_mark = None
        self._set_status(self.session.status_text())
        self._redraw()

    def _clear_marks(self):
        if self.session.clear_control_lines():
            self._sync_from_session()

    def _clear_crop(self):
        if self.session.clear_crop_rect():
            self._refresh_crop()

    def _auto_crop(self):
        """Cut the padded band away instead of inventing something to put in it."""
        if self.session.auto_crop():
            self._refresh_crop()
            self._set_status_extra("cropped to the largest rectangle with no "
                                   "invented pixels in it")
        else:
            self._set_status_extra("nothing to trim -- the correction opened no "
                                   "band, or the plan had already cropped it")

    def _refresh_crop(self):
        """Redraw the overlay for a changed crop, without re-rendering the image.

        The picture behind it cannot have changed -- the crop is applied on
        save, not in the preview -- so a full redraw would re-warp and, with a
        live fill, re-inpaint an image identical to the one already on screen.
        That pause is itself a kind of jump.
        """
        if getattr(self, "_ph_a", None) is None:
            self._schedule_redraw()      # nothing on screen to draw over yet
            return
        self._draw_crop_persistent()
        self._set_status(self.session.status_text())

    def _on_crop_press(self, event):
        if getattr(self, "_ph_a", None) is None:
            return
        x = event.x - self._after_off[0]
        y = event.y - self._after_off[1]
        iw, ih = self._ph_a.width(), self._ph_a.height()
        x = max(0, min(iw, x))
        y = max(0, min(ih, y))
        self._crop_drag_start = self._grab_corner(x, y, iw, ih) or (x, y)

    def _grab_corner(self, x, y, iw, ih, radius=14):
        """Answer a press near a corner of the existing crop with the *opposite*
        corner, or ``None`` when the press is not on a handle.

        That opposite corner then plays exactly the role the first click plays
        when a rectangle is drawn from nothing, so adjusting an existing crop
        and drawing a new one are one drag implementation rather than two.
        Without handles a crop can only be redrawn, and nudging one edge means
        re-placing all four.
        """
        # The same full-frame default `_draw_crop_persistent` draws, so the
        # handles it shows on an uncropped photograph are the handles this
        # grabs.  Drawing a handle nobody can pick up is worse than drawing none.
        x0, y0, x1, y1 = self.session.crop_rect or (0.0, 0.0, 1.0, 1.0)
        cx0, cy0, cx1, cy1 = x0 * iw, y0 * ih, x1 * iw, y1 * ih
        for (hx, hy), opposite in (((cx0, cy0), (cx1, cy1)),
                                   ((cx1, cy0), (cx0, cy1)),
                                   ((cx0, cy1), (cx1, cy0)),
                                   ((cx1, cy1), (cx0, cy0))):
            if abs(x - hx) <= radius and abs(y - hy) <= radius:
                return opposite
        return None

    def _on_crop_drag(self, event):
        if self._crop_drag_start is None or getattr(self, "_ph_a", None) is None:
            return
        self.c_after.delete("crop_overlay")
        x0 = self._crop_drag_start[0]
        y0 = self._crop_drag_start[1]
        x1 = event.x - self._after_off[0]
        y1 = event.y - self._after_off[1]
        iw, ih = self._ph_a.width(), self._ph_a.height()
        x1 = max(0, min(iw, x1))
        y1 = max(0, min(ih, y1))
        ox, oy = self._after_off
        self._draw_crop_overlay(ox + x0, oy + y0, ox + x1, oy + y1,
                                ox, oy, iw, ih)

    def _on_crop_release(self, event):
        if self._crop_drag_start is None or getattr(self, "_ph_a", None) is None:
            return
        x0 = self._crop_drag_start[0]
        y0 = self._crop_drag_start[1]
        x1 = event.x - self._after_off[0]
        y1 = event.y - self._after_off[1]
        iw, ih = self._ph_a.width(), self._ph_a.height()
        x1 = max(0, min(iw, x1))
        y1 = max(0, min(ih, y1))
        self._crop_drag_start = None
        # A click that never moved is a click, not a failed crop.  Now that the
        # rectangle is always live, saying "too small" on every stray press in
        # the after pane would be noise, and noise is how a real warning gets
        # ignored.
        if abs(x1 - x0) < 3 and abs(y1 - y0) < 3:
            self._refresh_crop()
            return
        ok = self.session.set_crop_rect(x0, y0, x1, y1, iw, ih)
        self._refresh_crop()
        self._set_status_extra("crop set" if ok else "crop too small, ignored")

    def _draw_crop_overlay(self, rx0, ry0, rx1, ry1, ox, oy, iw, ih):
        """Shade everything outside the crop rectangle on the after canvas.

        Stippled rather than alpha-blended: a Tk canvas item has no alpha
        channel, and an eight-digit colour is not a colour spec but a TclError.
        ``gray50`` is the dither that reads as a dimmed area at any zoom.
        """
        tag = "crop_overlay"
        x0, x1 = sorted((rx0, rx1))
        y0, y1 = sorted((ry0, ry1))
        shade = dict(fill="#000000", stipple="gray50", outline="", tags=tag)
        for sx0, sy0, sx1, sy1 in ((ox, oy, ox + iw, y0),
                                   (ox, y1, ox + iw, oy + ih),
                                   (ox, y0, x0, y1),
                                   (x1, y0, ox + iw, y1)):
            if sx1 > sx0 and sy1 > sy0:      # nothing outside an uncropped frame
                self.c_after.create_rectangle(sx0, sy0, sx1, sy1, **shade)
        self.c_after.create_rectangle(x0, y0, x1, y1, outline="#4da3ff",
                                      width=2, tags=tag)
        # Corner handles.  Drawn because a grab region nobody can see is a
        # feature nobody finds; sized to the radius `_grab_corner` accepts.
        for hx, hy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
            self.c_after.create_rectangle(hx - 5, hy - 5, hx + 5, hy + 5,
                                          fill="#4da3ff", outline="#0b1017",
                                          width=1, tags=tag)

    def _on_click_before(self, event):
        x = event.x - self._before_off[0]
        y = event.y - self._before_off[1]
        if getattr(self, "v_mark", None) is not None and self.v_mark.get():
            self._click_mark(x, y)
            return
        idx = self.session.pick_line(x, y, display_scale=self._before_scale)
        if idx is None:
            return
        self.session.toggle_line(idx)
        if self.session.mode == AUTO:
            self._sync_from_session()
        else:
            self._redraw()

    def _click_mark(self, x, y):
        """Two clicks make one vertical control line; a click on an existing one
        removes it.

        Removal shares the same gesture on purpose: the alternative is a
        modifier key nobody discovers, and a mark placed by mistake has to be as
        easy to take back as it was to make.
        """
        hit = self.session.pick_control_line(x, y, display_scale=self._before_scale)
        if hit is not None and self._pending_mark is None:
            self.session.remove_control_line(hit)
            self._sync_from_session()
            return
        if self._pending_mark is None:
            self._pending_mark = (x, y)
            self._set_status("marking a vertical: click the other end\n"
                             "(as far from the first point as the structure allows)")
            self._redraw()
            return
        x0, y0 = self._pending_mark
        self._pending_mark = None
        added = self.session.add_control_line(x0, y0, x, y,
                                              display_scale=self._before_scale)
        if added is None:
            self._set_status("too short to be trusted -- mark the full height of "
                             "the structure, not a few pixels of it")
            self._redraw()
            return
        self._sync_from_session()

    # -- drawing ---------------------------------------------------------
    def _schedule_redraw(self):
        if self._busy:
            return
        self._busy = True
        self.after(60, self._redraw)

    def _redraw(self):
        self._busy = False
        try:
            box_b = (self.c_before.winfo_width(), self.c_before.winfo_height())
            box_a = (self.c_after.winfo_width(), self.c_after.winfo_height())
            if min(box_b) < 20 or min(box_a) < 20:
                self.after(120, self._redraw)
                return
            before = self.session.render_before(max_edge=max(box_b), 
                                                show_lines=self.v_show_lines.get())
            # Un-cropped on purpose: `_draw_crop_persistent` shades what the
            # crop discards, so the picture keeps one size and one scale for
            # the whole session instead of leaping every time a corner moves.
            after = self.session.render_after(max_edge=max(box_a), apply_crop=False)
            ph_b, s_b = _to_photo(before, box_b)
            ph_a, _ = _to_photo(after, box_a)
            # scale from the *original* image to what is on screen
            self._before_scale = s_b * (before.shape[1] / self.session.w)
            self._before_off = ((box_b[0] - ph_b.width()) // 2,
                                (box_b[1] - ph_b.height()) // 2)
            self.c_before.delete("all")
            self.c_before.create_image(self._before_off[0], self._before_off[1],
                                       anchor="nw", image=ph_b)
            self._ph_b = ph_b
            self._draw_marks()
            aox = (box_a[0] - ph_a.width()) // 2
            aoy = (box_a[1] - ph_a.height()) // 2
            self._after_off = (aox, aoy)
            self.c_after.delete("all")
            self.c_after.create_image(aox, aoy, anchor="nw", image=ph_a)
            self._ph_a = ph_a
            self._draw_crop_persistent()
            self._set_status(self.session.status_text())
            self._update_slider_labels()
        except Exception:
            self._set_status("preview failed:\n" + traceback.format_exc(limit=2))

    def _draw_marks(self):
        """Vertical control lines, over the preview.

        Drawn by the canvas rather than burnt into the rendered image because
        they are interaction state, not detection: they have to appear the
        instant a click lands, without waiting for a re-render, and the pending
        first point has to be visible while it is still only half a line.
        """
        ox, oy = self._before_off
        active = self.session.control_active
        for x0, y0, x1, y1 in self.session.control_lines_for_display(self._before_scale):
            self.c_before.create_line(ox + x0, oy + y0, ox + x1, oy + y1,
                                      fill="#00e5ff" if active else "#ffb300",
                                      width=3, arrow="both", arrowshape=(9, 11, 4))
        pend = getattr(self, "_pending_mark", None)
        if pend is not None:
            px, py = ox + pend[0], oy + pend[1]
            self.c_before.create_oval(px - 6, py - 6, px + 6, py + 6,
                                       outline="#00e5ff", width=2)

    def _draw_crop_persistent(self):
        """Redraw the crop overlay from the session's stored fractions.

        An uncropped photograph draws the frame itself, so the four handles are
        always there to grab.  A crop tool that has to be switched on first is a
        crop tool people drag at and nothing happens -- and the drag is then lost
        silently, which is the one thing this project does not do.  Nothing is
        cropped until a handle actually moves: the full-frame rectangle trims
        every edge by zero.
        """
        self.c_after.delete("crop_overlay")
        rect = self.session.crop_rect or (0.0, 0.0, 1.0, 1.0)
        ox, oy = self._after_off
        iw, ih = self._ph_a.width(), self._ph_a.height()
        x0, y0, x1, y1 = rect
        self._draw_crop_overlay(ox + x0 * iw, oy + y0 * ih,
                                ox + x1 * iw, oy + y1 * ih,
                                ox, oy, iw, ih)

    def _set_status(self, text):
        self.status.delete("1.0", "end")
        self.status.insert("1.0", text)

    def _set_status_extra(self, text):
        self.status.insert("end", "\n" + text)

    # -- output ----------------------------------------------------------
    def _save(self):
        dst = self._target_path()
        # Asked once per photograph, because replacing an original is the one
        # action here nothing can undo -- and in a queue of thirty the checkbox
        # was ticked long before this particular picture came up.
        if (self.v_overwrite.get()
                and not messagebox.askyesno(
                    "Overwrite", f"Replace the original?\n\n{dst}", parent=self)):
            return
        try:
            self.session.save(dst)
        except Exception as exc:
            messagebox.showerror("Save", str(exc), parent=self)
            return
        if self.on_saved:
            self.on_saved(self.session.path, dst)
        self._fire_closed()
        self.destroy()

    def _keep(self):
        try:
            from .imageio import copy_through
            dst = self._target_path()
            if os.path.abspath(dst) != os.path.abspath(self.session.path):
                copy_through(self.session.path, dst)
        except Exception as exc:
            messagebox.showerror("Save", str(exc), parent=self)
            return
        self._fire_closed()
        self.destroy()


# ==========================================================================
# batch window
# ==========================================================================
class App(_ROOT_CLASS):
    def __init__(self, initial=None):
        super().__init__()
        self.title("Batch Perspective Correction")
        self.geometry("1120x760")
        self.minsize(880, 560)
        apply_theme(self)
        self._icon = _set_window_icon(self)
        self.queue = queue.Queue()
        self.worker = None
        self.stop_flag = threading.Event()
        self.results = {}
        # Loaded before the widgets, because some of them show a remembered
        # value as their initial state rather than being set afterwards.
        self._remembered = prefs.load()
        self._build()
        if self._remembered.get("output"):
            self.v_output.set(self._remembered["output"])
        self._refresh_items()          # opens on the drop stage, not the work one
        # a remembered path is offered, never forced: the selector still says off
        if initial:
            self._add(list(initial))
        self.after(120, self._pump)

    def _build(self):
        pad = dict(padx=6, pady=4)
        _brand_header(self)
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")

        self.v_output = tk.StringVar()
        self.items = []                       # files and/or folders, in order

        hint = ("Drop photographs or a folder"
                if HAVE_DND else
                "Click to add photographs or a folder")
        self.drop = tk.Label(top, text=hint, borderwidth=0, height=4,
                             background=INK["field"], foreground=INK["dim"],
                             cursor="hand2")
        self.drop.grid(row=0, column=0, columnspan=3, sticky="ew", **pad)
        # one drop, one photograph, straight into it -- see _add
        self.drop.bind("<Enter>", lambda e: self.drop.configure(foreground=INK["accent"]))
        self.drop.bind("<Leave>", lambda e: self.drop.configure(foreground=INK["dim"]))
        self.drop.bind("<Button-1>", lambda e: self._add_files())
        if HAVE_DND:
            self.drop.drop_target_register(DND_FILES)
            self.drop.dnd_bind("<<Drop>>", self._on_drop)

        row = ttk.Frame(top)
        row.grid(row=1, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Button(row, text="Images", command=self._add_files).pack(side="left")
        ttk.Button(row, text="Folder", command=self._add_folder).pack(side="left", padx=6)
        ttk.Button(row, text="Remove", command=self._remove_selected).pack(side="left")
        ttk.Button(row, text="Clear", command=self._clear).pack(side="left", padx=6)
        self.lbl_items = ttk.Label(row, text="nothing yet", style="Dim.TLabel")
        self.lbl_items.pack(side="left", padx=14)
        ttk.Label(row, text="double-click a row to open it",
                  style="Dim.TLabel").pack(side="right")

        # A visible, selectable list.  Without it "review one image" had to guess
        # which of several dropped photos was meant, and it guessed the first --
        # so dropping a second one and clicking review opened the first again.
        listrow = ttk.Frame(top)
        listrow.grid(row=2, column=0, columnspan=3, sticky="ew", **pad)
        self._w_listrow = listrow
        self.lst = tk.Listbox(listrow, height=4, activestyle="none",
                              exportselection=False, borderwidth=0,
                              highlightthickness=0, background=INK["field"],
                              foreground=INK["text"],
                              selectbackground=INK["line"],
                              selectforeground=INK["text"])
        self.lst.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(listrow, orient="vertical", command=self.lst.yview)
        sb.pack(side="right", fill="y")
        self.lst.configure(yscrollcommand=sb.set)
        self.lst.bind("<Double-1>", lambda e: self._review_single())

        self._w_out = [
            ttk.Label(top, text="Output", style="Head.TLabel"),
            ttk.Entry(top, textvariable=self.v_output, width=70),
            ttk.Button(top, text="Browse", command=self._pick_out)]
        self._w_out[0].grid(row=3, column=0, sticky="w", **pad)
        self._w_out[1].grid(row=3, column=1, sticky="ew", **pad)
        self._w_out[2].grid(row=3, column=2, **pad)
        top.columnconfigure(1, weight=1)

        opt = ttk.Frame(self, padding=(14, 4, 14, 8))
        opt.pack(fill="x")
        self._w_opt = opt
        self.v_strength = tk.DoubleVar(value=1.0)
        self.v_conf = tk.DoubleVar(value=Settings.min_confidence)
        self.v_maxpitch = tk.DoubleVar(value=Settings.max_pitch_deg)
        self.v_crop = tk.StringVar(value=Settings.crop)
        self.v_recursive = tk.BooleanVar(value=False)
        self.v_overwrite = tk.BooleanVar(value=False)
        self.v_review = tk.BooleanVar(value=True)
        self._spin(opt, 0, 0, "strength", self.v_strength, 0.0, 1.0, 0.05)
        self._spin(opt, 0, 3, "min confidence", self.v_conf, 0.0, 1.0, 0.05)
        self._spin(opt, 1, 0, "max pitch (deg)", self.v_maxpitch, 0.0, 45.0, 1.0)
        ttk.Label(opt, text="mask").grid(row=0, column=6, sticky="e", padx=4)
        self.v_mask = tk.StringVar(value=Settings.mask_mode)
        ttk.Combobox(opt, textvariable=self.v_mask, values=["off", "file", "birefnet"],
                     width=6, state="readonly").grid(row=0, column=7, sticky="w")
        self.v_maskpath = tk.StringVar(value="")
        ttk.Button(opt, text="mask source...", command=self._pick_mask_source
                   ).grid(row=0, column=8, sticky="w", padx=(6, 0))
        ttk.Label(opt, text="crop").grid(row=1, column=3, sticky="e", padx=4)
        ttk.Combobox(opt, textvariable=self.v_crop,
                     values=["auto", "aspect", "inside", "none"],
                     width=8, state="readonly").grid(row=1, column=4, sticky="w")
        ttk.Label(opt, text="detector").grid(row=1, column=6, sticky="e", padx=4)
        self.v_detector = tk.StringVar(value=Settings.detector)
        ttk.Combobox(opt, textvariable=self.v_detector, values=list(DETECTORS),
                     width=11, state="readonly").grid(row=1, column=7, sticky="w")
        # Generating the band a rotation opens up is off by default and says so
        # when it cannot run: a batch that quietly writes padded frames because
        # the backend was missing is the silent failure this tool avoids.
        ttk.Label(opt, text="fill gaps").grid(row=2, column=6, sticky="e", padx=4)
        self.v_fill = tk.StringVar(value=Settings.fill)
        fbox = ttk.Combobox(opt, textvariable=self.v_fill,
                            values=["none", "telea", "lama", "comfyui"],
                            width=11, state="readonly")
        fbox.grid(row=2, column=7, sticky="w")
        fbox.bind("<<ComboboxSelected>>", lambda e: self._check_fill())
        self.lbl_fill = ttk.Label(opt, text="", style="Dim.TLabel", wraplength=900,
                                  justify="left")
        self.lbl_fill.grid(row=3, column=0, columnspan=9, sticky="w", pady=(4, 0))

        # Where the ComfyUI generator lives, and whether it is actually there.
        # Without this the mode was selectable and unconfigurable: the address
        # and the workflow existed only as command-line flags, so choosing
        # "comfyui" in the window could only ever mean the default port and the
        # shipped workflow, and finding out it was wrong meant running a batch.
        self.comfy_row = ttk.Frame(opt)
        self.comfy_row.grid(row=4, column=0, columnspan=9, sticky="ew", pady=(4, 0))
        ttk.Label(self.comfy_row, text="ComfyUI").pack(side="left")
        # Host and port apart, because the port is the half that actually gets
        # changed -- a second instance, a tunnel, a container -- and hunting for
        # it inside a URL is how it gets mistyped.  One fact still lives in one
        # place: `_comfy_url` composes them and nothing stores the joined form.
        host, port = _split_url(self._remembered.get("comfy_url", Settings.comfy_url))
        self.v_comfy_host = tk.StringVar(value=host)
        self.v_comfy_port = tk.StringVar(value=port)
        ttk.Entry(self.comfy_row, textvariable=self.v_comfy_host,
                  width=20).pack(side="left", padx=(6, 2))
        ttk.Label(self.comfy_row, text=":").pack(side="left")
        ttk.Entry(self.comfy_row, textvariable=self.v_comfy_port,
                  width=6).pack(side="left", padx=(2, 6))
        self.lbl_comfy_state = tk.Label(self.comfy_row, text="not checked",
                                        background=INK["bg"], foreground=INK["dim"])
        self.lbl_comfy_state.pack(side="left", padx=(0, 8))
        # Editing the address invalidates the verdict.  A green "connected"
        # sitting beside a port nobody has asked yet is worse than no light at
        # all: it is an answer to a question that was not the one on screen.
        for var in (self.v_comfy_host, self.v_comfy_port):
            var.trace_add("write",
                          lambda *_: self._set_comfy_state("not checked", INK["dim"]))
        self.v_comfy_wf = tk.StringVar(value=self._remembered.get("comfy_workflow", ""))
        ttk.Button(self.comfy_row, text="workflow...",
                   command=self._pick_comfy_workflow).pack(side="left")
        self.lbl_comfy_wf = ttk.Label(self.comfy_row, text="", style="Dim.TLabel")
        self.lbl_comfy_wf.pack(side="left", padx=6)
        self.btn_comfy_test = ttk.Button(self.comfy_row, text="Test connection",
                                         command=self._test_comfy)
        self.btn_comfy_test.pack(side="left", padx=6)
        self._sync_comfy_workflow_label()
        ttk.Checkbutton(opt, text="subfolders", variable=self.v_recursive).grid(row=2, column=0, sticky="w")
        ttk.Checkbutton(opt, text="overwrite originals", variable=self.v_overwrite).grid(row=2, column=1, sticky="w")
        ttk.Checkbutton(opt, text="offer manual review for unclear images",
                        variable=self.v_review).grid(row=2, column=2, columnspan=3, sticky="w")

        bar = ttk.Frame(self, padding=8)
        bar.pack(fill="x")
        self._w_bar = bar
        self.btn_run = ttk.Button(bar, text="Start", command=self._start,
                                  style="Accent.TButton")
        self.btn_run.pack(side="left")
        self.btn_stop = ttk.Button(bar, text="Stop", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=6)
        # The other way through a folder: no unattended writing at all, one
        # window per photograph, each one confirmed by hand.
        ttk.Button(bar, text="Review each...",
                   command=self._review_each).pack(side="left", padx=(6, 0))
        self.progress = ttk.Progressbar(bar, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=10)
        self.lbl_count = ttk.Label(bar, text="", style="Value.TLabel")
        self.lbl_count.pack(side="right")

        cols = ("status", "file", "roll", "pitch", "conf", "note")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", selectmode="browse")
        for c, w in zip(cols, (80, 320, 70, 70, 60, 380)):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.tree.bind("<Double-1>", lambda e: self._review_selected())
        for status, colour in STATUS_COLOUR.items():
            self.tree.tag_configure(status, foreground=colour)

    def _spin(self, parent, r, c, label, var, lo, hi, step):
        ttk.Label(parent, text=label).grid(row=r, column=c, sticky="e", padx=4)
        ttk.Spinbox(parent, textvariable=var, from_=lo, to=hi, increment=step,
                    width=7).grid(row=r, column=c + 1, sticky="w")

    # -- settings --------------------------------------------------------
    def _settings(self):
        s = Settings()
        s.pitch_strength = s.roll_strength = float(self.v_strength.get())
        s.min_confidence = float(self.v_conf.get())
        s.max_pitch_deg = float(self.v_maxpitch.get())
        s.crop = self.v_crop.get()
        s.detector = self.v_detector.get()
        s.fill = self.v_fill.get()
        s.comfy_url = self._comfy_url()
        s.comfy_workflow = self.v_comfy_wf.get()
        s.mask_mode = self.v_mask.get()
        path = self.v_maskpath.get() or self._remembered.get(
            "birefnet_model" if self.v_mask.get() == "birefnet" else "mask_file", "")
        if s.mask_mode == "file":
            s.mask_file = path
        elif s.mask_mode == "birefnet":
            s.birefnet_model = path
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
        from . import inpaint as FILL
        s = self._settings()
        text = FILL.describe(mode, s)
        if not FILL.available(mode, s):
            text = "fill will FAIL on every image -- " + text
        self.lbl_fill.configure(text=text)

    def _sync_comfy_workflow_label(self):
        wf = self.v_comfy_wf.get()
        self.lbl_comfy_wf.configure(
            text=os.path.basename(wf) if wf else "shipped workflow")

    def _pick_comfy_workflow(self):
        """The API export, not the editor export.

        ``inpaint.load_workflow`` tells the two apart and says which one it got,
        because posting an editor export to ``/prompt`` is the mistake everyone
        makes once and the error it produces on its own is unreadable.
        """
        p = filedialog.askopenfilename(
            title="ComfyUI workflow (API format)", parent=self,
            filetypes=[("ComfyUI API workflow", "*.json"), ("All files", "*.*")])
        if not p:
            return
        self.v_comfy_wf.set(p)
        self._sync_comfy_workflow_label()
        prefs.save(comfy_workflow=p)       # an address, not a correction setting
        # The workflow is half of what "connected" means -- a reachable server
        # with an unusable graph is not a working fill -- so the verdict is
        # stale the moment it changes.
        self._set_comfy_state("not checked", INK["dim"])
        self._check_fill()

    def _comfy_url(self):
        return _join_url(self.v_comfy_host.get(), self.v_comfy_port.get())

    def _set_comfy_state(self, text, colour):
        self.lbl_comfy_state.configure(text=text, foreground=colour)

    def _test_comfy(self):
        """Ask the server whether it is there, off the UI thread.

        ``describe`` does two network round trips with a three second timeout
        each, and doing that inline freezes the window mid-click -- which reads
        as a crash, not as a slow server.  The button says it is working and
        comes back either way; a check that cannot fail visibly is not a check.
        """
        self.btn_comfy_test.configure(state="disabled")
        self._set_comfy_state("checking...", INK["dim"])
        self.lbl_fill.configure(text="ComfyUI: asking...")
        settings = self._settings().replace(fill="comfyui")

        def work():
            from . import inpaint as FILL
            try:
                ok = FILL.available("comfyui", settings)
                text = FILL.describe("comfyui", settings)
            except Exception as exc:                     # never take the window down
                ok, text = False, f"comfyui check failed: {exc}"
            # Through the queue the batch run already uses, not `after` from
            # here: Tk is not thread-safe, and registering a callback from a
            # worker raises "main thread is not in main loop" outright.
            self.queue.put(("comfy", (ok, text)))

        threading.Thread(target=work, daemon=True).start()

    def _comfy_result(self, ok, text):
        self.btn_comfy_test.configure(state="normal")
        self._set_comfy_state("connected" if ok else "disconnected",
                              INK["ok"] if ok else INK["err"])
        self.lbl_fill.configure(
            text=("ComfyUI reachable -- " if ok else "ComfyUI NOT usable -- ") + text)
        if ok:
            prefs.save(comfy_url=self._comfy_url())

    def _pick_mask_source(self):
        """One button for both, because the batch panel had a mask selector with
        no way to say *which* mask -- so choosing 'file' made every image fail
        with what looked like an internal error."""
        mode = self.v_mask.get()
        if mode == "birefnet":
            p = filedialog.askopenfilename(
                title="BiRefNet weights",
                filetypes=[("BiRefNet weights", "*.safetensors *.pth *.pt"),
                           ("all files", "*.*")])
            if p:
                from . import birefnet as BN
                self.v_maskpath.set(p)
                prefs.save(birefnet_model=p)
                messagebox.showinfo("BiRefNet model", BN.describe(p))
        elif mode == "file":
            d = filedialog.askdirectory(title="folder of mask images (one per photo)")
            if d:
                self.v_maskpath.set(d)
                prefs.save(mask_file=d)
        else:
            messagebox.showinfo("Mask", "set the mask selector to 'file' or 'birefnet' first")

    def _add(self, paths):
        added = 0
        first_new = len(self.items)
        was_empty = not self.items
        for p in paths:
            p = os.path.abspath(p)
            if not os.path.exists(p) or p in self.items:
                continue
            if os.path.isdir(p) or os.path.splitext(p)[1].lower() in READABLE:
                self.items.append(p)
                added += 1
        if added and hasattr(self, "lst"):
            self.lst.selection_clear(0, "end")
        self._refresh_items()
        if added and hasattr(self, "lst"):
            self.lst.selection_clear(0, "end")
            self.lst.selection_set(first_new)
            self.lst.see(first_new)
        # One photograph dropped on an empty window means "work on this one".
        # Making that cost a second click, on a list of one, was the window
        # asking a question it already had the answer to.  A folder, or several
        # files, still lands in the list: there the question is real.
        if was_empty and added == 1 and os.path.isfile(self.items[0]):
            self.after(60, self._review_single)
        return added

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

    def _clear(self):
        self.items = []
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
            self.drop.configure(text="nothing usable in that drop")
            self.after(1800, self._refresh_items)

    def _remove_selected(self):
        for i in reversed(self.lst.curselection()):
            del self.items[i]
        self._refresh_items()

    def _set_stage(self):
        """Two stages in one window: open, then work.

        With nothing loaded there is exactly one thing to do, and a screen of
        sliders, an empty results table and a disabled Start button do not help
        anyone do it -- they bury the drop target, which is the only control
        that matters yet. So the empty window is the drop target, at four times
        the height, plus the buttons that do the same thing for anyone who would
        rather browse.

        Everything else appears the moment there is something to work on, and
        goes away again on "Clear". Nothing is destroyed and rebuilt: the
        widgets keep their state, so a folder chosen, a detector picked or a
        ComfyUI address typed survives emptying the list.
        """
        loaded = bool(self.items)
        self.drop.configure(height=14 if not loaded else 3)
        for w in (self._w_listrow, *self._w_out):
            (w.grid() if loaded else w.grid_remove())
        # Re-packed in order, because `pack` appends and the three of them must
        # not end up above the frame they belong under.
        for w in (self._w_opt, self._w_bar, self.tree):
            w.pack_forget()
        if loaded:
            self._w_opt.pack(fill="x")
            self._w_bar.pack(fill="x")
            self.tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _refresh_items(self):
        keep = list(self.lst.curselection()) if hasattr(self, "lst") else []
        if hasattr(self, "lst"):
            self.lst.delete(0, "end")
            for p in self.items:
                self.lst.insert("end", ("[folder]  " if os.path.isdir(p) else "")
                                + os.path.basename(p))
            if self.items:
                # select what was just added, which is what the user is looking at
                idx = keep[0] if keep and keep[0] < len(self.items) else len(self.items) - 1
                self.lst.selection_clear(0, "end")
                self.lst.selection_set(idx)
                self.lst.see(idx)
        n_files = sum(1 for p in self.items if os.path.isfile(p))
        n_dirs = sum(1 for p in self.items if os.path.isdir(p))
        if not self.items:
            self.lbl_items.configure(text="nothing selected")
        else:
            bits = []
            if n_files:
                bits.append(f"{n_files} image" + ("s" if n_files != 1 else ""))
            if n_dirs:
                bits.append(f"{n_dirs} folder" + ("s" if n_dirs != 1 else ""))
            self.lbl_items.configure(text=" + ".join(bits))
        hint = ("drop photos or a folder here" if HAVE_DND
                else "click to add photos or a folder   "
                     "(pip install tkinterdnd2 for drag and drop)")
        self._set_stage()
        if self.items:
            hint += "\n" + os.path.basename(self.items[-1]) + \
                    (f"  (+{len(self.items) - 1} more)" if len(self.items) > 1 else "")
        self.drop.configure(text=hint)

    def _review_single(self):
        """Open the *selected* image in the review window.

        The batch list is the normal path, but a single photograph being checked
        by hand should not need a run first.  It reviews what is selected in the
        list -- picking the first entry regardless, as this used to, meant that
        dropping a second photo and clicking review opened the first one again."""
        if not self.items:
            messagebox.showinfo("Review", "add an image first")
            return
        sel = self.lst.curselection()
        item = self.items[sel[0]] if sel else self.items[-1]
        if os.path.isdir(item):
            inside = [f for f in self._expand(item) if os.path.isfile(f)]
            if not inside:
                messagebox.showinfo("Review", "that folder has no readable images")
                return
            item = inside[0]
        ReviewWindow(self, item, self._settings(), self._dest_corr(item),
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

    def _review_each(self):
        """Walk the whole selection, one review window at a time.

        The batch decides and writes; this asks. Every photograph is opened,
        corrected as it comes, and becomes a file only when Save is pressed --
        so the run cannot produce a single output nobody looked at.

        Chained rather than looped: Tk has one event loop, and a `for` around a
        modal window either blocks it or opens thirty windows at once. Each
        window's `on_closed` opens the next, whichever way it was closed, so
        closing one with the X advances the queue instead of stalling it.
        """
        files = self._files()
        if not files:
            messagebox.showinfo("Review", "add some images or a folder first")
            return
        self._review_queue = list(files)
        self._review_total = len(files)
        self._open_next_review()

    def _open_next_review(self):
        queue_left = getattr(self, "_review_queue", [])
        if not queue_left:
            if getattr(self, "_review_total", 0):
                self.lbl_count.configure(
                    text=f"reviewed {self._review_total} file(s); "
                         f"{len(self.items)} left in the list")
                self._review_total = 0
            return
        src = queue_left.pop(0)
        done = self._review_total - len(queue_left)
        ReviewWindow(self, src, self._settings(), self._dest_corr(src),
                     overwrite=self.v_overwrite.get(),
                     on_saved=self._forget_saved,
                     # Deferred: `on_closed` fires while the old window is being
                     # destroyed, and building a Toplevel inside that teardown is
                     # asking for a half-dead parent.
                     on_closed=lambda: self.after(50, self._open_next_review),
                     position=f"[{done}/{self._review_total}]")

    def _pick_out(self):
        d = filedialog.askdirectory(title="output folder")
        if d:
            self.v_output.set(d)
            prefs.save(output=d)

    # -- run -------------------------------------------------------------
    def _expand(self, item):
        """One selection entry -> the photographs it stands for."""
        if os.path.isfile(item):
            return [item]
        if self.v_recursive.get():
            return [os.path.join(b, n) for b, _, names in os.walk(item)
                    for n in sorted(names)
                    if os.path.splitext(n)[1].lower() in READABLE]
        return [os.path.join(item, n) for n in sorted(os.listdir(item))
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

    def _start(self):
        files = self._files()
        if not files:
            messagebox.showinfo("Batch", "add some images or a folder first")
            return
        if self.v_overwrite.get() and not messagebox.askyesno(
                "Overwrite", f"Replace {len(files)} original file(s)?"):
            return
        self.tree.delete(*self.tree.get_children())
        self.results.clear()
        self.progress.configure(maximum=len(files), value=0)
        self.stop_flag.clear()
        self.btn_run.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        settings = self._settings()
        self.worker = threading.Thread(target=self._run, args=(files, settings), daemon=True)
        self.worker.start()

    def _run(self, files, settings):
        for i, src in enumerate(files, 1):
            if self.stop_flag.is_set():
                self.queue.put(("done", "stopped"))
                return
            try:
                r = process(src, self._dest(src), settings)
            except Exception as exc:                  # never let one file kill the run
                self.queue.put(("error", (src, str(exc))))
                continue
            self.queue.put(("row", (i, r)))
        self.queue.put(("done", "finished"))

    def _stop(self):
        self.stop_flag.set()

    def _pump(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "row":
                    i, r = payload
                    self._add_row(r)
                    self.progress.configure(value=i)
                elif kind == "comfy":
                    self._comfy_result(*payload)
                elif kind == "error":
                    src, msg = payload
                    self.tree.insert("", "end", values=(ERROR, os.path.basename(src),
                                                        "", "", "", msg), tags=(ERROR,))
                elif kind == "done":
                    self.btn_run.configure(state="normal")
                    self.btn_stop.configure(state="disabled")
                    n_skip = sum(1 for r in self.results.values() if r.status == SKIPPED)
                    self.lbl_count.configure(text=f"{payload}: {len(self.results)} file(s)")
                    if n_skip and self.v_review.get():
                        messagebox.showinfo(
                            "Manual review",
                            f"{n_skip} image(s) were left unchanged because the detection "
                            f"was not clear enough.\n\nDouble-click any SKIPPED row to "
                            f"correct it by hand.")
        except queue.Empty:
            pass
        self.after(120, self._pump)

    def _add_row(self, r):
        iid = self.tree.insert("", "end", tags=(r.status,), values=(
            r.status, os.path.basename(r.src),
            f"{r.roll_deg:+.2f}" if r.status == OK else "",
            f"{r.pitch_deg:+.2f}" if r.status == OK else "",
            f"{r.confidence:.2f}",
            r.reason if r.status != OK else
            f"f={r.focal_35mm:.0f}mm ({r.focal_source}), keeps {r.coverage * 100:.0f}%"))
        self.results[iid] = r
        self.tree.see(iid)

    def _review_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Review", "select a row first")
            return
        r = self.results.get(sel[0])
        if r is None:
            return
        ReviewWindow(self, r.src, self._settings(), self._dest_corr(r.src),
                     overwrite=self.v_overwrite.get(),
                     on_saved=lambda s, d: (self._mark_manual(sel[0]),
                                            self._forget_saved(s, d)))

    def _mark_manual(self, iid):
        vals = list(self.tree.item(iid, "values"))
        vals[0] = OK
        vals[5] = "corrected manually"
        self.tree.item(iid, values=vals, tags=(OK,))


def run(initial=None) -> int:
    App(initial).mainloop()
    return 0
