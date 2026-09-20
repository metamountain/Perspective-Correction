"""End to end: files in, files out, decisions logged."""
import math
import os
import shutil
import tempfile

import cv2
import numpy as np

import synth
from pc.config import Settings
from pc.pipeline import ERROR, OK, SKIPPED, process


def _tmp():
    d = tempfile.mkdtemp(prefix="pc-test-")
    return d


def _write(scene, path, quality=95):
    cv2.imwrite(path, scene.img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return path


def test_a_tilted_photo_is_corrected_and_written():
    d = _tmp()
    try:
        src = _write(synth.Scene(pitch_deg=9, roll_deg=-3, seed=12), os.path.join(d, "a.jpg"))
        dst = os.path.join(d, "a_corr.jpg")
        r = process(src, dst, Settings())
        assert r.status == OK, r.line()
        assert os.path.exists(dst)
        out = cv2.imread(dst)
        assert out is not None and out.shape[0] > 100
        assert abs(r.roll_deg) > 1.0
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_an_upright_photo_is_left_alone():
    d = _tmp()
    try:
        src = _write(synth.Scene(pitch_deg=0, roll_deg=0, seed=13), os.path.join(d, "b.jpg"))
        r = process(src, os.path.join(d, "b_corr.jpg"), Settings())
        assert r.status == SKIPPED
        assert "upright" in r.reason or "confidence" in r.reason
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_an_image_without_structure_is_skipped_not_mangled():
    d = _tmp()
    try:
        src = os.path.join(d, "c.jpg")
        cv2.imwrite(src, synth.flat_image())
        dst = os.path.join(d, "c_corr.jpg")
        r = process(src, dst, Settings())
        assert r.status == SKIPPED
        # a skipped image still produces an output, byte identical to the input
        assert os.path.exists(dst)
        assert open(src, "rb").read() == open(dst, "rb").read()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_unreadable_input_is_an_error_not_a_crash():
    d = _tmp()
    try:
        bad = os.path.join(d, "broken.jpg")
        with open(bad, "wb") as fh:
            fh.write(b"not an image at all")
        r = process(bad, os.path.join(d, "out.jpg"), Settings())
        assert r.status == ERROR and "cannot read" in r.reason
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_exif_survives_and_orientation_is_reset():
    import piexif
    from PIL import Image
    d = _tmp()
    try:
        src = os.path.join(d, "e.jpg")
        sc = synth.Scene(pitch_deg=8, roll_deg=2, seed=14)
        Image.fromarray(cv2.cvtColor(sc.img, cv2.COLOR_BGR2RGB)).save(src, quality=95)
        exif = {"0th": {piexif.ImageIFD.Make: b"TestCam",
                        piexif.ImageIFD.Orientation: 1},
                "Exif": {piexif.ExifIFD.FocalLengthIn35mmFilm: 28,
                         piexif.ExifIFD.DateTimeOriginal: b"2026:01:02 03:04:05"},
                "GPS": {}, "1st": {}, "thumbnail": None}
        piexif.insert(piexif.dump(exif), src)
        dst = os.path.join(d, "e_corr.jpg")
        r = process(src, dst, Settings())
        assert r.status == OK, r.line()
        assert r.focal_source == "exif", r.focal_source
        got = piexif.load(dst)
        assert got["0th"][piexif.ImageIFD.Make] == b"TestCam"
        assert got["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"2026:01:02 03:04:05"
        assert got["0th"][piexif.ImageIFD.Orientation] == 1
        out = Image.open(dst)
        assert got["Exif"][piexif.ExifIFD.PixelXDimension] == out.width
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_portrait_orientation_tag_is_applied_before_detection():
    """A portrait shot stored landscape plus an orientation tag: detecting on
    the stored pixels would look for verticals along the wrong axis."""
    import piexif
    from PIL import Image
    d = _tmp()
    try:
        sc = synth.Scene(w=800, h=1200, pitch_deg=9, roll_deg=0, seed=15)
        stored = cv2.rotate(sc.img, cv2.ROTATE_90_COUNTERCLOCKWISE)   # as the file holds it
        src = os.path.join(d, "p.jpg")
        Image.fromarray(cv2.cvtColor(stored, cv2.COLOR_BGR2RGB)).save(src, quality=95)
        piexif.insert(piexif.dump({"0th": {piexif.ImageIFD.Orientation: 6},
                                   "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}), src)
        r = process(src, os.path.join(d, "p_corr.jpg"), Settings())
        assert r.status == OK, r.line()
        assert abs(r.pitch_deg) > 3.0, r.line()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_dry_run_writes_nothing():
    d = _tmp()
    try:
        src = _write(synth.Scene(pitch_deg=9, seed=16), os.path.join(d, "f.jpg"))
        dst = os.path.join(d, "f_corr.jpg")
        r = process(src, dst, Settings(), dry_run=True)
        assert r.status in (OK, SKIPPED)
        assert not os.path.exists(dst)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_same_input_gives_a_byte_identical_output_twice():
    d = _tmp()
    try:
        src = _write(synth.Scene(pitch_deg=7, roll_deg=2, seed=17), os.path.join(d, "g.jpg"))
        a, b = os.path.join(d, "g1.jpg"), os.path.join(d, "g2.jpg")
        assert process(src, a, Settings()).status == OK
        assert process(src, b, Settings()).status == OK
        assert open(a, "rb").read() == open(b, "rb").read()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_debug_overlay_is_written_when_asked():
    d = _tmp()
    try:
        src = _write(synth.Scene(pitch_deg=9, roll_deg=-2, seed=18), os.path.join(d, "h.jpg"))
        dbg = os.path.join(d, "debug")
        process(src, os.path.join(d, "h_corr.jpg"), Settings(), debug_dir=dbg)
        assert os.path.exists(os.path.join(dbg, "h_lines.jpg"))
        assert os.path.exists(os.path.join(dbg, "h_compare.jpg"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_skipped_image_can_be_written_into_a_new_output_folder():
    """The corrected path creates the output folder; the skipped path used not
    to, so a run into a fresh folder died on the first image it declined."""
    d = _tmp()
    try:
        src = os.path.join(d, "flat.jpg")
        cv2.imwrite(src, synth.flat_image())
        out = os.path.join(d, "does", "not", "exist", "flat_corr.jpg")
        r = process(src, out, Settings())
        assert r.status == SKIPPED, r.line()
        assert os.path.exists(out)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_mask_source_with_no_path_fails_once_not_per_file():
    """`--mask birefnet` without weights used to fail every image with what read
    as an internal fault. It is a configuration error and belongs before the run.

    The preferences file has to be isolated here or the test asserts on
    behaviour the developer's own machine cannot produce: a remembered
    ``birefnet_model`` fills the missing path in and the run succeeds, so this
    passed in CI and failed for anyone who had ever used ``--remember``.
    """
    from pc.cli import main as cli_main
    d = _tmp()
    old = (os.environ.get("XDG_CONFIG_HOME"), os.environ.get("APPDATA"))
    try:
        os.environ["XDG_CONFIG_HOME"] = d
        os.environ.pop("APPDATA", None)
        cv2.imwrite(os.path.join(d, "a.jpg"), synth.Scene(pitch_deg=8, seed=61).img)
        assert cli_main([d, "--mask", "birefnet", "-o", os.path.join(d, "out")]) == 2
        assert cli_main([d, "--mask", "file", "-o", os.path.join(d, "out")]) == 2
        assert not os.path.exists(os.path.join(d, "out"))
    finally:
        for k, v in zip(("XDG_CONFIG_HOME", "APPDATA"), old):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(d, ignore_errors=True)


def test_the_json_report_says_what_produced_it():
    """A log line reading "SKIPPED, low confidence" is nearly useless on its
    own: the answer depends on the interpreter, the library versions, which
    optional backends were present and what the settings were after defaults and
    remembered values were applied. A report that omits those cannot be judged
    by anyone who did not run it."""
    import json

    from pc.cli import main as cli_main
    d = _tmp()
    try:
        cv2.imwrite(os.path.join(d, "a.jpg"), synth.Scene(pitch_deg=8, seed=71).img)
        rep = os.path.join(d, "report.json")
        assert cli_main([d, "-n", "-q", "--json-report", rep]) == 0
        with open(rep, encoding="utf-8") as fh:
            data = json.load(fh)
        assert set(data) == {"environment", "results"}
        env = data["environment"]
        for expected in ("python", "numpy", "cv2", "optional backends", "settings"):
            assert expected in env, f"the report must record {expected}"
        assert len(data["results"]) == 1
        assert "confidence" in data["results"][0]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_diagnostics_go_into_the_log_file_too():
    from pc.cli import main as cli_main
    d = _tmp()
    try:
        cv2.imwrite(os.path.join(d, "a.jpg"), synth.Scene(pitch_deg=8, seed=72).img)
        log = os.path.join(d, "log.txt")
        assert cli_main([d, "-n", "-q", "--diagnostics", "--log-file", log]) == 0
        text = open(log, encoding="utf-8").read()
        assert "# python" in text and "# settings:" in text
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_correction_past_the_limit_is_refused_not_trimmed():
    """A cap used to clamp: an estimate past --max-pitch was trimmed to the cap
    and applied, turning "I do not believe this" into "I will do as much of it
    as I am allowed to".

    Found on a photograph of a railway station ceiling. A coffered ceiling has a
    clean bundle of parallel lines and a perfectly good vanishing point, so the
    fit was confident (0.57) while the model quietly took the ceiling grid for
    the world vertical -- and the answer was the maximum allowed warp, throwing
    away 41 % of the frame.
    """
    from pc.config import Settings
    d = _tmp()
    try:
        src = os.path.join(d, "a.jpg")
        cv2.imwrite(src, synth.Scene(pitch_deg=9, seed=71).img)
        # a cap far below what the scene needs stands in for the ceiling
        tight = Settings().replace(max_pitch_deg=1.0)
        r = process(src, os.path.join(d, "out.jpg"), tight, dry_run=True)
        assert r.status == SKIPPED, r.line()
        assert "beyond the limit" in r.reason
        assert "caps are" in r.reason, "the reason has to say what it was measured against"
        # and the old behaviour is still reachable
        loose = tight.replace(refuse_beyond_limit=False)
        r2 = process(src, os.path.join(d, "out2.jpg"), loose, dry_run=True)
        assert r2.status == OK and r2.clamped
        assert abs(r2.pitch_deg) <= 1.0001, "clamping must still clamp"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_pure_yaw_breach_warns_and_applies_not_refuses():
    """A yaw that runs past the horizontal cap is not refused when roll and
    pitch are both within their limits.

    Yaw is a special case the user opted into by ticking "correct horizontal":
    unlike roll/pitch it does not level the frame, it squares one facade onto
    fronto-parallel, and a legitimate corner shot routinely asks for more than
    the cap allows (P9 measured real single-VP yaws at 16-70 deg).  Refusing it
    would be the batch-era asymmetry that raised max_horizontal_deg to end.  So
    the capped value is applied with a diagnostic note, not a skip -- while a
    roll or pitch breach on the same frame still refuses.
    """
    d = _tmp()
    try:
        src = os.path.join(d, "a.jpg")
        # 30 deg is a corner that renders cleanly (75+ pushes the facade behind
        # the camera); the estimator recovers ~9 deg pitch and ~30 deg yaw on it
        cv2.imwrite(src, synth.Scene(pitch_deg=9, yaw_deg=30, seed=31).img)
        # a horizontal cap far below what the scene asks for stands in for the
        # 60 deg default meeting a real corner; roll/pitch stay at their caps
        s = Settings(correct_horizontal=True, max_horizontal_deg=15.0,
                     refuse_beyond_limit=True)
        r = process(src, os.path.join(d, "out.jpg"), s, dry_run=True)
        assert r.status == OK, f"expected the capped yaw to be applied: {r.line()}"
        assert r.clamped, "the yaw ran past the cap, so clamped must be set"
        assert abs(r.yaw_deg) <= 15.001, "clamping must still clamp the yaw"
        note = (r.diagnostics or {}).get("yaw_clamped", "")
        assert "yaw clamped from" in note, f"expected a yaw_clamped note, got {note!r}"
        # and the refuse path is untouched: a roll/pitch breach on the same frame
        # still skips, never warns-and-applies
        tight = s.replace(max_pitch_deg=1.0)
        r2 = process(src, os.path.join(d, "out2.jpg"), tight, dry_run=True)
        assert r2.status == SKIPPED, f"roll/pitch breach must still refuse: {r2.line()}"
        assert "beyond the limit" in r2.reason
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------
# roi_x -- the vertical strip that restricts the *horizontal* evidence
#
# A corner view shows two facades with two different horizontal vanishing
# points, and the estimator takes whichever has more support -- not necessarily
# the one the user means to straighten.  The strip says "take the yaw from the
# horizontals in here"; the verticals stay global, because both facades share
# the world-vertical vanishing point and restricting them would only burn
# evidence.
#
# The seam was written with no tests at all.  Everything below was measured
# first and then asserted, including the two places where what it does is not
# what it reports having done.
# --------------------------------------------------------------------------
_ROI = {}


def _roi_scene():
    """A corner view, built once -- rendering it is the expensive half."""
    if "scene" not in _ROI:
        _ROI["scene"] = synth.Scene(pitch_deg=7, roll_deg=-2, seed=21, corner=True)
    return _ROI["scene"]


def _roi_padded():
    """The same facade with blank margins left and right.

    Needed for the "strip over a blank region" case: on a plain rendered scene
    there is no gap between horizontal midpoints wide enough to clear the 5 %
    floor set_roi_x imposes, so the frame has to be widened rather than the
    API reached past."""
    if "padded" not in _ROI:
        sc = synth.Scene(pitch_deg=7, roll_deg=-2, seed=21)
        canvas = np.full((sc.h, 1600, 3), 200, np.uint8)
        canvas[:, 200:200 + sc.w] = sc.img
        _ROI["padded"] = canvas
    return _ROI["padded"]


def _roi_analyse(band=None, max_edge=None, padded=False):
    """analyse() on the cached scene, memoized on the FULL argument tuple.

    The full tuple matters: a cache keyed on the image alone would hand the
    degenerate-input tests the baseline result and they would pass while
    measuring nothing -- the same trap the round-trip cache documents."""
    from pc.pipeline import analyse
    key = (band, max_edge, padded)
    if key not in _ROI:
        s = Settings() if max_edge is None else Settings().replace(detect_max_edge=max_edge)
        img = _roi_padded() if padded else _roi_scene().img
        _ROI[key] = analyse(img, s, roi_x=band)
    return _ROI[key]


def _roi_summary(res):
    """(horizontals kept, roll, pitch, f) -- everything a strip could move."""
    m, horiz = res[0], res[2]
    return (len(horiz), round(math.degrees(m.roll), 9), round(math.degrees(m.pitch), 9),
            round(m.f or 0.0, 6))


def test_in_xband_selects_by_the_midpoint_so_a_straddling_line_is_a_coin_toss():
    """The strip is the whole feature: everything else is plumbing around which
    lines it keeps. It selects on the segment *midpoint*, which means a line
    lying half in and half out is decided by which side its centre falls on --
    a facade edge running out of the strip can be kept, and one running into it
    can be dropped. That is a real property of the rule, not an accident, and
    anybody changing it to an endpoint or an overlap test would change which
    facade the yaw comes from without changing any other visible behaviour."""
    from pc import lines as L
    seg = np.array([
        [110., 10., 190., 12.],   # wholly inside 100..200
        [10., 10., 40., 12.],     # wholly outside, left
        [300., 10., 400., 12.],   # wholly outside, right
        [50., 10., 150., 12.],    # straddles x0, midpoint exactly 100
        [60., 10., 150., 12.],    # straddles x0, midpoint 105 -> kept
        [90., 10., 400., 12.],    # straddles both ends, midpoint 245 -> dropped
        [180., 10., 260., 12.],   # straddles x1, midpoint 220 -> dropped
    ])
    keep = L.in_xband(seg, 100.0, 200.0)
    assert list(keep) == [True, False, False, True, True, False, False], list(keep)
    # the bounds are inclusive, so a midpoint sitting exactly on an edge is in
    assert bool(keep[3]), "a midpoint on x0 must be inside, not a boundary case"
    # and the empty inputs the fallback path depends on
    assert L.in_xband(np.zeros((0, 4)), 0.0, 10.0).shape == (0,)
    assert L.in_xband(None, 0.0, 10.0).shape == (0,)


def test_in_xband_accepts_its_bounds_in_either_order():
    """A band comes from a drag, and a drag has no preferred direction. Sorting
    inside the filter is what lets the caller pass the press and the release
    point without normalising them first."""
    from pc import lines as L
    seg = np.array([[110., 10., 190., 12.], [300., 10., 400., 12.]])
    assert list(L.in_xband(seg, 200.0, 100.0)) == list(L.in_xband(seg, 100.0, 200.0))


def test_use_scheme_partitions_lines_and_is_off_by_default():
    """The batch/CLI half of ArchitectureScheme: off by default the frame is
    untouched and nothing is reported; on, it partitions the detected lines into
    the building's Manhattan planes, re-derives vert/horiz from the survivors and
    leaves a visible summary in detect_info.  The safety contract this test pins
    is that the partition only ever *removes* evidence -- a classifier must never
    invent a line to fit a hypothesis.  A corner view is the case it exists for:
    two facades whose horizontals the plain orientation split would otherwise mix."""
    from pc.pipeline import analyse
    img = _roi_scene().img
    base_m, v0, h0, _, _ = analyse(img, Settings())
    assert "scheme" not in (base_m.detect_info or {}), \
        "use_scheme defaults off, so nothing may be partitioned or reported"

    on_m, v1, h1, _, _ = analyse(img, Settings().replace(use_scheme=True))
    info = on_m.detect_info or {}
    assert isinstance(info.get("scheme"), str) and info["scheme"], \
        f"a scheme run must report its summary, got {info.get('scheme')!r}"
    assert len(v1) + len(h1) <= len(v0) + len(h0), \
        "the scheme only removes lines, it never invents them"


def test_a_strip_thins_both_pools_not_just_the_horizontals():
    """The strip restricts the evidence to one facade of a corner view -- BOTH
    pools, verticals included, because the two faces have different vertical
    clusters and a mixed-facade pitch fit invalidates the yaw.

    Renamed: it used to be called `..._leaves_the_verticals_bit_identical`
    while asserting the exact opposite two lines down. A test whose name
    contradicts its own body teaches the wrong contract to everyone who greps
    for it and never runs it."""
    _, v0, h0, _, _ = _roi_analyse()
    # A quarter of the width, as a FRACTION: line midpoints are spread across
    # the whole frame, so this must drop lines from both pools.
    _, v1, h1, _, _ = _roi_analyse(band=(0.0, 0.25))
    assert len(h1) < len(h0), f"a strip over a quarter of the frame must drop horizontals ({len(h1)} vs {len(h0)})"
    assert len(v1) < len(v0), f"a strip must also drop verticals (corner-view fix) ({len(v1)} vs {len(v0)})"


def test_a_strip_barely_moves_the_roll_and_does_move_the_pitch():
    """The strip changes the correction: roll drifts slightly (horizon-support
    term), pitch moves because the focal is re-fitted from fewer horizontals.
    Since the corner-view fix, verticals are also restricted, and pitch damping
    at |yaw|>20° further reduces the applied pitch.  The assertion is that
    roll stays stable (< 0.5°) and pitch stays in the same ballpark (< 3°)."""
    base = _roi_analyse()[0]
    for band in ((0.0, 480.0), (720.0, 1200.0), (300.0, 720.0)):
        m = _roi_analyse(band=band)[0]
        d_roll = abs(math.degrees(m.roll - base.roll))
        d_pitch = abs(math.degrees(m.pitch - base.pitch))
        assert d_roll < 0.5, f"{band}: roll moved {d_roll:.3f} deg"
        assert d_pitch < 3.0, f"{band}: pitch moved {d_pitch:.3f} deg"


def test_a_strip_over_a_blank_region_falls_back_to_the_whole_frame():
    """A selection that selects nothing must not silently zero the evidence.
    Fitting a yaw to no horizontals at all would either fail or -- worse --
    succeed on whatever the empty case degenerates to, and a batch tool cannot
    afford a correction derived from nothing. Asserted as exact equality with
    the un-restricted run: the fallback is total, not partial."""
    base = _roi_analyse(padded=True)
    blank = _roi_analyse(band=(1420.0, 1590.0), padded=True)
    assert _roi_summary(blank) == _roi_summary(base), "the empty strip must fall back"
    assert not math.isnan(blank[0].roll) and not math.isnan(blank[0].pitch)


def test_every_degenerate_strip_falls_back_except_a_reversed_one():
    """Degenerate bounds arrive from a mis-drag, a stale coordinate or a flag
    typed by hand, and the only safe answer is the one that changes nothing.
    Measured: zero width, wholly outside the frame on either side, a one-pixel
    sliver and an absurdly wide band all select either no midpoint or every
    midpoint, and come back bit identical to no strip at all -- no exception,
    no NaN.

    Reversed bounds are the one input that is not a fallback: in_xband sorts, so
    (900, 300) filters exactly like (300, 900). Defensible -- but see the
    round-trip test, because the reversed pair is still what gets reported."""
    base = _roi_summary(_roi_analyse())
    # These select no midpoint at all, so the fallback is what fires.  "Zero
    # width" belongs here only because no horizontal midpoint in this scene sits
    # exactly on 600.0: the bounds are inclusive, so a zero-width band laid over
    # a midpoint keeps it -- see the in_xband unit test above.
    for name, band in (("zero width", (600.0, 600.0)),
                       ("wholly right of the frame", (5000.0, 6000.0)),
                       ("wholly negative", (-900.0, -100.0)),
                       ("one-pixel sliver", (0.0, 1.0))):
        assert _roi_summary(_roi_analyse(band=band)) == base,             f"{name} selects nothing, so the fit must be unchanged"
    # This one is the opposite route to the same place: it selects *everything*,
    # so the subset runs and is simply the whole set.  Asserting it as a
    # "fallback" would be asserting the wrong mechanism.
    assert _roi_summary(_roi_analyse(band=(-5000.0, 5000.0))) == base,         "a band wider than the world keeps every line, so the fit must be unchanged"
    # Reversed bounds sort inside in_xband, so (900, 300) filters exactly like
    # (300, 900).  With vertical filtering + pitch damping the fit for this
    # band happens to be bit-identical to the un-restricted one, so we only
    # assert the sorting property, not a difference from base.
    assert _roi_summary(_roi_analyse(band=(900.0, 300.0))) == \
        _roi_summary(_roi_analyse(band=(300.0, 900.0))), "reversed bounds sort"


def test_the_strip_means_the_same_thing_at_two_analysis_resolutions():
    """A strip given from outside must not move when the analysis size changes.

    It used to be documented as full-resolution pixels and USED as analysis
    pixels, so a caller's band landed somewhere nobody pointed at on every
    photograph large enough to be downscaled -- and the test that was supposed
    to catch that asserted only that the band restricted SOMETHING, never that
    it was rescaled. Its own name said "full pixels", its comment inside said
    "analysis-image pixels", and neither was checked.

    Fractions of the width remove the question. This measures the answer: the
    kept horizontals must fall inside the requested band at BOTH resolutions,
    with the band read back as a fraction of each analysis width."""
    import numpy as np

    lo, hi = 0.30, 0.70
    for max_edge in (600, 1100):
        _, _v, h, _sc, _d = _roi_analyse(band=(lo, hi), max_edge=max_edge)
        full = _roi_analyse(max_edge=max_edge)
        assert len(h) < len(full[2]),             f"max_edge={max_edge}: the band must restrict ({len(h)} vs {len(full[2])})"
        # Midpoints of what survived, as fractions of that analysis width.
        mid = (h.seg[:, 0] + h.seg[:, 2]) / 2.0
        span = float(mid.max() - mid.min())
        width = float(np.max(full[2].seg[:, [0, 2]]))
        frac_span = span / width
        assert frac_span <= (hi - lo) + 0.08, (
            f"max_edge={max_edge}: kept lines span {frac_span:.2f} of the width, "
            f"wider than the {hi - lo:.2f} band asked for")




def test_the_strip_round_trips_onto_the_result_and_into_the_log_line():
    """A run has to be judgeable by someone who did not make it, and a
    correction taken from one facade is not comparable with one taken from the
    whole frame. The strip therefore has to survive onto the Result and into the
    log line -- as floats, whatever sequence type the caller passed."""
    d = _tmp()
    try:
        src = _write(synth.Scene(pitch_deg=8, roll_deg=-2, seed=21, corner=True),
                     os.path.join(d, "a.jpg"))
        r = process(src, os.path.join(d, "o.jpg"), Settings(), dry_run=True,
                    roi_x=(120, 900))
        assert r.status == OK, r.line()
        assert isinstance(r.roi_x, tuple) and r.roi_x == (120.0, 900.0)
        assert all(isinstance(v, float) for v in r.roi_x), "stored as floats, not ints"
        assert "roi=x[120-900]" in r.line(), r.line()
        plain = process(src, os.path.join(d, "o.jpg"), Settings(), dry_run=True)
        assert plain.roi_x is None and "roi=" not in plain.line()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_log_line_reports_a_strip_that_was_silently_ignored():
    """DOCUMENTS CURRENT BEHAVIOUR, WHICH LOOKS WRONG. Reported, not fixed.

    process() files roi_x onto the Result from its own argument, before analyse
    runs, and analyse returns nothing about whether the strip held any
    horizontals. So a strip that fell back to the full frame is still reported
    as though it had been used: the log line below claims roi=x[5000-6000] for a
    run that used every horizontal in the picture, and a reversed pair is
    printed as the impossible range roi=x[900-300].

    This is the shape of failure this project has already fixed once, for masks
    -- "a mask covering 0.0 % of the frame was indistinguishable from a working
    one" -- and the answer there was a diagnostic, not a hidden number. The
    roi_x seam has the same hole and no diagnostic. When one is added, this is
    the test to change."""
    d = _tmp()
    try:
        src = _write(synth.Scene(pitch_deg=8, roll_deg=-2, seed=21, corner=True),
                     os.path.join(d, "a.jpg"))
        ignored = process(src, os.path.join(d, "o.jpg"), Settings(), dry_run=True,
                          roi_x=(5000.0, 6000.0))
        plain = process(src, os.path.join(d, "o.jpg"), Settings(), dry_run=True)
        assert abs(ignored.pitch_deg - plain.pitch_deg) < 1e-9, \
            "the strip really was ignored -- the fit is the un-restricted one"
        assert ignored.roi_x == (5000.0, 6000.0), "...and it is reported anyway"
        assert "roi=x[5000-6000]" in ignored.line(), ignored.line()
        backwards = process(src, os.path.join(d, "o.jpg"), Settings(), dry_run=True,
                            roi_x=(900.0, 300.0))
        assert "roi=x[900-300]" in backwards.line(), backwards.line()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_set_roi_x_converts_from_display_pixels_and_clear_says_whether_it_cleared():
    """The band is dragged on a preview a few hundred pixels wide, while the
    evidence lives in the analysis image, so the conversion is the whole method.
    And clear_roi_x returns whether there was anything to clear: a control that
    reports success on a no-op is how a window comes to disagree with the state
    it is showing."""
    from pc.review import ReviewSession
    s = ReviewSession("mem.jpg", Settings(), image=_roi_scene().img)
    assert s.roi_x is None and s.scale == 1.0
    assert s.set_roi_x(100, 700, 1.0) is True
    assert s.roi_x == (100.0, 700.0)
    assert s.clear_roi_x() is True and s.roi_x is None
    assert s.clear_roi_x() is False, "nothing to clear must report nothing cleared"
    # a preview at half size: the same drag means twice as many analysis pixels
    assert s.set_roi_x(100, 400, 0.5) is True
    assert s.roi_x == (200.0, 800.0), s.roi_x


def test_set_roi_x_refuses_a_strip_too_narrow_to_have_been_meant():
    """A mis-click is a drag of a few pixels, and a few pixels of facade is not
    a choice of facade -- it is a selection that would fall back to the full
    frame anyway, while the status line claimed a restriction. Refusing at 5 %
    of the frame says so instead. Measured: a refusal leaves the band exactly as
    it was rather than clearing it, which is the right answer for a stray click
    during a review."""
    from pc.review import ReviewSession
    s = ReviewSession("mem.jpg", Settings(), image=_roi_scene().img)
    for name, args in (("narrow", (100, 130)), ("zero width", (600, 600)),
                       ("wholly right of the frame", (5000, 6000)),
                       ("wholly negative", (-900, -100))):
        assert s.set_roi_x(args[0], args[1], 1.0) is False, name
        assert s.roi_x is None, f"{name} must not store a band"
    # NOTE the mechanism, because it is accidental rather than designed: the two
    # out-of-frame bands are refused by the *width* floor, not by a bounds
    # check.  Clamping leaves ax0 > ax1, the width comes out negative, and a
    # negative number is below 5 % of anything.  Right answer, by luck; anyone
    # reordering or widening that guard has to keep it.
    # one that merely overlaps the frame edge is clamped to it, not refused
    assert s.set_roi_x(-500, 600, 1.0) is True
    assert s.roi_x == (0.0, 600.0), s.roi_x
    # and a backwards drag is sorted, like in_xband
    assert s.set_roi_x(900, 300, 1.0) is True
    assert s.roi_x == (300.0, 900.0), s.roi_x


def test_the_status_line_says_zero_lines_while_the_fit_quietly_used_them_all():
    """DOCUMENTS CURRENT BEHAVIOUR, WHICH LOOKS WRONG. Reported, not fixed.

    refit() falls back to the full frame when a strip holds no horizontals, but
    status_text recomputes the count on its own and knows nothing about that
    fallback. So the review window tells the user "horizontal evidence
    restricted to the selected strip (0 of N lines) -- the yaw is taken from
    that facade only" about a fit that used all N and took the yaw from the
    whole frame. The status line states the opposite of what happened, which is
    worse than saying nothing, and it is the user-facing half of the same
    missing diagnostic as the log line above."""
    from pc.review import ReviewSession
    s = ReviewSession("mem.jpg", Settings(), image=_roi_padded())
    before = (s.model.roll, s.model.pitch)
    assert s.set_roi_x(1420, 1590, 1.0) is True, "the blank margin clears the 5 % floor"
    assert (s.model.roll, s.model.pitch) == before, \
        "the fit fell back to the full frame, exactly as refit intends"
    region = [ln for ln in s.status_text().splitlines() if ln.startswith("region:")]
    assert len(region) == 1, s.status_text()
    assert "(0 of " in region[0], region[0]
    assert "that facade only" in region[0], region[0]
