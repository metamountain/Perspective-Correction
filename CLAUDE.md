# Batch Perspective Correction — working notes (live)

**Governance.** This file is the live working document, deliberately kept small so it
fits a ~100k context window with room for the conversation. Together with
**`knowledge.md`** — the analysis and external sources for the four remaining research
goals (facade-outline-first, the dominant-edge hierarchy, SAM3, the found-geometry
overlay), cited from the Ledger's "Four research goals" entry — it is the complete
record. The former separate history archive `claude_save.md` was deleted 2026-09-13
after its still-relevant content was carried forward into these two files, so there is
no third file to follow; read a section of `knowledge.md` only when this file points at
one. After every completed feature or fix, update
*this* file — a change that lands without its note here is not done. **Status lives in
exactly one place: the Ledger below.** Open, in-progress, done and merely-wished items
are one list; a wish is recorded there the moment it is asked for. Never restate a
status in a second section — two lists that must agree is how P11 and P12 stayed
written as open and "asked, not started" for a day after they had landed. Work runs to
completion without check-ins; report back only when tests pass and every open item is
closed or blocked on a user decision.

## Project in one paragraph

Straightening converging verticals in architectural photographs (roll, pitch, f —
three numbers; the horizon is `K^-T u`, never detected).

**Direction change 2026-09-13 (user): manual review is the product; unattended
batch is not.** If several photographs are processed, it is image by image with a
person looking at each one — not fire-and-forget over a folder.

This inverts the reasoning every current default was built on, so read those
defaults with it in mind. The old premise was: *the metric is how many photos it
ruined, because a batch tool runs unattended over a folder someone cares about; a
photo left alone costs nothing, a photo warped on a bad hypothesis is gone;
therefore when in doubt, do nothing.* That is why `max_pitch_deg` / `max_horizontal_deg`
refuse rather than trim, why confidence is multiplicative so any factor can veto, and
why P9 recommended refusing a whole correction over applying part of it. **With a
person reviewing each image those costs are no longer asymmetric** — an attempt that
is wrong gets rejected on sight, so refusing early now costs a correction the user
wanted rather than saving them from one they didn't. The caps and gates are therefore
open questions again, not settled ones; each should be re-decided as a *review-panel*
default with its own measurement before anything is loosened.

Implementation is done here directly (2026-09-13); the local Qwen3.8-27B worker is
paused — why, and how to resume it, in `docs/qwen-knowledge.md`.

## Running things (do not rediscover this)

- Project root `D:\Coding\Batch-Perspective-Correction`, Windows, PowerShell 7,
  Python 3.12.9 (python.org), 32 cores.
- Compile: `python -m py_compile src/bpc/gui.py` (and anything else touched).
- One module: `python tests/run_tests.py test_gui` — fast.
- Full suite: `python tests/run_tests.py` — 282 tests, ~135 s, modules in worker
  processes; `-s` for the old sequential run.
- A new `test_*.py` must be added to `MODULES` in `run_tests.py` or `_unlisted()`
  fails the run.
- GUI off-screen pattern: `App(start_maximized=False)`, `geometry("<WxH>-4000+0")`,
  pump with `update()` + `sleep(0.02)`, then `destroy()`. `invalid command name
  ..._pump` on teardown is harmless Tk noise, not a failure.
- `python tools/debug_ui.py` sweeps the window lifecycle; must end `FAILURES: none`
  with `bpc_errors.log` clean.

## Hard rules (the ones that bite)

- **No pixel arithmetic in `gui.py`**; sizes live in `layout.py` and are tested at
  five resolutions. `review.py` has no Tkinter import — pure state functions, tested
  headlessly; `gui.py` is only the shell.
- **Optional backends install with `--no-deps`.** `simple-lama-inpainting` downgrades
  Pillow/numpy and breaks OpenCV in the same interpreter; a test bans `lama`/`ultralytics`
  extras from `pyproject.toml`.
- `H = K R K^-1`, always — a camera rotation, three DOF, cannot shear. Seeded RNG
  everywhere (a batch that differs on re-run is unusable). Confidence is multiplicative,
  so any single factor can veto.
- **Beyond the limit means refuse, not trim** (`--clamp-beyond-limit` restores the old
  cap behaviour). Magnitude test, not semantic: it cannot tell a ceiling from a wall,
  only that one asks for something no photographer plausibly wanted.
- `D:\Batch-Perspective-Correction` (a second, older copy) is **off-limits** per user
  directive — do not write, sync or run tools there.
- Anything fiddly belongs in Python, not in a `.bat`.

## Environment split (short version)

| | torch | transformers | tkinter | BiRefNet |
|---|---|---|---|---|
| system python.org 3.12 | yes (CUDA) | **absent** | yes | no |
| ComfyUI `python_embeded` | yes | yes | **no** | yes |

BiRefNet wants torch **+ transformers** + timm + einops; the GUI interpreter has no
transformers, so `--mask birefnet` runs from the ComfyUI interpreter via
`--mask-export` (writes one PNG per photo, consumed later through `--mask file`).
The full interpreter split is the table above.

M-LSD needs no second interpreter: its TFLite runtime (`ai-edge-litert`, the
declared `mlsd` extra) is installed in the **system** interpreter, so
`--detector mlsd|hybrid|union` run there directly. Reinstall with
`pip install --no-deps ai-edge-litert`.

## Ledger — the only place status lives

**Suite 2026-09-14: 285 tests, 2 failed, 2 skipped, ~171 s — NOT green.** Both
failures are the single receding-row photograph in the bug entry below;
everything else passes, including the three features the worker landed on 09-14
(theme switch, paste button, JPEG-quality spinbox — see Done). Re-run
before trusting this — it is a measurement, not a promise, and no entry below may
restate it. An item stays under **Open** until
nothing is left to do; **Done** is only for finished work. `Pn` labels are short
handles for work packages; entries written out in full here stand on their own.

### Open — do these

**Priority, set 2026-09-14.** The list below is not in order; this is.

**0. Commit. Nothing else matters until this is done.** 47 files, ~5,966
insertions and 55 untracked files sit uncommitted on a branch whose last commit
predates all of it, 4 ahead of `origin` and never pushed. That is two agents'
worth of a full day — the manual-first reshape, the mask brush, the overlay
moves, Qwen's theme/paste/JPEG work — one bad `checkout` or crash from gone. Not
a Ledger item, which is exactly why it kept being skipped. Commit in coherent
chunks, then push.

**1. Re-decide the batch-era caps as manual-first defaults.** Cheapest real
improvement per hour, and it touches every photograph reviewed. `max_horizontal_deg`
8 → 30 already proved the pattern: a cap reasoned for unattended batch was
throttling a correction the user wanted, and the fix was one constant. The same
argument is still unexamined for `max_pitch_deg`, the multiplicative confidence
veto, and P9's refuse-whole-correction recommendation. Each needs its own
measurement, not a blanket loosening — but each is a constant with a documented
sweep behind it, so the work is bounded.

