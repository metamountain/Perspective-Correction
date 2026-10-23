"""Homography, limits and cropping."""
import math

import numpy as np

from pc import warp as W
from pc.config import Settings


def test_zero_angles_are_the_identity():
    H = W.build(900, 600, 800.0, 0.0, 0.0)
    assert np.allclose(H, np.eye(3), atol=1e-12)


def test_plan_of_the_identity_keeps_the_whole_frame():
    s = Settings()
    H = W.build(900, 600, 800.0, 0.0, 0.0)
    H_total, ow, oh, coverage, ratio = W.plan(900, 600, H, s)
    assert (ow, oh) == (900, 600)
    assert coverage > 0.999 and abs(ratio - 1.0) < 1e-9


def test_limits_clamp_and_report():
    s = Settings().replace(max_pitch_deg=10.0, max_roll_deg=5.0)
    roll, pitch, _yaw, clamped = W.limit(math.radians(20), math.radians(30), s)
    assert clamped
    assert abs(math.degrees(roll) - 5.0) < 1e-9
    assert abs(math.degrees(pitch) - 10.0) < 1e-9


def test_strength_scales_the_correction():
    s = Settings().replace(pitch_strength=0.5, roll_strength=0.25)
    roll, pitch, _yaw, _ = W.limit(math.radians(4), math.radians(8), s)
    assert abs(math.degrees(roll) - 1.0) < 1e-9
    assert abs(math.degrees(pitch) - 4.0) < 1e-9


def test_disabling_a_axis_zeroes_only_that_axis():
    s = Settings().replace(correct_roll=False)
    roll, pitch, _yaw, _ = W.limit(math.radians(4), math.radians(8), s)
    assert roll == 0.0 and abs(math.degrees(pitch) - 8.0) < 1e-9


def test_crop_stays_inside_the_warped_quad():
    s = Settings()
    H = W.build(1200, 800, 900.0, math.radians(4), math.radians(11))
    quad = W.warped_quad(H, 1200, 800)
    centre = np.array([quad[:, 0].mean(), quad[:, 1].mean()])
    centre = W.inscribed_rect(quad, 1200 / 800, centre)
    rect = np.array([[centre[0], centre[1]], [centre[2], centre[1]],
                     [centre[2], centre[3]], [centre[0], centre[3]]])
    assert W._inside(quad, rect * 0.999 + rect.mean(axis=0) * 0.001)


def test_crop_aspect_matches_the_source_aspect():
    s = Settings().replace(crop="aspect")
    H = W.build(1200, 800, 900.0, math.radians(3), math.radians(9))
    _, ow, oh, coverage, _ = W.plan(1200, 800, H, s)
    assert abs(ow / oh - 1200 / 800) < 0.02
    assert 0.3 < coverage < 1.0


def test_auto_crop_is_bounded_to_the_strip_plus_20_percent():
    """Ledger A.4 (measured 2026-09-20): cropping to the strip +20% was worth
    more than a second correction pass on its own, and 20% beat a 30% guess
    by 8x. This applies the same band to the production auto-crop path --
    specifically the `_whole_frame` fallback `crop="auto"` takes at any real
    horizontal-auto yaw (coverage loss already exceeds even the review
    window's 30% budget well under 20deg, so this is the path a strip
    actually has to survive, not the aspect/inside path)."""
    w, h, f = 1600, 1000, 1400.0
    yaw = math.radians(20.0)
    H = W.build(w, h, f, math.radians(1.0), math.radians(6.0), yaw)
    strip = (0.0, 0.30)

    quad = W.warped_quad(H, w, h)
    band = W._strip_band(H, quad, strip, w, h)
    assert band is not None

    s = Settings()  # crop="auto" default; no line_segs -- the strip is all
                    # there is to go on, which is exactly the gap being closed
    planned = W.plan(w, h, H, s, yaw=yaw, strip=strip)
    assert planned is not None
    H_total, ow, oh, _, _ = planned

    # H_total = S @ H with S a pure translate in this branch (keep_size is
    # off by default), so inverting H back out recovers the crop's x-range
    # in the same warped space as quad/band.
    S = H_total @ np.linalg.inv(H)
    rect_x0 = -S[0, 2] / S[0, 0]
    rect_x1 = rect_x0 + ow / S[0, 0]

    tol = 1.0
    assert rect_x0 >= band[0] - tol, (
        f"crop left edge {rect_x0:.1f} is outside the strip+20% band {band}")
    assert rect_x1 <= band[1] + tol, (
        f"crop right edge {rect_x1:.1f} is outside the strip+20% band {band}")

    # Non-vacuity: without the strip, the same yaw's whole-frame crop really
    # does exceed the band -- otherwise this test would pass for a reason it
    # doesn't name (see CLAUDE.md's gotcha on exactly that failure mode).
    H_total0, ow0, *_ = W.plan(w, h, H, s, yaw=yaw)
    S0 = H_total0 @ np.linalg.inv(H)
    rw0 = ow0 / S0[0, 0]
    assert rw0 > (band[1] - band[0]) + tol


