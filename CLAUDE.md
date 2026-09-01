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

## One idea measurement accepted

**Damping pitch by 0.85 when the focal length is a guess.** The error is
symmetric; its consequences are not. Verticals left slightly converging read as
an ordinary photograph; verticals splayed outwards at the top read as a mistake.
Over-corrections 15/40 → 9/40 at no cost in mean accuracy. Pitch only, and only
when `f` was not supplied.

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

## The front end: LSD stays, and it has now been beaten twice, narrowly

Four alternatives have now been measured against LSD -- M-LSD, DeepLSD, FLD and
Hough. None of them is the default, and the reasons differ enough to keep the
two learned ones apart.

### M-LSD: long and coarse loses to short and sharp

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

The M-LSD rows above come from an interpreter that had a TFLite runtime; this
one does not, so they cannot be re-run here. SOLD2 is reachable through
`kornia.feature.sold2` with no extra install and is still unmeasured.

### DeepLSD: judgement, not geometry

The section above ends with "if a user has real data, run the benchmark
tool and let it decide". This is that run. DeepLSD (Pautrat et al., CVPR 2023,
MIT) is not another wireframe network -- it regresses a distance field and an
angle field and hands *those* to LSD in place of the image gradient, so the
endpoints still come from LSD's sub-pixel fit. That matters, because the reason
M-LSD lost was endpoint quantisation, not judgement.

Twelve photographs x six rotations, round trip with the border guard, `f` fixed
at 24 mm, mask off:

| | mean | p90 | worst | seconds |
|---|---|---|---|---|
| **deep-hybrid** (LSD gated by DeepLSD) | **0.71°** | **1.65°** | 3.78° | 68.8 |
| deep-union (LSD + DeepLSD) | 0.76° | 2.16° | **3.74°** | 55.4 |
| lsd | 0.77° | 2.04° | 4.71° | **18.2** |
| deeplsd alone | 0.86° | 2.41° | 7.10° | 48.4 |

**The shape is the same as the M-LSD result and it is the interesting part.**
The learned detector *alone* is the worst of the four -- worse than plain LSD on
all three statistics, and its 7.10° worst case is a photograph ruined. Used as a
*gate* over LSD it is the best of the four. Neither model is a better line
detector; one of them knows which lines are structure and the other knows where
they are, and the hybrid is the only arrangement that gets both.

**LSD stays the default anyway**, for reasons that are not about the numbers:
deep-hybrid costs torch, a 98 MB checkpoint, a research checkout that is not on
PyPI, and `pytlsd`, which has no wheels and builds from source. That is a large
bill for 0.06° of mean and it buys nothing on the machine of anyone who cannot
pay it. It is a genuine option now, not a default, which is exactly what the
M-LSD section argued for and could not deliver because no TFLite runtime was
ever installed here.

**And the reason is no longer only the dependency bill: the win does not
survive a change of the fixed focal length.** The table above fixes `f` at
24 mm, which is close to what these twelve photographs were actually shot at
(16-33 mm). Repeating the identical run at 35 mm -- a *wrong* focal for all of
them, which is the case the "known weakness" section says web JPEGs land in --
inverts the order:

| f fixed at 35 mm | mean | p90 | worst |
|---|---|---|---|
| **deep-union** | **1.05** | **2.02** | **9.55** |
| lsd | 1.22 | 3.00 | 19.50 |
| deep-hybrid | 2.37 | 5.71 | 22.91 |

deep-hybrid goes from best to worst, and its worst case from 3.78° to **22.91°**.
A gate that decides which lines are structure is apparently tuned to agree with
the geometry only when the geometry is roughly right; when `f` is wrong it gates
away evidence the fit needed -- the same failure the M-LSD hybrid showed as a
7.9° worst-case roll on synthetic scenes. **A detector whose ranking depends on
getting another parameter right is not a safer default, it is a second thing
that can be wrong.**

deep-union is the one that does not collapse: it beats LSD's worst case in both
conditions (3.74 vs 4.71, and 9.55 vs 19.50). That is the arrangement to
re-measure if the dependency bill ever becomes payable by default -- not the
hybrid, despite the hybrid winning the headline table.

Twelve photographs is still thin. The honest claim is "deep-hybrid did not lose
on any of the three statistics", not "deep-hybrid is better".

