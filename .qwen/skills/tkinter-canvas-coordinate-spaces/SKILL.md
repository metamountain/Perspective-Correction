---
name: tkinter-canvas-coordinate-spaces
description: The two coordinate spaces (image-relative vs canvas) on an inset-image Tkinter canvas, and the conversion rules that prevent offset bugs in drag previews and overlays.
source: auto-skill
extracted_at: '2026-09-14T15:16:10.374Z'
---

# Canvas coordinate spaces: image-relative vs canvas

## The problem

A Tkinter canvas displays an image inset by an offset `(ox, oy)` (e.g., a margin
for rulers). Event coordinates (`event.x`, `event.y`) are in **canvas space**.
Image content is positioned at `(ox, oy)`. Two coordinate spaces coexist:

- **Canvas coords**: where you draw. Origin at canvas top-left.
- **Image-relative coords**: where you store logical positions. Origin at image top-left. Range `[0, iw] × [0, ih]`.

Mixing them up causes elements to appear shifted by exactly `(ox, oy)` — the
offset. This bug is silent (no crash) and only visible when the offset is
non-zero (i.e., the image is not flush with the canvas corner).

## The conversion rules

```python
# Canvas → image-relative (on event receipt):
x_img = event.x - ox
y_img = event.y - oy

# Image-relative → canvas (on draw):
x_canvas = ox + x_img
y_canvas = oy + y_img
```

**Rule: store in image-relative, convert to canvas only at the `create_*` call.**

## The bug pattern (SAM box drag preview)

A box-drag interaction stores the start point and draws a live preview rectangle:

```python
# WRONG — start stored as image-relative, drawn without offset:
def on_click(self, x_img, y_img):  # already converted from event
    self._drag_start = (x_img, y_img)

def on_drag(self, event):
    x1 = event.x - ox  # image-relative
    y1 = event.y - oy
    cx0, cy0 = min(self._drag_start[0], x1), min(self._drag_start[1], y1)
    cx1, cy1 = max(self._drag_start[0], x1), max(self._drag_start[1], y1)
    canvas.create_rectangle(cx0, cy0, cx1, cy1, ...)  # BUG: no +ox, +oy

# CORRECT — convert to canvas coords before drawing:
def on_drag(self, event):
    x1 = event.x - ox
    y1 = event.y - oy
    cx0, cy0 = min(self._drag_start[0], x1), min(self._drag_start[1], y1)
    cx1, cy1 = max(self._drag_start[0], x1), max(self._drag_start[1], y1)
    canvas.create_rectangle(ox + cx0, oy + cy0, ox + cx1, oy + cy1, ...)
```

The bug is invisible when `ox == 0 and oy == 0` (image flush with canvas corner),
which is why it survives code review but appears the moment a margin is added.

## Checklist for any new interactive overlay

When adding a new drag/preview element to an inset-image canvas:

1. **Event handler**: convert `event.x/y` → image-relative immediately after
   computing the offset subtraction. Store only image-relative values in state.
2. **Draw call**: add `ox`/`oy` back before every `create_*` that uses stored
   positions. Never pass an image-relative value directly to `create_rectangle`,
   `create_line`, etc.
3. **Hit-test**: if you hit-test against stored positions, convert the event
   coord to image-relative first (or convert stored positions to canvas — pick
   one direction and be consistent).
4. **Redraw on resize/reload**: the offset changes when the window resizes or a
   new image loads. All draw functions must read `self._offset` at call time,
   not cache it.

## Testing the conversion

The simplest test: set a non-zero offset (simulate a margin), perform the drag,
and assert the drawn rectangle's canvas coords match `event.x/y` exactly:

```python
# After a drag from (100, 200) to (300, 400) in canvas coords:
items = canvas.find_withtag("preview")
x1, y1, x2, y2 = canvas.coords(items[0])
assert (x1, y1, x2, y2) == (100, 200, 300, 400)
```

If the offset is `(50, 30)`, the image-relative start would be `(50, 170)` and
the drawn rectangle must still land at canvas `(100, 200, 300, 400)`.

## Related: ruler ticks in the margin zone

Ruler ticks live entirely in the margin (between canvas edge and image edge).
They use **canvas coordinates directly** (spawn from `y=0` or `x=0`) and must
stop short of the image boundary:

```python
# Top ruler tick at image-x position p:
# Spawns from y=0, extends downward to (m - gap) where m = RULER_MARGIN, gap >= 8
canvas.create_line(ox + p, 0, ox + p, m - gap, ...)
```

The invariant: `tick_end < image_edge` for all four sides. A tick that reaches
the image edge collides with crop handles drawn at the boundary.

**The margin zone belongs to the rulers.** Do not add decorative frames, borders,
or other visual elements in that zone — they compete with the ticks and make the
area look cluttered. The user's directive: "create frames outside q2 in black!
border!" was actually a complaint that the zone looked wrong; the fix was simple
solid tick lines (no stipple, no text labels) in a muted color, not an added
rectangle. Keep the margin clean: ticks only.

## Pitfalls

- **The offset is not always symmetric.** `_before_off` and `_after_off` are
  computed independently per pane and can differ (different image sizes, different
  margins). Never hardcode one pane's offset for the other.
- **`winfo_width()` includes padding.** If the canvas has `highlightthickness=1`,
  the drawable area is 2 px smaller than `winfo_width()`. In practice this is
  negligible for a 20 px margin, but it means "stop at the canvas edge" is
  approximate.
- **Debounced redraws hide the bug.** If `_redraw()` is scheduled via
  `after()`, the preview may not appear until the next event loop tick. In tests,
  call the draw function explicitly rather than relying on the debounce timer.
