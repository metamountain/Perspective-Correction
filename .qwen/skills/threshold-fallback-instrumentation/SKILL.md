---
name: threshold-fallback-instrumentation
description: Debug a threshold-based gate/fallback by instrumenting per-stage counts and comparing end-to-end accuracy of each path against ground truth
source: auto-skill
extracted_at: '2026-09-16T18:35:32.237Z'
---

# Threshold / Fallback Instrumentation

## Problem

A pipeline has a conditional branch (gate, threshold, fallback) that selects between
two paths based on a computed metric (e.g., "if fewer than 25% of segments survive
the gate, fall back to the base detector"). The user reports the fallback triggers
on some inputs but not others. You need to determine:

1. **Why** the threshold trips (exact counts at each stage).
2. **Whether it matters** — does the "active" path actually produce better results
   than the "fallback" path? If they're identical, the threshold is cosmetic.
3. **What to do** — lower the threshold, remove the fallback, or leave it alone.

## Procedure

### Step 1: Instrument per-stage counts

Write a small standalone script (not inline `python -c` — buffering eats output on
Windows) that calls each stage of the pipeline separately and prints exact numbers:

```python
# For each test image:
base = base_detector(image)          # e.g., LSD → 2769 segments
guide = guide_detector(image)        # e.g., M-LSD → 101 guides
gated = gate(base, guide)            # e.g., 525 survive
threshold = len(base) // 4           # e.g., 692
verdict = "FALLBACK" if len(gated) < threshold else "OK"
print(f"LSD={len(base)}  guides={len(guide)}  gated={len(gated)}  "
      f"min_keep={threshold}  -> {verdict}")
```

Print one line per image. This gives you the exact margin (or deficit) for each case.

### Step 2: Compare end-to-end accuracy of each path

Write a second script that runs the **full pipeline** with each path forced active
(e.g., `detector="lsd"` vs `detector="hybrid"`) and compares results against ground
truth:

```python
for det in ["base", "gated"]:
    result = full_pipeline(image, detector=det)
    print(f"{det}: roll_err={result.roll - gt_roll:+.2f}°  "
          f"pitch_err={result.pitch - gt_pitch:+.2f}°  f={result.focal:.1f}")
```

Key insight to check: **is the fallback path literally the same code as the base
path?** If `fallback → return base_result`, then by definition the results are
bit-identical. In that case the question is not "does the fallback lose accuracy?"
(it can't) but "does the gated path ever *gain* accuracy over the base?"

### Step 3: Diagnose the structural cause

If the threshold trips on most inputs, check whether it's a **structural mismatch**
rather than a tuning issue:

- Guide detector produces N guides; base detector produces M segments.
- The gate can keep at most ~N×k segments (where k is the average overlap factor).
- If N×k < threshold × M structurally, no amount of per-image tuning helps — you
  need more guides, a looser gate, or a different threshold formula.

In this session: M-LSD produced 47–101 guides vs 1600–2800 LSD segments. The gate
structurally kept only 19–29%, right at the 25% boundary.

### Step 4: Document and present options

Write findings into `debug.md` (or equivalent) with:
- Exact count table per image
- Accuracy comparison table (each path vs ground truth)
- Root cause (structural vs tuning)
- Numbered options with trade-offs

**Do not change the threshold until the user picks an option.** The data may show
the "active" path is actually *worse* than the fallback, in which case the fix is
the opposite of what was expected.

## Key rules

- **Never guess the threshold from theory.** Print the actual numbers first.
- **Check if the fallback is a no-op by construction.** If `fallback = return base`,
  the accuracy comparison will show identical results — that's expected, not a bug.
  The real question shifts to "does the gated path ever help?"
- **Separate detector issues from pipeline issues.** If both paths show the same
  large error on some axis (e.g., roll), the problem is downstream (VP search,
  RANSAC fit), not in the line detection. Document it but don't conflate it with
  the threshold question.
- **Use standalone scripts, not `python -c`.** On Windows, inline Python often
  produces empty output due to stdout buffering. Always write a `.py` file.

## Anti-pattern

Lowering the threshold from `//4` to `//5` because "the fallback triggers too often"
without checking whether the gated path actually produces better results. In this
session, the data showed the gate was *slightly worse* on the one image where it
was active (pitch error 0.81° vs 0.29°), so lowering the threshold would make
things marginally worse, not better.
