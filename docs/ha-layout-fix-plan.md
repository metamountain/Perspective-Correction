# Fix plan — layout + horizontal auto + title bar/fullscreen (2026-10-20)

Working folder: `D:\_Code\Perspective-Correction`. Edit only `src/pc/gui.py`,
`src/pc/review.py`, `src/pc/layout.py`, `tests/test_gui.py`.

## HARD RULES (read first)

1. **Test before you report.** After every task run the probe (below) and
   `python tests/run_tests.py test_gui`. `py_compile` alone is NOT a test.
2. **Never mix coordinate spaces.** `_before_off`, `_after_off`,
   `_before_scale` are used for hit-testing. Change only how the OFFSET is
   computed; never change a scale or a conversion.
3. **No pixel numbers in `gui.py`, no bare `#rrggbb`.** Sizes go in
   `layout.py`, colours come from `INK` / `OVERLAY`.
4. **Every state change shows a status prompt** (`self._set_status(...)`)
   saying what happened and how to reverse it.
5. Do not commit. Do not touch `D:\Batch-Perspective-Correction` or
   `D:\Coding\Batch-Perspective-Correction`.

## Measured state (architect, 2026-10-20, real window, `Platte_1.jpg`, strip 0–48 %)

- HA warp itself works: `_rectify_active()` = True, residual slope after the
  warp = vertical median 0.48°, horizontal median 0.59°. Do NOT change
  `warp.rectify` or `ReviewSession._build_H`.
- Mode switch shows **Auto** while HA is on (`v_corr_mode == "auto"`).
- Status line still prints `AUTO roll=-0.84 pitch=+10.02 … f=30mm`, but roll,
  pitch and focal do not enter the HA warp.
- `v_autocrop` is True, yet after `_on_horizontal_toggle()` +
  `_apply_strip()` the crop stays `None` (the frame covers the whole
  smeared canvas). Pressing `_auto_crop()` by hand gives
  `(0.214, 0.483, 0.987, 0.778)`.
- Pictures are vertically centred in their fields: ~110 px of empty black
  above the before picture and ~145 px above the after picture at 1920x1080.
  The "before Platte_1.jpg 1320×742" label sits at the bottom of the field,
  outside the picture.
- Startup is fullscreen (`gui.py:6065`); F11 toggles and ESC exits
  (`gui.py:4726-4772`). But `_enter_fullscreen` shows no prompt, and leaving
  fullscreen never re-applies `_dark_titlebar(self)`, so the Windows title
  bar comes back light.

## Tasks (in this order)

### T1 — Title bar black + fullscreen prompts (`gui.py:4744-4772`)
- `_enter_fullscreen`: after setting the attribute, call
  `self._set_status("fullscreen -- F11 or ESC exits")`. `_set_status` may
  not exist on App yet; if not, use `self.review._set_status(...)` and guard
  it with `getattr(self, "review", None)`.
- `_toggle_fullscreen`, when it LEAVES fullscreen: call `self._maximize()`
  (which already calls `_dark_titlebar`). Then also
  `self.after(100, lambda: _dark_titlebar(self))` and
  `self.after(400, lambda: _dark_titlebar(self))`, because Windows re-creates
  the caption after the state change. Status:
  `"windowed -- F11 for fullscreen"`.
- When it ENTERS fullscreen: same prompt as `_enter_fullscreen`.
- `_esc`: keep it as it is, but delete its own `_set_status` line (the toggle now prompts).

### T2 — The mode switch IS the HA switch (`gui.py:1382`, `:1589`, `:2124`, `load` at `:1004`)
- `_show_mode`: after `tkraise`, if mode == `"horizontal"` and
  `not self.v_correct_horizontal.get()`, set it True and call
  `self._on_horizontal_toggle()`. If mode == `"auto"` and it is True, set it False
  and call `self._on_horizontal_toggle()`. `"manual"` changes nothing.
  Guard against recursion with a flag `self._syncing_mode`.
- `_on_horizontal_toggle`: at the end, unless `self._syncing_mode` is set, put
  `v_corr_mode` to `"horizontal"` if on, else `"auto"` (only if it is not
  `"manual"`), then `self._mode_frames[...].tkraise()`.
- In `load()`, after the session exists: set `v_corr_mode` from
  `settings.correct_horizontal` the same way and raise that frame.
  Variables are made once (a `getattr(..., None) is None` guard), never rebuilt.

