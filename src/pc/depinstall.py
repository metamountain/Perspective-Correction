"""Install the optional packages from inside the program (the ``deps`` button).

Kept out of ``gui.py`` for the reason ``selfupdate.py`` is: it can then be
tested without a window. Standard library only, so ``tools/install_deps.py``
can load this file by path and share the list before numpy is even there.

User, 2026-10-23: lama was missing on the first Mac and nothing said so; the
fix is a notice that says so and a button that installs it -- on every OS,
into the interpreter that runs the program (``sys.executable``), which is the
venv on a Mac and the Python on PATH on Windows.

Each package goes in with ``--no-deps`` where CLAUDE.md demands it:
simple-lama's pins downgrade Pillow/numpy and break OpenCV.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import subprocess
import sys

# key, label, import name, pip arguments, rough download size.
PACKAGES = [
    {"key": "lama", "label": "lama fill (simple-lama-inpainting)",
     "module": "simple_lama_inpainting",
     "pip": ["--no-deps", "simple-lama-inpainting"], "size": "small, needs torch"},
    {"key": "mlsd", "label": "mlsd detector (ai-edge-litert)",
     "module": "ai_edge_litert", "pip": ["--no-deps", "ai-edge-litert"], "size": "~10 MB"},
    {"key": "dnd", "label": "drag and drop (tkinterdnd2)",
     "module": "tkinterdnd2", "pip": ["tkinterdnd2>=0.4"], "size": "~1 MB"},
]
# lama imports torch and nothing else of its pins at run time. An installed
# torch is never touched: on Windows it is often a CUDA build that a plain
# `pip install torch` would replace with the CPU one.
TORCH = {"key": "torch", "label": "torch (for lama)", "module": "torch",
         "pip": ["torch"], "size": "~250 MB-2.5 GB"}


def _version():
    return f"{sys.version_info[0]}.{sys.version_info[1]}"


def _hint_312():
    """Only worth saying where it is news."""
    return "" if sys.version_info[:2] == (3, 12) else " (Python 3.12 works)"


def present(module):
    """Installed, without importing it (torch takes seconds to import)."""
    importlib.invalidate_caches()
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def missing():
    """The optional packages not installed, in install order (torch before lama)."""
    out = []
    for pkg in PACKAGES:
        if pkg["key"] == "lama" and not present(TORCH["module"]):
            # lama without torch cannot run on any OS -- the first Mac had
            # exactly that (lama installed, no torch for Python 3.15), and this
            # list used to call it complete because lama itself was present.
            out.append(TORCH)
        if present(pkg["module"]):
            continue
        out.append(pkg)
    return out


def pip_python(exe=None):
    """The interpreter to run pip with.

    The launchers start ``pythonw``; pip works there too, but the console
    twin beside it is the same installation and reports errors normally."""
    exe = exe or sys.executable
    head, tail = os.path.split(exe)
    if tail.lower() == "pythonw.exe":
        twin = os.path.join(head, "python.exe")
        if os.path.exists(twin):
            return twin
    return exe


def _run(args, timeout=None):
    """``(returncode, output)``; never raises, never opens a console window."""
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000           # CREATE_NO_WINDOW
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, timeout=timeout, **kw)
    except Exception as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    return p.returncode, p.stdout.decode("utf-8", "replace")


def torch_installable(py=None):
    """Does pip find a torch for this Python? torch lags new Pythons (none for
    3.15 in 2026-10). ``pip index`` exits 1 when nothing fits; asked rather
    than kept as a version table that goes stale with the next release."""
    code, _out = _run([pip_python(py), "-m", "pip", "index", "versions", "torch"], timeout=120)
    return code == 0


def explain(output):
    """One sentence for a failed pip run, for people who do not read pip."""
    low = output.lower()
    if "externally-managed-environment" in low:
        return ("this Python is managed by the system and refuses pip; run the "
                "program from a venv (python3 -m venv .venv)")
    if "no matching distribution" in low or "could not find a version" in low:
        return f"no package for Python {_version()} on this machine{_hint_312()}"
    if "permission denied" in low or "access is denied" in low:
        return "no permission to write to this Python's folder"
    lines = [ln.strip() for ln in output.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else "pip failed without a message"


def install(items, py=None, on_step=None):
    """Install ``items`` (from :func:`missing`) one by one.

    ``on_step(i, n, label)`` before each. Returns ``[(item, ok, reason)]``. A
    failed torch skips lama rather than installing a lama that cannot run."""
    py = pip_python(py)
    results = []
    torch_failed = False
    for i, item in enumerate(items):
        if on_step is not None:
            try:
                on_step(i, len(items), item["label"])
            except Exception:
                pass
        if item["key"] == "lama" and torch_failed:
            results.append((item, False, "skipped: torch is missing"))
            continue
        if item["key"] == "torch" and not torch_installable(py):
            results.append((item, False, f"no torch for Python {_version()} yet{_hint_312()}"))
            torch_failed = True
            continue
        code, out = _run([py, "-m", "pip", "install", "--disable-pip-version-check",
                          *item["pip"]])
        ok = code == 0
        if item["key"] == "torch" and not ok:
            torch_failed = True
        results.append((item, ok, "" if ok else explain(out)))
    importlib.invalidate_caches()
    return results


def summary(results):
    """``(all_ok, text)`` for a dialog."""
    lines = []
    for item, ok, reason in results:
        lines.append(f"{'ok' if ok else 'FAILED'}: {item['label']}"
                     + ("" if ok else f" -- {reason}"))
    all_ok = all(ok for _i, ok, _r in results)
    if any(ok and item["key"] in ("dnd",) for item, ok, _r in results):
        lines.append("\nDrag and drop works after a restart.")
    return all_ok, "\n".join(lines)
