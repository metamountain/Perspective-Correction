"""The window itself, off-screen -- the pattern skills/ui.md prescribes.

`layout.py` is tested as arithmetic in test_layout.py; this is the other half,
that the layout actually reaches the widgets: the window is the brand header
and the perfect cross and nothing else -- no paned split, no strip below --
with the results list inside the cross's lower-left field.

Kept deliberately small -- one photograph, a few event pumps -- because a GUI
test that takes a minute is a GUI test people stop running.
"""
import os
import sys
import time
import types
from unittest import SkipTest

import numpy as np
import pytest
from pc import layout

HERE = os.path.dirname(os.path.abspath(__file__))
ASSET = os.path.join(HERE, "assets", "79cb33878fd0ed76d368e7cfa8827e15.jpg")


def _no_dialog(*a, **k):
    raise AssertionError(f"a modal dialog would block the run: {a[:2]}")


def _app():
    """An off-screen App, or a clean skip where there is no display/Tkinter."""
    try:
        import tkinter  # noqa: F401
    except Exception as exc:                       # no tcl/tk in this interpreter
        raise SkipTest(f"no tkinter ({exc})")      # noqa: F821
    if not os.path.exists(ASSET):
        raise SkipTest("asset missing")            # noqa: F821
    from pc.gui import App
    from pc import gui as GUI
    # A real modal blocks the whole run with nobody there to press it -- the
    # suite once hung for an hour on "Unsaved changes".  An unexpected one fails
    # its test instead; a test that wants the dialog installs its own fake.
    if getattr(GUI.messagebox.askyesnocancel, "__module__", "") == "tkinter.messagebox":
        GUI.messagebox.askyesnocancel = _no_dialog
    try:
        app = App(start_maximized=False, in_taskbar=False)
    except Exception as exc:
        # Only a MISSING DISPLAY may skip.  This caught everything, so a plain
        # AttributeError in `_build` -- a button wired to a method that no
        # longer existed -- turned 32 tests into quiet skips and the module
        # still said "0 failed".  A broken window reading as green is the exact
        # thing this file exists to prevent, so anything that is not Tk telling
        # us there is no screen is re-raised.
        text = f"{type(exc).__name__}: {exc}".lower()
        if not any(w in text for w in ("display", "no screen", "couldn't connect",
                                       "can't find a usable", "tclerror: no ")):
            raise
        raise SkipTest(f"no display ({exc})")      # noqa: F821
    return app


def _settle(app, n=40):
    for _ in range(n):
        app.update()
        time.sleep(0.02)


def _settle_until(app, done, n=120):
    """Pump until ``done()`` is true, or give up after ``n`` turns.

    A fixed pump count is a sleep racing a debounced redraw: it passes on an idle
    machine and fails when the suite runs its modules in parallel, which is a
    flake, not a finding.  Waiting for the condition removes the race without
    weakening what is asserted -- the caller still asserts it afterwards, so a
    redraw that never happens still fails, just at the assertion rather than at
    whatever the timing happened to be.
    """
    for _ in range(n):
        if done():
            return True
        app.update()
        time.sleep(0.02)
    return done()


def _skip_if_window_cannot_grow(h0, h1):
    """A CI runner's virtual display is small, and Windows clamps a window to
    the screen (measured: asked 800 and 1200, got 1044 both times). A test of
    what a bigger window does then has nothing to measure; skip only then."""
    if h1 <= h0:
        raise SkipTest(f"the display cannot hold a taller window "   # noqa: F821
                       f"({h0} -> {h1} px)")


def _loaded(app, w, h):
    app.geometry(f"{w}x{h}-4000+0")                # off-screen, per skills/ui.md
    app.update_idletasks(); app.update()
    app._add([ASSET])
    _settle(app)


def test_adding_photographs_walks_them_and_the_batch_is_one_footer_button():
    """Manual review is the product: adding photographs opens the first for
    review, and leaving it (here: Skip) opens the next -- no button to press
    first, and nothing written by a worker thread.  The unattended batch is a
    rudiment (user, 2026-10-21): one footer button, not the accent style,
    labelled with its own counter while it runs.  Not invoked here -- running
    it would write real files from a test."""
    app = _app()
    try:
        app.geometry("1920x1080-4000+0")
        app.update_idletasks(); app.update()
        second = os.path.join(os.path.dirname(ASSET), "1070912stkzg.jpg")
        app._add([ASSET, second])
        _settle(app)
        assert getattr(app, "worker", None) is None, (
            "adding photographs started an unattended run")
        assert os.path.basename(app.review.session.path) == os.path.basename(ASSET)
        assert app._review_total == 2 and app._review_queue == [os.path.abspath(second)]
        app.review._skip()
        _settle(app)
        assert os.path.basename(app.review.session.path) == "1070912stkzg.jpg", (
            "Skip did not walk on to the next photograph")
        assert str(app.btn_batch.cget("style")) != "Accent.TButton"
        assert app.btn_batch.winfo_ismapped(), "the batch button is not on screen"
        for gone in ("tree", "btn_run", "btn_stop", "progress", "_w_bar", "_w_opt"):
            assert not hasattr(app, gone), f"{gone} is back -- the batch is a rudiment"
    finally:
        app.destroy()

def test_shorten_middle_keeps_the_end_never_just_truncates():
    """P15: a filename is shortened by cutting its middle, so the end -- the
    extension and the _corr suffix that name where Save writes -- always shows."""
    from pc.gui import _shorten_middle as s
    assert s("x.jpg") == "x.jpg", "a name that fits is untouched"
    long = "a_very_long_architectural_photography_name_0042.jpg"
    out = s(long)
    assert len(out) <= 28, "bounded so it fits one line of the panel"
    assert out.endswith("name_0042.jpg"), "the end survives truncation"
    assert "\u2026" in out, "only the middle is elided"


def test_the_mask_brush_still_works_after_a_second_photograph_loads():
    """Press the real checkbox, drag with real events, on the *second* load.

    This is the shape of a bug that shipped: `_build` re-runs on every load and
    rebuilt `v_stroke`, while the checkbutton lives in the tools field, which is
    built once.  From the second photograph on, ticking the box set a variable
    nobody read and the brush silently did nothing.  Every test passed, because
    they all set `v_stroke` directly instead of pressing the widget -- the
    "tested is not reachable" trap this file already warns about.

    So: press the button, not the variable, and send real events rather than
    calling handlers, after a reload rather than on a fresh window.
    """
    app = _app()
    try:
        _loaded(app, 1600, 1000)
        app._add([ASSET])              # second load: rebuilds the review panel
        _settle(app)
        r = app.review
        s = r.session
        if s is None or len(s.vert) == 0:
            raise SkipTest("no session or no verticals")          # noqa: F821

        r._brush_chk.invoke()          # the widget, exactly as a click would
        _settle(app, 5)
        assert r.v_stroke.get(), "pressing the box must arm the brush the handler reads"

        ox, oy = r._before_off
        c = r.c_before
        c.event_generate("<ButtonPress-1>", x=ox + 100, y=oy + 100)
        app.update()
        for d in range(0, 50, 10):
            c.event_generate("<B1-Motion>", x=ox + 100 + d, y=oy + 100 + d)
            app.update()
        c.event_generate("<ButtonRelease-1>", x=ox + 150, y=oy + 150)
        _settle_until(app, lambda: s.paint is not None and s.paint.any())

        assert s.paint is not None and s.paint.any(), (
            "a real drag with the real checkbox pressed must paint the mask")
    finally:
        app.destroy()


def test_the_mask_boxes_tell_the_truth_about_the_second_photograph():
    """Every photograph brings a fresh session; the boxes were built once.

    Measured over two loads before this was fixed: untick "mask active", tick
    "invert ALL sources", load the next image -- the boxes still read
    active=False invert=True while the new session said mask_enabled=True and
    invert_mask=False.  Both switches lied about the picture in front of them,
    which is the same stale-variable trap as `v_stroke` above, one level up:
    there the variable was rebuilt and the widget was not, here the widget
    keeps a variable that no longer describes anything.
    """
    app = _app()
    try:
        _loaded(app, 1600, 1000)
        r = app.review
        if r.session is None:
            raise SkipTest("no session")                          # noqa: F821
        second = os.path.join(HERE, "assets", "1070912stkzg.jpg")
        if not os.path.exists(second):
            raise SkipTest("second asset missing")                # noqa: F821
        r.v_mask_active.set(False)
        r._on_mask_active_toggle()
        r.v_invert.set(True)
        r._on_invert_toggle()
        _settle(app, 5)
        assert r.session.mask_enabled is False and r.session.invert_mask is True

        # `ReviewPanel` is built once on the App and every photograph after the
        # first arrives through `load` -- which is why `_add` of the same file
        # would not reach this: it returns early when the session is already on
        # that path.  This is the batch's own call, from `_open_review_for`.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            r.load(second, r.settings, os.path.join(d, "out.jpg"))
            _settle(app)
        s = r.session
        if s is None:
            raise SkipTest("no session")                          # noqa: F821
        assert r.v_mask_active.get() == bool(s.mask_enabled), \
            "the active box has to describe the photograph now in front of it"
        assert r.v_invert.get() == bool(s.invert_mask), \
            "and so does the invert box"
        assert r.v_maskmode.get() == s.settings.mask_mode, \
            "and the source combobox"
    finally:
        app.destroy()


def test_the_mask_brush_paints_the_ignore_region_and_erases_it_again():
    """The mask brush, off-screen: a drag paints a live preview, and on release
    it adds what was swept to the ignore mask -- the manual answer to a segmenter
    that chose the wrong subject (a stroke over the parked car, rather than an
    argument with a text prompt about it).

    Swept end to end along one segment, so the paint covers *both* its endpoints:
    the strike rule is `drop_by_endpoints`, the same one the automatic mask uses,
    precisely so a long facade edge that merely crosses the painted region keeps
    its say.  Then erased again, which must hand that line back -- painting is
    re-derived from the region each time rather than accumulated, and this is
    what proves it.
    """
    app = _app()
    try:
        _loaded(app, 1280, 800)
        r = app.review
        s = r.session
        if len(s.vert) == 0:
            raise SkipTest("no verticals detected on the asset")      # noqa: F821
        i = int(np.argmax(s.vert.length))
        seg = s.vert.seg[i]
        k = r._before_scale / s.scale          # original -> displayed (image origin)
        disp = []
        for t in (0.0, 0.25, 0.5, 0.75, 1.0):
            ox = seg[0] + t * (seg[2] - seg[0])
            oy = seg[1] + t * (seg[3] - seg[1])
            disp.append((ox * k, oy * k))

        def sweep():
            r._stroke_start(*disp[0])
            for px, py in disp[1:]:
                r._on_stroke_drag(types.SimpleNamespace(
                    x=px + r._before_off[0], y=py + r._before_off[1]))
            assert len(r.c_before.find_withtag("stroke_preview")) > 0, \
                "the brush is painted while dragging"
            r._on_stroke_release(types.SimpleNamespace(
                x=disp[-1][0] + r._before_off[0],
                y=disp[-1][1] + r._before_off[1]))
            assert len(r.c_before.find_withtag("stroke_preview")) == 0, \
                "the preview is gone once the stroke is released"

        r.v_stroke.set(True)
        r._on_stroke_toggle()
        sweep()
        assert s.paint is not None and s.paint.any(), "the stroke painted a region"
        assert not s.enabled[i], "a line the paint covers end to end is struck"

        # Right-drag erases: the same sweep with the erase flag must undo it.
        r._stroke_erasing = True
        sweep()
        assert not s.paint.any(), "erasing the same stroke clears the region"
        assert s.enabled[i], "and hands the struck line back"
    finally:
        app.destroy()


def test_the_window_is_the_cross_and_nothing_else():
    """The layout rule, asserted on the real widgets at two window sizes.

    There is no paned split: the review panel fills everything, and the only
    thing under the cross is the slim fixed-height status bar (wordmark left,
    batch + theme + update right) -- a strip, not a growing field. A
    bigger window grows the four equal fields -- and with them the previews.
    """
    sizes = [(1280, 800), (1920, 1200)]
    seen, heights = [], []
    for w, h in sizes:
        app = _app()
        try:
            _loaded(app, w, h)
            heights.append(app.winfo_height())
            below = app.winfo_height() - app.review.winfo_y() \
                    - app.review.winfo_height()
            # The status bar is a fixed-height strip; it must not grow with the
            # window, so it stays in a narrow band at every size.
            assert 20 <= below <= 60, (
                f"the strip below the cross is not the slim status bar: {below} px")
            seen.append(app.review.winfo_height())
        finally:
            app.destroy()

    _skip_if_window_cannot_grow(*heights)
    r0, r1 = seen
    assert r1 > r0, f"the cross did not take the window's extra height " \
                    f"({r0} -> {r1} px)"


def test_loading_a_photograph_reports_no_failure():
    """skills/ui.md: every UI change ships with this."""
    app = _app()
    try:
        _loaded(app, 1280, 800)
    finally:
        app.destroy()


