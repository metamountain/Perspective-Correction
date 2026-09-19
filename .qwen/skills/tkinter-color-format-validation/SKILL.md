---
name: tkinter-color-format-validation
description: Tkinter silently rejects 8-digit hex colors (#RRGGBBAA); the crash surfaces in _redraw() and breaks unrelated features — always use #RRGGBB or named colors.
source: auto-skill
extracted_at: '2026-09-14T18:38:36.401Z'
---

# Tkinter color format: 8-digit hex is invalid and fails silently at call time

## The problem

You pass an 8-digit hex color like `fill="#ff5fa218"` to a canvas `create_*`
method. **Tkinter does not raise at the call site.** The item is created (or
silently discarded), but the next full `_redraw()` cycle that touches the same
canvas raises:

```
_tkinter.TclError: invalid color name "#ff5fa218"
```

Because `_redraw()` is a monolithic render pass, this one bad color **crashes
the entire redraw**, which means *every* feature drawn in that pass stops
working — not just the one you added. In this project it killed planar quad
rendering AND mark-line rendering simultaneously because both are drawn inside
`_redraw()`.

## Valid color formats (Tk 8.6, Windows)

| Format | Example | Valid? |
|---|---|---|
| `#RRGGBB` | `#ff5fa2` | ✓ |
| Named color | `"red"`, `"lightgrey"` | ✓ |
| `RGB(r,g,b)` | `"RGB(255,95,162)"` | ✓ |
| `#RGB` (shorthand) | `#f5a` | ✓ |
| `#RRGGBBAA` | `#ff5fa218` | ✗ **TclError** |
| `RGBA(r,g,b,a)` | `"RGBA(255,95,162,0.09)"` | ✗ **TclError** |

Tkinter/Tk has **no alpha channel** in canvas item colors. There is no way to
draw a semi-transparent fill on a canvas item.

## How to express "translucent" fills without alpha

Since `#RRGGBBAA` and `RGBA()` are both invalid:

1. **Pre-blend against the known background.** If the background is a solid
   color (e.g., black `INK["bg"]`), compute the blended RGB in Python:

   ```python
   def blend(fg_hex, bg_hex, alpha):
       """Blend fg over bg at given alpha (0..1). Returns '#rrggbb'."""
       f = tuple(int(fg_hex[i:i+2], 16) for i in (1, 3, 5))
       b = tuple(int(bg_hex[i:i+2], 16) for i in (1, 3, 5))
       return "#{:02x}{:02x}{:02x}".format(
           *(round(f[i] * alpha + b[i] * (1 - alpha)) for i in range(3)))

   fill = blend("#ff5fa2", INK["bg"], 0.09)  # was "#ff5fa218"
   ```

2. **Use `fill=""` (no fill) + outline only.** For quads, rectangles, and
   other shapes where the interior is transparent, just omit the fill:

   ```python
   canvas.create_polygon(pts, outline="#ff5fa2", width=2, fill="")
   ```

3. **Use a lighter/darker shade of the base color** to simulate reduced
   opacity against a dark background: `#ff5fa2` → `#6a2f40` (darker).

## Diagnostic pattern: "unrelated features broke after I added one draw call"

If adding a single `create_*` call causes **multiple unrelated UI elements** to
stop rendering, check the color arguments first. The failure mode is:

1. Bad color → TclError inside `_redraw()`
2. `_redraw()` aborts mid-pass
3. Everything drawn *after* the bad item in that pass is missing
4. User reports "planar doesn't work" / "mark line doesn't show" — features
   you never touched

**Quick check:** grep for 8-digit hex or RGBA in the file:

```bash
grep -nE '#[0-9a-fA-F]{8}|RGBA\(' src/pc/gui.py
```

## Pitfalls

- **The error is not at the call site.** `canvas.create_polygon(..., fill="#ff5fa218")`
  may appear to succeed (no exception) if Tk defers color validation. The crash
  surfaces on the next `_redraw()` or `update_idletasks()`, making the stack
  trace point at the redraw function, not the offending `create_*` call.
- **`fill=""` is not the same as omitting `fill`.** Omitting uses the default
  (which may be a solid color). Always pass `fill=""` explicitly for no fill.
- **Pre-blended colors must match the actual background.** If the background
  changes at runtime (theme switch), the pre-blended color will look wrong.
  In that case, recompute in `_redraw()` or use `outline`-only styling.
