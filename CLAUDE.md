# Batch Perspective Correction — working notes

## What this is actually for

**Straightening converging verticals in architectural photographs, in batch.**
The metric is not "how much perspective did it remove" — it is **how many photos
it ruined**, because a batch tool runs unattended over a folder someone cares
about. A photo left alone costs nothing. A photo warped on a bad hypothesis is
gone unless the original survives.

Every default here follows from that asymmetry. When in doubt: do nothing, and
offer it for manual review.

## Where it came from

`chsasank/Image-Rectification`, which implements a good paper (Chaudhury et al.,
ICIP 2014) badly. It was **run and measured**, not just read — full write-up in
`docs/reference-review.md`. The disqualifying finding:

```
compute_votes:  vp = model[:2] / model[2]
```

A level camera puts the vertical vanishing point **at infinity**, `model[2] == 0`,
so the correct answer scores `nan` → zero votes. Measured: two exactly parallel
verticals returned `[0., 0.]` votes. The code is therefore *biased towards
inventing a correction for photographs that need none* — the exact failure this
project cannot have. Five identical runs on one image put its "vertical"
vanishing point 3 657 to 47 283 px from the truth, on a 600 px tall image, with
no seed so a re-run changes the output.

Nothing here dehomogenises a vanishing point in any path that must work for
every image. `geometry.bearing_to_vp` is the fix and `test_reference.py` pins it.

## The model is three numbers, and that is the point

`roll`, `pitch`, `f`. The horizon is **not detected** — it is `K^-T u`, the polar
line of the vertical vanishing point. Horizontal lines only pin down `f` and
cross-validate; they never drive the vertical estimate, because windows,
balconies and roof edges generate false horizontal candidates by the hundred.

The consequence that matters most:

| | depends on `f`? | measured accuracy |
|---|---|---|
| **roll** (levelling) | **no** — the `1/f` factors cancel in `atan2(u_x, -u_y)` | mean **0.018°** |
| **pitch** (converging verticals) | **yes, linearly** — `atan2(f, \|v_z - c\|)` | 0.10° known `f`, 2.03° guessed |

So levelling is free and exact. Correcting verticals is only as good as the
focal length. **Roll is applied first** (`R = Rx(pitch) Rz(-roll)`) — pitching
first would tilt the axis the roll is measured against.

## Four ideas that measurement killed

Full tables in `docs/accuracy.md`. Recorded because each one still *sounds*
right, and will be re-proposed otherwise.

1. **Focal length from the horizon position.** Beautiful: once `v_z` is known
   the horizon has one unknown, `d = -f²/|a|`, and `pitch ≈ √(-d/|a|)` so it is
   better conditioned than `f` itself. Measured **3.86° mean / 16.15° worst**
   against 2.12/6.68 for the two-vanishing-point estimator. Two distinct
   failures behind it — see below. Demoted to `--focal-estimate horizon`.
2. **Merging collinear line fragments.** LSD splits a facade corner at every
   balcony; rejoining them should help a length-weighted fit. It forces one
   straight line through fragments that are not exactly collinear and replaces
   many independent measurements with one: pitch mean **0.10° → 0.33°**, worst
   **0.61° → 3.58°**. Default off, `--merge-lines` to enable.
3. **Fitting `f` in the joint refinement unconditionally.** A horizontal
   vanishing point near infinity carries *no* focal information (`K^-1 v` is
   independent of `f` when `v[2] == 0`) but still moves the cost, because a
   larger `f` shrinks every residual through the unit-norm normalisation. A true
   28 mm scene came back as 42.5 mm. Now `f` is only fitted when `sigma_geo < 0.35`.
4. **Hard switching between EXIF and geometry.** One badly conditioned
   measurement won outright: 64 mm for a 28 mm scene, 8° of pitch error. Replaced
   by inverse-variance blending in log space; worst case fell to 0.11°.

The two horizon failures are worth keeping separate because the first hid the
second. **(a)** Intersecting arbitrary pairs of horizontal lines — only lines
parallel *in the world* meet on the horizon; a facade edge crossing a paving
joint meets it mid-picture. ~7 000 meaningless crossings vs a few hundred real
ones. **(b)** After fixing that with sequential-RANSAC consensus: a dominant
horizontal vanishing point 3.1 million px away pins the horizon to no better
than a few hundred px, because its positional uncertainty is `R·σ_θ`.

**Lesson:** the elegant estimator lost to the dumb prior. Measure before
believing, and keep the losing branch documented rather than deleted.

## Fachwerk: it is the *shallow* brace that is dangerous

