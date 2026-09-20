# Perspective Correction

![A Newark mansion photographed looking up, before and after. The verticals converge sharply in the left frame and stand plumb in the right one; the soft band down the left edge of the result is the area the rotation opened up, filled rather than cropped away.](docs/hero-before-after.jpg)

<sup>Kastner Mansion, Newark — [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Kastner_Mansion_Newark.jpg), Djflem, CC0. Corrected here at pitch +12.7°, roll +2.4°, confidence 0.63.</sup>

Straightens converging verticals in architectural photographs — roll, pitch and
focal length, three numbers, `H = K R K⁻¹`. That is a camera rotation, so it
cannot shear the frame into something the lens never saw. The horizon is derived
from the fitted model (`K⁻ᵀu`), never detected on its own.

**It is built around reviewing each photograph, not around processing a folder
unattended.** You step through a selection one image at a time, see what the
detector found, adjust it, and nothing is written until you press Save. A
fire-and-forget run over a folder is still there, one button along, but it is no
longer the default gesture. The reasoning is worth stating because it shaped
every threshold in the code: when nobody is looking, a photograph left alone
costs nothing while a photograph warped on a bad guess is ruined, so the tool
should refuse when in doubt. When a person *is* looking, a bad attempt is
rejected on sight — and refusing early instead costs a correction that was
wanted. The caps and confidence gates are being re-decided one at a time on that
basis, each against its own measurement.

## The window

![The review window at 1920×1080: four equal fields around a black cross. Top left the original with detected lines drawn on it and the tool palette down its edge; top right the corrected frame with crop handles; bottom left the detector and mask controls; bottom right the angle sliders and the Review/Unattended bar.](docs/ui.png)

The window *is* a cross of four equal fields. **Top left** is the original, with
what the estimator saw drawn over it and the tools that change the input running
down its edge. **Top right** is the result, at the same scale as the original so
the two can be compared honestly, with a crop rectangle whose discarded area is
shaded rather than cut — the picture never moves while you drag. Grey guides are
pulled out of the black cross with the mouse and laid against an edge; a true
vertical should run parallel to one. **Bottom left** finds things: line detector,
brush width, mask source. **Bottom right** edits them: roll, pitch, yaw, crop,
and the buttons the review ends on.

Every tool sits on the picture it acts on, and the palette is one set of marks
drawn at one pen weight:

| tool | key | what it is for |
|---|---|---|
| **Mark** | `m` | drag a line along an edge you know is truly vertical or horizontal, and it outranks the detector. Which of the two it means is read off the drag — steeper than 45° is a vertical — and the rubber band is tinted by that decision while you drag, so you see it before you let go. A click that never moved deletes the mark under it |
| **Mask brush** | `b` | paint over what the fit should ignore. A stroke across the parked cars says "not the building" directly, which is faster than arguing with a segmenter. Left drag paints, right drag erases, `Alt`+right drag sizes the pen |
| **PC Rectangle** | `p` | click the four corners of one facade to rectify that surface instead of rotating the camera. The tool when one flat plane has to come out exactly square |
| **Box-select (SAM)** | `s` | drag a box around the building; SAM segments it and everything outside is ignored |
| **Grounding DINO** | | find the building by name instead of by hand, and mask the rest |
| **Strike slanted** | | drop every detected line that is neither vertical nor horizontal. Usually the roof |
| **Facade strip (ROI)** | | two draggable rulers limiting which horizontals feed the yaw. For corner views, where two walls pull the horizontal fit apart |

Across the top of each pane are the overlays: **Lines** and **Mask** on the
original, **Check lines** on the result. Check lines re-runs the detector *on the
corrected frame* and draws what it finds — green where a line came out truly
vertical or level, red where it still leans. It is the direct way to see a
correction that came out too weak, instead of inferring it. When the primary
detector is not M-LSD, a second M-LSD pass is drawn in cyan and yellow beside it,
which answers "would another front end have found this facade?" while you are
looking at the lines.

**Review** walks the selection one photograph at a time and writes only on Save.
**Unattended** is the old fire-and-forget run, kept for when you want it. The
settings panel under the sliders is titled *defaults every photograph opens
with*: the same names appear in the panel itself, where they override the default
for the image in front of you only.

## What it sees

![A Franconian timber-framed barn with the detected lines drawn over it. The upright posts are green, the diagonal braces and roof battens are yellow, the eaves and window courses are blue, and a magenta line runs across the frame at the implied horizon.](docs/detection.jpg)

<sup>Burgebrach, Pfarrweg 1 — [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Burgebrach,_Pfarrweg_1,_Scheune,_001.jpg), Tilman2007, CC BY-SA 3.0.</sup>

