"""The SAM2 seam that runs without SAM2.

This file was committed empty in `843a76b` -- the commit that introduced the
feature -- and stayed 0 bytes.  pytest collected it and reported nothing, so a
352-line module with a subprocess boundary in it has looked covered ever since
while nothing at all ran.  An empty test file is worse than a missing one: the
missing one is visible.

What can be tested here is everything on this side of the subprocess: the
config name derived from a checkpoint filename, the quoting that keeps a
Windows path from closing the child's string literal, and the polarity
conversion on the way back.  The model itself is not run -- `available()` says
when it could be, and that is the point of the split.
"""
import os
import tempfile

import cv2
import numpy as np

from pc import sam2seg as S


def test_the_config_name_is_read_off_the_checkpoint_not_hardcoded():
    """The child asked for a SAM 2.0 config while the shipped checkpoint was
    2.1, and torch rejected the mismatch inside the subprocess with three
    unexpected state_dict keys -- where the traceback went nowhere.  Both the
    version and the size live in the checkpoint's own filename."""
    assert S.config_for("sam2.1_hiera_base_plus.pt") == "sam2.1/sam2.1_hiera_b+"
    assert S.config_for("sam2.1_hiera_large.pt") == "sam2.1/sam2.1_hiera_l"
    assert S.config_for("sam2.1_hiera_small.pt") == "sam2.1/sam2.1_hiera_s"
    assert S.config_for("sam2.1_hiera_tiny.pt") == "sam2.1/sam2.1_hiera_t"
    assert S.config_for("sam2_hiera_large.pt") == "sam2/sam2_hiera_l"
    # a full path, and an unknown size: base_plus is the documented default
    assert S.config_for(os.path.join("models", "sam2", "sam2.1_hiera_weird.pt")) \
        == "sam2.1/sam2.1_hiera_b+"


def test_a_windows_path_survives_being_written_into_the_child_source():
    """The child receives its inputs as Python source, so a backslash or a
    quote would close the literal early and kill it with a syntax error that
    names no file -- which is how the predecessor's "cannot read" failure
    looked.  Round-trip through `eval` is the only check that means anything:
    the escaped text has to read back as the original string."""
    for raw in (r"D:\Coding\Fotos\haus.jpg",
                r"C:\Users\blasa\AppData\Local\Temp\x'y.jpg",
                "/home/a/b c/d.jpg",
                "D:\\ends\\with\\backslash\\"):
        lit = "'" + S._q(raw) + "'"
        assert eval(lit) == raw, f"{raw!r} does not survive {lit!r}"


def test_the_mask_comes_back_inverted_because_sam_marks_the_selection():
    """SAM2 writes white where the building IS; this project's convention is
    white means IGNORE (`masks.load`).  The conversion belongs in one place,
    and getting it backwards would mask exactly the facade being measured."""
    with tempfile.TemporaryDirectory() as d:
        p = S.mask_png_path("haus", d)
        assert os.path.isdir(d) and p.endswith("haus.png")
        sel = np.zeros((40, 60), np.uint8)
        sel[:, :30] = 255                      # SAM says: the left half is it
        cv2.imwrite(p, sel)

        out = S.load_mask_png(p, (40, 60))
        assert out.dtype == bool
        assert not out[:, :30].any(), "the selected half is what we keep"
        assert out[:, 30:].all(), "and the rest is what we ignore"


def test_a_stale_mask_written_at_another_size_is_resized_not_refused():
    """The fit runs at analysis resolution, which changes with `detect_max_edge`.
    A PNG from an earlier run would otherwise crash the indexing rather than
    line up."""
    with tempfile.TemporaryDirectory() as d:
        p = S.mask_png_path("haus", d)
        sel = np.zeros((80, 120), np.uint8)
        sel[:, :60] = 255
        cv2.imwrite(p, sel)
        out = S.load_mask_png(p, (40, 60))     # half the size it was written at
        assert out.shape == (40, 60)
        assert not out[:, :30].any() and out[:, 30:].all()


def test_an_unreadable_mask_is_reported_with_its_path():
    """A child that wrote nothing must not surface as a `None` three calls
    later; the error names the file."""
    try:
        S.load_mask_png(os.path.join(tempfile.gettempdir(), "no_such_mask.png"),
                        (10, 10))
    except ValueError as exc:
        assert "no_such_mask.png" in str(exc)
    else:
        raise AssertionError("a missing mask has to raise, not return None")


def test_availability_names_the_two_halves_separately():
    """Neither import is attempted -- importing torch in the GUI's Python is
    what this module exists to avoid -- so availability is two file checks, and
    a missing either one is False rather than an exception."""
    assert S.available(python_exe="definitely_not_a_python", ckpt=__file__) is False
    assert S.available(python_exe=__file__, ckpt="definitely_not_a_checkpoint") is False
    assert S.available(python_exe=__file__, ckpt=__file__) is True


def _stub_sam2(folder):
    """torch, hydra and sam2 as stand-ins the child can import: the predictor
    selects the left half.  They go beside the child script, which Python puts
    first on the child's path."""
    files = {
        "torch/__init__.py": "class cuda:\n    @staticmethod\n    def is_available():\n        return False\n",
        "hydra/__init__.py": "def initialize_config_dir(config_dir=None, version_base=None):\n    pass\n",
        "hydra/core/__init__.py": "",
        "hydra/core/global_hydra.py": (
            "class GlobalHydra:\n    @staticmethod\n    def instance():\n"
            "        return GlobalHydra()\n    def clear(self):\n        pass\n"),
        "sam2/__init__.py": "",
        "sam2/build_sam.py": "def build_sam2(name, ckpt, device=None):\n    return object()\n",
        "sam2/sam2_image_predictor.py": (
            "import numpy as np\n"
            "class SAM2ImagePredictor:\n"
            "    def __init__(self, model):\n        pass\n"
            "    def set_image(self, rgb):\n        self.shape = rgb.shape[:2]\n"
            "    def predict(self, **kw):\n"
            "        m = np.zeros((1,) + self.shape, bool)\n"
            "        m[:, :, : self.shape[1] // 2] = True\n"
            "        return m, np.ones(1), None\n"),
    }
    for rel, text in files.items():
        p = os.path.join(folder, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)


def test_the_child_reads_and_writes_a_photograph_with_an_umlaut_in_its_name():
    """The child opened the photograph with cv2.imread and wrote the mask with
    cv2.imwrite; on Windows both fail on a non-ASCII path, so SAM2 failed on
    every such photograph (OpenCode review, 2026-10-24).  Runs the real child
    script in this interpreter against stand-in packages."""
    import sys
    tmp = tempfile.mkdtemp()
    src = os.path.join(tmp, "Fassade_Größe.jpg")
    cv2.imencode(".jpg", np.full((60, 80, 3), 128, np.uint8))[1].tofile(src)
    out_dir = os.path.join(tmp, "sam2_masks")
    os.makedirs(out_dir)
    _stub_sam2(out_dir)
    ckpt = os.path.join(tmp, "sam2.1_hiera_base_plus.pt")
    open(ckpt, "wb").close()
    png = S.run_subprocess(src, (5, 5, 70, 50), [], python_exe=sys.executable,
                           out_dir=out_dir, ckpt=ckpt, cfgdir=tmp, timeout=120)
    ignore = S.load_mask_png(png, (60, 80))
    assert ignore.shape == (60, 80)
    assert not ignore[:, :40].any() and ignore[:, 40:].all(), "left half selected"
