#!/usr/bin/env python3
"""Re-measure mask effectiveness on this machine.

    python tools/benchmark_mask.py                          # all five configs
    python tools/benchmark_mask.py --only "f unknown, BiRefNet-HR"
    python tools/benchmark_mask.py --limit 3                # first three photos only
    python tools/benchmark_mask.py --only "f known, dynamic" --quiet

**The warp's focal length is fixed ground truth, not the estimate.** The round
trip is exact only when the warp is a known rotation in the *true* camera model.
Feeding the estimator's own focal guess into the warp (this script's first
version did) injects the estimation error into the ground truth itself: on the
18 test photos that inflated "f unknown, mask off" from ~1.1 deg to 4.1 deg
mean before anyone had measured anything. The default is a fixed 24 mm warp,
matching tools/benchmark_detectors.py; --warp-focal 0 restores the
estimate-driven warp if you specifically want to see how bad it is.

**Run under the ComfyUI python_embeded for the BiRefNet configs.** The system
python has torch but not `transformers`, which the BiRefNet architecture
imports, so every BiRefNet pass fails there with a module error:

    "D:\\ComfyUI_windows_portable\\python_embeded\\python.exe" tools/benchmark_mask.py

One config per call keeps each run under the 120 s terminal timeout; the
BiRefNet configs take ~65 s over all 18 photos.
"""
from __future__ import annotations

# Triton's AMD backend probe runs subprocess.check_output(["rocm-sdk", ...])
# during backend discovery. On this Windows box process creation intermittently
# fails with a bare OSError (WinError 6) instead of FileNotFoundError, which the
# driver does not catch -- so importing torch/triton can kill the whole run.
# Convert that OSError to FileNotFoundError, which the driver handles cleanly.
import subprocess as _sp
_sp_co = _sp.check_output
def _sp_co_shim(*a, **k):
    try:
        return _sp_co(*a, **k)
    except OSError as e:
        raise FileNotFoundError(str(e)) from e
_sp.check_output = _sp_co_shim

import argparse
import math
import os
import sys
import time

_TOOLS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_TOOLS)
sys.path.insert(0, os.path.join(_ROOT, "src"))
sys.path.insert(0, _TOOLS)

import cv2
import numpy as np

from pc import geometry as G
from pc import imageio as IO
from pc import model as M
from pc.config import Settings
from pc.pipeline import analyse
from benchmark_detectors import DELTAS, BORDER_GUARD, collect

HR = r"D:\ComfyUI_windows_portable\ComfyUI\models\RMBG\BiRefNet\BiRefNet-HR.safetensors"
DYN = r"D:\ComfyUI_windows_portable\ComfyUI\models\BiRefNet\General-dynamic.safetensors"

KNOWN = dict(focal_35mm=24.0, detect_max_edge=1600)
UNKNOWN = dict(focal_35mm=0.0, use_exif_focal=False, detect_max_edge=1600)

CONFIGS = (
    ("f known, mask off",      dict(detector="lsd", **KNOWN)),
    ("f known, BiRefNet-HR",   dict(detector="lsd", mask_mode="birefnet",
                                    birefnet_model=HR, **KNOWN)),
    ("f unknown, mask off",    dict(detector="lsd", **UNKNOWN)),
    ("f unknown, BiRefNet-HR", dict(detector="lsd", mask_mode="birefnet",
                                    birefnet_model=HR, **UNKNOWN)),
    ("f known, dynamic",       dict(detector="lsd", mask_mode="birefnet",
                                    birefnet_model=DYN, **KNOWN)),
)


