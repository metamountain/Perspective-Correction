# Masking: keeping the fit on the building

Trees, sky, cars and people contribute lines that are not part of any building.
A bare winter tree is the worst case: its twigs are near-vertical, long, and
plentiful, so they land squarely in the vertical candidate pool.

## The one measurement that decides how to use this

40 synthetic scenes with trees occluding the facade:

| | pitch mean | pitch max | roll max |
|---|---|---|---|
| focal length **known**, mask off | 0.30 deg | 2.84 deg | 0.356 deg |
| focal length **known**, mask on | **0.23 deg** | **0.93 deg** | **0.190 deg** |
| focal length **unknown**, mask off | **1.51 deg** | **5.58 deg** | 0.469 deg |
| focal length **unknown**, mask on | 1.82 deg | 10.05 deg | 0.170 deg |

**Masking pays off only when the focal length is known.** It improves the
geometry -- it removes exactly the outliers that hurt most -- but it also removes
*evidence*, and the focal length estimator is the part most starved for it.
Masking a stripped web JPEG can nearly double the worst-case pitch error.

So: `--mask auto` or `--mask file` belongs with `--focal-35mm`, or with photos
that carry EXIF. On a folder of unknown-lens web JPEGs, leave masking off. This
is why the default is `off` rather than `auto`.

Masking the vertical pool only, keeping every horizontal line for the focal
estimate, was tried as a way to have both: it gained essentially nothing
(pitch max 3.00 vs 2.84 with masking off) and was removed rather than kept as a
third mode nobody could choose between.

## The cheap mask, no model required

    python rectify.py "D:\Fotos" --mask auto --focal-35mm 24

`masks.vegetation_and_sky` uses three cues and needs no download:

* **excess green** -- foliage in leaf;
* **low structure-tensor coherence over busy pixels** -- a facade has a dominant
  local direction almost everywhere, a canopy has none. This is the cue that
  catches a *bare* winter tree, which is not green at all and is exactly the
  case that breaks an eaves line;
* **bright, unsaturated or blue regions connected to the top edge** -- sky, as
  opposed to a white wall, which is not connected to the top.

## An external segmenter (SAM, and friends)

    python rectify.py "D:\Fotos" --mask file --mask-file "D:\Masken" --focal-35mm 24

`--mask-file` accepts a folder as well as a single PNG. For each photograph it
looks for `<stem>.png`, `<stem>_mask.png` or `<stem>-mask.png` beside it, so a
batch exported from another tool drops straight in. A missing mask is an error,
not a silent fallback.

Convention: **white means ignore**. A segmenter normally returns the *subject*
in white, so if the mask marks the building rather than the clutter, add
`--mask-invert`.

### Choosing a model

Plain SAM is *promptable* segmentation without semantics: it will happily cut
out a tree, but it does not know that the tree is what you want removed. So it
needs either a text-grounded front end or an explicit prompt.

| option | size | notes |
|---|---|---|
| **SAM 3** (text-promptable concepts) | large | best fit if available -- prompt the *rejects* directly: "tree, foliage, sky, car, person, person" |
| GroundingDINO + SAM ViT-B | ~700 MB total | the established ComfyUI pairing; same effect, two models |
| GroundingDINO + MobileSAM | ~40 MB for SAM | markedly faster, and coarse masks are all this needs |
| SAM alone, hand-prompted | -- | fine for a handful of images, not for a batch |

**Resolution barely matters.** Analysis runs at 1600 px on the long edge and
masks are resampled nearest-neighbour, so a 1024 px mask is already finer than
the geometry can use. Segment small and fast.

Prompt for what should be *ignored* and no `--mask-invert` is needed. Prompt for
the building and add it.

### Why this is a seam rather than a dependency

Everything above goes through one function, `masks.build`, returning a boolean
array the size of the analysis image. Adding a bundled segmentation model would
mean a torch dependency and hundreds of megabytes in a project that is otherwise
numpy, OpenCV and Pillow -- and the table at the top of this page shows that
masking is not an unconditional win. A user who already runs a segmenter gets
the benefit through `--mask file` today; if the cheap `auto` mask ever proves
insufficient, a small ONNX segmentation model can be loaded through
`cv2.dnn` behind the same interface without adding torch.
