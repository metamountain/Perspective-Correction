"""Render representative synthetic scenes to PNG for visual inspection.

Usage:  python tools/render_synth.py [--outdir DIR]
Default outdir: tests/assets/Synthetic
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))

import cv2
import numpy as np
from synth import Scene


SCENES = [
    # (filename, kwargs)
    ("facade_pitch8_roll-2.png", dict(pitch_deg=8, roll_deg=-2, seed=41)),
    ("facade_pitch0_roll0.png", dict(pitch_deg=0, roll_deg=0, seed=7)),
    ("facade_pitch12_roll3_yaw5.png", dict(pitch_deg=12, roll_deg=3, yaw_deg=5, seed=99)),
    ("half_timbered_pitch8_roll-2.png", dict(pitch_deg=8, roll_deg=-2, half_timbered=True, seed=42)),
    ("half_timbered_corner_pitch6.png", dict(pitch_deg=6, roll_deg=0, half_timbered=True, corner=True, seed=55)),
    ("facade_occluders_pitch10.png", dict(pitch_deg=10, roll_deg=-1, occluders=3, seed=77)),
    ("facade_clutter_heavy_pitch8.png", dict(pitch_deg=8, roll_deg=2, clutter=120, noise=6.0, seed=88)),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default=os.path.join(
        os.path.dirname(__file__), "..", "tests", "assets", "Synthetic"))
    args = ap.parse_args()

    outdir = os.path.normpath(args.outdir)
    os.makedirs(outdir, exist_ok=True)

    for name, kw in SCENES:
        sc = Scene(w=1200, h=800, focal_35mm=28.0, **kw)
        path = os.path.join(outdir, name)
        cv2.imwrite(path, sc.img)
        tr, tp = sc.true_roll_pitch()
        print(f"  {name:45s}  roll={np.degrees(tr):+.2f}°  pitch={np.degrees(tp):+.2f}°")

    print(f"\nSaved {len(SCENES)} scenes to {outdir}")


if __name__ == "__main__":
    main()
