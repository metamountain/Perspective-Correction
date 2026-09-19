---
name: tkinter-subsystem-retirement
description: How to fully retire one Tkinter UI subsystem (state, handlers, draw calls, methods, constants, tests) when a new system absorbs its role — the mechanical cleanup checklist.
source: auto-skill
extracted_at: '2026-09-16T13:35:32.634Z'
---

# Fully retiring a Tkinter UI subsystem in favor of a replacement

## When to use this

A new interaction system (e.g., per-side rulers) has absorbed the role of an old one
(e.g., shared-list guides). The user wants **one mechanism, no doublettes**. You must
remove every trace of the old system so it cannot leak state, draw ghost items, or
confuse future readers.

## Why this is non-trivial

1. **State is initialized in multiple places.** `__init__`, `load()`, and sometimes a
   `_build()`/rebuild method each set `self._old_state = ...`. Missing one leaves an
   attribute that exists but is never reset, causing stale data on the next image load.

2. **Event handler branches are scattered.** The old system's press/drag/release logic
   is inserted as early-return branches at the top of shared handlers (`_on_crop_drag`,
   `_on_crop_release`). Removing the methods but forgetting these branches means the
   handler references a deleted method → `AttributeError` at runtime.

3. **Draw calls are interleaved.** The old system's draw call sits between other draw
   calls in a render sequence. Removing it is easy; the risk is removing the wrong
   line or leaving a dangling tag that `canvas.delete(tag)` still targets.

4. **Tests pin the old API.** A test may assert on the old state list, call the old
   methods, or import the old constants. The test must be rewritten to exercise the
   new system's equivalent behaviour — not just deleted (the behaviour is still
   user-visible and worth pinning).

5. **Constants become orphaned.** A colour constant (`GUIDE_GREY`) or a margin value
   exists only for the old system. Grep after removal; if zero references remain,
   delete the constant too.

## The retirement checklist (in order)

### 1. Identify the full footprint

Grep for every identifier belonging to the old system:

```bash
grep -n "_old_state\|_old_method\|OLD_CONSTANT" src/pc/gui.py
```

Categorize each hit:
- **State initialization** (`self._x = ...` in `__init__`, `load()`, `_build()`)
- **Event handler branch** (`if self._x is not None: self._old_handler(event); return`)
- **Draw call** (`self._draw_old()`)
- **Method definition** (`def _old_method(self): ...`)
- **Constant** (`OLD_CONSTANT = "..."`)
- **Test reference** (in `tests/`)

### 2. Remove event handler branches first

These are the highest-risk: if you delete the method before removing the branch that
calls it, the file won't parse (or will crash at runtime). Remove each branch:

```python
# In _on_crop_drag — REMOVE this block:
-        if self._guide_drag is not None:
-            self._on_guide_drag(event)
-            return
```

Do this for every shared handler that had an early-return for the old system.

### 3. Remove draw calls from render sequences

Find where the old system's draw was called in the full redraw path and remove just
that line:

```python
self._draw_rulers(self.c_after, *self._after_off, ph.width(), ph.height())
-self._draw_after_guides()          # ← remove
self._draw_after_lines(arr, ph.width(), ph.height())
```

### 4. Remove method definitions

Now that nothing calls them, delete the methods. Include any section comments that
belonged exclusively to the old system:

```python
-    # -- After-pane reference guides -----------------------------------------
-    # Pure measurement aids: ...
-    def _after_guide_at(self, x, y, iw, ih): ...
-    def _draw_after_guides(self): ...
-    def _on_guide_drag(self, event): ...
-    def _on_guide_release(self, event): ...
```

### 5. Remove state initializations (all locations)

Grep for the state variable name. Remove from **every** initialization site:

```python
# __init__:
-        self._after_guides = []
-        self._guide_drag = None

# load():
-        self._after_guides = []
-        self._guide_drag = None
+        self._ruler_visible = False   # ← add the NEW system's reset here instead

# _build() or rebuild:
-        self._guide_drag = None       # ("v"|"h", index) while a guide is dragged
```

**Critical:** if the new system has its own state that needs resetting on load, add
that reset in the same location where you removed the old one. Don't just delete —
replace with the new system's equivalent.

### 6. Remove orphaned constants

After all code removals, grep for each constant the old system used:

```bash
grep -n "GUIDE_GREY" src/pc/gui.py
```

If the only hit is the definition itself, remove it (and its comment block).

### 7. Rewrite tests

The old test exercised the old API. Rewrite it to pin the **new** system's behaviour:
- Same user-visible contract (e.g., "pressing in a border creates a line")
- New state variables (`_ruler_top` instead of `_after_guides[0]`)
- New method names (`_on_cross_press` with side detection)
- New visual tags (`"ruler"` instead of `"after_guide"`)

Do NOT just delete the test. The behaviour is still user-visible and worth pinning.

### 8. Verify

```bash
python -c "import ast; ast.parse(open('src/pc/gui.py').read())"  # syntax
python -m pytest tests/ -q                                        # full suite
grep -n "_old_state\|_old_method\|OLD_CONSTANT" src/pc/gui.py    # zero hits
```

## Pitfalls

- **Removing methods before branches** → `AttributeError` at runtime (the branch
  still calls the deleted method). Always remove call sites first.
- **Forgetting a state init in `_build()`** → the attribute exists from `__init__`
  but is never reset when the panel rebuilds for a new image. The old value leaks
  into the next session.
- **The `getattr(self, "_ruler_top", None)` pattern** — per-side attributes that are
  only set on first drag don't exist until then. Tests must use `getattr` with a
  default, not bare attribute access, or they'll raise `AttributeError`.
- **Tag-based canvas items** — if the old system used a tag like `"after_guide"`,
  make sure no remaining code calls `canvas.delete("after_guide")`. It's harmless
  (deletes nothing) but misleading.
- **The new system may need a visibility flag reset.** In this case, `_ruler_visible`
  must be set to `False` in `load()` so a fresh image doesn't show rulers from the
  previous one. The old system's equivalent was `_after_guides = []`.

## What makes this different from "interaction zone migration"

The migration skill (`tkinter-interaction-zone-migration`) covers **adding** a new
zone alongside an existing one (both coexist). This skill covers **removing** the
old system entirely after the new one has absorbed its role. The overlap is in steps
2–3 (handler branches), but this skill adds the state-init sweep, constant cleanup,
and test rewrite that migration doesn't need.
