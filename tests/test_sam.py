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


def test_the_hint_adapts_to_which_interpreter_you_are_in():
    """The two failures look identical and have opposite answers: the Python
    with the GUI is usually not the one to install a multi-gigabyte CUDA torch
    into, because the machine already has one in ComfyUI."""
    errs = ["ultralytics: No module named 'ultralytics'",
            "sam2: No module named 'sam2'",
            "sam1: No module named 'segment_anything'"]
    real = S.backends
    try:
        S.backends = lambda: {"tkinter": True, "torch": False}
        gui = S._install_hint("/m/sam_vit_b.pth", errs)
        assert "--sam-export" in gui, "the GUI python must be offered the export route"
        assert gui.index("--sam-export") < gui.index("pip install"), \
            "the export route must come first there"
        S.backends = lambda: {"tkinter": False, "torch": False}
        headless = S._install_hint("/m/sam_vit_b.pth", errs)
        assert "pip install" in headless and "--sam-export" not in headless
    finally:
        S.backends = real


def test_a_blank_wall_panel_is_not_mistaken_for_foliage():
    """The bug a screenshot exposed: SAM masked the stucco panels between the
    windows of a building it was meant to be measuring.

    A flat surface contains no straight lines -- its edges are the window frames
    and floor bands *around* it -- so scoring only the interior scored the wall
    of a building as if it were a tree."""
    import cv2
    h = w = 400
    panel = np.zeros((h, w), bool)
    panel[120:280, 120:280] = True
    bounding = np.array([[120., 120., 280., 120.], [120., 280., 280., 280.],
                         [120., 120., 120., 280.], [280., 120., 280., 280.]])
    crown = np.zeros((h, w), np.uint8)
    rng = np.random.default_rng(0)
    for _ in range(120):
        cv2.circle(crown, (int(rng.uniform(300, 370)), int(rng.uniform(30, 100))),
                   int(rng.uniform(6, 18)), 255, -1)
    crown = crown.astype(bool)

    d_panel = S.line_density(panel, bounding, margin=5)
    d_crown = S.line_density(crown, bounding, margin=5)
    assert d_panel > S.line_density(panel, bounding, margin=0), \
        "the margin must let a panel claim the lines that bound it"
    assert d_crown == 0.0
    assert S.boundary_straightness(panel) > 0.9
    assert S.boundary_straightness(crown) < 0.6


def test_straightness_alone_rescues_a_surface_with_no_lines_near_it():
    """The second signal needs no lines at all: SAM's own boundary is evidence.
    A wall is bounded by a handful of straight edges, a tree crown by a fractal
    outline no small number of segments approximates."""
    import cv2
    plain = np.zeros((300, 300), bool)
    plain[50:250, 50:250] = True
    assert S.boundary_straightness(plain) > 0.9
    ragged = np.zeros((300, 300), np.uint8)
    rng = np.random.default_rng(3)
    for _ in range(150):
        cv2.circle(ragged, (int(rng.uniform(80, 220)), int(rng.uniform(80, 220))),
                   int(rng.uniform(5, 20)), 255, -1)
    assert S.boundary_straightness(ragged.astype(bool)) < 0.6


def test_straightness_of_nothing_is_zero():
    assert S.boundary_straightness(np.zeros((50, 50), bool)) == 0.0


def test_find_checkpoint_prefers_what_actually_works():
    """Batch files cannot do this reliably -- the version that tried grew a line
    continuation inside a parenthesised block, which cmd runs as a command -- so
    the search lives in Python where it can be tested.

    Preference is for what works over what is largest: HQ checkpoints need
    another package, safetensors need converting, and ViT-H buys nothing for a
    mask resampled to a few hundred pixels."""
    with tempfile.TemporaryDirectory() as d:
        sams = os.path.join(d, "ComfyUI", "models", "sams")
        os.makedirs(sams)
        for n, sz in (("mobile_sam.pt", 129_341),
                      ("sam2.1_hiera_tiny-fp16.safetensors", 77_980_668),
                      ("sam_hq_vit_b.pth", 379_335_069),
                      ("sam_vit_b_01ec64.pth", 375_042_383),
                      ("sam_vit_h_4b8939.pth", 2_564_550_879)):
            _fake(sams, n, sz)
        chosen = S.find_checkpoint(sams)
        assert os.path.basename(chosen) == "sam_vit_b_01ec64.pth"


def test_find_checkpoint_skips_files_too_small_to_be_real():
    with tempfile.TemporaryDirectory() as d:
        _fake(d, "sam_vit_b_stub.pth", 129_341)          # named right, far too small
        _fake(d, "sam_vit_l_0b3195.pth", 1_249_524_607)
        assert os.path.basename(S.find_checkpoint(d)) == "sam_vit_l_0b3195.pth"


def test_find_checkpoint_returns_empty_when_there_is_nothing():
    with tempfile.TemporaryDirectory() as d:
        assert S.find_checkpoint(d) == ""


