"""Region masks, and the seam an external segmenter plugs into."""
import os
import tempfile

import cv2
import numpy as np

import synth
from pc import masks as MK
from pc.config import Settings
from pc.pipeline import analyse


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


def test_the_retired_auto_mode_is_accepted_and_does_nothing():
    """``auto`` was a cheap texture heuristic and is gone.  It is still accepted
    so that an old command line, or a settings file that remembers it, does not
    abort a batch -- it simply produces no mask."""
    m, note = MK.build(np.zeros((40, 40, 3), np.uint8), Settings().replace(mask_mode="auto"))
    assert m is None and note == ""


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
    from pc import birefnet as BN
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


# --------------------------------------------------------------------------
# the gdino matte: a prompt finds the box, BiRefNet mattes inside it
# --------------------------------------------------------------------------
def test_gdino_compose_ignores_outside_and_mattes_inside():
    """Everything outside the (padded) box is not the subject and is ignored;
    inside, the crop's own matte decides.  The convention is True = ignore."""
    crop_ignore = np.zeros((40, 40), bool)
    crop_ignore[:, :10] = True          # the left strip of the crop is background
    ignore = MK._gdino_compose(100, 200, 40, 20, 80, 60, crop_ignore)
    assert ignore.shape == (100, 200)
    # outside the box, in every corner and on a far edge: ignored
    assert ignore[0, 0] and ignore[99, 199] and ignore[5, 100]
    # just above the top border of the box is still outside
    assert ignore[19, 60]
    # inside: the left strip of the crop is background (ignored), the rest kept
    assert ignore[30, 45]               # frame col 45 -> crop col 5  -> bg
    assert not ignore[30, 70]           # frame col 70 -> crop col 30 -> fg


def test_gdino_compose_resizes_a_mismatched_matte():
    """A matte that does not exactly match the box region is resampled to fit
    rather than misaligned -- an off-by-one here smears the whole matte."""
    crop_ignore = np.zeros((20, 30), bool)   # a different size than the 40x40 box
    ignore = MK._gdino_compose(100, 200, 40, 20, 80, 60, crop_ignore)
    assert ignore.shape == (100, 200)
    assert not ignore[30, 60]           # an all-False matte stays kept inside
    assert ignore[5, 5]                 # outside is still ignored


def test_gdino_mask_crops_mattes_and_composes():
    """The whole gdino path with the two torch calls stubbed out: the box is
    padded, BiRefNet mattes only the crop, and the result is pasted back into a
    full-frame ignore mask that ignores everything outside the box."""
    from pc import birefnet as BN
    h, w = 100, 200
    bgr = np.zeros((h, w, 3), np.uint8)
    orig_box, orig_fg = MK.gdino_box, BN.foreground
    try:
        MK.gdino_box = lambda img, prompt, d: ((40, 20, 80, 60), 0.9)

        def fake_foreground(crop, weights, device="", res=0):
            ch, cw = crop.shape[:2]
            fg = np.ones((ch, cw), np.float32) * 0.9   # foreground everywhere
            fg[:, :10] = 0.1                           # left strip is background
            return fg

        BN.foreground = fake_foreground
        st = Settings().replace(mask_mode="gdino", birefnet_model="x.safetensors",
                                gdino_prompt="building", birefnet_threshold=0.5,
                                birefnet_shrink_frac=0.0)
        ignore, note = MK.gdino_mask(bgr, st)
    finally:
        MK.gdino_box, BN.foreground = orig_box, orig_fg
    assert ignore.shape == (h, w)
    # the box (40,20,80,60) padded by 4% of the frame (8 px x, 4 px y) is
    # rows[16:64], cols[32:88]; the crop's background strip is its left 10 cols
    assert ignore[0, 0] and ignore[99, 199] and ignore[10, 50]   # outside
    assert ignore[40, 35]          # frame col 35 -> crop col 3  -> background
    assert not ignore[40, 75]      # frame col 75 -> crop col 43 -> foreground
    assert "GDINO" in note and "building" in note


def test_gdino_mask_refuses_a_box_too_small_to_matte():
    """A detection so small that padding cannot make it matte-able is refused,
    not run through BiRefNet at a size the network was never trained on."""
    from pc import birefnet as BN
    orig_box = MK.gdino_box
    try:
        MK.gdino_box = lambda img, prompt, d: ((0, 0, 3, 3), 0.9)
        st = Settings().replace(mask_mode="gdino", birefnet_model="x.safetensors")
        tiny = np.zeros((6, 12, 3), np.uint8)     # w=12 -> pad is 0 px
        try:
            MK.gdino_mask(tiny, st)
        except ValueError as e:
            assert "too small" in str(e)
        else:
            raise AssertionError("a tiny box must be refused")
    finally:
        MK.gdino_box = orig_box


def test_build_routes_gdino_to_the_matte():
    """``build`` is the seam the pipeline calls; gdino must reach gdino_mask."""
    calls = {}

    def fake(bgr, settings):
        calls["n"] = 1
        return np.zeros(bgr.shape[:2], bool), "gdino note"

    orig = MK.gdino_mask
    try:
        MK.gdino_mask = fake
        m, note = MK.build(np.zeros((30, 40, 3), np.uint8),
                           Settings().replace(mask_mode="gdino"))
    finally:
        MK.gdino_mask = orig
    assert calls.get("n") == 1 and note == "gdino note" and m.shape == (30, 40)


def test_gdino_available_is_false_without_the_weights():
    """No weights directory means the mode is unavailable, whatever torch can do."""
    with tempfile.TemporaryDirectory() as d:
        assert MK.gdino_available(os.path.join(d, "does-not-exist")) is False
