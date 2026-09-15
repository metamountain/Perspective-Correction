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

import numpy as np

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
    from pc.gui import App
    try:
        app = App(start_maximized=False)
    except Exception as exc:                       # no display (CI, headless)
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


def _loaded(app, w, h):
    app.geometry(f"{w}x{h}-4000+0")                # off-screen, per skills/ui.md
    app.update_idletasks(); app.update()
    app._add([ASSET])
    _settle(app)


def test_the_prominent_button_asks_rather_than_writing_unattended():
    """Manual review is the product, so the accent button must open a review
    window per photograph -- never start a run that writes files nobody saw.

    Asserted by pressing it, not by reading its label or its `command` string: a
    button that *says* Review while wired to the batch is exactly the failure
    worth catching, and only invoking it can tell the two apart. The unattended
    run must still exist (it was demoted, not deleted) and must not be the accent
    button.
    """
    app = _app()
    try:
        _loaded(app, 1920, 1080)
        assert getattr(app, "worker", None) is None, "nothing should be running yet"

        app.btn_run.invoke()
        _settle(app, 5)

        # The batch writes from a worker thread; a review walk never starts one.
        assert getattr(app, "worker", None) is None, (
            "the accent button started an unattended run -- it must open a "
            "review window instead, so no file is written unlooked-at")
        assert getattr(app, "_review_total", 0) >= 1, (
            "pressing it should have begun walking the selection one photograph "
            "at a time")

        # The unattended run is demoted, not deleted: still present, still its
        # own action, just no longer the primary gesture.  Not invoked here --
        # running it would write real files from a test.
        assert str(app.btn_run.cget("style")) == "Accent.TButton", (
            "the asking path is the one that should look primary")
        assert str(app.btn_batch.cget("style")) != "Accent.TButton", (
            "the unattended run must not be the primary gesture")
        assert app.btn_batch.cget("command") != app.btn_run.cget("command"), (
            "the two buttons must be distinct actions")
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


def test_save_routes_to_planar_when_four_corners_are_placed():
    """Planar Save must write the rectified view, not the roll/pitch correction.

    Four placed corners with the planar toggle on route to `save_planar`; the
    toggle off, or fewer than four corners, routes to `save`.  The regression is
    silent: without the branch, Save wrote the rotation-only image and the quad
    was gone with no error and nothing on screen saying so.
    """
    app = _app()
    try:
        _loaded(app, 1280, 800)
        r = app.review
        s = r.session
        calls = []
        s.save = lambda dst: (calls.append("save"), dst)[1]
        s.save_planar = lambda dst: (calls.append("save_planar"), dst)[1]
        r.on_saved = None
        r.v_planar.set(False)
        r._save()
        s.clear_planar()
        s.set_planar_point(0, 10, 10)
        s.set_planar_point(1, 200, 10)      # two corners: still cannot rectify
        r.v_planar.set(True)
        r._save()
        for i, (x, y) in enumerate([(10, 10), (500, 12), (12, 800), (498, 795)]):
            s.set_planar_point(i, x, y)     # four corners: the rectified view
        r._save()
        assert calls == ["save", "save", "save_planar"], f"wrong routing: {calls}"
    finally:
        app.destroy()