**2. Click-to-select the building** (item below). The user's own preference, it
kills the failure that recurred all of 09-13 (BiRefNet chose two parked cars,
GDINO chose one building of a row), it completes the manual masking story the
brush started — brush for coarse, click for exact — and every piece is already
local. Bigger than #1 but the highest-value feature left.

**3. Decide the receding-row bug** (item below) — *decide*, not necessarily fix.
It is the only thing keeping the suite red, and a permanently red suite stops
being a signal. Either do the real work (make the focal estimate reject
horizontal evidence spanning multiple planes) or scope that photograph class out
of the round-trip gate deliberately and in writing. Leaving it red by default is
the one option that costs something every day.

**4. SAM3 via ComfyUI** (item below) is now partly superseded by #2 — a point
prompt is the same machinery with a better interface. Fold it into #2 or drop it;
do not build both.

**5. Distortion correction and the four `knowledge.md` research goals** stay
last: blocked on a go-ahead and on measurement passes respectively, and none of
them is what the product needs next.

- **[open] Click-to-select the building instead of guessing it (2026-09-13,
  user-directed, ref `github.com/Acly/krita-vision-tools`).** Every automatic
  subject finder tried here guesses, and today it guessed *two parked cars* on
  `39079116-...`. A point prompt does not guess: click the facade, SAM segments
  it. This is what SAM is designed for and is **simpler** than the box/text route
  already built, not harder. Pieces already in place: SAM2 (base-plus/large/small/
  tiny) and SAM3 weights local, BiRefNet wired, and the review canvas already
  handles clicks (planar corners, line brush, marks). Missing: a **point**-prompt
  path (only the box path exists, via `masks.gdino_box` → `gdino_mask`), plus the
  usual interpreter split (SAM needs the ComfyUI python, the GUI needs tkinter) —
  solve it the way BiRefNet already does, compute once and cache, rather than
  loading a segmenter inside the GUI process. **Fits the manual-first direction
  and only that**: a click is per-photograph, so this is a review-panel tool and
  can never be a batch default. **Licence**: krita-vision-tools is GPL-3.0 — take
  the idea, not the code; SAM2 itself is Apache-2.0 and already vendored.

