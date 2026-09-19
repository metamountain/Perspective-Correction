---
name: tkinter-segmentation-selection-overlay
description: A segmentation result (SAM2, BiRefNet) is a SELECTION shown as a highlight overlay, not an ignore-mask that dims the image. The user must see what was picked before deciding to apply it.
source: auto-skill
extracted_at: '2026-09-14T16:32:56.718Z'
---

# Segmentation result = selection overlay, not a mask

## The user's rule

"selection is not a mask!" — the SAM2/BiRefNet result must be shown as a visible
**selection highlight** (the user can see what was picked), not silently applied
as an ignore/dimming mask that darkens everything outside the subject.

Applying it as a mask means:
- The image changes appearance immediately with no way to tell *what* was selected
- The user cannot judge whether the segmentation is correct
- "Nothing happening" from the user's perspective — they drew a box, the image
  got darker, but they can't see the boundary of what was picked

## Implementation pattern

```python
def _on_segmentation_done(self, ignore_mask):
    """Store the result as a selection (bool array, True = selected)."""
    self.canvas.config(cursor="")
    self._selection = ~ignore_mask  # invert: ignore=True → selected=False
    self._redraw()
    frac = self._selection.mean()
    self._set_status(f"selected {frac:.0%} of frame -- rework or clear")

def _draw_selection_overlay(self):
    """Draw the selection as a visible highlight on the canvas.

    Final implementation (user-confirmed 2026-09-14): a 3px solid green border
    rectangle around the canvas perimeter in the off-border (RULER_MARGIN) area.
    This is a SELECTION INDICATOR, not a pixel-accurate mask overlay — it tells
    the user "something was selected" and where roughly, without obscuring the
    image. The user explicitly rejected: stipple bounding boxes, dimming the
    image, applying the mask automatically.
    """
    if getattr(self, "_selection", None) is None:
        return
    cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
    # 3px solid green rectangle inset 2px from canvas edge (in the margin zone)
    self.canvas.create_rectangle(
        2, 2, cw - 2, ch - 2,
        outline="#5ac37f", width=3, fill="",
        tags="selection")
```

**Why a perimeter rectangle and not a bounding box of the selection?** The user
tested both. A bounding box of the segmented region looks like "a box on my
photo" and competes with crop handles and line marks. A perimeter border reads
as a window-level state indicator ("SAM ran, here's the result") without
interfering with any other canvas element. It lives in the RULER_MARGIN zone
which is reserved for chrome, not content.

## Key principles

1. **The selection indicator is chrome, not content.** It lives in the margin
   zone (RULER_MARGIN) as a perimeter border. It does NOT draw on top of the
   photograph. This keeps it from competing with crop handles, line marks,
   ruler ticks, or any other canvas element.

2. **Do not modify the image array.** The base photo stays unchanged. The
   selection is an overlay drawn on top, tagged so it can be cleared independently.

3. **Clearing is explicit.** Right-click or toggle-off removes the overlay.
   The selection does not persist across images (cleared on reload).

4. **Applying is a separate step.** If/when the user wants to use the selection
   as an ignore mask for correction, that's a deliberate "apply" action — not
   automatic on segmentation completion.

5. **Status bar says what was selected.** "selected 34% of frame" gives the
   user an immediate sanity check (a building should be 20-60%, not 95%).

6. **Hourglass cursor during prediction.** The SAM2 subprocess takes seconds;
   the canvas shows `cursor="watch"` (hourglass) from press to completion so
   the user knows it's working, not frozen.

## Anti-pattern: auto-applying as mask

```python
# WRONG — dims the image, user can't see what was selected:
def _on_done(self, ignore_mask):
    self.session.apply_sam_mask(ignore_mask)  # bakes into the render pipeline
    self._redraw()  # image is now darker outside the subject
```

The user sees a dimmed image and thinks "nothing happened" or "it broke." They
cannot evaluate the segmentation quality because the boundary is invisible.

## When a mask IS appropriate

- Batch mode (no human reviewing) — apply automatically, no UI feedback needed
- After the user explicitly clicks "Apply mask" — they've seen the selection,
  judged it good, and now want it baked in
- The brush/paint tool — the user is painting *with* intent, so immediate
  visual feedback (dimming) is correct there
