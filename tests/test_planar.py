"""Planar rectification: homography from four points, target size, degeneracy."""
import numpy as np

from pc import planar as P


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
from pc.config import Settings
from pc.review import ReviewSession


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


def _box_faces(rng, n, iw=3000, ih=2000):
    """Projected faces of random boxes: ``(ordered quad, true w/h, focal px)``.

    Ground truth from the 3D corners each screen corner came from, so a face
    seen mirrored or sideways is scored against the edges it really shows.
    """
    c = (iw / 2.0, ih / 2.0)
    out = []
    while len(out) < n:
        W, H, D = rng.uniform(0.5, 3, 3)
        f = rng.uniform(0.35, 2.0) * iw
        y, p, r = np.radians([rng.uniform(-60, 60), rng.uniform(-25, 25), rng.uniform(-5, 5)])
        Ry = np.array([[np.cos(y), 0, np.sin(y)], [0, 1, 0], [-np.sin(y), 0, np.cos(y)]])
        Rx = np.array([[1, 0, 0], [0, np.cos(p), -np.sin(p)], [0, np.sin(p), np.cos(p)]])
        Rz = np.array([[np.cos(r), -np.sin(r), 0], [np.sin(r), np.cos(r), 0], [0, 0, 1]])
        R = Rz @ Rx @ Ry
        x0, x1, y0, y1, z0, z1 = -W / 2, W / 2, -H / 2, H / 2, -D / 2, D / 2
        for pts in ([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0]],
                    [[x1, y0, z0], [x1, y0, z1], [x1, y1, z1], [x1, y1, z0]]):
            pts = np.array(pts, dtype=np.float64)
            cam = (R @ pts.T).T + [0, 0, rng.uniform(4, 9)]
            q = cam[:, :2] / cam[:, 2:] * f + c
            if (q[:, 0] < 0).any() or (q[:, 0] > iw).any() or (q[:, 1] < 0).any() \
                    or (q[:, 1] > ih).any() or abs(P.quad_area(q)) < 0.01 * iw * ih:
                continue
            q = q + rng.normal(0, 1.5, q.shape)
            qo = P.order_quad(q)
            idx = [int(np.argmin(((q - k) ** 2).sum(1))) for k in qo]
            if len(set(idx)) != 4:
                continue
            true = (np.linalg.norm(pts[idx[1]] - pts[idx[0]])
                    / np.linalg.norm(pts[idx[3]] - pts[idx[0]]))
            out.append((qo, true, f))
    return out[:n], c, iw


def test_the_rectangle_comes_out_in_its_true_proportions():
    """A facade seen at an angle must not come out squashed (user, 2026-10-23:
    "verhaeltnisse von rectangle in der tat sehr gestaucht"). Over synthetic
    box faces with click noise and an estimator focal off by up to 30 %, the
    apparent edges erred ~84 % on average; the true-aspect size must stay far
    below that."""
    rng = np.random.default_rng(11)
    faces, c, iw = _box_faces(rng, 400)
    old, new = [], []
    for qo, true, f in faces:
        w0, h0 = P.target_size(qo)
        w1, h1 = P.target_size(qo, center=c, focal=f * rng.uniform(0.7, 1.3), long_edge=iw,
                               foreshorten=False)
        old.append(abs(w0 / h0 / true - 1))
        new.append(abs(w1 / h1 / true - 1))
    assert np.median(old) > 0.3, f"harness lost its teeth: old median {np.median(old):.3f}"
    assert np.median(new) < 0.08, f"true-aspect median error {np.median(new):.3f}"
    assert np.percentile(new, 90) < 0.25, f"p90 {np.percentile(new, 90):.3f}"


