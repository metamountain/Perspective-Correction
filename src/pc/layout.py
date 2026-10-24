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
# Physical size of a correction/mask Scale. The track already fills its row
# (sticky="ew"), so grabbability comes from thickness and thumb length, not from
# the track's length. A default ttk.Scale is a few pixels thick -- easy to miss
# at any zoom; these make it a chunk you can hit by eye.
SLIDER_WIDTH = 28      # px, trough thickness (perpendicular to the track)
SLIDER_THUMB = 30      # px, thumb length along the track (~circular with width)
# Grid floor for the track column. The scale fills its column (sticky="ew"), but
# on a narrow window the label + spinbox can squeeze it to a useless sliver; a
# grid `minsize` is a hard floor the weighted column cannot shrink past (unlike
# the Scale's own `length`, which yields under pressure). Wide windows still
# stretch it far beyond this via sticky="ew". Also the Scale's requested
# `length`, so the row asks for no more than the floor: 60 lets the control
# strip fit a 1366 px wide window (2026-10-21).
SLIDER_MIN = 60        # px, minimum track-column width

# --- The per-photograph control strip (2026-10-20, user-directed) ------------
# The cross's lower-right field is a dense two-column strip: General (mask,
# detector, fill, crop, save, overlays) beside Correction (a 3-mode switch).
# Every number below is the one place these sizes live; gui.py must not type
# any of them.  The whole point is that the strip stays short so the two
# pictures on top take every spare pixel.
CTRL_FONT_PT = 8          # pt, the strip's face (labels, readouts, buttons)
CTRL_LINE_HEIGHT = 1.5    # row pitch ceiling, as a multiple of the font line height
CTRL_ROW_GAP_PX = 0       # px between rows: none (user: "keine Luft zwischen Zeilen")
CTRL_GROUP_PAD_PX = 4     # px between groups (around a group title)
CTRL_COL_GAP_PX = 8       # px between the General and Correction columns
SLIDER_TRACK_H_PX = 4     # px, slim correction-slider trough thickness
CTRL_FIELD_H_PX = 20      # px, a single control row's height (field/button)
MODE_SWITCH_H_PX = 24     # px, the segmented Auto / Horizontal auto / Manual bar
CTRL_STRIP_MAX_H_PX = 140 # px, the strip's height budget at 1080p; the pictures
                          #   get everything above it


def tree_height(n_rows, row_h=ROW_H, rows_max=TREE_ROWS_MAX, rows_min=TREE_ROWS_MIN):
    """Pixels the results tree wants in order to show ``n_rows`` results.

    Bounded at both ends: below ``rows_min`` it stops looking like a list, and
    above ``rows_max`` it should scroll rather than keep eating the preview.
    """
    rows = max(rows_min, min(int(rows_max), int(n_rows)))
    return int(rows * row_h + TREE_CHROME)


# Superseded (2026-09-12, "the window is the cross"): the paned split and its
# sash are gone -- the results list now lives in the cross's lower-left field
# and sizes itself with the packer.  Kept, like LOADER_SHARE, only for its
# tests; nothing in src/ calls these any more.


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


# The window's floor. It was a fixed 1920x1080, larger than many screens (a
# 1366x768 laptop, 125 % scaling, a CI runner), so the window hung off the
# display. Measured 2026-10-21: with the sliders at SLIDER_MIN no control row
# clips at 1366x768.
MIN_WINDOW = (1366, 768)


def min_window(screen_w, screen_h, floor=MIN_WINDOW):
    """``(w, h)`` the window may not shrink below: the floor, never past the screen."""
    return min(floor[0], screen_w), min(floor[1], screen_h)


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


# --------------------------------------------------------------------------
# The cross: two previews on top, loader and controls compact beneath
# --------------------------------------------------------------------------
# The previews are the instrument; everything else is chrome around them. What
# makes a preview *big* is not height alone but the box's **aspect**, and that
# is the thing the side-by-side layout got wrong. Two boxes across a 2560 px
# window are ~1263 px wide each; given only 361 px of height, a 3:2 photograph
# paints about 540 px wide and leaves more than half the box empty. The picture
# is height-starved while the width goes to waste.
#
# So the row is sized to make each box roughly 4:3. That is a compromise
# chosen for this subject: architectural work arrives as 3:2 landscape and 2:3
# portrait in similar numbers, and 4:3 is the box that wastes the least across
# both. Wider than 4:3 starves the portraits; narrower starves the landscapes.
PREVIEW_ASPECT = 4.0 / 3.0
# Gap between the two preview boxes, and the frame's own padding.
PREVIEW_GAP = 12
# Below this a preview is not worth showing whatever the arithmetic says.
MIN_PREVIEW_H = 150