def test_strip_control_restricts_horizontal_evidence_and_defaults_off():
    """The find column's facade strip is the GUI half of ``--strip``: off by
    default (the frame stays unfiltered), and a valid strip sets
    ``session.strip`` as fractions of the width so refit can restrict horizontals to
    one facade on a corner view.  An inverted or empty strip is refused."""
    app = _app()
    try:
        _loaded(app, 1280, 800)
        r = app.review
        s = r.session
        assert s is not None and s.strip is None, "no strip by default"
        # Facade strip removed from Q4 (2026-09-17): v_strip no longer exists.
        if getattr(r, "v_strip", None) is None:
            raise pytest.skip("facade strip removed from Q4")
        assert not r.v_strip.get(), "facade strip must start off"

        aw = s.gray.shape[1]
        r.v_strip.set(True)
        r.v_strip_x0.set(10.0)
        r.v_strip_x1.set(50.0)
        r._apply_strip()
        assert s.strip is not None, "a valid strip must set strip"
        x0, x1 = s.strip
        # Fractions, not pixels: one unit for the strip from the spinbox to the
        # Result to the saved record, so no stored number needs its frame
        # guessed. The spinboxes are percentages, so 10 and 50 arrive as such.
        assert abs(x0 - 0.10) < 1e-6 and abs(x1 - 0.50) < 1e-6, (x0, x1)

        # x0 >= x1 is not a strip -- the frame stays unfiltered.
        r.v_strip_x0.set(80.0)
        r.v_strip_x1.set(20.0)
        r._apply_strip()
        assert s.strip is None, "x0 >= x1 must not restrict anything"

        # Unchecking always clears it, whatever the spinboxes hold.
        r.v_strip_x0.set(10.0); r.v_strip_x1.set(50.0)
        r._apply_strip()
        assert s.strip is not None
        r.v_strip.set(False)
        r._apply_strip()
        assert s.strip is None, "unchecking must clear the strip"
    finally:
        app.destroy()


def test_enabling_a_strip_turns_on_horizontal_correction():
    """The strip only shapes the yaw, and the yaw is gated on correct_horizontal
    (off by default) -- so a strip that left the flag off would show rulers and
    change nothing.  Enabling it therefore turns horizontal correction on with it;
    clearing the strip does not force the flag back off."""
    app = _app()
    try:
        _loaded(app, 1280, 800)
        r = app.review
        s = r.session
        assert not s.settings.correct_horizontal, "horizontal is off by default"
        if getattr(r, "v_strip", None) is None:
            raise pytest.skip("facade strip removed from Q4")

        r.v_strip.set(True)
        r.v_strip_x0.set(10.0)
        r.v_strip_x1.set(50.0)
        r._apply_strip()
        assert s.strip is not None, "the strip is set"
        assert s.settings.correct_horizontal, \
            "a valid strip must turn on the horizontal correction it shapes"
        assert r.v_correct_horizontal.get(), "the checkbox shows the flag that turned on"

        # Clearing the strip leaves a deliberate yaw choice alone.
        r.v_strip.set(False)
        r._apply_strip()
        assert s.strip is None, "unchecking clears the strip"
        assert s.settings.correct_horizontal, \
            "clearing the strip must not force horizontal correction back off"
    finally:
        app.destroy()


def test_strip_draws_two_draggable_rulers_defaulting_to_20_and_80():
    """facade strip is set by hand, not blind percentages: ticking it on draws two
    draggable vertical rulers on the before pane -- default 20/80 %, not the
    useless full-frame 0/100 -- and greys out the sides that get ignored.  A drag
    moves a ruler, clamped to the frame edge and to its neighbour so the strip can
    never collapse below 2 % or be swiped away."""
    app = _app()
    try:
        _loaded(app, 1280, 800)
        r = app.review
        if getattr(r, "v_strip", None) is None:
            raise pytest.skip("facade strip removed from Q4")
        # The spinboxes default to a real strip, not the whole frame.
        assert r.v_strip_x0.get() == 20.0 and r.v_strip_x1.get() == 80.0, \
            "the default must restrict something, not cover the whole frame"

        # Off: no rulers on the canvas.
        assert r.c_before.find_withtag("strip_ruler") == (), "no rulers while off"

        r.v_strip.set(True)
        r._apply_strip()
        items = r.c_before.find_withtag("strip_ruler")
        rects = [i for i in items if r.c_before.type(i) == "rectangle"]
        assert len(rects) == 2, "both excluded sides must be washed"

        ox, oy = r._before_off
        iw = r._ph_b.width()
        # The two solid ruler lines sit at 20 % and 80 % of the frame width.
        vlines = sorted(c[0] for i in items if r.c_before.type(i) == "line"
                        for c in [r.c_before.coords(i)]
                        if abs(c[0] - c[2]) < 0.5)
        assert len(vlines) == 2, vlines
        assert abs(vlines[0] - (ox + 0.2 * iw)) < 1.0, vlines
        assert abs(vlines[1] - (ox + 0.8 * iw)) < 1.0, vlines

        # A drag moves the grabbed ruler and clamps it: pulling the left one far
        # right stops at a 2 % gap from the right ruler, not a crossing; far left
        # clamps to the frame edge.
        r._on_strip_drag_start(0)

        class _E:                       # a minimal motion event
            pass
        e = _E()
        e.x = ox + iw                   # far right: must clamp, not cross
        r._on_strip_drag_move(e)
        assert r.v_strip_x0.get() <= r.v_strip_x1.get() - 2.0, \
            "the left ruler must not cross the right one"
        e.x = ox                        # far left: clamps to the frame edge
        r._on_strip_drag_move(e)
        assert r.v_strip_x0.get() == 0.0, "a ruler clamps to the frame edge"
        r._on_strip_drag_release()

        # Off again: the rulers disappear with the toggle.
        r.v_strip.set(False)
        r._apply_strip()
        assert r.c_before.find_withtag("strip_ruler") == (), \
            "unchecking must remove the rulers"
    finally:
        app.destroy()


def test_the_control_strip_is_always_open_and_the_pictures_never_move():
    """Three rows, nothing collapses, and the pictures stay put.

    User, 2026-10-20: no fold-out menus, everything open and ultra compact, the
    picture area static.  So the strip is the same height in all three
    correction modes' *picture* terms: switching Auto / Horizontal auto /
    Manual changes which controls show, and must not move or resize either
    preview.  The rows are packed ``side="bottom"``, so the packer order runs
    upwards (Save row first) and reads backwards.  The mode lives on the panel
    and must survive ``load``, which destroys every child.
    """
    from pc import layout as L
    app = _app()
    try:
        _loaded(app, 1920, 1080)
        r = app.review
        rows = [int(w.grid_info()["row"]) for w in (r._btns2, r._adj, r._btns)]
        assert rows == [0, 1, 2], (
            f"the rows left their order {rows}: mode/fill/crop on top, the "
            f"active mode's controls in the middle, Save at the bottom")
        assert not hasattr(r, "v_adjust"), "the fold-out toggle is back"

        sizes = set()
        for mode in ("auto", "horizontal"):
            r.v_corr_mode.set(mode); r._show_mode(); _settle(app, 8)
            assert r._mode_top == mode, f"{mode}: showing {r._mode_top}"
            sizes.add((r.c_before.winfo_height(), r.c_after.winfo_height(),
                       r._img_box, r._strip.winfo_height()))
            assert r._adj.winfo_ismapped() and r._btns.winfo_ismapped()
        assert len(sizes) == 1, f"switching mode moved the pictures or the strip: {sizes}"

        # Compact: all three rows together stay inside the strip's budget.
        strip = (r._btns2.winfo_height() + r._adj.winfo_height()
                 + r._btns.winfo_height())
        assert strip <= L.CTRL_STRIP_MAX_H_PX, (
            f"the control strip is {strip} px, over the {L.CTRL_STRIP_MAX_H_PX} "
            f"px budget")

        # The queue path: the chosen mode survives the next photograph.
        r.v_corr_mode.set("horizontal"); r._show_mode(); _settle(app, 8)
        r._mark_clean()
        r.load(r.session.path, r.session.settings, r.dest_path)
        _settle(app, 20)
        assert app.review.v_corr_mode.get() == "horizontal", (
            "the mode did not survive the next photograph")
        assert "manual" not in r._mode_frames, "Manual mode is back"
    finally:
        app.destroy()