Half-timbered facades are the adversarial case, and the intuition about why is
backwards. The steep 45° brace is harmless — it falls outside any plausible
candidate window. The killer is the **20° brace**, deep inside the window, and
braces come in mirrored pairs at a consistent angle so they form a *coherent*
false vanishing point rather than scattered noise. Worst pitch error by brace
lean: 20° → **3.24°**, 25° → 1.68°, 28° → 0.66°, 31° → 0.37°, 45° → 1.40°.

And the fix is **weighting, not gating**. Narrowing the vertical window from 32°
to 18° moves the worst error by 0.04°. Sharpening `angular_softness` from 0.6 to
0.35 cuts it 58 % (3.24° → 1.36°) at zero cost on plain facades. That is why the
default is 0.35 and why the window is still a generous 32°.

Roll survives all of it (worst 0.47°). And on Fachwerk *without* a known focal
length the confidence diagnostics report `weakest: focal` in every single case —
the diagonals are not what limits accuracy there, the focal prior is.

## One idea measurement accepted

**Damping pitch by 0.85 when the focal length is a guess.** The error is
symmetric; its consequences are not. Verticals left slightly converging read as
an ordinary photograph; verticals splayed outwards at the top read as a mistake.
Over-corrections 15/40 → 9/40 at no cost in mean accuracy. Pitch only, and only
when `f` was not supplied.

## The detector: LSD stays, and it was a close call

The front end was the obvious suspect for the accuracy ceiling, and the
diagnosis was right: on a real barn LSD returned 4823 raw segments with a
**median length of 16 px** on a 1600 px grid, only 29 longer than a tenth of the
short edge. M-LSD (Apache-2.0, 6.1 MB, vendored in `models/`) returns ~110
segments with a median of **250–318 px** — 20× longer.

**And it is less accurate.** Angular precision scales with length *and* endpoint
precision, and M-LSD decodes endpoints from a 256×256 displacement map — about
5.5 px of quantisation at 1400 px, i.e. ~1° on a 300 px line, where LSD's
sub-pixel endpoints give ~0.1° on a 50 px fragment. **Long and coarse loses to
short and sharp.**

But the synthetic benchmark is biased for LSD (flat rendered lines are its home
turf and out-of-distribution for a network trained on photographs), so the
question was asked again on real photographs, with a **round-trip test**: warp by
a known rotation `R_d`, and the warped copy's up must be `R_d @ u0` — real
texture, exact ground truth, no need to know `u0`. That test is shipped as
`tools/benchmark_detectors.py`.

| | synthetic pitch mean | real round-trip mean | real p90 |
|---|---|---|---|
| **LSD** | **0.21°** | 0.94° | 3.44° |
| M-LSD large | 1.42° | 1.44° | 2.55° |
| hybrid (LSD gated by M-LSD) | 1.37° | **0.78°** | 2.41° |
| union (LSD + M-LSD) | 0.48° | 1.25° | **2.23°** |

The picture *inverts* between the benchmarks. LSD stays the default because it
wins decisively on ground truth and loses only narrowly on 24 real samples, and
because the hybrid's 7.9° worst-case roll on synthetic scenes shows it can gate
away evidence it needed. Promoting on 24 measurements against a benchmark it
loses would be exactly the mistake the rest of these notes documents.

**If a user has real data, run the benchmark tool and let it decide.** That is
the missing evidence, not more argument.

## Masking: only with a known focal length

`--mask auto` (cheap vegetation/sky) and `--mask file` (a folder of PNGs from an
external segmenter such as SAM) both go through one seam, `masks.build`. The
measurement that governs their use:

| | pitch max |
|---|---|
| f known, mask off | 2.84° |
| f known, mask on | **0.93°** |
| f unknown, mask off | **5.58°** |
| f unknown, mask on | 10.05° |

Masking removes the worst outliers *and* removes evidence, and the focal
estimator is the part most starved for it. Default is off. Never recommend
masking without `--focal-35mm` or EXIF.

Note on SAM: plain SAM has no text encoder and cannot take words at all. Text
prompting comes from GroundingDINO → SAM, or SAM 3's concept head. Prompt the
*rejects* (`tree, foliage, sky, car, person`) rather than the building —
concrete countable nouns ground well, abstractions like "architecture" do not,
and a missed piece of building is lost evidence.

## The confidence score is validated, not just plausible

Six real barn photographs, round-trip tested (warp by a known `R_d`; the warped
copy's up must be `R_d @ u0`, so real texture gets exact ground truth without
knowing the true pose):