- **[bug, diagnosed 2026-09-13, not fixed] An oblique *receding row* of facades
  is corrected confidently and wrongly — a real limitation, not a bad asset.**
  `39079116-ein-augenmerk-...-3Rec` is a legitimate architectural photograph
  (looked at it: a row of gabled townhouses on Münzstraße, shot obliquely, the
  street receding to the right, parked cars in front). Two tests fail on it:
  round-trip error **3.71°** against a 2.5° gate, at **conf 0.73** against a 0.4
  gate. **Measured across the whole 46-asset pool** (`analysis/` diagnostic, one
  run): every other asset lands ≤1.87°, most under 0.6 — this is a **2× outlier**,
  not a borderline case.
  **Why it is confident**: the verticals really are vertical, so the vertical VP
  is excellent — inlier share 0.911, stability 0.065°, horizon support 0.915, and
  every confidence term passes (share/count/spread 1.0, horizon 0.95, focal 0.84,
  stability 0.92). **Why it is wrong**: a receding row puts the *horizontals*
  — rooflines, eaves, window courses — on many differently-angled planes, the
  focal length is then derived geometrically from that mixed evidence
  (`focal_source='geometric'`, f=2433), and a wrong f buys a wrong pitch
  (13.5°) that fits the lines just as well. The classic pitch/focal degeneracy.
  **Dead ends, do not repeat**: (1) the mask is irrelevant — `test_assets.py`
  never references one, which is why the numbers were byte-identical before and
  after every mask change made today; (2) `horizontal_vps` is **3 for every asset
  in the pool**, so it cannot discriminate this case; (3) confidence does not rank
  error at all here (conf 0.05 → 1.57°, conf 0.77 → 0.38°), so no threshold tweak
  separates it without refusing good photographs too.
  **Not faked green.** The honest routes are: make the focal estimate reject
  horizontal evidence that spans multiple planes (real work, related to
  `knowledge.md` §1 facade-outline-first and to `ArchitectureScheme`); or accept
  that the round-trip gate encodes a *batch-era* promise ("what it touches does
  not come out wrong") that the manual-first direction has made negotiable, and
  scope this class out of that gate deliberately, in writing. **Blocked on: which
  of those two.**
  **Mask half, now closed**: `35559_XXL` got its missing mask, and
  `39079116-...`'s mask was inverted because plain full-frame BiRefNet segmented
  **two parked cars** as the subject (verified by looking at the PNG). Regenerated
  via the GDINO crop path (prompt "building", box score 0.56) —
  `test_birefnet.test_the_cached_masks_mark_what_to_ignore_not_what_to_keep`
  passes now. Note the inconsistency this leaves: that one asset's mask came from
  the gdino route while every other cached mask is plain BiRefNet, and
  `birefnet.export_masks` cannot reproduce it (it calls `build_mask` directly and
  never honours `mask_mode`), so the file is currently not regenerable by the
  documented command.
- **[open] SAM3 via ComfyUI as a second masking technique (follow-on, 2026-09-12;
  feasibility assessed 2026-09-13).** The tools field now hosts line editing *and*
  BiRefNet masking (see Done). The next exploration is a second masking route through
  ComfyUI, like BiRefNet's `--mask-export` path (one PNG per photo, consumed later via
  `--mask file`). **Assessment:** the HTTP side already exists — `inpaint._fill_comfy`
  (upload → `/prompt` → poll → download) is a ready-made ComfyUI client, and
  `masks.py:38` already names "SAM in ComfyUI" as the intended `--mask-export` consumer —
   so a SAM3 mask workflow would be a thin addition reusing that plumbing. **Not blocked on
   infrastructure after all (checked 2026-09-13):** every weight is already local in
   `D:\ComfyUI_windows_portable\ComfyUI\models\` — Grounding DINO (Swin-T OGC + Swin-B), SAM2
   (base-plus/large/small/tiny) and SAM3 (`sam3.pt`, 3.29 GB) — plus the matching custom nodes,
   so nothing needs downloading (the 403 egress is moot). Two routes: the ComfyUI server (a
   `GroundingDetector`→`Sam2Segment` graph), or **direct Python in `python_embeded`** with no
   server at all — `transformers` GroundingDINO + the official `sam2` package; recipe and pitfalls
   live in `.claude/skills/grounding-sam/SKILL.md`. The remaining gate is §3a's round-trip benchmark
   (beat BiRefNet / no-mask on the pool) before it becomes a default. Same two-interpreter story as
   BiRefNet (ComfyUI interpreter has no tkinter).
- **[open] Distortion correction (barrel/pincushion) — fully researched 2026-09-14,
  blocked on go-ahead to prototype Stage 0+1.** `H = K R K^-1` is a pure rotation and
  cannot touch radial distortion. **Full research now in `knowledge.md` §5** (APIs,
  licences, interpreter split, remap composition, trigger signal) — read that before
  implementing. Summary: Stage 0 `lensfunpy` (MIT, Windows wheels, returns per-pixel
  remap coords directly for `cv2.remap`) when EXIF identifies the lens; Stage 1
  AnyCalib (Apache-2.0, ICCV'25, `radial:k` k=1..4, ~25 ms on 4090, ComfyUI
  interpreter) blind fit for no-EXIF images; Stage 2 GeoCalib (Apache-2.0 code /
  CC-BY-4.0 weights, ECCV'24, accepts focal prior, enters `model.py` prior table as
  one row); Stage 3 cross-check gate (free, multiplicative confidence); Stage 5
  compose undistortion map + H into a single `cv2.remap`. Pipeline order: detect →
  undistort → correct. Trigger: monotonic angle drift in `merge_collinear` chains.
  Test case: `Aulendorf_Schloss_Fassade.jpg` (zero EXIF, fragmented lines). Must keep
  the 8% border guard. **Blocked on**: user go-ahead to prototype — new dependency,
  new pipeline stage.
- **[open] Four research goals in `knowledge.md` — read that file before picking any
  of these up (2026-09-13).** Full analysis, existing-measurement citations and
  external sources live there; this is the pointer plus the one-line scope of each.
  **None of these are implementation packages** — each needs its own measured
  comparison first, same discipline as the P9/pitch-cap passes. Don't guess at any of
  them; if the measurement says "no", write that down and stop, same as those did.
  1. **Facade-outline-first vs. partition-after-detect** (`knowledge.md` §1): does
     marking the two facades before detection recover corner cases like `lochfassade`
     that `ArchitectureScheme`'s post-hoc split under-weights (`h2` support 0.045 there
     vs. 0.36 on a confident corner)? Analysis-only until a comparison exists.
  2. **Dominant-edge hierarchy in the detector** (`knowledge.md` §2): a structural-edge
     tier (roofline, ground line, the corner seam) above ordinary window/course lines,
     orthogonal to `ArchitectureScheme`'s plane split. Needs a literature check on
     building-outline/roofline extraction (not done yet, flagged in the file) before a
     design, then the usual `tools/benchmark_detectors.py`-style before/after.
  3. **SAM reconsidered via SAM3's text prompts** (`knowledge.md` §3a): the original
     SAM rejection was the *invented selection criterion*, not SAM itself — SAM3 takes
     a text/concept prompt ("building facade") and removes that criterion at the root.
     Needs the same round-trip benchmark that killed SAM1 before it becomes anything.
     **Infrastructure landed 2026-09-13**: `.claude/skills/grounding-sam/SKILL.md`
     documents a detect-then-segment route (Grounding DINO text-prompts a "building"
     box, SAM2 segments inside it — SAM3 itself is also present locally as a one-model
     alternative, `models/sam3/sam3.pt`), all weights already local (this box has 403
     egress, nothing more can be fetched). `tools/probe_gdino_sam.py` is the probe
     script the skill's own §7 validation calls for. **First run crashed, not yet
     measured**: `analysis/gdino_sam_probe_run.log` — GDINO loads fine (0.9s), then
     `TypeError: string indices must be integers, not 'str'` at
     `probe_gdino_sam.py:105` (`d["label"]` assumes a dict; the actual detection
      result shape differs) — fix that before anything can be eyeballed or benchmarked.
      The skill's own gate (§5) still applies in full: a box overlay + mask to eyeball,
      then score against cached BiRefNet masks and no-mask on the worst case, only then
      a `mask_mode` slot. **Direction shifted 2026-09-13 (user):** keep BiRefNet as the
      matte engine ("don't drop it too fast") and test GDINO as a *crop in front of*
      BiRefNet rather than replacing it with SAM2 — that combination was measured and
      gives **no gain** on the current pool (`tools/probe_gdino_birefnet.py`, result
      recorded in `knowledge.md` §3a); `probe_gdino_sam.py` is superseded, not deleted.
  4. **A found-geometry overlay helper** (`knowledge.md` §3b): show the *specific*
     vanishing points/inlier lines the algorithm found on **this** photo (not the
     generic verticality grid, which already exists and is Done) — the rendering
     already exists twice over (`analysis/README.md`'s debug overlay,
     `ArchitectureScheme.draw_preview`); what's missing is a GUI toggle and a decision
     on which of the two to show by default.

### Done — rely on these

- **Clipboard paste + jpeg quality spinbox** (09-13, user: "möchte screenshots per copy paste einfügen"). Paste button (`add_paste_btn`) in addbar calls `App._paste_screenshot()`, which grabs the clipboard via `PIL.ImageGrab.grabclipboard()`, saves a timestamped JPG in the output dir at the configured quality, and feeds it through `_add`. Jpeg quality spinbox (`v_jpegq`, range 10–100) added to batch options grid (2,4), persisted via `trace_add` → `prefs.save(jpeg_quality=…)`. Pinned by `test_gui.test_paste_button_exists_and_handler_is_wired` and `test_gui.test_jpeg_quality_spinbox_in_batch_options`.
- **[bug] Before/after canvases stayed black after a theme switch** (09-13, user:
  "still bg black!"). `tk.Canvas` has no `-fg` option, so the old Canvas branch's
  `cget("fg")` raised TclError and aborted the branch *before* the background was
  re-tinted. Rewrote it as an independent per-option cget loop (gui.py ~280-294):
  each of bg/foreground/highlightbackground is read and swapped on its own, a bad
  option can no longer take the others down with it. Pinned by
  `test_gui.test_theme_switch_retints_canvas_backgrounds`.
- **Status box removed; detector moved Q4→Q3** (09-13, user: "status not needed
  anymore, move all detector stuff from 4 quarter to 3rd quadrant left bottom").
  The `tk.Text` status area and its copy button are gone from the review panel's
  top frame; `_set_status`/`_set_status_extra` are no-ops so the ~25 call sites
  throughout `gui.py` remain safe. The detector combobox and weights button now
  live in Q3 (`_build_tools`, the tools field, bottom-left) instead of the App-level
  batch options panel (Q4). `App.v_detector` is kept as a StringVar synced via
  `trace_add("write", …)` from the review panel's combobox so `_settings()` still
  reads the right value. The weights button is created on the App object
  (`app.btn_weights`) inside `_build_tools` so `_download_models()` can reference
  it for progress display. Four test assertions in `test_gui.py` that read
  `r.status.get("1.0","end")` were removed.
- **[bug] The mask brush did nothing from the second photograph onwards — fixed
  (09-13).** `v_stroke`/`v_stroke_w` were rebuilt by `_build`, which re-runs on
  every load, while the "Mask brush" checkbutton lives in the tools field, which
  is built **once**. After the first load the box set a variable nothing read and
  `_on_click_before` saw a fresh `False`. Every test passed throughout, because
  they all set `v_stroke` directly instead of pressing the widget — the exact
  "Tested is not reachable" trap this file names. Fixed by making them once, and
  pinned by `test_gui.test_the_mask_brush_still_works_after_a_second_photograph_loads`,
  which **presses the real checkbutton and sends real Tk events, after a reload**.
  **Audited for more of the same class**: every other tools-field variable
  (`v_alpha`, `v_detector`, `v_gdino_prompt`, `v_maskinv`, `v_maskmode`, `v_roi`,
  `v_roi_x0/x1`) is created inside `_build_tools` itself, so none can go stale.
  This was the only instance.
- **Brush preview fixed and restyled** (09-13, user: "yellow cursor is far off").
  It drew stroke points — stored in *image* coordinates — straight onto the
  *canvas*, so it sat a whole `_before_off` away from the pointer; and it used the
  radius as a width, so the guide was half the mark it left. Now offset correctly,
  at the true diameter, solid dark red instead of dashed yellow (a marquee reads
  as a selection, not as paint).
- **Grid draws on the corrected pane only** (09-13, user-directed). It was being
  drawn on both. On the original it measures nothing and competes with the
  detected lines; on the result a true vertical should run along a grid line,
  which is the whole point. Pinned in `test_the_grid_never_reaches_the_saved_file`.
- **"Check lines": re-detect on the corrected frame** (09-13, user-directed
  diagnostic). A switch on the after pane runs the detector over the *result* and
  draws what it finds — green where a line came out truly vertical/horizontal,
  red/orange where it still leans. The direct way to see a correction that came
  out too weak, instead of inferring it from the before pane. Off by default;
  runs on the preview-sized array, never full resolution.
- **`max_horizontal_deg` raised 8 → 30** (09-13, user: "horizontal correction is
  often way too weak"). The 8 was reasoned for unattended batch, where a wrong yaw
  shears a frame nobody looks at; P9 measured the real single-VP yaw on this pool
  at **16–70°**, so the cap was clamping it to a fraction of what the geometry
  asked for. 30 matches the manual slider, so automatic and by-hand now reach the
  same place. The shear risk is unchanged and real — it is simply seen by the
  person reviewing before anything is written. First consequence of the
  manual-first direction reaching a measured default; the others (pitch cap,
  confidence veto, P9's refuse-whole) are still open.
- **Tools live on the picture now** (09-13, user-directed): the before pane's
  top-left corner is a vertical palette — a bigger `+`, the folder icon, then
  Mask brush / Mark / Planar as toggle buttons (`indicatoron=False`, so a tool
  reads as held). Everything you can do to the original is in one column on the
  original.

- **The brush paints the mask now, instead of erasing individual lines** (09-13,
  user-directed: "use brush to expand mask manually — for example killing the
  car"). Renamed **Mask brush**. `review.paint_ignore(pts, display_scale, radius,
  erase=False)` maintains a hand-painted region at analysis resolution, merges it
  into the shown mask so the red wash grows as you paint, and strikes lines the
  paint covers **end to end** — `drop_by_endpoints`, the same rule the automatic
  mask uses, so a facade edge that merely crosses the painted area keeps its say.
  Re-derived from the region on every stroke rather than accumulated, which is
  what makes erasing work. Deliberately skips the two heuristics that
  second-guess a *computed* mask — `protect_structure` (hands long lines back)
  and `credible` (can refuse a mask outright): what the user paints is a decision,
  not a hypothesis. Survives a re-detect (`_detect` re-applies it), so changing
  detector or mask source no longer discards it. Gestures are Photoshop's: **left
  paints, right erases, Alt+right dragged sideways sizes the pen**. This is the
  manual answer to the problem that ran all day — BiRefNet picked two parked cars
  as the subject, GDINO picked one building; a stroke ends the argument.
  `test_review.test_painting_the_mask_strikes_what_it_covers_and_erasing_hands_it_back`
  + `test_gui.test_the_mask_brush_paints_the_ignore_region_and_erases_it_again`.
  **Supersedes `erase_lines_in_stroke`, which is deleted** along with its tests —
  painting subsumes it (the mask drops those lines anyway) and the user asked to
  drop per-line annotating.
- **Overlay switches moved onto the images they draw on** (09-13, user-directed):
  `Lines` and `Mask` to the top-right of the *before* pane, `Grid` to the
  top-right of the *after* pane, both `place`d over the canvas like the add icons
  in the before pane's top-left. A switch for an overlay belongs on the picture it
  changes, not in a box across the window; and the grid in particular is the ruler
  you judge the *corrected* frame with, so it belongs on that frame. Built in
  `_build` beside their canvases — `_build` re-runs on every load, so a bar
  parented to the previous canvas dies with it; the variables are made once so the
  switches don't flip themselves back on each photograph. `_mk_show`/`_mk_mask`
  deleted (their only callers were the old row).
- **Masks get a morphological close before the shrink** (09-13, user-reported thin
  stripes, with a screenshot). `build_mask` now closes (dilate then erode, default
  `close_frac=0.004`) before the measured `shrink_frac` erosion. The matte leaves
  thin unmasked slivers where it runs between structures, and the shrink is an
  erosion, which eats a thin region entirely and leaves broken stripes. Closing
  first fills them without moving the silhouette, so it cannot disturb what
  `shrink_frac` was measured against. Kept deliberately smaller than the shrink:
  it is for speckle, not for reshaping the subject.
- **The options bar says what it is**: titled "defaults every photograph opens
  with" (09-13). "detector", "mask" and "fill" appear *twice* in this window and
  nothing said which was which — `_settings()` feeds these to every
  `review.load`, and the panel's copies override them for the photograph on
  screen. Same controls, now legible.

- **The prominent run button now *asks* instead of writing unattended** (09-13,
  user-directed "focus on manual; if batch then image by image"). The accent
  button in the run bar is `Review each` (`Review` for a single photograph) and
  calls `_review_each`, which opens one review window per image and writes only
  on Save; the old fire-and-forget run is demoted to a plain `Unattended` button
  (`btn_batch`) beside it — **demoted, not deleted**, and both go disabled while
  a run is writing so a review walk can't race it. Nothing about `_review_each`
  itself changed: the walk, its `[n/total]` position label and its close-advances-
  the-queue chaining already existed and were simply not the default.
  `test_gui.test_the_prominent_button_asks_rather_than_writing_unattended` pins
  it by *pressing* the button and checking no worker thread starts — a button
  labelled Review but wired to the batch is the failure worth catching, and only
  invoking it tells the two apart.
- **`masks.build` accepts several sources at once** (09-13): `mask_mode` may be
  one name as always ("birefnet") or comma-joined ("file,birefnet"), in which
  case the ignore regions are **added** (a pixel is ignored when any source
  ignores it). Single values behave exactly as before, so every caller, CLI flag
  and remembered preference is untouched. `_build_one` holds the per-source logic.
  **Currently reachable from nothing** — the GUI checkbox UI that would drive it
  was started and reverted when mask work was paused, so this is a seam with no
  caller: the gotcha this file already names. Either wire it or drop it; don't
  leave it a third time.

Condensed 2026-09-13 (see Governance) — one to a few lines each: what shipped, the
date, and the pinning test if any. Full narrative for anything still worth arguing
about lives in `knowledge.md` (research goals) or `docs/worker-environment.md`
(worker/tooling); everything else, the code and tests are the source of truth for
*how* — this list is only the record *that* it happened.

- **Review-window UI polish: tooltips, movable mark endpoints, Alt+click mask erase, Mask Apply, loupe in mark mode** (09-13, user-directed). `_attach_tooltip` (gui.py ~473) adds a `tk.Toplevel` tooltip to all ~30 interactive buttons; rubberband mark lines gained draggable endpoints for refinement; Alt+Left-click erases mask paint (same as right-click, gui.py 2414); "Mask Apply" button strikes lines covered by painted mask (gui.py 2208); full-resolution loupe magnifier follows the cursor when dragging planar corner handles (gui.py 1539-1588). `test_gui.test_the_mask_brush_paints_the_ignore_region_and_erases_it_again`, `test_gui.test_planar_corners_can_be_placed_and_dragged`.
- **Loupe click forwarding + drag tracking** (09-14, user-reported "loupe not working on click" then "loupe still fixed when moving handle"). Two fixes: (1) `_loupe_show()` now binds `<Button-1>`, `<ButtonRelease-1>`, `<B1-Motion>` on the loupe canvas and relays them to `c_before` via `event_generate` at the equivalent coordinate — the loupe floats above `c_before` and was swallowing clicks. (2) `_on_before_b1motion` now calls `_loupe_move(event)` first, so the loupe tracks the cursor during drags (`<B1-Motion>` fires instead of `<Motion>` while a button is held). Checkbox indicators enlarged ~25% via `indicatorwidth=16, indicatorheight=16` on the TCheckbutton style (same session, user-directed "25% larger").
- **FIND controls moved to the input side; review panel is edit-only.** The line
  detector + ROI x left the lower-right adjustments panel (`leftcol` removed) and now
  live in the bottom-left tools field beside the before-image (`_build_tools`) — what
  the estimator *sees* belongs on the input side, not with the angles that edit the
  result (09-13, user-directed; verified by off-screen position dump). The panel is a
  single edit column now. This supersedes the "three-zone within the panel" proposal in
  the layout-ergonomics item it closed.
- **ROI strip auto-enables horizontal correction** (09-13, fixes "ROI shown but not
  applied"): ROI x only restricts *horizontal* evidence → yaw, and yaw is gated on
  `correct_horizontal` (`model.py:426`, `warp.py:49`) which defaults off — so the strip
  changed nothing until that flag was set. `_apply_roi` now turns the flag on (and calls
  `_on_horizontal_toggle()`) when a valid strip is set; clearing the strip does not force
  it back off. `test_gui.test_enabling_an_roi_strip_turns_on_horizontal_correction`.
- **Batch Output destination moved to the Start/Stop bar** from the loader field — an
  output concern riding the run controls' existing height (the options frame above has no
  vertical headroom; the cross fixes field height). `_w_out` packed right on `bar`, path
  field before the progress bar so it keeps width at 1280 (09-13). Closes layout-ergonomics ask #1.
- **Window floor raised to 1920×1080** (`self.minsize(1920, 1080)`, `gui.py`): the cross
  needs a full field per quadrant, so the old 960×640 floor let a resize starve the
  canvases. Off-screen tests updated to the new floor (09-13).

- **Controls `btns` row no longer clips at any window size** (09-13, full fix for
  the overflow bug — closes the Open item that had called this "partially fixed").
  Two mechanisms: (1) the action row (Save/overwrite/Close/Keep original) reaches
  every size because `btns.pack(side="bottom", fill="x")` now runs first in the
  assembly, so it claims its strip before the picture does — the picture yields
  height, never the buttons; paired with the adaptive adjustments-panel default
  `layout.adjustments_start_open` (open only if the window height can afford both
  controls and a usable picture; `_adapt_adjust_default` applies it and stands down
  permanently the instant the user touches the toggle by hand). (2) The row's own
  width overflow is solved by splitting it into two rows — `self._btns` (Auto/Reset
  left, Save/overwrite/Close/Keep right) and `self._btns2` (Mark/kind/Planar/Clear
  marks/Strike slanted/Auto crop/Reset crop) — and moving the display overlays
  (Lines/Mask/Grid/grid-step) out of the row into the lower-left tools field
  (`_build_tools`), so each row fits the ~742–1230 px field at the 1920 floor.
  `test_gui.test_the_save_button_is_reachable_at_every_window_size` (now asserts
  every child of both rows has width > 0) +
  `test_the_panel_default_keeps_both_the_buttons_and_a_usable_picture` +
  `test_a_hand_made_choice_about_the_controls_outranks_the_height` +
  `test_collapsing_the_adjustments_leaves_the_cross_where_it_was`; measured by
  `analysis/verify_btns_fit.py`. **Supersedes** the "three sub-rows" IA redesign this
  bug's Open entry used to propose — that specific proposal was not what got built;
  this is a narrower, different fix (assembly priority + adaptive default + row
  split), not the redesign.
- **Folder-add icon redesigned**: one continuous polygon silhouette (a body whose
  top edge steps up into a short tab over the left half), replacing the old two-
  overlapping-rounded-rectangles blob that was confirmed unrecognizable at 18px
  (09-13, `_folder_pil`, `gui.py`). **Visually verified by the architect** at 18px
  and 8x (`analysis/folder_final_18.png` / `folder_final_8x.png`, rendered via
  `analysis/render_folder_final.py` from four candidates) — Qwen cannot view images
  itself (confirmed directly from its own reasoning trace while working this item),
  so this item's "visually checked before done" gate routes through rendered
  artifacts plus a sighted reviewer, not a worker self-check. No dedicated shape-
  pinning test, only the pre-existing `test_gui.py:673` mapped-presence check — a
  future edit could silently regress the shape.
- **Crop rectangle gained mid-edge handles**, not just corners: a drag on an edge
  midpoint moves just that edge along its axis (top/bottom vertically, left/right
  horizontally) instead of re-placing all four (09-13, `_grab_handle`, `gui.py`).
  `test_gui.test_the_mid_edge_handles_move_one_edge_on_its_axis`.
- **Enabling the `roi_x` strip now turns on `correct_horizontal` with it** (09-13)
  — the strip only shapes the yaw, and yaw is gated on that flag (off by default),
  so a strip that left it off changed nothing. One-way: clearing the strip again
  does not force the flag back off, since that may now be a deliberate choice.
  `test_gui.test_enabling_an_roi_strip_turns_on_horizontal_correction`.
- **`roi_x` got a real interface**: mouseover tooltip + two draggable vertical
  rulers on the before-canvas, defaulting to 20%/80% (not the old useless 0/100),
  clamped to the frame edge and to each other so the strip can't collapse or be
  swiped away; percent spinboxes stay as a secondary fine-tune (09-13, user-directed).
  `test_gui.test_roi_x_draws_two_draggable_rulers_defaulting_to_20_and_80`. The
  auto-derived default from a GDINO box (`grounding-sam/SKILL.md` §6) is a later,
  separately-measured refinement — this is the baseline.
- **Mask opacity works with "Lines" off** (09-13) — `render_before` drew the mask
  wash only when lines were shown; now independent.
  `test_review.test_mask_opacity_is_adjustable_and_zero_means_invisible`.
- **"subfolders"/"overwrite originals" duplicate checkbox fixed** (09-13) — one pair
  now, not two. `test_gui.test_the_batch_bar_has_each_setting_exactly_once`.
- **`--mask gdino` shipped**: 4th mask_mode, text-prompt box (GDINO) crops, BiRefNet
  mattes inside it; opt-in, not default (09-13). Needed `kornia` (`--no-deps`) to
  unblock BiRefNet loading in-process. 6 `test_masks` tests +
  `test_gui.test_the_gdino_prompt_field_and_mode_are_reachable`. Not a measured win on
  clean assets (see the GDINO-crop finding below) — ships as an alternate entry point /
  competing-foreground fix.
- **Pitch cap raised 20°→30°** (09-13, user-directed, overriding the measurement
  below). `config.py max_pitch_deg = 30.0`.
- **BiRefNet-lite checkpoint support** added (`_arch_file` routes "lite" names to
  `birefnet_lite.py`) (09-13). `test_birefnet.test_the_lite_checkpoint_uses_its_own_network_file`.
  **GDINO-crop-then-BiRefNet measured no gain** over plain BiRefNet on 3 assets (IoU
  0.975–1.000) — `tools/probe_gdino_birefnet.py`, recorded in `knowledge.md` §3a. (This
  did not stay a stopped probe — it shipped anyway as `--mask gdino` above, a looser
  reading of §3a's gate than this measurement supports; noted once, here.)
- **Crop rectangle gains a pan gesture**: press-inside drags the whole rect, clipping
  to the frame at an edge (09-13, user-directed).
  `test_gui.test_dragging_inside_the_crop_pans_it_and_clips_to_the_frame`.
- **Cached-mask pool completed** (6 assets generated) **and a real bug fixed**:
  `cv2.imread` mangles non-ASCII paths on Windows — added `masks._imread`
  (`np.fromfile`+`imdecode` fallback) (09-13). A mojibake mask filename and two
  legitimately-degenerate masks (routed to the existing `MK.credible` refusal) were
  also cleaned up in the same pass.
- **P13 decided**: division/fraction grid modes stay bare, by construction
  (`_grid_step` returns `step=0` → `_draw_rulers` no-ops there) (09-13). No code
  change needed — rulers remain pixel-mode-only, top/left/right edges.
- **P10 done**: the 2 actually-missing assets (of the 4 the item named — 2 had
  already landed) were tracked-but-deleted, restored via `git checkout --`, no
  network needed (Commons is 403 from this box regardless) (09-13).
- **`ArchitectureScheme` wired in** behind `config.use_scheme` (default `False`) /
  `--scheme` (09-13) — off by default since it only ever removes evidence.
  `pipeline.analyse` re-derives vert/horiz from survivors when it fires; summary
  logged to `detect_info`. `test_pipeline.test_use_scheme_partitions_lines_and_is_off_by_default`
  + `test_schemes` (7). **Do not build a second classifier for this under a different
  name** — checked against a prompt referencing `XiaohuLuVPDetection`/`GlobustVP`/
  `vp-toolbox`/`perspective-control` on 09-12, this class already covers it. A more
  outlier-robust VP search belongs in `vanishing.py`, not a new class; `vp-toolbox`'s
  J-linkage drops the Manhattan-orthogonality assumption `H=KRK^-1` depends on, a
  core-model change, not a line-filter tweak.
- **P9 (yaw policy) measured, analysis-only** (09-13): over 33 assets with
  `correct_horizontal=True`, refuse-whole-on-limit throws away 25 photos whose
  roll+pitch were safely within cap — only yaw (16–70°) breached; only lochfassade
  breached multi-axis. **Recommendation: refuse only on roll/pitch breach; drop yaw
  and keep levelling when only yaw breaches.** Not implemented — a separate
  `warp.limit`/`pipeline.analyse` decision, and only matters once `correct_horizontal`
  is opted into (default off).
- **Pitch cap measured, analysis-only** (09-13): 32/33 assets ≤19.5°, only
  lochfassade at 28.6° exceeded 20°; forcing it through showed a fill-smear + a
  trapezoidal far-facade (expected — one rotation can't square two non-coplanar
  planes). Recommended keeping the cap at 20 — **overridden the same day**, see
  "Pitch cap raised to 30" above; kept here as the measurement the override was
  weighed against.
- **P16 done**: rubberband marking takes `kind="v"|"h"` now — a horizontal-kind mark
  drives yaw via `min_horizontal_support=0`; mark-line width scales with image size
  (09-12). `test_review` (4 new) + `test_layout.test_the_mark_line_thins_out_on_small_photographs`.
- **BiRefNet's process-wide `subprocess.check_output` monkeypatch scoped** to a
  contextmanager around just the torch import (09-12) — stopped leaking into other
  callers.
- **`tests/assets/Horizontal/` wired into `test_assets.py`**, `*_corr.*` outputs
  filtered out so the test doesn't grade its own homework (09-12).
- **`skills/ui.md` rewritten** for the cross layout + the widgets that landed since
  (Spinboxes, mark-kind combobox, rulers, loupe) (09-12).
- **Bottom-left field became the persistent tools area**: line-brush + masking
  controls (incl. a working mask-opacity `tk.Scale`) moved there from the old
  lower-right row (09-12, user-directed).
- **Review-panel columns read "find vs. edit"**: detector in `leftcol`, angle
  sliders + fill/mask/ComfyUI in `rightcol` (09-12, user-directed). Pure reparenting.
  **Superseded 09-13** — the FIND controls (detector + ROI x) left the panel entirely
  for the bottom-left tools field; the panel is now a single edit column (see the
  "FIND controls moved to the input side" entry above).
- **Loader minimized** to two grey add-icons overlaid on the before-image + a
  save-folder row; file listbox removed (09-12, user-directed) — this also fixed a
  3px results-tree collapse at 1280×800 that the listbox's height demand was causing.
- **Line-brush stroke tool**: drag over the before-canvas erases every candidate
  line it touches — a pencil, not a toggle; sweeping the same path twice is
  idempotent, one `refit()` per stroke (09-12; **corrected 09-13** — this shipped as
  a toggle/flip and was documented as one here, but the code is now erase-only:
  `erase_lines_in_stroke`, was `toggle_lines_in_stroke` — the old entry was a wrong
  done). Refined 09-13 (user-directed): default width 24→10; struck lines now
  *vanish* from the before render instead of lingering grey (`render_before` no
  longer draws disabled lines in `PV.GREY`), so an erased line stays gone; the drag
  preview is a temporary saturated-amber dashed brush (`#ffd000`, denser dash) that
  clears on release while the erasure persists — pale `#ffe14d` was invisible on
  light facades. `test_review.test_a_stroke_erases_every_line_it_crosses_and_is_idempotent`
  + `test_gui.test_line_brush_stroke_erases_lines_and_clears_its_preview`.
