# Perspective Correction — working notes (live)

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

Implementation is done here directly. **The local Qwen3.8-27B worker is no longer
paused (2026-09-15)** — it acts through `tools/worker_agent.py`; see the Worker
section and `docs/worker-anleitung.txt`. `docs/qwen-knowledge.md` records why it was
shelved and is history now, not instruction.

## Running things (do not rediscover this)

- Project root `D:\Coding\Perspective-Correction`, Windows, PowerShell 7,
  Python 3.12.9 (python.org), 32 cores.
- Compile: `python -m py_compile src/pc/gui.py` (and anything else touched).
- One module: `python tests/run_tests.py test_gui` — fast.
- **The runner has two modes since `74da5a9` (09-14) — the bare command is no
  longer the full suite.** `python tests/run_tests.py` is the *fast* run: 264
  tests, ~24 s, deliberately skipping `test_gui` and `test_assets` (real Tk
  windows and real photograph sweeps, together most of the old wall time). It
  says so on exit — `!! FAST RUN -- did NOT run: test_assets, test_gui` — and
  **green there does not mean green**. Full suite: `python tests/run_tests.py
  --full` (or `PC_FULL=1`) — 306 tests, ~55 s, modules in worker processes; `-s`
  for the old sequential run. The line this replaces claimed "282 tests, ~135 s"
  for the bare command, which now measures neither that count nor that set:
  any suite number quoted anywhere must say which mode produced it.
- A new `test_*.py` must be added to `MODULES` in `run_tests.py` or `_unlisted()`
  fails the run.
- GUI off-screen pattern: `App(start_maximized=False)`, `geometry("<WxH>-4000+0")`,
  pump with `update()` + `sleep(0.02)`, then `destroy()`. `invalid command name
  ..._pump` on teardown is harmless Tk noise, not a failure.
- `python tools/debug_ui.py` sweeps the window lifecycle; must end `FAILURES: none`
  with `pc_errors.log` clean.

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
- **Every colour comes from `INK`, and anything RENDERED must be re-rendered.**
  The palette is the single source; a hex literal in a widget option cannot be
  retinted and will sit there in the old theme's colour forever. Two traps, both
  paid for on 2026-09-15:
  1. `_retint_bg` matches widgets **by class**. It walked Frame, Canvas, Label
     and Button, so the entire tool palette — `tk.Checkbutton` — was never
     visited. Add a widget class, add it to that list.
  2. **A walk cannot recolour an image.** Icons and the brand mark are
     `PhotoImage`s: the tint is in their *pixels*, baked at build time.
     Re-optioning does nothing; they have to be drawn again. `_switch_theme`
     therefore rebuilds the palette and re-renders the mark.
  The pattern to copy is `_logo_image`: `Logo_BPC.png` is a black silhouette and
  only its **alpha channel** is used — the colour comes from the palette, so the
  mark follows the theme instead of fighting it. Any new icon does the same via
  `_icon_pil`, which takes the tint as an argument for exactly this reason.
  **The check is one gesture:** switch the theme and look for anything still
  wearing the old colours. Sizes obey the same idea one rule up — they live in
  `layout.py`, colours live in `INK`, and neither is typed into a widget.
- **Mechanical work goes to the worker; the architect measures and decides.**
  (user, 2026-09-15, after watching it go the other way all day.) It is local and
  free, and every hour it sat idle was an hour of hand-editing that bought
  nothing. The split that works:
  * **Worker**: deletions, renames, applying a written spec, a pattern in several
    places, and *read-only hunts* — it is genuinely good at those and cites
    `file:line` for every claim. Write the package with every name supplied; it
    cannot grep for what you failed to tell it.
  * **Architect**: deciding what should be true, and **running the thing to find
    out what is**. That is the half a worker cannot do — it has no shell.
  The reflex to fix it yourself "because writing the spec costs as much" is the
  trap. Writing the spec is also what makes you state the change exactly, and a
  spec that is hard to write is a change that was not thought through.
- Anything fiddly belongs in Python, not in a `.bat`.

## Environment split (short version)

**Re-measured 2026-09-15, and the split this section described is gone.** The
table below said the system interpreter had no `transformers`; it has 5.17.0, and
torch 2.12.1+cu130. That single stale cell produced a confidently wrong diagnosis
of why `--mask gdino` fails — *by both the worker and the architect*, because both
read it instead of running it. **Ask the interpreter, never this table.**

| | torch | transformers | tkinter | BiRefNet |
|---|---|---|---|---|
| system python.org 3.12 | **yes** 2.12.1+cu130 | **yes** 5.17.0 | yes | **yes** |
| ComfyUI `python_embeded` | yes | yes | **no** | yes |

Measured in the system interpreter on the 33-asset pool's first photograph:
`--mask birefnet` ignores 60.3 % of the frame, and `--mask gdino` finds its box
(score 0.52) and ignores 98.2 %. **Both run in the GUI's own Python.** The
`--mask-export` bridge and the ComfyUI route still exist and still work; they are
no longer the only way. SAM2 is the one that genuinely still subprocesses, because
it needs the `sam2` package rather than `transformers`.

M-LSD needs no second interpreter: its TFLite runtime (`ai-edge-litert`, the
declared `mlsd` extra) is installed in the **system** interpreter, so
`--detector mlsd|hybrid|union` run there directly. Reinstall with
`pip install --no-deps ai-edge-litert`.

## Ledger — the only place status lives

**Suite 2026-09-15, `--full` at `04f58df`: 306 tests, 2 failed, 2 skipped,
52.2 s** (the fast run is 264 in ~24 s, and is not the suite). Both failures are the single receding-row photograph in the bug entry
below (3.71° round-trip on one asset) — the long-standing limitation, nothing
else, and the only red the suite has. The earlier ruler regression is gone for
good: that test was superseded when rulers became guides pulled from the cross
(see Done). Wall time fell 160 s → ~55 s when the runner was split in parallel;
that is the split, not tests disappearing, and only `--full` produces 306.
Re-run before trusting this — it is a measurement, not a
promise, and no entry below may restate it. An item stays under **Open** until
nothing is left to do; **Done** is only for finished work. `Pn` labels are short
handles for work packages; entries written out in full here stand on their own.

