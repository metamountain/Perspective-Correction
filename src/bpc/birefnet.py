"""BiRefNet as a region-mask source.

The predecessor here was Segment Anything, and the reason it is gone is worth
keeping: SAM returns forty boundaries and no labels, so which of them is the
building had to be decided by a second, invented criterion.  Two were tried.
Line density works; a boundary-shape statistic did not, and because a region
survived on *either*, the broken half silently vetoed the working one -- see
``docs/masking.md``.  The whole construction existed only because SAM cannot
say what it segmented.

BiRefNet can.  It is a dichotomous segmenter: one high-resolution matte
separating the salient object from everything else, and on an architectural
photograph the salient object is the building.  So the mask is simply

    ignore := foreground < 0.5

with no scoring criterion in between, nothing for the line detector to
adjudicate, and no threshold worth tuning -- the matte comes back essentially
binary, and sweeping 0.1 to 0.9 moves the masked share by half a percent.

Round-trip over the seven shipped assets, with the harness's border guard in
place (``docs/masking.md`` carries the full table):

    mask off, f known      0.70 deg mean / 1.68 worst
    BiRefNet-HR, f known   0.65 / 1.36
    mask off, f unknown    1.12 / 2.25
    BiRefNet-HR, f unknown 0.88 / 1.96

The gain is modest and is *larger* where the focal length is unknown, which is
the opposite of how the cheap ``--mask auto`` heuristic behaves.  Earlier drafts
of this module claimed 0.98 -> 0.56; that spread was an artifact of the harness,
not the mask.

It also runs in ~0.3 s against SAM's ~1.5 s, needs no checkpoint hunting, and
drops an AGPL-3.0 optional dependency the project was carrying to load SAM at
all.

Nothing is vendored.  The weights are Apache-2.0 and the architecture MIT, but
both live in the user's ComfyUI install exactly as the SAM checkpoints did --
this module only knows how to talk to them.  ``birefnet.py`` there is
self-contained apart from importing its own config module by absolute name, so
that name is registered before the file is executed.

torch is imported only when this mask mode is actually used, so the default
install stays numpy + OpenCV + Pillow.
"""
from __future__ import annotations

import os
import sys
import threading

import cv2
import numpy as np

_LOCK = threading.Lock()
_CACHE = {}

# ImageNet statistics: what BiRefNet was trained with.
_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_STD = np.array([0.229, 0.224, 0.225], np.float32)

DEFAULT_THRESHOLD = 0.5

# Preference order for an automatic find.  HR first because it is what was
# measured, and because a mask resampled to the analysis resolution loses
# nothing to the larger inference size.
PREFERRED_ORDER = ("birefnet-hr", "birefnet_hr", "birefnet-general",
                   "birefnet_dynamic", "birefnet-dynamic", "birefnet_lite-2k",
                   "birefnet_lite", "general", "dis")


class BiRefNetUnavailable(RuntimeError):
    pass


def resolution_for(weights: str) -> int:
    """Inference size implied by the checkpoint name.

    The HR and 2K variants were trained at 2048 and are visibly worse when run
    at 1024; the others gain nothing above it.  Guessing from the name is what
    every ComfyUI node does, and the names are consistent enough for it.
    """
    name = os.path.basename(weights).lower()
    return 2048 if ("hr" in name or "2k" in name) else 1024


# Exactly what has to be on disk, named, because "it does not work" is what a
# missing file looks like from the outside.
WEIGHTS_FILE = "BiRefNet-HR.safetensors"
WEIGHTS_REPO = "1038lab/BiRefNet"
ARCH_FILES = ("birefnet.py", "BiRefNet_config.py")
TARGET_DIR = os.path.join("ComfyUI", "models", "RMBG", "BiRefNet")


