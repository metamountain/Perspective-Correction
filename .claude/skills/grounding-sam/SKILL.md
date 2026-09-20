---
name: grounding-sam
description: Detect-then-segment masking with Grounding DINO + SAM2, run directly in Python (no ComfyUI server). Use when exploring an automatic building mask as a third mask source beside BiRefNet/file, or when deriving roi_x from a detected building box. Covers the local model paths, the two-interpreter split, the direct-load recipe (transformers GroundingDINO + the official sam2 package), Windows pitfalls, and the round-trip gate before anything becomes a default.
---

# Grounding DINO + SAM2 masking — Perspective-Correction

Automatic building mask via detect-then-segment: `boxes = grounding_dino(image, "building")` →
`mask = sam2(image, boxes)`. A text prompt localizes the building (no 40-region guess — that invented
criterion is what killed SAM1), then SAM2 turns that one box into a tight mask. Runs **directly in
Python** in the ComfyUI interpreter: no ComfyUI server, no GUI, no HTTP.

This is a **mask source only**. It plugs into the existing `--mask file` path (one PNG per photo),
exactly like BiRefNet's `--mask-export`. The line pipeline is untouched.

## 1. Everything is already installed (checked 2026-09-13)

No downloads — this box has 403 egress, so it must all be local, and it is. The
weights are **vendored into the repo** under `models\` (copied from ComfyUI), so a
probe runs against the repo copy without touching the ComfyUI install:

| what | where (BPC-local first; source in `D:\ComfyUI_windows_portable\`) | notes |
|---|---|---|
| GDINO weights (HF format) | `models\GroundingDINO\`  ← from `ComfyUI\models\grounding-dino\` | `config.json` + `model.safetensors` (689 MB) + tokenizer = Swin-T OGC; ComfyUI also has `groundingdino_swinb_cogcoor.pth` (895 MB, original format) |
| SAM2 checkpoints | `models\sam2\`  ← from `ComfyUI\models\sam2\` | `sam2.1_hiera_base_plus.pt` (324 MB); config YAMLs under `models\sam2\configs\sam2.1\`; `-fp16.safetensors` + small/tiny in ComfyUI |
| SAM3 (text-prompted, alt route) | `ComfyUI\models\sam3\sam3.pt` (not vendored) | 3.29 GB — the §3a "concept prompt" option: one model instead of two |
| BiRefNet (the mask engine this box actually uses) | `models\BiRefNet\` | `BiRefNet-HR.safetensors` (424 MB) + `BiRefNet_lite.safetensors` (169 MB, CPU-capable PVT-v2 backbone) + `birefnet.py` / `birefnet_lite.py` arch; see `src/pc/birefnet.py` |
| `transformers` | `python_embeded\Lib\site-packages\` | loads GDINO via `AutoModelForZeroShotObjectDetection` |
| `sam2` (official pkg, v1.1.0) | `python_embeded\Lib\site-packages\sam2` | `build_sam.py` + `sam2_image_predictor.py`; config YAMLs also vendored at `models\sam2\configs\` |
| ComfyUI node graph (server route only) | `custom_nodes\comfyui-grounding` | `GroundingDetector` → `Sam2Segment`, or the fused `GroundingMaskDetector` |

## 2. Interpreter (same split as the debug skill)

**Corrected 2026-09-15 by measurement:** system python 3.12 DOES have `transformers` (5.17.0)
and torch 2.12.1+cu130, and GDINO loads and runs there — this file's earlier claim that the
import fails in system python was false and produced a wrong diagnosis. `python_embeded` is
still required for `sam2`, which system python lacks. No tkinter is needed for a mask probe, so this
is headless-friendly (unlike BiRefNet's GUI-adjacent path). Quick check:

```
"D:\ComfyUI_windows_portable\python_embeded\python.exe" -c "import transformers, sam2; print('ok')"
```

## 3. The direct-Python recipe

```python
# run with python_embeded. Force offline first — egress is 403 here and transformers will hang on HF otherwise.
import os; os.environ["HF_HUB_OFFLINE"] = "1"; os.environ["TRANSFORMERS_OFFLINE"] = "1"
# WinError-6 torch/triton shim goes ABOVE the torch import — copy it from tools/benchmark_mask.py.

import numpy as np, cv2, torch
from PIL import Image

REPO    = r"D:\Coding\Perspective-Correction"
GDINO_DIR = REPO + r"\models\GroundingDINO"
SAM2_CKPT = REPO + r"\models\sam2\sam2.1_hiera_base_plus.pt"
SAM2_CFG  = REPO + r"\models\sam2\configs\sam2.1\sam2.1_hiera_b+.yaml"

