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
the missing evidence, not more argument. `tools/benchmark_detectors.py` now
actually exists -- this section claimed it shipped while the repository held a
zero-byte `tools_placeholder`.

**FLD had never been measured; it does not beat LSD.** It was in the chain only
as a fallback for OpenCV builds without LSD, so it was worth measuring. On the
seven assets, round-trip with the border guard, `f` fixed:

| | mean | p90 | worst |
|---|---|---|---|
| **lsd, masked** | **0.65°** | **1.52°** | **2.02°** |
| lsd, unmasked | 0.70° | 1.66° | 2.89° |
| fld, unmasked | 0.99° | 2.13° | 3.19° |
| fld, masked | 1.36° | 2.85° | 4.76° |
| hough | 2.6-2.7° | ~4° | **36°** |

LSD wins in both conditions and masking helps it further, so the default stands.
Note that masking *hurts* FLD -- it returns fewer, cleaner segments and has less
to spare. Hough is not competitive and its 36° worst case is the argument for
keeping it a last resort.

An earlier run of this table said FLD won unmasked. That was the border
artifact; see the section on it below.

M-LSD could not be measured here at all -- it needs a TFLite runtime
(`pip install ai-edge-litert`) that this interpreter lacks. SOLD2 is available
through `kornia.feature.sold2` with no extra install, and DeepLSD needs the
`cvg/DeepLSD` repository plus weights from `cvg-data.inf.ethz.ch`.

## Masking: BiRefNet, and the two knobs that are not knobs

`--mask auto` (cheap vegetation/sky), `--mask birefnet` (Segment-anything's
replacement) and `--mask file` (a folder of PNGs) all go through one seam,
`masks.build`. The producers are **not interchangeable**, and using one table
for both was the mistake that hid a broken feature for a release.

`--mask auto` is a texture statistic and its measurement is unchanged — it is
why the default is off:

| | pitch max |
|---|---|
| f known, mask off | 2.84° |
| f known, mask on | **0.93°** |
| f unknown, mask off | **5.58°** |
| f unknown, mask on | 10.05° |

**Never recommend `--mask auto` without `--focal-35mm` or EXIF.** It removes
green, chaotic and sky-like *pixels*, and on a stripped JPEG that takes the
horizontals the focal estimate needed.

`--mask birefnet` removes whole non-building *objects* and does not share that
failure. Round-trip on the seven assets, with the border guard:

| | mean | worst |
|---|---|---|
| mask off, f known | 0.70° | 1.68° |
| **BiRefNet-HR, f known** | **0.65°** | **1.36°** |
| mask off, f unknown | 1.12° | 2.25° |
| **BiRefNet-HR, f unknown** | **0.88°** | **1.96°** |

The gain is real but modest, and **larger where the focal length is unknown** --
the opposite of how `--mask auto` behaves. Earlier drafts of this table claimed
0.98° → 0.56°; that spread was the border artifact, not the mask.

**It masks 40–70 % of the frame and 1–11 % of the line evidence**, because what
it removes is sky, grass and road. That gap is the whole reason `masks.credible`
judges on evidence rather than coverage, and it means the 55 % refusal threshold
has a wide margin here.

**A line is dropped only when both its endpoints are inside the mask.** No
threshold, no weight. Anything crossing the boundary -- a facade edge running
down into shrubbery, a roofline against the sky -- keeps its full say, because
the half of it on the building is real evidence and the fit is length-weighted
anyway.

Two earlier rules were tried and both are worse or more complicated:

* a **sampled threshold** dropping a segment once 60 % of five points along it
  fell inside. It discarded straddling lines wholesale and which side of the
  threshold a line landed on turned on a sample or two.
* a **per-segment weight** equal to the visible fraction. Measurably slightly
  better (0.558 deg mean / 1.01 worst against 0.556 / 1.15 for endpoints) but it
  makes the segmenter a soft influence on every line rather than a decision
  about a few, and it needs a third factor in the weight. The endpoint rule is
  within noise of it and has nothing to tune.

**The shrink is what makes the mask worth having at all.** BiRefNet cuts exactly
along the silhouette, so the building's own corner and roof edges have both ends
just inside the mask and are the first thing the endpoint rule throws away.
Round-trip over ten assets, shrink as a fraction of the frame diagonal:

| shrink | mean | worst |
|---|---|---|
| 0.000 (~0 px) | 0.663° | 1.95° |
| 0.002 (~4 px) | 0.597° | 1.43° |
| 0.004 (~8 px) | 0.575° | 1.43° |
| **0.008 (~15 px, default)** | **0.556°** | **1.15°** |
| 0.016 (~31 px) | 0.639° | 1.64° |
| no mask at all | 0.661° | 1.69° |

Read the first row against the last: **unshrunk, the mask buys nothing** --
0.663° against 0.661° for not masking. It removes as much good evidence as
clutter. Everything the segmenter is worth here is bought by handing the
silhouette back. A fraction of the diagonal rather than a pixel count, so it
does not change meaning with `--detect-max-edge`.

