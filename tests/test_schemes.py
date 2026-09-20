"""The Manhattan-world line scheme: a classifier, not a corrector.

``ArchitectureScheme`` establishes the frame (the vertical vanishing point plus
up to two horizontal directions) and then partitions every detected line into
*relevant* (belongs to an established direction) or *ignored* (a brace, rafter,
handrail -- real but not structural).  It never deletes a line: relevant and
ignored together are always the whole set.  The Sonderregel -- in
horizontal-only mode the secondary plane's lines drop out -- is the one
behaviour that changes what gets corrected, so it gets its own test.

The scenes are synthetic because the question here is geometric (does the
partition land where the ground-truth planes are), and a rendered corner view
with an exact known pose is the right instrument for that.
"""
import cv2

import synth
from pc import config, scheme


def _scheme(scene):
    s = config.Settings()
    gray = cv2.cvtColor(scene.img, cv2.COLOR_BGR2GRAY)
    return scheme.ArchitectureScheme.from_image(gray, s, scene.img).detect()


# -- the frame ---------------------------------------------------------------

def test_a_corner_view_establishes_all_three_directions():
    sc = _scheme(synth.Scene(w=1200, h=800, pitch_deg=6, roll_deg=-2,
                             yaw_deg=30, seed=9, corner=True))
    assert sc.established is True
    assert sc.vp_vert is not None
    assert sc.vp_h1 is not None
    # a corner view shows two distinct facade directions
    assert sc.vp_h2 is not None


def test_the_frame_is_not_established_without_verticals():
    # a clean facade with no vertical vanishing point: the frame is incomplete,
    # so the scheme must declare itself degraded rather than guess
    scene = synth.Scene(w=1200, h=800, seed=5, clutter=0, noise=0, texture=False)
    sc = _scheme(scene)
    assert sc.established is False
    assert sc.degraded is True


# -- the partition -----------------------------------------------------------

def test_the_partition_never_deletes_a_line():
    for scene in (synth.Scene(w=1200, h=800, pitch_deg=6, roll_deg=-2,
                              yaw_deg=30, seed=9, corner=True),
                  synth.Scene(w=1200, h=800, pitch_deg=6, roll_deg=-2,
                              seed=11, corner=True, half_timbered=True)):
        sc = _scheme(scene)
        relevant, ignored = sc.filter_by_vanishing_points()
        assert len(relevant) + len(ignored) == len(sc.ls)
        assert len(sc.deviation_deg) == len(sc.ls)
        assert set(sc.plane.tolist()) <= {-1, 0, 1, 2}


def test_braces_are_set_aside_on_half_timbered_work():
    # the adversarial case: mirrored diagonal braces form a coherent false
    # vanishing point, so they must be recognised as non-structural and ignored
    scene = synth.Scene(w=1200, h=800, pitch_deg=6, roll_deg=-2,
                        seed=11, corner=True, half_timbered=True)
    sc = _scheme(scene)
    _, ignored = sc.filter_by_vanishing_points()
    assert len(ignored) > 0.1 * len(sc.ls)


def test_the_second_plane_drops_out_in_horizontal_only_mode():
    # Sonderregel: with horizontal correction on, the secondary facade's lines
    # (plane 2) are dropped from the relevant set; in normal mode they stay.
    scene = synth.Scene(w=1200, h=800, pitch_deg=6, roll_deg=-2,
                        yaw_deg=30, seed=9, corner=True)
    normal = _scheme(scene).filter_by_vanishing_points()[0]
    horiz_only = _scheme(scene).filter_by_vanishing_points(horizontal_correction_only=True)[0]
    assert len(horiz_only) < len(normal)


def test_a_degraded_scheme_filters_nothing():
    # when the frame is not established, every line stays relevant: no filter,
    # no risk of dropping structural lines on a guess
    scene = synth.Scene(w=1200, h=800, seed=5, clutter=0, noise=0, texture=False)
    sc = _scheme(scene)
    relevant, ignored = sc.filter_by_vanishing_points()
    assert len(ignored) == 0
    assert len(relevant) == len(sc.ls)