- **`ReviewPanel._apply_mask` no longer crashes on a missing `v_maskpath`** (09-13,
  surfaced by an off-screen probe). It read `self.v_maskpath`, but that StringVar is
  created on the App (batch options), not the ReviewPanel — so picking birefnet/gdino
  with no stored model threw `AttributeError`. Now it reads the App's remembered
  `"birefnet_model"` (mode-independent, unlike the mode-dependent `v_maskpath` field)
   via `self._app()`, falling back to prefs under a test root.
- **Mask-mode label renamed "source" → "mask"** (09-13, user-directed): the tools-field
  label beside the mask-mode combobox (off/file/birefnet/gdino) now reads "mask". One-line
  text change at `gui.py` (`_build_tools`), nothing else on that row moves.
- **Hough detector removed** (09-12, user call — too noisy, and was a silent
  fallback even when a different detector was explicitly picked). `detect_segments`
  now returns empty rather than degrading to it. `test_detectors.py`/`test_prefs.py`
  updated for the removed name.
- **Off-screen test coverage added for planar corners** (`debug_ui.py` +
  `test_gui.test_planar_corners_can_be_placed_and_dragged`), closing the gap that let
  P11/P12 (below) sit broken undetected for a day (09-12).
- **Loupe crash fix**: `w.lift()` is the wrong Canvas API for raising a window (it's
  the *item*-stacking call); now `w.tk.call("raise", w._w)` (09-12).
