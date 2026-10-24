"""Tkinter review window, with a rudimentary unattended batch.

One screen.  Each photograph opens for review and the decision can be overridden by hand:
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
import sys
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

# macOS Tk numbers the right mouse button 2 and reports Option as state bit
# 0x0010; Windows and Linux say 3 and 0x0008 (2026-10-23, untested on a Mac).
_MAC = sys.platform == "darwin"
RB = "2" if _MAC else "3"
ALT_MASK = 0x0010 if _MAC else 0x0008

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


# Only the detectors that work and earn a place; must match cli.py's
# --detector choices.  mlsd needs the TFLite runtime (ai-edge-litert).
DETECTORS = ("lsd", "mlsd")

RULER_MARGIN = 20
# The two candidates for text sitting ON the accent colour. Not part of a
# palette: they are the ink, and which one is used is decided per theme by
# measuring contrast -- see `on_accent`.
# The window/taskbar icon is drawn white on an opaque black plate, because a
# taskbar's own colour is not knowable and a white-on-nothing glyph disappears
# on half of them. Not themed for that reason, and named so it is visibly a
# decision.
ICON_WHITE = "#ffffff"

# The mask-tint picker: sixteen vivid, saturated choices laid out 4x4. Fixed
# on purpose -- they are read against a PHOTOGRAPH, so they must stay apart
# from each other and from brick, sky and foliage in every theme. Tkinter's
# own `colorchooser` is broken on Windows, which is why this grid exists.
MASK_SWATCHES = (
    "#ff3030", "#ff7030", "#ffb030", "#ffff30",
    "#30ff50", "#30ffd0", "#3090ff", "#3030ff",
    "#8030ff", "#d030ff", "#ff30c0", "#ff3060",
    "#ffffff", "#b0b0b0", "#606060", "#202020",
)

INK_ON_ACCENT_DARK = "#0b1017"
INK_ON_ACCENT_LIGHT = "#ffffff"

GUIDE_GREY = "#9aa0a8"

# Marks drawn ON a photograph, and deliberately NOT part of the theme.
#
# The hard rule is "every colour comes from INK", and for chrome it holds
# without exception. These are the exception, and it is worth stating rather
# than leaving twenty-odd bare hex literals scattered through the draw calls
# looking like oversights -- which is what they were until 2026-09-20, and
# why nobody could tell a deliberate colour from a forgotten one.
#
# They do not follow the theme because they are not read against the theme.
# A green inlier line is read against whatever the photograph happens to be,
# and it has to stay legible on brick, sky and shadow in every palette. Nine
# of these duplicate an INK value by coincidence of taste; binding them to it
# would mean that picking Amiga 500 -- whose `ok` is #008800 -- turns the SAM
# selection outline into dark green on a dark facade. The palette is free to
# move; these are not.
#
# Named, so the next reader can tell intent from accident.
OVERLAY = {
    # the crop rectangle on the corrected pane, and its handles
    "crop":        "#4da3ff",
    "crop_edge":   "#0b1017",
    # small pixel-dimension labels burned over the image corners
    "label":       "#ffffff",
    # the status word, drawn faintly inside the after field so the pictures
    # keep their height; dim enough to read as a watermark, not a label
    "status_faint": "#3a3f47",
    # hand-placed marks: cyan/amber for verticals, magenta for horizontals,
    # the paler tone being the one that is not currently selected
    "mark_v":      "#00e5ff",
    "mark_v_off":  "#ffb300",
    "mark_h":      "#e040fb",
    "mark_h_off":  "#ce93d8",
    "mark_handle": "#9e9e9e",
    # SAM: the selection, its dashed drag preview, and a negative point
    "sam":         "#5ac37f",
    "sam_neg":     "#ff5555",
    # the facade-strip rulers on the original
    "strip":       "#9fd8ff",
    # what the mask brush lays down
    "paint":       "#8b0f14",
    # "Check lines" re-runs the detector on the RESULT: green where a line
    # came out truly straight, red/amber where it still leans.
    "check_ok":    "#39ff7a",
    # "Outline": where the photograph's own border landed after the warp
    "outline":     "#ffe14d",
    "check_v_off": "#ff5a5a",
    "check_h_off": "#ffb03a",
    # the second M-LSD pass, drawn beside the primary detector's colours and
    # therefore in a different hue family so the two can be told apart
    "mlsd_ok":     "#00e5ff",
    "mlsd_v_off":  "#76ff03",
    "mlsd_h_off":  "#ffff00",
    # the loupe crosshair; cyan while Alt damps the tracking, so the damping
    # is visible rather than only felt
    "loupe":       "#000000",
    "loupe_alt":   "#00e5ff",
    # what the mask wash is tinted with until the user picks otherwise
    "mask_default": "#dc4c3e",
}

CHECK_LINE_TOL_DEG = 1.5
""""Check lines" straightness tolerance, in DEGREES from the nearest axis.

