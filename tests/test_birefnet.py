"""BiRefNet as a mask source.

Nothing here loads the network: the weights are 444 MB, they are not in the
repository, and torch is not a dependency of this project.  What is testable
without them is everything that decides *whether and how* it runs -- which
resolution a checkpoint name implies, whether the architecture sits beside the
weights, what a user is told when it cannot run -- plus the mask contract
itself, exercised through the cached PNGs in ``tests/assets/masks``.

That split is deliberate and is the same one ``--mask-export`` exists to make:
the model runs once, everything downstream consumes a folder of masks.
"""
import os
import sys
import tempfile

import numpy as np

from bpc import birefnet as BN


def test_the_resolution_comes_from_the_checkpoint_name():
    """HR and 2K were trained at 2048 and are visibly worse at 1024; the rest
    gain nothing above it.  Every ComfyUI node guesses this the same way."""
    assert BN.resolution_for("BiRefNet-HR.safetensors") == 2048
    assert BN.resolution_for("BiRefNet_lite-2K.safetensors") == 2048
    assert BN.resolution_for("BiRefNet_lite.safetensors") == 1024
    assert BN.resolution_for(r"C:\models\BiRefNet_dynamic.safetensors") == 1024


def test_the_architecture_is_looked_for_beyond_the_weights_folder():
    """A checkpoint and the file that defines its network are separate things,
    and ComfyUI does not keep them together.

    This was a real failure, reported as "BiRefNet does not work": a remembered
    checkpoint pointed into ``models/BiRefNet``, which held three perfectly good
    checkpoints and no ``birefnet.py``, while ``models/RMBG/BiRefNet`` next door
    held both. The architecture is generic across BiRefNet checkpoints, so
    pairing them is correct rather than a workaround -- and ``load_state_dict``
    catches it at once if it ever is not.
    """
    with tempfile.TemporaryDirectory() as d:
        beside = os.path.join(d, "beside")
        os.makedirs(beside)
        w = os.path.join(beside, "BiRefNet-HR.safetensors")
        open(w, "wb").write(b"0" * 32)
        open(os.path.join(beside, "birefnet.py"), "w").write("# architecture\n")
        assert BN._arch_dir(w) == os.path.abspath(beside), \
            "beside the weights must still win"

    real = BN.architecture_dirs
    try:
        BN.architecture_dirs = lambda: []
        with tempfile.TemporaryDirectory() as d:
            w = os.path.join(d, "BiRefNet-HR.safetensors")
            open(w, "wb").write(b"0" * 32)
            try:
                BN._arch_dir(w)
                assert False, "with no architecture anywhere it must refuse"
            except BN.BiRefNetUnavailable as exc:
                assert "birefnet.py" in str(exc)
            assert "cannot be loaded" in BN.describe(w)
    finally:
        BN.architecture_dirs = real


def test_describe_names_the_size_and_the_resolution():
    with tempfile.TemporaryDirectory() as d:
        w = os.path.join(d, "BiRefNet-HR.safetensors")
        open(w, "wb").write(b"0" * 1024)
        text = BN.describe(w)
        assert "BiRefNet-HR.safetensors" in text and "2048 px" in text
        assert "suspiciously small" in text


def test_a_missing_checkpoint_is_reported_clearly():
    try:
        BN._load(os.path.join(tempfile.gettempdir(), "nope-does-not-exist.safetensors"))
        assert False, "must raise"
    except BN.BiRefNetUnavailable as exc:
        assert "not found" in str(exc)


def test_backends_reports_what_is_importable():
    b = BN.backends()
    assert set(b) >= {"torch", "timm", "transformers", "safetensors", "tkinter"}
    assert all(isinstance(v, bool) for v in b.values())


def test_the_install_hint_adapts_to_which_interpreter_you_are_in():
    """The two failures look identical and have opposite answers: the Python
    with the GUI is usually not the one to install a multi-gigabyte CUDA torch
    into, because the machine already has one in ComfyUI."""
    real = BN.backends
    try:
        BN.backends = lambda: {"torch": False, "timm": False, "transformers": False,
                               "safetensors": False, "torchvision": False,
                               "tkinter": True}
        gui = BN._install_hint()
        BN.backends = lambda: {"torch": False, "timm": False, "transformers": False,
                               "safetensors": False, "torchvision": False,
                               "tkinter": False}
        cli = BN._install_hint()
    finally:
        BN.backends = real
    assert "--mask-export" in gui, "the GUI Python must be offered the export route first"
    assert "pip install" in gui and "pip install" in cli
    assert "--mask-export" not in cli


