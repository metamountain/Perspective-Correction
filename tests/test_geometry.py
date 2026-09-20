"""Geometry identities.  These are exact, so they are asserted exactly."""
import math

import numpy as np

from pc import geometry as G


def test_roll_pitch_round_trip():
    rng = np.random.default_rng(0)
    for _ in range(300):
        roll = rng.uniform(-0.5, 0.5)
        pitch = rng.uniform(-0.6, 0.6)
        u = G.up_from_roll_pitch(roll, pitch)
        r2, p2 = G.roll_pitch_from_up(u)
        assert abs(r2 - roll) < 1e-9, (roll, r2)
        assert abs(p2 - pitch) < 1e-9, (pitch, p2)


def test_correction_rotation_maps_up_to_image_up():
    rng = np.random.default_rng(1)
    for _ in range(200):
        roll, pitch = rng.uniform(-0.5, 0.5), rng.uniform(-0.6, 0.6)
        u = G.up_from_roll_pitch(roll, pitch)
        R = G.correction_rotation(roll, pitch)
        assert np.linalg.norm(R @ u - G.UP) < 1e-9


def test_correction_applies_roll_before_pitch():
    """The order is not cosmetic: levelling happens about the optical axis and
    must come first, otherwise the pitch axis is itself tilted."""
    roll, pitch = 0.2, 0.3
    R = G.correction_rotation(roll, pitch)
    assert np.allclose(R, G.rot_x(pitch) @ G.rot_z(-roll))
    # rolling first genuinely differs from pitching first
    assert not np.allclose(R, G.rot_z(-roll) @ G.rot_x(pitch))


def test_vanishing_point_at_infinity_is_a_normal_value():
    """The level-camera case.  chsasank/Image-Rectification scores it zero
    because it divides by the homogeneous coordinate; nothing here may."""
    seg = np.array([[100., 100., 100., 400.], [500., 80., 500., 420.]])
    lines = G.lines_from_segments(seg)
    vp = G.vp_from_two(lines[0], lines[1])
    assert abs(vp[2]) < 1e-12
    res = G.angular_residual(vp, G.segment_midpoints(seg), G.segment_directions(seg))
    assert np.all(res < 1e-9)
    refined = G.refine_vp(lines, G.segment_midpoints(seg), np.array([300., 340.]), vp)
    assert abs(refined[2]) < 1e-9


def test_level_camera_gives_identity_homography():
    K = G.intrinsics(900.0, 600.0, 400.0)
    vp = G.normalize_vp(K @ G.UP)
    roll, pitch = G.roll_pitch_from_up(G.up_vector(vp, K))
    H = G.homography(K, G.correction_rotation(roll, pitch))
    assert np.allclose(H, np.eye(3), atol=1e-9)


def test_horizon_of_a_level_camera_is_the_centre_row():
    K = G.intrinsics(900.0, 600.0, 400.0)
    h = G.horizon_line(G.UP, K)
    assert abs(-h[2] / h[1] - 400.0) < 1e-6


def test_focal_from_orthogonal_pair():
    f, cx, cy = 812.0, 640.0, 427.0
    K = G.intrinsics(f, cx, cy)
    u = G.up_from_roll_pitch(0.06, 0.21)
    b = np.array([0.9, 0.1, 0.4])
    b = b - (b @ u) * u
    b /= np.linalg.norm(b)
    got = G.focal_from_orthogonal(K @ u, K @ b, cx, cy)
    assert got is not None and abs(got - f) < 1e-3


def test_refine_vp_beats_the_two_line_estimate():
    rng = np.random.default_rng(3)
    K = G.intrinsics(900.0, 600.0, 400.0)
    u = G.up_from_roll_pitch(0.03, 0.16)
    vp_true = G.normalize_vp(K @ u)
    seg = []
    for x in np.linspace(120, 1080, 14):
        p0 = np.array([x, 620.0])
        d = G.bearing_to_vp(vp_true, p0[None, :])[0]
        d = d / np.linalg.norm(d) + rng.normal(0, 0.004, 2)
        p1 = p0 + d * 300
        seg.append([p0[0], p0[1], p1[0], p1[1]])
    seg = np.asarray(seg)
    lines = G.lines_from_segments(seg)
    mid, wts = G.segment_midpoints(seg), G.segment_lengths(seg)
    pair = G.vp_from_two(lines[0], lines[-1])
    refined = G.refine_vp(lines, mid, wts, pair)
    ang = lambda v: math.degrees(math.acos(min(1.0, abs(float(v @ vp_true)))))
    assert ang(refined) < ang(pair)
    assert ang(refined) < 0.2


def test_normalize_vp_signs_every_result_the_same_way_including_its_fallback():
    """The sign fix exists so equal vanishing points compare equal -- and one
    path used to skip it.

    A vanishing point is a line through the origin, not a ray, so `normalize_vp`
    flips the sign until the dominant component is positive. Every return did
    that except the zero-norm fallback, which handed back [0, -1, 0]: the only
    output in the function with a negative dominant component, and therefore
    the only one that could not compare equal to an identical direction that
    had arrived the normal way.

    That path is reachable -- `intersect` normalises `cross(l1, l2)`, which is
    the zero vector for two identical lines. Asserted as the invariant rather
    than as the fallback's particular value, because the defect was a path
    escaping the rule, not a wrong constant.
    """
    import numpy as np

    cases = [np.zeros(3),                       # the fallback
             np.array([0.0, -1.0, 0.0]),
             np.array([0.0, 1.0, 0.0]),
             np.array([-3.0, 1.0, 0.0]),
             np.array([600.0, 400.0, 1.0]),
             np.array([-1e-13, 1e-14, 0.0])]    # under the 1e-12 floor
    for v in cases:
        out = G.normalize_vp(v)
        k = int(np.argmax(np.abs(out)))
        assert out[k] > 0, f"normalize_vp({v.tolist()}) = {out.tolist()} is not sign-fixed"
        assert abs(np.linalg.norm(out) - 1.0) < 1e-9, f"{out.tolist()} is not unit"

    # the concrete consequence: a direction reached two ways must agree
    assert np.allclose(G.normalize_vp(np.zeros(3)),
                       G.normalize_vp(np.array([0.0, -1.0, 0.0])))