# -- the preview -------------------------------------------------------------

def test_draw_preview_returns_a_same_shaped_copy():
    scene = synth.Scene(w=1200, h=800, pitch_deg=6, roll_deg=-2, seed=9, corner=True)
    sc = _scheme(scene)
    sc.filter_by_vanishing_points()
    prev = sc.draw_preview(scene.img)
    assert prev.shape == scene.img.shape
    # it is a fresh buffer: editing the preview must not touch the source
    prev[0, 0] = (0, 0, 0)
    assert tuple(int(c) for c in scene.img[0, 0]) != (0, 0, 0)


def test_the_two_renderings_of_found_geometry_agree_on_what_green_means():
    """`scheme.draw_preview` and `preview.render` draw the same ideas.

    They did not agree. `scheme.draw_preview` wrote (0, 200, 0) for the lines
    that count and (96, 96, 96) for the ones that do not, inline, while
    `preview.py` drew the same two ideas as GREEN (80, 220, 90) and GREY
    (130, 130, 130) from a named table. Two views of one thing that did not
    look like one thing -- and the open research goal about a found-geometry
    overlay is a choice between exactly these two renderings, which is an
    awkward choice to make when they disagree about the colours.

    Asserted by rendering, not by reading the source: a colour constant that
    is imported but never actually used would pass a grep and fail here.

    The two lines need different arguments, and the reason is `cv2.LINE_AA`.
    The relevant line is drawn 2 px wide, so it has solid core pixels and the
    colour appears exactly. The ignored line is 1 px, so antialiasing blends
    it toward the background and it peaks at 118 rather than 130 -- an exact
    match is simply the wrong question there. What makes the weaker assertion
    sound is that **antialiasing can only darken**: a peak of 118 cannot have
    come from a source of (96, 96, 96), so measuring above the old value is
    proof it is gone, without needing to know the coverage.
    """
    import numpy as np
    from pc import preview as PV
    from pc.lines import LineSet

    sch = scheme.ArchitectureScheme.__new__(scheme.ArchitectureScheme)
    sch.relevant = LineSet(np.array([[10.0, 10.0, 90.0, 10.0]]))
    sch.ignored = LineSet(np.array([[10.0, 40.0, 90.0, 40.0]]))
    sch.vp_vert = np.array([50.0, 5.0, 1.0])
    sch.vp_h1 = sch.vp_h2 = None

    out = sch.draw_preview(np.zeros((60, 100, 3), np.uint8))
    seen = {tuple(int(c) for c in px) for px in out.reshape(-1, 3)}

    # 2 px: exact.
    assert tuple(PV.GREEN) in seen, (
        f"the lines that count are not drawn in preview.GREEN {PV.GREEN}; "
        "scheme has its own green again")
    assert (0, 200, 0) not in seen, "the old inline green (0, 200, 0) is back"

    # 1 px and antialiased: bounded above by the true colour, and strictly
    # brighter than the value it replaced.
    peak = out[38:43, 10:90].reshape(-1, 3).max(axis=0)
    assert np.all(peak <= np.array(PV.GREY)), (
        f"the ignored line peaks at {tuple(peak)}, brighter than "
        f"preview.GREY {PV.GREY} -- it is not being drawn in GREY")
    assert np.all(peak > np.array([96, 96, 96])), (
        f"the ignored line peaks at {tuple(peak)}, which antialiasing could "
        "have produced from the old inline (96, 96, 96) -- that grey is back")

    # the vanishing point ring comes from the same table
    assert tuple(PV.VP_MARK) in seen or any(
        abs(np.array(c) - np.array(PV.VP_MARK)).max() <= 40 for c in seen), (
        f"the vanishing-point ring is not drawn in preview.VP_MARK {PV.VP_MARK}")
