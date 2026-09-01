#!/usr/bin/env python3
"""Run the line detectors against each other on real photographs.

    python tools/benchmark_detectors.py "D:\\Fotos"
    python tools/benchmark_detectors.py tests/assets --mask birefnet
    python tools/benchmark_detectors.py tests/assets --detectors lsd,fld --focal 24

**Why this exists rather than a synthetic benchmark.** A rendered scene carries
an exactly known camera pose, which makes it the right instrument for a
geometric question -- but the front end is not a geometric question. Whether LSD
finds the facade under real texture, JPEG blocking, foliage and lens distortion
is a statistical question about photographs, and flat rendered lines are LSD's
home turf while being out of distribution for a network trained on photographs.
Measuring the detector on synthetic data answers the wrong question in the
incumbent's favour.

**The round trip is how a real photograph gets ground truth.** Nobody knows the
true pitch of a photograph. But a rotation applied *here* is known exactly: if
the true world-up in camera coordinates is ``u0`` and the image is warped by
``R_d``, then the warped copy's up must be ``R_d @ u0``. Estimating from both
and comparing measures the estimator on real texture without anyone having to
know ``u0``. Errors are in degrees of angle between the recovered and expected
up vector.

**Read the p90 and the worst case, not only the mean.** This project's metric is
how many photographs it ruins, not how much perspective it removes, and a
detector that is superb on twenty images and catastrophic on one is worse here
than a duller one that is never catastrophic.

Two traps, both of which will silently produce meaningless numbers:

* **Fix the focal length.** Re-estimating ``f`` in both passes makes the metric
  self-inconsistent -- the two passes are then measuring different cameras.
  ``--focal`` is applied to every pass and defaults to 24 mm.
* **Do not use a cached mask folder.** Every warped copy needs its own mask,
  because the building has moved. ``--mask file`` is rejected for that reason;
  see ``tests/assets/masks/README.md``.
* **The border guard is load-bearing.** The warp has to invent the band that
  rotates in from outside the frame, and smearing edge pixels produces long
  straight streaks that the detector believes. Both passes are cropped by
  ``BORDER_GUARD`` before analysis. Without it this tool ranks detectors partly
  by how readily they bite on that artifact.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import cv2                                                       # noqa: E402
import numpy as np                                               # noqa: E402

from bpc import geometry as G                                    # noqa: E402
from bpc import imageio as IO                                    # noqa: E402
from bpc import model as M                                       # noqa: E402
from bpc.config import Settings                                  # noqa: E402
from bpc.pipeline import analyse                                 # noqa: E402

# Small rotations, in the range a real correction works over, and mixed so that
# roll-only, pitch-only and combined cases are all represented.
DELTAS = [(2.0, 0.0), (-3.0, 0.0), (0.0, 4.0), (0.0, -5.0), (2.5, 3.5), (-1.5, -2.5)]

ALL_DETECTORS = ("lsd", "fld", "hough", "mlsd", "hybrid", "union",
                 "deeplsd", "deep-hybrid", "deep-union")


BORDER_GUARD = 0.08


def round_trip_errors(path, settings, focal_35mm=24.0, edge=1600,
                      inner=BORDER_GUARD):
    """Every per-delta error in degrees for one photograph, or ``[]``.

    Returned unaggregated so the caller can pool them across a folder: a p90
    over six images and a p90 over their thirty-six measurements are different
    statistics, and the second is the one worth reading.

    ``inner`` discards the band the warp has to invent. ``BORDER_REPLICATE``
    invents it by smearing edge pixels into long straight streaks, and where a
    photograph's content reaches the frame edge -- the normal architectural case
    -- the detector reads those streaks as lines. Left in, this function
    measures its own border fill: on a facade that fills the frame it reported
    6.08 deg for an estimator that actually achieves 0.33. Base and warped copy
    are cropped identically so both keep the same focal length in pixels.
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
    # K describes the uncropped frame, because that is what is being rotated
    K = G.intrinsics(M.focal_px_from_35mm(focal_35mm, fw, fh), fw / 2.0, fh / 2.0)
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