def round_trip(path, settings, warp_focal=24.0, edge=1600, inner=BORDER_GUARD):
    """Per-delta errors in degrees for one photograph, or [].

    ``warp_focal`` is the fixed ground-truth focal for the warp, in mm.  0
    means "use the base image's own estimate" -- see the module docstring for
    why that is not a valid ground truth.
    """
    bgr = IO.load(path).bgr
    h, w = bgr.shape[:2]
    s = min(1.0, edge / max(w, h))
    full = cv2.resize(bgr, (int(w * s), int(h * s)),
                      interpolation=cv2.INTER_AREA) if s < 1 else bgr
    fh, fw = full.shape[:2]
    m = int(min(fh, fw) * inner)
    m0, _, _, _, _ = analyse(full[m:fh - m, m:fw - m], settings)
    if m0.f is None:
        return []
    f35 = warp_focal if warp_focal > 0 else (settings.focal_35mm or m0.f)
    K = G.intrinsics(M.focal_px_from_35mm(f35, fw, fh), fw / 2.0, fh / 2.0)
    out = []
    for dr, dp in DELTAS:
        Rd = G.correction_rotation(math.radians(dr), math.radians(dp))
        warped = cv2.warpPerspective(full, G.homography(K, Rd), (fw, fh),
                                     flags=cv2.INTER_LANCZOS4,
                                     borderMode=cv2.BORDER_REPLICATE)
        m1, _, _, _, _ = analyse(warped[m:fh - m, m:fw - m], settings)
        if m1.f is None:
            continue
        expect = Rd @ m0.up
        expect /= np.linalg.norm(expect)
        got = m1.up / np.linalg.norm(m1.up)
        out.append(math.degrees(math.acos(min(1.0, abs(float(got @ expect))))))
    return out


def run(files, label, settings, warp_focal=24.0, quiet=False):
    errs, t0, failed = [], time.time(), 0
    for f in files:
        try:
            e = round_trip(f, settings, warp_focal)
        except Exception as exc:
            print("  {} / {}: {}".format(label, os.path.basename(f), exc))
            e = []
        if not e:
            failed += 1
            continue
        errs += e
        if not quiet:
            print("    {:44s} mean {:6.3f}".format(os.path.basename(f)[:43],
                                                   float(np.mean(e))), flush=True)
    dt = time.time() - t0
    if not errs:
        # Every photograph failed (e.g. a missing dependency).  Say so plainly
        # instead of crashing on an empty percentile -- the reader needs to see
        # WHICH configuration is broken, not a traceback.
        print("{:28s} NO MEASUREMENTS ({} of {} failed)".format(
            label, failed, len(files)), flush=True)
        return errs
    print("{:28s} mean {:6.3f}   p90 {:6.3f}   worst {:6.3f}   ({} measurements, "
          "{} no fit, {:.0f}s)".format(
              label, float(np.mean(errs)), float(np.percentile(errs, 90)),
              float(np.max(errs)), len(errs), failed, dt), flush=True)
    return errs


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", nargs="?", default=os.path.join(_ROOT, "tests", "assets"))
    ap.add_argument("--only", default="",
                    help="pipe-separated config labels to run (labels contain "
                         "commas, so the separator is |); default all")
    ap.add_argument("--limit", type=int, default=0,
                    help="first N photographs only (debug)")
    ap.add_argument("--warp-focal", type=float, default=24.0,
                    help="fixed ground-truth focal for the warp in mm; "
                         "0 = base estimate (invalid ground truth, see docstring)")
    ap.add_argument("--quiet", action="store_true", help="suppress per-image lines")
    args = ap.parse_args(argv)

    files = collect(args.folder)
    if args.limit > 0:
        files = files[:args.limit]
    wanted = [c.strip() for c in args.only.split("|") if c.strip()]
    if not wanted:
        wanted = [label for label, _ in CONFIGS]
    bad = [w for w in wanted if w not in dict(CONFIGS)]
    if bad:
        print("unknown config(s): " + ", ".join(bad))
        print("available: " + ", ".join(label for label, _ in CONFIGS))
        return 2

    print("# {} photograph(s) x {} rotations, LSD detector, border guard on, "
          "warp f={:.0f}mm".format(len(files), len(DELTAS), args.warp_focal), flush=True)
    for label, kw in CONFIGS:
        if label not in wanted:
            continue
        run(files, label, Settings().replace(**kw), args.warp_focal, quiet=args.quiet)
    print("\n# degrees between the recovered up and the one the known rotation implies.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
