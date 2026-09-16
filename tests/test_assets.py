"""Tests against real photographs in ``tests/assets``.

Synthetic scenes verify the geometry, because only there is the true camera
pose known.  What they cannot verify is the front end: whether LSD finds a
facade under real texture, JPEG blocking, foliage and lens distortion, and
whether the confidence gate fires on the right pictures.  That needs real
files, so drop architectural photos into ``tests/assets`` and these tests start
running.  Nothing here asserts a specific angle -- there is no ground truth for
a photograph -- only that the tool behaves sanely and repeatably.

A file named ``*_upright.*`` is additionally asserted to need no correction,
and ``*_skip.*`` to be refused.  Everything else is only checked for sanity.
"""
import glob
import math
import os

import pytest

from pc.config import Settings
from pc.imageio import READABLE
from pc.pipeline import ERROR, OK, SKIPPED, analyse, process
from pc.review import ReviewSession

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")


def _horizontal_files():
    """Inputs under ``assets/Horizontal`` (the yaw set).

    Mixed extensions, and this tool's own ``*_corr.*`` outputs land in the same
    folder -- those are results, not inputs, so a test that read one back would
    be grading its own homework. Filter them out."""
    out = []
    for f in sorted(glob.glob(os.path.join(ASSETS, "Horizontal", "*"))):
        name = os.path.basename(f)
        if os.path.splitext(f)[1].lower() in READABLE and "_corr" not in name.lower():
            out.append(f)
    return out


def _files():
    seen, out = set(), []
    candidates = sorted(glob.glob(os.path.join(ASSETS, "*"))) + _horizontal_files()
    for f in candidates:
        if os.path.splitext(f)[1].lower() not in READABLE:
            continue
        k = os.path.abspath(f)
        if k in seen:
            continue
        seen.add(k)
        out.append(f)
    return out


# How many photographs the pool-wide sweeps may use. The full pool is ~50 and
# the sweeps are the bulk of the suite's runtime; five is a cross-section, not a
# measurement of the pool (user, 2026-09-14: "testsuite max 5 images").
# `PC_TEST_ASSETS=0` restores the whole pool for a real measurement -- use it
# before writing any number into CLAUDE.md, because a claim about the pool that
# was computed from five of it is not a claim about the pool.
MAX_ASSETS = int(os.environ.get("PC_TEST_ASSETS", "5"))

# Names that must survive the cap whatever the sample says, because a dedicated
# test is about them. Dropping one would turn its test into a silent skip -- or
# worse, would take a *known failure* out of the suite and read as a fix.
_ALWAYS = ("_upright.", "_skip.", "39079116")


def _sample(files):
    """An evenly spaced slice of the sorted pool, plus the named exceptions.

    Evenly spaced rather than the first N: the pool is sorted by filename, so
    the first five are whatever sorts first, which on this set means five
    photographs from one contributor.
    """
    if MAX_ASSETS <= 0 or len(files) <= MAX_ASSETS:
        return files
    step = len(files) / float(MAX_ASSETS)
    picked = {files[min(len(files) - 1, int(i * step))] for i in range(MAX_ASSETS)}
    picked.update(f for f in files if any(k in os.path.basename(f) for k in _ALWAYS))
    return [f for f in files if f in picked]


def _require():
    files = _sample(_files())
    if not files:
        raise pytest.skip("no images in tests/assets")
    return files


def test_every_asset_is_processed_without_error():
    import tempfile
    for f in _require():
        with tempfile.TemporaryDirectory() as d:
            r = process(f, os.path.join(d, os.path.basename(f)), Settings())
            assert r.status != ERROR, f"{os.path.basename(f)}: {r.reason}"


def test_corrections_stay_within_plausible_bounds():
    """Whatever it decides, it must not propose an angle no photographer
    would have produced, and must not throw the frame away."""
    for f in _require():
        m = _analyse_default(f)
        assert abs(math.degrees(m.roll)) < 25.0, os.path.basename(f)
        assert abs(math.degrees(m.pitch)) < 35.0, os.path.basename(f)


