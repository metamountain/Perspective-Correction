"""Install the dependencies on macOS or Windows.

Run by a double-click on ``Install (macOS).command`` / ``Install (Windows).bat``,
or by hand:

    python tools/install_deps.py            core + the small optional packages
    python tools/install_deps.py --lama     also the lama fill (torch: ~1 GB)

Written 2026-10-23; the Windows path is tested, the macOS path has never run
on a Mac. Linux users install from requirements.txt themselves (user).

Where it installs differs on purpose:

- **macOS: into ``.venv`` beside the code.** The Python macOS ships and
  Homebrew's both resist ``pip install`` into themselves, and a venv is thrown
  away by deleting one folder. ``Perspective Correction.command`` starts from it.
- **Windows: into the Python running this script.** ``Perspective Correction.exe``
  and the ``.bat`` start the ``pythonw`` on PATH, so a venv would install
  everything where the launchers never look. ``--venv DIR`` overrides.

The Tk check comes first because it is the failure that installs cleanly and
then breaks at the first window: Apple's Python carries the deprecated Tk 8.5,
Homebrew's has none without ``python-tk``, and the python.org Windows installer
lets you untick it.

Optional packages never fail the install: each is reported and the program
runs without it (``--doctor`` at the end says what is present). They go in
with ``--no-deps`` for the reason CLAUDE.md gives: simple-lama's pins
downgrade Pillow/numpy and break OpenCV.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import venv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIN_PYTHON = (3, 9)            # pyproject's requires-python
MIN_TK = 8.6
MAC = sys.platform == "darwin"
WIN = os.name == "nt"

# (label, pip arguments). Each is tried on its own so one missing wheel
# (ai-edge-litert has none for some OS + Python pairs) costs only it.
OPTIONAL = [
    ("drag and drop (tkinterdnd2)", ["tkinterdnd2>=0.4"]),
    ("mlsd detector (ai-edge-litert)", ["--no-deps", "ai-edge-litert"]),
]
# lama runs on torch alone once installed with --no-deps (its fire, torchvision
# and opencv-python pins are not imported at run time).
LAMA = ("lama fill (simple-lama-inpainting)", ["--no-deps", "simple-lama-inpainting"])
TORCH = ("torch (for lama)", ["torch"])


def say(msg=""):
    print(msg, flush=True)


def tk_problem():
    """The GUI's floor, asked of the running interpreter. A reason, or None."""
    try:
        import tkinter
    except ImportError:
        return "this Python has no tkinter"
    if float(tkinter.TkVersion) < MIN_TK:
        return f"this Python has Tk {tkinter.TkVersion}, the window needs {MIN_TK}"
    return None


def tk_advice():
    if MAC:
        v = f"{sys.version_info[0]}.{sys.version_info[1]}"
        return ["Install Python from https://www.python.org/downloads/macos/ (it brings Tk 8.6),",
                f"or with Homebrew: brew install python-tk@{v}"]
    return ["Re-run the python.org installer, choose Modify and tick 'tcl/tk and IDLE'."]


def venv_python(path):
    if WIN:
        return os.path.join(path, "Scripts", "python.exe")
    return os.path.join(path, "bin", "python")


def has_module(py, name):
    """Ask the target interpreter, not this one -- they differ on a Mac."""
    probe = f"import importlib.util,sys; sys.exit(importlib.util.find_spec({name!r}) is None)"
    return subprocess.call([py, "-c", probe]) == 0


def pip(py, args):
    return subprocess.call([py, "-m", "pip", "install", "--disable-pip-version-check", *args],
                           cwd=ROOT) == 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--lama", action="store_true",
                    help="also install the lama fill, and torch if it is missing (large download)")
    ap.add_argument("--venv", default=os.path.join(ROOT, ".venv") if MAC else None,
                    help="install into this venv (default: .venv on macOS, "
                         "the running Python on Windows)")
    a = ap.parse_args(argv)

    if not (MAC or WIN):
        say("This installer covers macOS and Windows. On Linux: "
            "pip install -r requirements.txt (see README).")
        return 2
    say(f"Python {sys.version.split()[0]}  ({sys.executable})")
    if sys.version_info < MIN_PYTHON:
        say(f"!! needs Python {'.'.join(map(str, MIN_PYTHON))} or newer.")
        return 1
    reason = tk_problem()
    if reason:
        say(f"!! {reason}.")
        for line in tk_advice():
            say("   " + line)
        say("   Then run this installer again.")
        return 1

    if a.venv:
        py = venv_python(a.venv)
        if os.path.exists(py):
            say(f"using the existing environment {a.venv}")
        else:
            say(f"creating {a.venv} ...")
            venv.create(a.venv, with_pip=True)
        if not pip(py, ["--upgrade", "pip"]):
            say("!! could not upgrade pip; carrying on with the one there is")
    else:
        py = sys.executable
        say("installing into this Python (the one the launchers start)")

    say("\n== core (requirements.txt)")
    if not pip(py, ["-r", os.path.join(ROOT, "requirements.txt")]):
        say("!! the core install failed -- read the pip output above.")
        return 1

    steps = list(OPTIONAL)
    if a.lama:
        # An installed torch is left alone: on Windows it is often a CUDA build
        # that a plain `pip install torch` would not reproduce.
        if not has_module(py, "torch"):
            steps.append(TORCH)
        steps.append(LAMA)
    done, missed = [], []
    for label, args in steps:
        say(f"\n== optional: {label}")
        (done if pip(py, args) else missed).append(label)

    say("\n== doctor")
    subprocess.call([py, os.path.join(ROOT, "perspective_correction.py"), "--doctor"], cwd=ROOT)

    say("\n== summary")
    say("core: installed")
    for label in done:
        say(f"optional ok:      {label}")
    for label in missed:
        say(f"optional MISSING: {label} (the program runs without it)")
    if not a.lama:
        say("lama fill not asked for (telea is used without it); add it with --lama")
    launcher = "Perspective Correction.command" if MAC else "Perspective Correction.bat"
    say(f"\nStart: double-click '{launcher}', or")
    say(f"       \"{py}\" perspective_correction.py --gui")
    return 0


if __name__ == "__main__":
    sys.exit(main())
