# Perspective Correction — working notes (live)

**Rewritten 2026-09-20 from measurement, not from its own previous text.** The
version before this one was five days stale and confidently wrong about the
thing that matters most: it recorded the suite as "307 tests, 2 failed" when
`--full` could not run at all. Everything below was either re-measured today or
is marked with where it came from.

## Governance — three files, one status

**Corrected 2026-09-26 (audit-caught): this used to say "four files" and list
a `debug.md` that no longer exists.** `debug.md` is retired
(`cae2160 debug.md is done: the living part kept, the file retired`) — its
still-relevant findings were folded into this file directly; there is no
fourth file to read.

| file | holds | rule |
|---|---|---|
| **`CLAUDE.md`** (this) | **status, hard rules, how to run things** | the only place status lives |
| **`QWEN.md`** | worker/agent operating rules — coordinate scaling, vision, MCP tools | **no status.** Points here instead |
| **`knowledge.md`** | analysis + external sources for the open research goals | read only when this file points at a section |

This file is kept small on purpose so it fits a context window with room for the
conversation. After every completed feature or fix, update *this* file — a
change that lands without its note here is not done.

**Status lives in exactly one place: the Ledger below.** Open, in-progress, done
and merely-wished items are one list; a wish is recorded the moment it is asked
for. Never restate a status in a second section. Two lists that must agree will
not — that is how P11/P12 stayed written as open for a day after they landed,
and on 2026-09-20 it is how CLAUDE.md and QWEN.md ended up describing two
different projects.

Work runs to completion without check-ins; report back when tests pass and every
open item is closed or blocked on a user decision.

## Project in one paragraph

Straightening converging verticals in architectural photographs (roll, pitch, f —
three numbers; the horizon is `K^-T u`, never detected).

**Direction, 2026-09-13 (user): manual review is the product; unattended batch is
not.** If several photographs are processed it is image by image with a person
looking at each one.

This inverts the reasoning every early default was built on. The old premise was
*a photo left alone costs nothing, a photo warped on a bad hypothesis is gone,
so when in doubt do nothing* — that is why caps refuse rather than trim and why
confidence is multiplicative so any factor can veto. **With a person reviewing,
those costs are no longer asymmetric**: refusing early now costs a correction the
user wanted. Each cap is therefore re-decided as a *review-panel* default, with
its own measurement, before it moves. `max_horizontal_deg` has moved twice on
this argument (8 → 30 → 60); `max_pitch_deg` and `max_roll_deg` were measured and
left alone (see Done).

## Standing orders (user) — follow every session

Standing instructions from the user, recorded 2026-10-20 after being repeated.
They outrank convenience; a session that skips one is a failed session.

- **Test before you show.** Every projection or UI change is verified
  headlessly — a repro that measures the actual output, plus the relevant test
  module — BEFORE it is shown to the user. No half-baked output. The user's
  words: *"bitte test das selbst, bevor du mir halbgares Zeug vorsetzt."* A
  change that only `py_compile`s is not tested.
- **Always add a prompt.** When a feature engages or state changes, show a
  status/prompt saying what happened and how to reverse it — never switch state
  silently (e.g. starting in fullscreen says so and says ESC exits it).
- **Don't forget the orders.** The orders in this section are standing; re-read
  them at the start of a session. Forgetting one is the failure the user means.

## Running things (do not rediscover this)

- Project root `D:\Coding\Perspective-Correction`, Windows, PowerShell 7,
  Python 3.12.9 (python.org), 32 cores.
