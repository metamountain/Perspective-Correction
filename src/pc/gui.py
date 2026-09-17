"""Tkinter batch window with a manual review mode.

Two screens.  The *batch* screen runs a folder and logs OK / SKIPPED / ERROR.
The *review* screen opens one image and lets the decision be overridden by hand:
sliders for roll, pitch and focal length, and a clickable overlay for striking
out the lines the detector should not have trusted.

Anything that is not toolkit plumbing lives in :mod:`pc.review`, which has no
Tkinter dependency and is covered by the test suite; this file is the shell.

Tkinter ships with the python.org installer on Windows, which is the target
platform.  On Linux it may need a separate package (``python3-tk``); the CLI
works without it.
"""
from __future__ import annotations

import logging
import math
import os
import queue
import threading
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

import cv2
import numpy as np
from PIL import Image, ImageTk

from . import __version__
from . import layout
from . import masks as MK
from . import model as M
from . import warp as W
from . import prefs
from .config import Settings
from .imageio import READABLE
from .pipeline import ERROR, OK, SKIPPED, process
from .inpaint import join_url as _join_url, split_url as _split_url
from . import sam2seg
from .review import AUTO, MANUAL, ReviewSession, darken_outside_crop

QUEUED = "queued"
STATUS_COLOUR = {OK: "#5ac37f", SKIPPED: "#e0b24c", ERROR: "#ef6b6b", QUEUED: "#8899aa"}

# Both windows offer the same list, and it has to match cli.py's --detector
# choices; a name only one of the two knows about is a bug report waiting to
# happen.  mlsd/hybrid/union need a TFLite runtime and the deep-* three need
# torch and a DeepLSD checkout, so several of these can fail to load -- which
# is why both windows report the failure rather than falling back.
DETECTORS = ("lsd", "fld", "mlsd", "hybrid", "union",
             "deeplsd", "deep-hybrid", "deep-union")

RULER_MARGIN = 20
GUIDE_GREY = "#9aa0a8"
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
    "cross":   "#0b0d10",   # the review panel's dark cross and border
    "line":    "#2b2f36",   # hairlines, borders
    "text":    "#e6e8ec",
    "dim":     "#8b929c",   # secondary text
    "accent":  "#4da3ff",
    "ok":      "#5ac37f",
    "warn":    "#e0b24c",
    "err":     "#ef6b6b",
}

# ---------------------------------------------------------------------------
# Theme palettes.  Each entry overrides the INK keys; fonts are optional and
# fall back to the system defaults when absent.  The default (Minimal Black)
# is INK itself -- switching to it simply restores the original palette.
# ---------------------------------------------------------------------------
THEMES = {
    # --- Minimal Black (original, untouched) ---
    "Minimal Black": {
        "bg": "#16181c", "panel": "#1d2025", "field": "#101216",
        "cross": "#0b0d10", "line": "#2b2f36",
        "text": "#e6e8ec", "dim": "#8b929c",
        "accent": "#4da3ff", "ok": "#5ac37f", "warn": "#e0b24c", "err": "#ef6b6b",
    },
    # --- C64: VIC-II PAL palette.  Blue (#0000AA) bg, Light Blue (#0088FF)
    #     border/panels -- the classic blue-screen look. ---
    "C64": {
        "bg": "#0000AA", "panel": "#0088FF", "field": "#000055",
        "cross": "#000033", "line": "#0088FF",
        "text": "#FFFFFF", "dim": "#8888FF",
        "accent": "#00E5D0", "ok": "#66CC66", "warn": "#FFE554", "err": "#E3241B",
        "ui_font": "Courier New", "mono_font": "Courier New",
    },
    # --- Amiga 500: Workbench 2.0-3.1 (3D Grey & Blue).  Bevel gray bg,
    #     dark-blue title bar accent, black text, white light edges. ---
    "Amiga 500": {
        "bg": "#AAAAAA", "panel": "#B4B4B4", "field": "#9A9A9A",
        "cross": "#808080", "line": "#FFFFFF",
        "text": "#000000", "dim": "#555555",
        "accent": "#0055BB", "ok": "#008800", "warn": "#886600", "err": "#CC0000",
        "ui_font": "Verdana", "mono_font": "Consolas",
    },
    # --- Light: all-light, no dark contrast.  Soft off-white bg so the white
    #     panels read as raised; cross/border in light gray (not black). ---
    "Light": {
        "bg": "#eef0f2", "panel": "#ffffff", "field": "#f6f7f8",
        "cross": "#dfe2e5", "line": "#cdd1d6",
        "text": "#6b7a86", "dim": "#9aa5ae",
        "accent": "#6fa8d6", "ok": "#7dc49a", "warn": "#d4ad5e", "err": "#cf8a82",
    },
    # --- Phosphor: the CRT after the tube has warmed up.  It began as pure
    #     black with a #55ff55 hover, which is accurate and shouts; this is the
    #     same glow left to settle.  Every accent is a pastel -- mint, apricot,
    #     dusty rose -- and each is faint on purpose, because they sit around a
    #     photograph and a loud chrome shifts every tone in it.  The one thing
    #     no other palette has is `grad`: the two picture wells get a ramp from
    #     slate-indigo down to teal ink instead of a flat fill, which is where
    #     the phosphor is still visible.  Consolas keeps the terminal in it. ---
    "Phosphor": {
        "bg": "#151b22", "panel": "#1c242d", "field": "#111820",
        "cross": "#0b1016", "line": "#2a3642",
        "text": "#e2eef0", "dim": "#8ba1a7",
        "accent": "#9fd6cb", "ok": "#b6e0c2", "warn": "#efd9ab", "err": "#eeb3b3",
        "ui_font": "Segoe UI", "mono_font": "Consolas",
        "grad": ("#27323f", "#0e151a", "#9fd6cb"),
    },
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


def _shorten_middle(name, limit=28):
    """Shorten a filename by cutting its middle, never its end.

    The tail is what identifies a file -- the extension, the ``_corr`` suffix that
    tells you where Save will write -- so it always survives; only the middle is
    elided.  A name that already fits is returned untouched.
    """
    if len(name) <= limit:
        return name
    head = (limit - 1) // 2
    tail = limit - head - 1          # the ellipsis occupies one slot
    return f"{name[:head]}\u2026{name[-tail:]}"


def apply_theme(root, palette=None):
    """Apply a colour palette to all ttk styles.  Returns ``(ui_family, mono_family)``."""
    p = palette or INK
    ui_override = p.get("ui_font")
    mono_override = p.get("mono_font")
    if ui_override:
        ui = _pick_family(root, (ui_override, *tuple(_UI_FAMILIES)), "TkDefaultFont")
    else:
        ui = _pick_family(root, _UI_FAMILIES, "TkDefaultFont")
    if mono_override:
        mono = _pick_family(root, (mono_override, *tuple(_MONO_FAMILIES)), "TkFixedFont")
    else:
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

    root.configure(background=p["bg"])
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except Exception:
        pass
    st.configure(".", background=p["bg"], foreground=p["text"],
                 fieldbackground=p["field"], bordercolor=p["line"],
                 lightcolor=p["panel"], darkcolor=p["panel"],
                 focuscolor=p["accent"], troughcolor=p["field"],
                 insertcolor=p["text"], font=(ui, 10))
    st.configure("TFrame", background=p["bg"])
    st.configure("Panel.TFrame", background=p["panel"])
    st.configure("TLabel", background=p["bg"], foreground=p["text"])
    st.configure("Dim.TLabel", foreground=p["dim"])
    st.configure("Head.TLabel", foreground=p["dim"], font=(ui, 9))
    st.configure("Value.TLabel", foreground=p["text"], font=(mono, 10))
    st.configure("Title.TLabel", foreground=p["text"], font=(ui, 16))

    st.configure("TButton", background=p["panel"], foreground=p["text"],
                 borderwidth=0, focusthickness=0, padding=(16, 9))
    st.map("TButton",
           background=[("pressed", p["line"]), ("active", p["line"])],
           foreground=[("disabled", p["dim"])])
    st.configure("Accent.TButton", background=p["accent"], foreground="#0b1017",
                 padding=(18, 10))
    st.map("Accent.TButton", background=[("active", p["accent"]),
                                         ("disabled", p["line"])])

    st.configure("TEntry", padding=7, borderwidth=0)
    st.configure("TCombobox", padding=7, borderwidth=0, arrowcolor=p["dim"])
    st.map("TCombobox", fieldbackground=[("readonly", p["field"])],
           foreground=[("readonly", p["text"])])
    st.configure("TCheckbutton", background=p["bg"], foreground=p["text"],
                 padding=6, indicatorwidth=24, indicatorheight=24)
    st.map("TCheckbutton", background=[("active", p["bg"])])
    st.configure("TScale", background=p["bg"], troughcolor=p["field"],
                 sliderlength=20, thickness=10)
    st.map("TScale", background=[("active", p["bg"])])
    st.configure("TProgressbar", background=p["accent"], troughcolor=p["field"],
                 borderwidth=0, thickness=6)
    st.configure("Treeview", background=p["field"], fieldbackground=p["field"],
                 foreground=p["text"], borderwidth=0, rowheight=28)
    st.configure("Treeview.Heading", background=p["bg"], foreground=p["dim"],
                 borderwidth=0, font=(ui, 9))
    st.map("Treeview", background=[("selected", p["line"])],
           foreground=[("selected", p["text"])])
    st.configure("TSpinbox", arrowcolor=p["dim"], borderwidth=0, padding=6)
    st.configure("TLabelframe", background=p["bg"], bordercolor=p["line"])
    st.configure("TLabelframe.Label", background=p["bg"], foreground=p["dim"])
    st.configure("TSeparator", background=p["line"])
    # Menus (right-click, dropdown) use the theme palette, not the OS default.
    st.configure("TMenu", background=p["panel"], foreground=p["text"])
    st.map("TMenu", background=[("active", p["line"])])
    return ui, mono


def _retint_bg(widget, old_palette, new_palette):
    """Recursively re-tint tk widgets whose explicit bg/fg match a palette key."""
    try:
        wclass = widget.winfo_class()
    except Exception:
        return

    all_keys = ("bg", "panel", "field", "cross", "line", "text", "dim",
                "accent", "ok", "warn", "err")

    def _swap(color, old_p, new_p):
        if color is None or color == "":
            return None
        for key in all_keys:
            if color == old_p.get(key):
                return new_p[key]
        return None

    try:
        if wclass == "Frame":
            bg = widget.cget("background")
            new = _swap(bg, old_palette, new_palette)
            if new:
                widget.configure(background=new)
        elif wclass == "Canvas":
            # Canvas options are -bg / -foreground / -highlightbackground; there
            # is no -fg.  cget each independently so a missing option on one
            # cannot abort the re-tint of the others (the old code read "fg"
            # and the TclError it raised killed the whole branch before bg was
            # ever configured -- the "still bg black!" report).
            for opt, cfg in (("bg", "bg"), ("foreground", "foreground"),
                             ("highlightbackground", "highlightbackground")):
                try:
                    val = widget.cget(opt)
                except tk.TclError:
                    continue
                new_val = _swap(val, old_palette, new_palette)
                if new_val:
                    widget.configure(**{cfg: new_val})
        elif wclass in ("Label", "Button", "Checkbutton"):
            bg = widget.cget("background")
            fg = widget.cget("foreground")
            new_bg = _swap(bg, old_palette, new_palette)
            if new_bg:
                widget.configure(background=new_bg)
            new_fg = _swap(fg, old_palette, new_palette)
            if new_fg:
                widget.configure(foreground=new_fg)
            for opt in ("activebackground", "activeforeground", "selectcolor"):
                try:
                    val = widget.cget(opt)
                except tk.TclError:
                    continue
                new_val = _swap(val, old_palette, new_palette)
                if new_val:
                    widget.configure(**{opt: new_val})
    except tk.TclError:
        pass
    for child in widget.winfo_children():
        _retint_bg(child, old_palette, new_palette)


def _rgb(hexcol):
    """``"#rrggbb"`` -> a float RGB triple."""
    return np.array([int(hexcol[i:i + 2], 16) for i in (1, 3, 5)], dtype=float)


def _gradient_photo(w, h, top, bottom, glow=None):
    """The graded ground as a PhotoImage *w* x *h*: a vertical ramp from *top*
    to *bottom*, with an optional soft bloom of *glow* in the middle.

    Tk fills flat -- a Canvas has no gradient option and no alpha -- so a ramp
    has to arrive as pixels, like everything else rendered in this window.  The
    bloom is what makes it worth having: the photograph sits in the middle of
    this, so the light gathers just outside the frame and falls away to the
    corners.  Faint on purpose (16% at its strongest): the ground is behind a
    picture being judged, and a ground that announces itself has already won an
    argument it should not have been in.
    """
    w, h = max(int(w), 1), max(int(h), 1)
    ca, cb = _rgb(top), _rgb(bottom)
    t = np.linspace(0.0, 1.0, h)[:, None, None]
    img = np.repeat(ca[None, None, :] + (cb - ca)[None, None, :] * t, w, axis=1)
    if glow:
        yy = (np.arange(h)[:, None] - (h - 1) / 2.0) / max(h / 2.0, 1.0)
        xx = (np.arange(w)[None, :] - (w - 1) / 2.0) / max(w / 2.0, 1.0)
        # Squared falloff, not linear: linear leaves a visible disc edge where
        # it reaches zero, and the whole point is that you cannot see where it
        # stops.
        f = np.clip(1.0 - np.sqrt(yy ** 2 + xx ** 2), 0.0, 1.0) ** 2
        img += (_rgb(glow)[None, None, :] - img) * (0.16 * f[:, :, None])
    return ImageTk.PhotoImage(
        Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)))


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


def _hex_rgba(hexc, alpha=255):
    """A ``#rrggbb`` palette colour as an RGBA tuple for PIL."""
    h = hexc.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


_ICON_FONTS = ("segoeicons.ttf", "SegMDL2.ttf")   # Fluent, then MDL2