- **Planar Save fix**: `_save` was writing the roll/pitch correction even with 4
  planar corners placed, silently discarding them. Now branches on
  `v_planar.get() and len(planar_quad)==4` (09-12).
  `test_gui.test_save_routes_to_planar_when_four_corners_are_placed`.
- **P15 done**: before/after panes show source/destination filenames,
  middle-truncated so the extension always survives (`_shorten_middle`) (09-12).
- **Grid-spacing combobox made genuinely editable** — was still `readonly` despite
  an earlier claim otherwise (09-12).
- **Angle/focal sliders gained paired Spinboxes** for fine control; flex rulers (P13)
  added on the after-canvas, pixel mode only (09-12). `layout.ruler_ticks` +
  `gui._draw_rulers`, `test_layout.test_ruler_ticks_*`.
- **Ruler ticks added to the right edge too**, for counter-checking level — same
  y-positions as the left ruler (09-12, user-directed).
  `test_gui.test_the_ruler_reads_the_same_height_on_left_and_right`.
- **M-LSD unblocked in the GUI interpreter** — needed `ai-edge-litert`
  (`--no-deps`) (09-12). 2 tests un-skipped.
- **Perfect cross UI, window = cross** (09-12). Four exactly equal fields — before/
  after on top, loader + controls below — a flat **20px dark cross** and **20px dark
  border** (`CROSS_GAP`/`CROSS_BORDER`, `layout.py`; `INK["cross"]`, `gui.py`). No
  PanedWindow, no results strip: the results tree lives in the loader field, the
  batch bar at the foot of the controls field. Pinned by `test_the_cross_is_four_equal_fields`,
  `test_the_window_is_the_cross_and_nothing_else`,
  `test_the_perfect_cross_is_flat_twenty_on_every_real_screen` (five screen sizes).
  **Closed decision** — the cross and the loader's `+` icon are what the user wants;
  do not propose layout changes.
