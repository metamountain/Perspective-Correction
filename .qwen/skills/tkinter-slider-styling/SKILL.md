---
name: tkinter-slider-styling
description: How to make tk.Scale sliders look like minimal round-dot thumbs on a slim track in this project's dark theme.
source: auto-skill
extracted_at: '2026-09-13T20:25:30.970Z'
---

# Tkinter slider styling (round-dot thumbs)

## The constraint

`ttk.Scale` (themed) does **not** support `-width` or `-sliderlength`. Only the
classic `tk.Scale` widget does. That is why every slider in this project is a
`tk.Scale`, not a `ttk.Scale` — and why the TScale style in `apply_theme()`
exists only for colour consistency, not sizing.

## Making the thumb read as a round dot

The classic `tk.Scale` draws its thumb as a rectangle whose:
- **height** (perpendicular to track) = `-width`
- **length** (along the track) = `-sliderlength`

To make it look circular rather than rectangular, set `sliderlength ≈ width`:

```python
# layout.py
SLIDER_WIDTH = 28   # px, trough thickness (perpendicular to track)
SLIDER_THUMB = 30   # px, thumb length along track (~circular with width)
```

A thumb much longer than the track width (e.g. 60 on a 36-wide track) reads as
a fat rectangle — "dumb." A near-square ratio reads as a dot.

## Colours to match the theme

The classic widget cannot use named ttk styles, so colours are passed inline
and must mirror the `TScale` style configured in `apply_theme()`:

```python
tk.Scale(parent, ...,
    bg=INK["bg"],            # widget background (matches window)
    troughcolor=INK["field"],# track groove (matches input fields)
    highlightthickness=0,    # no focus ring
    bd=0,                    # no border
    showvalue=0,             # no numeric label at the end
)
```

## Where the constants live

All sliders in `gui.py` reference `layout.SLIDER_WIDTH` and `layout.SLIDER_THUMB`
(never hardcoded), so changing the two constants in `layout.py` updates every
slider at once: roll, pitch, focal, yaw, mask-opacity.

## Testing

No dedicated test for slider appearance; verify visually or via an off-screen
screenshot if needed. The layout constants are covered by `tests/test_layout.py`
only insofar as they feed into grid sizing (`SLIDER_MIN`).
