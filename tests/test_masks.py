"""Region masks, and the seam an external segmenter plugs into."""
import os
import tempfile

import cv2
import numpy as np

import synth
from bpc import masks as MK
from bpc.config import Settings
from bpc.pipeline import analyse


def test_drop_masked_removes_lines_that_lie_mostly_inside():
    """The contract: a segment is kept while the masked fraction of it stays
    *below* the tolerance."""
    mask = np.zeros((200, 200), bool)
    mask[:, 100:] = True
    seg = np.array([[10., 10., 10., 190.],       # 0/5 samples masked -> kept
                    [150., 10., 150., 190.],     # 5/5 masked        -> dropped
                    [60., 10., 110., 190.]])     # 1/5 masked        -> kept
    kept = MK.drop_masked(seg, mask, tolerance=0.6)
    assert len(kept) == 2
    assert set(kept[:, 0].tolist()) == {10.0, 60.0}


def test_the_tolerance_is_what_decides_a_straddling_line():
    """A line running out of a wall into a tree is half evidence, so how much
    of it may be masked has to be a dial rather than a midpoint test."""
    mask = np.zeros((200, 200), bool)
    mask[:, 100:] = True
    straddler = np.array([[80., 10., 120., 190.]])   # 3 of 5 samples masked
    assert len(MK.drop_masked(straddler, mask, tolerance=0.7)) == 1
    assert len(MK.drop_masked(straddler, mask, tolerance=0.6)) == 0
    assert len(MK.drop_masked(straddler, mask, tolerance=0.2)) == 0


def test_no_mask_is_a_no_op():
    seg = np.array([[10., 10., 10., 190.]])
    assert np.array_equal(MK.drop_masked(seg, None), seg)
    assert MK.build(np.zeros((40, 40, 3), np.uint8), Settings())[0] is None


def test_auto_mask_finds_the_trees_and_spares_the_facade():
    sc = synth.Scene(w=900, h=600, pitch_deg=6, seed=9, occluders=3, clutter=10)
    m = MK.vegetation_and_sky(sc.img)
    assert m.any() and not m.all()
    # the wall occupies the middle band; it must not be masked wholesale
    middle = m[int(0.35 * 600):int(0.55 * 600), int(0.35 * 900):int(0.55 * 900)]
    assert middle.mean() < 0.5


def test_a_mask_folder_is_matched_by_file_stem():
    """What makes an external segmenter usable in batch: one mask per photo."""
    with tempfile.TemporaryDirectory() as d:
        cv2.imwrite(os.path.join(d, "shot.png"), np.zeros((10, 10), np.uint8))
        assert MK.resolve(d, "/somewhere/shot.jpg").endswith("shot.png")
        cv2.imwrite(os.path.join(d, "other_mask.png"), np.zeros((10, 10), np.uint8))
        assert MK.resolve(d, "x/other.jpg").endswith("other_mask.png")
        try:
            MK.resolve(d, "x/missing.jpg")
        except ValueError:
            pass
        else:
            raise AssertionError("a missing mask must be reported, not ignored")