- **P14 done**: manual yaw slider to ±30° (`max_horizontal_deg` unchanged at 8 —
  that's the auto-estimator's cap, a slider is a person deciding, not a guess).
- **Version series starts at 1.0** — 0.x was never user-visible.
- **P11+P12 done**: planar corner placement/drag and the loupe both work (an
  earlier CLAUDE.md claim that `_loupe_show`/`_loupe_hide` didn't exist was already
  stale when written).
- **Slim live CLAUDE.md**, full history split out (that archive, `claude_save.md`,
  was itself deleted 2026-09-13 once carried forward into this file and
  `knowledge.md`) — 09-12.
- **Batch-options bar regrouped**: detector+params left (cols 0-5), output
  (mask/fill/server/checkboxes) right (cols 7-11) (09-12, user-directed).
- **`ttk.Scale` crash on window open, fixed** (09-12) — `ttk.Scale` doesn't take
  `width`/`sliderlength` (that's `tk.Scale`'s API); an in-flight styling change had
  added them to three sliders, crashing the whole window on open. Removed;
  `layout.SLIDER_WIDTH`/`SLIDER_THUMB` stay defined but unused pending a proper
  `ttk.Style` pass, if that's still wanted.

### Repo note

Nothing on this branch is committed yet. `git status` is otherwise clean — no scratch
files at the root, and the untracked additions (`skills/`, `scheme.py`
+ its test, `tools/debug_ui.py`, `tools/worker_bench.py`, `tests/test_cli.py`,
`run_bpc_gui.bat` replacing the deleted `run_gui.bat`, the `Horizontal/` assets and the
newer top-level ones) are all wanted. Nothing to delete or move. The commit-readiness
caveat this note used to carry (`scheme.py` unwired, the monkeypatch unscoped) no longer
applies — both landed (see Done).

