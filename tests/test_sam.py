"""The SAM mask source.

SAM itself cannot run here -- no torch, no checkpoints, no GPU -- so these cover
what is testable without it: checkpoint identification, the diagnostics that
tell a user why a particular file will not work, and the line-density scoring
that turns SAM's boundaries into a decision. The scoring is the part that
actually matters and it is pure numpy.
"""
import os
import tempfile

import numpy as np

from bpc import sam as S


def _fake(dirpath, name, size):
    p = os.path.join(dirpath, name)
    with open(p, "wb") as fh:
        fh.write(b"\0")
    os.truncate(p, size)
    return p


def test_checkpoint_generation_is_read_from_the_name():
    assert S._kind("sam2.1_hiera_tiny-fp16.safetensors") == "sam2"
    assert S._kind("sam3_base.pt") == "sam3"
    assert S._kind("sam_vit_b_01ec64.pth") == "sam1"
    assert S._kind("sam_hq_vit_l.pth") == "sam1"
    assert S._kind("mobile_sam.pt") == "sam1"


def test_describe_warns_about_the_look_alikes():
    """A ComfyUI sams folder is full of files that fail differently -- HQ
    variants, safetensors, and stubs far too small to be what they are named
    after. Each needs a different answer, so each must be named."""
    with tempfile.TemporaryDirectory() as d:
        assert "segment-anything-hq" in S.describe(_fake(d, "sam_hq_vit_b.pth", 379_000_000))
        assert "converted" in S.describe(_fake(d, "sam2.1_hiera_tiny-fp16.safetensors", 78_000_000))
        assert "suspiciously small" in S.describe(_fake(d, "mobile_sam.pt", 129_341))
        plain = S.describe(_fake(d, "sam_vit_b_01ec64.pth", 375_000_000))
        assert "suspiciously" not in plain and "hq" not in plain.lower()
        assert "no text prompting" in plain
        assert "no text prompting" not in S.describe(_fake(d, "sam3_base.pt", 900_000_000))


def test_a_missing_checkpoint_is_reported_clearly():
    try:
        S._load("/definitely/not/a/checkpoint.pt")
    except S.SAMUnavailable as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("a missing checkpoint must raise SAMUnavailable")


def test_line_density_separates_a_facade_from_foliage():
    """The whole semantics layer in one number: SAM gives the boundaries, the
    line detector says which of them is a building."""
    h, w = 200, 200
    facade = np.zeros((h, w), bool); facade[:, :100] = True
    canopy = np.zeros((h, w), bool); canopy[:, 100:] = True
    seg = np.array([[10. + 8 * i, 10., 10. + 8 * i, 190.] for i in range(10)])
    d_facade = S.line_density(facade, seg)
    d_canopy = S.line_density(canopy, seg)
    assert d_facade > 0
    assert d_canopy == 0.0
    assert d_facade > 10 * max(d_canopy, 1e-9)


def test_line_density_is_zero_without_lines_or_area():
    empty = np.zeros((50, 50), bool)
    assert S.line_density(empty, np.array([[0., 0., 0., 40.]])) == 0.0
    full = np.ones((50, 50), bool)
    assert S.line_density(full, np.zeros((0, 4))) == 0.0


def test_density_scales_with_line_length_not_count():
    """A facade seen small still has long lines relative to its area, which is
    why the threshold is relative to the densest region in the same picture."""
    region = np.ones((200, 200), bool)
    short = np.array([[10., 10., 10., 30.]] * 4)
    long_ = np.array([[10., 10., 10., 190.]])
    assert S.line_density(region, long_) > S.line_density(region, short)


def test_backends_reports_what_is_importable():
    b = S.backends()
    assert set(b) >= {"ultralytics", "sam2", "segment_anything", "torch"}
    assert all(isinstance(v, bool) for v in b.values())


def test_the_install_hint_leads_with_the_command():
    """The first version of this message opened with three package names and
    their licences, and the actual instruction was off the end of the GUI
    label. Nothing installed and 'installed but cannot read this file' are
    different problems and must read differently."""
    missing = S._install_hint("/x/sam_vit_b.pth", ["ultralytics: No module named 'ultralytics'",
                                                  "sam2: No module named 'sam2'",
                                                  "sam1: No module named 'segment_anything'"])
    assert missing.startswith("no SAM backend installed")
    assert "pip install segment-anything" in missing
    assert "python_embeded" in missing            # reuse ComfyUI's torch
    broken = S._install_hint("/x/sam_vit_b.pth", ["sam1: size mismatch for image_encoder"])
    assert broken.startswith("a SAM backend is installed")
    assert "size mismatch" in broken


def test_the_hint_names_the_package_that_fits_the_checkpoint():
    sam1 = S._install_hint("/x/sam_vit_b_01ec64.pth", ["a: No module named 'x'"])
    sam2 = S._install_hint("/x/sam2.1_hiera_tiny.pt", ["a: No module named 'x'"])
    assert "segment-anything" in sam1
    assert "ultralytics" in sam2


def test_backends_reports_tkinter_too():
    """The interpreter split is the whole problem: ComfyUI's embedded Python has
    torch and SAM but no tkinter, the system Python is the other way round.
    Reporting only the SAM half hides half the diagnosis."""
    b = S.backends()
    assert "tkinter" in b and "torch" in b


def test_export_writes_one_mask_per_photo_and_survives_failures():
    """The bridge across that split, so nobody has to choose between the
    segmenter and the review window."""
    import cv2
    from bpc.config import Settings
    with tempfile.TemporaryDirectory() as d:
        img = os.path.join(d, "a.jpg")
        cv2.imwrite(img, (np.random.default_rng(0).integers(0, 255, (200, 300, 3))
                          ).astype(np.uint8))
        out = os.path.join(d, "masks")
        lines = []
        written, failed = S.export_masks([img], "/no/such/model.pt", out,
                                         Settings(), log=lines.append)
        assert written == 0 and failed == 1        # no model here: must not raise
        assert os.path.isdir(out)
        assert any("ERROR" in l for l in lines)
        assert any("--mask file" in l for l in lines), "must say how to use the folder"