## Masking: BiRefNet, and the two knobs that are not knobs

`--mask birefnet` (a segmenter) and `--mask file` (a folder of PNGs) go through
one seam, `masks.build`. The producers are **not interchangeable**, and using
one table for both was the mistake that hid a broken feature for a release.

A third, `--mask auto`, was a cheap texture statistic and is **gone** -- the CLI
no longer offers it and `masks.build` accepts the word only to keep an old
preferences file from failing. Its measurement is why:

| | pitch max |
|---|---|
| f known, mask off | 2.84° |
| f known, auto mask on | **0.93°** |
| f unknown, mask off | **5.58°** |
| f unknown, auto mask on | 10.05° |

It removed green, chaotic and sky-like *pixels* where the question is about
*objects*, and on a stripped JPEG that took the horizontals the focal estimate
needed. Never restore it without `--focal-35mm` or EXIF in front of it.

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
file read. `tests/assets/masks` is that cache, 160 KB for sixteen photographs (four of
them not yet in the asset folder), white meaning ignore. It must never be used for the round-trip test — the warped copy
has moved and the cached mask has not (IoU 1.000 unwarped, 0.802 warped), which
reports 0.69°/2.35° for an estimator that achieves 0.56°/1.40°.

### Segment Anything was here, and what its failure teaches

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
3. **An optional dependency can change a required one.** `ultralytics` replaces
   `cv2.imread`, returning `(h, w, 1)` for a greyscale read, which broke
   `--mask file` for anyone who merely had it installed; `masks.load` now
   insists on two dimensions. `simple-lama-inpainting` does it the other way
   round, at install time -- its stale pins downgrade Pillow and numpy and
   break OpenCV in the same interpreter. **Install optional backends with
   `--no-deps`**; see the Environment section.

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

## Generating the band the rotation opens up

`--fill telea`, `--fill lama` and `--fill comfyui` (`src/bpc/inpaint.py`) replace
the padded corners with generated pixels. Read that against the first section of
this file: those pixels were never photographed, so the feature is the most
dangerous thing in the tool by construction, and the containment is where the
design lives.

**The three are on one scale, not two.** It is tempting to file `telea` under
"harmless" because it loads no model and downloads nothing — `cv2.inpaint`
marches colour and gradient inwards from the boundary and that is all it does.
But a pixel nobody photographed is invented whether a network or a fast-marching
solver put it there, so it lives under the same containment as the other two:
same hole, same `--fill-max-share`, same default of `none`. What it buys is that
it is **deterministic and dependency-free**, which makes it the honest choice for
a thin band of sky or road and the wrong one for anything a viewer would read as
content. It sits *below* lama and comfyui on the invention scale, not outside it.

**There is no `--fill color`, and that is deliberate.** A flat colour in the band
is what `--pad` has always meant, and a second flag saying the same thing would
be a second place to configure one fact — the failure this file keeps warning
about. The review window's colour picker therefore writes `settings.pad`, and
its swatch reads `pad` back rather than showing black over a setting that says
`edge`.

* **The default is `none` and stays `none`.**
* **Only the hole is touched.** `warp.filled_region` warps a white frame and
  marks where no source pixel landed; the composite ramps its alpha *inside*
  that mask and multiplies by it again, so a photographed pixel comes through
  bit for bit. Asserted, with exact equality, by
  `test_the_fill_touches_nothing_that_was_photographed`.
* **A missing backend is an error for that image, never a silent pass-through.**
  A batch that quietly writes un-filled frames when the user asked for a fill is
  the failure mode this whole project is built against.
* **`--fill-max-share` (0.35) refuses to invent most of a picture.** The band a
  plausible correction opens is a few per cent; a 60 % hole means the answer was
  a crop, not a fill.
* Generation runs at `--fill-max-edge` (2048) and only the hole is scaled back
  up. What is being invented is sky, wall and road at the frame edge -- low
  frequency -- and every photographed pixel stays at full resolution.
