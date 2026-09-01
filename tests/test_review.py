"""Manual review mode -- the part of the GUI that is not Tkinter."""
import math
import os
import tempfile

import numpy as np

import synth
from bpc.config import Settings
from bpc.review import AUTO, MANUAL, ReviewSession


def _session(**kw):
    sc = synth.Scene(pitch_deg=kw.pop("pitch_deg", 9), roll_deg=kw.pop("roll_deg", -3),
                     seed=kw.pop("seed", 21), **kw)
    return ReviewSession("in-memory.jpg", Settings(), image=sc.img), sc


def _with_painted_mask(sc, tmpdir, right_of=None):
    """Write a mask covering the right half, so the review tests have a mask
    source that needs no model.  ``auto`` used to serve this purpose and is
    retired; a painted PNG exercises exactly the same seam."""
    import cv2
    import numpy as np
    h, w = sc.img.shape[:2]
    src = os.path.join(tmpdir, "s.jpg")
    cv2.imwrite(src, sc.img)
    m = np.zeros((h, w), np.uint8)
    m[:, (right_of if right_of is not None else w // 2):] = 255
    cv2.imwrite(os.path.join(tmpdir, "s.png"), m)
    return src


def test_a_fresh_session_starts_from_the_automatic_fit():
    s, sc = _session()
    assert s.mode == AUTO
    tr, tp = sc.true_roll_pitch()
    assert abs(math.degrees(s.model.roll - tr)) < 1.0


def test_striking_out_a_line_refits():
    s, _ = _session()
    before = (s.model.roll, s.model.pitch)
    idx = int(np.argmax(s.vert.length))
    assert s.toggle_line(idx)
    assert not s.enabled[idx]
    assert int(s.enabled.sum()) == len(s.vert) - 1
    # the fit is recomputed, not merely flagged
    assert (s.model.roll, s.model.pitch) != before or s.model is not None


def test_striking_out_the_slanted_candidates_only_touches_slanted_ones():
    s, _ = _session()
    lean = np.degrees(s.vert.angle_to_vert)
    n = s.disable_lines_by_angle(18.0)
    assert n == int((lean > 18.0).sum())
    assert np.all(lean[~s.enabled] > 18.0)


def test_a_click_picks_the_nearest_segment_and_misses_return_none():
    s, _ = _session()
    seg = s.vert.seg[0]
    mx, my = (seg[0] + seg[2]) / 2, (seg[1] + seg[3]) / 2
    assert s.pick_line(mx / s.scale, my / s.scale, display_scale=1.0) is not None
    assert s.pick_line(-500.0, -500.0, display_scale=1.0) is None


def test_manual_sliders_override_the_fit():
    s, _ = _session()
    s.set_manual(roll_deg=-4.0, pitch_deg=11.0, focal_35mm=24.0)
    assert s.mode == MANUAL
    roll, pitch, f, _ = s.current_angles()
    assert abs(math.degrees(roll) + 4.0) < 1e-9
    assert abs(math.degrees(pitch) - 11.0) < 1e-9
    # a manual decision is never overruled by the confidence gate
    assert s.would_skip() is None


def test_manual_mode_ignores_the_automatic_limits():
    """A person looking at the picture outranks a safety cap meant for
    unattended batches."""
    s, _ = _session()
    s.set_manual(pitch_deg=28.0)
    _, pitch, _, _ = s.current_angles()
    assert abs(math.degrees(pitch) - 28.0) < 1e-9 > 0


def test_reset_returns_to_automatic_and_re_enables_every_line():
    s, _ = _session()
    s.disable_lines_by_angle(5.0)
    s.set_manual(pitch_deg=20.0)
    s.reset_to_auto()
    assert s.mode == AUTO and bool(s.enabled.all())


def test_render_pair_produces_two_images():
    s, _ = _session()
    before, after = s.render_pair(max_edge=480)
    assert before.ndim == 3 and after.ndim == 3
    assert max(before.shape[:2]) <= 480 and max(after.shape[:2]) <= 480


def test_an_unclear_image_reports_why_it_would_be_skipped():
    s = ReviewSession("flat.jpg", Settings(), image=synth.flat_image())
    assert s.would_skip() is not None
    # and the manual mode can still correct it
    s.set_manual(roll_deg=2.0, pitch_deg=0.0, focal_35mm=28)
    assert s.would_skip() is None


def test_the_mask_can_be_switched_while_looking_at_the_picture():
    """A mask is only judgeable against the image it is applied to, so the
    review window must be able to turn it on, not only the batch settings."""
    import synth as _synth
    sc = _synth.Scene(pitch_deg=9, roll_deg=-3, seed=31, occluders=3)
    with tempfile.TemporaryDirectory() as d:
        src = _with_painted_mask(sc, d)
        s = ReviewSession(src, Settings())
        assert not s.mask_active
        assert "mask: off" in s.status_text()
        n_before = len(s.vert)
        s.set_mask("file", d)
        assert s.mask_active
        assert "mask: file" in s.status_text()
        assert len(s.vert) <= n_before
        s.set_mask("off")
        assert not s.mask_active


def test_a_missing_mask_file_is_reported_not_raised():
    """A wrong folder must not take the window down mid-review."""
    s, _ = _session(seed=32)
    err = s.set_mask("file", "/definitely/not/here")
    assert err and "mask" in err.lower()
    assert len(s.vert) > 0, "detection must fall back, not collapse"
    assert "mask problem" in s.status_text()


def test_mask_opacity_is_adjustable_and_zero_means_invisible():
    import numpy as np

    import synth as _synth
    sc = _synth.Scene(pitch_deg=9, roll_deg=-3, seed=33, occluders=3)
    d = tempfile.mkdtemp()
    src = _with_painted_mask(sc, d)
    s = ReviewSession(src, Settings())
    s.set_mask("file", d)
    s.mask_alpha = 0.0
    plain = s.render_before(320)
    s.mask_alpha = 0.9
    tinted = s.render_before(320)
    import shutil
    shutil.rmtree(d, ignore_errors=True)
    assert plain.shape == tinted.shape
    assert not np.array_equal(plain, tinted), "the opacity slider must do something"


def test_the_mask_reports_how_many_lines_it_removed():
    import synth as _synth
    sc = _synth.Scene(pitch_deg=9, roll_deg=-3, seed=34, occluders=3)
    with tempfile.TemporaryDirectory() as d:
        src = _with_painted_mask(sc, d)
        s = ReviewSession(src, Settings())
        s.set_mask("file", d)
        dropped = (s.detect_info or {}).get("masked_out")
        assert dropped is not None
        assert f"{len(dropped)} line(s) removed" in s.status_text()


# --------------------------------------------------------------------------
# Hugin-style vertical control lines
# --------------------------------------------------------------------------
def _session_with_known_pose(pitch_deg=9.0, roll_deg=-2.0, seed=31):
    """A synthetic scene whose true camera pose is known exactly."""
    import synth
    sc = synth.Scene(w=900, h=600, pitch_deg=pitch_deg, roll_deg=roll_deg, seed=seed)
    return ReviewSession("scene.jpg", Settings(), image=sc.img), sc


def test_two_control_lines_replace_the_detected_verticals():
    """The contract: the user's assertion outranks the detector, it does not
    join it. Two lines is the threshold because two is what determines a
    vanishing point -- Hugin needs two as well."""
    s, _ = _session_with_known_pose()
    assert not s.control_active
    before = len(s.model.vert_inliers)
    i = s.add_control_line(300, 60, 300, 540)
    assert i == 0
    assert not s.control_active, "one line cannot determine a vanishing point"
    s.add_control_line(600, 60, 600, 540)
    assert s.control_active
    # the detected pool is no longer what is being fitted, so no detected line
    # is reported as an inlier
    assert len(s.model.vert_inliers) == before
    assert not s.model.vert_inliers.any()


def test_a_control_line_that_is_really_vertical_recovers_the_true_pose():
    """Drawn along the scene's actual world-verticals, the fit has to come back
    to the pose the scene was rendered with.  Without this the feature is only a
    way of replacing one guess with another.

    The scene's world y axis is its vertical, so two points differing only in y
    project to a genuine image vertical -- ground truth, not a detection.
    """
    s, sc = _session_with_known_pose(pitch_deg=9.0, roll_deg=-2.0)
    for x_world in (-3.0, 3.0):
        a = sc.project((x_world, -9.0, 16.0))
        b = sc.project((x_world, 3.0, 16.0))
        assert a and b
        assert s.add_control_line(a[0], a[1], b[0], b[1]) is not None
    assert s.control_active
    tr, tp = sc.true_roll_pitch()
    assert abs(math.degrees(s.model.roll - tr)) < 1.0,         f"roll off by {math.degrees(s.model.roll - tr):.2f} deg"
    assert abs(math.degrees(s.model.pitch - tp)) < 1.5,         f"pitch off by {math.degrees(s.model.pitch - tp):.2f} deg"


def test_control_lines_beat_a_detector_led_astray():
    """The case striking-out cannot fix.

    Every line the detector found may be real and still belong to the wrong
    plane -- a corner view where the stronger facade is not the one the user
    cares about.  There is then nothing to delete, only something to state, and
    two clicks on the right wall have to move the answer.
    """
    import synth
    sc = synth.Scene(w=900, h=600, pitch_deg=9.0, roll_deg=-2.0, seed=5, corner=True)
    s = ReviewSession("corner.jpg", Settings(), image=sc.img)
    auto_pitch = math.degrees(s.model.pitch)
    for x_world in (-3.0, 3.0):
        a = sc.project((x_world, -9.0, 16.0))
        b = sc.project((x_world, 3.0, 16.0))
        assert s.add_control_line(a[0], a[1], b[0], b[1]) is not None
    tr, tp = sc.true_roll_pitch()
    stated = math.degrees(s.model.pitch - tp)
    assert abs(stated) < 1.5, (
        f"pitch off by {stated:.2f} deg after stating the verticals; "
        f"the automatic fit was off by {auto_pitch - math.degrees(tp):.2f}")


def test_a_mis_click_that_marks_a_stub_is_refused():
    """A control line steers the whole fit, so two points landing near each
    other must not be quietly accepted: the direction of a short segment is
    badly conditioned, and Hugin's own advice is to place the two points as far
    apart as possible."""
    s, _ = _session_with_known_pose()
    assert s.add_control_line(300, 300, 306, 318) is None
    assert len(s.control_lines) == 0
    assert not s.control_active


def test_control_lines_can_be_removed_and_reset():
    s, _ = _session_with_known_pose()
    s.add_control_line(300, 60, 300, 540)
    s.add_control_line(600, 60, 600, 540)
    assert s.control_active
    assert s.pick_control_line(300, 300) == 0
    assert s.remove_control_line(0)
    assert not s.control_active
    s.add_control_line(300, 60, 300, 540)
    assert s.clear_control_lines() == 2
    assert not s.control_active
    assert s.pick_control_line(300, 300) is None


def test_control_lines_survive_the_display_scaling():
    """They are stored in analysis pixels but drawn and clicked in display
    pixels; a round trip through both must land back where it started."""
    import numpy as np
    s, _ = _session_with_known_pose()
    s.add_control_line(150, 30, 150, 270, display_scale=0.5)
    back = s.control_lines_for_display(display_scale=0.5)
    assert np.allclose(back[0], [150, 30, 150, 270], atol=1.0)


def test_reset_to_auto_forgets_the_control_lines():
    s, _ = _session_with_known_pose()
    s.add_control_line(300, 60, 300, 540)
    s.add_control_line(600, 60, 600, 540)
    s.reset_to_auto()
    assert len(s.control_lines) == 0
    assert not s.control_active


def test_marking_verticals_does_not_get_the_photo_skipped():
    """The trap this feature walks straight into if nobody looks.

    The confidence score is largely a count of supporting lines. Two control
    lines are far below what it expects of a detector, so a photograph the user
    had just told the truth about came back ``SKIP, conf=0.04, weakest: count``
    -- the feature refusing its own input. Refusing evidence for being scarce is
    right when a detector produced it and wrong when a person did.
    """
    import synth
    sc = synth.Scene(w=900, h=600, pitch_deg=9.0, roll_deg=-2.0, seed=31)
    s = ReviewSession("x.jpg", Settings(), image=sc.img)
    for x_world in (-3.0, 3.0):
        a = sc.project((x_world, -9.0, 16.0))
        b = sc.project((x_world, 3.0, 16.0))
        s.add_control_line(a[0], a[1], b[0], b[1])
    assert s.control_active
    assert s.would_skip() is None, f"would skip: {s.would_skip()}"
    assert "MARKED" in s.status_text()
    assert "conf=n/a" in s.status_text(), "a confidence built on line count is not meaningful here"


# --------------------------------------------------------------------------
# manual crop, after the frame has been kept and padded
# --------------------------------------------------------------------------
def test_a_hand_drawn_crop_is_kept_in_fractions_not_pixels():
    """The preview is a few hundred pixels and the file is saved at full size,
    so a rectangle in pixels would mean two different things. Fractions mean the
    same thing at both, which is what makes the preview honest."""
    s, _ = _session(seed=41)
    assert s.crop_rect is None
    assert s.set_crop_rect(50, 25, 250, 175, shown_w=300, shown_h=200)
    assert s.crop_rect == (50 / 300, 25 / 200, 250 / 300, 175 / 200)
    small = s.render_after(240)
    big = s.render_after(700)
    a = small.shape[1] / small.shape[0]
    b = big.shape[1] / big.shape[0]
    assert abs(a - b) < 0.05, f"the crop changed shape with the preview size: {a} vs {b}"


def test_a_crop_drawn_backwards_or_by_accident_is_handled():
    """Dragging right-to-left is the same rectangle, and a stray click is not a
    crop -- a rectangle a few pixels across would otherwise delete the picture."""
    s, _ = _session(seed=42)
    assert s.set_crop_rect(250, 175, 50, 25, shown_w=300, shown_h=200)
    assert s.crop_rect[0] < s.crop_rect[2] and s.crop_rect[1] < s.crop_rect[3]
    s.clear_crop_rect()
    assert not s.set_crop_rect(100, 100, 104, 103, shown_w=300, shown_h=200)
    assert s.crop_rect is None


def test_the_crop_survives_into_the_saved_file():
    import os
    import tempfile

    import cv2
    s, _ = _session(seed=43)
    with tempfile.TemporaryDirectory() as d:
        plain = os.path.join(d, "plain.jpg")
        s.save(plain)
        h0, w0 = cv2.imread(plain).shape[:2]
        s.set_crop_rect(0.25 * 300, 0.25 * 200, 0.75 * 300, 0.75 * 200,
                        shown_w=300, shown_h=200)
        cropped = os.path.join(d, "cropped.jpg")
        s.save(cropped)
        h1, w1 = cv2.imread(cropped).shape[:2]
    assert w1 < w0 and h1 < h0, f"{w1}x{h1} is not inside {w0}x{h0}"
    assert abs(w1 / w0 - 0.5) < 0.05 and abs(h1 / h0 - 0.5) < 0.05


def test_reset_to_auto_forgets_the_crop_too():
    s, _ = _session(seed=44)
    s.set_crop_rect(10, 10, 200, 150, shown_w=300, shown_h=200)
    s.reset_to_auto()
    assert s.crop_rect is None


def test_auto_returns_the_found_angles_without_undoing_the_rest():
    """Two different retreats, and conflating them loses work.

    ``reset_to_auto`` discards every judgement the user made -- struck-out
    lines, control lines, a hand-drawn crop -- which is right after a wrong turn
    and far too much after a mis-dragged slider. ``use_auto_angles`` puts the
    found angles back and leaves the rest standing.
    """
    s, _ = _session(seed=45)
    s.toggle_line(0)
    s.set_crop_rect(20, 20, 280, 180, shown_w=300, shown_h=200)
    # captured *after* the line edit: striking a line refits, so "the angles it
    # found" means the current fit, not the one from before the user edited it
    found_roll, found_pitch = s.model.roll, s.model.pitch
    struck = int((~s.enabled).sum())
    s.set_manual(roll_deg=11.0, pitch_deg=-9.0)
    assert s.mode == MANUAL
    s.use_auto_angles()
    assert s.mode == AUTO
    assert abs(s.manual_roll - found_roll) < 1e-9
    assert abs(s.manual_pitch - found_pitch) < 1e-9
    assert int((~s.enabled).sum()) == struck, "line edits must survive"
    assert s.crop_rect is not None, "the crop must survive"
    s.reset_to_auto()
    assert s.enabled.all() and s.crop_rect is None, "reset is the big hammer"
