---
name: tkinter-runtime-theme-switching
description: How to add a runtime theme/palette switcher to a Tkinter app that uses a module-level INK dict referenced in 65+ widget constructors.
source: auto-skill
extracted_at: '2026-09-13T20:40:04.507Z'
---

# Runtime theme switching for Tkinter (INK-dict pattern)

## The problem

A module-level `INK` dict holds palette keys (`bg`, `panel`, `field`, etc.) and is
referenced in 65+ widget constructors at build time. You cannot simply reassign
`INK = new_dict` — existing widgets captured the old string values. And you
cannot rebuild the entire UI on every switch (expensive, loses state).

## Architecture (three pieces)

### 1. Keep INK mutable; add a THEMES catalog

```python
INK = {"bg": "#16181c", "panel": "#1d2025", ...}  # the *current* palette, mutated in place

THEMES = {
    "Minimal Black": {"bg": "#16181c", ...},
    "C64":           {"bg": "#40318d", ..., "ui_font": "Courier New"},
    ...
}
```

INK is the live reference. Switching a theme does `INK.update(THEMES[name])` so
any code that reads `INK["key"]` after the switch sees the new value. Widgets
built *before* the switch are handled by the retint walk (below).

### 2. Make apply_theme accept an optional palette param

```python
def apply_theme(root, palette=None):
    p = palette or INK
    # all ~20 ttk style configs use p["key"]
    # font overrides: p.get("ui_font"), p.get("mono_font")
```

ttk-themed widgets (TButton, TEntry, TCombobox, TScale, Treeview, etc.)
**auto-update** when you reconfigure their style — no per-widget walk needed.
This single call handles the majority of the UI.

### 3. Recursive retint walk for tk widgets with explicit bg/fg

Classic `tk.Frame`, `tk.Canvas`, and `tk.Label` that were constructed with an
explicit `background=` or `bg=` do NOT follow ttk styles. They hold a frozen
string. A recursive walker matches each widget's current color against the
**old** palette and swaps it for the **new** one:

```python
def _retint_bg(widget, old_palette, new_palette):
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
        if wclass == "Frame":          # raw tk.Frame ONLY — NOT TFrame (see pitfall below)
            bg = widget.cget("background")
            new = _swap(bg, old_palette, new_palette)
            if new:
                widget.configure(background=new)
        elif wclass == "Canvas":
            bg = widget.cget("bg")
            fg = widget.cget("fg")
            hb = widget.cget("highlightbackground")
            for opt, val in (("bg", bg), ("fg", fg), ("highlightbackground", hb)):
                new_val = _swap(val, old_palette, new_palette)
                if new_val:
                    widget.configure(**{opt: new_val})
        elif wclass in ("Label", "Button"):
            bg = widget.cget("background")
            fg = widget.cget("foreground")
            for opt, val in (("background", bg), ("foreground", fg)):
                new_val = _swap(val, old_palette, new_palette)
                if new_val:
                    widget.configure(**{opt: new_val})
            for opt in ("activebackground", "activeforeground", "selectcolor"):
                try:
                    val = widget.cget(opt)
                except tk.TclError:
                    continue
                new_val = _swap(val, old_palette, new_palette)
                if new_val:
                    widget.configure(**{opt: new_val})
    except tk.TclError:
        pass  # never let a per-widget error kill the recursive walk

    for child in widget.winfo_children():
        _retint_bg(child, old_palette, new_palette)
```

**Critical pitfall — ttk.Frame (TFrame) does NOT support `cget("background")`:**
`winfo_class()` returns `"TFrame"` for `ttk.Frame`. Calling
`widget.cget("background")` on it raises `_tkinter.TclError: unknown option
"-background"`. If the per-widget logic is not wrapped in try/except, this
exception propagates up and **kills the recursive walk for that entire branch** —
all child widgets (including canvases) under that ttk.Frame are never retinted.

This was the root cause of the Light theme bug: `cell_before`/`cell_after`
are `ttk.Frame` instances in ReviewPanel. The old code had
`wclass in ("Frame", "TFrame")` and called `cget("background")` without
protection. The TclError on the first TFrame killed the walk before reaching
the child canvases (`c_before`, `c_after`) that needed retinting.

Fix: (1) Only handle `wclass == "Frame"` for direct background changes —
TFrame backgrounds are managed by ttk styles via `apply_theme`.
(2) Wrap the entire per-widget block in `try/except tk.TclError` so any
unexpected option error doesn't kill the walk.

**Critical pitfall:** match against `old_palette`, NOT against INK or a hardcoded
default. If the user switches Minimal Black → C64 → Amiga 500, the second switch
must match C64's colors (the old palette), not Minimal Black's. Passing
`dict(INK)` *before* the update captures the correct snapshot.

