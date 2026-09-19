---
name: tkinter-gesture-completion-triggers-action
description: Fire the action on gesture completion (button release) rather than requiring a second confirmation keypress; show hourglass cursor during async work.
source: auto-skill
extracted_at: '2026-09-14T15:30:18.791Z'
---

# Gesture completion triggers the action

## The problem

A drag-to-select interaction (box drag for SAM2) was implemented with a two-step
flow: drag the box → release → user must press Enter or click "Apply" to run.
The user's reaction: "sam not enter to run. select directly! nothing happening!"

For a single-gesture interaction where the gesture *is* the complete input
(drag a box = specify the region), requiring a second confirmation step adds
friction with no information gain. The user already committed by releasing the
mouse — that IS the confirmation.

## The rule

**If a gesture fully specifies the action's parameters, fire the action on
gesture completion (ButtonRelease), not on a separate keypress or button click.**

Two-step is only justified when:
- The gesture is ambiguous (e.g., a single click could mean "select" or "start drag")
- The action is destructive and benefits from an explicit confirm
- There are multiple possible actions for the same gesture (mode-dependent)

## Implementation pattern

```python
def _on_release(self, event):
    """Gesture complete: validate, store state, fire the action."""
    if self._drag_start is None:
        return
    # ... compute final params from drag_start + release position ...
    if invalid(params):
        self._drag_start = None
        return
    self.session.set_params(params)
    self._redraw()
    # Fire immediately — no second step:
    self._run_action()

def _run_action(self):
    """Async work with hourglass feedback."""
    if not self._has_params():
        return
    self._set_status("running…")
    self.canvas.config(cursor="watch")  # hourglass
    self.update_idletasks()             # force cursor update before blocking

    def _worker():
        try:
            result = expensive_computation(self.params)
            self.after(0, lambda: self._on_done(result))
        except Exception as exc:
            self.after(0, lambda e=exc: self._on_fail(e))

    threading.Thread(target=_worker, daemon=True).start()

def _on_done(self, result):
    self.canvas.config(cursor="")  # restore cursor
    self.apply(result)
    self._redraw()

def _on_fail(self, exc):
    self.canvas.config(cursor="")  # restore on failure too
    self._set_status(f"failed: {exc}")
```

## Key details

1. **`update_idletasks()` before the thread starts.** Without it, Tk may not
   process the cursor change until the next event loop iteration, and if the
   subprocess returns fast the user never sees the hourglass.

2. **Restore cursor in BOTH success and failure paths.** A stuck hourglass is
   worse than no hourglass — the user thinks the app froze.

3. **Keep the Enter-key binding as a fallback** for re-running with point
   prompts (Shift/Alt+click) without redrawing the box. The primary path is
   release; Enter is the secondary "re-run with modified points" path.

4. **The status bar message must say what's happening and how long.**
   "SAM2: running (subprocess, ~5-15 s)…" — not just "running…". The user
   needs to know this is expected, not a hang.

## Anti-pattern: the two-step trap

```python
# WRONG for single-gesture interactions:
def _on_release(self, event):
    self.store_params(...)
    self._set_status("press Enter to run")  # forces a second step

# The user reads "nothing happening" because they expected the action on release.
```

The mental model mismatch: the developer thinks "store then confirm" is safe;
the user thinks "I just drew the box, it should work now." For non-destructive
actions (compute a mask, show a preview), the cost of a false trigger is
re-drawing — trivial. The cost of friction is user frustration.
