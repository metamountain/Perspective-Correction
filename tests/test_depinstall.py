"""The `deps` button's logic, against a real pip in a throwaway venv.

Offline on purpose: the package installed is a two-file one written into a
temp folder and handed to pip with --no-index, so CI needs no network and pip
is still the real one -- the same reasoning as test_selfupdate's real git.
"""
import os
import subprocess
import sys
import tempfile
import venv

from pc import depinstall as DI


def _venv(root):
    path = os.path.join(root, "env")
    venv.create(path, with_pip=True)
    py = os.path.join(path, "Scripts" if os.name == "nt" else "bin",
                      "python.exe" if os.name == "nt" else "python")
    return py


def _tiny_wheel(root, name="pc_dep_probe"):
    """A ready wheel, so pip installs it without building anything (a fresh
    3.12 venv has no setuptools, and --no-index cannot fetch one)."""
    import zipfile
    dist = f"{name}-0.1.dist-info"
    files = {
        f"{name}/__init__.py": "VALUE = 42\n",
        f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: 0.1\n",
        f"{dist}/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    files[f"{dist}/RECORD"] = "".join(f"{n},,\n" for n in files) + f"{dist}/RECORD,,\n"
    path = os.path.join(root, f"{name}-0.1-py3-none-any.whl")
    with zipfile.ZipFile(path, "w") as zf:
        for n, text in files.items():
            zf.writestr(n, text)
    return path


def test_install_puts_a_package_into_the_given_python_and_reports_a_failure_plainly():
    with tempfile.TemporaryDirectory() as root:
        py = _venv(root)
        wheel = _tiny_wheel(root)
        good = {"key": "probe", "label": "probe", "module": "pc_dep_probe",
                "pip": ["--no-index", wheel], "size": "0"}
        bad = {"key": "nothing", "label": "nothing", "module": "nope",
               "pip": ["--no-index", "pc-dep-does-not-exist"], "size": "0"}
        steps = []
        results = DI.install([good, bad], py=py, on_step=lambda i, n, l: steps.append((i, n, l)))
        assert steps == [(0, 2, "probe"), (1, 2, "nothing")]
        (_g, ok_g, why_g), (_b, ok_b, why_b) = results
        assert ok_g, why_g
        assert subprocess.call([py, "-c", "import pc_dep_probe; assert pc_dep_probe.VALUE == 42"]) == 0
        assert not ok_b
        assert "no package for Python" in why_b, why_b
        all_ok, text = DI.summary(results)
        assert not all_ok and "FAILED: nothing" in text and "ok: probe" in text


def test_lama_is_skipped_when_torch_cannot_be_had_and_pip_is_not_asked_for_it():
    calls = []
    real_run, real_ti = DI._run, DI.torch_installable
    DI._run = lambda args, timeout=None: (calls.append(args) or (0, ""))
    DI.torch_installable = lambda py=None: False
    try:
        lama = next(p for p in DI.PACKAGES if p["key"] == "lama")
        results = DI.install([DI.TORCH, lama], py=sys.executable)
    finally:
        DI._run, DI.torch_installable = real_run, real_ti
    assert [ok for _i, ok, _r in results] == [False, False]
    assert "no torch for Python" in results[0][2]
    assert "torch is missing" in results[1][2]
    assert calls == [], f"pip was run although torch was unavailable: {calls}"


def test_torch_comes_before_lama_and_only_when_it_is_missing():
    real = DI.present
    try:
        DI.present = lambda m: False
        keys = [i["key"] for i in DI.missing()]
        assert keys.index("torch") == keys.index("lama") - 1, keys
        DI.present = lambda m: m == "torch"
        keys = [i["key"] for i in DI.missing()]
        assert "torch" not in keys and "lama" in keys, keys
        DI.present = lambda m: True
        assert DI.missing() == []
    finally:
        DI.present = real


def test_the_explanations_name_the_cause_a_user_can_act_on():
    assert "venv" in DI.explain("error: externally-managed-environment\n...")
    assert "no package for Python" in DI.explain("ERROR: No matching distribution found for torch")
    assert "permission" in DI.explain("OSError: [Errno 13] Permission denied: '/usr/lib'")
    assert DI.explain("one\nlast line\n") == "last line"


def test_pip_runs_with_the_console_twin_of_pythonw():
    with tempfile.TemporaryDirectory() as d:
        for n in ("python.exe", "pythonw.exe"):
            open(os.path.join(d, n), "w").close()
        assert DI.pip_python(os.path.join(d, "pythonw.exe")) == os.path.join(d, "python.exe")
        assert DI.pip_python(os.path.join(d, "python.exe")) == os.path.join(d, "python.exe")
