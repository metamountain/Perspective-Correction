---
name: tkinter-canvas-overlay-guides
description: How to add draggable visual reference guides (lines with add/delete via border-click and drag-off-bounds) on a Tkinter canvas that already has crop/event bindings.
source: auto-skill
extracted_at: '2026-09-13T21:38:06.655Z'
---

# Draggable canvas overlay guides (add by border-click, delete by drag-off)

## The problem

A Tkinter canvas already has `<ButtonPress-1>`, `<B1-Motion>`, `<ButtonRelease-1>`
bound to crop-rectangle logic. You need to layer a new interactive element
(reference guide lines) on top without breaking the existing handlers, and the
interaction model is: click a border zone to add, drag off-bounds to delete.

## Architecture (four methods + wiring)

### State variables

```python
# In __init__ and load():
self._after_guides = []   # list of ("v", pixel_x) or ("h", pixel_y)
self._guide_drag = None   # or ("v"|"h", index) while dragging
```

Positions are in **image-relative pixels** (0..iw, 0..ih), not canvas coordinates.
The offset is stored in `self._after_off = (aox, aoy)` and applied at draw time.

### 1. Hit-test: `_after_guide_at(x, y, iw, ih)`

```python
def _after_guide_at(self, x, y, iw, ih):
    for i, (kind, pos) in enumerate(self._after_guides):
        if kind == "v":
            if abs(x - pos) <= 15 and 0 <= y <= ih:
                return ("v", i)
        else:
            if abs(y - pos) <= 15 and 0 <= x <= iw:
                return ("h", i)
    return None
```

Grab distance is 15 px (matches the border-zone width). Returns `(kind, index)`
or `None`. Called with image-relative coordinates.

### 2. Draw: `_draw_after_guides()`

Uses a **canvas tag** (`"after_guide"`) so all guides are deleted and redrawn
atomically. Visual style mirrors the ROI rulers already in the codebase:
solid `#9fd8ff`, width=2, tick marks every 10 px, pixel-position label.

```python
def _draw_after_guides(self):
    self.c_after.delete("after_guide")
    if not getattr(self, "_ph_a", None):
        return
    aox, aoy = self._after_off
    iw, ih = self._ph_a.width(), self._ph_a.height()
    for kind, pos in self._after_guides:
        if kind == "v":
            x = aox + pos
            self.c_after.create_line(x, aoy, x, aoy + ih,
                                     fill="#9fd8ff", width=2, tags="after_guide")
            for ty in range(0, ih, 10):
                self.c_after.create_line(x-4, aoy+ty, x+4, aoy+ty,
                                         fill="#9fd8ff", width=1, tags="after_guide")
            self.c_after.create_text(x, aoy-6, text=str(int(pos)),
                                     fill="#9fd8ff", font=("Courier", 8),
                                     anchor="s", tags="after_guide")
        # horizontal is symmetric
```

**Critical:** convert image-relative → canvas coords by adding `aox`/`aoy`.
The tag-based delete means partial redraws (during drag) are cheap.

### 3. Drag: `_on_guide_drag(event)`

During drag, do **NOT clamp** the position. The overshoot past bounds is what
enables the delete-off-bounds mechanic on release.

```python
def _on_guide_drag(self, event):
    if self._guide_drag is None or getattr(self, "_ph_a", None) is None:
        return
    kind, idx = self._guide_drag
    aox, aoy = self._after_off
    pos = (event.x - aox) if kind == "v" else (event.y - aoy)
    self._after_guides[idx] = (kind, pos)  # no clamp
    self._draw_after_guides()
```

### 4. Release: `_on_guide_release(event)`

On release, check if the guide is past a margin (20 px) beyond bounds → delete.
Otherwise clamp back into `[0, iw]` or `[0, ih]`.

```python
def _on_guide_release(self, event):
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
    # horizontal symmetric
    self._guide_drag = None
    try: self.c_after.grab_release()
    except tk.TclError: pass
    self._draw_after_guides()
```

## Wiring into existing event handlers

The canvas already has `_on_crop_press`, `_on_crop_drag`, `_on_crop_release`.
Insert the guide logic **before** the crop logic in each handler:

