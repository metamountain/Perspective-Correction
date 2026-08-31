"""What the reference implementation gets wrong, kept as executable claims.

chsasank/Image-Rectification is where this project started.  Each test here
pins a failure that was measured on it (see docs/reference-review.md) and
asserts that this implementation does not repeat it.  The reference itself is
not imported -- it is not a dependency -- so these are statements about *this*
code, phrased against the behaviour that made a rewrite necessary.
"""
import math

import numpy as np

import synth
from bpc import geometry as G
from bpc import vanishing as V
from bpc.config import Settings
from bpc.lines import LineSet, split_by_orientation
from bpc.pipeline import analyse


def test_parallel_verticals_score_full_marks_not_zero():
    """``compute_votes`` in the reference computes ``vp[:2] / vp[2]`` first, so
    the level-camera case -- ``vp[2] == 0``, the commonest input there is --
    scores zero votes and systematically loses to a converging hypothesis.
    Measured: two exactly parallel verticals received ``[0., 0.]``."""
    seg = np.array([[100., 100., 100., 400.], [500., 80., 500., 420.]])
    ls = LineSet(seg)
    vp = G.vp_from_two(ls.lines[0], ls.lines[1])
    assert abs(vp[2]) < 1e-12
    res = G.angular_residual(vp, ls.mid, ls.dir)
    assert np.all(res < 1e-9)


def test_the_parallel_model_is_an_explicit_candidate():
    """RANSAC over pairs can never propose "exactly parallel", so an already
    straight photo is decided by whichever noise realisation won."""
    sc = synth.Scene(pitch_deg=0, roll_deg=0, seed=31)
    s = Settings()
    from bpc import imageio as IO
    from bpc import lines as L
    gray, _ = IO.analysis_gray(sc.img, s.detect_max_edge)
    _, vert, _, _, _ = L.prepare(gray, s)
    par = V.parallel_hypothesis(vert, s)
    assert par is not None and abs(par.vp[2]) < 1e-12
    assert par.support > 0.3


def test_the_dominant_direction_is_not_assumed_to_be_the_answer():
    """The reference takes the strongest vanishing point whatever it is; on a
    gabled building that can be the roof.  The vertical search is constrained
    by an orientation prior instead."""
    sc = synth.Scene(pitch_deg=7, roll_deg=0, seed=32)
    m, _, _, _, _ = analyse(sc.img, Settings().replace(focal_35mm=28))
    u = m.up
    assert abs(float(u @ G.UP)) > math.cos(math.radians(20)), "vertical is not vertical"


def test_a_vanishing_point_inside_the_frame_is_rejected():
    """Verticals that converge inside the visible area describe no photograph of
    a building.  darktable rejects this case too."""
    s = Settings()
    inside = G.normalize_vp(np.array([600.0, 400.0, 1.0]))
    assert not V._plausible_vertical(inside, 600.0, 400.0, 1200.0, 40.0)
    outside = G.normalize_vp(np.array([620.0, -5000.0, 1.0]))
    assert V._plausible_vertical(outside, 600.0, 400.0, 1200.0, 40.0)


def test_the_warp_is_a_camera_rotation_so_it_cannot_shear():
    """The reference builds a general projective transform plus an affine
    "make the axes orthogonal" step, which can shear a building into a
    trapezoid.  ``K R K^-1`` has three degrees of freedom, all physical."""
    from bpc import warp as W
    H = W.build(1200, 800, 900.0, math.radians(5), math.radians(12))
    K = G.intrinsics(900.0, 600.0, 400.0)
    R = np.linalg.inv(K) @ H @ K
    R = R / np.linalg.det(R) ** (1 / 3)
    assert np.allclose(R @ R.T, np.eye(3), atol=1e-6), "not a rotation"


def test_no_correction_is_applied_without_evidence():
    """The reference always warps.  Here an image that cannot be judged is
    returned untouched, because an unchanged photo is a cheaper mistake than a
    wrongly warped one."""
    m, _, _, _, _ = analyse(synth.flat_image(), Settings())
    assert m.confidence < Settings().min_confidence
