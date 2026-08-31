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

from bpc.config import Settings
from bpc.imageio import READABLE
from bpc.pipeline import ERROR, OK, SKIPPED, analyse, process
from bpc.review import ReviewSession

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")


def _files():
    out = []
    for f in sorted(glob.glob(os.path.join(ASSETS, "*"))):
        if os.path.splitext(f)[1].lower() in READABLE:
            out.append(f)
    return out


def _require():
    files = _files()
    if not files:
        raise SkipTest("no images in tests/assets")   # noqa: F821
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
    s = Settings()
    for f in _require():
        m, _, _, _, _ = analyse(_load(f), s)
        assert abs(math.degrees(m.roll)) < 25.0, os.path.basename(f)
        assert abs(math.degrees(m.pitch)) < 35.0, os.path.basename(f)


def test_results_are_repeatable_on_real_files():
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
        raise SkipTest("no *_upright.* assets")       # noqa: F821
    for f in hits:
        with tempfile.TemporaryDirectory() as d:
            r = process(f, os.path.join(d, os.path.basename(f)), Settings())
            assert r.status == SKIPPED, f"{os.path.basename(f)} was changed: {r.line()}"


def test_files_marked_skip_are_refused():
    import tempfile
    hits = [f for f in _files() if "_skip" in os.path.basename(f).lower()]
    if not hits:
        raise SkipTest("no *_skip.* assets")          # noqa: F821
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


def _load(path):
    from bpc.imageio import load
    return load(path).bgr


# --------------------------------------------------------------------------
# ground truth on real photographs
# --------------------------------------------------------------------------
_DELTAS = [(2.0, 0.0), (-3.0, 0.0), (0.0, 4.0), (0.0, -5.0), (2.5, 3.5), (-1.5, -2.5)]


def _round_trip_error(path, focal_35mm=24.0, edge=1600):
    """Real error in degrees, without knowing the true camera pose.

    A photograph's pose is unknown, but a rotation applied here is known
    exactly: if the true world-up in camera coordinates is ``u0`` and the image
    is warped by ``R_d``, the warped copy's up must be ``R_d @ u0``.  Estimating
    from both and comparing measures the estimator on real texture, foliage and
    JPEG noise.
    """
    import cv2
    import numpy as np
    from bpc import geometry as G
    from bpc import imageio as IO
    from bpc import model as M

    bgr = IO.load(path).bgr
    h, w = bgr.shape[:2]
    s = min(1.0, edge / max(w, h))
    base = cv2.resize(bgr, (int(w * s), int(h * s)),
                      interpolation=cv2.INTER_AREA) if s < 1 else bgr
    bh, bw = base.shape[:2]
    st = Settings().replace(focal_35mm=focal_35mm)
    m0, _, _, _, _ = analyse(base, st)
    if m0.f is None:
        return None
    K = G.intrinsics(M.focal_px_from_35mm(focal_35mm, bw, bh), bw / 2.0, bh / 2.0)
    errs = []
    for dr, dp in _DELTAS:
        Rd = G.correction_rotation(math.radians(dr), math.radians(dp))
        warped = cv2.warpPerspective(base, G.homography(K, Rd), (bw, bh),
                                     flags=cv2.INTER_LANCZOS4,
                                     borderMode=cv2.BORDER_REPLICATE)
        m1, _, _, _, _ = analyse(warped, st)
        if m1.f is None:
            continue
        expect = Rd @ m0.up
        expect /= np.linalg.norm(expect)
        got = m1.up / np.linalg.norm(m1.up)
        errs.append(math.degrees(math.acos(min(1.0, abs(float(got @ expect))))))
    return float(np.mean(errs)) if errs else None


def test_a_known_rotation_is_recovered_on_real_photographs():
    import numpy as np
    errs = [e for e in (_round_trip_error(f) for f in _require()) if e is not None]
    assert errs, "no measurable assets"
    assert np.mean(errs) < 2.0, f"mean round-trip error {np.mean(errs):.2f} deg"
    assert max(errs) < 4.0, f"worst round-trip error {max(errs):.2f} deg"


def test_confidence_ranks_the_photographs_by_their_real_error():
    """The confidence score is the gate that decides whether a photo is touched
    at all, so it has to correlate with how wrong the answer actually is.  On
    the shipped assets it does: the two most confident images have the smallest
    round-trip error and the least confident has the largest.  Without this the
    score is only a plausible-looking formula."""
    import numpy as np
    conf, err = [], []
    for f in _require():
        e = _round_trip_error(f)
        if e is None:
            continue
        m, _, _, _, _ = analyse(_load(f), Settings())
        conf.append(m.confidence)
        err.append(e)
    if len(conf) < 4:
        raise SkipTest("need at least four measurable assets")     # noqa: F821
    rc = np.argsort(np.argsort(conf))
    re_ = np.argsort(np.argsort(err))
    rho = np.corrcoef(rc, re_)[0, 1]
    assert rho < -0.4, f"confidence does not track the real error (rho={rho:.2f})"