def what_you_need(missing_weights=True, missing_arch=True) -> str:
    """The setup instructions, naming files and giving commands to run.

    Written to be **copy-pasteable**.  An error that describes a situation but
    leaves the reader to work out the fix is only half an error message, and the
    fix here is two downloads into one folder -- easy to state exactly, and
    impossible to guess.
    """
    lines = ["BiRefNet needs two things in one folder, and they arrive separately:"]
    if missing_weights:
        lines += ["",
                  "  1. the weights   " + WEIGHTS_FILE + "   (444 MB)"]
    if missing_arch:
        lines += ["",
                  "  2. the network   " + " + ".join(ARCH_FILES),
                  "     (a few KB; these define the model the weights fill in)"]
    lines += ["",
              "Put them in your ComfyUI install, under:",
              "    " + TARGET_DIR,
              "",
              "Both come from the same Hugging Face repository. With the",
              "huggingface_hub package installed, this fetches exactly them:",
              "",
              '    huggingface-cli download ' + WEIGHTS_REPO + ' \\',
              '        ' + WEIGHTS_FILE + ' ' + ' '.join(ARCH_FILES) + ' \\',
              '        --local-dir "<your ComfyUI>/' + TARGET_DIR.replace("\\", "/") + '"',
              "",
              "Or install the ComfyUI-RMBG custom nodes, which place all three",
              "there on first use, and then pass --birefnet-model auto.",
              "",
              "Check the result with:",
              "    python rectify.py --mask-info --birefnet-model auto"]
    return "\n".join(lines)


def _found_report() -> str:
    """What *is* on this machine, so the reader can see how close they are."""
    weights = find_weights()
    arch = architecture_dirs()
    bits = []
    bits.append("  weights found:      " + (weights or "none"))
    bits.append("  network found in:   " + (arch[0] if arch else "nowhere"))
    return "\n".join(bits)


def architecture_dirs():
    """Folders that might hold ``birefnet.py``, best first.

    Separate from the weights on purpose.  ComfyUI installs put checkpoints in
    more than one place -- ``models/BiRefNet`` and ``models/RMBG/BiRefNet`` are
    both common -- and only one of them tends to carry the architecture, because
    the node that downloads the architecture is not the node that downloads
    every checkpoint.
    """
    import glob
    pats = [os.path.join(d + ":\\", p) for d in "CDEFG"
            for p in ("ComfyUI*/ComfyUI/models/RMBG/BiRefNet",
                      "ComfyUI*/models/RMBG/BiRefNet",
                      "*/ComfyUI*/ComfyUI/models/RMBG/BiRefNet",
                      "ComfyUI*/ComfyUI/models/BiRefNet",
                      "ComfyUI*/models/BiRefNet")]
    pats += [os.path.expanduser("~/ComfyUI/models/RMBG/BiRefNet"),
             os.path.expanduser("~/comfyui/models/RMBG/BiRefNet"),
             os.path.join(os.getcwd(), "models", "BiRefNet")]
    out = []
    for pat in pats:
        for base in glob.glob(pat):
            if os.path.isfile(os.path.join(base, "birefnet.py")) and base not in out:
                out.append(base)
    return out


def _arch_dir(weights: str) -> str:
    """The folder holding ``birefnet.py``.

    Beside the weights if it is there, otherwise wherever it can be found.  The
    architecture is generic across BiRefNet checkpoints -- HR, general, lite and
    dynamic are the same network with different weights -- so pairing a
    checkpoint with an architecture from another folder is correct, not a
    workaround, and ``load_state_dict`` catches it immediately if it ever is not.

    This exists because of a real failure: a user's remembered checkpoint pointed
    into ``models/BiRefNet``, which on that machine holds three perfectly good
    checkpoints and no ``birefnet.py``, while ``models/RMBG/BiRefNet`` next door
    holds both.  Refusing it read as "BiRefNet does not work".
    """
    d = os.path.dirname(os.path.abspath(weights))
    if os.path.isfile(os.path.join(d, "birefnet.py")):
        return d
    found = architecture_dirs()
    if found:
        return found[0]
    raise BiRefNetUnavailable(
        "found the weights but not the network that defines them.\n\n"
        "  have: " + os.path.abspath(weights) + "\n"
        "  need: " + " + ".join(ARCH_FILES) + " (searched beside the weights and "
        "every usual ComfyUI folder)\n\n" + what_you_need(missing_weights=False))


