---
name: tkinter-cursor-availability-windows
description: Which Tk cursor names are valid on Windows, how to verify before use, and the geometric pattern for drawing in a margin zone without colliding with content at the image boundary.
source: auto-skill
extracted_at: '2026-09-14T14:02:13.117Z'
---

# Tk cursor availability on Windows + margin-zone geometry

## The problem

You want a distinctive cursor for a specific UI zone (e.g., a ruler border, a
resize handle, a measurement strip). You pick a name that *semantically* fits
("ruler", "hand", "move") and set it — then `tk.TclError: bad cursor spec`
crashes at runtime on Windows.

## Valid built-in cursor names (verified on Windows 10/11, Tk 8.6)

Tested empirically with `canvas.config(cursor=name)`:

| Name | Valid | Notes |
|---|---|---|
| `arrow` | ✓ | default pointer |
| `crosshair` | ✓ | closest to a "measurement" symbol |
| `sizing` | ✓ | four-way resize arrows |
| `fleur` | ✓ | move/pan hand |
| `pirate` | ✓ | cross with dots (X11 name, works on Win) |
| `ibeam` | ✓ | text caret |
| `dotbox` | ✓ | small dot in a box |
| `plus` | ✓ | simple + |
| `watch` | ✓ | hourglass / busy |
| `sb_h_arrow` | ✓ | Verified working on this machine (system python 3.12, Win 11). Used in gui.py `_on_before_motion` for ruler drag-line hover. Earlier ✗ was a test artifact. |
| `sb_v_arrow` | ✗ | X11-only, not on Windows |
| `ruler` | ✗ | does not exist in any Tk build |
| `hand2` / `hand1` | ✗ | X11-only |

**Rule: only the 9 names marked ✓ are safe on Windows.** If you need a
distinctive symbol, `crosshair` is the best "measurement zone" indicator.

## How to verify before shipping

```python
import tkinter as tk
r = tk.Tk()
c = tk.Canvas(r)
try:
    c.config(cursor="your_name")
    print("OK:", c.cget("cursor"))
except tk.TclError:
    print("INVALID — pick another name")
r.destroy()
```

Run this in the **target interpreter** (system python.org 3.12 on this box),
not in a CI container that might have X11 cursors available.

## Custom bitmap cursors: do not bother on Windows Tk

Two routes were tried and both fail:

1. `tk.BitmapCursor(...)` — **does not exist** as a tkinter class
   (`AttributeError`). It's an X11/Tk 8.6 extension that CPython's tkinter
   module does not expose.
2. `root.tk.call('image', 'create', 'bitmap', ...)` — raises
   `TclError: format error in bitmap data` on Windows for the standard
   XBM-style data strings. The Windows Tk build uses a different internal
   representation (`.cur` files via `CreateCursor`) that the Tcl bitmap
   image command does not bridge to a cursor spec.

**Conclusion:** stick to the 9 built-in names. If you truly need a custom
shape, load a `.cur` file: `canvas.config(cursor="@" + path_to_cur)` — but
that ships a binary asset and is rarely worth it for a zone indicator.

## Margin-zone geometry: draw from the canvas edge, stop short of content

When a canvas has an image inset by a margin (e.g., `RULER_MARGIN=20` px),
decorative elements in the margin must **start at the canvas boundary and end
before the image boundary** to avoid colliding with interactive content
(crop handles, guides) that sits on the image edge.

```python
# Image is drawn at (ox, oy) with size (iw, ih). Canvas is (cw, ch).
# Margin = RULER_MARGIN (20 px). Ticks must NOT reach ox/oy/cw-ox-iw/ch-oy-ih.

# Top: from y=0 downward, stop 4 px before the image top edge
canvas.create_line(ox + p, 0, ox + p, oy - 4)

# Left: from x=0 rightward, stop 4 px before the image left edge
canvas.create_line(0, oy + p, ox - 4, oy + p)

# Right: from x=cw leftward, stop 4 px before the image right edge
canvas.create_line(cw, oy + p, cw - (cw - (ox + iw)) + 4, oy + p)
# i.e. inner end = ox + iw + 4

# Bottom: from y=ch upward, stop 4 px before the image bottom edge
canvas.create_line(ox + p, ch, ox + p, oy + ih + 4)
```

The **4 px gap** is the collision guard. Crop handles are drawn at the image
boundary with a ±5 px grab radius; a tick that reaches the boundary overlaps
the handle's visual and its hit-test zone.

### Testing the geometry

Assert on canvas coordinates of every tagged item:

```python
for item in canvas.find_withtag("ruler"):
    if canvas.type(item) != "line":
        continue
    x1, y1, x2, y2 = canvas.coords(item)
    # Top ruler: vertical line from y=0, inner end must be < oy
    if abs(y1) < 1 and abs(x1 - x2) < 1:
        assert y2 < oy, "tick reaches image edge"
    # ... symmetric for left/right/bottom
```

This is a **geometric invariant test** — it catches the collision regardless of
image size or aspect ratio, without needing to know the exact tick positions.

## Busy indicator ("watch") that survives pointer motion

`cursor="watch"` is the built-in hourglass and works on Windows (see table).
The trap is not availability — it's **clobbering**. A panel with `<Motion>`
handlers that reset the cursor to `""` (the common pattern: "clear any stale
cursor when the pointer moves") will silently kill the hourglass the instant
the user moves the mouse during a long inference, so the indicator looks dead.

Pattern that works (as used in gui.py for SAM2):

1. **Set it on every canvas the pointer can be over**, not just one. The
   canvases are what the pointer sits on; a watch set only on `c_before`
   vanishes when the mouse is over `c_after`. A small helper keeps this in
   one place:
   ```python
   def _set_busy(self, busy):
       for c in (getattr(self, "c_before", None), getattr(self, "c_after", None)):
           if c is not None:
               c.config(cursor="watch" if busy else "")
   ```
2. **Guard the motion handlers with a flag.** Set `self._busy_sam = True`
   before spawning the worker thread; early-return in `_on_before_motion`
   (and any other handler that touches `config(cursor=...)`) while it is set:
   ```python
   def _on_before_motion(self, event):
       if getattr(self, "_busy_sam", False):
           return  # keep the hourglass; motion must not reset it mid-inference
       ...
   ```
3. **Clear both flag and cursor on every exit path** — success *and* failure.
   The worker marshals back via `self.after(0, ...)`, so clear in both the
   `_on_*_done` and `_on_*_fail` callbacks, or a failed inference leaves the
   hourglass stuck forever.

## Pitfalls

- **Motion handlers clobber the watch cursor.** Any `<Motion>`/hover handler
  that does `config(cursor="")` will reset the busy indicator mid-inference.
  Gate them behind a busy flag (see section above). This is the #1 reason a
  "working" hourglass appears not to work.
- **Don't assume X11 cursor names work on Windows.** The Tk docs list many
  names; only ~9 are in the Windows build. Always verify in the target
  interpreter.
- **`cget("cursor")` returns the name you set, not a resolved icon.** You can't
  introspect "is this valid?" after the fact — the `config()` call either
  raises or succeeds.
- **The margin must be ≥ tick length + gap.** If `RULER_MARGIN=20` and major
  ticks are 16 px long (m−4), the gap is only 4 px. A larger tick or a smaller
  margin will cause overlap. Keep the invariant: `tick_length + gap ≤ margin`.
- **Cursor resets on `<Leave>`.** The existing pattern binds
  `c_after.bind("<Leave>", lambda e: c_after.config(cursor=""))` — any new
  cursor logic must coexist with this (it already does, since `_on_crop_motion`
  sets the cursor and `<Leave>` clears it).