### Press handler (top of `_on_crop_press`):

```python
# After computing x, y, iw, ih (image-relative):
hit = self._after_guide_at(x, y, iw, ih)
if hit is not None:
    kind, idx = hit
    self._guide_drag = (kind, idx)
    try: self.c_after.grab_set()
    except tk.TclError: pass
    return  # ← stop; don't fall through to crop logic

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
# ... existing crop logic continues ...
```

### Drag handler (top of `_on_crop_drag`):

```python
if self._guide_drag is not None:
    self._on_guide_drag(event)
    return
```

### Release handler (top of `_on_crop_release`):

```python
if self._guide_drag is not None:
    self._on_guide_release(event)
    return
```

## Redraw cycle integration

Call `self._draw_after_guides()` in `_show_after` (the full redraw path),
between the static rulers and the detected-line overlay:

```python
self._draw_rulers(self.c_after, *self._after_off, ph.width(), ph.height())
self._draw_after_guides()          # ← here
self._draw_after_lines(arr, ph.width(), ph.height())
```

## Pitfalls

- **Coordinate system**: store positions in image-relative pixels; convert to
  canvas coords only at draw time (`+ aox`, `+ aoy`). Mixing them up causes
  guides to drift when the canvas is resized or the image is re-centered.
- **Tag-based redraw**: always use a single tag for all guide items so
  `delete("after_guide")` clears everything in one call. Don't use per-guide
  tags — they accumulate on drag.
- **No clamp during drag**: if you clamp to `[0, iw]` in `_on_guide_drag`,
  the user can never drag a guide past the edge and the delete mechanic is dead.
- **grab_set / grab_release**: wrap both in `try/except tk.TclError` — Tk
  sometimes refuses grabs on off-screen or destroyed canvases.
- **Reset state on load**: `self._after_guides = []` and `self._guide_drag = None`
  must be in both `__init__` AND `load()` (the latter is called per-image).
- **Border-zone logic**: the `and not in_h_zone` guard prevents a click in the
  corner from adding two guides. Left/right borders → vertical; top/bottom → horizontal.

## What this is NOT

These guides are pure visual measurement aids. They do not:
- Feed back into the warp/crop computation
- Persist across images (reset on load)
- Interact with the before-pane or review state machine (`review.py`)

## Status in this project (2026-09-16, updated)

The guide system was **rebuilt twice** in `gui.py`. The first rebuild (panel-cross
interaction with `_cross_side` image-edge nearest-edge detection) was itself
replaced because the user demanded the implementation match the reference
(`Perspective-Correction - Kopie`). The current implementation follows the
reference pattern exactly:

### Cross interaction: preview-then-commit (NOT instant creation)

1. **Press** on the cross (`_on_cross_press`): sets `self._cross_pull = self._cross_orientation(event)` — a kind string `"h"` or `"v"`. No guide is created yet.
2. **Drag** (`_on_cross_pull`): calls `_cross_preview(event)` which draws a dashed grey line (dash pattern `(3,3)`, tag `"guide_preview"`) at the pointer's image-relative position. If the pointer is not over the image, nothing is drawn.
3. **Drop** (`_on_cross_drop`): calls `_after_pos(event, kind)`. If it returns a position (pointer is over the image), the guide is committed to `_after_guides`. If `None` (pointer still on the cross), the guide is **discarded**.

### Orientation: panel-edge based (`_cross_orientation`)

```python
def _cross_orientation(self, event):
    w, h = self.winfo_width(), self.winfo_height()
    dx = min(event.x, abs(event.x - w // 2), abs(w - event.x))
    dy = min(event.y, abs(event.y - h // 2), abs(h - event.y))
    return "h" if dy <= dx else "v"
```

This determines which *panel edge* (top/bottom/left/right) the pointer is nearest.
`dy <= dx` → horizontal guide (pointer near top or bottom border).
`dx < dy` → vertical guide (pointer near left or right gutter).

**Critical:** this is NOT the same as `_cross_side` (the old image-edge approach).
The panel-edge version is simpler and matches the user's mental model: "I pulled
from the left side, so I get a vertical guide."