def test_the_true_aspect_never_shrinks_the_canvas():
    """Proportions are reached by growing one side: the warp still adds pixels."""
    rng = np.random.default_rng(3)
    faces, c, iw = _box_faces(rng, 100)
    for qo, _true, f in faces:
        w0, h0 = P.target_size(qo)
        w1, h1 = P.target_size(qo, center=c, focal=f, long_edge=iw)
        assert w1 >= w0 - 1 and h1 >= h0 - 1, ((w0, h0), (w1, h1))


def test_the_margin_keeps_twenty_percent_around_the_quad():
    """`transform_for(margin=...)` insets the rectangle; the quad's corners land
    exactly that far in from the canvas edge (user: "rechteck modus crop +20%")."""
    q = _quad(400.0, 300.0)
    H, w, h = P.transform_for(q, margin=P.CROP_MARGIN)
    assert (w, h) == (400 + 2 * 80, 300 + 2 * 60), (w, h)
    tl = H @ np.array([0.0, 0.0, 1.0])
    assert np.allclose(tl[:2] / tl[2], [80.0, 60.0], atol=1e-6)


def test_the_compression_is_horizontal_autos_when_asked_for():
    """``foreshorten=True`` gives horizontal auto's sqrt(cos yaw): a 2:1
    facade, level camera, yaw 30/45/60 comes out 2 * sqrt(cos yaw). The
    rectangle tool does not ask for it (squares stay squares)."""
    import math
    from pc import warp as W
    iw, ih, f = 3000, 2000, 2400.0
    c = (iw / 2.0, ih / 2.0)
    for yaw_deg in (30.0, 45.0, 60.0):
        y = math.radians(yaw_deg)
        R = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
        pts = np.array([[-1, -0.5, 0], [1, -0.5, 0], [1, 0.5, 0], [-1, 0.5, 0]], dtype=float)
        cam = (R @ pts.T).T + [0, 0, 6.0]
        q = P.order_quad(cam[:, :2] / cam[:, 2:] * f + c)
        w, h = P.target_size(q, center=c, focal=f, long_edge=iw, foreshorten=True)
        want = 2.0 * W.yaw_x_scale(y)
        assert abs(w / h / want - 1) < 0.02, (yaw_deg, w / h, want)


def test_lines_and_rectangle_are_either_or_and_each_comes_back():
    """User, 2026-10-23: lines or rectangle, never both in force; switching
    back restores what was there."""
    s = _session()
    s.add_control_line(10, 10, 12, 200, display_scale=1.0, kind="v")
    lines_before = s.control_lines.copy()
    assert s.set_facade_mode("rect") is True
    assert len(s.control_lines) == 0 and len(s.control_hlines) == 0
    for i, (x, y) in enumerate(_quad_in(s)):
        s.set_planar_point(i, x, y)
    quad = list(s.planar_quad)
    assert s.planar_is_set()
    assert s.set_facade_mode("lines") is True
    assert s.planar_quad == [] and not s.planar_is_set()
    assert np.array_equal(s.control_lines, lines_before)
    assert s.set_facade_mode("lines") is False
    s.set_facade_mode("rect")
    assert s.planar_quad == quad and len(s.control_lines) == 0


