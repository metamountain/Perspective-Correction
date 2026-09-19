"""Dependency gate: what this interpreter can run, before a batch starts.

The rule under test is the one that keeps a batch from dying on every file with
a traceback: a backend the command line *asked for* must be present or the run
refuses to start, while a run left at the defaults must stay runnable wherever
the core is installed. piexif sits in between -- it degrades (EXIF falls back to
a default focal length), so its absence is reported, never fatal.
"""
import pc.deps as deps
from pc import mlsd
from pc.config import Settings


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


def test_the_doctor_agrees_with_mask_info_about_birefnet():
    """Two checks of one fact must not disagree.

    `--doctor` gated BiRefNet on torch alone and reported "[yes] mask birefnet"
    on an interpreter where `--mask-info` correctly said "loads: NO -- No module
    named 'transformers'". The architecture is loaded through `transformers`, so
    torch plus weights is not readiness; a user following the doctor got a green
    light and a failure on the first photograph.

    This asserts the doctor consults `transformers` at all -- the specific thing
    it used to ignore -- rather than re-deriving the verdict, which would just
    be the same mistake written twice.
    """
    from pc import birefnet as BN
    b = BN.backends()
    assert "transformers" in b, "backends() no longer reports transformers"

    report = deps.doctor_text() if hasattr(deps, "doctor_text") else None
    if report is None:
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            deps.doctor()
        report = buf.getvalue()
    line = [ln for ln in report.splitlines() if "mask birefnet" in ln]
    assert line, "the doctor no longer reports birefnet at all"
    line = line[0]
    # Assert the property, not the wording. An earlier version of this test
    # pinned the literal "transformers=" and went red the moment the line was
    # reworded -- the same mistake this file records elsewhere.
    missing = BN.arch_missing()
    if missing:
        assert "[no " in line or "[no]" in line, (
            f"the architecture cannot import {missing} but the doctor says "
            f"yes: {line}")
        # and it must name what is actually absent, so the reader can act
        assert any(m in line for m in missing), (
            f"the doctor does not say which of {missing} is missing: {line}")
    else:
        assert "[yes" in line, "everything imports but the doctor says no: " + line


def test_birefnet_requirements_come_from_the_architecture_source():
    """torch alone was never the requirement, and nor is transformers alone.

    The architecture read from the weights folder does `from transformers
    import PretrainedConfig`, `from timm.models.layers import DropPath`, `from
    einops import rearrange` and uses torch/torchvision throughout. Checking a
    subset passes an interpreter that dies one import later, which is how this
    was found: --doctor checked torch, then torch+transformers, and the real
    list is five names.
    """
    from pc import birefnet as BN
    for mod in ("torch", "torchvision", "transformers", "timm", "einops"):
        assert mod in BN.ARCH_REQUIRES, f"{mod} dropped from ARCH_REQUIRES"
    # the boolean and the list must not drift apart
    assert BN.transformers_available() == (not BN.arch_missing())


def test_the_source_parses_on_the_oldest_python_pyproject_promises():
    """`requires-python` is a promise, and nothing here was checking it.

    The whole project is developed on 3.12, so syntax that a 3.9 interpreter
    rejects -- a `match` statement, or a PEP 604 `X | Y` in a file without
    `from __future__ import annotations` -- is invisible locally and only
    surfaces in CI, on a job nobody reads until something else breaks.

    The floor is read from pyproject rather than written here, so the test
    follows the promise instead of duplicating it: raise `requires-python`
    and this relaxes by itself.

    Two distinct failures are checked, because they fail at different times.
    A `match` statement is a *syntax* error, caught by parsing against the
    older grammar. A PEP 604 union in an annotation is valid syntax at every
    version but is *evaluated* at import time -- and `str | None` only became
    a runtime expression in 3.10 -- so it raises TypeError on import unless
    the module carries the future import that turns annotations into strings.
    Grammar alone would miss the second one entirely.
    """
    import ast
    import os
    import re

    floor = _pyproject()["project"]["requires-python"]
    m = re.search(r"(\d+)\.(\d+)", floor)
    assert m, f"cannot read a version out of requires-python = {floor!r}"
    version = (int(m.group(1)), int(m.group(2)))

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    paths = []
    for sub in ("src", "tests", "tools"):
        for dirpath, dirnames, filenames in os.walk(os.path.join(root, sub)):
            # a vendored third-party checkout is not ours to keep compatible
            dirnames[:] = [d for d in dirnames
                           if d not in ("__pycache__", "DeepLSD")]
            paths += [os.path.join(dirpath, f)
                      for f in filenames if f.endswith(".py")]
    assert len(paths) > 40, f"only found {len(paths)} source files -- walk is wrong"

    bad_syntax, runtime_union = [], []
    for path in paths:
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        try:
            tree = ast.parse(src, filename=rel, feature_version=version)
        except SyntaxError as exc:
            bad_syntax.append(f"{rel}:{exc.lineno}: {exc.msg}")
            continue
        if any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
               and any(a.name == "annotations" for a in n.names) for n in tree.body):
            continue                      # annotations are strings; never evaluated
        def _flag(node, where):
            for sub in ast.walk(node):
                if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr):
                    runtime_union.append(f"{rel}:{sub.lineno}: {where}")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args
                for a in list(args.args) + list(args.kwonlyargs) + list(args.posonlyargs):
                    if a.annotation:
                        _flag(a.annotation, f"annotation of argument {a.arg!r}")
                if node.returns:
                    _flag(node.returns, "return annotation")
            elif isinstance(node, ast.AnnAssign) and node.annotation:
                _flag(node.annotation, "variable annotation")

    assert not bad_syntax, (
        f"pyproject promises {floor}, but these do not parse on "
        f"{version[0]}.{version[1]}:\n  " + "\n  ".join(bad_syntax))
    assert not runtime_union, (
        f"pyproject promises {floor}, and `X | Y` is evaluated at import time "
        f"before 3.10. Add `from __future__ import annotations` to:\n  "
        + "\n  ".join(runtime_union))