def test_invert_flips_the_convention_a_segmenter_uses():
    """SAM hands back the subject in white; this tool wants the rejects in
    white, so the flag has to exist or every SAM mask is used backwards."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "m.png")
        img = np.zeros((20, 20), np.uint8)
        img[:, 10:] = 255
        cv2.imwrite(p, img)
        plain = MK.load(p, (20, 20))
        flipped = MK.load(p, (20, 20), invert=True)
        assert plain[0, 15] and not plain[0, 5]
        assert flipped[0, 5] and not flipped[0, 15]


def test_a_file_mask_actually_reaches_the_detector():
    sc = synth.Scene(w=900, h=600, pitch_deg=8, seed=10, clutter=10)
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "s.jpg")
        cv2.imwrite(src, sc.img)
        mask = np.zeros((600, 900), np.uint8)
        mask[:, 450:] = 255                       # blank out the right half
        cv2.imwrite(os.path.join(d, "s.png"), mask)
        st = Settings().replace(mask_mode="file", mask_file=d)
        _, vert, _, _, _ = analyse(sc.img, st, image_path=src)
        _, vert_all, _, _, _ = analyse(sc.img, Settings(), image_path=src)
        assert len(vert) < len(vert_all)
        assert len(vert) > 0
        # most of what lies wholly in the blanked half is gone.  Not all of it:
        # protect_structure deliberately un-masks whatever a long straight line
        # runs through, so a long vertical inside the mask is rescued on purpose.
        def wholly_right(ls):
            return int(((ls.seg[:, 0] > 470) & (ls.seg[:, 2] > 470)).sum())
        assert wholly_right(vert) < wholly_right(vert_all)
        # left of the boundary nothing may be touched at all
        left = lambda ls: int(((ls.seg[:, 0] < 430) & (ls.seg[:, 2] < 430)).sum())
        assert left(vert) == left(vert_all)


def test_only_lines_with_both_ends_inside_the_mask_are_removed():
    """The rule, stated on its own: endpoints, and nothing else.

    A line crossing the boundary keeps its full say. That is the conservative
    reading on purpose -- the half of it on the building is real evidence, the
    fit is length-weighted anyway, and there is no threshold to get wrong. It
    replaced a sampled test that dropped a segment once 60 % of five points fell
    inside, which discarded straddling lines wholesale and turned on a sample or
    two of noise.
    """
    mask = np.zeros((200, 200), bool)
    mask[:, 100:] = True
    seg = np.array([[10., 10., 10., 190.],       # wholly outside  -> kept
                    [150., 10., 150., 190.],     # wholly inside   -> dropped
                    [60., 100., 140., 100.],     # straddles       -> kept
                    [199., 5., 105., 195.]])     # both ends inside -> dropped
    kept = MK.drop_by_endpoints(seg, mask)
    assert len(kept) == 2
    assert set(kept[:, 0].tolist()) == {10.0, 60.0}, "a straddling line must survive"


def test_the_endpoint_rule_is_a_no_op_without_a_mask():
    seg = np.array([[10., 10., 10., 190.]])
    assert np.array_equal(MK.drop_by_endpoints(seg, None), seg)
    assert len(MK.drop_by_endpoints(np.zeros((0, 4)), np.ones((5, 5), bool))) == 0


def test_the_shrink_scales_with_the_image_not_the_pixel_count():
    """The same photograph at two analysis sizes must lose the same *relative*
    amount of silhouette, or the correction changes with --detect-max-edge."""
    from bpc import birefnet as BN
    small = BN.shrink_px_for((1071, 1600), 0.008)
    big = BN.shrink_px_for((2142, 3200), 0.008)
    assert small == 15, f"the default of a 1600 px frame should be ~15 px, got {small}"
    assert 1.8 <= big / small <= 2.2, (
        f"doubling the frame should about double the margin: {small} -> {big}")
    assert BN.shrink_px_for((1071, 1600), 0.0) == 0


# --------------------------------------------------------------------------
# the credibility guard
# --------------------------------------------------------------------------
def test_a_mask_that_eats_the_evidence_is_refused():
    """The guard that matters most for an external segmenter: a SAM mask used
    with the wrong polarity removes the building instead of the clutter, and
    shows up here as nearly all the line evidence vanishing."""
    before = np.array([[0., 0., 0., 100.]] * 10)
    ok, why = MK.credible(before, before[:9])          # 10 % lost
    assert ok and why == ""
    ok, why = MK.credible(before, before[:2])          # 80 % lost
    assert not ok and "line evidence" in why


def test_credibility_is_about_evidence_not_pixels():
    """Measured on real barns: 64 % of a frame masked can cost 1.5 % of the
    evidence (a grassy foreground) while 71 % can cost 74.5 % (a green-painted
    wall read as foliage). Only the second is dangerous."""
    long_lines = np.array([[0., 0., 0., 400.]] * 5)
    assert MK.credible(long_lines, long_lines)[0]
    assert not MK.credible(long_lines, np.zeros((0, 4)))[0]


def test_no_lines_at_all_is_not_a_failure():
    assert MK.credible(np.zeros((0, 4)), np.zeros((0, 4)))[0]