**Green**: vertical lines the fit used. **Yellow**: vertical candidates it
rejected. **Blue**: horizontals. **Magenta**: the horizon implied by the fitted
model. This particular barn is the reason the legend is worth showing — the
*Fachwerk* braces are long, strong, straight lines that are not vertical, and
they are all correctly yellow. A detector that believed them would tilt the whole
building. `--debug-dir` writes one of these per image.

![A Swiss barn seen from an angle, before and after. Both visible walls have their verticals brought plumb.](docs/before-after.jpg)

<sup>Scheune Grauholzstrasse 30, Moosseedorf — [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Scheune_Grauholzstrasse_30_Moosseedorf.jpg), Uschoen, CC BY-SA 4.0. A corner view: 24 mm, pitch +7.4°.</sup>

## Install

```
pip install -r requirements.txt        run it from this folder
pip install -e .                       or install it, and get the `pc` command
```

Python 3.9+. numpy, OpenCV, Pillow, piexif — and nothing else. The GUI
additionally needs Tkinter, which ships with the python.org Windows installer
(`apt install python3-tk` on Debian/Ubuntu). The CLI works without it.

Everything beyond that is optional, imported lazily, and reports what is missing
instead of failing at import. **`python rectify.py --doctor` says what is
present** before any file is touched:

```
pip install -e ".[gui]"        drag-and-drop onto the batch window
pip install -e ".[mlsd]"       the M-LSD detector (the model is vendored)
pip install -e ".[deeplsd]"    the DeepLSD detector (also wants a checkout and 98 MB of weights)
```

Two backends are deliberately **not** extras, because installing them the
ordinary way damages the install:

```
pip install --no-deps simple-lama-inpainting     for --fill lama
```

Its stale pins downgrade Pillow to 9.5 and numpy to 1.26, and OpenCV in the same
interpreter then stops importing. `--fill comfyui` needs no package at all —
only a running ComfyUI and an API-format workflow.

### Model weights (not in this repository)

The optional segmentation backends need weights that are several gigabytes in
total, with single files over GitHub's 100 MB limit, so they are **not**
committed. Download them and put them where the code looks:

