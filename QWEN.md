# QWEN.md — Agent instructions (this session)

## Current task: read-only debug pass

**Directive (user):** work alone; do **not** modify any code. Instead do a
file-by-file review of `src/pc` (and the entry points) to find errors and collect
improvement suggestions — but **do not apply them**. Collect everything in one list.

### Write permissions (strict)

| File | Permission |
|---|---|
| `debug.md` | **WRITE** — the findings list, only file written during the pass |
| `QWEN.md` | **WRITE** — this plan/instructions file, only other file touched |
| everything else | **READ-ONLY** — no edits, no deletes, no new files |

### What a finding is

A finding names **file + symbol/line**, states the problem, and gives a suggested
fix that is **not applied**. Severity legend:

- **HIGH** — likely wrong behaviour or a real bug.
- **MED** — latent risk / inconsistency that may bite later.
- **LOW** — style / clarity / minor.

A finding is a pointer, not a verdict (per CLAUDE.md): it flags something for the
user to decide, not an instruction to change code.

### Method / token budget

- Budget is limited (~120k tokens) → go **file by file**, reading each fully before
  judging it. No speculative bulk reads.
- Order: small pure modules first (`config`, `geometry`), then the core
  (`model`, `lines`, `warp`, `pipeline`), then I/O & optional backends
  (`imageio`, `mlsd`, `deeplsd`, `inpaint`, `masks`, `birefnet`, `planar`,
  `optimize`, `scheme`, `preview`, `prefs`, `deps`), then the GUI split
  (`layout`, `review`, `gui`) and entry points (`cli`, `rectify.py`).
- After each file, append its findings to `debug.md` under a `## src/pc/<file>`
  section so progress is durable across compaction.

### Findings already identified (to be written into debug.md)

1. `geometry.normalize_vp` — the zero-norm fallback returns `(0, 1, 0)`, which in
   OpenCV's y-down convention points **down**, not up. Likely harmless (a zero
   vector has no direction) but the fallback is semantically "the wrong way".
2. `geometry.plane_normals` — docstring says the normal is ``K^T l`` but the code
   computes `lines @ K`. For row-vector lines these are equivalent, so it's a
   wording mismatch, not a bug (LOW).
3. `model.focal_from_horizon` — dead no-op line
   `sigma_log_f *= 1.0 + abs(float(p @ perp)) / max(R, 1e-6) * 0.0`. The trailing
   `* 0.0` makes the multiplier always `1.0`, so the comment's intended lever is
   not active (LOW/MED — dead code with a misleading comment).

### External tools

- **Firecrawl API key:** `fc-72208db35c9d4c5b8996b00e0adff3a9` — use for web research when `web_fetch` gets 403/404. Endpoint: `https://api.firecrawl.dev/v1/scrape` (POST, header `Authorization: Bearer <key>`).

### Status

In progress. Findings accumulate in `debug.md`; this file holds only the plan and
the write-permission rule.
