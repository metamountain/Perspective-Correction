"""Window and pane arithmetic, at the screen sizes people actually have.

These are the assertions that replace looking at a screen. The one that matters
most is ``test_a_bigger_screen_goes_to_the_photograph``: it is the whole reason
the module exists, and it is the thing a future "let's just use weights again"
would break.
"""
from pc import layout as L

# width x height, and what they are
SCREENS = [
    (1366, 768),    # the small laptop the 1080p entry admits is cramped
    (1920, 1080),   # the standard desktop
    (2560, 1440),   # this machine
    (3840, 2160),   # 4K
    (3440, 1440),   # ultrawide
]


def test_the_tree_asks_for_rows_not_a_fraction():
    """Its need is absolute: more results up to a cap, then it scrolls."""
    assert L.tree_height(0) == L.tree_height(1) == L.tree_height(2)   # floor
    assert L.tree_height(3) > L.tree_height(2)
    assert L.tree_height(6) == L.tree_height(60) == L.tree_height(600)  # cap
    # and a row really is a row
    assert L.tree_height(4) - L.tree_height(3) == L.ROW_H


def test_a_bigger_screen_goes_to_the_photograph():
    """Every pixel a larger monitor adds must reach the preview, not the list.

    This is "as big as useful": useful for a six-row list is bounded, useful
    for a photograph being judged by eye is not. A proportional split fails
    this outright -- it is what left a 265 px canvas on a 1440p screen.

    The rule has two halves, because on a cramped screen the tree is squeezed
    below its natural size by ``TREE_MAX_SHARE`` and recovers as room appears.
    So: the tree never exceeds what its rows ask for, and once it has that,
    *all* further height goes to the preview -- not most of it, all of it.
    """
    want = L.tree_height(8)
    seen = []
    for _w, h in sorted(SCREENS, key=lambda s: s[1]):
        available = h - 425                     # measured chrome at the loaded stage
        review = L.sash_position(available, n_rows=8)
        tree = available - review
        assert review > 0 and tree > 0, f"a pane vanished at {h}"
        assert tree <= want, f"{h}: tree took {tree} px for {want} px of rows"
        seen.append((available, review, tree))

    # from the first screen that can satisfy the tree, it stays exactly there
    satisfied = [s for s in seen if s[2] == want]
    assert satisfied, "no real screen gives the results list its rows"
    for (a0, r0, _t), (a1, r1, _t1) in zip(satisfied, satisfied[1:]):
        assert (r1 - r0) == (a1 - a0), (
            f"{a1 - a0} px of extra screen produced only {r1 - r0} px of preview")


def test_the_preview_keeps_a_floor_on_every_real_screen():
    """Even the 1366x768 laptop gets a preview worth looking at."""
    for w, h in SCREENS:
        available = h - 425
        review = L.sash_position(available, n_rows=8)
        assert review >= min(L.MIN_REVIEW_H, int(available * 0.70)), (
            f"{w}x{h}: review pane {review} px is below the floor")


def test_neither_pane_ever_vanishes():
    """Including absurd inputs -- a sash is dragged, a window is dragged small."""
    for available in (0, 1, 50, 200, 400, 800, 1600, 4000):
        for n in (0, 1, 5, 50):
            review = L.sash_position(available, n)
            assert 0 <= review <= available
            if available >= 200:
                assert review > 0, f"review vanished at {available}"


def test_the_tree_never_takes_most_of_the_window():
    """A long results list must not push the photograph off the screen."""
    for available in (300, 600, 926, 1735):
        review = L.sash_position(available, n_rows=500)
        assert (available - review) <= available * L.TREE_MAX_SHARE + 1


def test_the_restore_size_follows_the_screen_instead_of_a_constant():
    """It was a hardcoded 1920x1080: too small at 1440p, too big on a laptop."""
    for w, h in SCREENS:
        gw, gh = L.initial_window(w, h)
        assert gw <= w and gh <= h, f"{w}x{h}: window larger than the screen"
        assert gw >= min(1024, w) and gh >= min(680, h)
    # the case that motivated it: a 1440p screen must get more than 1080p did
    assert L.initial_window(2560, 1440)[1] > 1080
    assert L.initial_window(3840, 2160)[0] > 1920
    # and the small laptop must not be handed a window it cannot show
    assert L.initial_window(1366, 768) <= (1366, 768)


def test_the_control_rows_open_only_where_the_picture_can_spare_the_height():
    """The three real review-pane heights, measured off-screen.

    1080p is the case this exists for: with the control rows open the preview
    is left about 90 px, which is not a picture anyone can judge. The decision
    has to be the height's, so it is arithmetic here rather than a constant in
    the window.
    """
    assert L.adjustments_start_open(895) is True, "1440p can afford both"
    assert L.adjustments_start_open(574) is False, "1080p cannot"
    assert L.adjustments_start_open(294) is False, "a 800px-tall window cannot"