def _load(weights: str, device: str = ""):
    """Build the model once per checkpoint per process.

    Reloading 444 MB for every photograph in a batch would dwarf everything
    else the tool does.
    """
    key = (os.path.abspath(weights), device)
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
        if not os.path.isfile(weights):
            raise BiRefNetUnavailable(
                "BiRefNet weights not found:\n  " + os.path.abspath(weights) +
                "\n\n" + _found_report() + "\n\n" + what_you_need())
        try:
            import torch
            from safetensors.torch import load_file
        except Exception as exc:
            raise BiRefNetUnavailable(_install_hint()) from exc

        import importlib.util
        d = _arch_dir(weights)
        cfg_path = os.path.join(d, "BiRefNet_config.py")
        try:
            # birefnet.py imports its config by absolute name, so the name has
            # to exist before the file is executed.
            if os.path.isfile(cfg_path):
                spec = importlib.util.spec_from_file_location("BiRefNet_config", cfg_path)
                mod = importlib.util.module_from_spec(spec)
                sys.modules["BiRefNet_config"] = mod
                spec.loader.exec_module(mod)
            spec = importlib.util.spec_from_file_location(
                "birefnet_arch", os.path.join(d, "birefnet.py"))
            arch = importlib.util.module_from_spec(spec)
            sys.modules["birefnet_arch"] = arch
            spec.loader.exec_module(arch)
        except Exception as exc:
            raise BiRefNetUnavailable(
                "could not load the BiRefNet architecture from " + d + ": " + str(exc)) from exc

        dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
        try:
            # bb_pretrained=False: the backbone weights are in the checkpoint,
            # and leaving it true makes the constructor reach for the network.
            model = arch.BiRefNet(config=arch.BiRefNetConfig(bb_pretrained=False))
            model.load_state_dict(load_file(weights))
            model.eval()
            if dev != "cpu":
                model.half()
            model.to(dev)
            torch.set_float32_matmul_precision("high")
        except Exception as exc:
            raise BiRefNetUnavailable(
                "could not load " + os.path.basename(weights) + " with the network "
                "in\n  " + d + "\n\n" + str(exc).split(chr(10))[0] + "\n\n"
                "The two do not match.  BiRefNet checkpoints share one network, so "
                "this\nusually means the file is a different model wearing a "
                "BiRefNet name.\nThe known-good pairing is:\n\n" +
                what_you_need()) from exc
        entry = {"model": model, "device": dev, "res": resolution_for(weights),
                 "half": dev != "cpu"}
        _CACHE[key] = entry
        return entry


def _install_hint() -> str:
    """Lead with what to do, and name *both* ways out.

    The two failures look identical and have opposite answers.  Running in the
    Python that has torch (ComfyUI's ``python_embeded``) but no tkinter, and
    running in the system Python that has tkinter but no torch, both print "no
    torch".  Telling a GUI user to install a multi-gigabyte CUDA torch into
    their system Python, when the machine already has one three folders away,
    is the wrong first suggestion.
    """
    have = backends()
    missing = [n for n in ("torch", "timm", "transformers", "safetensors",
                           "torchvision") if not have.get(n)]
    install = "    " + sys.executable + " -m pip install " + " ".join(missing or ["torch"])
    export = ('    <python with torch> rectify.py "<folder>" '
              '--mask-export "<mask folder>"\n'
              '    then, from here:  --mask file --mask-file "<mask folder>"')
    if have.get("tkinter") and not have.get("torch"):
        other = _likely_torch_python()
        where = "\n(on this machine, probably: " + other + ")" if other else ""
        return ("BiRefNet needs torch -- and this is the Python with the GUI, so "
                "it is usually not the one to install torch into.\n\n"
                "Either write the masks once from the Python that already has "
                "it:\n" + export + where +
                "\n\nor install it here, which means a multi-gigabyte torch:\n"
                + install)
    return ("BiRefNet needs " + ", ".join(missing or ["torch"]) + ". Run:\n" + install +
            "\nInstall into the SAME interpreter that runs this tool (" +
            os.path.basename(sys.executable) + "). On a ComfyUI portable install "
            "use its python_embeded\\python.exe -- it already has all of them.")


def _likely_torch_python() -> str:
    """A best-effort guess at the ComfyUI python on this machine.

    Only ever used to make an error message concrete; nothing depends on it.
    """
    import glob
    for pat in ("C:\\ComfyUI*\\python_embeded\\python.exe",
                "D:\\ComfyUI*\\python_embeded\\python.exe",
                "E:\\ComfyUI*\\python_embeded\\python.exe",
                os.path.expanduser("~/ComfyUI*/python_embeded/python.exe")):
        hits = glob.glob(pat)
        if hits:
            return hits[0]
    return ""