def _seven_category_scene():
    """A scene holding the four things to reject and the three to keep."""
    import cv2
    h, w = 500, 700
    rng = np.random.default_rng(0)
    img = np.zeros((h, w, 3), np.uint8)
    reg = {}

    def box(y0, y1, x0, x1):
        m = np.zeros((h, w), bool)
        m[y0:y1, x0:x1] = True
        return m

    img[0:150, :] = (235, 215, 190)                       # bright smooth sky
    reg["sky"] = box(0, 150, 0, w)
    veg = np.zeros((h, w), np.uint8)
    for _ in range(400):
        cv2.circle(veg, (int(rng.uniform(430, 690)), int(rng.uniform(160, 330))),
                   int(rng.uniform(4, 16)), 255, -1)
    img[veg > 0] = (60, 120, 55)
    for _ in range(300):
        x, y = int(rng.uniform(430, 690)), int(rng.uniform(160, 330))
        if veg[y, x]:
            cv2.circle(img, (x, y), 1, (30, 80, 25), -1)
    reg["vegetation"] = veg.astype(bool)
    img[400:500, :] = (95, 95, 100)
    img[400:500, :] += rng.integers(-14, 14, (100, w, 3)).astype(np.uint8)
    reg["street"] = box(400, 500, 0, w)
    img[330:400, :] = (150, 145, 140)
    img[330:400, :] += rng.integers(-25, 25, (70, w, 3)).astype(np.uint8)
    reg["floor"] = box(330, 400, 0, w)

    img[150:330, 60:400] = (185, 180, 172)
    reg["house wall"] = box(150, 330, 60, 400)
    for wx in (100, 190, 280):
        cv2.rectangle(img, (wx, 180), (wx + 55, 250), (70, 70, 75), -1)
    reg["window"] = box(180, 250, 100, 155)
    reg["facade panel"] = box(255, 325, 200, 390)         # blank: no lines inside

    seg = []
    for wx in (100, 190, 280):
        seg += [[wx, 180, wx + 55, 180], [wx, 250, wx + 55, 250],
                [wx, 180, wx, 250], [wx + 55, 180, wx + 55, 250]]
    seg += [[60, 150, 400, 150], [60, 330, 400, 330],
            [60, 150, 60, 330], [400, 150, 400, 330]]
    seg += [[60, 255, 400, 255], [200, 255, 200, 325], [390, 255, 390, 325]]
    return img, reg, np.array(seg, float)


def test_the_classifier_separates_clutter_from_architecture():
    """Positive against negative, as the categories are actually named: sky,
    vegetation, street and floor must go; wall, window and panel must stay."""
    import math
    img, regions, seg = _seven_category_scene()
    margin = max(4, int(round(0.012 * math.hypot(*img.shape[:2]))))
    sigs = {k: S.region_signals(m, seg, img, margin) for k, m in regions.items()}
    keep_density = max(s["density"] for s in sigs.values()) * 0.25
    reject = {"sky", "vegetation", "street", "floor"}
    for name, sig in sigs.items():
        verdict, why = S.classify_region(sig, keep_density)
        want = "reject" if name in reject else "keep"
        assert verdict == want, f"{name}: got {verdict} ({why}), wanted {want}"


def test_a_weak_positive_cannot_overrule_a_clear_negative():
    """Order is as load-bearing as the tests themselves. Sky and asphalt are
    routinely bounded by perfectly straight edges -- a roofline, a kerb -- and an
    earlier arrangement kept both as 'built (straight outline)'."""
    sky = {"area": 0.3, "cy": 0.15, "green": -20.0, "value": 235.0, "sat": 30.0,
           "grad": 3.3, "density": 1.2, "straight": 1.0, "mix": 0.0}
    assert S.classify_region(sky, keep_density=5.0)[0] == "reject"
    kerb = {"area": 0.2, "cy": 0.9, "green": -7.0, "value": 104.0, "sat": 20.0,
            "grad": 25.0, "density": 0.0, "straight": 1.0, "mix": 0.0}
    assert S.classify_region(kerb, keep_density=5.0)[0] == "reject"


def test_the_only_signal_buildings_produce_wins_outright():
    """Verticals and horizontals together is a Manhattan object. A fence brings
    one family, foliage brings neither -- so nothing else may overturn it."""
    greenish_facade = {"area": 0.2, "cy": 0.8, "green": 40.0, "value": 120.0,
                       "sat": 90.0, "grad": 30.0, "density": 0.0,
                       "straight": 0.1, "mix": 0.6}
    verdict, why = S.classify_region(greenish_facade, keep_density=5.0)
    assert verdict == "keep" and "verticals and horizontals" in why


def test_an_unrecognised_region_is_kept():
    """Absence of evidence is not evidence of clutter: dropping a facade costs
    the measurement, keeping a stray region costs a little noise."""
    nothing = {"area": 0.05, "cy": 0.5, "green": 0.0, "value": 120.0, "sat": 40.0,
               "grad": 5.0, "density": 0.0, "straight": 0.1, "mix": 0.0}
    verdict, why = S.classify_region(nothing, keep_density=5.0)
    assert verdict == "keep" and "unrecognised" in why
