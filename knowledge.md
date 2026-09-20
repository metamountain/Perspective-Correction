# Knowledge base — remaining architecture goals

Research + analysis backing the last open goals, written so this project's earlier measurements can retire
once this is in place. Every claim below is either (a) already measured in this
project (cited from this project's earlier measurements, with its section name so it can still be found
after that file is gone) or (b) external research with a source link — never both
blended into one unlabelled sentence. **This file is analysis and instructions, not
code.** Nothing here has been implemented; see CLAUDE.md's Ledger for the Qwen-facing
work packages that point back at each section.

---

## 1. Analysis: using the data better — outline the two main facades first?

### The question

Today the pipeline detects lines, classifies them vertical/horizontal by a fixed
image-axis window (`lines.split_by_orientation`), fits one roll/pitch/f, and —
*optionally*, since `use_scheme` landed — partitions lines into up to two facade
planes via `ArchitectureScheme` (verticals + `V_h1` + `V_h2`) **after** the raw
detection. The question worth answering with a test, not an opinion: does outlining
the two main facades **first** (a person or a detector marks roughly where facade A
ends and facade B begins) let the whole perspective construct — both facades' planes,
the shared vertical, the corner line between them — be computed more accurately or
more robustly than partition-after-detect?

### What's already measured

- **"The model is three numbers, and that is the point"**: roll is `f`-independent
  (mean 0.018°), pitch is linear in `f` (0.10° known `f`, 2.03° guessed) — so *any*
  facade-outlining scheme only ever helps the `f`/pitch side, never roll.
- **"Where it came from"**: the disqualified `chsasank/Image-Rectification` failed
  exactly at vanishing-point-at-infinity handling — a reminder that a two-facade
  scheme must be checked against a perfectly level test case (both VPs might sit at
  infinity or near it) before trusting it on obliques.
- **`ArchitectureScheme`** (now wired, `use_scheme`) already does the *detect, then
  partition* half of this. Its own measurement on Alte_Scheune (a real corner asset):
  `v=0.94, h1=0.61, h2=0.36` — a confidently double-planed frame. On `lochfassade.jpg`
  (CLAUDE.md Ledger, 2026-09-13) the second plane's support was only `0.045` despite
  the corner being visually obvious — the automatic *post-hoc* split under-detects a
  narrow or steeply foreshortened second facade. That is the concrete evidence for
  trying the opposite order.
  **Re-verified 2026-09-20** — this number was flagged going into the audit as having
  drifted to `h1=0.596, h2=0.147`. It has not: `ArchitectureScheme.from_image(gray,
  Settings(), img).detect()` on `tests/assets/Alte_Scheune.jpg`, run three times in
  separate processes, gives `{'v': 0.9379412277281797, 'h1': 0.6108816411240725,
  'h2': 0.35773513349100466}` byte-identical every time — `v=0.94, h1=0.61, h2=0.36`
  to the file's own precision, exactly as cited. `lochfassade.jpg` likewise reproduces
  `h2=0.04515409414057064` against the cited `0.045`. Both assets, `src/pc/scheme.py`
  and `src/pc/vanishing.py` are unchanged in git history since this was written (`git
  log` shows no commits touching either since before this note), which is consistent
  with the number not having moved. **So the claimed drift did not happen** — recorded
  here rather than silently accepted, because the whole point of this pass was to not
  take a claim about this file on faith either.

### External angle

