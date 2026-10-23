# Perspective Correction

![The review window in Horizontal auto: the obliquely shot facade on the left with its detected lines and the tool palette, the corrected frontal view on the right with the source outline and the crop, controls below.](docs/screenshot.jpg)

Straightens architectural photographs. Two modes:

- **Auto**: converging verticals made plumb and the frame levelled: roll, pitch
  and focal length, a camera rotation `H = K R K⁻¹`, so it cannot shear.
- **Horizontal auto**: an obliquely shot facade turned into a **frontal view**,
  with verticals plumb *and* horizontals level, so the wall is a true rectangle.

You review one photograph at a time and nothing is written until **Save**.

## Horizontal auto: frontal facades from oblique shots

This mode is for **facade textures for 3D**, **Google Street View grabs**,
elevation tracing, or any wall you could only photograph from the side. The
receding direction is undone (yaw), and the stretch that undoing causes is
compressed back to plausible proportions. On a corner view only one wall can come
out frontal: drag the **facade strip** onto that wall. A drawn horizontal line
overrules the detector.

## Detectors first, a few seconds of your hand second

A line detector (**lsd**, or **mlsd**, which keeps only structural edges) finds
the facade, and on most photographs that is the whole job. Where it is fooled by
cars, a sloping street, roof battens or a second wall, a few seconds of hand work
on the picture fix it:

- **paint it out** (`b`), **box it in** with SAM (`s`), or name it (Grounding DINO);
- **strike slanted** lines in one click;
- **draw the truth** (`m`): a drawn line comes out *exactly* vertical or
  horizontal, and 2 + 2 drawn lines make a rectangle with 90° corners.

Every control explains itself on hover (after a second).

## Install

```
pip install -r requirements.txt        run it from this folder
pip install -e .                       or install it, and get the `pc` command
pip install --no-deps ai-edge-litert   optional: the mlsd detector
pip install --no-deps simple-lama-inpainting   optional: lama fill (default; telea without it)
```

Python 3.9+, numpy, OpenCV, Pillow, piexif. The GUI needs Tkinter.
`--no-deps` matters: lama's pins otherwise break OpenCV.
`python rectify.py --doctor` says what is present. Segmentation weights
(BiRefNet, Grounding DINO, SAM 2) are not in the repo; see [docs/masking.md](docs/masking.md).

**Developed and tested on Windows only. Linux and macOS are untested.**

## Use

```
python rectify.py --gui                          the review window
python rectify.py "D:\Fotos"                     batch: write Foto_corr.jpg beside each
python rectify.py "D:\Fotos" --horizontal        batch, Horizontal auto
python rectify.py --help                         every option
```

or double-click `Perspective Correction.bat`. The biggest accuracy win is
telling it the lens: `--focal-35mm 24`.

More: [accuracy](docs/accuracy.md) · [algorithm](docs/algorithm.md) ·
[detectors](docs/detectors.md) · [masking](docs/masking.md).

## Credits and licence

MIT. See LICENSE and CREDITS. Descended from
[chsasank/Image-Rectification](https://github.com/chsasank/Image-Rectification)
([review](docs/reference-review.md)); design informed by darktable's `ashift`
and ShiftN ([prior art](docs/prior-art.md)), no code taken from either. README
photographs from Wikimedia Commons, credited beneath each; the test pool is
listed in [tests/assets/README.md](tests/assets/README.md).
