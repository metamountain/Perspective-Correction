# Masking: keeping the fit on the building

Trees, sky, cars and people contribute lines that are not part of any building.
A bare winter tree is the worst case: its twigs are near-vertical, long, and
plentiful, so they land squarely in the vertical candidate pool.

## The one measurement that decides how to use the *cheap* mask

40 synthetic scenes with trees occluding the facade. This table is about
`--mask auto`; `--mask birefnet` does not share its failure and has its own
numbers below.

| | pitch mean | pitch max | roll max |
|---|---|---|---|
| focal length **known**, mask off | 0.30 deg | 2.84 deg | 0.356 deg |
| focal length **known**, mask on | **0.23 deg** | **0.93 deg** | **0.190 deg** |
| focal length **unknown**, mask off | **1.51 deg** | **5.58 deg** | 0.469 deg |
| focal length **unknown**, mask on | 1.82 deg | 10.05 deg | 0.170 deg |

**The cheap mask pays off only when the focal length is known.** It improves
the geometry -- it removes exactly the outliers that hurt most -- but it also
removes *evidence*, and the focal length estimator is the part most starved for
it. Masking a stripped web JPEG with `auto` can nearly double the worst-case
pitch error.

So `--mask auto` belongs with `--focal-35mm`, or with photos that carry EXIF. On
a folder of unknown-lens web JPEGs, leave it off. That is why the default is
`off` rather than `auto`.

The reason `auto` breaks this way is that it is a texture statistic: it removes
green, chaotic and sky-like *pixels*, which on a stripped JPEG takes horizontals
the focal estimate needed. A segmenter that removes whole non-building *objects*
does not, which is why the numbers further down look different -- BiRefNet helps
*more* where the focal length is unknown (1.12 deg / 2.25 worst becomes
**0.88 / 1.96**) than where it is known (0.70 / 1.68 -> **0.65 / 1.36**).

Masking the vertical pool only, keeping every horizontal line for the focal
estimate, was tried as a way to have both: it gained essentially nothing
(pitch max 3.00 vs 2.84 with masking off) and was removed rather than kept as a
third mode nobody could choose between.

## The cheap mask, no model required

    python rectify.py "D:\Fotos" --mask auto --focal-35mm 24

`masks.vegetation_and_sky` uses three cues and needs no download:

* **excess green** -- foliage in leaf;
* **low structure-tensor coherence over busy pixels, but only near vegetation**
  -- a canopy has no dominant local direction. This cue used to stand alone, and
  drawing the mask on screen showed why that was wrong: coherence asks whether
  *one* direction dominates, and a half-timbered facade has *two*, so the beam
  grid scored as foliage. It was masking **8.2 %** of a real Fachwerk barn's
  facade -- excluding the building from its own measurement. Requiring greenness
  nearby costs nothing measurable (pitch mean 0.28 to 0.31 deg on the occluder
  benchmark, identical worst case) and drops the false masking to **1.5 %**. The
  price is a bare winter tree, which is not green and is now only partly caught;
  that case belongs to `--mask file`;
* **bright, unsaturated or blue regions connected to the top edge** -- sky, as
  opposed to a white wall, which is not connected to the top.

## Look at the mask before you trust it

`--debug-dir` tints the excluded region and draws the lines it removed in red,
and the GUI review window has a **show mask** toggle. Both exist because the
Fachwerk failure above was invisible in the numbers -- the only symptom was a
slightly lower confidence -- and obvious the instant the mask was drawn.

## BiRefNet, built in

    python rectify.py "D:\Fotos" --mask birefnet --birefnet-model auto --focal-35mm 24

BiRefNet is a *dichotomous* segmenter: one high-resolution matte separating the
salient object from everything else. On an architectural photograph the salient
object is the building, so the mask is the whole of it —

    ignore := foreground < 0.5

