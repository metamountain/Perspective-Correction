---
name: tkinter-overlay-event-forwarding
description: How to make a floating Tkinter canvas (loupe, tooltip, magnifier) transparent to mouse events so clicks and drags pass through to the widget beneath it.
source: auto-skill
extracted_at: '2026-09-14T06:46:11.471Z'
---

# Transparent overlay canvas: forwarding button events to the widget beneath

## The problem

A `tk.Canvas` is placed **above** another canvas (via `w.tk.call("raise", w._w)` or
simple stacking order) to show a magnified view, tooltip, or other visual aid.
Because it sits on top, it **intercepts all mouse events** — clicks land on the
overlay and are swallowed; the widget beneath never sees them.

Two distinct symptoms appear:

1. **Clicks freeze**: `<Button-1>` on the overlay does nothing (or triggers the
   overlay's own handler). The user perceives "clicking doesn't work."
2. **Drag tracking stops**: even if you forward `<Button-1>`, the *drag* events
   (`<B1-Motion>`) fire on the overlay, not on the underlying widget's
   `<B1-Motion>` binding — so any logic that updates position/content during a
   drag (loupe following cursor, rubberband, handle movement) freezes.

## The fix: two-part pattern

### Part 1 — Forward button events from the overlay to the target widget

In the method that creates the overlay canvas, bind all three button event types
and relay them via `event_generate` at the **equivalent coordinate** on the
target widget:

```python
def _loupe_show(self):
    # ... create w = tk.Canvas(...) ...
    w.place(x=-2*size, y=-2*size)
    w.tk.call("raise", w._w)

    def _forward(event):
        # Convert root-window coords → target widget's local coords.
        lx = event.x_root - self.c_before.winfo_rootx()
        ly = event.y_root - self.c_before.winfo_rooty()
        # `state` MUST ride along too, or modifier keys (Alt/Shift/Ctrl) are
        # lost on the synthetic event — see "Why state" below.
        self.c_before.event_generate(
            event.type, x=int(lx), y=int(ly), buttons=event.buttons,
            state=getattr(event, "state", 0))

    w.bind("<Button-1>", _forward)
    w.bind("<ButtonRelease-1>", _forward)
    w.bind("<B1-Motion>", _forward)
```

**Why `event.x_root` / `winfo_rootx()` and not `event.x`:**
`event.x` is relative to the widget that received the event (the overlay).
The target widget (`c_before`) may be at a different position within the same
parent. Converting through root-window coordinates guarantees the forwarded
event lands at the correct pixel on `c_before`.

**Why all three bindings:**
- `<Button-1>`: initial press (starts a drag, selects an item).
- `<B1-Motion>`: every move while the button is held (drags a handle, paints).
- `<ButtonRelease-1>`: release (commits the drag, ends paint stroke).

Missing any one of them leaves a gap in the gesture.

**Why `buttons=event.buttons`:**
`event_generate` needs the button state to match; without it, the synthetic
`<B1-Motion>` arrives with `buttons=0` and handlers that check
`event.buttons & 1` will ignore it.

**Why `state=getattr(event, "state", 0)` (the one that bites):**
`event_generate` builds a *fresh* event. It does **not** copy the modifier-key
bits (`state`) from the event you are relaying unless you pass them. So a drag
that crosses the overlay arrives at the target with `state=0` — **Alt, Shift
and Ctrl are silently dropped** for exactly the portion of the gesture that
happens over the glass. The symptom is subtle and position-dependent: a
modifier-key feature (e.g. "hold Alt to damp the loupe") works when the cursor
is on the bare target widget but *stops working while the cursor is over the
overlay*, because only that part goes through `_forward`. This is easy to miss
in testing if you only try the modifier on the plain widget. Always forward
`state` in the same call as `buttons`.

### Part 2 — Call the tracking/update method in the drag handler too

Tkinter fires **`<Motion>` only when no button is held** and **`<B1-Motion>`
only while a button is held**. They are separate events with separate bindings.
If your "follow the cursor" logic (loupe repositioning, crosshair update,
magnified-crop refresh) lives in the `<Motion>` handler, it will **not run
during a drag** — the loupe freezes in place while the user drags a handle.

The fix: also call the tracking method at the top of the `<B1-Motion>` handler,
before any mode-specific branching:

```python
def _on_before_b1motion(self, event):
    # Track the loupe during drag too — <Motion> does NOT fire while a
    # button is held, so without this the loupe freezes mid-drag.
    if getattr(self, "_loupe", None) is not None:
        self._loupe_move(event)

    # ... existing ROI / mark / stroke / planar drag logic ...
```

This must come **before** the early `return` statements for each drag mode,
otherwise the loupe won't update when a specific drag mode is active.

## Generalisation

This pattern applies to any floating overlay canvas in Tkinter:

| Overlay type | Forwarded events | Tracking call |
|---|---|---|
| Loupe / magnifier | Button-1, B1-Motion, ButtonRelease-1 | `_loupe_move(event)` |
| Crosshair cursor | same three | reposition crosshair |
| Tooltip (if canvas-based) | usually none needed (passive) | — |
| Selection rectangle overlay | all button events | update rect coords |

## Pitfalls

- **`event_generate` is asynchronous.** The synthetic event enters the Tk
  event queue and is processed on the next `update()` cycle. For interactive
  use this is fine (the user won't notice one-frame latency), but do not rely
  on the forwarded handler having *already run* by the time `_forward` returns.
- **Do NOT bind `<Motion>` on the overlay for forwarding.** `<Motion>` fires
  constantly even when no button is held; forwarding it would inject phantom
  motion events into the target widget and trigger unwanted hover/tooltip
  logic. Only forward button-qualified events.
- **Coordinate conversion must use root-window coords.** Using `event.x` /
  `event.y` directly assumes the overlay and target share the same origin,
  which is only true if they are siblings at the same `place()` position.
  The `x_root` / `winfo_rootx()` path is always correct.
- **The tracking call must precede mode-specific early returns.** If
  `_on_before_b1motion` has `if self._roi_drag: ...; return` branches, putting
  `_loupe_move(event)` *after* them means the loupe only updates in the
  fall-through (planar) case and freezes during ROI or mark drags.
- **Guard with `getattr(self, "_loupe", None) is not None`.** The overlay may
  not exist yet (mode not active) or may have been destroyed by a `_build()`
  rebuild. A bare `self._loupe` would raise `AttributeError` during early
  construction.

## What this is NOT

- This does not make the overlay truly click-through at the X11/Win32 level.
  Tkinter has no "transparent to pointer" flag for canvas widgets. The
  forwarding approach simulates pass-through by re-dispatching events.
- This does not handle right-click (`<Button-3>`) or middle-click. Add those
  bindings if the target widget uses them (e.g. right-click erase in mask mode).