def test_results_are_repeatable_on_real_files():
    # Deliberately bypasses _analyse_default: this test *is* the determinism
    # check, so its two runs must both be fresh.
    s = Settings()
    for f in _require():
        img = _load(f)
        a = analyse(img, s)[0]
        b = analyse(img, s)[0]
        assert (a.roll, a.pitch, a.f) == (b.roll, b.pitch, b.f), os.path.basename(f)


def test_files_marked_upright_are_left_alone():
    import tempfile
    hits = [f for f in _files() if "_upright" in os.path.basename(f).lower()]
    if not hits:
        raise pytest.skip("no *_upright.* assets")
    for f in hits:
        with tempfile.TemporaryDirectory() as d:
            r = process(f, os.path.join(d, os.path.basename(f)), Settings())
            assert r.status == SKIPPED, f"{os.path.basename(f)} was changed: {r.line()}"


def test_files_marked_skip_are_refused():
    import tempfile
    hits = [f for f in _files() if "_skip" in os.path.basename(f).lower()]
    if not hits:
        raise pytest.skip("no *_skip.* assets")
    for f in hits:
        with tempfile.TemporaryDirectory() as d:
            r = process(f, os.path.join(d, os.path.basename(f)), Settings())
            assert r.status == SKIPPED, f"{os.path.basename(f)}: {r.line()}"


def test_manual_review_opens_on_every_asset():
    for f in _require():
        s = ReviewSession(f, Settings())
        before, after = s.render_pair(400)
        assert before.size and after.size
        s.set_manual(roll_deg=1.0, pitch_deg=2.0, focal_35mm=28)
        assert s.would_skip() is None


_LOAD_CACHE = {}


def _load(path):
    """Decode ``path`` once per file and hand out a fresh copy each time.

    The copy is the point: callers keep exactly today's contract -- an array
    they own and may mutate -- while the decode (PIL open, EXIF transpose,
    colour conversion) happens only once per asset instead of once per test.
    """
    from pc.imageio import load
    arr = _LOAD_CACHE.get((path,))
    if arr is None:
        arr = _LOAD_CACHE[(path,)] = load(path).bgr
    return arr.copy()


_DEFAULT_ANALYSE = {}


def _analyse_default(path):
    """The default-settings model for one asset, computed once per run.

    Four of the sweeps below ask the same question of every asset -- what does
    a plain ``Settings()`` run make of it -- and the answer is deterministic,
    which ``test_results_are_repeatable_on_real_files`` pins with two fresh,
    uncached runs. Sharing it here turns three ~2.8 s re-runs into one; the
    repeatability test is what keeps the sharing honest.
    """
    m = _DEFAULT_ANALYSE.get(path)
    if m is None:
        m = _DEFAULT_ANALYSE[path] = analyse(_load(path), Settings())[0]
    return m


def known_focal_35mm(path):
    """The 35 mm equivalent a ``_f<NN>`` suffix records, or ``None``.

    EXIF cannot supply this for the shipped assets -- see
    ``tests/assets/README.md`` -- so where it is known it lives in the file name,
    which survives every resave.
    """
    import re
    stem = os.path.splitext(os.path.basename(path))[0]
    m = re.search(r"_f(\d{1,3})$", stem)
    return float(m.group(1)) if m else None


def test_the_focal_suffix_is_read_and_is_plausible():
    """A mis-typed suffix would silently feed a wrong focal length into every
    measurement that trusts it, which is worse than having no suffix at all."""
    assert known_focal_35mm("a/b/hospital-nikon-d60_f27.jpg") == 27.0
    assert known_focal_35mm("a/b/plain.jpg") is None
    for f in _require():
        v = known_focal_35mm(f)
        assert v is None or 8.0 <= v <= 200.0, f"{f}: implausible focal {v}"


# --------------------------------------------------------------------------
# ground truth on real photographs
# --------------------------------------------------------------------------
_DELTAS = [(2.0, 0.0), (-3.0, 0.0), (0.0, 4.0), (0.0, -5.0), (2.5, 3.5), (-1.5, -2.5)]


# How much of the frame the warp is allowed to spoil.  See _round_trip_error.
_BORDER_GUARD = 0.08


_RT_CACHE = {}


