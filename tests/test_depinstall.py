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


def test_the_menu_lists_every_package_with_torch_first_and_says_what_is_installed():
    """The deps menu's rows (user 2026-10-24: "soll nicht blind installieren").
    torch comes before what needs it; GeoCalib counts as installed only with
    its weights complete; every row says what it is for."""
    real, real_w = DI.present, DI.geocalib_weights_ok
    try:
        DI.present = lambda m: False
        rows = DI.catalogue()
        keys = [r["key"] for r in rows]
        assert keys.index("torch") < keys.index("lama") < keys.index("geocalib"), keys
        assert not any(r["installed"] for r in rows) and all(r["purpose"] for r in rows)
        DI.present = lambda m: True
        DI.geocalib_weights_ok = lambda path=None: False
        inst = {r["key"]: r["installed"] for r in DI.catalogue()}
        assert inst["geocalib"] is False and inst["torch"] and inst["lama"], inst
        DI.geocalib_weights_ok = lambda path=None: True
        assert all(r["installed"] for r in DI.catalogue())
        assert [i["key"] for i in DI.in_order([{"key": "dnd"}, {"key": "geocalib"},
                                               {"key": "torch"}])] == ["torch", "geocalib", "dnd"]
    finally:
        DI.present, DI.geocalib_weights_ok = real, real_w


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


def test_the_installer_does_not_call_lama_ok_when_torch_failed():
    """`tools/install_deps.py --lama` installed lama with --no-deps after torch
    had failed and listed it as "optional ok" -- a component that cannot run
    (OpenCode review, 2026-10-24).  The in-app button already skipped it."""
    import importlib.util
    import types
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location(
        "install_deps_probe", os.path.join(here, "..", "tools", "install_deps.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    said, asked = [], []
    mod.say = lambda msg="": said.append(msg)
    mod.MAC, mod.WIN = False, True              # the running Python, no venv
    mod.tk_problem = lambda: None
    mod.has_module = lambda py, name: False
    mod.subprocess = types.SimpleNamespace(call=lambda *a, **k: 0)

    def pip(py, args):
        asked.append(list(args))
        return args != mod.TORCH[1]
    mod.pip = pip
    assert mod.main(["--lama"]) == 0
    assert mod.LAMA[1] not in asked, "pip was asked for a lama that cannot run"
    assert f"optional ok:      {mod.LAMA[0]}" not in said
    assert any(mod.LAMA[0] in s and "MISSING" in s for s in said), said[-6:]


def test_the_weights_count_only_when_complete_and_survive_a_scanner_lock():
    """A short download must never look installed, and a virus scanner holding
    the fresh file (WinError 32 on the first real install) must not fail it."""
    import pathlib
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "w.bin")
        with open(src, "wb") as f:
            f.write(b"x" * 1000)
        url = pathlib.Path(src).as_uri()
        dst = os.path.join(d, "models", "geocalib", "pinhole.tar")
        ok, why = DI.fetch_geocalib_weights(dst, url, size=2000)
        assert not ok and "incomplete" in why and not os.path.exists(dst), why
        assert not os.path.exists(dst + ".part")
        real, calls = os.replace, []

        def locked(a, b):
            calls.append(a)
            if len(calls) < 3:
                raise PermissionError(32, "used by another process")
            return real(a, b)
        DI.os.replace = locked
        try:
            ok, why = DI.fetch_geocalib_weights(dst, url, size=1000)
        finally:
            DI.os.replace = real
        assert ok and os.path.getsize(dst) == 1000 and len(calls) == 3, (ok, why, calls)
        assert DI.fetch_geocalib_weights(dst, url, size=1000) == (True, "")
