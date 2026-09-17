# QWEN.md — Agent instructions (this session)

### Context window

- **262k tokens** configured (user raised from default on 2026-07-15).

## SCALING RULE (global, do not break)

The preview pipeline has exactly **one** scale chain. Every overlay, marker,
ruler, or guide that is drawn onto the `render_after` output must use this
chain — never invent a second one:

```
original image (self.w × self.h)
    │  × s  where s = min(1, max_edge / max(w, h))
    ▼
preview space (sw × sh)          ← H_total maps FROM here
    │  H_total  (W.build → W.plan bakes in crop/fit)
    ▼
final output (ow × oh)           ← this IS the array that _fit returns
```

**Rules:**
1. To project a point from the original image onto the `render_after` output:
   multiply by `s`, then apply `H_total` via `cv2.perspectiveTransform`.
   **Do NOT** multiply by any additional scale factor afterwards — `H_total`
   already lands in final-output pixel coordinates.
2. `_fit()` at the end of `render_after` resizes the array to fit `max_edge`.
   Because `W.plan` already accounts for this (the output dimensions `ow, oh`
   are post-fit), the perspectiveTransform result is directly drawable on the
   final array. No extra `fit_s` factor.
3. In the GUI (`_redraw`), the before/after photos are fitted to their canvas
   boxes by `_to_photo()`. The scale from original→canvas is:
   `canvas_scale = photo_width / self.session.w` (for before) or
   `photo_width / out_w` (for after). Use these for any canvas-space overlay.
4. **Never** mix the two coordinate systems. A point in "original pixels" must
   be converted to exactly one target space before drawing.

This rule was violated ~50 times during the H-Marker session (2026-09-17),
each time causing offset/scale bugs in Q2 marker projection. The fix is always
the same: scale by `s`, warp by `H_total`, done.

## Session changes (2026-09-17, uncommitted)

### Loupe precision
- **Alt-damping:** Hold Alt while dragging → loupe crop centre follows cursor at `1/LOUPE_MAG` rate. Crosshair turns cyan while active. `_loupe_center` tracks the damped point; reset on show/hide/rebuild.
- **Instant press render:** `_loupe_move(event)` called immediately after `_loupe_show()` in both press paths (new mark + endpoint drag). No more empty glass until first motion.
- **Alt state forwarding:** `event_generate` in `_forward` now passes `state=getattr(event, "state", 0)` so Alt survives when the pointer is over the glass (which overlaps c_before).

### Mark delete handle
- X-cross instead of plus in `_draw_delete_handle` (plus reads as "add"; this removes).

### Status label (F3)
- `_set_status` / `_set_status_extra` now write to a real `lbl_status` ttk.Label (Dim style, below the static hint, above the action row). All ~20 interaction hint texts are visible.

### SAM2 box fix
- `_on_sam_release` stores both `_sam_box` (normalised, for drawing) and `_sam_box_px` (pixel ints, for SAM2). `_on_sam_apply` sends `box_px` to `run_subprocess`. SAM2 expects pixel coords `[x0,y0,x1,y1]`; normalised 0-1 values were interpreted as sub-pixel → whole-frame selection.

### Horizontal auto (yaw)
- `min_horizontal_support`: 0.3 → **0.15** (config.py)
- `n_hypotheses` for horizontal VP search: 3 → **6** (model.py)
- `_plausible_horizontal_rows` angle filter: 45° → **70°** (vanishing.py)
- Yaw activates when `correct_horizontal=True` AND `len(horiz) >= 2` AND dominant VP support ≥ 0.15.

### Mask overlay controls (Q1 top-right)
- Colour swatch button + opacity slider in the before-pane overlay bar (`_ovbar`).
- Custom 4×4 colour picker (16 vivid presets, square Canvas swatches, opens directly below the swatch). Tkinter `colorchooser` is broken on Windows.
- `session.mask_color` (BGR tuple) read by `render_before` → `tint_mask`.
- Default mask alpha: 0.28 → **0.60**.
- Mask brush default width: 10 → **60px**.

### Q2 crop dimensions
- `_after_dims` label (Dim style, top-right of after pane) shows live pixel dimensions of the crop rect. Updated on every `_refresh_crop` and initial load.

### Menu theming
- `TMenu` ttk.Style configured with theme palette (`panel`/`text`/`line`) instead of OS default black.

## Prior task: read-only debug pass (complete)

The read-only debug pass is **complete**. All 27 source files in `src/pc` plus
entry points were reviewed. Findings are in `debug.md` (1124 lines). Several
findings have since been implemented; the remainder are open proposals awaiting
the user's decision on scope and priority.

### Completed: Guide system rewrite (2026-09-16)

The guide system in `gui.py` was rewritten to match the reference implementation
(`Perspective-Correction - Kopie`). All changes verified by 31 GUI tests passing:

