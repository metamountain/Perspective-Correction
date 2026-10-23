"""Render Rubik cubes and straighten each face with PC Rectangle, to LOOK at.

User, 2026-10-23: "es muessen nach Entzerrung wieder Quadrate rauskommen.
leicht zu messen" -- and "zeige synthetische Bilder", then "20x20 m, du 1,6
hoch, mehrere Wuerfel, gedreht". Each scene is a street of Rubik cubes the
size of buildings (`tests/synth.rubik_street`: 20 m a side, 5 m squares, eye
height 1.6 m, camera tilted up), at a known focal and without EXIF, so the
session estimates the focal exactly as it does for a real photograph.
For every visible face this writes one picture: the source with the quad on
the left, the rectified face on the right with the true square grid drawn
over it in green, and the measured cell aspect (1.000 = squares) in the
title. `summary.csv` holds the numbers.

    python tools/render_cubes.py            -> tests/assets/Synthetic/cubes/
"""
from __future__ import annotations

import csv
import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "src"), os.path.join(ROOT, "tests")]

import synth  # noqa: E402
from pc.config import Settings  # noqa: E402
from pc.review import ReviewSession  # noqa: E402

OUT = os.path.join(ROOT, "tests", "assets", "Synthetic", "cubes")
# (name, cubes [(x m, z m, turn deg)], pitch up deg, focal 35 mm, camera yaw deg)
SCENES = [
    ("two_corners", [(-14, 40, 25), (22, 55, -20)], 14, 24, 0),
    ("street_of_three", [(-14, 40, 25), (22, 55, -20), (0, 90, 40)], 14, 24, 0),
    ("close_and_steep", [(0, 28, 35)], 22, 16, 0),
    ("row_at_an_angle", [(-30, 60, 0), (-5, 75, 0), (20, 90, 0)], 10, 28, 25),
    ("far_tele", [(0, 120, 30), (35, 140, -10)], 6, 50, 0),
    ("wide_square", [(-25, 35, 45), (25, 35, -45), (0, 70, 10)], 16, 18, 0),
]
SHOW = 900


def _fit(img, edge):
    s = edge / float(max(img.shape[:2]))
    return cv2.resize(img, (int(img.shape[1] * s), int(img.shape[0] * s)),
                      interpolation=cv2.INTER_AREA), s


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for label, cubes, pitch, mm, yaw in SCENES:
        img, f, faces = synth.rubik_street(cubes, pitch, mm, yaw_deg=yaw)
        s = ReviewSession("cube.png", Settings(), image=img)
        est_mm = s.current_angles()[2] / img.shape[1] * 36.0
        stem = f"{label}_pitch{pitch}_{mm}mm"
        cv2.imwrite(os.path.join(OUT, stem + ".png"), img)
        for name, (corners, grid) in faces.items():
            s.planar_quad = [tuple(p) for p in corners]
            H, cw, ch = s.planar_homography()
            aspect = synth.cell_aspect(H, grid)
            out = cv2.warpPerspective(img, H, (cw, ch))
            g = cv2.perspectiveTransform(grid.reshape(-1, 1, 2), H).reshape(grid.shape)
            for row in g:
                cv2.polylines(out, [row.astype(np.int32)], False, (0, 255, 0), 3)
            for col in g.transpose(1, 0, 2):
                cv2.polylines(out, [col.astype(np.int32)], False, (0, 255, 0), 3)
            x0, y0, x1, y1 = (int(round(v)) for v in s._planar_box)
            out = out[max(0, y0):y1 + 1, max(0, x0):x1 + 1]
            left = img.copy()
            cv2.polylines(left, [corners.astype(np.int32)], True, (255, 0, 255), 5)
            a, _ = _fit(left, SHOW)
            b, _ = _fit(out, SHOW)
            hh = max(a.shape[0], b.shape[0]) + 50
            pic = np.full((hh, a.shape[1] + b.shape[1] + 20, 3), 30, np.uint8)
            pic[50:50 + a.shape[0], :a.shape[1]] = a
            pic[50:50 + b.shape[0], a.shape[1] + 20:] = b
            cv2.putText(pic, f"{name}  pitch {pitch}  {mm} mm (estimated "
                        f"{est_mm:.1f} mm)  ->  cell w/h = {aspect:.3f}",
                        (10, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            cv2.imwrite(os.path.join(OUT, f"{stem}_{name}.png"), pic)
            rows.append(dict(scene=stem, face=name, camera_yaw=yaw, pitch=pitch, focal_mm=mm,
                             estimated_mm=round(est_mm, 1), cell_aspect=round(aspect, 3)))
            print(f"{stem:28s} {name:5s} est {est_mm:5.1f} mm  cell w/h {aspect:.3f}")
    with open(os.path.join(OUT, "summary.csv"), "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
