"""Install the dependencies on macOS or Windows.

Run by a double-click on ``Install (macOS).command`` / ``Install (Windows).bat``,
or by hand:

    python tools/install_deps.py            the core, plus drag and drop
    python tools/install_deps.py --mlsd     add the mlsd detector (ai-edge-litert)
    python tools/install_deps.py --lama     also the lama fill (torch if missing)
    python tools/install_deps.py --full     both of the above

The default is deliberately minimal: the four core packages plus the small
tkinterdnd2 package. The larger backends (mlsd, lama with torch) are opt-in,
and the in-app deps button offers them too. OpenCV comes from the headless
wheel on every platform: the window is Tkinter and the program never calls
``cv2.imshow``, so the GUI flavour is dead weight. Each package is probed in
the target Python first and pip installs only what is missing, so a system
Python that already carries the core downloads nothing and is left untouched.

The Windows path is tested, the macOS path has never run on a Mac. Linux users
install from requirements.txt by hand.

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
import shutil
import subprocess
import sys
import venv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIN_PYTHON = (3, 9)            # pyproject's requires-python
MIN_TK = 8.6
MAC = sys.platform == "darwin"
WIN = os.name == "nt"


def _load_depinstall():
    """The one package list, shared with the in-app 'deps' button. Loaded by
    path: importing the `pc` package pulls in numpy, which may not be here yet."""
    path = os.path.join(ROOT, "src", "pc", "depinstall.py")
    spec = importlib.util.spec_from_file_location("pc_depinstall", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_DI = _load_depinstall()

# (label, pip arguments, import name) for the optional packages the installer
# knows. Each is tried on its own so one missing wheel (ai-edge-litert has
# none for some OS + Python pairs) costs only it, and each is probed first:
# a module that is already there is reported, never reinstalled.
_BY_KEY = {p["key"]: p for p in _DI.PACKAGES}
_BY_KEY["torch"] = _DI.TORCH


def _step(key):
    p = _BY_KEY[key]
    return (p["label"], p["pip"], p["module"])


DND = _step("dnd")
MLSD = _step("mlsd")
LAMA = _step("lama")
TORCH = _step("torch")

# (import name, pip specification) of the four core packages. Probed before
# pip is asked, so a present module is left untouched and a Python that
# already carries the whole core downloads nothing.
CORE = [("numpy", "numpy>=1.24"), ("cv2", "opencv-python-headless>=4.8"),
        ("PIL", "Pillow>=10.0"), ("piexif", "piexif>=1.1.3")]


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


def venv_version(path):
    """major.minor of the Python a venv was made with, from its pyvenv.cfg."""
    try:
        with open(os.path.join(path, "pyvenv.cfg"), encoding="utf-8") as f:
            for line in f:
                key, _, value = line.partition("=")
                if key.strip() in ("version", "version_info"):
                    return ".".join(value.strip().split(".")[:2])
    except OSError:
        pass
    return None


def has_module(py, name):
    """Ask the target interpreter, not this one -- they differ on a Mac."""
    probe = f"import importlib.util,sys; sys.exit(importlib.util.find_spec({name!r}) is None)"
    return subprocess.call([py, "-c", probe]) == 0


def build_launcher(py):
    """Build ``Perspective Correction.exe`` with the logo (Windows only).

    Runs after the core install because ``make_launcher.py`` renders the icon
    from ``pc.gui``, which imports cv2/numpy/PIL. Never fails the install: the
    .bat launcher still works, and the .exe is build output, not the program."""
    say("\n== launcher: Perspective Correction.exe")
    if subprocess.call([py, os.path.join(ROOT, "tools", "make_launcher.py")], cwd=ROOT) == 0:
        return True
    say("!! the launcher build failed; double-click 'Perspective Correction.bat' instead")
    return False


def pip(py, args):
    return subprocess.call([py, "-m", "pip", "install", "--disable-pip-version-check", *args],
                           cwd=ROOT) == 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mlsd", action="store_true",
                    help="also install the mlsd detector (ai-edge-litert, ~10 MB)")
    ap.add_argument("--lama", action="store_true",
                    help="also install the lama fill, and torch if it is missing "
                         "(large download)")
    ap.add_argument("--full", action="store_true",
                    help="install every optional package: --mlsd plus --lama")
    ap.add_argument("--venv", default=os.path.join(ROOT, ".venv") if MAC else None,
                    help="install into this venv (default: .venv on macOS, "
                         "prompt or running Python on Windows)")
    ap.add_argument("--system", action="store_true",
                    help="install into the running system Python without prompting")
    a = ap.parse_args(argv)
    a.mlsd = a.mlsd or a.full
    a.lama = a.lama or a.full

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

    if WIN and a.venv is None and not a.system and sys.stdin.isatty():
        say("\nInstall target:")
        say("  [1] System Python (smaller -- reuses already-installed packages)")
        say("  [2] Local .venv   (isolated inside this folder)")
        try:
            choice = input("Choice [1]: ").strip().lower()
        except EOFError:
            choice = ""
        if choice in ("2", "v", "venv"):
            a.venv = os.path.join(ROOT, ".venv")

    if a.venv:
        py = venv_python(a.venv)
        built_with = venv_version(a.venv)
        mine = f"{sys.version_info[0]}.{sys.version_info[1]}"
        if os.path.exists(py) and built_with not in (None, mine):
            # A venv is tied to the Python that made it; one left over from
            # another version (3.15 before 3.12 was installed, on the first
            # Mac) would keep everything on that version for good.
            say(f"rebuilding {a.venv}: it was made with Python {built_with}, this is {mine}")
            shutil.rmtree(a.venv)
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

    say("\n== core (numpy, cv2, Pillow, piexif)")
    missing = [spec for mod, spec in CORE if not has_module(py, mod)]
    if not missing:
        say("core: all packages already present (0 MB downloaded)")
    elif not pip(py, missing):
        say("!! the core install failed -- read the pip output above.")
        return 1

    launcher_ok = WIN and build_launcher(py)

    steps = [DND]
    if a.mlsd:
        steps.append(MLSD)
    if a.lama:
        # An installed torch is left alone: on Windows it is often a CUDA build
        # that a plain `pip install torch` would not reproduce.
        if not has_module(py, "torch"):
            steps.append(TORCH)
        steps.append(LAMA)
    present, done, missed = [], [], []
    for label, args, module in steps:
        say(f"\n== optional: {label}")
        if has_module(py, module):
            say(f"already installed: {label}")
            present.append(label)
            continue
        if args == LAMA[1] and TORCH[0] in missed:
            # The in-app button skips it the same way: lama installs fine
            # with --no-deps and then cannot run, and the summary called it ok.
            say(f"skipped: {label} cannot run without torch")
            missed.append(label)
            continue
        if pip(py, args):
            done.append(label)
        else:
            missed.append(label)
            if args == TORCH[1]:
                say(f"!! torch has no package for Python {sys.version_info[0]}."
                    f"{sys.version_info[1]} here; lama needs it. Python 3.12 works.")

    say("\n== doctor")
    subprocess.call([py, os.path.join(ROOT, "perspective_correction.py"), "--doctor"], cwd=ROOT)

    say("\n== summary")
    say("core: installed" if missing
        else "core: already present, left untouched")
    for label in present:
        say(f"optional present: {label} (already installed)")
    for label in done:
        say(f"optional ok:      {label}")
    for label in missed:
        say(f"optional MISSING: {label} (the program runs without it)")
    if not a.mlsd:
        say("mlsd not asked for (the lsd detector runs without it); "
            "add it with --mlsd or --full")
    if not a.lama:
        say("lama fill not asked for (telea is used without it); "
            "add it with --lama or --full")
    if launcher_ok:
        say("\nStart: double-click 'Perspective Correction.exe', or")
    else:
        launcher = "Perspective Correction.command" if MAC else "Perspective Correction.bat"
        say(f"\nStart: double-click '{launcher}', or")
    say(f"       \"{py}\" perspective_correction.py --gui")
    return 0


if __name__ == "__main__":
    sys.exit(main())
