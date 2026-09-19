# Perspective Correction — working notes (live)

**Rewritten 2026-09-20 from measurement, not from its own previous text.** The
version before this one was five days stale and confidently wrong about the
thing that matters most: it recorded the suite as "307 tests, 2 failed" when
`--full` could not run at all. Everything below was either re-measured today or
is marked with where it came from.

## Governance — four files, one status

| file | holds | rule |
|---|---|---|
| **`CLAUDE.md`** (this) | **status, hard rules, how to run things** | the only place status lives |
| **`QWEN.md`** | worker/agent operating rules — coordinate scaling, vision, MCP tools | **no status.** Points here instead |
| **`debug.md`** | the read-only audit's findings list (1383 lines) | append-only findings; a finding is a pointer, not a verdict |
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

## Running things (do not rediscover this)

- Project root `D:\Coding\Perspective-Correction`, Windows, PowerShell 7,
  Python 3.12.9 (python.org), 32 cores.
- Compile: `python -m py_compile src/pc/gui.py` (and anything else touched).
- One module: `python tests/run_tests.py test_gui`.
- **The runner has two modes, and the bare command is not the full suite.**

  | command | what it runs | measured 2026-09-20 |
  |---|---|---|
  | `python tests/run_tests.py` | fast — skips `test_gui`, `test_assets` | **283 tests, 0 failed, ~19 s** |
  | `python tests/run_tests.py --full` (or `PC_FULL=1`) | everything, 23 modules | **328 tests, 0 failed, 2 skipped, ~65 s** |

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
  fails the run. There are **23** modules.
- GUI off-screen pattern: `App(start_maximized=False)`,
  `geometry("<WxH>-4000+0")`, pump with `update()` + `sleep(0.02)`, then
  `destroy()`. `invalid command name ..._pump` on teardown is harmless Tk noise.
- `python tools/debug_ui.py` sweeps the window lifecycle; must end
  `FAILURES: none` with `pc_errors.log` clean.

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
- `H = K R K^-1`, always — a camera rotation, three DOF, cannot shear. Seeded RNG
  everywhere. Confidence is multiplicative, so any single factor can veto.
- **Beyond the limit means refuse, not trim** (`--clamp-beyond-limit` restores the
  old cap behaviour). Magnitude test, not semantic. *Exception, measured and
  deliberate:* a **pure-yaw** breach warns and applies capped rather than refusing
  the whole correction (`pipeline.py:208-218`) — P9 measured that refusing whole
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
  (`gui.py:4084`, `INK.update(new)`). It outlives the `App` that changed it. Any
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
`--detector mlsd|hybrid|union` run there directly. Reinstall with
`pip install --no-deps ai-edge-litert`.

## Ledger — the only place status lives

**Suite, measured 2026-09-20 at `fa88c86`+: `--full` = 328 tests, 0 failed,
2 skipped, 65.4 s. The suite is green for the first time in this file's history.** Fast run
= 283 in ~19 s and is not the suite. **And the honest one:
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

1. **Tool icons as one coherent set; bigger buttons; typography and spacing.**
   The window is judged by this. Do it as a **single pass** — icons, font sizes,
   button sizes and spacing share a visual language and will not match if split
   across sessions. Partially advanced already (button padding, type scale and
   control sizes were enlarged 09-14) but the icon set itself is untouched.
2. **More tools — only where a gesture is already being done the long way.**
   Not a wish for its own sake. Shortcuts are done (`m` mark, `b` brush,
   `p` planar, `s` SAM).
3. **P4 — two-facade warning in the status area.** Small, diagnostic. The status
   label now exists (`lbl_status`), so this is only the detection plus a line.
4. **P3 — show the multiple horizontal VPs as markers.** Cosmetic, and it
   overlaps the found-geometry overlay research goal below; decide which one is
   being built before building either.

**B. Correctness and infrastructure.**

