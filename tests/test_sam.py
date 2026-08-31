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
