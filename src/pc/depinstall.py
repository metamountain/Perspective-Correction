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
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# GeoCalib (pc.geocal): one commit's GitHub archive, so pip needs no git, and
# the weights it would otherwise fetch on the first photograph.
GEOCALIB_URL = ("https://github.com/cvg/GeoCalib/archive/"
                "1dedb29f94fc11b896c5f572005978aeebccd67f.tar.gz")
GEOCALIB_WEIGHTS = os.path.join(ROOT, "models", "geocalib", "pinhole.tar")
GEOCALIB_WEIGHTS_URL = ("https://github.com/cvg/GeoCalib/releases/download/v1.0/"
                        "geocalib-pinhole.tar")
GEOCALIB_WEIGHTS_SIZE = 116074121
# SAM 2.1 small for the box select (pc.sam2seg), run through transformers in
# this Python -- no ComfyUI, no torchvision. One pinned revision; every file is
# size-checked like GeoCalib's weights.
SAM2_DIR = os.path.join(ROOT, "models", "sam2.1-hiera-small")
SAM2_REV = "ee5bba1d82bb8749febdf90f45e84b687142ba03"
SAM2_URL = "https://huggingface.co/facebook/sam2.1-hiera-small/resolve/" + SAM2_REV + "/"
SAM2_FILES = {"config.json": 5698, "model.safetensors": 184305280,
              "preprocessor_config.json": 683, "processor_config.json": 95}

# key, label, import name, pip arguments, rough download size.
PACKAGES = [
    {"key": "lama", "label": "lama fill (simple-lama-inpainting)",
     "module": "simple_lama_inpainting",
     "pip": ["--no-deps", "simple-lama-inpainting"], "size": "small, needs torch"},
    {"key": "mlsd", "label": "mlsd detector (ai-edge-litert)",
     "module": "ai_edge_litert", "pip": ["--no-deps", "ai-edge-litert"], "size": "~10 MB"},
    {"key": "dnd", "label": "drag and drop (tkinterdnd2)",
     "module": "tkinterdnd2", "pip": ["tkinterdnd2>=0.4"], "size": "~1 MB"},
    # Needs torch: ticking it in the menu takes torch along, and `install`
    # skips it when pip finds no torch for this Python/OS (torch_installable),
    # so it is only ever installed where it can run. It only sharpens a focal
    # GUESS (no EXIF, lines that cannot fix it); see pc.geocal.
    {"key": "geocalib", "label": "focal guess without EXIF (GeoCalib)",
     "module": "geocalib", "pip": ["--no-deps", GEOCALIB_URL],
     "size": "~140 MB, needs torch"},
    # Needs torch the same way. transformers goes in with its own (light,
    # pure-Python) dependencies; it pins nothing that touches numpy or Pillow.
    {"key": "sam", "label": "box select (SAM 2.1 small)",
     "module": "transformers", "pip": ["transformers"],
     "size": "~200 MB, needs torch"},
]
# lama imports torch and nothing else of its pins at run time. An installed
# torch is never touched: on Windows it is often a CUDA build that a plain
# `pip install torch` would replace with the CPU one.
TORCH = {"key": "torch", "label": "torch (for lama, GeoCalib and SAM)", "module": "torch",
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


# What each one is for, in the deps menu.
PURPOSE = {
    "torch": "the engine lama, GeoCalib and SAM run on",
    "lama": "the good fill for the empty corners (telea fills without it)",
    "geocalib": "a better focal guess for photos without EXIF",
    "sam": "drag a box around the building, everything else is ignored",
    "mlsd": "a second line detector",
    "dnd": "drag photos onto the window",
}
_ORDER = ("torch", "lama", "geocalib", "sam", "mlsd", "dnd")
NEEDS_TORCH = ("lama", "geocalib", "sam")


def catalogue():
    """Every optional package for the deps menu, with ``installed`` and
    ``purpose`` added, in install order (torch first).

    User, 2026-10-24: "soll nicht blind installieren" -- the menu shows them
    all and installs only what is ticked; this is its data."""
    by_key = {p["key"]: p for p in PACKAGES}
    by_key["torch"] = TORCH
    rows = []
    for key in _ORDER:
        pkg = by_key[key]
        inst = present(pkg["module"])
        if key == "geocalib":
            inst = inst and geocalib_weights_ok()
        if key == "sam":
            inst = inst and sam2_weights_ok()
        rows.append(dict(pkg, installed=inst, purpose=PURPOSE[key]))
    return rows


def in_order(items):
    """``items`` sorted so torch comes before what needs it."""
    return sorted(items, key=lambda i: _ORDER.index(i["key"]) if i["key"] in _ORDER else 99)


def geocalib_weights_ok(path=None):
    path = path or GEOCALIB_WEIGHTS
    return os.path.isfile(path) and os.path.getsize(path) == GEOCALIB_WEIGHTS_SIZE


def sam2_weights_ok(folder=None):
    folder = folder or SAM2_DIR
    return all(os.path.isfile(os.path.join(folder, n))
               and os.path.getsize(os.path.join(folder, n)) == size
               for n, size in SAM2_FILES.items())


def fetch_sam2_weights(folder=None, url=None, files=None):
    """Download SAM 2.1 small's four files; ``(ok, reason)``."""
    folder, url = folder or SAM2_DIR, url or SAM2_URL
    for name, size in (files or SAM2_FILES).items():
        ok, reason = fetch_file(os.path.join(folder, name), url + name, size)
        if not ok:
            return False, f"{name}: {reason}"
    return True, ""


def fetch_geocalib_weights(path=None, url=None, size=None):
    """Download GeoCalib's weights; ``(ok, reason)``."""
    return fetch_file(path or GEOCALIB_WEIGHTS, url or GEOCALIB_WEIGHTS_URL,
                      GEOCALIB_WEIGHTS_SIZE if size is None else size)


def fetch_file(path, url, size):
    """Download one file; ``(ok, reason)``. Written to a temp name and moved
    into place only at the right size, so a broken download never looks
    installed."""
    if os.path.isfile(path) and os.path.getsize(path) == size:
        return True, ""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        got = os.path.getsize(tmp)
        if got != size:
            os.remove(tmp)
            return False, f"weights download incomplete ({got} of {size} bytes)"
        # A virus scanner holds a fresh 116 MB file for a moment (WinError 32,
        # met on the first real install), so the move is retried.
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.5)
        return True, ""
    except Exception as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False, f"weights download failed: {exc}"


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
        if item["key"] in NEEDS_TORCH and torch_failed:
            results.append((item, False, "skipped: torch is missing"))
            continue
        if item["key"] in ("geocalib", "sam"):
            ok, reason = True, ""
            if not present(item["module"]):
                code, out = _run([py, "-m", "pip", "install", "--disable-pip-version-check",
                                  *item["pip"]])
                ok, reason = code == 0, ("" if code == 0 else explain(out))
            if ok:
                ok, reason = (fetch_geocalib_weights() if item["key"] == "geocalib"
                              else fetch_sam2_weights())
            results.append((item, ok, reason))
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
