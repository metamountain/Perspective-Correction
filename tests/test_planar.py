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


# --------------------------------------------------------------------------
# P2: the ReviewSession layer the GUI actually calls
#
# `planar.py` above is the math; these are the seven methods `gui.py` reaches
# through. They had no test at all, which is how the whole feature shipped
# broken -- the GUI shell existed, the math raised on every call, and the two
# stale claims in CLAUDE.md cancelled out. They are pure functions of session
# state, so there was never a reason not to test them.
# --------------------------------------------------------------------------
import os
import tempfile

import synth
from bpc.config import Settings
from bpc.review import ReviewSession


def _session():
    sc = synth.Scene(pitch_deg=9, roll_deg=-3, seed=21)
    return ReviewSession("in-memory.jpg", Settings(), image=sc.img)


def _quad_in(s, inset=0.2):
    """A convex quad well inside the frame, in full-resolution pixels."""
    w, h = float(s.w), float(s.h)
    dx, dy = w * inset, h * inset
    return [(dx, dy), (w - dx, dy * 1.3), (w - dx * 1.2, h - dy), (dx * 1.1, h - dy * 1.1)]


def test_corners_can_be_placed_out_of_order_and_moved():
    s = _session()
    assert s.planar_quad == []
    s.set_planar_point(2, 10.0, 20.0)          # a gap is filled, not an IndexError
    assert len(s.planar_quad) == 3
    assert s.planar_quad[2] == (10.0, 20.0)
    s.set_planar_point(2, 11.0, 21.0)          # moving replaces, never appends
    assert len(s.planar_quad) == 3 and s.planar_quad[2] == (11.0, 21.0)
    s.clear_planar()
    assert s.planar_quad == []


def test_the_corner_grab_radius_is_screen_pixels_not_image_pixels():
    """The whole point of `display_scale`: the target stays the same size
    under the cursor however far the photograph is zoomed out. At scale 0.5 a
    14 px screen radius must reach 28 px into the image."""
    s = _session()
    s.set_planar_point(0, 100.0, 100.0)
    assert s.pick_planar_corner(100.0, 100.0, 1.0) == 0          # dead on
    assert s.pick_planar_corner(120.0, 100.0, 1.0) is None        # 20 px > 14
    assert s.pick_planar_corner(120.0, 100.0, 0.5) == 0           # 20 px < 28
    # and a click nowhere near anything is a miss at any zoom
    assert s.pick_planar_corner(1.0, 1.0, 0.25) is None


def test_no_homography_until_all_four_corners_are_placed():
    """Three points do not determine a plane; returning something for them
    would warp the photograph on a guess."""
    s = _session()
    for i, (x, y) in enumerate(_quad_in(s)[:3]):
        s.set_planar_point(i, x, y)
        assert s.planar_homography() is None
        assert s.planar_rectified() is None
    s.set_planar_point(3, *_quad_in(s)[3])
    t = s.planar_homography()
    assert t is not None
    H, w, h = t
    assert H.shape == (3, 3) and w > 1 and h > 1


def test_the_rectified_output_is_exactly_the_canvas_the_homography_asked_for():
    s = _session()
    for i, (x, y) in enumerate(_quad_in(s)):
        s.set_planar_point(i, x, y)
    _H, w, h = s.planar_homography()
    out = s.planar_rectified()
    assert out is not None
    assert (out.shape[1], out.shape[0]) == (w, h), (
        f"canvas {w}x{h} but got {out.shape[1]}x{out.shape[0]}")


def test_the_preview_is_the_same_picture_as_the_full_size_warp():
    """`max_edge` warps a reduced copy, which the docstring calls exact up to
    the preview's own resolution. If that is wrong the corner drag shows one
    thing and the saved file is another -- the failure the crop section of
    CLAUDE.md is built around."""
    import cv2
    import numpy as np
    s = _session()
    for i, (x, y) in enumerate(_quad_in(s)):
        s.set_planar_point(i, x, y)
    full = s.planar_rectified()
    small = s.planar_rectified(max_edge=160)
    assert max(small.shape[:2]) <= 160
    assert max(small.shape[:2]) < max(full.shape[:2])
    ref = cv2.resize(full, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_AREA)
    diff = float(np.mean(np.abs(small.astype(np.float32) - ref.astype(np.float32))))
    assert diff < 12.0, f"preview differs from the full warp by {diff:.1f} grey levels"


def test_saving_needs_four_corners_and_writes_the_rectified_view():
    s = _session()
    with tempfile.TemporaryDirectory() as d:
        dst = os.path.join(d, "sub", "flat.jpg")
        try:
            s.save_planar(dst)
            raise AssertionError("saved a planar view with no corners")
        except ValueError:
            pass
        for i, (x, y) in enumerate(_quad_in(s)):
            s.set_planar_point(i, x, y)
        out = s.save_planar(dst)                 # also creates the subfolder
        assert os.path.exists(out) and os.path.getsize(out) > 0


def test_a_degenerate_quad_is_refused_at_the_session_layer_too():
    """Four collinear clicks must raise rather than flatten the photograph
    into a sliver. `transform_for` enforces it; this pins that the session
    does not swallow the error on its way to the GUI."""
    s = _session()
    for i in range(4):
        s.set_planar_point(i, 10.0 + 30.0 * i, 10.0 + 30.0 * i)
    try:
        s.planar_homography()
        raise AssertionError("a collinear quad produced a homography")
    except ValueError:
        pass