- **Cross-pull creation:** `_on_cross_press` sets `_cross_pull` kind; drag shows
  dashed preview (`_cross_preview`); drop over image commits via `_after_pos`,
  discards if pointer not over image.
- **Canvas-based guide interaction:** `_after_guide_at(x, y, iw, ih)` with 15 px
  grab distance; `_guide_drag = (kind, idx)` tuple; no clamp during drag;
  clamp-or-delete on release with 20 px margin.
- **Border-zone creation from Q2:** clicking in the 15 px border zone of the
  after-canvas creates a new guide (v if left/right zone, h if top/bottom).
- **Crop handle dominance:** `_grab_handle` is checked **first** in
  `_on_crop_press`, before any guide interaction. All **8** handles (4 corners +
  4 mid-edges) take priority over guides.
- **Cursor swap:** v-guides → `sb_h_double_arrow`, h-guides →
  `sb_v_double_arrow` (user preference).
- **Tag & colour:** `"after_guide"` tag, `GUIDE_GREY = "#9aa0a8"`.

### Implementation status (verified against code, 2026-09-16)

| ID | Description | Status |
|---|---|---|
| F1 | `fill_max_share` raise → warn + note | **Done** — `inpaint.py:673` returns `(bgr, note)` |
| F2 | Auto-crop threshold too tight for review | **Partially done** — `review.py` uses `auto_crop_threshold = max(crop_max_loss, 0.30)`; no `interactive` flag in config (batch path still 5%) |
| F3 | Size warning when output > 1.5× input | **Not done** — `_set_status` is still a `pass` no-op |
| F4 | Collapse three crop gates to one | **Done** — single `crop_max_loss` in config; review bumps to 30% locally |
| F5 | Save button never disabled by warnings | **Not done** — no invariant test, no audit of `.config(state=)` calls |
| P1 | Yaw-only clamp warns instead of refusing | **Not done** — `pipeline.py:191` still refuses on any `clamped=True` |
| P2 | Surface support-gate decision in diagnostics | **Not done** — no `yaw_skipped` key in model.py |
| P3 | Display multiple horizontal VPs as markers | **Not done** |
| P4 | Two-facade warning in status area | **Not done** |
| P5 | Document + test manual-yaw bypass | **Partially done** — docstring added to `current_yaw()`; no dedicated test |
| gui split | 12-file composition split of gui.py (4864 lines) | **Not done** — still one file |
| Shootout | 20-image benchmark suite | **Not done** — no `tests/shootout/` directory |
| cli MED | `isatty()` gate blocks piped double-click | **Not done** — `cli.py:651` unchanged |

### Per-file findings (LOW severity, unaddressed)

These are in `debug.md` under each `## src/pc/<file>` section. Highlights:

- `geometry.normalize_vp` — zero-norm fallback points down, not up (harmless)
- `model.focal_from_horizon` — dead no-op line with misleading comment
- `lines.prepare()` — empty `masked_out` has shape `(0,)` not `(0,4)` (MED)
- `review.py` reaches into `preview._draw_lines` private API
- `gui.py` — ~15 inline hex literals bypass the INK theme dict
- `gui.py` — `cb_comfy_models` initialised twice (line 3832 dead; line 4082 authoritative)
- Colour systems in `preview.py` and `scheme.py` diverge (same semantic, different BGR values)

### Write permissions (strict)

| File | Permission |
|---|---|
| `debug.md` | **WRITE** — findings list + proposals |
| `QWEN.md` | **WRITE** — this plan/instructions file |
| everything else | **READ-ONLY** unless the user explicitly requests a change |

### What a finding is

A finding names **file + symbol/line**, states the problem, and gives a suggested
fix. Severity: **HIGH** = likely wrong behaviour, **MED** = latent risk,
**LOW** = style/clarity/minor. A finding is a pointer, not a verdict.

### External tools

- **Firecrawl API key:** `fc-72208db35c9d4c5b8996b00e0adff3a9` — use for web research when `web_fetch` gets 403/404. Endpoint: `https://api.firecrawl.dev/v1/scrape` (POST, header `Authorization: Bearer <key>`).

### Next steps (user to decide)

Open items ranked by impact if the user wants to continue:

1. **F3 + gui MED #1** — wire `_set_status` to a real status label (unblocks F3, P2 display, P4 display, and all interaction feedback)
2. **P1** — yaw-only clamp → warn not refuse (one block in pipeline.py)
3. **P2** — `yaw_skipped` diagnostic in model.py (small, unblocks P4)
4. **F5** — Save-always-enabled test + audit
5. **gui.py split** — 12-file composition (large, structural; do after the above so controllers are stable)
6. **Shootout suite** — 9–10 h effort; independent of the above
