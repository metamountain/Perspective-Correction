"""Loading honours an embedded colour profile (imageio._to_srgb)."""
import os
import struct
import sys
import tempfile

import numpy as np
from PIL import Image, ImageCms

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from pc import imageio as IO


def _s15(v):
    return struct.pack(">i", int(round(v * 65536)))


def _linear_srgb_icc():
    """A minimal ICC v2 RGB profile: sRGB primaries (D50-adapted), gamma 1.0.

    The same kind of profile as the user's ``sRGB-elle-V2-g10`` (2026-10-24),
    written here so no third-party profile has to ship with the tests."""
    def xyz(x, y, z):
        return b"XYZ " + b"\0" * 4 + _s15(x) + _s15(y) + _s15(z)
    curv = b"curv" + b"\0" * 4 + struct.pack(">I", 1) + struct.pack(">H", 0x0100) + b"\0\0"
    desc_txt = b"linear sRGB test\0"
    desc = (b"desc" + b"\0" * 4 + struct.pack(">I", len(desc_txt)) + desc_txt
            + b"\0" * 8 + b"\0" * 3 + b"\0" * 67)
    tags = [(b"desc", desc), (b"wtpt", xyz(0.9642, 1.0, 0.8249)),
            (b"rXYZ", xyz(0.4361, 0.2225, 0.0139)), (b"gXYZ", xyz(0.3851, 0.7169, 0.0971)),
            (b"bXYZ", xyz(0.1431, 0.0606, 0.7141)),
            (b"rTRC", curv), (b"gTRC", curv), (b"bTRC", curv)]
    off = 128 + 4 + 12 * len(tags)
    table, data = b"", b""
    for sig, body in tags:
        while (off + len(data)) % 4:
            data += b"\0"
        table += sig + struct.pack(">II", off + len(data), len(body))
        data += body
    size = off + len(data)
    header = (struct.pack(">I", size) + b"\0" * 4 + struct.pack(">I", 0x02100000)
              + b"mntrRGB XYZ " + b"\0" * 12 + b"acsp" + b"\0" * 28
              + _s15(0.9642) + _s15(1.0) + _s15(0.8249) + b"\0" * 48)
    assert len(header) == 128
    return header + struct.pack(">I", len(tags)) + table + data


def _write(arr, icc):
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    Image.fromarray(arr).save(path, icc_profile=icc)
    return path


def test_a_linear_profile_is_converted_so_the_photo_is_not_shown_too_dark():
    """User, 2026-10-24: "wajavilla wird zu dunkel dargestellt" -- the file is
    tagged gamma 1.0, and its raw values were shown as if they were sRGB."""
    arr = np.full((8, 8, 3), 50, np.uint8)
    path = _write(arr, _linear_srgb_icc())
    try:
        got = IO.load(path)
    finally:
        os.remove(path)
    # linear 50/255 = 0.196 -> sRGB ~ 0.48 -> ~123
    assert 110 < got.bgr.mean() < 135, got.bgr.mean()
    desc = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(
        __import__("io").BytesIO(got.icc)))
    assert "sRGB" in desc, ("the save would embed a profile that no longer fits", desc)


def test_an_srgb_profile_leaves_the_pixels_untouched():
    rng = np.random.default_rng(3)
    arr = rng.integers(0, 256, (16, 16, 3), dtype=np.uint8)
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    path = _write(arr, icc)
    try:
        got = IO.load(path)
    finally:
        os.remove(path)
    assert np.array_equal(got.bgr[..., ::-1], arr)
    assert got.icc == icc
