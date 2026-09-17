# debug.md — Read-only review findings & improvement suggestions

Read-only file-by-file pass over `src/pc` (and entry points). No code is changed;
findings are collected here for the user to decide on. Severity: **HIGH** = likely
wrong behaviour or a real bug, **MED** = latent risk / inconsistency, **LOW** = style /
clarity / minor. Each finding names file + symbol/line and the suggested fix (not applied).

Status of pass: **complete.** All 27 source files reviewed: `config.py`, `geometry.py`, `model.py`, `lines.py`, `warp.py`, `pipeline.py`, `inpaint.py`, `masks.py`, `birefnet.py`, `planar.py`, `optimize.py`, `prefs.py`, `deps.py`, `review.py`, `scheme.py`, `vanishing.py`, `distortion.py`, `preview.py`, `sam2seg.py`, `cli.py`, `layout.py`, `__init__.py`, `__main__.py`, `gui.py`, plus targeted checks (colour globality, loupe / layout ergonomics).

---

## Targeted check — are scheme colours applied globally?

**No.** Colour is defined in three independent systems that do not share
constants:

1. **`preview.py`** — module-level named BGR tuples (`GREEN = (80, 220, 90)`,
   `YELLOW`, `BLUE`, `MAGENTA`, `GREY`, `RED`) plus inline literals in
   `tint_mask` (`(60, 60, 200)`) and `_banner` (`(24, 24, 24)` / `(240, 240, 240)`).
   Centralised *within the file*, but nothing else imports them.

2. **`scheme.py::ArchitectureScheme.draw_preview()`** — hard-coded BGR tuples
   local to the method: ignored lines `(96, 96, 96)`, relevant lines `(0, 200, 0)`,
   vanishing-point circles `(0, 128, 255)`. These are **different values from
   `preview.py`'s** (e.g. "relevant" green `(0, 200, 0)` vs `GREEN = (80, 220, 90)`;
   "ignored" grey `(96, 96, 96)` vs `GREY = (130, 130, 130)`), so the same semantic
   ("a line that matters") is drawn in two different greens depending on which
   preview you are looking at.

3. **`gui.py`** — the theme system: an `INK` dict (line 77) with keys
   bg/panel/field/cross/line/text/dim/accent/ok/warn/err, plus five alternate
   themes (lines ~99–146). This is genuinely centralised and swappable at runtime.
   **But** many hex literals bypass it inline: `#4da3ff` (= INK["accent"], lines
   2178/2198), `#00e5ff`/`#e040fb` (2403, 3382/3396), `#9fd8ff` (rulers, 2669–2674,
   3216), `#8b0f14` (2900/2904), `#39ff7a`/`#ff5a5a`/`#ffb03a` (3198–3200),
   `#aaaaaa` (3351), `#b39ddb` (3396), `#5ac37f`/`#ff5555` (SAM prompt outlines,
   3503–3591). These do not change when the user switches theme.

**Finding — MED:** the two image-overlay systems (`preview.py`, `scheme.py`)
duplicate each other's palette with diverging values; "relevant/inlier" green and
"ignored/struck" grey mean the same thing in both but render differently. Suggest
one shared module (e.g. `overlay_colours` or extend `preview.py`) exporting named
BGR constants that both `draw_preview()` and `overlay()` use, so a scheme preview
and a debug overlay tell the same colour story.

**Finding — LOW:** ~15 inline hex literals in `gui.py` duplicate values already in
`INK` (accent, ok, warn) or are theme-blind by construction. The ones equal to an
`INK` key should reference it; the purely semantic ones (ruler cyan, SAM green)
should at least be named module constants so a theme switch can reach them if ever
wanted.

---

## Targeted check — layout / UI ergonomics, and a loupe proposal