* **The manual save runs the same seam.** `review.py` `save()` calls the identical
  `warp.filled_region` + `inpaint.fill`, behind the same `fill not in ("", "none")`
  guard, so a photograph corrected by hand gets generated corners rather than an
  un-filled frame. The live preview (`render_after`) deliberately does *not* fill --
  a model load and inference per slider tick is too expensive; only the saved file
  does, which is exactly what the batch does. Pinned by
  `test_single_image_save_runs_the_fill_when_a_mode_is_set` (it hands the warp's own
  hole to `inpaint.fill`) and
  `test_single_image_save_does_not_load_a_backend_when_fill_is_off`.

LaMa is the right default backend: no prompt, ~3 s, and it *continues* structure
rather than inventing objects. ComfyUI is there for the wide band and for anyone
who would rather their own Flux graph did it; the workflow is a file
(`workflows/flux-klein-outpaint.json`), the contract is three node titles
(`BPC_IMAGE`, `BPC_MASK`, `BPC_PROMPT`), and the most likely user error -- posting
an editor export instead of an API export to `/prompt` -- is caught with the fix
in the message.

**`pip install simple-lama-inpainting` downgrades Pillow to 9.5 and numpy to
1.26 and breaks OpenCV in the same interpreter.** Install it with `--no-deps`.
This is the third time an optional dependency has moved a required one (see
`ultralytics` replacing `cv2.imread`), and it is worth treating as a rule:
install optional backends with `--no-deps` and let the failure be an ImportError
rather than a silently changed numpy.

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

## The manual crop is always live, and that is the whole design

The corrected pane carries a crop rectangle at all times. On an uncropped
photograph it *is* the frame, so the four corner handles sit in the frame
corners and there is nothing to switch on. Dragging a handle crops; dragging
anywhere else draws a new rectangle; "Reset crop" puts it back.

**It used to be behind a checkbox and that was the bug.** With the mode off,
`_on_crop_press` returned immediately, so a drag did nothing, produced no
message, and the file saved uncropped — a user action swallowed in silence,
which is the one failure this project does not permit anywhere else. The crop
itself was never broken: `save()` applies it, and did before.

Two things follow from the always-live rectangle and both are load-bearing:

* **A corner grab answers with the *opposite* corner.** That corner then plays
  exactly the role the first click plays when a rectangle is drawn from
  nothing, so adjusting an existing crop and drawing a new one are one drag
  implementation rather than two. `_grab_corner` and `_draw_crop_persistent`
  share the same `crop_rect or (0, 0, 1, 1)` default, because a handle that is
  drawn but cannot be picked up is worse than no handle.
* **A click that never moved is a click.** With the rectangle always live, a
  stray press in the pane would otherwise report "crop too small, ignored" on
  every mis-click, and noise is how a real warning gets ignored.

The rectangle is applied to the *rendered result*, after the correction and
after any fill, in fractions rather than pixels — the preview is a few hundred
pixels and the file is full size, so a pixel rectangle would mean two different
things. Stored as four independent edges: a pitch correction opens the band at
the top and leaves the bottom alone, and trimming only that band should not
require re-placing the other three sides.

**The preview shades the crop, it does not cut it, and that is not cosmetic.**
`render_after(apply_crop=False)` is what the review window asks for. Cutting it
made the after image come back a different size, which `_to_photo` then fitted
into the pane at a different scale — the picture leapt under the cursor the
instant a corner was released. The leap was the visible half. The other half:
the *next* drag was measured against a frame already smaller than the one the
fractions are stored against, and `set_crop_rect` filed it as fractions of the
full canvas, so a second rectangle landed somewhere nobody had dragged, in
silence. Shading keeps one coordinate system for the whole session. Pinned by
`test_the_preview_keeps_its_size_while_the_crop_is_drawn`, which asserts the
returned dimensions after two successive crops.

Two consequences. `_refresh_crop` redraws the overlay *only* — never
`_schedule_redraw` — because the image behind it cannot have changed, and a
re-warp plus a live `telea` fill of a pixel-identical frame is its own kind of
jump. And `status_text` now has to state the crop and what fraction it costs: a
rectangle that exists only as a dimmed area is exactly the thing that gets
forgotten before the save, and the save is where it becomes permanent.

## "Auto crop" is the answer to the band that invents nothing

The band a rotation opens up has two honest answers. Fill it — `telea`, `lama`,
`comfyui` — and pixels the camera never saw end up in the file. Or cut to the
largest rectangle that contains none of it, and pay in frame instead. `Auto
crop` is the second, and it needs no model, no checkpoint and no ComfyUI.

