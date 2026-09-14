# UI skills — Batch Perspective Correction

Rules for touching `src/bpc/gui.py`. Minimal additions only; no redesigns.
Stick to what is there: one window, black theme, existing rows and frames.

## Theme (black, flat, quiet)
- The INK dict in gui.py is the single source of truth for colours:
  bg #16181c, panel #1d2025, field #101216, line #2b2f36, text #e6e8ec,
  dim #8b929c, accent #4da3ff, ok #5ac37f, warn #e0b24c, err #ef6b6b.
- Never hardcode a colour; use `INK["..."]`.
- Theme is applied once via `apply_theme()` (clam + named styles:
  Dim.TLabel, Head.TLabel, Value.TLabel, Title.TLabel, Accent.TButton).
  New widget kinds get a style there, not inline options.
- Numbers in mono (TkFixedFont); UI text in the picked family, size 10.

## Window structure (one window = header + cross)
- One root window. No Toplevel for review: `ReviewPanel` *is* the window's body
  and holds everything. The whole window is the brand header plus a four-field
  cross -- no PanedWindow, no options strip, no start/stop bar below, no Comfy
  dock (the ComfyUI settings open their own small window).
- The cross: before/after previews on top in equal columns; lower-left field =
  loader + results tree; lower-right field = controls + batch options + the
  start/cancel bar. All four fields are exactly the same size; a flat dark cross
  (`CROSS_GAP`, 20 px) separates them and a dark border (`CROSS_BORDER`, 20 px)
  rings the panel -- both from `layout.py`, colour `INK["cross"]`.
- The results tree lives in the loader field (`fill/expand`); the batch-options
  frame and the start/cancel bar sit at the foot of the controls field. Both are
  flagged `_bpc_persistent = True` so `load()`'s rebuild leaves them alone.
- Opens maximized; restore size comes from `layout.initial_window(sw, sh)`, never
  a constant. F11 toggles borderless fullscreen; there is no menu bar.

## Sizing (layout.py owns the numbers)
- **No pixel arithmetic in gui.py.** Sizes come from `src/bpc/layout.py`, which
  has no Tkinter import and is tested at five resolutions in
  `tests/test_layout.py`. Add a function there, not a magic number here.
- The four cross fields are equal by construction (`layout.perfect_cross_field`);
  a resize grows all four and re-decides nothing. There is no sash to drag and no
  stage to switch -- `_set_stage` only *re-maps* whatever the persistent layout
  hasn't mapped yet; it hides and destroys nothing, so widget state (a folder
  chosen, a detector picked) survives emptying the list.
- The loader field's *content* is compact and top-aligned inside its box; the
  controls' field may use all its space. The empty window is the same layout with
  `session=None`; `_redraw` early-returns to `_draw_empty`, which paints the
  prompt into the before slot, and that slot is the click/drop target.
- `layout.sash_position` / `tree_height` are superseded by the cross; they remain
  only for their tests. Do not reintroduce them into gui.py.
- Do **not** add Windows DPI awareness without a scaled display to test on:
  points would scale and raw pixels (pads, the 20 px cross) would not.

## Review panel rules
- Preview renders from a reduced copy; one coordinate system for the whole
  session (`apply_crop=False` + shading of the discarded part).
- Errors: full traceback in the status Text AND appended to `bpc_errors.log`
  at the project root (copyable via Notepad, always).
- Status box is read-only but Ctrl+C must work: `_status_key` lets
  state 0x4 + 'c' fall through to tk::TextCopy.
- Comfy listener registration must stay idempotent — `load()` rebuilds the
  widgets and a second registration delivers every state change twice.
- The crop rectangle is always live: eight handles (four corners, four edge
  midpoints). Edge midpoints move one edge on its axis only (`_edge_drag_rect`);
  corners are checked before edges in `_grab_handle`. Auto crop maximises the
  kept area via `warp.max_inscribed_rect`, not the plan's centred rectangle.

## Download assistance
- The "weights..." button in the options panel (next to the detector selector)
  fetches the DeepLSD weights (98 MB) into models/ via
  `deeplsd.download_weights(progress_cb)`. The button label is the progress bar;
  a second click while busy is refused (`_dl_busy`). There is no Setup menu.
- Weights that are already complete are returned as-is, never re-downloaded.

## Testing (fast, off-screen)
- App tests: `App(start_maximized=False)`, `geometry("...-4000+0")` (off-
  screen), pump with `update()` + `sleep(0.02)`, then `destroy()`. The review
  panel's widgets live on `app.review`, not `app`. New widgets to reach in that
  pattern: the angle/focal Spinboxes (`_slider`, bound to the same DoubleVar as
  their Scale), the mark-kind Combobox (`v_mark_kind` → "vertical"/"horizontal";
  a ttk.Combobox has **no** `-command` -- bind `<<ComboboxSelected>>`), the flex
  rulers (drawn by `_draw_rulers`, tagged `"ruler"`, gated on grid **and** pixel
  mode) and the loupe (`_loupe_show` raises via `w.tk.call("raise", w._w)` -- a
  Canvas's own `lift()`/`tkraise()` are *item* commands and will throw).
- Real photos live in tests/assets/; keep a run under ~30 s by picking a few
  small images, not the whole suite.
- `python -m py_compile` before running anything; run test modules
  individually (`tests/run_tests.py <module>`) — the full suite is slow.

## Change discipline
- Add, don't restructure: new controls go beside existing ones in their row
  or frame.
- Every UI change ships with an off-screen test that loads a real image and
  asserts the status text contains no "failed".