Ledger item 15 (2026-09-26, user-caught): this used to be an absolute PIXEL
offset (``abs(dy) <= 1.5``) checked against whatever length segment the
re-detector happened to return on the small preview array. For a short
segment (most of what a real detector returns -- window reveals, coursing,
brick joints, 15-25 px typical) 1.5 px is 3-6 deg of slack, enough to hide a
real, measured -2.2 deg systematic lean entirely, segment by segment, while
the line it belongs to visibly climbs across the whole frame. An angle is
the only version of this check that means the same thing regardless of how
long a piece the detector happened to find.
"""


def _off_angle_deg(x0, y0, x1, y1):
    """Degrees the segment leans from its nearest axis (horizontal or vertical).

    ``atan2(min(|dx|,|dy|), max(|dx|,|dy|))`` gives that in one formula: the
    smaller of the two extents IS the offset from whichever axis the larger
    one is closest to, so there is no separate branch for "vertical-ish" vs
    "horizontal-ish" -- classifying the segment for colour purposes (see
    callers) is a different question from measuring how far off it is.
    """
    dx, dy = abs(x1 - x0), abs(y1 - y0)
    return math.degrees(math.atan2(min(dx, dy), max(dx, dy, 1e-9)))


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
    "dim":     "#6f7680",   # secondary text (RefGrade's dim is #666)
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
        "text": "#e6e8ec", "dim": "#6f7680",
        "accent": "#4da3ff", "ok": "#5ac37f", "warn": "#e0b24c", "err": "#ef6b6b",
    },
    # --- C64: three colours and nothing else (user, 2026-10-21) -- the start
    #     screen's blue, the light blue of its border and text, and white.
    #     Pepto's VIC-II values. ok/warn/err are white too: the C64 screen had
    #     no traffic lights, and a fourth colour is what the user asked away.
    #     The Commodore 64 Pixelized face is loaded privately from the repo
    #     root when it is there (not committed: its ReadMe grants no
    #     redistribution); without it the theme falls back to Courier New. ---
    #     The keys sharing a colour differ in the last bit on purpose:
    #     `_retint_bg` maps old to new BY VALUE, so two keys with one value
    #     would recolour a picture canvas as "bg" on the way out of C64. One
    #     step in 255 is invisible; the screen still shows three colours.
    "C64": {
        "bg": "#352879", "panel": "#35287A", "field": "#35287B",
        "cross": "#6C5EB5", "line": "#6C5EB6",
        "text": "#FFFFFF", "dim": "#6C5EB7",
        "accent": "#6C5EB4", "ok": "#FFFFFE", "warn": "#FFFFFD", "err": "#FFFFFC",
        "ui_font": "Commodore 64 Pixelized", "mono_font": "Commodore 64 Pixelized",
        "font_file": "Commodore Pixelized v1.2.ttf", "font_px": 12, "pad_x": 1,
    },
    # --- Amiga 500: Workbench 2.0-3.1 (3D Grey & Blue).  Bevel gray bg,
    #     dark-blue title bar accent, black text, white light edges. ---
    "Amiga 500": {
        "bg": "#AAAAAA", "panel": "#B4B4B4", "field": "#9A9A9A",
        "cross": "#808080", "line": "#FFFFFF",
        "text": "#000000", "dim": "#555555",
        "accent": "#0055BB", "ok": "#008800", "warn": "#886600", "err": "#CC0000",
        "ui_font": "Inter", "mono_font": "JetBrains Mono",
    },
    # --- Dark Grey: replaces "Light" (user, 2026-10-21: "light looks
    #     shitty"). A neutral mid-dark grey, a step up from Minimal Black so
    #     the two are told apart, with a muted steel-blue accent. ---
    "Dark Grey": {
        "bg": "#2b2d31", "panel": "#33363b", "field": "#26282c",
        "cross": "#1e2023", "line": "#45494f",
        "text": "#e3e5e8", "dim": "#8a9099",
        "accent": "#8fb4d9", "ok": "#7fc79a", "warn": "#dcb866", "err": "#e08a84",
    },
    # --- Phosphor: the CRT after the tube has warmed up.  It began as pure
    #     black with a #55ff55 hover, which is accurate and shouts; this is the
    #     same glow left to settle.  Every accent is a pastel -- mint, apricot,
    #     dusty rose -- and each is faint on purpose, because they sit around a
    #     photograph and a loud chrome shifts every tone in it.  The one thing
    #     no other palette has is `grad`: the two picture wells get a ramp from
    #     slate-indigo down to teal ink instead of a flat fill, which is where
    #     the phosphor is still visible.  JetBrains Mono keeps the terminal in it. ---
    "Phosphor": {
        "bg": "#151b22", "panel": "#1c242d", "field": "#111820",
        "cross": "#0b1016", "line": "#2a3642",
        "text": "#e2eef0", "dim": "#8ba1a7",
        "accent": "#9fd6cb", "ok": "#b6e0c2", "warn": "#efd9ab", "err": "#eeb3b3",
        "ui_font": "Inter", "mono_font": "JetBrains Mono",
        "grad": ("#27323f", "#0e151a", "#9fd6cb"),
    },
}

# One face, Inter (RefGrade): the whole window is a single typeface, numbers
# included.  Tkinter cannot switch on Inter's tabular-figures feature (tnum),
# so a column of numbers does not align to the pixel the way a monospace face
# would -- but one face reads as one instrument, and that is the reference's
# look.  Retro themes opt back into a terminal face via `mono_font`.
# Both first names ship in fonts/; the rest only answer if registering failed.
_UI_FAMILIES = ("Inter", "Helvetica Neue", "DejaVu Sans")
_MONO_FAMILIES = ("JetBrains Mono", "Menlo", "DejaVu Sans Mono")


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


def _relative_luminance(hex_colour):
    """WCAG relative luminance of ``#rrggbb``, 0 (black) to 1 (white)."""
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    srgb = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
           for c in srgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast_ratio(a, b):
    """WCAG contrast between two ``#rrggbb`` colours: 1.0 (same) to 21.0."""
    la, lb = _relative_luminance(a), _relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def on_accent(accent, dark=INK_ON_ACCENT_DARK, light=INK_ON_ACCENT_LIGHT):
    """Text colour for a button painted in *accent*: whichever reads better.

    It was the literal ``"#0b1017"`` -- one dark, hardcoded, for every theme.
    That is fine while the accent stays bright, and five of the six palettes
    here do. **Amiga 500 does not**: its accent is ``#0055BB``, and dark text
    on it measures a contrast ratio of **2.74**, against the 4.5 that WCAG AA
    asks for body text. The primary action button in that theme -- Save, the
    one the whole review loop ends on -- was the least legible thing in the
    window, and nothing said so because a hex literal in a widget option looks
    like a decision somebody made.

    Measured rather than switched on a luminance threshold, because a
    threshold is another number to be wrong about: compute both ratios and
    take the better one. Every palette in THEMES now clears 4.5, which
    `test_every_theme_keeps_the_accent_button_readable` checks.
    """
    return dark if contrast_ratio(dark, accent) >= contrast_ratio(light, accent) else light


_PRIVATE_FONTS = set()
_UI_FACE = "Inter"        # the active theme's face; `apply_theme` sets it
_UI_PX = None             # ...and its pinned pixel size, if it has one


def _clip_segment(a, b, box):
    """Liang-Barsky: the part of segment a-b inside ``box`` (x0, y0, x1, y1)
    as (x0, y0, x1, y1), or None when none of it is."""
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, ax - box[0]), (dx, box[2] - ax),
                 (-dy, ay - box[1]), (dy, box[3] - ay)):
        if p == 0:
            if q < 0:
                return None
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return None
    return (ax + t0 * dx, ay + t0 * dy, ax + t1 * dx, ay + t1 * dy)


def ui_font(size):
    """(family, size) in the active theme's face, for text drawn on a canvas.

    A literal ("Segoe UI", 9) ignored the theme, and ("TkDefaultFont", 9) is
    read by Tk as a FAMILY called TkDefaultFont, not the named font -- so it
    ignored it too. Resolved at draw time, so a theme switch reaches the next
    redraw."""
    return (_UI_FACE, -_UI_PX if _UI_PX else size)


_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FONT_DIR = os.path.join(_REPO, "fonts")
# Google fonts shipped in fonts/ (tools/build_fonts.py), replacing the
# Windows-only Segoe UI / Consolas (user, 2026-10-23). Registered for this
# process before Tk starts, so the window looks the same on every OS.
BUNDLED_FONTS = ("Inter-Regular.ttf", "JetBrainsMono-Regular.ttf")
ICON_FONT = os.path.join(FONT_DIR, "MaterialSymbols-Subset.ttf")
EMOJI_FONT = os.path.join(FONT_DIR, "NotoEmoji-Subset.ttf")