`ReviewSession.auto_crop` is `warp.plan`'s own `crop="auto"` arithmetic —
`inscribed_rect` on the warped quad, original aspect ratio, anchored on the
mapped centre so the composition survives — **minus the `max_crop_loss` gate**.
That gate exists to stop a batch quietly throwing a third of every picture away;
a button pressed by hand is not quiet, and refusing there would be refusing the
thing that was asked for. On the 33 %-loss synthetic scene the plan pads and the
button trims, which is
`test_auto_crop_does_by_hand_what_the_batch_gate_refuses_to_do_alone`.

When the plan already cropped (`crop="inside"`/`"aspect"`, or `"auto"` inside
the gate) the quad runs past the canvas, the clamp yields the whole frame, and
`auto_crop` returns `False` and says so rather than storing a rectangle that
trims nothing. A button that appears to do nothing is worse than one that says
why — same rule as the swallowed drag above.

**"No invented pixel" has to be true against the definition the *fill* path
uses, and it was not.** The inscribed rectangle is exact against the warped
quad, but `warp.filled_region` deliberately dilates the hole by three pixels,
because the resampler leaves a sub-pixel fringe along that diagonal edge and an
inpaint that stops at the geometric boundary leaves a dark rim. Measured on a
9° pitch: **0** invented pixels inside the rectangle at `grow=0` and **88** at
`grow=3`, all of them in the outermost three rows of one corner. So `auto_crop`
now insets by `warp.FRINGE`, the one constant both users read — 0.9 percentage
points of frame on that scene, 35.7 % → 36.6 %.

The inset goes **after** the two guards, not before. The "nothing was padded"
test compares the rectangle against the full canvas, and three pixels of inset
slipped under it: a photograph the plan had already cropped came back with a
stored rectangle that trimmed only the inset. Pinned at all three definitions
of the hole by `test_the_auto_crop_contains_no_invented_pixel`, so the constant
and its two users cannot drift apart.

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

## A run has to be judgeable by someone who did not make it

`--diagnostics` writes the interpreter, library versions, importable backends and
the settings actually in force as a log header; `--json-report` stores the same
block beside the results. "SKIPPED, low confidence" is nearly useless without
them. `run_and_log.bat` collects a whole run — corrected images, overlays, log,
report — into one folder, and writes the environment *before* the run so a failed
run still leaves something diagnosable.

`--remember` stores **paths only** (checkpoint, mask folder, output, focal
length). Correction parameters are deliberately not remembered: a setting that
silently persists between runs is one nobody can reason about, and a batch must
stay reproducible from its command line. Unknown keys are dropped on load so the
file cannot become a second, hidden place where behaviour is configured.

## Testing

`python tests/run_tests.py` -- 147 tests, standalone, no pytest. The modules are
listed explicitly in `run_tests.py`, so a new test file that is not in `MODULES`
runs nowhere and is worse than no test at all.

Synthetic scenes (`tests/synth.py`) carry an **exactly known camera pose**. The
high-frequency-mask notes warn that synthetic fixtures misled that project; the
difference is that it was asking a *statistical* question about real texture,
while this asks a *geometric* one where a rendered scene with ground truth is
strictly the better instrument -- on a real photograph nobody knows the true
pitch to compare against.

What synthetic data cannot test is the front end: does LSD find the facade under
real texture, JPEG blocking, foliage and lens distortion. That needs
`tests/assets/`, where six further tests activate as soon as real photos are
present. `*_upright.*` is asserted to be left unchanged, `*_skip.*` to be
refused.

**Two of those six are currently dormant, and the mask cache is what gives it
away.** There are sixteen masks and twelve photographs: `painted-hall`,
`prague-main-railway-station-ceiling`, `tiled-skyscraper-facade` and
`warsaw-d3200-27mm_upright` have a cached mask and no image. So
`test_files_marked_upright_are_left_alone` and `test_files_marked_skip_are_refused`
both skip for want of assets -- including the Prague ceiling, which is the
photograph the whole "beyond the limit means refuse" section is built on.
`tools/fetch_commons_asset.py` is how the others arrived.

