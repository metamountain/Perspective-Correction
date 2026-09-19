---
name: large-module-split-proposal
description: Analyze an oversized source file and produce a concrete composition-based split plan with method assignments, wiring examples, and migration order
source: auto-skill
extracted_at: '2026-09-16T07:51:12.425Z'
---

# Large Module Split Proposal

## When this applies

A single source file exceeds ~800 lines (or ~25k tokens) and needs to be split for maintainability, testability, or AI-coder context budget. The task is to produce a *proposal document* (not the actual refactor) with enough specificity that a future session can execute it step-by-step.

## Procedure

### 1. Build a method map (don't read the whole file)

For files >2000 lines, do NOT `read_file` the entire thing. Instead:

```
grep -n "def \|class " <file>   # or use grep_search with pattern "^\s*(def|class)\s"
```

This gives you every method/class name + line number in one pass (~1–2k tokens for a 5000-line file). Group the results by functional area based on naming conventions and line proximity.

### 2. Identify natural boundaries

Look for:
- **Functional clusters**: methods that only call each other (e.g., all `_crop_*` methods form one cluster)
- **Widget groups**: in GUI code, each Toplevel/Canvas often owns a self-contained interaction
- **State ownership**: which methods read/write the same instance variables? Methods sharing state stay together; methods with disjoint state can separate.
- **Entry points**: public API methods (called from outside) vs. internal helpers (only called within the file)

### 3. Choose the pattern

| Pattern | When | Trade-off |
|---------|------|-----------|
| **Composition/Delegation** (default choice) | Controllers own widget subsets + state; parent delegates via `self.crop._on_drag(...)` | One back-reference indirection; flat navigation |
| Mixin | Shared behaviour with no state of their own, multiple classes need it | MRO complexity; harder for AI to trace ownership |
| Facade + submodules | Top-level file becomes a thin wiring shell (~200 lines) | Slightly more import boilerplate |

**Default: Composition + Facade.** Avoid Mixin unless the same stateless behaviour is genuinely needed by 3+ unrelated classes.

### 4. Produce the split table

For each proposed file, list:
- File name (convention: `<parent>_<area>.py`, e.g., `gui_review_crop.py`)
- Estimated line count (from method map line ranges)
- Which methods move there (by name or line range)
- What it imports from other new files

### 5. Write a wiring example

Show ONE concrete example of how a controller plugs into the parent:
```python
class CropController:
    def __init__(self, panel, canvas):
        self.panel = panel   # back-reference
        ...
# In parent's _build():
self.crop = CropController(self, self.canvas)
```

This is the pattern every other controller follows — one example suffices.

### 6. Define migration order

Order by **risk ascending**:
1. Pure functions / zero widget state (trivial extract, zero risk)
2. Self-contained Toplevels or additive features (low coupling)
3. Most-tested interactions (existing tests validate the extract)
4. Hub methods that everything calls (`_redraw()`, etc.) — extract LAST
5. Top-level shell shrinks to facade after all extracts

Each step must be independently shippable (tests pass, app runs).

### 7. Quantify the benefit

Include a small table: "Task X loads N tokens before vs. M tokens after." This justifies the refactor effort and helps future sessions decide whether the split is still needed.

## Output format

Write into the project's findings/debug document (or a dedicated `SPLIT.md`):
```
## Module Splitting — <filename>
### Problem (1–2 sentences + size table)
### Pattern choice (table with rationale)
### Proposed split (file table with method assignments)
### Wiring example (one code block)
### Migration order (numbered list, each step = one PR)
### Context budget (before/after table)
```

## Anti-patterns

- **Splitting by line count alone** ("move lines 1000–2000 to file B"). Must be functional boundaries.
- **Extracting the hub first.** If `_redraw()` is called by 15 methods, extracting it before those methods are in their own files creates a web of imports that's worse than the original monolith.
- **Proposing without method names.** "Move crop-related code" is not actionable. "Move `_on_crop_press`, `_on_crop_drag`, `_on_crop_release`, `_apply_crop`, `_cancel_crop` (L1889–2288)" is.
