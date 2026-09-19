---
name: tkinter-ttk-indicator-sizing
description: How to enlarge or shrink ttk checkbox/radiobutton indicator squares using clam theme options.
source: auto-skill
extracted_at: '2026-09-14T06:38:32.209Z'
---

# Sizing ttk checkbox / radiobutton indicators

## The key fact

The **clam** theme (used by this project) exposes `indicatorwidth` and
`indicatorheight` as configurable style options on `TCheckbutton` and
`TRadiobutton`. Other themes (default, aqua, vista) ignore these options.

```python
st = ttk.Style(root)
st.theme_use("clam")  # already done in _apply_theme()

# Default indicator is 13×13 px on clam.
# This project uses 24×24 (user demanded larger, complained 5× at 16):
st.configure("TCheckbutton", ..., padding=4, indicatorwidth=24, indicatorheight=24)
```

## Where it lives in this project

`gui.py`, inside the `_apply_theme()` function (the block that configures all
ttk styles). The `TCheckbutton` configure call is the single place — every
checkbox in the app inherits from it. No per-widget overrides exist.

## Sizing reference (clam defaults)

| Widget | Default indicatorwidth/height | Notes |
|---|---|---|
| TCheckbutton | 13 | square box |
| TRadiobutton | 13 | circle in a square hit-area |

**This project uses 24×24** (nearly 2× the default). The user complained five
times that 16 px was too small — do not go below 20. Keep both dimensions equal
for checkboxes (square); radiobuttons also look best square since the circle is
centred in the indicator area.

Also set `padding=4` (not the default 8) so the larger indicator doesn't bloat
the widget's total footprint.

## Pitfalls

- **Do not** try to resize via widget-level `-width`/`-height` — those affect
  the whole widget bounding box, not just the indicator glyph.
- If you switch themes at runtime (see `tkinter-runtime-theme-switching` skill),
  re-apply the indicator size after `theme_use()` because some theme switches
  reset style options.
- The `padding` option on TCheckbutton controls space *around* the indicator +
  label, not the indicator size itself.