### T3 — Auto crop follows HA and the strip (`gui.py:2750 _autocrop_follow`)
- Find out why `_autocrop_follow` does not fire after
  `_on_horizontal_toggle()` and `_apply_strip()`. Add a call to
  `self._autocrop_follow()` at the end of both (after the refit and after
  the strip is applied).
- Check: the probe prints `crop` != None right after the toggle and the strip,
  without `_auto_crop()` having been pressed.

### T4 — Status line names the warp (`review.py:1462 status_text`)
- When `self._rectify_active()` is True, the first line becomes
  `HORIZONTAL AUTO (warp)  facade -> rectangle  x-scale {W.yaw_x_scale(yaw):.2f}`
  and drops roll, pitch and focal from that line. Every other line stays
  unchanged.

### T5 — Pictures to the top edge, title inside the picture (`gui.py:3015`, `:3859`, `:3880`)
- `_before_off`: keep the x centring and set y to `0`. Same for `aoy` in the
  after pane (`aoy = 0`). Nothing else changes, because every hit-test reads
  these two tuples.
- Move the `before …  W×H` text: anchor `"nw"`, at
  `(self._before_off[0] + layout.LABEL_INSET_PX, self._before_off[1] + layout.LABEL_INSET_PX)`.
  Add `LABEL_INSET_PX = 6` to `layout.py`. Before it, draw a
  `create_rectangle` backing behind the text using `INK["bg"]`, sized from
  `bbox` of the text item, with the text raised above it.
- Do the same for the after pane's size label (`f"{ow}×{oh}  {cw}×{ch}"`,
  bottom-left today): put it at the after picture's top-left corner with the same inset. If it
  collides with the `Check lines` toggle, put it at the bottom-left of the
  PICTURE (not of the field) instead.

### T6 — Status text out of Q4, faint overlay in the picture field (user, 2026-10-20: "Müll-Info")
- `gui.py:1376-1380`: do NOT pack `self.lbl_status` any more (delete the
  `.pack(...)` and the `<Configure>` bind; the strip then sits directly
  under nothing and Q4 shrinks). Keep the attribute alive as a plain
  string: `self._status_text = ""`.
- `_set_status` (`gui.py:4569`): store the text in `self._status_text` and
  call `self._draw_status_overlay()`. `_set_status_extra` stays a wrapper.
- New `_draw_status_overlay()`: `self.c_after.delete("status_ov")`, then
  `create_text` on `c_after`, tag `"status_ov"`, anchor `"sw"`, at the
  bottom-left of the after FIELD (below the picture, in the empty black),
  x = `layout.LABEL_INSET_PX`, y = canvas height − `layout.LABEL_INSET_PX`,
  `width` = canvas width − 2*inset, font size `layout.CTRL_FONT_PT`,
  colour `OVERLAY["status_faint"]`. Add to the `OVERLAY` table:
  `"status_faint": "#3a3f47"` (very low contrast on the dark ground). Call
  it also at the end of the after-pane redraw (after `c_after.delete("all")`
  wipes it), so the text survives a redraw.
- If the after picture covers that corner, the text is drawn over the
  picture anyway. Do not add a backing.
- Check in the screenshot: no status text in Q4, the faint text is visible in
  the after field, and the pictures got taller.

### T7 — Auto crop keeps headroom above the eave (user, 2026-10-20: "ja")
Measured on `csm.jpg` (HA on): the fill-free crop starts at canvas y=171 px,
the eave (topmost long horizontal) lies at y=190.6 → only 20 px above the roof,
no sky. On `redbarn.JPG` the crop starts at y=124 and the eave at y=461.5, so it
already has room and must NOT change.

- `warp.py`: add constants `EAVE_MIN_LEN = 0.15` (fraction of canvas width)
  and `EAVE_HEADROOM = 0.10` (fraction of the crop height below the eave).
  Add `def eave_y(H_total, horiz_segs, ow, x0, x1)`: map each segment's two
  end points with `G.apply_h(H_total, ...)`; keep the ones whose mapped length is
  `>= EAVE_MIN_LEN * ow` and whose mid-x lies in `[x0, x1]`; return the
  smallest mid-y, or `None` if none is left.