def test_the_canvas_keeps_room_around_the_quad_but_never_past_the_photograph():
    """User, 2026-10-23: "keine pixel verloren, oder nur moderat". The canvas
    reaches KEEP_MARGIN beyond the quad, cut back to where the photograph
    ends; the box (the default crop) is exactly the old quad + CROP_MARGIN."""
    w0, h0 = 2000, 1500
    quad = np.array([(700, 500), (1300, 540), (1290, 1000), (710, 1040)], float)
    H_old, ow, oh = P.transform_for(quad, margin=P.CROP_MARGIN, long_edge=2000)
    H, cw, ch, box = P.canvas_for(quad, w0, h0, long_edge=2000)
    assert cw > ow * 1.3 and ch > oh * 1.3, "no room was kept around the quad"
    assert abs((box[2] - box[0] + 1) - ow) <= 2 and abs((box[3] - box[1] + 1) - oh) <= 2
    # The quad corners land where transform_for put them, shifted by the box.
    a = P.cv2.perspectiveTransform(quad.reshape(-1, 1, 2), H).reshape(-1, 2)
    b = P.cv2.perspectiveTransform(quad.reshape(-1, 1, 2), H_old).reshape(-1, 2)
    assert np.abs(a - (b + [box[0], box[1]])).max() < 1.5
    # A quad at the photograph's edge: no canvas beyond where the photo ends.
    edge = np.array([(0, 0), (900, 30), (890, 700), (0, 720)], float)
    He, ew, eh, ebox = P.canvas_for(edge, w0, h0, long_edge=2000)
    # The photograph ends at the quad's left and top edge, so the canvas
    # starts at the crop box there instead of KEEP_MARGIN further out.
    assert ebox[0] < 1.0 and ebox[1] < 1.0, "canvas runs past the photograph"
    # Centred on the quad (user, 2026-10-23: "roi centered"): the narrower
    # side's room on both sides, here none beyond the crop box.
    for B, cw_, ch_ in ((box, cw, ch), (ebox, ew, eh)):
        assert abs(B[0] - (cw_ - 1 - B[2])) <= 2 and abs(B[1] - (ch_ - 1 - B[3])) <= 2, \
            ("the quad is not centred on its canvas", B, cw_, ch_)


def test_the_crop_goes_with_its_mode_and_the_rectangle_starts_at_quad_plus_20():
    """User, 2026-10-23: the saved rectangle kept 24 % because the line mode's
    crop came along. Each mode keeps its own crop."""
    s = _session()
    s.set_crop_rect(10, 10, 90, 90, 100, 100)
    lines_crop = s.crop_rect
    s.set_facade_mode("rect")
    assert s.crop_rect is None
    for i, (x, y) in enumerate(_quad_in(s)):
        s.set_planar_point(i, x, y)
    rect_crop = s.crop_rect
    H, cw, ch = s.planar_homography()
    x0, y0, x1, y1 = s._planar_box
    assert rect_crop is not None and s.crop_is_auto
    assert abs(rect_crop[0] - x0 / cw) < 1e-6 and abs(rect_crop[2] - (x1 + 1) / cw) < 1e-6
    s.set_facade_mode("lines")
    assert s.crop_rect == lines_crop
    s.set_facade_mode("rect")
    assert s.crop_rect == rect_crop


def test_the_saved_rectangle_is_the_cropped_preview():
    s = _session()
    s.set_facade_mode("rect")
    for i, (x, y) in enumerate(_quad_in(s)):
        s.set_planar_point(i, x, y)
    prev = s.render_after(max_edge=600, fill="none", apply_crop=True)
    with tempfile.TemporaryDirectory() as d:
        out = s.save_planar(os.path.join(d, "r.png"))
        sv = P.cv2.imread(out)
    assert abs(prev.shape[1] / prev.shape[0] - sv.shape[1] / sv.shape[0]) < 0.02
    small = P.cv2.resize(sv, (prev.shape[1], prev.shape[0]), interpolation=P.cv2.INTER_AREA)
    assert np.abs(small.astype(float) - prev).mean() < 12