5. **CI runs the fast suite only** (`.github/workflows/tests.yml:33`). It has
   never executed `test_gui` or `test_assets`, so **"CI is green" has never been
   a statement about the GUI or the photographs.** Now that `--full` completes in
   ~64 s there is no longer a runtime argument against it, but the obvious change
   is **not** safely blind:
   - `test_gui` opens real Tk windows at `geometry("<WxH>-4000+0")` — off-screen
     by design. Whether a runner's single virtual display tolerates a negative
     off-screen origin is **unverified**, and cannot be verified from here.
   - On Linux it additionally needs a display at all (`xvfb-run`), and the Linux
     job is already failing for an unrelated and undiagnosed reason.
   - A runner has 2 cores; the ~64 s here is on 32, and the runner would largely
     serialise.
   **The narrow, defensible version is a windows-only `--full` job**, since
   Windows CI passes today and is where the GUI tests are developed. Do not
   record it as done without a green run — a CI change cannot be tested locally,
   which is exactly why this one has to be made deliberately rather than
   assumed.
6. **CI Linux is failing** (ubuntu-latest, 3.9 and 3.12; Windows passing) —
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
     `rectify.py --doctor` exit 2 at CI step 2. Version-independent and
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
7. **`gui.py` is 5217 lines** — 3.5× the next largest file (`review.py`, 1369).
   A 12-file composition split was proposed and **deprioritised by the user**. It
   stays deprioritised; recorded so it is not re-proposed as if new.
8. **`cli.py:651` `isatty()` gate** blocks a piped double-click launch. MED,
   minor, unchanged.

**C. Decide, do not necessarily fix.**

9. **Shootout suite** — a 20-image benchmark. Estimated 9–10 h, independent of
   everything else, no `tests/shootout/` exists.

**D. Research — measurement passes, explicitly not implementation packages.**

10. **Distortion Stages 1–3.** Stage 0 (`lensfunpy`, EXIF-driven) shipped
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
11. **Four research goals in `knowledge.md`** — read that file before picking any
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

### Done — rely on these

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
  straighten both facades at once — expected, not a bug. "horizontal auto (yaw)"
  and "horizontal marker (manuell)" are mutually exclusive in Q4. PC Rectangle is
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
- `_after_dims` shows the live crop size; menus themed via `TMenu` instead of the
  OS default black.
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
perfect-cross UI (closed decision — do not propose layout changes) · Hough
detector removed · M-LSD unblocked in the GUI interpreter.

### Repo note (measured 2026-09-20)

- `D:\Coding\Perspective-Correction` is the live checkout. **`main` and
  `origin/main` are identical** — 0 ahead, 0 behind. Everything is pushed. The
  old note here claimed 5 unpushed commits and "a full day of work on one disk";
  that is no longer true and was the kind of stale alarm that wastes a session.
- Working tree carries an **uncommitted asset-pool change**: 13 tracked files
  deleted (12 photographs plus `camden-gfx100s-16mm.jpg`) and ~15 new
  photographs untracked in `tests/assets/Horizontal/`, plus
  `tests/assets/Synthetic/`, `tools/render_synth.py` and three new
  `tests/assets/sam2_masks/` PNGs. This is deliberate curation by the user, not
  drift. **Any pool-wide number measured before it is committed is measured
  against a different pool than any number recorded earlier in this file.**
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
- Caches key on the **full** argument tuple; hit-check is `key in cache`, never
  truthiness (`None` is a real result).
- `cv2.imread` mangles non-ASCII paths on Windows — use `masks._imread`
  (`np.fromfile` + `imdecode`).
- Synthetic scenes are right for geometric questions with ground truth, wrong for
  statistical ones about real texture.
- **Tested is not reachable.** A seam with tests is half a feature; "done" means
  someone can use it. Check for the caller before writing a Done entry. SAM drew
  an outline and never reached the estimator for weeks while looking wired.
- **Deleting one function strands the next.** After any deletion, sweep every
  function in `src/pc` for references across src, tests and tools.

## Worker (local Qwen, via `tools/worker_agent.py`)

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
- `debug.md` — the audit findings list. **Large (1383 lines): send questions
  about it to a subagent rather than reading it into context.**
- `docs/claude-md-overhaul-plan.md` — historical; superseded by this file and
  `knowledge.md`, kept only for the Stage 4 plumb-line detail.
- `docs/qwen-knowledge.md` — history, not instruction: why the worker was once
  shelved. The Worker section above supersedes it.