## Gotchas (each cost real time)

- Tk fires **no `<Configure>`** for a widget re-packed at the size it had — so a
  `pack_forget()`/`pack()` round trip reschedules nothing; only a genuine geometry
  change (window resize) will. The window's `minsize(1920, 1080)` also means no resize
  can starve the canvases (<20 px) — an unmapped canvas is the only reachable
  starvation branch now (`test_the_preview_gives_up_retrying...`).
- Tk 8.6 does **not** fire `<<ListboxSelect>>` for programmatic `selection_set`; call
  `_on_list_select()` by hand after programmatic selections (the guard makes repeats
  no-ops).
- A test written to match observed behaviour certifies the bug and goes red when
  somebody fixes it. Assert what the code is *for*, against the constant that defines
  it, never the number it currently prints.
- The round-trip test must keep the border guard (8 % crop) or `BORDER_REPLICATE`
  smears edge pixels into long straight streaks the detector reads as lines — that
  artifact produced two confident wrong conclusions.
- Caches key on the **full** argument tuple; hit-check is `key in cache`, never
  truthiness (`None` is a real result).
- Synthetic scenes are right for geometric questions with ground truth, wrong for
  statistical ones about real texture.
- **Tested is not reachable.** `roi_x` and `ArchitectureScheme` sat correct, covered and
  switched on by nobody for a day — no flag, no control, no caller. Both are fully
  wired now (CLI flags, and `roi_x` picked up a GUI control too, 2026-09-13). A seam
  with tests is half a feature; "done" means someone can use it. Check for the caller
  before writing a done entry.