| photo | conf | real error |
|---|---|---|
| quaker | 0.75 | 0.33° |
| small red barn | 0.70 | 0.34° |
| white sparrow | 0.66 | 0.63° |
| XYZ | 0.42 | 0.78° |
| pole barn | 0.49 | 1.01° |
| Alte Scheune | 0.44 | **2.80°** |

**Confidence ranks them by actual error.** It is a product of six heuristic
factors and there was no guarantee of that, so
`test_confidence_ranks_the_photographs_by_their_real_error` asserts it. The
whole skip-and-review design rests on this property; do not change the factors
without re-running that test.

Two traps when measuring a correction by re-analysing its output:
- **Cropping moves the principal point** away from the image centre, which the
  model assumes coincide. Worth 0.2–0.8° of apparent residual on these six.
  It is also a real limitation on any *previously cropped* input — web JPEGs.
- **A re-estimated focal length makes the metric self-inconsistent.** Fix `f`
  in both passes or the number means nothing.

With `f` fixed correctly, Alte Scheune goes 7.09° → **0.63°**; with `f` wrong
(35mm) it goes 6.87° → **9.42°**, worse than doing nothing. The auto estimate
picked 41mm for it. That is the whole argument for `--focal-35mm` in one line.

## Known weakness, stated plainly

**A flat-on facade with no EXIF.** One horizontal direction fixes one *point* on
the horizon, not the line, so the focal length is genuinely not determined by
lines alone. Web JPEGs are usually exactly this case. Mitigations: the damping
above, a 0.60 confidence factor for a guessed focal length, and `--max-pitch`.

The real fix is a learned focal prior (GeoCalib, or Hold-Geoffroy et al.'s
perceptual measure). Not implemented — it would be the project's first deep
learning dependency. **`--focal-35mm` on a folder shot with one lens is exact
and costs one flag**, and should be the first thing suggested to a user whose
results look under-corrected.

## Conventions that are correct as written

- **`H = K R K^-1`, always.** A pure camera rotation: three degrees of freedom,
  all physical, yaw deliberately zero. It *cannot* shear. The reference built a
  general projective transform plus an affine fix-up — eight free parameters,
  nothing tying them to anything a camera could do, and a `clip_factor` hack to
  stop the output exploding. Asserted by
  `test_the_warp_is_a_camera_rotation_so_it_cannot_shear`.
- **The `1/|g|²` reweighting in `refine_vp`.** `line · vp == |g| · sin(θ)`, so
  dividing the algebraic residual by `|g|²` turns the cheap eigenvector solution
  into the angular one. Not a fudge factor.
- **An explicit "already parallel" hypothesis.** Pairwise RANSAC can never
  propose exactly-parallel, so without it a straight photo is decided by
  whichever noise realisation won.
- **Seeded RNG everywhere.** A batch tool that gives different output on a
  re-run is not usable. `test_the_same_input_gives_a_byte_identical_output_twice`.
- **Confidence is multiplicative**, so any single factor can veto.
- **Analysis at 1600 px, geometry in angles.** Angles are scale invariant, so
  the only thing needing rescaling to full resolution is `f`. That is a real
  argument for this parameterisation, not just tidiness.
- **`review.py` has no Tkinter import.** Every manual-mode behaviour is a pure
  function of state and is tested headlessly; `gui.py` is only the shell. This
  was forced by the dev container having no Tkinter and turned out to be the
  right split anyway.

## Testing

`python tests/run_tests.py` — 59 tests, standalone, no pytest.

Synthetic scenes (`tests/synth.py`) carry an **exactly known camera pose**. The
high-frequency-mask notes warn that synthetic fixtures misled that project; the
difference is that it was asking a *statistical* question about real texture,
while this asks a *geometric* one where a rendered scene with ground truth is
strictly the better instrument — on a real photograph nobody knows the true
pitch to compare against.

What synthetic data cannot test is the front end: does LSD find the facade under
real texture, JPEG blocking, foliage and lens distortion. That needs
`tests/assets/`, where six further tests activate as soon as real photos are
present. `*_upright.*` is asserted to be left unchanged, `*_skip.*` to be
refused.

## Licensing

MIT, and it must stay clean. darktable's `ashift.c` is GPL-3.0 and ShiftN's
source is LGPL. Both were **read to understand the algorithms** and neither was
copied. Constants like "assume 28 mm" are facts about the problem, not
expression. See `docs/prior-art.md` for what was taken conceptually and what was
deliberately rejected.

## Environment

Plain CPython, `pip install -r requirements.txt`. Tkinter is needed only for the
GUI and ships with the python.org Windows installer; the CLI runs without it.
OpenCV's LSD was dropped in 4.1 and restored in 4.8, hence the detector fallback
chain in `lines.py`.
