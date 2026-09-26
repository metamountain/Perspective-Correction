"""Does the estimator recover a camera pose it was never told?

Accuracy targets come from measurement, not from wishes -- see
docs/accuracy.md for the numbers these thresholds are drawn from.
"""
import math

import numpy as np

import synth
from pc.config import Settings
from pc.pipeline import analyse


def _err(scene, settings):
    m, vert, horiz, scale, _ = analyse(scene.img, settings)
    tr, tp = scene.true_roll_pitch()
    return (math.degrees(m.roll - tr), math.degrees(m.pitch - tp), m)


def test_roll_is_recovered_precisely_and_does_not_need_the_focal_length():
    """Levelling is the half of the job that is exactly solvable: roll is
    ``atan2(u_x, -u_y)`` and both components carry a factor 1/f that cancels.
    It must therefore be right even when the focal length is unknown."""
    worst = 0.0
    for f35 in (18, 28, 50):
        for roll in (-8, -3, 0, 2.5, 6):
            sc = synth.Scene(focal_35mm=f35, pitch_deg=5, roll_deg=roll, seed=11)
            dr, _, _ = _err(sc, Settings())          # no focal length supplied
            worst = max(worst, abs(dr))
    assert worst < 0.5, f"roll error {worst:.3f} deg"


def test_pitch_is_recovered_when_the_focal_length_is_known():
    worst = 0.0
    for f35 in (18, 24, 28, 35, 50):
        for pitch in (3, 6, 10, 14):
            sc = synth.Scene(focal_35mm=f35, pitch_deg=pitch, roll_deg=1.5, seed=5)
            _, dp, _ = _err(sc, Settings().replace(focal_35mm=f35))
            worst = max(worst, abs(dp))
    assert worst < 2.0, f"pitch error {worst:.3f} deg"


def test_a_level_camera_is_recognised_as_level():
    sc = synth.Scene(pitch_deg=0, roll_deg=0, seed=2)
    dr, dp, m = _err(sc, Settings())
    assert abs(dr) < 0.2 and abs(dp) < 0.3
    total = math.degrees(math.hypot(m.roll, m.pitch))
    assert total < Settings().min_correction_deg * 4


def test_texture_without_lines_yields_no_confidence():
    """A photo with no straight edges must not be corrected on the strength of
    whatever the detector scraped together."""
    img = synth.flat_image()
    m, _, _, _, _ = analyse(img, Settings())
    assert m.confidence < Settings().min_confidence


def test_estimation_is_deterministic():
    """Two runs on one file must give one answer.  The reference RANSAC uses
    the global RNG and returned vanishing points 3 700 to 47 000 px apart across
    five identical runs of the same image."""
    sc = synth.Scene(pitch_deg=7, roll_deg=-2, seed=4)
    a = analyse(sc.img, Settings())[0]
    b = analyse(sc.img, Settings())[0]
    assert a.roll == b.roll and a.pitch == b.pitch and a.f == b.f


def test_confidence_falls_when_the_evidence_is_local():
    """A vanishing point voted for by one corner of the frame is not evidence
    about the camera, and must score lower than one supported across it."""
    full = synth.Scene(pitch_deg=9, roll_deg=1, seed=6)
    m_full = analyse(full.img, Settings())[0]
    cropped = full.img.copy()
    cropped[:, : int(full.w * 0.62)] = 235          # keep only a narrow strip
    m_part = analyse(cropped, Settings())[0]
    assert m_part.confidence < m_full.confidence


def test_gable_diagonals_do_not_become_the_vertical_direction():
    """The roof is the classic false positive: two long, strong, converging
    lines that a dominant-direction search happily calls the answer."""
    sc = synth.Scene(pitch_deg=6, roll_deg=0, seed=8)
    _, dp, m = _err(sc, Settings().replace(focal_35mm=28))
    assert abs(dp) < 2.0
    assert abs(math.degrees(m.roll)) < 2.0


