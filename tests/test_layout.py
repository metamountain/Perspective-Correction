"""Window and pane arithmetic, at the screen sizes people actually have.

These are the assertions that replace looking at a screen. The one that matters
most is ``test_a_bigger_screen_goes_to_the_photograph``: it is the whole reason
the module exists, and it is the thing a future "let's just use weights again"
would break.
"""
from bpc import layout as L

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
