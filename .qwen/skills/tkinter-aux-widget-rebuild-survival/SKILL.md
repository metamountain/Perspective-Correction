---
name: tkinter-aux-widget-rebuild-survival
description: How to keep a mode-bound auxiliary widget (loupe Toplevel, overlay canvas) alive across _build() rebuilds in the BPC review panel.
source: auto-skill
extracted_at: '2026-09-14T06:23:20.760Z'
---

# Auxiliary widget survival across `_build()` rebuilds

## The problem

`ReviewPanel._build()` destroys and recreates all review-panel widgets every time
a new image loads (or the user toggles a major mode). Any **dynamically-created**
auxiliary widget — the loupe Toplevel, a floating tooltip canvas, a temporary
overlay — is destroyed unconditionally in that path. If the *mode* that owns it
is still active (e.g. mark mode is on, planar mode is on), the user expects the
widget to persist; silently losing it reads as a bug.

## The pattern: destroy → re-show if mode still active

At the end of the destruction block in `_build()`, check whether the BooleanVar
that gates the widget's owning mode is still True, and call the show method:

```python
# Inside _build(), after the existing "destroy old widgets" section:
if getattr(self, "_loupe", None) is not None:
    self._loupe.destroy()
self._loupe = None

# Re-show if the mode that owns it survived the rebuild.
if getattr(self, "v_mark", None) is not None and self.v_mark.get():
    self._loupe_show()
elif getattr(self, "v_planar", None) is not None and self.v_planar.get():
    self._loupe_show()
```

### Why `getattr` with a default

`_build()` can be called from `__init__` before the BooleanVars are created.
A bare `self.v_mark.get()` would raise `AttributeError`. The `getattr(..., None)`
guard makes the re-show a no-op during initial construction.

### Why check the mode var, not the widget's own state

The mode var (`v_mark`, `v_planar`) is the **source of truth** for whether the
feature is active. The widget itself (`self._loupe`) was just set to `None` two
lines above — checking it would always be False. The mode outlives any single
widget instance.

## Generalisation: any mode-bound auxiliary widget

The same pattern applies to any widget that:

1. Is created on-demand (not in `_build()`'s normal widget tree).
2. Is destroyed by `_build()` (either explicitly or as a child of a destroyed parent).
3. Should persist across image loads **as long as its gating mode is active**.

```python
# Generic form at the end of _build():
for attr, var_attr, show_method in (
    ("_loupe", "v_mark", "_loupe_show"),
    ("_loupe", "v_planar", "_loupe_show"),   # same widget, two modes
):
    if getattr(self, attr, None) is not None:
        getattr(self, attr).destroy()
    setattr(self, attr, None)

# Then re-show for any active mode:
if (getattr(self, "v_mark", None) is not None and self.v_mark.get() or
        getattr(self, "v_planar", None) is not None and self.v_planar.get()):
    self._loupe_show()
```

## What does NOT need this treatment

- **Widgets in the normal `_build()` tree** (frames, buttons, sliders): they are
  recreated every time by design; no survival logic needed.
- **Canvas items drawn by `_redraw()`**: these are redrawn from session state on
  every repaint; they don't "survive" — they are re-derived. The delete handle
  at a line's midpoint, the planar quad fill, the mark endpoints — all of these
  are created fresh in `_draw_marks()` / `_draw_planar_quad()` each time
  `_redraw()` runs. No lifecycle issue.
- **Session state** (`control_lines`, `planar_quad`, etc.): lives in
  `review.py`'s `ReviewSession`, completely independent of the Tk widget tree.

## Pitfall: re-show must happen AFTER the destroy, not before

If you call `_loupe_show()` before the destroy block, the new loupe is
immediately destroyed. The order must be:

1. Destroy old widget (if exists).
2. Set attribute to `None`.
3. Check mode var → re-show if active.

## Testing

No dedicated test for loupe survival (it's a visual/interaction concern). The
off-screen GUI test pattern (`app.geometry("1200x800-4000+0")`) can verify the
loupe Toplevel exists after a second image load while mark mode is active, but
in practice this is verified by hand.
