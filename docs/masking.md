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

## Segment Anything, built in

    python rectify.py "D:\Fotos" --mask sam ^
        --sam-model "D:\ComfyUI_windows_portable\ComfyUI\models\sams\sam_vit_b_01ec64.pth" ^
        --focal-35mm 24

**SAM finds boundaries superbly and has no idea what they mean.** Nothing in its
output says which of the forty regions it returned is the building. That gap,
not loading the model, is the whole problem, and it is closed with evidence the
pipeline already has:

> SAM supplies the edges. The line detector supplies the labels.

Every region SAM returns is judged by **two independent signals**, and survives
on either:

* **line evidence in or around it.** A facade is threaded with long straight
  lines; foliage, sky, cars and people are not. Counted over a slightly dilated
  region, which matters more than it sounds: a stucco panel between two windows
  contains no straight lines at all -- its edges are the window frames and floor
  bands *around* it -- so counting only the interior scored the wall of a
  building as foliage and masked it out of its own measurement.
* **how straight its own outline is.** A wall, a window or a roof plane is
  bounded by a handful of straight edges; a tree crown has a fractal outline
  that no small number of segments approximates. This needs no lines at all and
  rescues a plain surface that happens to sit away from any of them.

Foliage fails both, which is the only thing that has to be true. No text model,
no GroundingDINO, and it works with SAM 1 and 2 as well as SAM 3.
`--sam-min-density` sets how empty a region must be before it is dropped,
relative to the densest region *in that picture*, because line density scales
with how much of the frame the building occupies.

### Check the setup before running a batch

    python rectify.py --sam-info --sam-model "D:\...\sams\sam_vit_b_01ec64.pth"

Prints the interpreter in use, which backends it can import, and whether that
checkpoint actually loads. The three ways this goes wrong -- no backend
installed, a backend installed into a *different* interpreter, and a checkpoint
needing a different package -- are indistinguishable from a run that simply
errors on every file.

**Install into the interpreter that runs this tool.** On a ComfyUI portable
install that is its own python, which already has torch and CUDA:

    D:\ComfyUI_windows_portable\python_embeded\python.exe -m pip install segment-anything
    D:\ComfyUI_windows_portable\python_embeded\python.exe D:\Batch-Perspective-Correction\rectify.py --gui

Using ComfyUI's python avoids downloading a second multi-gigabyte torch.

### When torch and the GUI live in different Pythons

They usually do, and neither side is wrong:

| | torch + SAM | tkinter (the GUI) |
|---|---|---|
| ComfyUI's `python_embeded` | yes | **no** -- the Windows embeddable Python omits tcl/tk |
| the system Python | usually not | yes |

`--sam-info` prints both halves, because seeing only the SAM half hides half the
diagnosis. And when the mask fails in the Python that has tkinter, the error
offers the export route *before* suggesting an install -- putting a
multi-gigabyte CUDA torch into a second interpreter, on a machine that already
has one three folders away, is the wrong first answer.

Rather than choosing between the segmenter and the review window, run SAM once
from whichever Python can load it and consume the result anywhere:

    rem from ComfyUI's python, which has torch
    D:\ComfyUI_windows_portable\python_embeded\python.exe rectify.py "D:\Fotos" ^
        --sam-export "D:\Masken" ^
        --sam-model "D:\ComfyUI_windows_portable\ComfyUI\models\sams\sam_vit_b_01ec64.pth"

    rem afterwards, from any python -- batch, GUI, another machine
    python rectify.py "D:\Fotos" --mask file --mask-file "D:\Masken" --focal-35mm 24

The masks are written at analysis resolution and resampled on load, so the
folder stays small and the segmenter runs once per photograph rather than once
per experiment.

### Or do not type it at all

    --mask sam --sam-model auto

Searches the usual ComfyUI locations and prefers what actually works over what
is largest: plain `sam_vit_b` first, because HQ checkpoints need another
package, safetensors need converting, and ViT-H buys nothing for a mask that is
resampled to a few hundred pixels. Files far too small to be the model their
name claims are skipped. It reports what it chose.

### Type the checkpoint path once

    python rectify.py "D:\Fotos" --mask sam --sam-model "D:\...\sam_vit_b_01ec64.pth" --remember

After that the path fills itself in, and the GUI remembers whatever you pick in
its file dialogs. `--forget` clears it; an explicit flag always wins over a
remembered one.

Only *paths* are remembered -- the checkpoint, a mask folder, the output folder
and the focal length. Correction parameters deliberately are not: a setting that
silently persists between runs is one nobody can reason about, and a batch
should stay reproducible from its command line.

### Backends, and their licences

Tried in order; install whichever suits you.

| package | handles | licence |
|---|---|---|
| `ultralytics` | SAM 1, 2 and 3 from one class, picks the predictor from the file name | **AGPL-3.0** |
| `sam2` | SAM 2 checkpoints, needs the matching architecture config | Apache-2.0 |
| `segment_anything` | SAM 1 | Apache-2.0 |
| `segment_anything_hq` | the `sam_hq_*` checkpoints | Apache-2.0 |

This project is MIT and bundles none of them: torch is imported only when
`--mask sam` is actually used, so a default install stays numpy, OpenCV and
Pillow. If you care about copyleft reaching your work, prefer the Apache-2.0
packages.

### Text prompting needs SAM 3

    --sam-text "tree, foliage, sky, car, person"

Only SAM 3 has a concept head; SAM 1 and SAM 2 have no text encoder at all and
cannot take words. Prompt the *rejects* rather than the building: concrete
countable nouns ground reliably, "architecture" does not, and a missed piece of
building is lost evidence rather than a cosmetic flaw. Without `--sam-text` the
line-density route above is used, which needs no text head.

### A folder of SAM checkpoints is full of look-alikes

Each of these fails differently, so each is named on sight — the GUI shows it
when you pick the file, and `--mask sam` reports it in the log:

| file | what happens |
|---|---|
| `sam_vit_b_01ec64.pth` | works, the sensible default |
| `sam_hq_vit_*.pth` | different architecture; needs `segment-anything-hq` |
| `sam2.1_*.safetensors` | converted to `.pt` once, beside the original |
| `mobile_sam.pt` at 129 kB | far too small to be MobileSAM; flagged |
| no `sam3*` present | text prompting unavailable |

### Whatever the source, a mask that eats the evidence is refused

Judged on **line evidence lost, not pixels covered**, and the difference is not
academic. Measured on six real barns: one photograph has 64 % of its frame
masked and loses 1.5 % of its vertical line weight — a grassy foreground,
harmless — while another masks 71 % and loses **74.5 %**, because that barn is
painted green and the vegetation cue took the wall for foliage. A coverage test
rejects the harmless one and passes the dangerous one.

For an external segmenter this is the check that matters most: a SAM mask
applied with the wrong polarity, or one belonging to a different photograph,
both show up as nearly all the evidence vanishing, and both are refused with a
reason rather than quietly ruining the fit.

## A hand-painted mask, or another tool's output

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