**Two things that look like knobs and are not.** The matte is near-binary, so
the threshold does nothing: 0.1 to 0.9 moves the masked share 50.5 % → 51.1 %.
And *shrinking* the mask a few pixels, so silhouette lines survive
`drop_masked`, measures worse — 0.556° → **0.839°** at **2 px**, and 16 px is
barely worse than 2. It is a step, not a slope: a thin ring re-admits the
*neighbouring building's* lines, which are long, straight, and converge
somewhere else. The selective version of that rescue already exists and is
load-bearing: without `masks.protect_structure` the same set measures
0.906°/3.76°.

**`--mask-export` writes the masks once.** It bridges the interpreter split
(torch without tkinter, tkinter without torch) *and* turns a repeated run into a
file read. `tests/assets/masks` is that cache, 42 KB for six photographs, white
meaning ignore. It must never be used for the round-trip test — the warped copy
has moved and the cached mask has not (IoU 1.000 unwarped, 0.802 warped), which
reports 0.69°/2.35° for an estimator that achieves 0.56°/1.40°.

## Segment Anything was here, and what its failure teaches

SAM is deleted. It needed an invented criterion to say which of its forty
regions was the building, and a region survived on **either** line density (which
works) **or** how straight its outline is (which has no signal: median 1.00 over
42 real regions, 41 of 42 above the floor, rescuing the sky and the foreground
grass). The broken half of an "either" test silently vetoed the working half, and
`--mask sam` measured **worse than not masking** — 1.04°/3.52° against
0.98°/2.80°.

Three things to carry forward:

1. **It was validated on synthetic shapes and both tests passed.** A drawn
   rectangle scores 0.9+, a ragged blob under 0.6; real SAM regions are neither.
   Synthetic fixtures are right for a *geometric* question with ground truth and
   wrong for a *statistical* one about real texture.
2. **Repaired SAM still won the worst case** (0.62°/1.18°) and the union of both
   models won outright (0.52°/1.18°), because they fail on different
   photographs. Six assets is too thin to justify two models, a checkpoint hunt
   and an AGPL-3.0 dependency — but it is the first thing to re-measure if more
   ground truth appears.
3. **An optional dependency can change a required one at import.**
   `ultralytics` replaces `cv2.imread`, returning `(h, w, 1)` for a greyscale
   read, which broke `--mask file` for anyone who merely had it installed.
   `masks.load` now insists on two dimensions.

## The confidence score: a gate, not a ranking

This section used to say the score was *validated* because it ranked six barn
photographs by their real error (rho = -0.68, asserted by a test). **That
correlation was the border artifact.** With the guard in place it is **-0.11**:

| photo | conf | real error |
|---|---|---|
| quaker | 0.75 | 0.45° |
| 79cb3387… | 0.70 | 0.54° |
| white sparrow | 0.66 | 0.33° |
| hospital | 0.52 | 0.58° |
| pole barn | 0.49 | 1.68° |
| Alte Scheune | 0.44 | 1.11° |
| XYZ | 0.40 | 0.19° |

The honest reading is not "the score is broken". Once the artifact is gone the
errors span 0.19° to 1.68° -- there is almost nothing left to rank, and a rank
correlation over seven nearly-equal values is mostly noise. What the old test
was ranking was how much of the frame each photograph filled.

So the assertion moved to the property the skip-and-review design actually
rests on, which is a **bound and not an ordering**: everything the gate admits
must be measured accurately (`< 2°`), and the gate must still admit most of a
set of ordinary architectural photographs, or "nothing it touches is wrong"
could be satisfied by refusing everything. Both are asserted;
`test_every_photograph_it_is_confident_about_is_measured_accurately` and
`test_the_confidence_gate_admits_most_of_a_good_set`.

Restore a ranking test only with assets that genuinely span a range of accuracy.
Two traps when measuring a correction by re-analysing its output remain true:

- **Cropping moves the principal point** away from the image centre, which the
  model assumes coincide. It is a real limitation on any *previously cropped*
  input -- web JPEGs.
- **A re-estimated focal length makes the metric self-inconsistent.** Fix `f`
  in both passes or the number means nothing.

## The round trip measured itself for a while, and it cost two conclusions

`tests/assets/_round_trip_error` warps a photograph by a known rotation and
re-estimates. `warpPerspective` has to invent the band that rotates in from
outside the frame, and `BORDER_REPLICATE` invents it by smearing edge pixels
into **long, perfectly straight streaks**. Where a photograph's content reaches
the frame edge -- the ordinary architectural case -- the detector reads those
streaks as lines.

It hid for as long as every asset was a barn with sky at its edges, where the
smear is bland. One modern facade that fills the frame exposed it:

| | mean | worst |
|---|---|---|
| harness as it was | 1.71° | 6.08° |
| **with the border guard** | **0.66°** | **1.68°** |

`hospital-nikon-d60_f27.jpg` went **6.08° → 0.33°**, from the worst photograph in
the set to one of the best. Both passes are now cropped by 8 % before analysis,
which also keeps them the same size and therefore the same focal length in
pixels. Pinned by `test_the_border_guard_is_what_makes_the_measurement_honest`.

**It produced two confident wrong conclusions before it was found**, and both
are worth remembering as a shape:

1. **"Wide-angle lens distortion."** It had a mechanism, a camera that fits (an
   18-55 kit zoom at 18 mm), and a crop experiment that appeared to confirm it
   (full frame 6.08°, centre 80 % 0.53°). It was wrong. Implementing Hugin's
   radial model and sweeping the `b` coefficient moved 6.08° to 5.70° at a
   realistic value -- while discarding the invented band moved it to 0.33°. The
   crop "confirmed" the hypothesis because cropping the base also removes the
   content that gets smeared.
2. **"FLD beats LSD."** Measured 0.84° against 0.98° unmasked. With the guard,
   LSD wins in both conditions (0.70/2.89 against 0.99/3.19 unmasked;
   0.65/2.02 against 1.36/4.76 masked). FLD was simply biting less on the
   artifact.

**A benchmark that is wrong in the incumbent's disfavour is the dangerous kind**,
because it reads as a discovery rather than a bug.

## Beyond the limit means refuse, not trim

`--max-pitch` and `--max-roll` used to be caps: an estimate past them was
clamped to the cap and applied. That turns "I do not believe this" into "I will
do as much of it as I am allowed to", which is the opposite of every other
decision in this tool.

What exposed it was a photograph of a railway station ceiling, added while
filling in the asset wishlist. A coffered ceiling has a strong, clean bundle of
parallel lines and a perfectly good vanishing point, so the estimator found it
correctly and every confidence factor scored well — **0.57**, better than most
of the barns. Nothing in the model can tell that the bundle it locked onto is
the ceiling grid rather than the world vertical. The result was pitch pinned to
the `-20 deg` clamp and 41 % of the frame thrown away, at high confidence.

**Confidence cannot catch this and is not built to.** Every factor it scores —
share, count, spread, horizon support, focal, stability — measures *how well the
lines agree*, never *whether they are the right lines*. On a ceiling they agree
beautifully.

The magnitude of the correction can, and does. Two ceilings wanted 24 and 26
degrees of pitch; the most extreme genuine facade in the asset set, a modern
hospital shot from below, wants 16.6 and is untouched by the rule. So
`refuse_beyond_limit` is on by default and `--clamp-beyond-limit` restores the
old behaviour for anyone who wants it.

It is a magnitude test, not a semantic one, so it does not *understand* the
difference between a ceiling and a wall — it only notices that one of them asks
for something no photographer plausibly wanted. That is enough here and it is
the kind of guard this project prefers: cheap, and wrong in the safe direction.

**Masking catches the same case independently**, which is worth knowing: with
`--mask birefnet` the Prague ceiling falls to confidence 0.09, because a
segmenter looking for a salient object finds almost nothing in a ceiling texture
(95 % of the frame masked). A semantic check in front of a geometric one.

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

## Vertical control lines, taken from Hugin

The manual mode has three ways in, and this is the third: the user clicks two
points on something they *know* is vertical in the world — a door jamb, a
downpipe, a building corner. It is Hugin's `t2` control point, and Hugin's own
advice carries over: place the two points as far apart as the structure allows,
because the direction of a short segment is badly conditioned. A segment under
8 % of the short edge is refused rather than quietly accepted, since a mis-click
would otherwise steer the whole fit.

**They replace the detected pool rather than joining it.** A user who marks two
door jambs is not adding two votes to three hundred, they are saying the three
hundred were beside the point. Adding them with a large weight instead would
mean choosing how large, and the answer would be "large enough to win", which is
the same thing with a fudge factor in it. Two is the threshold because two lines
determine a vanishing point — Hugin needs two as well — so `min_vertical_lines`
drops from 4 to 2 while they are in force.

This is the case that striking lines out cannot fix: on a corner view every line
the detector found may be real and still belong to the wrong plane. There is
then nothing to delete, only something to state.

**The trap it walks into, and the fix.** Confidence is largely a count of
supporting lines, so two of them scored **0.04** and the photograph came back
`SKIP, weakest: count` — the feature refusing its own input. Control lines now
count as a decision, exactly like moving a slider, and `would_skip` returns
`None` while they are active. Refusing evidence for being scarce is right when a
detector produced it and wrong when a person did. Pinned by
`test_marking_verticals_does_not_get_the_photo_skipped`; validated against a
synthetic scene's exact pose by
`test_a_control_line_that_is_really_vertical_recovers_the_true_pose`.

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
