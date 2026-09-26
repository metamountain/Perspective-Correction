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
