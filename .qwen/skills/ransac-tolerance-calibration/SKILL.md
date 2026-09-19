---
name: ransac-tolerance-calibration
description: Calibrate test tolerances for RANSAC VP estimators by measuring actual error, not guessing from theory
source: auto-skill
extracted_at: '2026-09-16T09:38:29.234Z'
---

# RANSAC Tolerance Calibration

## Problem

When writing tests for vanishing-point estimators (yaw, pitch, roll) on synthetic scenes, the first instinct is to set tight tolerances based on "the math should be exact." It isn't — RANSAC + line detection introduces systematic bias that grows with angle magnitude:

- Small angles (≤5°): error typically <1.5°
- Medium angles (~12°): error ~2–3° (underestimation)
- Large angles (~25°): error ~4–5° (overestimation)

The bias is not random noise; it's a consistent direction of error at each magnitude, caused by the interaction of line-detection quantization, RANSAC inlier selection, and the VP→bearing→angle chain.

## Procedure

1. **Write the test with a generous tolerance first** (e.g., 5° for any angle). Run it.
2. **Read the actual error from the assertion message.** The test should print the measured value: `f"yaw={math.degrees(m.yaw):.2f} deg, expected ~{expected} deg"`.
3. **Set the tolerance to ~1.5× the observed error** for that specific scene configuration (size, focal, seed). This gives headroom for minor platform differences without being so loose it stops testing anything.
4. **Document the tolerance in the docstring**: "recovered within 3.5°" not "recovered accurately."

## Key rules

- Never set a tolerance below what you've actually measured on this machine. The estimator's error is deterministic for a given seed+scene, but the *margin* should absorb small drift (OpenCV version, CPU FMA differences).
- Different angle magnitudes need different tolerances. A single global tolerance either passes the easy cases uselessly or fails the hard ones unfairly.
- If you change the Scene parameters (focal, size, seed), re-measure and re-calibrate. The error is scene-dependent.
- The tolerance tests the *estimator*, not the *implementation*. If the implementation changes (e.g., new RANSAC iterations), re-run and check whether tolerances need tightening or loosening.

## Anti-pattern

Setting `tolerance = 1°` because "the synthetic scene is noise-free." The scene has Gaussian noise (`noise=3.0` in synth.py), LSD quantizes to pixels, and RANSAC picks a subset of lines. The pipeline is approximate by design; the test tolerance must reflect that.
