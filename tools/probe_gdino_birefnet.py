#!/usr/bin/env python3
"""One-off probe: does cropping to a Grounding DINO "building" box and then
matte-ing that crop with BiRefNet give a better building mask than full-frame
BiRefNet alone?

Section-3a measurement gate -- NOT a production path.  BiRefNet stays the matte
engine (it is good and fast); Grounding DINO is used only to localise the facade
so the segmenter cannot lock onto foreground clutter.  Both "plain" and
"combined" use the SAME model (HR) so the IoU between them isolates the crop
effect; the trusted HR-cached mask in tests/assets/masks/ is the reference both
are scored against.

    "D:\\ComfyUI_windows_portable\\python_embeded\\python.exe" tools/probe_gdino_birefnet.py

Prints a small table and writes per-photo overlays (plain vs combined) + the
combined mask PNG to analysis/gdino_birefnet_probe/ for eyeballing.  Cached
masks are white=ignore, so the building region is the black part (<127).
"""
from __future__ import annotations

import os

# Fully offline: every weight is vendored in models/, and egress 403s anyway.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_DATASETS_OFFLINE"] = "1"

# Triton's AMD backend probe runs subprocess.check_output(["rocm-sdk", ...]) during
# torch import; on this Windows box that intermittently raises a bare OSError
# (WinError 6) instead of FileNotFoundError.  Convert it so the driver catches it.
import subprocess as _sp

_sp_co = _sp.check_output


def _sp_co_shim(*a, **k):
    try:
        return _sp_co(*a, **k)
    except OSError as e:
        raise FileNotFoundError(str(e)) from e


_sp.check_output = _sp_co_shim

import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GDINO_DIR = os.path.join(_ROOT, "models", "GroundingDINO")
# HR is the known-good pairing for the shared birefnet.py arch.  The lite
# checkpoint has a smaller backbone (embed 96 vs 192) and needs its own arch
# (birefnet_lite.py), which bpc.birefnet does not select yet -- so it is vendored
# but not usable through build_mask until that wiring exists.
BIREF_WT = os.path.join(_ROOT, "models", "BiRefNet", "BiRefNet-HR.safetensors")
ASSETS = os.path.join(_ROOT, "tests", "assets")
MASKS = os.path.join(ASSETS, "masks")
OUT = os.path.join(_ROOT, "analysis", "gdino_birefnet_probe")

# One corner case (hard) + two clean single-facade shots; all have a cached HR mask.
STEMS = ("lochfassade", "heilsbronn-d7000-27mm", "quaker-barn-with-office-fit-out")

MAX_EDGE = 1280
BOX_PAD_FRAC = 0.04   # grow the GDINO box a little so the facade edge is not clipped


def _load_bgr(path: str):
    import cv2
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(path)
    h, w = img.shape[:2]
    s = min(1.0, MAX_EDGE / max(h, w))
    if s < 1.0:
        img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    return img


def _find_asset(stem: str):
    for ext in (".jpg", ".jpeg", ".JPG", ".png", ".webp"):
        p = os.path.join(ASSETS, stem + ext)
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f"no asset for {stem}")


def _load_ref(stem: str, w: int, h: int):
    """Cached HR mask -> building-region bool (black part), resized to (w, h)."""
    import cv2
    ref = os.path.join(MASKS, stem + ".png")
    if not os.path.exists(ref):
        return None
    raw = cv2.imread(ref, cv2.IMREAD_GRAYSCALE)
    if raw is None:
        return None
    raw = cv2.resize(raw, (w, h), interpolation=cv2.INTER_NEAREST)
    return raw < 127   # white=ignore, so building = black


def load_gdino():
    """Load Grounding DINO once; the processor and model are reused per prompt."""
    from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection

    t0 = time.time()
    proc = AutoProcessor.from_pretrained(GDINO_DIR, local_files_only=True)
    model = AutoModelForZeroShotObjectDetection.from_pretrained(
        GDINO_DIR, local_files_only=True).to("cuda").eval()
    print(f"# GDINO loaded in {time.time() - t0:.1f}s", flush=True)
    return proc, model


def detect(proc, model, bgr, prompt):
    """Top-scoring box for one text prompt on one image -> (box_xyxy_abs, score, label).

    ``[0]`` of the post-process is a dict {scores, boxes, text_labels}; passing
    ``target_sizes`` makes the boxes absolute pixels in the shown image.  The keep
    filter prefers boxes whose label actually contains the prompt concept and falls
    back to the overall top box if none do (so a bare "building" that GDINO relabels
    still returns *something* rather than nothing).
    """
    import cv2
    import torch
    from PIL import Image

    h, w = bgr.shape[:2]
    pil = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    inputs = proc(text=[prompt], images=pil, return_tensors="pt").to("cuda")
    with torch.no_grad():
        res = model(**inputs)
    det = proc.post_process_grounded_object_detection(
        res, inputs["input_ids"], threshold=0.20, text_threshold=0.20,
        target_sizes=[(h, w)])[0]
    scores = det["scores"].cpu().numpy()
    boxes = det["boxes"].cpu().numpy()
    labels = [str(x) for x in det["text_labels"]]
    n = len(scores)
    if n == 0:
        return [0, 0, w, h], 0.0, "none"
    keep = [i for i, lab in enumerate(labels) if prompt.lower() in lab.lower()]
    if not keep:
        keep = list(range(n))
    best = int(max(keep, key=lambda i: float(scores[i])))
    return boxes[best].tolist(), float(scores[best]), labels[best]