def test_the_save_button_is_reachable_at_every_window_size():
    """Save / Skip / Close, mapped, at three real resolutions.

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
            assert r._btns.winfo_ismapped(), (
                f"{w}x{h}: the Save row is not on screen")
            assert r._btns.winfo_height() > 8, (
                f"{w}x{h}: the Save row is {r._btns.winfo_height()} px tall")
            # The frame being mapped is not enough: a row that is on screen but
            # too narrow for its children still starves them to zero width, which
            # is how Save went missing while this very test passed.  Every child
            # of both rows must have real width.
            for row in (r._btns, r._btns2):
                assert row.winfo_ismapped(), f"{w}x{h}: a button row is not on screen"
                for c in row.winfo_children():
                    assert c.winfo_width() > 0, (
                        f"{w}x{h}: {str(c).rsplit('.', 1)[-1]} starved to "
                        f"{c.winfo_width()} px")

            # ... and it survives the thing that rebuilds the panel
            r.load(r.session.path, r.settings, r.dest_path)
            _settle(app, 20)
            assert app.review._btns.winfo_ismapped(), f"{w}x{h}: lost on reload"
        finally:
            app.destroy()


def test_skip_moves_on_and_writes_nothing():
    """Skip replaced Keep original (user, 2026-10-20), which copied the
    untouched photograph to the output.  Skip must advance the queue and leave
    no file behind -- neither at the output path nor anywhere a copy would go."""
    app = _app()
    try:
        _loaded(app, 1920, 1080)
        r = app.review
        dst = r._target_path()
        if os.path.exists(dst):
            os.remove(dst)
        fired = []
        r.on_closed = lambda: fired.append(True)
        r._closed_sent = False
        r._skip()
        _settle(app, 5)
        assert fired == [True], "Skip did not advance the queue"
        assert not os.path.exists(dst), f"Skip wrote {dst}"
        assert not any(c.winfo_class() == "TButton" and c.cget("text") == "Keep original"
                       for c in r._btns.winfo_children()), "Keep original is still there"
    finally:
        app.destroy()


def test_ctrl_v_pastes_a_picture_without_the_icon():
    """Ctrl+V does what the paste icon does (user, 2026-10-20), and an empty
    clipboard says so in the notice line instead of a modal dialog."""
    import tempfile
    from PIL import Image, ImageGrab
    app = _app()
    real = ImageGrab.grabclipboard
    try:
        _settle(app, 10)
        assert app.bind("<Control-v>") and app.bind("<Control-V>"), "Ctrl+V is not bound"
        with tempfile.TemporaryDirectory() as tmp:
            app.v_output.set(tmp)
            ImageGrab.grabclipboard = lambda: Image.new("RGB", (64, 48), (90, 120, 160))
            before = len(app.items)
            assert app._paste_key() == "break"
            _settle(app, 5)
            shots = [f for f in os.listdir(tmp) if f.startswith("screenshot_")]
            assert len(shots) == 1, shots
            assert len(app.items) == before + 1
            ImageGrab.grabclipboard = lambda: None
            app._paste_key()
            assert "no image on the clipboard" in app.review._notice
    finally:
        ImageGrab.grabclipboard = real
        app.destroy()


def test_the_panel_default_keeps_both_the_buttons_and_a_usable_picture():
    """The whole point, at three real resolutions.

    The picture is what this window is for, so a default arrangement that
    leaves it below ``layout.MIN_USEFUL_CANVAS`` is a broken default however
    tidy the controls look.

    Under the content-sized cross (2026-10-19) the control rows start folded
    at every resolution: the two preview fields on top are maximized
    vertically and the lower row carries only the per-photograph controls and
    the run bar. The angles are one toggle away when they need a hand. So the
    default is simply "shut", and what must hold on the real widgets is that
    the picture clears the floor and Save is still reachable.
    """
    from pc import layout as L
    for w, h in ((1280, 800), (1920, 1080), (2560, 1400)):
        app = _app()
        try:
            _loaded(app, w, h)
            r = app.review
            canvas = r.c_before.winfo_height()
            assert r._btns.winfo_ismapped(), f"{w}x{h}: Save is not on screen"
            assert canvas >= L.MIN_USEFUL_CANVAS, (
                f"{w}x{h}: the preview opened at {canvas} px, below the "
                f"{L.MIN_USEFUL_CANVAS} px floor")
        finally:
            app.destroy()


def test_the_preview_gives_up_retrying_instead_of_spinning_forever():
    """A canvas too small to draw into used to reschedule at 8 Hz, endlessly.

    The retry exists to wait for the first layout, so it is bounded -- and the
    bound has to reset, or a genuine resize much later would find its retries
    spent.
    """
    app = _app()
    try:
        # An unmapped canvas is starved too -- the branch `_redraw` checks --
        # and it is the only way to reach it now: at the window's minimum size
        # (1920x1080) the cross always gives the canvas well over 20 px, so no
        # resize can manufacture the old sash-starved case.
        _loaded(app, 1920, 1080)
        r = app.review
        r.c_before.pack_forget()
        r._schedule_redraw(); _settle(app, 25)
        assert (not r.c_before.winfo_ismapped()
                or r.c_before.winfo_height() < 20), (
            "this asserts the starved case and the canvas is not starved")
        _settle(app, 25)
        assert r._redraw_tries <= 6, f"still retrying: {r._redraw_tries}"
        settled = r._redraw_tries
        _settle(app, 25)
        assert r._redraw_tries == settled, "the retry loop never stopped"

        # ...and a real redraw hands the retries back.  Re-packing alone is
        # not enough -- Tk fires no <Configure> for a widget that comes back
        # at the size it had, and without one nothing reschedules the draw.
        r.c_before.pack(fill="both", expand=True)
        h0 = app.winfo_height()
        app.geometry("2560x1440-4000+0"); _settle(app, 25)
        _skip_if_window_cannot_grow(h0, app.winfo_height())
        assert r.c_before.winfo_height() > 20, "the resize did not free height"
        assert r._redraw_tries == 0, "a successful redraw must reset the count"
    finally:
        app.destroy()


def test_guides_are_pulled_off_the_black_cross():
    """Guides are pulled off the black cross by dragging.

    Cross press starts a pull (no guide yet); dragging shows a dashed preview;
    dropping over the corrected pane commits the guide.  Guides can also be
    pulled from the band GUIDE_BAND_PX either side of Q2's picture edge,
    and grabbed on the canvas.
    Guides live in ``_after_guides`` as ``(kind, pos)`` tuples and are drawn
    as plain grey lines with tag "after_guide" on ``c_after``.
    """
    app = _app()
    try:
        _loaded(app, 1920, 1200)
        r = app.review
        assert r._after_guides == [], "a fresh photograph should have no guides"

        from pc.layout import CROSS_BORDER

        class _E:
            def __init__(self, x, y, xr=None, yr=None):
                self.x, self.y = x, y
                self.x_root = xr if xr is not None else x
                self.y_root = yr if yr is not None else y

        pw, ph_ = r.winfo_width(), r.winfo_height()
        aox, aoy = r._after_off
        iw_, ih_ = r._ph_a.width(), r._ph_a.height()
        # Root offsets so _after_pos can convert panel coords → canvas coords.
        rx0 = r.c_after.winfo_rootx()
        ry0 = r.c_after.winfo_rooty()

        def cross_drop(x, y, kind):
            """Simulate the full cross press→pull→drop cycle committing a guide."""
            e_press = _E(x, y)
            r._on_cross_press(e_press)
            assert r._cross_pull == kind, (
                f"press should set _cross_pull to {kind}, got {r._cross_pull}")
            # Drag over the image so the preview is visible.
            e_drag = _E(x, y, rx0 + aox + 50, ry0 + aoy + 40)
            r._on_cross_pull(e_drag)
            # Drop over the image → commits the guide.
            e_drop = _E(x, y, rx0 + aox + 50, ry0 + aoy + 40)
            r._on_cross_drop(e_drop)

        # --- Cross pull: top border → horizontal guide ---
        top_x = pw // 4  # avoid panel center where dx=0
        cross_drop(top_x, CROSS_BORDER // 2, "h")
        assert len(r._after_guides) == 1, "cross drop over image should create one guide"
        assert r._after_guides[0][0] == "h", "top press should make a horizontal guide"
        _settle(app, 4)

        # --- Cross pull: left gutter → vertical guide ---
        left_y = ph_ // 3  # avoid panel center where dy=0
        cross_drop(CROSS_BORDER // 2, left_y, "v")
        assert len(r._after_guides) == 2, "second cross drop should add a second guide"
        assert r._after_guides[1][0] == "v", "left press should make a vertical guide"
        _settle(app, 4)

        # --- Cross pull: bottom border → horizontal guide ---
        cross_drop(pw // 3, ph_ - CROSS_BORDER // 2, "h")
        assert len(r._after_guides) == 3, "third cross drop should add a third guide"
        assert r._after_guides[2][0] == "h", "bottom press should make a horizontal guide"
        _settle(app, 4)

        # --- Cross pull: right gutter → vertical guide ---
        cross_drop(pw - CROSS_BORDER // 2, left_y, "v")
        assert len(r._after_guides) == 4, "fourth cross drop should add a fourth guide"
        assert r._after_guides[3][0] == "v", "right press should make a vertical guide"
        _settle(app, 4)

        # Four guides: two horizontal (top + bottom), two vertical (left + right).
        kinds = sorted(g[0] for g in r._after_guides)
        assert kinds == ["h", "h", "v", "v"], f"expected two h and two v, got {kinds}"

        # --- Cross drop NOT over image → guide discarded ---
        before = len(r._after_guides)
        e_press = _E(top_x, CROSS_BORDER // 2)
        r._on_cross_press(e_press)
        assert r._cross_pull == "h"
        # Drop back on the cross: root coords land in the cross gutter,
        # so _after_pos returns None and the guide is discarded.
        e_drop_cross = _E(top_x, CROSS_BORDER // 2, rx0 - 50, ry0 - 50)
        r._on_cross_drop(e_drop_cross)
        assert len(r._after_guides) == before, (
            "dropping back on the cross should discard the guide")

        # --- The guide band: GUIDE_BAND_PX outside and inside every edge ---
        # A press in the band creates a guide that is already being dragged;
        # a press that never travels is a click and adds nothing.
        from pc.layout import GUIDE_BAND_PX as B
        pulls = (  # (press x, press y, drag dx, drag dy, kind) -- all four sides
            (-(B - 3), ih_ // 3, 60, 0, "v"),       # outside left
            (B - 3, ih_ // 3, 60, 0, "v"),          # inside left
            (iw_ + (B - 3), ih_ // 3, -60, 0, "v"),  # outside right
            (iw_ // 3, -(B - 3), 0, 40, "h"),       # outside top
            (iw_ // 3, ih_ - (B - 3), 0, -40, "h"),  # inside bottom
        )
        for px, py, dx, dy, kind in pulls:
            before = len(r._after_guides)
            r._on_crop_press(_E(aox + px, aoy + py))
            assert len(r._after_guides) == before + 1, (
                f"a press in the band at ({px},{py}) should start a guide")
            assert r._guide_drag is not None, "the new guide is already dragging"
            r._on_crop_drag(_E(aox + px + dx, aoy + py + dy))
            want = (px + dx) if kind == "v" else (py + dy)
            assert r._after_guides[-1] == (kind, want), (
                f"the guide should follow the drag: {r._after_guides[-1]} != {(kind, want)}")
            r._on_crop_release(_E(aox + px + dx, aoy + py + dy))
            assert r._guide_drag is None and len(r._after_guides) == before + 1, (
                "release keeps the pulled guide")
            _settle(app, 2)
        # A bare click in the band adds nothing.
        before = len(r._after_guides)
        r._on_crop_press(_E(aox + 2, aoy + ih_ // 3))
        r._on_crop_release(_E(aox + 2, aoy + ih_ // 3))
        assert len(r._after_guides) == before, "a click in the band is not a pull"
        # Outside the band, nothing starts.
        r._on_crop_press(_E(aox - (B + 4), aoy + ih_ // 3))
        assert r._guide_drag is None and len(r._after_guides) == before, (
            "a press beyond the band must not start a guide")
        r._on_crop_release(_E(aox - (B + 4), aoy + ih_ // 3))
        r._crop_drag_start = None
        # Hovering the band shows which way the guide will run.
        r._on_crop_motion(_E(aox - 3, aoy + ih_ // 3))
        assert r.c_after.cget("cursor") == "sb_h_double_arrow"
        r._on_crop_motion(_E(aox + iw_ // 3, aoy + ih_ + 3))
        assert r.c_after.cget("cursor") == "sb_v_double_arrow"
        _settle(app, 4)

        # --- Canvas grab: press on an existing guide grabs it (no new guide) ---
        h_pos = r._after_guides[0][1]
        before = len(r._after_guides)
        hit = r._after_guide_at(50, h_pos, iw_, ih_)
        assert hit is not None, "pointer on the guide should be detected"
        assert hit[0] == "h", "should detect a horizontal guide"
        r._on_crop_press(_E(aox + 50, aoy + h_pos))
        assert len(r._after_guides) == before, (
            "pressing over an existing guide must not create a new one")
        assert r._guide_drag is not None, "canvas press on guide should set _guide_drag"
        # Drag the grabbed guide: it moves 1:1 from the image edge.
        r._on_crop_drag(_E(aox + 50, aoy + h_pos + 30))
        assert abs(r._after_guides[hit[1]][1] - (h_pos + 30)) < 1, (
            "the picked-up guide should track the cursor")
        r._on_crop_release(_E(aox + 50, aoy + h_pos + 30))
        assert r._guide_drag is None, "release should clear _guide_drag"
        _settle(app, 4)

        # --- Guide items are drawn on c_after with tag "after_guide" ---
        items = r.c_after.find_withtag("after_guide")
        assert len(items) >= 2, f"expected at least 2 guide lines, found {len(items)}"
        for item in items:
            assert r.c_after.type(item) == "line", "a guide should be a line"
            assert int(float(r.c_after.itemcget(item, "width"))) == 1

        # --- Hover cursor shows drag direction on the cross ---
        r._on_cross_motion(_E(top_x, CROSS_BORDER // 2))
        assert r.cget("cursor") == "sb_v_double_arrow", (
            "hovering top border should show sb_v_double_arrow")
        r._on_cross_motion(_E(CROSS_BORDER // 2, left_y))
        assert r.cget("cursor") == "sb_h_double_arrow", (
            "hovering left gutter should show sb_h_double_arrow")
        r.config(cursor="")

    finally:
        app.destroy()


def test_the_cross_breaks_vertically_with_the_pictures_on_top():
    """The cross is broken vertically (2026-10-19, user-directed), asserted on
    the real widgets.

    The two preview fields on top take every extra pixel and the two lower fields
    only what their content asks for -- the lower row is not in a uniform group
    (weight 0), it sizes to the per-photograph controls plus the run bar, and the
    upper row (weight 1) takes the rest. The pictures want every extra pixel, the
    controls only what they need. The columns stay one uniform group, so all four
    fields still share a width; the heights are the thing that broke. What is
    pinned is that the top row dominates the bottom row at every window size, and
    that the plus shape is the plus shape (loader lower-left, controls
    lower-right), which no size assertion alone would catch.
    """
    from pc import layout as L
    for w, h in ((1920, 1080), (2560, 1440)):
        app = _app()
        try:
            _loaded(app, w, h)
            r = app.review
            cells = [r.cell_before, r.cell_after, r.loader, r.cell_ui]
            sizes = [(c.winfo_width(), c.winfo_height()) for c in cells]
            assert max(s[0] for s in sizes) - min(s[0] for s in sizes) <= 2, (
                f"{w}x{h}: the field widths differ: {sizes}")
            # The rows broke: the two previews match each other, the two lower
            # fields match each other, and the top row is the bigger of the two.
            top = [s[1] for s in sizes[:2]]
            bot = [s[1] for s in sizes[2:]]
            assert max(top) - min(top) <= 2, f"{w}x{h}: the two previews differ: {sizes}"
            assert max(bot) - min(bot) <= 2, f"{w}x{h}: the two lower fields differ: {sizes}"
            assert top[0] > bot[0], (
                f"{w}x{h}: the pictures are not bigger than the controls: {sizes}")
            # The lower row sizes to its content and the upper row takes the
            # rest, so the pictures dominate at every window size.
            assert top[0] >= 2 * bot[0], (
                f"{w}x{h}: the pictures should dominate the controls: {sizes}")

            # The dark shows through at one width everywhere (user,
            # 2026-10-21: "same width everywhere", a border on top too): left,
            # right, top, bottom and the cross between the fields.
            assert abs(r.cell_before.winfo_x() - L.CROSS_SIDE) <= 1, (
                f"{w}x{h}: the left border is {r.cell_before.winfo_x()}")
            assert abs(r.cell_before.winfo_y() - L.CROSS_BORDER) <= 1, (
                f"{w}x{h}: the top border is {r.cell_before.winfo_y()}")
            gap = r.cell_after.winfo_x() - (r.cell_before.winfo_x() + r.cell_before.winfo_width())
            assert abs(gap - L.CROSS_GAP) <= 1, f"{w}x{h}: the cross is {gap} wide"
            right = r.winfo_width() - (r.cell_after.winfo_x() + r.cell_after.winfo_width())
            assert abs(right - L.CROSS_SIDE) <= 1, f"{w}x{h}: the right border is {right}"
            bottom = r.winfo_height() - (r.cell_ui.winfo_y() + r.cell_ui.winfo_height())
            assert abs(bottom - L.CROSS_BORDER) <= 1, f"{w}x{h}: the bottom border is {bottom}"
            vgap = r.cell_after.winfo_x() - (r.cell_before.winfo_x() + r.cell_before.winfo_width())
            assert abs(vgap - L.CROSS_GAP) <= 1, f"{w}x{h}: the vertical cross is {vgap}"
            hgap = r.loader.winfo_y() - (r.cell_before.winfo_y() + r.cell_before.winfo_height())
            assert abs(hgap - L.CROSS_GAP) <= 1, f"{w}x{h}: the horizontal cross is {hgap}"

            # The plus shape: loader lower-left, controls lower-right.
            assert r.loader.winfo_rooty() > r.cell_before.winfo_rooty(), (
                f"{w}x{h}: the loader is not below the previews")
            assert abs(r.loader.winfo_rootx() - r.cell_before.winfo_rootx()) <= 1, (
                f"{w}x{h}: the loader is not in the left column")
            assert r.cell_ui.winfo_rooty() > r.cell_after.winfo_rooty(), (
                f"{w}x{h}: the controls are not in the bottom row")

            assert r._btns.winfo_ismapped(), f"{w}x{h}: the Save row went missing"
            assert r.add_btn.winfo_ismapped(), f"{w}x{h}: the add-images icon went missing"
            assert r.add_folder_btn.winfo_ismapped(), f"{w}x{h}: the add-folder icon went missing"
        finally:
            app.destroy()


def test_the_mid_edge_handles_move_one_edge_on_its_axis():
    """The crop rectangle has handles at its four corners *and* at the midpoint
    of each edge.  A corner drag re-places two edges; a mid-edge drag moves
    just that one, along its axis -- top/bottom vertically, left/right
    horizontally -- which is how you trim the band one side opened without
    re-placing the other three.
    """
    app = _app()
    try:
        _loaded(app, 1920, 1080)
        r = app.review
        iw, ih = r._ph_a.width(), r._ph_a.height()
        # A loaded photograph may already carry an auto-crop; start from a
        # known full frame so the assertions below have one reference.
        assert r.session.set_crop_rect(0, 0, iw, ih, iw, ih)

        hit = r._grab_handle(0.5 * iw, 2, iw, ih)
        assert hit == ("edge", "top"), f"the top midpoint was not grabbed: {hit}"
        hit = r._grab_handle(iw - 2, 0.5 * ih, iw, ih)
        assert hit == ("edge", "right"), f"the right midpoint was not grabbed: {hit}"
        hit = r._grab_handle(2, 2, iw, ih)
        assert hit is not None and hit[0] == "corner", f"a corner was not grabbed: {hit}"

        rect = r._edge_drag_rect("top", 0.5 * iw, int(ih * 0.2), iw, ih)
        x0, y0, x1, y1 = rect
        assert abs(y0 - ih * 0.2) <= 1, f"the top edge did not follow the cursor: {rect}"
        assert abs(x0) <= 1 and abs(x1 - iw) <= 1 and abs(y1 - ih) <= 1, (
            f"a vertical drag moved a horizontal edge: {rect}")

        rect = r._edge_drag_rect("bottom", int(iw * 0.3), int(ih * 0.8), iw, ih)
        x0, y0, x1, y1 = rect
        assert abs(y1 - ih * 0.8) <= 1 and abs(y0) <= 1, (
            f"the bottom edge did not follow the cursor: {rect}")

        # clamped to the frame and so it cannot cross its opposite edge
        x0, y0, x1, y1 = r._edge_drag_rect("top", 0.5 * iw, -50, iw, ih)
        assert abs(y0) <= 1, f"the top edge escaped the frame: {(x0, y0, x1, y1)}"
        x0, y0, x1, y1 = r._edge_drag_rect("top", 0.5 * iw, ih + 50, iw, ih)
        assert abs(y0 - ih) <= 1, f"the top edge crossed the bottom: {(x0, y0, x1, y1)}"
    finally:
        app.destroy()


def test_dragging_inside_the_crop_pans_it_and_clips_to_the_frame():
    """A press inside the kept region pans the whole crop rectangle; a press in
    the cut-away area draws a new one from scratch.  Pan past an edge and that
    border becomes the new crop border -- what went outside is clipped away, not
    dragged along for the ride."""
    app = _app()
    try:
        _loaded(app, 1920, 1080)
        r = app.review
        iw, ih = r._ph_a.width(), r._ph_a.height()
        assert r.session.set_crop_rect(0.25 * iw, 0.25 * ih, 0.75 * iw, 0.75 * ih, iw, ih)

        class _E:                                # the handlers only read .x / .y
            def __init__(self, x, y):
                self.x, self.y = x, y
        ox, oy = r._after_off
        r._on_crop_press(_E(ox + 0.5 * iw, oy + 0.5 * ih))
        assert r._crop_drag_start[0] == "move", (
            f"a press inside the kept region should pan: {r._crop_drag_start}")
        offx, offy = r._crop_drag_start[1], r._crop_drag_start[2]
        assert abs(offx - 0.25 * iw) <= 1 and abs(offy - 0.25 * ih) <= 1, (
            f"the grab offset is not from the crop corner: {r._crop_drag_start}")

        r._on_crop_press(_E(ox + 0.1 * iw, oy + 0.1 * ih))
        assert r._crop_drag_start[0] == "corner", (
            f"a cut-away press should draw a new rectangle: {r._crop_drag_start}")

        # A pan that stays in the frame is a pure translation of the stored crop.
        x0, y0, x1, y1 = r._move_drag_rect(offx, offy, 0.6 * iw, 0.3 * ih, iw, ih)
        assert abs(x0 - 0.35 * iw) <= 2 and abs(y0 - 0.05 * ih) <= 2, (
            f"the rectangle did not translate with the cursor: {(x0, y0, x1, y1)}")
        assert abs(x1 - 0.85 * iw) <= 2 and abs(y1 - 0.55 * ih) <= 2, (
            f"a pan changed the crop size: {(x0, y0, x1, y1)}")

        # Pan up past the top edge: the frame border becomes the new top border
        # and the height shrinks by exactly what went out.
        x0, y0, x1, y1 = r._move_drag_rect(offx, offy, 0.5 * iw, 0.1 * ih, iw, ih)
        assert abs(y0) <= 1, f"the top was not clipped to the frame: {(x0, y0, x1, y1)}"
        assert abs(y1 - 0.35 * ih) <= 2, (
            f"the height did not shrink by the overflow: {(x0, y0, x1, y1)}")
    finally:
        app.destroy()


def test_the_gdino_prompt_field_and_mode_are_reachable():
    """The gdino mask mode is not a half-feature: the source combobox must offer
    it and the prompt entry must exist with a sane default, so a user can type a
    word and press Enter to re-find the box.  Pinned off-screen -- the 'Tested is
    not reachable' gotcha this file exists to close."""
    app = _app()
    try:
        r = app.review
        assert "gdino" in list(r.cb_maskmode["values"]), (
            f"the mask source combobox must offer gdino: {r.cb_maskmode['values']}")
        assert r.v_gdino_prompt.get() == "building", (
            f"the prompt should default to 'building', got {r.v_gdino_prompt.get()!r}")
    finally:
        app.destroy()