def backends() -> dict:
    """What this interpreter can import -- diagnostics only, never control flow,
    since importing torch is expensive.

    ``tkinter`` is in the list because it is the other half of the same problem:
    a ComfyUI portable install has torch but no tkinter, the system Python is
    usually the other way round, and seeing both at once is what makes the
    situation legible.
    """
    import importlib.util
    out = {}
    for n in ("torch", "timm", "transformers", "safetensors", "torchvision",
              "tkinter"):
        try:
            out[n] = importlib.util.find_spec(n) is not None
        except Exception:
            out[n] = False
    return out


def find_weights(hint: str = "") -> str:
    """Locate usable BiRefNet weights without making anyone type a path.

    A ``hint`` searches *only* there.  The predecessor searched the hint **and**
    every usual location, which meant asking about one folder could return
    weights from another -- unpredictable in use, and untestable in principle,
    because the answer depended on what the developer's machine happened to
    have installed.  Its two tests were red on any machine with a real ComfyUI.
    """
    import glob

    if hint:
        roots = [hint]
    else:
        roots = [os.path.join(d + ":\\", p) for d in "CDEFG"
                 for p in ("ComfyUI*/ComfyUI/models/RMBG/BiRefNet",
                           "ComfyUI*/models/RMBG/BiRefNet",
                           "*/ComfyUI*/ComfyUI/models/RMBG/BiRefNet",
                           "ComfyUI*/ComfyUI/models/BiRefNet",
                           "ComfyUI*/models/BiRefNet")]
        roots += [os.path.expanduser("~/ComfyUI/models/RMBG/BiRefNet"),
                  os.path.expanduser("~/comfyui/models/RMBG/BiRefNet"),
                  os.path.join(os.getcwd(), "models", "BiRefNet")]

    found = []
    for root in roots:
        for base in glob.glob(root):
            if not os.path.isdir(base):
                continue
            found += glob.glob(os.path.join(base, "*.safetensors"))
    if not found:
        return ""

    def rank(p):
        name = os.path.basename(p).lower()
        size = os.path.getsize(p)
        for i, tag in enumerate(PREFERRED_ORDER):
            if tag in name:
                # a file far too small to be the model it claims is not a
                # candidate however well its name ranks
                return (0 if size > 20_000_000 else 1, i, -size)
        return (2, len(PREFERRED_ORDER), -size)

    return sorted(found, key=rank)[0]


def describe(weights: str) -> str:
    """One line about a checkpoint, for the log and the GUI."""
    name = os.path.basename(weights)
    size = os.path.getsize(weights) / 1e6 if os.path.isfile(weights) else 0.0
    bits = ["{} ({:.0f} MB, {} px)".format(name, size, resolution_for(weights))]
    try:
        d = _arch_dir(weights)
        if os.path.dirname(os.path.abspath(weights)) != d:
            bits.append("architecture from " + d)
    except BiRefNetUnavailable:
        bits.append("no birefnet.py anywhere -- cannot be loaded")
    if size and size < 20:
        bits.append("suspiciously small for a BiRefNet checkpoint")
    return "; ".join(bits)


def available(weights: str) -> bool:
    try:
        _load(weights)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# the mask
# --------------------------------------------------------------------------
def foreground(bgr: np.ndarray, weights: str, device: str = "",
               res: int = 0) -> np.ndarray:
    """Salient-foreground probability, 0..1, at ``bgr``'s resolution."""
    import torch

    entry = _load(weights, device)
    res = res or entry["res"]
    h, w = bgr.shape[:2]
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    x = cv2.resize(rgb, (res, res), interpolation=cv2.INTER_CUBIC).astype(np.float32) / 255.0
    x = (x - _MEAN) / _STD
    t = torch.from_numpy(x).permute(2, 0, 1).unsqueeze(0)
    t = (t.half() if entry["half"] else t.float()).to(entry["device"])
    with _LOCK:
        with torch.no_grad():
            out = entry["model"](t)
    while isinstance(out, (list, tuple)):
        out = out[-1]                          # the network returns its pyramid
    p = torch.sigmoid(out.float())[0, 0].cpu().numpy()
    return cv2.resize(p, (w, h), interpolation=cv2.INTER_LINEAR)


