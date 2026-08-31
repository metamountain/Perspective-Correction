# Measured accuracy, and the measurements that changed the design

Every number here comes from `tests/synth.py`: rendered building facades with an
exactly known camera rotation. Synthetic data answers a *geometric* question
honestly -- on a real photograph nobody knows the true pitch to compare against.
It cannot answer front-end questions (does LSD find the facade under real
texture and JPEG blocking); those need `tests/assets`.

Benchmark: 40 scenes -- focal lengths {18, 24, 28, 35, 50} mm equivalent x
pitch/roll {3/1.5, 6/-2, 10/0, 14/3} deg x {flat facade, corner view}.

## Where it ended up

| | pitch mean | pitch p90 | pitch max | roll mean | roll max |
|---|---|---|---|---|---|
| focal length known (EXIF or `--focal-35mm`) | **0.10 deg** | 0.19 deg | 0.61 deg | 0.018 deg | 0.081 deg |
| focal length unknown (stripped web JPEG) | 2.03 deg | 3.77 deg | 5.41 deg | **0.017 deg** | 0.117 deg |

**Roll is accurate whether or not the focal length is known**, and that is not
luck. `roll = atan2(u_x, -u_y)` where `u = K^-1 v_z`; both components carry a
factor `1/f`, which cancels. Pitch is `atan2(f, |v_z - c|)` and scales linearly
with the assumed focal length. So an unknown lens puts a proportional error on
exactly one of the two corrections, and levelling is free.

## Four ideas that measurement rejected

### 1. Estimating the focal length from the horizon position

The geometry is exact and elegant. Once the vertical vanishing point is known
the horizon has one unknown left: its normal is fixed, only the distance
`d = -f^2/|a|` is free, and `pitch ~ sqrt(-d/|a|)` so a 50 % error in the
horizon position is only a 25 % error in the correction. It should have won.

| estimator, no EXIF | mean abs error | p90 | max |
|---|---|---|---|
| 28 mm prior only | 2.23 deg | 5.34 | 7.21 |
| horizon estimator | 3.86 deg | 10.14 | **16.15** |
| two-vanishing-point estimator | **2.12 deg** | 5.41 | **6.68** |
| both | 3.73 deg | 10.14 | 16.14 |

Two failures, found in order. First, intersecting arbitrary pairs of horizontal
lines: only lines parallel *in the world* meet on the horizon, and a facade edge
crossing a paving joint meets it in the middle of the picture. About 7 000
meaningless crossings against a few hundred real ones put a true 28 mm scene at
36 mm. Replacing pairwise intersections with sequential-RANSAC consensus fixed
that and exposed the second failure: a dominant horizontal vanishing point
3.1 million pixels away pins the horizon to no better than a few hundred pixels,
because its positional uncertainty is `R * sigma_theta`. Weighting by that
conditioning helped, then let two confidently-wrong clusters agree with each
other and produce 65 mm for a 28 mm scene.

The honest conclusion is that a facade photographed straight on genuinely does
not determine the focal length from lines alone: one horizontal direction fixes
one *point* on the horizon, not the line. The code is kept and documented under
`--focal-estimate horizon`; the default is `vp`.

### 2. Merging collinear line fragments

LSD splits one facade corner into fragments at every balcony, so joining them
back into one long precise line ought to help a length-weighted fit.

| | pitch mean | pitch max | roll mean | vertical lines |
|---|---|---|---|---|
| merging on | 0.33 deg | 3.58 deg | 0.045 | 158 |
| merging off | **0.10 deg** | **0.61 deg** | **0.018** | 236 |

(with a known focal length, so the focal noise does not mask the effect)

It forces a single straight line through fragments that are not exactly
collinear, and replaces many independent measurements with one. Default off,
available as `--merge-lines`.

### 3. Fitting the focal length in the joint refinement

A horizontal vanishing point near infinity says "this direction is perpendicular
to up" -- information about roll, none about the focal length, since `K^-1 v` is
independent of `f` when `v[2] == 0`. Left in a three-parameter Nelder-Mead fit it
still moves the cost, because a larger `f` tilts `up` out of the image plane and
shrinks every residual through the unit-norm normalisation. The optimiser duly
ran the focal length up: a true 28 mm scene at 3 deg of tilt came back as 42.5 mm
and tripled the pitch correction. `f` is now only fitted when the geometry
actually constrains it (`sigma_geo < 0.35`).

