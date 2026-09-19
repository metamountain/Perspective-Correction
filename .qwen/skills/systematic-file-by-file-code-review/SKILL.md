---
name: systematic-file-by-file-code-review
description: Execute a read-only file-by-file code review with severity-classified findings, durable progress tracking, and compaction-safe resumption
source: auto-skill
extracted_at: '2026-09-16T19:17:52.819Z'
---

# Systematic File-by-File Code Review

## When this applies

The user requests a read-only review of a codebase (or subset) to find bugs, inconsistencies, and improvement opportunities — without modifying any source code. Findings are collected in a single document (`debug.md` or similar) for the user to decide on later. The pass may span multiple sessions due to context-window limits.

## Procedure

### 1. Define scope and ordering

- List all files to review (e.g., `src/pc/*.py` + entry points).
- Order: **small pure modules first** (config, geometry, layout), then core logic (model, lines, warp, pipeline), then I/O & optional backends (imageio, masks, deeplsd, inpaint, birefnet), then GUI (layout, review, gui), then entry points last.
- Rationale: pure modules are fast to read and establish conventions; the GUI is largest and benefits from already-understood core semantics.

### 2. Per-file protocol

For each file:

1. **Read the full file** (use `offset`/`limit` pagination for files >500 lines; target ~1000 lines per read call).
2. **Judge against three axes:**
   - **Correctness**: Does the code do what its docstring/comments claim? Are there off-by-one errors, wrong coordinate spaces, missing guards?
   - **Consistency**: Do two functions that should agree (e.g., colour constants in two modules) actually agree? Is a setting used where it's documented?
   - **Defensiveness**: Are failure paths handled? Are optional dependencies imported lazily? Would a corrupt/missing input crash or degrade gracefully?
3. **Classify each finding by severity:**
   - **HIGH** — likely wrong behaviour or a real bug that produces incorrect output.
   - **MED** — latent risk, inconsistency that may bite later, or a design choice that contradicts the documented intent.
   - **LOW** — style, clarity, dead code, misleading comments, magic numbers without justification.
4. **Write the finding into `debug.md`** under a `## src/pc/<filename>` section immediately after finishing the file. Do NOT batch findings in memory across multiple files — write them as you go so progress survives compaction.

### 3. Finding format

Each finding is a numbered entry:

```
N. **SEVERITY** — `symbol` (line X): description of the problem. Suggested fix (not applied).
```

- Name the file + symbol/line precisely.
- State the problem in one or two sentences.
- Give a concrete suggested fix (code snippet if non-trivial, otherwise a sentence).
- End with "No HIGH findings" / "No HIGH/MED findings" if the file is clean — an explicit "clean" verdict is valuable; it tells the user this file was actually read and judged, not skipped.

### 4. Handle context compaction (resume protocol)

When resuming after a compaction:

1. **Do NOT acknowledge, greet, or re-introduce.** Go straight to the next `read_file` call or substantive finding.
2. Re-read the tail of `debug.md` to confirm which files are already covered.
3. Continue with the next file in the ordering list.
4. If a file was partially read before compaction, re-read it from the start (partial understanding is worse than none).

### 5. Targeted checks (beyond per-file)

After the file-by-file pass, add sections for cross-cutting concerns:

- **Colour/constant globality**: Are semantic constants defined once or duplicated with diverging values?
- **Layout/ergonomics**: Any UI element that's unreachable, mis-sized, or missing feedback?
- **Threading safety**: Are Tk callbacks posted through `queue` + `after(0, ...)` rather than called directly from worker threads?

### 6. Consolidation and status

At the end of the pass:

- Add a "Status" line at the top of `debug.md`: "complete" or "in progress — files remaining: …".
- If proposals have concrete code blocks, group them in a dedicated section ("Concrete Code Blocks — F1–F5") separate from the per-file findings.
- Update the project instructions file (QWEN.md) with a summary table of open items ranked by impact.

## Anti-patterns

- **Acknowledging the resume.** "Got it, thanks for the context!" wastes tokens and violates the user's explicit instruction. Go straight to work.
- **Holding findings in memory across files.** If compaction hits after reading 5 files but before writing them, all 5 are lost. Write after each file.
- **Reading speculatively.** Don't read a file "to see if it's relevant" — the scope is defined upfront; every file in scope gets a full read and a verdict.
- **Marking a file clean without reading it fully.** A "no findings" verdict on a 4800-line file that was only half-read is worse than no verdict at all.
- **Applying fixes during the review pass.** The review is read-only. Suggested fixes are written into `debug.md` as text, never executed.

## Validated at scale (2026-09-16)

27 source files + 4 entry points reviewed across 3 sessions (with 2 compactions). Zero HIGH-severity bugs found; 1 MED (shape invariant in `lines.prepare()`), ~35 LOW findings. All files received an explicit verdict (findings or "no findings"). The pass produced a 1317-line `debug.md` with per-file sections, targeted cross-cutting checks, concrete code blocks for 10 proposals, and a quantified module-split plan.