def test_planar_corners_can_be_placed_and_dragged():
    """Off-screen coverage for the planar quad itself.

    The Save-routing test covers what four corners *produce*; this covers that a
    person can actually *build* and *adjust* them: four clicks place four corners
    through the real click handler, clicking a placed corner grabs it, and a drag
    moves the grabbed corner before release ends it.  Without this, P11/P12's own
    bug (a grabbable radius that only worked at scale ~1) had no off-screen guard.
    """
    app = _app()
    try:
        _loaded(app, 1280, 800)
        r = app.review
        s = r.session
        r.v_planar.set(True)
        r._on_planar_toggle()            # real mode entry: also brings up the loupe
        pts = [(10, 10), (300, 14), (16, 250), (296, 246)]
        for dx, dy in pts:                     # display coords, image-origin relative
            r._on_planar_click(dx, dy)
        assert len(s.planar_quad) == 4, "four clicks should place four corners"
        before = s.planar_quad[0]
        r._on_planar_click(*pts[0])            # click on corner 0 grabs it
        assert r._planar_drag == 0, "clicking a placed corner grabs it"
        ev = types.SimpleNamespace(
            x=120 + r._before_off[0], y=130 + r._before_off[1])
        r._on_planar_drag(ev)                  # canvas coords carry the offset
        r._on_planar_release(None)
        assert s.planar_quad[0] != before, "dragging moves the grabbed corner"
        assert r._planar_drag is None, "release ends the drag"
    finally:
        app.destroy()


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

    There is no paned split and no strip below the cross: the review panel
    fills everything under the header, and the results list lives inside the
    cross's lower-left field with a height of its own. A bigger window grows
    the four equal fields -- and with them the previews.
    """
    sizes = [(1280, 800), (1920, 1200)]
    seen = []
    for w, h in sizes:
        app = _app()
        try:
            _loaded(app, w, h)
            assert str(app._w_tree.master) == str(app.review.loader), (
                "the results list left the cross's lower-left field")
            below = app.winfo_height() - app.review.winfo_y() \
                    - app.review.winfo_height()
            assert below < 12, (
                f"a strip sits below the cross: {below} px")
            seen.append((app.review.winfo_height(), app.tree.winfo_height()))
        finally:
            app.destroy()

    (r0, t0), (r1, t1) = seen
    assert t0 > 8 and t1 > 8, f"the results list collapsed: {t0} px, {t1} px"
    assert r1 > r0, f"the cross did not take the window's extra height " \
                    f"({r0} -> {r1} px)"


def test_loading_a_photograph_reports_no_failure():
    """skills/ui.md: every UI change ships with this."""
    app = _app()
    try:
        _loaded(app, 1280, 800)
    finally:
        app.destroy()


def test_roi_x_control_restricts_horizontal_evidence_and_defaults_off():
    """The find column's ROI x strip is the GUI half of ``--roi-x``: off by
    default (the frame stays unfiltered), and a valid strip sets
    ``session.roi_x`` in analysis pixels so refit can restrict horizontals to
    one facade on a corner view.  An inverted or empty strip is refused."""
    app = _app()
    try:
        _loaded(app, 1280, 800)
        r = app.review
        s = r.session
        assert s is not None and s.roi_x is None, "no strip by default"
        assert not r.v_roi.get(), "ROI x must start off"

        aw = s.gray.shape[1]
        r.v_roi.set(True)
        r.v_roi_x0.set(10.0)
        r.v_roi_x1.set(50.0)
        r._apply_roi()
        assert s.roi_x is not None, "a valid strip must set roi_x"
        x0, x1 = s.roi_x
        assert abs(x0 - 0.10 * aw) < 1e-6 and abs(x1 - 0.50 * aw) < 1e-6, (x0, x1, aw)

        # x0 >= x1 is not a strip -- the frame stays unfiltered.
        r.v_roi_x0.set(80.0)
        r.v_roi_x1.set(20.0)
        r._apply_roi()
        assert s.roi_x is None, "x0 >= x1 must not restrict anything"

        # Unchecking always clears it, whatever the spinboxes hold.
        r.v_roi_x0.set(10.0); r.v_roi_x1.set(50.0)
        r._apply_roi()
        assert s.roi_x is not None
        r.v_roi.set(False)
        r._apply_roi()
        assert s.roi_x is None, "unchecking must clear the strip"
    finally:
        app.destroy()


def test_enabling_an_roi_strip_turns_on_horizontal_correction():
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

        r.v_roi.set(True)
        r.v_roi_x0.set(10.0)
        r.v_roi_x1.set(50.0)
        r._apply_roi()
        assert s.roi_x is not None, "the strip is set"
        assert s.settings.correct_horizontal, \
            "a valid strip must turn on the horizontal correction it shapes"
        assert r.v_correct_horizontal.get(), "the checkbox shows the flag that turned on"

        # Clearing the strip leaves a deliberate yaw choice alone.
        r.v_roi.set(False)
        r._apply_roi()
        assert s.roi_x is None, "unchecking clears the strip"
        assert s.settings.correct_horizontal, \
            "clearing the strip must not force horizontal correction back off"
    finally:
        app.destroy()


def test_roi_x_draws_two_draggable_rulers_defaulting_to_20_and_80():
    """ROI x is set by hand, not blind percentages: ticking it on draws two
    draggable vertical rulers on the before pane -- default 20/80 %, not the
    useless full-frame 0/100 -- and greys out the sides that get ignored.  A drag
    moves a ruler, clamped to the frame edge and to its neighbour so the strip can
    never collapse below 2 % or be swiped away."""
    app = _app()
    try:
        _loaded(app, 1280, 800)
        r = app.review
        # The spinboxes default to a real strip, not the whole frame.
        assert r.v_roi_x0.get() == 20.0 and r.v_roi_x1.get() == 80.0, \
            "the default must restrict something, not cover the whole frame"

        # Off: no rulers on the canvas.
        assert r.c_before.find_withtag("roi_ruler") == (), "no rulers while off"

        r.v_roi.set(True)
        r._apply_roi()
        items = r.c_before.find_withtag("roi_ruler")
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
        r._on_roi_drag_start(0)

        class _E:                       # a minimal motion event
            pass
        e = _E()
        e.x = ox + iw                   # far right: must clamp, not cross
        r._on_roi_drag_move(e)
        assert r.v_roi_x0.get() <= r.v_roi_x1.get() - 2.0, \
            "the left ruler must not cross the right one"
        e.x = ox                        # far left: clamps to the frame edge
        r._on_roi_drag_move(e)
        assert r.v_roi_x0.get() == 0.0, "a ruler clamps to the frame edge"
        r._on_roi_drag_release()

        # Off again: the rulers disappear with the toggle.
        r.v_roi.set(False)
        r._apply_roi()
        assert r.c_before.find_withtag("roi_ruler") == (), \
            "unchecking must remove the rulers"
    finally:
        app.destroy()


def _assert_adjust_above_save(r, top, when):
    """The controls sit above the action row, in the packer and on screen.

    Both halves are needed: these rows are packed `side="bottom"`, so the
    packing order runs *upwards* from the bottom row and reads backwards --
    `_btns2` (the marking/crop row) sits directly above `_btns` (the Save row),
    and `_adj` comes immediately after `_btns2` precisely because it is drawn
    above both button rows.
    """
    slaves = top.pack_slaves()
    assert slaves.index(r._btns2) == slaves.index(r._btns) + 1, (
        f"{when}: the marking row left its slot above the Save row "
        f"({[str(w).rsplit('.', 1)[-1] for w in slaves]})")
    assert slaves.index(r._adj) == slaves.index(r._btns2) + 1, (
        f"{when}: the adjustments left their slot above the button rows "
        f"({[str(w).rsplit('.', 1)[-1] for w in slaves]})")
    assert r._adj.winfo_y() < r._btns2.winfo_y() < r._btns.winfo_y(), (
        f"{when}: the controls came back below the button rows")


def test_collapsing_the_adjustments_leaves_the_cross_where_it_was():
    """The four control rows fold away, and come back where they were.

    This used to assert that collapsing them bought the picture 100 px. Under
    the perfect cross it cannot, and the inversion *is* the feature: the two
    rows are equal halves of the pane, so folding the controls away changes
    what is inside the lower-right field and never the size of any field. The
    preview must therefore not move at all -- a layout whose boxes jump when a
    checkbox is pressed is the thing the equal cross exists to stop.

    Everything else this test guards is unchanged and still live: `pack`
    appends, so a re-expand that does not name its place puts the controls
    *below* the Save row. Order is read from `pack_slaves` rather than from
    coordinates, because on a short window the packer unmaps the bottom rows
    outright and a `winfo_y` comparison would then be reading stale numbers.
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
        assert abs(shut_h - open_h) <= 2, (
            f"the preview field moved {shut_h - open_h} px when the controls "
            f"folded; the cross's rows are equal halves and must not depend on "
            f"what is inside one of them")

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

    The picture is what this window is for, so a default arrangement that
    leaves it below ``layout.MIN_USEFUL_CANVAS`` is a broken default however
    tidy the controls look. `_adapt_adjust_default` is what enforces it, and it
    was written and then never called: the control columns stayed open at every
    height and a 1280x800 window opened on a 121 px preview.

    So this asserts the verdict, not a constant -- the arithmetic lives in
    `layout.adjustments_start_open` and is tested there. What must hold on the
    real widgets is that the panel agrees with it, that the picture clears the
    floor, and that Save is still reachable either way.
    """
    from pc import layout as L
    for w, h in ((1280, 800), (1920, 1080), (2560, 1400)):
        app = _app()
        try:
            _loaded(app, w, h)
            r = app.review
            canvas = r.c_before.winfo_height()
            assert r._btns.winfo_ismapped(), f"{w}x{h}: Save is not on screen"
            assert r.v_adjust.get() is L.adjustments_start_open(r.winfo_height()), (
                f"{w}x{h}: the control columns are "
                f"{'open' if r.v_adjust.get() else 'shut'} against the height's "
                f"verdict on a {r.winfo_height()} px pane")
            assert canvas >= L.MIN_USEFUL_CANVAS, (
                f"{w}x{h}: the preview opened at {canvas} px, below the "
                f"{L.MIN_USEFUL_CANVAS} px floor")
            if (w, h) == (2560, 1400):
                assert r.v_adjust.get() is True, (
                    "a 1440p pane can afford the control columns and must "
                    "start with them open")
        finally:
            app.destroy()


def test_a_hand_made_choice_about_the_controls_outranks_the_height():
    """`_adapt_adjust_default` stands down permanently once anyone touches it.

    It runs off `<Configure>`, which fires on every resize, so without the
    stand-down a user who opened the controls on a short window would watch
    them shut again the moment they dragged the window. `_adj_open` exists the
    instant the toggle is pressed and that is the whole guard.
    """
    app = _app()
    try:
        _loaded(app, 2560, 1400)                   # tall enough to start open
        r = app.review
        r.v_adjust.set(False); r._toggle_adjust(); _settle(app, 10)
        assert r.v_adjust.get() is False
        app.geometry("2560x1400-4000+0"); _settle(app, 10)
        assert r.v_adjust.get() is False, (
            "a resize re-opened controls the user had shut by hand")

        r.v_adjust.set(True); r._toggle_adjust(); _settle(app, 10)
        app.geometry("1280x800-4000+0"); _settle(app, 20)
        assert r.v_adjust.get() is True, (
            "a resize shut controls the user had opened by hand")
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
        app.geometry("2560x1440-4000+0"); _settle(app, 25)
        assert r.c_before.winfo_height() > 20, "the resize did not free height"
        assert r._redraw_tries == 0, "a successful redraw must reset the count"
    finally:
        app.destroy()


def test_the_grid_never_reaches_the_saved_file():
    """The overlay grid is a measuring instrument, not part of the photograph.

    It is painted on the canvas, so it *cannot* be composited into the output --
    and "cannot by construction" is exactly what was believed about several
    things that turned out to be wrong. `save()` is where a measuring
    instrument would become permanent, so the claim is asserted rather than
    argued: the same photograph saved with the grid on and with it off, byte
    for byte.

    Two guards stop it passing vacuously. The written file must differ from the
    original, or `ReviewSession.save`'s copy-through shortcut would be
    comparing two byte copies of the source whatever the grid did; and the grid
    must really add items to the canvas, or a checkbox that does nothing
    satisfies the whole test. The fit must also not be re-run: the grid goes
    through the redraw, never a refit.
    """
    import shutil
    import tempfile
    app = _app()
    d = tempfile.mkdtemp(prefix="bpc_grid_")
    try:
        _loaded(app, 1920, 1200)
        r = app.review
        # Do not advance the review queue out from under the test, and keep the
        # save cheap and machine-independent: the grid claim holds whatever the
        # fill mode is.
        r.on_saved = r.on_closed = None
        r.session.settings = r.session.settings.replace(fill="none")
        refits = []
        _real_refit = r.session.refit

        def _counted_refit(*a, **k):
            refits.append(1)
            return _real_refit(*a, **k)
        r.session.refit = _counted_refit

        assert r.v_grid.get() is False, "the grid is meant to start off"
        plain = os.path.join(d, "plain.jpg")
        r.dest_path = plain
        r._save()
        assert os.path.exists(plain), "nothing was written"
        bare_items = len(r.c_after.find_all())

        bare_before = len(r.c_before.find_all())
        r.v_grid.set(True)
        r._schedule_redraw()
        _settle_until(app, lambda: len(r.c_after.find_all()) > bare_items)
        assert len(r.c_after.find_all()) > bare_items, (
            "the grid drew nothing on the corrected pane, so this proves nothing")
        # The grid is a ruler for the *corrected* frame -- a true vertical should
        # run along a grid line -- so it has no business on the original, where it
        # measures nothing and competes with the detected lines.
        assert len(r.c_before.find_all()) == bare_before, (
            "the grid must draw on the after pane only, not over the original")

        gridded = os.path.join(d, "gridded.jpg")
        r.dest_path = gridded
        r._save()
        assert os.path.exists(gridded), "nothing was written with the grid on"

        a = open(plain, "rb").read()
        b = open(gridded, "rb").read()
        assert a == b, "the grid reached the saved file"
        assert a != open(r.session.path, "rb").read(), (
            "the save fell through to a byte copy of the original, so this "
            "comparison would pass whatever the grid did")
        assert not refits, "toggling the grid re-ran the fit instead of redrawing"
    finally:
        app.destroy()
        shutil.rmtree(d, ignore_errors=True)


def test_a_guide_is_pulled_out_of_the_border_as_a_plain_grey_line():
    """Guides are hairlines laid against an edge, not a ruler scale.

    This replaces `test_the_ruler_reads_the_same_height_on_left_and_right`,
    which asserted tick marks near the left and right edges appearing and
    vanishing **with the grid toggle**. That was a ruler *scale*, and the
    design it pinned was superseded (user, 2026-09-14: "rulers should be simple
    grey lines"). Bending the code back to satisfy it would have shipped a
    feature nobody asked for, so the test moved instead -- the thing this file
    warns about is a test written to match old behaviour outliving the decision.

    What is pinned now is the behaviour that was actually asked for: a press in
    the border zone outside the picture adds a guide, it draws as a single grey
    line spanning the frame, it carries no ticks or labels, and the grid toggle
    has nothing to do with it.
    """
    app = _app()
    try:
        _loaded(app, 1920, 1200)
        r = app.review
        assert r._after_guides == [], "a fresh photograph starts with no guides"

        aox, aoy = r._after_off
        iw, ih = r._ph_a.width(), r._ph_a.height()

        # press above the picture -- the border strip, not the photograph
        from pc.gui import GUIDE_GREY

        aox, aoy = r._after_off
        iw, ih = r._ph_a.width(), r._ph_a.height()

        class _E:
            """Panel-space x/y plus the root coords the drop handler reads."""
            def __init__(self, x, y, xr, yr):
                self.x, self.y = x, y
                self.x_root, self.y_root = xr, yr

        # press on the cross's horizontal centre bar, release over the picture
        pw, ph_ = r.winfo_width(), r.winfo_height()
        press = _E(pw // 2, ph_ // 2, r.winfo_rootx() + pw // 2,
                   r.winfo_rooty() + ph_ // 2)
        r._on_cross_press(press)
        assert r._cross_pull == "h", (
            f"a pull from the horizontal bar should make a horizontal guide, "
            f"got {r._cross_pull!r}")

        drop = _E(0, 0,
                  r.c_after.winfo_rootx() + aox + iw // 2,
                  r.c_after.winfo_rooty() + aoy + ih // 2)
        r._on_cross_drop(drop)
        _settle(app, 6)
        assert len(r._after_guides) == 1, "the pull did not leave a guide"
        assert r._after_guides[0][0] == "h"

        items = r.c_after.find_withtag("after_guide")
        assert items, "the guide drew nothing"
        kinds = {r.c_after.type(i) for i in items}
        assert kinds == {"line"}, f"a guide should be one plain line, found {kinds}"
        assert len(items) == 1, f"a guide should be a single line, found {len(items)}"
        assert r.c_after.itemcget(items[0], "fill").lower() == GUIDE_GREY.lower(), (
            "the guide is not the documented grey")
        assert int(float(r.c_after.itemcget(items[0], "width"))) == 1, (
            "the guide is not a hairline")

        # dropped back on the cross instead, it is put away, not left behind
        r._on_cross_press(press)
        r._on_cross_drop(press)
        _settle(app, 4)
        assert len(r._after_guides) == 1, (
            "a guide released over the cross should be discarded, not added")

        # and the grid toggle must not touch it -- they are different instruments
        r.v_grid.set(True)
        r._schedule_redraw()
        _settle(app, 8)
        assert r.c_after.find_withtag("after_guide"), "the grid toggle removed the guide"
        r.v_grid.set(False)
        r._schedule_redraw()
        _settle(app, 8)
        assert r.c_after.find_withtag("after_guide"), "turning the grid off removed the guide"
    finally:
        app.destroy()


def test_the_cross_is_four_equal_fields():
    """The 2026-09-12 directive, asserted on the real widgets.

    Four exactly equal fields -- before/after on top, loader and controls
    below -- divided by a flat dark cross and ringed by a dark border, both of
    them the constants' width.  The constants are the oracle rather than a
    typed-in pixel count; what is pinned here is that Tk's uniform grid lands
    on them, and that the plus shape is the plus shape (loader lower-left,
    controls lower-right), which no size assertion alone would catch.
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
            assert max(s[1] for s in sizes) - min(s[1] for s in sizes) <= 2, (
                f"{w}x{h}: the field heights differ: {sizes}")

            # The dark shows through at exactly the constants' width: the full
            # border on every outer edge, the cross between the fields.
            assert abs(r.cell_before.winfo_x() - L.CROSS_BORDER) <= 1, (
                f"{w}x{h}: the left border is {r.cell_before.winfo_x()}")
            assert abs(r.cell_before.winfo_y() - L.CROSS_BORDER) <= 1, (
                f"{w}x{h}: the top border is {r.cell_before.winfo_y()}")
            right = r.winfo_width() - (r.cell_after.winfo_x() + r.cell_after.winfo_width())
            assert abs(right - L.CROSS_BORDER) <= 1, f"{w}x{h}: the right border is {right}"
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
        # Switch to Light theme directly (bypasses the combobox trace).
        app._switch_theme("Light")
        app.update(); time.sleep(0.02)
        new_field = THEMES["Light"]["field"]
        assert new_field != old_field, "test needs distinct palettes"
        cb = review.c_before.cget("bg")
        ca = review.c_after.cget("bg")
        assert cb == new_field, (
            f"c_before bg is {cb!r} after switching to Light, expected {new_field!r}")
        assert ca == new_field, (
            f"c_after bg is {ca!r} after switching to Light, expected {new_field!r}")
        # Switch back and confirm the round-trip.
        app._switch_theme("Minimal Black")
        app.update(); time.sleep(0.02)
        assert review.c_before.cget("bg") == THEMES["Minimal Black"]["field"], (
            "c_before did not return to Minimal Black field colour")
        assert review.c_after.cget("bg") == THEMES["Minimal Black"]["field"], (
            "c_after did not return to Minimal Black field colour")
    finally:
        app.destroy()


def test_the_batch_bar_has_each_setting_exactly_once():
    """The 2026-09-13 duplication bug: subfolders and overwrite originals were each
    created twice in the batch options frame -- bound to the same vars, so it was
    pure visual clutter (four checkboxes for three settings), which no functional
    test could ever see.  Pinned by counting the Checkbuttons actually built."""
    def count(frame, text):
        n = [0]

        def walk(w):
            try:
                if w.winfo_class() == "TCheckbutton" and str(w.cget("text")) == text:
                    n[0] += 1
            except Exception:
                pass
            for c in w.winfo_children():
                walk(c)

        walk(frame)
        return n[0]

    app = _app()
    try:
        opt = app._w_opt
        assert count(opt, "subfolders") == 1, (
            f"subfolders built {count(opt, 'subfolders')}x, expected once")
        assert count(opt, "overwrite originals") == 1, (
            f"overwrite originals built {count(opt, 'overwrite originals')}x, expected once")
        assert count(opt, "offer manual review for unclear images") == 1, (
            "offer manual review must appear exactly once too")
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


def test_jpeg_quality_spinbox_in_batch_options():
    """The jpeg quality spinbox must be present in the batch options panel and
    wired to v_jpegq with a persistence trace."""
    app = _app()
    try:
        opt = app._w_opt
        assert hasattr(app, "v_jpegq"), "v_jpegq variable missing"
        val = app.v_jpegq.get()
        assert isinstance(val, int) and 10 <= val <= 100, (
            f"jpeg quality {val} out of range")
        # Walk the options frame for a Spinbox bound to v_jpegq.
        found = [False]

        def walk(w):
            try:
                if w.winfo_class() == "TSpinbox":
                    tv = w.cget("textvariable")
                    if isinstance(tv, str) and app.nametowidget(tv).get() == val \
                            if tv.startswith("!") else False:
                        found[0] = True
            except Exception:
                pass
            for c in w.winfo_children():
                walk(c)

        walk(opt)
        # The _spin helper uses ttk.Spinbox; the nametowidget check above is
        # fragile across Tk versions, so also verify by label text.
        if not found[0]:
            labels = []

            def walk2(w):
                try:
                    if w.winfo_class() in ("TLabel", "Label"):
                        t = str(w.cget("text"))
                        if "jpeg" in t.lower():
                            labels.append(t)
                except Exception:
                    pass
                for c in w.winfo_children():
                    walk2(c)

            walk2(opt)
            assert any("jpeg" in l.lower() for l in labels), (
                f"no 'jpeg quality' label found in batch options, got {labels}")
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
    """m / b / p / s reach the four tool modes, and SAM is not flipped twice.

    Tk's `bind()` *replaces* a handler for the same sequence, so four modes on
    one key would silently leave three dead -- this asserts four distinct
    sequences are actually registered, not just that the dispatcher works.

    All four handlers read their variable and none flips it, so the flip is the
    caller's -- the contract the palette buttons rely on too. `_on_sam_toggle`
    used to flip its own; a second flip anywhere cancels the first and the tool
    looks dead, which is what this asserts against.
    """
    app = _app()
    try:
        _loaded(app, 1280, 800)
        r = app.review
        for seq in ("<m>", "<b>", "<p>", "<s>"):
            assert app.bind(seq), f"{seq} is not bound"

        for seq, var, handler in (("<m>", "v_mark", "_on_mark_toggle"),
                                  ("<b>", "v_stroke", "_on_stroke_toggle"),
                                  ("<p>", "v_planar", "_on_planar_toggle"),
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
        first = (r.v_roi, r.v_roi_x0, r.v_roi_x1)
        r.load(r.session.path, r.settings, r.dest_path)   # a second photograph
        _settle(app)
        assert (r.v_roi, r.v_roi_x0, r.v_roi_x1) == first, (
            "the strip's variables were rebuilt by the second load -- the "
            "checkbox now sets one nobody reads")
        # And the widget the user presses must still drive the session.
        assert r._roi_chk.winfo_exists(), "the strip checkbox vanished"
        r._roi_chk.invoke()
        _settle(app)
        assert r.v_roi.get(), "pressing the real checkbox must turn the strip on"
        assert r.session.roi_x is not None, (
            "the strip is on, so the session must be restricted")
        r._roi_chk.invoke()
        _settle(app)
        assert r.session.roi_x is None, "turning it off must lift the restriction"
    finally:
        app.destroy()
