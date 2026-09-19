---
name: verify-findings-implementation-status
description: Cross-reference a findings/proposals document (debug.md) against actual code to produce an accurate implementation status table
source: auto-skill
extracted_at: '2026-09-16T12:13:50.832Z'
---

# Verify Findings Implementation Status

## When this applies

The user asks "check where we stopped", "what's been achieved", or "update the status" after a gap (days/weeks) between when findings were written and now. A document (`debug.md`, `TODO.md`, etc.) lists proposed changes with code blocks, but some may have been implemented in intervening sessions. The task is to produce an accurate done/partial/not-done table without re-reading every file fully.

## Procedure

### 1. Extract the verification checklist from the findings doc

For each finding (F1–F5, P1–P5, etc.), identify **2–3 concrete code markers** that would prove implementation:

- A specific symbol name that should now exist (e.g., `yaw_skipped` key in diagnostics)
- A specific behaviour change (e.g., `fill()` returns a note instead of raising)
- A new file/directory that should exist (e.g., `tests/shootout/`)
- A config field that should be added (e.g., `interactive: bool = False`)

### 2. Grep for the markers (not full reads)

Use `grep_search` with the key symbol names across `src/pc`. This is fast and definitive:

```
# Does the fix exist?
grep "yaw_skipped" src/pc/model.py        → no match = not done
grep "interactive" src/pc/config.py       → no match = not done
grep "auto_crop_max_loss|crop_max_loss" src/pc  → shows which naming won

# Did a new file appear?
glob "src/pc/gui_*.py"                    → empty = split not done
glob "tests/shootout/**/*"                → empty = suite not built
```

### 3. Read only the ambiguous cases

If grep shows the symbol exists, read **5–10 lines** around it to confirm it's the actual fix (not a coincidental name match or a different usage). Example: `crop_max_loss` appeared in config — but is it the *collapsed* single setting (F4 done) or just a rename? Read the surrounding context.

### 4. Check for partial implementations

A finding can be partially done:
- **F2**: The review-side threshold exists (`auto_crop_threshold = max(crop_max_loss, 0.30)`) but the proposed `interactive` flag in config was not added. Mark as "Partially done" with a note on what's missing.
- **P5**: Docstring added (the documentation half) but no dedicated test. Mark as "Partially done."

### 5. Produce the status table

Format:

| ID | Description | Status |
|---|---|---|
| F1 | short description | **Done** — `file:line` evidence |
| F2 | short description | **Partially done** — what exists, what's missing |
| P1 | short description | **Not done** — current behaviour unchanged at `file:line` |

Include the file:line reference for "done" items so a future session can verify without re-grepping. For "not done" items, note the current (unchanged) code location.

### 6. Update the project instructions file

Write the status table into `QWEN.md` (or equivalent) replacing any stale "pending" list. Add a "Next steps" section ranked by impact/dependency order so the user can pick up where they left off without re-reading the full findings doc.

## Anti-patterns

- **Re-reading every file fully to "double-check."** Grep + targeted 10-line reads are sufficient; the findings doc already has the exact symbols and line numbers.
- **Assuming a finding is done because the symbol name exists.** `horiz_vps` in model.py's cost function is NOT the P3 marker (which is `diagnostics["horiz_vps"]` as a list of points for GUI display). Read the context.
- **Marking "partially done" without specifying what's missing.** "F2 partially done" is useless; "F2: review threshold exists at review.py:74, but no `interactive` flag in config so batch path still uses 5%" is actionable.