— with no scoring criterion in between and nothing for the line detector to
adjudicate. Round-trip on the six shipped assets, focal length fixed at 24 mm in
both passes so the metric stays self-consistent:

| | mean | worst |
|---|---|---|
| mask off | 0.70° | 1.68° |
| **BiRefNet-HR** | **0.65°** | **1.36°** |

and with the focal length *unknown*, the regime a stripped web JPEG puts you in,
1.12°/2.25° becomes **0.88°/1.96°** — a *larger* relative gain, which is exactly
where it parts company with `--mask auto`.

> All numbers on this page were re-measured after the round-trip harness gained
> a border guard. Before it, the same comparison read 0.98°/2.80° → 0.56°/1.40°;
> that spread was the harness smearing frame-edge pixels into straight lines,
> not the mask. See CLAUDE.md, "The round trip measured itself for a while".

It runs in ~0.3 s per photograph at 2048 px inference, and 0.0 s if the masks
were exported once (below).

### It masks most of the frame and almost none of the evidence

This is the number that decides whether the guard in `masks.credible` is thick
enough, and it is the distinction that module was written for:

| asset | share of frame masked | share of **line evidence** lost |
|---|---|---|
| XYZ | 70 % | **1.2 %** |
| white sparrow | 47 % | **0.9 %** |
| Alte Scheune | 53 % | 7.0 % |
| 79cb3387… | 63 % | **11.1 %** |

Masking 40–70 % of the pixels costs 1–11 % of the straight-line length, because
what it removes is sky, grass and road — large, and empty of lines. The refusal
threshold is 55 % of the evidence, so on this set the guard has a wide margin.
It is still the outermost check, and it is what catches a mask applied with the
wrong polarity or belonging to a different photograph.

### Which lines a mask actually removes

**Both endpoints inside, or the line stays.** That is the whole rule
(`masks.drop_by_endpoints`). A line crossing the mask boundary keeps its full
weight, because the part of it on the building is real evidence and the fit is
length-weighted regardless. There is no threshold to choose and nothing is
half-removed.

Two earlier rules, kept here because both sound reasonable:

* a **sampled threshold** — drop the segment once 60 % of five points along it
  fall inside. It discards straddling lines wholesale, and which side of the
  line a segment lands on turns on a sample or two of noise.
* a **per-segment weight** equal to the visible fraction. It measures a shade
  better (0.558° mean / 1.01° worst against 0.556° / 1.15° for endpoints) but
  makes the segmenter a soft influence on every line in the frame rather than a
  decision about a few, and it needs a third factor in the weight for that.
  Within noise, and more machinery; the endpoint rule won on simplicity.

### The threshold is not a knob; the shrink is the whole game

**The threshold.** The matte comes back essentially binary. Sweeping the cut-off
from 0.1 to 0.9 on a real barn moved the masked share from 50.5 % to 51.1 %.
There is nothing in between to select.

**The shrink.** BiRefNet cuts exactly along the building's silhouette, so the
building's own corner and roof edges have both endpoints just inside the mask —
they are precisely what the endpoint rule throws away first, and they are the
longest and best-conditioned evidence in the frame. Pulling the reject region
off the silhouette first hands them back:

| reject mask shrunk by | mean | worst |
|---|---|---|
| 0.000 (~0 px) | 0.663° | 1.95° |
| 0.002 (~4 px) | 0.597° | 1.43° |
| 0.004 (~8 px) | 0.575° | 1.43° |
| **0.008 (~15 px, default)** | **0.556°** | **1.15°** |
| 0.016 (~31 px) | 0.639° | 1.64° |
| **no mask at all** | 0.661° | 1.69° |

**Read the first row against the last.** Unshrunk, the mask is worth nothing —
0.663° against 0.661° for not masking at all. It removes as much good evidence
as clutter. Everything the segmenter buys is bought by handing the silhouette
back. `--birefnet-shrink 0` disables it; the value is a fraction of the frame
diagonal, so it does not change meaning with `--detect-max-edge`.

