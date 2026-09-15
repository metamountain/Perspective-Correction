# UI skills — Perspective Correction

Rules for touching `src/pc/gui.py`. Minimal additions only; no redesigns.
Stick to what is there: one window, black theme, existing rows and frames.

## Theme (black, flat, quiet -- but switchable)
- The INK dict in gui.py is the single source of truth for colours:
  bg #16181c, panel #1d2025, field #101216, line #2b2f36, text #e6e8ec,
  dim #8b929c, accent #4da3ff, ok #5ac37f, warn #e0b24c, err #ef6b6b. It also
  doubles as the "Minimal Black" entry of `THEMES`, a dict of named
  alternative palettes (C64, Amiga 500, Light, Phosphor);
  the header's theme combobox calls `App._switch_theme`, which does
  `INK.update(new)` in place, re-runs `apply_theme()` for the ttk styles, then
  walks the tree with `_retint_bg` to fix up plain `tk` widgets (Canvas,
  Label, Button) whose bg/fg still hold an *old* palette's colour, matched by
  value. That match-by-value is exactly why hardcoding a colour is banned, not
  just untidy: a literal equal to an old INK value gets swapped by accident on
  the next theme switch, and one that isn't never gets swapped at all.
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
  start/cancel bar. All four fields are exactly the same size -- built as a
  single 2x2 `grid` on `ReviewPanel` itself (`rowconfigure`/`columnconfigure`
  with `weight=1` and a shared `uniform` group per axis), each cell padded by
  half `CROSS_GAP` on its inner sides and full `CROSS_BORDER` on its outer
  ones so the panel's own dark background (`INK["cross"]`) shows through as a
  flat 20 px cross and a 20 px ring. `layout.perfect_cross_field` computes
  the same arithmetic for `test_layout.py` but gui.py does not call it --
  the grid's `uniform` groups are what actually keep the four fields equal.