### Position: root-coordinate conversion (`_after_pos`)

```python
def _after_pos(self, event, kind):
    cx = event.x_root - self.c_after.winfo_rootx()
    cy = event.y_root - self.c_after.winfo_rooty()
    aox, aoy = self._after_off
    iw, ih = self._ph_a.width(), self._ph_a.height()
    x, y = cx - aox, cy - aoy
    if not (0 <= x <= iw and 0 <= y <= ih):
        return None
    return float(y if kind == "h" else x)
```

Uses **root coordinates** because the event belongs to the panel but the answer
is wanted in canvas/image space. Returns `None` if the pointer is not over the
image (this is how the discard-on-cross-drop works).

### Canvas interaction: tuple-based drag state

- `_after_guide_at(x, y, iw, ih)`: 15 px grab distance, returns `(kind, index)` or `None`.
- `_guide_drag` stores a **tuple** `(kind, idx)` — not just the index. This is
  simpler than the old approach of re-reading kind from the list.
- No clamp during drag (overshoot enables delete-off-bounds).
- On release: clamp to `[0, dim]` or delete if >20 px past bounds. Calls `grab_release()`.

### Canvas press: crop handle dominance (all 8 handles)

In `_on_crop_press`, the check order is **critical**:

1. **`_grab_handle(x, y, iw, ih)`** — checked FIRST. All **8** handles (4 corners + 4 mid-edges, radius 14 px) take absolute priority over guides. If a handle is hit, set `_crop_drag_start` and return immediately.
2. **`_after_guide_at(x, y, iw, ih)`** — grab an existing guide (early return).
3. **Border-zone creation** — 15 px zones: v for left/right, h for top/bottom (early return).
4. **Fall through** to crop rectangle pan/corner logic.

**Why:** without step 1 first, a guide drawn near an edge can steal the press at
a crop handle, making the handle ungrabbable. The user was emphatic: "corners of
crop should always have dominance! all 8." Always verify the full count (4 corners
+ 4 mid-edges = 8) rather than assuming a subset.

### Canvas border-zone creation

In `_on_crop_press`, after crop-handle and guide-grab checks:
1. Check 15 px border zones of the image → create new guide (v for left/right, h for top/bottom). Early return.
2. Fall through to crop rectangle logic.

### Visual style

- Tag: `"after_guide"` (single tag for all guides, atomic delete+redraw).
- Color: `GUIDE_GREY = "#9aa0a8"` (module-level constant).
- Width: 1 px, no tick marks, no labels (simpler than the original skill's style).

### Cursor mapping (panel cross) — SWAPPED per user preference

- Vertical guide zone (left/right gutter) → `sb_h_double_arrow` (drag left/right).
- Horizontal guide zone (top/bottom border) → `sb_v_double_arrow` (drag up/down).

The cursor shows the **drag direction** of the guide line. Note: the initial
implementation had these inverted (v→`sb_v_double_arrow`, h→`sb_h_double_arrow`)
and the user explicitly requested the swap. The code is:
```python
cur = "sb_h_double_arrow" if kind == "v" else "sb_v_double_arrow"
```

### Test coordinate pitfall (root coords)

The test's `_E` class must support `x_root`/`y_root` parameters. For "drop over
image" cases, root coords are `rx0 + aox + offset_x, ry0 + aoy + offset_y`.
For "drop on cross → discard" cases, use root coords that map to the cross area
(e.g., `rx0 - 50, ry0 - 50`), NOT panel-relative coords — those would land inside
the image and commit the guide.

The core rules still hold: image-relative positions converted at draw time, single
tag for atomic redraw, state reset in both `__init__` and `load()`, drawn only on
`c_after` (never composited into the saved frame). The test is
`test_guides_are_pulled_off_the_black_cross` in `tests/test_gui.py`.

See `tkinter-interaction-zone-migration` for the older image-edge zone pattern
(now superseded by `_cross_orientation`). See `tkinter-subsystem-retirement` for
the cleanup procedure used to remove the ruler system.