def _round_trip_error(path, focal_35mm=24.0, edge=1600, inner=_BORDER_GUARD):
    """Real error in degrees, without knowing the true camera pose.

    A photograph's pose is unknown, but a rotation applied here is known
    exactly: if the true world-up in camera coordinates is ``u0`` and the image
    is warped by ``R_d``, the warped copy's up must be ``R_d @ u0``.  Estimating
    from both and comparing measures the estimator on real texture, foliage and
    JPEG noise.

    **``inner`` is not a detail -- without it this function measures itself.**
    ``warpPerspective`` has to invent the band that rotates in from outside the
    frame, and ``BORDER_REPLICATE`` invents it by smearing the edge pixels into
    long, perfectly straight streaks.  Where a photograph's content runs to the
    frame edge, those streaks are strong lines at an angle that belongs to no
    building, and the detector believes them.

    It stayed hidden for as long as every asset was a barn with sky at its
    edges, where the smear is bland.  A modern facade that fills the frame
    exposed it at once: ``hospital-nikon-d60_f27.jpg`` measured **6.08 deg**, and
    the cause looked convincingly like wide-angle lens distortion -- the error
    vanished under a centred crop, it was flat across assumed focal lengths, and
    the camera really is a kit zoom at its wide end.  It was none of that.
    Correcting the distortion with Hugin's radial model barely moved it (6.08 to
    5.70 at a realistic coefficient), while discarding the invented band moved it
    to **0.33**, making that photograph one of the *best* in the set.

    Cropping both the base and the warped copy by the same margin keeps the two
    passes the same size, so they share a focal length in pixels and the metric
    stays self-consistent.

    Across the assets, as shipped vs. guarded: mean 1.71 -> **0.66 deg**, worst
    6.08 -> **1.68**.

    The result is memoized on the *full* argument tuple. Two of the tests below
    sweep every asset with identical defaults, so without this the second sweep
    recomputes ~1.2 s per photograph for nothing. ``inner`` must stay in the
    key: ``test_the_border_guard_is_what_makes_the_measurement_honest`` calls
    the same file once guarded and once with ``inner=0.0`` and relies on the
    two values differing -- a path-only cache would silently break it.
    """
    key = (path, focal_35mm, edge, inner)
    if key in _RT_CACHE:
        return _RT_CACHE[key]
    import cv2
    import numpy as np
    from pc import geometry as G
    from pc import imageio as IO
    from pc import model as M

    bgr = IO.load(path).bgr
    h, w = bgr.shape[:2]
    s = min(1.0, edge / max(w, h))
    full = cv2.resize(bgr, (int(w * s), int(h * s)),
                      interpolation=cv2.INTER_AREA) if s < 1 else bgr
    fh, fw = full.shape[:2]
    m = int(min(fh, fw) * inner)
    st = Settings().replace(focal_35mm=focal_35mm)
    m0, _, _, _, _ = analyse(full[m:fh - m, m:fw - m], st)
    if m0.f is None:
        _RT_CACHE[key] = None
        return None
    # K describes the *uncropped* frame, because that is what is being rotated
    K = G.intrinsics(M.focal_px_from_35mm(focal_35mm, fw, fh), fw / 2.0, fh / 2.0)
    errs = []
    for dr, dp in _DELTAS:
        Rd = G.correction_rotation(math.radians(dr), math.radians(dp))
        warped = cv2.warpPerspective(full, G.homography(K, Rd), (fw, fh),
                                     flags=cv2.INTER_LANCZOS4,
                                     borderMode=cv2.BORDER_REPLICATE)
        m1, _, _, _, _ = analyse(warped[m:fh - m, m:fw - m], st)
        if m1.f is None:
            continue
        expect = Rd @ m0.up
        expect /= np.linalg.norm(expect)
        got = m1.up / np.linalg.norm(m1.up)
        errs.append(math.degrees(math.acos(min(1.0, abs(float(got @ expect))))))
    result = float(np.mean(errs)) if errs else None
    _RT_CACHE[key] = result
    return result


