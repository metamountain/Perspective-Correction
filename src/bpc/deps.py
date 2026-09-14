"""Dependency check: what this interpreter can actually run, before a batch.

The rest of the package degrades gracefully -- every optional backend names the
missing piece instead of failing at import, and `--diagnostics` prints the
environment *after* the fact.  This module is the gate that runs *before* any
file is touched: it answers "can this Python do what the command line asked for"
and refuses loudly when the answer is no, so a batch never starts and then dies
on every file with a traceback.

Two kinds of dependency, treated differently on purpose:

* **core** -- numpy, cv2, Pillow.  Without them the tool cannot open or write an
  image at all, so a missing one stops *any* invocation (except `--doctor`, which
  exists to report exactly this).  piexif is the exception within core: it is
  imported lazily in `imageio.py` with a fallback to `default_focal_35mm`, so a
  missing piexif degrades, not stops -- reported, never fatal.

* **optional** -- the learned backends (M-LSD, DeepLSD, LaMa, ComfyUI, BiRefNet).
  These are only a problem when *asked for*.  A run left at the defaults
  (`detector=lsd`, `fill=telea`, `mask=off`) must stay runnable on a machine
  with nothing but the core installed; that is the point of them being optional.
  So the pre-flight fails only on a backend the command line explicitly selected,
  and says which flag pulled it in.

Nothing here imports torch or any heavy package at module level: availability is
probed with guarded imports, for the same reason `birefnet.backends()` does it --
importing torch is expensive and must not be the price of asking "is torch there?".
"""
from __future__ import annotations

import importlib
import platform
import sys


def _core_specs():
    """(import name, pip name, hard?, why) for the four packages requirements.txt pins.

    cv2's pip name differs by platform exactly as requirements.txt does: the
    headless build off-Windows, the full one on Windows (the GUI wants it).
    """
    opencv = "opencv-python" if platform.system() == "Windows" else "opencv-python-headless"
    return [
        ("numpy",  "numpy",   True,  "line fitting and the geometry"),
        ("cv2",    opencv,    True,  "reading images and LSD line detection"),
        ("PIL",    "Pillow",  True,  "writing output and EXIF"),
        ("piexif", "piexif",  False, "EXIF focal length (falls back to --default-focal-35mm)"),
    ]


def _probe(name):
    """(ok, detail). Never raises: a broken install is 'not ok', not a crash.

    Importing (not just find_spec) on purpose -- a package that imports but then
    breaks (the simple-lama Pillow-downgrade case) must read as missing, and the
    version string comes for free.  Core packages are already imported by the time
    this runs, so it costs nothing.
    """
    try:
        mod = importlib.import_module(name)
        return True, getattr(mod, "__version__", "?")
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def core_status():
    """One record per core package: name, ok, detail, pip name, hard."""
    out = []
    for name, pip_name, hard, why in _core_specs():
        ok, detail = _probe(name)
        out.append({"name": name, "ok": ok, "detail": detail,
                    "pip": pip_name, "hard": hard, "why": why})
    return out


def core_errors():
    """Install commands for the missing *hard* core packages. Empty = go.

    Soft misses (piexif) are deliberately excluded: they degrade, and a batch that
    refuses to run because EXIF is unreadable would be wrong -- the tool already
    falls back to a default focal length in that case.
    """
    errs = []
    for rec in core_status():
        if not rec["ok"] and rec["hard"]:
            errs.append(f"{rec['name']} ({rec['why']}) -- pip install {rec['pip']}")
    return errs


def preflight(settings):
    """Errors for backends the command line *asked for* that this interpreter
    cannot run. Empty = go.

    Includes the core check so it is safe to call on its own, though main() gates
    on core_errors() first and never reaches here with a broken core.  The test is
    "was this backend selected", not "is it installed" -- a default run must stay
    runnable wherever the core is.
    """
    errs = list(core_errors())

    det = settings.detector
    if det in ("mlsd", "hybrid", "union"):
        from . import mlsd as ML
        if not ML.available(settings.mlsd_model):
            errs.append(f"--detector {det} needs the M-LSD TFLite runtime "
                        f"(pip install ai-edge-litert) and a model; run --doctor for detail")
    elif det in ("deeplsd", "deep-hybrid", "deep-union"):
        from . import deeplsd as DL
        if not DL.available(settings.deeplsd_model, settings.deeplsd_device):
            errs.append(f"--detector {det} needs torch + a DeepLSD checkout in tools/ "
                        f"+ models/deeplsd_md.tar; run --doctor for detail")

    fill = settings.fill
    if fill == "lama":
        from . import inpaint as FILL
        if not FILL.available(fill, settings):
            errs.append("--fill lama needs simple-lama-inpainting "
                        "(pip install --no-deps simple-lama-inpainting)")
    elif fill == "comfyui":
        from . import inpaint as FILL
        if not FILL.available(fill, settings):
            errs.append(f"--fill comfyui needs a running ComfyUI at {settings.comfy_url} "
                        f"and an API-format workflow; run --fill-info --fill comfyui")

    if settings.mask_mode == "birefnet":
        from . import birefnet as BN
        if not settings.birefnet_model:
            errs.append("--mask birefnet needs --birefnet-model <weights> (or 'auto')")
        elif not BN.available(settings.birefnet_model):
            errs.append(f"--mask birefnet: weights do not load ({settings.birefnet_model}); "
                        f"run --mask-info for detail")
    if settings.mask_mode == "gdino":
        from . import masks as MK
        if not MK.gdino_available(settings.gdino_model):
            errs.append("--mask gdino needs transformers (pip install transformers timm einops) "
                        "and a Grounding DINO model dir; run --doctor for detail")
        if not settings.birefnet_model:
            errs.append("--mask gdino needs --birefnet-model <weights> (or 'auto') for the matte")
        else:
            from . import birefnet as BN
            if not BN.available(settings.birefnet_model):
                errs.append(f"--mask gdino: BiRefNet weights do not load ({settings.birefnet_model})")

    if settings.undistort == "lensfun":
        from . import distortion as DIST
        if not DIST.available():
            errs.append("--undistort lensfun needs lensfunpy (pip install lensfunpy)")
    return errs