- `review.py ReviewSession.auto_crop`: after the inset (just before
  `return self.set_crop_rect(...)`), if the fill is active
  (`self.settings.fill` not in `(None, "", "none")`): call
  `W.eave_y(H_total, np.asarray(self.horiz.seg, float)[:, :4] * (self.w / self.gray.shape[1]), ow, x0, x1)`.
  If it returns `ye` and `y0 > ye - EAVE_HEADROOM * (y1 - ye)`, set
  `y0 = max(0.0, ye - W.EAVE_HEADROOM * (y1 - ye))`. Only ever LOWER `y0`
  (more room). Never touch x0, x1, y1.
- Test in `tests/test_warp.py`: `eave_y` on a hand-made H = identity with
  three segments (one short high one, one long one at y=50, one long one at y=80)
  returns 50, and `None` when all are short.
- Verify: `crop` for csm via `probe_crop.py` → the top crop edge
  `crop[1]*canvas_h` is about 132 px (was 171); redbarn's top crop edge is unchanged
  (≈124 px). Run `python tests/run_tests.py test_warp test_review`.

### T8 — The loupe sits on the TARGET image and follows the cursor (user, 2026-10-20)
Today `_loupe_park` (`gui.py` ~2605) parks the glass at a fixed spot, the black
gap between the two panes. Wanted: while the cursor moves over the before
picture, the glass sits centred on the SAME point in the after picture (the
point mapped through the correction) and moves with it. Content stays as it is
(full-resolution source pixels around the cursor), so placing marks keeps working.

- `review.py render_after` (~1299): remember how a source pixel lands on the
  returned array. Add `self.preview_map = None` at its start, then:
  - no-correction branch (`return _fit(crop(self.bgr), max_edge)`): set
    `self.preview_map = ("scale", fit)` where `fit = returned_array.shape[1] / self.w`.
  - warp branch: after `fit_s` is computed, set
    `self.preview_map = ("warp", s, H_total, fit_s)`.
  - planar branch and the `planned is None` fallback: leave it `None`.
  Add a method `def source_to_preview(self, x, y)`: for `"scale"` return
  `(x*fit, y*fit)`; for `"warp"` apply `H_total` to `(x*s, y*s, 1)`, divide by
  w, multiply by `fit_s`; return `None` if the map is None or w <= 1e-9.
- `gui.py _loupe_move`: after computing `px, py` (full-res source px under the
  cursor), call `p = self.session.source_to_preview(px, py)`. If `p` is not None:
  `ax = p[0] + self._after_off[0]`, `ay = p[1] + self._after_off[1]` (after-canvas
  coords), convert to panel coords with
  `self.c_after.winfo_rootx() - self.winfo_rootx()` (same for y), place the glass
  centred there (`x - size//2`, `y - size//2`), clamped so it stays inside the
  `c_after` canvas rectangle. If `p` is None: keep `self._loupe_park(size)`.
  Also use the same position in `_loupe_show` when the session can map;
  otherwise park.
- Coordinate rule: `_after_off` and the preview array are in after-CANVAS pixels;
  `px, py` are FULL-RES source pixels. Do not mix them up, and do not change any
  existing scale.
- Check: with HA on `Platte_1.jpg`, hovering over a window corner on the before
  pane puts the glass centre on that same window corner in the after pane (take
  a screenshot with `event_generate("<Motion>", x=..., y=...)` on `c_before`
  while the mark tool is on, and look at it).

## Verification (run all of it, paste the output)

```
python -m py_compile src/pc/gui.py src/pc/review.py src/pc/layout.py
python "C:/Users/omen/AppData/Local/Temp/claude/D---Code-Perspective-Correction/9d5f5afc-33a5-4fbd-ae6f-03c732d60cab/scratchpad/probe_crop.py" tests/assets/Horizontal/Platte_1.jpg
python "C:/Users/omen/AppData/Local/Temp/claude/D---Code-Perspective-Correction/9d5f5afc-33a5-4fbd-ae6f-03c732d60cab/scratchpad/probe_ha.py" tests/assets/Horizontal/Platte_1.jpg 0.0 0.48 after_fix
python tests/run_tests.py test_gui
python tools/debug_ui.py
```

Expected: `crop before` is not None; `v_corr_mode horizontal`; residuals
unchanged (≈0.48 / 0.59); `test_gui` 0 failed; `debug_ui` ends
`FAILURES: none`. Look at `after_fix.png` (scratchpad): both pictures touch the top of their
field, and the title sits inside the picture.

## Reminder

Test before you report. Do not change scales or conversions, only offsets.
Report what you changed (file:line), the probe output, and any test that
failed, verbatim.
