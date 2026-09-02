# Batch Perspective Correction

![left: detected verticals, horizontals and the implied horizon. right: corrected, the opened band filled by an optional ComfyUI backend rather than cropped](docs/hero-before-after.jpg)

Straightens converging verticals in architectural photographs, a folder at a
time. Built for the case where most of a batch needs a small correction, some
needs none, and a few must not be touched at all -- so the default behaviour
when the geometry is unclear is to **leave the photograph alone** and offer it
for manual review.

Descended from [chsasank/Image-Rectification](https://github.com/chsasank/Image-Rectification),
rewritten after measuring what that code actually does
([docs/reference-review.md](docs/reference-review.md)). Design informed by
darktable's `ashift` and ShiftN ([docs/prior-art.md](docs/prior-art.md)); no code
taken from either.

## Install

    pip install -r requirements.txt

Python 3.9+. numpy, OpenCV, Pillow, piexif. The GUI additionally needs Tkinter,
which ships with the python.org Windows installer (`apt install python3-tk` on
Debian/Ubuntu). The CLI works without it.

## Use

    python rectify.py "D:\Fotos"                     write Foto_corr.jpg beside each original
    python rectify.py "D:\Fotos" -o "D:\Fertig" -r   to another folder, with subfolders
    python rectify.py "D:\Fotos" --overwrite         replace the originals (asks first)
    python rectify.py "D:\Fotos" -n -v               decide, write nothing, explain
    python rectify.py --gui                          graphical batch window

or double-click `run_gui.bat` on Windows.

To produce something reviewable — by a colleague, or by an assistant helping you
tune it — drop a photo folder onto **`run_and_log.bat`**. It finds ComfyUI's
python by itself (the one with torch and CUDA), offers the BiRefNet weights it
finds, and writes one folder holding the corrected images, the detection
overlays, a `log.txt` that begins with the environment and settings that
produced it, and a machine-readable `report.json`. A log that says "SKIPPED, low
confidence" is nearly useless without knowing which interpreter, which library
versions and which settings were actually in force, so it records all three.

Log lines are one per file:

    OK      DSC_0142.jpg  roll=-1.83deg pitch=+6.41deg conf=0.88 f=24mm(exif) keeps 87% 3648x2432 0.71s
    SKIPPED DSC_0143.jpg  already upright (0.09deg < 0.15deg)
    SKIPPED DSC_0144.jpg  low confidence (conf=0.21 < 0.40)
    ERROR   DSC_0145.jpg  cannot read (broken data stream)

### Options worth knowing

| flag | what it does |
|---|---|
| `--focal-35mm 24` | the exact lens, if you know it. **The single biggest accuracy win** |
| `--strength 0.7` | correct only part of the way |
| `--max-pitch`, `--max-roll` | caps in degrees (20 / 12) |
| `--min-confidence` | raise to skip more, lower to correct more |
| `--no-pitch` / `--no-roll` | level only, or straighten verticals only |
| `--crop aspect\|inside\|none` | keep the original aspect ratio, fit inside, or don't crop |
| `--detector hybrid` | combine LSD's precision with M-LSD's judgement. Needs `pip install ai-edge-litert` ([measurements](docs/detectors.md)) |
| `--detector deep-hybrid` | the same idea with DeepLSD as the guide, and the only one measured to beat plain LSD here. Needs torch, a DeepLSD checkout and its weights ([measurements](docs/detectors.md)) |
| `--detector-info` | which detectors this Python can actually run |
| `--fill telea` | **the default.** Fills the band the rotation opens up by propagating the edge inwards: no model, no download, deterministic. `--fill none` keeps the pad instead |
| `--fill lama` | generate that band with a learned model instead. Off by default -- those pixels were never photographed |
| `--fill comfyui --comfy-workflow x.json` | the same through a running ComfyUI ([workflows/README.md](workflows/README.md)). The batch window has the address and a "Test connection" button |
| `--remember` | store `--birefnet-model`, `--mask-file`, `-o` and `--focal-35mm` as defaults; `--forget` clears them |
| `--birefnet-model auto` | find usable weights in the usual ComfyUI folders |
| `--mask-info` | what this Python can import, and whether the weights load |
| `--mask-export DIR` | write the masks once -- from the Python that has torch, or just to stop recomputing them |
| `--mask birefnet --birefnet-model PATH` | segment the building out and ignore everything else ([details](docs/masking.md)) |
| `--mask file --mask-file DIR` | one PNG mask per photo from any other tool |
| `--debug-dir DIR` | write line/horizon overlays and before-after pairs |
| `--json-report FILE` | machine-readable results |
| `-j 8` | parallel workers |

`python rectify.py --help` lists all of them.

## What it sees

![detected lines and the implied horizon](docs/detection.jpg)

Green: vertical lines the fit used. Yellow: vertical candidates the fit
rejected -- here the scattered clutter, on a real building usually the roof
rafters. Blue: horizontal lines. Magenta: the horizon implied by the fitted
model, which is derived from the vertical vanishing point rather than detected
separately. `--debug-dir` writes one of these per image.

![before and after](docs/before-after.jpg)

## Graphical mode

Drop photos or a folder onto the window -- a **single image** is fine, so is a
mixed selection -- or click the drop area to browse. Drag and drop needs
`pip install tkinterdnd2`; without it the same area is a click target and says
so. **Double-click an entry in the list** to open it in the review window
before running the batch at all.

The batch window runs the selection and colour-codes every result. Double-click any
row -- especially a SKIPPED one -- to open manual review:

* **before and after, side by side**, updating live;
* **sliders** for roll, pitch and focal length;
* **click any detected line to strike it out**, and the fit is recomputed
  without it. One button strikes out everything leaning more than 18 deg, which
  is usually the roof;
* **save correction** or **keep original**;
* **mark a vertical** — click two points on something you know is vertical (a
  door jamb, a downpipe, a building corner) and that outranks the detector
  entirely. Hugin's `t2` control point; two of them determine the answer. The
  case for it is the corner view where every detected line is real and belongs
  to the wrong wall — nothing to delete, only something to state.
* **crop by hand, or press "Auto crop"** — the after pane always carries a
  rectangle with four corner handles, and the part it discards is *shaded*
  rather than cut, so the picture never moves while you drag. "Auto crop" trims
  to the largest rectangle containing no invented pixel, which is the answer to
  the band a rotation opens up that needs no inpainting model at all;
* a **line detector** dropdown, so the question "would another front end have
  found the facade?" is answered while looking at the lines it found;
* a **region mask** panel: switch between `off`, `birefnet` and a folder
  of masks from any other tool, with an **opacity slider**, and see
  the excluded area and the lines it removed straight away. A mask you cannot
  see is a mask you cannot trust.

**"Review each..."** walks the whole selection through this same window, one
photograph at a time, writing nothing until Save is pressed for that one --
the unattended batch decides, this asks.

So an image the automatic pass declines is not lost -- it is queued for a
decision a person makes in a couple of seconds.

## Accuracy

Measured on 40 rendered scenes with an exactly known camera pose
([docs/accuracy.md](docs/accuracy.md)):

| | pitch | roll |
|---|---|---|
| focal length known | mean **0.10 deg**, worst 0.61 deg | mean 0.018 deg |
| focal length unknown (stripped web JPEG) | mean 2.03 deg, worst 5.41 deg | mean **0.017 deg** |

Levelling is accurate regardless, because roll does not depend on the focal
length. Correcting converging verticals does, so supplying `--focal-35mm` for a
folder shot with one lens turns the second row into the first.

## Licence

MIT. See LICENSE for the prior-art notes and CREDITS for the full list of
third-party models, dependencies and prior art this work builds on.
