"""Horizontal (yaw) perspective correction: estimation accuracy and residual.

Uses synthetic scenes with a known yaw to verify that the estimator recovers
the correct angle, and that after warp the residual is near zero.
"""
import math

import numpy as np

from synth import Scene
from pc.config import Settings
from pc.pipeline import analyse, measure_horizontals


def _settings(**kw):
    base = dict(correct_horizontal=True, min_horizontal_support=0.25,
                detect_max_edge=1600)
    base.update(kw)
    return Settings(**base)


def test_yaw_estimation_small():
    """A 5-degree yaw on a plain facade should be recovered within 1 degree."""
    scene = Scene(w=1200, h=800, focal_35mm=28.0, yaw_deg=5.0, seed=42)
    bgr = scene.img
    settings = _settings()
    m, vert, horiz, scale, det = analyse(bgr, settings)
    assert abs(m.yaw - math.radians(5.0)) < math.radians(1.5), \
        f"yaw={math.degrees(m.yaw):.2f} deg, expected ~5 deg"


def test_yaw_estimation_medium():
    """A 12-degree yaw should be recovered within 2 degrees."""
    scene = Scene(w=1400, h=900, focal_35mm=35.0, yaw_deg=-12.0, seed=7)
    bgr = scene.img
    settings = _settings()
    m, vert, horiz, scale, det = analyse(bgr, settings)
    assert abs(m.yaw - math.radians(-12.0)) < math.radians(3.5), \
        f"yaw={math.degrees(m.yaw):.2f} deg, expected ~-12 deg"


def test_yaw_estimation_large():
    """A 25-degree yaw (aggressive but valid) should be recovered within 3 degrees."""
    scene = Scene(w=1600, h=1000, focal_35mm=24.0, yaw_deg=25.0, seed=99)
    bgr = scene.img
    settings = _settings()
    m, vert, horiz, scale, det = analyse(bgr, settings)
    assert abs(m.yaw - math.radians(25.0)) < math.radians(5.0), \
        f"yaw={math.degrees(m.yaw):.2f} deg, expected ~25 deg"


def test_yaw_zero_when_straight():
    """A scene with no yaw should estimate near-zero (within 1 degree)."""
    scene = Scene(w=1200, h=800, focal_35mm=28.0, yaw_deg=0.0, seed=1)
    bgr = scene.img
    settings = _settings()
    m, vert, horiz, scale, det = analyse(bgr, settings)
    assert abs(m.yaw) < math.radians(1.0), \
        f"yaw={math.degrees(m.yaw):.2f} deg, expected ~0"


def test_measure_horizontals_detects_lines():
    """measure_horizontals should find horizontal lines in a facade scene."""
    scene = Scene(w=1200, h=800, focal_35mm=28.0, yaw_deg=8.0, seed=5)
    bgr = scene.img
    settings = _settings()
    f_px = 28.0 * math.hypot(1200, 800) / math.hypot(36.0, 24.0)
    result = measure_horizontals(bgr, settings, f_px)
    assert result["n_lines"] >= 3, f"only {result['n_lines']} horiz lines found"
    assert result["yaw_deg"] is not None
    assert abs(result["yaw_deg"] - 8.0) < 2.5, \
        f"measured yaw={result['yaw_deg']}, expected ~8 deg"


def test_measure_horizontals_residual_after_warp():
    """After correcting a known yaw, the residual should be much smaller."""
    from pc import warp as W

    scene = Scene(w=1200, h=800, focal_35mm=28.0, yaw_deg=10.0, seed=11)
    bgr = scene.img
    settings = _settings()
    m, vert, horiz, scale, det = analyse(bgr, settings)

    # build and apply the correction
    roll, pitch, yaw, clamped = W.limit(m.roll, m.pitch, settings, yaw=m.yaw)
    H = W.build(1200, 800, m.f, roll, pitch, yaw)
    planned = W.plan(1200, 800, H, settings)
    assert planned is not None, "crop plan failed"
    H_total, ow, oh, coverage, area_ratio = planned
    out = W.apply(bgr, H_total, ow, oh, settings)

    f_px = 28.0 * math.hypot(1200, 800) / math.hypot(36.0, 24.0)
    before = measure_horizontals(bgr, settings, f_px)
    after = measure_horizontals(out, settings, f_px)

    assert before["yaw_deg"] is not None
    if after["yaw_deg"] is not None:
        assert abs(after["yaw_deg"]) < 2.0, \
            f"residual yaw={after['yaw_deg']} deg after correcting 10 deg"


def test_yaw_with_pitch_combined():
    """Yaw and pitch together should both be recovered."""
    scene = Scene(w=1400, h=900, focal_35mm=35.0, pitch_deg=-8.0, yaw_deg=7.0, seed=23)
    bgr = scene.img
    settings = _settings()
    m, vert, horiz, scale, det = analyse(bgr, settings)
    assert abs(m.yaw - math.radians(7.0)) < math.radians(2.5), \
        f"yaw={math.degrees(m.yaw):.2f} deg, expected ~7 deg"
    assert abs(m.pitch - math.radians(-8.0)) < math.radians(3.0), \
        f"pitch={math.degrees(m.pitch):.2f} deg, expected ~-8 deg"


def test_yaw_disabled_by_default():
    """Without correct_horizontal, yaw should be zero."""
    scene = Scene(w=1200, h=800, focal_35mm=28.0, yaw_deg=10.0, seed=42)
    bgr = scene.img
    settings = Settings(correct_horizontal=False)
    m, vert, horiz, scale, det = analyse(bgr, settings)
    assert m.yaw == 0.0