def shrink_px_for(shape, frac: float) -> int:
    """Shrink margin in pixels for an image of this size.

    Scaled to the frame diagonal rather than fixed, because the analysis
    resolution is not: the same photograph at 1600 px and at 800 px must get
    the same amount of building silhouette handed back, and a constant pixel
    count would give the smaller one twice as much in relative terms.
    """
    if frac <= 0:
        return 0
    h, w = shape[:2]
    return max(1, int(round(float(frac) * float(np.hypot(h, w)))))


def build_mask(bgr: np.ndarray, weights: str, threshold: float = DEFAULT_THRESHOLD,
               device: str = "", res: int = 0, shrink_frac: float = 0.008):
    """``(ignore_mask, note)`` -- True where the fit should not look.

    **The threshold is not a knob.**  The matte is near-binary: sweeping 0.1 to
    0.9 on a real barn moved the masked share from 50.5% to 51.1%.

    **``shrink_frac`` is, and it is what makes the mask worth having.**  The
    pipeline discards a line only when *both* its endpoints fall inside the mask
    (``masks.drop_by_endpoints``).  BiRefNet cuts exactly along the building's
    silhouette, so the building's own corner and roof edges -- the longest and
    best-conditioned evidence in the frame -- end up with both ends just inside
    it and are thrown away.  Pulling the reject region off the silhouette first
    gives them back.  Round-trip over ten assets, shrink as a fraction of the
    frame diagonal so it does not change meaning with the analysis resolution:

        0.000  (~0 px)    0.663 deg mean / 1.95 worst
        0.002  (~4 px)    0.597 / 1.43
        0.004  (~8 px)    0.575 / 1.43
        0.008  (~15 px)   0.556 / 1.15      <- default
        0.016  (~31 px)   0.639 / 1.64
        no mask at all    0.661 / 1.69

    Read the first and last rows together: **unshrunk, the mask is worth
    nothing** -- 0.663 against 0.661 for not masking at all.  It removes as much
    good evidence as clutter.  Everything the segmenter buys here is bought by
    handing the silhouette back.
    """
    fg = foreground(bgr, weights, device=device, res=res)
    mask = fg < float(threshold)
    px = shrink_px_for(mask.shape, shrink_frac)
    if px > 0:
        k = np.ones((2 * px + 1,) * 2, np.uint8)
        mask = cv2.erode(mask.astype(np.uint8), k).astype(bool)
    return mask, "BiRefNet: {:.0f}% of frame outside the subject".format(
        float(mask.mean()) * 100)


def export_masks(images, weights, out_dir, settings, log=print):
    """Write one mask PNG per photograph, for use later with ``--mask file``.

    Two jobs in one seam.  It bridges the interpreter split -- the Python with
    torch has no tkinter and the one with tkinter has no torch -- and it is how
    a mask gets computed once instead of once per run, which matters because
    every benchmark re-analyses the same folder many times over.

    White means *ignore*, matching what ``masks.load`` expects, so the folder is
    usable with a plain ``--mask file`` and no ``--mask-invert``.
    """
    from . import imageio as IO

    os.makedirs(out_dir, exist_ok=True)
    written, failed = 0, 0
    log("# mask export -> " + out_dir)
    log("# " + describe(weights))
    for path in images:
        stem = os.path.splitext(os.path.basename(path))[0]
        try:
            src = IO.load(path)
            gray, _ = IO.analysis_gray(src.bgr, settings.detect_max_edge)
            small = cv2.resize(src.bgr, (gray.shape[1], gray.shape[0]),
                               interpolation=cv2.INTER_AREA)
            mask, note = build_mask(
                small, weights,
                threshold=getattr(settings, "birefnet_threshold", DEFAULT_THRESHOLD),
                device=getattr(settings, "birefnet_device", ""),
                res=getattr(settings, "birefnet_res", 0),
                shrink_frac=getattr(settings, "birefnet_shrink_frac", 0.008))
            # written at the analysis resolution; the loader resamples to
            # whatever the photograph is, so this stays small on disk
            cv2.imwrite(os.path.join(out_dir, stem + ".png"),
                        (mask.astype(np.uint8) * 255))
            log("OK      " + stem + "  " + note)
            written += 1
        except Exception as exc:
            log("ERROR   " + stem + "  " + str(exc))
            failed += 1
    log("# {} written, {} failed".format(written, failed))
    log('# now run anywhere:  --mask file --mask-file "' + out_dir + '"')
    return written, failed