def test_the_boundary_is_where_the_picture_stops_being_useful():
    """Exactly `min_canvas` of picture counts as affordable; a pixel less does not."""
    edge = L.PANEL_FIXED_H + L.ADJUST_ROWS_H + L.MIN_USEFUL_CANVAS
    assert L.adjustments_start_open(edge) is True
    assert L.adjustments_start_open(edge - 1) is False
    assert L.adjustments_start_open(edge + 1000) is True
    # monotone over real heights: more height can never turn an open verdict
    # back into a shut one.  Started at 2 px, because 0 and 1 mean "not laid
    # out yet" and are answered True on purpose -- see the test below.
    prev = False
    for h in range(2, 2000, 7):
        now = L.adjustments_start_open(h)
        assert not (prev and not now), f"verdict flipped back shut at {h}"
        prev = now


def test_a_panel_with_no_height_yet_is_not_judged():
    """`_build` runs before the panel is laid out; 0 or 1 px is not a measurement."""
    assert L.adjustments_start_open(0) is True
    assert L.adjustments_start_open(1) is True


# --------------------------------------------------------------------------
# The cross: the previews are the instrument, everything else is chrome
# --------------------------------------------------------------------------
# Common architectural framings, as (w, h). Portrait matters as much as
# landscape here -- a building shot from below is usually portrait.
FRAMINGS = [(3, 2), (2, 3), (4, 3), (3, 4), (16, 9)]


def test_a_four_three_box_beats_the_wide_short_one_for_real_framings():
    """The measurement behind the aspect choice, not an assertion of taste.

    The old side-by-side row gave each preview ~1263x361. That box is so wide
    and short that a 3:2 photograph paints under half of it: the picture is
    height-starved while the width goes to waste. Averaged over the framings
    this tool actually sees, a 4:3 box must do clearly better -- otherwise the
    whole reason for sizing the row by aspect collapses.
    """
    old_w, old_h = 1263, 361
    new_w = L.preview_box(2560)
    new_h = int(new_w / L.PREVIEW_ASPECT)

    old = [L.fill_fraction(old_w, old_h, w, h) for w, h in FRAMINGS]
    new = [L.fill_fraction(new_w, new_h, w, h) for w, h in FRAMINGS]
    assert sum(new) / len(new) > sum(old) / len(old) * 1.5, (
        f"4:3 box fills {sum(new)/len(new):.2f} vs {sum(old)/len(old):.2f} -- "
        f"not worth reshaping the window for")
    # and the worst case must improve too, not just the average
    assert min(new) > min(old)





def test_both_preview_boxes_fit_across_the_pane():
    for w in (800, 1366, 1920, 2560, 3440):
        assert 2 * L.preview_box(w) + L.PREVIEW_GAP <= w + 1


# --------------------------------------------------------------------------
# The perfect cross: four fields of exactly the same size
# --------------------------------------------------------------------------




def test_equal_fields_still_beat_the_old_wide_short_row():
    """The price of equality, stated. An equal field paints less of a
    photograph than a 4:3 box would, but far more than the row it replaces."""
    pane_w, pane_h = 2560, 1100
    qw, qh = L.quadrant(pane_w, pane_h)
    equal = L.fill_fraction(qw, qh, 3, 2)
    old = L.fill_fraction(1263, 361, 3, 2)
    ideal = L.fill_fraction(qw, int(qw / L.PREVIEW_ASPECT), 3, 2)
    assert equal > old, f"equal field {equal:.2f} no better than the old row {old:.2f}"
    assert equal <= ideal + 1e-9        # the documented price