def _register_font_file(path):
    """Make a font file usable by Tk in this process only, installing nothing.

    Windows: AddFontResourceEx(FR_PRIVATE). macOS: CoreText, process scope.
    Linux: fontconfig's application fonts. Returns True when the OS accepted
    it. A failure is quiet: `_pick_family` then falls back to what is there.
    Only the Windows branch has run; macOS and Linux are written to their
    documented APIs and untested (2026-10-23)."""
    if not path or path in _PRIVATE_FONTS or not os.path.exists(path):
        return False
    _PRIVATE_FONTS.add(path)
    try:
        import ctypes
        if os.name == "nt":
            FR_PRIVATE = 0x10
            return ctypes.windll.gdi32.AddFontResourceExW(path, FR_PRIVATE, 0) > 0
        raw = os.fsencode(path)
        if sys.platform == "darwin":
            cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
            ct = ctypes.CDLL("/System/Library/Frameworks/CoreText.framework/CoreText")
            cf.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
            cf.CFURLCreateFromFileSystemRepresentation.argtypes = [
                ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_bool]
            cf.CFRelease.argtypes = [ctypes.c_void_p]
            ct.CTFontManagerRegisterFontsForURL.restype = ctypes.c_bool
            ct.CTFontManagerRegisterFontsForURL.argtypes = [
                ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
            url = cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), False)
            if not url:
                return False
            try:
                return bool(ct.CTFontManagerRegisterFontsForURL(url, 1, None))  # 1 = process
            finally:
                cf.CFRelease(url)
        import ctypes.util
        fc = ctypes.CDLL(ctypes.util.find_library("fontconfig") or "libfontconfig.so.1")
        fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        return bool(fc.FcConfigAppFontAddFile(None, raw))
    except Exception:
        return False


def _register_bundled_fonts():
    for name in BUNDLED_FONTS:
        _register_font_file(os.path.join(FONT_DIR, name))


def _load_private_font(name):
    """A theme's own font file in the repo root (the C64 face), for this process."""
    if name:
        _register_font_file(os.path.join(_REPO, name))


def apply_theme(root, palette=None):
    """Apply a colour palette to all ttk styles.  Returns ``(ui_family, mono_family)``."""
    p = palette or INK
    _register_bundled_fonts()
    _load_private_font(p.get("font_file"))
    # A pixel face is sharp only at its own pixel grid, so a theme may pin ONE
    # pixel size for every text (Tk reads a negative size as pixels).
    px = p.get("font_px")
    sz = (lambda n: -int(px)) if px else (lambda n: n)
    # A wide face needs the strip's padding back: the C64 face at 12 px asked
    # 1027 px of a 948 px row with the default 6 px each side.
    padx = p.get("pad_x", 6)
    ui_override = p.get("ui_font")
    mono_override = p.get("mono_font")
    if ui_override:
        ui = _pick_family(root, (ui_override, *tuple(_UI_FAMILIES)), "TkDefaultFont")
    else:
        ui = _pick_family(root, _UI_FAMILIES, "TkDefaultFont")
    if mono_override:
        mono = _pick_family(root, (mono_override, *tuple(_MONO_FAMILIES)), "TkFixedFont")
    else:
        mono = ui   # one face (RefGrade): numbers share the UI face, not a mono
    try:
        from tkinter import font as tkfont
        for name, fam, size in (("TkDefaultFont", ui, sz(8)), ("TkTextFont", ui, sz(8)),
                                ("TkMenuFont", ui, sz(8)), ("TkHeadingFont", ui, sz(8)),
                                ("TkFixedFont", mono, sz(8))):
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
                  insertcolor=p["text"], font=(ui, sz(8)))
    st.configure("TFrame", background=p["bg"])
    st.configure("Panel.TFrame", background=p["panel"])
    # Field labels are dim (RefGrade); values and the title stay bright.
    st.configure("TLabel", background=p["bg"], foreground=p["dim"])
    st.configure("Dim.TLabel", foreground=p["dim"])
    st.configure("Head.TLabel", foreground=p["dim"], font=(ui, sz(8)))
    st.configure("Value.TLabel", foreground=p["text"], font=(mono, sz(8)))
    st.configure("Title.TLabel", foreground=p["text"], font=(ui, sz(11)))

    st.configure("TButton", background=p["panel"], foreground=p["text"],
                  borderwidth=0, focusthickness=0, padding=(12, 6))
    st.map("TButton",
           background=[("pressed", p["line"]), ("active", p["line"])],
           foreground=[("disabled", p["dim"])])
    st.configure("Accent.TButton", background=p["accent"],
                  foreground=on_accent(p["accent"]),
                  padding=(14, 7))
    st.map("Accent.TButton", background=[("active", p["accent"]),
                                         ("disabled", p["line"])])

    # The control strip's compact set (2026-10-20): same colours, minimum height.
    # width=-6: at least six characters, otherwise as wide as the text. The ttk
    # default (~11 characters) made every key 89 px and the action row 782 px,
    # too wide for a 1366 px window (2026-10-21).
    st.configure("Compact.TButton", padding=(padx, 0), width=-6)
    st.configure("Accent.Compact.TButton", background=p["accent"],
                 foreground=on_accent(p["accent"]), padding=(10, 0), width=-6)
    st.map("Accent.Compact.TButton", background=[("active", p["accent"]),
                                                 ("disabled", p["line"])])
    st.configure("Compact.TCheckbutton", background=p["bg"], foreground=p["text"],
                 padding=0, indicatorwidth=13, indicatorheight=13)
    st.map("Compact.TCheckbutton", background=[("active", p["bg"])])
    st.configure("Compact.Toolbutton", padding=(padx, 0), anchor="center",
                 background=p["panel"], foreground=p["text"], borderwidth=0)
    st.map("Compact.Toolbutton",
           background=[("selected", p["accent"]), ("active", p["line"])],
           foreground=[("selected", on_accent(p["accent"]))])
    # The mode switch: the active mode light, the others dark.
    st.configure("Mode.Toolbutton", padding=(padx, 0), anchor="center",
                 background=p["bg"], foreground=p["dim"], borderwidth=0)
    st.map("Mode.Toolbutton",
           background=[("selected", p["text"]), ("active", p["line"])],
           foreground=[("selected", p["bg"]), ("active", p["text"])])
    st.configure("Compact.TCombobox", padding=0)
    st.configure("Compact.TSpinbox", padding=0)

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
                  foreground=p["text"], borderwidth=0, rowheight=20)
    st.configure("Treeview.Heading", background=p["bg"], foreground=p["dim"],
                 borderwidth=0, font=(ui, sz(9)))
    st.map("Treeview", background=[("selected", p["line"])],
           foreground=[("selected", p["text"])])
    st.configure("TSpinbox", arrowcolor=p["dim"], borderwidth=0, padding=6)
    st.configure("TLabelframe", background=p["bg"], bordercolor=p["line"])
    st.configure("TLabelframe.Label", background=p["bg"], foreground=p["dim"])
    st.configure("TSeparator", background=p["line"])
    # Menus (right-click, dropdown) use the theme palette, not the OS default.
    st.configure("TMenu", background=p["panel"], foreground=p["text"])
    st.map("TMenu", background=[("active", p["line"])])
    global _UI_FACE, _UI_PX
    _UI_FACE, _UI_PX = ui, (int(px) if px else None)
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


