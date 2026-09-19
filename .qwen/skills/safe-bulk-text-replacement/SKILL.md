---
name: safe-bulk-text-replacement
description: How to safely do find-and-replace across many files without destroying them — the read-before-write discipline and verification steps that prevent catastrophic data loss.
source: auto-skill
extracted_at: '2026-09-14T17:53:25.898Z'
---

# Safe bulk text replacement across many files

## The catastrophe pattern (do NOT do this)

```python
# DESTROYS FILES — opens for write (truncating) before the read value is available:
[open(fp, 'w').write(re.sub(r'\bbpc\b', 'pc', open(fp).read()))
 for fp in files]
```

The list comprehension evaluates left-to-right per element, but `open(fp, 'w')`
truncates the file to zero bytes **before** the inner `open(fp).read()` executes.
Result: every file becomes empty. This happened twice in one session (2026-09-14),
destroying ~60 `.py` files including a 4300-line GUI module and an untracked
module that had to be reconstructed from `.pyc` inspection.

## The safe pattern

**Read all content first, then write:**

```python
import re, os

def bulk_replace(root, pattern, replacement):
    changed = []
    for dp, _, fns in os.walk(root):
        for f in fns:
            if not f.endswith('.py'):
                continue
            fp = os.path.join(dp, f)
            content = open(fp, encoding='utf-8').read()   # READ FIRST
            new = re.sub(pattern, replacement, content)
            if new != content:                             # only write if changed
                with open(fp, 'w', encoding='utf-8') as fh:  # THEN write
                    fh.write(new)
                changed.append(fp)
    return changed
```

Key differences from the catastrophe:
1. **Read completes before any write opens.** The `content` variable holds the
   full string; the file is not touched until after the read.
2. **Only write if content actually changed.** Avoids touching mtime on unchanged
   files and makes the operation idempotent.
3. **Use `with` for the write.** If the process crashes mid-write, at least the
   file descriptor is closed (though partial writes are still possible — see below).

## Verification (non-negotiable)

After any bulk replacement, **immediately** verify:

```python
# 1. No file is empty
for fp in changed:
    assert os.path.getsize(fp) > 0, f"EMPTY: {fp}"

# 2. The old pattern is gone (where it should be)
remaining = [fp for fp in all_files if re.search(pattern, open(fp).read())]
print(f"{len(remaining)} files still match: {remaining}")

# 3. Spot-check one file's content
print(open(changed[0]).read()[:200])
```

If step 1 fails, you have destroyed files. Stop and recover immediately.

## Recovery when files ARE destroyed

### Tracked files (in git)

```bash
git checkout HEAD -- path/to/file.py    # restore from last commit
git show :path/to/file.py > file.py     # restore from index (staged version)
```

### Untracked files (never committed)

Git cannot help. Options in order of fidelity:

1. **`.pyc` inspection** — if the module was imported at some point, CPython
   wrote a `.pyc` to `__pycache__/`. You can extract function names, signatures,
   and string constants via `marshal` + `types.CodeType`:

```python
import marshal, types, dis

with open('module.pyc', 'rb') as f:
    f.read(16)  # skip header (Python 3.7+)
    code = marshal.load(f)

# Top-level names
print(code.co_names)
# Iterate nested code objects for function signatures
for const in code.co_consts:
    if isinstance(const, types.CodeType):
        print(const.co_name, const.co_varnames[:const.co_argcount])
```

2. **Context reconstruction** — use the pyc structure (function names, argument
   counts, string constants) plus knowledge of the module's purpose and sibling
   modules' patterns to rewrite it. This is lossy: docstrings, comments, and
   exact logic are gone.

3. **Backup / editor history** — check if the OS or editor kept a previous version.

## The sys.path trap (rename-specific)

When renaming a package, verify that tests actually import the **new** name from
the **new** location:

```python
import sys; sys.path.insert(0, 'src')
import pc.config as c
print(c.__file__)  # MUST be src/pc/config.py, not some other copy
```

If an old copy of the package is installed (via `pip install -e .` or a `.pth`
file in site-packages), tests that still use the old import name will silently
import the **old** code and "pass" while testing nothing new. The fix:
- Change all imports to the new name
- Verify resolution points at the right file
- If an off-limits directory provides the old package, you cannot uninstall it —
  just make sure your code never imports the old name

## Detecting zeroed files when you walk into a messy tree

If you resume a session and find files listed as `M` (modified) in `git status`
but the diff shows the entire content deleted (`@@ -1,N +0,0 @@`), the file was
truncated to 0 bytes on disk. This is the signature of the catastrophe above —
or any other write-before-read bug.

**Fast check (Windows cmd):**

```bat
for %f in (QWEN.md README.md knowledge.md) do @for %A in (%f) do @if %~zA==0 echo EMPTY: %f
```

**Fast check (any shell, all modified files at once):**

```bash
# POSIX
git diff --name-only | while read f; do [ ! -s "$f" ] && echo "EMPTY: $f"; done

# PowerShell
git diff --name-only | ForEach-Object { if ((Get-Item $_).Length -eq 0) { "EMPTY: $_" } }
```

**What to look for in the diff:** a hunk that reads `@@ -1,219 +0,0 @@` with every
line prefixed `-` means the file went from 219 lines to 0. That is not an edit;
it is data loss. Restore immediately:

```bash
git checkout HEAD -- <file>
```

Do this **before** doing anything else with the working tree, because other
changes in the same tree may be legitimate and you don't want to `git checkout`
them away by accident.

## Checklist before declaring a bulk rename done

- [ ] `grep -r '\bold_name\b' --include='*.py'` returns zero hits (excluding
      intentional constants like API field names)
- [ ] `python -c "import new_package; print(new_package.__file__)"` points at
      the right directory
- [ ] Full test suite passes AND the tests import from the new location
- [ ] No file in the repo is empty (`find . -name '*.py' -empty`)
- [ ] `git status` shows only expected modifications, no unexpected deletions
