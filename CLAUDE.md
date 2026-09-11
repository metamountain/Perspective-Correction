# Batch Perspective Correction — working notes

Master / Worker

You are the MASTER. Qwen3.8-27B is the WORKER.

Use Qwen whenever a task can be implemented or investigated locally without requiring architectural decisions.

MASTER responsibilities:

architecture and overall strategy
task decomposition
interfaces and dependencies
system-level debugging
review and integration
final validation

WORKER responsibilities:

implement small, clearly defined changes
modify specific files/functions
write tests
fix localized bugs
perform mechanical refactoring
investigate specific errors

TOKEN OPTIMIZATION:

Never delegate the whole request.
Break work into small, independent work packages.
Give Qwen only the context and files needed for the current task.
Do not make Qwen rediscover the project architecture.
Keep worker responses concise.
Do not delegate architectural decisions or complex cross-module debugging.
Review each worker result before assigning the next task.
Avoid overlapping worker tasks.

For every delegation provide:

GOAL – one concrete objective
SCOPE – exact files/functions
CONSTRAINTS – relevant restrictions
VALIDATION – how to verify the result

The MASTER owns the architecture and final result.
The WORKER executes focused implementation tasks.















## How this file is maintained (standing rule)

**Update this file after every successfully completed feature or problem fix.**
Not "when there is time" — the update is part of the work, and a change that
lands without its note here is not done. The note goes where the subject lives:
the existing section on that topic, or a new one if there is none.

**Wishes are added directly, the moment they are asked for.** A request —
feature, fix, idea, complaint — gets an entry in "Feature requests, and where
each stands" immediately, even while it is still open. The section is the source
of truth; chat history is not, and a wish that exists only in the conversation
is a wish that will be lost.

Statuses used there: *done* (with a pointer to the section that records how),
*half-wired*, *not started*, or a plain description of the current state.