def test_the_loader_is_the_small_half_and_the_controls_the_big_one():
    """Equal bottom fields were tried and failed on contact with the content.

    The loader is a drop target and a short list; the controls are four slider
    rows plus three selectors. An even split spends half the row on the
    emptiest thing in the window.
    """
    for w in (1366, 1920, 2560, 3440):
        loader, controls = L.bottom_split(w)
        assert controls > loader, f"{w}: loader {loader} >= controls {controls}"
        assert loader + controls <= w, f"{w}: bottom row overflows"
        assert loader >= min(L.MIN_LOADER_W, w // 2), f"{w}: loader too small to name a file"


def test_the_previews_stay_exactly_equal_even_though_the_bottom_row_does_not():
    """The pair is matched; any difference between before and after reads as a
    bug. The bottom row is two different jobs and may differ."""
    for w, h in [(1366, 700), (1920, 900), (2560, 1200)]:
        qw, _qh = L.quadrant(w, h)
        assert 2 * qw + L.PREVIEW_GAP <= w + 1
        loader, controls = L.bottom_split(w)
        assert loader != controls or w < 2 * L.MIN_LOADER_W   # normally unequal


def test_a_narrow_window_protects_the_sliders_not_the_loader():
    """When something has to give it is the loader: the mode exists to move
    sliders, and a drop target that is merely small is still usable."""
    loader, controls = L.bottom_split(420)
    assert controls >= loader


def test_the_perfect_cross_is_flat_twenty_on_every_real_screen():
    """The 2026-09-12 directive: the cross and the border are a flat 20 px of
    dark, on every screen -- a constant, not a fraction that drifts with the
    window.  The four fields it cuts out are exactly equal by construction."""
    assert L.CROSS_GAP == 20, "the cross width is a directive, not a tuning knob"
    assert L.CROSS_BORDER == 20, "the border width is a directive, not a tuning knob"
    for w, h in SCREENS:
        fw, fh = L.perfect_cross_field(w, h)
        assert 2 * fw + L.CROSS_GAP + 2 * L.CROSS_BORDER <= w + 1, (
            f"{w}x{h}: the fields plus cross and border do not fit")
        assert 2 * fh + L.CROSS_GAP + 2 * L.CROSS_BORDER <= h + 1, (
            f"{w}x{h}: the fields plus cross and border do not fit")
        # equality: halving an integer once can only lose a pixel to the gap,
        # never to one field or the other
        assert fw >= w // 4 - L.CROSS_BORDER - L.CROSS_GAP


def test_ruler_ticks_fall_on_whole_steps_and_major_every_fifth():
    """Ticks land on exact multiples of the step from the origin, and a major
    (labelled) tick comes every fifth -- so a coordinate can be read off the
    ruler without counting minor marks."""
    ticks = L.ruler_ticks(100, 10)
    positions = [p for p, _ in ticks]
    assert positions == list(range(0, 101, 10))          # whole steps, inclusive of the end
    majors = [p for p, major in ticks if major]
    assert majors == [0, 50, 100], "a major tick every fifth, from the origin"
    # a span that does not divide evenly still stops at the last whole step
    assert L.ruler_ticks(105, 10)[-1][0] == 100


def test_ruler_ticks_refuse_a_span_with_no_room():
    """No room, no ruler: a non-positive span or step yields nothing rather
    than a single cramped tick at the origin."""
    assert L.ruler_ticks(0, 10) == []
    assert L.ruler_ticks(50, 0) == []
    assert L.ruler_ticks(-20, 10) == []


def test_the_mark_line_thins_out_on_small_photographs():
    """A hand-drawn control line is a screen-pixel width that reads as a thick
    bar on a small image and hairline on a large one; it scales with the shown
    short edge, clamped to a hairline (1) up to the old constant (3)."""
    assert L.mark_line_width(0) == 1
    assert L.mark_line_width(-5) == 1
    # monotonic and bounded: small stays thin, large caps at the old 3 px
    widths = [L.mark_line_width(s) for s in (120, 249, 250, 499, 500, 749, 750, 2000)]
    assert widths == [1, 1, 1, 1, 2, 2, 3, 3]
    for s in (100, 250, 500, 750, 1000, 4096):
        assert 1 <= L.mark_line_width(s) <= 3


def test_the_drawn_glyph_pen_is_one_number_and_follows_the_glyph_size():
    """One pen for every drawn tool mark, scaling with the key.

    The palette drifted into four visual languages because three keys drew
    themselves with pixel arithmetic inline in `gui.py` -- `gs // 10` here,
    `gs // 4` there -- which is the thing the hard rules forbid, and the
    reason they give is exactly what happened: a size tuned in the window is
    a size no test can see. This is that test.
    """
    base = L.tool_glyph()
    assert L.glyph_stroke() == L.glyph_stroke(base), (
        "the default must be the tool glyph size, or the palette and the "
        "pen can disagree without anything saying so")
    # Never hairline: measured 2026-09-20, a 1 px mark disappears beside the
    # antialiased icon-font keys in the same column.
    assert L.glyph_stroke(base) >= 2
    # Scales with the glyph, so a larger key does not get a thinner-looking
    # mark. Not a fixed constant: that is what "one pen" has to survive.
    assert L.glyph_stroke(base * 2) > L.glyph_stroke(base)
    for size in (16, 24, 32, 48, 64):
        assert L.glyph_stroke(size) >= 2, size
        assert L.glyph_stroke(size) <= size // 4, (
            f"a {L.glyph_stroke(size)} px pen in a {size} px glyph is a blob")