**Match all INK keys, not just a subset.** The `_swap` helper must iterate over
every key in the INK dict (all 11: bg, panel, field, cross, line, text, dim,
accent, ok, warn, err). Missing even one key means widgets using that color
won't be retinted.

### 4. The switch method

```python
def _switch_theme(self, theme_name):
    old = dict(INK)          # snapshot BEFORE mutation
    new = THEMES.get(theme_name)
    if new is None or theme_name == getattr(self, "_last_applied_theme", None):
        return
    self._last_applied_theme = theme_name
    INK.update(new)          # mutate in place — future reads see new values
    apply_theme(self, new)   # reconfigure all ttk styles (auto-updates themed widgets)
    _retint_bg(self, old, new)  # walk tk widgets with explicit bg/fg
```

**Critical:** the guard must compare against a stored `self._last_applied_theme`
attribute, NOT against the live StringVar. The trace callback fires *after* the
var already holds the new value, so `theme_name == self._theme_var.get()` is
always True and the function returns early every time (silent no-op).

Initialize in `__init__` right after the first `apply_theme(self)` call:
```python
apply_theme(self)
self._last_applied_theme = "Minimal Black"  # matches INK's default palette name
```

## Dropdown wiring

In the header builder, create a `ttk.Combobox(state="readonly", values=list(THEMES.keys()))`
and return it alongside the bar. **ttk.Combobox does NOT support a `command`
option** (that's `tk.OptionMenu` only). Use StringVar trace instead:

```python
# In _brand_header:
theme_var = tk.StringVar(value="Minimal Black")
if on_select is not None:
    theme_var.trace_add("write", lambda *_: on_select(theme_var.get()))
combo = ttk.Combobox(bar, textvariable=theme_var, values=list(THEMES.keys()),
                     state="readonly", width=14)
combo.pack(side="right", padx=(0, 8), pady=2)
return bar, theme_var, combo

# In App._build:
_, self._theme_var, self._theme_combo = _brand_header(self, on_select=self._switch_theme)
```

The trace fires with the new value already in the var — pass it through to the
callback. The callback's guard (see above) prevents redundant re-application.

## Font pairing per theme (typography axis)

Each theme can override `ui_font` and `mono_font`. The existing `_pick_family`
helper checks `tkfont.families(root)` for availability and falls back gracefully:

| Theme | UI font | Mono font | Rationale |
|-------|---------|-----------|-----------|
| Minimal Black / Light | system default (Segoe UI) | system default | clean, no character push |
| C64 | Courier New | Courier New | authentic 8-bit monospace |
| Amiga 500 | Verdana | Consolas | 90s workstation feel |

## Toplevel windows (the "loading windows still dark" pitfall)

`tk.Toplevel` windows do **not** inherit ttk theme styling. Their child
`tk.Label`, `tk.Frame`, etc. widgets without explicit `bg`/`fg` fall back to
OS defaults (dark grey on Windows). The `_retint_bg` walk starts from `self`
(the main App window) and never reaches dynamically-created Toplevels.

**Fix: set colors explicitly at creation time from INK.** Every Toplevel must
call `win.configure(bg=INK["bg"])` immediately after construction, and every
child `tk.Label`/`tk.Frame` must pass `bg=INK[...]`, `fg=INK[...]` explicitly
rather than relying on inheritance.

```python
win = tk.Toplevel(self)
win.configure(bg=INK["bg"])          # ← without this, OS default (dark grey)
lbl = tk.Label(win, text="...", bg=INK["bg"], fg=INK["text"])  # ← explicit
```

This is sufficient for transient/short-lived windows (tooltips, download
progress, settings dialogs). If a Toplevel stays open across a theme switch,
you would additionally need to walk it in `_switch_theme`:

```python
# In _switch_theme, after retinting the main window:
for w in getattr(self, "_open_toplevels", []):
    if w.winfo_exists():
        _retint_bg(w, old, new)
```

In practice the three Toplevels in this project (tooltip, BiRefNet download,
ComfyUI settings) are all short-lived or withdrawn-on-close, so creation-time
INK values are enough.

## What does NOT need retinting

- All `ttk.*` widgets (TButton, TEntry, TCombobox, TScale, Treeview, TProgressbar)
  — they read from the style system which `apply_theme` reconfigures.
- Widgets that use `INK["key"]` as a *default* argument but are rebuilt on every
  load cycle (e.g. preview canvases destroyed and recreated by `ReviewPanel.load`).

## Edit-size pitfall

The THEMES dict + apply_theme refactor is too large for a single `edit` call
(max_tokens truncation). Split into:
1. Insert THEMES after INK (one edit)
2. Modify apply_theme signature + body (second edit)
3. Add _retint_bg helper (third edit)
4. Add dropdown + switch method (fourth/fifth edits)