def test_gdino_batch_uses_the_stored_birefnet_model_without_asking():
    """A gdino batch also needs BiRefNet weights for its matte. If the user already
    saved a path (prefs), _ensure_birefnet_model must accept it and never pop the
    dialog -- asked once, not per run. The old guard only checked 'birefnet', so a
    gdino batch with a stored model would still fall through to the prompt."""
    app = _app()
    try:
        from pc import gui as G
        real_ask = G.messagebox.askyesnocancel
        # If the code wrongly reaches the dialog, return Cancel (None) so the test
        # fails fast instead of hanging on a modal window off-screen.
        G.messagebox.askyesnocancel = lambda *a, **k: None
        try:
            app._remembered["birefnet_model"] = ASSET   # any existing file passes isfile
            app.v_maskpath.set("")                       # fresh reload: entry empty
            app.v_mask.set("gdino")
            assert app._ensure_birefnet_model() is True, (
                "a stored BiRefNet path must be accepted without the dialog")
        finally:
            G.messagebox.askyesnocancel = real_ask
    finally:
        app.destroy()


def test_theme_switch_retints_canvas_backgrounds():
    """Switching to a light theme must re-tint every tk.Canvas whose bg was an
    INK value -- the 'still bg black!' report.  The canvases are created with
    bg=INK["field"] and _retint_bg walks them; this pins that the walk actually
    reaches them and the swap fires."""
    from pc.gui import THEMES, INK

    app = _app()
    try:
        app.geometry("1200x800-4000+0")
        app.update(); time.sleep(0.02)
        # Load an image so the canvases exist with real content.
        app._add([ASSET])
        _settle(app)
        review = app.review
        assert review is not None, "no review panel"
        old_field = INK["field"]
        # Switch to Dark Grey theme directly (bypasses the combobox trace).
        app._switch_theme("Dark Grey")
        app.update(); time.sleep(0.02)
        new_field = THEMES["Dark Grey"]["field"]
        assert new_field != old_field, "test needs distinct palettes"
        cb = review.c_before.cget("bg")
        ca = review.c_after.cget("bg")
        assert cb == new_field, (
            f"c_before bg is {cb!r} after switching to Dark Grey, expected {new_field!r}")
        assert ca == new_field, (
            f"c_after bg is {ca!r} after switching to Dark Grey, expected {new_field!r}")
        # Switch back and confirm the round-trip.
        app._switch_theme("Minimal Black")
        app.update(); time.sleep(0.02)
        assert review.c_before.cget("bg") == THEMES["Minimal Black"]["field"], (
            "c_before did not return to Minimal Black field colour")
        assert review.c_after.cget("bg") == THEMES["Minimal Black"]["field"], (
            "c_after did not return to Minimal Black field colour")
    finally:
        app.destroy()


def test_phosphor_lays_a_graded_ground_and_takes_it_away_again():
    """Phosphor is the one palette with a `grad`, and the ramp is PIXELS -- the
    re-tint walk cannot produce it and cannot remove it.  Two halves, and the
    second is the one that bites: INK is updated and never rebuilt, so a key
    only one palette defines outlives that palette unless something pops it.
    Switch Phosphor -> Dark Grey without that and its flat panes keep the
    ramp."""
    from pc.gui import INK

    app = _app()
    try:
        app.geometry("1200x800-4000+0")
        app.update(); time.sleep(0.02)
        app._add([ASSET])
        _settle(app)
        review = app.review
        assert review is not None, "no review panel"

        app._switch_theme("Phosphor")
        _settle(app)
        assert INK.get("grad"), "Phosphor did not put a gradient in INK"
        for name in ("c_before", "c_after"):
            c = getattr(review, name)
            ground = c.find_withtag("ground")
            assert ground, f"{name} has no ground item under Phosphor"
            # It must cover the canvas, and it must be UNDER the photograph.
            assert c.bbox(ground[0]) == (0, 0, c.winfo_width(), c.winfo_height()), (
                f"{name} ground does not cover the canvas: {c.bbox(ground[0])}")
            assert c.find_all()[0] == ground[0], (
                f"{name} ground is not the lowest item -- it would cover the picture")

        app._switch_theme("Dark Grey")
        # wait for the redraw, not a fixed count: with live lama the default a
        # redraw takes about a second and a fixed pump raced it under load
        _settle_until(app, lambda: not (review.c_before.find_withtag("ground")
                                        or review.c_after.find_withtag("ground")), n=400)
        assert INK.get("grad") is None, (
            "the Phosphor gradient outlived Phosphor: INK still carries it")
        for name in ("c_before", "c_after"):
            c = getattr(review, name)
            assert not c.find_withtag("ground"), (
                f"{name} still has a graded ground after switching to Dark Grey")
    finally:
        # INK is a module-level dict that `_switch_theme` mutates in place
        # (gui.py: `INK.update(new)`), so a theme left switched here outlives
        # this App and lands in the next test in the same worker process.
        # Alphabetically this test runs BEFORE
        # test_theme_switch_retints_canvas_backgrounds, which starts by
        # asserting its two palettes differ -- and failed for exactly this
        # reason, in a test that had nothing wrong with it.
        try:
            app._switch_theme("Minimal Black")
        except Exception:
            pass
        app.destroy()


def test_the_yaw_controls_follow_the_yaw_not_the_checkbox():
    """A control that is dead while the thing it controls is running says the
    opposite of what is happening.

    The yaw slider, its box and the detail dial were greyed from
    `correct_horizontal` alone, while `current_yaw` gives a hand-drawn
    horizontal precedence over that flag entirely. Measured on Platte_1: one
    drawn horizontal with the checkbox off yaws the picture by 2.88 degrees --
    and all three controls sat greyed out while it did. The detail dial's own
    tooltip names three routes to a yaw; the greying followed one.

    Both halves, because "always enabled" would pass the first alone."""
    import math

    app = _app()
    try:
        app.geometry("1200x800-4000+0")
        app.update(); time.sleep(0.02)
        app._add([ASSET])
        _settle(app)
        r = app.review
        assert r is not None and r.session is not None

        assert not r.v_correct_horizontal.get(), "this test starts with HA off"
        assert str(r._yaw_scale.cget("state")) == "disabled", (
            "no yaw in force: the slider must be grey")

        s = r.session
        s.add_control_line(60, 260, 450, 150, display_scale=1.0, kind="h")
        s.refit()
        r._sync_from_session()
        app.update(); time.sleep(0.02)

        assert abs(math.degrees(s.current_yaw())) > 0.2, (
            "the drawn horizontal has to produce a yaw, or this proves nothing")
        assert not r.v_correct_horizontal.get(), (
            "one horizontal must not flip the checkbox -- that is the case "
            "this test is about")
        for name in ("_yaw_scale", "_yaw_spin", "_keep_px_scale"):
            assert str(getattr(r, name).cget("state")) == "normal", (
                f"{name} is greyed while a yaw of "
                f"{math.degrees(s.current_yaw()):+.2f} deg is applied")
    finally:
        app.destroy()


