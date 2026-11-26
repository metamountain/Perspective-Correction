"""The alternative line detectors.

The M-LSD test skips cleanly when its optional dependency is missing -- a
TFLite runtime -- because the default path must not require it.
"""
import math

import synth
from pc import lines as L
from pc.config import Settings
from pc.pipeline import analyse


def _mlsd_or_skip():
    from pc import mlsd
    if not mlsd.available():
        raise SkipTest("no TFLite runtime or model")     # noqa: F821
    return mlsd


def test_mlsd_returns_long_structural_segments():
    _mlsd_or_skip()
    sc = synth.Scene(w=1200, h=800, pitch_deg=8, seed=41)
    st = Settings().replace(detector="mlsd")
    _, vert, horiz, name, _ = L.prepare(*_grids(sc, st))
    assert name == "mlsd"
    assert len(vert) + len(horiz) > 0


def test_every_detector_produces_a_usable_estimate():
    from pc import mlsd
    sc = synth.Scene(w=1200, h=800, focal_35mm=28, pitch_deg=9, roll_deg=-2, seed=43)
    tr, tp = sc.true_roll_pitch()
    names = ["lsd"] + (["mlsd", "union"] if mlsd.available() else [])
    for name in names:
        st = Settings().replace(detector=name, focal_35mm=28)
        m, _, _, _, _ = analyse(sc.img, st)
        assert abs(math.degrees(m.roll - tr)) < 3.0, f"{name}: roll off by too much"
        assert abs(math.degrees(m.pitch - tp)) < 6.0, f"{name}: pitch off by too much"


def _grids(scene, settings):
    from pc import imageio as IO
    gray, _ = IO.analysis_gray(scene.img, settings.detect_max_edge)
    import cv2
    small = cv2.resize(scene.img, (gray.shape[1], gray.shape[0]),
                       interpolation=cv2.INTER_AREA)
    return gray, settings, small
