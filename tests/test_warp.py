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