`masks.protect_structure` works alongside it, un-masking whatever a long
straight line runs through — a second, selective way of keeping architecture
that a mask happens to cover.

### Export the masks once instead of computing them every run

    python rectify.py "D:\Fotos" --mask-export "D:\Masken"
    python rectify.py "D:\Fotos" --mask file --mask-file "D:\Masken" --focal-35mm 24

Two problems, one seam. It bridges the interpreter split — the Python with torch
has no tkinter and the one with tkinter has no torch — and it turns a repeated
run, or a benchmark that re-analyses a folder many times, into a file read. The
PNGs are written at the analysis resolution and `masks.load` resamples, so the
six masks in `tests/assets/masks` total 42 KB. White means ignore, so they need
no `--mask-invert`.

**One thing the cache must not be used for: the round-trip test.** That test
warps each photograph by a known rotation, so the building has moved while the
cached mask still describes the original. On `Alte_Scheune.jpg` the live and
cached masks agree at **IoU 1.000** on the unwarped original and only 0.802 on a
warped copy, and using the cache anyway reports 0.69°/2.35° for an estimator
that actually achieves 0.65°/1.36°. It fails quietly, which is the dangerous
kind. See `tests/assets/masks/README.md`.

### Check the setup before running a batch

    python rectify.py --mask-info --birefnet-model auto

It names the interpreter, which of `torch`, `timm`, `transformers`,
`safetensors` and `torchvision` it can import, whether `tkinter` is present, and
whether the weights actually load. The failure modes are a missing torch, a
torch installed into a *different* interpreter, and weights sitting apart from
the architecture that defines them — none of which is obvious from a run that
simply errors on every file.

`--birefnet-model auto` searches the usual ComfyUI locations, and a folder is
only a candidate if `birefnet.py` sits beside the weights: a 444 MB file that
cannot be loaded is a worse answer than finding nothing. Pass a path as a hint
and **only** that path is searched.

`--remember` stores the path so it is typed once.

### Nothing is vendored

The weights are Apache-2.0 and the BiRefNet architecture is MIT, so either could
be redistributed — but both live in the user's ComfyUI install and this project
only knows how to talk to them, exactly as it did with SAM checkpoints. That
keeps a torch dependency out of a project that is otherwise numpy, OpenCV and
Pillow, and `--mask-export` means the machine that has torch need not be the
machine that runs the correction.

`--mask birefnet` caps the worker pool at 2 unless `-j` is explicit, because
workers are separate processes with their own model cache: eight of them load
444 MB eight times and then queue for one GPU.

## What was here before: Segment Anything

SAM was the first implementation and it is gone. The reason is worth keeping,
because the argument for it still sounds right.

SAM returns forty boundaries and no labels. Nothing in its output says which
region is the building, so that had to be decided by an invented criterion, and
two were used with a region surviving on **either**:

* **line density in and around the region.** A facade is threaded with long
  straight lines; foliage, sky and cars are not. This one works.
* **how straight the region's own outline is.** A wall is bounded by a handful
  of straight edges, a tree crown by a fractal outline no small number of
  segments approximates. This one has no signal at all.

Measured over the 42 regions SAM returns for `Alte_Scheune.jpg`, the outline
score (`6/n` vertices from `approxPolyDP` at 1 % of perimeter) had median **1.00**
and **41 of 42** regions cleared the 0.45 floor:

| region | share of frame | line density | outline score | fate |
|---|---|---|---|---|
| the barn | 46.2 % | 30.1 | 0.86 | kept, correctly |
| **sky** | 23.5 % | 1.7 | **0.67** | **rescued** |
| **foreground grass** | 10.2 % | 0.9 | **1.00** | **rescued** |