def doctor() -> int:
    """`--doctor`: the full picture, printed and exited on.  The sibling of the
    three `*-info` probes, but it covers core *and* every optional backend at once.

    Returns 2 only when a *required* package is missing (the tool cannot run);
    unavailable optional backends are reported but do not fail the check, because
    they only matter if selected.
    """
    from .config import Settings
    st = Settings()  # defaults: doctor reports what is installed, not a run's config

    recs = core_status()
    by = {r["name"]: r for r in recs}
    hard_missing = sum(1 for r in recs if not r["ok"] and r["hard"])

    lines = ["bpc dependency check",
             f"  interpreter: {sys.executable}",
             f"  python: {platform.python_version()}   ({platform.platform()})",
             "",
             "core (required to run at all)"]
    for rec in recs:
        if rec["ok"]:
            lines.append(f"  [ok     ] {rec['name']:<7} {rec['detail']}")
        else:
            tag = "MISSING  " if rec["hard"] else "missing  "
            line = f"  [{tag}] {rec['name']:<7} {rec['detail']}   -- pip install {rec['pip']}"
            if not rec["hard"]:
                line += "   (soft: EXIF falls back to the default focal length)"
            lines.append(line)

    if by.get("cv2", {}).get("ok") and by.get("numpy", {}).get("ok"):
        from . import mlsd as ML
        from . import deeplsd as DL
        from . import inpaint as FILL
        from . import birefnet as BN
        lines += ["", "optional backends (only needed when selected on the command line)"]
        ok = ML.available(st.mlsd_model)
        lines.append(f"  [{'yes' if ok else 'no ':>3}]  mlsd / hybrid / union        TFLite runtime + vendored model")
        ok = DL.available(st.deeplsd_model, st.deeplsd_device)
        lines.append(f"  [{'yes' if ok else 'no ':>3}]  deeplsd / deep-hybrid / deep-union   {DL.describe(st.deeplsd_model, st.deeplsd_device)}")
        for mode in ("telea", "lama", "comfyui"):
            ok = FILL.available(mode, st)
            lines.append(f"  [{'yes' if ok else 'no ':>3}]  fill {mode:<8} {FILL.describe(mode, st)}")
        b = BN.backends()
        weights = BN.find_weights()
        # torch is not enough. BiRefNet's architecture is loaded through
        # `transformers`, so an interpreter with torch and the weights and no
        # transformers reports ready and then fails on the first photograph.
        # That is exactly what happened: `--doctor` said "[yes] mask birefnet"
        # on the very interpreter where `--mask-info` said "loads: NO -- No
        # module named 'transformers'". Two checks of one fact, disagreeing;
        # this one now reads the same `backends()` dict that --mask-info does.
        missing = BN.arch_missing()
        ok = bool(weights) and not missing
        if not missing:
            why = "ready" if weights else "no weights found"
        else:
            # Name the bridge before the install. Opting in costs torch,
            # torchvision, transformers, timm and einops -- gigabytes -- to buy
            # 0.70 -> 0.65 deg. That is a fine trade for someone who already
            # has them and a bad one for everybody else, so the line says so
            # rather than reading as a broken requirement.
            why = ("optional, not needed for a normal run; wants "
                   + ", ".join(missing))
        lines.append(f"  [{'yes' if ok else 'no ':>3}]  mask birefnet                {why}"
                     + (f"; weights found: {weights}" if weights else ""))
        if missing and weights:
            lines.append("           (an existing ComfyUI python can write the masks once: "
                          "--mask-export DIR, then --mask file DIR)")
        from . import masks as MK
        gok = MK.gdino_available(st.gdino_model)
        lines.append(f"  [{'yes' if gok else 'no ':>3}]  mask gdino                   "
                     + ("ready (transformers + Grounding DINO model present)" if gok
                        else "needs transformers (pip install transformers timm einops) "
                             "+ a Grounding DINO model dir"))
        from . import distortion as DIST
        dok = DIST.available()
        lines.append(f"  [{'yes' if dok else 'no ':>3}]  undistort lensfun            {DIST.describe()}")
    else:
        lines += ["", "optional backends: cannot check -- cv2 or numpy missing (see core above)"]

    lines.append("")
    if hard_missing:
        lines.append(f"result: {hard_missing} required package(s) missing -- the tool cannot run until installed")
    else:
        lines.append("result: core OK. Optional backends marked 'no' only matter if you select them.")
    for ln in lines:
        print(ln)
    return 2 if hard_missing else 0