def test_half_timbered_bracing_does_not_capture_the_vertical_direction():
    """Half-timbered facades are the adversarial case, and specifically so.

    A brace leaning 20-30 deg off vertical sits *inside* the vertical candidate
    window, and braces come in mirrored pairs at a consistent angle, so they
    form a coherent false vanishing point rather than scattered noise.  The
    steep 45 deg brace is harmless -- it falls outside any window.  The shallow
    one can only be handled by weighting.
    """
    worst_pitch = worst_roll = 0.0
    for lean in (20, 25, 28, 31, 45):
        for pitch, roll in ((3, -1.5), (6, 2), (10, 0), (14, -3)):
            sc = synth.Scene(w=1400, h=1000, focal_35mm=28, pitch_deg=pitch,
                             roll_deg=roll, seed=3, half_timbered=True,
                             brace_lean_deg=lean, clutter=25)
            dr, dp, _ = _err(sc, Settings().replace(focal_35mm=28))
            worst_pitch = max(worst_pitch, abs(dp))
            worst_roll = max(worst_roll, abs(dr))
    assert worst_roll < 0.6, f"roll error {worst_roll:.3f} deg on half-timbered work"
    assert worst_pitch < 2.0, f"pitch error {worst_pitch:.3f} deg on half-timbered work"


def test_a_sharper_angular_prior_is_what_helps_on_bracing():
    """Pins the measured finding: the weighting does the work, not the window."""
    from pc import lines as L
    lean = np.radians([0.0, 10.0, 20.0, 30.0])
    window = math.radians(32.0)
    soft = L.angular_prior(lean, window, 0.35)
    loose = L.angular_prior(lean, window, 0.60)
    assert soft[0] == loose[0] == 1.0
    assert np.all(soft[1:] < loose[1:]), "0.35 must down-weight leaning lines harder"
    assert soft[2] < 0.5, "a 20 deg brace must lose at least half its weight"


def test_yaw_is_recovered_from_the_dominant_horizontal_vp():
    """Yaw cannot be read off the vertical vanishing point -- a yaw is a
    rotation about the world vertical and leaves it fixed -- so it comes from
    the dominant horizontal one.  On a rendered facade with a known yaw the
    estimator must recover it to well under the 8 deg cap."""
    worst = 0.0
    for f35 in (24, 28, 35):
        for yaw in (-12, -6, 0, 5, 10):
            sc = synth.Scene(focal_35mm=f35, pitch_deg=8, roll_deg=2,
                             yaw_deg=yaw, seed=7)
            m, _, _, _, _ = analyse(sc.img, Settings().replace(
                correct_horizontal=True, focal_35mm=f35))
            worst = max(worst, abs(math.degrees(m.yaw) - yaw))
    assert worst < 1.0, f"yaw error {worst:.3f} deg"


def test_yaw_stays_zero_when_the_feature_is_off():
    """A run with the feature explicitly off over a scene with strong horizontal
    evidence must not estimate any yaw at all -- an off feature must leave the
    output exactly as it was."""
    sc = synth.Scene(focal_35mm=28, pitch_deg=8, roll_deg=2, yaw_deg=10, seed=7)
    m, _, _, _, _ = analyse(sc.img, Settings().replace(
        focal_35mm=28, correct_horizontal=False))
    assert m.yaw == 0.0
    assert m.diagnostics.get("yaw_deg", 0.0) == 0.0


def test_yaw_is_gated_on_horizontal_support():
    """A single weak horizontal cluster is evidence about one window row, not
    about the camera: an impossible support gate must hold the yaw at zero even
    with the feature switched on."""
    sc = synth.Scene(focal_35mm=28, pitch_deg=8, roll_deg=2, yaw_deg=10, seed=7)
    m, _, _, _, _ = analyse(sc.img, Settings().replace(
        correct_horizontal=True, focal_35mm=28, min_horizontal_support=1.01))
    assert m.yaw == 0.0


def test_the_tilt_prior_trades_an_implausible_tilt_for_a_wider_lens():
    """Without EXIF one vertical vanishing point fixes only f : tilt. The
    Antibes facade (2026-09-26) needed 50 deg of tilt at the 28 mm default --
    past the cap -- and straightened cleanly at ~12 mm. The tilt prior must
    slide such a case toward a wider lens, and leave a moderate tilt alone."""
    from pc import geometry as G
    from pc import model as M
    w, h = 1200, 1800
    cx, cy = w / 2.0, h / 2.0
    f28 = M.focal_px_from_35mm(28.0, w, h)
    for tilt, must_move in ((50.0, True), (8.0, False)):
        up = G.up_from_roll_pitch(0.0, math.radians(tilt))
        vp = G.intrinsics(f28, cx, cy) @ up
        f = M._tilt_prior_focal(vp, f28, 0.60, cx, cy, 15.0)
        _, pitch = G.roll_pitch_from_up(G.up_vector(vp, G.intrinsics(f, cx, cy)))
        if must_move:
            assert f < 0.7 * f28 and abs(math.degrees(pitch)) < 30.0, (f, math.degrees(pitch))
        else:
            assert abs(math.log(f / f28)) < 0.35, f
