# CLAUDE.md overhaul + bugfix plan (drafted 2026-09-11)

Produced by a two-pass multi-agent review (local code audit + external research on
single-image camera calibration, run alongside a research pass on the 9 requested
lens-distortion/calibration repos and their current successors). This file is the
architect's output; nothing in it has been applied to the tree yet.

**Verified against the live tree**: `python tests/run_tests.py` → 172 tests, **5 failed**,
4 skipped, 138.7s, all five traced to one root cause (see B1).

---

# Part A — CLAUDE.md fundamental rewrite

Current: 1269 lines, auto-loaded into every session. Target: **~500 lines**. Governing
rule: **conclusions stay in CLAUDE.md verbatim; tables and narrative move to `docs/`
with a one-line pointer.**

`docs/` already has: `accuracy.md`, `algorithm.md`, `detectors.md`, `masking.md`,
`prior-art.md`, `reference-review.md`. New files proposed: `docs/dev-workflow.md`,
`docs/fill.md`.

## Proposed section list

| # | Section | Purpose | Fate of current content |
|---|---|---|---|
| 1 | `# Batch Perspective Correction — working notes` | title | keep |
| 2 | What this is for, and the one rule | The asymmetry: a ruined photo is unrecoverable, a skipped photo costs nothing. | **PRESERVE VERBATIM** (84-93) |
| 3 | How this file is maintained | Standing rule; wishes logged immediately; statuses; run-to-completion. + NEW size rule. | preserve 62-82, add size rule |
| 4 | The model: roll, pitch, yaw, f | `H = K R K^-1`, pure rotation, cannot shear. Dependency table. `R = Rx(pitch) Rz(-roll)` order. | **REWRITE**, merge 116-133 + yaw half of 1052-1068 + convention bullet 1117. Table 125-132 preserved verbatim. |
| 5 | Module map | **NEW.** One line per module (geometry, lines, deeplsd, mlsd, model, warp, planar, birefnet, masks, inpaint, deps, pipeline, cli, gui, review, imageio, prefs, preview, vanishing, optimize). | new |
| 6 | Where it came from | The `chsasank/Image-Rectification` disqualifier. | **CONDENSE** 95-115 → ~6 lines; keep the `compute_votes` snippet; pointer to `docs/reference-review.md` |
| 7 | Negative results: what measurement killed | Horizon-focal estimator; line merging; unconditional f fitting; hard EXIF/geometry switching; radial distortion (border-replication story); FLD-beats-LSD; M-LSD; DeepLSD-as-default; SAM; automatic planar routing; **yaw-on-by-default (new entry)**. | **CONDENSE HARD** ~500 lines (134-324, 419-447, 486-525) → ~90 lines. Preserve verbatim: "the elegant estimator lost to the dumb prior" (166-167), "A benchmark that is wrong in the incumbent's disfavour is the dangerous kind" (523-524). Tables → `docs/accuracy.md`, `docs/detectors.md`. |
| 8 | Positive results: what measurement accepted | Pitch damping 0.85; angular_softness 0.35; 8% border guard (1.71/6.08 → 0.66/1.68); multiplicative confidence; refuse-beyond-limit; BiRefNet over SAM; seeded RNG; 1600px analysis. | **CONDENSE** 169-194, 325-418, 448-485 → ~60 lines. Preserve verbatim: "Confidence cannot catch this and is not built to" (541-544). |
| 9 | Refuse rather than trim; and the review loop | Limit philosophy (Prague ceiling, confidence 0.57); review panel; manual crop; auto crop; vertical control lines; planar as the manual answer to the frontal case. | **CONDENSE** 526-561 + 828-1001 → ~50 lines |
| 10 | The band the rotation opens up | Fill; why telea is default; fill_max_share as refusal not clamp; "never invent pixels without saying so". | **CONDENSE** 562-827 (265 lines!) → ~35 lines + NEW `docs/fill.md` |
| 11 | Open issues (as of date) | **NEW, standing structural section.** Every known-broken/half-wired thing with file:line + the test that proves it. | new |
| 12 | Feature requests, and where each stands | Wish ledger stays. | **KEEP structure, CORRECT content**, condense GUI-merge narratives (1096-1114) |
| 13 | Known weakness, stated plainly | Flat facade + no EXIF is genuinely under-determined. `--focal-35mm` is the answer. Forward-pointer to §14. | **PRESERVE**, trimmed (1002-1013) |
| 14 | Architecture roadmap — external research, not implemented | **NEW.** Staged GeoCalib/AnyCalib/lensfun proposal — full text below. | new |
| 15 | Conventions | Retitled — current heading "Conventions that are correct as written" is itself false (yaw claim, see staleness #1). | **CORRECT** 1115-1139 |
| 16 | Testing | How to run; the MODULES gotcha (with the live test_planar example); synthetic vs real assets; pointer to `skills/ui.md`. | **CORRECT + CONDENSE** 1157-1202 |
| 17 | Environment and dependencies | Optional-backend table, `--no-deps` trap, two-Pythons problem. | **MERGE** 1203-1262, keep `--no-deps` paragraph (1253-1258) verbatim |
| 18 | Licensing | MIT, must stay clean. + new rule: every future dependency's licence checked before a prototype (Apache/BSD/MIT/LGPL only). | **PRESERVE VERBATIM** (1263-1269) + one new paragraph |

### Cut outright

- **`CLAUDE.md:3-46`** — the Master/Worker Qwen delegation block (+ 14 blank lines 47-61).
  Dev-workflow, not project knowledge, and currently displaces the standing rule as the
  first thing in the file. Move to **`docs/dev-workflow.md`** together with the Unsloth
  note, 3-line pointer left behind. **User decision, not a done deal**: moving it out
  means it stops auto-loading into every session — say so before doing it.
- M-LSD (201-261) / DeepLSD (262-324) full write-ups → one paragraph each; tables to
  `docs/detectors.md`.

### Staleness the rewrite must correct, not copy

1. `1117-1119`: "yaw is estimated and gated but not yet fed into the warp" — **false**,
   `warp.py:49` and `warp.build(..., yaw)` apply it.
2. `1059`: "`max_horizontal_deg`, 8.0" — now `90.0` (`config.py:96`).
3. `1062`: "`--horizontal` (off by default)" — contradicts `config.py:94` (currently `True`).
4. `1034`: "Frontal / planar correction — core done, GUI shell not started" — the GUI shell
   exists (`gui.py:567` checkbox, click handlers, live preview at `gui.py:1165-1172`);
   what's actually broken is Save (see B2).
5. `1046-1047`: "nothing calls it yet — no CLI flag, no review-panel mode" — half false.
6. `1164-1167`: "standing skips" claim — suite currently reports 4 skips, not those described.
7. `1115` heading "correct as written" — no longer true.
8. `1159`: "172 tests" is right today but changes once `test_planar` joins `MODULES` (B6).

### §14 content — Architecture Roadmap (verbatim proposal)

> **Status: researched, not implemented. No code in the tree depends on any of this.**
>
> **The gap it closes**: a flat-on facade with no EXIF. One horizontal direction fixes
> one *point* on the horizon, not the line — focal length is genuinely underdetermined
> by lines alone. Today's answer is `--focal-35mm` or a 0.60-sigma guess.
>
> **Stage 0 — EXIF + `lensfunpy`** (LGPL, pip, Windows wheels). Runs first, before any
> estimator. A manufacturer-measured distortion/focal profile beats any blind estimate
> at near-zero cost. `imageio.py` already reads EXIF focal; this adds make/model/lens
> lookup. Optional-dependency-gated exactly like `birefnet.py`: lazy import,
> `describe()`/`available()` so `deps.py --doctor` reports it; absent = unchanged behaviour.
>
> **Stage 1 — AnyCalib** (Apache-2.0, code+weights, ICCV 2025, github.com/javrtg/AnyCalib).
> Blind distortion fallback when the lens isn't in the lensfun DB. Brown-Conrady up to
> four radial coefficients — best distortion expressiveness currently available.
> Does not estimate gravity. ~25ms/image on an RTX 4090.
>
> **Stage 2 — GeoCalib** (Apache-2.0, ETH CVG, github.com/cvg/GeoCalib). Run on the
> undistorted image: gravity (roll/pitch) + a focal estimate; accepts a known focal as
> a prior. This is the "learned focal prior" this file has named as wanted.
>
> **How it enters the existing model — load-bearing design claim.** A learned focal is
> a **prior term**, not a new estimator branch. `model.py:334-338` already carries a
> prior-with-sigma table (`manual` 0.12, `exif` 0.20, `default` 0.60) and
> `model.py:256 _blend_focal` already combines prior and geometry by inverse variance
> in log space. GeoCalib enters as one more row (`"geocalib"`, sigma **measured**, not
> guessed), replacing the 0.60 `default` row exactly where that row exists for. No new
> branch in `estimate()`.
>
> **GeoCalib does not replace the VP search, and must not.** Roll is already measured
> at 0.018° mean and doesn't depend on `f` at all — nothing for a learned gravity
> estimator to win there. Its value is almost entirely the focal prior for the no-EXIF
> flat-facade case. Swapping a measured 0.018° estimator for an unmeasured learned one
> would repeat a mistake this file already records three times over.
>
> **Stage 3 — the cross-check gate, which is free.** With up to four independent focal
> estimates (EXIF/lensfun, AnyCalib, GeoCalib, VP geometry), disagreement above ~10%
> becomes a new multiplicative confidence factor in `model.py:467 _confidence`.
> Confidence is already multiplicative — no new philosophy, no new refusal path.
>
> **Stage 4 (optional) — plumb-line refinement.** DeepLSD segments +
> `scipy.optimize.least_squares` on `(k1, k2, cx, cy)`, ~150 lines, reimplementing the
> published method. Do **not** vendor `LensDistortionFromLines` (CC-BY-NC-SA, non-commercial).
>
> **Stage 5 — one resample, never two.** Compose the undistortion map with
> `H = K R K^-1` into a single `remap`. `warp.limit`/`warp.plan` untouched;
> `warp.build` grows a map-producing variant.
>
> **Does this contradict the rejected radial-distortion result? No.** What was rejected
> was blindly sweeping Hugin's radial `b` coefficient to explain an artifact that turned
> out to be border replication — a blind fit to a broken benchmark. A manufacturer
> profile and a learned per-image estimate are different instruments. The rejection
> **transfers as a requirement**: no distortion model lands without the same round-trip
> benchmark, and it must beat the border-guarded **0.66/1.68**, not the broken 1.71/6.08.
>
> **New settings, all defaulting to off**: `lens_profile: str = "off"`,
> `focal_prior: str = "off"`, `undistort: str = "off"`. `deps.py` gains rows; `--doctor`
> reports them; pre-flight hard-fails only when a backend is explicitly requested but
> absent — the rule already established for BiRefNet/DeepLSD.
>
> **Licences checked**: GeoCalib Apache-2.0, AnyCalib Apache-2.0, lensfunpy LGPL
> (dynamic link via pip compatible with an MIT project), OpenCV BSD. **Rejected on
> licence**: PerspectiveFields (Adobe Research, non-commercial), LensDistortionFromLines
> (CC-BY-NC-SA). **Rejected on capability**: OpenCV calib3d has no single-image
> auto-calibration — execution layer, not an estimator. **Fallback baseline kept**:
> `chsasank/Image-Rectification` (BSD-3), if the learned models are ever unavailable.

### How §11 "Open issues" works given the sequencing

§11 is a **permanent structural section**, not a one-off list. At write time (after
Phase 1 below) it holds what genuinely remains open: the 90° cap question (B5),
roi-x unwired (B3), no batch-level yaw control, test_planar fallout (B6), the working-
tree scratch files. Fixed bugs don't vanish from the file — each stays recorded in its
topical section as a failure + what fixed it, matching this project's habit of keeping
losing branches documented rather than deleted.

---

# Part B — Concrete code fixes

## B1. Yaw default contradicts the CLI, the tests, and a real asset

**Decision required first: `False` or `True`?** Recommended **`False`** — CLI help,
`cli.py:304`, `tests/test_warp.py:80`'s own comment, and a real-photograph regression
(`hospital-nikon-d60_f27.jpg`, 43% hole, refused by `--fill-max-share`) all point the
same way.

- **`src/bpc/config.py:94`**: `correct_horizontal: bool = True` → `False`.
- **`src/bpc/config.py:79-93`**: the comment currently argues *for* on-by-default and
  becomes orphaned by the flip. Rewrite to state off-by-default and why. **Preserve the
  measured content** (the -39..+70° benchmark range, the 8→25→90 cap history, "a yaw
  near 90° is a valid camera pose") — relocate it into CLAUDE.md §7 as a new
  negative-result entry.
- **`src/bpc/gui.py:1623-1649` (`App._settings`)**: no change strictly required once the
  default is `False` — `ReviewPanel._build` already initialises `v_correct_horizontal`
  from `settings.correct_horizontal` (`gui.py:479`), and `_on_horizontal_toggle`
  (`gui.py:851-852`) already applies the per-photo choice. The missing **batch-level**
  control is a separate feature gap — see B-follow-up in Phase 3, not this bug.

**Verify per test, not as a blanket claim:**
- `python tests/run_tests.py test_warp` → `test_yaw_is_zero_when_horizontal_correction_is_off` passes.
- `python tests/run_tests.py test_review` → the two shape-assertion tests pass.
- `python tests/run_tests.py test_assets` → the hospital 43%-hole error clears.
- **Flagged, don't assume fixed**: `test_the_auto_crop_contains_no_invented_pixel`
  ("28 invented pixels at grow=3") looks like a boundary/rounding smell, not obviously
  proportional to yaw. If it survives the flip, it's an **independent auto-crop bug**
  that yaw merely exposed — file it separately, don't fold it into this fix.

## B2. Planar Save silently discards the user's four corners

- **`src/bpc/gui.py:1341`**: `self.session.save(dst)` unconditional.
- Factor the preview's own predicate (`gui.py:1165-1166`) into a helper:
  ```python
  def _planar_active(self):
      return (getattr(self, "v_planar", None) is not None
              and self.v_planar.get()
              and len(self.session.planar_quad) >= 4)
  ```
  Use it in both `_redraw` (replacing the inline check) and `_save`, so Save and the
  preview can never disagree.
- **`src/bpc/gui.py:1340-1344`**: branch on it —
  ```python
  if self._planar_active():
      self.session.save_planar(dst)
  else:
      self.session.save(dst)
  ```
  `review.py:823 save_planar` is already correct. If fewer than four corners exist,
  the preview already shows the rotation result, so saving that is consistent — just
  say so in the status line.
- **Per-photo state is already safe** — confirmed: `ReviewPanel.load()` (`gui.py:353-383`)
  destroys all children and calls `_build()`, which recreates `v_planar = False`
  (`gui.py:567`) against a fresh `ReviewSession`. No leak into the next photo.

**Verify**: nothing currently covers this. Add a headless test in `tests/test_review.py`
(already in `MODULES`) asserting `save_planar` writes `planar.target_size(quad)` and
differs from what `save` writes for the same session, plus an off-screen GUI test per
`skills/ui.md:44-50` (ticks Planar, sets four corners, calls `_save`, asserts written
shape). If it lands in a new module, it must be added to `tests/run_tests.py:19-25`.

## B3. `set_roi_x`/`clear_roi_x` unreachable; a documented flag doesn't exist

- `src/bpc/review.py:747` / `:764` — implemented, tested by nothing, called by nothing.
- `src/bpc/config.py:86` references `--roi-x`; it doesn't exist in `cli.py`.
- **Now (zero risk)**: drop the `--roi-x` half of the `config.py:86` comment; record the
  feature as *not started* in CLAUDE.md §12 with a pointer to the two `review.py` entry
  points. Wiring a UI for it before the B5 cap decision is premature.
- **Later, once B5 is settled**: `--roi-x X0:X1` beside `--horizontal` (`cli.py:95-103`,
  `settings_from` at `cli.py:304-307`), plus a drag gesture on the before-canvas.
- **Verify**: pin the existing behaviour with a headless test — `set_roi_x` returns
  `False` and changes nothing for a strip narrower than 5% of frame width
  (`review.py:758-760`); `clear_roi_x` returns whether there was anything to clear.

## B4. Process-wide, permanent monkeypatch of `subprocess.check_output`

- `src/bpc/birefnet.py:61-68` — module-level, unscoped, never restored; converts any
  bare `OSError` from any caller, process-wide, for the process's lifetime.
- **Fix** — scope it:
  ```python
  @contextlib.contextmanager
  def _oserror_as_filenotfound():
      orig = subprocess.check_output
      def shim(*a, **k):
          try:
              return orig(*a, **k)
          except FileNotFoundError:
              raise
          except OSError as e:
              raise FileNotFoundError(str(e)) from e
      subprocess.check_output = shim
      try:
          yield
      finally:
          subprocess.check_output = orig
  ```
  Apply at `birefnet.py:225` (`import torch` inside `_load`, already under `_LOCK` at
  `:216`) and at `birefnet.py:419` (`import torch` at the top of `foreground`, before
  its `_load` call) — or move that import below the `_load(...)` call so only one
  guarded site exists. `backends()` (`:323-340`) uses `importlib.util.find_spec`,
  imports nothing, needs no guard. **Keep the existing rocm-sdk/WinError-6 comment
  verbatim** — it's the only reason anyone will understand why the guard exists.

**Verify**: extend `tests/test_birefnet.py` (already in `MODULES`, no torch needed) —
assert `import bpc.birefnet` leaves `subprocess.check_output is` the stdlib original,
and that a bare `OSError` inside `_oserror_as_filenotfound()` surfaces as
`FileNotFoundError` while one outside it does not.

## B5. (New open item — do NOT fold into B1) `max_horizontal_deg = 90.0`

Flipping B1 to `False` hides this rather than resolving it. `config.py:96` sets a 90°
cap; `cli.py:102` inherits it as `--max-horizontal`'s default; `warp.limit`
(`warp.py:49-56`) clamps and flags. The next person who passes `--horizontal` on
`hospital-nikon-d60_f27.jpg` gets the same 43% hole and the same refusal. Genuine
tension: `config.py:88-93`'s measurement says real corner views need a wide cap, but
wide cap + `refuse_beyond_limit` + `fill_max_share=0.35` means a legitimate large-yaw
correction gets produced and then refused at write time. **Needs its own measured
pass** — narrower default cap, or route large-yaw corrections through crop instead of
fill. List in CLAUDE.md §11; do not fix blind.

## B6. (New open item) `tests/test_planar.py` orphaned from `MODULES`

`tests/run_tests.py:19-25` omits `test_planar` — its 7 test functions have **never
executed**. Add it. **Not free** — expect findings from 7 never-run tests; treat any
as real. `CLAUDE.md:1159`'s test count changes as a result. Also: ~14 untracked scratch
files at the repo root (`_probe_*.py`, `_gui_merge_test.py`, `_tests_out.txt`,
`full_suite.log`, `_analysis_run.txt`, …) should be cleaned up before any commit.

---

# Dev tooling (separate from A and B — not part of the CV architecture)

A local Unsloth Studio server is live on the LAN at `http://192.168.188.114:8888`,
serving Qwen-family GGUF models, mostly idle. Running
`unsloth start claude --as-subagent --model <repo:variant> --reasoning-effort medium`
in your own terminal lets a separate Claude Code CLI session use it as a local coding
subagent for mechanical work (localized fixes, test scaffolding, rote refactors) —
saving Anthropic tokens while architecture/cross-module debugging/final review stay
with the primary model. This is the same delegation contract the Master/Worker block
at `CLAUDE.md:3-46` already describes — recommend consolidating both into
`docs/dev-workflow.md` with a 3-line pointer left in CLAUDE.md. **Trade-off**: content
moved out of CLAUDE.md stops auto-loading into every session — if the delegation
protocol must be active by default, keep it in CLAUDE.md, just at the bottom under a
"Dev workflow (not project knowledge)" heading instead of displacing the standing rule.

**Note on remote dispatch**: this Claude session cannot message a plain CLI session
directly (`send_message` only reaches sessions the CCD session store tracks; a raw
`unsloth start claude ...` terminal session doesn't register there) — the user runs
that session and feeds it tasks from this plan themselves.

---

# Recommended sequencing

**Phase 0 — one decision, blocking (user).** Yaw on or off by default. Recommended: off.

**Phase 1 — the bugs** (small, high confidence, measurable). Order: B1 → B4 → B2 →
doc-honesty half of B3 → B6. Run `python tests/run_tests.py` after each, full suite at
the end. Success: the five named failures resolve, or
`test_the_auto_crop_contains_no_invented_pixel` is isolated and re-filed as its own
finding. Do B6's `MODULES` addition last so new planar failures stay attributable.

**Phase 2 — the CLAUDE.md rewrite.** Written *against the post-Phase-1 state* — four of
its statements only become true after the fixes land. Target 1269 → ~500 lines,
overflow into `docs/accuracy.md`, `docs/detectors.md`, `docs/masking.md`, new `docs/fill.md`.

**Phase 3 — measured follow-ups, own pass each.** B5 (90° cap vs. fill-share
interaction), `--roi-x` wiring, a batch-level yaw control in `App._settings()` per
`skills/ui.md`'s "add, don't restructure" rule.

**Phase 4 — GeoCalib/AnyCalib/lensfun.** A separate project phase. New optional
dependency stack, new pipeline stage, licence review, its own benchmark (must beat
0.66/1.68). Ships as **documentation only** in this pass — CLAUDE.md §14, no code.

### Critical files
- `CLAUDE.md`
- `src/bpc/config.py`
- `src/bpc/gui.py`
- `src/bpc/birefnet.py`
- `tests/run_tests.py`