### Open — do these

- ~~**[seam] The SAM prompt layer in `review.py` is unreachable.**~~ **Settled
  2026-09-15 — both answers, because it was two seams.** The *prompt* trio
  (`set_sam_box`, `add_sam_point`, `clear_sam_prompts`) was deleted: nothing read
  `sam_box` or `sam_points`, they were write-only, and they were specified in
  analysis pixels while the GUI keeps its prompts in frame fractions. Wiring them
  would have meant inventing a consumer. **The bigger find was underneath.**
  `_on_sam_done` computed the segment, stored it in `_sam_selection`, drew a green
  outline and a percentage — **and never handed it to the session**, so
  `apply_sam_mask` had no caller either and *the estimator never saw the selected
  building at all*. SAM was decorative. The entry asked "whatever the box button
  does"; it did nothing to the fit. Now wired, and the reason it was easy to get
  wrong is recorded in `apply_sam_mask`: SAM segments the **file**, `paint` and
  `detect_info["mask"]` are **analysis-res**, so the only honest caller held the
  wrong shape. The method converts now rather than demanding the caller does.
  Pinned by `test_a_sam_segment_reaches_the_estimator_and_composes_with_the_paint`,
  which also asserts paint and segment compose and that clearing leaves no stale
  union. **Follow-on, same day:** deleting the prompt trio exposed that
  `sam2seg._load` and `predict_box_and_points` had no caller either — SAM2 has
  only ever run through the *generated standalone* `_CHILD_SCRIPT`, which imports
  torch itself and never imports the module. `_load`'s docstring asserted a call
  that cannot happen ("only ever called from inside a child spawned by
  `run_subprocess`"), which is why the pair read as live; 80 lines gone with
  `_LOCK`, `_CACHE` and `import threading`. **A docstring claiming a caller is not
  evidence of one** — that sentence outlived every reader who believed it. Found by
  sweeping every function in `src/pc` for references across src, tests and tools:
  worth re-running after any deletion, since removing one function is exactly what
  strands the next.
*(The two dead helpers listed here — `sam2seg.predict_box` and
`vanishing._plausible_horizontal` — are **gone**, deleted 2026-09-14 and verified
absent from both files; `--full` stays at 301 tests, 2 failed, so nothing was
using them. The deletion is uncommitted in the working tree.)*


**Priority, re-sorted 2026-09-14 (user: "sort jobs, start with easy ones, first
work on tools and mask features").** Easiest first, tools and masking ahead of
everything else. The list below is not in that order; this is.

**A. Tools and masking — small, visible, an afternoon or less each.**

1. ~~**Mark lines by rubberband.**~~ **Done — it already worked.** `_mark_rubber` and
   `_mark_commit` landed in `74da5a9` and are wired (`<B1-Motion>` and
   `<ButtonRelease-1>` both dispatch on `_pending_mark`); the entry sat open here
   anyway, which is the "two lists that must agree will not" failure again, this
   time between the ledger and the code. **What was actually missing was the test**
   — added 2026-09-15, `test_a_mark_is_dragged_out_in_one_gesture_and_a_still_click_still_removes`.
   The trap is real and the code does handle it: a press that never moved falls
   past `_mark_commit` (gated on `_mark_moved`) into `_click_mark`, so removal
   survives — but only as a *first* click, since `pick_control_line` is guarded by
   `_pending_mark is None`. Mid-placement, a click cannot delete. That is the
   contract the test now pins.
   **Superseded in part 2026-09-15 (user): one Mark tool, not two.** The
   vertical/horizontal combobox is gone; `ReviewPanel._kind_for` reads the plane
   off the drawn segment (falls more than it runs → vertical, exactly 45° →
   vertical, stated not incidental), and the rubberband is tinted by that
   decision *while dragging* so it is visible before release. Both planes are on
   screen together now, so every pick carries `(index, kind)` — an index into
   `control_lines` means nothing against `control_hlines`, and verticals are
   searched first as a deliberate tiebreak, not nearest-wins.
2. **Tool icons as one coherent set; bigger buttons; typography and spacing.** The
   window is judged by this. Do it as a **single pass** — icons, font sizes, button
   sizes and spacing share a visual language, and split across sessions they will
   not match.
3. ~~**More tools / shortcuts**~~ **— the shortcuts half is done (2026-09-15).**
   `m` mark, `b` brush, `p` planar, `s` SAM: four modes that each cost a trip to a
   button. `App._tool_key` flips the variable and calls the existing handler —
   **except SAM, whose handler flips `v_sam` itself**, so the dispatcher must not;
   passing `var_name=None` says so. A key arriving while an entry, spinbox or
   combobox has focus is ignored, because typing is not a shortcut. Pinned by
   `test_each_tool_mode_has_a_key_and_the_keys_do_not_replace_each_other`, which
   asserts four *distinct* sequences are registered — `bind()` replaces, so four
   modes on one key would leave three silently dead.
   **The tools now live in one place (2026-09-15, user: "either the toolbar or the
   marker, I would prefer only the tool").** Mark, Mask brush and box-select are
   glyph toggles in the picture-corner palette; the text controls that duplicated
   them left the lower-left field, which keeps only brush *width* (a setting, not
   a tool) and Strike slanted (a one-shot action on the evidence, not a tool you
   hold). `_on_sam_toggle` no longer flips `v_sam` itself — **all four handlers
   read, the caller flips**, because a palette toggle button carries the variable
   and a second flip cancels the first, leaving a tool that looks dead.
   **Still open: more tools**, and only where a gesture is already being done the
   long way. Not a wish for its own sake.

   **[2026-09-15, user] One mask registry: every source is the same thing, and
   they add.** Four sources had four mechanisms — `prepare` OR-ed the automatic
   mask in at detect time, paint and SAM each merged themselves into the shown
   array through an eight-branch function that had to know what removing one
   should leave behind, and the strip was not a mask at all but a filter on line
   *midpoints* applied in `refit`. "Which of these put that red there" had no
   answer. Now `LAYER_SCOPE` names them, `ignore_mask(pool)` unions them, and
   `_drop_touching` applies **one rule: an annotator that touches the mask is not
   evidence** — hand-drawn control lines included, since they *replace* the
   detected pool and would otherwise be the only thing left, unchallenged.
   **The strip is the only layer not speaking for both pools, and that is
   measured, not a carve-out.** Cutting verticals with it left the angles alone
   (pitch within 0.4° on every asset tried) and cost ~0.11 of confidence every
   single time — confidence is multiplicative and counts verticals, so it would
   refuse photographs that are corrected today and buy nothing. One string in
   `LAYER_SCOPE` flips it back if that is ever wanted. The touch rule itself was
   measured before adoption: it keeps 46 % of detected lines where the endpoint
   rule keeps 48 %, against a docstring that feared it would "discard straddling
   lines wholesale". `--full` unchanged at the same two receding-row failures.

   **[2026-09-15] The facade strip sits beside the switch it serves, and is not
   called ROI any more.** It only ever changes which *horizontals* count, so it
   belongs under the horizontal (yaw) checkbox and nowhere else; "ROI x" was
   jargon a new user cannot decode. Both now carry hover help that names the
   real situation (two facades pulling the fit apart) instead of restating the
   label. **It moved from `_build_tools` into `_build`, which re-runs per
   photograph**, so its three variables are behind the `getattr(...) is None`
   guard and `test_the_facade_strip_still_works_after_a_second_photograph_loads`
   loads twice and then presses the real checkbox — the third time this file has
   met that trap, and the first time a test was written for it up front.

   **[audited 2026-09-15] SAM's polarity is correct; the confusion is elsewhere.**
   Worker traced the whole chain: the child writes selection-white
   (`sam2seg.py:113`), `load_mask_png` inverts to ignore (`sam2seg.py:207`), and
   that convention survives to the red wash and the estimator. **Red lands around
   the building, the building keeps its colours and its lines** — the stated
   intent, already true. Two real findings underneath: **`mask_invert` applies
   only to the `file` source** (`masks.py:259`) — birefnet, gdino and SAM all
   bypass the checkbox, so the same semantic decision exists twice, once
   toggleable and once hardwired. And `_draw_sam_prompts` draws a border around
   the *whole canvas*, not an outline of the subject: it reads as a contour and
   is only an "active" light. **Do not invert the brush globally** (user asked):
   most frames need a small exclusion, so starting fully masked means carving the
   building out by hand every time. One rule already holds — *red is what the
   estimator ignores* — and SAM obeys it.

   **[diagnosed 2026-09-15, NOT fixed] `--mask gdino` cannot work from the
   window, and the reason is structural.** Worker investigation, every claim
   cited. The weights are present (`models/GroundingDINO/` has `config.json` and
   `model.safetensors`), the call site is fully wired (combobox → `_apply_mask`
   → `set_mask` → `L.prepare` → `MK.build` → `gdino_mask`), and it still cannot
   run — **and that diagnosis was wrong.** It rested on the environment table
   above, which was stale. Measured instead: `transformers` 5.17.0 is installed,
   `gdino_available()` is True, the model loads (978 weights), and a full
   `MK.build` run returns a box at score 0.52 ignoring 98.2 % of the frame.
   **gdino works.** The real blocker is one line:
   `ValueError: --mask gdino needs --birefnet-model <weights> for the matte`
   (`masks.py:209-211`) — gdino finds the box and then asks BiRefNet to matte
   inside it, and `settings.birefnet_model` defaults to `""`. The weights are
   vendored at `models/BiRefNet/BiRefNet_lite.safetensors` and nothing points at
   them. **So: not an interpreter problem, not a bridge, a missing default.**
   The lesson is the expensive part and it is the third time today: *a document
   claiming something is not evidence of it.* `_load`'s docstring claimed a caller
   that could not exist, `add_sam_point`'s claimed bindings that did not exist,
   and this table claimed a package that was installed. The worker quoted each
   faithfully — it reads what is written, so what is written has to be true.

   **[fixed 2026-09-15] The mask "active" box lied, and off did not mean off.**
   User: *"if the mask is shown it should act; if you switch the display off it
   is useless"* — **display and effect were never coupled** (worker traced it:
   `show_mask` / `mask_alpha` are read only at `review.py:800`, nowhere on the
   effect path), so that premise was wrong. The real defect was the control
   beside it. `v_mask_active` was built `value=False` while `_apply_mask`
   defaults `_mask_enabled` to `True` — the box read *off* while masks were being
   applied. And unticking it merely early-returned, blocking future applications
   while leaving an applied mask in force, against a tooltip promising "toggle
   mask on/off". Both fixed: the box starts True, and inactive now calls
   `set_mask("off")` so the lines come back. **A control that reports a state it
   does not hold is worse than no control** — it was the likeliest source of the
   "mask behaves arbitrarily" impression.

   **[fixed 2026-09-15] The paint brush looked rectangular and nothing drew a
   rectangle.** Reported by the user, found by the worker on a read-only search.
   The mask *is* round (`cv2.circle`, `review.paint_ignore`) and so is the drag
   preview (`create_line(capstyle="round", joinstyle="round")`, the only place in
   the repo setting either). The squares were made on **display**:
   `preview.tint_mask` upsampled the analysis-res mask (long edge
   `detect_max_edge`, 1600) to the full photograph with `INTER_NEAREST`, which has
   no sub-pixel blending, so every round edge arrived as a staircase of right
   angles baked into the pixels before `create_image` ever saw them. Now
   `INTER_LINEAR` plus a per-pixel blend by coverage instead of a hard in/out
   test. **The lesson is the search order**: two round things and a square result
   means the fault is between them, not in either.

**B. Estimator defaults — still one constant each.**

4. **Re-decide the remaining batch-era caps as manual-first defaults.**
   `max_horizontal_deg` has now moved twice on exactly this argument (8 → 30 → 60).
   **`max_pitch_deg` and `max_roll_deg` are measured and the answer is "leave
   them" (2026-09-15, `tools/cap_sweep.py`).** Run uncapped over the 33-asset
   top-level pool, the steepest correction the estimator *wants* is **28.62°** of
   pitch (`lochfassade.jpg`, conf 0.62) against a 30° cap, and **3.42°** of roll
   against a 12° cap. **Neither cap refuses a single photograph in the pool**, so
   there is no refused correction to argue from and nothing to re-decide — the
   `max_horizontal_deg` argument does not transfer, because that cap *was*
   binding. Note the pitch headroom is only 1.4°: the cap is idle, not generous,
   and one steeper photograph would make it live. **If it is ever to move, first
   get a photograph it refuses** — a cap cannot be judged by the corrections it
   lets through. 39 % of the pool wants more than 10° of pitch, so the estimator
   is using the range it has.
   Still unexamined: the multiplicative confidence veto and P9's
   refuse-whole-correction recommendation. Each has a documented sweep behind it.

**C. Decide, do not necessarily fix.**

5. **The receding-row bug** (below) is the only thing keeping the suite red, and a
   permanently red suite stops being a signal. Either do the real work (make the
   focal estimate reject horizontal evidence spanning multiple planes) or scope that
   photograph class out of the round-trip gate deliberately and in writing.

**D. Last.**

6. **SAM3 via ComfyUI** is largely superseded by the click-to-select work that has
   already landed — fold it in or drop it, do not build both. **Distortion Stages 1+
   and the four `knowledge.md` research goals** stay last: blocked on a go-ahead and
   on measurement passes respectively, and none is what the product needs next.

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
   live in `.claude/skills/grounding-sam/SKILL.md`. **The second route is no longer
   hypothetical (2026-09-15):** `src/pc/sam2seg.py` *is* it — `run_subprocess` shells
   into `python_embeded` and hands a PNG back, and click-to-select ships on it. So
   what is left here is only the *text-prompt* question (SAM3 replacing a click with
   "building facade"), not the plumbing, and the entry's opening line — "the next
   exploration is a second masking route through ComfyUI" — is spent. Weigh it
   against what a click already gives before spending anything on it. The remaining gate is §3a's round-trip benchmark
   (beat BiRefNet / no-mask on the pool) before it becomes a default. Same two-interpreter story as
   BiRefNet (ComfyUI interpreter has no tkinter).
- **[open] Distortion correction (barrel/pincushion) — Stage 0 implemented 2026-09-14,
  Stages 1+ remain.** `H = K R K^-1` is a pure rotation and cannot touch radial
  distortion. **Full research in `knowledge.md` §5** (APIs, licences, interpreter
  split, remap composition, trigger signal). Stage 0 (`lensfunpy`) is done:
  `src/pc/distortion.py` (EXIF make/model/focal/aperture → per-pixel remap),
  `warp.apply_undistorted()` (single composed `cv2.remap`), `--undistort lensfun`
  CLI flag, `Settings.undistort`, preflight + doctor rows, 7 tests. No EXIF =
  graceful skip (returns None). Stages 1–3 (AnyCalib blind fit, GeoCalib gravity,
  cross-check gate) remain open pending their own measurements. Pipeline order:
  detect → undistort → correct.
  Test case: `Aulendorf_Schloss_Fassade.jpg` (zero EXIF, fragmented lines). Must keep
  the 8% border guard.
  **Go-ahead given 2026-09-14** (user: lensfun where EXIF identifies the lens, an
  estimator where it does not). Stage 0 is already in flight — `src/pc/distortion.py`
  is lensfunpy + EXIF make/model/focal/aperture with the usual `available()` guards.
  **Trigger: measured 2026-09-14, it does NOT work — do not build it.** The proposal
  was to suspect distortion when a `merge_collinear` chain's members show monotonic
  angle drift. Scored over the whole pool (fraction of chains drifting one way, plus
  angular span): **`Aulendorf` — the one asset known to be wide-angle and EXIF-free,
  and this item's own named test case — scores 0.04, near the bottom.** Ordinary
  facades and a phone snapshot score higher (hospital 0.10, `20260902_111335` 0.10).
  It ranks the distorted photograph as *less* distorted than undistorted ones; no
  threshold separates them. **Why it cannot work as specified**: `merge_collinear`
  groups segments *by similar angle*, 2° tolerance (`lines.py:180`) — segments that
  drift further never join the same chain, so drift within a chain is capped at ~2°
  **by construction**. The grouper filters out exactly the signal the trigger wants
  to read; the large spans in the data are clutter, not curvature (46° on
  `bcbac1c1`, 40° on `35559_XXL`, both undistorted). **A real trigger would have to**
  group by proximity and continuity rather than by angle, then measure residual
  curvature — straight-line fit and look at the residual, or fit a circular arc.
  Different mechanism, not a tuning of this one, and unmeasured. Until it exists both
  stages run on every photograph or are switched on by hand.
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

*(The lens-profile bug that stood here — `find_lenses(cam, "", "")` with an empty
name, then `lenses[0]` — was fixed in `02277a4` and is recorded in Done. Verified
against the code before deleting the entry: `distortion.py:160` now passes the
EXIF `lens_model`. It had been open here **and** closed in Done at the same time,
which is the "two lists that must agree will not" failure, caught on a drift
check rather than by the file itself.)*
- **Rulers: simple grey lines, dragged out of the black cross** (09-14, user).
  Hover over the cross or the border reveals; drag pulls a guide onto Q2 and
  leaves it there. Done so far: `_draw_rulers` targets `c_after` (it guarded on
  `c_before` — drew on Q1 and returned early for Q2 while its own docstring said
  Q2), and `_on_cross_motion` / `_on_cross_leave` reveal on hover, which works
  because the cross *is* the panel background showing through the grid, so only
  gutter events reach `self`. Left to do: the drag-out itself.
  **`test_the_ruler_reads_the_same_height_on_left_and_right` still fails** — it
  expects tick marks tied to the grid toggle, i.e. a ruler *scale*, not a grey
  guide. The user has now said "simple grey lines", so the test encodes a
  superseded design; replace it rather than bending the code to it.
- **Marker lines: place by rubberband, not two clicks** (09-14, user).
  `_click_mark` sets `_pending_mark` on the first click and completes on the
  second. Wanted: press, drag, release, line following the cursor. The session
  layer is already the right shape — `add_control_line(x0, y0, x1, y1,
  display_scale, kind)` takes a whole segment in one call — so this is GUI-only.
  **The trap:** a click that never moved must still mean "remove this mark", or
  the removal gesture disappears.
- **Marker lines: vertical and horizontal need different colours, chosen
  automatically** (09-14, user). Two kinds drawn identically is the one thing a
  glance cannot resolve, and the kind is what decides which array the line
  lands in.
- **Both preview images must be the same size and fill the frame** (09-14,
  user, emphatic). Horizontally *and* vertically. Today a photograph is fitted
  into its field and leaves the rest of the field empty; the two fields are
  equal but the two *pictures* need not be.
- **UI craft pass** (09-14, user): typography and graphic design in the
  templates — font sizes and layout; tool icons reworked to read as one
  coherent set, Photoshop-like; more tools where a useful shortcut exists;
  and the click buttons made larger and less ugly. Treat as one pass, not six
  tickets: they share a visual language and doing them separately guarantees
  they will not match.

### Done — rely on these

- **[bug, fixed] The lens profile was picked essentially at random** (09-14).
  Two defects in one path. `undistort_map` queried
  `db.find_lenses(cam, "", "")` — empty name — and took `lenses[0]`, with a
  comment calling it "imperfect but better than nothing"; on an
  interchangeable-lens body that is an arbitrary profile out of everything known
  for the camera, and **a wrong distortion profile bends straight lines the
  wrong way**, which is worse than leaving them alone. And `_exif_lens_model`,
  which reads the name that was never passed in, did `str(lm)` on piexif's
  **bytes** — yielding the literal `b'NIKKOR 18-55mm'`, a string the database
  can never match, so even a camera that records its lens fell through to the
  generic path.
  Now: decode properly, look the lens up **by name**, and when that finds
  nothing accept a generic query **only if it returns exactly one lens** — a
  fixed-lens body, where there is nothing to get wrong. More than one and it
  declines and returns None, so the photograph is left alone. Pinned by
  `test_the_lens_model_comes_back_as_text_not_a_bytes_repr` (asserts the *type*
  of thing that comes out, which is the only way this bug is visible) and
  `test_an_unknown_lens_is_refused_rather_than_guessed`. The stronger case —
  a multi-lens body — needs lensfunpy and a real camera in the database and is
  deliberately not faked.


- **The two remaining batch-era caps, measured** (09-14, `analysis/measure_caps.py`,
  51 files / 49 after deduplication, 0 load errors).
  **`max_pitch_deg = 30` is inert on this pool: 0 refusals.** Post-damping pitch runs
  min 0.12°, median 7.73°, p90 13.55°, max **24.25°** (`lochfassade`, whose raw
  28.53° the 0.85 damping brings clear). Nothing tests the cap, so **the measurement
  does not decide it** — leave it at 30. Lowering it would start refusing corrections
  around the median that this pass never evaluated for correctness.
  **`min_confidence = 0.40` refuses 7/51, and `stability` is the vetoing factor in
  6 of those 7.** The sweep is smooth — 0.30→3, 0.35→5, 0.40→7, 0.45→12, 0.50→17 —
  so **0.40 is not cutting at a natural break**; the only real gap in the
  distribution is between 0.07 and 0.26. Of the seven, one is a 0.20° non-correction,
  one is a genuine outlier (`marienrode`, conf 0.07, stability swing 1.56°, earned),
  and **five are moderate, plausible corrections (pitch 1.9–13.6°) whose
  share/count/spread all read 1.0** and which fail on `stability` alone.
  Whether a reviewer would want those five needs visual/round-trip ground truth that
  this pass did not collect, so it **does not decide the number either**. What it does
  establish: the gate slices a dense continuum almost entirely on one factor —
  **changing `min_confidence` and reworking how `stability` enters the product are
  two different fixes, and this measurement does not separate them.**
  Incidental: yaw (flag on) runs median 31.5°, max 71.5°, matching P9's 16–70°;
  3/49 now exceed the raised 60° cap. And the pool's three `screenshot_*` files are
  two byte-identical duplicates of one — deduplicating moved pitch p90 15.15 → 13.55,
  which is the housekeeping note's skew, confirmed.
- **`tools/debug_ui.py` was watching a file nothing writes** (09-14). It checked
  `bpc_errors.log`; the code has written **`pc_errors.log`** since the `bpc` → `pc`
  rename, so every "clean" it printed was vacuous — and it judged by
  `os.path.exists`, so a log left behind hours earlier read as "WROTE ERRORS". Both
  fixed: right filename, and size compared before and after the sweep. This cost real
  time: it made a correct "that error is historical" reading look wrong twice.
  A diagnostic that cries wolf is worse than none.
- **Buttons, type scale and control sizes** (09-14, user: "improve click buttons,
  they look ugly and they are too small"). One `apply_theme` pass: TButton padding
  (12,6) → (16,9), Accent (14,7) → (18,10), Title 15 → 16, Entry/Combobox padding
  → 7, Spinbox and Checkbutton → 6, Treeview rowheight 24 → 28, Scale slider 16 → 20
  and thickness 8 → 10, Progressbar 4 → 6. No new style names, no new colours.
  **Generated by the local Qwen worker to spec and applied by hand** — see the Worker
  section for why that is the shape that works.


- **The suite runs on five photographs, not fifty** (09-14, user: "test should be
  minimal half of time", "testsuite max 5 images"). **152.8 s → 48.3 s**, a 3.2x cut,
  with the same 292 tests, the same 2 known failures and the same 2 skips — the
  pool-wide sweeps were the bulk of the runtime. `test_assets.MAX_ASSETS = 5`,
  sampled **evenly across the sorted pool** rather than the first five (the pool
  sorts by filename, so the first five are five photographs from one contributor).
  `PC_TEST_ASSETS=0` restores the whole pool, and **must** be used before writing any
  pool-wide number into this file: a claim about the pool computed from a tenth of it
  is not a claim about the pool. `_ALWAYS` force-keeps `*_upright.*`, `*_skip.*` and
  the receding-row asset, so the cap cannot quietly drop a **known failure** and read
  as a fix — the sample is 6, not 5, for exactly that reason.
- **The ruler test that was failing is gone, and the rulers are guides now** (09-14).
  The open bug entry that called this a regression is closed: the test pinned a ruler
  *scale* (ticks tied to the grid toggle), which the "simple grey lines" decision
  superseded. Replaced rather than loosened — see the guides entry below.
- **Pad colour, its swatch and the "edge" button removed from the fill row** (09-14,
  user). `pad` only shows through when the fill is off, and the fill defaults to
  `telea`, so three controls competed for width in the busiest row to set something
  almost nobody sees. `--pad` and `Settings.pad` are untouched. The "edge" button's
  tooltip described padding *width*, which it never set — it had been lying about
  itself too. Handlers, swatch and the now-orphaned `colorchooser` import all went
  with it; zero references left.


- **Both previews fill their field, at one scale** (09-14, user: "images must be
  same size and fill frame fully", "images same size maximized"). `_redraw` shrank
  *both* boxes by `2 * RULER_MARGIN` and `_show_after` then inset again, so the
  corrected image rendered **11.5 % smaller than the original beside it** — the one
  thing a before/after comparison must not do. Measured at 1920x1200: before
  648x486 (64.9 % of canvas) and after 625x446 (57.5 %); now **701x526 (76.0 %)**
  and **737x526 (79.9 %)**, both exactly the canvas height. The two boxes are
  computed by identical expressions on purpose — one rule is the only way two
  panes stay at one scale. Remaining width difference is real: the warp changes
  the frame's aspect, so at equal scale the corrected frame *is* wider, which is
  information rather than a defect.
- **Rulers are guides pulled out of the black cross** (09-14, user: "rulers should
  be simple grey lines", "ruler zone outside in black cross", "from black cross and
  border"). Three changes. (1) The guide zone left the inside of the canvases —
  that inset was what was stealing the pixels above, and the cross is outside the
  images where it costs nothing. (2) Creation moved to the panel itself: the cross
  *is* `ReviewPanel`'s background showing through the grid, every child canvas eats
  its own events, so anything reaching `self` is over the gutter or border and
  needs no hit-testing (`_on_cross_press` / `_on_cross_pull` / `_on_cross_drop`,
  with `_cross_orientation` giving a guide parallel to the bar it came from, and a
  dashed preview while dragging). Released back over the cross it is discarded,
  like dragging a guide back to the ruler. (3) They draw as **plain grey hairlines**
  (`GUIDE_GREY`); they had been 3 px light blue with a tick every 20 px, majors
  every 100 and a numeric label — that is a ruler *scale*, and a guide is laid
  against an edge to judge parallelism, so every tick competes with the photograph.
  `test_a_guide_is_pulled_out_of_the_border_as_a_plain_grey_line` **replaces**
  `test_the_ruler_reads_the_same_height_on_left_and_right`, which pinned the
  superseded scale and was the suite's only self-inflicted red.
- **`max_horizontal_deg` raised 30 → 60** (09-14, user: "horizontal correction up
  to 60 degrees"). Manual yaw slider and spinbox widened to ±60 to match, so the
  estimator's cap and the by-hand control reach the same place. Second time this
  constant has moved under the manual-first direction (8 → 30 → 60); the shear
  risk is unchanged and real, and is seen by the person reviewing before anything
  is written.
- **Three dead Tk bindings, and the brush cursor nobody could see** (09-14). Tk's
  `bind()` *replaces* a handler for the same sequence rather than adding, so
  `c_before` binding `<B1-Motion>`, `<ButtonRelease-1>` and `<ButtonPress-3>` twice
  each left `_on_sam_drag`, `_on_sam_release` and `_on_sam_right_click` **dead** —
  SAM's drag, release and right-click did nothing, which is why SAM read as
  missing. Now one bind per sequence dispatching to both, each handler already
  self-guarding (`v_sam` / `_brush_live`). Separately the brush outline drew a
  single **black** 1 px ring on a dark field and was invisible; it is a white ring
  outside a black one now, the way every editor does it, so one of the two always
  contrasts.
- **Planar left Q4 and the tool palette** (09-14, user: "remove planar from q4",
  "less is more"). The quad was doing two unrelated jobs — rectify a flat face, and
  state which plane the lines belong to — and marker lines answer the second with
  far less UI. `v_planar` is now made once rather than rebuilt by `_build`, the
  trap that had already killed the mask brush from the second photograph onward.
  `planar.py` and its tests stay; nothing in the window points at them.
- **Docs condensed** (09-14, user). CLAUDE.md 957 → 567 lines (Done collapsed to
  one line per entry; full prose is in git history, which is why dropping it is
  not lossy), `knowledge.md` 451 → 342 (the `claude_save.md` reconciliation section
  had done its job when that file was deleted, nine pointers at it were dead, and
  §5's status duplicated this Ledger — only third-party APIs and licences kept).
  `qwen.md` had four stale names — `src/bpc/`, the `bpc` command, `bpc --doctor`,
  `run_bpc_gui.bat` — all of which would have sent the worker somewhere that does
  not exist.


One line each. **The full reasoning for any entry is in this file's git
history** — it has been committed at every step, so `git log -p -- CLAUDE.md`
recovers the prose without keeping it in the live context. Kept as titles so
nothing is silently re-proposed and so a search still finds it.

- **Package renamed `bpc` → `pc`** (09-14)
- **Rulers, Q2 layout, SAM interaction overhaul** (09-14)
- **Rulers moved to static 20px border zone** (09-14)
- **Stage 0 barrel distortion (lensfunpy)** (09-14)
- **Clipboard paste + jpeg quality spinbox** (09-13)
- **[bug] Before/after canvases stayed black after a theme switch** (09-13)
- **Status box removed; detector moved Q4→Q3** (09-13)
- **[bug] The mask brush did nothing from the second photograph onwards — fixed (09-13)** (09-13)
- **Brush preview fixed and restyled** (09-13)
- **Grid draws on the corrected pane only** (09-13)
- **"Check lines": re-detect on the corrected frame** (09-13)
- **`max_horizontal_deg` raised 8 → 30** (09-13)
- **Tools live on the picture now** (09-13)
- **The brush paints the mask now, instead of erasing individual lines** (09-13)
- **Overlay switches moved onto the images they draw on** (09-13)
- **Masks get a morphological close before the shrink** (09-13)
- **The options bar says what it is** (09-13)
- **The prominent run button now *asks* instead of writing unattended** (09-13)
- **`masks.build` accepts several sources at once** (09-13)
- **Review-window UI polish: tooltips, movable mark endpoints, Alt+click mask erase, Mask Apply, loupe in mark mode** (09-13)
- **Loupe click forwarding + drag tracking** (09-14)
- **FIND controls moved to the input side; review panel is edit-only** (09-13)
- **ROI strip auto-enables horizontal correction** (09-13)
- **Batch Output destination moved to the Start/Stop bar** (09-13)
- **Window floor raised to 1920×1080** (09-13)
- **Controls `btns` row no longer clips at any window size** (09-13)
- **Folder-add icon redesigned** (09-13)
- **Crop rectangle gained mid-edge handles** (09-13)
- **Enabling the `roi_x` strip now turns on `correct_horizontal` with it** (09-13)
- **`roi_x` got a real interface** (09-13)
- **Mask opacity works with "Lines" off** (09-13)
- **"subfolders"/"overwrite originals" duplicate checkbox fixed** (09-13)
- **`--mask gdino` shipped** (09-13)
- **Pitch cap raised 20°→30°** (09-13)
- **BiRefNet-lite checkpoint support** (09-13)
- **Crop rectangle gains a pan gesture** (09-13)
- **Cached-mask pool completed** (09-13)
- **P13 decided** (09-13)
- **P10 done** (09-13)
- **`ArchitectureScheme` wired in** (09-13)
- **P9 (yaw policy) measured, analysis-only** (09-13)
- **Pitch cap measured, analysis-only** (09-13)
- **P16 done** (09-12)
- **BiRefNet's process-wide `subprocess.check_output` monkeypatch scoped** (09-12)
- **`tests/assets/Horizontal/` wired into `test_assets.py`** (09-12)
- **`skills/ui.md` rewritten** (09-12)
- **Bottom-left field became the persistent tools area** (09-12)
- **Review-panel columns read "find vs. edit"** (09-12)
- **Loader minimized** (09-12)
- **Line-brush stroke tool** (09-12)
- **`ReviewPanel._apply_mask` no longer crashes on a missing `v_maskpath`** (09-13)
- **Mask-mode label renamed "source" → "mask"** (09-13)
- **Hough detector removed** (09-12)
- **Off-screen test coverage added for planar corners** (09-12)
- **Loupe crash fix** (09-12)
- **Planar Save fix** (09-12)
- **P15 done** (09-12)
- **Grid-spacing combobox made genuinely editable** (09-12)
- **Angle/focal sliders gained paired Spinboxes** (09-12)
- **Ruler ticks added to the right edge too** (09-12)
- **M-LSD unblocked in the GUI interpreter** (09-12)
- **Perfect cross UI, window = cross** (09-12)
- **P14 done**
- **Version series starts at 1.0**
- **P11+P12 done**
- **Slim live CLAUDE.md**
- **Batch-options bar regrouped** (09-12)
- **`ttk.Scale` crash on window open, fixed** (09-12)

### Repo note

**Rewritten 2026-09-14 — the old text ("nothing on this branch is committed yet")
had been false for eight commits.** Actual state, measured:

- `D:\Coding\Perspective-Correction` is the **live checkout**. `main` is at
  `02277a4` and **five commits ahead of `origin/main`** (`843a76b`): `74da5a9`
  (guides from the cross, both previews maximized, the fast/full runner split),
  `0978371` (ignore local `.bak`), `89677f0` (caps measured, buttons enlarged),
  `35d646b` (dead-code sweep) and `02277a4` (the lens-lookup fix). All local
  only — **a full day of work exists on one disk**, which is the one risk in this
  note worth acting on. Working tree clean at the last check.
- **`D:\Coding\Batch-Perspective-Correction` still exists.** The rescue section at
  the foot of this file says that folder "is gone"; it is not. It is a stale
  checkout one commit behind (`1dc2670`, still `src/bpc/`) with its own modified
  `CLAUDE.md`, and it is what a recurring drift-check pointed at all day — which is
  how a suite was run against the wrong tree. Delete it or leave it, but **do not
  measure anything in it**, and do not confuse it with the off-limits
  `D:\Batch-Perspective-Correction`.
- Two strays sit at `D:\Coding\` itself, outside any checkout: a `CLAUDE.md` (a
  stale copy of this ledger, dated 09-13, claiming 280 tests) and an `analysis\`
  folder. The stray `CLAUDE.md` is worse than clutter — it is one directory above
  the project, so a session opened at `D:\Coding` loads *it* as the instructions
  and reads a day-old ledger as current. That is this file's own "two lists that
  must agree will not", made worse by neither copy knowing the other exists.
- Untracked and wanted, unchanged from before: `skills/`, `tools/debug_ui.py`,
  `tools/worker_bench.py`, `"Perspective Correction.bat"`.

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

**The limit was never the model or the server — it is the client harness** (established
2026-09-14). Hit llama.cpp under Unsloth Studio **directly** at
`127.0.0.1:8888/v1/chat/completions` with an OpenAI `tools[]` array and it returns
`finish_reason=tool_calls` with well-formed arguments in **1.6 s**; a two-turn loop
(`write_file` then `run_shell` to read the file back) wrote the file, verified it
and reported the match in **22.8 s** — it chose the verification step itself.
**So the worker is agent-capable; it needs a client that executes the calls.**
Measured with `enable_thinking:False`; thinking-mode tool calling is *unmeasured*.

**`qwen -p` is the harness that has no tools**, and everything below describes only it
(re-confirmed 2026-09-14: a write-one-file probe returned **nothing in 10 minutes**,
matching the failure recorded here from the start). In its own words: *"in this session
I have no tool to write the file or launch the process, so that's the blocker"*.
`qwen -p` here has no file-editing and no shell,
but it **can** read the repo: asked to triage six dead-code candidates it cited
`pipeline.py` (line 223 against an actual 225), named the vectorised
`_plausible_horizontal_rows` twin and the GUI's own `_sam_box` — all verified
correct. Its own words were "no tool to **write** the file or launch the
process", which is narrower than "no tools" and is the useful distinction:
**reading and reasoning are on the table, acting is not.** That single fact explains every previous
disappointment with it: the first real package returned nothing in five minutes
because it was composing an answer nobody could apply; "reply with OK" worked
fine; and a later one-file task came back as a correct patch with no way to
write it.

**So use it, and use it freely — it costs nothing.** The working shape is:

* hand it a **precise spec with every name supplied** (no exploration — it
  cannot grep, and asking it to would be asking for invention);
* ask for **only** a fenced code block, no prose;
* **apply the patch yourself and run it yourself.**

Measured on that footing it is good: given the spec for a preview-size check it
produced code matching it exactly, first try. What it cannot do is find out what
the names are, verify its own work, or notice that the file moved under it.
Anything needing those is a metered subagent or the architect.

### How to actually use it — `tools/worker_agent.py` (2026-09-14, works)

**Do not use the plugin and do not use `qwen -p`. Use the server.** `tools/worker_agent.py`
is a ~150-line client: it POSTs to `/v1/chat/completions` with a `tools[]` array, executes
the calls the worker returns, feeds the results back, and loops. The worker gets four
repo-scoped tools — `read_file`, `grep`, `str_replace`, `run_tests` — and **no raw shell**.

```
python tools/worker_agent.py package.txt       # or  -  for stdin
```

**First real job, measured:** the two dead helpers below. It read both files, made two
`str_replace` edits, grepped to confirm the *kept* twins (`predict_box_and_points`,
`_plausible_horizontal_rows`) were still there, and stopped. `git diff` was 23 deleted
lines and nothing else — no reformatting, no drift. Suite after: 301 / 2 failed / 2
skipped, the standing baseline. **That is a real delegation, end to end.**

**It refused to claim success it could not verify**, which is the property that makes it
usable: when its test runs came back broken it said so rather than asserting green. The
breakage was *this harness*, not the worker — `run_tests` returned the raw output tail,
which was the `invalid command name ..._pump` Tk teardown noise that "Running things"
documents as harmless. It retried eight times against garbage. **Never hand the worker a
tool that returns an unparsed tail**; `run_tests` now returns the verdict line and strips
that noise. Eight of its twelve turns were this bug.

**Operationally — the server is the dependency, nothing else.**

* Start it: `unsloth studio run --model unsloth/Qwen3.8-27B-GGUF:UD-IQ4_XS` (~45 s to load).
* **Closing the console that started Studio kills the server.** Found the hard way: the
  endpoint went to connection-refused mid-session. Restart with the line above; the API key
  in `~/.qwen/settings.json` survives a restart, so the harness keeps working.
* `unsloth start claude --as-subagent …` registers a *plugin* MCP server instead. It works,
  but MCP servers attach at **session start** — running it inside a live session does
  nothing for that session. And it launches its own Claude session, so it cannot be driven
  from a non-interactive shell (it exits 1 on missing stdin). **The harness needs none of it.**
* `UNSLOTH_STUDIO_URL` overrides the endpoint if Studio is not on localhost.

**The limits are unchanged and still bind.** One package, two tool turns, four
hand-written tools is a floor, not a licence. The architect still reads `git diff` before
anything is committed — a green suite is evidence, not permission — and anything needing
judgement (which of two designs, whether a seam should exist) is not a package at all.


**Context is 96 256** (2026-09-14, from the running server's `/v1/models`
`context_length` — not the card, not the Studio panel, which shows the
*requested* number). The same response reports `native_context_length: 262144`,
which is where the drifting figures come from: **the served window is the loaded
quant's, not the model's.** This file has now carried 125 056, 94 848 and 96 256
for one number — trust only a figure with a date beside it, and re-ask the server.
Reached with the KV cache at `q4_0` on both halves and
speculation off. **Qwen Code does not ask the server**: `contextLimit =
model.contextWindowSize ?? tokenLimit(id)`, and an unknown id falls back to
`DEFAULT_TOKEN_LIMIT = 200 000`, so it must be told via
`generationConfig.contextWindowSize` in `~/.qwen/settings.json` or it compacts
far too late. Fourth different figure this project has recorded — **ask the
running server and write the date beside the number.**


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
- Context: see the dated figure above — do not restate it here. An agentic CLI
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
- `docs/worker-anleitung.txt` — **German, for the user, not the architect**: how to
  bring the worker up in a fresh session from nothing (start the server, write the
  package, run `tools/worker_agent.py`, check the diff yourself) and the four
  gotchas that have cost time. Read `tools/worker_agent.py` itself for the harness.
- `.claude/skills/debug/SKILL.md` — benchmark / BiRefNet failure workflow, the
  two-Python setup, Windows pitfalls.
- `.claude/skills/grounding-sam/SKILL.md` — Grounding DINO + SAM2 detect-then-segment masking,
  direct Python (no ComfyUI server); local model paths, the §3a benchmark gate, roi_x synergy.
- `knowledge.md` — analysis + external sources for the four remaining research goals
  (facade-outline-first, dominant-edge hierarchy, SAM3, found-geometry overlay); read
  before picking up any of them.