### 4. Hard switching between "trust EXIF" and "trust geometry"

A single badly conditioned measurement won outright and produced 64 mm for a
28 mm scene, an 8 deg pitch error. Replaced by inverse-variance blending in log
space, where a sharp measurement can still override a vague prior but a vague
one barely moves it: worst-case error over the same set fell from 7.98 deg to
0.11 deg.

## One idea that measurement accepted

**Damping the pitch when the focal length is a guess.** The error is roughly
symmetric but its consequences are not: verticals left slightly converging read
as an ordinary photograph, verticals splayed outwards at the top read as a
mistake.

| damping | mean abs error | max | over-corrections |
|---|---|---|---|
| 1.00 | 2.23 deg | 7.21 | 15/40 |
| 0.90 | 2.15 deg | 6.81 | 12/40 |
| **0.85** | **2.14 deg** | 7.21 | **9/40** |
| 0.80 | 2.26 deg | 7.61 | 9/40 |
| 0.70 | 2.57 deg | 8.41 | 6/40 |

0.85 cuts over-corrections by 40 % at no cost in mean accuracy. It applies to
pitch only, and only when the focal length was not supplied.

## Half-timbered facades: the shallow brace, not the steep one

Fachwerk is the adversarial case for a vertical-lines method, and it is
adversarial in a specific way. Braces come in mirrored pairs at a consistent
angle, so they form a *coherent* false vanishing point rather than scattered
noise. 20 scenes (brace lean x pitch/roll), focal length supplied so the focal
prior does not mask the effect:

| brace lean off vertical | pitch mean | pitch max |
|---|---|---|
| **20 deg** | **0.85 deg** | **3.24 deg** |
| 25 deg | 0.55 deg | 1.68 deg |
| 28 deg | 0.20 deg | 0.66 deg |
| 31 deg | 0.11 deg | 0.37 deg |
| 45 deg | 0.47 deg | 1.40 deg |

The steep 45 deg brace is harmless: it falls outside any plausible candidate
window. The dangerous one is the shallow 20 deg brace, which sits deep inside
it. Roll is unaffected throughout (worst 0.47 deg).

The fix is weighting, not gating:

| vertical window | prior softness | Fachwerk pitch max | plain facade pitch mean |
|---|---|---|---|
| 32 deg | 0.60 | 3.24 deg | 0.12 deg |
| 18 deg | 0.60 | 3.28 deg | 0.12 deg |
| 32 deg | **0.35** | **1.36 deg** | 0.12 deg |
| 18 deg | 0.35 | 1.37 deg | 0.11 deg |

Narrowing the window from 32 deg to 18 deg moves the worst error by 0.04 deg.
Sharpening the prior from 0.6 to 0.35 cuts it by 58 %, at no cost on plain
facades. `angular_softness` is 0.35 by default because of this table;
`--angular-softness` exposes it.

## Two more ideas that measurement rejected

**Merging the horizontal lines only.** Verticals and horizontals have opposite
needs -- a facade offers many unoccluded verticals, but its few horizontals are
routinely broken in half by a tree, and a vanishing point is located by the
baseline of the lines voting for it. So merging just the horizontal pool should
have helped. On facades with tree occluders it made things markedly worse:
pitch mean 0.28 -> 1.28 deg, worst 2.84 -> 8.90 deg, horizon support
0.930 -> 0.811. Across a tree, the fragments being joined are often *not* the
same world line: two string courses at slightly different heights fall inside
the offset tolerance and merge into one confidently wrong long line.

**Masking the vertical pool only.** Since masking helps the geometry but starves
the focal estimator, masking only the verticals should have given both. It gave
neither: pitch max 3.00 deg against 2.84 deg with no mask at all. Removed rather
than kept as a third mode. See docs/masking.md for the table that does matter.

## Known weakness

A flat-on facade with no EXIF is the hard case and the common one for web
JPEGs. The horizon is under-determined, the 28 mm prior carries the estimate,
and a 50 mm shot corrected as if it were 28 mm is under-corrected by roughly a
factor of two. Mitigations in place: the damping above, a confidence penalty for
a guessed focal length (0.60), and the `--max-pitch` cap. The real fix is a
learned focal-length prior (GeoCalib, or Hold-Geoffroy et al.'s perceptual
measure); that is future work, not something to pretend around. Until then,
`--focal-35mm` on a folder shot with one lens is exact and costs one flag.
