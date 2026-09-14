"""Tests for the Stage 0 lens-distortion correction (lensfunpy route)."""
from __future__ import annotations

import numpy as np


def test_available_reports_install_state():
    from bpc import distortion as D
    ok = D.available()
    assert isinstance(ok, bool)
    desc = D.describe()
    assert isinstance(desc, str) and len(desc) > 0


def test_undistort_map_returns_none_without_exif():
    from bpc import distortion as D
    result = D.undistort_map(None, 1920, 1080)
    assert result is None


def test_undistort_map_returns_none_with_empty_bytes():
    from bpc import distortion as D
    result = D.undistort_map(b"", 1920, 1080)
    assert result is None


def test_apply_undistorted_identity_map_matches_warp():
    """When the undistortion map is identity (no distortion) and H_total is a
    pure rotation about the image centre (no crop translation), the composed
    remap must produce the same result as plain warpPerspective."""
    import cv2
    import math
    from bpc.config import Settings
    from bpc import warp as W, geometry as G

    w, h = 400, 300
    # Smooth gradient image: avoids Lanczos ringing on per-pixel noise that
    # would amplify the tiny float32/float64 coordinate differences between
    # warpPerspective and remap into visible pixel-level diffs.
    gx = np.linspace(0, 255, w, dtype=np.float32)
    gy = np.linspace(0, 255, h, dtype=np.float32)
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :, 0] = gx[np.newaxis, :]       # R: horizontal gradient
    img[:, :, 1] = gy[:, np.newaxis]       # G: vertical gradient
    img[:, :, 2] = 128                     # B: flat

    st = Settings(crop="none")  # no crop → H_total is pure rotation about centre

    f = 1000.0
    roll = math.radians(2.0)
    K = G.intrinsics(f, w / 2, h / 2)
    R = G.correction_rotation(roll, 0.0, 0.0)
    H = G.homography(K, R)
    H_total, ow, oh, cov, ar = W.plan(w, h, H, st)

    # Identity undistortion map: map_x[x,y] = x, map_y[x,y] = y
    ys, xs = np.mgrid[0:h, 0:w]
    ident_map = (xs.astype(np.float32), ys.astype(np.float32))

    out_normal = W.apply(img, H_total, ow, oh, st)
    out_undist = W.apply_undistorted(img, H_total, ow, oh, st, ident_map)

    # On a smooth gradient the two paths should agree to within 1-2 levels
    # everywhere except the extreme border where replicate vs remap-edge
    # handling can differ.
    diff = np.abs(out_normal.astype(int) - out_undist.astype(int))
    assert diff.max() <= 40, f"identity map diverged: max diff {diff.max()}"
    interior = diff[5:-5, 5:-5]
    assert interior.max() <= 3, f"interior diverged: max diff {interior.max()}"


def test_apply_undistorted_with_shifted_map():
    """A uniform shift in the undistortion map shifts the output by that amount."""
    import cv2
    from bpc.config import Settings
    from bpc import warp as W, geometry as G
    import math

    w, h = 400, 300
    img = np.zeros((h, w, 3), dtype=np.uint8)
    # Draw a bright vertical line at x=200
    img[:, 198:202] = 255
    st = Settings()

    f = 1000.0
    roll = math.radians(1.0)
    K = G.intrinsics(f, w / 2, h / 2)
    R = G.correction_rotation(roll, 0.0, 0.0)
    H = G.homography(K, R)
    H_total, ow, oh, cov, ar = W.plan(w, h, H, st)

    # Shift the map by +5 px in x: source is sampled 5px to the right
    ys, xs = np.mgrid[0:h, 0:w]
    shifted_x = (xs.astype(np.float32) + 5.0)
    shifted_y = ys.astype(np.float32)

    out_normal = W.apply(img, H_total, ow, oh, st)
    out_shifted = W.apply_undistorted(img, H_total, ow, oh, st, (shifted_x, shifted_y))

    # The bright line should be ~5px further left in the shifted output
    # (sampling from 5px right means the feature appears 5px left).
    col_normal = np.where(out_normal[oh // 2, :, 0] > 128)[0]
    col_shifted = np.where(out_shifted[oh // 2, :, 0] > 128)[0]
    if len(col_normal) and len(col_shifted):
        shift = col_normal.mean() - col_shifted.mean()
        assert 3 < shift < 7, f"expected ~5px shift, got {shift:.1f}"


def test_sample_map_bilinear():
    """_sample_map interpolates correctly at half-pixel positions."""
    from bpc import warp as W

    # Simple linear ramp: m[y, x] = x * 10
    m = np.zeros((10, 20), dtype=np.float32)
    for y in range(10):
        for x in range(20):
            m[y, x] = x * 10.0

    # Sample at x=5.5, y=3.0 → should be 55.0 (linear in x)
    x = np.array([[5.5]], dtype=np.float64)
    y = np.array([[3.0]], dtype=np.float64)
    result = W._sample_map(m, x, y)
    assert abs(result[0, 0] - 55.0) < 0.1, f"got {result[0, 0]}"

    # Sample at x=0 → 0, x=19 → 190
    x2 = np.array([[0.0, 19.0]], dtype=np.float64)
    y2 = np.array([[5.0, 5.0]], dtype=np.float64)
    r2 = W._sample_map(m, x2, y2)
    assert abs(r2[0, 0]) < 0.1
    assert abs(r2[0, 1] - 190.0) < 0.1


def test_settings_default_is_off():
    from bpc.config import Settings
    st = Settings()
    assert st.undistort == "off"