- **The cross itself is a live drag surface, not just a divider.** Every child
  canvas eats its own pointer events, so a `<Motion>`/`<Button-1>` that
  reaches the panel (`ReviewPanel.bind`, not a child's) is over the gutter or
  the border and nowhere else -- no hit-testing needed. That is what lets you
  drag a reference guide out of the black cross the way you'd drag one off a
  ruler: `_on_cross_press`/`_on_cross_pull`/`_on_cross_drop` on the panel;
  `_cross_orientation` decides horizontal vs. vertical by which edge/centre
  the press is nearest; `_after_pos` converts the release point into an
  offset inside the after image; `_cross_preview` draws the dashed follower.
  A guide dropped back over the cross (not on the after pane) is discarded,
  same as dragging one back onto an editor's ruler.
- The results tree lives in the loader field (`fill/expand`); the batch-options
  frame and the start/cancel bar sit at the foot of the controls field. Both are
  flagged `_bpc_persistent = True` so `load()`'s rebuild leaves them alone.
- Opens maximized; restore size comes from `layout.initial_window(sw, sh)`, never
  a constant. F11 toggles borderless fullscreen; there is no menu bar.

## Sizing (layout.py owns the numbers)
- **No pixel arithmetic in gui.py.** Sizes come from `src/pc/layout.py`, which
  has no Tkinter import and is tested at five resolutions in
  `tests/test_layout.py`. Add a function there, not a magic number here.
- The four cross fields are equal by construction (see "Window structure"
  above); a resize grows all four and re-decides nothing. There is no sash to
  drag and no stage to switch -- `_set_stage` only *re-maps* whatever the
  persistent layout hasn't mapped yet; it hides and destroys nothing, so
  widget state (a folder chosen, a detector picked) survives emptying the list.
- The loader field's *content* is compact and top-aligned inside its box; the
  controls' field may use all its space. The empty window is the same layout with
  `session=None`; `_redraw` early-returns to `_draw_empty`, which paints the
  prompt into the before slot, and that slot is the click/drop target.
- `layout.sash_position` / `tree_height` are superseded by the cross; they remain
  only for their tests. Do not reintroduce them into gui.py.
- Do **not** add Windows DPI awareness without a scaled display to test on:
  points would scale and raw pixels (pads, the 20 px cross) would not.

## Both preview panes are the same picture at the same scale
- Both the before and after canvases fit the **full** field, no inset for
  either -- `_redraw` (before) and `_show_after` (after) size against the
  literal identical box (`max(1, canvas.winfo_width())`,
  `max(1, canvas.winfo_height())`). This is load-bearing, not incidental: the
  same photograph must never appear at two scales side by side, and shrinking
  one box by a border reservation while the other stays full-size is exactly
  how that guarantee breaks (it did, briefly, when guides ate a
  `2 * RULER_MARGIN` inset from each side -- costs ~11.5% of the after pane's
  area against the before pane at 1920x1200). Guides live in the black cross
  *outside* the images (see "Window structure"), never in a strip carved out
  of either pane.
- Reference guides on the after pane draw as **plain grey hairlines** --
  `GUIDE_GREY = "#9aa0a8"`, width 1, no ticks, no numeric label
  (`_draw_after_guides`, tag `after_guide`; the in-flight drag preview is the
  same colour dashed, tag `guide_preview`). A guide is not a ruler *scale*: it
  exists only to be laid against an edge to see whether that edge is
  parallel, so a tick-and-label treatment only competes with the photograph.
  Drawn on the canvas, never composited into the frame -- `save()` cannot see
  them. `RULER_MARGIN` (20 px) still matters for the static border strip
  around the after image and for the ROI-x rulers on the before image
  (`_draw_roi_rulers`, its own separate blue/ticked style) -- just not for
  shrinking either preview.

## Review panel rules
- Preview renders from a reduced copy; one coordinate system for the whole
  session (`apply_crop=False` + shading of the discarded part).
- **There is no status box.** `_set_status`/`_set_status_extra` are kept as
  no-ops purely so the ~25 existing call sites stay safe -- do not read
  anything into them, and do not add a new one expecting it to show. A caught
  exception during a redraw still writes its full traceback to
  `pc_errors.log` at the project root (`_redraw`'s `except Exception` block);
  that log is the only place an error surfaces now. An off-screen test cannot
  assert against status text any more (see "Testing" below).
- Comfy listener registration must stay idempotent — `load()` rebuilds the
  widgets and a second registration delivers every state change twice.
- The crop rectangle is always live: eight handles (four corners, four edge
  midpoints). Edge midpoints move one edge on its axis only (`_edge_drag_rect`);
  corners are checked before edges in `_grab_handle`. Auto crop maximises the
  kept area via `warp.max_inscribed_rect`, not the plan's centred rectangle.
- The mask-brush hover cursor is a **double ring**, white outside and black
  inside (`_brush_cursor_indicator`) -- a single black ring is invisible
  against a dark facade or against `INK["field"]` itself; one of the two
  rings always contrasts with whatever is underneath. This is separate from
  `_draw_stroke_preview`, the solid dark-red line drawn only while an actual
  paint stroke is being dragged.
- Manually placed control lines (marks) can be **either** two-click or a
  rubberband drag -- they are the same gesture with and without movement, not
  two modes. A press starts `_pending_mark`; if the pointer moves before
  release, `_mark_rubber` previews a dashed line and `_mark_commit` places it
  on release; if it never moves, release falls through to `_click_mark`,
  which is what keeps "click an existing mark to delete it" working -- that
  gesture has no other home, and losing it was the trap this is written down
  to avoid. Placed marks also have draggable endpoints (`_on_mark_drag`) for
  refinement.
- Planar rectification is **gone from the UI entirely, deliberately, twice**
  -- not just moved. A click-to-place quad is a drawing tool, not a
  correction setting, so it first left the lower-right controls for the
  picture-corner tool palette; it then left the palette too, because a
  vertical mark and a horizontal mark say the same thing about which plane a
  facade's lines belong to as four dragged corners, with far less UI, using a
  tool that already exists. `v_planar` is still created (`_build`, made once
  like `v_mark`/`v_stroke`/`v_sam`, guarded by
  `getattr(self, "v_planar", None) is None`) and every code path that reads
  it (`_draw_planar_quad`, `_on_planar_drag/_release`, the planar branch of
  `_redraw`/`_save`) still exists and still runs correctly if the variable is
  ever set -- there is simply no widget left that sets it. `planar.py` and its
  tests are untouched in the tree. Do not reintroduce a Planar toggle without
  checking with the user first.
- The fill row has no pad-colour picker, swatch or "edge" button, nor the
  `colorchooser` import they needed. `--pad` / `Settings.pad` are untouched,
  they just have no widget: `pad` only shows through when fill is `none` (the
  default is `telea`), so three controls were spending the busiest row in the
  window on something almost nobody sees. If a pad-colour control is wanted
  back, design it fresh rather than reviving the old one -- its tooltip
  described a width it never actually set.

## Tools live on the picture (the lower-left tools field and the palette)
- The before pane's top-left corner hosts a small vertical palette of toggle
  **buttons** (`indicatoron=False` `tk.Checkbutton`s, not checkboxes -- a tool
  is either in your hand or it is not) built by `_build_tool_palette`, called
  from `_build` once the variables it toggles exist. It currently holds
  exactly one tool, **Mark** (`│`, toggles `v_mark`) -- Planar and Mask brush
  are not here (see above; Mask brush lives in the lower-left tools field).
- The lower-left tools field (`_build_tools`, built once by `App._build` into
  a persistent frame -- it must survive every `load()`) holds, in order: the
  line detector combobox + `weights...` button + the ROI-x checkbox and its
  two draggable rulers on the before pane; **Mask brush** (a checkbutton
  bound to `v_stroke`) with its pen-width spinbox, and a **SAM** button
  (`v_sam`, box-drag prompt --
  drag a box over the subject on the before pane to segment it, right-click
  clears the prompt); **Mark vertical** + its vertical/horizontal kind combo
  + **Strike slanted**; and the mask-mode row (off/file/birefnet/gdino,
  BiRefNet model picker, Mask Apply, Clear Mask, mask-marks-KEEP invert,
  opacity slider, gdino prompt entry).
- `v_planar`, `v_mark`, `v_mark_kind`, `v_stroke`, `v_stroke_w` and `v_sam`
  are all **made once**, guarded by `if getattr(self, "v_<name>", None) is
  None:` in `_build` (which re-runs on every photograph) rather than being
  recreated there. `_build_tools` (the lower-left field) and
  `_build_tool_palette` (the picture-corner buttons) are both built a single
  time by contrast, so a variable rebuilt on every `_build` would hand their
  widgets a stale one -- ticking a box would set a variable nobody reads and
  the tool would silently stop working from the second photograph onward.
  This has bitten twice for real (the mask brush once, the palette buttons
  once); if you add a new toggle that a tools-field or palette widget binds
  to, make it this way from the start.

## Download assistance
- The "weights..." button lives in the lower-left tools field, beside the
  line detector combobox, not in the batch-options panel. It fetches the
  DeepLSD weights (98 MB) into models/ via
  `deeplsd.download_weights(progress_cb)` (`App._download_models`); the button
  label is the progress bar; a second click while busy is refused (`_dl_busy`).
  There is no Setup menu.
- Weights that are already complete are returned as-is, never re-downloaded.

## Testing (fast, off-screen)
- App tests: `App(start_maximized=False)`, `geometry("...-4000+0")` (off-
  screen), pump with `update()` + `sleep(0.02)`, then `destroy()`. The review
  panel's widgets live on `app.review`, not `app`. Import as `from pc.gui
  import App` -- the package is `pc`, not `bpc`.
- There is no status Text to assert against (see "Review panel rules"): a
  change lands clean if it loads and pumps without raising and without
  writing to `pc_errors.log`, not by grepping rendered status text for
  "failed".
- New widgets worth knowing the quirks of: the angle/focal Spinboxes
  (`_slider`, bound to the same DoubleVar as their Scale), the mark-kind
  Combobox (`v_mark_kind` -> "vertical"/"horizontal"; a ttk.Combobox has
  **no** `-command` -- bind `<<ComboboxSelected>>`), the ROI-x rulers
  (`_draw_roi_rulers`, draggable, tag `roi_ruler`), the after-pane guides
  pulled from the cross (`_draw_after_guides`, tag `after_guide` -- see
  "Window structure"; the old tick-and-label flex ruler this superseded,
  `layout.ruler_ticks`, is no longer called from gui.py at all, only from its
  own test), and the loupe (`_loupe_show` raises via `w.tk.call("raise",
  w._w)` -- a Canvas's own `lift()`/`tkraise()` are *item* commands and will
  throw).
- Real photos live in tests/assets/; keep a run under ~30 s by picking a few
  small images, not the whole suite.
- `python -m py_compile` before running anything; run test modules
  individually (`tests/run_tests.py <module>`) — the full suite is slow.

## Change discipline
- Add, don't restructure: new controls go beside existing ones in their row
  or frame.
- Every UI change ships with an off-screen test that loads a real image and
  pumps the event loop, asserting on real state (a variable, a widget's own
  option, `pc_errors.log`) rather than on status text, which no longer exists.
