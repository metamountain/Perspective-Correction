---
name: tkinter-synthetic-event-attributes
description: When adding Tk event attributes (state, keysym, num) to handlers tested with SimpleNamespace fixtures, use getattr(event, attr, default) to avoid AttributeError in existing tests.
source: auto-skill
extracted_at: '2026-09-16T19:39:59.174Z'
---

# Defensive access to Tk event attributes in testable handlers

## The problem

Tkinter event objects (from real `bind` callbacks) always carry the full set of
attributes: `.x`, `.y`, `.state`, `.keysym`, `.num`, `.type`, `.widget`, etc.

But this project's GUI tests (`tests/test_gui.py`) construct **synthetic events**
using `types.SimpleNamespace`:

```python
def ev(dx, dy):
    return types.SimpleNamespace(x=dx + ox, y=dy + oy)
```

These fixtures only carry the attributes the test author thought to include.
When you add a new attribute access to a handler (e.g., `event.state` for Alt-key
damping), every existing test that calls that handler with a bare
`SimpleNamespace(x=..., y=...)` raises:

```
AttributeError: 'types.SimpleNamespace' object has no attribute 'state'
```

This is not a one-time cost — it breaks **all** tests that exercise the handler,
not just the new one. In this project that was 1 of 32 tests failing after a
6-line feature addition.

## The rule

**In any handler that is called directly from tests with synthetic events, access
optional Tk attributes via `getattr` with a sensible default:**

```python
# Safe — works with both real Tk events and SimpleNamespace fixtures:
if (getattr(event, "state", 0) & 0x0008):   # Alt held?
    ...

# UNSAFE — crashes on SimpleNamespace without .state:
if (event.state & 0x0008):
    ...
```

Defaults that match "nothing pressed / neutral":
- `state` → `0` (no modifier keys)
- `keysym` → `""` (no key)
- `num` → `0` (no button)
- `delta` → `0` (no scroll)

## When to apply

- Any handler in `gui.py` that reads an event attribute beyond `.x` and `.y`.
- Specifically: `.state` (modifier keys), `.keysym`, `.num`, `.delta`.
- The pattern is especially important for **motion handlers** (`_on_*_b1motion`,
  `_loupe_move`, etc.) because many tests drive them directly.

## When it's NOT needed

- If you control the test fixture and can add the attribute there, plain
  `event.state` is fine. But in this codebase the fixtures are shared across
  dozens of tests; modifying all of them for one new attribute is more churn
  than a single `getattr`.
- In event *forwarding* code (`event_generate`) where you construct the event
  yourself, you know exactly what's there.

## Checklist

1. Added `event.state` (or similar) to a handler? → Use `getattr(event, "state", 0)`.
2. New test for the feature? → Include the attribute in the `SimpleNamespace`:
   ```python
   def ev(dx, dy, state=0):
       return types.SimpleNamespace(x=dx + ox, y=dy + oy, state=state)
   ```
3. Run the **full** test file, not just the new test — the regression is in
   existing tests that call the same handler without the attribute.

## Why not just update all the fixtures?

The `ev()` helper is defined locally inside each test function (not shared), so
there are ~15+ copies across `test_gui.py`. Updating all of them for one new
attribute is a large diff with no behavioural change, and any future attribute
addition would require the same sweep. The `getattr` in production code is one
line that makes the handler robust to **any** fixture shape.
