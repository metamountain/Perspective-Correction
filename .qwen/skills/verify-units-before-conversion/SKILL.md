---
name: verify-units-before-conversion
description: When displaying or comparing angle values from geometry code, verify the units of the source function before applying any conversion (degrees/radians).
source: auto-skill
extracted_at: '2026-09-16T17:57:32.444Z'
---

# Verify units before converting angles

## Problem

`Scene.true_roll_pitch()` in `tests/synth.py` returns values computed as:

```python
r = math.hypot(u[0], u[1])
return math.atan2(u[0], -u[1]), math.atan2(u[2], r)
```

`math.atan2` of dimensionless unit-vector components returns **radians**. But the values are small (e.g. 0.14 rad ≈ 8°), so when a render script printed them with `np.degrees()` applied, it produced numbers like `+0.04°` — which looked like the geometry was broken.

The actual values were already correct in radians; the extra conversion made them appear near-zero.

## Rule

Before applying any unit conversion (`np.degrees`, `math.radians`, etc.) to a value returned by a project function:

1. **Read the source** of the function that produced the value.
2. Check whether it already applies a conversion internally (e.g. returns `math.degrees(math.atan2(...))` vs raw `math.atan2(...)`).
3. If uncertain, print the raw value and compare its magnitude to the expected physical quantity. A roll of "8 degrees" should be ~0.14 in radians or ~8 in degrees — a value of 0.04 is neither, which is the tell-tale sign of a double conversion.

## How to apply

- When writing diagnostic or display code that prints angles from project geometry functions, always trace the return path to confirm units first.
- When a printed angle looks implausibly small (or large) compared to the scene setup parameters, suspect a unit mismatch before suspecting a geometry bug.
- This applies to any function in `geometry.py`, `model.py`, or `synth.py` that returns angular quantities.
