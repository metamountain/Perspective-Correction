# Perspective Correction

![The review window in Auto mode: the tilted Barcelona Pavilion on the left with its detected lines, the levelled correction on the right with the photograph's own border (outline) drawn in yellow.](docs/example_auto.jpg)

Straightens architectural photographs. Two modes:

- **Auto**: converging verticals made plumb and the frame levelled: roll, pitch
  and focal length, a camera rotation `H = K R K⁻¹`, so it cannot shear.
- **Horizontal auto**: an obliquely shot facade turned into a **frontal view**,
  with verticals plumb *and* horizontals level, so the wall is a true rectangle.

You review one photograph at a time and nothing is written until **Save**.

## Horizontal auto: frontal facades from oblique shots

![The review window in Horizontal auto: the obliquely shot Bauhaus Dessau facade on the left with its detected lines, the frontal correction on the right with the photograph's own border (outline) drawn in yellow.](docs/example_ha.jpg)

This mode is for **facade textures for 3D**, **Google Street View grabs**,
elevation tracing, or any wall you could only photograph from the side. The wall
comes out in its **real proportions**: squares stay square. Even a wall seen so
steeply that its own horizon crosses the photograph is rectified and framed
around the facade rather than refused. On a corner view only one wall can come
out frontal: drag the **facade strip** onto that wall. A drawn horizontal line
overrules the detector.

## Detectors first, a few seconds of your hand second

A line detector (**lsd**, or **mlsd**, which keeps only structural edges) finds
the facade, and on most photographs that is the whole job. Where it is fooled by
cars, a sloping street, roof battens or a second wall, a few seconds of hand work
on the picture fix it:

- **paint it out** (`b`), or **box it in** with SAM (`s`);
- **draw the truth** (`m`): a drawn line comes out *exactly* vertical or
  horizontal, and 2 + 2 drawn lines make a rectangle with 90° corners;
- **click four corners** (PC Rectangle): one wall flattened to a front view in
  its real proportions (squares come out square), cropped to 20 % around it,
  with more of the picture kept to drag the crop out by hand.

A loupe follows the pointer while you place points. Every control explains
itself on hover (after a second).

## Install

**Windows, double-click** `Install (Windows).bat` (`--lama` adds the lama fill,
which needs torch). It installs into the Python on PATH.

**macOS, one line in Terminal** (fetches the code into `~/Perspective-Correction`,
offers to install Python if it is missing, then installs everything):

```
curl -fsSL https://raw.githubusercontent.com/metamountain/Perspective-Correction/main/tools/install_mac.sh | bash
```

Or **double-click** `Install (macOS).command`; it installs into `.venv` here,
and `Perspective Correction.command` starts from it. On Linux, or by hand:

```
pip install -r requirements.txt        run it from this folder
pip install -e .                       or install it, and get the `pc` command
pip install --no-deps ai-edge-litert   optional: the mlsd detector
pip install --no-deps simple-lama-inpainting   optional: lama fill (default; telea without it)
```

Python 3.9+, numpy, OpenCV, Pillow, piexif. The GUI needs Tkinter.
`--no-deps` matters: lama's pins otherwise break OpenCV.
`python perspective_correction.py --doctor` says what is present. Everything
optional is also installed from inside the program: the **deps** button at the
bottom right lists only what is missing and installs what you tick.

**Sizes** (measured 2026-10-24 on Windows, Python 3.12). The program itself is
small; almost everything is the optional AI parts, and most of that is torch:

| part | on disk | needed for |
|---|---|---|
| **the program** | **14 MB** | everything (includes the M-LSD model, fonts and 4 test photos) |
| numpy, OpenCV, Pillow, piexif | 190 MB | everything |
| tkinterdnd2 | 3 MB | drag photos onto the window |
| ai-edge-litert | 59 MB | the mlsd detector |
| torch | 0.25–0.7 GB (CPU, macOS) · 3.1 GB (Windows CUDA) | lama, GeoCalib, SAM |
| lama + its weights | 206 MB | the good fill for the empty corners (telea fills without it) |
| GeoCalib + its weights | 117 MB | a better focal guess for photos without EXIF |
| SAM 2.1 small + transformers | 306 MB | box select (`s`) |

With all of it on Windows that is about 4 GB, of which the program is under
0.5 %; without the optional parts it is about 7 %.

Screen: made for **1920×1080** or larger. Smaller screens (or display scaling)
still fit, down to 1366×768, with smaller pictures.

**Developed and tested on Windows.** CI runs the core tests on Linux and macOS
too; the window itself is tested on Windows only. On a Mac the right-click
gestures use the right button and Option replaces Alt, but nobody has tried
them there yet.

## Use

```
python perspective_correction.py --gui                          the review window
python perspective_correction.py "D:\Fotos"                     batch: write Foto_corr.jpg beside each
python perspective_correction.py "D:\Fotos" --horizontal        batch, Horizontal auto
python perspective_correction.py --help                         every option
```

or, on Windows, run `python tools/make_launcher.py` once: it builds
`Perspective Correction.exe` with the logo, which starts the window without a
console and opens photographs dropped onto it (`Perspective Correction.bat`
still works). The biggest accuracy win is
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
