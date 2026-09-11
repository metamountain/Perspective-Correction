"""Planar rectification: homography from four points, target size, degeneracy."""
import numpy as np

from bpc import planar as P


def _quad(w=400.0, h=300.0):
    """A simple axis-aligned quad at the origin (TL, TR, BR, BL)."""
    return np.array([[0.0, 0.0], [w, 0.0], [w, h], [0.0, h]], dtype=np.float64)


def test_corner_mapping_is_exact():
    """transform_for sends the four quad corners onto the rectangle corners."""
    q = _quad(400, 300)
    H, w, h = P.transform_for(q)
    dst = np.array([[0.0, 0.0], [w - 1, 0.0], [w - 1, h - 1], [0.0, h - 1]])
    for src_pt, want in zip(q, dst):
        got = H @ np.append(src_pt, 1.0)
        got = got[:2] / got[2]
        assert np.allclose(got, want, atol=1e-6), f"{src_pt} -> {got}, want {want}"


def test_full_coverage_no_fill_band():
    """Every pixel of the output canvas comes from inside the quad -- no fill band.

    This is the property that separates planar rectification from the rotation
    path: there, a rotation opens a band nobody photographed and something has
    to invent it; here the canvas is exactly the quad, so there is nothing to
    fill.  It is asserted the way ``warpPerspective`` actually resamples -- each
    *output* corner is sent through ``H^-1`` and must land on the corresponding
    *source* corner.  Pushing the canvas corners through the forward ``H``
    instead would only be meaningful if ``H`` were its own inverse.
    """
    q = _quad(400, 300)
    H, w, h = P.transform_for(q)
    Hinv = np.linalg.inv(H)
    corners = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float64)
    for c, want in zip(corners, q):
        pt = Hinv @ np.append(c, 1.0)
        pt = pt[:2] / pt[2]
        assert np.allclose(pt, want, atol=1e-3), f"{c} <- {pt}, want {want}"


def test_target_size_preserves_aspect():
    """A wider quad yields a wider output; a taller quad yields a taller one."""
    wide = _quad(600, 200)
    tall = _quad(200, 600)
    w1, h1 = P.target_size(wide)
    w2, h2 = P.target_size(tall)
    assert w1 > h1
    assert w2 < h2
    # Aspect ratio is preserved (within rounding).
    assert abs(w1 / h1 - 600 / 200) < 0.05
    assert abs(w2 / h2 - 200 / 600) < 0.05


def test_degenerate_quad_raises():
    """Collinear (zero-area) quads are rejected, not warped into a sliver."""
    collinear = np.array([[0.0, 0.0], [100.0, 0.0], [200.0, 0.0], [300.0, 0.0]])
    try:
        P.transform_for(collinear)
        assert False, "expected ValueError for collinear quad"
    except ValueError as e:
        assert "degenerate" in str(e).lower()


def test_quad_area_known_value():
    """Unit square has twice-signed-area 2 (shoelace); reversed order flips sign."""
    sq = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
    assert abs(P.quad_area(sq) - 2.0) < 1e-12
    # Reversed winding flips the sign.
    sq_rev = sq[::-1]
    assert abs(P.quad_area(sq_rev) + 2.0) < 1e-12


def test_homography_shape_validation():
    """Wrong point-array shapes are rejected with a clear message."""
    try:
        P.homography_from_quad(np.zeros((3, 2)), np.zeros((4, 2)))
        assert False, "expected ValueError for (3,2) src"
    except ValueError as e:
        assert "(4, 2)" in str(e)


def test_roundtrip_distort_then_rectify():
    """Distort a square with a known homography, then rectify it back."""
    # Start with a 100x100 square.
    sq = np.array([[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 100.0]])
    # A perspective distortion (not a pure affine — has shear + scale).
    distorted = np.array([[20.0, 15.0], [120.0, 5.0], [130.0, 95.0], [10.0, 105.0]])
    H_distort = P.homography_from_quad(sq, distorted)

    # Now rectify: map the distorted quad back to a clean rectangle.
    H_rect, w, h = P.transform_for(distorted)
    H_total = H_rect @ H_distort

    # The total transform should be (approximately) a similarity:
    # it maps the original square to an axis-aligned rectangle.
    for src_pt, want in zip(sq, [[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]]):
        got = H_total @ np.append(src_pt, 1.0)
        got = got[:2] / got[2]
        assert np.allclose(got, want, atol=1e-4), f"{src_pt} -> {got}, want {want}"