LOGO_FILE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "..", "..", "Logo_BPC.png"))


def _logo_image(size, colour):
    """The shipped mark at ``size``, recoloured to ``colour``, or ``None``.

    ``Logo_BPC.png`` (the pyramid and eye since 2026-10-24, cut from
    ``Logo_new.png`` to its ink and squared) is a black silhouette on
    transparency, and this window's
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
    # Fit inside the square and centre, never stretch (user: "skalier
    # korrekt") -- the shipped file is square, a replacement need not be.
    k = size / float(max(src.size))
    w, h = max(1, round(src.width * k)), max(1, round(src.height * k))
    alpha = Image.new("L", (size, size), 0)
    alpha.paste(src.getchannel("A").resize((w, h), Image.LANCZOS),
                ((size - w) // 2, (size - h) // 2))
    tint = Image.new("RGBA", (size, size), colour)
    tint.putalpha(alpha)
    return tint


def _hex_rgba(hexc, alpha=255):
    """A ``#rrggbb`` palette colour as an RGBA tuple for PIL."""
    h = hexc.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


WORDMARK_FILE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                              "..", "..", "Logo_wordmark.png"))


def _wordmark_image(height, colour):
    """The v07 wordmark scaled to ``height`` px tall, tinted to ``colour``.

    ``Logo_wordmark.png`` is white type on black with no alpha channel, so the
    alpha is taken from its luminance: white ink becomes opaque, the black
    ground transparent.  The colour comes from the palette, so the wordmark
    follows the theme like the mark does.  ``None`` when the file is absent.
    """
    try:
        src = Image.open(WORDMARK_FILE).convert("L")
    except Exception:
        return None
    # Crop to the ink first: the source is the wordmark centred in a larger
    # canvas, so scaling the whole canvas would leave the type tiny.
    box = src.getbbox()
    if box is None:
        return None
    src = src.crop(box)
    w = max(1, int(round(src.width * height / src.height)))
    src = src.resize((w, height), Image.LANCZOS)
    tint = Image.new("RGBA", (w, height), colour)
    tint.putalpha(src)
    return tint




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
    # The shipped Google fonts (fonts/), not Segoe's: those exist on Windows
    # only, may not be redistributed, and crashed the first Mac (2026-10-23).
    try:
        # The filled emoji is drawn 4x and scaled down, or at key size the
        # fill runs its arms and tail into one blob.
        up = 4 if (emoji and mono) else 1
        font = ImageFont.truetype(EMOJI_FONT if emoji else ICON_FONT, size * up)
    except Exception:
        return None
    pad = size * up // 2
    scratch = Image.new("RGBA", (size * up + 2 * pad, size * up + 2 * pad), (0, 0, 0, 0))
    if emoji and not mono:
        ImageDraw.Draw(scratch).text((pad, pad), ch, font=font, embedded_color=True)
    else:
        # `mono` takes the emoji font's outline and fills it flat, which is what
        # puts a dinosaur in a palette of monochrome keys without it arriving as
        # the one coloured sticker on the bar.  Measured readable: 27x25 of ink
        # at 39 % coverage, a silhouette rather than a blob.
        ImageDraw.Draw(scratch).text((pad, pad), ch, font=font, fill=_hex_rgba(colour))
        if emoji:
            # The emoji font draws the dinosaur as an outline, so it read
            # thinner and paler than the solid marks around it (user,
            # 2026-10-24: "Grounding Dino falsche Farbe"). Fill what the
            # outline encloses: everything transparent that the frame's edge
            # cannot reach.
            # Its inner detail strokes leave hairline gaps, so the outline is
            # closed by one pixel before the flood and the fill shrunk back by
            # the same pixel afterwards; the original strokes are kept.
            alpha = np.array(scratch.getchannel("A"))
            k = np.ones((3, 3), np.uint8)
            reach = cv2.dilate((alpha > 40).astype(np.uint8), k)
            flood = np.zeros((reach.shape[0] + 2, reach.shape[1] + 2), np.uint8)
            cv2.floodFill(reach, flood, (0, 0), 2)
            holes = cv2.erode((reach != 2).astype(np.uint8), k).astype(bool) & (alpha < 255)
            alpha[holes] = 255
            px = np.array(scratch)
            px[holes, :3] = _hex_rgba(colour)[:3]
            px[..., 3] = alpha
            scratch = Image.fromarray(px, "RGBA")
            scratch = scratch.resize((scratch.width // up, scratch.height // up),
                                     Image.LANCZOS)
    return _fit_ink(scratch, size)


def _fit_ink(scratch, size):
    """Crop *scratch* to the pixels actually drawn and centre those in a box.

    Factored out of ``_icon_pil`` on 2026-09-20 so the hand-drawn keys get the
    identical treatment. That difference was the measurable half of why the
    palette read as two sets: a font glyph fills its box because this step
    crops it to its ink, while the drawn ones kept whatever margin the drawing
    code happened to leave -- 0.69 and 0.77 of the box against 1.00, which is
    simply a smaller icon in an identical key.
    """
    ink = scratch.getbbox()
    if ink is None:
        return None
    glyph = scratch.crop(ink)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if glyph.width > size or glyph.height > size:
        glyph.thumbnail((size, size))
    out.paste(glyph, ((size - glyph.width) // 2, (size - glyph.height) // 2), glyph)
    return out


def _load_key_icon(codepoint, name):
    """A load key's icon from the shipped Material Symbols (add, folder,
    content_paste), else its drawn twin if the font file is missing."""
    return (_icon_pil(codepoint, layout.tool_glyph(), INK["dim"])
            or _glyph_pil(name, layout.tool_glyph(), INK["dim"]))


_DRAWN_GLYPHS = ("mark", "rect", "brush", "sam", "strip")


def _glyph_pil(name, size, colour):
    """One hand-drawn tool mark, in the palette's single visual language.

    Why drawn at all, when ``_icon_pil`` argues for stock icons: because the
    stock set does not contain this vocabulary. Measured on 2026-09-20, the
    seven keys were in four languages -- two flat line diagrams, one solid
    disc, a monochromed dinosaur emoji, and two *pictorial* font icons that
    were also saying the wrong thing: SAM, which box-selects a building, wore
    an eyedropper, and "strike slanted lines" wore Segoe's debug beetle. A
    shipped icon set is consistent with itself; it is not automatically
    consistent with the six concepts this window actually has.

    So: one language for all of them. Straight strokes at
    ``layout.glyph_stroke()``, no tapers, no highlights, no perspective on
    anything except the one glyph whose meaning IS perspective, and every mark
    cropped to its ink by ``_fit_ink`` so they share a footprint.

    Two deliberate exceptions, both recorded rather than quietly made:
      * ``brush`` stays a filled disc, and stays inverted. It is the one key
        that is a swatch rather than a diagram -- the mark stands for the paint
        it lays down (user, 2026-09-15).
      * the Grounding DINO key keeps its dinosaur. It is a pun the user put
        there on purpose; a previous session already traded colour for
        coherence and stopped short of replacing it, which is the right place
        to stop.
    """
    from PIL import ImageDraw
    d_size = size * 4                       # draw large, downsample: clean edges
    img = Image.new("RGBA", (d_size, d_size), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    ink = _hex_rgba(colour)
    w = max(2, layout.glyph_stroke(size) * 4)

    def P(fx, fy):
        return (fx * d_size, fy * d_size)

    def line(a, b):
        dr.line([P(*a), P(*b)], fill=ink, width=w)

    def dot(fx, fy, fr):
        r = fr * d_size
        x, y = P(fx, fy)
        dr.ellipse([x - r, y - r, x + r, y + r], fill=ink)

    if name == "mark":
        # A drawn edge with a grip at each end: exactly the gesture, and it
        # says which way the line leans, which the old "#" grid never did.
        line((0.14, 0.86), (0.86, 0.14))
        for fx, fy in ((0.14, 0.86), (0.86, 0.14)):
            h = 0.10 * d_size
            x, y = P(fx, fy)
            dr.rectangle([x - h, y - h, x + h, y + h], fill=ink)
    elif name == "rect":
        # The one glyph allowed perspective, because perspective is its
        # meaning: four clicked corners of a facade seen at an angle. A square
        # here would be indistinguishable from a crop tool.
        quad = [(0.10, 0.14), (0.90, 0.26), (0.86, 0.90), (0.14, 0.78)]
        for i in range(4):
            line(quad[i], quad[(i + 1) % 4])
        for fx, fy in quad:
            dot(fx, fy, 0.085)
    elif name == "strip":
        # Two rulers and the span between them: the facade strip restricts
        # which horizontals count, and the old three solid bars read as a
        # barcode rather than as a measurement.
        line((0.16, 0.06), (0.16, 0.94))
        line((0.84, 0.06), (0.84, 0.94))
        line((0.16, 0.50), (0.84, 0.50))
        # Big enough to be an arrow. At 0.09 they disappeared at 1:1 and the
        # glyph read as a capital H -- two rulers and a bar is a letter, two
        # rulers and a span is a measurement, and the difference is entirely
        # in whether the heads survive the downsample.
        for fx, dx in ((0.16, 1), (0.84, -1)):
            x, y = P(fx, 0.50)
            a = 0.15 * d_size
            dr.polygon([(x, y), (x + dx * a * 1.5, y - a), (x + dx * a * 1.5, y + a)],
                       fill=ink)
    elif name == "sam":
        # Drag a box, get the subject: a marquee with a pointer inside it.
        # Dashes drawn by hand because PIL has no dash pattern, and a marquee
        # without them is just a rectangle.
        x0, y0, x1, y1 = 0.06, 0.06, 0.74, 0.74
        span = x1 - x0
        # Three dashes a side with a real gap between them. Four at 0.16 with a
        # 0.17 step was the first try and it rendered as a closed rectangle --
        # a marquee whose dashes touch is just a box, and the one thing this
        # glyph has to say is "drag a selection".
        for i in range(3):
            t0 = i * 0.36
            seg = 0.20
            line((x0 + span * t0, y0), (x0 + span * (t0 + seg), y0))
            line((x0 + span * t0, y1), (x0 + span * (t0 + seg), y1))
            line((x0, y0 + span * t0), (x0, y0 + span * (t0 + seg)))
            line((x1, y0 + span * t0), (x1, y0 + span * (t0 + seg)))
        dr.polygon([P(0.44, 0.40), P(0.44, 0.96), P(0.60, 0.80), P(0.80, 0.80)],
                   fill=ink)
    elif name == "brush":
        # A swatch, not a diagram -- see the docstring.
        dot(0.5, 0.5, 0.47)
    # Stand-ins for the three load keys when fonts/ is missing. They came in
    # when Segoe's icon fonts were still used and crashed the first Mac
    # (2026-10-23: "Image is required"); Material Symbols replaced Segoe since.
    elif name == "add":
        line((0.50, 0.08), (0.50, 0.92))
        line((0.08, 0.50), (0.92, 0.50))
    elif name == "folder":
        body = [(0.06, 0.30), (0.06, 0.86), (0.94, 0.86), (0.94, 0.30)]
        for i in range(3):
            line(body[i], body[i + 1])
        line((0.06, 0.30), (0.06, 0.18))
        line((0.06, 0.18), (0.40, 0.18))
        line((0.40, 0.18), (0.48, 0.30))
        line((0.48, 0.30), (0.94, 0.30))
    elif name == "paste":
        board = [(0.16, 0.14), (0.84, 0.14), (0.84, 0.94), (0.16, 0.94)]
        for i in range(4):
            line(board[i], board[(i + 1) % 4])
        dr.rectangle([*P(0.34, 0.04), *P(0.66, 0.22)], fill=ink)
        line((0.30, 0.46), (0.70, 0.46))
        line((0.30, 0.66), (0.70, 0.66))
    else:
        return None
    img = img.resize((size, size), Image.LANCZOS)
    return _fit_ink(img, size)


def _beholder_pil(size=64):
    """The mark as a black-background RGBA PIL image, for ``iconphoto``.

    Backed on black rather than left transparent: a taskbar's own colour is not
    knowable, and a white-on-nothing glyph disappears on half of them.
    """
    from PIL import ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    mark = _logo_image(size, ICON_WHITE)
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


def _dark_titlebar(win):
    """Match the native Windows title bar to the active palette.

    On Windows 10/11 the OS-drawn caption follows the OS theme, not the app, so
    a dark window still wears a light title bar. This pushes the active INK
    colours onto that chrome via ``DwmSetWindowAttribute``:

    * ``DWMWA_USE_IMMERSIVE_DARK_MODE`` (20) -- dark caption for dark palettes.
    * ``DWMWA_BORDER_COLOR`` (34) / ``DWMWA_CAPTION_COLOR`` (35) -- frame and
      caption, set to the palette's ``bg``.
    * ``DWMWA_TEXT_COLOR`` (36) -- the title text, set to the palette's ``text``.

    Best-effort: a no-op off Windows, on older Windows, or before the native
    window exists. The frame -- not the Tk client -- owns the caption, so the
    hwnd is the client's parent.
    """
    import sys
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import byref, c_int

        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        if not hwnd:
            return
        dwmapi = ctypes.windll.dwmapi

        def _ref(hexcolor):
            h = hexcolor.lstrip("#")
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return (b << 16) | (g << 8) | r, r, g, b

        def _set(attr, value):
            dwmapi.DwmSetWindowAttribute(hwnd, c_int(attr), byref(c_int(value)), 4)

        bgref, r, g, b = _ref(INK["bg"])
        # The caption follows the palette's background, dark or light.
        _set(20, 1 if (0.299 * r + 0.587 * g + 0.114 * b) < 140 else 0)
        _set(34, bgref)                       # DWMWA_BORDER_COLOR
        _set(35, bgref)                       # DWMWA_CAPTION_COLOR
        _set(36, _ref(INK["text"])[0])        # DWMWA_TEXT_COLOR
    except Exception:
        return


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


def _attach_tooltip(widget, text):
    """A plain hover tooltip; no dependency beyond Tk itself.

    It waits ``layout.TOOLTIP_DELAY_MS`` (user: not at once, after 1 s), so a
    pointer passing over the palette does not flash help.
    """
    tip = {"t": None, "job": None}

    def arm(_e=None):
        cancel()
        tip["job"] = widget.after(layout.TOOLTIP_DELAY_MS, show)

    def cancel():
        if tip["job"] is not None:
            try:
                widget.after_cancel(tip["job"])
            except tk.TclError:
                pass
            tip["job"] = None

    def show(_e=None):
        tip["job"] = None
        if tip["t"] is not None or not widget.winfo_exists():
            return
        t = tk.Toplevel(widget)
        t.configure(bg=INK["cross"])
        t.wm_overrideredirect(True)
        t.withdraw()                    # measured first, shown where it fits
        lbl = tk.Label(t, text=text, background=INK["cross"], foreground=INK["text"],
                       font=ui_font(9), padx=8, pady=4, relief="solid",
                       borderwidth=1, wraplength=320, justify="left")
        lbl.pack()
        t.update_idletasks()
        x, y = layout.tooltip_xy(widget.winfo_rootx(), widget.winfo_rooty(),
                                 widget.winfo_width(), widget.winfo_height(),
                                 t.winfo_reqwidth(), t.winfo_reqheight(),
                                 widget.winfo_screenwidth(), widget.winfo_screenheight())
        t.wm_geometry(f"+{x}+{y}")
        t.deiconify()
        tip["t"] = t

    def hide(_e=None):
        cancel()
        if tip["t"] is not None:
            tip["t"].destroy()
            tip["t"] = None

    widget.bind("<Enter>", arm)
    widget.bind("<Leave>", hide)
    widget.bind("<ButtonPress>", hide, add="+")


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
        self._build()
        # The mode switch mirrors the horizontal-auto state the photograph
        # opened with, so the strip shows the right controls from the start.
        self.v_corr_mode.set("horizontal" if self.settings.correct_horizontal else "auto")
        self._mode_frames[self.v_corr_mode.get()].tkraise()
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
        self.after(60, self._sync_from_session)
        # "Unchanged" is the state once opening has settled -- the automatic
        # crop and the widget sync above are the program's, not the user's.
        self._mark_clean()
        self.after(120, self._mark_clean)
        # Once per run, not per photograph: the first one is where it matters.
        if not getattr(self, "_lama_told", False):
            self._lama_told = True
            self.after(400, self._fill_fallback_notice)

    def _fill_fallback_notice(self, note=""):
        """lama chosen and not installed: say telea fills instead, and how to
        get lama (user, 2026-10-23: on the first Mac it fell back silently).
        ``note`` is a save's fill note, which names the fallback when it ran."""
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
        if getattr(self, "v_strip", None) is not None and self.v_strip.get():
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
        if mode in ("birefnet", "gdino") and not self.session.settings.birefnet_model:
            # The model comes from `deps` into models/BiRefNet, or from a path
            # remembered from earlier; there is no picker any more (user,
            # 2026-10-24: the mask-source row was for testing only). Missing
            # weights are said, with where they were looked for -- never a
            # file dialog nobody asked for.
            app = self._app()
            remembered = getattr(app, "_remembered", {}) if app is not None else {}
            stored = (remembered.get("birefnet_model", "")
                      or prefs.load().get("birefnet_model", "")
                      or MK.default_birefnet())
            if stored and os.path.isfile(stored):
                self.session.settings = self.session.settings.replace(
                    birefnet_model=stored)
            else:
                self.v_maskmode.set("off")
                if getattr(self, "v_gdino", None) is not None:
                    self.v_gdino.set(False)
                self._notify(f"Grounding DINO needs the BiRefNet model -- not found in "
                             f"{MK.birefnet_home()}. Install it with 'deps' at the bottom right.")
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

    def _toggle_mask(self):
        """Show the excluded region and the lines it removed.

        A mask the user cannot see is a mask the user cannot trust: when a
        segmenter takes out the wrong half of a building, the only other symptom
        is a quietly worse answer."""
        if self.session is None:
            return
        self.session.show_mask = bool(self.v_show_mask.get())
        self._schedule_redraw()

    def _slider(self, parent, row, label, var, lo, hi, unit, inc, fmt):
        ttk.Label(parent, text=label, width=18).grid(row=row, column=0, sticky="w")
        sc = ttk.Scale(parent, from_=lo, to=hi, variable=var, orient="horizontal",
                       length=layout.SLIDER_MIN, command=lambda _v: self._on_slider())
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
        if getattr(self, "v_strip", None) is not None and self.v_strip.get():
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
        if getattr(self, "v_strip", None) is not None and self.v_strip.get() \
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

    def _loupe_follow(self, size, event=None):
        """Where the glass goes: beside the cursor, by ``LOUPE_OFFSET``.

        User, 2026-10-23: "loupe soll mauszeiger folgen". It was parked in the
        gutter -- 8 px wide, so a 362 px glass sat half over the before picture
        and hid the very corners being aimed at -- and then jumped onto the
        after picture. Below-right of the pointer, flipped to the other side
        where the panel ends, so it never covers the point it magnifies.
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
        alt = bool(getattr(event, "state", 0) & ALT_MASK)
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
        if getattr(self, "v_strip", None) is not None and self.v_strip.get():
            hit = self._strip_ruler_at(event.x, event.y)
            if hit is not None:
                self._on_strip_drag_start(hit)
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
        #   row 0  LINES      detector | weights | clear marks (2 cells)
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
        app = self._app()
        if app is not None:
            app.btn_weights = put(ttk.Button(g, text="weights", style="Compact.TButton",
                                             command=app._download_models), 0, 2)
            _attach_tooltip(app.btn_weights, "Download or check detector model weights")
        # No brush-width field: the pen is sized on the picture, Shift+left
        # or Alt+right dragged sideways (user, 2026-10-20).
        _b = ttk.Button(g, text="Clear marks", style="Compact.TButton",
                        command=self._clear_marks)
        _b.grid(row=0, column=3, columnspan=2, sticky="nsew")
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
                             "Applies to every source at once - brush, SAM, "
                             "gdino, BiRefNet and the facade strip together.")
        self.v_alpha = tk.DoubleVar(value=0.28)
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
        if getattr(self, "v_sam", None) is not None and self.v_sam.get():
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
        if getattr(self, "v_stroke", None) is not None and self.v_stroke.get():
            self._on_stroke_drag(event)

    def _on_before_b1release(self, event):
        # A Shift+drag pen sizing whose Shift came up before the button ends
        # here, not in `_on_shift_release`; a kept anchor then swallowed the
        # next right-drag erase (OpenCode review, 2026-10-24).
        if getattr(self, "_pen_anchor", None) is not None:
            return self._on_pen_size_end(event)
        if getattr(self, "v_sam", None) is not None and self.v_sam.get():
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
        if getattr(self, "v_stroke", None) is not None and self.v_stroke.get():
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

    def _on_right_press(self, event):
        """Right-click: SAM prompt reset when SAM is on, erase stroke otherwise."""
        if getattr(self, "v_sam", None) is not None and self.v_sam.get():
            self._on_sam_right_click(event)
            return "break"
        # Rectangle: right-click clears the quad. A LEFT click no longer does,
        # because wiping four placed corners because somebody missed a handle is
        # a destructive answer to a miss; clearing is now a gesture of its own.
        if getattr(self, "v_rect", None) is not None and self.v_rect.get():
            if self.session is not None and self.session.planar_quad:
                self.session.clear_planar()
                self.c_before.delete("rect_rubber")
                self._set_status("PC Rectangle cleared -- click the first corner")
                self._redraw()
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
                                      outline="", fill=OVERLAY["paint"],
                                      tags="stroke_preview")
            return
        flat = [c for p in pts for c in (ox + p[0], oy + p[1])]
        self.c_before.create_line(flat, width=2 * r, fill=OVERLAY["paint"],
                                  capstyle="round", joinstyle="round",
                                  tags="stroke_preview")

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
            self._drag_job = None
            if self.session is None:
                return
            if getattr(self, "_mark_drag", None) is not None:
                self.session.refit()
            self._redraw()
        self._drag_job = self.after(layout.DRAG_PREVIEW_MS, tick)

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
        self._fill_after = self.after(layout.DEFERRED_FILL_MS, self._fill_start)

    def _fill_start(self):
        self._fill_after = None
        s = self.session
        job = getattr(s, "deferred_fill", None) if s is not None else None
        if job is None:
            return
        if self._fill_thread is not None and self._fill_thread.is_alive():
            # One model, one job: wait for the running one, then do the latest.
            self._fill_after = self.after(150, self._fill_start)
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
        self.after(60, self._fill_poll)

    def _fill_poll(self):
        try:
            gen, img, err = self._fill_q.get_nowait()
        except queue.Empty:
            if self._fill_thread is not None and self._fill_thread.is_alive():
                self.after(60, self._fill_poll)
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
                fill=INK["dim"], font=ui_font(13))
        self._set_status("")

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
            before = self.session.render_before(max_edge=max(lock_b),
                                                show_lines=self.v_show_lines.get())
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

        _mk_btn = tool("mark", self.v_mark, self._on_mark_toggle,
                       "Mark (M): click two points on a straight edge.\n"
                       "It comes out exactly vertical or horizontal --\n"
                       "the way it leans decides which. Click a line to\n"
                       "remove it. 2 lines of a direction override the\n"
                       "detector. Hold Alt for a slow, fine loupe.",
                       lead=True)
        _pr_btn = tool("rect", self.v_rect, self._on_rect_toggle,
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
        self._gdino_btn.bind(f"<Button-{RB}>", lambda _e: self._ask_gdino_prompt())
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
        _strip_btn = tool("strip", self.v_strip, self._on_strip_icon_click,
                        "Facade strip: restrict horizontal evidence\n"
                        "to one facade on corner views.\n"
                        "Drag the two rulers on the before picture.")
        # NOTE: this used to say the quad had left the palette and that
        # nothing in the window pointed at it -- sitting directly above
        # `tool("rect", ...)`. The quad came back and the comment stayed, which
        # is the exact shape of defect this project keeps paying for: a reader
        # trusts the prose over the line of code beside it.

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
            colour = (OVERLAY["check_ok"] if _off_angle_deg(x0, y0, x1, y1) <= CHECK_LINE_TOL_DEG
                     else (OVERLAY["check_v_off"] if abs(y1 - y0) >= abs(x1 - x0)
                           else OVERLAY["check_h_off"]))
            self.c_after.create_line(ox + x0, oy + y0, ox + x1, oy + y1,
                                     fill=colour, width=1, tags="after_lines")
        # Second pass: M-LSD in a distinct colour (cyan) so both detectors
        # are visible for comparison.  Only when the primary is not already mlsd.
        if self.session.settings.detector != "mlsd":
            try:
                seg_m, _ = _L.detect_segments(gray, min_len, "mlsd",
                                              small, self.session.settings)
            except Exception:
                seg_m = None
            if seg_m is not None and len(seg_m):
                for x0, y0, x1, y1 in seg_m:
                    colour = (OVERLAY["mlsd_ok"] if _off_angle_deg(x0, y0, x1, y1) <= CHECK_LINE_TOL_DEG
                             else (OVERLAY["mlsd_v_off"] if abs(y1 - y0) >= abs(x1 - x0)
                                   else OVERLAY["mlsd_h_off"]))
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
        self._wait_hide()
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
        self._notice_job = self.after(layout.NOTICE_MS, self._clear_notice)
        self._draw_status_overlay()

    def _clear_notice(self):
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
class App(_ROOT_CLASS):
    def __init__(self, initial=None, start_maximized=True, start_fullscreen=False,
                 in_taskbar=True):
        super().__init__()
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
        self.after(0, lambda: _dark_titlebar(self))
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
        self.after(120, self._pump)
        if start_fullscreen:
            self.after(50, self._enter_fullscreen)
        elif start_maximized:
            self.after(50, self._maximize)

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
            self.after(100, lambda: _dark_titlebar(self))
            self.after(400, lambda: _dark_titlebar(self))
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
        pad = dict(padx=6, pady=2)
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
            # It does not fail: `inpaint.fill` falls back to telea (2026-10-10).
            text = (f"{mode} unavailable, telea fills instead -- " + text
                    + ("  ('deps' installs it)" if mode == "lama" else ""))
        self.lbl_fill.configure(text=text)

    def _check_for_update(self):
        """The `update` button beside the version number.

        Same shape as `_download_models` below, for the same reason: a network
        call behind a button. The work happens on a daemon thread so the window
        keeps answering, the label carries the state instead of a second
        widget, and every return to Tk goes through `self.after(0, ...)` --
        touching a widget from the worker thread is the bug this pattern
        exists to avoid.

        Two steps, and the user decides between them. `selfupdate.check` only
        looks; nothing moves until they say yes to what it found. A button that
        changes the program under you without showing you what it is taking is
        not something to press twice.
        """
        if getattr(self, "_upd_busy", False):
            return
        from . import selfupdate as SU
        self._upd_busy = True
        btn = self.btn_update
        # The label is the indicator, the way `_download_models` does it below:
        # a second widget for two seconds of network is not worth the width.
        btn.configure(text="checking")

        def done():
            btn.configure(text="update")
            self._upd_busy = False

        def found(info):
            done()
            if not info.get("can_update"):
                # Not an error: "already up to date" arrives here too, and so
                # does every refusal. They are all answers to what was asked.
                messagebox.showinfo("Update", info["message"], parent=self)
                return
            detail = info.get("log", "")
            ask = info["message"]
            if detail:
                ask += "\n\n" + "\n".join("   " + ln
                                          for ln in detail.splitlines()[:12])
            ask += "\n\nTake them now?"
            if not messagebox.askyesno("Update", ask, parent=self):
                return
            self._upd_busy = True
            btn.configure(text="updating")

            def pulled(ok, text):
                done()
                (messagebox.showinfo if ok else messagebox.showerror)(
                    "Update", text, parent=self)

            def pull():
                try:
                    ok, text = SU.apply()
                except Exception as exc:               # never lose the button
                    ok, text = False, str(exc)
                self.after(0, lambda: pulled(ok, text))

            threading.Thread(target=pull, daemon=True).start()

        def look():
            try:
                info = SU.check()
            except Exception as exc:
                info = {"can_update": False, "message": str(exc)}
            self.after(0, lambda: found(info))

        threading.Thread(target=look, daemon=True).start()

    def _install_deps(self):
        """The `deps` button: a menu of the optional packages.

        User, 2026-10-24: "soll nicht blind installieren. torch lama telea
        geo". It used to list what was missing and install all of it after one
        yes. Now every MISSING package is a row (what is installed is one line
        and never offered: "dont ask for things that are already there"),
        telea is named as the built-in fill, nothing is ticked, and only what
        the user ticks is installed. Ticking lama or GeoCalib ticks torch
        too while torch is missing (both cannot run without it).

        The work runs as before: a daemon thread with the button label as the
        indicator, back to Tk through `self.queue` (Tk refuses `after` from a
        thread whenever no mainloop runs -- found by the test, which pumps).
        Installs into the Python running the window.
        """
        if getattr(self, "_deps_busy", False):
            return
        from . import depinstall as DI
        old = getattr(self, "_w_deps", None)
        if old is not None:
            try:
                old.destroy()
            except tk.TclError:
                pass
        rows = DI.catalogue()
        # Only what is missing is offered (user: "dont ask for things that
        # are already there"); what is installed is one line, for reference.
        have = [r for r in rows if r["installed"]]
        rows = [r for r in rows if not r["installed"]]
        if not rows:
            messagebox.showinfo("Optional packages",
                                "Everything optional is installed already:\n   "
                                + "\n   ".join(r["label"] for r in have), parent=self)
            return
        win = tk.Toplevel(self)
        win.configure(bg=INK["bg"])
        win.title("Optional packages")
        win.transient(self)
        self._w_deps = win
        body = ttk.Frame(win, padding=(10, 8))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Tick what to install. Nothing is installed unasked.").pack(anchor="w")
        self._deps_vars = {}
        boxes = {}
        torch_there = any(r["key"] == "torch" and r["installed"] for r in rows)

        def ticked(key):
            # lama/GeoCalib without torch cannot run: take torch along, and
            # keep it while anything that needs it is ticked.
            v = self._deps_vars
            if key in DI.NEEDS_TORCH and v[key].get() and not torch_there:
                v["torch"].set(True)
            if key == "torch" and not v["torch"].get():
                for k in DI.NEEDS_TORCH:
                    if k in v and not rows_by[k]["installed"]:
                        v[k].set(False)

        rows_by = {r["key"]: r for r in rows}
        for r in rows:
            v = tk.BooleanVar(value=False)
            self._deps_vars[r["key"]] = v
            cb = ttk.Checkbutton(body, variable=v, text=f"{r['label']}  ({r['size']})",
                                 command=lambda k=r["key"]: ticked(k))
            cb.pack(anchor="w", pady=(6, 0))
            ttk.Label(body, text="      " + r["purpose"], style="Dim.TLabel").pack(anchor="w")
            boxes[r["key"]] = cb
        self._deps_boxes = boxes
        if have:
            ttk.Label(body, text="already installed: " + ", ".join(r["key"] for r in have),
                      style="Dim.TLabel").pack(anchor="w", pady=(6, 0))
        ttk.Label(body, text="telea fill  (built in, always there)").pack(anchor="w", pady=(6, 0))
        ttk.Label(body, text="      fills the corners when lama is missing",
                  style="Dim.TLabel").pack(anchor="w")
        ttk.Label(body, text=f"Into the Python running this window:\n   {DI.pip_python()}",
                  style="Dim.TLabel").pack(anchor="w", pady=(8, 0))
        bar = ttk.Frame(body)
        bar.pack(fill="x", pady=(8, 0))

        def go():
            chosen = [dict(rows_by[k]) for k, v in self._deps_vars.items()
                      if v.get() and not rows_by[k]["installed"]]
            if not chosen:
                self._notify("nothing ticked -- nothing installed")
                return
            win.destroy()
            self._w_deps = None
            self._deps_run(DI.in_order(chosen))
        self._deps_go = go
        ttk.Button(bar, text="Close", command=win.destroy).pack(side="right")
        ttk.Button(bar, text="Install ticked", style="Accent.TButton",
                   command=go).pack(side="right", padx=(0, 6))
        _dark_titlebar(win)

    def _deps_run(self, items):
        """Install ``items`` (ticked in the deps menu) on a thread."""
        from . import depinstall as DI
        self._deps_busy = True
        btn = self.btn_deps
        btn.configure(text="installing")
        self._notify("installing " + ", ".join(i["key"] for i in items)
                     + " -- the window stays usable")

        def step(i, n, label):
            self.queue.put(("call", lambda: btn.configure(text=f"{i + 1}/{n}")))

        def finished(results):
            btn.configure(text="deps")
            self._deps_busy = False
            ok, text = DI.summary(results)
            if any(item["key"] == "lama" and good for item, good, _r in results):
                text += "\n\nlama fills from the next preview or save on."
            if any(item["key"] == "geocalib" and good for item, good, _r in results):
                text += ("\n\nGeoCalib guesses the focal length from the next photograph "
                         "on, where neither EXIF nor the lines decide it.")
            (messagebox.showinfo if ok else messagebox.showwarning)(
                "Optional packages", text, parent=self)
            self._notify("optional packages installed" if ok
                         else "some optional packages could not be installed")
            if any(good for _i, good, _r in results):
                self._check_fill()

        def work():
            try:
                results = DI.install(items, on_step=step)
            except Exception as exc:                   # never lose the button
                results = [(items[0], False, str(exc))]
            self.queue.put(("call", lambda: finished(results)))

        threading.Thread(target=work, daemon=True).start()

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
        # A verdict is news; "not checked yet" (start-up, an address being
        # typed) is not, and on the picture it read as an error.
        if state != "unknown":
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
        from PIL import Image, ImageGrab
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
                         on_closed=lambda: self.after(50, self._open_next_review),
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

    def _stop(self):
        self.stop_flag.set()

    def _pump(self):
        try:
            while True:
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
        self.after(120, self._pump)

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