Optional backends are tested by *skipping* cleanly -- M-LSD without a TFLite
runtime, DeepLSD without its checkout or weights, LaMa without its package. A
suite that fails because an optional dependency is absent trains people to
ignore it.

**`test_inpaint.py` deliberately asserts nothing about image quality.** There is
no ground truth for a pixel nobody photographed, so what it pins is
*containment*: exact equality outside the hole, refusal above `--fill-max-share`,
an unknown backend raising rather than passing through, and the shipped ComfyUI
workflow still carrying the node titles the code writes into. Do not add a test
that scores the generated band -- it would be scoring a guess.

## Windows has two Pythons and neither is wrong

This costs more time than any algorithm here, so it is worth stating plainly:

| | torch (BiRefNet) | tkinter (the GUI) |
|---|---|---|
| ComfyUI `python_embeded` | yes, with CUDA | **no** -- the embeddable Python omits tcl/tk |
| system Python | usually not | yes |

A system Python that has *both* is the happy case and is worth checking for
before assuming the split: the DeepLSD and LaMa measurements in these notes were
only possible because this machine's python.org 3.12 carries torch with CUDA and
tkinter at once. When that is true, none of the bridging below is needed.

Both failures print "no backend". `--mask-info` reports both halves plus the
interpreter, and the error message reads which side it is on: in the GUI Python
it offers `--mask-export` *before* suggesting an install, because putting a
multi-gigabyte CUDA torch into a second interpreter on a machine that already
has one is the wrong first answer.

`--mask-export DIR` runs the segmenter once from whichever Python can load it and
writes one mask PNG per photograph; everything afterwards consumes the folder
through `--mask file`. That bridge exists so nobody has to choose between the
segmenter and the review window.

**Anything fiddly belongs in Python, not in a `.bat`.** `run_and_log.bat` once
searched for the checkpoint itself, grew a `^` continuation inside a
parenthesised block -- which cmd splits and runs as a command -- and the mask
prompt silently never appeared. That search is now `--birefnet-model auto`, where
it is tested, and it prefers what *works* over what is largest.

## Environment

Plain CPython, `pip install -r requirements.txt` -- numpy, OpenCV, Pillow,
piexif, and nothing else. Tkinter is needed only for the GUI and ships with the
python.org Windows installer; the CLI runs without it. OpenCV's LSD was dropped
in 4.1 and restored in 4.8, hence the detector fallback chain in `lines.py`.

Everything below is optional, imported lazily, and says what is missing instead
of failing at import:

| feature | needs | ask it |
|---|---|---|
| `--mask birefnet` | torch + weights (ComfyUI's are found by `--birefnet-model auto`) | `--mask-info` |
| `--detector mlsd`, `hybrid`, `union` | `pip install ai-edge-litert`; the model is vendored | `--detector-info` |
| `--detector deeplsd`, `deep-hybrid`, `deep-union` | torch, a `cvg/DeepLSD` checkout in `tools/`, `pip install omegaconf scikit-image pytlsd`, and `models/deeplsd_md.tar` (98 MB) | `--detector-info` |
| `--fill lama` | `pip install --no-deps simple-lama-inpainting` | `--fill-info --fill lama` |
| `--fill comfyui` | a running ComfyUI and an API-format workflow | `--fill-info --fill comfyui` |
| GUI drag-and-drop | `pip install tkinterdnd2` | the window says so |

**The `--no-deps` on that fourth row is not a style preference.** Plain
`pip install simple-lama-inpainting` downgrades Pillow to 9.5 and numpy to 1.26
to satisfy pins the package no longer needs, and OpenCV in the same interpreter
stops importing. An optional backend must never be able to move a required
dependency; install it without its dependencies and let a real ImportError say
what is genuinely absent.

`pytlsd` ships no wheels and builds from source, so DeepLSD also wants cmake and
a C++ compiler. That, and not accuracy, is why it is not the default.

## Licensing

MIT, and it must stay clean. darktable's `ashift.c` is GPL-3.0 and ShiftN's
source is LGPL. Both were **read to understand the algorithms** and neither was
copied. Constants like "assume 28 mm" are facts about the problem, not
expression. See `docs/prior-art.md` for what was taken conceptually and what was
deliberately rejected.
