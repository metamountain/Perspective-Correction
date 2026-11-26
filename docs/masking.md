# Masking: keeping the fit on the building

Trees, sky, cars and people contribute lines that are not part of any building.
A bare winter tree is the worst case: its twigs are near-vertical, long, and
plentiful, so they land squarely in the vertical candidate pool.

## The cheap mask that was here, and why it is gone

`--mask auto` needed no model at all: excess green, incoherent local gradient
orientation near vegetation, and bright unsaturated regions connected to the top
edge. That was its whole appeal, and it is retired.

Its measurement, on 40 synthetic scenes with trees occluding the facade:

| | pitch mean | pitch max | roll max |
|---|---|---|---|
| focal length **known**, mask off | 0.30 deg | 2.84 deg | 0.356 deg |
| focal length **known**, mask on | **0.23 deg** | **0.93 deg** | **0.190 deg** |
| focal length **unknown**, mask off | **1.51 deg** | **5.58 deg** | 0.469 deg |
| focal length **unknown**, mask on | 1.82 deg | 10.05 deg | 0.170 deg |

It helped where the focal length was known and **nearly doubled the worst-case
pitch error where it was not** — which is the case a stripped web JPEG puts you
in, and the one with least to spare. The reason is that it is a *pixel*
statistic answering a question about *objects*: it removes green, chaotic and
sky-like pixels, and on a stripped JPEG that takes the horizontals the focal
estimate needed.

Two things it taught, both kept:

* **A texture cue must not stand alone.** Coherence asks whether *one* direction
  dominates locally, and a half-timbered facade has *two* — posts and rails — so
  the beam grid scored as foliage. It was masking **8.2 %** of a real Fachwerk
  barn's facade, excluding the building from its own measurement. Requiring
  greenness nearby dropped that to 1.5 % at no measurable cost.
* **Drawing the mask is what found it.** The numbers showed only a slightly
  lower confidence; the screen showed speckles all over the timber frame.

`--mask auto` is **not** a command-line choice any more: `--mask` takes
`off` or `file`, and an old script naming `auto` stops with argparse
listing the two. That is deliberate -- a batch that quietly runs
unmasked because the mask it asked for no longer exists is the silent kind of
wrong this project avoids. The seam itself (`masks.build`) still accepts the
word and returns no mask, so a programmatic caller holding an old settings
object does not crash.

## Look at the mask before you trust it

`--debug-dir` tints the excluded region and draws the lines it removed in red,
and the GUI review window has a **show mask** toggle. Both exist because the
Fachwerk failure above was invisible in the numbers -- the only symptom was a
slightly lower confidence -- and obvious the instant the mask was drawn.

## BiRefNet and Grounding DINO, retired

The two learned-model backends -- `--mask birefnet` (one full-frame matte of
the salient object) and `--mask gdino` (a text prompt finds the box, the matte
runs inside it) -- are gone. In practice SAM's box select and the manual brush
masked better and cost less: two learned models, a checkpoint hunt and hundreds
of megabytes, for a gain of a few tenths of a degree on six photographs.
`--mask` takes `off` or `file` now; an old script naming a retired mode is
refused by argparse, and `masks.build` still accepts the old names and returns
no mask.

The general lessons from that era hold for any mask:

### It masks most of the frame and almost none of the evidence

This is the number that decides whether the guard in `masks.credible` is thick
enough, and it is the distinction that module was written for. The figures
below were measured with a segmenter mask (BiRefNet, before it was retired):

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

`masks.protect_structure` works alongside any mask, un-masking whatever a long
straight line runs through — a second, selective way of keeping architecture
that a mask happens to cover.

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

Three things are recorded here rather than deleted:

1. **The rescue made masking worse than not masking at all.** A broken half of
   an "either" test silently vetoes the working half — that is the failure shape
   to watch for, not the specific statistic.
2. **It was validated on synthetic shapes, and both tests passed.** A drawn
   rectangle scores 0.9+ and a drawn ragged blob under 0.6; real SAM regions are
   neither. Synthetic fixtures are the right instrument for a *geometric*
   question with known ground truth and the wrong one for a *statistical*
   question about real texture — and "is this region foliage" is the second kind.
3. **The union of two segmenters won both regimes.** The second model (then
   BiRefNet) fails on different photographs than the first, so joining the two
   masks beat either alone on all six assets. That is too thin a base to justify
   carrying two models, a checkpoint hunt and an extra optional dependency — but
   it is the first thing to re-measure if more ground truth ever exists.

One incidental trap died with SAM and is worth remembering as a *class* of bug:
**`ultralytics` replaces `cv2.imread` on import**, to support non-ASCII paths,
and its wrapper returns `(h, w, 1)` for a greyscale read where OpenCV returns
`(h, w)`. It was the first SAM backend tried, so merely having it installed
broke `--mask file` — the very folder the export path writes. `masks.load` now
insists on two dimensions rather than trusting whichever `imread` is installed.
An optional dependency can change the behaviour of a required one, at import.

## A hand-painted mask, or another tool's output

    python perspective_correction.py "D:\Fotos" --mask file --mask-file "D:\Masken" --focal-35mm 24

`--mask-file` accepts a folder as well as a single PNG. For each photograph it
looks for `<stem>.png`, `<stem>_mask.png` or `<stem>-mask.png` beside it, so a
batch exported from another tool drops straight in. A missing mask is an error,
not a silent fallback.

Convention: **white means ignore**. A segmenter normally returns the *subject*
in white, so if the mask marks the building rather than the clutter, add
`--mask-invert`.

### Why this is a seam rather than a dependency

Everything above goes through one function, `masks.build`, returning a boolean
array the size of the analysis image. `off` (and the retired `auto`) and `file`
are interchangeable behind it, which is what makes an external mask folder
possible: the mask comes from wherever the user got it -- painted by hand, or
written by another tool -- and everything afterwards, batch runs, the GUI,
another machine, consumes the same PNGs. A user who prefers a different
segmenter gets the same benefit through `--mask file` without this project
learning about it.

## Model weights (not in this repository)

Several hundred megabytes at a stretch, over GitHub's 100 MB limit, so not
committed:

| what | where it goes | download |
|---|---|---|
| SAM 2.1 | `models/sam2/` | [facebookresearch/sam2](https://github.com/facebookresearch/sam2) |

The M-LSD model is small and vendored in `models/`. The SAM 2.1 small weights
come from the `deps` menu.