**Work runs to completion without check-ins.** When told to go on until the
project is done, keep implementing -- wiring, flags, UI, tests -- and report
back only when the full test suite has passed *and* every open item in the
feature list is either closed or blocked on a decision that only the user can
make. A half-wired feature is not a reason to stop; it is the next step.

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
file read. `tests/assets/masks` is that cache, 155 KB for twenty-one photographs (four of
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

* **The default is `telea`, and it changed.** It was `none`, on the argument
  that a generated band is content the camera never saw. That argument still
  holds -- a pixel nobody photographed is invented whether a network or a
  fast-marching solver put it there -- so what changed is not the principle but
  which backend can carry a default at all. `telea` needs no model, no
  download, no network and no GPU; it is deterministic, so two runs of the same
  batch on two machines agree; and it costs milliseconds. `lama` and `comfyui`
  stay off, because a batch that silently waits on a 196 MB download or on a
  server that is not running is exactly the failure this project is built
  against.

  What makes it tolerable rather than merely convenient is what surrounds it.
  With `crop="auto"` a correction under about 1.5 degrees is *cropped*, so
  there is no band and the fill never runs. Above that the band is a few per
  cent of frame at the very edge, `--fill-max-share` refuses anything over 35 %,
  and `--fill none` is one flag away. Pinned by
  `test_the_default_fill_is_the_one_that_needs_nothing`, which asserts both the
  value and that it is a backend needing no download.
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

**What ComfyUI receives in the hole is primed, not padded.** Left alone the
band arriving at the sampler is either black or `BORDER_REPLICATE`'s long
straight streaks, and both are bad starting points in opposite ways: black is an
edge a sampler will treat as content, and the streaks are the very artifact that
cost this project two confident wrong conclusions. `_prime_for_generation` runs
TELEA over the hole and then pulls it halfway to mid grey. The TELEA half hands
over the right colours and rough gradient to continue; the grey half destroys
the *structure* TELEA invents alongside them, so nothing in the band reads as an
edge worth preserving. It is a starting point, not an answer -- the generator
replaces it -- and it touches nothing outside the hole.

**Which is why `BPC_MASK` is optional.** A whole family of edit models takes an
image and an instruction and has nowhere to put a mask; for those the priming
*is* the signal, and the prompt ("remove the grey border") does the rest. Only
`BPC_IMAGE` is required. Nothing about BPC's own guarantee rests on the
workflow honouring a mask in any case: `_composite` puts the returned image back
through the hole and nowhere else, so a model that repaints the entire frame
still cannot move a photographed pixel. The mask is uploaded when the graph has
a node for it and quietly skipped when it does not, and `--fill-info` says which
of the two modes it is in rather than calling the second one broken.

Note the priming reads differently depending on what the padding left. Over a
flat grey pad it *adds* variation -- measured on a real corrected frame, the
band's spread went 7.3 to 11.8 as the sky continued into it. Over `edge`
padding's streaks it removes structure instead. Both are the point: what
reaches the sampler is boundary colour with no edges in it, whichever pad
produced the band.

LaMa is the right default backend: no prompt, ~3 s, and it *continues* structure
rather than inventing objects. ComfyUI is there for the wide band and for anyone
who would rather their own Flux graph did it; the workflow is a file
(`workflows/flux2-klein-edit-nomask.json`), the contract is three node titles
(`BPC_IMAGE`, `BPC_MASK`, `BPC_PROMPT`), and the most likely user error -- posting
an editor export instead of an API export to `/prompt` -- is caught with the fix
in the message.

**The batch window now has the address and a "Test connection" button**, because
`comfyui` was selectable there and unconfigurable: the URL and the workflow
existed only as command-line flags, so choosing it in the window could only ever
mean the default port and the shipped workflow, and finding out otherwise meant
running a batch. `inpaint.describe` already answered the question in two
independently readable halves -- *server up, ComfyUI 0.3.x* and *workflow: 12
nodes, sockets [BPC_IMAGE, BPC_MASK]* -- so the button is wiring, not new logic.
A dead port and a live one are both asserted, the second against a stub server.

**The model filenames are resolved, then chosen.** A workflow names three
checkpoints and those names are the first thing that is wrong on somebody
else's machine: the shipped graph says `flux2-klein-9b.safetensors` where a
real install has `flux-2-klein-9b-fp8.safetensors`. ComfyUI answers with three
`Value not in list` errors and ignores the output -- a correct message about
the wrong problem, and one nothing in BPC could have prevented.

Two layers, in this order. `apply_model_choices` writes whatever the user
picked in the window, and `resolve_models` then matches anything still absent
against what `/object_info` reports, by shared filename tokens. A match below
0.34 is left alone: better ComfyUI's own error than a silent swap to the wrong
file. Every substitution is *returned*, never merely done -- it is a guess
about which of forty-six text encoders was meant, and a guess nobody is told
about is how a batch quietly produces something else.

**A filename match can be the wrong model, and nothing in the matcher can
know.** The shipped inpainting workflow named
`mistral_3_small_flux2_fp8_scaled.safetensors`; the server had
`mistral_3_small_flux2_fp8.safetensors`, which is an excellent name match and
the wrong text encoder for Klein 9B -- that wants Qwen3. ComfyUI failed with
`mat1 and mat2 shapes cannot be multiplied (512x15360 and 12288x4096)`, a
text-embedding width, at `txt_in`. It reads like a VAE problem and is not one.
Compatibility is not visible in a filename, so the matcher must never be
trusted silently: that is the whole argument for reporting every substitution
and for the amber light.

**The guess is not enough on its own, which is why there is a selector.** With
sixteen UNETs, forty-six text encoders and twenty-seven VAEs installed -- the
normal case -- two candidates are routinely equally plausible. The three
dropdowns are filled from the server on a successful connection test (the same
round trip that answers "is it there", so it is asked once), default to
"(from the workflow)", and are remembered like any other address. They must
skip nodes titled `BPC_*` and every `LoadImage`: their `image` is a COMBO too,
so an unguarded resolver helpfully rewrites the photograph about to be uploaded
into somebody else's leftover PNG. That bug existed for one commit and is
pinned by `test_the_workflow_is_pointed_at_files_the_server_actually_has`.

**The ComfyUI settings are docked along the bottom of the main window.** They
have been in three places and the first two were both wrong for the same
reason. A row in the options panel: that panel is hidden until a folder is
loaded -- the two-stage window above -- so the server could not be configured
*before* the work, which is the only time anyone wants to, and every route in
lived inside the hidden panel, including the fill selector. Then a `Toplevel`,
which fixed reachability and cost a second window for a tool that is otherwise
one.

`side="bottom"` gets both. The dock claims the bottom strip once, outside
everything `_set_stage` hides and re-packs, so it is on screen with nothing
loaded and stays put when the list fills. `Setup > ComfyUI server...` and the
review window's button no longer *open* anything -- they raise the window and
focus the address field, which is what "let me at the settings" means when the
settings are already visible. Choosing `comfyui` in a fill selector does the
same and tests immediately: a mode that silently needs six settings nobody was
shown is the quiet failure this file keeps arguing against.

The dock owns no state. Host, port, workflow and the three model choices are
`StringVar`s on the App, so `_settings()` reads them whether or not anything is
drawn, and `_comfy_open()` -- which used to ask "is the dialog up" -- now only
asks whether `_build` has run yet.

**Two workflows shipped, and the one that ran was whichever nobody chose.**
`--comfy-workflow` defaulted to `flux-klein-outpaint.json` and the window's
label read "shipped workflow", singular, while two of them shipped for two
*incompatible* model shapes. So a FLUX.2 [klein] **edit** model -- picked
explicitly in the model selector, present on the server under exactly that
name -- was fed through an `InpaintModelConditioning` + `KSampler` graph. The
band came back wrong and **the light was green**, correctly: every checkpoint
the workflow named was installed. Nothing was missing. The wrong graph was
running.

**The inpainting graph is now deleted, not demoted**, so only the edit-model
one ships and the default is at least the right *shape*. That does not retire
the naming: `--comfy-workflow` takes any file, and an indicator that says
"connected" without saying to what is the same failure waiting on a graph
nobody here wrote. The cost is that `BPC_MASK` -- still uploaded whenever a
graph has the node -- has no shipped example left, so
`test_a_masked_workflow_still_gets_its_mask` builds one; a branch no bundled
file exercises is one nobody notices breaking.

This is the same shape as the text-encoder failure above and one level up from
it. There the guess was about *which file*; here it was about *which graph*,
and the four-state light said nothing because none of its four states is about
the workflow. The fix is not a fifth colour -- the default is a graph that
runs, so red would be wrong, and `models` is labelled "models missing", which
would be a correct colour on the wrong problem. **The fix is that every state
names the file and says whether anybody chose it**, and that the window offers
the two by shape rather than a "choose..." button onto a folder. Pinned by
`test_the_indicator_names_the_workflow_and_says_when_nobody_chose_it`.

The general rule, which this project keeps re-learning: *a default that is
invisible is a decision nobody made.* `--fill telea` is a defensible default
because it is named, deterministic and cheap. An unnamed choice between two
mutually exclusive graphs is not a default, it is a coin toss with a green
light on it.

**And the contract is not optional just because the graph works.** A user's own
API export ran perfectly by hand and BPC refused it: no `BPC_IMAGE`. That
refusal is right -- on a graph with two `LoadImage` nodes a guess would be
silent and wrong half the time -- but the message has to name the file, because
"the workflow has no node titled BPC_IMAGE" reads as a bug in the shipped one.

**The review window could select `comfyui` and configure nothing.** Every
control -- address, light, workflow, the three model selectors -- lived on the
batch window only, so picking the mode in a review window meant the default
address and the unnamed default workflow, silently. It now opens the same
dialog (there is one server) and listens for the same verdict rather than
polling for it, and `_sync_comfy` re-reads the App's settings at save time --
without that, choosing a workflow while a review is open would move the light
and not the file, which is worse than not offering the control at all.

**The light has four states, and the last two are the point.** `down` (red):
nothing will run -- no answer, or a workflow `/prompt` cannot take. `ok`
(green): every model name resolves as written. `models` (amber): the server
answered and the graph is sound, but the checkpoints it names are not the ones
installed, so a *guess* is in force. `unknown` (grey, "not checked"): nobody has
asked yet, or the address or workflow changed since anyone did -- red there
would be a claim, and a wrong one. Two states would have to fold that into one
of the others and both readings are wrong -- green hides the guess, red refuses
something that works. The judgement lives in `inpaint.status`, not the window,
so it is asserted without a display.

**Host and port are two fields, and the verdict expires.** The port is the half
that actually gets changed -- a second instance, a tunnel, a container -- and
hunting for it inside a URL is how it gets mistyped. Only the joined form is
ever stored, so `inpaint.split_url` / `join_url` have to round-trip anything a
user might type and tolerate what they are halfway through typing; they live in
`inpaint.py` rather than the window because they are pure, which is the same
rule that keeps `review.py` free of Tkinter. Beside them a label reads
*connected* / *disconnected* / *not checked*, and **editing the address or the
workflow puts it back to "not checked"** -- a green light next to a port nobody
has asked yet is an answer to a question that is no longer on screen.

Two things it must not do. It must not run on the UI thread: `describe` makes
two network round trips at three seconds each, and a window frozen mid-click
reads as a crash rather than as a slow server. And it must not post its result
with `after()` from the worker -- Tk is not thread-safe and that raises "main
thread is not in main loop" outright. It goes through `self.queue`, the same
one the batch run already uses.

`comfy_url` and `comfy_workflow` joined `--remember`. They are **addresses**,
like the checkpoint and the mask folder -- where the generator lives, not how
hard to correct -- so they do not breach the rule that correction parameters
never persist. `comfy_url` needs a different test from the others in
`apply_prefs`, because it has a real default rather than an empty one and
"unset" cannot be read as falsy. Pinned by
`test_the_comfyui_address_is_remembered_but_the_correction_is_not`.

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

## Going through a folder by hand, one photograph at a time

`Review each...` is the other way through a selection: no unattended writing at
all, one panel load per photograph, each becoming a file only when Save is pressed.
A batch run decides; this asks. It cannot produce a single output nobody looked
at, which makes it the right mode for a folder that matters and the wrong one
for four hundred holiday snaps.

**Chained, never looped.** Tk has one event loop, so a `for` around a blocking
load either stalls it or queues thirty photographs at once. Each load's
`on_closed` opens the next -- and it fires however the review went away, saved,
kept, or closed with Close, because a queue that only advances on Save stalls
forever on the first photograph someone dismisses. The next load happens
through `after(50, ...)` rather than inline, since `on_closed` runs while the
panel is still being rebuilt.

**A saved photograph leaves the list.** What is left is then exactly what is
left to do, which is the only reading of a list that survives being interrupted.

**Overwriting is decided per photograph and confirmed per photograph.** The
batch checkbox seeds the panel's, but replacing an original is the one
action here nothing undoes, and in a queue of thirty the checkbox was ticked
long before this particular picture came up. So the panel asks, every time,
naming the file. `_dest_corr` exists because `_dest` folds the overwrite
decision into the path -- right for an unattended run, wrong where the choice
is per image and the panel needs both candidates.

## The window opens on one thing, because there is one thing to do

With nothing loaded, a screen of sliders, an empty results table and a disabled
Start button do not help anyone start -- they bury the drop target, which is
the only control that matters yet. So the empty window *is* the drop target, at
four times the height, plus the buttons that do the same for anyone who would
rather browse. Everything else appears when there is something to work on and
goes away again on "Clear".

Nothing is destroyed and rebuilt: `_set_stage` only hides. The widgets keep
their state, so an output folder chosen, a detector picked or a ComfyUI address
typed survives emptying the list. The two packed sections (options, bar) have
to be re-packed in order, because `pack` appends and they would otherwise
surface above the frame they belong under; the results tree is re-added to the
paned split rather than re-packed.

The review panel sits directly under the compact loader -- the photograph under
the cursor is reviewed in the frame you are looking at, not in a second window.
The window opens at 1920x1080, the most common desktop resolution (Statista
2025), clamped to smaller screens, and maximized so a larger monitor gets the
whole frame; F11 toggles borderless fullscreen for a long review session.
Review and results share the remaining height in a draggable `PanedWindow`
split (review starts with the larger share): the sash means nobody is stuck
with whatever ratio the code picked.

## The split is not a ratio, because the two panes want different things

"The layout is not optimized; the GUI should match the screen -- as big as
useful." Measured on a maximized window at 2560x1440, the complaint was exact:

| | px | share of window |
|---|---|---|
| chrome (header, loader, options 128, bar 47) | 424 | 31 % |
| results tree | 267 | 20 % |
| review controls (8 rows) | 436 | 32 % |
| **the image canvas** | **265** | **20 %** |

A fifth of a 1440p screen for the only thing anyone is looking at, and a
results list showing *one row* given more height than the photograph.

**The cause was a proportional split, and proportions are the wrong instrument
here.** `weight=3`/`weight=2` divides a paned window by ratio -- so the tree's
share grows with the monitor. But **a results list's need is absolute and a
photograph's is not**: six visible rows is six visible rows at any resolution,
while every pixel given to a picture being judged by eye is another pixel of
usable detail. Splitting by ratio therefore gets steadily *worse* as the screen
gets bigger, which is the opposite of "as big as useful".

So `layout.py` gives the tree what its rows ask for -- bounded below at two
rows and above at six, and never more than 32 % of the pane -- and hands the
preview everything else. The pane weights say the same thing for resizes
afterwards: review `weight=1`, tree `weight=0`, so extra height has nowhere to
go but the picture. Measured, loaded, one file:

| window (simulated, not maximized) | tree | canvas |
|---|---|---|
| 1366x768 | 76 px | 228 px |
| 1920x1080 | 76 px | 265 px |
| 2560x1440 | **76 px** | **411 px** |

The tree is the *same* height on all three and every pixel the bigger screen
adds reaches the image.

**Mind which probe produced a number before quoting one against another.** An
earlier draft of this table read "411 px (was 265)", which compares a window
*simulated* at a full 2560x1440 against the earlier measurement of a genuinely
*maximized* window -- 2560x1351, because the taskbar takes 89 px. Same machine,
two different windows. The like-for-like maximized figures are **265 -> 361 px**
of canvas (review pane 653 -> 845); the 228/265/411 row set is internally
consistent but only among itself. It is a small instance of the shape this file
keeps recording: a measurement that flatters the change is the one to re-check. That is the assertion, not the anecdote:
`test_a_bigger_screen_goes_to_the_photograph` pins it as arithmetic and
`test_the_results_list_keeps_its_rows_and_the_preview_takes_the_rest` pins it
on the real widgets.

**`weight` does not place the initial sash**, which is the part that cost the
most time. It distributes *surplus* on resize; where the sash first lands comes
from the panes' requested sizes, and nothing ever called `sashpos`. Setting it
once from `after_idle` was not enough either -- `_set_stage` packs the options
row and the bar *after* adding the tree, so the first measurement saw the empty
stage's taller pane and put the sash past the loaded window's height, which
collapsed the tree to **one pixel**. It follows `<Configure>` instead, so the
split is right after the stage settles, after a resize, and after a move to
another monitor -- and it stops permanently the moment the user drags the sash,
because a window that keeps re-deciding a split someone just set by hand is
worse than one that never helped.

**The restore-down size follows the screen too.** It was a hardcoded
1920x1080 -- too small on a 1440p or 4K monitor, and larger than the display in
both directions on a 1366x768 laptop. `layout.initial_window` takes a share of
the real screen and clamps it both ways.

**What is still wrong, and was not touched.** The review panel's eight control
rows take **436 px -- more than the image gets**, and the canvas is
*height*-limited while roughly half its width goes unused (a 3:2 photograph in
a 1263x361 box paints about 540 px wide). Height is the scarce resource in this
window and the controls are where it is. Fixing that means restructuring the
review panel, which `skills/ui.md` forbids on purpose ("add, don't
restructure"), so it is recorded as the next move rather than taken: collapse
or tab the control rows, or shrink the 84 px status box until something fails.

**`layout.py` has no Tkinter import**, for the same reason `review.py` does
not: a layout rule that can only be checked by looking at a screen is a layout
rule nobody checks. Screen size in, pixels out, tested at five resolutions.

**Windows DPI awareness is deliberately not done.** The process is DPI-unaware
(`GetProcessDpiAwareness` -> 0), so at any display scaling other than 100 %
Windows bitmap-stretches the whole window. This machine runs at 100 % -- Tk
reports 2560x1440 at 96 dpi and `GetSystemMetrics` agrees -- so the change
would be a **no-op here and unverified everywhere else**, which is exactly the
kind of default-path edit the rest of this file argues against shipping
unmeasured. It is also all-or-nothing: making the process aware means point
sizes scale and raw pixels do not, so `rowheight=24` and every pixel pad would
shrink against the text at 150 %. Half the change is worse than none. Re-open
it on a machine that actually runs scaled.

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

## Feature requests, and where each stands

Recorded here because this file is the source of truth; chat history and any
`debug.md` are not. Nine were asked for, in this order.

**DeepLSD as a second front end -- done.** See "DeepLSD: judgement, not geometry"
above. It is wired as `--detector deeplsd`, with the `deep-hybrid` / `deep-union`
pair, a GUI dropdown, and a lazy torch import so the default path never pays for
it. Nothing left to do.

**Fill the band the rotation opens up -- done.** See "Generating the band the
rotation opens up" above. The request was "when the corrected frame is bigger
than the original, fill the empty corners"; the answer is `--fill` with
`none / telea / lama / comfyui`, default `telea`. LaMa for a band a few percent
wide, ComfyUI (differential diffusion) when it is wide or wants a plausible sky.
Nothing left to do.

**A dependency check before the run -- done.** The tool already degraded gracefully and named the missing piece per backend; what was missing was a *gate* that runs before any file is touched. `deps.py` provides it: a core check (numpy, cv2 and Pillow hard; piexif soft, because `imageio.py` already falls back to `default_focal_35mm` without it), a `--doctor` report that reuses each backend's existing `describe()`, and a pre-flight in `main()` that hard-fails only when a backend is *explicitly requested* but absent. Requested-but-missing fails loud before any file is opened; left at the default (`auto` / `none`) it stays graceful, so a batch still runs on a machine where the user never asked for the heavy backends. Six tests in `tests/test_deps.py`; `--doctor` exits 2 only when a required package is missing.

**Frontal / planar correction -- wired end to end, and it had never once
worked.** This entry was wrong in *both* directions at the same time, and the
pair is the lesson. It said "core done" while `planar.py` could not complete a
single call -- `test_planar.py` was missing from `MODULES`, so nothing ever ran
it, and `cv2.getPerspectiveTransform` rejected every float64 quad it was handed.
It said "GUI shell not started" while `gui.py` in fact carried the whole shell:
`v_planar`, `_on_planar_toggle`, `_on_planar_drag`, `_on_planar_release`, and
`review.py` carried the pure layer beneath it -- `set_planar_point`,
`pick_planar_corner`, `planar_homography`, `planar_rectified`, `save_planar`.

So a user could tick the box, click four corners, and reach
`planar_homography` -> `transform_for` -> an exception, on every photograph,
for as long as the feature has existed. **Two stale claims cancelled out into a
feature nobody noticed was broken**: the half that said "not started" explained
away the absence of any working behaviour, and the half that said "core done"
explained away the absence of a test. Neither was checked against the code.

The dtype bug and a wrong assertion are fixed and the seven `test_planar` tests
now run -- see "Testing". **What is still genuinely missing is coverage of the
layer in between.** `test_planar.py` exercises `planar.py`'s math only; the
seven `ReviewSession.planar_*` methods the GUI actually calls have no test at
all, which is exactly the gap that let this survive. They are pure functions of
state, like everything else in `review.py`, so they are headlessly testable and
there is no excuse. That is the next job on this feature, ahead of any polish.

The math
lives in `planar.py`: a general homography (eight degrees of freedom) from four
clicked corners -- exact for a planar correspondence, no focal length, no RANSAC,
no guess. `target_size` follows the document-scanner convention (the longer of
the top/bottom edges is the width), and `transform_for` raises on a degenerate
quad rather than returning a homography that would warp the image into a sliver.
The output canvas is exactly covered by the warped quad, so there is no fill
band -- what separates planar rectification from the rotation path. Automatic
routing was considered and dropped on purpose: a shot with strong perspective
distortion is exactly the case where line geometry cannot decide for you (the
focal length is under-determined by lines alone), so "this looks like an oblique
facade" is not a reliable trigger, and guessing wrong warps a good photo into a
sliver; when in doubt the rotation path stays in charge. There is still no CLI
flag -- planar is a manual, one-photograph-at-a-time mode by design, so the
review panel is the right and only home for it. The learned-focal-prior idea
(see "Known weakness") still stands as the other half of the frontal case;
planar is the manual answer to it.

**Horizontal (yaw) de-convergence -- estimator done, policy wrong, needs a new
approach. Off by default and staying off.**

The geometry is right and is wired end to end. Yaw cannot be read off the
vertical vanishing point -- a yaw is a rotation about the world vertical and
leaves it fixed -- so it comes from the dominant horizontal one instead: level
the frame with (roll, pitch), and the angle that remaining horizontal direction
makes with the image x-axis is the yaw. Gated on `min_horizontal_support` (0.3)
and folded to [-90, 90], because a line has no direction and the two antipodal
readings differ by a half-turn. `warp.limit` scales it by `horizontal_strength`
and caps it tighter than pitch (`max_horizontal_deg`, 8.0); past the cap the
whole correction clamps into the existing refuse-beyond-limit path. CLI:
`--horizontal` (off), `--horizontal-strength`, `--max-horizontal`. Review panel:
a "horizontal (yaw)" checkbox plus a +/-15 deg slider, disabled unless enabled;
toggling it on in AUTO mode calls `session.refit()`, because the estimator only
computes a yaw when the flag was already on at estimation time. Recovery is
under 1 deg worst over -12 to +10 deg synthetic scenes.

**It was briefly switched on by default with the cap widened to 90 deg, and that
is the mistake this file exists to prevent.** The argument in the config comment
was that leaving the horizontals converging "reads as not corrected". Measured
over the seventeen real assets with it on:

| | yaw | conf |
|---|---|---|
| burgebrach scheune | **+57.7 deg** | 0.53 |
| ulica Machcowskiego | **-67.5 deg** | 0.46 |
| wilsdruff scheunen | **+70.1 deg** | 0.33 |
| heilsbronn | +44.0 deg | 0.48 |
| quaker barn | +27.7 deg | **0.75** |
| camden (genuinely frontal) | -0.05 deg | 0.46 |

Fourteen of seventeen asked for more than 25 deg of yaw, most of them at a
confidence the 0.40 gate admits. One photograph -- the hospital -- then opened a
band 43 % of the frame and failed the write outright, which is how it was found.

**Those are not errors the estimator made.** Converging horizontals *are*
correct perspective on an obliquely photographed facade; the estimator is
faithfully measuring facade obliquity. Squaring it up is *frontalisation*, not
levelling, which is precisely what the planar section rejects as an automatic
route by name -- "guessing wrong warps a good photo into a sliver". So
confidence cannot catch this and is not built to, exactly as with the Prague
ceiling: every factor it scores measures how well the lines agree, never whether
they are the right lines.

**With the cap back at 8 deg, turning the flag on does *less* than leaving it
off, and that is the first thing the next person will hit.** `warp.limit`
returns one `clamped` flag for all three axes, and "beyond the limit means
refuse, not trim" then drops the whole correction. Measured:

```
        bpc -n burgebrach-...jpg
OK       roll=-0.08deg pitch=+6.27deg conf=0.53 keep=100%
        bpc -n --horizontal burgebrach-...jpg
SKIPPED  correction beyond the limit (roll -0.1, pitch +6.3, yaw +57.4; caps 12/20/8)
```

The good 6.3 deg of pitch is lost because of a yaw nobody needed. **Whether
that is right is an open question and part of the redesign**, not something to
patch: the refuse-beyond-limit rule was written for the Prague ceiling, where a
huge *pitch* is evidence the estimator locked onto the wrong line bundle, so the
whole fit is suspect. A huge *yaw* says only that the facade is oblique, which
is no evidence at all about roll and pitch. So the two plausible answers are
"refuse, as today" and "drop the yaw, keep the levelling" -- and picking the
second needs the plane question below answered first, because a yaw that is
silently dropped is a flag that appears to do nothing.

**The open design problem is which plane, and who chooses it.** A photograph
routinely shows two facades. Yaw makes *one* of them fronto-parallel and
necessarily makes the other worse, so there is no answer the geometry can give
on its own. Candidate routes, none decided:

* pick the dominant horizontal cluster only, and refuse when the second cluster
  is within some margin of the first -- cheap, and refuses the corner view
  instead of guessing at it;
* let the user say which facade, by dragging a band over it. **The seam for this
  already exists and is half-wired**: `pipeline.analyse(..., roi_x=(x0, x1))`
  and `review.set_roi_x / clear_roi_x` restrict the *horizontal* evidence to a
  vertical strip in full-resolution pixels (verticals stay global, and a strip
  holding no horizontals falls back to the full frame). There is no CLI flag and
  no GUI drag to drive it -- the config comment claiming `--roi-x` exists was
  wrong. `Result.roi_x` is carried into the log line;
* or a mask, the same way `--mask` already names a region.

Until one of those is measured, it stays off. It is a special case, not a
default, and `test_yaw_is_zero_when_horizontal_correction_is_off` is what pins
the default itself -- it builds a bare `Settings()`, so it is the test that
fails if anyone flips it again. It is how this was caught.

**One window, not two -- done.** The review used to open as a `Toplevel` per
photograph; it now lives embedded in the batch window as `ReviewPanel`, under
the compact loader, and `load()` swaps the photograph into the same widgets.
The old X button is a Close button; Save/Keep no longer destroy anything. The
error status shows the full traceback and appends it to `bpc_errors.log` at the
project root, because a Tk status box is not reliably copyable and a truncated
traceback hides the frame that actually failed.

**1080p standard, fullscreen, flex -- done, and the split is no longer a
ratio.** The window opens maximized; F11 toggles borderless fullscreen (View
menu). Review and results share a draggable `PanedWindow`, and what changed is
how it is divided. See "The split is not a ratio" below.

**Download assistance -- done.** Setup > "Download model files..." fetches the
DeepLSD weights (98 MB) into `models/` via `deeplsd.download_weights`; the menu
label is the progress bar, a second click while busy is refused, and a complete
file is returned as-is. M-LSD ships in `models/`; BiRefNet weights are chosen by
hand, so there is nothing else to download.

**UI skills doc -- done.** `skills/ui.md`: the INK palette as the single colour
source, the one-window structure, review-panel rules, and the off-screen test
pattern for GUI changes.

**ComfyUI settings in a compact popup -- done.** The ComfyUI controls live in
a small withdrawn `Toplevel` (`_build_comfy_popup`), built once at startup and
hidden until opened; close hides rather than destroys, so the verdict and model
lists survive a round trip. The StringVars stay on the App, so `_comfy_open`,
`_show_comfy_state`, `_fill_model_lists` and the queue path are untouched.
This work arrived tangled in a bad merge that left **two `class App`
definitions** in `gui.py`: the second shadowed the first and called
`_build_comfy_popup()`, a method only the first had, so the window died at
startup with an `AttributeError`. The duplicate is gone -- one class, one popup.

**Preview on selection -- done.** `self.lst` binds `<<ListboxSelect>>` to
`_on_list_select`, which loads the selected photograph into `ReviewPanel`,
guarded by `session.path` so a re-click never re-runs detection. One catch that
took a probe to find: **Tk 8.6 does not fire `<<ListboxSelect>>` for a
programmatic `selection_set`** -- only real user clicks do (verified with a
minimal repro). So `_add` and `_refresh_items` call `_on_list_select()` by hand
after their programmatic selections; the guard makes a repeat a no-op. Adding
files previews the first new one, and saving a photograph advances to the next.

**Clean up the repository, fix the structure, improve the setup -- done.**
Recorded here because it was a wish like any other. What it came to:

*Cleanup.* Fourteen scratch files sat in the working tree -- six `_probe_*.py`,
`_gui_merge_test.py`, `_layout_test.py`, `_smoke_preview.py`, and five captured
logs including `full_suite.log` and `tools/mask_bench.log`. They are **moved to
`analysis/scratch/`, not deleted**: they were untracked, so git could not have
brought them back, and everything they discovered is already written into this
file (the Tk `<<ListboxSelect>>` finding among it). `.gitignore` now catches the
shape at the root -- `/_*.py`, `/_*.log`, `/_*.txt`, `*.log` -- so the next
probe cannot reach a commit.

*`analysis/README.md` is now the one tracked file in that folder.* The rule was
`analysis/`, and **git does not descend into an excluded directory**, so a
`!analysis/README.md` negation under it does nothing at all. It takes
`analysis/*` for the negation to be reachable. A fresh clone otherwise gets no
explanation of a folder the docs tell people to write into.

*Structure.* The `MODULES` guard above, and the dead `planar.py` it found.

*Setup.* `pyproject.toml` grew `[project.optional-dependencies]` --
`gui` / `mlsd` / `deeplsd` -- and the platform markers `requirements.txt`
already carried (headless OpenCV off Windows), which its `dependencies` had
silently dropped. CI installs `-r requirements.txt` instead of a hand-written
pip list that could drift from it, and runs `--doctor` as a gate before the
suite.

**There is deliberately no `lama` extra, and a test enforces it.**
`simple-lama-inpainting` must go in with `--no-deps`; an extra resolves
dependencies normally, so `pip install .[lama]` would be a one-command way to
do exactly the Pillow-9.5/numpy-1.26 damage the manual step exists to avoid --
a regression that reads as a convenience, which is why it needs a test and not
a comment. `test_no_extra_can_install_a_backend_that_breaks_the_core` also
bans `ultralytics`, the other package with a history of moving a required one.
`test_the_declared_dependencies_are_the_ones_the_core_check_requires` pins
pyproject against `deps.core_status`, since those are two statements of one
fact and drift between them is quiet in both directions.

*Left untracked, but not scratch.* `tests/assets/Horizontal/` -- seven
photographs, 3.6 MB -- **is a deliberate asset folder for testing horizontal
(yaw) correction**, which is the open feature two entries above. It was
initially mistaken here for stray files; it is not, and it should not be
deleted.

It is still **untracked**, for one reason that has nothing to do with its
purpose: filenames like `017d2b2a-...-2008695636.jpg` and
`Webseitentitel_1.080x675.png` carry no provenance, every other asset here
arrived through `tools/fetch_commons_asset.py`, and the licensing section is
explicit that this repo stays MIT-clean. Committing them is a licensing
decision for the author to make, not a cleanup one.

**Nothing reads the folder yet either way.** `test_assets` globs `assets/*`
and filters by extension, so it never descends into a subfolder -- the photos
sit there inert until the yaw work grows a test that opens them. When it does,
that test is the natural home for the "which facade" question the
plane-selection wish has to answer, because these are the photographs it will
be answered on.

**A transparent grid over the corrected pane -- asked for, not started.** The
question a reviewer actually has is "is it straight *now*", and the eye is bad
at judging verticality against nothing. A grid answers it directly, and it is
the rare request here that **cannot ruin a photograph**: read-only, invents no
pixel, changes no estimate, and is one toggle away from gone. That makes it the
right thing to build first among the open wishes.

Three things it has to get right, all of them already settled by existing
decisions elsewhere in this file:

* **It goes on the *after* pane.** A grid over the original shows only that the
  original was crooked, which nobody doubted. The corrected pane is where the
  claim is being made.
* **It is a canvas overlay, not a composite into the image.** Same reason
  `_refresh_crop` redraws the overlay only and never calls `_schedule_redraw`:
  compositing would mean a re-warp and a live `telea` fill of a
  pixel-identical frame on every toggle, which is its own kind of jump.
* **It must never reach the saved file.** It is a measuring instrument, not a
  correction. The crop rectangle already establishes the pattern -- shaded in
  the preview, applied only in `save()`; the grid is the case that is applied
  *nowhere*.

Open: spacing (a fixed division of the frame is scale-independent and
defensible; pixel spacing is not), and whether to offer thirds for composition
as well as a dense grid for verticality. A centre cross costs nothing and is
probably the most useful single line.

**Manual selection of the horizontal plane -- asked for, and it settles an open
design question rather than adding a feature.** The yaw entry above ends by
naming three candidate routes for "which facade should be made
fronto-parallel", and says none is decided. This request picks the second:
**let the user say, by dragging a band over the facade they mean.**

**The seam is already written and is half-wired.** `pipeline.analyse(...,
roi_x=(x0, x1))` and `review.set_roi_x` / `clear_roi_x` restrict the
*horizontal* evidence to a vertical strip in full-resolution pixels; verticals
stay global, and a strip holding no horizontals falls back to the full frame.
`Result.roi_x` is already carried into the log line. What is missing is a drag
in the GUI to drive it -- and **a test, because `roi_x` currently has none at
all**, which is precisely the state `planar.py` was in.

**It cannot be built alone, and the ordering matters.** Turning `--horizontal`
on today does *less* than leaving it off: `warp.limit` returns one `clamped`
flag for all three axes, so a yaw past the 8 deg cap drops the whole correction
through refuse-beyond-limit, losing a good 6.3 deg of pitch over a yaw nobody
asked for. A band selector feeding a yaw that then gets the photograph refused
is a control that appears to do nothing -- the exact failure the always-live
crop section was written about. So the refuse-vs-drop-the-yaw question has to
be answered first, and this file says picking "drop the yaw, keep the
levelling" needs the plane question settled. **This wish is what settles it**,
which is why the two are one job and not two:

1. decide refuse-vs-drop-the-yaw, now that a person rather than the geometry
   names the plane;
2. test the `roi_x` seam headlessly;
3. then the drag, and only then is `--horizontal` worth turning on for anyone.

## Conventions that are correct as written

- **`H = K R K^-1`, always.** A pure camera rotation: three degrees of freedom,
  all physical. Yaw is now fed into the warp as the third angle
  (`correction_rotation(roll, pitch, yaw)` = `Ry(-yaw) Rx(pitch) Rz(-roll)`), but
  it is **off by default**, so an ordinary run is still the two-angle rotation
  and reproduces the old homography exactly -- pinned by
  `test_zero_yaw_builds_the_same_homography_as_before`. It *cannot* shear. The reference built a
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

`--remember` stores **addresses only** (checkpoint, mask folder, output, focal
length, ComfyUI URL, workflow, and the three model files chosen for it). Correction parameters are deliberately not
remembered: a setting that
silently persists between runs is one nobody can reason about, and a batch must
stay reproducible from its command line. Unknown keys are dropped on load so the
file cannot become a second, hidden place where behaviour is configured.

## Testing

`python tests/run_tests.py` -- 181 tests, standalone, no pytest (some skip at
runtime depending on assets and backends), 113 s. The modules are listed explicitly in
`run_tests.py`, so a new test file that is not in `MODULES` runs nowhere and is
worse than no test at all.

**That rule is now enforced rather than written down, and enforcing it found a
dead module.** `_unlisted()` compares `test_*.py` on disk against `MODULES` and
fails the run naming anything missing, because the old failure mode was silent
in the worst way: the file exists, it reads as covered, and it has never once
executed. `test_planar.py` was in exactly that state -- seven tests, never run,
and **three of them failed the moment they were wired in**:

* `cv2.getPerspectiveTransform` asserts `CV_32F` on its inputs and
  `planar.homography_from_quad` passed float64, so *every* call raised. The
  module was dead on arrival -- and the assertion is long-standing, so it had
  most likely never worked on any OpenCV this project supports, while this file
  recorded it as "core done". That is the MODULES lesson at full strength: not
  a version that got stricter, but code that had never once executed. The shape check still
  happens in float64 and only the call is narrowed -- the solve is double
  internally, `H` comes back float64, and a pixel coordinate needs three of
  float32's seven digits.
* `test_full_coverage_no_fill_band` asserted the wrong algebra: it pushed the
  *output* canvas corners through the forward `H` and expected them to land on
  *source* quad corners, which holds only if `H` is its own inverse. It now
  sends them through `H^-1`, which is how `warpPerspective` actually resamples.

The lesson is the one this file keeps recording in other forms: a test that
cannot run is worse than an absent one, because absence is visible.

The former known red is fixed: `Aulendorf_Schloss_Fassade.png` now has a cached
mask in `tests/assets/masks/`, so the mask-cache test passes. The standing skips
are the documented ones -- M-LSD without a TFLite runtime, and the two
`*_upright.*` / `*_skip.*` asset tests while those assets are absent.

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
away.** There are twenty-one masks and seventeen photographs: `painted-hall`,
`prague-main-railway-station-ceiling`, `tiled-skyscraper-facade` and
`warsaw-d3200-27mm_upright` have a cached mask and no image. So
`test_files_marked_upright_are_left_alone` and `test_files_marked_skip_are_refused`
both skip for want of assets -- including the Prague ceiling, which is the
photograph the whole "beyond the limit means refuse" section is built on.
`tools/fetch_commons_asset.py` is how the others arrived.

**The suite was shortened by memoizing, not by deleting.** Two thirds of the
runtime was the real-asset sweeps, and the largest single item in it was pure
duplicate work: `test_a_known_rotation_is_recovered_on_real_photographs` and
`test_every_photograph_it_is_confident_about_is_measured_accurately` both call
`_round_trip_error(f)` over the same seventeen photographs with the same default
arguments. `_round_trip_error` and `_load` now cache on their **full** argument
tuple, and the second sweep went **20.3 s -> 2.8 s** (suite 129 s -> 113 s) with
every assertion and threshold untouched.

The key has to be the full tuple, and that is the whole trap.
`test_the_border_guard_is_what_makes_the_measurement_honest` deliberately calls
the same file twice, guarded and with `inner=0.0`, and asserts the second is
three times worse. A cache keyed on the path alone would hand it the same number
twice and the test would pass while measuring nothing -- the border artifact
story in this file, repeated as a test bug. A cached `None` is also a real
result, so the hit check is `key in cache`, never a truthiness test.

The remaining big item, `test_every_asset_is_processed_without_error` at 26 s,
is genuinely end to end (it writes files) and shares nothing with the round-trip
path. It stays as it is.

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
piexif, and nothing else. `pip install -e .` does the same and adds the `bpc`
command. Tkinter is needed only for the GUI and ships with the
python.org Windows installer; the CLI runs without it. OpenCV's LSD was dropped
in 4.1 and restored in 4.8, hence the detector fallback chain in `lines.py`.

Three of the optional backends are also extras -- `pip install -e ".[gui]"`,
`".[mlsd]"`, `".[deeplsd]"`. The other two are **not**, and must not become
extras: `--fill lama` needs `--no-deps` (see the feature-list entry on the
cleanup), and `--fill comfyui` needs no package at all, only a running server.

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