def test_keep_size_returns_the_original_dimensions():
    s = Settings().replace(crop="aspect", keep_size=True)
    _, ow, oh, _, _ = W.plan(1200, 800, W.build(1200, 800, 900.0, 0.05, 0.15), s)
    assert (ow, oh) == (1200, 800)


def test_pure_roll_is_a_rotation_of_the_frame():
    """Levelling must not depend on the focal length, so the same roll at two
    very different focal lengths has to produce the same warp."""
    a = W.build(1000, 700, 500.0, math.radians(6), 0.0)
    b = W.build(1000, 700, 3000.0, math.radians(6), 0.0)
    assert np.allclose(a, b, atol=1e-9)


def test_yaw_is_zero_when_horizontal_correction_is_off():
    """The default run must be byte-identical to before yaw existed: with the
    flag off, any requested yaw is forced to zero and nothing clamps."""
    s = Settings()  # correct_horizontal=False by default
    roll, pitch, yaw, clamped = W.limit(math.radians(4), math.radians(8), s,
                                        yaw=math.radians(10))
    assert yaw == 0.0
    assert not clamped
    assert abs(math.degrees(roll) - 4.0) < 1e-9
    assert abs(math.degrees(pitch) - 8.0) < 1e-9


def test_yaw_is_capped_tighter_and_reports_clamped():
    s = Settings().replace(correct_horizontal=True, max_horizontal_deg=8.0)
    roll, pitch, yaw, clamped = W.limit(0.0, 0.0, s, yaw=math.radians(15))
    assert clamped
    assert abs(math.degrees(yaw) - 8.0) < 1e-9


def test_yaw_strength_scales_the_correction():
    s = Settings().replace(correct_horizontal=True, horizontal_strength=0.5)
    _, _, yaw, _ = W.limit(0.0, 0.0, s, yaw=math.radians(10))
    assert abs(math.degrees(yaw) - 5.0) < 1e-9


def test_zero_yaw_builds_the_same_homography_as_before():
    """A zero yaw must reproduce the old two-angle homography exactly, so an
    off-by-default feature cannot move a single output pixel."""
    H_old = W.build(900, 600, 800.0, 0.1, 0.2)
    H_new = W.build(900, 600, 800.0, 0.1, 0.2, 0.0)
    assert np.allclose(H_old, H_new, atol=1e-12)


def test_nonzero_yaw_changes_the_homography():
    H0 = W.build(900, 600, 800.0, 0.1, 0.2, 0.0)
    Hy = W.build(900, 600, 800.0, 0.1, 0.2, math.radians(5))
    assert not np.allclose(H0, Hy)


def _area(rect):
    return (rect[2] - rect[0]) * (rect[3] - rect[1])


def test_max_inscribed_rect_beats_the_centred_one_on_an_asymmetric_quad():
    """The hand-pressed auto-crop wants the largest rectangle anywhere, not one
    anchored on the mapped centre.  On a warped quad those differ, and this is
    the assertion that the anchor was actually removed: the free rectangle must
    be at least as large as the centred one, and strictly larger where the
    correction opens the band on one side only."""
    H = W.build(1200, 800, 900.0, math.radians(4), math.radians(11))
    quad = W.warped_quad(H, 1200, 800)
    aspect = 1200 / 800
    centre = np.array([quad[:, 0].mean(), quad[:, 1].mean()])
    centred = W.inscribed_rect(quad, aspect, centre)
    free = W.max_inscribed_rect(quad, aspect)
    assert free is not None
    assert _area(free) >= _area(centred) - 1e-6
    assert _area(free) > _area(centred) * 1.02