def test_find_weights_looks_only_where_it_was_told():
    """A hint searches *only* there. The predecessor searched the hint **and**
    every usual location, so asking about one folder could return weights from
    another -- unpredictable in use, and untestable in principle, because the
    answer depended on what the developer's machine happened to have."""
    with tempfile.TemporaryDirectory() as d:
        assert BN.find_weights(d) == ""
        open(os.path.join(d, "BiRefNet-HR.safetensors"), "wb").write(b"0" * 40_000_000)
        assert os.path.basename(BN.find_weights(d)) == "BiRefNet-HR.safetensors"


def test_find_weights_prefers_hr_and_skips_stubs():
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "BiRefNet_lite.safetensors"), "wb").write(b"0" * 30_000_000)
        open(os.path.join(d, "BiRefNet-HR.safetensors"), "wb").write(b"0" * 40_000_000)
        assert os.path.basename(BN.find_weights(d)) == "BiRefNet-HR.safetensors"
        # a file far too small to be the model it claims loses to a real one
        os.remove(os.path.join(d, "BiRefNet-HR.safetensors"))
        open(os.path.join(d, "BiRefNet-HR.safetensors"), "wb").write(b"0" * 1000)
        assert os.path.basename(BN.find_weights(d)) == "BiRefNet_lite.safetensors"


# --------------------------------------------------------------------------
# the cached masks, which is how everything downstream consumes this
# --------------------------------------------------------------------------
_MASKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "masks")


def _cached():
    import glob
    return sorted(glob.glob(os.path.join(_MASKS, "*.png")))


def test_the_cached_masks_mark_what_to_ignore_not_what_to_keep():
    """Polarity is the one thing a mask folder cannot get wrong quietly.

    BiRefNet returns the *subject*; these store the complement, so they work with
    a plain ``--mask file`` and no ``--mask-invert``. Inverted, they would mask
    the building out of its own measurement and the only symptom would be a
    worse answer. The building is the larger, more central object, so an
    inverted cache shows up as the frame edges being kept and the middle thrown
    away.

    A mask covering the *whole* frame is not inverted, it is empty of subject --
    BiRefNet finds no salient object in a street scene photographed down its
    length, and returns 100 %. That is honest output and it is handled at
    runtime rather than here: ``masks.credible`` refuses a mask that takes more
    than 55 % of the line evidence, so such a cache entry is ignored rather than
    obeyed. Asserted below, so the degenerate case stays a known one.
    """
    from bpc import masks as MK
    if not _cached():
        raise SkipTest("no cached masks")                      # noqa: F821
    degenerate = []
    for p in _cached():
        m = MK.load(p, (400, 600, 3))
        h, w = m.shape
        if m.mean() > 0.98:
            degenerate.append(os.path.basename(p))
            continue
        centre = m[h // 3:2 * h // 3, w // 3:2 * w // 3].mean()
        border = np.concatenate([m[0, :], m[-1, :], m[:, 0], m[:, -1]]).mean()
        assert border > centre, (
            f"{os.path.basename(p)} looks inverted: the frame edge should be "
            f"ignored more often than the middle ({border:.2f} vs {centre:.2f})")
    for name in degenerate:
        seg = np.array([[0., 0., 0., 100.]] * 8)
        ok, why = MK.credible(seg, np.zeros((0, 4)))
        assert not ok, f"{name} masks everything and must be refused at runtime"


def test_every_asset_has_a_cached_mask_matched_by_stem():
    """``masks.resolve`` matches on the file stem, so a renamed asset silently
    loses its mask and the run quietly stops masking."""
    import glob

    from bpc import masks as MK
    assets = sorted(glob.glob(os.path.join(os.path.dirname(_MASKS), "*.jpg")))
    if not assets or not _cached():
        raise SkipTest("no assets or no cached masks")         # noqa: F821
    for a in assets:
        got = MK.resolve(_MASKS, a)
        assert os.path.isfile(got), f"no cached mask for {os.path.basename(a)}"


def test_the_cache_is_small_enough_to_live_in_the_repository():
    """Stored at the analysis resolution, not the photograph's -- masks.load
    resamples.  That is what keeps six masks under 100 KB instead of 20 MB."""
    if not _cached():
        raise SkipTest("no cached masks")                      # noqa: F821
    total = sum(os.path.getsize(p) for p in _cached())
    assert total < 300_000, f"cached masks total {total} bytes; are they full size?"
