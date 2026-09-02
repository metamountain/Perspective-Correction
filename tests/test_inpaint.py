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


def test_comfyui_gets_a_primed_band_not_a_padded_one():
    """What a generator sees in the hole must be neither black nor the
    resampler's streaks.  Both are bad starting points in opposite ways: black
    reads as content, and the streaks are the artifact that cost this project
    two wrong conclusions.  Primed with TELEA for colour, then pulled halfway to
    grey so the structure TELEA invents alongside it does not survive.
    """
    import cv2
    import numpy as np
    from bpc import inpaint as FILL

    rng = np.random.default_rng(3)
    img = cv2.GaussianBlur((rng.random((200, 300, 3)) * 255).astype(np.uint8), (31, 31), 0)
    mask = np.zeros((200, 300), np.uint8)
    mask[:30, :] = 255
    inside = mask > 0

    out = FILL._prime_for_generation(img, mask)
    assert np.array_equal(out[~inside], img[~inside]), "outside the hole is untouched"
    assert not np.array_equal(out[inside], img[inside]), "inside the hole is primed"
    assert abs(float(out[inside].mean()) - FILL.PRIME_GREY) < 12, "pulled towards grey"
    plain = cv2.inpaint(img, mask, 3, cv2.INPAINT_TELEA)
    assert out[inside].std() < plain[inside].std(), "grey flattens telea's invented structure"
    assert np.array_equal(FILL._prime_for_generation(img, np.zeros_like(mask)), img), \
        "no hole, no work"


def test_a_workflow_without_a_mask_node_is_an_edit_model_not_an_error():
    """A whole family of models takes an image and an instruction and has
    nowhere to put a mask.  For those the *priming* is the signal -- the band
    arrives as flat grey-tinted colour and the prompt says to replace it -- so a
    missing ``BPC_MASK`` is a mode, not a fault.  ``BPC_IMAGE`` stays required,
    because without it there is nothing to send.

    The containment promise does not rest on the workflow honouring a mask in
    any case: ``_composite`` puts the result back through the hole and nowhere
    else, so a model that repaints the whole frame still cannot touch a
    photographed pixel.  That is asserted by
    ``test_the_fill_touches_nothing_that_was_photographed``.
    """
    import json
    import os
    import tempfile
    from bpc.config import Settings
    from bpc import inpaint as FILL

    edit = {"1": {"class_type": "LoadImage", "inputs": {"image": "x.png"},
                  "_meta": {"title": FILL.TITLE_IMAGE}},
            "2": {"class_type": "FluxEdit",
                  "inputs": {"text": "remove grey border", "image": ["1", 0]},
                  "_meta": {"title": FILL.TITLE_PROMPT}},
            "3": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]},
                  "_meta": {"title": "out"}}}

    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "edit.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(edit, fh)
        s = Settings().replace(fill="comfyui", comfy_workflow=p)
        text = FILL.describe("comfyui", s)
        assert "edit-model mode" in text, text
        assert "REQUIRED" not in text, "a missing mask must not read as a fault"

        del edit["1"]
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(edit, fh)
        assert "BPC_IMAGE is REQUIRED" in FILL.describe("comfyui", s)


def test_the_shipped_edit_workflow_carries_the_titles_the_code_writes_into():
    """The maskless workflow is a real ComfyUI graph, flattened out of a
    subgraph export, so it is exactly the kind of file that rots silently: a
    rename in the editor and BPC would upload into nothing.  Same guard the
    inpainting workflow already has, plus the assertion that this one has *no*
    mask node -- that absence is the feature, not an oversight.
    """
    import os
    from bpc import inpaint as FILL

    path = os.path.join(os.path.dirname(FILL.DEFAULT_WORKFLOW),
                        "flux2-klein-edit-nomask.json")
    assert os.path.isfile(path), "the edit-model workflow ships with the project"
    wf = FILL.load_workflow(path)          # also asserts it is the API format
    assert FILL._find(wf, FILL.TITLE_IMAGE), "BPC_IMAGE is where the photo goes"
    assert FILL._find(wf, FILL.TITLE_PROMPT), "BPC_PROMPT is the instruction"
    assert FILL._find(wf, FILL.TITLE_MASK) is None, \
        "an edit model takes no mask; if this ever gains one, say why"
    assert any(n.get("class_type") == "SaveImage" for n in wf.values()), \
        "without an output node the run completes and returns nothing"


def test_the_server_address_survives_being_taken_apart_and_put_together():
    """The window edits host and port as two fields, because the port is the
    half that gets changed -- a second instance, a tunnel, a container -- and
    hunting for it inside a URL is how it gets mistyped.  Only the joined form
    is ever stored, so the split has to be lossless for anything a user might
    have typed, and forgiving of what they are halfway through typing.
    """
    from bpc import inpaint as FILL

    for url in ("http://127.0.0.1:8188", "https://box.local:9000",
                "http://127.0.0.1", "http://[::1]:8188"):
        host, port = FILL.split_url(url)
        assert FILL.join_url(host, port) == url, url

    assert FILL.join_url("myserver", "8188") == "http://myserver:8188", "scheme filled in"
    assert FILL.split_url("127.0.0.1:8188") == ("127.0.0.1", "8188")
    assert FILL.join_url("http://a", "not-a-port") == "http://a", "junk port dropped"
    assert FILL.split_url("") == ("http://127.0.0.1", "8188"), "empty falls back"
