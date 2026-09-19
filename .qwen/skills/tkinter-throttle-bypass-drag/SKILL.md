---
name: tkinter-throttle-bypass-drag
description: When a throttled full-panel redraw (e.g. 60ms after() with _busy flag) makes continuous drag interactions appear frozen, bypass the throttle by directly updating tagged canvas items per event.
source: auto-skill
extracted_at: '2026-09-16T10:17:49.894Z'
---

# Bypass throttled redraw for continuous drag feedback

## The problem

A Tkinter app uses a throttled full-panel redraw to avoid excessive repaints:

```python
def _schedule_redraw(self):
    if self._busy:
        return          # ← drops the call entirely
    self._busy = True
    self.after(60, self._redraw)
```

During a continuous drag (B1-Motion fires at 60–250 Hz depending on OS), most
events hit `if self._busy: return` and are silently dropped. The user sees the
element jump or appear frozen. This is especially bad in small interaction zones
(20px borders) where the total drag range is short and only a few events fire.

## Why it's hard to diagnose

The code *looks* correct: bindings are wired, delta math is right, state
variables update. The bug is invisible because `_ruler_y` (or whatever state)
**does** change on every event — it's just that the visual feedback (the canvas
redraw) never happens for most of those events. Adding a `print(self._ruler_y)`
in the handler will show values changing; the canvas simply isn't repainting.

## The fix: direct tagged-item update during drag

In the drag handler, skip `_schedule_redraw()` and instead delete + recreate
only the affected canvas item(s) using their tag:

```python
def _on_drag(self, event):
    if not self._dragging:
        return
    delta = event.y - self._drag_start_y
    self._value = max(2, min(50, self._drag_start_val + delta))
    # Direct update — no throttle:
    aox, aoy = self._offset
    iw = self._photo.width()
    off = self._value
    self.canvas.delete("ruler")          # ← tag-based delete is O(n) for that tag only
    self.canvas.create_line(aox + off, aoy + off, aox + iw - off, aoy + off,
                            fill="#ffffff", width=1, dash=(4, 2), tags="ruler")
```

On release, call `_schedule_redraw()` once to let the full redraw path
re-establish all items in their normal order (z-order, ground paint, etc.):

```python
def _on_release(self, event):
    self._dragging = False
    self._schedule_redraw()   # ← one final full redraw for consistency
```

## When to use this pattern

- Any continuous interaction where per-event visual feedback matters (drags,
  sliders, live previews).
- The throttled redraw does a **full panel** repaint (delete("all") + rebuild)
  rather than an incremental update.
- The affected element is a small tagged subset of the canvas (a line, a few
  rectangles) that can be deleted and recreated cheaply.

## When NOT to use it

- If the drag changes layout (widget geometry, grid weights), you must go
  through the full redraw because child positions depend on the container.
- If the element is part of a complex compositing pipeline (e.g. baked into an
  image buffer), direct canvas updates won't work — use a separate overlay
  canvas layered on top instead.

## Checklist

1. **Tag your items.** Every canvas item that participates in a drag must have
   a unique tag so `delete(tag)` is surgical.
2. **Store the offset.** Canvas coordinates = image-relative + offset
   (`self._after_off`). The direct-draw code must apply the same offset as the
   full-redraw path, or the item will be in the wrong place after a resize.
3. **Clamp state, not pixels.** Clamp the logical value (e.g. `_ruler_y` to
   2–50) before computing canvas coordinates. Don't clamp `event.y` directly —
   it's in widget space and may be negative or past the edge.
4. **Delta-based, not absolute.** For drags in small zones (< 30px), use
   `delta = event.y - start_y` mapped to a value range. Absolute `event.y`
   gives you only 0–20 px of range in a 20px border — barely visible movement.
5. **Release triggers full redraw.** The direct update is a shortcut for the
   drag; on release, let the normal path rebuild everything so z-order and
   ground paint are consistent.

## Sensitivity multiplier for small interaction zones

When the drag zone is physically small (e.g. a 20px border strip), even with
the throttle bypassed the total mouse travel is limited. A user pressing at
y≈10 in a 0–20px zone can only produce ±10px of delta before leaving the zone
(or releasing). Mapped 1:1, that's barely visible on a large image.

Fix: multiply the delta by a sensitivity factor (3× worked well for a 20px
zone controlling a 2–50px value range):

```python
delta = (event.y - self._drag_start_y) * 3   # ← 3x sensitivity
self._value = max(2, min(50, self._drag_start_val + delta))
```

This makes a 10px mouse drag produce 30px of visual movement. The clamp still
protects against overshoot if the user drags far outside the zone (B1-Motion
continues past the zone boundary until release).

**Rule of thumb:** sensitivity ≈ (value_range / zone_size) × 0.5 to keep the
full value range reachable without extreme mouse travel. For a 48px value range
in a 20px zone: 48/20 × 0.5 ≈ 1.2… but in practice 3× felt right because users
don't use the full zone width and prefer "responsive" over "precise."

## Placed child widgets can block interaction zones

A `tk.Frame` or other widget placed on a canvas (`place(relx=1.0, y=8,
anchor="ne")`) sits **above** the canvas in the stacking order and intercepts
all mouse events in its bounding box. If that widget overlaps an area where the
parent panel expects to receive `<Button-1>` / `<B1-Motion>`, the press never
reaches the panel handler.

Diagnosis: if a drag works from most of a zone but fails in one corner, check
for placed children (toolbars, labels, checkboxes) whose bounding box covers
that corner. The fix is either:
- Remove or reposition the widget so it doesn't overlap the interaction zone.
- Forward events from the widget to the panel (see `tkinter-overlay-event-forwarding` skill).

In practice, removing an unused widget (e.g. a grid toolbar that was no longer
needed) solved both the visual clutter and the event interception.

## Symptom → diagnosis shortcut

If a drag "works" (state changes) but the visual is frozen or jumps:
1. Check if the redraw path has a throttle (`_busy` flag, `after()` delay).
2. Temporarily replace `_schedule_redraw()` with `self._redraw()` (synchronous)
   in the drag handler. If it now works smoothly, the throttle was the cause.
3. Replace with the direct tagged-item update as the permanent fix.
4. If movement is still too subtle, add a sensitivity multiplier to the delta.
5. If the drag doesn't start at all in part of the zone, check for placed child
   widgets intercepting the press.
