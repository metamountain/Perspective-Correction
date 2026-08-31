"""Segment Anything as a region-mask source.

SAM finds boundaries superbly and has no idea what they mean.  It will happily
cut a tree out of a photograph, but nothing in its output says which of the
forty regions it returned is the building.  That gap -- not loading the model --
is the whole problem, and it is solved here with evidence the pipeline already
has:

    SAM supplies the edges.  The line detector supplies the labels.

A facade is made of long straight lines; foliage, sky, cars and people are not.
So every segment SAM returns is scored by the straight-line evidence inside it,
and the ones with none are masked out.  No text model, no GroundingDINO, and it
works with SAM 1 and SAM 2 alike.  If the loaded checkpoint turns out to expose
a text-promptable concept head -- SAM 3 -- that is used instead, prompting for
the *rejects*, because concrete countable nouns ground well and a missed piece
of building is lost evidence.

torch is imported only when this mask mode is actually used, so the default
install stays numpy + OpenCV + Pillow.  Anyone running ComfyUI already has both
torch and a checkpoint in ``models/sams``.

Three backends are tried, in this order:

``ultralytics``   the easiest by far -- one class handles SAM 1, 2 and 3, picks
                  the right predictor from the file name, and SAM 3 checkpoints
                  get a text-promptable head through ``set_classes``.
                  **AGPL-3.0**, so it is an optional dependency the user
                  installs, never a bundled one; this project stays MIT.
``sam2``          Meta's own package, Apache-2.0, but it needs the architecture
                  config that matches the checkpoint.
``segment_anything``  Meta's SAM 1 package, Apache-2.0.
"""
from __future__ import annotations

import os
import threading

import cv2
import numpy as np

_LOCK = threading.Lock()
_CACHE = {}

# Names as they appear in ComfyUI's models/sams folder.
_SAM1_HINTS = ("sam_vit_h", "sam_vit_l", "sam_vit_b", "mobile_sam")
_HQ_HINTS = ("sam_hq", "hq_vit")
_SAM2_HINTS = ("sam2", "sam2.1", "hiera")
_SAM3_HINTS = ("sam3",)

DEFAULT_REJECT_PROMPT = "tree, foliage, bush, sky, cloud, car, person, grass"


class SAMUnavailable(RuntimeError):
    pass


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------
def _kind(path: str) -> str:
    name = os.path.basename(path).lower()
    for hint in _SAM3_HINTS:
        if hint in name:
            return "sam3"
    for hint in _SAM2_HINTS:
        if hint in name:
            return "sam2"
    for hint in _SAM1_HINTS + _HQ_HINTS:
        if hint in name:
            return "sam1"
    return "unknown"


def _load(path: str, device: str = ""):
    """Build a segmenter once per checkpoint per process.

    Reloading hundreds of megabytes for every photograph in a batch would dwarf
    everything else the tool does.
    """
    key = (os.path.abspath(path), device)
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
        if not os.path.isfile(path):
            raise SAMUnavailable(f"SAM checkpoint not found: {path}")
        path = _as_torch_checkpoint(path)
        errors = []
        for loader in (_load_ultralytics, _load_sam2, _load_sam1):
            try:
                entry = loader(path, device)
            except Exception as exc:
                errors.append(f"{loader.__name__[6:]}: {exc}")
                continue
            _CACHE[key] = entry
            return entry
        raise SAMUnavailable(
            f"could not load {os.path.basename(path)}.  Install one of: "
            f"'pip install ultralytics' (handles SAM 1/2/3, AGPL-3.0), "
            f"'pip install sam2', or 'pip install segment-anything'.  "
            + " | ".join(errors))


def _as_torch_checkpoint(path: str) -> str:
    """Convert a ``.safetensors`` checkpoint to ``.pt`` once, and cache it.

    ComfyUI ships SAM 2 weights as safetensors, and every SAM loader in the wild
    expects a torch file -- ultralytics rejects anything but ``.pt``/``.pth``
    outright.  Converting once beside the original costs a few seconds and makes
    the file usable by all three backends, instead of telling the user their
    model is unsupported when it plainly is.
    """
    if not path.lower().endswith(".safetensors"):
        return path
    out = os.path.splitext(path)[0] + ".converted.pt"
    if os.path.isfile(out):
        return out
    try:
        import torch
        from safetensors.torch import load_file
    except Exception as exc:
        raise SAMUnavailable(
            "this checkpoint is .safetensors; converting it needs "
            "'pip install safetensors'"
        ) from exc
    state = load_file(path)
    tmp = out + ".part"
    torch.save({"model": state}, tmp)
    os.replace(tmp, out)
    return out


