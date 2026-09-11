"""Homography, limits and cropping."""
import math

import numpy as np

from bpc import warp as W
from bpc.config import Settings


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