def test_max_inscribed_rect_stays_inside_and_keeps_the_aspect():
    H = W.build(1200, 800, 900.0, math.radians(4), math.radians(11))
    quad = W.warped_quad(H, 1200, 800)
    aspect = 1200 / 800
    x0, y0, x1, y1 = W.max_inscribed_rect(quad, aspect)
    corners = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
    assert W._inside(quad, corners * 0.999 + corners.mean(axis=0) * 0.001)
    assert abs((x1 - x0) / (y1 - y0) - aspect) < 1e-6


def test_max_inscribed_rect_of_a_plain_frame_is_the_whole_frame():
    quad = np.array([[0, 0], [1200, 0], [1200, 800], [0, 800]], dtype=float)
    assert np.allclose(W.max_inscribed_rect(quad, 1.5), [0, 0, 1200, 800], atol=1e-6)


def test_max_inscribed_rect_of_a_degenerate_quad_is_none():
    quad = np.array([[0, 0], [10, 0], [20, 0], [30, 0]], dtype=float)
    assert W.max_inscribed_rect(quad, 1.5) is None


def test_the_keep_pixels_dial_trades_canvas_for_detail_and_one_loses_none():
    """`keep_pixels` is the one knob over that trade, and it must work.

    ``H = K R K^-1`` at a large yaw inflates the receding edge five- to
    sevenfold, and the plan then scales the output back so the facade keeps
    *about* its source pixel count.  On average that is right.  At the **near**
    edge -- the part of the building closest to the camera, carrying the most
    real detail -- it is a loss, because the average is dragged up by the edge
    that was inflated.  Measured 2026-09-20 before the dial existed: the canvas
    grew to 2.3-7x the source area while the near edge was still sampled at
    **0.71-0.95**, which is invisible in the output size and is why it survived.

    Two claims, both against the definition rather than today's numbers:
      * at 0.0 the short/near edge is the reference, so NOTHING is sampled
        below 1:1 -- no photographed detail is discarded;
      * the dial is monotonic -- turning it up must not both shrink the canvas
        and keep the sampling, or it is not a trade and the number means nothing.
    """
    import numpy as np

    from pc import warp as W
    from pc.config import Settings

    w, h, f = 1600, 1000, 1400.0
    bit = False
    for yaw_deg in (20.0, 35.0, 50.0):
        yaw = np.radians(yaw_deg)
        H = W.build(w, h, f, np.radians(1.0), np.radians(6.0), yaw)

        seen = []
        for dial in (1.0, 0.5, 0.0):        # most pixels -> fewest
            st = Settings(correct_horizontal=True)
            st.keep_pixels = dial
            plan = W.plan(w, h, H, st, yaw=yaw)
            assert plan is not None, f"no plan at yaw {yaw_deg}, dial {dial}"
            seen.append((W.min_magnification(plan[0], w, h, plan[1], plan[2]),
                         plan[1] * plan[2]))

        # keep_pixels=1 keeps every pixel UP TO the canvas ceiling. The two
        # promises collide at a strong yaw -- holding the near edge at 1:1
        # through 50 deg would ask for roughly 35x the source area, which on a
        # 24 MP original is a failed allocation rather than a large file -- and
        # max_area_ratio is the one that wins, because a plan that cannot be
        # executed is not a plan. So: either the sampling is kept, or the
        # ceiling is the reason it was not.
        capped = seen[0][1] >= 0.99 * Settings.max_area_ratio * w * h
        assert seen[0][0] >= 0.99 or capped, (
            f"yaw {yaw_deg} deg at keep_pixels=1.0: the near edge is sampled "
            f"at {seen[0][0]:.3f} and the canvas is {seen[0][1] / (w * h):.1f}x "
            f"the source, under the {Settings.max_area_ratio}x ceiling. Detail "
            f"was discarded with room to spare")

        if capped:
            # Where the ceiling binds, IT decides the canvas and the dial stops
            # having an effect -- all three settings land on the same plan.
            # That is the price of the ceiling and it is better said than
            # discovered: a dial that silently stops working is worse than one
            # documented to saturate.
            assert max(a for a, _ in seen) - min(a for a, _ in seen) < 1e-3, (
                f"yaw {yaw_deg} deg: the canvas is at the ceiling, so the dial "
                f"cannot move the sampling: {[round(a, 3) for a, _ in seen]}")
            continue

        if seen[2][0] < 0.99:          # this yaw actually exercises the trade
            bit = True
            assert seen[0][0] >= seen[1][0] >= seen[2][0] - 1e-9, (
                f"yaw {yaw_deg} deg: sampling is not monotonic in the dial: "
                f"{[round(a, 3) for a, _ in seen]}")
            assert seen[0][1] >= seen[1][1] >= seen[2][1], (
                f"yaw {yaw_deg} deg: the canvas is not monotonic in the dial: "
                f"{[b for _, b in seen]}")
            assert seen[0][1] > seen[2][1], (
                f"yaw {yaw_deg} deg: keep_pixels=1 kept more detail than 0 "
                f"without costing a single pixel of canvas, which cannot be true")

    assert bit, ("no yaw in the sweep downsampled even at keep_pixels=0, so this "
                 "cannot fail and is not testing anything -- pick a harder case")


