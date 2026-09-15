"""Drive the real window off-screen and MEASURE what a gesture did.

Why this file exists: the mask eraser broke and reading the code could not find
it.  Every handler was individually correct, the chain from press to
``paint_ignore(erase=True)`` was complete, and the worker read it twice before
saying honestly that it could not decide statically.  It could not -- the fault
was a binding that *does not exist* (``<Alt-ButtonRelease-3>``), and absence is
the thing reading is worst at.  Running the gestures and measuring the painted
fraction found it in one pass.

So: for anything gestural, measure first and read second.

    python tools/probe_gestures.py              # the standard sweep
    python tools/probe_gestures.py --bindings   # just the binding table

This is a diagnostic, not a test: it prints numbers and judges nothing, so it
stays useful for a gesture nobody has written an assertion for yet.  When it
does find something, the finding belongs in `tests/test_gui.py` as an assertion
-- this file is how you get there, not where the guard lives.
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

# Every sequence the before-pane cares about.  Listed rather than discovered,
# because the interesting answer is the one that is MISSING -- a discovered list
# can only ever show what is there.
SEQUENCES = ("<Button-1>", "<B1-Motion>", "<ButtonRelease-1>",
             "<Alt-ButtonPress-1>", "<Alt-B1-Motion>", "<Alt-ButtonRelease-1>",
             "<ButtonPress-3>", "<B3-Motion>", "<ButtonRelease-3>",
             "<Alt-ButtonPress-3>", "<Alt-B3-Motion>", "<Alt-ButtonRelease-3>",
             "<Motion>", "<Leave>", "<Configure>")


def _app():
    from pc.gui import App
    app = App(start_maximized=False)
    app.geometry("1280x800-4000+0")           # off-screen, per skills/ui.md
    app.update_idletasks()
    app.update()
    app._add([os.path.abspath(ASSET)])
    _settle(app, 40)
    return app


def _settle(app, n=10):
    for _ in range(n):
        app.update()
        time.sleep(0.02)


def bindings(review) -> None:
    print("-- before-pane bindings " + "-" * 40)
    for seq in SEQUENCES:
        state = "bound" if review.c_before.bind(seq) else "-- NOT BOUND"
        print(f"   {seq:24s} {state}")


def _painted(session) -> float:
    return 0.0 if session.paint is None else float(session.paint.mean())


def sweep(app) -> None:
    r = app.review
    s = r.session
    r.v_stroke.set(True)
    r._on_stroke_toggle()
    _settle(app, 6)
    ox, oy = r._before_off

    def ev(dx, dy):
        return types.SimpleNamespace(x=dx + ox, y=dy + oy, state=0)

    def drag(press, motion, release):
        press(ev(120, 120))
        for i in range(1, 7):
            motion(ev(120 + i * 18, 120 + i * 6))
            app.update()
        release(ev(228, 156))
        _settle(app, 6)

    print("-- gestures, as a fraction of the analysis frame " + "-" * 16)
    for label, press, motion, release in (
            ("erase, right drag", r._on_erase_press, r._on_erase_motion,
             r._on_erase_release),
            ("erase, Alt+left  ", r._on_alt_erase_press, r._on_before_b1motion,
             r._on_before_b1release)):
        # Fresh paint before EACH erase.  The first version of this probe erased
        # everything with the right button and then "tested" Alt+left against an
        # already-empty mask, which passes and means nothing.
        s.paint = None
        drag(r._on_click_before, r._on_before_b1motion, r._on_before_b1release)
        before = _painted(s)
        drag(press, motion, release)
        after = _painted(s)
        print(f"   {label}: {before:8.4%} -> {after:8.4%}   "
              f"{'removed' if after < before * 0.2 else 'LEFT BEHIND'}")

    # The gesture that broke it: sizing, then erasing.
    s.paint = None
    r._on_pen_size_start(ev(200, 200))
    r._on_pen_size_drag(ev(260, 200))
    print(f"   after a sizing drag, _pen_anchor = {r._pen_anchor!r}"
          f"   {'(stuck -- erase will lose its stroke)' if r._pen_anchor else ''}")
    r._on_pen_size_end()
    print(f"   after its release,   _pen_anchor = {r._pen_anchor!r}")


def main() -> None:
    app = _app()
    try:
        bindings(app.review)
        if "--bindings" not in sys.argv:
            sweep(app)
    finally:
        app.destroy()


if __name__ == "__main__":
    main()