| what | where it goes | download |
|---|---|---|
| BiRefNet (general) | `models/BiRefNet/` | [ZhengPeng7/BiRefNet](https://huggingface.co/ZhengPeng7/BiRefNet) |
| BiRefNet HR — used by `--mask birefnet` here | `models/BiRefNet/` | [ZhengPeng7/BiRefNet_HR](https://huggingface.co/ZhengPeng7/BiRefNet_HR) |
| Grounding DINO — the box finder behind `--mask gdino` | `models/GroundingDINO/` | [IDEA-Research/GroundingDINO](https://github.com/IDEA-Research/GroundingDINO) (the `transformers` build: a folder with `config.json` + `.safetensors`) |
| SAM 2.1 | `models/sam2/` | [facebookresearch/sam2](https://github.com/facebookresearch/sam2) |

`--mask-info` reports what this interpreter can import and whether the weights
actually load, and `--birefnet-model auto` finds usable weights in the usual
ComfyUI folders if you already have them there. The M-LSD detector is the
exception: its model *is* vendored here (`models/`, with its licence beside it),
because it is small.

## Use

```
python rectify.py "D:\Fotos"                     write Foto_corr.jpg beside each original
python rectify.py "D:\Fotos" -o "D:\Fertig" -r   to another folder, with subfolders
python rectify.py "D:\Fotos" --overwrite         replace the originals (asks first)
python rectify.py "D:\Fotos" -n -v               decide, write nothing, explain
python rectify.py --gui                          graphical window
```

or double-click `Perspective Correction.bat` on Windows.

Drop photos or a folder onto the window — a **single image** is fine, so is a
mixed selection — or click the drop area to browse. Drag and drop needs
`pip install tkinterdnd2`; without it the same area is a click target and says
so. Double-click any row in the result list, especially a SKIPPED one, to open
that photograph in the review panel: an image the automatic pass declines is not
lost, it is queued for a decision a person makes in a couple of seconds.

### Options worth knowing

| flag | what it does |
|---|---|
| `--focal-35mm 24` | the exact lens, if you know it. **The single biggest accuracy win** |
| `--strength 0.7` | correct only part of the way |
| `--max-pitch`, `--max-roll` | caps in degrees (30 / 12). Beyond the cap a correction is refused, not trimmed |
| `--max-horizontal` | the yaw cap (60°). A *pure* yaw breach is the one exception to refusing: it warns and applies capped, because throwing away a good levelling over yaw alone was measured to cost more than it saved |
| `--min-confidence` | raise to skip more, lower to correct more (0.40) |
| `--no-pitch` / `--no-roll` | level only, or straighten verticals only |
| `--crop auto\|aspect\|inside\|none` | **auto** (the default) crops while the loss stays small and keeps the whole frame otherwise; `inside` always crops back to real pixels; `none` never crops |
| `--detector hybrid` | combine LSD's precision with M-LSD's judgement. Needs `pip install ai-edge-litert` ([measurements](docs/detectors.md)) |
| `--detector deep-hybrid` | the same idea with DeepLSD as the guide, and the only one measured to beat plain LSD here ([measurements](docs/detectors.md)) |
| `--detector-info` | which detectors this Python can actually run |
| `--fill telea` | **the default.** Fills the band the rotation opens up by propagating the edge inwards: no model, no download, deterministic. `--fill none` keeps the pad instead |
| `--fill lama` | generate that band with a learned model. Off by default — those pixels were never photographed |
| `--fill comfyui` | the same through a running ComfyUI; a Klein edit-model workflow ships, `--comfy-workflow` names another ([workflows/README.md](workflows/README.md)) |
| `--undistort lensfun` | correct barrel/pincushion first, from the EXIF lens profile, composed into the same single resample. No EXIF, no undistortion — it declines rather than guesses |
| `--mask birefnet --birefnet-model PATH` | segment the building out and ignore everything else ([details](docs/masking.md)) |
| `--mask gdino` | find the building by text prompt first, then matte inside that box |
| `--mask file --mask-file DIR` | one PNG mask per photo from any other tool |
| `--mask-export DIR` | write the masks once — from the Python that has torch, or just to stop recomputing them |
| `--mask-info` | what this Python can import, and whether the weights load |
| `--remember` | store `--birefnet-model`, `--mask-file`, `-o` and `--focal-35mm` as defaults; `--forget` clears them |
| `--debug-dir DIR` | write the line/horizon overlays and before-after pairs shown above |
| `--json-report FILE` | machine-readable results |
| `-j 8` | parallel workers |

`python rectify.py --help` lists all of them.

### A log somebody else can read

To produce something reviewable — by a colleague, or by an assistant helping you
tune it — drop a photo folder onto **`run_and_log.bat`**. It finds ComfyUI's
python by itself (the one with torch and CUDA), offers the BiRefNet weights it
finds, and writes one folder holding the corrected images, the detection
overlays, a `log.txt` that begins with the environment and settings that produced
it, and a machine-readable `report.json`. A line reading "SKIPPED, low
confidence" is nearly useless without knowing which interpreter, which library
versions and which settings were in force, so it records all three.

```
OK      DSC_0142.jpg  roll=-1.83deg pitch=+6.41deg conf=0.88 f=24mm(exif) keeps 87% 3648x2432 0.71s
SKIPPED DSC_0143.jpg  already upright (0.09deg < 0.15deg)
SKIPPED DSC_0144.jpg  low confidence (conf=0.21 < 0.40)
ERROR   DSC_0145.jpg  cannot read (broken data stream)
```

## Accuracy

Measured on 40 rendered scenes with an exactly known camera pose
([docs/accuracy.md](docs/accuracy.md)):

| | pitch | roll |
|---|---|---|
| focal length known | mean **0.10°**, worst 0.61° | mean 0.018° |
| focal length unknown (stripped web JPEG) | mean 2.03°, worst 5.41° | mean **0.017°** |

Levelling is accurate regardless, because roll does not depend on the focal
length. Correcting converging verticals does, so supplying `--focal-35mm` for a
folder shot with one lens turns the second row into the first.

The honest limitation: an obliquely shot **row** of facades receding down a
street puts its horizontals on many differently angled planes, the focal length
is then derived from that mixed evidence, and a wrong `f` buys a wrong pitch that
fits the lines just as well. Such a photograph is corrected confidently and
slightly wrongly. It is the case the facade strip and the Mark tool exist for.

## Credits and licence

MIT. See LICENSE for the prior-art notes and CREDITS for the full list of
third-party models, dependencies and prior art this work builds on.

Descended from [chsasank/Image-Rectification](https://github.com/chsasank/Image-Rectification),
rewritten after measuring what that code actually does
([docs/reference-review.md](docs/reference-review.md)). Design informed by
darktable's `ashift` and ShiftN ([docs/prior-art.md](docs/prior-art.md)); no code
taken from either.

The photographs in this README are from Wikimedia Commons and are credited
individually beneath each one — CC0, CC BY-SA 3.0 and CC BY-SA 4.0 respectively.
The full test-asset pool, with its sources, authors and licences, is listed in
[tests/assets/README.md](tests/assets/README.md).