def test_the_near_edge_floor_leaves_a_pure_roll_and_pitch_warp_alone():
    """No yaw, no asymmetry, no reason to touch the plan.

    The floor is deliberately scoped to yaw: a roll/pitch correction is
    symmetric about the centre and does not inflate one edge against the other,
    and widening its scope would move every output size in the suite for
    nothing.  Pinned so that scope is a decision somebody has to undo on
    purpose.
    """
    import numpy as np

    from pc import warp as W
    from pc.config import Settings

    w, h = 1600, 1000
    H = W.build(w, h, 1400.0, np.radians(2.0), np.radians(8.0), 0.0)
    a = W.plan(w, h, H, Settings(), yaw=0.0)
    b = Settings()
    b.preserve_near_edge = False
    c = W.plan(w, h, H, b, yaw=0.0)
    assert a is not None and c is not None
    assert (a[1], a[2]) == (c[1], c[2]), (
        "the near-edge floor changed a zero-yaw plan; it is scoped to yaw")


def test_the_time_estimate_is_honest_about_what_it_does_not_know():
    """`progress` exists so the window can say how long, and a progress
    indicator that lies teaches people to ignore progress indicators. Four
    properties, all of them about honesty rather than accuracy:

      * an unknown stage returns 0.0, not a guess -- the caller then shows a
        working indicator with no time on it;
      * under two seconds there is no message at all, because a box that
        appears and vanishes reads as a fault;
      * the estimate grows with the canvas, or it is not an estimate;
      * telea costs MORE than lama at full size. That is the reverse of the
        preview and it is measured: telea works on the real hole and grows
        with it, lama generates at fill_max_edge and pastes back. The
        constants must keep that ordering or they have drifted from the
        measurement they came from.
    """
    from pc import progress as PR

    assert PR.estimate("no-such-stage", 100.0) == 0.0
    assert PR.humanise(PR.estimate("no-such-stage", 100.0)) == ""
    assert PR.humanise(1.4) == "", "a sub-two-second job must say nothing"
    assert PR.humanise(11.0) == "about 11 s"
    assert PR.humanise(95.0) == "about 1:35 min"

    small = PR.estimate("fill:telea", 9.0)
    large = PR.estimate("fill:telea", 108.0)
    assert large > small, "the estimate must grow with the canvas"

    assert PR.estimate("fill:telea", 108.0) > PR.estimate("fill:lama", 108.0), (
        "measured 13.1 s against 4.5 s on a 108 MPx canvas -- telea is the "
        "slow one at full size, however cheap it is in the preview")


