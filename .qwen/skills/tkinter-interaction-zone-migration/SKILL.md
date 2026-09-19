---
name: tkinter-interaction-zone-migration
description: How to move a click/drag/hover interaction zone from a child canvas to a parent container (panel/frame) in Tkinter, including the full cleanup checklist for dead code.
source: auto-skill
extracted_at: '2026-09-16T09:55:56.486Z'
---

# Migrating an interaction zone from a child canvas to a parent container

## The problem

An interaction (e.g., ruler drag) is bound to a specific widget (a `Canvas`) but the user wants it in a different location — specifically, in the **parent container's padding/border area** that surrounds the canvas. The old code path lives in 3–4 separate event handlers on the child; the new code must be added to the parent's existing handlers without breaking their other responsibilities.

## Why this is non-trivial

1. **The parent already has handlers for a different purpose.** In this project, the panel's cross/border area already handles guide-pull gestures (`_on_cross_press/pull/drop`). Adding ruler logic means inserting a spatial check at the top of each handler that says "if in ruler zone → do ruler thing and return; else fall through to guide logic."

2. **Dead code is scattered across multiple handlers.** The old canvas had ruler logic in `_on_click_before` (press), `_on_before_b1motion` (drag), `_on_before_b1release` (release), and `_on_before_motion` (hover cursor). Missing even one leaves a ghost: the state flag (`_ruler_dragging`) gets set but never cleared, or the cursor sticks.

3. **Spatial check uses a different coordinate origin.** The parent's `event.y` is relative to the panel; the child's was relative to the canvas. The zone boundary must be recomputed in the parent's space (e.g., `event.y < CROSS_BORDER and event.x >= self.cell_after.winfo_x()`).

## The migration pattern

### Step 1: Identify the full old code path

Grep for the state flag (e.g., `_ruler_dragging`) across the file. Every hit is a handler that must be cleaned:

```
_on_click_before        → press/activate   (remove)
_on_before_b1motion     → drag             (remove)
_on_before_b1release    → release          (remove)
_on_before_motion       → hover cursor     (remove or keep if zone still overlaps)
```

### Step 2: Add the spatial gate to the parent's existing handlers

The parent already has `_on_cross_press`, `_on_cross_pull`, `_on_cross_drop`. Insert the ruler check **before** the guide logic in each:

```python
def _on_cross_press(self, event):
    if getattr(self, "_ph_a", None) is None:
        return
    side = self._cross_side(event)   # nearest-edge; see "Zone detection" above
    if side is None:
        return
    self._ruler_visible = True
    self._ruler_dragging = True
    self._ruler_drag_side = side
    self.config(cursor=("sb_v_double_arrow" if side in ("top", "bottom")
                        else "sb_h_double_arrow"))
    self._schedule_redraw()

def _on_cross_pull(self, event):
    if not getattr(self, "_ruler_dragging", False):
        return
    side = self._ruler_drag_side
    aox, aoy = self._after_off
    iw = self._ph_a.width() if self._ph_a else 0
    ih = self._ph_a.height() if self._ph_a else 0
    # Measure from the IMAGE edge, not the panel edge (see "Drag positioning"):
    if side == "top":    pos, limit = event.y - aoy,        ih
    elif side == "bottom": pos, limit = (aoy + ih) - event.y, ih
    elif side == "left":  pos, limit = event.x - aox,        iw
    else:                 pos, limit = (aox + iw) - event.x, iw
    setattr(self, f"_ruler_{side}", max(2, min(limit, pos)))
    self._schedule_redraw()

def _on_cross_drop(self, event):
    if getattr(self, "_ruler_dragging", False):
        self._ruler_dragging = False
        self.config(cursor="")
        self._schedule_redraw()
```

### Step 3: Remove dead code from the child canvas handlers

For each handler in the old widget, remove the ruler block. The pattern is always the same shape:

```python
# In _on_click_before (press):
-        if event.y < RULER_MARGIN and RULER_MARGIN <= event.x <= cw - RULER_MARGIN:
-            self._ruler_y = ...
-            self._ruler_dragging = True
-            ...
-            return

# In _on_before_b1motion (drag):
-        if getattr(self, "_ruler_dragging", False):
-            self._ruler_y = ...
-            self._schedule_redraw()
-            return

# In _on_before_b1release (release):
-        if getattr(self, "_ruler_dragging", False):
-            self._ruler_dragging = False
-            self.c_before.config(cursor="sb_v_double_arrow")
-            self._schedule_redraw()
-            return

# In _on_before_motion (hover):
-        in_top_border = event.y < RULER_MARGIN and ...
-        if in_top_border:
-            self.c_before.config(cursor="sb_v_double_arrow")
-        elif ...  # ← restructure: the elif becomes an if
```

**The `elif` → `if` restructuring is easy to miss.** When you remove the first branch of an `if/elif/else` chain, the remaining `elif` must become `if`, otherwise the code still references the removed condition's truthiness.

### Step 4: Verify no orphaned state remains

After cleanup, grep for the state flag one more time. The only remaining references should be in the **parent** handlers (the new code). If any reference remains in a child handler, it's dead code that will never execute (because the flag is only set by the parent now) — remove it.

## Coordinate conversion at the boundary

### Zone detection: nearest-edge with ABSOLUTE distances

When the interaction zone is the **entire** black cross (both gutters + all borders), do NOT try to match thin strips. Instead, compute which child content edge is nearest to the pointer using **absolute** distances:

```python
def _cross_side(self, event):
    """Return the Q2 edge nearest the pointer, or None if no photo loaded."""
    if getattr(self, "_ph_a", None) is None:
        return None
    aox, aoy = self._after_off          # image offset within the child canvas
    iw, ih = self._ph_a.width(), self._ph_a.height()
    img_l, img_r = aox, aox + iw        # Q2 image edges in panel coords
    img_t, img_b = aoy, aoy + ih
    d_top   = abs(event.y - img_t)      # distance to top edge (always ≥ 0)
    d_bot   = abs(event.y - img_b)      # distance to bottom edge (always ≥ 0)
    d_left  = abs(event.x - img_l)      # distance to left edge (always ≥ 0)
    d_right = abs(event.x - img_r)      # distance to right edge (always ≥ 0)
    if min(d_top, d_bot) < min(d_left, d_right):
        return "top" if d_top <= d_bot else "bottom"
    return "left" if d_left <= d_right else "right"
```

**Why `abs()` and not signed distances:** A signed formulation (`d_bot = img_b - event.y`) goes negative when the pointer is *below* the image, so a point 50 px below the bottom edge has `d_bot = -50` which is always `< d_left` (positive) — the "bottom" branch never fires for points actually in the bottom border. The cross extends far beyond the image in all directions; the pointer can be outside the image on *any* axis, so distances must be unsigned.

**Why nearest-edge, not strip-matching:** The old code tested `event.y < CROSS_BORDER` for "top" and `event.x >= q2_left and event.x < w - b` for "right". The second condition matched the *entire* Q2 column width, not just the right border — a press in the vertical gutter grabbed the wrong side. Nearest-edge is robust to any cross width and needs no per-strip boundary constants.

### Test coordinates: anchor to IMAGE edges, not panel dimensions

When writing tests for nearest-edge zone detection with an inset image (e.g., 701×526 centered in a 1904×1156 panel), **every press point must be positioned relative to the image's own edges** (`aox`, `aoy`, `aox+iw`, `aoy+ih`), never relative to the panel dimensions:

```python
# CORRECT — just outside each image edge, within the cross:
press_top    = _E(aox + iw_ // 2, aoy - CROSS_BORDER // 2)       # above image top
press_bot    = _E(aox + iw_ // 3, aoy + ih_ + CROSS_BORDER // 2) # below image bottom
press_left   = _E(aox - CROSS_BORDER // 2, aoy + ih_ // 2)       # left of image
press_right  = _E(aox + iw_ + CROSS_BORDER // 2, aoy + ih_ // 2) # right of image

# WRONG — panel-relative; the cross extends hundreds of px beyond the image,
# so "panel center" or "panel edge" is NOT near the corresponding image edge:
press_left   = _E(CROSS_BORDER // 2, ph_ // 2)   # y=578 > img_b=526 → "bottom"!
press_bot    = _E(bot_x, ph_ - CROSS_BORDER // 2) # y=1146, d_bot=620 > d_left=233!
press_right  = _E(pw - CROSS_BORDER // 2, ...)   # x=1894, d_right=1083 → "left"!
```

