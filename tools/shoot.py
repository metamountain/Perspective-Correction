"""Photograph the real window in a named state, so a change can be LOOKED at.

Why this exists: three bugs shipped in one afternoon that no assertion caught
and one glance would have -- a magnifier masked inside-out so the picture showed
only in four petals at the rim, a rubber band trailing at half the pointer's
speed, and crop handles hanging off the top of the canvas.  Every one of them
passed `--full` and `debug_ui`, because a test asks a question you already
thought to ask and a screenshot answers the one you did not.

    python tools/shoot.py                 # every scene, into analysis/shots/
    python tools/shoot.py mark            # just that one
    python tools/shoot.py --list

The window is opened ON screen at 0,0 for the grab -- PIL captures the screen,
so an off-screen window photographs as nothing.  It flashes up for a second and
closes.
"""

from __future__ import annotations

import os
import sys
import time
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

HERE = os.path.dirname(os.path.abspath(__file__))
ASSET = os.path.join(HERE, "..", "tests", "assets",
                     "79cb33878fd0ed76d368e7cfa8827e15.jpg")
OUT = os.path.join(HERE, "..", "analysis", "shots")


def _pump(app, n=10):
    for _ in range(n):
        app.update()
        time.sleep(0.02)


def _grab(app, name):
    from PIL import ImageGrab
    app.update_idletasks()
    app.update()
    time.sleep(0.4)                       # let the compositor catch up
    x, y = app.winfo_rootx(), app.winfo_rooty()
    im = ImageGrab.grab((x, y, x + app.winfo_width(), y + app.winfo_height()))
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name + ".png")
    im.save(path)
    print(f"  {name:14s} {im.size[0]}x{im.size[1]}  {path}")


def _ev(r, dx, dy, app):
    ox, oy = r._before_off
    return types.SimpleNamespace(x=dx + ox, y=dy + oy, state=0,
                                 x_root=app.winfo_rootx() + dx + ox,
                                 y_root=app.winfo_rooty() + dy + oy)


def scene_idle(app, r):
    """Nothing held: the palette, both panes, the controls."""


def scene_mark(app, r):
    """Mid-drag: the rubber band must END at the cursor, and the glass must sit
    in the gutter between the pictures rather than over either of them."""
    r.v_mark.set(True)
    r._on_mark_toggle()
    _pump(app, 4)
    r._on_click_before(_ev(r, 120, 100, app))
    for d in (60, 140, 260):
        r._on_before_b1motion(_ev(r, 120 + d, 100 + d, app))
        _pump(app, 2)


def scene_brush(app, r):
    """A painted stroke: the wash must have soft round edges, not stair steps."""
    r.v_stroke.set(True)
    r._on_stroke_toggle()
    _pump(app, 4)
    r._on_click_before(_ev(r, 120, 150, app))
    for i in range(1, 8):
        r._on_before_b1motion(_ev(r, 120 + i * 24, 150 + i * 10, app))
        _pump(app, 1)
    r._on_before_b1release(_ev(r, 288, 220, app))
    _pump(app, 10)


def scene_phosphor(app, r):
    """Phosphor's graded ground: the ramp must reach every edge of both wells,
    the photograph must sit ON it and not behind it, and the chrome around it
    must have moved with it -- a half-switched theme is the bug this catches."""
    app._switch_theme("Phosphor")
    _pump(app, 30)


def scene_rect(app, r):
    """The rectangle tool mid-polygon: the glass must already be up (it comes
    with the TOOL now, not with the first corner), and the rubber band must
    reach the cursor on plain motion -- it used to be bound to B1-Motion, so it
    only existed while a button was held, which is never while clicking."""
    r.v_rect.set(True)
    r._on_rect_toggle()
    _pump(app, 4)
    r._click_rect(140, 120)
    r._click_rect(520, 150)
    _pump(app, 2)
    r._on_before_motion(_ev(r, 540, 400, app))
    _pump(app, 3)


SCENES = {"idle": scene_idle, "mark": scene_mark, "brush": scene_brush,
          "phosphor": scene_phosphor, "rect": scene_rect}


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "--list" in sys.argv:
        print("scenes:", ", ".join(SCENES))
        return
    wanted = args or list(SCENES)
    from pc.gui import App
    for name in wanted:
        if name not in SCENES:
            print(f"  {name}: no such scene (try --list)")
            continue
        app = App(start_maximized=False)
        app.geometry("1400x900+0+0")       # on screen: a grab cannot see off it
        app.update_idletasks()
        app.update()
        app._add([os.path.abspath(ASSET)])
        _pump(app, 50)
        try:
            SCENES[name](app, app.review)
            _grab(app, name)
        finally:
            app.destroy()


if __name__ == "__main__":
    main()
