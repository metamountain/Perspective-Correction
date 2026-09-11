"""Dependency gate: what this interpreter can run, before a batch starts.

The rule under test is the one that keeps a batch from dying on every file with
a traceback: a backend the command line *asked for* must be present or the run
refuses to start, while a run left at the defaults must stay runnable wherever
the core is installed. piexif sits in between -- it degrades (EXIF falls back to
a default focal length), so its absence is reported, never fatal.
"""
import bpc.deps as deps
from bpc import mlsd
from bpc.config import Settings


def _patch(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    return lambda: setattr(obj, name, old)


def test_core_status_has_the_four_packages_with_correct_hardness():
    recs = deps.core_status()
    assert [r["name"] for r in recs] == ["numpy", "cv2", "PIL", "piexif"]
    by = {r["name"]: r for r in recs}
    assert by["piexif"]["hard"] is False          # soft: degrades, never blocks
    for name in ("numpy", "cv2", "PIL"):
        assert by[name]["hard"] is True           # hard: cannot run without it
    for r in recs:
        for key in ("name", "ok", "detail", "pip", "hard", "why"):
            assert key in r


def test_core_errors_ignores_the_soft_package():
    un = _patch(deps, "_probe",
                lambda name: (name != "piexif", "ok" if name != "piexif" else "ModuleNotFoundError"))
    try:
        assert deps.core_errors() == []           # piexif alone must not block a run
    finally:
        un()


def test_core_errors_names_a_missing_hard_package():
    un = _patch(deps, "_probe", lambda name: (name != "numpy", "ok"))
    try:
        errs = deps.core_errors()
        assert len(errs) == 1
        assert "numpy" in errs[0] and "pip install" in errs[0]
    finally:
        un()


def test_preflight_flags_a_requested_backend_that_is_missing():
    s = Settings()
    s.detector = "mlsd"
    un_core = _patch(deps, "core_errors", lambda: [])
    un_mlsd = _patch(mlsd, "available", lambda *a, **k: False)
    try:
        errs = deps.preflight(s)
        assert errs and any("ai-edge-litert" in e for e in errs)
    finally:
        un_core(); un_mlsd()


def test_preflight_passes_on_the_defaults():
    s = Settings()          # auto / telea / off -- nothing heavy requested
    un_core = _patch(deps, "core_errors", lambda: [])
    try:
        assert deps.preflight(s) == []
    finally:
        un_core()


def test_preflight_flags_birefnet_without_weights():
    s = Settings()
    s.mask_mode = "birefnet"
    s.birefnet_model = ""
    un_core = _patch(deps, "core_errors", lambda: [])
    try:
        errs = deps.preflight(s)
        assert any("birefnet-model" in e for e in errs)
    finally:
        un_core()


# --------------------------------------------------------------------------
# packaging: what `pip install` is allowed to pull in
# --------------------------------------------------------------------------
def _pyproject():
    import os
    try:
        import tomllib
    except ModuleNotFoundError:                    # 3.9-3.10
        raise SkipTest("no tomllib before 3.11")   # noqa: F821
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "pyproject.toml"), "rb") as fh:
        return tomllib.load(fh)


def test_no_extra_can_install_a_backend_that_breaks_the_core():
    """The install must not offer a one-command way to break itself.

    ``simple-lama-inpainting`` has to go in with ``--no-deps``: its stale pins
    downgrade Pillow to 9.5 and numpy to 1.26, and OpenCV in the same
    interpreter then stops importing. An extra resolves dependencies normally,
    so ``pip install .[lama]`` would do exactly the damage the manual step
    exists to avoid -- a regression that reads as a convenience. ``ultralytics``
    is the same shape (it replaces ``cv2.imread``).

    This is a packaging decision with no runtime symptom, so nothing else would
    catch it being helpfully "fixed".
    """
    extras = _pyproject()["project"].get("optional-dependencies", {})
    banned = ("simple-lama", "simple_lama", "ultralytics")
    for name, reqs in extras.items():
        for req in reqs:
            low = req.lower()
            assert not any(b in low for b in banned), (
                f"extra [{name}] would install {req!r} with its dependencies; "
                f"it must stay a documented --no-deps step")


def test_the_declared_dependencies_are_the_ones_the_core_check_requires():
    """pyproject and ``deps.core_status`` must name the same four packages.

    They are two statements of one fact -- what this tool needs to run at all --
    and the failure of them drifting apart is quiet: an install that satisfies
    pyproject and then fails the pre-flight, or a pre-flight that passes on an
    interpreter missing something the code imports.
    """
    declared = _pyproject()["project"]["dependencies"]
    # requirement strings -> the import names deps.py reports
    imports = {"numpy": "numpy", "opencv-python": "cv2",
               "opencv-python-headless": "cv2", "pillow": "PIL",
               "piexif": "piexif"}
    got = set()
    for req in declared:
        pkg = req.split(";")[0].split(">=")[0].split("==")[0].strip().lower()
        assert pkg in imports, f"undeclared core dependency {pkg!r}"
        got.add(imports[pkg])
    assert got == {r["name"] for r in deps.core_status()}