def test_eave_y_is_the_topmost_long_horizontal_in_the_band():
    # Identity warp: mapped length equals source length.  EAVE_MIN_LEN * ow is
    # the length floor, so the short segment is ignored and the topmost of the
    # long ones (y=50, above y=80) is the eave.
    H = np.identity(3)
    ow = 1000.0
    segs = np.array([
        [100.0, 10.0, 120.0, 10.0],    # length 20 < 0.15*1000=150, too short
        [0.0, 50.0, 1000.0, 50.0],     # length 1000, counts, mid-y 50
        [0.0, 80.0, 1000.0, 80.0],     # length 1000, counts, mid-y 80
    ])
    assert W.eave_y(H, segs, ow, 0.0, ow) == 50.0

    # A segment whose midpoint falls outside the x-band is not the eave.
    segs_off = np.array([
        [2000.0, 50.0, 3000.0, 50.0],  # mid-x 2500, outside [0, 1000]
    ])
    assert W.eave_y(H, segs_off, ow, 0.0, ow) is None

    # All short -> no eave.
    segs_short = np.array([
        [100.0, 10.0, 120.0, 10.0],
        [200.0, 60.0, 210.0, 60.0],
    ])
    assert W.eave_y(H, segs_short, ow, 0.0, ow) is None


def _quad_cases():
    """Quadrilaterals a user might trace around a facade, corners in the order
    top-left, top-right, bottom-right, bottom-left (image y down)."""
    yield "square", [(300, 200), (700, 200), (700, 600), (300, 600)]
    yield "rhombus", [(500, 150), (750, 400), (500, 650), (250, 400)]
    yield "parallelogram", [(320, 180), (760, 230), (700, 620), (260, 570)]
    yield "steep parallelogram", [(420, 120), (640, 150), (520, 680), (300, 650)]
    yield "keystone trapezoid", [(380, 160), (620, 160), (760, 640), (240, 640)]
    yield "sideways trapezoid", [(260, 120), (740, 260), (740, 540), (260, 680)]
    rng = np.random.default_rng(7)
    n = 0
    while n < 40:
        base = np.array([(300, 200), (700, 200), (700, 600), (300, 600)], float)
        q = base + rng.uniform(-110, 110, size=(4, 2))
        # keep it convex and in the clockwise corner order
        def _z(a, b):
            return a[0] * b[1] - a[1] * b[0]
        cross = [_z(q[(i + 1) % 4] - q[i], q[(i + 2) % 4] - q[(i + 1) % 4])
                 for i in range(4)]
        if all(c > 0 for c in cross):
            n += 1
            yield f"random {n}", [tuple(p) for p in q]


def test_a_drawn_rhombus_parallelogram_or_trapezoid_becomes_a_rectangle():
    """Two drawn verticals and two drawn horizontals are a rectangle (user,
    2026-10-20): whatever quadrilateral was traced -- rhombus, parallelogram
    (both vanishing points at infinity), trapezoid, any convex quad -- the
    warp makes every side axis-aligned and every corner 90 degrees."""
    w, h = 1000, 800
    for name, (tl, tr, br, bl) in _quad_cases():
        verts = np.array([[*tl, *bl], [*tr, *br]], float)
        horiz = np.array([[*tl, *tr], [*bl, *br]], float)
        vv = W.vp_from_lines(verts, np.array([0.0, 1.0, 0.0]))
        vh = W.vp_from_lines(horiz, np.array([1.0, 0.0, 0.0]))
        H = W.rectify(vv, vh, w, h)
        assert H is not None, name
        q = W.G.apply_h(H, np.array([tl, tr, br, bl], float))
        for i in range(4):
            a, b, c = q[i - 1], q[i], q[(i + 1) % 4]
            u, v = a - b, c - b
            cos = float(u @ v) / (np.linalg.norm(u) * np.linalg.norm(v))
            assert abs(cos) < 1e-7, (name, "corner", i, math.degrees(math.acos(cos)))
        # and axis-aligned, not merely a rotated rectangle
        assert abs(q[0, 1] - q[1, 1]) < 1e-6 * h and abs(q[3, 1] - q[2, 1]) < 1e-6 * h, name
        assert abs(q[0, 0] - q[3, 0]) < 1e-6 * w and abs(q[1, 0] - q[2, 0]) < 1e-6 * w, name


