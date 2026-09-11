"""Window and pane arithmetic -- pure numbers in, pure numbers out.

`gui.py` is the Tk shell; every decision about *how big* something should be
lives here, for the same reason `review.py` has no Tkinter import: a layout
rule that can only be checked by looking at a screen is a layout rule nobody
checks. These are plain functions of the screen and the content, tested at
1366x768 through 3840x2160 in ``tests/test_layout.py``.

The rule the whole module turns on:

**The results tree's need is absolute; the preview's need is everything else.**

A list of results wants enough height to show a handful of rows -- and a
handful is a fixed number of pixels, because a row is a line of text. It does
not want more on a larger monitor; six visible rows is six visible rows. The
preview is the opposite: it is a photograph being judged by eye, so every pixel
it is given is a pixel of extra usable detail, without limit.

Splitting them by a *fraction* (the old 3:2 weight) therefore gets steadily
worse as the screen grows -- at 2560x1440 it handed 267 px to a tree showing
one row and left the image canvas 265 px, a fifth of the window, for the only
thing anyone is looking at. Giving the tree what it needs and the preview all
the rest is what "as big as useful" means: useful for the tree is bounded,
useful for the photograph is not.
"""
from __future__ import annotations

# A results row is a line of text plus padding; the Treeview style sets 24.
ROW_H = 24
# Header, borders and the pane's own padding, above and beyond the rows.
TREE_CHROME = 34
# Rows the tree shows before it starts scrolling instead of growing.
TREE_ROWS_MAX = 6
# ... and the fewest it may shrink to while still reading as a list.
TREE_ROWS_MIN = 2
# The preview stops being worth looking at below about this.
MIN_REVIEW_H = 380
# However cramped things get, the tree never takes more than this share.
TREE_MAX_SHARE = 0.32
# Below this the preview stops being worth looking at, and the height the
# control rows occupy is better spent as picture.
MIN_USEFUL_CANVAS = 260
# Measured height of the collapsible control block in the review panel.  It is
# two side-by-side columns (angles+detector | mask+fill), not four stacked rows,
# so opening it costs ~150 px instead of the old ~282 -- the cross layout that
# gives the picture its height back.
ADJUST_ROWS_H = 150
# hint + status + action row plus the panel's own paddings: the part of the
# review pane that is never collapsible, measured as (pane - canvas) with the
# control block shut.
PANEL_FIXED_H = 200


def tree_height(n_rows, row_h=ROW_H, rows_max=TREE_ROWS_MAX, rows_min=TREE_ROWS_MIN):
    """Pixels the results tree wants in order to show ``n_rows`` results.

    Bounded at both ends: below ``rows_min`` it stops looking like a list, and
    above ``rows_max`` it should scroll rather than keep eating the preview.
    """
    rows = max(rows_min, min(int(rows_max), int(n_rows)))
    return int(rows * row_h + TREE_CHROME)


def sash_position(available_h, n_rows, min_review=MIN_REVIEW_H):
    """Y of the review/results sash inside a paned window ``available_h`` tall.

    Returns the height given to the *review* pane, which is what
    ``PanedWindow.sashpos(0)`` takes. The tree gets the remainder.

    Three pressures, resolved in this order: the tree gets what its rows need;
    the preview keeps ``min_review`` if there is any way to give it; and if the
    window is genuinely too short for both, the split falls back to a
    proportion so neither pane vanishes entirely.
    """
    available_h = int(available_h)
    if available_h <= 0:
        return 0
    want = tree_height(n_rows)
    # Never let the tree take more than its share, however many results there are.
    want = min(want, int(available_h * TREE_MAX_SHARE))
    review = available_h - want
    # On a short screen the floor cannot be honoured outright; scale it down
    # rather than collapsing the tree to nothing.
    floor = min(min_review, int(available_h * 0.70))
    if review < floor:
        review = floor
    return max(0, min(review, available_h))


def initial_window(screen_w, screen_h, frac=0.85, min_w=1024, min_h=680):
    """``(w, h)`` for the window's *restore* size on a given screen.

    The window opens maximized, so this is the size it returns to when someone
    un-maximizes it -- which used to be a hardcoded 1920x1080 regardless of the
    screen. That is too small on a 1440p or 4K monitor and, on a 1366x768
    laptop, larger than the screen in both directions. A share of the actual
    screen is right in both cases.

    Clamped to the screen, so it can never open larger than the display, and to
    a floor, so the controls still fit on a small one.
    """
    w = int(min(screen_w, max(min(min_w, screen_w), screen_w * frac)))
    h = int(min(screen_h, max(min(min_h, screen_h), screen_h * frac)))
    return w, h


def adjustments_start_open(review_h, adj_h=ADJUST_ROWS_H,
                           fixed_h=PANEL_FIXED_H, min_canvas=MIN_USEFUL_CANVAS):
    """Whether the review panel's control rows fit without starving the picture.

    The action row is packed first and the canvases take what is left, so on a
    1080p screen -- the commonest there is -- leaving the controls open costs
    the preview all but about 90 px. That is a picture nobody can judge by eye,
    in the one mode that exists to be driven by hand. So the default is neither
    "open" nor "shut": it is whichever the height can afford.

    A user's own choice outranks this; it decides only what they find on
    opening. Not laid out yet (``review_h <= 1``) reads as open, because
    guessing shut on a number that does not exist yet would collapse the
    controls on every window.
    """
    review_h = int(review_h)
    if review_h <= 1:
        return True
    return review_h - int(fixed_h) - int(adj_h) >= int(min_canvas)