**The trap:** with a small image in a large panel, the cross gutters are hundreds of pixels wide. A point at `ph_//2` (panel vertical center) can be *below* the image bottom, making the bottom edge closer than the left edge. Always verify: for a "left" press, `d_left < d_top`, `d_left < d_bot`, AND `d_left < d_right`. The simplest guarantee: put the cross-axis coordinate at the image midpoint (`aoy + ih_//2` for side presses) and the drag-axis coordinate just outside the image edge.

### Drag positioning: measure from the image edge, NOT the panel edge

This is the critical rule that was missed initially and caused the "strange offset" bug:

```python
# CORRECT — offset is pointer distance from the IMAGE edge:
if side == "top":
    pos = event.y - aoy              # negative when pointer is in the cross → clamps to 2
elif side == "bottom":
    pos = (aoy + ih) - event.y       # negative when pointer is below the image
elif side == "left":
    pos = event.x - aox
else:  # right
    pos = (aox + iw) - event.x
new_val = max(2, min(limit, pos))

# WRONG — measures from panel edge; cross is ~40px outside the image, so
# a pointer at y=10 in the top border gives pos=10-0=10, but the image starts
# at y≈60, so the ruler appears 50px too deep inside the frame:
pos = event.y - 0  # panel edge
```

**The geometry:** The cross (border + half-gap ≈ 40 px) sits *outside* the child canvas content. A pointer in the cross is always past the image edge on the drag axis, so `pos` is negative and clamps to the minimum (2). As the user drags into the picture, `pos` grows from 0 → dim, and the ruler follows 1:1. Measuring from the panel edge adds a phantom ~40 px offset that shifts every ruler inward.

**Rule: when the interaction zone is in a parent's padding, all position math must reference the child content's edges (`aox`, `aoy`, `aox+iw`, `aoy+ih`), never the panel's own edges (0, 0, w, h).**

## Cleanup checklist (run after migration)

- [ ] Grep for the old state flag → only parent handlers reference it
- [ ] Grep for the old zone constant (e.g., `RULER_MARGIN`) in child handlers → no hits
- [ ] The `elif` chain in `_on_before_motion` is restructured to `if`
- [ ] Parent's `_on_cross_press/pull/drop` each have an early-return for the new zone
- [ ] No handler sets the flag without a corresponding clear (press sets, drop clears)
- [ ] Cursor is set on press and cleared on release (both in the parent)
- [ ] Full test suite passes (catches any import or syntax error from the edit)

## Pitfalls

- **The `return` after the zone check is mandatory.** Without it, a click in the ruler zone also starts a guide pull, and the user sees a ghost guide line appearing during ruler drag.
- **Do not remove the hover-reveal (`_on_cross_motion`).** The parent's `<Motion>` handler already reveals the ruler on hover over the cross. This is separate from the old canvas-level hover cursor and must stay.
- **The visual drawing location may differ from the interaction zone.** In this project, the ruler *line* is still drawn inside `c_after`, but the *interaction* (click/drag) happens in the panel's cross/border area. This is intentional: the user sees the line on the image but manipulates it from outside the canvas. Do not "fix" this by moving the draw call — the interaction zone and the visual location are decoupled by design.
- **`winfo_x()` returns 0 before the widget is mapped.** If the handler can fire before the first layout pass, guard with `if self.cell_after.winfo_id() == 0: return`. In practice, button events only arrive after the window is shown, so this is rarely needed.
- **Panel-edge vs image-edge measurement (the "strange offset" bug).** The most common mistake when implementing drag positioning in a parent's padding: using `event.y - 0` (panel edge) instead of `event.y - aoy` (image edge). The cross is ~40 px wide, so the error is invisible at the border (both give small values) but becomes a 40-px phantom shift the moment the pointer enters the picture. **Always measure from the child content's edge.**
- **Strip-matching breaks with wide gutters.** Testing `event.x >= q2_left and event.x < w - b` for "right" matches the entire Q2 column, not just the right border. Use nearest-edge distance comparison instead — it is correct for any cross width and needs no per-strip boundary constants.