def test_one_drawn_line_is_exact_and_leaves_the_rest_to_the_detector():
    """One line per direction: exact for that line, and the detector's
    vanishing point is only moved onto it (the shortest way), not replaced."""
    w, h = 1000, 800
    auto_vp = np.array([480.0, -9000.0, 1.0])            # detector: verticals converge up
    drawn = np.array([[300.0, 100.0, 320.0, 700.0]])      # the user disagrees a little
    vv = W.vp_from_lines(drawn, auto_vp)
    x1, y1, x2, y2 = drawn[0]
    assert abs(np.cross([x1, y1, 1], [x2, y2, 1]) @ vv) < 1e-6 * np.linalg.norm(vv), \
        "the vanishing point must lie on the drawn line"
    l = np.cross([x1, y1, 1.0], [x2, y2, 1.0])
    l = l / np.hypot(l[0], l[1])
    moved = np.hypot(vv[0] / vv[2] - auto_vp[0], vv[1] / vv[2] - auto_vp[1])
    assert abs(moved - abs(l @ auto_vp)) < 1e-6 * abs(auto_vp[1]), \
        "one line moves the detector's point the shortest way onto it, it does not replace it"
    H = W.rectify(vv, np.array([1.0, 0.0, 0.0]), w, h)
    p = W.G.apply_h(H, np.array([[x1, y1], [x2, y2]]))
    assert abs(p[0, 0] - p[1, 0]) < 1e-6 * w


def test_the_interest_box_ignores_slanted_foreground_but_keeps_a_gable():
    """00010519 (user, 2026-10-23: "Bild sitzt gut, Auto-Crop noch nicht"): a
    railing running down a stair pulled the box into the foreground. Only
    straight segments frame it; a long slanted one above may raise the top."""
    import numpy as np
    from pc import warp as W
    rng = np.random.default_rng(0)
    facade = []
    for _ in range(80):                       # windows: straight, x 200-600, y 200-500
        x, y = rng.uniform(200, 600), rng.uniform(200, 500)
        facade += [[x, y, x + 30, y], [x, y, x, y + 30]]
    railing = [[300 + 40 * i, 560 + 12 * i, 340 + 40 * i, 572 + 12 * i] for i in range(12)]
    gable = [[300, 200, 400, 120], [400, 120, 500, 200]]
    H = np.eye(3)
    plain = W.interest_box(H, np.array(facade, float), 1000, 800)
    with_rail = W.interest_box(H, np.array(facade + railing, float), 1000, 800)
    assert with_rail[3] < 560 and abs(with_rail[2] - plain[2]) < 1, (plain, with_rail)
    with_gable = W.interest_box(H, np.array(facade + railing + gable, float), 1000, 800)
    assert with_gable[1] < 125, ("the gable was cut off", with_gable)


def test_a_pitch_correction_keeps_the_motif_at_full_resolution():
    """User, 2026-10-23: after Auto the house had visibly fewer pixels. A pitch
    of 16 deg at a 15 mm-ish focal (the measured case, 768x1024, f=444 px)
    stretches the top, and fitting the long edge back to the source sampled the
    middle at 0.84. `keep_centre` must bring the motif's centre to >= 1:1, and
    the old plan must really have been below it (or this test proves nothing)."""
    w, h, f = 768, 1024, 444.0
    H = W.build(w, h, f, math.radians(-1.0), math.radians(16.2), 0.0, max_area=9.0)
    s = Settings().replace(keep_size=True, max_area_ratio=9.0)
    Ht, ow, oh, _c, _a = W.plan(w, h, H, s)
    centre = (0.55 * w, 0.55 * h)
    before = min(W.magnification_at(Ht, *centre))
    assert before < 0.95, f"the case no longer shrinks the middle ({before:.2f})"
    segs = np.array([[0.40 * w, 0.40 * h, 0.40 * w, 0.70 * h],
                     [0.70 * w, 0.40 * h, 0.70 * w, 0.70 * h],
                     [0.40 * w, 0.55 * h, 0.70 * w, 0.55 * h]])
    Ht2, ow2, oh2 = W.keep_centre(Ht, ow, oh, w, h, segs)
    after = min(W.magnification_at(Ht2, *W.interest_point(w, h, segs)))
    assert after >= 0.999, after
    assert ow2 / ow == oh2 / oh or abs(ow2 / ow - oh2 / oh) < 0.01, "not a pure scale"
    assert ow2 <= ow * W.NEAR_EDGE_MAX_SCALE + 1
    # Already at full resolution: left alone.
    assert W.keep_centre(Ht2, ow2, oh2, w, h, segs)[1:] == (ow2, oh2)