- Compile: `python -m py_compile src/pc/gui.py` (and anything else touched).
- One module: `python tests/run_tests.py test_gui`.
- **The runner has two modes, and the bare command is not the full suite.**

  | command | what it runs | measured 2026-09-26 (audit-caught: this row said 337/290 from 09-20, already stale against the Ledger's own Done section) |
  |---|---|---|
  | `python tests/run_tests.py` | fast — skips `test_gui`, `test_assets` | **303 tests, 0 failed, ~23 s** |
  | `python tests/run_tests.py --full` (or `PC_FULL=1`) | everything, 23 modules | **353 tests, 0 failed, 2 skipped, ~77 s** |

  The fast run says so on exit (`!! FAST RUN -- did NOT run: test_assets,
  test_gui`). **Green there does not mean green.** `-s` forces the old sequential
  run. Any suite number quoted anywhere must say which mode produced it.
- **The 2 skips are real and expected**: `test_files_marked_skip_are_refused` and
  the `*_upright.*` check have no matching assets in the current pool. They are
  skips, not failures — but see the runner gotcha below for why they used to be
  fatal.
- **CI runs the FAST run only** (`.github/workflows/tests.yml:33` —
  `python tests/run_tests.py -v`). So "CI is green" has never been evidence about
  `test_gui` or `test_assets`. If you want CI to mean what people assume it
  means, that line is what has to change.
- `test_assets` samples **5 photographs**, not the pool (`MAX_ASSETS`,
  `tests/test_assets.py:63`). `_ALWAYS` (`:68`) force-keeps `*_upright.*`,
  `*_skip.*`, the receding-row asset `39079116` and the ultra-wide
  `ultra-weitwinkel` facade, so a known-hard case cannot be silently sampled
  away. **`PC_TEST_ASSETS=0` restores the whole pool and must be
  used before writing any pool-wide number into this file.**
- A new `test_*.py` must be added to `MODULES` in `run_tests.py` or `_unlisted()`
  fails the run. There are **23** modules. (`test_selfupdate` is in `MODULES` since 2026-10-21.)
- `src/pc/selfupdate.py` (229 lines) holds the update button's logic — kept out of `gui.py` so it can be tested without a window; it checks and applies a fast-forward-only pull, refusing a dirty tree or diverged history. Tested by `tests/test_selfupdate.py` (193 lines), which builds throwaway git repos rather than mocking.
- GUI off-screen pattern: `App(start_maximized=False)`,
  `geometry("<WxH>-4000+0")`, pump with `update()` + `sleep(0.02)`, then
  `destroy()`. `invalid command name ..._pump` on teardown is harmless Tk noise.
- `python tools/debug_ui.py` sweeps the window lifecycle; must end
  `FAILURES: none` with `pc_errors.log` clean. **It does, as of 2026-09-20** —
  re-run after the mask-row change. Before that it had been failing on a Tk
  variable that no longer exists; see the Ledger, and do not trust a green from
  a diagnostic you have not checked is still pointed at something real.

## Hard rules (the ones that bite)

- **No pixel arithmetic in `gui.py`**; sizes live in `layout.py` and are tested at
  five resolutions. `review.py` has no Tkinter import — pure state functions,
  tested headlessly; `gui.py` is only the shell.
- **Never mix coordinate spaces.** Canvas pixels and full-resolution pixels are
  two different things, and the conversion is `_before_scale`. Store divided,
  draw multiplied, hit-test with `display_scale`. **This has been broken and
  re-fixed more than ten times.** The full rule, with the checklist, is in
  `QWEN.md` — read it before touching stored geometry or its display.
- **Optional backends install with `--no-deps`.** `simple-lama-inpainting`
  downgrades Pillow/numpy and breaks OpenCV in the same interpreter; a test bans
  `lama`/`ultralytics` extras from `pyproject.toml`.
- `H = K R K^-1` decides what comes out straight — a camera rotation, three
  DOF, cannot shear. **One deliberate addition (2026-09-26, user-directed):**
  with a yaw in play `warp.build` follows it with a pure x-scale
  `sqrt(cos yaw)` (`YAW_FORESHORTEN`), which changes proportions only and
  keeps every horizontal horizontal and every vertical vertical. Seeded RNG
  everywhere. Confidence is multiplicative, so any single factor can veto.
- **Beyond the limit means refuse, not trim** (`--clamp-beyond-limit` restores the
  old cap behaviour). Magnitude test, not semantic. *Exception, measured and
  deliberate:* a **pure-yaw** breach warns and applies capped rather than refusing
  the whole correction (`pipeline.py:265-292`) — P9 measured that refusing whole
  threw away 25 photographs whose roll+pitch were fine.
- **Every colour comes from `INK`, and anything RENDERED must be re-rendered.**
  Two traps, both paid for: `_retint_bg` matches widgets **by class**, so adding a
  widget class means adding it to that list; and **a walk cannot recolour an
  image** — icons and the brand mark are `PhotoImage`s whose tint is baked into
  pixels, so `_switch_theme` rebuilds and re-renders them. Copy `_logo_image`:
  `Logo_BPC.png` is a black silhouette and only its **alpha** is used, so the mark
  follows the theme. The check is one gesture: switch theme, look for anything
  still wearing the old colours.
- **`INK` is a module-level dict that `_switch_theme` mutates in place**
  (`gui.py:4790`, `INK.update(new)`). It outlives the `App` that changed it. Any
  test that switches theme **must restore it** — one that did not made an
  unrelated test fail for a day. See the Ledger.
- **Mechanical work goes to the worker; the architect measures and decides.**
  Worker: deletions, renames, applying a written spec, a pattern in several
  places, read-only hunts with `file:line` citations. Architect: deciding what
  should be true, and **running the thing to find out what is** — the half a
  worker cannot do. Writing the spec is also what makes you state the change
  exactly; a spec that is hard to write is a change that was not thought through.
- **A worker or subagent finding is a pointer, not a verdict. Every HIGH gets RUN
  before it gets applied.** Measured 2026-09-15: of six findings with citations,
  severities and copy-paste patches, **five were wrong**, and both HIGHs would
  have broken working code — one "fix" to `geometry.py:209` measured an
  orthogonality error of 0.19 against the existing code's 4.4e-17 and returned the
  wrong shape. Grep the names, run the arithmetic, *then* read the patch. A
  fluent proposal is not evidence, and severity is the reporter's confidence, not
  the defect's. The wrong ones pass the suite too.
- **A document claiming something is not evidence of it.** Paid for three times in
  one day: a docstring asserted a caller that could not exist, a comment asserted
  bindings that did not exist, and the environment table asserted a package was
  absent that was installed — producing a confidently wrong diagnosis from both
  the worker and the architect, because both read instead of running. **Ask the
  interpreter, run the command, open the file.**
- `D:\Batch-Perspective-Correction` and `D:\Coding\Batch-Perspective-Correction`
  are **older copies and off-limits** — do not write, sync, run tools or measure
  anything there. A suite was once run against the wrong tree that way.
- Anything fiddly belongs in Python, not in a `.bat`.

## Environment split

**Ask the interpreter, never this table.** It is recorded because it is useful,
and it has been wrong before in a way that cost a day.

| | torch | transformers | tkinter | BiRefNet |
|---|---|---|---|---|
| system python.org 3.12 | yes 2.12.1+cu130 | yes 5.17.0 | yes | yes |
| ComfyUI `python_embeded` | yes | yes | **no** | yes |

`--mask birefnet` and `--mask gdino` both run in the GUI's own Python. The
`--mask-export` bridge and the ComfyUI route still work but are no longer the
only way. **SAM2 is the one that genuinely still subprocesses**, because it needs
the `sam2` package rather than `transformers` — `src/pc/sam2seg.py` shells into
`python_embeded` and hands a PNG back.

M-LSD needs no second interpreter: its TFLite runtime (`ai-edge-litert`, the
declared `mlsd` extra) is in the **system** interpreter, so
`--detector mlsd` runs there directly. Reinstall with
`pip install --no-deps ai-edge-litert`.

## Ledger — the only place status lives

**Suite, measured 2026-09-26 (corrected — this line said 337/09-20, stale
against this file's own Done section, which already said 353; audit-caught,
see Open item 15's history): `--full` = 353 tests, 0 failed, 2 skipped,
77.0 s.** Fast run = 303 in ~22.6 s and is not the suite. **And the honest one:
`PC_TEST_ASSETS=0 python tests/run_tests.py test_assets` over the whole
photograph pool = 11 tests, 0 failed, 2 skipped, 133.7 s** — `--full` still
samples 5 photographs, so it is not by itself a claim about the pool. Re-run
before trusting any of this; it is a measurement, not a promise, and no entry
below may restate it.

An item stays under **Open** until nothing is left to do. **Done** is only for
finished work.

### Open — do these

Ordered by the user's 2026-09-14 instruction: *easy first, tools and mask
features ahead of everything else.*

**A. Tools and UI — small and visible.**

1. ~~**Tool icons as one coherent set; bigger buttons; typography and
   spacing.**~~ **Closed 2026-09-20, all four halves.** Button sizes and the
   type scale landed 09-14; the icons landed today; and **the spacing was
   measured and deliberately left alone** — see the Ledger for why, because
   "we chose not to" is a different answer from "nobody got to it" and the
   entry should not be re-opened as if it were the second one.
2. **[landed 2026-09-20] `pixel_reference_edge`: a dial over how much
   resolution a yaw correction may throw away, with a GUI control.**
   **The defect first**, because it was invisible in the output size: with
   horizontal auto on, the canvas grew to **2.3-7x the source area while the
   near edge was still being sampled at 0.71-0.95** — real photographed detail
   discarded. `H = K R K^-1` at a large yaw inflates the receding edge five- to
   sevenfold and `_whole_frame` then scales the whole output back so the facade
   keeps *about* its source pixel count; on average that is right, and at the
   near edge — where the detail actually is — the average is dragged up by the
   edge that was inflated. **The same bug class was fixed once already** for PC
   Rectangle (`8aed6dc`, "scales up, never down"); the yaw path never got it.
   Now `warp.min_magnification` measures the worst sampling still visible and
   `_keep_near_edge` scales up against it, bounded by `NEAR_EDGE_MAX_SCALE = 3`.
   **`max_area_ratio` deliberately does not bound it** — its own comment says it
   is for trimming fill zones, and letting it clamp left two assets at 0.84 and
   0.98 when the whole point is 1.0.
   **How far to go is taste, so it is a dial — `keep_pixels`, 1 = MAX px,
   0 = MIN px** (user's own framing, 2026-09-20). Measured over 8 corner views:

   | `keep_pixels` | min sampling | canvas vs source | linear detail lost |
   |---|---|---|---|
   | 1.00 max px | 1.08 | 6.44x | 0.0% |
   | 0.75 | 1.03 | 5.75x | 4.8% |
   | **0.50 (shipped)** | 0.98 | 5.16x | 9.2% |
   | 0.25 | 0.94 | 4.65x | 13.4% |
   | 0.00 min px | 0.90 | 4.22x | 17.3% |

   **The default is 0.5, the user's call, and it is the honest one.** The
   arithmetic alone argues for keeping more — the whole range moves the canvas
   by only a third while detail loss runs 0 to 17.3% — but rendered side by side
   at equal magnification **the two ends are indistinguishable on this pool**,
   every photograph in which is downscaled to ~1800 px and has no fine detail
   left to lose. Neither end is defensible over the middle on the evidence that
   exists. Measured at the shipped 0.5: mean worst sampling **0.999**, i.e.
   almost exactly the point where nothing is discarded. The name was inverted
   from an earlier `pixel_reference_edge` (0 = keep most), which read backwards.
   **GUI**: a `ttk.Scale` labelled **"detail (px kept)"** under the
   horizontal-auto checkbox, greyed out while no yaw is in play, with a readout
   that says `max px` / `min px` at the ends because "0.50" alone explains
   nothing. **The wording was drafted by the local Qwen worker and then
   corrected** — its help text was better than the architect's and is mostly
   kept, but its recommended label, "size vs detail", is wrong and so was its
   reason for it: it claimed keeping more pixels keeps *less of the frame*,
   which `keep_pixels` never touches (cropping is `plan`'s job), and there is no
   "vs" here at all — size is the price, detail is the benefit, and they move
   together. The same confusion was in its opening help line. **Fourth time a
   confident worker recommendation has not survived being checked.** Built by a subagent to spec; the architect read the diff
   and re-ran every check. Two things it got right that the spec had wrong —
   worth keeping: a `ttk.Scale`'s `command` fires only when the **widget** moves,
   so `v.set(...)` left `settings` behind (the "tested is not reachable" trap
   inverted), hence a `trace_add` on the variable; and the once-only variable
   outlives the session, so a fresh session needed the shown value pushed
   through or the widget would lie on photograph two. Pinned by
   `test_the_detail_slider_is_greyed_out_unless_a_yaw_is_being_corrected`,
   verified red by sabotage.
   **Checked, NOT a bug**: the slider stays live when the h-marker is on and
   `correct_horizontal` is off. `current_yaw()` (`review.py:1096`) calls
   `self.marker_yaw()` (`review.py:990`) at line 1116, ahead of that flag,
   so a yaw really is applied and the dial really does
   bite — the tooltip was the thing that was wrong and now names all three yaw
   sources.

3. **More tools — only where a gesture is already being done the long way.**
   Not a wish for its own sake. Shortcuts are done (`m` mark, `b` brush,
   `p` planar, `s` SAM).
4. **[measured 2026-09-20, user-directed] A second correction pass works — but
   only if it is forbidden to make a large one.** User's hypothesis: with
   horizontal auto on, the first warp leaves distortion a second analyse+correct
   could clean up. Measured over **11 corner views, nothing excluded**
   (`min_confidence=0`, `refuse_beyond_limit=False` — a refusal is a result, not
   a missing datum), horizontal lean measured inside the hand-placed facade strip:

   | pass 2 allowed | mean | gain | better/worse | worst case |
   |---|---|---|---|---|
   | uncapped | 5.386 | +0.045 | 6 / 5 | **-3.74** |
   | **capped to 10 deg** | **4.847** | **+0.584** | **8 / 3** | -1.75 |
   | capped to 5 deg | 4.992 | +0.439 | 7 / 4 | -1.75 |
   | capped to 2 deg | 5.133 | +0.298 | 7 / 4 | -1.77 |

   **Uncapped it is a coin flip with catastrophic tails**: on `39079116` pass 2
   asks for **yaw -46.2 deg** and on `1070912stkzg` **+45.4 deg** — it re-estimates
   a fresh full-range correction on an already-yaw-corrected frame instead of a
   residual. Cap it and both are fixed (13.21 -> 9.15, 7.14 -> 5.28).
   **Nothing was built.** Two things must be settled first: (1) the project's own
   rule is **one resample, never two** (the distortion roadmap's Stage 5), so this
   ships as `H2 @ H1` composed into a single `remap`, never as two warps — the
   gain here is ~0.6 deg and a second resample costs sharpness that was not
   measured; (2) 10 deg beat 5 and 2, which is suspicious for a *residual* cap and
   suggests the number is doing something other than what it is named for.
   **Order matters and was measured**: `crop="inside"` before pass 2 moved pass 1's
   own lean 5.353 -> 4.969, and cropping to the strip +20% before pass 2 was worth
   more than the second pass itself. The margin matters too — **20% beat 30% by 8x**
   (+0.184 vs +0.023), which was the user's own correction to a first guess of 30.

5. **[measured 2026-09-20] Placing the facade strip needs somebody to LOOK, and
   the confidence gate then refuses the result.** Two findings, one experiment.
   **(a)** A strip read off the photograph by eye beats the blanket 20-80% default
   by **+0.364 deg against +0.131** on 11 corner views. A blanket strip is not an
   application of the feature; on a corner view it straddles both walls, which is
   what the feature exists to prevent. The strips are now recorded in
   **`tests/assets/roi.json`**, each marked with how carefully it was read — and
   the one entry checked at full size (`Platte`) was **wrong**, read off a 440px
   thumbnail as 0.05-0.50 when the foreshortened left wall ends at 0.17. Assume
   the same of every entry still marked `thumbnail`.
   **(b) The narrow, correct strip is the one the tool refuses.** Placing it
   properly drops **7 of 11 corner views from OK to SKIPPED**, confidence falling
   e.g. 0.67 -> 0.20 (`35559_XXL`), 0.69 -> 0.34 (`39079116`), 0.40 -> 0.05
   (`images-(1)`), weakest factor `stability` in 6 of 7. Confidence is
   multiplicative and counts evidence, so restricting evidence to the right wall
   reads as *less certainty* — **a deliberate, informed user action is scored as
   weakness.** On `Platte` all three strips tried return essentially the same yaw
   (-42.8 / -43.3 / -43.1 deg) while confidence runs 0.20 / 0.49 / 0.54: here the
   strip moves the confidence and not the answer. This is the same mechanism
   already recorded for verticals in the mask-registry entry (~0.11 of confidence
   every time, buying nothing).
   **(b2) CORRECTION, same day, and it matters: that collapse is NOT what a user
   sees.** Those numbers come from `pipeline.analyse(roi_x=...)`, which restricts
   the evidence *before* the vanishing-point search. The review panel's
   `ReviewSession.set_roi_x` filters an already-detected pool instead, and across
   the same 11 corner views it barely moves confidence at all: 0.59 -> 0.59
   (`Platte`), 0.54 -> 0.54 (`Platte_1`), 0.67 -> **0.68** (`35559_XXL`),
   0.40 -> 0.44 (`images-(1)`); the largest fall is 0.69 -> 0.57. **Not one of
   the eleven crosses the gate in the review path.** So the same control, named
   the same thing, does two different things depending on where it is set — and
   the alarming half of this entry describes the batch path only. Found by
   writing a test against the review path and watching it stay green with the
   clause it was testing removed. **The inconsistency is itself the open item**:
   two applications of "the facade strip" that disagree is a defect whichever one
   is right, and nothing here establishes which.
   **(c) But the fix is not the gate — it is where the strip ends** (measured on
   `Platte_1`, sweeping the right edge; the corner seam is at 0.33):

   | strip | conf | yaw | lean in the left wing, 1 pass / +2nd pass |
   |---|---|---|---|
   | 0-32% (at the seam) | **0.33** refused | +45.24 | **4.99** / 6.56 |
   | 0-48% | 0.57 | +45.31 | 5.14 / 5.51 |
   | 0-55% | 0.54 | +45.17 | 5.24 / 5.49 |
   | 0-62% | 0.52 | +45.83 | 5.22 / **4.88** |
   | 0-68% | 0.59 | **-39.66** | 8.28 / 8.23 |
   | none | 0.54 | -40.03 | 8.46 / 8.31 |

   **A strip that stops exactly at the corner gives the best single correction and
   is the one the gate refuses. Running it well PAST the seam keeps the same yaw
   to within 0.7 deg and buys the confidence back** — 0.33 to 0.52-0.57. Past
   roughly twice the seam the other facade wins the vote, the yaw flips sign, and
   the wall it was aimed at comes out **worse than uncorrected** (8.28 against
   6.91). So: wide of the seam, short of the flip. `Platte_1` is also the asset
   that shows the strip changing the *answer* rather than only the confidence —
   +45 deg for the left wing against -39 for the gable end, opposite rotations, as
   two walls of a corner should be. On `Platte` all three strips agreed on the yaw
   and moved only the confidence, so both behaviours are real and neither
   generalises. **Blocked on a decision**: whether a hand-placed strip should enter
   the confidence terms at all. Do not tune `min_confidence` before deciding —
   this measurement says the strip's *extent* is the lever, not the threshold.
   **(d) What was actually built, 2026-09-20.** The gate was first removed from
   `pipeline.process` — the wrong path, and the user caught it by looking:
   `Alte_Scheune` (conf 0.39, weakest term the focal length) then went through
   with a **-26.5 deg yaw**, came out visibly sheared, and bought 0.5 deg of
   verticality for it (lean 3.31 -> 2.81). *"leider sehr schief"*, and that was
   right. **`process` is the unattended run** (`cli.py:375`, `gui.py:5359`):
   nobody sees what it writes, so the batch-era reasoning holds there in full and
   the veto is back. The rule the measurement argues for belongs in
   `review.would_skip`, which already exempts control lines on exactly this
   ground — *"refusing evidence because there is little of it is right when a
   detector produced it and wrong when a person did"* — and a hand-drawn strip
   now joins that list. Pinned by
   `test_a_hand_placed_facade_strip_stands_the_review_gate_down_but_not_the_batch`,
   which asserts **both** halves so loosening one cannot quietly loosen the
   other, and which had to be rewritten once: its first version picked an asset
   that sits below the gate by luck and stayed green with the clause deleted. It
   raises `min_confidence` to 0.95 now, so the gate is engaged by construction
   rather than by hope.

6. **P4 — two-facade warning.** **The detection half was measured 2026-09-20 and
   does not work; nothing was built.** See the Ledger. It is now **blocked on
   `knowledge.md` §1 (facade-outline-first)**, not on effort: a threshold over
   `ArchitectureScheme`'s post-hoc split cannot represent the case, because the
   split itself under-detects the second plane.
7. **P3 — show the multiple horizontal VPs as markers.** Cosmetic, and it
   overlaps the found-geometry overlay research goal below; decide which one is
   being built before building either.

**B. Correctness and infrastructure.**

7. **CI's `full-windows` job exists but has never run — watch its first run.**
   Until 2026-09-20 CI ran only `run_tests.py -v`, the fast run, so it had
   **never executed `test_gui` or `test_assets`** and "CI is green" was never a
   statement about the GUI or the photographs. A sibling `full-windows` job now
   runs `--full`; the original `test` job is untouched and neither gates the
   other. Windows-only deliberately: the Linux leg is failing for an unrelated
   undiagnosed reason (item 6), and `test_gui` opens real Tk windows.
   **This is NOT done, and the reason is written here rather than assumed away.**
   `test_gui` positions its windows off-screen at `geometry("<WxH>-4000+0")`,
   and whether a GitHub runner's single virtual display tolerates a negative
   origin is **unverified and cannot be verified from this machine**. A runner
   also has 2 cores against the 32 the ~67 s was measured on, so it will largely
   serialise. **The first run of that job is the experiment.** If it goes red,
   read the log before touching the job: a red that says the display cannot
   place the window is a different answer from a red that says a GUI test
   genuinely fails, and only one of them is about this project's code.
8. **CI Linux is failing** (ubuntu-latest, 3.9 and 3.12; Windows passing) —
   recorded 2026-09-19. **`gh` is not installed on this box and the Actions logs
   cannot be read from here**, so this is diagnosed only by elimination:
   - **Python version is ruled out, definitively.** The repo parses clean against
     the 3.9 grammar with no runtime-evaluated unions (64 files) — and the job
     fails on 3.9 *and* 3.12 anyway, so a syntax floor was never a candidate.
     Pinned by `test_the_source_parses_on_the_oldest_python_pyproject_promises`.
   - **Ranked by a read of every reachable path** (subagent, all claims cited):
     **(1)** `requirements.txt:2-3` is the *only* line in the whole reachable
     path where Linux and Windows install genuinely different packages -
     `opencv-python-headless` vs `opencv-python` - and `cv2` is the one import
     marked `hard=True` (`deps.py:45`), the only kind that makes
     `perspective_correction.py --doctor` exit 2 at CI step 2. Version-independent and
     OS-specific: it fits the failure exactly. **(2)** the `ProcessPoolExecutor`
     start method, now pinned to `spawn` (see Done) - one variable removed.
   - **Ruled out with evidence**, so nobody repeats them: no fast-run module
     imports tkinter or `pc.gui` (only `test_gui`/`test_assets` do, and both are
     SLOW); no `cv2.imshow`/`namedWindow`/`waitKey` anywhere; no case-sensitivity
     mismatch in any reachable import or path; the one non-ASCII asset lives
     under `Horizontal/` and is read only by `test_assets`, which CI never runs;
     every optional backend reports `[no]` without raising.
   **Whoever picks this up: read the log first.** Every hypothesis above is an
   inference from the repo, and this project's own rule is that a document
   claiming something is not evidence of it.

7. ~~**Two agent passes cut off by a rate limit.**~~ **Both finished; closed
   2026-09-20.** Kept as a title so the next session does not re-open it:
   neither had left a half-edited source file (checked at the time — only the
   one test each had completed), the non-GUI findings were adjudicated by hand
   and the `knowledge.md` audit was re-run. **Both outcomes are in Done, and
   the second one needed correcting afterwards** — the audit stamped a wrong
   support figure as verified, which is the entry worth reading.

8. **`gui.py` is 6014 lines** — 3.3× the next largest file (`review.py`, 1809).
   A 12-file composition split was proposed and **deprioritised by the user**. It
   stays deprioritised; recorded so it is not re-proposed as if new.
9. ~~**`cli.py` `isatty()` gate**~~ **Struck 2026-09-20: it was not a defect.**
   See the Ledger — the gate refuses rather than proceeding, which is the only
   safe answer for a flag that destroys originals, and `--yes` is the documented
   way to mean it non-interactively.

**C. Decide, do not necessarily fix.**

10. **[found 2026-09-20] SAM2 click-to-select has no tests, and the suite made
    that invisible.** `tests/test_sam2seg.py` is **0 bytes** and has been since
    the feature shipped (`843a76b`, 09-14). It is listed in `MODULES`, so
    `_unlisted()` is satisfied, it "passes", and the total simply never counted
    it — coverage from every angle except the one that matters. This is the
    twin of the failure `_unlisted` was written for, and the runner now prints
    a **LISTED BUT EMPTY** banner beside the FAST RUN one (reported, not fatal:
    turning a pre-existing gap into a red suite makes the red stop meaning
    anything). **Tests were written (103 lines): the config-name derivation, the quoting that keeps a non-ASCII path safe, and the seam that runs without SAM2.** The segmentation path
    is not small — `sam2seg.run_subprocess` shells into `python_embeded` and
    hands a PNG back, `load_mask_png` inverts selection-white to ignore, and
    `apply_sam_mask` converts file-resolution to analysis-resolution, which is
    exactly the coordinate-space rule this file warns about twice.

11. **Shootout suite** — a 20-image benchmark. Estimated 9–10 h, independent of
   everything else, no `tests/shootout/` exists.

**D. Research — measurement passes, explicitly not implementation packages.**

12. **Distortion Stages 1–3.** Stage 0 (`lensfunpy`, EXIF-driven) shipped
    2026-09-14: `src/pc/distortion.py`, `warp.apply_undistorted()`,
    `--undistort lensfun`, 7 tests. No EXIF = graceful skip. Stages 1–3
    (AnyCalib blind fit, GeoCalib gravity prior, cross-check gate) remain, each
    behind its own measurement. Pipeline order is **detect → undistort →
    correct**, one composed `remap`, never two. Keep the 8 % border guard.
    Test case: `Aulendorf_Schloss_Fassade.jpg` (zero EXIF, fragmented lines).
    **The proposed trigger was measured 2026-09-14 and does NOT work — do not
    build it.** `merge_collinear` groups segments *by similar angle* with a 2°
    tolerance (`lines.py:180`), so drift within a chain is capped at ~2° **by
    construction**; the grouper filters out exactly the signal the trigger wanted
    to read. Aulendorf, the one known wide-angle asset, scored 0.04 — near the
    bottom, below ordinary facades. A real trigger would group by proximity and
    continuity and measure residual curvature. Different mechanism, unmeasured.
13. **Four research goals in `knowledge.md`** — read that file before picking any
    of them up. Each needs its own measured comparison first; if the measurement
    says no, write that down and stop. (1) facade-outline-first vs.
    partition-after-detect; (2) a dominant-edge hierarchy in the detector;
    (3) SAM3's text prompts — **largely superseded**, because `sam2seg.py` already
    ships the click-to-select route and the plumbing question is answered; what
    is left is only whether a text prompt beats a click, and it should be weighed
    against that before anything is spent; (4) a found-geometry overlay showing
    the VPs and inlier lines found on *this* photo — the rendering exists twice
    already (`ArchitectureScheme.draw_preview` and the debug overlay), so the
    open part is a GUI toggle and a choice of which to show.

14. **[bug, measured 2026-09-26, user-caught] Yaw is estimated by inverting
    `vp_x = f * tan(yaw)` — well-conditioned near 0 deg, numerically explosive
    as yaw approaches 90 deg, and nothing in the pipeline detects when an
    estimate has landed in that region.** Found on `tests/assets/Horizontal/altbau.jpeg`
    (a real corner-view photo, no EXIF): the tool reports **yaw=63.5 deg**, but
    the 420 detected horizontal segments themselves only show **median 5.6 deg,
    90th pct 16.4 deg** of visible 2D slope — the user's own eyeball estimate
    ("etwa 10 grad") was right, and asking "why does 63 come out of a photo
    that only shows 10" is the right question, not user error.
    **The two numbers are not the same quantity** (2D line slope in the photo
    vs. a 3D camera-obliquity angle) and are related by `tan`, not 1:1 — but
    that alone doesn't excuse 63.5 deg: at that estimate the implied horizontal
    vanishing point sits at **x=8072px, 6.7 frame-widths outside the 1200px
    photo**, extrapolated from segments that only span the visible frame.
    **Measured sensitivity near the reported value**: yaw 61.46->65.46 deg (a
    4 deg wobble, well inside plausible line-detection noise at only 5-16 deg
    of visible convergence) moves the implied VP position **7413px->8830px,
    +19%** — the arctan inversion amplifies ordinary detection noise into a
    swing of tens of degrees once the true angle is this close to 90 deg. A
    forced-focal sweep (18mm to 50mm equivalent) barely moved the estimate
    (58.7-61.5 deg either way), so this is not simply the focal-length
    degeneracy already documented elsewhere in this file — it is the yaw
    inversion itself being ill-conditioned in this regime, a distinct failure
    mode from "confidence doesn't know if it has the right lines."
    **Not fixed.** `max_horizontal_deg` (currently 60) caps in ANGLE space,
    which is exactly the space that explodes here — it does nothing to catch
    an estimate that is precise-looking but numerically fragile.

    **Sharper root cause, measured directly (same day, user pushed back that
    "ill-conditioned" wasn't a strong enough explanation for THIS MUCH visible
    stretch — correctly).** Re-warped the same 420 detected segments through
    `warp.build()` at a sweep of yaw values and measured the residual 2D slope
    that survives correction at each, alongside the warped-quad area it costs:

    | yaw tried | median residual slope after | area cost |
    |---|---|---|
    | 20 deg | 4.4 deg | 1.2x |
    | 40 deg | 3.8 deg | 2.3x |
    | 55 deg | 2.8 deg | 5.8x |
    | 60 deg | 2.4 deg | 9.2x |
    | **63.5 deg (reported)** | **2.0 deg** | **13.5x** |
    | 70 deg | 1.8 deg | 36x |
    | 75 deg | 1.7 deg | 121x |
    | 80 deg | 2.0 deg | 2312x |

    **The fit is nearly flat across the whole 20-80 deg range** (residual moves
    from 4.4 to 1.7 deg, a small change relative to the noise already present
    in the 5-16 deg of raw visible slope) **while the area cost explodes by
    three orders of magnitude over the same range.** The line-fit objective the
    hypothesis search actually optimises cannot distinguish 20 deg from 75 deg
    on this photo — they explain the visible evidence almost equally well — and
    it landed on 63.5 deg, one of the most expensive points in that flat valley,
    with nothing in the objective penalising the cost of being wrong there. This
    is the real bug, not merely "the arctan inversion is sensitive near 90 deg"
    (true, and kept above as supporting evidence, but the sensitivity alone
    doesn't explain choosing an expensive point over a cheap, equally-valid one
    — a hypothesis search that was blind to cost either way would land on
    different values, at random, not systematically on the expensive end).

    **Not fixed. Architect decision needed on which of three directions:**
    (a) add a stretch/area-cost term to the yaw hypothesis scoring itself (in
    `model.py`'s search, not as a post-hoc cap) so that among near-equally-fitting
    candidates the cheaper one wins — the most direct fix, but changes the
    estimator's objective, needs its own accuracy re-measurement against the
    existing benchmark pool; (b) cap/flag in VP-POSITION space instead of angle
    space (e.g. refuse or damp confidence hard once the implied VP sits more
    than N frame-widths outside the photo — cheaper to add, but only a backstop,
    not a fix to the search picking an arbitrary point in the flat valley in the
    first place); (c) report yaw with an uncertainty band derived from how flat
    the local objective is, rather than a bare point estimate. Any of these
    needs its own measurement pass before shipping, per this file's own rule.

15. **[bug, measured 2026-09-26, user-caught] The "full" HA correction leaves a
    real, SYSTEMATIC residual tilt on the very facade it targeted — and
    "Check lines" cannot see it, because its own threshold is the wrong shape
    of check.** On `altbau.jpeg` at the reported yaw=60deg (same case as item
    14): re-warping the model's own inlier horizontal lines (`horiz.subset(dom.inliers)`,
    287 of 420 detected, `dom.support=0.81`) through the applied `H` gives
    **mean -2.20deg on the LEFT (dominant) facade's own lines, mean -2.09deg on
    the right** — not scatter around zero, a one-directional slant on both
    sides. This is real: measured with the model's own inlier set, not raw
    detector noise, and it is what the user saw as "schief wie Hoelle" in both
    my rendered comparisons and their own saved output. **Root cause**: the
    yaw hypothesis search (`model.py`, `horiz_hyps[0]`) picks the most-agreed
    RANSAC cluster by inlier COUNT, which is a consensus criterion, not a
    least-squares fit minimizing total residual slope to zero — a robust
    majority vote can still land a couple of degrees off the line that would
    truly zero the residual, and nothing downstream corrects for that gap.
    **Contrast, same photo, measured 2026-09-26 (prototype only, not shipped,
    no Done entry yet — recorded here since this is where it was measured)**:
    the single-facade PLANAR rectification (strip-restricted
    `auto_facade_corners` + `planar.transform_for`) gave **0.31-0.88deg**
    median residual — 3-7x
    tighter, because it fits the facade's own four corners directly rather
    than inheriting a RANSAC consensus built for a different purpose (finding
    *a* dominant direction, not minimizing final slope).
    **"Check lines" (`gui.py:4073 _draw_after_lines`) gave a false all-clear
    and that is a second, independent bug, not confirmation the first one is
    fine.** Its colour test is
    ```python
    colour = OVERLAY["check_ok"] if abs(dy) <= 1.5 else OVERLAY["check_h_off"]
    ```
    — an ABSOLUTE PIXEL offset on whatever segment the re-detector happens to
    find on the small preview array, not an angle. For a long segment 1.5px
    is strict (~0.3deg over 300px); for the short segments a real detector
    mostly returns (window reveals, coursing, brick joints, 15-25px typical)
    the same 1.5px is **~3.4-5.7deg of slack** — enough to hide the measured
    -2.2deg mean entirely, segment by segment, while the cornice as a whole
    visibly climbs across the frame. The diagnostic's own docstring says its
    job is showing "a residual lean" from "the photograph actually ended
    up" — a per-segment absolute-pixel test cannot do that job; it needs to
    measure the angle each segment makes (`degrees(atan2(abs(dy), abs(dx)))`
    for a near-horizontal one) against a degree threshold, independent of how
    long the piece the detector happened to return is.
    **(i) fixed, same session, uncommitted.** `_draw_after_lines` now uses
    `_off_angle_deg()` against `CHECK_LINE_TOL_DEG` (`gui.py:150-175`, call
    sites at `gui.py:4105` and `:4120`) — an angle, not a pixel offset.
    `python -m py_compile src/pc/gui.py` succeeds; not yet committed
    (`git status` shows `gui.py` modified). **(ii) still open.** The
    RANSAC-consensus-vs-least-squares gap in the yaw search itself is the
    same class of decision as item 14's option (a) — needs its own
    measurement pass, not a quick patch, since tightening the search could
    change which cluster wins on other photographs in the pool.

### Done — rely on these

**2026-10-24**

- **[docs, user-directed] README leads with a simple vertical correction; Windows installs first** ("fix readme. windows installation before apple and linux!"; "too many ha examples... need a simple vertical correction instead"). Install section reordered Windows → macOS → Linux. The hero is now `docs/vertical-correction.jpg` (Barcelona Pavilion before/after, Auto mode roll +6.81° / pitch −6.91°, f 25 mm): made by running the CLI on `tests/assets/Pavilhão_de_Barcelona_-_panoramio_(10).jpg` with `--min-confidence 0` (the pool scene scores conf 0.08, so the batch gate refuses it by default) and compositing before + corrected side by side. The Horizontal auto review-window hero (`docs/screenshot.jpg`) and the synthetic-cubes example (`docs/synthetic-squares.jpg`) are removed; `docs/horizontal-auto.jpg` (Mariánské Hory) is the single remaining HA before/after.

- **[user-directed] The dragged SAM box is the crop box; Shift+drag adds to the selection** ("SAM-Box sollte Crop-Box sein, easy"; "Shift-drag SAM add to selection?"). `ReviewSession.apply_sam_mask(ignore, box)` keeps `sam_box` (full-res px; cleared with the selection); `_kept_box` then maps the box's corners, no margin, and `_kept_source_x` uses its x-range for the HA fold quad. `ReviewPanel._on_sam_done` sets the crop to it even over a hand crop (a new selection is a new framing), turns auto crop on and says "crop set to the SAM box -- drag its edges to widen it". Shift+drag (`SHIFT_MASK`) joins the new segment to the old one and the box to the union of both; a plain drag replaces. The last full-res segment lives in `_sam_ignore_full` (`_sam_selection` is reset by every redraw). `refresh_auto_crop`'s 30 % loss gate no longer throws a mask-made crop away. Schloss repro: crop = the dragged box. Pinned by `test_planar.test_the_dragged_sam_box_is_the_crop_box` (red with the box ignored) and `test_gui.test_the_sam_box_becomes_the_crop_and_shift_adds_to_the_selection` (red without the union, red without the crop call). `test_gui` 61/0, review+planar+warp+sam 133/0, `debug_ui` clean.

- **[user-directed] A mask makes the crop box** ("Ausschnitt schlecht gewählt, Maskierung muss relevant sein", "nur der unmaskierte Ausschnitt, wie ROI", "Maskierung soll Crop-Box generieren"). `ReviewSession._kept_box`: what the SAM selection / automatic mask keeps, outline mapped through the warp, + `STRIP_CROP_MARGIN` (20 %) each side. `auto_crop` clips its search region to it (ahead of centre of interest and centring; with a fill the box is the crop); the HA fold quad takes its x-extent from the kept region like a strip and its height only from verticals on kept pixels (the near colonnade beside the Schloss facade had set the height); `_planar_default_crop` uses the box instead of quad + 20 %. The painted mask counts only from `PAINT_FRAMES_SHARE` = 15 % of the frame ("manuell oft nur punktweise, um Annotatoren zu killen" / "grundsätzlich eher ausserhalb POI maskiert"). Found on the way: the planar homography can carry a negative scale, so "in front" is the majority sign, not w > 0. Repro: Berliner_Schloss + real SAM box -> canvas and crop on the palace (was quad 1360-4244 px wide and quad + 20 %). Pinned by `test_planar.test_a_sam_selection_makes_the_crop_box_and_painted_dabs_do_not` (Auto + HA; red with the box off, red with the paint threshold at 0).
- **Open, user-asked -- DUAL CROP, next to build** ("links grob, rechts exakt"; "der Bildausschnitt rechts soll anders, alles weg was unwichtig"; "links manuell Crop anzeigen"). LEFT: the crop drawn on the original photograph (output rectangle mapped back through the inverse warp = a quadrilateral), edges draggable for coarse changes (map the pointer through the warp, move that output edge). RIGHT: only the crop plus a thin margin (planned `layout.CROP_VIEW_MARGIN` ~0.08 of the crop per side, veil outside), handles for exact changes; view frozen during a drag, re-framed on release. Plan: keep all crop math in full-frame fractions; `_show_after` slices `_after_base` to the view and stores a virtual frame (origin, VW, VH) that `_on_crop_press/_drag/_release/_motion`, `_grab_handle`, `_edge_drag_rect`, `_move_drag_rect`, `_draw_crop_outline`, `_draw_after_outline` and the loupe (`gui.py` ~1917, `source_to_preview`) use instead of `_after_off`/`_ph_a` size. Guides stay in displayed px.

- **[user-directed] Auto crop sits on the middle of the outline for Auto and soft HA** ("bei soften HA-Korrekturen Auto-Crop auf Mittelpunkt Outline (Trapez)", "bei Auto soll das auch so sein"). `ReviewSession.auto_crop`: up to `SOFT_YAW_DEG` = 15 (no strip) the crop is `warp.centred_free_rect` around `warp.poly_centroid` of the warped outline clipped to the canvas (free aspect, scored by source area) and the centre-of-interest box is skipped; without a yaw it is `inscribed_rect` in the photo's aspect around the same centroid (was `max_inscribed_rect`, placed anywhere). Above 15 deg unchanged. Note: `current_yaw()` is RADIANS. Measured, crop midpoint before -> after: Ostenallee (13 deg) 0.61/0.61 -> 0.49/0.52, redbarn (8) 0.45/0.41 -> 0.50/0.49, Antibes y 0.65 -> 0.40; with a fill the growth is clamped at the canvas, so the centre shifts by up to ~0.03 there. Pinned by `test_planar.test_a_soft_horizontal_auto_crops_around_the_middle_of_the_outline` (soft HA, strong HA, Auto; red with the threshold at 0, with the old free rect and with the old Auto rect).
- **[fix, user-caught] A photo with a non-sRGB colour profile was shown too dark** ("wajavilla wird zu dunkel dargestellt"). `wajavill.jpg` carries `sRGB-elle-V2-g10` (gamma 1.0); its raw values were used as sRGB (mean 44 -> 95 after conversion). `imageio._to_srgb` converts on load when the profile moves a test ramp by more than `SRGB_TOLERANCE` = 2 grey levels and then carries an sRGB profile, so the save is tagged right; sRGB and profiles that only rename it stay byte-identical (the 7 other profiled pool photos: unchanged). Looked at side by side. New module `test_imageio` (hand-built linear ICC, no third-party profile shipped): red with the conversion removed.
- **[UI, user-directed] Mask defaults to neon green at 50 %** (`OVERLAY["mask_default"]` `#39ff14`, also a swatch; alpha 0.50 in both panels and the session). **Brush key is Photoshop's quick-mask picture**: grey square, dark disc inside, white square when held.

- **[fix, user-caught] After horizontal auto, the next photo showed Auto and still applied HA** ("HA kommt noch zum Tragen, obwohl Auto-Modus suggeriert wird"). Reproduced: HA on, load Alte_Scheune -> switch "auto", `correct_horizontal` True, yaw -0.57. Cause (confirmed independently by an OpenCode read): `v_corr_mode` is made once and kept "horizontal"; `load` called `_build` -> `_show_mode` first, which turned HA on for the new session, and set the switch to "auto" only afterwards. Now the switch is set from the photo's settings BEFORE `_build`, and a notice says "new photo opens in Auto -- horizontal auto was on for the last one". Pinned by `test_gui.test_a_new_photo_after_horizontal_auto_opens_in_auto_and_applies_auto` (red with the old order).
- **[user-directed] SAM via `deps`, in the program's own Python; Grounding DINO key removed** ("auf jeden Fall"; dino: "zu gross, nervt ... Tool weg"). `sam2seg.segment_local`: SAM 2.1 small (184 MB, `facebook/sam2.1-hiera-small`, pinned revision) through transformers' `Sam2Model`; transformers' image processor needs torchvision (which must match the torch build), so resize 1024 / ImageNet normalise / box scaling / logit upsampling are done here. Measured: Alte_Scheune barn selected whole, 0.66 s CUDA, 2.0 s CPU, 9 s first load. The window uses it when installed, else the old ComfyUI subprocess, else the SAM key switches back off with "install it with deps". `deps` row `sam` (needs torch; pip `transformers` with its light deps, then four size-checked files, `fetch_file` shared with GeoCalib). **Found on the way:** `geocal._stand_ins` left an empty `torchvision` in `sys.modules`, which crashed transformers ("torchvision.__spec__ is None") whenever GeoCalib had run first -- every photo, since GeoCalib runs on load; the stand-ins are now removed once GeoCalib is imported. The Grounding DINO key and its handlers are gone (690 MB model; `--mask gdino` stays in the CLI); the palette has 5 tools. Brush glyph is a grey ring with a dark centre ("aussen grau, innen schwarz"), white when held. README carries a sizes table (program 14 MB; core deps 190 MB; torch 3.1 GB CUDA; all optional parts ~4 GB on Windows). Tests: `test_sam2seg.test_the_in_process_route_selects_the_boxed_building_where_it_is_installed` (skips without the model; red with the mask inverted), `test_depinstall.test_sam_counts_as_installed_only_with_every_file_at_its_size` (red with the check stubbed), `test_gui.test_the_dinosaur_is_gone_and_sam_says_where_it_comes_from`. `test_gui` 60/0, estimation+review+deps+sam 97/0, `debug_ui` clean.
- **[UI, user-directed] Tool palette: one unbroken column, one look per state.** The mid-height split for the strip handle (10-23) read as a missing tool ("Loch in tools", then "zwischen Paste und Mark Luecke"), so the palette is one column again and the handle dodges instead: `layout.strip_handle_y` puts it `STRIP_HANDLE_CLEAR` below the palette (above if that leaves the picture) when its ruler runs over the keys, mid-height otherwise; `ReviewPanel._strip_handle_y` feeds draw and hit test. Keys: the glyph is mid grey (`INK["dim"]`) at rest and turns white (`INK["text"]`) while its tool is held, the key never changes colour ("lieber weiss wenn aktiv, sonst mittelgrau"); the brush was inverted at rest and looked held. The dinosaur was an outline and read paler than the solid marks: drawn 4x, holes filled, scaled down (a 24 px fill ran arms and tail into one blob). `palette_split`/`PALETTE_HANDLE_GAP` are gone. Looked at in screenshots, rest vs held. Pinned in `test_the_strip_handle_is_never_under_the_tool_palette` (red without the dodge, red with a hole: steps 36/64/38) and `test_every_tool_key_is_the_same_size...` (one held colour, one rest colour, a held glyph on every key; red with the brush's accent back).
- **[UI, user-directed] The "mask from" row is gone** ("war nur zu Testzwecken"): source combobox, mask folder, BiRefNet model picker, file polarity. `_pick_birefnet_model`/`_pick_mask_folder` deleted; `v_maskmode`/`v_maskinv` stay hidden (the dino key and a reload drive them). The dino key without BiRefNet weights no longer opens a file dialog (it hung the test run off-screen): it switches back off and says where it looked (`masks.birefnet_home()`) and that `deps` installs it. Mask models are to come from `deps` (proposed, waiting on the user: BiRefNet lite, Grounding DINO tiny, SAM2.1 in the GUI's own Python). **None of the three is on this laptop**, so the mask AI keys did nothing here before either. `test_gui`+`test_layout` 81/0, `debug_ui` clean.
- **[fix, user-caught] The mode switch showed Auto while horizontal auto stayed in force** ("manchmal ist Auto aktiver Schalter, dennoch haengt Programm im HA Modus"). Reproduced on Platte_1: HA, nudge a slider (session MANUAL), press Auto -> flag off but the slider's yaw -40.8 deg still applied; pressing again changed nothing, and HA in MANUAL never engaged (`_rectify_active` needs AUTO). `_show_mode` now returns a manual session to the estimator's angles (`use_auto_angles`) with a notice; `_planar_takes_over` also moves the switch to Auto when it turns HA off. Pinned by `test_gui.test_the_mode_switch_means_what_it_shows_even_after_a_slider_moved` (red without either half).
- **[measured] GeoCalib re-checked on scenes nothing was tuned on** (user: "mach Test und pruefe Ergebnisse"). `benchmark_focal.py --seed`; 150 scenes, seed 777, off vs on: |log f err| 0.242 -> 0.166, pitch err 2.45 -> 1.74 deg, rect aspect 0.044 -> 0.035. Asked in 104 of 150: 63 better, 32 worse, 9 same; the 46 not asked are identical. The worst losses are steep wide-angle frontals where the tilt prior was already right and GeoCalib reads long (median bias +0.15 log): scene 75, 19 mm at 25 deg pitch, 18.3 -> 27.5 mm, pitch err 1.1 -> 8.6 deg. Alternatives tuned on 777 and checked on seed 4242: precision weighting by GeoCalib's own uncertainty (mean 0.224-0.252 vs 0.239, worst 0.82-1.00 vs 0.80) and a bias correction (0.259-0.274) are no better than the shipped geometric mean, so nothing changed.
- **[UI, user-directed] New mark: the pyramid and eye** ("änder Logo ... skalier korrekt", "setz es unten gross", "exe Logo auch neu"). `Logo_BPC.png` is now cut from the user's `Logo_new.png` (white on black, a bright frame artefact on the top row and right column masked off): ink box 839x727, centred in a square with 2 % margin, luminance -> alpha, black silhouette, 512 px. Old file and source in `Trashcan/logo_2026-10-24/`. `_logo_image` fits and centres instead of stretching, so a non-square replacement keeps its proportions. In the foot the mark sits in front of the wordmark at `layout.FOOT_LOGO_PX` = 26, tinted like it and re-rendered on a theme switch; in front looked better than behind in side-by-side screenshots (behind, the "v1.21" superscript wedges between the two). Window icon and `Perspective Correction.exe` / `tools/launcher.ico` rebuilt (`tools/make_launcher.py`; both gitignored, Explorer may show the old icon until its icon cache refreshes). Pinned by `test_gui.test_the_logo_sits_large_before_the_wordmark_in_its_true_proportions` (aspect 839:727 at 26/64/256 px, a 3:1 file stays 3:1 -- red when stretched; order and size in the foot; red without the theme re-render). `test_gui`+`test_layout` 80/0, `debug_ui` clean.
- **[UI, user-directed] The correction values sit exactly centred in the window's foot** ("info text in center of lowest field ... show correction values ... decent, not too flashy"; "exakt mittig"). `ReviewSession.values_text()`: roll · pitch · yaw · f mm (source) · conf, plus "at the limit" when capped; `App._values_lbl` (Dim.TLabel) is `place`d at relx 0.5 so the wordmark and the buttons cannot push it off centre; `ReviewPanel._show_values` refreshes it from `_set_status`. Looked at in a screenshot. Pinned by `test_gui.test_the_correction_values_sit_exactly_centred_in_the_foot_and_follow_the_sliders` (centre within 1 px; red when packed left and when not refreshed). `test_gui`+`test_review` 122/0, `debug_ui` clean.
- **[user-directed] GeoCalib as an optional focal guess, and `deps` is a menu** ("lass uns geo auch noch draufpacken, aber nur installieren wenn erfolgversprechend"; "soll nicht blind installieren. torch lama telea geo"). Measured first, 55 cases with a known focal (48 cube streets + 7 EXIF photos, EXIF ignored): GeoCalib alone is worse than the vanishing points where they decide (|log f err| 0.27 vs 0.07, 22 cases) and helps only where the priors decide (33 cases: ours 0.27, GeoCalib 0.23, geometric mean 0.18). So `model.estimate(learned_focal=...)` asks it only when f_source is the default and f_src is not geometric/horizon, blends by geometric mean, f_source `GeoCalib` (shown in the status line, confidence term 0.65). `pc/geocal.py`: lazy, cached per image, never raises; kornia/torchvision stand-ins (GeoCalib uses them only to resize), so the install is GeoCalib (commit archive, no git) `--no-deps` + 116 MB weights in `models/geocalib/` (gitignored, size-checked, temp+replace with a retry: a virus scanner held the fresh file, WinError 32, on the first real install). Integrated benchmark (`tools/benchmark_focal.py`): |log f err| 0.185 -> 0.129 (worst 0.88 -> 0.57, frontal 0.31 -> 0.22), pitch err 1.70 -> 1.36 deg, rect aspect 0.049 -> 0.038. EXIF photos: WDVS 0.139 -> 0.064, 1070912_corr 0.41 -> 0.26, the 5 geometric ones unchanged. Cost: first photo 4.5 s (torch + model), then ~1.5 s on a CPU, only on photos where it is asked. **`deps` button**: a menu (`depinstall.catalogue`) of the MISSING ones among torch, lama, GeoCalib, mlsd, tkinterdnd2 (installed ones are one "already installed" line and never offered, user: "dont ask for things that are already there"; all there -> a message, no menu) plus telea named as built in; nothing ticked, ticking lama/GeoCalib ticks torch while it is missing (unticking torch unticks them); only ticked rows are installed, torch first (`in_order`); `install` skips lama/GeoCalib when pip finds no torch for this Python (`torch_installable`). `depinstall.missing` is gone (only the old button used it). `tools/install_deps.py --lama` also installs GeoCalib. CC BY 4.0 credit in `CREDITS`. **`PC_LEARNED_FOCAL=0`** turns it off; `run_tests.py` sets it, because with GeoCalib installed every parallel worker loaded torch (11 workers, 13 GB, fast run past 10 min) and results must not depend on what a machine has installed. Pinned: `test_estimation.test_a_learned_focal_guess_is_asked_only_where_the_priors_decide` (red with the gate removed and with replace-instead-of-blend), `test_depinstall.test_the_menu_lists_every_package_...` (red with weights not checked), `..._the_weights_count_only_when_complete_and_survive_a_scanner_lock` (red without the retry), `test_gui.test_the_deps_button_is_a_menu_that_installs_only_what_is_ticked` (red with rows pre-ticked and without torch taken along). Fast run 364/1 (`00010519.jpg`, no mask)/1, `test_gui`+`test_assets` 67/0/2, `debug_ui` clean.
- **[fix, found by Fable, re-measured] Without EXIF the refinement replaced a good geometric focal with the 28 mm default.** Since the horizontal term left `_cost_terms` (2026-10-21) the (roll, pitch, f) refinement saw only the vertical lines and the focal prior; lines through one VP are flat along the f : tilt curve, so fitting f just walked it back to the prior. `model.estimate` now refines (roll, pitch) only. `tools/benchmark_focal.py` (Fable; 48 seeded rendered cube streets, 14-85 mm): all 8 scenes whose focal the refinement set came out at exactly 28.0 mm (truth 24-47 mm); after, 7 of 8 closer (47 mm: 28.0 -> 39.5; 24.1: 28.0 -> 24.4), one worse (30.3: 28.0 -> 22.3, tilt prior). Overall |log f err| 0.198 -> 0.185, frontal 0.325 -> 0.306; pitch err 1.77 -> 1.70 deg; rect aspect 0.055 -> 0.049. Pool (36 photos): round trip unchanged (all mean 0.464, worst 2.94); 13 photos change focal (e.g. Mariánské Hory 826 -> 1022 px, Platte_1 979 -> 1058), verticals stay plumb (residual lean median within 0.03 deg old vs new on 5 checked). Confidence rises on most (the `refined` rating 0.72 is gone) but falls on `images.jpg` 0.47 -> 0.38 (now under the batch gate) and `hospital` 0.51 -> 0.42. altbau, csm_klassik, Struktur, Chateau, Platte, Antibes unchanged. Pinned by `test_estimation.test_the_refinement_keeps_the_geometric_focal_instead_of_the_28mm_default` (red on the old code: all four `refined`, 47 mm at -0.517). Still open in the benchmark: frontal scenes |log f err| 0.31 (the f : tilt ambiguity, decided by the priors), HA cell aspect |log| mean 0.51 over all faces incl. ones with too few horizontals -- not looked at.
- **[user-directed] The `update` button also updates a copy without git** ("ja bau das ein"). The macOS installer unpacks the GitHub tarball when there is no git, and the button then only refused. Now `selfupdate.check`/`apply` route a folder without `.git` to `archive_check`/`archive_apply`: newest commit from the GitHub API, that commit's tarball unpacked into a temp folder first, each file written via a temp name + `os.replace`, exec bits kept. `.pc_install.json` (gitignored) records the commit and a sha256 per written file: a changed file stops the update and is named (the archive twin of a dirty tree); only files the previous update wrote are deleted; `.git`/`.venv`/`venv` are never touched; a path that leaves the folder refuses the whole archive. A copy without the mark is offered "unknown -> sha". `is_checkout` no longer runs git without a `.git` folder (on a Mac without the developer tools, git is a stub that opens an install dialog). Live against GitHub in a temp folder: check 0.5 s, apply 3.9 s, 121 files, photo and `.venv` untouched, then "Already up to date", `--doctor` core OK in the copy. Tests in `test_selfupdate` run over real HTTP against a local stand-in for GitHub; each of the five guards was switched off and its test went red. `--full` 416 tests, 1 failed (untracked `00010519.jpg`, no mask), 3 skipped.
- **[fix, found by OpenCode review, reproduced] An archive update that stopped half way could never be finished.** The files written before the failure kept their old hashes in the mark, so the next press read them as local edits and refused ("press update again" was a lie). Now a file enters the record only after `os.replace` succeeded, a failure saves the record under the OLD commit with the written files' new hashes, and the record is written via temp + `os.replace` (`_write_mark`). Pinned by `test_an_update_that_stops_half_way_can_be_finished_by_pressing_again` (a local folder blocks the third file; red before the fix). `test_selfupdate` 14/0.
- **[review, user-directed] OpenCode read the whole code base in 22 portions overnight** ("nein alles! in portionen"; packets `tmp/review/packet_*.md`, answers `out_*.md`, untracked). 24 findings; each was reproduced before anything changed, and every fix has a test that went red on the old code. Summary with the rejected ones: `tmp/review/SUMMARY.md`. **Fixed (12):**
  - `review.refit`: with any mask dropping lines the inlier overlay showed **no** inlier (Alte_Scheune 300 of 390 → 0); `_touch_keep` keeps the positions the fit saw.
  - `review.current_angles`: `clamped` ignored the yaw, so a yaw-only breach was capped without "correction hit the configured limit".
  - `review.planned_size` (the status line's "saved size") planned without save's `keep_size`/`keep_centre`: Platte_1 said 3726x2366, the save wrote 2883x1831. It is `output_size()` now.
  - GUI: Clear marks (`a() or b()`) left the horizontal lines behind a vertical one; seven controls (mask switch, mask alpha, Reset, Auto-fit, sliders, fill, clear crop) raised AttributeError before a photograph was open; a Shift+drag pen sizing whose Shift came up first kept `_pen_anchor`, which swallowed the next erase.
  - `sam2seg` child script read the photograph with `cv2.imread` and wrote the mask with `cv2.imwrite`: an umlaut in the name was "cannot read". Now `np.fromfile`/`imdecode`, `imencode().tofile`; the test runs the real child against stand-in torch/hydra/sam2 packages.
  - `warp.apply_undistorted` clipped every lookup into the image, so `pad=black`/a colour never reached remap's border (edge smear instead). `edge` unchanged.
  - CLI: two inputs with one name (`A/img.jpg B/img.jpg -o out`) wrote one file, the second replacing the first; now `-2`, `-3` and a log line (`unique_destination`). The `--horizontal` log measured the SOURCE with the output's pixel focal (`_focal_px(r, shape)`). `correction_log.write_record` wiped a strip `remember_strip` had kept.
  - `tools/install_deps.py --lama` called lama "optional ok" after torch had failed; it is skipped and listed missing now, like the in-app button.
  - **Decided (user, "Maske muss weg"):** the mask mode picked in the panel is NOT carried to the next photograph; every photo opens with the mask off, so BiRefNet never runs unasked on a walk. Behaviour unchanged, deliberate -- do not re-file it as a bug.

**2026-10-23**

- **[user-directed] Dependency installer for macOS and Windows** (Linux: by hand, user: "die Linux User sind selbst schlau genug"). `tools/install_deps.py` checks Python ≥ 3.9 and Tk ≥ 8.6 first, installs `requirements.txt`, then tkinterdnd2 and `ai-edge-litert --no-deps` (each optional, reported, never fatal), with `--lama` also torch (only if missing -- a CUDA build stays) and `simple-lama-inpainting --no-deps`, then runs `--doctor`. **macOS → `.venv`, Windows → the Python on PATH**, because the .exe/.bat start that `pythonw`. Double-click wrappers `Install (Windows).bat`, `Install (macOS).command`; launcher `Perspective Correction.command` starts from `.venv`. `.gitattributes` pins `.command` LF, `.bat` CRLF; the `.command` files carry the exec bit in git. Verified on Windows through the .bat into a scratch venv: core + both optionals ok, `--doctor` core OK, `App` opens from that venv, torch detection right. **The macOS path has never run on a Mac.** CI's first `macos-latest` run (fast suite, arm64) was green: 339/0/4.
- **[user-directed] macOS one-line install, `tools/install_mac.sh`** (`curl -fsSL .../main/tools/install_mac.sh | bash`): a browser ZIP is quarantined (Gatekeeper: "cannot check for malicious software") and Downloads/Documents/Desktop give Terminal "Operation not permitted", so it fetches into `~/Perspective-Correction` by `git clone` (only when `xcode-select -p` says git is real), else a curl tarball; re-run updates. No usable Python → asks (via `/dev/tty`, stdin is the pipe) and installs python.org's signed 3.12.10 pkg with `sudo installer`, plus its certificates. `Install (macOS).command` runs the same script with `PC_NO_FETCH=1`. Tested here in Git Bash: tarball, clone, update, Python-missing yes/no. Not run on a Mac.
- **[fix, first Mac run] The window crashed on macOS with `ValueError: Image is required`.** The three load keys (add, folder, paste) quote Segoe's icon fonts, which only Windows has; `_icon_pil` returned None and went straight into ImageTk (the palette keys already guarded it). Now `_load_key_icon` falls back to drawn twins in `_glyph_pil` ("add", "folder", "paste"). Diagnosis came from another assistant on the Mac; its patched gui.py was not used. Pinned by `test_gui.test_the_window_opens_without_the_windows_icon_fonts` (WINDIR → empty folder), red with the fallback removed: exactly the Mac's error. `test_gui` 49/0, `debug_ui` clean.
- **[fix, first Mac run] Python 3.15 has no torch (PyPI 2026-10: macOS wheels cp310-cp314), so `--lama` could not install.** `install_mac.sh` now prefers 3.12, then 3.13/3.14; with `--lama` it asks `pip index versions torch` (exit 1 when none fits -- checked with `--python-version 3.15`) and offers to install 3.12 alongside. `install_deps.py` rebuilds a `.venv` made by another Python version (read from `pyvenv.cfg`; tested by faking 3.15 there) and says why torch failed.
- **[UI, user-directed] lama missing is said, and a `deps` button installs it** (all OS). Notice "lama is not installed -- telea fills the band instead. Install it with 'deps' at the bottom right." on the first photograph, on choosing lama, and after a save whose fill note names the fallback (`ReviewSession.last_fill_note`, reset per save). `_check_fill` no longer claims a missing backend "will FAIL" (it falls back). **`deps`** (footer, beside update) lists the missing optional packages, asks, pip-installs into the running Python on a thread (back to Tk through `self.queue`, kind `call`: `after` from a thread fails without mainloop -- the test found it), torch only if absent and only if `pip index` finds one, then summarises. Logic in `src/pc/depinstall.py` (Tk-free; `tools/install_deps.py` loads its package list by path). `test_depinstall` (new module, real pip offline into a throwaway venv with a hand-built wheel) 5/0; `test_gui` 51/0; each new test red by sabotage.
- **[fix, user-caught] Auto shrank the motif; the save now keeps its centre at 1:1** ("sollte eigentlich zentral erhalten werden"). On `screenshot_20261023_225249.jpg` (no EXIF, f 15.6 mm by tilt prior, pitch 16 deg) the pitch stretched the top, the canvas grew tall and `_fit_inside` scaled back to the source's long edge: house centre sampled 0.84 x 0.87 (0.73 of its pixels), bottom 0.54, treetops 2.15. `_keep_near_edge` guards this only under a yaw. New `warp.keep_centre` (+ `interest_point`: length-weighted median of the segment midpoints, `magnification_at`) scales the save plan up until that point is >= 1:1, max `NEAR_EDGE_MAX_SCALE`; called in `ReviewSession.save` and `output_size` alike, a pure scale so preview crop fractions hold. House: 969x1024 -> 1196x1264, centre 1.03 x 1.08, bottom 0.93 x 0.88. Pinned by `test_warp.test_a_pitch_correction_keeps_the_motif_at_full_resolution` (red with keep_centre a no-op). Two `test_review` save tests re-derived save's plan by hand and now assert against `output_size()` instead. `--full` 407/1/3 (the untracked `00010519.jpg`), `debug_ui` clean.
- **[UI, user-caught] Footer help was off screen.** Tooltips always opened below their widget, so batch/deps/update (bottom edge) showed theirs under the screen. `layout.tooltip_xy`: below, else above, clamped to the screen; `_attach_tooltip` measures the box before placing it. `update` got help too; deps names each package and what it is for. Pinned by `test_layout.test_help_stays_on_screen_for_a_button_in_the_bottom_right_corner` and `test_gui.test_the_footer_buttons_show_their_help_on_screen` (window flush bottom right; red with always-below: y 1183 on a 1152 screen).
- **[UI, user-directed] The program opens maximized, not fullscreen** ("viele lieben das nicht"): `run()` passes `start_maximized=True`; F11 still toggles fullscreen. Checked: state zoomed, `-fullscreen` 0.
- **[UI, user-directed] Microsoft fonts replaced by Google fonts shipped in `fonts/`** ("lass uns Microsoft-Schriften durch Google Fonts ersetzen"). `tools/build_fonts.py` downloads, pins one instance and subsets: Inter Regular 96 KB, JetBrains Mono Regular 59 KB, Material Symbols (add E145 / folder E2C7 / content_paste E14F, wght 300) 2 KB, Noto Emoji (dinosaur, wght 700) 3 KB; licences beside them, credited in `CREDITS`. `_register_font_file` registers per process: Windows AddFontResourceEx (verified: JetBrains Mono, not installed here, appears in Tk's families), macOS CoreText process scope and Linux fontconfig app fonts (**written to the APIs, untested**). `_icon_pil` reads only fonts/ now (no WINDIR); the drawn twins stay as fallback. Themes: Phosphor/Amiga Inter + JetBrains Mono (were Segoe UI/Verdana + Consolas); fallback lists carry no Microsoft names. Also: `depinstall.missing()` now lists torch when lama is installed without it (the first Mac on 3.15). Pinned by `test_the_icons_come_from_the_shipped_fonts_and_survive_without_them`, `test_the_shipped_text_fonts_are_what_the_window_uses` (red with registration off), the torch case in `test_depinstall`. `--full` 410/1/3, `debug_ui` clean.
- **[perf, user-directed] lama on Apple's GPU, and no live lama preview on a CPU.** Measured here, same fill: CPU 7.3 s at 1000x750 / 19.8 s at 2000x1500, CUDA 2.5 / 2.6 s. (1) `inpaint._pick_device`: CUDA, else MPS, else CPU (simple-lama alone knows CUDA/CPU, so a Mac always got the CPU); MPS is probed with a 64 px fill at load (LaMa's FFT convolutions may not run there) and falls back to the CPU at load or on the first real photograph, reason in `inpaint.LAMA_NOTE`. **MPS itself is untested** -- no Mac here. (2) `ReviewPanel._live_cpu_check`: after the first lama fill, if `inpaint.lama_device()` is `cpu`, untick live once per run and say so ("saves still use lama"); decided then, not at start, because asking means importing torch. Real lama through the new loader: auto -> cuda 4.0 s, forced cpu 6.1 s, band filled both times. Pinned by `test_inpaint.test_lama_prefers_cuda_then_the_apple_gpu_then_the_cpu`, `..._an_apple_gpu_that_fails_falls_back_to_the_cpu_at_load_and_mid_fill`, `test_gui.test_lama_on_the_cpu_switches_the_live_preview_off_once_and_says_so`; all red by sabotage. `--full` 413/1/3, `debug_ui` clean.

- **[fix, user-directed] Horizontal auto gives squares, also on steep facades** ("nein ich will natürlich Quadrate", "auch auf stark verkürzten"). HA no longer applies the √cos compression of 2026-09-26: `warp.metric_x_scale` / `rectify_metric` step along `K⁻¹vp_h` and `K⁻¹vp_v` through H and set the x factor so a metre is a metre both ways. **This reverses the user's own 09-26 calibration (altbau 70 %, csm 85 %) on the user's word.** The README's "compressed back to plausible proportions" is rewritten.
- **[fix, user-directed] A folding warp is framed, not refused** ("warp müssen kontrolliert, nicht verworfen werden"; "rechtzeitig croppen, darf nicht abschmieren"). `ReviewSession.active_quad()` = the hand quad, else `ha_fold_quad()`: when `W.plan` refuses (the facade's vanishing line crosses the frame) **or, with a strip, frames a canvas the facade pokes out of by more than `FOLD_FRAME_TOL` 5 %**, HA builds a quad from lines through the two vanishing points (x from the strip, else the 2-98 % of the horizontals; y from each vertical end slid along its line to vp_h onto the strip's middle) and goes the planar way (`planar.canvas_for`). Save, `output_size`, preview and status all follow it. Guards: a vanishing point inside the span keeps the side with more lines; the quad stops where the wall is foreshortened `FOLD_MAX_FORESHORTEN` = 3x its far end; only a convex quad counts. Mariánské Hory without a strip: 1969x11664 → 2715x1093, pink block frontal.
- **[fix] Inside a facade strip the horizontal window widens** to 90 − vertical window − 4 = 54° (`_strip_horizontals`): the detector's 32° dropped a steep facade's horizontals (2 found against 33-37 verticals on rendered cubes). With nothing in the strip, the detector's own pool stays (`test_pipeline` caught the wide pool leaking into the fallback).
- **Measured on rendered cube streets (`synth.rubik_street`), strip on the face**: cells 0.997-1.010, row/column tilt ≤ 0.18° on most faces, folding ones included; close_and_steep (16 mm, 22° up) right and front face 1.003 / 1.000, now framed (before: canvas of sky and street beside the face). Still failing: faces with too few horizontals (small, far, sticker edges < min_len) and strips overlapping another cube; far_tele cube0_right 0.785 (focal 42.7 vs 50 mm). Real photos, `drive_review` over `Horizontal/` (no strips): mean |vertical lean| 0.10°, |horizontal slope| 0.08° (ledger 10-21: 0.12° / 0.10°).
- **[UI] PC Rectangle / HA-fold canvas centred on the quad** ("roi centered"): the narrower side's room on both sides, at least the 20 % crop box.
- **[UI] The preview fills only the crop + 5 %** (`PREVIEW_FILL_CROP_MARGIN`, "zu krasser telea Anteil"), on that window alone so the dark band beside it is not read as picture: on the close steep cube the invented area outside the crop falls from 100 % to 25 %, inside unchanged (0.83), 136 → 85 ms. Saves are unchanged (they crop first, then fill).
- **[UI] Tool palette split at mid-height** (`layout.PALETTE_HANDLE_GAP` 32 px): a facade-strip ruler near the left edge had its grab handle under the palette.
- **[UI, user] "strike slanted" is gone from the palette** ("geht ja sowieso"); `ReviewSession.disable_lines_by_angle` stays.
- **README** carries `docs/horizontal-auto.jpg` (Mariánské Hory, strip 0.30-0.70, yaw 59.6°, credited) and `docs/synthetic-squares.jpg` (cube street, HA on two faces with the true grid; cells 1.000 / 1.002).
- Pinned: `test_planar.test_horizontal_auto_turns_a_chosen_facade_into_squares_even_when_it_folds`, `..._a_wall_running_into_its_vanishing_point_is_cropped_in_time_not_refused`, `..._a_close_steep_facade_is_framed_by_horizontal_auto_not_cut_off`, the centring assertion in `..._canvas_keeps_room...`, `test_gui.test_the_strip_handle_is_never_under_the_tool_palette`; each red by sabotage.
- **[UI, user-directed] Auto crop frames the centre of interest** ("center of interest per LSD-Dichte ... Vorteil Himmel fliegt raus"). With a yaw and no strip, `ReviewSession.auto_crop` cuts the crop to `warp.interest_box`: the 5-95 % quantile box of the warped segments, weighted by their length in the PHOTOGRAPH (mapped length explodes near a VP), +10 %. With a fill on it replaces the fill-free rect inside the quad's bbox; without one it intersects it. Switch: `ReviewSession.CROP_TO_INTEREST`.
  - Only segments that come out straight (`INTEREST_STRAIGHT_DEG` 3) count; slanted ones at least `INTEREST_ROOF_LEN` 0.06 of the extent long may only raise the top (gables, pediments). User on 00010519: "Bild sitzt gut, Auto-Crop noch nicht" -- the stair railing had pulled the box right and down; now building + margin (crop x 0.03-0.83, y 0.30-0.86). A grid-density variant (connected component over a 48-cell map) was tried and cut the plain right half of that facade off; dropped.
  - Measured old -> COI (crop share of the canvas): far_tele 0.92 -> 0.28 (sky 0.47 -> 0.32, cubes kept 0.99), row_at_an_angle 0.89 -> 0.27, redbarn 1.00 -> 0.46, grimm 0.69 -> 0.37, csm 0.85 -> 0.45 (lawn out), Platte_1 0.40 -> 0.25; weighted lines in the crop 0.87-1.00 on every real photo. Fold path and strip: unchanged (not applied).
  - **Found on the way, fixed:** `W.plan(line_segs=...)` reads segments in the plan's own pixels and got analysis-image pixels, so preview and full-size canvas were framed differently whenever the analysis image was a third size (2400-px scenes: the box landed in the ground). `ReviewSession._plan_segs(width)` scales them; `pipeline.py` divides by `scale`.
  - Pinned: `test_planar.test_the_auto_crop_frames_where_the_lines_are_and_leaves_the_sky_out` (red with the switch off), `..._the_plan_gets_its_line_segments_in_its_own_pixels` (red without the scaling: 840x1200 preview vs 3885x2917), `test_warp.test_the_interest_box_ignores_slanted_foreground_but_keeps_a_gable` (red at 45 deg and with the roof rule off).
- **Suite, 2026-10-23 late: `--full` 398 tests, 1 failed, 3 skipped, 127.6 s**; the failure is `tests/assets/00010519.jpg` (added 20:23, untracked, no mask). `debug_ui` clean.

- **[docs, user-directed] README carries one picture, at the top: `docs/screenshot.jpg`** (the review window in Horizontal auto, 1920x1080 JPEG q88, from the user's 2559x1439 `Screenshot_PC.png`). `docs/hero-before-after.jpg`, `docs/horizontal-auto.jpg`, `docs/ui.png` and their attribution lines are removed from git; copies in `Trashcan/readme_images_2026-10-23/`. `docs/outpaint-band-example.jpg` stays (`workflows/README.md` names it).

- **[fix, user-caught] PC Rectangle came out squashed; now true proportions, then HA's compression.** `planar.target_size` used the apparent edge lengths, which keep the foreshortening. Measured on synthetic 3D box faces (random pose, focal 0.35-2x, 1.5 px click noise): a 2:1 facade at 40° came out 1.28 (-36 %), at 55° 1.02; over 3263 faces mean error 84 %, p90 194 %.
  - Now `quad_focal` (Zhang & He 2004, from the four corners) and `true_aspect`; the focal is the geometric mean of the quad's and the estimator's (mean/p90 error: quad alone 9.1/21.6 %, estimator ±30 % alone 8.7/19.0, blend **6.9/15.4**). Outside `FOCAL_RANGE` (0.25-6x long edge) or with parallel sides the estimator's focal alone.
  - Then `warp.yaw_x_scale(facade_angle)`, the user-calibrated √cos of horizontal auto, so one facade has one width whichever tool straightened it (**off again since the evening -- squares must stay squares, see below**) (Fable's review, `REPORT_rectangle_vs_HA_2026-10-23.md`, which also found the session never passed center/focal at all). `foreshorten=False` gives the bare metric aspect.
  - Never fewer pixels: the aspect is reached by growing one side.
  - Pinned by `test_planar.test_the_rectangle_comes_out_in_its_true_proportions` (red with the aspect step removed), `..._is_compressed_exactly_like_horizontal_auto` (2:1 at 30/45/60° = 2·√cos ±2 %), `..._never_shrinks_the_canvas`.
- **[UI, user-directed] PC Rectangle crops to 20 % around the quad** (`planar.CROP_MARGIN`, each side, of the rectified width/height; "rechteck modus crop +20%. lieber manuell wenn nötig"). **Superseded the same evening, see "canvas keeps room" below**: the canvas was quad + 20 % itself, so nothing could be dragged back. The margin outside the photograph is filled (preview and save). Measured on Platte_1: TL corner at 14 % of the canvas (20 % of the quad), saved file 1048x463 = the 0.1-0.9 crop of 1310x579, darkest 1 % = 36 (filled, not black).
- **[UI, user-directed] The loupe follows the pointer** (`_loupe_follow`: `LOUPE_OFFSET` below-right, flipped at the panel edge). It was parked in the 8 px gutter, so the 362 px glass covered the right 176 px of the before picture, then jumped onto the after picture. It also comes back when a placed rectangle corner is grabbed (it vanished with the fourth click). The glass forwards `<Motion>`, or a fast move onto it froze it.
- **[perf, user-caught] Dragging a mark end or a rectangle corner no longer lags.** Before: every motion refit (30 ms) and the debounced full redraw rendered the after picture with live lama (900 ms with HA on Platte_1). Now the before canvas redraws its marks/quad at once, and the after picture refits and re-renders at most every `layout.DRAG_PREVIEW_MS` (250 at first; **1000 since the evening**, user: "lupe muss smooth laufen! max 1/s" -- each re-render stalls the glass ~200 ms; measured 180 motions over 3 s: median 21 ms, 4 frames > 50 ms), without fill, at half size and without the status line; the full preview comes on release. Platte_1 + HA, 60 motions: 4 redraws, worst motion 304 → 197 ms. Profile of the remaining tick (231 ms): refit 97, render 58, `_show_after` 32, status line 20.
- **[perf, user-directed] Quick fill first, slow fill after** ("erst telea, dann relaxed lama hinterher"). `render_after(fill="defer")` shows the telea stand-in and leaves `deferred_fill = (raw, hole, settings, finish)`; the GUI runs it `DEFERRED_FILL_MS = 400` after the last render on a worker thread and lands it only if `_fill_gen` still matches. Saves wait for it (`_fill_wait`), so the model never runs twice at once. Measured: first redraw 198 ms, lama arrives after ~6 s (first model load). Note: on Platte_1 with HA the band is 47 % of the frame, above `fill_max_share` 40 %, so neither telea nor lama fills it -- not a bug.
- **[test] The three behaviours above are pinned in `test_gui`**: `test_the_loupe_follows_the_pointer_and_comes_back_for_a_corner`, `test_dragging_a_mark_end_refits_and_redraws_at_a_throttled_rate`, `test_a_slow_fill_follows_the_quick_one_and_a_stale_one_is_dropped` (fake lama, so CI needs no model). Each verified red by sabotage: loupe parked (offset 712,115), refit+redraw per motion (40/40 against 18 allowed), stale generation accepted, fill never scheduled. Written to spec by OpenCode on BeeLlama.
- **[macOS, untested on a Mac] Right button and Option**: Tk on macOS numbers the right button 2 and reports Option as state 0x0010, so every right-click gesture (erase, clear the rectangle, pen size, Grounding DINO prompt) and the Alt-damped loupe were dead there. `gui.RB` / `gui.ALT_MASK` (`_MAC = sys.platform == "darwin"`), plus Cmd+V. Windows unchanged (3 / 0x0008). CI's fast matrix gained `macos-latest`; its first run is the first time this code has met a Mac. README: made for 1920x1080, fits down to 1366x768 (`layout.MIN_WINDOW`, unchanged; user: no small-screen pass).
- **Suite, 2026-10-23 end: `--full` 384 tests, 1 failed, 3 skipped, 119.8 s.** The failure is `tests/assets/39079116-muenzstrasse-fassadenwerbung_corr.jpg` (a Save output from 18:17, gitignored, no mask) -- local only; moved to `Trashcan/stray_corr_2026-10-23/` on the user's word, `test_assets` then 11/0/2. `debug_ui`: FAILURES none, log clean.
- **[user-directed] `Perspective Correction.exe`: a starter with the logo, no console** ("bat nervt ... cmd window auch"). `tools/make_launcher.py` writes `tools/launcher.ico` from `gui._beholder_pil` (16-256 px) and compiles a 25 KB C# winexe with the `csc.exe` that ships with .NET Framework 4: it runs `pythonw perspective_correction.py --gui <dropped files>` from its own folder (falls back to `pyw`, else a message box). A starter, not a bundle, so it follows every `git pull`. Both outputs gitignored; the .bat stays. Verified: launched, `pythonw.exe "...
ectify.py" --gui` came up, closed again by WM_CLOSE. User: "sieht top aus".
- **[UI, user-directed] Lines OR rectangle, never both** ("niemals gleichzeitig im display"). `ReviewSession.set_facade_mode("lines"|"rect")` moves the other side's lines or quad aside and restores it on the way back; the line and rectangle tools call it and post a notice saying what was put away and how to get it back. Pinned by `test_planar.test_lines_and_rectangle_are_either_or_and_each_comes_back` and `test_gui.test_lines_and_rectangle_never_share_the_picture` (red with the switch removed). Built by OpenCode (P8/P9), checked by the architect.
- **[fix, user-caught] The saved rectangle was cut by the line mode's crop** (Münzstraße: preview whole, file kept 24 %). The crop is fractions of one mode's canvas, and the mode switch carried it across. Now the crop and `crop_is_auto` are swapped with the mode too (`_stashed_crop`). Pinned by `test_planar.test_the_crop_goes_with_its_mode_and_the_rectangle_starts_at_quad_plus_20` (red without the swap). Preview vs. saved file on Münzstraße: aspect 1.575 / 1.570, mean difference 5.6 grey levels.
- **[UI, user-directed] The rectangle's canvas keeps room around the quad** ("maxime dass keine pixel verloren gehen, oder nur moderat"). Measured on Münzstraße: quad + 20 % kept only **14-33 % of the photograph**; keeping all of it would need 7-21x the canvas, nearly all fill. `planar.canvas_for`: the canvas reaches `KEEP_MARGIN = 0.5` of the quad beyond it on each side, cut back to where the warped photograph ends; the default crop is the old quad + 20 % box (automatic, follows a corner drag, a hand crop stands), so the crop can be dragged OUT. `auto_crop` on the rectified view gives that box; the loss gate exempts it. `save_planar` crops first, then fills only the written part. The rectangle no longer switches auto crop off. Pinned by `test_planar.test_the_canvas_keeps_room_around_the_quad_but_never_past_the_photograph` (red with the outline clip removed) and `..._the_saved_rectangle_is_the_cropped_preview`.
- **[fix, user-directed] PC Rectangle: squares come out square** ("es müssen nach Entzerrung wieder Quadrate rauskommen"). `planar.target_size(foreshorten=False)` is the default now: the HA √cos compression adopted from Fable's review in the morning is off for the rectangle (still `foreshorten=True`, still HA's). Measured on rendered Rubik cubes (`synth.rubik_cube`, 4x4 squares a face, no EXIF): cells 1.000 with the true focal, 0.93-1.06 with the estimator's, 1.19 worst (18 mm lens, estimator at its 28 mm default); with the compression they were 0.66-0.95. Blend vs. the quad's own focal re-measured on 40 random cubes with 1.5 px click noise: blend 7.8 % mean / 19-23 % p90, quad alone 9.0-10.2 / 23-27 -- even where the estimator's focal is only a prior, so the blend stays. Pinned by `test_planar.test_squares_come_out_square_on_a_rendered_rubik_cube` (red with the compression back on). **Then at building scale** (user: "20x20 m, du 1,6 hoch, mehrere Würfel, gedreht"): `synth.rubik_street`, 20 m cubes with 5 m squares, eye height 1.6 m, camera tilted up, several cubes turned against each other. Over 6 scenes / 20 framed faces (16-50 mm, pitch 6-22°): cell aspect **0.98-1.005 on 18 faces**, 0.93/0.95 on two faces of the 50 mm scene (estimator 42.7 mm). Pinned by `..._on_building_sized_cubes_from_eye_height` (within 3 %). **`tools/render_cubes.py`** writes the pictures to look at: `tests/assets/Synthetic/cubes/` (gitignored), source + rectified face with the true grid in green, `summary.csv`.
- **[superseded by the entry above] The rectangle's proportions look over-compensated on Münzstraße** ("rechte Giebelseite wirkt breiter"). No EXIF; the aspect then rests on the estimator's focal (2260 px ≈ 34 mm on 2400 px), because the quad's own focal is undefined (its verticals are parallel). Blue house, true/compressed aspect over the focal: 16 mm 1.61/1.52, 24 mm 1.79/1.61, 34 mm ≈ 2.1/1.73, 50 mm 2.65/1.95. No ground truth on the photo; not changed.
- **[repo] `rectify.py` is `perspective_correction.py`** (git mv; every reference in workflows, .bat, README, docs, `cli.py` prog name, tools, tests updated; the launcher rebuilt).
- **OpenCode headless hangs on a spec passed inline**: 590 s, 0 bytes. The same spec from a file (`tmp/packet_*.md`, prompt `'Read the file ... and carry out exactly what it says.'`) ran in 14 s.

**2026-10-21**

- **[repo, user-directed] Git carries only the test photos the suite names**: `79cb33878…jpg`, `1070912stkzg.jpg`, `Alte_Scheune.jpg` (with masks), `Horizontal/Platte_1.jpg`, plus `roi.json` and the READMEs. Untracked, kept on disk, re-ignored in `.gitignore`: 39079116, Mariánské Hory, Antibes, the avif, cedric-vilim, hospital, lochfassade, their masks, `sam2_masks/`, and the unlinked `docs/before-after.jpg`/`detection.jpg`. Two `test_gui` tests used lochfassade and now use 1070912stkzg. Checked on a `git archive HEAD` export (what CI sees): `--full` 377 tests, 0 failed, 4 skipped. **CI's `test_assets` now samples 3 photos, not 5, and the `_ALWAYS` hard cases are local-only**; the full pool runs here.

- **[docs/UI, user-directed] README short, Horizontal auto featured, detail in hover help.** README 272 → ~95 lines: both modes up front, an HA section with a before/after (`docs/horizontal-auto.jpg`, Mariánské Hory, CC BY-SA 2.5, via `drive_review --strip 0.30 0.70`: yaw 59.4°, after lean -0.06°/slope -0.11°; without the strip the save refuses at yaw 60, folds) aimed at 3D textures / street-view grabs. The tool table, overlay prose, option table and log section are gone; their content lives in the tooltips (`--help` for the CLI). The weights table moved to `docs/masking.md`. New/rewritten tooltips: the two mode buttons, horizontal auto (the old one called it "a special case, not a default"), Check lines.
- **[UI, user] Hover help waits `layout.TOOLTIP_DELAY_MS = 1000` ms**; leaving or clicking before then cancels it. Pinned by `test_hover_help_waits_a_second_before_it_appears`, red with the delay at 0. `test_gui` 43/0, `debug_ui` clean.

- **[UI, user-directed] The window adapts to smaller screens ("bisschen flex").** The floor was a fixed `minsize(1920, 1080)`, larger than a 1366x768 laptop, a 125 % scaled screen or a CI runner, so the window hung off the display. Now it is `layout.min_window(screen)`: `MIN_WINDOW = (1366, 768)`, never larger than the screen.
  - Two changes make 1366x768 fit; measured, no row asks more than it gets:
    - every Scale requests `length=layout.SLIDER_MIN`, which went 80 → 60. The ttk default of 100 px was a hidden floor;
    - `Compact.TButton` gets `width=-6`, i.e. at least 6 characters, else as wide as the text. The ttk default of ~11 characters made every key 89 px.
  - Rows went from 765/782 to 575/600 px, inside a 671 px field. Looked at in a 1366x768 screenshot.
  - Pinned by `test_layout.test_the_window_floor_never_exceeds_the_screen`, red when sabotaged.
  - The two GUI tests that need a taller window than a runner's display holds now skip only when the window could not grow at all (`_skip_if_window_cannot_grow`). They still run here: the screen is 2048x1152.
  - `run_tests.py` also catches `unittest.SkipTest`, which `test_gui` imports.
  - `--full`: 376 tests, 1 failed (the untracked asset), 3 skipped. `debug_ui` clean.
  - CI then failed `test_no_tools_field_row_asks_for_more_width_than_it_gets`: the runner's screen is 1024x768 and the window now honours it. The test now measures at `layout.MIN_WINDOW` (1366x768, stricter than the old 1920x1080) and skips only when the display cannot hold that floor.
  - The test also had no teeth for the slider row: a row's own container grows with it and the clip happens a level up. Measured: `SLIDER_MIN = 110` asked 725 px of 671 and stayed green. It now also walks every mapped container in the panel; with the sabotage it goes red, without it green.
  - **CI fully green for the first time** (`79cb2df`, run 39575603466): ubuntu 3.9/3.12, windows 3.9/3.12 and `full-windows`. Open B.7 (first `full-windows` run) and B.8 (Linux failing) are answered by this.
  - **Test and tool windows stay out of the taskbar** (user): `App(in_taskbar=False)` sets Tk's `-toolwindow`, used by `test_gui._app`, `debug_ui` and `drive_review`. Checked: `WS_EX_TOOLWINDOW` set, size unchanged, `test_gui` 42/0, `debug_ui` clean.
- **[user-directed] The public repo carries only what users and developers need.** Untracked but kept on disk, now in `.gitignore`: `QWEN.md`, `knowledge.md`, `powerdebug-report.md`, `.qwen/`, `.claude/`, `skills/`, `analysis/`, the internal plans in `docs/` (overhaul, ha-layout, layout spec/plan, qwen-knowledge, worker-*), and the worker/one-off tools (`worker_agent`, `worker_bench`, `powerdebug`, `verify_findings`, `probe_gestures`, `cap_sweep`, `measure_cost`, `shoot`). Root scratch (layout mockups, screenshots, `.playwright-mcp/`, the C64 font) is ignored. CLAUDE.md stays tracked (user). The files below that point at these paths still work locally. They are still in the git history.
- **CI after `766a0ef`:** ubuntu 3.9/3.12 and windows 3.9/3.12 are green for the first time. `full-windows` has 2 failures, both the runner's display: the window cannot grow past 1044 px (`test_the_window_is_the_cross_and_nothing_else`) and `test_the_preview_gives_up_retrying...`. This is the experiment Open B.7 predicted. Not fixed.
- **[fix] CI was red on every job, Windows included; two causes, read from the real Actions logs** (job logs fetched through the API with the stored git credential):
  - `run_tests.py` set `_PytestSkip = ()` when pytest is missing, and `except (_Skip, ())` is a TypeError. So the first failing test crashed the whole run with no summary. The fallback is now `_Skip`.
  - The failing test was `test_diagnostics_go_into_the_log_file_too`. Since lama became the default fill, the CLI refused its own default (`--fill lama needs simple-lama-inpainting`, exit 2) on any install from `requirements.txt` alone, which is what CI and a new user have. That broke `deps.py`'s contract that a default run works on the core install. Now `deps.preflight(settings, fill_asked=False)` counts lama only when `--fill` was given. Left at the default, the CLI steps down to telea and prints a note saying so and how to install lama. An explicit `--fill lama` still exits 2. Pinned by `test_cli.test_the_default_fill_steps_down_to_telea_when_lama_is_missing`, red with the step-down removed.
  - `test_selfupdate` is in `MODULES` now (10/0).
  - First CI run after the fix (`2189abb`): both Windows fast jobs went green. Ubuntu failed 3 tests in `test_correction_log`, a test-only bug: the fake `src` was `D:\photos\IMG_0421.jpg`, and Linux `basename` does not split at `\`. Now `os.path.join`. `full-windows` failed because `test_gui` and `test_assets` import pytest, which that job did not install; it now installs it.
  - Clean venv with only `requirements.txt`, the fast run: 322 tests, 1 failed, 4 skipped. The one failure is the untracked `3zb3...jpg`, which is not in the repo and so not in CI. Local `--full`: 375 tests, same 1 failure, 3 skipped. Linux itself is still untested here (no WSL or Docker). The next CI run is the test.
- **[user-directed] Detector choice is `lsd` | `mlsd` only** (GUI and `--detector`). `hybrid`/`deep-hybrid` were plain LSD under another name and are deleted with `gate_by`. `fld` is gone from the choice because it needs `cv2.ximgproc`, which is not installed. `union` is gone too. Measured with `tools/benchmark_detectors.py tests/assets` (8 photos x 6 rotations): lsd 0.38/0.71/1.69 (mean/p90/worst), union 0.46/0.92/1.38 at 4x the time, mlsd 1.08/1.88/5.37. That benchmark measures round-trip consistency, not M-LSD's clutter filtering, which is why mlsd stays. A remembered detector outside the list opens as lsd. `union`/`fld`/`deeplsd` stay callable in `lines.py` for the benchmark tool. `test_detectors`+`test_cli`+`test_gui` 50/0/1, `debug_ui` clean.
- **[fix, user-caught] Horizontal auto warps without a facade strip.** The warp (`_rectify_active`) required a strip or mask, and the strip is off by default (`86e62f7`). So HA usually fell back to the camera rotation, which bent the already-plumb verticals of wohnheim by up to 4.7°. Through `tools/drive_review.py`, all 12 Horizontal photos, before → after:
  - mean |vertical lean| 0.36° → 0.12°; mean |horizontal slope| 0.45° → 0.10°.
  - wohnheim verticals 1.18° → 0.04°, horizontals 1.14° → 0.06°.
  - Slightly worse: bitterfeld verticals 0.00° → 0.23°, grimm 0.11° → 0.16°.
  - Same gate fix: a drawn **horizontal** now also turns the warp off. The status had said "HORIZONTAL AUTO (warp)" while the drawn lines were in charge.
  - Pinned by `test_horizontal_auto_warps_without_a_facade_strip`. Only its gate assertion goes red on revert: on the synthetic scene the rotation's median lean is fine; wohnheim is untracked, so it can't be the test.
- **[UI, user-directed] Opening replaces, never queues.** A single file opens directly. If the photo on screen was changed (`ReviewSession.state_key()` differs from its snapshot at load or save), a dialog asks first: Yes saves, No discards, Cancel stays. A folder, or several files at once, starts a walk that replaces the old one. Also fixed: opening a path already in the list was silently ignored. Pinned by `test_opening_a_file_replaces_the_photo_and_asks_only_when_it_changed`.
- **[UI, user-directed] Pictures centred on both axes; tool palette centred on the left edge.** The before title dodges the palette only where they share a row.
- **Environment:** installed `piexif` (a hard dependency in `requirements.txt` that was missing; 3 tests) and `simple-lama-inpainting --no-deps`. The default fill is lama, and without the package every Save silently fell back to TELEA and preflight refused the defaults (3 tests). Pillow 12.3 / numpy 2.5.3 / cv2 5.0 unchanged. `test_layout` now asserts the 2026-10-20 edge directives (bottom 10, sides 8) instead of the retired flat 20.
- **`--full`: 364 tests, 1 failed, 4 skipped.** The failure is the known untracked `3zb3nbpvnp9njrb76wymv5m8pp.jpg` without a cached mask (see 2026-09-26).
- **[superseded the same day -- FIXED, see the next entry]** in **Auto** mode wohnheim gets pitch +7.38° (conf 0.34, "would skip") although its verticals are plumb, so the after preview splays them.
  - **Cause:** the (roll, pitch, f) Nelder-Mead refinement in `model.estimate`. Before it the vertical VP gives pitch **0.90°**. The horizontal-VP term in `_cost_terms` then pulls it to 7.38°, following clusters off the sloping street (horizon support 0.09). The verticals' own cost goes up **7.8x** (median residual 0.53° → 1.47°).
  - **Three guards tried and measured over the whole pool round trip (`PC_TEST_ASSETS=0`); none separates the cases:**
    - drop the horizontals when horizon support < 0.3: hospital goes 0.61° → 1.01°;
    - when they raise the vertical cost > 5x: Platte_1 goes 0.29° → 3.16°. Synthetic round trips reach growth **23** with the horizontals *right*, because they fix the f : tilt ambiguity;
    - growth > 5 *and* start pitch < 2°: Mariánské Hory goes 0.54° → 1.39°.
  - Reverted; `model.py` is unchanged. Wohnheim's growth (7.8) sits inside the range of legitimate pulls, so the signal that tells a street from a facade is not in these two numbers. Probably it is in *which* segments formed the horizontal cluster (ground vs. facade, i.e. the mask or a below-horizon test). Measure that before building anything.
  - Mitigated already: **HA** uses the lines' own VP (`_data_vertical_vp`) and is right on wohnheim, and Auto flags it "would skip".
- **[fix, user-caught] Auto no longer reads pitch off the horizon's height.** The (roll, pitch, f) refinement in `model._cost_terms` had a horizontal-VP term (b · u = 0, weight 0.5). It pins pitch to where the horizon sits, which holds only while the principal point is the frame centre. Shifted and cropped architectural photos break that assumption.
  - wohnheim: the dominant horizontal cluster is the **facade's own windows** (not the street, as guessed overnight). Its VP sits at y=490 of 643 while the verticals are plumb: a shifted frame. The term invented pitch +7.38°.
  - The night's guards failed for a reason: the round trip measures **consistency** between a base and a warped copy, and a switch that fires on one and not the other is punished whatever it does. Measured instead as a smooth change, removing the term outright:
    - Whole-pool round trip (`PC_TEST_ASSETS=0`): mean 0.679° → **0.513°**, worst 4.43° → **2.94°**.
    - Big gains: 39079116 2.25° → 0.40°, hospital 0.79° → 0.29°, antibes 4.43° → 2.94°.
    - Slightly worse, all still small: ziegrlburtz 0.28° → 0.57°, grimm 0.20° → 0.41°, csm 0.23° → 0.38°.
    - Softening it (Cauchy scale 6°) scored mean 0.455° but left wohnheim at 2.25°.
  - Auto pitch: wohnheim 7.38° → 0.24°, bitterfeld 4.93° → 0.83° (both plumb).
  - The horizontal VPs still drive yaw, focal and confidence; only the refinement ignores them.
  - Pinned by `test_estimation.test_a_shifted_frame_with_plumb_verticals_is_left_level` (level synthetic scene with the bottom cut away, yaw 0/20/35): old code 0.81°, new ≤ 0.06°, bound 0.3°. Verified red on the old code.
  - `test_a_hand_placed_facade_strip_stands_the_review_gate_down_but_not_the_batch` had its batch half held by luck (Alte_Scheune 0.39 vs gate 0.40). With the strip it now scores ≥ 0.40, so the batch half also sets `min_confidence = 0.95`.
- **[fix, user-caught] The after picture is centred in the LIVE canvas** ("ha image target not on axis"). `_show_after` centred in the locked fit box (`_img_box`), so after fullscreen or a resize it kept its old offset: wohnheim HA L0 R64 T23 B96, while the before pane was L32 R32. Now `_after_live` holds the live size, used for the offset only (the fit still uses the lock). Pinned by `test_both_pictures_stay_centred_when_the_canvas_grows_after_the_lock`, verified red on the old code.
- **[UI, user-directed] One frame width everywhere** (`layout.CROSS_PX = 8`): the cross between the fields, top, sides and bottom. The cross was 20 (2 × 10) and the top 0. `CROSS_GAP`/`CROSS_BORDER`/`CROSS_SIDE` are all `CROSS_PX`. Pinned in `test_layout` and `test_the_cross_breaks_vertically_with_the_pictures_on_top` (top border and cross width measured).
- **[UI, user-directed] C64 theme: three colours and the C64 face.**
  - Colours: Pepto blue `#352879`, light blue `#6C5EB5`, white. ok/warn/err are white too. Keys that share a colour differ in the last bit, because `_retint_bg` maps by value and would otherwise recolour a picture canvas as "bg" on the way out (measured: back to Minimal Black gives `c_before` `#101216`).
  - Font: `Commodore Pixelized v1.2.ttf` in the repo root, loaded per process (`_load_private_font`, `AddFontResourceEx` FR_PRIVATE). **Not committed**: its ReadMe grants no redistribution. Without it the theme falls back.
  - Theme keys `font_px` (12, one pixel size for every text; 1.5× by the user's wish) and `pad_x` (1). At 12 px the control row asked 1027 of 948 px with the default 6 px padding; with 1 it is 947 of 948. 16 px clipped labels whatever the padding.
  - Canvas texts no longer hard-code `Segoe UI`: `ui_font(size)` follows the theme. `("TkDefaultFont", n)` was a family name Tk does not have, not the named font.
  - One unexplained Tcl error in `root.configure(background=...)` on the first C64 switch of one run; not reproduced in 5 runs since.
- **[UI, user-directed] "Light" is gone; "Dark Grey" replaces it** ("light looks shitty"). The theme is not persisted, so no saved preference needed migrating.
- **[UI, user-directed] Manual mode removed.** It only showed four buttons that pressed the tool palette's own keys; a drawn line overrules the detector in every mode. The switch is Auto | Horizontal auto.
- **[UI, user-directed] Live lama preview is the default** (`live_fill_preview = True`). Cost: about 1.2 s per redraw at preview size (measured 09-26 on Platte_1); untick "live" for the 38 ms telea stand-in while dragging.
- **[UI, user-directed] Auto crop grows with a fill on: +10% wide, +30% tall (20 first, then 30: "I always have to drag it further"), centred, never past the image outline or the canvas** (`warp.AUTO_CROP_GROW_X/Y`; "motifs need more flesh"). The fill covers the growth; with fill "none" nothing grows, and the no-invented-pixel test runs with fill "none".
  - Pinned by `test_with_a_fill_on_the_auto_crop_grows_10_wide_30_tall_inside_the_outline`, checked against a computed expectation at pitch 4/9/20. It goes red without the growth.
  - The outline clamp did NOT go red on sabotage: in every tested case the canvas ends where the outline does, so the two limits coincide. It stays as the stated rule.
- **[bug] The "filled -- 100%" notice stayed up for good** once live lama became the default: the preview's fill reports steps through `_fill_step`, and nothing hid the notice after the (synchronous) render. `_redraw` now hides it unless a save is busy. Pinned by `test_the_fill_progress_notice_goes_away_once_the_preview_is_drawn`, red on the old code. The live default also makes a redraw ~1 s slower, which raced `test_phosphor_...`'s fixed pump count under load; it now waits for the condition.
- **[UI, user-directed] "Outline" switch beside "Check lines"**: the photograph's own border mapped through the correction onto the after picture (`OVERLAY["outline"]`, yellow). Each edge piece is clipped to the picture; nothing is drawn outside it or along the picture's own edge. Toggling posts a notice. Pinned by `test_the_outline_switch_draws_the_source_border_where_the_warp_put_it`.
- **[UI, user-directed] Guides are pulled from a band around the picture**, `GUIDE_BAND_PX = 8` outside and 8 inside every edge, so 16 px all round (user: finding them was "a fuck up").
  - A press in the band creates the guide and is already dragging it. A press that travels less than `GUIDE_CLICK_PX` is a click and leaves nothing.
  - Hover shows the direction cursor. Crop handles still win. A notice says how to remove a guide.
  - The old 15 px inside-only zone that added a guide on a bare click is gone; the cross pull is unchanged.
  - Pinned in `test_guides_are_pulled_off_the_black_cross`: all four sides, inside and outside, follow-the-drag, bare click, beyond the band, cursors.
- **[bug] `_on_crop_release` raised AttributeError on an empty canvas.** `_guide_drag` was only set in `load()`; it is now initialised in `__init__`.
- **[test infra] A real modal dialog hung the whole `--full` run for over an hour** ("Unsaved changes": `test_the_detail_slider_...` moved the dial, which counts as a change, then opened the next photo). `_app()` now replaces `askyesnocancel` with a function that raises, so an unexpected dialog fails its test by name. That test marks the photo clean before the reload.
- **`debug_ui` "previews same height: False" was a stale premise**, not a defect: each pane fits its own picture, and the warp has another aspect. The check now asks that each picture fill its render box on one axis, and it counts as a FAILURE when it does not (it used to print FAIL without counting).
- **Night-end suite:** `--full` 364 tests, 0 failed, 4 skipped. The whole pool, `PC_TEST_ASSETS=0 test_assets`: 11 tests, 0 failed, 2 skipped. `debug_ui`: FAILURES none, log clean.

- **[UI, user-directed] The batch is a rudiment: one footer button.** Removed from the window: the results table, the run bar (Review each / Unattended / Stop / progress / output path + Browse), and the never-shown defaults Labelframe (its vars stay, `_settings()` reads them). The control strip went from ~180 px to 88 px.
  - **Adding photographs starts the review walk** (`App._walk`). Save / Skip / Close opens the next one; photographs added during a walk join its end.
  - **The footer `batch` button** (`_batch_click`) asks once, then reads `stop i/n` while it runs and stops the run when clicked. At the end a notice gives the counts, and SKIPPED/ERROR photographs open for review. Error reasons go to `pc_errors.log`.
  - **Bug found by running it:** the worker called `_dest()`, which reads a Tk var. Off mainloop that raised, every file "failed", and the reason was swallowed. Destinations are now resolved on the Tk thread.
  - **The remembered `output` folder is no longer restored.** It pointed into the off-limits sibling checkout. Save writes `<name>_corr` next to the source; Save As… is the way elsewhere.
  - **`lbl_fill` is a `_NoticeLabel` shim.** Fill/ComfyUI verdicts now land in the picture notice, except the start-up "not checked".
  - Pinned by `test_adding_photographs_walks_them_and_the_batch_is_one_footer_button`. The two tests of the retired panel are gone. Batch end-to-end on 3 copies: 2 written, 1 skipped, and that one opened for review. `test_gui`+`test_review`+`test_warp` 123/0/1.
  - **Still to look at:** `debug_ui` prints `FAIL previews ... same height: False` (before 923x923, after 942x911) without counting it in FAILURES. Not checked whether that is pre-existing.
- **Brush pen sized like Krita**: Shift+left dragged sideways (Alt+right kept, both bound). With the brush down the Shift handlers fall through to the plain click, so Shift+click in mark mode still marks (measured). The brush-width spinbox is gone; Clear marks spans its cells. Every palette tooltip names its gestures and shortcut keys.
- **M-LSD**: `ai-edge-litert` was not installed, so choosing mlsd snapped the combobox back with the reason only in a small label. Installed (`pip install --no-deps ai-edge-litert`); mlsd and hybrid now run (lochfassade: 84 v / 3 h), and lsd ↔ mlsd switches both ways. The hint in `mlsd.py` named the wrong package (`tflite-runtime`), now fixed. A detector switch also posts a notice.
- **HA verticals follow the lines, not `model.vp`** (`review._data_vertical_vp`, length-weighted LSQ of the vertical inliers). Signed median lean: wohnheim +3.97° → −0.06°, bitterfeld +1.42° → −0.18°, redbarn −0.62° → +0.03°, antibes −2.31° → −1.49° (lens distortion, Stage 1).
- **Cross edges**: top 0, left/right `CROSS_SIDE = 8` px (the ruler strip, user), bottom `CROSS_BORDER`.

**2026-10-20**

- **[fix, user-directed] Every hand-drawn line comes out EXACTLY vertical or horizontal; 2+2 drawn lines are a rectangle (all corners 90 deg).** Measured before: a drawn vertical came out 0.00 deg, but a drawn horizontal was ignored by the rotation fit (Platte_1 27.69 -> 29.25 deg, csm 6.81 -> 6.94 deg). Now `ReviewSession._build_H` builds a 2D warp from the drawn lines first: `warp.vp_from_lines` gives each direction's vanishing point -- one drawn line = the detector's VP moved the shortest way onto it (user: "1 line, the algorithm's lines assist"), two or more = their intersection only (least squares from three) -- and `warp.rectify` sends both VPs to infinity. Horizontal fallback is the detector's horizontal VP only with horizontal auto on, else (1,0,0) (horizontals left alone). Measured through the real session path on Platte_1 / csm / redbarn, HA on and off, full and preview size: every drawn line 0.000 deg, every corner 90.000 deg. Pinned by `test_review.test_every_drawn_line_comes_out_exactly_vertical_or_horizontal` and synthetic `test_warp.test_a_drawn_rhombus_parallelogram_or_trapezoid_becomes_a_rectangle` (square, rhombus, 2 parallelograms -- both VPs at infinity --, 2 trapezoids, 40 random convex quads) + `test_one_drawn_line_is_exact_and_leaves_the_rest_to_the_detector`; both verified red by sabotage.
- **[UI/HA, user-directed] Plan `docs/ha-layout-fix-plan.md` T1-T8, built by OpenCode on BeeLlama, verified by the architect.** Fullscreen: leaving it re-applies the dark title bar (DWM dark mode = 1 read back), and a 4 s notice line (`_notify`, `layout.NOTICE_MS`) at the top centre of the after field says "fullscreen -- F11 or ESC exits" -- the plain status was wiped by the next redraw (`_draw_empty`). The mode switch IS the horizontal-auto switch, both ways (`_syncing_mode`). Auto crop follows HA and the strip (`_autocrop_follow`). Status line names the warp ("HORIZONTAL AUTO (warp) ... x-scale"). Pictures flush to the top of their fields, titles inside the picture (before title right of the tool palette). Status text out of Q4, faint overlay (`OVERLAY["status_faint"]`) in the after field; mode buttons flush to the top of Q4. Auto crop keeps headroom above the eave (`warp.eave_y`, `EAVE_MIN_LEN 0.15`, `EAVE_HEADROOM 0.10`, only with a fill on, only ever raises the top): csm 171 -> 132 px, redbarn unchanged 124. The loupe sits on the TARGET picture at the mapped point (`ReviewSession.source_to_preview`, `_loupe_place`); measured source (467,354) -> after canvas (720,272), glass centred there. HA warp unchanged (Platte_1 residual 0.48/0.59 deg). `test_gui` 38/0, `test_warp`+`test_review` 84/0/1, `debug_ui` clean. **Lesson: OpenCode hangs when its context fills** (102k on HQ, 190k on XS by T7) -- one small packet per fresh session.
- **[UI, user-directed] "Skip" replaces "Keep original"; Ctrl+V pastes.** Keep original copied the untouched photograph to the output (batch era); `_skip` advances the queue and writes nothing. Ctrl+V (`App._paste_key`) does what the paste icon does -- except while a text field has focus, which keeps its own paste -- and also takes files copied in Explorer (the clipboard then holds paths). An empty clipboard says so in the notice line, not in a modal dialog. Pinned by `test_skip_moves_on_and_writes_nothing` and `test_ctrl_v_pastes_a_picture_without_the_icon` (fake clipboard). `test_gui` 40/0, `debug_ui` clean.
- **Open, waiting on the user:** ~300 px of empty black under both pictures now that they sit at the top (width-limited, they cannot grow into it).
- **[UI, user-directed] The control strip is one always-open grid, 66 px tall.** No fold-out (`v_adjust`, `_toggle_adjust`, `_adj_open` are gone). Row 0 mode switch | fill | crop, row 1 the active mode's controls, row 2 Auto/Reset | Keep, Close, Save. Modes: Auto (roll/pitch/focal), Horizontal auto (yaw + detail), Manual (mark/rect/brush/SAM, pressing the palette keys). All three mode frames share ONE grid cell and `_show_mode` only `tkraise`s, so the strip and the pictures never change size (measured: 922x694 locked, strip 24+22+20 px in every mode). One `_strip_*` method per topic; `Compact.*` styles in `apply_theme`; row gap `CTRL_ROW_GAP_PX = 0`. Pinned by `test_the_control_strip_is_always_open_and_the_pictures_never_move`. Also fixed: the foot stripe grew to 68 px on a 3-line status (now one line), and a `review.py` snap that levelled every drawn horizontal (erasing its yaw signal) was reverted. `test_gui` 38/0, `debug_ui` clean. Still open: `--full` has 5 failures from missing `piexif` (environment, not layout).

**2026-10-19**

- **[UI, user-directed] Fonts matched to RefGrade — dim labels, one face,
  slightly smaller.** The typeface was already Inter (the reference's face);
  what was off was the *treatment*. Three changes, all in `apply_theme`:
  (1) **field labels dim** — `TLabel` foreground `text` → `dim`, and `dim`
  itself darkened `#8b929c` → `#6f7680` toward RefGrade's `#666`; values and
  the title stay bright (`Value.TLabel`, `Title.TLabel`), and `_keep_px_read`
  was moved to `Value.TLabel` so the detail dial's "max/min px" does not dim
  with the labels. (2) **numbers share the UI face** — with no `mono_font`
  override the number family now resolves to `ui` (Inter) instead of
  `_MONO_FAMILIES` (Cascadia Mono), so the whole window is one face like the
  reference; the retro themes keep their terminal face via `mono_font`.
  Tkinter cannot switch on Inter's tabular-figures feature (tnum), so a column
  of numbers does not align to the pixel the way a mono face would — accepted,
  because one face reads as one instrument. (3) **slightly smaller** — base
  font 10 → 9 pt, title 16 → 14 pt. Layout untouched (user: "layout is
  fine"). `--full`: 0 failed; the runner's exit 1 is the pre-existing
  `test_selfupdate` "not in MODULES" banner, not a failure.

- **[UI, user-directed] Native Windows title bar matches the palette.** The
  OS-drawn caption follows the OS theme, not the app, so a dark window still
  wore a light title bar. `_dark_titlebar(win)` (gui.py) pushes the active INK
  colours onto that chrome via `dwmapi.DwmSetWindowAttribute`: immersive dark
  mode (attr 20) keyed off the palette's `bg` luminance, plus `bg` for the
  border/caption (34/35) and `text` for the title text (36) — so it tracks the
  theme, dark *or* light. The hwnd is the Tk client's parent (the frame owns
  the caption). Best-effort: no-op off-Windows / older Windows / no window yet.
  Called via `after(0)` in `App.__init__` (a direct call is reset when the
  window maps in `mainloop`) and re-applied in `_maximize` and `_switch_theme`.
  Verified by screenshot: caption is black under Minimal Black.

- **[UI, user-directed] The cross broke vertically: the pictures on top take
  every extra pixel.** The 2026-09-12 "four exactly equal fields" directive is
  superseded for the rows only: the two preview fields on top now get more
  height than the loader/controls below, because a photograph judged by eye
  wants every extra pixel while the controls want only what they need.
  `CROSS_TOP_WEIGHT = 1` / `CROSS_BOT_WEIGHT = 0` in `layout.py`; the
  `ReviewPanel` rows (`gui.py:975-976`) use those weights with NO uniform
  group, so the lower row sizes to its own content and the upper row takes the
  rest. A fixed ratio (the earlier 3:2, then 4:1, both inside one uniform group
  `cross_rows`) was tried and killed: it clipped the run bar whenever the
  controls' request outgrew its share, so the split now follows the content
  instead of a constant. Columns are untouched — still one uniform group, four
  equal widths. The batch defaults section (`opt`, "defaults every photograph
  opens with") was killed from the visible layout (its vars are still built so
  `_settings()` works, but the Labelframe is never packed), and the adjustments
  panel opens collapsed (`start_open=False`), so the lower row only carries the
  per-photograph controls and the run bar. Pinned by
  `test_the_cross_breaks_vertically_with_the_pictures_on_top` (renamed from
  `test_the_cross_is_four_equal_fields`): per-row uniformity, top row at least
  double the bottom row at 1920x1080 and 2560x1440;
  `test_collapsing_the_adjustments_leaves_the_cross_where_it_was` updated to the
  content-sizing wording (collapsing the adjustments still moves no field).

**2026-09-26**

- **[feature, user-directed, asked ~20 times] Horizontal compression after
  HA: `x-scale = sqrt(cos yaw)`.** The request was always "Verkürzung auf 70%
  bei altbau" AFTER the full yaw correction. It was built wrongly first as a
  tangent-space damping of the yaw itself (`atan(k tan yaw)`), which left the
  lines slanted, and that was removed. The actual fix is `warp.build`: K·R·K⁻¹,
  then a pure x-scale about the centre, so straightness is untouched and only
  the 1/cos stretch is halved on a log scale. **Calibrated against the user's
  two numbers, not fitted to them**: altbau yaw 60.0° → 0.707 (asked 70%),
  csm_klassik yaw 44.3° → 0.846 (asked ~85%), Chateau −29.9° → 0.931. Yaw
  measured by real CLI runs, `correction_log/`. Every consumer (preview, save,
  `output_size`, auto-crop, CLI) goes through `build`, so they cannot disagree.
- **All correction damping off.** Tangent yaw damping removed (above),
  corner-view pitch damping removed, and `uncertain_pitch_damping` 0.85 → 1.0
  (still a setting and `--uncertain-damping`). User: "Dämpfungen sollen so
  weit wie möglich weg". The GUI damping slider is gone; roll/pitch/focal are
  back on rows 3-5.
- **`correction_log/<stem>.lines.json`**: raw M-LSD segments of source and
  output (fractions of w/h) on every review save and `--horizontal` CLI run,
  plus signed median lean in the record/CSV. Caveat: the whole-frame median
  cancels out on a corner view (the two facades slope opposite ways) — judge
  per facade from the segments.
- **Open, noticed on the way**: the CLI does not bound output size like the
  review save does (`keep_size=True` only in `ReviewSession.save`).
- **[fix, user-caught] No-EXIF ultra-wide: focal now found automatically.**
  The Antibes ultra-wide straightened by hand at 12 mm but not by itself: no
  EXIF, frontal facade, so one vertical VP fixes only f : tilt; the program
  sat at the 28 mm default, which needs a 50° tilt, past the 30° cap. Two
  changes in `model.py`: (A) `_horizontal_sensitivity` wobbles the horizontal
  VP's bearing by 0.5° and discards the orthogonality focal (sigma = inf)
  when that wobble leaves no estimate at all — it had claimed 37 mm at
  sigma 0.26. Only the total-loss case is acted on; a graded version moved
  Struktur (conf 0.50→0.32) and altbau (117→112 mm) for no shown gain.
  (B) `_tilt_prior_focal`: with no EXIF/manual focal, choose the point on
  the curve of equally good (f, tilt) pairs that maximises focal prior ×
  tilt prior (half-normal, `tilt_prior_deg` = 15). Antibes: 28.4 mm/30°
  capped → **14.3 mm / 27.6°**, LSD vertical lean −0.81° → +0.59°.
  Measured with `drive_review.py` against a baseline: 12 of 23 unchanged,
  including altbau, csm_klassik, Struktur, Chateau, Platte, Presserundgang
  (the user-calibrated x-compression cases). YNS7 (U-shaped block, conf
  0.15) was garbage before and after. `39079116` now reads "tilt prior"
  (conf 0.69→0.50, still admitted; its geometric focal came from mixed
  planes, the documented failure). Pinned by
  `test_the_tilt_prior_trades_an_implausible_tilt_for_a_wider_lens`.
  `diag["focal_blend"]` records prior/blend/sigma/tilt-prior per photo.
  Looked at `nandometzger/MLFocalLengths` (MIT, EfficientNet-B4, 233 MB):
  MAE 16 mm on a 256 px centre crop — cannot tell 12 from 28 mm; possible
  later as a softer default prior, not tested (download not approved).
- **[bug] Save framed differently from the preview.** Review save planned
  with `keep_size=True`, whose whole-frame branches forced the SOURCE's
  exact dimensions and padded the rest — but a correction changes the aspect,
  so on the portrait Antibes facade half the saved canvas was fill (lama
  invented a wedge of steps), and the preview / Auto crop (planned without
  `keep_size`) showed another framing, so crop fractions landed on fill.
  `warp._fit_inside` now scales the plan uniformly, long edge to the
  source's long edge ("lange Kante gleich viel Pixel") — verified identical
  framing on four photos. The review crop now maximises the SOURCE area a
  rectangle shows (`warp.source_area`), not output area: output pixels over
  a stretched sky counted as much as the building and the crop picked the
  sky. Antibes now keeps door to pediment, narrow — the inverted trapezoid
  allows no wider fill-free rectangle.
- **Open**: `test_every_asset_has_a_cached_mask_matched_by_stem` fails on
  `tests/assets/3zb3nbpvnp9njrb76wymv5m8pp.jpg`, added 2026-09-26 20:51
  (untracked, gitignored) without a cached mask.
- **`tools/drive_review.py` — remote control of the REAL window.** Opens the
  actual `App` off-screen with the user's remembered settings, loads each
  photograph into the review panel, presses horizontal auto / facade strip /
  Auto crop / Save and writes `debug_out/<stem>_corr.jpg` + `summary.csv`
  (dialogs are recorded, not shown). This is how HA results are checked now —
  not hand-rebuilt pipelines (user: "idiotische Tests"). Verified identical
  angles against the CLI on five photographs.
- **Auto crop fixed ("freaked")**: it searched the source-aspect rectangle in
  the whole warped quad and clamped afterwards — a 10%-wide sliver on a strip,
  the bottom-right quarter of csm_klassik. Now clipped to the canvas, with a
  strip to strip +20%, and free aspect once a yaw is in play (`max_free_rect`).
- **Save no longer copies the original silently when the plan fails** — it
  raises with the reason (Mariánské Hory at yaw 60: the warp folds past the
  vanishing line, quad ~500x source).
- **M-LSD reads exact verticals as −0.62°** (synthetic grid; LSD 0.001°),
  horizontals fine. The correction log therefore keeps both segment sets and
  reports LSD, length-weighted (a plain median of four segments once read a
  level facade as 13.8°). Anything else that trusts M-LSD verticals — the
  check-lines cyan pass, `--detector mlsd` — inherits that bias; not fixed.

- **[feature, user-directed] Auto-crop now respects the facade strip, bounded
  to strip + 20%.** User's request: "wenn ROI aktiv ist sollte das der auto
  crop beachten." Ledger A.4 (2026-09-20) had measured that cropping to the
  strip +20% was worth more than a whole second correction pass on its own,
  and that 20% beat a 30% guess by 8x — this generalises that measurement to
  the production `warp.plan()` auto-crop, not just the experimental two-pass
  harness. **Designed by a read-only Sonnet plan-subagent working directly
  against this checkout (not the unsloth worker, whose first attempt at this
  same task was caught citing stale session-transcript line numbers instead
  of the live file — discarded, not applied), verified and applied by the
  architect.**
  New in `src/pc/warp.py`: `STRIP_CROP_MARGIN = 0.20`, `_strip_x_range_warped`
  (maps `strip`'s original-image-space fractions through `H` — confirmed by
  measurement that `quad`/`inscribed_rect` live in warped/output space, which
  under real yaw is NOT a scaled copy of the original x-axis), `_strip_band`
  (the warped strip + margin, clipped to `quad` — same clip-to-what-exists
  rule `_whole_frame`'s own facade-box margin already uses), `_clip_quad_to_x_band`
  (Sutherland-Hodgman, stays convex), and `_strip_bounded_rect` (uses
  `max_inscribed_rect`, deliberately NOT the centre-anchored `inscribed_rect`
  — measured that anchoring on the original frame's centre degenerates to a
  zero-area rect whenever that centre sits outside the chosen strip, which is
  the common corner-view case).
  Gated in `plan()` on `strip is not None and abs(yaw) > 1e-9` (a strip placed
  AND a real rotation fired) — this necessarily reads as "any yaw," since
  `plan()`'s signature can't distinguish the three yaw sources (auto/h-marker/
  manual) CLAUDE.md names elsewhere; not fixed here.
  **The path that actually matters is `_whole_frame`, not the plain
  `inscribed_rect` call** — measured: `crop="auto"` (the default) blows
  through `crop_max_loss` and falls back to `_whole_frame` at essentially
  every yaw large enough for a strip to matter (e.g. 60% coverage loss at
  yaw=20°, against a 5% batch / 30% review budget), so `_whole_frame` gained
  the same strip-awareness in both its branches: the line-segs facade-box path
  (intersects the existing box with the strip band) and the no-line-segs
  fallback (previously ignored `strip` entirely, and had no width floor to
  begin with). **One deliberate override of "never smaller than source"**:
  the line-segs branch's `ow_c = max(ow_c, img_w)` floor is now skipped
  specifically when a strip band applies — measured that the band is
  narrower than `img_w` in most real (yaw, strip) pairs, so honouring the
  floor there would routinely re-admit the region the user's strip excluded.
  Pinned by `tests/test_warp.py::test_auto_crop_is_bounded_to_the_strip_plus_20_percent`
  (asserts the crop's warped x-range stays inside the strip+20% band, and
  separately that the SAME yaw without a strip really does exceed that band —
  the non-vacuity half this file's gotchas insist on). `python tests/run_tests.py --full`:
  **353 tests, 0 failed, 2 skipped** (up from 337 on 09-20 — `test_distortion`,
  `test_sam2seg` and this new test all grew the count; `test_sam2seg` is no
  longer the "0 bytes" file item C.10 named, worth a separate confirmation
  pass since that item is still open below).
  **Found on the way, NOT fixed, flagged instead of silently repaired:**
  (1) `_whole_frame`'s line-segs branch chains TWO margins, not one — a
  hardcoded 20% (`mx, my = 0.20 * (fx1-fx0), ...`) followed by a second,
  separately-clipped `settings.reframe_margin` margin (`config.py:177`
  defaults to **0.30**, not the `getattr(..., 0.20)` fallback its own line
  suggests) — so that path's real behaviour today is closer to "facade
  +20%, then +30% more," which is roughly the ratio A.4 measured as *worse*,
  not the 20% it measured as best. Independent of this fix; needs its own
  decision. (2) `tests/test_selfupdate.py` exists and is **not in
  `tests/run_tests.py`'s `MODULES`** — the runner now prints
  `!! not in MODULES, so never run: test_selfupdate` on every run. Exactly
  the "Tested is not reachable" trap this file names twice already; not
  investigated further here (unrelated to this task, and unknown whether it
  passes). (3) `ReviewSession.auto_crop()` (`review.py`) already threads
  `strip=self.strip` into `W.plan()`, so its `ow`/`oh` now correctly narrow
  — but its OWN subsequent `W.max_inscribed_rect(quad, ...)` call (on the
  full, un-clipped quad derived from `H_total`) is not itself strip-band-aware;
  the later `min(x1, ow)`-style clamp bounds the result into the now-narrower
  canvas but does not guarantee the *largest* rect within the band the way
  `_strip_bounded_rect` does for `plan()` proper. Likely fine in practice,
  not verified against a real corner-view asset by hand — a candidate for the
  same treatment if the button's output is ever measured and found wanting.

**2026-09-20**

- **[bug] The full suite could not finish at all** (`167cee6`). `pytest.skip()`
  raises `_pytest.outcomes.Skipped`, whose MRO is Skipped → OutcomeException →
  **BaseException** → object. It is not an `Exception`, so `_run_module`'s
  `except _Skip` *and* its `except Exception` both missed it; the skip escaped
  the worker process, could not be pickled, and `--full` died with
  `PicklingError: Can't pickle <class 'Skipped'>` — whole run lost, no summary.
  Eight call sites can trigger it, two do. Fixed by importing pytest's `Skipped`
  guarded and catching `(_Skip, _PytestSkip)`; **not** a blanket
  `except BaseException`, which would swallow `KeyboardInterrupt`.
- **[bug] One test was poisoning the next** (`167cee6`).
  `test_phosphor_lays_a_graded_ground_and_takes_it_away_again` ended on
  `_switch_theme("Light")` and never restored. `INK` is module-level and
  `_switch_theme` mutates it in place, so the palette outlived the App.
  Alphabetically "phosphor" precedes "theme", so
  `test_theme_switch_retints_canvas_backgrounds` opened with `INK` already Light
  and failed its own premise — **a test with nothing wrong with it, failing for
  what ran before it.** Root-caused by subagent, verified by hand: the palettes
  do differ (`Minimal Black` `#101216` vs `Light` `#f6f7f8`). Fixed in the
  polluter's `finally`.
- **The receding-row case is inside the gate now — but only just, and it is not
  "fixed".** `39079116-...-3Rec` used to fail round-trip at **3.71°** against a
  2.5° gate; **measured today it is 2.251°** — 90 % of the gate, still by far the
  worst in the pool, and one tuning change away from red again. It is still in
  the pool and still force-kept by `_ALWAYS`, so this is not a sampling artifact.
  **No deliberate fix was made** — it was carried by the yaw/warp rework
  (`594f244`, `3905fb7`, `5bfec7b`, `8aed6dc` and that series), and nobody has
  explained *which* commit did it. The diagnosis therefore stands unchanged: a
  receding row puts the horizontals on many differently-angled planes, the focal
  length is derived geometrically from that mixed evidence, and a wrong f buys a
  wrong pitch that fits the lines just as well. **The suite is green on this by a
  margin of 0.25°. Treat it as a live limitation, not a closed item.**
- **The plausible-bounds test now bounds what is *applied*, not the raw
  estimate — a deliberate narrowing, flagged for review.** Over the whole pool
  one of the user's new photographs,
  `ultra-weitwinkelfassade-von-antibesrathaus-...webp`, estimates **pitch
  +50.4°** against a 35° assertion. Measured: **the product is right about it** —
  confidence collapses to 0.07 and `process()` returns `SKIPPED "low confidence
  (conf=0.07 < 0.40; weakest: stability 0.15)"`. The photograph is left alone.
  The old assertion therefore failed a case the tool handles correctly, and its
  own docstring said "whatever it *decides*" — a refusal is not a decision to
  warp. The test now asserts the conditional claim instead: an implausible angle
  may be *estimated*, but never with enough confidence to be applied. **This is
  narrower than what stood before** (it permits wild-but-refused), which is why
  it is written down here rather than quietly changed — if the wider claim is
  wanted back, the asset is the argument to have it against.
  **Checked in both paths, because manual review is the product and review
  relaxes gates elsewhere:** `ReviewSession.would_skip()` (`review.py:980`) tests
  the same `min_confidence`, and `current_angles()` opens the sliders at
  **pitch +30.00° — the cap, not the estimate.** So the +50.4° never reaches a
  user in either path.
  **This retires an earlier measurement.** The 09-14 sweep recorded
  `max_pitch_deg = 30` as *inert on this pool, 0 refusals*, and warned "the cap
  is idle, not generous, and one steeper photograph would make it live."
  **That photograph has arrived.** The cap is now load-bearing, so re-deciding it
  is no longer an argument without evidence — this asset is the evidence.
  **It is also a live test case for distortion Stage 1**: an ultra-wide facade is
  exactly the barrel-distortion failure the roadmap predicts, and `_ALWAYS` now
  force-keeps it so the 5-asset sampler cannot drop it.
- **The worker harness is back, and the worker can see** (`d1ffe96`).
  `tools/worker_agent.py` and `tools/worker_bench.py` had been swept into
  `Trashcan/` by the dead-code pass (`1c2afe6`); the harness is not dead code, it
  is the only working way to drive the local worker. Restored. **Qwen has vision**
  (256k context; confirmed against the running server by posting an image and
  getting it described) and the harness had no way to hand it a picture, so every
  question about a rendered frame was answered from source. `view_image` fixes
  that: downscales to a max edge, queues the image, and flushes it as its own
  user turn — a `role: "tool"` message carries text only, so the picture cannot
  ride back in the tool result.
- **Root clutter moved to `Trashcan/stale_root_2026-09-20/`**: `bpc_errors.log`
  (53 KB, written under the pre-rename `bpc` name that nothing has produced since;
  the live name is `pc_errors.log`), `.claude_md_before_slim.bak`,
  `.knowledge_before_slim.bak`. All three were untracked. `analysis/` was
  recreated with its README — `tools/shoot.py` and `tools/worker_bench.py` still
  write there and `.gitignore` still describes it.
- **The worker's 27 skills are in git now** (`229a050`). `.qwen/skills/` held
  ~200 KB of markdown the worker extracted while working here — several of them
  recording lessons this repo paid for twice (the `_build` rebuild trap, canvas
  coordinate spaces, runtime theme switching) — and `.gitignore` hid the whole
  `.qwen/` directory. `.qwen/*` is ignored with `!.qwen/skills/` excepted, so
  `settings.json` and `tmp/` stay local. **Knowledge that lives on one disk and
  is invisible to every reader is knowledge the next session re-derives.**
- **The 3.9 promise is now checked** (`229a050`).
  `test_the_source_parses_on_the_oldest_python_pyproject_promises` reads
  `requires-python` out of pyproject — raise the floor and the test relaxes by
  itself — and checks two failures that happen at different times: a `match`
  statement is a grammar error, while a PEP 604 `X | Y` annotation is legal
  syntax at every version but is **evaluated at import** before 3.10, so it
  needs `from __future__ import annotations`. Grammar alone misses the second.
  **Measured: 64 files, 0 grammar errors, 0 runtime unions — the repo is
  3.9-clean**, which rules Python version out as the cause of the Linux CI
  failure (it fails on 3.9 *and* 3.12 while Windows passes both). All three
  detectors were verified to fire on synthetic input first; a test that cannot
  fail is not a test.
- **The photograph pool is committed as the user curated it** (`eebdb6a`) — 13
  assets out, 15 in, 3 cached SAM2 masks. `tests/assets/Synthetic/` is **not**
  committed: 15 MB of rendered output from `tools/render_synth.py`, and
  `test_assets._files()` globs `assets/*` and `assets/Horizontal/*` only, so no
  test reads it. The generator is tracked; its output is reproducible.
- **`tools/debug_ui.py` was failing against a feature that no longer exists.**
  It probed `app.review.v_grid` — measured: that name matches **only
  `debug_ui.py` itself**, nowhere in `src/`. The grid overlay was retired with
  the ruler-to-guides rewrite and the diagnostic was never updated, so the
  documented check reported three failures and made a healthy window look
  broken. Now probes `v_show_lines` (`gui.py:839`), the live before-pane
  toggle. **`FAILURES: none`, `pc_errors.log` clean** — the first clean run of
  the check this file requires. Second drift in this one file (it also watched
  `bpc_errors.log`, a name nothing has written since the rename): **a
  diagnostic that cries wolf is worse than none.** Delegated to the worker as a
  spec with every name supplied; it made the edits and folded the comment
  sensibly, and the architect ran it.
- **`view_image` compiled and did not run** — it used `io.BytesIO` with no
  `import io`, so it died with `NameError` at first use, and the commit adding
  it was "verified" with `py_compile`, which cannot see that. Found by running
  it. **A green compile is not evidence that a code path runs.** Worth
  recording: under the broken tool the worker reported `NO IMAGE RECEIVED`
  rather than describing the building from the filename. After the fix, on
  `Platte.jpg` it named a Plattenbau with mosaic murals, said the verticals
  converge going up, said the camera looks up, and spotted the silver
  Volkswagen lower right — checked against the photograph, all four correct.
- **`README.md` carried the same class of staleness** — it is user-facing, so it
  matters more than this file does. Fixed against the code: the installed
  command is **`pc`**, not `bpc` (`pyproject.toml:54`); the launcher is
  **`Perspective Correction.bat`**, and `run_bpc_gui.bat` does not exist; the
  **Grid** overlay it documented is gone (see the `debug_ui` entry above) and
  the corrected pane now carries pull-out grey guides instead; **Planar** is
  **PC Rectangle** (`gui.py:3308`), **ROI x** is **Facade strip (ROI)**
  (`gui.py:3386`), and **h-marker** (`gui.py:1002`) was missing from the table
  entirely. **`docs/ui.png` still shows the retired grid** and wants re-taking —
  noted in the README itself rather than silently left wrong.
- **[bug, found by looking] The mask row was clipped, and no test could see
  it.** Eleven widgets sat in one `grid(row=0, ...)`; **measured at the
  1920x1080 window floor the line asked for 1035 px of a 910 px field**, so
  "Clear Mask" rendered as "Cle". It overflowed at 2560x1400 too. Found in a
  screenshot, not by the suite - and the suite *could not* have found it:
  `test_the_save_button_is_reachable_at_every_window_size` asserts every child
  has `winfo_width() > 0`, which is the right question for a control that never
  got packed and **the wrong one for a control that did**. Tk maps a child that
  does not fit and reports its full requested width; only the container's
  allocation tells the truth. Split into two packed sub-frames by meaning -
  where the mask **comes from** above, what is **done to it** below - rather
  than two grid rows, because grid columns are shared between rows and a 189 px
  checkbutton in column 0 would widen the "mask" label's column with it. Now
  506 px and 529 px in 910. Pinned by
  `test_no_tools_field_row_asks_for_more_width_than_it_gets`, which asks the
  container. **That test took three attempts to give teeth**, and the first two
  were caught only by reverting the fix and watching them stay green: filtering
  out containers whose children are not all at one y threw away the offending
  frame, and grouping by `winfo_y()` split one grid row into one group per
  widget, because `sticky="w"` centres children vertically. It groups by
  `grid_info()["row"]` now. **A test that cannot fail is not a test, and the
  only way to know is to run it against the bug.**
- **`docs/ui.png` re-taken** - the committed one still showed the retired
  measuring grid. Rendered the real window at 1920x1080 with an asset loaded and
  grabbed it; it now shows the cross, the corner tool palette, the Lines/Mask
  and Check-lines switches, the h-marker and yaw controls, and the
  Review/Unattended bar as they actually are.
- **The test runner's start method is pinned to `spawn`.** It was left to the
  platform, and the platform does not agree: **Windows spawns, Linux forks.**
  Forking inherits a live interpreter - imported C extensions, handles, threads
  - while spawning re-imports clean, and it is the only start-method-sensitive
  construct in the whole run. **This does not claim to be the cause of the
  failing Linux job**; it removes one of the two places the platforms genuinely
  differ, so the next person reading that log has one fewer variable. Suite
  unchanged on Windows at 328/0/2, which is expected - Windows already spawned.
- **[hazard, NOT changed - the user's own setting] The GUI writes into the
  off-limits sibling checkout.** The remembered output folder is
  `D:/Coding/Batch-Perspective-Correction/tests/assets/Horizontal`. That is the
  stale duplicate this file tells everyone not to touch, and it is where every
  **Save** in the review panel currently lands. It is a stored preference, not a
  default in code (grepping `src/` for the path finds nothing), so it has not
  been rewritten - silently changing where someone's work is saved is worse than
  telling them. **Check it before the next review session.**
- **The tool palette is one set now, and one construction site.** It was
  **seven keys in four visual languages** — two flat line diagrams, a solid
  disc, a monochromed dinosaur emoji, and two *pictorial* font icons that were
  also saying the wrong thing: **SAM, which box-selects a building, wore an
  eyedropper, and "strike slanted lines" wore Segoe's debug beetle.** Six marks
  are now drawn in one language (`_glyph_pil`): straight strokes at one pen, no
  tapers or highlights, perspective on the one glyph whose meaning *is*
  perspective, and every mark cropped to its ink by `_fit_ink` so they share a
  footprint — measured before, the drawn keys spanned 0.69 and 0.77 of the box
  against the font's 1.00, so they were simply smaller in an identical key.
  **The bigger find was underneath the glyphs.** Three keys were assembled by
  hand beside the factory that built the rest, and had drifted in three ways
  that are not about taste: `highlightthickness=2`, which Tk adds *outside* the
  requested width, made them **4 px larger** in a vertical column where nothing
  hides it (measured 38 px against 34 px); they lit up in `INK["accent"]` when
  held while the others used `INK["line"]`, so **"which tool am I holding" had
  two different answers**; and their glyphs used inline pixel arithmetic
  (`gs // 10`, `gs // 4`) — the hard rule's forbidden thing, and plausibly
  *why* they drifted. All seven now go through one `tool()` factory; exactly one
  `tk.Checkbutton(bar` remains in the file. The pen and box live in
  `layout.py` (`glyph_stroke`, `glyph_box`).
  **Two deliberate exceptions, recorded rather than quietly made**: the brush
  stays a filled inverted disc (it is a swatch, not a diagram — user,
  09-15), and **the Grounding DINO key keeps its dinosaur**, a pun the user put
  there on purpose. A previous session already traded its colour for coherence
  and stopped short of replacing it, which is the right place to stop.
  **The measurement disagreed with the eye, and the eye won** — that is the part
  worth keeping. A run-length median says the three icon-font keys at the top of
  the same column stroke at **1.0 px**, and the first version of `glyph_stroke`
  believed it (it had measured the two *pictorial* glyphs, which are heavier and
  never represented the set). Rendered at 1 px the drawn marks **vanish** beside
  those icons; at 2 px they match. Font glyphs are antialiased, so a one-pixel
  core carries soft shoulders and *looks* wider, while a hard-edged drawn stroke
  measures exactly what it is. **No run-length median reports optical weight.**
  The docstring now records the number that misled rather than the number that
  was chosen. Pinned by
  `test_every_tool_key_is_the_same_size_and_every_glyph_renders` (verified
  failing on the reintroduced defect: `[(34, 34), (38, 38)]`) and
  `test_the_drawn_glyph_pen_is_one_number_and_follows_the_glyph_size` (verified
  failing on a 1 px pen). `docs/ui.png` re-taken again.
- **[measured, nothing built] P4's two-facade detection does not work.** Same
  discipline as the P9 and pitch-cap passes: the obvious signal was scored over
  the whole 51-file pool before any production code was written, and it failed.
  **Signal 1** (opposite-side horizontal VPs weighted by RANSAC support) ranks
  `lochfassade.jpg` — the pool's most unambiguous corner view — at **46 of 51**,
  *below* two confirmed-flat facades, because `ArchitectureScheme` reports its
  second plane at support 0.045. **Signal 2** (spatial separation of the two
  planes' line midpoints) fixes `lochfassade` but puts `39079116`, the asset the
  whole item exists for, at the **bottom**, indistinguishable from near-flat
  photographs. No threshold and no obvious combination separates them.
  **Why it cannot work as specified**: the receding row is not a clean two-plane
  corner at all — its horizontals smear across many shallow-angle planes, which
  a binary h1/h2 read-out was never going to represent — and the post-hoc split
  under-detects a narrow or steeply foreshortened second plane. **A diagnostic
  that fires on flat facades and stays silent on the pool's clearest corner
  would be worse than the current silence**, so none was added. Blocked on
  `knowledge.md` §1, facade-outline-first.
  **Discrepancy found on the way — and since resolved, see the entry below.**
  `knowledge.md` §1 cited `Alte_Scheune` support as `h1=0.61, h2=0.36`; at the
  resolution the estimator actually runs it is **`h1=0.596, h2=0.147`**. A sweep
  over long edge then killed the ratio as well: `h2` is not monotonic in
  resolution (`Alte_Scheune` 0.358 at 4032, 0.147 at 1600, 0.249 at 1010) and
  moves the *other* way on `lochfassade` (0.045 native, 0.117 at 600), so the gap
  reads 5.5x at a matched size, 3.3x at production and 1.5x at 600. **§1's 8x,
  and the 3.3x this ledger briefly replaced it with, are both artifacts of a
  chosen scale.** Only the direction survives. This blocker is therefore still
  blocked, and on a weaker foundation than it looked: if P4 is ever taken up the
  comparison must be round-trip error on the production path, not support.
- **[not a defect] The `cli.py` `isatty()` gate was a wrong finding, and is
  struck.** It had been carried as an open MED item — "blocks piped
  double-click". Measured: when stdin is not a tty the `else` branch prints
  `--overwrite needs --yes when running non-interactively` and **returns 1
  before any file is touched**. That is the only safe answer for a flag that
  destroys originals, and it deliberately also stops `echo y | ... --overwrite`
  from authorising destruction nobody typed. `--yes` (`cli.py:75`) is the
  documented way to mean it non-interactively. **No logic changed** — a comment
  explaining why, so the next audit does not re-file it, plus two tests pinning
  *both* directions: refusal without `--yes` (asserting `process` was never
  called, not merely that a message was printed) and no refusal with it.
  Red/green demonstrated by mutating the `return 1` to `pass`.
- **CI gained a windows-only `--full` job.** The existing `test` job is
  untouched and still runs the fast run on the full matrix; `full-windows` is a
  sibling with no `needs:`, so neither gates the other. Windows-only on purpose:
  the Linux leg is failing for an unrelated undiagnosed reason, and `test_gui`
  opens real Tk windows. **Whether a runner's single virtual display tolerates
  `test_gui`'s off-screen `geometry("<WxH>-4000+0")` is UNVERIFIED and cannot be
  checked from here — the first run of that job is the experiment, not a
  formality.** YAML machine-validated.
- **[bug, measured] The primary action button was unreadable in one theme.**
  `Accent.TButton`'s foreground was the literal `"#0b1017"` — one dark ink,
  hardcoded for every palette. Five of the six have a bright accent and read
  fine. **Amiga 500 does not**: accent `#0055BB` under that ink measures a WCAG
  contrast ratio of **2.74** against AA's 4.5, which made **Save** — the button
  the entire review loop ends on — the least legible thing in the window.
  Nothing said so, because a hex literal in a widget option looks like a
  decision somebody made. `on_accent()` now computes both candidate ratios and
  takes the better, rather than switching on a luminance threshold (which is
  one more number to be wrong about). Amiga goes **2.74 → 6.96** and **no other
  theme changes at all** — the fix reaches only the palette that needed it.
  Pinned by `test_every_theme_keeps_the_accent_button_readable`, asserted
  against the 4.5 constant rather than today's numbers, and verified failing on
  the old behaviour.
- **"Every colour comes from `INK`" is enforceable now, instead of prose.**
  There were **24 bare `#rrggbb` literals** scattered through the draw calls
  below the palette, and the rule could not be checked by looking: you could
  not tell a deliberate colour from a forgotten one. That ambiguity is how the
  Amiga bug above survived and how the tool palette ended up with two different
  "held" colours. They are named now — `OVERLAY` for the marks drawn **on a
  photograph**, plus `MASK_SWATCHES`, `ICON_WHITE`, `INK_ON_ACCENT_*`, beside
  the `GUIDE_GREY` that was already exactly this idea.
  **`OVERLAY` deliberately does NOT follow the theme, and that is the point of
  writing it down.** Those marks are read against the *picture*, not against
  the palette: a green inlier line has to stay legible on brick, sky and shadow
  in every theme. Nine of them duplicate an `INK` value by coincidence of
  taste, and binding them to it would mean picking Amiga 500 — whose `ok` is
  `#008800` — turns the SAM selection outline into dark green on a dark facade.
  Pinned by `test_no_colour_is_typed_into_a_widget_below_the_palette_tables`,
  which **parses** rather than greps, so a hex inside a docstring (such as
  `on_accent`'s, explaining the literal it replaced) is not mistaken for a
  colour in use. Verified failing on one reintroduced literal.
- **[not a defect] `lines.prepare()`'s empty `masked_out`.** Carried as an open
  **MED** audit item — "shape `(0,)` not `(0,4)`", which would break any
  consumer doing `dropped[:, 0]`, on a path that runs constantly (any clean
  photograph whose mask ignores nothing). Measured: the list-comprehension
  *does* produce `(0,)`, and `lines.py:388-389` catches it two lines later with
  `if len(masked_out) == 0: masked_out = np.zeros((0, 4))`; `:362` initialises
  it the same way. The guard predates the audit, so the finding was already
  fixed in the code the audit read. **Nothing changed**, and
  `test_masked_out_stays_four_wide_when_nothing_was_dropped` now pins the guard
  so a future edit cannot quietly drop it. **Second wrong finding struck
  today**, after the `cli.py` `isatty()` one — the hard rule about pointers and
  verdicts keeps paying.
- **The remaining four audit findings, adjudicated. Two were wrong.** Running
  the arithmetic before reading the proposal keeps being the whole method.
  * **`model.focal_from_horizon` "dead no-op line with misleading comment" —
    NOT PRESENT.** Checked mechanically with `ast`, not by eye: no bare no-op
    expression, no local assigned and never read, nothing unreachable after a
    return, anywhere in lines 114-211. Either the finding was wrong or it named
    code the 09-19 dead-code sweep already removed. **Third non-defect today**,
    after `cli.py`'s `isatty()` gate and `lines.masked_out`.
  * **`geometry.normalize_vp` "zero-norm fallback points down, not up" — the
    claim is wrong and there was a real defect underneath it.** The comment said
    "up (negative y)" and the code returned `[0, -1, 0]`, which *is* negative y:
    comment and code agreed. **What was actually broken** is that the fallback
    was the only return skipping the sign fix two lines below, so the function
    violated the invariant that fix exists for — its own comment says "so that
    equal vanishing points compare equal", and `normalize_vp(0)` returned
    `[0, -1, 0]` while `normalize_vp([0, -1, 0])` returned `[0, 1, 0]`. The path
    is reachable: `intersect` normalises `cross(l1, l2)`, zero for two identical
    lines. The careful "up" was moot anyway, because a vanishing point is a line
    through the origin and not a ray, so direction is erased on every other
    path. Now consistent, pinned by
    `test_normalize_vp_signs_every_result_the_same_way_including_its_fallback`,
    which asserts the **invariant** rather than the fallback's value — the
    defect was a path escaping the rule, not a wrong constant.
  * **`review.py` reaching into `preview._draw_lines` — REAL, fixed.** Four
    call sites across a module boundary into an underscore-prefixed function.
    **An underscore two modules ignore is not privacy, it is a note that went
    unread**; it is `preview.draw_lines` now, which is what was true all along.
  * **Diverging colour tables in `preview.py` and `scheme.py` — REAL, fixed.**
    `scheme.draw_preview` wrote `(0, 200, 0)` for the lines that count and
    `(96, 96, 96)` for the ones that do not, inline, while `preview.py` drew the
    same two ideas as `GREEN (80, 220, 90)` and `GREY (130, 130, 130)` from a
    named table. **Two renderings of the same geometry that did not agree on
    what green means** — and the open found-geometry research goal is precisely
    a choice between those two renderings, which is an awkward choice to make
    from there. One table now, plus `VP_MARK` for the vanishing-point ring.
    Pinned by `test_the_two_renderings_of_found_geometry_agree_on_what_green_means`,
    which **renders and reads pixels** rather than grepping the source, so a
    constant imported but never used still fails.
    **The two lines need different assertions, and the reason is worth keeping**:
    the 2 px relevant line has solid core pixels and matches exactly, while the
    1 px ignored line is antialiased and peaks at **118**, not 130 — an exact
    match is the wrong question there. What makes the weaker assertion sound is
    that **antialiasing can only darken**, so a peak of 118 cannot have come
    from a source of 96, and measuring above the old value proves it is gone
    without needing to know the coverage.
- **[measured, deliberately not changed] The spacing was already consistent.**
  The UI item called for icons, button sizes, type scale *and* spacing as one
  pass, so spacing was measured rather than assumed: **132 `padx`/`pady` values
  in `gui.py`, of which 41 % are not multiples of the `GRID = 4` in
  `layout.py`.** That looks like the drift the entry feared — and it is not.
  **Every single off-grid value is exactly 2 away** (2, 6, 10, 14, with one 14
  at `gui.py:1359`), so the window is laid out on a consistent **2 px** rhythm
  and `GRID = 4` is a local doubling for the palette keys alone. There is no
  disorder to fix; a sweep forcing 54 values onto a 4 px grid would move the
  whole window and correct nothing. **Nothing was re-spaced.**
  What *was* wrong is the documentation: `GRID = 4` carried the comment "the
  spacing unit the tool column is built from", which a reader can easily take
  as the window's unit. It now states the measurement and why the spacing was
  left as it is, so this is not re-proposed as an untouched task.
- **[resolved] Two agents disagreed about one number, both had really run it,
  and the cause was resolution.** One reported `Alte_Scheune` support as
  `h1=0.596, h2=0.147`; the other reported `v=0.9379, h1=0.6109, h2=0.3577`
  "byte-identical over three runs" and wrote into `knowledge.md` that the
  citation **holds**. It does not. Measured by hand, four ways:

      gray + resized colour (production)   {'v': 0.8221, 'h1': 0.5961, 'h2': 0.1466}
      gray + FULL-RES colour               {'v': 0.8221, 'h1': 0.5961, 'h2': 0.1466}
      gray + no colour                     {'v': 0.8221, 'h1': 0.5961, 'h2': 0.1466}
      FULL-RES gray + full-res colour      {'v': 0.9379, 'h1': 0.6109, 'h2': 0.3577}

  The asset is 4032x3024 and `pipeline.analyse` calls
  `io.analysis_gray(bgr, settings.detect_max_edge)` — 1600 on the long edge —
  before anything else. **Skip that one call and the supports move by more than
  a factor of two.** The second agent skipped it, and so did whoever wrote the
  citation, because `0.94 / 0.61 / 0.36` is the full-resolution answer to three
  decimals. **Nothing drifted; it was never measured the way the estimator
  runs.** Checked against the other five "Scheune" assets in the pool — none
  produces those numbers at production resolution — and against `lochfassade`,
  which is identical at both resolutions because it is already inside
  `detect_max_edge` and never downscaled.
  **The part worth keeping is not the number.** Both agents were honest and one
  was thorough — it ruled out a cached mask, and monkeypatched
  `_plausible_horizontal_rows` back to its pre-`b6cefbf` 45° gate and traced
  that it ran — but it never varied the one thing that mattered, and then
  stamped a wrong figure as verified with today's date. **A confirmed-wrong
  number carrying a fresh verification date is worse than a stale one**, which
  is why the architect running it is not a formality. And this file had already
  repeated the first agent's half of it without running it either.
- **This file rewritten from measurement** - see the header.

**2026-09-19 → 20 (from `QWEN.md`, verified against the code)**

- **PC Rectangle squeeze fixed** (`8aed6dc`). `planar.target_size()` now scales
  up, never down, so the output canvas is at least the source quad's bounding
  box. Foreshortened edge lengths used to set the output size, compressing
  pixels; the warp now **adds** pixels for expansion and never deletes.
- **M-LSD second pass in the check-lines diagnostic** (`21848b5`). When the
  primary detector is not M-LSD, the overlay runs an extra M-LSD pass in
  cyan/yellow beside the primary's green/orange, so both detectors' line quality
  can be compared on the corrected image.
- **Dead code cleanup** (`1c2afe6`) — broken one-off tools and the whole
  `analysis/` directory to `Trashcan/`. **This pass also took `worker_agent.py`,
  which was wrong**; see 09-20 above.
- **"pc rect (auto)" disabled** (`a67c4ae`) — corner detection unreliable on
  multi-facade views. `auto_facade_corners` stays; the manual 4-corner click is
  the supported path.
- **H-Marker is a per-facade tool, by design.** It gives an exact yaw for **one**
  facade. On a corner view with opposing VPs the single-rotation model cannot
  straighten both facades at once — expected, not a bug. **Stale as of
  2026-09-26 (audit-caught): there is no separate "horizontal marker (manuell)"
  checkbox any more** — it was removed 2026-09-20 (`gui.py:1299-1306`'s own
  comment: "the line is the switch now"); a drawn control line is itself the
  h-marker, nothing to toggle against "horizontal auto (yaw)". PC Rectangle is
  the tool when one surface needs full planar control.

**2026-09-17 → 18**

- Loupe: Alt-damping (crop centre follows the cursor at `1/LOUPE_MAG`, crosshair
  turns cyan), instant render on press, Alt state forwarded through `_forward`.
- Mark delete handle is an X, not a plus — a plus reads as "add".
- **F3 status label**: `_set_status`/`_set_status_extra` write to a real
  `lbl_status` (28 call sites), so ~20 interaction hints are finally visible.
  They had been no-ops since the status box was removed on 09-13.
- **SAM2 box fix**: `_on_sam_release` stores `_sam_box_px` (pixel ints) beside the
  normalised `_sam_box`; SAM2 wants pixel coords, and normalised 0–1 values were
  read as sub-pixel, selecting the whole frame.
- Horizontal/yaw retuned: `min_horizontal_support` 0.3 → **0.15**,
  `n_hypotheses` 3 → **6**, `_plausible_horizontal_rows` angle filter 45° → **70°**.
- Mask overlay controls in the Q1 bar: colour swatch + opacity slider, with a
  custom 4×4 picker because Tkinter's `colorchooser` is broken on Windows.
  Default alpha 0.28 → **0.60**, brush width 10 → **60 px**.
- The corrected pane shows its output size and the live crop size, as a canvas
  text item at the **bottom left** (`gui.py:2595`, `f"{ow}×{oh}  {cw}×{ch}"`).
  This entry used to name a widget `_after_dims` and place it top-right; there
  is no such attribute anywhere in `src/`, and the corner was wrong too
  (found 2026-09-20 by checking every symbol the docs name against the tree).
  Menus themed via `TMenu` instead of the OS default black.
- **Guide system rewrite** (09-16): cross-pull creation with a dashed preview,
  canvas-based interaction at 15 px grab distance, border-zone creation from Q2,
  and **crop handles checked first** so all 8 of them outrank guides.

**Earlier — titles only.** The full reasoning for any of these is in this file's
git history (`git log -p -- CLAUDE.md`), which is why dropping the prose is not
lossy. Kept so nothing is silently re-proposed.

`F1` fill_max_share warns · `F2`/`F4` one crop gate, review bumps to 30 % ·
`F5` Save never disabled by a warning · `P1` pure-yaw breach warns and caps ·
`P2` `diag["yaw_skipped"]` · `P5` manual-yaw bypass documented and tested ·
one mask registry (`LAYER_SCOPE`, `ignore_mask`, one touch rule) · SAM wired
into the actual fit (it had been decorative — it drew an outline and never
reached the estimator) · gdino weights default to the vendored BiRefNet ·
`max_horizontal_deg` 8 → 30 → 60 · `max_pitch_deg`/`max_roll_deg` measured and
left (uncapped, the steepest the estimator wants is 28.62° pitch against a 30°
cap — **the cap is idle, not generous**; get a photograph it refuses before
moving it) · caps/confidence sweep (`min_confidence` 0.40 refuses 7/51, six of
them on `stability` alone, and the distribution has no natural break there) ·
Stage 0 distortion · package renamed `bpc` → `pc` · rulers became plain grey
guides pulled from the cross · both previews fill their field at one scale ·
planar removed from the palette, module kept · lens profile looked up by name
and **refused rather than guessed** when ambiguous · suite sampled to 5 assets ·
perfect-cross UI (superseded 2026-10-19: rows broke to a 3:2 split, pictures
on top — see the Done entry) · Hough
detector removed · M-LSD unblocked in the GUI interpreter.

### Repo note (measured 2026-09-20)

- `D:\Coding\Perspective-Correction` is the live checkout. It started
  2026-09-20 level with `origin/main` and is **ahead and unpushed** by that
  whole session's work. **Pushing was deliberately not done**: it publishes, and
  it was never asked for. One command clears it:

      git push origin main

  The count is deliberately not written here. It was, for one commit, and the
  commit that wrote "10" made it 11 — a number in a file is stale the moment
  the file is saved. Ask git:

      git log --oneline origin/main..HEAD
- **The working tree is clean.** The asset-pool curation that was sitting in it
  — 13 tracked photographs out, 15 in, 3 cached SAM2 masks — is committed
  (`eebdb6a`). **Every pool-wide number in this file was measured after that**,
  so it is a claim about the current pool and not an earlier one.
- `Trashcan/`, `hpc_save/`, `verworfen/` and `analysis/*` are gitignored. Nothing
  is ever hard-deleted here — things move to `Trashcan/`, which is why the
  `worker_agent.py` mistake was recoverable.
- `models/BiRefNet|GroundingDINO|sam2|sam3` are gitignored: several GB, five
  files over GitHub's 100 MB hard limit, and a push containing them is rejected
  outright rather than failing gracefully. The vendored M-LSD model is the
  deliberate exception (small, `models/LICENSE.mlsd`).

## Gotchas (each cost real time)

- **`pytest.skip()` is not catchable as `Exception`.** `Skipped` derives from
  `BaseException`. Any bespoke runner that catches `Exception` will let it escape
  — and across a process boundary it is unpicklable, so it kills the whole run
  rather than one test.
- **Module-level mutable state leaks between tests in the same process.** The
  runner executes a module's tests in **alphabetical order by function name**, in
  one process. `INK` is the live example; anything else module-level behaves the
  same way. Restore in a `finally`.
- Tk fires **no `<Configure>`** for a widget re-packed at the size it had, so a
  `pack_forget()`/`pack()` round trip reschedules nothing. `minsize(1920, 1080)`
  means no resize can starve the canvases; an unmapped canvas is the only
  reachable starvation branch.
- Tk 8.6 does **not** fire `<<ListboxSelect>>` for programmatic `selection_set`;
  call `_on_list_select()` by hand.
- **`bind()` replaces, it does not add.** Binding the same sequence twice on one
  widget silently kills the first handler — that is how SAM's drag, release and
  right-click were all dead at once, which read as "SAM is missing".
- **Variables rebuilt by `_build` go stale**, because `_build` re-runs on every
  photograph while the tools field is built once. A checkbox then sets a variable
  nothing reads, and the tool dies from the second photograph onward. Met three
  times (mask brush, facade strip, planar). Make the variable once, behind a
  `getattr(...) is None` guard.
- A test written to match observed behaviour certifies the bug and goes red when
  somebody fixes it. Assert what the code is *for*, against the constant that
  defines it, never the number it currently prints.
- The round-trip test must keep the border guard (8 % crop), or `BORDER_REPLICATE`
  smears edge pixels into long straight streaks the detector reads as lines —
  that artifact produced two confident wrong conclusions.
- **Three pixel spaces, one conversion bug class (gray vs bgr vs preview).** The
  analysis image is `gray = bgr * scale` px; the preview is `small = bgr * s` px
  (`s = min(1, max_edge / max(w, h))`). Converting gray → small is `* s / scale`,
  **not** `* s`. The control-line marker projection in `render_after` did `* s`
  and put every marker at `scale`× the correct position (pulled toward the
  centre) — invisible at `scale = 1.0` (Platte_1), which is why a scale-1.0
  repro cannot catch it. Convert between spaces explicitly, and test any
  projection on a `scale ≠ 1.0` asset (Alte_Scheune, scale 0.397) before
  trusting it.
- Caches key on the **full** argument tuple; hit-check is `key in cache`, never
  truthiness (`None` is a real result).
- `cv2.imread` mangles non-ASCII paths on Windows — use `masks._imread`
  (`np.fromfile` + `imdecode`).
- Synthetic scenes are right for geometric questions with ground truth, wrong for
  statistical ones about real texture.
- **Tested is not reachable.** A seam with tests is half a feature; "done" means
  someone can use it. Check for the caller before writing a Done entry. SAM drew
  an outline and never reached the estimator for weeks while looking wired.
- **A probe that skips `analysis_gray` is measuring a pipeline that does not
  exist.** `pipeline.analyse` downscales to `detect_max_edge` (1600 on the long
  edge) before any detection, so a 4032x3024 photograph is never seen at full
  size. Measured 2026-09-20: skipping that call moves `ArchitectureScheme`'s
  plane supports by more than 2x on one asset, and it is what put a wrong number
  into `knowledge.md` in the first place and what made two agents disagree about
  it. **Every probe against `ArchitectureScheme`, `lines.prepare` or anything
  downstream starts with `analysis_gray`.**
- **`git add -A` while an agent still owns a file commits a half-finished tree.**
  The companion to the turn-limit lesson: a worker that stops mid-edit leaves a
  file that compiles and is wrong, and a blanket stage sweeps it into the commit
  under the architect's own message. **Either every agent is idle before staging,
  or the paths are named explicitly.** No damage yet — recorded because it has
  been relied on repeatedly and worked by luck.
- **Windows filenames are case-insensitive, so `x_c.png` and `x_C.png` are ONE
  file.** Cost an entire measurement on 2026-09-20: a two-pass harness wrote its
  cropped input to `_c.png` and its pass-2 output to `_C.png`, so the second pass
  silently overwrote its own input and the comparison measured the same file
  twice — reporting "+0.000 deg, unchanged on all 10" as a *finding*. It looked
  like a clean null result and it was a filename collision. **A row of exact
  zeros is a bug until proven otherwise**; real measurements are never that tidy.
  The one file that escaped had an umlaut in its name, because `cv2.imwrite`
  mangles it into a different name entirely — use
  `cv2.imencode(...)[1].tofile(path)`.
- **Do not drop the photographs that refuse.** (user, 2026-09-20: *"rausfallen
  gibt's nicht"*.) A measurement that skips the assets the tool declined is
  measuring the easy half and will report whatever the gate lets through. Set
  `min_confidence=0` and `refuse_beyond_limit=False` for any comparison — a
  refusal is a result to be recorded, not a missing datum. Doing this changed the
  two-pass verdict: with the refusals restored the sample went from 3 usable
  photographs to 11, and the conclusion with it.
- **Ask which PATH a gate is on before removing it.** A measurement about a
  feature is not automatically a measurement about the code path you are editing.
  The confidence gate was removed from `pipeline.process` on the strength of a
  sound measurement — and `process` is the *unattended* run, so the change took
  the safety off where nobody is watching, and a visibly sheared photograph came
  straight out. The review panel does not call `process` at all. **Manual-first
  loosens gates where a person is looking; it says nothing about the batch.**
- **A test that passes for a reason it does not name is not passing.** Twice on
  2026-09-20, both caught only by deliberately breaking the source and re-running:
  a near-edge test that needed a `found_a_case` guard, and a review-gate test
  whose asset happened to sit below the threshold, so the clause under test was
  never exercised. **Sabotage the thing the test is about, watch it go red, put
  it back.** It costs a minute and it is the only evidence that exists.
- **Deleting one function strands the next.** After any deletion, sweep every
  function in `src/pc` for references across src, tests and tools.

## Worker (local Qwen, via `tools/worker_agent.py`)

**A third route exists now, and its first real test (2026-09-26) failed in a
way worth naming.** Claude Code Desktop can expose the same Unsloth server as
MCP tools, `unsloth_agent` (write) / `unsloth_plan_agent` (read-only). Asked
to propose the strip-aware auto-crop fix above, it reported that its
filesystem access to this checkout was sandboxed/denied, then answered anyway
— from two old session transcripts instead of the live tree — and its own
report flagged the line numbers as "not independently verified." The
proposal was discarded, not applied; a Sonnet plan-subagent with real
Read/Grep/Bash access redid it from scratch and found real corrections to it
(see the 2026-09-26 Done entry). **Treat an `unsloth_agent`/`unsloth_plan_agent`
result as a pointer, same as any worker or subagent finding, and check first
whether it says its own file access actually worked** — this session's proposal
said so itself, in its own uncertainty section, and would have been missed by
skimming only the confident-sounding parts.

**Do not use the `qwen -p` CLI and do not use the plugin. Use the server.**
`qwen -p` registers no tools: it reads and reasons but cannot write or execute,
which is why the worker was written off for weeks. `tools/worker_agent.py` POSTs
to `/v1/chat/completions` with a `tools[]` array, executes the calls, feeds the
results back and loops.

```bash
python tools/worker_agent.py package.txt
```

Five repo-scoped tools, **no raw shell**: `read_file`, `grep`, `str_replace`,
`run_tests`, `view_image`.

- **Server**: `unsloth studio run --model unsloth/Qwen3.8-27B-GGUF:UD-IQ4_XS`
  (~45 s to load). **Closing the console that started Studio kills the server.**
  Check `/v1/models` for `loaded: true` before blaming anything.
- **Context: 258 688** (asked of the running server 2026-09-20; the same response
  reports `native_context_length: 262144` and `max_context_length: 88832` — the
  served window is the loaded quant's, not the model's). This file has carried
  125 056, 94 848 and 96 256 for this one number. **Trust only a figure with a
  date beside it, and re-ask the server.**
- **Vision works** (2026-09-20). Use `view_image` on screenshots, rendered output
  and test assets instead of reasoning about pixels from source. Max 2 per turn,
  downscaled to a max edge.
- Sampling: non-thinking `T=0.7 top_p=0.8 top_k=20 presence_penalty=1.5` — the
  only path on which tool calling is measured. Thinking-mode tool calling is
  **untested**. **Never temperature 0** (Qwen3 degrades into repetition). Give
  `max_tokens` real room; a thinking model on a small budget returns empty
  `content` and a full `reasoning_content`.
- Temperature is not a lever: a sweep over 0.3–1.0 passed every task at every
  point, wall time flat at 11–13 s.
- **Never hand the worker a tool that returns an unparsed tail.** `run_tests`
  returns the verdict line and strips the Tk teardown noise, because the worker
  once retried eight times against that noise — eight of its twelve turns.
- **It refuses to claim success it cannot verify**, which is the property that
  makes it usable. Measured delegation: it read two files, made two `str_replace`
  edits, grepped to confirm the kept twins were still there, and stopped — `git
  diff` was 23 deleted lines and nothing else.
- Package contract: GOAL / SCOPE / CONSTRAINTS / VALIDATION, **with every name
  supplied** — it cannot grep for what you failed to tell it, and asking it to
  explore is asking for invention. Full method in `skills/delegation.md`.
- The architect reads `git diff` before anything is committed. A green suite is
  evidence, not permission. Anything needing judgement — which of two designs,
  whether a seam should exist — is not a package at all.

## Skills and references

- `QWEN.md` — coordinate-scaling rule and checklist, visual-debugging workflow,
  MCP tool table. **Read before touching stored geometry or its display.**
- `skills/ui.md` — INK palette, one-window structure, review-panel rules,
  off-screen test pattern.
- `skills/delegation.md` — how to write a worker package; the lessons are worth
  more than the contract.
- `docs/worker-environment.md` — chat-template internals and the llama.cpp
  `reasoning_tokens`-always-0 bug (harmless).
- `docs/worker-anleitung.txt` — **German, for the user**: bringing the worker up
  from nothing in a fresh session.
- `.claude/skills/debug/SKILL.md` — benchmark / BiRefNet failure workflow.
- `.claude/skills/grounding-sam/SKILL.md` — Grounding DINO + SAM2
  detect-then-segment, local model paths, the benchmark gate.
- `knowledge.md` — analysis and sources for the open research goals.
- `docs/claude-md-overhaul-plan.md` — historical; superseded by this file and
  `knowledge.md`, kept only for the Stage 4 plumb-line detail.
- `docs/qwen-knowledge.md` — history, not instruction: why the worker was once
  shelved. The Worker section above supersedes it.