# --- detect: boxes for "building" -------------------------------------------
from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection
proc = AutoProcessor.from_pretrained(GDINO_DIR)
dino = AutoModelForZeroShotObjectDetection.from_pretrained(GDINO_DIR, attn_implementation="eager").cuda().eval()

img_bgr = cv2.imread(path)                       # full resolution — keep both models on the SAME pixels
pil     = Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))
inputs  = proc(images=pil, text=["building"], return_tensors="pt").cuda()
with torch.no_grad():
    out = dino(**inputs)
res = proc.post_process_grounded_object_detection(
        out, inputs.input_ids, threshold=0.25, text_threshold=0.25,
        target_sizes=[pil.size[::-1]])[0]         # boxes are xyxy in pixels
box = res["boxes"][int(res["scores"].argmax())].tolist()   # [x0, y0, x1, y1]

del dino; torch.cuda.empty_cache()               # free ~0.7 GB before loading SAM2

# --- segment: box -> mask ----------------------------------------------------
from sam2.build_sam import build_sam2
from sam2.sam2_image_predictor import Sam2ImagePredictor
m    = build_sam2(SAM2_CFG, SAM2_CKPT).cuda().eval()
pred = Sam2ImagePredictor(m)
pred.set_image(np.asarray(pil))                  # RGB, same pixels as the box
mask, scores, _ = pred.predict(box=box, multimask_output=False)   # mask: HxW bool

cv2.imwrite(out_png, (mask[0] * 255).astype(np.uint8))            # consumed later via --mask file
```

Keep both models on the **same full-resolution image** so GDINO's pixel boxes feed SAM2 directly — no
normalization mismatch. `multimask_output=False` returns one mask; if a busy facade bleeds, post-filter
to the largest connected component or intersect with the box before saving.

## 4. Windows / environment pitfalls (from the debug skill + this box)

1. **WinError 6 on torch import** — Triton's AMD probe can throw a bare `OSError [WinError 6]`
   (Defender, transient). Install the `OSError -> FileNotFoundError` shim from `tools/benchmark_mask.py`
   *above* any torch import, or just retry.
2. **Force offline** — `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1`, and load GDINO from the local
   folder path (never an `hf_id`), or transformers tries HuggingFace and hangs on the 403.
3. **RAM: load one model at a time.** GDINO (~0.7 GB) then `del` + `empty_cache()` before SAM2
   (~0.3–0.4 GB). This is far lighter than starting the whole ComfyUI server — the reason to go
   direct-Python at all.
4. **fp16** — use the `-fp16.safetensors` checkpoint or wrap inference in `torch.autocast` to halve
   VRAM; the 4090 has room either way, but it keeps the probe snappy.
5. **Config/ckpt size must match.** Pair each `sam2_hiera_*.yaml` with its hiera-size checkpoint
   (`b+` ↔ base_plus). If state-dict keys mismatch (e.g. a 2.0 config against the 2.1 weights), swap in
   the fp16 safetensors or the matching config rather than forcing it.

## 5. The gate before this is anything but a note (§3a discipline)

Per `knowledge.md` §3a: SAM1 was killed by its *invented selection criterion*, not its mask quality —
and any reintroduction must win the **round-trip benchmark on the same asset pool** before it becomes a
default. So the first run is a measurement, not a feature:

- pick one corner case (`lochfassade`) + one clean facade;
- write the GDINO box overlay + the SAM2 mask PNG and eyeball them (code that compiles is not a mask
  that's right — the "does it read as the building" check);
- score against the existing **BiRefNet cached mask** and **no-mask** with `tools/benchmark_mask.py`'s
  metric (masking's effect is on the *worst case*, not the mean);
- the mask must also pass the runtime credibility check (`MK.credible`, border>centre) — degenerate
  masks are refused, same as BiRefNet's.

Only if it beats both does it earn a `mask_mode` slot beside `birefnet`/`file` (optional-backend
convention: any new dependency installs `--no-deps`).

## 6. roi_x synergy (the second idea)

A `"building"` box gives the building's **outer** left/right extent — a good automatic default for
roi_x (replaces the useless 0–100 / arbitrary 20–80) and excludes sky/ground clutter. But it spans
*both* facades, and roi_x's job is to isolate *one* facade in a corner view. The full win: GDINO box =
the strip's outer edges + the corner seam the line detector **already** finds (the strong vertical where
the two facades meet) = the inner edge → auto-derived roi_x for corner views. That is a design to measure
against the existing 20/80 default, not something to hard-code blind.

## 7. Validation

- Probe produces a box overlay + mask PNG you can open and judge by eye.
- `MK.credible` accepts the mask; border>centre sanity holds.
- Round-trip number on the pool beats BiRefNet / no-mask on the worst case before any default changes.