def collect(folder):
    exts = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp")
    if os.path.isfile(folder):
        return [folder]
    return sorted(os.path.join(folder, f) for f in os.listdir(folder)
                  if f.lower().endswith(exts))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="a folder of photographs, or one file")
    ap.add_argument("--detectors", default="lsd,fld,hough",
                    help="comma separated; any of " + ", ".join(ALL_DETECTORS))
    ap.add_argument("--focal", type=float, default=24.0,
                    help="35mm-equivalent focal length, fixed in every pass "
                         "(re-estimating it makes the metric meaningless)")
    ap.add_argument("--mask", default="off", choices=["off", "auto", "birefnet"],
                    help="'file' is deliberately not offered: a cached mask is "
                         "stale on a warped copy")
    ap.add_argument("--birefnet-model", default="auto")
    ap.add_argument("--deeplsd-model", default="",
                    help="DeepLSD weights; empty takes models/deeplsd_md.tar")
    ap.add_argument("--edge", type=int, default=1600)
    ap.add_argument("--per-image", action="store_true",
                    help="also print each photograph, not only the summary")
    args = ap.parse_args(argv)

    files = collect(args.folder)
    if not files:
        print("no readable images in " + args.folder)
        return 1

    base = Settings().replace(focal_35mm=args.focal, detect_max_edge=args.edge,
                              mask_mode=args.mask,
                              deeplsd_model=args.deeplsd_model)
    if args.mask == "birefnet":
        from bpc import birefnet as BN
        w = args.birefnet_model
        if w == "auto":
            w = BN.find_weights()
        if not w:
            print("--mask birefnet found no weights")
            return 2
        base = base.replace(birefnet_model=w)
        print("# mask: " + BN.describe(w))

    wanted = [d.strip() for d in args.detectors.split(",") if d.strip()]
    bad = [d for d in wanted if d not in ALL_DETECTORS]
    if bad:
        print("unknown detector(s): " + ", ".join(bad))
        return 2

    print("# {} photograph(s) x {} rotations, f fixed at {:.0f}mm, mask={}".format(
        len(files), len(DELTAS), args.focal, args.mask))
    rows = []
    for det in wanted:
        st = base.replace(detector=det)
        errs, per_image, failed, t0 = [], [], 0, time.time()
        for f in files:
            try:
                e = round_trip_errors(f, st, args.focal, args.edge)
            except Exception as exc:
                print("  {}: {} failed ({})".format(det, os.path.basename(f), exc))
                e = []
            if not e:
                failed += 1
                continue
            errs += e
            per_image.append((os.path.basename(f), float(np.mean(e))))
        dt = time.time() - t0
        if not errs:
            print("{:12s}  no measurements (detector unavailable?)".format(det))
            continue
        rows.append((det, float(np.mean(errs)), float(np.percentile(errs, 90)),
                     float(np.max(errs)), failed, dt, per_image))

    if not rows:
        return 3
    print()
    print("{:12s}{:>9s}{:>9s}{:>9s}{:>8s}{:>9s}".format(
        "detector", "mean", "p90", "worst", "no fit", "seconds"))
    for det, mean, p90, worst, failed, dt, _ in sorted(rows, key=lambda r: r[1]):
        print("{:12s}{:9.2f}{:9.2f}{:9.2f}{:8d}{:9.1f}".format(
            det, mean, p90, worst, failed, dt))
    if args.per_image:
        for det, _, _, _, _, _, per_image in rows:
            print("\n# " + det)
            for name, m in sorted(per_image, key=lambda x: -x[1]):
                print("  {:44s}{:7.2f}".format(name[:43], m))
    print("\n# degrees between the recovered up and the one the known rotation "
          "implies.\n# Read p90 and worst: this project's metric is how many "
          "photographs it ruins.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
