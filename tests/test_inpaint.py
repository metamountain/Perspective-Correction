"""Filling the band the rotation opens up.

The interesting assertions here are not about image quality -- there is no
ground truth for a pixel nobody photographed.  They are about *containment*:
what the fill is allowed to touch, when it is allowed to run at all, and that a
missing backend is an error rather than a quiet no-op.  That is the whole risk
this feature carries in a batch tool.

The LaMa and ComfyUI tests skip when their optional pieces are absent.
"""
import numpy as np

from bpc import inpaint as F
from bpc import warp as W
from bpc.config import Settings


def _hole(h=80, w=120):
    m = np.zeros((h, w), bool)
    m[:, :10] = True                 # a band down the left edge
    return m


def _photo(h=80, w=120):
    rng = np.random.default_rng(7)
    return rng.integers(0, 256, (h, w, 3), dtype=np.uint8)


def test_off_is_the_default():
    """The one setting in this module that is a decision rather than a knob."""
    assert Settings.fill == "none"


def test_the_fill_touches_nothing_that_was_photographed():
    """The feather ramps *inside* the hole, so every pixel outside it survives
    bit for bit -- not approximately, exactly."""
    img = _photo()
    hole = _hole()
    generated = np.full_like(img, 255)
    out = F._composite(img, generated, hole, feather=3)
    assert np.array_equal(out[~hole], img[~hole])
    assert not np.array_equal(out[hole], img[hole])


def test_a_hole_that_is_not_there_is_not_an_error():
    img = _photo()
    st = Settings().replace(fill="lama")
    out, note = F.fill(img, np.zeros(img.shape[:2], bool), st)
    assert np.array_equal(out, img) and "nothing" in note


def test_no_fill_means_no_backend_is_needed():
    img = _photo()
    out, note = F.fill(img, _hole(), Settings())
    assert np.array_equal(out, img) and note == ""


def test_inventing_most_of_the_frame_is_refused():
    """A correction opens a band. A picture that is 60 % generated is not a
    correction any more, whatever the estimator believed."""
    img = _photo()
    hole = np.zeros(img.shape[:2], bool)
    hole[:, :80] = True                                   # two thirds of it
    try:
        F.fill(img, hole, Settings().replace(fill="lama"))
    except F.FillUnavailable as exc:
        assert "fill-max-share" in str(exc)
    else:
        raise AssertionError("a 67 % hole should have been refused")


def test_an_unknown_backend_is_an_error_not_a_pass_through():
    try:
        F.fill(_photo(), _hole(), Settings().replace(fill="stable-something"))
    except F.FillUnavailable:
        pass
    else:
        raise AssertionError("an unknown fill mode must not silently do nothing")


def test_the_shipped_workflow_has_the_sockets_the_code_fills_in():
    """The contract with ComfyUI is three node titles. If the bundled graph
    loses them, every fill fails at run time with a server error instead of
    here."""
    wf = F.load_workflow()
    for title in (F.TITLE_IMAGE, F.TITLE_MASK):
        assert F._find(wf, title) is not None, f"no node titled {title}"
    n = F._find(wf, F.TITLE_IMAGE)
    assert "image" in wf[n]["inputs"], "BPC_IMAGE must be a LoadImage-shaped node"


def test_an_editor_export_is_rejected_with_the_fix_in_the_message():
    """The single most likely user error: ComfyUI's 'Save' and 'Save (API
    format)' produce different JSON and only the second one can be posted."""
    import json
    import os
    import tempfile
    editor = {"last_node_id": 12, "last_link_id": 20, "nodes": [], "links": []}
    path = os.path.join(tempfile.gettempdir(), "bpc_editor_export.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(editor, fh)
    try:
        F.load_workflow(path)
    except F.FillUnavailable as exc:
        assert "API format" in str(exc)
    else:
        raise AssertionError("an editor export must not be posted to /prompt")
    finally:
        os.remove(path)


def test_the_hole_the_warp_reports_is_the_one_the_fill_gets():
    """The two halves of the seam agree: `filled_region` marks exactly the
    pixels with no source behind them, and those are what `fill` may touch."""
    import math

    from bpc import geometry as G
    w, h, f = 400, 300, 600.0
    H = W.build(w, h, f, math.radians(3.0), math.radians(6.0))
    plan = W.plan(w, h, H, Settings().replace(crop="none"))
    assert plan is not None
    H_total, ow, oh, _, _ = plan
    hole = W.filled_region(H_total, w, h, ow, oh)
    assert hole.shape == (oh, ow)
    assert hole.any() and not hole.all()
    # the centre of a small rotation is always covered
    assert not hole[oh // 2, ow // 2]
    quad = G.apply_h(H_total, G.image_corners(w, h))
    assert quad.shape == (4, 2)


def test_lama_fills_the_band_and_leaves_the_rest_alone():
    if not F.available("lama"):
        raise SkipTest("simple-lama-inpainting is not installed")     # noqa: F821
    img = _photo(160, 240)
    hole = np.zeros(img.shape[:2], bool)
    hole[:, :20] = True
    out, note = F.fill(img, hole, Settings().replace(fill="lama", fill_max_edge=0))
    assert np.array_equal(out[~hole], img[~hole])
    assert out.shape == img.shape and "lama" in note


def test_telea_fills_the_band_and_leaves_the_rest_alone():
    """The no-model backend must keep the same promise every other one does:
    generated pixels inside the hole, the photograph bit for bit outside it.
    It needs no package, so unlike the LaMa test this one never skips."""
    rng = np.random.default_rng(4)
    img = (rng.random((200, 300, 3)) * 255).astype(np.uint8)
    hole = np.zeros((200, 300), bool)
    hole[:24, :] = True

    out, note = F.fill(img, hole, Settings().replace(fill="telea"))
    assert "telea" in note
    assert np.array_equal(out[40:], img[40:]), "outside the hole nothing may move"
    assert not np.array_equal(out[:24], img[:24]), "inside the hole something must"