def test_paste_button_exists_and_handler_is_wired():
    """The addbar must carry a paste button that calls App._paste_screenshot.

    Pinned by loading an image (which builds the addbar), asserting the button
    widget exists, and confirming the handler method is present on App."""
    app = _app()
    try:
        app.geometry("1200x800-4000+0")
        app._add([ASSET])
        _settle(app)
        review = app.review
        assert review is not None, "no review panel"
        btn = getattr(review, "add_paste_btn", None)
        assert btn is not None, "addbar has no paste button (add_paste_btn)"
        # cget("command") returns a Tcl string for tk.Button; verify the
        # handler method exists and is callable on the App instance instead.
        assert hasattr(app, "_paste_screenshot"), "App._paste_screenshot missing"
        assert callable(app._paste_screenshot), "App._paste_screenshot not callable"
    finally:
        app.destroy()


def test_a_mark_is_dragged_out_in_one_gesture_and_a_still_click_still_removes():
    """Press-drag-release places one control line; a press that never moved removes.

    Both halves are here because they share one binding, and that is the whole
    risk: making the rubberband work is the easy part, keeping "click an
    existing mark to delete it" alive beside it is the part that breaks.  The
    drag landed in `74da5a9` with no coverage at all -- the ledger still called
    it open while the code was already doing it -- so this asserts the wiring
    (`_on_click_before` -> `<B1-Motion>` -> `<ButtonRelease-1>`), not the
    helpers in isolation.
    """
    app = _app()
    try:
        _loaded(app, 1280, 800)
        r = app.review
        s = r.session
        r.v_mark.set(True)
        ox, oy = r._before_off

        def ev(dx, dy):                       # image-origin -> canvas coords
            return types.SimpleNamespace(x=dx + ox, y=dy + oy)

        # How long a segment must be *in display pixels* to clear the session's
        # own minimum -- derived rather than guessed, so the test does not break
        # when the window size or the asset changes.
        inv = s.scale / max(r._before_scale, 1e-9)
        need = s.MIN_CONTROL_LENGTH_FRAC * min(s.gray.shape[:2]) / inv
        top, bottom = 40, 40 + int(need * 1.5)

        r._on_click_before(ev(60, top))
        assert r._pending_mark is not None, "the press should start a mark"
        r._on_before_b1motion(ev(62, bottom))
        assert r._mark_moved, "motion should turn the press into a drag"
        r._on_before_b1release(ev(62, bottom))
        assert len(s.control_lines) == 1, "press-drag-release places one line"
        assert r._pending_mark is None, "the gesture should be finished, not half-open"

        mid = (top + bottom) // 2
        r._on_click_before(ev(61, mid))       # on the line, and never moved
        r._on_before_b1release(ev(61, mid))
        assert len(s.control_lines) == 0, (
            "a still click on a placed mark must still remove it -- that "
            "gesture has nowhere else to live")

        short = max(2, int(need * 0.2))
        r._on_click_before(ev(200, top))
        r._on_before_b1motion(ev(200, top + short))
        r._on_before_b1release(ev(200, top + short))
        assert len(s.control_lines) == 0, "a few pixels is not a control line"
    finally:
        app.destroy()


def test_each_tool_mode_has_a_key_and_the_keys_do_not_replace_each_other():
    """m / b / s reach the three tool modes, and SAM is not flipped twice.

    Tk's `bind()` *replaces* a handler for the same sequence, so three modes on
    one key would silently leave two dead -- this asserts three distinct
    sequences are actually registered, not just that the dispatcher works.

    All three handlers read their variable and none flips it, so the flip is the
    caller's -- the contract the palette buttons rely on too. `_on_sam_toggle`
    used to flip its own; a second flip anywhere cancels the first and the tool
    looks dead, which is what this asserts against.
    """
    app = _app()
    try:
        _loaded(app, 1280, 800)
        r = app.review
        for seq in ("<m>", "<b>", "<s>"):
            assert app.bind(seq), f"{seq} is not bound"

        for seq, var, handler in (("<m>", "v_mark", "_on_mark_toggle"),
                                  ("<b>", "v_stroke", "_on_stroke_toggle"),
                                  ("<s>", "v_sam", "_on_sam_toggle")):
            before = getattr(r, var).get()
            app._tool_key(var, handler)
            assert getattr(r, var).get() != before, (
                f"{seq} must flip {var} exactly once -- a handler that flips it "
                f"again cancels the caller out and the tool looks dead")
            app._tool_key(var, handler)          # and back, so modes do not stack
            assert getattr(r, var).get() == before, f"{seq} twice must return"
    finally:
        app.destroy()


