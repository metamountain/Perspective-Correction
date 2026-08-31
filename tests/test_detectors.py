"""The alternative line detectors and the hybrid gate.

The M-LSD tests skip cleanly when no TFLite runtime is installed, because it is
an optional dependency and the default path must not require it.
"""
import math

import numpy as np

import synth
from bpc import lines as L
from bpc.config import Settings
from bpc.pipeline import analyse


def _mlsd_or_skip():
    from bpc import mlsd
    if not mlsd.available():
        raise SkipTest("no TFLite runtime or model")     # noqa: F821
    return mlsd


def test_gate_keeps_segments_that_lie_along_a_guide():
    guide = np.array([[100., 0., 100., 400.]])
    seg = np.array([[100., 50., 100., 120.],     # on it
                    [103., 200., 103., 260.],    # just beside it, parallel
                    [300., 50., 300., 120.],     # far away
                    [100., 50., 160., 120.]])    # crossing at a large angle
    kept = L.gate_by(seg, guide, angle_tol_deg=6.0, dist_tol=8.0)
    assert len(kept) == 2
    assert set(kept[:, 0].tolist()) == {100.0, 103.0}


def test_gate_respects_the_guide_extent():
    """A guide segment vouches for its own stretch of an edge, not for the
    whole infinite line through it."""
    guide = np.array([[100., 0., 100., 100.]])
    seg = np.array([[100., 20., 100., 60.],       # inside the guide's extent
                    [100., 900., 100., 950.]])    # same infinite line, far off the end
    kept = L.gate_by(seg, guide, angle_tol_deg=6.0, dist_tol=8.0)
    assert len(kept) == 1 and kept[0][1] == 20.0


def test_gate_is_a_no_op_without_a_guide():
    seg = np.array([[100., 0., 100., 400.]])
    assert np.array_equal(L.gate_by(seg, np.zeros((0, 4))), seg)


def test_mlsd_returns_long_structural_segments():
    _mlsd_or_skip()
    sc = synth.Scene(w=1200, h=800, pitch_deg=8, seed=41)
    st = Settings().replace(detector="mlsd")
    _, vert, horiz, name, _ = L.prepare(*_grids(sc, st))
    assert name == "mlsd"
    assert len(vert) + len(horiz) > 0


def test_hybrid_never_gates_the_evidence_away_entirely():
    """The gate falls back to plain LSD rather than starving the fit, which is
    what makes it safe to switch on."""
    _mlsd_or_skip()
    sc = synth.Scene(w=1200, h=800, pitch_deg=8, seed=42)
    st = Settings().replace(detector="hybrid")
    _, vert, horiz, name, _ = L.prepare(*_grids(sc, st))
    assert name in ("hybrid", "lsd(hybrid fallback)")
    assert len(vert) >= st.min_vertical_lines


def test_every_detector_produces_a_usable_estimate():
    from bpc import mlsd
    sc = synth.Scene(w=1200, h=800, focal_35mm=28, pitch_deg=9, roll_deg=-2, seed=43)
    tr, tp = sc.true_roll_pitch()
    names = ["lsd", "hough"] + (["mlsd", "hybrid", "union"] if mlsd.available() else [])
    for name in names:
        st = Settings().replace(detector=name, focal_35mm=28)
        m, _, _, _, _ = analyse(sc.img, st)
        assert abs(math.degrees(m.roll - tr)) < 3.0, f"{name}: roll off by too much"
        assert abs(math.degrees(m.pitch - tp)) < 6.0, f"{name}: pitch off by too much"


def _grids(scene, settings):
    from bpc import imageio as IO
    gray, _ = IO.analysis_gray(scene.img, settings.detect_max_edge)
    import cv2
    small = cv2.resize(scene.img, (gray.shape[1], gray.shape[0]),
                       interpolation=cv2.INTER_AREA)
    return gray, settings, small
