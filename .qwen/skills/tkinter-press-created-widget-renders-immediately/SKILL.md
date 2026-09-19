---
name: tkinter-press-created-widget-renders-immediately
description: A visual aid created on mouse press (loupe, magnifier, preview) must render its content at creation time, not wait for the first motion event; plus the modifier-key damping pattern with visible feedback.
source: auto-skill
extracted_at: '2026-09-16T20:49:15.251Z'
---

# Render a press-created visual aid immediately, and give modifier features visible feedback

## The problem: "empty until I move"

A common Tk pattern creates a floating visual aid (loupe / magnifier / live
preview) **on button press** and updates its content in the **motion** handler.
The bug this invites: the widget is *created* on press but its *content* is only
drawn on the first `<B1-Motion>`. So the user presses to start a gesture, sees
an **empty** aid (blank canvas, no image), and it only fills in once they move
the mouse.

The symptom reads as "it's broken / laggy" even though it technically works.
Worse, it defeats the purpose: for a loupe the whole point is *seeing the exact
pixel before you commit* — an empty glass on press means you can't aim at all
until you start moving, which is backwards.

## The fix: render once at creation time

The creation method (`_loupe_show`) and the update method (`_loupe_move`) are
separate. After creating the widget in the **press** handler, call the update
method immediately with the press event so the first frame exists before any
motion:

```python
# In the press handler (e.g. _click_mark), at each site that raises the aid:
self._loupe_show()
if event is not None:
    self._loupe_move(event)   # render the crop NOW, not on first motion
```

Key details:

- **The press handler must have the event in scope.** If your press logic lives
  in a helper that only receives `(x, y)` image coords (not the Tk `event`),
  you cannot call the update method. Thread the `event` through:
  `_click_mark(self, x, y, event=None)` and pass it from the caller that has it
  (`_on_click_before`). Guard the call with `if event is not None` so direct
  callers / tests that pass only coords don't crash.
- **Do every creation site.** If the aid is raised from more than one branch
  (e.g. "start a new mark" and "nudge an existing endpoint"), add the render
  call to *each* — missing one leaves that gesture with the empty-until-motion
  bug.
- **The update method must be idempotent at creation.** It should read the
  widget's current size and session state, not assume a prior frame exists.

## The modifier-key damping pattern (and why it needs visible feedback)

A useful precision feature: hold a modifier (Alt/Shift/Ctrl) to make a tracking
aid move *slower* than the cursor, for fine aiming. The aid's tracked point
becomes an exponential blend of its previous position and the new cursor:

```python
alt = bool(getattr(event, "state", 0) & 0x0008)   # Alt bit; getattr for SimpleNamespace tests
if alt and self._loupe_center is not None:
    f = 1.0 / layout.LOUPE_MAG                    # damping factor (e.g. 0.5 at 2x)
    cx = self._loupe_center[0] + (px - self._loupe_center[0]) * f
    cy = self._loupe_center[1] + (py - self._loupe_center[1]) * f
else:
    cx, cy = px, py
self._loupe_center = (cx, cy)                     # persist for the next blend
```

Rules that made this work:

- **Track a persistent centre** (`self._loupe_center`), reset to `None` in the
  show/hide/rebuild paths. The first motion after creation has no previous
  centre, so it snaps 1:1 (correct — there's nothing to damp *from*); subsequent
  motions blend. Resetting on hide/rebuild prevents a stale centre from a prior
  gesture leaking into the next one.
- **Damp the *displayed* point, not the committed one.** The mark/endpoint that
  actually gets placed stays 1:1 with the cursor; only the glass lags. Damping
  the real position makes the rubberband and cursor disagree, which is worse
  than it helps.
- **Derive the factor from an existing constant** (`1 / LOUPE_MAG`) rather than
  hardcoding `0.5`, so it auto-scales if magnification changes.

### The part that's easy to get wrong: visible feedback

A half-speed aid is **easy to mistake for a full-speed one**, especially at 2x
where the difference is subtle. If the user holds the modifier and can't tell
whether it's doing anything, they conclude "it doesn't work" — which is exactly
what happened here until a visual cue was added. Make the active state *visible*
in the aid itself, not just in behaviour:

```python
cross = "#00e5ff" if alt else "#000000"   # crosshair turns cyan while Alt held
for x1, y1, x2, y2 in ((m - arm, m, m + arm, m), (m, m - arm, m, m + arm)):
    c.create_line(x1, y1, x2, y2, fill=cross, width=2)
```

A colour change on the crosshair/ring is unambiguous: cyan = damping active.
This turned a "does it even work?" feature into an observable one.

## Testing

Drive the press and motion handlers directly with synthetic events (see
`tkinter-synthetic-event-attributes` for the `getattr`/SimpleNamespace rule):

```python
def ev(dx, dy, state=0):
    return types.SimpleNamespace(x=dx + ox, y=dy + oy, state=state)

r._on_click_before(ev(100, 100))
assert r._loupe is not None
assert r._loupe_center is not None, "press must render immediately, not on first motion"

# without Alt: centre snaps to cursor (1:1)
r._loupe_move(ev(200, 150))
c0 = r._loupe_center
# with Alt: centre lags by the damping factor
r._loupe_move(ev(300, 250, state=0x0008))
c1 = r._loupe_center
f = 1.0 / layout.LOUPE_MAG
nx = int(round((ev(300, 250).x - ox) / r._before_scale))
assert abs(c1[0] - (c0[0] + (nx - c0[0]) * f)) <= 1
```

Assert the **gap** between damped centre and cursor (`abs(c1 - nx) > threshold`)
— that is the actual property, and it fails if damping silently stops applying.

## Pitfalls

- **Forwarding must carry `state`** or the modifier is lost when the pointer is
  over the aid (see `tkinter-overlay-event-forwarding`). Without it, damping only
  works on the bare target widget and dies over the glass — a position-dependent
  "it doesn't work" that's easy to miss.
- **The press handler may live in a coords-only helper.** If `_click_mark(x, y)`
  has no `event`, you cannot render at creation. Thread the event through rather
  than reconstructing a fake one.
- **Don't damp before there's a previous centre.** Guard with
  `self._loupe_center is not None`; the first frame snaps, later frames blend.
- **Reset the persistent centre on every hide/rebuild**, or a stale value from a
  finished gesture biases the next one.