fSpy (a widely-used single-image camera-matching tool) calibrates a corner exactly
this way: two vanishing points, **assumed orthogonal**, each defined by two
user-marked line segments on one facade — i.e. the human outlines which lines belong
to which facade *before* the vanishing points are computed, not after
([fSpy basics](https://fspy.io/basics/); background PDF:
[Using Vanishing Points for Camera Calibration](https://github.com/stuffmatic/fSpy/blob/develop/doc/Using%20Vanishing%20Points%20for%20Camera%20Calibration.pdf)).
That is the manual version of "outline first." The orthogonality assumption is the
same one this project already carries (`H = K R K^-1`, Manhattan world) — no new
geometric model is implied, only a different order of operations and where the
facade-membership signal comes from (a marked region vs. a post-hoc angular fit).

### Proposed test, not a change

Before touching `src/`: build a small measured comparison, analysis-only (same
discipline as the P9/pitch-cap passes already in the Ledger).

- **Test basis**: the existing corner-view assets that already have two confidently
  distinct planes per `ArchitectureScheme` (Alte_Scheune-like) *plus* the ones where
  the second plane is weak or missed (`lochfassade.jpg`). A synthetic corner scene
  (`tests/synth.py`'s `corner=True`) gives ground truth for the geometric half; the
  real assets give the statistical half — this project's own rule ("Synthetic scenes
  are right for geometric questions with ground truth, wrong for statistical ones
  about real texture," CLAUDE.md Gotchas) applies directly here.
- **What to measure**: for each asset, run the current post-hoc `use_scheme` path and
  a hand-outlined baseline (mark the facade split manually, e.g. via a fixed ROI split
  read from a sidecar file for the test only — no UI work), then compare the resulting
  roll/pitch/f error against ground truth (synthetic) or against the confidence gap
  (real assets, where ground truth doesn't exist).
- **Decision rule**: only worth building a facade-outlining UI/detector step if it
  measurably recovers cases like `lochfassade` (weak second-plane support) without
  regressing the already-confident cases. If it doesn't move the needle, the answer is
  "no" and that itself is worth recording, not silently dropped.

---

## 2. Detector: optimized architecture-line-finding with a dominant-edge hierarchy

### The question

Right now every detected line is either "vertical", "horizontal", or discarded by the
orientation window — a flat classification. The ask: add a **hierarchy** that also
flags the *dominant* edge lines (the building's actual structural boundary —
roofline, ground line, the corner edge between two facades) as a distinguished tier
above ordinary window/course lines, so the fit (and `ArchitectureScheme`'s partition)
can weight them differently instead of treating a roofline and a window mullion as
the same kind of evidence.

### What's already measured

- **"Four ideas that measurement killed"** — the project has already tried and
  rejected several plausible-sounding line-quality ideas: merging broken fragments
  into one line (`merge_lines`, off by default — costs accuracy: 0.10° off vs 0.33° on
  mean pitch error over 40 scenes), FLD-beats-LSD (measured, lost), M-LSD and DeepLSD
  as *defaults* (both measured, both lost to plain LSD narrowly — M-LSD is now
  available as an opt-in detector, not default). **The lesson that must carry
  forward**: any "dominant edge" heuristic needs the same before/after measurement on
  the same asset pool, not intuition about what a roofline "should" look like.
  Length-weighting already exists in the fit — a naive "longest lines win" hierarchy
  risks re-deriving something the fit does implicitly and calling it a new feature.
- **`ArchitectureScheme`**'s `relevant`/`ignored` split is already a two-tier
  hierarchy (structural vs. clutter), just not a *dominant-edge* one — it partitions
  by *which plane* a line belongs to, not by *how structural* it is within a plane.
  A dominant-edge tier would sit orthogonal to that split, not replace it.
- **Hough's removal** (this session, CLAUDE.md Done) is relevant precedent: a detector
  change that quietly degrades to worse evidence is worse than refusing. A dominant-
  edge hierarchy must not become a second silent fallback.

### External angle

Classical vanishing-point literature scores line segments by a combination of length
and angular consistency with a VP hypothesis (RANSAC inlier weighting already does the
consistency half); a dedicated "dominant contour" pass is closer to building-outline /
roofline extraction work than to VP search itself — worth searching specifically for
recent (2025-2026) **building outline / roofline extraction from oblique or ground-level
photographs** literature before designing this, since VP-search papers won't cover it.
That search was not done yet — flagged as the next research step, not completed here
(don't guess at a citation).

### Proposed test, not a change

- **Test basis**: reuse the existing detector benchmark harness
  (`tools/benchmark_detectors.py`, already measures round-trip error per detector) —
  add a variant that weights the "dominant" tier higher in the fit and compares
  against the current flat weighting, on the same asset pool used for every other
  detector decision in this file's history.
- **Decision rule**: same as Section 1 — only keep it if it wins a measured comparison
  on real assets, not synthetic ones (texture/clutter judgments are the statistical
  kind this project's own rule says synthetic scenes get wrong).

---

## 3. Supporting techniques: SAM as an assist, and a found-geometry overlay

### 3a. SAM — revisit now that it can be prompted by text

**What's already measured (earlier measurement, "Segment Anything was here, and what its
failure teaches")**: SAM (SAM1-era) was tried and deleted. It needed an invented
criterion — line density OR outline straightness — to decide which of ~40 regions was
"the building," and the straightness half had no signal on real photographs (median
1.00 over 42 regions, 41 of 42 above the floor — it couldn't tell the building from
the sky). `--mask sam` measured **worse than no mask at all** (1.04°/3.52° vs.
0.98°/2.80°). Three lessons explicitly flagged to carry forward: synthetic shapes
validate geometric questions, not statistical ones; a repaired/union approach won on
some assets but six assets was too thin to justify the dependency; an optional
dependency can silently break a required one (`ultralytics` replacing `cv2.imread`).

**Why this is worth reopening, not just re-trying**: the failure was specifically that
SAM had **no way to be told which region mattered** — it returned ~40 candidate
regions and the project had to *guess* with a proxy criterion. That constraint no
longer holds. **SAM3** (Meta, released late 2025) accepts short text/concept prompts
directly — "building facade," "wall" — and segments that concept across the image,
removing the invented-criterion problem at its root rather than patching it
([SAM 3: Segment Anything with Concepts](https://docs.ultralytics.com/models/sam-3),
[SAM3 by Meta: Text-Prompted Image Segmentation](https://www.codecademy.com/article/sam-3-by-meta-text-prompted-image-segmentation-tutorial)).
A second, narrower approach specific to facades pairs instance segmentation with CLIP
semantic filtering to keep only facade-like regions and discard the rest — i.e. a
built two-stage version of exactly the "invented criterion" this project's own SAM
attempt lacked, done properly with a learned filter instead of a hand-picked geometric
proxy (mentioned in passing in facade-processing literature found this session; not
independently verified against this project's assets — flag for benchmarking, not a
citation to build on blind).

**Where this could plug in**: as *support* for Section 1 — a text-prompted "building
facade" region could seed the facade-outlining step, or as a mask source alternative
to BiRefNet (`mask_mode` already has the `birefnet`/`file` slots; `sam` would be a
third, following the exact same optional-backend convention — lazy import,
`describe()`/`available()`, `--doctor` reporting, `--no-deps` install).

**Do not repeat the past mistake**: any reintroduction needs the same round-trip
benchmark this project already has (`tools/benchmark_detectors.py` / the round-trip
error methodology in "The round trip measured itself for a while"), on the *same* or a
comparable asset pool, before it becomes a default anything. It is explicitly SAM's
*fitness as a mask source*, not a detector, being reconsidered — the project's whole
line-detection pipeline stays untouched by this.

**Measured 2026-09-13 — GDINO-crop + BiRefNet gives no gain over plain BiRefNet on
the current pool (written down and stopped).** The user's actual direction was to keep
BiRefNet as the matte engine ("biref is quite good and fast, don't drop it too fast")
and test whether a Grounding DINO "building" crop *in front of* BiRefNet improves the
mask, rather than replacing BiRefNet with SAM2. `tools/probe_gdino_birefnet.py` runs
GDINO → top box (4 % pad) → BiRefNet-HR on the crop → paste back, and compares against
plain full-frame BiRefNet-HR and the cached HR reference on three assets:

| asset | IoU plain~comb | IoU comb~HRref |
|---|---|---|
| lochfassade | 1.000 | 1.000 |
| heilsbronn-d7000-27mm | 0.975 | 0.962 |
| quaker-barn-with-office-fit-out | 0.988 | 0.943 |

The combined mask is essentially identical to plain BiRefNet (IoU 0.975–1.000 between
them). GDINO's boxes are large — they cover most of the frame — so cropping barely
constrains a BiRefNet that already locks onto the facade; the crop adds no silhouette
information BiRefNet did not already find. The combination's only *theoretical* benefit
— stopping BiRefNet from locking onto a **competing foreground object** (a car, tree or
person in front of the building) — is not exercised by these three clean-facade assets,
all of which plain BiRefNet already handles. Per this section's discipline the result is
recorded and the route stopped; it would only be worth revisiting on photographs that
actually contain a foreground object fighting the facade, where the crop could do real
work. (BiRefNet-lite, 169 MB, CPU-capable PVT-v2 backbone, was vendored alongside HR in
`models/BiRefNet/` and wired into `src/bpc/birefnet.py`'s arch selection the same pass.)

### 3b. Helper: overlay the perspective grid the algorithm actually found

**What's already built (earlier work, "A transparent grid over the corrected pane" —
current CLAUDE.md Done)**: a rectilinear checking grid on the *after* pane
(`v_grid`, `_draw_grid`, now with rulers on three edges) — it answers "is this vertical
now," a **generic** measuring instrument unrelated to what the algorithm detected.

**What's being asked for here is different**: an overlay that draws the *specific*
perspective construction the algorithm found on **this** photograph — the detected
vanishing points, the lines that voted for them, the implied horizon — i.e. a live,
in-GUI version of what `analysis/README.md`'s offline debug output already renders
(`<name>_lines.jpg`: green = vertical inliers, yellow = rejected candidates, blue =
horizontals, magenta = the implied horizon) and what `ArchitectureScheme.draw_preview`
already does for its own relevant/ignored partition (green vs. grey). Both rendering
functions already exist; what doesn't exist is a **user-facing toggle** on the *before*
pane that shows one of them without going through the CLI's `--debug-dir` flag.

**External angle**: this is exactly the visualization fSpy shows *while the user is
placing vanishing-point lines* — a live grid projected from the current VP estimate,
so a bad guess is visible as a skewed grid before committing to it. The project
already has all the geometry (`vanishing.Hypothesis`, the fit's `roll/pitch/f`); this
is a rendering job, not a new estimator.

**Proposed shape, not a spec**: a checkbox beside the existing "Grid"/"Lines" toggles
in the review controls (left/FIND column, alongside the detector — see the
2026-09-13 "find vs. edit" column entry in CLAUDE.md) that calls a thin adapter around
the existing `draw_preview`-style rendering onto `c_before`, gated the same way the
line-brush preview is (canvas overlay, never composited into the saved file — the same
rule the transparent grid already follows). Left as a shape, not a package, because it
needs a decision on *which* geometry to show by default (raw VP inliers vs. the
scheme's relevant/ignored partition) before it's a scoped task.

---

## 5. Barrel/pincushion distortion — external reference only

**Status, pipeline order, the landed Stage 0, the measured-and-rejected drift
trigger, settings and the test case all live in the Ledger.** They were
duplicated here and are not any more — one status, one place. What is kept
below is the part CLAUDE.md should *not* carry: third-party APIs and licences,
which are facts about the outside world rather than about this project.

### Stage 1 — AnyCalib (blind single-image distortion fit)

- **Paper**: ICCV 2025, github.com/javrtg/AnyCalib.
- **License**: Apache-2.0 (code + weights).
- **Requirements**: Python ≥ 3.10, PyTorch, CUDA for practical speed (~25 ms/image
  on RTX 4090 per the existing §14 notes; README gives no explicit timing).
- **Model IDs**: `anycalib_pinhole`, `anycalib_gen`, `anycalib_dist` (trained on
  Brown-Conrady distorted + EUCM strongly-distorted), `anycalib_edit`.
- **Camera models supported**: `pinhole`, `radial:k` (k=1..4, i.e. up to four
  radial coefficients k1..k4 — best distortion expressiveness currently available),
  `simple_radial:k`, `kb:k`, `ucm`, `eucm`, `division:k`.
- **API**:
  ```python
  from anycalib import AnyCalib
  model = AnyCalib(model_id="anycalib_dist").to(device)
  output = model.predict(image_tensor, cam_id="radial:4")
  # output["intrinsics"] → (f_x, f_y, c_x, c_y, k1, k2, k3, k4)
  ```
- **Weights**: auto-downloaded to torch hub cache or HuggingFace cache.
- **Does NOT estimate gravity** — that is Stage 2's job.
- **Integration concern**: needs the ComfyUI interpreter (torch + CUDA), same split
  as BiRefNet. Compute once, cache results (the undistortion map per image), consume
  in the GUI interpreter via a cached file — same pattern as `--mask-export`.

### Stage 2 — GeoCalib (gravity + focal prior on undistorted image)

- **Paper**: ECCV 2024, ETH CVG, github.com/cvg/GeoCalib.
- **License**: Apache-2.0 (code), CC-BY-4.0 (weights).
- **Requirements**: Python ≥ 3.9, torch, torchvision, opencv-python, kornia.
  Weights auto-downloaded from GitHub releases (`geocalib-pinhole.tar` or
  `geocalib-distorted.tar`).
- **API**:
  ```python
  from geocalib import GeoCalib
  model = GeoCalib(weights="distorted").to(device)  # "pinhole" for clean images
  result = model.calibrate(image, camera_model="radial",
                           priors={"focal": known_f})  # optional prior
  # result["camera"].f → (fx, fy)
  # result["gravity"] → gravity direction vector
  # result["focal_uncertainty"] → uncertainty on focal estimate
  ```
- **Camera models**: `pinhole`, `simple_radial` (k1), `radial` (k1,k2),
  `simple_divisional` (Fitzgibbon, for strong fisheye).
- **Key capability**: accepts a known focal as a prior (`priors={"focal": f}`),
  holding it fixed and estimating only distortion + gravity. This is exactly how
  our EXIF/lensfun focal feeds in.
- **Performance** (LaMAR benchmark, AUC at 1°/5°/10°):
  - Roll: 86.4 / 92.5 / 95.0
  - Pitch: 55.0 / 76.9 / 86.2
  - FoV: 19.1 / 41.5 / 60.0
  (Consistently best across LaMAR, MegaDepth, TartanAir, Stanford2D3D.)
- **Limitations**: fixed principal point (assumed image centre); PyTorch-only, no
  ONNX export; scene-dependent accuracy (textureless scenes uncharacterised).
- **How it enters our model** (load-bearing design claim from §14): a learned focal
  is a **prior term**, not a new estimator branch. `model.py` already carries a
  prior-with-sigma table (`manual` 0.12, `exif` 0.20, `default` 0.60) and
  `_blend_focal` combines prior and geometry by inverse variance in log space.
  GeoCalib enters as one more row (`"geocalib"`, sigma **measured** from
  `focal_uncertainty`, not guessed). No new branch in `estimate()`.
- **GeoCalib does NOT replace the VP search.** Roll is already measured at 0.018°
  mean and doesn't depend on f at all. GeoCalib's value is almost entirely the
  focal prior for the no-EXIF flat-facade case.

### Stage 5 — One resample, never two

Compose the undistortion map with `H = K R K⁻¹` into a single `remap`.
Concretely: the undistortion gives per-pixel `(map_x, map_y)` from lensfun or a
Brown-Conrady evaluation; the homography gives `H`. The composed remap is:

```
for each output pixel (u', v'):
    # 1. Invert the perspective warp to find where in the undistorted image it came from
    (x_u, y_u) = H⁻¹ · (u', v', 1)   # normalised
    # 2. Undistort: map (x_u, y_u) back through the radial model to the source pixel
    (x_s, y_s) = undistort(x_u, y_u; k1..k4, cx, cy, f)
```

`warp.limit`/`warp.plan` untouched; `warp.build` grows a map-producing variant
that returns `(map_x, map_y)` arrays instead of (or in addition to) the 3×3 H.
The existing `cv2.warpPerspective` call in `warp.apply` becomes `cv2.remap` when
an undistortion map is present.

### Licences (checked 2026-09-14)

| Component | Licence | Verdict |
|---|---|---|
| GeoCalib code | Apache-2.0 | OK |
| GeoCalib weights | CC-BY-4.0 | OK (attribution) |
| AnyCalib code+weights | Apache-2.0 | OK |
| lensfunpy wrapper | MIT | OK |
| lensfun DB (in wheel) | GPL-3.0, dynamic link | OK for MIT project |
| OpenCV | BSD | OK |
| PerspectiveFields (Adobe) | Non-commercial | **Rejected** |
| LensDistortionFromLines | CC-BY-NC-SA | **Rejected** |
