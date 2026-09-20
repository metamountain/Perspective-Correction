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
  | `python tests/run_tests.py` | fast — skips `test_gui`, `test_assets` | **290 tests, 0 failed, ~19 s** |
  | `python tests/run_tests.py --full` (or `PC_FULL=1`) | everything, 23 modules | **337 tests, 0 failed, 2 skipped, ~67 s** |

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

**Suite, measured 2026-09-20 at `a6d1aaa`+: `--full` = 337 tests, 0 failed,
2 skipped, 67.0 s. The suite is green for the first time in this file's history.** Fast run
= 290 in ~19 s and is not the suite. **And the honest one:
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
2. **More tools — only where a gesture is already being done the long way.**
   Not a wish for its own sake. Shortcuts are done (`m` mark, `b` brush,
   `p` planar, `s` SAM).
3. **P4 — two-facade warning.** **The detection half was measured 2026-09-20 and
   does not work; nothing was built.** See the Ledger. It is now **blocked on
   `knowledge.md` §1 (facade-outline-first)**, not on effort: a threshold over
   `ArchitectureScheme`'s post-hoc split cannot represent the case, because the
   split itself under-detects the second plane.
4. **P3 — show the multiple horizontal VPs as markers.** Cosmetic, and it
   overlaps the found-geometry overlay research goal below; decide which one is
   being built before building either.

**B. Correctness and infrastructure.**

5. **CI's `full-windows` job exists but has never run — watch its first run.**
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

7. ~~**Two agent passes cut off by a rate limit.**~~ **Both finished; closed
   2026-09-20.** Kept as a title so the next session does not re-open it:
   neither had left a half-edited source file (checked at the time — only the
   one test each had completed), the non-GUI findings were adjudicated by hand
   and the `knowledge.md` audit was re-run. **Both outcomes are in Done, and
   the second one needed correcting afterwards** — the audit stamped a wrong
   support figure as verified, which is the entry worth reading.

8. **`gui.py` is 5217 lines** — 3.5× the next largest file (`review.py`, 1369).
   A 12-file composition split was proposed and **deprioritised by the user**. It
   stays deprioritised; recorded so it is not re-proposed as if new.
9. ~~**`cli.py` `isatty()` gate**~~ **Struck 2026-09-20: it was not a defect.**
   See the Ledger — the gate refuses rather than proceeding, which is the only
   safe answer for a flag that destroys originals, and `--yes` is the documented
   way to mean it non-interactively.

**C. Decide, do not necessarily fix.**

10. **Shootout suite** — a 20-image benchmark. Estimated 9–10 h, independent of
   everything else, no `tests/shootout/` exists.

**D. Research — measurement passes, explicitly not implementation packages.**

11. **Distortion Stages 1–3.** Stage 0 (`lensfunpy`, EXIF-driven) shipped
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
12. **Four research goals in `knowledge.md`** — read that file before picking any
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
  resolution the estimator actually runs it is **`h1=0.596, h2=0.147`**. §1's
  headline comparison is therefore 0.045 against **0.147**, a 3.3x gap and not
  the 8x it reads as. The direction of its argument survives; the margin is
  narrower than it has been claiming.
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
perfect-cross UI (closed decision — do not propose layout changes) · Hough
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
