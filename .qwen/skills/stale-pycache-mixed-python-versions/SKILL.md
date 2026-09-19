---
name: stale-pycache-mixed-python-versions
description: When source fixes appear not to take effect at runtime, suspect stale .pyc bytecode from mixed Python versions before re-debugging the logic
source: auto-skill
extracted_at: '2026-09-17T11:41:09.144Z'
---

# Stale .pyc cache with mixed Python versions

## When this applies

A source-level fix (e.g. adding a `getattr` guard, removing a dead attribute) is
confirmed present in the file, yet the running program still exhibits the old
behaviour — crashes, missing widgets, features that "don't work". The user
reports multiple unrelated symptoms that all trace to one early failure in a
setup or redraw path.

## Diagnosis procedure

1. **Confirm the fix is in the source.** Read the file at the relevant line.
   If the guard/fix is there, the problem is not the code — it's what Python
   actually loaded.

2. **Check the error log for the real traceback.** In this project, `_redraw`
   writes to `pc_errors.log`. The traceback will name the exact attribute and
   line. If the line number in the traceback doesn't match the current source
   (e.g. log says line 2662 but the guard is at line 2710), Python ran an old
   compiled version.

3. **Check for mixed-version bytecode.** List `src/pc/__pycache__/`. If the
   project has been run under both Python 3.12 and 3.13 (or any two versions),
   the `.pyc` files may be from the wrong interpreter. CPython normally detects
   version mismatch via the magic number in the `.pyc` header, but if the same
   minor version was used with different patch releases, or if files were
   copied between environments, stale bytecode can survive.

4. **Clear and restart.**
   ```
   del /q src\pc\__pycache__\*.pyc
   ```
   Then restart the application. No source changes needed.

## Why this is easy to miss

- The source file looks correct — `git diff` shows the fix, `read_file` confirms
  it. The developer (or agent) concludes "the code is fine" and starts hunting
  for a second bug that doesn't exist.
- Multiple symptoms (Q2 black, ROI icon missing, horizontal broken) look like
  independent failures but are all downstream of one early `AttributeError` in
  `_build()` or `_redraw()`. The exception is caught by the broad `except` in
  `_redraw`, logged to file, and shown as a status-bar message that scrolls off.
- The user's frustration ("nichts geht mehr", "komplett zerstört") is real — from
  their perspective nothing works — but the root cause is invisible in the source.

## Prevention

- After switching Python interpreters (e.g. moving between system Python and a
  venv, or upgrading minor versions), always clear `__pycache__` before running.
- In this project specifically: the ComfyUI portable install at `D:\ComfyUI_windows_portable`
  has its own Python; if `PATH` resolution picks it up instead of the intended
  interpreter, bytecode will be written for the wrong version.
- A `Makefile` or script target `make clean-pyc` (`del /s /q src\__pycache__\*.pyc`)
  is worth adding if this recurs.

## Symptom checklist (any one of these + fix confirmed in source → clear pycache first)

- Widget that exists in code but doesn't appear on screen
- Feature that was fixed but "still broken" after restart
- Q2/after pane black while Q1/before pane renders fine
- Multiple unrelated GUI features failing simultaneously
- Error log traceback references a line number that doesn't match current source
