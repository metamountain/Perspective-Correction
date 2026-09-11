"""The window itself, off-screen -- the pattern skills/ui.md prescribes.

`layout.py` is tested as arithmetic in test_layout.py; this is the other half,
that the arithmetic actually reaches the widgets. It is the assertion that
would have caught the bug it was written for: the sash was being set while the
*empty* stage was still on screen, so once the options row packed and shrank
the pane, the results list collapsed to one pixel.

Kept deliberately small -- one photograph, a few event pumps -- because a GUI
test that takes a minute is a GUI test people stop running.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ASSET = os.path.join(HERE, "assets", "79cb33878fd0ed76d368e7cfa8827e15.jpg")


def _app():
    """An off-screen App, or a clean skip where there is no display/Tkinter."""
    try:
        import tkinter  # noqa: F401
    except Exception as exc:                       # no tcl/tk in this interpreter
        raise SkipTest(f"no tkinter ({exc})")      # noqa: F821
    if not os.path.exists(ASSET):
        raise SkipTest("asset missing")            # noqa: F821
    from bpc.gui import App
    try:
        app = App(start_maximized=False)
    except Exception as exc:                       # no display (CI, headless)
        raise SkipTest(f"no display ({exc})")      # noqa: F821
    return app


def _settle(app, n=40):
    for _ in range(n):
        app.update()
        time.sleep(0.02)


def _loaded(app, w, h):
    app.geometry(f"{w}x{h}-4000+0")                # off-screen, per skills/ui.md
    app.update_idletasks(); app.update()
    app._add([ASSET])
    _settle(app)


def test_the_results_list_keeps_its_rows_and_the_preview_takes_the_rest():
    """The layout rule, asserted on the real widgets at two window sizes.

    The tree's need is absolute, so it must be the *same* height on a larger
    window; everything the bigger window adds belongs to the preview. A
    proportional split -- what this used to do -- fails the first assertion.
    """
    sizes = [(1280, 800), (1920, 1200)]
    seen = []
    for w, h in sizes:
        app = _app()
        try:
            _loaded(app, w, h)
            seen.append((app._paned.winfo_height(),
                         app.review.winfo_height(),
                         app._w_tree.winfo_height()))
        finally:
            app.destroy()

    (_p0, r0, t0), (_p1, r1, t1) = seen
    assert t0 > 8 and t1 > 8, f"the results list collapsed: {t0} px, {t1} px"
    assert abs(t1 - t0) <= 2, (
        f"the list grew with the window ({t0} -> {t1} px); the extra height "
        f"belongs to the preview")
    assert r1 > r0, f"the preview did not grow ({r0} -> {r1} px)"


def test_loading_a_photograph_reports_no_failure():
    """skills/ui.md: every UI change ships with this."""
    app = _app()
    try:
        _loaded(app, 1280, 800)
        txt = app.review.status.get("1.0", "end").lower()
        assert "failed" not in txt and "traceback" not in txt, txt[:400]
    finally:
        app.destroy()


def _assert_adjust_above_save(r, top, when):
    """The controls sit above the action row, in the packer and on screen.

    Both halves are needed: these rows are packed `side="bottom"`, so the
    packing order runs *upwards* from the action row and reads backwards --
    `_adj` comes immediately after `_btns` in `pack_slaves` precisely because
    it is drawn above it.
    """
    slaves = top.pack_slaves()
    assert slaves.index(r._adj) == slaves.index(r._btns) + 1, (
        f"{when}: the adjustments left their slot above the Save row "
        f"({[str(w).rsplit('.', 1)[-1] for w in slaves]})")
    assert r._adj.winfo_y() < r._btns.winfo_y(), (
        f"{when}: the controls came back below the Save row")


def test_collapsing_the_adjustments_gives_the_picture_the_height():
    """The four control rows fold away, and come back where they were.

    Two assertions, and the second is the one that will fail: `pack` appends,
    so a re-expand that does not name its place puts the controls *below* the
    Save row. Order is read from `pack_slaves` rather than from coordinates,
    because on a short window the packer unmaps the bottom rows outright and a
    `winfo_y` comparison would then be reading stale numbers. The default has
    to stay expanded -- nothing changes for anyone who does not press it.
    """
    app = _app()
    try:
        _loaded(app, 1920, 1200)
        r = app.review
        top = r._adj.master
        # Expanded on purpose: the *default* is the height's decision (see
        # test_the_panel_default_keeps_both_the_buttons_and_a_usable_picture),
        # and touching the toggle is what takes that decision away from it.
        if not r.v_adjust.get():
            r.v_adjust.set(True); r._toggle_adjust(); _settle(app, 10)
        assert r._adj in top.pack_slaves(), "expanded, but not packed"
        open_h = r.c_before.winfo_height()

        r.v_adjust.set(False); r._toggle_adjust(); _settle(app, 10)
        shut_h = r.c_before.winfo_height()
        assert r._adj not in top.pack_slaves(), "collapsed, but still packed"
        assert shut_h > open_h + 100, (
            f"collapsing bought only {shut_h - open_h} px of picture")

        r.v_adjust.set(True); r._toggle_adjust(); _settle(app, 10)
        assert abs(r.c_before.winfo_height() - open_h) <= 2, (
            "re-expanding did not restore the layout")
        _assert_adjust_above_save(r, top, "after a collapse and re-expand")

        # The path the review queue actually takes: collapse, then the next
        # photograph.  `load` destroys every child and rebuilds, so the state
        # has to live on the panel -- a collapse that re-opened itself thirty
        # times in a queue of thirty is worse than no collapse at all.
        r.v_adjust.set(False); r._toggle_adjust(); _settle(app, 6)
        r.load(r.session.path, r.settings, r.dest_path)
        _settle(app, 20)
        r = app.review
        top = r._adj.master
        assert r.v_adjust.get() is False, "the collapse did not survive the next photograph"
        assert r._adj not in top.pack_slaves(), "rebuilt collapsed, but packed anyway"
        r.v_adjust.set(True); r._toggle_adjust(); _settle(app, 10)
        _assert_adjust_above_save(r, top, "after a reload")
        assert r._btns.winfo_ismapped(), "Save went missing across a reload"

        txt = r.status.get("1.0", "end").lower()
        assert "failed" not in txt and "traceback" not in txt, txt[:400]
    finally:
        app.destroy()


def test_the_save_button_is_reachable_at_every_window_size():
    """Save / Keep original / Close, mapped, at three real resolutions.

    They were not. `pack` serves its children in call order and the canvases
    were packed first with `expand=True`, so at 1920x1080 -- the commonest
    desktop resolution there is -- the action row fell off the bottom of the
    review panel and the one mode that exists to be driven by hand could not
    be driven. The picture is what gives up height now; a button never is.
    """
    for w, h in ((1280, 800), (1920, 1080), (2560, 1440)):
        app = _app()
        try:
            _loaded(app, w, h)
            r = app.review
            # Expanded on purpose: the point is that the action row survives
            # even the arrangement that squeezes it hardest.
            if not r.v_adjust.get():
                r.v_adjust.set(True); r._toggle_adjust(); _settle(app, 8)
            assert r._btns.winfo_ismapped(), (
                f"{w}x{h}: the Save row is not on screen")
            assert r._btns.winfo_height() > 8, (
                f"{w}x{h}: the Save row is {r._btns.winfo_height()} px tall")

            # ... and it survives both things that rebuild or re-pack the panel
            r.v_adjust.set(False); r._toggle_adjust(); _settle(app, 6)
            assert r._btns.winfo_ismapped(), f"{w}x{h}: lost on collapse"
            r.v_adjust.set(True); r._toggle_adjust(); _settle(app, 6)
            assert r._btns.winfo_ismapped(), f"{w}x{h}: lost on re-expand"

            r.load(r.session.path, r.settings, r.dest_path)
            _settle(app, 20)
            assert app.review._btns.winfo_ismapped(), f"{w}x{h}: lost on reload"
        finally:
            app.destroy()


def test_the_panel_default_keeps_both_the_buttons_and_a_usable_picture():
    """The whole point, at three real resolutions.

    The control columns are always on now -- one persistent layout, no
    auto-collapse -- so what must hold is that Save stays reachable and the
    picture is still mapped, even if it is small on a short window. A usable
    size floor is only asserted where the window genuinely has the height for
    it: a 1280x800 pane cannot give both two control columns and a big picture.
    """
    floors = {(1280, 800): 20, (1920, 1080): 260, (2560, 1440): 260}
    for w, h in floors:
        app = _app()
        try:
            _loaded(app, w, h)
            r = app.review
            canvas = r.c_before.winfo_height()
            assert r._btns.winfo_ismapped(), f"{w}x{h}: Save is not on screen"
            assert r.v_adjust.get() is True, (
                f"{w}x{h}: the control columns collapsed on their own")
            assert canvas >= floors[(w, h)], (
                f"{w}x{h}: the preview opened at {canvas} px")
        finally:
            app.destroy()


def test_the_preview_gives_up_retrying_instead_of_spinning_forever():
    """A canvas too small to draw into used to reschedule at 8 Hz, endlessly.

    Reachable since the action row started claiming its strip first. The retry
    exists to wait for the first layout, so it is bounded -- and the bound has
    to reset, or a genuine resize much later would find its retries spent.
    """
    app = _app()
    try:
        # Small enough that the two control columns leave the canvas unmapped --
        # 1280x800 no longer does that once the columns are on by default.
        _loaded(app, 1100, 650)
        r = app.review
        r.v_adjust.set(True); r._toggle_adjust(); _settle(app, 20)
        assert (not r.c_before.winfo_ismapped()
                or r.c_before.winfo_height() < 20), (
            "this asserts the starved case and the canvas is not starved")
        _settle(app, 25)
        assert r._redraw_tries <= 6, f"still retrying: {r._redraw_tries}"
        settled = r._redraw_tries
        _settle(app, 25)
        assert r._redraw_tries == settled, "the retry loop never stopped"

        # ...and a real redraw hands the retries back
        r.v_adjust.set(False); r._toggle_adjust(); _settle(app, 25)
        assert r.c_before.winfo_height() > 20, "the collapse did not free height"
        assert r._redraw_tries == 0, "a successful redraw must reset the count"
    finally:
        app.destroy()