def _icon_pil(codepoint, size, colour, emoji=False, mono=False):
    """One stock Windows icon, tinted, centred on its ink in a size x size box.

    Stock rather than hand-drawn: a shipped icon set is already consistent, and
    drawing six marks by hand to look like a family is work with no upside.

    Centred on the INK and not on the text box, which is the whole reason this
    renders to an image instead of being a glyph in a Checkbutton: text sits on
    a baseline with its descent below, so a circle in a fixed key reads high
    even when the widget is centring it perfectly.  Cropping to the drawn
    pixels and re-centring those is the only way the eye agrees.

    Falls back to None when neither font is present, so a caller can keep a
    plain text glyph rather than showing nothing.
    """
    from PIL import ImageDraw, ImageFont
    import os
    ch = chr(int(codepoint, 16))
    # Colour emoji live in their own font and carry their own palette, so
    # they are drawn with embedded_color and no tint.  The icon fonts have
    # no dinosaur: they answer with tofu, which measures as a 12x17 box and
    # would have shipped looking like a deliberate glyph.
    font = None
    for name in (("seguiemj.ttf",) if emoji else _ICON_FONTS):
        path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", name)
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, size)
                break
            except Exception:
                continue
    if font is None:
        return None
    pad = size // 2
    scratch = Image.new("RGBA", (size + 2 * pad, size + 2 * pad), (0, 0, 0, 0))
    if emoji and not mono:
        ImageDraw.Draw(scratch).text((pad, pad), ch, font=font, embedded_color=True)
    else:
        # `mono` takes the emoji font's outline and fills it flat, which is what
        # puts a dinosaur in a palette of monochrome keys without it arriving as
        # the one coloured sticker on the bar.  Measured readable: 27x25 of ink
        # at 39 % coverage, a silhouette rather than a blob.
        ImageDraw.Draw(scratch).text((pad, pad), ch, font=font, fill=_hex_rgba(colour))
    ink = scratch.getbbox()
    if ink is None:
        return None
    glyph = scratch.crop(ink)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if glyph.width > size or glyph.height > size:
        glyph.thumbnail((size, size))
    out.paste(glyph, ((size - glyph.width) // 2, (size - glyph.height) // 2), glyph)
    return out


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
        t.configure(bg=INK["cross"])
        t.wm_overrideredirect(True)
        x = widget.winfo_rootx() + 12
        y = widget.winfo_rooty() + widget.winfo_height() + 4
        t.wm_geometry(f"+{x}+{y}")
        lbl = tk.Label(t, text=text, background=INK["cross"], foreground=INK["text"],
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


def _brand_header(parent, on_select=None):
    """Emblem + wordmark, packed at the top of a window.

    The bar is registered on the toplevel so `_switch_theme` can find it again;
    its emblem is a rendered image and has to be redrawn, not re-optioned.
    """
    bar = ttk.Frame(parent)
    bar.pack(fill="x", side="top")
    top = parent.winfo_toplevel()
    if not hasattr(top, "_brand_bars"):
        top._brand_bars = []
    top._brand_bars.append(bar)
    size = _emblem_size(parent)
    mark = _logo_image(size, INK["text"])
    if mark is not None:
        photo = ImageTk.PhotoImage(mark)
        lbl = ttk.Label(bar, image=photo)
        lbl.image = photo               # a Label keeps no reference of its own
        lbl.pack(side="left", padx=(2, 8), pady=4)
        # Handed back so a theme switch can re-render it: the mark is tinted
        # INK["text"] into its pixels, and no walk over widget options can
        # reach a colour that is part of an image.
        bar._brand_lbl = lbl
        bar._brand_size = size
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
    title = ttk.Label(bar, text="Perspective Correction", style="Title.TLabel")
    title.pack(side="left", anchor="w")
    _attach_tooltip(title, "Batch perspective correction for architectural photographs")
    # Which copy of this repo is actually running: a stale second copy shows an
    # older number and gives the whole exercise away.
    ttk.Label(bar, text=f"v{__version__}", style="Dim.TLabel").pack(side="right", padx=(8, 6))
    theme_var = tk.StringVar(value="Minimal Black")
    if on_select is not None:
        theme_var.trace_add("write", lambda *_: on_select(theme_var.get()))
    combo = ttk.Combobox(bar, textvariable=theme_var, values=list(THEMES.keys()),
                         state="readonly", width=14)
    combo.pack(side="right", padx=(0, 8), pady=2)
    return bar, theme_var, combo


# ==========================================================================
# review window
# ==========================================================================
class ReviewPanel(tk.Frame):
    # The review UI, embedded in the batch window.  One frame for the whole
    # task: a second window split one photograph across two frames and stole
    # focus on every double-click.  Built once; `load` swaps the photograph
    # into the same widgets.
    def __init__(self, master):
        super().__init__(master, background=INK["cross"])
        # The perfect cross (2026-09-12): four exactly equal fields -- the two
        # previews on top, loader and controls below -- divided by a flat dark
        # cross and ringed by a dark border, both CROSS_GAP/CROSS_BORDER wide.
        # One grid of uniform rows and columns makes equality a property of the
        # layout rather than an agreement between two arrangements; `load`
        # destroys and rebuilds three of the four fields, the loader survives.
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
        self.rowconfigure(0, weight=1, uniform="cross_rows")
        self.rowconfigure(1, weight=1, uniform="cross_rows")
        self.columnconfigure(0, weight=1, uniform="cross_cols")
        self.columnconfigure(1, weight=1, uniform="cross_cols")
        self.cell_before = ttk.Frame(self)
        self.cell_after = ttk.Frame(self)
        self.loader = ttk.Frame(self)              # rebuilt by the batch window
        self.cell_ui = ttk.Frame(self)
        # Each cell carries half the cross on its inner sides and the full
        # border on its outer ones, so the dark shows through at exactly
        # CROSS_GAP between fields and CROSS_BORDER around the panel.
        self.cell_before.grid(row=0, column=0, sticky="nsew", padx=(b, g), pady=(b, g))
        self.cell_after.grid(row=0, column=1, sticky="nsew", padx=(g, b), pady=(b, g))
        self.loader.grid(row=1, column=0, sticky="nsew", padx=(b, g), pady=(g, b))
        self.cell_ui.grid(row=1, column=1, sticky="nsew", padx=(g, b), pady=(g, b))
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
        self.settings = settings
        self.dest_path = dest_path
        self.on_saved = on_saved
        self.on_closed = on_closed
        self._closed_sent = False
        self._busy = False
        self._before_scale = 1.0
        self._redraw_tries = 0
        self._after_guides = []
        self._guide_drag = None
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
            title = (f"{position}  {os.path.basename(path)}  {W}×{H}  |  Batch "
                     f"Perspective Correction  v{__version__}")
            app.title(title.strip())
        self._build()
        self.v_overwrite.set(bool(overwrite))
        self._refresh_file_names()
        self.v_alpha.set(self.session.mask_alpha)
        # Small correction, small band: take the crop rather than leave a band
        # that would otherwise need a generative model to fill.  Visible, shaded
        # and undoable with "Reset crop" -- see `auto_crop_if_cheap`.
        if self.session.auto_crop_if_cheap():
            self._refresh_crop()
        self.after(60, self._sync_from_session)

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

        `_save` and `_keep` call `destroy()` directly, so hanging the removal
        off `_on_close` would leave a dead widget in the App's list after the
        commonest exit of all.  `_show_comfy_state` catches and drops a stale
        listener anyway -- but that net is for the unexpected, not for the
        normal path.
        """
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
        self._before_lbl.configure(text=f"before   {_shorten_middle(src)}")
        self._after_lbl.configure(text=f"after   {_shorten_middle(dst)}")

    # -- layout ----------------------------------------------------------
    def _build(self):
        # No brand header here: the panel sits inside the batch window, which
        # carries its own.
        # The controls are one of the four fields, not a strip under two of
        # them: `cell_ui` is the lower-right box of the cross and everything
        # from the hint down to the Save row lives inside it.
        top = ttk.Frame(self.cell_ui, padding=6)
        top.pack(fill="both", expand=True)

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
        self._before_lbl = ttk.Label(self.cell_before, text="before")
        self._before_lbl.pack(anchor="w")
        self.c_before = tk.Canvas(self.cell_before, bg=INK["field"],
                                  highlightthickness=0, width=1, height=1)
        self.c_before.pack(fill="both", expand=True)
        # The add triggers live in the before-image's own top-left corner, in front
        # of the picture: a grey "+" for images and a grey folder for a folder.
        # Grey rather than accent -- they are part of the ground, not a call to
        # action; the cross keeps its one colour of emphasis elsewhere.  The frame
        # is re-created on every `load` (the canvas is rebuilt), so the buttons
        # track the panel's lifecycle for free.
        app = self._app()
        addbar = tk.Frame(self.c_before, bg=INK["field"])
        self._addbar = addbar
        # Stacked downwards, not across (2026-09-13, user-directed): this corner
        # is a tool palette, and a palette reads as a column.  Load is the big one
        # -- it is the only thing to press on an empty window, so it earns the
        # size; the tools below it are small and uniform.
        if getattr(self, "_palette_blank", None) is None:
            self._palette_blank = tk.PhotoImage(width=1, height=1)
        _side, _gap = layout.tool_key()
        self._add_img = ImageTk.PhotoImage(_icon_pil("E710", layout.tool_glyph(), INK["dim"]))
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
        self._folder_img = ImageTk.PhotoImage(_icon_pil("E8B7", layout.tool_glyph(), INK["dim"]))
        self.add_folder_btn = tk.Button(addbar, image=self._folder_img, relief="flat",
                                        bd=0, cursor="hand2", background=INK["field"],
                                        width=_side, height=_side, compound="center",
                                        padx=0, pady=0, highlightthickness=0, command=self._on_add_folder)
        self.add_folder_btn.pack(side="top", pady=(_gap, 0))
        _attach_tooltip(self.add_folder_btn, "Add a folder of images")
        self._paste_img = ImageTk.PhotoImage(_icon_pil("E77F", layout.tool_glyph(), INK["dim"]))
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
        addbar.place(x=8, y=8, anchor="nw")
        # Lines and Mask draw on *this* image, so their switches sit on it --
        # top-right, opposite the add icons, and mirroring the Grid switch on the
        # after pane (2026-09-13, user-directed).  Made once and reused because
        # `_build` re-runs on every load: fresh variables here would reset both
        # toggles for each photograph and leave the old canvas' checkbuttons
        # pointing at dead ones.
        if getattr(self, "v_show_lines", None) is None:
            self.v_show_lines = tk.BooleanVar(value=True)
            self.v_show_mask = tk.BooleanVar(value=True)
            self.v_mask_color = tk.StringVar(value="#dc4c3e")
            self.v_mask_alpha = tk.DoubleVar(value=0.60)
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
        self._after_lbl = ttk.Label(self.cell_after, text="after")
        self._after_lbl.pack(anchor="w")
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
        gridbar = tk.Frame(self.c_after, bg=INK["field"])
        self._gridbar = gridbar
        ttk.Checkbutton(gridbar, text="Check lines", command=self._schedule_redraw,
                        variable=self.v_after_lines).pack(side="left", padx=(4, 0))
        gridbar.place(relx=1.0, x=-8, y=8, anchor="ne")
        self._pending_mark = None
        self._after_off = (0, 0)
        self._crop_drag_start = None
        self._roi_drag = None         # which ROI ruler is grabbed: 0=left, 1=right
        if getattr(self, "_loupe", None) is not None:   # _build re-runs on load
            self._loupe.destroy()
        self._loupe = None            # magnifying-glass canvas over the cross, or None
        self._loupe_center = None     # damped crop centre (image px) for Alt tracking
        # A rebuild (new image loaded) kills the glass; it is raised again on
        # the next mark press rather than kept up while the mode is on.
        if getattr(self, "v_mark", None) is not None and self.v_mark.get():
            pass  # nothing to restore: the loupe now lives only during a drag
        self.c_before.bind("<Button-1>", self._on_click_before)
        self.c_before.bind("<Motion>", self._on_before_motion)
        self.c_before.bind("<B1-Motion>", self._on_before_b1motion)
        self.c_before.bind("<ButtonRelease-1>", self._on_before_b1release)
        # Photoshop's gestures, because this is a brush and those are the ones in
        # everybody's hands already: left paints, right erases, Alt+right dragged
        # sideways sizes the pen.  The Alt bindings are declared first only for
        # readability -- Tk picks the more specific pattern regardless of order.
        self.c_before.bind("<Alt-ButtonPress-1>", self._on_alt_erase_press)
        self.c_before.bind("<Alt-ButtonPress-3>", self._on_pen_size_start)
        self.c_before.bind("<Alt-B3-Motion>", self._on_pen_size_drag)
        # The sizing drag had a press and a motion and no END. Alt+right
        # release arrives as <Alt-ButtonRelease-3>, which nothing was bound
        # to, so `_pen_anchor` stayed set forever -- and `_on_erase_motion`
        # bails out while it is set. After sizing the pen once, right-drag
        # erase silently degraded to erasing the press and release points
        # only, with the whole dragged stroke dropped. Measured, not read.
        self.c_before.bind("<Alt-ButtonRelease-3>", self._on_pen_size_end)
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
        self.c_before.bind("<ButtonPress-3>", self._on_right_press)
        self.c_before.bind("<B3-Motion>", self._on_erase_motion)
        self.c_before.bind("<ButtonRelease-3>", self._on_erase_release)
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

        hint = ttk.Label(top, style="Dim.TLabel",
                         text="click a line in the left image to strike it out, "
                              "or to bring it back")
        self.lbl_status = ttk.Label(top, style="Dim.TLabel", text="",
                                    wraplength=760, justify="left")

        stat = ttk.Frame(top)
        # The four control rows below cost ~280 px of height that the two image
        # canvases want back.  Collapsing them is one click.  The toggle rides
        # in this row instead of a row of its own, because a new row would cost
        # height in the default (expanded) state -- which is the state this is
        # trying to improve.  The state lives on the panel, not the widget:
        # `load` destroys every child and rebuilds, and a collapse that
        # re-opened itself on the next photograph of a queue is worse than no
        # collapse at all.
        # The default is the height's to decide, not the code's: with the rows
        # open a 1080p review pane leaves the picture ~90 px, which is not a
        # preview.  A choice already made outranks it -- `_adj_open` exists the
        # moment anyone touches the toggle, and then it holds for the session
        # and for every `load` of a review queue.
        # The control columns default to visible -- one persistent layout, no
        # height-based auto-collapse (that is what kept them out of sight).  A
        # manual choice already made outranks the default: `_adj_open` exists
        # the moment anyone touches the toggle and must survive `load`, which
        # destroys every child and rebuilds.
        if hasattr(self, "_adj_open"):
            start_open = self._adj_open
        else:
            start_open = True
        self.v_adjust = tk.BooleanVar(value=True)

        adj = ttk.Frame(top)
        self._adj = adj
        # Two columns instead of four stacked rows: the two image canvases on
        # top want the height back, and a cross (images over controls) reads as
        # one surface rather than a long scroll.  Left is FIND -- which detector
        # feeds the estimator; right is EDIT -- the angles that turn it into the
        # after pane, plus how the opened band is filled.  Each column is about
        # half the old block, so opening the controls costs the picture ~140 px
        # instead of ~282.
        # The panel is the EDIT side only now: the FIND controls (line detector)
        # moved to the lower-left tools field, beside the input image where
        # what-the-estimator-sees belongs.  The facade strip stayed here -- it
        # restricts horizontal evidence and nothing else, so it sits under the
        # yaw switch it shapes.  One column, so the sliders that need
        # horizontal room to drag take all of it -- an even split used to starve
        # the tracks on a laptop: label + spinbox leave ~14 px.
        rightcol = ttk.Frame(adj)
        rightcol.pack(side="left", fill="both", expand=True)

        ctl = ttk.Frame(rightcol, padding=(0, 8, 0, 0))
        ctl.pack(fill="x")
        self.v_roll = tk.DoubleVar(value=0.0)
        self.v_pitch = tk.DoubleVar(value=0.0)
        self.v_focal = tk.DoubleVar(value=28.0)
        self.v_correct_horizontal = tk.BooleanVar(value=self.settings.correct_horizontal)
        self.v_yaw = tk.DoubleVar(value=0.0)
        if getattr(self, "v_hmarker", None) is None:
            self.v_hmarker = tk.BooleanVar(value=False)

        # Row 0: horizontal marker (manuell) — yaw from hand-drawn lines.
        _hm = ttk.Checkbutton(ctl, text="horizontal marker (manuell)",
                              variable=self.v_hmarker,
                              command=self._on_hmarker_toggle)
        _hm.grid(row=0, column=0, columnspan=3, sticky="w")
        _attach_tooltip(
            _hm,
            "Compute yaw directly from a hand-drawn horizontal marker.\n"
            "1-2 lines: exact. 3+: least-squares interpolation.\n"
            "Mutually exclusive with horizontal auto.")

        # Row 1: horizontal auto (yaw) — VP-based correction + slider, as before.
        _hchk = ttk.Checkbutton(ctl, text="horizontal auto (yaw)",
                                variable=self.v_correct_horizontal,
                                command=self._on_horizontal_toggle)
        _hchk.grid(row=1, column=0, sticky="w")
        _attach_tooltip(
            _hchk,
            "Square the camera onto one horizontal direction via vanishing point.\n"
            "On a corner view with two facades this necessarily makes the second "
            "one worse - it is a special case, not a default.")
        self._yaw_scale = ttk.Scale(ctl, from_=-60, to=60, variable=self.v_yaw,
                                    orient="horizontal",
                                    command=lambda _v: self._on_slider())
        self._yaw_scale.grid(row=1, column=1, sticky="ew", padx=6)
        self._yaw_spin = ttk.Spinbox(ctl, textvariable=self.v_yaw, from_=-60, to=60,
                                     increment=0.1, format="%.2f", width=7,
                                     command=self._on_slider)
        self._yaw_spin.grid(row=1, column=2, sticky="e", padx=(6, 0))
        self._yaw_spin.bind("<Return>", lambda _e: self._on_slider())
        if not self.settings.correct_horizontal:
            self._yaw_scale.configure(state="disabled")
            self._yaw_spin.configure(state="disabled")

        # Rows 2-4: roll, pitch, focal sliders.
        self._slider(ctl, 2, "roll (level)", self.v_roll, -20, 20, "deg", 0.1, "%.2f")
        self._slider(ctl, 3, "pitch (verticals)", self.v_pitch, -30, 30, "deg", 0.1, "%.2f")
        self._slider(ctl, 4, "focal length", self.v_focal, 8, 200, "mm eq", 1, "%.0f")

        # The line detector moved to the lower-left tools field with the rest of
        # the FIND controls (see `_build_tools`) -- beside the input image, where
        # what-the-estimator-sees belongs.  The facade strip stayed here, under
        # the yaw switch it shapes.  This panel is EDIT only.

        # The masking controls (source + BiRefNet model picker) live in the
        # lower-left tools field now -- App._build calls `_build_tools`, which
        # builds them there. They were a row here in the lower-right cell.

        # A hairline between the edit block above and the output block below, so
        # "angles" and "fill/mask/output" read as two zones instead of one column.
        ttk.Separator(rightcol, orient="horizontal").pack(fill="x", pady=(6, 2))

        fill_row = ttk.Frame(rightcol, padding=(0, 8, 0, 0))
        fill_row.pack(fill="x")
        self.v_fill = tk.StringVar(value=self._cfg().fill or "none")
        ttk.Label(fill_row, text="fill band", width=18).grid(row=0, column=0, sticky="w")
        fbox = ttk.Combobox(fill_row, textvariable=self.v_fill, width=11,
                            state="readonly", values=["none", "telea", "lama", "comfyui"])
        fbox.grid(row=0, column=1, sticky="w", padx=(6, 6))
        fbox.bind("<<ComboboxSelected>>", lambda e: self._apply_fill())
        # The pad colour picker, its swatch and the "edge" button left this row
        # (2026-09-14, user). `pad` only shows through when the fill is off, and
        # the fill defaults to `telea`, so three controls were competing for
        # width in the busiest row in the window to set something almost nobody
        # ever sees. `--pad` and `Settings.pad` are untouched -- the setting is
        # still there for anyone who runs with `--fill none`, it simply has no
        # widget. (The "edge" button's tooltip described padding *width*, which
        # it never set, so it had been lying about itself as well.)
        # The ComfyUI mode was selectable here with no way to configure it and
        # no indicator -- so picking it meant the default address and, worse,
        # the *unnamed default workflow*, which is the inpainting graph. An
        # edit model run through it produces a wrong band at a green light.
        # Same window as the batch panel's, because there is one server.
        self.btn_comfy = ttk.Button(fill_row, text="ComfyUI settings",
                                    command=self._open_comfy)
        self.btn_comfy.grid(row=0, column=5, sticky="w", padx=(12, 0))
        _attach_tooltip(self.btn_comfy, "Configure the ComfyUI server address and inpainting workflow")
        self.lbl_comfy = ttk.Label(fill_row, text="", style="Dim.TLabel")
        self.lbl_comfy.grid(row=1, column=1, columnspan=5, sticky="w", padx=(6, 0))
        fill_row.columnconfigure(5, weight=1)
        self._register_comfy()

        # Save / Keep / Close stay outside the collapsible: a queue advances on
        # Save, so a Save button that can be hidden is a behaviour change.
        # Two rows, not one.  The single row requested ~1795 px -- thirteen left-
        # packed correction controls plus the four right-packed actions -- against
        # a 910 px field at the 1920x1080 floor, so `pack` starved the action
        # cluster (and the Grid/grid-step tail) to zero width.  Splitting keeps
        # every control mapped; the picture yields the height, per the assembly
        # rule below.  The display overlays (Lines / Mask / Grid / grid-step)
        # moved to the lower-left tools field -- see `_build_tools`.
        btns = ttk.Frame(top, padding=(0, 8))
        self._btns = btns
        _b = ttk.Button(btns, text="Auto", command=self._use_auto)
        _b.pack(side="left")
        _attach_tooltip(_b, "Apply the auto-computed correction (roll + pitch)")
        _b = ttk.Button(btns, text="Reset", command=self._reset)
        _b.pack(side="left", padx=6)
        _attach_tooltip(_b, "Reset all angles to zero")
        _b = ttk.Button(btns, text="Save As…", command=self._save_as)
        _b.pack(side="right", padx=(0, 6))
        _attach_tooltip(_b, "Choose a file name and location for the corrected image")
        _b = ttk.Button(btns, text="Save", command=self._save,
                        style="Accent.TButton")
        _b.pack(side="right")
        _attach_tooltip(_b, "Save the corrected image and advance to the next")
        self.v_overwrite = tk.BooleanVar(value=False)
        _cb = ttk.Checkbutton(btns, text="overwrite original",
                              variable=self.v_overwrite,
                              command=self._refresh_file_names)
        _cb.pack(side="right", padx=8)
        _attach_tooltip(_cb, "Replace the source file instead of writing a new one")
        _b = ttk.Button(btns, text="Close",
                        command=self._close)
        _b.pack(side="right", padx=(14, 6))
        _attach_tooltip(_b, "Close this image without saving")
        _b = ttk.Button(btns, text="Keep original",
                        command=self._keep)
        _b.pack(side="right", padx=6)
        _attach_tooltip(_b, "Skip correction and keep the unmodified file")

        # Row two: crop operations.  Mark / Strike slanted
        # moved to the lower-left tools field (2026-09-13).
        btns2 = ttk.Frame(top, padding=(0, 8))
        self._btns2 = btns2
        # Mask brush state lives here; its widgets live in the lower-left tools
        # field -- App._build calls `_build_tools` to place them.  **Made once.**
        # `_build` re-runs on every load while that tools field is built a single
        # time, so rebuilding these variables handed the checkbutton a stale one:
        # ticking the box set a variable nobody read, `_on_click_before` saw the
        # fresh False, and the brush silently did nothing from the second
        # photograph onwards.  Same trap as the overlay switches above.
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
        # The palette in the picture's top-left corner can only be finished here:
        # it toggles these variables, and they do not exist until this point.
        self._build_tool_palette()
        _b = ttk.Button(btns2, text="Clear marks",
                        command=self._clear_marks)
        _b.pack(side="left", padx=(6, 0))
        _attach_tooltip(_b, "Remove all manually placed control lines")
        _b = ttk.Button(btns2, text="Auto crop",
                        command=self._auto_crop)
        _b.pack(side="left", padx=(0, 6))
        _attach_tooltip(_b, "Crop to the detected content bounds")
        _b = ttk.Button(btns2, text="Reset crop",
                        command=self._clear_crop)
        _b.pack(side="left", padx=(0, 6))
        _attach_tooltip(_b, "Remove any manual crop selection")

        # -- assembly: who gives up height first ---------------------------
        # `pack` hands each child its requested height in call order and gives
        # the cavity that is left to the expanding one, so the pack order *is*
        # the priority order.  Packed bottom-up, so this reads as the reverse
        # of what is on screen: the action row claims its strip first and the
        # picture takes whatever survives.  At 1920x1080 -- the commonest
        # desktop there is -- Save, Keep original and Close were simply not
        # mapped before this, in the one mode that exists to be driven by hand.
        # Same pattern as the ComfyUI dock's `side="bottom"`.
        btns.pack(side="bottom", fill="x")
        btns2.pack(side="bottom", fill="x")
        adj.pack(side="bottom", fill="x", after=btns2)
        stat.pack(side="bottom", fill="x", pady=(6, 4))
        self.lbl_status.pack(side="bottom", anchor="w", pady=(2, 0))
        hint.pack(side="bottom", anchor="w", pady=(4, 0))

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

    def _apply_roi(self, _event=None):
        """Restrict horizontal evidence to an x-strip (corner views).  Off or an
        empty/invalid strip leaves ``roi_x`` at None -- the unfiltered frame.
        Re-estimates only in AUTO; a manual take-over keeps its angles until the
        user returns to Auto, so the strip cannot yank a correction they set.

        A valid strip also turns horizontal (yaw) correction on: the strip only
        shapes the yaw, and the yaw is gated on ``correct_horizontal`` -- left off,
        the rulers would show and change nothing.  Clearing the strip does not force
        the flag back off; a hand-set yaw or an independent choice stays put."""
        s = self.session
        if s is None:
            return
        aw = s.gray.shape[1]
        roi = None
        if self.v_roi.get():
            try:
                x0 = float(self.v_roi_x0.get()) / 100.0
                x1 = float(self.v_roi_x1.get()) / 100.0
            except ValueError:
                return
            if 0.0 <= x0 < x1 <= 1.0 and (x1 - x0) >= 0.02:
                roi = (x0 * aw, x1 * aw)
        s.roi_x = roi
        if roi is not None and not s.settings.correct_horizontal:
            self.v_correct_horizontal.set(True)
            # Sets the flag, enables the yaw slider, and refits in AUTO -- with
            # roi_x already in place above, so the strip is what gets applied.
            self._on_horizontal_toggle()
            return
        if s.mode == AUTO:
            s.refit()
            self._sync_from_session()
        else:
            self._redraw()

    def _apply_mask(self):
        if not self.session:
            return                     # no photo loaded; nothing to mask yet
        if getattr(self, "_mask_enabled", True) is False:
            # Off has to mean off.  This used to return early, which blocked new
            # applications but left an already-applied mask in force -- the box
            # said inactive while the fit was still missing every line the mask
            # had removed.  `set_mask("off")` re-detects without it, and the
            # painted region is untouched by this: `paint` lives on the session
            # and is re-applied by refit, which is what the tooltip promises.
            self.lbl_mask.configure(text="mask inactive")
            self.session.set_mask("off")
            self._sync_from_session()
            return
        mode = self.v_maskmode.get()
        if mode == "file" and not self.session.settings.mask_file:
            if not self._pick_mask_folder(apply_now=False):
                self.v_maskmode.set(self.session.settings.mask_mode)
                return
        if mode in ("birefnet", "gdino") and not self.session.settings.birefnet_model:
            # The path was typed once and saved to prefs; a fresh photo reloads
            # with an empty session, so fall back to the stored value before
            # asking again -- it must be asked exactly once, not per photograph.
            # Read the App's remembered birefnet model (mode-independent), not
            # its v_maskpath field, which holds a folder when the batch side sits
            # in "file" mode; under a test root there is no App, so use prefs.
            app = self._app()
            remembered = getattr(app, "_remembered", {}) if app is not None else {}
            # The weights vendored in the repo come BEFORE the file dialog.
            # Without this the window asked the user to go and find a file that
            # ships with the project, and cancelling that dialog rolled the
            # combobox back with no message -- which is exactly what "the mask
            # does nothing" looked like.  `masks.default_birefnet()` is the same
            # fallback the CLI path uses, so both agree about what "unset" means.
            stored = (remembered.get("birefnet_model", "")
                      or prefs.load().get("birefnet_model", "")
                      or MK.default_birefnet())
            if stored and os.path.isfile(stored):
                self.session.settings = self.session.settings.replace(
                    birefnet_model=stored)
            elif not self._pick_birefnet_model(apply_now=False):
                # Say so.  A control that silently undoes the user's choice is
                # indistinguishable from one that is broken.
                self.v_maskmode.set(self.session.settings.mask_mode)
                self.lbl_mask.configure(
                    text=f"no BiRefNet weights chosen -- mask source stays "
                         f"'{self.session.settings.mask_mode}'")
                return
        if mode == "gdino":
            # the prompt lives in the entry, not the settings, until it is applied
            self.session.settings = self.session.settings.replace(
                gdino_prompt=self.v_gdino_prompt.get())
        err = self.session.set_mask(mode, invert=bool(self.v_maskinv.get()))
        self.lbl_mask.configure(text=err or "")
        if self.session.mode == AUTO:
            self._sync_from_session()
        else:
            self._redraw()

    def _clear_mask(self):
        """Remove painted mask and SAM selection; reset mode to off."""
        if not self.session:
            return
        self.session.paint = None
        self.session._paint_struck = np.zeros(len(self.session.vert), dtype=bool)
        self.session.sam_mask = None
        self._sam_box = None
        self._sam_box_px = None
        self._sam_points = []
        self._sam_selection = None
        self.v_maskmode.set("off")
        self.lbl_mask.configure(text="")
        self.c_before.delete("sam_prompts")
        self._redraw()

    def _pick_birefnet_model(self, apply_now=True):
        """Point at BiRefNet weights and say what they are.

        A folder of these holds look-alikes -- HR, lite, matting, 2K variants
        that run at different resolutions -- and the architecture has to sit
        beside them, so the choice is described immediately rather than after a
        failed batch."""
        if not self.session:
            return False               # nothing to attach the model to yet
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
        if not self.session:
            return False               # nothing to attach the mask to yet
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
        # 4×4 grid of vivid, saturated colours — easy to distinguish on any theme.
        presets = [
            "#ff3030", "#ff7030", "#ffb030", "#ffff30",
            "#30ff50", "#30ffd0", "#3090ff", "#3030ff",
            "#8030ff", "#d030ff", "#ff30c0", "#ff3060",
            "#ffffff", "#b0b0b0", "#606060", "#202020",
        ]
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

    def _on_mask_active_toggle(self):
        """Toggle mask active/inactive without clearing the painted region."""
        if not self.session:
            return
        self._mask_enabled = self.v_mask_active.get()
        self._apply_mask()

    def _on_invert_toggle(self):
        """Flip the whole mask and re-fit."""
        if not self.session:
            return
        self.session.invert_mask = bool(self.v_invert.get())
        self.session._refresh_mask()
        self.session.refit()
        self._sync_from_session()

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
        self.session.settings = self.session.settings.replace(fill=self.v_fill.get())
        if self.v_fill.get() == "comfyui":
            # Choosing it is asking for it -- the same rule the batch panel
            # follows. A mode that silently needs six settings nobody was shown
            # is the quiet failure this project keeps arguing against.
            self._open_comfy()
        else:
            self.lbl_comfy.configure(text="")
        self._schedule_redraw()

    def _toggle_mask(self):
        """Show the excluded region and the lines it removed.

        A mask the user cannot see is a mask the user cannot trust: when a
        segmenter takes out the wrong half of a building, the only other symptom
        is a quietly worse answer."""
        self.session.show_mask = bool(self.v_show_mask.get())
        self._schedule_redraw()

    def _slider(self, parent, row, label, var, lo, hi, unit, inc, fmt):
        ttk.Label(parent, text=label, width=18).grid(row=row, column=0, sticky="w")
        sc = ttk.Scale(parent, from_=lo, to=hi, variable=var, orient="horizontal",
                       command=lambda _v: self._on_slider())
        sc.grid(row=row, column=1, sticky="ew", padx=6)
        parent.columnconfigure(1, weight=1, minsize=layout.SLIDER_MIN)
        # The scale reaches a region by eye; the spinbox is for the last fraction
        # of a degree -- type an exact value or nudge one fine step at a time.
        # Both drive the same variable, so moving either moves both.
        sp = ttk.Spinbox(parent, textvariable=var, from_=lo, to=hi, increment=inc,
                         format=fmt, width=7, command=self._on_slider)
        sp.grid(row=row, column=2, sticky="e", padx=(6, 0))
        sp.bind("<Return>", lambda _e: self._on_slider())

    # -- state -----------------------------------------------------------
    def _sync_from_session(self):
        roll, pitch, f, _ = self.session.current_angles()
        yaw = self.session.current_yaw()
        from .model import focal_35mm_from_px
        self.v_roll.set(round(math.degrees(roll), 2))
        self.v_pitch.set(round(math.degrees(pitch), 2))
        self.v_yaw.set(round(math.degrees(yaw), 2))
        if f:
            self.v_focal.set(round(focal_35mm_from_px(f, self.session.w, self.session.h), 0))
        self._redraw()

    def _on_horizontal_toggle(self):
        """Auto mode: VP-based yaw. Mutually exclusive with H-Marker."""
        on = self.v_correct_horizontal.get()
        if on and self.v_hmarker.get():
            # Switching to auto turns off manual.
            self.v_hmarker.set(False)
            self._on_hmarker_toggle()
        s = self.session
        if s is None:
            return
        s.settings = s.settings.replace(correct_horizontal=on)
        state = "normal" if on else "disabled"
        self._yaw_scale.configure(state=state)
        self._yaw_spin.configure(state=state)
        if on and self.session.mode == AUTO:
            self.session.refit()
            self._sync_from_session()
        elif not on and self.session.mode == AUTO:
            self.v_yaw.set(0.0)
            self._schedule_redraw()

    def _on_hmarker_toggle(self):
        """Manual mode (H-Marker): yaw from line bearings, no VP.

        Mutually exclusive with "horizontal auto".  When active, the bearing
        algorithm replaces the VP path entirely -- different correction model.
        """
        on = self.v_hmarker.get()
        if not on:
            s = self.session
            if s is not None:
                s._hmarker_yaw = None
                s._hmarker_active = False
                s.refit()
                self._sync_from_session()
            return
        # Switching to manual turns off auto.
        if self.v_correct_horizontal.get():
            self.v_correct_horizontal.set(False)
            s = self.session
            if s is not None:
                s.settings = s.settings.replace(correct_horizontal=False)
        s = self.session
        if s is None:
            self._set_status("no image loaded")
            self.v_hmarker.set(False)
            return
        n_v = len(s.control_lines)
        n_h = len(s.control_hlines)
        if n_v < 1 and n_h < 1:
            self._set_status("draw at least one marker line (V or H)")
            self.v_hmarker.set(False)
            return
        try:
            import math as _m
            import numpy as _np
            from . import geometry as _G
            f = (s.model.f if s.model and s.model.f
                 else M.focal_px_from_35mm(s.settings.default_focal_35mm,
                                           s.w, s.h))
            # Roll/pitch: from V-line(s) if available, else model.
            if n_v >= 1:
                roll, pitch = s._marker_roll_pitch(f)
            elif s.model and s.model.f:
                roll, pitch, _, _ = W.limit(s.model.roll, s.model.pitch,
                                            s.settings)
            else:
                roll, pitch = 0.0, 0.0
            # Yaw from H-line(s): the line's image direction, un-rotated by
            # roll/pitch, gives the world horizontal bearing.  The yaw that
            # makes it axis-aligned is atan2 of its z/x components after
            # removing the camera tilt.
            cx, cy = s.w / 2.0, s.h / 2.0
            yaw = 0.0
            if n_h >= 1:
                # For each H-line, compute the 3D direction of the line in
                # camera coords (difference of two ray directions), then
                # un-rotate by roll and pitch to get the world direction.
                # The yaw that makes this world direction lie in the x-axis
                # is atan2(w[2], w[0]).
                yaws = []
                for i in range(n_h):
                    seg = s.control_hlines[i]
                    x0, y0, x1, y1 = (float(seg[0]), float(seg[1]),
                                      float(seg[2]), float(seg[3]))
                    # 3D direction of the line in camera coords.
                    r0 = _np.array([(x0 - cx) / f, (y0 - cy) / f, 1.0])
                    r1 = _np.array([(x1 - cx) / f, (y1 - cy) / f, 1.0])
                    d3 = r1 - r0
                    dn = _np.linalg.norm(d3)
                    if dn < 1e-9:
                        continue
                    d3 = d3 / dn
                    # Un-rotate by roll and pitch to get world direction.
                    w = _G.rot_z(-roll) @ _G.rot_x(-pitch) @ d3
                    # The yaw that sends this direction onto the x-axis.
                    yaws.append(_m.atan2(w[2], w[0]))
                if yaws:
                    # Average angles (wrap-aware).
                    yaw = _m.atan2(
                        sum(_m.sin(y) for y in yaws),
                        sum(_m.cos(y) for y in yaws))
            s._hmarker_yaw = yaw
            s._hmarker_active = True
            self.v_yaw.set(_m.degrees(yaw))
            self._yaw_scale.configure(state="normal")
            self._yaw_spin.configure(state="normal")
            label = f"{n_v}V + {n_h}H"
            self._set_status(f"Marker: yaw={_m.degrees(yaw):.1f}° roll="
                             f"{_m.degrees(roll):.1f}° pitch={_m.degrees(pitch):.1f}° ({label})")
            self._schedule_redraw()
        except Exception as e:
            self._set_status(f"H-Marker error: {e}")

    def _on_slider(self):
        self.session.set_manual(roll_deg=self.v_roll.get(), pitch_deg=self.v_pitch.get(),
                                focal_35mm=self.v_focal.get(), yaw_deg=self.v_yaw.get())
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

    # Every drawing tool, and the handler that owns entering and leaving it.
    # `_on_click_before` tests them in a fixed order, so two tools on at once is
    # not "both available" -- it is the earlier one winning every click while the
    # later one looks broken.  That is exactly how the mask brush went dead: SAM
    # is tested first, so leaving SAM on made the brush unreachable.
    _TOOL_MODES = (("v_mark", "_on_mark_toggle"),
                   ("v_rect", "_on_rect_toggle"),
                   ("v_stroke", "_on_stroke_toggle"),
                   ("v_sam", "_on_sam_toggle"))

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
            self._rect_pending = []
            self._loupe_hide()
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
            return
        # Otherwise place the next corner (1→2→3→4).
        i = len(self.session.planar_quad)
        if i >= 4:
            # All four placed; a fifth click clears and starts over.
            self.session.clear_planar()
            self._rect_pending = []
            self._set_status("PC Rectangle cleared -- click corner 1 again")
            self._redraw()
            return
        self.session.set_planar_point(i, fx, fy)
        if len(self.session.planar_quad) >= 4:
            self._loupe_hide()
            self._set_status("PC Rectangle set -- drag any corner to adjust")
        else:
            self._set_status(f"PC Rectangle corner {i + 1}/4 placed -- click the next "
                             f"corner (top-left, top-right, bottom-right, bottom-left)")
        self._redraw()

    def _rect_drag_move(self, event):
        """Nudge the grabbed corner; the rectified preview follows live."""
        if self._rect_drag is None or self.session is None:
            return
        x = (event.x - self._before_off[0]) / self._before_scale
        y = (event.y - self._before_off[1]) / self._before_scale
        self.session.set_planar_point(self._rect_drag, x, y)
        self._redraw()

    def _rect_drag_release(self):
        if self._rect_drag is None:
            return
        self._rect_drag = None
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
        if getattr(self, "v_roi", None) is not None and self.v_roi.get() \
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
        w.place(**dict(zip(("x", "y"), self._loupe_park(size))))
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
        alt = bool(getattr(event, "state", 0) & 0x0008)
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
        cross = "#00e5ff" if alt else "#000000"
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
        # Parked in the middle of the cross, where the gutter is, instead of
        # trailing the cursor across whatever switch happens to be underneath.
        # A fixed place is one you learn once; a wandering one has to be dodged
        # every time.
        c.place(**dict(zip(("x", "y"), self._loupe_park(size))))

    def _on_roi_icon_click(self, event=None):
        """Toggle ROI on/off; apply the strip from the rulers."""
        self._apply_roi()

    def _clear_marks(self):
        if self.session.clear_control_lines() or \
                self.session.clear_control_lines(kind="h"):
            self._sync_from_session()

    def _clear_crop(self):
        if self.session.clear_crop_rect():
            self._refresh_crop()

    def _auto_crop(self):
        """Cut the padded band away instead of inventing something to put in it."""
        if self.session.auto_crop():
            self._refresh_crop()
            kept = (1.0 - self.session.crop_loss()) * 100.0
            self._set_status_extra(f"cropped -- keeps {kept:.0f}% of the frame, "
                                   f"cuts {100.0 - kept:.0f}%")
        else:
            self._set_status_extra("nothing to trim -- the correction opened no "
                                   "band, or the plan had already cropped it")

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
        x = event.x - self._after_off[0]
        y = event.y - self._after_off[1]
        iw, ih = self._ph_a.width(), self._ph_a.height()
        # Crop handles (4 corners + 4 mid-edges) always have dominance.
        grab = self._grab_handle(x, y, iw, ih)
        if grab is not None:
            self._crop_drag_start = grab
            return
        # Reference guides: grab an existing one or add a new one from the
        # 15 px border zone.
        hit = self._after_guide_at(x, y, iw, ih)
        if hit is not None:
            kind, idx = hit
            self._guide_drag = (kind, idx)
            try:
                self.c_after.grab_set()
            except tk.TclError:
                pass
            return
        border = 15
        in_v_zone = (0 <= x < border or iw - border < x <= iw)
        in_h_zone = (0 <= y < border or ih - border < y <= ih)
        if in_v_zone and not in_h_zone:
            self._after_guides.append(("v", max(0.0, min(iw, x))))
            self._draw_after_guides()
            return
        if in_h_zone and not in_v_zone:
            self._after_guides.append(("h", max(0.0, min(ih, y))))
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
        # Border zone (ruler margin): show crosshair as ruler indicator
        in_border = (event.x < RULER_MARGIN or event.y < RULER_MARGIN
                     or event.x > self.c_after.winfo_width() - RULER_MARGIN
                     or event.y > self.c_after.winfo_height() - RULER_MARGIN)
        if in_border:
            cur = "crosshair"
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
        self._refresh_crop()
        self._set_status_extra("crop set" if ok else "crop too small, ignored")

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
        aox = (box[0] - ph.width()) // 2
        aoy = (box[1] - ph.height()) // 2
        self._after_off = (aox, aoy)
        self.c_after.delete("all")
        self._paint_ground(self.c_after)
        self.c_after.create_image(aox, aoy, anchor="nw", image=ph)
        self._ph_a = ph
        self._draw_after_guides()
        self._draw_after_lines(arr, ph.width(), ph.height())
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
        ow, oh = arr.shape[1], arr.shape[0]
        cw = max(1, int(round((x1 - x0) * ow)))
        ch = max(1, int(round((y1 - y0) * oh)))
        self.c_after.create_text(
            8, box[1] - 6, anchor="sw",
            text=f"{ow}×{oh}  {cw}×{ch}",
            fill="#ffffff", font=("Segoe UI", 9))

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
        self.c_after.create_rectangle(x0, y0, x1, y1, outline="#4da3ff",
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
                                          fill="#4da3ff", outline="#0b1017",
                                          width=1, tags=tag)

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
        # A press right on a visible ROI ruler grabs it for dragging rather than
        # falling through to line-picking: the strip is set by hand, so its edges
        # must be reachable wherever they land.  Checked first -- it is the most
        # specific gesture (a click on a drawn line).
        if getattr(self, "v_roi", None) is not None and self.v_roi.get():
            hit = self._roi_ruler_at(event.x, event.y)
            if hit is not None:
                self._on_roi_drag_start(hit)
                return
        if getattr(self, "v_sam", None) is not None and self.v_sam.get():
            self._on_sam_press(event)
            return
        if getattr(self, "v_stroke", None) is not None and self.v_stroke.get():
            self._stroke_start(x, y)
            return
        if getattr(self, "v_mark", None) is not None and self.v_mark.get():
            self._click_mark(x, y, event)
            return
        if getattr(self, "v_rect", None) is not None and self.v_rect.get():
            self._click_rect(x, y, event)
            return
        idx = self.session.pick_line(x, y, display_scale=self._before_scale)
        if idx is None:
            return
        self.session.toggle_line(idx)
        if self.session.mode == AUTO:
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
            self._redraw()
            return
        x0, y0 = self._pending_mark
        self._pending_mark = None
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
        col = ("#00e5ff"
               if self._kind_for(x0, y0, dx, dy) == "v"
               else "#e040fb")
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
        if getattr(self, "_loupe", None) is not None:
            self._loupe_move(event)
        (line_idx, ep_idx), kind = self._mark_drag
        x = event.x - self._before_off[0]
        y = event.y - self._before_off[1]
        self.session.move_control_line_endpoint(line_idx, ep_idx, x, y,
                                                display_scale=self._before_scale,
                                                kind=kind)
        self._schedule_redraw()

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
        det = ttk.Frame(parent)
        det.pack(fill="x", pady=(0, 4))
        self.v_detector = tk.StringVar(value=self.settings.detector)
        ttk.Label(det, text="line detector", width=18).grid(row=0, column=0, sticky="w")
        dbox = ttk.Combobox(det, textvariable=self.v_detector, width=11,
                            state="readonly", values=list(DETECTORS))
        dbox.grid(row=0, column=1, sticky="w", padx=(6, 6))
        dbox.bind("<<ComboboxSelected>>", lambda e: self._apply_detector())
        app = self._app()
        if app is not None:
            app.btn_weights = ttk.Button(det, text="weights...",
                                         command=app._download_models)
            app.btn_weights.grid(row=0, column=2, sticky="w", padx=(6, 0))
            _attach_tooltip(app.btn_weights, "Download or check detector model weights")
        det.columnconfigure(3, weight=1)

        # The facade strip (the former ROI-x control) moved up beside the
        # horizontal (yaw) checkbox in `_build` -- it restricts horizontal
        # evidence and nothing else, so it sits with the switch it shapes.  Its
        # variables are made there, once, for the stale-variable reason written
        # out there.

        # The three tools that used to sit here -- Mask brush, SAM, Mark --
        # are buttons in the picture-corner palette now (2026-09-15, user).
        # What stays is the one thing that is a *setting* rather than a tool:
        # how wide the brush paints.  Its variables are still made in `_build`,
        # once, for the stale-variable reason written out there.
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(0, 4))
        ttk.Label(row, text="brush width", width=18).pack(side="left")
        ttk.Spinbox(row, from_=8, to=160, increment=4, width=3,
                    textvariable=self.v_stroke_w).pack(side="left", padx=(4, 0))

        # Strike slanted moved to the picture-corner palette (2026-09-15, user).

        msk = ttk.Frame(parent)
        msk.pack(fill="x")
        self.v_maskmode = tk.StringVar(value=self.settings.mask_mode)
        ttk.Label(msk, text="mask", width=18).grid(row=0, column=0, sticky="w")
        self.cb_maskmode = ttk.Combobox(msk, textvariable=self.v_maskmode, width=8,
                                        state="readonly",
                                        values=["off", "file", "birefnet", "gdino"])
        self.cb_maskmode.grid(row=0, column=1, sticky="w", padx=(6, 6))
        self.cb_maskmode.bind("<<ComboboxSelected>>", lambda e: self._apply_mask())
        _attach_tooltip(self.cb_maskmode,
                        "Where the ignore mask comes from.\n"
                        "off - none.  file - one PNG per photograph.\n"
                        "birefnet - cut the subject out automatically.\n"
                        "gdino - say what to find, see the prompt below.\n"
                        "gdino finds the building by name, then mattes inside "
                        "that box with BiRefNet - so it needs a BiRefNet model "
                        "set below, or it refuses.")
        _b = ttk.Button(msk, text="mask folder...", command=self._pick_mask_folder)
        _b.grid(row=0, column=2, sticky="w")
        _attach_tooltip(_b, "Choose the folder containing mask PNG files (one per image)")
        # True, because `_apply_mask` defaults `_mask_enabled` to True: a box
        # drawn unchecked while masks were in fact being applied is a control
        # that lies about the state it reports.
        self.v_mask_active = tk.BooleanVar(value=True)
        self.msk_active_cb = ttk.Checkbutton(msk, text="active",
                        variable=self.v_mask_active,
                        command=self._on_mask_active_toggle)
        self.msk_active_cb.grid(row=0, column=3, sticky="w", padx=(10, 0))
        _attach_tooltip(self.msk_active_cb,
                        "Toggle mask on/off without clearing the painted region")
        self.v_maskinv = tk.BooleanVar(value=self.settings.mask_invert)
        ttk.Checkbutton(msk, text="mask marks what to KEEP",
                        variable=self.v_maskinv, command=self._apply_mask
                        ).grid(row=0, column=4, sticky="w", padx=(10, 0))
        self.v_invert = tk.BooleanVar(value=False)
        _cb = ttk.Checkbutton(msk, text="invert mask",
                              variable=self.v_invert,
                              command=self._on_invert_toggle)
        _attach_tooltip(_cb, "Swap it: what is red becomes the part that "
                             "counts, and the rest is ignored.\n"
                             "Applies to every source at once - brush, SAM, "
                             "gdino, BiRefNet and the facade strip together.")
        _cb.grid(row=0, column=8, sticky="w", padx=(10, 0))
        _b = ttk.Button(msk, text="BiRefNet model...", command=self._pick_birefnet_model)
        _b.grid(row=0, column=5, sticky="w", padx=(10, 0))
        _attach_tooltip(_b, "Select the BiRefNet segmentation model to use")
        _b = ttk.Button(msk, text="Mask Apply", command=self._apply_mask)
        _b.grid(row=0, column=6, sticky="w", padx=(10, 0))
        _attach_tooltip(_b, "Re-apply the current mask to filter detected lines")
        _b = ttk.Button(msk, text="Clear Mask", command=self._clear_mask)
        _b.grid(row=0, column=7, sticky="w", padx=(10, 0))
        _attach_tooltip(_b, "Remove all painted mask and SAM selection")
        self.lbl_mask = ttk.Label(msk, text="", wraplength=760, justify="left")
        self.lbl_mask.grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))
        self.v_alpha = tk.DoubleVar(value=0.28)
        ttk.Label(msk, text="mask opacity", width=18).grid(row=2, column=0, sticky="w")
        ttk.Scale(msk, from_=0.0, to=1.0, variable=self.v_alpha, orient="horizontal",
                  command=lambda _v: self._on_alpha()
                  ).grid(row=2, column=1, columnspan=3, sticky="ew", padx=6)
        # The gdino prompt is the interactive half of that mask mode: type a word,
        # press Enter, and the box (and the matte inside it) is re-found.  It is
        # only read when the source combobox says gdino, so it stays inert in the
        # other three modes rather than needing to be hidden.
        self.v_gdino_prompt = tk.StringVar(value=self.settings.gdino_prompt or "building")
        # The prompt row left this panel (user, 2026-09-15): it belongs to the
        # dinosaur key in the palette, which is where it is asked for now.  A
        # labelled entry three rows away from anything that used it was the only
        # way in.  The variable stays -- `_apply_mask` reads it.
        msk.columnconfigure(3, weight=1)

        # The Lines/Mask/Grid overlay switches used to live here.  They now sit on
        # the image each one draws on -- Lines and Mask in the before pane's
        # top-right, Grid in the after pane's -- built in `_build` beside those
        # canvases (2026-09-13, user-directed).  A switch for an overlay belongs
        # on the picture it changes, not in a box on the far side of the window.

    def _on_before_b1motion(self, event):
        if getattr(self, "_loupe", None) is not None:
            self._loupe_move(event)
        if getattr(self, "v_sam", None) is not None and self.v_sam.get():
            self._on_sam_drag(event)
            return
        if getattr(self, "_rect_drag", None) is not None:
            self._rect_drag_move(event)
            return
        if getattr(self, "v_rect", None) is not None and self.v_rect.get():
            # Rubber band: from the last placed corner to the cursor.  When
            # three corners are down, also preview the closing edge (4→1) so
            # the polygon reads as closed before the fourth click lands.
            self.c_before.delete("rect_rubber")
            quad = self.session.planar_quad if self.session else []
            if quad:
                ox, oy = self._before_off
                s = self._before_scale
                lx, ly = quad[-1]
                self.c_before.create_line(ox + lx * s, oy + ly * s, event.x, event.y,
                                          fill="#5ac37f", width=2, dash=(4, 4),
                                          tags="rect_rubber")
                if len(quad) >= 3:
                    fx, fy = quad[0]
                    self.c_before.create_line(event.x, event.y, ox + fx * s, oy + fy * s,
                                              fill="#5ac37f", width=2, dash=(4, 4),
                                              tags="rect_rubber")
            return
        if getattr(self, "_roi_drag", None) is not None:
            self._on_roi_drag_move(event)
            return
        if getattr(self, "_mark_drag", None) is not None:
            self._on_mark_drag(event)
            return
        if getattr(self, "_pending_mark", None) is not None:
            self._mark_rubber(event)
            return
        if getattr(self, "v_stroke", None) is not None and self.v_stroke.get():
            self._on_stroke_drag(event)

    def _on_before_b1release(self, event):
        if getattr(self, "v_sam", None) is not None and self.v_sam.get():
            self._on_sam_release(event)
            return
        if getattr(self, "_rect_drag", None) is not None:
            self._rect_drag_release()
            return
        if getattr(self, "_roi_drag", None) is not None:
            self._on_roi_drag_release()
            return
        if getattr(self, "_pending_mark", None) is not None and getattr(self, "_mark_moved", False):
            self._mark_commit(event)
            self._loupe_hide()          # a released drag always clears it
            return
        if getattr(self, "_mark_drag", None) is not None:
            self._mark_drag = None
            self._loupe_hide()
            self._sync_from_session()
            return
        if getattr(self, "v_stroke", None) is not None and self.v_stroke.get():
            self._on_stroke_release(event)

    # -- roi x rulers ----------------------------------------------------
    def _roi_strip(self):
        """The ROI strip as fractions of frame width, or None when off/invalid.

        Mirrors `_apply_roi`'s validity test so the rulers and the estimator
        never disagree about whether a strip is in force."""
        v = getattr(self, "v_roi", None)
        if v is None or not v.get():
            return None
        try:
            x0 = float(self.v_roi_x0.get()) / 100.0
            x1 = float(self.v_roi_x1.get()) / 100.0
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

    def _draw_roi_rulers(self):
        """Two draggable vertical rulers on the before pane marking the ROI strip.

        The spinboxes are a numeric fine-tune; these are the visual way to see
        where the strip falls and drag it.  Only the sides *outside* the strip get
        ignored, so they carry the wash -- the strip itself stays clean.  Ruler
        look (stipple, #9fd8ff, tick + label) differs from the reference guides
        on the after pane (`_draw_after_guides`); these are handles, so the lines
        are solid and a touch heavier than a grid line."""
        self.c_before.delete("roi_ruler")
        strip = self._roi_strip()
        if strip is None:
            return
        x0f, x1f = strip
        ox, oy = self._before_off
        iw, ih = self._ph_b.width(), self._ph_b.height()
        px0 = ox + x0f * iw
        px1 = ox + x1f * iw
        wash = dict(fill=INK["field"], stipple="gray50")
        self.c_before.create_rectangle(ox, oy, px0, oy + ih, **wash, tags="roi_ruler")
        self.c_before.create_rectangle(px1, oy, ox + iw, oy + ih, **wash, tags="roi_ruler")
        for pxf in (px0, px1):
            self.c_before.create_line(pxf, oy, pxf, oy + ih, fill="#9fd8ff",
                                      width=2, tags="roi_ruler")
            cy = oy + ih // 2
            self.c_before.create_oval(pxf - 7, cy - 7, pxf + 7, cy + 7,
                                      outline="#9fd8ff", width=2, fill=INK["field"],
                                      tags="roi_ruler")

    def _roi_ruler_at(self, x, y=None):
        """Which ROI ruler (0=left, 1=right) sits near the handle, or None.

        The grab zone is the small circle in the middle of each line, not the
        whole line: a click meant for the picture does not accidentally drag
        the strip."""
        strip = self._roi_strip()
        if strip is None or getattr(self, "_ph_b", None) is None:
            return None
        ox, oy = self._before_off
        iw, ih = self._ph_b.width(), self._ph_b.height()
        cy = oy + ih // 2
        for i, f in enumerate(strip):
            px = ox + f * iw
            if abs(x - px) <= 14:
                if y is None or abs(y - cy) <= 14:
                    return i
        return None

    def _on_roi_drag_start(self, idx):
        self._roi_drag = idx
        try:
            self.c_before.grab_set()
        except tk.TclError:
            pass

    def _on_roi_drag_move(self, event):
        """Move the grabbed ruler, clamped to the frame and to the other edge.

        The strip must stay >= 2 % wide (the same floor `_apply_roi` enforces) so a
        drag can never collapse it into an invalid, ignored state.  Updates the
        spinbox vars live; the refit happens once, on release."""
        if self._roi_drag is None or self.session is None:
            return
        ox = self._before_off[0]
        iw = self._ph_b.width()
        if iw <= 0:
            return
        f = (event.x - ox) / iw
        gap = 0.02
        if self._roi_drag == 0:
            f = min(max(f, 0.0), float(self.v_roi_x1.get()) / 100.0 - gap)
            self.v_roi_x0.set(round(f * 100.0, 1))
        else:
            f = max(min(f, 1.0), float(self.v_roi_x0.get()) / 100.0 + gap)
            self.v_roi_x1.set(round(f * 100.0, 1))
        self._draw_roi_rulers()

    def _on_roi_drag_release(self):
        if self._roi_drag is None:
            return
        self._roi_drag = None
        try:
            self.c_before.grab_release()
        except tk.TclError:
            pass
        self._apply_roi()

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

    # -- brush: right erases, Alt+right sizes the pen ----------------------
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

    def _on_alt_erase_press(self, event):
        """Alt+Left-click erases mask paint (same as right-click)."""
        if not self._brush_live():
            return
        self._stroke_erasing = True
        self._stroke_start(event.x - self._before_off[0],
                           event.y - self._before_off[1])
        return "break"

    def _on_right_press(self, event):
        """Right-click: SAM prompt reset when SAM is on, erase stroke otherwise."""
        if getattr(self, "v_sam", None) is not None and self.v_sam.get():
            self._on_sam_right_click(event)
            return "break"
        return self._on_erase_press(event)

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
                                      outline="", fill="#8b0f14",
                                      tags="stroke_preview")
            return
        flat = [c for p in pts for c in (ox + p[0], oy + p[1])]
        self.c_before.create_line(flat, width=2 * r, fill="#8b0f14",
                                  capstyle="round", joinstyle="round",
                                  tags="stroke_preview")

    # -- drawing ---------------------------------------------------------
    def _schedule_redraw(self):
        if self._busy:
            return
        self._busy = True
        self.after(60, self._redraw)

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
                fill=INK["dim"], font=("Segoe UI", 13))
        self._set_status("no photograph loaded")

    def _redraw(self):
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
                    self.after(120, self._redraw)
                return
            self._redraw_tries = 0
            before = self.session.render_before(max_edge=max(box_b),
                                                show_lines=self.v_show_lines.get())
            # Un-cropped on purpose: `_show_after` bakes a veil over what the crop
            # discards, so the picture keeps one size and one scale for the whole
            # session instead of leaping every time a corner moves.
            after = self.session.render_after(max_edge=max(box_a), apply_crop=False)
            self._after_base = after      # un-veiled, preview-sized; _show_after bakes it
            self._after_box = box_a
            import logging as _log
            _log.getLogger("pc.gui").info(
                "render_after done: shape=%s mean=%.1f",
                getattr(after, "shape", "?"),
                float(after.mean()) if after is not None else -1)
            ph_b, s_b = _to_photo(before, box_b)
            # scale from the *original* image to what is on screen
            self._before_scale = s_b * (before.shape[1] / self.session.w)
            self._before_off = ((box_b[0] - ph_b.width()) // 2,
                                (box_b[1] - ph_b.height()) // 2)
            self.c_before.delete("all")
            self._paint_ground(self.c_before)
            self._sam_selection = None
            self.c_before.create_image(self._before_off[0], self._before_off[1],
                                       anchor="nw", image=ph_b)
            self._ph_b = ph_b
            # No grid on this pane (2026-09-13, user-directed).  The grid is a
            # ruler for judging the *corrected* frame -- a true vertical should
            # run along a grid line -- so it belongs on the after pane only.  On
            # the original it measures nothing and only competes with the lines
            # the detector drew, which is what this pane is for.
            self._draw_marks()
            self._draw_roi_rulers()
            self._draw_sam_prompts()
            # P3: horizontal VP markers on the before pane
            if (self.session.model is not None
                    and self.v_correct_horizontal.get()):
                self._draw_horiz_vp_markers(ph_b.width(), ph_b.height())
            # Q1: original image dimensions, bottom-left.
            self.c_before.create_text(
                8, box_b[1] - 6, anchor="sw",
                text=f"{self.session.w}×{self.session.h}",
                fill="#ffffff", font=("Segoe UI", 9))
            # Rectangle quad LAST: drawn over everything (loupe, marks, guides)
            self._draw_rect_quad()
            self._show_after(self.session.crop_rect)
            self._set_status(self.session.status_text())
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

        def tool(codepoint, var, command, tip, invert=False, lead=False,
                 emoji=False):
            # Inverted is for the mask brush ALONE (2026-09-15, user): it is the
            # one tool whose glyph stands for the thing it paints, so a dark
            # circle on a light key reads as the brush tip itself.  Inverting
            # the whole palette instead made every tool shout equally, which is
            # no emphasis at all.
            fg = INK["field"] if invert else INK["dim"]
            img = _icon_pil(codepoint, layout.tool_glyph(), fg,
                            emoji=emoji, mono=emoji)
            if img is not None:
                self._palette_imgs.append(ImageTk.PhotoImage(img))
            b = tk.Checkbutton(bar, variable=var, command=command,
                               image=(self._palette_imgs[-1] if img is not None else self._palette_blank),
                               text="" if img is not None else chr(0x25CF), compound="center",
                               indicatoron=False, width=side, height=side,
                               padx=0, pady=0, highlightthickness=0,
                               bd=0, relief="flat",
                               font=("Segoe UI", 20), cursor="hand2",
                               background=INK["text"] if invert else INK["field"],
                               foreground=fg,
                               activebackground=INK["dim"] if invert else INK["line"],
                               activeforeground=fg if invert else INK["text"],
                               selectcolor=INK["dim"] if invert else INK["line"])
            # A double gap above the first tool: the load group and the tool
            # group are different kinds of thing, and one skipped pitch says so
            # without a separator line.
            b.pack(side="top", pady=(gap * 2 if lead else gap, 0))
            _attach_tooltip(b, tip)
            self._palette_btns.append(b)
            return b

        # Mark: "#" glyph -- two straight lines crossed, the tool's promise.
        from PIL import ImageDraw as _ID
        gs = layout.tool_glyph()
        _mk_img = Image.new("RGBA", (gs, gs), (0, 0, 0, 0))
        _md = _ID.Draw(_mk_img)
        fg_hex = INK["dim"] + "ff" if len(INK["dim"]) == 7 else INK["dim"]
        lw = max(2, gs // 10)
        m = gs // 4
        # two verticals
        _md.line([(m, 2), (m, gs - 2)], fill=fg_hex, width=lw)
        _md.line([(gs - m, 2), (gs - m, gs - 2)], fill=fg_hex, width=lw)
        # two horizontals
        _md.line([(2, m), (gs - 2, m)], fill=fg_hex, width=lw)
        _md.line([(2, gs - m), (gs - 2, gs - m)], fill=fg_hex, width=lw)
        self._palette_imgs.append(ImageTk.PhotoImage(_mk_img))
        _mk_btn = tk.Checkbutton(bar, variable=self.v_mark,
                                 command=self._on_mark_toggle,
                                 image=self._palette_imgs[-1],
                                 text="", compound="center", indicatoron=False,
                                 width=side, height=side, padx=0, pady=0,
                                 highlightthickness=2,
                                 highlightbackground=INK["field"],
                                 bd=0, relief="flat",
                                 cursor="hand2", background=INK["field"],
                                 activebackground=INK["line"],
                                 selectcolor=INK["accent"])
        _mk_btn.pack(side="top", pady=(gap * 2, 0))
        _attach_tooltip(_mk_btn, "Mark a straight edge by hand -- which way it leans\n"
                                  "decides whether it counts as vertical or horizontal")
        self._palette_btns.append(_mk_btn)
        # Facade rect: four corners → perspective transform.  The icon is a
        # simple rectangle outline, placed directly under the # mark icon.
        _pr_img = Image.new("RGBA", (gs, gs), (0, 0, 0, 0))
        _prd = _ID.Draw(_pr_img)
        _prd.rectangle([m // 2, m // 2, gs - m // 2, gs - m // 2],
                       outline=fg_hex, width=lw)
        self._palette_imgs.append(ImageTk.PhotoImage(_pr_img))
        _pr_btn = tk.Checkbutton(bar, variable=self.v_rect,
                                 command=self._on_rect_toggle,
                                 image=self._palette_imgs[-1],
                                 text="", compound="center", indicatoron=False,
                                 width=side, height=side, padx=0, pady=0,
                                 highlightthickness=2,
                                 highlightbackground=INK["field"],
                                 bd=0, relief="flat",
                                 cursor="hand2", background=INK["field"],
                                 activebackground=INK["line"],
                                 selectcolor=INK["accent"])
        _pr_btn.pack(side="top", pady=(gap, 0))
        _attach_tooltip(_pr_btn, "PC Rectangle: click 4 corners of a facade\n"
                                  "(TL, TR, BR, BL) to rectify it.\n"
                                  "Drag corners to adjust. 5th click clears.")
        self._palette_btns.append(_pr_btn)
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
        self._brush_chk = tool("E91F", self.v_stroke, self._on_stroke_toggle,
                               "Paint a mask over regions to exclude from line "
                               "detection. Right-click or Alt+click to erase.",
                               invert=True)
        # The dinosaur moved up here from the prompt label (user, 2026-09-15),
        # and lost its colours on the way: one coloured glyph among five
        # monochrome ones reads as a mistake, however charming.
        # A toggle, not an action.  In the layer registry gdino is a LAYER, and
        # a layer is on or off -- which is also the answer to "how do I turn it
        # off again", a question an action key cannot answer at all.  It stays
        # out of `_TOOL_MODES`: it is a source of mask, not something you hold,
        # so switching it on must not put the brush down.
        if getattr(self, "v_gdino", None) is None:
            self.v_gdino = tk.BooleanVar(value=False)
        self._gdino_btn = tool(
            "1F996", self.v_gdino, self._on_gdino_toggle,
            "Grounding DINO: find the building by name and mask everything "
            "else.\nRight-click to say what to look for.",
            emoji=True)
        self._gdino_btn.bind("<Button-3>", lambda _e: self._ask_gdino_prompt())
        self._sam_btn = tool("EF3C", self.v_sam, self._on_sam_toggle,
                             "Box-select the subject with SAM; right-click "
                             "clears the prompt")
        # Strike slanted moved here from Q3 (2026-09-15, user): a one-shot
        # action on the before image's line evidence, so it belongs with the
        # other tools that act on the original.
        self._strike_btn = tool("E9A8", None, self._strike_slanted,
                                "Remove all lines that are neither vertical nor horizontal")
        # ROI / Facade strip: dark-light-dark vertical stripes.  Click opens a
        # popup with the two % spinboxes; the rulers on the before pane are the
        # primary interaction.
        if getattr(self, "v_roi", None) is None:
            self.v_roi = tk.BooleanVar(value=False)
            self.v_roi_x0 = tk.DoubleVar(value=20.0)
            self.v_roi_x1 = tk.DoubleVar(value=80.0)
        from PIL import ImageDraw
        _roi_img = Image.new("RGBA", (layout.tool_glyph(),) * 2, (0, 0, 0, 0))
        _d = ImageDraw.Draw(_roi_img)
        gs = layout.tool_glyph()
        # three vertical stripes: text-field-text (inverted: light-dark-light)
        sw = gs // 3
        _dark = INK["field"] + "ff" if len(INK["field"]) == 7 else INK["field"]
        _light = INK["text"] + "ff" if len(INK["text"]) == 7 else INK["text"]
        _d.rectangle([0, 2, sw, gs - 2], fill=_light)
        _d.rectangle([sw, 2, sw * 2, gs - 2], fill=_dark)
        _d.rectangle([sw * 2, 2, gs, gs - 2], fill=_light)
        self._palette_imgs.append(ImageTk.PhotoImage(_roi_img))
        _roi_btn = tk.Checkbutton(bar, variable=self.v_roi,
                                  command=self._on_roi_icon_click,
                                  image=self._palette_imgs[-1],
                                  text="", compound="center", indicatoron=False,
                                  width=side, height=side, padx=0, pady=0,
                                  highlightthickness=0, bd=0, relief="flat",
                                  cursor="hand2", background=INK["field"],
                                  activebackground=INK["line"],
                                  selectcolor=INK["dim"])
        _roi_btn.pack(side="top", pady=(gap, 0))
        _attach_tooltip(_roi_btn, "Facade strip (ROI): restrict horizontal\n"
                                  "evidence to one facade on corner views.")
        self._palette_btns.append(_roi_btn)
        # Planar left the palette (2026-09-14, "less is more"). The quad was
        # being asked to do two unrelated jobs -- rectify a flat face, and state
        # which plane the lines belong to -- and the second is answered better,
        # and with far less UI, by marking the lines themselves: a vertical and
        # a horizontal mark say the same thing about a facade as four dragged
        # corners, and they are the tool that already exists. The module and its
        # tests stay in the tree; nothing in the window points at them.

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
        ox, oy = self._after_off
        for x0, y0, x1, y1 in seg:
            dx, dy = x1 - x0, y1 - y0
            if abs(dy) >= abs(dx):     # vertical-ish: the ones being straightened
                colour = "#39ff7a" if abs(dx) <= 1.5 else "#ff5a5a"
            else:
                colour = "#39ff7a" if abs(dy) <= 1.5 else "#ffb03a"
            self.c_after.create_line(ox + x0, oy + y0, ox + x1, oy + y1,
                                     fill=colour, width=1, tags="after_lines")

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
        iw, ih = self._ph_a.width(), self._ph_a.height()
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
            col = "#00e5ff" if active_v else "#ffb300"
            self.c_before.create_line(ox + x0, oy + y0, ox + x1, oy + y1,
                                      fill=col, width=lw)
            for ex, ey in ((x0, y0), (x1, y1)):
                hx, hy = inside(ox + ex, oy + ey, 4)
                self.c_before.create_oval(hx - 4, hy - 4, hx + 4, hy + 4,
                                          fill=col, outline="")
            mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            self._draw_delete_handle(*inside(ox + mx, oy + my, 8), col,
                                     tag=f"mark_del_v_{i}")
        active_h = len(self.session.control_hlines) >= 2
        for i, (x0, y0, x1, y1) in enumerate(
                self.session.control_lines_for_display(
                    self._before_scale, kind="h")):
            col = "#e040fb" if active_h else "#ce93d8"
            self.c_before.create_line(ox + x0, oy + y0, ox + x1, oy + y1,
                                      fill=col, width=lw, arrow="both",
                                      arrowshape=(9, 11, 4))
            for ex, ey in ((x0, y0), (x1, y1)):
                hx, hy = inside(ox + ex, oy + ey, 4)
                self.c_before.create_oval(hx - 4, hy - 4, hx + 4, hy + 4,
                                          fill=col, outline="")
            mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            self._draw_delete_handle(*inside(ox + mx, oy + my, 8), col,
                                     tag=f"mark_del_h_{i}")
        pend = getattr(self, "_pending_mark", None)
        if pend is not None:
            px, py = ox + pend[0], oy + pend[1]
            # One point leans no way yet, so it wears neither plane colour.
            self.c_before.create_oval(px - 6, py - 6, px + 6, py + 6,
                                       outline="#9e9e9e", width=2)

    def _draw_rect_quad(self):
        """Draw the four PC Rectangle corners and connecting edges on the before pane."""
        quad = self.session.planar_quad if self.session else []
        if not quad:
            return
        ox, oy = self._before_off
        s = self._before_scale
        pts = [(ox + px * s, oy + py * s) for px, py in quad]
        col = "#5ac37f"
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
                                      fill=col, font=("TkDefaultFont", 8),
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

    # -- SAM2 box-prompt segmentation --------------------------------------
    def _on_gdino_toggle(self):
        """Switch the Grounding DINO layer on or off.

        It drives the same combobox a user would, so there is one path into
        `_apply_mask` and the control panel keeps saying what is actually in
        force rather than disagreeing with the key.
        """
        if not self.session or getattr(self, "v_maskmode", None) is None:
            return
        self.v_maskmode.set("gdino" if self.v_gdino.get() else "off")
        self._apply_mask()

    def _ask_gdino_prompt(self):
        """Right-click: say what to look for, then go and look for it.

        The prompt used to be a labelled entry in the mask panel, three rows
        away from anything that used it.  It belongs to this key, so it is asked
        for from this key -- and asking is also applying, because a prompt you
        typed and did not apply is a note to yourself.
        """
        if not self.session or getattr(self, "v_gdino_prompt", None) is None:
            return "break"
        from tkinter import simpledialog
        answer = simpledialog.askstring(
            "Grounding DINO",
            "What should it look for?\n(a plain word: building, facade, house)",
            initialvalue=self.v_gdino_prompt.get(), parent=self)
        if not answer:
            return "break"              # cancelled: leave the layer as it was
        self.v_gdino_prompt.set(answer.strip())
        self.v_gdino.set(True)
        self._on_gdino_toggle()
        return "break"

    def _on_sam_toggle(self):
        """Entering or leaving box-select; the caller owns the flip.

        It used to flip `v_sam` itself, which was fine while a plain Button
        was the only way in.  A palette toggle *button* carries the variable,
        so Tk flips it first and a second flip here would cancel it out and
        the tool would look dead.  Read-only, like the other three.
        """
        if self.v_sam.get():
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
                                       outline="#5ac37f", width=2, tags="sam_prompts")

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
        if (cx1 - cx0) < 5 or (cy1 - cy0) < 5:
            return  # too small, ignore
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

        def _run():
            try:
                log.info("SAM apply: box_px=%s pts=%d image=%s",
                         list(box_px), len(pts or ()),
                         os.path.basename(self.session.path))
                png = sam2seg.run_subprocess(
                    self.session.path, box_px, pts,
                    ckpt=getattr(self.session.settings, "sam_model", ""),
                    device=getattr(self.session.settings, "sam_device", ""))
                log.info("SAM apply done: %s", os.path.basename(png))
                self.after(0, lambda p=png: self._on_sam_done(p))
            except Exception as exc:
                log.exception("SAM apply failed")
                self.after(0, lambda e=exc: self._on_sam_fail(e))

        import threading
        threading.Thread(target=_run, daemon=True).start()

    def _on_sam_done(self, png_path):
        self._busy_sam = False
        self._set_busy(False)
        from . import sam2seg
        ignore = sam2seg.load_mask_png(png_path, (self.session.h, self.session.w))
        self._sam_selection = ~ignore
        # Hand the result to the session, which is the whole point of selecting
        # the building: until this call the segment only drew a green outline
        # and a percentage, and the estimator never saw it.  `apply_sam_mask`
        # resizes to analysis-res and refits.
        self.session.apply_sam_mask(ignore)
        self._sync_from_session()
        self._draw_sam_prompts()
        frac = self._sam_selection.mean() if self._sam_selection is not None else 0
        self._set_status(f"SAM selection: {frac:.1%} of frame")

    def _on_sam_fail(self, exc):
        self._busy_sam = False
        self._set_busy(False)
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
                                           outline="#5ac37f", width=2, tags="sam_prompts")
        # Points
        for px, py, pos in (getattr(self, '_sam_points', None) or []):
            col = "#5ac37f" if pos else "#ff5555"
            self.c_before.create_oval(ox + px * s - 4, oy + py * s - 4,
                                      ox + px * s + 4, oy + py * s + 4,
                                      fill=col, outline="", tags="sam_prompts")
        # Selection indicator: green border in the off-border area
        sel = getattr(self, "_sam_selection", None)
        if sel is not None and sel.any():
            cw = self.c_before.winfo_width()
            ch = self.c_before.winfo_height()
            self.c_before.create_rectangle(2, 2, cw - 2, ch - 2,
                                           outline="#5ac37f", width=3,
                                           tags="sam_prompts")

    def _on_sam_right_click(self, event):
        if not self.v_sam.get():
            return
        self._sam_box = None
        self._sam_box_px = None
        self._sam_points = []
        self._sam_selection = None
        self.c_before.delete("sam_prompts")

    def _set_status(self, text):
        lbl = getattr(self, "lbl_status", None)
        if lbl is not None:
            lbl.configure(text=text or "")

    def _set_status_extra(self, text):
        self._set_status(text)

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
        # The file that gets written must use the ComfyUI settings as they are
        # now, not as they were when this window opened.
        self._sync_comfy()
        try:
            # The planar branch went with the tool (user, 2026-09-15). It tested
            # `v_planar`, which no longer exists, so `getattr` returned None and
            # the branch was already unreachable -- dead weight reading as a
            # decision. `session.save_planar` stays in review.py with its tests.
            self.session.save(dst)
        except Exception as exc:
            messagebox.showerror("Save", str(exc), parent=self)
            return
        if self.on_saved:
            self.on_saved(self.session.path, dst)
        self._fire_closed()

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
            self.session.save(dst)
        except Exception as exc:
            messagebox.showerror("Save As", str(exc), parent=self)
            return
        if self.on_saved:
            self.on_saved(self.session.path, dst)
        self._fire_closed()

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


# ==========================================================================
# batch window
# ==========================================================================
class App(_ROOT_CLASS):
    def __init__(self, initial=None, start_maximized=True):
        super().__init__()
        self.title(f"Perspective Correction  v{__version__}")
        # The window opens maximized, so this is the size it *restores* to.
        # It used to be a hardcoded 1920x1080, which is too small on a 1440p or
        # 4K monitor and larger than the screen in both directions on a
        # 1366x768 laptop. `layout.initial_window` takes a share of the real
        # screen instead and clamps it both ways; it is pure arithmetic and
        # tested at five resolutions in tests/test_layout.py. F11 toggles
        # borderless fullscreen for the review work.
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        gw, gh = layout.initial_window(sw, sh)
        self.geometry(f"{gw}x{gh}")
        # Floor raised to 1920x1080 (user, 2026-09-13): below that the review
        # controls under-subscribe -- the angle columns and status box clip.
        # Note this alone does NOT fit the single-row Save/queue buttons: at
        # 1920 wide the controls field is ~910px while that row needs ~1791px,
        # so those stay unreachable until the btns sub-row split lands.
        self.minsize(1920, 1080)
        apply_theme(self)
        self._last_applied_theme = "Minimal Black"
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
        self._fullscreen = False
        self.bind("<F11>", lambda _e: self._toggle_fullscreen())
        self.bind("<m>", lambda _e: self._tool_key("v_mark", "_on_mark_toggle"))
        self.bind("<b>", lambda _e: self._tool_key("v_stroke", "_on_stroke_toggle"))
        self.bind("<s>", lambda _e: self._tool_key("v_sam", "_on_sam_toggle"))
        self.after(120, self._pump)
        if start_maximized:
            self.after(50, self._maximize)

    def _maximize(self):
        try:
            self.state("zoomed")
        except tk.TclError:          # platforms without a zoomed state
            pass

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
        # outlive it: switch Phosphor -> Light and the light panes would keep
        # the dark ramp. A palette without a gradient has to say so.
        if "grad" not in new:
            INK.pop("grad", None)
        apply_theme(self, new)
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
        # The brand mark is a rendered image too, tinted INK["text"] into its
        # pixels at build time and never re-rendered.
        for bar in getattr(self, "_brand_bars", []):
            lbl = getattr(bar, "_brand_lbl", None)
            if lbl is None:
                continue
            mark = _logo_image(getattr(bar, "_brand_size", 24), INK["text"])
            if mark is None:
                continue
            try:
                photo = ImageTk.PhotoImage(mark)
                lbl.configure(image=photo)
                lbl.image = photo
            except tk.TclError:
                pass

    def _build(self):
        pad = dict(padx=6, pady=2)
        _, self._theme_var, self._theme_combo = _brand_header(self, on_select=self._switch_theme)

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
        self.review.pack(fill="both", expand=True, padx=8, pady=(0, 4))

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

        # The batch options are the lower-right field's own business, not a
        # strip under the cross: packed side="bottom" they hold the foot of the
        # controls whatever `load` rebuilds above them.
        # Titled, because the same words appear twice in this window and nothing
        # used to say which was which: "detector", "mask" and "fill" live here
        # *and* in the panel above.  These are the values every photograph opens
        # with (`_settings()` feeds them to each `review.load`); the panel's
        # copies override them for the photograph on screen and nothing else.
        opt = ttk.Labelframe(self.review.cell_ui, padding=(14, 4, 14, 8),
                             text="defaults every photograph opens with")
        opt._bpc_persistent = True
        self._w_opt = opt
        self.v_strength = tk.DoubleVar(value=1.0)
        self.v_conf = tk.DoubleVar(value=Settings.min_confidence)
        self.v_maxpitch = tk.DoubleVar(value=Settings.max_pitch_deg)
        self.v_crop = tk.StringVar(value=Settings.crop)
        self.v_recursive = tk.BooleanVar(value=False)
        self.v_overwrite = tk.BooleanVar(value=False)
        self.v_review = tk.BooleanVar(value=True)
        # The detector combobox and weights button moved to the lower-left
        # tools field (_build_tools) beside the input image.  App.v_detector
        # is set by the review panel's combobox; _settings() reads it back.
        self.v_detector = tk.StringVar(value=Settings.detector)
        self.v_jpegq = tk.IntVar(value=int(self._remembered.get("jpeg_quality", Settings.jpeg_quality)))
        self.v_jpegq.trace_add("write", lambda *_a: prefs.save(jpeg_quality=self.v_jpegq.get()))
        self._spin(opt, 0, 4, "max pitch (deg)", self.v_maxpitch, 0.0, 45.0, 1.0)
        self._spin(opt, 1, 0, "strength", self.v_strength, 0.0, 1.0, 0.05)
        ttk.Label(opt, text="crop").grid(row=1, column=4, sticky="e", padx=4)
        ttk.Combobox(opt, textvariable=self.v_crop,
                     values=["auto", "aspect", "inside", "none"],
                     width=8, state="readonly").grid(row=1, column=5, sticky="w")
        self._spin(opt, 2, 0, "min confidence", self.v_conf, 0.0, 1.0, 0.05)
        self._spin(opt, 2, 4, "jpeg quality", self.v_jpegq, 10, 100, 1)

        ttk.Label(opt, text="mask").grid(row=0, column=7, sticky="e", padx=4)
        self.v_mask = tk.StringVar(value=Settings.mask_mode)
        mask_cb = ttk.Combobox(opt, textvariable=self.v_mask,
                               values=["off", "file", "birefnet", "gdino"],
                               width=6, state="readonly")
        mask_cb.grid(row=0, column=8, sticky="w")
        mask_cb.bind("<<ComboboxSelected>>", self._on_mask_mode)
        self.v_maskpath = tk.StringVar(value="")
        _b = ttk.Button(opt, text="mask source...", command=self._pick_mask_source)
        _b.grid(row=0, column=9, sticky="w", padx=(6, 0))
        _attach_tooltip(_b, "Choose the folder containing mask PNG files (one per image)")
        # Generating the band a rotation opens up is off by default and says so
        # when it cannot run: a batch that quietly writes padded frames because
        # the backend was missing is the silent failure this tool avoids.
        ttk.Label(opt, text="fill gaps").grid(row=1, column=7, sticky="e", padx=4)
        self.v_fill = tk.StringVar(value=Settings.fill)
        fbox = ttk.Combobox(opt, textvariable=self.v_fill,
                            values=["none", "telea", "lama", "comfyui"],
                            width=11, state="readonly")
        fbox.grid(row=1, column=8, sticky="w")
        fbox.bind("<<ComboboxSelected>>", lambda e: self._check_fill())
        # The ComfyUI address and workflow used to be reachable only from the
        # Setup menu; with the menus gone they are one button away from the
        # fill mode that needs them.  It opens the same small settings window
        # as before -- there is one server, so there is one set of settings.
        _b = ttk.Button(opt, text="server...", command=self._open_comfy)
        _b.grid(row=1, column=9, sticky="w", padx=(6, 0))
        _attach_tooltip(_b, "Configure the ComfyUI server address and inpainting workflow")
        ttk.Checkbutton(opt, text="subfolders", variable=self.v_recursive
                        ).grid(row=2, column=7, sticky="w")
        ttk.Checkbutton(opt, text="overwrite originals", variable=self.v_overwrite
                        ).grid(row=2, column=8, columnspan=2, sticky="w")

        self.lbl_fill = ttk.Label(opt, text="", style="Dim.TLabel", wraplength=900,
                                  justify="left")
        self.lbl_fill.grid(row=3, column=0, columnspan=12, sticky="w", pady=(4, 0))

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
        ttk.Checkbutton(opt, text="offer manual review for unclear images",
                        variable=self.v_review).grid(row=2, column=0, columnspan=3, sticky="w")

        bar = ttk.Frame(self.review.cell_ui, padding=8)
        bar._bpc_persistent = True
        self._w_bar = bar
        # Manual review is the product (2026-09-13, see "Project in one paragraph"),
        # so the prominent gesture is the one that *asks*: it opens one window per
        # photograph and writes only what was confirmed by hand. The unattended run
        # stays reachable for anyone who wants it, demoted rather than removed --
        # nothing about it changed except that it is no longer the default.
        self.btn_run = ttk.Button(bar, text="Review each", command=self._review_each,
                                  style="Accent.TButton")
        self.btn_run.pack(side="left")
        _attach_tooltip(self.btn_run, "Open one review window per photograph; write only what was confirmed by hand")
        self.btn_batch = ttk.Button(bar, text="Unattended", command=self._start)
        self.btn_batch.pack(side="left", padx=6)
        _attach_tooltip(self.btn_batch, "Run the batch automatically without manual review")
        self.btn_stop = ttk.Button(bar, text="Stop", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=6)
        _attach_tooltip(self.btn_stop, "Abort the current batch run")
        self.progress = ttk.Progressbar(bar, mode="determinate")
        self.lbl_count = ttk.Label(bar, text="", style="Value.TLabel")
        # The batch output destination moved here from the loader field: an output
        # concern that belongs with the run controls, and the options frame above has
        # no vertical headroom for another row (the cross fixes the field height). It
        # rides this bar's existing height. No "Output" caption -- at 1280 the bar is
        # already full of buttons, and the path the entry holds says enough.
        _browse_btn = ttk.Button(bar, text="Browse", command=self._pick_out)
        self._w_out = [
            ttk.Entry(bar, textvariable=self.v_output, width=15),
            _browse_btn]
        _attach_tooltip(_browse_btn, "Choose the output folder for corrected images")
        # Pack order is allocation priority in Tk: when the bar runs out of room at
        # 1280 the *last* widget packed is the one that gets clipped.  So the path
        # field packs before the progress bar and keeps its width; the progress bar
        # is the expander, so it is what gives way (the count label and the results
        # tree already report progress).
        self._w_out[0].pack(side="right", padx=(6, 6))
        self._w_out[1].pack(side="right")
        self.lbl_count.pack(side="right")
        self.progress.pack(side="left", fill="x", expand=True, padx=10)
        # Start row at the very foot of the field, options above it.  Both are
        # packed side="bottom", so the frame `load` rebuilds -- packed
        # side="top" with expand -- can never push them out of place.
        bar.pack(side="bottom", fill="x")
        opt.pack(side="bottom", fill="x")

        # The results list is the lower half of the loader field: files and
        # what happened to them in one place, under the list that names them.
        self._w_tree = ttk.Frame(self.review.loader)
        cols = ("status", "file", "roll", "pitch", "conf", "note")
        self.tree = ttk.Treeview(self._w_tree, columns=cols, show="headings", selectmode="browse")
        for c, w in zip(cols, (80, 320, 70, 70, 60, 380)):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True)
        self._w_tree.pack(fill="both", expand=True, pady=(6, 0))
        self.tree.bind("<Double-1>", lambda e: self._review_selected())
        for status, colour in STATUS_COLOUR.items():
            self.tree.tag_configure(status, foreground=colour)

        # Built once and kept withdrawn: the popup costs nothing on screen
        # until it is opened, and the server can be configured before there
        # is any work to do.
        self._build_comfy_popup()

    def _spin(self, parent, r, c, label, var, lo, hi, step):
        ttk.Label(parent, text=label).grid(row=r, column=c, sticky="e", padx=4)
        ttk.Spinbox(parent, textvariable=var, from_=lo, to=hi, increment=step,
                    width=7).grid(row=r, column=c + 1, sticky="w")

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

    def _on_mask_mode(self, _e=None):
        """The mode is setup, not a per-run decision: store it together with
        its path, so a restart finds the same mask the user left behind.  The
        path field keeps whichever path belongs to the newly chosen mode."""
        prefs.save(mask_mode=self.v_mask.get())
        key = "birefnet_model" if self.v_mask.get() in ("birefnet", "gdino") else "mask_file"
        remembered = self._remembered.get(key, "")
        if remembered:
            self.v_maskpath.set(remembered)

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
            text = "fill will FAIL on every image -- " + text
        self.lbl_fill.configure(text=text)

    def _download_models(self):
        """Fetch the DeepLSD weights (98 MB) into models/.

        The button label is the progress bar: a download is the one thing a
        user will not start twice, so the second click is refused and the label
        carries the megabytes instead of a second widget.  It used to be a menu
        entry; with the menus gone it lives next to the detector that needs it.
        """
        if getattr(self, "_dl_busy", False):
            return
        from . import deeplsd as DL
        self._dl_busy = True
        item = self.btn_weights

        def finish(text, is_error):
            item.configure(text="weights...")
            self._dl_busy = False
            (messagebox.showerror if is_error else messagebox.showinfo)(
                "Download", text)

        def work():
            try:
                DL.download_weights(
                    lambda d, t: self.after(0, lambda d=d, t=t: item.configure(
                        text=f"downloading… {d >> 20} MB"
                             + (f" / {t >> 20} MB" if t else ""))))
                self.after(0, lambda: finish("DeepLSD weights ready in models/", False))
            except Exception as exc:
                self.after(0, lambda: finish(str(exc), True))

        threading.Thread(target=work, daemon=True).start()

    # -- the ComfyUI dock ------------------------------------------------
    def _build_comfy_popup(self):
        """The ComfyUI controls, in a small window that stays out of the way.

        They were docked along the bottom of this window, which cost four
        permanent rows for one of four fill modes -- visible to everyone who
        never fills a band, and gone from no one.  Before that they were a
        Toplevel, and before that a row inside the options panel; the panel is
        hidden until a folder is loaded, so the server could not be configured
        *before* the work -- which is the only time anyone wants to.

        The answer is a Toplevel built once at startup and kept withdrawn: it
        costs nothing on screen until opened, and closing hides rather than
        destroys, so the verdict and the model lists survive a round trip.
        The StringVars stay on the App, so `_comfy_open`, `_show_comfy_state`,
        `_fill_model_lists` and the queue path are untouched.
        """
        win = tk.Toplevel(self)
        win.configure(bg=INK["bg"])
        win.title("ComfyUI settings")
        win.transient(self)
        win.protocol("WM_DELETE_WINDOW", win.withdraw)
        self._w_comfy = win
        body = ttk.Frame(win, padding=(10, 8))
        body.pack(fill="both", expand=True)

        addr = ttk.Frame(body)
        addr.pack(fill="x")
        ttk.Label(addr, text="server").pack(side="left")
        self.ent_comfy_host = ttk.Entry(addr, textvariable=self.v_comfy_host, width=20)
        self.ent_comfy_host.pack(side="left", padx=(6, 2))
        ttk.Entry(addr, textvariable=self.v_comfy_port, width=6).pack(side="left")
        self.btn_comfy_test = ttk.Button(addr, text="Test connection",
                                         command=self._test_comfy)
        self.btn_comfy_test.pack(side="left", padx=8)
        _attach_tooltip(self.btn_comfy_test, "Check that the ComfyUI server is reachable and the workflow loads")
        self.lbl_comfy_state = tk.Label(body, text="not checked",
                                        background=INK["bg"], foreground=INK["dim"])
        self.lbl_comfy_state.pack(anchor="w")

        # A list, not a browse button.  The graph decides what the fill *is*,
        # and the unset case silently took the inpainting one and ran an edit
        # model through it, at a green light, because every checkpoint it named
        # was installed.  Naming the choice is the fix; see `inpaint.SHIPPED`.
        wfr = ttk.Frame(body)
        wfr.pack(fill="x", pady=(8, 0))
        ttk.Label(wfr, text="workflow").pack(anchor="w")
        self.cb_comfy_wf = ttk.Combobox(wfr, textvariable=self.v_comfy_wf_pick,
                                        width=46, state="readonly")
        self.cb_comfy_wf.pack(fill="x", pady=(2, 0))
        self.cb_comfy_wf.bind("<<ComboboxSelected>>",
                              lambda e: self._on_comfy_workflow_pick())
        self.lbl_comfy_wf = ttk.Label(wfr, text="", style="Dim.TLabel")
        self.lbl_comfy_wf.pack(anchor="w")

        # The three files a workflow names, chosen from what the server has.
        # `resolve_models` guesses well enough when one candidate is obviously
        # the same file under another name, and not at all when a machine has
        # forty-six text encoders installed -- which is the normal case, and the
        # reason these are a selector rather than a message.  Stacked, because
        # the popup is narrow where the dock was wide.
        mrow = ttk.Frame(body)
        mrow.pack(fill="x", pady=(8, 0))
        self.cb_comfy_models = {}
        for label, key in (("model", "comfy_unet"), ("clip", "comfy_clip"),
                           ("vae", "comfy_vae")):
            row = ttk.Frame(mrow)
            row.pack(fill="x")
            ttk.Label(row, text=label, width=6).pack(side="left")
            box = ttk.Combobox(row, textvariable=self.v_comfy_models[key],
                               width=40, state="readonly",
                               values=["(from the workflow)"])
            box.pack(side="left", fill="x", expand=True)
            self.cb_comfy_models[key] = box

        self.lbl_comfy_detail = ttk.Label(body, text="", style="Dim.TLabel",
                                          wraplength=440, justify="left")
        self.lbl_comfy_detail.pack(fill="x", pady=(8, 0))

        self._sync_comfy_workflow_label()
        self._fill_model_lists(self._comfy_models_cache)
        self._show_comfy_state(*self._comfy_state)
        win.withdraw()

    def _open_comfy(self):
        """Open the ComfyUI settings window.

        Kept as a name because three callers mean "let them at the ComfyUI
        settings": the menu, the fill selector, and a review window's button.
        The window is built once and kept withdrawn, so this only shows it --
        the verdict and model lists from the last test are still there.
        """
        try:
            self._w_comfy.deiconify()
            self._w_comfy.lift()
            self.ent_comfy_host.focus_set()
        except Exception:                     # a torn-down or headless window
            pass

    def _comfy_open(self):
        """Whether the controls exist to be drawn into.

        Always true once `_build` has run, and false during construction and
        teardown -- which is what the `_show_comfy_state` and
        `_sync_comfy_workflow_label` guards are actually asking.
        """
        return getattr(self, "lbl_comfy_state", None) is not None

    CHOOSE_A_FILE = "choose a file..."

    def _comfy_workflow_choices(self):
        """``([label, ...], {label: path})`` -- the shipped graphs, then a file.

        The path is absolute so the choice is a *choice*: once it is stored,
        `inpaint.workflow_path` reports ``chosen`` and the indicator stops
        saying nobody picked one.
        """
        from . import inpaint as FILL
        labels, paths = [], {}
        for name, what in FILL.SHIPPED:
            label = f"{what}  [{name}]"
            labels.append(label)
            paths[label] = os.path.join(FILL.WORKFLOWS, name)
        current = self.v_comfy_wf.get()
        if current and current not in paths.values():
            label = os.path.basename(current)
            labels.append(label)
            paths[label] = current
        labels.append(self.CHOOSE_A_FILE)
        return labels, paths

    def _sync_comfy_workflow_label(self):
        """Show which graph is in force -- by name, and never as 'shipped'.

        The old label said "shipped workflow" while two of them ship, which is
        the whole defect: it read as an answer and named nothing.
        """
        if not self._comfy_open():
            return
        labels, paths = self._comfy_workflow_choices()
        self.cb_comfy_wf.configure(values=labels)
        current = self.v_comfy_wf.get()
        from . import inpaint as FILL
        if not current:
            # Nobody chose. Say so where the choice is made, not only in the
            # detail line -- a blank box reads as "fine".
            self.v_comfy_wf_pick.set("")
            self.lbl_comfy_wf.configure(
                text=f"not chosen -- {os.path.basename(FILL.DEFAULT_WORKFLOW)} will run")
            return
        for label, path in paths.items():
            if path == current:
                self.v_comfy_wf_pick.set(label)
                break
        self.lbl_comfy_wf.configure(text="")

    def _on_comfy_workflow_pick(self):
        labels, paths = self._comfy_workflow_choices()
        picked = self.v_comfy_wf_pick.get()
        if picked == self.CHOOSE_A_FILE:
            # A cancelled dialog must not leave the box showing the sentinel.
            if not self._pick_comfy_workflow():
                self._sync_comfy_workflow_label()
            return
        self._use_comfy_workflow(paths.get(picked, ""))

    def _pick_comfy_workflow(self):
        """The API export, not the editor export.

        ``inpaint.load_workflow`` tells the two apart and says which one it got,
        because posting an editor export to ``/prompt`` is the mistake everyone
        makes once and the error it produces on its own is unreadable.
        """
        p = filedialog.askopenfilename(
            title="ComfyUI workflow (API format)", parent=self,
            initialdir=self._comfy_workflow_dir(),
            filetypes=[("ComfyUI API workflow", "*.json"), ("All files", "*.*")])
        if not p:
            return False
        self._use_comfy_workflow(p)
        return True

    def _comfy_workflow_dir(self):
        from . import inpaint as FILL
        current = self.v_comfy_wf.get()
        return os.path.dirname(current) if current else FILL.WORKFLOWS

    def _use_comfy_workflow(self, path):
        if not path:
            return
        self.v_comfy_wf.set(path)
        self._sync_comfy_workflow_label()
        prefs.save(comfy_workflow=path)    # an address, not a correction setting
        # The workflow is half of what "connected" means -- a reachable server
        # with an unusable graph is not a working fill -- so the verdict is
        # stale the moment it changes.
        self._show_comfy_state("unknown", "the workflow changed -- press Test connection")
        self._check_fill()

    def _comfy_url(self):
        return _join_url(self.v_comfy_host.get(), self.v_comfy_port.get())

    def _remember_model(self, key):
        """A chosen checkpoint is an address, like the mask folder."""
        value = self.v_comfy_models[key].get()
        if value and not value.startswith("("):
            prefs.save(**{key: value})

    def _fill_model_lists(self, models):
        """Offer what the server reported, keeping any choice still valid.

        The first entry is always "(from the workflow)" -- the default, and the
        only honest label for it. Naming a file the user did not pick would make
        the selector claim a decision nobody made, and the workflow's own value
        is what runs until they do.  Silently does nothing when the window is
        shut: the choices live on the App and outlive it.
        """
        if not self._comfy_open():
            return
        for key, box in self.cb_comfy_models.items():
            names = models.get(key) or []
            box.configure(values=["(from the workflow)"] + names)
            current = self.v_comfy_models[key].get()
            if current and not current.startswith("(") and current in names:
                continue                          # a still-valid choice survives
            self.v_comfy_models[key].set("(from the workflow)")

    def _show_comfy_state(self, state, text):
        """Remembered on the App, drawn only when the window is open."""
        self._comfy_state = (state, text)
        label, ink = self.COMFY_LIGHTS.get(state, self.COMFY_LIGHTS["down"])
        if self._comfy_open():
            self.lbl_comfy_state.configure(text=label, foreground=INK[ink])
            self.lbl_comfy_detail.configure(text=text)
        self.lbl_fill.configure(text=f"ComfyUI: {label} -- {text}")
        for listen in list(self._comfy_listeners):
            try:
                listen(state, text)
            except Exception:     # a dead review window must not take this one down
                self._comfy_listeners.remove(listen)

    def _test_comfy(self):
        """Ask the server whether it is there, off the UI thread.

        ``describe`` does two network round trips with a three second timeout
        each, and doing that inline freezes the window mid-click -- which reads
        as a crash, not as a slow server.  The button says it is working and
        comes back either way; a check that cannot fail visibly is not a check.
        """
        if self._comfy_open():
            self.btn_comfy_test.configure(state="disabled")
            self.lbl_comfy_state.configure(text="checking...", foreground=INK["dim"])
        self.lbl_fill.configure(text="ComfyUI: asking...")
        settings = self._settings().replace(fill="comfyui")

        def work():
            from . import inpaint as FILL
            models = {}
            try:
                state, text = FILL.status(settings)
                if state != "down":
                    # Same round trip answers both questions, so ask once.
                    models = FILL.model_options(settings.comfy_url)
            except Exception as exc:                     # never take the window down
                state, text = "down", f"comfyui check failed: {exc}"
            # Through the queue the batch run already uses, not `after` from
            # here: Tk is not thread-safe, and registering a callback from a
            # worker raises "main thread is not in main loop" outright.
            self.queue.put(("comfy", (state, text, models)))

        threading.Thread(target=work, daemon=True).start()

    # Three lights, because "up but running on a guessed checkpoint" is neither
    # of the other two: green would hide it, red would refuse something that
    # works.
    COMFY_LIGHTS = {"ok":      ("connected", "ok"),
                    "models":  ("models missing", "warn"),
                    "down":    ("disconnected", "err"),
                    # Not a verdict.  "disconnected" for an address nobody has
                    # asked yet would be a claim, and a wrong one.
                    "unknown": ("not checked", "dim")}

    def _comfy_result(self, state, text, models=None):
        if self._comfy_open():
            self.btn_comfy_test.configure(state="normal")
        self._comfy_models_cache = models or self._comfy_models_cache
        self._fill_model_lists(self._comfy_models_cache)
        self._show_comfy_state(state, text)
        if state != "down":
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
        for p in paths:
            p = os.path.abspath(p)
            if not os.path.exists(p) or p in self.items:
                continue
            if os.path.isdir(p):
                files = self._expand(p)
                if not files:
                    continue
                self.items.append(p)
                added += 1
                for f in files:
                    self.tree.insert("", "end", tags=(QUEUED,),
                                     values=(QUEUED, os.path.basename(f), "", "", "", ""))
            elif os.path.splitext(p)[1].lower() in READABLE:
                self.items.append(p)
                added += 1
                self.tree.insert("", "end", tags=(QUEUED,),
                                 values=(QUEUED, os.path.basename(p), "", "", "", ""))
        if added:
            # The newest entry is what the user just pointed at; put the panel on
            # it so a single dropped photograph goes straight to work with no
            # second click.  When several arrive at once, the first new one is the
            # current item -- the question of which to look at is real there.
            self._cur = first_new
            self._preview_index(self._cur)
        self._refresh_items()
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

    def _paste_screenshot(self):
        """Grab an image from the clipboard and load it into the queue.

        Saved as a JPG in the output directory so the existing ``_add`` /
        review path handles it without any special-casing."""
        from PIL import Image, ImageGrab
        img = ImageGrab.grabclipboard()
        if img is None:
            messagebox.showinfo("Paste", "no image on the clipboard")
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
        self.tree.delete(*self.tree.get_children())
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
        # One persistent layout: the work UI -- options, start bar, results
        # list, output row and review -- is always on screen, all inside the
        # cross. There is no separate "empty" landing screen; an empty window
        # shows the same controls with nothing in them, so the layout never
        # changes shape when a folder arrives. The drop target lives in the
        # loader field. The output row now lives inside the persistent start bar,
        # so it is mapped whenever that bar is -- nothing of its own to re-map here.
        for w in (self._w_opt, self._w_bar):
            if not w.winfo_ismapped():
                w.pack(side="bottom", fill="x")

    def _refresh_items(self):
        # The file listbox is gone, so there is no queue view to repaint here: the
        # queue lives in self.items, and what will be processed shows in the results
        # tree once a run has happened. All that remains is to keep the current-item
        # index honest and make sure the one persistent layout is mapped.
        if self._cur >= len(self.items):
            self._cur = len(self.items) - 1
        self._set_stage()
        self._update_run_label()

    def _update_run_label(self):
        """The prominent button names what it will do. One loose photograph is a
        single review; anything else walks the selection a photograph at a time.
        The command is `_review_each` either way -- it opens one window per image
        and writes only on Save -- so only the label changes to match."""
        single = len(self.items) == 1 and os.path.isfile(self.items[0])
        self.btn_run.configure(text="Review" if single else "Review each")

    def _preview_index(self, i):
        """Load the queue entry at ``i`` into the review panel.

        A folder resolves to its first readable image. The guard keeps a refresh
        that re-points at the same photograph from re-detecting it."""
        if not self.items or not (0 <= i < len(self.items)):
            return
        item = self.items[i]
        if os.path.isdir(item):
            inside = [f for f in self._expand(item) if os.path.isfile(f)]
            if not inside:
                return
            item = inside[0]
        if self.review.session is not None and self.review.session.path == item:
            return
        self.review.load(item, self._settings(), self._dest_corr(item),
                         overwrite=self.v_overwrite.get(),
                         on_saved=self._forget_saved)

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
        self.review.load(src, self._settings(), self._dest_corr(src),
                         overwrite=self.v_overwrite.get(),
                         on_saved=self._forget_saved,
                         # Deferred: `on_closed` fires while the panel is being
                         # rebuilt, and loading inside that rebuild is asking for
                         # a half-dead parent.
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

    def _ensure_birefnet_model(self):
        """A batch with BiRefNet masking but no saved model would fail on every
        photo with the same message; ask at the door instead.  The two answers
        are the only two: point at a file, or fetch one into models/BiRefNet/."""
        if self.v_mask.get() not in ("birefnet", "gdino"):
            return True
        path = self.v_maskpath.get() or self._remembered.get("birefnet_model", "")
        if path and os.path.isfile(path):
            return True
        from . import birefnet as BN
        ans = messagebox.askyesnocancel(
            "Kein BiRefNet-Modell",
            "Masking is set to BiRefNet, but no model file is saved.\n\n"
            "Yes     download BiRefNet-HR into models/BiRefNet/ (~444 MB)\n"
            "No      choose an existing .safetensors file\n"
            "Cancel  stop")
        if ans is None:
            return False
        if ans:
            return self._download_birefnet()
        p = filedialog.askopenfilename(
            title="BiRefNet weights",
            filetypes=[("BiRefNet weights", "*.safetensors *.pth *.pt"),
                       ("all files", "*.*")])
        if not p:
            return False
        self.v_maskpath.set(p)
        prefs.save(birefnet_model=p)
        self._remembered["birefnet_model"] = p
        messagebox.showinfo("BiRefNet model", BN.describe(p))
        return True

    def _download_birefnet(self):
        """Fetch the weights and architecture into models/BiRefNet/ and save
        the path, so the next run starts with a working model.  Runs on the
        main thread on purpose: the progress window is only alive while the
        event loop turns, and `update()` inside the callback is what turns it."""
        from . import birefnet as BN
        if not BN.transformers_available():
            ok = messagebox.askyesno(
                "BiRefNet",
                "This interpreter has no 'transformers' package, which the\n"
                "BiRefNet architecture imports. A downloaded model would still\n"
                "fail to load here.\n\n"
                "Yes: download anyway (for another python, e.g. ComfyUI's\n"
                "python_embeded)    No: cancel")
            if not ok:
                return False
        win = tk.Toplevel(self)
        win.title("Downloading BiRefNet-HR")
        win.resizable(False, False)
        win.transient(self)
        win.configure(bg=INK["bg"])
        lbl = tk.Label(win, text="starting...", justify="left", padx=12, pady=8,
                       bg=INK["bg"], fg=INK["text"])
        lbl.pack()
        bar = ttk.Progressbar(win, length=360, mode="determinate")
        bar.pack(padx=12, pady=(0, 10))

        def progress(name, done, total):
            if total:
                bar.configure(maximum=total, value=done)
                lbl.configure(text=f"{name}: {done / 1e6:.0f} / {total / 1e6:.0f} MB")
            else:
                lbl.configure(text=name + " ...")
            self.update()

        try:
            res = BN.download_weights(progress=progress)
        except Exception as exc:
            win.destroy()
            messagebox.showerror("BiRefNet download", str(exc))
            return False
        win.destroy()
        path = res["weights"]
        self.v_maskpath.set(path)
        prefs.save(birefnet_model=path)
        self._remembered["birefnet_model"] = path
        messagebox.showinfo("BiRefNet", BN.describe(path) + "\n\nSaved as the model path.")
        return True

    def _start(self):
        files = self._files()
        if not files:
            messagebox.showinfo("Batch", "add some images or a folder first")
            return
        if not self._ensure_birefnet_model():
            return
        if self.v_overwrite.get() and not messagebox.askyesno(
                "Overwrite", f"Replace {len(files)} original file(s)?"):
            return
        self.tree.delete(*self.tree.get_children())
        self.results.clear()
        self.progress.configure(maximum=len(files), value=0)
        self.stop_flag.clear()
        # Both entry points go dark while the unattended run writes: starting a
        # review walk over files a worker thread is rewriting is a race, and
        # pressing Unattended twice would run the folder twice.
        self.btn_run.configure(state="disabled")
        self.btn_batch.configure(state="disabled")
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
                    self.btn_batch.configure(state="normal")
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
        self.review.load(r.src, self._settings(), self._dest_corr(r.src),
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