def test_squares_come_out_square_on_a_rendered_rubik_cube():
    """User, 2026-10-23: "es muessen nach Entzerrung wieder Quadrate
    rauskommen". Both faces of a rendered 4x4 cube, the quad on the face's
    true corners, through the session (no EXIF, so the estimator's focal):
    the cells come out square within the focal estimate's error, and exactly
    square with the true focal."""
    for yaw, pitch, mm in ((25, 8, 24), (45, 12, 28), (55, 8, 20)):
        img, f, faces = synth.rubik_cube(yaw, pitch, mm)
        s = ReviewSession("cube.png", Settings(), image=img)
        c = (img.shape[1] / 2.0, img.shape[0] / 2.0)
        for name, (corners, grid) in faces.items():
            w, h = P.target_size(corners, center=c, focal=f, long_edge=max(img.shape[:2]))
            Ht = P.homography_from_quad(P.order_quad(corners), np.array(
                [[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], float))
            assert abs(synth.cell_aspect(Ht, grid) - 1) < 0.01, (yaw, name)
            s.planar_quad = [tuple(p) for p in corners]
            H, _cw, _ch = s.planar_homography()
            a = synth.cell_aspect(H, grid)
            assert abs(a - 1) < 0.08, (yaw, pitch, mm, name, a)


def test_squares_come_out_square_on_building_sized_cubes_from_eye_height():
    """User, 2026-10-23: "20x20 m, du 1,6 hoch, mehrere Wuerfel, gedreht".
    Two corner buildings of 5 m squares, camera at eye height tilted up 14 deg,
    24 mm, no EXIF: every fully framed face comes out square within 3 %."""
    img, f, faces = synth.rubik_street([(-14, 40, 25), (22, 55, -20)], 14, 24)
    assert len(faces) >= 3
    s = ReviewSession("street.png", Settings(), image=img)
    for name, (corners, grid) in faces.items():
        s.planar_quad = [tuple(p) for p in corners]
        H, _cw, _ch = s.planar_homography()
        a = synth.cell_aspect(H, grid)
        assert abs(a - 1) < 0.03, (name, a)


def test_horizontal_auto_turns_a_chosen_facade_into_squares_even_when_it_folds():
    """User, 2026-10-23: horizontal auto must give squares too, "auch auf
    stark verkuerzten", and a warp that folds is to be controlled, not thrown
    away. Building-sized cubes from eye height; the facade strip on one face.
    cube1_left is seen so obliquely that its vanishing line crosses the frame
    (the whole-frame warp folds), cube1_front is the ordinary case."""
    import math
    img, f, faces = synth.rubik_street([(-14, 40, 25), (22, 55, -20)], 14, 24)
    h, w = img.shape[:2]
    for name in ("cube1_left", "cube1_front", "cube0_right"):
        corners, grid = faces[name]
        s = ReviewSession("street.png", Settings().replace(correct_horizontal=True), image=img)
        s.strip = (corners[:, 0].min() / w, corners[:, 0].max() / w)
        s.refit()
        assert s._rectify_active(), name
        s.render_after(max_edge=2400, fill="none", apply_crop=False)
        assert s.preview_map is not None and s.preview_map[0] == "warp",             (name, "the warp was thrown away")
        kind, sc, Ht, fit = s.preview_map
        M = np.diag([fit, fit, 1.0]) @ Ht @ np.diag([sc, sc, 1.0])
        g = P.cv2.perspectiveTransform(grid.reshape(-1, 1, 2), M).reshape(grid.shape)
        for r in g:
            assert abs(math.degrees(math.atan2(r[-1, 1] - r[0, 1], r[-1, 0] - r[0, 0]))) < 0.3, name
        for c in g.transpose(1, 0, 2):
            assert abs(math.degrees(math.atan2(c[-1, 0] - c[0, 0], c[-1, 1] - c[0, 1]))) < 0.3, name
        assert abs(synth.cell_aspect(M, grid) - 1) < 0.02, (name, synth.cell_aspect(M, grid))
        # and Save writes what the preview showed instead of refusing
        with tempfile.TemporaryDirectory() as d:
            out = P.cv2.imread(s.save(os.path.join(d, "ha.png")))
        prev = s.render_after(max_edge=2400, fill="none", apply_crop=True)
        assert abs(out.shape[1] / out.shape[0] - prev.shape[1] / prev.shape[0]) < 0.02, name


def test_a_wall_running_into_its_vanishing_point_is_cropped_in_time_not_refused():
    """User, 2026-10-23: "fliegt einem um die Ohren ist ja klar. man muss dann
    rechtzeitig croppen, darf nicht abschmieren". Without a facade strip the
    horizontals of this street reach almost to their vanishing point (x = -87
    of 2400), so a quad drawn over all of them is foreshortened without bound
    and comes out degenerate -- the warp was refused. The quad now stops where
    the wall is foreshortened FOLD_MAX_FORESHORTEN times its far end, and the
    result is a bounded frontal view."""
    img, f, faces = synth.rubik_street([(-25, 35, 45), (25, 35, -45), (0, 70, 10)], 16, 18)
    h, w = img.shape[:2]
    s = ReviewSession("wide.png", Settings().replace(correct_horizontal=True), image=img)
    s.refit()
    assert s.strip is None
    assert s.ha_fold_quad() is not None, "the folding warp was refused instead of framed"
    s.render_after(max_edge=1200, fill="none", apply_crop=False)
    assert s.preview_map is not None, "no preview for the folding warp"
    ow, oh = s.output_size()
    assert (ow, oh) != (w, h), "Save would copy the original through"
    assert ow * oh <= 16 * w * h, (ow, oh)


def test_a_close_steep_facade_is_framed_by_horizontal_auto_not_cut_off():
    """User, 2026-10-23: "close steep ... versagt noch HA ... rechte fail".
    16 mm, 22 deg up, one cube close by: the whole-frame plan succeeded and
    showed only sky and street beside the right face. With a strip the facade
    must lie inside the canvas, squares square, centred."""
    import math
    img, f, faces = synth.rubik_street([(0, 28, 35)], 22, 16)
    h, w = img.shape[:2]
    for name in ("cube0_right", "cube0_front"):
        corners, grid = faces[name]
        s = ReviewSession("close.png", Settings().replace(correct_horizontal=True), image=img)
        s.strip = (corners[:, 0].min() / w, corners[:, 0].max() / w)
        s.refit()
        out = s.render_after(max_edge=1200, fill="none", apply_crop=False)
        kind, sc, Ht, fit = s.preview_map
        M = np.diag([fit, fit, 1.0]) @ Ht @ np.diag([sc, sc, 1.0])
        g = P.cv2.perspectiveTransform(grid.reshape(-1, 1, 2), M).reshape(-1, 2)
        oh, ow = out.shape[:2]
        assert g[:, 0].min() >= 0 and g[:, 1].min() >= 0 \
            and g[:, 0].max() <= ow and g[:, 1].max() <= oh, (name, "facade cut off", g.min(0), g.max(0), (ow, oh))
        assert abs(synth.cell_aspect(M, grid.reshape(faces[name][1].shape)) - 1) < 0.02, name


def _crop_on_far_tele(flag):
    """Auto crop under horizontal auto on a 50 mm street of two cubes far off
    (lots of sky and street): returns (crop share, sky share in the crop,
    cube pixels kept)."""
    img, f, faces = synth.rubik_street([(0, 120, 30), (35, 140, -10)], 6, 50)
    old = ReviewSession.CROP_TO_INTEREST
    ReviewSession.CROP_TO_INTEREST = flag
    try:
        s = ReviewSession("tele.png", Settings().replace(correct_horizontal=True, fill="telea"), image=img)
        s.refit()
        s.refresh_auto_crop()
        raw = s.render_after(max_edge=1200, fill="none", apply_crop=False)
    finally:
        ReviewSession.CROP_TO_INTEREST = old
    hh, ww = raw.shape[:2]
    r = s.crop_rect or (0, 0, 1, 1)
    m = np.zeros((hh, ww), bool)
    m[int(r[1] * hh):int(r[3] * hh), int(r[0] * ww):int(r[2] * ww)] = True
    px = raw.astype(int)
    sky = np.all(np.abs(px - (215, 200, 185)) < 6, 2)
    gnd = np.all(np.abs(px - (95, 95, 100)) < 6, 2)
    cube = ~(sky | gnd | (raw.sum(2) == 0))
    return m.mean(), (sky & m).sum() / m.sum(), (cube & m).sum() / max(1, cube.sum())


def test_the_auto_crop_frames_where_the_lines_are_and_leaves_the_sky_out():
    """User, 2026-10-23: "center of interest per LSD-Dichte ... Vorteil Himmel
    fliegt raus". With a yaw and no strip, the auto crop is cut to the
    length-weighted 5-95 % box of the warped line segments (+10 %). Measured:
    crop 0.92 -> 0.28 of the canvas, sky in it 0.47 -> 0.32, cubes kept 0.99."""
    share0, sky0, kept0 = _crop_on_far_tele(False)
    share1, sky1, kept1 = _crop_on_far_tele(True)
    assert kept1 >= 0.95, ("the building was cropped away", kept1)
    assert share1 < 0.6 * share0, ("the crop did not tighten", share0, share1)
    assert sky1 < sky0 - 0.08, ("the sky stayed in", sky0, sky1)


def test_a_soft_horizontal_auto_crops_around_the_middle_of_the_outline():
    """User, 2026-10-24: "bei soften HA-Korrekturen will ich Auto-Crop auf
    Mittelpunkt Outline (Trapez)". Up to SOFT_YAW_DEG the auto crop is the
    largest rectangle centred on the area centroid of the warped outline
    (clipped to the canvas); above it the centre-of-interest crop stays.
    Auto (no yaw) is centred the same way ("bei Auto soll das auch so sein")."""
    import math
    from pc import warp as W
    for yaw, soft, ha in ((10, True, True), (25, False, True), (25, True, False)):
        img, _, _ = synth.rubik_street([(0, 60, 0), (-30, 70, 0)], 10, 28, yaw_deg=yaw,
                                       w=1600, h=1067)
        s = ReviewSession("street.png", Settings().replace(correct_horizontal=ha, fill="none"),
                          image=img)
        s.refit()
        assert s.active_quad() is None
        assert (math.degrees(abs(s.current_yaw())) <= s.SOFT_YAW_DEG) == soft
        roll, pitch, f, _ = s.current_angles()
        H = s._build_H(s.w, s.h, f, roll, pitch, s.current_yaw(),
                       max_area=s.settings.max_area_ratio)
        Ht, ow, oh, _, _ = W.plan(s.w, s.h, H, s.settings, line_segs=s._plan_segs(s.w),
                                  yaw=s.current_yaw(), strip=None)
        c = W.poly_centroid(W.clip_to_box(W.warped_quad(Ht, s.w, s.h), 0, 0, ow, oh))
        assert s.auto_crop()
        x0, y0, x1, y1 = s.crop_rect
        mid = ((x0 + x1) / 2 * ow, (y0 + y1) / 2 * oh)
        off = max(abs(mid[0] - c[0]) / ow, abs(mid[1] - c[1]) / oh)
        if soft:
            assert off < 0.005, ("soft crop not on the outline's middle", yaw, mid, c)
        else:
            assert off > 0.05, ("a strong correction lost its interest crop", yaw, mid, c)


def test_the_plan_gets_its_line_segments_in_its_own_pixels():
    """W.plan(line_segs=...) reads segments in the plan's pixels; they were
    passed in analysis pixels, so on a photograph wider than detect_max_edge
    the preview and the full-size canvas were framed on different facades.
    The preview's canvas must be the full one, scaled."""
    img, f, faces = synth.rubik_street([(-14, 40, 25), (22, 55, -20)], 14, 24)
    s = ReviewSession("street.png", Settings().replace(correct_horizontal=True), image=img)
    s.refit()
    assert s.gray.shape[1] < s.w, "scene must be downscaled for analysis"
    ow, oh = s.output_size()
    out = s.render_after(max_edge=1200, fill="none", apply_crop=False)
    assert abs(out.shape[1] / out.shape[0] - ow / oh) < 0.02, (out.shape, (ow, oh))