Line density alone dropped 34 of 42 regions; the outline rescue put 33 back. The
surviving mask covered 11 %, 2.6 % and **0.0 %** of the frame on three test
photographs — so `--mask sam` cost 30 seconds a batch and changed almost nothing.
Sharpening the epsilon does not save it: at 0.2 % of perimeter the ranking
*inverts*, the barn scoring 0.18 against the sky's 0.22, because SAM's blob
outlines are already smooth at 768 px.

| | mean | worst |
|---|---|---|
| mask off | 0.98° | 2.80° |
| **SAM as shipped** | **1.04°** | **3.52°** |
| SAM, line density only | 0.62° | **1.18°** |
| BiRefNet-HR | **0.56°** | 1.40° |
| SAM ∪ BiRefNet | **0.52°** | **1.18°** |

Three things are recorded here rather than deleted:

1. **The rescue made masking worse than not masking at all.** A broken half of
   an "either" test silently vetoes the working half — that is the failure shape
   to watch for, not the specific statistic.
2. **It was validated on synthetic shapes, and both tests passed.** A drawn
   rectangle scores 0.9+ and a drawn ragged blob under 0.6; real SAM regions are
   neither. Synthetic fixtures are the right instrument for a *geometric*
   question with known ground truth and the wrong one for a *statistical*
   question about real texture — and "is this region foliage" is the second kind.
3. **Repaired SAM still won the worst case, and the union of the two won both.**
   0.52°/1.18° against BiRefNet's 0.56°/1.40°, because the two models fail on
   different photographs. On six assets that is too thin to justify carrying two
   models, a checkpoint hunt and an AGPL-3.0 optional dependency — but it is the
   first thing to re-measure if more ground truth ever exists.

One incidental trap died with SAM and is worth remembering as a *class* of bug:
**`ultralytics` replaces `cv2.imread` on import**, to support non-ASCII paths,
and its wrapper returns `(h, w, 1)` for a greyscale read where OpenCV returns
`(h, w)`. It was the first SAM backend tried, so merely having it installed
broke `--mask file` — the very folder the export path writes. `masks.load` now
insists on two dimensions rather than trusting whichever `imread` is installed.
An optional dependency can change the behaviour of a required one, at import.

## A hand-painted mask, or another tool's output

    python rectify.py "D:\Fotos" --mask file --mask-file "D:\Masken" --focal-35mm 24

`--mask-file` accepts a folder as well as a single PNG. For each photograph it
looks for `<stem>.png`, `<stem>_mask.png` or `<stem>-mask.png` beside it, so a
batch exported from another tool drops straight in. A missing mask is an error,
not a silent fallback.

Convention: **white means ignore**. A segmenter normally returns the *subject*
in white, so if the mask marks the building rather than the clutter, add
`--mask-invert`.

### Which BiRefNet weights

| file | size | inference | notes |
|---|---|---|---|
| **BiRefNet-HR** | 444 MB | 2048 px | what was measured; the default of `auto` |
| BiRefNet-general | 885 MB | 1024 px | larger file, lower inference resolution |
| BiRefNet_lite / lite-2K | 178 MB | 1024 / 2048 px | faster, untested here |

The resolution is read from the file name, because that is what every ComfyUI
node does and the names are consistent enough for it. Override with
`--birefnet-res`.

**Analysis resolution barely matters downstream.** The correction runs at 1600 px
on the long edge and masks are resampled nearest-neighbour, so a mask finer than
a few pixels is wasted work. What the 2048 px inference buys is a cleaner
*decision* about what the subject is, not a finer edge.

### Why this is a seam rather than a dependency

Everything above goes through one function, `masks.build`, returning a boolean
array the size of the analysis image. `off`, `auto`, `file` and `birefnet` are
interchangeable behind it, which is what makes `--mask-export` possible: the
model runs in whichever interpreter can load it, and everything afterwards --
batch runs, the GUI, another machine -- consumes a folder of PNGs. A user who
prefers a different segmenter gets the same benefit through `--mask file`
without this project learning about it.