- **Two lists that must agree will not.** P11/P12 were implemented while a second
  section still called them "not started", because the status lived in two places. One
  ledger, one entry per item — if you are about to note a status twice, you have found
  the same bug again.

## Worker (local Qwen3.8-27B)

- CLI: `qwen -m "unsloth/Qwen3.8-27B-GGUF" -p "<package>"`. Server
  `http://127.0.0.1:8888/v1`. Key lives in `~/.qwen/settings.json` under
  `env.UNSLOTH_API_KEY` — read it at call time, never paste it into a script.
- **Check `/v1/models` for `loaded: true` before blaming anything.** A 404 "downloaded
  but not loaded" is the server state, not a broken install.
- Sampling (Qwen3.8 card, bench-verified): thinking `T=1.0 top_p=0.95 top_k=20
  presence_penalty=0`, non-thinking `T=0.7 top_p=0.8 top_k=20 presence_penalty=1.5`.
  **Never temperature 0** (Qwen3 degrades into repetition). Give `max_tokens` real room
  (≥32768) — a thinking model with a small budget returns empty `content` and a full
  `reasoning_content`.
- **Decided settings (bench 2026-09-12, `tools/worker_bench.py`, history in
  `analysis/worker_settings/history.jsonl`):** default packages to thinking with
  `reasoning_effort=low` (~25 % fewer completion tokens than medium, equal pass rate on
  coding tasks); `/no_think` for mechanical sub-steps (fastest: 8.3 s vs 11.1 s mean);
  escalate to `medium` only after a package fails twice. The effort knob works per
  request via `chat_template_kwargs`.
- **The chat template is a custom build, not stock Qwen3, and strict thinking-off
  *is* exposed per request** (corrects an earlier claim in this file) — plus a
  token-efficiency lever (`preserve_thinking`, now defaulted off) and a confirmed
  llama.cpp server bug (`reasoning_tokens` always reports 0 in `usage`, harmless).
  Full detail, sources and the launcher/`.bat`/resume notes from the 2026-09-13
  debugging session: **`docs/worker-environment.md`**.
- **Temperature is not a lever (sweep T=0.3/0.4/0.5/0.7/1.0, effort low):** all pass
  every task at every point; wall time flat at 11–13 s, completion tokens 607–764.
  Keep the card's `T=1.0` for thinking mode — lowering it buys nothing here.
- **Bench tasks:** t1 spec-following, t2 grid arithmetic, t3 boundary bug fix
  (debug test), t4 novel coding task. For quality checks use problems **newer than the
  model's training data** — Qwen3.8 shipped Aug 2026, so take contest problems from
  Sept 2026+ (t4 = AtCoder ABC 474 B "Exit Order", held 2026-09-06, official samples +
  statement-derived edge cases). Public 2021-era benchmarks (MBPP, HumanEval) are
  contaminated — the model has seen them.
- **Self-testing: objective verifiers only.** LLM-as-judge has documented
  self-enhancement, position and verbosity biases (Zheng et al., arXiv 2306.05685) —
  never let Qwen grade Qwen's own code. The worker runs the project's tests; the
  architect reviews against them. Vendor evals are likewise objective-benchmark based
  (`QwenLM/Qwen3` `eval/`, resumable inference scripts).
- Context is **94 848** (measured from the server, not the card). An agentic CLI
  re-prefills the whole context on every tool round trip, so the lever is removing
  exploration: look up names yourself (`correct_horizontal`, `Result.roll_deg`, …) and
  hand them over. The architect does the reading.
- Package contract: GOAL / SCOPE / CONSTRAINTS / VALIDATION — full method and ten
  lessons in `skills/delegation.md`.
- **Next packages, ready to hand over.** Each open ledger entry already names its files,
  symbols and line numbers — paste the entry in as SCOPE verbatim; the worker should
  never have to grep for a name. Check the Open list itself for what's current rather
  than a fixed list here, since it has been overtaken three times already. As of
  2026-09-13 the open items are the distortion roadmap, the SAM3-via-ComfyUI follow-on
  (both blocked on external go-aheads/infrastructure) and the four `knowledge.md`
  research goals below — **all four are measurement/research passes, explicitly not
  implementation packages yet**: no `src/` change until each has its own measured
  comparison, per that file's "Proposed test, not a change" sections.

## Skills

- `skills/ui.md` — INK palette (single colour source), one-window structure,
  review-panel rules, off-screen test pattern.
- `skills/delegation.md` — how to write a worker package; the lessons are worth more
  than the contract.
- `docs/worker-environment.md` — chat-template internals, the llama.cpp
  `reasoning_tokens`-always-0 bug, and the launcher/`.bat`/resume notes; the Worker
  section above only points here.
- `.claude/skills/debug/SKILL.md` — benchmark / BiRefNet failure workflow, the
  two-Python setup, Windows pitfalls.
- `.claude/skills/grounding-sam/SKILL.md` — Grounding DINO + SAM2 detect-then-segment masking,
  direct Python (no ComfyUI server); local model paths, the §3a benchmark gate, roi_x synergy.
- `knowledge.md` — analysis + external sources for the four remaining research goals
  (facade-outline-first, dominant-edge hierarchy, SAM3, found-geometry overlay); read
  before picking up any of them.