def _load_ultralytics(path, device):
    """The easy path: one class for every SAM generation.

    ``SAM`` reads the generation off the file name and selects the matching
    predictor, so no architecture config has to be guessed -- which is exactly
    where the official SAM 2 package is brittle.
    """
    from ultralytics import SAM as _USAM

    model = _USAM(path)
    kw = {"device": device} if device else {}

    def generate(rgb):
        res = model.predict(rgb, verbose=False, **kw)
        return _ultra_masks(res)

    def text_generate(rgb, words):
        inner = getattr(model, "model", None)
        setter = getattr(inner, "set_classes", None)
        if setter is None:
            raise SAMUnavailable("this checkpoint has no text-promptable head")
        setter(text=list(words))
        res = model.predict(rgb, verbose=False, **kw)
        return _ultra_masks(res)

    return {"kind": "ultralytics", "generate": generate,
            "text_generate": text_generate, "model": model, "device": device,
            "has_text": bool(getattr(model, "is_sam3", False))}


def _ultra_masks(results):
    """Ultralytics results -> a list of boolean arrays."""
    out = []
    for r in results or []:
        m = getattr(r, "masks", None)
        if m is None or getattr(m, "data", None) is None:
            continue
        data = m.data
        arr = data.detach().cpu().numpy() if hasattr(data, "detach") else np.asarray(data)
        for layer in arr:
            out.append(layer > 0.5)
    return out


def _load_sam2(path, device):
    import torch
    from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator
    from sam2.build_sam import build_sam2

    dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
    model = build_sam2(_sam2_config_for(path), path, device=dev,
                       apply_postprocessing=False)
    gen = SAM2AutomaticMaskGenerator(model, points_per_side=16,
                                     pred_iou_thresh=0.8,
                                     stability_score_thresh=0.9,
                                     min_mask_region_area=256)
    return {"kind": "sam2",
            "generate": lambda rgb: [r["segmentation"].astype(bool)
                                     for r in gen.generate(rgb)],
            "text_generate": None, "model": model, "device": dev,
            "has_text": False}


def _sam2_config_for(path: str) -> str:
    """SAM 2's own package needs the architecture config implied by the name."""
    name = os.path.basename(path).lower()
    two_one = "2.1" in name or "2_1" in name
    ver = "sam2.1" if two_one else "sam2"
    for tag, size in (("large", "l"), ("base_plus", "b+"), ("base", "b+"),
                      ("small", "s"), ("tiny", "t")):
        if tag in name:
            return f"configs/{ver}/{ver}_hiera_{size}.yaml"
    return f"configs/{ver}/{ver}_hiera_s.yaml"


def _load_sam1(path, device):
    import torch

    name = os.path.basename(path).lower()
    # SAM-HQ is a different architecture with the same file naming, so vanilla
    # segment_anything loads it into a state_dict mismatch rather than failing
    # cleanly.  ComfyUI users commonly have both sitting side by side.
    if any(h in name for h in _HQ_HINTS):
        try:
            from segment_anything_hq import (SamAutomaticMaskGenerator,
                                             sam_model_registry)
        except Exception as exc:
            raise SAMUnavailable(
                "this is a SAM-HQ checkpoint; it needs "
                "'pip install segment-anything-hq', or use a plain sam_vit_*.pth"
            ) from exc
    else:
        from segment_anything import SamAutomaticMaskGenerator, sam_model_registry

    dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
    arch = ("vit_h" if "vit_h" in name else "vit_l" if "vit_l" in name
            else "vit_t" if "mobile" in name else "vit_b")
    if arch not in sam_model_registry:
        arch = "vit_b"
    model = sam_model_registry[arch](checkpoint=path)
    model.to(device=dev)
    gen = SamAutomaticMaskGenerator(model, points_per_side=16,
                                    pred_iou_thresh=0.88,
                                    stability_score_thresh=0.92,
                                    min_mask_region_area=256)
    return {"kind": "sam1",
            "generate": lambda rgb: [r["segmentation"].astype(bool)
                                     for r in gen.generate(rgb)],
            "text_generate": None, "model": model, "device": dev,
            "has_text": False}


def describe(model_path: str) -> str:
    """One line about a checkpoint, for the log and the GUI.

    Worth having because a folder of SAM weights is full of look-alikes -- HQ
    variants, safetensors, and stubs far too small to be the model they are
    named after -- and each fails differently.
    """
    name = os.path.basename(model_path)
    size = os.path.getsize(model_path) / 1e6 if os.path.isfile(model_path) else 0.0
    kind = _kind(model_path)
    bits = [f"{name} ({size:.0f} MB, {kind})"]
    if any(h in name.lower() for h in _HQ_HINTS):
        bits.append("SAM-HQ: needs segment-anything-hq")
    if name.lower().endswith(".safetensors"):
        bits.append("will be converted to .pt once")
    if size and size < 20:
        bits.append("suspiciously small for a SAM checkpoint")
    if kind != "sam3":
        bits.append("no text prompting (SAM 3 only)")
    return "; ".join(bits)


def available(model_path: str) -> bool:
    try:
        _load(model_path)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# the mask