def _iou(a, b):
    u = float((a | b).sum())
    return float((a & b).sum()) / u if u else float("nan")


def _tint(base, m, color, alpha):
    import numpy as np
    b = base.copy()
    b[m] = (b[m] * (1 - alpha) + np.array(color) * alpha).astype("uint8")
    return b


def _crop_box(bgr, box):
    """Pad the GDINO box by BOX_PAD_FRAC and clamp to the frame -> (x0,y0,x1,y1)."""
    h, w = bgr.shape[:2]
    px, py = int(BOX_PAD_FRAC * w), int(BOX_PAD_FRAC * h)
    x0 = max(0, int(box[0]) - px)
    y0 = max(0, int(box[1]) - py)
    x1 = min(w, int(box[2]) + px)
    y1 = min(h, int(box[3]) + py)
    return x0, y0, x1, y1


def _combined_mask(bgr, box, BN):
    """BiRefNet on the padded crop, pasted back onto a full-frame zero mask.

    Outside the box is declared not-building, which is exactly what the crop is for:
    anything BiRefNet would have grabbed in the cut-away margin (sky, ground, a car)
    simply cannot enter the mask.  Returns ``(comb, (x0,y0,x1,y1))``.
    """
    import numpy as np
    h, w = bgr.shape[:2]
    x0, y0, x1, y1 = _crop_box(bgr, box)
    comb = np.zeros((h, w), bool)
    crop = bgr[y0:y1, x0:x1]
    if crop.size:
        ign_crop, _ = BN.build_mask(crop, BIREF_WT)
        comb[y0:y1, x0:x1] = ~ign_crop
    return comb, (x0, y0, x1, y1)


def main():
    import cv2
    from bpc import birefnet as BN

    # Prompts come from argv (comma-separated); default keeps the original single run.
    prompts = ["building"]
    if len(sys.argv) > 1:
        prompts = [p.strip() for p in sys.argv[1].split(",") if p.strip()]

    # Plain full-frame BiRefNet is prompt-independent -> compute it once, not per prompt.
    bgrs, plains = {}, {}
    t0 = time.time()
    for stem in STEMS:
        bgrs[stem] = _load_bgr(_find_asset(stem))
        ign, _ = BN.build_mask(bgrs[stem], BIREF_WT)
        plains[stem] = ~ign
    print(f"# plain full-frame BiRefNet done in {time.time() - t0:.1f}s", flush=True)

    proc, model = load_gdino()
    for prompt in prompts:
        pdir = os.path.join(OUT, prompt.replace(" ", "_"))
        os.makedirs(pdir, exist_ok=True)
        print(f"\n# prompt = {prompt!r}   ->  {pdir}")
        print("  stem | score | box xyxy(x0,y0,x1,y1) | box%frame | "
              "plain% | comb% | IoU p~c | IoU c~ref")
        for stem in STEMS:
            bgr = bgrs[stem]
            h, w = bgr.shape[:2]
            box, score, label = detect(proc, model, bgr, prompt)
            comb, (x0, y0, x1, y1) = _combined_mask(bgr, box, BN)
            plain = plains[stem]
            ref = _load_ref(stem, w, h)
            ipc = _iou(plain, comb)
            icr = _iou(comb, ref) if ref is not None else float("nan")
            cov = (x1 - x0) * (y1 - y0) / (w * h) * 100

            def f(x):
                return "  n/a " if x != x else f"{x:6.3f}"
            print(f"  {stem[:28]:28s} {score:5.2f}  {(x0, y0, x1, y1)!s:22s} "
                  f"{cov:6.1f}  {f(plain.mean())} {f(comb.mean())}  {f(ipc)}   {f(icr)}")

            ov = bgr.copy()
            cv2.rectangle(ov, (x0, y0), (x1, y1), (255, 200, 0), 3)
            cv2.putText(ov, f"{label} {score:.2f}", (x0, max(0, y0 - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 200, 0), 2)
            cv2.imwrite(os.path.join(pdir, f"{stem}_box.png"), ov)
            cv2.imwrite(os.path.join(pdir, f"{stem}_plain.png"),
                        _tint(bgr.copy(), plain, (0, 200, 0), 0.45))
            cv2.imwrite(os.path.join(pdir, f"{stem}_comb.png"),
                        _tint(bgr.copy(), comb, (0, 0, 220), 0.45))
            cv2.imwrite(os.path.join(pdir, f"{stem}_comb_mask.png"),
                        (comb * 255).astype("uint8"))
    print(f"\n# results written to {OUT}/<prompt>/")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(_ROOT, "src"))
    raise SystemExit(main())
