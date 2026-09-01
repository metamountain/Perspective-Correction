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

import cv2
import numpy as np
from PIL import Image, ImageTk

from . import prefs
from .config import Settings
from .imageio import READABLE
from .pipeline import ERROR, OK, SKIPPED, process
from .review import AUTO, MANUAL, ReviewSession

STATUS_COLOUR = {OK: "#1a7f37", SKIPPED: "#9a6700", ERROR: "#b62324"}


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


# ==========================================================================
# review window
# ==========================================================================
class ReviewWindow(tk.Toplevel):
    def __init__(self, master, path, settings, dest_path, on_saved=None):
        super().__init__(master)
        self.title(f"Review - {os.path.basename(path)}")
        self.geometry("1280x820")
        self.settings = settings
        self.dest_path = dest_path
        self.on_saved = on_saved
        self._busy = False
        self._before_scale = 1.0

        try:
            self.session = ReviewSession(path, settings)
        except Exception as exc:
            messagebox.showerror("Review", f"cannot open image:\n{exc}", parent=master)
            self.destroy()
            return

        self._build()
        self.v_alpha.set(self.session.mask_alpha)
        self.after(60, self._sync_from_session)

    # -- layout ----------------------------------------------------------
    def _build(self):
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

        self.c_before = tk.Canvas(panes, bg="#1e1e1e", highlightthickness=0)
        self.c_before.grid(row=1, column=0, sticky="nsew", padx=(0, 3))
        self.c_after = tk.Canvas(panes, bg="#1e1e1e", highlightthickness=0)
        self.c_after.grid(row=1, column=1, sticky="nsew", padx=(3, 0))
        self._pending_mark = None
        self.c_before.bind("<Button-1>", self._on_click_before)
        for c in (self.c_before, self.c_after):
            c.bind("<Configure>", lambda e: self._schedule_redraw())

        self.status = tk.Text(top, height=4, wrap="word", relief="flat",
                              background="#f4f4f4")
        self.status.pack(fill="x", pady=(6, 4))
        self.status.configure(state="disabled")

        ctl = ttk.LabelFrame(top, text="manual correction", padding=6)
        ctl.pack(fill="x")
        self.v_roll = tk.DoubleVar(value=0.0)
        self.v_pitch = tk.DoubleVar(value=0.0)
        self.v_focal = tk.DoubleVar(value=28.0)
        self._slider(ctl, 0, "roll (level)", self.v_roll, -20, 20, "deg")
        self._slider(ctl, 1, "pitch (verticals)", self.v_pitch, -30, 30, "deg")
        self._slider(ctl, 2, "focal length", self.v_focal, 8, 200, "mm eq")

        msk = ttk.LabelFrame(top, text="region mask", padding=6)
        msk.pack(fill="x", pady=(6, 0))
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

        btns = ttk.Frame(top, padding=(0, 8))
        btns.pack(fill="x")
        self.v_mark = tk.BooleanVar(value=False)
        ttk.Checkbutton(btns, text="mark a vertical (2 clicks)",
                        variable=self.v_mark, command=self._on_mark_toggle
                        ).pack(side="left")
        ttk.Button(btns, text="clear marks",
                   command=self._clear_marks).pack(side="left", padx=(6, 12))
        ttk.Button(btns, text="strike out slanted lines (>18 deg)",
                   command=self._strike_slanted).pack(side="left")
        ttk.Button(btns, text="reset to automatic",
                   command=self._reset).pack(side="left", padx=6)
        ttk.Checkbutton(btns, text="show detected lines", command=self._schedule_redraw,
                        variable=self._mk_show()).pack(side="left", padx=12)
        ttk.Checkbutton(btns, text="show mask", command=self._toggle_mask,
                        variable=self._mk_mask()).pack(side="left")
        ttk.Button(btns, text="keep original", command=self._keep).pack(side="right")
        ttk.Button(btns, text="save correction", command=self._save).pack(side="right", padx=6)

    def _mk_show(self):
        self.v_show_lines = tk.BooleanVar(value=True)
        return self.v_show_lines

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
            after = self.session.render_after(max_edge=max(box_a))
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
            self.c_after.delete("all")
            self.c_after.create_image((box_a[0] - ph_a.width()) // 2,
                                      (box_a[1] - ph_a.height()) // 2,
                                      anchor="nw", image=ph_a)
            self._ph_a = ph_a
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

    def _set_status(self, text):
        self.status.configure(state="normal")
        self.status.delete("1.0", "end")
        self.status.insert("1.0", text)
        self.status.configure(state="disabled")

    def _set_status_extra(self, text):
        self.status.configure(state="normal")
        self.status.insert("end", "\n" + text)
        self.status.configure(state="disabled")

    # -- output ----------------------------------------------------------
    def _save(self):
        try:
            self.session.save(self.dest_path)
        except Exception as exc:
            messagebox.showerror("Save", str(exc), parent=self)
            return
        if self.on_saved:
            self.on_saved(self.session.path, self.dest_path)
        self.destroy()

    def _keep(self):
        try:
            from .imageio import copy_through
            if os.path.abspath(self.dest_path) != os.path.abspath(self.session.path):
                copy_through(self.session.path, self.dest_path)
        except Exception as exc:
            messagebox.showerror("Save", str(exc), parent=self)
            return
        self.destroy()