def test_a_known_rotation_is_recovered_on_real_photographs():
    """Bounded over the photographs the tool would actually act on.

    It used to average every asset, which was fine while every asset was one the
    tool would correct. It is not a fair question once the folder also holds the
    cases that exist to be *refused*: a skyscraper shot straight up the facade
    measures **31.6 deg** here and is rejected at confidence 0.00, so averaging
    it in says nothing about the estimator and everything about the test set.

    The promise this project makes is not "every photograph is estimated well",
    it is "what it touches is not ruined". So the bound follows the gate. The
    refused ones are not unmeasured -- ``test_the_confidence_gate_admits_most_of
    _a_good_set`` keeps the gate from earning this by refusing everything.
    """
    import numpy as np
    s = Settings()
    errs = []
    for f in _require():
        e = _round_trip_error(f)
        if e is None:
            continue
        m = _analyse_default(f)
        if m.confidence >= s.min_confidence:
            errs.append(e)
    assert errs, "no measurable assets the tool would act on"
    assert np.mean(errs) < 1.2, f"mean round-trip error {np.mean(errs):.2f} deg"
    assert max(errs) < 2.5, f"worst round-trip error {max(errs):.2f} deg"


def test_the_border_guard_is_what_makes_the_measurement_honest():
    """Pins the artifact, because removing the guard does not fail loudly.

    Without it the harness measures the streaks ``BORDER_REPLICATE`` invents
    rather than the estimator, and it does so *selectively* -- only on
    photographs whose content reaches the frame edge, which is exactly the
    architectural case this project is for. The wrong conclusion it produced
    was "wide-angle lens distortion", complete with a plausible mechanism and a
    crop experiment that appeared to confirm it.
    """
    hits = [f for f in _files() if "hospital" in os.path.basename(f)]
    if not hits:
        raise pytest.skip("the frame-filling asset is not present")
    guarded = _round_trip_error(hits[0], focal_35mm=27.0)
    raw = _round_trip_error(hits[0], focal_35mm=27.0, inner=0.0)
    assert guarded is not None and raw is not None
    assert guarded < 1.0, f"guarded {guarded:.2f} deg"
    assert raw > 3.0 * guarded, (
        f"the border artifact no longer shows: guarded {guarded:.2f}, "
        f"unguarded {raw:.2f} -- if warping changed, rewrite the note above")


def test_every_photograph_it_is_confident_about_is_measured_accurately():
    """The property the skip-and-review design actually rests on.

    This replaces ``test_confidence_ranks_the_photographs_by_their_real_error``,
    and the reason is a correction worth keeping. That test asserted a rank
    correlation of at least -0.4 between confidence and real error, and it
    passed at **-0.68** -- but only because the harness was measuring its own
    border fill (see ``_round_trip_error``). With the artifact removed the
    correlation is **-0.11**: confidence does *not* rank these photographs by
    their error.

    That is much less alarming than it sounds, and the honest reading is not
    "the score is broken". Once the artifact is gone the errors span 0.19 to
    1.68 deg -- there is barely anything left to rank, and a rank correlation
    over seven nearly-equal values is mostly noise. What was being ranked before
    was how much of the frame each photograph filled.

    So the assertion moves to what the gate is *for*: confidence decides whether
    a photograph is touched at all, and the promise is that what it touches does
    not come out wrong. That is a bound, not an ordering, and it is testable
    without a wide spread of errors. Restore a ranking test only with assets
    that genuinely span a range of accuracy.
    """
    s = Settings()
    bad = []
    for f in _require():
        e = _round_trip_error(f)
        if e is None:
            continue
        m = _analyse_default(f)
        if m.confidence >= s.min_confidence and e > 2.0:
            bad.append(f"{os.path.basename(f)}: conf {m.confidence:.2f}, error {e:.2f} deg")
    assert not bad, "confident but wrong: " + "; ".join(bad)


def test_the_confidence_gate_admits_most_of_a_good_set():
    """The complement, so the bound above cannot be met by refusing everything.

    A gate that skips every photograph satisfies "nothing it touches is wrong"
    perfectly and is useless. These assets are all ordinary, correctable
    architectural photographs, so most of them have to get through.
    """
    s = Settings()
    conf = [_analyse_default(f).confidence for f in _require()]
    admitted = sum(1 for c in conf if c >= s.min_confidence)
    assert admitted >= 0.7 * len(conf), (
        f"only {admitted}/{len(conf)} assets clear the confidence gate")
