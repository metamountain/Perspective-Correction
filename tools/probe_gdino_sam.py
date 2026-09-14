#!/usr/bin/env python3
"""One-off probe: does Grounding DINO ("building") + SAM2 box->mask beat BiRefNet?

This is the section-3a measurement gate for reintroducing a segmenter as a mask
source -- NOT a production path.  It runs the detect-then-segment recipe in the
ComfyUI ``python_embeded`` interpreter (torch + transformers + the official sam2
package, no tkinter), loads each model once, and scores every SAM mask by IoU
against the trusted cached BiRefNet mask already in tests/assets/masks/.

    "D:\\ComfyUI_windows_portable\\python_embeded\\python.exe" tools/probe_gdino_sam.py

Outputs a small table to stdout and writes box overlays + mask PNGs to
analysis/gdino_sam_probe/ for eyeballing.  A high IoU against the BiRefNet mask
means the new route is at least as good as the one we already trust; a low one
means stop here and do not wire it in.
"""
from __future__ import annotations

import os

# Force fully offline: every weight is vendored in models/, so no network call
# should happen -- and on this box egress 403s anyway, so fail fast if one does.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_DATASETS_OFFLINE"] = "1"

# Triton's AMD backend probe runs subprocess.check_output(["rocm-sdk", ...]) during
# torch import; on this Windows box that intermittently raises a bare OSError
# (WinError 6) instead of FileNotFoundError, which the driver does not catch and
# which kills the whole run.  Convert it to FileNotFoundError (handled cleanly).
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
SAM2_CKPT = os.path.join(_ROOT, "models", "sam2", "sam2.1_hiera_base_plus.pt")
SAM2_CFGDIR = os.path.join(_ROOT, "models", "sam2", "configs")
ASSETS = os.path.join(_ROOT, "tests", "assets")
MASKS = os.path.join(ASSETS, "masks")
OUT = os.path.join(_ROOT, "analysis", "gdino_sam_probe")

# One corner case (hard) + two clean single-facade shots; all three have a cached
# BiRefNet mask to score against.
STEMS = ("lochfassade", "heilsbronn-d7000-27mm", "quaker-barn-with-office-fit-out")

MAX_EDGE = 1280


def _load_rgb(path: str):
    import cv2
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(path)
    h, w = img.shape[:2]
    s = min(1.0, MAX_EDGE / max(h, w))
    if s < 1.0:
        img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def _find_asset(stem: str):
    import os as _os
    for ext in (".jpg", ".jpeg", ".JPG", ".png", ".webp"):
        p = _os.path.join(ASSETS, stem + ext)
        if _os.path.exists(p):
            return p
    raise FileNotFoundError(f"no asset for {stem}")


def run_gdino(stems):
    """Load Grounding DINO once, return {stem: (box_xyxy, score, rgb)}."""
    import torch
    from PIL import Image
    from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection

    t0 = time.time()
    proc = AutoProcessor.from_pretrained(GDINO_DIR, local_files_only=True)
    model = AutoModelForZeroShotObjectDetection.from_pretrained(
        GDINO_DIR, local_files_only=True).to("cuda").eval()
    print(f"# GDINO loaded in {time.time() - t0:.1f}s", flush=True)

    out = {}
    for stem in stems:
        rgb = _load_rgb(_find_asset(stem))
        pil = Image.fromarray(rgb)
        inputs = proc(text=["building"], images=pil, return_tensors="pt").to("cuda")
        with torch.no_grad():
            res = model(**inputs)
        det = proc.post_process_grounded_object_detection(
            res, inputs["input_ids"], threshold=0.20, text_threshold=0.20)[0]
        boxes = [d for d in det if "building" in d["label"].lower()]
        if not boxes:
            boxes = det  # fall back to whatever it found, and flag it below
        best = max(boxes, key=lambda d: float(d["score"]))
        out[stem] = (best["box"], float(best["score"]), rgb,
                     "building" if boxes else f"{best['label']!r}")
    del model, proc
    torch.cuda.empty_cache()
    return out


def run_sam2(stems, gdino):
    """Load SAM2 once; for each box predict a mask and score it vs BiRefNet."""
    import cv2
    import numpy as np
    import torch
    from hydra import initialize_config_dir
    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor

    device = "cuda" if torch.cuda.is_available() else "cpu"
    t0 = time.time()
    initialize_config_dir(config_dir=SAM2_CFGDIR, version_base=None)
    model = build_sam2("sam2/sam2_hiera_b+", SAM2_CKPT, device=device)
    pred = SAM2ImagePredictor(model)
    print(f"# SAM2 loaded in {time.time() - t0:.1f}s on {device}", flush=True)

    rows = []
    for stem in stems:
        box, score, rgb, label = gdino[stem]
        x0, y0, x1, y1 = [int(round(v)) for v in box]
        h, w = rgb.shape[:2]
        x0, x1 = max(0, min(w - 1, x0)), max(0, min(w - 1, x1))
        y0, y1 = max(0, min(h - 1, y0)), max(0, min(h - 1, y1))

        pred.set_image(rgb)
        masks, iou, _ = pred.predict(box=np.array([x0, y0, x1, y1]),
                                     multimask_output=False)
        mask = bool(masks[0])
        frac = float(mask.mean())

        # Score against the trusted cached BiRefNet mask (subject=white), trying
        # both polarities so an inverted convention still reads correctly.
        iou_txt = "n/a"
        ref = os.path.join(MASKS, stem + ".png")
        if os.path.exists(ref):
            raw = cv2.imread(ref, cv2.IMREAD_GRAYSCALE)
            raw = cv2.resize(raw, (w, h), interpolation=cv2.INTER_NEAREST) > 127
            inter_w = float((mask & raw).sum())
            union_w = float((mask | raw).sum()) or 1.0
            iou_white = inter_w / union_w
            iou_black = float((mask & ~raw).sum()) / (float((mask | ~raw).sum()) or 1.0)
            best_iou, pol = max(iou_white, iou_black), ("white" if iou_white >= iou_black else "inverted")
            iou_txt = f"{best_iou:.3f} ({pol})"

        # Overlays for eyeballing.
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        cv2.rectangle(bgr, (x0, y0), (x1, y1), (0, 200, 255), 3)
        cv2.putText(bgr, f"{label} {score:.2f}", (x0, max(0, y0 - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)
        cv2.imwrite(os.path.join(OUT, f"{stem}_box.png"), bgr)
        cv2.imwrite(os.path.join(OUT, f"{stem}_mask.png"),
                    (mask * 255).astype("uint8"))
        tint = bgr.copy()
        tint[mask] = (tint[mask] * 0.5 + np.array([0, 0, 160]) * 0.5).astype("uint8")
        cv2.imwrite(os.path.join(OUT, f"{stem}_overlay.png"), tint)

        rows.append((stem, label, score, (x0, y0, x1, y1), frac, iou_txt))
    del model, pred
    torch.cuda.empty_cache()
    return rows


def main():
    os.makedirs(OUT, exist_ok=True)
    gdino = run_gdino(STEMS)
    rows = run_sam2(STEMS, gdino)

    print("\n# stem | gdino label | box score | box xyxy | mask frac | IoU vs BiRefNet")
    for stem, label, score, box, frac, iou in rows:
        print(f"  {stem[:34]:34s} {label:10s} {score:5.2f}  {str(box):22s} "
              f"{frac:6.3f}   {iou}")
    print(f"\n# overlays + masks written to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
