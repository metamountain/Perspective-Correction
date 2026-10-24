"""Ground-truth benchmark for the focal length the estimator picks WITHOUT EXIF.

User, 2026-10-24: "das Brennweitenproblem ... das muss weg". Every scene is a
street of building-sized Rubik cubes (`tests/synth.rubik_street`) rendered at
a known focal, pitch and facade yaw, then handed to a `ReviewSession` exactly
as a photograph without EXIF would be -- so detection runs through
`io.analysis_gray` at `detect_max_edge`, like `pipeline.analyse`.

Per scene it records the estimator's focal against the truth (log error), its
pitch against the truth, which source decided the focal, and for every face
that lies wholly in the frame the cell aspect (1.000 = squares) after PC
Rectangle on the exact corners, and after horizontal auto with the facade
strip on that face.

    python tools/benchmark_focal.py [N] [--no-ha] [--csv out.csv]

N scenes (default 48), seeded, so before/after runs see the same scenes.
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "src"), os.path.join(ROOT, "tests")]

import synth  # noqa: E402
from pc.config import Settings  # noqa: E402
from pc.review import ReviewSession  # noqa: E402


def scenes(n, seed=20261024):
    """``n`` scenes spread over focal 14-85 mm, pitch 0-25 deg, facade yaw
    0-60 deg, 1-3 cubes. Kind 'frontal' keeps every cube within 8 deg of
    facing the camera; 'corner' turns them 20-60 deg; 'mixed' turns each cube
    on its own."""
    rng = np.random.default_rng(seed)
    out = []
    kinds = ("frontal", "corner", "mixed")
    for i in range(n):
        kind = kinds[i % 3]
        mm = float(np.exp(rng.uniform(math.log(14), math.log(85))))
        pitch = float(rng.uniform(0, 25))
        ncubes = int(rng.integers(1, 4))
        yaw = float(rng.uniform(0, 8) if kind == "frontal" else rng.uniform(20, 60))
        yaw *= 1 if rng.random() < 0.5 else -1
        hfov = math.atan(18.0 / mm)          # half the horizontal field of view
        vfov = math.atan(12.0 / mm)
        # far enough for the cube's top to be inside the frame, near enough to fill it
        top = math.radians(pitch) + 0.85 * vfov
        z_top = 18.4 / math.tan(top) * 1.15
        z_fit = 1.25 * 20.0 / (2 * math.tan(hfov) / max(ncubes, 1))
        z0 = max(z_top, z_fit, 22.0)
        # A tele looking steeply up at a cube far enough to fit would look over
        # it: come closer instead, so the facade fills the frame and is cut off
        # at the bottom -- the real tele-up-a-tower shot.
        if math.atan(18.4 / z0) < math.radians(pitch) + 0.3 * vfov:
            z0 = max(18.4 / math.tan(math.radians(pitch) + 0.5 * vfov), 12.0)
        cubes = []
        for k in range(ncubes):
            bearing = (k - (ncubes - 1) / 2.0) * 1.1 * hfov / max(ncubes, 1) * 2 \
                + rng.uniform(-0.1, 0.1) * hfov
            z = z0 * rng.uniform(1.0, 1.35)
            turn = yaw if kind != "mixed" else float(rng.uniform(-60, 60))
            cubes.append((z * math.tan(bearing), z, turn + rng.uniform(-3, 3)))
        out.append(dict(i=i, kind=kind, mm=round(mm, 2), pitch=round(pitch, 2),
                        yaw=round(yaw, 2), cubes=cubes))
    return out


def run(sc, ha=True, settings=None):
    settings = settings or Settings()
    img, f_true, faces = synth.rubik_street(sc["cubes"], sc["pitch"], sc["mm"])
    h, w = img.shape[:2]
    s = ReviewSession("cube.png", settings, image=img)
    m = s.model
    f_est = s.current_angles()[2]
    row = dict(i=sc["i"], kind=sc["kind"], mm=sc["mm"], pitch=sc["pitch"], yaw=sc["yaw"],
               ncubes=len(sc["cubes"]),
               est_mm=round(f_est / w * 36.0, 2) if f_est else None,
               ferr=round(math.log(f_est / f_true), 4) if f_est else None,
               est_pitch=round(abs(m.pitch_deg), 2) if m and m.pitch is not None else None,
               perr=round(abs(m.pitch_deg) - sc["pitch"], 2) if m and m.pitch is not None else None,
               src=m.f_source if m else None,
               nhv=(m.diagnostics or {}).get("horizontal_vps") if m else None,
               conf=round(m.confidence, 3) if m else None,
               blend=(m.diagnostics or {}).get("focal_blend") if m else None,
               rect=[], ha=[])
    for name, (corners, grid) in faces.items():
        s.planar_quad = [tuple(p) for p in corners]
        try:
            H, _cw, _ch = s.planar_homography()
            row["rect"].append((name, round(synth.cell_aspect(H, grid), 4)))
        except Exception as e:      # a degenerate quad is a result, not a crash
            row["rect"].append((name, None))
        s.planar_quad = []
    if ha:
        for name, (corners, grid) in faces.items():
            t = ReviewSession("cube.png", settings.replace(correct_horizontal=True), image=img)
            t.strip = (corners[:, 0].min() / w, corners[:, 0].max() / w)
            try:
                t.refit()
                t.render_after(max_edge=1200, fill="none", apply_crop=False)
                kind, sc_, Ht, fit = t.preview_map
                M = np.diag([fit, fit, 1.0]) @ Ht @ np.diag([sc_, sc_, 1.0])
                row["ha"].append((name, round(synth.cell_aspect(M, grid), 4)))
            except Exception:
                row["ha"].append((name, None))
    return row


def _aspect_err(pairs):
    return [abs(math.log(a)) for _, a in pairs if a]


def summarise(rows):
    fe = np.array([abs(r["ferr"]) for r in rows if r["ferr"] is not None])
    pe = np.array([abs(r["perr"]) for r in rows if r["perr"] is not None])
    re_ = np.array(sum((_aspect_err(r["rect"]) for r in rows), []))
    he = np.array(sum((_aspect_err(r["ha"]) for r in rows), []))

    def q(a):
        if not len(a):
            return "n/a"
        return f"mean {np.mean(a):.3f} p90 {np.percentile(a, 90):.3f} worst {np.max(a):.3f} (n={len(a)})"
    print("|log f err|      ", q(fe))
    print("|pitch err| deg  ", q(pe))
    print("|log rect aspect|", q(re_))
    print("|log HA aspect|  ", q(he))
    for kind in ("frontal", "corner", "mixed"):
        k = np.array([abs(r["ferr"]) for r in rows if r["kind"] == kind and r["ferr"] is not None])
        print(f"  {kind:8s} |log f err|", q(k))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("n", nargs="?", type=int, default=48)
    ap.add_argument("--no-ha", action="store_true")
    ap.add_argument("--csv", default=None)
    a = ap.parse_args()
    rows = []
    t0 = time.time()
    for sc in scenes(a.n):
        r = run(sc, ha=not a.no_ha)
        rows.append(r)
        rect = " ".join(f"{x:.3f}" for _, x in r["rect"] if x)
        has = " ".join(f"{x:.3f}" for _, x in r["ha"] if x)
        print(f"{r['i']:3d} {r['kind']:7s} {r['mm']:5.1f}mm p{r['pitch']:5.1f} y{r['yaw']:6.1f} "
              f"n{r['ncubes']} -> {r['est_mm']}mm ({r['src']}, nhv {r['nhv']}) "
              f"ferr {r['ferr']:+.3f} perr {r['perr']} | rect {rect} | ha {has}", flush=True)
    print(f"{time.time() - t0:.0f} s")
    summarise(rows)
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as fh:
            wr = csv.DictWriter(fh, fieldnames=list(rows[0]))
            wr.writeheader()
            wr.writerows(rows)


if __name__ == "__main__":
    main()