# --------------------------------------------------------------------------
def segment(bgr: np.ndarray, model_path: str, max_edge: int = 768, device: str = ""):
    """Every region SAM finds, as boolean arrays at ``bgr``'s resolution.

    Runs at reduced resolution on purpose: the geometry downstream is measured
    in angles from lines at 1600 px, so a mask finer than a few pixels is wasted
    work, and SAM's cost grows quickly with side length.
    """
    entry = _load(model_path, device)
    h, w = bgr.shape[:2]
    rgb = _reduced(bgr, max_edge)
    with _LOCK:
        raw = entry["generate"](rgb)
    return [_to_full(m, h, w) for m in raw]


def _reduced(bgr, max_edge):
    """RGB copy at a working resolution.

    Deliberately small: the geometry downstream is angles measured from lines at
    1600 px, so a mask finer than a few pixels is wasted work, and SAM's cost
    grows quickly with side length.
    """
    h, w = bgr.shape[:2]
    s = min(1.0, float(max_edge) / max(h, w))
    small = cv2.resize(bgr, (max(1, int(w * s)), max(1, int(h * s))),
                       interpolation=cv2.INTER_AREA) if s < 1.0 else bgr
    return cv2.cvtColor(small, cv2.COLOR_BGR2RGB)


def _to_full(m, h, w):
    m = np.asarray(m).astype(np.uint8)
    if m.shape[:2] != (h, w):
        m = cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST)
    return m.astype(bool)


def line_density(mask: np.ndarray, seg: np.ndarray, samples: int = 7) -> float:
    """Straight-line length per unit area inside a region, normalised.

    This is what turns SAM's boundaries into a decision.  A facade segment is
    threaded with long straight edges; a tree canopy of the same area has almost
    none, however intricate its outline.
    """
    area = float(mask.sum())
    if area <= 0 or len(seg) == 0:
        return 0.0
    h, w = mask.shape[:2]
    t = np.linspace(0.0, 1.0, samples)[None, :]
    xs = np.clip((seg[:, 0:1] + (seg[:, 2:3] - seg[:, 0:1]) * t).astype(int), 0, w - 1)
    ys = np.clip((seg[:, 1:2] + (seg[:, 3:4] - seg[:, 1:2]) * t).astype(int), 0, h - 1)
    inside = mask[ys, xs].mean(axis=1)
    lengths = np.hypot(seg[:, 2] - seg[:, 0], seg[:, 3] - seg[:, 1])
    return float((lengths * inside).sum() / np.sqrt(area))


def mask_from_segments(bgr, seg, model_path, min_density_ratio=0.25,
                       max_edge=768, device=""):
    """Regions to ignore: everything SAM found that holds no straight lines.

    The threshold is relative -- a fraction of the densest region in this
    picture -- rather than absolute, because line density per unit area scales
    with how much of the frame the building occupies and how far away it is.
    """
    regions = segment(bgr, model_path, max_edge=max_edge, device=device)
    if not regions:
        return None, "SAM returned no regions"
    dens = np.array([line_density(m, seg) for m in regions])
    if not np.isfinite(dens).any() or dens.max() <= 0:
        return None, "no straight lines inside any SAM region"
    keep_thr = dens.max() * float(min_density_ratio)
    mask = np.zeros(bgr.shape[:2], bool)
    n_masked = 0
    for m, d in zip(regions, dens):
        if d < keep_thr:
            mask |= m
            n_masked += 1
    return mask, f"SAM: {len(regions)} regions, {n_masked} without line structure"


def mask_from_text(bgr, model_path, prompt=DEFAULT_REJECT_PROMPT, max_edge=768,
                   device=""):
    """SAM 3's concept head, when the checkpoint has one.

    Prompts the *rejects* rather than the building: concrete countable nouns
    ground reliably, "architecture" does not, and a missed piece of building is
    lost evidence rather than a cosmetic flaw.
    """
    entry = _load(model_path, device)
    fn = entry.get("text_generate")
    if fn is None or not entry.get("has_text"):
        raise SAMUnavailable("this checkpoint has no text-promptable head "
                             "(SAM 1 and SAM 2 have none; SAM 3 via ultralytics does)")
    words = [p.strip() for p in prompt.split(",") if p.strip()]
    h, w = bgr.shape[:2]
    rgb = _reduced(bgr, max_edge)
    with _LOCK:
        raw = fn(rgb, words)
    if not raw:
        return None, f"SAM text prompt matched nothing for: {', '.join(words)}"
    mask = np.zeros((h, w), bool)
    for m in raw:
        mask |= _to_full(m, h, w)
    return mask, f"SAM 3 text prompt ({len(raw)} region(s)): {', '.join(words)}"


def has_text_head(model_path: str) -> bool:
    try:
        return bool(_load(model_path).get("has_text"))
    except Exception:
        return False