# ==========================================================================
# batch window
# ==========================================================================
class App(_ROOT_CLASS):
    def __init__(self, initial=None):
        super().__init__()
        self.title("Batch Perspective Correction")
        self.geometry("1080x720")
        self.queue = queue.Queue()
        self.worker = None
        self.stop_flag = threading.Event()
        self.results = {}
        self._build()
        stored = prefs.load()
        if stored.get("output"):
            self.v_output.set(stored["output"])
        # a remembered path is offered, never forced: the selector still says off
        self._remembered = stored
        if initial:
            self._add(list(initial))
        self.after(120, self._pump)

    def _build(self):
        pad = dict(padx=6, pady=4)
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")

        self.v_output = tk.StringVar()
        self.items = []                       # files and/or folders, in order

        hint = ("drop photos or a folder here"
                if HAVE_DND else
                "click to add photos or a folder   (pip install tkinterdnd2 for drag and drop)")
        self.drop = tk.Label(top, text=hint, relief="ridge", borderwidth=2,
                             background="#eef1f4", foreground="#334", height=3,
                             cursor="hand2")
        self.drop.grid(row=0, column=0, columnspan=3, sticky="ew", **pad)
        self.drop.bind("<Button-1>", lambda e: self._add_files())
        if HAVE_DND:
            self.drop.drop_target_register(DND_FILES)
            self.drop.dnd_bind("<<Drop>>", self._on_drop)

        row = ttk.Frame(top)
        row.grid(row=1, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Button(row, text="add images...", command=self._add_files).pack(side="left")
        ttk.Button(row, text="add folder...", command=self._add_folder).pack(side="left", padx=6)
        ttk.Button(row, text="remove", command=self._remove_selected).pack(side="left")
        ttk.Button(row, text="clear", command=self._clear).pack(side="left", padx=6)
        self.lbl_items = ttk.Label(row, text="nothing selected")
        self.lbl_items.pack(side="left", padx=12)
        ttk.Button(row, text="review selected image...",
                   command=self._review_single).pack(side="right")

        # A visible, selectable list.  Without it "review one image" had to guess
        # which of several dropped photos was meant, and it guessed the first --
        # so dropping a second one and clicking review opened the first again.
        listrow = ttk.Frame(top)
        listrow.grid(row=2, column=0, columnspan=3, sticky="ew", **pad)
        self.lst = tk.Listbox(listrow, height=4, activestyle="dotbox",
                              exportselection=False)
        self.lst.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(listrow, orient="vertical", command=self.lst.yview)
        sb.pack(side="right", fill="y")
        self.lst.configure(yscrollcommand=sb.set)
        self.lst.bind("<Double-1>", lambda e: self._review_single())

        ttk.Label(top, text="output folder").grid(row=3, column=0, sticky="w", **pad)
        ttk.Entry(top, textvariable=self.v_output, width=70).grid(row=3, column=1, sticky="ew", **pad)
        ttk.Button(top, text="browse...", command=self._pick_out).grid(row=3, column=2, **pad)
        top.columnconfigure(1, weight=1)

        opt = ttk.LabelFrame(self, text="settings", padding=8)
        opt.pack(fill="x", padx=8)
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
        ttk.Combobox(opt, textvariable=self.v_crop, values=["aspect", "inside", "none"],
                     width=8, state="readonly").grid(row=1, column=4, sticky="w")
        ttk.Checkbutton(opt, text="subfolders", variable=self.v_recursive).grid(row=2, column=0, sticky="w")
        ttk.Checkbutton(opt, text="overwrite originals", variable=self.v_overwrite).grid(row=2, column=1, sticky="w")
        ttk.Checkbutton(opt, text="offer manual review for unclear images",
                        variable=self.v_review).grid(row=2, column=2, columnspan=3, sticky="w")

        bar = ttk.Frame(self, padding=8)
        bar.pack(fill="x")
        self.btn_run = ttk.Button(bar, text="start", command=self._start)
        self.btn_run.pack(side="left")
        self.btn_stop = ttk.Button(bar, text="stop", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=6)
        ttk.Button(bar, text="review selected...", command=self._review_selected).pack(side="left", padx=6)
        self.progress = ttk.Progressbar(bar, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=10)
        self.lbl_count = ttk.Label(bar, text="")
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
        s.mask_mode = self.v_mask.get()
        path = self.v_maskpath.get() or self._remembered.get(
            "birefnet_model" if self.v_mask.get() == "birefnet" else "mask_file", "")
        if s.mask_mode == "file":
            s.mask_file = path
        elif s.mask_mode == "birefnet":
            s.birefnet_model = path
        return s

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
        ReviewWindow(self, item, self._settings(), self._dest(item))

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
        ReviewWindow(self, r.src, self._settings(), self._dest(r.src),
                     on_saved=lambda s, d: self._mark_manual(sel[0]))

    def _mark_manual(self, iid):
        vals = list(self.tree.item(iid, "values"))
        vals[0] = OK
        vals[5] = "corrected manually"
        self.tree.item(iid, values=vals, tags=(OK,))


def run(initial=None) -> int:
    App(initial).mainloop()
    return 0