def test_the_mark_tool_reads_the_plane_off_the_drawing_and_removes_the_right_one():
    """One Mark tool: which way the segment leans decides vertical or horizontal.

    Replaces the orientation combobox (2026-09-15, user). The risk the change
    invites is not the classification -- it is removal: both planes are on
    screen at once now, and an index into `control_lines` means nothing against
    `control_hlines`, so deleting the wrong array is one forgotten `kind=` away.
    The last block is that regression.
    """
    app = _app()
    try:
        _loaded(app, 1280, 800)
        r = app.review
        s = r.session
        r.v_mark.set(True)
        ox, oy = r._before_off
        K = type(r)._kind_for

        # The decision itself, including the stated 45 deg tiebreak.
        assert K(0, 0, 2, 100) == "v", "falls more than it runs -> vertical"
        assert K(0, 0, 100, 2) == "h", "runs more than it falls -> horizontal"
        assert K(0, 0, 50, 50) == "v", "exactly 45 deg is documented as vertical"
        assert K(0, 0, -2, -100) == "v", "direction of travel must not matter"

        def ev(dx, dy):
            return types.SimpleNamespace(x=dx + ox, y=dy + oy)

        inv = s.scale / max(r._before_scale, 1e-9)
        need = s.MIN_CONTROL_LENGTH_FRAC * min(s.gray.shape[:2]) / inv
        span = int(need * 1.5)
        top = 40

        # A steep drag lands among the verticals, a shallow one among the
        # horizontals -- no selector touched in between.
        r._on_click_before(ev(60, top))
        r._on_before_b1motion(ev(63, top + span))
        r._on_before_b1release(ev(63, top + span))
        assert len(s.control_lines) == 1 and len(s.control_hlines) == 0, (
            "a steep drag must be a vertical")

        hy = top + span + 30
        r._on_click_before(ev(60, hy))
        r._on_before_b1motion(ev(60 + span, hy + 3))
        r._on_before_b1release(ev(60 + span, hy + 3))
        assert len(s.control_hlines) == 1 and len(s.control_lines) == 1, (
            "a shallow drag must be a horizontal, and must not touch the verticals")

        # The regression: a still click on the horizontal removes *it*.
        r._on_click_before(ev(60 + span // 2, hy + 1))
        r._on_before_b1release(ev(60 + span // 2, hy + 1))
        assert len(s.control_hlines) == 0, "the horizontal under the cursor must go"
        assert len(s.control_lines) == 1, (
            "and the vertical must survive -- an index is meaningless without "
            "the array it came from")
    finally:
        app.destroy()


def test_the_facade_strip_still_works_after_a_second_photograph_loads():
    """The strip moved into `_build`, which re-runs per photograph.

    That is the trap this file has already paid for twice: `_build` runs again
    for every load while the widgets elsewhere were made once, so a rebuilt
    variable leaves a control pointing at something nobody reads and the tool
    dies silently from the second photograph on. The guard is only as good as a
    test that actually loads twice and then presses the real widget.
    """
    app = _app()
    try:
        _loaded(app, 1280, 800)
        r = app.review
        if getattr(r, "v_strip", None) is None:
            raise pytest.skip("facade strip not present")
        first = (r.v_strip, r.v_strip_x0, r.v_strip_x1)
        r.load(r.session.path, r.settings, r.dest_path)   # a second photograph
        _settle(app)
        assert (r.v_strip, r.v_strip_x0, r.v_strip_x1) == first, (
            "the strip's variables were rebuilt by the second load -- the "
            "checkbox now sets one nobody reads")
        # The facade strip is now an icon + popup, not a checkbox. Drive it via the var.
        r.v_strip.set(True)
        r._apply_strip()
        _settle(app)
        assert r.v_strip.get(), "setting the var must turn the strip on"
        assert r.session.strip is not None, (
            "the strip is on, so the session must be restricted")
        r.v_strip.set(False)
        r._apply_strip()
        _settle(app)
        assert r.session.strip is None, "turning it off must lift the restriction"
    finally:
        app.destroy()


def test_only_one_tool_can_be_in_your_hand_at_a_time():
    """Turning a tool on puts every other one down.

    Not cosmetic. `_on_click_before` tests the modes in a fixed order, so two on
    at once is not "both available" -- the earlier one wins every click and the
    later one looks broken with nothing in the log. That is how the mask brush
    went dead: SAM is tested first, and nothing ever switched SAM off, so a user
    who had used SAM could never paint again.

    Pressing through `_tool_key` rather than setting the variables, because the
    flip and the handler together are the contract; setting a variable by hand
    would test a path no user can take.
    """
    app = _app()
    try:
        _loaded(app, 1280, 800)
        r = app.review
        modes = [(v, h) for v, h in type(r)._TOOL_MODES
                 if getattr(r, v, None) is not None]
        assert len(modes) >= 3, "expected at least mark, brush and SAM"

        for var, handler in modes:
            app._tool_key(var, handler)
            _settle(app, 4)
            assert getattr(r, var).get(), f"{var} should now be on"
            others = [o for o, _ in modes if o != var and getattr(r, o).get()]
            assert not others, (
                f"turning {var} on left {others} on as well -- whichever "
                f"`_on_click_before` tests first will eat every click")

        # And putting the last one down leaves nothing held.
        last, last_handler = modes[-1]
        app._tool_key(last, last_handler)
        _settle(app, 4)
        assert not any(getattr(r, v).get() for v, _ in modes), (
            "switching the last tool off must leave no tool active")
    finally:
        app.destroy()


def test_sizing_the_pen_does_not_cripple_the_next_erase():
    """Alt+right sizes the brush; afterwards right-drag must still erase fully.

    Found by running the gestures and measuring the painted fraction, not by
    reading: every handler was individually correct. The gap was a binding.
    Alt+right release arrives as <Alt-ButtonRelease-3>, nothing was bound to it,
    so `_pen_anchor` stayed set -- and `_on_erase_motion` returns early while it
    is. The erase then dropped its whole dragged stroke and removed only the
    press and release points, which reads as "erasing does not work".

    Nothing breaks at the end of the sizing gesture itself. That is why it
    survived: the damage lands on the next gesture but one.
    """
    app = _app()
    try:
        _loaded(app, 1280, 800)
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

        # Size the pen and let go, exactly as the bindings deliver it.
        r._on_pen_size_start(ev(200, 200))
        r._on_pen_size_drag(ev(260, 200))
        r._on_pen_size_end()
        assert r._pen_anchor is None, (
            "the sizing drag must forget its anchor on release -- while it is "
            "set, `_on_erase_motion` bails out and erasing loses its stroke")
        # Krita's Shift+drag with Shift let go before the button: the release
        # arrives as a plain <ButtonRelease-1> (OpenCode review, 2026-10-24).
        r._on_shift_press(ev(200, 200))
        r._on_shift_motion(ev(260, 200))
        r._on_before_b1release(ev(260, 200))
        assert r._pen_anchor is None, "a plain release must end the sizing too"

        s.paint = None
        drag(r._on_click_before, r._on_before_b1motion, r._on_before_b1release)
        painted = float(s.paint.mean())
        assert painted > 0, "the brush must paint something to erase"

        drag(r._on_erase_press, r._on_erase_motion, r._on_erase_release)
        left = 0.0 if s.paint is None else float(s.paint.mean())
        assert left < painted * 0.2, (
            f"right-drag must erase the whole stroke, not just its ends: "
            f"{painted:.4%} -> {left:.4%}")
    finally:
        app.destroy()


def test_loupe_alt_damping_tracks_cursor_at_half_rate():
    """Holding Alt makes the loupe crop centre follow the cursor at 1/LOUPE_MAG.

    Without Alt the centre jumps to the cursor in one step; with Alt it moves
    only a fraction of the remaining distance, so repeated small motions creep
    toward the target instead of racing it.
    """
    app = _app()
    try:
        _loaded(app, 1280, 800)
        r = app.review
        s = r.session
        ox, oy = r._before_off

        def ev(dx, dy, state=0):
            return types.SimpleNamespace(x=dx + ox, y=dy + oy, state=state)

        # Bring the loupe up as a mark press would.
        r.v_mark.set(True)
        r._on_click_before(ev(100, 100))
        assert r._loupe is not None, "pressing in mark mode must raise the glass"
        assert r._loupe_center is not None, (
            "the press must render the crop immediately -- an empty glass "
            "until the first motion was the bug this test guards")

        # First motion without Alt: centre snaps to the cursor (1:1).
        r._loupe_move(ev(200, 150))
        c0 = r._loupe_center
        assert c0 is not None, "centre must be recorded after a move"
        exp_x = int(round((ev(200, 150).x - ox) / r._before_scale))
        exp_y = int(round((ev(200, 150).y - oy) / r._before_scale))
        assert abs(c0[0] - exp_x) <= 1 and abs(c0[1] - exp_y) <= 1, (
            f"without Alt the centre should be at the cursor: {c0} vs ({exp_x},{exp_y})")

        # Now hold Alt and move further: the centre must lag.
        ALT = 0x0008
        r._loupe_move(ev(300, 250, state=ALT))
        c1 = r._loupe_center
        f = 1.0 / layout.LOUPE_MAG
        # Recompute expected: new cursor in image px
        nx = int(round((ev(300, 250).x - ox) / r._before_scale))
        ny = int(round((ev(300, 250).y - oy) / r._before_scale))
        exp_cx = c0[0] + (nx - c0[0]) * f
        exp_cy = c0[1] + (ny - c0[1]) * f
        assert abs(c1[0] - exp_cx) <= 1 and abs(c1[1] - exp_cy) <= 1, (
            f"with Alt the centre should be damped: {c1} vs ({exp_cx:.1f},{exp_cy:.1f})")

        # The damped centre must NOT have jumped all the way to the new cursor.
        assert abs(c1[0] - nx) > 2 or abs(c1[1] - ny) > 2, (
            "Alt damping must leave a gap between centre and cursor")

        r._loupe_hide()
    finally:
        app.destroy()


def test_no_tools_field_row_asks_for_more_width_than_it_gets():
    """A clipped control is mapped, full-width and invisible to the old check.

    `test_the_save_button_is_reachable_at_every_window_size` asserts every
    child has `winfo_width() > 0`, which is the right question for a control
    that never got packed -- and the wrong one for a control that did. Tk maps
    a child that does not fit and reports its full requested width; the
    container simply clips it. Measured before the fix: the mask row asked for
    ~1097 px of a 910 px field at the 1920x1080 window floor, "Clear Mask"
    rendered as "Cle", and every existing assertion stayed green. It was found
    by looking at a screenshot, which is not a thing a suite can do.

    So this asks the container, not the child: no row may request more width
    than the field it sits in. Written generically over the tool-field rows so
    that adding one more control to either of them trips this rather than
    silently eating the last button.
    """
    app = _app()
    try:
        fw, fh = layout.MIN_WINDOW                # the window's own floor
        app.geometry(f"{fw}x{fh}-4000+0")
        app.update(); time.sleep(0.02)
        if app.winfo_width() < fw or app.winfo_height() < fh:
            raise SkipTest(f"the display cannot hold the {fw}x{fh} floor "   # noqa: F821
                           f"(got {app.winfo_width()}x{app.winfo_height()})")
        app._add([ASSET])
        _settle(app)
        review = app.review
        assert review is not None, "no review panel"

        rows, seen = [], set()
        def walk(widget):
            for child in widget.winfo_children():
                try:
                    cls = child.winfo_class()
                except Exception:
                    cls = ""
                if cls in ("TButton", "TCheckbutton", "TCombobox"):
                    parent = child.master
                    if id(parent) not in seen:
                        seen.add(id(parent))
                        rows.append(parent)
                walk(child)
        walk(review)
        assert rows, "found no control rows at all -- the walk is wrong"

        # Group each container's children into visual LINES, and ask the
        # geometry manager which line a child is on rather than guessing from
        # pixels. Two earlier attempts had NO TEETH and both failed the same
        # way -- they passed on the known-broken code:
        #   1. "skip any container whose children are not all at one y" threw
        #      away the very frame that overflowed, because it also held the
        #      note label and the opacity slider on later rows.
        #   2. "group by winfo_y()" split a single grid row into one group per
        #      widget, because `sticky="w"` centres children vertically, so a
        #      combobox and a label on the same row sit at different y.
        # Both were caught by reverting the fix and re-running; a test that
        # cannot fail is not a test, and this one had to be shown failing.
        too_wide = []
        for row in rows:
            field = row.winfo_width()
            if field <= 1:
                continue                      # never mapped; a different bug
            lines = {}
            for k in row.winfo_children():
                try:
                    mgr = k.winfo_manager()
                    if mgr == "grid":
                        key = ("grid", k.grid_info().get("row"))
                    elif mgr == "pack":
                        key = ("y", k.winfo_y() // 16)
                    else:
                        continue
                except Exception:
                    continue
                lines.setdefault(key, []).append(k)
            for key, kids in lines.items():
                if len(kids) < 2:
                    continue                  # a lone wide label may wrap
                need = sum(k.winfo_reqwidth() for k in kids)
                if need > field:
                    labels = [repr(k.cget("text"))[:24]
                              for k in kids if "text" in k.keys()]
                    too_wide.append(
                        f"{need}px wanted in {field}px on line {key}: "
                        + ", ".join(labels))
        # A row's own container can grow with it while the clip happens a level
        # up, so also ask every mapped container in the panel.
        def _walk(w):
            for c in w.winfo_children():
                if c.winfo_ismapped() and c.winfo_children():
                    if c.winfo_reqwidth() > c.winfo_width() + 2:
                        too_wide.append(f"{c.winfo_reqwidth()}px wanted in "
                                        f"{c.winfo_width()}px by {c}")
                    _walk(c)
        _walk(review)
        assert not too_wide, (
            f"a control row is clipped at the {fw}x{fh} window floor:\n  "
            + "\n  ".join(too_wide))
    finally:
        app.destroy()


def test_a_missing_lama_is_said_and_never_switched_to_telea_silently():
    """User, 2026-10-23: lama was not installed on the first Mac and the window
    filled with telea without a word. The notice must say so -- on opening,
    on choosing lama, after a save whose note names the fallback -- and stay
    quiet when lama is there."""
    from pc import gui as GUI
    real = GUI._lama_installed
    try:
        for installed in (False, True):
            GUI._lama_installed = lambda: installed
            app = _app()
            try:
                app.v_fill.set("lama")
                _loaded(app, 1400, 800)
                r = app.review
                r.v_fill.set("lama")
                note = lambda: getattr(r, "_notice", "") or ""
                _settle_until(app, lambda: "lama is not installed" in note(), n=40)
                said = "lama is not installed" in note()
                assert said == (not installed), (installed, note())
                r._notice = ""
                assert r._fill_fallback_notice() == (not installed)
            finally:
                app.destroy()
        GUI._lama_installed = lambda: True
        app = _app()
        try:
            _loaded(app, 1400, 800)
            r = app.review
            assert r._fill_fallback_notice("telea filled 12% in 0.1s (lama unavailable: x)")
            assert "telea fills the band instead" in r._notice
            assert not r._fill_fallback_notice("lama filled 12% in 3.0s")
        finally:
            app.destroy()
    finally:
        GUI._lama_installed = real


def test_the_deps_button_is_a_menu_that_installs_only_what_is_ticked():
    """User, 2026-10-24: "soll nicht blind installieren. torch lama telea geo".
    The button opens a menu of what is MISSING ("dont ask for things that are
    already there"): nothing ticked, installed ones only named, telea named as
    built in; ticking lama takes torch along while torch is missing;
    only the ticked rows are installed, on a thread, with a summary after."""
    from pc import depinstall as DI
    from pc import gui as GUI

    def row(key, installed):
        return {"key": key, "label": key + " pkg", "module": "x", "pip": [],
                "size": "~1 MB", "installed": installed, "purpose": key + " for"}
    cat = [row("torch", False), row("lama", False), row("geocalib", False),
           row("mlsd", True), row("dnd", False)]
    shown, ran = [], []
    saved = (DI.catalogue, DI.install, GUI.messagebox.showinfo, GUI.messagebox.showwarning)
    DI.catalogue = lambda: [dict(r) for r in cat]
    GUI.messagebox.showinfo = lambda title, text, **k: shown.append(text)
    GUI.messagebox.showwarning = lambda title, text, **k: shown.append("WARN " + text)

    def fake_install(items, on_step=None, py=None):
        time.sleep(0.3)                        # the window must answer meanwhile
        ran.append([i["key"] for i in items])
        if on_step:
            on_step(0, len(items), items[0]["label"])
        return [(i, True, "") for i in items]
    DI.install = fake_install
    app = _app()
    try:
        _settle(app, 5)
        app._install_deps()
        _settle(app, 3)
        v = app._deps_vars
        assert not any(x.get() for x in v.values()), "something was ticked unasked"
        # installed: not offered, named once
        assert "mlsd" not in app._deps_boxes and "dnd" in app._deps_boxes
        texts = [w.cget("text") for w in app._w_deps.winfo_children()[0].winfo_children()
                 if isinstance(w, GUI.ttk.Label)]
        assert any("telea" in t and "built in" in t for t in texts), texts
        assert any(t.startswith("already installed") and "mlsd" in t for t in texts), texts
        # Nothing ticked: nothing installed, the menu stays.
        app._deps_go()
        _settle(app, 3)
        assert ran == [] and app._w_deps is not None
        # lama takes torch along; untick torch and lama goes with it.
        app._deps_boxes["lama"].invoke()
        assert v["lama"].get() and v["torch"].get()
        app._deps_boxes["torch"].invoke()
        assert not v["torch"].get() and not v["lama"].get()
        app._deps_boxes["dnd"].invoke()
        app._deps_boxes["geocalib"].invoke()
        app._deps_go()
        assert app.btn_deps.cget("text") in ("installing", "1/3")
        _settle_until(app, lambda: shown, n=150)
        assert ran == [["torch", "geocalib", "dnd"]], ran
        assert shown and shown[0].startswith("ok: torch pkg"), shown
        assert "GeoCalib guesses the focal" in shown[0], shown
        assert app.btn_deps.cget("text") == "deps" and not app._deps_busy
        # Everything there: says so, opens no menu, installs nothing.
        for r in cat:
            r["installed"] = True
        shown.clear(); ran.clear(); app._w_deps = None
        app._install_deps()
        assert shown and shown[0].startswith("Everything optional is installed already")
        assert app._w_deps is None and ran == []
    finally:
        (DI.catalogue, DI.install, GUI.messagebox.showinfo, GUI.messagebox.showwarning) = saved
        app.destroy()


def test_the_icons_come_from_the_shipped_fonts_and_survive_without_them():
    """The first Mac run (2026-10-23) died in `_build` with "Image is required":
    the load keys quoted Segoe's icon fonts, which only Windows has. They now
    come from fonts/ (Material Symbols, Noto Emoji) on every OS -- and if that
    folder is missing, the drawn twins still give every key a glyph."""
    from pc import gui as G
    from pc import layout
    g = layout.tool_glyph()
    for cp in ("E145", "E2C7", "E14F"):
        img = G._icon_pil(cp, g, G.INK["dim"])
        assert img is not None and img.getbbox() is not None, f"{cp} not in the shipped font"
    dino = G._icon_pil("1F996", g, G.INK["dim"], emoji=True, mono=True)
    assert dino is not None and dino.getbbox() is not None, "no dinosaur in the shipped emoji font"
    real = G.ICON_FONT
    G.ICON_FONT = os.path.join(G.FONT_DIR, "missing.ttf")
    try:
        app = _app()
        try:
            app.geometry("1400x800-4000+0")
            app.update(); time.sleep(0.02)
            r = app.review
            for b in (r.add_btn, r.add_folder_btn, r.add_paste_btn):
                assert str(b.cget("image")), f"{b} has no icon"
        finally:
            app.destroy()
    finally:
        G.ICON_FONT = real


def test_the_shipped_text_fonts_are_what_the_window_uses():
    """Inter and JetBrains Mono come from fonts/ and are registered for this
    process, so the window needs neither installed. JetBrains Mono is not
    installed on the development machine, so finding it proves the
    registration rather than the system."""
    from tkinter import font as tkfont
    app = _app()
    try:
        fams = set(tkfont.families(app))
        assert "JetBrains Mono" in fams and "Inter" in fams, sorted(f for f in fams if "Mono" in f)
        assert tkfont.nametofont("TkDefaultFont").actual("family") == "Inter"
        app._theme_var.set("Phosphor")
        app.update()
        assert tkfont.nametofont("TkFixedFont").actual("family") == "JetBrains Mono"
    finally:
        app._theme_var.set("Minimal Black")      # INK is module state: restore
        app.update()
        app.destroy()


def test_every_tool_key_is_the_same_size_and_every_glyph_renders():
    """A column of keys is judged on its alignment before anything else.

    Three of the seven keys used to be assembled by hand beside the factory
    that built the rest, and picked up `highlightthickness=2` on the way --
    which Tk adds OUTSIDE the requested width, so those keys were 4 px larger
    than their neighbours in a vertical column where nothing hides it. They
    also lit up in a different colour when held. Both are the same defect:
    a second construction site.

    So this asserts the two things a second construction site breaks -- one
    size for every key, and a glyph actually present on each -- rather than
    asserting any particular size, which would be a number copied out of the
    code instead of a claim about it.
    """
    from pc.gui import _DRAWN_GLYPHS, _glyph_pil, INK
    from pc import layout

    # Every mark this palette draws must actually draw something. A glyph that
    # comes back None falls through to a bullet character, which is how a key
    # can look "fine" while saying nothing.
    for name in _DRAWN_GLYPHS:
        img = _glyph_pil(name, layout.tool_glyph(), INK["dim"])
        assert img is not None, f"{name} rendered nothing"
        assert img.size == (layout.tool_glyph(),) * 2, f"{name} is {img.size}"
        assert img.getbbox() is not None, f"{name} rendered an empty box"

    app = _app()
    try:
        app.geometry("1920x1080-4000+0")
        app.update(); time.sleep(0.02)
        app._add([ASSET])
        _settle(app)
        keys = getattr(app.review, "_palette_btns", [])
        assert len(keys) >= 6, f"only {len(keys)} tool keys -- the palette is not built"
        sizes = {(k.winfo_width(), k.winfo_height()) for k in keys}
        assert len(sizes) == 1, (
            "the tool keys are not all one size, so the column does not line "
            f"up: {sorted(sizes)}")
        highlights = {int(k.cget("highlightthickness")) for k in keys}
        assert len(highlights) == 1, (
            "a key carries a different highlight thickness, which Tk adds "
            f"outside the requested width: {sorted(highlights)}")
        selects = {str(k.cget("selectcolor")) for k in keys}
        assert len(selects) <= 2, (
            "more than two 'held' colours in one palette -- which tool am I "
            f"holding has to have one answer (the brush inverts, so two): {sorted(selects)}")
    finally:
        app.destroy()


def test_every_theme_keeps_the_accent_button_readable():
    """The primary action button must be legible in every palette.

    Its foreground was the literal "#0b1017" -- one dark ink, hardcoded for
    all of them. Five of the six palettes have a bright accent and read fine
    on it. **Amiga 500 does not**: accent #0055BB under dark text measures a
    contrast ratio of 2.74 against WCAG AA's 4.5, which made Save -- the
    button the whole review loop ends on -- the least legible thing in the
    window. Nothing said so, because a hex literal in a widget option looks
    like a decision somebody made.

    4.5 is WCAG AA for body text, and it is asserted against the constant
    rather than against the numbers the palettes currently produce: a new
    theme is exactly how this comes back.
    """
    from pc.gui import THEMES, INK, on_accent, contrast_ratio

    AA = 4.5
    palettes = {"Minimal Black": INK["accent"]}
    palettes.update({name: p.get("accent", INK["accent"])
                     for name, p in THEMES.items()})
    assert len(palettes) >= 3, "the theme table looks empty -- check the import"

    bad = []
    for name, accent in sorted(palettes.items()):
        ratio = contrast_ratio(on_accent(accent), accent)
        if ratio < AA:
            bad.append(f"{name}: accent {accent} gives {ratio:.2f} (< {AA})")
    assert not bad, "the accent button is unreadable in:\n  " + "\n  ".join(bad)


def test_no_colour_is_typed_into_a_widget_below_the_palette_tables():
    """"Every colour comes from INK" is a hard rule; this is what enforces it.

    Until 2026-09-20 there were twenty-four bare `#rrggbb` literals scattered
    through the draw calls, and the rule was unenforceable prose: you could
    not tell a deliberate colour from a forgotten one by looking, which is how
    the Amiga contrast bug above survived and how the tool palette ended up
    with two different "held" colours.

    They are named now -- `INK` and `THEMES` for chrome, `OVERLAY` for marks
    drawn ON a photograph (which deliberately do NOT follow the theme, because
    they are read against the picture and not against the palette),
    `MASK_SWATCHES`, `ICON_WHITE`, `GUIDE_GREY`. All of those live at the top
    of the module. So the rule becomes checkable: below them, a colour must be
    referred to by name.

    Parsed rather than grepped, so that a hex written inside a docstring --
    such as the one in `on_accent` explaining the literal it replaced -- is
    not mistaken for a colour being used.
    """
    import ast
    import os
    import re

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "src", "pc", "gui.py")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src, filename="gui.py")

    # The tables end where the first function after them begins.
    boundary = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_relative_luminance":
            boundary = node.lineno
            break
    assert boundary, ("cannot find `_relative_luminance`, which marks the end of "
                      "the colour tables -- if it was renamed, update this test")

    hexish = re.compile(r"^#[0-9a-fA-F]{3,8}$")
    offenders = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and hexish.match(node.value) and node.lineno > boundary):
            offenders.append(f"gui.py:{node.lineno}: {node.value!r}")
    assert not offenders, (
        "a colour is typed in below the palette tables, so it cannot be "
        "retinted and no theme switch will reach it. Name it in INK, OVERLAY "
        "or a constant instead:\n  " + "\n  ".join(offenders))


def test_the_detail_slider_is_greyed_out_unless_a_yaw_is_being_corrected():
    """It only bites while a yaw is applied, so it is dead while one is not.

    `keep_pixels` chooses which edge of a foreshortened facade keeps
    its sampling, and `warp._keep_near_edge` returns untouched when the yaw is
    zero.  A live control that changes nothing is worse than a greyed-out one:
    it invites the user to move it and then says nothing back.

    Three claims, and the third is the one this file keeps paying for:
      * greyed out at build while horizontal auto is off;
      * following the checkbox, both ways;
      * **still alive on the SECOND photograph.**  `_build` re-runs per image
        while these variables are made once, and a variable rebuilt by `_build`
        goes stale -- a control that sets something nobody reads.  Met three
        times here already (mask brush, facade strip, planar), so it is
        asserted rather than hoped for.
    """
    app = _app()
    try:
        _loaded(app, 1920, 1080)
        r = app.review
        scale = r._keep_px_scale

        assert str(scale.cget("state")) == "disabled", (
            "horizontal auto is off at build, so the pixel reference must be "
            f"greyed out (got {scale.cget('state')!r})")

        r.v_correct_horizontal.set(True)
        r._on_horizontal_toggle()
        _settle(app, 5)
        assert str(scale.cget("state")) == "normal", (
            "the slider must follow the checkbox it is gated on")

        r.v_keep_px.set(0.8)
        _settle(app, 5)
        assert abs(r.session.settings.keep_pixels - 0.8) < 1e-9, (
            "moving the control must reach the settings the save reads -- the "
            "'tested is not reachable' failure, where SAM drew an outline for "
            "weeks and never touched the estimator")

        r.v_correct_horizontal.set(False)
        r._on_horizontal_toggle()
        _settle(app, 5)
        assert str(scale.cget("state")) == "disabled"

        # ...and now the trap.  Moving the dial is a change, so opening the next
        # photograph would ask to save it; this test is about the reload.
        r._mark_clean()
        app._add([ASSET])                      # second load: rebuilds the panel
        _settle(app)
        r = app.review
        assert abs(r.v_keep_px.get() - 0.8) < 1e-9, (
            "this is a preference about output size, not a property of the "
            "photograph; it must survive the next one")
        assert abs(r.session.settings.keep_pixels - 0.8) < 1e-9, (
            "a fresh session starts at the config default, so the shown value "
            "must be pushed through or the widget lies about what will be saved")
        r.v_correct_horizontal.set(True)
        r._on_horizontal_toggle()
        _settle(app, 5)
        r.v_keep_px.set(0.3)
        _settle(app, 5)
        assert abs(r.session.settings.keep_pixels - 0.3) < 1e-9, (
            "the control died on the second photograph -- the `_build` rebuild "
            "trap, met for the fourth time")
    finally:
        app.destroy()


def test_opening_a_file_replaces_the_photo_and_asks_only_when_it_changed():
    """User, 2026-10-21: opening another file never queues it.  Unchanged, the
    new photograph simply opens; changed, the user is asked to save first
    (Yes saves, No discards, Cancel stays).  Only a folder is walked."""
    import shutil
    import tempfile
    from pc import gui as GUI
    real = GUI.messagebox.askyesnocancel
    answers, asked = [], []

    def fake(*a, **k):
        asked.append(a[0])
        return answers.pop(0)

    GUI.messagebox.askyesnocancel = fake
    tmp = tempfile.mkdtemp()
    app = _app()
    try:
        names = ("1070912stkzg.jpg", "Alte_Scheune.jpg", "79cb33878fd0ed76d368e7cfa8827e15.jpg")
        a, b, c = (shutil.copy(os.path.join(os.path.dirname(ASSET), n), tmp) for n in names)
        app.geometry("1600x1000-4000+0")
        app.update_idletasks(); app.update()
        r = app.review
        app._add([a]); _settle(app)
        assert not r.is_dirty(), "a photograph that was only opened counts as changed"
        app._add([b]); _settle(app)
        assert asked == [], "asked about a photograph nobody changed"
        assert os.path.basename(r.session.path) == "Alte_Scheune.jpg"
        assert not getattr(app, "_review_queue", []), "a single file was queued"

        r.session.add_control_line(100, 50, 104, 400)          # a change
        assert r.is_dirty()
        answers.append(None)                                   # Cancel
        app._add([c]); _settle(app)
        assert os.path.basename(r.session.path) == "Alte_Scheune.jpg", "Cancel left"
        answers.append(False)                                  # No: discard
        app._add([c]); _settle(app)
        assert os.path.basename(r.session.path) == names[2]
        assert not any(f.endswith("_corr.jpg") for f in os.listdir(tmp)), "No wrote a file"

        r.session.add_control_line(100, 50, 104, 400)
        answers.append(True)                                   # Yes: save first
        app._add([a]); _settle(app)
        assert os.path.exists(os.path.join(tmp, names[2][:-4] + "_corr.jpg")), "Yes did not save"
        assert os.path.basename(r.session.path) == "1070912stkzg.jpg"
        assert len(asked) == 3

        app._add([tmp]); _settle(app)                          # a folder walks
        assert app._review_total >= 3 and len(app._review_queue) == app._review_total - 1
    finally:
        GUI.messagebox.askyesnocancel = real
        app.destroy()
        shutil.rmtree(tmp, ignore_errors=True)


def test_both_pictures_stay_centred_when_the_canvas_grows_after_the_lock():
    """The render box is locked on the first draw, so a canvas that grows later
    (fullscreen, a resize) leaves room around the picture. Both panes must
    centre in the LIVE canvas. The after pane centred in the locked box, so
    after fullscreen it sat 64 px off the axis while the original beside it was
    centred (user, 2026-10-21: "ha image target not on axis"). The lock is
    shrunk here instead of going fullscreen, which is the same state."""
    app = _app()
    try:
        _loaded(app, 1920, 1080)
        r = app.review
        assert r.session is not None
        (bw, bh), (aw, ah) = r._img_box
        r._img_box = ((bw - 80, bh - 60), (aw - 80, ah - 60))
        r._redraw(); _settle(app, 20)
        for name, canvas, ph, off in (("before", r.c_before, r._ph_b, r._before_off),
                                      ("after", r.c_after, r._ph_a, r._after_off)):
            left = off[0]; right = canvas.winfo_width() - off[0] - ph.width()
            top = off[1]; bottom = canvas.winfo_height() - off[1] - ph.height()
            assert abs(left - right) <= 1 and abs(top - bottom) <= 1, (
                f"{name} picture off the axis: L{left} R{right} T{top} B{bottom}")
    finally:
        app.destroy()


def test_the_outline_switch_draws_the_source_border_where_the_warp_put_it():
    """"Outline" beside "Check lines" (user, 2026-10-21): the photograph's own
    border mapped through the correction onto the after picture. Its first
    point is the source's top-left corner, wherever the warp sent it; off
    removes it."""
    app = _app()
    try:
        _loaded(app, 1920, 1080)
        r = app.review
        r.session.set_manual(roll_deg=-3.0, pitch_deg=9.0, focal_35mm=24.0)
        r._redraw(); _settle(app, 10)
        assert not r.c_after.find_withtag("outline"), "outline drawn while off"
        r.v_after_outline.set(True); r._toggle_outline(); r._redraw(); _settle(app, 10)
        items = r.c_after.find_withtag("outline")
        assert items, "outline switched on but nothing drawn"
        k = r._ph_a.width() / float(r._after_base.shape[1])
        qx, qy = r.session.source_to_preview(0.0, 0.0)
        x, y = r.c_after.coords(items[0])[:2]
        assert abs(x - (r._after_off[0] + qx * k)) < 1.0 and abs(y - (r._after_off[1] + qy * k)) < 1.0, (
            f"top-left corner drawn at {(x, y)}")
        # never past the picture ("dont expand for outline")
        ox, oy = r._after_off
        for it in items:
            xs, ys = r.c_after.coords(it)[0::2], r.c_after.coords(it)[1::2]
            assert min(xs) >= ox - 1 and max(xs) <= ox + r._ph_a.width() + 1, xs
            assert min(ys) >= oy - 1 and max(ys) <= oy + r._ph_a.height() + 1, ys
        r.v_after_outline.set(False); r._toggle_outline(); r._redraw(); _settle(app, 10)
        assert not r.c_after.find_withtag("outline"), "outline survived switching it off"
    finally:
        app.destroy()


def test_the_fill_progress_notice_goes_away_once_the_preview_is_drawn():
    """With live lama the default (2026-10-21) the preview reports its fill
    steps, which raises the "filled -- 100%" notice -- and nothing took it down
    again, so it sat over the cross for good. Reported through the session's
    real step callback, as the fill does."""
    app = _app()
    try:
        _loaded(app, 1920, 1080)
        r = app.review
        r._fill_step("filled", 1.0)
        _settle(app, 5)
        assert r._wait.winfo_ismapped(), "premise: the step callback shows the notice"
        r._redraw(); _settle(app, 10)
        assert not r._wait.winfo_ismapped(), "the fill notice outlived the redraw"
    finally:
        app.destroy()


def test_hover_help_waits_a_second_before_it_appears():
    import tkinter as tk
    from pc import gui as G
    root = tk.Tk()
    root.geometry("200x80-4000+0")
    try:
        b = tk.Button(root, text="x")
        b.pack()
        G._attach_tooltip(b, "help")
        root.update()

        def tips():
            return [w for w in b.winfo_children() if isinstance(w, tk.Toplevel)]

        b.event_generate("<Enter>")
        root.update()
        assert not tips(), "help appeared at once"
        # a pointer that leaves before the delay never sees it
        b.event_generate("<Leave>")
        end = time.time() + layout.TOOLTIP_DELAY_MS / 1000 + 0.3
        while time.time() < end:
            root.update(); time.sleep(0.02)
        assert not tips(), "help appeared after the pointer left"
        # staying shows it after the delay
        b.event_generate("<Enter>")
        end = time.time() + layout.TOOLTIP_DELAY_MS / 1000 + 0.3
        while time.time() < end and not tips():
            root.update(); time.sleep(0.02)
        assert tips(), "help never appeared"
    finally:
        root.destroy()


def test_the_loupe_follows_the_pointer_and_comes_back_for_a_corner():
    """User, 2026-10-23: "loupe soll mauszeiger folgen". The glass sits
    LOUPE_OFFSET below-right of the pointer wherever it moves, and grabbing a
    placed rectangle corner brings it back (it went with the fourth click)."""
    from pc import layout
    app = _app()
    try:
        _loaded(app, 1920, 1080)
        r = app.review
        r.v_rect.set(True); r._on_rect_toggle(); _settle(app, 5)
        cb = r.c_before
        ox, oy = r._before_off
        for x, y in ((ox + 60, oy + 60), (ox + 160, oy + 110)):
            cb.event_generate("<Motion>", x=x, y=y); app.update()
            g = r._loupe
            assert g is not None, "no glass in rectangle mode"
            dx = g.winfo_rootx() - (cb.winfo_rootx() + x)
            dy = g.winfo_rooty() - (cb.winfo_rooty() + y)
            assert (dx, dy) == (layout.LOUPE_OFFSET, layout.LOUPE_OFFSET), (dx, dy)
        sc = r._before_scale
        pts = [(ox + 40, oy + 40), (ox + 300, oy + 50), (ox + 310, oy + 250), (ox + 30, oy + 240)]
        for x, y in pts:
            cb.event_generate("<ButtonPress-1>", x=x, y=y); app.update()
            cb.event_generate("<ButtonRelease-1>", x=x, y=y); app.update()
        assert len(r.session.planar_quad) == 4 and r._loupe is None
        cb.event_generate("<ButtonPress-1>", x=pts[0][0], y=pts[0][1]); app.update()
        assert r._rect_drag is not None and r._loupe is not None, "no glass on a corner grab"
        cb.event_generate("<ButtonRelease-1>", x=pts[0][0], y=pts[0][1]); app.update()
        assert sc > 0
    finally:
        app.destroy()


def test_dragging_a_mark_end_refits_and_redraws_at_a_throttled_rate():
    """User, 2026-10-23: "aktualisierung target reduziert, sonst lag". A drag
    moves the line on the canvas at once but refits and re-renders at most
    every DRAG_PREVIEW_MS, and once more on release."""
    from pc import layout
    app = _app()
    try:
        _loaded(app, 1920, 1080)
        r = app.review
        s = r.session
        sc = r._before_scale
        s.add_control_line(100 * sc, 50 * sc, 104 * sc, 400 * sc, display_scale=sc, kind="v")
        r._redraw(); _settle(app, 5)
        r.v_mark.set(True); r._on_mark_toggle(); _settle(app, 5)
        counts = {"refit": 0, "redraw": 0}
        orig_refit, orig_redraw = s.refit, r._redraw
        s.refit = lambda *a, **k: (counts.__setitem__("refit", counts["refit"] + 1), orig_refit(*a, **k))[1]
        r._redraw = lambda *a, **k: (counts.__setitem__("redraw", counts["redraw"] + 1), orig_redraw(*a, **k))[1]
        ox, oy = r._before_off
        x, y = int(ox + 104 * sc), int(oy + 400 * sc)
        r.c_before.event_generate("<ButtonPress-1>", x=x, y=y); app.update()
        assert r._mark_drag is not None, "endpoint not grabbed"
        n = 40
        t0 = time.perf_counter()
        for k in range(n):
            r.c_before.event_generate("<B1-Motion>", x=x + k, y=y, state=0x100)
            app.update(); time.sleep(0.01)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        allowed = int(elapsed_ms / layout.DRAG_PREVIEW_MS) + 2
        assert counts["redraw"] <= allowed, (counts, allowed)
        assert counts["refit"] <= allowed, (counts, allowed)
        r.c_before.event_generate("<ButtonRelease-1>", x=x + n - 1, y=y); app.update()
        assert counts["refit"] >= 1 and counts["redraw"] >= 1, counts
        x1 = s.control_lines[0][2] / s.scale
        assert abs(x1 - (104 + (n - 1) / sc)) < 2.0, x1
    finally:
        app.destroy()


def test_a_slow_fill_follows_the_quick_one_and_a_stale_one_is_dropped():
    """User, 2026-10-23: "erst telea, dann relaxed lama hinterher". The redraw
    shows the stand-in and leaves the slow backend's job; it runs off the Tk
    thread and lands on the after picture, unless the picture moved on."""
    import numpy as np
    from pc import inpaint as FILL
    app = _app()
    real_fill = FILL.fill
    try:
        _loaded(app, 1920, 1080)
        r = app.review
        s = r.session
        s.settings = s.settings.replace(fill="lama", live_fill_preview=True)
        s.set_manual(roll_deg=0.0, pitch_deg=6.0, focal_35mm=28.0)

        def fake(img, hole, settings, on_step=None):
            if settings.fill == "lama":
                out = img.copy(); out[hole] = (0, 0, 255)
                return out, "fake lama"
            return real_fill(img, hole, settings, on_step=on_step)
        FILL.fill = fake
        r._redraw()
        assert s.deferred_fill is not None or r._fill_after is not None, "no slow fill queued"
        quick = r._after_base.copy()
        assert _settle_until(app, lambda: r._after_base is not None
                             and not np.array_equal(r._after_base, quick), n=150), \
            "the slow fill never reached the after picture"
        # a result for an older render is thrown away
        r._fill_q.put((r._fill_gen - 1, np.zeros_like(quick), None))
        before = r._after_base.copy()
        r._fill_poll(); app.update()
        assert np.array_equal(r._after_base, before), "a stale fill replaced the picture"
    finally:
        FILL.fill = real_fill
        app.destroy()


def test_lines_and_rectangle_never_share_the_picture():
    """User, 2026-10-23: line tool or rectangle tool, never both on screen;
    each comes back when its tool is picked again."""
    app = _app()
    try:
        _loaded(app, 1920, 1080)
        r = app.review
        s = r.session
        sc = r._before_scale
        s.add_control_line(60 * sc, 40 * sc, 62 * sc, 300 * sc, display_scale=sc, kind="v")
        r.v_mark.set(True); r._on_mark_toggle(); _settle(app, 5)
        assert r.c_before.find_withtag("marks"), "the drawn line is not shown"
        r.v_rect.set(True); r._on_rect_toggle(); _settle(app, 5)
        assert not r.c_before.find_withtag("marks"), "a line is shown in rectangle mode"
        ox, oy = r._before_off
        for x, y in ((ox + 40, oy + 40), (ox + 300, oy + 50), (ox + 310, oy + 250), (ox + 30, oy + 240)):
            r.c_before.event_generate("<ButtonPress-1>", x=x, y=y); app.update()
            r.c_before.event_generate("<ButtonRelease-1>", x=x, y=y); app.update()
        _settle(app, 5)
        assert len(s.planar_quad) == 4 and r.c_before.find_withtag("rect_quad")
        r.v_mark.set(True); r._on_mark_toggle(); _settle(app, 5)
        assert not r.c_before.find_withtag("rect_quad"), "the rectangle is shown with the line tool"
        assert r.c_before.find_withtag("marks") and len(s.control_lines) == 1
        r.v_rect.set(True); r._on_rect_toggle(); _settle(app, 5)
        assert len(s.planar_quad) == 4 and r.c_before.find_withtag("rect_quad")
        assert not r.c_before.find_withtag("marks")
    finally:
        app.destroy()


def test_the_strip_handle_is_never_under_the_tool_palette():
    """User, 2026-10-23: the facade strip's ruler near the left edge put its
    grab handle (mid-height) under the centred tool palette, out of reach --
    "auf Handle-Hoehe kleine Aussparung". The palette is split there; the
    handle of a ruler at 2.5 % must fall in the gap, not on a key."""
    app = _app()
    try:
        _loaded(app, 1920, 1200)
        r = app.review
        r.v_strip.set(True)
        r.v_strip_x0.set(2.5)
        r.v_strip_x1.set(60.0)
        r._apply_strip()
        _settle(app)
        ox, oy = r._before_off
        iw, ih = r._ph_b.width(), r._ph_b.height()
        hx, hy = ox + 0.025 * iw, oy + ih // 2
        assert r._strip_ruler_at(hx, hy) == 0
        keys = [w for w in r._palette_btns + [r.add_btn, r.add_folder_btn, r.add_paste_btn]
                if w.winfo_ismapped()]
        assert len(keys) >= 6
        for k in keys:
            par = k.nametowidget(k.winfo_parent())
            x0, y0 = par.winfo_x() + k.winfo_x(), par.winfo_y() + k.winfo_y()
            x1, y1 = x0 + k.winfo_width(), y0 + k.winfo_height()
            near_x = x0 - 7 <= hx <= x1 + 7
            assert not (near_x and y0 - 7 <= hy <= y1 + 7), \
                (str(k), (x0, y0, x1, y1), (hx, hy))
    finally:
        app.destroy()


def test_the_footer_buttons_show_their_help_on_screen():
    """batch, deps and update sit on the bottom edge; their help used to open
    below it, off the screen (user, 2026-10-23)."""
    import tkinter as tk
    app = _app()
    try:
        sw, sh = app.winfo_screenwidth(), app.winfo_screenheight()
        app.geometry(f"1400x800+{sw - 1400}+{sh - 800}")      # flush bottom right
        _settle(app, 10)
        for btn in (app.btn_batch, app.btn_deps, app.btn_update):
            btn.event_generate("<Enter>")
            tips = []
            end = time.time() + layout.TOOLTIP_DELAY_MS / 1000 + 1.0
            while time.time() < end and not tips:
                app.update(); time.sleep(0.02)
                tips = [w for w in btn.winfo_children() if isinstance(w, tk.Toplevel)]
            assert tips, f"{btn.cget('text')}: no help"
            t = tips[0]
            t.update_idletasks()
            x, y, w, h = t.winfo_rootx(), t.winfo_rooty(), t.winfo_width(), t.winfo_height()
            assert 0 <= x and x + w <= sw and 0 <= y and y + h <= sh, \
                (btn.cget("text"), x, y, w, h, sw, sh)
            btn.event_generate("<Leave>")
            app.update()
    finally:
        app.destroy()


def test_lama_on_the_cpu_switches_the_live_preview_off_once_and_says_so():
    """User, 2026-10-23: with no GPU, lama takes seconds per preview; the live
    preview goes off after the first lama fill shows it runs on the CPU, with
    a notice saying how to bring it back -- once, and never on a GPU."""
    from pc import gui as G
    from pc import inpaint as FILL
    saved = (FILL.lama_device, G._CPU_LIVE_TOLD)
    app = _app()
    try:
        _loaded(app, 1400, 800)
        r = app.review
        for dev in ("cuda", "mps"):
            G._CPU_LIVE_TOLD = False
            FILL.lama_device = lambda dev=dev: dev
            r.v_livefill.set(True)
            r._live_cpu_check()
            assert r.v_livefill.get(), f"live switched off on {dev}"
        G._CPU_LIVE_TOLD = False
        FILL.lama_device = lambda: "cpu"
        r._notice = ""
        r._live_cpu_check()
        assert not r.v_livefill.get()
        assert not r.session.settings.live_fill_preview, "the session still renders live"
        assert "CPU" in r._notice and "live" in r._notice, r._notice
        r.v_livefill.set(True)          # the user ticks it back: it stays
        r._live_cpu_check()
        assert r.v_livefill.get(), "switched off a second time"
    finally:
        FILL.lama_device, G._CPU_LIVE_TOLD = saved
        app.destroy()


def test_the_controls_do_nothing_before_a_photograph_is_open():
    """The strip and the mask row are built before any photograph; seven of
    their handlers raised AttributeError on `session is None` (OpenCode
    review, 2026-10-24)."""
    app = _app()
    errors = []
    app.report_callback_exception = lambda e, v, tb: errors.append(f"{e.__name__}: {v}")
    try:
        app.geometry("1600x1000-4000+0")
        app.update_idletasks(); app.update()
        r = app.review
        assert r.session is None
        for name in ("_toggle_mask", "_on_mask_alpha", "_reset", "_use_auto",
                     "_on_slider", "_apply_fill", "_clear_crop", "_clear_marks"):
            try:
                getattr(r, name)()
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
        _settle(app, 4)
        assert not errors, errors
    finally:
        app.destroy()


def test_clear_marks_removes_the_vertical_and_the_horizontal_lines():
    """`clear(v) or clear(h)` stopped after the verticals (OpenCode review)."""
    app = _app()
    try:
        _loaded(app, 1280, 800)
        s = app.review.session
        s.add_control_line(100, 50, 104, 400)
        s.add_control_line(50, 300, 400, 302, kind="h")
        assert len(s.control_lines) == 1 and len(s.control_hlines) == 1
        app.review._clear_marks()
        assert len(s.control_lines) == 0
        assert len(s.control_hlines) == 0, "the horizontal line was left behind"
    finally:
        app.destroy()
