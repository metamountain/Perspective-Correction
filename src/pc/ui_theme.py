"""Presentation layer for the review window: palettes, fonts, glyphs, chrome.

Everything the window is built out of visually, kept out of ``gui.py`` so that
module holds the review window and nothing else:

* the palette tables -- ``INK`` for chrome, ``OVERLAY`` for marks drawn ON a
  photograph (which deliberately do not follow the theme), ``THEMES`` for the
  switchable palettes, plus the fixed names (``MASK_SWATCHES``, ``ICON_WHITE``,
  ``GUIDE_GREY``);
* the colour helpers (WCAG contrast, the readable accent-button ink);
* font registration and resolution (the shipped faces, a theme's own face);
* the drawn glyphs and the stock icon fonts;
* the window chrome (taskbar icon, dark title bar);
* the plain hover tooltip.

``INK`` is live module state: a theme switch mutates it in place
(``INK.update(...)``), so every reader -- here and in ``gui.py`` -- sees the
new colours on the next draw. The dict is shared by object identity, never
copied.
"""
from __future__ import annotations

import os
import sys

import tkinter as tk
from tkinter import ttk

import cv2
import numpy as np
from PIL import Image, ImageTk

from . import layout


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
    "#39ff14", "#30ffd0", "#3090ff", "#3030ff",
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
    # "Outline": where the photograph's own border landed after the warp
    "outline":     "#ffe14d",
    # the line overlays -- the detected lines on the before pane and the
    # "Check lines" re-run on the after pane, in two palettes -- live in
    # lineoverlay.LINE, the one table both panes read.
    # the loupe crosshair; cyan while Alt damps the tracking, so the damping
    # is visible rather than only felt
    "loupe":       "#000000",
    "loupe_alt":   "#00e5ff",
    # what the mask wash is tinted with until the user picks otherwise
    "mask_default": "#39ff14",   # neon green (user, 2026-10-24)
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
      * ``brush`` is a swatch: a filled square with a dark disc (2026-10-24). It is the one key
        that is a swatch rather than a diagram -- the mark stands for the paint
        it lays down (user, 2026-09-15).
      * the Grounding DINO key kept its dinosaur, a pun the user put there --
        until the key itself went (2026-10-24, its model is 690 MB).
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
        # A swatch, not a diagram -- see the docstring. A grey square with a
        # dark disc inside (user, 2026-10-24: "Quadrat hellgrau, schwarzer
        # Kreis innen"): the disc is cut back to clear, so the key's own dark
        # shows through, and the square turns white when held.
        dr.rectangle([P(0.06, 0.06), P(0.94, 0.94)], fill=ink)
        r = 0.30 * d_size
        x, y = P(0.5, 0.5)
        dr.ellipse([x - r, y - r, x + r, y + r], fill=(0, 0, 0, 0))
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