def preview_box(pane_w, gap=PREVIEW_GAP):
    """Width available to *each* of the two side-by-side preview boxes."""
    return max(1, (int(pane_w) - int(gap)) // 2)



def fill_fraction(box_w, box_h, img_w, img_h):
    """Share of a preview box a photograph actually paints, 0..1.

    The number the aspect choice is arguing about: fitting a 3:2 frame into a
    1263x361 box covers 0.43 of it, and the same frame into a 4:3 box covers
    0.75. It is the honest measure of "as big as possible", because pixels of
    box nobody paints are not preview.
    """
    box_w, box_h = max(1, int(box_w)), max(1, int(box_h))
    s = min(box_w / float(img_w), box_h / float(img_h))
    return (img_w * s) * (img_h * s) / float(box_w * box_h)


# --------------------------------------------------------------------------
# The perfect cross: four equal fields
# --------------------------------------------------------------------------
# A later directive overrides the 4:3 cap above: **four fields of exactly the
# same size**, previews on top, loader and controls in the two below. The cap
# and the equal cross cannot both hold -- capping the preview row at 4:3 makes
# the top row taller or shorter than the bottom, which is precisely the thing a
# cross is not. Equal wins, and the reason is not geometry:
#
#   a layout you can verify at a glance costs nothing to learn.
#
# This is a simple program. A user who sees four equal fields knows immediately
# where everything is and that nothing is hidden; a user who sees a 4:3-capped
# top row and a residual bottom strip has to work out which parts are fixed,
# which grew, and whether anything is missing. `fill_fraction` says the equal
# field paints a little less of a photograph than a 4:3 one would -- that is the
# price, it is paid once, and it buys a window that explains itself.
#
# The previews are still the point: they get half the height, which is far more
# than the 361 px the old wide-and-short row gave them.
#
# 2026-10-19 (user-directed): the window broke the cross *vertically* -- the
# pictures on top now take the larger share (CROSS_TOP_WEIGHT : CROSS_BOT_WEIGHT
# below), not equal halves. The two functions in this section (`quadrant`,
# `perfect_cross_field`) still compute the equal-field arithmetic, because that
# is what the flat-20-px cross/border directive is stated in; the live grid in
# gui.py no longer asks them for the row split.

# The lower field's need is not one number, because the control block collapses
# (`adjustments_start_open`, wired to the review pane's <Configure>). Two
# figures, and the distinction is what keeps a 1366x768 laptop working:
#
#   hard floor  -- the action row. Save / Keep / Close must be reachable at
#                  every size; this is the one thing that may never be traded.
#   preferred   -- action row plus the control block, i.e. the controls open.
#
# A window that cannot afford the preferred height does not get an uneven cross;
# it gets an equal cross with the controls collapsed, which is the same layout
# with one block folded away. Only a window too short even for the hard floor
# plus a minimum preview falls back to an uneven split.
ACTION_ROW_H = 48
UI_HARD_MIN_H = ACTION_ROW_H
MIN_UI_QUADRANT_H = ADJUST_ROWS_H + ACTION_ROW_H


def quadrant(pane_w, pane_h, gap=PREVIEW_GAP):
    """``(w, h)`` of each of the four fields -- exactly half the pane, both ways.

    Integer division, so the two columns and two rows are the same size as each
    other rather than one carrying a spare pixel: a cross that is one pixel off
    reads as a mistake, and the leftover belongs in the gap.
    """
    return (max(1, (int(pane_w) - int(gap)) // 2),
            max(1, (int(pane_h) - int(gap)) // 2))


# One width for the whole frame (user, 2026-10-21: "same width everywhere",
# a border on top too, "no double width for cross"): the cross between the
# fields, the top, the sides and the bottom are all CROSS_PX. 8 px is the side
# edge the user chose on 2026-10-20 as "just enough to pull a ruler from".
# Supersedes the flat 20 px cross of 2026-09-12 and the top-0 / bottom-10 edges.
CROSS_PX = 8
CROSS_GAP = CROSS_PX       # between fields: each side of it carries half
CROSS_BORDER = CROSS_PX    # top and bottom edge
CROSS_SIDE = CROSS_PX      # left and right edge
# Guides are pulled from a band hugging the corrected picture's edge, this far
# outside AND this far inside it -- 16 px all round (user, 2026-10-21: finding
# the old pull zone was "a fuck up").
GUIDE_BAND_PX = 8
GUIDE_CLICK_PX = 3     # a press that travels less than this is a click, not a pull
# The cross is broken vertically (2026-10-19, user-directed): the two preview
# fields on top take every extra pixel, because a photograph judged by eye wants
# them all while the controls below want only what they need. The lower row is
# NOT in a uniform group and carries weight 0, so it sizes to its own content
# (the per-photograph controls plus the run bar; the batch defaults were killed
# and the adjustments panel opens collapsed) and the upper row, weight 1, takes
# the rest. A fixed ratio would clip the run bar whenever the controls' request
# outgrew its share, so the split now follows the content instead of a constant.
CROSS_TOP_WEIGHT = 1
CROSS_BOT_WEIGHT = 0
# Inset of the dimension label from the top-left corner of its picture.  The
# pictures now sit flush to the top edge of their field, so the label lives
# inside the picture (top-left) rather than in the black below it.
LABEL_INSET_PX = 6
NOTICE_MS = 4000   # ms a state-change prompt stays on screen
TOOLTIP_DELAY_MS = 1000   # hover this long before help appears (user)
TOOLTIP_GAP = 4           # px between a widget and its help
# The pyramid-and-eye mark in the window's foot, beside the wordmark: as tall
# as the foot's buttons allow (user, 2026-10-24: "setz es unten gross").
FOOT_LOGO_PX = 26
TOOLTIP_INSET = 12        # px the help starts right of the widget's left edge


def tooltip_xy(wx, wy, ww, wh, tw, th, sw, sh):
    """Where a ``tw`` x ``th`` help box goes for a widget at ``wx, wy`` (``ww`` x
    ``wh``) on an ``sw`` x ``sh`` screen: below it, unless that runs off the
    bottom, then above; and pulled left so it never runs off the right.

    Always-below hid the help of every footer button (batch, deps, update)
    under the screen edge (user, 2026-10-23)."""
    x = wx + TOOLTIP_INSET
    y = wy + wh + TOOLTIP_GAP
    if y + th > sh:
        y = wy - th - TOOLTIP_GAP
    x = max(0, min(x, sw - tw))
    y = max(0, min(y, sh - th))
    return x, y


def perfect_cross_field(pane_w, pane_h, gap=CROSS_GAP, border=CROSS_BORDER):
    """``(w, h)`` of each of the four equal fields inside a pane.

    The pane carries a ``border`` margin on all four sides and one ``gap``
    between the two columns and one between the two rows; what remains is
    split in half both ways, so all four fields are exactly the same size.
    """
    usable_w = max(1, int(pane_w) - 2 * int(border) - int(gap))
    usable_h = max(1, int(pane_h) - 2 * int(border) - int(gap))
    return (usable_w // 2, usable_h // 2)


# --------------------------------------------------------------------------
# The loupe: a magnifier for placing planar corners
# --------------------------------------------------------------------------
# A corner clicked by eye on a preview scaled to 0.08 lands within ~12
# full-resolution pixels of the intended point, and a homography does not
# forgive that.  The loupe shows full-resolution pixels around the cursor with
# a crosshair on the exact pixel under it.  The crop is odd so the cursor's
# pixel sits exactly at the window's centre, and the magnification is integral
# so one image pixel is a whole number of screen pixels: aiming within half a
# pixel is then just looking at where the crosshair falls.

LOUPE_CROP = 81      # full-resolution image px across the window (odd: centred)
LOUPE_MAG = 2        # screen px per image px
LOUPE_OFFSET = 24    # cursor-to-window distance, so the glass never covers the point

# While a mark end or a rectangle corner is dragged the after picture is
# re-rendered at most this often, without fill; the full preview comes on
# release (user, 2026-10-23: "aktualisierung target reduziert, sonst lag").
# Measured on Platte_1 with horizontal auto: a refit is 30 ms, the after
# render 100 ms unfilled against 900 ms with live lama. Once a second, not
# four times: every re-render stalls the loupe for ~200 ms, and the glass is
# what the hand is aiming with ("lupe muss smooth laufen! max 1/s").
DRAG_PREVIEW_MS = 1000
# A slow fill (lama) follows the stand-in once nothing has changed for this
# long, off the Tk thread ("erst telea, dann relaxed lama hinterher").
DEFERRED_FILL_MS = 400


def loupe(field_short=0):
    """``(window_px, crop_px, offset_px)`` for the magnifier.

    The size follows the window rather than a constant: parked in the middle of
    the cross it has room, and a fixed 162 px was small on a large screen and
    intrusive on a small one. Half the short edge of one quadrant, clamped, so
    it never eats a field it is supposed to help you read.

    ``field_short`` is the short edge of one quadrant; 0 keeps the old size for
    callers that have not measured one yet.
    """
    if field_short <= 0:
        return LOUPE_CROP * LOUPE_MAG, LOUPE_CROP, LOUPE_OFFSET
    size = int(max(180, min(360, field_short * 0.55)))
    size -= size % 2                      # even, so the crosshair sits centred
    return size, max(41, size // LOUPE_MAG) | 1, LOUPE_OFFSET


# --------------------------------------------------------------------------
# Rulers: tick marks along the corrected frame's edges
# --------------------------------------------------------------------------
# The grid says where the lines are; the rulers say what number they sit at.
# Ticks fall on whole multiples of the (user-chosen) step from the image's
# top-left corner, and every fifth is a major tick that carries a label.  The
# selection is pure geometry -- no window -- so it is tested like the rest of
# the layout rather than rediscovered by eye.

RULER_MAJOR_EVERY = 5


def ruler_ticks(span, step):
    """``[(position, is_major), ...]`` along an axis ``span`` px long.

    A tick at every multiple of ``step`` from the origin up to (not beyond)
    ``span``, with a major tick -- the ones that get a numeric label -- every
    fifth.  Empty for a non-positive span or step: a ruler with no room draws
    nothing rather than one cramped tick."""
    if span <= 0 or step <= 0:
        return []
    n = int(span // step)
    return [(i * step, i % RULER_MAJOR_EVERY == 0) for i in range(n + 1)]


MARK_LINE_MIN, MARK_LINE_MAX = 1, 3


# The tool column's unit -- and ONLY the tool column's.
#
# Worth stating, because reading `GRID = 4` here and assuming it is the
# window's unit would be wrong. Measured 2026-09-20 over every `padx`/`pady`
# in gui.py: 132 values, and 41 % of them are not multiples of 4. They are not
# arbitrary either -- every one of the "off-grid" values is exactly 2 away
# (2, 6, 10, 14), so the window at large is laid out on a consistent **2 px**
# rhythm and this 4 px unit is a local doubling for the palette keys.
#
# That measurement is also why the spacing was left alone rather than
# regularised: a sweep that forced 54 values onto a 4 px grid would move the
# whole window to fix nothing. The rhythm was already there.
GRID = 4


# The facade strip's rulers carry their grab handle at the picture's
# mid-height, which is exactly where the centred tool palette sits: a ruler near
# the left edge put its handle under the palette, out of reach (user,
# 2026-10-23). The palette was split there first, and the gap read as a missing
# tool (2026-10-24), so the handle dodges instead: below the palette, or above
# it when below would leave the picture. The 14 px handle's grab zone is 14 px
# each way; this much is kept clear of a key.
STRIP_HANDLE_CLEAR = 6 * GRID


def strip_handle_y(px, mid, top, bottom, bar):
    """y of a strip ruler's handle at x = *px*.

    *mid* is the picture's mid-height, *top*/*bottom* its edges, *bar* the
    palette's ``(x0, y0, x1, y1)`` in the same pixels (or None)."""
    if bar is None:
        return mid
    x0, y0, x1, y1 = bar
    c = STRIP_HANDLE_CLEAR
    if not (x0 - c <= px <= x1 + c) or not (y0 - c <= mid <= y1 + c):
        return mid
    if y1 + c <= bottom - c:
        return y1 + c
    return max(top + c, y0 - c)


def tool_key(unit=GRID):
    """``(side, gap)`` in px for one palette tool key and the space below it.

    Square by construction: a key wider than it is tall reads as a label rather
    than something you press, and every key is the same size because they are
    the same kind of thing -- nothing in the palette earns being bigger.

    Key plus gap is 9 units, so the column advances on one pitch from the top
    icon to the last tool. Both numbers are whole multiples of one grid unit:
    the column then lines up with anything else built on ``GRID`` instead of each
    gap being chosen by eye.  It lives here and not in ``gui.py`` for the reason
    the hard rules give -- a size tuned in the window is a size no test can see.
    """
    return 8 * unit, unit           # 32 px key, 4 px below it -> 36 px pitch


def tool_glyph(unit=GRID):
    """Optical size in px of what sits inside a tool key.

    One number for every key, because "the same size" is what makes six
    different marks read as one set -- a 22 px icon beside a 27 px glyph reads
    as two languages however well each is drawn. Five units inside an eight
    unit key leaves a ring of empty on every side, which is what centres them
    to the eye rather than only to the pixel.
    """
    return 6 * unit        # 24 px in a 32 px key: 5 read too thin beside
                           # the window's own line weights


def glyph_stroke(glyph_px=None):
    """Pen width in px for a hand-drawn tool glyph of ``glyph_px``.

    Chosen by eye, after the measurement disagreed with it -- which is the
    part worth writing down.

    A run-length median over the rendered alpha says the three icon-font keys
    at the top of this same column (add, folder, paste) stroke at **1.0 px**
    inside a 24 px box, and the first version of this function believed that
    number: it was written claiming 2 px on the strength of measuring the
    palette's two *pictorial* font glyphs, which are heavier than the rest of
    the set and were never representative.

    Drawn at 1 px against those icons, the marks vanish -- the diagonal, the
    quad and the struck lines all read as a fainter pen (rendered both ways
    and compared side by side at 3x, 2026-09-20). Drawn at 2 px they match.
    The metric is not wrong so much as blind: font glyphs are antialiased, so
    a stroke whose opaque core is one pixel carries soft shoulders either side
    and *looks* wider, while a hard-edged drawn stroke measures exactly what
    it is. Optical weight is what the eye matches, and no run-length median
    reports it.

    So: one number for every drawn glyph, for the same reason ``tool_glyph``
    is one number for every key -- a 2 px mark next to a 3 px mark reads as
    two hands, however well each is drawn -- and that number is settled by
    looking, with the measurement recorded here as the thing that misled.

    It lives here rather than in ``gui.py`` because the three hand-drawn keys
    that drifted out of the set computed ``gs // 10`` and ``gs // 4`` inline --
    pixel arithmetic in the window, which the hard rules forbid precisely
    because no test can see it. This one is tested.
    """
    glyph_px = tool_glyph() if glyph_px is None else glyph_px
    return max(2, int(round(glyph_px / 12.0)))


def glyph_box(glyph_px=None):
    """The square a glyph is drawn into before it is cropped back to its ink.

    Drawing happens at this size and the result is then cropped to the pixels
    actually drawn and re-centred on them, which is what the icon font's path
    already does. That step is the whole reason the sets did not match: a font
    glyph fills its box because it was cropped to its ink, while a hand-drawn
    one kept whatever margin its author left -- measured 2026-09-20, the two
    drawn keys spanned 0.69 and 0.77 of the box against the font's 1.00, so
    they simply looked smaller in an identical key.
    """
    return tool_glyph() if glyph_px is None else glyph_px


def mark_line_width(short_edge):
    """Screen-pixel width of a hand-drawn control line for an image shown with
    ``short_edge`` px on its short side.

    A fixed 3 px reads as heavy on a small photograph and hairline on a large
    one, so it scales with the displayed size: 1 px up to ~250 px, 2 px past
    ~500, capping at the old constant of 3.  Pure, so it is tested like the rest
    of the layout instead of tuned by eye on one window."""
    if short_edge <= 0:
        return MARK_LINE_MIN
    return max(MARK_LINE_MIN, min(MARK_LINE_MAX, int(short_edge) // 250))




# equal (CROSS_GAP / CROSS_BORDER above), with the loader's *content* kept
# compact and top-aligned inside its field instead of the field itself being
# shrunk.  `bottom_split` is no longer called from the panel; it stays because
# test_layout.py still pins its arithmetic.
LOADER_SHARE = 0.28
# Below this the loader stops being able to show a filename.
MIN_LOADER_W = 180


def bottom_split(pane_w, gap=PREVIEW_GAP, share=LOADER_SHARE, min_loader=MIN_LOADER_W):
    """``(loader_w, controls_w)`` for the lower row.

    The loader takes a fixed *share*, floored so it can still show a name, and
    capped so it can never take more than the controls: on a narrow window the
    thing that must survive is the sliders, because that is what the mode is
    for.
    """
    pane_w = int(pane_w)
    usable = max(1, pane_w - int(gap))
    loader = int(usable * float(share))
    loader = max(min(int(min_loader), usable // 2), loader)
    loader = min(loader, usable // 2)
    return loader, max(1, usable - loader)
