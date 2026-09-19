---
name: separate-decisions-not-settings
description: When a "simplify to one setting" refactor breaks tests, the real issue is two different decisions sharing one field — split by decision context, not by config surface.
source: auto-skill
extracted_at: '2026-09-16T08:43:20.540Z'
---

# Separate decisions, not settings

## The trap

A codebase has two (or more) thresholds that *look* like the same concept
("how much frame may a crop cost") but govern **different decisions made by
different actors in different contexts**:

| Decision | Actor | Context | Consequence of getting it wrong |
|---|---|---|---|
| "May I crop or must I pad?" | `warp.plan()` | batch, unattended | silently throws away a third of every picture |
| "May I trim the padded band?" | auto-crop button | review window, user watching | user presses Reset and nothing is lost |

A "clean" refactor collapses them into one settings field because they share a
name. The moment you raise that field to satisfy the second decision, the first
decision's behaviour changes — and tests that exercise the first decision break.

## What went wrong (concrete example)

`max_crop_loss` (5%) and `auto_crop_max_loss` (12%) were two settings fields.
The refactor proposal said "one gate, one number." Setting it to 30% made
`warp.plan()` crop instead of pad for moderate corrections, breaking tests that
asserted a padded band exists.

## The fix pattern

**Keep the shared concept in settings at its strictest (batch) value. Give the
interactive context its own instance-level field.**

```python
# config.py — the batch gate stays tight
crop_max_loss: float = 0.05

# review.py — the interactive threshold is a session concern, not a global setting
class ReviewSession:
    def __init__(self, path, settings, image=None):
        self.settings = settings          # unchanged; warp.plan() sees 5%
        # Interactive auto-crop: user sees result on screen, Reset undoes it.
        # NOT settings.crop_max_loss — that governs the warp plan's pad-vs-crop.
        self.auto_crop_threshold = max(settings.crop_max_loss, 0.30)
```

Why an instance field and not a second settings field:
- It is **derived from** the settings value (floor of `crop_max_loss`), so it
  can never go below what the batch allows.
- It is **not user-configurable via CLI** — there is no `--auto-crop-threshold`
  flag because the interactive context is not a batch parameter.
- It lives where the decision is made, not in a global config blob that every
  consumer imports.

## Diagnostic: how to recognise this pattern early

When a "simplify to one setting" change breaks tests, ask:

1. **Who makes each decision?** If two different code paths (batch pipeline vs.
   interactive UI) read the same field for different purposes, they need
   separate values.
2. **What is the blast radius of raising it?** If raising the value changes
   behaviour in a context where nobody is watching (batch), that context needs
   its own lower gate.
3. **Is the interactive value derived from or independent of the batch value?**
   If it's `max(batch_value, some_floor)`, an instance field with that formula
   is the right shape. If truly independent, two settings fields were correct
   all along — just rename them to reflect the decision, not the unit.

## Rule of thumb

> A setting is a **policy** (what the operator chose). An instance field is a
> **contextual adaptation** (what this particular UI session needs given that
> policy). Don't promote a contextual adaptation to a policy just because it
> has a number in it.

## Checklist before merging a "single setting" refactor

- [ ] Every consumer of the old fields still gets the value appropriate to its
      decision context (run the full test suite, not just the module you touched)
- [ ] The batch path's behaviour is **unchanged** at its default value
- [ ] The interactive path's threshold is **derived from** (not copied from) the
      settings value, so future changes to the batch default propagate correctly
- [ ] No CLI flag exposes the interactive-only threshold