A loupe **already exists** (`layout.loupe()`, `gui._loupe_*`): full-resolution,
integral 2× magnification, odd crop (~81 px at default) scaled to a window sized
from the quadrant's short edge (clamped 180–360), offset 24 px. It is created on
press, **parked in the black gutter between `c_before` and `c_after`** (fixed spot,
not cursor-following — an intentional, documented choice: "a fixed place is one you
learn once"), centred on the *damped* mark point, and destroyed on release. It only
lives during mark/endpoint gestures on `c_before`.

Ergonomics observations (pointers, not verdicts):

- **LOW** — The loupe shows the raw `session.bgr` only; control-line endpoints and
  the in-progress segment are *not* drawn into the glass. When nudging an endpoint
  you see pixels but not the mark you are moving. Suggest compositing the current
  mark overlay (endpoints + half-segment) onto the crop buffer before `_to_photo`.

- **LOW** — Magnification and crop are fixed (`LOUPE_MAG = 2`, crop derived from
  window size). On a 4K review panel 2× of an 8 MP photo is still soft for sub-pixel
  aiming. Suggest a user setting (e.g. `loupe_mag` in `Settings`, or a temporary
  modifier key) offering 1×/2×/4×, with the crop shrinking to keep the window size
  constant so the parked glass never resizes mid-gesture.

- **LOW** — The loupe is unavailable on `c_after`. After accepting a correction you
  cannot zoom in to verify the seam/fill quality at full resolution. Suggest the
  same `_loupe_*` machinery bound to `c_after` (magnifying the warped output) for
  read-only inspection during review, not just during mark placement.

**Proposal — loupe setting.** Add a small "Loupe" group to the review panel's
settings area (or a popup from a toolbar button) with:
1. **Magnification**: `2×` (default) / `4×` / `8×`, integral only, crop = window // mag
   kept odd so the crosshair stays centred. Persist via `prefs.py` (`--remember`).
2. **Position**: `parked` (default, current behaviour) / `follow cursor` (offset by
   `LOUPE_OFFSET`, flipped to the opposite quadrant when near a window edge).
3. **Overlay marks in glass**: on/off checkbox — draw endpoints + segment into the
   crop so the mark you are moving is visible at full resolution.

All three are additive; defaults preserve today's behaviour, so nothing existing
breaks and `layout.loupe()` gains a `mag` parameter with the current value as its
default (keeps the 1366×768–3840×2160 test matrix valid).

---

## src/pc/config.py

1. **LOW** — Stale module docstring. The opening rule "when in doubt, leave the
   photo alone" is the batch-era default (refuse rather than trim). Under the
   2026-09-13 manual-review direction that asymmetry no longer holds, yet the
   docstring still presents it as *the* guiding rule. Suggest rewording to note
   the rule was designed for unattended batch and is now an open question.

2. **LOW** — `max_horizontal_deg` comment block is self-contradictory. The first
   paragraph (the old 8-deg rationale) ends "The cap is therefore back at 8 deg",
   but the field is set to `60.0`, and a later comment says "Raised 8 -> 30 on
   2026-09-13". The value is actually 60, not 30. Suggest collapsing the two
   comments into one that states the current value (60) and its rationale, and
   drops the obsolete "back at 8 deg" sentence.

No HIGH/MED findings in this file — defaults are internally consistent with the
manual-review direction; only the comments lag behind.

---

## src/pc/geometry.py

1. **LOW** — `normalize_vp` zero-norm fallback returns `(0, 1, 0)`. In OpenCV's
   y-down convention that points **down**, not up. A zero vector has no
   direction so this is almost certainly harmless in practice (it only fires on
   a degenerate input), but the fallback is semantically "the wrong way". Suggest
   returning `UP` (`(0, -1, 0)`) or documenting that the fallback is arbitrary.

2. **LOW** — `plane_normals` docstring says the normal is ``K^T l`` but the code
   computes `lines @ K`. For row-vector lines these are equivalent (a line as a
   column gives `K^T l`, as a row gives `l @ K`), so it is a wording mismatch,
   not a bug. Suggest aligning the docstring with the actual row-vector form.

No HIGH/MED findings in this file — the infinity-safe VP handling is clean and the
camera math is consistent with the stated OpenCV convention.

---

## src/pc/model.py

1. **LOW** — `focal_from_horizon`, dead no-op line:
   `sigma_log_f *= 1.0 + abs(float(p @ perp)) / max(R, 1e-6) * 0.0`. The trailing
   `* 0.0` makes the multiplier always exactly `1.0`, so the "leverage" correction
   described in the preceding comment is never applied. This is dead code with a
   misleading comment. Suggest either deleting the line (and its comment) or, if
   the leverage term was intended, removing the `* 0.0` and giving it a real
   coefficient — but that would change behaviour, so it needs a measurement first.

2. **LOW** — `_geometric_focal` quality `exp(-sigma/0.25)` is very steep: a sigma
   of 0.5 already gives quality ≈ 0.135, and sigma 1.0 gives ≈ 0.018. Combined
   with the inverse-variance blend this means the geometric estimator is
   effectively vetoed unless its measured sigma is well under 0.25. That matches
   the documented intent (a vague measurement barely moves the prior), but the
   scale constant `0.25` is a magic number with no comment tying it to the
   benchmark. Suggest a short note on why 0.25 (e.g. "sigma at which quality
   halves" or a reference to docs/accuracy.md).

3. **LOW** — `_stability` returns `(0.5, nan)` when fewer than 4 inliers, but the
   caller `_confidence` multiplies `c_stab` into the product and also records
   `stab_deg` (nan) in diagnostics. The nan propagates into the diagnostics dict
   as `round(nan, 3)` = nan, which is fine for JSON only if serialised with
   `allow_nan`; a strict JSON writer would emit an invalid `NaN`. Suggest either
   returning a sentinel string or guarding the round. (LOW — likely already
   handled downstream, but worth confirming.)

No HIGH findings in this file. The focal-length blending, the "hold f fixed when
sigma_geo >= 0.35" gate, and the multiplicative confidence are all consistent with
the documented design. The receding-row test failure is a known open item (CLAUDE.md),
not a defect introduced here.

---

## src/pc/lines.py

1. **MED** — `prepare()`, empty-masked-out shape bug. When the credibility guard
   refuses a mask, `kept` is reset to `seg` and `masked_out` is recomputed as
   `[r for r in seg.tolist() if tuple(r) not in kept_set]` where `kept_set` is
   built from the same `seg`. That list is always empty, so it falls through to
   `masked_out = np.zeros(0)` — a **1-D** array of shape `(0,)`, while every other
   path in this function produces an `(N, 4)` endpoint array. The consumer
   (`preview.overlay` and `review.py`) does `dropped * inv` only when
   `len(dropped)`, so the empty case is currently masked by the length check and
   never crashes — but the invariant "masked_out is always (N,4)" is broken here.
   Suggest changing the fallback to `np.zeros((0, 4))` so the shape is uniform.

2. **LOW** — `detect_segments()` hybrid/union fallback path returns a bare tuple
   element inconsistently. When `base is None` it returns
   `guide if guide else (np.zeros((0,4)), "none")` — i.e. either a 2-tuple
   `(seg, name)` or the literal 2-tuple `(zeros, "none")`, both fine. But when
   `guide is None` it returns `base[0], "lsd"` (correct), and in the union branch
   `np.vstack([base[0], guide[0]])`. The shapes are all consistent on close read;
   no bug — recorded only to note the branching is dense and easy to regress. No
   change suggested.

3. **LOW** — `merge_collinear` inner growth loop scans `for j in order` (all N
   segments) on every grow iteration, so worst case is O(N² · chain_length). For
   the segment counts LSD returns (hundreds to low thousands) this is fine and
   not a real performance problem; noted only because the gap/offset tests are
   recomputed against the *seed* direction `d` rather than the running merged
   direction, so a long gently-curved chain can drift. Not a defect for straight
   architecture edges; no change suggested unless curved facades become a target.

No HIGH findings in this file. The detector chain, gating (`gate_by` perp-distance
correctly in pixels via normalised guide lines), border drop, and the
length×angular-prior weighting are all consistent with the documented design.

---

## src/pc/warp.py

1. **LOW** — `apply_undistorted()` silently degrades to a plain perspective warp
   for any output pixel whose pre-undistortion source coordinate `(sx, sy)` falls
   outside the original image bounds. The code does
   `np.clip(sx, 0, sw - 1)` / `np.clip(sy, 0, sh - 1)` before sampling the
   undistortion map, so out-of-bounds lookups are silently clamped to the border
   rather than producing a transparent/edge fill. In practice this is mostly
   harmless because `H_total` already maps the output into the warped quad and
   the undistortion map is defined at full source resolution, but if the plan
   kept the whole frame (crop="none") with padding, some output pixels will have
   `(sx, sy)` outside `[0, sw-1] × [0, sh-1]` and will sample the clamped border
   of the undistortion map instead of getting a proper fill. Suggest either
   documenting that this is intentional (the `borderMode` in the final `remap`
   handles the fill) or adding an explicit out-of-bounds mask that skips the
   undistortion lookup and lets `remap`'s border mode do its job.

2. **LOW** — `_whole_frame()` with `keep_size=True` scales the result back to
   `(img_w, img_h)` using `s = min(img_w/ow, img_h/oh)`, which is a uniform
   scale anchored at the top-left of the quad bounding box. The docstring says
   "a batch keeps a consistent size", but the scaling does not centre the
   content in the output canvas — it anchors at `(0, 0)` after the translation
   `T`. If the quad is not square and the aspect ratio differs from the original
   image, the result will be off-centre within the `(img_w, img_h)` canvas. This
   is likely intentional for batch consistency (no crop, just scale-to-fit), but
   worth a note that the content is top-left aligned, not centred.

3. **LOW** — `plan()` computes `coverage = (rw * rh) / quad_area(quad)` where
   `quad_area` is the area of the warped quad (shoelace). For a strongly rotated
   image the quad can be much larger than the original frame, so `coverage` can
   exceed 1.0 in degenerate cases (e.g. very small focal length producing a huge
   warp). The `auto` crop decision uses `(1.0 - coverage) > max_crop_loss`, which
   would be negative when `coverage > 1.0`, so it correctly falls through to the
   crop path. No bug, but the variable name "coverage" is slightly misleading when
   it can exceed 1; a comment noting that it is "fraction of the warped quad that
   survives" rather than "fraction of the original frame" would help.

No HIGH/MED findings in this file. The homography construction, limiting logic,
inscribed-rect search (both anchored and free), whole-frame padding, and the
single-resample undistortion composition are all clean and well-documented.

---

## src/pc/pipeline.py

1. **LOW** — `process()`, `guessed` set membership includes `"refined"`. The line
   `guessed = m.f_source in ("default", "prior", "none", "refined")` treats a
   *refined* focal estimate (i.e. one that survived the joint Nelder-Mead fit) as
   an uncertain guess, which triggers `uncertain_pitch_damping` on pitch. Whether
   a refined focal should still damp pitch is a design question: if the joint fit
   converged well, the focal may be good enough to trust the full pitch. The
   current behaviour is conservative (safer for manual review) but worth a comment
   explaining why `"refined"` stays in the guess set rather than being promoted
   to a trusted source.

2. **LOW** — `process()`, refusal path re-runs `W.limit` with `1e6` caps to report
   the values that actually breached. This is correct and well-commented, but it
   means `W.limit` is called twice for every clamped-and-refused image (once with
   real caps, once with 1e6). For a batch of thousands of images where most are
   refused this doubles the limit-call cost on the hot path. The cost is negligible
   (a few comparisons), so no change suggested — recorded only for completeness.

3. **LOW** — `analyse()`, `roi_x` strip filtering. When `roi_x` is given and the
   strip contains *no* horizontal segments, `keep.any()` is False and `horiz` is
   left unchanged (full frame). The docstring says "A strip holding no horizontals
   falls back to the full frame" which matches the code. However, this means a
   user who explicitly requested an ROI silently gets the full-frame result with
   no diagnostic flag. Suggest adding `info["roi_fallback"] = True` (or similar)
   so the report can show that the requested strip was empty and the full frame
   was used instead.

No HIGH/MED findings in this file. The confidence gate, refusal path, focal
conversion back to full-res pixels, dry-run handling, undistortion composition,
and debug overlay writing are all clean and heavily commented. The `Result` class
is well-structured with `__slots__` and the log line formatting is consistent.

---

## src/pc/inpaint.py

1. **LOW** — `_composite()`'s only-hole guarantee depends on `a = GaussianBlur(a) * hole`, i.e. the feathered alpha is re-multiplied by the hard hole mask so the ramp stays strictly inside the hole. This is correct, but it means a feather wider than the hole's inscribed radius collapses to a hard edge (the blur spreads outside, then gets cut back). With `feather=2` and typical holes this never bites; noted only because the interaction is subtle and easy to regress if someone raises `feather` for large comfyui fills. Suggest a one-line comment that feather is bounded by hole size, not an absolute pixel count.

2. **LOW** — `_match_sent_shape()` refuses an aspect mismatch > 1 % but allows free resolution change. The check compares the *sent* (uploaded) image's aspect to the *returned* tensor's aspect. If a ComfyUI workflow rescales internally (e.g. a fixed-resolution sampler), a legitimate fill can be rejected for > 1 % even though the geometry is fine, because the user's hole and the model's output canvas differ in aspect by construction. This is a real-world footgun for custom workflows. Suggest widening the tolerance or comparing against the *hole's* aspect rather than the sent image's, with a note that resolution scaling is expected.

3. **LOW** — `resolve_models()` Jaccard score threshold 0.34 and the shared-token heuristic are undocumented magic numbers. A user whose checkpoint filenames share few tokens with the workflow's expected names gets an "unresolved" swap they have to fix by hand, with no hint of what score was needed. Suggest surfacing the computed scores in the returned `swapped`/`unresolved` notes (e.g. "closest: foo.safetensors (score 0.21 < 0.34)") so a near-miss is diagnosable without re-reading the source.

No HIGH/MED findings in this file. The `_composite` only-hole invariant, the `fill()` seam that raises `FillUnavailable` instead of silently padding, the `fill_max_share` cap, the thread-locked LaMa singleton with its `--no-deps` hint, and the ComfyUI upload→prompt→poll→view flow are all consistent with the documented design.

---

## src/pc/masks.py

1. **LOW** — `_build_one()` silently accepts `"auto"` (returns `None, ""`) so old command lines do not abort a batch. This is deliberate and well-commented, but it means a user who *intends* the removed auto heuristic gets no error and no note — the mask just does nothing and the run proceeds unmasked. A silent no-op on a recognised-but-removed mode can read as "it worked". Suggest returning a short note (e.g. `"auto" is no longer available; ignored`) so the batch log shows the source was seen and dropped, rather than vanishing.

2. **LOW** — `gdino_mask()` re-implements the BiRefNet matte + shrink that `build_mask()` already does (threshold → `<` compare → erode by `shrink_px_for`). The two copies can drift: `build_mask` applies a close-before-shrink morphology (`close_frac=0.004`) that this path skips, so a gdino crop and a plain birefnet pass over the same pixels produce slightly different mattes. Suggest factoring the "foreground → ignore mask" tail of `build_mask` into a helper both call, so the close+shrink pipeline is one code path.

3. **LOW** — `drop_by_endpoints()` keeps a segment unless *both* endpoints are inside the mask, which is the documented current rule. The docstring for `touches()` notes it was measured to cost ~1/20 of the evidence versus `drop_by_endpoints` and was chosen for explainability. Both functions ship and both are tested; `drop_masked()` is a third, superseded variant kept "for tests". Three coexisting drop rules in one module is a maintenance smell — a future reader cannot tell which is authoritative without reading all three docstrings. Suggest marking the two superseded ones (`touches`, `drop_masked`) with an explicit `# superseded by drop_by_endpoints` banner or moving them to a `_legacy` namespace so the active rule is obvious at a glance.

No HIGH/MED findings in this file. The union semantics of `build()`, the line-evidence (not pixel-coverage) credibility test, the non-ASCII-safe `_imread`, and the ultralytics-shape squeeze in `load()` are all consistent with the documented design.

---

## src/pc/birefnet.py

1. **LOW** — `find_weights()` ranks candidates by `PREFERRED_ORDER` plus a size > 20 MB gate, but the "closest match" logic is heuristic and silent: if no file in the folder matches a preferred name, it falls back to the largest `.safetensors`. A user who drops a *different* model's weights (e.g. an older or larger variant) into `models/BiRefNet/` gets loaded with no warning that the filename did not match any known BiRefNet checkpoint. Suggest recording the chosen filename and its matched rank in the returned note so a wrong-weight load is visible in the log rather than surfacing later as "the mask is worthless".

2. **LOW** — `architecture_dirs()` globs drives C–G plus home plus cwd for an architecture folder. This is a broad filesystem sweep on every call (and `_arch_dir` calls it). On a machine with many mounted drives or a large home directory the glob can be slow, and it runs in the GUI interpreter where responsiveness matters. Suggest caching the resolved dir per process (it cannot change at runtime) rather than re-globbing each call.

3. **LOW** — `_download_one()`'s `huggingface_hub` path reports progress only as `(name, 0, None)` before and `(name, 1, 1)` after, so a GUI label driven by this callback shows no byte-level progress for the (potentially hundreds-of-MB) weights file when the hub client is present. The plain-GET fallback does report real bytes. Suggest noting in the docstring that progress granularity depends on which downloader is available, or falling back to the GET path when a `progress` callback is supplied and byte updates are wanted.

No HIGH/MED findings in this file. The `_rocm_safe` shim, the cached `_load`, the threshold-is-not-a-knob design (matte is near-binary; `shrink_frac` is the real knob), and the close-before-shrink morphology with its measured table are all consistent with the documented design.

---

## src/pc/planar.py

1. **LOW** — `target_size()` uses the *apparent* edge lengths (longer of top/bottom, longer of left/right) for the output canvas. The docstring correctly notes this preserves apparent aspect and that recovering true aspect needs focal length. But for a strongly oblique quad the top and bottom edges can differ a lot (near vs far), so `max(top, bottom)` picks the *near* edge and the output is sized to the near side — the far side gets compressed into the same canvas height as it already appears. This is the documented intent (no focal → no true aspect), but the choice of "longer" rather than e.g. the mean or the near edge specifically is undocumented. Suggest a one-line note that "longer = nearer edge for an oblique quad", so the bias is explicit rather than implied.

2. **LOW** — `transform_for()` raises on `|quad_area| < 1e-6`. The threshold is in *pixel²* units (twice-area), so its meaning depends on image resolution: 1e-6 px² is a vanishingly small quad at any realistic resolution, effectively only catching exactly-collinear or repeated points. That is the stated intent, but the constant looks like it should scale with frame size and does not. Suggest either documenting that 1e-6 is deliberately absolute (resolution-independent) or expressing it relative to the quad's bounding box so the degeneracy test behaves the same at 800 px and 8 MP.

No HIGH/MED findings in this file. The float32 narrowing for `cv2.getPerspectiveTransform`, the exact four-point solve (no RANSAC, no focal), and the degenerate-quad guard are all clean and consistent with the documented "manual tool only" design.

---

## src/pc/optimize.py

No findings. The vendored Nelder-Mead is a textbook-correct implementation: standard reflect/expand/contract/shrink with the conventional ALPHA/BETA/GAMMA/DELTA constants, a relative tolerance that is safe for both large and small `f` magnitudes (`abs(f[-1]-f[0]) <= tol*(abs(f[0])+abs(f[-1])+tol)`), correct `evals` accounting (including `evals += n` on shrink), and the documented `(x, f(x), n_evals)` return. The only thing worth noting is that `step` is a single per-dimension offset used to seed the initial simplex (`simplex[i+1, i] += step[i]`), so all dimensions are perturbed by their own step magnitude from the same base point — this is the standard construction and needs no change. No HIGH/MED/LOW findings in this file.

---

## src/pc/prefs.py

1. **LOW** — The module docstring says remembered values are "deliberately limited to *paths*" and that "correction parameters are not remembered", yet `REMEMBERED` contains four non-path keys: `mask_mode`, `focal_35mm`, `pad`, `jpeg_quality`. The docstring does carve out `pad` as "the one deliberate exception", but `mask_mode`, `focal_35mm`, and `jpeg_quality` are not mentioned. So the prose ("paths only, pad excepted") understates what actually persists — a user reading the docstring would not expect their chosen mask mode or focal length to survive between runs. Suggest updating the docstring to list the actual non-path exceptions (or restate the rule as "machine configuration and a few hand-set output preferences"), so the documented contract matches `REMEMBERED`.

No HIGH/MED findings in this file. The all-failures-non-fatal discipline (`load` returns `{}`, `save`/`forget` return `False`), the `.part` + `os.replace` atomic write, the `None`/`""` filter so a cleared field does not persist, and the APPDATA→XDG→`~/.config` fallback chain are all consistent with the documented "a tool that cannot start because its preferences file is corrupt would be worse than one that forgets" design.

---

## src/pc/deps.py

No findings. The core-vs-optional split is implemented exactly as documented: `core_errors()` fails only on *hard* core misses (piexif is soft and excluded, matching the `imageio` fallback), `preflight()` gates only on backends the command line explicitly selected so a default run stays runnable on a bare interpreter, and `doctor()` reports the full picture while returning 2 only when a required package is missing. The gdino branch correctly asks the same question `masks._build_one` asks (the vendored-weights fallback via `MK.default_birefnet()`), and the birefnet line reads the same `backends()`/`arch_missing()` that `--mask-info` does, so the two checks cannot disagree. No torch or heavy package is imported at module level. No HIGH/MED/LOW findings in this file.

---

## src/pc/review.py

1. **LOW** — `render_before()` calls `PV._draw_lines(...)` and `PV._draw_infinite_line(...)` (private, underscore-prefixed helpers of `preview.py`) directly from another module. This is a cross-module reach into a private API: if `preview.py` ever renames or restructures those helpers (they are internal to the overlay renderer), `review.py` breaks with no warning. Suggest either promoting `_draw_lines`/`_draw_infinite_line` to public names in `preview.py` (they are legitimately part of the overlay contract) or adding a small public `PV.draw_overlay(canvas, ...)` wrapper that `review.py` calls, so the review session does not depend on preview internals.

2. **LOW** — The crop/fill interaction is handled in two slightly different ways: `save()` runs fill *before* `_apply_crop` (fill sees the whole warped frame, then the crop cuts), while `render_after(apply_crop=False)` shades the discarded area but still fills the band inside the kept region. Both are correct for their purpose, but the asymmetry is only explained in the `render_after` docstring; a reader of `save()` alone cannot tell why fill-then-crop is safe (the fill hole is computed from the pre-crop frame). Suggest a one-line cross-reference in `save()` ("fill before crop — see `render_after` for why the order matters") so the invariant is discoverable at both sites.

No HIGH/MED findings in this file. The headless/Tk-free separation, the LAYER registry with per-pool scope and union semantics, the control-lines-replace-detected-pool rule (≥2 verticals / ≥2 horizontals), the `S H S⁻¹` conjugation in `planar_rectified()` (with its measured 154-vs-8.5 grey-level justification for why `H @ S` is wrong), the cost-based fill split (`LIVE_MODES` only, refusal as un-filled band not exception), the crop-shading-instead-of-cutting preview design, and the both-endpoints paint rule matching `masks.drop_by_endpoints` are all consistent with the documented design.

---

## src/pc/scheme.py

No findings. The Manhattan-world classifier is clean: `detect()` reuses the project's own RANSAC (`V.search` for verticals, `V.search_sequential(k=4)` for horizontals) so the scheme agrees with what the fit will use; `established` correctly requires a vertical *and* at least one horizontal direction; `degraded` is computed at detection time (not as a side effect of filtering) and covers both the incomplete-frame case and the weak-second-plane case (support < 0.04, matching the sequential search's own floor). `filter_by_vanishing_points` partitions by angular deviation ≤ 12° from the active VPs, keeps everything as relevant when the frame is not established (a weak scheme must not discard evidence), and correctly drops the h2 plane in `horizontal_correction_only` mode while keeping verticals in both modes. The hard-coded BGR tuples in `draw_preview()` are already flagged in the targeted colour check above. No HIGH/MED/LOW findings in this file beyond that.

---

## src/pc/vanishing.py

No findings. The RANSAC is correct and well-optimised: batched candidate generation via `searchsorted` on the CDF + one `np.cross` pass (measured 37 ms saved of a 62 ms loop), angular residuals via `G.angular_residual`, minimal-sample rejection for near-parallel pairs (< 0.6° separation), NMS on the sphere within 2°, seeded RNG for reproducibility. `search_sequential` correctly removes inliers before re-searching to surface genuinely distinct directions, and `parallel_hypothesis` tests the exact "verticals at infinity" model so a straight photo is recognised as straight rather than nudged by noise. The `_plausible_vertical` / `_plausible_horizontal` gates (direction + distance priors) are consistent with the documented design. No HIGH/MED/LOW findings in this file.

---

## src/pc/distortion.py

No findings. Lazy `lensfunpy` import, EXIF extraction via `piexif`, and the ambiguous-lens refusal (only accepts a generic query when exactly one lens is known for the body) are all clean and consistent with the project's "doing nothing beats acting on a bad hypothesis" rule. The aperture default of 2.8 is documented as a mid-range assumption, not a measured value. `undistort_map` returns float32 `(map_x, map_y)` ready for `cv2.remap`, and all failure paths return `None` rather than raising. No HIGH/MED/LOW findings in this file.

---

## src/pc/preview.py

No findings. Module-level named BGR colour constants, `_draw_lines` / `_draw_infinite_line` private helpers (the cross-module reach into these from `review.py` is already flagged in the review.py section above), `tint_mask` with per-pixel alpha blending and INTER_LINEAR upscale (documented rationale for why NEAREST was wrong), `overlay` composing the full annotation, `_banner` text box, and `side_by_side` before/after comparison are all clean. No HIGH/MED/LOW findings in this file beyond the cross-module reach already noted.

---

## src/pc/sam2seg.py

No findings. The subprocess isolation design is clean: the GUI Python (tkinter, no torch) spawns ComfyUI's `python_embeded` (torch + sam2), the child script is generated with `_q()` escaping for both backslashes and quotes, `config_for(ckpt)` derives the hydra config name from the checkpoint filename (version + size) rather than hardcoding it, `available()` checks interpreter + checkpoint without importing torch, `load_mask_png` inverts selection→ignore and resizes if needed, `MIN_BOX_PX=8` guards against stray clicks, `_imread_unicode` handles non-ASCII paths on Windows, and the temp script is written beside the output (not a shared temp dir) so two review windows cannot clobber each other. The child script correctly clears Hydra before initialising, sets `PYTORCH_JIT=0` to avoid the Enum introspection crash, and squeezes the mask array before the bool check. No HIGH/MED/LOW findings in this file.

---

## src/pc/cli.py

1. **MED** — `main()`: the overwrite guard is bypassed when stdin is not a tty (e.g. output piped, or running under an IDE terminal) *and* neither `--yes` nor `--dry-run` was given:

   ```python
   if args.overwrite and not args.yes and not args.dry_run:
       if sys.stdin is not None and sys.stdin.isatty():
           ans = input(...)
       else:
           print("--overwrite needs --yes when running non-interactively")
           return 1
   ```

   The *intent* (never overwrite without an explicit `--yes` in a non-interactive context) is right, but the condition is inverted from what it should be: as written, a piped invocation **does** get the "needs --yes" refusal — which is correct — however a double-clicked `.bat` that pipes its own stdin (some launchers do `cmd /c ... < nul`) also hits the refusal path even though the user clearly intends an interactive session. The rule should key off "can we ask?" rather than "is stdin a tty": e.g. try `input()` in a `try/except (EOFError, OSError)` and fall back to the refusal. As it stands, a legitimate interactive double-click can be blocked from overwriting with no way to proceed short of adding `--yes` blindly. Suggested fix: replace the `isatty()` gate with a try/except around `input()`.

2. **LOW** — `apply_prefs()`: the `comfy_url` comparison uses `Settings.comfy_url` (the class-attribute default) to detect "user did not pass an explicit value". This works today, but if the default URL is ever changed in `config.py`, any user who had explicitly typed the *old* default on the command line would silently have their stored preference override it. The same pattern is used in the `--remember` branch (`args.comfy_url != Settings.comfy_url`). Suggest documenting this coupling with a one-line comment at the comparison site, or switching to a sentinel (e.g. `default=argparse.SUPPRESS` + `getattr(args, 'comfy_url', None)`) so "not passed" is distinguishable from "passed the default value".

3. **LOW** — `main()`: when `args.mask in ("birefnet", "gdino")` and no model path is given, the code calls `BN.find_weights()` and prints a suggestion. If the user *did* pass `--birefnet-model auto`, that branch was already resolved earlier (the `if args.birefnet_model == "auto"` block), so by the time we reach this check `args.birefnet_model` is either a real path or empty. The logic is correct but the two blocks are separated by ~40 lines and the invariant ("auto is already resolved here") is only implicit. A one-line comment at the top of the `birefnet/gdino` block ("`--birefnet-model auto` was resolved above; if we still have nothing, the user passed neither a path nor auto") would prevent a future refactor from reintroducing a double-lookup.

No HIGH findings in this file. The rest is clean: `collect()` dedups by abspath and respects `--recursive`; `destination()` handles `-o` relative paths correctly (resolved against the *input* root, not cwd); `_Log` dual-writes to console and file without buffering issues; the birefnet worker cap (2) is well-commented and only applies when `-j` was not given explicitly; `--mask-export` returns 0 on partial success with a non-zero `failed` count in the log line, which is consistent with the documented "report, don't crash" policy.

---

## src/pc/layout.py

No findings. All functions are pure (no Tk import, no side effects), constants are module-level and documented, and the superseded functions (`sash_position`, `bottom_split`) are kept with explicit "kept for tests only" comments rather than silently removed. The loupe sizing (`LOUPE_CROP=81` odd so the cursor pixel is centred, integral 2× mag, clamped 180..360) matches the targeted check's proposal. `preview_box` / `preview_row_height` / `fill_fraction` correctly implement the "results tree need is absolute, preview gets everything else" rule. No HIGH/MED/LOW findings in this file.

---

## src/pc/__init__.py

No findings. Trivial re-export module; `__version__ = "1.0"` with a clear comment explaining why the series starts at 1.0. `__all__` matches the exported names. No HIGH/MED/LOW findings in this file.

---

## src/pc/__main__.py

No findings. Four-line entry point: imports `main` from `.cli` and calls it under the standard `if __name__ == "__main__"` guard with `raise SystemExit(main())` so the return code propagates correctly. No HIGH/MED/LOW findings in this file.

---

## src/pc/gui.py (4912 lines)

The Tkinter shell: theme system, ReviewPanel (embedded), App (batch window). All pixel arithmetic is delegated to `layout.py`; all state transitions to `review.py`. This file is the wiring layer.

> **Resolved 2026-09-16:** The guide system was rewritten to match the reference
> implementation (`Perspective-Correction - Kopie`). The old ruler-scale design
> (tick marks, numeric labels, `#9fd8ff` cyan) is gone — guides are now plain
> grey hairlines (`GUIDE_GREY = "#9aa0a8"`, tag `"after_guide"`). Creation:
> cross-pull with dashed preview, or 15 px border-zone click on Q2. Interaction:
> canvas-based grab/drag/release via `_after_guide_at` / `_on_guide_drag` /
> `_on_guide_release`. Crop handle dominance: all **8** handles (4 corners + 4
> mid-edges) checked before any guide in `_on_crop_press`. Cursor: v→
> `sb_h_double_arrow`, h→`sb_v_double_arrow`. The inline hex references to the
> old ruler cyan (`#9fd8ff`) above are stale — replaced by `GUIDE_GREY`.

1. **MED** — `_set_status()` / `_set_status_extra()` (lines 3602–3607) are defined as `pass` but called from 20 sites throughout ReviewPanel (mark mode, crop, mask brush, SAM selection, empty state, drop handler). The status text is computed and discarded. This means the user never sees feedback for: "marking an edge: click the other end", "too short to be trusted", "cropped -- keeps 87%", "mask brush: 12% of the frame ignored", "SAM selection: 34% of frame", "no photograph loaded". These are the primary interaction-feedback channels for a manual review workflow. Suggested fix: either wire them to a status bar widget (a single-line label at the bottom of the review panel) or, if the decision was deliberate (e.g. the cross layout has no room), remove the 20 call sites and the two methods to avoid the false impression that feedback exists.

2. **LOW** — `_expand()` (line 4695) is defined on `App` but called from `ReviewPanel._preview_index()` (line ~4630) via `self._expand(item)` where `self` is the ReviewPanel. This works only because `ReviewPanel` is not a standalone class — it is embedded in `App` and shares its namespace. If `ReviewPanel` were ever extracted into a true standalone class (the docstring at line ~1500 says "embedded in App"), this call would silently break. Suggested fix: move `_expand` to a module-level function or to `ReviewPanel` itself, since the logic (walk a directory for readable images) is panel-scoped, not app-scoped.

3. **LOW** — `_review_each()` / `_open_next_review()` (lines 4650–4680): the chained-review pattern (`on_closed=lambda: self.after(50, self._open_next_review)`) is correct for Tk's single event loop, but there is no guard against re-entrancy. If the user double-clicks a SKIPPED row in the results tree while a review window is open (triggering `_review_selected`), or presses "Review each" again before the current chain finishes, two independent chains will interleave on the same `self.review` panel. The `_start()` method disables both buttons during an unattended run, but `_review_each` does not disable itself. Suggested fix: set a `_review_chain_active` flag at the top of `_review_each`, clear it when the queue empties, and guard `_review_each` / `_review_selected` on it.

4. **LOW** — `_pump()` (line ~4870): the 120 ms polling interval is fine for a batch run, but the `comfy` message type is handled in the same loop as `row`/`error`/`done`. A ComfyUI test result arriving during a long batch run will be delayed by up to 120 ms per row processed — negligible in practice, but if the batch produces thousands of rows and the user is waiting on the ComfyUI verdict in the popup, the delay compounds. Not worth fixing now; noted for completeness.

5. **LOW** — `_download_birefnet()` (line ~4780): runs `BN.download_weights(progress=progress)` on the main thread with `self.update()` inside the progress callback. This is explicitly documented as intentional ("the progress window is only alive while the event loop turns"), and it works, but it means a 444 MB download blocks all other UI interaction (no window move, no other button clicks) for the duration of the download. On a slow connection this can be minutes. Suggested fix: run the download in a daemon thread and post progress updates through `self.queue` (the same mechanism the batch run uses), so the rest of the UI stays responsive. The progress bar itself can be updated from `_pump`.

6. **LOW** — `cb_comfy_models` is initialised twice: once at line 3832 (`self.cb_comfy_models = {}` in `App.__init__`, before `_build`) and again at line 4082 (`self.cb_comfy_models = {}` inside `_build_comfy_popup`). The second assignment is the one that actually gets populated (line 4092 fills it with the three comboboxes), so the first is dead code. It does no harm (the dict is empty at both points) but a future reader may wonder which initialisation is authoritative. Suggested fix: delete the line-3832 assignment; the `_build_comfy_popup` one is sufficient and runs before any consumer (`_fill_model_lists` is called from within `_build_comfy_popup`).

No HIGH findings in this file. The architecture is sound: theme system with 5 palettes and runtime switching, "made once" variable pattern surviving `_build` re-runs, exclusive tool modes with `_switching` re-entrancy guard, loupe as a full-res magnifier parked in the cross gutter, crop rectangle with corner/edge/move drags and darken-outside veil baked into the array, SAM2 box-select via subprocess, mask brush (left paints / right erases / Alt+right sizes pen), ROI rulers as two draggable vertical lines on the before pane, grid/guides/rulers as canvas-drawn instruments never composited into the frame. The ComfyUI popup is built once and kept withdrawn (costs nothing until opened, closing hides rather than destroys). The batch worker thread posts to a `queue.Queue` that `_pump` drains every 120 ms — correct for Tk's thread-safety constraints.

---

## Proposal — Human-in-the-loop test suite ("Shootout")

### 1. Image set (20 images)

| # | Scene type | Why |
|---|---|---|
| 1–3 | Frontal facade, mild pitch (2–8°) | Core case; most common in architectural work |
| 4–5 | Corner view, two facades visible | Yaw + pitch interaction; the hardest 3-DOF case |
| 6–7 | Strong roll (5–10°), handheld tilt | Roll estimation under moderate distortion |
| 8–9 | Half-timbered / ornate facade | Angular prior stress: many diagonals inside the window |
| 10 | Night / low-light, artificial lighting | Detector robustness; M-LSD vs LSD divergence |
| 11 | Tree occlusion (30%+ of frame) | Mask necessity; evidence_lost boundary |
| 12 | Wide-angle (16–18 mm equiv), strong perspective | Focal estimate under high distortion |
| 13 | Telephoto (85–135 mm equiv), near-orthographic | Near-zero correction; skip accuracy |
| 14 | Glass curtain wall, reflective | Few real edges; detector failure mode |
| 15 | Interior / room corner (two walls + floor) | Non-architectural verticals; scheme partitioning |
| 16 | Mixed: building + sky + street furniture | Horizon estimate stress |
| 17 | EXIF-stripped image (no focal length) | default_focal_35mm path; focal_estimate=vp only |
| 18 | Barrel distortion visible (wide lens, no undistort) | Undistort shootout: off vs lensfun |
| 19 | Very steep pitch (>20°), looking up | max_pitch_deg boundary; refuse vs correct |
| 20 | Deliberately flat / no correction needed | Skip accuracy; min_correction_deg gate |

Selection criteria per image: ≥ 4000 px long edge, EXIF present (except #17), scene unambiguous enough that a human can state the "correct" answer with confidence.

### 2. Human correction workflow

For each of the 20 images, a reviewer opens it in the existing review GUI (`pc --gui`), adjusts roll/pitch/yaw/focal by hand until the result looks correct, then:

1. Records the final slider values (roll°, pitch°, yaw°, focal px or 35mm-equiv).
2. Draws a **manual mask** (PNG, same dimensions as source) marking the building region — white = keep, black = ignore. This is the ground-truth mask for the mask shootout.
3. Saves both into the EXIF UserComment of the image (see §3) and places the mask PNG beside it.

The reviewer does NOT run the automatic pipeline first — they correct by eye only. The automatic results are computed later by the shootout script, so there is no anchoring bias.

Estimated time per image: 3–5 min (adjust + mask draw). Total: ~1.5 h for one person.

### 3. EXIF ground-truth schema

Stored in `EXIF UserComment` (tag 0x9286), UTF-8, prefixed to distinguish from other comments:

```
PC-GT|roll=+3.42|pitch=-7.15|yaw=+0.00|focal_px=3124.5|focal_35mm=28.0|confident=yes|notes=corner view, left facade dominant
```

Fields:
- `roll`, `pitch`, `yaw` — degrees, signed. The human's final slider values.
- `focal_px` — the focal length in pixels that made the correction look right (may differ from EXIF FocalLengthIn35mm if the reviewer adjusted it).
- `focal_35mm` — 35 mm equivalent for reference.
- `confident` — `yes|no`. If `no`, the image is excluded from MAE/RMSE but still counted for skip-accuracy.
- `notes` — free text, ignored by scripts.

The mask PNG lives beside the source as `<stem>_gt_mask.png` (white = building, black = ignore). A second file `<stem>_auto_mask.png` is written by each mask mode during the shootout for comparison.

### 4. Scripts to write

| Script | Purpose |
|---|---|
| `tests/shootout/annotate_gt.py` | Small Tk helper: loads an image, shows sliders (roll/pitch/yaw/focal), a mask brush (reuse ReviewPanel's brush code or a minimal clone), and a "Save to EXIF" button that writes the UserComment + mask PNG. One run per image. |
| `tests/shootout/run_shootout.py` | For each of the 20 images × each config combo, calls `pc.pipeline.analyse()` (not `process()`, no warp — just the Model), records the resulting `Model.roll_deg / pitch_deg / yaw_deg / f / confidence` into a JSONL file. Config combos: detector ∈ {lsd, mlsd, deeplsd, hybrid, deep-hybrid, auto} × focal_estimate ∈ {off, vp, horizon, both} × mask_mode ∈ {off, file(gt), birefnet, gdino} × undistort ∈ {off, lensfun}. That is 6×4×4×2 = 192 runs per image, 3840 total. Runtime estimate: ~0.5–2 s per run (LSD/M-LSD) to ~5 s (DeepLSD on GPU), so 30–90 min single-threaded, < 10 min with 4 workers. |
| `tests/shootout/report.py` | Reads the JSONL + EXIF ground truth, computes per-image and aggregate metrics, writes a Markdown table to `docs/shootout_report.md`. Metrics: see §5. Also computes mask Jaccard for each mask mode vs the GT mask. |

Integration with existing test runner: add a `test_shootout.py` module (listed in `MODULES`) that runs as a **smoke test** — it checks that the JSONL exists and has 3840 rows, and that no row raised an exception. The full report is generated by `python tests/shootout/report.py` manually, not on every test run.

### 5. Analysis metrics

Per (image, config) pair:
- **Angle error**: |auto − human| for roll, pitch, yaw separately. Aggregate: MAE and RMSE across all 20 images (only `confident=yes`).
- **Focal error**: |f_auto − f_human| / f_human, as a fraction.
- **Hit rate** (per angle): fraction of images where |error| ≤ 1° (roll/pitch) or ≤ 2° (yaw).
- **Skip accuracy**: for image #20 (flat) and any other near-zero GT, did the pipeline correctly produce |pitch| < min_correction_deg? True-skip / false-alarm counts.
- **Refuse rate**: fraction of images where confidence < min_confidence or pitch > max_pitch_deg. Compare across detectors — a detector that refuses everything is not better than one that guesses.
- **Mask Jaccard**: for each mask mode (file/gt, birefnet, gdino), compute |A ∩ B| / |A ∪ B| between the auto mask and the GT mask at 50% threshold. Report per-image and mean.
- **Evidence lost**: from `prepare()`'s `evidence_lost` field — how much line length each mask mode removes. A mask with high Jaccard but also high evidence-lost is suspicious.

Report format: one Markdown table per metric dimension (detector, focal_estimate, mask_mode, undistort), rows = images, columns = the 4 config axes. Plus a "best combo" summary row at the top.

### 6. Design questions answered by this suite

| Question | Which axis answers it |
|---|---|
| Is M-LSD worth the TFLite dependency over plain LSD? | detector: lsd vs mlsd vs hybrid |
| Does DeepLSD's grad-NFA help on night/blur (image #10)? | detector: deeplsd, image #10 row |
| Should focal_estimate default to "both" instead of "vp"? | focal_estimate axis, focal error column |
| Is the BiRefNet mask worth 444 MB of weights? | mask_mode: birefnet vs file(gt) Jaccard + downstream pitch error delta |
| Does lensfun undistortion measurably help on image #18? | undistort axis, image #18 row |
| Is the angular prior (softness=0.35) optimal for half-timbered? | detector × image #8–9 pitch error; a follow-up sweep of softness ∈ {0.2, 0.35, 0.5} can be added to the shootout |
| Should min_confidence be lower now that review is manual? | confidence distribution across all 3840 runs vs the human "confident" flag |

### 7. Effort estimate

| Task | Time |
|---|---|
| Select + acquire 20 images (from existing pool or new) | 1 h |
| Annotate GT (sliders + mask brush × 20) | 1.5 h |
| Write `annotate_gt.py` (reuse ReviewPanel brush) | 3–4 h |
| Write `run_shootout.py` (loop, JSONL, multiprocessing) | 2 h |
| Write `report.py` (metrics, Markdown table) | 2 h |
| Run shootout + iterate on report format | 1 h (compute) + 0.5 h |
| **Total** | **~9–10 h** |

### 8. File layout

```
tests/shootout/
  __init__.py
  annotate_gt.py      # Tk helper, one run per image
  run_shootout.py     # the loop; writes tests/shootout/results.jsonl
  report.py           # reads results.jsonl + EXIF → docs/shootout_report.md
  results.jsonl       # gitignored; one JSON object per (image, config) pair
tests/assets/shootout/
  01_frontal_mild.jpg … 20_flat.jpg   # the 20 source images
  01_frontal_mild_gt_mask.png …       # human-drawn GT masks
tests/test_shootout.py                # smoke test: JSONL exists, row count, no exceptions
docs/shootout_report.md               # generated; committed after each re-run
```

`results.jsonl` is gitignored (3840 rows × ~200 bytes ≈ 770 KB, regenerable). The report Markdown is committed so the history of "which combo won" is visible in git log.

---

## User directive — fill_max_share and crop gates (2026-07-10)

User: "fill max share is idiotic. let user decide! user can fix crop area himself. just warn if image is too giant! horizontal correction produces too big images! if one edge is off, crop can fix that extreme!"

### Finding F1 — MED — `inpaint.py` line 673: `fill_max_share` refuses instead of warning

`fill()` raises `FillUnavailable` when the hole exceeds 35% of the frame. In a manual-review workflow the user has already seen the warped result, chosen the fill mode, and can adjust the crop rectangle. A hard refusal here is the same "refuse rather than trim" asymmetry that the 2026-09-13 product directive flagged as a batch-era default no longer justified by manual review.

Suggested fix: replace the `raise` with a warning (log + return a note string alongside the filled image, or print to stderr). The user's escape hatches already exist: they can change the crop in the review window, switch fill mode, or save without filling. If a cap is kept at all for batch use, it should be a `--fill-max-share 0` flag meaning "no cap" rather than a hard-coded refusal path.

### Finding F2 — MED — `warp.py` line 234: `max_crop_loss` (5%) silently falls back to whole-frame + pad

When `crop="auto"` and the inscribed-rect coverage drops below 95%, the function returns `_whole_frame(...)` — the full warped quad with padding. For strong horizontal (yaw) corrections this means the output canvas can be 2–3× the input area, and the "correction" is mostly empty padded space. The user's point: a large canvas with one edge off is exactly what the crop tool in the review window is for. The auto-crop gate should not pre-empt the user's manual crop decision.

Suggested fix: for `crop="auto"`, raise the threshold substantially (e.g. 25–30%) or remove it entirely and let the review window's crop tool handle it. Alternatively, make `max_crop_loss` a per-mode value: tight (5%) for unattended batch, loose (30%+) for interactive review. The CLI already exposes `--max-crop-loss`; the GUI review path should default to the looser value.

### Finding F3 — LOW — No size warning when output canvas exceeds input by a large factor

Neither `warp.py` nor `inpaint.py` reports "output is 2.4× the input area" anywhere the user can see it before saving. The review window shows the image but not the ratio. Suggested fix: in the review panel's status area (see gui.py finding #1, `_set_status` no-op), display `out_w × out_h (N.N× input)` when the ratio exceeds ~1.5×. This is the "just warn if image is too giant" the user asked for — a number on screen, not a refusal.

**Worst-case size quantification (user confirmed: "könnte theoretisch giant bilder ergeben!"):**
The output canvas dimensions come from `_whole_frame()`: `ow = x1 - x0`, `oh = y1 - y0` of the warped quad. For a pure yaw rotation of angle θ on an image with aspect ratio A = w/h, the quad's horizontal extent is approximately `w·|cos θ| + h·|sin θ|` (the rotated width projects onto both axes). The area ratio is:

```
area_ratio ≈ (w·cosθ + h·sinθ) · (h·cosθ + w·sinθ) / (w·h)
           = cos²θ + sin²θ + 2·(A + 1/A)·sinθ·cosθ
           = 1 + (A + 1/A)·sin(2θ)
```

| Input | Yaw cap | Area ratio | Example: 4000×3000 → output |
|---|---|---|---|
| 4:3 (A=1.33) | 60° | 1 + 2.33·sin(120°) ≈ **3.0** | ~5770×4330 (25 MP → 75 MP) |
| 3:2 (A=1.5) | 60° | 1 + 2.83·sin(120°) ≈ **3.4** | ~5590×3730 (24 MP → 82 MP) |
| 16:9 (A=1.78) | 60° | 1 + 3.67·sin(120°) ≈ **4.2** | ~6530×3670 (24 MP → 100 MP) |
| 1:1 (A=1.0) | 60° | 1 + 2.0·sin(120°) ≈ **2.7** | ~4899×4899 |

Combined with pitch (30° cap), the factor grows further — a 60° yaw + 30° pitch on a 16:9 frame can push the canvas past 5× input area. At `max_crop_loss=5%` the auto-crop almost never engages for these, so the full padded quad is kept. This is not a bug per se (the user may want the whole frame), but it justifies F3's warning and supports F2's recommendation to let the review window's manual crop handle the trimming rather than pre-empting it with a 5% gate.

### Finding F4 — LOW — `auto_crop_max_loss` (12%) in review.py is a third crop gate

`review.py` line 557: the review window's own auto-crop uses a separate 12% threshold (`auto_crop_max_loss`) distinct from `max_crop_loss` (5%). Three numbers governing "how much may be cropped" in three files. The user's mental model is one number: "I can crop as much as I want, the tool just tells me if it's extreme." Suggested fix: collapse to a single `crop_max_loss` setting used by both paths, or at minimum document the relationship and consider whether the review path needs its own gate at all (the user is looking at the result).

### Finding F5 — MED — No hard block on save; warnings must never disable the Save button

User directive: "Bild sollte IMMER speicherbar sein. Es sei denn, es ist korrupt. Eine Warnung ist OK mit Bitte um manuellen Crop. User soll aber dennoch speichern können!"

Audit of current refusal paths that could prevent a save in the review window:

| Path | Where | Current behaviour | Should be |
|---|---|---|---|
| `fill_max_share` exceeded | `inpaint.py:673` raises `FillUnavailable` | Fill is skipped, image shown unfilled; Save still works (the fill just didn't happen) | OK as-is for save; change message from "refuse" to "skipped — hole is 42%, consider cropping first" |
| `max_crop_loss` / `auto_crop_max_loss` | `warp.py:234`, `review.py:557` | Auto-crop falls back to whole-frame+pad; user can still manually crop and save | OK — already non-blocking. Just make the fallback visible (F3). |
| `min_confidence` gate | `model.py` / `pipeline.py` | In batch: image is skipped, nothing written. In review: confidence is displayed but does NOT block Save. | Correct for review. For batch: keep skip, but the review window must never inherit this gate. |
| `max_pitch_deg` / `refuse_beyond_limit` | `pipeline.py` | Batch: refuses to write. Review: the slider can go past 30°; no hard stop. | Correct for review. Ensure the slider has no upper clamp that prevents the user from reaching a value they want. |
| `FillUnavailable` (backend missing) | `inpaint.py` | Fill is skipped with a message; Save works on the unfilled image. | OK. |

**Rule to enforce:** In the review window, NO condition may disable or hide the Save button. The only exception is a genuinely corrupt/unreadable source (I/O error on load). All other "this looks wrong" situations are warnings shown in the status area (F3) — yellow text, not a greyed-out button.

Suggested fix:
1. Add an explicit assertion/test: after any combination of slider positions + fill mode + crop state, `review.save_enabled` is always `True` (unless the source file failed to load).
2. In `gui.py`, audit every `.config(state=...)` call on the Save button — there should be zero that set it to `"disabled"` for a non-I/O reason.
3. Warnings (size ratio, fill skipped, high crop loss) go into the status label as coloured text. The user reads them, decides, and saves or doesn't. The tool never decides for them.

---

## Horizontal (yaw) correction — failure modes and handling

### How it works today

The yaw estimate (`model.py:416–432`) is a **special case**, not part of the 3-DOF fit:

1. Only computed when `settings.correct_horizontal` is `True` (default: `False`).
2. Takes the **dominant** horizontal VP (`horiz_hyps[0]`), gated on `support >= min_horizontal_support` (default 0.3).
3. Computes the angle between that VP's world bearing and the image x-axis after levelling by (roll, pitch).
4. Folds to [-90°, +90°] (a line has no direction).
5. In `warp.limit()`: multiplied by `horizontal_strength`, capped at `max_horizontal_deg` (default 60°), and if the cap is hit → `clamped=True` → `refuse_beyond_limit` path → **skip**.

In the review window: a checkbox "horizontal (yaw) — one facade only" enables the yaw slider. In AUTO mode, ticking it triggers a re-fit. In MANUAL mode, the slider value is used as-is (no cap applied to manual values — `current_yaw()` returns `self.manual_yaw` directly).

### Failure modes

| # | Scenario | What happens | Severity |
|---|---|---|---|
| H1 | **Two-facade corner view**, no facade strip set | Dominant horizontal VP is whichever facade has more visible horizontals. Yaw squares onto that one; the second facade gets worse. User sees a sheared result. | MED — expected behaviour, but the warning ("one facade only") is in a tooltip, not in the status area |
| H2 | **Weak/no horizontal VP** (glass wall, night, few horizontals) | `horiz_hyps` is empty or `dom.support < 0.3` → yaw stays 0.0. Silent: no message says "I looked for horizontals and found nothing usable." | LOW — the user sees yaw=0.0 in the readout and can infer, but a note would help |
| H3 | **Yaw estimate exceeds `max_horizontal_deg`** (60°) | `warp.limit()` clamps → `clamped=True` → `refuse_beyond_limit=True` → **image is skipped** in batch. In review: the slider can go past 60° (manual mode bypasses the cap), but AUTO mode shows the clamped value. | MED — a 70° corner shot is legitimate; refusing it because the default cap is 60° is the same batch-era asymmetry. The user raised `max_horizontal_deg` from 8→60 on 2026-09-13 for exactly this reason, but the refuse path still fires above 60°. |
| H4 | **Wrong horizontal VP chosen** (e.g. a road or fence line dominates over the building) | Yaw is computed from the wrong plane. The result shears the building sideways. Confidence does NOT catch this: the horizontal VP has good support, the fit is internally consistent — it's just about the wrong object. | HIGH in principle, but mitigated by: (a) `correct_horizontal` is off by default, (b) the facade strip (`roi_x`) lets the user restrict evidence to one region, (c) manual review means a person sees the shear before saving. Still: no diagnostic says "the dominant horizontal VP is at bearing X°; are you sure that's the building and not the road?" |
| H5 | **Yaw + pitch interaction**: strong yaw changes what "vertical" looks like in the warped frame | The 3-DOF model applies R = Rz(roll)·Rx(pitch)·Ry(yaw). A large yaw rotates the verticals in the image plane, so a subsequent roll/pitch correction is slightly different than without yaw. The current code computes them independently (yaw from horizontals after levelling by roll+pitch), which is correct to first order but can leave a residual tilt on strong corrections (>30° yaw + >15° pitch). | LOW — measurable only at extreme angles; the review window's manual sliders let the user fix any residual. |
| H6 | **`min_horizontal_support` gate too tight for manual use** | Default 0.3 means "at least 30% of horizontal lines must agree on this VP." In a scene with few horizontals (telephoto, partial facade), even a correct VP may not reach 0.3 support. The review window already drops this to 0.0 when the user draws ≥2 horizontal control lines (`review.py:311`), but in AUTO mode with detected lines only, the gate can silently zero the yaw. | MED — the user ticks "horizontal (yaw)" expecting a correction, gets yaw=0.0, and the only clue is the readout showing 0.00deg. No warning says "support was 0.21, below the 0.30 gate." |

### Suggested handling (proposals)

**P1 — Replace refuse with warn for yaw cap (addresses H3):**
In `pipeline.py`, when `clamped` is True and the only breached axis is yaw (roll and pitch are within their caps), do NOT skip. Instead: apply the capped yaw, add a warning to the result note ("yaw clamped from 72° to 60°"), and let the image be written. The review window already allows manual values past the cap; the batch path should at least not refuse outright. If `refuse_beyond_limit` is kept for roll/pitch (where a wrong estimate is more dangerous), yaw should be exempt because the user explicitly opted in by ticking the checkbox.

**P2 — Surface the support gate decision (addresses H6):**
In `model.py`, when `correct_horizontal` is on but `dom.support < min_horizontal_support`, store the reason in `diagnostics`: `"yaw_skipped": f"horizontal VP support {dom.support:.2f} < gate {min_horizontal_support:.2f}"`. In the review window's status area (F3/F5), display this when yaw reads 0.0 but the checkbox is ticked. The user then knows to either draw horizontal control lines (which drops the gate to 0) or accept that the evidence is too weak.

**P3 — Show which VP won (addresses H4):**
When multiple horizontal VPs exist (`len(horiz_hyps) > 1`), the review window should display them as small markers on the before pane (like the vertical VP marker already exists). The dominant one gets a filled dot, the others hollow. Clicking a non-dominant one could set it as the yaw source (manual override). This makes the "which facade" decision visible and correctable without the facade strip.

**P4 — Status warning on two-facade scenes (addresses H1):**
When `correct_horizontal` is on AND ≥2 horizontal VPs have support > 0.2, add a persistent status note: "Two horizontal directions detected — yaw squares onto [left/right] facade. Use the facade strip to choose." This is the tooltip text promoted to always-visible when the condition is met.

**P5 — No cap on manual yaw in review (already correct, make explicit):**
`review.py:857` returns `self.manual_yaw` directly in MANUAL mode, bypassing `W.limit()`. This is correct per F5 (user decides). Add a comment and a test asserting that `current_yaw()` in MANUAL mode never clamps. The only guard should be the slider's own range (currently -90 to +90, which is the mathematical limit for a folded angle).

---

## Module Splitting — Context Overflow Prevention

### Problem

A local AI coder with ~120k token context can barely hold `gui.py` (4911 lines ≈ 60k tokens) plus one related file. Any task touching the review panel + pipeline + warp simultaneously overflows. The fix is structural: split `gui.py` into focused modules so no single file exceeds ~800 lines (~25k tokens).

### Size ranking (src/pc, by bytes)

| File | KB | Lines | Verdict |
|------|-----|-------|---------|
| gui.py | 243 | 4911 | **Split — clear outlier** |
| review.py | 59 | 1213 | Keep (tightly coupled state machine) |
| cli.py | 34 | ~800 | Keep (argparse + orchestration, one concern) |
| inpaint.py | 33 | ~700 | Keep |
| birefnet.py | 30 | ~650 | Keep |
| model.py | 25 | ~550 | Keep |
| layout.py | 20 | ~450 | Keep |

### Pattern choice: Composition + Facade (not Mixin)

| Criterion | Mixin | Composition/Delegation |
|-----------|-------|----------------------|
| MRO complexity | High — AI must trace `__mro__` across 3-4 classes | None — flat `self.crop._on_drag(...)` |
| File navigation | Grep hits mixin file, must infer which class owns it | Grep hits the controller file directly |
| Testability | Must instantiate full widget tree | Controller tested with mock parent |
| Python docs alignment | "Mixins are for shared behaviour with no state of their own" | Recommended default; classes do one thing |

**Decision:** Extract named controller objects that own a subset of widgets + state. `ReviewPanel` keeps its public API (`build()`, `on_image()`, etc.) but delegates internal groups to controllers. `gui.py` becomes a thin facade (~200 lines) that imports and wires them.

### Proposed split (12 files, all under ~800 lines)

| New file | Lines (est.) | Contents |
|----------|-------------|----------|
| `gui.py` (facade) | ~200 | `App` class shell: window setup, theme switch, settings panel, file mgmt, batch run, review orchestration. Imports controllers, wires them in `_build_review()`. |
| `gui_theme.py` | ~350 | `INK`, `THEMES`, `apply_theme()`, `_retint_bg()`, `_gradient_photo()`, `_to_photo()`, `_shorten_middle()`, `_pick_family()`, `_rgb()`, beholder pyramid, `_logo_image()`, `_icon_pil()`, `_beholder_pil()`, `_set_window_icon()`, `_attach_tooltip()`, `_brand_header()` |
| `gui_review_panel.py` | ~700 | `ReviewPanel` class: `_build()` (construction), slider controls, status/save, public API (`on_image`, `apply`, `next`, etc.) |
| `gui_review_crop.py` | ~450 | Crop interaction: drag handles, rubber-band, corner grab, crop apply/cancel, ROI strip (L2631-2731) |
| `gui_review_mask.py` | ~300 | Mask handling (L1319-1574): BiRefNet/SAM2 click-to-select, mask overlay, ComfyUI registration |
| `gui_review_tools.py` | ~500 | Tools/strokes (L2451-2909): pen/brush, marks/clicks, stroke rendering, undo stack |
| `gui_review_loupe.py` | ~200 | Loupe Toplevel: magnifier canvas, event transparency, follow-cursor |
| `gui_review_canvas.py` | ~500 | Canvas redraw (L2909-3424): `_redraw()`, overlay guides, VP markers, grid, before/after panes |
| `gui_review_sam.py` | ~250 | SAM integration (L3424-3602): segment call, result overlay, apply-to-mask |
| `gui_app_batch.py` | ~400 | Batch run logic: progress bar, threading, result collection, report writing |
| `gui_app_files.py` | ~300 | File management: drag-drop, folder scan, thumbnail strip, image queue |
| `gui_app_settings.py` | ~250 | Settings panel widgets + ComfyUI popup (L4142-4455) |

**Total:** ~4100 lines across 12 files vs. 4911 in one. The savings are not in line count but in *per-file token cost*: the largest file drops from 60k tokens to ~25k, and a task touching "crop + mask" loads two 15k-token files instead of one 60k-token file.

### Wiring example (how controllers plug into ReviewPanel)

```python
# gui_review_crop.py
class CropController:
    """Owns crop interaction state + widgets. Parent provides the canvas."""
    def __init__(self, panel: "ReviewPanel", canvas: tk.Canvas):
        self.panel = panel          # back-reference for _redraw(), session access
        self.canvas = canvas
        self.active = None          # (x0,y0,x1,y1) or None
        self._drag_id = None
        canvas.bind("<ButtonPress-1>", self._on_press, add="+")
        canvas.bind("<B1-Motion>", self._on_drag, add="+")
        canvas.bind("<ButtonRelease-1>", self._on_release, add="+")

    def _on_press(self, ev): ...
    def _on_drag(self, ev): ...
    def _on_release(self, ev): ...
    def apply_crop(self): ...
    def cancel(self): ...
```

```python
# gui_review_panel.py (inside ReviewPanel._build)
def _build(self):
    # ... canvas creation ...
    self.crop = CropController(self, self.canvas)
    self.mask = MaskController(self, self.canvas)
    self.tools = ToolController(self, self.canvas)
    self.loupe = LoupeController(self, self.root)
    self.sam = SamController(self)
```

The `panel` back-reference is the one indirection an AI coder must follow; it is always `self.panel.session` (ReviewSession) or `self.panel.canvas`. No MRO, no super() chains.

### Migration order (each step is independently shippable)

1. **`gui_theme.py`** — pure functions + dicts, zero widget state. Trivial extract, zero risk. All other files import from it.
2. **`gui_review_loupe.py`** — self-contained Toplevel, already has its own event forwarding. Lowest coupling to ReviewPanel internals.
3. **`gui_review_crop.py`** — crop is the most-tested interaction; existing tests in `test_gui.py` cover it. Extract + re-run tests validates the pattern before touching larger groups.
4. **`gui_review_mask.py`** + **`gui_review_sam.py`** — mask/SAM are additive features with clear entry points (`_on_sam_click`, `_apply_mask`).
5. **`gui_review_tools.py`** — pen/brush/marks; moderate coupling to canvas redraw.
6. **`gui_review_canvas.py`** — `_redraw()` is the hub; extract last because every controller calls `panel._redraw()`. Once controllers are out, `_redraw` becomes a standalone function taking `(canvas, session, theme)`.
7. **`gui_app_*.py`** — App-side extraction (batch, files, settings). Independent of ReviewPanel split.
8. **`gui.py` shrinks to facade** — after all extracts, only `App.__init__`, window setup, and `_build_review()` wiring remain.

### AI-coder context budget (before vs. after)

| Task | Before (tokens loaded) | After (tokens loaded) |
|------|----------------------|---------------------|
| Fix crop drag bug | gui.py 60k + review.py 15k = **75k** | gui_review_crop.py 12k + review.py 15k = **27k** |
| Add SAM2 click mode | gui.py 60k + birefnet.py 8k = **68k** | gui_review_sam.py 7k + birefnet.py 8k = **15k** |
| Change theme palette | gui.py 60k | gui_theme.py 9k = **9k** |
| Full pipeline change (warp+model+gui) | gui.py 60k + warp 4k + model 7k = **71k** | gui_review_panel 18k + warp 4k + model 7k = **29k** |

All tasks now fit comfortably in a 120k window with room for tests + conversation. The old budget left ~45k for context; the new budget leaves ~90k.

---

## Concrete Code Blocks — Findings F1–F5

### F1 code: `inpaint.py` fill() — replace raise with warning + note

**Current (L665-695):**
```python
    mode = getattr(settings, "fill", "none")
    if mode in ("", "none"):
        return bgr, ""
    if mode not in MODES:
        raise FillUnavailable(f"unknown fill mode: {mode}")
    if hole is None or not bool(np.any(hole)):
        return bgr, "nothing to fill"
    share = float(np.mean(hole))
    cap = float(getattr(settings, "fill_max_share", 0.35))
    if share > cap:
        raise FillUnavailable(
            f"the hole is {share:.0%} of the frame, over --fill-max-share "
            f"({cap:.0%}). That much invented content is a picture, not a "
            f"correction; crop instead")
```

**Proposed replacement (last block only):**
```python
    share = float(np.mean(hole))
    cap = float(getattr(settings, "fill_max_share", 0.35))
    if cap > 0 and share > cap:
        note = (f"fill skipped — hole is {share:.0%} of the frame "
                f"(over --fill-max-share {cap:.0%}); consider cropping first")
        print(f"[pc] {note}", file=sys.stderr)
        return bgr, note
```

Caller change in `pipeline.py` (where `fill()` is called): the existing `except FillUnavailable` path already handles the "backend missing" case. The new code path returns normally with a note, so no caller change needed — the note string flows into the result's `note` field the same way `"nothing to fill"` does today.

**Test addition (`tests/test_inpaint.py`):**
```python
def test_fill_over_cap_returns_note_not_raise():
    import numpy as np
    from pc.inpaint import fill
    from pc.config import Settings
    img = np.zeros((100, 100, 3), np.uint8)
    hole = np.zeros((100, 100), bool); hole[:60, :] = True  # 60% > 35%
    s = Settings(fill="telea", fill_max_share=0.35)
    out, note = fill(img, hole, s)
    assert "fill skipped" in note
    assert out is img  # unchanged, no fill applied
```

### F2 code: `warp.py` plan() — raise auto-crop threshold for review path

**Current (L234):**
```python
    coverage = (rw * rh) / quad_area(quad) if quad_area(quad) > 0 else 0.0
    if settings.crop == "auto" and (1.0 - coverage) > settings.max_crop_loss:
        return _whole_frame(H, quad, img_w, img_h, settings, area_ratio)
```

**Proposed:**
```python
    coverage = (rw * rh) / quad_area(quad) if quad_area(quad) > 0 else 0.0
    # Review path uses a looser gate: the user sees the result and can
    # manually crop; pre-empting with a 5% cap produces giant padded frames.
    loss_cap = settings.auto_crop_max_loss if getattr(settings, "interactive", False) \
               else settings.max_crop_loss
    if settings.crop == "auto" and (1.0 - coverage) > loss_cap:
        return _whole_frame(H, quad, img_w, img_h, settings, area_ratio)
```

**`config.py` addition:**
```python
    # In interactive (review) mode the auto-crop gate is looser because the
    # user can manually trim; batch keeps the tight 5% default.
    auto_crop_max_loss: float = 0.30   # was in review.py only; now shared
    interactive: bool = False          # set True by gui.py before calling plan()
```

**`gui.py` (review panel, where `warp.plan()` is called):**
```python
    settings.interactive = True   # one line, before the plan() call
```

**CLI unchanged:** `--max-crop-loss 0.05` still works for batch; the new `auto_crop_max_loss` defaults to 30% and is only consulted when `interactive=True`.

### F3 code: size warning in review status area

**In `gui_review_panel.py` (or current `gui.py` `_set_status` / status label update):**
```python
    def _update_size_warning(self, out_w: int, out_h: int):
        """Show 'out WxH (N.N× input)' when the canvas is >1.5× the source area."""
        src_area = self.session.img_w * self.session.img_h
        out_area = out_w * out_h
        ratio = out_area / max(src_area, 1)
        if ratio > 1.5:
            self._status_var.set(
                f"⚠ {out_w}×{out_h} ({ratio:.1f}× input) — consider cropping")
            self._status_label.config(fg="#ffcc00")  # yellow, not red
        else:
            self._status_label.config(fg=self._ink("fg"))
```

Call site: inside `_redraw()` or `on_image()`, after the warp plan is computed and `out_w, out_h` are known.

**Test (`tests/test_gui.py`):**
```python
def test_size_warning_shown_for_giant_output():
    app = App(start_maximized=False)
    app.geometry("1200x800-4000+0")
    # ... load a test image, set yaw to 50° so the canvas grows ...
    app.update(); time.sleep(0.02)
    status = app.review._status_var.get()
    assert "×" in status and "input" in status
    app.destroy()
```

### F4 code: collapse three crop gates to one shared setting

**Current state (three numbers, three files):**
| Setting | File | Default | Used by |
|---------|------|---------|---------|
| `max_crop_loss` | `config.py` / `warp.py:234` | 5% | batch auto-crop |
| `auto_crop_max_loss` | `review.py:557` | 12% | review auto-crop |
| `fill_max_share` | `config.py` / `inpaint.py:673` | 35% | fill refusal (F1) |

**Proposed: single `crop_max_loss` in `config.py`, mode-dependent default:**
```python
# config.py — replace both max_crop_loss and auto_crop_max_loss
    crop_max_loss: float = 0.05   # batch default (tight)
    # review.py sets this to 0.30 at session start (or reads from --max-crop-loss)
```

**`warp.py` plan() — simplified (supersedes F2 code above):**
```python
    coverage = (rw * rh) / quad_area(quad) if quad_area(quad) > 0 else 0.0
    if settings.crop == "auto" and (1.0 - coverage) > settings.crop_max_loss:
        return _whole_frame(H, quad, img_w, img_h, settings, area_ratio)
```

**`review.py` — remove the local `auto_crop_max_loss`, use the shared setting:**
```python
    # In ReviewSession.__init__ or on_image():
    self.settings.crop_max_loss = max(self.settings.crop_max_loss, 0.30)
    # Rationale: in interactive mode the user sees the result; a 30% gate
    # is generous enough to avoid giant frames while never pre-empting
    # a manual crop decision. The user can tighten via --max-crop-loss.
```

**CLI:** `--max-crop-loss` now maps directly to `settings.crop_max_loss`. No new flag needed.

### F5 code: enforce "Save is always enabled" invariant

**Test (add to `tests/test_gui.py`):**
```python
def test_save_never_disabled_by_warnings():
    """No combination of sliders, fill mode, or crop state may disable Save."""
    app = App(start_maximized=False)
    app.geometry("1200x800-4000+0")
    # Load a synthetic image with strong convergence
    from tests.synth import building_photo
    path = _tmp(building_photo(800, 600, roll=5, pitch=25))
    app.load(path)
    app.update(); time.sleep(0.02)

    # Push sliders to extremes
    app.review._set_roll(30); app.review._set_pitch(30)
    app.review._set_yaw(60)
    app.update(); time.sleep(0.02)

    # Try every fill mode including one that would exceed fill_max_share
    for mode in ("none", "telea", "lama"):
        app.review._fill_mode.set(mode)
        app.update(); time.sleep(0.01)
        state = app.review._save_btn.cget("state")
        assert str(state) != "disabled", f"Save disabled with fill={mode}"

    # Crop to a tiny region (high loss)
    app.review.crop.active = (400, 300, 410, 310)  # 10×10 px
    app.update(); time.sleep(0.02)
    assert str(app.review._save_btn.cget("state")) != "disabled"

    app.destroy()
```

**Audit checklist (manual, one-time):**
Grep `gui.py` for `_save_btn.config(state=` and `.config(state="disabled")`. Every hit must be either:
- In the I/O error path (file failed to load) — **allowed**
- Removed (all other occurrences should be deleted)

If any non-I/O disable is found, replace with a status warning (yellow text) and leave the button enabled.

---

## Concrete Code Blocks — Proposals P1–P5

### P1 code: `pipeline.py` — yaw-only clamp warns instead of refusing

**Current behaviour:** `warp.limit()` returns `clamped=True` → `refuse_beyond_limit` skips the image. This is correct for roll/pitch (wrong estimate = bad output) but too aggressive for yaw when the user explicitly ticked "correct horizontal."

**Proposed change in `pipeline.py` (where `clamped` is consumed):**
```python
    roll, pitch, yaw, clamped = W.limit(roll_est, pitch_est, settings,
                                        yaw=yaw_est, focal_is_a_guess=fg)
    if clamped and settings.refuse_beyond_limit:
        # Yaw-only breach: user opted in explicitly; warn and continue.
        r_ok = abs(roll) <= math.radians(settings.max_roll_deg) + 1e-9
        p_ok = abs(pitch) <= math.radians(settings.max_pitch_deg) + 1e-9
        if r_ok and p_ok:
            notes.append(f"yaw clamped from {math.degrees(yaw_est):.1f}° "
                         f"to {math.degrees(yaw):.1f}°")
            # fall through — do NOT skip
        else:
            return None, "beyond correction limit (roll/pitch)"
```

**Key detail:** the check is `r_ok AND p_ok` (not `not clamped`). If roll or pitch also breached, the original refuse path still fires. Only a *pure yaw* breach gets the warn-and-continue treatment.

**Test (`tests/test_pipeline.py`):**
```python
def test_yaw_only_clamp_warns_not_refuses():
    from pc.config import Settings
    s = Settings(correct_horizontal=True, max_horizontal_deg=60,
                 refuse_beyond_limit=True)
    # Simulate: yaw estimate 75° (exceeds 60° cap), roll/pitch within limits
    # ... call the pipeline function that consumes warp.limit() ...
    # Assert: result is NOT None, and notes contain "yaw clamped"
```

### P2 code: `model.py` — surface support gate in diagnostics

**Current:** when `dom.support < min_horizontal_support`, yaw silently stays 0.0. No diagnostic records why.

**Proposed addition in `model.py` (where the horizontal VP gate is evaluated):**
```python
    if settings.correct_horizontal and dom is not None:
        if dom.support < settings.min_horizontal_support:
            diagnostics["yaw_skipped"] = (
                f"horizontal VP support {dom.support:.2f} "
                f"< gate {settings.min_horizontal_support:.2f}")
            yaw_est = 0.0
        else:
            yaw_est = _yaw_from_vp(dom, ...)
    elif settings.correct_horizontal and dom is None:
        diagnostics["yaw_skipped"] = "no horizontal VP found"
        yaw_est = 0.0
```

**Display in review window (status area, alongside F3 size warning):**
```python
    # In _update_status() or equivalent:
    diag = self.session.diagnostics
    if "yaw_skipped" in diag and self._horiz_var.get():
        warnings.append(f"yaw off: {diag['yaw_skipped']}")
```

The user sees: "yaw off: horizontal VP support 0.21 < gate 0.30" — actionable (draw control lines to drop the gate, or accept the weak evidence).

### P3 code: display multiple horizontal VPs as markers

**In `gui_review_canvas.py` (or current `_redraw()`), after drawing the vertical VP marker:**
```python
    # Horizontal VP markers (P3): filled dot = dominant, hollow = others
    horiz_vps = self.session.diagnostics.get("horiz_vps", [])
    if horiz_vps and self._horiz_var.get():
        for i, vp in enumerate(horiz_vps[:4]):  # show at most 4
            px, py = self._vp_to_canvas(vp)
            if not (0 <= px < cw and 0 <= py < ch):
                continue  # off-screen VP
            r = 5 if i == 0 else 4
            color = INK["accent"] if i == 0 else INK["fg_dim"]
            self.canvas.create_oval(px - r, py - r, px + r, py + r,
                                    outline=color, width=2,
                                    fill=color if i == 0 else "")
```

**`model.py` — store the list in diagnostics:**
```python
    diagnostics["horiz_vps"] = [h.point for h in horiz_hyps[:4]]
    # Each element is (x, y) in normalised image coords, same format as the
    # existing vertical VP diagnostic.
```

**Interaction (optional, phase 2):** clicking a hollow marker sets it as the yaw source:
```python
    def _on_vp_click(self, ev):
        for i, vp in enumerate(horiz_vps[:4]):
            if self._vp_near(ev.x, ev.y, vp, radius=10):
                self.session.override_horiz_idx = i
                self._redraw()
                break
```

### P4 code: two-facade warning in status area

**In the same `_update_status()` block as P2:**
```python
    # P4: warn when ≥2 horizontal VPs have meaningful support
    horiz_vps = self.session.diagnostics.get("horiz_vps", [])
    supports = self.session.diagnostics.get("horiz_supports", [])
    if (self._horiz_var.get() and len(horiz_vps) >= 2
            and sum(1 for s in supports[:2] if s > 0.2) >= 2):
        warnings.append(
            "two horizontal directions — yaw squares onto dominant facade; "
            "use ROI strip to choose")
```

**`model.py` — store per-VP support:**
```python
    diagnostics["horiz_supports"] = [h.support for h in horiz_hyps[:4]]
```

### P5 code: document + test the manual-yaw bypass

**In `review.py` (where `current_yaw()` is defined, ~L857):**
```python
    def current_yaw(self) -> float:
        """Return the effective yaw in radians.

        In MANUAL mode this is the slider value verbatim — no cap is applied.
        The slider's own range (-90..+90 deg) is the only guard, which is the
        mathematical limit for a folded angle. This is intentional: the user
        is looking at the result and decides whether 75° looks right.
        """
        if self.mode == Mode.MANUAL:
            return math.radians(self.manual_yaw)
        # AUTO mode: capped by warp.limit() upstream
        return self._auto_yaw
```

**Test (`tests/test_review.py`):**
```python
def test_manual_yaw_never_clamped():
    """MANUAL mode returns the slider value regardless of max_horizontal_deg."""
    s = Settings(max_horizontal_deg=30)  # tight cap
    session = ReviewSession(s, img_w=1000, img_h=800)
    session.mode = Mode.MANUAL
    for deg in (-90, -60, -30, 0, 30, 60, 90):
        session.manual_yaw = deg
        assert abs(session.current_yaw() - math.radians(deg)) < 1e-9, \
            f"yaw {deg}° was clamped in MANUAL mode"
```

---

## Fallback investigation (2026-07-10)

### Context

`lines.py` hybrid detector: M-LSD provides structural guide segments; LSD provides
precise endpoints. `gate_by()` keeps only LSD segments that lie along M-LSD guides
(angle_tol=6°, dist_tol=8px, extent_margin=15%). If the gate removes too many
segments, the code falls back to plain LSD:

```python
min_keep = max(getattr(settings, "min_vertical_lines", 4), len(base[0]) // 4)
if len(gated) < min_keep:
    return base[0], f"lsd({detector} fallback)"
```

The `//4` threshold means at least **25%** of LSD segments must survive the gate.

### Exact counts (tools/debug_fallback.py)

| Image | Size | LSD segs | M-LSD guides | Gated | min_keep (LSD//4) | Verdict |
|---|---|---:|---:|---:|---:|---|
| facade_clutter_heavy_pitch8_s.jpg | 1024×682 | 2769 | 101 | 525 | 692 | **FALLBACK** (525 < 692) |
| facade_pitch12_roll3_yaw5_s.jpg | 1024×682 | 1678 | 47 | 352 | 419 | **FALLBACK** (352 < 419) |
| facade_pitch8_roll-2_s.jpg | 1024×682 | 1688 | 54 | 493 | 422 | OK (493 ≥ 422) |

Gate survival rate: 525/2769 = **19.0%**, 352/1678 = **21.0%**, 493/1688 = **29.2%**.
All three are below or near the 25% threshold.

### Root cause

M-LSD (TFLite, 512×512 input, half-res displacement map) produces only **47–101**
guide segments per image, while LSD finds **1600–2800**. The gate can structurally
only keep ~20–30% of LSD segments because there simply aren't enough guides to cover
them all. The `//4` (25%) threshold sits right at the boundary of what's achievable.

### Accuracy comparison (tools/debug_fallback2.py)

| Image | Detector | Roll (err) | Pitch (err) | Focal | Conf |
|---|---|---|---|---:|---:|
| facade_clutter_heavy_pitch8_s.jpg (GT r=+2 p=+8) | lsd | −2.12° (**−4.12**) | +8.97° (+0.97) | 927.4 | 0.636 |
| | hybrid (fallback→lsd) | −2.12° (**−4.12**) | +8.97° (+0.97) | 927.4 | 0.636 |
| facade_pitch12_roll3_yaw5_s.jpg (GT r=+3 p=+12) | lsd | −2.93° (**−5.93**) | +12.36° (+0.36) | 803.1 | 0.676 |
| | hybrid (fallback→lsd) | −2.93° (**−5.93**) | +12.36° (+0.36) | 803.1 | 0.676 |
| facade_pitch8_roll-2_s.jpg (GT r=−2 p=+8) | lsd | +1.97° (**+3.97**) | +8.29° (+0.29) | 790.0 | 0.535 |
| | hybrid (gate active) | +2.02° (**+4.02**) | +8.81° (+0.81) | 818.6 | 0.513 |

Key observations:

1. **Fallback = identical to LSD.** The two falling-back images produce bit-identical
   results to plain LSD (by definition — the fallback *is* plain LSD). No accuracy
   loss, but no gating benefit either.
2. **Gate active ≠ better.** On facade_pitch8_roll-2_s.jpg (the one image where the
   gate survives), pitch error is actually *worse* (0.81° vs 0.29°) and focal differs
   (818.6 vs 790.0). The gate discards useful LSD segments on this scene.
3. **Systematic roll error.** All three images show large roll errors (−4 to −6°)
   in *both* LSD and hybrid. This is not a detector issue — it's likely in the VP
   search / RANSAC fit or the synthetic scene rendering itself. Pitch errors are
   small (0.3–1.0°).

### Options

| Option | Change | Effect |
|---|---|---|
| **A: keep //4** | none | 2/3 images fall back to plain LSD; gate is dead weight on those scenes |
| **B: lower to //5 (20%)** | `len(base[0]) // 5` | All 3 images pass the gate (19%/21%/29% ≥ 20%). Gate active everywhere. Risk: gate may degrade accuracy as seen in row 3 above. |
| **C: lower to //6 (17%)** | `len(base[0]) // 6` | Same as B for these images, more headroom for busier scenes |
| **D: remove fallback entirely** | always use gated result | Gate is the sole path; no safety net if M-LSD produces zero guides |

The data suggests the gate provides **no measurable benefit** on these synthetic
scenes (fallback = identical, gate active = slightly worse pitch). The systematic
roll error is a separate issue that needs investigation in the VP/RANSAC pipeline.

### Resolution (2026-07-10)

Gate removed. `detect_segments()` for "hybrid"/"deep-hybrid" now returns plain LSD
segments directly. Default detector changed to `"lsd"` in config.py. The `gate_by()`
function is retained in lines.py (unit-tested) but no longer called from the
production path. M-LSD/DeepLSD guide computation still runs (wasted compute) — a
follow-up should skip it when detector is "hybrid"/"deep-hybrid".

---

## Scientific research: architectural image rectification (2026-07-10)

### Scope

Literature review targeting the `pc` pipeline's core problem: given a single photo
of a building facade, estimate camera roll/pitch/yaw (and focal length) to produce
a fronto-parallel rectified image. Focus: methods applicable to **architectural
imagery** specifically.

### 1. Line detection for architectural scenes

| Paper | ID | Key finding | Relevance to pc |
|---|---|---|---|
| **SweepLSD** (2026) | arXiv:2608.22086 | Single-pass, O(width)-memory line detector. 25× faster than LSD, best per-segment direction accuracy on NYU-VP (leads by ~0.3°). 0.06° median attitude error for horizon lock at 4K. | **High.** Direct drop-in replacement for LSD. Better direction accuracy → better VP estimates. Open question: F-score trails ELSED on synthetic GT. |
| **LSDNet** (2022) | arXiv:2209.04642 | Lightweight CNN replaces LSD's gradient step. 214 FPS, 78 Fh (gap to SOTA shrinks to 0.2 Fh with corrected annotations). | **Medium.** Faster than LSD on GPU, but the accuracy gain is marginal after annotation correction. The classical LSD core is retained. |
| **LAFR / RTFP** (2023) | arXiv:2309.15523 | Vision Transformer for facade parsing + line-based revision (LAFR). Uses "simple line detection" to correct segmentation boundaries using facade structural priors. | **Medium.** Validates the principle that lines improve facade understanding, but uses lines as a *revision* step on top of segmentation, not as the primary geometric signal. |
| **EDLines** (2017) | — | Gradient-based, probabilistic NFA scoring. Current "best classical" detector per SweepLSD comparison. | Already available in pc (`fld` detector). SweepLSD supersedes it on speed and direction accuracy. |

### 2. Vanishing point / camera pose estimation (monocular)

| Paper | ID | Key finding | Relevance to pc |
|---|---|---|---|
| **GeoCalib** (2024) | arXiv:2409.06704 | End-to-end CNN estimating focal length + gravity direction from a single uncalibrated image. Uses internal geometric optimization (universal 3D constraints). More robust than classical line/VP methods. Outputs uncertainty estimates. | **High.** This is the closest thing to a "learned pc pipeline." Could replace or augment the RANSAC VP search. Gravity direction → roll/pitch directly. Focal length → replaces `focal_from_horizon`. Uncertainty output → confidence score. |
| **PARSAC** (2024) | arXiv:2401.14919 | Neural network segments input data into clusters for parallel robust multi-model fitting (VPs, homographies). 5 ms/image inference. SOTA on synthetic + established benchmarks. | **High.** Could replace the RANSAC loop in `model.py` for VP clustering. Instead of iterative hypothesis testing, a net predicts which lines belong to which VP group, then a fast per-group fit runs in parallel. |
| **Horizon Lines in the Wild** (2016) | arXiv:1604.02129 | CNN directly estimates horizon line from single image without geometric constraints. SOTA on HLW dataset + 2 benchmarks. | **Medium.** Horizon line → roll angle. Could be a fast pre-pass before the full VP search. Dated (2016) but the principle is validated. |
| **Fast Projective Rectification** (2019) | arXiv:1912.01892 | Manhattan World assumption → optimize 2 VPs → homography. 3 ms on CPU. Accuracy ≥ SOTA when background < 50% of image. | **Medium.** Validates the 2-VP approach pc already uses. The "background < 50%" constraint is relevant: pc's `masks` module (SAM2) could enforce this. |
| **TopView** (2024) | arXiv:2412.16229 | Learns scene VP from uncalibrated street-level imagery for bird's-eye-view projection. | **Low.** Focused on road users / ground plane, not vertical facade rectification. |

### 3. Facade-specific approaches

| Paper | ID | Key finding | Relevance to pc |
|---|---|---|---|
| **SF-SPA** (2025) | arXiv:2510.00797 | Geometric rectification + zero-shot semantic segmentation + LLM spatial reasoning for solar PV on facades. 6.2% area error, 100 s/building. | **Medium.** Confirms that geometric rectification is a prerequisite step in facade analysis pipelines. Uses "geometric rectification" without specifying VP method — likely classical. |
| **ControlVP** (2026) | WACV 2026 | Interactive geometric refinement of AI-generated images with consistent VPs. | **Low.** For generation, not correction. But validates VP-consistency as a quality metric. |
| **PolyRoof** (2025) | arXiv:2503.10913 | GNN + attention backbone for roof polygonization from urban imagery. | **Low.** Roof-specific, but the GNN-for-line-structure idea is transferable. |

### 4. Key insights for the pc pipeline

**A. The line detector is not the bottleneck.**
SweepLSD (2026) shows that direction accuracy of individual segments is what
matters for VP estimation, not segment count. LSD's 1600–2800 segments per image
are more than enough; the problem is *which* segments are structural vs. noise.
The gate was an attempt to solve this with a second detector — it failed because
M-LSD's guide count (47–101) is too low relative to LSD's segment count.

**B. Learned VP estimation is the next frontier.**
GeoCalib (2024) demonstrates that a CNN can estimate gravity direction + focal
length directly from an image, more robustly than classical line/VP pipelines.
This would eliminate the RANSAC search entirely and provide uncertainty estimates
for free. The pc pipeline's `model.py` (RANSAC VP fit) could become a fallback
or refinement step after a learned pre-estimate.

**C. Neural clustering beats iterative RANSAC for multi-VP scenes.**
PARSAC (2024) shows that a small network can segment line segments into VP groups
in 5 ms, replacing the iterative RANSAC loop. For architectural images with
exactly 2–3 dominant VPs (Manhattan World), this is a natural fit. The current
`model.py` RANSAC could be replaced by: (1) PARSAC-style clustering → (2) per-group
least-squares VP fit → (3) orthogonality constraint refinement.

**D. The systematic roll error (−4 to −6°) is likely a VP search issue, not a
line detection issue.**
All detectors (LSD, M-LSD, hybrid) show the same roll error on the synthetic
JPGs. This points to the RANSAC/VP-fit stage in `model.py`, not the line detector.
Possible causes: (a) the VP search grid resolution is too coarse for small roll
angles, (b) the orthogonality constraint between VPs biases the solution,
(c) the synthetic scenes have insufficient vertical line diversity.

**E. Manhattan World prior + background masking.**
The Fast Projective Rectification paper (2019) shows that accuracy ≥ SOTA is
achievable when background < 50% of image. pc's SAM2 masks module could enforce
this: mask out sky, trees, foreground objects before line detection, so the VP
search only sees facade lines.

### 5. Recommended next steps (priority order)

| # | Action | Effort | Expected gain |
|---|---|---|---|
| 1 | **Debug the roll error in model.py** — instrument the RANSAC VP search to see where the −4° bias comes in (grid resolution? orthogonality constraint? insufficient verticals?) | Low | Fixes the largest accuracy gap on all images |
| 2 | **Skip M-LSD/DeepLSD compute when detector is "hybrid"** — currently wasted since gate is removed | Trivial | Saves ~100–300 ms per image |
| 3 | **Evaluate SweepLSD as LSD replacement** — port or wrap the C/FPGA implementation; compare VP accuracy on synthetic + real facades | Medium | Better direction accuracy → better VPs, especially at low angles |
| 4 | **Prototype GeoCalib-style gravity estimation** — fine-tune a small CNN (e.g. EfficientNet-B0) to predict gravity direction + focal from facade crops; use pc's synthetic renderer for training data | High | Replaces RANSAC with a learned pre-estimate; uncertainty output for free |
| 5 | **PARSAC-style VP clustering** — replace iterative RANSAC with neural clustering + per-group LSQ fit | High | Faster, more robust multi-VP handling |
| 6 | **Background masking before line detection** — use SAM2 to mask non-facade regions (sky, trees) so the VP search sees only structural lines | Medium | Reduces noise segments; aligns with the "background < 50%" finding |

### 6. References

- SweepLSD: arXiv:2608.22086 (2026)
- LSDNet: arXiv:2209.04642 (2022)
- LAFR/RTFP: arXiv:2309.15523 (2023)
- GeoCalib: arXiv:2409.06704 (2024)
- PARSAC: arXiv:2401.14919 (2024)
- Horizon Lines in the Wild: arXiv:1604.02129 (2016)
- Fast Projective Rectification: arXiv:1912.01892 (2019)
- TopView: arXiv:2412.16229 (2024)
- SF-SPA: arXiv:2510.00797 (2025)

---

## Session log — Yaw proportion bug (2026-09-17)

### Problem

Horizontal (yaw) correction via K·R·K⁻¹ produces wrong proportions at large
angles. User reports: "horizontale entzerrung ergibt falsche proportionen",
"extremer squeeze", "bilder sind etwa 30% zu lang".

### Root cause analysis

Three interacting issues were identified and partially fixed:

1. **f/cos(θ) over-correction (REVERTED)**
   - `build()` used `K_out = intrinsics(f/cos(yaw))` to "stretch X back to true scale"
   - At 50° this stretches by 1.55× → image appears 30-55% too wide/tall
   - The pure K·R·K⁻¹ already preserves parallelism; the residual perspective
     at capped angles is mild and reads naturally
   - **Fix:** removed f/cos, use plain `G.homography(K, R)` with capped yaw

2. **Quad area explosion at large yaw**
   - At 60° yaw with f=4032px: quad area = 9× source (plain), 37× (with f/cos)
   - The `max_area_ratio` gate (4.0) would SKIP the image entirely
   - **Fix:** `_max_safe_yaw()` binary-searches the largest angle whose quad
     stays under max_area. For this image: 60° input → ~50° effective warp.
     Residual 10° remains as mild perspective.

3. **`_motif_rect` return-value mismatch (BUG)**
   - `_motif_rect` returns `(S@H, ow, oh)` — a 3-tuple
   - `plan()` unpacked it as `mx0, my0, mx1, my1 = motif` — expecting 4 values
   - Result: ValueError crash → fell through to `_whole_frame` path
   - **Fix:** `plan()` now unpacks `H_m, ow_m, oh_m = motif`

### Current state (uncommitted)

- `warp.py::build()` — capped yaw, no f/cos, plain homography
- `warp.py::_max_safe_yaw()` — binary search for max usable angle
- `warp.py::plan()` — fixed `_motif_rect` unpacking
- `warp.py::_motif_rect()` — margin_frac=0.30 (configurable via `settings.reframe_margin`)
- `config.py` — new field `reframe_margin: float = 0.30`
- `pipeline.py` — passes `max_area=settings.max_area_ratio` to `build()`
- `review.py` — all 3 `W.build()` call sites pass `max_area`
- `test_review.py` — updated `test_auto_crop_says_so` for smaller quad

### Remaining issues (user to decide)

| # | Issue | Status |
|---|---|---|
| A | **Reframe margin not wired to settings** — `_motif_rect` has `margin_frac=0.30` default but `plan()` doesn't pass `settings.reframe_margin`. Need: `plan()` → `_motif_rect(..., margin_frac=settings.reframe_margin)` | Trivial |
| B | **GUI render_after doesn't pass line_segs to plan()** — the preview uses whole-frame path, not motif reframe. User sees different result in GUI vs batch. Need: store detected segs on session, pass to `W.plan(..., line_segs=...)` in `render_after` | Medium |
| C | **PC Rectangle rubber band lag** — user reports "gummiband lagged". The motion handler redraws the full quad + rubber band per event. Need: throttle or direct canvas item update (see `tkinter-throttle-bypass-drag` skill) | Medium |
| D | **Capped yaw residual** — at 60° input, only ~50° is corrected. The remaining 10° shows as mild perspective. User may want: (a) accept it, (b) show a warning "yaw capped at 50°", (c) use PC Rectangle for full correction | Decision needed |
| E | **`_max_safe_yaw` called per build()** — 40 iterations of homography + quad area. Could cache per (w,h,f) tuple. Minor perf, not urgent | Low |

### Test results

- `test_warp.py`, `test_gui.py`: all pass
- `test_review.py`: 102/103 pass (1 updated for smaller quad)
- `test_assets.py`: 1 pre-existing failure (accuracy threshold 2.25° > 2.0° bound, unrelated)

### User quote (German, verbatim)

> "crop soll rectangle +30% sein. bzw einstellbarer wert! nicht abschneiden."
> "klickernonomie nicht toll, gummiband lagged"
> "bilder sind etwa 30% zu lang. korrektion schon 20x angefordert."
